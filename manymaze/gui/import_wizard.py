"""Import wizard for spreadsheets exported by ANY-maze (or other software): animals, test schedules, track data."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import importers as imp
from .widgets import error_box, hint

KINDS = {
    "animals": ("Import animals", imp.ANIMAL_ROLES,
                {"id": "Animal ID", "group": "Treatment", "sex": "Sex"}),
    "tests": ("Import tests (test schedule)", imp.TEST_ROLES,
              {"test": "Test number", "animal": "Animal ID", "group": "Treatment", "stage": "Stage", "trial": "Trial",
               "apparatus": "Apparatus", "video": "Video file", "duration": "Test duration"}),
    "track": ("Import track data", imp.TRACK_ROLES,
              {"t": "Time", "x": "Centre X", "y": "Centre Y", "hx": "Head X", "hy": "Head Y", "tx": "Tail X",
               "ty": "Tail Y"}),
}
FILTER = "Spreadsheets (*.csv *.txt *.tsv *.xlsx);;All files (*)"


class ImportDialog(QDialog):
    """Choose a file, check the column mapping (guessed from the header) and import."""

    def __init__(self, project, kind: str, parent=None, path: str | None = None, test=None):
        super().__init__(parent)
        self.project, self.kind, self.test = project, kind, test
        title, self.roles, self.labels = KINDS[kind]
        self.setWindowTitle(title)
        self.resize(820, 620)
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self.result = None
        lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setObjectName("PageTitle")
        lay.addWidget(head)
        lay.addWidget(hint("Choose a spreadsheet saved from ANY-maze (File ▸ Save / Copy of a spreadsheet as CSV, text "
                           "or Excel) or from other software. Columns are matched by name — check the mapping below."))
        row = QHBoxLayout()
        self.path = QLineEdit(path or "")
        self.path.setReadOnly(True)
        browse = QPushButton("Choose file…")
        browse.clicked.connect(self.choose)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview.setMaximumHeight(190)
        self.preview.verticalHeader().hide()
        lay.addWidget(self.preview)
        cols = QHBoxLayout()
        self.form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for role in self.roles:
            cb = QComboBox()
            cb.setMinimumWidth(240)
            self.combos[role] = cb
            self.form.addRow(f"{self.labels.get(role, role)} column", cb)
        left = QWidget()
        left.setLayout(self.form)
        cols.addWidget(left, 1)
        self.extra = QFormLayout()
        right = QWidget()
        right.setLayout(self.extra)
        cols.addWidget(right, 1)
        lay.addLayout(cols)
        if kind == "animals":
            self.fields = QListWidget()
            self.fields.setMaximumHeight(120)
            self.extra.addRow("Also import as animal fields", self.fields)
        elif kind == "tests":
            self.video_dir = QLineEdit()
            self.video_dir.setPlaceholderText("folder of the videos (if the file names are relative)")
            self.extra.addRow("Video folder", self.video_dir)
        else:
            self.scale = QDoubleSpinBox()
            self.scale.setRange(1e-6, 1e6)
            self.scale.setDecimals(4)
            self.scale.setValue(1.0)
            self.scale.setToolTip("Pixels per position unit (e.g. 1000 if positions are in metres and the video has "
                                  "1000 px per metre; 1 if positions are already pixels)")
            self.ox, self.oy = QDoubleSpinBox(), QDoubleSpinBox()
            for w in (self.ox, self.oy):
                w.setRange(-1e6, 1e6)
            self.flip = QCheckBox("Y axis points up (mirror)")
            self.height = QDoubleSpinBox()
            self.height.setRange(0, 1e6)
            self.fps = QDoubleSpinBox()
            self.fps.setRange(0, 1000)
            self.fps.setToolTip("Only needed when the table has no time column")
            self.extra.addRow("Pixels per unit", self.scale)
            self.extra.addRow("X offset (px)", self.ox)
            self.extra.addRow("Y offset (px)", self.oy)
            self.extra.addRow(self.flip)
            self.extra.addRow("Image height (px)", self.height)
            self.extra.addRow("Frame rate if no time column", self.fps)
            if test is not None and project is not None:
                app = project.get_apparatus(test.apparatus)
                if app is not None and app.frame_size:
                    self.height.setValue(app.frame_size[1])
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if path:
            self.load(path)

    def choose(self):
        p, _ = QFileDialog.getOpenFileName(self, self.windowTitle(), str(Path.home()), FILTER)
        if p:
            self.load(p)

    def load(self, path: str):
        try:
            self.header, self.rows = imp.read_table(path)
        except Exception as e:
            error_box(self, self.windowTitle(), e)
            return
        self.path.setText(path)
        self.preview.setColumnCount(len(self.header))
        self.preview.setHorizontalHeaderLabels(self.header)
        self.preview.setRowCount(min(8, len(self.rows)))
        for r, row in enumerate(self.rows[:8]):
            for c, v in enumerate(row):
                self.preview.setItem(r, c, QTableWidgetItem(v))
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        guess = imp.guess_mapping(self.header, self.roles)
        for role, cb in self.combos.items():
            cb.clear()
            cb.addItem("— not imported —", None)
            for i, h in enumerate(self.header):
                cb.addItem(h, i)
            cb.setCurrentIndex(cb.findData(guess.get(role)) if guess.get(role) is not None else 0)
        if self.kind == "animals":
            self.fields.clear()
            mapped = {v for v in guess.values() if v is not None}
            for i, h in enumerate(self.header):
                if i in mapped or h.lower() in ("animal", "status"):
                    continue
                it = QListWidgetItem(h)
                it.setData(Qt.UserRole, i)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.fields.addItem(it)

    def mapping(self) -> dict:
        return {role: cb.currentData() for role, cb in self.combos.items()}

    def run_import(self):
        m = self.mapping()
        p = self.project
        if self.kind == "animals":
            extra = [self.fields.item(i).data(Qt.UserRole) for i in range(self.fields.count())
                     if self.fields.item(i).checkState() == Qt.Checked]
            return imp.import_animals(p, self.header, self.rows, m, extra)
        if self.kind == "tests":
            return imp.import_tests(p, self.header, self.rows, m, self.video_dir.text().strip() or None)
        tr = imp.import_track(self.header, self.rows, m, self.scale.value(), (self.ox.value(), self.oy.value()),
                              self.flip.isChecked(), self.height.value(), self.fps.value() or None)
        if self.test is not None:
            tr.meta["video_start_s"] = self.test.start_s
            p.save_tracks(self.test, [tr])
        return tr

    def accept(self):
        if not self.rows:
            error_box(self, self.windowTitle(), "Choose a file with a header row and data first.")
            return
        try:
            self.result = self.run_import()
        except Exception as e:
            error_box(self, self.windowTitle(), e)
            return
        super().accept()
