"""Dialogs of the Data page: HTML report options and video export overlays; the "Select data" tree of measures
(also used to insert a measure into a calculation's formula)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ....core.calculations import CATEGORY as CALCULATIONS
from ....core.videoexport import OverlayOptions
from .table import CATEGORY_ORDER, measure_category


def measure_groups(columns, names: dict, info=()) -> dict[str, dict[str, list[str]]]:
    """{category: {sub-category: [columns]}} as the Select data tree shows them: the information columns, then
    the measures by measure_category()."""
    cats: dict[str, dict[str, list[str]]] = {}
    if info:
        cats["Information"] = {"": list(info)}
    for c in columns:
        cat, sub = measure_category(c, names)
        cats.setdefault(cat, {}).setdefault(sub, []).append(c)
    return cats


def fill_measure_tree(tree: QTreeWidget, cats: dict, hidden: set | None = None):
    """Fill a Select data tree: categories (in CATEGORY_ORDER) > sub-categories > columns (Qt.UserRole holds the
    column, shown without its sub-category prefix). hidden: tick boxes, unticked for these columns (None: no tick
    boxes, to pick one measure)."""
    tree.clear()
    tick = hidden is not None
    expanded = len(cats) <= 2
    for cat in CATEGORY_ORDER:
        if cat not in cats:
            continue
        top = QTreeWidgetItem([cat])
        if tick:
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        tree.addTopLevelItem(top)
        for sub, cols in cats[cat].items():
            parent = top
            if sub:
                parent = QTreeWidgetItem(top, [sub])
                if tick:
                    parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            for c in cols:
                label = c.split(": ", 1)[1] if sub and c.startswith(sub + ": ") else c
                it = QTreeWidgetItem(parent, [label])
                it.setData(0, Qt.UserRole, c)
                it.setToolTip(0, c)
                if tick:
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Unchecked if c in hidden else Qt.Checked)
        top.setExpanded(expanded or cat in ("General", CALCULATIONS))
        if cat == "Information":
            top.setToolTip(0, "Identification columns (empty columns are hidden automatically)")


def filter_measure_tree(tree: QTreeWidget, text: str):
    """Show only the columns containing `text` (and their categories, expanded)."""
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

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))


class MeasurePickerDialog(QDialog):
    """Choose one results column from the Select data tree (double-click or OK); ``selected()`` is the column."""

    def __init__(self, cats: dict, parent=None, title: str = "Insert measure"):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search measures…")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        fill_measure_tree(self.tree, cats)
        self.search.textChanged.connect(lambda t: filter_measure_tree(self.tree, t))
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.accept() if it.data(0, Qt.UserRole) else None)
        lay.addWidget(self.tree, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Insert")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.tree.currentItemChanged.connect(self._update)
        self._update()
        self.resize(420, 520)

    def _update(self, *_):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.selected() is not None)

    def select(self, column: str) -> bool:
        """Make a column the current item (False if the tree has no such column)."""
        def walk(item):
            if item.data(0, Qt.UserRole) == column:
                return item
            for i in range(item.childCount()):
                hit = walk(item.child(i))
                if hit is not None:
                    return hit
            return None

        for i in range(self.tree.topLevelItemCount()):
            hit = walk(self.tree.topLevelItem(i))
            if hit is not None:
                self.tree.setCurrentItem(hit)
                return True
        return False

    def selected(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it is not None else None


def _combo(items, data=None, tip: str = "") -> QComboBox:
    c = QComboBox()
    for i, it in enumerate(items):
        c.addItem(it, data[i] if data else it)
    if tip:
        c.setToolTip(tip)
    return c


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
        super().__init__(parent)
        self.setWindowTitle("Export video with overlays")
        o = OverlayOptions()
        f = QFormLayout(self)
        self.zones = QCheckBox("Draw the zones, points and lines")
        self.zones.setChecked(o.zones)
        self.trail = _combo(["- No track -", "2 seconds", "5 seconds", "15 seconds", "Whole test so far"],
                            [0, 2, 5, 15, -1])
        self.trail.setCurrentIndex(2)
        self.trail_color = _combo(["Speed", "Time", "Animal colour"], ["speed", "time", "fixed"])
        self.body = QCheckBox("Mark the centre, head and tail")
        self.body.setChecked(True)
        self.beh = QCheckBox("Show scored behaviours and freezing")
        self.beh.setChecked(True)
        self.stamp = QCheckBox("Add a time stamp and the test caption")
        self.stamp.setChecked(True)
        self.speed = _combo(["0.5×", "1×", "2×", "4×", "8×"], [0.5, 1.0, 2.0, 4.0, 8.0])
        self.speed.setCurrentIndex(1)
        self.scale = _combo(["100 %", "75 %", "50 %"], [1.0, 0.75, 0.5])
        for w in (self.zones, self.body, self.beh, self.stamp):
            f.addRow(w)
        f.addRow("Draw the track of the last", self.trail)
        f.addRow("Colour the track by", self.trail_color)
        f.addRow("Playback speed of the video", self.speed)
        f.addRow("Size of the video", self.scale)
        f.setVerticalSpacing(10)
        f.setHorizontalSpacing(16)
        self.form = f
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        f.addRow(self.buttons)

    def embed(self):
        """Use the options as a panel inside a page (no buttons, no window)."""
        self.setWindowFlags(Qt.Widget)
        self.buttons.hide()
        self.setSizeGripEnabled(False)
        return self

    def options(self) -> OverlayOptions:
        return OverlayOptions(zones=self.zones.isChecked(), trail_s=float(self.trail.currentData()),
                              trail_color=self.trail_color.currentData(), body_points=self.body.isChecked(),
                              behaviours=self.beh.isChecked(), freezing=self.beh.isChecked(),
                              timestamp=self.stamp.isChecked(), info=self.stamp.isChecked(),
                              speed=float(self.speed.currentData()), scale=float(self.scale.currentData()))
