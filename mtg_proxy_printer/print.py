#  Copyright © 2020-2026  Thomas Hess <thomas.hess@udo.edu>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.


from functools import partial
import math
from pathlib import Path
import typing

try:
    from os import process_cpu_count
except ImportError:  # Py <3.13 compatibility
    from os import cpu_count as process_cpu_count

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QMarginsF, QSizeF, Signal, QSize, Slot, QPersistentModelIndex, QThreadPool, QRectF, Qt
from PySide6.QtGui import QPainter, QPdfWriter, QPageSize, QImage, QColor, QPageLayout
from PySide6.QtPrintSupport import QPrinter


if typing.TYPE_CHECKING:
    from mtg_proxy_printer.ui.main_window import MainWindow
    from mtg_proxy_printer.ui.dialogs import SavePDFDialog

from mtg_proxy_printer.async_tasks.base import AsyncTask
from mtg_proxy_printer.units_and_sizes import RESOLUTION, distance_to_px, unit_registry, PageType
import mtg_proxy_printer.meta_data
from mtg_proxy_printer.settings import settings
from mtg_proxy_printer.model.document import Document
from mtg_proxy_printer.model.page_geometry import build_page_geometry, PageGeometry
from mtg_proxy_printer.page_scene.page_scene import RenderMode, PageScene
from mtg_proxy_printer.logger import get_logger
import mtg_proxy_printer.units_and_sizes
logger = get_logger(__name__)
del get_logger

RenderHint = QPainter.RenderHint
Format = QImage.Format

__all__ = [
    "export_pdf",
    "create_printer",
    "Renderer",
    "PNGRenderer",
]

PNGEncoderThreadLimit = max(1, process_cpu_count()-1)


def _render_export_page(scene: PageScene, painter: QPainter, output_dpi: int, rotate_landscape: bool = False):
    """Map nominal physical paper into device units without Qt fitting."""
    scene.require_geometry_ready()
    geometry = scene.geometry
    source = QRectF(0, 0,
                    distance_to_px(geometry.sheet_width_mm * unit_registry.mm),
                    distance_to_px(geometry.sheet_height_mm * unit_registry.mm))
    painter.save()
    try:
        scale = output_dpi / RESOLUTION.magnitude
        painter.scale(scale, scale)
        if rotate_landscape and geometry.sheet_width_mm > geometry.sheet_height_mm:
            painter.translate(source.height(), 0)
            painter.rotate(90)
        scene.render(painter, target=source, source=source,
                     aspectRatioMode=Qt.AspectRatioMode.IgnoreAspectRatio)
    finally:
        painter.restore()


class PNGRenderer(AsyncTask):
    def __init__(self, main_window: "MainWindow|None", document: Document, file_path: str):
        super().__init__(main_window)
        self.document = document
        self.file_path = Path(file_path)
        self.page_count = document.rowCount()
        self.completed = 0
        self._output_dpi = round(RESOLUTION.magnitude)

    def run(self):
        document = self.document
        file_path = self.file_path
        page_count = self.page_count
        if not page_count:  # No pages in document
            logger.error("Tried to export a document with zero pages. Aborting.")
            self.task_completed.emit()
            return
        logger.info(f'Exporting document with {document.rowCount()} pages as PNG image sequence to "{file_path}"')
        pool = QThreadPool(self, maxThreadCount=PNGEncoderThreadLimit)
        dots_per_meter = round(self._output_dpi / 0.0254)
        background_color = settings["export"].get_color("png-background-color")
        number_width = len(str(page_count))
        self.task_begins.emit(page_count, self.tr("Export as PNGs:", "Progress bar label text"))
        locked = False
        try:
            self.ui_lock_acquire.emit()
            locked = True
            scene = PageScene(document, RenderMode.ON_PAPER | RenderMode.FILE_EXPORT, self)
            for page_nr in range(page_count):
                scene.on_current_page_changed(QPersistentModelIndex(document.index(page_nr, 0)))
                scene.require_geometry_ready()
                geometry = scene.geometry
                page_size = QSize(round(geometry.sheet_width_mm * self._output_dpi / 25.4),
                                  round(geometry.sheet_height_mm * self._output_dpi / 25.4))
                image = self._create_image(page_size, background_color, dots_per_meter)
                painter = QPainter()
                try:
                    if not painter.begin(image):
                        raise RuntimeError(f"Cannot start PNG painter for page {page_nr + 1}")
                    painter.setRenderHint(RenderHint.LosslessImageRendering, True)
                    _render_export_page(scene, painter, self._output_dpi)
                finally:
                    if painter.isActive():
                        painter.end()
                file_name = f"{file_path.stem}-{str(page_nr + 1).zfill(number_width)}.png"
                pool.start(partial(self._compress_single_image, image, str(file_path.parent / file_name)))
        except RuntimeError as error:
            self.error_occurred.emit(f"PNG export failed: {error}")
        finally:
            if locked:
                self.ui_lock_release.emit()
            pool.waitForDone()
            self.task_completed.emit()

    @staticmethod
    def _create_image(page_size: QSize, background_color: QColor, dots_per_meter: int):
        # 255 is solid. So avoid adding the alpha channel, if it won't be used.
        image_format = Format.Format_RGB888 if background_color.alpha() == 255 else Format.Format_RGBA8888
        image = QImage(page_size, image_format)
        image.setDevicePixelRatio(1)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        image.fill(background_color)
        return image

    def _compress_single_image(self, image: QImage, output_path: str):
        image.save(output_path, "PNG", 0)
        self.advance_progress.emit()


