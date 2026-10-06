"""Tests page: the list of tests (animal × stage × trial × video), batch tracking and track import."""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QSpinBox, QStyle, QStyledItemDelegate,
                               QTableView, QTableWidget, QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout)

from ...core.track import Track, import_deeplabcut_csv
from ...core.video import VIDEO_EXTENSIONS, VideoSource
from ..widgets import error_box, run_with_progress
from .base import Page

COLUMNS = ["ID", "Animal", "Group", "Stage", "Trial", "Apparatus", "Video", "Start (s)", "Duration (s)", "Status",
           "Notes"]
C_ID, C_ANIMAL, C_GROUP, C_STAGE, C_TRIAL, C_APP, C_VIDEO, C_START, C_DUR, C_STATUS, C_NOTES = range(len(COLUMNS))
EDITABLE = {C_ANIMAL, C_STAGE, C_TRIAL, C_APP, C_START, C_DUR, C_NOTES}
SORT_ROLE = Qt.UserRole + 1
STATUS_COLORS = {"pending": "#d97706", "tracked": "#16a34a", "excluded": "#94a3b8"}


# ------------------------------------------------------------------ tracking jobs
class TrackingCancelled(Exception):
    pass


def _stopper(should_stop):
    """Wrap should_stop so that cancelling aborts tracking before partial tracks are saved."""

    def check():
        if should_stop and should_stop():
            raise TrackingCancelled()
        return False

    return check


def tracking_batches(project, tests) -> list[list]:
    """Group tests that share a video and time window so they are tracked in one pass."""
    batches: dict[tuple, list] = {}
    order = []
    for t in tests:
        if not t.video:
            continue
        s = project.detection_for(t)
        if project.start_mode == "on_detection":
            key = ("single", t.id)
        else:
            key = (project.abs_path(t.video), round(s.start_time_s, 4), round(s.duration_s, 4), s.frame_step)
        if key not in batches:
            batches[key] = []
            order.append(key)
        batches[key].append(t)
    return [batches[k] for k in order]


def track_tests_job(project, tests):
    """Return fn(progress, should_stop) tracking tests in a background thread.

    The result is a dict {"tracked": [test ids], "cancelled": bool, "errors": [str]}.
    """
    batches = tracking_batches(project, tests)

    def run(progress, should_stop):
        out = {"tracked": [], "cancelled": False, "errors": []}
        stop = _stopper(should_stop)
        n = max(1, len(batches))
        for bi, batch in enumerate(batches):
            def prog(f, bi=bi):
                progress((bi + min(1.0, f)) / n)

            try:
                if len(batch) == 1:
                    project.track_test(batch[0], prog, stop)
                else:
                    project.track_video_tests(batch, prog, stop)
                out["tracked"].extend(t.id for t in batch)
            except TrackingCancelled:
                out["cancelled"] = True
                break
            except Exception as e:  # keep going with the other videos
                out["errors"].append(f"Test {', '.join(str(t.id) for t in batch)}: {type(e).__name__}: {e}")
        progress(1.0)
        return out

    return run


