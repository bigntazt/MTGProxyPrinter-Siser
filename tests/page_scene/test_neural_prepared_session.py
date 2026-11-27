"""Explicit neural selection through the real current-page session/controller."""
from dataclasses import replace
import threading

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent

from mtg_proxy_printer import image_preparation_neural as neural
from mtg_proxy_printer.async_tasks import image_preparation as batch
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.image_preparation import _original
from mtg_proxy_printer.neural_worker_client import NeuralWorkerSession
from mtg_proxy_printer.page_scene import prepared_raster_session as module
from tests.page_scene.test_prepared_raster_session import harness
from tests.page_scene.test_prepared_scene import ordinary
from tests.test_image_preparation_neural import setup
from tests.test_image_source import encoded
from tests.async_tasks.test_neural_preparation_batch import child_config


def enable(h, config, ppi=600):
    h.session.enable(mode="neural", target_ppi=ppi, worker_config=config)


def install_preparer(monkeypatch, hook):
    class Preparer:
        cleanup_report = None
        def __init__(self, config, cache, event):
            self.config, self.event = config, event
        def prepare(self, snapshot, plan):
            return hook(self, snapshot, plan)
        def close(self):
            pass
    monkeypatch.setattr(neural, "NeuralImagePreparer", Preparer)


def test_invalid_neural_options_preserve_active_generation(harness, setup):
    h = harness
    h.write(h.add(), encoded(3, 5))
    h.hold = True
    enable(h, setup[0], 900)
    h.qtbot.waitUntil(lambda: bool(h.tasks))
    generation, active, pending = h.session._generation, h.session._storage.active, h.session._pending
    for mode, ppi, config in [("neural", 600, None), ("neural", 600, object()),
                              ("original", 900, setup[0]), ("neural", True, setup[0]),
                              ("neural", 600.0, setup[0]), ("neural", 300, setup[0])]:
        with pytest.raises((ValueError, TypeError)):
            h.session.enable(mode=mode, target_ppi=ppi, worker_config=config)
        assert h.session._generation == generation and h.session._storage.active is active
        assert h.session._pending is pending and not h.tasks[0]._control.cancelled.is_set()
    request = active.request
    assert request.worker_config is setup[0] and request.cache_root == h.session._cache.root
    assert h.tasks[0].worker_config is request.worker_config


def test_latest_distinct_configuration_wins_without_decoding_stale_results(harness, setup, monkeypatch):
    h = harness
    h.write(h.add(), encoded(3, 5))
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    configs = [replace(setup[0], startup_timeout=timeout) for timeout in (11, 12, 13)]
    seen, decoded = [], []
    def prepare(self, snapshot, plan):
        seen.append(self.config)
        if self.config == configs[0]:
            entered.set()
            assert release.wait(5)
        return _original(snapshot, plan, outcome="fallback_original", reason="test_rejection", detail="bounded")
    install_preparer(monkeypatch, prepare)
    original_decode = module.decode_prepared_pixmap
    monkeypatch.setattr(module, "decode_prepared_pixmap", lambda value: (decoded.append(value), original_decode(value))[1])
    enable(h, configs[0])
    h.qtbot.waitUntil(entered.is_set)
    first_id = h.tasks[0].batch_id
    enable(h, configs[1]); enable(h, configs[2])
    generation = h.session._generation
    assert h.session._pending.worker_config == configs[2] and len(h.tasks) == 1
    # Stale queued metadata must not alter current-generation presentation.
    h.session._on_progress(batch.BatchProgress(first_id, 2, 2, 1, 0))
    assert not h.progress and h.session.status().progress is None
    release.set()
    completion = h.wait()
    assert len(h.tasks) == 2 and [t.worker_config for t in h.tasks] == [configs[0], configs[2]]
    assert seen == [configs[0], configs[2]] and len(decoded) == 1
    assert completion.generation == generation and completion.published_occurrences == 2
    assert len(h.diagnostics) == 1 and h.diagnostics[0].occurrence_ids == (0, 1)
    assert h.diagnostics[0].generation == generation
    print("configuration freshness evidence:", [t.worker_config.startup_timeout for t in h.tasks],
          "decoder calls", len(decoded), "current generation", generation)


@pytest.mark.parametrize("change", ["page", "refresh", "source_notification"])
def test_observed_changes_hide_published_lookup_and_reject_late_decode(harness, setup, monkeypatch, change):
    h = harness
    card = h.add()
    h.write(card, encoded(3, 5))
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    calls, decoded = [], []
    def prepare(self, snapshot, plan):
        calls.append(snapshot.sha256)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return _original(snapshot, plan)
    install_preparer(monkeypatch, prepare)
    original_decode = module.decode_prepared_pixmap
    monkeypatch.setattr(module, "decode_prepared_pixmap", lambda value: (decoded.append(value), original_decode(value))[1])
    enable(h, setup[0]); h.qtbot.waitUntil(entered.is_set)
    if change == "page":
        h.document.apply(ActionNewPage(content=[[card]]))
        h.document.set_currently_edited_page(h.document.pages[-1])
    else:
        h.write(card, encoded(4, 6))
        if change == "refresh":
            h.session.refresh()
        else:
            h.document.dataChanged.emit(h.index(), h.index())
    assert h.session.resolve(h.index()) is None
    release.set()
    completion = h.wait()
    assert completion.generation == h.session._generation and len(h.tasks) == 2 and len(decoded) == 1
    if change != "page":
        assert calls[0] != calls[1]
    assert h.session.resolve(h.index()) is not None
    h.session.refresh()
    assert h.session.resolve(h.index()) is None  # Published lookup invalidates synchronously too.


