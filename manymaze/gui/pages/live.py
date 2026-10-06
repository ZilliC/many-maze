"""Live testing page: track an animal in real time from a camera (or a video file simulating one)."""

from __future__ import annotations

import copy
import datetime as _dt
import math
import os
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...core import procedures as procs
from ...core.live import LiveSession
from ...core.procedures import Outputs
from ...core.tracking import ArenaTracker, DetectionSettings, compute_background, draw_overlay
from ...core.video import VIDEO_EXTENSIONS, VideoRecorder, VideoSource, list_cameras
from ..widgets import FrameView, Worker, error_box, fmt_time
from .base import Page

RESOLUTIONS = [("Camera default", None), ("640 × 480", (640, 480)), ("800 × 600", (800, 600)),
               ("1280 × 720", (1280, 720)), ("1920 × 1080", (1920, 1080))]
TRIGGER_LABELS = {
    "start": "Test starts", "end": "Test ends", "time": "At a time", "zone_enter": "Enters zone",
    "zone_exit": "Leaves zone", "freezing_start": "Freezing starts", "freezing_end": "Freezing ends",
    "immobile_start": "Immobility starts", "immobile_end": "Immobility ends", "not_detected": "Animal lost",
}
ACTION_LABELS = {"serial": "Serial command", "ttl": "TTL output", "beep": "Beep", "mark": "Mark event",
                 "end_test": "End the test", "log": "Log message"}
STATE_STYLE = {
    "idle": ("Not armed", "#64748b"),
    "preview": ("Preview", "#0ea5e9"),
    "waiting": ("Waiting for animal…", "#f59e0b"),
    "running": ("Running", "#dc2626"),
    "finished": ("Finished", "#7c3aed"),
}


def serial_ports() -> list[str] | None:
    """Available serial ports, or None when pyserial is not installed."""
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None
    try:
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def recording_path(project, test, size=(640, 480), fps=25.0) -> str:
    """test_<id>_<animal>.mp4 in the recordings folder, or .avi if no mp4 writer is available."""
    safe = re.sub(r"[^\w.-]+", "_", test.animal_id or "animal")
    base = project.recordings_dir() / f"test_{test.id:04d}_{safe}"
    probe = project.recordings_dir() / ".probe.mp4"
    try:
        VideoRecorder(str(probe), fps, size).close()
        return str(base.with_suffix(".mp4"))
    except Exception:
        return str(base.with_suffix(".avi"))
    finally:
        if probe.exists():
            probe.unlink()