def export_pdf(document: Document, file_path: str, parent: "SavePDFDialog"):
    # TODO: Deprecate this and merge logic into the PDFPrinter class
    main_window = parent.parent()
    total_pages = document.rowCount()
    pages_to_print = settings["export"].getint("pdf-page-count-limit") or total_pages
    if not pages_to_print:  # No pages in document. Return now, to avoid dividing by zero
        logger.error("Tried to export a document with zero pages as a PDF. Aborting.")
        return
    logger.info(f'Exporting document with {total_pages} pages as PDF to "{file_path}"')
    total_documents = math.ceil(total_pages/pages_to_print)
    export_progress = AsyncTask()
    main_window.progress_bar_manager.add_task(export_progress)
    export_progress.task_begins.emit(
        total_pages, QApplication.translate("export_pdf", "Write PDF:", "Progress label"))
    try:
        QApplication.processEvents()
        for document_index in range(total_documents):
            logger.info(f"Creating PDF ({document_index+1}/{total_documents}) with up to {pages_to_print} pages.")
            PDFPrinter(
                document, file_path, export_progress.advance_progress, parent, document_index, pages_to_print
            ).run()
    finally:
        export_progress.task_completed.emit()
        QApplication.processEvents()


def create_printer(renderer: "Renderer") -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    layout = renderer.document.page_layout
    page_layout = layout.to_page_layout(renderer.render_mode)
    if not printer.setPageLayout(page_layout):
        logger.error(
            f"Setting page layout failed! "
            f"Layout: page_size={page_layout.pageSize().size(QPageSize.Unit.Millimeter)}, "
            f"orientation={page_layout.orientation()}, "
            f"margins={layout.margin_left, layout.margin_top, layout.margin_right, layout.margin_bottom}")
    # magnitude returns a float by default, so round to int to avoid a TypeError
    printer.setResolution(round(mtg_proxy_printer.units_and_sizes.RESOLUTION.magnitude))
    # Disable duplex printing by default
    printer.setDuplex(QPrinter.DuplexMode.DuplexNone)
    printer.setOutputFormat(QPrinter.OutputFormat.NativeFormat)
    if RenderMode.IMPLICIT_MARGINS not in renderer.render_mode:
        printer.setFullPage(True)
    return printer


