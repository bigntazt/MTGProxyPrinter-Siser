"""Architect reproducers: inactive setup ownership and real Windows path failures."""
from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
import threading

import pytest
from pypdf import PdfReader
from PySide6.QtWidgets import QDialogButtonBox

from mtg_proxy_printer.async_tasks import image_preparation as batch
from mtg_proxy_printer import image_preparation_neural as neural
from mtg_proxy_printer.image_preparation import _original
from mtg_proxy_printer.neural_worker_client import NeuralWorkerConfig, NeuralWorkerSession, NeuralWorkerError, REFERENCE_SHA256
from mtg_proxy_printer.page_scene import prepared_raster_session as presentation
from tests.async_tasks.test_neural_preparation_batch import child_config
from tests.test_prepared_page_pdf import preview, image_sizes
from tests.test_image_source import encoded


def config_at(root):
    return NeuralWorkerConfig(root / "python.exe", root / "worker.py", root / "checkpoint", REFERENCE_SHA256,
                              "nomos_compact_2x", "cuda:0", root)


def save_setup(h, config):
    h.dock.setup_button.click()
    dialog = h.window._neural_setup_dialog
    for name, edit in dialog.fields.items():
        edit.setText(str(getattr(config, name)))
    # Exercise the actual setup Save button and its accepted signal chain.
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    assert h.window._neural_worker_config == config


def observations(h):
    return (h.session.status().generation, len(h.tasks),
            h.window.ui.action_export_prepared_page_pdf.isEnabled(),
            h.window.ui.menu_export_prepared_png.isEnabled())


@pytest.mark.parametrize("mode", [1, 2], ids=["original", "lanczos"])
def test_setup_save_preserves_ready_baseline(preview, tmp_path, mode):
    h = preview
    h.add(encoded(5, 7)); h.open(); h.dock.mode_combo.setCurrentIndex(mode); h.ready()
    before = observations(h)
    completion = h.session.status().completion
    save_setup(h, config_at(tmp_path / "unused-neural"))
    assert observations(h) == before
    assert h.session.status().phase == "ready" and h.session.status().completion is completion
    print("Ready baseline setup save:", mode, before, observations(h))


