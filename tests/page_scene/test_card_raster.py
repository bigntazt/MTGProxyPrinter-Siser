"""M23: native image detail must not become physical scene geometry."""
import gc
import hashlib
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QBuffer, QIODevice, QPersistentModelIndex
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene

from mtg_proxy_printer.model.card import CardCorner, CheckCard, CustomCard
from mtg_proxy_printer.model.page_layout import PageLayoutSettings
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage
from mtg_proxy_printer.page_scene.card_raster import PreparedCardRaster
from mtg_proxy_printer.page_scene.items import CardItem, RenderLayers
from mtg_proxy_printer.page_scene.page_scene import PageScene, RenderMode
from mtg_proxy_printer.units_and_sizes import CardSizes, unit_registry
from tests.helpers import create_card

mm = unit_registry.mm
COLORS = (QColor("#c94b43"), QColor("#389871"), QColor("#3b65ba"), QColor("#d5ad38"))


def patterned(size, scale=1, colors=COLORS):
    w, h = size.as_qsize_px().toTuple()
    image = QImage(w*scale, h*scale, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(scale, scale)
    for color, rect in zip(colors, ((0, 0, w/2, h/2), (w/2, 0, w/2, h/2),
                                    (0, h/2, w/2, h/2), (w/2, h/2, w/2, h/2))):
        painter.fillRect(QRectF(*rect), color)
    # Distinct interior content, not only flat-color bounds.
    painter.setPen(QColor("#202020"))
    for x in range(60, w-40, 53):
        painter.drawLine(x, 80, x+17, h-80)
    painter.end()
    return QPixmap.fromImage(image)


def pixels(pixmap):
    return hashlib.sha256(bytes(pixmap.toImage().constBits())).hexdigest()


def card_with_pattern(size=CardSizes.REGULAR, name="Pattern"):
    card = create_card(name, size)
    card.set_image_file(patterned(size))
    return card


def item_for(document, card, *, raster=None, parent=None):
    document.apply(ActionAddCard(card))
    page = document.get_current_page_index()
    index = document.index(document.rowCount(page)-1, PageColumns.Image, page)
    return CardItem(index, document, parent, raster=raster)


def assert_rect(actual, width, height):
    assert (actual.x(), actual.y(), actual.width(), actual.height()) == pytest.approx(
        (0, 0, width, height), abs=1e-8, rel=0)


def sample_color(pixmap, corner):
    sample = pixmap.copy(QRect(
        QPoint(round(pixmap.width()*corner.value[0]), round(pixmap.height()*corner.value[1])),
        QSize(10, 10)))
    return sample.scaledToWidth(1, Qt.TransformationMode.SmoothTransformation).toImage().pixelColor(0, 0)


@pytest.mark.parametrize("size,scale", [(size, scale) for size in CardSizes for scale in (1, 2, 4)])
def test_native_pixels_and_trim_are_independent(document_light, size, scale):
    card = card_with_pattern(size)
    source = patterned(size, scale)
    before = pixels(source), source.size(), source.devicePixelRatio()
    item = item_for(document_light, card, raster=source)
    width, height = size.as_qsize_px().toTuple()
    assert item.card_pixmap_item.pixmap().size() == QSize(width*scale, height*scale)
    assert item.card_pixmap_item.pixmap().devicePixelRatio() == 1
    assert pixels(item.card_pixmap_item.pixmap()) == before[0]
    assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), width, height)
    assert item.transform().isIdentity() and item.scale() == 1
    assert item._raster.style.size() == size.as_qsize_px()
    assert all(corner.corner_length == (50 if card.is_oversized else 32) for corner in item.bleeds[4:])
    assert all(corner.rect().size().toTuple() == (50, 50) for corner in item.corners)
    assert (pixels(source), source.size(), source.devicePixelRatio()) == before


def test_canonical_preparation_never_resamples(qtbot):
    card = card_with_pattern()
    source = card.image_file
    with patch.object(QPixmap, "scaled", side_effect=AssertionError("Canonical raster resampled")):
        prepared = PreparedCardRaster.from_card(card)
    assert prepared.style.cacheKey() == prepared.artwork.cacheKey()
    assert pixels(prepared.artwork) == pixels(source)


