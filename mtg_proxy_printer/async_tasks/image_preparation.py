"""Inactive sequential preparation batches with owner-thread acceptance and bounded delivery."""
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
import threading
from typing import Callable, Iterable, Literal

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from mtg_proxy_printer.async_tasks.base import AsyncTask
from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI, SourceRequest, SourceUnavailable, load_source_snapshot, plan_target_resolution
from mtg_proxy_printer.image_preparation import PreparedImage, prepare_image
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.units_and_sizes import UUID
from mtg_proxy_printer.logger import get_logger

logger = get_logger(__name__)
_batch_ids = count(1)


@dataclass(frozen=True)
class BatchEntry:
    occurrence_id: int
    source: SourceRequest | SourceUnavailable


@dataclass(frozen=True)
class PreparedResult:
    result_id: int
    occurrence_ids: tuple[int, ...]
    prepared_image: PreparedImage


@dataclass(frozen=True)
class ResultAlias:
    result_id: int
    occurrence_ids: tuple[int, ...]


@dataclass(frozen=True)
class UnavailableResult:
    occurrence_ids: tuple[int, ...]
    unavailable: SourceUnavailable


PreparationEvent = PreparedResult | ResultAlias | UnavailableResult


@dataclass(frozen=True)
class BatchProgress:
    batch_id: int
    total_occurrences: int
    resolved_occurrences: int
    unique_prepared_results: int
    unavailable_occurrences: int


@dataclass(frozen=True)
class BatchSummary:
    batch_id: int
    status: Literal["completed", "cancelled", "failed"]
    total_occurrences: int
    resolved_occurrences: int
    unique_prepared_results: int
    unavailable_occurrences: int
    error_detail: str = ""
    neural_relaunch_blocked: bool = False


def validate_selection(mode, target_ppi, worker_config=None):
    """Validate frozen metadata only; optional imports never probe the filesystem."""
    if type(mode) is not str or mode not in ("original", "lanczos", "neural"):
        raise ValueError("Preparation mode must be original, lanczos or neural")
    if type(target_ppi) is not int or target_ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("Preparation target must be integer 600, 900 or 1200")
    if mode == "neural":
        from mtg_proxy_printer.neural_worker_client import NeuralWorkerConfig
        if not isinstance(worker_config, NeuralWorkerConfig):
            raise TypeError("Neural mode requires an explicit frozen NeuralWorkerConfig")
    elif worker_config is not None:
        raise ValueError("worker_config is only valid for neural mode")


def _error_detail(error):
    return f"{type(error).__name__}: {error}"[:1024]


def _cleanup_incomplete(report):
    return report is not None and (report.limitations or not report.reaped
                                   or not report.threads_stopped or not report.temporary_tree_removed)


def _freeze_entries(entries) -> tuple[BatchEntry, ...]:
    """Cheap metadata capture only. Never hash bytes, read files or access models."""
    frozen, seen = [], set()
    for entry in entries:
        if isinstance(entry, BatchEntry):
            occurrence, source = entry.occurrence_id, entry.source
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            occurrence, source = entry
        else:
            raise TypeError("Each entry must contain an occurrence ID and a captured source")
        if type(occurrence) is not int or occurrence in seen:
            raise ValueError("Occurrence IDs must be unique batch-local integers")
        seen.add(occurrence)
        if isinstance(source, SourceUnavailable):
            if type(source.reason) is not str or type(source.detail) is not str:
                raise TypeError("Unavailable metadata must be strings")
            source = SourceUnavailable(source.reason, source.detail)
        elif isinstance(source, SourceRequest):
            key = source.image_key
            if (not isinstance(key, ImageKey) or not isinstance(key.scryfall_id, str)
                    or not UUID.uuid_re.fullmatch(key.scryfall_id) or type(key.is_front) is not bool
                    or type(key.image_quality) is not ImageQuality
                    or key.image_quality not in (ImageQuality.low_resolution, ImageQuality.high_resolution, ImageQuality.custom_card)):
                raise ValueError("Invalid captured source identity")
            dimensions = source.canonical_dimensions
            if not isinstance(dimensions, (tuple, list)) or len(dimensions) != 2 or any(
                    type(axis) is not int or axis <= 0 for axis in dimensions):
                raise ValueError("Captured canonical dimensions must be two positive integers")
            if source.custom_bytes is not None and type(source.custom_bytes) is not bytes:
                raise TypeError("Captured custom payload must be immutable bytes")
            source = SourceRequest(ImageKey(str(key.scryfall_id), key.is_front, key.image_quality), tuple(dimensions),
                                   original_path=Path(str(source.original_path)) if source.original_path is not None else None,
                                   custom_bytes=source.custom_bytes)
        else:
            raise TypeError("Batch sources must be SourceRequest or SourceUnavailable")
        frozen.append(BatchEntry(occurrence, source))
    return tuple(frozen)


