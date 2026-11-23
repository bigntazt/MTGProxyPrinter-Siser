"""Deterministic acknowledgment/cancellation tests with isolated real runners."""
import threading
import gc
import weakref

import pytest
import shiboken6
from PySide6.QtCore import QTimer, Qt

from mtg_proxy_printer.async_tasks import image_preparation as batch
from tests.async_tasks.test_image_preparation_batch import harness, doubles, request, real_request


def wake_events(h, event, *, delivery=None, terminal=False):
    def attach(task):
        if terminal:
            task.terminal.connect(lambda *_: event.set(), Qt.ConnectionType.DirectConnection)
        else:
            task.delivery_ready.connect(lambda _, d: event.set() if delivery is None or d == delivery else None,
                                        Qt.ConnectionType.DirectConnection)
    h.on_submit = attach


def test_bounded_mailbox_and_stale_batch_delivery_and_ack_ids(harness, doubles):
    first, second = threading.Event(), threading.Event()
    def attach(task):
        task.delivery_ready.connect(lambda _, d: (first if d == 1 else second).set(), Qt.ConnectionType.DirectConnection)
    harness.on_submit = attach
    batch_id = harness.start([(1, request()), (2, request(b"second source"))])
    assert first.wait(3)  # Test-only bounded wait, without dispatching owner deliveries yet.
    task = harness.tasks[0]
    control = task._control
    assert len(doubles.loads) == len(doubles.preparations) == 1
    assert control.take(99) is None and not control.acknowledge(99, True)
    harness.controller._on_delivery(batch_id+100, 1)
    harness.controller._on_delivery(batch_id, 99)
    assert not harness.events
    harness.controller._on_delivery(batch_id, 1)  # Explicit first acceptance releases exactly one slot.
    assert second.wait(3)
    assert len(doubles.loads) == 2 and len(harness.events) == 1
    assert not control.acknowledge(1, True)
    harness.controller._on_delivery(batch_id, 1)  # Old wakeup cannot consume pending delivery 2.
    assert len(harness.events) == 1
    with control.condition:
        assert control._pending[0] == 2 and not control._claimed
    assert harness.wait().resolved_occurrences == 2
    assert len(harness.events) == 2
    print("bounded/stale-ID evidence:", len(doubles.loads), len(doubles.preparations), "slot 2 survived stale slot-1 traffic")


def test_previous_accepted_payload_is_not_retained_by_worker(harness, doubles, monkeypatch):
    second_entered, release = threading.Event(), threading.Event()
    harness.blockers.append(release)
    original = doubles.prepare
    references = []
    def prepare(*args, **kwargs):
        if doubles.preparations:
            second_entered.set()
            assert release.wait(3)
        result = original(*args, **kwargs)
        references.append(weakref.ref(result))
        return result
    monkeypatch.setattr(batch, "prepare_image", prepare)
    harness.start([(1, request()), (2, request(b"second unique payload"))])
    harness.qtbot.waitUntil(second_entered.is_set, timeout=3000)
    assert len(references) == 1 and len(harness.events) == 1
    # The consumer keeps an addressable small representation, not the encoded payload.
    harness.representations = {key: image.source_identity for key, image in harness.representations.items()}
    harness.events.clear()
    harness.attempts.clear()
    gc.collect()
    assert references[0]() is None
    release.set()
    assert harness.wait().status == "completed"


def test_owner_loop_responsive_and_worker_execution_off_thread(harness, doubles, monkeypatch):
    entered, release, responsive = threading.Event(), threading.Event(), threading.Event()
    harness.blockers.append(release)
    original = doubles.prepare
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3), "Test preparation gate not released"
        return original(*args, **kwargs)
    monkeypatch.setattr(batch, "prepare_image", blocked)
    harness.start([(1, request())])
    assert entered.wait(3)
    QTimer.singleShot(0, responsive.set)
    harness.qtbot.waitUntil(responsive.is_set, timeout=2000)
    assert not harness.summaries and not harness.events
    release.set()
    assert harness.wait().status == "completed"
    assert doubles.worker_threads and all(t != harness.owner for t in doubles.worker_threads)


