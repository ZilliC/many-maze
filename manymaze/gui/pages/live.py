"""Live testing page: track animals in real time from cameras (or video files simulating them).

Three modes: one test; several tests at once (several cameras and / or several apparatus in one camera image,
with collective start / pause / stop); observation only (no camera, a clock and scoring keys).
"""

from __future__ import annotations

import copy
import datetime as _dt
import os
import re
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QThread, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QStackedWidget, QStyle, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout, QWidget)

from ...core import procedures as procs
from ...core import workflow as wf
from ...core.camera import CameraView, SourceSpec, TransformedSource, camera_settings, set_camera_settings
from ...core.live import LiveSession, ObservationSession, open_devices
from ...core.livegroup import DEFAULT_START_KEYS, DEFAULT_STOP_KEYS, ClockSchedule, LiveGroup, save_live_test
from ...core.procedures import Outputs
from ...core.tracking import ArenaTracker, DetectionSettings, compute_background, draw_overlay, median_background
from ...core.video import VIDEO_EXTENSIONS, VideoRecorder, VideoSource, list_cameras
from ..live_widgets import CameraOptionsDialog, MonitorPanel, MosaicView, ObservationPanel
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
    "paused": ("Paused", "#ca8a04"),
    "finished": ("Finished", "#7c3aed"),
}
START_MODES = [("immediate", "Immediately when armed"), ("on_detection", "When the animal is detected"),
               ("experimenter_leaves", "When the experimenter leaves the view"),
               ("manual", "On a start key (keyboard / remote)"), ("scheduled", "At a clock time")]
MODES = [("single", "One test"), ("multi", "Several tests at once"), ("observe", "Observation only (no camera)")]
_KEY_NAMES = {"pagedown": "PgDown", "pgdown": "PgDown", "pagedn": "PgDown", "pageup": "PgUp", "pgup": "PgUp",
              "space": "Space", "esc": "Esc", "escape": "Esc", "enter": "Return", "return": "Return"}


def qt_key(name: str) -> str:
    """Key name as typed by the user (Space, PageDown, F5, B…) → QKeySequence text."""
    n = name.strip()
    return _KEY_NAMES.get(n.lower(), n.upper() if len(n) == 1 else n)


def parse_keys(text: str) -> list[str]:
    return [k.strip() for k in re.split(r"[,;]", text or "") if k.strip()]


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
    probe = project.recordings_dir() / f".probe_{threading.get_ident()}.mp4"
    try:
        VideoRecorder(str(probe), fps, size).close()
        return str(base.with_suffix(".mp4"))
    except Exception:
        return str(base.with_suffix(".avi"))
    finally:
        if probe.exists():
            probe.unlink()


def _source_background(src) -> np.ndarray | None:
    """Median of frames sampled through a (transformed) file source."""
    n = getattr(src, "frame_count", 0)
    if not n:
        return None
    frames = [f for f in (src.frame_at(int(i)) for i in np.unique(np.linspace(0, n - 1, 21).astype(int)))
              if f is not None]
    src.seek(0)
    return median_background(frames) if frames else None