# ------------------------------------------------------------------ model
class TestsModel(QAbstractTableModel):
    def __init__(self, page):
        super().__init__()
        self.page = page

    @property
    def tests(self):
        p = self.page.project
        return p.tests if p is not None else []

    def reset(self):
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tests)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
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
                return a.group if a else ""
            if c == C_STAGE:
                return t.stage
            if c == C_TRIAL:
                return t.trial
            if c == C_APP:
                return t.apparatus
            if c == C_VIDEO:
                return Path(t.video).name if t.video else ("" if role != Qt.DisplayRole else "— no video —")
            if c == C_START:
                return t.start_s if role != Qt.DisplayRole else f"{t.start_s:.2f}"
            if c == C_DUR:
                if role == Qt.DisplayRole:
                    return f"{t.duration_s:g}" if t.duration_s else f"default ({p.test_duration_s:g})"
                return t.duration_s
            if c == C_STATUS:
                return t.status
            if c == C_NOTES:
                return t.notes
        elif role == Qt.ToolTipRole:
            if c == C_VIDEO and t.video:
                full = p.abs_path(t.video)
                return full if Path(full).exists() else f"{full}\n(file not found)"
            if c == C_DUR:
                return "Test duration in seconds. 0 = experiment default."
            if c == C_ANIMAL and t.extra_animals:
                return "Also in this test: " + ", ".join(t.extra_animals)
        elif role == Qt.ForegroundRole:
            if t.status == "excluded":
                return QBrush(QColor(STATUS_COLORS["excluded"]))
            if c == C_STATUS:
                return QBrush(QColor(STATUS_COLORS.get(t.status, "#334155")))
            if c == C_VIDEO and (not t.video or not Path(p.abs_path(t.video)).exists()):
                return QBrush(QColor("#94a3b8" if not t.video else "#dc2626"))
            if c == C_DUR and not t.duration_s:
                return QBrush(QColor("#64748b"))
            if c == C_GROUP:
                a = p.get_animal(t.animal_id)
                if a and a.group:
                    return QBrush(QColor(p.group_color(a.group)))
        elif role == Qt.FontRole:
            if c == C_STATUS:
                f = QFont()
                f.setBold(True)
                return f
            if t.status == "excluded":
                f = QFont()
                f.setItalic(True)
                return f
        elif role == Qt.TextAlignmentRole:
            if c in (C_ID, C_TRIAL, C_START, C_DUR):
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
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        self.page.edited()
        return True


