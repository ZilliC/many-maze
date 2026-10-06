"""Results page: table of measures per test (and per time period), column chooser, exports, track plots / heat
maps, charts of per-frame parameters over time and video export with overlays."""

from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
                               QSizePolicy, QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import charts, plots
from ...core.export import (export_raw_data, export_xml, html_report, write_csv, write_table,
                            write_tsv, write_xlsx)
from ...core.measures import all_periods, kinematics
from ...core.project import INACTIVE_STATUSES, result_columns
from ...core.video import VideoSource
from ..widgets import PlotCanvas, error_box, run_with_progress
from ._results_cache import RowsLoader, info_columns
from .base import Page

GENERAL_PREFIXES = ("Test duration", "Detection", "Total distance", "Mean speed", "Max speed", "Time mobile",
                    "Time immobile", "Immobile episodes", "Latency to first immobility", "Time freezing",
                    "Freezing", "Latency to first freezing", "Mean freezing episode", "Mean motion",
                    "Path efficiency", "Absolute turn angle", "Meander", "Rotations", "Mean distance from wall",
                    "Thigmotaxis", "Time outside arena", "Time not detected")
CATEGORY_ORDER = ["Information", "General", "Zones", "Points of interest", "Lines", "Test-specific", "Behaviours",
                  "Social", "Other"]
FIG_FILTER = "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)"
TABLE_FILTER = "CSV file (*.csv);;Tab-separated text (*.tsv *.txt);;Excel workbook (*.xlsx)"
POSITION = "Position (all time)"


def is_number(v) -> bool:
    return isinstance(v, (int, float, np.number)) and not isinstance(v, (bool, np.bool_))


def fmt_value(v) -> str:
    if v is None:
        return ""
    if is_number(v):
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        return f"{float(v):.3f}" if math.isfinite(v) else ""
    return str(v)


def raw_value(v) -> str:
    """Full-precision text for exports / clipboard."""
    if is_number(v) and not isinstance(v, (int, np.integer)):
        return f"{float(v):.6g}" if math.isfinite(v) else ""
    return "" if v is None else str(v)


def _names(project) -> dict[str, set]:
    zones, points, lines = set(), set(), set()
    for a in (project.apparatus if project else []):
        zones.update(z.name for z in a.zones)
        zones.update(g.name for g in a.groups)
        points.update(p.name for p in a.points)
        lines.update(l.name for l in a.lines)
    beh = {b.name for b in (project.behaviours if project else [])}
    return {"zones": zones, "points": points, "lines": lines, "behaviours": beh}


def measure_category(col: str, names: dict) -> tuple[str, str]:
    """(category, sub-category) of a measure column, inferred from its "Name: measure" prefix."""
    if ": " in col:
        prefix = col.split(": ", 1)[0]
        for key, cat in (("zones", "Zones"), ("points", "Points of interest"), ("lines", "Lines"),
                         ("behaviours", "Behaviours")):
            if prefix in names[key]:
                return cat, prefix
        if prefix.startswith("Animal "):
            return "Social", ""
        return "Other", prefix
    if col.startswith(GENERAL_PREFIXES):
        return "General", ""
    return "Test-specific", ""


def numeric_columns(rows: list[dict], cols: list[str]) -> list[str]:
    return [c for c in cols if any(is_number(r.get(c)) for r in rows)]


def figure_to_clipboard(fig: Figure, dpi: int = 150) -> bool:
    if fig is None:
        return False
    img = QImage.fromData(plots.fig_to_png(fig, dpi=dpi))
    QGuiApplication.clipboard().setImage(img)
    return not img.isNull()


def _combo(items, data=None, tip: str = "") -> QComboBox:
    c = QComboBox()
    for i, it in enumerate(items):
        c.addItem(it, data[i] if data else it)
    if tip:
        c.setToolTip(tip)
    return c


# ------------------------------------------------------------------ table model
class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.columns: list[str] = []

    def set_data(self, rows: list[dict], columns: list[str]):
        self.beginResetModel()
        self.rows = rows
        self.columns = columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def value(self, row: int, col: int):
        return self.rows[row].get(self.columns[col])

    def sort_key(self, row: int, col: int):
        v = self.value(row, col)
        if is_number(v):
            return (0, float(v), "") if math.isfinite(v) else (2, 0.0, "")
        if v is None or v == "":
            return (2, 0.0, "")
        return (1, 0.0, str(v).lower())

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.value(index.row(), index.column())
        if role == Qt.DisplayRole:
            return fmt_value(v)
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if is_number(v) else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and is_number(v) and not isinstance(v, (int, np.integer)):
            return raw_value(v)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            c = self.columns[section]
            if role == Qt.DisplayRole:
                if ": " in c:
                    return c.replace(": ", ":\n", 1)
                return c.replace(" (", "\n(", 1) if len(c) > 12 else c
            if role == Qt.ToolTipRole:
                return c
        elif orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None


class ResultsProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filters: dict[str, str] = {}

    def set_filters(self, filters: dict):
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            self.filters = {k: v for k, v in filters.items() if v is not None}
            self.endFilterChange()
        else:
            self.filters = {k: v for k, v in filters.items() if v is not None}
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        return all(str(r.get(k, "")) == v for k, v in self.filters.items())

    def lessThan(self, left, right):
        m = self.sourceModel()
        return m.sort_key(left.row(), left.column()) < m.sort_key(right.row(), right.column())


# ------------------------------------------------------------------ dialogs
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
        from ...core.videoexport import OverlayOptions

        super().__init__(parent)
        self.setWindowTitle("Export video with overlays")
        o = OverlayOptions()
        f = QFormLayout(self)
        self.zones = QCheckBox("Zones, points and lines")
        self.zones.setChecked(o.zones)
        self.trail = _combo(["None", "Last 2 s", "Last 5 s", "Last 15 s", "Whole track so far"], [0, 2, 5, 15, -1])
        self.trail.setCurrentIndex(2)
        self.trail_color = _combo(["Speed", "Time", "Animal colour"], ["speed", "time", "fixed"])
        self.body = QCheckBox("Centre, head and tail")
        self.body.setChecked(True)
        self.beh = QCheckBox("Scored behaviours and freezing")
        self.beh.setChecked(True)
        self.stamp = QCheckBox("Time stamp and test caption")
        self.stamp.setChecked(True)
        self.speed = _combo(["0.5×", "1×", "2×", "4×", "8×"], [0.5, 1.0, 2.0, 4.0, 8.0])
        self.speed.setCurrentIndex(1)
        self.scale = _combo(["100 %", "75 %", "50 %"], [1.0, 0.75, 0.5])
        for w in (self.zones, self.body, self.beh, self.stamp):
            f.addRow("", w)
        f.addRow("Track trail", self.trail)
        f.addRow("Trail colour", self.trail_color)
        f.addRow("Playback speed", self.speed)
        f.addRow("Size", self.scale)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def options(self):
        from ...core.videoexport import OverlayOptions

        return OverlayOptions(zones=self.zones.isChecked(), trail_s=float(self.trail.currentData()),
                              trail_color=self.trail_color.currentData(), body_points=self.body.isChecked(),
                              behaviours=self.beh.isChecked(), freezing=self.beh.isChecked(),
                              timestamp=self.stamp.isChecked(), info=self.stamp.isChecked(),
                              speed=float(self.speed.currentData()), scale=float(self.scale.currentData()))


