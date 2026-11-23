"""Batch identity, real runner/thread delivery and isolated M24-M26 integration."""
from dataclasses import replace
import hashlib
from pathlib import Path
import threading
from unittest.mock import patch

import pytest
import shiboken6
from PySide6.QtCore import QThread, QThreadPool, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy

from mtg_proxy_printer.async_tasks import image_preparation as batch
from mtg_proxy_printer.async_tasks.base import AsyncTaskRunner
from mtg_proxy_printer.image_source import SourceRequest, SourceUnavailable, SourceSnapshot
from mtg_proxy_printer.image_preparation import PreparedImage
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality


IDENTIFIER = "12345678-1234-1234-1234-123456789abc"


def request(payload=b"synthetic-source-A", *, path=None, canonical=(745, 1040), front=True, quality=ImageQuality.custom_card):
    return SourceRequest(ImageKey(IDENTIFIER, front, quality), canonical,
                         original_path=path, custom_bytes=payload if path is None else None)


class Harness:
    def __init__(self, qtbot, root):
        self.qtbot = qtbot
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.owner = threading.get_ident()
        self.tasks, self.runners, self.held = [], [], []
        self.events, self.attempts, self.summaries, self.progresses = [], [], [], []
        self.representations = {}
        self.spies = []
        self.blockers = []
        self.hold = False
        self.submission_error = None
        self.on_submit = self.consumer = self.on_finished = None
        self.cache = DerivativeCache(root / "cache")
        self.controller = batch.ImagePreparationController(self.submit, self.consume)
        self.controller.finished.connect(self.finished)
        self.controller.progress.connect(self.progresses.append)

    def submit(self, task):
        self.tasks.append(task)
        self.spies.append({name: QSignalSpy(getattr(task, name)) for name in
                           ("terminal", "task_completed", "task_deleted", "task_begins", "set_progress", "error_occurred")})
        assert task.parent() is None and task.can_cancel
        if self.on_submit is not None:
            self.on_submit(task)
        if self.submission_error is not None:
            raise self.submission_error
        if self.hold:
            self.held.append(task)
        else:
            self.launch(task)

    def launch(self, task):
        runner = AsyncTaskRunner(task)
        self.runners.append(runner)
        self.pool.start(runner)

    def launch_held(self):
        pending, self.held = self.held, []
        for task in pending:
            self.launch(task)

    def consume(self, batch_id, event):
        assert threading.get_ident() == self.owner
        assert QThread.currentThread() == self.controller.thread()
        self.attempts.append((batch_id, event))
        if self.consumer is not None:
            self.consumer(batch_id, event)
        if isinstance(event, batch.PreparedResult):
            self.representations[batch_id, event.result_id] = event.prepared_image
        elif isinstance(event, batch.ResultAlias):
            assert (batch_id, event.result_id) in self.representations
        self.events.append(event)

    def finished(self, summary):
        assert threading.get_ident() == self.owner
        assert not self.controller.is_busy
        self.summaries.append(summary)
        if self.on_finished is not None:
            self.on_finished(summary)

    def start(self, entries, **settings):
        return self.controller.start(entries, mode=settings.get("mode", "original"),
                                     target_ppi=settings.get("target_ppi", 600), cache=settings.get("cache", self.cache))

    def wait(self, count=1):
        self.qtbot.waitUntil(lambda: len(self.summaries) >= count, timeout=5000)
        return self.summaries[count-1]

    def cleanup(self):
        if shiboken6.isValid(self.controller):
            self.controller.close()
        for task in self.tasks:
            task.cancel()
        for blocker in self.blockers:
            blocker.set()
        self.launch_held()
        assert self.pool.waitForDone(5000), "Isolated preparation pool did not drain"
        if shiboken6.isValid(self.controller) and self.controller.is_busy:
            self.qtbot.waitUntil(lambda: not self.controller.is_busy, timeout=3000)
        assert not ({id(runner) for runner in self.runners} & AsyncTaskRunner.INSTANCES.keys())


@pytest.fixture
def harness(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path)
    try:
        yield h
    finally:
        h.cleanup()


class Doubles:
    def __init__(self, owner):
        self.owner = owner
        self.loads, self.preparations, self.worker_threads = [], [], []
        self.paths = {}
        self.outcome = "original"

    def load(self, source):
        assert threading.get_ident() != self.owner
        self.worker_threads.append(threading.get_ident())
        self.loads.append(source)
        payload = source.custom_bytes if source.original_path is None else self.paths[str(source.original_path)]
        return SourceSnapshot(source.image_key, hashlib.sha256(payload).hexdigest(), payload, (13, 17),
                              source.canonical_dimensions, "png", "synthetic-decode-v1", "synthetic-qt")

    def prepare(self, source, plan, *, mode, cache):
        assert threading.get_ident() != self.owner
        self.worker_threads.append(threading.get_ident())
        self.preparations.append(source.identity)
        return PreparedImage(source.identity, tuple(source.canonical_dimensions), b"prepared:"+source.encoded_bytes,
                             "png", source.native_dimensions, plan.target_ppi, plan.target_dimensions, self.outcome)


