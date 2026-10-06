"""Procedure editor (block/tree editor for Project.procedures) and the I/O devices dialog.

``ProcedureEditor`` edits ``project.procedures`` in place: procedures on the left (tick to enable), their
statements as a tree in the middle (drag & drop to move or nest, toolbar to add / indent / outdent / move),
the selected statement's parameters on the right, and the validation messages below. Signal ``changed``.

``IODevicesDialog`` edits ``project.io_devices`` (device type, port, channels) and shows the live state of
the I/O with manual output toggles and a test button.
"""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QStyle, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import iodevices as iod
from ..core import procedures as pr

ROLE = Qt.UserRole
ELSE = "__else__"
COLORS = {"when": "#7c3aed", "wait": "#b45309", "if": "#2563eb", ELSE: "#2563eb", "repeat": "#0d9488",
          "set": "#15803d", "do": None, "stop": "#dc2626", "comment": "#6b7280", "var": "#166534"}
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


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v)) if abs(v) < 1e15 else str(v)
    return str(v)


def _combo() -> QComboBox:
    cb = QComboBox()
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(10)
    return cb


class StatementTree(QTreeWidget):
    """Tree of statements with internal drag & drop; emits ``dropped`` after a move."""

    dropped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setIndentation(22)
        self.setUniformRowHeights(True)
        self.setAnimated(False)

    drag_item = None

    def dropEvent(self, e):
        self.drag_item = self.currentItem()
        super().dropEvent(e)
        self.dropped.emit()


