"""PNG-specific summary and naming over the accepted shared save lifecycle."""
from pathlib import Path

from mtg_proxy_printer.ui.prepared_page_output_dialog import PreparedPageOutputDialog


class PreparedPagePNGDialog(PreparedPageOutputDialog):
    format_name = "PNG"
    suffix = "png"

    def _format_lines(self, result):
        return (
            self.tr("PNG page canvas: {ppi} PPI — {width} × {height} pixels").format(
                ppi=result.output_ppi, width=result.pixel_width, height=result.pixel_height),
            self.tr("Opaque white paper. Page resolution does not imply recovered source detail."),
        )

    def _suggested_filename(self, result):
        stem = Path(result.source_path).stem if result.source_path else "document"
        return f"{stem}-page-{result.page_number}-prepared-{result.output_ppi}ppi.png"
