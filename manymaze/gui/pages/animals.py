"""Experiment tab (as in ANY-maze): the Animals sheet (Animal, Animal ID, Status, Treatment, custom fields, Sex) and
the Treatments sheet (name, code, colour, number of animals), with blind coding, retirement, doses and criteria."""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QColorDialog, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QStackedWidget,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...core import export
from ...core import workflow as wf
from ...core.project import Animal
from ...core.workflow import treatment_code, treatment_text  # noqa: F401 (testview imports it from here)
from .. import ribbon, theme
from ..icons import icon
from ..ribbon import action as ribbon_action  # noqa: F401 (testview imports it from here)
from ..widgets import error_box
from .animal_dialogs import AddSeveralDialog, CriteriaDialog, DoseDialog
from .base import Page

SEXES = ["", "Male", "Female"]
STATUSES = ["Normal", "Retired"]
COMBO_KINDS = ("status", "treatment", "sex")
ROW_H = 32
MUTED_ROW = "#9ca3af"


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


def _strip_code(project, text: str) -> str:
    """"A - Saline" → "Saline" (the treatment name typed or picked in a cell)."""
    if " - " in text:
        code, rest = text.split(" - ", 1)
        if rest in [g.name for g in project.groups] and treatment_code(project, rest) == code:
            return rest
    return text


class _SheetDelegate(QStyledItemDelegate):
    """Cells of the Animals sheet: drop-down editors (with a ▾ drawn in the cell, as in ANY-maze) for Status,
    Treatment and Sex, and treatment names shown with their code ("A - Saline")."""

    def __init__(self, page):
        super().__init__(page.table)
        self.page = page

    def _kind(self, index):
        return self.page._col_kind(index.column())

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        p = self.page.project
        if self._kind(index) == "treatment" and p is not None and not p.blind:
            option.text = treatment_text(p, index.data(Qt.EditRole) or "")

    def _arrow_rect(self, rect: QRect) -> QRect:
        return QRect(rect.right() - 20, rect.top() + (rect.height() - 10) // 2, 10, 10)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if self._kind(index) in COMBO_KINDS and index.flags() & Qt.ItemIsEditable:
            icon("chevron_down").paint(painter, self._arrow_rect(option.rect))

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.MouseButtonRelease and self._kind(index) in COMBO_KINDS
                and index.flags() & Qt.ItemIsEditable and event.position().x() >= option.rect.right() - 26):
            self.page.table.edit(index)  # a click on ▾ opens the list straight away
            return True
        return super().editorEvent(event, model, option, index)

    def createEditor(self, parent, option, index):
        kind = self._kind(index)
        if kind not in COMBO_KINDS:
            return super().createEditor(parent, option, index)
        p = self.page.project
        cb = QComboBox(parent)
        if kind == "status":
            cb.addItems(STATUSES)
        else:
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            if kind == "treatment":
                cb.addItem("", "")
                for g in p.groups:
                    cb.addItem(swatch(wf.display_color(p, g.name)), treatment_text(p, g.name), g.name)
            else:
                cb.addItems(SEXES)
        cb.activated.connect(lambda _i, cb=cb: (self.commitData.emit(cb), self.closeEditor.emit(cb)))
        QTimer.singleShot(0, cb.showPopup)
        return cb

    def setEditorData(self, editor, index):
        if not isinstance(editor, QComboBox):
            return super().setEditorData(editor, index)
        v = index.data(Qt.EditRole) or ""
        i = editor.findData(v) if self._kind(index) == "treatment" else editor.findText(v)
        if i >= 0:
            editor.setCurrentIndex(i)
        elif editor.isEditable():
            editor.setEditText(v)

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QComboBox):
            return super().setModelData(editor, model, index)
        text = editor.currentText().strip()
        if self._kind(index) == "treatment":
            i = editor.findText(text)
            text = (editor.itemData(i) or "") if i >= 0 else _strip_code(self.page.project, text)
        model.setData(index, text, Qt.EditRole)


