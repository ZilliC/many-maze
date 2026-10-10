"""Live (real-time) testing from a camera: tracking, recording, procedures, pauses, start conditions and live
statistics.  :class:`ObservationSession` is the camera-less variant (a clock and scoring keys)."""

from __future__ import annotations

import copy
import datetime as _dt
import errno
import math
import queue
import threading
import time
import weakref
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .apparatus import CALIBRATION_KEY, POSITION_KEY, Apparatus, calibration_override, position_args
from .autosave import Autosaver
from .diskspace import free_mb as disk_free_mb, is_disk_full
from .freezing import LiveThresholds
from .geometry import body_fraction_inside
from .iolog import LiveIOLog
from .livemonitor import LivePoints
from .measures import AnalysisSettings
from .procedures import Outputs, ProcedureEngine
from .session import END_DURATION, END_PROCEDURE, END_USER, Session
from .track import Track
from .tracking import ArenaTracker, Detection, DetectionSettings, TrackBuilder, postprocess, to_gray
from .video import SplitRecorder, VideoRecorder

START_MODES = ("immediate", "on_detection", "experimenter_leaves", "manual")


def open_devices(project):
    """A DeviceManager for the project's I/O devices, or None (no devices / failure)."""
    if project is None or not project.io_devices:
        return None
    try:
        from .iodevices import DeviceManager

        return DeviceManager.from_project(project)
    except Exception:
        return None


class _RecordingThread:
    """Encodes a recording in its own thread, so the frame thread (holding the session lock) never waits for the
    encoder.  The queue is bounded: a stalled encoder slows the frame thread down rather than filling the memory.
    Items are (frame, count): padding after a capture gap is one item repeated by the encoder, not count frames.
    A frame that cannot be queued within PUT_TIMEOUT_S (stalled disk or encoder) is a recording error: the frame
    thread, which holds the session lock, never waits longer."""

    CLOSE_TIMEOUT_S = 30.0
    PUT_TIMEOUT_S = 2.0

    def __init__(self, recorder: VideoRecorder, maxsize: int = 64):
        self.recorder = recorder
        self.error: Exception | None = None
        self._queue: queue.Queue = queue.Queue(maxsize)
        self._closed = threading.Event()  # set by close(): the encoder ends once the queue is empty
        self._thread = threading.Thread(target=self._run, name="live-recorder", daemon=True)
        self._thread.start()

    def write(self, frame: np.ndarray):
        self.repeat(frame, 1)

    def repeat(self, frame: np.ndarray, count: int):
        """Write the frame count times (the encoder repeats it)."""
        if self.error is not None:
            raise self.error
        if count > 0:
            try:
                self._queue.put((frame, int(count)), timeout=self.PUT_TIMEOUT_S)
            except queue.Full:
                raise OSError(errno.EIO, f"the video encoder did not take a frame within {self.PUT_TIMEOUT_S:g} s "
                                         "(disk or encoder stalled)") from None

    def close(self, timeout: float | None = None):
        """Encode what is queued, close the file; raises the encoder's error, if any, or TimeoutError when the
        encoder has not finished within timeout (it goes on and closes the file in the background)."""
        timeout = self.CLOSE_TIMEOUT_S if timeout is None else timeout
        deadline = time.monotonic() + timeout
        self._closed.set()
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:  # the encoder sees the flag once it has emptied the queue
            pass
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            raise TimeoutError(f"the video encoder did not finish within {timeout:g} s")
        if self.error is not None:
            raise self.error

    def _run(self):
        while True:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._closed.is_set():
                    break
                continue
            if item is None:
                break
            if self.error is None:
                frame, count = item
                try:
                    for _ in range(count):
                        self.recorder.write(frame)
                except Exception as e:
                    self.error = e
        try:
            self.recorder.close()
        except Exception as e:
            self.error = self.error or e


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
        self._oriented: dict[str, bool] = {}  # zones entered with the orientation rule (entry_orientation_deg)
        self._angle = math.nan  # last known body orientation
        # for the procedures: zones investigated, angle to each zone / point, events (hidden-zone partial exits)
        self.investigating: dict[str, bool] = {}
        self.orientation: dict[str, float] = {}
        self.events: list[tuple[str, dict]] = []
        self._peek: tuple[str, float] | None = None  # (hidden zone, time seen again) of a possible partial exit
        self._prev_hidden: str | None = None

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

    def update(self, d: Detection, t: float | None = None) -> tuple[dict[str, bool], dict[str, bool]]:
        """(zone and group membership, head membership) for this frame; also updates :attr:`investigating`,
        :attr:`orientation` and :attr:`events` (t: the test time, for the events)."""
        memb, head = self._update(d)
        self.events = []
        if self.app is not None and self._last is not None:
            self._procedure_state(d, t)
        return memb, head

    def _procedure_state(self, d: Detection, t: float | None):
        """Investigation (head in the zone, or within its investigation distance while facing it, as the
        investigation measures), the angle between the body's orientation and the direction to each zone centre
        and point, and hidden-zone partial exits (seen near the hidden zone between two times hidden in it)."""
        app, L, s = self.app, self._last, self.s
        ang = self._angle
        hx, hy = self._pos(L, "head")
        hidden = self._hidden
        for z in app.zones:
            if hidden is not None or not d.detected:
                inv = False
            else:
                inside = bool(z.shape.contains(hx, hy)[0])
                inv = inside
                if not inside and z.investigation_distance_cm and z.investigation_distance_cm > 0:
                    near = float(z.shape.distance_to_edge(hx, hy)[0]) <= z.investigation_distance_cm / app.scale
                    if near and math.isfinite(ang):
                        zx, zy = z.shape.centroid()
                        brg = math.degrees(math.atan2(zy - float(hy[0]), zx - float(hx[0])))
                        near = abs((ang - brg + 180.0) % 360.0 - 180.0) <= s.exploration_facing_deg
                    inv = near
            self.investigating[z.name] = inv
        targets = [(z.name, z.shape.centroid()) for z in app.zones] + [(p.name, (p.x, p.y)) for p in app.points]
        self.orientation = {}
        for name, (px, py) in targets:
            if math.isfinite(ang) and math.isfinite(L.x):
                brg = math.degrees(math.atan2(py - L.y, px - L.x))
                self.orientation[name] = abs((ang - brg + 180.0) % 360.0 - 180.0)
        # partial exits: seen again near the hidden zone, then hidden in it again
        if d.detected:
            if self._prev_hidden is not None:
                self._peek = (self._prev_hidden, t if t is not None else 0.0)
            if self._peek is not None:
                z = app.zone(self._peek[0])
                if z is None or not self._near_hidden(z, L):
                    self._peek = None
        elif self._prev_hidden is None and self._peek is not None:  # lost again
            if self._peek[0] == hidden:
                dur = (t - self._peek[1]) if t is not None else 0.0
                self.events.append(("hidden_partial_exit", {"zone": hidden, "value": round(dur, 3)}))
            self._peek = None
        self._prev_hidden = hidden

    def _hidden_margin(self, z) -> float:
        return (self.s.hidden_zone_margin / self.app.scale if self.s.hidden_zone_margin
                and self.s.hidden_zone_margin > 0 else 0.5 * math.sqrt(max(z.shape.area(), 1.0)))

    def _near_hidden(self, z, L: Detection) -> bool:
        x, y = np.array([L.x]), np.array([L.y])
        return bool(z.shape.contains(x, y)[0]) or float(z.shape.distance_to_edge(x, y)[0]) <= self._hidden_margin(z)

    def _update(self, d: Detection) -> tuple[dict[str, bool], dict[str, bool]]:
        app = self.app
        if app is None:
            return {}, {}
        if d.detected:
            self._last, self._lost, self._hidden = d, False, None
        L = self._last
        if L is None:
            return {}, {}
        s = self.s
        if math.isfinite(L.angle):
            self._angle = L.angle
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
            if z.entry_orientation_deg and z.entry_orientation_deg > 0:
                zones[z.name] = self._orientation_rule(z, zones[z.name], L)
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

    def _orientation_rule(self, z, inside: bool, L: Detection) -> bool:
        """A visit starts once the animal faces the zone (as occupancy.oriented_visits)."""
        was = self._oriented.get(z.name, False)
        if not inside:
            now = False
        elif was or not math.isfinite(self._angle):
            now = True
        else:
            zx, zy = z.shape.centroid()
            brg = math.degrees(math.atan2(zy - L.y, zx - L.x))
            now = abs((self._angle - brg + 180.0) % 360.0 - 180.0) <= z.entry_orientation_deg
        self._oriented[z.name] = now
        return now

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
                margin = self._hidden_margin(z)
                dist = 0.0 if bool(z.shape.contains(x, y)[0]) else float(z.shape.distance_to_edge(x, y)[0])
                if dist <= margin and (best is None or dist < best[0]):
                    best = (dist, z.name)
            self._hidden = best[1] if best else None
        return self._hidden


