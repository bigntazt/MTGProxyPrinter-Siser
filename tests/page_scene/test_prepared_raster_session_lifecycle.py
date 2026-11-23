"""Controlled owner-thread session lifecycle; no workers or external storage."""
import gc
import weakref
from PySide6.QtCore import QObject, Signal, QCoreApplication, QPersistentModelIndex
import pytest
import shiboken6

from mtg_proxy_printer.async_tasks.image_preparation import (
    BatchSummary, BatchProgress, PreparedResult, ResultAlias, UnavailableResult,
)
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.replace_card import ActionReplaceCard
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.image_source import SourceIdentity, SourceUnavailable
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.page_scene import prepared_raster_session as module
from tests.page_scene.test_prepared_scene import ordinary
from tests.page_scene.test_prepared_pixmap import prepared


class ControlledController(QObject):
    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, submit, consume, parent):
        super().__init__(parent)
        self.consume = consume
        self.is_busy = False
        self.starts = []
        self.cancelled = False
        self.events = []

    def start(self, entries, **options):
        assert not self.is_busy
        self.is_busy = True
        self.cancelled = False
        self.events = []
        self.starts.append((entries, options))
        return len(self.starts)

    def cancel(self):
        if self.is_busy:
            self.cancelled = True

    def close(self):
        self.cancel()

    def deliver(self, event):
        assert self.is_busy
        self.consume(len(self.starts), event)
        self.events.append(event)

    def finish(self, status="completed"):
        assert self.is_busy
        count = len(self.starts[-1][0])
        resolved = sum(len(e.occurrence_ids) for e in self.events)
        unique = sum(isinstance(e, PreparedResult) for e in self.events)
        unavailable = sum(len(e.occurrence_ids) for e in self.events if isinstance(e, UnavailableResult))
        summary = BatchSummary(len(self.starts), status, count, resolved, unique, unavailable)
        self.is_busy = False
        self.finished.emit(summary)
        return summary


def settle():
    # Zero-duration owned timers run without sleeps or worker timing assumptions.
    for _ in range(4):
        QCoreApplication.processEvents()


@pytest.fixture
def harness(qtbot, document_light, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ImagePreparationController", ControlledController)
    card = ordinary()
    document_light.apply(ActionAddCard(card, 3))
    session = module.PreparedPageSession(document_light, lambda task: None,
                                         tmp_path / "originals", DerivativeCache(tmp_path / "derived"))
    controller = session._controller
    page = document_light.get_current_page_index()
    indices = [document_light.index(i, PageColumns.Image, page) for i in range(3)]
    yield document_light, session, controller, indices
    if shiboken6.isValid(session):
        session.close()
    if shiboken6.isValid(controller) and controller.is_busy:
        controller.finish("cancelled")
    settle()


def begin(session):
    session.enable(mode="original", target_ppi=600)
    settle()


def result(controller, data=None):
    source = controller.starts[-1][0][0].source
    return prepared(data=data, source_identity=SourceIdentity(source.image_key, "a" * 64))


@pytest.mark.parametrize("broken", [False, True])
def test_aliases_stage_once_and_publish_atomically(harness, monkeypatch, broken):
    document, session, controller, indices = harness
    notifications, completions, diagnostics, decoded = [], [], [], []
    session.rasters_changed.connect(lambda: notifications.append(tuple(session.resolve(i) for i in indices)))
    session.completed.connect(completions.append)
    session.diagnostic.connect(diagnostics.append)
    original = module.decode_prepared_pixmap
    def decode(value):
        decoded.append(value)
        return original(value)
    monkeypatch.setattr(module, "decode_prepared_pixmap", decode)
    assert not controller.starts
    begin(session)
    notifications.clear()
    controller.deliver(PreparedResult(7, (0,), result(controller, b"invalid" if broken else None)))
    controller.deliver(ResultAlias(7, (1,)))
    controller.deliver(UnavailableResult((2,), SourceUnavailable("missing_source", "synthetic")))
    assert len(decoded) == 1
    assert all(session.resolve(i) is None for i in indices)
    assert notifications == completions == diagnostics == []
    summary = controller.finish()
    assert all(session.resolve(i) is None for i in indices)
    settle()
    assert len(completions) == 1 and completions[0].summary is summary
    assert completions[0].published_occurrences == (0 if broken else 2)
    assert session.resolve(indices[2]) is None
    if not broken:
        assert session.resolve(indices[0]) is session.resolve(indices[1]) is not None
    else:
        assert all(session.resolve(i) is None for i in indices)
    assert len(notifications) == 1
    assert len(diagnostics) == (2 if broken else 1)
    settle()
    assert len(controller.starts) == 1


def test_latest_request_waits_for_cancelled_batch_to_drain(harness):
    document, session, controller, indices = harness
    begin(session)
    session.enable(mode="lanczos", target_ppi=1200)
    session.enable(mode="original", target_ppi=1200)
    settle()
    assert controller.cancelled and controller.is_busy and len(controller.starts) == 1
    controller.finish("cancelled")
    settle()
    assert len(controller.starts) == 2
    assert controller.starts[-1][1]["mode"] == "original"
    assert controller.starts[-1][1]["target_ppi"] == 1200


def test_same_persistent_slot_replacement_rejects_before_decode(harness, monkeypatch):
    document, session, controller, indices = harness
    begin(session)
    obsolete = result(controller)
    persistent = QPersistentModelIndex(indices[0])
    old_card = document.pages[0][0].card
    document.apply(ActionReplaceCard(ordinary(name="Replacement"), 0, 0))
    assert persistent.isValid() and persistent == indices[0]
    assert document.pages[0][0].card is not old_card
    monkeypatch.setattr(module, "decode_prepared_pixmap", lambda value: pytest.fail("obsolete decode"))
    controller.deliver(PreparedResult(1, (0,), obsolete))
    assert controller.cancelled
    assert session.resolve(persistent) is None


@pytest.mark.parametrize("before,after", [
    ("modelAboutToBeReset", "modelReset"),
    ("layoutAboutToBeChanged", "layoutChanged"),
])
def test_structural_boundaries_block_capture_and_public_refresh(harness, monkeypatch, before, after):
    document, session, controller, indices = harness
    captures, refreshes = [], []
    original = module.capture_source_request
    def capture(*args, **kwargs):
        captures.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "capture_source_request", capture)
    session.rasters_changed.connect(lambda: refreshes.append(1))
    session.enable(mode="original", target_ppi=600)
    getattr(document, before).emit()
    session.refresh()
    settle()
    assert captures == refreshes == controller.starts == []
    getattr(document, after).emit()
    settle()
    assert len(controller.starts) == 1 and len(captures) == 3 and len(refreshes) == 1


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_terminal_without_new_request_does_not_retry(harness, status):
    document, session, controller, indices = harness
    begin(session)
    completed = []
    session.completed.connect(completed.append)
    controller.finish(status)
    settle()
    assert len(controller.starts) == 1 and len(completed) == 1
    assert completed[0].published_occurrences == 0
    assert all(session.resolve(i) is None for i in indices)


