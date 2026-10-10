"""One test: its source and preview, frame processing, test setup and run control."""

from __future__ import annotations

import copy
import datetime as _dt
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QTableWidgetItem

from ....core import autosave, diskspace
from ....core.camera import CameraView, SourceReader, SourceSpec, camera_settings, set_camera_settings
from ....core.camhw import CameraHardware
from ....core.camsources import is_native_source, list_native_cameras
from ....core.live import IOSession, LiveSession, draw_display_texts
from ....core.livemonitor import beam_angle
from ....core.livegroup import ClockSchedule
from ....core.procedures import Outputs, test_context
from ....core.project import INFO_COLUMNS
from ....core.session import END_SOURCE, END_SOURCE_FAILED, END_USER, finish_live_test
from ....core.tracking import ArenaTracker, DetectionSettings, draw_tracking
from ....core.video import VIDEO_EXTENSIONS, VideoSource, list_cameras
from ...confirm_id import confirm_animal_id, weigh_before_test
from ...io_devices_dialog import open_device_manager
from ...live_widgets import CameraOptionsDialog, short_time
from ...widgets import Worker, error_box, fmt_time
from .common import TRAIL_LEN, describe_view, peek_frame, recording_path


class _GrabberSignals(QObject):
    frame_ready = Signal(object, object)
    opened = Signal(int, int, float)
    background_ready = Signal(object)
    ended = Signal()
    failed = Signal(str)
    capture = Signal(str)  # a camera drop-out / its recovery, for the log


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
        self.session_of = None  # () -> the armed session, told about camera drop-outs (from the reader thread)

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

    def on_capture_lost(self, msg: str):
        s = self.session_of() if self.session_of is not None else None
        if s is not None and hasattr(s, "capture_lost"):
            s.capture_lost(msg)
        self.signals.capture.emit(f"Video capture lost ({msg}) — reconnecting the camera…")

    def on_capture_restored(self, gap_s: float):
        s = self.session_of() if self.session_of is not None else None
        if s is not None and hasattr(s, "capture_restored"):
            s.capture_restored(gap_s)
        self.signals.capture.emit(f"Video capture restored after {gap_s:.1f} s.")


def scan_all_cameras() -> tuple[list[tuple[str, object]], list[str]]:
    """Cameras for the camera chooser: OpenCV indices (webcams, UVC cameras, capture cards) then the native
    industrial cameras of the installed SDKs, as (label, source); and messages about the SDKs that are missing."""
    cams = [(f"Camera {i}", i) for i in list_cameras()]
    try:
        native, messages = list_native_cameras()
    except Exception as e:  # never let an SDK break the scan
        native, messages = [], [f"Industrial cameras: {e}"]
    return cams + [(c.label, c.source) for c in native], messages


