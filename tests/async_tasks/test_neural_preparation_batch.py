"""M36 adoption through production task/controller, with bounded public client doubles."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt, QTimer

from mtg_proxy_printer.async_tasks import image_preparation as batch
from mtg_proxy_printer import image_preparation_neural as neural
from mtg_proxy_printer.image_derivative_cache import DerivativeCache, CacheWriteFailure
from mtg_proxy_printer.image_source import SourceUnavailable
from mtg_proxy_printer.neural_worker_client import (
    NeuralWorkerConfig, NeuralWorkerSession, NeuralWorkerError, NeuralWorkerCancelled,
    NeuralWorkerCleanup, REFERENCE_SHA256,
)
from tests.async_tasks.test_image_preparation_batch import harness, request
from tests.test_image_preparation_neural import setup
from tests.test_image_processing_neural import source
from tests.test_image_processing_pillow import patterned_image


def start(h, config, entries, ppi=600):
    return h.controller.start(entries, mode="neural", target_ppi=ppi, cache=h.cache, worker_config=config)


def snapshots(monkeypatch, values):
    monkeypatch.setattr(batch, "load_source_snapshot", lambda req: values[req.custom_bytes])


@pytest.mark.parametrize("ppi", [600, 900, 1200])
def test_neural_batch_reuse_aliases_progress_and_fresh_readiness(harness, setup, monkeypatch, ppi):
    h = harness
    config, _, _, sessions = setup
    a, b = source(), source(patterned_image(2, 3))
    sufficient = source(patterned_image(16, 24))
    snapshots(monkeypatch, {b"a": a, b"equal": a, b"b": b, b"large": sufficient})
    owner = threading.get_ident()
    real = neural.NeuralImagePreparer
    ownership = []
    class Owned(real):
        def __init__(self, config, cache, event):
            ownership.append(("create", threading.get_ident(), event))
            assert event is h.tasks[-1]._control.cancelled
            super().__init__(config, cache, event)
        def close(self):
            ownership.append(("close", threading.get_ident(), self._event))
            super().close()
    monkeypatch.setattr(neural, "NeuralImagePreparer", Owned)
    entries = [(0, request(b"a")), (1, request(b"a")), (2, request(b"equal")),
               (3, request(b"b")), (4, request(b"large")), (5, SourceUnavailable("missing_file", "synthetic"))]
    start(h, config, entries, ppi)
    summary = h.wait()
    assert summary.status == "completed" and summary.resolved_occurrences == 6
    assert summary.unique_prepared_results == 3 and summary.unavailable_occurrences == 1
    assert [type(e) for e in h.events] == [batch.PreparedResult, batch.ResultAlias, batch.PreparedResult,
                                         batch.PreparedResult, batch.UnavailableResult]
    assert h.events[0].occurrence_ids == (0, 1) and h.events[1].occurrence_ids == (2,)
    assert [p.resolved_occurrences for p in h.progresses] == [2, 3, 4, 5, 6]
    original = h.events[3].prepared_image
    assert original.encoded_bytes is sufficient.encoded_bytes and original.source_identity == sufficient.identity
    assert original.reason == "source_sufficient" and original.outcome == "original"
    assert h.events[0].prepared_image.actual_dimensions == (4 * (ppi // 300), 6 * (ppi // 300))
    assert len(sessions) == 1 and len(sessions[0].inputs) == 2 and sessions[0].starts == 2
    assert sessions[0].cleanup_report.reaped
    assert ownership[0][1] != owner and ownership[0][1] == ownership[-1][1]
    start(h, config, [(10, request(b"a"))], ppi)
    assert h.wait(2).status == "completed"
    assert h.events[-1].prepared_image.outcome == "cache_hit"
    assert len(sessions) == 2 and sessions[1].starts == 1 and not sessions[1].inputs
    assert all(spy["terminal"].count() == spy["task_completed"].count() == 1 for spy in h.spies)
    print("neural selection evidence:", ppi, summary, "starts/inferences",
          [(s.starts, len(s.inputs)) for s in sessions], "original bytes identical", original.encoded_bytes is sufficient.encoded_bytes)


@pytest.mark.parametrize("entries", [[], [(0, SourceUnavailable("missing_file", "absent"))], [(0, request(b"large"))]])
def test_neural_batch_without_eligible_work_launches_nothing(harness, setup, monkeypatch, entries):
    snapshots(monkeypatch, {b"large": source(patterned_image(8, 12))})
    with patch.object(neural.processor, "discover_neural_runtime", side_effect=AssertionError("No runtime needed")):
        start(harness, setup[0], entries)
        assert harness.wait().status == "completed"
    assert not setup[3]


@pytest.mark.parametrize("mode,ppi,config", [("neural", 600, None), ("neural", 600, object()),
    ("original", 600, "config"), ("lanczos", 900, "config"), ("other", 600, None),
    ("neural", True, None), ("neural", 600.0, None), ("neural", 601, None)])
def test_invalid_selection_never_submits(harness, mode, ppi, config):
    with pytest.raises((TypeError, ValueError)):
        harness.controller.start([], mode=mode, target_ppi=ppi, cache=harness.cache, worker_config=config)
    assert not harness.tasks and not harness.controller.is_busy


@pytest.mark.parametrize("reason,disposed", [("worker_rejected", False), ("worker_rejected", True),
    ("timeout", True), ("request_budget", True)])
def test_batch_provider_failures_do_not_replace_worker(harness, setup, monkeypatch, reason, disposed):
    h = harness
    snapshots(monkeypatch, {b"a": source(), b"b": source(patterned_image(2, 3)), b"c": source(patterned_image(4, 3)),
                           b"large": source(patterned_image(8, 12))})
    # Warm first image creates the worker; inject its public next-image behavior after acceptance.
    def consume(_id, event):
        if isinstance(event, batch.PreparedResult) and event.occurrence_ids == (0,):
            error = NeuralWorkerError(reason, "injected rejection")
            if disposed:
                error.cleanup_report = NeuralWorkerCleanup(1, 0, True, True, True)
            setup[3][0].infer_error = error
        elif isinstance(event, batch.PreparedResult) and event.occurrence_ids == (1,) and not disposed:
            setup[3][0].infer_error = None
    h.consumer = consume
    start(h, setup[0], [(0, request(b"a")), (1, request(b"b")),
                        (2, request(b"c")), (3, request(b"large"))])
    # Make the later insufficient image a distinct content/identity, avoiding batch aliasing.
    assert h.wait().status == "completed"
    results = [e.prepared_image for e in h.events if isinstance(e, batch.PreparedResult)]
    assert results[1].outcome == "fallback_original" and results[1].reason == reason
    assert results[-1].reason == "source_sufficient" and len(setup[3]) == 1
    assert setup[3][0].starts == (2 if disposed else 3)
    assert results[2].outcome == ("fallback_original" if disposed else "generated_cached")
    if disposed:
        assert results[2].reason == reason


def test_uncached_unavailable_and_programming_consumer_failures(harness, setup, monkeypatch):
    h = harness
    snapshots(monkeypatch, {b"a": source()})
    monkeypatch.setattr(DerivativeCache, "store", lambda *args: CacheWriteFailure("write_failed", "disk full"))
    start(h, setup[0], [(0, request(b"a")), (1, SourceUnavailable("missing_file", "gone"))])
    assert h.wait().status == "completed"
    assert h.events[0].prepared_image.outcome == "generated_uncached"
    assert h.events[0].prepared_image.reason == "cache_write_failed"
    h.consumer = lambda *args: (_ for _ in ()).throw(ValueError("consumer defect"))
    start(h, setup[0], [(0, request(b"a"))])
    assert h.wait(2).status == "failed" and "consumer defect" in h.summaries[-1].error_detail
    h.consumer = None
    monkeypatch.setattr(neural.NeuralImagePreparer, "prepare", lambda *args: (_ for _ in ()).throw(ValueError("programming defect")))
    start(h, setup[0], [(0, request(b"a"))])
    assert h.wait(3).status == "failed" and "programming defect" in h.summaries[-1].error_detail


@pytest.mark.parametrize("consumer_failure", [False, True])
def test_cleanup_failure_logs_bounds_and_blocks_owner_relaunch(harness, setup, monkeypatch, caplog, consumer_failure):
    h = harness
    snapshots(monkeypatch, {b"a": source()})
    def consume(*args):
        error = NeuralWorkerError("cleanup_failed", "cleanup limitation" * 600)
        error.cleanup_report = NeuralWorkerCleanup(1, None, False, False, False, ("still live",))
        setup[3][0].close_error = error
        if consumer_failure:
            raise ValueError("independent consumer failure")
    h.consumer = consume
    start(h, setup[0], [(0, request(b"a"))])
    result = h.wait()
    assert result.status == "failed" and result.neural_relaunch_blocked
    assert len(result.error_detail) <= 4096 and "cleanup" in result.error_detail
    if consumer_failure:
        assert "independent consumer failure" in result.error_detail
    assert "teardown limitation" in caplog.text
    with pytest.raises(RuntimeError, match="teardown"):
        start(h, setup[0], [(0, request(b"a"))])
    assert len(h.tasks) == 1


def test_cancellation_before_execution_and_during_store(harness, setup, monkeypatch):
    h = harness
    snapshots(monkeypatch, {b"a": source()})
    h.hold = True
    start(h, setup[0], [(0, request(b"a"))])
    h.controller.cancel()
    h.launch_held()
    assert h.wait().status == "cancelled" and not setup[3] and not h.events
    h.hold = False
    original = DerivativeCache.store
    def store(cache, req, data):
        result = original(cache, req, data)
        h.tasks[-1]._control.cancelled.set()
        assert cache.lookup(req) == result
        return result
    monkeypatch.setattr(DerivativeCache, "store", store)
    start(h, setup[0], [(0, request(b"a"))])
    assert h.wait(2).status == "cancelled" and not h.events
    assert setup[3][0].cleanup_report.reaped


def test_final_close_cancellation_is_cancelled_after_cleanup_before_terminal(harness, setup, monkeypatch):
    h = harness
    snapshots(monkeypatch, {b"a": source()})
    closed = []
    def consume(*args):
        def close():
            setup[3][0].cleanup_report = NeuralWorkerCleanup(1, 0, True, True, True)
            closed.append("closed")
            raise NeuralWorkerCancelled()
        setup[3][0].close = close
    h.consumer = consume
    h.on_submit = lambda task: task.terminal.connect(lambda *args: closed.append("terminal"), Qt.ConnectionType.DirectConnection)
    start(h, setup[0], [(0, request(b"a"))])
    assert h.wait().status == "cancelled" and closed == ["closed", "terminal"]
    assert h.spies[0]["terminal"].count() == h.spies[0]["task_completed"].count() == 1


def child_config(tmp_path, scenario):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    fixture = Path(__file__).parents[1] / "neural_worker_client/worker_fixture.py"
    (bundle / "worker.py").write_bytes(fixture.read_bytes())
    for name in ("backend.py", "protocol.py", "reference-model.json"):
        (bundle / name).write_bytes(b"test-only bundle")
    (bundle / "scenario.json").write_text(json.dumps(scenario))
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"inert test checkpoint")
    parent = tmp_path / "private"
    parent.mkdir()
    return NeuralWorkerConfig(Path(sys.executable), bundle / "worker.py", checkpoint, REFERENCE_SHA256,
        "nomos_compact_2x", "cuda:0", parent, startup_timeout=5, inference_timeout=5,
        shutdown_timeout=2, terminate_timeout=.2, kill_timeout=2)


@pytest.mark.parametrize("phase", ["startup_silent", "job_hang", "ack", "shutdown_hang"])
def test_real_client_cancellation_keeps_gui_responsive_and_reaps(harness, monkeypatch, tmp_path, phase):
    h = harness
    config = child_config(tmp_path, {"mode": phase if phase != "ack" else "normal"})
    snapshots(monkeypatch, {b"a": source()})
    sessions, ordering = [], []
    class Recording(NeuralWorkerSession):
        def __init__(self, *args):
            super().__init__(*args)
            sessions.append(self)
        def close(self):
            try:
                return super().close()
            finally:
                ordering.append("close")
    monkeypatch.setattr(neural, "NeuralWorkerSession", Recording)
    delivery = threading.Event()
    def attach(task):
        task.terminal.connect(lambda *args: ordering.append("terminal"), Qt.ConnectionType.DirectConnection)
        task.delivery_ready.connect(lambda *args: delivery.set(), Qt.ConnectionType.DirectConnection)
    h.on_submit = attach
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(1)
    start(h, config, [(0, request(b"a"))])
    try:
        if phase == "ack":
            assert delivery.wait(4)  # Deliberately leave the queued owner notification unprocessed.
            h.controller.cancel()
        else:
            h.qtbot.waitUntil(lambda: bool(sessions) and sessions[0]._process is not None, timeout=4000)
        if phase in ("job_hang", "shutdown_hang"):
            requests = config.worker_script.parent / "requests.jsonl"
            wanted = "infer" if phase == "job_hang" else "shutdown"
            h.qtbot.waitUntil(lambda: requests.exists() and wanted in requests.read_text(), timeout=4000)
        if phase != "ack":
            h.controller.cancel()
        summary = h.wait()
        assert summary.status == "cancelled" and ticks
        assert ordering[-1] == "terminal" and "close" in ordering[:-1]
        report = sessions[0].cleanup_report
        assert report.reaped and report.threads_stopped and report.temporary_tree_removed and not report.limitations
        assert list(config.private_parent.iterdir()) == []
        assert sessions[0]._process.poll() is not None
        assert all(not thread.is_alive() for thread in sessions[0]._readers)
        assert sessions[0]._writer is None or not sessions[0]._writer.is_alive()
        print("test-child cancellation evidence:", phase, "GUI ticks", len(ticks), report, "ordering", ordering)
        if phase != "shutdown_hang":
            assert not h.events
    finally:
        timer.stop()


def test_final_cleanup_report_supersedes_cancel_report_after_retry(harness, setup, monkeypatch):
    h = harness
    snapshots(monkeypatch, {b'a': source()})
    class Preparer:
        cleanup_report = None
        def __init__(self, *args): pass
        def prepare(self, *args):
            error = NeuralWorkerCancelled()
            error.cleanup_report = NeuralWorkerCleanup(1, None, False, False, False, ('prior incomplete',))
            self.cleanup_report = error.cleanup_report
            raise error
        def close(self):
            self.cleanup_report = NeuralWorkerCleanup(1, 0, True, True, True)
    monkeypatch.setattr(neural, 'NeuralImagePreparer', Preparer)
    start(h, setup[0], [(0, request(b'a'))])
    summary = h.wait()
    assert summary.status == 'cancelled' and not summary.neural_relaunch_blocked and not h.events


@pytest.mark.parametrize('cancel_on_close', [False, True])
def test_processing_failure_remains_primary_when_final_close_cancels(harness, setup, monkeypatch, cancel_on_close):
    h = harness
    snapshots(monkeypatch, {b'a': source()})
    class Preparer:
        cleanup_report = None
        def __init__(self, *args): pass
        def prepare(self, *args): raise ValueError('primary processing failure')
        def close(self):
            self.cleanup_report = NeuralWorkerCleanup(1, 0, True, True, True)
            if cancel_on_close: raise NeuralWorkerCancelled()
    monkeypatch.setattr(neural, 'NeuralImagePreparer', Preparer)
    start(h, setup[0], [(0, request(b'a'))])
    summary = h.wait()
    assert summary.status == 'failed' and 'primary processing failure' in summary.error_detail
    assert not summary.neural_relaunch_blocked and not h.events
