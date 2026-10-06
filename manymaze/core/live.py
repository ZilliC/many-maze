"""Live (real-time) testing from a camera: tracking, recording, procedures and auto start/stop."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from .apparatus import Apparatus
from .measures import AnalysisSettings
from .procedures import Outputs, ProcedureEngine
from .track import Track
from .tracking import ArenaTracker, Detection, DetectionSettings, postprocess, to_gray
from .video import VideoRecorder


@dataclass
class LiveSession:
    """Feed frames one by one with :meth:`process`.

    States: "waiting" (armed, waiting for start condition) -> "running" -> "finished".
    start_mode: "immediate" | "on_detection" (animal detected inside the arena for start_hold_s).
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

    def __post_init__(self):
        self.tracker: ArenaTracker | None = None
        self.state = "waiting"
        self.t0: float | None = None
        self.cols = {c: [] for c in ("t", "x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle", "detected")}
        self.events: list[dict] = []
        self.recorder: VideoRecorder | None = None
        self.engine = ProcedureEngine(self.procedures, self.outputs or Outputs(), on_mark=self._mark,
                                      on_end=self.finish)
        self._detect_since: float | None = None
        self._frame_i = 0
        self._last_dets: list[Detection] = []
        self._still_since: float | None = None
        self._motion_hist: list[float] = []

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

    @property
    def elapsed(self) -> float:
        n = len(self.cols["t"])
        return self.cols["t"][-1] if n else 0.0

    def process(self, frame: np.ndarray, timestamp: float | None = None) -> list[Detection]:
        """Process one frame. timestamp defaults to frame_index / fps."""
        self._ensure_tracker(frame)
        self._frame_shape = frame.shape[:2]
        ts = timestamp if timestamp is not None else self._frame_i / self.fps
        self._frame_i += 1
        dets, fg = self.tracker.process(frame)
        self._last_dets = dets
        self.last_fg = fg
        if self.state == "finished":
            return dets
        if self.state == "waiting":
            detected = any(d.detected for d in dets)
            if self.start_mode == "immediate":
                self._start(ts)
            elif detected:
                if self._detect_since is None:
                    self._detect_since = ts
                if ts - self._detect_since >= self.start_hold_s:
                    self._start(ts)
            else:
                self._detect_since = None
            if self.state != "running":
                return dets
        t = ts - self.t0
        d = dets[0] if dets else Detection()
        self.cols["t"].append(t)
        for c in ("x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle"):
            self.cols[c].append(getattr(d, c))
        self.cols["detected"].append(d.detected)
        if self.recorder is not None:
            self.recorder.write(frame)
        zones = {}
        if d.detected:
            zones = {k: bool(v) for k, v in self.apparatus.zone_membership(np.array([d.x]), np.array([d.y])).items()}
            zones = {k: bool(np.asarray(v).ravel()[0]) for k, v in zones.items()}
        freezing = self._freezing_now(d)
        self.engine.update(t, zones, detected=d.detected, freezing=freezing, immobile=freezing)
        if self.duration_s and t >= self.duration_s:
            self.finish()
        return dets

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
        self.t0 = ts
        if self.record_path:
            h, w = self._frame_shape
            self.recorder = VideoRecorder(self.record_path, self.fps, (w, h))
        self.engine.start(0.0)
        self.started_wall = time.time()

    def finish(self):
        if self.state == "finished":
            return
        if self.state == "running":
            self.engine.stop(self.elapsed)
        self.state = "finished"
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None

    def track(self) -> Track:
        tr = Track(**{c: np.asarray(v) for c, v in self.cols.items()}, fps=self.fps)
        tr.meta["source"] = "live"
        return postprocess(tr, self.settings)
