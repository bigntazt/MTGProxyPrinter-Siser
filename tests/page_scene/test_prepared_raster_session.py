"""Isolated real M24–M29 services and owner-thread session boundaries."""
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
from pathlib import Path
import threading
import weakref
from unittest.mock import patch

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThread, QThreadPool, Qt
from PySide6.QtTest import QSignalSpy

from mtg_proxy_printer.async_tasks.base import AsyncTaskRunner
from mtg_proxy_printer.async_tasks import image_preparation as worker
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage, ActionRemovePage
from mtg_proxy_printer.document_controller.move_cards import ActionMoveCardsWithinPage, ActionMoveCardsBetweenPages
from mtg_proxy_printer.document_controller.new_document import ActionNewDocument
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.model.card import CheckCard, CustomCard
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.page_scene import prepared_raster_session as module
from mtg_proxy_printer.page_scene.page_scene import PageScene
from mtg_proxy_printer.units_and_sizes import unit_registry
from tests.page_scene.test_prepared_scene import ordinary, MODE
from tests.page_scene.test_card_raster import pixels, render, assert_rect
from tests.test_image_source import encoded, write_original


class Harness:
    def __init__(self, document, qtbot, root):
        self.document, self.qtbot, self.root = document, qtbot, root
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.tasks, self.runners, self.held, self.gates = [], [], [], []
        self.hold = self.reject = False
        self.scene = None
        self.session = module.PreparedPageSession(document, self.submit, root / "sources", DerivativeCache(root / "derived"))
        self.completions, self.diagnostics, self.progress = [], [], []
        self.session.completed.connect(self.completions.append)
        self.session.diagnostic.connect(self.diagnostics.append)
        self.session.progress.connect(self.progress.append)

    def submit(self, task):
        assert QThread.currentThread() == self.session.thread()
        self.tasks.append(task)  # Owner-created task survives runner and queued lifecycle cleanup.
        if self.reject:
            raise RuntimeError("Synthetic scheduling refusal")
        runner = AsyncTaskRunner(task)
        self.runners.append(runner)
        if self.hold:
            self.held.append(runner)
        else:
            self.pool.start(runner)

    def launch_held(self):
        for runner in self.held:
            self.pool.start(runner)
        self.held.clear()

    def attach_scene(self):
        self.scene = PageScene(self.document, MODE, raster_resolver=self.session.resolve)
        self.session.rasters_changed.connect(self.scene.refresh_card_rasters)
        self.scene.destroyed.connect(self.session.close)
        return self.scene

    def add(self, count=2, name="Synthetic"):
        card = ordinary(name=name)
        self.document.apply(ActionAddCard(card, count))
        return card

    def write(self, card, data):
        return write_original(self.root / "sources", card, data)

    def index(self, row=0):
        return self.document.index(row, PageColumns.Image, self.document.get_current_page_index())

    def wait(self, count=1):
        self.qtbot.waitUntil(lambda: len(self.completions) >= count, timeout=30000)
        self.qtbot.waitUntil(lambda: self.session._storage.active is None, timeout=5000)
        return self.completions[count-1]

    def cleanup(self):
        if shiboken6.isValid(self.session):
            self.session.close()
        for task in self.tasks:
            if shiboken6.isValid(task):
                task.cancel()
        for gate in self.gates:
            gate.set()
        self.launch_held()
        assert self.pool.waitForDone(10000), "Own preparation pool did not drain"
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
        assert not ({id(runner) for runner in self.runners} & AsyncTaskRunner.INSTANCES.keys())
        if self.scene is not None and shiboken6.isValid(self.scene):
            shiboken6.delete(self.scene)


@pytest.fixture
def harness(document_light, qtbot, tmp_path):
    document_light.page_layout = PageLayoutSettings(
        paper_size="A4", row_spacing=2*unit_registry.mm, column_spacing=3*unit_registry.mm,
        card_bleed=1*unit_registry.mm, cut_marker_style="Solid", print_registration_marks_style="Bullseye")
    h = Harness(document_light, qtbot, tmp_path)
    try:
        yield h
    finally:
        h.cleanup()


def settle():
    for _ in range(4):
        QCoreApplication.processEvents()


