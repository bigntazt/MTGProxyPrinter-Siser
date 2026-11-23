"""MainWindow preview ownership: locks, cleanup, shutdown and invalid roots."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QStringListModel
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget, QWizard

from mtg_proxy_printer.ui import main_window
from mtg_proxy_printer.ui.cache_cleanup_wizard import CacheCleanupWizard


@pytest.fixture
def owner_factory(qtbot, document_light, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, 'UI_LOCK_SEMAPHORE', 0)
    windows = []

    def create(root=None):
        document_light.image_db.db_path = root if root is not None else tmp_path / 'CardImages'
        with patch.dict(main_window.Ui_MainWindow.setupUi.__globals__, {'CentralWidget': QWidget}), \
                patch.object(main_window.MainWindow, '_create_about_dialog', return_value=Mock()), \
                patch.object(main_window.MainWindow, '_create_missing_images_manager', return_value=Mock()), \
                patch.object(main_window.MainWindow, '_create_progress_bar_manager', return_value=Mock()), \
                patch.object(main_window.MainWindow, '_setup_central_widget'):
            window = main_window.MainWindow(document_light.image_db, document_light, QStringListModel(['en']))
        qtbot.addWidget(window)
        window.show()
        windows.append(window)
        return window

    yield create
    for window in windows:
        window.is_running = False
        window._preview_resume_timer.stop()
        if window.preview_session is not None:
            window.preview_session.close()
        window.hide()
    main_window.UI_LOCK_SEMAPHORE = 0


def activate(window, mode=2):
    window.ui.action_image_preparation_preview.trigger()
    assert window.image_preparation_preview.isVisible()
    window.image_preparation_preview.mode_combo.setCurrentIndex(mode)


def test_nested_lock_resumes_latest_options_only_at_final_release(owner_factory, qtbot):
    window = owner_factory()
    activate(window)
    session, dock = window.preview_session, window.image_preparation_preview
    with patch.object(session, 'disable', wraps=session.disable) as disable, \
            patch.object(session, 'enable', wraps=session.enable) as enable:
        window.ui_lock_acquire()
        window.ui_lock_acquire()
        assert disable.call_count == 1
        assert not dock.mode_combo.isEnabled()
        assert not window.ui.action_image_preparation_preview.isEnabled()
        dock.target_combo.setCurrentIndex(dock.target_combo.findData(1200))
        window.ui_lock_release()
        assert not window._preview_resume_timer.isActive()
        assert not dock.refresh_button.isEnabled()
        window.ui_lock_release()
        assert window._preview_resume_timer.isActive()
        assert not enable.called
        qtbot.waitUntil(lambda: enable.call_count == 1)
        enable.assert_called_once_with(mode='lanczos', target_ppi=1200)
        assert dock.target_combo.isEnabled() and dock.refresh_button.isEnabled()
        window.ui_lock_release()
        assert not window._preview_resume_timer.isActive()
        assert enable.call_count == 1


@pytest.mark.parametrize('stop', ['off', 'hide', 'close'])
def test_stopping_while_loading_prevents_resume(owner_factory, qtbot, stop):
    window = owner_factory()
    activate(window)
    window.ui_lock_acquire()
    dock = window.image_preparation_preview
    if stop == 'off':
        dock.set_off()
    elif stop == 'hide':
        dock.hide()
    else:
        dock.close()
    with patch.object(window.preview_session, 'enable') as enable:
        window.ui_lock_release()
        qtbot.waitUntil(lambda: not window._preview_resume_timer.isActive())
        enable.assert_not_called()
    assert dock.mode == 'off'
    assert not window.preview_session.status().enabled
    assert not dock.target_combo.isEnabled() and not dock.refresh_button.isEnabled()


def test_cleanup_refresh_is_queued_after_actual_accept_deletion(owner_factory, qtbot):
    window = owner_factory()
    activate(window, 1)
    events = []
    wizard = CacheCleanupWizard.__new__(CacheCleanupWizard)
    QWizard.__init__(wizard, window)
    wizard.image_db = Mock()
    wizard.image_db.delete_disk_cache_entries.side_effect = lambda keys: events.append(('deleted', list(keys)))
    wizard.field = Mock(return_value=[('face-id', True, True, 42)])
    wizard._clear_tooltip_cache = Mock()
    qtbot.addWidget(wizard)
    with patch.object(main_window, 'CacheCleanupWizard', return_value=wizard), \
            patch.object(main_window, 'show_wizard_or_dialog'), \
            patch.object(window.preview_session, 'refresh', side_effect=lambda: events.append(('refresh', None))):
        window.on_action_cleanup_local_image_cache_triggered()
        wizard.accepted.connect(lambda: events.append(('accepted', None)))
        wizard.accept()
        assert [event[0] for event in events] == ['accepted', 'deleted']
        qtbot.waitUntil(lambda: len(events) == 3)
    assert [event[0] for event in events] == ['accepted', 'deleted', 'refresh']
    assert len(events[1][1]) == 1


@pytest.mark.parametrize('entry', ['quit', 'close'])
def test_session_closes_before_application_quit(owner_factory, entry):
    window = owner_factory()
    events = []
    with patch.object(window.preview_session, 'close', side_effect=lambda: events.append('session-close')), \
            patch.object(QApplication.instance(), 'quit', side_effect=lambda: events.append('application-quit')), \
            patch.object(main_window.mtg_proxy_printer.settings, 'write_settings_to_file'):
        if entry == 'quit':
            window.on_action_quit_triggered()
        else:
            event = QCloseEvent()
            window.closeEvent(event)
            assert event.isAccepted()
    assert events[0] == 'session-close' and events[-1] == 'application-quit'
    assert not window.is_running
    assert not window._preview_resume_timer.isActive()


@pytest.mark.parametrize('kind', ['same', 'descendant', 'ancestor', 'invalid'])
def test_invalid_injected_roots_keep_normal_window_available(owner_factory, tmp_path, kind):
    source = tmp_path / 'CardImages'
    if kind == 'invalid':
        # A non-path object must be caught by normal startup, without filesystem work.
        source = object()
    elif kind == 'same':
        source = tmp_path / 'ImageDerivatives'
    resolved_source = tmp_path / 'resolved-original'
    if kind in {'descendant', 'ancestor'}:
        resolved_derivative = (resolved_source / 'nested') if kind == 'descendant' else tmp_path
        # Model a junction/symlink without requiring Windows symlink privileges.
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == source:
                return resolved_source
            if path == source.parent / 'ImageDerivatives':
                return resolved_derivative
            return original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            window = owner_factory(source)
    else:
        window = owner_factory(source)
    assert window.preview_session is None
    assert window.is_running and window.ui.central_widget.isEnabled()
    dock = window.image_preparation_preview
    assert 'unavailable' in dock.status_label.text().lower()
    assert dock.details_text.toPlainText()
    assert not dock.mode_combo.isEnabled()
    assert not (tmp_path / 'ImageDerivatives').exists()