class AnimalsPage(Page):
    """The Experiment tab: Animals and Treatments sheets with the ANY-maze "Experiment" ribbon."""

    title = "Animals"

    def __init__(self, main):
        super().__init__(main)
        self._loading = False
        self.view = "animals"
        self._cols: list[tuple[str, str]] = []  # (kind, field name) per column of the Animals sheet

        # ---- ribbon actions ------------------------------------------------------------
        def act(text, ic, fn, tip="", checkable=False, large=True):
            return ribbon.action(self, text, ic, fn, tip, checkable, large)

        self.a_view_treat = act("View treatments", "treatment", lambda on: on and self.set_view("treatments"),
                                "Show the Treatments sheet: treatment names, codes and colours", True)
        self.a_view_animals = act("View animals", "animal", lambda on: on and self.set_view("animals"),
                                  "Show the Animals sheet", True)
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for a in (self.a_view_treat, self.a_view_animals):
            grp.addAction(a)
        self.a_view_animals.blockSignals(True)
        self.a_view_animals.setChecked(True)
        self.a_view_animals.blockSignals(False)
        self.a_add_animals = act("Add animals", "animals_add", self._add_several_dialog,
                                 "Add a numbered series of animals (e.g. M01…M12)")
        self.a_del_animals = act("Delete animals", "animal_delete", self.delete_selected_interactive,
                                 "Delete the selected animals")
        self.a_reveal = act("Reveal treatment coding", "eye", self._reveal_toggled,
                            "On: the treatments are visible. Off: blind testing — treatments are shown only as "
                            "codes while testing and scoring", True)
        self.a_import_animals = act("Import animals", "import", lambda: self.main.import_table("animals"),
                                    "Import animals (ID, treatment, sex and other columns) from a spreadsheet saved "
                                    "by ANY-maze or other software")
        self.a_import_tests = act("Import tests", "import_tests", lambda: self.main.import_table("tests"),
                                  "Import a test schedule (animal, stage, trial, apparatus, video) from a spreadsheet")
        self.retire_btn = act("Retire", "retire", lambda: self.toggle_retire_selected(),
                              "Withdraw the selected animals from the experiment (their pending tests are skipped "
                              "and new schedules leave them out), or reinstate retired animals")
        self.a_dose = act("Dose calculator", "calculator", self.dose_dialog,
                          "Injection volume from body weight, dose and concentration")
        self.a_criteria = act("Training criteria", "criteria", self.criteria_dialog,
                              "Evaluate the training criteria (Protocol) against the results: complete stages and "
                              "retire animals that failed")
        self.a_export = act("Export CSV", "export", self._export_dialog, "Save the animal list as a CSV file")
        self.a_add_one = act("Add animal", "add", self.add_animal_interactive, "Add one animal and type its ID",
                             large=False)
        self.a_dup = act("Duplicate", "copy", self.duplicate_selected,
                         "Copy the selected animals (treatment, sex and fields)", large=False)
        self.a_field_add = act("Add field", "field_add", self._add_field_dialog,
                               "Add a column such as Animal weight, Genotype or Date of birth", large=False)
        self.a_field_rename = act("Rename field", "field_edit", self._rename_field_dialog,
                                  "Rename the selected field column", large=False)
        self.a_field_remove = act("Remove field", "delete", self._remove_field_dialog,
                                  "Remove the selected field column and its values", large=False)
        self.a_treat_add = act("Add treatment", "add", self._add_group_dialog, "Add a treatment")
        self.a_treat_rename = act("Rename", "edit", self._rename_group_dialog, "Rename the selected treatment",
                                  large=False)
        self.a_treat_color = act("Colour", "heatmap", self._color_group_dialog,
                                 "Colour of the selected treatment in plots and tables", large=False)
        self.a_treat_delete = act("Delete treatment", "delete", self._delete_group_dialog,
                                  "Delete the selected treatment (its animals keep no treatment)", large=False)
        self._group_acts = [self.a_treat_add, self.a_treat_rename, self.a_treat_color, self.a_treat_delete]

        # ---- title row -------------------------------------------------------------------
        self.title_lbl = QLabel("Animals")
        self.title_lbl.setObjectName("PageTitle")
        self.summary = QLabel()
        self.summary.setObjectName("Hint")
        top = QHBoxLayout()
        top.addWidget(self.title_lbl)
        top.addStretch()
        top.addWidget(self.summary, 0, Qt.AlignBottom)
        self.blind_lbl = QLabel("Blind testing is on: treatments are shown only as codes and cannot be edited. "
                                "Click <b>Reveal treatment coding</b> to unblind.")
        self.blind_lbl.setWordWrap(True)
        self.blind_lbl.setStyleSheet("color:#7c3aed;padding:2px 0 6px 0;")
        self.blind_lbl.hide()

        # ---- Animals sheet ---------------------------------------------------------------
        self.table = QTableWidget(0, 0)
        self._sheet_style(self.table)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                   | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.setSortingEnabled(True)
        self.table.setItemDelegate(_SheetDelegate(self))
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._update_actions)
        QShortcut(QKeySequence.Delete, self.table, self.delete_selected_interactive)

        # ---- Treatments sheet ------------------------------------------------------------
        self.treatments = QTableWidget(0, 4)
        self._sheet_style(self.treatments)
        self.treatments.setSelectionMode(QAbstractItemView.SingleSelection)
        self.treatments.setHorizontalHeaderLabels(["Treatment", "Code", "Colour", "Number of animals"])
        self.treatments.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                        | QAbstractItemView.EditKeyPressed)
        for c, w in enumerate((320, 90, 160, 170)):
            self.treatments.setColumnWidth(c, w)
        self.treatments.itemChanged.connect(self._treatment_changed)
        self.treatments.itemDoubleClicked.connect(
            lambda it: self._color_group_dialog() if it.column() == 2 else None)
        self.treatments.itemSelectionChanged.connect(self._update_actions)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.treatments)
        self.hint = QLabel()
        self.hint.setObjectName("Hint")
        self.hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.blind_lbl)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.hint)
        self._update_hint()

    @staticmethod
    def _sheet_style(t: QTableWidget):
        """A light ANY-maze style spreadsheet: roomy rows, thin grey grid, no row header."""
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setAlternatingRowColors(False)
        t.setShowGrid(True)
        t.setWordWrap(False)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        t.horizontalHeader().setHighlightSections(False)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setMinimumHeight(34)
        t.setStyleSheet("QTableWidget{font-size:14px;gridline-color:#e6e6e6;border:none;}"
                        "QTableWidget::item{padding:0 6px;}"
                        "QHeaderView::section{font-size:14px;padding:6px 8px;border:none;"
                        "border-right:1px solid #ececec;border-bottom:1px solid #d6d6d6;}")

    # ------------------------------------------------------------------ ribbon / explorer hooks
    def ribbon_groups(self):
        exp = ("Experiment", [self.a_view_treat, self.a_view_animals, self.a_add_animals, self.a_del_animals,
                              self.a_reveal, self.a_import_animals, self.a_import_tests])
        if self.view == "treatments":
            return [exp, ("Treatments", [self.a_treat_add, (self.a_treat_rename, "small"),
                                         (self.a_treat_color, "small"), (self.a_treat_delete, "small")])]
        return [exp,
                ("Animals", [self.retire_btn, self.a_dose, self.a_criteria, self.a_export,
                             (self.a_add_one, "small"), (self.a_dup, "small")]),
                ("Fields", [(self.a_field_add, "small"), (self.a_field_rename, "small"),
                            (self.a_field_remove, "small")])]

    def explorer_items(self):
        return [("Treatments", "treatment", "treatments"), ("Animals", "animal", "animals")]

    def show_item(self, key):
        self.set_view(key)

    def set_view(self, view: str):
        """Switch between the Animals sheet ("animals") and the Treatments sheet ("treatments")."""
        view = "treatments" if view == "treatments" else "animals"
        changed = view != self.view
        self.view = view
        self.stack.setCurrentWidget(self.treatments if view == "treatments" else self.table)
        self.title_lbl.setText("Treatments" if view == "treatments" else "Animals")
        for a, on in ((self.a_view_treat, view == "treatments"), (self.a_view_animals, view == "animals")):
            if a.isChecked() != on:
                a.blockSignals(True)
                a.setChecked(on)
                a.blockSignals(False)
        self._update_hint()
        self._update_actions()
        self.main.select_explorer(self, view)
        if changed and self.main.current_page() is self:
            self.main.refresh_ribbon()

    def _update_hint(self):
        if self.view == "treatments":
            self.hint.setText("Double-click a name to rename the treatment, or a colour to change it. While testing "
                              "blind, treatments are shown by their code only.")
        else:
            self.hint.setText("Click a selected cell (or double-click) to edit it; ▾ cells offer a list. Typing a new "
                              "treatment creates it. Renaming an animal updates its tests.")

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
        """Column titles of the Animals sheet (kinds in self._cols)."""
        p = self.project
        self._cols = ([("number", ""), ("id", ""), ("status", ""), ("treatment", "")]
                      + [("field", f) for f in p.animal_fields] + [("sex", ""), ("tests", "")])
        titles = {"number": "Animal", "id": "Animal ID", "status": "Status", "treatment": "Treatment", "sex": "Sex",
                  "tests": "Tests"}
        return [f if k == "field" else titles[k] for k, f in self._cols]

    def _col_kind(self, c: int) -> str | None:
        return self._cols[c][0] if 0 <= c < len(self._cols) else None

    def col_of(self, kind: str, field: str = "") -> int:
        return next((c for c, (k, f) in enumerate(self._cols) if k == kind and (k != "field" or f == field)), -1)

    def refresh(self):
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        p = self.project
        if p is None:
            self.table.setColumnCount(0)
            self.treatments.setRowCount(0)
            self.summary.setText("")
            self._loading = False
            self._update_actions()
            return
        cols = self._columns()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        counts = self._test_counts()
        self.table.setRowCount(len(p.animals))
        for r, a in enumerate(p.animals):
            self._fill_row(r, a, counts)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        fm = self.table.fontMetrics()
        longest = max([len(treatment_text(p, g.name)) for g in p.groups] + [10])
        widths = {"number": 80, "id": 150, "status": 130, "treatment": min(320, 60 + 9 * longest), "sex": 120,
                  "tests": 70}
        for c, (k, f) in enumerate(self._cols):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, widths.get(k) or max(130, fm.horizontalAdvance(f) + 40))
        self.table.setSortingEnabled(True)
        self._loading = False
        self._refresh_side(counts)

    def _item(self, text="", editable=True, align=None) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        if align is not None:
            it.setTextAlignment(align)
        return it

    def _fill_row(self, r: int, a: Animal, counts: dict):
        p = self.project
        num = self._item(editable=False, align=Qt.AlignCenter)
        num.setData(Qt.DisplayRole, p.animals.index(a) + 1)
        num.setData(Qt.UserRole, a)
        items = {"number": num, "id": self._item(a.id),
                 "status": self._item("Retired" if a.retired else "Normal"),
                 "sex": self._item(a.sex)}
        if a.retired and a.retired_reason:
            items["status"].setToolTip(f"Retired: {a.retired_reason}")
        g = self._item(treatment_text(p, a.group) if p.blind else a.group, editable=not p.blind)
        if a.group:
            g.setIcon(swatch(wf.display_color(p, a.group)))
        items["treatment"] = g
        ids = counts.get(a.id, [])
        n = self._item(editable=False, align=Qt.AlignRight | Qt.AlignVCenter)
        n.setData(Qt.DisplayRole, len(ids))
        n.setForeground(QColor("#6b7280"))
        if ids:
            n.setToolTip("Tests: " + ", ".join(str(i) for i in ids))
        items["tests"] = n
        for c, (k, f) in enumerate(self._cols):
            it = self._item(str(a.fields.get(f, ""))) if k == "field" else items[k]
            if a.retired:
                it.setForeground(QColor(MUTED_ROW))
            self.table.setItem(r, c, it)

    def _refresh_side(self, counts=None):
        p = self.project
        counts = counts if counts is not None else self._test_counts()
        self._fill_treatments()
        n_tests = sum(len(v) for k, v in counts.items() if p.get_animal(k))
        n_ret = sum(1 for a in p.animals if a.retired)
        nt = len(p.groups)
        self.summary.setText(f"{len(p.animals)} animals · {nt} treatment{'s' if nt != 1 else ''} · {n_tests} tests"
                             + (f" · {n_ret} retired" if n_ret else ""))
        self.blind_lbl.setVisible(p.blind)
        self.a_reveal.blockSignals(True)
        self.a_reveal.setChecked(not p.blind)
        self.a_reveal.blockSignals(False)
        self._update_actions()

    def _fill_treatments(self):
        p = self.project
        cur_g = self._current_group()
        t = self.treatments
        self._loading = True
        t.setRowCount(len(p.groups))
        for r, g in enumerate(p.groups):
            n = sum(1 for a in p.animals if a.group == g.name)
            name = self._item(g.name if not p.blind else "Hidden (blind testing)", editable=not p.blind)
            name.setData(Qt.UserRole, g.name)
            if p.blind:
                name.setForeground(QColor(MUTED_ROW))
            col = self._item(wf.display_color(p, g.name) if not p.blind else "", editable=False)
            col.setIcon(swatch(wf.display_color(p, g.name), 16))
            col.setToolTip("Double-click to change the colour")
            cnt = self._item(editable=False, align=Qt.AlignRight | Qt.AlignVCenter)
            cnt.setData(Qt.DisplayRole, n)
            for c, it in enumerate((name, self._item(treatment_code(p, g.name), editable=False), col, cnt)):
                t.setItem(r, c, it)
            if g.name == cur_g:
                t.setCurrentCell(r, 0)
        self._loading = False

    def _changed(self):
        self.main.mark_dirty()

    def _update_actions(self, *_):
        p = self.project
        has = p is not None
        animals = self.view == "animals"
        sel = self.selected_animals() if has and animals else []
        for a in (self.a_add_animals, self.a_add_one, self.a_import_animals, self.a_import_tests, self.a_reveal,
                  self.a_field_add, self.a_export, self.a_criteria, self.a_dose):
            a.setEnabled(has)
        for a in (self.a_del_animals, self.a_dup):
            a.setEnabled(bool(sel))
        self._update_retire_btn()
        field = self._current_field()
        self.a_field_rename.setEnabled(has and bool(p.animal_fields))
        self.a_field_remove.setEnabled(has and bool(p.animal_fields))
        if field:
            self.a_field_remove.setToolTip(f"Remove the column “{field}” and its values")
        blind = has and p.blind
        self.a_treat_add.setEnabled(has and not blind)
        cur = self._current_group() if has else None
        for a in (self.a_treat_rename, self.a_treat_color, self.a_treat_delete):
            a.setEnabled(bool(cur) and not blind)

    # ------------------------------------------------------------------ blind coding
    def _reveal_toggled(self, on: bool):
        p = self.project
        if p is None:
            return
        if on and p.blind and QMessageBox.question(
                self, "Reveal treatment coding", "Reveal the treatments? The experimenter will no longer be blind "
                "to the treatment of each animal.") != QMessageBox.Yes:
            self.a_reveal.blockSignals(True)
            self.a_reveal.setChecked(False)
            self.a_reveal.blockSignals(False)
            return
        self.set_blind(not on)

    def set_blind(self, blind: bool):
        p = self.project
        p.blind = bool(blind)
        if p.blind:
            wf.blind_codes(p)
        self._changed()
        self.refresh()
        self.main.status("Blind testing: treatments are shown as codes." if p.blind
                         else "Treatment coding revealed.")

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
            p.ensure_group(group)
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
            c = self.col_of("id")
            self.table.setCurrentCell(r, c)
            self.table.editItem(self.table.item(r, c))

    def add_several(self, prefix: str, count: int, group: str = "", start: int = 1, digits: int = 2) -> list[Animal]:
        ids = [f"{prefix}{start + i:0{digits}d}" for i in range(count)]
        return self._add_ids(ids, group)

    def _add_ids(self, ids, group) -> list[Animal]:
        p = self.project
        taken = self._taken()
        if group:
            p.ensure_group(group)
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
        dlg = AddSeveralDialog(self._group_names(), self)
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
                    "treatment or animal details.")
        if QMessageBox.question(self, "Delete animals", msg) == QMessageBox.Yes:
            self.delete_animals(sel)

    def rename_animal(self, a: Animal, new_id: str) -> bool:
        if not wf.rename_animal(self.project, a, new_id):
            return False
        self._changed()
        return True

    def _cell_changed(self, item: QTableWidgetItem):
        if self._loading or self.project is None:
            return
        r, kind = item.row(), self._col_kind(item.column())
        idit = self.table.item(r, 0)
        if idit is None:
            return
        a: Animal = idit.data(Qt.UserRole)
        text = item.text().strip()
        p = self.project
        self._loading = True
        try:
            if kind == "id":
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
            elif kind == "status":
                retire = text.lower().startswith("retire")
                if retire and not a.retired:
                    n = wf.retire_animal(p, a, "")
                    self.main.status(f"Retired {a.id} ({n} pending tests skipped).")
                elif not retire and a.retired:
                    n = wf.reinstate_animal(p, a)
                    self.main.status(f"Reinstated {a.id} ({n} skipped tests resumed).")
                item.setText("Retired" if a.retired else "Normal")
                for c in range(self.table.columnCount()):
                    it = self.table.item(r, c)
                    if it is not None and self._col_kind(c) != "tests":
                        it.setForeground(QColor(MUTED_ROW) if a.retired else QColor(theme.TEXT))
                self._changed()
                self._refresh_side()
            elif kind == "treatment":
                if p.blind:
                    item.setText(treatment_text(p, a.group))
                    return
                text = _strip_code(p, text)
                if text:
                    p.ensure_group(text)
                a.group = text
                item.setText(text)
                item.setIcon(swatch(p.group_color(text)) if text else QIcon())
                self._changed()
                self._refresh_side()
            elif kind == "sex":
                a.sex = text
                self._changed()
            elif kind == "field":
                a.fields[self._cols[item.column()][1]] = text
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
                                    "Protocol (Training criteria).")
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
        r = self.treatments.currentRow()
        it = self.treatments.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _treatment_changed(self, item: QTableWidgetItem):
        if self._loading or self.project is None or item.column() != 0:
            return
        old, new = item.data(Qt.UserRole), item.text().strip()
        if not new or new == old:
            self._loading = True
            item.setText(old)
            self._loading = False
            return
        if new in self._group_names():
            QTimer.singleShot(0, self.refresh)
            QMessageBox.warning(self, "Rename treatment", f"A treatment named “{new}” already exists.")
            return
        if wf.rename_group(self.project, old, new):
            self._changed()
        QTimer.singleShot(0, self.refresh)  # not while the sheet is emitting itemChanged

    def add_group(self, name: str, color: str | None = None):
        g = wf.add_group(self.project, name, color)
        if g is not None:
            self._changed()
            self.refresh()
        return g

    def rename_group(self, old: str, new: str) -> bool:
        return self._edited(wf.rename_group(self.project, old, new))

    def set_group_color(self, name: str, color: str):
        g = self.project.get_group(name)
        if g is not None:
            g.color = color
            self._edited(True)

    def delete_group(self, name: str):
        wf.delete_group(self.project, name)
        self._edited(True)

    def _edited(self, changed: bool) -> bool:
        if changed:
            self._changed()
            self.refresh()
        return changed

    def _add_group_dialog(self):
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "Add treatment", "Treatment name:",
                                        text=f"Treatment {len(self.project.groups) + 1}")
        if ok and name.strip():
            if self.add_group(name) is None:
                QMessageBox.warning(self, "Add treatment", f"A treatment named “{name.strip()}” already exists.")

    def _rename_group_dialog(self):
        old = self._current_group()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename treatment", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            if not self.rename_group(old, new):
                QMessageBox.warning(self, "Rename treatment", f"A treatment named “{new.strip()}” already exists.")

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
        msg = f"Delete the treatment “{name}”?"
        if n:
            msg += f"\n\n{n} animals will be left without a treatment."
        if QMessageBox.question(self, "Delete treatment", msg) == QMessageBox.Yes:
            self.delete_group(name)

    # ------------------------------------------------------------------ fields
    def add_field(self, name: str) -> bool:
        return self._edited(wf.add_field(self.project, name))

    def remove_field(self, name: str):
        self._edited(wf.remove_field(self.project, name))

    def rename_field(self, old: str, new: str) -> bool:
        return self._edited(wf.rename_field(self.project, old, new))

    def _add_field_dialog(self):
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "Add field", "Field (column) name, e.g. Weight (g), Genotype:")
        if ok and name.strip() and not self.add_field(name):
            QMessageBox.warning(self, "Add field", f"“{name.strip()}” is already a column.")

    def _current_field(self) -> str | None:
        """The field column holding the current cell of the Animals sheet, if any."""
        if self.project is None:
            return None
        c = self.table.currentColumn()
        return self._cols[c][1] if self._col_kind(c) == "field" else None

    def _pick_field(self, title: str) -> str | None:
        f = self._current_field()
        if f:
            return f
        fields = list(self.project.animal_fields) if self.project else []
        if not fields:
            return None
        if len(fields) == 1:
            return fields[0]
        name, ok = QInputDialog.getItem(self, title, "Field:", fields, 0, False)
        return name if ok else None

    def _rename_field_dialog(self):
        old = self._pick_field("Rename field")
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename field", "New name:", text=old)
        if ok and new.strip() and new.strip() != old and not self.rename_field(old, new):
            QMessageBox.warning(self, "Rename field", f"“{new.strip()}” is already a column.")

    def _remove_field_dialog(self):
        name = self._pick_field("Remove field")
        if not name:
            return
        if QMessageBox.question(self, "Remove field",
                                f"Remove the column “{name}” and its values for every animal?") == QMessageBox.Yes:
            self.remove_field(name)

    # ------------------------------------------------------------------ CSV
    def _export_dialog(self):
        if self.project is None:
            return
        default = str(self.project.exports_dir() / "animals.csv") if self.project.path else "animals.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export animals", default, "CSV files (*.csv)")
        if not path:
            return
        try:
            export.export_animals(self.project, path)
        except Exception as e:
            error_box(self, "Export animals", e)
            return
        self.main.status(f"Exported {len(self.project.animals)} animals to {path}")