@pytest.fixture
def doubles(harness, monkeypatch):
    d = Doubles(harness.owner)
    monkeypatch.setattr(batch, "load_source_snapshot", d.load)
    monkeypatch.setattr(batch, "prepare_image", d.prepare)
    return d


def test_freeze_input_collection_and_metadata_without_owner_work(harness, doubles):
    dimensions = [745, 1040]
    payload = b"immutable shared payload"
    source = request(payload, canonical=dimensions)
    entries = [[1, source]]
    harness.hold = True
    with patch.object(Path, "resolve", side_effect=AssertionError("No path resolution")):
        harness.start(entries)
    assert not doubles.loads and not doubles.preparations
    assert harness.tasks[0]._entries[0].source.custom_bytes is payload
    dimensions[0] = 999
    entries[0][0] = 99
    entries.append((2, request(b"new source")))
    harness.launch_held()
    summary = harness.wait()
    assert summary.total_occurrences == summary.resolved_occurrences == 1
    assert harness.events[0].occurrence_ids == (1,)
    assert doubles.loads[0].canonical_dimensions == (745, 1040)


@pytest.mark.parametrize("setting,value", [("mode", "neural"), ("target_ppi", True), ("target_ppi", 600.0), ("cache", None)])
def test_invalid_settings_reject_before_submission(harness, doubles, setting, value):
    with pytest.raises((TypeError, ValueError)):
        harness.start([(1, request())], **{setting: value})
    assert not harness.tasks and not doubles.loads and not harness.controller.is_busy


@pytest.mark.parametrize("entries", [[(1, request()), (1, request())], [(True, request())], [(1, object())]])
def test_invalid_occurrences_and_sources_reject(harness, entries):
    with pytest.raises((TypeError, ValueError)):
        harness.start(entries)
    assert not harness.tasks and not harness.controller.is_busy


def test_empty_batch_has_one_terminal_and_existing_runner_cleanup(harness, doubles):
    batch_id = harness.start([])
    summary = harness.wait()
    assert summary == batch.BatchSummary(batch_id, "completed", 0, 0, 0, 0)
    harness.qtbot.waitUntil(lambda: harness.spies[0]["task_deleted"].count() == 1, timeout=3000)
    assert harness.spies[0]["terminal"].count() == harness.spies[0]["task_completed"].count() == 1
    assert harness.spies[0]["task_begins"].at(0)[0] == 0
    assert not doubles.loads and not doubles.preparations


def test_exact_locator_and_shared_custom_bytes_group_once_and_progress_counts_occurrences(harness, doubles):
    blob = b"shared custom bytes"
    harness.start([(i, request(blob)) for i in range(9)])
    summary = harness.wait()
    assert len(doubles.loads) == len(doubles.preparations) == 1
    assert harness.events[0].occurrence_ids == tuple(range(9))
    assert summary.resolved_occurrences == 9 and summary.unique_prepared_results == 1
    assert [p.resolved_occurrences for p in harness.progresses] == [9]
    assert harness.spies[0]["set_progress"].at(0) == [9]


def test_equal_separate_bytes_alias_but_different_bytes_do_not_merge(harness, doubles):
    a = b"custom payload A not interned"
    equal_a = bytes(bytearray(a))
    b = b"custom payload B not interned"
    assert a == equal_a and a is not equal_a
    assert request(a) == request(b)  # M24 equality intentionally excludes custom bytes.
    harness.start([(1, request(a)), (2, request(equal_a)), (3, request(b))])
    summary = harness.wait()
    assert len(doubles.loads) == 3 and len(doubles.preparations) == 2
    assert [type(event) for event in harness.events] == [batch.PreparedResult, batch.ResultAlias, batch.PreparedResult]
    assert harness.events[1].result_id == harness.events[0].result_id
    assert summary.resolved_occurrences == 3 and summary.unique_prepared_results == 2


def test_exact_path_groups_and_equal_content_locations_alias_even_uncached(harness, doubles, tmp_path):
    a, b = tmp_path / "A.png", tmp_path / "a.png"
    doubles.paths = {str(a): b"same source bytes", str(b): b"same source bytes"}
    doubles.outcome = "generated_uncached"
    harness.start([(1, request(path=a)), (2, request(path=a)), (3, request(path=b))])
    summary = harness.wait()
    assert len(doubles.loads) == 2 and len(doubles.preparations) == 1
    assert harness.events[0].occurrence_ids == (1, 2)
    assert isinstance(harness.events[1], batch.ResultAlias)
    assert summary.resolved_occurrences == 3
    # A later batch rereads, rather than freezing the path contents across batches.
    doubles.paths[str(a)] = b"changed source bytes"
    harness.start([(4, request(path=a))])
    harness.wait(2)
    assert len(doubles.loads) == 3 and len(doubles.preparations) == 2
    assert harness.events[0].prepared_image.source_identity != harness.events[-1].prepared_image.source_identity


