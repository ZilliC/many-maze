"""Test view of a test without a video that ran with the I/O devices (Input/output only mode): its I/O log and
events as a timeline and a list, in place of the video."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSplitter,
                               QTableWidget, QVBoxLayout, QWidget)

from ....core.iolog import review_channels, review_rows
from ....core.plots import io_timeline
from ...widgets import PlotCanvas, value_text
from .common import _num_item, _ro_item


def has_io_log(test) -> bool:
    """A test analysed from its I/O log (as Project.has_results): run with the I/O devices, not only scored."""
    return bool(test.io_events or test.result_variables)


class IOLogReview(QWidget):
    """The I/O log of a test run without a camera: a timeline of its inputs, outputs, scored keys and marks, and
    the list of every change (with a filter), above which a line sums the test up (its duration, the numbers of
    channels and events, the procedures' result variables)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 0)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.summary)
        split = QSplitter(Qt.Vertical)
        self.canvas = PlotCanvas()
        self.canvas.setMinimumHeight(140)
        split.addWidget(self.canvas)
        low = QWidget()
        lv = QVBoxLayout(low)
        lv.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("Hint")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter the log…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(self._filter)
        row.addWidget(self.count_lbl, 1)
        row.addWidget(self.filter)
        lv.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Time (s)", "What", "Name", "Value"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        lv.addWidget(self.table, 1)
        split.addWidget(low)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        v.addWidget(split, 1)

    def show_test(self, project, test):
        """Show a test's I/O log and events (its duration: the test's, else the protocol's, else its last event)."""
        dur = test.duration_s or project.test_duration_s or None
        chans = review_channels(test.io_events, test.events, dur)
        self.canvas.set_figure(io_timeline(chans, dur))
        rows, more = review_rows(test.io_events, test.events)
        self.table.setRowCount(len(rows))
        for r, (t, what, name, value) in enumerate(rows):
            self.table.setItem(r, 0, _num_item(f"{t:.3f}", t))
            self.table.setItem(r, 1, _ro_item(what))
            self.table.setItem(r, 2, _ro_item(name))
            self.table.setItem(r, 3, _ro_item(value))
        self.count_lbl.setText(f"{len(rows)} line{'s' if len(rows) != 1 else ''}" +
                               (f" (and {more} more not listed)" if more else ""))
        n_in = sum(c["kind"] == "input" for c in chans)
        n_out = sum(c["kind"] == "output" for c in chans)
        parts = [f"Run with the I/O devices, without a video: {n_in} input{'s' if n_in != 1 else ''}, {n_out} "
                 f"output{'s' if n_out != 1 else ''}, {len(test.io_events)} I/O event"
                 f"{'s' if len(test.io_events) != 1 else ''}, {len(test.events)} scored event"
                 f"{'s' if len(test.events) != 1 else ''}"]
        if dur:
            parts.append(f"test duration {dur:g} s")
        if test.result_variables:
            parts.append("result variables: " + ", ".join(f"{k} = {value_text(v)}"
                                                           for k, v in test.result_variables.items()))
        self.summary.setText(" · ".join(parts) + ".")
        self._filter()

    def clear(self):
        self.table.setRowCount(0)
        self.summary.setText("")
        self.count_lbl.setText("")

    def _filter(self, *_):
        words = self.filter.text().lower().split()
        for r in range(self.table.rowCount()):
            text = " ".join(self.table.item(r, c).text().lower() for c in range(1, 4) if self.table.item(r, c))
            self.table.setRowHidden(r, not all(w in text for w in words))