def test_dpr_and_positional_transformed_parent_do_not_change_local_geometry(document_light):
    card = card_with_pattern()
    source = patterned(CardSizes.REGULAR, 2)
    source.setDevicePixelRatio(2.5)
    before = pixels(source), source.size(), source.devicePixelRatio()
    parent = QGraphicsRectItem()
    parent.setPos(47, 23)
    parent.setRotation(11)
    parent.setScale(1.3)
    document_light.page_layout.watermark_angle = 17*unit_registry.degree
    item = item_for(document_light, card, raster=source, parent=parent)
    assert item.parentItem() is parent
    assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)
    assert (pixels(source), source.size(), source.devicePixelRatio()) == before
    assert item._raster.artwork.devicePixelRatio() == 1
    assert item._raster.style.devicePixelRatio() == 1
    assert item.corners[0].pos() == QPointF(0, 0)
    assert item.corners[-1].pos() == QPointF(695, 990)
    assert item.watermark_item.rotation() == 17
    assert item.watermark_item.pos().x() == pytest.approx(document_light.page_layout.watermark_pos_x.to("pixel", "print").magnitude)
    assert parent.pos() == QPointF(47, 23) and parent.rotation() == 11 and parent.scale() == 1.3
    print("transformed-parent bounds:", item.boundingRect().getRect(), item.childrenBoundingRect().getRect())
    assert item.boundingRect().getRect() == pytest.approx(item.childrenBoundingRect().getRect(), abs=1e-8, rel=0)


def test_nonuniform_input_still_maps_complete_face_to_trim(document_light):
    card = card_with_pattern()
    source = QPixmap(137, 211)
    source.fill(QColor("cyan"))
    item = item_for(document_light, card, raster=source)
    assert item.card_pixmap_item.pixmap().size() == source.size()
    assert item.card_pixmap_item.transform().m11() != item.card_pixmap_item.transform().m22()
    assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)


def test_override_controls_corners_and_legacy_one_pixel_bleed_strips(document_light):
    card = card_with_pattern()
    card.image_file.fill(QColor("magenta"))
    model_key, model_pixels = card.image_file.cacheKey(), pixels(card.image_file)
    source = patterned(CardSizes.REGULAR, 4)
    item = item_for(document_light, card, raster=source)
    style = source.scaled(CardSizes.REGULAR.as_qsize_px(), Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    for corner, sharp, bleed in zip(CardCorner, item.corners, item.bleeds[4:]):
        expected = sample_color(style, corner)
        assert item._raster.corner_colors[corner] == expected
        assert sharp.brush().color() == expected
        assert bleed.brush().color() == expected
        assert expected != QColor("magenta")
    w, h = 745, 1040
    for strip, rect in zip(item.bleeds[:4], (QRect(0, 1, w, 1), QRect(0, h-1, w, 1),
                                            QRect(1, 0, 1, h), QRect(w-1, 0, 1, h))):
        assert strip.pixmap().toImage() == style.copy(rect).toImage()
    assert (card.image_file.cacheKey(), pixels(card.image_file)) == (model_key, model_pixels)


def test_canonical_decorations_and_layout_updates(document_light):
    layout = PageLayoutSettings(paper_size="A4", draw_sharp_corners=True, watermark_text="M23",
                               watermark_pos_x=9*mm, watermark_pos_y=7*mm,
                               watermark_font_size=18*unit_registry.point, watermark_angle=17*unit_registry.degree)
    document_light.page_layout = layout
    source = patterned(CardSizes.REGULAR, 2)
    item = item_for(document_light, card_with_pattern(), raster=source)
    item.bleeds.update_bleeds(top=13, bottom=17, left=19, right=23)
    assert item.bleeds.top.transform().m22() == -13
    assert item.bleeds.bottom.transform().m22() == 17
    assert item.bleeds.left.transform().m11() == -19
    assert item.bleeds.right.transform().m11() == 23
    assert item.bleeds.bottom.pos() == QPointF(0, 1040)
    assert item.bleeds.right.pos() == QPointF(745, 0)
    assert all(bleed.opacity() == 1 for bleed in item.bleeds)
    assert item.corners[-1].pos() == QPointF(695, 990)
    assert all(corner.opacity() == 1 for corner in item.corners)
    assert item.zValue() == RenderLayers.CARDS
    assert item.card_pixmap_item.zValue() == 0
    assert item.watermark_item.zValue() == RenderLayers.WATERMARK
    assert all(corner.zValue() == RenderLayers.CORNERS for corner in item.corners)
    assert all(bleed.zValue() == RenderLayers.BLEEDS for bleed in item.bleeds[:4])
    assert all(bleed.zValue() == RenderLayers.BLEEDS+.1 for bleed in item.bleeds[4:])
    layout.draw_sharp_corners = False
    layout.watermark_angle = -29*unit_registry.degree
    layout.watermark_pos_x = 12*mm
    document_light.page_layout_changed.emit(layout)
    assert all(corner.opacity() == 0 for corner in item.corners)
    assert item.watermark_item.rotation() == -29
    assert item.watermark_item.x() == pytest.approx(layout.watermark_pos_x.to("pixel", "print").magnitude)
    assert item.watermark_item.font().pointSizeF() == pytest.approx(18)
    item.bleeds.update_bleeds(0, 0, 0, 0)
    assert all(bleed.opacity() == 0 for bleed in item.bleeds)
    assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)


