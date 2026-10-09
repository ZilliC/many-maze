"""What handlers and threads wait for: event detectors (stateful watchers for one event with its parameters)
and the wait conditions of a thread."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .catalog import EPS, EVENT_SPECS, MAX_ENCODER_EVENTS


def _rising(prev, cur, test) -> bool:
    """`test` became true: it holds for cur but did not for prev (None: unknown, e.g. at the start)."""
    return bool(test(cur)) and not (prev is not None and test(prev))


class _Detector:
    """Watches for one event (with its parameters) for a handler or a waiting thread."""

    def __init__(self, eng, st: dict, th=None, path=(), initial: bool = False):
        self.event = st.get("event", "")
        self.spec = EVENT_SPECS.get(self.event, {"params": [], "generic": True})
        self.generic = self.spec.get("generic", True)
        self.args: dict = {}
        self.hits: list = []
        self.th, self.path = th, path
        self.done = False
        for p in self.spec["params"]:
            v = st.get(p["name"], p["default"])
            if p["type"] in ("number", "int"):
                v = None if v in (None, "") else eng._num(th, v, path, what=p["label"], default=None)
                if v is None and isinstance(p["default"], (int, float)):
                    v = p["default"]  # empty (or not a number): the default
                    if p["req"]:
                        eng._error(th, path, f"{self.spec.get('label', self.event)}: {p['label']} is empty or not "
                                             f"a number — using {v:g}")
                elif v is None and p["req"]:  # no default: the detector never fires
                    eng._error(th, path, f"{self.spec.get('label', self.event)}: {p['label']} is empty or not a "
                                         "number — the event is ignored")
                    self.done = True
            elif p["type"] != "expr":
                v = "" if v is None else str(v).strip()
            if p["type"] == "device" and v:
                v = eng._devname(v)
            self.args[p["name"]] = v
        self.cond = st.get("cond")
        self.prev = None
        self.seq: list[str] = []
        a, e = self.args, self.event
        if e == "every":
            # counted from when the detector starts (the test start; or when its procedure is enabled / the thread
            # starts waiting), so a late start does not fire a burst of back-dated occurrences
            iv = a.get("interval") or 0
            base = th.clock if th is not None and math.isfinite(th.clock) else eng.t
            self.next_t = base + (a["first"] if a.get("first") not in (None, "") else iv)
            if iv <= 0:
                eng._error(th, path, "Every: the interval must be positive")
                self.done = True
        elif initial:  # a handler created at the start: a condition already true at the start counts
            self.prev = None
        elif e in ("speed_above", "speed_below"):
            self.prev = eng._speed
        elif e in ("analog_above", "analog_below", "sensor_above", "sensor_below"):
            self.prev = eng._input_value(a.get("device"), a.get("channel"))
        elif e in ("condition_true", "condition_false"):
            self.prev = eng._truth(th, self.cond, path, quiet=True)
        if e == "encoder_every":  # from the first reading (None: none yet)
            self.prev = eng._input_value(a.get("device"), a.get("channel"))
        self._init_extra(eng, initial)

    def _init_extra(self, eng, initial):
        """State of the event-wizard, orientation, position, encoder and value detectors."""
        a, e = self.args, self.event
        self.since = None
        self.fired = False
        self.samples: list = []
        self.dir = 0
        if e == "random_interval":
            lo, hi = sorted((a.get("min") or 0, a.get("max") or 0))
            if hi <= 0:
                eng._error(self.th, self.path, "At random intervals: the longest interval must be positive")
                self.done = True
            else:
                self.range = (max(lo, 0.001), hi)
                self.next_t = eng.t + eng.rng.uniform(*self.range)
        elif e == "time_of_day":
            from .live_state import parse_clock

            self.target = parse_clock(a.get("clock"))
            if self.target is None:
                eng._error(self.th, self.path, f"Time of day: '{a.get('clock')}' is not a time (HH:MM)")
                self.done = True
            self.prev = _seconds_of_day(eng.wall_clock())
        elif e == "zone_not_entered":
            self.since = None if eng._zones.get(a.get("zone")) else eng.t
        elif e in ("oriented_towards", "oriented_away") and not initial:
            self.prev = eng._orientation(a.get("target"))
        elif e == "position_changed":
            self.prev = eng._parts.get(a.get("part") or "centre")

    def matches(self, name: str, args: dict) -> bool:
        if name != self.event:
            return False
        for k, want in self.args.items():
            if want in (None, ""):
                continue
            got = args.get(k)
            if k == "key":
                if str(got or "").lower() != str(want).lower():
                    return False
            elif str(got) != str(want):
                return False
        return True

    def poll(self, eng, t: float) -> list:
        """Stateful detection, once per frame: [(t_occurrence, args)]."""
        if self.done:
            return []
        a, e = self.args, self.event
        out = []
        if e == "time_reached":
            if t + EPS >= (a.get("time") or 0):
                out.append((a.get("time") or 0, {}))
                self.done = True
        elif e == "every":
            iv = a["interval"]
            n = 0
            while self.next_t <= t + EPS and n < 100:
                out.append((self.next_t, {}))
                self.next_t += iv
                n += 1
        elif e == "zone_time_reaches":
            if eng.zone_time.get(a["zone"], 0.0) + EPS >= a["seconds"]:
                out.append((t, {"zone": a["zone"]}))
                self.done = True
        elif e == "zone_entries_reach":
            if eng.zone_entries.get(a["zone"], 0) >= a["count"]:
                out.append((t, {"zone": a["zone"]}))
                self.done = True
        elif e == "zone_dwell":
            since = eng.zone_since.get(a["zone"])
            if since is None:
                self.prev = None
            elif self.prev != since and t - since + EPS >= a["seconds"]:
                self.prev = since
                out.append((since + a["seconds"], {"zone": a["zone"]}))
        elif e == "zone_sequence":
            want = [z.strip() for z in str(a.get("sequence", "")).split(",") if z.strip()]
            for z in eng._entered_now:
                if z in want and (not self.seq or self.seq[-1] != z):
                    self.seq.append(z)
                    self.seq = self.seq[-len(want):]
                    if self.seq == want:
                        out.append((t, {"sequence": a.get("sequence")}))
        elif e in ("speed_above", "speed_below"):
            v = eng._speed
            if v is not None and math.isfinite(v):
                thr = a["threshold"]
                if _rising(self.prev, v, (lambda x: x > thr) if e == "speed_above" else (lambda x: x < thr)):
                    out.append((t, {"value": v}))
                self.prev = v
        elif e == "distance_reaches":
            if eng._distance + EPS >= a["distance"]:
                out.append((t, {"value": eng._distance}))
                self.done = True
        elif e in ("freezing_time_reaches", "immobile_time_reaches"):
            v = eng.freeze_time if e.startswith("freezing") else eng.immobile_time
            if v + EPS >= a["seconds"]:
                out.append((t, {"value": v}))
                self.done = True
        elif e == "input_count_reaches":
            if eng._input_count(a.get("device"), a.get("channel")) >= a["count"]:
                out.append((t, {"channel": a.get("channel")}))
                self.done = True
        elif e in ("analog_above", "analog_below", "sensor_above", "sensor_below"):
            v = eng._input_value(a.get("device"), a.get("channel"))
            if v is not None:
                thr = a["threshold"]
                if _rising(self.prev, v, (lambda x: x > thr) if e.endswith("_above") else (lambda x: x < thr)):
                    out.append((t, {"value": v}))
                self.prev = v
        elif e == "pump_volume_reaches":
            v = eng._input_value(a.get("device"), f"{a.get('channel')}.infused_ml") or 0
            if v + EPS >= (a.get("volume") or 0):
                out.append((t, {"channel": a.get("channel"), "value": v}))
                self.done = True
        elif e == "encoder_reaches":
            v = eng._input_value(a.get("device"), a.get("channel")) or 0
            if abs(v) >= a["count"]:
                out.append((t, {"value": v}))
                self.done = True
        elif e == "encoder_every":
            v = eng._input_value(a.get("device"), a.get("channel"))
            if v is None:
                return out
            if self.prev is None:  # the first reading: counted from here
                self.prev = v
                return out
            step = max(1, int(a["count"] or 1))
            k = int(abs(v - self.prev) // step)
            if k:
                self.prev += k * step if v > self.prev else -k * step
                out.extend((t, {"value": v}) for _ in range(min(k, MAX_ENCODER_EVENTS)))
        elif e in ("condition_true", "condition_false"):
            v = eng._truth(self.th, self.cond, self.path)
            if _rising(self.prev, v, bool if e == "condition_true" else (lambda x: not x)):
                out.append((t, {"value": int(v)}))
            self.prev = v
        else:
            out = self._poll_extra(eng, t)
        return out

    def _poll_extra(self, eng, t: float) -> list:
        a, e = self.args, self.event
        out = []
        if e == "time_before_end":
            dur = eng.test_duration()
            due = dur - (a.get("seconds") or 0)
            if dur > 0 and t + EPS >= due:
                out.append((max(due, 0.0), {}))
                self.done = True
        elif e == "random_interval":
            n = 0
            while self.next_t <= t + EPS and n < 100:
                out.append((self.next_t, {}))
                self.next_t += eng.rng.uniform(*self.range)
                n += 1
        elif e == "time_of_day":
            now = _seconds_of_day(eng.wall_clock())
            prev, self.prev = self.prev, now
            crossed = prev < self.target <= now if now >= prev else (self.target > prev or self.target <= now)
            if crossed:
                out.append((t, {}))
                self.done = True
        elif e == "zone_not_entered":
            if eng._zones.get(a["zone"]):
                self.since, self.fired = None, False
            else:
                if self.since is None:
                    self.since = t
                if not self.fired and t - self.since + EPS >= a["seconds"]:
                    self.fired = True
                    out.append((self.since + a["seconds"], {"zone": a["zone"]}))
        elif e in ("oriented_towards", "oriented_away"):
            v = eng._orientation(a.get("target"))
            if v is not None:
                lim = a.get("angle") or 0
                if e == "oriented_towards":
                    hit = _rising(self.prev, v, lambda x: x <= lim)
                else:  # stops facing it
                    hit = self.prev is not None and self.prev <= lim < v
                if hit:
                    out.append((t, {"target": a.get("target"), "value": v}))
                self.prev = v
        elif e == "position_changed":
            pos = eng._parts.get(a.get("part") or "centre")
            if pos is not None and all(math.isfinite(c) for c in pos):
                if self.prev is None or not all(math.isfinite(c) for c in self.prev):
                    self.prev = pos
                else:
                    d = math.hypot(pos[0] - self.prev[0], pos[1] - self.prev[1])
                    if d > 1e-9 and d + EPS >= (a.get("distance") or 0):
                        out.append((t, {"value": [pos[0], pos[1]]}))
                        self.prev = pos
        elif e.startswith("encoder_") and e in EVENT_SPECS and not EVENT_SPECS[e]["generic"]:
            out = self._poll_encoder(eng, t)
        elif e == "condition_held":
            if eng._truth(self.th, self.cond, self.path):
                if self.since is None:
                    self.since = t
                if not self.fired and t - self.since + EPS >= a["seconds"]:
                    self.fired = True
                    out.append((self.since + a["seconds"], {"value": 1}))
            else:
                self.since, self.fired = None, False
        elif e == "value_changes_by":
            v = eng._num(self.th, a.get("expr"), self.path, "Value", default=None)
            if isinstance(v, (int, float)) and math.isfinite(v):
                win = a.get("seconds") or 0
                self.samples = [(tt, vv) for tt, vv in self.samples if tt >= t - win - EPS]
                if any(abs(v - vv) + EPS >= (a.get("amount") or 0) for _tt, vv in self.samples):
                    out.append((t, {"value": v}))
                    self.samples = []
                self.samples.append((t, v))
        return out

    def _poll_encoder(self, eng, t: float) -> list:
        """Rotary encoders: direction over the last 0.5 s (counts going up = clockwise) and speed in revolutions
        per minute over the last second (the channel's counts_per_rev option, default 1024)."""
        a, e = self.args, self.event
        out = []
        v = eng._input_value(a.get("device"), a.get("channel"))
        if v is None:
            return out
        self.samples.append((t, float(v)))
        while len(self.samples) > 2 and self.samples[1][0] <= t - 1.0 + EPS:
            self.samples.pop(0)
        recent = [s for s in self.samples if s[0] >= t - 0.5 - EPS]
        net = recent[-1][1] - recent[0][1] if recent else 0.0
        d = (net > 0) - (net < 0)
        if e in ("encoder_cw", "encoder_ccw"):
            want = 1 if e == "encoder_cw" else -1
            if d == want and self.dir != want:
                out.append((t, {"value": v}))
            self.dir = d
        elif e == "encoder_reversed":
            if d and self.dir and d != self.dir:
                out.append((t, {"value": d}))
            if d:
                self.dir = d
        else:
            t0, v0 = self.samples[0]
            if t - t0 < 0.2:
                return out
            dev, ch = eng._input_key(a.get("device") or "", a.get("channel"))
            try:
                cpr = float(eng._cfg(dev, ch).get("counts_per_rev") or 1024)
            except (TypeError, ValueError):
                cpr = 1024.0
            rpm = abs(float(v) - v0) / cpr / (t - t0) * 60.0
            thr = a.get("rpm") or 0
            if _rising(self.prev, rpm, (lambda x: x > thr) if e == "encoder_rpm_above" else (lambda x: x < thr)):
                out.append((t, {"value": rpm}))
            self.prev = rpm
        return out


_CONDITION_EVENTS = ("condition_true", "condition_false")


def _seconds_of_day(now) -> float:
    return now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6


def _timed_out(th, due, t) -> bool:
    if due is not None and t + EPS >= due:
        th.resume_clock, th.locals["timed_out"] = due, 1
        return True
    return False


@dataclass
class TimeWait:
    due: float

    def ready(self, eng, th, t) -> bool:
        return t + EPS >= self.due


@dataclass
class FrameWait:
    """The next frame (polling loops, and threads that used up their step budget)."""

    update_no: int

    def ready(self, eng, th, t) -> bool:
        return eng._update_no > self.update_no


@dataclass
class UntilWait:
    cond: object
    timeout: float | None
    path: tuple

    def ready(self, eng, th, t) -> bool:
        if eng._truth(th, self.cond, self.path):
            th.resume_clock, th.locals["timed_out"] = t, 0
            return True
        return _timed_out(th, self.timeout, t)


@dataclass
class EventWait:
    """Waiting for an event, or for the first of several (``others``): the local ``wait_event`` is then 1 for the
    first event, 2 for the next … (0 after a timeout)."""

    det: _Detector
    timeout: float | None
    others: list = field(default_factory=list)

    @property
    def dets(self) -> list:
        return [self.det] + self.others

    def ready(self, eng, th, t) -> bool:
        first = None
        for k, d in enumerate(self.dets):
            if d.hits and (first is None or d.hits[0][0] < first[1].hits[0][0]):
                first = (k, d)
        if first is not None:
            k, d = first
            t_occ, args = d.hits.pop(0)
            th.resume_clock = t_occ
            th.locals.update(eng._event_locals(t_occ, args))
            th.locals["timed_out"], th.locals["wait_event"] = 0, k + 1
            return True
        return _timed_out(th, self.timeout, t)