class FrameGrabber(QThread):
    """Reads frames from a camera / file and runs `handler(frame, t)` on them, off the UI thread.

    handler returns (display_frame, info); frame_ready is only emitted when the UI has consumed the previous
    frame (call ack()), so a slow UI drops display frames but never tracking frames.
    Files are paced at their frame rate × speed and loop while `loop` is True.  An optional CameraView (region,
    zoom, rotation, flip) and a second source merged into the same image are applied to every frame.
    """

    frame_ready = Signal(object, object)
    opened = Signal(int, int, float)
    background_ready = Signal(object)
    ended = Signal()
    failed = Signal(str)

    def __init__(self, source, handler, size=None, fps=None, parent=None, view: CameraView | None = None,
                 second=None, layout: str = "side"):
        super().__init__(parent)
        self.source = source
        self.handler = handler
        self.size = size
        self.req_fps = fps
        self.view = view or CameraView()
        self.second = second
        self.layout = layout
        self.loop = True
        self.speed = 1.0
        self._stop = False
        self._restart = False
        self._busy = False
        self.fps = 25.0
        self.src = None

    def stop(self):
        self._stop = True

    def restart(self):
        self._restart = True

    def ack(self):
        self._busy = False

    def raw_frames(self):
        src = self.src
        if isinstance(src, TransformedSource):
            return src.last_raw, src.last_raw2
        return None, None

    def run(self):
        try:
            w, h = self.size or (None, None)
            src = VideoSource(self.source, w, h, self.req_fps or None)
            if self.second is not None or not self.view.is_identity:
                second = None
                if self.second is not None:
                    try:
                        second = VideoSource(self.second, w, h, self.req_fps or None)
                    except Exception:
                        src.release()
                        raise
                src = TransformedSource(src, self.view, second, self.layout)
        except Exception as e:
            self.failed.emit(f"Cannot open {'camera' if isinstance(self.source, int) else 'video'}: {e}")
            return
        self.src = src
        self.fps = src.fps or 25.0
        self.opened.emit(src.width, src.height, self.fps)
        try:
            if not src.is_camera:
                try:
                    if isinstance(src, TransformedSource):
                        bg = _source_background(src)
                    else:
                        bg = compute_background(str(self.source), DetectionSettings(background_samples=21))
                    if bg is not None:
                        self.background_ready.emit(bg)
                except Exception:
                    pass
            self._loop(src)
        finally:
            src.release()

    def _loop(self, src):
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
        self._log_seen = 0
        self._fired_seen = 0
        self._shortcuts: list[QShortcut] = []
        self.last_results: list[dict] = []
        self._hold_finished = False
        self._shown_state = None
        self._bg_mode_value = "frame"  # plain copies of widget state read from the grabber thread
        self._source_is_file = False
        self._outputs: Outputs | None = None
        self._schedule: ClockSchedule | None = None  # single-test scheduled start
        self._view = CameraView()  # single-test camera options
        self._second = None
        self._merge_layout = "side"
        self.devices = None  # core.iodevices.DeviceManager while tests run
        self.mode = "single"
        # several tests at once
        self.group = LiveGroup()
        self._group_bgs: dict[str, np.ndarray] = {}
        self._row_widgets: dict[int, dict] = {}
        self._group_outputs: Outputs | None = None
        # observation only
        self.obs: ObservationSession | None = None
        self.obs_test = None
        self._obs_new = False
        self._key_filter = False

        # ---- mode bar --------------------------------------------------------------------------------
        self.mode_btns: dict[str, QPushButton] = {}
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        for i, (key, lbl) in enumerate(MODES):
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setMinimumHeight(28)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addButton(b)
            self.mode_btns[key] = b
            mode_bar.addWidget(b)
        self.mode_btns["single"].setChecked(True)
        mode_bar.addStretch()

        # ---- left: one stacked panel per mode -------------------------------------------------------
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self._build_single())
        self.left_stack.addWidget(self._build_multi())
        self.obs_panel = ObservationPanel()
        self.obs_panel.start_clicked.connect(self.obs_start)
        self.obs_panel.pause_clicked.connect(self.obs_pause)
        self.obs_panel.stop_clicked.connect(lambda: self.obs_stop(save=True))
        if self.obs_panel.pad is not None:
            self.obs_panel.pad.pressed.connect(lambda n: self._pad(n, True))
            self.obs_panel.pad.released.connect(lambda n: self._pad(n, False))
        self.left_stack.addWidget(self.obs_panel)
        left = QVBoxLayout()
        left.addLayout(mode_bar)
        left.addWidget(self.left_stack, 1)

        # ---- right: tabs ----------------------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.setup_tab = self._build_setup()
        self.monitor = MonitorPanel()
        mon_scroll = QScrollArea()
        mon_scroll.setWidget(self.monitor)
        mon_scroll.setWidgetResizable(True)
        mon_scroll.setFrameShape(QScrollArea.NoFrame)
        self.monitor_tab = mon_scroll
        self.procedures_tab = self._build_procedures()
        self.results_tab = self._build_results()
        self.log_tab = self._build_log()
        self.tabs.addTab(self.setup_tab, "Setup")
        self.tabs.addTab(self.monitor_tab, "Monitor")
        self.tabs.addTab(self.procedures_tab, "Procedures")
        self.tabs.addTab(self.results_tab, "Results")
        self.tabs.addTab(self.log_tab, "Log")
        self.tabs.setFixedWidth(410)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.tabs)
        self._set_state_display("idle")
        self._update_buttons()

        # ≤ 5 Hz: monitor, session table, schedules, saving finished tests; ~12 Hz: mosaic images
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start()
        self._mosaic_timer = QTimer(self)
        self._mosaic_timer.setInterval(80)
        self._mosaic_timer.timeout.connect(self._refresh_mosaic)

    # ================================================================== UI construction
    def _build_single(self) -> QWidget:
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
        self.arm_btn.setStyleSheet("QPushButton{font-weight:600}")
        self.arm_btn.clicked.connect(self._arm_clicked)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop_clicked)
        self.next_btn = QPushButton("Next test ▸")
        self.next_btn.clicked.connect(self.next_test)
        self.preview_btn = QPushButton("Start preview")
        self.preview_btn.clicked.connect(self._toggle_preview)
        self.keys_lbl = QLabel()
        self.keys_lbl.setWordWrap(True)
        self.keys_lbl.setStyleSheet("color:palette(mid)")
        ctl = QHBoxLayout()
        for b in (self.preview_btn, self.arm_btn, self.pause_btn, self.stop_btn, self.next_btn):
            b.setMinimumHeight(34)
            ctl.addWidget(b)
        self.score_pad = _scoring_pad()
        if self.score_pad is not None:
            self.score_pad.pressed.connect(lambda n: self._pad(n, True))
            self.score_pad.released.connect(lambda n: self._pad(n, False))
        w = QWidget()
        left = QVBoxLayout(w)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.placeholder)
        left.addWidget(self.view, 1)
        left.addWidget(dash)
        if self.score_pad is not None:
            left.addWidget(self.score_pad)
        left.addLayout(ctl)
        left.addWidget(self.keys_lbl)
        return w

    def _build_multi(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.mosaic = MosaicView()
        self.mosaic.tile_clicked.connect(self._tile_clicked)
        v.addWidget(self.mosaic, 1)

        bar = QHBoxLayout()
        self.cams_btn = QPushButton("Start cameras")
        self.cams_btn.clicked.connect(self._toggle_cameras)
        self.g_arm = QPushButton("Arm all")
        self.g_arm.setStyleSheet("QPushButton{font-weight:600}")
        self.g_arm.clicked.connect(lambda: self.arm_all())
        self.g_start = QPushButton("Start all now")
        self.g_start.clicked.connect(self.start_all)
        self.g_pause = QPushButton("Pause all")
        self.g_pause.clicked.connect(self.group_pause_all)
        self.g_resume = QPushButton("Resume all")
        self.g_resume.clicked.connect(self.group_resume_all)
        self.g_stop = QPushButton("Stop all")
        self.g_stop.clicked.connect(self._stop_all_clicked)
        for b in (self.cams_btn, self.g_arm, self.g_start, self.g_pause, self.g_resume, self.g_stop):
            b.setMinimumHeight(32)
            bar.addWidget(b)
        v.addLayout(bar)

        self.sess_table = QTableWidget(0, 8)
        self.sess_table.setHorizontalHeaderLabels(["Source", "Apparatus", "Animal", "Stage", "Trial", "State",
                                                   "Time", ""])
        self.sess_table.verticalHeader().hide()
        self.sess_table.verticalHeader().setDefaultSectionSize(30)
        self.sess_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sess_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sess_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.sess_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wd in ((1, 110), (2, 78), (3, 70), (4, 52), (5, 78), (6, 64), (7, 92)):
            self.sess_table.setColumnWidth(c, wd)
        self.sess_table.setFixedHeight(172)
        self.sess_table.currentCellChanged.connect(lambda *_: self._refresh_monitor(force=True))
        v.addWidget(self.sess_table)

        row = QHBoxLayout()
        self.add_src_btn = QToolButton()
        self.add_src_btn.setText("Add source ▾")
        self.add_src_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self.add_src_btn)
        m.addAction("Camera…", self._add_camera_source)
        m.addAction("Video file (simulated camera)…", self._add_file_source)
        self.add_src_btn.setMenu(m)
        self.add_src_btn.setMinimumHeight(28)
        self.cam_opts_multi = QPushButton("Camera options…")
        self.cam_opts_multi.clicked.connect(self.multi_camera_options)
        self.add_sess_btn = QPushButton("Add session")
        self.add_sess_btn.clicked.connect(lambda: self.add_session_row())
        self.rm_sess_btn = QPushButton("Remove")
        self.rm_sess_btn.clicked.connect(self.remove_session_row)
        self.cap_bgs_btn = QPushButton("Capture backgrounds")
        self.cap_bgs_btn.setToolTip("Take the current image of every camera as its empty-arena background.")
        self.cap_bgs_btn.clicked.connect(self.capture_group_backgrounds)
        row.addWidget(self.add_src_btn)
        for b in (self.cam_opts_multi, self.add_sess_btn, self.rm_sess_btn, self.cap_bgs_btn):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.group_lbl = QLabel()
        self.group_lbl.setWordWrap(True)
        self.group_lbl.setStyleSheet("color:palette(mid)")
        v.addWidget(self.group_lbl)
        return w

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
        self.camera.currentIndexChanged.connect(self._camera_changed)
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
        self.cam_opts_btn = QPushButton("Camera options…")
        self.cam_opts_btn.setToolTip("Region of the image, digital zoom / pan, rotation, flip, merge two cameras")
        self.cam_opts_btn.clicked.connect(self.camera_options)
        self.view_lbl = QLabel()
        self.view_lbl.setStyleSheet("color:palette(mid)")
        self.view_lbl.setWordWrap(True)
        f.addRow(self.cam_radio)
        f.addRow("Device", row)
        f.addRow("Resolution", self.resolution)
        f.addRow("Frame rate", self.cam_fps)
        f.addRow(self.sim_radio)
        f.addRow("File", srow)
        f.addRow("Speed", self.sim_speed)
        orow = QHBoxLayout()
        orow.addWidget(self.cam_opts_btn)
        orow.addWidget(self.view_lbl, 1)
        f.addRow(orow)
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
        f.addRow("Run", self.test_combo)
        f.addRow("Animal", self.animal)
        f.addRow("Stage", self.stage)
        f.addRow("Trial", self.trial)
        f.addRow("Apparatus", self.apparatus)
        v.addWidget(tb)

        sb = QGroupBox("Start and end")
        f = QFormLayout(sb)
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
        for w in (self.start_keys, self.stop_keys):
            w.editingFinished.connect(self._save_live_settings)
        self.sched_time.timeChanged.connect(self._save_live_settings)
        self.sched_daily.toggled.connect(self._save_live_settings)
        f.addRow("Duration", self.duration)
        f.addRow("Test starts", self.start_mode)
        f.addRow("Start time", trow)
        f.addRow("Start keys", self.start_keys)
        f.addRow("Stop keys", self.stop_keys)
        v.addWidget(sb)

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
        self.record_overlay = QCheckBox("Burn time and events into the video")
        self.record_overlay.setToolTip("Write the test time, the clock time and the latest event labels on the "
                                       "recorded video (otherwise the recording is clean).")
        self.record_overlay.toggled.connect(self._save_live_settings)
        self.lost_warn = QDoubleSpinBox()
        self.lost_warn.setRange(0, 3600)
        self.lost_warn.setDecimals(1)
        self.lost_warn.setValue(3.0)
        self.lost_warn.setSuffix(" s")
        self.lost_warn.setSpecialValueText("Never")
        self.lost_warn.valueChanged.connect(self._save_live_settings)
        self.serial = QComboBox()
        self.serial.setEditable(True)
        self.serial_note = QLabel()
        self.serial_note.setWordWrap(True)
        self.serial_note.setStyleSheet("color:palette(mid)")
        f.addRow("Background", self.bg_mode)
        f.addRow("", self.capture_btn)
        f.addRow("", self.bg_status)
        f.addRow("", self.record)
        f.addRow("", self.record_overlay)
        f.addRow("Warn if lost for", self.lost_warn)
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
        self._source_mode_changed()
        self._start_mode_changed()
        return scroll

    def _build_procedures(self) -> QWidget:
        legacy = self._build_rules()
        # the full procedure editor; the simple trigger → action rules stay available in a second tab
        self.proc_editor = None
        try:
            from ..procedure_editor import ProcedureEditor
            self.proc_editor = ProcedureEditor()
            self.proc_editor.changed.connect(self.main.mark_dirty)
        except Exception:
            traceback.print_exc()
        if self.proc_editor is None:
            return legacy
        tabs = QTabWidget()
        tabs.addTab(self.proc_editor, "Procedures")
        tabs.addTab(legacy, "Simple rules")
        return tabs

    def _build_rules(self) -> QWidget:
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
        self._close_touch()
        self.stop_test(save=False, quiet=True)
        self.stop_preview()
        self.group.close()
        self.group = LiveGroup()
        self._group_bgs = {}
        self.obs_stop(save=False)
        self._close_devices()
        self._background = None
        self._file_background = None
        self.bg_status.setText("No background captured")
        self.results.setRowCount(0)
        self.results_title.setText("Results of the last live test appear here.")
        self.log.clear()
        self.monitor.clear()
        if project is not None:
            self.duration.setValue(project.test_duration_s)
            self.start_mode.setCurrentIndex(1 if project.start_mode == "on_detection" else 0)
            self._load_live_settings()
            self._restore_group_layout()
        self._rebuild_session_table()
        self.on_show()

    def on_show(self):
        p = self.project
        if p is None:
            return
        self._loading = True
        armed = self.session is not None or self.obs is not None
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
        if self.proc_editor is not None and hasattr(self.proc_editor, "set_project"):
            try:
                self.proc_editor.set_project(p)
            except Exception:
                pass
        self._load_single_view()
        self._refresh_row_choices()
        sig = [(b.name, b.key, b.kind, b.group, b.color) for b in p.behaviours]
        if sig != getattr(self, "_pad_sig", None):
            self._pad_sig = sig
            for pad in (self.obs_panel.pad, self.score_pad):
                if pad is not None:
                    pad.set_behaviours(p.behaviours)
        if self.score_pad is not None:
            self.score_pad.setVisible(bool(p.behaviours))
        self._update_keys_label()
        self._update_buttons()

    def on_hide(self):
        if self.any_active():
            self.main.status("Live tests are still running in the background — return to “Live testing” to "
                             "follow or stop them.", 10000)
            return
        self.stop_preview()
        self.stop_cameras()

    def _touch_window(self):
        """The touch-screen stimulus window when enabled for this experiment (shown full screen on its display)."""
        cfg = (self.project.settings_extra.get("touchscreen") or {}) if self.project is not None else {}
        if not cfg.get("enabled"):
            self._close_touch()
            return None
        if getattr(self, "_touch", None) is None:
            from ..touchscreen import TouchStimulusWindow
            self._touch = TouchStimulusWindow.from_project(self.project)
            self._touch.show_on_screen()
        return self._touch

    def _close_touch(self):
        if getattr(self, "_touch", None) is not None:
            self._touch.close()
            self._touch = None

    def shutdown(self):
        self._close_touch()
        if self.session is not None:
            self.stop_test(save=True, quiet=True)
        if self.obs is not None:
            self.obs_stop(save=True)
        if any(e.state in ("running", "paused") for e in self.group.entries):
            self.group.stop_all(save=True)
            self._save_finished_entries()
        self.group.close()
        self._save_finished_entries()
        self.stop_preview()
        self._close_devices()
        self._ui_timer.stop()
        self._mosaic_timer.stop()
        if self._scan_worker is not None:
            self._scan_worker.wait(3000)

    def any_active(self) -> bool:
        return (self.session is not None or (self.obs is not None and self.obs.state != "finished")
                or any(e.state in ("waiting", "running", "paused") for e in self.group.entries))

    # ================================================================== modes
    def set_mode(self, mode: str) -> bool:
        if mode == self.mode:
            self.mode_btns[mode].setChecked(True)
            return True
        if self.any_active():
            QMessageBox.information(self, "Live testing", "Stop the running test(s) before changing the mode.")
            self.mode_btns[self.mode].setChecked(True)
            return False
        if self.mode == "single":
            self.stop_preview()
        elif self.mode == "multi":
            self.stop_cameras()
        self.mode = mode
        self.mode_btns[mode].setChecked(True)
        self.left_stack.setCurrentIndex([m for m, _ in MODES].index(mode))
        self.src_box.setVisible(mode == "single")
        self.test_box.setVisible(mode != "multi")
        self.det_box.setVisible(mode != "observe")
        if mode == "multi":
            self._rebuild_session_table()
            self._mosaic_timer.start()
        else:
            self._mosaic_timer.stop()
        self._update_keys_label()
        self._update_buttons()
        self._refresh_monitor(force=True)
        return True

    # ================================================================== settings persistence
    def _live_settings(self) -> dict:
        return self.project.settings_extra.setdefault("live", {}) if self.project is not None else {}

    def _load_live_settings(self):
        d = (self.project.settings_extra.get("live") or {}) if self.project is not None else {}
        self._loading = True
        try:
            self.start_keys.setText(", ".join(d.get("start_keys", DEFAULT_START_KEYS)))
            self.stop_keys.setText(", ".join(d.get("stop_keys", DEFAULT_STOP_KEYS)))
            self.record_overlay.setChecked(bool(d.get("record_overlay", False)))
            self.lost_warn.setValue(float(d.get("lost_warning_s", 3.0)))
            hh, mm = (str(d.get("schedule_at", "05:00")) + ":0").split(":")[:2]
            self.sched_time.setTime(QTime(int(hh) % 24, int(mm) % 60))
            self.sched_daily.setChecked(bool(d.get("schedule_daily", False)))
        finally:
            self._loading = False

    def _save_live_settings(self, *_):
        if self._loading or self.project is None:
            return
        new = {"start_keys": parse_keys(self.start_keys.text()), "stop_keys": parse_keys(self.stop_keys.text()),
               "record_overlay": self.record_overlay.isChecked(), "lost_warning_s": self.lost_warn.value(),
               "schedule_at": self.sched_time.time().toString("HH:mm"),
               "schedule_daily": self.sched_daily.isChecked()}
        d = self._live_settings()
        if any(d.get(k) != v for k, v in new.items()):
            d.update(new)
            self.main.mark_dirty()
        self.group.start_keys, self.group.stop_keys = new["start_keys"], new["stop_keys"]
        self._update_keys_label()

    def _start_mode_changed(self, *_):
        sched = self.start_mode.currentData() == "scheduled"
        self.sched_time.setEnabled(sched)
        self.sched_daily.setEnabled(sched)

    # ================================================================== source / preview
    def _source_mode_changed(self, *_):
        cam = self.cam_radio.isChecked()
        for w in (self.camera, self.scan_btn, self.resolution, self.cam_fps):
            w.setEnabled(cam)
        for w in (self.sim_path, self.sim_browse, self.sim_speed):
            w.setEnabled(not cam)
        self._load_single_view()

    def _camera_changed(self, *_):
        self._load_single_view()

    def _speed_changed(self):
        sp = self.sim_speed.currentData() or 1.0
        if self.grabber is not None:
            self.grabber.speed = sp
        self.group.set_speed(sp)

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
        self._load_single_view()

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

    def _single_key(self) -> str | None:
        src = self._source()
        return SourceSpec(src).key if src is not None else None

    def _load_single_view(self):
        key = self._single_key()
        d = camera_settings(self.project, key) if key else {}
        self._view = CameraView.from_dict(d.get("view"))
        self._second = d.get("second")
        self._merge_layout = d.get("layout", "side")
        self.view_lbl.setText(_describe_view(self._view, self._second, self._merge_layout))

    def _merge_choices(self, exclude=None) -> list[tuple[str, object]]:
        out = [(self.camera.itemText(i), self.camera.itemData(i)) for i in range(self.camera.count())]
        files = [self.sim_path.text().strip()] + [s.source for s in self.group.sources.values() if s.is_file]
        for f in dict.fromkeys(x for x in files if x):
            out.append((Path(f).name, f))
        return [(lbl, s) for lbl, s in out if s != exclude]

    def camera_options(self) -> bool:
        """Region / zoom / rotation / flip / merge options of the single-test source."""
        src = self._source()
        if src is None:
            QMessageBox.information(self, "Camera options", "Choose a camera or a video file first.")
            return False
        raw, raw2 = self.grabber.raw_frames() if self.grabber is not None else (None, None)
        if raw is None:
            raw = self._last_frame if (self._view.is_identity and self._second is None) else None
        if raw is None and isinstance(src, str):
            raw = _first_frame(src)
        if raw2 is None and isinstance(self._second, str):
            raw2 = _first_frame(self._second)
        dlg = CameraOptionsDialog(raw, self._view, self._second, self._merge_layout, self._merge_choices(src),
                                  raw2, self)
        if dlg.exec() != QDialog.Accepted:
            return False
        self._apply_single_view(dlg.result())
        return True

    def _apply_single_view(self, res: dict):
        key = self._single_key()
        view = CameraView.from_dict(res.get("view"))
        second = res.get("second")
        layout = res.get("layout", "side")
        settings = {}
        if not view.is_identity:
            settings["view"] = view.to_dict()
        if second is not None:
            settings.update(second=second, layout=layout)
        set_camera_settings(self.project, key, settings)
        self.main.mark_dirty()
        self._view, self._second, self._merge_layout = view, second, layout
        self.view_lbl.setText(_describe_view(view, second, layout))
        self._file_background = None
        self._background = None
        self.bg_status.setText("No background captured")
        if self.grabber is not None and self.session is None:
            self.stop_preview()
            self.start_preview()

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
        g = FrameGrabber(src, self.process_frame, size=size, fps=fps, parent=self, view=self._view,
                         second=self._second, layout=self._merge_layout)
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
        if self.session.state in ("running", "paused"):
            self._log("End of the video file — test finished.")
            self.stop_test(save=True, quiet=True)
        else:
            self._log("End of the video file before the test started.")
            self.stop_test(save=False, quiet=True)

    # ================================================================== frame processing (one test)
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

    def _detection_settings(self, test=None) -> DetectionSettings:
        s = DetectionSettings.from_dict(self.project.detection.to_dict())
        test = test if test is not None else self.test
        if test is not None and test.detection:
            s = DetectionSettings.from_dict({**s.to_dict(), **test.detection})
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
                n = 250
                trail = list(zip(s.cols["x"][-n:], s.cols["y"][-n:]))
                elapsed = s.elapsed if state != "waiting" else 0.0
                info = {"state": state, "elapsed": elapsed, "duration": s.duration_s, "events": len(s.events),
                        "fired": list(getattr(s.engine, "fired", [])),
                        "outputs": list(s.outputs.log) if s.outputs is not None else [],
                        "proc_log": list(s.log), "phase": s.start_phase}
                info["distance"] = s.stats.distance
                info["unit"] = s.stats.unit
            else:
                if self._preview_tracker is None:
                    self._preview_tracker = self._make_preview_tracker(frame, app)
                dets, _fg = self._preview_tracker.process(frame) if self._preview_tracker else ([], None)
                d = dets[0] if dets else None
                info = {"state": "preview", "distance": 0.0, "unit": app.unit if app else "px"}
            info["detected"] = bool(d is not None and d.detected)
            zones = []
            if s is not None and s.state in ("running", "paused"):
                zones = s.stats.current_zones() if info["detected"] else []
            elif app is not None and d is not None and d.detected:
                zm = app.zone_membership(np.array([d.x]), np.array([d.y]))
                zones = [k for k, v in zm.items() if bool(np.asarray(v).ravel()[0])]
            info["zones"] = zones
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
        waiting = {"experimenter": "WAITING FOR EXPERIMENTER", "leaving": "WAITING FOR HAND TO LEAVE",
                   "animal": "WAITING FOR ANIMAL"}.get(info.get("phase") or "", "WAITING FOR ANIMAL")
        txt = {"running": f"REC {fmt_time(info.get('elapsed', 0))}", "waiting": waiting,
               "paused": f"PAUSED {fmt_time(info.get('elapsed', 0))}", "finished": "FINISHED"}.get(st, "PREVIEW")
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
        if state != getattr(self, "_btn_state", None):
            self._btn_state = state
            self._update_buttons()
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
            outs = info["outputs"] + [f"{fmt_time(t)} {m}" for t, m in info["proc_log"]]
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

    def _prepare_test(self, need_apparatus: bool = True):
        """Create / update the test described by the Test group. Returns (test, is_new) or (None, False)."""
        p = self.project
        if p is None:
            return None, False
        if p.path is None:
            QMessageBox.information(self, "Live testing", "Save the experiment first.")
            return None, False
        if need_apparatus and not p.apparatus:
            QMessageBox.information(self, "Live testing", "Draw an apparatus first (Apparatus page).")
            return None, False
        aid = self.animal.currentText().strip()
        if not aid:
            QMessageBox.information(self, "Live testing", "Choose or type the animal ID.")
            return None, False
        tid = self.test_combo.currentData()
        test = p.get_test(tid) if tid is not None else None
        new = test is None
        if p.get_animal(aid) is None:
            p.ensure_animal(aid)
        app_name = self.apparatus.currentText() or (p.apparatus[0].name if p.apparatus else "")
        if test is None:
            test = p.add_test("", aid, app_name, stage=self.stage.currentText().strip(), trial=self.trial.value())
        else:
            test.animal_id = aid
            test.apparatus = app_name
            test.stage = self.stage.currentText().strip()
            test.trial = self.trial.value()
        dur = self.duration.value()
        test.duration_s = 0.0 if abs(dur - p.test_duration_s) < 1e-9 else dur
        self.main.mark_dirty()
        return test, new

    def _open_devices(self):
        if self.devices is None:
            self.devices = open_devices(self.project)
        return self.devices

    def _close_devices(self):
        if self.devices is not None and not self.any_active():
            try:
                self.devices.close()
            except Exception:
                pass
            self.devices = None

    def _session_mode(self) -> str:
        m = self.start_mode.currentData()
        return "manual" if m == "scheduled" else m

    def _new_schedule(self, entry_ids=None):
        at = self.sched_time.time().toString("HH:mm")
        if entry_ids is None:
            return ClockSchedule(at, False)
        return self.group.schedule(at, self.sched_daily.isChecked(), entry_ids)

    # ================================================================== run control (one test)
    def _arm_clicked(self):
        s = self.session
        if s is not None and s.state == "waiting":
            s.request_start()  # armed: the button now starts the test immediately
            self._log("Start requested.")
            return
        self.arm()

    def arm(self) -> bool:
        p = self.project
        if p is None or self.session is not None:
            return False
        test, new = self._prepare_test()
        if test is None:
            return False
        self.test = test
        self._new_test = new
        if not _confirm_id(self, test):
            self._discard_new_test()
            return False
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
        self._outputs = outputs
        for line in outputs.log:
            self._log(line)
        dur = self.duration.value()
        touch = self._touch_window()
        session = LiveSession(p.get_apparatus(test.apparatus), settings, duration_s=dur,
                              start_mode=self._session_mode(), procedures=copy.deepcopy(p.procedures),
                              outputs=outputs, record_path=self._record_path, fps=self._fps,
                              analysis=p.analysis_for(test), devices=self._open_devices(), variables=p.variables,
                              record_overlay=self.record_overlay.isChecked(), lost_warning_s=self.lost_warn.value(),
                              name=f"Test {test.id} · {test.animal_id}", zone_overrides=test.zone_overrides,
                              on_stimulus=touch.handle if touch is not None else None)
        if touch is not None:
            touch.clear()
            touch.connect_engine(session.engine, clock=lambda: session.elapsed)
        if bg is not None:
            session.set_background(bg)
        with self._lock:
            self._apparatus = session.apparatus
            self._fired_seen = 0
            self._log_seen = len(outputs.log)
            self.session = session
        self._schedule = self._new_schedule() if self.start_mode.currentData() == "scheduled" else None
        if self.grabber is not None:
            self.grabber.loop = False
            if self.simulating:
                self.grabber.restart()
        self._enable_shortcuts(True)
        self._hold_finished = False
        self.tabs.setCurrentWidget(self.log_tab)
        when = self.start_mode.currentText().lower()
        if self._schedule is not None:
            when = f"at {self._schedule.next_fire:%H:%M} ({self._schedule.next_fire:%a %d %b})"
        self._log(f"Test {test.id} armed — animal {test.animal_id}, {'until stopped' if not dur else f'{dur:g} s'}, "
                  f"start {when}")
        self._set_state_display("waiting")
        self._update_buttons()
        return True

    def toggle_pause(self) -> bool:
        if self.mode == "observe":
            return self.obs_pause()
        s = self.session
        if s is None:
            return False
        if s.state == "running":
            s.pause()
            self._log(f"{fmt_time(s.elapsed)}  test paused")
        elif s.state == "paused":
            s.resume()
            self._log(f"{fmt_time(s.elapsed)}  test resumed")
        else:
            return False
        self._set_state_display(s.state)
        self._update_buttons()
        return True

    def _stop_clicked(self):
        s = self.session
        if s is None:
            return
        if s.state not in ("running", "paused"):
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
        self._schedule = None
        self._enable_shortcuts(False)
        if self._outputs is not None:
            self._outputs.close()
            self._outputs = None
        if self.grabber is not None:
            self.grabber.loop = True
        p, test = self.project, self.test
        self.test = None
        el = s.elapsed
        for t, msg in s.warnings:
            self._log(f"{fmt_time(t)}  warning: {msg}")
        if not save or p is None or test is None or not save_live_test(p, test, s, self._record_path):
            _remove_file(self._record_path)
            if test is not None and self._new_test and p is not None and test in p.tests:
                p.tests.remove(test)
            self._log("Test discarded.")
            self._set_state_display("preview" if self.grabber else "idle")
            self._update_buttons()
            self._close_devices()
            self.on_show()
            return
        self.main.mark_dirty()
        self.main.save()
        self.last_test_id = test.id
        self._log(f"Test {test.id} finished after {fmt_time(el)} and saved.")
        self._set_state_display("finished")
        self._hold_finished = True
        self._show_results(test)
        self._update_buttons()
        self._close_devices()
        self.on_show()
        if not quiet:
            self.main.status(f"Test {test.id} saved. Press “Next test” to continue.")

    def _discard_new_test(self):
        if self._new_test and self.test is not None and self.test in self.project.tests:
            self.project.tests.remove(self.test)
        self.test = None

    def _show_results(self, test, switch: bool = True):
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
        if switch:
            self.tabs.setCurrentWidget(self.results_tab)

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
        self.tabs.setCurrentWidget(self.setup_tab)
        self._hold_finished = False
        self._set_state_display("preview" if self.grabber else "idle")
        self._update_buttons()
        if self.simulating and self.grabber is not None:
            self.grabber.restart()

    # ================================================================== several tests at once
    def add_source(self, source, second=None, layout: str = "side") -> str:
        """Add a camera index or a video file (simulated camera) to the multi-test sources; returns its key."""
        spec = SourceSpec(source)
        d = camera_settings(self.project, spec.key)
        spec.view = CameraView.from_dict(d.get("view"))
        spec.second = second if second is not None else d.get("second")
        spec.layout = d.get("layout", layout) if second is None else layout
        if not isinstance(source, str) or str(source).isdigit():
            spec.size = self.resolution.currentData()
            spec.fps = self.cam_fps.value() or None
        key = self.group.add_source(spec)
        self._save_group_layout()
        self._refresh_row_choices()
        if self.group.runners or self.cams_btn.text().startswith("Stop"):
            self.start_cameras()
        self._update_mosaic_tiles()
        return key

    def _add_camera_source(self):
        i, ok = QInputDialog.getInt(self, "Add camera", "Camera number (0 = first camera):", 0, 0, 63)
        if ok:
            self.add_source(i)

    def _add_file_source(self):
        start = str(self.project.path) if self.project and self.project.path else str(Path.home())
        exts = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Video file to simulate a camera", start,
                                              f"Videos ({exts});;All files (*)")
        if path:
            self.add_source(path)

    def add_session_row(self, source_key: str | None = None, apparatus: str | None = None,
                        animal: str | None = None, stage: str | None = None, trial: int | None = None):
        """Add a test row (source × apparatus × animal / stage / trial) to the multi-test table."""
        p = self.project
        if p is None:
            return None
        if not self.group.sources:
            QMessageBox.information(self, "Live testing", "Add a camera or a video file first (Add source).")
            return None
        keys = list(self.group.sources)
        source_key = source_key if source_key in self.group.sources else self._selected_source() or keys[0]
        used = {e.meta.get("apparatus") for e in self.group.entries_for(source_key)}
        if apparatus is None:
            apparatus = next((a.name for a in p.apparatus if a.name not in used),
                             p.apparatus[0].name if p.apparatus else "")
        ids = [a.id for a in p.animals]
        taken = {e.meta.get("animal") for e in self.group.entries}
        if animal is None:
            animal = next((a for a in ids if a not in taken), ids[0] if ids else f"A{len(self.group.entries) + 1}")
        meta = {"apparatus": apparatus, "animal": animal,
                "stage": stage if stage is not None else (p.stages[0] if p.stages else ""),
                "trial": trial or 1, "test_id": None}
        e = self.group.add_entry(source_key, p.get_apparatus(apparatus) if apparatus else None, "", meta)
        self._relabel(e)
        self._rebuild_session_table()
        self._save_group_layout()
        self.sess_table.selectRow(len(self.group.entries) - 1)
        return e

    def remove_session_row(self):
        e = self._selected_entry()
        if e is None:
            return
        if e.state in ("waiting", "running", "paused"):
            QMessageBox.information(self, "Live testing", "Stop this test before removing it.")
            return
        self.group.remove(e)
        keep = {x.source_key for x in self.group.entries}
        for k in [k for k in self.group.sources if k not in keep]:  # unused sources go too
            self.group.stop_sources([k])
            self.group.sources.pop(k, None)
        self._rebuild_session_table()
        self._update_mosaic_tiles()
        self._save_group_layout()

    def _relabel(self, e):
        m = e.meta
        e.label = f"{m.get('animal') or '?'} · {m.get('apparatus') or '?'}"

    def _rebuild_session_table(self):
        t = self.sess_table
        t.setRowCount(0)
        self._row_widgets = {}
        p = self.project
        for e in [x for x in self.group.entries if x.source_key is not None]:
            r = t.rowCount()
            t.insertRow(r)
            w = {}
            src = QComboBox()
            for k, spec in self.group.sources.items():
                src.addItem(spec.label, k)
            src.setCurrentIndex(max(0, src.findData(e.source_key)))
            app = QComboBox()
            app.addItems([a.name for a in p.apparatus] if p else [])
            app.setCurrentText(e.meta.get("apparatus", ""))
            animal = QComboBox()
            animal.setEditable(True)
            animal.setInsertPolicy(QComboBox.NoInsert)
            animal.addItems([a.id for a in p.animals] if p else [])
            animal.setCurrentText(e.meta.get("animal", ""))
            stage = QComboBox()
            stage.setEditable(True)
            stage.addItems(p.stages if p else [])
            stage.setCurrentText(e.meta.get("stage", ""))
            trial = QSpinBox()
            trial.setRange(1, 10000)
            trial.setValue(int(e.meta.get("trial", 1)))
            w.update(source=src, apparatus=app, animal=animal, stage=stage, trial=trial)
            for c, wd in enumerate((src, app, animal, stage, trial)):
                t.setCellWidget(r, c, wd)
            src.currentIndexChanged.connect(lambda *_a, e=e: self._row_edited(e))
            app.currentIndexChanged.connect(lambda *_a, e=e: self._row_edited(e))
            animal.currentTextChanged.connect(lambda *_a, e=e: self._row_edited(e))
            stage.currentTextChanged.connect(lambda *_a, e=e: self._row_edited(e))
            trial.valueChanged.connect(lambda *_a, e=e: self._row_edited(e))
            t.setItem(r, 5, QTableWidgetItem(""))
            t.setItem(r, 6, QTableWidgetItem(""))
            ctl = QWidget()
            h = QHBoxLayout(ctl)
            h.setContentsMargins(2, 0, 2, 0)
            h.setSpacing(2)
            for kind, icon, tip in (("start", QStyle.SP_MediaPlay, "Arm / start now / resume"),
                                    ("pause", QStyle.SP_MediaPause, "Pause"),
                                    ("stop", QStyle.SP_MediaStop, "Stop and save")):
                b = QToolButton()
                b.setIcon(self.style().standardIcon(icon))
                b.setToolTip(tip)
                b.setAutoRaise(True)
                b.clicked.connect(lambda _=False, e=e, k=kind: self.row_action(e, k))
                h.addWidget(b)
                w[kind] = b
            t.setCellWidget(r, 7, ctl)
            w["entry"] = e
            self._row_widgets[e.id] = w
        self._update_row_states()
        self._update_mosaic_tiles()

    def _refresh_row_choices(self):
        if self.mode == "multi" or self.group.entries:
            self._rebuild_session_table()

    def _row_edited(self, e):
        w = self._row_widgets.get(e.id)
        if w is None or e.state in ("waiting", "running", "paused"):
            return
        e.source_key = w["source"].currentData()
        e.meta.update(apparatus=w["apparatus"].currentText(), animal=w["animal"].currentText().strip(),
                      stage=w["stage"].currentText().strip(), trial=w["trial"].value(), test_id=None)
        e.apparatus = self.project.get_apparatus(e.meta["apparatus"]) if self.project else None
        self._relabel(e)
        self._save_group_layout()
        self._update_mosaic_tiles()

    def _selected_entry(self):
        r = self.sess_table.currentRow()
        rows = [x for x in self.group.entries if x.source_key is not None]
        return rows[r] if 0 <= r < len(rows) else None

    def _selected_source(self):
        e = self._selected_entry()
        return e.source_key if e is not None else None

    def _tile_clicked(self, key):
        rows = [x for x in self.group.entries if x.source_key is not None]
        for i, e in enumerate(rows):
            if e.source_key == key:
                self.sess_table.selectRow(i)
                return

    def _update_mosaic_tiles(self):
        used = []
        for e in self.group.entries:
            if e.source_key is not None and e.source_key not in used:
                used.append(e.source_key)
        used += [k for k in self.group.sources if k not in used]
        self.mosaic.set_sources([(k, self.group.sources[k].label) for k in used if k in self.group.sources])

    def _update_row_states(self):
        rows = [x for x in self.group.entries if x.source_key is not None]
        for r, e in enumerate(rows):
            st = e.state
            s = e.session
            if st == "waiting" and getattr(s, "start_phase", ""):
                txt = {"experimenter": "wait hand", "leaving": "hand in", "animal": "wait animal"}[s.start_phase]
            else:
                txt = {"idle": "not armed"}.get(st, st)
            it = self.sess_table.item(r, 5)
            if it is not None and it.text() != txt:
                it.setText(txt)
                it.setForeground(Qt.white if st != "idle" else Qt.black)
                it.setBackground(_qcolor(STATE_STYLE.get(st, STATE_STYLE["idle"])[1]) if st != "idle" else
                                 _qcolor("#e2e8f0"))
            it = self.sess_table.item(r, 6)
            if it is not None:
                it.setText(fmt_time(e.elapsed) if s is not None else "")
            w = self._row_widgets.get(e.id)
            if w is not None:
                active = st in ("waiting", "running", "paused")
                for k in ("source", "apparatus", "animal", "stage", "trial"):
                    w[k].setEnabled(not active)
                w["pause"].setEnabled(st == "running")
                w["stop"].setEnabled(active)
                w["start"].setEnabled(st != "running")

    # ---- cameras of the group
    def _toggle_cameras(self):
        if self.group.runners:
            if any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
                QMessageBox.information(self, "Live testing", "Stop the tests before stopping the cameras.")
                return
            self.stop_cameras()
        else:
            self.start_cameras()

    def start_cameras(self) -> bool:
        if not self.group.sources:
            QMessageBox.information(self, "Live testing", "Add a camera or a video file first (Add source).")
            return False
        self.group.start_sources(speed=self.sim_speed.currentData() or 1.0)
        self.cams_btn.setText("Stop cameras")
        self._update_mosaic_tiles()
        self._mosaic_timer.start()
        self._update_buttons()
        return True

    def stop_cameras(self):
        self.group.stop_sources()
        self.cams_btn.setText("Start cameras")
        self._update_buttons()

    def capture_group_backgrounds(self) -> int:
        n = 0
        for key, r in self.group.runners.items():
            f = r.last_frame
            if f is not None:
                self._group_bgs[key] = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f.copy()
                n += 1
        self.group_lbl.setText(f"Empty-arena background captured for {n} camera{'s' if n != 1 else ''} at "
                               f"{_dt.datetime.now():%H:%M:%S}." if n else "Start the cameras first.")
        return n

    def multi_camera_options(self) -> bool:
        key = self._selected_source() or next(iter(self.group.sources), None)
        if key is None:
            QMessageBox.information(self, "Camera options", "Add a camera or a video file first.")
            return False
        spec = self.group.sources[key]
        if any(e.state in ("waiting", "running", "paused") for e in self.group.entries_for(key)):
            QMessageBox.information(self, "Camera options", "Stop the tests using this camera first.")
            return False
        r = self.group.runners.get(key)
        raw, raw2 = r.raw_frames() if r is not None else (None, None)
        if raw is None and spec.is_file:
            raw = _first_frame(spec.source)
        if raw2 is None and isinstance(spec.second, str):
            raw2 = _first_frame(spec.second)
        dlg = CameraOptionsDialog(raw, spec.view, spec.second, spec.layout, self._merge_choices(spec.source), raw2,
                                  self, title=f"Camera options — {spec.label}")
        if dlg.exec() != QDialog.Accepted:
            return False
        self.apply_source_options(key, dlg.result())
        return True

    def apply_source_options(self, key: str, res: dict):
        spec = self.group.sources[key]
        spec.view = CameraView.from_dict(res.get("view"))
        spec.second = res.get("second")
        spec.layout = res.get("layout", "side")
        settings = {}
        if not spec.view.is_identity:
            settings["view"] = spec.view.to_dict()
        if spec.second is not None:
            settings.update(second=spec.second, layout=spec.layout)
        set_camera_settings(self.project, SourceSpec(spec.source).key, settings)
        self._group_bgs.pop(key, None)
        self.main.mark_dirty()
        self._save_group_layout()
        if key in self.group.runners:
            self.group.stop_sources([key])
            self.group.start_sources(speed=self.sim_speed.currentData() or 1.0, keys=[key])
        self._update_mosaic_tiles()

    def _save_group_layout(self):
        if self.project is None or self._loading:
            return
        keys = list(self.group.sources)
        d = {"sources": [self.group.sources[k].to_dict() for k in keys],
             "sessions": [{"source": keys.index(e.source_key), **{k: e.meta.get(k) for k in
                                                                ("apparatus", "animal", "stage", "trial")}}
                          for e in self.group.entries if e.source_key in self.group.sources]}
        live = self._live_settings()
        if live.get("multi") != d:
            live["multi"] = d
            self.main.mark_dirty()

    def _restore_group_layout(self):
        d = (self.project.settings_extra.get("live") or {}).get("multi") or {}
        keys = []
        for sd in d.get("sources", []):
            spec = SourceSpec.from_dict(sd)
            if spec.is_file and not Path(str(spec.source)).exists():
                keys.append(None)
                continue
            keys.append(self.group.add_source(spec))
        for sd in d.get("sessions", []):
            i = sd.get("source", 0)
            if not 0 <= i < len(keys) or keys[i] is None:
                continue
            meta = {"apparatus": sd.get("apparatus", ""), "animal": sd.get("animal", ""),
                    "stage": sd.get("stage", ""), "trial": sd.get("trial", 1), "test_id": None}
            e = self.group.add_entry(keys[i], self.project.get_apparatus(meta["apparatus"]), "", meta)
            self._relabel(e)

    # ---- arming / control of the group
    def arm_row(self, e) -> bool:
        p = self.project
        if p is None or e.session is not None and e.state != "finished":
            return False
        if p.path is None:
            QMessageBox.information(self, "Live testing", "Save the experiment first.")
            return False
        m = e.meta
        app = p.get_apparatus(m.get("apparatus") or "") if p.apparatus else None
        if app is None or not m.get("animal"):
            self._log(f"{e.label}: choose an apparatus and an animal first.")
            return False
        if e.source_key not in self.group.runners and not self.start_cameras():
            return False
        if p.get_animal(m["animal"]) is None:
            p.ensure_animal(m["animal"])
        test = p.get_test(m["test_id"]) if m.get("test_id") is not None else None
        if test is None:
            pend = next((t for t in p.tests if t.status == "pending" and not t.video and t.animal_id == m["animal"]
                         and t.stage == m.get("stage", "") and t.trial == m.get("trial", 1)), None)
            test = pend or p.add_test("", m["animal"], app.name, stage=m.get("stage", ""), trial=m.get("trial", 1))
            m["new_test"] = pend is None
        if not _confirm_id(self, test):
            if m.get("new_test") and test in p.tests:
                p.tests.remove(test)
            return False
        test.apparatus = app.name
        dur = self.duration.value()
        test.duration_s = 0.0 if abs(dur - p.test_duration_s) < 1e-9 else dur
        m["test_id"] = test.id
        r = self.group.runners.get(e.source_key)
        size, fps = (r.size if r is not None and r.size else (640, 480)), (r.fps if r is not None else 25.0)
        settings = self._detection_settings(test)
        bg = self._group_bgs.get(e.source_key)
        if bg is None and r is not None and r.background is not None:
            bg = r.background
        if bg is not None and bg.shape[:2] != (size[1], size[0]):
            bg = None
        if settings.background == "frame" and bg is None:
            settings.background = "adaptive"
        m["record_path"] = recording_path(p, test, size, fps) if self.record.isChecked() else None
        if self._group_outputs is None:
            self._group_outputs = Outputs(self.serial.currentText().strip() or None)
        s = LiveSession(app, settings, duration_s=dur, start_mode=self._session_mode(),
                        procedures=copy.deepcopy(p.procedures), outputs=self._group_outputs,
                        record_path=m["record_path"], fps=fps, analysis=p.analysis_for(test),
                        devices=self._open_devices(), variables=p.variables,
                        record_overlay=self.record_overlay.isChecked(), lost_warning_s=self.lost_warn.value(),
                        name=f"Test {test.id} · {test.animal_id} · {app.name}",
                        zone_overrides=test.zone_overrides)
        if bg is not None:
            s.set_background(bg)
        self.group.arm(e, s)
        self.main.mark_dirty()
        self._log(f"{e.label}: test {test.id} armed ({self.start_mode.currentText().lower()}).")
        return True

    def arm_all(self, entries=None) -> int:
        """Arm every idle (or finished) row; video files restart so the tests start at their beginning."""
        ents = [e for e in (entries or self.group.entries) if e.source_key is not None
                and e.state in ("idle", "finished")]
        if not ents:
            return 0
        n = sum(1 for e in ents if self.arm_row(e))
        if not n:
            return 0
        for key in {e.source_key for e in ents if e.session is not None}:
            spec = self.group.sources.get(key)
            if spec is not None and spec.is_file:
                self.group.restart_source(key)
        if self.start_mode.currentData() == "scheduled":
            sch = self._new_schedule([e.id for e in ents if e.session is not None])
            self.group.on_schedule = self._on_group_schedule
            self._log(f"Scheduled start {sch.describe()}.")
        self._enable_shortcuts(True)
        self.tabs.setCurrentWidget(self.monitor_tab)
        self._update_row_states()
        self._update_buttons()
        return n

    def _on_group_schedule(self, sch, entries):
        """A clock schedule fired: start waiting tests and re-arm finished rows (daily schedules)."""
        self._save_finished_entries()
        rearm = [e for e in entries if e.state in ("idle", "finished")]
        if rearm:
            self.arm_all(rearm)
        for e in entries:
            if e.state == "waiting":
                e.session.request_start()
        self._log(f"Scheduled start ({sch.at}): {len(entries)} test(s).")

    def row_action(self, e, kind: str):
        st = e.state
        if kind == "start":
            if st in ("idle", "finished"):
                if self.arm_all([e]):
                    pass
            else:
                self.group.start(e)
        elif kind == "pause":
            self.group.pause(e)
        elif kind == "stop":
            self.group.stop(e, save=st in ("running", "paused"))
        self._update_row_states()
        self._update_buttons()

    def start_all(self):
        if not any(e.session is not None and e.state != "finished" for e in self.group.entries):
            self.arm_all()
        self.group.start_all()
        self._update_buttons()

    def group_pause_all(self):
        self.group.pause_all()
        self._log("All tests paused.")
        self._update_row_states()
        self._update_buttons()

    def group_resume_all(self):
        self.group.resume_all()
        self._log("All tests resumed.")
        self._update_row_states()
        self._update_buttons()

    def _stop_all_clicked(self):
        if not any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
            return
        r = QMessageBox.question(self, "Stop all tests", "Stop every test now?\n\nSave keeps the data recorded so "
                                 "far; Discard throws the tests away.",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return
        self.group.stop_all(save=r == QMessageBox.Save)
        self._save_finished_entries()

    def _save_finished_entries(self):
        p = self.project
        for e in self.group.finished_unsaved():
            e.saved = True
            m = e.meta
            s = e.session
            test = p.get_test(m.get("test_id")) if p is not None and m.get("test_id") is not None else None
            for t, msg in s.warnings:
                self._log(f"{e.label} · {fmt_time(t)}  warning: {msg}")
            if e.aborted or test is None or not save_live_test(p, test, s, m.get("record_path")):
                _remove_file(m.get("record_path"))
                if test is not None and m.get("new_test") and test in p.tests:
                    p.tests.remove(test)
                self._log(f"{e.label}: test discarded.")
            else:
                self.main.mark_dirty()
                self.main.save()
                self.last_test_id = test.id
                self._log(f"{e.label}: test {test.id} finished after {fmt_time(s.elapsed)} and saved.")
                self._show_results(test, switch=False)
                m["trial"] = int(m.get("trial", 1)) + 1
                w = self._row_widgets.get(e.id)
                if w is not None:
                    w["trial"].blockSignals(True)
                    w["trial"].setValue(m["trial"])
                    w["trial"].blockSignals(False)
            m["test_id"] = None
            m["record_path"] = None
        if not any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
            if self._group_outputs is not None:
                self._group_outputs.close()
                self._group_outputs = None
            if self.session is None and (self.obs is None or self.obs.state == "finished"):
                self._enable_shortcuts(False)
            self._close_devices()

    # ================================================================== observation only
    def obs_start(self) -> bool:
        if self.obs is not None and self.obs.state == "paused":
            self.obs.resume()
            return True
        if self.obs is not None and self.obs.state == "waiting":
            self.obs.start()
            return True
        if self.obs is not None and self.obs.state != "finished":
            return False
        test, new = self._prepare_test(need_apparatus=False)
        if test is None:
            return False
        if not _confirm_id(self, test):
            if new and test in self.project.tests:
                self.project.tests.remove(test)
            return False
        self.obs_test, self._obs_new = test, new
        self.obs = ObservationSession(self.duration.value(), start_mode="manual",
                                      name=f"Test {test.id} · {test.animal_id} (observation)")
        if self.start_mode.currentData() == "scheduled":
            self._schedule = self._new_schedule()
            self._log(f"Observation of test {test.id} will start {self._schedule.describe()}.")
        else:
            self.obs.start()
            self._log(f"Observation of test {test.id} started — animal {test.animal_id}.")
        self._enable_shortcuts(True)
        self.obs_panel.show_session(self.obs, self.obs.duration_s)
        self._update_buttons()
        return True

    def obs_pause(self) -> bool:
        o = self.obs
        if o is None:
            return False
        ok = o.pause() if o.state == "running" else o.resume()
        self.obs_panel.show_session(o, o.duration_s)
        return ok

    def obs_stop(self, save: bool = True):
        o, test = self.obs, self.obs_test
        if o is None:
            return
        o.finish()
        self.obs, self.obs_test = None, None
        self._schedule = None
        p = self.project
        if save and p is not None and test is not None and save_live_test(p, test, o):
            self.main.mark_dirty()
            self.main.save()
            self.last_test_id = test.id
            self._log(f"Observation of test {test.id} saved: {len(o.events)} events in {fmt_time(o.elapsed)}.")
            self._show_results(test)
        else:
            if test is not None and self._obs_new and p is not None and test in p.tests:
                p.tests.remove(test)
            self._log("Observation discarded.")
        if self.session is None and not any(e.state in ("waiting", "running", "paused")
                                             for e in self.group.entries):
            self._enable_shortcuts(False)
        self.obs_panel.show_session(None)
        self._update_buttons()
        self.on_show()

    # ================================================================== periodic UI work
    def _tick(self):
        now = _dt.datetime.now()
        if self._schedule is not None and self._schedule.due(now):
            self._schedule.fired(now)
            target = self.session if self.session is not None else self.obs
            if target is not None and target.state == "waiting":
                target.request_start()
                self._log("Scheduled start.")
            self._schedule = None
        if self.obs is not None:
            self.obs.tick()
            self.obs_panel.show_session(self.obs, self.obs.duration_s)
            if self.obs.state == "finished":
                self.obs_stop(save=True)
        if self.score_pad is not None and self.mode == "single":
            self.score_pad.set_active(list(self.session.open_states) if self.session is not None else [])
        if self.group.entries:
            self.group.tick(now)
            self._save_finished_entries()
            if self.mode == "multi":
                self._update_row_states()
                self._update_buttons()
        self._refresh_monitor()

    def _refresh_mosaic(self):
        for key, r in list(self.group.runners.items()):
            d = r.take_display()
            if d is not None:
                self.mosaic.set_frame(key, d)
        if self.group.warnings and self.group_lbl.text() != self.group.warnings[-1][1]:
            self.group_lbl.setText(self.group.warnings[-1][1])

    def _monitor_target(self):
        if self.mode == "observe":
            return self.obs, "Observation"
        if self.mode == "multi":
            e = self._selected_entry()
            if e is None or e.session is None:
                e = next((x for x in self.group.entries if x.session is not None), e)
            if e is None:
                return None, "No live test"
            return e.session, (e.session.name if e.session is not None else f"{e.label} (not armed)")
        return self.session, (self.session.name if self.session is not None else "No live test")

    def _refresh_monitor(self, force: bool = False):
        if not force and self.tabs.currentWidget() is not self.monitor_tab:
            return
        s, title = self._monitor_target()
        warns = []
        if self.mode == "multi":
            warns = self.group.all_warnings()
        elif s is not None:
            warns = [f"{fmt_time(t)}  {m}" for t, m in s.warnings]
        self.monitor.refresh(s, title, self.devices, warns)

    # ================================================================== keys: scoring, start / stop, remote
    def _update_keys_label(self):
        p = self.project
        bs = [b for b in (p.behaviours if p else []) if b.key]
        parts = []
        sk, tk = parse_keys(self.start_keys.text()), parse_keys(self.stop_keys.text())
        if sk:
            parts.append("Start: " + ", ".join(f"<b>{k}</b>" for k in sk))
        if tk:
            parts.append("Stop: " + ", ".join(f"<b>{k}</b>" for k in tk))
        if bs:
            parts.append("Scoring: " + ", ".join(f"<b>{b.key}</b> {b.name}" for b in bs))
        else:
            parts.append("Define behaviours with keys on the Experiment page to score live.")
        txt = " · ".join(parts)
        self.keys_lbl.setText(txt)
        self.obs_panel.keys.setText(txt)

    def _enable_shortcuts(self, on: bool):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        for w in self.setup_widgets + [self.rule_editor] + self._proc_buttons:
            w.setEnabled(not on)
        app = QApplication.instance()
        if on and not self._key_filter:
            app.installEventFilter(self)
            self._key_filter = True
        elif not on and self._key_filter:
            app.removeEventFilter(self)
            self._key_filter = False
        if not on or self.project is None:
            return
        # scoring keys go through an event filter (press and release: "hold" behaviours, no auto-repeat)
        taken = {qt_key(b.key).lower() for b in self.project.behaviours if b.key}
        for keys, fn in ((parse_keys(self.start_keys.text()), self.start_key),
                         (parse_keys(self.stop_keys.text()), self.stop_key)):
            for k in keys:
                q = qt_key(k)
                if q.lower() in taken or QKeySequence(q).isEmpty():
                    continue
                taken.add(q.lower())
                sc = QShortcut(QKeySequence(q), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    def start_key(self) -> bool:
        """Start key (keyboard / USB presenter): start the waiting test(s) or resume paused ones."""
        if self.mode == "observe" and self.obs is not None:
            return self.obs_start()
        if self.mode == "multi":
            if any(e.state in ("waiting", "paused") for e in self.group.entries):
                self.group.start_all()
                self._log("Start key: tests started.")
                return True
            return False
        s = self.session
        if s is None:
            return False
        if s.state == "waiting":
            s.request_start()
            self._log("Start key: test started.")
            return True
        if s.state == "paused":
            return self.toggle_pause()
        return False

    def stop_key(self) -> bool:
        """Stop key: stop and save the running test(s)."""
        if self.mode == "observe" and self.obs is not None:
            self.obs_stop(save=True)
            return True
        if self.mode == "multi":
            if any(e.state in ("running", "paused") for e in self.group.entries):
                self.group.stop_all(save=True)
                self._save_finished_entries()
                return True
            return False
        if self.session is not None and self.session.state in ("running", "paused"):
            self.stop_test(save=True)
            return True
        return False

    def _scoring_target(self):
        if self.mode == "observe":
            return self.obs
        if self.mode == "multi":
            e = self._selected_entry()
            if e is None or e.state != "running":
                e = next((x for x in self.group.entries if x.state == "running"), None)
            return e.session if e is not None else None
        return self.session

    def score_key(self, key: str, down: bool = True) -> bool:
        """A scoring key was pressed (down) or released: point events, state toggles, hold behaviours (scored
        while the key is down) and exclusive sets (starting one behaviour stops its partners)."""
        if self.project is None:
            return False
        b = next((b for b in self.project.behaviours if b.key and b.key.lower() == key.lower()), None)
        return b is not None and self._score(b, down)

    def _pad(self, name: str, down: bool):
        b = next((b for b in (self.project.behaviours if self.project else []) if b.name == name), None)
        if b is not None:
            self._score(b, down)
        if self.obs is not None:
            self.obs_panel.show_session(self.obs, self.obs.duration_s)

    def _score(self, b, down: bool) -> bool:
        s = self._scoring_target()
        if s is None or s.state != "running":
            return False
        if not down:
            if b.kind != "hold" or b.name not in s.open_states:
                return False
            ev = s.score(b.name, "state")
            self._log(f"{fmt_time(ev['t_end'])}  {b.name} ends")
            return True
        s.key(b.key)
        if b.kind == "point":
            ev = s.score(b.name, "point")
            self._log(f"{fmt_time(ev['t'])}  {b.name}")
        elif b.name in s.open_states:
            if b.kind == "hold":
                return True
            ev = s.score(b.name, "state")
            self._log(f"{fmt_time(ev['t_end'])}  {b.name} ends")
        else:
            for o in wf.exclusive_partners(self.project.behaviours, b):
                if o.name in s.open_states:
                    end = s.score(o.name, "state")
                    self._log(f"{fmt_time(end['t_end'])}  {o.name} ends")
            ev = s.score(b.name, "state")
            self._log(f"{fmt_time(ev['t'])}  {b.name} starts")
        if s is self.session:
            self.vals["events"].setText(str(len(s.events)))
        return True

    def eventFilter(self, obj, e):
        t = e.type()
        if self._key_filter and t in (QEvent.KeyPress, QEvent.KeyRelease) and isinstance(obj, QWidget) \
                and obj.window() is self.window() and self.project is not None \
                and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            fw = QApplication.focusWidget()
            editing = (isinstance(fw, QLineEdit) and not fw.isReadOnly()) or isinstance(fw, QAbstractSpinBox) \
                or (isinstance(fw, QComboBox) and fw.isEditable())
            text = e.text().strip()
            b = next((b for b in self.project.behaviours if b.key and text and b.key.lower() == text.lower()),
                     None) if not editing else None
            if b is not None:
                if not e.isAutoRepeat():
                    self._score(b, t == QEvent.KeyPress)
                return True
        return super().eventFilter(obj, e)

    # ================================================================== procedures editor
    def _load_procedures(self):
        p = self.project
        cur = self.proc_table.currentRow()
        self.proc_table.setRowCount(0)
        for r in (p.procedures if p else []):
            if isinstance(r, dict) and "trigger" in r:
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
        s = self.session
        armed = s is not None
        has = self.project is not None
        self.arm_btn.setEnabled(has and (not armed or s.state == "waiting"))
        self.arm_btn.setText("Start now" if armed and s.state == "waiting" else "Arm / Start test")
        self.pause_btn.setEnabled(armed and s.state in ("running", "paused"))
        self.pause_btn.setText("Resume" if armed and s.state == "paused" else "Pause")
        self.stop_btn.setEnabled(armed)
        self.next_btn.setEnabled(has and not armed)
        self.preview_btn.setText("Stop preview" if self.grabber is not None else "Start preview")
        self.preview_btn.setEnabled(has and not armed)
        self.capture_btn.setEnabled(not armed)
        states = [e.state for e in self.group.entries if e.source_key is not None]
        self.g_arm.setEnabled(has and any(st in ("idle", "finished") for st in states))
        self.g_start.setEnabled(has and bool(states) and any(st != "running" for st in states))
        self.g_pause.setEnabled("running" in states)
        self.g_resume.setEnabled("paused" in states)
        self.g_stop.setEnabled(any(st in ("waiting", "running", "paused") for st in states))
        self.cams_btn.setText("Stop cameras" if self.group.runners else "Start cameras")
        self.obs_panel.show_session(self.obs, self.obs.duration_s if self.obs else 0.0)


def _scoring_pad():
    try:  # on-screen scoring buttons of the test viewer (mouse / touch screen)
        from .testview import ScoringPad
        return ScoringPad()
    except Exception:  # pragma: no cover
        return None


def _confirm_id(parent, test) -> bool:
    """Animal ID confirmation before a test starts (no-op unless the experiment requires it)."""
    try:
        from ..confirm_id import confirm_animal_id
    except Exception:  # pragma: no cover
        return True
    return bool(confirm_animal_id(parent, test))


def _qcolor(hex_: str):
    from PySide6.QtGui import QColor

    return QColor(hex_)


def _remove_file(path):
    if path and Path(path).exists():
        try:
            os.remove(path)
        except OSError:
            pass


def _first_frame(path) -> np.ndarray | None:
    try:
        with VideoSource(path) as v:
            ok, f = v.read()
            return f if ok else None
    except Exception:
        return None


def _describe_view(view: CameraView, second, layout: str) -> str:
    parts = []
    if second is not None:
        parts.append(f"merged with {second if not isinstance(second, str) else Path(second).name} "
                     f"({'side by side' if layout == 'side' else 'stacked'})")
    if view.rotate:
        parts.append(f"rotated {view.rotate}°")
    if view.flip:
        parts.append({"h": "mirrored", "v": "upside down"}.get(view.flip, "flipped"))
    if view.crop:
        parts.append(f"region {view.crop[2]}×{view.crop[3]}")
    if view.zoom > 1:
        parts.append(f"zoom {view.zoom:g}×")
    return ", ".join(parts) if parts else "Whole image"
