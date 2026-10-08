"""The Run tests page: the shell that holds the three modes together (see the package docstring)."""

from __future__ import annotations

import copy
import datetime as _dt
import json
import threading

import numpy as np
from PySide6.QtCore import QSize, QTime, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QScrollArea, QSizePolicy,
                               QStackedWidget, QTabWidget, QToolButton)

from ....core import autosave
from ....core import camsources
from ....core.camera import CameraView
from ....core.camhw import CameraHardware
from ....core.live import LiveSession, ObservationSession
from ....core.livegroup import DEFAULT_START_KEYS, DEFAULT_STOP_KEYS, ClockSchedule, LiveGroup
from ....core.procedures import Outputs
from ....core.tracking import ArenaTracker
from ....core.video import MAX_CAMERAS
from ...icons import icon
from ...live_widgets import (LAYOUT_LABELS, LAYOUTS, IndustrialCamerasDialog, MonitorPanel, ObservationPanel,
                             PanelSettingsDialog, TestPanel)
from ...touchscreen import TouchStimulusWindow
from ...widgets import Worker, cv_to_qpixmap, fmt_time
from ..base import Page
from .calibration import CalibrationMixin
from .common import MODE_ACTIONS, MODES, TRAIL_LEN, VIEW_DEFAULTS, parse_keys, serial_ports
from .keys import KeysMixin
from .multi import MultiTestMixin
from .observe import ObservationMixin
from .setup_tab import SetupMixin
from .single import FrameGrabber, SingleTestMixin


