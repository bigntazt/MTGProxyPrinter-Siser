"""Experimental dock setup and publication-bound prepared output workflows."""
from dataclasses import FrozenInstanceError, replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QFileDialog

from mtg_proxy_printer import prepared_page_pdf as pdf, prepared_page_png as png
from mtg_proxy_printer import image_preparation_neural as neural
from mtg_proxy_printer.ui import dialogs
from mtg_proxy_printer.ui.neural_worker_setup import NeuralWorkerSetup
from mtg_proxy_printer.units_and_sizes import CardSizes
from tests.test_prepared_page_pdf import preview, image_sizes
from tests.test_image_preparation_neural import setup
from tests.test_image_source import encoded, write_original
from tests.ui.test_prepared_page_pdf_dialog import choose
from tests.page_scene.test_prepared_scene import ordinary
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard


def configure(h, config):
    h.dock.setup_button.click()
    dialog = h.window._neural_setup_dialog
    assert isinstance(dialog, NeuralWorkerSetup)
    for name, edit in dialog.fields.items():
        edit.setText(str(getattr(config, name)))
    with patch.object(Path, "is_file", side_effect=AssertionError("GUI worker probe")):
        dialog.accept()
    assert h.window._neural_worker_config == config


def neural_ready(h, config, ppi=600):
    if not h.dock.isVisible(): h.open()
    configure(h, config)
    h.dock.target_combo.setCurrentIndex(h.dock.target_combo.findData(ppi))
    h.dock.mode_combo.setCurrentIndex(3)
    return h.ready()


def test_missing_setup_invalidates_ready_and_baselines_still_work(preview):
    h = preview
    h.add(encoded(3, 5)); h.open(); h.dock.mode_combo.setCurrentIndex(1); h.ready()
    assert h.window.ui.action_export_prepared_page_pdf.isEnabled()
    h.dock.mode_combo.setCurrentIndex(3)
    assert h.session.status().phase == "off"
    assert not h.window.ui.action_export_prepared_page_pdf.isEnabled()
    assert not h.window.ui.menu_export_prepared_png.isEnabled()
    assert h.dock.target_combo.isEnabled() and h.dock.mode_combo.isEnabled()
    assert "Worker Python" in h.dock.details_text.toPlainText()
    h.dock.mode_combo.setCurrentIndex(2); assert h.ready().completion.mode == "lanczos"
    h.dock.mode_combo.setCurrentIndex(1); assert h.ready().completion.mode == "original"


def test_setup_pickers_validation_and_cancel_are_session_only(preview, setup, monkeypatch):
    h = preview
    h.open(); h.dock.setup_button.click()
    dialog = h.window._neural_setup_dialog
    dialog.accept(); assert "Choose:" in dialog.error_label.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(setup[0].interpreter), ""))
    dialog._browse("interpreter")
    assert dialog.fields["interpreter"].text() == str(setup[0].interpreter)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(setup[0].private_parent))
    dialog._browse("private_parent")
    assert dialog.fields["private_parent"].text() == str(setup[0].private_parent)
    dialog.reject(); assert h.window._neural_worker_config is None
    configure(h, setup[0]); assert h.dock.mode == "off" and not setup[3]