def test_face_quality_geometry_and_content_remain_distinct(harness, doubles):
    sources = [request(), request(front=False), request(quality=ImageQuality.high_resolution),
               request(canonical=(1040, 1490)), request(b"different bytes")]
    harness.start(list(enumerate(sources)))
    summary = harness.wait()
    assert len(doubles.loads) == len(doubles.preparations) == summary.unique_prepared_results == 5
    assert len(set(doubles.worker_threads)) == 1
    print("identity/thread evidence:", harness.owner, sorted(set(doubles.worker_threads)), summary)


@pytest.mark.parametrize("field,value", [("native_dimensions", (17, 13)), ("image_format", "jpeg"),
                                         ("decode_policy", "other-policy"), ("qt_decoder_version", "other-qt")])
def test_content_alias_key_includes_inspection_metadata(harness, doubles, monkeypatch, field, value):
    payload = b"same content, independently captured locators"
    original_load = doubles.load
    def load(source):
        result = original_load(source)
        return replace(result, **{field: value}) if len(doubles.loads) == 2 else result
    monkeypatch.setattr(batch, "load_source_snapshot", load)
    harness.start([(1, request(payload)), (2, request(bytes(bytearray(payload))))])
    assert harness.wait().unique_prepared_results == 2
    assert len(doubles.loads) == len(doubles.preparations) == 2


def test_unavailable_and_fallback_are_explicit_resolved_results_without_popups(harness, doubles, monkeypatch):
    original_load = doubles.load
    monkeypatch.setattr(batch, "load_source_snapshot", lambda source:
                        SourceUnavailable("missing_file", "synthetic missing") if source.custom_bytes == b"missing" else original_load(source))
    doubles.outcome = "fallback_original"
    harness.start([(1, SourceUnavailable("unsupported_composite", "captured")),
                   (2, request(b"missing")), (3, request(b"available")), (4, request(b"after fallback"))])
    summary = harness.wait()
    assert summary.status == "completed" and summary.resolved_occurrences == 4
    assert summary.unavailable_occurrences == 2 and summary.unique_prepared_results == 2
    assert [e.prepared_image.outcome for e in harness.events if isinstance(e, batch.PreparedResult)] == ["fallback_original"]*2
    assert harness.spies[0]["error_occurred"].count() == 0


def real_request(*, high_depth=False):
    from tests.test_image_processing_pillow import encoded_image, patterned_image
    image = patterned_image().convertToFormat(QImage.Format.Format_RGBA64) if high_depth else patterned_image()
    return request(encoded_image(image))


def test_real_original_with_pillow_blocked_and_no_cache_access(harness):
    import builtins
    import_original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("Optional Pillow is unavailable")
        return import_original(name, *args, **kwargs)
    source = real_request()
    with patch.object(builtins, "__import__", side_effect=blocked), \
            patch.object(DerivativeCache, "lookup", side_effect=AssertionError("No Original lookup")), \
            patch.object(DerivativeCache, "store", side_effect=AssertionError("No Original write")), \
            patch("PySide6.QtGui.QPixmap", side_effect=AssertionError("No worker QPixmap")):
        harness.start([(1, source)])
        assert harness.wait().status == "completed"
    image = harness.events[0].prepared_image
    assert image.outcome == "original" and image.encoded_bytes == source.custom_bytes
    assert image.actual_dimensions == (37, 53)


@pytest.mark.parametrize("target,dimensions", [(600, (1490, 2080)), (1200, (2980, 4160))])
def test_real_cold_and_warm_lanczos(harness, target, dimensions):
    from mtg_proxy_printer.image_processing_pillow import PillowRuntime
    source = real_request()
    harness.start([(1, source)], mode="lanczos", target_ppi=target)
    assert harness.wait().status == "completed"
    first = harness.events[0].prepared_image
    assert first.outcome == "generated_cached" and first.actual_dimensions == dimensions
    with patch.object(PillowRuntime, "produce_png", side_effect=AssertionError("No warm processing")):
        harness.start([(2, source)], mode="lanczos", target_ppi=target)
        assert harness.wait(2).status == "completed"
    second = harness.events[-1].prepared_image
    assert second.outcome == "cache_hit" and second.encoded_bytes == first.encoded_bytes
    print("real service reuse:", target, first.outcome, second.outcome, first.actual_dimensions)


def test_real_fallback_and_uncached_success_alias(harness):
    from mtg_proxy_printer import image_derivative_cache
    source = real_request()
    duplicate = replace(source, custom_bytes=bytes(bytearray(source.custom_bytes)))
    assert source.custom_bytes is not duplicate.custom_bytes
    with patch.object(image_derivative_cache, "_write_atomic", side_effect=PermissionError("Synthetic M27 denial")):
        harness.start([(1, source), (2, duplicate), (3, real_request(high_depth=True))], mode="lanczos")
        summary = harness.wait()
    assert summary.status == "completed" and summary.resolved_occurrences == 3 and summary.unique_prepared_results == 2
    assert harness.events[0].prepared_image.outcome == "generated_uncached"
    assert isinstance(harness.events[1], batch.ResultAlias)
    assert harness.events[2].prepared_image.outcome == "fallback_original"
    assert harness.events[2].prepared_image.reason == "unsupported_source"
    print("real uncached/alias/fallback:", [type(e).__name__ for e in harness.events], summary)
