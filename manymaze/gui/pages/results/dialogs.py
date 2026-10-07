"""Dialogs of the Data page: HTML report options and video export overlays."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout)

from ....core.videoexport import OverlayOptions


def _combo(items, data=None, tip: str = "") -> QComboBox:
    c = QComboBox()
    for i, it in enumerate(items):
        c.addItem(it, data[i] if data else it)
    if tip:
        c.setToolTip(tip)
    return c


class ReportDialog(QDialog):
    """Choose measures for the statistics section of the HTML report and figure options."""

    def __init__(self, measures: list[str], preselect: list[str], parent=None, chart_params: list[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("HTML report")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Compare groups (statistics and graphs) for these measures:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search measures…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        for m in measures:
            it = QListWidgetItem(m)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if m in preselect else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        self.plots = QCheckBox("Include track plots and heat maps for every test")
        self.plots.setChecked(True)
        lay.addWidget(self.plots)
        f = QFormLayout()
        self.norm = _combo(["Automatic (each map)", "Same scale for all tests", "% of time", "Relative (max = 1)"],
                           ["auto", "fixed", "percent", "relative"])
        f.addRow("Heat map scale", self.norm)
        self.charts = QCheckBox("Charts of " + (", ".join(chart_params) if chart_params else "speed"))
        self.chart_params = list(chart_params) or ["Speed"]
        f.addRow("", self.charts)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create report…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, 560)

    def _filter(self, text):
        t = text.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def selected(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class VideoExportDialog(QDialog):
    """Options for exporting a test's video with overlays."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export video with overlays")
        o = OverlayOptions()
        f = QFormLayout(self)
        self.zones = QCheckBox("Draw the zones, points and lines")
        self.zones.setChecked(o.zones)
        self.trail = _combo(["- No track -", "2 seconds", "5 seconds", "15 seconds", "Whole test so far"],
                            [0, 2, 5, 15, -1])
        self.trail.setCurrentIndex(2)
        self.trail_color = _combo(["Speed", "Time", "Animal colour"], ["speed", "time", "fixed"])
        self.body = QCheckBox("Mark the centre, head and tail")
        self.body.setChecked(True)
        self.beh = QCheckBox("Show scored behaviours and freezing")
        self.beh.setChecked(True)
        self.stamp = QCheckBox("Add a time stamp and the test caption")
        self.stamp.setChecked(True)
        self.speed = _combo(["0.5×", "1×", "2×", "4×", "8×"], [0.5, 1.0, 2.0, 4.0, 8.0])
        self.speed.setCurrentIndex(1)
        self.scale = _combo(["100 %", "75 %", "50 %"], [1.0, 0.75, 0.5])
        for w in (self.zones, self.body, self.beh, self.stamp):
            f.addRow(w)
        f.addRow("Draw the track of the last", self.trail)
        f.addRow("Colour the track by", self.trail_color)
        f.addRow("Playback speed of the video", self.speed)
        f.addRow("Size of the video", self.scale)
        f.setVerticalSpacing(10)
        f.setHorizontalSpacing(16)
        self.form = f
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        f.addRow(self.buttons)

    def embed(self):
        """Use the options as a panel inside a page (no buttons, no window)."""
        self.setWindowFlags(Qt.Widget)
        self.buttons.hide()
        self.setSizeGripEnabled(False)
        return self

    def options(self) -> OverlayOptions:
        return OverlayOptions(zones=self.zones.isChecked(), trail_s=float(self.trail.currentData()),
                              trail_color=self.trail_color.currentData(), body_points=self.body.isChecked(),
                              behaviours=self.beh.isChecked(), freezing=self.beh.isChecked(),
                              timestamp=self.stamp.isChecked(), info=self.stamp.isChecked(),
                              speed=float(self.speed.currentData()), scale=float(self.scale.currentData()))
