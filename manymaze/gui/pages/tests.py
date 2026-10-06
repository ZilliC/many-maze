"""Test schedule (as in ANY-maze): the list of tests (animal × stage × trial × video) with their testing status —
the next test of each apparatus is "Ready" — plus batch tracking, track import and the test status commands."""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QEvent, QItemSelectionModel, QModelIndex, QRect,
                            QSortFilterProxyModel, Qt)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QSpinBox, QStyledItemDelegate, QTableView,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...core import workflow as wf
from ...core.batch import TrackingCancelled, track_tests, tracking_batches  # noqa: F401 (re-exported)
from ...core.track import Track, import_deeplabcut_csv
from ...core.video import VIDEO_EXTENSIONS, VideoSource
from .. import theme
from ..icons import icon
from ..widgets import error_box, run_with_progress
from .animals import ribbon_action, swatch, treatment_code, treatment_text
from .base import Page

COLUMNS = ["Test", "Animal", "Code", "Stage", "Trial", "Apparatus", "Video", "Testing status", "Start (s)",
           "Duration (s)", "Notes"]
C_ID, C_ANIMAL, C_GROUP, C_STAGE, C_TRIAL, C_APP, C_VIDEO, C_STATUS, C_START, C_DUR, C_NOTES = range(len(COLUMNS))
EDITABLE = {C_ANIMAL, C_STAGE, C_TRIAL, C_APP, C_START, C_DUR, C_NOTES}
SORT_ROLE = Qt.UserRole + 1
STATUS_COLORS = {"pending": "#d97706", "tracked": "#16a34a", "scored": "#0891b2", "skipped": "#9333ea",
                 "superseded": "#94a3b8", "excluded": "#94a3b8"}
STATUS_TEXT = {"pending": "", "tracked": "Tracked", "scored": "Scored", "skipped": "Skipped",
               "superseded": "Superseded", "excluded": "Excluded"}
READY_FG = "#1e8e3e"  # the next test of each apparatus (ANY-maze green)
READY_BG = "#e8f4e8"
LINK_FG = "#1f6fc5"  # animal IDs (blue, as links in ANY-maze)
GREY_FG = "#a3a3a3"  # skipped / excluded / superseded tests


def _dot(color: str, size: int = 12):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.setPen(Qt.NoPen)
    qp.setBrush(QColor(color))
    qp.drawEllipse(3, 3, size - 6, size - 6)
    qp.end()
    return pm


def ready_tests(project) -> set[int]:
    """IDs of the next test to perform on each apparatus: the first pending test (in schedule order) whose animal
    is not retired."""
    out, seen = set(), set()
    if project is None:
        return out
    for t in project.tests:
        if t.status != "pending" or t.apparatus in seen:
            continue
        a = project.get_animal(t.animal_id)
        if a is not None and a.retired:
            continue
        seen.add(t.apparatus)
        out.add(t.id)
    return out


# ------------------------------------------------------------------ tracking jobs
def track_tests_job(project, tests):
    """Return fn(progress, should_stop) tracking tests (in parallel processes) from a background thread.

    The result is a dict {"tracked": [test ids], "cancelled": bool, "errors": [str], "workers": n}.
    """
    return lambda progress, should_stop: track_tests(project, tests, progress, should_stop)