@pytest.mark.parametrize("mode", [1, 2], ids=["original", "lanczos"])
@pytest.mark.parametrize("state", ["running", "cancelled"])
def test_setup_save_preserves_running_or_cancelled_baseline(preview, tmp_path, monkeypatch, mode, state):
    h = preview
    entered, release = threading.Event(), threading.Event(); h.gates.append(release)
    real_prepare = batch.prepare_image
    def held(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return real_prepare(*args, **kwargs)
    monkeypatch.setattr(batch, "prepare_image", held)
    h.add(encoded(5, 7)); h.open(); h.dock.mode_combo.setCurrentIndex(mode)
    h.qtbot.waitUntil(entered.is_set)
    first = h.tasks[0]
    if state == "cancelled":
        h.session._controller.cancel(); release.set()
        h.qtbot.waitUntil(lambda: h.session.status().phase == "cancelled")
    before = observations(h)
    save_setup(h, config_at(tmp_path / "unused-neural"))
    assert observations(h) == before
    if state == "running":
        assert not first._control.cancelled.is_set()
        release.set(); h.ready()
    else:
        assert h.session.status().phase == "cancelled"
        h.qtbot.wait(20)
    assert len(h.tasks) == 1
    print("Baseline setup save:", mode, state, before, observations(h))


def test_setup_save_does_not_touch_off_generation(preview, tmp_path):
    h = preview; h.open()
    before = observations(h)
    save_setup(h, config_at(tmp_path / "unused-neural"))
    assert observations(h) == before and h.session.status().phase == "off"


def test_changed_setup_still_invalidates_neural_and_suppresses_old_result(preview, tmp_path, monkeypatch):
    h = preview; h.add(encoded(5, 7)); h.open()
    first = config_at(tmp_path / "first")
    second = config_at(tmp_path / "second")
    entered, release = threading.Event(), threading.Event(); h.gates.append(release)
    def prepare(self, snapshot, plan):
        if self._config == first:
            entered.set(); assert release.wait(5)
        return _original(snapshot, plan, outcome="fallback_original", reason="worker_unavailable", detail="test")
    monkeypatch.setattr(neural.NeuralImagePreparer, "prepare", prepare)
    decoded = []
    decode = presentation.decode_prepared_pixmap
    monkeypatch.setattr(presentation, "decode_prepared_pixmap", lambda value: (decoded.append(value), decode(value))[1])
    save_setup(h, first); h.dock.mode_combo.setCurrentIndex(3)
    h.qtbot.waitUntil(entered.is_set)
    generation = h.session.status().generation
    save_setup(h, second)
    assert h.session.status().generation > generation and h.tasks[0]._control.cancelled.is_set()
    assert not h.window.ui.action_export_prepared_page_pdf.isEnabled()
    release.set(); h.ready()
    assert len(decoded) == 1 and len(h.tasks) == 2
    assert h.tasks[-1].worker_config == second and h.dock.target_ppi == 600


@pytest.mark.skipif(os.name != "nt", reason="Requires actual Windows path spelling and error semantics")
@pytest.mark.parametrize("spelling", ["case", "dotdot"])
def test_real_private_work_failure_never_reaches_public_reviews(preview, monkeypatch, tmp_path, caplog, spelling):
    h = preview; h.add(encoded(5, 7)); h.open()
    fixture = child_config(tmp_path, {})
    work = tmp_path / "Private Worker Work"
    work.mkdir()
    if spelling == "case":
        entered_path = Path(str(work).swapcase())
    else:
        extra = tmp_path / "existing-parent"; extra.mkdir()
        entered_path = extra / ".." / work.name
    # A previously selected directory became a file. Do not touch any real worker/storage path.
    work.rmdir(); work.write_bytes(b"fixture blocker")
    resolved_path = work.resolve(strict=True)
    config = config_at(tmp_path)
    config = replace(config, interpreter=fixture.interpreter, worker_script=fixture.worker_script,
                     checkpoint=fixture.checkpoint, private_parent=entered_path)
    raw = []
    class Recording(NeuralWorkerSession):
        def start(self):
            try:
                return super().start()
            except NeuralWorkerError as error:
                raw.append(error.detail)
                raise
    monkeypatch.setattr(neural, "NeuralWorkerSession", Recording)
    owner = threading.get_ident()
    resolve = Path.resolve
    def guarded_resolve(path, *args, **kwargs):
        if work.name.casefold() in str(path).casefold():
            assert threading.get_ident() != owner, "GUI thread probed worker work location"
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    save_setup(h, config); h.dock.mode_combo.setCurrentIndex(3)
    status = h.ready()
    assert dict(status.completion.outcome_counts) == {"fallback_original": 2}
    assert len(raw) == 1 and "neural-worker-" in raw[0]
    assert str(resolved_path).casefold() in raw[0].replace("\\\\", "\\").casefold()
    public = [d.detail for d in status.diagnostics] + [h.dock.details_text.toPlainText()]
    assert h.window.ui.action_export_prepared_page_pdf.isEnabled() and h.window.ui.menu_export_prepared_png.isEnabled()
    for suffix in ("pdf", "png"):
        if suffix == "pdf": h.window.ui.action_export_prepared_page_pdf.trigger()
        else: h.window.ui.action_export_prepared_png_600.trigger()
        dialog = h.window.current_dialog; assert dialog is not None
        result = dialog._payload["result"]
        assert dict(result.outcome_counts) == {"fallback_original": 2}
        assert all(d.reason == "worker_unavailable" and 0 < len(d.detail) <= 4096 for d in result.diagnostics)
        public.extend(d.detail for d in result.diagnostics)
        public.append(dialog.ui.summary_text.toPlainText())
        if suffix == "pdf": assert image_sizes(PdfReader(BytesIO(result.data)).pages[0]) == {(5, 7)}
        dialog.reject()
    # Compare after both actual reviews have been opened, so the before-fix evidence exercises both formats.
    text = "\n".join(public).replace("\\\\", "\\").casefold()
    assert str(entered_path).casefold() not in text
    assert str(resolved_path).casefold() not in text
    assert "neural-worker-" not in text
    assert raw[0] in caplog.text  # Full technical details stay in the private operational log.
    print("Private work failure:", spelling, "fallbacks 2; native PDF 5x7; both reviews available; public paths absent")
