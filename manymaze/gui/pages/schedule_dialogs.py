"""Dialogs of the test schedule: tests from videos, schedule generation, DeepLabCut import and test variables."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...core import workflow as wf
from ...core.workflow import treatment_text
from .. import theme


class AddVideosDialog(QDialog):
    """Assign animal IDs, stage, trial and apparatus to newly added videos."""

    def __init__(self, project, files: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add tests from videos")
        self.project = project
        self.files = files
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.stage = QComboBox()
        self.stage.setEditable(True)
        self.stage.addItems(project.stages)
        self.trial = QSpinBox()
        self.trial.setRange(1, wf.MAX_TRIALS)
        self.trial.setValue(1)
        self.app = QComboBox()
        self.app.addItems([a.name for a in project.apparatus])
        self.per_app = QCheckBox("One test per apparatus (several arenas filmed in each video)")
        self.per_app.setEnabled(len(project.apparatus) > 1)
        self.per_app.toggled.connect(self._fill)
        f.addRow("Stage", self.stage)
        f.addRow("Trial", self.trial)
        f.addRow("Apparatus", self.app)
        f.addRow("", self.per_app)
        lay.addLayout(f)
        lay.addWidget(QLabel("Animal IDs (new IDs are added to the animal list):"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Video", "Apparatus", "Animal ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()
        self.resize(720, 460)

    def _fill(self):
        self.table.setRowCount(0)
        apps = [a.name for a in self.project.apparatus] if self.per_app.isChecked() else [None]
        for fpath in self.files:
            for an in apps:
                r = self.table.rowCount()
                self.table.insertRow(r)
                it = QTableWidgetItem(Path(fpath).name)
                it.setToolTip(fpath)
                it.setData(Qt.UserRole, fpath)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, 0, it)
                ai = QTableWidgetItem(an or "(as above)")
                ai.setData(Qt.UserRole, an)
                ai.setFlags(ai.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, 1, ai)
                stem = Path(fpath).stem
                self.table.setItem(r, 2, QTableWidgetItem(f"{stem}-{an}" if an else stem))

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            an = self.table.item(r, 1).data(Qt.UserRole) or self.app.currentText()
            out.append({"video": self.table.item(r, 0).data(Qt.UserRole), "apparatus": an,
                        "animal_id": self.table.item(r, 2).text().strip(), "stage": self.stage.currentText().strip(),
                        "trial": self.trial.value()})
        return out


class ScheduleDialog(QDialog):
    """Create tests (without video) for animals × stages × trials, in a chosen running order."""

    ORDERS = [("animal", "By animal: all trials of an animal, then the next animal"),
              ("trial", "By trial: trial 1 of every animal, then trial 2, …"),
              ("random", "Randomised: animals in random order within each trial"),
              ("latin", "Latin square: each animal runs in every position equally often")]

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Create test schedule")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Creates one test without video for every animal × stage × trial, in running order. "
                             "Videos can be assigned later, or the tests recorded live."))
        row = QHBoxLayout()
        self.animals = self._checklist([(a.id, a.id + (f"  ({treatment_text(project, a.group)})" if a.group else "")
                                         + ("  — retired" if a.retired else ""), not a.retired)
                                        for a in project.animals])
        stages = project.stages or [""]
        self.stages = self._checklist([(s, s or "(no stage)", True) for s in stages])
        for title, w in (("Animals", self.animals), ("Stages", self.stages)):
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{title}</b>"))
            col.addWidget(w)
            row.addLayout(col)
        lay.addLayout(row, 1)
        f = QFormLayout()
        self.trials = QSpinBox()
        self.trials.setRange(1, wf.MAX_TRIALS)
        self.app = QComboBox()
        self.app.addItems([a.name for a in project.apparatus])
        self.order = QComboBox()
        for v, label in self.ORDERS:
            self.order.addItem(label, v)
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setSpecialValueText("new random order")
        self.seed.setToolTip("Random seed: the same number gives the same order again")
        # "new random order": one order drawn when the dialog opens, so the preview shows the tests that are created
        self.random_seed = random.SystemRandom().randrange(1, 2 ** 31)
        self.cb = QComboBox()
        self.cb.addItem("No counterbalancing", "")
        self.cb.addItem("Apparatus (Latin square across each animal's tests)", "apparatus")
        self.cb.addItem("Test variable (Latin square across each animal's tests)", "variable")
        self.cb.currentIndexChanged.connect(self._cb_changed)
        self.var_name = QComboBox()
        self.var_name.setEditable(True)
        self.var_name.addItems(["novel_object", "social_side", "condition"])
        self.levels = QLineEdit()
        self.levels.setPlaceholderText("Levels, comma separated (e.g. Object A, Object B)")
        self.skip = QCheckBox("Skip combinations that already have a test")
        self.skip.setChecked(True)
        self.skip_done = QCheckBox("Skip stages already completed (training criteria) and retired animals")
        self.skip_done.setChecked(True)
        f.addRow("Trials per stage", self.trials)
        f.addRow("Apparatus", self.app)
        f.addRow("Running order", self.order)
        f.addRow("Random seed", self.seed)
        f.addRow("Counterbalance", self.cb)
        f.addRow("Variable", self.var_name)
        f.addRow("Levels", self.levels)
        f.addRow("", self.skip)
        f.addRow("", self.skip_done)
        lay.addLayout(f)
        self.preview = QLabel()
        theme.style(self.preview, lambda: f"color:{theme.HINT};")
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        for w in (self.trials, self.seed):
            w.valueChanged.connect(self._update_preview)
        for w in (self.order, self.cb, self.app):
            w.currentIndexChanged.connect(self._update_preview)
        self.levels.textChanged.connect(self._update_preview)
        self.animals.itemChanged.connect(self._update_preview)
        self.stages.itemChanged.connect(self._update_preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._cb_changed()
        self.resize(620, 620)

    def _cb_changed(self, *_):
        cb = self.cb.currentData()
        self.var_name.setEnabled(cb == "variable")
        self.levels.setEnabled(bool(cb))
        if cb == "apparatus" and not self.levels.text().strip():
            self.levels.setText(", ".join(a.name for a in self.project.apparatus))
        self._update_preview()

    @staticmethod
    def _checklist(items):
        w = QListWidget()
        for v, label, on in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            w.addItem(it)
        return w

    @staticmethod
    def _checked(w):
        return [w.item(i).data(Qt.UserRole) for i in range(w.count()) if w.item(i).checkState() == Qt.Checked]

    def combos(self):
        return [(r["animal_id"], r["stage"], r["trial"]) for r in self.rows()]

    def rows(self) -> list[dict]:
        levels = [x.strip() for x in self.levels.text().split(",") if x.strip()]
        return wf.generate_schedule(
            self.project, animals=self._checked(self.animals), stages=self._checked(self.stages),
            trials=self.trials.value(), order=self.order.currentData(), apparatus=self.app.currentText(),
            seed=self.seed.value() or self.random_seed, counterbalance=self.cb.currentData() if levels else "",
            levels=levels, variable=self.var_name.currentText().strip(), skip_existing=self.skip.isChecked(),
            skip_retired=self.skip_done.isChecked(), skip_completed=self.skip_done.isChecked())

    def _update_preview(self, *_):
        rows = self.rows()
        head = ", ".join(f"{r['animal_id']}/{r['stage'] or '–'}/{r['trial']}" for r in rows[:6])
        self.preview.setText(f"<b>{len(rows)}</b> test{'s' if len(rows) != 1 else ''} will be created"
                             + (f" — first: {head}{' …' if len(rows) > 6 else ''}" if rows else "."))


class DlcImportDialog(QDialog):
    def __init__(self, parts: list[str], fps: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import DeepLabCut track")
        f = QFormLayout(self)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(0.1, 1000)
        self.fps.setDecimals(3)
        self.fps.setValue(fps)
        self.centre = QListWidget()
        self.centre.setMaximumHeight(140)
        for p in parts:
            it = QListWidgetItem(p)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.centre.addItem(it)
        self.head = QComboBox()
        self.tail = QComboBox()
        for cb, hints in ((self.head, ("nose", "snout", "head")), (self.tail, ("tailbase", "tail_base", "tail"))):
            cb.addItem("(none)", None)
            for p in parts:
                cb.addItem(p, p)
            guess = next((i + 1 for h in hints for i, p in enumerate(parts) if h in p.lower().replace(" ", "")), 0)
            cb.setCurrentIndex(guess)
        self.lik = QDoubleSpinBox()
        self.lik.setRange(0, 1)
        self.lik.setSingleStep(0.05)
        self.lik.setValue(0.6)
        self.trim = QCheckBox("CSV covers the whole video: keep only the test period")
        self.trim.setChecked(True)
        f.addRow("Frame rate (fps)", self.fps)
        f.addRow("Body centre = mean of", self.centre)
        f.addRow("Head", self.head)
        f.addRow("Tail base", self.tail)
        f.addRow("Min likelihood", self.lik)
        f.addRow("", self.trim)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def options(self) -> dict:
        centre = [self.centre.item(i).text() for i in range(self.centre.count())
                  if self.centre.item(i).checkState() == Qt.Checked]
        return {"fps": self.fps.value(), "centre_parts": centre or None, "head_part": self.head.currentData(),
                "tail_part": self.tail.currentData(), "likelihood_min": self.lik.value(), "trim": self.trim.isChecked()}


class VariablesDialog(QDialog):
    """Per-test variables that change the analysis (e.g. which object is novel in a NOR test)."""

    def __init__(self, project, tests: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test variables")
        f = QFormLayout(self)
        f.addRow(QLabel(f"{len(tests)} test{'s' if len(tests) != 1 else ''} selected. "
                        "“Experiment default” uses the analysis settings of the experiment."))
        points = []
        for t in tests:
            app = project.get_apparatus(t.apparatus)
            for p in (app.points if app else []):
                if p.name not in points:
                    points.append(p.name)
        self.novel = QComboBox()
        self.novel.addItem(f"Experiment default ({project.analysis.novel_object})", None)
        for name in points:
            self.novel.addItem(name, name)
        self.side = QComboBox()
        self.side.addItem(f"Experiment default ({project.analysis.social_side})", None)
        for side in ("Left", "Right"):
            self.side.addItem(side, side)
        for cb, key in ((self.novel, "novel_object"), (self.side, "social_side")):
            vals = {(t.variables or {}).get(key) for t in tests}
            if len(vals) == 1:
                v = vals.pop()
                i = cb.findData(v) if v is not None else 0
                if i < 0:
                    cb.addItem(str(v), v)
                    i = cb.count() - 1
                cb.setCurrentIndex(i)
            else:
                cb.insertItem(0, "(mixed — keep)", "__keep__")
                cb.setCurrentIndex(0)
        f.addRow("Novel object", self.novel)
        f.addRow("Social stimulus side", self.side)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def apply(self, tests: list) -> None:
        for t in tests:
            v = dict(t.variables or {})
            for cb, key in ((self.novel, "novel_object"), (self.side, "social_side")):
                d = cb.currentData()
                if d == "__keep__":
                    continue
                if d is None:
                    v.pop(key, None)
                else:
                    v[key] = d
            t.variables = v