# ------------------------------------------------------------------ model
class TestsModel(QAbstractTableModel):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.ready: set[int] = set()
        self._dots: dict[str, QPixmap] = {}

    @property
    def tests(self):
        p = self.page.project
        return p.tests if p is not None else []

    def reset(self):
        self.beginResetModel()
        self.ready = ready_tests(self.page.project)
        self.endResetModel()

    def dot(self, color):
        if color not in self._dots:
            self._dots[color] = _dot(color)
        return self._dots[color]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tests)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            p = self.page.project
            if section == C_GROUP:
                return "Code" if p is not None and p.blind else "Treatment"
            return COLUMNS[section]
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() in EDITABLE:
            f |= Qt.ItemIsEditable
        return f

    def test_at(self, row):
        return self.tests[row] if 0 <= row < len(self.tests) else None

    def data(self, index, role=Qt.DisplayRole):
        t = self.test_at(index.row())
        if t is None:
            return None
        p = self.page.project
        c = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole, SORT_ROLE):
            if c == C_ID:
                return t.id
            if c == C_ANIMAL:
                return t.animal_id + (f" +{len(t.extra_animals)}" if t.extra_animals and role == Qt.DisplayRole
                                      else "")
            if c == C_GROUP:
                a = p.get_animal(t.animal_id)
                if not a or not a.group:
                    return ""
                return treatment_code(p, a.group) if p.blind else a.group
            if c == C_STAGE:
                return t.stage
            if c == C_TRIAL:
                return t.trial
            if c == C_APP:
                return t.apparatus
            if c == C_VIDEO:
                return Path(t.video).name if t.video else ""
            if c == C_START:
                return t.start_s if role != Qt.DisplayRole else f"{t.start_s:.2f}"
            if c == C_DUR:
                if role == Qt.DisplayRole:
                    return f"{t.duration_s:g}" if t.duration_s else f"default ({p.test_duration_s:g})"
                return t.duration_s
            if c == C_STATUS:
                if role != Qt.DisplayRole:
                    return t.status
                text = "Ready" if t.id in self.ready else STATUS_TEXT.get(t.status, t.status.capitalize())
                if t.attempt > 1:
                    text = f"{text} (attempt {t.attempt})" if text else f"(attempt {t.attempt})"
                return text
            if c == C_NOTES:
                return t.notes
        elif role == Qt.ToolTipRole:
            if c == C_VIDEO:
                if not t.video:
                    return "No video: tested live, scored by direct observation, or set the video file later"
                full = p.abs_path(t.video)
                return full if Path(full).exists() else f"{full}\n(file not found)"
            if c == C_DUR:
                return "Test duration in seconds. 0 = experiment default."
            if c == C_ANIMAL:
                a = p.get_animal(t.animal_id)
                tips = (["Also in this test: " + ", ".join(t.extra_animals)] if t.extra_animals else []) + \
                    ([f"Retired: {a.retired_reason or 'withdrawn from the experiment'}"] if a and a.retired else [])
                return "\n".join(tips) or None
            if c == C_STATUS:
                tips = (["The next test to perform on this apparatus"] if t.id in self.ready else
                        ["Not performed yet"] if t.status == "pending" else [])
                if t.replaces:
                    tips.append(f"Re-performs test {t.replaces}")
                return "\n".join(tips) or None
        elif role == Qt.ForegroundRole:
            inactive = t.status in ("excluded", "superseded", "skipped")
            if inactive:
                return QBrush(QColor(GREY_FG))
            if t.id in self.ready:
                return QBrush(QColor(READY_FG))
            if c == C_ANIMAL:
                a = p.get_animal(t.animal_id)
                return QBrush(QColor("#dc2626" if a and a.retired else LINK_FG))
            if c == C_VIDEO and (not t.video or not Path(p.abs_path(t.video)).exists()):
                return QBrush(QColor("#94a3b8" if not t.video else "#dc2626"))
            if c == C_DUR and not t.duration_s:
                return QBrush(QColor("#64748b"))
        elif role == Qt.BackgroundRole:
            if t.id in self.ready:
                return QBrush(QColor(READY_BG))
        elif role == Qt.DecorationRole:
            if c == C_ID:
                inactive = t.status in ("excluded", "superseded", "skipped")
                return self.dot(GREY_FG if inactive else READY_FG if t.id in self.ready else LINK_FG)
            if c == C_GROUP and not p.blind:
                a = p.get_animal(t.animal_id)
                if a and a.group:
                    return swatch(p.group_color(a.group), 10)
        elif role == Qt.FontRole:
            if t.status in ("excluded", "superseded"):
                f = QFont()
                f.setItalic(True)
                if t.status == "superseded":
                    f.setStrikeOut(True)
                return f
        elif role == Qt.TextAlignmentRole:
            if c in (C_START, C_DUR):
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        t = self.test_at(index.row())
        p = self.page.project
        if t is None or role != Qt.EditRole:
            return False
        c = index.column()
        if c == C_ANIMAL:
            aid = str(value).strip()
            if aid == t.animal_id:
                return False
            if aid:
                p.ensure_animal(aid)
            t.animal_id = aid
        elif c == C_STAGE:
            st = str(value).strip()
            if st == t.stage:
                return False
            if st and st not in p.stages:
                p.stages.append(st)
            t.stage = st
        elif c == C_TRIAL:
            t.trial = int(value)
        elif c == C_APP:
            t.apparatus = str(value)
        elif c == C_START:
            t.start_s = float(value)
        elif c == C_DUR:
            t.duration_s = float(value)
        elif c == C_NOTES:
            t.notes = str(value)
        else:
            return False
        ready = ready_tests(p)
        if ready != self.ready:
            self.ready = ready
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.tests) - 1, len(COLUMNS) - 1))
        else:
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        self.page.edited()
        return True


