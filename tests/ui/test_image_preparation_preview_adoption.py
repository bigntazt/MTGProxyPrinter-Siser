"""Real isolated MainWindow controls and existing submission path; preview only."""
from types import SimpleNamespace
from unittest.mock import patch
import builtins
import threading

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QStringListModel, QThreadPool
from PySide6.QtTest import QSignalSpy

from mtg_proxy_printer.application import Application
from mtg_proxy_printer import application, image_processing_pillow, print as printing
from mtg_proxy_printer.async_tasks.base import AsyncTaskRunner
from mtg_proxy_printer.async_tasks import image_preparation as worker
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.image_derivative_cache import CacheWriteFailure, DerivativeCache
from mtg_proxy_printer.model.carddb import CardDatabase
from mtg_proxy_printer.model.imagedb import ImageDatabase
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.model.document import Document
from mtg_proxy_printer.settings import settings
from mtg_proxy_printer.ui import main_window
from mtg_proxy_printer.ui.progress_bar import ProgressBar
from mtg_proxy_printer.units_and_sizes import unit_registry
from tests.page_scene.test_prepared_scene import ordinary
from tests.page_scene.test_card_raster import pixels, render, assert_rect
from tests.test_image_source import encoded, write_original


class PreviewHarness:
    def __init__(self, root, qtbot):
        self.root, self.qtbot = root, qtbot
        self.previous_db = CardDatabase.main_instance
        with patch.dict(settings["printing-filter"], {name: "False" for name in settings["printing-filter"]}):
            self.card_db = CardDatabase(":memory:", check_same_thread=False)
        CardDatabase.main_instance = self.card_db
        self.image_db = ImageDatabase(root / "cache" / "CardImages")
        self.document = Document(self.card_db, self.image_db)
        self.document.page_layout.paper_size = "A4"
        self.document.page_layout.row_spacing = 2*unit_registry.mm
        self.document.page_layout.column_spacing = 3*unit_registry.mm
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.tasks, self.gates = [], []
        self.window = main_window.MainWindow(self.image_db, self.document, QStringListModel(["en"]))
        self.owner = SimpleNamespace(main_window=self.window, card_db=self.card_db, document=self.document)
        self.window.request_run_async_task.connect(self.submit)
        self.document.request_run_async_task.connect(self.submit)
        self.window.resize(1360, 860)
        self.window.show()

    def submit(self, task):
        self.tasks.append(task)
        # Exercise the production submission function/progress manager, redirecting
        # only its global pool lookup to this isolated test's own pool.
        with patch.object(application.QThreadPool, "globalInstance", return_value=self.pool):
            Application.run_async_task(self.owner, task)

    @property
    def session(self):
        return self.window.preview_session

    @property
    def dock(self):
        return self.window.image_preparation_preview

    @property
    def scene(self):
        return self.window.ui.central_widget.ui.page_renderer.scene()

    @property
    def derivative_root(self):
        return self.image_db.db_path.parent / "ImageDerivatives"

    def add(self, source=None, count=2):
        card = ordinary()
        self.document.apply(ActionAddCard(card, count))
        write_original(self.image_db.db_path, card, source if source is not None else encoded(149, 208, color="#bf497180"))
        return card

    def open(self):
        self.window.ui.action_image_preparation_preview.trigger()
        assert self.dock.isVisible()

    def ready(self):
        self.qtbot.waitUntil(lambda: self.session.status().phase == "ready", timeout=30000)
        return self.session.status()

    def cleanup(self):
        self.window.is_running = False
        self.window._preview_resume_timer.stop()
        self.session.close()
        for gate in self.gates:
            gate.set()
        for task in self.tasks:
            task.cancel()
        assert self.pool.waitForDone(10000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
        assert not any(runner.task in self.tasks for runner in AsyncTaskRunner.INSTANCES.values())
        self.window.close()
        shiboken6.delete(self.window)
        shiboken6.delete(self.document)
        shiboken6.delete(self.image_db)
        self.card_db.db.close()
        CardDatabase.main_instance = self.previous_db
        main_window.UI_LOCK_SEMAPHORE = 0


@pytest.fixture
def preview(tmp_path, qtbot, monkeypatch):
    monkeypatch.setattr(main_window, "UI_LOCK_SEMAPHORE", 0)
    h = PreviewHarness(tmp_path, qtbot)
    try:
        yield h
    finally:
        h.cleanup()


def test_normal_mainwindow_off_original_without_pillow_and_no_derivative_io(preview, monkeypatch):
    h = preview
    assert not h.dock.isVisible() and h.dock.mode == "off" and h.dock.target_ppi == 600
    assert h.session.status().phase == "off" and not h.tasks and not h.derivative_root.exists()
    h.add(encoded(73, 109))
    before = render(h.scene)
    original_import = builtins.__import__
    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("Pillow intentionally unavailable")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_pillow)
    with patch.object(image_processing_pillow, "discover_pillow_runtime", side_effect=AssertionError("Original processing")), \
            patch.object(DerivativeCache, "lookup", side_effect=AssertionError("Original cache lookup")), \
            patch.object(DerivativeCache, "store", side_effect=AssertionError("Original cache write")):
        h.open()
        h.dock.mode_combo.setCurrentIndex(1)
        status = h.ready()
        assert dict(status.completion.outcome_counts) == {"original": 2}
        assert not h.dock.target_combo.isEnabled()
        assert h.scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == (73, 109)
        assert not h.derivative_root.exists()
    h.dock.mode_combo.setCurrentIndex(0)
    h.qtbot.waitUntil(lambda: h.scene.card_items[0].card_pixmap_item.pixmap().width() == 745)
    assert render(h.scene) == before


