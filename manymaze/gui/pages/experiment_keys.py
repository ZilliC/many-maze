"""The Protocol page's Keys element: the table of manually scored behaviours (a mixin of experiment.ExperimentPage)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QTableWidgetItem, QWidget

from ...core import workflow as wf
from ...core.project import Behaviour
from ..widgets import ColorButton, loading

# key types of the keys table (the property page offers the full ANY-maze wording)
KIND_LABELS = [("hold", "Simple"), ("state", "Toggle"), ("state", "Radio"), ("point", "Event")]
BEH_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16", "#f97316",
              "#06b6d4"]


class KeysMixin:
    """Protocol ▸ Keys: the keys (manually scored behaviours) table and the key property page."""

    def _append_behaviour_row(self, b: Behaviour):
        with loading(self):
            self._add_behaviour_cells(b)

    def _add_behaviour_cells(self, b: Behaviour):
        r = self.beh.rowCount()
        self.beh.insertRow(r)
        self._key_names.insert(r, b.name)
        it = QTableWidgetItem(b.name)
        it.setData(Qt.UserRole, bool(b.activity))  # counts as activity (the Key property page)
        self.beh.setItem(r, 0, it)
        k = QTableWidgetItem(b.key.upper() if len(b.key) == 1 else b.key)
        k.setTextAlignment(Qt.AlignCenter)
        self.beh.setItem(r, 1, k)
        kind = QComboBox()
        for v, label in KIND_LABELS:
            kind.addItem(label, v)
        kind.setToolTip("Simple: while the key is held down · Toggle: first press starts, second press ends · "
                        "Radio: a toggle that also ends when another key of its radio set is pressed · Event: "
                        "an instant")
        kind.setCurrentIndex(self._kind_index(b.kind, b.group))
        kind.currentIndexChanged.connect(lambda _i, w=kind: self._kind_changed(w))
        self.beh.setCellWidget(r, 2, kind)
        self.beh.setItem(r, 3, QTableWidgetItem(b.group))
        col = ColorButton(b.color or BEH_COLORS[r % len(BEH_COLORS)], "Key colour")
        col.setFixedSize(52, 22)
        col.setToolTip("Colour of the on-screen scoring button")
        col.color_changed.connect(lambda _c: self._store_behaviours())
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.addWidget(col)
        holder.setProperty("color", col.color())
        holder.button = col
        col.color_changed.connect(lambda c, h=holder: h.setProperty("color", c))
        self.beh.setCellWidget(r, 4, holder)

    @staticmethod
    def _kind_index(kind: str, group: str) -> int:
        mode = wf.key_mode(kind, group)
        return {"simple": 0, "toggle": 1, "radio": 2, "event": 3}[mode]

    def _kind_changed(self, combo: QComboBox):
        """The table's "How it works" changed: Radio keys need a radio set, Toggle keys have none."""
        if self._loading:
            return
        r = next((r for r in range(self.beh.rowCount()) if self.beh.cellWidget(r, 2) is combo), -1)
        if r < 0:
            return
        it = self.beh.item(r, 3)
        group = it.text().strip() if it else ""
        mode = ("simple", "toggle", "radio", "event")[combo.currentIndex()]
        _kind, new_group = wf.mode_to_kind(mode, group)
        if new_group != group:
            with loading(self):
                self.beh.setItem(r, 3, QTableWidgetItem(new_group))
        self._store_behaviours()

    def _add_behaviour(self):
        if self.project is None:
            return
        key = wf.free_key(self.project.behaviours)
        self._append_behaviour_row(Behaviour(f"Behaviour {self.beh.rowCount() + 1}", key, "state"))
        self.beh.setCurrentCell(self.beh.rowCount() - 1, 0)
        self._store_behaviours()

    def _remove_behaviour(self, confirm: bool = True):
        r = self.beh.currentRow()
        if r < 0:
            return
        b = self._row_behaviour(r)
        n, n_tests = self.project.key_events(b.name) if b is not None and self.project is not None else (0, 0)
        if n and confirm and QMessageBox.question(
                self, "Delete key", f"The key “{b.name}” has {n} scored event{'s' if n != 1 else ''} in {n_tests} "
                f"test{'s' if n_tests != 1 else ''}. Delete the key?\n\nThe events stay in the tests but are no longer "
                "analysed (a key of the same name analyses them again).") != QMessageBox.Yes:
            return
        if r < len(self._key_names):
            self._key_names.pop(r)
        self.beh.removeRow(r)
        self._store_behaviours()

    def _row_behaviour(self, r) -> Behaviour | None:
        name = self.beh.item(r, 0).text().strip() if self.beh.item(r, 0) else ""
        if not name:
            return None
        key = self.beh.item(r, 1).text().strip()[:1].lower() if self.beh.item(r, 1) else ""
        kind = self.beh.cellWidget(r, 2).currentData() if self.beh.cellWidget(r, 2) else "state"
        group = self.beh.item(r, 3).text().strip() if self.beh.item(r, 3) else ""
        color = self.beh.cellWidget(r, 4).property("color") if self.beh.cellWidget(r, 4) else ""
        return Behaviour(name, key, kind, group, color or "", bool(self.beh.item(r, 0).data(Qt.UserRole)))

    def _store_behaviours(self, *_):
        if self._loading or self.project is None:
            return
        self._follow_key_renames()
        out = [b for b in (self._row_behaviour(r) for r in range(self.beh.rowCount())) if b is not None]
        self.project.behaviours = out
        with loading(self):
            for r in range(self.beh.rowCount()):  # Toggle ⇄ Radio follows the radio set
                combo, b = self.beh.cellWidget(r, 2), self._row_behaviour(r)
                if combo is not None and b is not None:
                    combo.setCurrentIndex(self._kind_index(b.kind, b.group))
        self._validate_behaviours()
        self._show_key()
        self._update_summary()
        self.main.mark_dirty()

    def _follow_key_renames(self):
        """A renamed key keeps its data: scored events, marked time periods and criteria follow the new name
        (p.rename_key). Rows are matched by position (the table's rows are the keys)."""
        names = [self.beh.item(r, 0).text().strip() if self.beh.item(r, 0) else "" for r in range(self.beh.rowCount())]
        if len(names) != len(self._key_names):
            self._key_names = names
            return
        for r, (prev, cur) in enumerate(zip(self._key_names, names)):
            if not cur:
                continue
            if prev and cur != prev and names.count(cur) == 1 and prev not in names:
                self.project.rename_key(prev, cur)
            self._key_names[r] = cur

    def _validate_behaviours(self) -> list[str]:
        errs = wf.validate_behaviours(self.project.behaviours) if self.project is not None else []
        self.beh_lbl.setText("<br>".join(errs))
        self.beh_lbl.setVisible(bool(errs))
        return errs

    def _show_key(self):
        """Show the selected key on the "Key" property page."""
        r = self.beh.currentRow()
        b = self._row_behaviour(r) if 0 <= r < self.beh.rowCount() else None
        if b is None:
            self.key_editor.load(None)
        else:
            self.key_editor.load(b.name, b.key, b.kind, b.group, b.color, b.activity)

    def _key_edited(self, v: dict):
        r = self.beh.currentRow()
        if not 0 <= r < self.beh.rowCount():
            return
        with loading(self):
            if v["name"]:
                self.beh.item(r, 0).setText(v["name"])
            self.beh.item(r, 0).setData(Qt.UserRole, bool(v.get("activity")))
            key = v["key"]
            self.beh.item(r, 1).setText(key.upper() if len(key) == 1 else key)
            self.beh.cellWidget(r, 2).setCurrentIndex(self._kind_index(v["kind"], v["group"]))
            self.beh.setItem(r, 3, QTableWidgetItem(v["group"]))
            holder = self.beh.cellWidget(r, 4)
            if holder is not None and v["color"]:
                holder.setProperty("color", v["color"])
                holder.button.set_color(v["color"])
        self._store_behaviours()