@pytest.mark.parametrize("action", ["disable", "refresh", "close"])
def test_reentrant_refresh_notification_prevents_obsolete_start(harness, action):
    document, session, controller, indices = harness
    calls = []
    def change_once():
        calls.append(1)
        if len(calls) == 1:
            getattr(session, action)()
    session.rasters_changed.connect(change_once)
    begin(session)
    assert len(controller.starts) == (1 if action == "refresh" else 0)
    assert all(session.resolve(i) is None for i in indices)


@pytest.mark.parametrize("action", ["disable", "refresh", "close"])
def test_reentrant_completed_handler_invalidates_publication(harness, action):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    session.completed.connect(lambda completion: getattr(session, action)())
    controller.finish()
    settle()
    assert all(session.resolve(i) is None for i in indices)
    assert len(controller.starts) == (2 if action == "refresh" else 1)



def test_document_notifications_coalesce_after_settling(harness):
    document, session, controller, indices = harness
    session.enable(mode="original", target_ppi=600)
    document.current_page_changed.emit(QPersistentModelIndex(document.get_current_page_index()))
    document.page_type_changed.emit(document.get_current_page_index())
    document.page_layout_changed.emit(document.page_layout)
    document.dataChanged.emit(indices[0], indices[-1], [])
    assert controller.starts == []
    settle()
    assert len(controller.starts) == 1


@pytest.mark.parametrize("signal", ["rasters_changed", "diagnostic", "completed"])
def test_native_deletion_from_public_notification_stops_reconciliation(harness, signal):
    document, session, controller, indices = harness
    starts = controller.starts
    if signal != "rasters_changed":
        begin(session)
        event = UnavailableResult((0, 1, 2), SourceUnavailable("missing_source", "synthetic"))
        controller.deliver(event)
    getattr(session, signal).connect(lambda *args: shiboken6.delete(session))
    if signal == "rasters_changed":
        session.enable(mode="original", target_ppi=600)
    else:
        controller.finish()
    settle()
    assert not shiboken6.isValid(session)
    assert len(starts) == (0 if signal == "rasters_changed" else 1)


def test_mixed_decode_failure_alias_and_valid_slots_publish_legacy_holes(harness):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0,), result(controller, b"corrupt")))
    controller.deliver(ResultAlias(1, (1,)))
    controller.deliver(PreparedResult(2, (2,), result(controller)))
    controller.finish()
    settle()
    assert session.resolve(indices[0]) is session.resolve(indices[1]) is None
    assert session.resolve(indices[2]) is not None


