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


from pathlib import Path
from functools import partial

from PySide6.QtCore import Slot, Signal, QStringListModel, QUrl, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QAction, QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget, QMainWindow, QDialog, QCheckBox
from PySide6.QtPrintSupport import QPrintDialog

from mtg_proxy_printer import BlockingQueuedConnection
from mtg_proxy_printer.async_tasks.base import AsyncTask
from mtg_proxy_printer.async_tasks.document_loader import DocumentLoader
from mtg_proxy_printer.async_tasks.card_info_downloader import ApiStreamTask, DatabaseImportTask
from mtg_proxy_printer.document_controller.compact_document import ActionCompactDocument
from mtg_proxy_printer.document_controller.page_actions import ActionNewPage, ActionRemovePage
from mtg_proxy_printer.document_controller.shuffle_document import ActionShuffleDocument
from mtg_proxy_printer.document_controller.new_document import ActionNewDocument
from mtg_proxy_printer.document_controller.card_actions import ActionAddCard
from mtg_proxy_printer.missing_images_manager import MissingImagesManager
from mtg_proxy_printer.model.carddb import CardDatabase
from mtg_proxy_printer.model.imagedb import ImageDatabase
from mtg_proxy_printer.model.document import Document
from mtg_proxy_printer.model.page_geometry import build_page_geometry
from mtg_proxy_printer.units_and_sizes import PageType
from mtg_proxy_printer.registration_profile_ids import RegistrationProfileId
from mtg_proxy_printer.cut_template_svg import serialize_cut_template_svg
from mtg_proxy_printer.calibration import render_calibration_pdf
from mtg_proxy_printer.siser_registration import require_artwork_output_supported
from mtg_proxy_printer.units_and_sizes import DEFAULT_SAVE_SUFFIX
import mtg_proxy_printer.settings
import mtg_proxy_printer.print
from mtg_proxy_printer.ui.custom_card_import_dialog import CustomCardImportDialog
from mtg_proxy_printer.ui.dialogs import SavePDFDialog, SaveDocumentAsDialog, LoadDocumentDialog, \
    AboutDialog, PrintPreviewDialog, PrintDialog, DocumentSettingsDialog, SavePNGDialog, ExportCardImagesDialog, \
    SaveCutTemplateDialog, SaveCalibrationPDFDialog
from mtg_proxy_printer.ui.common import show_wizard_or_dialog
from mtg_proxy_printer.ui.cache_cleanup_wizard import CacheCleanupWizard
from mtg_proxy_printer.ui.deck_import_wizard import DeckImportWizard
from mtg_proxy_printer.ui.progress_bar import ProgressBarManager
from mtg_proxy_printer.ui.siser_guide_dialog import SiserGuideDialog
from mtg_proxy_printer.ui.image_preparation_preview import ImagePreparationPreview
from mtg_proxy_printer.ui.neural_worker_setup import NeuralWorkerSetup
from mtg_proxy_printer.ui.prepared_page_pdf_dialog import PreparedPagePDFDialog
from mtg_proxy_printer.prepared_page_pdf import capture_prepared_page_pdf, require_ready_status
from mtg_proxy_printer.prepared_page_png import capture_prepared_page_png
from mtg_proxy_printer.ui.prepared_page_png_dialog import PreparedPagePNGDialog
from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI
from mtg_proxy_printer.page_scene.prepared_raster_session import PreparedPageSession
from mtg_proxy_printer.image_derivative_cache import DerivativeCache

try:
    from mtg_proxy_printer.ui.generated.main_window import Ui_MainWindow
except ModuleNotFoundError:
    from mtg_proxy_printer.ui.common import load_ui_from_file
    Ui_MainWindow = load_ui_from_file("main_window")

from mtg_proxy_printer.logger import get_logger

logger = get_logger(__name__)
del get_logger
__all__ = [
    "MainWindow",
]
TransformationMode = Qt.TransformationMode
StandardButton = QMessageBox.StandardButton
StandardKey = QKeySequence.StandardKey
UiElements = list[QWidget | QAction]
QueuedConnection = Qt.ConnectionType.QueuedConnection
# Counts the number of async tasks currently working on the document. Disable the UI while this is non-zero to ensure
# that data doesn't change while those work.
UI_LOCK_SEMAPHORE = 0


