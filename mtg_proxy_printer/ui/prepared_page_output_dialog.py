"""Review and atomically save completed immutable prepared-page output."""
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


class PreparedPageOutputDialog(QDialog):
    format_name = "PDF"
    suffix = "pdf"
    request_run_async_task = Signal(PrintCountUpdater)

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.ui = Ui_PreparedPagePDFDialog()
        self.ui.setupUi(self)
        self.setWindowTitle(self.tr("Review prepared current-page {format}").format(format=self.format_name))
        self.ui.freshness_label.setText(self.tr(
            "This {format} uses the completed preparation results. Refresh preparation before exporting to include externally changed source images."
        ).format(format=self.format_name))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # This holder can be cleared on native destruction without touching widgets.
        self._payload = {"result": result}
        self.destroyed.connect(self._payload.clear)
        self._filename_dialog = None
        self._saving = self._saved = False
        self.finished.connect(self._release_result)
        button = self.ui.button_box.button(QDialogButtonBox.StandardButton.Save)
        button.setText(self.tr("Save {format}…").format(format=self.format_name))
        button.clicked.connect(self.choose_path)
        self.ui.button_box.rejected.connect(self.reject)
        provider = self.tr("Neural") if result.mode == "neural" else self.tr("Lanczos")
        mode = self.tr("Original — native source pixels") if result.mode == "original" else self.tr(
            "{provider} requested target: {ppi} PPI").format(provider=provider, ppi=result.target_ppi)
        labels = {
            "original": self.tr("Source-sufficient originals") if result.mode == "neural" else self.tr("Original native pixels"), "cache_hit": self.tr("{provider} cache hits").format(provider=provider),
            "generated_cached": self.tr("{provider} generated and cached").format(provider=provider),
            "generated_uncached": self.tr("{provider} generated, not cached").format(provider=provider),
            "fallback_original": self.tr("Original fallback"), "standard": self.tr("Standard rendering"),
            "skipped": self.tr("Unsupported/skipped"),
        }
        lines = [self.tr("Captured page {page}").format(page=result.page_number), mode]
        if result.mode == "neural":
            lines.append(self.tr("Experimental: one native 2× pass followed by conventional resizing. Text, symbols and artwork may change."))
        lines.extend(self._format_lines(result))
        lines.extend(self.tr("{outcome}: {count}").format(outcome=labels[key], count=count)
                     for key, count in result.outcome_counts if count)
        if dict(result.outcome_counts).get("standard"):
            lines.append(self.tr("Standard-rendering cards use their existing preview images; missing images may remain placeholders."))
        lines.extend(self.tr("Preparation detail: {detail}").format(detail=d.detail or d.reason)
                     for d in result.diagnostics)
        self.ui.summary_text.setPlainText("\n".join(lines))

    def _format_lines(self, result):
        return ()

    def _suggested_filename(self, result):
        stem = Path(result.source_path).stem if result.source_path else "document"
        return f"{stem}-page-{result.page_number}-prepared.{self.suffix}"

    @Slot()
    def choose_path(self):
        if self._saved or self._saving or not self._payload:
            return
        if self._filename_dialog is not None:
            self._filename_dialog.raise_()
            return
        result = self._payload["result"]
        child = self._filename_dialog = QFileDialog(self)
        child.setWindowTitle(self.tr("Save prepared current page as {format}").format(format=self.format_name))
        child.setNameFilter(self.tr("{format} documents (*.{suffix})").format(format=self.format_name, suffix=self.suffix))
        child.setDefaultSuffix(self.suffix)
        child.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        child.setFileMode(QFileDialog.FileMode.AnyFile)
        child.selectFile(self._suggested_filename(result))
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
            self.ui.error_label.setText(self.tr("Could not save {format}: {error}. Choose Save {format} to retry.").format(format=self.format_name, error=error))
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