class SingleTestMixin:
    """One test: its source and preview, frame processing, test setup and run control."""

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
        w = Worker(lambda progress, stop: scan_all_cameras(), self)
        w.signals.done.connect(self._cameras_found)
        w.signals.failed.connect(lambda msg: self._cameras_found(([], [])))
        w.finished.connect(w.deleteLater)
        self._scan_worker = w
        w.start()

    def _cameras_found(self, found):
        """found: ([(label, source)], messages) from scan_all_cameras (or a list of camera indices)."""
        self._scan_worker = None
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan cameras")
        cams, messages = found if isinstance(found, tuple) else ([(f"Camera {i}", i) for i in found], [])
        tip = "Look for cameras connected to this computer"
        self.scan_btn.setToolTip(tip + ("\n\n" + "\n".join(messages) if messages else ""))
        cur = self.camera.currentData()
        self.camera.clear()
        for lbl, src in cams:
            self.camera.addItem(lbl, src)
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
            f = peek_frame(path)
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
        d = self.camera.currentData()
        return d if is_native_source(d) else int(d or 0)

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
        self._hardware = CameraHardware.from_dict(d.get("hardware"))
        self.view_lbl.setText(describe_view(self._view, self._second, self._merge_layout, self._hardware))
        self._update_single_title()

    def _merge_choices(self, exclude=None) -> list[tuple[str, object]]:
        out = [(self.camera.itemText(i), self.camera.itemData(i)) for i in range(self.camera.count())]
        files = [self.sim_path.text().strip()] + [s.source for s in self.group.sources.values() if s.is_file]
        for f in dict.fromkeys(x for x in files if x):
            out.append((Path(f).name, f))
        return [(lbl, s) for lbl, s in out if s != exclude]

    def camera_options(self) -> bool:
        """Region / zoom / rotation / flip / merge options and camera hardware settings of the single-test
        source (hardware settings change live while the camera image is on)."""
        src = self._source()
        if src is None:
            QMessageBox.information(self, "Camera options", "Choose a camera or a video file first.")
            return False
        raw, raw2 = self.grabber.raw_frames() if self.grabber is not None else (None, None)
        if raw is None:
            # the last image only when it is of this source, untransformed (never another camera's / file's)
            raw = self._last_frame if (self._view.is_identity and self._second is None
                                       and self._frame_key == self._single_key()) else None
        if raw is None and SourceSpec(src).is_file:
            raw = peek_frame(src)
        if raw2 is None and self._second is not None and SourceSpec(self._second).is_file:
            raw2 = peek_frame(self._second)
        camera = self.grabber.camera() if self.grabber is not None and not self.simulating else None
        dlg = CameraOptionsDialog(raw, self._view, self._second, self._merge_layout, self._merge_choices(src),
                                  raw2, self, hardware=self._hardware, camera=camera, is_camera=not self.simulating,
                                  genicam=is_native_source(src))
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return False
        self._apply_single_view(dlg.result())
        return True

    def _apply_single_view(self, res: dict):
        key = self._single_key()
        view = CameraView.from_dict(res.get("view"))
        second = res.get("second")
        layout = res.get("layout", "side")
        hardware = CameraHardware.from_dict(res["hardware"]) if "hardware" in res else self._hardware
        settings = {}
        if not view.is_identity:
            settings["view"] = view.to_dict()
        if second is not None:
            settings.update(second=second, layout=layout)
        if not hardware.is_empty:
            settings["hardware"] = hardware.to_dict()
        set_camera_settings(self.project, key, settings)
        self.main.mark_dirty()
        image_changed = (view, second, layout) != (self._view, self._second, self._merge_layout)
        self._view, self._second, self._merge_layout, self._hardware = view, second, layout, hardware
        if self.grabber is not None:
            self.grabber.spec.hardware = hardware  # already applied live by the dialog
        self.view_lbl.setText(describe_view(view, second, layout, hardware))
        if not image_changed:
            return
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
        spec = SourceSpec(src, self._second, self._merge_layout, self._view, size, fps or None,
                          hardware=self._hardware if not self.simulating else CameraHardware())
        g = FrameGrabber(spec, self.process_frame, opener=VideoSource)
        g.speed = (self.sim_speed.currentData() or 1.0) if self.simulating else 1.0
        g.session_of = lambda: self.session
        sig = g.signals
        for signal, slot in ((sig.frame_ready, self._on_frame), (sig.opened, self._on_opened),
                             (sig.background_ready, self._on_file_background), (sig.ended, self._on_source_ended),
                             (sig.failed, self._on_grab_failed), (sig.capture, self._log)):
            # queued signals of a grabber already stopped (or replaced) are ignored
            signal.connect(lambda *a, slot=slot, g=g: slot(*a) if g is self.grabber else None)
        self.grabber = g
        self._source_opened = False
        self._frame_key = spec.key  # the source _last_frame comes from
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
        self._source_opened = False
        self._cancel_pending_arm()
        g.stop()
        self._update_buttons()
        if self.session is None:
            self._set_state_display("idle")

    def _toggle_preview(self):
        if self.grabber is None:
            self.start_preview()
        else:
            if self.session is not None or self._pending_arm is not None:
                QMessageBox.information(self, "Run tests", "Stop the test before stopping the camera.")
                return
            self.stop_preview()

    def _on_opened(self, w, h, fps):
        self._fps = fps
        self._frame_size = (w, h)
        self._source_opened = True
        g = self.grabber
        if g is not None and self._source_is_file and self._file_background is None and g.background is not None:
            self._on_file_background(g.background)  # (its own signal follows: the session needs it now)
        kind = "Video" if self.simulating else "Camera"
        self.main.status(f"{kind} opened: {w}×{h} at {fps:.1f} fps")
        msg = self.grabber.hardware_message if self.grabber is not None else ""
        if msg:
            self._log(f"Camera settings: {msg}")
        app = self._apparatus
        if app is not None and app.frame_size and tuple(app.frame_size) != (w, h):
            self._log(f"Note: apparatus “{app.name}” was drawn on a {app.frame_size[0]}×{app.frame_size[1]} "
                      f"image but the source is {w}×{h}.")
        if self._pending_arm is not None:  # armed before the source was open: the session gets its real fps / size
            test, self._pending_arm = self._pending_arm, None
            start = self._pending_start
            self._pending_start = False
            if self._arm_session(test) and start and self.session is not None and self.session.state == "waiting":
                self.session.request_start()
                self._log("Start requested.")

    def _on_file_background(self, bg):
        self._file_background = bg
        if self._background is None:
            self.bg_status.setText("Using the median of the video file (simulation)")
            self._reset_preview_tracker()

    def _on_grab_failed(self, msg):
        self._log(f"Error: {msg}")
        self._cancel_pending_arm()
        if self.session is not None:
            self.stop_test(save=len(self.session.cols["t"]) > 0, quiet=True, reason=END_SOURCE_FAILED)
        self.stop_preview()
        error_box(self, "Run tests", msg)

    def _on_source_ended(self):
        if self.session is None:
            return
        if self.session.state in ("running", "paused"):
            self._log("End of the video file — test finished.")
            self.stop_test(save=True, quiet=True, reason=END_SOURCE)
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

    @staticmethod
    def _session_info(s) -> dict:
        """What the panel, the log and the end of the test need from a running session (under its lock)."""
        state = s.state
        return {"session": s, "state": state, "elapsed": s.elapsed if state != "waiting" else 0.0,
                "duration": s.duration_s, "events": len(s.events), "fired": list(s.engine.fired),
                "outputs": list(s.outputs.log) if s.outputs is not None else [], "proc_log": list(s.log),
                "phase": s.start_phase, "waiting_end": s.waiting_end, "distance": s.stats.distance * s.stats.factor,
                "unit": s.stats.unit}

    @property
    def io_only(self) -> bool:
        """The protocol runs its tests with the I/O devices only (ANY-maze's Input/output only mode): no camera."""
        p = self.project
        return p is not None and p.settings_extra.get("mode") == "io_only"

    def _io_refresh(self):
        """An I/O-only test has no camera frames: its clock drives the panel, the log and the end of the test."""
        s = self.session
        if s is None or not s.io_only or self.mode != "single":
            return
        with s.lock:
            info = self._session_info(s)
        info.update(detected=False, zones=[], io_only=True, popups=s.take_popups())
        self._on_frame(None, info)

    def process_frame(self, frame: np.ndarray, ts: float):
        """Track one frame (called from the grabber thread). Returns (display frame, info dict)."""
        with self._lock:
            self._last_frame = frame
            app = self._apparatus
            s = self.session
            trail = None
            if s is not None:
                dets = s.process(frame, ts)
                d = dets[0] if dets else None
                trail = s.trail(TRAIL_LEN) if self._show_trail else None
                info = self._session_info(s)
            else:
                if self._preview_tracker is None:
                    self._preview_tracker = self._make_preview_tracker(frame, app)
                dets, _fg = self._preview_tracker.process(frame) if self._preview_tracker else ([], None)
                d = dets[0] if dets else None
                info = {"state": "preview", "distance": 0.0, "unit": app.report_unit if app else "px"}
            info["detected"] = bool(d is not None and d.detected)
            zones = []
            if s is not None and s.state in ("running", "paused"):
                zones = s.stats.current_zones() if info["detected"] else []
            elif app is not None and d is not None and d.detected:
                zm = app.zone_membership(np.array([d.x]), np.array([d.y]))
                zones = [k for k, v in zm.items() if bool(np.asarray(v).ravel()[0])]
            info["zones"] = zones
        disp = draw_tracking(frame, [d] if d is not None else [], trail,
                             beam=self._show_beam and beam_angle(s))
        if s is not None:  # the procedures' texts on the display and pop-up messages
            disp = draw_display_texts(disp, s.display_texts)
            info["popups"] = s.take_popups()
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
        btn_state = state + ("/end" if info.get("waiting_end") else "")  # "waiting for test end"
        if btn_state != self._btn_state:
            self._btn_state = btn_state
            self._update_buttons()
        self.vals["zone"].setText(", ".join(info["zones"]) if info["zones"] else
                                  ("—" if info["detected"] or info.get("io_only") else "not detected"))
        # a preview frame, or a stale one of the previous test, may arrive just after arming
        for pop in info.get("popups") or ():
            self._show_popup(pop)
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
        """The project's I/O devices, opened in a Worker (a board can take seconds); problems are shown and logged
        (the tests still run, without the devices that failed)."""
        p = self.project
        if self.devices is None and p is not None and p.io_devices:
            self.devices, problems = open_device_manager(self, p.io_devices)
            if problems:
                for msg in problems:
                    self._log(f"I/O devices: {msg}")
                box = QMessageBox(QMessageBox.Warning, "I/O devices", "\n".join(problems[-10:]), QMessageBox.Ok, self)
                box.setModal(False)
                box.setAttribute(Qt.WA_DeleteOnClose)
                box.show()
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
        return {"autosave_path": path, "autosave_key": p.file_key, "autosave_meta": {
            "test_id": test.id, "animal": test.animal_id, "apparatus": test.apparatus, "stage": test.stage,
            "trial": test.trial}}

    def _make_session(self, test, app, bg, size, fps: float, outputs, devices, name: str, entry=None,
                      on_stimulus=None) -> LiveSession:
        """A live session of `test` in `app` with the page's settings: detection (an adaptive background without
        an empty-arena image `bg`), duration and start, procedures, recording, warnings, pausing, crash recovery.
        In an Input/output only protocol (with several tests: a test panel without a camera) an IOSession."""
        p = self.project
        io = self.io_only if entry is None else entry.source_key is None
        if io:  # no camera: the I/O devices and the procedures on the computer's clock
            mode = self._session_mode()
            if mode in ("on_detection", "experimenter_leaves"):
                self._log("I/O only: the test starts as soon as it is armed (no camera to detect the animal).",
                          entry)
                mode = "immediate"
            return IOSession(app, duration_s=self.duration.value(), start_mode=mode,
                             procedures=copy.deepcopy(p.procedures), outputs=outputs, analysis=p.analysis_for(test),
                             devices=devices, variables=p.variables, name=name, zone_overrides=test.zone_overrides,
                             on_stimulus=on_stimulus, outputs_off_on_pause=self.pause_off.isChecked(),
                             test_info=test_context(p, test), control_input=self.control_input.text().strip(),
                             sync=p.sync, start_input=self.start_input.text().strip(),
                             start_delay_s=float(p.start_switch_delay_s or 0.0), **self._autosave_args(test))
        settings = self._detection_settings(test)
        if settings.background == "frame" and bg is None:
            settings.background = "adaptive"
            self._log("No empty-arena background: using an adaptive background.", entry)
        s = LiveSession(app, settings, duration_s=self.duration.value(), start_mode=self._session_mode(),
                        procedures=copy.deepcopy(p.procedures), outputs=outputs,
                        record_path=recording_path(p, test, size, fps) if self.record.isChecked() else None,
                        fps=fps, analysis=p.analysis_for(test), devices=devices, variables=p.variables,
                        record_overlay=self.record_overlay.isChecked(), lost_warning_s=self.lost_warn.value(),
                        split_minutes=self.split_min.value(),
                        name=name, zone_overrides=test.zone_overrides, on_stimulus=on_stimulus,
                        outputs_off_on_pause=self.pause_off.isChecked(), test_info=test_context(p, test),
                        control_input=self.control_input.text().strip(), sync=p.sync,
                        start_input=self.start_input.text().strip(),
                        start_delay_s=float(p.start_switch_delay_s or 0.0), **self._autosave_args(test))
        if bg is not None:
            s.set_background(bg)
        if s.record_path:  # room for the recording?
            space = diskspace.check(s.record_path, diskspace.recording_bytes(size[0], size[1], fps, s.duration_s))
            if not space.ok:
                self._log(f"Warning: {space.message}", entry)
                s.warn(space.message, 0.0)
        return s

    def _show_popup(self, pop: dict, entry=None):
        """A procedure's "show a pop-up message": a non-modal message box (the test keeps running)."""
        prefix = f"{entry.label} · " if entry is not None else ""
        self._log(f"{prefix}{fmt_time(pop.get('t', 0))}  message: {pop.get('text', '')}", entry)
        box = QMessageBox(QMessageBox.Information, f"{prefix}{pop.get('title') or 'Procedure'}",
                          str(pop.get("text", "")), QMessageBox.Ok, self)
        box.setModal(False)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

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
        if s is not None and s.waiting_end:
            if s.continue_test():  # "waiting for test end": the button continues the test
                self._log(f"{fmt_time(s.elapsed)}  test continued")
            self._update_buttons()
            return
        if s is not None and s.state == "waiting":
            s.request_start()  # armed: the button now starts the test immediately
            self._log("Start requested.")
            return
        self.arm()

    def arm(self) -> bool:
        """Arm the test.  Without the camera image on, the source is opened first and the test is armed when it
        is open (_on_opened): its session needs the real frame rate, image size and (video file) background."""
        p = self.project
        if p is None or self.session is not None or self._pending_arm is not None:
            return False
        if not self.procedures_ready():
            return False
        test, new = self._prepare_test(need_apparatus=not self.io_only)
        if test is None:
            return False
        self.test = test
        self._new_test = new
        if not confirm_animal_id(self, test) or not weigh_before_test(self, test, reader=self.scale_reader):
            self._discard_new_test()
            return False
        if self.io_only:
            return self._arm_session(test)
        if self.grabber is None and not self.start_preview():
            self._discard_new_test()
            return False
        if self.grabber is not None and not self._source_opened:
            self._pending_arm = test
            self._log(f"Test {test.id}: opening the {'video' if self.simulating else 'camera'} — the test is "
                      f"armed as soon as it is open.")
            self._update_buttons()
            return True
        return self._arm_session(test)

    def _cancel_pending_arm(self):
        """The source of a test being armed failed or was stopped: the test is not armed."""
        if self._pending_arm is None:
            return
        self._pending_arm = None
        self._pending_start = False
        self._discard_new_test()
        self._log("Test not armed.")
        self._update_buttons()

    def _arm_session(self, test) -> bool:
        p = self.project
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
        if session.io_only:
            session.start_clock()
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
        if self.session is not None and self.session.waiting_end:
            self._arm_clicked()  # continue a test waiting for its end
            return True
        if self.session is None and not self.arm():
            return False
        if self._pending_arm is not None:
            self._pending_start = True  # started as soon as the source is open
            return True
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
            self._cancel_pending_arm()
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

    def stop_test(self, save: bool = True, quiet: bool = False, reason: str = END_USER):
        if self.session is None:
            self._cancel_pending_arm()
            return
        with self._lock:
            self.session.finish(reason)
        self._finalise(save=save and self.session.has_data, quiet=quiet)

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
            if new_test and test is not None:  # the test created for it was removed: the Test schedule follows
                self.main.notify_tests_changed()
            return False
        self.main.mark_dirty()
        self._save_soon(session)
        self.last_test_id = test.id
        return True

    def _save_soon(self, session):
        """Save the experiment with a finished test (its crash-recovery file goes once saved).  While other tests
        run, the save is made once for every test finished meanwhile, from the event loop (no stall of the tick
        that stores several tests)."""
        pending = self._unsaved_sessions
        pending.append(session)
        if not self.any_active():
            self._flush_save()
        elif len(pending) == 1:
            QTimer.singleShot(0, self._flush_save)

    def _flush_save(self):
        pending = self._unsaved_sessions
        if not pending:
            return
        self._unsaved_sessions = []
        if self.main.save():
            for s in pending:
                s.remove_autosave()

    def _discard_new_test(self):
        if self._new_test and self.test is not None:
            self._remove_test(self.test)
        self.test = None

    def _show_results(self, test, switch: bool = True):
        """The results of a finished test in the report.  While other tests run they are calculated in a
        background thread (the other tests' panels keep updating)."""
        project = self.project
        if self.any_active():
            w = Worker(lambda progress, stop: project.analyse_test(test), self)
            w.signals.done.connect(lambda rows: self.project is project and self._fill_results(test, rows, switch))
            w.signals.failed.connect(lambda msg: self.project is project and (
                self._log(f"Analysis failed: {msg}"), self._fill_results(test, [], switch)))
            w.finished.connect(w.deleteLater)
            w.start()
            return
        try:
            rows = project.analyse_test(test)
        except Exception as e:
            self._log(f"Analysis failed: {e}")
            rows = []
        self._fill_results(test, rows, switch)

    def _fill_results(self, test, rows: list, switch: bool):
        self.last_results = rows
        self.results_title.setText(f"Test {test.id} · animal {test.animal_id} · {test.stage or ''} trial "
                                   f"{test.trial}")
        self.results.setRowCount(0)
        if rows:
            skip = set(INFO_COLUMNS) | set(self.project.animal_fields)
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
