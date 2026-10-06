"""Live (real-time) testing from a camera: tracking, recording, procedures, pauses, start conditions and live
statistics.  :class:`ObservationSession` is the camera-less variant (a clock and scoring keys)."""

from __future__ import annotations

import datetime as _dt
import inspect
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .apparatus import Apparatus
from .geometry import body_fraction_inside
from .measures import AnalysisSettings
from .procedures import Outputs, ProcedureEngine
from .track import Track
from .tracking import ArenaTracker, Detection, DetectionSettings, postprocess, to_gray
from .video import VideoRecorder

START_MODES = ("immediate", "on_detection", "experimenter_leaves", "manual")


def make_engine(procedures, outputs: Outputs | None = None, devices=None, on_mark=None, on_end=None, on_log=None,
                variables: dict | None = None, context: dict | None = None, **callbacks):
    """A procedure engine through the new contract (devices / variables / context) when available, else the old
    rules + Outputs API."""
    try:
        params = inspect.signature(ProcedureEngine).parameters
    except (TypeError, ValueError):
        params = {}
    if "devices" in params:
        # the legacy serial-port Outputs is accepted in place of a DeviceManager by the new engine
        extra = {k: v for k, v in callbacks.items() if k in params and v is not None}  # on_pause / on_stimulus …
        return ProcedureEngine(procedures, devices=devices if devices is not None else outputs, on_mark=on_mark,
                               on_end=on_end, on_log=on_log, variables=variables if variables is not None else {},
                               context=context or {}, **extra)
    return ProcedureEngine(procedures, outputs or Outputs(), on_mark=on_mark, on_end=on_end)


def open_devices(project):
    """A DeviceManager for the project's I/O devices, or None (no devices / module not available / failure)."""
    if project is None or not getattr(project, "io_devices", None):
        return None
    try:
        from . import iodevices
        dm = iodevices.DeviceManager
    except Exception:
        return None
    for build in (lambda: dm.from_project(project), lambda: dm(project.io_devices)):
        try:
            return build()
        except Exception:
            continue
    return None