# ====================================================================== procedure editor
class ProcedureEditor(QWidget):
    changed = Signal()

    def __init__(self, project=None, parent=None, context: dict | None = None):
        if isinstance(project, QWidget):  # ProcedureEditor(parent_widget)
            project, parent = None, parent or project
        super().__init__(parent)
        self.project = None
        self.procs: list = []
        self._ctx_override = context
        self.issues: list = []
        self._loading = False
        self._read_only = False
        self._form_widgets: dict = {}
        self._meta: dict[int, tuple] = {}
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

    def set_context(self, context: dict | None):
        self._ctx_override = context
        self._validate()

    def set_read_only(self, ro: bool):
        self._read_only = ro
        self._update_buttons()
        self._build_form()

    def context(self) -> dict:
        if self._ctx_override is not None:
            return self._ctx_override
        p = self.project
        if p is None:
            return {}
        zones = []
        for a in getattr(p, "apparatus", []) or []:
            zones += [z.name for z in getattr(a, "zones", [])] + [g.name for g in getattr(a, "groups", [])]
        extra = getattr(p, "settings_extra", {}) or {}
        areas = [a.get("name") for a in (extra.get("touchscreen", {}) or {}).get("areas", []) if a.get("name")]
        ctx = {"zones": sorted(set(zones)) if zones else None, "devices": list(getattr(p, "io_devices", []) or [])}
        if areas:
            ctx["areas"] = areas
        if not p.io_devices:
            ctx["devices"] = None
        return ctx

    def _emit(self):
        self._validate()
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
        self._loading = True
        cur = self.proc_list.currentRow() if select is None else select
        self.proc_list.clear()
        for p in self.procs:
            it = QListWidgetItem(str(p.get("name", "Procedure")))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
            it.setCheckState(Qt.Checked if p.get("enabled", True) else Qt.Unchecked)
            self.proc_list.addItem(it)
        self._loading = False
        if self.procs:
            self.proc_list.setCurrentRow(min(max(cur, 0), len(self.procs) - 1))
        self._proc_selected()

    def _proc_item_changed(self, it: QListWidgetItem):
        if self._loading or self._read_only:
            return
        i = self.proc_list.row(it)
        if not 0 <= i < len(self.procs):
            return
        p = self.procs[i]
        name, on = it.text().strip() or p.get("name", "Procedure"), it.checkState() == Qt.Checked
        if name != p.get("name") or on != p.get("enabled", True):
            p["name"], p["enabled"] = name, on
            self._emit()

    def _proc_selected(self):
        self._populate_tree()
        self._update_buttons()
        self._validate()

    def add_procedure(self, proc: dict | None = None):
        if self._read_only:
            return
        proc = proc or pr.new_procedure(self._unique_name("Procedure"))
        names = {p.get("name") for p in self.procs}
        if proc.get("name") in names:
            proc["name"] = self._unique_name(proc["name"])
        self.procs.append(proc)
        self._refresh_all(select=len(self.procs) - 1)
        self._emit()

    def _unique_name(self, base):
        names = {p.get("name") for p in self.procs}
        if base not in names:
            return base
        k = 2
        while f"{base} {k}" in names:
            k += 1
        return f"{base} {k}"

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
        self._loading = True
        self.tree.clear()
        self._meta = {}
        p = self._cur_proc()
        if p is not None:
            self._add_items(self.tree.invisibleRootItem(), p.get("statements") or [], ())
            self.tree.expandAll()
        self._loading = False
        item = self._item_for(select_path) if select_path is not None else None
        if item is None and self.tree.topLevelItemCount():
            item = self.tree.topLevelItem(0) if select_path is None else None
        if item is not None:
            self.tree.setCurrentItem(item)
        self._statement_selected()
        self._mark_issues()

    def _add_items(self, parent, stmts, path):
        for i, st in enumerate(stmts):
            if not isinstance(st, dict):
                continue
            p = path + (i,)
            it = QTreeWidgetItem(parent)
            self._style_item(it, st, p)
            if st.get("type") in pr.CONTAINERS:
                self._add_items(it, st.get("body") or [], p + ("body",))
                if st.get("type") == "if" and "else" in st:
                    el = QTreeWidgetItem(it)
                    self._set_meta(el, {"type": ELSE}, p + ("else",))
                    el.setText(0, "Else")
                    el.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled)
                    f = el.font(0)
                    f.setBold(True)
                    el.setFont(0, f)
                    el.setForeground(0, QBrush(QColor(COLORS[ELSE])))
                    self._add_items(el, st.get("else") or [], p + ("else",))

    def _style_item(self, it: QTreeWidgetItem, st: dict, path: tuple):
        t = st.get("type")
        data = {k: v for k, v in st.items() if k not in ("body", "else")}
        if t == "if" and "else" in st:
            data["else"] = []
        key = it.data(0, ROLE)
        if key in self._meta:
            self._meta[key] = (data, tuple(path))
        else:
            self._set_meta(it, data, path)
        it.setText(0, pr.describe_statement(st))
        it.setToolTip(0, "")
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled
        if t in pr.CONTAINERS:
            flags |= Qt.ItemIsDropEnabled
        it.setFlags(flags)
        f = QFont(it.font(0))
        f.setBold(t in ("when", "var"))
        f.setItalic(t == "comment")
        f.setStrikeOut(st.get("enabled", True) is False)
        it.setFont(0, f)
        col = COLORS.get(t)
        if st.get("enabled", True) is False:
            col = "#9ca3af"
        it.setForeground(0, QBrush(QColor(col)) if col else QBrush())

    def _item_for(self, path) -> QTreeWidgetItem | None:
        if path is None:
            return None

        def walk(parent):
            for i in range(parent.childCount()):
                it = parent.child(i)
                if self._path(it) == tuple(path):
                    return it
                r = walk(it)
                if r is not None:
                    return r
            return None
        return walk(self.tree.invisibleRootItem())

    def _collect(self, parent) -> list:
        out = []
        for i in range(parent.childCount()):
            it = parent.child(i)
            d = self._data(it)
            if d.get("type") == ELSE:
                continue
            st = {k: copy.deepcopy(v) for k, v in d.items() if k not in ("body", "else")}
            t = st.get("type")
            if t in pr.CONTAINERS:
                st["body"] = self._collect(it)
                if t == "if":
                    el = next((it.child(j) for j in range(it.childCount())
                               if self._data(it.child(j)).get("type") == ELSE), None)
                    if el is not None:
                        st["else"] = self._collect(el)
                    elif "else" in d:
                        st["else"] = []
            out.append(st)
        return out

    def _tree_dropped(self):
        p = self._cur_proc()
        if p is None:
            return
        cur = self.tree.drag_item or self.tree.currentItem()
        root = self.tree.invisibleRootItem()
        p["statements"] = self._collect(root)
        # find the new path of the dragged item before rebuilding
        new_path = None
        if cur is not None:
            idx = []
            it = cur
            while it is not None:
                parent = it.parent() or root
                pd = self._data(parent) if parent is not root else {}
                siblings = [parent.child(k) for k in range(parent.childCount())
                            if self._data(parent.child(k)).get("type") != ELSE]
                pos = next((k for k, sib in enumerate(siblings) if sib is it), 0)
                if pd.get("type") == ELSE:
                    idx = ["else", pos] + idx
                    it = parent.parent()
                    continue
                idx = [pos] + idx
                if parent is not root:
                    idx = ["body"] + idx
                it = parent if parent is not root else None
            new_path = tuple(idx)
        self.tree.drag_item = None
        self._populate_tree(new_path)
        self._emit()

    def _set_meta(self, it: QTreeWidgetItem, data: dict, path: tuple):
        key = len(self._meta) + 1
        self._meta[key] = (data, tuple(path))
        it.setData(0, ROLE, key)

    def _data(self, it) -> dict:
        m = self._meta.get(it.data(0, ROLE)) if it is not None else None
        return m[0] if m else {}

    def _path(self, it) -> tuple | None:
        m = self._meta.get(it.data(0, ROLE)) if it is not None else None
        return m[1] if m else None

    def _cur_path(self) -> tuple | None:
        return self._path(self.tree.currentItem())

    def _block_and_index(self, path):
        """(list containing the statement, index) for a statement path."""
        p = self._cur_proc()
        block = p["statements"]
        st = None
        for k in path[:-1]:
            if isinstance(k, int):
                st = block[k]
            else:
                block = st.setdefault(k, [])
        return block, path[-1]

    def _statement(self, path) -> dict | None:
        p = self._cur_proc()
        return pr.statement_at(p, path) if p is not None and path else None

    def add_statement(self, type_: str, inside: bool = False) -> tuple | None:
        p = self._cur_proc()
        if self._read_only:
            return None
        if p is None:
            self.add_procedure()
            p = self._cur_proc()
        st = pr.new_statement(type_)
        path = self._cur_path()
        if path and path[-1] == "else":
            inside = True
        if inside and path:
            if path[-1] == "else":
                parent = self._statement(path[:-1])
                block = parent.setdefault("else", [])
                new_path = path + (len(block),)
            else:
                parent = self._statement(path)
                if parent is None or parent.get("type") not in pr.CONTAINERS:
                    return None
                block = parent.setdefault("body", [])
                new_path = path + ("body", len(block))
            block.append(st)
        elif path:
            if path[-1] == "else":
                path = path[:-1]
            block, i = self._block_and_index(path)
            block.insert(i + 1, st)
            new_path = path[:-1] + (i + 1,)
        else:
            p.setdefault("statements", []).append(st)
            new_path = (len(p["statements"]) - 1,)
        self._populate_tree(new_path)
        self._emit()
        return new_path

    def remove_statement(self):
        path = self._cur_path()
        if not path or self._read_only:
            return
        if path[-1] == "else":
            parent = self._statement(path[:-1])
            parent.pop("else", None)
            self._populate_tree(path[:-1])
        else:
            block, i = self._block_and_index(path)
            del block[i]
            nxt = path[:-1] + (i,) if i < len(block) else (path[:-1] + (i - 1,) if i > 0 else
                                                          (path[:-2] if len(path) > 1 else None))
            self._populate_tree(nxt)
        self._emit()

    def duplicate_statement(self):
        path = self._cur_path()
        if not path or path[-1] == "else" or self._read_only:
            return
        block, i = self._block_and_index(path)
        block.insert(i + 1, copy.deepcopy(block[i]))
        self._populate_tree(path[:-1] + (i + 1,))
        self._emit()

    def move_statement(self, d: int):
        path = self._cur_path()
        if not path or path[-1] == "else" or self._read_only:
            return
        block, i = self._block_and_index(path)
        j = i + d
        if not 0 <= j < len(block):
            return
        block[i], block[j] = block[j], block[i]
        self._populate_tree(path[:-1] + (j,))
        self._emit()

    def indent_statement(self):
        """Move the statement into the container just above it (as its last child)."""
        path = self._cur_path()
        if not path or path[-1] == "else" or self._read_only:
            return
        block, i = self._block_and_index(path)
        if i == 0 or block[i - 1].get("type") not in pr.CONTAINERS:
            return
        st = block.pop(i)
        target = block[i - 1]
        if target.get("type") == "if" and "else" in target:
            target["else"].append(st)
            new = path[:-1] + (i - 1, "else", len(target["else"]) - 1)
        else:
            target.setdefault("body", []).append(st)
            new = path[:-1] + (i - 1, "body", len(target["body"]) - 1)
        self._populate_tree(new)
        self._emit()

    def outdent_statement(self):
        """Move the statement out of its block, just after the block."""
        path = self._cur_path()
        if not path or path[-1] == "else" or len(path) < 3 or self._read_only:
            return
        block, i = self._block_and_index(path)
        st = block.pop(i)
        parent_path = path[:-2]
        outer, j = self._block_and_index(parent_path)
        outer.insert(j + 1, st)
        self._populate_tree(parent_path[:-1] + (j + 1,))
        self._emit()

    def toggle_statement(self):
        path = self._cur_path()
        st = self._statement(path) if path and path[-1] != "else" else None
        if st is None or self._read_only:
            return
        if st.get("enabled", True) is False:
            st.pop("enabled", None)
        else:
            st["enabled"] = False
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
    def _validate(self):
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

        def walk(parent):
            for k in range(parent.childCount()):
                it = parent.child(k)
                msgs = by_path.get(self._path(it) or ())
                if msgs:
                    it.setIcon(0, icon)
                    it.setToolTip(0, "\n".join(msgs))
                    it.setBackground(0, QBrush(QColor(254, 226, 226)))
                else:
                    it.setIcon(0, QIcon())
                    it.setToolTip(0, "")
                    it.setBackground(0, QBrush())
                walk(it)
        walk(self.tree.invisibleRootItem())
        if self._issue_lbl is not None:
            path = self._cur_path()
            msgs = by_path.get(path or (), [])
            self._issue_lbl.setText("\n".join("⚠ " + m for m in msgs))
            self._issue_lbl.setVisible(bool(msgs))

    def _issue_clicked(self, it: QListWidgetItem):
        d = it.data(ROLE)
        if not d:
            return
        pi, path = d
        if pi != self._cur_proc_index():
            self.proc_list.setCurrentRow(pi)
        if path:
            item = self._item_for(tuple(path))
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
        path = self._cur_path()
        p = self._cur_proc()
        if p is None:
            self.form_box.setTitle("Statement")
            self.form.addRow(QLabel("Add a procedure to start."))
            return
        if not path:
            self.form_box.setTitle("Statement")
            lbl = QLabel("Select a statement, or use “Add” to build the procedure.\n\nA procedure is made of "
                         "When blocks (what to do when something happens), variables and, optionally, "
                         "statements that run from the start of the test.")
            lbl.setWordWrap(True)
            self.form.addRow(lbl)
            return
        if path[-1] == "else":
            self.form_box.setTitle("Else")
            lbl = QLabel("Statements inside Else run when the If condition is false.")
            lbl.setWordWrap(True)
            self.form.addRow(lbl)
            return
        st = self._statement(path)
        if st is None:
            return
        t = st.get("type")
        self.form_box.setTitle(f"{pr.STATEMENT_TYPES.get(t, t)} — statement {pr.path_text(path)}")
        self._loading = True
        try:
            if t == "when":
                self._event_fields(st, with_mode=True)
            elif t == "wait":
                self._combo_field(st, "mode", "Wait", WAIT_MODES, pr._wait_mode(st), rebuild=True)
                mode = pr._wait_mode(st)
                if mode == "seconds":
                    self._line_field(st, pr.P("seconds", "number", 1, "Seconds", True))
                elif mode == "until":
                    self._line_field(st, pr.P("until", "expr", "", "Condition", True))
                else:
                    self._event_fields(st, with_mode=False)
                if mode != "seconds":
                    self._line_field(st, pr.P("timeout", "number", "", "Timeout (s)",
                                              help="optional; afterwards timed_out = 1"))
            elif t == "if":
                self._line_field(st, pr.P("cond", "expr", "", "Condition", True))
                cb = QCheckBox("Else branch")
                cb.setChecked("else" in st)
                cb.toggled.connect(lambda on: self._toggle_else(on))
                self.form.addRow("", cb)
            elif t == "repeat":
                mode = pr._repeat_mode(st)
                self._combo_field(st, "mode", "Repeat", REPEAT_MODES, mode, rebuild=True)
                if mode == "count":
                    self._line_field(st, pr.P("count", "int", 3, "Times", True))
                elif mode == "while":
                    self._line_field(st, pr.P("while", "expr", "", "Condition", True))
                self._line_field(st, pr.P("var", "var", "", "Loop variable", help="optional: 0, 1, 2, …"))
            elif t == "set":
                self._line_field(st, pr.P("var", "var", "", "Variable", True))
                self._line_field(st, pr.P("index", "expr", "", "Index", help="optional: set one element of an array"))
                self._line_field(st, pr.P("value", "expr", "0", "Value", True))
            elif t == "do":
                self._spec_combo(st, "action", "Action", pr.ACTION_SPECS)
                spec = pr.ACTION_SPECS.get(st.get("action"))
                if spec:
                    for prm in spec["params"]:
                        self._param_field(st, prm)
                    self._help_row(spec.get("help"))
            elif t == "stop":
                self._combo_field(st, "what", "Stop", pr.STOP_WHAT, st.get("what", "handler"))
            elif t == "comment":
                self._line_field(st, pr.P("text", "text", "", "Comment"))
            elif t == "var":
                self._line_field(st, pr.P("name", "var", "", "Name", True))
                self._line_field(st, pr.P("value", "expr", 0, "Initial value",
                                          help="number, 'text' or an array such as [0, 0, 0]"))
                self._check_field(st, "keep", "Keep the value between tests")
                self._check_field(st, "result", "Save as a test result")
            en = QCheckBox("Enabled")
            en.setChecked(st.get("enabled", True) is not False)
            en.toggled.connect(lambda on: self.toggle_statement() if on != (st.get("enabled", True) is not False)
                               else None)
            self.form.addRow("", en)
            msgs = [m for pi, pth, m in self.issues if pi == self._cur_proc_index() and tuple(pth) == path]
            self._issue_lbl = QLabel("\n".join("⚠ " + m for m in msgs))
            self._issue_lbl.setWordWrap(True)
            self._issue_lbl.setStyleSheet("color:#dc2626")
            self._issue_lbl.setVisible(bool(msgs))
            self.form.addRow(self._issue_lbl)
        finally:
            self._loading = False
        self.form_box.setEnabled(not self._read_only)

    def _help_row(self, text):
        if text:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:palette(mid);font-style:italic")
            self.form.addRow(lbl)

    def _event_fields(self, st, with_mode):
        self._spec_combo(st, "event", "Event", pr.EVENT_SPECS, skip=() if with_mode else ("test_start",))
        spec = pr.EVENT_SPECS.get(st.get("event"))
        if spec:
            for prm in spec["params"]:
                self._param_field(st, prm)
            self._help_row(spec.get("help"))
        if with_mode:
            self._combo_field(st, "mode", "If it recurs while running", pr.WHEN_MODES, st.get("mode", "ignore"))
            self._check_field(st, "once", "Only the first time")

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
        ed = QLineEdit(_txt(st.get(key, prm["default"])))
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
        cur = _txt(st.get(key, prm["default"]))
        cb.setCurrentText(cur)
        cb.lineEdit().setPlaceholderText(prm.get("help") or ("required" if prm.get("req") else ""))
        cb.setToolTip(prm.get("help", ""))

        def changed(*_):
            if self._loading:
                return
            v = cb.currentText().strip()
            if v == _txt(st.get(key, prm["default"])):
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

    def _toggle_else(self, on):
        if self._loading:
            return
        path = self._cur_path()
        st = self._statement(path)
        if st is None:
            return
        if on:
            st.setdefault("else", [])
        else:
            st.pop("else", None)
        self._populate_tree(path)
        self._emit()

    def _statement_edited(self, rebuild=False):
        path = self._cur_path()
        st = self._statement(path)
        it = self.tree.currentItem()
        if st is None or it is None:
            return
        self._loading = True
        self._style_item(it, st, path)
        self._loading = False
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


