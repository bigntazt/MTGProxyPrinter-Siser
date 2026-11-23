"""Optional GUI-thread current-page sessions; no normal application/export adoption."""
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QModelIndex, QPersistentModelIndex, QTimer, Qt, Signal, Slot

from mtg_proxy_printer.async_tasks.image_preparation import (
    BatchEntry, BatchSummary, ImagePreparationController, PreparedResult, ResultAlias, UnavailableResult,
)
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.image_source import SourceRequest, capture_source_request
from mtg_proxy_printer.model.card import Card
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.page_scene.prepared_pixmap import (
    PreparedPixmap, RasterDecodeFailure, decode_prepared_pixmap, require_gui_thread,
)
from mtg_proxy_printer.units_and_sizes import UUID


@dataclass(frozen=True)
class SessionCompletion:
    generation: int
    summary: BatchSummary  # Real submitted batch's accepted counts, not publication counts.
    published_occurrences: int


@dataclass(frozen=True)
class SessionDiagnostic:
    generation: int
    reason: str
    detail: str
    occurrence_ids: tuple[int, ...] = ()
    batch_id: int | None = None


@dataclass(frozen=True)
class _Request:
    generation: int
    mode: str
    target_ppi: int


@dataclass(frozen=True)
class _Occurrence:
    generation: int
    page: object
    index: QPersistentModelIndex
    card: Card
    image_key: ImageKey
    canonical_dimensions: tuple[int, int]
    source: SourceRequest
    occurrence_id: int


@dataclass
class _Batch:
    request: _Request
    page: object
    page_card_count: int
    occurrences: tuple[_Occurrence, ...]
    batch_id: int | None = None
    results: dict[int, PreparedPixmap | RasterDecodeFailure] = field(default_factory=dict)
    result_keys: dict = field(default_factory=dict)
    assignments: dict[int, int | None] = field(default_factory=dict)
    diagnostics: list[SessionDiagnostic] = field(default_factory=list)
    terminal: BatchSummary | None = None


@dataclass(frozen=True)
class _Publication:
    generation: int
    page: object
    page_card_count: int
    bindings: tuple[tuple[_Occurrence, PreparedPixmap], ...]


@dataclass
class _Storage:
    alive: bool = True
    active: _Batch | None = None
    published: _Publication | None = None

    def clear(self, *_):
        # Native destruction hook owns only this plain state, never a dead QObject.
        self.alive = False
        self.active = self.published = None


def _card_metadata(card):
    """Same ordinary-face eligibility as M28; never access DisplayRole/image_file."""
    if (type(card) is not Card or not isinstance(card.scryfall_id, str)
            or not UUID.uuid_re.fullmatch(card.scryfall_id)
            or type(card.is_front) is not bool or type(card.highres_image) is not bool):
        return None
    dimensions = card.size.as_qsize_px().toTuple()
    if dimensions not in ((745, 1040), (1040, 1490)):
        return None
    return ImageKey(str(card.scryfall_id), card.is_front, ImageQuality.of_card(card)), dimensions