# ====================================================================== live zone occupancy
class LiveOccupancy:
    """Frame-by-frame zone occupancy with the rules used by the analysis (measures.occupancy): each zone's entry
    rule (centre / head / tail / body proportion with hysteresis / exclusion), investigation distance, hidden zones
    (an animal lost near a hidden zone is in it) and zone groups.  While the animal is not detected the last
    position is kept."""

    def __init__(self, apparatus: Apparatus | None, analysis: AnalysisSettings):
        self.app, self.s = apparatus, analysis
        self._body_state: dict[str, bool] = {}
        self._last: Detection | None = None
        self._hidden: str | None = None  # hidden zone of the current lost episode
        self._lost = False

    def _pos(self, d: Detection, part: str):
        if part == "head" and math.isfinite(d.hx):
            return np.array([d.hx]), np.array([d.hy])
        if part == "tail" and math.isfinite(d.tx):
            return np.array([d.tx]), np.array([d.ty])
        return np.array([d.x]), np.array([d.y])

    def _axes(self, d: Detection):
        L = math.hypot(d.hx - d.tx, d.hy - d.ty) if math.isfinite(d.hx) and math.isfinite(d.tx) else math.nan
        area = d.area if d.area and math.isfinite(d.area) else math.nan
        a = L / 2 if math.isfinite(L) and L > 0 else math.sqrt(2 * area / math.pi) if math.isfinite(area) else math.nan
        b = min(area / (math.pi * a), a) if math.isfinite(area) and a > 0 else a / 2
        return a, b, d.angle if math.isfinite(d.angle) else 0.0

    def update(self, d: Detection) -> tuple[dict[str, bool], dict[str, bool]]:
        """(zone and group membership, head membership) for this frame."""
        app = self.app
        if app is None:
            return {}, {}
        if d.detected:
            self._last, self._lost, self._hidden = d, False, None
        L = self._last
        if L is None:
            return {}, {}
        s = self.s
        zones: dict[str, bool] = {}
        excl = []
        for z in app.zones:
            rule = z.entry_rule or s.zone_body_part or "centre"
            if z.investigation_distance_cm and z.investigation_distance_cm > 0:
                hx, hy = self._pos(L, "head")
                d_px = z.investigation_distance_cm / app.scale
                zones[z.name] = bool(z.shape.contains(hx, hy)[0]) or \
                    bool(z.shape.distance_to_edge(hx, hy)[0] <= d_px)
            elif rule == "body":
                a, b, ang = self._axes(L)
                frac = float(body_fraction_inside(z.shape, [L.x], [L.y], [ang], [a], [b])[0])
                enter = float(min(max(z.body_fraction if z.entry_rule == "body" else s.body_proportion_pct / 100.0,
                                      0.01), 1.0))
                was = self._body_state.get(z.name, False)
                if math.isfinite(frac):
                    was = frac >= enter if not was else (frac >= min(enter, 1.0 - enter) and frac > 0)
                self._body_state[z.name] = was
                zones[z.name] = was
            elif rule == "exclusion":
                excl.append(z)
                zones[z.name] = bool(z.shape.contains(*self._pos(L, "centre"))[0])
            else:
                zones[z.name] = bool(z.shape.contains(*self._pos(L, rule))[0])
        for z in excl:
            for o in app.zones:
                if o is not z and o not in excl and not o.hidden and o.shape.area() < z.shape.area() and zones[o.name]:
                    zones[z.name] = False
        hidden = self._hidden_zone(d)
        if hidden is not None:
            zones = {k: k == hidden for k in zones}
        memb = {k: bool(np.asarray(v).ravel()[0]) for k, v in
                app.combine_groups({k: np.array([v]) for k, v in zones.items()}, (1,)).items()}
        head = {}
        if math.isfinite(L.hx):
            hx, hy = self._pos(L, "head")
            hz = {z.name: (z.name == hidden) if hidden is not None else bool(z.shape.contains(hx, hy)[0])
                  for z in app.zones}
            head = {k: bool(np.asarray(v).ravel()[0]) for k, v in
                    app.combine_groups({k: np.array([v]) for k, v in hz.items()}, (1,)).items()}
        return memb, head

    def _hidden_zone(self, d: Detection) -> str | None:
        if d.detected or self._last is None:
            return None
        if not self._lost:  # new lost episode: the nearest hidden zone around the last seen position
            self._lost = True
            best = None
            x, y = np.array([self._last.x]), np.array([self._last.y])
            for z in self.app.zones:
                if not z.hidden:
                    continue
                margin = (self.s.hidden_zone_margin / self.app.scale if self.s.hidden_zone_margin
                          and self.s.hidden_zone_margin > 0 else 0.5 * math.sqrt(max(z.shape.area(), 1.0)))
                dist = 0.0 if bool(z.shape.contains(x, y)[0]) else float(z.shape.distance_to_edge(x, y)[0])
                if dist <= margin and (best is None or dist < best[0]):
                    best = (dist, z.name)
            self._hidden = best[1] if best else None
        return self._hidden


