"""Focused controls, truthful metadata presentation and optional resolver seams."""
from types import SimpleNamespace

import pytest

import mtg_proxy_printer.settings
from mtg_proxy_printer.ui.image_preparation_preview import ImagePreparationPreview
from mtg_proxy_printer.ui.central_widget import CentralWidget
from mtg_proxy_printer.ui.page_renderer import PageRenderer


def test_controls_are_ephemeral_and_mode_specific(qtbot):
    dock = ImagePreparationPreview()
    qtbot.addWidget(dock)
    assert dock.mode == "off"
    assert dock.target_ppi == 600
    assert [dock.target_combo.itemData(i) for i in range(dock.target_combo.count())] == [600, 900, 1200]
    dock.target_combo.setCurrentIndex(dock.target_combo.findData(900))
    assert dock.target_ppi == 900
    dock.target_combo.setCurrentIndex(dock.target_combo.findData(600))
    assert dock.mode_combo.isEnabled()
    assert not dock.target_combo.isEnabled()
    assert not dock.refresh_button.isEnabled()
    dock.mode_combo.setCurrentIndex(1)
    assert dock.mode == "original"
    assert not dock.target_combo.isEnabled()
    assert dock.refresh_button.isEnabled()
    dock.mode_combo.setCurrentIndex(2)
    assert dock.target_combo.isEnabled()
    dock.target_combo.setCurrentIndex(dock.target_combo.findData(1200))
    assert dock.target_ppi == 1200
    dock.set_locked(True)
    assert not any(w.isEnabled() for w in (dock.mode_combo, dock.target_combo, dock.refresh_button))
    dock.set_locked(False)
    assert dock.target_combo.isEnabled()
    dock.set_off()
    assert not dock.target_combo.isEnabled()
    assert not dock.refresh_button.isEnabled()
    assert "regular Print, PDF and PNG" in dock.ui.scope_label.text()
    assert "CheckCards" in dock.ui.supported_cards_label.text()


def test_refresh_emits_explicit_request(qtbot):
    dock = ImagePreparationPreview()
    qtbot.addWidget(dock)
    dock.mode_combo.setCurrentIndex(1)
    with qtbot.waitSignal(dock.refresh_requested):
        dock.refresh_button.click()


def test_fallback_and_uncached_results_are_truthful(qtbot):
    dock = ImagePreparationPreview()
    qtbot.addWidget(dock)
    completion = SimpleNamespace(mode="lanczos", summary=SimpleNamespace(error_detail=""), outcome_counts=(("fallback_original", 2), ("generated_uncached", 1),
                                               ("standard", 1), ("skipped", 3)))
    status = SimpleNamespace(phase="applying", progress=None, completion=completion,
                             diagnostics=(SimpleNamespace(reason="provider_unavailable", detail="Pillow missing"),))
    dock.show_status(status)
    assert dock.status_label.text() == "Updating preview…"
    text = dock.details_text.toPlainText()
    assert "Original fallback: 2" in text
    assert "Lanczos generated, not cached: 1" in text
    assert "Standard preview: 1" in text
    assert "Unsupported/skipped: 3" in text
    assert "Pillow missing" in text
    status.phase = "ready"
    dock.show_status(status)
    assert dock.status_label.text() == "Ready"
    status.phase, status.completion, status.diagnostics = "off", None, ()
    dock.show_status(status)
    assert not dock.details_text.toPlainText()


def test_unavailable_dock_keeps_scope_visible(qtbot):
    dock = ImagePreparationPreview()
    qtbot.addWidget(dock)
    dock.set_unavailable("Original and derivative roots overlap")
    dock.set_locked(False)
    assert not dock.mode_combo.isEnabled()
    assert "unavailable" in dock.status_label.text()
    assert "overlap" in dock.details_text.toPlainText()
    assert dock.ui.scope_label.text()


@pytest.mark.parametrize("layout", ["horizontal", "columnar", "tabbed"])
def test_all_central_layouts_forward_resolver(qtbot, document, image_db, monkeypatch, layout):
    monkeypatch.setitem(mtg_proxy_printer.settings.settings["gui"], "central-widget-layout", layout)
    widget = CentralWidget()
    qtbot.addWidget(widget)
    resolver = lambda index: None
    widget.set_data(document, image_db, raster_resolver=resolver)
    assert widget.ui.page_renderer.scene()._raster_resolver is resolver


def test_default_renderer_retains_standard_path(qtbot, document):
    renderer = PageRenderer()
    qtbot.addWidget(renderer)
    renderer.set_document(document)
    assert renderer.scene()._raster_resolver is None
