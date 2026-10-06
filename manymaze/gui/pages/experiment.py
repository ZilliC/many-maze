"""Experiment page: protocol, test timing, stages, behaviours, detection and analysis defaults."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import workflow as wf
from ...core.periods import ANCHORS
from ...core.project import Behaviour
from ...core.templates import TEMPLATES
from ...core import pose
from ..pose_model import PoseModelBox
from .base import ANALYSIS_SPEC, DETECTION_SPEC, Page, SettingsForm


KIND_LABELS = [("state", "State (key toggles on/off)"), ("hold", "Hold (while key is down)"),
               ("point", "Point (instant)")]
BEH_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16", "#f97316",
              "#06b6d4"]
MET_ACTIONS = [("complete_stage", "Stage completed: skip remaining trials"), ("report", "Report only")]


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
        self.stages_lbl = QLabel()
        self.stages_lbl.setStyleSheet("color:#dc2626;")
        self.stages_lbl.hide()
        sl.addWidget(self.stages_lbl)
        left.addWidget(st)

        bh = QGroupBox("Manually scored behaviours")
        bl = QVBoxLayout(bh)
        self.beh = QTableWidget(0, 5)
        self.beh.setHorizontalHeaderLabels(["Behaviour", "Key", "Type", "Exclusive set", "Colour"])
        self.beh.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.beh.setColumnWidth(1, 40)
        self.beh.setColumnWidth(2, 190)
        self.beh.setColumnWidth(3, 95)
        self.beh.setColumnWidth(4, 52)
        self.beh.verticalHeader().hide()
        self.beh.setToolTip("Key: a letter, digit or punctuation key (up to 46 keys). Exclusive set: behaviours with "
                            "the same set name cannot overlap — starting one stops the others.")
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
        self.beh_lbl = QLabel()
        self.beh_lbl.setWordWrap(True)
        self.beh_lbl.setStyleSheet("color:#dc2626;")
        self.beh_lbl.hide()
        bl.addWidget(self.beh_lbl)
        bl.addLayout(bb)
        left.addWidget(bh)

        wfb = QGroupBox("Testing workflow")
        wl = QVBoxLayout(wfb)
        self.blind = QCheckBox("Blind testing — hide treatment groups (shown as codes) while testing and scoring")
        self.blind.toggled.connect(self._blind_toggled)
        self.confirm_id = QCheckBox("Confirm the animal's ID (barcode / microchip scan or typed) before each test")
        self.confirm_id.toggled.connect(self._store_workflow)
        wl.addWidget(self.blind)
        wl.addWidget(self.confirm_id)
        left.addWidget(wfb)

        cr = QGroupBox("Training criteria")
        cl = QVBoxLayout(cr)
        chint = QLabel("A stage is completed when a result measure meets the condition on N consecutive trials; "
                       "animals that have not met it after the given number of trials can be retired. Apply the "
                       "criteria from the Animals page.")
        chint.setWordWrap(True)
        chint.setStyleSheet("color:#475569;")
        cl.addWidget(chint)
        self.crit = QTableWidget(0, 7)
        self.crit.setHorizontalHeaderLabels(["Stage", "Measure", "Op", "Value", "Consecutive", "When met",
                                             "Retire after"])
        self.crit.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c, wd in ((0, 100), (2, 52), (3, 64), (4, 78), (5, 150), (6, 84)):
            self.crit.setColumnWidth(c, wd)
        self.crit.verticalHeader().hide()
        self.crit.setMaximumHeight(150)
        self.crit.itemChanged.connect(self._store_criteria)
        cb = QHBoxLayout()
        cadd = QPushButton("Add criterion")
        cadd.clicked.connect(self._add_criterion)
        crm = QPushButton("Remove")
        crm.clicked.connect(lambda: self._remove_row(self.crit, self._store_criteria))
        cb.addWidget(cadd)
        cb.addWidget(crm)
        cb.addStretch()
        cl.addWidget(self.crit)
        cl.addLayout(cb)
        left.addWidget(cr)

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

        ev = QGroupBox("Event-anchored periods (e.g. the 30 s after first leaving the start box)")
        evl = QVBoxLayout(ev)
        self.ev_periods = QTableWidget(0, 6)
        self.ev_periods.setHorizontalHeaderLabels(["Label", "Anchor", "Zone / event / input", "Offset (s)",
                                                   "Duration (s)", "Occurrence"])
        self.ev_periods.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.ev_periods.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.ev_periods.verticalHeader().hide()
        self.ev_periods.setToolTip("Duration 0 = until the end of the test. Occurrence: 1 = first, 2 = second…, "
                                   "0 = one period for every occurrence. Periods whose event never happens are "
                                   "left out.")
        self.ev_periods.itemChanged.connect(self._store_event_periods)
        eb = QHBoxLayout()
        eadd = QPushButton("Add event period")
        eadd.clicked.connect(self._add_event_period)
        erm = QPushButton("Remove")
        erm.clicked.connect(lambda: self._remove_row(self.ev_periods, self._store_event_periods))
        eb.addWidget(eadd)
        eb.addWidget(erm)
        eb.addStretch()
        evl.addWidget(self.ev_periods)
        evl.addLayout(eb)
        left.addWidget(ev)
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
        hw = QGroupBox("Hardware (live tests)")
        hl = QVBoxLayout(hw)
        self.hw_lbl = QLabel()
        self.hw_lbl.setWordWrap(True)
        self.hw_lbl.setStyleSheet("color:#64748b;")
        hb = QHBoxLayout()
        io_btn = QPushButton("I/O devices…")
        io_btn.setToolTip("Arduino boards (see firmware/), serial devices, audio and simulated devices used by "
                          "procedures: levers, nose pokes, lights, pellet dispensers, shockers, lasers, wheels…")
        io_btn.clicked.connect(self.edit_io_devices)
        ts_btn = QPushButton("Touch screen…")
        ts_btn.clicked.connect(self.edit_touchscreen)
        hb.addWidget(io_btn)
        hb.addWidget(ts_btn)
        hl.addWidget(self.hw_lbl)
        hl.addLayout(hb)
        dl.addWidget(hw)
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
        self._update_hardware()
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
        self._validate_behaviours()
        self.blind.setChecked(p.blind)
        self.confirm_id.setChecked(wf.confirm_id_enabled(p))
        self.crit.setRowCount(0)
        for c in p.training_criteria:
            self._append_criterion_row(c)
        self.periods.setRowCount(0)
        for lbl, a, b in p.analysis.custom_periods:
            self._append_period_row(lbl, a, b)
        self.ev_periods.setRowCount(0)
        for d in p.analysis.event_periods:
            self._append_event_period_row(d)
        self.det_form.load(p.detection)
        self.an_form.load(p.analysis)
        self._loading = False

    def _update_hardware(self):
        p = self.project
        if p is None:
            self.hw_lbl.setText("")
            return
        devs = ", ".join(f"{d.get('name', '?')} ({d.get('type', '?')})" for d in p.io_devices) or "none"
        ts = "on" if (p.settings_extra.get("touchscreen") or {}).get("enabled") else "off"
        self.hw_lbl.setText(f"I/O devices: {devs}. Touch screen: {ts}. Procedures are edited on the Live "
                            "testing page.")

    def edit_io_devices(self):
        if self.project is None:
            return None
        from ..procedure_editor import IODevicesDialog
        dlg = IODevicesDialog(self.project, self)
        dlg.changed.connect(self.main.mark_dirty)
        dlg.exec()
        self._update_hardware()
        return dlg

    def edit_touchscreen(self):
        if self.project is None:
            return None
        from ..touchscreen import TouchScreenDialog
        dlg = TouchScreenDialog(self.project, self)
        if dlg.exec():
            self.main.mark_dirty()
        self._update_hardware()
        return dlg

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
        stages = [s.strip() for s in self.stages.toPlainText().splitlines() if s.strip()]
        self.stages_lbl.setVisible(len(stages) > wf.MAX_STAGES)
        self.stages_lbl.setText(f"At most {wf.MAX_STAGES} stages — the lines after the {wf.MAX_STAGES}th are ignored.")
        p.stages = stages[:wf.MAX_STAGES]
        self.main.mark_dirty()
        self.main.update_title()

    # ---- behaviours ----------------------------------------------------------
    def _append_behaviour_row(self, b: Behaviour):
        self._loading = True
        r = self.beh.rowCount()
        self.beh.insertRow(r)
        self.beh.setItem(r, 0, QTableWidgetItem(b.name))
        k = QTableWidgetItem(b.key.upper() if len(b.key) == 1 else b.key)
        k.setTextAlignment(Qt.AlignCenter)
        self.beh.setItem(r, 1, k)
        kind = QComboBox()
        for v, label in KIND_LABELS:
            kind.addItem(label, v)
        kind.setCurrentIndex(max(0, kind.findData(b.kind)))
        kind.currentIndexChanged.connect(self._store_behaviours)
        self.beh.setCellWidget(r, 2, kind)
        self.beh.setItem(r, 3, QTableWidgetItem(b.group))
        col = QPushButton()
        col.setToolTip("Colour of the on-screen scoring button")
        self._set_color_button(col, b.color or BEH_COLORS[r % len(BEH_COLORS)])
        col.clicked.connect(lambda _=False, btn=col: self._pick_color(btn))
        self.beh.setCellWidget(r, 4, col)
        self._loading = False

    @staticmethod
    def _set_color_button(btn, color):
        btn.setProperty("color", color)
        btn.setStyleSheet(f"background:{color};border:1px solid #94a3b8;margin:3px;")

    def _pick_color(self, btn):
        c = QColorDialog.getColor(QColor(btn.property("color")), self, "Behaviour colour")
        if c.isValid():
            self._set_color_button(btn, c.name())
            self._store_behaviours()

    def _add_behaviour(self):
        key = wf.free_key(self.project.behaviours)
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
            group = self.beh.item(r, 3).text().strip() if self.beh.item(r, 3) else ""
            color = self.beh.cellWidget(r, 4).property("color") if self.beh.cellWidget(r, 4) else ""
            if name:
                out.append(Behaviour(name, key, kind, group, color or ""))
        self.project.behaviours = out
        self._validate_behaviours()
        self.main.mark_dirty()

    def _validate_behaviours(self) -> list[str]:
        errs = wf.validate_behaviours(self.project.behaviours) if self.project is not None else []
        self.beh_lbl.setText("<br>".join(errs))
        self.beh_lbl.setVisible(bool(errs))
        return errs

    # ---- workflow ------------------------------------------------------------
    def _blind_toggled(self, on):
        p = self.project
        if self._loading or p is None:
            return
        if not on and p.blind and QMessageBox.question(
                self, "Unblind", "Reveal the treatment groups? The experimenter will no longer be blind to the "
                "groups on the Animals, Tests and Test view pages.") != QMessageBox.Yes:
            self._loading = True
            self.blind.setChecked(True)
            self._loading = False
            return
        p.blind = on
        if on:
            wf.blind_codes(p)
        self.main.mark_dirty()

    def _store_workflow(self, *_):
        if self._loading or self.project is None:
            return
        self.project.settings_extra["confirm_id"] = self.confirm_id.isChecked()
        self.main.mark_dirty()

    # ---- training criteria ----------------------------------------------------
    def _append_criterion_row(self, c: dict):
        c = wf.normalize_criterion(c)
        self._loading = True
        r = self.crit.rowCount()
        self.crit.insertRow(r)
        stage = QComboBox()
        stage.setEditable(True)
        stage.addItems(self.project.stages if self.project else [])
        stage.setCurrentText(c["stage"])
        stage.currentTextChanged.connect(self._store_criteria)
        self.crit.setCellWidget(r, 0, stage)
        self.crit.setItem(r, 1, QTableWidgetItem(c["measure"]))
        op = QComboBox()
        op.addItems(["<", "<=", ">", ">="])
        op.setCurrentText(c["op"])
        op.currentIndexChanged.connect(self._store_criteria)
        self.crit.setCellWidget(r, 2, op)
        it = QTableWidgetItem(f"{c['value']:g}")
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.crit.setItem(r, 3, it)
        cons = QSpinBox()
        cons.setRange(1, wf.MAX_TRIALS)
        cons.setValue(c["consecutive_trials"])
        cons.valueChanged.connect(self._store_criteria)
        self.crit.setCellWidget(r, 4, cons)
        met = QComboBox()
        for v, label in MET_ACTIONS:
            met.addItem(label, v)
        met.setCurrentIndex(max(0, met.findData("complete_stage" if c["action_met"] == "advance" else c["action_met"])))
        met.currentIndexChanged.connect(self._store_criteria)
        self.crit.setCellWidget(r, 5, met)
        after = QSpinBox()
        after.setRange(0, wf.MAX_TRIALS)
        after.setSpecialValueText("never")
        after.setSuffix(" trials")
        after.setValue(c["action_fail"]["after_trials"] if c["action_fail"]["action"] == "retire" else 0)
        after.setToolTip("Retire animals that have not met the criterion after this many trials of the stage")
        after.valueChanged.connect(self._store_criteria)
        self.crit.setCellWidget(r, 6, after)
        self._loading = False

    def _add_criterion(self):
        if self.project is None:
            return
        stage = self.project.stages[0] if self.project.stages else ""
        self._append_criterion_row({"stage": stage, "measure": "Latency to first entry (s)", "op": "<", "value": 10,
                                    "consecutive_trials": 3})
        self._store_criteria()

    def _store_criteria(self, *_):
        if self._loading or self.project is None:
            return
        out = []
        for r in range(self.crit.rowCount()):
            w = self.crit.cellWidget
            try:
                value = float(self.crit.item(r, 3).text().replace(",", "."))
            except (ValueError, AttributeError):
                value = 0.0
            after = w(r, 6).value()
            out.append({"stage": w(r, 0).currentText().strip(),
                        "measure": self.crit.item(r, 1).text().strip() if self.crit.item(r, 1) else "",
                        "op": w(r, 2).currentText(), "value": value, "consecutive_trials": w(r, 4).value(),
                        "action_met": w(r, 5).currentData(),
                        "action_fail": {"after_trials": after, "action": "retire" if after else "none"}})
        self.project.training_criteria = out
        self.main.mark_dirty()

    # ---- event-anchored periods -------------------------------------------------
    _TARGET_KEY = {"first_entry": "zone", "first_exit": "zone", "mark": "behaviour", "input": "channel"}

    def _append_event_period_row(self, d: dict):
        self._loading = True
        r = self.ev_periods.rowCount()
        self.ev_periods.insertRow(r)
        anchor = QComboBox()
        for key, title in ANCHORS.items():
            anchor.addItem(title, key)
        anchor.setCurrentIndex(max(0, anchor.findData(d.get("anchor", "start"))))
        anchor.currentIndexChanged.connect(self._store_event_periods)
        self.ev_periods.setCellWidget(r, 1, anchor)
        target = d.get(self._TARGET_KEY.get(d.get("anchor", ""), "zone"), "")
        for c, v in ((0, d.get("label", "")), (2, target), (3, d.get("offset_s", 0)), (4, d.get("duration_s", 0)),
                     (5, d.get("occurrence", 1))):
            it = QTableWidgetItem(str(v))
            if c >= 3:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.ev_periods.setItem(r, c, it)
        self._loading = False

    def _add_event_period(self):
        zone = ""
        p = self.project
        if p is not None and p.apparatus and p.apparatus[0].zones:
            zone = p.apparatus[0].zones[-1].name
        n = self.ev_periods.rowCount() + 1
        self._append_event_period_row({"label": f"Event period {n}", "anchor": "first_exit", "zone": zone,
                                       "offset_s": 0, "duration_s": 30, "occurrence": 1})
        self._store_event_periods()

    def _store_event_periods(self, *_):
        if self._loading or self.project is None:
            return
        out = []
        for r in range(self.ev_periods.rowCount()):
            try:
                anchor = self.ev_periods.cellWidget(r, 1).currentData()
                d = {"label": self.ev_periods.item(r, 0).text().strip() or f"Event period {r + 1}",
                     "anchor": anchor, "offset_s": float(self.ev_periods.item(r, 3).text()),
                     "duration_s": float(self.ev_periods.item(r, 4).text()),
                     "occurrence": int(float(self.ev_periods.item(r, 5).text()))}
            except (ValueError, AttributeError):
                continue
            target = self.ev_periods.item(r, 2).text().strip()
            if anchor in self._TARGET_KEY:
                d[self._TARGET_KEY[anchor]] = target
            out.append(d)
        self.project.analysis.event_periods = out
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
