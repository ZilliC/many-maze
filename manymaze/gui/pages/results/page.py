"""Data page: the spreadsheet of measures per test (and per time period) with its measure chooser and filters,
charts of per-frame parameters, track plots / heat maps and video export with overlays."""

from __future__ import annotations

from matplotlib.figure import Figure
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QStackedWidget,
                               QTableView, QTreeWidget, QVBoxLayout, QWidget)

from ....core.apparatus import unique_name
from ....core.project import OPTIONAL_INFO_COLUMNS, result_columns
from ....core.reports import default_report, find_report, set_default
from ....core.terminology import column_labels, term
from ... import ribbon, theme
from ...icons import icon
from ...ribbon import RibbonHost
from ...widgets import error_box
from .._results_cache import SEGMENT_COLUMNS, RowsLoader, info_columns
from ..base import Page
from .charts_panel import ChartsPanel
from .dialogs import fill_measure_tree, filter_measure_tree, measure_groups
from .exports import ExportsMixin
from .plot_views import PLOT_VIEWS, RIBBON_CONTROL_STYLE, PlotViewsMixin
from .table import ResultsModel, ResultsProxy, _names

# views of the Data page (explorer sub-items): key, label, icon, page title
VIEWS = [("spreadsheet", "Spreadsheet", "table", "Data"), ("track", "Track plots", "track", "Track plots"),
         ("heat", "Heat maps", "heatmap", "Heat maps"), ("charts", "Charts", "chart", "Charts"),
         ("video", "Video export", "video_file", "Video export")]

def table_style() -> str:
    """The Data page's style sheet in the colours of the scheme in use."""
    return f"""
QTableView#ResultsTable {{ border: none; border-top: 1px solid {theme.BORDER}; background: {theme.BASE};
    font-size: 13px; gridline-color: {theme.SHEET_GRID}; }}
QTableView#ResultsTable::item {{ padding: 0 8px; }}
QTableView#ResultsTable QHeaderView::section {{ background: {theme.HEADER_BG}; font-size: 13px; font-weight: normal;
    padding: 7px 8px; border: none; border-right: 1px solid {theme.SHEET_GRID}; border-bottom: 1px solid {theme.BORDER}; }}
QListWidget#TestList {{ border: none; border-right: 1px solid {theme.BORDER}; background: {theme.BASE};
    font-size: 13px; }}
QListWidget#TestList::item {{ padding: 5px 8px; border-bottom: 1px solid {theme.ROW_LINE}; }}
QListWidget#TestList::item:selected {{ background: {theme.SELECTION}; color: {theme.TEXT}; }}
QToolButton#ModeButton {{ border: 1px solid {theme.INPUT_BORDER}; background: {theme.BASE}; color: {theme.TEXT};
    padding: 3px 12px; }}
QToolButton#ModeButton:checked {{ background: {theme.SELECTION}; border-color: {theme.SELECTION_BORDER}; }}
QLabel#PlotCaption {{ font-size: 14px; color: {theme.TEXT}; }}
QTreeWidget::item {{ height: 22px; }}
"""


