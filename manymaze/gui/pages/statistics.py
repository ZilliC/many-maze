"""Statistics page: group comparisons, time courses with two-way ANOVA, and correlations."""

from __future__ import annotations

import math
from collections import OrderedDict
from html import escape

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...core import plots
from ...core.project import result_columns
from ...core.stats import compare_groups, correlation, format_p, stars, summary_text, two_way_anova
from ..widgets import PlotCanvas, error_box
from ._results_cache import RowsLoader, has_periods, info_columns
from .base import Page
from .results import FIG_FILTER, is_number, numeric_columns

WHOLE = "Whole test"
STAT_NAMES = {"Welch's t-test": "t", "Paired t-test": "t", "Mann-Whitney U": "U", "Wilcoxon signed-rank": "W",
              "One-way ANOVA": "F", "Kruskal-Wallis": "H", "Friedman": "χ²"}


def _fmt(v, nd=3) -> str:
    if v is None:
        return ""
    if isinstance(v, (tuple, list)):
        return ", ".join(_fmt(x, nd) for x in v)
    if is_number(v):
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        if not math.isfinite(v):
            return "–"
        return f"{float(v):.{nd}f}"
    return str(v)


def _p_text(p) -> str:
    s = stars(p)
    return f"{format_p(p)} {s}".strip() if s else format_p(p)


def _period_key(label: str):
    try:
        return (0, float(str(label).split()[0].split("-")[0]))
    except (ValueError, IndexError):
        return (1, 0.0)


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.NoSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.verticalHeader().setDefaultSectionSize(22)
    return t


def _fill(t: QTableWidget, rows: list[list]):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem(v if isinstance(v, str) else _fmt(v))
            if j > 0:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, j, it)
    h = t.horizontalHeader().sizeHint().height() + t.verticalHeader().defaultSectionSize() * max(1, len(rows))
    t.setFixedHeight(min(h + 2 * t.frameWidth() + 2, 240))


def _message_figure(text: str, size=(4.2, 3.6)) -> Figure:
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=10, color="#64748b", wrap=True,
            transform=ax.transAxes)
    return fig


