"""What the real-time monitor shows besides the zone statistics (live.LiveStats): statistics of the apparatus's
points, of its zone sequences and of the I/O inputs, and live charts of any per-frame parameter of core.charts
(computed from the track recorded so far)."""

from __future__ import annotations

import bisect
import math
import threading
import time

import numpy as np

from . import charts
from .apparatus import Apparatus
from .sequences import find_sequences, other_zones
from .track import Track

# the parameters LiveStats keeps itself (cheap, updated every frame): key -> (label, unit pattern)
FAST_PARAMS = {"speed": ("Speed", "{u}/s"), "distance": ("Distance", "{u}"), "motion": ("Motion (% body)", "%"),
               "detected": ("Detected", ""), "freezing": ("Freezing", "")}
# charts.py parameters equivalent to a fast one (not offered twice)
_SAME_AS_FAST = {"Speed", "Distance travelled", "Motion", "Detected", "Freezing"}
CHART_PREFIX = "chart:"


def beam_angle(session) -> float:
    """Half angle (degrees) of a session's orientation beam: the angle within which the animal counts as facing an
    object in the analysis (exploration_facing_deg), so the beam shows what it is oriented towards."""
    a = getattr(getattr(session, "analysis", None), "exploration_facing_deg", 0.0) or 0.0
    return float(min(max(a, 5.0), 90.0)) if a > 0 else 20.0


# ====================================================================== points
class LivePoints:
    """Per point of interest: distance from the animal now, time near it (within its radius), approaches (entries
    into the radius) and the latency to the first approach — as the "near point" measures of the analysis."""

    def __init__(self, apparatus: Apparatus | None):
        self.set_apparatus(apparatus)
        self.distance = {p.name: math.nan for p in self.points}
        self.time_near = dict.fromkeys(self.distance, 0.0)
        self.approaches = dict.fromkeys(self.distance, 0)
        self.latency: dict[str, float | None] = dict.fromkeys(self.distance, None)
        self.near = dict.fromkeys(self.distance, False)

    def set_apparatus(self, apparatus: Apparatus | None):
        """The points (and scale) of the apparatus, e.g. after the geometry was changed during the test."""
        self.points = list(apparatus.points) if apparatus is not None else []
        self.scale = apparatus.scale if apparatus is not None else 1.0

    def update(self, t: float, dt: float, x: float, y: float, detected: bool):
        for p in self.points:
            if p.name not in self.distance:
                continue
            if detected and math.isfinite(x):
                self.distance[p.name] = math.hypot(x - p.x, y - p.y) * self.scale
                now = self.distance[p.name] <= (p.radius_cm or 0.0)
                if now and not self.near[p.name]:
                    self.approaches[p.name] += 1
                    if self.latency[p.name] is None:
                        self.latency[p.name] = t
                self.near[p.name] = now
            if self.near[p.name]:
                self.time_near[p.name] += dt

    def rows(self) -> list[tuple[str, float, float, int, float | None]]:
        """(point, distance now, time near s, approaches, latency s or None)."""
        return [(n, self.distance[n], self.time_near[n], self.approaches[n], self.latency[n]) for n in self.distance]


# ====================================================================== sequences
def sequence_rows(apparatus: Apparatus | None, visits: list, now: float) -> list[tuple[str, int, int, int, float | None]]:
    """(sequence, completed, attempts, errors, latency to the first completion or None) of every sequence of the
    apparatus, from the zone visits so far ([zone, t_in, t_out or None (still inside)], live.LiveStats.visits)."""
    if apparatus is None or not apparatus.sequences:
        return []
    entries = [(z, a, b if b is not None else now) for z, a, b in visits]
    out = []
    for seq in apparatus.sequences:
        try:
            att = find_sequences(seq, entries, other_zones(apparatus, seq) if not seq.allow_other else ())
        except Exception:  # an incomplete definition (e.g. a step zone was deleted)
            att = []
        done = [a for a in att if a.completed]
        out.append((seq.name, len(done), len(att), int(sum(a.errors for a in att)), done[0].end if done else None))
    return out


# ====================================================================== inputs
def input_rows(io_events, now: float) -> list[tuple[str, str, int, float, float | None]]:
    """Live statistics of the I/O inputs from a session's I/O log: (input, current value, activations, time on s,
    latency to the first activation or None).  Analogue / encoder inputs only report their current value.

    io_events may also be running statistics (a live session's ``input_stats``, core.iolog.InputStats): then
    nothing is rescanned (the monitor of a long test should use ``session.input_rows(now)``)."""
    if hasattr(io_events, "rows"):
        return io_events.rows(now)
    series: dict[tuple[str, str], list] = {}
    kinds: dict[tuple[str, str], str] = {}
    for e in io_events:
        if e.get("kind") != "input":
            continue
        key = (str(e.get("device", "")), str(e.get("channel", "")))
        series.setdefault(key, []).append((float(e.get("t", 0.0)), e.get("value")))
        kinds[key] = e.get("type") or "digital"
    names = [k[1] for k in series]
    out = []
    for key, ev in series.items():
        label = key[1] if names.count(key[1]) == 1 else f"{key[0]}/{key[1]}"
        last = ev[-1][1]
        if kinds[key] != "digital":
            txt = f"{last:.4g}" if isinstance(last, (int, float)) else str(last)
            out.append((label, txt, 0, math.nan, None))
            continue
        state, since, n, on, first = False, 0.0, 0, 0.0, None
        for t, v in ev:
            v = bool(v)
            if v and not state:
                n += 1
                since = t
                first = t if first is None else first
            elif not v and state:
                on += t - since
            state = v
        if state:
            on += max(0.0, now - since)
        out.append((label, "on" if state else "off", n, on, first))
    return out