def test_controls_drive_cold_warm_duplicates_both_targets_and_restore(preview, monkeypatch):
    h = preview
    card = h.add()
    before = render(h.scene)
    geometry, model_key, model_pixels = h.scene.geometry, card.image_file.cacheKey(), pixels(card.image_file)
    undo = tuple(h.document.undo_stack)
    changes = QSignalSpy(h.document.dataChanged)
    h.open()
    h.dock.mode_combo.setCurrentIndex(2)
    status = h.ready()
    assert dict(status.completion.outcome_counts) == {"generated_cached": 2}
    assert status.completion.summary.unique_prepared_results == 1
    assert h.scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == (1490, 2080)
    assert h.dock.status_label.text() == "Ready"
    assert "Lanczos generated and cached: 2" in h.dock.details_text.toPlainText()
    with patch.object(image_processing_pillow.PillowRuntime, "produce_png", side_effect=AssertionError("Warm producer")):
        h.dock.refresh_button.click()
        assert dict(h.ready().completion.outcome_counts) == {"cache_hit": 2}
    h.dock.target_combo.setCurrentIndex(1)
    status = h.ready()
    assert dict(status.completion.outcome_counts) == {"generated_cached": 2}
    assert h.scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == (2980, 4160)
    for item in h.scene.card_items:
        assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)
    assert h.scene.geometry == geometry and card.image_file.cacheKey() == model_key
    assert pixels(card.image_file) == model_pixels and tuple(h.document.undo_stack) == undo and changes.count() == 0
    assert main_window.UI_LOCK_SEMAPHORE == 0  # Preparation did not acquire the loading lock.
    h.dock.hide()
    h.qtbot.waitUntil(lambda: h.scene.card_items[0].card_pixmap_item.pixmap().width() == 745)
    assert h.dock.mode == "off" and render(h.scene) == before
    h.open()
    assert h.dock.mode == "off" and h.session.status().phase == "off"


@pytest.mark.parametrize("failure", ["provider", "cache"])
def test_real_fallback_and_uncached_status_are_truthful(preview, monkeypatch, failure):
    h = preview
    h.add(encoded(73, 109))
    if failure == "provider":
        def unavailable():
            raise image_processing_pillow.ProcessingFailure("provider_unavailable", "Pillow is unavailable")
        monkeypatch.setattr(image_processing_pillow, "discover_pillow_runtime", unavailable)
    else:
        monkeypatch.setattr(DerivativeCache, "store", lambda *a: CacheWriteFailure("write_failed", "Synthetic read-only cache"))
    h.open()
    h.dock.mode_combo.setCurrentIndex(2)
    status = h.ready()
    expected = "fallback_original" if failure == "provider" else "generated_uncached"
    assert dict(status.completion.outcome_counts) == {expected: 2}
    text = h.dock.details_text.toPlainText()
    assert ("Original fallback: 2" if failure == "provider" else "Lanczos generated, not cached: 2") in text
    if failure == "provider":
        assert "Lanczos generated" not in text and "Pillow is unavailable" in text
        assert h.scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == (73, 109)


def test_existing_progress_cancel_button_does_not_retry(preview, monkeypatch):
    h = preview
    h.add()
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    loader = worker.load_source_snapshot
    def blocked(request):
        entered.set()
        assert release.wait(10)
        return loader(request)
    monkeypatch.setattr(worker, "load_source_snapshot", blocked)
    h.open()
    h.dock.mode_combo.setCurrentIndex(2)
    h.qtbot.waitUntil(entered.is_set)
    bars = h.window.progress_bar_manager.findChildren(ProgressBar)
    assert len(bars) == 1
    h.qtbot.waitUntil(lambda: bars[0].ui.cancel_button.isVisible())
    bars[0].ui.cancel_button.click()
    release.set()
    h.qtbot.waitUntil(lambda: h.session.status().phase == "cancelled")
    QCoreApplication.processEvents()
    assert len(h.tasks) == 1 and "Cancelled" in h.dock.status_label.text()