# ====================================================================== live rearing
class LiveRearing:
    """Rearing frame by frame with the rules of the rearing measures (measures.rearing_mask): the body area falls
    below rear_area_pct % of the animal's usual area and, with head and tail tracked, the body length below
    rear_length_pct % of its usual length; rears shorter than min_rear_s are ignored and gaps up to 0.2 s inside
    a rear are bridged. The usual area / length is the median over the last 30 s of frames that are not rears."""

    BASELINE_S = 30.0
    MIN_BASELINE_S = 1.0
    GAP_S = 0.2

    def __init__(self, analysis: AnalysisSettings, fps: float):
        self.s = analysis
        n = max(10, int(self.BASELINE_S * (fps or 25.0)))
        self._areas: deque = deque(maxlen=n)
        self._lengths: deque = deque(maxlen=n)
        self._fps = fps or 25.0
        self.rearing = False
        self._cand_since: float | None = None
        self._off_since: float | None = None

    def update(self, t: float, d: Detection) -> bool:
        cand = False
        if d.detected and d.area and math.isfinite(d.area):
            L = math.hypot(d.hx - d.tx, d.hy - d.ty) if math.isfinite(d.hx) and math.isfinite(d.tx) else math.nan
            if len(self._areas) >= self.MIN_BASELINE_S * self._fps:
                base_a = float(np.median(self._areas))
                cand = d.area < base_a * self.s.rear_area_pct / 100.0
                if cand and math.isfinite(L) and len(self._lengths) >= self.MIN_BASELINE_S * self._fps:
                    cand = L < float(np.median(self._lengths)) * self.s.rear_length_pct / 100.0
            if not cand and not self.rearing:
                self._areas.append(float(d.area))
                if math.isfinite(L):
                    self._lengths.append(L)
        if cand:
            self._off_since = None
            if self._cand_since is None:
                self._cand_since = t
            if not self.rearing and t - self._cand_since + 1e-9 >= self.s.min_rear_s:
                self.rearing = True
        else:
            self._cand_since = None if not self.rearing else self._cand_since
            if self.rearing:
                if self._off_since is None:
                    self._off_since = t
                if t - self._off_since >= self.GAP_S:
                    self.rearing = False
                    self._cand_since = self._off_since = None
        return self.rearing