class _BatchControl:
    """Standalone cancellation/mailbox state; no QObject, callback or controller references."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.condition = threading.Condition()
        self._pending: tuple[int, PreparationEvent] | None = None
        self._claimed = False
        self._acknowledged = 0
        self._next_delivery = 0

    def cancel(self, *_):
        with self.condition:
            self.cancelled.set()
            self._pending = None
            self._claimed = False
            self.condition.notify_all()

    def deliver(self, event, batch_id, notify) -> bool:
        with self.condition:
            if self.cancelled.is_set():
                return False
            if self._pending is not None:
                raise RuntimeError("Preparation mailbox already contains a pending delivery")
            self._next_delivery += 1
            delivery = self._next_delivery
            self._pending = delivery, event
        notify(batch_id, delivery)  # Lightweight signal only, outside the lock.
        with self.condition:
            self.condition.wait_for(lambda: self.cancelled.is_set() or self._acknowledged == delivery)
            return not self.cancelled.is_set() and self._acknowledged == delivery

    def take(self, delivery):
        with self.condition:
            if (self.cancelled.is_set() or self._pending is None
                    or self._pending[0] != delivery or self._claimed):
                return None
            self._claimed = True
            return self._pending[1]

    def acknowledge(self, delivery, accepted):
        with self.condition:
            if self._pending is None or self._pending[0] != delivery or not self._claimed:
                return False
            if accepted:
                self._acknowledged = delivery
            else:
                self.cancelled.set()
            self._pending = None
            self._claimed = False
            self.condition.notify_all()
            return True


def _groups(entries):
    groups, indices = [], {}
    for entry in entries:
        source = entry.source
        if isinstance(source, SourceUnavailable):
            groups.append((source, [entry.occurrence_id]))
            continue
        locator = (source.image_key, source.canonical_dimensions,
                   ("file", str(source.original_path)) if source.original_path is not None
                   else ("bytes-object", id(source.custom_bytes)))
        if locator not in indices:
            indices[locator] = len(groups)
            groups.append((source, []))
        groups[indices[locator]][1].append(entry.occurrence_id)
    return groups


class PreparationBatchTask(AsyncTask):
    delivery_ready = Signal(object, object)  # Batch and monotonically increasing delivery IDs, never image bytes.
    terminal = Signal(object, str, str)

    def __init__(self, batch_id: int, entries, *, mode, target_ppi, cache, worker_config=None):
        super().__init__()  # Parentless; retained by controller and existing AsyncTaskRunner.
        if type(batch_id) is not int:
            raise TypeError("Batch ID must be an integer")
        validate_selection(mode, target_ppi, worker_config)
        if not isinstance(cache, DerivativeCache):
            raise TypeError("An explicit DerivativeCache is required")
        self.batch_id = batch_id
        self._entries = _freeze_entries(entries)
        self.total_occurrences = len(self._entries)
        self.mode, self.target_ppi = mode, target_ppi
        self.worker_config = worker_config
        self.neural_relaunch_blocked = False
        self.cache = DerivativeCache(Path(cache.root))
        self._control = _BatchControl()

    @property
    def can_cancel(self) -> bool:
        return True

    @Slot()
    def cancel(self):
        self._control.cancel()

    def _process_group(self, source, occurrences, accepted, result_ids, preparer=None):
        control = self._control
        if control.cancelled.is_set():
            return None
        if isinstance(source, SourceUnavailable):
            control.deliver(UnavailableResult(occurrences, source), self.batch_id, self.delivery_ready.emit)
            return None
        snapshot = load_source_snapshot(source)
        if control.cancelled.is_set():
            return None
        if isinstance(snapshot, SourceUnavailable):
            control.deliver(UnavailableResult(occurrences, snapshot), self.batch_id, self.delivery_ready.emit)
            return None
        plan = plan_target_resolution(snapshot, self.target_ppi)
        key = (snapshot.identity, tuple(snapshot.canonical_dimensions), tuple(snapshot.native_dimensions),
               snapshot.image_format, snapshot.decode_policy, snapshot.qt_decoder_version,
               self.mode, self.target_ppi, tuple(plan.target_dimensions))
        if control.cancelled.is_set():
            return None
        if key in accepted:
            control.deliver(ResultAlias(accepted[key], occurrences), self.batch_id, self.delivery_ready.emit)
            return None
        prepared = (preparer.prepare(snapshot, plan) if preparer is not None
                    else prepare_image(snapshot, plan, mode=self.mode, cache=self.cache))
        if control.cancelled.is_set():
            return None
        result_id = next(result_ids)
        if control.deliver(PreparedResult(result_id, occurrences, prepared), self.batch_id, self.delivery_ready.emit):
            return key, result_id
        return None

    def run(self):
        status, detail = "failed", "Worker exited before completing the batch"
        preparer = None
        # Keep optional host modules out of startup and the baseline paths.
        cancellation_type = ()
        primary = None
        try:
            self.task_begins.emit(self.total_occurrences, "Preparing card images")
            if self.mode == "neural" and not self._control.cancelled.is_set():
                from mtg_proxy_printer.image_preparation_neural import NeuralImagePreparer
                from mtg_proxy_printer.neural_worker_client import NeuralWorkerCancelled
                cancellation_type = NeuralWorkerCancelled
                preparer = NeuralImagePreparer(self.worker_config, self.cache, self._control.cancelled)
            accepted, result_ids = {}, count(1)
            for source, occurrences in _groups(self._entries):
                if self._control.cancelled.is_set():
                    break
                # Release per-image snapshots/buffers before advancing to another group.
                result = self._process_group(source, tuple(occurrences), accepted, result_ids, preparer)
                if result is not None:
                    accepted[result[0]] = result[1]  # Metadata only, after owner acceptance.
            status = "cancelled" if self._control.cancelled.is_set() else "completed"
            detail = ""
        except cancellation_type as error:
            primary = error
            self._control.cancel()
            status, detail = "cancelled", ""
        except Exception as error:
            primary = error
            logger.exception("Image preparation batch failed")
            status, detail = "failed", _error_detail(error)
        finally:
            cleanup_error = None
            if preparer is not None:
                try:
                    preparer.close()
                except cancellation_type as error:
                    cleanup_error = error
                    self._control.cancel()
                    if status != "failed":
                        status, detail = "cancelled", ""
                except Exception as error:
                    cleanup_error = error
                    if status != "failed":
                        status, detail = "failed", _error_detail(error)
            # A final public report supersedes a prior incomplete report after owned cleanup retry.
            report = preparer.cleanup_report if preparer is not None else None
            if report is None:
                report = (getattr(cleanup_error, "cleanup_report", None)
                          or getattr(primary, "cleanup_report", None))
            if _cleanup_incomplete(report):
                limitation = str(report)[:1800]
                cause = detail or (_error_detail(primary or cleanup_error) if primary or cleanup_error else status)
                detail = (cause[:1024] + "; neural cleanup incomplete: " + limitation)[:4096]
                status = "failed"
                self.neural_relaunch_blocked = not report.reaped or not report.threads_stopped
                logger.error("Image preparation teardown limitation: %s", detail)
            elif cleanup_error is not None and not isinstance(cleanup_error, cancellation_type):
                detail = (detail + "; cleanup: " + _error_detail(cleanup_error))[:4096]
                # Unknown cleanup failure cannot establish that owned resources have ended.
                self.neural_relaunch_blocked = True
                logger.error("Image preparation teardown failure: %s", detail)
            if status == "failed":
                self._control.cancel()
            elif self._control.cancelled.is_set():
                status = "cancelled"
            self._entries = ()
            preparer = primary = cleanup_error = None
            try:
                self.terminal.emit(self.batch_id, status, detail[:4096])
            finally:
                self.task_completed.emit()  # Runner, not this task, emits task_deleted.


@dataclass
class _ActiveBatch:
    task: PreparationBatchTask
    resolved: int = 0
    unique: int = 0
    unavailable: int = 0
    failure: str = ""
    connections: list = field(default_factory=list)


class ImagePreparationController(QObject):
    progress = Signal(object)  # BatchProgress, metadata only.
    finished = Signal(object)  # BatchSummary, metadata only.

    def __init__(self, submit_task: Callable, consume_result: Callable, parent=None):
        super().__init__(parent)
        if not callable(submit_task) or not callable(consume_result):
            raise TypeError("Submission and synchronous acceptance callbacks must be callable")
        self._submit_task, self._consume_result = submit_task, consume_result
        self._active: _ActiveBatch | None = None
        self._closed = False
        self._neural_relaunch_blocked = False
        self._delivering = False
        self._deferred_terminal = None

    def _assert_owner(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError("ImagePreparationController operations require its owning Qt thread")

    @property
    def is_busy(self) -> bool:
        self._assert_owner()
        return self._active is not None

    def start(self, entries: Iterable, *, mode, target_ppi, cache, worker_config=None) -> int:
        self._assert_owner()
        if self._closed:
            raise RuntimeError("Image preparation controller is closed")
        if self._active is not None:
            raise RuntimeError("An image preparation batch is already active")
        validate_selection(mode, target_ppi, worker_config)
        if mode == "neural" and self._neural_relaunch_blocked:
            raise RuntimeError("Neural preparation blocked: prior worker teardown is unresolved")
        task = PreparationBatchTask(next(_batch_ids), entries, mode=mode, target_ppi=target_ppi,
                                    cache=cache, worker_config=worker_config)
        active = _ActiveBatch(task)
        active.connections = [
            task.delivery_ready.connect(self._on_delivery, Qt.ConnectionType.QueuedConnection),
            task.terminal.connect(self._on_terminal, Qt.ConnectionType.QueuedConnection),
            self.destroyed.connect(task._control.cancel, Qt.ConnectionType.DirectConnection),
        ]
        self._active = active
        try:
            self._submit_task(task)
        except Exception as error:
            task.cancel()
            task._entries = ()
            self._active = None
            self._disconnect(active)
            raise RuntimeError("Unable to schedule image preparation batch: " + str(error)) from error
        return task.batch_id

    @Slot()
    def cancel(self):
        self._assert_owner()
        if self._active is not None:
            self._active.task.cancel()

    @Slot()
    def close(self):
        self._assert_owner()
        self._closed = True
        self.cancel()

    @staticmethod
    def _disconnect(active):
        for connection in active.connections:
            QObject.disconnect(connection)
        active.connections.clear()

    @Slot(object, object)
    def _on_delivery(self, batch_id, delivery):
        self._assert_owner()
        active = self._active
        if active is None or active.task.batch_id != batch_id or self._closed or self._delivering:
            return
        control = active.task._control
        event = control.take(delivery)
        if event is None:
            return
        if control.cancelled.is_set():
            return
        self._delivering = True
        accepted = False
        try:
            self._consume_result(batch_id, event)  # No lock held; normal return is explicit acceptance.
            accepted = True
            active.resolved += len(event.occurrence_ids)
            if isinstance(event, PreparedResult):
                active.unique += 1
            elif isinstance(event, UnavailableResult):
                active.unavailable += len(event.occurrence_ids)
            active.task.set_progress.emit(active.resolved)
            self.progress.emit(BatchProgress(batch_id, active.task.total_occurrences, active.resolved,
                                             active.unique, active.unavailable))
        except Exception as error:
            logger.exception("Image preparation consumer rejected a delivery")
            active.failure = _error_detail(error)
            control.cancel()
        finally:
            event = None  # Drop the owner slot's payload before waking work or forwarding deferred terminal metadata.
            control.acknowledge(delivery, accepted)
            self._delivering = False
            if self._deferred_terminal is not None:
                terminal, self._deferred_terminal = self._deferred_terminal, None
                self._on_terminal(*terminal)

    @Slot(object, str, str)
    def _on_terminal(self, batch_id, status, detail):
        self._assert_owner()
        active = self._active
        if active is None or active.task.batch_id != batch_id:
            return
        if self._delivering:
            self._deferred_terminal = batch_id, status, detail
            return
        self._neural_relaunch_blocked |= active.task.neural_relaunch_blocked
        if active.failure:
            status, detail = "failed", (active.failure + ("; " + detail if detail else ""))[:4096]
        elif status != "failed" and (active.task._control.cancelled.is_set() or self._closed):
            status, detail = "cancelled", ""
        summary = BatchSummary(batch_id, status, active.task.total_occurrences, active.resolved,
                               active.unique, active.unavailable, detail, active.task.neural_relaunch_blocked)
        self._active = None
        self._disconnect(active)
        self.finished.emit(summary)  # Idle before notification; a handler may start the next batch.
