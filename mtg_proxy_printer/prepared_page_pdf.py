"""Prepare a single PDF using the shared current-page capture contract."""
from dataclasses import dataclass
import sys

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter

from mtg_proxy_printer.prepared_page_output import _capture_prepared_page_output, require_ready_status
from mtg_proxy_printer.page_scene.prepared_raster_session import SessionDiagnostic
from mtg_proxy_printer.model.imagedb_files import ImageKey
from mtg_proxy_printer.print import _render_export_page
from mtg_proxy_printer.units_and_sizes import RESOLUTION


@dataclass(frozen=True)
class PreparedPagePDF:
    data: bytes
    page_number: int
    source_path: str | None
    generation: int
    mode: str
    target_ppi: int
    outcome_counts: tuple[tuple[str, int], ...]
    diagnostics: tuple[SessionDiagnostic, ...]
    image_keys: tuple[ImageKey, ...]
    db_path: str


def capture_prepared_page_pdf(document, session, *, mode, target_ppi, landscape_workaround=False):
    data, metadata, _ = _capture_prepared_page_output(
        document, session, mode=mode, target_ppi=target_ppi,
        renderer=lambda scene, geometry: _render_pdf(scene, geometry, landscape_workaround))
    return PreparedPagePDF(data=data, **vars(metadata))


def _render_pdf(scene, geometry, landscape_workaround):
    buffer = QBuffer()
    writer = painter = None
    try:
        dimensions = (geometry.sheet_width_mm, geometry.sheet_height_mm)
        if landscape_workaround and dimensions[0] > dimensions[1]:
            dimensions = dimensions[::-1]
        paper = QPageSize(QSizeF(*dimensions), QPageSize.Unit.Millimeter, "", QPageSize.SizeMatchPolicy.ExactMatch)
        if not paper.isValid() or not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError("Cannot open prepared PDF memory output")
        writer = QPdfWriter(buffer)
        dpi = round(RESOLUTION.magnitude)
        writer.setResolution(dpi)
        if not writer.setPageLayout(QPageLayout(paper, QPageLayout.Orientation.Portrait,
                                               QMarginsF(), QPageLayout.Unit.Millimeter)):
            raise RuntimeError("Prepared PDF rejected the paper layout")
        emitted = writer.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
        if any(abs(actual-wanted) > 25.4/72 for actual, wanted in zip((emitted.width(), emitted.height()), dimensions)):
            raise RuntimeError("Prepared PDF paper differs from nominal dimensions")
        painter = QPainter()
        if not painter.begin(writer):
            raise RuntimeError("Cannot start prepared PDF painter")
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering)
        _render_export_page(scene, painter, dpi, landscape_workaround)
        if not painter.end():
            raise RuntimeError("Cannot finalize prepared PDF painter")
        painter = None
        writer = None  # Writer finalizes while the QBuffer is alive.
        return bytes(buffer.data()), ()
    finally:
        primary_failure = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if painter is not None and painter.isActive():
                painter.end()
        except Exception as error:
            cleanup_error = error
        painter = writer = None
        try:
            buffer.close()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None and not primary_failure:
            raise cleanup_error
