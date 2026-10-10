"""Run tests ▸ Setup ▸ *File names…*: the fields the videos recorded during live tests are named after
(Project.recording_name_fields, see core.recordings)."""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
                               QVBoxLayout)

from ....core.recordings import DEFAULT_FIELDS, FIELDS, field_label, name_fields, recording_name
from ....core.terminology import term


def describe(project) -> str:
    """The chosen fields and an example name, e.g. "Test number, animal — test_0007_M01.mp4"."""
    fields = name_fields(project)
    labels = [field_label(project, f) for f in fields]
    text = ", ".join([labels[0]] + [lab[:1].lower() + lab[1:] if not lab[:2].isupper() else lab
                                    for lab in labels[1:]])
    test = next(iter(project.tests), None) if project is not None else None
    example = recording_name(project, test) if test is not None else ""
    return f"{text} — e.g. {example}.mp4" if example else text


class RecordingNamesDialog(QDialog):
    """Tick the fields to use and drag them into order; the name below shows the result for the first test."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Recorded video file names")
        v = QVBoxLayout(self)
        intro = QLabel("Name the videos recorded during tests after these fields, in this order (drag to reorder), "
                       "joined by _. Testing blind, the treatment's code is used, never its name. A name already "
                       "used gets _2, _3 … so no recording is ever replaced.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        chosen = name_fields(project)
        for f in chosen + [f for f in FIELDS if f not in chosen]:
            it = QListWidgetItem(field_label(project, f))
            it.setData(Qt.UserRole, f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if f in chosen else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(self._update)
        self.list.model().rowsMoved.connect(self._update)
        v.addWidget(self.list)
        self.example = QLabel()
        self.example.setObjectName("Hint")
        self.example.setWordWrap(True)
        v.addWidget(self.example)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self.restore_defaults)
        v.addWidget(bb)
        self._update()

    def fields(self) -> list[str]:
        """The ticked fields in their order."""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out

    def set_fields(self, fields: list[str]):
        """Tick these fields (in this order, first), untick the others."""
        self.list.blockSignals(True)
        items = {self.list.item(i).data(Qt.UserRole): self.list.takeItem(i) for i in reversed(range(self.list.count()))}
        for f in list(fields) + [f for f in FIELDS if f not in fields]:
            it = items[f]
            it.setCheckState(Qt.Checked if f in fields else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._update()

    def restore_defaults(self):
        self.set_fields(list(DEFAULT_FIELDS))

    def _update(self, *_):
        p = self.project
        test = next(iter(p.tests), None) if p is not None else None
        fields = self.fields()
        if test is None:
            self.example.setText("")
            return
        name = recording_name(p, test, _dt.datetime.now(), fields or DEFAULT_FIELDS)
        self.example.setText(f"For {term(p, 'test', lower=True)} {test.id}: {name}.mp4"
                             + ("" if fields else " (no field ticked: the default name)"))

    def stored_fields(self) -> list[str]:
        """What to save in the experiment: [] for mANY-MAZE's default name."""
        f = self.fields()
        return [] if f == list(DEFAULT_FIELDS) or not f else f
