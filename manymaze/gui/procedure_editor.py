"""Procedure editor: a block/tree editor for Project.procedures.

``ProcedureEditor`` edits ``project.procedures`` in place: procedures on the left (tick to enable), their
statements as a tree in the middle (drag & drop to move or nest, toolbar to add / indent / outdent / move),
the selected statement's parameters on the right, and the validation messages below. Signal ``changed``.
The tree edits themselves are core.procedures.edit operations; the I/O devices dialog is in io_devices_dialog.
"""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStyle, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import iodevices as iod
from ..core import procedures as pr
from ..core.apparatus import unique_name
from ..core.procedures import edit as pe
from .icons import icon as named_icon
from .io_devices_dialog import IODevicesDialog  # noqa: F401  (re-exported)
from .statement_tree import ELSE, TYPE_ROLE, StatementTree, block_parts  # noqa: F401  (re-exported)
from .widgets import loading, value_text

ROLE = Qt.UserRole
ADD_TYPES = ["when", "wait", "if", "repeat", "set", "do", "stop", "comment", "var"]
WAIT_MODES = {"seconds": "Fixed time", "until": "Until a condition", "event": "For an event"}
REPEAT_MODES = {"count": "A number of times", "while": "While a condition is true", "forever": "Forever"}
SPEC_EXAMPLES = ["CRF", "FR 5", "VR 5", "FI 30", "VI 30", "PR", "PR 2", "FT 60", "VT 60", "EXT"]


def _num_or_expr(text: str, integer: bool = False):
    s = text.strip()
    if not s:
        return ""
    try:
        v = float(s)
    except ValueError:
        return s
    if integer or (v.is_integer() and "." not in s and "e" not in s.lower()):
        return int(v)
    return v


def _combo() -> QComboBox:
    cb = QComboBox()
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(10)
    return cb