# ====================================================================== live statistics
class LiveStats:
    """Running per-zone time / entries / latency, distance, speed, immobility and a short history for charts."""

    CHART_PARAMS = {"speed": "Speed", "distance": "Distance", "motion": "Motion (% body)", "detected": "Detected",
                    "freezing": "Freezing"}

    def __init__(self, apparatus: Apparatus | None, fps: float, analysis: AnalysisSettings,
                 history_s: float = 300.0, sample_s: float = 0.1):
        self.set_scale(apparatus)
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
        self.visits: list[list] = []  # [zone, t_in, t_out or None] in order: the live sequence statistics
        self._open_visit: dict[str, list] = {}
        self.points = LivePoints(apparatus)

    def set_scale(self, apparatus: Apparatus | None):
        """Use the apparatus calibration; the distance and speed so far (tracked in pixels) are converted to it."""
        scale = apparatus.scale if apparatus else 1.0
        old = getattr(self, "scale", None)
        if old:
            self.distance *= scale / old
            self.speed *= scale / old
        self.scale = scale
        self.unit = apparatus.unit if apparatus else "px"
        if hasattr(self, "points"):
            self.points.set_apparatus(apparatus)

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
                if now and not self.inside[n]:
                    self._open_visit[n] = v = [n, t, None]
                    self.visits.append(v)
                elif not now and self.inside[n] and n in self._open_visit:
                    self._open_visit.pop(n)[2] = t
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
        self.points.update(t, dt, d.x, d.y, bool(d.detected))
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
class _Scoring(Session):
    def _init_scoring(self):
        self.events: list[dict] = []
        self.open_states: dict[str, dict] = {}
        self.pauses: list[list[float]] = []
        self.pause_log: list[dict] = []  # {"t": test time, "duration_s": real pause length}
        self.warnings: list[tuple[float, str]] = []
        self.log: list[tuple[float, str]] = []

    def warn(self, msg: str, t: float | None = None):
        self.warnings.append((round(self.elapsed if t is None else t, 2), msg))

    def _call_engine(self, fn, *args, t: float | None = None):
        """A procedure engine call: procedure errors become warnings, they never stop the test."""
        try:
            fn(*args)
        except Exception as e:
            self.warn(f"Procedure error: {e}", t)
        self._end_if_requested()

    def _end_if_requested(self):
        """Overridden by sessions whose procedures can end the test (after the engine call has returned)."""

    def _locked(self):
        """The session lock (sessions that record override it to close stopped recorders after it)."""
        return self.lock

    def score(self, behaviour: str, kind: str = "point", t: float | None = None) -> dict | None:
        """Score a behaviour now: a point event, or start / end of a state event. Returns the event, or None when
        the test is not running."""
        with self._locked():
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
            if self.engine is not None:  # procedures can react to scored behaviours ("event marked")
                self._call_engine(self.engine.mark_event, t, behaviour)
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
    While paused the test clock stops: no track rows, no recording and no procedure timing; the "test paused"
    handlers run at once, pulse trains stop and shocks (by default every output) go off, keys and touches still
    reach the procedures (e.g. a "resume test" key) and safety tasks keep running in real time.

    Thread safety: every entry point takes ``lock`` before the procedure engine's own lock (frames from the
    camera thread, :meth:`key` / :meth:`touch` / :meth:`score` / :meth:`pause` from the GUI), so procedure
    handlers that end or pause the test cannot deadlock.

    Recording: frames are written at ``fps`` with the last frame repeated for dropped / late frames so that the
    video stays aligned with the track time; they are encoded in a background thread, and closed in another one
    (:meth:`finish` called from outside the frame thread still returns once the file is closed;
    :meth:`wait_recordings` waits for it). With ``autosave_path`` the track, events and I/O log are written to that
    side file every ``autosave_s`` seconds or less often as it grows (see :func:`autosave.recover`); fast analogue
    samples go to a side file of their own (``<autosave_path>.samples``), appended in chunks.

    Safety without frames: when no frame has arrived for ``frame_timeout_s`` (camera unplugged, reconnecting…) a
    background thread runs the procedures' clock from the last frame's time on the computer's clock, so that shock
    cut-offs, pulse ends, sound ends and input events still happen; frames take over again when they come back.
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
    devices: object = None  # core.iodevices.DeviceManager (or a per-test DeviceView)
    variables: dict | None = None  # procedure variables shared between tests (project.variables)
    record_overlay: bool = False  # burn the test time and event labels into the recording
    split_minutes: float = 0.0  # start a new video file every N minutes (long tests; 0 = one file)
    experimenter_area_px: int = 0  # foreground area counted as the experimenter; 0 = auto
    lost_warning_s: float = 3.0  # warn when the animal is not detected for this long (0 = never)
    name: str = ""
    zone_overrides: dict | None = None  # moveable zones of this test (Test.zone_overrides)
    on_stimulus: object = None  # touch-screen window handler (gui.touchscreen.TouchStimulusWindow.handle)
    outputs_off_on_pause: bool = True  # pausing switches every output off (shocks and pulse trains always stop)
    autosave_path: str | None = None  # crash-recovery side file (track, events, I/O log), rewritten periodically
    autosave_s: float = 5.0
    autosave_meta: dict | None = None  # test id, animal, apparatus … stored in the side file
    autosave_key: object = None  # the experiment's security.ExperimentKey: the side file is encrypted
    record_from_start: bool = True  # False: only the procedures' "start video recording" starts the recording
    disk_low_mb: float = 1024.0  # "disk space low" below this much free space on the recording disk
    disk_full_mb: float = 50.0  # below this the recording stops ("disk full")
    disk_check_s: float = 5.0  # how often the free space is checked while recording (test time)
    control_input: str = ""  # test control switch: "[device/]channel"; closing it continues a test waiting to end
    test_info: dict | None = None  # the test for the procedures (procedures.test_context): trial(), animal() …
    frame_timeout_s: float = 0.5  # no frame for this long: the safety thread runs the procedures (0 = never)

    SAFETY_TICK_S = 0.05

    def __post_init__(self):
        self._base_apparatus = self.apparatus  # the project's map, before this test's overrides
        self.procedure_zone_overrides: dict = {}  # zones / points moved by the procedures ("set zone location")
        if self.zone_overrides and self.apparatus is not None:
            self.apparatus = self.apparatus.with_overrides(self.zone_overrides)
        self.lock = threading.RLock()
        self.tracker: ArenaTracker | None = None
        self.state = "waiting"
        self.t0: float | None = None
        self._track = TrackBuilder()
        self.cols = self._track.cols
        self._init_scoring()
        self.recorder: _RecordingThread | None = None
        self._closing: list[_RecordingThread] = []  # recorders to close once the session lock is released
        self._end_requested = False  # the "end the test" action fired: finish() once the engine call returns
        self._finishing = False
        app = self.apparatus
        ctx = {"zones": [z.name for z in app.zones] + [g.name for g in app.groups] if app else [],
               "points": [p.name for p in app.points] if app else [], "keys": [],
               "duration_s": self.duration_s, "apparatus_map": app, **(self.test_info or {})}
        # the legacy serial-port Outputs is accepted in place of a DeviceManager by the engine
        self.engine = ProcedureEngine(self.procedures, self.devices if self.devices is not None else self.outputs,
                                      on_mark=self._mark, on_end=self._procedure_end,
                                      on_log=self._engine_log,
                                      variables=self.variables if self.variables is not None else {}, context=ctx,
                                      on_pause=lambda t: self.pause(), on_resume=lambda t: self.resume(),
                                      on_stimulus=self.on_stimulus,
                                      outputs_off_on_pause=bool(self.outputs_off_on_pause), commit_kept=False)
        self.engine.on_display, self.engine.on_video, self.engine.on_zone = \
            self._display_cmd, self._video_cmd, self._zone_cmd
        self.engine.on_end_pending = self._end_pending
        self._io_log = LiveIOLog(f"{self.autosave_path}.samples" if self.autosave_path else None)
        self.engine.io_sink = self._io_log  # fast analogue samples kept out of engine.io_events
        if self.outputs is None:
            self.outputs = self.engine.outputs
        self.stats = LiveStats(self.apparatus, self.fps, self.analysis)
        self.occupancy = LiveOccupancy(self.apparatus, self.analysis)
        self.rearing = LiveRearing(self.analysis, self.fps)
        self.popups: list[dict] = []  # pop-up messages of the procedures not yet shown (take_popups)
        self.recording_log: list[tuple[float, str]] = []  # what the procedures did to the recording
        self.record_parts: list[str] = []  # the files recorded (more than one after a stop and a new start)
        self.video_labels: list[dict] = []  # "label the video recording": {"t", "video_t", "text", "file"}
        self._video_paused = False
        self._video_pause_t = 0.0
        self._rec_offset = 0.0  # test time not in the recording (recording paused / started late)
        self._video_label: tuple[str, float | None] | None = None  # (text, until test time)
        self._disk_last = -1e9
        self._control_prev = False
        self._cut_t: float | None = None  # ended by a procedure allowing continuation: the data ends here
        self._disk_low_sent = False
        self._user_warnings_seen = 0
        self.calibration: dict | None = None  # set_calibration(): this test's own calibration
        self.calibration_log: list[tuple[float, dict]] = []
        self.geometry: dict = {}  # set_geometry(): this test's own map position / moved zones (Test.zone_overrides)
        self.geometry_log: list[tuple[float, dict]] = []
        self.capture_gaps: list[list] = []  # [t_lost, t_restored or None]: the camera stopped delivering frames
        self._gap_pending: float | None = None
        self._freeze_thr = LiveThresholds(self.analysis)
        self.start_phase = ""  # experimenter_leaves: "experimenter" -> "leaving" -> "animal"
        self._frame_shape: tuple[int, int] | None = None
        self._detect_since: float | None = None
        self._frame_i = 0
        self._still_since: float | None = None
        self._start_requested = False
        self._wait_ts0: float | None = None  # first frame while waiting to start (procedures' pre-test clock)
        self._resume_pending = False
        self._pause_ts: float | None = None
        self._last_ts: float | None = None
        self._lost_since: float | None = None
        self._lost_warned = False
        self._errors_seen = 0
        self._band: np.ndarray | None = None
        self._arena_px = 0
        self._frz_state = False
        self._rec_frames = 0
        self._last_rec_frame: np.ndarray | None = None
        self._autosave_last = -1e9
        self._autosaver = Autosaver(self.autosave_path, self.autosave_snapshot, self.warn, self.autosave_key) \
            if self.autosave_path else None
        self._closers: list[threading.Thread] = []  # recorders being closed in the background
        self._last_frame_wall: float | None = None  # monotonic time of the last frame (safety thread)
        self._ticker: threading.Thread | None = None

    # ------------------------------------------------------------------
    def set_background(self, frame: np.ndarray):
        self._ensure_tracker(frame)
        self.tracker.set_background(to_gray(frame))

    def set_calibration(self, px_per_cm: float, line=None, length_cm: float | None = None) -> dict:
        """Adjust the apparatus calibration while the test runs (or waits to start): live distances and speeds use
        the new scale from now on, and the saved test keeps it as its own calibration (Test.zone_overrides
        [CALIBRATION_KEY]) so that its analysis uses it. Positions are tracked in pixels, so the whole test is
        analysed with the corrected scale. Returns the stored calibration."""
        cal = calibration_override(px_per_cm, line, length_cm)
        with self.lock:
            if self.state == "finished":
                raise RuntimeError("The test has finished")
            app = (self.apparatus or Apparatus()).copy()  # never change the project's apparatus map
            app.px_per_cm = cal["px_per_cm"]
            app.calibration_line = tuple(cal["calibration_line"]) if cal["calibration_line"] else None
            app.calibration_length_cm = cal["calibration_length_cm"]
            self.apparatus = app
            self.engine.context["apparatus_map"] = app
            self.occupancy.app = app
            self.stats.set_scale(app)
            self.calibration = cal
            t = self.elapsed if self.state in ("running", "paused") else 0.0
            self.calibration_log.append((round(t, 3), dict(cal)))
            self.log.append((t, f"Calibration adjusted: {cal['px_per_cm']:.4g} px/cm"))
        return cal

    def set_geometry(self, position: dict | None = None, zones: dict | None = None) -> dict:
        """Change the apparatus geometry while the test runs (or waits to start), e.g. the apparatus was nudged.
        position {"dx", "dy" (px), "angle" (degrees, clockwise), "scale"} moves the whole map; zones {zone or point
        name: shape dict / {"x", "y"}, or None to undo} moves single zones or points (as Test.zone_overrides).
        Occupancy, the arena mask and the procedures use the new geometry from now on, and the saved test keeps it
        (Test.zone_overrides) so that its analysis uses it. Returns this test's geometry overrides."""
        with self.lock:
            if self.state == "finished":
                raise RuntimeError("The test has finished")
            if self._base_apparatus is None:
                raise ValueError("The test has no apparatus")
            geo = dict(self.geometry)
            if position is not None:
                pos = position_args(position)
                if pos == {"dx": 0.0, "dy": 0.0, "angle": 0.0, "scale": 1.0}:
                    geo.pop(POSITION_KEY, None)
                else:
                    geo[POSITION_KEY] = pos
            for name, v in (zones or {}).items():
                if v is None:
                    geo.pop(name, None)
                else:
                    geo[name] = dict(v)
            self.geometry = geo
            app = self._rebuild_apparatus()
            self.stats.set_scale(app)
            if self.tracker is not None and self._frame_shape is not None:
                try:
                    self.tracker.set_mask(app.arena_or_bounds().mask(self._frame_shape))
                except ValueError:
                    pass
                self._band = None  # the experimenter-detection band follows the arena
            t = self.elapsed if self.state in ("running", "paused") else 0.0
            self.geometry_log.append((round(t, 3), dict(geo)))
            self.log.append((t, "Apparatus geometry adjusted"))
        return dict(geo)

    def _rebuild_apparatus(self) -> Apparatus:
        """This test's map: the project's apparatus with the test's overrides, the geometry changed during the test
        (set_geometry), the zones moved by the procedures and the adjusted calibration."""
        ov = {**(self.zone_overrides or {}), **self.geometry, **self.procedure_zone_overrides}
        if self.calibration:
            ov[CALIBRATION_KEY] = dict(self.calibration)
        app = self._base_apparatus.with_overrides(ov)
        app = app.copy() if app is self._base_apparatus else app  # never change the project's apparatus map
        self.apparatus = app
        self.occupancy.app = app
        return app

    # ------------------------------------------------------------------ capture drop-outs (source thread)
    def capture_lost(self, msg: str = ""):
        """The camera stopped delivering frames and is being reopened: logged as a warning; the gap is marked in
        the track when frames arrive again."""
        with self.lock:
            if self.state not in ("running", "paused") or (self.capture_gaps and self.capture_gaps[-1][1] is None):
                return
            t = self.elapsed
            self.capture_gaps.append([round(t, 3), None])
            self.warn(f"Video capture lost{': ' + msg if msg else ''}", t)

    def capture_restored(self, gap_s: float = 0.0):
        """Frames arrive again after :meth:`capture_lost`: the next frame (whose time jumps over the gap) closes
        it, preceded by an undetected track row (the animal was not seen during the gap)."""
        with self.lock:
            if self.capture_gaps and self.capture_gaps[-1][1] is None:
                self._gap_pending = float(gap_s)

    def _mark_gap(self, t: float):
        gap = self.capture_gaps[-1]
        self._gap_pending = None
        gap[1] = round(t, 3)
        t_mark = gap[0] + 1.0 / self.fps
        if t_mark < t:
            self._track.add(t_mark, Detection())
        self.warn(f"Video capture restored: {t - gap[0]:.1f} s of the test not seen", t)
        self.log.append((t, f"Video capture restored after {t - gap[0]:.1f} s"))

    def _ensure_tracker(self, frame):
        if self.tracker is None:
            h, w = frame.shape[:2]
            mask = self.apparatus.arena_or_bounds().mask((h, w)) if self.apparatus else None
            self.tracker = ArenaTracker(self.settings, mask)

    def _mark(self, name: str, t: float):
        self.events.append({"behaviour": name, "t": t, "t_end": None})

    def _engine_log(self, msg: str, t: float | None = None):
        self.log.append((self.elapsed if t is None else t, str(msg)))

    # ------------------------------------------------------------------ procedure callbacks (under the lock)
    def _procedure_end(self):
        """The "end the test" action: the reason it gives is the test's end reason.  The engine is mid-run here
        (test-end handlers still to come), so the test ends when the engine call returns (_end_if_requested)."""
        self._end_requested = True
        self._end_if_requested()  # not from inside an engine call: now

    def _end_if_requested(self):
        if self._end_requested and self.state != "finished" and not self._finishing and not self.engine._busy:
            self._end_requested = False
            self.finish(self.engine.end_reason or END_PROCEDURE)

    def _end_pending(self, t: float):
        """"End the test" allowing continuation: the test is waiting for its end (see continue_test)."""
        self.warn("Waiting for test end: press Start (or a start key) within 10 s to continue the test", t)

    def _display_cmd(self, cmd: str, params: dict):
        """Pop-up messages are queued for the GUI (take_popups); texts on the display are in display_texts."""
        if cmd == "popup":
            self.popups.append({"t": round(self.elapsed, 3), **params})

    def take_popups(self) -> list[dict]:
        """The pop-up messages of the procedures not shown yet ({"t", "title", "text"}); any thread."""
        with self.lock:
            out, self.popups = self.popups, []
            return out

    @property
    def display_texts(self) -> dict[str, dict]:
        """Texts the procedures put on the display: {name: {"text", "x", "y", "color"}}."""
        return dict(self.engine.display_texts)

    def _zone_cmd(self, cmd: str, params: dict):
        if cmd != "move":
            return  # zone labels: kept by the engine (zone_labels), stored with the test
        name, x, y = params["zone"], float(params["x"]), float(params["y"])
        app = self.apparatus
        z = app.zone(name) if app is not None else None
        if z is not None:
            cx, cy = z.shape.centroid()
            self.procedure_zone_overrides[name] = z.shape.translated(x - cx, y - cy).to_dict()
        elif app is not None and app.point(name) is not None:
            self.procedure_zone_overrides[name] = {"x": x, "y": y}
        else:
            raise ValueError(f"no zone or point called '{name}'")
        self._rebuild_apparatus()

    def _video_cmd(self, cmd: str, params: dict):
        """The procedures' video recorder actions: start (a new file after a stop), stop, pause, unpause, label."""
        t = self.elapsed if self.state in ("running", "paused") else 0.0
        msg = ""
        if cmd == "start":
            if self.recorder is None:
                if not self.record_path or self._frame_shape is None:
                    raise RuntimeError("this test is not set to record video")
                self._open_recorder(t)
                msg = "recording started" if self.recorder is not None else ""
            elif self._video_paused:
                self._video_cmd("unpause", {})
        elif cmd == "stop" and self.recorder is not None:
            self._close_recorder()
            msg = "recording stopped"
        elif cmd == "pause" and self.recorder is not None and not self._video_paused:
            self._video_paused, self._video_pause_t = True, t
            msg = "recording paused"
        elif cmd == "unpause" and self._video_paused:
            self._video_paused = False
            self._rec_offset += t - self._video_pause_t
            msg = "recording resumed"
        elif cmd == "label":  # a marker in the video (as ANY-maze's video labels), optionally shown on it
            text, dur = str(params.get("text") or ""), float(params.get("duration") or 0)
            if self.recorder is None:
                raise RuntimeError("the video is not being recorded")
            part, video_t = self._recording_position()
            lab = {"t": round(t, 3), "video_t": round(video_t, 3), "text": text, "file": Path(part).name if part else ""}
            if self.split_minutes > 0:  # video_t is within the part; the time in the whole playlist too
                lab["recording_t"] = round(self._rec_frames / self.fps, 3)
            self.video_labels.append(lab)
            if dur > 0:
                self._video_label = (text, t + dur)
            msg = f"label “{text}”"
        if msg:
            self.recording_log.append((round(t, 3), msg))
            self.log.append((t, f"Video: {msg}"))

    def _recording_position(self) -> tuple[str, float]:
        """(the file the next recorded frame goes to, its time in that file): with split recordings the current
        part (<name>_partNNN), not the playlist's base name."""
        if not self.record_parts:
            return "", 0.0
        path = self.record_parts[-1]
        if self.split_minutes > 0:
            per = max(1, int(round(self.split_minutes * 60 * self.fps)))
            k, n = divmod(self._rec_frames, per)
            p = Path(path)
            return str(p.with_name(f"{p.stem}_part{k + 1:03d}{p.suffix}")), n / self.fps
        return path, self._rec_frames / self.fps

    def _open_recorder(self, t: float):
        h, w = self._frame_shape
        path = self.record_path
        if self.record_parts:  # started again after a stop: a new file next to the first one
            p = Path(path)
            path = str(p.with_name(f"{p.stem}_part{len(self.record_parts) + 1}{p.suffix}"))
        self._rec_frames = 0
        self._last_rec_frame = None
        self._rec_offset = t
        self._video_paused = False
        try:
            if self.split_minutes > 0:
                rec = SplitRecorder(path, self.fps, (w, h), int(round(self.split_minutes * 60 * self.fps)))
            else:
                rec = VideoRecorder(path, self.fps, (w, h), fragmented=True)
            self.recorder = _RecordingThread(rec)
            self.record_parts.append(path)
        except Exception as e:
            self.recorder = None
            self.warn(f"Cannot record: {e}", t)
            self._system_event(t, "recording_error", {"value": str(e)})

    def _close_recorder(self):
        """Stop recording; the file is closed (waiting for the encoder) once the session lock is released."""
        rec, self.recorder = self.recorder, None
        self._last_rec_frame = None
        if rec is not None:
            self._closing.append(rec)

    def _close_pending_recorders(self):
        """Close the recorders stopped under the lock, outside it and in a background thread each (the encoder may
        take a while to finish: neither the frame thread nor the GUI waits for it here)."""
        if not self._closing or self.lock._is_owned():  # still inside a locked call: its caller closes them
            return
        with self.lock:
            recs, self._closing = self._closing, []
            self._closers = [th for th in self._closers if th.is_alive()]
            for rec in recs:
                th = threading.Thread(target=self._close_recording, args=(rec,), name="live-recorder-close",
                                      daemon=True)
                self._closers.append(th)
                th.start()
        if self.state == "finished":  # the end of the test: whoever ended it gets the complete file, as before
            self.wait_recordings()

    def _close_recording(self, rec):
        close = getattr(rec, "close", None)
        try:
            if close is not None:
                close()
        except Exception as e:
            with self.lock:
                self.warn(f"Recording error: {e}")

    def wait_recordings(self, timeout: float | None = None) -> bool:
        """Wait until the stopped recordings are closed (their files complete); False if one is still closing
        after `timeout` (default: the encoder's own close timeout, plus a margin)."""
        self._close_pending_recorders()
        timeout = _RecordingThread.CLOSE_TIMEOUT_S + 5.0 if timeout is None else timeout
        deadline = time.monotonic() + timeout
        for th in list(self._closers):
            if th is not threading.current_thread():
                th.join(max(0.0, deadline - time.monotonic()))
        return not any(th.is_alive() for th in self._closers)

    @contextmanager
    def _locked(self):
        """The session lock, then the recorders stopped meanwhile are closed."""
        try:
            with self.lock:
                yield
        finally:
            self._close_pending_recorders()

    def _system_event(self, t: float, name: str, args: dict | None = None):
        if self.state in ("running", "paused"):
            self._call_engine(self.engine.system_event, t, name, args or {}, t=t)

    def _check_disk(self, t: float):
        """While recording: "disk space low" once below disk_low_mb, "disk full" (the recording stops) below
        disk_full_mb."""
        if self.recorder is None or t - self._disk_last < self.disk_check_s:
            return
        self._disk_last = t
        free = disk_free_mb(self._recording_position()[0] or self.record_path)
        if free is None:
            return
        if free <= self.disk_full_mb:
            self._recording_failed(OSError(errno.ENOSPC, f"only {free:.0f} MB free"), t)
        elif free <= self.disk_low_mb and not self._disk_low_sent:
            self._disk_low_sent = True
            self.warn(f"Disk space low: {free:.0f} MB free on the recording disk", t)
            self._system_event(t, "disk_space_low", {"value": round(free, 1)})

    def _recording_failed(self, e: Exception, t: float):
        full = is_disk_full(e)
        self.warn(("Disk full: recording stopped" if full else "Recording stopped") + f": {e}", t)
        self._close_recorder()
        self.recording_log.append((round(t, 3), "recording stopped: " + ("disk full" if full else str(e))))
        if full:
            self._system_event(t, "disk_full", {"value": str(e)})
        self._system_event(t, "recording_error", {"value": str(e)})

    @property
    def elapsed(self) -> float:
        t = self.cols["t"]
        return t[-1] if t else 0.0

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def has_data(self) -> bool:
        """The test recorded something worth saving (camera sessions: track rows)."""
        return bool(self.cols["t"])

    def trail(self, n: int) -> list[tuple[float, float]]:
        with self.lock:
            if self.state not in ("running", "paused"):
                return []
            return list(zip(self.cols["x"][-n:], self.cols["y"][-n:]))

    # ------------------------------------------------------------------ control (any thread)
    def request_start(self):
        """Start on the next frame (keyboard / remote / scheduled / collective start)."""
        with self.lock:
            if self.state == "waiting":
                self._start_requested = True

    @property
    def waiting_end(self) -> bool:
        """"Waiting for test end": a procedure ended the test allowing continuation; tracking goes on and the
        experimenter can continue the test (:meth:`continue_test`) for 10 s."""
        return self.state == "running" and bool(getattr(self.engine, "awaiting_continuation", False))

    def continue_test(self) -> bool:
        """Continue a test that is waiting for its end (the Start button, a start key or the test control input):
        the end is forgotten, the data has no gap and the procedures see "test continued"."""
        with self._locked():
            if not self.waiting_end:
                return False
            ok = False
            try:
                ok = self.engine.continue_test(self.elapsed)
            except Exception as e:
                self.warn(f"Procedure error: {e}")
            if ok:
                self.log.append((self.elapsed, "Test continued"))
            return ok

    def _cut_data(self, t_end: float):
        """A test ended by a procedure allowing continuation and not continued: as ANY-maze, only the data up to
        the moment the procedure ended it is kept (track rows, scored events, pauses and the I/O log; outputs
        still on then are logged off at that time). The recording is kept whole."""
        self._cut_t = t_end
        cols = self.cols
        n = sum(1 for x in cols["t"] if x <= t_end + 1e-9)
        for v in cols.values():
            del v[n:]
        self.pauses = [p for p in self.pauses if p[0] <= t_end + 1e-9]
        self.pause_log = [p for p in self.pause_log if p["t"] <= t_end + 1e-9]
        self.log.append((t_end, "Test ended by procedure: the data after this time is discarded"))

    @property
    def io_events(self) -> list:
        """The I/O log so far, fast analogue samples decimated (about 10 per second per channel, marked
        "decimated"): cheap enough to look at while the test runs. :meth:`all_io_events` has every sample."""
        return self._cut_events(list(self.engine.io_events) if self.engine is not None else [])

    def all_io_events(self) -> list:
        """The complete I/O log, every sample of the fast analogue inputs included (for saving the test)."""
        with self.lock:
            evs = self.engine.io_events if self.engine is not None else []
            return self._cut_events(self._io_log.full_events(evs))

    @property
    def input_stats(self):
        """Running statistics of the inputs (core.iolog.InputStats), for livemonitor.input_rows."""
        return self._io_log.stats

    def input_rows(self, now: float | None = None) -> list:
        """The live monitor's input rows (livemonitor.input_rows) from running statistics: no rescan of the log."""
        with self.lock:
            return self._io_log.stats.rows(self.elapsed if now is None else now)

    def _cut_events(self, evs: list) -> list:
        c = self._cut_t
        if c is None:
            return evs
        out, on = [], {}
        for e in evs:
            if e["t"] <= c + 1e-9:
                out.append(e)
                if e["kind"] == "output":
                    on[(e["device"], e["channel"], e.get("type"))] = e
        for (dev, ch, typ), e in on.items():  # still on at the end: off at the cut
            v = e.get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v and typ not in ("thermostat",):
                out.append({**{k: x for k, x in e.items() if k in ("device", "channel", "kind", "type")},
                            "t": round(c, 4), "value": 0})
        return out

    def _check_continuation(self, fg):
        """While waiting for the test end: the test control input continues the test; the experimenter walking
        into view confirms its end."""
        if self.control_input:
            dev, _, ch = self.control_input.rpartition("/")
            on = bool(self.engine._input_value(dev, ch) or 0)
            if on and not self._control_prev and self.continue_test():
                self._control_prev = on
                return
            self._control_prev = on
        if self._intruder(fg):
            self.log.append((self.elapsed, "Experimenter in view: the test ends"))
            self.finish(END_USER)

    def pause(self) -> bool:
        with self._locked():
            if self.state != "running" or self.waiting_end:  # nothing to pause while waiting for the test end
                return False
            self.state = "paused"
            self._pause_ts = self._last_ts
            self._pause_t = self.elapsed
            self._pause_wall = time.monotonic()
            # procedures see "test paused"; the engine's own pause action calls back here
            self._call_engine(self.engine.pause, self._pause_t)
            return True

    def resume(self) -> bool:
        with self._locked():
            if self.state != "paused":
                return False
            self.state = "running"
            self._resume_pending = True
            if self._last_frame_wall is not None:  # the test time goes on from now, not from the last frame
                self._last_frame_wall = max(self._last_frame_wall, time.monotonic())
            self._call_engine(self.engine.resume, self._pause_t)
            return True

    def key(self, key: str, down: bool = True):
        """Forward a key press to the procedures; also while paused (e.g. a "resume" key) and, for the "test is
        waiting to start" handlers, while waiting to start."""
        with self._locked():
            if self.state in ("running", "paused"):
                self._call_engine(self.engine.key, self.elapsed, key, down)
            elif self.state == "waiting" and self.engine._pretest:
                self._call_engine(self.engine.key, self.engine.t, key, down)

    def touch(self, area: str | None, x: float | None = None, y: float | None = None):
        """A touch on the stimulus screen (any thread): forwarded to the procedures under the session lock."""
        with self._locked():
            if self.state in ("running", "paused"):
                self._call_engine(self.engine.touch, self.elapsed, area or None, x, y)

    # ------------------------------------------------------------------ frames (grabber thread)
    def process(self, frame: np.ndarray, timestamp: float | None = None) -> list[Detection]:
        """Process one frame. timestamp defaults to frame_index / fps."""
        with self._locked():
            try:
                return self._process(frame, timestamp)
            finally:
                if self.state != "finished":
                    self._io_tick()
                self._last_frame_wall = time.monotonic()
                self._ensure_ticker()

    # ------------------------------------------------------------------ safety without frames (safety thread)
    def _io_tick(self):
        """The test runs normally: the boards' heartbeats go on (see DeviceManager.tick)."""
        fn = getattr(self.devices, "tick", None)
        if fn is not None:
            try:
                fn(self)
            except Exception:  # pragma: no cover - never stop a test for this
                pass

    def _ensure_ticker(self):
        if self._ticker is None and self.frame_timeout_s and self.frame_timeout_s > 0 and self.state != "finished":
            self._ticker = threading.Thread(target=_safety_loop, args=(weakref.ref(self), self.SAFETY_TICK_S),
                                            name="live-safety", daemon=True)
            self._ticker.start()

    def safety_tick(self) -> bool:
        """Run the procedures on the computer's clock when no frame has come for frame_timeout_s (camera lost):
        shock cut-offs, pulse and sound ends, timers and input events go on. True when it ran them."""
        last = self._last_frame_wall
        if last is None or time.monotonic() - last < self.frame_timeout_s:
            return False
        if not self.lock.acquire(timeout=0.25):
            return False  # a frame (or a long action) holds the session: nothing to do, or no tick (watchdog)
        ran = False
        try:
            gap = time.monotonic() - (self._last_frame_wall or last)
            if gap < self.frame_timeout_s:
                return False
            eng = self.engine
            if self.state == "running" and eng.started and not eng.stopped:
                t = self.elapsed + gap
                self._call_engine(eng.update_state, t, {}, t=t)
                self._engine_messages(t)
                ran = True
            elif self.state == "paused":
                self._call_engine(eng.paused_tick, time.monotonic() - self._pause_wall)
                ran = True
            elif self.state == "waiting" and getattr(eng, "_pretest", False) and self._wait_ts0 is not None \
                    and self._last_ts is not None:
                self._call_engine(eng.waiting_update, self._last_ts - self._wait_ts0 + gap, {})
                ran = True
            if ran and self.state != "finished":
                self._io_tick()
        finally:
            self.lock.release()
            self._close_pending_recorders()
        return ran

    def _process(self, frame, timestamp):
        self._ensure_tracker(frame)
        self._frame_shape = frame.shape[:2]
        ts = timestamp if timestamp is not None else self._frame_i / self.fps
        self._frame_i += 1
        dets, fg = self.tracker.process(frame)
        prev_ts, self._last_ts = self._last_ts, ts
        if self.state == "finished":
            return dets
        if self.state == "waiting":
            if self._wait_ts0 is None:
                self._wait_ts0 = ts
            d = dets[0] if dets else Detection()
            self._call_engine(self.engine.waiting_update, ts - self._wait_ts0,
                              {"detected": bool(d.detected), "x": float(d.x), "y": float(d.y)})
            self._check_start(ts, dets, fg)
            if self.state != "running":
                return dets
        if self.state == "paused":
            self._call_engine(self.engine.paused_tick, time.monotonic() - self._pause_wall)
            return dets
        if self._resume_pending:
            self._resume_pending = False
            gap = (ts - self._pause_ts) if self._pause_ts is not None else 0.0
            self.t0 = ts - (self.elapsed + 1.0 / self.fps)
            real = time.monotonic() - self._pause_wall
            self.pauses.append([round(self._pause_t, 3), round(self._pause_t, 3)])
            self.pause_log.append({"t": round(self._pause_t, 3), "duration_s": round(max(gap, real), 3)})
        elif prev_ts is not None and self.cols["t"] and ts - prev_ts > 2.5 / self.fps and self._gap_pending is None:
            n = int(round((ts - prev_ts) * self.fps)) - 1
            self.warn(f"{n} frame{'s' if n > 1 else ''} dropped")
        t = ts - self.t0
        d = dets[0] if dets else Detection()
        if self._gap_pending is not None:  # the first frame after a capture drop-out
            self._mark_gap(t)
        self._track.add(t, d)
        zones, head_zones = self.occupancy.update(d, t)
        freezing = self._freezing_now(d)
        self.stats.update(t, d, zones, freezing)
        rearing = self.rearing.update(t, d)
        self._check_lost(t, d.detected)
        if self.recorder is not None:
            self._record(frame, t)
            self._check_disk(t)
        self._update_engine(t, d, zones, head_zones, freezing, rearing)
        if self.state == "running" and self.waiting_end:
            self._check_continuation(fg)
            if self.state == "finished":
                return dets
        if self.duration_s and t >= self.duration_s:
            self.finish(END_DURATION)
        elif self._autosaver is not None and t - self._autosave_last >= self.autosave_s:
            self._autosave_last = t
            self._autosaver.request()
        return dets

    def _check_start(self, ts, dets, fg):
        if not self.engine.start_allowed:  # a "test is waiting to start" procedure holds the start
            return
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
        if self._video_paused:
            return
        try:
            img = annotate_recording(frame, t, self.recent_labels()) if self.record_overlay else frame
            if self._video_label is not None:
                text, until = self._video_label
                if until is not None and t >= until:
                    self._video_label = None
                else:
                    img = burn_label(img, text)
            # constant frame rate: frame k shows test time k / fps (from the start of the recording, without the
            # time it was paused). Late / dropped camera frames are padded by repeating the previous image;
            # frames arriving faster than fps are skipped.
            target = int(math.floor((t - self._rec_offset) * self.fps + 0.5)) + 1
            done = self._rec_frames
            if target <= done:
                return
            prev = self._last_rec_frame
            if prev is not None:
                if target - done - 1 > 0:  # repeated by the encoder thread, not queued frame by frame
                    self.recorder.repeat(prev, target - done - 1)
                self._rec_frames = target - 1
            self.recorder.write(img)
            self._rec_frames += 1
            self._last_rec_frame = img
        except Exception as e:
            self._recording_failed(e, t)

    def _update_engine(self, t, d, zones, head_zones, freezing, rearing=False):
        eng = self.engine
        occ = self.occupancy
        try:
            eng.update_state(t, {"zones": zones, "head_zones": head_zones, "detected": bool(d.detected),
                                 "freezing": freezing, "immobile": self.stats.immobile,
                                 "x": float(d.x), "y": float(d.y), "speed": self.stats.speed,
                                 "distance": self.stats.distance, "hx": float(d.hx), "hy": float(d.hy),
                                 "tx": float(d.tx), "ty": float(d.ty), "rearing": bool(rearing),
                                 "investigating": dict(occ.investigating), "orientation": dict(occ.orientation),
                                 "events": list(occ.events)})
        except Exception as e:
            self.warn(f"Procedure error: {type(e).__name__}: {e}", t)
        self._end_if_requested()
        self._engine_messages(t)

    def _engine_messages(self, t):
        """The procedures' new errors and warnings, as the test's warnings."""
        eng = self.engine
        for msg in eng.errors[self._errors_seen:]:
            self.warn(f"Procedure error: {msg}", t)
        self._errors_seen = len(eng.errors)
        for tw, msg in eng.user_warnings[self._user_warnings_seen:]:
            self.warn(msg, tw)
        self._user_warnings_seen = len(eng.user_warnings)

    def _freezing_now(self, d: Detection) -> bool:
        """Freezing with the analysis rules (measures.kinematics): motion below freeze_on_pct starts it, it
        lasts while motion stays at or below freeze_off_pct (hysteresis), and only after min_freeze_s."""
        if not d.detected or not d.area or math.isnan(d.motion):
            self._frz_state = False
            self._still_since = None
            return False
        pct = d.motion / max(d.area, 1) * 100
        t = self.cols["t"][-1]
        a = self.analysis
        on, off = self._freeze_thr.update(t, pct)  # manual, or automatic from the motion seen so far
        self._frz_state = pct <= max(off, on) if self._frz_state else pct < on
        if self._frz_state:
            if self._still_since is None:
                self._still_since = t
            return t - self._still_since >= a.min_freeze_s
        self._still_since = None
        return False

    def _start(self, ts):
        self.state = "running"
        self.start_phase = ""
        self.t0 = ts
        if self.record_path and self.record_from_start:
            self._open_recorder(0.0)
        self._call_engine(self.engine.start, 0.0, t=0.0)

    def finish(self, reason: str = END_USER):
        """End the test (any thread); reason: why (Test.end_reason), by default stopped by the user."""
        with self._locked():
            if self.state == "finished":
                return
            eng = self.engine
            waiting = self.waiting_end
            if waiting:  # waiting for the test end and not continued: the procedure ended the test
                reason = eng.end_reason or END_PROCEDURE
            end_at = getattr(eng, "end_at", None)
            self.end_reason = reason
            if self.state == "paused":
                self.pauses.append([round(self._pause_t, 3), round(self._pause_t, 3)])
                self.pause_log.append({"t": round(self._pause_t, 3),
                                       "duration_s": round(time.monotonic() - self._pause_wall, 3)})
            if self.state in ("running", "paused") or (self.state == "waiting" and getattr(eng, "_pretest", False)):
                self._finishing = True  # test-end handlers ending the test again: this finish() goes on
                try:  # while waiting: the pre-test procedures stop and their outputs go off
                    self._call_engine(eng.stop, self.elapsed if self.state != "waiting" else eng.t)
                finally:
                    self._finishing = False
            if end_at is not None and (waiting or eng.ended) and end_at < self.elapsed - 1e-9:
                self._cut_data(end_at)
            self._close_states()
            for m in self.engine.state_events:  # "mark start / end" actions
                self.events.append({"behaviour": m["behaviour"], "t": m["t"],
                                    "t_end": m["t_end"] if m["t_end"] is not None else round(self.elapsed, 3)})
            if self._cut_t is not None:
                c = self._cut_t
                self.events = [dict(e, t_end=min(e["t_end"], c) if e.get("t_end") is not None else None)
                               for e in self.events if e["t"] <= c + 1e-9]
            self.state = "finished"
            self._close_recorder()
            release = getattr(self.devices, "release", None)  # a per-test DeviceView: its box off, unsubscribed
            if release is not None:
                try:
                    release()
                except Exception as e:
                    self.warn(f"I/O error: {e}")
            untick = getattr(self.devices, "untick", None)  # the heartbeats no longer wait for this test
            if untick is not None:
                try:
                    untick(self)
                except Exception:  # pragma: no cover
                    pass
            if self._autosaver is not None and self.cols["t"]:
                self._autosaver.request(force=True)  # the final state, until the test is saved or discarded

    # ------------------------------------------------------------------ crash recovery
    def autosave_snapshot(self) -> dict:
        """Everything needed to rebuild the test after a crash (JSON-serialisable).  The short parts are copied
        under the lock; the track columns, only ever appended to, are copied up to that instant without it. Fast
        analogue samples are not in it: they are appended to their own side file (see core.iolog)."""
        with self.lock:
            n = len(self.cols["t"])
            eng = self.engine
            d = {"version": 2, "name": self.name, "meta": dict(self.autosave_meta or {}), "fps": self.fps,
                 "duration_s": self.duration_s, "state": self.state, "record_path": self.record_path,
                 "settings": self.settings.to_dict(), "events": [dict(e) for e in self.events],
                 "open_states": list(self.open_states), "pauses": [list(p) for p in self.pauses],
                 "pause_log": [dict(p) for p in self.pause_log],
                 "io_events": [e for e in self.io_events if not e.get("decimated")],
                 "io_samples": self._io_log.snapshot(),
                 "log": list(self.log), "warnings": list(self.warnings),
                 "result_variables": self.result_variables, "calibration": self.calibration,
                 "calibration_log": [[t, dict(c)] for t, c in self.calibration_log],
                 "geometry": dict(self.geometry), "geometry_log": [[t, dict(g)] for t, g in self.geometry_log],
                 "capture_gaps": [list(g) for g in self.capture_gaps],
                 "procedure_zone_overrides": copy.deepcopy(self.procedure_zone_overrides),
                 "zone_labels": dict(getattr(eng, "zone_labels", None) or {}),
                 "scheduled_tests": [dict(s) for s in getattr(eng, "scheduled_tests", None) or []],
                 "animal_weights": [list(w) for w in getattr(eng, "animal_weights", None) or []],
                 "kept_variables": self._kept_now(),
                 "video_labels": [dict(v) for v in self.video_labels],
                 "recording_log": [list(r) for r in self.recording_log],
                 "record_parts": list(self.record_parts),
                 "procedure_log": list(getattr(self.outputs, "log", None) or []),
                 "end_reason": self.end_reason if self.state == "finished" else "",
                 "saved_at": _dt.datetime.now().isoformat(timespec="seconds")}
        d["cols"] = self._track.snapshot(n)
        return d

    def _kept_now(self) -> dict:
        """The procedure variables marked "keep" as they are now (the engine stores them only at the test end)."""
        eng = self.engine
        try:
            if eng.stopped:
                return copy.deepcopy(eng.kept_variables)
            out: dict = {}
            for name, flags in eng.var_flags.items():
                if flags.get("keep"):
                    eng._kept_store(out, flags["keep"], create=True)[name] = copy.deepcopy(eng.vars.get(name))
            return out
        except Exception:  # never let crash recovery fail on a variable
            return {}

    def flush_autosave(self, timeout: float = 10.0):
        """Write the side file now and wait until it is on disk."""
        if self._autosaver is not None:
            self._autosaver.flush(timeout)

    def remove_autosave(self):
        """The test was saved or discarded: stop autosaving and delete the side files."""
        if self._autosaver is not None:
            self._autosaver.remove()
            self._io_log.remove()

    def track(self) -> Track:
        tr = self._track.build(self.fps)
        tr.meta["source"] = "live"
        if self.capture_gaps:
            tr.meta["capture_gaps"] = [list(g) for g in self.capture_gaps]
        contrast = self.tracker.animal_contrast() if self.tracker is not None else ""
        if contrast:
            tr.meta["animal_contrast"] = contrast
        return postprocess(tr, self.settings)


