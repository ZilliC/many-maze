"""Animals page: subjects table, treatment groups (with colours) and custom animal fields."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ...core import workflow as wf
from ...core.project import Animal, Group
from ..widgets import error_box
from .base import Page

PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#64748b"]
SEXES = ["", "Male", "Female"]
ID_KEYS = ("id", "animal", "animal id", "animal_id", "subject", "subject id")


def swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def unique_id(base: str, taken: set[str]) -> str:
    """`base` if free, else base with its trailing number incremented (or "-2", "-3", ...)."""
    if base and base not in taken:
        return base
    m = re.match(r"^(.*?)(\d+)$", base)
    if m:
        stem, num = m.group(1), m.group(2)
        n = int(num) + 1
        while f"{stem}{n:0{len(num)}d}" in taken:
            n += 1
        return f"{stem}{n:0{len(num)}d}"
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


class _ComboDelegate(QStyledItemDelegate):
    """Editable combo box editor whose choices come from a callable."""

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)
        cb.addItems(self.choices())
        cb.setInsertPolicy(QComboBox.NoInsert)
        return cb

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data(Qt.EditRole) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip(), Qt.EditRole)


class _AddSeveralDialog(QDialog):
    def __init__(self, groups: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add several animals")
        f = QFormLayout(self)
        self.prefix = QLineEdit("M")
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setValue(10)
        self.start = QSpinBox()
        self.start.setRange(0, 100000)
        self.start.setValue(1)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(2)
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + groups)
        self.preview = QLabel()
        self.preview.setStyleSheet("color:palette(mid)")
        for w in (self.prefix,):
            w.textChanged.connect(self._update)
        for w in (self.count, self.start, self.digits):
            w.valueChanged.connect(self._update)
        f.addRow("ID prefix", self.prefix)
        f.addRow("Number of animals", self.count)
        f.addRow("First number", self.start)
        f.addRow("Digits", self.digits)
        f.addRow("Group", self.group)
        f.addRow("", self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._update()

    def ids(self) -> list[str]:
        p, s, d = self.prefix.text().strip(), self.start.value(), self.digits.value()
        return [f"{p}{s + i:0{d}d}" for i in range(self.count.value())]

    def _update(self, *_):
        ids = self.ids()
        self.preview.setText(f"{ids[0]} … {ids[-1]}" if len(ids) > 1 else ids[0])


class DoseDialog(QDialog):
    """Injection volume = weight × dose / concentration, written to the animals' "Volume (mL)" field."""

    def __init__(self, project, n_selected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dose calculator")
        ds = wf.dose_settings(project)
        f = QFormLayout(self)
        f.addRow(QLabel("Volume (mL) = weight (g) / 1000 × dose (mg/kg) / concentration (mg/mL).<br>"
                        f"Animals with their own “{wf.DOSE_FIELD}” field use that dose instead of the default."))
        self.weight = QComboBox()
        self.weight.setEditable(True)
        fields = [x for x in project.animal_fields if x not in (wf.VOLUME_FIELD, wf.DOSE_FIELD)]
        self.weight.addItems(fields or [wf.WEIGHT_FIELD])
        self.weight.setCurrentText(ds["weight_field"] if ds["weight_field"] in fields or not fields
                                   else next((x for x in fields if "weight" in x.lower()), fields[0]))
        self.dose = QDoubleSpinBox()
        self.dose.setRange(0, 1e6)
        self.dose.setDecimals(3)
        self.dose.setSuffix(" mg/kg")
        self.dose.setValue(float(ds["dose_mg_kg"]))
        self.conc = QDoubleSpinBox()
        self.conc.setRange(0.0001, 1e6)
        self.conc.setDecimals(4)
        self.conc.setSuffix(" mg/mL")
        self.conc.setValue(float(ds["conc_mg_ml"]))
        self.only_sel = QCheckBox(f"Only the {n_selected} selected animal{'s' if n_selected != 1 else ''}")
        self.only_sel.setEnabled(n_selected > 0)
        self.only_sel.setChecked(n_selected > 0)
        f.addRow("Weight column", self.weight)
        f.addRow("Default dose", self.dose)
        f.addRow("Concentration", self.conc)
        f.addRow("", self.only_sel)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Calculate volumes")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def settings(self) -> dict:
        return {"weight_field": self.weight.currentText().strip() or wf.WEIGHT_FIELD,
                "dose_mg_kg": self.dose.value(), "conc_mg_ml": self.conc.value()}


class CriteriaDialog(QDialog):
    """Result of evaluating the training criteria; Apply retires animals / completes stages."""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Training criteria")
        lay = QVBoxLayout(self)
        rows = report["rows"]
        lay.addWidget(QLabel(f"{len(rows)} animal × criterion evaluation{'s' if len(rows) != 1 else ''}. "
                             f"<b>{sum(len(v) for v in report['completed'].values())}</b> stage(s) completed, "
                             f"<b>{len(report['retire'])}</b> animal(s) to retire."))
        t = QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Animal", "Criterion", "Trials", "Met at trial", "Outcome"])
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for r, row in enumerate(rows):
            outcome = ("criterion met" if row["met"] else "failed → retire" if row["failed"] and row["action"] == "retire"
                       else "failed" if row["failed"] else "in progress")
            for c, v in enumerate((row["animal"], row["criterion"], str(row["trials"]),
                                   str(row["met_at_trial"] or "–"), outcome)):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setForeground(QColor("#16a34a" if row["met"] else "#dc2626" if row["failed"] else "#475569"))
                t.setItem(r, c, it)
        lay.addWidget(t, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Apply).setToolTip("Retire failing animals (their pending tests are skipped) and "
                                                     "skip the remaining trials of completed stages")
        bb.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(760, 420)


class AnimalsPage(Page):
    title = "Animals"

    def __init__(self, main):
        super().__init__(main)
        self._loading = False

        # ---- left: animals table ------------------------------------------------
        bar = QHBoxLayout()

        bar2 = QHBoxLayout()

        def button(text, fn, tip="", row=bar):
            b = QPushButton(text)
            b.clicked.connect(fn)
            if tip:
                b.setToolTip(tip)
            row.addWidget(b)
            return b

        button("Add animal", self.add_animal_interactive)
        button("Add several…", self._add_several_dialog, "Create a numbered series of animals (e.g. M01…M12)")
        button("Duplicate", self.duplicate_selected, "Copy the selected animals (group, sex and fields)")
        button("Delete", self.delete_selected_interactive)
        bar.addSpacing(16)
        button("Import CSV…", self._import_dialog, "Columns: ID, Group, Sex; any other column becomes a field")
        button("Export CSV…", self._export_dialog)
        self.retire_btn = button("Retire", lambda: self.toggle_retire_selected(),
                                 "Withdraw the selected animals from the experiment (their pending tests are skipped "
                                 "and new schedules leave them out), or reinstate retired animals", bar2)
        button("Training criteria…", self.criteria_dialog,
               "Evaluate the training criteria (Experiment page) against the results: complete stages and retire "
               "animals that failed", bar2)
        button("Dose calculator…", self.dose_dialog, "Injection volume from body weight, dose and concentration",
               bar2)
        bar2.addStretch()
        bar.addStretch()
        self.summary = QLabel()
        self.summary.setStyleSheet("color:palette(mid)")
        bar.addWidget(self.summary)

        self.table = QTableWidget(0, 0)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._update_retire_btn)
        self.table.setItemDelegateForColumn(1, _ComboDelegate(self._group_names, self.table))
        self.table.setItemDelegateForColumn(2, _ComboDelegate(lambda: SEXES, self.table))
        QShortcut(QKeySequence.Delete, self.table, self.delete_selected_interactive)

        left = QVBoxLayout()
        left.addLayout(bar)
        left.addWidget(self.table, 1)
        left.addLayout(bar2)
        hint = QLabel("Double-click a cell to edit. Typing a new group name in the Group column creates the group. "
                      "Renaming an animal updates its tests.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:palette(mid)")
        left.addWidget(hint)

        # ---- right: groups and fields -----------------------------------------
        gb = QGroupBox("Treatment groups")
        gl = QVBoxLayout(gb)
        self.groups_list = QListWidget()
        self.groups_list.setStyleSheet("QListWidget::item{padding:4px 2px}")
        self.groups_list.itemDoubleClicked.connect(lambda _it: self._rename_group_dialog())
        gl.addWidget(self.groups_list)
        gbar = QHBoxLayout()
        self._group_btns = []
        for text, fn in (("Add", self._add_group_dialog), ("Rename…", self._rename_group_dialog),
                         ("Colour…", self._color_group_dialog), ("Delete", self._delete_group_dialog)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            gbar.addWidget(b)
            self._group_btns.append(b)
        gl.addLayout(gbar)
        self.blind_lbl = QLabel("Blind testing is on: groups are shown as codes and cannot be edited. Turn it off "
                                "on the Experiment page.")
        self.blind_lbl.setWordWrap(True)
        self.blind_lbl.setStyleSheet("color:#9333ea")
        self.blind_lbl.hide()
        gl.addWidget(self.blind_lbl)

        fb = QGroupBox("Custom fields (extra columns)")
        fl = QVBoxLayout(fb)
        self.fields_list = QListWidget()
        self.fields_list.setStyleSheet("QListWidget::item{padding:4px 2px}")
        self.fields_list.itemDoubleClicked.connect(lambda _it: self._rename_field_dialog())
        fl.addWidget(self.fields_list)
        fbar = QHBoxLayout()
        for text, fn in (("Add…", self._add_field_dialog), ("Rename…", self._rename_field_dialog),
                         ("Remove", self._remove_field_dialog)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            fbar.addWidget(b)
        fl.addLayout(fbar)
        fnote = QLabel("Fields such as body weight, genotype or date of birth are exported with the results "
                       "and can be used to filter tests.")
        fnote.setWordWrap(True)
        fnote.setStyleSheet("color:palette(mid)")
        fl.addWidget(fnote)

        right = QVBoxLayout()
        right.addWidget(gb, 1)
        right.addWidget(fb, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(330)
        right.setContentsMargins(0, 0, 0, 0)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(rw)

    # ------------------------------------------------------------------ refresh
    def set_project(self, project):
        self.refresh()

    def on_show(self):
        self.refresh()

    def _group_names(self) -> list[str]:
        return [g.name for g in self.project.groups] if self.project else []

    def _test_counts(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for t in self.project.tests:
            for aid in [t.animal_id] + list(t.extra_animals):
                out.setdefault(aid, []).append(t.id)
        return out

    def _columns(self) -> list[str]:
        return ["ID", "Group", "Sex"] + list(self.project.animal_fields) + ["Tests", "Status"]

    def refresh(self):
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        p = self.project
        if p is None:
            self.table.setColumnCount(0)
            self.groups_list.clear()
            self.fields_list.clear()
            self.summary.setText("")
            self._loading = False
            return
        cols = self._columns()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        counts = self._test_counts()
        self.table.setRowCount(len(p.animals))
        for r, a in enumerate(p.animals):
            self._fill_row(r, a, counts)
        hh = self.table.horizontalHeader()
        for c in range(len(cols)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, 130 if c < 3 else 120)
        self.table.setColumnWidth(len(cols) - 2, 70)
        self.table.setColumnWidth(len(cols) - 1, 90)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(len(cols) - 3, QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        self._loading = False
        self._refresh_side(counts)

    def _fill_row(self, r: int, a: Animal, counts: dict):
        p = self.project
        it = QTableWidgetItem(a.id)
        it.setData(Qt.UserRole, a)
        self.table.setItem(r, 0, it)
        g = QTableWidgetItem(wf.display_group(p, a.group))
        if a.group:
            g.setIcon(swatch(wf.display_color(p, a.group)))
        if p.blind:
            g.setFlags(g.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 1, g)
        self.table.setItem(r, 2, QTableWidgetItem(a.sex))
        for i, f in enumerate(p.animal_fields):
            self.table.setItem(r, 3 + i, QTableWidgetItem(str(a.fields.get(f, ""))))
        ids = counts.get(a.id, [])
        n = QTableWidgetItem()
        n.setData(Qt.DisplayRole, len(ids))
        n.setFlags(n.flags() & ~Qt.ItemIsEditable)
        n.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if ids:
            n.setToolTip("Tests: " + ", ".join(str(i) for i in ids))
        self.table.setItem(r, 3 + len(p.animal_fields), n)
        st = QTableWidgetItem("retired" if a.retired else "active")
        st.setFlags(st.flags() & ~Qt.ItemIsEditable)
        st.setForeground(QColor("#dc2626" if a.retired else "#16a34a"))
        if a.retired and a.retired_reason:
            st.setToolTip(a.retired_reason)
        self.table.setItem(r, 4 + len(p.animal_fields), st)

    def _refresh_side(self, counts=None):
        p = self.project
        counts = counts if counts is not None else self._test_counts()
        cur_g = self._current_group()
        self.groups_list.clear()
        for g in p.groups:
            n = sum(1 for a in p.animals if a.group == g.name)
            it = QListWidgetItem(swatch(wf.display_color(p, g.name), 14),
                                 f"{wf.display_group(p, g.name)}    ({n} animal{'s' if n != 1 else ''})")
            it.setData(Qt.UserRole, g.name)
            self.groups_list.addItem(it)
            if g.name == cur_g:
                self.groups_list.setCurrentItem(it)
        cur_f = self.fields_list.currentItem().text() if self.fields_list.currentItem() else None
        self.fields_list.clear()
        for f in p.animal_fields:
            it = QListWidgetItem(f)
            self.fields_list.addItem(it)
            if f == cur_f:
                self.fields_list.setCurrentItem(it)
        n_tests = sum(len(v) for k, v in counts.items() if p.get_animal(k))
        n_ret = sum(1 for a in p.animals if a.retired)
        self.summary.setText(f"{len(p.animals)} animals · {len(p.groups)} groups · {n_tests} tests"
                             + (f" · {n_ret} retired" if n_ret else ""))
        for b in self._group_btns:
            b.setEnabled(not p.blind)
        self.blind_lbl.setVisible(p.blind)
        self._update_retire_btn()

    def _changed(self):
        self.main.mark_dirty()

    # ------------------------------------------------------------------ animals
    def _taken(self) -> set[str]:
        return {a.id for a in self.project.animals}

    def _row_of(self, a: Animal) -> int:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and it.data(Qt.UserRole) is a:
                return r
        return -1

    def add_animal(self, aid: str | None = None, group: str = "", sex: str = "", fields: dict | None = None) -> Animal:
        p = self.project
        last = p.animals[-1].id if p.animals else "A0"
        aid = unique_id((aid or "").strip() or last, self._taken())
        if group:
            self.ensure_group(group)
        a = Animal(aid, group, sex, dict(fields or {}))
        p.animals.append(a)
        self._changed()
        self.refresh()
        return a

    def add_animal_interactive(self):
        if self.project is None:
            return
        a = self.add_animal(group=self._current_group() or "")
        r = self._row_of(a)
        if r >= 0:
            self.table.setCurrentCell(r, 0)
            self.table.editItem(self.table.item(r, 0))

    def add_several(self, prefix: str, count: int, group: str = "", start: int = 1, digits: int = 2) -> list[Animal]:
        ids = [f"{prefix}{start + i:0{digits}d}" for i in range(count)]
        return self._add_ids(ids, group)

    def _add_ids(self, ids, group) -> list[Animal]:
        p = self.project
        taken = self._taken()
        if group:
            self.ensure_group(group)
        out = []
        for aid in ids:
            if aid in taken:
                continue
            taken.add(aid)
            a = Animal(aid, group)
            p.animals.append(a)
            out.append(a)
        skipped = len(ids) - len(out)
        self._changed()
        self.refresh()
        self.main.status(f"Added {len(out)} animals" + (f" ({skipped} existing IDs skipped)" if skipped else ""))
        return out

    def _add_several_dialog(self):
        if self.project is None:
            return
        dlg = _AddSeveralDialog(self._group_names(), self)
        if dlg.exec() == QDialog.Accepted:
            self._add_ids(dlg.ids(), dlg.group.currentText().strip())

    def selected_animals(self) -> list[Animal]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in rows:
            it = self.table.item(r, 0)
            if it is not None:
                out.append(it.data(Qt.UserRole))
        return out

    def duplicate_selected(self) -> list[Animal]:
        if self.project is None:
            return []
        taken = self._taken()
        out = []
        for a in self.selected_animals():
            nid = unique_id(a.id, taken)
            taken.add(nid)
            b = Animal(nid, a.group, a.sex, dict(a.fields))
            self.project.animals.insert(self.project.animals.index(a) + 1 + len(out), b)
            out.append(b)
        if out:
            self._changed()
            self.refresh()
        return out

    def delete_animals(self, animals: list[Animal]):
        ids = {a.id for a in animals}
        self.project.animals = [a for a in self.project.animals if a.id not in ids]
        self._changed()
        self.refresh()

    def delete_selected_interactive(self):
        if self.project is None:
            return
        sel = self.selected_animals()
        if not sel:
            return
        counts = self._test_counts()
        n_tests = sum(len(counts.get(a.id, [])) for a in sel)
        msg = f"Delete {len(sel)} animal{'s' if len(sel) > 1 else ''}?"
        if n_tests:
            msg += (f"\n\n{n_tests} test{'s' if n_tests > 1 else ''} refer to "
                    f"{'these animals' if len(sel) > 1 else 'this animal'}; they are kept but will have no "
                    "group or animal details.")
        if QMessageBox.question(self, "Delete animals", msg) == QMessageBox.Yes:
            self.delete_animals(sel)

    def rename_animal(self, a: Animal, new_id: str) -> bool:
        new_id = new_id.strip()
        if not new_id or new_id == a.id:
            return False
        if new_id in self._taken():
            return False
        old = a.id
        a.id = new_id
        for t in self.project.tests:
            if t.animal_id == old:
                t.animal_id = new_id
            if old in t.extra_animals:
                t.extra_animals = [new_id if x == old else x for x in t.extra_animals]
        self._changed()
        return True

    def _cell_changed(self, item: QTableWidgetItem):
        if self._loading or self.project is None:
            return
        r, c = item.row(), item.column()
        idit = self.table.item(r, 0)
        if idit is None:
            return
        a: Animal = idit.data(Qt.UserRole)
        text = item.text().strip()
        p = self.project
        self._loading = True
        try:
            if c == 0:
                if text != a.id:
                    if not text:
                        self.main.status("An animal ID cannot be empty")
                        item.setText(a.id)
                    elif text in self._taken():
                        item.setText(a.id)
                        QMessageBox.warning(self, "Animals", f"There is already an animal with ID “{text}”. "
                                                             "Animal IDs must be unique.")
                    else:
                        self.rename_animal(a, text)
            elif c == 1:
                if p.blind:
                    item.setText(wf.display_group(p, a.group))
                    return
                if text and text not in self._group_names():
                    self.ensure_group(text)
                a.group = text
                item.setIcon(swatch(p.group_color(text)) if text else QIcon())
                self._changed()
                self._refresh_side()
            elif c == 2:
                a.sex = text
                self._changed()
            elif 3 <= c < 3 + len(p.animal_fields):
                a.fields[p.animal_fields[c - 3]] = text
                self._changed()
        finally:
            self._loading = False

    # ------------------------------------------------------------------ retirement, criteria, doses
    def _update_retire_btn(self):
        sel = self.selected_animals() if self.project is not None else []
        self.retire_btn.setEnabled(bool(sel))
        self.retire_btn.setText("Reinstate" if sel and all(a.retired for a in sel) else "Retire")

    def toggle_retire_selected(self, reason: str | None = None):
        sel = self.selected_animals()
        if not sel:
            return
        if all(a.retired for a in sel):
            n = sum(wf.reinstate_animal(self.project, a) for a in sel)
            msg = f"Reinstated {len(sel)} animal{'s' if len(sel) != 1 else ''} ({n} skipped tests resumed)."
        else:
            if reason is None:
                reason, ok = QInputDialog.getText(self, "Retire animals", "Reason (optional):")
                if not ok:
                    return
            n = sum(wf.retire_animal(self.project, a, reason.strip()) for a in sel if not a.retired)
            msg = f"Retired {len(sel)} animal{'s' if len(sel) != 1 else ''} ({n} pending tests skipped)."
        self._changed()
        self.refresh()
        self.main.status(msg)

    def evaluate_criteria(self) -> dict:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return wf.evaluate_criteria(self.project)
        finally:
            QApplication.restoreOverrideCursor()

    def criteria_dialog(self):
        p = self.project
        if p is None:
            return None
        if not p.training_criteria:
            QMessageBox.information(self, "Training criteria", "No training criteria are defined. Add them on the "
                                    "Experiment page (Training criteria).")
            return None
        try:
            rep = self.evaluate_criteria()
        except Exception as e:
            error_box(self, "Training criteria", e)
            return None
        if CriteriaDialog(rep, self).exec() != QDialog.Accepted:
            return None
        return self.apply_criteria(rep)

    def apply_criteria(self, report: dict | None = None) -> dict:
        res = wf.apply_criteria(self.project, report)
        self._changed()
        self.refresh()
        self.main.status(f"Training criteria: {res['completed']} stage(s) completed, {len(res['retired'])} animal(s) "
                         f"retired, {res['skipped']} test(s) skipped.")
        return res

    def dose_dialog(self):
        p = self.project
        if p is None:
            return None
        sel = self.selected_animals()
        dlg = DoseDialog(p, len(sel), self)
        if dlg.exec() != QDialog.Accepted:
            return None
        return self.calculate_doses(dlg.settings(), sel if dlg.only_sel.isChecked() else None)

    def calculate_doses(self, settings: dict | None = None, animals: list[Animal] | None = None) -> dict:
        p = self.project
        ds = wf.dose_settings(p)
        if settings:
            ds.update(settings)
        if ds["weight_field"] not in p.animal_fields:
            p.animal_fields.append(ds["weight_field"])
        vols = wf.compute_doses(p, animals)
        self._changed()
        self.refresh()
        missing = [k for k, v in vols.items() if v is None]
        self.main.status(f"Injection volumes calculated for {len(vols) - len(missing)} animal(s)"
                         + (f"; no valid weight for {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}"
                            if missing else "") + ".")
        return vols

    # ------------------------------------------------------------------ groups
    def _current_group(self) -> str | None:
        it = self.groups_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _next_color(self) -> str:
        used = {g.color.lower() for g in self.project.groups}
        free = [c for c in PALETTE if c not in used]
        return free[0] if free else PALETTE[len(self.project.groups) % len(PALETTE)]

    def ensure_group(self, name: str) -> Group:
        g = next((g for g in self.project.groups if g.name == name), None)
        if g is None:
            g = Group(name, self._next_color())
            self.project.groups.append(g)
            self._changed()
        return g

    def add_group(self, name: str, color: str | None = None) -> Group | None:
        name = name.strip()
        if not name or name in self._group_names():
            return None
        g = Group(name, color or self._next_color())
        self.project.groups.append(g)
        self._changed()
        self.refresh()
        return g

    def rename_group(self, old: str, new: str) -> bool:
        new = new.strip()
        g = next((g for g in self.project.groups if g.name == old), None)
        if g is None or not new or new == old or new in self._group_names():
            return False
        g.name = new
        for a in self.project.animals:
            if a.group == old:
                a.group = new
        self._changed()
        self.refresh()
        return True

    def set_group_color(self, name: str, color: str):
        g = next((g for g in self.project.groups if g.name == name), None)
        if g is not None:
            g.color = color
            self._changed()
            self.refresh()

    def delete_group(self, name: str):
        self.project.groups = [g for g in self.project.groups if g.name != name]
        for a in self.project.animals:
            if a.group == name:
                a.group = ""
        self._changed()
        self.refresh()

    def _add_group_dialog(self):
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "Add group", "Group name:",
                                        text=f"Group {len(self.project.groups) + 1}")
        if ok and name.strip():
            if self.add_group(name) is None:
                QMessageBox.warning(self, "Add group", f"A group named “{name.strip()}” already exists.")

    def _rename_group_dialog(self):
        old = self._current_group()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename group", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            if not self.rename_group(old, new):
                QMessageBox.warning(self, "Rename group", f"A group named “{new.strip()}” already exists.")

    def _color_group_dialog(self):
        name = self._current_group()
        if not name:
            return
        c = QColorDialog.getColor(QColor(self.project.group_color(name)), self, f"Colour of {name}")
        if c.isValid():
            self.set_group_color(name, c.name())

    def _delete_group_dialog(self):
        name = self._current_group()
        if not name:
            return
        n = sum(1 for a in self.project.animals if a.group == name)
        msg = f"Delete group “{name}”?" + (f"\n\n{n} animals will be left without a group." if n else "")
        if QMessageBox.question(self, "Delete group", msg) == QMessageBox.Yes:
            self.delete_group(name)

    # ------------------------------------------------------------------ fields
    def add_field(self, name: str) -> bool:
        name = name.strip()
        if not name or name in self.project.animal_fields or name.lower() in ("id", "group", "sex", "tests"):
            return False
        self.project.animal_fields.append(name)
        self._changed()
        self.refresh()
        return True

    def remove_field(self, name: str):
        if name not in self.project.animal_fields:
            return
        self.project.animal_fields.remove(name)
        for a in self.project.animals:
            a.fields.pop(name, None)
        self._changed()
        self.refresh()

    def rename_field(self, old: str, new: str) -> bool:
        new = new.strip()
        f = self.project.animal_fields
        if old not in f or not new or new in f or new.lower() in ("id", "group", "sex", "tests"):
            return False
        f[f.index(old)] = new
        for a in self.project.animals:
            if old in a.fields:
                a.fields[new] = a.fields.pop(old)
        self._changed()
        self.refresh()
        return True

    def _add_field_dialog(self):
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "Add field", "Field (column) name, e.g. Weight (g), Genotype:")
        if ok and name.strip() and not self.add_field(name):
            QMessageBox.warning(self, "Add field", f"“{name.strip()}” is already a column.")

    def _rename_field_dialog(self):
        it = self.fields_list.currentItem()
        if it is None:
            return
        new, ok = QInputDialog.getText(self, "Rename field", "New name:", text=it.text())
        if ok and new.strip() and new.strip() != it.text() and not self.rename_field(it.text(), new):
            QMessageBox.warning(self, "Rename field", f"“{new.strip()}” is already a column.")

    def _remove_field_dialog(self):
        it = self.fields_list.currentItem()
        if it is None:
            return
        if QMessageBox.question(self, "Remove field",
                                f"Remove the column “{it.text()}” and its values for every animal?") == QMessageBox.Yes:
            self.remove_field(it.text())

    # ------------------------------------------------------------------ CSV
    def import_csv(self, path: str) -> tuple[int, int]:
        """Import animals from a CSV file. Returns (added, updated)."""
        text = Path(path).read_text(encoding="utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(text.splitlines(), dialect))
        if not rows:
            return 0, 0
        header = [h.strip() for h in rows[0]]
        low = [h.lower() for h in header]
        id_col = next((i for i, h in enumerate(low) if h in ID_KEYS), None)
        if id_col is None:
            raise ValueError("The CSV file needs an “ID” column (header row: ID, Group, Sex, …)")
        g_col = next((i for i, h in enumerate(low) if h in ("group", "treatment", "treatment group")), None)
        s_col = next((i for i, h in enumerate(low) if h in ("sex", "gender")), None)
        f_cols = [(i, h) for i, h in enumerate(header) if i not in (id_col, g_col, s_col) and h
                  and h.lower() not in ("tests",)]
        p = self.project
        for _, h in f_cols:
            if h not in p.animal_fields:
                p.animal_fields.append(h)
        added = updated = 0
        for row in rows[1:]:
            if len(row) <= id_col or not row[id_col].strip():
                continue
            cell = lambda i: row[i].strip() if i is not None and i < len(row) else ""  # noqa: E731
            aid = cell(id_col)
            a = p.get_animal(aid)
            if a is None:
                a = Animal(aid)
                p.animals.append(a)
                added += 1
            else:
                updated += 1
            if g_col is not None:
                a.group = cell(g_col)
                if a.group:
                    self.ensure_group(a.group)
            if s_col is not None:
                a.sex = cell(s_col)
            for i, h in f_cols:
                a.fields[h] = cell(i)
        self._changed()
        self.refresh()
        return added, updated

    def export_csv(self, path: str):
        p = self.project
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ID", "Group", "Sex"] + list(p.animal_fields))
            for a in p.animals:
                w.writerow([a.id, a.group, a.sex] + [a.fields.get(f, "") for f in p.animal_fields])

    def _import_dialog(self):
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import animals", str(self.project.path or Path.home()),
                                              "CSV files (*.csv *.txt);;All files (*)")
        if not path:
            return
        try:
            added, updated = self.import_csv(path)
        except Exception as e:
            error_box(self, "Import animals", e)
            return
        self.main.status(f"Imported {added} new and updated {updated} existing animals from {Path(path).name}")

    def _export_dialog(self):
        if self.project is None:
            return
        default = str(self.project.exports_dir() / "animals.csv") if self.project.path else "animals.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export animals", default, "CSV files (*.csv)")
        if not path:
            return
        try:
            self.export_csv(path)
        except Exception as e:
            error_box(self, "Export animals", e)
            return
        self.main.status(f"Exported {len(self.project.animals)} animals to {path}")