class ResultsPage(PlotViewsMixin, ExportsMixin, Page):
    title = "Results"

    def __init__(self, main):
        super().__init__(main)
        self._workers: list = []  # background jobs still running (exports, reports, heat maps)
        self.rows: list[dict] = []
        self.segmented = False
        # columns unticked in the measure chooser (kept while the project is open); rarely needed information
        # columns start unticked
        self.hidden: set[str] = set(OPTIONAL_INFO_COLUMNS)
        self.report_name: str | None = None  # the saved results report shown (Project.reports), if any
        self._report_pending: dict | None = None  # a report to apply once its rows are delivered
        self._known_cols: set[str] = set()  # columns seen since the report was applied (new ones follow it)
        self._names = _names(None)
        self._frames: dict[int, object] = {}
        self._detail: dict | None = None
        self._detail_key = None
        self._stale: set[int] = set()
        self._loading_tree = False
        self._ribbon_refresh = False
        self.view = "spreadsheet"
        self._history = ["spreadsheet"]
        self._hist_pos = 0
        self.loader = RowsLoader(self)
        self.loader.loaded.connect(self._rows_loaded)
        self.loader.failed.connect(self._load_failed)
        self.loader.progress.connect(lambda f: self.progress.setValue(int(f * 100)))
        self.loader.busy_changed.connect(self._busy_changed)
        self._holder = QWidget(self)  # home of the controls shown in the ribbon (filters, plot options)
        self._holder.hide()

        # ---- filters (shown in the ribbon: Filter / Time periods) -------------------------------------
        self.seg_check = QCheckBox("Show time periods", self._holder)
        self.seg_check.setToolTip("Also show results per time segment (time bins / custom periods — see “Set "
                                  "segment length”)")
        self.seg_check.toggled.connect(self._seg_toggled)
        self.period_combo = self._ribbon_combo(135)
        self.period_combo.setToolTip("Show only this time period")
        self.period_combo.currentIndexChanged.connect(self._apply_filters)
        self.group_combo = self._ribbon_combo(140)
        self.group_combo.setToolTip("Show only the tests of animals given this treatment")
        self.group_combo.currentIndexChanged.connect(self._apply_filters)
        self.stage_combo = self._ribbon_combo(140)
        self.stage_combo.setToolTip("Show only the tests of this stage")
        self.stage_combo.currentIndexChanged.connect(self._apply_filters)
        self.reports_combo = self._ribbon_combo(150)
        self.reports_combo.setToolTip("The saved results report shown: its measures, information columns, time "
                                      "periods and filters (Report ▸ Save as… saves what the spreadsheet shows)")
        self.reports_combo.currentIndexChanged.connect(self._report_chosen)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(180)
        self.progress.setFormat("Calculating… %p%")
        self.progress.hide()
        self.count_lbl = QLabel()
        self.count_lbl.setObjectName("Hint")

        # ---- measure chooser (“Select data”) -------------------------------------------------------
        self.chooser = QWidget()
        self.chooser.setFixedWidth(300)
        cl = QVBoxLayout(self.chooser)
        cl.setContentsMargins(0, 0, 14, 0)
        cl.setSpacing(6)
        head = QLabel("Select data")
        head.setObjectName("SectionTitle")
        cl.addWidget(head)
        hint = QLabel("Tick the measures to show in the spreadsheet.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        cl.addWidget(hint)
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
        self.measure_lbl.setObjectName("Hint")
        cl.addWidget(self.measure_lbl)
        self.chooser.hide()
        self._tree_timer = QTimer(self)
        self._tree_timer.setSingleShot(True)
        self._tree_timer.setInterval(0)
        self._tree_timer.timeout.connect(self._update_columns)

        # ---- spreadsheet ---------------------------------------------------------------------------------
        self.model = ResultsModel(self)
        self.proxy = ResultsProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setObjectName("ResultsTable")
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.AscendingOrder)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setResizeContentsPrecision(60)
        hh.setMinimumSectionSize(48)
        hh.setHighlightSections(False)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._select_changed())
        self.table.doubleClicked.connect(self._open_test)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.empty_lbl = QLabel()
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setObjectName("Hint")
        self.empty_lbl.setStyleSheet("font-size:15px;")
        self.empty_lbl.hide()
        sheet = QWidget()
        sl = QHBoxLayout(sheet)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addWidget(self.chooser)
        sl.addWidget(self.table, 1)
        sl.addWidget(self.empty_lbl, 1)

        plot_view = self._build_plot_view()

        # ---- views ------------------------------------------------------------------------------------------
        self.charts = ChartsPanel(self)
        self.main_tabs = QStackedWidget()  # 0 spreadsheet, 1 charts, 2 track plots / heat maps / video export
        self.main_tabs.addWidget(sheet)
        self.main_tabs.addWidget(self.charts)
        self.main_tabs.addWidget(plot_view)
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
        self.title_lbl = QLabel("Data")
        self.title_lbl.setObjectName("PageTitle")
        top = QHBoxLayout()
        top.addWidget(self.title_lbl)
        top.addStretch()
        top.addWidget(self.progress)
        top.addWidget(self.count_lbl)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 12, 18, 10)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.main_tabs, 1)
        theme.style(self, table_style)
        self._build_actions()
        self._update_actions()

    # ------------------------------------------------------------------ ribbon / explorer
    def _ribbon_combo(self, width: int = 150) -> QComboBox:
        c = QComboBox(self._holder)
        c.setFixedWidth(width)
        c.setStyleSheet(RIBBON_CONTROL_STYLE)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(6)
        return c

    def _build_actions(self):
        def A(text, ic, fn, tip="", checkable=False, small=False):
            return ribbon.action(self, text, ic, fn, tip, checkable, large=not small)

        self.back_act = A("Back", "back", self.go_back, "Go back to the previous view")
        self.fwd_act = A("Forward", "forward", self.go_forward, "Go forward to the next view")
        self.copy_act = A("Copy", "copy", self.copy_to_clipboard,
                          "Copy the spreadsheet (or the selected range) as tab-separated text for Excel / Prism")
        self.copy_sel_act = A("Copy selection", "copy_select", self.copy_selection, "Copy only the selected cells",
                              small=True)
        self.print_act = A("Print", "print", self.print_table, "Print the spreadsheet")
        self.save_act = A("Save", "save", self.save_table, "Save the spreadsheet")
        m = QMenu(self)
        m.addAction("CSV file…", lambda: self.save_table(suffix=".csv"))
        m.addAction("Tab-separated text…", lambda: self.save_table(suffix=".tsv"))
        m.addAction("Excel workbook (.xlsx)…", lambda: self.save_table(suffix=".xlsx"))
        m.addAction("SYLK spreadsheet (.slk)…", lambda: self.save_table(suffix=".slk"))
        m.addAction("dBase table (.dbf)…", lambda: self.save_table(suffix=".dbf"))
        m.addAction("Selected cells…", lambda: self.save_table(selection=True))
        m.addAction("One row per animal (stages / trials as columns)…", lambda: self.export_wide())
        m.addAction("Mean of each animal's trials per stage…", lambda: self.export_trial_means())
        m.addSeparator()
        m.addAction("Experiment as XML (with raw tracks)…", self.export_xml)
        m.addAction("Raw data per test (CSV)…", self.export_raw)
        m.addAction("Event log of the shown tests…", lambda: self.export_event_log())
        self.save_act.setMenu(m)
        self.report_act = A("HTML report", "report", self.html_report,
                            "Create a report with the results, statistics, track plots, heat maps and charts")
        self.email_act = A("E-mail report…", "email", lambda: self.email_report(),
                           "E-mail the results (spreadsheet and / or HTML report) through the e-mail server of an "
                           "alert device (Protocol ▸ Hardware ▸ I/O devices)", small=True)
        self.reports_act = A("Report", "list", None, "Save what the spreadsheet shows (measures, information "
                             "columns, time periods and filters) as a named report, kept in the experiment")
        m = QMenu(self)
        self.new_report_act = m.addAction(icon("new"), "New report…", lambda: self.new_report())
        self.save_report_act = m.addAction(icon("save"), "Save report", lambda: self.save_report())
        self.save_report_as_act = m.addAction(icon("save_as"), "Save report as…", lambda: self.save_report_as())
        self.delete_report_act = m.addAction(icon("delete"), "Delete report", lambda: self.delete_report())
        m.addSeparator()
        self.default_report_act = m.addAction("Default report (shown when the experiment is opened)")
        self.default_report_act.setCheckable(True)
        self.default_report_act.triggered.connect(self.set_default_report)
        self.reports_act.setMenu(m)
        self.select_act = A("Select data", "select_data", self.chooser.setVisible,
                            "Choose the measures shown in the spreadsheet", checkable=True)
        self.view_sheet_act = A("View spreadsheet", "view_table", lambda: self.set_view("spreadsheet"),
                                "Show the results spreadsheet")
        self.clear_act = A("Clear settings", "clear_settings", self.clear_settings,
                           "Show every measure and test again (clears filters, sorting and time periods)", small=True)
        self.segment_act = A("Set segment length", "clock", self.set_segment_length,
                             "Divide every test into time segments (time bins) of a set length", small=True)
        self.recalc_act = A("Recalculate", "refresh", lambda: self.reload(force=True),
                            "Recompute all results from the tracks (e.g. after changing analysis settings)", small=True)
        self.save_fig_act = A("Save figure", "save", self.save_figure, "Save the figure as PNG, PDF or SVG")
        self.copy_fig_act = A("Copy", "copy", self.copy_figure, "Copy the figure to the clipboard")
        self.open_test_act = A("Open test", "video", self._open_test, "Show the selected test in Review and score")
        self.group_heat_act = A("Treatment heat maps", "layers", self.group_heatmaps,
                                "Average heat map of each treatment (tests shown in the spreadsheet), on a common "
                                "scale, with each test's alignment applied")
        self.hot_point_act = A("Add point here", "point", lambda: self.add_hot_spot_point(),
                               "Add a point to the apparatus at the hottest spot of the heat map shown (this test's "
                               "map, or the treatment maps')")
        self.playback_act = A("Save playback", "video_file", self.export_playback,
                              "Save the animated track plot as a video (at the playback bar's speed and trail)")
        self.video_act = A("Export video", "video_file", self.export_video,
                           "Save the selected test's video with zones, track, behaviours and time stamp drawn on it")
        self.measure_act = A("Measure interval", "ruler", self.charts.measure_check.setChecked,
                             "Drag across a chart to measure the selected time interval", checkable=True)
        self.charts.measure_check.toggled.connect(self.measure_act.setChecked)
        self.peaks_act = A("Find peaks", "sparkle", lambda: self.charts.find_peaks(),
                           "Mark the peaks of the charted (non on/off) parameters")
        self.chart_save_act = A("Save figure", "save", lambda: self.charts.save_image(), "Save the chart as an image")
        self.chart_copy_act = A("Copy", "copy", self.charts.copy_image, "Copy the chart to the clipboard")
        self.chart_data_act = A("Export data", "export", lambda: self.charts.export_data(),
                                "Save the charted series (one row per frame) as CSV / tab-separated text")

    def ribbon_groups(self):
        nav = ("Navigation", [(self.back_act, "large"), (self.fwd_act, "large")])
        host = lambda *rows: self._host(*rows)  # noqa: E731
        if self.view == "charts":
            return [nav, ("Chart", [(self.measure_act, "large"), (self.peaks_act, "large")]),
                    ("Figure", [(self.chart_save_act, "large"), (self.chart_copy_act, "large"),
                                (self.chart_data_act, "large")])]
        if self.view == "track":
            return [nav, ("Body part", [host([self.part_combo])]), ("Colour by", [host([self.color_combo])]),
                    ("Show", [host([self.markers_check], [self.split_check])]),
                    ("Figure", [(self.save_fig_act, "large"), (self.copy_fig_act, "large"),
                                (self.playback_act, "large")]),
                    ("Test", [(self.open_test_act, "large"), (self.video_act, "large")])]
        if self.view == "heat":
            return [nav, ("Body part", [host([self.part_combo])]), ("Heat map of", [host([self.heat_of])]),
                    ("Scale", [host([self.heat_norm], [self.heat_max])]), ("Align", [host([self.align_combo])]),
                    ("Treatments", [(self.group_heat_act, "large")]),
                    ("Hottest spot", [(self.hot_point_act, "large")]),
                    ("Figure", [(self.save_fig_act, "large"), (self.copy_fig_act, "large")])]
        if self.view == "video":
            return [nav, ("Video", [(self.video_act, "large"), (self.open_test_act, "large")])]
        return [nav, ("Clipboard", [(self.copy_act, "large"), (self.copy_sel_act, "small")]),
                ("Spreadsheet", [(self.print_act, "large"), (self.save_act, "large"), (self.report_act, "large"),
                                 (self.email_act, "small")]),
                ("Actions", [(self.select_act, "large"), (self.view_sheet_act, "large"), (self.clear_act, "small"),
                             (self.segment_act, "small"), (self.recalc_act, "small")]),
                ("Report", [host([self.reports_combo]), (self.reports_act, "large")]),
                ("Filter", [host([term(self.project, "treatment"), self.group_combo],
                                 [term(self.project, "stage"), self.stage_combo])]),
                ("Time periods", [host([self.seg_check], [self.period_combo])])]

    def _host(self, *rows) -> RibbonHost:
        h = RibbonHost(self._holder)
        for r in rows:
            h.add_row(*r)
        h.grid.setRowStretch(0, 0)
        return h

    def explorer_items(self):
        return [(label, ic, key) for key, label, ic, _ in VIEWS]

    def show_item(self, key):
        self.set_view(key)

    def set_view(self, key: str, record: bool = True):
        """Show a view of the page: spreadsheet, track (plots), heat (maps), charts or video (export)."""
        if key not in [v[0] for v in VIEWS]:
            return
        changed = key != self.view
        if record and changed:
            del self._history[self._hist_pos + 1:]
            self._history.append(key)
            self._hist_pos = len(self._history) - 1
        self.view = key
        self.title_lbl.setText(next(v[3] for v in VIEWS if v[0] == key))
        self.main_tabs.setCurrentIndex({"spreadsheet": 0, "charts": 1}.get(key, 2))
        if key in PLOT_VIEWS:
            self.plot_stack.setCurrentIndex(1 if key == "video" else 0)
            if key == "track":
                self.mode_a.setText("Track")
                self.mode_b.setText("Speed")
                if self.tabs.currentIndex() not in (0, 2):
                    self.tabs.setCurrentIndex(0)
            elif key == "heat":
                self.mode_a.setText("This test")
                self.mode_b.setText("By treatment")
                if self.tabs.currentIndex() not in (1, 3):
                    self.tabs.setCurrentIndex(1)
            self._sync_modes()
            self._fill_test_list()
            if key == "video":
                self._update_video_panel()
        self._sync_playback()
        self.count_lbl.setVisible(key == "spreadsheet")
        self._update_actions()
        if self.project is not None:
            self.main.select_explorer(self, key)
        if changed:
            self._refresh_ribbon()

    def _refresh_ribbon(self):
        if self.project is not None and self.main.current_page() is self:
            self._ribbon_refresh = True
            try:
                self.main.refresh_ribbon()
            finally:
                self._ribbon_refresh = False

    def go_back(self):
        if self._hist_pos > 0:
            self._hist_pos -= 1
            self.set_view(self._history[self._hist_pos], record=False)

    def go_forward(self):
        if self._hist_pos < len(self._history) - 1:
            self._hist_pos += 1
            self.set_view(self._history[self._hist_pos], record=False)

    def _update_actions(self):
        self.back_act.setEnabled(self._hist_pos > 0)
        self.fwd_act.setEnabled(self._hist_pos < len(self._history) - 1)
        self.view_sheet_act.setEnabled(self.view != "spreadsheet")
        has = bool(self.rows)
        for a in (self.copy_act, self.copy_sel_act, self.print_act, self.save_act, self.report_act,
                  self.email_act, self.group_heat_act, self.hot_point_act):
            a.setEnabled(has)
        row = self.current_row() if has else None
        self.open_test_act.setEnabled(row is not None)
        self.video_act.setEnabled(row is not None)

    # ------------------------------------------------------------------ project
    def set_project(self, project):
        self.loader.cancel()
        self.rows = []
        self.hidden = set(OPTIONAL_INFO_COLUMNS)
        self._frames = {}
        self._detail = None
        self._detail_key = None
        self._names = _names(project)
        self.model.project = project
        # the experiment's default report is shown once its results are delivered (on_show)
        rep = default_report(project.reports) if project is not None else None
        self.report_name, self._report_pending, self._known_cols = (rep["name"] if rep else None), rep, set()
        self._sync_report_combo()
        self.seg_check.blockSignals(True)
        self.seg_check.setChecked(bool(rep and rep["segmented"]))
        self.seg_check.blockSignals(False)
        self.period_combo.setEnabled(self.seg_check.isChecked())
        self.segmented = False
        self.model.set_data([], [])
        self.tree.clear()
        for c in (self.track_canvas, self.heat_canvas, self.speed_canvas, self.groups_canvas):
            c.set_figure(Figure(figsize=(3, 3)))
        self.detail_lbl.setText("Select a test")
        self.test_list.clear()
        self._fill_filter_combos()
        self._charts_stale = True
        if self.main_tabs.currentIndex() == 1:
            self.charts.refresh_tests()
            self._charts_stale = False
        self._update_actions()

    def on_show(self):
        if self.project is None or self._ribbon_refresh:
            return
        self._names = _names(self.project)
        self._charts_stale = True
        if self.main_tabs.currentIndex() == 1:
            self._main_tab_changed(1)
        self.reload()
        QTimer.singleShot(0, self._follow_explorer)

    def _follow_explorer(self):
        if self.project is not None and self.main.current_page() is self:
            self.main.select_explorer(self, self.view)

    def _main_tab_changed(self, i):
        want = {0: ("spreadsheet",), 1: ("charts",), 2: PLOT_VIEWS}[i]
        if self.view not in want:  # e.g. main_tabs.setCurrentIndex(1) from a script
            self.set_view(want[0])
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
        if rows is self.rows and segmented == self.segmented and self.model.rows is rows and rows:
            return  # unchanged (cached rows delivered again when the page is shown)
        self.rows = rows
        self.segmented = segmented
        self._detail_key = None
        self._names = _names(self.project)
        self._fill_filter_combos()
        self._follow_report()
        self._build_tree()
        self._update_columns()
        if not rows:
            self.empty_lbl.setText("No results yet.\n\nTrack the tests (Test › Run tests) or score behaviours "
                                   "(Test › Review and score) to see measures here.")
        self.empty_lbl.setVisible(not rows)
        self.table.setVisible(bool(rows))
        if rows and not self.table.selectionModel().hasSelection():
            self.table.selectRow(0)
        if self.view in PLOT_VIEWS:
            self._fill_test_list()
        self._update_actions()

    def all_columns(self) -> list[str]:
        return result_columns(self.rows)

    def measure_columns(self) -> list[str]:
        info = set(info_columns(self.project))
        return [c for c in self.all_columns() if c not in info]

    def _info_shown(self) -> list[str]:
        cols = self.all_columns()
        out = []
        for c in info_columns(self.project):
            if c not in cols or (c in SEGMENT_COLUMNS and not self.segmented):
                continue
            if c not in ("Test", "Animal") and not any(str(r.get(c, "")).strip() for r in self.rows):
                continue
            if c in self.hidden:
                continue
            out.append(c)
        return out

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
            hh.resizeSection(i, max(56, min(hh.sectionSize(i) + 8, 280)))
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
            n, total = self.proxy.rowCount(), len(self.rows)
            self.count_lbl.setText(f"{n} rows" if n == total else f"{n} of {total} rows shown")

    # ------------------------------------------------------------------ filters
    def _fill_filter_combos(self):
        p = self.project
        # testing blind: the rows' Group column holds the treatment codes (Project.info_columns), never the names
        if p is not None and p.blind:
            groups = list(dict.fromkeys(c for c in (p.treatment_code(g.name) for g in p.groups) if c))
        else:
            groups = [g.name for g in p.groups] if p else []
        stages = list(p.stages) if p else []
        periods = []
        for r in self.rows:
            for key, lst in (("Group", groups), ("Stage", stages), ("Period", periods)):
                v = str(r.get(key, ""))
                if v and v not in lst:
                    lst.append(v)
        for combo, items, allname in ((self.group_combo, groups, "All treatments"),
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
        if self.view in PLOT_VIEWS:
            self._fill_test_list()

    def shown_rows(self, selected_only: bool = False) -> list[dict]:
        """Rows currently shown in the table, in display order (optionally only the selected ones)."""
        if selected_only:
            idx = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
            if len(idx) > 1:
                return [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()] for i in idx]
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
                for i in range(self.proxy.rowCount())]

    def selection_range(self, any_cells: bool = False) -> tuple[list[dict], list[str]] | None:
        """(rows, columns) spanned by the selected cells, or None if fewer than two cells (or a single whole row)
        are selected (any_cells: None only without a selection)."""
        sel = self.table.selectionModel().selectedIndexes()
        if not sel or (len(sel) < 2 and not any_cells):
            return None
        rows = sorted({i.row() for i in sel})
        cols = sorted({i.column() for i in sel})
        if len(rows) == 1 and len(cols) == len(self.model.columns) and not any_cells:
            return None
        return ([self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()] for i in rows],
                [self.model.columns[c] for c in cols])

    # ------------------------------------------------------------------ chooser tree
    def _build_tree(self):
        self._loading_tree = True
        info = [c for c in info_columns(self.project) if c in self.all_columns() and c != "Test"
                and (c not in SEGMENT_COLUMNS or self.segmented)]
        fill_measure_tree(self.tree, measure_groups(self.measure_columns(), self._names, info), self.hidden,
                          labels=column_labels(self.project, info))
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
        filter_measure_tree(self.tree, text)

    def clear_settings(self):
        """Show every measure and test again: clears the measure selection, filters, sorting and time periods."""
        if self.project is None:
            return
        self.hidden = set(OPTIONAL_INFO_COLUMNS)
        self.search.clear()
        for c in (self.group_combo, self.stage_combo, self.period_combo):
            c.setCurrentIndex(0)
        self.proxy.sort(-1)
        self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        if self.seg_check.isChecked():
            self.seg_check.setChecked(False)
        self.report_name, self._report_pending = None, None  # everything shown: no saved report
        self._sync_report_combo()
        self._build_tree()
        self._update_columns()
        self.main.status("Spreadsheet settings cleared")

    def set_segment_length(self, seconds: float | None = None):
        """Divide every test into time segments (the time bins of the analysis settings) and show them."""
        p = self.project
        if p is None:
            return
        if seconds is None:
            v, ok = QInputDialog.getDouble(self, "Set segment length",
                                           "Divide each test into segments of (seconds; 0 = no segments)",
                                           float(p.analysis.bin_length_s or 0), 0.0, 1e6, 1)
            if not ok:
                return
            seconds = v
        p.analysis.bin_length_s = float(seconds)
        self.main.mark_dirty()
        if seconds > 0 and not self.seg_check.isChecked():
            self.seg_check.setChecked(True)  # reloads
        else:
            self.reload()
        self.main.status(f"Segment length: {seconds:g} s" if seconds > 0 else "Time segments switched off")

    # ------------------------------------------------------------------ saved reports
    def current_report(self) -> dict | None:
        """The saved report shown (Project.reports), or None."""
        p = self.project
        return find_report(p.reports, self.report_name) if p is not None and self.report_name else None

    def report_settings(self) -> dict:
        """What the spreadsheet shows, as a report (see core/reports.py) without its name: the measures and
        information columns ticked, whether time periods are shown and the rows chosen in the filters (every
        measure and the usual information columns while there are no results yet)."""
        seg = self.seg_check.isChecked()
        out = {"measures": None, "info_columns": None, "segmented": seg,
               "period": (self.period_combo.currentData() or "") if seg else "",
               "treatment": self.group_combo.currentData() or "", "stage": self.stage_combo.currentData() or ""}
        if self.rows:
            cols = set(self.all_columns())
            out["measures"] = self.visible_measures()
            out["info_columns"] = [c for c in info_columns(self.project) if c in cols and c not in self.hidden]
        return out

    def _sync_report_combo(self):
        """List the experiment's reports in the ribbon combo (the default one marked) and select the one shown."""
        p = self.project
        self.reports_combo.blockSignals(True)
        self.reports_combo.clear()
        self.reports_combo.addItem("- None -", None)
        for r in p.reports if p is not None else []:
            self.reports_combo.addItem(f"{r['name']} (default)" if r.get("default") else r["name"], r["name"])
        self.reports_combo.setCurrentIndex(max(0, self.reports_combo.findData(self.report_name)))
        self.reports_combo.blockSignals(False)
        rep = self.current_report()
        for a in (self.save_report_act, self.delete_report_act, self.default_report_act):
            a.setEnabled(rep is not None)
        self.save_report_as_act.setEnabled(p is not None)
        self.new_report_act.setEnabled(p is not None)
        self.default_report_act.setChecked(bool(rep and rep.get("default")))

    def _report_chosen(self, _i=None):
        self.show_report(self.reports_combo.currentData())

    def show_report(self, name: str | None):
        """Show a saved report: its measures, information columns, time periods and filters (None: no report; the
        spreadsheet keeps what it shows)."""
        rep = find_report(self.project.reports, name) if self.project is not None and name else None
        self.report_name, self._report_pending = (rep["name"] if rep else None), rep
        self._sync_report_combo()
        if rep is None:
            return
        changed = self.seg_check.isChecked() != rep["segmented"]
        self.seg_check.blockSignals(True)
        self.seg_check.setChecked(rep["segmented"])
        self.seg_check.blockSignals(False)
        self.period_combo.setEnabled(rep["segmented"])
        if changed or not self.rows:
            self.reload()  # applied when the rows arrive (_rows_loaded)
        else:
            self._follow_report()
            self._build_tree()
            self._update_columns()
        self.main.status(f"Report “{rep['name']}”")

    def _follow_report(self):
        """With freshly delivered rows: apply a report waiting for them; else hide the measures that appeared since
        (e.g. a new zone's) when the report shown lists its measures."""
        cols = set(self.all_columns())
        new, self._known_cols = cols - self._known_cols, cols | self._known_cols
        rep, pending = self.current_report(), self._report_pending
        if pending is not None and self.rows:
            self._report_pending = None
            self._known_cols = set(cols)
            self._apply_report(pending)
        elif rep is not None and rep.get("measures") is not None:
            keep = set(rep["measures"])
            self.hidden.update(c for c in self.measure_columns() if c in new and c not in keep)

    def _apply_report(self, rep: dict):
        measures = self.measure_columns()
        self.hidden.difference_update(measures)
        if rep.get("measures") is not None:
            keep = set(rep["measures"])
            self.hidden.update(c for c in measures if c not in keep)
        info = info_columns(self.project)
        self.hidden.difference_update(info)
        if rep.get("info_columns") is not None:
            keep = set(rep["info_columns"])
            self.hidden.update(c for c in info if c not in keep)
        else:
            self.hidden.update(OPTIONAL_INFO_COLUMNS)
        for combo, key in ((self.group_combo, "treatment"), (self.stage_combo, "stage"),
                           (self.period_combo, "period")):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(rep.get(key) or None)))
            combo.blockSignals(False)
        self._apply_filters()

    def _ask_report_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Name of the report", text=default)
        name = name.strip() if ok else ""
        return name or None

    def new_report(self, name: str | None = None):
        """A new report showing every measure and the usual information columns (whole tests, no filter)."""
        p = self.project
        if p is None:
            return None
        if name is None:
            name = self._ask_report_name("New report", unique_name(f"Report {len(p.reports) + 1}",
                                                                   [r["name"] for r in p.reports]))
            if name is None:
                return None
        name = unique_name(name, [r["name"] for r in p.reports])
        p.reports.append({"name": name, "measures": None, "info_columns": None, "segmented": False, "period": "",
                          "treatment": "", "stage": "", "default": False})
        self.main.mark_dirty()
        self.show_report(name)
        return name

    def save_report(self):
        """Save what the spreadsheet shows into the report shown (Save as… without one)."""
        rep = self.current_report()
        if rep is None:
            return self.save_report_as()
        rep.update(self.report_settings())
        self.main.mark_dirty()
        self.main.status(f"Report “{rep['name']}” saved")
        return rep["name"]

    def save_report_as(self, name: str | None = None, confirm: bool = True):
        """Save what the spreadsheet shows as a new report (or replace the report of that name)."""
        p = self.project
        if p is None:
            return None
        if name is None:
            cur = self.current_report()
            name = self._ask_report_name("Save report as", unique_name(
                f"{cur['name']} copy" if cur else f"Report {len(p.reports) + 1}", [r["name"] for r in p.reports]))
            if name is None:
                return None
        rep = find_report(p.reports, name)
        if rep is not None and confirm and QMessageBox.question(
                self, "Save report as", f"Replace the report “{name}”?") != QMessageBox.Yes:
            return None
        if rep is None:
            rep = {"name": name, "default": False}
            p.reports.append(rep)
        rep.update(self.report_settings())
        self.report_name = name
        self.main.mark_dirty()
        self._sync_report_combo()
        self.main.status(f"Report “{name}” saved")
        return name

    def delete_report(self, confirm: bool = True):
        p, rep = self.project, self.current_report()
        if rep is None:
            return False
        if confirm and QMessageBox.question(self, "Delete report", f"Delete the report “{rep['name']}”? (The "
                                            "spreadsheet keeps showing what it shows.)") != QMessageBox.Yes:
            return False
        p.reports.remove(rep)
        self.report_name = None
        self.main.mark_dirty()
        self._sync_report_combo()
        return True

    def set_default_report(self, on: bool = True):
        """Make the report shown the one the Data page shows when the experiment is opened (on=False: none)."""
        rep = self.current_report()
        if rep is None:
            return
        set_default(self.project.reports, rep["name"] if on else None)
        self.main.mark_dirty()
        self._sync_report_combo()
        self.main.status(f"“{rep['name']}” is the default report" if on else "No default report")

    def _table_menu(self, pos):
        m = QMenu(self)
        m.addAction(icon("copy"), "Copy", self.copy_to_clipboard)
        m.addAction(icon("copy_select"), "Copy selection", self.copy_selection)
        m.addAction("Copy without headers", lambda: QGuiApplication.clipboard().setText(
            self.clipboard_text(header=False)))
        m.addAction("Save selected cells…", lambda: self.save_table(selection=True))
        m.addSeparator()
        m.addAction(icon("video"), "Open test", self._open_test)
        m.addAction(icon("track"), "Show track plot", lambda: self.set_view("track"))
        m.addAction(icon("heatmap"), "Show heat map", lambda: self.set_view("heat"))
        m.addAction(icon("chart"), "Show in charts", lambda: self.show_charts())
        m.addAction(icon("video_file"), "Export video with overlays…", lambda: self.export_video())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def show_charts(self, test_id=None):
        r = self.current_row()
        tid = test_id if test_id is not None else (r.get("Test") if r else None)
        self.main_tabs.setCurrentWidget(self.charts)
        if tid is not None:
            self.charts.select_test(tid)

    # ------------------------------------------------------------------ selection
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
        if r is not None:
            self.main.open_test(r.get("Test"))

