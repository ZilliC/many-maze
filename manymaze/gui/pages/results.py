"""Results page: table of measures per test (and per time period), column chooser, exports and track plots."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QProgressBar, QPushButton, QSplitter, QTableView, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import plots
from ...core.export import html_report, write_csv, write_xlsx
from ...core.measures import kinematics, time_periods
from ...core.project import result_columns
from ...core.video import VideoSource
from ..widgets import PlotCanvas, error_box, run_with_progress
from ._results_cache import RowsLoader, info_columns
from .base import Page

GENERAL_PREFIXES = ("Test duration", "Detection", "Total distance", "Mean speed", "Max speed", "Time mobile",
                    "Time immobile", "Immobile episodes", "Latency to first immobility", "Time freezing",
                    "Freezing", "Latency to first freezing", "Mean freezing episode", "Mean motion",
                    "Path efficiency", "Absolute turn angle", "Meander", "Rotations", "Mean distance from wall",
                    "Thigmotaxis", "Time outside arena")
CATEGORY_ORDER = ["Information", "General", "Zones", "Points of interest", "Lines", "Test-specific", "Behaviours",
                  "Social", "Other"]
FIG_FILTER = "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)"


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
    """Choose measures for the statistics section of the HTML report."""

    def __init__(self, measures: list[str], preselect: list[str], parent=None):
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
        self.plots = QCheckBox("Include track plots and heatmaps for every test")
        self.plots.setChecked(True)
        lay.addWidget(self.plots)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create report…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, 520)

    def _filter(self, text):
        t = text.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def selected(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


# ------------------------------------------------------------------ page
class ResultsPage(Page):
    title = "Results"

    def __init__(self, main):
        super().__init__(main)
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
        em.addAction("Excel workbook (.xlsx)…", self.export_xlsx)
        export.setMenu(em)
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the shown table (or the selected rows) as tab-separated text for Excel / Prism")
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
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setResizeContentsPrecision(60)
        hh.setMinimumSectionSize(48)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._select_changed())
        self.table.doubleClicked.connect(self._open_test)
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
        for c, name in ((self.track_canvas, "Track"), (self.heat_canvas, "Heatmap"),
                        (self.speed_canvas, "Speed"), (self.groups_canvas, "Groups")):
            c.setMinimumSize(260, 260)
            self.tabs.addTab(c, name)
        self.tabs.setTabToolTip(2, "Speed over time; freezing episodes shaded")
        self.tabs.setTabToolTip(3, "Average occupancy heatmap per group (click “Group heatmaps”)")
        self.tabs.currentChanged.connect(lambda *_: self._render_current())
        dl.addWidget(self.tabs, 1)
        db = QHBoxLayout()
        gh = QPushButton("Group heatmaps")
        gh.setToolTip("Average occupancy heatmap of each group (tests shown in the table)")
        gh.clicked.connect(self.group_heatmaps)
        ot = QPushButton("Open test")
        ot.setToolTip("Show the selected test in the test viewer")
        ot.clicked.connect(lambda: self._open_test())
        sf = QPushButton("Save figure…")
        sf.clicked.connect(self.save_figure)
        db.addWidget(gh)
        db.addWidget(ot)
        db.addStretch()
        db.addWidget(sf)
        dl.addLayout(db)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(chooser)
        split.addWidget(tw)
        split.addWidget(detail)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([225, 610, 375])
        split.setChildrenCollapsible(False)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

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

    def on_show(self):
        if self.project is None:
            return
        self._names = _names(self.project)
        self.reload()

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

    def clipboard_text(self) -> str:
        cols = self.shown_columns()
        lines = ["\t".join(cols)]
        for r in self.shown_rows(selected_only=True):
            lines.append("\t".join(raw_value(r.get(c)).replace("\t", " ") for c in cols))
        return "\n".join(lines) + "\n"

    def copy_to_clipboard(self):
        if not self.rows:
            return
        text = self.clipboard_text()
        QGuiApplication.clipboard().setText(text)
        self.main.status(f"Copied {text.count(chr(10)) - 1} rows × {len(self.shown_columns())} columns")

    def _report_preselect(self, measures: list[str]) -> list[str]:
        pre = [m for m in measures if measure_category(m, self._names)[0] == "Test-specific"]
        pre += [m for m in measures if m.startswith(("Total distance", "Centre: time (%)", "Center: time (%)",
                                                     "Freezing (%)"))]
        return pre[:8]

    def html_report(self, path: str | None = None, stats_measures: list[str] | None = None,
                    include_plots: bool | None = None):
        if self.project is None or not self.rows:
            return
        measures = self.visible_measures()
        if stats_measures is None:
            numeric = numeric_columns(self.rows, measures)
            dlg = ReportDialog(numeric, self._report_preselect(numeric), self)
            if dlg.exec() != QDialog.Accepted:
                return
            stats_measures = dlg.selected()
            include_plots = dlg.plots.isChecked()
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save HTML report", self._default_path(".html")
                                                  .replace(" results", " report"), "HTML file (*.html)")
            if not path:
                return
        p = self.project
        ids = []
        for r in self.shown_rows():
            if r.get("Test") not in ids:
                ids.append(r.get("Test"))
        tests = [t for t in (p.get_test(i) for i in ids) if t is not None]
        plots_on = True if include_plots is None else include_plots

        def work(progress, stop):
            return html_report(p, path, tests=tests, include_plots=plots_on, measures=measures,
                               stats_measures=stats_measures)

        def done(out):
            self.main.status(f"Report saved: {out}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

        return run_with_progress(self, "Creating HTML report", work, on_done=done, cancellable=False)

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
        if not tracks or ai >= len(tracks):
            self._detail = None
            self.detail_lbl.setText(f"{title} — no track")
            for c in (self.track_canvas, self.heat_canvas, self.speed_canvas):
                c.set_figure(Figure(figsize=(3, 3)))
            return
        tr = tracks[ai]
        period = r.get("Period", "Whole test")
        if period and period != "Whole test":
            dur = tr.t[-1] + tr.dt if len(tr) else 0
            for label, a, b in time_periods(dur, p.analysis_for(test)):
                if label == period:
                    tr = tr.slice_time(a, b)
                    title += f" · {period}"
                    break
        self._detail = {"test": test, "track": tr, "app": p.get_apparatus(test.apparatus)}
        self.detail_lbl.setText(title)
        self._stale = {0, 1, 2}
        self._render_current()

    def _render_current(self):
        i = self.tabs.currentIndex()
        if self._detail is None or i not in self._stale:
            return
        self._stale.discard(i)
        d = self._detail
        tr, app, test = d["track"], d["app"], d["test"]
        try:
            if i == 0:
                frame = self._frame(test)
                self.track_canvas.set_figure(plots.track_plot(tr, app, frame=frame, size=(4, 4)))
            elif i == 1:
                self.heat_canvas.set_figure(plots.heatmap([tr], app, size=(4.4, 4)))
            elif i == 2:
                fr = None
                try:
                    fr = kinematics(tr, app, self.project.analysis_for(test)).freezing
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

        def work(progress, stop):
            data = []
            n = sum(len(v) for v in by_group.values())
            k = 0
            for g in order:
                trs = []
                for t in by_group[g]:
                    trs.extend(p.load_tracks(t)[:1])
                    k += 1
                    progress(k / n)
                data.append((g, trs, p.get_apparatus(by_group[g][0].apparatus)))
            return group_heatmap_figure(data)

        def done(fig):
            self.groups_canvas.set_figure(fig)
            self.tabs.setCurrentWidget(self.groups_canvas)

        return run_with_progress(self, "Group heatmaps", work, on_done=done)

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


def group_heatmap_figure(data: list[tuple[str, list, object]]) -> Figure:
    """Grid of average-occupancy heatmaps (one per group) on a common colour scale."""
    n = len(data)
    ncol = min(n, 3)
    nrow = math.ceil(n / ncol)
    fig = Figure(figsize=(3.3 * ncol + 0.6, 3.3 * nrow), dpi=100, layout="constrained")
    vmax = 0.0
    for _, trs, app in data:
        if trs:
            H, _ = plots.occupancy(trs, app)
            vmax = max(vmax, float(np.nanmax(H)) if H.size else 0.0)
    axes = []
    for i, (g, trs, app) in enumerate(data):
        ax = fig.add_subplot(nrow, ncol, i + 1)
        axes.append(ax)
        plots.heatmap(trs, app, title=f"{g} (n = {len(trs)})", ax=ax, vmax=vmax or None)
    if axes and axes[0].images:
        cb = fig.colorbar(axes[0].images[0], ax=axes, fraction=0.03, pad=0.02)
        cb.set_label("mean time (s)", fontsize=8)
        cb.ax.tick_params(labelsize=7)
    return fig
