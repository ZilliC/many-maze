"""Behavioural measures computed from a track and an apparatus.

`analyse()` returns an ordered dict of measure name -> value (float / int / str).
Measures are grouped into:

* general locomotion (distance, speed, mobility, freezing, rotations, path shape)
* per zone / zone group (time, entries, latency, distance, head entries, ...)
* per point of interest (distance to, time near, time exploring / facing)
* per line (crossings in each direction)
* test-specific measures chosen from the apparatus template (EPM, Y maze,
  water maze, Barnes maze, NOR, light/dark, three-chamber, radial arm, T maze)
* manually scored behaviours
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field

import numpy as np

from .apparatus import Apparatus
from .geometry import segments_intersect
from .track import Track


@dataclass
class AnalysisSettings:
    speed_smoothing_s: float = 0.2  # positions are smoothed over this window before distance/speed
    mobility_threshold: float = 2.0  # speed (units/s) below which the animal is immobile
    min_immobile_s: float = 2.0  # immobile episodes shorter than this count as mobile
    freeze_on_pct: float = 2.0  # motion (% of body area changing) below which freezing starts
    freeze_off_pct: float = 3.0  # motion above which freezing ends (hysteresis)
    min_freeze_s: float = 1.0
    entry_min_duration_s: float = 0.0  # zone visits shorter than this are ignored
    count_initial_entry: bool = True  # an animal starting in a zone has entered it
    latency_if_never: str = "duration"  # "duration" (cap at test length) or "blank"
    zone_body_part: str = "centre"  # body part used for zone occupancy: centre | head | tail
    thigmotaxis_distance: float = 0.0  # units from the arena wall; 0 = 25 % of arena half-width
    rotation_reset_deg: float = 90.0
    bin_length_s: float = 0.0  # time bins for segmented results; 0 = none
    custom_periods: list = field(default_factory=list)  # [[label, t0, t1], ...]
    novel_object: str = "Object B"  # for the novel object test
    social_side: str = "Left"  # three chamber: side holding the stranger animal
    exploration_facing_deg: float = 45.0
    grid_cells: int = 4  # open field: N×N grid for line-crossing counts (0 = off)
    contact_distance: float = 0.0  # inter-animal contact distance (units); 0 = auto (1 body length)
    measure_filter: list = field(default_factory=list)  # optional list of measure names to keep

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AnalysisSettings":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# helpers

def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return [start, end) index pairs of consecutive True runs."""
    m = np.asarray(mask, bool)
    if m.size == 0:
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def drop_short_runs(mask: np.ndarray, t: np.ndarray, dur: np.ndarray, min_s: float, value: bool = True) -> np.ndarray:
    """Remove runs of `value` shorter than min_s (they are set to the opposite value)."""
    if min_s <= 0:
        return mask
    m = np.asarray(mask, bool).copy()
    target = m if value else ~m
    for s, e in runs(target):
        if dur[s:e].sum() < min_s - 1e-9:
            m[s:e] = not value
    return m