# ====================================================================== I/O devices dialog
CH_COLS = ["Name", "Kind", "Pin", "Pin B", "Invert", "On text", "Off text", "Options"]
_CH_KEYS = {"name", "kind", "pin", "pin_b", "invert", "on", "off"}


def _parse_options(text: str) -> dict:
    out = {}
    for part in text.replace(";", ",").split(","):
        if "=" not in part:
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        if not k:
            continue
        try:
            fv = float(v)
            out[k] = int(fv) if fv.is_integer() and "." not in v else fv
        except ValueError:
            out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def _options_text(ch: dict) -> str:
    return ", ".join(f"{k}={_txt(v)}" for k, v in ch.items() if k not in _CH_KEYS)


class IODevicesDialog(QDialog):
    """Configure project.io_devices, with live I/O status, manual outputs and a test button."""

    changed = Signal()

    def __init__(self, project, parent=None, manager: iod.DeviceManager | None = None):
        super().__init__(parent)
        self.setWindowTitle("I/O devices")
        self.project = project
        self.configs = copy.deepcopy(list(project.io_devices or []))
        self.manager = manager
        self._own_manager = False
        self._loading = False
        self._status_keys: list = []
        self._build()
        self._refresh_list(0)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._poll)
        if manager is not None:
            self.timer.start()
            self._refresh_status(force=True)

    def _build(self):
        v = QVBoxLayout(self)
        top = QSplitter(Qt.Horizontal)
        v.addWidget(top, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Devices</b>"))
        self.dev_list = QListWidget()
        self.dev_list.currentRowChanged.connect(lambda *_: self._load_device())
        lv.addWidget(self.dev_list, 1)
        hb = QHBoxLayout()
        add = QToolButton()
        add.setText("Add ▾")
        add.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(add)
        for t, label in iod.DEVICE_TYPES.items():
            m.addAction(label, lambda t=t: self.add_device(t))
        add.setMenu(m)
        rm = QToolButton()
        rm.setText("Remove")
        rm.clicked.connect(self.remove_device)
        hb.addWidget(add)
        hb.addWidget(rm)
        hb.addStretch()
        lv.addLayout(hb)
        top.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.dev_box = QGroupBox("Device")
        f = QFormLayout(self.dev_box)
        self.f_name = QLineEdit()
        self.f_type = QComboBox()
        for t, label in iod.DEVICE_TYPES.items():
            self.f_type.addItem(label, t)
        self.f_port = QComboBox()
        self.f_port.setEditable(True)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Refresh the list of serial ports")
        refresh.clicked.connect(self._fill_ports)
        prow = QWidget()
        ph = QHBoxLayout(prow)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self.f_port, 1)
        ph.addWidget(refresh)
        self.f_baud = QComboBox()
        self.f_baud.setEditable(True)
        self.f_baud.addItems(["115200", "57600", "38400", "19200", "9600"])
        self.f_watchdog = QSpinBox()
        self.f_watchdog.setRange(0, 60000)
        self.f_watchdog.setSingleStep(500)
        self.f_watchdog.setSuffix(" ms")
        self.f_watchdog.setSpecialValueText("Off")
        self.f_watchdog.setToolTip("All outputs switch off if the computer stops talking to the board")
        self.f_backend = QComboBox()
        for b in ("auto", "none", "afplay", "paplay", "aplay"):
            self.f_backend.addItem(b, b)
        self.f_enabled = QCheckBox("Enabled")
        f.addRow("Name", self.f_name)
        f.addRow("Type", self.f_type)
        f.addRow("Serial port", prow)
        f.addRow("Baud rate", self.f_baud)
        f.addRow("Watchdog", self.f_watchdog)
        f.addRow("Audio player", self.f_backend)
        f.addRow("", self.f_enabled)
        self._rows = {"port": prow, "baud": self.f_baud, "watchdog": self.f_watchdog, "backend": self.f_backend}
        self._form = f
        for w in (self.f_name,):
            w.editingFinished.connect(self._save_device)
        for w in (self.f_type, self.f_backend):
            w.currentIndexChanged.connect(lambda *_: self._save_device(retype=True))
        self.f_port.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_baud.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_watchdog.valueChanged.connect(lambda *_: self._save_device())
        self.f_enabled.toggled.connect(lambda *_: self._save_device())
        rv.addWidget(self.dev_box)

        self.ch_box = QGroupBox("Channels")
        cv = QVBoxLayout(self.ch_box)
        self.ch_table = QTableWidget(0, len(CH_COLS))
        self.ch_table.setHorizontalHeaderLabels(CH_COLS)
        self.ch_table.verticalHeader().hide()
        hh = self.ch_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(CH_COLS) - 1, QHeaderView.Stretch)
        self.ch_table.itemChanged.connect(lambda *_: self._save_channels())
        self.ch_table.setToolTip("Options (key=value, comma separated): pullup, debounce_ms, counts_per_rev, "
                                 "cm_per_rev, scale, period_ms, deadband")
        cv.addWidget(self.ch_table)
        cb = QHBoxLayout()
        b1 = QPushButton("Add channel")
        b1.clicked.connect(lambda: self.add_channel())
        b2 = QPushButton("Remove channel")
        b2.clicked.connect(self.remove_channel)
        cb.addWidget(b1)
        cb.addWidget(b2)
        cb.addStretch()
        cv.addLayout(cb)
        rv.addWidget(self.ch_box, 1)
        top.addWidget(right)
        top.setSizes([170, 560])

        live = QGroupBox("Live status")
        lv2 = QVBoxLayout(live)
        hb2 = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip("Open the devices and show their inputs and outputs live")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.test_btn = QPushButton("Test")
        self.test_btn.setToolTip("Pulse the selected output for 0.5 s, or play a 1 kHz tone on an audio device")
        self.test_btn.clicked.connect(self.test_selected)
        hb2.addWidget(self.connect_btn)
        hb2.addWidget(self.test_btn)
        self.conn_lbl = QLabel("Not connected")
        self.conn_lbl.setWordWrap(True)
        hb2.addWidget(self.conn_lbl, 1)
        lv2.addLayout(hb2)
        self.status = QTableWidget(0, 5)
        self.status.setHorizontalHeaderLabels(["Device", "Channel", "Kind", "Value", ""])
        self.status.verticalHeader().hide()
        self.status.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.status.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.status.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.status.setMinimumHeight(130)
        lv2.addWidget(self.status)
        v.addWidget(live)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(860, 640)
        self._fill_ports()

    # ------------------------------------------------------------------ devices
    def _fill_ports(self):
        ports = iod.serial_ports()
        cur = self.f_port.currentText()
        self._loading = True
        self.f_port.clear()
        if ports is None:
            self.f_port.setToolTip("pyserial is not installed (pip install pyserial)")
        else:
            self.f_port.addItems(ports)
        self.f_port.setCurrentText(cur)
        self._loading = False

    def _refresh_list(self, select=None):
        self._loading = True
        cur = self.dev_list.currentRow() if select is None else select
        self.dev_list.clear()
        for c in self.configs:
            self.dev_list.addItem(f"{c.get('name', '?')}  ·  {iod.DEVICE_TYPES.get(c.get('type'), c.get('type'))}")
        self._loading = False
        if self.configs:
            self.dev_list.setCurrentRow(min(max(cur, 0), len(self.configs) - 1))
        self._load_device()

    def _cur(self) -> dict | None:
        i = self.dev_list.currentRow()
        return self.configs[i] if 0 <= i < len(self.configs) else None

    def add_device(self, type_: str = "virtual"):
        names = {c.get("name") for c in self.configs}
        base = {"virtual": "sim", "arduino": "box", "serial": "serial", "audio": "speakers"}[type_]
        name, k = base, 2
        while name in names:
            name, k = f"{base}{k}", k + 1
        cfg = {"name": name, "type": type_, "enabled": True, "channels": []}
        if type_ in ("arduino", "serial"):
            cfg.update(port="", baud=115200 if type_ == "arduino" else 9600)
        if type_ == "audio":
            cfg["backend"] = "auto"
        self.configs.append(cfg)
        self._refresh_list(len(self.configs) - 1)

    def remove_device(self):
        i = self.dev_list.currentRow()
        if 0 <= i < len(self.configs):
            del self.configs[i]
            self._refresh_list(min(i, len(self.configs) - 1))

    def _load_device(self):
        c = self._cur()
        self.dev_box.setEnabled(c is not None)
        self.ch_box.setEnabled(c is not None)
        self._loading = True
        try:
            c = c or {}
            self.f_name.setText(c.get("name", ""))
            self.f_type.setCurrentIndex(max(0, self.f_type.findData(c.get("type", "virtual"))))
            self.f_port.setCurrentText(c.get("port", ""))
            self.f_baud.setCurrentText(str(c.get("baud", 115200)))
            self.f_watchdog.setValue(int(c.get("watchdog_ms", 0) or 0))
            self.f_backend.setCurrentIndex(max(0, self.f_backend.findData(c.get("backend", "auto"))))
            self.f_enabled.setChecked(c.get("enabled", True))
            self._fill_channels(c)
        finally:
            self._loading = False
        self._update_visibility()

    def _update_visibility(self):
        t = (self._cur() or {}).get("type", "virtual")
        for key, w in self._rows.items():
            show = {"port": t in ("arduino", "serial"), "baud": t in ("arduino", "serial"),
                    "watchdog": t == "arduino", "backend": t == "audio"}[key]
            w.setVisible(show)
            lbl = self._form.labelForField(w)
            if lbl is not None:
                lbl.setVisible(show)
        hidden = {"virtual": {2, 3, 4, 5, 6}, "arduino": {5, 6}, "serial": {2, 3, 4}, "audio": set()}.get(t, set())
        for col in range(len(CH_COLS)):
            self.ch_table.setColumnHidden(col, col in hidden)
        self.ch_box.setVisible(t != "audio")

    def _save_device(self, retype=False):
        if self._loading:
            return
        c = self._cur()
        if c is None:
            return
        c["name"] = self.f_name.text().strip() or c.get("name", "device")
        c["type"] = self.f_type.currentData()
        c["enabled"] = self.f_enabled.isChecked()
        for k in ("port", "baud", "watchdog_ms", "backend"):
            c.pop(k, None)
        if c["type"] in ("arduino", "serial"):
            c["port"] = self.f_port.currentText().strip()
            try:
                c["baud"] = int(self.f_baud.currentText())
            except ValueError:
                c["baud"] = 115200
        if c["type"] == "arduino" and self.f_watchdog.value():
            c["watchdog_ms"] = self.f_watchdog.value()
        if c["type"] == "audio":
            c["backend"] = self.f_backend.currentData()
        it = self.dev_list.currentItem()
        if it is not None:
            it.setText(f"{c['name']}  ·  {iod.DEVICE_TYPES.get(c['type'], c['type'])}")
        if retype:
            self._update_visibility()

    # ------------------------------------------------------------------ channels
    def _fill_channels(self, c):
        self.ch_table.setRowCount(0)
        for ch in c.get("channels", []) or []:
            self._append_channel_row(ch)

    def _append_channel_row(self, ch):
        r = self.ch_table.rowCount()
        self.ch_table.insertRow(r)
        self.ch_table.setItem(r, 0, QTableWidgetItem(str(ch.get("name", ""))))
        kind = QComboBox()
        for k, label in iod.CHANNEL_KINDS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(ch.get("kind", "input"))))
        kind.currentIndexChanged.connect(lambda *_: self._save_channels())
        self.ch_table.setCellWidget(r, 1, kind)
        self.ch_table.setItem(r, 2, QTableWidgetItem(_txt(ch.get("pin", ""))))
        self.ch_table.setItem(r, 3, QTableWidgetItem(_txt(ch.get("pin_b", ""))))
        inv = QTableWidgetItem()
        inv.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        inv.setCheckState(Qt.Checked if ch.get("invert") else Qt.Unchecked)
        self.ch_table.setItem(r, 4, inv)
        self.ch_table.setItem(r, 5, QTableWidgetItem(str(ch.get("on", ""))))
        self.ch_table.setItem(r, 6, QTableWidgetItem(str(ch.get("off", ""))))
        self.ch_table.setItem(r, 7, QTableWidgetItem(_options_text(ch)))

    def add_channel(self, ch: dict | None = None):
        c = self._cur()
        if c is None:
            return
        n = len(c.get("channels", []))
        ch = ch or {"name": f"ch{n + 1}", "kind": "output" if n % 2 else "input"}
        if c.get("type") == "arduino" and "pin" not in ch:
            used = {int(x["pin"]) for x in c.get("channels", []) if str(x.get("pin", "")).isdigit()}
            ch["pin"] = next(p for p in range(2, 70) if p not in used)
        c.setdefault("channels", []).append(ch)
        self._loading = True
        self._append_channel_row(ch)
        self._loading = False

    def remove_channel(self):
        c = self._cur()
        r = self.ch_table.currentRow()
        if c is not None and 0 <= r < len(c.get("channels", [])):
            del c["channels"][r]
            self.ch_table.removeRow(r)

    def _save_channels(self):
        if self._loading:
            return
        c = self._cur()
        if c is None:
            return
        out = []
        t = self.ch_table
        for r in range(t.rowCount()):
            def txt(col):
                it = t.item(r, col)
                return it.text().strip() if it is not None else ""
            ch = {"name": txt(0), "kind": t.cellWidget(r, 1).currentData()}
            for col, key in ((2, "pin"), (3, "pin_b")):
                v = txt(col)
                if v:
                    ch[key] = int(v) if v.lstrip("-").isdigit() else v
            inv = t.item(r, 4)
            if inv is not None and inv.checkState() == Qt.Checked:
                ch["invert"] = True
            for col, key in ((5, "on"), (6, "off")):
                if txt(col):
                    ch[key] = txt(col)
            ch.update(_parse_options(txt(7)))
            out.append(ch)
        c["channels"] = out

    # ------------------------------------------------------------------ live status
    def toggle_connection(self):
        if self.manager is not None and self._own_manager:
            self.disconnect_devices()
        else:
            self.connect_devices()

    def connect_devices(self):
        self._save_channels()
        if self.manager is not None and self._own_manager:
            self.manager.close()
        self.manager = iod.DeviceManager(self.configs)
        self._own_manager = True
        self.connect_btn.setText("Disconnect")
        self._refresh_status(force=True)
        self.timer.start()

    def disconnect_devices(self):
        self.timer.stop()
        if self.manager is not None and self._own_manager:
            self.manager.close()
        self.manager = None
        self._own_manager = False
        self.connect_btn.setText("Connect")
        self.status.setRowCount(0)
        self.conn_lbl.setText("Not connected")

    def _poll(self):
        if self.manager is None:
            return
        self.manager.read_inputs()
        self._refresh_status()

    def _refresh_status(self, force=False):
        m = self.manager
        if m is None:
            return
        rows = m.status()
        keys = [(d, c, k) for d, c, k, _v in rows]
        if force or keys != self._status_keys:
            self._status_keys = keys
            self.status.setRowCount(len(rows))
            for r, (d, c, k, _v) in enumerate(rows):
                for col, text in enumerate((d, c, iod.CHANNEL_KINDS.get(k, k))):
                    self.status.setItem(r, col, QTableWidgetItem(text))
                self.status.setItem(r, 3, QTableWidgetItem(""))
                dev = m.devices.get(d)
                if k in iod.OUTPUT_KINDS or (k == "input" and isinstance(dev, iod.VirtualDevice)):
                    b = QPushButton("Toggle" if k in iod.OUTPUT_KINDS else "Simulate")
                    b.clicked.connect(lambda _=False, d=d, c=c, k=k: self.toggle_channel(d, c, k))
                    self.status.setCellWidget(r, 4, b)
                else:
                    self.status.removeCellWidget(r, 4)
        for r, (_d, _c, k, v) in enumerate(rows):
            it = self.status.item(r, 3)
            text = ("ON" if v else "off") if k in ("input", "output") else _txt(round(v, 3) if isinstance(v, float)
                                                                               else v)
            if it.text() != text:
                it.setText(text)
                it.setForeground(QBrush(QColor("#15803d" if v else "#6b7280")))
        errs = m.errors
        self.conn_lbl.setText("; ".join(errs[-3:]) if errs else f"Connected: {len(m.devices)} device(s)")
        self.conn_lbl.setStyleSheet("color:#dc2626" if errs else "color:#15803d")

    def toggle_channel(self, device, channel, kind):
        m = self.manager
        if m is None:
            return
        dev = m.devices.get(device)
        if kind in iod.OUTPUT_KINDS:
            m.set_output(device, channel, 0 if dev.outputs.get(channel) else 1)
        else:
            m.set_input(device, channel, 0 if dev.inputs.get(channel) else 1)
        self._poll()

    def test_selected(self):
        if self.manager is None:
            self.connect_devices()
        m = self.manager
        r = self.status.currentRow()
        c = self._cur()
        if c is not None and c.get("type") == "audio" and m.has(c.get("name")):
            m.audio(c["name"], "tone", frequency=1000, duration=0.5, volume=0.5)
            self.conn_lbl.setText(f"Played a 1 kHz tone on {c['name']}")
            return
        if 0 <= r < len(self._status_keys):
            d, ch, k = self._status_keys[r]
        else:
            outs = [key for key in self._status_keys if key[2] in iod.OUTPUT_KINDS]
            if not outs:
                self.conn_lbl.setText("Select an output to test")
                return
            d, ch, k = outs[0]
        if k not in iod.OUTPUT_KINDS:
            self.conn_lbl.setText(f"{ch} is an input: press the lever / break the beam to see it change")
            return
        m.set_output(d, ch, 1)
        QTimer.singleShot(500, lambda: (m.set_output(d, ch, 0), self._refresh_status()))
        self._refresh_status()

    # ------------------------------------------------------------------ close
    def accept(self):
        self._save_device()
        self._save_channels()
        names = [c.get("name") for c in self.configs]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            QMessageBox.warning(self, "I/O devices", f"Device names must be unique: {', '.join(sorted(dup))}")
            return
        if self.configs != list(self.project.io_devices or []):
            self.project.io_devices[:] = copy.deepcopy(self.configs)
            self.changed.emit()
        self.disconnect_devices()
        super().accept()

    def reject(self):
        self.disconnect_devices()
        super().reject()

    def closeEvent(self, e):
        self.disconnect_devices()
        super().closeEvent(e)

    def sizeHint(self):
        return QSize(860, 640)
