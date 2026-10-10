"""The real-time monitor: zone, point, sequence and input statistics, a live chart of any parameter, the I/O status
and warnings."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QSizePolicy, QTableWidget, QVBoxLayout, QWidget)

from ...core.livemonitor import CHART_PREFIX, FAST_PARAMS, LiveCharts, chart_parameters, input_rows, sequence_rows
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
def _stats_table(headers: list[str], min_h: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(24)
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.Stretch)
    for c in range(1, len(headers)):
        hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
    if min_h:
        t.setMinimumHeight(min_h)
    return t


def _input_rows(session, now: float) -> list:
    """The inputs table of the monitor: a live session's running statistics (``session.input_rows``: nothing is
    rescanned, however long the test), else the statistics of its I/O log."""
    rows = getattr(session, "input_rows", None)
    if callable(rows):
        return rows(now)
    return input_rows(list(getattr(session, "io_events", None) or []), now)


def _fill_rows(t: QTableWidget, rows: list[list[str]]):
    if t.rowCount() != len(rows):
        t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, txt in enumerate(row):
            t.setItem(r, c, table_item(txt, c > 0))
    hdr = t.horizontalHeader().sizeHint().height()
    t.setFixedHeight(hdr + t.verticalHeader().defaultSectionSize() * min(max(len(rows), 1), 6) + 4)


def _num(v, fmt="{:.1f}") -> str:
    return "—" if v is None or (isinstance(v, float) and not math.isfinite(v)) else fmt.format(v)


class MonitorPanel(QWidget):
    """Real-time monitoring of one live session: state, distance / speed / freezing, per-zone, per-point,
    per-sequence and per-input statistics, a live chart of any parameter (core.charts), the I/O device status and
    warnings.  Call :meth:`refresh` at ≤ 5 Hz."""

    CHART_UNITS = {k: u for k, (_lbl, u) in FAST_PARAMS.items()}

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
        # points, sequences and inputs: shown when the apparatus / I/O log has any
        self.points_box = QGroupBox("Points")
        self.points = _stats_table(["Point", "Distance", "Time near (s)", "Approaches", "Latency (s)"])
        self.seq_box = QGroupBox("Sequences")
        self.sequences = _stats_table(["Sequence", "Completed", "Attempts", "Errors", "Latency (s)"])
        self.inputs_box = QGroupBox("Inputs")
        self.inputs = _stats_table(["Input", "Now", "Activations", "Time on (s)", "Latency (s)"])
        for box, tbl in ((self.points_box, self.points), (self.seq_box, self.sequences),
                         (self.inputs_box, self.inputs)):
            bl = QVBoxLayout(box)
            bl.setContentsMargins(6, 4, 6, 4)
            bl.addWidget(tbl)
            box.hide()
            v.addWidget(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Chart"))
        self.param = QComboBox()
        self.param.setMaxVisibleItems(24)
        self._param_units: dict[str, str] = {}
        self._params_for = None  # the apparatus whose parameters the chart list holds
        self._set_params(chart_parameters(None))
        self.charts = LiveCharts()
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

    def _set_params(self, params: list[tuple[str, str, str]]):
        """The chart parameters: the fast ones, then those of core.charts for the apparatus."""
        cur = self.param.currentData()
        self.param.blockSignals(True)
        self.param.clear()
        self._param_units = {}
        for k, lbl, unit in params:
            self.param.addItem(lbl, k)
            self._param_units[k] = unit
        self.param.setCurrentIndex(max(0, self.param.findData(cur)))
        self.param.blockSignals(False)

    def set_chart_parameter(self, key: str) -> bool:
        """Show this parameter in the chart ("speed" …, or "chart:<core.charts name>")."""
        i = self.param.findData(key)
        if i < 0:
            i = self.param.findData(CHART_PREFIX + key)
        if i >= 0:
            self.param.setCurrentIndex(i)
        return i >= 0

    def clear(self, title: str = "No live test"):
        self.title.setText(title)
        for lbl in self.vals.values():
            lbl.setText("—")
        self.zones.setRowCount(0)
        for box in (self.points_box, self.seq_box, self.inputs_box):
            box.hide()
        self.chart.set_data([], [])

    def refresh(self, session, title: str = "", devices=None, warnings: list[str] | None = None):
        if session is None:
            self.clear(title or "No live test")
        else:
            self.title.setText(title or session.name or "Live test")
            st = getattr(session, "stats", None)
            if st is None or getattr(session, "io_only", False):
                self.vals["distance"].setText("—")
                self.vals["speed"].setText("—")
                self.vals["state"].setText(f"{len(session.events)} events")
                self.vals["zone"].setText("no camera (I/O only)" if st is not None else "no camera")
                self.zones.setRowCount(0)
                self.chart.set_data([], [])
                if st is not None:  # I/O only: the inputs table still follows the test
                    with session.lock:
                        now = session.elapsed
                    self._refresh_extra(None, [], [], _input_rows(session, now), now, "")
            else:
                app = getattr(session, "apparatus", None)
                if app is not self._params_for:
                    self._params_for = app
                    self._set_params(chart_parameters(app))
                param = self.param.currentData() or "speed"
                win = self.window.currentData() or 60.0
                with session.lock:
                    rows = st.rows()
                    if not param.startswith(CHART_PREFIX):
                        t, y = st.series(param, win)
                    zones = st.current_zones()
                    det, frz, imm = st.detected, st.freezing, st.immobile
                    dist, spd, unit = st.distance * st.factor, st.speed * st.factor, st.unit
                    points = [(n, d * st.factor, *rest) for n, d, *rest in st.points.rows()]
                    visits = [list(v) for v in st.visits]
                    now = session.elapsed
                if param.startswith(CHART_PREFIX):  # computed from the track so far, outside the lock
                    t, y = self.charts.series(session, param[len(CHART_PREFIX):], win)
                self._refresh_extra(app, points, visits, _input_rows(session, now), now, unit)
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
                                    self.CHART_UNITS[param].format(u=unit) if param in self.CHART_UNITS
                                    else self._param_units.get(param, ""), win)
        self._refresh_io(devices)
        ws = list(warnings or [])
        if len(ws) != self._n_warn:
            self.warnings.clear()
            self.warnings.addItems(ws[-200:])
            self.warnings.scrollToBottom()
            self._n_warn = len(ws)

    def _refresh_extra(self, app, points, visits, ins, now, unit):
        """The points, sequences and inputs tables (each hidden when there is nothing to show)."""
        _fill_rows(self.points, [[n, f"{_num(d)} {unit}", _num(tn), str(k), _num(lat)]
                                 for n, d, tn, k, lat in points])
        self.points_box.setVisible(bool(points))
        seqs = sequence_rows(app, visits, now)
        _fill_rows(self.sequences, [[n, str(c), str(a), str(e), _num(lat)] for n, c, a, e, lat in seqs])
        self.seq_box.setVisible(bool(seqs))
        _fill_rows(self.inputs, [[n, val, str(k) if math.isfinite(on) else "", _num(on), _num(lat)]
                                 for n, val, k, on, lat in ins])
        self.inputs_box.setVisible(bool(ins))

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