def ffill(v: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs (then back-fill leading NaNs)."""
    v = np.asarray(v, float).copy()
    ok = np.isfinite(v)
    if not ok.any():
        return v
    idx = np.where(ok, np.arange(len(v)), 0)
    np.maximum.accumulate(idx, out=idx)
    v = v[idx]
    first = np.flatnonzero(ok)[0]
    v[:first] = v[first]
    return v


def moving_average(v: np.ndarray, window: int) -> np.ndarray:
    if window < 2 or len(v) < 2:
        return v.copy()
    window = int(window) | 1
    ok = np.isfinite(v)
    vv = np.where(ok, v, 0.0)
    k = np.ones(window)
    num = np.convolve(vv, k, mode="same")
    den = np.convolve(ok.astype(float), k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~ok] = np.nan
    return out


def _latency(t, mask, t0, duration, settings) -> float:
    idx = np.flatnonzero(mask)
    if len(idx):
        return float(t[idx[0]] - t0)
    return float(duration) if settings.latency_if_never == "duration" else math.nan


def _r(v, nd=3):
    if v is None:
        return math.nan
    if isinstance(v, (int, np.integer)):
        return int(v)
    v = float(v)
    return round(v, nd) if math.isfinite(v) else math.nan


@dataclass
class Kinematics:
    t: np.ndarray
    dur: np.ndarray
    x: np.ndarray  # px, forward filled
    y: np.ndarray
    ux: np.ndarray  # units, smoothed
    uy: np.ndarray
    step: np.ndarray  # distance travelled into each sample (units)
    speed: np.ndarray  # units/s
    mobile: np.ndarray
    freezing: np.ndarray
    motion_pct: np.ndarray
    heading: np.ndarray  # movement direction (deg)
    t0: float
    duration: float
    scale: float
    unit: str


def kinematics(track: Track, app: Apparatus, s: AnalysisSettings, t0: float | None = None,
               duration: float | None = None) -> Kinematics:
    t = track.t
    dur = track.frame_durations()
    scale = app.scale
    unit = app.unit
    x = ffill(track.x)
    y = ffill(track.y)
    win = max(1, int(round(s.speed_smoothing_s / max(track.dt, 1e-6))))
    ux = moving_average(x, win) * scale
    uy = moving_average(y, win) * scale
    step = np.zeros(len(t))
    if len(t) > 1:
        d = np.hypot(np.diff(ux), np.diff(uy))
        step[1:] = np.nan_to_num(d)
    dts = np.diff(t, prepend=t[0] - track.dt) if len(t) else np.zeros(0)
    dts = np.where(dts <= 0, track.dt, dts)
    speed = step / dts
    # mobility: smoothed speed over ~0.5 s
    sp_s = moving_average(speed, max(1, int(round(0.5 / max(track.dt, 1e-6)))))
    mobile = np.nan_to_num(sp_s) >= s.mobility_threshold
    mobile = drop_short_runs(mobile, t, dur, s.min_immobile_s, value=False)
    # freezing from pixel change normalised by body area
    area = track.area
    med_area = np.nanmedian(area) if np.isfinite(area).any() else math.nan
    if np.isfinite(track.motion).any() and med_area and math.isfinite(med_area) and med_area > 0:
        motion_pct = track.motion / med_area * 100.0
        freezing = np.zeros(len(t), bool)
        state = False
        for i, mp in enumerate(motion_pct):
            if not math.isfinite(mp):
                freezing[i] = state
                continue
            if state:
                state = mp <= s.freeze_off_pct
            else:
                state = mp < s.freeze_on_pct
            freezing[i] = state
        freezing = drop_short_runs(freezing, t, dur, s.min_freeze_s, value=True)
    else:
        motion_pct = np.full(len(t), np.nan)
        freezing = np.zeros(len(t), bool)
    heading = np.full(len(t), np.nan)
    if len(t) > 1:
        heading[1:] = np.degrees(np.arctan2(np.diff(uy), np.diff(ux)))
        heading[step < 1e-6] = np.nan
    if t0 is None:
        t0 = float(t[0]) if len(t) else 0.0
    if duration is None:
        duration = float(dur.sum())
    return Kinematics(t, dur, x, y, ux, uy, step, speed, mobile, freezing, motion_pct, heading, t0, duration,
                      scale, unit)


def count_rotations(angle_deg: np.ndarray, reset_deg: float = 90.0) -> tuple[int, int]:
    """Count full 360° rotations (clockwise, anticlockwise) of an orientation series.

    A rotation is counted when the cumulative turn since the last reference reaches 360°
    in one direction; the reference is reset if the animal turns back by more than reset_deg.
    In image coordinates (y down) a positive angle change is clockwise.
    """
    a = np.asarray(angle_deg, float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return 0, 0
    u = np.degrees(np.unwrap(np.radians(a)))
    cw = acw = 0
    ref = u[0]
    hi = lo = u[0]
    for v in u[1:]:
        hi = max(hi, v)
        lo = min(lo, v)
        if v - ref >= 360:
            cw += 1
            ref = v
            hi = lo = v
        elif ref - v >= 360:
            acw += 1
            ref = v
            hi = lo = v
        elif hi - v > reset_deg and hi > ref:
            ref = v
            hi = lo = v
        elif v - lo > reset_deg and lo < ref:
            ref = v
            hi = lo = v
    return cw, acw


def zone_visits(t: np.ndarray, dur: np.ndarray, inside: np.ndarray, s: AnalysisSettings) -> list[tuple[int, int]]:
    m = drop_short_runs(inside, t, dur, s.entry_min_duration_s, value=True)
    v = runs(m)
    if not s.count_initial_entry and v and v[0][0] == 0:
        v = v[1:]
    return v


def zone_sequence(track: Track, app: Apparatus, zone_names: list[str], s: AnalysisSettings,
                  part: str | None = None) -> list[tuple[str, float, float]]:
    """Ordered list of (zone, t_enter, t_exit) entries into any of the given zones."""
    px, py = track.bodypart(part or s.zone_body_part)
    x, y = ffill(px), ffill(py)
    t, dur = track.t, track.frame_durations()
    memb = app.zone_membership(x, y)
    seq = []
    for zn in zone_names:
        if zn not in memb:
            continue
        for a, b in zone_visits(t, dur, memb[zn], s):
            seq.append((zn, float(t[a]), float(t[b - 1] + dur[b - 1])))
    seq.sort(key=lambda e: e[1])
    return seq


# ---------------------------------------------------------------------------

def analyse(track: Track, app: Apparatus, s: AnalysisSettings | None = None, events: list | None = None,
            behaviours: list | None = None, t_range: tuple[float, float] | None = None,
            duration: float | None = None, other_tracks: list[Track] | None = None) -> "OrderedDict[str, object]":
    """Compute all applicable measures for one animal's track."""
    s = s or AnalysisSettings()
    full = track
    if t_range is not None:
        track = track.slice_time(*t_range)
        t0 = t_range[0]
        duration = min(t_range[1], full.t[-1] + full.dt if len(full) else t_range[1]) - t_range[0]
    else:
        t0 = float(track.t[0]) if len(track) else 0.0
    res: OrderedDict[str, object] = OrderedDict()
    u = app.unit
    if len(track) == 0:
        res["Test duration (s)"] = 0.0
        return res
    k = kinematics(track, app, s, t0=t0, duration=duration)
    t, dur = k.t, k.dur
    T = k.duration
    res["Test duration (s)"] = _r(T)
    res["Detection (%)"] = _r(100.0 * track.detected.mean(), 1)

    # ---- locomotion ---------------------------------------------------------
    total = float(k.step.sum())
    res[f"Total distance ({u})"] = _r(total, 2)
    res[f"Mean speed ({u}/s)"] = _r(total / T if T > 0 else math.nan)
    sp = moving_average(k.speed, max(1, int(round(0.2 / max(track.dt, 1e-6)))))
    res[f"Max speed ({u}/s)"] = _r(np.nanmax(sp) if np.isfinite(sp).any() else math.nan)
    t_mob = float(dur[k.mobile].sum())
    res["Time mobile (s)"] = _r(t_mob)
    res["Time immobile (s)"] = _r(T - t_mob)
    imm = runs(~k.mobile)
    res["Immobile episodes"] = len(imm)
    res["Latency to first immobility (s)"] = _r(_latency(t, ~k.mobile, t0, T, s))
    res[f"Mean speed while mobile ({u}/s)"] = _r(k.step[k.mobile].sum() / t_mob if t_mob > 0 else math.nan)
    if np.isfinite(k.motion_pct).any():
        fr = runs(k.freezing)
        t_fr = float(dur[k.freezing].sum())
        res["Time freezing (s)"] = _r(t_fr)
        res["Freezing (%)"] = _r(100 * t_fr / T if T > 0 else math.nan, 2)
        res["Freezing episodes"] = len(fr)
        res["Latency to first freezing (s)"] = _r(_latency(t, k.freezing, t0, T, s))
        res["Mean freezing episode (s)"] = _r(t_fr / len(fr) if fr else 0.0)
        res["Mean motion (% body)"] = _r(np.nanmean(k.motion_pct), 2)
    # path shape
    ok = np.isfinite(k.ux)
    if ok.sum() >= 2:
        i0, i1 = np.flatnonzero(ok)[[0, -1]]
        straight = math.hypot(k.ux[i1] - k.ux[i0], k.uy[i1] - k.uy[i0])
        res["Path efficiency"] = _r(straight / total if total > 0 else math.nan)
    h = k.heading.copy()
    moving = np.isfinite(h) & (k.speed > max(s.mobility_threshold, 1e-9))
    hv = h[moving]
    if len(hv) > 1:
        dturn = np.abs((np.diff(hv) + 180) % 360 - 180)
        abs_turn = float(dturn.sum())
    else:
        abs_turn = 0.0
    res["Absolute turn angle (deg)"] = _r(abs_turn, 1)
    res[f"Meander (deg/{u})"] = _r(abs_turn / total if total > 0 else math.nan)
    orient = track.angle if np.isfinite(track.angle).sum() > 2 else h
    cw, acw = count_rotations(orient, s.rotation_reset_deg)
    res["Rotations clockwise"] = cw
    res["Rotations anticlockwise"] = acw
    # thigmotaxis
    try:
        arena = app.arena_or_bounds()
    except ValueError:
        arena = None
    if arena is not None:
        dwall = arena.distance_to_edge(k.x, k.y) * k.scale
        thr = s.thigmotaxis_distance
        if not thr or thr <= 0:
            x0, y0, x1, y1 = arena.bounds()
            thr = 0.25 * min(x1 - x0, y1 - y0) / 2 * k.scale
        inside_arena = arena.contains(k.x, k.y)
        near = (dwall <= thr) & inside_arena
        res[f"Mean distance from wall ({u})"] = _r(np.nanmean(np.where(inside_arena, dwall, np.nan)), 2)
        res["Thigmotaxis (%)"] = _r(100 * dur[near].sum() / T if T > 0 else math.nan, 2)
        res["Time outside arena (s)"] = _r(dur[~inside_arena].sum())

    # ---- zones ----------------------------------------------------------------
    px, py = track.bodypart(s.zone_body_part)
    zx, zy = ffill(px), ffill(py)
    memb = app.zone_membership(zx, zy)
    head_memb = None
    if track.has_head():
        head_memb = app.zone_membership(ffill(track.hx), ffill(track.hy))
    for zn, inside in memb.items():
        visits = zone_visits(t, dur, inside, s)
        vm = np.zeros(len(t), bool)
        for a, b in visits:
            vm[a:b] = True
        tz = float(dur[vm].sum())
        res[f"{zn}: time (s)"] = _r(tz)
        res[f"{zn}: time (%)"] = _r(100 * tz / T if T > 0 else math.nan, 2)
        res[f"{zn}: entries"] = len(visits)
        res[f"{zn}: latency to first entry (s)"] = _r(_latency(t, vm, t0, T, s))
        dz = float(k.step[vm].sum())
        res[f"{zn}: distance ({u})"] = _r(dz, 2)
        res[f"{zn}: mean speed ({u}/s)"] = _r(dz / tz if tz > 0 else math.nan)
        res[f"{zn}: mean visit (s)"] = _r(tz / len(visits) if visits else 0.0)
        res[f"{zn}: time immobile (s)"] = _r(dur[vm & ~k.mobile].sum())
        if np.isfinite(k.motion_pct).any():
            res[f"{zn}: time freezing (s)"] = _r(dur[vm & k.freezing].sum())
        if head_memb is not None and zn in head_memb:
            hv_ = zone_visits(t, dur, head_memb[zn], s)
            hm = np.zeros(len(t), bool)
            for a, b in hv_:
                hm[a:b] = True
            res[f"{zn}: head entries"] = len(hv_)
            res[f"{zn}: head time (s)"] = _r(dur[hm].sum())
            res[f"{zn}: latency to head entry (s)"] = _r(_latency(t, hm, t0, T, s))

    # ---- points of interest --------------------------------------------------
    for p in app.points:
        dx, dy = (k.x - p.x) * k.scale, (k.y - p.y) * k.scale
        dist = np.hypot(dx, dy)
        res[f"{p.name}: mean distance ({u})"] = _r(np.nanmean(dist), 2)
        res[f"{p.name}: min distance ({u})"] = _r(np.nanmin(dist), 2)
        radius = p.radius_cm if app.px_per_cm else p.radius_cm  # radius already in output units
        if radius and radius > 0:
            if track.has_head():
                hx, hy = ffill(track.hx), ffill(track.hy)
                hd = np.hypot((hx - p.x) * k.scale, (hy - p.y) * k.scale)
                near = hd <= radius
                # facing: angle between body axis and direction head->point
                ang_body = np.radians(track.angle)
                ang_to = np.arctan2(p.y - hy, p.x - hx)
                diff = np.degrees(np.abs((ang_to - ang_body + np.pi) % (2 * np.pi) - np.pi))
                facing = diff <= s.exploration_facing_deg
                explore = near & (facing | (hd <= radius * 0.5))
                res[f"{p.name}: time exploring (s)"] = _r(dur[explore].sum())
                ex_runs = runs(drop_short_runs(explore, t, dur, 0.2, value=True))
                res[f"{p.name}: exploration bouts"] = len(ex_runs)
                res[f"{p.name}: latency to explore (s)"] = _r(_latency(t, explore, t0, T, s))
            else:
                near = dist <= radius
            res[f"{p.name}: time near (s)"] = _r(dur[near].sum())
            res[f"{p.name}: approaches"] = len(runs(near))
            res[f"{p.name}: latency to approach (s)"] = _r(_latency(t, near, t0, T, s))

    # ---- lines -------------------------------------------------------------
    for ln in app.lines:
        P = np.column_stack([k.x, k.y])
        if len(P) > 1:
            hit, sign = segments_intersect(P[:-1], P[1:], (ln.x1, ln.y1), (ln.x2, ln.y2))
            res[f"{ln.name}: crossings"] = int(hit.sum())
            res[f"{ln.name}: crossings left-to-right"] = int((sign > 0).sum())
            res[f"{ln.name}: crossings right-to-left"] = int((sign < 0).sum())
            idx = np.flatnonzero(hit)
            res[f"{ln.name}: latency to first crossing (s)"] = _r(
                float(t[idx[0] + 1] - t0) if len(idx) else (T if s.latency_if_never == "duration" else math.nan))

    # ---- test-specific -----------------------------------------------------
    _template_measures(res, track, app, s, k, memb, head_memb)

    # ---- social (multiple animals) -----------------------------------------
    if other_tracks:
        for j, ot in enumerate(other_tracks):
            o = ot.slice_time(*t_range) if t_range is not None else ot
            n = min(len(o), len(track))
            if n == 0:
                continue
            d = np.hypot((ffill(o.x[:n]) - k.x[:n]) * k.scale, (ffill(o.y[:n]) - k.y[:n]) * k.scale)
            cd = s.contact_distance
            if not cd or cd <= 0:
                body = np.nanmedian(np.hypot(track.hx - track.tx, track.hy - track.ty)) * k.scale
                cd = body if math.isfinite(body) and body > 0 else 2 * math.sqrt(np.nanmedian(track.area) or 1) * k.scale
            res[f"Animal {ot.meta.get('animal_index', j + 1)}: mean distance ({u})"] = _r(np.nanmean(d), 2)
            res[f"Animal {ot.meta.get('animal_index', j + 1)}: time in contact (s)"] = _r(dur[:n][d <= cd].sum())

    # ---- manual scoring ------------------------------------------------------
    if behaviours:
        res.update(behaviour_measures(events or [], behaviours, t0, t0 + T))

    if s.measure_filter:
        keep = set(s.measure_filter)
        res = OrderedDict((key, v) for key, v in res.items() if key in keep or key == "Test duration (s)")
    return res


def behaviour_measures(events: list, behaviours: list, t0: float, t1: float) -> "OrderedDict[str, object]":
    """Measures from manually scored events.

    behaviours: [{"name", "key", "kind": "state"|"point"}]
    events: [{"behaviour", "t", "t_end" (state only)}]
    """
    out: OrderedDict[str, object] = OrderedDict()
    T = t1 - t0
    for b in behaviours:
        name = b["name"]
        evs = [e for e in events if e.get("behaviour") == name]
        if b.get("kind", "state") == "point":
            ts = sorted(e["t"] for e in evs if t0 <= e["t"] < t1)
            out[f"{name}: count"] = len(ts)
            out[f"{name}: latency (s)"] = _r(ts[0] - t0 if ts else T)
            out[f"{name}: rate (/min)"] = _r(len(ts) / (T / 60) if T > 0 else math.nan)
        else:
            spans = []
            for e in evs:
                a = max(e["t"], t0)
                bb = min(e.get("t_end", e["t"]) if e.get("t_end") is not None else t1, t1)
                if bb > a:
                    spans.append((a, bb))
            spans.sort()
            total = sum(bb - a for a, bb in spans)
            out[f"{name}: count"] = len(spans)
            out[f"{name}: duration (s)"] = _r(total)
            out[f"{name}: duration (%)"] = _r(100 * total / T if T > 0 else math.nan, 2)
            out[f"{name}: latency (s)"] = _r(spans[0][0] - t0 if spans else T)
            out[f"{name}: mean bout (s)"] = _r(total / len(spans) if spans else 0.0)
    return out


def _template_measures(res, track, app, s, k, memb, head_memb):
    tpl = app.template
    t, dur, T = k.t, k.dur, k.duration
    u = app.unit

    def ztime(name):
        return float(res.get(f"{name}: time (s)", 0) or 0)

    def zent(name):
        return int(res.get(f"{name}: entries", 0) or 0)

    if tpl in ("open_field", "novel_object", "custom") and s.grid_cells and s.grid_cells > 1 and app.arena is not None:
        x0, y0, x1, y1 = app.arena.bounds()
        n = int(s.grid_cells)
        gx = np.clip(((k.x - x0) / max(x1 - x0, 1e-9) * n).astype(int), 0, n - 1)
        gy = np.clip(((k.y - y0) / max(y1 - y0, 1e-9) * n).astype(int), 0, n - 1)
        cell = gx * n + gy
        res[f"Grid crossings ({n}×{n})"] = int(np.count_nonzero(np.diff(cell)))
        inner = (gx > 0) & (gx < n - 1) & (gy > 0) & (gy < n - 1)
        res["Inner grid squares time (%)"] = _r(100 * dur[inner].sum() / T if T > 0 else math.nan, 2)

    if tpl in ("epm", "ezm"):
        o, c = ("Open arms", "Closed arms") if tpl == "epm" else ("Open quadrants", "Closed quadrants")
        to, tc = ztime(o), ztime(c)
        eo, ec = zent(o), zent(c)
        # count arm entries per arm (entries into the group may merge adjacent arms)
        arms_o = app.group(o).zones if app.group(o) else []
        arms_c = app.group(c).zones if app.group(c) else []
        eo = sum(zent(a) for a in arms_o) or eo
        ec = sum(zent(a) for a in arms_c) or ec
        lbl = "arm" if tpl == "epm" else "quadrant"
        res[f"Open {lbl} time (%)"] = _r(100 * to / (to + tc) if to + tc > 0 else math.nan, 2)
        res[f"Open {lbl} entries (%)"] = _r(100 * eo / (eo + ec) if eo + ec > 0 else math.nan, 2)
        res[f"Total {lbl} entries"] = eo + ec
        res[f"Open {lbl} entries"] = eo
        res[f"Closed {lbl} entries"] = ec
        # head dips approximation: head outside the apparatus while body in an open arm
        if head_memb is not None and app.arena is not None:
            head_out = ~app.arena.contains(ffill(track.hx), ffill(track.hy))
            body_open = memb.get(o, np.zeros(len(t), bool))
            res["Head dips"] = len(runs(drop_short_runs(head_out & body_open, t, dur, 0.2, value=True)))

    elif tpl == "y_maze":
        arms = [z.name for z in app.zones if z.name.startswith("Arm")]
        seq = [e[0] for e in zone_sequence(track, app, arms, s)]
        # collapse consecutive duplicates (re-entering the same arm without a centre visit is impossible
        # in occupancy terms, but small gaps can split visits)
        res["Arm entry sequence"] = "".join(a.split()[-1] for a in seq)
        n = len(seq)
        alt = sum(1 for i in range(n - 2) if len({seq[i], seq[i + 1], seq[i + 2]}) == 3)
        res["Total arm entries"] = n
        res["Spontaneous alternations"] = alt
        res["Alternation (%)"] = _r(100 * alt / (n - 2) if n > 2 else math.nan, 2)
        res["Same arm returns"] = sum(1 for i in range(n - 1) if seq[i] == seq[i + 1])
        res["Alternate arm returns"] = sum(1 for i in range(n - 2) if seq[i] == seq[i + 2] and seq[i] != seq[i + 1])

    elif tpl == "radial_arm_maze":
        arms = [z.name for z in app.zones if z.name.startswith("Arm")]
        seq = [e[0] for e in zone_sequence(track, app, arms, s)]
        visited = set()
        errors = 0
        first_err = None
        for i, a in enumerate(seq):
            if a in visited:
                errors += 1
                if first_err is None:
                    first_err = i
            visited.add(a)
        res["Total arm entries"] = len(seq)
        res["Different arms visited"] = len(visited)
        res["Working memory errors (re-entries)"] = errors
        res["Correct entries before first error"] = first_err if first_err is not None else len(seq)
        first_n = seq[: len(arms)]
        res[f"Different arms in first {len(arms)} entries"] = len(set(first_n))
        res["Arm entry sequence"] = " ".join(a.split()[-1] for a in seq)
        all_idx = None
        seen = set()
        for i, a in enumerate(seq):
            seen.add(a)
            if len(seen) == len(arms):
                all_idx = i
                break
        res["Entries to visit all arms"] = (all_idx + 1) if all_idx is not None else math.nan

    elif tpl == "t_maze":
        seq = zone_sequence(track, app, ["Left arm", "Right arm"], s)
        res["First choice"] = seq[0][0].split()[0] if seq else "None"
        res["Choice latency (s)"] = _r(seq[0][1] - k.t0 if seq else T)
        res["Arm alternations"] = sum(1 for i in range(len(seq) - 1) if seq[i][0] != seq[i + 1][0])

    elif tpl == "water_maze":
        plat = app.zone("Platform")
        pc = app.point("Platform centre")
        if plat is not None:
            inp = memb.get("Platform", np.zeros(len(t), bool))
            idx = np.flatnonzero(inp)
            lat = float(t[idx[0]] - k.t0) if len(idx) else T
            res["Escape latency (s)"] = _r(lat)
            res["Found platform"] = "Yes" if len(idx) else "No"
            stop = idx[0] if len(idx) else len(t)
            res[f"Path length to platform ({u})"] = _r(k.step[:stop + 1].sum(), 2)
            res["Platform crossings"] = zent("Platform") - (1 if inp[0] and s.count_initial_entry else 0)
            others = [z.name for z in app.zones if z.name.startswith("Platform position")]
            if others:
                res["Mean crossings of other platform positions"] = _r(np.mean([zent(o) for o in others]), 2)
        if pc is not None:
            d = np.hypot((k.x - pc.x) * k.scale, (k.y - pc.y) * k.scale)
            res[f"Mean distance to platform ({u})"] = _r(np.nanmean(d), 2)
            res[f"Cumulative distance to platform ({u}·s)"] = _r(np.nansum(d * dur), 1)
            # initial heading error: direction from start to position after ~1 s vs direction to platform
            ok = np.flatnonzero(np.isfinite(k.ux))
            if len(ok) > 2:
                i0 = ok[0]
                j = np.searchsorted(t, t[i0] + 1.0)
                j = min(max(j, i0 + 1), len(t) - 1)
                hdx, hdy = k.x[j] - k.x[i0], k.y[j] - k.y[i0]
                tdx, tdy = pc.x - k.x[i0], pc.y - k.y[i0]
                if math.hypot(hdx, hdy) > 0 and math.hypot(tdx, tdy) > 0:
                    a = math.degrees(math.atan2(hdy, hdx) - math.atan2(tdy, tdx))
                    res["Initial heading error (deg)"] = _r(abs((a + 180) % 360 - 180), 1)
        tq = app.group("Target quadrant")
        if tq is not None:
            res["Target quadrant time (%)"] = res.get("Target quadrant: time (%)")
            res["Opposite quadrant time (%)"] = res.get("Opposite quadrant: time (%)")
        if "Thigmotaxis zone: time (%)" in res:
            res["Wall-hugging (%)"] = res["Thigmotaxis zone: time (%)"]
        res["Search strategy"] = classify_water_maze_strategy(res, u)

    elif tpl == "barnes_maze":
        holes = [z.name for z in app.zones if z.name.startswith("Hole")]
        esc = app.group("Escape hole zone")
        esc_name = esc.zones[0] if esc and esc.zones else None
        part = "head" if track.has_head() else s.zone_body_part
        seq = zone_sequence(track, app, holes, s, part=part)
        names = [e[0] for e in seq]
        if esc_name in names:
            first = names.index(esc_name)
            res["Primary latency (s)"] = _r(seq[first][1] - k.t0)
            res["Primary errors"] = first
            stop = np.searchsorted(t, seq[first][1])
            res[f"Primary path length ({u})"] = _r(k.step[:stop + 1].sum(), 2)
        else:
            res["Primary latency (s)"] = _r(T)
            res["Primary errors"] = len(names)
            res[f"Primary path length ({u})"] = _r(k.step.sum(), 2)
        res["Total errors"] = sum(1 for n in names if n != esc_name)
        res["Escape hole visits"] = sum(1 for n in names if n == esc_name)
        res["Hole visit sequence"] = " ".join(n.split()[-1] for n in names)
        res["Search strategy"] = classify_barnes_strategy(names, esc_name, len(holes))

    elif tpl == "novel_object":
        novel = s.novel_object
        names = [p.name for p in app.points]
        if len(names) >= 2:
            fam = [n for n in names if n != novel][0] if novel in names else names[0]
            nov = novel if novel in names else names[1]
            key = "time exploring (s)" if track.has_head() else "time near (s)"
            tn = float(res.get(f"{nov}: {key}", 0) or 0)
            tf = float(res.get(f"{fam}: {key}", 0) or 0)
            res["Novel object exploration (s)"] = _r(tn)
            res["Familiar object exploration (s)"] = _r(tf)
            res["Total exploration (s)"] = _r(tn + tf)
            res["Discrimination index"] = _r((tn - tf) / (tn + tf) if tn + tf > 0 else math.nan)
            res["Recognition index (%)"] = _r(100 * tn / (tn + tf) if tn + tf > 0 else math.nan, 2)

    elif tpl == "light_dark":
        dark = memb.get("Dark compartment")
        if dark is not None:
            res["Latency to enter dark (s)"] = res.get("Dark compartment: latency to first entry (s)")
            light = memb.get("Light compartment", ~dark)
            state = np.where(dark, 1, np.where(light, 0, -1))
            st = state[state >= 0]
            res["Transitions"] = int(np.sum(np.diff(st) != 0)) if len(st) > 1 else 0
            res["Time in light (%)"] = res.get("Light compartment: time (%)")

    elif tpl == "three_chamber":
        left = float(res.get("Left cup interaction zone: time (s)", 0) or 0)
        right = float(res.get("Right cup interaction zone: time (s)", 0) or 0)
        soc, obj = (left, right) if s.social_side.lower().startswith("l") else (right, left)
        res["Social interaction (s)"] = _r(soc)
        res["Object/empty interaction (s)"] = _r(obj)
        res["Sociability index"] = _r((soc - obj) / (soc + obj) if soc + obj > 0 else math.nan)
        lc = ztime("Left chamber")
        rc = ztime("Right chamber")
        sc, oc = (lc, rc) if s.social_side.lower().startswith("l") else (rc, lc)
        res["Social chamber preference index"] = _r((sc - oc) / (sc + oc) if sc + oc > 0 else math.nan)

    elif tpl == "forced_swim":
        if "Time freezing (s)" in res:
            res["Immobility (s)"] = res["Time freezing (s)"]
            res["Immobility (%)"] = res["Freezing (%)"]
            res["Latency to immobility (s)"] = res["Latency to first freezing (s)"]


def classify_water_maze_strategy(res: dict, u: str) -> str:
    """Very simple heuristic search-strategy classification (Garthe et al.-inspired)."""
    eff = res.get("Path efficiency", math.nan)
    thig = res.get("Wall-hugging (%)", res.get("Thigmotaxis (%)", 0)) or 0
    tq = res.get("Target quadrant time (%)", 0) or 0
    found = res.get("Found platform") == "Yes"
    if found and isinstance(eff, float) and eff >= 0.6:
        return "Direct"
    if thig >= 50:
        return "Thigmotaxis"
    if found and tq >= 50:
        return "Focal search"
    if found and isinstance(eff, float) and eff >= 0.3:
        return "Directed search"
    return "Random / scanning"


def classify_barnes_strategy(names: list[str], esc: str | None, n_holes: int) -> str:
    if not names:
        return "None"
    if esc is None:
        return "Unknown"
    if esc in names:
        first = names.index(esc)
        before = names[:first]
    else:
        before = names
    if len(before) <= 2:
        return "Direct"
    nums = [int(n.split()[-1]) for n in before]
    adj = sum(1 for a, b in zip(nums, nums[1:]) if min((a - b) % n_holes, (b - a) % n_holes) == 1)
    if len(nums) > 2 and adj / (len(nums) - 1) >= 0.6:
        return "Serial"
    return "Random"


# ---------------------------------------------------------------------------

def time_periods(duration: float, s: AnalysisSettings) -> list[tuple[str, float, float]]:
    """Periods for segmented analysis: custom periods if given, else regular bins."""
    out = []
    if s.custom_periods:
        for p in s.custom_periods:
            label, a, b = p[0], float(p[1]), float(p[2])
            out.append((str(label), a, b))
        return out
    if s.bin_length_s and s.bin_length_s > 0:
        n = int(math.ceil(duration / s.bin_length_s - 1e-9))
        for i in range(n):
            a = i * s.bin_length_s
            b = min(duration, (i + 1) * s.bin_length_s)
            out.append((f"{a:g}-{b:g} s", a, b))
    return out


def analyse_segmented(track: Track, app: Apparatus, s: AnalysisSettings, **kw) -> list[tuple[str, "OrderedDict"]]:
    """Whole-test results followed by results per time period."""
    out = [("Whole test", analyse(track, app, s, **kw))]
    dur = track.t[-1] + track.dt if len(track) else 0
    for label, a, b in time_periods(dur, s):
        out.append((label, analyse(track, app, s, t_range=(a, b), **kw)))
    return out