def test_aliases_only_follow_successful_acceptance(harness, doubles):
    source = b"equal source content for alias"
    def consume(_, event):
        if isinstance(event, batch.PreparedResult):
            assert len(doubles.loads) == len(doubles.preparations) == 1
            assert not harness.representations
    harness.consumer = consume
    harness.start([(1, request(source)), (2, request(bytes(bytearray(source))))])
    assert harness.wait().unique_prepared_results == 1
    assert [type(event) for event in harness.events] == [batch.PreparedResult, batch.ResultAlias]


def test_cancel_before_execution_busy_until_terminal_then_reusable(harness, doubles):
    harness.hold = True
    harness.start([(1, request())])
    harness.controller.cancel()
    harness.controller.cancel()
    assert harness.controller.is_busy
    with pytest.raises(RuntimeError, match="already active"):
        harness.start([])
    harness.launch_held()
    summary = harness.wait()
    assert summary.status == "cancelled" and summary.resolved_occurrences == 0
    assert not doubles.loads and not doubles.preparations
    harness.hold = False
    harness.start([(2, request())])
    assert harness.wait(2).status == "completed"


def test_cancel_during_preparation_discards_event_after_native_work_returns(harness, doubles, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    harness.blockers.append(release)
    original = doubles.prepare
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(batch, "prepare_image", blocked)
    harness.start([(1, request()), (2, request(b"after cancel"))])
    assert entered.wait(3)
    harness.controller.cancel()
    release.set()
    summary = harness.wait()
    assert summary.status == "cancelled" and summary.resolved_occurrences == 0
    assert not harness.events and len(doubles.loads) == len(doubles.preparations) == 1


def test_cancellation_after_source_load_skips_preparation(harness, doubles, monkeypatch):
    loaded, release = threading.Event(), threading.Event()
    harness.blockers.append(release)
    original = doubles.load
    def blocked(source):
        result = original(source)
        loaded.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(batch, "load_source_snapshot", blocked)
    harness.start([(1, request())])
    assert loaded.wait(3)
    harness.controller.cancel()
    release.set()
    assert harness.wait().status == "cancelled"
    assert len(doubles.loads) == 1 and not doubles.preparations and not harness.events


@pytest.mark.parametrize("direct_task", [False, True])
def test_cancel_wakes_pending_mailbox_without_acceptance(harness, doubles, direct_task):
    published = threading.Event()
    wake_events(harness, published)
    harness.start([(1, request()), (2, request(b"second source"))])
    assert published.wait(3)
    task = harness.tasks[0]
    if direct_task: task.cancel()
    else: harness.controller.cancel()
    assert task._control.cancelled.is_set()
    with task._control.condition:
        assert task._control._pending is None
    summary = harness.wait()
    assert summary.status == "cancelled" and not harness.events
    assert summary.resolved_occurrences == 0 and len(doubles.preparations) == 1


def test_cancel_after_real_cache_publication_suppresses_delivery_but_keeps_entry(harness):
    from mtg_proxy_printer.image_processing_pillow import PillowRuntime
    from unittest.mock import patch
    published = threading.Event()
    wake_events(harness, published)
    source = real_request()
    harness.start([(1, source)], mode="lanczos")
    assert published.wait(5)
    harness.controller.cancel()
    assert harness.wait().status == "cancelled"
    assert not harness.events and list(harness.cache.root.rglob("manifest.json"))
    with patch.object(PillowRuntime, "produce_png", side_effect=AssertionError("Cancelled publication should remain reusable")):
        harness.start([(2, source)], mode="lanczos")
        assert harness.wait(2).status == "completed"
    assert harness.events[-1].prepared_image.outcome == "cache_hit"


def test_controller_destruction_wakes_worker_waiting_for_ack(harness, doubles):
    published = threading.Event()
    wake_events(harness, published)
    harness.start([(1, request())])
    assert published.wait(3)
    control = harness.tasks[0]._control
    shiboken6.delete(harness.controller)
    assert control.cancelled.is_set()
    assert harness.pool.waitForDone(3000)
    assert harness.spies[0]["terminal"].count() == harness.spies[0]["task_completed"].count() == 1
    assert harness.spies[0]["task_deleted"].count() == 1
    assert not harness.events and not harness.summaries


@pytest.mark.parametrize("action", ["raise", "cancel-return", "cancel-raise", "close-return"])
def test_inflight_consumer_and_deferred_terminal_precedence(harness, doubles, action):
    def consume(batch_id, event):
        assert not harness.summaries
        if action.startswith("cancel"):
            harness.controller.cancel()
        elif action == "close-return":
            harness.controller.close()
        # Deterministic reentrant terminal metadata, without a nested event loop/wait.
        if action != "raise":
            harness.controller._on_terminal(batch_id, "cancelled", "")
            assert not harness.summaries and harness.controller.is_busy
        if action.endswith("raise"):
            raise ValueError("Synthetic consumer rejection")
    harness.consumer = consume
    harness.start([(1, request()), (2, request(b"must not follow"))])
    summary = harness.wait()
    rejected = action.endswith("raise")
    assert summary.status == ("failed" if rejected else "cancelled")
    assert summary.resolved_occurrences == (0 if rejected else 1)
    assert summary.unique_prepared_results == (0 if rejected else 1)
    assert len(doubles.preparations) == 1
    assert len(harness.summaries) == 1
    if rejected: assert "Synthetic consumer rejection" in summary.error_detail
    print("in-flight acceptance:", action, summary)
    if action == "close-return":
        harness.controller.close()
        with pytest.raises(RuntimeError, match="closed"):
            harness.start([])


def test_worker_failure_is_failed_even_when_cancelled_before_owner_finalization(harness, doubles, monkeypatch):
    terminal = threading.Event()
    wake_events(harness, terminal, terminal=True)
    def fail(*_, **__): raise RuntimeError("Synthetic unexpected worker failure")
    monkeypatch.setattr(batch, "prepare_image", fail)
    harness.start([(1, request())])
    assert terminal.wait(3)
    harness.controller.cancel()
    summary = harness.wait()
    assert summary.status == "failed" and summary.resolved_occurrences == 0
    assert "Synthetic unexpected worker failure" in summary.error_detail
    assert harness.spies[0]["terminal"].count() == harness.spies[0]["task_completed"].count() == 1
    assert harness.spies[0]["error_occurred"].count() == 0


def test_late_cancellation_precedes_completed_terminal_finalization(harness):
    terminal = threading.Event()
    wake_events(harness, terminal, terminal=True)
    harness.start([])
    assert terminal.wait(3)
    harness.controller.cancel()
    assert harness.wait().status == "cancelled"


def test_finished_handler_can_start_next_batch_and_old_signals_are_stale(harness, doubles):
    def finished(_):
        if len(harness.summaries) == 1:
            harness.start([(2, request(b"new batch"))])
    harness.on_finished = finished
    old = harness.start([(1, request())])
    harness.wait(2)
    harness.controller._on_delivery(old, 1)
    harness.controller._on_terminal(old, "failed", "stale failure")
    assert len(harness.summaries) == 2 and all(s.status == "completed" for s in harness.summaries)


def test_submission_failure_releases_state_without_claiming_task_ran(harness, doubles):
    harness.submission_error = ValueError("Synthetic scheduling denial")
    with pytest.raises(RuntimeError, match="Unable to schedule.*Synthetic scheduling denial"):
        harness.start([(1, request())])
    assert not harness.controller.is_busy and not harness.summaries
    assert harness.tasks[0]._control.cancelled.is_set() and harness.tasks[0]._entries == ()
    assert harness.spies[0]["terminal"].count() == 0
    harness.submission_error = None
    harness.start([(2, request())])
    assert harness.wait().status == "completed"


def test_public_controller_methods_reject_wrong_thread(harness):
    errors = []
    def wrong_thread():
        for call in (harness.controller.cancel, harness.controller.close, lambda: harness.start([]), lambda: harness.controller.is_busy):
            try: call()
            except RuntimeError as error: errors.append(str(error))
    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(3)
    assert not thread.is_alive() and len(errors) == 4
    assert all("owning Qt thread" in error for error in errors)
    harness.controller.close()
    harness.controller.close()
