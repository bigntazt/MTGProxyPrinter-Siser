"""Small-raster preview budgets, outcome accounting and generation-aware status."""
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch
import weakref
import gc

import pytest
from PySide6.QtGui import QPixmap

from mtg_proxy_printer.async_tasks.image_preparation import BatchProgress, PreparedResult, ResultAlias, UnavailableResult
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.image_source import SourceIdentity, SourceUnavailable
from mtg_proxy_printer.page_scene import prepared_raster_session as module
from tests.page_scene.test_prepared_raster_session_lifecycle import ControlledController, settle, begin
from tests.page_scene.test_prepared_scene import ordinary
from tests.page_scene.test_prepared_pixmap import prepared
from mtg_proxy_printer.model.document_page import PageColumns


@pytest.fixture
def factory(qtbot, document_light, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ImagePreparationController", ControlledController)
    document_light.apply(ActionAddCard(ordinary(), 3))
    sessions = []
    def make(budget=None):
        session = module.PreparedPageSession(document_light, lambda task: None, tmp_path / "sources",
                                             DerivativeCache(tmp_path / "derived"), max_raster_bytes=budget)
        sessions.append(session)
        return session
    yield make
    for session in sessions:
        session.close()
        if session._controller.is_busy:
            session._controller.finish("cancelled")
    settle()


def value(session, dimensions=(3, 5), **changes):
    source = session._controller.starts[-1][0][0].source
    return prepared(dimensions=dimensions, source_identity=SourceIdentity(source.image_key, "a"*64), **changes)


def resolve(session, row=0):
    document = session._document
    return session.resolve(document.index(row, PageColumns.Image, document.get_current_page_index()))


@pytest.mark.parametrize("budget,accepted", [(60, True), (59, False), (0, False), (None, True)])
def test_exact_budget_and_predecode_rejection(factory, monkeypatch, budget, accepted):
    session = factory(budget)
    begin(session)
    decoder = module.decode_prepared_pixmap
    calls = []
    def decode(prepared):
        calls.append(1)
        return decoder(prepared)
    monkeypatch.setattr(module, "decode_prepared_pixmap", decode)
    session._controller.deliver(PreparedResult(1, (0, 1, 2), value(session)))
    assert len(calls) == int(accepted)
    assert session._storage.active.raster_bytes == (60 if accepted else 0)
    session._controller.finish()
    settle()
    status = session.status()
    assert (resolve(session) is not None) == accepted
    assert dict(status.completion.outcome_counts) == ({"original": 3} if accepted else {"standard": 3})
    if not accepted:
        assert status.diagnostics[0].reason == "preview_memory_limit"


def test_unique_result_charging_aliases_and_partial_publication(factory):
    session = factory(60)
    begin(session)
    controller = session._controller
    controller.deliver(PreparedResult(1, (0,), value(session)))
    controller.deliver(ResultAlias(1, (1,)))
    assert session._storage.active.raster_bytes == 60
    controller.deliver(PreparedResult(2, (2,), value(session)))
    assert session._storage.active.raster_bytes == 60
    assert all(resolve(session, row) is None for row in range(3))
    controller.finish()
    settle()
    assert resolve(session) is resolve(session, 1) is not None and resolve(session, 2) is None
    completion = session.status().completion
    assert completion.summary.resolved_occurrences == 3 and completion.published_occurrences == 2
    assert completion.total_page_occurrences == 3
    assert dict(completion.outcome_counts) == {"original": 2, "standard": 1}


def test_rejected_result_and_alias_remain_addressable_without_charge(factory, monkeypatch):
    session = factory(60)
    begin(session)
    decoder = module.decode_prepared_pixmap
    calls = []
    def decode(prepared):
        calls.append(1)
        return decoder(prepared)
    monkeypatch.setattr(module, "decode_prepared_pixmap", decode)
    c = session._controller
    c.deliver(PreparedResult(1, (0,), value(session, (4, 4))))  # 64 > 60, no decode/charge.
    c.deliver(ResultAlias(1, (1,)))
    c.deliver(PreparedResult(2, (2,), value(session)))
    assert len(calls) == 1 and session._storage.active.raster_bytes == 60
    c.finish()
    settle()
    status = session.status()
    assert dict(status.completion.outcome_counts) == {"original": 1, "standard": 2}
    assert status.diagnostics[0].occurrence_ids == (0, 1)


def test_greater_postdecode_depth_rejects_without_copy_or_retained_charge(factory, monkeypatch):
    session = factory(60)
    begin(session)
    with patch.object(QPixmap, "depth", return_value=64), \
            patch.object(QPixmap, "toImage", side_effect=AssertionError("Accounting copied pixels")):
        session._controller.deliver(PreparedResult(1, (0,), value(session)))  # Estimate60, actual120.
        session._controller.deliver(ResultAlias(1, (1,)))
        assert session._storage.active.raster_bytes == 0
        session._controller.deliver(PreparedResult(2, (2,), value(session, (1, 1))))
        assert session._storage.active.raster_bytes == 8
    session._controller.finish()
    settle()
    assert resolve(session) is None and resolve(session, 2) is not None


@pytest.mark.parametrize("dimensions", [(True, 5), (3, 0), (-1, 5), (3,), ("3", 5), None])
def test_invalid_dimensions_keep_invalid_metadata_handling(factory, monkeypatch, dimensions):
    session = factory(0)
    begin(session)
    monkeypatch.setattr(module, "decode_prepared_pixmap", lambda value: pytest.fail("Invalid metadata decoded"))
    session._controller.deliver(PreparedResult(1, (0, 1, 2), replace(value(session), actual_dimensions=dimensions)))
    session._controller.finish()
    settle()
    assert session.status().diagnostics[0].reason == "invalid_metadata"
    assert session.status().completion.published_occurrences == 0


@pytest.mark.parametrize("budget,error", [(True, TypeError), (1.5, TypeError), ("60", TypeError), ({}, TypeError), (-1, ValueError)])
def test_budget_validation(factory, budget, error):
    with pytest.raises(error, match="budget"):
        factory(budget)


def test_budget_resets_with_batch_and_preserves_draining_representation(factory):
    session = factory(60)
    begin(session)
    c = session._controller
    c.deliver(PreparedResult(1, (0, 1, 2), value(session)))
    retained = weakref.ref(session._storage.active.results[1])
    session.refresh()
    assert retained() is not None and session._storage.active.raster_bytes == 60
    c.finish("cancelled")
    gc.collect()
    assert retained() is None
    settle()
    assert session._storage.active.raster_bytes == 0
    c.deliver(PreparedResult(1, (0, 1, 2), value(session)))
    c.finish()
    settle()
    assert session.status().completion.published_occurrences == 3


def test_status_metadata_precedes_notifications_and_invalidates_reentrantly(factory):
    session = factory()
    assert session.status().phase == "off"
    begin(session)
    assert session.status().phase == "preparing"
    received = []
    session.progress.connect(lambda event: received.append(session.status()))
    progress = BatchProgress(1, 3, 1, 1, 0)
    session._controller.progress.emit(progress)
    assert received[-1].progress is progress
    session.refresh()
    queued = session.status()
    assert queued.phase == "queued" and queued.progress is queued.completion is None and queued.diagnostics == ()
    session.progress.connect(lambda event: session.disable())
    session._controller.progress.emit(progress)  # Old generation suppressed.
    assert session.status().phase == "queued"
    session._controller.finish("cancelled")
    settle()
    session._controller.progress.emit(BatchProgress(2, 3, 1, 1, 0))
    status = session.status()
    assert status.phase == "off" and status.progress is status.completion is None
    assert status.diagnostics == ()
    with pytest.raises(FrozenInstanceError):
        status.phase = "ready"


def test_outcomes_count_aliases_skipped_and_deferred_fallback_reasons(factory):
    session = factory()
    placeholder = ordinary()
    placeholder.scryfall_id = placeholder.image_uri = ""
    session._document.apply(ActionAddCard(placeholder))
    begin(session)
    c = session._controller
    c.deliver(PreparedResult(1, (0,), value(session, outcome="fallback_original", reason="provider_unavailable", detail="Pillow unavailable")))
    c.deliver(ResultAlias(1, (1,)))
    c.deliver(PreparedResult(2, (2,), value(session, outcome="generated_uncached", reason="cache_write_failed", detail="Read-only cache")))
    assert not session.status().diagnostics and session.status().completion is None
    observed = []
    session.diagnostic.connect(lambda d: observed.append(session.status()))
    c.finish()
    settle()
    status = session.status()
    assert status.phase == "ready"
    assert status.completion.total_page_occurrences == 4
    assert dict(status.completion.outcome_counts) == {"fallback_original": 2, "generated_uncached": 1, "skipped": 1}
    assert status.diagnostics[0].occurrence_ids == (0, 1)
    assert status.diagnostics[0].detail == "Pillow unavailable"
    assert status.diagnostics[1].detail == "Read-only cache"
    assert observed[0].diagnostics[0] is status.diagnostics[0]
    assert observed[0].completion is status.completion


def test_completion_is_applying_before_queued_scene_notification(factory):
    session = factory()
    begin(session)
    phases = []
    session.completed.connect(lambda completion: phases.append(session.status().phase))
    session.rasters_changed.connect(lambda: phases.append(session.status().phase))
    session._controller.deliver(PreparedResult(1, (0, 1, 2), value(session)))
    session._controller.finish()
    settle()
    assert phases == ["applying", "ready"]
