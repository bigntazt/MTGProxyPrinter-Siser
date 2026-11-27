"""Shared synchronous capture, face accounting and scene lifetime for prepared output."""
from collections import Counter
from dataclasses import dataclass, replace
import sys

import shiboken6
from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtGui import QPixmap

from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI
from mtg_proxy_printer.model.document_page import PageColumns
from mtg_proxy_printer.model.card import CheckCard
from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.page_scene.page_scene import PageScene, RenderMode
from mtg_proxy_printer.page_scene.prepared_pixmap import PreparedPixmap, require_gui_thread
from mtg_proxy_printer.page_scene.prepared_raster_session import SessionDiagnostic
from mtg_proxy_printer.model.imagedb_files import ImageKey, ImageQuality
from mtg_proxy_printer.siser_registration import require_artwork_output_supported


@dataclass(frozen=True)
class CapturedPageMetadata:
    page_number: int
    source_path: str | None
    generation: int
    mode: str
    target_ppi: int
    outcome_counts: tuple[tuple[str, int], ...]
    diagnostics: tuple[SessionDiagnostic, ...]
    image_keys: tuple[ImageKey, ...]
    db_path: str


def require_ready_status(status):
    completion = status.completion
    if (not status.enabled or status.phase != "ready" or completion is None
            or completion.generation != status.generation or completion.summary.status != "completed"):
        raise RuntimeError("Preparation is not current and Ready. Refresh Image Preparation Preview and retry.")


def _capture_prepared_page_output(document, session, *, mode, target_ppi, renderer):
    """Own one synchronous captured revision through completed format encoding."""
    require_gui_thread()
    status = session.status()
    require_ready_status(status)
    if mode not in ("original", "lanczos", "neural") or type(target_ppi) is not int or target_ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("Invalid preview options")
    if (mode, target_ppi) != (status.completion.mode, status.completion.target_ppi):
        raise ValueError("Preview options do not match the completed preparation generation")
    page_index = QPersistentModelIndex(document.get_current_page_index())
    if not page_index.isValid() or page_index.model() != document or page_index.parent().isValid():
        raise RuntimeError("No valid prepared current page. Refresh preparation and retry.")
    page = document.index(page_index.row(), 0).internalPointer()
    completion = status.completion
    count = document.rowCount(page_index)
    if count != completion.total_page_occurrences:
        raise RuntimeError("Prepared page occurrence count changed. Refresh preparation and retry.")
    geometry = build_page_geometry(document.page_layout, page.page_type(), count)
    registration = document.page_layout.print_registration_marks_style
    require_artwork_output_supported(registration, geometry)
    page_number = page_index.row() + 1
    source_path = str(document.save_file_path) if document.save_file_path is not None else None
    db_path = str(document.card_db.db_path)
    captured, counts, keys = {}, Counter(), set()
    standard_keys = set(document._get_page_content_as_image_keys(page))

    def validate_state():
        current = session.status()
        require_ready_status(current)
        index = document.get_current_page_index()
        if (current.generation != status.generation or current.completion != completion
                or index != page_index or not page_index.isValid()
                or document.index(index.row(), 0).internalPointer() is not page
                or document.rowCount(index) != count
                or build_page_geometry(document.page_layout, page.page_type(), count) != geometry
                or document.page_layout.print_registration_marks_style != registration):
            raise RuntimeError("Prepared page changed during export. Refresh preparation and retry.")

    scene = None
    try:
        for row in range(count):
            index = document.index(row, PageColumns.Image, page_index)
            value = session.resolve(index)
            if value is not None:
                if (not isinstance(value, PreparedPixmap) or value.pixmap.isNull()
                        or value.pixmap.size().toTuple() != value.actual_dimensions):
                    raise RuntimeError("A published prepared raster is unusable. Refresh preparation and retry.")
                captured[QPersistentModelIndex(index)] = replace(value, pixmap=QPixmap(value.pixmap))
                counts[value.outcome] += 1
                keys.add(value.source_identity.image_key)
            else:
                card = index.data(Qt.ItemDataRole.UserRole)
                image = card.image_file
                if (not card.is_custom_card and not isinstance(card, CheckCard)
                        and image is not None and not image.isNull()
                        and image != document.image_db.get_blank(card.size)):
                    key = ImageKey(card.scryfall_id, card.is_front, ImageQuality.of_card(card))
                    if key in standard_keys:
                        keys.add(key)
        prepared_outcomes = {"original", "cache_hit", "generated_cached", "generated_uncached", "fallback_original"}
        expected = {key: value for key, value in completion.outcome_counts if key in prepared_outcomes and value}
        if (len(captured) != completion.published_occurrences or dict(counts) != expected
                or sum(value for _, value in completion.outcome_counts) != count):
            raise RuntimeError("Published prepared bindings are missing or changed. Refresh preparation and retry.")
        validate_state()
        scene = PageScene(document, RenderMode.ON_PAPER | RenderMode.FILE_EXPORT,
                          raster_resolver=lambda index: captured.get(QPersistentModelIndex(index)))
        scene.require_output_ready()
        require_artwork_output_supported(registration, scene.geometry)
        if scene.geometry != geometry:
            raise RuntimeError("Output geometry changed. Refresh preparation and retry.")
        validate_state()
        data, format_metadata = renderer(scene, geometry)
        validate_state()  # Encoding must finish before this final freshness boundary.
        metadata = CapturedPageMetadata(
            page_number, source_path, status.generation, mode, target_ppi,
            completion.outcome_counts, status.diagnostics,
            tuple(sorted(keys, key=lambda key: (key.scryfall_id, key.is_front, key.image_quality.value))), db_path)
        return data, metadata, format_metadata
    finally:
        primary_failure = sys.exc_info()[0] is not None
        try:
            if scene is not None:
                shiboken6.delete(scene)
        except Exception:
            if not primary_failure:
                raise
        finally:
            captured.clear()