class PreparedPageSession(QObject):
    rasters_changed = Signal()
    progress = Signal(object)  # M27 BatchProgress, current batch only; acceptance, not publication.
    completed = Signal(object)  # SessionCompletion, current submitted batch only.
    diagnostic = Signal(object)  # SessionDiagnostic, metadata only.

    def __init__(self, document, submit_task, source_cache_root, derivative_cache, parent=None):
        require_gui_thread()
        super().__init__(parent)
        if document.thread() != self.thread():
            raise RuntimeError("The document and preparation session must belong to the GUI thread")
        if not isinstance(derivative_cache, DerivativeCache):
            raise TypeError("An explicit DerivativeCache is required")
        # Freeze relative-root interpretation without reading, stat or cache discovery.
        self._source_root = Path(source_cache_root).absolute()
        self._cache = DerivativeCache(Path(derivative_cache.root))
        self._document = document
        self._closing = self._enabled = False
        self._generation = 0
        self._options: tuple[str, int] | None = None
        self._pending: _Request | None = None
        self._storage = _Storage()
        self.destroyed.connect(self._storage.clear, Qt.ConnectionType.DirectConnection)
        self._controller = ImagePreparationController(submit_task, self._consume, self)
        self._controller.progress.connect(self._on_progress)
        self._controller.finished.connect(self._on_finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reconcile)
        self._mutation_depth = 0
        self._refresh_needed = self._reconciling = self._again = False
        self._observations = []
        for name in ("current_page_changed", "dataChanged", "page_type_changed", "page_layout_changed"):
            self._observe(getattr(document, name), self._on_change)
        for before, after in (
                ("rowsAboutToBeInserted", "rowsInserted"), ("rowsAboutToBeRemoved", "rowsRemoved"),
                ("rowsAboutToBeMoved", "rowsMoved"), ("modelAboutToBeReset", "modelReset"),
                ("layoutAboutToBeChanged", "layoutChanged")):
            self._observe(getattr(document, before), self._before_mutation)
            self._observe(getattr(document, after), self._after_mutation)
        self._observe(document.destroyed, self._document_destroyed)

    def _observe(self, signal, slot):
        self._observations.append(signal.connect(slot))

    def _assert_gui(self):
        require_gui_thread()

    @property
    def _closed(self):
        return self._closing or not self._storage.alive

    def enable(self, *, mode, target_ppi):
        self._assert_gui()
        if type(mode) is not str or mode not in ("original", "lanczos"):
            raise ValueError("Session mode must be original or lanczos")
        if type(target_ppi) is not int or target_ppi not in (600, 1200):
            raise ValueError("Session target must be integer 600 or 1200")
        if self._closed:
            raise RuntimeError("Preparation session is closed")
        self._options = mode, target_ppi
        self._enabled = True
        self._invalidate()

    @Slot()
    def refresh(self):
        self._assert_gui()
        if self._closed:
            raise RuntimeError("Preparation session is closed")
        if self._enabled:
            self._invalidate()

    @Slot()
    def disable(self):
        self._assert_gui()
        if self._closed:
            return
        self._enabled = False
        self._invalidate()  # Queue a fallback refresh while the attached scene is alive.

    @Slot()
    def close(self):
        self._assert_gui()
        if self._closed:
            return
        self._closing = True  # Permanent teardown before any callback/cancellation.
        self._enabled = False
        self._generation += 1
        self._pending = self._storage.published = None
        if self._storage.active is not None and self._storage.active.terminal is not None:
            # Terminal already arrived; stopping reconciliation removes its remaining cleanup path.
            self._storage.active = None
        self._refresh_needed = self._again = False
        self._timer.stop()
        for connection in self._observations:
            QObject.disconnect(connection)
        self._observations.clear()
        self._document = None
        self._controller.close()  # No wait; terminal or native destruction releases staging.

    @Slot()
    def _document_destroyed(self, *_):
        self.close()  # close never dereferences the destroyed document.

    def _queue(self):
        if self._closed or self._mutation_depth:
            return
        if self._reconciling:
            self._again = True
        elif not self._timer.isActive():
            self._timer.start(0)

    def _invalidate(self):
        self._generation += 1
        self._storage.published = None
        self._pending = _Request(self._generation, *self._options) if self._enabled else None
        self._controller.cancel()
        self._refresh_needed = True
        self._queue()

    @Slot()
    def _on_change(self, *_):
        if not self._closed and self._enabled:
            self._invalidate()

    @Slot()
    def _before_mutation(self, *_):
        if self._closed:
            return
        self._mutation_depth += 1
        self._timer.stop()
        if self._enabled:
            self._invalidate()

    @Slot()
    def _after_mutation(self, *_):
        if self._closed:
            return
        self._mutation_depth = max(0, self._mutation_depth - 1)
        if self._enabled:
            self._invalidate()
        elif self._refresh_needed:
            self._queue()

    def _selected_page(self):
        try:
            index = self._document.get_current_page_index()
        except ValueError:
            # Page removal may finish before replacement selection; wait for its next notification.
            return None
        if not index.isValid() or index.model() != self._document or index.parent().isValid():
            return None
        # Page is a list subclass: a QVariant data round-trip can copy it into a
        # new list. The model index's Python internal pointer preserves identity.
        page = self._document.index(index.row(), index.column()).internalPointer()
        return index, page

    def _matches(self, occurrence, page_index, page):
        index = occurrence.index
        return (occurrence.generation == self._generation and occurrence.page is page
                and index.isValid() and index.model() == self._document
                and index.column() == PageColumns.Image and index.parent() == page_index
                and index.data(Qt.ItemDataRole.UserRole) is occurrence.card
                and _card_metadata(occurrence.card) == (occurrence.image_key, occurrence.canonical_dimensions))

    def _current(self, batch):
        if (self._closed or not self._enabled or self._mutation_depth
                or batch.request.generation != self._generation
                or (batch.request.mode, batch.request.target_ppi) != self._options):
            return False
        selected = self._selected_page()
        return (selected is not None and selected[1] is batch.page
                and self._document.rowCount(selected[0]) == batch.page_card_count
                and all(self._matches(occurrence, *selected) for occurrence in batch.occurrences))

    def resolve(self, image_index) -> PreparedPixmap | None:
        self._assert_gui()
        published = self._storage.published
        if (self._closed or not self._enabled or self._mutation_depth or published is None
                or published.generation != self._generation):
            return None
        if (not isinstance(image_index, (QModelIndex, QPersistentModelIndex)) or not image_index.isValid()
                or image_index.model() != self._document or image_index.column() != PageColumns.Image):
            return None
        selected = self._selected_page()
        if (selected is None or selected[1] is not published.page
                or self._document.rowCount(selected[0]) != published.page_card_count):
            return None
        for occurrence, value in published.bindings:
            if image_index == occurrence.index and self._matches(occurrence, *selected):
                return value
        return None

    def _consume(self, batch_id, event):
        # Called synchronously by M27: validation/staging only, no public notification or scene work.
        self._assert_gui()
        batch = self._storage.active
        if batch is None or batch.batch_id != batch_id or not self._current(batch):
            self._controller.cancel()
            return
        occurrences = {occurrence.occurrence_id: occurrence for occurrence in batch.occurrences}
        ids = event.occurrence_ids
        if not ids or any(i not in occurrences or i in batch.assignments for i in ids):
            raise ValueError("Unknown or repeated preparation occurrence")
        keys = {(occurrences[i].image_key, occurrences[i].canonical_dimensions) for i in ids}
        if isinstance(event, PreparedResult):
            if event.result_id in batch.results or len(keys) != 1:
                raise ValueError("Repeated result ID or inconsistent result occurrences")
            prepared = event.prepared_image
            if ((prepared.source_identity.image_key, prepared.canonical_dimensions) not in keys
                    or prepared.target_ppi != batch.request.target_ppi):
                raise ValueError("Prepared result does not match the captured request")
            value = decode_prepared_pixmap(prepared)
            batch.results[event.result_id] = value
            batch.result_keys[event.result_id] = next(iter(keys))
            if isinstance(value, RasterDecodeFailure):
                batch.diagnostics.append(SessionDiagnostic(self._generation, value.reason, value.detail, ids, batch_id))
        elif isinstance(event, ResultAlias):
            if event.result_id not in batch.results:
                raise ValueError("Alias references an unknown preparation result")
            if keys != {batch.result_keys[event.result_id]}:
                raise ValueError("Alias does not match its captured source identity")
        elif isinstance(event, UnavailableResult):
            batch.diagnostics.append(SessionDiagnostic(
                self._generation, event.unavailable.reason, event.unavailable.detail, ids, batch_id))
        else:
            raise TypeError("Unexpected preparation event")
        for i in ids:
            batch.assignments[i] = None if isinstance(event, UnavailableResult) else event.result_id

    @Slot(object)
    def _on_progress(self, progress):
        batch = self._storage.active
        if batch is not None and batch.batch_id == progress.batch_id and self._current(batch):
            self.progress.emit(progress)  # No work follows a potentially reentrant notification.

    @Slot(object)
    def _on_finished(self, summary):
        batch = self._storage.active
        if batch is None or batch.batch_id != summary.batch_id:
            return
        if self._closed or batch.request.generation != self._generation:
            self._storage.active = None
        else:
            batch.terminal = summary
        self._queue()  # Terminal alone never creates another pending request.

    def _capture(self, request):
        selected = self._selected_page()
        if selected is None:
            return None
        page_index, page = selected
        count = self._document.rowCount(page_index)
        occurrences = []
        for row in range(count):
            index = self._document.index(row, PageColumns.Image, page_index)
            card = index.data(Qt.ItemDataRole.UserRole)
            metadata = _card_metadata(card)
            if metadata is None:
                continue
            source = capture_source_request(card, cache_root=self._source_root)
            if not isinstance(source, SourceRequest):
                raise TypeError("Eligible ordinary source capture must return SourceRequest")
            occurrences.append(_Occurrence(request.generation, page, QPersistentModelIndex(index), card,
                                           *metadata, source, len(occurrences)))
        return _Batch(request, page, count, tuple(occurrences))

    def _finalize(self, batch):
        summary = batch.terminal
        diagnostics = tuple(batch.diagnostics)
        published_count = 0
        if summary.status == "completed" and self._current(batch):
            if len(batch.assignments) != len(batch.occurrences):
                raise RuntimeError("Completed batch has unresolved staged occurrences")
            bindings = tuple((occurrence, batch.results[batch.assignments[occurrence.occurrence_id]])
                             for occurrence in batch.occurrences
                             if batch.assignments[occurrence.occurrence_id] is not None
                             and isinstance(batch.results[batch.assignments[occurrence.occurrence_id]], PreparedPixmap))
            self._storage.published = _Publication(self._generation, batch.page, batch.page_card_count, bindings)
            published_count = len(bindings)
            self._refresh_needed = True
            self._queue()
        self._storage.active = None
        return SessionCompletion(batch.request.generation, summary, published_count), diagnostics

    @Slot()
    def _reconcile(self):
        self._assert_gui()
        if self._closed or self._mutation_depth:
            return
        if self._reconciling:
            self._again = True
            return
        self._reconciling = True
        try:
            generation = self._generation
            if self._refresh_needed:
                self._refresh_needed = False
                self.rasters_changed.emit()
                if self._closed or generation != self._generation or self._mutation_depth:
                    return
            batch = self._storage.active
            if batch is not None and batch.terminal is not None:
                if not self._current(batch):
                    self._storage.active = None
                else:
                    completion, diagnostics = self._finalize(batch)
                    batch = None  # Release obsolete staging before public metadata callbacks.
                    for diagnostic in diagnostics:
                        self.diagnostic.emit(diagnostic)
                        if self._closed or generation != self._generation or self._mutation_depth:
                            return
                    self.completed.emit(completion)
                    if self._closed or generation != self._generation or self._mutation_depth:
                        return
            if self._storage.active is not None or self._controller.is_busy or self._pending is None:
                return
            request, self._pending = self._pending, None  # Consume once; errors/terminal do not retry.
            batch = self._capture(request)
            if batch is None or not batch.occurrences:
                self.diagnostic.emit(SessionDiagnostic(generation, "idle_no_eligible_sources",
                                                      "No current eligible ordinary-card sources"))
                return
            self._storage.active = batch
            try:
                batch.batch_id = self._controller.start(
                    tuple(BatchEntry(occurrence.occurrence_id, occurrence.source) for occurrence in batch.occurrences),
                    mode=request.mode, target_ppi=request.target_ppi, cache=self._cache)
            except RuntimeError as error:
                self._storage.active = None
                if not self._closed and generation == self._generation:
                    self.diagnostic.emit(SessionDiagnostic(generation, "scheduling_failed", str(error)))
        finally:
            self._reconciling = False
            if self._again:
                self._again = False
                self._queue()