# ------------------------------------------------------------------ charts tab
class ChartsPanel(QWidget):
    """Per-frame parameters of one test over time, with zoom/pan, interval measurement and peak finding."""

    def __init__(self, page: "ResultsPage"):
        super().__init__()
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

        self.page = page
        self.data: dict = {}
        self.track = None
        self.app = None
        self.test = None
        self._loading = False
        self._spans = []
        self.measurements: list[dict] = []
        self.peaks: dict[str, list] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.redraw)

        # ---- controls ------------------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        f = QFormLayout()
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.test_combo = QComboBox()
        self.test_combo.setMinimumContentsLength(14)
        self.test_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.test_combo.currentIndexChanged.connect(self._test_changed)
        self.animal_combo = QComboBox()
        self.animal_combo.currentIndexChanged.connect(self._test_changed)
        self.period_combo = QComboBox()
        self.period_combo.setToolTip("Zoom the time axis to a time period")
        self.period_combo.currentIndexChanged.connect(self._schedule)
        f.addRow("Test", self.test_combo)
        f.addRow("Animal", self.animal_combo)
        f.addRow("Period", self.period_combo)
        ll.addLayout(f)
        ll.addWidget(QLabel("<b>Parameters</b> (up to 10)"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parameters…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        ll.addWidget(self.search)
        self.param_list = QListWidget()
        self.param_list.itemChanged.connect(lambda *_: self._loading or self._schedule())
        ll.addWidget(self.param_list, 3)
        ll.addWidget(QLabel("<b>Zone occupancy bands</b>"))
        self.band_list = QListWidget()
        self.band_list.setMaximumHeight(110)
        self.band_list.itemChanged.connect(lambda *_: self._loading or self._schedule())
        ll.addWidget(self.band_list, 1)
        self.events_check = QCheckBox("Show scored events")
        self.events_check.setChecked(True)
        self.events_check.toggled.connect(self._schedule)
        ll.addWidget(self.events_check)
        left.setFixedWidth(270)

        # ---- figure -------------------------------------------------------------
        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        self.measure_check = QCheckBox("Measure interval")
        self.measure_check.setToolTip("Drag across a chart to measure the selected time interval")
        self.measure_check.toggled.connect(self._attach_spans)
        peaks = QPushButton("Find peaks")
        peaks.setToolTip("Mark peaks of the charted (non on/off) parameters")
        peaks.clicked.connect(lambda: self.find_peaks())
        save = QPushButton("Save image…")
        save.clicked.connect(lambda: self.save_image())
        copy = QPushButton("Copy image")
        copy.clicked.connect(self.copy_image)
        data = QPushButton("Export data…")
        data.setToolTip("Save the charted series (one row per frame) as CSV / tab-separated text")
        data.clicked.connect(lambda: self.export_data())
        tb = QHBoxLayout()
        tb.addWidget(self.toolbar)
        tb.addStretch()
        for w in (self.measure_check, peaks, save, copy, data):
            tb.addWidget(w)
        self.meas_table = QTableWidget(0, 8)
        self.meas_table.setHorizontalHeaderLabels(["Parameter", "From (s)", "To (s)", "Mean", "SD", "Min", "Max",
                                                   "Change"])
        self.meas_table.verticalHeader().hide()
        self.meas_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.meas_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.meas_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.meas_table.verticalHeader().setDefaultSectionSize(22)
        self.meas_table.setMaximumHeight(130)
        self.meas_lbl = QLabel("Tick “Measure interval” and drag across the chart to measure; use the toolbar "
                               "to zoom and pan.")
        self.meas_lbl.setStyleSheet("color:palette(mid);")
        self.meas_lbl.setWordWrap(True)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(tb)
        rl.addWidget(self.canvas, 1)
        rl.addWidget(self.meas_lbl)
        rl.addWidget(self.meas_table)
        lay = QHBoxLayout(self)
        lay.addWidget(left)
        lay.addWidget(right, 1)

    # ------------------------------------------------------------------ data
    def refresh_tests(self):
        p = self.page.project
        cur = self.test_combo.currentData()
        self._loading = True
        self.test_combo.clear()
        if p is not None:
            for t in p.tests:
                if p.has_track(t) and t.status not in INACTIVE_STATUSES:
                    a = p.get_animal(t.animal_id)
                    grp = f" · {a.group}" if a and a.group and not getattr(p, "blind", False) else ""
                    self.test_combo.addItem(f"Test {t.id} · {t.animal_id}{grp}", t.id)
        i = self.test_combo.findData(cur)
        self.test_combo.setCurrentIndex(i if i >= 0 else (0 if self.test_combo.count() else -1))
        self._loading = False
        self._test_changed(force=True)

    def select_test(self, test_id):
        i = self.test_combo.findData(test_id)
        if i >= 0 and i != self.test_combo.currentIndex():
            self.test_combo.setCurrentIndex(i)

    def _test_changed(self, *_, force: bool = False):
        if self._loading and not force:
            return
        p = self.page.project
        tid = self.test_combo.currentData()
        test = p.get_test(tid) if p is not None and tid is not None else None
        sender_is_test = self.sender() is self.test_combo or force
        if test is None:
            self.test = self.track = None
            self.redraw()
            return
        self._loading = True
        if sender_is_test or self.animal_combo.count() == 0:
            ids = [test.animal_id] + list(test.extra_animals)
            self.animal_combo.clear()
            for i, a in enumerate(ids[:max(1, test.n_animals)]):
                self.animal_combo.addItem(str(a), i)
        self.animal_combo.setEnabled(self.animal_combo.count() > 1)
        ai = self.animal_combo.currentData() or 0
        tracks = p.load_tracks(test)
        self.test = test
        self.track = tracks[ai] if ai < len(tracks) else (tracks[0] if tracks else None)
        self.app = charts.apparatus_of_test(p, test)
        self.others = [o for j, o in enumerate(tracks) if j != ai]
        self.beh = [asdict(b) for b in p.behaviours]
        checked = set(self.checked_params()) or {"Speed", "Freezing"}
        bands = set(self.checked_bands())
        self.param_list.clear()
        self.band_list.clear()
        if self.track is not None and self.app is not None:
            group = None
            for prm in charts.parameters(self.app, self.track, self.beh, len(self.others)):
                if prm.group != group:
                    group = prm.group
                    head = QListWidgetItem(group)
                    head.setFlags(Qt.ItemIsEnabled)
                    fnt = head.font()
                    fnt.setBold(True)
                    head.setFont(fnt)
                    self.param_list.addItem(head)
                it = QListWidgetItem(prm.label)
                it.setData(Qt.UserRole, prm.name)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if prm.name in checked else Qt.Unchecked)
                self.param_list.addItem(it)
            for z in [z.name for z in self.app.zones] + [g.name for g in self.app.groups]:
                it = QListWidgetItem(z)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if z in bands else Qt.Unchecked)
                self.band_list.addItem(it)
        cur = self.period_combo.currentText()
        self.period_combo.clear()
        self.period_combo.addItem("Whole test", None)
        if self.track is not None and len(self.track):
            try:
                periods = all_periods(self.track, self.app, p.analysis_for(test), None, test.events,
                                      test.io_events, test.zone_overrides)
            except Exception:
                periods = []
            for label, a, b in periods:
                self.period_combo.addItem(label, (a, b))
        self.period_combo.setCurrentIndex(max(0, self.period_combo.findText(cur)))
        self._loading = False
        self._filter(self.search.text())
        self.redraw()

    def checked_params(self) -> list[str]:
        out = []
        for i in range(self.param_list.count()):
            it = self.param_list.item(i)
            if it.data(Qt.UserRole) and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def checked_bands(self) -> list[str]:
        return [self.band_list.item(i).text() for i in range(self.band_list.count())
                if self.band_list.item(i).checkState() == Qt.Checked]

    def set_selection(self, test_id=None, params: list[str] | None = None, bands: list[str] | None = None,
                      events: bool | None = None):
        """Programmatic setup (tests / scripting)."""
        if test_id is not None:
            self.select_test(test_id)
        self._loading = True
        if params is not None:
            for i in range(self.param_list.count()):
                it = self.param_list.item(i)
                if it.data(Qt.UserRole):
                    it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in params else Qt.Unchecked)
        if bands is not None:
            for i in range(self.band_list.count()):
                it = self.band_list.item(i)
                it.setCheckState(Qt.Checked if it.text() in bands else Qt.Unchecked)
        if events is not None:
            self.events_check.setChecked(events)
        self._loading = False
        self.redraw()

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.param_list.count()):
            it = self.param_list.item(i)
            if it.data(Qt.UserRole):
                it.setHidden(bool(t) and t not in it.text().lower())
            else:
                it.setHidden(bool(t))

    def _schedule(self, *_):
        if not self._loading:
            self._timer.start()

    # ------------------------------------------------------------------ drawing
    def redraw(self):
        self._timer.stop()
        self.figure.clear()
        self.peaks = {}
        names = self.checked_params()[:10]
        if self.track is None or self.app is None:
            ax = self.figure.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "No tracked tests", ha="center", va="center", color="#64748b", transform=ax.transAxes)
            self.canvas.draw_idle()
            return
        p = self.page.project
        s = p.analysis_for(self.test)
        events = self.test.events if (self.animal_combo.currentData() or 0) == 0 else []
        try:
            self.data = charts.compute(self.track, self.app, names, s, events, self.beh, self.others or None)
        except Exception as e:
            self.page.main.status(f"Chart failed: {e}")
            self.data = {}
            names = []
        title = self.test_combo.currentText()
        charts.chart_figure(self.track, self.app, names, s, events, self.beh, bands=self.checked_bands(),
                            show_events=self.events_check.isChecked(), t_range=self.period_combo.currentData(),
                            data=self.data, title=title, fig=self.figure, other_tracks=self.others or None)
        self._attach_spans()
        self.canvas.draw_idle()
        self.toolbar.update()

    def _attach_spans(self, *_):
        from matplotlib.widgets import SpanSelector

        for sp in self._spans:
            sp.set_active(False)
        self._spans = []
        if not self.measure_check.isChecked():
            return
        for ax in self.figure.axes:
            self._spans.append(SpanSelector(ax, self.measure, "horizontal", useblit=False, interactive=False,
                                            props=dict(alpha=0.2, facecolor="#0ea5e9")))

    def measure(self, t0: float, t1: float) -> list[dict]:
        """Measure every charted parameter between t0 and t1 (also shown in the table under the chart)."""
        if self.track is None:
            return []
        rows = []
        for n, v in self.data.items():
            rows.append({"parameter": n, **charts.measure_interval(self.track.t, v, t0, t1)})
        self.measurements = rows
        self.meas_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["parameter"], r["t0"], r["t1"], r["mean"], r["sd"], r["min"], r["max"], r["change"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v if isinstance(v, str) else fmt_value(float(v)))
                if j:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 6 and math.isfinite(r["t_max"]):
                    it.setToolTip(f"at {r['t_max']:.2f} s")
                self.meas_table.setItem(i, j, it)
        self.meas_lbl.setText(f"Interval {min(t0, t1):.2f}–{max(t0, t1):.2f} s ({abs(t1 - t0):.2f} s)")
        for ax in self.figure.axes:
            for patch in [p for p in ax.patches if p.get_gid() == "measure"]:
                patch.remove()
            ax.axvspan(min(t0, t1), max(t0, t1), color="#0ea5e9", alpha=0.12, gid="measure")
        self.canvas.draw_idle()
        return rows

    def find_peaks(self, prominence: float | None = None) -> dict[str, list]:
        if self.track is None:
            return {}
        info = {p.name: p for p in charts.parameters(self.app, self.track, self.beh, len(self.others))}
        out = {}
        axes = self.figure.axes
        for i, (n, v) in enumerate(self.data.items()):
            if n in info and info[n].kind != charts.VALUE:
                continue
            pk = charts.find_peaks(self.track.t, v, prominence)
            out[n] = pk
            if i < len(axes) and pk:
                axes[i].plot([a for a, _ in pk], [b for _, b in pk], "v", color="#dc2626", ms=4, zorder=5)
        self.peaks = out
        self.meas_lbl.setText("Peaks: " + "; ".join(f"{n}: {len(p)}" for n, p in out.items()) if out else
                              "No continuous parameters charted")
        self.canvas.draw_idle()
        return out

    # ------------------------------------------------------------------ output
    def save_image(self, path: str | None = None):
        if path is None:
            p = self.page.project
            base = str(p.exports_dir() / f"chart_test_{self.test.id if self.test else 0}.png") if p and p.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Save chart", base, FIG_FILTER)
            if not path:
                return
        if Path(path).suffix.lower() not in (".png", ".pdf", ".svg"):
            path += ".png"
        try:
            self.figure.savefig(path, dpi=200, bbox_inches="tight")
        except Exception as e:
            error_box(self, "Save chart", e)
            return
        self.page.main.status(f"Saved {path}")
        return path

    def copy_image(self):
        if figure_to_clipboard(self.figure):
            self.page.main.status("Chart copied to the clipboard")

    def export_data(self, path: str | None = None):
        if self.track is None or not self.data:
            return
        if path is None:
            p = self.page.project
            base = str(p.exports_dir() / f"chart_test_{self.test.id}.csv") if p and p.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Export chart data", base,
                                                  "CSV file (*.csv);;Tab-separated text (*.tsv *.txt)")
            if not path:
                return
        if Path(path).suffix.lower() not in (".csv", ".tsv", ".txt"):
            path += ".csv"
        labels = {p.name: p.label for p in charts.parameters(self.app, self.track, self.beh, len(self.others))}
        cols = ["Time (s)"] + [labels.get(n, n) for n in self.data]
        rows = [dict(zip(cols, vals)) for vals in zip(self.track.t, *self.data.values())]
        try:
            write_table(rows, path, cols)
        except Exception as e:
            error_box(self, "Export chart data", e)
            return
        self.page.main.status(f"Exported {path}")
        return path


