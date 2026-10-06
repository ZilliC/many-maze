"""Statistics page: group comparisons with post-hoc tests, two-factor / repeated-measures designs (learning curves),
descriptive statistics grouped by up to three factors, correlation / regression and categorical tests."""

from __future__ import annotations

import math
from collections import OrderedDict
from html import escape

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDoubleSpinBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import plots
from ...core import stats as st
from ...core.export import write_table
from ...core.project import result_columns
from ...core.stats import (anova_text, compare_groups, correlation, format_p, stars, summary_text,
                           two_way_anova)
from .. import theme
from ..icons import icon
from ..widgets import PlotCanvas, error_box
from ._results_cache import RowsLoader, has_periods, info_columns
from .base import Page
from .results import FIG_FILTER, TABLE_FILTER, figure_to_clipboard, is_number, numeric_columns, ribbon_label

WHOLE = "Whole test"
NONE = "(none)"
STAT_NAMES = {"Welch's t-test": "t", "Student's t-test": "t", "Paired t-test": "t", "Mann-Whitney U": "U",
              "Wilcoxon signed-rank": "W", "One-way ANOVA": "F", "Welch's ANOVA": "F", "Kruskal-Wallis": "H",
              "Friedman": "χ²", "Repeated-measures ANOVA": "F", "Kolmogorov-Smirnov": "D", "Brunner-Munzel": "W",
              "Alexander-Govern": "A", "Mood's median test": "χ²", "One-sample t-test": "t",
              "Wilcoxon signed-rank vs value": "W"}
METHODS = [("auto", "Automatic"), ("student", "Student's t-test"), ("welch", "Welch's t-test"),
           ("mannwhitney", "Mann-Whitney U"), ("ks", "Kolmogorov-Smirnov"), ("brunnermunzel", "Brunner-Munzel"),
           ("paired_t", "Paired t-test"), ("wilcoxon", "Wilcoxon signed-rank"), ("anova", "One-way ANOVA"),
           ("welch_anova", "Welch's ANOVA"), ("alexander_govern", "Alexander-Govern"),
           ("kruskal", "Kruskal-Wallis"), ("median", "Mood's median test"),
           ("rm_anova", "Repeated-measures ANOVA"), ("friedman", "Friedman"),
           ("one_t", "One-sample t-test vs value"), ("one_wilcoxon", "Wilcoxon signed-rank vs value")]
DESIGNS = [("between", "Between subjects (two-way ANOVA)"), ("mixed", "Repeated on X axis (mixed / RM ANOVA)"),
           ("srh", "Non-parametric (Scheirer-Ray-Hare)"), ("art", "Aligned rank transform ANOVA")]
GRAPHS = [("bar", "Column (mean ± error)"), ("point", "Points (mean ± error)"), ("box", "Box plot"),
          ("violin", "Violin plot")]
# analyses (explorer sub-items under "Statistics"): key, label, icon
VIEWS = [("compare", "Compare groups", "bars"), ("two", "Two factors", "chart"),
         ("correlation", "Correlation", "scatter"), ("grouped", "Grouped", "table"),
         ("categorical", "Categorical", "histogram")]
# how factors and choices are shown (internal names stay: the "Group" column holds the treatment)
DISPLAY = {"Group": "Treatment", NONE: "- None -", "(all rows)": "- All tests -"}

STYLE = f"""
QLabel#StatsHeading {{ color: {theme.HEADING}; font-size: 20px; font-weight: 300; padding: 14px 0 2px 0; }}
QLabel#StatsPrompt {{ font-size: 13px; }}
QLabel#ReportTitle {{ color: {theme.HEADING}; font-size: 20px; font-weight: 300; }}
QLabel#ReportHeading {{ color: {theme.HEADING}; font-size: 15px; padding: 12px 0 2px 0; }}
QLabel#ReportText {{ font-size: 13px; }}
QWidget#StatsReport {{ background: {theme.WORK_BG}; }}
QFrame#StatsSeparator {{ color: {theme.BORDER}; }}
QComboBox, QDoubleSpinBox {{ min-height: 22px; }}
QTableWidget {{ border: none; border-top: 1px solid {theme.BORDER}; background: transparent; font-size: 13px; }}
QTableWidget::item {{ padding: 0 6px; }}
QHeaderView::section {{ background: transparent; font-style: italic; font-weight: normal; border: none;
    border-bottom: 1px solid {theme.BORDER}; padding: 4px 6px; }}
"""


def _label(v) -> str:
    return DISPLAY.get(v, str(v))


def _wide_combo() -> QComboBox:
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return c


def _fmt(v, nd=None) -> str:
    """Numbers with 4 significant figures (at most 3 decimals), like the tables of ANY-maze's reports."""
    if v is None:
        return ""
    if isinstance(v, (tuple, list)):
        return ", ".join(_fmt(x, nd) for x in v)
    if is_number(v):
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        if not math.isfinite(v):
            return "–"
        if nd is None:
            a = abs(float(v))
            nd = 3 if a < 10 else 2 if a < 100 else 1 if a < 1000 else 0
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


def _table(headers: list[str], max_h: int = 240) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.ContiguousSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.verticalHeader().setDefaultSectionSize(28)
    t.setShowGrid(False)
    t.setFrameShape(QFrame.NoFrame)
    t.setFocusPolicy(Qt.NoFocus)
    t.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    t._max_h = max_h
    return t


def _set_headers(t: QTableWidget, headers: list[str]):
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)


def _fill(t: QTableWidget, rows: list[list]):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem(v if isinstance(v, str) else _fmt(v))
            if j > 0:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, j, it)
    h = t.horizontalHeader().sizeHint().height() + t.verticalHeader().defaultSectionSize() * max(1, len(rows))
    t.setFixedHeight(min(h + 2 * t.frameWidth() + 2, getattr(t, "_max_h", 240)))


def _table_text(t: QTableWidget) -> str:
    heads = [t.horizontalHeaderItem(j).text() if t.horizontalHeaderItem(j) else "" for j in range(t.columnCount())]
    lines = ["\t".join(heads)]
    for i in range(t.rowCount()):
        lines.append("\t".join(t.item(i, j).text() if t.item(i, j) else "" for j in range(t.columnCount())))
    return "\n".join(lines) + "\n"


def _message_figure(text: str, size=(4.2, 3.6)) -> Figure:
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=10, color="#64748b", wrap=True,
            transform=ax.transAxes)
    return fig