class TestsDelegate(QStyledItemDelegate):
    """Editors of the schedule cells; the status of a Ready test shows ▾ and opens the status commands."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def _ready(self, index) -> bool:
        t = self.page.model.test_at(self.page.proxy.mapToSource(index).row())
        return t is not None and t.id in self.page.model.ready

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.column() == C_STATUS and self._ready(index):
            fm = option.fontMetrics
            x = option.rect.left() + 6 + fm.horizontalAdvance(index.data() or "") + 12
            icon("chevron_down").paint(painter, QRect(x, option.rect.center().y() - 5, 10, 10))

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.MouseButtonRelease and index.column() == C_STATUS and self._ready(index)
                and event.button() == Qt.LeftButton):
            self.page.status_menu(index, event.globalPosition().toPoint())
            return True
        return super().editorEvent(event, model, option, index)

    def createEditor(self, parent, option, index):
        p = self.page.project
        c = index.column()
        if c in (C_ANIMAL, C_STAGE, C_APP):
            w = QComboBox(parent)
            w.setEditable(c != C_APP)
            items = ([a.id for a in p.animals] if c == C_ANIMAL else list(p.stages) if c == C_STAGE
                     else [a.name for a in p.apparatus])
            w.addItems(items)
            return w
        if c == C_TRIAL:
            w = QSpinBox(parent)
            w.setRange(1, wf.MAX_TRIALS)
            return w
        if c in (C_START, C_DUR):
            w = QDoubleSpinBox(parent)
            w.setRange(0, 1e7)
            w.setDecimals(2)
            w.setSpecialValueText("default" if c == C_DUR else "")
            return w
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        v = index.data(Qt.EditRole)
        if isinstance(editor, QComboBox):
            i = editor.findText(str(v))
            if i >= 0:
                editor.setCurrentIndex(i)
            elif editor.isEditable():
                editor.setEditText(str(v))
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            editor.setValue(v or 0)
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.EditRole)
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            editor.interpretText()
            model.setData(index, editor.value(), Qt.EditRole)
        else:
            super().setModelData(editor, model, index)


# ------------------------------------------------------------------ dialogs
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
        self.preview.setStyleSheet("color:#475569;")
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
            seed=self.seed.value() or None, counterbalance=self.cb.currentData() if levels else "", levels=levels,
            variable=self.var_name.currentText().strip(), skip_existing=self.skip.isChecked(),
            skip_retired=self.skip_done.isChecked(), skip_completed=self.skip_done.isChecked())

    def _update_preview(self, *_):
        rows = self.rows()
        head = ", ".join(f"{r['animal_id']}/{r['stage'] or '–'}/{r['trial']}" for r in rows[:6])
        self.preview.setText(f"<b>{len(rows)}</b> test{'s' if len(rows) != 1 else ''} will be created"
                             + (f" — first: {head}{' …' if len(rows) > 6 else ''}" if rows else "."))


def dlc_bodyparts(path) -> list[str]:
    with open(path, newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if row and row[0].strip().lower() == "bodyparts":
                seen = []
                for p in row[1:]:
                    if p not in seen:
                        seen.append(p)
                return seen
            if i > 5:
                break
    return []


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


# ------------------------------------------------------------------ page
class TestsPage(Page):
    """The test schedule, with the ANY-maze ribbon groups Tests, Testing, Status and Variables."""

    title = "Test schedule"

    def __init__(self, main):
        super().__init__(main)
        self._tracking = False
        self.model = TestsModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setSortCaseSensitivity(Qt.CaseInsensitive)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(TestsDelegate(self))
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_ID, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setStyleSheet(
            "QTableView{font-size:14px;border:none;background:white;outline:0;}"
            "QTableView::item{padding:0 6px;border-bottom:1px solid #f0f0f0;}"
            f"QTableView::item:selected{{background:{theme.SELECTION};}}"
            "QHeaderView::section{font-size:14px;font-weight:600;padding:7px 8px;border:none;"
            "border-bottom:1px solid #d6d6d6;background:white;}")
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        hh.setHighlightSections(False)
        hh.setMinimumHeight(36)
        for c, w in ((C_ID, 64), (C_ANIMAL, 96), (C_GROUP, 110), (C_STAGE, 96), (C_TRIAL, 56), (C_APP, 124),
                     (C_VIDEO, 140), (C_STATUS, 132), (C_START, 86), (C_DUR, 116)):
            self.table.setColumnWidth(c, w)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        def act(text, ic, fn, tip="", large=True):
            return ribbon_action(self, text, ic, fn, tip, large=large)

        # Tests
        self.a_add = act("Add tests from videos", "video_file", self.add_from_videos,
                         "Add tests from video files: one test per video (or per apparatus in each video)")
        self.a_add_blank = act("Add test", "add", self.add_blank,
                               "Add a test without video (manual scoring, or recorded live later)")
        self.a_schedule = act("Schedule…", "schedule", self.create_schedule,
                              "Create a test schedule: tests without video for animals × stages × trials, in a "
                              "chosen running order")
        self.a_dup = act("Duplicate", "copy", self.duplicate_selected, "Copy the selected tests", large=False)
        self.a_video = act("Set video file…", "video", self.set_video, "Choose the video of the selected tests",
                           large=False)
        self.a_del = act("Delete", "delete", lambda: self.delete_selected(),
                         "Delete the selected tests and their tracks", large=False)
        # Testing
        self.a_open = act("Review test", "eye", self.open_selected,
                          "Open the test in Review and score (or double-click a test)")
        self.a_track = act("Track selected", "tracking", self.track_selected, "Track the selected tests' videos")
        self.a_track_all = act("Track all untracked", "track", self.track_untracked,
                               "Track every test that has a video but no track yet")
        self.a_import = act("Import track…", "import", self.import_track,
                            "Import a DeepLabCut CSV or a mANY-MAZE track CSV for the selected test", large=False)
        self.a_import_data = act("Import track data…", "import_table", lambda: self.import_track_data(),
                                 "Import positions exported by ANY-maze, EthoVision or other software (any table "
                                 "with time and X / Y columns) for the selected test", large=False)
        # Status
        self.a_skip = act("Skip", "skip", self.skip_selected,
                          "Skip the selected tests for now — they can be resumed later", large=False)
        self.a_resume = act("Resume", "play", self.resume_selected, "Resume the selected skipped tests",
                            large=False)
        self.a_redo = act("Re-perform", "redo", self.reperform_selected,
                          "Run the selected tests again: a new attempt is added and the old one is kept as "
                          "“superseded” (left out of the results)", large=False)
        self.a_excl = act("Exclude", "exclude", self.toggle_exclude,
                          "Exclude / include the selected tests. Excluded tests are left out of results and "
                          "statistics", large=False)
        self.a_clear = act("Clear tracks", "eraser", lambda: self.clear_selected_tracks(),
                           "Delete the tracks of the selected tests (scored events are kept)", large=False)
        # Variables
        self.a_vars = act("Test variables", "variable", self.edit_variables,
                          "Per-test variables: novel object (NOR), social stimulus side (three-chamber)")

        title = QLabel("Test schedule")
        title.setObjectName("PageTitle")
        self.summary = QLabel()
        self.summary.setObjectName("Hint")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.summary, 0, Qt.AlignBottom)
        hint = QLabel("Double-click a test to review it · click a selected cell (or F2) to edit · the next test of "
                      "each apparatus is Ready")
        hint.setObjectName("Hint")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint)
        self._update_actions()

    # ------------------------------------------------------------------ ribbon
    def ribbon_groups(self):
        return [("Tests", [self.a_add, self.a_add_blank, self.a_schedule, (self.a_dup, "small"),
                           (self.a_video, "small"), (self.a_del, "small")]),
                ("Testing", [self.a_open, self.a_track, self.a_track_all, (self.a_import, "small"),
                             (self.a_import_data, "small")]),
                ("Status", [(self.a_skip, "small"), (self.a_resume, "small"), (self.a_redo, "small"),
                            (self.a_excl, "small"), (self.a_clear, "small")]),
                ("Variables", [self.a_vars])]

    # ------------------------------------------------------------------ page API
    def set_project(self, project):
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        sel = set(self.selected_ids())
        self.model.reset()
        self.select_ids(sel)
        self._update_summary()
        self._update_actions()

    def edited(self):
        self.main.mark_dirty()
        self._update_summary()

    def _update_summary(self):
        p = self.project
        if p is None:
            self.summary.setText("")
            return
        n = len(p.tests)
        counts = {k: sum(1 for t in p.tests if t.status == k) for k in STATUS_COLORS}
        no_vid = sum(1 for t in p.tests if not t.video)
        txt = (f"<b>{n}</b> test{'s' if n != 1 else ''} · "
               f"<span style='color:{STATUS_COLORS['tracked']}'>{counts['tracked']} tracked</span> · "
               f"<span style='color:{STATUS_COLORS['pending']}'>{counts['pending']} pending</span>")
        for k in ("scored", "skipped", "superseded", "excluded"):
            if counts[k]:
                txt += f" · <span style='color:{STATUS_COLORS[k]}'>{counts[k]} {k}</span>"
        if no_vid:
            txt += f" · {no_vid} without video"
        self.summary.setText(txt)

    # ------------------------------------------------------------------ selection
    def selected_ids(self) -> list[int]:
        rows = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.tests[r].id for r in rows if r < len(self.model.tests)]

    def selected_tests(self):
        ids = self.selected_ids()
        return [t for t in self.model.tests if t.id in ids]

    def select_ids(self, ids):
        sm = self.table.selectionModel()
        sm.clearSelection()
        first = None
        for r, t in enumerate(self.model.tests):
            if t.id in ids:
                pi = self.proxy.mapFromSource(self.model.index(r, 0))
                sm.select(pi, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                first = first or pi
        if first is not None:
            self.table.scrollTo(first)
            sm.setCurrentIndex(first, QItemSelectionModel.NoUpdate)

    def _update_actions(self, *_):
        has = self.project is not None
        n = len(self.selected_ids()) if has else 0
        for a in (self.a_add, self.a_add_blank, self.a_schedule, self.a_track_all):
            a.setEnabled(has)
        for a in (self.a_dup, self.a_del, self.a_excl, self.a_track, self.a_vars, self.a_redo, self.a_clear,
                  self.a_video):
            a.setEnabled(n > 0)
        self.a_open.setEnabled(n == 1)
        self.a_import.setEnabled(n == 1)
        self.a_import_data.setEnabled(n == 1)
        sel = self.selected_tests() if n else []
        self.a_excl.setText("Include" if sel and all(t.status == "excluded" for t in sel) else "Exclude")
        self.a_skip.setEnabled(any(t.status not in ("skipped", "excluded", "superseded") for t in sel))
        self.a_resume.setEnabled(any(t.status == "skipped" for t in sel))

    def _double_clicked(self, index):
        r = self.proxy.mapToSource(index).row()
        t = self.model.test_at(r)
        if t is not None:
            self.main.open_test(t.id)

    def _context_menu(self, pos):
        if self.project is None:
            return
        m = QMenu(self)
        for a in (self.a_open, self.a_track, self.a_import, self.a_import_data):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_video, self.a_vars, self.a_dup, self.a_skip, self.a_resume, self.a_redo, self.a_clear,
                  self.a_excl, self.a_del):
            m.addAction(a)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def status_menu(self, index, global_pos):
        """The ▾ of a Ready test: its testing-status commands."""
        t = self.model.test_at(self.proxy.mapToSource(index).row())
        if t is None:
            return
        self.select_ids({t.id})
        m = QMenu(self)
        for a in (self.a_open, self.a_skip, self.a_excl, self.a_vars):
            m.addAction(a)
        m.exec(global_pos)

    # ------------------------------------------------------------------ actions
    def _video_filter(self) -> str:
        pats = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS) + " " + " ".join(f"*{e.upper()}" for e in VIDEO_EXTENSIONS)
        return f"Video files ({pats});;All files (*)"

    def _start_dir(self) -> str:
        p = self.project
        for t in reversed(p.tests):
            if t.video:
                d = Path(p.abs_path(t.video)).parent
                if d.exists():
                    return str(d)
        return str(p.path or Path.home())

    def add_from_videos(self):
        p = self.project
        if p is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Add tests from videos", self._start_dir(), self._video_filter())
        if not files:
            return
        dlg = AddVideosDialog(p, files, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.add_tests(dlg.rows())

    def add_tests(self, rows: list[dict]) -> list:
        """Create tests from dicts with keys video, animal_id, apparatus, stage, trial."""
        p = self.project
        new = []
        for r in rows:
            aid = r.get("animal_id", "")
            if aid:
                p.ensure_animal(aid)
            st = r.get("stage", "")
            if st and st not in p.stages:
                p.stages.append(st)
            new.append(p.add_test(r.get("video", ""), aid, r.get("apparatus", ""), stage=st,
                                  trial=int(r.get("trial", 1)), variables=dict(r.get("variables") or {})))
        self.main.mark_dirty()
        self.refresh()
        self.select_ids({t.id for t in new})
        self.main.status(f"Added {len(new)} test{'s' if len(new) != 1 else ''}.")
        return new

    def add_blank(self):
        p = self.project
        if p is None:
            return
        cur = self.selected_tests()
        stage = cur[-1].stage if cur else (p.stages[0] if p.stages else "")
        t = p.add_test("", "", "", stage=stage, trial=1)
        self.main.mark_dirty()
        self.refresh()
        self.select_ids({t.id})
        pi = self.proxy.mapFromSource(self.model.index(len(p.tests) - 1, C_ANIMAL))
        self.table.setCurrentIndex(pi)
        if self.isVisible():
            self.table.edit(pi)

    def create_schedule(self):
        p = self.project
        if p is None:
            return
        if not p.animals:
            QMessageBox.information(self, "Create schedule", "Add animals first (Animals page).")
            return
        dlg = ScheduleDialog(p, self)
        if dlg.exec() != QDialog.Accepted:
            return
        rows = dlg.rows()
        if not rows:
            self.main.status("No new tests to create.")
            return
        self.add_tests(rows)

    def create_tests_for(self, combos, apparatus="", skip_existing=True) -> list:
        p = self.project
        have = {(t.animal_id, t.stage, t.trial) for t in p.tests}
        rows = [{"animal_id": a, "stage": s, "trial": k, "apparatus": apparatus} for a, s, k in combos
                if not (skip_existing and (a, s, k) in have)]
        return self.add_tests(rows) if rows else []

    def edit_variables(self):
        tests = self.selected_tests()
        if self.project is None or not tests:
            return
        dlg = VariablesDialog(self.project, tests, self)
        if dlg.exec() != QDialog.Accepted:
            return
        dlg.apply(tests)
        self.main.mark_dirty()
        self.refresh()

    def duplicate_selected(self):
        p = self.project
        new = []
        for t in self.selected_tests():
            d = dataclasses.asdict(t)
            d.update(id=p.next_test_id(), events=[], status="pending", recorded_at="")
            nt = type(t).from_dict(d)
            p.tests.append(nt)
            new.append(nt)
        if new:
            self.main.mark_dirty()
            self.refresh()
            self.select_ids({t.id for t in new})

    def delete_selected(self, confirm: bool = True):
        p = self.project
        tests = self.selected_tests()
        if not tests:
            return
        if confirm and QMessageBox.question(
                self, "Delete tests", f"Delete {len(tests)} test{'s' if len(tests) != 1 else ''} and "
                "their tracks? Video files are not deleted.") != QMessageBox.Yes:
            return
        for t in tests:
            if p.path is not None:
                for i in range(t.n_animals):
                    tp = p.track_path(t, i)
                    if tp.exists():
                        tp.unlink()
            p.tests.remove(t)
        self.main.mark_dirty()
        self.refresh()

    def toggle_exclude(self):
        p = self.project
        tests = self.selected_tests()
        if not tests:
            return
        include = all(t.status == "excluded" for t in tests)
        for t in tests:
            t.status = wf.data_status(p, t) if include else "excluded"
        self.main.mark_dirty()
        self.refresh()

    def skip_selected(self):
        tests = [t for t in self.selected_tests() if t.status not in ("skipped", "excluded", "superseded")]
        for t in tests:
            wf.skip_test(t)
        self._status_changed(tests, "Skipped")

    def resume_selected(self):
        tests = [t for t in self.selected_tests() if t.status == "skipped"]
        for t in tests:
            wf.resume_test(self.project, t)
        self._status_changed(tests, "Resumed")

    def _status_changed(self, tests, verb):
        if not tests:
            return
        self.main.mark_dirty()
        self.refresh()
        self.main.status(f"{verb} {len(tests)} test{'s' if len(tests) != 1 else ''}.")

    def toggle_skip(self):
        """Skip the selected tests, or resume them when they are all skipped."""
        p = self.project
        tests = self.selected_tests()
        if not tests:
            return
        resume = all(t.status == "skipped" for t in tests)
        for t in tests:
            if resume:
                wf.resume_test(p, t)
            elif t.status not in ("excluded", "superseded"):
                wf.skip_test(t)
        self.main.mark_dirty()
        self.refresh()
        self.main.status(f"{'Resumed' if resume else 'Skipped'} {len(tests)} test{'s' if len(tests) != 1 else ''}.")

    def reperform_selected(self) -> list:
        p = self.project
        tests = [t for t in self.selected_tests() if t.status != "superseded"]
        if not tests:
            return []
        new = [wf.reperform_test(p, t) for t in tests]
        self.main.mark_dirty()
        self.refresh()
        self.select_ids({t.id for t in new})
        self.main.status(f"Added {len(new)} new attempt{'s' if len(new) != 1 else ''}; the previous "
                         f"attempt{'s are' if len(new) != 1 else ' is'} kept as superseded.")
        return new

    def clear_selected_tracks(self, confirm: bool = True) -> int:
        p = self.project
        tests = [t for t in self.selected_tests() if p.has_track(t)]
        if not tests:
            return 0
        if confirm and QMessageBox.question(
                self, "Clear tracks", f"Delete the tracks of {len(tests)} test{'s' if len(tests) != 1 else ''}? "
                "Videos and scored events are kept.") != QMessageBox.Yes:
            return 0
        n = sum(wf.clear_tracks(p, t) for t in tests)
        self.main.mark_dirty()
        self.refresh()
        return n

    def open_selected(self):
        ids = self.selected_ids()
        if ids:
            self.main.open_test(ids[0])

    def set_video(self):
        p = self.project
        tests = self.selected_tests()
        if not tests:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Video for the selected tests", self._start_dir(),
                                           self._video_filter())
        if not f:
            return
        for t in tests:
            t.video = p.rel_path(f)
        self.main.mark_dirty()
        self.refresh()

    # ------------------------------------------------------------------ tracking
    def track_selected(self):
        return self.track_tests(self.selected_tests())

    def track_untracked(self):
        p = self.project
        if p is None:
            return None
        return self.track_tests([t for t in p.tests if t.status in ("pending", "scored") and t.video
                                 and not p.has_track(t)])

    def track_tests(self, tests):
        p = self.project
        if p is None:
            return None
        missing = [t for t in tests if t.video and not Path(p.abs_path(t.video)).exists()]
        tests = [t for t in tests if t.video and t not in missing and t.status not in ("excluded", "superseded")]
        if missing:
            self.main.status(f"Skipping {len(missing)} test(s) whose video file is missing.")
        if not tests:
            QMessageBox.information(self, "Track", "No tests to track (tests need an existing video and must not "
                                    "be excluded).")
            return None
        if p.path is None and not self.main.save():
            return None
        n_batches = len(tracking_batches(p, tests))
        title = f"Tracking {len(tests)} test{'s' if len(tests) != 1 else ''}"
        if n_batches < len(tests):
            title += f" ({n_batches} video pass{'es' if n_batches != 1 else ''})"
        self._tracking = True
        return run_with_progress(self, title, track_tests_job(p, tests), on_done=self._tracking_done,
                                 on_fail=self._tracking_failed)

    def _tracking_done(self, res):
        self._tracking = False
        self.main.save()
        self.refresh()
        self.select_ids(set(res["tracked"]))
        msg = f"Tracked {len(res['tracked'])} test{'s' if len(res['tracked']) != 1 else ''}"
        msg += f" ({res['workers']} videos in parallel)." if res.get("workers", 1) > 1 else "."
        if res["cancelled"]:
            msg += " Cancelled — the remaining tests were left untouched."
        self.main.status(msg)
        if res["errors"]:
            error_box(self, "Tracking", "\n".join(res["errors"]))

    def _tracking_failed(self, msg):
        self._tracking = False
        self.refresh()
        error_box(self, "Tracking", msg)

    # ------------------------------------------------------------------ track import
    def import_track(self):
        tests = self.selected_tests()
        if len(tests) != 1:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Import track (DeepLabCut CSV or mANY-MAZE track CSV)",
                                           self._start_dir(), "CSV files (*.csv);;All files (*)")
        if f:
            self.import_track_file(tests[0], f)

    def import_track_data(self, path: str | None = None):
        """Positions exported by ANY-maze / EthoVision / other software (a table with time and X / Y columns) for
        the selected test, through the import wizard."""
        p = self.project
        tests = self.selected_tests()
        if p is None or len(tests) != 1:
            return None
        if p.path is None and not self.main.save():
            return None
        from ..import_wizard import ImportDialog

        t = tests[0]
        dlg = ImportDialog(p, "track", self, path=path, test=t)
        if dlg.exec() != QDialog.Accepted:
            return None
        self.main.mark_dirty()
        self.refresh()
        self.select_ids({t.id})
        n = len(dlg.result) if dlg.result is not None else 0
        self.main.status(f"Imported {n} positions into test {t.id}.")
        return dlg.result

    def import_track_file(self, test, path, options: dict | None = None) -> Track | None:
        p = self.project
        try:
            parts = dlc_bodyparts(path)
            if parts:
                if options is None:
                    fps = 25.0
                    vid = p.abs_path(test.video) if test.video else ""
                    if vid and Path(vid).exists():
                        try:
                            with VideoSource(vid) as v:
                                fps = v.fps
                        except Exception:
                            pass
                    dlg = DlcImportDialog(parts, fps, self)
                    if dlg.exec() != QDialog.Accepted:
                        return None
                    options = dlg.options()
                opts = dict(options)
                trim = opts.pop("trim", True)
                tr = import_deeplabcut_csv(path, **opts)
                if trim:
                    t0 = test.start_s
                    dur = test.duration_s or p.test_duration_s
                    if t0 > 0 or dur:
                        tr = tr.slice_time(t0, t0 + dur if dur else float("inf"))
                        tr.t = tr.t - t0
                    tr.meta["video_start_s"] = t0
            else:
                tr = Track.from_csv(path)
            if len(tr) == 0:
                raise ValueError("The file contains no samples (for the test period).")
        except Exception as e:
            error_box(self, "Import track", e)
            return None
        if p.path is None and not self.main.save():
            return None
        p.save_tracks(test, [tr])
        self.main.save()
        self.refresh()
        self.main.status(f"Imported {len(tr)} samples into test {test.id}.")
        return tr