class PDFPrinter(QPdfWriter):
    """
    Exports the given document to PDF.
    Can be given an optional index and length parameter to only export a chunk of the document for splitting purposes.
    """

    def __init__(self, document: Document, file_path: str, advance_signal: Signal, parent: QObject | None = None,
                 document_index: int = 0, pages_to_print: int = None):
        """
        Constructs a new PDFPrinter.
        :param document: Document to export
        :param file_path: file path for the PDF output. If pages_to_print is set and less than the total page count,
          the output file will be numbered, by appending a dash-separated numerical suffix to the file name stem.
        :param parent: Qt object parent
        :param document_index: Document sequence number. Used to compute the range of pages to be exported
        :param pages_to_print: Number of pages to export. Default value None means "all pages"
        """
        self.advance_progress = advance_signal
        self.document = document
        self.document_index = document_index
        self.pages_to_print = pages_to_print = pages_to_print or document.rowCount()
        self.landscape_workaround_enabled = settings["export"].getboolean("landscape-compatibility-workaround")
        if pages_to_print < document.rowCount():
            # Determine the number of digits required to properly sort all documents, without having to rely on
            # external support for natural sorting
            suffix_length = len(str(math.ceil(document.rowCount() / pages_to_print)))
            # Add one to the document_index for human-readable counting starting at 1
            suffix = str(document_index+1).zfill(suffix_length)
            path = Path(file_path)
            file_path = str(path.with_stem(f"{path.stem}-{suffix}"))
        super().__init__(file_path)
        self.setParent(parent)
        self.setCreator(f"{mtg_proxy_printer.meta_data.PROGRAMNAME}, v{mtg_proxy_printer.meta_data.__version__}")
        self.painter = QPainter()
        # magnitude returns a float by default, so round to int to avoid a TypeError
        self.setResolution(round(mtg_proxy_printer.units_and_sizes.RESOLUTION.magnitude))
        geometry = build_page_geometry(document.page_layout, PageType.UNDETERMINED, 0)
        page_layout = QPageLayout(self._to_page_size(geometry), QPageLayout.Orientation.Portrait,
                                  QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        if not self.setPageLayout(page_layout):
            raise RuntimeError("PDF export rejected the requested paper layout")
        requested = (geometry.sheet_width_mm, geometry.sheet_height_mm)
        if self.landscape_workaround_enabled and requested[0] > requested[1]:
            requested = requested[::-1]
        emitted = self.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
        # Qt represents the page boundary in whole points; content remains independently scaled.
        if any(abs(actual - wanted) > 25.4 / 72 for actual, wanted in
               zip((emitted.width(), emitted.height()), requested)):
            raise RuntimeError(f"PDF paper size differs from requested {requested} mm: {emitted}")
        self.scene = PageScene(document, RenderMode.ON_PAPER | RenderMode.FILE_EXPORT, self)
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _to_page_size(self, geometry: PageGeometry) -> QPageSize:
        size = QSizeF(geometry.sheet_width_mm, geometry.sheet_height_mm)
        if geometry.sheet_width_mm > geometry.sheet_height_mm and self.landscape_workaround_enabled:
            size.transpose()
        return QPageSize(size, QPageSize.Unit.Millimeter, "", QPageSize.SizeMatchPolicy.ExactMatch)

    def run(self):
        logger.info("Begin rendering PDF document.")
        try:
            if not self.painter.begin(self):
                raise RuntimeError("Cannot start PDF painter")
            self.painter.setRenderHint(RenderHint.LosslessImageRendering)
            first_index = self.document_index * self.pages_to_print
            last_index = min((self.document_index + 1) * self.pages_to_print, self.document.rowCount())
            for page_number in range(first_index, last_index):
                try:
                    self._switch_to_page(page_number)
                    _render_export_page(self.scene, self.painter, self.resolution(),
                                        self.landscape_workaround_enabled)
                except RuntimeError as error:
                    raise RuntimeError(f"PDF export failed on page {page_number + 1}: {error}") from error
                self.advance_progress.emit()
                if page_number + 1 < last_index and not self.newPage():
                    raise RuntimeError(f"Cannot start PDF page {page_number + 2}")
                QApplication.processEvents()
        finally:
            if self.painter.isActive():
                self.painter.end()
        logger.info("Writing document finished.")

    def _switch_to_page(self, page_number: int):
        """Render the given page on the internal scene"""
        index = QPersistentModelIndex(self.document.index(page_number, 0))
        self.scene.on_current_page_changed(index)


class Renderer(QObject):

    def __init__(self, document: Document, parent: QObject | None = None):
        super().__init__(parent)
        self.document = document
        self.render_mode = RenderMode.ON_PAPER
        if not settings["printer"].getboolean("borderless-printing"):
            self.render_mode |= RenderMode.IMPLICIT_MARGINS
        self.scene = PageScene(document, self.render_mode, self)

    @Slot(QPrinter)
    def print_document(self, printer: QPrinter):
        logger.info("Begin printing document.")
        landscape_workaround_enabled = settings["printer"].getboolean("landscape-compatibility-workaround")
        is_landscape_document = self.scene.width() > self.scene.height()
        painter = QPainter(printer)
        if is_landscape_document and landscape_workaround_enabled:
            painter.rotate(90)
            painter.translate(0, -self.scene.height())
            scaling = self.scene.width()/self.scene.height()
            painter.scale(scaling, scaling)
        painter.setRenderHint(RenderHint.LosslessImageRendering)
        page_count = self.document.rowCount()
        for index in range(page_count):
            logger.debug(f"Printing page {index+1}/{page_count}")
            self.scene.on_current_page_changed(QPersistentModelIndex(self.document.index(index, 0)))
            self.scene.render(painter)
            if index+1 < page_count:
                printer.newPage()
        painter.end()
        logger.info("Printing document finished.")