# ====================================================================== live statistics
class LiveStats:
    """Running per-zone time / entries / latency, distance, speed, immobility and a short history for charts."""

    CHART_PARAMS = {"speed": "Speed", "distance": "Distance", "motion": "Motion (% body)", "detected": "Detected",
                    "freezing": "Freezing"}

    def __init__(self, apparatus: Apparatus | None, fps: float, analysis: AnalysisSettings,
                 history_s: float = 300.0, sample_s: float = 0.1):
        self.scale = apparatus.scale if apparatus else 1.0
        self.unit = apparatus.unit if apparatus else "px"
        self.names = ([z.name for z in apparatus.zones] + [g.name for g in apparatus.groups]) if apparatus else []
        self.a = analysis
        self.fps = fps or 25.0
        self.zone_time = dict.fromkeys(self.names, 0.0)
        self.entries = dict.fromkeys(self.names, 0)
        self.latency: dict[str, float | None] = dict.fromkeys(self.names, None)
        self.inside = dict.fromkeys(self.names, False)
        self.distance = 0.0
        self.speed = 0.0
        self.motion_pct = 0.0
        self.detected = False
        self.freezing = False
        self.immobile = False
        self.sample_s = sample_s
        self.history: deque = deque(maxlen=int(history_s / sample_s) + 1)  # (t, speed, distance, motion, det, frz)
        self._last_t: float | None = None
        self._last_hist = -1e9
        self._xy = None
        self._slow_since: float | None = None
        self._first = True

    def update(self, t: float, d: Detection, zones: dict[str, bool], freezing: bool):
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        self.detected = bool(d.detected)
        if zones:
            for n in self.names:
                now = bool(zones.get(n, False))
                if now and not self.inside[n] and (not self._first or self.a.count_initial_entry):
                    self.entries[n] += 1
                    if self.latency[n] is None:
                        self.latency[n] = t
                self.inside[n] = now
            self._first = False
        if d.detected:
            # exponential smoothing ≈ the analysis speed-smoothing window, so jitter is not counted
            n_win = max(1.0, self.a.speed_smoothing_s * self.fps)
            alpha = 2.0 / (n_win + 1.0)
            if self._xy is None:
                self._xy = (d.x, d.y)
                step = 0.0
            else:
                lx, ly = self._xy
                nx, ny = lx + alpha * (d.x - lx), ly + alpha * (d.y - ly)
                step = math.hypot(nx - lx, ny - ly) * self.scale
                self._xy = (nx, ny)
            self.distance += step
            if dt > 0:
                self.speed += alpha * (step / dt - self.speed)
            if d.area and not math.isnan(d.motion):
                self.motion_pct = d.motion / max(d.area, 1) * 100
        for n in self.names:
            if self.inside[n]:
                self.zone_time[n] += dt
        self.freezing = bool(freezing)
        if self.detected and self.speed < self.a.mobility_threshold:
            if self._slow_since is None:
                self._slow_since = t
            self.immobile = t - self._slow_since >= self.a.min_immobile_s
        else:
            self._slow_since = None
            self.immobile = False
        if t - self._last_hist >= self.sample_s:
            self._last_hist = t
            self.history.append((t, self.speed, self.distance, self.motion_pct, float(self.detected),
                                 float(self.freezing)))

    def current_zones(self) -> list[str]:
        return [n for n in self.names if self.inside[n]]

    def rows(self) -> list[tuple[str, float, int, float | None]]:
        """(zone, time s, entries, latency s or None) for every zone and zone group."""
        return [(n, self.zone_time[n], self.entries[n], self.latency[n]) for n in self.names]

    def series(self, param: str, window_s: float = 60.0) -> tuple[np.ndarray, np.ndarray]:
        col = {"speed": 1, "distance": 2, "motion": 3, "detected": 4, "freezing": 5}.get(param, 1)
        if not self.history:
            return np.zeros(0), np.zeros(0)
        h = list(self.history)
        t_end = h[-1][0]
        h = [r for r in h if r[0] >= t_end - window_s]
        return np.array([r[0] for r in h]), np.array([r[col] for r in h])


# ====================================================================== scoring shared by both session kinds
class _Scoring:
    def _init_scoring(self):
        self.events: list[dict] = []
        self.open_states: dict[str, dict] = {}
        self.pauses: list[list[float]] = []
        self.pause_log: list[dict] = []  # {"t": test time, "duration_s": real pause length}

    def score(self, behaviour: str, kind: str = "point", t: float | None = None) -> dict | None:
        """Score a behaviour now: a point event, or start / end of a state event. Returns the event."""
        with self.lock:
            if self.state != "running":
                return None
            t = round(self.elapsed if t is None else t, 3)
            if kind == "state":
                ev = self.open_states.pop(behaviour, None)
                if ev is not None:
                    ev["t_end"] = t
                    return ev
                ev = {"behaviour": behaviour, "t": t, "t_end": None}
                self.open_states[behaviour] = ev
            else:
                ev = {"behaviour": behaviour, "t": t, "t_end": None}
            self.events.append(ev)
            engine = getattr(self, "engine", None)
            if engine is not None and hasattr(engine, "mark_event"):
                try:  # procedures can react to scored behaviours ("event marked")
                    engine.mark_event(t, behaviour)
                except Exception as e:
                    self.warn(f"Procedure error: {e}")
            return ev

    def _close_states(self):
        el = self.elapsed
        for ev in self.open_states.values():
            ev["t_end"] = round(el, 3)
        self.open_states = {}

    def recent_labels(self, window_s: float = 2.0) -> list[str]:
        """Labels of events that happened in the last window_s seconds and of open state events."""
        el = self.elapsed
        out = [e["behaviour"] for e in self.events if e["t_end"] is None and e["behaviour"] not in self.open_states
               and 0 <= el - e["t"] <= window_s]
        return out + [f"{n} …" for n in self.open_states]