# ------------------------------------------------------------------ page
class ResultsPage(Page):
    title = "Results"

    def __init__(self, main):
        super().__init__(main)
        self._workers: list = []  # background jobs still running (exports, reports, heat maps)
        self.rows: list[dict] = []
        self.segmented = False
        self._names = _names(None)
        self._frames: dict[int, object] = {}
        self._detail: dict | None = None
        self._detail_key = None
        self._stale: set[int] = set()
        self._loading_tree = False
        self.loader = RowsLoader(self)
        self.loader.loaded.connect(self._rows_loaded)
        self.loader.failed.connect(self._load_failed)
        self.loader.progress.connect(lambda f: self.progress.setValue(int(f * 100)))
        self.loader.busy_changed.connect(self._busy_changed)

        # ---- toolbar ---------------------------------------------------------
        self.seg_check = QCheckBox("Show time periods")
        self.seg_check.setToolTip("Also show results per time bin / custom period (set on the Experiment page)")
        self.seg_check.toggled.connect(self._seg_toggled)
        self.period_combo = QComboBox()
        self.period_combo.setMinimumWidth(130)
        self.period_combo.currentIndexChanged.connect(self._apply_filters)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(110)
        self.group_combo.currentIndexChanged.connect(self._apply_filters)
        self.stage_combo = QComboBox()
        self.stage_combo.setMinimumWidth(110)
        self.stage_combo.currentIndexChanged.connect(self._apply_filters)
        recalc = QPushButton("Recalculate")
        recalc.setToolTip("Recompute all results from the tracks (e.g. after changing analysis settings)")
        recalc.clicked.connect(lambda: self.reload(force=True))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(160)
        self.progress.setFormat("Calculating… %p%")
        self.progress.hide()
        self.count_lbl = QLabel()
        self.count_lbl.setStyleSheet("color:palette(mid);")
        export = QPushButton("Export")
        em = QMenu(export)
        em.addAction("CSV file…", self.export_csv)
        em.addAction("Tab-separated text…", self.export_tsv)
        em.addAction("Excel workbook (.xlsx)…", self.export_xlsx)
        em.addAction("Selected cells…", self.export_selection)
        em.addSeparator()
        em.addAction("Experiment as XML (with raw tracks)…", self.export_xml)
        em.addAction("Raw data per test (CSV)…", self.export_raw)
        export.setMenu(em)
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the selected cells (or the whole shown table) as tab-separated text for Excel / Prism")
        copy.clicked.connect(self.copy_to_clipboard)
        report = QPushButton("HTML report…")
        report.clicked.connect(self.html_report)
        bar = QHBoxLayout()
        bar.addWidget(self.seg_check)
        bar.addWidget(self.period_combo)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Group"))
        bar.addWidget(self.group_combo)
        bar.addWidget(QLabel("Stage"))
        bar.addWidget(self.stage_combo)
        bar.addSpacing(12)
        bar.addWidget(recalc)
        bar.addWidget(self.progress)
        bar.addWidget(self.count_lbl)
        bar.addStretch()
        bar.addWidget(export)
        bar.addWidget(copy)
        bar.addWidget(report)

        # ---- measure chooser ------------------------------------------------
        chooser = QWidget()
        cl = QVBoxLayout(chooser)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel("<b>Measures</b>"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search measures…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tree)
        cl.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemChanged.connect(self._tree_changed)
        cl.addWidget(self.tree, 1)
        sb = QHBoxLayout()
        b_all = QPushButton("Select all")
        b_all.clicked.connect(lambda: self.set_all_measures(True))
        b_none = QPushButton("Select none")
        b_none.clicked.connect(lambda: self.set_all_measures(False))
        sb.addWidget(b_all)
        sb.addWidget(b_none)
        cl.addLayout(sb)
        self.measure_lbl = QLabel()
        self.measure_lbl.setStyleSheet("color:palette(mid);")
        cl.addWidget(self.measure_lbl)
        self._tree_timer = QTimer(self)
        self._tree_timer.setSingleShot(True)
        self._tree_timer.setInterval(0)
        self._tree_timer.timeout.connect(self._update_columns)

        # ---- table ---------------------------------------------------------------
        self.model = ResultsModel(self)
        self.proxy = ResultsProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setMinimumWidth(28)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setResizeContentsPrecision(60)
        hh.setMinimumSectionSize(48)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._select_changed())
        self.table.doubleClicked.connect(self._open_test)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.empty_lbl = QLabel()
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setStyleSheet("color:palette(mid);font-size:14px;")
        self.empty_lbl.hide()
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.table, 1)
        tl.addWidget(self.empty_lbl, 1)

        # ---- detail panel ---------------------------------------------------------
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        self.detail_lbl = QLabel("Select a row to see its track")
        self.detail_lbl.setStyleSheet("font-weight:bold;")
        dl.addWidget(self.detail_lbl)
        self.tabs = QTabWidget()
        self.track_canvas = PlotCanvas()
        self.heat_canvas = PlotCanvas()
        self.speed_canvas = PlotCanvas()
        self.groups_canvas = PlotCanvas()
        for c, name in ((self.track_canvas, "Track"), (self.heat_canvas, "Heat map"),
                        (self.speed_canvas, "Speed"), (self.groups_canvas, "Groups")):
            c.setMinimumSize(260, 260)
            self.tabs.addTab(c, name)
        self.tabs.setTabToolTip(2, "Speed over time; freezing episodes shaded")
        self.tabs.setTabToolTip(3, "Average heat map per group (click “Group heat maps”)")
        self.tabs.currentChanged.connect(self._tab_changed)
        dl.addWidget(self.tabs, 1)
        dl.addWidget(self._build_plot_options())
        db = QHBoxLayout()
        gh = QPushButton("Group heat maps")
        gh.setToolTip("Average heat map of each group (tests shown in the table), on a common scale, with each "
                      "test's alignment applied")
        gh.clicked.connect(self.group_heatmaps)
        ot = QPushButton("Open test")
        ot.setToolTip("Show the selected test in the test viewer")
        ot.clicked.connect(lambda: self._open_test())
        vid = QPushButton("Export video…")
        vid.setToolTip("Save the selected test's video with zones, track, behaviours and time stamp drawn on it")
        vid.clicked.connect(lambda: self.export_video())
        sf = QPushButton("Save figure…")
        sf.clicked.connect(lambda: self.save_figure())
        cf = QPushButton("Copy")
        cf.setToolTip("Copy the figure to the clipboard")
        cf.clicked.connect(self.copy_figure)
        db.addWidget(gh)
        db.addWidget(ot)
        db.addWidget(vid)
        db.addStretch()
        db.addWidget(sf)
        db.addWidget(cf)
        dl.addLayout(db)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(chooser)
        split.addWidget(tw)
        split.addWidget(detail)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([215, 560, 425])
        split.setChildrenCollapsible(False)
        self.charts = ChartsPanel(self)
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(split, "Results table")
        self.main_tabs.addTab(self.charts, "Charts")
        self.main_tabs.setTabToolTip(1, "Per-frame parameters over time (speed, distances, zones, head angle…) "
                                        "with zone bands and scored events")
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.main_tabs, 1)

    def _build_plot_options(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 2, 0, 2)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(3)
        self.part_combo = _combo(["Centre", "Head"], ["centre", "head"], "Body part drawn in track plots and heat maps")
        self.color_combo = _combo(["Time", "Speed", "Single colour"], ["time", "speed", "none"],
                                  "Colour the track by time, speed or any per-frame parameter")
        self.color_combo.setMinimumContentsLength(10)
        self.color_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.markers_check = QCheckBox("Markers")
        self.markers_check.setToolTip("Mark freezing episodes and scored behaviours on the track")
        self.markers_check.setChecked(True)
        self.split_check = QCheckBox("Split by period")
        self.split_check.setToolTip("One small track plot per time period (time bins / custom periods; "
                                    "quarters of the test if none are set)")
        self.heat_of = _combo([POSITION], [None], "Heat map of the position, or only of frames where a behaviour "
                                                  "occurred (freezing, immobility, scored behaviours…)")
        self.heat_of.setMinimumContentsLength(10)
        self.heat_of.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.heat_norm = _combo(["Automatic", "% of time", "Relative", "Fixed max"],
                                ["auto", "percent", "relative", "fixed"],
                                "Colour scale: each map scaled to its own maximum, % of the mapped time per bin, "
                                "relative to the maximum, or a fixed maximum for comparing tests")
        self.heat_max = QDoubleSpinBox()
        self.heat_max.setRange(0.001, 1e6)
        self.heat_max.setDecimals(3)
        self.heat_max.setValue(1.0)
        self.heat_max.setToolTip("Maximum of the colour scale (same units as the colour bar)")
        self.heat_max.setEnabled(False)
        self.align_combo = _combo(list(plots.TRANSFORMS.values()), list(plots.TRANSFORMS),
                                  "Orientation of this test in group heat maps (rotate / mirror so that equivalent "
                                  "parts of the apparatus line up between tests)")
        g.addWidget(QLabel("Body part"), 0, 0)
        g.addWidget(self.part_combo, 0, 1)
        g.addWidget(QLabel("Colour"), 0, 2)
        g.addWidget(self.color_combo, 0, 3)
        g.addWidget(self.markers_check, 1, 0, 1, 2)
        g.addWidget(self.split_check, 1, 2, 1, 2)
        g.addWidget(QLabel("Heat map of"), 2, 0)
        g.addWidget(self.heat_of, 2, 1, 1, 3)
        g.addWidget(QLabel("Scale"), 3, 0)
        sc = QHBoxLayout()
        sc.addWidget(self.heat_norm, 1)
        sc.addWidget(self.heat_max)
        g.addLayout(sc, 3, 1, 1, 3)
        g.addWidget(QLabel("Align"), 4, 0)
        g.addWidget(self.align_combo, 4, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        self._track_opts = [self.color_combo, self.markers_check, self.split_check]
        self._heat_opts = [self.heat_of, self.heat_norm, self.heat_max, self.align_combo]
        for c in (self.part_combo, self.color_combo, self.heat_of, self.heat_norm):
            c.currentIndexChanged.connect(self._options_changed)
        for c in (self.markers_check, self.split_check):
            c.toggled.connect(self._options_changed)
        self.heat_max.valueChanged.connect(self._options_changed)
        self.align_combo.currentIndexChanged.connect(self._align_changed)
        return w

    # ------------------------------------------------------------------ project
    def set_project(self, project):
        self.loader.cancel()
        self.rows = []
        self._frames = {}
        self._detail = None
        self._detail_key = None
        self._names = _names(project)
        self.seg_check.blockSignals(True)
        self.seg_check.setChecked(False)
        self.seg_check.blockSignals(False)
        self.segmented = False
        self.model.set_data([], [])
        self.tree.clear()
        for c in (self.track_canvas, self.heat_canvas, self.speed_canvas, self.groups_canvas):
            c.set_figure(Figure(figsize=(3, 3)))
        self.detail_lbl.setText("Select a row to see its track")
        self._fill_filter_combos()
        self._charts_stale = True
        if self.main_tabs.currentIndex() == 1:
            self.charts.refresh_tests()
            self._charts_stale = False

    def on_show(self):
        if self.project is None:
            return
        self._names = _names(self.project)
        self._charts_stale = True
        if self.main_tabs.currentIndex() == 1:
            self._main_tab_changed(1)
        self.reload()

    def _main_tab_changed(self, i):
        if i == 1 and getattr(self, "_charts_stale", True):
            self._charts_stale = False
            self.charts.refresh_tests()
            r = self.current_row()
            if r is not None:
                self.charts.select_test(r.get("Test"))

    def shutdown(self):
        self.loader.shutdown()

    def reload(self, force: bool = False):
        if self.project is None:
            return
        if force:
            self._frames = {}
            self._detail_key = None
        self.loader.request(self.project, self.seg_check.isChecked(), force)

    def wait_loaded(self, timeout_ms: int = 120000):
        """Block until a pending calculation has been delivered (used by tests)."""
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtWidgets import QApplication

        t = QElapsedTimer()
        t.start()
        while self.loader.busy and t.elapsed() < timeout_ms:
            self.loader.wait(50)
            QApplication.processEvents()

    def _busy_changed(self, busy: bool):
        self.progress.setValue(0)
        self.progress.setVisible(busy)
        if busy:
            self.count_lbl.setText("")

    def _load_failed(self, msg):
        self.main.status(f"Results: {msg}")
        error_box(self, "Results", msg)

    def _seg_toggled(self, on):
        self.period_combo.setEnabled(on)
        self.reload()

    # ------------------------------------------------------------------ data
    def _rows_loaded(self, rows, segmented):
        self.rows = rows
        self.segmented = segmented
        self._detail_key = None
        self._names = _names(self.project)
        self._fill_filter_combos()
        self._build_tree()
        self._update_columns()
        if not rows:
            self.empty_lbl.setText("No results yet.\n\nTrack the tests (Tests page) or score behaviours "
                                   "(Test view) to see measures here.")
        self.empty_lbl.setVisible(not rows)
        self.table.setVisible(bool(rows))
        if rows and not self.table.selectionModel().hasSelection():
            self.table.selectRow(0)

    def all_columns(self) -> list[str]:
        return result_columns(self.rows)

    def measure_columns(self) -> list[str]:
        info = set(info_columns(self.project))
        return [c for c in self.all_columns() if c not in info]

    def _info_shown(self) -> list[str]:
        cols = self.all_columns()
        out = []
        for c in info_columns(self.project):
            if c not in cols or (c == "Period" and not self.segmented):
                continue
            if c not in ("Test", "Animal") and not any(str(r.get(c, "")).strip() for r in self.rows):
                continue
            if c in self.hidden:
                continue
            out.append(c)
        return out

    @property
    def hidden(self) -> set:
        p = self.project
        if p is None:
            return set()
        if not isinstance(getattr(p, "ui_hidden_measures", None), set):
            p.ui_hidden_measures = set()
        return p.ui_hidden_measures

    def visible_measures(self) -> list[str]:
        hidden = self.hidden
        return [c for c in self.measure_columns() if c not in hidden]

    def shown_columns(self) -> list[str]:
        return self._info_shown() + self.visible_measures()

    def set_visible_measures(self, measures):
        """Show exactly these measure columns (others hidden)."""
        keep = set(measures)
        self.hidden.difference_update(self.measure_columns())
        self.hidden.update(c for c in self.measure_columns() if c not in keep)
        self._build_tree()
        self._update_columns()

    def set_all_measures(self, on: bool):
        """Check/uncheck all measures matching the search box."""
        t = self.search.text().strip().lower()
        for c in self.measure_columns():
            if not t or t in c.lower():
                (self.hidden.discard if on else self.hidden.add)(c)
        self._build_tree()
        self._update_columns()

    def _update_columns(self):
        cur = self._current_key()
        cols = self.shown_columns()
        sort_col = self.proxy.sortColumn()
        sort_name = self.model.columns[sort_col] if 0 <= sort_col < len(self.model.columns) else None
        self.model.set_data(self.rows, cols)
        hh = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        for i in range(len(cols)):
            hh.resizeSection(i, max(48, min(hh.sectionSize(i), 140)))
        if sort_name in cols:
            self.table.sortByColumn(cols.index(sort_name), self.proxy.sortOrder())
        elif sort_col >= 0:
            self.proxy.sort(-1)
            hh.setSortIndicator(-1, Qt.AscendingOrder)
        self._restore_selection(cur)
        n_m = len(self.measure_columns())
        self.measure_lbl.setText(f"{len(self.visible_measures())} of {n_m} measures shown")
        self._update_count()

    def _update_count(self):
        if self.rows:
            self.count_lbl.setText(f"{self.proxy.rowCount()} of {len(self.rows)} rows")

    # ------------------------------------------------------------------ filters
    def _fill_filter_combos(self):
        p = self.project
        groups = [g.name for g in p.groups] if p else []
        stages = list(p.stages) if p else []
        periods = []
        for r in self.rows:
            for key, lst in (("Group", groups), ("Stage", stages), ("Period", periods)):
                v = str(r.get(key, ""))
                if v and v not in lst:
                    lst.append(v)
        for combo, items, allname in ((self.group_combo, groups, "All groups"),
                                      (self.stage_combo, stages, "All stages"),
                                      (self.period_combo, periods, "All periods")):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(allname, None)
            for v in items:
                combo.addItem(v, v)
            i = combo.findData(cur) if cur is not None else 0
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)
        self.period_combo.setEnabled(self.seg_check.isChecked())
        self._apply_filters()

    def _apply_filters(self, *_):
        f = {"Group": self.group_combo.currentData(), "Stage": self.stage_combo.currentData()}
        if self.seg_check.isChecked():
            f["Period"] = self.period_combo.currentData()
        self.proxy.set_filters(f)
        self._update_count()

    def shown_rows(self, selected_only: bool = False) -> list[dict]:
        """Rows currently shown in the table, in display order (optionally only the selected ones)."""
        if selected_only:
            idx = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
            if len(idx) > 1:
                return [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()] for i in idx]
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
                for i in range(self.proxy.rowCount())]

    def selection_range(self) -> tuple[list[dict], list[str]] | None:
        """(rows, columns) spanned by the selected cells, or None if fewer than two cells (or a single whole row)
        are selected."""
        sel = self.table.selectionModel().selectedIndexes()
        if len(sel) < 2:
            return None
        rows = sorted({i.row() for i in sel})
        cols = sorted({i.column() for i in sel})
        if len(rows) == 1 and len(cols) == len(self.model.columns):
            return None
        return ([self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()] for i in rows],
                [self.model.columns[c] for c in cols])

    # ------------------------------------------------------------------ chooser tree
    def _build_tree(self):
        self._loading_tree = True
        self.tree.clear()
        hidden = self.hidden
        cats: dict[str, dict[str, list[str]]] = {}
        info = [c for c in info_columns(self.project) if c in self.all_columns() and c != "Test"
                and (c != "Period" or self.segmented)]
        if info:
            cats["Information"] = {"": info}
        for c in self.measure_columns():
            cat, sub = measure_category(c, self._names)
            cats.setdefault(cat, {}).setdefault(sub, []).append(c)
        expanded = len(cats) <= 2
        for cat in CATEGORY_ORDER:
            if cat not in cats:
                continue
            top = QTreeWidgetItem([cat])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            self.tree.addTopLevelItem(top)
            for sub, cols in cats[cat].items():
                parent = top
                if sub:
                    parent = QTreeWidgetItem(top, [sub])
                    parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                for c in cols:
                    label = c.split(": ", 1)[1] if sub and c.startswith(sub + ": ") else c
                    it = QTreeWidgetItem(parent, [label])
                    it.setData(0, Qt.UserRole, c)
                    it.setToolTip(0, c)
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Unchecked if c in hidden else Qt.Checked)
            top.setExpanded(expanded or cat == "General")
            if cat == "Information":
                top.setToolTip(0, "Identification columns (empty columns are hidden automatically)")
        self._loading_tree = False
        self._filter_tree(self.search.text())

    def _tree_changed(self, item, col):
        if self._loading_tree:
            return
        c = item.data(0, Qt.UserRole)
        if c is None:
            return
        if item.checkState(0) == Qt.Checked:
            self.hidden.discard(c)
        else:
            self.hidden.add(c)
        self._tree_timer.start()

    def _filter_tree(self, text):
        t = text.strip().lower()

        def walk(item) -> bool:
            if item.childCount() == 0:
                full = item.data(0, Qt.UserRole) or item.text(0)
                vis = not t or t in full.lower()
            else:
                vis = False
                for i in range(item.childCount()):
                    vis = walk(item.child(i)) or vis
                if t and vis:
                    item.setExpanded(True)
            item.setHidden(not vis)
            return vis

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    # ------------------------------------------------------------------ export
    def _default_path(self, suffix: str) -> str:
        p = self.project
        name = f"{p.name} results{' (time periods)' if self.segmented else ''}{suffix}"
        return str(p.exports_dir() / name) if p.path else name

    def export_csv(self, path: str | None = None):
        if not self.rows:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export results", self._default_path(".csv"),
                                                  "CSV file (*.csv)")
            if not path:
                return
        try:
            write_csv(self.shown_rows(), path, self.shown_columns())
        except Exception as e:
            error_box(self, "Export CSV", e)
            return
        self.main.status(f"Exported {path}")
        return path

    def export_tsv(self, path: str | None = None):
        if not self.rows:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export results", self._default_path(".tsv"),
                                                  "Tab-separated text (*.tsv *.txt)")
            if not path:
                return
        try:
            write_tsv(self.shown_rows(), path, self.shown_columns())
        except Exception as e:
            error_box(self, "Export tab-separated text", e)
            return
        self.main.status(f"Exported {path}")
        return path

    def export_selection(self, path: str | None = None):
        """Save the selected cell range (or the whole shown table) as CSV / TSV / xlsx."""
        if not self.rows:
            return
        rng = self.selection_range()
        rows, cols = rng if rng else (self.shown_rows(), self.shown_columns())
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export selected cells",
                                                  self._default_path(".csv").replace(" results", " selection"),
                                                  TABLE_FILTER)
            if not path:
                return
        if Path(path).suffix.lower() not in (".csv", ".tsv", ".txt", ".xlsx"):
            path += ".csv"
        try:
            write_table(rows, path, cols)
        except Exception as e:
            error_box(self, "Export selection", e)
            return
        self.main.status(f"Exported {len(rows)} rows × {len(cols)} columns to {path}")
        return path

    def export_xlsx(self, path: str | None = None):
        if not self.rows:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export results", self._default_path(".xlsx"),
                                                  "Excel workbook (*.xlsx)")
            if not path:
                return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        p = self.project
        cols = self.shown_columns()
        rows = self.shown_rows()
        whole = [r for r in rows if r.get("Period", "Whole test") == "Whole test"]
        main_cols = [c for c in cols if c != "Period"]
        sheets = {"Results": whole}
        colmap = {"Results": main_cols}
        if self.segmented:
            seg = [r for r in rows if r.get("Period", "Whole test") != "Whole test"]
            if seg:
                sheets["Time periods"] = seg
                colmap["Time periods"] = cols if "Period" in cols else main_cols[:1] + ["Period"] + main_cols[1:]
        sheets["Animals"] = [{"Animal": a.id, "Group": a.group, "Sex": a.sex, **a.fields} for a in p.animals]
        sheets["Tests"] = [{"Test": t.id, "Animal": t.animal_id, "Stage": t.stage, "Trial": t.trial,
                            "Video": t.video, "Apparatus": t.apparatus, "Start (s)": t.start_s,
                            "Status": t.status, "Notes": t.notes} for t in p.tests]
        sheets["Settings"] = [{"Setting": k, "Value": str(v)} for k, v in
                              {**{f"detection.{k}": v for k, v in p.detection.to_dict().items()},
                               **{f"analysis.{k}": v for k, v in p.analysis.to_dict().items()},
                               "test_duration_s": p.test_duration_s}.items()]
        try:
            write_xlsx(sheets, path, colmap)
        except Exception as e:
            error_box(self, "Export Excel", e)
            return
        self.main.status(f"Exported {path}")
        return path

    def _shown_tests(self):
        p = self.project
        ids = []
        for r in self.shown_rows():
            if r.get("Test") not in ids:
                ids.append(r.get("Test"))
        return [t for t in (p.get_test(i) for i in ids) if t is not None]

    def export_xml(self, path: str | None = None, include_tracks: bool = True):
        """Whole experiment (settings, apparatus, animals, tests, results, raw tracks, events) as XML."""
        p = self.project
        if p is None:
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export experiment as XML",
                                                  self._default_path(".xml").replace(" results", ""),
                                                  "XML file (*.xml)")
            if not path:
                return
        if not path.lower().endswith(".xml"):
            path += ".xml"
        rows = list(self.rows) if self.rows else None

        def work(progress, stop):
            return export_xml(p, path, rows=rows, segmented=self.segmented, progress=progress, should_stop=stop,
                              include_tracks=include_tracks)

        def done(out):
            if out:
                self.main.status(f"Exported {out}")

        return self._run("Exporting experiment (XML)", work, on_done=done)

    def export_raw(self, folder: str | None = None):
        """One CSV per shown test with the raw track and all per-frame parameters."""
        p = self.project
        if p is None:
            return
        if folder is None:
            base = str(p.exports_dir() / "raw data") if p.path else ""
            folder = QFileDialog.getExistingDirectory(self, "Folder for the per-test raw data", base)
            if not folder:
                return
        tests = self._shown_tests() if self.rows else None

        def work(progress, stop):
            return export_raw_data(p, folder, tests=tests, progress=progress, should_stop=stop)

        def done(paths):
            self.main.status(f"Exported {len(paths or [])} raw data files to {folder}")

        return self._run("Exporting raw data", work, on_done=done)

    def clipboard_text(self) -> str:
        rng = self.selection_range()
        if rng is not None:
            rows, cols = rng
        else:
            cols = self.shown_columns()
            rows = self.shown_rows(selected_only=True)
        lines = ["\t".join(cols)]
        for r in rows:
            lines.append("\t".join(raw_value(r.get(c)).replace("\t", " ") for c in cols))
        return "\n".join(lines) + "\n"

    def copy_to_clipboard(self):
        if not self.rows:
            return
        text = self.clipboard_text()
        QGuiApplication.clipboard().setText(text)
        n_cols = text.split("\n", 1)[0].count("\t") + 1
        self.main.status(f"Copied {text.count(chr(10)) - 1} rows × {n_cols} columns")

    def _table_menu(self, pos):
        m = QMenu(self)
        m.addAction("Copy", self.copy_to_clipboard)
        m.addAction("Copy without headers", lambda: QGuiApplication.clipboard().setText(
            self.clipboard_text().split("\n", 1)[1]))
        m.addAction("Save selected cells…", self.export_selection)
        m.addSeparator()
        m.addAction("Open test", self._open_test)
        m.addAction("Show in charts", lambda: self.show_charts())
        m.addAction("Export video with overlays…", lambda: self.export_video())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def show_charts(self, test_id=None):
        r = self.current_row()
        tid = test_id if test_id is not None else (r.get("Test") if r else None)
        self.main_tabs.setCurrentWidget(self.charts)
        if tid is not None:
            self.charts.select_test(tid)

    def _report_preselect(self, measures: list[str]) -> list[str]:
        pre = [m for m in measures if measure_category(m, self._names)[0] == "Test-specific"]
        pre += [m for m in measures if m.startswith(("Total distance", "Centre: time (%)", "Center: time (%)",
                                                     "Freezing (%)"))]
        return pre[:8]

    def _run(self, title, work, **kw):
        w = run_with_progress(self, title, work, **kw)
        self._workers.append(w)
        w.finished.connect(lambda: QTimer.singleShot(0, lambda: w in self._workers and self._workers.remove(w)))
        return w

    def html_report(self, path: str | None = None, stats_measures: list[str] | None = None,
                    include_plots: bool | None = None, heatmap_norm: str = "auto",
                    chart_parameters: list[str] | None = None):
        if self.project is None or not self.rows:
            return
        measures = self.visible_measures()
        if stats_measures is None:
            numeric = numeric_columns(self.rows, measures)
            dlg = ReportDialog(numeric, self._report_preselect(numeric), self,
                               chart_params=self.charts.checked_params()[:4])
            if dlg.exec() != QDialog.Accepted:
                return
            stats_measures = dlg.selected()
            include_plots = dlg.plots.isChecked()
            heatmap_norm = dlg.norm.currentData()
            chart_parameters = dlg.chart_params if dlg.charts.isChecked() else None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save HTML report", self._default_path(".html")
                                                  .replace(" results", " report"), "HTML file (*.html)")
            if not path:
                return
        p = self.project
        tests = self._shown_tests()
        plots_on = True if include_plots is None else include_plots
        color_by = self.color_combo.currentData() or "time"

        def work(progress, stop):
            return html_report(p, path, tests=tests, include_plots=plots_on, measures=measures,
                               stats_measures=stats_measures, heatmap_norm=heatmap_norm,
                               chart_parameters=chart_parameters, color_by=color_by)

        def done(out):
            self.main.status(f"Report saved: {out}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

        return self._run("Creating HTML report", work, on_done=done, cancellable=False)

    def export_video(self, path: str | None = None, options=None):
        """Render the selected test's video with overlays in the background (progress dialog, cancellable)."""
        from ...core.videoexport import export_video

        r = self.current_row()
        p = self.project
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        if test is None or not test.video:
            error_box(self, "Export video", "This test has no video.")
            return
        if options is None:
            dlg = VideoExportDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            options = dlg.options()
        if path is None:
            base = str(p.exports_dir() / f"test_{test.id:04d}_overlay.mp4") if p.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Export video with overlays", base,
                                                  "MPEG-4 video (*.mp4);;AVI video (*.avi)")
            if not path:
                return
        if Path(path).suffix.lower() not in (".mp4", ".m4v", ".mov", ".avi"):
            path += ".mp4"

        def work(progress, stop):
            return export_video(p, test, path, options, progress, stop)

        def done(out):
            self.main.status(f"Video saved: {out}" if out else "Video export cancelled")

        return self._run(f"Exporting video of test {test.id}", work, on_done=done)

    # ------------------------------------------------------------------ detail plots
    def _current_key(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        r = self.model.rows[self.proxy.mapToSource(idx).row()]
        return (r.get("Test"), r.get("Animal"), r.get("Period"))

    def _restore_selection(self, key):
        if key is None:
            return
        for i in range(self.proxy.rowCount()):
            r = self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
            if (r.get("Test"), r.get("Animal"), r.get("Period")) == key:
                self.table.selectRow(i)
                return

    def current_row(self) -> dict | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.rows[self.proxy.mapToSource(idx).row()]

    def _open_test(self, *_):
        r = self.current_row()
        if r is not None and hasattr(self.main, "open_test"):
            self.main.open_test(r.get("Test"))

    def _frame(self, test):
        if test.id not in self._frames:
            frame = None
            try:
                with VideoSource(self.project.abs_path(test.video)) as v:
                    frame = v.frame_at(int(round(test.start_s * v.fps)))
            except Exception:
                pass
            self._frames[test.id] = frame
        return self._frames[test.id]

    def _select_changed(self):
        r = self.current_row()
        p = self.project
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        key = (r.get("Test"), r.get("Animal"), r.get("Period"))
        if test is None or (key == self._detail_key and self._detail is not None):
            return
        self._detail_key = key
        tracks = p.load_tracks(test) if p.has_track(test) else []
        ids = [test.animal_id] + list(test.extra_animals)
        ai = ids.index(r.get("Animal")) if r.get("Animal") in ids else 0
        title = f"Test {test.id} · {r.get('Animal', '')}"
        if r.get("Group"):
            title += f" · {r.get('Group')}"
        self.align_combo.blockSignals(True)
        self.align_combo.setCurrentIndex(max(0, self.align_combo.findData(
            (test.variables or {}).get("heatmap_transform", "none"))))
        self.align_combo.blockSignals(False)
        if not tracks or ai >= len(tracks):
            self._detail = None
            self.detail_lbl.setText(f"{title} — no track")
            for c in (self.track_canvas, self.heat_canvas, self.speed_canvas):
                c.set_figure(Figure(figsize=(3, 3)))
            return
        tr = tracks[ai]
        full = tr
        app = charts.apparatus_of_test(p, test)
        period = r.get("Period", "Whole test")
        t_range = None
        if period and period != "Whole test":
            rng = self._period_range(test, tr, app, period)
            if rng is not None:
                tr = tr.slice_time(*rng)
                t_range = rng
                title += f" · {period}"
        self._detail = {"test": test, "track": tr, "full": full, "app": app, "t_range": t_range,
                        "events": test.events if ai == 0 else [], "others": [o for j, o in enumerate(tracks) if j != ai]}
        self.detail_lbl.setText(title)
        self._fill_param_combos(app, tr)
        self._stale = {0, 1, 2}
        self._render_current()

    def _periods(self, test, track, app) -> list[tuple[str, float, float]]:
        """Time bins, custom and event-anchored periods of a test."""
        try:
            return all_periods(track, app, self.project.analysis_for(test), None, test.events, test.io_events,
                               test.zone_overrides)
        except Exception:
            return []

    def _period_range(self, test, track, app, label) -> tuple[float, float] | None:
        return next(((a, b) for lab, a, b in self._periods(test, track, app) if lab == label), None)

    def _fill_param_combos(self, app, tr):
        beh = [asdict(b) for b in self.project.behaviours]
        params = charts.parameters(app, tr, beh)
        for combo, items, keep in (
                (self.color_combo, [p for p in params if p.kind != charts.STATE and p.group not in ("Zones",)],
                 [("Time", "time"), ("Speed", "speed"), ("Single colour", "none")]),
                (self.heat_of, [p for p in params if p.kind == charts.STATE and p.group not in ("Position",)],
                 [(POSITION, None)])):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for label, data in keep:
                combo.addItem(label, data)
            combo.insertSeparator(combo.count())
            for prm in items:
                if prm.name in ("Speed",):
                    continue
                combo.addItem(prm.label if combo is self.color_combo else prm.name, prm.name)
            i = combo.findData(cur)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)

    def _tab_changed(self, *_):
        i = self.tabs.currentIndex()
        for w in self._track_opts:
            w.setEnabled(i == 0)
        for w in self._heat_opts:
            w.setEnabled(i in (1, 3))
        self.heat_max.setEnabled(i in (1, 3) and self.heat_norm.currentData() == "fixed")
        self._render_current()

    def _options_changed(self, *_):
        self.heat_max.setEnabled(self.tabs.currentIndex() in (1, 3) and self.heat_norm.currentData() == "fixed")
        if self._detail is not None:
            self._stale |= {0, 1}
            self._render_current()

    def _align_changed(self, *_):
        r = self.current_row()
        p = self.project
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        if test is None:
            return
        tf = self.align_combo.currentData()
        if tf == "none":
            test.variables.pop("heatmap_transform", None)
        else:
            test.variables["heatmap_transform"] = tf
        if hasattr(self.main, "mark_dirty"):
            self.main.mark_dirty()
        self.main.status(f"Test {test.id}: group heat map alignment “{plots.TRANSFORMS[tf]}”")

    def plot_options(self) -> dict:
        return {"part": self.part_combo.currentData(), "color_by": self.color_combo.currentData() or "time",
                "markers": self.markers_check.isChecked(), "split": self.split_check.isChecked(),
                "heat_of": self.heat_of.currentData(), "norm": self.heat_norm.currentData(),
                "vmax": self.heat_max.value() if self.heat_norm.currentData() == "fixed" else None}

    def set_plot_options(self, **kw):
        """Programmatic setup (tests): part, color_by, markers, split, heat_of, norm, vmax, align."""
        combos = {"part": self.part_combo, "color_by": self.color_combo, "heat_of": self.heat_of,
                  "norm": self.heat_norm, "align": self.align_combo}
        for k, v in kw.items():
            if k in combos:
                i = combos[k].findData(v)
                if i < 0:
                    raise ValueError(f"{k}: {v!r} not available")
                combos[k].setCurrentIndex(i)
            elif k == "markers":
                self.markers_check.setChecked(bool(v))
            elif k == "split":
                self.split_check.setChecked(bool(v))
            elif k == "vmax":
                self.heat_max.setValue(float(v))

    def _mask(self, track, app, which, test, events=None):
        if not which:
            return None
        beh = [asdict(b) for b in self.project.behaviours]
        return charts.state_mask(track, app, which, self.project.analysis_for(test),
                                 test.events if events is None else events, beh)

    def _render_current(self):
        i = self.tabs.currentIndex()
        if self._detail is None or i not in self._stale:
            return
        self._stale.discard(i)
        d = self._detail
        tr, app, test = d["track"], d["app"], d["test"]
        o = self.plot_options()
        s = self.project.analysis_for(test)
        beh = [asdict(b) for b in self.project.behaviours]
        try:
            if i == 0:
                frame = self._frame(test)
                markers = plots.behaviour_markers(tr, app, s, d["events"], beh) if o["markers"] else None
                if o["split"]:
                    full = d["full"]
                    dur = full.t[-1] + full.dt if len(full) else 0
                    periods = self._periods(test, full, app) or [(f"{a:g}-{b:g} s", a, b) for a, b in
                                                       zip(np.linspace(0, dur, 5)[:-1], np.linspace(0, dur, 5)[1:])]
                    fm = plots.behaviour_markers(full, app, s, d["events"], beh) if o["markers"] else None
                    fig = plots.segmented_track_plot(full, app, periods, frame=frame, color_by=o["color_by"],
                                                     part=o["part"], markers=fm, settings=s)
                else:
                    fig = plots.track_plot(tr, app, frame=frame, size=(4.2, 4), color_by=o["color_by"],
                                           part=o["part"], markers=markers, colorbar=o["color_by"] != "none",
                                           settings=s)
                self.track_canvas.set_figure(fig)
            elif i == 1:
                mask = self._mask(tr, app, o["heat_of"], test, d["events"])
                label = plots._norm_label({"auto": "time", "fixed": "time"}.get(o["norm"], o["norm"]),
                                          o["heat_of"] or "")
                self.heat_canvas.set_figure(plots.heatmap([tr], app, size=(4.4, 4), part=o["part"], norm=o["norm"],
                                                          vmax=o["vmax"], masks=[mask], label=label,
                                                          title=o["heat_of"] or ""))
            elif i == 2:
                fr = None
                try:
                    fr = kinematics(tr, app, s).freezing
                except Exception:
                    pass
                self.speed_canvas.set_figure(plots.speed_trace(tr, app, freezing=fr, size=(4.4, 3)))
        except Exception as e:
            self.main.status(f"Plot failed: {e}")

    def render_all(self):
        """Render every stale detail tab (tests / screenshots)."""
        cur = self.tabs.currentIndex()
        for i in sorted(self._stale):
            self.tabs.setCurrentIndex(i)
        self.tabs.setCurrentIndex(cur)

    def group_heatmaps(self):
        p = self.project
        if p is None or not self.rows:
            return
        by_group: dict[str, list] = {}
        seen = set()
        for r in self.shown_rows():
            t = p.get_test(r.get("Test"))
            if t is None or t.id in seen or not p.has_track(t):
                continue
            seen.add(t.id)
            by_group.setdefault(str(r.get("Group", "")) or "No group", []).append(t)
        if not by_group:
            return
        order = [g.name for g in p.groups if g.name in by_group] + [g for g in by_group if
                                                                    g not in {x.name for x in p.groups}]
        o = self.plot_options()
        per = self.period_combo.currentData() if self.seg_check.isChecked() else None
        per = per if per and per != "Whole test" else None
        beh = [asdict(b) for b in p.behaviours]

        def work(progress, stop):
            data, masks = [], {}
            n = sum(len(v) for v in by_group.values())
            k = 0
            ref = charts.apparatus_of_test(p, by_group[order[0]][0])
            for g in order:
                trs, ms = [], []
                for t in by_group[g]:
                    app = charts.apparatus_of_test(p, t)
                    for tr in p.load_tracks(t)[:1]:
                        mask = None
                        if o["heat_of"]:
                            mask = charts.state_mask(tr, app, o["heat_of"], p.analysis_for(t), t.events, beh)
                        if per:
                            rng = self._period_range(t, tr, app, per)
                            if rng is None:
                                continue
                            keep = (tr.t >= rng[0]) & (tr.t < rng[1])
                            mask = None if mask is None else mask[keep]
                            tr = tr.slice_time(*rng)
                        if o["heat_of"]:
                            ms.append(mask)
                        trs.append(plots.align_track(tr, app, (t.variables or {}).get("heatmap_transform", "none"),
                                                     ref))
                    k += 1
                    progress(k / n)
                data.append((g, trs, ref))
                masks[g] = ms if o["heat_of"] else None
            return plots.group_heatmap_figure(data, norm=o["norm"], vmax=o["vmax"], masks=masks, part=o["part"], what=o["heat_of"] or "")

        def done(fig):
            self.groups_canvas.set_figure(fig)
            self.tabs.setCurrentWidget(self.groups_canvas)

        return self._run("Group heat maps", work, on_done=done)

    def save_figure(self, path: str | None = None):
        canvas = self.tabs.currentWidget()
        if not isinstance(canvas, PlotCanvas) or canvas.figure is None or not canvas.figure.axes:
            return
        if path is None:
            name = self.tabs.tabText(self.tabs.currentIndex()).split()[0].lower()
            base = str(self.project.exports_dir() / f"{name}.png") if self.project and self.project.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Save figure", base, FIG_FILTER)
            if not path:
                return
        if Path(path).suffix.lower() not in (".png", ".pdf", ".svg"):
            path += ".png"
        try:
            canvas.save(path)
        except Exception as e:
            error_box(self, "Save figure", e)
            return
        self.main.status(f"Saved {path}")
        return path

    def copy_figure(self):
        canvas = self.tabs.currentWidget()
        if isinstance(canvas, PlotCanvas) and figure_to_clipboard(canvas.figure):
            self.main.status("Figure copied to the clipboard")


def group_heatmap_figure(data: list[tuple[str, list, object]], **kw) -> Figure:
    """Grid of average-occupancy heat maps (one per group) on a common colour scale."""
    return plots.group_heatmap_figure(data, **kw)
