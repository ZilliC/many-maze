"""Statistics page: group comparisons with post-hoc tests, two-factor / repeated-measures designs (learning curves),
descriptive statistics grouped by up to three factors, correlation / regression and categorical tests."""

from __future__ import annotations

import math
from collections import OrderedDict
from html import escape

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from ...core import plots
from ...core import stats as st
from ...core.export import write_table
from ...core.project import result_columns
from ...core.stats import (anova_text, compare_groups, correlation, format_p, stars, summary_text,
                           two_way_anova)
from ..widgets import PlotCanvas, error_box
from ._results_cache import RowsLoader, has_periods, info_columns
from .base import Page
from .results import FIG_FILTER, TABLE_FILTER, figure_to_clipboard, is_number, numeric_columns

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


def _table(headers: list[str], max_h: int = 240) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.ContiguousSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.verticalHeader().setDefaultSectionSize(22)
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

        # ---- controls --------------------------------------------------------
        box = QGroupBox("Analysis")
        f = QFormLayout(box)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        f.setVerticalSpacing(5)
        self.measure = self._measure_combo()
        self.factor = QComboBox()
        self.period = QComboBox()
        self.period.setToolTip("Time period of the results to analyse (time bins are set on the Experiment page)")
        self.filter_field = QComboBox()
        self.filter_value = QComboBox()
        self.parametric = QComboBox()
        self.parametric.addItem("Parametric", True)
        self.parametric.addItem("Non-parametric", False)
        self.parametric.setToolTip("Used by the automatic test choice: t-test / ANOVA or Mann-Whitney / "
                                   "Kruskal-Wallis (Wilcoxon / Friedman when paired)")
        self.method = QComboBox()
        for k, v in METHODS:
            self.method.addItem(v, k)
        self.method.setMaxVisibleItems(20)
        self.method.setToolTip("Statistical test; Automatic picks one from the number of levels, the parametric "
                               "choice and pairing")
        self.posthoc = QComboBox()
        for k, v in st.POSTHOC.items():
            self.posthoc.addItem(v, k)
        self.posthoc.setToolTip("Pairwise comparisons after a significant test of more than two levels")
        self.control = QComboBox()
        self.control.setToolTip("Control level for Dunnett's test")
        self.mu = QDoubleSpinBox()
        self.mu.setRange(-1e9, 1e9)
        self.mu.setDecimals(3)
        self.mu.setToolTip("Reference value for one-sample tests (e.g. 50 % alternation, discrimination index 0)")
        self.paired = QCheckBox("Repeated measures (pair by animal)")
        self.paired.setToolTip("Within-animal comparison, e.g. Stage or Period: paired t / Wilcoxon / "
                               "repeated-measures ANOVA / Friedman")
        f.addRow("Measure", self.measure)
        f.addRow("Compare", self.factor)
        f.addRow("Period", self.period)
        fr = QHBoxLayout()
        fr.addWidget(self.filter_field, 1)
        fr.addWidget(QLabel("="))
        fr.addWidget(self.filter_value, 1)
        f.addRow("Only", fr)
        f.addRow("Family", self.parametric)
        f.addRow("Test", self.method)
        f.addRow("Post-hoc", self.posthoc)
        f.addRow("Control", self.control)
        f.addRow("Test value", self.mu)
        f.addRow(self.paired)
        gbox = QGroupBox("Graph")
        g = QFormLayout(gbox)
        g.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        g.setVerticalSpacing(5)
        self.plot_kind = QComboBox()
        for k, v in GRAPHS:
            self.plot_kind.addItem(v, k)
        self.error = QComboBox()
        for k, v in plots.ERRORS.items():
            self.error.addItem(v, k)
        self.points = QCheckBox("Show individual values")
        self.points.setChecked(True)
        g.addRow("Graph", self.plot_kind)
        g.addRow("Error bars", self.error)
        g.addRow(self.points)
        for w in (self.factor, self.period, self.filter_value, self.parametric, self.plot_kind, self.method,
                  self.posthoc, self.control, self.error):
            w.currentIndexChanged.connect(self._schedule)
        self.filter_field.currentIndexChanged.connect(self._filter_field_changed)
        self.paired.toggled.connect(self._schedule)
        self.points.toggled.connect(self._schedule)
        self.mu.valueChanged.connect(self._schedule)

        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color:palette(mid);")
        recalc = QPushButton("Recalculate results")
        recalc.clicked.connect(lambda: self.reload(force=True))
        copy = QPushButton("Copy summary")
        copy.setToolTip("Copy a text summary of the current analysis")
        copy.clicked.connect(self.copy_summary)
        save = QPushButton("Save figure…")
        save.clicked.connect(lambda: self.save_figure())
        copyfig = QPushButton("Copy figure")
        copyfig.clicked.connect(self.copy_figure)
        left_in = QWidget()
        ll = QVBoxLayout(left_in)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(box)
        ll.addWidget(gbox)
        b1 = QHBoxLayout()
        b1.addWidget(copy)
        b1.addWidget(recalc)
        b2 = QHBoxLayout()
        b2.addWidget(save)
        b2.addWidget(copyfig)
        ll.addLayout(b1)
        ll.addLayout(b2)
        ll.addWidget(self.status_lbl)
        ll.addWidget(QLabel("<b>Summary</b>"))
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.summary_box.setStyleSheet("font-family:monospace;font-size:11px;")
        self.summary_box.setMinimumHeight(110)
        ll.addWidget(self.summary_box, 1)
        left = QScrollArea()
        left.setWidget(left_in)
        left.setWidgetResizable(True)
        left.setFrameShape(QScrollArea.NoFrame)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setFixedWidth(340)

        # ---- tab 1: compare groups --------------------------------------------
        self.cmp_canvas = PlotCanvas()
        self.cmp_canvas.setMinimumSize(340, 300)
        self.test_lbl = QLabel()
        self.test_lbl.setWordWrap(True)
        self.test_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.desc_table = _table(["Group", "n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"], 200)
        self.assume_table = _table(["Check", "Group", "p", ""], 200)
        ah = self.assume_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        for j in (1, 2, 3):
            ah.setSectionResizeMode(j, QHeaderView.ResizeToContents)
        self.posthoc_table = _table(["Comparison", "Difference", "p", "", "Method"], 200)
        self.posthoc_lbl = QLabel("<b>Post-hoc comparisons</b>")
        res = QWidget()
        rl = QVBoxLayout(res)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.addWidget(self.test_lbl)
        rl.addSpacing(6)
        rl.addWidget(self.posthoc_lbl)
        rl.addWidget(self.posthoc_table)
        rl.addWidget(QLabel("<b>Assumption checks</b>"))
        rl.addWidget(self.assume_table)
        rl.addStretch()
        rs = QScrollArea()
        rs.setWidget(res)
        rs.setWidgetResizable(True)
        rs.setFrameShape(QScrollArea.NoFrame)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.cmp_canvas)
        top.addWidget(rs)
        top.setSizes([460, 400])
        top.setChildrenCollapsible(False)
        cmp = QWidget()
        cml = QVBoxLayout(cmp)
        cml.addWidget(top, 1)
        cml.addWidget(QLabel("<b>Descriptive statistics</b>"))
        cml.addWidget(self.desc_table)

        # ---- tab 2: two factors / across stages / periods ----------------------------
        self.tc_x = QComboBox()
        self.tc_by = QComboBox()
        self.design = QComboBox()
        for k, v in DESIGNS:
            self.design.addItem(v, k)
        self.design.setToolTip("Between subjects: every row independent. Repeated: animals measured at every "
                               "X level (mixed ANOVA with the Lines factor between animals; Greenhouse-Geisser "
                               "corrected p-values). Non-parametric: rank-based alternatives.")
        self.tc_plot = QComboBox()
        for k, v in [("line", "Line (mean ± error)")] + GRAPHS:
            self.tc_plot.addItem(v, k)
        for w in (self.tc_x, self.tc_by, self.design, self.tc_plot):
            w.currentIndexChanged.connect(self._schedule)
        tcbar = QHBoxLayout()
        tcbar.addWidget(QLabel("X axis"))
        tcbar.addWidget(self.tc_x)
        tcbar.addSpacing(8)
        tcbar.addWidget(QLabel("Lines"))
        tcbar.addWidget(self.tc_by)
        tcbar.addSpacing(8)
        tcbar.addWidget(QLabel("Design"))
        tcbar.addWidget(self.design, 1)
        tcbar.addSpacing(8)
        tcbar.addWidget(self.tc_plot)
        self.tc_canvas = PlotCanvas()
        self.tc_canvas.setMinimumSize(340, 280)
        self.anova_lbl = QLabel()
        self.anova_lbl.setWordWrap(True)
        self.anova_table = _table(["Effect", "SS", "df", "F", "p", "", "p (GG)"])
        tc = QWidget()
        tl = QVBoxLayout(tc)
        tl.addLayout(tcbar)
        tl.addWidget(self.tc_canvas, 1)
        tl.addWidget(self.anova_lbl)
        tl.addWidget(self.anova_table)

        # ---- tab 3: correlation / regression ------------------------------------------
        self.corr_x = self._measure_combo()
        self.corr_y = self._measure_combo()
        self.corr_method = QComboBox()
        self.corr_method.addItem("Pearson", "pearson")
        self.corr_method.addItem("Spearman", "spearman")
        self.corr_method.addItem("Kendall", "kendall")
        self.corr_by = QComboBox()
        self.corr_by.setToolTip("Colour the points by")
        for w in (self.corr_method, self.corr_by):
            w.currentIndexChanged.connect(self._schedule)
        cbar = QHBoxLayout()
        cbar.addWidget(QLabel("X"))
        cbar.addWidget(self.corr_x, 1)
        cbar.addWidget(QLabel("Y"))
        cbar.addWidget(self.corr_y, 1)
        cbar.addWidget(self.corr_method)
        cbar.addWidget(QLabel("Colour"))
        cbar.addWidget(self.corr_by)
        self.corr_canvas = PlotCanvas()
        self.corr_canvas.setMinimumSize(340, 280)
        self.corr_lbl = QLabel()
        self.corr_lbl.setWordWrap(True)
        self.corr_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cr_w = QWidget()
        crl = QVBoxLayout(cr_w)
        crl.setContentsMargins(6, 0, 0, 0)
        crl.addWidget(self.corr_lbl)
        crl.addStretch()
        c_split = QSplitter(Qt.Horizontal)
        c_split.addWidget(self.corr_canvas)
        c_split.addWidget(cr_w)
        c_split.setSizes([540, 300])
        c_split.setChildrenCollapsible(False)
        cw = QWidget()
        cwl = QVBoxLayout(cw)
        cwl.addLayout(cbar)
        cwl.addWidget(c_split, 1)

        # ---- tab 4: grouped descriptive statistics (up to 3 levels) ---------------------
        self.f1, self.f2, self.f3 = QComboBox(), QComboBox(), QComboBox()
        gbar = QHBoxLayout()
        for lbl, w in (("Group by", self.f1), ("then", self.f2), ("then", self.f3)):
            gbar.addWidget(QLabel(lbl))
            gbar.addWidget(w, 1)
            w.currentIndexChanged.connect(self._schedule)
        gcopy = QPushButton("Copy table")
        gcopy.clicked.connect(self.copy_grouped)
        gsave = QPushButton("Save table…")
        gsave.clicked.connect(lambda: self.save_grouped())
        gbar.addWidget(gcopy)
        gbar.addWidget(gsave)
        self.grp_canvas = PlotCanvas()
        self.grp_canvas.setMinimumSize(340, 260)
        self.grp_table = _table(["Level", "n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"], 10000)
        self.grp_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        g_split = QSplitter(Qt.Vertical)
        g_split.addWidget(self.grp_canvas)
        g_split.addWidget(self.grp_table)
        g_split.setSizes([420, 220])
        g_split.setChildrenCollapsible(False)
        gw = QWidget()
        gwl = QVBoxLayout(gw)
        gwl.addLayout(gbar)
        gwl.addWidget(g_split, 1)

        # ---- tab 5: categorical results ---------------------------------------------
        self.cat_rows = QComboBox()
        self.cat_col = QComboBox()
        self.cat_col.setToolTip("A categorical result (e.g. search strategy, first choice) or a factor")
        for w in (self.cat_rows, self.cat_col):
            w.currentIndexChanged.connect(self._schedule)
        kbar = QHBoxLayout()
        kbar.addWidget(QLabel("Rows"))
        kbar.addWidget(self.cat_rows, 1)
        kbar.addWidget(QLabel("Category"))
        kbar.addWidget(self.cat_col, 2)
        self.cat_canvas = PlotCanvas()
        self.cat_canvas.setMinimumSize(340, 260)
        self.cat_lbl = QLabel()
        self.cat_lbl.setWordWrap(True)
        self.cat_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cat_table = _table(["", "Total"])
        k_right = QWidget()
        krl = QVBoxLayout(k_right)
        krl.setContentsMargins(6, 0, 0, 0)
        krl.addWidget(self.cat_lbl)
        krl.addWidget(QLabel("<b>Counts</b>"))
        krl.addWidget(self.cat_table)
        krl.addStretch()
        k_split = QSplitter(Qt.Horizontal)
        k_split.addWidget(self.cat_canvas)
        k_split.addWidget(k_right)
        k_split.setSizes([500, 340])
        k_split.setChildrenCollapsible(False)
        kw = QWidget()
        kwl = QVBoxLayout(kw)
        kwl.addLayout(kbar)
        kwl.addWidget(k_split, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(cmp, "Compare groups")
        self.tabs.addTab(tc, "Two factors / time course")
        self.tabs.addTab(cw, "Correlation")
        self.tabs.addTab(gw, "Grouped (3 levels)")
        self.tabs.addTab(kw, "Categorical")
        self.tabs.setTabToolTip(1, "Learning curves and two-factor designs: two-way, mixed (repeated) and "
                                   "non-parametric ANOVAs")
        self.tabs.setTabToolTip(3, "Mean / SD / SEM / CI for every combination of up to three factors")
        self.tabs.setTabToolTip(4, "Chi-square, G-test and Fisher's exact test on categorical results")
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
        c.setMinimumContentsLength(16)
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
                  "corr_method": self.corr_method, "parametric": self.parametric, "method": self.method,
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
            res = compare_groups(gv, parametric=bool(self.parametric.currentData()), paired=paired, method=method,
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
        html = f"<div style='font-size:15px'><b>{escape(str(res.get('test')))}</b></div>"
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
                html += f"<div>Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}, {format_p(res['p_gg'])}</div>"
            es = dict(res.get("effect_sizes") or {})
            if res.get("effect_size") is not None and res["effect_size"] == res["effect_size"]:
                es = {res.get("effect_size_name"): res["effect_size"], **es}
            es = {k: v for k, v in es.items() if v == v}
            if es:
                html += "<div>" + ", ".join(f"{escape(str(k))} = {v:.3f}" for k, v in es.items()) + "</div>"
        n_total = sum(d["n"] for d in res["descriptive"].values())
        ctx = f"{measure} by {factor}"
        extra = self._context(factor)
        if extra:
            ctx += f" · {extra}"
        if paired:
            ctx += " · paired by animal"
        html += f"<div style='color:#64748b'>{escape(ctx)} · N = {n_total}</div>"
        self.test_lbl.setText(html)
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
                self._show_anova(res, measure, f"Repeated-measures ANOVA — {measure}", f"Within animals: {x}")
                return
            self.anova = None
            self.anova_lbl.setText(f"<b>{escape(measure)}</b> across {x}.<br>Choose a grouping for “Lines” for a "
                                   "two-factor analysis, or the “Repeated” design for a repeated-measures ANOVA.")
            _fill(self.anova_table, [])
            return
        if design == "mixed":
            res = st.mixed_anova(rows, measure, between=by_key, within=x, levels=order)
            desc = f"{by_key} (between animals) × {x} (within animals)"
        elif design == "srh":
            res = st.scheirer_ray_hare(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{by_key} × {x}, rank-based (H statistics, χ² p-values)"
        elif design == "art":
            res = st.art_anova(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{by_key} × {x}, aligned rank transform"
        else:
            res = two_way_anova(rows, measure, factor_a=by_key, factor_b=x)
            desc = f"{by_key} × {x} (between-subjects, type II SS)"
        self._show_anova(res, measure, f"{res.get('test', 'Two-way ANOVA')} — {measure}", desc)

    def _show_anova(self, res, measure, title, desc):
        self.anova = res
        if "error" in res:
            self.anova_lbl.setText(f"<b>{escape(title)}</b>: {escape(res['error'])}")
            _fill(self.anova_table, [])
            return
        extra = f", residual df = {res['df_residual']}" if res.get("df_residual") is not None else ""
        if "epsilon_gg" in res:
            extra += f", Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}"
        self.anova_lbl.setText(f"<b>{escape(title)}</b><br>{escape(desc)}{extra}")
        h = any("H" in e for e in res["effects"])
        _set_headers(self.anova_table, ["Effect", "SS", "df", "H" if h else "F", "p", "", "p (GG)"])
        _fill(self.anova_table, [[e["effect"], e["SS"], e["df"], e.get("H", e["F"]), format_p(e["p"]), stars(e["p"]),
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
                   f"<div>R² = {g['r2']:.3f}, {format_p(g['p'])}</div>")
        self.corr_lbl.setText(f"<div style='font-size:15px'><b>{name} = {_fmt(r)}</b></div>"
                              f"<div style='font-size:14px'>{escape(_p_text(p))}, n = {res.get('n', 0)}</div>"
                              f"{ci}<div>{strength}</div>{reg}<br>"
                              f"<div style='color:#64748b'>X: {escape(mx)}<br>Y: {escape(my)}</div>")

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
        self.grouped = st.describe_by(rows, measure, factors, orders)
        self.grouped_factors = factors
        self.grouped_measure = measure
        kind = self.plot_kind.currentData()
        fig = plots.factor_plot(rows, measure, factors, kind=kind, error=self.error.currentData(),
                                points=self.points.isChecked(), colors=self.colors_for(factors[1] if len(factors) > 1
                                                                                       else factors[0]),
                                orders=orders, size=(6.4, 3.8))
        self.grp_canvas.set_figure(fig)
        _set_headers(self.grp_table, factors + ["n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"])
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
        rl, cl, T = st.contingency_table(rows, rf, cf)
        res = st.categorical_test(T, rl, cl)
        self.cat = res
        self.cat_factors = (rf, cf)
        if len(rl) and len(cl):
            self.cat_canvas.set_figure(proportions_figure(rl, cl, T))
        else:
            self.cat_canvas.set_figure(_message_figure("No data"))
        _set_headers(self.cat_table, [rf] + cl + ["Total"])
        _fill(self.cat_table, [[r] + [int(v) for v in row] + [int(row.sum())] for r, row in zip(rl, T)])
        html = f"<div style='font-size:15px'><b>{escape(str(res.get('test')))}</b></div>"
        if res.get("p") == res.get("p"):
            html += (f"<div style='font-size:14px'>χ²({res['df']}) = {res['statistic']:.3f}, "
                     f"{escape(_p_text(res['p']))}</div><div>Cramér's V = {res['effect_size']:.3f}</div>")
            if "g_test" in res:
                html += f"<div>G-test: G = {res['g_test']['statistic']:.3f}, {format_p(res['g_test']['p'])}</div>"
            if "fisher" in res:
                odds = res["fisher"]["odds_ratio"]
                html += (f"<div>Fisher's exact test: odds ratio = {'∞' if odds == math.inf else _fmt(odds)}, "
                         f"{escape(_p_text(res['fisher']['p']))}</div>")
            if res.get("low_expected"):
                html += ("<div style='color:#b45309'>Some expected counts are below 5: prefer Fisher's exact test "
                         "(2 × 2) or pool categories.</div>")
        html += f"<div style='color:#64748b'>{escape(cf)} by {escape(rf)}</div>"
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
