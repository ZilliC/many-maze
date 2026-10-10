"""Per-frame parameters (time series) computed from a track.

`parameters()` lists what can be charted for an apparatus; `compute()` evaluates the requested
parameters (one value per track sample); plots.chart_figure() draws them over time.  The same series
feed per-test raw data exports and behaviour heat maps.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from .apparatus import Apparatus
from .freezing import struggle_index
from .project import Behaviour
from .measures import AnalysisSettings, _angle_diff, _body_angle, _head_direction, kinematics, turn_series
from .occupancy import occupancy, zone_visits
from .series import ffill, moving_average, runs
from .track import Track

STATE, VALUE, COUNT = "state", "value", "count"
_CHUNK = 4000


@dataclass
class Param:
    name: str
    unit: str = ""
    kind: str = VALUE  # value | state (0/1) | count (cumulative)
    group: str = "General"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.unit})" if self.unit else self.name


def _chunked(fn, x, y) -> np.ndarray:
    """fn(x, y) over chunks (distance-to-outline helpers allocate N × edges arrays)."""
    n = len(x)
    if n <= _CHUNK:
        return np.asarray(fn(x, y), float)
    return np.concatenate([np.asarray(fn(x[i:i + _CHUNK], y[i:i + _CHUNK]), float) for i in range(0, n, _CHUNK)])


def _seg_distance(x, y, x1, y1, x2, y2) -> np.ndarray:
    ax, ay = x2 - x1, y2 - y1
    den = ax * ax + ay * ay or 1e-12
    t = np.clip(((x - x1) * ax + (y - y1) * ay) / den, 0, 1)
    return np.hypot(x - (x1 + t * ax), y - (y1 + t * ay))


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def _unwrapped_rate(t, deg) -> np.ndarray:
    """Derivative (deg/s) of an angle series, NaN where the angle is missing."""
    out = np.full(len(t), np.nan)
    ok = np.isfinite(deg)
    if ok.sum() < 2:
        return out
    idx = np.flatnonzero(ok)
    u = np.degrees(np.unwrap(np.radians(deg[idx])))
    d = np.diff(u) / np.maximum(np.diff(t[idx]), 1e-9)
    out[idx[1:]] = d
    return out


class _Ctx:
    """Lazily computed shared quantities for one track."""

    def __init__(self, track: Track, app: Apparatus, s: AnalysisSettings, events, behaviours, others):
        self.tr, self.app, self.s = track, app, s
        self.events, self.behaviours, self.others = events or [], behaviours or [], others or []
        self.k = kinematics(track, app, s)
        self.scale = app.scale
        self._memb = self._hmemb = None
        self.hx, self.hy = ffill(track.hx), ffill(track.hy)

    @property
    def win1s(self) -> int:
        return max(1, int(round(1.0 / max(self.tr.dt, 1e-6))))

    def memb(self):
        """Zone / group occupancy as the analysis computes it (entry rules, body proportion, investigation
        distance, hidden zones)."""
        if self._memb is None:
            self._memb, self._hmemb, _ = occupancy(self.tr, self.app, self.s)
        return self._memb

    def hmemb(self):
        if self._hmemb is None:
            self.memb()
            if self._hmemb is None:
                self._hmemb = occupancy(self.tr, self.app, self.s, part="head")[0]
        return self._hmemb

    def arena(self):
        try:
            return self.app.arena_or_bounds()
        except ValueError:
            return None

    def zone_shape(self, name):
        z = self.app.zone(name)
        if z is not None:
            return [z.shape]
        g = self.app.group(name)
        if g is None:
            return []
        return [self.app.zone(n).shape for n in list(g.zones) + list(g.exclude) if self.app.zone(n) is not None]

    def visits_mask(self, inside):
        m = np.zeros(len(self.k.t), bool)
        for a, b in zone_visits(self.k.t, self.k.dur, inside, self.s, all_runs=True):
            m[a:b] = True
        return m


def _definitions(app: Apparatus, track: Track | None, behaviours, n_others: int) -> "OrderedDict[str, tuple]":
    """name -> (Param, fn(ctx) -> array)."""
    u = app.unit
    head = track is None or track.has_head()
    orient = track is None or np.isfinite(track.angle).any() or head
    d: OrderedDict[str, tuple] = OrderedDict()

    def add(name, unit, kind, group, fn):
        d[name] = (Param(name, unit, kind, group), fn)

    def cum(c, v):
        return np.cumsum(np.where(np.isfinite(v), v, 0.0))

    # ---- position -----------------------------------------------------------------
    add("X position", u, VALUE, "Position", lambda c: c.k.x * c.scale)
    add("Y position", u, VALUE, "Position", lambda c: c.k.y * c.scale)
    if head:
        add("Head X position", u, VALUE, "Position", lambda c: c.hx * c.scale)
        add("Head Y position", u, VALUE, "Position", lambda c: c.hy * c.scale)
        add("Tail X position", u, VALUE, "Position", lambda c: ffill(c.tr.tx) * c.scale)
        add("Tail Y position", u, VALUE, "Position", lambda c: ffill(c.tr.ty) * c.scale)
    add("Distance from start", u, VALUE, "Position", _dist_from_start)
    add("Distance from arena centre", u, VALUE, "Position", _dist_centre)
    add("Distance from wall", u, VALUE, "Position", _dist_wall)
    add("In arena", "", STATE, "Position", _in_arena)
    add("Near wall (thigmotaxis)", "", STATE, "Position", _near_wall)
    add("Detected", "", STATE, "Position", lambda c: c.tr.detected.astype(float))
    # ---- locomotion ---------------------------------------------------------------
    add("Speed", f"{u}/s", VALUE, "Locomotion", lambda c: c.k.speed)
    add("Smoothed speed (1 s)", f"{u}/s", VALUE, "Locomotion", lambda c: moving_average(c.k.speed, c.win1s))
    add("Acceleration", f"{u}/s²", VALUE, "Locomotion", _acceleration)
    add("Distance travelled", u, VALUE, "Locomotion", lambda c: np.cumsum(c.k.step))
    add("Distance in last second", u, VALUE, "Locomotion",
        lambda c: np.convolve(c.k.step, np.ones(c.win1s), mode="full")[:len(c.k.step)])
    add("Path efficiency", "", VALUE, "Locomotion", _path_eff)
    add("Mobile", "", STATE, "Locomotion", lambda c: c.k.mobile.astype(float))
    add("Immobile", "", STATE, "Locomotion", lambda c: _immobile(c).astype(float))
    add("Time mobile", "s", VALUE, "Locomotion", lambda c: np.cumsum(np.where(c.k.mobile, c.k.dur, 0)))
    add("Time immobile", "s", VALUE, "Locomotion", lambda c: np.cumsum(np.where(_immobile(c), c.k.dur, 0)))
    add("Immobile episodes", "", COUNT, "Locomotion", lambda c: _episode_count(_immobile(c)))
    # ---- motion / freezing -------------------------------------------------------------
    add("Motion", "% body", VALUE, "Freezing", lambda c: c.k.motion_pct)
    add("Freezing", "", STATE, "Freezing", lambda c: c.k.freezing.astype(float))
    add("Time freezing", "s", VALUE, "Freezing", lambda c: np.cumsum(np.where(c.k.freezing, c.k.dur, 0)))
    add("Freezing episodes", "", COUNT, "Freezing", lambda c: _episode_count(c.k.freezing))
    # forced swim / tail suspension: what immobility is detected from in that mode (freezing.struggle_index)
    add("Struggle index", "% body", VALUE, "Freezing",
        lambda c: c.k.struggle if c.k.struggle is not None else struggle_index(c.k.motion_pct, c.tr.dt, c.k.breaks))
    # ---- direction / body -------------------------------------------------------------
    add("Movement direction", "deg", VALUE, "Direction", lambda c: c.k.heading)
    add("Turn rate", "deg/s", VALUE, "Direction", lambda c: _unwrapped_rate(c.k.t, c.k.heading))
    # as the result: the turns of the direction of travel between the frames in which the animal moves
    add("Absolute turn angle", "deg", VALUE, "Direction",
        lambda c: cum(c, turn_series(c.k.heading, c.k.speed, c.s.mobility_threshold, c.k.breaks)[0]))
    if orient:
        add("Head angle", "deg", VALUE, "Direction", _orientation)
        add("Angular velocity", "deg/s", VALUE, "Direction", lambda c: _unwrapped_rate(c.k.t, _orientation(c)))
        add("Cumulative rotation", "deg", VALUE, "Direction", _cum_rotation)
    if head:
        add("Head speed", f"{u}/s", VALUE, "Body", _head_speed)
        add("Body length", u, VALUE, "Body",
            lambda c: np.hypot(c.tr.hx - c.tr.tx, c.tr.hy - c.tr.ty) * c.scale)
    add("Body area", f"{u}²", VALUE, "Body", lambda c: c.tr.area * c.scale * c.scale)
    add("Elongation", "", VALUE, "Body", _elongation)
    # ---- zones ------------------------------------------------------------------------------
    for zn in [z.name for z in app.zones] + [g.name for g in app.groups]:
        add(f"{zn}: in zone", "", STATE, "Zones", lambda c, zn=zn: c.memb()[zn].astype(float))
        if head:
            add(f"{zn}: head in zone", "", STATE, "Zones", lambda c, zn=zn: c.hmemb()[zn].astype(float))
        add(f"{zn}: distance to zone", u, VALUE, "Zones", lambda c, zn=zn: _dist_zone(c, zn))
        add(f"{zn}: time in zone", "s", VALUE, "Zones",
            lambda c, zn=zn: np.cumsum(np.where(c.visits_mask(c.memb()[zn]), c.k.dur, 0)))
        add(f"{zn}: entries", "", COUNT, "Zones",
            lambda c, zn=zn: _entry_count(c.k.t, c.k.dur, c.memb()[zn], c.s))
    # ---- points -----------------------------------------------------------------------------
    for p in app.points:
        add(f"{p.name}: distance", u, VALUE, "Points", lambda c, p=p: np.hypot(c.k.x - p.x, c.k.y - p.y) * c.scale)
        add(f"{p.name}: near", "", STATE, "Points", lambda c, p=p: _point_near(c, p).astype(float))
        if head:
            add(f"{p.name}: head distance", u, VALUE, "Points",
                lambda c, p=p: np.hypot(c.hx - p.x, c.hy - p.y) * c.scale)
        if orient:
            add(f"{p.name}: head-to-point angle", "deg", VALUE, "Points", lambda c, p=p: _head_point_angle(c, p))
    # ---- lines ------------------------------------------------------------------------------
    for ln in app.lines:
        add(f"{ln.name}: distance", u, VALUE, "Lines",
            lambda c, ln=ln: _seg_distance(c.k.x, c.k.y, ln.x1, ln.y1, ln.x2, ln.y2) * c.scale)
        add(f"{ln.name}: crossings", "", COUNT, "Lines", lambda c, ln=ln: _line_crossings(c, ln))
    # ---- manual scoring --------------------------------------------------------------------
    for b in behaviours or []:
        name = b.name
        if b.kind == "point":
            add(f"{name}: count", "", COUNT, "Behaviours", lambda c, name=name: _event_count(c, name))
        else:
            add(f"{name}: active", "", STATE, "Behaviours", lambda c, name=name: _event_state(c, name))
    # ---- other animals ---------------------------------------------------------------------
    for j in range(n_others):
        add(f"Animal {j + 2}: distance", u, VALUE, "Social", lambda c, j=j: _other_distance(c, j))
    return d


# ---- parameter implementations --------------------------------------------------------------
def _dist_from_start(c):
    ok = np.flatnonzero(np.isfinite(c.k.x))
    if not len(ok):
        return np.full(len(c.k.t), np.nan)
    return np.hypot(c.k.x - c.k.x[ok[0]], c.k.y - c.k.y[ok[0]]) * c.scale


def _dist_centre(c):
    a = c.arena()
    if a is None:
        return np.full(len(c.k.t), np.nan)
    cx, cy = a.centroid()
    return np.hypot(c.k.x - cx, c.k.y - cy) * c.scale


def _dist_wall(c):
    a = c.arena()
    if a is None:
        return np.full(len(c.k.t), np.nan)
    return _chunked(a.distance_to_edge, c.k.x, c.k.y) * c.scale


def _in_arena(c):
    a = c.arena()
    return (a.contains(c.k.x, c.k.y) if a is not None else np.ones(len(c.k.t), bool)).astype(float)


def _near_wall(c):
    a = c.arena()
    if a is None:
        return np.zeros(len(c.k.t))
    thr = c.s.thigmotaxis_distance
    if not thr or thr <= 0:
        x0, y0, x1, y1 = a.bounds()
        thr = 0.25 * min(x1 - x0, y1 - y0) / 2 * c.scale
    return ((_dist_wall(c) <= thr) & a.contains(c.k.x, c.k.y)).astype(float)


def _acceleration(c):
    sp = moving_average(c.k.speed, c.win1s)
    if len(sp) < 2:
        return np.full(len(sp), np.nan)
    return np.gradient(sp, c.k.t)


def _path_eff(c):
    ok = np.flatnonzero(np.isfinite(c.k.ux))
    out = np.full(len(c.k.t), np.nan)
    if not len(ok):
        return out
    straight = np.hypot(c.k.ux - c.k.ux[ok[0]], c.k.uy - c.k.uy[ok[0]])
    total = np.cumsum(c.k.step)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(total > 0, straight / total, np.nan)
    return out


def _episode_count(mask):
    m = np.asarray(mask, bool)
    start = np.zeros(len(m))
    for a, _ in runs(m):
        start[a] = 1
    return np.cumsum(start)


def _entry_count(t, dur, inside, s):
    start = np.zeros(len(t))
    for a, _ in zone_visits(t, dur, inside, s):
        start[a] = 1
    return np.cumsum(start)


def _immobile(c):
    """Not mobile, at a known position (as the result: an animal never detected is not immobile)."""
    return ~c.k.mobile & np.isfinite(c.k.x) & np.isfinite(c.k.y)


def _orientation(c):
    """The orientation of the animal as the results define it: the direction from the centre to the head, else
    the tracked body angle, else (neither tracked) the direction of travel."""
    a = _body_angle(c.tr)
    return a if a is not None else c.k.heading


def _point_near(c, p):
    """Near a point as the result: the head (when tracked, else the centre) within the point's radius."""
    if c.tr.has_head():
        d = np.hypot(c.hx - p.x, c.hy - p.y) * c.scale
    else:
        d = np.hypot(c.k.x - p.x, c.k.y - p.y) * c.scale
    with np.errstate(invalid="ignore"):
        return d <= (p.radius_cm or 0)