class FrameGrabber(QThread):
    """Reads frames from a camera / file and runs `handler(frame, t)` on them, off the UI thread.

    handler returns (display_frame, info); frame_ready is only emitted when the UI has consumed the previous
    frame (call ack()), so a slow UI drops display frames but never tracking frames.
    Files are paced at their frame rate × speed and loop while `loop` is True.
    """

    frame_ready = Signal(object, object)
    opened = Signal(int, int, float)
    background_ready = Signal(object)
    ended = Signal()
    failed = Signal(str)

    def __init__(self, source, handler, size=None, fps=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.handler = handler
        self.size = size
        self.req_fps = fps
        self.loop = True
        self.speed = 1.0
        self._stop = False
        self._restart = False
        self._busy = False
        self.fps = 25.0

    def stop(self):
        self._stop = True

    def restart(self):
        self._restart = True

    def ack(self):
        self._busy = False

    def run(self):
        try:
            w, h = self.size or (None, None)
            src = VideoSource(self.source, w, h, self.req_fps or None)
        except Exception as e:
            self.failed.emit(f"Cannot open {'camera' if isinstance(self.source, int) else 'video'}: {e}")
            return
        self.fps = src.fps or 25.0
        self.opened.emit(src.width, src.height, self.fps)
        try:
            if not src.is_camera:
                try:
                    self.background_ready.emit(compute_background(str(self.source),
                                                                  DetectionSettings(background_samples=21)))
                except Exception:
                    pass
            self._loop(src)
        finally:
            src.release()

    def _loop(self, src: VideoSource):
        idx = 0
        t_start = time.monotonic()
        at_end = False
        failures = 0
        while not self._stop:
            if self._restart:
                self._restart = False
                at_end = False
                src.seek(0)
                idx = 0
                t_start = time.monotonic()
            if at_end:
                self.msleep(20)
                continue
            ok, frame = src.read()
            if not ok:
                if src.is_camera:
                    failures += 1
                    if failures > 100:
                        self.failed.emit("The camera stopped delivering frames.")
                        return
                    self.msleep(10)
                    continue
                if self.loop:
                    src.seek(0)
                    idx = 0
                    t_start = time.monotonic()
                    continue
                at_end = True
                self.ended.emit()
                continue
            failures = 0
            if src.is_camera:
                ts = time.monotonic() - t_start
            else:
                ts = idx / self.fps
                delay = t_start + ts / max(self.speed, 1e-3) - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            idx += 1
            try:
                out = self.handler(frame, ts)
            except Exception as e:  # surfaced to the UI once, then stop
                import traceback

                traceback.print_exc()
                self.failed.emit(f"{type(e).__name__}: {e}")
                return
            if not self._busy:
                self._busy = True
                self.frame_ready.emit(*out)


class LivePage(Page):
    title = "Live testing"

    def __init__(self, main):
        super().__init__(main)
        self._lock = threading.RLock()
        self._loading = False
        self.grabber: FrameGrabber | None = None
        self._scan_worker: Worker | None = None
        self.session: LiveSession | None = None
        self.test = None
        self._new_test = False
        self._record_path: str | None = None
        self._apparatus = None
        self._preview_tracker: ArenaTracker | None = None
        self._background: np.ndarray | None = None
        self._file_background: np.ndarray | None = None
        self._last_frame: np.ndarray | None = None
        self._frame_size: tuple[int, int] | None = None
        self._fps = 25.0
        self._dist = 0.0
        self._last_xy = None
        self._manual: list[dict] = []
        self._open_states: dict[str, dict] = {}
        self._log_seen = 0
        self._fired_seen = 0
        self._shortcuts: list[QShortcut] = []
        self.last_results: list[dict] = []
        self._hold_finished = False
        self._shown_state = None
        self._bg_mode_value = "frame"  # plain copies of widget state read from the grabber thread
        self._source_is_file = False

        # ---- left: preview, dashboard, run controls ------------------------------------
        self.view = FrameView()
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.placeholder = QLabel("Choose a camera (or a video file to simulate one) and press “Start preview”.")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color:#94a3b8;font-size:14px;background:#1e293b;padding:6px")

        dash = QWidget()
        dash.setObjectName("dash")
        dash.setStyleSheet("#dash{background:palette(base);border:1px solid palette(mid);border-radius:6px}"
                           "QLabel[role=cap]{color:palette(mid);font-size:11px}"
                           "QLabel[role=val]{font-size:18px;font-weight:600}")
        dg = QGridLayout(dash)
        dg.setContentsMargins(10, 6, 10, 6)
        self.state_lbl = QLabel()
        self.state_lbl.setAlignment(Qt.AlignCenter)
        self.state_lbl.setMinimumWidth(170)
        dg.addWidget(self.state_lbl, 0, 0, 2, 1)
        self.vals = {}
        for i, (key, cap) in enumerate((("elapsed", "Elapsed"), ("remaining", "Remaining"), ("zone", "Current zone"),
                                        ("distance", "Distance"), ("events", "Events"))):
            c = QLabel(cap)
            c.setProperty("role", "cap")
            v = QLabel("—")
            v.setProperty("role", "val")
            dg.addWidget(c, 0, i + 1)
            dg.addWidget(v, 1, i + 1)
            self.vals[key] = v
        dg.setColumnStretch(3, 2)
        for c in (1, 2, 4, 5):
            dg.setColumnStretch(c, 1)

        self.arm_btn = QPushButton("Arm / Start test")
        self.arm_btn.setMinimumHeight(34)
        self.arm_btn.setStyleSheet("QPushButton{font-weight:600}")
        self.arm_btn.clicked.connect(self.arm)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setMinimumHeight(34)
        self.stop_btn.clicked.connect(self._stop_clicked)
        self.next_btn = QPushButton("Next test ▸")
        self.next_btn.setMinimumHeight(34)
        self.next_btn.clicked.connect(self.next_test)
        self.preview_btn = QPushButton("Start preview")
        self.preview_btn.setMinimumHeight(34)
        self.preview_btn.clicked.connect(self._toggle_preview)
        self.keys_lbl = QLabel()
        self.keys_lbl.setWordWrap(True)
        self.keys_lbl.setStyleSheet("color:palette(mid)")
        ctl = QHBoxLayout()
        for b in (self.preview_btn, self.arm_btn, self.stop_btn, self.next_btn):
            ctl.addWidget(b)
        ctl.addWidget(self.keys_lbl, 1)

        left = QVBoxLayout()
        left.addWidget(self.placeholder)
        left.addWidget(self.view, 1)
        left.addWidget(dash)
        left.addLayout(ctl)

        # ---- right: tabs ----------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_setup(), "Setup")
        self.tabs.addTab(self._build_procedures(), "Procedures")
        self.tabs.addTab(self._build_results(), "Results")
        self.tabs.addTab(self._build_log(), "Log")
        self.tabs.setFixedWidth(410)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.tabs)
        self._set_state_display("idle")
        self._update_buttons()

    # ================================================================== UI construction
    def _build_setup(self) -> QWidget:
        inner = QWidget()
        v = QVBoxLayout(inner)

        src = QGroupBox("Video source")
        f = QFormLayout(src)
        self.cam_radio = QRadioButton("Camera")
        self.sim_radio = QRadioButton("Simulate with a video file")
        self.src_group = QButtonGroup(self)
        self.src_group.addButton(self.cam_radio)
        self.src_group.addButton(self.sim_radio)
        self.cam_radio.setChecked(True)
        self.cam_radio.toggled.connect(self._source_mode_changed)
        self.camera = QComboBox()
        self.camera.addItem("Camera 0", 0)
        self.scan_btn = QPushButton("Scan cameras")
        self.scan_btn.clicked.connect(self.scan_cameras)
        row = QHBoxLayout()
        row.addWidget(self.camera, 1)
        row.addWidget(self.scan_btn)
        self.resolution = QComboBox()
        for lbl, val in RESOLUTIONS:
            self.resolution.addItem(lbl, val)
        self.cam_fps = QSpinBox()
        self.cam_fps.setRange(0, 240)
        self.cam_fps.setSpecialValueText("Default")
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
        f.addRow(self.cam_radio)
        f.addRow("Device", row)
        f.addRow("Resolution", self.resolution)
        f.addRow("Frame rate", self.cam_fps)
        f.addRow(self.sim_radio)
        f.addRow("File", srow)
        f.addRow("Speed", self.sim_speed)
        v.addWidget(src)

        tb = QGroupBox("Test")
        f = QFormLayout(tb)
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
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 1e6)
        self.duration.setDecimals(1)
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText("Until stopped")
        self.start_mode = QComboBox()
        self.start_mode.addItem("Immediately when armed", "immediate")
        self.start_mode.addItem("When the animal is detected", "on_detection")
        f.addRow("Run", self.test_combo)
        f.addRow("Animal", self.animal)
        f.addRow("Stage", self.stage)
        f.addRow("Trial", self.trial)
        f.addRow("Apparatus", self.apparatus)
        f.addRow("Duration", self.duration)
        f.addRow("Test starts", self.start_mode)
        v.addWidget(tb)

        db = QGroupBox("Detection and recording")
        f = QFormLayout(db)
        self.bg_mode = QComboBox()
        self.bg_mode.addItem("Empty-arena image", "frame")
        self.bg_mode.addItem("Adaptive (learns while running)", "adaptive")
        self.bg_mode.currentIndexChanged.connect(self._bg_mode_changed)
        self.capture_btn = QPushButton("Capture empty-arena background")
        self.capture_btn.setToolTip("Take the current frame as the background. The arena must be empty.")
        self.capture_btn.clicked.connect(self.capture_background)
        self.bg_status = QLabel("No background captured")
        self.bg_status.setStyleSheet("color:palette(mid)")
        self.record = QCheckBox("Record video of the test")
        self.record.setChecked(True)
        self.serial = QComboBox()
        self.serial.setEditable(True)
        self.serial_note = QLabel()
        self.serial_note.setWordWrap(True)
        self.serial_note.setStyleSheet("color:palette(mid)")
        f.addRow("Background", self.bg_mode)
        f.addRow("", self.capture_btn)
        f.addRow("", self.bg_status)
        f.addRow("", self.record)
        f.addRow("Serial port", self.serial)
        f.addRow("", self.serial_note)
        v.addWidget(db)
        v.addStretch()

        self.setup_widgets = [src, tb, db]
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._source_mode_changed()
        return scroll

    def _build_procedures(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel("Rules run during live tests: when a trigger happens (time, zone entry, freezing…) the "
                      "action is performed — e.g. send a command to an Arduino over the serial port, beep, or "
                      "mark an event in the test.")
        info.setWordWrap(True)
        info.setStyleSheet("color:palette(mid)")
        v.addWidget(info)
        self.proc_table = QTableWidget(0, 3)
        self.proc_table.setHorizontalHeaderLabels(["Trigger", "Action", "Description"])
        self.proc_table.verticalHeader().hide()
        self.proc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.proc_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.proc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.proc_table.setWordWrap(True)
        hh = self.proc_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.proc_table.currentCellChanged.connect(lambda *_: self._load_rule_editor())
        v.addWidget(self.proc_table, 1)
        bb = QHBoxLayout()
        add = QPushButton("Add rule")
        add.clicked.connect(lambda: self.add_rule())
        rm = QPushButton("Remove")
        rm.clicked.connect(self.remove_rule)
        bb.addWidget(add)
        bb.addWidget(rm)
        bb.addStretch()
        v.addLayout(bb)
        self._proc_buttons = [add, rm]

        ed = QGroupBox("Selected rule")
        f = QFormLayout(ed)
        self.r_trigger = QComboBox()
        for k in procs.TRIGGERS:
            self.r_trigger.addItem(TRIGGER_LABELS.get(k, k), k)
        self.r_zone = QComboBox()
        self.r_zone.setEditable(True)
        self.r_time = QDoubleSpinBox()
        self.r_time.setRange(0, 1e6)
        self.r_time.setDecimals(2)
        self.r_time.setSuffix(" s")
        self.r_delay = QDoubleSpinBox()
        self.r_delay.setRange(0, 1e6)
        self.r_delay.setDecimals(2)
        self.r_delay.setSuffix(" s")
        self.r_delay.setSpecialValueText("No delay")
        self.r_action = QComboBox()
        for k in procs.ACTIONS:
            self.r_action.addItem(ACTION_LABELS.get(k, k), k)
        self.r_payload = QLineEdit()
        for wdg in (self.r_trigger, self.r_action):
            wdg.currentIndexChanged.connect(self._rule_edited)
        self.r_zone.currentTextChanged.connect(self._rule_edited)
        self.r_time.valueChanged.connect(self._rule_edited)
        self.r_delay.valueChanged.connect(self._rule_edited)
        self.r_payload.textChanged.connect(self._rule_edited)
        f.addRow("When", self.r_trigger)
        f.addRow("Zone", self.r_zone)
        f.addRow("Time", self.r_time)
        f.addRow("Delay", self.r_delay)
        f.addRow("Action", self.r_action)
        f.addRow("Payload", self.r_payload)
        self.rule_desc = QLabel()
        self.rule_desc.setWordWrap(True)
        self.rule_desc.setStyleSheet("font-style:italic")
        f.addRow(self.rule_desc)
        self.rule_editor = ed
        v.addWidget(ed)
        return w

    def _build_results(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.results_title = QLabel("Results of the last live test appear here.")
        self.results_title.setWordWrap(True)
        self.results_title.setStyleSheet("font-weight:600")
        v.addWidget(self.results_title)
        self.results = QTableWidget(0, 2)
        self.results.setHorizontalHeaderLabels(["Measure", "Value"])
        self.results.verticalHeader().hide()
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setAlternatingRowColors(True)
        v.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.open_test_btn = QPushButton("Open in test viewer")
        self.open_test_btn.clicked.connect(lambda: self.main.open_test(self.last_test_id))
        self.open_test_btn.setEnabled(False)
        nb = QPushButton("Next test ▸")
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
        self.log = QListWidget()
        v.addWidget(self.log)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log.clear)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(clear)
        v.addLayout(h)
        return w

    # ================================================================== project / visibility
    def set_project(self, project):
        self.stop_test(save=False, quiet=True)
        self.stop_preview()
        self._background = None
        self._file_background = None
        self.bg_status.setText("No background captured")
        self.results.setRowCount(0)
        self.results_title.setText("Results of the last live test appear here.")
        self.log.clear()
        if project is not None:
            self.duration.setValue(project.test_duration_s)
            self.start_mode.setCurrentIndex(1 if project.start_mode == "on_detection" else 0)
        self.on_show()

    def on_show(self):
        p = self.project
        if p is None:
            return
        self._loading = True
        armed = self.session is not None
        if not armed:
            cur = self.test_combo.currentData()
            self.test_combo.clear()
            self.test_combo.addItem("New test", None)
            for t in p.tests:
                if t.status == "pending":
                    extra = " (has video)" if t.video else ""
                    self.test_combo.addItem(f"Test {t.id} · {t.animal_id or '?'} · {t.stage or '—'} · "
                                            f"trial {t.trial}{extra}", t.id)
            i = self.test_combo.findData(cur)
            self.test_combo.setCurrentIndex(max(0, i))
            ca = self.animal.currentText()
            self.animal.clear()
            self.animal.addItems([a.id for a in p.animals])
            self.animal.setCurrentText(ca if ca else (p.animals[0].id if p.animals else ""))
            cs = self.stage.currentText()
            self.stage.clear()
            self.stage.addItems(p.stages)
            self.stage.setCurrentText(cs if cs else (p.stages[0] if p.stages else ""))
            cap = self.apparatus.currentText()
            self.apparatus.clear()
            self.apparatus.addItems([a.name for a in p.apparatus])
            if cap:
                self.apparatus.setCurrentText(cap)
            ports = serial_ports()
            cp = self.serial.currentText()
            self.serial.clear()
            self.serial.addItem("")
            if ports is None:
                self.serial_note.setText("pyserial is not installed: serial / TTL actions are only written to "
                                         "the log (pip install pyserial).")
            else:
                self.serial.addItems(ports)
                self.serial_note.setText("Optional. Used by “serial” and “ttl” procedure actions.")
            self.serial.setCurrentText(cp)
        self._loading = False
        if not armed:
            self._test_selected()
        self._apparatus_changed()
        self._load_procedures()
        self._update_keys_label()
        self._update_buttons()

    def on_hide(self):
        if self.session is not None:
            self.main.status("A live test is still running in the background — return to “Live testing” to "
                             "follow or stop it.", 10000)
            return
        self.stop_preview()

    def shutdown(self):
        if self.session is not None:
            self.stop_test(save=True, quiet=True)
        self.stop_preview()
        if self._scan_worker is not None:
            self._scan_worker.wait(3000)

    # ================================================================== source / preview
    def _source_mode_changed(self, *_):
        cam = self.cam_radio.isChecked()
        for w in (self.camera, self.scan_btn, self.resolution, self.cam_fps):
            w.setEnabled(cam)
        for w in (self.sim_path, self.sim_browse, self.sim_speed):
            w.setEnabled(not cam)

    def _speed_changed(self):
        if self.grabber is not None:
            self.grabber.speed = self.sim_speed.currentData() or 1.0

    def scan_cameras(self):
        if self._scan_worker is not None:
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning…")
        self.main.status("Looking for cameras…")
        w = Worker(lambda progress, stop: list_cameras(), self)
        w.signals.done.connect(self._cameras_found)
        w.signals.failed.connect(lambda msg: self._cameras_found([]))
        w.finished.connect(w.deleteLater)
        self._scan_worker = w
        w.start()

    def _cameras_found(self, cams):
        self._scan_worker = None
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan cameras")
        cur = self.camera.currentData()
        self.camera.clear()
        for i in cams:
            self.camera.addItem(f"Camera {i}", i)
        if not cams:
            self.camera.addItem("Camera 0", 0)
            self.main.status("No camera found. You can simulate one with a video file.")
        else:
            self.main.status(f"Found {len(cams)} camera{'s' if len(cams) > 1 else ''}")
        self.camera.setCurrentIndex(max(0, self.camera.findData(cur)))

    def set_simulation_file(self, path: str):
        self.sim_path.setText(path)
        self.sim_radio.setChecked(True)
        self._file_background = None
        self._source_is_file = True

    def _choose_sim_file(self):
        start = str(self.project.path) if self.project and self.project.path else str(Path.home())
        exts = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Video file to simulate a camera", start,
                                              f"Videos ({exts});;All files (*)")
        if path:
            restart = self.grabber is not None
            self.stop_preview()
            self.set_simulation_file(path)
            if restart:
                self.start_preview()

    def _source(self):
        if self.sim_radio.isChecked():
            p = self.sim_path.text().strip()
            return p or None
        return int(self.camera.currentData() or 0)

    @property
    def simulating(self) -> bool:
        return self.sim_radio.isChecked()

    def start_preview(self) -> bool:
        if self.grabber is not None:
            return True
        src = self._source()
        if src is None:
            QMessageBox.information(self, "Live testing", "Choose a video file to simulate a camera first.")
            return False
        size = self.resolution.currentData() if not self.simulating else None
        fps = self.cam_fps.value() if not self.simulating else None
        self._source_is_file = self.simulating
        g = FrameGrabber(src, self.process_frame, size=size, fps=fps, parent=self)
        g.speed = (self.sim_speed.currentData() or 1.0) if self.simulating else 1.0
        g.frame_ready.connect(self._on_frame)
        g.opened.connect(self._on_opened)
        g.background_ready.connect(self._on_file_background)
        g.ended.connect(self._on_source_ended)
        g.failed.connect(self._on_grab_failed)
        g.finished.connect(self._grabber_finished)
        self.grabber = g
        g.start()
        self.placeholder.hide()
        self._update_buttons()
        if self.session is None:
            self._set_state_display("preview")
        return True

    def stop_preview(self):
        g = self.grabber
        if g is None:
            return
        self.grabber = None
        g.stop()
        g.wait(5000)
        g.deleteLater()
        self._update_buttons()
        if self.session is None:
            self._set_state_display("idle")

    def _toggle_preview(self):
        if self.grabber is None:
            self.start_preview()
        else:
            if self.session is not None:
                QMessageBox.information(self, "Live testing", "Stop the test before stopping the camera.")
                return
            self.stop_preview()

    def _grabber_finished(self):
        if self.grabber is not None and self.grabber.isFinished():
            g, self.grabber = self.grabber, None
            g.deleteLater()
            self._update_buttons()

    def _on_opened(self, w, h, fps):
        self._fps = fps
        self._frame_size = (w, h)
        kind = "Video" if self.simulating else "Camera"
        self.main.status(f"{kind} opened: {w}×{h} at {fps:.1f} fps")
        app = self._apparatus
        if app is not None and app.frame_size and tuple(app.frame_size) != (w, h):
            self._log(f"Note: apparatus “{app.name}” was drawn on a {app.frame_size[0]}×{app.frame_size[1]} "
                      f"image but the source is {w}×{h}.")

    def _on_file_background(self, bg):
        self._file_background = bg
        if self._background is None:
            self.bg_status.setText("Using the median of the video file (simulation)")
            self._reset_preview_tracker()

    def _on_grab_failed(self, msg):
        self._log(f"Error: {msg}")
        if self.session is not None:
            self.stop_test(save=len(self.session.cols["t"]) > 0, quiet=True)
        self.stop_preview()
        error_box(self, "Live testing", msg)

    def _on_source_ended(self):
        if self.session is None:
            return
        if self.session.state == "running":
            self._log("End of the video file — test finished.")
            self.stop_test(save=True, quiet=True)
        else:
            self._log("End of the video file before the test started.")
            self.stop_test(save=False, quiet=True)

    # ================================================================== frame processing
    def _bg_mode_changed(self, *_):
        self._bg_mode_value = self.bg_mode.currentData() or "frame"
        self._reset_preview_tracker()

    def _reset_preview_tracker(self, *_):
        with self._lock:
            self._preview_tracker = None

    def _apparatus_changed(self, *_):
        if self._loading or self.project is None:
            return
        app = self.project.get_apparatus(self.apparatus.currentText()) if self.project.apparatus else None
        with self._lock:
            self._apparatus = app
            self._preview_tracker = None
        self._refresh_zone_choices()

    def _detection_settings(self) -> DetectionSettings:
        s = DetectionSettings.from_dict(self.project.detection.to_dict())
        if self.test is not None and self.test.detection:
            s = DetectionSettings.from_dict({**s.to_dict(), **self.test.detection})
        s.n_animals = 1
        s.start_time_s = 0.0
        s.duration_s = 0.0
        s.frame_step = 1
        s.background = self._bg_mode_value
        return s

    def _current_background(self):
        return self._background if self._background is not None else (
            self._file_background if self._source_is_file else None)

    def process_frame(self, frame: np.ndarray, ts: float):
        """Track one frame (called from the grabber thread). Returns (display frame, info dict)."""
        with self._lock:
            self._last_frame = frame
            app = self._apparatus
            s = self.session
            trail = None
            if s is not None:
                dets = s.process(frame, ts)
                state = s.state
                d = dets[0] if dets else None
                if state == "running" and d is not None and d.detected:
                    # exponential smoothing ≈ the analysis speed-smoothing window, so jitter is not counted
                    n_win = max(1.0, s.analysis.speed_smoothing_s * s.fps)
                    alpha = 2.0 / (n_win + 1.0)
                    if self._last_xy is None:
                        self._last_xy = (d.x, d.y)
                    else:
                        lx, ly = self._last_xy
                        nx, ny = lx + alpha * (d.x - lx), ly + alpha * (d.y - ly)
                        self._dist += math.hypot(nx - lx, ny - ly)
                        self._last_xy = (nx, ny)
                n = 250
                trail = list(zip(s.cols["x"][-n:], s.cols["y"][-n:]))
                elapsed = s.elapsed if state != "waiting" else 0.0
                info = {"state": state, "elapsed": elapsed, "duration": s.duration_s,
                        "events": len(s.events) + len(self._manual), "fired": list(s.engine.fired),
                        "outputs": list(s.outputs.log) if s.outputs else []}
            else:
                if self._preview_tracker is None:
                    self._preview_tracker = self._make_preview_tracker(frame, app)
                dets, _fg = self._preview_tracker.process(frame) if self._preview_tracker else ([], None)
                d = dets[0] if dets else None
                info = {"state": "preview"}
            zones = []
            if app is not None and d is not None and d.detected:
                zm = app.zone_membership(np.array([d.x]), np.array([d.y]))
                zones = [k for k, v in zm.items() if bool(np.asarray(v).ravel()[0])]
            info["zones"] = zones
            info["detected"] = bool(d is not None and d.detected)
            info["distance"] = self._dist * (app.scale if app else 1.0)
            info["unit"] = app.unit if app else "px"
        disp = draw_overlay(frame, dets, app, trail)
        self._annotate(disp, info)
        return disp, info

    def _make_preview_tracker(self, frame, app):
        if self.project is None:
            return None
        s = self._detection_settings()
        h, w = frame.shape[:2]
        try:
            mask = app.arena_or_bounds().mask((h, w)) if app else None
        except ValueError:
            mask = None
        tr = ArenaTracker(s, mask)
        bg = self._current_background()
        if bg is not None and bg.shape[:2] == (h, w):
            tr.set_background(bg if bg.ndim == 2 else cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY))
        return tr

    @staticmethod
    def _annotate(img, info):
        st = info["state"]
        txt = {"running": f"REC {fmt_time(info.get('elapsed', 0))}", "waiting": "WAITING FOR ANIMAL",
               "finished": "FINISHED"}.get(st, "PREVIEW")
        col = STATE_STYLE.get(st, STATE_STYLE["preview"])[1].lstrip("#")
        bgr = (int(col[4:6], 16), int(col[2:4], 16), int(col[0:2], 16))
        scale = max(0.4, img.shape[1] / 1300)
        th = max(1, int(round(scale * 1.6)))
        (tw, tht), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
        pad = max(4, int(6 * scale))
        dot = tht if st == "running" else 0
        cv2.rectangle(img, (6, 6), (6 + tw + dot + 3 * pad, 6 + tht + base + 2 * pad), bgr, -1)
        if dot:
            cv2.circle(img, (6 + pad + dot // 2, 6 + pad + tht // 2), max(2, dot // 3), (255, 255, 255), -1,
                       cv2.LINE_AA)
        cv2.putText(img, txt, (6 + 2 * pad + dot - (pad if not dot else 0), 6 + pad + tht),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), th, cv2.LINE_AA)

    def feed_frame(self, frame: np.ndarray, ts: float):
        """Process and display a frame synchronously (used when driving the page without a grabber)."""
        self._on_frame(*self.process_frame(frame, ts))

    def _on_frame(self, disp, info):
        if self.grabber is not None:
            self.grabber.ack()
        self.placeholder.hide()
        self.view.set_frame(disp)
        state = info["state"]
        if self.session is not None or not self._hold_finished:
            self._set_state_display(state)
        self.vals["zone"].setText(", ".join(info["zones"]) if info["zones"] else
                                  ("—" if info["detected"] else "not detected"))
        if self.session is not None:
            el = info["elapsed"]
            dur = info["duration"]
            self.vals["elapsed"].setText(fmt_time(el))
            self.vals["remaining"].setText(fmt_time(max(0.0, dur - el)) if dur else "∞")
            self.vals["distance"].setText(f"{info['distance']:.1f} {info['unit']}")
            self.vals["events"].setText(str(info["events"]))
            fired = info["fired"]
            for t, trig, act, payload in fired[self._fired_seen:]:
                self._log(f"{fmt_time(t)}  rule: {trig} → {act} {payload}".rstrip())
            self._fired_seen = len(fired)
            outs = info["outputs"]
            for line in outs[self._log_seen:]:
                self._log(f"  {line}")
            self._log_seen = len(outs)
            if state == "finished":
                self._finalise(save=True)

    # ================================================================== test setup
    def _test_selected(self, *_):
        if self._loading or self.project is None:
            return
        tid = self.test_combo.currentData()
        t = self.project.get_test(tid) if tid is not None else None
        if t is not None:
            self.animal.setCurrentText(t.animal_id)
            self.stage.setCurrentText(t.stage)
            self.trial.setValue(t.trial)
            if t.apparatus:
                self.apparatus.setCurrentText(t.apparatus)
            self.duration.setValue(t.duration_s or self.project.test_duration_s)

    def _refresh_zone_choices(self):
        app = self._apparatus
        self._zone_names = ([z.name for z in app.zones] + [g.name for g in app.groups]) if app else []

    def capture_background(self) -> bool:
        with self._lock:
            f = self._last_frame
        if f is None:
            QMessageBox.information(self, "Background", "Start the preview first, with the arena empty.")
            return False
        self._background = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f.copy()
        self.bg_status.setText(f"Captured at {_dt.datetime.now():%H:%M:%S} ({f.shape[1]}×{f.shape[0]})")
        idx = self.bg_mode.findData("frame")
        self.bg_mode.setCurrentIndex(idx)
        self._reset_preview_tracker()
        self.main.status("Empty-arena background captured")
        return True

    # ================================================================== run control
    def arm(self) -> bool:
        p = self.project
        if p is None or self.session is not None:
            return False
        if p.path is None:
            QMessageBox.information(self, "Live testing", "Save the experiment first.")
            return False
        if not p.apparatus:
            QMessageBox.information(self, "Live testing", "Draw an apparatus first (Apparatus page).")
            return False
        aid = self.animal.currentText().strip()
        if not aid:
            QMessageBox.information(self, "Live testing", "Choose or type the animal ID.")
            return False
        tid = self.test_combo.currentData()
        test = p.get_test(tid) if tid is not None else None
        self._new_test = test is None
        if p.get_animal(aid) is None:
            p.ensure_animal(aid)
        app_name = self.apparatus.currentText() or p.apparatus[0].name
        if test is None:
            test = p.add_test("", aid, app_name, stage=self.stage.currentText().strip(), trial=self.trial.value())
        else:
            test.animal_id = aid
            test.apparatus = app_name
            test.stage = self.stage.currentText().strip()
            test.trial = self.trial.value()
        dur = self.duration.value()
        test.duration_s = 0.0 if abs(dur - p.test_duration_s) < 1e-9 else dur
        self.test = test
        self.main.mark_dirty()
        if self.grabber is None and not self.start_preview():
            self._discard_new_test()
            return False
        settings = self._detection_settings()
        bg = self._current_background()
        if settings.background == "frame" and bg is None:
            settings.background = "adaptive"
            self._log("No empty-arena background: using an adaptive background.")
        size = self._frame_size or (640, 480)
        self._record_path = recording_path(p, test, size, self._fps) if self.record.isChecked() else None
        port = self.serial.currentText().strip() or None
        outputs = Outputs(port)
        for line in outputs.log:
            self._log(line)
        session = LiveSession(p.get_apparatus(app_name), settings, duration_s=dur,
                              start_mode=self.start_mode.currentData(), procedures=copy.deepcopy(p.procedures),
                              outputs=outputs, record_path=self._record_path, fps=self._fps,
                              analysis=p.analysis_for(test))
        if bg is not None:
            session.set_background(bg)
        with self._lock:
            self._apparatus = session.apparatus
            self._dist = 0.0
            self._last_xy = None
            self._manual = []
            self._open_states = {}
            self._fired_seen = 0
            self._log_seen = len(outputs.log)
            self.session = session
        if self.grabber is not None:
            self.grabber.loop = False
            if self.simulating:
                self.grabber.restart()
        self._enable_shortcuts(True)
        self._hold_finished = False
        self.tabs.setCurrentIndex(3)
        self._log(f"Test {test.id} armed — animal {aid}, {'until stopped' if not dur else f'{dur:g} s'}, "
                  f"start {self.start_mode.currentText().lower()}")
        self._set_state_display("waiting")
        self._update_buttons()
        return True

    def _stop_clicked(self):
        s = self.session
        if s is None:
            return
        if s.state != "running":
            self.stop_test(save=False)
            return
        r = QMessageBox.question(self, "Stop test", "Stop the test now?\n\nSave keeps the data recorded so far; "
                                 "Discard throws the test away.",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Save:
            self.stop_test(save=True)
        elif r == QMessageBox.Discard:
            self.stop_test(save=False)

    def stop_test(self, save: bool = True, quiet: bool = False):
        if self.session is None:
            return
        with self._lock:
            self.session.finish()
        self._finalise(save=save and len(self.session.cols["t"]) > 0, quiet=quiet)

    def _finalise(self, save: bool = True, quiet: bool = False):
        with self._lock:
            s = self.session
            if s is None:
                return
            s.finish()
            self.session = None
        self._enable_shortcuts(False)
        if s.outputs is not None:
            s.outputs.close()
        if self.grabber is not None:
            self.grabber.loop = True
        p, test = self.project, self.test
        self.test = None
        el = s.elapsed
        for ev in self._open_states.values():
            ev["t_end"] = el
        if not save or p is None or test is None:
            if self._record_path and Path(self._record_path).exists():
                try:
                    os.remove(self._record_path)
                except OSError:
                    pass
            if test is not None and self._new_test and p is not None and test in p.tests:
                p.tests.remove(test)
            self._log("Test discarded.")
            self._set_state_display("preview" if self.grabber else "idle")
            self._update_buttons()
            self.on_show()
            return
        p.save_tracks(test, [s.track()])
        if self._record_path and Path(self._record_path).exists():
            test.video = p.rel_path(self._record_path)
            test.start_s = 0.0
        test.events = list(test.events) + [dict(e) for e in s.events] + [dict(e) for e in self._manual]
        test.events.sort(key=lambda e: e.get("t", 0))
        test.status = "tracked"
        test.recorded_at = _dt.datetime.now().isoformat(timespec="seconds")
        if s.outputs is not None and s.outputs.log:
            note = "Live procedures: " + "; ".join(s.outputs.log[:50])
            test.notes = (test.notes + "\n" + note).strip()
        self.main.mark_dirty()
        self.main.save()
        self.last_test_id = test.id
        self._log(f"Test {test.id} finished after {fmt_time(el)} and saved.")
        self._set_state_display("finished")
        self._hold_finished = True
        self._show_results(test)
        self._update_buttons()
        self.on_show()
        if not quiet:
            self.main.status(f"Test {test.id} saved. Press “Next test” to continue.")

    def _discard_new_test(self):
        if self._new_test and self.test is not None and self.test in self.project.tests:
            self.project.tests.remove(self.test)
        self.test = None

    def _show_results(self, test):
        try:
            rows = self.project.analyse_test(test)
        except Exception as e:
            self._log(f"Analysis failed: {e}")
            rows = []
        self.last_results = rows
        self.results_title.setText(f"Test {test.id} · animal {test.animal_id} · {test.stage or ''} trial "
                                   f"{test.trial}")
        self.results.setRowCount(0)
        if rows:
            skip = {"Test", "Animal", "Group", "Sex", "Stage", "Trial", "Apparatus", "Period"}
            for k, v in rows[0].items():
                if k in skip:
                    continue
                r = self.results.rowCount()
                self.results.insertRow(r)
                self.results.setItem(r, 0, QTableWidgetItem(str(k)))
                vi = QTableWidgetItem("" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)))
                vi.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.results.setItem(r, 1, vi)
        self.results.resizeColumnToContents(1)
        self.open_test_btn.setEnabled(self.main.page("TestViewPage") is not None)
        self.tabs.setCurrentIndex(2)

    def next_test(self):
        p = self.project
        if p is None or self.session is not None:
            return
        pending = [t for t in p.tests if t.status == "pending"]
        last = self.last_test_id or 0
        nxt = next((t for t in pending if t.id > last), pending[0] if pending else None)
        if nxt is not None:
            self.test_combo.setCurrentIndex(max(0, self.test_combo.findData(nxt.id)))
        else:
            self.test_combo.setCurrentIndex(0)
            ids = [a.id for a in p.animals]
            cur = self.animal.currentText()
            if cur in ids and ids.index(cur) + 1 < len(ids):
                self.animal.setCurrentText(ids[ids.index(cur) + 1])
        self.tabs.setCurrentIndex(0)
        self._hold_finished = False
        self._set_state_display("preview" if self.grabber else "idle")
        self._update_buttons()
        if self.simulating and self.grabber is not None:
            self.grabber.restart()

    # ================================================================== manual scoring
    def _update_keys_label(self):
        p = self.project
        bs = [b for b in (p.behaviours if p else []) if b.key]
        if bs:
            self.keys_lbl.setText("Scoring keys while running: " +
                                  ", ".join(f"<b>{b.key}</b> {b.name}" for b in bs))
        else:
            self.keys_lbl.setText("Define behaviours with keys on the Experiment page to score live.")

    def _enable_shortcuts(self, on: bool):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        for w in self.setup_widgets + [self.rule_editor] + self._proc_buttons:
            w.setEnabled(not on)
        if not on or self.project is None:
            return
        for b in self.project.behaviours:
            if not b.key:
                continue
            sc = QShortcut(QKeySequence(b.key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(lambda k=b.key: self.score_key(k))
            self._shortcuts.append(sc)

    def score_key(self, key: str) -> bool:
        s = self.session
        if s is None or s.state != "running" or self.project is None:
            return False
        b = next((b for b in self.project.behaviours if b.key.lower() == key.lower()), None)
        if b is None:
            return False
        with self._lock:
            t = round(s.elapsed, 3)
            if b.kind == "state":
                ev = self._open_states.pop(b.name, None)
                if ev is not None:
                    ev["t_end"] = t
                    self._log(f"{fmt_time(t)}  {b.name} ends")
                    return True
                ev = {"behaviour": b.name, "t": t, "t_end": None}
                self._open_states[b.name] = ev
                self._manual.append(ev)
                self._log(f"{fmt_time(t)}  {b.name} starts")
            else:
                self._manual.append({"behaviour": b.name, "t": t, "t_end": None})
                self._log(f"{fmt_time(t)}  {b.name}")
        self.vals["events"].setText(str(len(s.events) + len(self._manual)))
        return True

    # ================================================================== procedures editor
    def _load_procedures(self):
        p = self.project
        cur = self.proc_table.currentRow()
        self.proc_table.setRowCount(0)
        for r in (p.procedures if p else []):
            self._append_rule_row(r)
        if self.proc_table.rowCount():
            self.proc_table.selectRow(min(max(cur, 0), self.proc_table.rowCount() - 1))
        self._load_rule_editor()

    def _append_rule_row(self, rule: dict):
        r = self.proc_table.rowCount()
        self.proc_table.insertRow(r)
        self._fill_rule_row(r, rule)

    def _fill_rule_row(self, r: int, rule: dict):
        t = self.proc_table
        t.setItem(r, 0, QTableWidgetItem(TRIGGER_LABELS.get(rule.get("trigger"), rule.get("trigger", "?"))))
        t.setItem(r, 1, QTableWidgetItem(ACTION_LABELS.get(rule.get("action"), rule.get("action", "?"))))
        d = procs.describe(rule)
        it = QTableWidgetItem(d)
        it.setToolTip(d)
        t.setItem(r, 2, it)
        t.resizeRowToContents(r)

    def _load_rule_editor(self):
        p = self.project
        r = self.proc_table.currentRow()
        rules = p.procedures if p else []
        ok = 0 <= r < len(rules)
        self.rule_editor.setEnabled(ok and self.session is None)
        self._loading = True
        try:
            self.r_zone.clear()
            self.r_zone.addItems([""] + getattr(self, "_zone_names", []))
            if ok:
                rule = rules[r]
                self.r_trigger.setCurrentIndex(max(0, self.r_trigger.findData(rule.get("trigger", "time"))))
                self.r_zone.setCurrentText(rule.get("zone", ""))
                self.r_time.setValue(float(rule.get("time_s", 0) or 0))
                self.r_delay.setValue(float(rule.get("delay_s", 0) or 0))
                self.r_action.setCurrentIndex(max(0, self.r_action.findData(rule.get("action", "mark"))))
                self.r_payload.setText(str(rule.get("payload", "")))
                self.rule_desc.setText(procs.describe(rule))
            else:
                self.rule_desc.setText(f"{len(rules)} rule(s). Add a rule or select one to edit it.")
        finally:
            self._loading = False
        self._update_rule_fields()

    def _update_rule_fields(self):
        trig = self.r_trigger.currentData()
        self.r_zone.setEnabled(trig in ("zone_enter", "zone_exit"))
        self.r_time.setEnabled(trig == "time")
        act = self.r_action.currentData()
        self.r_payload.setEnabled(act not in ("beep", "end_test"))
        self.r_payload.setPlaceholderText({"serial": "Text sent to the serial port, e.g. LED1 ON",
                                           "ttl": "Command for the I/O board, e.g. PIN3 HIGH",
                                           "mark": "Event name, e.g. Tone",
                                           "log": "Message written to the log"}.get(act, ""))

    def _editor_rule(self) -> dict:
        rule = {"trigger": self.r_trigger.currentData(), "action": self.r_action.currentData()}
        if rule["trigger"] in ("zone_enter", "zone_exit"):
            rule["zone"] = self.r_zone.currentText().strip()
        if rule["trigger"] == "time":
            rule["time_s"] = round(self.r_time.value(), 3)
        if self.r_delay.value():
            rule["delay_s"] = round(self.r_delay.value(), 3)
        payload = self.r_payload.text()
        if payload and rule["action"] not in ("beep", "end_test"):
            rule["payload"] = payload
        return rule

    def _rule_edited(self, *_):
        self._update_rule_fields()
        if self._loading or self.project is None:
            return
        r = self.proc_table.currentRow()
        if not 0 <= r < len(self.project.procedures):
            return
        rule = self._editor_rule()
        if rule != self.project.procedures[r]:
            self.project.procedures[r] = rule
            self.main.mark_dirty()
        self._fill_rule_row(r, rule)
        self.rule_desc.setText(procs.describe(rule))

    def add_rule(self, rule: dict | None = None):
        if self.project is None:
            return
        rule = dict(rule or {"trigger": "time", "time_s": 60, "action": "mark", "payload": "Mark"})
        self.project.procedures.append(rule)
        self.main.mark_dirty()
        self._append_rule_row(rule)
        self.proc_table.selectRow(self.proc_table.rowCount() - 1)
        self._load_rule_editor()

    def remove_rule(self):
        r = self.proc_table.currentRow()
        if self.project is not None and 0 <= r < len(self.project.procedures):
            del self.project.procedures[r]
            self.main.mark_dirty()
            self.proc_table.removeRow(r)
            self._load_rule_editor()

    # ================================================================== helpers
    def _log(self, msg: str):
        self.log.addItem(msg)
        self.log.scrollToBottom()

    def _set_state_display(self, state: str):
        if state == self._shown_state:
            return
        self._shown_state = state
        text, col = STATE_STYLE.get(state, STATE_STYLE["idle"])
        self.state_lbl.setText(text)
        self.state_lbl.setStyleSheet(f"background:{col};color:white;font-weight:700;font-size:15px;"
                                     "border-radius:6px;padding:8px 10px")
        if state in ("idle", "preview"):
            for k in ("elapsed", "remaining", "distance", "events"):
                self.vals[k].setText("—")
            if state == "idle":
                self.vals["zone"].setText("—")

    def _update_buttons(self):
        armed = self.session is not None
        has = self.project is not None
        self.arm_btn.setEnabled(has and not armed)
        self.stop_btn.setEnabled(armed)
        self.next_btn.setEnabled(has and not armed)
        self.preview_btn.setText("Stop preview" if self.grabber is not None else "Start preview")
        self.preview_btn.setEnabled(has and not armed)
        self.capture_btn.setEnabled(not armed)
