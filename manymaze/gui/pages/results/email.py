"""Results ▸ E-mail report: the results (spreadsheet and / or HTML report) sent by e-mail through the SMTP server of
an alert device (Protocol ▸ Hardware ▸ I/O devices, *Alerts (e-mail / SMS)*), whose password is kept in
io-secrets.json as for the sensor alerts (core.mail)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QVBoxLayout)

from ....core.mail import addresses, mail_devices

TABLE_FORMATS = [(".xlsx", "Excel workbook (.xlsx)"), (".csv", "CSV file (.csv)"), ("", "No spreadsheet")]


class EmailReportDialog(QDialog):
    """Who to send the results to, the subject and message, and what to attach."""

    def __init__(self, project, parent=None, has_numeric: bool = True):
        super().__init__(parent)
        self.setWindowTitle("E-mail report")
        self.devices = mail_devices(project)
        v = QVBoxLayout(self)
        intro = QLabel("The results shown are sent through the e-mail server of an alert device (Protocol ▸ "
                       "Hardware ▸ I/O devices).")
        intro.setObjectName("Hint")
        intro.setWordWrap(True)
        v.addWidget(intro)
        f = QFormLayout()
        self.device = QComboBox()
        for d in self.devices:
            self.device.addItem(f"{d.get('name', 'Alerts')} ({d.get('smtp_host')})", d.get("name"))
        self.device.currentIndexChanged.connect(self._device_changed)
        self.to = QLineEdit()
        self.to.setPlaceholderText("name@example.org, other@example.org")
        self.subject = QLineEdit(f"{project.name} — results")
        self.message = QPlainTextEdit(f"The results of the experiment {project.name}, sent by mANY-MAZE.")
        self.message.setFixedHeight(80)
        self.table = QComboBox()
        for v_, label in TABLE_FORMATS:
            self.table.addItem(label, v_)
        self.report = QCheckBox("Attach the HTML report (results, statistics, track plots and heat maps)")
        self.report.setChecked(has_numeric)
        self.plots = QCheckBox("With track plots and heat maps (a larger file)")
        self.plots.setChecked(False)
        self.report.toggled.connect(self.plots.setEnabled)
        self.plots.setEnabled(self.report.isChecked())
        if len(self.devices) > 1:
            f.addRow("Send with", self.device)
        f.addRow("To", self.to)
        f.addRow("Subject", self.subject)
        f.addRow("Message", self.message)
        f.addRow("Spreadsheet", self.table)
        f.addRow("", self.report)
        f.addRow("", self.plots)
        v.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Send")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.error = QLabel()
        self.error.setObjectName("Hint")
        v.addWidget(self.error)
        self._device_changed()

    def device_config(self) -> dict | None:
        i = self.device.currentIndex()
        return self.devices[i] if 0 <= i < len(self.devices) else None

    def _device_changed(self, *_):
        d = self.device_config()
        if d is not None and not self.to.text().strip():
            self.to.setText(str(d.get("email_to", "") or ""))

    def _accept(self):
        if not addresses(self.to.text()):
            self.error.setText("Type the e-mail address to send the report to.")
            return
        if not self.table.currentData() and not self.report.isChecked():
            self.error.setText("Attach the spreadsheet, the report or both.")
            return
        self.accept()


def attachments_dir() -> Path:
    """A private temporary folder for the files of one e-mail (removed with :func:`remove_dir`)."""
    return Path(tempfile.mkdtemp(prefix="manymaze-mail-"))


def remove_dir(d: Path):
    shutil.rmtree(d, ignore_errors=True)