def proportions_figure(rows_l, cols_l, T, size=(5, 3.6)) -> Figure:
    """Stacked bars of the proportion of each category per row level."""
    fig = Figure(figsize=size, dpi=100)
    ax = fig.add_subplot(111)
    T = np.asarray(T, float)
    tot = T.sum(axis=1, keepdims=True)
    P = np.divide(T, tot, out=np.zeros_like(T), where=tot > 0) * 100
    bottom = np.zeros(len(rows_l))
    for j, c in enumerate(cols_l):
        ax.bar(range(len(rows_l)), P[:, j], bottom=bottom, label=c, color=f"C{j}", alpha=0.8, width=0.6)
        bottom += P[:, j]
    ax.set_xticks(range(len(rows_l)))
    ax.set_xticklabels([f"{r}\n(n = {int(n)})" for r, n in zip(rows_l, tot[:, 0])], fontsize=8)
    ax.set_ylabel("% of tests", fontsize=8)
    ax.set_ylim(0, 100)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=7, frameon=False, bbox_to_anchor=(1.0, 1.0), loc="upper left")
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
        self.reg: dict | None = None
        self.grouped: list[dict] | None = None
        self.cat: dict | None = None
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

        # ---- inputs (laid out below as an ANY-maze style property page) -------------------------------
        self.measure = self._measure_combo()
        self.factor = _wide_combo()
        self.period = _wide_combo()
        self.period.setToolTip("Time period of the results to analyse (time bins are set on the Protocol page or with "
                               "“Set segment length” on the Data page)")
        self.filter_field = _wide_combo()
        self.filter_value = _wide_combo()
        self.param_radio = QRadioButton("Parametric")
        self.param_radio.setChecked(True)
        self.nonparam_radio = QRadioButton("Non-parametric")
        for r in (self.param_radio, self.nonparam_radio):
            r.setToolTip("Used by the automatic test choice: t-test / ANOVA or Mann-Whitney / Kruskal-Wallis "
                         "(Wilcoxon / Friedman when the same animals were tested at each level)")
        self._param_group = QButtonGroup(self)
        self._param_group.addButton(self.param_radio)
        self._param_group.addButton(self.nonparam_radio)
        self.param_radio.toggled.connect(self._schedule)
        self.method = _wide_combo()
        for k, v in METHODS:
            self.method.addItem(v, k)
        self.method.setMaxVisibleItems(20)
        self.method.setToolTip("Statistical test; Automatic picks one from the number of levels, the type of tests "
                               "and pairing")
        self.posthoc = _wide_combo()
        for k, v in st.POSTHOC.items():
            self.posthoc.addItem(icon("posthoc") if k not in ("auto", "none") else QIcon(),
                                 "- None -" if k == "none" else v, k)
        self.posthoc.setMaxVisibleItems(20)
        self.posthoc.setToolTip("Pairwise comparisons after a test of more than two levels (Automatic: Tukey after "
                                "ANOVA, Games-Howell after Welch's ANOVA, Bonferroni after rank tests)")
        self.control = _wide_combo()
        self.control.setToolTip("Control level for Dunnett's test")
        self.mu = QDoubleSpinBox()
        self.mu.setRange(-1e9, 1e9)
        self.mu.setDecimals(3)
        self.mu.setToolTip("Reference value for one-sample tests (e.g. 50 % alternation, discrimination index 0)")
        self.paired = QCheckBox("Repeated measures")
        self.paired.setToolTip("Within-animal comparison, e.g. Stage or Period: paired t / Wilcoxon / "
                               "repeated-measures ANOVA / Friedman")
        self.plot_kind = _wide_combo()
        for k, v in GRAPHS:
            self.plot_kind.addItem(v, k)
        self.error = _wide_combo()
        for k, v in plots.ERRORS.items():
            self.error.addItem(v, k)
        self.points = QCheckBox("Show individual values")
        self.points.setChecked(True)
        self.tc_x = _wide_combo()
        self.tc_by = _wide_combo()
        self.design = _wide_combo()
        for k, v in DESIGNS:
            self.design.addItem(v, k)
        self.design.setToolTip("Between subjects: every row independent. Repeated: animals measured at every "
                               "level of the 1st variable (mixed ANOVA with the 2nd variable between animals; "
                               "Greenhouse-Geisser corrected p-values). Non-parametric: rank-based alternatives.")
        self.tc_plot = _wide_combo()
        for k, v in [("line", "Line (mean ± error)")] + GRAPHS:
            self.tc_plot.addItem(v, k)
        self.corr_x = self._measure_combo()
        self.corr_y = self._measure_combo()
        self.corr_method = _wide_combo()
        self.corr_method.addItem("Pearson", "pearson")
        self.corr_method.addItem("Spearman", "spearman")
        self.corr_method.addItem("Kendall", "kendall")
        self.corr_by = _wide_combo()
        self.f1, self.f2, self.f3 = _wide_combo(), _wide_combo(), _wide_combo()
        self.cat_rows = _wide_combo()
        self.cat_col = _wide_combo()
        self.cat_col.setToolTip("A categorical result (e.g. search strategy, first choice) or a factor")
        for w in (self.factor, self.period, self.filter_value, self.plot_kind, self.method, self.posthoc,
                  self.control, self.error, self.tc_x, self.tc_by, self.design, self.tc_plot, self.corr_method,
                  self.corr_by, self.f1, self.f2, self.f3, self.cat_rows, self.cat_col):
            w.currentIndexChanged.connect(self._schedule)
        self.filter_field.currentIndexChanged.connect(self._filter_field_changed)
        self.paired.toggled.connect(self._schedule)
        self.points.toggled.connect(self._schedule)
        self.mu.valueChanged.connect(self._schedule)

        # ---- property page -------------------------------------------------------------------------
        props = QWidget()
        props.setObjectName("StatsProps")
        self._grid = QGridLayout(props)
        self._grid.setContentsMargins(0, 0, 22, 12)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(8)
        self._grid.setColumnMinimumWidth(0, 250)
        self._grid.setColumnStretch(1, 1)
        self._rows_by_view: list[tuple[set, list]] = []
        C, T, R, G, K = "compare", "two", "correlation", "grouped", "categorical"
        filt = QWidget()
        fl = QHBoxLayout(filt)
        fl.setContentsMargins(0, 0, 0, 0)
        for c in (self.filter_field, self.filter_value):
            c.setMinimumContentsLength(4)
        fl.addWidget(self.filter_field, 1)
        fl.addWidget(QLabel("="))
        fl.addWidget(self.filter_value, 1)
        radios = QWidget()
        rl = QHBoxLayout(radios)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(18)
        rl.addWidget(self.param_radio)
        rl.addWidget(self.nonparam_radio)
        rl.addStretch()
        spec = [
            ("head", "Dependent variable", {C, T, G}),
            ("Select the measure to analyse", self.measure, {C, T, G}),
            ("head", "Dependent variables", {R}),
            ("Select the 1st measure (X axis)", self.corr_x, {R}),
            ("Select the 2nd measure (Y axis)", self.corr_y, {R}),
            ("head", "Dependent variable", {K}),
            ("Select the categorical result to analyse", self.cat_col, {K}),
            ("head", "Independent variables", {C, T, G, R, K}),
            ("Select the independent variable", self.factor, {C}),
            ("Were the same animals tested at each level?", self.paired, {C}),
            ("Select the 1st independent variable", self.tc_x, {T}),
            ("Optionally select a 2nd independent variable", self.tc_by, {T}),
            ("Select the design of the analysis", self.design, {T}),
            ("Select the 1st independent variable", self.f1, {G}),
            ("Optionally select a 2nd independent variable", self.f2, {G}),
            ("Optionally select a 3rd independent variable", self.f3, {G}),
            ("Optionally colour the points by", self.corr_by, {R}),
            ("Select the variable to group the tests by", self.cat_rows, {K}),
            ("head", "Tests to include", {C, T, G, R, K}),
            ("Select the time period to analyse", self.period, {C, T, G, R, K}),
            ("Optionally only include tests where", filt, {C, T, G, R, K}),
            ("head", "Options", {C, R}),
            ("Select the type of statistical tests to use", radios, {C}),
            ("Optionally select a specific statistical test", self.method, {C}),
            ("Optionally select a post-hoc test to use", self.posthoc, {C}),
            ("Select the control group to compare to", self.control, {C}),
            ("Select the value to compare the measure to", self.mu, {C}),
            ("Select the type of correlation", self.corr_method, {R}),
            ("head", "Report format", {C, T, G}),
            ("Select the type of graph", self.plot_kind, {C, G}),
            ("Select the type of graph", self.tc_plot, {T}),
            ("Select what the error bars show", self.error, {C, T, G}),
            ("Optionally show the value of each test", self.points, {C, T, G}),
        ]
        row = 0
        for text, w, views in spec:
            if text == "head":
                lbl = QLabel(w)
                lbl.setObjectName("StatsHeading")
                self._grid.addWidget(lbl, row, 0, 1, 2)
                self._rows_by_view.append((views, [lbl]))
            else:
                lbl = QLabel(text)
                lbl.setWordWrap(True)
                lbl.setObjectName("StatsPrompt")
                self._grid.addWidget(lbl, row, 0)
                self._grid.addWidget(w, row, 1)
                self._rows_by_view.append((views, [lbl, w]))
            row += 1
        self._grid.setRowStretch(row, 1)
        props_scroll = QScrollArea()
        props_scroll.setWidget(props)
        props_scroll.setWidgetResizable(True)
        props_scroll.setFrameShape(QScrollArea.NoFrame)
        props_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        props_scroll.setFixedWidth(570)

        # ---- reports (one per analysis; the explorer switches between them) -------------------------------
        self._heads: list[QLabel] = []
        self._subheads: list[QLabel] = []

        def report(*widgets) -> QScrollArea:
            w = QWidget()
            w.setObjectName("StatsReport")
            lay = QVBoxLayout(w)
            lay.setContentsMargins(22, 14, 22, 18)
            lay.setSpacing(6)
            head = QLabel()
            head.setObjectName("ReportTitle")
            head.setWordWrap(True)
            sub = QLabel()
            sub.setObjectName("Hint")
            sub.setWordWrap(True)
            self._heads.append(head)
            self._subheads.append(sub)
            lay.addWidget(head)
            lay.addWidget(sub)
            for x in widgets:
                if isinstance(x, str):
                    lbl = QLabel(x)
                    lbl.setObjectName("ReportHeading")
                    lay.addWidget(lbl)
                else:
                    lay.addWidget(x)
            lay.addStretch()
            sa = QScrollArea()
            sa.setWidget(w)
            sa.setWidgetResizable(True)
            sa.setFrameShape(QScrollArea.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            return sa

        def canvas(h=330) -> PlotCanvas:
            c = PlotCanvas()
            c.setFixedHeight(h)
            c.setMinimumWidth(320)
            return c

        def rich() -> QLabel:
            lbl = QLabel()
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl.setObjectName("ReportText")
            return lbl

        # compare groups
        self.cmp_canvas = canvas(340)
        self.test_lbl = rich()
        self.desc_table = _table(["Group", "n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"], 4000)
        self.assume_table = _table(["Check", "Group", "p", ""], 4000)
        ah = self.assume_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        for j in (1, 2, 3):
            ah.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        self.posthoc_table = _table(["Comparison", "Difference", "p", "", "Method"], 4000)
        for j in (2, 3, 4):
            self.posthoc_table.horizontalHeader().setSectionResizeMode(j, QHeaderView.ResizeToContents)
        self.posthoc_lbl = QLabel("Post-hoc comparisons")
        self.posthoc_lbl.setObjectName("ReportHeading")
        cmp = report(self.test_lbl, self.cmp_canvas, "Descriptive statistics", self.desc_table, self.posthoc_lbl,
                     self.posthoc_table, "Assumption checks", self.assume_table)
        # two factors / across stages / periods
        self.tc_canvas = canvas(340)
        self.anova_lbl = rich()
        self.anova_table = _table(["Effect", "SS", "df", "F", "p", "", "p (GG)"], 4000)
        tc = report(self.anova_lbl, self.tc_canvas, "Analysis of variance", self.anova_table)
        # correlation / regression
        self.corr_canvas = canvas(360)
        self.corr_lbl = rich()
        cw = report(self.corr_lbl, self.corr_canvas)
        # grouped descriptive statistics (up to 3 levels)
        self.grp_canvas = canvas(340)
        self.grp_table = _table(["Level", "n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"], 10000)
        gw = report(self.grp_canvas, "Descriptive statistics", self.grp_table)
        # categorical results
        self.cat_canvas = canvas(320)
        self.cat_lbl = rich()
        self.cat_table = _table(["", "Total"], 4000)
        kw = report(self.cat_lbl, self.cat_canvas, "Counts", self.cat_table)

        self.tabs = QStackedWidget()  # one report per analysis (explorer sub-items)
        for w in (cmp, tc, cw, gw, kw):
            self.tabs.addWidget(w)
        self.tabs.currentChanged.connect(self._view_changed)
        self.summary_box = QPlainTextEdit()  # text summary (copied with “Copy summary”)
        self.summary_box.setReadOnly(True)
        self.summary_box.hide()

        # ---- page ------------------------------------------------------------------------------------------
        self.title_lbl = QLabel(VIEWS[0][1])
        self.title_lbl.setObjectName("PageTitle")
        self.status_lbl = QLabel()
        self.status_lbl.setObjectName("Hint")
        top = QHBoxLayout()
        top.addWidget(self.title_lbl)
        top.addStretch()
        top.addWidget(self.status_lbl)
        top.addSpacing(18)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(props_scroll)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setObjectName("StatsSeparator")
        body.addWidget(sep)
        body.addWidget(self.tabs, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 12, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addLayout(body, 1)
        self.setStyleSheet(STYLE)

        # ---- ribbon ------------------------------------------------------------------------------------------
        def act(text, ic, fn, tip="", small=False):
            a = QAction(icon(ic), text, self)
            if not small:
                a.setIconText(ribbon_label(text))
            a.setToolTip(tip or text)
            a.triggered.connect(lambda _=False: fn())
            return a

        self.run_act = act("Run", "play", self.recompute, "Run the analysis again with the current settings")
        self.recalc_act = act("Recalculate", "refresh", lambda: self.reload(force=True),
                              "Recalculate all results from the tracks (e.g. after changing analysis settings)")
        self.copy_summary_act = act("Copy summary", "copy", self.copy_summary,
                                    "Copy a text summary of the analysis (for a lab book or a manuscript)")
        self.save_fig_act = act("Save figure", "save", self.save_figure, "Save the graph as PNG, PDF or SVG")
        self.save_report_act = act("Save report", "save_report", self.save_report,
                                   "Save the report (settings, tables and graph) as an HTML file")
        self.copy_fig_act = act("Copy figure", "copy", self.copy_figure, "Copy the graph to the clipboard", True)
        self.copy_table_act = act("Copy table", "copy_select", self.copy_grouped,
                                  "Copy the table of descriptive statistics", True)
        self.save_table_act = act("Save table", "export", self.save_grouped,
                                  "Save the table of descriptive statistics (CSV / tab-separated / Excel)", True)
        self._view_changed(0)
        self._clear_outputs()

    # ------------------------------------------------------------------ shell hooks
    def ribbon_groups(self):
        return [("Analysis", [(self.run_act, "large"), (self.recalc_act, "large")]),
                ("Report", [(self.copy_summary_act, "large"), (self.save_fig_act, "large"),
                            (self.save_report_act, "large"), (self.copy_fig_act, "small"),
                            (self.copy_table_act, "small"), (self.save_table_act, "small")])]

    def explorer_items(self):
        return [(label, ic, key) for key, label, ic in VIEWS]

    def show_item(self, key):
        i = next((n for n, v in enumerate(VIEWS) if v[0] == key), None)
        if i is not None and i != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(i)

    def view(self) -> str:
        return VIEWS[self.tabs.currentIndex()][0]

    def _view_changed(self, i):
        key, label, _ = VIEWS[i]
        self.title_lbl.setText(label)
        for views, widgets in self._rows_by_view:
            for w in widgets:
                w.setVisible(key in views)
        for a in (self.copy_table_act, self.save_table_act):
            a.setEnabled(key == "grouped")
        if self.project is not None:
            self.main.select_explorer(self, key)
        self._schedule()

    def _measure_combo(self) -> QComboBox:
        c = QComboBox()
        c.setEditable(True)
        c.setInsertPolicy(QComboBox.NoInsert)
        c.setMaxVisibleItems(25)
        c.setMinimumContentsLength(10)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        c.lineEdit().setPlaceholderText("Type to search the measures…")
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
            QTimer.singleShot(0, self._follow_explorer)
            self.reload()

    def _follow_explorer(self):
        if self.project is not None and self.main.current_page() is self:
            self.main.select_explorer(self, self.view())

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

    def categorical_columns(self) -> list[str]:
        """Text results with a few distinct values (e.g. search strategy, first choice)."""
        info = set(info_columns(self.project))
        out = []
        for c in result_columns(self.rows):
            if c in info:
                continue
            vals = [r.get(c) for r in self.rows if r.get(c) not in (None, "")]
            if vals and all(isinstance(v, str) for v in vals) and len(set(vals)) <= 20:
                out.append(c)
        return out

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

    def orders(self, factors, rows=None) -> dict:
        return {f: self.levels(f, rows) for f in factors if f}

    @staticmethod
    def _set_items(combo: QComboBox, items: list, current=None, data=None):
        combo.blockSignals(True)
        combo.clear()
        for i, it in enumerate(items):
            combo.addItem(_label(it), data[i] if data else it)
        idx = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(idx if idx >= 0 else (0 if items else -1))
        combo.blockSignals(False)

    def _default_measure(self, measures: list[str]) -> str | None:
        """The first measure that varies between tests (skipping test duration and detection quality)."""
        for m in measures:
            if m.startswith(("Test duration", "Detection", "Time not detected")):
                continue
            vals = {r.get(m) for r in self.rows if is_number(r.get(m))}
            if len(vals) > 1:
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
        by = [c for c in factors if c not in ("Period",)] + [NONE]
        self._set_items(self.tc_by, by, self.tc_by.currentData() or "Group")
        self._set_items(self.corr_by, ["Group"] + [c for c in factors if c not in ("Group", "Period")] + [NONE],
                        self.corr_by.currentData() or "Group")
        self._set_items(self.f1, factors, self.f1.currentData() or "Group")
        self._set_items(self.f2, [NONE] + factors, self.f2.currentData() or NONE)
        self._set_items(self.f3, [NONE] + factors, self.f3.currentData() or NONE)
        cats = self.categorical_columns()
        self._set_items(self.cat_rows, [c for c in factors if c != "Period"], self.cat_rows.currentData() or "Group")
        others = [c for c in factors if c not in ("Period", self.cat_rows.currentData())]
        self._set_items(self.cat_col, cats + [c for c in factors if c != "Period"],
                        self.cat_col.currentData() or (cats[0] if cats else (others[0] if others else None)))
        self._fill_control()
        self._loading = False

    def _fill_control(self):
        factor = self.factor.currentData()
        lv = [v for v in self.levels(factor) if v and v != WHOLE] if factor else []
        if [self.control.itemData(i) for i in range(self.control.count())] != lv:
            self._set_items(self.control, lv, self.control.currentData())

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
        paired, plot, method, posthoc, control, mu, error, points, tc_x, tc_by, design, tc_plot, corr_x, corr_y,
        corr_method, corr_by, f1, f2, f3, cat_rows, cat_col."""
        self._loading = True
        combos = {"measure": self.measure, "factor": self.factor, "period": self.period, "plot": self.plot_kind,
                  "tc_x": self.tc_x, "tc_by": self.tc_by, "corr_x": self.corr_x, "corr_y": self.corr_y,
                  "corr_method": self.corr_method, "method": self.method,
                  "posthoc": self.posthoc, "error": self.error, "design": self.design, "tc_plot": self.tc_plot,
                  "corr_by": self.corr_by, "f1": self.f1, "f2": self.f2, "f3": self.f3, "cat_rows": self.cat_rows,
                  "cat_col": self.cat_col}
        for k, v in kw.items():
            if k == "factor":
                combos[k].setCurrentIndex(combos[k].findData(v))
                self._fill_control()
            elif k in combos:
                i = combos[k].findData(v)
                if i < 0:
                    raise ValueError(f"{k}: {v!r} not available")
                combos[k].setCurrentIndex(i)
            elif k == "control":
                self._fill_control()
                self.control.setCurrentIndex(max(0, self.control.findData(str(v))))
            elif k == "parametric":
                (self.param_radio if v else self.nonparam_radio).setChecked(True)
            elif k == "mu":
                self.mu.setValue(float(v))
            elif k == "points":
                self.points.setChecked(bool(v))
            elif k == "paired":
                self.paired.setChecked(bool(v))
            elif k == "filter":
                field, value = v if v else ("(all rows)", None)
                self.filter_field.setCurrentIndex(max(0, self.filter_field.findData(field)))
                self._fill_filter_values()
                if value is not None:
                    self.filter_value.setCurrentIndex(max(0, self.filter_value.findData(str(value))))
            else:
                raise ValueError(f"Unknown input {k!r}")
        self._loading = False
        self.recompute()

    def is_parametric(self) -> bool:
        return self.param_radio.isChecked()

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

    def _context(self, factor: str | None = None) -> str:
        ctx = []
        if factor != "Period" and self.period.count() > 1:
            ctx.append(str(self.period.currentData()))
        if self.filter_value.isEnabled() and self.filter_value.currentData() is not None:
            ctx.append(f"{self.filter_field.currentData()} = {self.filter_value.currentData()}")
        return " · ".join(ctx)

    # ------------------------------------------------------------------ compute
    def _clear_outputs(self):
        self.result = self.anova = self.corr = self.reg = self.grouped = self.cat = None
        for c in (self.cmp_canvas, self.tc_canvas, self.corr_canvas, self.grp_canvas, self.cat_canvas):
            c.set_figure(_message_figure("No data"))
        self.test_lbl.setText("")
        for t in (self.desc_table, self.assume_table, self.posthoc_table, self.anova_table, self.grp_table,
                  self.cat_table):
            _fill(t, [])
        self.anova_lbl.setText("")
        self.corr_lbl.setText("")
        self.cat_lbl.setText("")
        self.summary_box.setPlainText("")
        for h in self._heads + self._subheads:
            h.setText("")

    def _set_head(self, i: int, title: str, sub: str = ""):
        self._heads[i].setText(escape(title))
        self._subheads[i].setText(escape(sub))

    def recompute(self):
        self._timer.stop()
        if self.project is None or not self.rows:
            self._clear_outputs()
            return
        self._measure_set = set(self.numeric_measures())
        self._fill_control()
        uses_period = self.factor.currentData() != "Period"
        self.period.setEnabled(uses_period and self.period.count() > 1)
        m = self.method.currentData()
        self.mu.setEnabled(m in ("one_t", "one_wilcoxon"))
        self.control.setEnabled(self.posthoc.currentData() == "dunnett")
        i = self.tabs.currentIndex()
        try:
            (self._compute_compare, self._compute_time_course, self._compute_correlation, self._compute_grouped,
             self._compute_categorical)[i]()
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
        method = self.method.currentData()
        paired = self.paired.isChecked() or method in st.PAIRED_METHODS
        rows = self.filtered_rows(use_period=factor != "Period")
        gv = self.group_data(rows, measure, factor, paired)
        one = method in ("one_t", "one_wilcoxon")
        if one:
            mu = self.mu.value()
            res = {"groups": list(gv), "descriptive": {k: st.descriptive(v) for k, v in gv.items()}, "posthoc": [],
                   "one_sample": {k: st.one_sample(v, mu, method == "one_t") for k, v in gv.items()}, "mu": mu,
                   "test": "One-sample t-test" if method == "one_t" else "Wilcoxon signed-rank vs value"}
            ps = [r["p"] for r in res["one_sample"].values() if r["p"] == r["p"]]
            res["p"] = min(ps) if ps else math.nan
            res["statistic"] = math.nan
        else:
            res = compare_groups(gv, parametric=self.is_parametric(), paired=paired, method=method,
                                 posthoc_method=self.posthoc.currentData(), control=self.control.currentData())
        self.result = res
        self.result_measure = measure
        names = res["groups"]
        gv = OrderedDict((k, gv[k]) for k in names if k in gv)
        if gv:
            fig = plots.group_plot(gv, measure, self.colors_for(factor), kind=self.plot_kind.currentData(),
                                   posthoc=None if one else res.get("posthoc"), p_value=None if one else res.get("p"),
                                   error=self.error.currentData(), points=self.points.isChecked(),
                                   ref_value=res.get("mu") if one else None)
        else:
            fig = _message_figure(f"No values of “{measure}”")
        self.cmp_canvas.set_figure(fig)
        # headline
        p = res.get("p", math.nan)
        html = f"<div style='font-size:16px'><b>{escape(str(res.get('test')))}</b></div>"
        if one:
            lines = []
            for g, r in res["one_sample"].items():
                sym = STAT_NAMES.get(r.get("test"), "stat")
                lines.append(f"{escape(g)}: {sym} = {_fmt(r.get('statistic'))}, {escape(_p_text(r.get('p')))}, "
                             f"d = {_fmt(r.get('effect_size'))}")
            html += f"<div style='font-size:13px'>vs {res['mu']:g}<br>{'<br>'.join(lines)}</div>"
        else:
            sym = STAT_NAMES.get(res.get("test"), "statistic")
            parts = []
            if res.get("statistic") == res.get("statistic") and res.get("statistic") is not None:
                parts.append(f"{sym} = {res['statistic']:.3f}")
            if res.get("df") is not None:
                parts.append(f"df = {_fmt(res['df'], 2)}")
            parts.append(format_p(p))
            colour = "#16a34a" if p == p and p < 0.05 else "#475569"
            html += (f"<div style='font-size:14px'>{escape(', '.join(parts))} "
                     f"<b style='color:{colour}'>{stars(p)}</b></div>")
            if "p_gg" in res:
                html += f"<div>Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}, {escape(format_p(res['p_gg']))}</div>"
            es = dict(res.get("effect_sizes") or {})
            if res.get("effect_size") is not None and res["effect_size"] == res["effect_size"]:
                es = {res.get("effect_size_name"): res["effect_size"], **es}
            es = {k: v for k, v in es.items() if v == v}
            if es:
                html += "<div>" + ", ".join(f"{escape(str(k))} = {v:.3f}" for k, v in es.items()) + "</div>"
        n_total = sum(d["n"] for d in res["descriptive"].values())
        ctx = f"by {_label(factor)}"
        extra = self._context(factor)
        if extra:
            ctx += f" · {extra}"
        if paired:
            ctx += " · same animals at each level"
        self._set_head(0, measure, f"{ctx} · N = {n_total}")
        self.test_lbl.setText(html)
        self.desc_table.setHorizontalHeaderItem(0, QTableWidgetItem(_label(factor)))
        _fill(self.desc_table, [[g, d["n"], d["mean"], d["sd"], d["sem"], d["ci95"], d["median"], d["min"],
                                 d["max"]] for g, d in res["descriptive"].items()])
        checks = []
        for g, v in gv.items():
            for name, pv in st.normality(v).items():
                checks.append([f"{name} (normality)", g, _fmt(pv), "non-normal" if pv < 0.05 else ""])
        for name, pv in st.variance_tests(gv).items():
            checks.append([f"{name} (equal variances)", "all", _fmt(pv), "unequal" if pv < 0.05 else ""])
        self.checks = checks
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
        design = self.design.currentData()
        if not measure or not x:
            self.tc_canvas.set_figure(_message_figure("Needs stages, trials or time periods"))
            self.anova = None
            return
        rows = self.filtered_rows(use_period=x != "Period")
        if x == "Period":
            rows = [r for r in rows if r.get("Period") != WHOLE]
        rows = [r for r in rows if is_number(r.get(measure))]
        by_key = by if by and by != NONE else None
        sub = f"by {_label(x)}" + (f" and {_label(by_key)}" if by_key else "")
        extra = self._context(x)
        self._set_head(1, measure, sub + (f" · {extra}" if extra else ""))
        if by_key is None:
            rows = [{**r, "_all": "All"} for r in rows]
        if not rows:
            self.tc_canvas.set_figure(_message_figure(f"No values of “{measure}”"))
            self.anova = None
            _fill(self.anova_table, [])
            return
        order = self.levels(x, rows)
        rows = sorted(rows, key=lambda r: order.index(str(r.get(x, ""))) if str(r.get(x, "")) in order else 0)
        kind = self.tc_plot.currentData()
        if kind == "line":
            fig = plots.time_course(rows, measure, x=x, by=by_key or "_all", colors=self.colors_for(by_key or ""),
                                    size=(5.2, 3.6), error=self.error.currentData(), points=self.points.isChecked(),
                                    order=order)
        else:
            fig = plots.factor_plot(rows, measure, [x] + ([by_key] if by_key else []), kind=kind,
                                    error=self.error.currentData(), points=self.points.isChecked(),
                                    colors=self.colors_for(by_key or ""), orders=self.orders([x, by_key], rows),
                                    size=(5.4, 3.6))
        self.tc_canvas.set_figure(fig)
        self.tc_measure = measure
        if by_key is None:
            if design == "mixed":
                res = st.rm_anova(rows, measure, within=x, levels=order)
                if "error" not in res:
                    res["effects"] = [{"effect": x, "SS": res["SS"], "df": res["df"][0], "F": res["F"],
                                       "p": res["p"], "p_gg": res["p_gg"], "df_error": res["df"][1]}]
                    res["factors"] = [x]
                self._show_anova(res, measure, "Repeated-measures ANOVA", f"Within animals: {_label(x)}")
                return
            self.anova = None
            self.anova_lbl.setText(f"<b>{escape(measure)}</b> across {escape(_label(x))}.<br>Optionally select a 2nd "
                                   "independent variable for a two-factor analysis, or the “Repeated” design for a "
                                   "repeated-measures ANOVA.")
            _fill(self.anova_table, [])
            return
        if design == "mixed":
            res = st.mixed_anova(rows, measure, between=by_key, within=x, levels=order)
            desc = f"{_label(by_key)} (between animals) × {_label(x)} (within animals)"
        elif design == "srh":
            res = st.scheirer_ray_hare(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{_label(by_key)} × {_label(x)}, rank-based (H statistics, χ² p-values)"
        elif design == "art":
            res = st.art_anova(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{_label(by_key)} × {_label(x)}, aligned rank transform"
        else:
            res = two_way_anova(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{_label(by_key)} × {_label(x)} (between-subjects, type II SS)"
        self._show_anova(res, measure, res.get("test", "Two-way ANOVA"), desc)

    def _show_anova(self, res, measure, title, desc):
        self.anova = res
        if "error" in res:
            err = " ".join(_label(w) for w in str(res["error"]).split(" "))
            self.anova_lbl.setText(f"<div style='font-size:16px'><b>{escape(title)}</b></div>{escape(err)}")
            _fill(self.anova_table, [])
            return
        extra = f", residual df = {res['df_residual']}" if res.get("df_residual") is not None else ""
        if "epsilon_gg" in res:
            extra += f", Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}"
        self.anova_lbl.setText(f"<div style='font-size:16px'><b>{escape(title)}</b></div>{escape(desc)}{extra}")
        h = any("H" in e for e in res["effects"])
        _set_headers(self.anova_table, ["Effect", "SS", "df", "H" if h else "F", "p", "", "p (GG)"])
        _fill(self.anova_table, [[" × ".join(_label(f) for f in str(e["effect"]).split(" × ")), e["SS"], e["df"],
                                  e.get("H", e["F"]), format_p(e["p"]), stars(e["p"]),
                                  format_p(e["p_gg"]) if "p_gg" in e else ""] for e in res["effects"]])

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
        self.reg = st.regression(xs, ys)
        self.corr_measures = (mx, my)
        extra = self._context()
        self._set_head(2, f"{my} against {mx}", f"N = {len(rows)}" + (f" · {extra}" if extra else ""))
        factor = self.corr_by.currentData()
        groups = [str(r.get(factor, "")) if factor and factor != NONE else "" for r in rows]
        if rows:
            fig = plots.scatter_plot(xs, ys, groups, mx, my, self.colors_for(factor or ""), res, self.reg)
        else:
            fig = _message_figure("No paired values")
        self.corr_canvas.set_figure(fig)
        name = {"pearson": "Pearson r", "spearman": "Spearman ρ", "kendall": "Kendall τ"}[method]
        r = res.get("r", math.nan)
        p = res.get("p", math.nan)
        strength = ""
        if r == r:
            a = abs(r)
            strength = "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else \
                "weak" if a >= 0.2 else "negligible"
            strength = f"{strength} {'positive' if r > 0 else 'negative'} correlation"
        ci = f"<div>95% CI {res['ci95'][0]:.3f} to {res['ci95'][1]:.3f}</div>" if "ci95" in res else ""
        g = self.reg
        reg = ""
        if g.get("slope") == g.get("slope"):
            reg = (f"<br><b>Linear regression</b><div>y = {g['slope']:.4g}·x + {g['intercept']:.4g}</div>"
                   f"<div>slope 95% CI {g['slope_ci'][0]:.4g} to {g['slope_ci'][1]:.4g}</div>"
                   f"<div>R² = {g['r2']:.3f}, {escape(format_p(g['p']))}</div>")
        self.corr_lbl.setText(f"<div style='font-size:16px'><b>{name} = {_fmt(r)}</b></div>"
                              f"<div style='font-size:14px'>{escape(_p_text(p))}, n = {res.get('n', 0)}</div>"
                              f"{ci}<div>{strength}</div>{reg}")

    def grouping_factors(self) -> list[str]:
        out = []
        for c in (self.f1, self.f2, self.f3):
            v = c.currentData()
            if v and v != NONE and v not in out:
                out.append(v)
        return out

    def _compute_grouped(self):
        measure = self._combo_measure(self.measure)
        factors = self.grouping_factors()
        if not measure or not factors:
            return
        rows = self.filtered_rows(use_period="Period" not in factors)
        if "Period" in factors:
            rows = [r for r in rows if r.get("Period") != WHOLE] or rows
        orders = self.orders(factors, rows)
        extra = self._context("Period" if "Period" in factors else None)
        self._set_head(3, measure, "by " + " and ".join(_label(f) for f in factors) + (f" · {extra}" if extra else ""))
        self.grouped = st.describe_by(rows, measure, factors, orders)
        self.grouped_factors = factors
        self.grouped_measure = measure
        kind = self.plot_kind.currentData()
        fig = plots.factor_plot(rows, measure, factors, kind=kind, error=self.error.currentData(),
                                points=self.points.isChecked(), colors=self.colors_for(factors[1] if len(factors) > 1
                                                                                       else factors[0]),
                                orders=orders, size=(6.4, 3.8))
        self.grp_canvas.set_figure(fig)
        _set_headers(self.grp_table, [_label(f) for f in factors] + ["n", "Mean", "SD", "SEM", "95% CI", "Median",
                                                                     "Min", "Max"])
        nf = len(factors)
        _fill(self.grp_table, [[d[f] for f in factors] + [d["n"], d["mean"], d["sd"], d["sem"], d["ci95"],
                                                         d["median"], d["min"], d["max"]] for d in self.grouped])
        for j in range(1, nf):
            self.grp_table.horizontalHeader().setSectionResizeMode(j, QHeaderView.ResizeToContents)
        self.grp_table.setMaximumHeight(16777215)
        self.grp_table.setMinimumHeight(0)

    def _compute_categorical(self):
        rf, cf = self.cat_rows.currentData(), self.cat_col.currentData()
        if not rf or not cf:
            self.cat_canvas.set_figure(_message_figure("No categorical results (e.g. search strategy) in this "
                                                       "experiment"))
            self.cat = None
            return
        if rf == cf:
            self.cat_canvas.set_figure(_message_figure("Choose a category different from the rows"))
            self.cat = None
            self.cat_lbl.setText("")
            _fill(self.cat_table, [])
            return
        rows = self.filtered_rows(use_period=True)
        extra = self._context()
        self._set_head(4, f"{_label(cf)} by {_label(rf)}", extra)
        rl, cl, T = st.contingency_table(rows, rf, cf)
        res = st.categorical_test(T, rl, cl)
        self.cat = res
        self.cat_factors = (rf, cf)
        if len(rl) and len(cl):
            self.cat_canvas.set_figure(proportions_figure(rl, cl, T))
        else:
            self.cat_canvas.set_figure(_message_figure("No data"))
        _set_headers(self.cat_table, [_label(rf)] + cl + ["Total"])
        _fill(self.cat_table, [[r] + [int(v) for v in row] + [int(row.sum())] for r, row in zip(rl, T)])
        html = f"<div style='font-size:16px'><b>{escape(str(res.get('test')))}</b></div>"
        if res.get("p") == res.get("p"):
            html += (f"<div style='font-size:14px'>χ²({res['df']}) = {res['statistic']:.3f}, "
                     f"{escape(_p_text(res['p']))}</div><div>Cramér's V = {res['effect_size']:.3f}</div>")
            if "g_test" in res:
                html += (f"<div>G-test: G = {res['g_test']['statistic']:.3f}, "
                         f"{escape(format_p(res['g_test']['p']))}</div>")
            if "fisher" in res:
                odds = res["fisher"]["odds_ratio"]
                html += (f"<div>Fisher's exact test: odds ratio = {'∞' if odds == math.inf else _fmt(odds)}, "
                         f"{escape(_p_text(res['fisher']['p']))}</div>")
            if res.get("low_expected"):
                html += ("<div style='color:#b45309'>Some expected counts are below 5: prefer Fisher's exact test "
                         "(2 × 2) or pool categories.</div>")
        self.cat_lbl.setText(html)

    # ------------------------------------------------------------------ actions
    def summary(self) -> str:
        i = self.tabs.currentIndex()
        if i == 0 and self.result:
            head = f"Compared by {self.factor.currentData()}"
            if self.factor.currentData() != "Period" and self.period.count() > 1:
                head += f", period: {self.period.currentData()}"
            if self.filter_value.isEnabled() and self.filter_value.currentData() is not None:
                head += f", only {self.filter_field.currentData()} = {self.filter_value.currentData()}"
            if "one_sample" in self.result:
                lines = [self.result_measure, f"  {self.result['test']} vs {self.result['mu']:g}"]
                for g, r in self.result["one_sample"].items():
                    lines.append(f"    {g}: n={r['descriptive']['n']}, statistic={r['statistic']:.3f}, "
                                 f"{format_p(r['p'])} {stars(r['p'])}")
                text = "\n".join(lines)
            else:
                text = summary_text(self.result, self.result_measure)
            checks = [f"    {c[0]} {c[1]}: {format_p(float(c[2])) if c[2] not in ('', '–') else 'n/a'}"
                      for c in getattr(self, "checks", [])]
            return f"{head}\n{text}" + ("\n  Assumption checks:\n" + "\n".join(checks) if checks else "")
        if i == 1 and self.anova and "effects" in self.anova:
            text = anova_text(self.anova, self.tc_measure)
            if self.anova.get("design", "between") == "between":
                text = text.replace("Two-way ANOVA", "two-way ANOVA", 1)
                text += f"\n  residual df = {self.anova['df_residual']}"
            return text
        if i == 2 and self.corr:
            mx, my = self.corr_measures
            name = {"pearson": "Pearson r", "spearman": "Spearman rho", "kendall": "Kendall tau"}[self.corr["method"]]
            s = f"{mx} vs {my}: {name} = {self.corr['r']:.3f}, {format_p(self.corr['p'])}, n = {self.corr['n']}"
            g = self.reg or {}
            if g.get("slope") == g.get("slope") and g.get("slope") is not None:
                s += (f"\n  Linear regression: slope = {g['slope']:.4g} (95% CI {g['slope_ci'][0]:.4g} to "
                      f"{g['slope_ci'][1]:.4g}), intercept = {g['intercept']:.4g}, R² = {g['r2']:.3f}, "
                      f"{format_p(g['p'])}")
            return s
        if i == 3 and self.grouped:
            lines = [f"{self.grouped_measure} by {' > '.join(self.grouped_factors)}"]
            for d in self.grouped:
                lab = " / ".join(d[f] for f in self.grouped_factors)
                lines.append(f"  {lab}: n={d['n']}, mean={d['mean']:.3f}, SD={d['sd']:.3f}, SEM={d['sem']:.3f}")
            return "\n".join(lines)
        if i == 4 and self.cat and self.cat.get("p") == self.cat.get("p"):
            rf, cf = self.cat_factors
            s = (f"{cf} by {rf}: chi-square({self.cat['df']}) = {self.cat['statistic']:.3f}, "
                 f"{format_p(self.cat['p'])}, Cramér's V = {self.cat['effect_size']:.3f}")
            if "fisher" in self.cat:
                s += f"\n  Fisher's exact test: {format_p(self.cat['fisher']['p'])}"
            return s
        return ""

    def copy_summary(self):
        text = self.summary()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.main.status("Summary copied to the clipboard")

    def copy_grouped(self):
        if self.grp_table.rowCount():
            QGuiApplication.clipboard().setText(_table_text(self.grp_table))
            self.main.status("Table copied to the clipboard")

    def save_grouped(self, path: str | None = None):
        if not self.grouped:
            return
        if path is None:
            base = str(self.project.exports_dir() / "grouped statistics.csv") if self.project.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Save table", base, TABLE_FILTER)
            if not path:
                return
        cols = self.grouped_factors + ["n", "mean", "sd", "sem", "ci95", "median", "min", "max"]
        try:
            write_table(self.grouped, path, cols, sheet="Statistics")
        except Exception as e:
            error_box(self, "Save table", e)
            return
        self.main.status(f"Saved {path}")
        return path

    def current_canvas(self) -> PlotCanvas:
        return (self.cmp_canvas, self.tc_canvas, self.corr_canvas, self.grp_canvas,
                self.cat_canvas)[self.tabs.currentIndex()]

    def copy_figure(self):
        if figure_to_clipboard(self.current_canvas().figure):
            self.main.status("Figure copied to the clipboard")

    def save_figure(self, path: str | None = None):
        canvas = self.current_canvas()
        if canvas.figure is None:
            return
        if path is None:
            base = ""
            if self.project and self.project.path:
                base = str(self.project.exports_dir() / f"{VIEWS[self.tabs.currentIndex()][1]}.png".replace(" ", "_"))
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

    # ------------------------------------------------------------------ report
    def current_tables(self) -> list[tuple[str, QTableWidget]]:
        i = self.tabs.currentIndex()
        if i == 0:
            out = [("Descriptive statistics", self.desc_table)]
            if self.posthoc_table.rowCount():
                out.append(("Post-hoc comparisons", self.posthoc_table))
            return out + [("Assumption checks", self.assume_table)]
        return [[("Analysis of variance", self.anova_table)], [], [("Descriptive statistics", self.grp_table)],
                [("Counts", self.cat_table)]][i - 1]

    def report_html(self) -> str:
        """The current analysis as a stand-alone HTML document (settings, result, tables and the graph)."""
        import base64

        i = self.tabs.currentIndex()
        title = self._heads[i].text() or VIEWS[i][1]
        parts = [f"<h1>{title}</h1>", f"<p class='sub'>{self._subheads[i].text()}</p>"]
        settings = []
        for views, widgets in self._rows_by_view:
            if VIEWS[i][0] not in views or len(widgets) != 2:
                continue
            lbl, w = widgets
            if isinstance(w, QComboBox):
                val = w.currentText()
            elif isinstance(w, QCheckBox):
                val = "Yes" if w.isChecked() else "No"
                if not lbl.text():
                    settings.append((w.text(), val))
                    continue
            elif isinstance(w, QDoubleSpinBox):
                val = f"{w.value():g}"
            elif w.findChild(QRadioButton):
                val = "Parametric" if self.is_parametric() else "Non-parametric"
            else:
                combos = w.findChildren(QComboBox)
                val = " = ".join(c.currentText() for c in combos if c.isEnabled() and c.currentText())
            if lbl.text() and w.isEnabled():
                settings.append((lbl.text(), val))
        if settings:
            parts.append("<table class='settings'>" + "".join(f"<tr><td>{escape(a)}</td><td>{escape(b)}</td></tr>"
                                                             for a, b in settings) + "</table>")
        text = {0: self.test_lbl, 1: self.anova_lbl, 2: self.corr_lbl, 4: self.cat_lbl}.get(i)
        if text is not None and text.text():
            parts.append(f"<div class='result'>{text.text()}</div>")
        fig = self.current_canvas().figure
        if fig is not None and fig.axes:
            png = base64.b64encode(plots.fig_to_png(fig, dpi=150)).decode()
            parts.append(f"<img src='data:image/png;base64,{png}' alt='graph'>")
        for name, t in self.current_tables():
            if not t.rowCount():
                continue
            heads = [t.horizontalHeaderItem(j).text() if t.horizontalHeaderItem(j) else ""
                     for j in range(t.columnCount())]
            body = "".join("<tr>" + "".join(f"<td>{escape(t.item(r, j).text() if t.item(r, j) else '')}</td>"
                                            for j in range(t.columnCount())) + "</tr>" for r in range(t.rowCount()))
            parts.append(f"<h2>{escape(name)}</h2><table><tr>" + "".join(f"<th>{escape(h)}</th>" for h in heads)
                         + f"</tr>{body}</table>")
        summary = self.summary()
        if summary:
            parts.append(f"<h2>Summary</h2><pre>{escape(summary)}</pre>")
        style = (f"body{{font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:32px;color:{theme.TEXT}}}"
                 f"h1,h2{{color:{theme.HEADING};font-weight:300}}h1{{margin-bottom:0}}.sub{{color:{theme.MUTED}}}"
                 "table{border-collapse:collapse;margin:6px 0 18px}td,th{padding:4px 10px;text-align:right;"
                 "border-bottom:1px solid #e3e3e3}td:first-child,th:first-child{text-align:left}"
                 "th{font-style:italic;font-weight:normal}table.settings td{text-align:left;border:none}"
                 "img{max-width:760px;display:block;margin:12px 0}pre{background:#f4f4f4;padding:10px}")
        project = escape(self.project.name) if self.project is not None else ""
        return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title} — {project}</title>"
                f"<style>{style}</style></head><body><p class='sub'>{project} · Statistical analysis · "
                f"{escape(VIEWS[i][1])}</p>{''.join(parts)}</body></html>")

    def save_report(self, path: str | None = None):
        if self.project is None:
            return
        if path is None:
            base = ""
            if self.project.path:
                base = str(self.project.exports_dir() / f"{VIEWS[self.tabs.currentIndex()][1]} report.html")
            path, _ = QFileDialog.getSaveFileName(self, "Save report", base, "HTML file (*.html)")
            if not path:
                return
        if not path.lower().endswith((".html", ".htm")):
            path += ".html"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report_html())
        except Exception as e:
            error_box(self, "Save report", e)
            return
        self.main.status(f"Saved {path}")
        return path