def scatter_figure(xs, ys, groups, x_label: str, y_label: str, colors: dict, res: dict, size=(4.6, 3.8)) -> Figure:
    """Scatter plot coloured by group with an overall least-squares regression line."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    names = list(OrderedDict.fromkeys(groups))
    for i, g in enumerate(names):
        m = np.array([gg == g for gg in groups])
        ax.scatter(xs[m], ys[m], s=26, color=colors.get(g, f"C{i}"), edgecolor="white", lw=0.6, zorder=3,
                   label=g or "All")
    ok = np.isfinite(xs) & np.isfinite(ys)
    if ok.sum() >= 2 and np.ptp(xs[ok]) > 0:
        k, b = np.polyfit(xs[ok], ys[ok], 1)
        gx = np.linspace(xs[ok].min(), xs[ok].max(), 50)
        ax.plot(gx, k * gx + b, "-", color="#334155", lw=1.2, zorder=2)
    sym = "r" if res.get("method", "pearson") == "pearson" else "ρ"
    if res.get("r") == res.get("r"):
        ax.set_title(f"{sym} = {res['r']:.3f}, {format_p(res['p'])}, n = {res['n']}", fontsize=9)
    ax.set_xlabel(x_label, fontsize=8)
    ax.set_ylabel(y_label, fontsize=8)
    ax.tick_params(labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    if len(names) > 1:
        ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    return fig


class StatisticsPage(Page):
    title = "Statistics"

    def __init__(self, main):
        super().__init__(main)
        self.rows: list[dict] = []
        self.result: dict | None = None
        self.anova: dict | None = None
        self.corr: dict | None = None
        self._loading = False
        self._measure_set: set[str] = set()
        self.loader = RowsLoader(self)
        self.loader.loaded.connect(self._rows_loaded)
        self.loader.failed.connect(lambda msg: (self.status_lbl.setText(msg), error_box(self, "Statistics", msg)))
        self.loader.busy_changed.connect(self._busy_changed)
        self.loader.progress.connect(lambda f: self.status_lbl.setText(f"Calculating results… {int(f * 100)} %"))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self.recompute)

        # ---- controls --------------------------------------------------------
        box = QGroupBox("Analysis")
        f = QFormLayout(box)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.measure = self._measure_combo()
        self.factor = QComboBox()
        self.period = QComboBox()
        self.period.setToolTip("Time period of the results to analyse (time bins are set on the Experiment page)")
        self.filter_field = QComboBox()
        self.filter_value = QComboBox()
        self.parametric = QComboBox()
        self.parametric.addItem("Parametric (t-test / ANOVA)", True)
        self.parametric.addItem("Non-parametric (Mann-Whitney / Kruskal-Wallis)", False)
        self.paired = QCheckBox("Repeated measures (pair by animal)")
        self.paired.setToolTip("Within-animal comparison, e.g. Stage or Period: paired t-test / Wilcoxon / Friedman")
        self.plot_kind = QComboBox()
        self.plot_kind.addItem("Bars (mean ± SEM) + points", "bar")
        self.plot_kind.addItem("Box plot + points", "box")
        f.addRow("Measure", self.measure)
        f.addRow("Compare", self.factor)
        f.addRow("Period", self.period)
        fr = QHBoxLayout()
        fr.addWidget(self.filter_field, 1)
        fr.addWidget(QLabel("="))
        fr.addWidget(self.filter_value, 1)
        f.addRow("Only", fr)
        f.addRow("Test", self.parametric)
        f.addRow(self.paired)
        f.addRow("Graph", self.plot_kind)
        for w in (self.factor, self.period, self.filter_value, self.parametric, self.plot_kind):
            w.currentIndexChanged.connect(self._schedule)
        self.filter_field.currentIndexChanged.connect(self._filter_field_changed)
        self.paired.toggled.connect(self._schedule)

        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color:palette(mid);")
        recalc = QPushButton("Recalculate results")
        recalc.clicked.connect(lambda: self.reload(force=True))
        copy = QPushButton("Copy summary")
        copy.setToolTip("Copy a text summary of the current analysis")
        copy.clicked.connect(self.copy_summary)
        save = QPushButton("Save figure…")
        save.clicked.connect(self.save_figure)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(box)
        ll.addWidget(copy)
        ll.addWidget(save)
        ll.addWidget(recalc)
        ll.addWidget(self.status_lbl)
        ll.addWidget(QLabel("<b>Summary</b>"))
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.summary_box.setStyleSheet("font-family:monospace;font-size:11px;")
        ll.addWidget(self.summary_box, 1)
        left.setFixedWidth(330)

        # ---- tab 1: compare groups --------------------------------------------
        self.cmp_canvas = PlotCanvas()
        self.cmp_canvas.setMinimumSize(360, 320)
        self.test_lbl = QLabel()
        self.test_lbl.setWordWrap(True)
        self.test_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.desc_table = _table(["Group", "n", "Mean", "SD", "SEM", "Median", "Min", "Max"])
        self.assume_table = _table(["Check", "Group", "p", ""])
        self.posthoc_table = _table(["Comparison", "Difference", "p", "", "Method"])
        self.posthoc_lbl = QLabel("<b>Post-hoc comparisons</b>")
        res = QWidget()
        rl = QVBoxLayout(res)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.addWidget(self.test_lbl)
        rl.addSpacing(6)
        rl.addWidget(QLabel("<b>Assumption checks</b>"))
        rl.addWidget(self.assume_table)
        rl.addWidget(self.posthoc_lbl)
        rl.addWidget(self.posthoc_table)
        rl.addStretch()
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.cmp_canvas)
        top.addWidget(res)
        top.setSizes([470, 400])
        top.setChildrenCollapsible(False)
        cmp = QWidget()
        cml = QVBoxLayout(cmp)
        cml.addWidget(top, 1)
        cml.addWidget(QLabel("<b>Descriptive statistics</b>"))
        cml.addWidget(self.desc_table)

        # ---- tab 2: across stages / periods ------------------------------------
        self.tc_x = QComboBox()
        self.tc_by = QComboBox()
        for w in (self.tc_x, self.tc_by):
            w.currentIndexChanged.connect(self._schedule)
        tcbar = QHBoxLayout()
        tcbar.addWidget(QLabel("X axis"))
        tcbar.addWidget(self.tc_x)
        tcbar.addSpacing(10)
        tcbar.addWidget(QLabel("Lines"))
        tcbar.addWidget(self.tc_by)
        tcbar.addStretch()
        self.tc_canvas = PlotCanvas()
        self.tc_canvas.setMinimumSize(360, 300)
        self.anova_lbl = QLabel()
        self.anova_lbl.setWordWrap(True)
        self.anova_table = _table(["Effect", "SS", "df", "F", "p", ""])
        tc = QWidget()
        tl = QVBoxLayout(tc)
        tl.addLayout(tcbar)
        tl.addWidget(self.tc_canvas, 1)
        tl.addWidget(self.anova_lbl)
        tl.addWidget(self.anova_table)

        # ---- tab 3: correlation ------------------------------------------------
        self.corr_x = self._measure_combo()
        self.corr_y = self._measure_combo()
        self.corr_method = QComboBox()
        self.corr_method.addItem("Pearson", "pearson")
        self.corr_method.addItem("Spearman", "spearman")
        self.corr_method.currentIndexChanged.connect(self._schedule)
        cbar = QHBoxLayout()
        cbar.addWidget(QLabel("X"))
        cbar.addWidget(self.corr_x, 1)
        cbar.addWidget(QLabel("Y"))
        cbar.addWidget(self.corr_y, 1)
        cbar.addWidget(self.corr_method)
        self.corr_canvas = PlotCanvas()
        self.corr_canvas.setMinimumSize(360, 300)
        self.corr_lbl = QLabel()
        self.corr_lbl.setWordWrap(True)
        self.corr_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cr = QWidget()
        crl = QVBoxLayout(cr)
        crl.setContentsMargins(6, 0, 0, 0)
        crl.addWidget(self.corr_lbl)
        crl.addStretch()
        c_split = QSplitter(Qt.Horizontal)
        c_split.addWidget(self.corr_canvas)
        c_split.addWidget(cr)
        c_split.setSizes([560, 320])
        c_split.setChildrenCollapsible(False)
        cw = QWidget()
        cwl = QVBoxLayout(cw)
        cwl.addLayout(cbar)
        cwl.addWidget(c_split, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(cmp, "Compare groups")
        self.tabs.addTab(tc, "Across stages / periods")
        self.tabs.addTab(cw, "Correlation")
        self.tabs.currentChanged.connect(self._schedule)

        lay = QHBoxLayout(self)
        lay.addWidget(left)
        lay.addWidget(self.tabs, 1)
        self._clear_outputs()

    def _measure_combo(self) -> QComboBox:
        c = QComboBox()
        c.setEditable(True)
        c.setInsertPolicy(QComboBox.NoInsert)
        c.setMaxVisibleItems(25)
        c.setMinimumContentsLength(18)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        comp = c.completer()
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        c.currentIndexChanged.connect(self._schedule)
        return c

    # ------------------------------------------------------------------ project / data
    def set_project(self, project):
        self.loader.cancel()
        self.rows = []
        self._populate_controls()
        self._clear_outputs()

    def on_show(self):
        if self.project is not None:
            self.reload()

    def shutdown(self):
        self.loader.shutdown()

    def reload(self, force: bool = False):
        if self.project is None:
            return
        self.loader.request(self.project, has_periods(self.project), force)

    def wait_loaded(self, timeout_ms: int = 120000):
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtWidgets import QApplication

        t = QElapsedTimer()
        t.start()
        while self.loader.busy and t.elapsed() < timeout_ms:
            self.loader.wait(50)
            QApplication.processEvents()

    def _busy_changed(self, busy):
        if busy:
            self.status_lbl.setText("Calculating results…")

    def _rows_loaded(self, rows, segmented):
        self.rows = rows
        self._populate_controls()
        n_tests = len({r.get("Test") for r in rows})
        self.status_lbl.setText(f"{n_tests} tests analysed." if rows else
                                "No results yet: track tests or score behaviours first.")
        self.recompute()

    def numeric_measures(self) -> list[str]:
        info = set(info_columns(self.project))
        return numeric_columns(self.rows, [c for c in result_columns(self.rows) if c not in info])

    def factors(self) -> list[str]:
        p = self.project
        cand = ["Group", "Sex", "Stage", "Trial", "Period", "Apparatus"] + (list(p.animal_fields) if p else [])
        out = []
        for c in cand:
            vals = {str(r.get(c, "")) for r in self.rows if str(r.get(c, "")).strip()}
            if c == "Period":
                vals.discard(WHOLE)
            if c in ("Group", "Stage", "Period") or len(vals) > 1:
                out.append(c)
        return out

    def levels(self, col: str, rows=None) -> list[str]:
        rows = self.rows if rows is None else rows
        vals = list(OrderedDict.fromkeys(str(r.get(col, "")) for r in rows))
        p = self.project
        if col == "Group" and p:
            order = [g.name for g in p.groups]
            vals.sort(key=lambda v: (order.index(v) if v in order else len(order), v))
        elif col == "Stage" and p:
            order = list(p.stages)
            vals.sort(key=lambda v: (order.index(v) if v in order else len(order), v))
        elif col == "Period":
            vals.sort(key=lambda v: (v != WHOLE, _period_key(v)))
        elif col == "Trial":
            vals.sort(key=lambda v: (0, float(v)) if v.replace(".", "", 1).isdigit() else (1, v))
        return vals

    @staticmethod
    def _set_items(combo: QComboBox, items: list, current=None, data=None):
        combo.blockSignals(True)
        combo.clear()
        for i, it in enumerate(items):
            combo.addItem(str(it), data[i] if data else it)
        idx = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(idx if idx >= 0 else (0 if items else -1))
        combo.blockSignals(False)

    def _default_measure(self, measures: list[str]) -> str | None:
        for m in measures:
            if not m.startswith(("Test duration", "Detection")):
                return m
        return measures[0] if measures else None

    def _populate_controls(self):
        self._loading = True
        measures = self.numeric_measures()
        dm = self._default_measure(measures)
        for combo, default in ((self.measure, dm), (self.corr_x, dm),
                               (self.corr_y, measures[min(len(measures) - 1, measures.index(dm) + 1)]
                                if dm in measures else dm)):
            cur = combo.currentData() if combo.count() else None
            self._set_items(combo, measures, cur if cur in measures else default)
        factors = self.factors()
        self._set_items(self.factor, factors, self.factor.currentData() or "Group")
        periods = self.levels("Period")
        self._set_items(self.period, periods, self.period.currentData() or WHOLE)
        fields = ["(all rows)"] + [c for c in factors if c != "Period"]
        self._set_items(self.filter_field, fields, self.filter_field.currentData())
        self._fill_filter_values()
        xs = [c for c in ("Stage", "Trial", "Period") if c in factors]
        default_x = "Period" if "Period" in xs and len(self.levels("Period")) > 2 and \
            len(self.levels("Stage")) < 2 else ("Stage" if "Stage" in xs else (xs[0] if xs else None))
        self._set_items(self.tc_x, xs, self.tc_x.currentData() or default_x)
        by = [c for c in factors if c not in ("Period",)] + ["(none)"]
        self._set_items(self.tc_by, by, self.tc_by.currentData() or "Group")
        self._loading = False

    def _filter_field_changed(self, *_):
        self._fill_filter_values()
        self._schedule()

    def _fill_filter_values(self):
        field = self.filter_field.currentData()
        enabled = bool(field) and field != "(all rows)"
        vals = self.levels(field) if enabled else []
        self._set_items(self.filter_value, vals, self.filter_value.currentData())
        self.filter_value.setEnabled(enabled)

    def _schedule(self, *_):
        if not self._loading:
            self._timer.start()

    # ------------------------------------------------------------------ selection helpers
    def set_inputs(self, **kw):
        """Programmatic setup (tests / scripting): measure, factor, period, filter=(field, value), parametric,
        paired, plot, tc_x, tc_by, corr_x, corr_y, corr_method."""
        self._loading = True
        combos = {"measure": self.measure, "factor": self.factor, "period": self.period, "plot": self.plot_kind,
                  "tc_x": self.tc_x, "tc_by": self.tc_by, "corr_x": self.corr_x, "corr_y": self.corr_y,
                  "corr_method": self.corr_method, "parametric": self.parametric}
        for k, v in kw.items():
            if k in combos:
                i = combos[k].findData(v)
                if i < 0:
                    raise ValueError(f"{k}: {v!r} not available")
                combos[k].setCurrentIndex(i)
            elif k == "paired":
                self.paired.setChecked(bool(v))
            elif k == "filter":
                field, value = v if v else ("(all rows)", None)
                self.filter_field.setCurrentIndex(max(0, self.filter_field.findData(field)))
                self._fill_filter_values()
                if value is not None:
                    self.filter_value.setCurrentIndex(max(0, self.filter_value.findData(str(value))))
        self._loading = False
        self.recompute()

    def _combo_measure(self, combo: QComboBox) -> str | None:
        t = combo.currentText()
        return t if t in self._measure_set else (combo.currentData() if combo.currentIndex() >= 0 else None)

    def filtered_rows(self, use_period: bool = True) -> list[dict]:
        rows = self.rows
        field = self.filter_field.currentData()
        if field and field != "(all rows)" and self.filter_value.currentData() is not None:
            v = self.filter_value.currentData()
            rows = [r for r in rows if str(r.get(field, "")) == v]
        if use_period and self.period.currentData() is not None:
            per = self.period.currentData()
            rows = [r for r in rows if str(r.get("Period", WHOLE)) == per]
        return rows

    def group_data(self, rows, measure: str, factor: str, paired: bool) -> "OrderedDict[str, np.ndarray]":
        levels = [lv for lv in self.levels(factor, rows) if lv != ""]
        if factor == "Period":
            levels = [lv for lv in levels if lv != WHOLE] or levels
        if not paired:
            out = OrderedDict()
            for lv in levels:
                vals = [float(r[measure]) for r in rows if str(r.get(factor, "")) == lv and is_number(r.get(measure))
                        and math.isfinite(float(r[measure]))]
                if vals:
                    out[lv] = np.asarray(vals)
            return out
        per_animal: dict[str, dict[str, list]] = {}
        for r in rows:
            v = r.get(measure)
            lv = str(r.get(factor, ""))
            if lv in levels and is_number(v) and math.isfinite(float(v)):
                per_animal.setdefault(str(r.get("Animal", "")), {}).setdefault(lv, []).append(float(v))
        animals = sorted(a for a, d in per_animal.items() if all(lv in d for lv in levels))
        return OrderedDict((lv, np.asarray([np.mean(per_animal[a][lv]) for a in animals])) for lv in levels
                           if animals)

    def colors_for(self, factor: str) -> dict:
        p = self.project
        if factor == "Group" and p:
            return {g.name: g.color for g in p.groups}
        return {}

    # ------------------------------------------------------------------ compute
    def _clear_outputs(self):
        self.result = self.anova = self.corr = None
        for c in (self.cmp_canvas, self.tc_canvas, self.corr_canvas):
            c.set_figure(_message_figure("No data"))
        self.test_lbl.setText("")
        for t in (self.desc_table, self.assume_table, self.posthoc_table, self.anova_table):
            _fill(t, [])
        self.anova_lbl.setText("")
        self.corr_lbl.setText("")
        self.summary_box.setPlainText("")

    def recompute(self):
        self._timer.stop()
        if self.project is None or not self.rows:
            self._clear_outputs()
            return
        self._measure_set = set(self.numeric_measures())
        uses_period = self.factor.currentData() != "Period"
        self.period.setEnabled(uses_period and self.period.count() > 1)
        i = self.tabs.currentIndex()
        try:
            if i == 0:
                self._compute_compare()
            elif i == 1:
                self._compute_time_course()
            else:
                self._compute_correlation()
            self.summary_box.setPlainText(self.summary())
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.main.status(f"Statistics error: {e}")

    def _compute_compare(self):
        measure = self._combo_measure(self.measure)
        factor = self.factor.currentData()
        if not measure or not factor:
            return
        paired = self.paired.isChecked()
        rows = self.filtered_rows(use_period=factor != "Period")
        gv = self.group_data(rows, measure, factor, paired)
        res = compare_groups(gv, parametric=bool(self.parametric.currentData()), paired=paired)
        self.result = res
        self.result_measure = measure
        names = res["groups"]
        gv = OrderedDict((k, gv[k]) for k in names)
        if gv:
            fig = plots.group_plot(gv, measure, self.colors_for(factor), kind=self.plot_kind.currentData(),
                                   posthoc=res.get("posthoc"), p_value=res.get("p"))
        else:
            fig = _message_figure(f"No values of “{measure}”")
        self.cmp_canvas.set_figure(fig)
        # headline
        p = res.get("p", math.nan)
        sym = STAT_NAMES.get(res.get("test"), "statistic")
        parts = []
        if res.get("statistic") == res.get("statistic"):
            parts.append(f"{sym} = {res['statistic']:.3f}")
        if res.get("df") is not None:
            parts.append(f"df = {_fmt(res['df'], 2)}")
        parts.append(format_p(p))
        colour = "#16a34a" if p == p and p < 0.05 else "#475569"
        star = stars(p)
        html = (f"<div style='font-size:15px'><b>{escape(str(res.get('test')))}</b></div>"
                f"<div style='font-size:14px'>{escape(', '.join(parts))} "
                f"<b style='color:{colour}'>{star}</b></div>")
        if res.get("effect_size") is not None and res["effect_size"] == res["effect_size"]:
            html += f"<div>{res.get('effect_size_name')}: {res['effect_size']:.3f}</div>"
        n_total = sum(d["n"] for d in res["descriptive"].values())
        ctx = f"{measure} by {factor}"
        if factor != "Period" and self.period.count() > 1:
            ctx += f" · {self.period.currentData()}"
        if self.filter_value.isEnabled() and self.filter_value.currentData() is not None:
            ctx += f" · {self.filter_field.currentData()} = {self.filter_value.currentData()}"
        if paired:
            ctx += " · paired by animal"
        html += f"<div style='color:#64748b'>{escape(ctx)} · N = {n_total}</div>"
        self.test_lbl.setText(html)
        _fill(self.desc_table, [[g, d["n"], d["mean"], d["sd"], d["sem"], d["median"], d["min"], d["max"]]
                                for g, d in res["descriptive"].items()])
        checks = []
        for g, pv in (res.get("normality_p") or {}).items():
            checks.append(["Shapiro-Wilk (normality)", g, _fmt(pv), "non-normal" if pv == pv and pv < 0.05 else ""])
        if "levene_p" in res:
            lp = res["levene_p"]
            checks.append(["Levene (equal variances)", "all", _fmt(lp),
                           "unequal" if lp == lp and lp < 0.05 else ""])
        _fill(self.assume_table, checks)
        ph = res.get("posthoc") or []
        _fill(self.posthoc_table, [[f"{x['a']} vs {x['b']}", _fmt(x.get("diff")), format_p(x["p"]), stars(x["p"]),
                                    x.get("test", "")] for x in ph])
        self.posthoc_lbl.setVisible(bool(ph))
        self.posthoc_table.setVisible(bool(ph))

    def _compute_time_course(self):
        measure = self._combo_measure(self.measure)
        x = self.tc_x.currentData()
        by = self.tc_by.currentData()
        if not measure or not x:
            self.tc_canvas.set_figure(_message_figure("Needs stages, trials or time periods"))
            return
        rows = self.filtered_rows(use_period=x != "Period")
        if x == "Period":
            rows = [r for r in rows if r.get("Period") != WHOLE]
        rows = [r for r in rows if is_number(r.get(measure))]
        by_key = by if by and by != "(none)" else None
        if by_key is None:
            rows = [{**r, "_all": "All"} for r in rows]
        if not rows:
            self.tc_canvas.set_figure(_message_figure(f"No values of “{measure}”"))
            self.anova = None
            _fill(self.anova_table, [])
            return
        # keep the x order of levels() (time_course sorts numerically when it can)
        order = self.levels(x, rows)
        rows = sorted(rows, key=lambda r: order.index(str(r.get(x, ""))) if str(r.get(x, "")) in order else 0)
        self.tc_canvas.set_figure(plots.time_course(rows, measure, x=x, by=by_key or "_all",
                                                    colors=self.colors_for(by_key or ""), size=(5.2, 3.6)))
        self.tc_measure = measure
        if by_key is None:
            self.anova = None
            self.anova_lbl.setText(f"<b>{measure}</b> across {x}.<br>Choose a grouping for “Lines” to run a "
                                   "two-way ANOVA.")
            _fill(self.anova_table, [])
            return
        res = two_way_anova(rows, measure, factor_a=by_key, factor_b=x)
        self.anova = res
        if "error" in res:
            self.anova_lbl.setText(f"<b>Two-way ANOVA</b> ({by_key} × {x}): {res['error']}")
            _fill(self.anova_table, [])
            return
        self.anova_lbl.setText(f"<b>Two-way ANOVA</b> — {escape(measure)}<br>Factors: {by_key} × {x} "
                               f"(between-subjects, type II SS), residual df = {res['df_residual']}")
        _fill(self.anova_table, [[e["effect"], e["SS"], e["df"], e["F"], format_p(e["p"]), stars(e["p"])]
                                 for e in res["effects"]])

    def _compute_correlation(self):
        mx, my = self._combo_measure(self.corr_x), self._combo_measure(self.corr_y)
        if not mx or not my:
            return
        rows = self.filtered_rows()
        rows = [r for r in rows if is_number(r.get(mx)) and is_number(r.get(my))]
        xs = [float(r[mx]) for r in rows]
        ys = [float(r[my]) for r in rows]
        method = self.corr_method.currentData()
        res = correlation(xs, ys, method)
        res.setdefault("method", method)
        self.corr = res
        self.corr_measures = (mx, my)
        factor = "Group"
        groups = [str(r.get(factor, "")) for r in rows]
        if rows:
            fig = scatter_figure(xs, ys, groups, mx, my, self.colors_for(factor), res)
        else:
            fig = _message_figure("No paired values")
        self.corr_canvas.set_figure(fig)
        name = "Pearson r" if method == "pearson" else "Spearman ρ"
        r = res.get("r", math.nan)
        p = res.get("p", math.nan)
        strength = ""
        if r == r:
            a = abs(r)
            strength = "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else \
                "weak" if a >= 0.2 else "negligible"
            strength = f"{strength} {'positive' if r > 0 else 'negative'} correlation"
        self.corr_lbl.setText(f"<div style='font-size:15px'><b>{name} = {_fmt(r)}</b></div>"
                              f"<div style='font-size:14px'>{escape(_p_text(p))}, n = {res.get('n', 0)}</div>"
                              f"<div>{strength}</div><br>"
                              f"<div style='color:#64748b'>X: {escape(mx)}<br>Y: {escape(my)}</div>")

    # ------------------------------------------------------------------ actions
    def summary(self) -> str:
        i = self.tabs.currentIndex()
        if i == 0 and self.result:
            head = f"Compared by {self.factor.currentData()}"
            if self.factor.currentData() != "Period" and self.period.count() > 1:
                head += f", period: {self.period.currentData()}"
            if self.filter_value.isEnabled() and self.filter_value.currentData() is not None:
                head += f", only {self.filter_field.currentData()} = {self.filter_value.currentData()}"
            text = summary_text(self.result, self.result_measure)
            checks = []
            for g, pv in (self.result.get("normality_p") or {}).items():
                checks.append(f"    Shapiro-Wilk {g}: {format_p(pv)}")
            if "levene_p" in self.result:
                checks.append(f"    Levene: {format_p(self.result['levene_p'])}")
            return f"{head}\n{text}" + ("\n  Assumption checks:\n" + "\n".join(checks) if checks else "")
        if i == 1 and self.anova and "effects" in self.anova:
            lines = [f"{self.tc_measure}: two-way ANOVA ({' × '.join(self.anova['factors'])}), "
                     f"residual df = {self.anova['df_residual']}"]
            for e in self.anova["effects"]:
                lines.append(f"  {e['effect']}: F({e['df']}, {self.anova['df_residual']}) = {e['F']:.3f}, "
                             f"{format_p(e['p'])} {stars(e['p'])}")
            return "\n".join(lines)
        if i == 2 and self.corr:
            mx, my = self.corr_measures
            name = "Pearson r" if self.corr.get("method") == "pearson" else "Spearman rho"
            return f"{mx} vs {my}: {name} = {self.corr['r']:.3f}, {format_p(self.corr['p'])}, n = {self.corr['n']}"
        return ""

    def copy_summary(self):
        text = self.summary()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.main.status("Summary copied to the clipboard")

    def current_canvas(self) -> PlotCanvas:
        return (self.cmp_canvas, self.tc_canvas, self.corr_canvas)[self.tabs.currentIndex()]

    def save_figure(self, path: str | None = None):
        canvas = self.current_canvas()
        if canvas.figure is None:
            return
        if path is None:
            base = ""
            if self.project and self.project.path:
                base = str(self.project.exports_dir() / f"{self.tabs.tabText(self.tabs.currentIndex())}.png"
                           .replace(" / ", "-").replace(" ", "_"))
            path, _ = QFileDialog.getSaveFileName(self, "Save figure", base, FIG_FILTER)
            if not path:
                return
        if not path.lower().endswith((".png", ".pdf", ".svg")):
            path += ".png"
        try:
            canvas.save(path)
        except Exception as e:
            error_box(self, "Save figure", e)
            return
        self.main.status(f"Saved {path}")
        return path
