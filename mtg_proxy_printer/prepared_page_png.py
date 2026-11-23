"""Direct prepared-page RGB PNG output with an independent bounded page canvas."""
from dataclasses import dataclass
import math
import sys

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI
from mtg_proxy_printer.model.imagedb_files import ImageKey
from mtg_proxy_printer.page_scene.prepared_raster_session import SessionDiagnostic
from mtg_proxy_printer.prepared_page_output import _capture_prepared_page_output
from mtg_proxy_printer.print import _render_export_page

MAX_PAGE_RASTER_BYTES = 512 * 1024 * 1024
MAX_QT_DIMENSION = 2**31 - 1


@dataclass(frozen=True)
class PreparedPagePNG:
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
    output_ppi: int
    pixel_width: int
    pixel_height: int


def _canvas_plan(geometry, output_ppi, landscape_workaround):
    if type(output_ppi) is not int or output_ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("PNG page output PPI must be the integer 600, 900 or 1200")
    paper = (geometry.sheet_width_mm, geometry.sheet_height_mm)
    if any(not math.isfinite(axis) or axis <= 0 for axis in paper):
        raise ValueError("PNG paper dimensions must be finite and positive")
    if landscape_workaround and paper[0] > paper[1]:
        paper = paper[::-1]
    pixel_axes = tuple(axis * output_ppi / 25.4 for axis in paper)
    if any(not math.isfinite(axis) for axis in pixel_axes):
        raise ValueError("PNG canvas is outside Qt integer dimensions. Choose a lower PNG resolution or prepared PDF.")
    dimensions = tuple(round(axis) for axis in pixel_axes)
    if any(axis <= 0 or axis > MAX_QT_DIMENSION for axis in dimensions):
        raise ValueError(f"PNG canvas {dimensions[0]} × {dimensions[1]} is outside Qt's positive integer range. "
                         "Choose a lower PNG resolution or prepared PDF.")
    width, height = dimensions
    stride = ((width * 3 + 3) // 4) * 4
    estimated_bytes = stride * height
    if estimated_bytes > MAX_PAGE_RASTER_BYTES:
        raise RuntimeError(f"PNG canvas {width} × {height} at {output_ppi} PPI needs {estimated_bytes} bytes, "
                           f"above the {MAX_PAGE_RASTER_BYTES}-byte page-raster limit. "
                           "Choose a lower PNG resolution or prepared PDF.")
    return width, height, estimated_bytes


def capture_prepared_page_png(document, session, *, mode, target_ppi, output_ppi, landscape_workaround=False):
    # Validate this independent choice before capture or allocation; never coerce it.
    if type(output_ppi) is not int or output_ppi not in SUPPORTED_TARGET_PPI:
        raise ValueError("PNG page output PPI must be the integer 600, 900 or 1200")
    try:
        data, metadata, dimensions = _capture_prepared_page_output(
            document, session, mode=mode, target_ppi=target_ppi,
            renderer=lambda scene, geometry: _render_png(scene, geometry, output_ppi, landscape_workaround))
        return PreparedPagePNG(data=data, **vars(metadata), output_ppi=output_ppi,
                               pixel_width=dimensions[0], pixel_height=dimensions[1])
    except MemoryError as error:
        raise RuntimeError("Not enough memory to prepare this PNG page. Choose a lower PNG resolution or prepared PDF.") from error


def _allocate_page(width, height):
    return QImage(width, height, QImage.Format.Format_RGB888)


def _encode_png(image, buffer):
    return image.save(buffer, "PNG")


def _render_png(scene, geometry, output_ppi, landscape_workaround):
    width, height, _ = _canvas_plan(geometry, output_ppi, landscape_workaround)
    buffer = QBuffer()
    image = painter = None
    try:
        image = _allocate_page(width, height)
        if image.isNull():
            raise RuntimeError(f"Cannot allocate PNG canvas {width} × {height}. "
                               "Choose a lower PNG resolution or prepared PDF.")
        if image.sizeInBytes() > MAX_PAGE_RASTER_BYTES:
            raise RuntimeError(f"Allocated PNG canvas {width} × {height} exceeds the page-raster limit. "
                               "Choose a lower PNG resolution or prepared PDF.")
        image.setDevicePixelRatio(1)
        density = round(output_ppi / 0.0254)
        image.setDotsPerMeterX(density)
        image.setDotsPerMeterY(density)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter()
        if not painter.begin(image):
            raise RuntimeError("Cannot start prepared PNG painter")
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering)
        _render_export_page(scene, painter, output_ppi, landscape_workaround)
        if not painter.end():
            raise RuntimeError("Cannot finalize prepared PNG painter")
        painter = None
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RuntimeError("Cannot open prepared PNG memory output")
        if not _encode_png(image, buffer):
            raise RuntimeError("Cannot encode the prepared PNG page")
        return bytes(buffer.data()), (width, height)
    finally:
        primary_failure = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if painter is not None and painter.isActive():
                painter.end()
        except Exception as error:
            cleanup_error = error
        painter = image = None
        try:
            buffer.close()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None and not primary_failure:
            raise cleanup_error