@pytest.mark.parametrize("kind", ["card", "custom", "check"])
def test_patterned_default_preserves_model_corner_policy_and_single_composition(document_light, kind):
    front = card_with_pattern()
    if kind == "card":
        card = front
    elif kind == "custom":
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        front.image_file.save(buffer, "PNG")
        card = CustomCard("Custom", front.set, "C1", "en", True, "", True,
                          CardSizes.REGULAR, False, buffer.data().data())
    else:
        back = card_with_pattern(name="Back")
        back.image_file = patterned(CardSizes.REGULAR, colors=tuple(reversed(COLORS)))
        card = CheckCard(front, back)
    expected_pixels = pixels(card.image_file)
    expected_colors = {corner: card.corner_color(corner) for corner in CardCorner}
    calls = []
    original_getter = CheckCard.image_file.fget
    def composed(self):
        calls.append(self)
        return original_getter(self)
    with ExitStack() as stack:
        if kind == "check":
            stack.enter_context(patch.object(CheckCard, "image_file", property(composed)))
        colors = stack.enter_context(patch.object(card, "corner_color", wraps=card.corner_color))
        item = item_for(document_light, card)
        assert colors.call_count == 4
        assert item._raster.corner_colors == expected_colors
        assert [c.brush().color() for c in item.bleeds[4:]] == list(expected_colors.values())
        assert pixels(item.card_pixmap_item.pixmap()) == expected_pixels
    if kind == "check":
        assert len(calls) == 1
        assert item._raster.corner_colors[CardCorner.TOP_LEFT] == front.corner_color(CardCorner.BOTTOM_LEFT)
        assert item._raster.corner_colors[CardCorner.BOTTOM_RIGHT] == back.corner_color(CardCorner.TOP_RIGHT)


def test_override_does_not_decode_custom_source_and_rejects_null(document_light):
    card = CustomCard("Lazy", card_with_pattern().set, "C2", "en", True, "", True,
                      CardSizes.REGULAR, False, b"intentionally not a decoded image")
    source = patterned(CardSizes.REGULAR, 2)
    with patch("mtg_proxy_printer.model.card.post_process_image", side_effect=AssertionError("Unexpected decode")):
        item = item_for(document_light, card, raster=source)
        assert "image_file" not in card.__dict__
        assert item.card_pixmap_item.pixmap().size() == source.size()
        with pytest.raises(ValueError, match="must not be null"):
            CardItem(item.index, document_light, raster=QPixmap())
        assert "image_file" not in card.__dict__


def test_override_lifetime_and_default_reconstruction(document_light):
    card = card_with_pattern()
    original = pixels(card.image_file)
    source = patterned(CardSizes.REGULAR, 2, tuple(reversed(COLORS)))
    expected = pixels(source)
    item = item_for(document_light, card, raster=source)
    del source
    gc.collect()
    assert pixels(item.card_pixmap_item.pixmap()) == expected
    assert item.index.isValid()
    rebuilt = CardItem(item.index, document_light)
    assert pixels(rebuilt.card_pixmap_item.pixmap()) == original
    assert pixels(card.image_file) == original


