"""Run tests page (ANY-maze's Test page while testing): track animals in real time from cameras (or video files
simulating them), each test in a panel with its own toolbar, title, camera image and Session log / Video / Zones tabs.

Three modes: one test; several tests at once (several cameras and / or several apparatus in one camera image,
panels in a grid with collective start / pause / stop); observation only (TakeNote: no camera, a clock and keys).
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QObject, QRectF, QSize, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout, QWidget)

from ...core import autosave
from ...core import workflow as wf
from ...core.camera import CameraView, SourceReader, SourceSpec, camera_settings, set_camera_settings
from ...core.live import LiveSession, ObservationSession, open_devices
from ...core.livegroup import DEFAULT_START_KEYS, DEFAULT_STOP_KEYS, ClockSchedule, LiveGroup, device_plan
from ...core.procedures import Outputs
from ...core.session import finish_live_test
from ...core.tracking import ArenaTracker, DetectionSettings, draw_tracking
from ...core.video import VIDEO_EXTENSIONS, VideoRecorder, VideoSource, list_cameras
from ..icons import icon
from ..live_widgets import (LAYOUT_LABELS, LAYOUTS, CameraOptionsDialog, MonitorPanel, ObservationPanel,
                            PanelGrid, PanelSettingsDialog, TestPanel, short_time)
from ..procedure_editor import ProcedureEditor
from ..widgets import Worker, cv_to_qpixmap, error_box, fmt_time
from .base import Page

RESOLUTIONS = [("Camera default", None), ("640 × 480", (640, 480)), ("800 × 600", (800, 600)),
               ("1280 × 720", (1280, 720)), ("1920 × 1080", (1920, 1080))]
START_MODES = [("immediate", "Immediately when armed"), ("on_detection", "When the animal is detected"),
               ("experimenter_leaves", "When the experimenter leaves the view"),
               ("manual", "On a start key (keyboard / remote)"), ("scheduled", "At a clock time")]
MODES = [("single", "One test"), ("multi", "Several tests"), ("observe", "Observation only")]
MODE_ACTIONS = [("single", "One test", "video", "Run one test: one camera, one apparatus."),
                ("multi", "Several tests", "grid", "Run several tests at once: several cameras and / or several "
                 "apparatus in one camera image, each in its own panel."),
                ("observe", "Observation only", "observe", "TakeNote: score behaviour by direct observation, "
                 "without a camera.")]
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


class _GrabberSignals(QObject):
    frame_ready = Signal(object, object)
    opened = Signal(int, int, float)
    background_ready = Signal(object)
    ended = Signal()
    failed = Signal(str)


class FrameGrabber(SourceReader):
    """The single test's source: `handler(frame, t)` tracks every frame in the reader thread and returns
    (display frame, info).  frame_ready is only emitted when the UI took the previous display (call ack()), so a
    slow UI drops display frames but never tracking frames.  Video files loop while `loop` is True."""

    def __init__(self, spec: SourceSpec, handler, opener=None):
        super().__init__(spec, opener, name="live-single")
        self.signals = _GrabberSignals()
        self.handler = handler
        self.loop = True
        self._busy = False

    def ack(self):
        self._busy = False

    def keep_looping(self) -> bool:
        return self.loop

    def on_opened(self):
        self.signals.opened.emit(*self.size, self.fps)
        if self.background is not None:
            self.signals.background_ready.emit(self.background)

    def on_frame(self, frame, ts):
        out = self.handler(frame, ts)
        if not self._busy:
            self._busy = True
            self.signals.frame_ready.emit(*out)

    def on_ended(self):
        self.signals.ended.emit()

    def on_failed(self, msg: str):
        self.signals.failed.emit(msg)


TRAIL_LEN = 250  # positions drawn behind the animal on the camera images
VIEW_DEFAULTS = {"layout": "2x2", "fit": True, "trail": True, "zones": True, "labels": False, "hide_report": False,
                 "hide_apparatus": False, "panel": {}}


class LivePage(Page):
    """Run tests: ANY-maze's Test page while tests run — a panel per test (toolbar, title, camera image with the
    apparatus, time slider, Session log / Video / Zones tabs), several panels in a grid, and a collapsible report
    (setup, real-time monitor, procedures, results, log) on the right.  Commands live in the ribbon."""

    title = "Run tests"

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
        self._outputs_seen = self._proc_log_seen = 0
        self._fired_seen = 0
        self._shortcuts: list[QShortcut] = []
        self.last_results: list[dict] = []
        self._hold_finished = False
        self._shown_state = None
        self._bg_mode_value = "frame"  # plain copies of widget state read from the grabber thread
        self._show_trail = True
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
        self._panels: dict[int, TestPanel] = {}
        self._group_outputs: Outputs | None = None
        # observation only
        self.obs: ObservationSession | None = None
        self.obs_test = None
        self._obs_new = False
        self._key_filter = False
        self._undo: list[tuple] = []  # (session, [(kind, event, behaviour), ...]) per scoring key press
        self._touch = None  # touch-screen stimulus window (gui.touchscreen) and the settings it was built with
        self._touch_cfg: dict | None = None
        self._pad_sig = None
        self._btn_state = None
        self.prefs = self._load_view_prefs()

        self._build_actions()

        # ---- test panels: one page per mode
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self._build_single())
        self.left_stack.addWidget(self._build_multi())
        self.obs_panel = ObservationPanel()
        self.obs_panel.start_clicked.connect(self.obs_start)
        self.obs_panel.pause_clicked.connect(self.obs_pause)
        self.obs_panel.stop_clicked.connect(lambda: self.obs_stop(save=True))
        self.obs_panel.undo_clicked.connect(lambda: self.undo_last_event(self.obs))
        if self.obs_panel.pad is not None:
            self.obs_panel.pad.pressed.connect(lambda n: self._pad(n, True))
            self.obs_panel.pad.released.connect(lambda n: self._pad(n, False))
        self.left_stack.addWidget(self.obs_panel)

        # ---- the report: a collapsible side panel (ribbon: View ▸ Hide report)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("LiveReport")
        self.tabs.setUsesScrollButtons(True)
        self.setup_tab = self._build_setup()
        self.monitor = MonitorPanel()
        mon_scroll = QScrollArea()
        mon_scroll.setWidget(self.monitor)
        mon_scroll.setWidgetResizable(True)
        mon_scroll.setFrameShape(QScrollArea.NoFrame)
        mon_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.monitor_tab = mon_scroll
        self.procedures_tab = self._build_procedures()
        self.results_tab = self._build_results()
        self.log_tab = self._build_log()
        self.tabs.addTab(self.setup_tab, "Setup")
        self.tabs.addTab(self.monitor_tab, "Monitor")
        self.tabs.addTab(self.procedures_tab, "Procedures")
        self.tabs.addTab(self.results_tab, "Results")
        self.tabs.addTab(self.log_tab, "Log")
        self.tabs.setFixedWidth(372)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 6)
        lay.setSpacing(8)
        lay.addWidget(self.left_stack, 1)
        lay.addWidget(self.tabs)
        self._set_state_display("idle")
        self._apply_view_prefs()
        self._update_buttons()

        # ≤ 5 Hz: monitor, panels, schedules, saving finished tests; ~12 Hz: camera images of the panels
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start()
        self._mosaic_timer = QTimer(self)
        self._mosaic_timer.setInterval(80)
        self._mosaic_timer.timeout.connect(self._refresh_mosaic)

    # ================================================================== ribbon
    def _act(self, name: str, text: str, tip: str, fn=None, checkable: bool = False) -> QAction:
        a = QAction(icon(name), text, self)
        a.setToolTip(tip)
        a.setStatusTip(tip)
        a.setCheckable(checkable)
        if fn is not None:
            a.triggered.connect(lambda _=False: fn())
        return a

    def _build_actions(self):
        A = self._act
        # All apparatus
        self.start_all_act = A("play", "Start all", "Start the test of every apparatus (they are armed first if "
                               "needed). One test: arm it, or start it now when it is waiting.", self.start_all_clicked)
        self.start_now_act = A("play", "Start all now", "Start every test immediately, without waiting for its "
                               "start condition.", self.start_all_now)
        self.arm_all_act = A("timer", "Arm all", "Arm every test: each one starts when its start condition is met "
                             "(animal detected, start key, clock time…).", self.arm_all_clicked)
        m = QMenu(self)
        m.addAction(self.start_now_act)
        m.addAction(self.arm_all_act)
        self.start_all_act.setMenu(m)
        self.stop_all_act = A("stop", "Stop all", "Stop every running test (you choose to save or discard).",
                              self.stop_all_clicked)
        self.pause_all_act = A("pause", "Pause all", "Pause every running test (the test clocks stop).",
                               self.pause_all_clicked)
        self.resume_all_act = A("resume", "Resume all", "Resume every paused test.", self.resume_all_clicked)
        # Session
        self.add_source_act = A("camera_add", "Add\nsource", "Use a camera, or a video file that "
                                "simulates one.")
        m = QMenu(self)
        m.addAction(icon("camera"), "Camera…", self.add_camera_clicked)
        m.addAction(icon("video_file"), "Video file (simulated camera)…", self.add_file_clicked)
        m.addSeparator()
        m.addAction(icon("refresh"), "Scan for cameras", self.scan_cameras)
        self.add_source_act.setMenu(m)
        self.add_panel_act = A("panel_add", "Add test panel", "Add a test (camera image × apparatus × animal) to "
                               "the session — switches to Several tests.", self.add_panel_clicked)
        self.remove_panel_act = A("delete", "Remove panel", "Remove the selected test panel.",
                                  self.remove_session_row)
        self.capture_bg_act = A("image_capture", "Capture backgrounds", "Take the current camera image(s) as the "
                                "empty-arena background. The arenas must be empty.", self.capture_backgrounds_clicked)
        self.cam_opts_act = A("settings", "Camera options", "Region of the image, digital zoom / pan, rotation, "
                              "flip, merging two cameras.", self.camera_options_clicked)
        self.camera_act = A("video", "Camera image", "Turn the camera image(s) on or off (preview before "
                            "the test).", self.camera_toggled, checkable=True)
        self.next_test_act = A("forward", "Next test", "Go to the next test of the test schedule.", self.next_test)
        # View
        self.layout_act = A("layout_grid", "Apparatus\nlayout", "How the test panels are arranged.")
        m = QMenu(self)
        self._layout_group = QActionGroup(self)
        self.layout_acts: dict[str, QAction] = {}
        for k, lbl in LAYOUT_LABELS.items():
            a = m.addAction(lbl)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=k: self.set_layout(k))
            self._layout_group.addAction(a)
            self.layout_acts[k] = a
        self.layout_act.setMenu(m)
        self.fit_act = A("fit", "Scale\nto fit", "Scale the camera images to fit their panels (off: 1:1 pixels).",
                         lambda: self._set_pref("fit", self.fit_act.isChecked()), checkable=True)
        self.indicators_act = A("highlighter", "Tracking\nindicators", "What is drawn on the camera images.")
        m = QMenu(self)
        self.trail_act = m.addAction("Animal's track (trail)")
        self.zones_act = m.addAction("Highlight the zone the animal is in")
        self.labels_act = m.addAction("Zone names")
        for a, k in ((self.trail_act, "trail"), (self.zones_act, "zones"), (self.labels_act, "labels")):
            a.setCheckable(True)
            a.triggered.connect(lambda on, k=k: self._set_pref(k, on))
        self.indicators_act.setMenu(m)
        self.hide_report_act = A("report_hide", "Hide report", "Hide the report on the right (setup, monitor, "
                                 "procedures, results, log) to give the panels more room.",
                                 lambda: self._set_pref("hide_report", self.hide_report_act.isChecked()),
                                 checkable=True)
        self.hide_app_act = A("eye_off", "Hide apparatus", "Hide the apparatus drawn over the camera images.",
                              lambda: self._set_pref("hide_apparatus", self.hide_app_act.isChecked()),
                              checkable=True)
        self.panel_settings_act = A("panel_settings", "Panel settings", "What each test panel shows.",
                                    self.panel_settings)
        # Mode
        self._mode_group = QActionGroup(self)
        self.mode_acts: dict[str, QAction] = {}
        for key, lbl, ic, tip in MODE_ACTIONS:
            a = A(ic, lbl, tip, lambda k=key: self.set_mode(k), checkable=True)
            self._mode_group.addAction(a)
            self.mode_acts[key] = a
        self.mode_acts["single"].setChecked(True)
        self.mode_btns = self.mode_acts  # older name

    def ribbon_groups(self):
        # (large labels carry their own line break: the ribbon's automatic one is lost when an action changes)
        start = QToolButton()  # a split button, as in ANY-maze: click starts, ▾ offers "now" / "arm"
        start.setObjectName("RibbonLarge")
        start.setDefaultAction(self.start_all_act)
        start.setPopupMode(QToolButton.MenuButtonPopup)
        start.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        start.setIconSize(QSize(32, 32))
        start.setAutoRaise(True)
        start.setFocusPolicy(Qt.NoFocus)
        start.setMinimumWidth(60)
        start.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        return [("All apparatus", [start, (self.stop_all_act, "small"), (self.pause_all_act, "small"),
                                   (self.resume_all_act, "small")]),
                ("Session", [(self.add_source_act, "large"), (self.add_panel_act, "small"),
                             (self.remove_panel_act, "small"), (self.capture_bg_act, "small"),
                             (self.camera_act, "small"), (self.cam_opts_act, "small"), (self.next_test_act, "small")]),
                ("View", [(self.layout_act, "large"), (self.fit_act, "large"), (self.indicators_act, "large"),
                          (self.hide_report_act, "small"), (self.hide_app_act, "small"),
                          (self.panel_settings_act, "small")]),
                ("Mode", [(self.mode_acts[k], "small") for k, *_ in MODE_ACTIONS])]

    def explorer_items(self):
        """Several tests: one explorer entry per test panel."""
        if self.mode != "multi":
            return []
        return [(self._entry_short(e), "video", e.id) for e in self._entries()]

    def show_item(self, key):
        self._panel_clicked(key)

    def _refresh_explorer(self):
        try:
            self.main.refresh_explorer(self)
        except Exception:  # pragma: no cover - the window is still being built
            pass

    # ---- ribbon commands (they act on the test(s) of the current mode)
    def start_all_clicked(self):
        if self.mode == "multi":
            self.start_all()
        elif self.mode == "observe":
            self.obs_start()
        else:
            self._arm_clicked()

    def start_all_now(self):
        if self.mode == "multi":
            self.start_all()
        elif self.mode == "observe":
            self.obs_start()
        else:
            self.start_now()

    def arm_all_clicked(self):
        if self.mode == "multi":
            self.arm_all()
        elif self.mode == "observe":
            self.obs_start()
        else:
            self.arm()
        self._update_buttons()

    def stop_all_clicked(self):
        if self.mode == "multi":
            self._stop_all_clicked()
        elif self.mode == "observe":
            self.obs_stop(save=True)
        else:
            self._stop_clicked()
        self._update_buttons()

    def pause_all_clicked(self):
        if self.mode == "multi":
            self.group_pause_all()
        elif self.mode == "observe":
            if self.obs is not None and self.obs.state == "running":
                self.obs_pause()
        elif self.session is not None and self.session.state == "running":
            self.toggle_pause()
        self._update_buttons()

    def resume_all_clicked(self):
        if self.mode == "multi":
            self.group_resume_all()
        elif self.mode == "observe":
            if self.obs is not None and self.obs.state == "paused":
                self.obs_pause()
        elif self.session is not None and self.session.state == "paused":
            self.toggle_pause()
        self._update_buttons()

    def add_camera_clicked(self):
        i, ok = QInputDialog.getInt(self, "Add camera", "Camera number (0 = first camera):", 0, 0, 63)
        if not ok:
            return
        if self.mode == "multi":
            self.add_source(i)
            return
        restart = self.grabber is not None
        self.stop_preview()
        if self.camera.findData(i) < 0:
            self.camera.addItem(f"Camera {i}", i)
        self.camera.setCurrentIndex(self.camera.findData(i))
        self.cam_radio.setChecked(True)
        self._update_single_title()
        if restart:
            self.start_preview()

    def add_file_clicked(self):
        if self.mode == "multi":
            self._add_file_source()
        else:
            self._choose_sim_file()

    def add_panel_clicked(self):
        if self.mode != "multi" and not self.set_mode("multi"):
            return None
        return self.add_session_row()

    def capture_backgrounds_clicked(self):
        if self.mode == "multi":
            self.capture_group_backgrounds()
        else:
            self.capture_background()

    def camera_options_clicked(self):
        if self.mode == "multi":
            self.multi_camera_options()
        else:
            self.camera_options()

    def camera_toggled(self):
        if self.mode == "multi":
            self._toggle_cameras()
        elif self.mode == "single":
            self._toggle_preview()
        self._update_buttons()

    # ---- view preferences (kept in the user's settings, not in the experiment)
    def _load_view_prefs(self) -> dict:
        d = dict(VIEW_DEFAULTS)
        try:
            raw = self.main.settings.value("live/view", "")
            if raw:
                d.update(json.loads(raw))
        except Exception:
            pass
        return d

    def _save_view_prefs(self):
        try:
            self.main.settings.setValue("live/view", json.dumps(self.prefs))
        except Exception:
            pass

    def _set_pref(self, key: str, value):
        self.prefs[key] = value
        self._save_view_prefs()
        self._apply_view_prefs()

    def set_layout(self, key: str):
        self._set_pref("layout", key if key in LAYOUTS else "2x2")

    def panel_settings(self) -> bool:
        dlg = PanelSettingsDialog(self.prefs.get("panel") or {}, self)
        if dlg.exec() != QDialog.Accepted:
            return False
        self._set_pref("panel", dlg.result_settings())
        return True

    def _all_panels(self) -> list[TestPanel]:
        return [self.single_panel] + list(self._panels.values())

    def _apply_view_prefs(self):
        d = self.prefs
        for a, k in ((self.fit_act, "fit"), (self.trail_act, "trail"), (self.zones_act, "zones"),
                     (self.labels_act, "labels"), (self.hide_report_act, "hide_report"),
                     (self.hide_app_act, "hide_apparatus")):
            a.setChecked(bool(d.get(k)))
        lay = d.get("layout") if d.get("layout") in LAYOUTS else "2x2"
        self.layout_acts[lay].setChecked(True)
        self.mosaic.set_layout_key(lay)
        self._show_trail = bool(d.get("trail", True))
        self.group.trail_len = TRAIL_LEN if self._show_trail else 0
        self.tabs.setVisible(not d.get("hide_report"))
        for p in self._all_panels():
            self._apply_prefs_to_panel(p)

    def _apply_prefs_to_panel(self, p: TestPanel):
        d = self.prefs
        p.view.set_scale_to_fit(bool(d.get("fit", True)))
        p.view.set_indicators(zones=bool(d.get("zones", True)), labels=bool(d.get("labels", False)))
        p.view.set_apparatus_visible(not d.get("hide_apparatus"))
        p.apply_settings(d.get("panel") or {})

    # ================================================================== UI construction
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
        self.score_pad = _scoring_pad()
        if self.score_pad is not None:
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
        if self.score_pad is not None:
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
        v.addWidget(self.mosaic, 1)
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
        for w in (self.start_keys, self.stop_keys):
            w.editingFinished.connect(self._save_live_settings)
        self.sched_time.timeChanged.connect(self._save_live_settings)
        self.sched_daily.toggled.connect(self._save_live_settings)
        f.addRow("Test duration", self.duration)
        f.addRow("The test starts", self.start_mode)
        f.addRow("Start time", trow)
        f.addRow("Start keys", self.start_keys)
        f.addRow("Stop keys", self.stop_keys)
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

    # ================================================================== project / visibility
    def set_project(self, project):
        self._close_touch()
        self.stop_test(save=False, quiet=True)
        self.stop_preview()
        self.group.close()
        self.group = LiveGroup()
        self.group.trail_len = TRAIL_LEN if self._show_trail else 0
        self._panels = {}  # the panels of the previous experiment go with its session
        self._group_bgs = {}
        self.obs_stop(save=False)
        self._close_devices()
        self._background = None
        self._file_background = None
        self._undo = []
        self.bg_status.setText("No background captured")
        self.results.setRowCount(0)
        self.results_title.setText("The results of the last test appear here.")
        self.log.clear()
        self.single_panel.log.clear()
        self.single_panel.view.set_apparatus(None)
        self.monitor.clear()
        if project is not None:
            self.duration.setValue(project.test_duration_s)
            self.start_mode.setCurrentIndex(1 if project.start_mode == "on_detection" else 0)
            self._load_live_settings()
            self._restore_group_layout()
            self._recover_interrupted(project)
        self._rebuild_session_table()
        self.on_show()

    def _recover_interrupted(self, project):
        """Live tests interrupted by a crash leave an autosave side file: store what they recorded."""
        try:
            rec = autosave.recover(project)
        except Exception as e:  # never block opening the experiment (the side files stay for the next time)
            self._log(f"Could not recover interrupted live tests: {e}")
            return
        if not rec:
            return
        ids = ", ".join(str(t.id) for t in rec)
        self._log(f"Recovered {len(rec)} live test(s) interrupted by a crash: {ids} (see the test notes).")
        try:
            self.main.status(f"Recovered interrupted live test(s) {ids}.")
        except Exception:  # pragma: no cover
            pass

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
        self._load_single_view()
        self._refresh_row_choices()
        self.proc_editor.set_project(p)
        sig = [(b.name, b.key, b.kind, b.group, b.color) for b in p.behaviours]
        if sig != self._pad_sig:
            self._pad_sig = sig
            for pad in (self.obs_panel.pad, self.score_pad):
                if pad is not None:
                    pad.set_behaviours(p.behaviours)
        if self.score_pad is not None:
            self.score_pad.setVisible(bool(p.behaviours))
        self._update_keys_label()
        self._update_single_title()
        self._update_buttons()

    def on_hide(self):
        if self.any_active():
            self.main.status("Tests are still running in the background — return to “Run tests” to follow or "
                             "stop them.", 10000)
            return
        self.stop_preview()
        self.stop_cameras()

    def _touch_window(self):
        """The touch-screen stimulus window when enabled for this experiment (shown full screen on its display)."""
        cfg = (self.project.settings_extra.get("touchscreen") or {}) if self.project is not None else {}
        if not cfg.get("enabled") or cfg != self._touch_cfg:  # off, or its settings changed: rebuilt below
            self._close_touch()
        if not cfg.get("enabled"):
            return None
        if self._touch is None:
            from ..touchscreen import TouchStimulusWindow
            self._touch = TouchStimulusWindow.from_project(self.project)
            self._touch_cfg = copy.deepcopy(cfg)
            self._touch.show_on_screen()
        return self._touch

    def _close_touch(self):
        if self._touch is not None:
            self._touch.close()
            self._touch = None
            self._touch_cfg = None

    def stop_and_save_all(self):
        """Stop every test and save what it recorded (the window closes or another experiment opens)."""
        if self.session is not None:
            self.stop_test(save=True, quiet=True)
        if self.obs is not None:
            self.obs_stop(save=True)
        self.group.stop_all(save=True)
        self._save_finished_entries()

    def shutdown(self):
        self._close_touch()
        self.stop_and_save_all()
        self.group.close()
        self._save_finished_entries()
        self.stop_preview()
        self._close_devices()
        self._enable_shortcuts(False)  # the application-wide key filter goes with the page
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
            self.mode_acts[mode].setChecked(True)
            return True
        if self.any_active():
            QMessageBox.information(self, "Run tests", "Stop the running test(s) before changing the mode.")
            self.mode_acts[self.mode].setChecked(True)
            return False
        if self.mode == "single":
            self.stop_preview()
        elif self.mode == "multi":
            self.stop_cameras()
        self.mode = mode
        self.mode_acts[mode].setChecked(True)
        self.left_stack.setCurrentIndex([m for m, _ in MODES].index(mode))
        self.panels_box.setVisible(mode == "multi")
        self.src_box.setVisible(mode == "single")
        self.test_box.setVisible(mode != "multi")
        self.det_box.setVisible(mode != "observe")
        if mode == "multi":
            self._rebuild_session_table()
            self._mosaic_timer.start()
        else:
            self._mosaic_timer.stop()
        self._update_keys_label()
        self._update_single_title()
        self._update_buttons()
        self._refresh_monitor(force=True)
        self._refresh_explorer()
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
            self.pause_off.setChecked(bool(d.get("pause_outputs_off", True)))
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
        if self.pause_off.isChecked() != bool(d.get("pause_outputs_off", True)):
            new["pause_outputs_off"] = self.pause_off.isChecked()
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
        if self.grabber is None and self._second is None:  # show the first image until the preview starts
            f = _first_frame(path)
            if f is not None:
                try:
                    self.view.set_frame(self._view.apply(f))
                except Exception:
                    pass

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
        self._update_single_title()

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
            QMessageBox.information(self, "Run tests", "Choose a video file to simulate a camera first.")
            return False
        size = self.resolution.currentData() if not self.simulating else None
        fps = self.cam_fps.value() if not self.simulating else None
        self._source_is_file = self.simulating
        spec = SourceSpec(src, self._second, self._merge_layout, self._view, size, fps or None)
        g = FrameGrabber(spec, self.process_frame, opener=VideoSource)
        g.speed = (self.sim_speed.currentData() or 1.0) if self.simulating else 1.0
        sig = g.signals
        sig.frame_ready.connect(self._on_frame)
        sig.opened.connect(self._on_opened)
        sig.background_ready.connect(self._on_file_background)
        sig.ended.connect(self._on_source_ended)
        sig.failed.connect(self._on_grab_failed)
        self.grabber = g
        g.start()
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
        self._update_buttons()
        if self.session is None:
            self._set_state_display("idle")

    def _toggle_preview(self):
        if self.grabber is None:
            self.start_preview()
        else:
            if self.session is not None:
                QMessageBox.information(self, "Run tests", "Stop the test before stopping the camera.")
                return
            self.stop_preview()

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
        error_box(self, "Run tests", msg)

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
        if self.session is not None:  # the armed test keeps its apparatus (with its moved zones)
            return
        with self._lock:
            self._apparatus = app
            self._preview_tracker = None
        self.single_panel.view.set_apparatus(app)
        self._update_single_title()

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
                trail = s.trail(TRAIL_LEN) if self._show_trail else None
                elapsed = s.elapsed if state != "waiting" else 0.0
                info = {"session": s, "state": state, "elapsed": elapsed, "duration": s.duration_s,
                        "events": len(s.events), "fired": list(s.engine.fired),
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
        disp = draw_tracking(frame, [d] if d is not None else [], trail)
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

    def feed_frame(self, frame: np.ndarray, ts: float):
        """Process and display a frame synchronously (used when driving the page without a grabber)."""
        self._on_frame(*self.process_frame(frame, ts))

    def _on_frame(self, disp, info):
        if self.grabber is not None:
            self.grabber.ack()
        self.view.set_frame(disp)
        self.view.set_active_zones(info["zones"])
        state = info["state"]
        if self.session is not None or not self._hold_finished:
            self._set_state_display(state)
        if state != self._btn_state:
            self._btn_state = state
            self._update_buttons()
        self.vals["zone"].setText(", ".join(info["zones"]) if info["zones"] else
                                  ("—" if info["detected"] else "not detected"))
        # a preview frame, or a stale one of the previous test, may arrive just after arming
        if info.get("session") is not None and info["session"] is self.session:
            el = info["elapsed"]
            dur = info["duration"]
            self.single_panel.set_state(state, el, dur)
            self._update_single_title(el)
            self.vals["distance"].setText(f"{info['distance']:.1f} {info['unit']}")
            self.vals["events"].setText(str(info["events"]))
            fired = info["fired"]
            for t, trig, act, payload in fired[self._fired_seen:]:
                self._log(f"{fmt_time(t)}  rule: {trig} → {act} {payload}".rstrip())
            self._fired_seen = len(fired)
            for line in info["outputs"][self._outputs_seen:]:
                self._log(f"  {line}")
            self._outputs_seen = len(info["outputs"])
            for t, m in info["proc_log"][self._proc_log_seen:]:
                self._log(f"  {fmt_time(t)} {m}")
            self._proc_log_seen = len(info["proc_log"])
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
        self._update_single_title()

    def _update_single_title(self, elapsed: float | None = None):
        """Title of the single-test / observation panel ("Open field: Animal C1, Day 1 trial 2 - 0:38") and the
        video source shown next to it."""
        if not hasattr(self, "apparatus") or not hasattr(self, "obs_panel"):
            return
        s = self.session if self.mode == "single" else self.obs
        t = self.test if self.mode == "single" else self.obs_test
        if t is not None:
            animal, stage, trial, app = t.animal_id, t.stage, t.trial, t.apparatus
        else:
            animal, stage = self.animal.currentText().strip(), self.stage.currentText().strip()
            trial, app = self.trial.value(), self.apparatus.currentText()
        what = f"Animal {animal or '?'}, {(stage + ' ') if stage else ''}trial {trial}"
        if s is not None and elapsed is None:
            elapsed = s.elapsed if s.state != "waiting" else 0.0
        clock = f" - {short_time(elapsed)}" if s is not None else ""
        if self.mode == "observe":
            self.obs_panel.set_title(f"Observation: {what}{clock}")
            return
        title = f"{app}: {what}{clock}" if app else f"{what}{clock}"
        if self.single_panel.title.text() != title:
            self.single_panel.set_title(title)
        if self.simulating:
            path = self.sim_path.text().strip()
            src, tip = (path or "No video file chosen"), path
        else:
            src = self.camera.currentText() or "Camera"
            if self.resolution.currentData():
                src += f" · {self.resolution.currentText()}"
            tip = src
        desc = self.view_lbl.text()
        if desc and desc != "Whole image":
            tip += f" ({desc})"
        self.single_panel.set_source(src, tip)

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
            QMessageBox.information(self, "Run tests", "Save the experiment first.")
            return None, False
        if need_apparatus and not p.apparatus:
            QMessageBox.information(self, "Run tests", "Draw an apparatus first (Apparatus page).")
            return None, False
        aid = self.animal.currentText().strip()
        if not aid:
            QMessageBox.information(self, "Run tests", "Choose or type the animal ID.")
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

    def _autosave_args(self, test) -> dict:
        """Crash-recovery side file of a live test (see core.autosave)."""
        p = self.project
        if p is None or p.path is None:
            return {}
        try:
            path = autosave.path_for(p, test)
        except Exception:
            return {}
        return {"autosave_path": path, "autosave_meta": {
            "test_id": test.id, "animal": test.animal_id, "apparatus": test.apparatus, "stage": test.stage,
            "trial": test.trial}}

    def _make_session(self, test, app, bg, size, fps: float, outputs, devices, name: str, entry=None,
                      on_stimulus=None) -> LiveSession:
        """A live session of `test` in `app` with the page's settings: detection (an adaptive background without
        an empty-arena image `bg`), duration and start, procedures, recording, warnings, pausing, crash recovery."""
        p = self.project
        settings = self._detection_settings(test)
        if settings.background == "frame" and bg is None:
            settings.background = "adaptive"
            self._log("No empty-arena background: using an adaptive background.", entry)
        s = LiveSession(app, settings, duration_s=self.duration.value(), start_mode=self._session_mode(),
                        procedures=copy.deepcopy(p.procedures), outputs=outputs,
                        record_path=recording_path(p, test, size, fps) if self.record.isChecked() else None,
                        fps=fps, analysis=p.analysis_for(test), devices=devices, variables=p.variables,
                        record_overlay=self.record_overlay.isChecked(), lost_warning_s=self.lost_warn.value(),
                        name=name, zone_overrides=test.zone_overrides, on_stimulus=on_stimulus,
                        outputs_off_on_pause=self.pause_off.isChecked(), **self._autosave_args(test))
        if bg is not None:
            s.set_background(bg)
        return s

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
        outputs = Outputs(self.serial.currentText().strip() or None)
        self._outputs = outputs
        for line in outputs.log:
            self._log(line)
        dur = self.duration.value()
        touch = self._touch_window()
        session = self._make_session(test, p.get_apparatus(test.apparatus), self._current_background(),
                                     self._frame_size or (640, 480), self._fps, outputs, self._open_devices(),
                                     f"Test {test.id} · {test.animal_id}",
                                     on_stimulus=touch.handle if touch is not None else None)
        self._record_path = session.record_path
        if touch is not None:
            touch.clear()
            touch.connect_session(session)  # session lock first, like the camera thread (no deadlock)
        with self._lock:
            self._apparatus = session.apparatus
            self._fired_seen = 0
            self._outputs_seen, self._proc_log_seen = len(outputs.log), 0
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
        self.single_panel.log.clear()
        self.single_panel.view.set_apparatus(session.apparatus)
        self._log(f"Test {test.id} armed — animal {test.animal_id}, {'until stopped' if not dur else f'{dur:g} s'}, "
                  f"start {when}")
        self._set_state_display("waiting")
        self._update_single_title(0.0)
        self._update_buttons()
        return True

    def start_now(self) -> bool:
        """▶ ▾ Start now: arm the test if needed and start it without waiting for its start condition."""
        if self.session is None and not self.arm():
            return False
        s = self.session
        if s is not None and s.state == "waiting":
            s.request_start()
            self._log("Start requested.")
        self._update_buttons()
        return s is not None

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
        test = self.test
        self.test = None
        el = s.elapsed
        if not self._store(test, s, self._record_path, save, self._new_test):
            self._log("Test discarded.")
            self._set_state_display("preview" if self.grabber else "idle")
            self._update_buttons()
            self._close_devices()
            self.on_show()
            return
        self._log(f"Test {test.id} finished after {fmt_time(el)} and saved.")
        self._set_state_display("finished")
        self._hold_finished = True
        self._show_results(test)
        self._update_buttons()
        self._close_devices()
        self.on_show()
        self.single_panel.set_title(f"{self.single_panel.title.text()} - {short_time(el)}")
        if not quiet:
            self.main.status(f"Test {test.id} saved. Press “Next test” to continue.")

    def _store(self, test, session, record_path: str | None, save: bool, new_test: bool, entry=None) -> bool:
        """A test is over (any mode): its warnings go to the log, then it is stored and the experiment saved, or
        it is discarded (see session.finish_live_test).  Returns True when stored."""
        prefix = f"{entry.label} · " if entry is not None else ""
        for t, msg in session.warnings:
            self._log(f"{prefix}{fmt_time(t)}  warning: {msg}", entry)
        if not finish_live_test(self.project, test, session, record_path, save, new_test):
            return False
        self.main.mark_dirty()
        if self.main.save():
            session.remove_autosave()
        self.last_test_id = test.id
        return True

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
        if self.group.runners:
            self.start_cameras()
        self._sync_panels()
        self.main.status(f"Added {spec.label}. Add a test panel for every apparatus it shows.")
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
        """Add a test panel (source × apparatus × animal / stage / trial) to the session."""
        p = self.project
        if p is None:
            return None
        if not self.group.sources:
            QMessageBox.information(self, "Run tests", "Add a camera or a video file first (Add camera / video "
                                    "source).")
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
        self.sess_table.selectRow(len(self._entries()) - 1)
        return e

    def remove_session_row(self):
        e = self._selected_entry()
        if e is None:
            return
        if e.state in ("waiting", "running", "paused"):
            QMessageBox.information(self, "Run tests", "Stop this test before removing it.")
            return
        self.group.remove(e)
        keep = {x.source_key for x in self.group.entries}
        for k in [k for k in self.group.sources if k not in keep]:  # unused sources go too
            self.group.stop_sources([k])
            self.group.sources.pop(k, None)
        self._rebuild_session_table()
        self._save_group_layout()
        self._update_buttons()

    def _relabel(self, e):
        m = e.meta
        e.label = f"{m.get('animal') or '?'} · {m.get('apparatus') or '?'}"

    def _entries(self) -> list:
        return [x for x in self.group.entries if x.source_key is not None]

    @staticmethod
    def _entry_short(e) -> str:
        return f"{e.meta.get('apparatus') or '?'}: {e.meta.get('animal') or '?'}"

    def _rebuild_session_table(self):
        t = self.sess_table
        cur = self._selected_entry()
        t.blockSignals(True)
        t.setRowCount(0)
        rows = self._entries()
        for e in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c in range(7):
                t.setItem(r, c, QTableWidgetItem(""))
            self._fill_row(r, e)
        if rows:
            t.setCurrentCell(rows.index(cur) if cur in rows else 0, 0)
        t.blockSignals(False)
        self._load_row_editor()
        self._sync_panels()
        self._update_row_states()

    def _fill_row(self, r: int, e):
        spec = self.group.sources.get(e.source_key)
        m = e.meta
        for c, txt in enumerate((spec.label if spec is not None else "?", m.get("apparatus", ""),
                                 m.get("animal", ""), m.get("stage", ""), str(m.get("trial", 1)))):
            it = self.sess_table.item(r, c)
            if it is not None and it.text() != txt:
                it.setText(txt)
                it.setToolTip(txt)

    def _load_row_editor(self):
        """Show the selected panel's camera / apparatus / animal / stage / trial in the editor below the table."""
        e = self._selected_entry()
        p = self.project
        eds = (self.row_source, self.row_apparatus, self.row_animal, self.row_stage, self.row_trial, self.row_device)
        for w in eds:
            w.blockSignals(True)
        try:
            self.row_source.clear()
            for k, spec in self.group.sources.items():
                self.row_source.addItem(spec.label, k)
            self.row_apparatus.clear()
            self.row_apparatus.addItems([a.name for a in p.apparatus] if p else [])
            self.row_animal.clear()
            self.row_animal.addItems([a.id for a in p.animals] if p else [])
            self.row_stage.clear()
            self.row_stage.addItems(p.stages if p else [])
            self.row_device.clear()
            self.row_device.addItem("Automatic (only test running)", "")
            for c in (getattr(p, "io_devices", None) or []) if p else []:
                if c.get("enabled", True) and c.get("type", "virtual") != "audio" and c.get("name"):
                    self.row_device.addItem(f"{c['name']} ({c.get('type', 'virtual')})", str(c["name"]))
            self.row_device.addItem("None (simulated outputs)", "-")
            if e is not None:
                dv = e.meta.get("device", "") or ""
                i = self.row_device.findData(dv)
                if i < 0:  # a device that is no longer configured: kept, shown as missing
                    self.row_device.addItem(f"{dv} (not configured)", dv)
                    i = self.row_device.count() - 1
                self.row_device.setCurrentIndex(i)
                self.row_source.setCurrentIndex(max(0, self.row_source.findData(e.source_key)))
                self.row_apparatus.setCurrentText(e.meta.get("apparatus", ""))
                self.row_animal.setCurrentText(e.meta.get("animal", ""))
                self.row_stage.setCurrentText(e.meta.get("stage", ""))
                self.row_trial.setValue(int(e.meta.get("trial", 1)))
        finally:
            for w in eds:
                w.blockSignals(False)
        self.row_editor.setEnabled(e is not None and e.state not in ("waiting", "running", "paused"))

    def _refresh_row_choices(self):
        if self.mode == "multi" or self.group.entries:
            self._rebuild_session_table()

    def _row_editor_changed(self, *_):
        e = self._selected_entry()
        if e is None or e.state in ("waiting", "running", "paused"):
            return
        e.source_key = self.row_source.currentData() or e.source_key
        e.meta.update(apparatus=self.row_apparatus.currentText(), animal=self.row_animal.currentText().strip(),
                      stage=self.row_stage.currentText().strip(), trial=self.row_trial.value(), test_id=None,
                      device=self.row_device.currentData() or "")
        e.apparatus = self.project.get_apparatus(e.meta["apparatus"]) if self.project else None
        self._relabel(e)
        self._fill_row(self._entries().index(e), e)
        self._save_group_layout()
        self._sync_panels()

    def _selected_entry(self):
        r = self.sess_table.currentRow()
        rows = self._entries()
        return rows[r] if 0 <= r < len(rows) else None

    def _selected_source(self):
        e = self._selected_entry()
        return e.source_key if e is not None else None

    def _selection_changed(self):
        e = self._selected_entry()
        self._load_row_editor()
        if e is not None:
            self.mosaic.set_current(e.id)
            try:
                self.main.select_explorer(self, e.id)
            except Exception:  # pragma: no cover
                pass
        self._refresh_monitor(force=True)
        self._update_buttons()

    def _panel_clicked(self, entry_id: int):
        for i, e in enumerate(self._entries()):
            if e.id == entry_id:
                if self.sess_table.currentRow() != i:
                    self.sess_table.selectRow(i)
                else:
                    self._selection_changed()
                return

    # ---- the test panels of the session
    def _new_panel(self, e) -> TestPanel:
        p = TestPanel(single=False)
        eid = e.id
        ent = lambda: self.group.entry(eid)  # noqa: E731 - the entry object may be replaced by a new session
        p.clicked.connect(lambda: self._panel_clicked(eid))
        p.start_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "start"))
        p.start_now.connect(lambda: ent() is not None and self._start_entry_now(ent()))
        p.arm_clicked.connect(lambda: ent() is not None and ent().state in ("idle", "finished")
                              and self.arm_all([ent()]))
        p.pause_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "pause"))
        p.stop_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "stop"))
        p.undo_clicked.connect(lambda: ent() is not None and self.undo_last_event(ent().session))
        p.menu.addAction(icon("settings"), "Camera options…",
                         lambda: (self._panel_clicked(eid), self.multi_camera_options()))
        p.menu.addAction(icon("delete"), "Remove this test panel",
                         lambda: (self._panel_clicked(eid), self.remove_session_row()))
        p.menu.addSeparator()
        p.menu.addAction(self.panel_settings_act)
        p.view.set_message("Turn on the camera image (Show camera image) or press ▶ to start the test.")
        self._apply_prefs_to_panel(p)
        return p

    def _focus_rect(self, e) -> QRectF | None:
        """Several apparatus in one camera image: each panel shows its own apparatus."""
        if e.apparatus is None or len(self.group.entries_for(e.source_key)) < 2:
            return None
        try:
            x0, y0, x1, y1 = e.apparatus.arena_or_bounds().bounds()
        except Exception:
            return None
        m = 0.05 * max(x1 - x0, y1 - y0) + 4
        return QRectF(x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)

    def _sync_panels(self):
        """One panel per test of the session, in the order of the table."""
        panels = {}
        for e in self._entries():
            p = self._panels.get(e.id) or self._new_panel(e)
            panels[e.id] = p
            spec = self.group.sources.get(e.source_key)
            if spec is not None:
                src = str(spec.source) if spec.is_file else spec.label
                p.set_source(src, src)
            p.view.set_apparatus(e.session.apparatus if e.session is not None and
                                 e.session.apparatus is not None else e.apparatus)
            p.view.set_focus(self._focus_rect(e))
        self._panels = panels
        self.mosaic.set_panels(panels)
        cur = self._selected_entry()
        if cur is not None:
            self.mosaic.set_current(cur.id)
        self._update_panels()
        self._refresh_explorer()

    def _panel_title(self, e) -> str:
        m = e.meta
        stage = m.get("stage") or ""
        title = (f"{m.get('apparatus') or '?'}: Animal {m.get('animal') or '?'}, {(stage + ' ') if stage else ''}"
                 f"trial {m.get('trial', 1)}")
        if e.session is not None:
            title += f" - {short_time(e.elapsed if e.state != 'waiting' else 0.0)}"
        return title

    def _update_panels(self):
        """State, time, statistics and zones of every panel (≤ 5 Hz)."""
        for e in self._entries():
            p = self._panels.get(e.id)
            if p is None:
                continue
            s = e.session
            st = e.state
            title = self._panel_title(e)
            if p.title.text() != title:
                p.set_title(title)
            if s is None:
                p.set_state("idle")
                p.reset_values()
            else:
                p.set_state(st, e.elapsed if st != "waiting" else 0.0, s.duration_s or 0.0)
                stats = s.stats
                if stats is not None and st in ("running", "paused", "finished"):
                    with s.lock:
                        zones = stats.current_zones() if stats.detected else []
                        rows = stats.rows() if p.stack.currentIndex() == 2 else None
                        dist, unit = stats.distance, stats.unit
                    inner = [z for z in zones if z != "Arena"] or zones
                    p.vals["zone"].setText(", ".join(inner) if inner else ("—" if stats.detected else
                                                                           "not detected"))
                    p.vals["distance"].setText(f"{dist:.1f} {unit}")
                    p.view.set_active_zones(zones if st != "finished" else [])
                    if rows is not None:
                        p.set_zone_rows(rows, zones)
                p.vals["events"].setText(str(len(s.events)))
            active = st in ("waiting", "running", "paused")
            p.start_btn.setEnabled(st != "running" and self.project is not None)
            p.start_btn.setText("Start now" if st == "waiting" else "Resume" if st == "paused" else "Arm / Start test")
            p.start_btn.setToolTip(p.start_btn.text())
            p.pause_btn.setEnabled(st in ("running", "paused"))
            p.pause_btn.setText("Resume" if st == "paused" else "Pause")
            p.pause_btn.setIcon(icon("resume" if st == "paused" else "pause"))
            p.pause_btn.setToolTip(p.pause_btn.text())
            p.stop_btn.setEnabled(active)
            p.undo_btn.setEnabled(self._can_undo(s))
            p.set_recording(st in ("running", "paused") and bool(e.meta.get("record_path")), e.meta.get("record_path"))

    def _update_row_states(self):
        rows = self._entries()
        for r, e in enumerate(rows):
            st = e.state
            s = e.session
            if st == "waiting" and s.start_phase:
                txt = {"experimenter": "wait hand", "leaving": "hand in", "animal": "wait animal"}[s.start_phase]
            else:
                txt = {"idle": "not armed"}.get(st, st)
            it = self.sess_table.item(r, 5)
            if it is not None and it.text() != txt:
                it.setText(txt)
            it = self.sess_table.item(r, 6)
            if it is not None:
                it.setText(fmt_time(e.elapsed) if s is not None else "")
        sel = self._selected_entry()
        self.row_editor.setEnabled(sel is not None and sel.state not in ("waiting", "running", "paused"))
        self._update_panels()

    # ---- cameras of the group
    def _toggle_cameras(self):
        if self.group.runners:
            if any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
                QMessageBox.information(self, "Run tests", "Stop the tests before stopping the cameras.")
                return
            self.stop_cameras()
        else:
            self.start_cameras()

    def start_cameras(self) -> bool:
        if not self.group.sources:
            QMessageBox.information(self, "Run tests", "Add a camera or a video file first (Add camera / video "
                                    "source).")
            return False
        self.group.start_sources(speed=self.sim_speed.currentData() or 1.0)
        self._mosaic_timer.start()
        self._update_buttons()
        return True

    def stop_cameras(self):
        self.group.stop_sources()
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
        self._sync_panels()

    def _save_group_layout(self):
        if self.project is None or self._loading:
            return
        keys = list(self.group.sources)
        d = {"sources": [self.group.sources[k].to_dict() for k in keys],
             "sessions": [{"source": keys.index(e.source_key), **{k: e.meta.get(k) for k in
                                                                ("apparatus", "animal", "stage", "trial")},
                           **({"device": e.meta["device"]} if e.meta.get("device") else {})}
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
                    "stage": sd.get("stage", ""), "trial": sd.get("trial", 1), "test_id": None,
                    "device": sd.get("device", "") or ""}
            e = self.group.add_entry(keys[i], self.project.get_apparatus(meta["apparatus"]), "", meta)
            self._relabel(e)

    # ---- arming / control of the group
    def arm_row(self, e) -> bool:
        p = self.project
        if p is None or e.session is not None and e.state != "finished":
            return False
        if p.path is None:
            QMessageBox.information(self, "Run tests", "Save the experiment first.")
            return False
        m = e.meta
        app = p.get_apparatus(m.get("apparatus") or "") if p.apparatus else None
        if app is None or not m.get("animal"):
            self._log(f"{e.label}: choose an apparatus and an animal first.", e)
            return False
        plan, msg = self._io_plan(e)
        if plan is None:
            self._log(f"{e.label}: not armed. {msg}", e)
            QMessageBox.warning(self, "Run tests", f"{e.label}: {msg}")
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
        bg = self._group_bgs.get(e.source_key)
        if bg is None and r is not None and r.background is not None:
            bg = r.background
        if bg is not None and bg.shape[:2] != (size[1], size[0]):
            bg = None
        if self._group_outputs is None:
            self._group_outputs = Outputs(self.serial.currentText().strip() or None)
        try:
            devices = self._session_devices(plan)
        except Exception as ex:
            self._log(f"{e.label}: not armed. I/O devices: {ex}", e)
            if m.get("new_test") and test in p.tests:
                p.tests.remove(test)
            m["test_id"] = None
            return False
        panel = self._panels.get(e.id)
        if panel is not None:
            panel.log.clear()
        s = self._make_session(test, app, bg, size, fps, self._group_outputs, devices,
                               f"Test {test.id} · {test.animal_id} · {app.name}", entry=e)
        m["record_path"] = s.record_path
        m["io_plan"] = plan
        self.group.arm(e, s)
        self.main.mark_dirty()
        if panel is not None:
            panel.view.set_apparatus(s.apparatus)
        self._log(f"{e.label}: test {test.id} armed ({self.start_mode.currentText().lower()}).", e)
        return True

    def _io_plan(self, e) -> tuple[str | None, str]:
        """Which I/O devices the test of panel `e` may use (see livegroup.device_plan), given the running tests."""
        p = self.project
        others = [x.meta.get("io_plan") or "*" for x in self.group.entries
                  if x is not e and x.session is not None and x.state in ("waiting", "running", "paused")]
        if others and self.serial.currentText().strip():
            return None, ("the serial port in the test settings is shared by every test: with several tests at "
                          "once, configure each box as an I/O device instead (Experiment › I/O devices) and clear "
                          "the serial port.")
        if not getattr(p, "io_devices", None):
            return "*", ""
        return device_plan(p.io_devices, e.meta.get("device", "") or "", others)

    def _session_devices(self, plan: str):
        """The device manager (plan "*"), or a per-test view of one box ("name") or of no hardware ("-")."""
        dm = self._open_devices()
        if plan == "*" or dm is None:
            return dm
        from ...core.iodevices import DeviceView

        return DeviceView(dm, None if plan == "-" else plan)

    def arm_all(self, entries=None) -> int:
        """Arm every idle (or finished) row; video files restart so the tests start at their beginning (unless
        another test already running uses the same video)."""
        ents = [e for e in (entries or self.group.entries) if e.source_key is not None
                and e.state in ("idle", "finished")]
        if not ents:
            return 0
        busy = {x.source_key for x in self.group.entries if x not in ents and x.state in ("running", "paused")}
        n = sum(1 for e in ents if self.arm_row(e))
        if not n:
            return 0
        for key in {e.source_key for e in ents if e.session is not None}:
            spec = self.group.sources.get(key)
            if spec is not None and spec.is_file:
                if key in busy:
                    self._log(f"{spec.label}: another test is running on this video: the new test starts at the "
                              f"current position.")
                    continue
                self.group.restart_source(key)
        # a daily schedule re-arms its tests when it fires: they keep that schedule rather than get another one
        scheduled = {i for sch in self.group.schedules if sch.entry_ids is not None for i in sch.entry_ids}
        ids = [e.id for e in ents if e.session is not None and e.id not in scheduled]
        if self.start_mode.currentData() == "scheduled" and ids:
            sch = self._new_schedule(ids)
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
            if st == "paused":
                self.group.resume(e)
                self._log(f"{fmt_time(e.elapsed)}  test resumed", e)
            elif st == "running":
                self.group.pause(e)
                self._log(f"{fmt_time(e.elapsed)}  test paused", e)
        elif kind == "stop":
            self.group.stop(e, save=st in ("running", "paused"))
            self._save_finished_entries()
        self._update_row_states()
        self._update_buttons()

    def _start_entry_now(self, e):
        """Panel ▶ ▾ Start now: arm the test if needed and start it without waiting for its start condition."""
        if e.state in ("idle", "finished") and not self.arm_all([e]):
            return False
        if e.state == "waiting":
            e.session.request_start()
            self._log("Start requested.", e)
        self._update_row_states()
        self._update_buttons()
        return True

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
            if not self._store(test, s, m.get("record_path"), not e.aborted, bool(m.get("new_test")), e):
                self._log(f"{e.label}: test discarded.", e)
            else:
                self._log(f"{e.label}: test {test.id} finished after {fmt_time(s.elapsed)} and saved.", e)
                self._show_results(test, switch=False)
                m["trial"] = int(m.get("trial", 1)) + 1
                rows = self._entries()
                if e in rows:
                    self._fill_row(rows.index(e), e)
                if e is self._selected_entry():
                    self.row_trial.blockSignals(True)
                    self.row_trial.setValue(m["trial"])
                    self.row_trial.blockSignals(False)
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
        self._update_single_title()
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
        if self._store(test, o, None, save, self._obs_new):
            self._log(f"Observation of test {test.id} saved: {len(o.events)} events in {fmt_time(o.elapsed)}.")
            self._show_results(test)
        else:
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
            self._update_single_title()
            if self.obs.state == "finished":
                self.obs_stop(save=True)
        if self.score_pad is not None and self.mode == "single":
            self.score_pad.set_active(list(self.session.open_states) if self.session is not None else [])
        s = self.session
        if s is not None and self.mode == "single" and self.single_panel.stack.currentIndex() == 2:
            with s.lock:
                rows, zones = s.stats.rows(), (s.stats.current_zones() if s.stats.detected else [])
            self.single_panel.set_zone_rows(rows, zones)
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
            if d is None:
                continue
            panels = [self._panels[e.id] for e in self.group.entries_for(key) if e.id in self._panels]
            if panels:
                pix = cv_to_qpixmap(d)  # one conversion for every panel showing this camera
                for p in panels:
                    if p.isVisible() or not p.view.frame_size:
                        p.view.set_pixmap(pix)
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
        devs = s.devices if s is not None else None
        self.monitor.refresh(s, title, devs if devs is not None else self.devices, warns)

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
        for w in self.setup_widgets:
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
        e = self._entry_of(s)
        done = []  # what this key press did, for Undo: (kind, event, behaviour)

        def score(name: str, kind: str) -> bool:
            """kind: "point", "start" or "end". False when the test ended meanwhile (nothing scored)."""
            ev = s.score(name, "point" if kind == "point" else "state")
            if ev is None:
                return False
            what = {"point": "", "start": " starts", "end": " ends"}[kind]
            self._log(f"{fmt_time(ev['t_end'] if kind == 'end' else ev['t'])}  {name}{what}", e)
            done.append((kind, ev, name))
            return True

        if not down:
            if b.kind != "hold" or b.name not in s.open_states:
                return False
            score(b.name, "end")
        else:
            s.key(b.key)
            if b.kind == "point":
                score(b.name, "point")
            elif b.name in s.open_states:
                if b.kind == "hold":
                    return True
                score(b.name, "end")
            elif all(score(o.name, "end") for o in wf.exclusive_partners(self.project.behaviours, b)
                     if o.name in s.open_states):
                score(b.name, "start")
        self._push_undo(s, done)
        if s is self.session:
            self.vals["events"].setText(str(len(s.events)))
        return bool(done)

    def _entry_of(self, session):
        if session is None or session is self.session or session is self.obs:
            return None
        return next((x for x in self.group.entries if x.session is session), None)

    def _push_undo(self, session, done):
        if done:
            self._undo.append((session, done))
            del self._undo[:-200]
            self._update_undo_buttons()

    def _can_undo(self, session) -> bool:
        return session is not None and session.state in ("running", "paused") and \
            any(s is session for s, _ in self._undo)

    def undo_last_event(self, session=None) -> bool:
        """Panel toolbar ↶: take back the last scoring key press of this test (a point event, the start or the end
        of a behaviour)."""
        session = session if session is not None else self._scoring_target()
        i = next((i for i in range(len(self._undo) - 1, -1, -1) if self._undo[i][0] is session), None)
        if session is None or i is None or session.state not in ("running", "paused"):
            return False
        _, done = self._undo.pop(i)
        with session.lock:
            for kind, ev, name in reversed(done):
                if kind in ("point", "start"):
                    session.events[:] = [x for x in session.events if x is not ev]
                    if session.open_states.get(name) is ev:
                        session.open_states.pop(name)
                else:
                    ev["t_end"] = None
                    session.open_states[name] = ev
        self._log("Undo: " + ", ".join(f"{name} {'ends' if k == 'end' else 'starts' if k == 'start' else ''}".strip()
                                       for k, _, name in done), self._entry_of(session))
        if session is self.session:
            self.vals["events"].setText(str(len(session.events)))
        if session is self.obs:
            self.obs_panel.show_session(session, session.duration_s)
        self._update_undo_buttons()
        return True

    def _update_undo_buttons(self):
        self.single_panel.undo_btn.setEnabled(self._can_undo(self.session))
        self.obs_panel.undo_btn.setEnabled(self._can_undo(self.obs))
        for e in self._entries():
            p = self._panels.get(e.id)
            if p is not None:
                p.undo_btn.setEnabled(self._can_undo(e.session))

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

    # ================================================================== helpers
    def _log(self, msg: str, entry=None):
        """Write to the report's log and to the Session log of the test's panel."""
        self.log.addItem(msg)
        self.log.scrollToBottom()
        if entry is not None:
            p = self._panels.get(entry.id)
            if p is not None:
                p.append_log(msg)
        elif self.mode == "single":
            self.single_panel.append_log(msg)

    def _set_state_display(self, state: str):
        if state == self._shown_state:
            return
        self._shown_state = state
        self.single_panel.set_state(state)
        if state in ("idle", "preview"):
            self.single_panel.reset_values()
            if state == "preview":
                self.vals["zone"].setText("—")

    def _update_buttons(self):
        s = self.session
        armed = s is not None
        has = self.project is not None
        mode = self.mode
        st = s.state if armed else None
        # the single test panel's toolbar
        self.arm_btn.setEnabled(has and (not armed or st == "waiting"))
        self.arm_btn.setText("Start now" if st == "waiting" else "Arm / Start test")
        self.arm_btn.setToolTip("Start the test now" if st == "waiting" else
                                "Arm the test: it starts when its start condition is met (▾ to start it now)")
        self.pause_btn.setEnabled(st in ("running", "paused"))
        self.pause_btn.setText("Resume" if st == "paused" else "Pause")
        self.pause_btn.setIcon(icon("resume" if st == "paused" else "pause"))
        self.pause_btn.setToolTip(self.pause_btn.text())
        self.stop_btn.setEnabled(armed)
        self.next_btn.setEnabled(has and not armed)
        self.preview_btn.setChecked(self.grabber is not None)
        self.preview_btn.setToolTip("Turn the camera image off" if self.grabber is not None else
                                    "Show the camera image (preview)")
        self.preview_btn.setEnabled(has and not armed)
        self.single_panel.bg_btn.setEnabled(has and not armed)
        self.single_panel.set_recording(st in ("running", "paused") and bool(self._record_path), self._record_path)
        self._update_undo_buttons()
        # ribbon: All apparatus
        states = [e.state for e in self._entries()]
        o = self.obs
        if mode == "multi":
            start = has and bool(states) and any(x != "running" for x in states)
            arm = has and any(x in ("idle", "finished") for x in states)
            pause, resume = "running" in states, "paused" in states
            stop = any(x in ("waiting", "running", "paused") for x in states)
        elif mode == "observe":
            ost = o.state if o is not None else None
            start = arm = has and ost in (None, "finished", "waiting", "paused")
            pause, resume, stop = ost == "running", ost == "paused", ost in ("waiting", "running", "paused")
        else:
            start = has and (not armed or st == "waiting")
            arm = has and not armed
            pause, resume, stop = st == "running", st == "paused", armed
        self.start_all_act.setEnabled(start)
        self.start_now_act.setEnabled(start)
        self.arm_all_act.setEnabled(arm)
        self.pause_all_act.setEnabled(pause)
        self.resume_all_act.setEnabled(resume)
        self.stop_all_act.setEnabled(stop)
        # ribbon: Session
        cams = mode != "observe"
        single_free = not (mode == "single" and armed)
        self.add_source_act.setEnabled(has and cams and single_free)
        self.add_panel_act.setEnabled(has and (mode == "multi" or not self.any_active()))
        e = self._selected_entry() if mode == "multi" else None
        self.remove_panel_act.setEnabled(e is not None and e.state not in ("waiting", "running", "paused"))
        self.capture_bg_act.setEnabled(has and ((mode == "single" and not armed) or
                                                (mode == "multi" and bool(self.group.runners))))
        self.cam_opts_act.setEnabled(has and cams and single_free and (mode == "single" or bool(self.group.sources)))
        on = (self.grabber is not None) if mode == "single" else bool(self.group.runners) if mode == "multi" else False
        self.camera_act.setChecked(on)
        self.camera_act.setEnabled(has and cams and single_free)
        self.next_test_act.setEnabled(has and mode != "multi" and not armed and (o is None or o.state == "finished"))
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