def _safety_loop(ref, period: float):
    """The safety thread of a live session (weakly referenced: it ends with the session or the test)."""
    while True:
        time.sleep(period)
        s = ref()
        if s is None or s.state == "finished":
            return
        try:
            s.safety_tick()
        except Exception:  # pragma: no cover - reported by the session itself; never end the thread silently
            pass
        del s


def _put_text(img: np.ndarray, text: str, x: int, y: int, colour=(255, 255, 255), scale: float | None = None):
    scale = scale or max(0.4, img.shape[1] / 1200)
    th = max(1, int(round(scale * 1.5)))
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    cv2.rectangle(img, (x - 4, y - tht - 4), (x + tw + 4, y + base + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, th, cv2.LINE_AA)


def burn_label(frame: np.ndarray, text: str) -> np.ndarray:
    """A copy of the frame with a procedure's recording label at the top left."""
    img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    _put_text(img, str(text), 8, 8 + int(max(0.4, img.shape[1] / 1200) * 24))
    return img


def _bgr(colour: str) -> tuple[int, int, int]:
    c = str(colour or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return b, g, r
    except (ValueError, IndexError):
        return 0, 255, 255


def draw_display_texts(frame: np.ndarray, texts: dict) -> np.ndarray:
    """The procedures' texts on the display ("output text on the display") drawn on a copy of the frame."""
    if not texts:
        return frame
    img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    for v in texts.values():
        _put_text(img, str(v.get("text", "")), int(v.get("x") or 0), int(v.get("y") or 0), _bgr(v.get("color")))
    return img


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




# ====================================================================== I/O only (no camera)
@dataclass
class IOSession(LiveSession):
    """A live test without a camera (ANY-maze's "Input/output only mode", e.g. operant chambers): the procedures
    and the I/O devices run on the computer's clock. Everything else is a camera session's — start modes
    (immediately, on a start key / remote / clock time: :meth:`request_start`), the "test is waiting to start"
    procedures, pausing, the end by duration / procedure / user, continuation, scoring keys and crash recovery —
    but there is no track, image or recording, and the procedures see no animal.

    :meth:`start_clock` runs :meth:`tick` every ``tick_s`` seconds in a thread of its own (stopped when the test
    ends); tests can call ``tick(t)`` themselves with a clock time instead. Saved as a test without a track
    (session.save_live_test: the I/O log, events and result variables)."""

    apparatus: Apparatus | None = None
    settings: DetectionSettings = field(default_factory=DetectionSettings)
    start_mode: str = "manual"
    frame_timeout_s: float = 0.0  # the session runs its own clock: no safety thread
    tick_s: float = 0.01  # how often the procedures run (s)
    clock: object = time.monotonic

    io_only = True

    def __post_init__(self):
        super().__post_init__()
        self._t = 0.0  # test time (s): the elapsed time of the test
        self._clock0: float | None = None
        self._clock_thread: threading.Thread | None = None

    @property
    def elapsed(self) -> float:
        return self._t

    @property
    def has_data(self) -> bool:
        return self.t0 is not None  # the test started

    def track(self):
        return None

    def trail(self, n: int) -> list[tuple[float, float]]:
        return []

    def tick(self, t: float | None = None):
        """Run the procedures up to clock time t (s since the first tick; default: the session's clock)."""
        if t is None:
            now = self.clock()
            if self._clock0 is None:
                self._clock0 = now
            t = now - self._clock0
        self.process(None, float(t))

    def start_clock(self):
        """Tick in a thread of its own until the test ends (weakly referenced: it ends with the session)."""
        if self._clock_thread is None and self.state != "finished":
            self._clock_thread = threading.Thread(target=_io_loop, args=(weakref.ref(self), self.tick_s),
                                                  name="io-session", daemon=True)
            self._clock_thread.start()

    def _process(self, frame, timestamp):
        ts = float(timestamp)
        self._last_ts = ts
        if self.state == "finished":
            return []
        if self.state == "waiting":
            if self._wait_ts0 is None:
                self._wait_ts0 = ts
            self._call_engine(self.engine.waiting_update, ts - self._wait_ts0, {})
            self._check_start(ts, [], None)
            if self.state != "running":
                return []
        if self.state == "paused":
            self._call_engine(self.engine.paused_tick, time.monotonic() - self._pause_wall)
            return []
        if self._resume_pending:  # the test clock goes on from where it was paused
            self._resume_pending = False
            self.t0 = ts - self._pause_t
            self.pauses.append([round(self._pause_t, 3), round(self._pause_t, 3)])
            self.pause_log.append({"t": round(self._pause_t, 3),
                                   "duration_s": round(time.monotonic() - self._pause_wall, 3)})
        t = max(self._t, ts - self.t0)
        if self.duration_s and t >= self.duration_s:
            t = float(self.duration_s)
        self._t = t
        self._update_engine(t, Detection(), {}, {}, False, False)
        if self.state == "running" and self.waiting_end:
            self._check_continuation(None)
            if self.state == "finished":
                return []
        if self.duration_s and t >= self.duration_s and self.state != "finished":
            self.finish(END_DURATION)
        elif self._autosaver is not None and t - self._autosave_last >= self.autosave_s:
            self._autosave_last = t
            self._autosaver.request()
        return []

    def _cut_data(self, t_end: float):
        super()._cut_data(t_end)
        self._t = min(self._t, float(t_end))

    def finish(self, reason: str = END_USER):
        super().finish(reason)
        if self._autosaver is not None and self.t0 is not None:
            self._autosaver.request(force=True)  # (a camera session does this when it has track rows)

    def autosave_snapshot(self) -> dict:
        d = super().autosave_snapshot()
        d["io_only"] = True
        d["elapsed"] = round(self._t, 4)
        return d


def _io_loop(ref, period: float):
    """The clock of an I/O-only session (weakly referenced: it ends with the session or the test)."""
    while True:
        s = ref()
        if s is None or s.state == "finished":
            return
        try:
            s.tick()
        except Exception:  # pragma: no cover - reported by the session itself; never end the clock silently
            pass
        del s
        time.sleep(period)


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
        self.apparatus = None
        self._init_scoring()
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

    def tick(self):
        with self.lock:
            if self.state == "running" and self.duration_s and self.elapsed >= self.duration_s:
                self._frozen = float(self.duration_s)
                self.finish(END_DURATION)

    def finish(self, reason: str = END_USER):
        with self.lock:
            if self.state == "finished":
                return
            self.end_reason = reason
            if self.state == "paused":
                self.resume()
            if self._frozen is None:
                self._frozen = self.elapsed
            self._close_states()
            self.state = "finished"
