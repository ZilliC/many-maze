"""More live state of the procedure engine (a mixin): investigation, orientation, body-part positions, rearing,
events reported by the live test (hidden-zone partial exits, disk space, recording errors), the event-wizard
options of "when" handlers (N times within S seconds, only in some trials) and the callbacks a live test
implements (pop-ups and display text, the video recorder, zone labels and positions)."""

from __future__ import annotations

import datetime as _dt
import math
import re
from collections import deque

# the "when" options of the event wizard: {field: (label, parameter type)}
WHEN_OPTIONS = ("times", "within", "trials")
_TRIAL_PART = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def parse_trials(text) -> tuple[set[int], set[str]] | None:
    """Trials a handler applies to: "1, 3-5, 8", "odd", "even" (combined with commas). None if invalid."""
    nums: set[int] = set()
    words: set[str] = set()
    for part in str(text or "").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in ("odd", "even"):
            words.add(part)
            continue
        m = _TRIAL_PART.match(part)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if b < a or b - a > 100000:
            return None
        nums.update(range(a, b + 1))
    return nums, words


def trial_selected(text, trial) -> bool:
    parsed = parse_trials(text)
    if parsed is None:
        return True
    nums, words = parsed
    if not nums and not words:
        return True
    try:
        n = int(trial)
    except (TypeError, ValueError):
        return True  # trial unknown (e.g. not a live test of the experiment): not filtered
    return n in nums or ("odd" in words and n % 2 == 1) or ("even" in words and n % 2 == 0)


def parse_clock(text) -> float | None:
    """Seconds since midnight of "HH:MM" or "HH:MM:SS" (None if invalid)."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?\s*$", str(text or ""))
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3) or 0)
    if h > 23 or mi > 59 or s >= 60:
        return None
    return h * 3600 + mi * 60 + s


class LiveState:
    """Mixin of :class:`ProcedureEngine`. Callbacks (attributes, set by the live test):

    * ``on_display(cmd, params)`` — "popup" {text, title}, "text" {name, text, x, y, color}, "remove" {name},
      "clear" {};
    * ``on_video(cmd, params)`` — "start", "stop", "pause", "unpause", "label" {text, duration};
    * ``on_zone(cmd, params)`` — "label" {zone, label} ("" = removed), "move" {zone, x, y}.

    ``wall_clock()`` (the computer's time, for "time of day") can be replaced in tests."""

    on_display = None
    on_video = None
    on_zone = None
    wall_clock = staticmethod(_dt.datetime.now)

    def _reset_extra(self):
        self.end_reason = ""
        self.awaiting_continuation = False
        self.scheduled_tests: list[dict] = []
        self.user_warnings: list[tuple[float, str]] = []
        self.zone_labels: dict[str, str] = {}
        self.zone_moves: dict[str, tuple[float, float]] = {}
        self.video_commands: list = []
        self.display_texts: dict[str, dict] = {}
        self.display_log: list = []
        self.programs: list = []
        self.output_params: dict[tuple, dict] = {}
        self.volumes: dict[str, float] = {}
        self._investigating: dict[str, bool] = {}
        self._orient: dict[str, float] = {}
        self._parts: dict[str, tuple[float, float]] = {}
        self._rearing = False
        self.rear_count = 0
        self.rear_time = 0.0
        self._when_hits: dict[int, deque] = {}

    # ------------------------------------------------------------------ observation
    def _observe_extra(self, t, dt, st):
        """The newer parts of a frame's state: "investigating" {zone: bool}, "orientation" {zone or point: angle
        between the body's orientation and the direction to it, degrees}, "hx", "hy", "tx", "ty", "rearing" and
        "events" [(event name, args)] reported by the live test."""
        if self._rearing:
            self.rear_time += dt
        inv = st.get("investigating")
        if inv:
            for name, on in inv.items():
                on = bool(on)
                if on != self._investigating.get(name, False):
                    self._emit("investigation_start" if on else "investigation_end", {"zone": name}, t)
                self._investigating[name] = on
        if st.get("orientation") is not None:
            self._orient = dict(st["orientation"])
        if st.get("x") is not None and st.get("y") is not None:
            self._parts["centre"] = (float(st["x"]), float(st["y"]))
        for part, kx, ky in (("head", "hx", "hy"), ("tail", "tx", "ty")):
            if st.get(kx) is not None and st.get(ky) is not None:
                self._parts[part] = (float(st[kx]), float(st[ky]))
        if "rearing" in st:
            v = bool(st["rearing"])
            if v != self._rearing:
                if v:
                    self.rear_count += 1
                self._emit("rearing_start" if v else "rearing_end", {}, t)
            self._rearing = v
        for name, args in st.get("events") or ():
            self._emit(name, dict(args or {}), t)

    def system_event(self, t: float, name: str, args: dict | None = None):
        """An event the live test reports (disk space low, disk full, recording error …); thread-safe."""
        with self._lock:
            self._external(t, name, dict(args or {}))

    def test_duration(self) -> float:
        try:
            return float(self.context.get("duration_s") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _orientation(self, name) -> float | None:
        v = self._orient.get(str(name))
        return v if v is not None and math.isfinite(v) else None

    # ------------------------------------------------------------------ event wizard
    def _when_ok(self, h, t_occ) -> bool:
        """The "when" options: only in some trials; only once the event happened N times within S seconds."""
        st = h.st
        if st.get("trials") not in (None, "") and not trial_selected(st["trials"], self._test_info().get("trial")):
            return False
        try:
            times = int(st.get("times") or 0)
        except (TypeError, ValueError):
            times = 0
        if times <= 1:
            return True
        q = self._when_hits.setdefault(id(h), deque())
        q.append(t_occ)
        try:
            within = float(st.get("within") or 0)
        except (TypeError, ValueError):
            within = 0.0
        if within > 0:
            while q and q[0] < t_occ - within - 1e-9:
                q.popleft()
        if len(q) >= times:
            q.clear()
            return True
        return False


# expression functions of this live state (registered in expr.FUNCTIONS; implemented by the engine)
LIVE_FUNCTIONS = {
    "rearing": (lambda eng: int(eng._rearing), 0, 0, "1 while the animal rears"),
    "rears": (lambda eng: eng.rear_count, 0, 0, "rears so far"),
    "rearing_time": (lambda eng: eng.rear_time, 0, 0, "total time rearing so far (s)"),
    "investigating": (lambda eng, z: int(bool(eng._investigating.get(str(z)))), 1, 1,
                      "1 while the animal investigates the zone"),
    "orientation": (lambda eng, name: (lambda v: -1 if v is None else v)(eng._orientation(name)), 1, 1,
                    "angle (°) between the body's orientation and the direction to a zone or point (-1: unknown)"),
    "zone_label": (lambda eng, z: eng.zone_labels.get(str(z), ""), 1, 1, "the label set on a zone (or '')"),
}


def _register_functions():
    from .expr import FUNCTIONS

    for name, (_fn, lo, hi, doc) in LIVE_FUNCTIONS.items():
        FUNCTIONS.setdefault(name, (lo, hi, None, doc))


_register_functions()