def test_explicit_check_raster_uses_proxy_without_composition_or_component_colors(document_light):
    from unittest.mock import PropertyMock
    check = CheckCard(card_with_pattern(), card_with_pattern(name="Back"))
    source = patterned(CardSizes.REGULAR, 2, tuple(reversed(COLORS)))
    with patch.object(CheckCard, "image_file", new_callable=PropertyMock,
                      side_effect=AssertionError("An explicit final raster must not compose faces")), \
            patch.object(check, "corner_color", side_effect=AssertionError("Override must sample its own proxy")):
        item = item_for(document_light, check, raster=source)
    for corner, fill in zip(CardCorner, item.corners):
        assert fill.brush().color() == sample_color(item._raster.style, corner)
    assert item.card_pixmap_item.pixmap().size() == source.size()


def render(scene):
    image = QImage(round(scene.width()), round(scene.height()), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering)
    scene.render(painter)
    painter.end()
    return image


def test_scene_alignment_with_test_only_raster_injection(document_light):
    document_light.page_layout = PageLayoutSettings(
        paper_size="A4", row_spacing=2*mm, column_spacing=3*mm, card_bleed=1.5*mm,
        print_registration_marks_style="Bullseye", cut_marker_style="Solid")
    cards = [card_with_pattern(name=f"Card {i}") for i in range(4)]
    document_light.apply(ActionNewPage(content=[cards]))
    scene = PageScene(document_light, RenderMode.ON_PAPER | RenderMode.FILE_EXPORT)
    scene.on_current_page_changed(QPersistentModelIndex(document_light.index(1, 0)))
    geometry = scene.geometry
    assert geometry.placements[1].column == 1 and geometry.placements[3].row == 1
    guides = [line.line() for line in scene.cut_lines]
    marks = [mark.sceneBoundingRect() for mark in scene.print_markers]
    assert len(marks) == 3
    before = render(scene)
    old = next(item for item in scene.card_items if item.index.row() == 1)
    position = old.pos()
    index = document_light.index(1, PageColumns.Image, scene.selected_page)
    scene.removeItem(old)
    item = CardItem(index, document_light, raster=patterned(CardSizes.REGULAR, 4))
    scene.addItem(item)  # Normal one-time ownership transfer, no duplicate insertion.
    item.setPos(position)
    scene.update_card_bleeds()
    assert scene.geometry == geometry
    assert len(scene.card_items) == 4
    assert [line.line() for line in scene.cut_lines] == guides
    assert [mark.sceneBoundingRect() for mark in scene.print_markers] == marks
    assert_rect(item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect()), 745, 1040)
    for child in scene.card_items:
        trim = geometry.placements[child.index.row()].trim_px
        assert child.pos() == QPointF(trim.x, trim.y)
    after = render(scene)
    for rect in marks:
        assert before.copy(rect.toAlignedRect()) == after.copy(rect.toAlignedRect())
    assert cards[1].image_file.size() == CardSizes.REGULAR.as_qsize_px()


@pytest.mark.parametrize("size", list(CardSizes))
def test_construction_group_bounds_match_children_at_every_resolution(document_light, size):
    scene = QGraphicsScene()
    retained = []
    measurements = []
    card = card_with_pattern(size)
    width, height = size.as_qsize_px().toTuple()
    for scale in (1, 2, 4):
        source = patterned(size, scale)
        expected_pixels = pixels(source)
        item = item_for(document_light, card, raster=source)
        scene.addItem(item)  # One ordinary scene insertion; group refresh is internal.
        retained.append(item)
        del source
        gc.collect()
        assert item.scene() is scene
        assert item.card_pixmap_item.parentItem() is item
        assert item.card_pixmap_item.pixmap().size() == QSize(width*scale, height*scale)
        assert pixels(item.card_pixmap_item.pixmap()) == expected_pixels
        artwork = item.card_pixmap_item.mapRectToParent(item.card_pixmap_item.boundingRect())
        assert_rect(artwork, width, height)
        measurements.append((item.boundingRect().getRect(), item.childrenBoundingRect().getRect()))
        print(size.name, scale, "group/children/artwork:", *measurements[-1], artwork.getRect())
    for group, children in measurements:
        assert group == pytest.approx(children, abs=1e-8, rel=0)
        assert group == pytest.approx(measurements[0][0], abs=1e-8, rel=0)
    assert len(scene.items()) >= len(retained)
