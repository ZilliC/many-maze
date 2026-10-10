"""The widgets of the Run tests page: test panels and the report tabs (Setup, Procedures, Results, Log)."""

from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTimeEdit, QVBoxLayout, QWidget)

from ....core.livegroup import DEFAULT_START_KEYS, DEFAULT_STOP_KEYS
from ...icons import icon
from ...live_widgets import PanelGrid, TestPanel
from ...procedure_editor import ProcedureEditor
from ...scoring_pad import ScoringPad
from .common import RESOLUTIONS, START_MODES


class SetupMixin:
    """Building the page's widgets: the single test panel, the grid of test panels and the report tabs (Setup,
    Procedures, Results, Log)."""

    def _build_single(self) -> QWidget:
        p = TestPanel(single=True)
        self.single_panel = p
        self.view = p.view
        self.state_lbl = p.state_lbl
        self.vals = p.vals
        self.arm_btn = p.start_btn
        self.pause_btn = p.pause_btn
        self.stop_btn = p.stop_btn
        self.preview_btn = p.camera_btn
        self.next_btn = p.next_btn
        p.start_clicked.connect(self._arm_clicked)
        p.start_now.connect(self.start_now)
        p.arm_clicked.connect(self.arm)
        p.pause_clicked.connect(self.toggle_pause)
        p.stop_clicked.connect(self._stop_clicked)
        p.undo_clicked.connect(lambda: self.undo_last_event(self.session))
        p.camera_btn.clicked.connect(lambda: (self._toggle_preview(), self._update_buttons()))
        p.bg_btn.clicked.connect(self.capture_background)
        p.next_btn.clicked.connect(self.next_test)
        p.menu.addAction(self.cam_opts_act)
        p.menu.addAction(self.capture_bg_act)
        p.menu.addSeparator()
        p.menu.addAction(self.panel_settings_act)
        p.view.set_message("Choose a camera — or a video file that simulates one — in Setup, then press ▶ to "
                           "start the test, or turn on the camera image to preview it.")
        self.score_pad = ScoringPad()
        self.score_pad.pressed.connect(lambda n: self._pad(n, True))
        self.score_pad.released.connect(lambda n: self._pad(n, False))
        self.keys_lbl = QLabel()
        self.keys_lbl.setObjectName("Hint")
        self.keys_lbl.setWordWrap(True)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(p, 1)
        v.addWidget(self.score_pad)
        v.addWidget(self.keys_lbl)
        return w

    def _build_multi(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.mosaic = PanelGrid()
        self.mosaic.panel_clicked.connect(self._panel_clicked)
        # many panels (up to 40 apparatus) do not shrink below their minimum size: the grid scrolls instead
        self.mosaic_scroll = QScrollArea()
        self.mosaic_scroll.setWidget(self.mosaic)
        self.mosaic_scroll.setWidgetResizable(True)
        self.mosaic_scroll.setFrameShape(QScrollArea.NoFrame)
        v.addWidget(self.mosaic_scroll, 1)
        self.group_lbl = QLabel()
        self.group_lbl.setObjectName("Hint")
        self.group_lbl.setWordWrap(True)
        v.addWidget(self.group_lbl)
        return w

    def _build_session_table(self) -> QGroupBox:
        box = QGroupBox("Test panels")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 4)
        hint = QLabel("Each row is a test panel: the camera or video it uses, its apparatus and the animal, stage "
                      "and trial tested. Add and remove panels with the ribbon.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.sess_table = QTableWidget(0, 8)
        self.sess_table.setHorizontalHeaderLabels(["Source", "Apparatus", "Animal", "Stage", "Trial", "State",
                                                   "Time", ""])
        self.sess_table.verticalHeader().hide()
        self.sess_table.verticalHeader().setDefaultSectionSize(30)
        self.sess_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sess_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sess_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sess_table.setShowGrid(False)
        hh = self.sess_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wd in ((1, 92), (2, 62), (3, 62), (4, 50)):
            self.sess_table.setColumnWidth(c, wd)
        for c in (5, 6, 7):  # state, time and controls are shown by the panels themselves
            self.sess_table.setColumnHidden(c, True)
        self.sess_table.setMinimumHeight(120)
        self.sess_table.setMaximumHeight(200)
        self.sess_table.currentCellChanged.connect(lambda *_: self._selection_changed())
        v.addWidget(self.sess_table)
        # the selected panel's test, edited with wide inputs (ANY-maze style property rows)
        self.row_editor = QWidget()
        f = QFormLayout(self.row_editor)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        f.setContentsMargins(0, 8, 0, 0)
        f.setVerticalSpacing(7)
        self.row_source = QComboBox()
        self.row_apparatus = QComboBox()
        self.row_animal = QComboBox()
        self.row_animal.setEditable(True)
        self.row_animal.setInsertPolicy(QComboBox.NoInsert)
        self.row_stage = QComboBox()
        self.row_stage.setEditable(True)
        self.row_trial = QSpinBox()
        self.row_trial.setRange(1, 10000)
        self.row_device = QComboBox()
        self.row_device.setToolTip("The I/O device (box) of this test: its procedures only see that device's "
                                   "inputs and drive its outputs. Needed when several tests run at once.")
        self.row_source.currentIndexChanged.connect(self._row_editor_changed)
        self.row_device.currentIndexChanged.connect(self._row_editor_changed)
        self.row_apparatus.currentIndexChanged.connect(self._row_editor_changed)
        self.row_animal.currentTextChanged.connect(self._row_editor_changed)
        self.row_stage.currentTextChanged.connect(self._row_editor_changed)
        self.row_trial.valueChanged.connect(self._row_editor_changed)
        f.addRow("Camera / video", self.row_source)
        f.addRow("Apparatus", self.row_apparatus)
        f.addRow("Animal", self.row_animal)
        f.addRow("Stage", self.row_stage)
        f.addRow("Trial", self.row_trial)
        f.addRow("I/O device", self.row_device)
        v.addWidget(self.row_editor)
        return box

    def _build_setup(self) -> QWidget:
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(10, 4, 10, 10)

        def form(box):
            f = QFormLayout(box)
            f.setRowWrapPolicy(QFormLayout.WrapLongRows)
            f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            f.setHorizontalSpacing(10)
            f.setVerticalSpacing(7)
            f.setContentsMargins(0, 4, 0, 4)
            return f

        self.panels_box = self._build_session_table()
        v.addWidget(self.panels_box)

        src = QGroupBox("Video source")
        f = form(src)
        self.cam_radio = QRadioButton("Use a camera")
        self.sim_radio = QRadioButton("Simulate a camera with a video file")
        self.src_group = QButtonGroup(self)
        self.src_group.addButton(self.cam_radio)
        self.src_group.addButton(self.sim_radio)
        self.cam_radio.setChecked(True)
        self.cam_radio.toggled.connect(self._source_mode_changed)
        self.camera = QComboBox()
        self.camera.addItem("Camera 0", 0)
        self.camera.currentIndexChanged.connect(self._camera_changed)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setToolTip("Look for cameras connected to this computer")
        self.scan_btn.clicked.connect(self.scan_cameras)
        row = QHBoxLayout()
        row.addWidget(self.camera, 1)
        row.addWidget(self.scan_btn)
        self.resolution = QComboBox()
        for lbl, val in RESOLUTIONS:
            self.resolution.addItem(lbl, val)
        self.cam_fps = QSpinBox()
        self.cam_fps.setRange(0, 240)
        self.cam_fps.setSpecialValueText("Camera default")
        self.cam_fps.setSuffix(" fps")
        self.sim_path = QLineEdit()
        self.sim_path.setPlaceholderText("No file chosen")
        self.sim_path.setReadOnly(True)
        self.sim_browse = QPushButton("Choose…")
        self.sim_browse.clicked.connect(self._choose_sim_file)
        srow = QHBoxLayout()
        srow.addWidget(self.sim_path, 1)
        srow.addWidget(self.sim_browse)
        self.sim_speed = QComboBox()
        for s in (1.0, 2.0, 4.0, 8.0):
            self.sim_speed.addItem(f"{s:g}× real time", s)
        self.sim_speed.currentIndexChanged.connect(self._speed_changed)
        self.view_lbl = QLabel()
        self.view_lbl.setObjectName("Hint")
        self.view_lbl.setWordWrap(True)
        self.view_lbl.setToolTip("Change with Camera options in the ribbon")
        f.addRow(self.cam_radio)
        f.addRow("Camera", row)
        f.addRow("Image size", self.resolution)
        f.addRow("Frame rate", self.cam_fps)
        f.addRow(self.sim_radio)
        f.addRow("Video file", srow)
        f.addRow("Play it at", self.sim_speed)
        f.addRow("Camera options", self.view_lbl)
        v.addWidget(src)

        tb = QGroupBox("Test")
        f = form(tb)
        self.test_combo = QComboBox()
        self.test_combo.currentIndexChanged.connect(self._test_selected)
        self.animal = QComboBox()
        self.animal.setEditable(True)
        self.animal.setInsertPolicy(QComboBox.NoInsert)
        self.stage = QComboBox()
        self.stage.setEditable(True)
        self.trial = QSpinBox()
        self.trial.setRange(1, 10000)
        self.apparatus = QComboBox()
        self.apparatus.currentIndexChanged.connect(self._apparatus_changed)
        for sig in (self.animal.currentTextChanged, self.stage.currentTextChanged, self.trial.valueChanged):
            sig.connect(lambda *_: self._update_single_title())
        f.addRow("Test to run", self.test_combo)
        f.addRow("Animal", self.animal)
        f.addRow("Stage", self.stage)
        f.addRow("Trial", self.trial)
        f.addRow("Apparatus", self.apparatus)
        v.addWidget(tb)

        sb = QGroupBox("Start and end")
        f = form(sb)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 1e6)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText("Until stopped")
        self.start_mode = QComboBox()
        for k, lbl in START_MODES:
            self.start_mode.addItem(lbl, k)
        self.start_mode.currentIndexChanged.connect(self._start_mode_changed)
        self.sched_time = QTimeEdit(QTime(5, 0))
        self.sched_time.setDisplayFormat("HH:mm")
        self.sched_daily = QCheckBox("every day")
        trow = QHBoxLayout()
        trow.addWidget(self.sched_time)
        trow.addWidget(self.sched_daily)
        trow.addStretch()
        self.start_keys = QLineEdit(", ".join(DEFAULT_START_KEYS))
        self.start_keys.setToolTip("Keys that start (or resume) the test(s). USB presenters / remotes act as "
                                   "keyboards: PageDown / PageUp / F5 / B.")
        self.stop_keys = QLineEdit(", ".join(DEFAULT_STOP_KEYS))
        self.stop_keys.setToolTip("Keys that stop (and save) the running test(s).")
        self.control_input = QLineEdit()
        self.control_input.setPlaceholderText("none")
        self.control_input.setToolTip("Test control switch: an input ([device/]channel). Closing it continues a "
                                      "test that a procedure ended allowing continuation (waiting for test end).")
        self.start_input = QLineEdit()
        self.start_input.setPlaceholderText("[device/]channel")
        self.start_input.setToolTip("The start switch: an input ([device/]channel, e.g. box/start) whose closing "
                                    "starts the armed test(s) (The test starts: On a start switch)")
        self.start_delay = QDoubleSpinBox()
        self.start_delay.setRange(0, 3600)
        self.start_delay.setDecimals(1)
        self.start_delay.setSuffix(" s")
        self.start_delay.setSpecialValueText("None (at once)")
        self.start_delay.setToolTip("After the start switch — a start key, a remote or the start switch input — "
                                    "the test starts this much later, e.g. to put the animal in and step away "
                                    "(saved with the protocol)")
        self.start_delay.valueChanged.connect(self._store_start_delay)
        for w in (self.start_keys, self.stop_keys, self.control_input, self.start_input):
            w.editingFinished.connect(self._save_live_settings)
        self.sched_time.timeChanged.connect(self._save_live_settings)
        self.sched_daily.toggled.connect(self._save_live_settings)
        f.addRow("Test duration", self.duration)
        f.addRow("The test starts", self.start_mode)
        f.addRow("Start time", trow)
        f.addRow("Start switch", self.start_input)
        f.addRow("Start keys", self.start_keys)
        f.addRow("Delay after the start switch", self.start_delay)
        f.addRow("Stop keys", self.stop_keys)
        f.addRow("Test control input", self.control_input)
        v.addWidget(sb)

        db = QGroupBox("Detection and recording")
        f = form(db)
        self.bg_mode = QComboBox()
        self.bg_mode.addItem("Empty-arena image", "frame")
        self.bg_mode.addItem("Adaptive (learns while running)", "adaptive")
        self.bg_mode.currentIndexChanged.connect(self._bg_mode_changed)
        self.bg_status = QLabel("No background captured")
        self.bg_status.setObjectName("Hint")
        self.bg_status.setWordWrap(True)
        self.record = QCheckBox("Record a video of the test")
        self.record.setChecked(True)
        self.record_overlay = QCheckBox("Burn the time and events into the video")
        self.record_overlay.setToolTip("Write the test time, the clock time and the latest event labels on the "
                                       "recorded video (otherwise the recording is clean).")
        self.record_overlay.toggled.connect(self._save_live_settings)
        self.split_min = QDoubleSpinBox()
        self.split_min.setRange(0, 24 * 60)
        self.split_min.setDecimals(0)
        self.split_min.setSuffix(" min")
        self.split_min.setSpecialValueText("Never (one file)")
        self.split_min.setToolTip("For long tests (e.g. 24 h home cage): record consecutive files of this length, "
                                  "listed in a playlist that plays and tracks as one video. A crash loses at most "
                                  "the end of the current file.")
        self.split_min.valueChanged.connect(self._save_live_settings)
        self.lost_warn = QDoubleSpinBox()
        self.lost_warn.setRange(0, 3600)
        self.lost_warn.setDecimals(1)
        self.lost_warn.setValue(3.0)
        self.lost_warn.setSuffix(" s")
        self.lost_warn.setSpecialValueText("Never")
        self.lost_warn.valueChanged.connect(self._save_live_settings)
        self.pause_off = QCheckBox("Switch all outputs off while a test is paused")
        self.pause_off.setChecked(True)
        self.pause_off.setToolTip("Pausing a test always stops pulse trains and switches shocks off; with this "
                                  "option every output and sound goes off too. \"When test paused\" procedures "
                                  "run at once.")
        self.pause_off.toggled.connect(self._save_live_settings)
        self.serial = QComboBox()
        self.serial.setEditable(True)
        self.serial_note = QLabel()
        self.serial_note.setWordWrap(True)
        self.serial_note.setObjectName("Hint")
        f.addRow("Background", self.bg_mode)
        f.addRow("", self.bg_status)
        f.addRow(self.record)
        f.addRow(self.record_overlay)
        f.addRow("Start a new video file every", self.split_min)
        f.addRow("Warn if the animal is lost for", self.lost_warn)
        f.addRow(self.pause_off)
        f.addRow("Serial port", self.serial)
        f.addRow("", self.serial_note)
        v.addWidget(db)
        v.addStretch()

        self.src_box, self.test_box, self.start_box, self.det_box = src, tb, sb, db
        self.setup_widgets = [src, tb, sb, db]
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.panels_box.setVisible(False)
        self._source_mode_changed()
        self._start_mode_changed()
        return scroll

    def _build_procedures(self) -> QWidget:
        self.proc_editor = ProcedureEditor()  # it also converts the trigger → action rules of older experiments
        self.proc_editor.changed.connect(self.main.mark_dirty)
        return self.proc_editor

    def _build_results(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 8)
        self.results_title = QLabel("The results of the last test appear here.")
        self.results_title.setObjectName("SectionTitle")
        self.results_title.setWordWrap(True)
        v.addWidget(self.results_title)
        self.results = QTableWidget(0, 2)
        self.results.setHorizontalHeaderLabels(["Measure", "Value"])
        self.results.verticalHeader().hide()
        self.results.verticalHeader().setDefaultSectionSize(26)
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setShowGrid(False)
        self.results.setAlternatingRowColors(True)
        v.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.open_test_btn = QPushButton(icon("video"), "Open in Review and score")
        self.open_test_btn.clicked.connect(lambda: self.main.open_test(self.last_test_id))
        self.open_test_btn.setEnabled(False)
        nb = QPushButton(icon("forward"), "Next test")
        nb.clicked.connect(self.next_test)
        row.addWidget(self.open_test_btn)
        row.addStretch()
        row.addWidget(nb)
        v.addLayout(row)
        self.last_test_id = None
        return w

    def _build_log(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 8, 10, 8)
        self.log = QListWidget()
        self.log.setWordWrap(True)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.log)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log.clear)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(clear)
        v.addLayout(h)
        return w