def test_disabled_construction_no_capture_no_tasks_and_identical_default_scene(harness, monkeypatch):
    h = harness
    h.add()
    monkeypatch.setattr(module, "capture_source_request", lambda *a, **k: pytest.fail("Disabled capture"))
    plain = PageScene(h.document, MODE)
    scene = h.attach_scene()
    settle()
    assert h.tasks == [] and render(scene) == render(plain)
    assert h.session.resolve(h.index()) is None
    shiboken6.delete(plain)


def test_invalid_options_do_not_change_valid_state_and_empty_page_never_submits(harness):
    h = harness
    h.session.enable(mode="original", target_ppi=600)
    generation, pending = h.session._generation, h.session._pending
    for mode, ppi in (("ai", 600), ("original", True), ("original", 300), (None, 1200)):
        with pytest.raises(ValueError):
            h.session.enable(mode=mode, target_ppi=ppi)
        assert h.session._generation == generation and h.session._pending is pending
    settle()
    assert not h.tasks and not h.completions
    assert h.diagnostics[-1].reason == "idle_no_eligible_sources"
    settle()
    assert not h.tasks


def test_capture_uses_metadata_only_and_workers_receive_only_frozen_requests(harness, monkeypatch):
    h = harness
    h.add()
    h.hold = True
    original = h.document.data
    def data(index, role=Qt.ItemDataRole.DisplayRole):
        assert role != Qt.ItemDataRole.DisplayRole, "Capture touched the legacy image"
        return original(index, role)
    monkeypatch.setattr(h.document, "data", data)
    with patch.object(Path, "read_bytes", side_effect=AssertionError("Owner read a source")), \
            patch.object(hashlib, "sha256", side_effect=AssertionError("Owner hashed bytes")):
        h.session.enable(mode="original", target_ppi=600)
        h.qtbot.waitUntil(lambda: bool(h.tasks))
    assert len(h.tasks[0]._entries) == 2
    assert all(isinstance(entry, worker.BatchEntry) and type(entry.source.canonical_dimensions) is tuple
               and entry.source.custom_bytes is None for entry in h.tasks[0]._entries)


def test_custom_composite_placeholder_invalid_identity_are_never_captured(harness, monkeypatch):
    h = harness
    front, back = ordinary(), ordinary(name="Back")
    custom = CustomCard("Custom", front.set, "C1", "en", True, "", True,
                        front.size, False, encoded())
    placeholder = ordinary()
    placeholder.scryfall_id = placeholder.image_uri = ""
    invalid = ordinary()
    invalid.is_front = "front"
    h.document.apply(ActionNewPage(content=[[custom, CheckCard(front, back), placeholder, invalid]]))
    h.document.set_currently_edited_page(h.document.pages[1])
    monkeypatch.setattr(module, "capture_source_request", lambda *a, **k: pytest.fail("Ineligible capture"))
    h.session.enable(mode="original", target_ppi=600)
    settle()
    assert not h.tasks and h.diagnostics[-1].reason == "idle_no_eligible_sources"