# ====================================================================== camera session
@dataclass
class LiveSession(_Scoring):
    """Feed frames one by one with :meth:`process`.

    States: "waiting" (armed, waiting for the start condition) -> "running" <-> "paused" -> "finished".
    start_mode: "immediate" | "on_detection" (animal detected inside the arena for start_hold_s) |
    "experimenter_leaves" (a large object — the experimenter's hand — appears and leaves, then the animal is
    detected for start_hold_s) | "manual" (keyboard / remote / scheduled: call :meth:`request_start`).
    While paused the test clock stops: no track rows, no recording, no procedure updates.
    """

    apparatus: Apparatus
    settings: DetectionSettings
    duration_s: float = 300.0
    start_mode: str = "immediate"
    start_hold_s: float = 0.5
    procedures: list = field(default_factory=list)
    outputs: Outputs | None = None
    record_path: str | None = None
    fps: float = 25.0
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    devices: object = None  # core.iodevices.DeviceManager (new procedure engine)
    variables: dict | None = None  # procedure variables shared between tests (project.variables)
    record_overlay: bool = False  # burn the test time and event labels into the recording
    experimenter_area_px: int = 0  # foreground area counted as the experimenter; 0 = auto
    lost_warning_s: float = 3.0  # warn when the animal is not detected for this long (0 = never)
    name: str = ""
    zone_overrides: dict | None = None  # moveable zones of this test (Test.zone_overrides)
    on_stimulus: object = None  # touch-screen window handler (gui.touchscreen.TouchStimulusWindow.handle)

    def __post_init__(self):
        if self.zone_overrides and self.apparatus is not None:
            self.apparatus = self.apparatus.with_overrides(self.zone_overrides)
        self.lock = threading.RLock()
        self.tracker: ArenaTracker | None = None
        self.state = "waiting"
        self.t0: float | None = None
        self.cols = {c: [] for c in ("t", "x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle", "detected")}
        self._init_scoring()
        self.recorder: VideoRecorder | None = None
        self.log: list[tuple[float, str]] = []
        self.warnings: list[tuple[float, str]] = []
        names = ([z.name for z in self.apparatus.zones] + [g.name for g in self.apparatus.groups]
                 if self.apparatus else [])
        ctx = {"zones": names, "points": [p.name for p in self.apparatus.points] if self.apparatus else [],
               "keys": []}
        self.engine = make_engine(self.procedures, self.outputs, self.devices, on_mark=self._mark,
                                  on_end=self._engine_end, on_log=self._engine_log, variables=self.variables,
                                  context=ctx, on_pause=lambda t: self.pause(), on_resume=lambda t: self.resume(),
                                  on_stimulus=self.on_stimulus)
        if self.outputs is None:
            self.outputs = getattr(self.engine, "outputs", None)
        self.stats = LiveStats(self.apparatus, self.fps, self.analysis)
        self.occupancy = LiveOccupancy(self.apparatus, self.analysis)
        self.start_phase = ""  # experimenter_leaves: "experimenter" -> "leaving" -> "animal"
        self._detect_since: float | None = None
        self._frame_i = 0
        self._last_dets: list[Detection] = []
        self._still_since: float | None = None
        self._motion_hist: list[float] = []
        self._start_requested = False
        self._resume_pending = False
        self._pause_ts: float | None = None
        self._last_ts: float | None = None
        self._lost_since: float | None = None
        self._lost_warned = False
        self._errors_seen = 0
        self._band: np.ndarray | None = None
        self._arena_px = 0
        self.frames_dropped = 0
        self.last_fg: np.ndarray | None = None
        self.started_wall: float | None = None

    # ------------------------------------------------------------------
    def set_background(self, frame: np.ndarray):
        self._ensure_tracker(frame)
        self.tracker.set_background(to_gray(frame))

    def _ensure_tracker(self, frame):
        if self.tracker is None:
            h, w = frame.shape[:2]
            mask = self.apparatus.arena_or_bounds().mask((h, w)) if self.apparatus else None
            self.tracker = ArenaTracker(self.settings, mask)

    def _mark(self, name: str, t: float):
        self.events.append({"behaviour": name, "t": t, "t_end": None})

    def _engine_end(self):
        self.finish()

    def _engine_log(self, msg: str, t: float | None = None):
        self.log.append((self.elapsed if t is None else t, str(msg)))

    def warn(self, msg: str, t: float | None = None):
        self.warnings.append((round(self.elapsed if t is None else t, 2), msg))

    @property
    def elapsed(self) -> float:
        n = len(self.cols["t"])
        return self.cols["t"][-1] if n else 0.0

    @property
    def running(self) -> bool:
        return self.state == "running"

    # ------------------------------------------------------------------ control (any thread)
    def request_start(self):
        """Start on the next frame (keyboard / remote / scheduled / collective start)."""
        with self.lock:
            if self.state == "waiting":
                self._start_requested = True

    def pause(self) -> bool:
        with self.lock:
            if self.state != "running":
                return False
            self.state = "paused"
            self._pause_ts = self._last_ts
            self._pause_t = self.elapsed
            self._pause_wall = time.monotonic()
            self._notify_engine("pause", self._pause_t)
            return True

    def _notify_engine(self, what: str, t: float):
        fn = getattr(self.engine, what, None)
        if fn is not None:  # procedures see "test paused / resumed"; the engine's own pause action calls back here
            try:
                fn(t)
            except Exception as e:
                self.warn(f"Procedure error: {e}")

    def resume(self) -> bool:
        with self.lock:
            if self.state != "paused":
                return False
            self.state = "running"
            self._resume_pending = True
            self._notify_engine("resume", self._pause_t)
            return True

    def key(self, key: str, down: bool = True):
        """Forward a key press to the procedure engine (new API)."""
        with self.lock:
            if self.state == "running" and hasattr(self.engine, "key"):
                try:
                    self.engine.key(self.elapsed, key, down=down)
                except Exception as e:
                    self.warn(f"Procedure error: {e}")

    # ------------------------------------------------------------------ frames (grabber thread)
    def process(self, frame: np.ndarray, timestamp: float | None = None) -> list[Detection]:
        """Process one frame. timestamp defaults to frame_index / fps."""
        with self.lock:
            return self._process(frame, timestamp)

    def _process(self, frame, timestamp):
        self._ensure_tracker(frame)
        self._frame_shape = frame.shape[:2]
        ts = timestamp if timestamp is not None else self._frame_i / self.fps
        self._frame_i += 1
        dets, fg = self.tracker.process(frame)
        self._last_dets = dets
        self.last_fg = fg
        prev_ts, self._last_ts = self._last_ts, ts
        if self.state == "finished":
            return dets
        if self.state == "waiting":
            self._check_start(ts, dets, fg)
            if self.state != "running":
                return dets
        if self.state == "paused":
            return dets
        if self._resume_pending:
            self._resume_pending = False
            gap = (ts - self._pause_ts) if self._pause_ts is not None else 0.0
            self.t0 = ts - (self.elapsed + 1.0 / self.fps)
            real = time.monotonic() - self._pause_wall
            self.pauses.append([round(self._pause_t, 3), round(self._pause_t, 3)])
            self.pause_log.append({"t": round(self._pause_t, 3), "duration_s": round(max(gap, real), 3)})
        elif prev_ts is not None and self.cols["t"] and ts - prev_ts > 2.5 / self.fps:
            n = int(round((ts - prev_ts) * self.fps)) - 1
            self.frames_dropped += n
            self.warn(f"{n} frame{'s' if n > 1 else ''} dropped")
        t = ts - self.t0
        d = dets[0] if dets else Detection()
        self.cols["t"].append(t)
        for c in ("x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle"):
            self.cols[c].append(getattr(d, c))
        self.cols["detected"].append(d.detected)
        zones, head_zones = self.occupancy.update(d)
        freezing = self._freezing_now(d)
        self.stats.update(t, d, zones, freezing)
        self._check_lost(t, d.detected)
        if self.recorder is not None:
            self._record(frame, t)
        self._update_engine(t, d, zones, head_zones, freezing)
        if self.duration_s and t >= self.duration_s:
            self.finish()
        return dets

    def _check_start(self, ts, dets, fg):
        detected = any(d.detected for d in dets)
        mode = self.start_mode
        if self._start_requested or mode == "immediate":
            self._start(ts)
            return
        if mode == "experimenter_leaves":
            intruder = self._intruder(fg)
            if intruder:
                self.start_phase = "leaving"
                self._detect_since = None
                return
            if self.start_phase == "leaving":
                self.start_phase = "animal"
            if self.start_phase != "animal":
                self.start_phase = "experimenter"
                return
        elif mode != "on_detection":
            return
        if detected:
            if self._detect_since is None:
                self._detect_since = ts
            if ts - self._detect_since >= self.start_hold_s:
                self._start(ts)
        else:
            self._detect_since = None

    def _intruder(self, fg: np.ndarray | None) -> bool:
        """A large foreground object in the arena (the experimenter's hand / arm)."""
        if fg is None:
            return False
        if self._band is None:
            m = self.tracker.mask if self.tracker is not None and self.tracker.mask is not None else \
                np.full(fg.shape, 255, np.uint8)
            self._arena_px = int(cv2.countNonZero(m))
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
            self._band = cv2.subtract(m, cv2.erode(m, k))
        thr = self.experimenter_area_px or self.settings.max_area_px or int(0.08 * max(self._arena_px, 1))
        area = int(cv2.countNonZero(fg))
        if area > thr:
            return True
        return area > thr / 2 and cv2.countNonZero(cv2.bitwise_and(fg, self._band)) > max(self.settings.min_area_px,
                                                                                          area // 4)

    def _check_lost(self, t, detected):
        if detected:
            self._lost_since = None
            self._lost_warned = False
            return
        if self._lost_since is None:
            self._lost_since = t
        if self.lost_warning_s and not self._lost_warned and t - self._lost_since >= self.lost_warning_s:
            self._lost_warned = True
            self.warn(f"Animal lost for {self.lost_warning_s:g} s", t)

    def _record(self, frame, t):
        try:
            self.recorder.write(annotate_recording(frame, t, self.recent_labels()) if self.record_overlay
                                else frame)
        except Exception as e:
            self.warn(f"Recording stopped: {e}", t)
            try:
                self.recorder.close()
            except Exception:
                pass
            self.recorder = None

    def _update_engine(self, t, d, zones, head_zones, freezing):
        eng = self.engine
        try:
            if hasattr(eng, "update_state"):
                eng.update_state(t, {"zones": zones, "head_zones": head_zones, "detected": bool(d.detected),
                                     "freezing": freezing, "immobile": self.stats.immobile,
                                     "x": float(d.x), "y": float(d.y), "speed": self.stats.speed,
                                     "distance": self.stats.distance})
            else:
                eng.update(t, zones, detected=d.detected, freezing=freezing, immobile=freezing)
        except Exception as e:
            self.warn(f"Procedure error: {type(e).__name__}: {e}", t)
        errs = getattr(eng, "errors", None) or []
        for msg in errs[self._errors_seen:]:
            self.warn(f"Procedure error: {msg}", t)
        self._errors_seen = len(errs)

    def _freezing_now(self, d: Detection) -> bool:
        if not d.detected or not d.area or math.isnan(d.motion):
            return False
        pct = d.motion / max(d.area, 1) * 100
        self._motion_hist.append(pct)
        t = self.cols["t"][-1]
        if pct < self.analysis.freeze_on_pct:
            if self._still_since is None:
                self._still_since = t
            return t - self._still_since >= self.analysis.min_freeze_s
        self._still_since = None
        return False

    def _start(self, ts):
        self.state = "running"
        self.start_phase = ""
        self.t0 = ts
        if self.record_path:
            h, w = self._frame_shape
            try:
                self.recorder = VideoRecorder(self.record_path, self.fps, (w, h))
            except Exception as e:
                self.recorder = None
                self.warn(f"Cannot record: {e}", 0.0)
        try:
            self.engine.start(0.0)
        except Exception as e:
            self.warn(f"Procedure error: {e}", 0.0)
        self.started_wall = time.time()

    def finish(self):
        with self.lock:
            if self.state == "finished":
                return
            if self.state == "paused":
                self.pauses.append([round(self._pause_t, 3), round(self._pause_t, 3)])
                self.pause_log.append({"t": round(self._pause_t, 3),
                                       "duration_s": round(time.monotonic() - self._pause_wall, 3)})
            if self.state in ("running", "paused"):
                try:
                    self.engine.stop(self.elapsed)
                except Exception as e:
                    self.warn(f"Procedure error: {e}")
            self._close_states()
            for m in getattr(self.engine, "state_events", None) or []:  # "mark start / end" actions
                self.events.append({"behaviour": m["behaviour"], "t": m["t"],
                                    "t_end": m["t_end"] if m["t_end"] is not None else round(self.elapsed, 3)})
            self.state = "finished"
            if self.recorder is not None:
                try:
                    self.recorder.close()
                except Exception as e:
                    self.warn(f"Recording error: {e}")
                self.recorder = None

    @property
    def io_events(self) -> list:
        return list(getattr(self.engine, "io_events", None) or [])

    @property
    def result_variables(self) -> dict:
        return dict(getattr(self.engine, "result_variables", None) or {})

    def track(self) -> Track:
        tr = Track(**{c: np.asarray(v) for c, v in self.cols.items()}, fps=self.fps)
        tr.meta["source"] = "live"
        return postprocess(tr, self.settings)


def annotate_recording(frame: np.ndarray, t: float, labels: list[str] = ()) -> np.ndarray:
    """A copy of the frame with the test time, the wall-clock time and recent event labels burned in."""
    img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    m, s = divmod(max(0.0, t), 60)
    lines = [f"{int(m):02d}:{s:05.2f}  {_dt.datetime.now():%H:%M:%S}"] + list(labels)[-3:]
    scale = max(0.35, img.shape[1] / 1400)
    th = max(1, int(round(scale * 1.5)))
    y = img.shape[0] - 8
    for txt in reversed(lines):
        (tw, tht), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
        cv2.rectangle(img, (4, y - tht - base - 2), (12 + tw, y + 2), (0, 0, 0), -1)
        cv2.putText(img, txt, (8, y - base), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), th, cv2.LINE_AA)
        y -= tht + base + 6
    return img


