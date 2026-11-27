"""Final-shutdown errors through the real M34 child and M36 task/controller."""
import json
from pathlib import Path

import pytest

from mtg_proxy_printer import image_preparation_neural as neural, neural_worker_client as client
from mtg_proxy_printer.image_preparation import _original
from tests.async_tasks.test_image_preparation_batch import harness, request
from tests.async_tasks.test_neural_preparation_batch import child_config, snapshots, start
from tests.page_scene.test_prepared_raster_session import harness as session_harness
from tests.test_image_processing_neural import source
from tests.test_image_processing_pillow import patterned_image
from tests.test_image_source import encoded


def record_sessions(monkeypatch):
    sessions = []
    class Recording(client.NeuralWorkerSession):
        def __init__(self, *args):
            super().__init__(*args)
            sessions.append(self)
    monkeypatch.setattr(neural, "NeuralWorkerSession", Recording)
    return sessions


def healthy(config):
    (config.worker_script.parent / "scenario.json").write_text(json.dumps({"mode": "normal"}))


def test_bad_shutdown_with_verified_cleanup_allows_explicit_next_batch(harness, monkeypatch, tmp_path):
    h = harness
    config = child_config(tmp_path, {"mode": "bad_bye"})
    sessions = record_sessions(monkeypatch)
    snapshots(monkeypatch, {b"a": source(), b"b": source(patterned_image(2, 3))})
    start(h, config, [(0, request(b"a"))])
    first = h.wait()
    report = sessions[0].cleanup_report
    assert first.status == "failed" and "Shutdown acknowledgement differs" in first.error_detail
    assert report.reaped and report.threads_stopped and report.temporary_tree_removed and not report.limitations
    assert len(h.events) == 1 and h.events[0].prepared_image.outcome == "generated_cached"
    assert not first.neural_relaunch_blocked
    assert len(h.tasks) == 1  # Failure never resubmits automatically.
    healthy(config)
    start(h, config, [(1, request(b"b"))])
    second = h.wait(2)
    assert second.status == "completed" and not second.neural_relaunch_blocked
    assert len(h.tasks) == len(sessions) == len(h.events) == 2
    assert sessions[1].cleanup_report.temporary_tree_removed
    assert list(config.private_parent.iterdir()) == []
    assert all(spy["terminal"].count() == spy["task_completed"].count() == 1 for spy in h.spies)
    print("bad-bye evidence:", first, report, "explicit successor:", second)


def deny_owned_removal(monkeypatch, session):
    root = session._root
    assert root.parent == session.config.private_parent and root.name.startswith("neural-worker-")
    original = client.shutil.rmtree
    attempts = []
    def remove(path, *args, **kwargs):
        if Path(path) == root:
            attempts.append(root)
            raise PermissionError("fixture-owned directory removal denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(client.shutil, "rmtree", remove)
    return root, attempts


def assert_cancelled_cleanup_failure(summary, report, log):
    assert summary.status == "failed" and not summary.neural_relaunch_blocked
    assert report.reaped and report.threads_stopped and not report.temporary_tree_removed and report.limitations
    assert len(summary.error_detail) <= 4096
    assert "NeuralWorkerCancelled" in summary.error_detail
    assert "Neural worker operation cancelled" in summary.error_detail
    assert "fixture-owned directory removal denied" in summary.error_detail
    assert "NeuralWorkerCancelled" in log and "fixture-owned directory removal denied" in log


def test_inference_cancel_with_retained_directory_preserves_primary_and_allows_explicit_batch(
        harness, monkeypatch, tmp_path, caplog):
    h = harness
    config = child_config(tmp_path, {"mode": "job_hang"})
    sessions = record_sessions(monkeypatch)
    snapshots(monkeypatch, {b"a": source()})
    start(h, config, [(0, request(b"a"))])
    requests = config.worker_script.parent / "requests.jsonl"
    h.qtbot.waitUntil(lambda: requests.exists() and "infer" in requests.read_text(), timeout=5000)
    with monkeypatch.context() as scoped:
        root, attempts = deny_owned_removal(scoped, sessions[0])
        h.controller.cancel()
        first = h.wait()
        report = sessions[0].cleanup_report
        assert attempts and root.is_dir() and not h.events
        assert_cancelled_cleanup_failure(first, report, caplog.text)
    healthy(config)
    start(h, config, [(1, request(b"a"))])
    assert h.wait(2).status == "completed" and len(h.events) == 1
    assert list(config.private_parent.iterdir()) == [root]  # Retained evidence is not silently deleted.
    print("cancel/removal evidence:", first, report, "removal attempts", len(attempts))


def test_stale_page_cancel_with_retained_directory_logs_original_cause(
        session_harness, monkeypatch, tmp_path, caplog):
    h = session_harness
    h.write(h.add(), encoded(3, 5))
    config = child_config(tmp_path, {"mode": "job_hang"})
    sessions = record_sessions(monkeypatch)
    summaries = []
    h.session._controller.finished.connect(summaries.append)
    h.session.enable(mode="neural", target_ppi=600, worker_config=config)
    requests = config.worker_script.parent / "requests.jsonl"
    h.qtbot.waitUntil(lambda: requests.exists() and "infer" in requests.read_text(), timeout=5000)
    with monkeypatch.context() as scoped:
        root, attempts = deny_owned_removal(scoped, sessions[0])
        h.session.disable()  # Invalidates generation and signals cancellation, without queuing a retry.
        h.qtbot.waitUntil(lambda: bool(summaries), timeout=5000)
        first = summaries[0]
        report = sessions[0].cleanup_report
        assert attempts and root.is_dir()
        assert not h.completions and not h.progress and not h.diagnostics
        assert h.session.status().phase == "off" and h.session.resolve(h.index()) is None
        assert_cancelled_cleanup_failure(first, report, caplog.text)
    healthy(config)
    h.session.enable(mode="neural", target_ppi=600, worker_config=config)
    assert h.wait().summary.status == "completed"
    assert len(h.tasks) == len(sessions) == 2
    print("stale cancel/removal evidence:", first, report, "removal attempts", len(attempts))


def test_final_cleanup_without_report_still_blocks_neural_owner(harness, monkeypatch, tmp_path):
    config = child_config(tmp_path, {})
    snapshots(monkeypatch, {b"a": source()})
    class Preparer:
        cleanup_report = None
        def __init__(self, *args): pass
        def prepare(self, snapshot, plan): return _original(snapshot, plan)
        def close(self): raise RuntimeError("unknown owned cleanup failure")
    monkeypatch.setattr(neural, "NeuralImagePreparer", Preparer)
    start(harness, config, [(0, request(b"a"))])
    result = harness.wait()
    assert result.status == "failed" and result.neural_relaunch_blocked
    with pytest.raises(RuntimeError, match="teardown"):
        start(harness, config, [(1, request(b"a"))])
    assert len(harness.tasks) == 1
