"""Ephemeral controls and metadata-only status for the main-page preview."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDockWidget

from mtg_proxy_printer.image_source import SUPPORTED_TARGET_PPI

try:
    from mtg_proxy_printer.ui.generated.image_preparation_preview import Ui_ImagePreparationPreview
except ModuleNotFoundError:
    from mtg_proxy_printer.ui.common import load_ui_from_file
    Ui_ImagePreparationPreview = load_ui_from_file("image_preparation_preview")


class ImagePreparationPreview(QDockWidget):
    options_changed = Signal()
    refresh_requested = Signal()
    setup_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_ImagePreparationPreview()
        self.ui.setupUi(self)
        self.setObjectName("image_preparation_preview")
        for name in ("mode_combo", "target_combo", "refresh_button", "status_label", "details_text", "setup_button"):
            setattr(self, name, getattr(self.ui, name))
        for index, ppi in enumerate(SUPPORTED_TARGET_PPI):
            self.target_combo.setItemData(index, ppi)
        self._available = True
        self._locked = False
        self.mode_combo.currentIndexChanged.connect(self._options_changed)
        self.target_combo.currentIndexChanged.connect(self._options_changed)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.setup_button.clicked.connect(self.setup_requested)
        self.update_controls()

    @property
    def mode(self):
        return ("off", "original", "lanczos", "neural")[self.mode_combo.currentIndex()]

    @property
    def target_ppi(self):
        return self.target_combo.currentData()

    def _options_changed(self, *_):
        self.update_controls()
        self.options_changed.emit()

    def set_off(self):
        self.mode_combo.setCurrentIndex(0)
        self.update_controls()

    def set_locked(self, locked):
        self._locked = locked
        self.update_controls()

    def update_controls(self):
        interactive = self._available and not self._locked
        self.mode_combo.setEnabled(interactive)
        self.target_combo.setEnabled(interactive and self.mode in ("lanczos", "neural"))
        self.refresh_button.setEnabled(interactive and self.mode != "off")
        self.setup_button.setEnabled(interactive)

    def set_unavailable(self, detail):
        self._available = False
        self.status_label.setText(self.tr("Preview unavailable"))
        self.details_text.setPlainText(detail)
        self.update_controls()

    def show_configuration_required(self):
        self.status_label.setText(self.tr("Neural setup required — standard preview retained"))
        self.details_text.setPlainText(self.tr(
            "Set up Worker Python, bundle worker.py, the pinned Compact checkpoint and an existing user-private work directory. "
            "Original and Lanczos remain available."))

    def show_status(self, status):
        # The owner supplies a current snapshot, never an old signal payload.
        labels = {
            "off": self.tr("Off — standard preview"),
            "queued": self.tr("Queued…"),
            "preparing": self.tr("Preparing…"),
            "applying": self.tr("Updating preview…"),
            "ready": self.tr("Ready"),
            "no_eligible_sources": self.tr("No eligible ordinary card sources on this page"),
            "cancelled": self.tr("Cancelled — use Refresh to try again"),
            "failed": self.tr("Preparation failed — standard preview retained"),
            "closed": self.tr("Preview closed"),
        }
        label = labels[status.phase]
        if status.phase == "preparing" and status.progress is not None:
            label += self.tr(" {resolved}/{total}").format(
                resolved=status.progress.resolved_occurrences, total=status.progress.total_occurrences)
        self.status_label.setText(label)
        lines = []
        if status.completion is not None:
            neural = status.completion.mode == "neural"
            provider = self.tr("Neural") if neural else self.tr("Lanczos")
            names = {
                "original": self.tr("Source-sufficient originals") if neural else self.tr("Original native pixels"),
                "cache_hit": self.tr("{provider} cache hits").format(provider=provider),
                "generated_cached": self.tr("{provider} generated and cached").format(provider=provider),
                "generated_uncached": self.tr("{provider} generated, not cached").format(provider=provider),
                "fallback_original": self.tr("Original fallback"),
                "standard": self.tr("Standard preview"),
                "skipped": self.tr("Unsupported/skipped"),
            }
            for outcome, count in status.completion.outcome_counts:
                if count:
                    lines.append(self.tr("{outcome}: {count}").format(outcome=names[outcome], count=count))
            if status.completion.summary.error_detail:
                lines.append(self.tr("Preparation failed: {detail}").format(
                    detail=status.completion.summary.error_detail[:4096]))
        reasons = {
            "provider_unavailable": self.tr("Preparation provider unavailable"),
            "worker_unavailable": self.tr("Neural worker unavailable"),
            "worker_rejected": self.tr("Neural worker rejected image"),
            "request_budget": self.tr("Neural worker request limit reached"),
            "worker_input_limit": self.tr("Source exceeds neural worker input limit"),
            "cache_write_failed": self.tr("Result could not be cached"),
            "preview_memory_limit": self.tr("Preview memory limit"),
            "missing_file": self.tr("Original source unavailable"),
            "idle_no_eligible_sources": self.tr("No eligible sources"),
            "scheduling_failed": self.tr("Preparation could not start"),
        }
        for diagnostic in status.diagnostics:
            lines.append(self.tr("{reason}: {detail}").format(
                reason=reasons.get(diagnostic.reason, diagnostic.reason), detail=diagnostic.detail[:4096]))
        self.details_text.setPlainText("\n".join(lines))