# ====================================================================== charts
def chart_parameters(apparatus: Apparatus | None, behaviours=None) -> list[tuple[str, str, str]]:
    """(key, label, unit) of everything the live chart can show: the fast parameters, then every parameter of
    core.charts for this apparatus (key "chart:<name>")."""
    unit = apparatus.report_unit if apparatus is not None else "px"
    out = [(k, lbl, u.format(u=unit)) for k, (lbl, u) in FAST_PARAMS.items()]
    if apparatus is not None:
        for p in charts.parameters(apparatus, None, behaviours):
            if p.name not in _SAME_AS_FAST:
                out.append((CHART_PREFIX + p.name, p.name, p.unit))
    return out


class LiveCharts:
    """Series of core.charts parameters over the track recorded so far by a live session.  Parameters of the
    moment (positions, distances, states …) are computed over the displayed window only; running totals (counts,
    times, distance travelled) over the whole test, at most every `total_every_s` seconds (cached, recomputed in
    a background thread while the previous result is shown).  Only the frames added since the last call are
    copied under the session lock (an incremental copy of the track)."""

    def __init__(self, total_every_s: float = 1.0, behaviours=None):
        self.total_every_s = total_every_s
        self.behaviours = behaviours
        self._cache: dict[str, tuple[float, int, np.ndarray, np.ndarray]] = {}
        self._session = None  # the session the copied columns belong to
        self._cols: dict[str, list] = {}
        self._busy: set[str] = set()  # cumulative parameters being recomputed in the background
        self._lock = threading.Lock()

    def _sync(self, session) -> tuple[int, object, list]:
        """Copy the frames recorded since the last call (under the session lock); (frames, apparatus, events)."""
        with session.lock:
            n = len(session.cols["t"])
            app = session.apparatus
            if session is not self._session or n < len(self._cols.get("t", ())):
                with self._lock:
                    self._session, self._cols, self._cache = session, {}, {}
            have = len(self._cols.get("t", ()))
            new = session._track.snapshot(n) if not have else \
                {k: v[have:n] for k, v in session._track.cols.items()}
            events = [dict(e) for e in session.events]
        new.pop("outline", None)
        for k, v in new.items():
            self._cols.setdefault(k, []).extend(v)
        return n, app, events

    def series(self, session, name: str, window_s: float = 60.0) -> tuple[np.ndarray, np.ndarray]:
        """(t, values) of the chart parameter `name` (without the "chart:" prefix) over the last window_s s."""
        n, app, events = self._sync(session)
        if n < 2 or app is None:
            return np.zeros(0), np.zeros(0)
        defs = {p.name: p for p in charts.parameters(app, None, self.behaviours)}
        p = defs.get(name)
        if p is None:
            return np.zeros(0), np.zeros(0)
        cols = self._cols
        ts = cols["t"]
        t_end = ts[n - 1]
        if charts.is_cumulative(p):
            with self._lock:
                hit = self._cache.get(name)
            stale = hit is None or hit[1] > n or time.monotonic() - hit[0] >= self.total_every_s
            if hit is None:  # the first time: computed now
                tt, vv = self._compute(cols, slice(0, n), session, app, name, events)
                with self._lock:
                    self._cache[name] = (time.monotonic(), n, tt, vv)
            else:
                if stale and name not in self._busy:
                    self._busy.add(name)
                    frozen = {k: v[:n] for k, v in cols.items()}  # (appended to while it computes)
                    threading.Thread(target=self._recompute, args=(frozen, n, session, app, name, events),
                                     name="live-chart", daemon=True).start()
                tt, vv = hit[2], hit[3]
        else:
            i0 = bisect.bisect_left(ts, t_end - window_s - 2.0, 0, n)  # a little more: smoothing windows
            tt, vv = self._compute(cols, slice(i0, n), session, app, name, events)
        keep = tt >= tt[-1] - window_s if len(tt) else np.zeros(0, bool)
        return tt[keep], vv[keep]

    def _recompute(self, cols, n, session, app, name, events):
        try:
            tt, vv = self._compute(cols, slice(0, n), session, app, name, events)
            with self._lock:
                if session is self._session:
                    self._cache[name] = (time.monotonic(), n, tt, vv)
        finally:
            self._busy.discard(name)

    @staticmethod
    def _compute(cols, sl, session, app, name, events):
        tr = Track(**{k: np.asarray(v[sl], float if k != "detected" else bool) for k, v in cols.items()},
                   fps=session.fps)
        try:
            v = charts.compute(tr, app, [name], session.analysis, events)[name]
        except Exception:
            v = np.full(len(tr), np.nan)
        return tr.t, v