def _cum_rotation(c):
    a = _orientation(c)
    out = np.full(len(a), np.nan)
    ok = np.isfinite(a)
    if ok.sum() < 2:
        return out
    idx = np.flatnonzero(ok)
    u = np.degrees(np.unwrap(np.radians(a[idx])))
    out[idx] = u - u[0]
    return ffill(out)


def _head_speed(c):
    hx, hy = c.hx * c.scale, c.hy * c.scale
    out = np.full(len(hx), np.nan)
    if len(hx) > 1:
        out[1:] = np.hypot(np.diff(hx), np.diff(hy)) / np.maximum(np.diff(c.k.t), 1e-9)
    return moving_average(out, max(1, c.win1s // 5))


def _elongation(c):
    L = np.hypot(c.tr.hx - c.tr.tx, c.tr.hy - c.tr.ty)
    with np.errstate(invalid="ignore", divide="ignore"):
        return L / np.sqrt(np.where(c.tr.area > 0, c.tr.area, np.nan))


def _dist_zone(c, zn):
    shapes = c.zone_shape(zn)
    if not shapes:
        return np.full(len(c.k.t), np.nan)
    inside = c.memb().get(zn, np.zeros(len(c.k.t), bool))
    if c.app.group(zn) is not None and c.app.zone(zn) is None:
        d = np.min([_chunked(s.distance_to_edge, c.k.x, c.k.y) for s in shapes], axis=0)
    else:
        d = _chunked(shapes[0].distance_to_edge, c.k.x, c.k.y)
    return np.where(inside, 0.0, d) * c.scale


def _head_point_angle(c, p):
    """Angle (deg, 0 = facing the point) between body orientation and the head→point direction (as the result
    "mean head angle": the direction from the centre to the head, forward filled, vs the head→point direction)."""
    hd = _head_direction(c.tr)
    if np.isfinite(hd).any():
        return _angle_diff(np.arctan2(p.y - c.hy, p.x - c.hx), np.radians(ffill(hd)))
    ang = np.radians(_orientation(c))
    hx = np.where(np.isfinite(c.hx), c.hx, c.k.x)
    hy = np.where(np.isfinite(c.hy), c.hy, c.k.y)
    to = np.arctan2(p.y - hy, p.x - hx)
    return np.abs(np.degrees((to - ang + np.pi) % (2 * np.pi) - np.pi))


def _line_crossings(c, ln):
    from .geometry import segments_intersect

    P = np.column_stack([c.k.x, c.k.y])
    out = np.zeros(len(P))
    if len(P) > 1:
        hit, _ = segments_intersect(P[:-1], P[1:], (ln.x1, ln.y1), (ln.x2, ln.y2))
        out[1:] = hit
    return np.cumsum(out)


def _event_state(c, name):
    t = c.k.t
    m = np.zeros(len(t))
    for e in c.events:
        if e.get("behaviour") != name:
            continue
        t1 = e.get("t_end")
        t1 = t[-1] + c.tr.dt if t1 is None else t1
        m[(t >= e["t"]) & (t < t1)] = 1
    return m


def _event_count(c, name):
    t = c.k.t
    ts = np.sort([e["t"] for e in c.events if e.get("behaviour") == name])
    return np.searchsorted(ts, t, side="right").astype(float)


def _other_distance(c, j):
    o = c.others[j]
    n = min(len(o), len(c.k.t))
    out = np.full(len(c.k.t), np.nan)
    out[:n] = np.hypot(ffill(o.x[:n]) - c.k.x[:n], ffill(o.y[:n]) - c.k.y[:n]) * c.scale
    return out


# ---- public API -------------------------------------------------------------------------
def parameters(app: Apparatus, track: Track | None = None, behaviours: list[Behaviour] | None = None,
               n_others: int = 0) -> list[Param]:
    """Parameters that can be computed for this apparatus (and track: head/orientation availability)."""
    return [p for p, _ in _definitions(app, track, behaviours, n_others).values()]


def compute(track: Track, app: Apparatus, names: list[str] | None = None, settings: AnalysisSettings | None = None,
            events: list | None = None, behaviours: list[Behaviour] | None = None,
            other_tracks: list[Track] | None = None
            ) -> "OrderedDict[str, np.ndarray]":
    """Evaluate the named parameters (all if None) for each track sample. Unknown names raise KeyError."""
    defs = _definitions(app, track, behaviours, len(other_tracks or []))
    names = list(defs) if names is None else list(names)
    if not len(track):
        return OrderedDict((n, np.zeros(0)) for n in names)
    ctx = _Ctx(track, app, settings or AnalysisSettings(), events, behaviours, other_tracks)
    out: OrderedDict[str, np.ndarray] = OrderedDict()
    for n in names:
        if n not in defs:
            raise KeyError(f"Unknown parameter: {n}")
        with np.errstate(invalid="ignore", divide="ignore"):
            out[n] = np.asarray(defs[n][1](ctx), float)
    return out


_RUNNING_TOTALS = {"Distance travelled", "Time mobile", "Time immobile", "Time freezing", "Absolute turn angle",
                   "Cumulative rotation", "Distance from start", "Path efficiency"}


def is_cumulative(p: Param) -> bool:
    """A running total since the start of the test (counts, times, distance travelled …): its value at a moment
    depends on the whole track up to it, not only on the last few seconds (live charts compute these over the
    whole test)."""
    return p.kind == COUNT or p.name in _RUNNING_TOTALS or p.name.endswith(": time in zone")


def param_info(app: Apparatus, name: str, track: Track | None = None, behaviours=None, n_others: int = 3) -> Param:
    d = _definitions(app, track, behaviours, n_others)
    return d[name][0] if name in d else Param(name)


def per_frame_table(track: Track, app: Apparatus, settings=None, events=None, behaviours=None,
                    names: list[str] | None = None, other_tracks=None) -> tuple[list[str], np.ndarray]:
    """(column labels, 2-D array) of time, raw track columns and derived per-frame parameters."""
    data = compute(track, app, names, settings, events, behaviours, other_tracks)
    defs = _definitions(app, track, behaviours, len(other_tracks or []))
    cols = ["Time (s)"] + [defs[n][0].label for n in data]
    arr = np.column_stack([track.t] + list(data.values())) if len(track) else np.zeros((0, len(cols)))
    return cols, arr


def state_mask(track: Track, app: Apparatus, which: str, settings=None, events=None, behaviours=None) -> np.ndarray:
    """Boolean per-frame mask of a state parameter (e.g. "Freezing", "Rearing: active", "Centre: in zone")."""
    v = compute(track, app, [which], settings, events, behaviours)[which]
    return np.nan_to_num(v) > 0.5


# ---- measurements on a series --------------------------------------------------------------
def measure_interval(t: np.ndarray, v: np.ndarray, t0: float | None = None, t1: float | None = None) -> dict:
    """Statistics of a series within [t0, t1]: mean, SD, min/max (and when), change and time integral."""
    t = np.asarray(t, float)
    v = np.asarray(v, float)
    t0 = t[0] if t0 is None and len(t) else t0
    t1 = t[-1] if t1 is None and len(t) else t1
    m = (t >= min(t0, t1)) & (t <= max(t0, t1)) & np.isfinite(v)
    out = {"t0": float(min(t0, t1)), "t1": float(max(t0, t1)), "duration": float(abs(t1 - t0)), "n": int(m.sum())}
    if not m.any():
        out.update(mean=math.nan, sd=math.nan, min=math.nan, max=math.nan, t_min=math.nan, t_max=math.nan,
                   change=math.nan, integral=math.nan)
        return out
    tt, vv = t[m], v[m]
    out.update(mean=float(vv.mean()), sd=float(vv.std(ddof=1)) if len(vv) > 1 else math.nan,
               min=float(vv.min()), max=float(vv.max()), t_min=float(tt[vv.argmin()]), t_max=float(tt[vv.argmax()]),
               change=float(vv[-1] - vv[0]), integral=float(np.trapezoid(vv, tt)) if len(vv) > 1 else 0.0)
    return out


def find_peaks(t: np.ndarray, v: np.ndarray, prominence: float | None = None, min_separation_s: float = 0.0,
               valleys: bool = False) -> list[tuple[float, float]]:
    """Peaks (or valleys) of a series as (time, value); prominence defaults to 1 SD of the series."""
    from scipy.signal import find_peaks as _fp

    t = np.asarray(t, float)
    v = np.asarray(v, float)
    ok = np.isfinite(v)
    if ok.sum() < 3:
        return []
    vv = np.where(ok, v, np.nanmedian(v))
    sig = -vv if valleys else vv
    if prominence is None:
        prominence = float(np.nanstd(vv)) or None
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    dist = max(1, int(round(min_separation_s / dt))) if min_separation_s > 0 else None
    idx, _ = _fp(sig, prominence=prominence, distance=dist)
    return [(float(t[i]), float(v[i])) for i in idx]


# ---- zone occupancy bands ---------------------------------------------------------------------------
def zone_bands(track: Track, app: Apparatus, zones: list[str], settings=None) -> list[tuple[str, str, list]]:
    """[(zone, colour, [(t_start, t_end), ...])] occupancy intervals for background bands."""
    s = settings or AnalysisSettings()
    if not len(track):
        return []
    memb = occupancy(track, app, s)[0]
    t, dur = track.t, track.frame_durations()
    palette = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"]
    out = []
    for i, zn in enumerate(zones):
        if zn not in memb:
            continue
        z = app.zone(zn)
        col = z.color if z is not None else palette[i % len(palette)]
        spans = [(float(t[a]), float(t[b - 1] + dur[b - 1])) for a, b in zone_visits(t, dur, memb[zn], s, all_runs=True)]
        out.append((zn, col, spans))
    return out