class LivePage(SetupMixin, SingleTestMixin, MultiTestMixin, ObservationMixin, KeysMixin, CalibrationMixin, Page):
    """Run tests: ANY-maze's Test page while tests run — a panel per test (toolbar, title, camera image with the
    apparatus, time slider, Session log / Video / Zones tabs), several panels in a grid, and a collapsible report
    (setup, real-time monitor, procedures, results, log) on the right.  Commands live in the ribbon."""

    title = "Run tests"
    CTI_KEY = "cameras/cti_files"  # GenTL producers of the GenICam backend (machine setting, not per project)

    def __init__(self, main):
        super().__init__(main)
        camsources.set_cti_files(self._cti_files_setting())
        self._lock = threading.RLock()
        self._loading = False
        self.grabber: FrameGrabber | None = None
        self._scan_worker: Worker | None = None
        self.session: LiveSession | None = None
        self.test = None
        self._new_test = False
        self._pending_arm = None  # the test armed while its source opens (made a session by _on_opened)
        self._pending_start = False
        self._unsaved_sessions: list = []  # finished tests stored, the experiment not saved yet (_save_soon)
        self._source_opened = False
        self._record_path: str | None = None
        self._apparatus = None
        self._preview_tracker: ArenaTracker | None = None
        self._background: np.ndarray | None = None
        self._file_background: np.ndarray | None = None
        self._last_frame: np.ndarray | None = None
        self._frame_key = None  # SourceSpec key of the source of _last_frame
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
        self._show_beam = True  # the animal's orientation drawn as a "flashlight beam"
        self._source_is_file = False
        self._outputs: Outputs | None = None
        self._schedule: ClockSchedule | None = None  # single-test scheduled start
        self._view = CameraView()  # single-test camera options
        self._second = None
        self._merge_layout = "side"
        self._hardware = CameraHardware()  # single-test camera hardware settings
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
        self._key_filter = False  # the application-wide key filter is installed (tests run, page shown)
        self._keys_wanted = False  # tests run: scoring keys / keys for the procedures wanted
        self._last_key = None
        self._holds: dict = {}  # "hold" behaviour → the session its key press started it in
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
        m.addAction(icon("settings"), "Industrial cameras…", self.industrial_cameras)
        self.add_source_act.setMenu(m)
        self.add_panel_act = A("panel_add", "Add test panel", "Add a test (camera image × apparatus × animal) to "
                               "the session — switches to Several tests.", self.add_panel_clicked)
        self.remove_panel_act = A("delete", "Remove panel", "Remove the selected test panel.",
                                  self.remove_session_row)
        self.capture_bg_act = A("image_capture", "Capture backgrounds", "Take the current camera image(s) as the "
                                "empty-arena background. The arenas must be empty.", self.capture_backgrounds_clicked)
        self.cam_opts_act = A("settings", "Camera options", "Region of the image, digital zoom / pan, rotation, "
                              "flip, merging two cameras; camera settings (exposure, gain, white balance…).",
                              self.camera_options_clicked)
        self.camera_act = A("video", "Camera image", "Turn the camera image(s) on or off (preview before "
                            "the test).", self.camera_toggled, checkable=True)
        self.next_test_act = A("forward", "Next test", "Go to the next test of the test schedule.", self.next_test)
        self.calibrate_act = A("ruler", "Adjust calibration", "Change the scale (pixels per cm) of the running test "
                               "(the selected panel's with several tests); it is saved with the test and used for "
                               "its results.", self.adjust_calibration)
        self.geometry_act = A("area", "Adjust apparatus", "Move, rotate or scale the apparatus map — or move one "
                              "zone — of the running test (the selected panel's with several tests); it is saved "
                              "with the test and used for its results.", self.adjust_geometry)
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
        self.beam_act = m.addAction("Animal's orientation (flashlight beam)")
        self.zones_act = m.addAction("Highlight the zone the animal is in")
        self.labels_act = m.addAction("Zone names")
        for a, k in ((self.trail_act, "trail"), (self.beam_act, "beam"), (self.zones_act, "zones"),
                     (self.labels_act, "labels")):
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
                             (self.camera_act, "small"), (self.cam_opts_act, "small"), (self.next_test_act, "small"),
                             (self.calibrate_act, "small"), (self.geometry_act, "small")]),
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
        """Add a camera: one found by the last scan (industrial cameras included) or a camera number."""
        found = [(self.camera.itemText(i), self.camera.itemData(i)) for i in range(self.camera.count())]
        src = None
        if any(camsources.is_native_source(s) for _, s in found):
            other = "Another camera number…"
            item, ok = QInputDialog.getItem(self, "Add camera", "Camera:", [lbl for lbl, _ in found] + [other], 0,
                                            False)
            if not ok:
                return
            src = next((s for lbl, s in found if lbl == item), None)
        if src is None:
            src, ok = QInputDialog.getInt(self, "Add camera", "Camera number (0 = first camera):", 0, 0,
                                          MAX_CAMERAS - 1)
            if not ok:
                return
        if self.mode == "multi":
            self.add_source(src)
            return
        restart = self.grabber is not None
        self.stop_preview()
        if self.camera.findData(src) < 0:
            self.camera.addItem(f"Camera {src}" if isinstance(src, int) else camsources.native_label(src), src)
        self.camera.setCurrentIndex(self.camera.findData(src))
        self.cam_radio.setChecked(True)
        self._update_single_title()
        if restart:
            self.start_preview()

    def _cti_files_setting(self) -> list[str]:
        settings = getattr(self.main, "settings", None)
        v = settings.value(self.CTI_KEY, []) if settings is not None else []
        return [v] if isinstance(v, str) and v else [str(x) for x in (v or []) if x]

    def industrial_cameras(self) -> bool:
        """GenICam / vendor SDK backends and the GenTL producer files; rescans the cameras when changed."""
        dlg = IndustrialCamerasDialog(self._cti_files_setting(), self)
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return False
        files = dlg.files()
        settings = getattr(self.main, "settings", None)
        if settings is not None:
            settings.setValue(self.CTI_KEY, files)
        camsources.set_cti_files(files)
        self.scan_cameras()
        return True

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
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return False
        self._set_pref("panel", dlg.result_settings())
        return True

    def _all_panels(self) -> list[TestPanel]:
        return [self.single_panel] + list(self._panels.values())

    def _apply_view_prefs(self):
        d = self.prefs
        for a, k in ((self.fit_act, "fit"), (self.trail_act, "trail"), (self.beam_act, "beam"),
                     (self.zones_act, "zones"), (self.labels_act, "labels"), (self.hide_report_act, "hide_report"),
                     (self.hide_app_act, "hide_apparatus")):
            a.setChecked(bool(d.get(k)))
        lay = d.get("layout") if d.get("layout") in LAYOUTS else "2x2"
        self.layout_acts[lay].setChecked(True)
        self.mosaic.set_layout_key(lay)
        self._show_trail = bool(d.get("trail", True))
        self.group.trail_len = TRAIL_LEN if self._show_trail else 0
        self._show_beam = bool(d.get("beam", True))
        self.group.beam = self._show_beam
        self.tabs.setVisible(not d.get("hide_report"))
        for p in self._all_panels():
            self._apply_prefs_to_panel(p)

    def _apply_prefs_to_panel(self, p: TestPanel):
        d = self.prefs
        p.view.set_scale_to_fit(bool(d.get("fit", True)))
        p.view.set_indicators(zones=bool(d.get("zones", True)), labels=bool(d.get("labels", False)))
        p.view.set_apparatus_visible(not d.get("hide_apparatus"))
        p.apply_settings(d.get("panel") or {})

    # ================================================================== project / visibility
    def set_project(self, project):
        self._close_touch()
        self.stop_test(save=False, quiet=True)
        self.stop_preview()
        self.group.close()
        self.group = LiveGroup()
        self.group.trail_len = TRAIL_LEN if self._show_trail else 0
        self.group.beam = self._show_beam
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
            self.start_mode.setCurrentIndex(max(0, self.start_mode.findData(project.start_mode))
                                            if project.start_mode != "manual" else 0)
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
        armed = self.session is not None or self.obs is not None or self._pending_arm is not None
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
                pad.set_behaviours(p.behaviours)
        self.score_pad.setVisible(bool(p.behaviours))
        self._update_keys_label()
        self._update_single_title()
        self._update_buttons()
        cur = getattr(self.main, "current_page", None)
        if cur is None or cur() is self:  # (set_project also calls on_show while another page is shown)
            self._sync_key_filter(True)

    def on_hide(self):
        self._sync_key_filter(False)  # scoring keys only while this page is shown
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
            self._touch = TouchStimulusWindow.from_project(self.project)
            self._touch_cfg = copy.deepcopy(cfg)
            self._touch.show_on_screen()
        elif not self._touch.isVisible():  # closed with Esc during an earlier test: shown again for this one
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
        self._flush_save()

    def shutdown(self):
        self._close_touch()
        self.stop_and_save_all()
        self.group.close()
        self._save_finished_entries()
        self._flush_save()
        self.stop_preview()
        self._close_devices()
        self._enable_shortcuts(False)  # the application-wide key filter goes with the page
        self._ui_timer.stop()
        self._mosaic_timer.stop()
        if self._scan_worker is not None:
            self._scan_worker.wait(3000)

    def any_active(self) -> bool:
        return (self.session is not None or self._pending_arm is not None
                or (self.obs is not None and self.obs.state != "finished")
                or any(e.state in ("waiting", "running", "paused") or e.meta.get("arm_pending")
                       for e in self.group.entries))

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
            self.control_input.setText(str(d.get("control_input", "")))
            self.record_overlay.setChecked(bool(d.get("record_overlay", False)))
            self.split_min.setValue(float(d.get("split_minutes", 0.0)))
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
               "split_minutes": self.split_min.value(), "schedule_at": self.sched_time.time().toString("HH:mm"),
               "schedule_daily": self.sched_daily.isChecked(), "control_input": self.control_input.text().strip()}
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
        if self.mode == "single":
            self.score_pad.set_active(list(self.session.open_states) if self.session is not None else [])
        s = self.session
        if s is not None and self.mode == "single" and self.single_panel.stack.currentIndex() == 2:
            with s.lock:
                rows, zones = s.stats.rows(), (s.stats.current_zones() if s.stats.detected else [])
            self.single_panel.set_zone_rows(rows, zones)
        if self.group.entries:
            self._complete_pending_arms()  # tests armed while their source was opening
            for e in self.group.entries:  # the procedures' pop-up messages (multi-test mode)
                take = getattr(e.session, "take_popups", None)
                for pop in (take() if take is not None else ()):
                    self._show_popup(pop, e)
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
        armed = s is not None or self._pending_arm is not None  # (pending: armed once its source is open)
        has = self.project is not None
        mode = self.mode
        st = s.state if s is not None else None
        # the single test panel's toolbar
        waiting_end = s is not None and s.waiting_end  # a procedure ended the test allowing continuation
        self.arm_btn.setEnabled(has and (not armed or st == "waiting" or waiting_end))
        self.arm_btn.setText("Continue test" if waiting_end else "Start now" if st == "waiting" else
                             "Arm / Start test")
        self.arm_btn.setToolTip("Waiting for test end: continue the test (within 10 s)" if waiting_end else
                                "Start the test now" if st == "waiting" else
                                "Arm the test: it starts when its start condition is met (▾ to start it now)")
        self.pause_btn.setEnabled(st in ("running", "paused") and not waiting_end)
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
        self.calibrate_act.setEnabled(has and mode != "observe" and self._calibration_target()[0] is not None)
        self.geometry_act.setEnabled(self.calibrate_act.isEnabled())
        self.obs_panel.show_session(self.obs, self.obs.duration_s if self.obs else 0.0)