def test_real_pipeline_targets_duplicates_refresh_bytes_and_disable(harness, monkeypatch):
    h = harness
    card = h.add()
    source = encoded(149, 208, color="#bf497180")
    path = h.write(card, source)
    scene = h.attach_scene()
    default = render(scene)
    geometry = scene.geometry
    model_state = pixels(card.image_file), card.image_file.cacheKey(), tuple(h.document.undo_stack)
    changes = QSignalSpy(h.document.dataChanged)
    decoded, prepared_refs = [], []
    original = module.decode_prepared_pixmap
    def decode(prepared):
        assert QThread.currentThread() == h.session.thread()
        decoded.append(prepared.source_identity.sha256)
        prepared_refs.append(weakref.ref(prepared))
        return original(prepared)
    monkeypatch.setattr(module, "decode_prepared_pixmap", decode)
    for number, ppi in enumerate((600, 1200), start=1):
        h.session.enable(mode="lanczos", target_ppi=ppi)
        assert h.session.resolve(h.index()) is None
        completion = h.wait(number)
        assert completion.summary.status == "completed"
        assert completion.summary.resolved_occurrences == completion.published_occurrences == 2
        assert completion.summary.unique_prepared_results == 1
        h.qtbot.waitUntil(lambda: scene.card_items[0].card_pixmap_item.pixmap().width() == 745*(ppi//300))
        value = h.session.resolve(h.index())
        assert value is h.session.resolve(h.index(1))
        assert value.actual_dimensions == (745*(ppi//300), 1040*(ppi//300))
        assert len(decoded) == number
        assert scene.geometry == geometry
        for item in scene.card_items:
            assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)
            trim = geometry.placements[item.index.row()].trim_px
            assert item.pos() == scene._presentation_position(trim.x, trim.y)
        print("real session:", ppi, value.actual_dimensions, completion, "trim", scene.card_items[0].pos().toTuple())
    old_hash, old_pixels = value.source_identity.sha256, pixels(value.pixmap)
    changed = encoded(149, 208, color="#3280c080")
    path.write_bytes(changed)
    assert h.session.resolve(h.index()).source_identity.sha256 == old_hash  # No file watcher.
    h.session.refresh()
    assert h.session.resolve(h.index()) is None
    h.wait(3)
    value = h.session.resolve(h.index())
    assert value.source_identity.sha256 == hashlib.sha256(changed).hexdigest() != old_hash
    assert pixels(value.pixmap) != old_pixels
    reference = weakref.ref(value)
    del value
    gc.collect()
    assert all(ref() is None for ref in prepared_refs), "Session retained encoded PreparedImage payloads"
    h.session.disable()
    assert h.session.resolve(h.index()) is None
    h.qtbot.waitUntil(lambda: scene.card_items[0].card_pixmap_item.pixmap().size() == card.image_file.size())
    assert render(scene) == default and reference() is None
    assert changes.count() == 0
    assert (pixels(card.image_file), card.image_file.cacheKey(), tuple(h.document.undo_stack)) == model_state
    assert path.read_bytes() == changed


def test_explicit_original_retains_native_source_and_resolver_has_no_io(harness):
    h = harness
    card = h.add()
    h.write(card, encoded(73, 109))
    h.session.enable(mode="original", target_ppi=1200)
    h.wait()
    index = h.index()
    value = h.session.resolve(index)
    assert value.outcome == "original" and value.actual_dimensions == (73, 109)
    def forbidden(*a, **k):
        raise AssertionError("Resolver performed work")
    with patch.object(Path, "read_bytes", forbidden), patch.object(Path, "stat", forbidden), \
            patch.object(hashlib, "sha256", forbidden), \
            patch.object(module, "decode_prepared_pixmap", forbidden), \
            patch.object(module, "capture_source_request", forbidden), \
            patch.object(DerivativeCache, "lookup", forbidden), patch.object(DerivativeCache, "store", forbidden), \
            patch.object(h.session._controller, "start", forbidden):
        assert h.session.resolve(index) is value
        assert h.session.resolve(h.document.index(0, 0)) is None


def test_real_staging_invisible_and_current_progress_only(harness, monkeypatch):
    h = harness
    first = h.add(1)
    second = ordinary(name="Second")
    second.scryfall_id = "00000000-0000-0000-0000-000000000000"
    h.document.apply(ActionAddCard(second))
    h.write(first, encoded())
    h.write(second, encoded(color="#3280c0"))
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    load = worker.load_source_snapshot
    def gated(request):
        assert QThread.currentThread() != h.session.thread()
        if request.image_key.scryfall_id == second.scryfall_id:
            entered.set()
            assert release.wait(10), "Test source barrier timed out"
        return load(request)
    monkeypatch.setattr(worker, "load_source_snapshot", gated)
    h.session.enable(mode="original", target_ppi=600)
    h.qtbot.waitUntil(entered.is_set, timeout=5000)
    assert len(h.session._storage.active.results) == 1
    assert h.session._storage.active.page is h.document.currently_edited_page
    assert h.progress[-1].resolved_occurrences == 1
    assert h.session.resolve(h.index()) is None and not h.completions
    h.session.enable(mode="original", target_ppi=1200)
    progress_count = len(h.progress)
    release.set()
    completion = h.wait()
    assert len(h.tasks) == 2 and completion.summary.batch_id == h.tasks[-1].batch_id
    assert all(p.batch_id == h.tasks[-1].batch_id for p in h.progress[progress_count:])


@pytest.mark.parametrize("failure", ["cancel", "worker", "consumer", "schedule"])
def test_real_cancel_and_failures_do_not_publish_or_retry(harness, monkeypatch, failure):
    h = harness
    h.write(h.add(), encoded())
    h.hold = failure == "cancel"
    h.reject = failure == "schedule"
    def fail(*a, **k):
        raise ValueError("Synthetic failure")
    if failure == "worker":
        monkeypatch.setattr(worker, "prepare_image", fail)
    elif failure == "consumer":
        monkeypatch.setattr(module, "decode_prepared_pixmap", fail)
    h.session.enable(mode="original", target_ppi=600)
    h.qtbot.waitUntil(lambda: len(h.tasks) == 1)
    if failure == "cancel":
        h.tasks[0].cancel()  # Existing task Cancel action, without creating another request.
        h.launch_held()
    if failure == "schedule":
        h.qtbot.waitUntil(lambda: bool(h.diagnostics))
        assert h.diagnostics[-1].reason == "scheduling_failed" and not h.completions
    else:
        assert h.wait().summary.status == ("cancelled" if failure == "cancel" else "failed")
        assert h.completions[0].published_occurrences == 0
    settle()
    assert len(h.tasks) == 1 and h.session.resolve(h.index()) is None


@pytest.mark.parametrize("ending", ["close", "document", "native"])
def test_teardown_during_worker_operation_suppresses_late_model_access(harness, monkeypatch, ending):
    h = harness
    h.write(h.add(), encoded())
    entered, release = threading.Event(), threading.Event()
    h.gates.append(release)
    loader = worker.load_source_snapshot
    def gated(request):
        entered.set()
        assert release.wait(10), "Test barrier timed out"
        return loader(request)
    monkeypatch.setattr(worker, "load_source_snapshot", gated)
    h.session.enable(mode="original", target_ppi=600)
    h.qtbot.waitUntil(entered.is_set, timeout=5000)
    notifications = QSignalSpy(h.session.rasters_changed)
    if ending == "document":
        shiboken6.delete(h.document)
    elif ending == "native":
        shiboken6.delete(h.session)
    else:
        h.session.close()
    assert not release.is_set() and h.session._closed  # Teardown did not wait for native work.
    monkeypatch.setattr(h.document, "get_current_page_index", lambda: pytest.fail("Late model access"))
    release.set()
    assert h.pool.waitForDone(10000)
    settle()
    assert notifications.count() == 0 and not h.completions
    assert h.session._storage.active is h.session._storage.published is None


def test_wrong_thread_operations_reject_before_model_access(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(h.document, "get_current_page_index", lambda: pytest.fail("Wrong-thread model access"))
    calls = [lambda: h.session.enable(mode="original", target_ppi=600), h.session.refresh,
             h.session.disable, h.session.close, lambda: h.session.resolve(None),
             lambda: module.PreparedPageSession(h.document, h.submit, h.root, DerivativeCache(h.root))]
    with ThreadPoolExecutor(max_workers=1) as pool:
        for operation in calls:
            with pytest.raises(RuntimeError, match="GUI application's thread"):
                pool.submit(operation).result(timeout=5)
    assert not h.tasks


def test_real_model_movement_page_removal_and_new_document_coalesce(harness):
    h = harness
    card = h.add(3)
    h.write(card, encoded())
    h.session.enable(mode="original", target_ppi=600)
    h.wait()
    before = len(h.tasks)
    h.document.apply(ActionMoveCardsWithinPage(0, [0], 2))
    h.document.apply(ActionNewPage())
    h.document.apply(ActionMoveCardsBetweenPages(0, [0], 1))
    h.document.set_currently_edited_page(h.document.pages[1])
    h.document.apply(ActionRemovePage(position=1))  # Completion precedes replacement selection.
    assert len(h.tasks) == before
    h.wait(2)
    assert len(h.tasks) == before+1
    h.document.apply(ActionNewDocument())
    settle()
    assert h.session.resolve(h.document.index(0, 0)) is None
    assert len(h.tasks) == before+1  # Empty replacement, no fabricated completion or retry.
