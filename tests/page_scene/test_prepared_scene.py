"""Explicit, inactive prepared-raster adoption and physical scene invariants."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
from unittest.mock import patch

import pytest
from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QPersistentModelIndex, QThreadPool, Qt
from PySide6.QtGui import QColor, QPainter, QPdfWriter, QPixmap
from PySide6.QtTest import QSignalSpy

from mtg_proxy_printer.async_tasks.base import AsyncTaskRunner
from mtg_proxy_printer.async_tasks.image_preparation import ImagePreparationController, PreparedResult, ResultAlias
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.document_controller.replace_card import ActionReplaceCard
from mtg_proxy_printer.image_derivative_cache import DerivativeCache
from mtg_proxy_printer.image_source import SourceIdentity, capture_source_request
from mtg_proxy_printer.model.card import CheckCard, CustomCard
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.page_scene.page_scene import PageScene, RenderMode
from mtg_proxy_printer.page_scene.prepared_pixmap import PreparedPixmap, decode_prepared_pixmap
from mtg_proxy_printer.units_and_sizes import CardSizes, unit_registry
from tests.page_scene.test_card_raster import patterned, pixels, card_with_pattern, render, assert_rect
from tests.page_scene.test_prepared_pixmap import prepared
from tests.test_image_source import PRINTING, encoded, write_original

mm = unit_registry.mm
MODE = RenderMode.ON_PAPER | RenderMode.FILE_EXPORT


def ordinary(size=CardSizes.REGULAR, name="Ordinary"):
    card = card_with_pattern(size, name)
    card.scryfall_id = PRINTING
    card.oracle_id = PRINTING
    card.image_uri = "https://invalid.example/synthetic"
    return card


def record(card, ppi=600):
    dimensions = card.size.as_qsize_px().toTuple()
    raster = patterned(card.size, ppi//300)
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly) and raster.save(buffer, "PNG")
    return decode_prepared_pixmap(prepared(
        bytes(buffer.data()), raster.size().toTuple(), dimensions, ppi,
        source_identity=SourceIdentity(ImageKey(card.scryfall_id, card.is_front,
                                              ImageQuality.of_card(card)), "c"*64),
        outcome="generated_uncached"))


@pytest.fixture
def scene_document(document_light):
    document_light.page_layout = PageLayoutSettings(
        paper_size="A4", row_spacing=2*mm, column_spacing=3*mm, card_bleed=1.5*mm,
        print_registration_marks_style="Bullseye", cut_marker_style="Solid",
        watermark_text="M28", watermark_angle=17*unit_registry.degree,
        watermark_color=QColor("#8d2035"), watermark_font_size=14*unit_registry.point,
        watermark_pos_x=7*mm, watermark_pos_y=10*mm,
        document_name="Synthetic scene", draw_page_numbers=True)
    return document_light


def decoration_geometry(scene):
    return (
        scene.geometry, scene.sceneRect(),
        [(line.line(), line.pos(), line.zValue()) for line in scene.cut_lines],
        [mark.sceneBoundingRect() for mark in scene.print_markers],
        [(text.pos(), text.rotation(), text.zValue()) for text in scene.text_items],
        [(item.pos(), item.watermark_item.pos(), item.watermark_item.rotation(),
          item.watermark_item.font(), item.watermark_item.zValue(),
          [(bleed.pos(), bleed.transform(), bleed.opacity()) for bleed in item.bleeds])
         for item in scene.card_items])


def select_page(document, row=1):
    document.on_ui_selects_new_page(document.index(row, 0))


@pytest.mark.parametrize("size", list(CardSizes))
def test_default_and_miss_scenes_are_pixel_identical(scene_document, size):
    d = scene_document
    d.apply(ActionNewPage(content=[[ordinary(size), ordinary(size)]]))
    select_page(d)
    plain = PageScene(d, MODE)
    assert len(plain.card_items) == 2
    default = render(plain)
    miss = PageScene(d, MODE, raster_resolver=lambda index: None)
    assert render(miss) == default
    assert decoration_geometry(plain) == decoration_geometry(miss)


@pytest.mark.parametrize("size", list(CardSizes))
@pytest.mark.parametrize("ppi", [600, 1200])
def test_native_scene_pixels_trim_and_decorations(scene_document, size, ppi):
    d = scene_document
    card = ordinary(size)
    d.apply(ActionNewPage(content=[[card]*4]))
    select_page(d)
    scene = PageScene(d, MODE)
    assert len(scene.card_items) == 4
    before = decoration_geometry(scene)
    group_bounds = [item.boundingRect() for item in scene.card_items]
    model_state = pixels(card.image_file), card.image_file.cacheKey(), tuple(d.undo_stack), tuple(d.redo_stack)
    prepared_raster = record(card, ppi)
    lookup = {row: prepared_raster for row in range(4)}
    del scene
    scene = PageScene(d, MODE, raster_resolver=lambda index: lookup.get(index.row()))
    assert decoration_geometry(scene) == before
    assert [item.boundingRect() for item in scene.card_items] == group_bounds
    for item in scene.card_items:
        assert item.card_pixmap_item.pixmap().size().toTuple() == prepared_raster.actual_dimensions
        assert pixels(item.card_pixmap_item.pixmap()) == pixels(prepared_raster.pixmap)
        width, height = size.as_qsize_px().toTuple()
        assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), width, height)
        assert item._raster.style.size() == size.as_qsize_px()
    assert (pixels(card.image_file), card.image_file.cacheKey(), tuple(d.undo_stack), tuple(d.redo_stack)) == model_state


def test_override_precedes_legacy_availability_and_misses_preserve_holes(scene_document):
    d = scene_document
    missing, present = ordinary(name="Missing"), ordinary(name="Present")
    available = record(missing)
    missing.image_file = None
    d.apply(ActionNewPage(content=[[missing, present]]))
    select_page(d)
    lookup = {}
    scene = PageScene(d, MODE, raster_resolver=lambda index: lookup.get(index.row()))
    assert [item.index.row() for item in scene.card_items] == [1]
    lookup[0] = available
    scene.refresh_card_rasters()
    assert [item.index.row() for item in scene.card_items] == [0, 1]
    assert scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == available.actual_dimensions
    assert missing.image_file is None
    lookup.clear()
    scene.refresh_card_rasters()
    assert [item.index.row() for item in scene.card_items] == [1]


@pytest.mark.parametrize("kind", ["printing", "face", "quality", "canonical", "null", "dimensions"])
def test_mismatched_record_falls_back_with_diagnostic(scene_document, kind):
    d = scene_document
    card = ordinary()
    good = record(card)
    key = good.source_identity.image_key
    if kind in ("printing", "face", "quality"):
        changes = {"printing": {"scryfall_id": "00000000-0000-0000-0000-000000000000"},
                   "face": {"is_front": False}, "quality": {"image_quality": ImageQuality.low_resolution}}[kind]
        bad = replace(good, source_identity=replace(good.source_identity, image_key=replace(key, **changes)))
    else:
        bad = replace(good, **{"canonical": {"canonical_dimensions": (1040, 1490)},
                               "null": {"pixmap": QPixmap()},
                               "dimensions": {"actual_dimensions": (77, 111)}}[kind])
    d.apply(ActionAddCard(card))
    with patch("mtg_proxy_printer.page_scene.page_scene.logger.warning") as warning:
        scene = PageScene(d, MODE, raster_resolver=lambda index: bad)
    assert warning.call_count == 1
    assert pixels(scene.card_items[0].card_pixmap_item.pixmap()) == pixels(card.image_file)
    card.image_file = None
    scene.refresh_card_rasters()
    assert not scene.card_items


def test_resolver_programming_errors_are_not_cache_misses(scene_document):
    scene_document.apply(ActionAddCard(ordinary()))
    with pytest.raises(TypeError, match="PreparedPixmap or None"):
        PageScene(scene_document, MODE, raster_resolver=lambda index: object())
    def buggy(index):
        raise ValueError("lookup bug")
    with pytest.raises(ValueError, match="lookup bug"):
        PageScene(scene_document, MODE, raster_resolver=buggy)
    with pytest.raises(TypeError, match="callable"):
        PageScene(scene_document, MODE, raster_resolver={})


def test_excluded_cards_do_not_invoke_resolver(scene_document):
    d = scene_document
    front = ordinary()
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly) and front.image_file.save(buffer, "PNG")
    custom = CustomCard("Custom", front.set, "C1", "en", True, "", True,
                        CardSizes.REGULAR, False, bytes(buffer.data()))
    check = CheckCard(front, ordinary(name="Back"))
    placeholder = card_with_pattern()
    invalid = ordinary(name="Invalid")
    invalid.scryfall_id = "invalid-printing"
    d.apply(ActionNewPage(content=[[custom, check, placeholder, invalid]]))
    select_page(d)
    normal = PageScene(d, MODE)
    assert len(normal.card_items) == 4
    baseline = render(normal)
    with patch("mtg_proxy_printer.model.card.post_process_image", side_effect=AssertionError("Masked source decoded again")):
        def forbidden(index):
            raise AssertionError("Resolver received an ineligible card")
        scene = PageScene(d, MODE, raster_resolver=forbidden)
        assert render(scene) == baseline
        scene.refresh_card_rasters()
        assert render(scene) == baseline


def test_refresh_adoption_invalidation_copy_lifetime_and_no_model_notifications(scene_document):
    d = scene_document
    card = ordinary()
    d.apply(ActionNewPage(content=[[card, card]]))
    select_page(d)
    lookup = {}
    scene = PageScene(d, MODE, raster_resolver=lambda index: lookup.get(index.row()))
    baseline = render(scene)
    state = tuple(d.undo_stack), tuple(d.redo_stack), pixels(card.image_file), card.image_file.cacheKey()
    spy = QSignalSpy(d.dataChanged)
    native = record(card)
    expected = pixels(native.pixmap)
    lookup[0] = native
    old = scene.card_items[0]
    scene.refresh_card_rasters()
    assert old not in scene.card_items
    assert scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == native.actual_dimensions
    lookup.clear()
    native.pixmap.fill(Qt.GlobalColor.magenta)  # Copy-on-write must leave the item intact.
    del native
    gc.collect()
    assert pixels(scene.card_items[0].card_pixmap_item.pixmap()) == expected
    for _ in range(4):
        scene.refresh_card_rasters()
        assert len(scene.card_items) == 2 and render(scene) == baseline
    assert spy.count() == 0
    assert (tuple(d.undo_stack), tuple(d.redo_stack), pixels(card.image_file), card.image_file.cacheKey()) == state


def test_existing_reconstruction_and_page_switch_use_current_identity(scene_document):
    d = scene_document
    card = ordinary()
    d.apply(ActionNewPage(count=2, content=[[card], [card]]))
    select_page(d)
    available = record(card)
    scene = PageScene(d, MODE, raster_resolver=lambda index: available)
    index = d.index(0, PageColumns.Image, scene.selected_page)
    old_index = QPersistentModelIndex(index)
    d.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
    assert scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == available.actual_dimensions
    scene.on_current_page_changed(QPersistentModelIndex(d.index(2, 0)))
    assert scene.card_items[0].card_pixmap_item.pixmap().size().toTuple() == available.actual_dimensions
    # A valid persistent slot can survive replacement; ImageKey must be checked again.
    replacement = ordinary(name="Different printing")
    replacement.scryfall_id = "00000000-0000-0000-0000-000000000000"
    d.apply(ActionReplaceCard(replacement, page=old_index.parent().row(), slot=old_index.row()))
    assert old_index.isValid()
    scene.on_current_page_changed(QPersistentModelIndex(old_index.parent()))
    assert scene.card_items[0].card_pixmap_item.pixmap().size() == replacement.image_file.size()


def test_refresh_pending_geometry_recovers_and_requires_gui_thread(scene_document):
    d = scene_document
    card = ordinary()
    d.apply(ActionAddCard(card))
    available = record(card)
    scene = PageScene(d, MODE, raster_resolver=lambda index: available)
    valid = d.page_layout
    d.page_layout = PageLayoutSettings(paper_size="Custom", custom_page_width=20*mm, custom_page_height=20*mm)
    scene.refresh_card_rasters()
    assert scene.geometry_pending and not scene.card_items
    d.page_layout = valid
    scene.refresh_card_rasters()
    assert not scene.geometry_pending and len(scene.card_items) == 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="GUI application's thread"):
            pool.submit(scene.refresh_card_rasters).result(timeout=10)
        with pytest.raises(RuntimeError, match="GUI application's thread"):
            pool.submit(PageScene, d, MODE, raster_resolver=lambda index: None).result(timeout=10)


def prepare_batch(qtbot, entries, ppi, cache):
    """Real M24–M27 flow. Store only decoded records; aliases reuse the accepted value."""
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    records, occurrences, events, summaries = {}, {}, [], []
    submitted = []
    def submit(task):
        # Match the established task harness: retain owner-created QObjects until
        # runner completion and queued lifecycle notifications have drained.
        submitted.append(task)
        pool.start(AsyncTaskRunner(task))
    def consume(batch_id, event):
        events.append(type(event).__name__)
        if isinstance(event, PreparedResult):
            value = decode_prepared_pixmap(event.prepared_image)
            assert isinstance(value, PreparedPixmap)
            records[event.result_id] = value
        else:
            assert isinstance(event, ResultAlias) and event.result_id in records
        for occurrence in event.occurrence_ids:
            occurrences[occurrence] = records[event.result_id]
    controller = ImagePreparationController(submit, consume)
    controller.finished.connect(summaries.append)
    try:
        controller.start(entries, mode="lanczos", target_ppi=ppi, cache=cache)
        qtbot.waitUntil(lambda: not controller.is_busy, timeout=30000)
        assert summaries[0].status == "completed", summaries[0]
        assert summaries[0].resolved_occurrences == len(entries)
        assert events == ["PreparedResult", "ResultAlias"]
        assert occurrences[0] is occurrences[1]
        return occurrences
    finally:
        controller.close()
        assert pool.waitForDone(10000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
        assert all(not task._running for task in submitted)


def assert_pdf_artwork(scene, path, native_dimensions):
    """Independent PDF evidence: full embedded pixels and composed physical CTMs."""
    from pypdf import PdfReader
    from mtg_proxy_printer.print import _render_page
    writer = QPdfWriter(str(path))
    writer.setResolution(1200)
    writer.setPageLayout(scene.document.page_layout.to_page_layout(MODE))
    writer.setTitle("M28 explicit synthetic software proof; normal export is not adopted")
    painter = QPainter(writer)
    assert painter.isActive()
    painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering)
    try:
        _render_page(scene, painter, 1200, 1200)
    finally:
        painter.end()
    del writer
    page = PdfReader(path).pages[0]
    resources = page["/Resources"]["/XObject"]
    ctm = (1, 0, 0, 1, 0, 0)
    stack, measured = [], []
    def multiply(left, right):
        a, b, c, d, e, f = left
        g, h, i, j, k, l = right
        return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)
    for operands, operation in page.get_contents().operations:
        if operation == b"q":
            stack.append(ctm)
        elif operation == b"Q":
            ctm = stack.pop()
        elif operation == b"cm":
            ctm = multiply(ctm, tuple(float(v) for v in operands))
        elif operation == b"Do":
            image = resources[operands[0]].get_object()
            if image.get("/Subtype") != "/Image" or (image["/Width"], image["/Height"]) != native_dimensions:
                continue
            if scene.card_items[0].card_pixmap_item.pixmap().hasAlphaChannel():
                mask = image["/SMask"].get_object()
                assert (mask["/Width"], mask["/Height"]) == native_dimensions
            a, b, c, d, e, f = ctm
            corners = [(e, f), (a+e, b+f), (c+e, d+f), (a+c+e, b+d+f)]
            x, y = min(p[0] for p in corners), min(p[1] for p in corners)
            measured.append((x, y, max(p[0] for p in corners)-x, max(p[1] for p in corners)-y))
    expected = []
    for item in scene.card_items:
        trim = scene.geometry.placements[item.index.row()].trim_px
        expected.append((trim.x*72/300, float(page.mediabox.height)-(trim.y+trim.height)*72/300,
                         trim.width*72/300, trim.height*72/300))
    assert len(measured) == len(expected) > 0, (measured, expected)
    for actual, target in zip(sorted(measured), sorted(expected)):
        assert actual == pytest.approx(target, abs=0.02, rel=0)
    return {"native_dimensions": native_dimensions, "artwork_placements_pt": measured,
            "expected_placements_pt": expected, "pdf_bytes": path.stat().st_size}


@pytest.mark.parametrize("ppi", [600, 1200])
def test_real_batch_to_decoder_to_scene_once(scene_document, qtbot, tmp_path, ppi):
    d = scene_document
    card = ordinary()
    d.apply(ActionNewPage(content=[[card, card]]))
    select_page(d)
    source = encoded(149, 208, color="#bf497180")
    write_original(tmp_path / "sources-a", card, source)
    write_original(tmp_path / "sources-b", card, source)
    requests = [(i, capture_source_request(card, cache_root=tmp_path / name))
                for i, name in enumerate(("sources-a", "sources-b"))]
    lookup = {}
    scene = PageScene(d, MODE, raster_resolver=lambda index: lookup.get(index.row()))
    geometry = scene.geometry
    lookup.update(prepare_batch(qtbot, requests, ppi, DerivativeCache(tmp_path / "derivatives")))
    scene.refresh_card_rasters()  # Once after the batch, never from an acceptance callback.
    assert scene.geometry == geometry
    for item in scene.card_items:
        assert item.card_pixmap_item.pixmap().size().toTuple() == (745*(ppi//300), 1040*(ppi//300))
        assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)
    assert not render(scene).isNull()
    evidence = assert_pdf_artwork(scene, tmp_path / f"prepared-{ppi}.pdf", lookup[0].actual_dimensions)
    print("explicit PDF proof:", ppi, evidence)
