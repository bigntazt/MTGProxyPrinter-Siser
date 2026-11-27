"""Session-only explicit paths for the optional local neural worker."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout)


class NeuralWorkerSetup(QDialog):
    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Set up local neural worker"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.config = None
        layout = QVBoxLayout(self)
        notice = QLabel(self.tr(
            "Experimental Compact 2× on CUDA. Choose your existing worker environment and accepted bundle. "
            "worker.py needs adjacent protocol.py, backend.py and reference-model.json. "
            "No downloads or installation. Choices last only for this application session. "
            "Worker checks run in the background when Neural is selected."))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        layout.addLayout(form)
        self.fields = {}
        for name, label in (("interpreter", self.tr("Worker Python")), ("worker_script", self.tr("Bundle worker.py")),
                            ("checkpoint", self.tr("Pinned Compact checkpoint")),
                            ("private_parent", self.tr("Existing user-private work directory"))):
            row = QHBoxLayout()
            edit = self.fields[name] = QLineEdit(str(getattr(config, name)) if config is not None else "")
            edit.setAccessibleName(label)
            row.addWidget(edit)
            button = QPushButton(self.tr("Browse…"))
            button.setAccessibleName(self.tr("Browse {field}").format(field=label))
            button.clicked.connect(lambda checked=False, field=name: self._browse(field))
            row.addWidget(button)
            form.addRow(label, row)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(680, 330)

    def _browse(self, field):
        edit = self.fields[field]
        if field == "private_parent":
            value = QFileDialog.getExistingDirectory(self, self.tr("Choose existing user-private work directory"), edit.text())
        else:
            value, _ = QFileDialog.getOpenFileName(self, edit.accessibleName(), edit.text())
        if value:
            edit.setText(value)

    def accept(self):
        values = {name: edit.text().strip() for name, edit in self.fields.items()}
        missing = [edit.accessibleName() for name, edit in self.fields.items() if not values[name]]
        if missing:
            self.error_label.setText(self.tr("Choose: {fields}").format(fields=", ".join(missing)))
            return
        if any(not Path(value).is_absolute() for value in values.values()):
            self.error_label.setText(self.tr("Choose absolute paths for all four locations."))
            return
        # Metadata only. No stat, hashing, model/runtime discovery or process on the GUI thread.
        from mtg_proxy_printer.neural_worker_client import NeuralWorkerConfig, NeuralWorkerError, REFERENCE_SHA256
        try:
            self.config = NeuralWorkerConfig(**{name: Path(value) for name, value in values.items()},
                expected_sha256=REFERENCE_SHA256, model_id="nomos_compact_2x", device="cuda:0")
        except NeuralWorkerError as error:
            self.error_label.setText(error.detail)
            return
        super().accept()
