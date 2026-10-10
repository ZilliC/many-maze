"""Charts view of the Data page: per-frame parameters of one test over time, with zoom / pan, interval
measurement and peak finding."""

from __future__ import annotations

import math
from pathlib import Path

from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from ....core import charts, plots
from ....core.export import display_text, write_table
from ....core.pauses import to_recording_time
from ....core.project import INACTIVE_STATUSES
from ...figures import FIG_FILTER, figure_to_clipboard
from ...widgets import error_box


class ChartsPanel(QWidget):
    """Per-frame parameters of one test over time, with zoom/pan, interval measurement and peak finding."""

    def __init__(self, page):
        super().__init__()
        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT

        from ...figures import ThemedCanvas

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
        self.canvas = ThemedCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        self.measure_check = QCheckBox("Measure interval")
        self.measure_check.setToolTip("Drag across a chart to measure the selected time interval")
        self.measure_check.toggled.connect(self._attach_spans)
        self.measure_check.hide()  # shown as “Measure interval” in the ribbon (Chart group)
        self.canvas.mpl_connect("scroll_event", self._wheel)
        self.canvas.mpl_connect("button_press_event", self._click)
        self._home_xlim: tuple[float, float] | None = None
        self.reset_zoom_btn = QToolButton()
        self.reset_zoom_btn.setText("Reset zoom")
        self.reset_zoom_btn.setToolTip("Show the whole time range again (or double-click the chart)")
        self.reset_zoom_btn.clicked.connect(self.reset_zoom)
        self.reset_zoom_btn.setEnabled(False)
        tb = QHBoxLayout()
        tb.addWidget(self.toolbar)
        tb.addWidget(self.reset_zoom_btn)
        tb.addStretch()
        tb.addWidget(self.measure_check)
        self.meas_table = QTableWidget(0, 8)
        self.meas_table.setHorizontalHeaderLabels(["Parameter", "From (s)", "To (s)", "Mean", "SD", "Min", "Max",
                                                   "Change"])
        self.meas_table.verticalHeader().hide()
        self.meas_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.meas_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.meas_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.meas_table.verticalHeader().setDefaultSectionSize(22)
        self.meas_table.setMaximumHeight(130)
        self.meas_lbl = QLabel("Click “Measure interval” in the ribbon and drag across the chart to measure; turn "
                               "the mouse wheel over the chart to zoom the time axis (double-click: whole range), "
                               "or use the toolbar to zoom and pan.")
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
                    grp = f" · {a.group}" if a and a.group else ""
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
        self.app = p.apparatus_of(test)
        self.others = [o for j, o in enumerate(tracks) if j != ai]
        self.beh = p.behaviours
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
                periods = p.test_periods(test, self.track)
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
            plots.message_figure("No tracked tests", fig=self.figure)
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
        t_range = self.period_combo.currentData()
        if t_range is not None and self.test.pauses:  # periods are in test time, the chart in recording time
            t_range = (float(to_recording_time([t_range[0]], self.test.pauses, True)[0]),
                       float(to_recording_time([t_range[1]], self.test.pauses, False)[0]))
        plots.chart_figure(self.track, self.app, names, s, events, self.beh, bands=self.checked_bands(),
                            show_events=self.events_check.isChecked(), t_range=t_range,
                            data=self.data, title=title, fig=self.figure, other_tracks=self.others or None)
        self._attach_spans()
        axes = self.figure.axes
        self._home_xlim = tuple(float(v) for v in axes[0].get_xlim()) if axes and names else None
        self.reset_zoom_btn.setEnabled(False)
        self.canvas.draw_idle()
        self.toolbar.update()

    # ------------------------------------------------------------------ wheel zoom
    def zoom_time(self, factor: float, centre: float | None = None):
        """Zoom the (shared) time axis by factor (< 1 zooms in) about the time centre (default: the middle),
        within the charted time range."""
        if not self.figure.axes or self._home_xlim is None:
            return
        ax = self.figure.axes[0]
        x0, x1 = ax.get_xlim()
        h0, h1 = self._home_xlim
        c = (x0 + x1) / 2 if centre is None else min(max(centre, x0), x1)
        full = h1 - h0
        dt = 1.0 / max(1.0, self.track.fps if self.track is not None else 25.0)
        width = min(full, max((x1 - x0) * factor, 5 * dt))  # at least a few frames, at most everything
        a = c - (c - x0) / (x1 - x0) * width if x1 > x0 else c - width / 2
        a = min(max(a, h0), h1 - width)
        for axis in self.figure.axes:  # also the axes that do not share x (e.g. zone bands)
            axis.set_xlim(a, a + width)
        self.reset_zoom_btn.setEnabled(bool(width < full - 1e-9))
        self.canvas.draw_idle()

    def reset_zoom(self):
        if self._home_xlim is None:
            return
        for axis in self.figure.axes:
            axis.set_xlim(*self._home_xlim)
        self.reset_zoom_btn.setEnabled(False)
        self.canvas.draw_idle()

    def time_range(self) -> tuple[float, float] | None:
        """The time range shown (the x limits of the charts)."""
        return tuple(self.figure.axes[0].get_xlim()) if self.figure.axes else None

    def _wheel(self, event):
        if event.inaxes is None or event.xdata is None:
            return
        self.zoom_time(0.8 ** event.step, event.xdata)  # step > 0: wheel forward / up = zoom in

    def _click(self, event):
        if event.dblclick and event.inaxes is not None and not self.measure_check.isChecked():
            self.reset_zoom()

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
                it = QTableWidgetItem(v if isinstance(v, str) else display_text(float(v)))
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