def test_export_scenes_remain_standard_while_preview_active(preview, monkeypatch):
    h = preview
    h.add()
    h.open()
    h.dock.mode_combo.setCurrentIndex(1)
    h.ready()
    original = printing.PageScene
    calls = []
    def scene(*args, **kwargs):
        assert "raster_resolver" not in kwargs
        calls.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(printing, "PageScene", scene)
    native = printing.Renderer(h.document)
    pdf = printing.PDFPrinter(h.document, str(h.root / "unused.pdf"), h.tasks[-1].advance_progress)
    # Stop PNG immediately after its existing in-task scene construction; no output matrix.
    class StopAfterScene(RuntimeError):
        pass
    def png_scene(*args, **kwargs):
        scene(*args, **kwargs)
        raise StopAfterScene("Boundary inspection only")
    monkeypatch.setattr(printing, "PageScene", png_scene)
    printing.PNGRenderer(None, h.document, str(h.root / "unused.png")).run()
    assert len(calls) == 3
    assert native.scene._raster_resolver is None and pdf.scene._raster_resolver is None
    shiboken6.delete(native)
    shiboken6.delete(pdf)


@pytest.mark.parametrize("layout", ["horizontal", "columnar", "tabbed"])
def test_real_mainwindow_layout_starts_off_without_pillow_or_derivatives(tmp_path, qtbot, monkeypatch, layout):
    monkeypatch.setitem(settings["gui"], "central-widget-layout", layout)
    monkeypatch.setattr(main_window, "UI_LOCK_SEMAPHORE", 0)
    original_import = builtins.__import__
    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("Pillow absent during startup")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_pillow)
    with patch.object(image_processing_pillow, "discover_pillow_runtime", side_effect=AssertionError("Startup provider discovery")):
        h = PreviewHarness(tmp_path, qtbot)
        try:
            assert h.scene._raster_resolver.__self__ is h.session
            assert h.session._max_raster_bytes == 512*1024*1024
            assert h.session._cache.root == h.derivative_root
            assert h.session.status().phase == "off" and not h.tasks and not h.derivative_root.exists()
            assert h.window.ui.action_image_preparation_preview in h.window.ui.menu_application.actions()
        finally:
            h.cleanup()


def test_settings_preview_and_original_cache_cleanup_keep_separate_paths(preview, monkeypatch):
    from mtg_proxy_printer.ui import page_config_preview_area
    h = preview
    card = h.add()
    h.open()
    h.dock.mode_combo.setCurrentIndex(1)
    h.ready()
    with patch.object(page_config_preview_area, "ImageDatabase", return_value=h.image_db):
        settings_preview = page_config_preview_area.PageConfigPreviewArea()
        assert settings_preview.ui.preview_area.scene()._raster_resolver is None
        shiboken6.delete(settings_preview)
    h.derivative_root.mkdir(exist_ok=True)
    retained = h.derivative_root / "retained-proof.txt"
    retained.write_text("Derivative cache remains separate")
    key = ImageKey(card.scryfall_id, card.is_front, ImageQuality.of_card(card))
    h.image_db.images_on_disk.add(key)
    removed = h.image_db.delete_disk_cache_entries([key])
    assert len(removed) == 1 and retained.read_text() == "Derivative cache remains separate"
    h.dock.refresh_button.click()
    status = h.ready()
    assert dict(status.completion.outcome_counts) == {"standard": 2}
    assert "Original source unavailable" in h.dock.details_text.toPlainText()


@pytest.mark.parametrize("custom_only", [False, True])
def test_empty_and_custom_only_pages_submit_no_preparation(preview, custom_only):
    from mtg_proxy_printer.model.card import CustomCard
    h = preview
    if custom_only:
        front = ordinary()
        custom = CustomCard("Synthetic custom", front.set, "C1", "en", True, "", True,
                            front.size, False, encoded())
        h.document.apply(ActionAddCard(custom, 1))
    h.open()
    h.dock.mode_combo.setCurrentIndex(2)
    h.qtbot.waitUntil(lambda: h.session.status().phase == "no_eligible_sources")
    assert not h.tasks and not h.derivative_root.exists()
    assert "No eligible ordinary card sources" in h.dock.status_label.text()