# ====================================================================== observation only (no camera)
class ObservationSession(_Scoring):
    """Live observation without a camera (TakeNote): a test clock and scoring keys, saved as ``test.events``.

    Call :meth:`tick` regularly (the GUI does it with a timer); ``clock`` is injectable for tests.
    """

    def __init__(self, duration_s: float = 0.0, start_mode: str = "manual", clock=time.monotonic, name: str = ""):
        self.lock = threading.RLock()
        self.duration_s = duration_s
        self.start_mode = start_mode
        self.clock = clock
        self.name = name
        self.state = "waiting"
        self._init_scoring()
        self.warnings: list[tuple[float, str]] = []
        self.log: list[tuple[float, str]] = []
        self.stats = None
        self.apparatus = None
        self._t0: float | None = None
        self._paused_total = 0.0
        self._pause_at: float | None = None
        self._frozen: float | None = None
        if start_mode == "immediate":
            self.start()

    @property
    def elapsed(self) -> float:
        if self._frozen is not None:
            return self._frozen
        if self._t0 is None:
            return 0.0
        now = self._pause_at if self._pause_at is not None else self.clock()
        return max(0.0, now - self._t0 - self._paused_total)

    def request_start(self):
        self.start()

    def start(self):
        with self.lock:
            if self.state == "waiting":
                self.state = "running"
                self._t0 = self.clock()

    def pause(self) -> bool:
        with self.lock:
            if self.state != "running":
                return False
            self._pause_at = self.clock()
            self.state = "paused"
            return True

    def resume(self) -> bool:
        with self.lock:
            if self.state != "paused":
                return False
            t = round(self.elapsed, 3)
            d = self.clock() - self._pause_at
            self._paused_total += d
            self._pause_at = None
            self.pauses.append([t, t])
            self.pause_log.append({"t": t, "duration_s": round(d, 3)})
            self.state = "running"
            return True

    def key(self, key: str, down: bool = True):
        pass

    def tick(self):
        with self.lock:
            if self.state == "running" and self.duration_s and self.elapsed >= self.duration_s:
                self._frozen = float(self.duration_s)
                self.finish()

    def finish(self):
        with self.lock:
            if self.state == "finished":
                return
            if self.state == "paused":
                self.resume()
            if self._frozen is None:
                self._frozen = self.elapsed
            self._close_states()
            self.state = "finished"

    @property
    def io_events(self) -> list:
        return []

    @property
    def result_variables(self) -> dict:
        return {}

    def track(self):
        return None