def _wrapped(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl


class ProcedureEditor(QWidget):
    changed = Signal()

    def __init__(self, project=None, parent=None):
        super().__init__(parent)
        self.project = None
        self.procs: list = []
        self.issues: list = []
        self._loading = False
        self._read_only = False
        self._form_widgets: dict = {}
        self._issue_lbl = None
        self._build()
        if project is not None:
            self.set_project(project)
        else:
            self._refresh_all()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # procedures
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Procedures</b>"))
        self.proc_list = QListWidget()
        self.proc_list.setStyleSheet("QListWidget::item{padding:4px 2px;}")
        self.proc_list.setToolTip("All ticked procedures run at the same time during a live test. "
                                  "Double-click to rename.")
        self.proc_list.currentRowChanged.connect(lambda *_: self._proc_selected())
        self.proc_list.itemChanged.connect(self._proc_item_changed)
        lv.addWidget(self.proc_list, 1)
        pb = QHBoxLayout()
        self.add_proc_btn = QToolButton()
        self.add_proc_btn.setText("Add")
        self.add_proc_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.add_proc_btn.clicked.connect(lambda: self.add_procedure())
        m = QMenu(self.add_proc_btn)
        m.addAction("Empty procedure", lambda: self.add_procedure())
        ex = m.addMenu("From an example")
        for name in pr.EXAMPLES:
            ex.addAction(name, lambda n=name: self.add_procedure(copy.deepcopy(pr.EXAMPLES[n])))
        self.add_proc_btn.setMenu(m)
        self.proc_btns = {}
        pb.addWidget(self.add_proc_btn)
        for key, text, tip, fn in (("dup", "Duplicate", "Duplicate the procedure", self.duplicate_procedure),
                                   ("del", "Remove", "Remove the procedure", self.remove_procedure),
                                   ("up", "▲", "Move up", lambda: self.move_procedure(-1)),
                                   ("down", "▼", "Move down", lambda: self.move_procedure(1))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            pb.addWidget(b)
            self.proc_btns[key] = b
        pb.addStretch()
        lv.addLayout(pb)
        split.addWidget(left)

        # statements tree
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        tb = QHBoxLayout()
        self.add_btn = QToolButton()
        self.add_btn.setText("Add ▾")
        self.add_btn.setToolTip("Add a statement after the selected one")
        self.add_btn.setPopupMode(QToolButton.InstantPopup)
        self.add_btn.setMenu(self._type_menu(lambda t: self.add_statement(t, inside=False)))
        self.add_in_btn = QToolButton()
        self.add_in_btn.setText("Add inside ▾")
        self.add_in_btn.setToolTip("Add a statement inside the selected When / If / Else / Repeat block")
        self.add_in_btn.setPopupMode(QToolButton.InstantPopup)
        self.add_in_btn.setMenu(self._type_menu(lambda t: self.add_statement(t, inside=True), nested=True))
        tb.addWidget(self.add_btn)
        tb.addWidget(self.add_in_btn)
        self.st_btns = {}
        for key, text, tip, fn in (
                ("del", "Remove", "Remove the statement (Delete)", self.remove_statement),
                ("dup", "Copy", "Duplicate the statement", self.duplicate_statement),
                ("up", "▲", "Move up", lambda: self.move_statement(-1)),
                ("down", "▼", "Move down", lambda: self.move_statement(1)),
                ("indent", "→", "Indent: move into the block above", self.indent_statement),
                ("outdent", "←", "Outdent: move out of its block", self.outdent_statement),
                ("toggle", "On/off", "Enable / disable the statement", self.toggle_statement),
                ("json", "JSON", "Edit the procedure as JSON", self.edit_json)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.addWidget(b)
            self.st_btns[key] = b
        tb.addStretch()
        mv.addLayout(tb)
        self.tree = StatementTree()
        self.tree.currentItemChanged.connect(lambda *_: self._statement_selected())
        self.tree.dropped.connect(lambda: QTimer.singleShot(0, self._tree_dropped))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        mv.addWidget(self.tree, 1)
        self.issue_list = QListWidget()
        self.issue_list.setMaximumHeight(110)
        self.issue_list.itemClicked.connect(self._issue_clicked)
        self.issue_list.setToolTip("Problems found while checking the procedures; click one to select it")
        mv.addWidget(self.issue_list)
        split.addWidget(mid)

        # side form
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.form_box = QGroupBox("Statement")
        self.form = QFormLayout(self.form_box)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidget(self.form_box)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        rv.addWidget(scroll, 1)
        hb = QHBoxLayout()
        self.help_btn = QPushButton("Functions…")
        self.help_btn.setToolTip("Functions and operators available in expressions")
        self.help_btn.clicked.connect(self.show_functions)
        hb.addStretch()
        hb.addWidget(self.help_btn)
        rv.addLayout(hb)
        split.addWidget(right)
        split.setSizes([170, 520, 360])
        split.setStretchFactor(1, 1)

    def _type_menu(self, cb, nested=False) -> QMenu:
        m = QMenu(self)
        for t in ADD_TYPES:
            a = m.addAction(pr.STATEMENT_TYPES[t], lambda t=t: cb(t))
            if nested and t in ("when", "var"):
                a.setEnabled(False)
        return m

    # ------------------------------------------------------------------ data binding
    def set_project(self, project):
        self.project = project
        if project is None:
            self.set_procedures([])
            return
        if any(pr.is_legacy_rule(p) or not isinstance(p, dict) for p in project.procedures):
            project.procedures[:] = pr.normalize_procedures(project.procedures)
        self.set_procedures(project.procedures)

    def set_procedures(self, procs: list):
        """Edit this list in place (normally project.procedures)."""
        self.procs = procs
        self._refresh_all()

    def procedures(self) -> list:
        return self.procs

    def set_read_only(self, ro: bool):
        self._read_only = ro
        self._refresh_all()

    def context(self) -> dict:
        """What the procedures may refer to (zones, devices, touch-screen areas): see procedures.validate."""
        return pr.project_context(self.project) if self.project is not None else {}

    def _emit(self):
        self.validate()
        if not self._loading:
            self.changed.emit()

    def _cur_proc_index(self) -> int:
        i = self.proc_list.currentRow()
        return i if 0 <= i < len(self.procs) else -1

    def _cur_proc(self) -> dict | None:
        i = self._cur_proc_index()
        return self.procs[i] if i >= 0 else None

    # ------------------------------------------------------------------ procedures list
    def _refresh_all(self, select: int | None = None):
        cur = self.proc_list.currentRow() if select is None else select
        flags = Qt.ItemIsUserCheckable | Qt.ItemIsEditable
        with loading(self):
            self.proc_list.clear()
            for p in self.procs:
                it = QListWidgetItem(named_icon("procedure"), str(p.get("name", "Procedure")))
                it.setCheckState(Qt.Checked if p.get("enabled", True) else Qt.Unchecked)
                it.setFlags(it.flags() & ~flags if self._read_only else it.flags() | flags)
                self.proc_list.addItem(it)
        if self.procs:
            self.proc_list.setCurrentRow(min(max(cur, 0), len(self.procs) - 1))
        self._proc_selected()

    def _proc_item_changed(self, it: QListWidgetItem):
        i = self.proc_list.row(it)
        if self._loading or self._read_only or not 0 <= i < len(self.procs):
            return
        p = self.procs[i]
        name, on = it.text().strip() or p.get("name", "Procedure"), it.checkState() == Qt.Checked
        if name != p.get("name"):
            name = unique_name(name, [q.get("name") for q in self.procs if q is not p])
        if name != it.text():
            with loading(self):
                it.setText(name)
        if name != p.get("name") or on != p.get("enabled", True):
            p["name"], p["enabled"] = name, on
            self._emit()

    def _proc_selected(self):
        self._populate_tree()
        self._update_buttons()
        self.validate()

    def add_procedure(self, proc: dict | None = None):
        if self._read_only:
            return
        proc = proc or pr.new_procedure("Procedure")
        proc["name"] = self._unique_name(proc.get("name", "Procedure"))
        self.procs.append(proc)
        self._refresh_all(select=len(self.procs) - 1)
        self._emit()

    def _unique_name(self, base):
        return unique_name(base, [p.get("name") for p in self.procs])

    def duplicate_procedure(self):
        p = self._cur_proc()
        if p is None or self._read_only:
            return
        q = copy.deepcopy(p)
        q["name"] = self._unique_name(p.get("name", "Procedure") + " copy")
        self.procs.insert(self._cur_proc_index() + 1, q)
        self._refresh_all(select=self._cur_proc_index() + 1)
        self._emit()

    def remove_procedure(self):
        i = self._cur_proc_index()
        if i < 0 or self._read_only:
            return
        del self.procs[i]
        self._refresh_all(select=min(i, len(self.procs) - 1))
        self._emit()

    def move_procedure(self, d):
        i = self._cur_proc_index()
        j = i + d
        if i < 0 or not 0 <= j < len(self.procs) or self._read_only:
            return
        self.procs[i], self.procs[j] = self.procs[j], self.procs[i]
        self._refresh_all(select=j)
        self._emit()

    # ------------------------------------------------------------------ tree
    def _populate_tree(self, select_path: tuple | None = None):
        """Show the current procedure's statements and select one (by default the first)."""
        p = self._cur_proc()
        with loading(self):
            self.tree.show_statements(p.get("statements") or [] if p is not None else [])
        item = self.tree.item_for(select_path) if select_path is not None else None
        if item is None and select_path is None and self.tree.topLevelItemCount():
            item = self.tree.topLevelItem(0)
        if item is not None:
            self.tree.setCurrentItem(item)
        self._statement_selected()
        self._mark_issues()

    def _cur_path(self) -> tuple | None:
        return self.tree.path(self.tree.currentItem())

    def _statement(self, path) -> dict | None:
        p = self._cur_proc()
        return pr.statement_at(p, path) if p is not None and path else None

    def _apply(self, op, *args) -> tuple | None:
        """Apply a core.procedures.edit operation to the selected statement; select the statement it returns."""
        path, p = self._cur_path(), self._cur_proc()
        if not path or p is None or self._read_only:
            return None
        new = op(p, path, *args)
        if new is not None:
            self._populate_tree(new)
            self._emit()
        return new

    def add_statement(self, type_: str, inside: bool = False) -> tuple | None:
        """Add a statement after the selected one, or inside it (a When / If / Else / Repeat block)."""
        if self._read_only:
            return None
        if self._cur_proc() is None:
            self.add_procedure()
        new = pe.add(self._cur_proc(), self._cur_path(), pr.new_statement(type_), inside)
        if new is not None:
            self._populate_tree(new)
            self._emit()
        return new

    def remove_statement(self):
        self._apply(pe.remove)

    def duplicate_statement(self):
        self._apply(pe.duplicate)

    def move_statement(self, d: int):
        self._apply(pe.move, d)

    def indent_statement(self):
        """Move the statement into the container just above it (as its last child)."""
        self._apply(pe.indent)

    def outdent_statement(self):
        """Move the statement out of its block, just after the block."""
        self._apply(pe.outdent)

    def toggle_statement(self):
        self._apply(pe.toggle)

    def _tree_dropped(self):
        """A statement was dragged to a new place in the tree: move it there in the procedure."""
        p, it = self._cur_proc(), self.tree.drag_item or self.tree.currentItem()
        self.tree.drag_item = None
        if p is None or self.tree.path(it) is None:
            return
        dest, index = self.tree.drop_target(it)
        self._populate_tree(pe.move_to(p, self.tree.path(it), dest, index))
        self._emit()

    def _toggle_else(self, on):
        path = self._cur_path()
        st = self._statement(path)
        if self._loading or st is None:
            return
        if on:
            st.setdefault("else", [])
        else:
            st.pop("else", None)
        self._populate_tree(path)
        self._emit()

    def _tree_menu(self, pos):
        m = QMenu(self)
        m.addMenu(self._type_menu(lambda t: self.add_statement(t))).setTitle("Add after")
        sub = self._type_menu(lambda t: self.add_statement(t, inside=True), nested=True)
        sub.setTitle("Add inside")
        m.addMenu(sub)
        m.addSeparator()
        for key in ("dup", "toggle", "indent", "outdent", "del"):
            b = self.st_btns[key]
            a = m.addAction(b.toolTip().split(" (")[0].split(":")[0], b.click)
            a.setEnabled(b.isEnabled())
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete and self.tree.hasFocus():
            self.remove_statement()
            return
        super().keyPressEvent(e)

    def _update_buttons(self):
        has_p = self._cur_proc() is not None
        path = self._cur_path()
        st = self._statement(path) if path and path[-1] != "else" else None
        ro = self._read_only
        self.add_proc_btn.setEnabled(not ro)
        for b in self.proc_btns.values():
            b.setEnabled(has_p and not ro)
        self.add_btn.setEnabled(not ro)
        container = bool(path) and (path[-1] == "else" or (st is not None and st.get("type") in pr.CONTAINERS))
        self.add_in_btn.setEnabled(container and not ro)
        for k, b in self.st_btns.items():
            b.setEnabled(st is not None and not ro if k != "json" else has_p and not ro)
        self.tree.setDragEnabled(not ro)

    # ------------------------------------------------------------------ validation
    def validate(self):
        """Check the procedures (against the project's zones, devices and areas) and show the problems."""
        self.issues = pr.validate(self.procs, self.context())
        self.issue_list.clear()
        icon = self.style().standardIcon(QStyle.SP_MessageBoxWarning)
        for pi, path, msg in self.issues:
            name = self.procs[pi].get("name", "?") if 0 <= pi < len(self.procs) else "?"
            where = f"{name} › {pr.path_text(path)}" if path else name
            it = QListWidgetItem(icon, f"{where}: {msg}")
            it.setData(ROLE, (pi, path))
            self.issue_list.addItem(it)
        if not self.issues:
            ok = QListWidgetItem("✓ No problems found")
            ok.setForeground(QBrush(QColor("#15803d")))
            self.issue_list.addItem(ok)
        self._mark_issues()
        for i in range(self.proc_list.count()):
            bad = any(pi == i for pi, _p, _m in self.issues)
            it = self.proc_list.item(i)
            it.setForeground(QBrush(QColor("#dc2626")) if bad else QBrush())

    def _mark_issues(self):
        pi = self._cur_proc_index()
        by_path: dict = {}
        for i, path, msg in self.issues:
            if i == pi and path:
                by_path.setdefault(tuple(path), []).append(msg)
        icon = self.style().standardIcon(QStyle.SP_MessageBoxWarning)
        for it in self.tree.all_items():
            msgs = by_path.get(self.tree.path(it) or ())
            it.setIcon(0, icon if msgs else QIcon())
            it.setToolTip(0, "\n".join(msgs) if msgs else "")
            it.setBackground(0, QBrush(QColor(254, 226, 226)) if msgs else QBrush())
        if self._issue_lbl is not None:
            msgs = by_path.get(self._cur_path() or (), [])
            self._issue_lbl.setText("\n".join("⚠ " + m for m in msgs))
            self._issue_lbl.setVisible(bool(msgs))

    def _issue_clicked(self, it: QListWidgetItem):
        d = it.data(ROLE)
        if not d:
            return
        pi, path = d
        if pi != self._cur_proc_index():
            self.proc_list.setCurrentRow(pi)
        item = self.tree.item_for(path)
        if item is not None:
            self.tree.setCurrentItem(item)

    # ------------------------------------------------------------------ side form
    def _statement_selected(self):
        self._update_buttons()
        self._build_form()

    def _clear_form(self):
        self._issue_lbl = None
        while self.form.rowCount():
            self.form.removeRow(0)
        self._form_widgets = {}

    def _build_form(self):
        self._clear_form()
        path, st = self._cur_path(), None
        if self._cur_proc() is None:
            self.form_box.setTitle("Statement")
            self.form.addRow(_wrapped("Add a procedure to start."))
        elif not path:
            self.form_box.setTitle("Statement")
            self.form.addRow(_wrapped("Select a statement, or use “Add” to build the procedure.\n\nA procedure is "
                                      "made of When blocks (what to do when something happens), variables and, "
                                      "optionally, statements that run from the start of the test."))
        elif path[-1] == "else":
            self.form_box.setTitle("Else")
            self.form.addRow(_wrapped("Statements inside Else run when the If condition is false."))
        else:
            st = self._statement(path)
        if st is None:
            return
        t = st.get("type")
        self.form_box.setTitle(f"{pr.STATEMENT_TYPES.get(t, t)} — statement {pr.path_text(path)}")
        with loading(self):
            self._statement_fields(st)
            en = QCheckBox("Enabled")
            en.setChecked(st.get("enabled", True) is not False)
            en.toggled.connect(lambda on: self.toggle_statement() if on != (st.get("enabled", True) is not False)
                               else None)
            self.form.addRow("", en)
            msgs = [m for pi, pth, m in self.issues if pi == self._cur_proc_index() and tuple(pth) == path]
            self._issue_lbl = _wrapped("\n".join("⚠ " + m for m in msgs))
            self._issue_lbl.setStyleSheet("color:#dc2626")
            self._issue_lbl.setVisible(bool(msgs))
            self.form.addRow(self._issue_lbl)
        self.form_box.setEnabled(not self._read_only)

    def _statement_fields(self, st):
        """The fields of a statement: its mode or the event / action it uses, its main fields
        (procedures.statement_fields: the event's or action's parameters, the seconds, condition, count or value),
        then the optional ones."""
        t = st.get("type")
        wait = pr.wait_mode(st) if t == "wait" else None
        if t == "wait":
            self._combo_field(st, "mode", "Wait", WAIT_MODES, wait, rebuild=True)
        elif t == "repeat":
            self._combo_field(st, "mode", "Repeat", REPEAT_MODES, pr.repeat_mode(st), rebuild=True)
        elif t == "set":
            self._line_field(st, pr.P("var", "var", "", "Variable", True))
            self._line_field(st, pr.P("index", "expr", "", "Index", help="optional: set one element of an array"))
        elif t == "stop":
            self._combo_field(st, "what", "Stop", pr.STOP_WHAT, st.get("what", "handler"))
        key = "event" if t == "when" or wait == "event" else "action" if t == "do" else None
        specs = pr.ACTION_SPECS if key == "action" else pr.EVENT_SPECS
        if key:
            self._spec_combo(st, key, key.capitalize(), specs, skip=("test_start",) if wait else ())
        for prm in pr.statement_fields(st):
            self._param_field(st, prm)
        if key:
            self._help_row(specs.get(st.get(key), {}).get("help"))
        if t == "when":
            self._combo_field(st, "mode", "If it recurs while running", pr.WHEN_MODES, st.get("mode", "ignore"))
            self._check_field(st, "once", "Only the first time")
        elif t == "wait" and wait != "seconds":
            self._line_field(st, pr.P("timeout", "number", "", "Timeout (s)",
                                      help="optional; afterwards timed_out = 1"))
        elif t == "if":
            cb = QCheckBox("Else branch")
            cb.setChecked("else" in st)
            cb.toggled.connect(self._toggle_else)
            self.form.addRow("", cb)
        elif t == "repeat":
            self._line_field(st, pr.P("var", "var", "", "Loop variable", help="optional: 0, 1, 2, …"))
        elif t == "comment":
            self._line_field(st, pr.P("text", "text", "", "Comment"))
        elif t == "var":
            self._line_field(st, pr.P("name", "var", "", "Name", True))
            self._line_field(st, pr.P("value", "expr", 0, "Initial value",
                                      help="number, 'text' or an array such as [0, 0, 0]"))
            self._check_field(st, "keep", "Keep the value between tests")
            self._check_field(st, "result", "Save as a test result")

    def _help_row(self, text):
        if text:
            lbl = _wrapped(text)
            lbl.setStyleSheet("color:palette(mid);font-style:italic")
            self.form.addRow(lbl)


    def _spec_combo(self, st, key, label, specs, skip=()):
        cb = _combo()
        cb.setMaxVisibleItems(25)
        group = None
        model = cb.model()
        for name, spec in specs.items():
            if name in skip:
                continue
            if spec["group"] != group:
                group = spec["group"]
                cb.addItem(f"— {group} —")
                model.item(cb.count() - 1).setEnabled(False)
            cb.addItem("   " + spec["label"], name)
        i = cb.findData(st.get(key))
        if i < 0:
            cb.addItem(f"Unknown: {st.get(key)}", st.get(key))
            i = cb.count() - 1
        cb.setCurrentIndex(i)

        def changed(_i):
            v = cb.currentData()
            if self._loading or v is None or v == st.get(key):
                return
            old = specs.get(st.get(key), {"params": []})
            for prm in old["params"]:
                st.pop(prm["name"], None)
            st[key] = v
            st.update(pr.spec_defaults(specs[v]))
            self._statement_edited(rebuild=True)
        cb.currentIndexChanged.connect(changed)
        self.form.addRow(label, cb)
        self._form_widgets[key] = cb

    def _combo_field(self, st, key, label, options: dict, current, rebuild=False):
        cb = _combo()
        for k, v in options.items():
            cb.addItem(v, k)
        cb.setCurrentIndex(max(0, cb.findData(current)))

        def changed(_i):
            if self._loading:
                return
            st[key] = cb.currentData()
            self._statement_edited(rebuild=rebuild)
        cb.currentIndexChanged.connect(changed)
        self.form.addRow(label, cb)
        self._form_widgets[key] = cb

    def _check_field(self, st, key, label):
        cb = QCheckBox(label)
        cb.setChecked(bool(st.get(key)))

        def changed(on):
            if self._loading:
                return
            if on:
                st[key] = True
            else:
                st.pop(key, None)
            self._statement_edited()
        cb.toggled.connect(changed)
        self.form.addRow("", cb)
        self._form_widgets[key] = cb

    def _names_for(self, typ) -> list[str]:
        ctx = self.context()
        if typ == "zone":
            return list(ctx.get("zones") or [])
        if typ in ("device", "audio"):
            devs = self.project.io_devices if self.project is not None else []
            return [d.get("name") for d in devs if typ == "device" or d.get("type") == "audio"]
        if typ in ("input", "output"):
            want = iod.INPUT_KINDS if typ == "input" else iod.OUTPUT_KINDS
            devs = self.project.io_devices if self.project is not None else []
            return sorted({c.get("name") for d in devs for c in d.get("channels", []) or []
                           if c.get("kind", "input") in want and c.get("name")})
        if typ == "var":
            return sorted(pr.declared_names(self.procs) - set(pr.LOCAL_NAMES))
        if typ == "procedure":
            return [p.get("name", "") for p in self.procs]
        if typ == "area":
            return list(ctx.get("areas") or [])
        if typ == "spec":
            return SPEC_EXAMPLES
        names = set()
        for p in self.procs:
            for _path, s in pr.iter_statements(p.get("statements")):
                spec = pr.EVENT_SPECS.get(s.get("event")) if s.get("type") in ("when", "wait") else \
                    pr.ACTION_SPECS.get(s.get("action")) if s.get("type") == "do" else None
                for prm in (spec or {}).get("params", []):
                    if prm["type"] == typ and s.get(prm["name"]):
                        names.add(str(s[prm["name"]]))
        return sorted(names)

    def _param_field(self, st, prm):
        typ = prm["type"]
        if typ == "bool":
            self._check_field(st, prm["name"], prm["label"])
        elif typ.startswith("choice:"):
            opts = {o: o.capitalize() for o in typ[7:].split("|")}
            self._combo_field(st, prm["name"], prm["label"], opts, st.get(prm["name"], prm["default"]))
        elif typ in ("number", "int", "expr", "text", "sequence"):
            self._line_field(st, prm)
        else:
            self._name_field(st, prm)

    def _line_field(self, st, prm):
        key, typ = prm["name"], prm["type"]
        if typ in ("var",) and key in ("var", "name"):
            return self._name_field(st, prm)
        ed = QLineEdit(value_text(st.get(key, prm["default"])))
        tip = {"number": "a number or an expression, e.g. 30 or randint(20, 40)",
               "int": "a whole number or an expression", "expr": "an expression, e.g. count >= 5 and zone('A')",
               "text": "text; {expression} inserts a value, e.g. Trial {trial}"}.get(typ, "")
        ed.setPlaceholderText(prm.get("help") or ("required" if prm.get("req") else ""))
        ed.setToolTip("\n".join(x for x in (prm.get("help"), tip) if x))

        def changed():
            if self._loading:
                return
            text = ed.text()
            if typ in ("number", "int"):
                v = _num_or_expr(text, typ == "int" and text.strip().lstrip("-").isdigit())
            else:
                v = text
            if v == "" and not prm.get("req") and key not in ("cond", "value", "until", "while", "text"):
                st.pop(key, None)
            else:
                st[key] = v
            self._statement_edited()
        ed.editingFinished.connect(changed)
        ed.textEdited.connect(lambda _t: self._live_preview(st, key, ed, typ))
        self.form.addRow(prm["label"], ed)
        self._form_widgets[key] = ed

    def _live_preview(self, st, key, ed, typ):
        """Colour the field red while it does not parse."""
        text = ed.text()
        bad = False
        if typ in ("expr",) or (typ in ("number", "int") and _num_or_expr(text) == text and text.strip()):
            bad = bool(text.strip()) and bool(pr.check_expr(text))
        ed.setStyleSheet("background:#fee2e2" if bad else "")

    def _name_field(self, st, prm):
        key, typ = prm["name"], prm["type"]
        cb = _combo()
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.NoInsert)
        names = [n for n in self._names_for(typ) if n]
        cb.addItems(names)
        cur = value_text(st.get(key, prm["default"]))
        cb.setCurrentText(cur)
        cb.lineEdit().setPlaceholderText(prm.get("help") or ("required" if prm.get("req") else ""))
        cb.setToolTip(prm.get("help", ""))

        def changed(*_):
            if self._loading:
                return
            v = cb.currentText().strip()
            if v == value_text(st.get(key, prm["default"])):
                return
            st[key] = v
            self._statement_edited()
        cb.lineEdit().editingFinished.connect(changed)
        cb.activated.connect(changed)
        row = cb
        if typ == "file":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(cb, 1)
            b = QToolButton()
            b.setText("…")

            def browse():
                f, _ = QFileDialog.getOpenFileName(self, prm["label"], cb.currentText())
                if f:
                    cb.setCurrentText(f)
                    changed()
            b.clicked.connect(browse)
            h.addWidget(b)
            row = w
        self.form.addRow(prm["label"], row)
        self._form_widgets[key] = cb

    def _statement_edited(self, rebuild=False):
        path = self._cur_path()
        st = self._statement(path)
        it = self.tree.currentItem()
        if st is None or it is None:
            return
        with loading(self):
            self.tree.style_item(it, st)
        self._emit()
        if rebuild:
            QTimer.singleShot(0, self._build_form)


    # ------------------------------------------------------------------ json & help
    def edit_json(self):
        p = self._cur_proc()
        if p is None or self._read_only:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Procedure “{p.get('name', '')}” as JSON")
        v = QVBoxLayout(dlg)
        ed = QPlainTextEdit(json.dumps(p, indent=2, ensure_ascii=False))
        ed.setFont(QFont("Menlo, Consolas, monospace"))
        v.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(bb)
        bb.rejected.connect(dlg.reject)

        def ok():
            try:
                q = json.loads(ed.toPlainText())
                if not isinstance(q, dict):
                    raise ValueError("the procedure must be a JSON object")
                q = pr.normalize_procedures([q])[0]
            except Exception as e:
                QMessageBox.warning(dlg, "Invalid JSON", str(e))
                return
            dlg.accept()
            self.set_procedure_json(self._cur_proc_index(), q)
        bb.accepted.connect(ok)
        dlg.resize(560, 600)
        dlg.exec()

    def set_procedure_json(self, index: int, proc: dict):
        self.procs[index] = proc
        self._refresh_all(select=index)
        self._emit()

    def show_functions(self):
        rows = "".join(f"<tr><td><code>{n}</code></td><td>{h or ''}</td></tr>" for n, (_a, _b, _f, h)
                       in pr.FUNCTIONS.items())
        html = ("<h3>Expressions</h3><p>Numbers, 'text', arrays <code>[1, 2, 3]</code>, variables, "
                "<code>+ - * / // % **</code>, <code>== != &lt; &lt;= &gt; &gt;=</code>, <code>and or not</code>, "
                "<code>a if condition else b</code>, <code>array[i]</code>, <code>x in array</code>.</p>"
                "<p>Inside a handler: <code>event_time</code>, <code>event_name</code> (zone, input, key…), "
                "<code>event_value</code>; after a wait with a timeout: <code>timed_out</code>.</p>"
                f"<table cellspacing=4>{rows}</table>")
        dlg = QDialog(self)
        dlg.setWindowTitle("Expression functions")
        v = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setHtml(html)
        v.addWidget(tb)
        dlg.resize(520, 560)
        dlg.exec()

