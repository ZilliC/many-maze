"""Statistics page: group comparisons with post-hoc tests, two-factor / repeated-measures designs (learning curves),
descriptive statistics grouped by up to three factors, correlation / regression and categorical tests."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDoubleSpinBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import analyses as an
from ...core import plots
from ...core import stats as st
from ...core.analyses import NONE, WHOLE, cell, label as _label
from ...core.export import write_table
from ...core.project import result_columns
from ...core.stats import is_number, numeric_columns
from .. import ribbon, theme
from ..figures import FIG_FILTER, TABLE_FILTER, figure_to_clipboard
from ..icons import icon
from ..widgets import PlotCanvas, error_box
from ._results_cache import RowsLoader, has_periods, info_columns
from .base import Page

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


def _wide_combo() -> QComboBox:
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return c


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
            it = QTableWidgetItem(cell(v))
            if j > 0:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, j, it)
    h = t.horizontalHeader().sizeHint().height() + t.verticalHeader().defaultSectionSize() * max(1, len(rows))
    t.setFixedHeight(min(h + 2 * t.frameWidth() + 2, getattr(t, "_max_h", 240)))


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
        self.analyses: dict[int, an.Analysis] = {}  # the last analysis of each view
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
            ("Optionally colour the points by (and compare with ANCOVA)", self.corr_by, {R}),
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

        # the outputs of each view (in VIEWS order) that render its Analysis
        self._canvases = [self.cmp_canvas, self.tc_canvas, self.corr_canvas, self.grp_canvas, self.cat_canvas]
        self._headlines = [self.test_lbl, self.anova_lbl, self.corr_lbl, None, self.cat_lbl]
        self._tables = [[self.desc_table, self.posthoc_table, self.assume_table], [self.anova_table], [],
                        [self.grp_table], [self.cat_table]]
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
            return ribbon.action(self, text, ic, fn, tip, large=not small)

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

    def levels(self, col: str) -> list[str]:
        return an.level_order(self.project, self.rows, col)

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

    def _included(self) -> dict:
        """period / filt arguments of the analyses: the tests included, as mentioned in titles and summaries."""
        filt = None
        if self.filter_value.isEnabled() and self.filter_value.currentData() is not None:
            filt = (self.filter_field.currentData(), self.filter_value.currentData())
        return {"period": self.period.currentData() if self.period.count() > 1 else None, "filt": filt}

    # ------------------------------------------------------------------ compute
    def _clear_outputs(self):
        self.result = self.anova = self.corr = self.reg = self.grouped = self.cat = self.ancova = None
        self.analyses = {}
        for c in self._canvases:
            c.set_figure(plots.message_figure("No data"))
        for lbl in self._headlines:
            if lbl is not None:
                lbl.setText("")
        for t in (t for ts in self._tables for t in ts):
            _fill(t, [])
        self.summary_box.setPlainText("")
        for h in self._heads + self._subheads:
            h.setText("")

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
        try:
            a = self.analyse()
            if a is not None:
                self._render(self.tabs.currentIndex(), a)
            self.summary_box.setPlainText(self.summary())
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.main.status(f"Statistics error: {e}")

    def analyse(self) -> an.Analysis | None:
        """The analysis of the current view with the current settings (None when a measure or factor is missing)."""
        view = self.view()
        graph = dict(error=self.error.currentData(), points=self.points.isChecked())
        if view == "compare":
            factor = self.factor.currentData()
            return an.compare(self.project, self.filtered_rows(use_period=factor != "Period"),
                              self._combo_measure(self.measure), factor, self.method.currentData(),
                              self.is_parametric(), self.paired.isChecked(), self.posthoc.currentData(),
                              self.control.currentData(), self.mu.value(), self.plot_kind.currentData(),
                              **graph, **self._included())
        if view == "two":
            x = self.tc_x.currentData()
            return an.two_factor(self.project, self.filtered_rows(use_period=x != "Period"),
                                 self._combo_measure(self.measure), x, self.tc_by.currentData(),
                                 self.design.currentData(), self.tc_plot.currentData(), **graph, **self._included())
        if view == "correlation":
            return an.correlate(self.project, self.filtered_rows(), self._combo_measure(self.corr_x),
                                self._combo_measure(self.corr_y), self.corr_method.currentData(),
                                self.corr_by.currentData(), **self._included())
        if view == "grouped":
            factors = self.grouping_factors()
            return an.grouped(self.project, self.filtered_rows(use_period="Period" not in factors),
                              self._combo_measure(self.measure), factors, self.plot_kind.currentData(), **graph,
                              **self._included())
        return an.categorical(self.project, self.filtered_rows(), self.cat_rows.currentData(),
                              self.cat_col.currentData(), **self._included())

    def _render(self, i: int, a: an.Analysis):
        self.analyses[i] = a
        res = a.result
        if i == 0:
            self.result = res
        elif i == 1:
            self.anova = res
        elif i == 2:
            self.corr, self.reg = res, res["regression"]
            self.ancova = res.get("ancova")  # set by an.correlate when the points are coloured by a factor
        elif i == 3:
            self.grouped = res
        else:
            self.cat = res
        self._heads[i].setText(escape(a.title))
        self._subheads[i].setText(escape(a.subtitle))
        if self._headlines[i] is not None:
            self._headlines[i].setText(a.headline)
        self._canvases[i].set_figure(a.figure)
        for k, w in enumerate(self._tables[i]):
            t = a.tables[k] if k < len(a.tables) else None
            if t is not None:
                if w.columnCount() != len(t.headers):
                    _set_headers(w, t.headers)
                else:
                    w.setHorizontalHeaderLabels(t.headers)
            _fill(w, t.rows if t is not None else [])
        if i == 0:
            has_posthoc = bool(self.posthoc_table.rowCount())
            self.posthoc_lbl.setVisible(has_posthoc)
            self.posthoc_table.setVisible(has_posthoc)
        elif i == 3:
            for j in range(1, self.grp_table.columnCount() - len(an.DESC_KEYS)):
                self.grp_table.horizontalHeader().setSectionResizeMode(j, QHeaderView.ResizeToContents)
            self.grp_table.setMaximumHeight(16777215)
            self.grp_table.setMinimumHeight(0)

    def grouping_factors(self) -> list[str]:
        out = []
        for c in (self.f1, self.f2, self.f3):
            v = c.currentData()
            if v and v != NONE and v not in out:
                out.append(v)
        return out

    # ------------------------------------------------------------------ actions
    def analysis(self) -> an.Analysis | None:
        """The last analysis of the current view."""
        return self.analyses.get(self.tabs.currentIndex())

    def summary(self) -> str:
        a = self.analysis()
        return a.summary_text if a is not None else ""

    def copy_summary(self):
        text = self.summary()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.main.status("Summary copied to the clipboard")

    def copy_grouped(self):
        a = self.analyses.get(3)
        if a is not None and a.tables[0].rows:
            QGuiApplication.clipboard().setText(a.tables[0].text())
            self.main.status("Table copied to the clipboard")

    def save_grouped(self, path: str | None = None):
        if not self.grouped:
            return
        if path is None:
            base = str(self.project.exports_dir() / "grouped statistics.csv") if self.project.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Save table", base, TABLE_FILTER)
            if not path:
                return
        cols = [k for k in self.grouped[0] if k not in st.descriptive([])] + an.DESC_KEYS
        try:
            write_table(self.grouped, path, cols, sheet="Statistics")
        except Exception as e:
            error_box(self, "Save table", e)
            return
        self.main.status(f"Saved {path}")
        return path

    def current_canvas(self) -> PlotCanvas:
        return self._canvases[self.tabs.currentIndex()]

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
    def report_html(self) -> str:
        """The current analysis as a stand-alone HTML document (settings, result, tables and the graph)."""
        import base64

        i = self.tabs.currentIndex()
        a = self.analysis() or an.Analysis()
        title = escape(a.title) or VIEWS[i][1]
        parts = [f"<h1>{title}</h1>", f"<p class='sub'>{escape(a.subtitle)}</p>"]
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
        if a.headline:
            parts.append(f"<div class='result'>{a.headline}</div>")
        if a.figure is not None and a.figure.axes:
            png = base64.b64encode(plots.fig_to_png(a.figure, dpi=150)).decode()
            parts.append(f"<img src='data:image/png;base64,{png}' alt='graph'>")
        for t in a.tables:
            if not t.rows:
                continue
            body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in t.cells())
            parts.append(f"<h2>{escape(t.name)}</h2><table><tr>" + "".join(f"<th>{escape(h)}</th>" for h in t.headers)
                         + f"</tr>{body}</table>")
        if a.summary_text:
            parts.append(f"<h2>Summary</h2><pre>{escape(a.summary_text)}</pre>")
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
