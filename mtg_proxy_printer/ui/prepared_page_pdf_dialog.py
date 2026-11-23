"""Review and atomically save one completed, immutable prepared-page PDF."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog

from mtg_proxy_printer.async_tasks.print_count_updater import PrintCountUpdater
from mtg_proxy_printer.ui import dialogs

try:
    from mtg_proxy_printer.ui.generated.prepared_page_pdf_dialog import Ui_PreparedPagePDFDialog
except ModuleNotFoundError:
    from mtg_proxy_printer.ui.common import load_ui_from_file
    Ui_PreparedPagePDFDialog = load_ui_from_file("prepared_page_pdf_dialog")


class PreparedPagePDFDialog(QDialog):
    request_run_async_task = Signal(PrintCountUpdater)

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.ui = Ui_PreparedPagePDFDialog()
        self.ui.setupUi(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # This holder can be cleared on native destruction without touching widgets.
        self._payload = {"result": result}
        self.destroyed.connect(self._payload.clear)
        self._filename_dialog = None
        self._saving = self._saved = False
        self.finished.connect(self._release_result)
        button = self.ui.button_box.button(QDialogButtonBox.StandardButton.Save)
        button.setText(self.tr("Save PDF…"))
        button.clicked.connect(self.choose_path)
        self.ui.button_box.rejected.connect(self.reject)
        mode = self.tr("Original — native source pixels") if result.mode == "original" else self.tr(
            "Lanczos requested target: {ppi} PPI").format(ppi=result.target_ppi)
        labels = {
            "original": self.tr("Original native pixels"), "cache_hit": self.tr("Lanczos cache hits"),
            "generated_cached": self.tr("Lanczos generated and cached"),
            "generated_uncached": self.tr("Lanczos generated, not cached"),
            "fallback_original": self.tr("Original fallback"), "standard": self.tr("Standard rendering"),
            "skipped": self.tr("Unsupported/skipped"),
        }
        lines = [self.tr("Captured page {page}").format(page=result.page_number), mode]
        lines.extend(self.tr("{outcome}: {count}").format(outcome=labels[key], count=count)
                     for key, count in result.outcome_counts if count)
        if dict(result.outcome_counts).get("standard"):
            lines.append(self.tr("Standard-rendering cards use their existing preview images; missing images may remain placeholders."))
        lines.extend(self.tr("Preparation detail: {detail}").format(detail=d.detail or d.reason)
                     for d in result.diagnostics)
        self.ui.summary_text.setPlainText("\n".join(lines))

    @Slot()
    def choose_path(self):
        if self._saved or self._saving or not self._payload:
            return
        if self._filename_dialog is not None:
            self._filename_dialog.raise_()
            return
        result = self._payload["result"]
        child = self._filename_dialog = QFileDialog(self)
        child.setWindowTitle(self.tr("Save prepared current page as PDF"))
        child.setNameFilter(self.tr("PDF documents (*.pdf)"))
        child.setDefaultSuffix("pdf")
        child.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        child.setFileMode(QFileDialog.FileMode.AnyFile)
        stem = Path(result.source_path).stem if result.source_path else "document"
        child.selectFile(f"{stem}-page-{result.page_number}-prepared.pdf")
        child.accepted.connect(self._save_selected)
        child.finished.connect(self._filename_finished)
        child.open()

    @Slot()
    def _save_selected(self):
        child = self._filename_dialog
        if self._saved or self._saving or not self._payload or child is None:
            return
        self._saving = True
        try:
            result = self._payload["result"]
            dialogs._write_prepared_file(child.selectedFiles()[0], result.data)
            # Commit is complete. Reentrant/repeated acceptance cannot schedule twice.
            self._saved = True
            task = PrintCountUpdater(image_keys=result.image_keys, db_path=result.db_path)
            self.request_run_async_task.emit(task)
            self.accept()
        except OSError as error:
            self.ui.error_label.setText(self.tr("Could not save PDF: {error}. Choose Save PDF to retry.").format(error=error))
        finally:
            self._saving = False

    @Slot()
    def _filename_finished(self, *_):
        child, self._filename_dialog = self._filename_dialog, None
        if child is not None:
            child.deleteLater()

    @Slot()
    def _release_result(self, *_):
        self._payload.clear()
        child, self._filename_dialog = self._filename_dialog, None
        if child is not None:
            child.reject()
            child.deleteLater()
