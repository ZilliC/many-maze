"""Experiment page: protocol, test timing, stages, behaviours, detection and analysis defaults."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.project import Behaviour
from ...core.templates import TEMPLATES
from ...core import pose
from ..pose_model import PoseModelBox
from .base import ANALYSIS_SPEC, DETECTION_SPEC, Page, SettingsForm


class ExperimentPage(Page):
    title = "Experiment"

    def __init__(self, main):
        super().__init__(main)
        self._loading = False
        inner = QWidget()
        cols = QHBoxLayout(inner)

        # ---- left column: general -------------------------------------------
        left = QVBoxLayout()
        gen = QGroupBox("Experiment")
        f = QFormLayout(gen)
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._store)
        self.desc = QPlainTextEdit()
        self.desc.setMaximumHeight(80)
        self.desc.textChanged.connect(self._store)
        self.protocol = QComboBox()
        for k, t in TEMPLATES.items():
            self.protocol.addItem(t.title, k)
        self.protocol.currentIndexChanged.connect(self._store)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 1e6)
        self.duration.setSuffix(" s")
        self.duration.setToolTip("Test duration. 0 = until the end of the video.")
        self.duration.valueChanged.connect(self._store)
        self.start_mode = QComboBox()
        self.start_mode.addItem("At the test start time set for each test", "manual")
        self.start_mode.addItem("When the animal is first detected in the apparatus", "on_detection")
        self.start_mode.currentIndexChanged.connect(self._store)
        f.addRow("Name", self.name)
        f.addRow("Description", self.desc)
        f.addRow("Protocol", self.protocol)
        f.addRow("Test duration", self.duration)
        f.addRow("Test starts", self.start_mode)
        left.addWidget(gen)

        st = QGroupBox("Stages (e.g. days, sessions)")
        sl = QVBoxLayout(st)
        self.stages = QPlainTextEdit()
        self.stages.setPlaceholderText("One stage per line, e.g.\nHabituation\nTraining day 1\nProbe")
        self.stages.setMaximumHeight(90)
        self.stages.textChanged.connect(self._store)
        sl.addWidget(self.stages)
        left.addWidget(st)

        bh = QGroupBox("Manually scored behaviours")
        bl = QVBoxLayout(bh)
        self.beh = QTableWidget(0, 3)
        self.beh.setHorizontalHeaderLabels(["Behaviour", "Key", "Type"])
        self.beh.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.beh.setColumnWidth(1, 50)
        self.beh.setColumnWidth(2, 170)
        self.beh.verticalHeader().hide()
        self.beh.itemChanged.connect(self._store_behaviours)
        bb = QHBoxLayout()
        add = QPushButton("Add behaviour")
        add.clicked.connect(self._add_behaviour)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove_behaviour)
        bb.addWidget(add)
        bb.addWidget(rm)
        bb.addStretch()
        bl.addWidget(self.beh)
        bl.addLayout(bb)
        left.addWidget(bh)

        per = QGroupBox("Custom analysis periods (optional, override time bins)")
        pl = QVBoxLayout(per)
        self.periods = QTableWidget(0, 3)
        self.periods.setHorizontalHeaderLabels(["Label", "Start (s)", "End (s)"])
        self.periods.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.periods.verticalHeader().hide()
        self.periods.itemChanged.connect(self._store_periods)
        pb = QHBoxLayout()
        padd = QPushButton("Add period")
        padd.clicked.connect(self._add_period)
        prm = QPushButton("Remove")
        prm.clicked.connect(lambda: self._remove_row(self.periods, self._store_periods))
        pb.addWidget(padd)
        pb.addWidget(prm)
        pb.addStretch()
        pl.addWidget(self.periods)
        pl.addLayout(pb)
        left.addWidget(per)
        left.addStretch()

        # ---- middle: detection --------------------------------------------------
        det = QGroupBox("Default detection settings")
        dl = QVBoxLayout(det)
        self.det_form = SettingsForm(DETECTION_SPEC)
        self.det_form.changed.connect(self.main.mark_dirty)
        self.det_form.changed.connect(self._update_pose_box)
        dl.addWidget(self.det_form)
        self.pose_box = PoseModelBox()
        self.pose_box.changed.connect(self.main.mark_dirty)
        dl.addWidget(self.pose_box)
        dl.addStretch()

        # ---- right: analysis ---------------------------------------------------
        an = QGroupBox("Analysis settings")
        al = QVBoxLayout(an)
        self.an_form = SettingsForm(ANALYSIS_SPEC)
        self.an_form.changed.connect(self.main.mark_dirty)
        al.addWidget(self.an_form)
        al.addStretch()

        cols.addLayout(left, 1)
        cols.addWidget(det, 1)
        cols.addWidget(an, 1)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

    # ------------------------------------------------------------------
    def set_project(self, project):
        self.on_show()

    def on_show(self):
        p = self.project
        self.pose_box.set_settings(p.detection if p is not None else None)
        if p is None:
            return
        self._loading = True
        self.name.setText(p.name)
        self.desc.setPlainText(p.description)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(p.protocol)))
        self.duration.setValue(p.test_duration_s)
        self.start_mode.setCurrentIndex(max(0, self.start_mode.findData(p.start_mode)))
        self.stages.setPlainText("\n".join(p.stages))
        self.beh.setRowCount(0)
        for b in p.behaviours:
            self._append_behaviour_row(b)
        self.periods.setRowCount(0)
        for lbl, a, b in p.analysis.custom_periods:
            self._append_period_row(lbl, a, b)
        self.det_form.load(p.detection)
        self.an_form.load(p.analysis)
        self._loading = False

    def _update_pose_box(self):
        self.pose_box.refresh()
        if self.project is not None and self.project.detection.body_parts == "pose" \
                and self.project.detection.pose_model in pose.MODELS \
                and not pose.is_installed(self.project.detection.pose_model):
            self.main.status("The pose model is not installed yet — press Install… under the detection settings.")

    def _store(self, *_):
        if self._loading or self.project is None:
            return
        p = self.project
        p.name = self.name.text().strip() or p.name
        p.description = self.desc.toPlainText()
        p.protocol = self.protocol.currentData()
        p.test_duration_s = self.duration.value()
        p.start_mode = self.start_mode.currentData()
        p.stages = [s.strip() for s in self.stages.toPlainText().splitlines() if s.strip()]
        self.main.mark_dirty()
        self.main.update_title()

    # ---- behaviours ----------------------------------------------------------
    def _append_behaviour_row(self, b: Behaviour):
        self._loading = True
        r = self.beh.rowCount()
        self.beh.insertRow(r)
        self.beh.setItem(r, 0, QTableWidgetItem(b.name))
        self.beh.setItem(r, 1, QTableWidgetItem(b.key))
        kind = QComboBox()
        kind.addItem("State (has duration)", "state")
        kind.addItem("Point (instant)", "point")
        kind.setCurrentIndex(0 if b.kind == "state" else 1)
        kind.currentIndexChanged.connect(self._store_behaviours)
        self.beh.setCellWidget(r, 2, kind)
        self._loading = False

    def _add_behaviour(self):
        used = {b.key for b in self.project.behaviours}
        key = next((c for c in "123456789qwertyuiop" if c not in used), "")
        self._append_behaviour_row(Behaviour(f"Behaviour {self.beh.rowCount() + 1}", key, "state"))
        self._store_behaviours()

    def _remove_behaviour(self):
        self._remove_row(self.beh, self._store_behaviours)

    def _remove_row(self, table, after):
        r = table.currentRow()
        if r >= 0:
            table.removeRow(r)
            after()

    def _store_behaviours(self, *_):
        if self._loading or self.project is None:
            return
        out = []
        for r in range(self.beh.rowCount()):
            name = self.beh.item(r, 0).text().strip() if self.beh.item(r, 0) else ""
            key = self.beh.item(r, 1).text().strip()[:1].lower() if self.beh.item(r, 1) else ""
            kind = self.beh.cellWidget(r, 2).currentData() if self.beh.cellWidget(r, 2) else "state"
            if name:
                out.append(Behaviour(name, key, kind))
        self.project.behaviours = out
        self.main.mark_dirty()

    # ---- periods -----------------------------------------------------------------
    def _append_period_row(self, lbl, a, b):
        self._loading = True
        r = self.periods.rowCount()
        self.periods.insertRow(r)
        for c, v in enumerate((lbl, a, b)):
            it = QTableWidgetItem(str(v))
            if c:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.periods.setItem(r, c, it)
        self._loading = False

    def _add_period(self):
        n = self.periods.rowCount()
        last_end = 0.0
        if n:
            try:
                last_end = float(self.periods.item(n - 1, 2).text())
            except (ValueError, AttributeError):
                pass
        self._append_period_row(f"Period {n + 1}", last_end, last_end + 60)
        self._store_periods()

    def _store_periods(self, *_):
        if self._loading or self.project is None:
            return
        out = []
        for r in range(self.periods.rowCount()):
            try:
                lbl = self.periods.item(r, 0).text()
                a = float(self.periods.item(r, 1).text())
                b = float(self.periods.item(r, 2).text())
            except (ValueError, AttributeError):
                continue
            if b > a:
                out.append([lbl, a, b])
        self.project.analysis.custom_periods = out
        self.main.mark_dirty()
