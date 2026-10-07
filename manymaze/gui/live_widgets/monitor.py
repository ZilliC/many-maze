"""The real-time monitor: zone statistics, a live chart, the I/O status and warnings."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QSizePolicy, QTableWidget, QVBoxLayout, QWidget)

from ...core.live import LiveStats
from ..widgets import fmt_time
from .panels import ElidedLabel, table_item


# ====================================================================== live chart
class LiveChart(QWidget):
    """A light-weight scrolling line chart (QPainter, no matplotlib) of one parameter over the last N seconds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t = np.zeros(0)
        self.y = np.zeros(0)
        self.label = ""
        self.unit = ""
        self.window = 60.0
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_data(self, t, y, label="", unit="", window=60.0):
        self.t, self.y = np.asarray(t, float), np.asarray(y, float)
        self.label, self.unit, self.window = label, unit, window
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        r = QRectF(self.rect()).adjusted(38, 8, -8, -20)
        p.fillRect(self.rect(), pal.base())
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(r)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        ok = np.isfinite(self.y)
        if len(self.t) < 2 or not ok.any():
            p.setPen(pal.mid().color())
            p.drawText(r, Qt.AlignCenter, "No data yet")
            return
        t1 = self.t[-1]
        t0 = max(self.t[0], t1 - self.window)
        if t1 - t0 < 1e-6:
            t0 = t1 - 1
        lo, hi = float(np.nanmin(self.y[ok])), float(np.nanmax(self.y[ok]))
        lo = min(lo, 0.0)
        if hi - lo < 1e-9:
            hi = lo + 1.0
        hi += 0.08 * (hi - lo)
        sx = lambda t: r.left() + (t - t0) / (t1 - t0) * r.width()
        sy = lambda v: r.bottom() - (v - lo) / (hi - lo) * r.height()
        path = QPainterPath()
        started = False
        for t, v in zip(self.t, self.y):
            if t < t0 or not math.isfinite(v):
                started = False
                continue
            pt = QPointF(sx(t), sy(v))
            if started:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
                started = True
        p.setPen(QPen(QColor("#2563eb"), 1.6))
        p.drawPath(path)
        p.setPen(pal.text().color())
        p.drawText(QRectF(0, r.top() - 4, 35, 14), Qt.AlignRight, f"{hi:.3g}")
        p.drawText(QRectF(0, r.bottom() - 10, 35, 14), Qt.AlignRight, f"{lo:.3g}")
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignLeft, fmt_time(t0))
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignRight, fmt_time(t1))
        last = self.y[ok][-1]
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignHCenter,
                   f"{self.label}: {last:.3g} {self.unit}".strip())