@pytest.mark.parametrize("ppi", [600, 900, 1200])
def test_neural_dock_targets_metadata_and_export_does_not_infer(preview, setup, ppi):
    h = preview
    h.add(encoded(5, 7))
    completion = neural_ready(h, setup[0], ppi).completion
    assert (completion.mode, completion.target_ppi) == ("neural", ppi)
    with pytest.raises(FrozenInstanceError): completion.mode = "lanczos"
    assert dict(completion.outcome_counts) == {"generated_cached": 2}
    assert "Neural generated and cached: 2" in h.dock.details_text.toPlainText()
    session = setup[3][0]; before = (session.starts, len(session.inputs))
    h.window.ui.action_export_prepared_page_pdf.trigger()
    dialog = h.window.current_dialog; result = dialog._payload["result"]
    assert (result.mode, result.target_ppi) == ("neural", ppi)
    assert (745*(ppi//300), 1040*(ppi//300)) in image_sizes(PdfReader(BytesIO(result.data)).pages[0])
    assert "Neural requested target" in dialog.ui.summary_text.toPlainText()
    assert (session.starts, len(session.inputs)) == before
    dialog.reject()
    h.dock.refresh_button.click(); assert dict(h.ready().completion.outcome_counts) == {"cache_hit": 2}
    assert len(setup[3][-1].inputs) == 0
    assert "Neural cache hits: 2" in h.dock.details_text.toPlainText()


def test_published_options_are_authoritative_and_capture_rejects_mismatch(preview, setup):
    h = preview; h.add(encoded(5, 7)); neural_ready(h, setup[0])
    for capture, kwargs in ((pdf.capture_prepared_page_pdf, {}), (png.capture_prepared_page_png, {"output_ppi": 600})):
        with pytest.raises(ValueError, match="completed preparation"):
            capture(h.document, h.session, mode="lanczos", target_ppi=600, **kwargs)
        with pytest.raises(ValueError, match="completed preparation"):
            capture(h.document, h.session, mode="neural", target_ppi=900, **kwargs)
    # Change visible controls without their option notification: exports still label the pixels' generation.
    h.dock.mode_combo.blockSignals(True); h.dock.mode_combo.setCurrentIndex(2); h.dock.mode_combo.blockSignals(False)
    h.dock.target_combo.blockSignals(True); h.dock.target_combo.setCurrentIndex(2); h.dock.target_combo.blockSignals(False)
    h.window.ui.action_export_prepared_page_pdf.trigger()
    result = h.window.current_dialog._payload["result"]
    assert result.mode == "neural" and result.target_ppi == 600
    h.window.current_dialog.reject()


def test_unavailable_all_fallback_and_same_ppi_configuration_retry(preview, setup, monkeypatch):
    h = preview; h.add(encoded(5, 7))
    original = neural.processor.discover_neural_runtime
    from mtg_proxy_printer.image_processing_pillow import ProcessingFailure
    monkeypatch.setattr(neural.processor, "discover_neural_runtime", lambda: (_ for _ in ()).throw(
        ProcessingFailure("provider_unavailable", "test provider missing")))
    status = neural_ready(h, setup[0])
    assert dict(status.completion.outcome_counts) == {"fallback_original": 2}
    assert "Original fallback: 2" in h.dock.details_text.toPlainText()
    assert "provider is unavailable" in h.dock.details_text.toPlainText()
    assert h.window.ui.action_export_prepared_page_pdf.isEnabled()
    first = status.generation
    monkeypatch.setattr(neural.processor, "discover_neural_runtime", original)
    configure(h, replace(setup[0], private_parent=setup[0].private_parent / "other-session"))
    assert h.session.status().generation > first and not h.window.ui.action_export_prepared_page_pdf.isEnabled()
    assert dict(h.ready().completion.outcome_counts) == {"generated_cached": 2}
    h.dock.hide(); assert h.dock.mode == "off" and h.session.status().phase == "off"


@pytest.mark.parametrize("size", [CardSizes.REGULAR, CardSizes.OVERSIZED])
def test_neural_pdf_png_menu_save_retry_and_immutable_bytes(preview, setup, monkeypatch, size):
    h = preview
    card = ordinary(size)
    h.document.apply(ActionAddCard(card, 1)); write_original(h.image_db.db_path, card, encoded(7, 11))
    neural_ready(h, setup[0], 1200)
    count = len(setup[3][0].inputs)
    # Observe the accounting task but do not run a database mutation in this focused save test.
    accounting = []
    for suffix in ("pdf", "png"):
        if suffix == "pdf": h.window.ui.action_export_prepared_page_pdf.trigger()
        else: h.window.ui.action_export_prepared_png_600.trigger()
        dialog = h.window.current_dialog
        result = dialog._payload["result"]
        assert result.mode == "neural" and result.target_ppi == 1200
        if suffix == "png":
            assert result.output_ppi == 600
            image = __import__('PySide6.QtGui', fromlist=['QImage']).QImage.fromData(result.data)
            assert image.size().toTuple() == (result.pixel_width, result.pixel_height)
            assert image.dotsPerMeterX() == round(600/.0254)
        # Replace the dialog's task connection with an observer; actual save code emits once.
        dialog.request_run_async_task.disconnect()
        dialog.request_run_async_task.connect(accounting.append)
        data = result.data
        path = h.root/f"saved.{suffix}"
        choose(dialog, path)
        with patch.object(dialogs, "_write_prepared_file", side_effect=OSError("injected save failure")):
            dialog._save_selected()
        assert not path.exists() and not dialog._saved
        dialog._filename_finished()
        # A document/source change after capture cannot mutate the held output bytes.
        write_original(h.image_db.db_path, card, encoded(3, 5))
        assert result.data is data
        choose(dialog, path); dialog._save_selected()
        assert path.read_bytes() == data and len(accounting) == (1 if suffix == "pdf" else 2)
    assert len(setup[3][0].inputs) == count


def test_mixed_neural_page_counts_sufficient_fallback_generated_and_standard(preview, setup, monkeypatch):
    from mtg_proxy_printer.neural_worker_client import NeuralWorkerError
    h = preview
    cases = [(encoded(1490, 2080), 1), (encoded(5, 7), 2), (encoded(7, 9), 1), (None, 1)]
    for i, (data, count) in enumerate(cases):
        card = ordinary(name=f"Mixed {i}")
        card.scryfall_id = f"{i+1:08d}-1234-1234-1234-123456789abc"
        h.document.apply(ActionAddCard(card, count))
        if data: write_original(h.image_db.db_path, card, data)
    factory = neural.NeuralWorkerSession
    class RejectOnce(factory):
        rejected = False
        def infer(self, *args):
            if not self.rejected:
                self.rejected = True
                raise NeuralWorkerError("worker_rejected", "test healthy rejection")
            return super().infer(*args)
    monkeypatch.setattr(neural, "NeuralWorkerSession", RejectOnce)
    status = neural_ready(h, setup[0])
    assert dict(status.completion.outcome_counts) == {"original": 1, "fallback_original": 2,
                                                    "generated_cached": 1, "standard": 1}
    text = h.dock.details_text.toPlainText()
    assert "Source-sufficient originals: 1" in text and "Original fallback: 2" in text
    assert "worker rejected this image" in text and "Standard preview: 1" in text


def test_neural_cancel_then_explicit_refresh_and_terminal_error_detail(preview, setup, monkeypatch):
    import threading
    h = preview; h.add(encoded(5, 7)); h.open(); configure(h, setup[0])
    entered, release = threading.Event(), threading.Event(); h.gates.append(release)
    prepare = neural.NeuralImagePreparer.prepare
    def held(self, *args):
        entered.set(); assert release.wait(5)
        return prepare(self, *args)
    monkeypatch.setattr(neural.NeuralImagePreparer, "prepare", held)
    h.dock.mode_combo.setCurrentIndex(3); h.qtbot.waitUntil(entered.is_set)
    h.session._controller.cancel(); release.set()
    h.qtbot.waitUntil(lambda: h.session.status().phase == "cancelled")
    assert "Cancelled" in h.dock.status_label.text() and not h.window.ui.menu_export_prepared_png.isEnabled()
    h.dock.refresh_button.click(); assert h.ready().completion.mode == "neural"
    monkeypatch.setattr(neural.NeuralImagePreparer, "prepare", lambda *args: (_ for _ in ()).throw(ValueError("test terminal defect")))
    h.dock.refresh_button.click(); h.qtbot.waitUntil(lambda: h.session.status().phase == "failed")
    assert "See the private log" in h.dock.details_text.toPlainText()


def test_worker_paths_are_not_carried_into_captured_metadata(preview, setup, monkeypatch):
    from mtg_proxy_printer.image_processing_pillow import ProcessingFailure
    h = preview; h.add(encoded(5, 7))
    config = setup[0]
    detail = f"Cannot open {config.worker_script.parent / 'backend.py'} with {config.interpreter} and {config.checkpoint}"
    monkeypatch.setattr(neural.processor, 'discover_neural_runtime', lambda: (_ for _ in ()).throw(
        ProcessingFailure('provider_unavailable', detail)))
    neural_ready(h, config)
    result = pdf.capture_prepared_page_pdf(h.document, h.session, mode='neural', target_ppi=600)
    text = '\n'.join(d.detail for d in result.diagnostics)
    assert 'provider is unavailable' in text
    assert result.diagnostics and all(d.reason == 'provider_unavailable' for d in result.diagnostics)
    private_paths = (config.interpreter, config.worker_script, config.checkpoint,
                     config.private_parent, config.worker_script.parent)
    assert all(spelling not in text for value in private_paths
               for spelling in (str(value), value.as_posix(), str(value).replace("\\", "\\\\")))


def test_missing_bundle_is_checked_by_real_client_off_gui_and_can_fallback(preview, setup, monkeypatch):
    from mtg_proxy_printer.neural_worker_client import NeuralWorkerSession
    h = preview; h.add(encoded(5, 7))
    monkeypatch.setattr(neural, 'NeuralWorkerSession', NeuralWorkerSession)
    status = neural_ready(h, setup[0])
    assert dict(status.completion.outcome_counts) == {'fallback_original': 2}
    assert 'Neural worker unavailable' in h.dock.details_text.toPlainText()
    assert h.window.ui.action_export_prepared_page_pdf.isEnabled()
    result = pdf.capture_prepared_page_pdf(h.document, h.session, mode='neural', target_ppi=600)
    assert image_sizes(PdfReader(BytesIO(result.data)).pages[0]) == {(5, 7)}
    assert all(str(setup[0].worker_script.parent) not in d.detail for d in result.diagnostics)


@pytest.mark.parametrize("missing_worker", [False, True])
def test_public_fallback_surfaces(preview, setup, monkeypatch, missing_worker, caplog):
    from mtg_proxy_printer.image_processing_pillow import ProcessingFailure
    from mtg_proxy_printer.model.document_page import PageColumns
    from mtg_proxy_printer.neural_worker_client import NeuralWorkerSession

    h = preview
    h.add(encoded(5, 7))
    config = setup[0]
    if missing_worker:
        monkeypatch.setattr(neural, "NeuralWorkerSession", NeuralWorkerSession)
        reason, explanation = "worker_unavailable", "worker"
    else:
        # POSIX-spelled, normalized exception path on any host.
        import posixpath
        entered = "/private/normalized/entered/../worker assets/checkpoint"
        config = replace(config, checkpoint=Path(entered).absolute())
        detail = f"Cannot open '{posixpath.normpath(entered)}'"
        monkeypatch.setattr(neural.processor, "discover_neural_runtime", lambda: (_ for _ in ()).throw(
            ProcessingFailure("provider_unavailable", detail)))
        reason, explanation = "provider_unavailable", "provider"
    status = neural_ready(h, config)
    idx = h.document.index(0, PageColumns.Image, h.document.get_current_page_index())
    raster = h.session.resolve(idx)
    assert raster.reason == reason and raster.actual_dimensions == (5, 7)
    assert status.diagnostics and all(d.reason == reason for d in status.diagnostics)
    surfaces = [raster.detail, *(d.detail for d in status.diagnostics), h.dock.details_text.toPlainText()]
    for action in (h.window.ui.action_export_prepared_page_pdf, h.window._prepared_png_actions[0]):
        assert action.isEnabled()
        action.trigger()
        dialog = h.window.current_dialog
        surfaces.append(dialog.ui.summary_text.toPlainText())
        dialog.reject()
    for surface in surfaces:
        assert explanation in surface.lower()
        assert "/private/normalized" not in surface
        assert config.private_parent.name.lower() not in surface.lower()
    if not missing_worker:
        assert detail in caplog.text