class MainWindow(QMainWindow):

    request_run_async_task = Signal(AsyncTask)

    def __init__(self,
                 image_db: ImageDatabase,
                 document: Document,
                 language_model: QStringListModel,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info(f"Creating {self.__class__.__name__} instance.")
        self.is_running = True
        self._show_network_errors = True
        self.ui = ui = Ui_MainWindow()
        ui.setupUi(self)
        self.setAcceptDrops(True)
        self.default_undo_tooltip = ui.action_undo.toolTip()
        self.default_redo_tooltip = ui.action_redo.toolTip()
        self.missing_images_manager = self._create_missing_images_manager(document)
        self.about_dialog = self._create_about_dialog()
        self.progress_bar_manager = self._create_progress_bar_manager()
        self.image_db = image_db
        self.document = document
        self._setup_prepared_png_actions()
        self._setup_image_preparation_preview()
        self._connect_document_signals(document)
        self._setup_web_action_signals(ui)
        self.language_model = language_model
        self._setup_central_widget()
        self._setup_undo_redo_actions(document)
        self.ui.action_show_toolbar.setChecked(mtg_proxy_printer.settings.settings["gui"].getboolean("show-toolbar"))
        self._setup_platform_dependent_default_shortcuts()
        self.current_dialog: QDialog | None = None
        self.siser_guide_dialog: SiserGuideDialog | None = None
        logger.info(f"Created {self.__class__.__name__} instance.")

    @property
    def current_dialog(self):
        return getattr(self, "_current_dialog", None)

    @current_dialog.setter
    def current_dialog(self, dialog):
        self._current_dialog = dialog
        self._update_prepared_pdf_action()

    def _prepared_pdf_available(self):
        if (not self.is_running or UI_LOCK_SEMAPHORE or self.current_dialog is not None
                or getattr(self, "preview_session", None) is None):
            return False
        try:
            require_ready_status(self.preview_session.status())
            return self.document.get_current_page_index().isValid()
        except (ValueError, RuntimeError):
            return False

    def _update_prepared_pdf_action(self):
        available = self._prepared_pdf_available()
        self.ui.action_export_prepared_page_pdf.setEnabled(available)
        self.ui.menu_export_prepared_png.setEnabled(available)
        for action in self._prepared_png_actions:
            action.setEnabled(available)

    def _setup_prepared_png_actions(self):
        self._prepared_png_actions = tuple(
            getattr(self.ui, f"action_export_prepared_png_{ppi}") for ppi in SUPPORTED_TARGET_PPI)
        for action, ppi in zip(self._prepared_png_actions, SUPPORTED_TARGET_PPI):
            action.setData(ppi)
            action.triggered.connect(lambda checked=False, action=action: self._export_prepared_page_png(action.data()))

    def _export_prepared_page_png(self, output_ppi):
        if not self._prepared_pdf_available() or not self._artwork_output_allowed():
            return
        try:
            completed = self.preview_session.status().completion
            result = capture_prepared_page_png(
                self.document, self.preview_session, mode=completed.mode, target_ppi=completed.target_ppi,
                output_ppi=output_ppi,
                landscape_workaround=mtg_proxy_printer.settings.settings["export"].getboolean(
                    "landscape-compatibility-workaround"))
        except (ValueError, RuntimeError, MemoryError) as error:
            self.on_error_occurred(str(error) or self.tr(
                "Not enough memory for this PNG page. Choose a lower PNG resolution or prepared PDF."))
            return
        self.current_dialog = dialog = PreparedPagePNGDialog(result, self)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.open()

    @Slot()
    def on_action_export_prepared_page_pdf_triggered(self):
        if not self._prepared_pdf_available() or not self._artwork_output_allowed():
            return
        try:
            completed = self.preview_session.status().completion
            result = capture_prepared_page_pdf(
                self.document, self.preview_session, mode=completed.mode, target_ppi=completed.target_ppi,
                landscape_workaround=mtg_proxy_printer.settings.settings["export"].getboolean(
                    "landscape-compatibility-workaround"))
        except (ValueError, RuntimeError) as error:
            self.on_error_occurred(str(error))
            return
        self.current_dialog = dialog = PreparedPagePDFDialog(result, self)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.open()

    @Slot()
    def on_action_show_siser_guide_triggered(self):
        if self.siser_guide_dialog is None:
            try:
                self.siser_guide_dialog = SiserGuideDialog(self)
            except RuntimeError as error:
                self.on_error_occurred(str(error))
                return
        self.siser_guide_dialog.show()
        self.siser_guide_dialog.raise_()
        self.siser_guide_dialog.activateWindow()

    def update_language_model(self):
        available_languages = CardDatabase.main_instance.get_all_languages()
        logger.debug(f"Setting the list of available languages to {available_languages}")
        self.language_model.setStringList(available_languages)

    def _create_about_dialog(self) -> AboutDialog:
        about_dialog = AboutDialog(self)
        self.ui.action_show_about_dialog.triggered.connect(about_dialog.show_about)
        self.ui.action_show_changelog.triggered.connect(about_dialog.show_changelog)
        return about_dialog

    def _create_missing_images_manager(self, document: Document) -> MissingImagesManager:
        manager = MissingImagesManager(document, self)
        manager.request_run_async_task.connect(self.request_run_async_task)
        manager.obtaining_missing_images_failed.connect(self.on_network_error_occurred)
        return manager

    @staticmethod
    def _setup_web_action_signals(ui: Ui_MainWindow):
        ui.action_web_kofi.setIcon(mtg_proxy_printer.ui.common.load_icon("kofi_symbol.svg"))
        for action, link in [
            (ui.action_web_contribute_translations, "https://crowdin.com/project/mtgproxyprinter"),
            (ui.action_web_source_code, "https://chiselapp.com/user/luziferius/repository/MTGProxyPrinter/index"),
            (ui.action_web_source_code_github, "https://github.com/luziferius/MTGProxyPrinter/"),
            (ui.action_web_project_on_pypi, "https://pypi.org/project/MTGProxyPrinter/"),
            (ui.action_web_kofi, "https://ko-fi.com/luziferius")
        ]:
            url = QUrl(link, QUrl.ParsingMode.StrictMode)
            action.triggered.connect(partial(QDesktopServices.openUrl, url))

    def _setup_platform_dependent_default_shortcuts(self):
        actions_with_shortcuts: list[tuple[QAction, StandardKey]] = [
            (self.ui.action_new_document, StandardKey.New),
            (self.ui.action_load_document, StandardKey.Open),
            (self.ui.action_save_document, StandardKey.Save),
            (self.ui.action_save_as, StandardKey.SaveAs),
            (self.ui.action_show_settings, StandardKey.Preferences),
            (self.ui.action_print, StandardKey.Print),
            (self.ui.action_quit, StandardKey.Quit),
            (self.ui.action_undo, StandardKey.Undo),
            (self.ui.action_redo, StandardKey.Redo),
        ]
        for action, shortcut in actions_with_shortcuts:
            action.setShortcut(shortcut)

    def _setup_central_widget(self):
        resolver = self.preview_session.resolve if self.preview_session is not None else None
        self.ui.central_widget.set_data(self.document, self.image_db, raster_resolver=resolver)
        if self.preview_session is not None:
            self.preview_session.rasters_changed.connect(self._on_preview_rasters_changed)
            for signal in (self.preview_session.progress, self.preview_session.completed,
                           self.preview_session.diagnostic):
                signal.connect(self._update_preview_status)
        self.ui.central_widget.request_run_async_task.connect(self.request_run_async_task)

    def _setup_image_preparation_preview(self):
        self.preview_session = None
        self._neural_worker_config = None
        self._neural_setup_dialog = None
        self.image_preparation_preview = dock = ImagePreparationPreview(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        self._preview_resume_timer = QTimer(self)
        self._preview_resume_timer.setSingleShot(True)
        self._preview_resume_timer.timeout.connect(self._apply_preview_options)
        self.ui.action_image_preparation_preview.toggled.connect(dock.setVisible)
        dock.visibilityChanged.connect(self._on_preview_visibility_changed)
        dock.options_changed.connect(self._apply_preview_options)
        dock.refresh_requested.connect(self._refresh_image_preparation_preview)
        dock.setup_requested.connect(self._configure_neural_worker)
        try:
            source = Path(self.image_db.db_path).absolute()
            derivative = source.parent / "ImageDerivatives"
            # Compare resolved trees too: injected roots can contain symbolic links.
            original_tree = source.resolve()
            derivative_tree = derivative.resolve()
            if derivative_tree == original_tree or original_tree in derivative_tree.parents or derivative_tree in original_tree.parents:
                raise ValueError(self.tr("The derivative cache must be separate from the original image cache."))
            self.preview_session = PreparedPageSession(
                self.document, self.request_run_async_task.emit, source, DerivativeCache(derivative), self,
                max_raster_bytes=512 * 1024 * 1024)
        except (ValueError, TypeError, OSError, RuntimeError) as error:
            dock.set_unavailable(self.tr("Image preparation preview is unavailable: {error}").format(error=error))
        dock.set_locked(bool(UI_LOCK_SEMAPHORE))

    @Slot(bool)
    def _on_preview_visibility_changed(self, visible):
        action = self.ui.action_image_preparation_preview
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)
        if not visible:
            self._preview_resume_timer.stop()
            self.image_preparation_preview.set_off()
            if self.preview_session is not None:
                self.preview_session.disable()
                self._update_preview_status()

    @Slot()
    def _configure_neural_worker(self):
        if self._neural_setup_dialog is not None:
            self._neural_setup_dialog.raise_()
            return
        dialog = self._neural_setup_dialog = NeuralWorkerSetup(self._neural_worker_config, self)
        def accepted():
            self._neural_worker_config = dialog.config
            self._apply_preview_options()
        dialog.accepted.connect(accepted)
        dialog.finished.connect(lambda *_: setattr(self, "_neural_setup_dialog", None))
        dialog.open()

    @Slot()
    def _apply_preview_options(self):
        dock = self.image_preparation_preview
        dock.set_locked(bool(UI_LOCK_SEMAPHORE))
        session = self.preview_session
        if session is None or not self.is_running:
            return
        if UI_LOCK_SEMAPHORE or not dock.isVisible() or dock.mode == "off":
            session.disable()
        elif dock.mode == "neural":
            if self._neural_worker_config is None:
                session.disable()
            else:
                session.enable(mode="neural", target_ppi=dock.target_ppi, worker_config=self._neural_worker_config)
        else:
            session.enable(mode=dock.mode, target_ppi=dock.target_ppi)
        self._update_preview_status()

    @Slot()
    def _refresh_image_preparation_preview(self):
        if (self.is_running and not UI_LOCK_SEMAPHORE and self.preview_session is not None
                and self.image_preparation_preview.isVisible() and self.image_preparation_preview.mode != "off"):
            self.preview_session.refresh()
            self._update_preview_status()

    @Slot()
    def _on_preview_rasters_changed(self):
        self.ui.central_widget.ui.page_renderer.scene().refresh_card_rasters()
        self._update_preview_status()

    @Slot()
    def _update_preview_status(self, *_):
        if self.preview_session is not None:
            dock = self.image_preparation_preview
            if dock.mode == "neural" and self._neural_worker_config is None and dock.isVisible():
                dock.show_configuration_required()
            else:
                dock.show_status(self.preview_session.status())
        self._update_prepared_pdf_action()

    def _setup_undo_redo_actions(self, document: Document):
        ui = self.ui
        ui.action_undo.triggered.connect(document.undo)
        ui.action_redo.triggered.connect(document.redo)
        document.action_applied.connect(self.on_document_action_applied_or_undone)
        document.action_undone.connect(self.on_document_action_applied_or_undone)
        document.undo_available_changed.connect(ui.action_undo.setEnabled)
        document.redo_available_changed.connect(ui.action_redo.setEnabled)

    def _connect_document_signals(self, document: Document):
        ui = self.ui
        ui.action_new_page.triggered.connect(lambda: document.apply(ActionNewPage()))
        ui.action_discard_page.triggered.connect(lambda: document.apply(ActionRemovePage()))
        ui.action_new_document.triggered.connect(lambda: document.apply(ActionNewDocument()))
        ui.action_compact_document.triggered.connect(lambda: document.apply(ActionCompactDocument()))
        ui.action_shuffle_document.triggered.connect(lambda: document.apply(ActionShuffleDocument()))

    @Slot()
    def on_action_download_card_data_triggered(self):
        logger.info("About to update the card data from Scryfall")
        ui = self.ui
        ui.action_download_card_data.setDisabled(True)
        data_source = ApiStreamTask()
        import_task = DatabaseImportTask(data_source, carddb_path=CardDatabase.main_instance.db_path)
        import_task.error_occurred.connect(
            lambda: ui.action_download_card_data.setEnabled(True), BlockingQueuedConnection)
        data_source.network_error_occurred.connect(
            lambda: ui.action_download_card_data.setEnabled(True), BlockingQueuedConnection)
        data_source.error_occurred.connect(
            lambda: ui.action_download_card_data.setEnabled(True), BlockingQueuedConnection)
        self.request_run_async_task.emit(data_source)
        self.request_run_async_task.emit(import_task)

    def _get_widgets_and_actions_disabled_in_loading_state(self) -> UiElements:
        ui = self.ui
        return [
            ui.central_widget,
            ui.action_new_document,
            ui.action_save_as,
            ui.action_save_document,
            ui.action_edit_document_settings,
            ui.action_compact_document,
            ui.action_shuffle_document,
            ui.action_load_document,
            ui.action_import_deck_list,
            ui.action_new_page,
            ui.action_add_empty_card,
            ui.action_discard_page,
            ui.action_cleanup_local_image_cache,
            ui.action_image_preparation_preview,
            ui.action_print,
            ui.action_print_pdf,
            ui.action_export_prepared_page_pdf,
            ui.menu_export_prepared_png.menuAction(),
            *self._prepared_png_actions,
            ui.action_print_preview,
            ui.action_export_png,
            ui.action_export_cut_template,
            ui.action_export_calibration,
            ui.action_show_settings,
            ui.action_add_custom_cards,
            ui.action_download_missing_card_images,
            ui.action_export_card_images,
            ui.action_undo,
            ui.action_redo,
        ]

    def _create_progress_bar_manager(self):
        manager = ProgressBarManager(self)
        self.statusBar().addPermanentWidget(manager)
        return manager

    @Slot()
    def on_dialog_finished(self):
        self.current_dialog = None

    @Slot()
    def on_document_action_applied_or_undone(self):
        undo_tooltip = self.tr("Undo:\n{top_entry}").format(top_entry=self.document.undo_stack[-1]) \
            if self.document.undo_stack else self.default_undo_tooltip
        redo_tooltip = self.tr("Redo:\n{top_entry}").format(top_entry=self.document.redo_stack[-1]) \
            if self.document.redo_stack else self.default_redo_tooltip
        self.ui.action_undo.setToolTip(undo_tooltip)
        self.ui.action_redo.setToolTip(redo_tooltip)

    def closeEvent(self, event: QCloseEvent):
        """
        This function is automatically called when the window is closed using the close [X] button in the window
        decorations or by right-clicking in the system window list and using the close action, or similar ways to close
        the window.
        """
        logger.debug("User closed the main window, closing application…")
        event.accept()
        if self.preview_session is not None:
            self.preview_session.close()
        # Triggering the quit action implicitly closes all windows, thus causes this event to fire during application
        # quit. This check prevents the quit logic from running twice.
        if self.is_running:
            self.on_action_quit_triggered()

    @Slot()
    def on_action_quit_triggered(self):
        logger.info(f"User wants to quit.")
        self.is_running = False
        self._preview_resume_timer.stop()
        if self.preview_session is not None:
            self.preview_session.close()
        if self.ui.toolBar.isVisible() != mtg_proxy_printer.settings.settings["gui"].getboolean("show-toolbar"):
            logger.debug("Toolbar visibility setting changed. Updating config and writing new state to disk.")
            mtg_proxy_printer.settings.settings["gui"]["show-toolbar"] = str(self.ui.toolBar.isVisible())
            mtg_proxy_printer.settings.write_settings_to_file()
        QApplication.instance().quit()

    @Slot()
    def on_action_cleanup_local_image_cache_triggered(self):
        logger.info("User wants to clean up the local image cache")
        wizard = CacheCleanupWizard(self.image_db, self)
        wizard.accepted.connect(self._refresh_image_preparation_preview, QueuedConnection)
        show_wizard_or_dialog(wizard)

    @Slot()
    def on_action_import_deck_list_triggered(self):
        logger.info(f"User imports a deck list.")
        wizard = DeckImportWizard(self.document, self.language_model, self)
        wizard.request_run_async_task.connect(self.request_run_async_task)
        show_wizard_or_dialog(wizard)

    @Slot()
    def on_action_add_custom_cards_triggered(self):
        logger.info(f"User adds custom cards.")
        self.current_dialog = dialog = CustomCardImportDialog(self.document, self)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.request_action.connect(self.document.apply)
        show_wizard_or_dialog(dialog)

    @Slot()
    def on_action_print_triggered(self):
        if not self._artwork_output_allowed():
            return
        logger.info(f"User prints the current document.")
        action_str = self.tr(
            "printing",
            "This is passed as the {action} when asking the user about compacting the document if that can save pages")
        if (self.document.page_layout.print_registration_marks_style != RegistrationProfileId.SISER.value
                and self._ask_user_about_compacting_document(action_str) == StandardButton.Cancel):
            return
        try:
            dialog = PrintDialog(self.document, self)
        except RuntimeError as error:
            self.on_error_occurred(str(error))
            return
        self.current_dialog = dialog
        dialog.error_occurred.connect(self.on_error_occurred)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.finished.connect(self.on_dialog_finished)
        # Use the QDialog base class open() method, because QPrintDialog.open() performs additional, unwanted actions.
        self.missing_images_manager.obtain_missing_images(super(QPrintDialog, dialog).open)

    @Slot()
    def on_action_print_preview_triggered(self):
        if not self._artwork_output_allowed():
            return
        logger.info(f"User views the print preview.")
        action_str = self.tr(
            "printing",
            "This is passed as the {action} when asking the user about compacting the document if that can save pages")
        if (self.document.page_layout.print_registration_marks_style != RegistrationProfileId.SISER.value
                and self._ask_user_about_compacting_document(action_str) == StandardButton.Cancel):
            return
        try:
            dialog = PrintPreviewDialog(self.document, self)
        except RuntimeError as error:
            self.on_error_occurred(str(error))
            return
        self.current_dialog = dialog
        dialog.error_occurred.connect(self.on_error_occurred)
        dialog.finished.connect(self.on_dialog_finished)
        self.missing_images_manager.obtain_missing_images(dialog.open)

    @Slot()
    def on_action_print_pdf_triggered(self):
        if not self._artwork_output_allowed():
            return
        logger.info(f"User prints the current document to PDF.")
        action_str = self.tr(
            "exporting as a PDF",
            "This is passed as the {action} when asking the user about compacting the document if that can save pages")
        if (self.document.page_layout.print_registration_marks_style != RegistrationProfileId.SISER.value
                and self._ask_user_about_compacting_document(action_str) == StandardButton.Cancel):
            return
        self.current_dialog = dialog = SavePDFDialog(self, self.document)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.finished.connect(self.on_dialog_finished)
        self.missing_images_manager.obtain_missing_images(dialog.open)

    @Slot()
    def on_action_export_png_triggered(self):
        if not self._artwork_output_allowed():
            return
        logger.info(f"User exports the current document to a sequence of PNG images.")
        action_str = self.tr(
            "exporting as a PNG image sequence",
            "This is passed as the {action} when asking the user about compacting the document if that can save pages")
        if (self.document.page_layout.print_registration_marks_style != RegistrationProfileId.SISER.value
                and self._ask_user_about_compacting_document(action_str) == StandardButton.Cancel):
            return
        self.current_dialog = dialog = SavePNGDialog(self, self.document)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.finished.connect(self.on_dialog_finished)
        self.missing_images_manager.obtain_missing_images(dialog.open)

    def _artwork_output_allowed(self) -> bool:
        if UI_LOCK_SEMAPHORE or self.current_dialog is not None:
            return False
        try:
            geometry = build_page_geometry(self.document.page_layout, PageType.UNDETERMINED, 0)
            require_artwork_output_supported(self.document.page_layout.print_registration_marks_style, geometry)
        except (ValueError, RuntimeError) as error:
            self.on_error_occurred(str(error))
            return False
        return True

    @Slot()
    def on_action_export_calibration_triggered(self):
        if UI_LOCK_SEMAPHORE or self.current_dialog is not None:
            return
        document = self.document
        try:
            page = document.currently_edited_page
            page_number = document.get_current_page_index().row() + 1
            source_path = document.save_file_path
            registration_style = document.page_layout.print_registration_marks_style
            geometry = build_page_geometry(document.page_layout, page.page_type(), len(page))
            pdf_bytes = render_calibration_pdf(geometry, registration_style)
        except (ValueError, RuntimeError) as error:
            self.on_error_occurred(str(error))
            return
        self.current_dialog = dialog = SaveCalibrationPDFDialog(self, pdf_bytes, source_path, page_number)
        dialog.error_occurred.connect(self.on_error_occurred)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.open()

    @Slot()
    def on_action_export_cut_template_triggered(self):
        if UI_LOCK_SEMAPHORE or self.current_dialog is not None:
            return
        document = self.document
        try:
            page = document.currently_edited_page
            page_number = document.get_current_page_index().row() + 1
            source_path = document.save_file_path
            geometry = build_page_geometry(document.page_layout, page.page_type(), len(page))
            svg_bytes = serialize_cut_template_svg(geometry).encode("utf-8")
        except ValueError as error:
            self.on_error_occurred(str(error))
            return
        self.current_dialog = dialog = SaveCutTemplateDialog(self, svg_bytes, source_path, page_number)
        dialog.error_occurred.connect(self.on_error_occurred)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.open()

    @Slot()
    def on_action_export_card_images_triggered(self):
        logger.info("User exports the card images in the current document to a directory")
        self.current_dialog = dialog = ExportCardImagesDialog(self.document, self)
        dialog.error_occurred.connect(self.on_error_occurred)
        dialog.finished.connect(self.on_dialog_finished)
        dialog.open()

    @Slot()
    def on_action_add_empty_card_triggered(self):
        empty_card = self.document.get_empty_card_for_current_page()
        self.document.apply(ActionAddCard(empty_card))

    @Slot(str)
    def on_network_error_occurred(self, message: str):
        if not self._show_network_errors:
            return
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            self.tr("Network error"),
            self.tr(
                "Operation failed, because a network error occurred.\n"
                "Check your internet connection. Reported error message:\n\n{message}").format(message=message),
            StandardButton.Ok, self,)
        dont_show_checkbox = QCheckBox("Don't show again for this session", self)
        box.setCheckBox(dont_show_checkbox)
        box.exec()
        if dont_show_checkbox.isChecked():
            self._show_network_errors = False

    @Slot(str)
    def on_error_occurred(self, message: str):
        QMessageBox.critical(
            self, self.tr("Error"),
            self.tr(
                "Operation failed, because an internal error occurred.\n"
                "Reported error message:\n\n{message}").format(message=message),
            StandardButton.Ok, StandardButton.Ok)

    def _ask_user_about_compacting_document(self, action: str) -> StandardButton:
        if savable_pages := self.document.compute_pages_saved_by_compacting():
            if (result := QMessageBox.question(
                self, self.tr("Saving pages possible"),
                self.tr("It is possible to save %n pages when printing this document.\n"
                        "Do you want to compact the document now to minimize the page count prior to {action}?",
                        "", savable_pages).format(action=action),
                StandardButton.Yes | StandardButton.No | StandardButton.Cancel
            )) == StandardButton.Yes:
                self.document.apply(ActionCompactDocument())
            return result
        return StandardButton.No  # No pages can be saved, assume "No" for this case

    def ask_user_about_empty_database(self):
        """
        This is called when the application starts with an empty or no card database. Ask the user if they wish
        to download the card data now. If so, trigger the appropriate action, just as if the user clicked the menu item.
        """
        if QMessageBox.question(
                self, self.tr("Download required Card data from Scryfall?"),
                self.tr(
                    "This program requires downloading additional card data from Scryfall to operate the card search.\n"
                    "Download the required data from Scryfall now?\n"
                    "Without the data, you can only print custom cards by drag&dropping "
                    "the image files onto the main window."),
                StandardButton.Yes | StandardButton.No, StandardButton.Yes) == StandardButton.Yes:
            self.on_action_download_card_data_triggered()

    @Slot()
    def on_action_save_document_triggered(self):
        logger.debug("User clicked on Save")
        if self.document.save_file_path is None:
            logger.debug("No save file path set. Call 'Save as' instead.")
            self.ui.action_save_as.trigger()
        else:
            logger.debug("About to save the document")
            self.document.save_to_disk()
            logger.debug("Saved.")

    @Slot()
    def on_action_edit_document_settings_triggered(self):
        logger.info("User wants to edit the document settings. Showing the editor dialog")
        self.current_dialog = dialog = DocumentSettingsDialog(self.document, self)
        dialog.finished.connect(self.on_dialog_finished)
        show_wizard_or_dialog(dialog)

    @Slot()
    def on_action_download_missing_card_images_triggered(self):
        logger.info("User wants to download missing card images")
        self.missing_images_manager.obtain_missing_images()

    @Slot()
    def on_action_save_as_triggered(self):
        self.current_dialog = dialog = SaveDocumentAsDialog(self.document, self)
        dialog.finished.connect(self.on_dialog_finished)
        show_wizard_or_dialog(dialog)

    @Slot()
    def on_action_load_document_triggered(self):
        self.current_dialog = dialog = LoadDocumentDialog(self, self.document)
        dialog.request_run_async_task.connect(self.request_run_async_task)
        dialog.accepted.connect(self.ui.central_widget.select_first_page)
        dialog.finished.connect(self.on_dialog_finished)
        show_wizard_or_dialog(dialog)

    def on_document_loading_failed(self, failed_path: Path, reason: str):
        function_text = self.ui.action_import_deck_list.text()
        QMessageBox.critical(
            self, self.tr("Document loading failed"),
            self.tr('Loading file "{failed_path}" failed. The file was not recognized as a '
                    '{program_name} document. If you want to load a deck list, use the '
                    '"{function_text}" function instead.\n'
                    'Reported failure reason: {reason}').format(
                failed_path=failed_path, program_name=mtg_proxy_printer.meta_data.PROGRAMNAME,
                function_text=function_text, reason=reason),
            StandardButton.Ok, StandardButton.Ok
        )

    def on_document_loading_found_unknown_scryfall_ids(self, unknown: int, replaced: int):
        if replaced:
            QMessageBox.warning(
                self, self.tr("Unavailable printings replaced"),
                self.tr(
                    "The document contained %n unavailable printings of cards that "
                    "were automatically replaced with other printings. The replaced printings are unavailable, "
                    "because they match a configured card filter.", "", replaced),
                StandardButton.Ok, StandardButton.Ok
            )
        if unknown:
            QMessageBox.warning(
                self, self.tr("Unrecognized cards in loaded document found"),
                self.tr(
                    "Skipped %n unrecognized cards in the loaded document. "
                    "Saving the document will remove these entries permanently.\n\nThe locally stored card "
                    "data may be outdated or the document was tampered with.", "", unknown),
                StandardButton.Ok, StandardButton.Ok
            )

    def show_application_update_available_message_box(self, newer_version: str):
        if QMessageBox.question(
                self, self.tr("Application update available. Visit website?"),
                self.tr("An application update is available: Version {newer_version}\n"
                        "You are currently using version {current_version}.\n\n"
                        "Open the {program_name} website in your web browser "
                        "to download the new version?").format(
                    newer_version=newer_version, current_version=mtg_proxy_printer.meta_data.__version__,
                    program_name=mtg_proxy_printer.meta_data.PROGRAMNAME,
                ),
                StandardButton.Yes | StandardButton.No, StandardButton.No
        ) == StandardButton.Yes:
            url = QUrl(mtg_proxy_printer.meta_data.DOWNLOAD_WEB_PAGE, QUrl.ParsingMode.StrictMode)
            QDesktopServices.openUrl(url)

    def show_card_data_update_available_message_box(self, estimated_card_count: int):
        if QMessageBox.question(
                self, self.tr("New card data available"),
                self.tr(
                    "There are %n new printings available on Scryfall. Update the local data now?",
                    "", estimated_card_count),
                StandardButton.Yes | StandardButton.No, StandardButton.Yes
        ) == StandardButton.Yes:
            logger.info("User agreed to update the card data from Scryfall. Performing update")
            self.on_action_download_card_data_triggered()
        else:
            # If the user declines to perform the update now, allow them to perform it later by enabling the action.
            self.ui.action_download_card_data.setEnabled(True)

    def ask_user_about_application_update_policy(self):
        """Executed on start when the application update policy setting is set to None, the default value."""
        name = mtg_proxy_printer.meta_data.PROGRAMNAME
        self._ask_user_about_update_policy(
            title=self.tr("Check for application updates?"),
            question=self.tr(
                "Automatically check for application updates whenever you start {program_name}?").format(
                program_name=name),
            logger_message="Application update policy set.",
            settings_key="check-for-application-updates"
        )

    def ask_user_about_card_data_update_policy(self):
        """Executed on start when the card data update policy setting is set to None, the default value."""
        name = mtg_proxy_printer.meta_data.PROGRAMNAME
        self._ask_user_about_update_policy(
            title=self.tr("Check for card data updates?"),
            question=self.tr(
                "Automatically check for card data updates on Scryfall whenever you start {program_name}?").format(
                program_name=name),
            logger_message="Card data update policy set.",
            settings_key="check-for-card-data-updates"
        )

    def _ask_user_about_update_policy(self, title: str, question: str, logger_message: str, settings_key: str):
        result = QMessageBox.question(
                self, title,
                self.tr("{question}\nYou can change this later in the settings.").format(question=question),
                StandardButton.Yes | StandardButton.No | StandardButton.Cancel
        )  # type: StandardButton
        if result in {StandardButton.Yes, StandardButton.No}:
            logger.info(f"{logger_message} User choice: {result.name}")
            mtg_proxy_printer.settings.settings["update-checks"][settings_key] = str(result == StandardButton.Yes)
            mtg_proxy_printer.settings.write_settings_to_file()
            logger.debug("Written settings to disk.")
        else:
            logger.info("User declined answering. Will ask again at next start")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._to_save_file_path(event):
            logger.info("User drags a saved MTGProxyPrinter document onto the main window, accepting event")
            event.acceptProposedAction()
        elif CustomCardImportDialog.dragdrop_acceptable(event):
            logger.info(f"User drags {len(event.mimeData().urls())} images onto the main window, accepting event")
            event.acceptProposedAction()
        else:
            logger.debug("Rejecting drag&drop action for unknown or invalid data")

    def dropEvent(self, event: QDropEvent) -> None:
        if path := self._to_save_file_path(event):
            logger.info("User dropped save file onto the main window, loading the dropped document")
            self.request_run_async_task.emit(DocumentLoader(self.document, path))
        elif CustomCardImportDialog.dragdrop_acceptable(event):
            self.current_dialog = dialog = CustomCardImportDialog(self.document, self)
            dialog.request_action.connect(self.document.apply)
            dialog.finished.connect(self.on_dialog_finished)
            dialog.show_from_drop_event(event)

    @staticmethod
    def _to_save_file_path(event: QDragEnterEvent | QDropEvent) -> Path | None:
        """
        Returns a Path instance to a file, if the drag&drop event contains a reference to exactly 1 document save file,
        None otherwise.
        """
        mime_data = event.mimeData()
        # It doesn't make sense to drop multiple save files at once, since only one can be loaded.
        # So ignore drag&drop containing multiple files
        if mime_data.hasUrls() and len(dropped_urls := mime_data.urls()) == 1:
            url = dropped_urls[0].toLocalFile()
            path = Path(url)
            acceptable = path.is_file() and path.suffix.casefold() == f".{DEFAULT_SAVE_SUFFIX}"
            if acceptable:
                return path
        return None

    @Slot()
    def ui_lock_acquire(self):
        global UI_LOCK_SEMAPHORE
        if not UI_LOCK_SEMAPHORE:
            self._preview_resume_timer.stop()
            self.image_preparation_preview.set_locked(True)
            if self.preview_session is not None:
                self.preview_session.disable()
                self._update_preview_status()
            for item in self._get_widgets_and_actions_disabled_in_loading_state():
                item.setDisabled(True)
        UI_LOCK_SEMAPHORE += 1

    @Slot()
    def ui_lock_release(self):
        global UI_LOCK_SEMAPHORE
        was_locked = bool(UI_LOCK_SEMAPHORE)
        UI_LOCK_SEMAPHORE = max(0, UI_LOCK_SEMAPHORE-1)
        if not UI_LOCK_SEMAPHORE:
            for item in self._get_widgets_and_actions_disabled_in_loading_state():
                item.setEnabled(True)
            self.image_preparation_preview.set_locked(False)
            if was_locked:
                self._preview_resume_timer.start(0)
            self._update_prepared_pdf_action()
        # The undo/redo buttons are part of the list above, so ensure that the state is consistent
        self.ui.action_redo.setEnabled(bool(self.document.redo_stack))
        self.ui.action_undo.setEnabled(bool(self.document.undo_stack))
