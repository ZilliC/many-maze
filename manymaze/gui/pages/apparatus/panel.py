"""The property panel of the apparatus page: one tab per kind of map object (zones, points, lines, zone groups,
sequences) with the list of objects and the properties of the selected one, phrased as ANY-maze sentences.

The panel only shows the apparatus and turns the user's input into calls of the page's editing methods (which
save undo steps and refresh the panel)."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from ....core.apparatus import ENTRY_RULES, Apparatus, ZoneGroup
from ....core.geometry import Ellipse, Polygon
from ... import theme
from ...widgets import ColorButton, button_row, color_icon, hint, loading, separator

KINDS = ("zone", "point", "line", "group", "sequence")  # one tab each, in this order
MAP_KINDS = ("zone", "point", "line")  # objects drawn on the map (selected there too)
NO_COLOR = "#94a3b8"

SEQ_END = {"entry": "Complete on entering the last step", "exit": "Complete on leaving the last step"}

# ANY-maze style sentences for the zone and sequence options: attribute -> (text when False, text when True)
ZONE_SENTENCES = {
    "hidden": ("This is not a hidden zone", "This is a hidden zone"),
    "moveable": ("Zone position: the same in all tests", "Zone position: can differ in each test"),
}
SEQ_SENTENCES = {
    "from_start": ("It can begin at any step (rotations count)", "It must begin at the first step"),
    "allow_other": ("No other zones allowed between steps", "Other zones allowed between steps"),
    "bidirectional": ("Only in the order shown", "In either direction"),
    "overlap": ("Sequences cannot overlap", "Sequences may overlap"),
}
ENTRY_SENTENCES = {
    "": "Zone entry: as in analysis settings",
    "centre": "Zone entry: centre of the animal",
    "head": "Zone entry: the animal's head",
    "tail": "Zone entry: the animal's tail base",
    "body": "Zone entry: part of the body in it",
    "exclusion": "Zone entry: not in any other zone",
}

PANEL_QSS = f"""
QFrame#PropPanel {{ background: {theme.WORK_BG}; border: none; border-left: 1px solid {theme.BORDER}; }}
QTabWidget#PropTabs::pane {{ border: none; border-top: 1px solid {theme.BORDER}; background: {theme.WORK_BG}; }}
QTabWidget#PropTabs > QTabBar::tab {{ background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 5px 6px 4px 6px; margin: 0; color: {theme.TEXT}; }}
QTabWidget#PropTabs > QTabBar::tab:selected {{ color: {theme.ACCENT}; border-bottom: 2px solid {theme.ACCENT}; }}
QTabWidget#PropTabs > QTabBar::tab:hover:!selected {{ background: {theme.HOVER}; }}
QLabel#PropHeading {{ color: {theme.HEADING}; font-size: 18px; font-weight: 300; }}
QLabel#PropSubheading {{ color: {theme.HEADING}; font-size: 15px; font-weight: 300; padding-top: 4px; }}
QLabel#PropCaption {{ color: {theme.TEXT}; padding-top: 2px; }}
QListWidget {{ font-size: 13px; }}
QListWidget::item {{ padding: 2px 2px; }}
QScrollArea#PropScroll, QWidget#PropBody {{ background: {theme.WORK_BG}; }}
"""


def _is_axis_rect(shape) -> bool:
    if not isinstance(shape, Polygon) or len(shape.points) != 4:
        return False
    xs = sorted({round(p[0], 6) for p in shape.points})
    ys = sorted({round(p[1], 6) for p in shape.points})
    return len(xs) == 2 and len(ys) == 2


def describe_shape(shape, app: Apparatus | None) -> str:
    if shape is None:
        return "—"
    if isinstance(shape, Ellipse):
        kind = "Circle" if abs(shape.rx - shape.ry) < 1e-6 else "Ellipse"
    elif _is_axis_rect(shape):
        kind = "Rectangle"
    else:
        kind = f"Polygon ({len(shape.points)} vertices)"
    a = shape.area()
    if app is not None and app.px_per_cm:
        return f"{kind} · {a / app.px_per_cm ** 2:,.1f} cm²"
    return f"{kind} · {a:,.0f} px²"


def group_text(g: ZoneGroup) -> str:
    s = " + ".join(g.zones) or "(empty)"
    if g.exclude:
        s += " − " + " − ".join(g.exclude)
    return s


class SentenceChoice(QComboBox):
    """ANY-maze style yes/no option phrased as a sentence ("This is not a hidden zone ▾"); offers the QCheckBox
    API (isChecked / setChecked / toggled)."""

    toggled = Signal(bool)

    def __init__(self, sentences: tuple[str, str], tooltip: str = "", parent=None):
        super().__init__(parent)
        self.addItem(sentences[0], False)
        self.addItem(sentences[1], True)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        if tooltip:
            self.setToolTip(tooltip)
        self.currentIndexChanged.connect(lambda i: self.toggled.emit(i == 1))

    def isChecked(self) -> bool:
        return self.currentIndex() == 1

    def setChecked(self, on: bool):
        self.setCurrentIndex(1 if on else 0)


def _sentence_combo(items, tooltip: str = "") -> QComboBox:
    w = QComboBox()
    for k, lbl in items:
        w.addItem(lbl, k)
    w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    w.setMinimumContentsLength(12)
    if tooltip:
        w.setToolTip(tooltip)
    return w


def _label(text: str, name: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName(name)
    return lb


def _spin(spin, lo, hi, decimals=None, prefix="", suffix="", special="", tip=""):
    spin.setRange(lo, hi)
    if decimals is not None:
        spin.setDecimals(decimals)
    spin.setPrefix(prefix)
    spin.setSuffix(suffix)
    spin.setSpecialValueText(special)
    spin.setToolTip(tip)
    return spin


class NamedListSection:
    """One tab of the panel: a heading with the number of objects, their list, and the name (and colour) of the
    selected one. ``layout`` takes the kind-specific rows."""

    def __init__(self, kind: str, title: str, color_title: str | None = None):
        self.kind = kind
        self.widget = QScrollArea()
        self.widget.setObjectName("PropScroll")
        self.widget.setWidgetResizable(True)
        self.widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.widget.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("PropBody")
        self.layout = QVBoxLayout(body)
        self.layout.setContentsMargins(12, 8, 12, 10)
        self.layout.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_label(title, "PropHeading"))
        head.addStretch()
        self.count = hint(wrap=False)
        head.addWidget(self.count)
        self.layout.addLayout(head)
        self.widget.setWidget(body)
        self.list = QListWidget()
        self.name = QLineEdit()
        self.color = ColorButton(NO_COLOR, color_title) if color_title else None

    def name_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        lb = QLabel("Name")
        lb.setMinimumWidth(40)
        row.addWidget(lb)
        row.addWidget(self.name, 1)
        if self.color is not None:
            row.addWidget(self.color)
        return row

    @property
    def row(self) -> int:
        return self.list.currentRow()

    def fill(self, objects, tooltip=None):
        """List the objects (with their colour), keeping the current row when it still exists."""
        r = self.row
        self.list.clear()
        for m in objects:
            it = QListWidgetItem(color_icon(m.color), m.name) if self.color is not None else QListWidgetItem(m.name)
            if tooltip is not None:
                it.setToolTip(tooltip(m))
            self.list.addItem(it)
        return r

    def show(self, m):
        """Name and colour of the selected object (None: nothing selected)."""
        self.name.setText(m.name if m is not None else "")
        self.name.setEnabled(m is not None)
        if self.color is not None:
            self.color.set_color(m.color if m is not None else NO_COLOR)
            self.color.setEnabled(m is not None)

    def set_count(self, n: int | None):
        self.count.setText("" if n is None else f"{n} {self.kind}{'' if n == 1 else 's'}")


class PropertyPanel(QFrame):
    """Tabs with the zones, points, lines, zone groups and sequences of the page's current apparatus."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self._loading = False
        self.setObjectName("PropPanel")
        self.setFixedWidth(306)
        self.setStyleSheet(PANEL_QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 6, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("PropTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        lay.addWidget(self.tabs)
        self.zone = NamedListSection("zone", "Zones", "Zone colour")
        self.point = NamedListSection("point", "Points", "Point colour")
        self.line = NamedListSection("line", "Lines", "Line colour")
        self.group = NamedListSection("group", "Zone groups")
        self.sequence = NamedListSection("sequence", "Sequences")
        self.sections = {k: getattr(self, k) for k in KINDS}
        self._build_zones()
        self._build_points()
        self._build_lines()
        self._build_groups()
        self._build_sequences()
        for sec, title in zip(self.sections.values(), ("Zones", "Points", "Lines", "Groups", "Sequences")):
            self.tabs.addTab(sec.widget, title)
            sec.name.editingFinished.connect(lambda s=sec: self.page.rename(s.kind, s.row, s.name.text()))
            if sec.color is not None:
                sec.color.color_changed.connect(lambda c, s=sec: self.page.set_item_color(s.kind, s.row, c))
            sec.list.currentRowChanged.connect(lambda r, k=sec.kind: self._row_changed(k, r))
        self.tabs.currentChanged.connect(lambda _i: self.page.show_sequence_overlay())
        # long sentences must not widen the panel: let inputs shrink to the panel width
        for wdg in self.findChildren(QWidget):
            if isinstance(wdg, (QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit)):
                wdg.setMinimumWidth(60)

    # ---- building -----------------------------------------------------------------------
    def _set(self, kind: str, **values):
        """A property widget changed: edit the selected object (not while the panel is being filled)."""
        if not self._loading:
            self.page.set_property(kind, self.sections[kind].row, **values)

    def _build_zones(self):
        sec, page = self.zone, self.page
        zl = sec.layout
        sec.list.setMinimumHeight(110)
        zl.addWidget(sec.list, 1)
        zl.addLayout(sec.name_row())
        self.zone_info = hint("—")
        zl.addWidget(self.zone_info)
        self.zone_hidden = SentenceChoice(ZONE_SENTENCES["hidden"],
                                          "Hidden zone: the animal cannot be seen in it (nest box, tunnel). When it "
                                          "disappears in or near this zone it is counted as in the zone rather "
                                          "than lost.")
        self.zone_hidden.toggled.connect(lambda on: self._set("zone", hidden=bool(on)))
        self.zone_moveable = SentenceChoice(ZONE_SENTENCES["moveable"],
                                            "Position of the zone remains the same in all tests, or can be "
                                            "different in each test (e.g. a water-maze platform; set it per test "
                                            "in the test view)")
        self.zone_moveable.toggled.connect(lambda on: self._set("zone", moveable=bool(on)))
        self.zone_rule = _sentence_combo([(k, ENTRY_SENTENCES.get(k, lbl)) for k, lbl in ENTRY_RULES.items()],
                                         "When is the animal in this zone: by its centre, head or tail base, when "
                                         "a proportion of its body is inside, or when it is in no other zone")
        self.zone_rule.currentIndexChanged.connect(lambda _: self._set("zone", entry_rule=self.zone_rule.currentData()))
        self.zone_frac = _spin(QSpinBox(), 1, 100, prefix="At least ", suffix=" % of the body in the zone")
        self.zone_frac.valueChanged.connect(lambda v: self._set("zone", body_fraction=v / 100.0))
        self.zone_inv = _spin(QDoubleSpinBox(), 0, 10000, 1, "Investigation zone: head within ",
                              special="This is not an investigation zone",
                              tip="Investigation zone: the animal is in the zone while its head is within this "
                                  "distance of it (e.g. sniffing an object)")
        self.zone_inv.valueChanged.connect(lambda v: self._set("zone", investigation_distance_cm=float(v)))
        self.zone_orient = _spin(QSpinBox(), 0, 180, prefix="Entry only when facing it: within ", suffix="°",
                                 special="Entry does not need facing the zone",
                                 tip="An entry only counts once the animal is oriented towards the zone: its body "
                                     "orientation within this angle of the direction to the zone centre (0 = off)")
        self.zone_orient.valueChanged.connect(lambda v: self._set("zone", entry_orientation_deg=float(v)))
        self.zone_whishaw = _spin(QDoubleSpinBox(), 0, 10000, 1, "Whishaw's corridor: ",
                                  special="No Whishaw's corridor",
                                  tip="Width of the zone's Whishaw's corridor, a band from the animal's start "
                                      "position to the zone centre (time and distance in the corridor; 0 = none)")
        self.zone_whishaw.valueChanged.connect(lambda v: self._set("zone", whishaw_width_cm=float(v)))
        for wdg in (self.zone_hidden, self.zone_moveable, self.zone_rule, self.zone_frac, self.zone_inv,
                    self.zone_orient, self.zone_whishaw):
            zl.addWidget(wdg)
        self.btn_zone_dup = QPushButton("Duplicate")
        self.btn_zone_dup.clicked.connect(lambda: page.duplicate_zone(sec.row))
        self.btn_zone_arena = QPushButton("Use as arena")
        self.btn_zone_arena.setToolTip("Copy this zone's outline to the arena boundary")
        self.btn_zone_arena.clicked.connect(lambda: page.zone_to_arena(sec.row))
        self.btn_zone_del = QPushButton("Delete")
        self.btn_zone_del.clicked.connect(lambda: page.delete_item("zone", sec.row))
        zl.addLayout(button_row(self.btn_zone_dup, self.btn_zone_arena, self.btn_zone_del))
        self.btn_grid_del = QPushButton("Delete the whole grid")
        self.btn_grid_del.setToolTip("Delete the whole grid this zone belongs to")
        self.btn_grid_del.clicked.connect(lambda: page.delete_grid_of(sec.row))
        zl.addLayout(button_row(self.btn_grid_del))
        zl.addSpacing(4)
        zl.addWidget(separator())
        zl.addWidget(_label("Arena boundary", "PropSubheading"))
        self.arena_info = QLabel()
        self.arena_info.setWordWrap(True)
        zl.addWidget(self.arena_info)
        self.btn_arena_sel = QPushButton("Select")
        self.btn_arena_sel.setToolTip("Select the arena boundary on the map")
        self.btn_arena_sel.clicked.connect(lambda: page.select_item("arena", 0))
        self.btn_arena_clear = QPushButton("Remove")
        self.btn_arena_clear.clicked.connect(lambda: page.delete_item("arena", 0))
        zl.addLayout(button_row(self.btn_arena_sel, self.btn_arena_clear))

    def _build_points(self):
        sec = self.point
        pl = sec.layout
        sec.list.setMinimumHeight(110)
        pl.addWidget(sec.list, 1)
        pl.addLayout(sec.name_row())
        self.point_radius = _spin(QDoubleSpinBox(), 0, 1000, 1, "Near the point: within ", " cm",
                                  tip="Distance counted as 'near' the point (object exploration, platform "
                                      "proximity)")
        self.point_radius.valueChanged.connect(lambda v: self._set("point", radius_cm=float(v)))
        pl.addWidget(self.point_radius)
        self.point_x, self.point_y = QDoubleSpinBox(), QDoubleSpinBox()
        xy = QHBoxLayout()
        xy.setSpacing(6)
        xy.addWidget(QLabel("Position"))
        for s, pre in ((self.point_x, "x "), (self.point_y, "y ")):
            _spin(s, -100000, 100000, 1, pre, " px")
            s.valueChanged.connect(lambda _v: self._set("point", x=float(self.point_x.value()),
                                                        y=float(self.point_y.value())))
            xy.addWidget(s, 1)
        pl.addLayout(xy)
        self.btn_point_del = QPushButton("Delete point")
        self.btn_point_del.clicked.connect(lambda: self.page.delete_item("point", sec.row))
        pl.addLayout(button_row(self.btn_point_del))
        pl.addStretch()

    def _build_lines(self):
        sec = self.line
        ll = sec.layout
        sec.list.setMinimumHeight(110)
        ll.addWidget(sec.list, 1)
        ll.addLayout(sec.name_row())
        self.line_info = hint("—", wrap=False)
        ll.addWidget(self.line_info)
        self.btn_line_del = QPushButton("Delete line")
        self.btn_line_del.clicked.connect(lambda: self.page.delete_item("line", sec.row))
        ll.addLayout(button_row(self.btn_line_del))
        ll.addStretch()

    def _build_groups(self):
        sec = self.group
        gl = sec.layout
        sec.list.setMaximumHeight(120)
        gl.addWidget(sec.list)
        self.btn_group_add = QPushButton("Add group")
        self.btn_group_add.clicked.connect(lambda: self.page.add_group())
        self.btn_group_del = QPushButton("Delete")
        self.btn_group_del.clicked.connect(lambda: self.page.delete_item("group", sec.row))
        gl.addLayout(button_row(self.btn_group_add, self.btn_group_del))
        gl.addLayout(sec.name_row())
        gl.addWidget(_label("The group is made of these zones", "PropCaption"))
        self.group_inc = QListWidget()
        gl.addWidget(self.group_inc, 1)
        gl.addWidget(_label("…minus these zones", "PropCaption"))
        self.group_exc = QListWidget()
        gl.addWidget(self.group_exc, 1)
        for lst in (self.group_inc, self.group_exc):
            lst.setMinimumHeight(90)
            lst.itemChanged.connect(self._group_members_changed)

    def _build_sequences(self):
        sec, page = self.sequence, self.page
        sl = sec.layout
        sec.list.setMaximumHeight(96)
        sl.addWidget(sec.list)
        self.btn_seq_add = QPushButton("Add sequence")
        self.btn_seq_add.clicked.connect(lambda: page.add_sequence())
        self.btn_seq_del = QPushButton("Delete")
        self.btn_seq_del.clicked.connect(lambda: page.delete_item("sequence", sec.row))
        sl.addLayout(button_row(self.btn_seq_add, self.btn_seq_del))
        sl.addLayout(sec.name_row())
        sl.addWidget(_label("The animal visits these zones in order", "PropCaption"))
        self.seq_steps = QListWidget()
        self.seq_steps.setMinimumHeight(96)
        sl.addWidget(self.seq_steps, 1)
        self.seq_zone = QComboBox()
        self.seq_zone.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.seq_zone.setMinimumContentsLength(8)
        self.btn_step_add = QPushButton("Add step")
        self.btn_step_add.clicked.connect(lambda: page.add_sequence_step(sec.row, self.seq_zone.currentText()))
        st = QHBoxLayout()
        st.setSpacing(6)
        st.addWidget(self.seq_zone, 1)
        st.addWidget(self.btn_step_add)
        sl.addLayout(st)
        self.btn_step_up = QPushButton("Up")
        self.btn_step_up.clicked.connect(lambda: page.move_sequence_step(sec.row, self.seq_steps.currentRow(), -1))
        self.btn_step_down = QPushButton("Down")
        self.btn_step_down.clicked.connect(lambda: page.move_sequence_step(sec.row, self.seq_steps.currentRow(), 1))
        self.btn_step_del = QPushButton("Remove")
        self.btn_step_del.clicked.connect(lambda: page.remove_sequence_step(sec.row, self.seq_steps.currentRow()))
        sl.addLayout(button_row(self.btn_step_up, self.btn_step_down, self.btn_step_del))
        sl.addSpacing(2)
        self.seq_from_start = SentenceChoice(SEQ_SENTENCES["from_start"],
                                             "Can begin at any step: any rotation of the steps counts (e.g. ABC, "
                                             "BCA, CAB)")
        self.seq_allow_other = SentenceChoice(SEQ_SENTENCES["allow_other"])
        self.seq_bidir = SentenceChoice(SEQ_SENTENCES["bidirectional"],
                                        "Either direction: the reversed order also counts (e.g. CBA)")
        self.seq_overlap = SentenceChoice(SEQ_SENTENCES["overlap"], "Overlapping (sliding window): A B C A "
                                                                    "contains A B C and B C A")
        for wdg, attr in ((self.seq_from_start, "from_start"), (self.seq_allow_other, "allow_other"),
                          (self.seq_bidir, "bidirectional"), (self.seq_overlap, "overlap")):
            wdg.toggled.connect(lambda on, a=attr: self._set("sequence", **{a: bool(on)}))
            sl.addWidget(wdg)
        self.seq_end = _sentence_combo(SEQ_END.items())
        self.seq_end.currentIndexChanged.connect(lambda _: self._set("sequence", end=self.seq_end.currentData()))
        sl.addWidget(self.seq_end)
        self.seq_max = _spin(QDoubleSpinBox(), 0, 1e6, 1, "Must be completed within ", " s", "No time limit")
        self.seq_max.valueChanged.connect(lambda v: self._set("sequence", max_duration_s=float(v)))
        sl.addWidget(self.seq_max)

    # ---- state ----------------------------------------------------------------------------
    @property
    def app(self) -> Apparatus | None:
        return self.page.app

    def selected(self, kind: str):
        """The object selected in a tab's list, or None."""
        items = getattr(self.app, kind + "s") if self.app is not None else []
        r = self.sections[kind].row
        return items[r] if 0 <= r < len(items) else None

    def current_kind(self) -> str:
        return KINDS[self.tabs.currentIndex()]

    def show_kind(self, kind: str):
        self.tabs.setCurrentIndex(KINDS.index(kind))

    def select(self, kind: str, row: int):
        """Show a tab with one of its objects selected."""
        self.show_kind(kind)
        self.sections[kind].list.setCurrentRow(row)

    def shown_sequence(self):
        """The selected sequence while the Sequences tab is shown."""
        return self.selected("sequence") if self.current_kind() == "sequence" else None

    def commit(self):
        """Flush half-edited names (e.g. when saving while a name field has focus)."""
        for sec in self.sections.values():
            it = sec.list.item(sec.row) if sec.row >= 0 else None
            text = sec.name.text().strip()
            if it is not None and text and text != it.text():
                self.page.rename(sec.kind, sec.row, text)

    # ---- showing the apparatus ----------------------------------------------------------------
    def refresh(self):
        """Refill the lists from the apparatus (the map selection selects in the lists)."""
        app = self.app
        with loading(self):
            for kind in MAP_KINDS:
                self.sections[kind].fill(getattr(app, kind + "s") if app else [])
                self.sections[kind].list.setCurrentRow(-1)
            for kind, tip in (("group", group_text), ("sequence", lambda q: " → ".join(q.steps) or "(no steps)")):
                items = getattr(app, kind + "s") if app else []
                r = self.sections[kind].fill(items, tip)
                if items:
                    self.sections[kind].list.setCurrentRow(min(max(r, 0), len(items) - 1))
            self.seq_zone.clear()
            self.seq_zone.addItems(app.names() if app else [])
        self.sync_with_map(self.page.view.selected_key())
        self.load_group_editor()
        self.load_seq_editor()
        self.update_counts()

    def update_counts(self):
        for kind, sec in self.sections.items():
            sec.set_count(len(getattr(self.app, kind + "s")) if self.app is not None else None)

    def show_arena(self, app: Apparatus | None):
        self.arena_info.setText("" if app is None else describe_shape(app.arena, app) if app.arena is not None else
                                "Not set — draw it with the Arena tool. Without an arena the tracker searches "
                                "the whole frame.")
        has = app is not None and app.arena is not None
        self.btn_arena_sel.setEnabled(has)
        self.btn_arena_clear.setEnabled(has)

    def sync_with_map(self, key: tuple[str, int] | None):
        """Select in the lists (and show the tab of) the object selected on the map."""
        with loading(self):
            for kind in MAP_KINDS:
                lst = self.sections[kind].list
                if key is not None and key[0] == kind:
                    lst.setCurrentRow(key[1])
                    lst.scrollToItem(lst.item(key[1]), QAbstractItemView.EnsureVisible)
                else:
                    lst.setCurrentRow(-1)
                    lst.clearSelection()
        if key is not None:
            self.show_kind(key[0] if key[0] in MAP_KINDS else "zone")
        self.load_editor()

    def _row_changed(self, kind: str, row: int):
        if self._loading:
            return
        if kind in MAP_KINDS:
            if row >= 0:
                self.page.select_item(kind, row)
            self.load_editor()
        elif kind == "group":
            self.load_group_editor()
        else:
            self.load_seq_editor()
            self.page.show_sequence_overlay()

    def load_editor(self):
        """Properties of the selected zone, point and line."""
        app = self.app
        with loading(self):
            z = self.selected("zone")
            self.zone.show(z)
            for w in (self.btn_zone_dup, self.btn_zone_arena, self.btn_zone_del, self.zone_rule, self.zone_inv,
                      self.zone_hidden, self.zone_moveable, self.zone_orient, self.zone_whishaw):
                w.setEnabled(z is not None)
            self.zone_info.setText(describe_shape(z.shape, app) if z else "—")
            self.zone_rule.setCurrentIndex(max(0, self.zone_rule.findData(z.entry_rule if z else "")))
            self.zone_frac.setValue(int(round((z.body_fraction if z else 0.8) * 100)))
            body = z is not None and z.entry_rule == "body"
            self.zone_frac.setEnabled(body)
            self.zone_frac.setVisible(body)
            self.zone_inv.setValue(z.investigation_distance_cm if z else 0.0)
            self.zone_inv.setSuffix(f" {app.unit}" if app is not None else " cm")
            self.zone_orient.setValue(int(round(z.entry_orientation_deg)) if z else 0)
            self.zone_whishaw.setValue(z.whishaw_width_cm if z else 0.0)
            self.zone_whishaw.setSuffix(f" wide ({app.unit})" if app is not None else " wide (cm)")
            self.zone_hidden.setChecked(bool(z and z.hidden))
            self.zone_moveable.setChecked(bool(z and z.moveable))
            self.btn_grid_del.setVisible(z is not None and any(z.name in g.zones for g in app.grids))

            p = self.selected("point")
            self.point.show(p)
            self.point_radius.setValue(p.radius_cm if p else 0)
            self.point_x.setValue(p.x if p else 0)
            self.point_y.setValue(p.y if p else 0)
            for w in (self.point_radius, self.point_x, self.point_y, self.btn_point_del):
                w.setEnabled(p is not None)

            ln = self.selected("line")
            self.line.show(ln)
            if ln:
                L = math.hypot(ln.x2 - ln.x1, ln.y2 - ln.y1)
                self.line_info.setText(f"{L / app.px_per_cm:.1f} cm" if app.px_per_cm else f"{L:.0f} px")
            else:
                self.line_info.setText("—")
            self.btn_line_del.setEnabled(ln is not None)

    def load_group_editor(self):
        app = self.app
        g = self.selected("group")
        with loading(self):
            self.group.show(g)
            for w in (self.group_inc, self.group_exc, self.btn_group_del):
                w.setEnabled(g is not None)
            for lst, members in ((self.group_inc, g.zones if g else []), (self.group_exc, g.exclude if g else [])):
                lst.clear()
                for z in (app.zones if app is not None else []):
                    it = QListWidgetItem(color_icon(z.color), z.name)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Checked if z.name in members else Qt.Unchecked)
                    lst.addItem(it)
            if g is not None:
                self.group.list.item(self.group.row).setToolTip(group_text(g))

    def _group_members_changed(self, _item):
        if self._loading:
            return

        def ticked(lst):
            return [lst.item(i).text() for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
        self.page.set_group_members(self.group.row, ticked(self.group_inc), ticked(self.group_exc))

    def load_seq_editor(self, step: int | None = None):
        """The selected sequence; `step` selects one of its steps (default: keep the current one)."""
        app = self.app
        q = self.selected("sequence")
        with loading(self):
            self.sequence.show(q)
            cur = self.seq_steps.currentRow() if step is None else step
            self.seq_steps.clear()
            for i, st in enumerate(q.steps if q else []):
                z = app.zone(st)
                self.seq_steps.addItem(QListWidgetItem(color_icon(z.color if z else NO_COLOR), f"{i + 1}. {st}"))
            if q and q.steps:
                self.seq_steps.setCurrentRow(min(max(cur, 0), len(q.steps) - 1))
            if q is not None:
                self.sequence.list.item(self.sequence.row).setToolTip(" → ".join(q.steps) or "(no steps)")
            self.seq_from_start.setChecked(q.from_start if q else True)
            self.seq_allow_other.setChecked(q.allow_other if q else True)
            self.seq_bidir.setChecked(q.bidirectional if q else False)
            self.seq_overlap.setChecked(q.overlap if q else False)
            self.seq_end.setCurrentIndex(max(0, self.seq_end.findData(q.end if q else "entry")))
            self.seq_max.setValue(q.max_duration_s if q else 0.0)
            for w in (self.seq_steps, self.seq_zone, self.btn_step_add, self.btn_step_up, self.btn_step_down,
                      self.btn_step_del, self.seq_from_start, self.seq_allow_other, self.seq_bidir,
                      self.seq_overlap, self.seq_end, self.seq_max, self.btn_seq_del):
                w.setEnabled(q is not None)