def test_staging_and_published_values_release_at_cleanup_boundaries(harness):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    staged = weakref.ref(session._storage.active.results[1])
    session.refresh()
    gc.collect()
    assert staged() is not None  # Obsolete active aliases can still refer to their representation.
    assert all(session.resolve(i) is None for i in indices)
    controller.finish("cancelled")
    gc.collect()
    assert staged() is None
    settle()
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    controller.finish()
    settle()
    published = weakref.ref(session.resolve(indices[0]))
    session.disable()
    gc.collect()
    assert published() is None and session._storage.active is None


def test_terminal_during_structural_boundary_cannot_publish_or_start_successor(harness):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    refreshes, completions = [], []
    session.rasters_changed.connect(lambda: refreshes.append(1))
    session.completed.connect(completions.append)
    document.modelAboutToBeReset.emit()
    controller.finish()
    session.enable(mode="lanczos", target_ppi=1200)
    settle()
    assert not refreshes and not completions and len(controller.starts) == 1
    document.modelReset.emit()
    settle()
    assert len(controller.starts) == 2 and len(refreshes) == 1 and not completions


@pytest.mark.parametrize("action", ["disable", "refresh", "close"])
def test_diagnostic_handler_invalidates_before_old_completion_is_announced(harness, action):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(UnavailableResult((0, 1, 2), SourceUnavailable("missing_file", "synthetic")))
    completions = []
    session.completed.connect(completions.append)
    session.diagnostic.connect(lambda diagnostic: getattr(session, action)())
    controller.finish()
    settle()
    assert not completions and all(session.resolve(i) is None for i in indices)
    assert len(controller.starts) == (2 if action == "refresh" else 1)


def test_unknown_alias_is_an_explicit_programming_error(harness):
    document, session, controller, indices = harness
    begin(session)
    with pytest.raises(ValueError, match="unknown preparation result"):
        controller.deliver(ResultAlias(999, (0,)))
    controller.finish("failed")
    settle()
    assert all(session.resolve(i) is None for i in indices)


def test_progress_handler_can_disable_without_late_publication(harness):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    session.progress.connect(lambda progress: session.disable())
    controller.progress.emit(BatchProgress(1, 3, 3, 1, 0))
    assert controller.cancelled
    controller.finish("cancelled")
    settle()
    assert all(session.resolve(i) is None for i in indices) and len(controller.starts) == 1


@pytest.mark.parametrize("ending", ["close", "document"])
@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_close_releases_terminal_stage_before_reconciliation(harness, ending, status):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    staged = weakref.ref(session._storage.active.results[1])
    batch = weakref.ref(session._storage.active)
    refreshes, completions, diagnostics = [], [], []
    session.rasters_changed.connect(lambda: refreshes.append(1))
    session.completed.connect(completions.append)
    session.diagnostic.connect(diagnostics.append)
    controller.finish(status)  # No event settling: terminal received, publication still queued.
    assert not controller.is_busy
    assert session._storage.active.terminal.status == status
    assert session._timer.isActive() and session._storage.published is None
    assert staged() is not None and batch() is not None
    if ending == "document":
        shiboken6.delete(document)
    else:
        session.close()
    assert shiboken6.isValid(session) and session._closed
    gc.collect()
    assert session._storage.active is None
    assert staged() is None and batch() is None
    settle()
    assert not refreshes and not completions and not diagnostics
    assert session._storage.published is None and session._pending is None
    assert not session._timer.isActive() and len(controller.starts) == 1


def test_close_preserves_draining_stage_until_terminal_cleanup(harness):
    document, session, controller, indices = harness
    begin(session)
    controller.deliver(PreparedResult(1, (0, 1, 2), result(controller)))
    staged = weakref.ref(session._storage.active.results[1])
    batch = weakref.ref(session._storage.active)
    refreshes, completions, diagnostics = [], [], []
    session.rasters_changed.connect(lambda: refreshes.append(1))
    session.completed.connect(completions.append)
    session.diagnostic.connect(diagnostics.append)
    assert controller.is_busy and session._storage.active.terminal is None
    session.close()
    gc.collect()
    assert controller.is_busy and controller.cancelled
    assert staged() is not None and batch() is not None
    assert session._storage.active.results[1] is staged()
    assert session.resolve(indices[0]) is None
    settle()
    assert staged() is not None  # Waiting/cancellation is not terminal cleanup.
    controller.finish("cancelled")
    gc.collect()
    assert session._storage.active is None and staged() is None and batch() is None
    settle()
    assert not refreshes and not completions and not diagnostics
    assert session._storage.published is None and len(controller.starts) == 1
