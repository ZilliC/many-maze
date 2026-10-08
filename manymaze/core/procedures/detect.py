"""What handlers and threads wait for: event detectors (stateful watchers for one event with its parameters)
and the wait conditions of a thread."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .catalog import EPS, EVENT_SPECS


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
        for p in self.spec["params"]:
            v = st.get(p["name"], p["default"])
            if p["type"] in ("number", "int"):
                v = None if v in (None, "") else eng._num(th, v, path, what=p["label"])
            elif p["type"] != "expr":
                v = "" if v is None else str(v).strip()
            if p["type"] == "device" and v:
                v = eng._devname(v)
            self.args[p["name"]] = v
        self.cond = st.get("cond")
        self.th, self.path = th, path
        self.done = False
        self.prev = None
        self.seq: list[str] = []
        a, e = self.args, self.event
        if e == "every":
            iv = a.get("interval") or 0
            self.next_t = a["first"] if a.get("first") not in (None, "") else iv
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
        if e == "encoder_every":
            self.prev = eng._input_value(a.get("device"), a.get("channel")) or 0

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
            v = eng._input_value(a.get("device"), a.get("channel")) or 0
            step = max(1, int(a["count"] or 1))
            while abs(v - self.prev) >= step:
                self.prev += step if v > self.prev else -step
                out.append((t, {"value": v}))
        elif e in ("condition_true", "condition_false"):
            v = eng._truth(self.th, self.cond, self.path)
            if _rising(self.prev, v, bool if e == "condition_true" else (lambda x: not x)):
                out.append((t, {"value": int(v)}))
            self.prev = v
        return out


_CONDITION_EVENTS = ("condition_true", "condition_false")


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