def test_neural_partial_failure_never_publishes_staged_rasters(harness, setup, monkeypatch):
    h = harness
    first = h.add(count=1)
    second = ordinary(name="Other")
    second.scryfall_id = "22345678-1234-1234-1234-123456789abc"
    h.document.apply(ActionAddCard(second, 1))
    h.write(first, encoded(3, 5)); h.write(second, encoded(4, 6))
    calls = []
    def prepare(self, snapshot, plan):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("independent processing defect")
        return _original(snapshot, plan)
    install_preparer(monkeypatch, prepare)
    enable(h, setup[0])
    completion = h.wait()
    assert completion.summary.status == "failed" and completion.published_occurrences == 0
    assert completion.summary.unique_prepared_results == 1
    assert h.session.resolve(h.index(0)) is h.session.resolve(h.index(1)) is None


def test_neural_mixed_budget_aliases_and_deferred_fallbacks(harness, setup, monkeypatch):
    h = harness
    first = h.add(count=2)
    second = ordinary(name="Other")
    second.scryfall_id = "22345678-1234-1234-1234-123456789abc"
    h.document.apply(ActionAddCard(second, 2))
    data_a, data_b = encoded(3, 5), encoded(4, 4)
    h.write(first, data_a); h.write(second, data_b)
    capture = module.capture_source_request
    payloads = {first.scryfall_id: data_a, second.scryfall_id: data_b}
    def separate_locator(card, **kwargs):
        value = capture(card, **kwargs)
        return replace(value, original_path=None, custom_bytes=bytes(bytearray(payloads[card.scryfall_id])))
    monkeypatch.setattr(module, "capture_source_request", separate_locator)
    h.session._max_raster_bytes = 60
    install_preparer(monkeypatch, lambda self, snapshot, plan: _original(
        snapshot, plan, outcome="fallback_original", reason="worker_rejected", detail="test"))
    decoded, staged_charges = [], []
    real_decode = module.decode_prepared_pixmap
    monkeypatch.setattr(module, "decode_prepared_pixmap", lambda value: (decoded.append(value), real_decode(value))[1])
    original_consume = h.session._controller._consume_result
    def consume(*args):
        original_consume(*args)
        staged_charges.append(h.session._storage.active.raster_bytes)
        assert not h.completions and not h.diagnostics
        assert all(h.session.resolve(h.index(i)) is None for i in range(4))
    h.session._controller._consume_result = consume
    enable(h, setup[0])
    result = h.wait()
    assert result.published_occurrences == 2 and len(decoded) == 1 and staged_charges == [60, 60, 60, 60]
    assert h.session.resolve(h.index(0)) is h.session.resolve(h.index(1)) is not None
    assert h.session.resolve(h.index(2)) is h.session.resolve(h.index(3)) is None
    assert [(d.reason, d.occurrence_ids) for d in h.diagnostics] == [
        ("worker_rejected", (0, 1)), ("preview_memory_limit", (2, 3))]
    assert dict(result.outcome_counts) == {"fallback_original": 2, "standard": 2}
    print("publication/budget evidence: published rows 0,1; standard rows 2,3; charges", staged_charges,
          "decoder calls", len(decoded), "diagnostics", [(d.reason, d.occurrence_ids) for d in h.diagnostics])


def test_session_destruction_while_real_worker_drains(harness, monkeypatch, tmp_path):
    h = harness
    h.write(h.add(), encoded(3, 5))
    config = child_config(tmp_path, {"mode": "job_hang"})
    sessions = []
    class Recording(NeuralWorkerSession):
        def __init__(self, *args):
            super().__init__(*args)
            sessions.append(self)
    monkeypatch.setattr(neural, "NeuralWorkerSession", Recording)
    enable(h, config)
    requests = config.worker_script.parent / "requests.jsonl"
    h.qtbot.waitUntil(lambda: requests.exists() and "infer" in requests.read_text(), timeout=5000)
    shiboken6.delete(h.session)
    h.qtbot.waitUntil(lambda: sessions[0].cleanup_report is not None, timeout=5000)
    assert h.pool.waitForDone(5000)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
    report = sessions[0].cleanup_report
    assert report.reaped and report.threads_stopped and report.temporary_tree_removed and not report.limitations
    assert list(config.private_parent.iterdir()) == [] and not h.completions


def test_stale_teardown_limitation_is_logged_and_pending_neural_start_blocked(harness, setup, monkeypatch, caplog):
    from mtg_proxy_printer.neural_worker_client import NeuralWorkerError, NeuralWorkerCleanup
    h = harness
    h.write(h.add(), encoded(3, 5))
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    class Preparer:
        cleanup_report = None
        def __init__(self, config, cache, event): self.event = event
        def prepare(self, snapshot, plan):
            entered.set(); assert release.wait(5)
            return _original(snapshot, plan)
        def close(self):
            self.cleanup_report = NeuralWorkerCleanup(1, None, False, False, False, ('owned thread still live',))
            error = NeuralWorkerError('cleanup_failed', 'stale worker teardown failed')
            error.cleanup_report = self.cleanup_report
            raise error
    monkeypatch.setattr(neural, 'NeuralImagePreparer', Preparer)
    decoded = []
    monkeypatch.setattr(module, 'decode_prepared_pixmap', lambda value: decoded.append(value))
    enable(h, setup[0]); h.qtbot.waitUntil(entered.is_set)
    enable(h, replace(setup[0], startup_timeout=14))
    release.set()
    h.qtbot.waitUntil(lambda: any(d.reason == 'scheduling_failed' for d in h.diagnostics), timeout=5000)
    assert len(h.tasks) == 1 and not decoded and not h.completions
    assert 'teardown limitation' in caplog.text and 'owned thread still live' in caplog.text
    assert h.session.status().phase == 'failed' and h.session.resolve(h.index()) is None