class TestsDelegate(QStyledItemDelegate):
    def __init__(self, page):
        super().__init__(page)
        self.page = page

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
            w.setRange(0, 100000)
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
        self.trial.setRange(0, 100000)
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
    """Create tests (without video) for animals × stages × trials."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create test schedule")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Creates one test without video for every animal × stage × trial. Videos can be "
                             "assigned later, or the tests recorded live."))
        row = QHBoxLayout()
        self.animals = self._checklist([(a.id, f"{a.id}  ({a.group})" if a.group else a.id) for a in project.animals])
        stages = project.stages or [""]
        self.stages = self._checklist([(s, s or "(no stage)") for s in stages])
        for title, w in (("Animals", self.animals), ("Stages", self.stages)):
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{title}</b>"))
            col.addWidget(w)
            row.addLayout(col)
        lay.addLayout(row, 1)
        f = QFormLayout()
        self.trials = QSpinBox()
        self.trials.setRange(1, 1000)
        self.app = QComboBox()
        self.app.addItems([a.name for a in project.apparatus])
        self.skip = QCheckBox("Skip combinations that already have a test")
        self.skip.setChecked(True)
        f.addRow("Trials per stage", self.trials)
        f.addRow("Apparatus", self.app)
        f.addRow("", self.skip)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(520, 420)

    @staticmethod
    def _checklist(items):
        w = QListWidget()
        for v, label in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            w.addItem(it)
        return w

    @staticmethod
    def _checked(w):
        return [w.item(i).data(Qt.UserRole) for i in range(w.count()) if w.item(i).checkState() == Qt.Checked]

    def combos(self):
        return [(a, s, k + 1) for a in self._checked(self.animals) for s in self._checked(self.stages)
                for k in range(self.trials.value())]


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
    title = "Tests"

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
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in ((C_ID, 50), (C_ANIMAL, 110), (C_GROUP, 100), (C_STAGE, 120), (C_TRIAL, 55), (C_APP, 130),
                     (C_VIDEO, 190), (C_START, 75), (C_DUR, 110), (C_STATUS, 85)):
            self.table.setColumnWidth(c, w)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        st = self.style()
        tb = QToolBar()
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        def act(text, fn, icon=None, tip=""):
            a = QAction(st.standardIcon(icon), text, self) if icon is not None else QAction(text, self)
            a.triggered.connect(fn)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        self.a_add = act("Add videos…", self.add_from_videos, QStyle.SP_DialogOpenButton,
                         "Add tests from video files: one test per video (or per apparatus in each video)")
        self.a_add_blank = act("Add test", self.add_blank, QStyle.SP_FileIcon,
                               "Add a test without video (manual scoring, or recorded live later)")
        self.a_schedule = act("Schedule…", self.create_schedule, None,
                              "Create a test schedule: tests without video for animals × stages × trials")
        tb.addSeparator()
        self.a_open = act("Open", self.open_selected, None, "Review this test in the Test view (double-click)")
        self.a_dup = act("Duplicate", self.duplicate_selected, None)
        self.a_vars = act("Variables…", self.edit_variables, None,
                          "Per-test variables: novel object (NOR), social stimulus side (three-chamber)")
        self.a_excl = act("Exclude", self.toggle_exclude, None,
                          "Exclude / include the selected tests. Excluded tests are left out of results and statistics")
        self.a_del = act("Delete", self.delete_selected, None)
        tb.addSeparator()
        self.a_import = act("Import track…", self.import_track, None,
                            "Import a DeepLabCut CSV or a mANY-MAZE track CSV for the selected test")
        self.a_track = act("Track selected", self.track_selected, QStyle.SP_MediaPlay)
        self.a_track_all = act("Track all untracked", self.track_untracked, QStyle.SP_MediaSeekForward)
        # make the main actions stand out
        for a in (self.a_track, self.a_track_all):
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton):
                f = w.font()
                f.setBold(True)
                w.setFont(f)

        self.summary = QLabel()
        self.summary.setStyleSheet("color:#475569;padding:2px 4px;")
        hint = QLabel("Double-click a test to review it · click a selected cell (or F2) to edit")
        hint.setStyleSheet("color:#94a3b8;")
        bottom = QHBoxLayout()
        bottom.addWidget(self.summary)
        bottom.addStretch()
        bottom.addWidget(hint)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(tb)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)
        self._update_actions()

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
        counts = {k: sum(1 for t in p.tests if t.status == k) for k in ("tracked", "pending", "excluded")}
        no_vid = sum(1 for t in p.tests if not t.video)
        txt = (f"<b>{n}</b> test{'s' if n != 1 else ''} · "
               f"<span style='color:{STATUS_COLORS['tracked']}'>{counts['tracked']} tracked</span> · "
               f"<span style='color:{STATUS_COLORS['pending']}'>{counts['pending']} pending</span>")
        if counts["excluded"]:
            txt += f" · {counts['excluded']} excluded"
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
        for a in (self.a_dup, self.a_del, self.a_excl, self.a_track, self.a_vars):
            a.setEnabled(n > 0)
        self.a_open.setEnabled(n == 1)
        self.a_import.setEnabled(n == 1)
        sel = self.selected_tests() if n else []
        self.a_excl.setText("Include" if sel and all(t.status == "excluded" for t in sel) else "Exclude")

    def _double_clicked(self, index):
        r = self.proxy.mapToSource(index).row()
        t = self.model.test_at(r)
        if t is not None:
            self.main.open_test(t.id)

    def _context_menu(self, pos):
        if self.project is None:
            return
        m = QMenu(self)
        for a in (self.a_open, self.a_track, self.a_import):
            m.addAction(a)
        m.addSeparator()
        sv = m.addAction("Set video file…", self.set_video)
        sv.setEnabled(len(self.selected_ids()) >= 1)
        for a in (self.a_vars, self.a_dup, self.a_excl, self.a_del):
            m.addAction(a)
        m.exec(self.table.viewport().mapToGlobal(pos))

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
                                  trial=int(r.get("trial", 1))))
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
        self.create_tests_for(dlg.combos(), dlg.app.currentText(), dlg.skip.isChecked())

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
            t.status = ("tracked" if p.has_track(t) else "pending") if include else "excluded"
        self.main.mark_dirty()
        self.refresh()

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
        return self.track_tests([t for t in p.tests if t.status == "pending" and t.video])

    def track_tests(self, tests):
        p = self.project
        if p is None:
            return None
        missing = [t for t in tests if t.video and not Path(p.abs_path(t.video)).exists()]
        tests = [t for t in tests if t.video and t not in missing and t.status != "excluded"]
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
        msg = f"Tracked {len(res['tracked'])} test{'s' if len(res['tracked']) != 1 else ''}."
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