# ====================================================================== monitor
class MonitorPanel(QWidget):
    """Real-time monitoring of one live session: state, distance / speed / freezing, per-zone statistics, a live
    chart, the I/O device status and warnings.  Call :meth:`refresh` at ≤ 5 Hz."""

    CHART_UNITS = {"speed": "{u}/s", "distance": "{u}", "motion": "%", "detected": "", "freezing": ""}

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 4, 10, 8)
        v.setSpacing(6)
        self.title = QLabel("No live test")
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        v.addWidget(self.title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(0)
        self.vals: dict[str, QLabel] = {}
        for i, (k, cap) in enumerate((("distance", "Distance"), ("speed", "Speed"), ("state", "Animal"),
                                      ("zone", "Zone"))):
            c = QLabel(cap)
            c.setObjectName("Hint")
            c.setStyleSheet("font-size:11px")
            val = ElidedLabel("—")
            val.setStyleSheet("font-size:14px")
            grid.addWidget(c, 0, i)
            grid.addWidget(val, 1, i)
            self.vals[k] = val
        v.addLayout(grid)

        self.zones = QTableWidget(0, 4)
        self.zones.setHorizontalHeaderLabels(["Zone", "Time (s)", "Entries", "Latency (s)"])
        self.zones.verticalHeader().hide()
        self.zones.verticalHeader().setDefaultSectionSize(24)
        self.zones.setShowGrid(False)
        self.zones.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.zones.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.zones.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.zones.setMinimumHeight(110)
        v.addWidget(self.zones, 2)

        row = QHBoxLayout()
        row.addWidget(QLabel("Chart"))
        self.param = QComboBox()
        for k, lbl in LiveStats.CHART_PARAMS.items():
            self.param.addItem(lbl, k)
        self.window = QComboBox()
        for c in (self.param, self.window):
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(6)
        for s in (30, 60, 120, 300):
            self.window.addItem(f"last {s} s", float(s))
        self.window.setCurrentIndex(1)
        row.addWidget(self.param, 1)
        row.addWidget(self.window)
        v.addLayout(row)
        self.chart = LiveChart()
        v.addWidget(self.chart, 2)

        io = QGroupBox("I/O devices")
        iv = QVBoxLayout(io)
        iv.setContentsMargins(6, 4, 6, 4)
        self.io_note = QLabel("No I/O devices")
        self.io_note.setObjectName("Hint")
        self.io = QTableWidget(0, 4)
        self.io.setHorizontalHeaderLabels(["Device", "Channel", "Kind", "Value"])
        self.io.verticalHeader().hide()
        self.io.verticalHeader().setDefaultSectionSize(24)
        self.io.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.io.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.io.setMaximumHeight(110)
        self.io.hide()
        iv.addWidget(self.io_note)
        iv.addWidget(self.io)
        v.addWidget(io)

        wb = QGroupBox("Warnings")
        wv = QVBoxLayout(wb)
        wv.setContentsMargins(6, 4, 6, 4)
        self.warnings = QListWidget()
        self.warnings.setMaximumHeight(90)
        wv.addWidget(self.warnings)
        v.addWidget(wb)
        self._n_warn = 0

    def clear(self, title: str = "No live test"):
        self.title.setText(title)
        for lbl in self.vals.values():
            lbl.setText("—")
        self.zones.setRowCount(0)
        self.chart.set_data([], [])

    def refresh(self, session, title: str = "", devices=None, warnings: list[str] | None = None):
        if session is None:
            self.clear(title or "No live test")
        else:
            self.title.setText(title or session.name or "Live test")
            st = getattr(session, "stats", None)
            if st is None:
                self.vals["distance"].setText("—")
                self.vals["speed"].setText("—")
                self.vals["state"].setText(f"{len(session.events)} events")
                self.vals["zone"].setText("no camera")
                self.zones.setRowCount(0)
                self.chart.set_data([], [])
            else:
                with session.lock:
                    rows = st.rows()
                    param = self.param.currentData()
                    win = self.window.currentData() or 60.0
                    t, y = st.series(param, win)
                    zones = st.current_zones()
                    det, frz, imm = st.detected, st.freezing, st.immobile
                    dist, spd, unit = st.distance, st.speed, st.unit
                self.vals["distance"].setText(f"{dist:.1f} {unit}")
                self.vals["speed"].setText(f"{spd:.1f} {unit}/s")
                self.vals["state"].setText("not detected" if not det else
                                           "freezing" if frz else "immobile" if imm else "moving")
                inner = [z for z in zones if z != "Arena"] or zones
                self.vals["zone"].setText(", ".join(inner[:2]) if inner else "—")
                if self.zones.rowCount() != len(rows):
                    self.zones.setRowCount(len(rows))
                for r, (name, tt, n, lat) in enumerate(rows):
                    self.zones.setItem(r, 0, table_item(name))
                    self.zones.setItem(r, 1, table_item(f"{tt:.1f}", True))
                    self.zones.setItem(r, 2, table_item(str(n), True))
                    self.zones.setItem(r, 3, table_item("—" if lat is None else f"{lat:.1f}", True))
                self.chart.set_data(t, y, self.param.currentText(),
                                    self.CHART_UNITS.get(param, "").format(u=unit), win)
        self._refresh_io(devices)
        ws = list(warnings or [])
        if len(ws) != self._n_warn:
            self.warnings.clear()
            self.warnings.addItems(ws[-200:])
            self.warnings.scrollToBottom()
            self._n_warn = len(ws)

    def _refresh_io(self, devices):
        status = None
        if devices is not None:
            try:
                status = list(devices.status())
            except Exception as e:
                self.io_note.setText(f"I/O status unavailable: {e}")
                status = None
        if not status:
            if devices is None:
                self.io_note.setText("No I/O devices")
            self.io_note.show()
            self.io.hide()
            return
        self.io_note.hide()
        self.io.show()
        self.io.setRowCount(len(status))
        for r, row in enumerate(status):
            for c, val in enumerate(list(row)[:4]):
                txt = "" if val is None else f"{val:g}" if isinstance(val, float) else str(val)
                self.io.setItem(r, c, table_item(txt))
