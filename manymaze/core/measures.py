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
from .geometry import body_fraction_inside, segments_intersect
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
    body_proportion_pct: float = 80.0  # "body" entry rule: % of the body inside a zone to be in it
    hidden_zone_margin: float = 0.0  # units: lost within this distance of a hidden zone = in it (0 = auto)
    event_periods: list = field(default_factory=list)  # event-anchored periods (see periods.py)
    whishaw_width: float = 0.0  # water maze: Whishaw corridor width (units); 0 = 20 cm / 13 % of the pool
    arena_quadrants: bool = False  # time in each quadrant of the arena (NE, SE, SW, NW)
    behaviour_by_zone: bool = False  # manually scored behaviours split by zone
    paired_chamber: str = "Chamber A"  # conditioned place preference: drug-paired chamber
    nose_contact_distance: float = 0.0  # social: nose-to-nose / nose-to-body distance (units); 0 = auto
    follow_distance: float = 0.0  # social: max distance for following (units); 0 = 2 body lengths

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
               duration: float | None = None, breaks: np.ndarray | None = None) -> Kinematics:
    """breaks: frames that follow a pause (no distance is counted into them; the frame before lasts one dt)."""
    t = track.t
    dur = track.frame_durations()
    if breaks is not None and breaks.any():
        prev = np.flatnonzero(breaks) - 1
        dur[prev[prev >= 0]] = track.dt
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
        if breaks is not None:
            step[breaks] = 0.0
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


def body_axes(track: Track) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame body ellipse (semi-major a, semi-minor b in px; orientation in degrees).

    The long axis is the head–tail distance (or, without head/tail, derived from the blob area assuming a 2:1
    body); the short axis follows from the blob area (ellipse area = π a b).
    """
    n = len(track)
    L = np.hypot(track.hx - track.tx, track.hy - track.ty)
    area = track.area
    if np.isfinite(L).any():
        a = ffill(np.where(L > 0, L / 2, np.nan))
    elif np.isfinite(area).any():
        a = np.sqrt(2 * ffill(area) / math.pi)
    else:
        a = np.full(n, np.nan)
    if np.isfinite(area).any():
        b = ffill(area) / (math.pi * np.where(a > 0, a, np.nan))
        b = np.minimum(np.where(np.isfinite(b), b, a / 2), a)
    else:
        b = a / 2
    ang = track.angle.copy()
    if not np.isfinite(ang).any():
        x, y = ffill(track.x), ffill(track.y)
        ang = np.full(n, np.nan)
        if n > 1:
            ang[1:] = np.degrees(np.arctan2(np.diff(y), np.diff(x)))
            ang[1:][np.hypot(np.diff(x), np.diff(y)) < 1e-6] = np.nan
    ang = ffill(ang)
    if not np.isfinite(ang).any():
        ang = np.zeros(n)
    return a, b, ang


def _entry_hysteresis(frac: np.ndarray, enter: float) -> np.ndarray:
    """In-zone state from the fraction of the body inside: enters at >= enter, leaves below min(enter, 1-enter)."""
    leave = min(enter, 1.0 - enter)
    out = np.zeros(len(frac), bool)
    state = False
    for i, f in enumerate(frac):
        if np.isfinite(f):
            state = f >= enter if not state else f >= leave and f > 0
        out[i] = state
    return out


def occupancy(track: Track, app: Apparatus, s: AnalysisSettings, part: str | None = None
              ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray] | None, dict[str, np.ndarray]]:
    """Zone occupancy per frame honouring each zone's entry rule, investigation distance and hidden flag.

    Returns (membership of zones and groups, head membership (None without head), hidden: {hidden zone:
    frames the animal is hidden in it}). `part` forces one body part for every zone (e.g. "head").
    """
    n = len(track)
    scale = app.scale
    cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    has_head = track.has_head()

    def pos(bp):
        if bp in ("head", "tail") and not (has_head if bp == "head" else np.isfinite(track.tx).any()):
            bp = "centre"
        if bp not in cache:
            px, py = track.bodypart(bp)
            cache[bp] = (ffill(px), ffill(py))
        return cache[bp]

    axes = None
    zones: dict[str, np.ndarray] = {}
    excl = []
    for z in app.zones:
        rule = part or z.entry_rule or s.zone_body_part or "centre"
        if z.investigation_distance_cm and z.investigation_distance_cm > 0 and not part:
            hx, hy = pos("head")
            d_px = z.investigation_distance_cm / scale
            m = z.shape.contains(hx, hy)
            near = z.shape.distance_to_edge(hx, hy) <= d_px
            zones[z.name] = m | (near & np.isfinite(hx))
        elif rule == "body":
            if axes is None:
                axes = body_axes(track)
            x, y = pos("centre")
            frac = body_fraction_inside(z.shape, x, y, axes[2], axes[0], axes[1])
            thr = (z.body_fraction if z.entry_rule == "body" else s.body_proportion_pct / 100.0)
            zones[z.name] = _entry_hysteresis(frac, float(min(max(thr, 0.01), 1.0)))
        elif rule == "exclusion":
            excl.append(z)
            zones[z.name] = z.shape.contains(*pos("centre"))
        else:
            zones[z.name] = z.shape.contains(*pos(rule))
    for z in excl:
        others = [o for o in app.zones if o is not z and o not in excl and not o.hidden
                  and o.shape.area() < z.shape.area()]
        m = zones[z.name].copy()
        for o in others:
            m &= ~zones[o.name]
        zones[z.name] = m
    hidden = _hidden_frames(track, app, s, n)
    for hz, hm in hidden.items():
        for zn in zones:
            zones[zn] = zones[zn] & ~hm
        zones[hz] = zones[hz] | hm
    memb = app.combine_groups(zones, (n,))
    head_memb = None
    if has_head and part is None:
        hx, hy = pos("head")
        hz_ = {z.name: z.shape.contains(hx, hy) for z in app.zones}
        for hzn, hm in hidden.items():
            for zn in hz_:
                hz_[zn] = hz_[zn] & ~hm
        head_memb = app.combine_groups(hz_, (n,))
    return memb, head_memb, hidden


def _hidden_frames(track: Track, app: Apparatus, s: AnalysisSettings, n: int) -> dict[str, np.ndarray]:
    """Undetected frames attributed to hidden zones (animal last seen, or next seen, in or near the zone)."""
    hz = [z for z in app.zones if z.hidden]
    if not hz or n == 0:
        return {}
    out = {z.name: np.zeros(n, bool) for z in hz}
    det = track.detected
    x, y = track.x, track.y
    for a, b in runs(~det):
        for idx in (a - 1, b):
            if not (0 <= idx < n) or not (math.isfinite(x[idx]) and math.isfinite(y[idx])):
                continue
            best = None
            for z in hz:
                if s.hidden_zone_margin and s.hidden_zone_margin > 0:
                    margin = s.hidden_zone_margin / app.scale
                else:
                    margin = 0.5 * math.sqrt(max(z.shape.area(), 1.0))
                if bool(z.shape.contains(x[idx], y[idx])):
                    d = 0.0
                else:
                    d = float(z.shape.distance_to_edge(np.array([x[idx]]), np.array([y[idx]]))[0])
                if d <= margin and (best is None or d < best[0]):
                    best = (d, z.name)
            if best is not None:
                out[best[1]][a:b] = True
                break
    return {k: v for k, v in out.items() if v.any()}


def zone_sequence(track: Track, app: Apparatus, zone_names: list[str], s: AnalysisSettings,
                  part: str | None = None) -> list[tuple[str, float, float]]:
    """Ordered list of (zone, t_enter, t_exit) entries into any of the given zones."""
    t, dur = track.t, track.frame_durations()
    memb = occupancy(track, app, s, part=part)[0]
    seq = []
    for zn in zone_names:
        if zn not in memb:
            continue
        for a, b in zone_visits(t, dur, memb[zn], s):
            seq.append((zn, float(t[a]), float(t[b - 1] + dur[b - 1])))
    seq.sort(key=lambda e: e[1])
    return seq


# ---------------------------------------------------------------------------

def _drop_pauses(track: Track, pauses) -> tuple[Track, np.ndarray]:
    """Remove frames inside paused intervals; returns the track and the frames that follow a pause."""
    keep = np.ones(len(track), bool)
    for p in pauses or []:
        a, b = float(p[0]), float(p[1])
        keep &= ~((track.t >= a) & (track.t < b))
    if keep.all():
        return track, np.zeros(len(track), bool)
    from .track import COLUMNS

    out = Track(**{c: getattr(track, c)[keep] for c in COLUMNS}, fps=track.fps, meta=dict(track.meta))
    idx = np.flatnonzero(keep)
    breaks = np.zeros(len(idx), bool)
    breaks[1:] = np.diff(idx) > 1
    return out, breaks


def _pause_overlap(pauses, a: float, b: float) -> float:
    tot = 0.0
    for p in pauses or []:
        tot += max(0.0, min(b, float(p[1])) - max(a, float(p[0])))
    return tot


def _mask(visits, n) -> np.ndarray:
    m = np.zeros(n, bool)
    for a, b in visits:
        m[a:b] = True
    return m


def _angle_diff(a, b) -> np.ndarray:
    """Absolute angle difference in degrees (0..180) of radians arrays."""
    return np.degrees(np.abs((a - b + np.pi) % (2 * np.pi) - np.pi))


def body_length(track: Track, scale: float) -> float:
    L = np.nanmedian(np.hypot(track.hx - track.tx, track.hy - track.ty)) if track.has_head() else math.nan
    if not (math.isfinite(L) and L > 0):
        a = np.nanmedian(track.area) if np.isfinite(track.area).any() else math.nan
        L = 2 * math.sqrt(a) if math.isfinite(a) and a > 0 else math.nan
    return L * scale if math.isfinite(L) else math.nan


def analyse(track: Track, app: Apparatus, s: AnalysisSettings | None = None, events: list | None = None,
            behaviours: list | None = None, t_range: tuple[float, float] | None = None,
            duration: float | None = None, other_tracks: list[Track] | None = None,
            zone_overrides: dict | None = None, io_events: list | None = None,
            result_variables: dict | None = None, pauses: list | None = None,
            io_devices: list | None = None) -> "OrderedDict[str, object]":
    """Compute all applicable measures for one animal's track.

    zone_overrides: per-test positions of moveable zones (Test.zone_overrides); io_events: Test.io_events
    (I/O measures; io_devices: Project.io_devices); result_variables: Test.result_variables; pauses: Test.pauses
    (paused time is excluded).
    """
    s = s or AnalysisSettings()
    app = app.with_overrides(zone_overrides)
    breaks = None
    if pauses:
        track, breaks = _drop_pauses(track, pauses)
    full = track
    if t_range is not None:
        sel = (track.t >= t_range[0]) & (track.t < t_range[1])
        track = track.slice_time(*t_range)
        t0 = t_range[0]
        end = min(t_range[1], full.t[-1] + full.dt if len(full) else t_range[1])
        duration = end - t_range[0] - _pause_overlap(pauses, t_range[0], end)
        if breaks is not None:
            breaks = breaks[sel]
    else:
        t0 = float(track.t[0]) if len(track) else 0.0
    res: OrderedDict[str, object] = OrderedDict()
    u = app.unit
    if len(track) == 0:
        res["Test duration (s)"] = 0.0
        return res
    memb, head_memb, hidden = occupancy(track, app, s)
    hid_any = np.zeros(len(track), bool)
    for hm in hidden.values():
        hid_any |= hm
    if hid_any.any():
        # no interpolation through hidden periods: the animal stays where it was last seen
        track = track.copy()
        for c in ("x", "y", "hx", "hy", "tx", "ty"):
            getattr(track, c)[hid_any] = np.nan
    k = kinematics(track, app, s, t0=t0, duration=duration, breaks=breaks)
    t, dur = k.t, k.dur
    T = k.duration
    n = len(t)
    res["Test duration (s)"] = _r(T)
    res["Detection (%)"] = _r(100.0 * track.detected.mean(), 1)
    res["Time not detected (s)"] = _r(dur[~track.detected & ~hid_any].sum())
    if hidden:
        res["Time hidden (s)"] = _r(dur[hid_any].sum())

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
    mob = runs(k.mobile)
    res["Immobile episodes"] = len(imm)
    res["Latency to first immobility (s)"] = _r(_latency(t, ~k.mobile, t0, T, s))
    res[f"Mean speed while mobile ({u}/s)"] = _r(k.step[k.mobile].sum() / t_mob if t_mob > 0 else math.nan)
    res["Mobile episodes"] = len(mob)
    res["Mean mobile episode (s)"] = _r(t_mob / len(mob) if mob else 0.0)
    res["Mean immobile episode (s)"] = _r((T - t_mob) / len(imm) if imm else 0.0)
    res["Longest immobile episode (s)"] = _r(max((dur[a:b].sum() for a, b in imm), default=0.0))
    if np.isfinite(k.motion_pct).any():
        fr = runs(k.freezing)
        t_fr = float(dur[k.freezing].sum())
        res["Time freezing (s)"] = _r(t_fr)
        res["Freezing (%)"] = _r(100 * t_fr / T if T > 0 else math.nan, 2)
        res["Freezing episodes"] = len(fr)
        res["Latency to first freezing (s)"] = _r(_latency(t, k.freezing, t0, T, s))
        res["Mean freezing episode (s)"] = _r(t_fr / len(fr) if fr else 0.0)
        res["Mean motion (% body)"] = _r(np.nanmean(k.motion_pct), 2)
        res["Longest freezing episode (s)"] = _r(max((dur[a:b].sum() for a, b in fr), default=0.0))
    # path shape
    ok = np.isfinite(k.ux)
    if ok.sum() >= 2:
        i0, i1 = np.flatnonzero(ok)[[0, -1]]
        straight = math.hypot(k.ux[i1] - k.ux[i0], k.uy[i1] - k.uy[i0])
        res["Path efficiency"] = _r(straight / total if total > 0 else math.nan)
        res["Path tortuosity"] = _r(total / straight if straight > 0 else math.nan)
    h = k.heading.copy()
    moving = np.isfinite(h) & (k.speed > max(s.mobility_threshold, 1e-9))
    hv = h[moving]
    if len(hv) > 1:
        dturn = np.abs((np.diff(hv) + 180) % 360 - 180)
        abs_turn = float(dturn.sum())
    else:
        dturn = np.zeros(0)
        abs_turn = 0.0
    res["Absolute turn angle (deg)"] = _r(abs_turn, 1)
    res[f"Meander (deg/{u})"] = _r(abs_turn / total if total > 0 else math.nan)
    res["Mean turn angle (deg)"] = _r(dturn.mean() if len(dturn) else math.nan, 2)
    res["Angular velocity (deg/s)"] = _r(abs_turn / t_mob if t_mob > 0 else math.nan, 2)
    orient = track.angle if np.isfinite(track.angle).sum() > 2 else h
    cw, acw = count_rotations(orient, s.rotation_reset_deg)
    res["Rotations clockwise"] = cw
    res["Rotations anticlockwise"] = acw
    if np.isfinite(track.angle).sum() > 2 and np.isfinite(h).sum() > 2:
        # rotations of the direction of travel (the rotations above follow the body / head orientation)
        pcw, pacw = count_rotations(h, s.rotation_reset_deg)
        res["Path rotations clockwise"] = pcw
        res["Path rotations anticlockwise"] = pacw
    # thigmotaxis / position in the arena
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
        acx, acy = arena.centroid()
        dc = np.hypot(k.x - acx, k.y - acy) * k.scale
        res[f"Mean distance from centre ({u})"] = _r(np.nanmean(dc), 2)
        res[f"Max distance from centre ({u})"] = _r(np.nanmax(dc) if np.isfinite(dc).any() else math.nan, 2)
        if s.arena_quadrants:
            east, south = k.x >= acx, k.y >= acy
            for q, m in (("NE", east & ~south), ("SE", east & south), ("SW", ~east & south), ("NW", ~east & ~south)):
                res[f"Arena quadrant {q}: time (%)"] = _r(100 * dur[m].sum() / T if T > 0 else math.nan, 2)

    # ---- zones ----------------------------------------------------------------
    facing_ok = track.has_head() and np.isfinite(track.angle).any()
    if facing_ok:
        hxf, hyf = ffill(track.hx), ffill(track.hy)
        ang_body = np.radians(ffill(track.angle))
    has_motion = np.isfinite(k.motion_pct).any()
    for zn, inside in memb.items():
        visits = zone_visits(t, dur, inside, s)
        vm = _mask(visits, n)
        tz = float(dur[vm].sum())
        vdur = [float(dur[a:b].sum()) for a, b in visits]
        res[f"{zn}: time (s)"] = _r(tz)
        res[f"{zn}: time (%)"] = _r(100 * tz / T if T > 0 else math.nan, 2)
        res[f"{zn}: entries"] = len(visits)
        res[f"{zn}: latency to first entry (s)"] = _r(_latency(t, vm, t0, T, s))
        dz = float(k.step[vm].sum())
        res[f"{zn}: distance ({u})"] = _r(dz, 2)
        res[f"{zn}: mean speed ({u}/s)"] = _r(dz / tz if tz > 0 else math.nan)
        res[f"{zn}: mean visit (s)"] = _r(tz / len(visits) if visits else 0.0)
        res[f"{zn}: time immobile (s)"] = _r(dur[vm & ~k.mobile].sum())
        if has_motion:
            res[f"{zn}: time freezing (s)"] = _r(dur[vm & k.freezing].sum())
        if head_memb is not None and zn in head_memb:
            hv_ = zone_visits(t, dur, head_memb[zn], s)
            hm = _mask(hv_, n)
            res[f"{zn}: head entries"] = len(hv_)
            res[f"{zn}: head time (s)"] = _r(dur[hm].sum())
            res[f"{zn}: latency to head entry (s)"] = _r(_latency(t, hm, t0, T, s))
        res[f"{zn}: latency to second entry (s)"] = _r(
            float(t[visits[1][0]] - t0) if len(visits) > 1 else (T if s.latency_if_never == "duration" else math.nan))
        exits = [b for a, b in visits if b < n]
        res[f"{zn}: time of last exit (s)"] = _r(float(t[exits[-1] - 1] + dur[exits[-1] - 1] - t0) if exits else math.nan)
        res[f"{zn}: longest visit (s)"] = _r(max(vdur, default=0.0))
        res[f"{zn}: entries (/min)"] = _r(len(visits) / (T / 60) if T > 0 else math.nan)
        res[f"{zn}: time mobile (s)"] = _r(dur[vm & k.mobile].sum())
        res[f"{zn}: immobile episodes"] = len(runs(vm & ~k.mobile))
        if has_motion:
            res[f"{zn}: freezing episodes"] = len(runs(vm & k.freezing))
        res[f"{zn}: max speed ({u}/s)"] = _r(np.nanmax(sp[vm]) if vm.any() and np.isfinite(sp[vm]).any() else math.nan)
        zobj = app.zone(zn)
        if facing_ok and zobj is not None:
            zx, zy = zobj.shape.centroid()
            diff = _angle_diff(np.arctan2(zy - hyf, zx - hxf), ang_body)
            head_in = head_memb[zn] if head_memb is not None and zn in head_memb else np.zeros(n, bool)
            facing = (diff <= s.exploration_facing_deg) & ~head_in & ~hid_any
            res[f"{zn}: time facing (s)"] = _r(dur[facing].sum())
    if hidden:
        for hz in hidden:
            res[f"{hz}: time hidden (s)"] = _r(dur[hidden[hz]].sum())
    # zone transitions between the smallest zones the animal is in
    if app.zones:
        order = sorted(range(len(app.zones)), key=lambda i: app.zones[i].shape.area())
        cur = np.full(n, -1)
        for i in reversed(order):
            m = memb.get(app.zones[i].name)
            if m is not None:
                cur[m] = i
        cz = cur[cur >= 0]
        res["Zone transitions"] = int(np.count_nonzero(np.diff(cz))) if len(cz) > 1 else 0

    # ---- points of interest --------------------------------------------------
    for p in app.points:
        dx, dy = (k.x - p.x) * k.scale, (k.y - p.y) * k.scale
        dist = np.hypot(dx, dy)
        res[f"{p.name}: mean distance ({u})"] = _r(np.nanmean(dist), 2)
        res[f"{p.name}: min distance ({u})"] = _r(np.nanmin(dist), 2)
        radius = p.radius_cm  # in output units
        if radius and radius > 0:
            if track.has_head():
                hx, hy = ffill(track.hx), ffill(track.hy)
                hd = np.hypot((hx - p.x) * k.scale, (hy - p.y) * k.scale)
                near = hd <= radius
                ang_to = np.arctan2(p.y - hy, p.x - hx)
                diff = _angle_diff(ang_to, np.radians(track.angle))
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
        res[f"{p.name}: max distance ({u})"] = _r(np.nanmax(dist), 2)
        ud = np.hypot(k.ux - p.x * k.scale, k.uy - p.y * k.scale)
        dd = np.zeros(n)
        if n > 1:
            dd[1:] = np.nan_to_num(np.diff(ud))
        towards = k.mobile & (dd < 0)
        away = k.mobile & (dd > 0)
        res[f"{p.name}: time moving towards (s)"] = _r(dur[towards].sum())
        res[f"{p.name}: time moving away (s)"] = _r(dur[away].sum())
        res[f"{p.name}: distance moved towards ({u})"] = _r(-dd[towards].sum(), 2)
        res[f"{p.name}: distance moved away ({u})"] = _r(dd[away].sum(), 2)
        if facing_ok:
            diff = _angle_diff(np.arctan2(p.y - hyf, p.x - hxf), ang_body)
            res[f"{p.name}: time head oriented towards (s)"] = _r(dur[diff <= s.exploration_facing_deg].sum())
            res[f"{p.name}: time head oriented away (s)"] = _r(dur[diff >= 180 - s.exploration_facing_deg].sum())
            res[f"{p.name}: mean head angle (deg)"] = _r(np.nanmean(diff), 1)
            res[f"{p.name}: head turns towards"] = len(runs(diff <= s.exploration_facing_deg))

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

    # ---- grids ---------------------------------------------------------------
    for g in app.grids:
        res.update(grid_measures(g, memb, t, dur, t0, T, s))

    # ---- sequences -----------------------------------------------------------
    if app.sequences:
        from .sequences import find_sequences, other_zones, sequence_measures

        for q in app.sequences:
            names = list(dict.fromkeys(list(q.steps) + ([] if q.allow_other else other_zones(app, q))))
            entries = []
            for zn in names:
                if zn in memb:
                    for a, b in zone_visits(t, dur, memb[zn], s):
                        entries.append((zn, float(t[a]), float(t[b - 1] + dur[b - 1])))
            att = find_sequences(q, entries, names)
            res.update(sequence_measures(q, att, t0, T, s.latency_if_never))

    # ---- test-specific -----------------------------------------------------
    _template_measures(res, track, app, s, k, memb, head_memb)

    # ---- social (multiple animals) -----------------------------------------
    if other_tracks:
        for j, ot in enumerate(other_tracks):
            o = ot.slice_time(*t_range) if t_range is not None else ot
            res.update(social_measures(track, k, o, app, s, f"Animal {ot.meta.get('animal_index', j + 1)}"))

    # ---- manual scoring ------------------------------------------------------
    if behaviours:
        zocc = {zn: m for zn, m in memb.items()} if s.behaviour_by_zone else None
        res.update(behaviour_measures(events or [], behaviours, t0, t0 + T, zones=zocc, t=t, dur=dur))

    # ---- I/O and procedure variables -----------------------------------------
    if io_events:
        res.update(_io_measures(io_events, T, (t0, t0 + T), io_devices))
    if result_variables:
        for name, v in result_variables.items():
            try:
                res[f"Variable: {name}"] = _r(float(v))
            except (TypeError, ValueError):
                res[f"Variable: {name}"] = str(v)

    if s.measure_filter:
        keep = set(s.measure_filter)
        res = OrderedDict((key, v) for key, v in res.items() if key in keep or key == "Test duration (s)")
    return res


def _io_measures(io_events: list, duration: float, t_range, devices=None) -> dict:
    """I/O measures from the procedures/I/O module (skipped if it is not available)."""
    fn = None
    for mod in ("procedures", "iodevices"):
        try:
            m = __import__(f"{__package__}.{mod}", fromlist=["io_measures"])
            fn = getattr(m, "io_measures", None)
        except Exception:
            fn = None
        if fn is not None:
            break
    if fn is None:
        return {}
    try:
        return dict(fn(io_events, duration, t_range, devices) if devices else fn(io_events, duration, t_range))
    except Exception:
        return {}


def grid_measures(g, memb: dict, t: np.ndarray, dur: np.ndarray, t0: float, T: float,
                  s: AnalysisSettings) -> "OrderedDict[str, object]":
    """Crossings and coverage of a grid of zones (any kind: square, rings, sectors)."""
    out: OrderedDict[str, object] = OrderedDict()
    cells = [c for c in g.zones if c in memb]
    n = len(t)
    if not cells or n == 0:
        return out
    cur = np.full(n, -1)
    for i, c in enumerate(cells):
        cur[memb[c] & (cur < 0)] = i
    seen = cur[cur >= 0]
    out[f"{g.name}: crossings"] = int(np.count_nonzero(np.diff(seen))) if len(seen) > 1 else 0
    visited = set(seen.tolist())
    out[f"{g.name}: cells visited"] = len(visited)
    out[f"{g.name}: cells visited (%)"] = _r(100 * len(visited) / len(cells), 2)
    all_t = math.nan
    if len(visited) == len(cells):
        got = set()
        for i in np.flatnonzero(cur >= 0):
            got.add(int(cur[i]))
            if len(got) == len(cells):
                all_t = float(t[i] - t0)
                break
    out[f"{g.name}: latency to visit all cells (s)"] = _r(
        all_t if math.isfinite(all_t) else (T if s.latency_if_never == "duration" else math.nan))
    if g.kind == "square":
        import re

        rc = [re.match(r".* ([A-Z]+)(\d+)$", c) for c in cells]
        if all(rc):
            rows = [m.group(1) for m in rc]
            cols = [int(m.group(2)) for m in rc]
            rset = sorted(set(rows), key=lambda r: (len(r), r))
            inner_idx = [i for i, (r, c) in enumerate(zip(rows, cols))
                         if r not in (rset[0], rset[-1]) and c not in (min(cols), max(cols))]
            inner = np.isin(cur, inner_idx)
            out[f"{g.name}: inner cells time (%)"] = _r(100 * dur[inner].sum() / T if T > 0 else math.nan, 2)
    elif g.kind == "rings" and len(cells) > 1:
        out[f"{g.name}: outer ring time (%)"] = _r(100 * dur[cur == len(cells) - 1].sum() / T if T > 0 else math.nan, 2)
    return out


def _point_segment_distance(px, py, ax, ay, bx, by) -> np.ndarray:
    abx, aby = bx - ax, by - ay
    den = abx ** 2 + aby ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.clip(((px - ax) * abx + (py - ay) * aby) / np.where(den > 0, den, np.nan), 0, 1)
    u = np.nan_to_num(u)
    return np.hypot(px - (ax + u * abx), py - (ay + u * aby))


def social_measures(track: Track, k: Kinematics, o: Track, app: Apparatus, s: AnalysisSettings,
                    label: str) -> "OrderedDict[str, object]":
    """Inter-animal measures for this animal relative to another animal tracked in the same arena."""
    out: OrderedDict[str, object] = OrderedDict()
    n = min(len(o), len(track))
    if n == 0:
        return out
    u = app.unit
    sc = k.scale
    dur = k.dur[:n]
    ox, oy = ffill(o.x[:n]), ffill(o.y[:n])
    d = np.hypot((ox - k.x[:n]) * sc, (oy - k.y[:n]) * sc)
    body = body_length(track, sc)
    if not (math.isfinite(body) and body > 0):
        body = 2 * math.sqrt(np.nanmedian(track.area) or 1) * sc if np.isfinite(track.area).any() else 1.0
    cd = s.contact_distance if s.contact_distance and s.contact_distance > 0 else body
    contact = d <= cd
    out[f"{label}: mean distance ({u})"] = _r(np.nanmean(d), 2)
    out[f"{label}: time in contact (s)"] = _r(dur[contact].sum())
    out[f"{label}: min distance ({u})"] = _r(np.nanmin(d), 2)
    out[f"{label}: max distance ({u})"] = _r(np.nanmax(d), 2)
    cr = runs(contact)
    out[f"{label}: contacts"] = len(cr)
    # nose contacts
    if track.has_head() and o.has_head():
        nd = s.nose_contact_distance if s.nose_contact_distance and s.nose_contact_distance > 0 else body / 4
        mhx, mhy = ffill(track.hx[:n]) * sc, ffill(track.hy[:n]) * sc
        ohx, ohy = ffill(o.hx[:n]) * sc, ffill(o.hy[:n]) * sc
        otx, oty = ffill(o.tx[:n]) * sc, ffill(o.ty[:n]) * sc
        nn = np.hypot(mhx - ohx, mhy - ohy) <= nd
        nb = (_point_segment_distance(mhx, mhy, otx, oty, ohx, ohy) <= nd) & ~nn
        out[f"{label}: nose-to-nose contacts"] = len(runs(nn))
        out[f"{label}: nose-to-nose time (s)"] = _r(dur[nn].sum())
        out[f"{label}: nose-to-body contacts"] = len(runs(nb))
        out[f"{label}: nose-to-body time (s)"] = _r(dur[nb].sum())
    # following and approaches
    ko = kinematics(o.slice_index(0, n), app, s, t0=k.t0)
    mh = np.radians(k.heading[:n])
    oh = np.radians(ko.heading[:n])
    to_other = np.arctan2(oy - k.y[:n], ox - k.x[:n])
    fd = s.follow_distance if s.follow_distance and s.follow_distance > 0 else 2 * body
    with np.errstate(invalid="ignore"):
        follow = (k.mobile[:n] & ko.mobile[:n] & (d <= fd) & (_angle_diff(mh, to_other) <= 45)
                  & (_angle_diff(mh, oh) <= 45))
    follow = drop_short_runs(np.nan_to_num(follow).astype(bool), k.t[:n], dur, 0.5, value=True)
    out[f"{label}: time following (s)"] = _r(dur[follow].sum())
    out[f"{label}: following episodes"] = len(runs(follow))
    w = max(1, int(round(1.0 / max(track.dt, 1e-6))))
    mine = theirs = 0
    for a, _b in cr:
        if a == 0:
            continue
        i0 = max(0, a - w)
        if k.step[i0:a].sum() >= ko.step[i0:a].sum():
            mine += 1
        else:
            theirs += 1
    out[f"{label}: approaches"] = mine
    out[f"{label}: approached by"] = theirs
    return out


def behaviour_measures(events: list, behaviours: list, t0: float, t1: float, zones: dict | None = None,
                       t: np.ndarray | None = None, dur: np.ndarray | None = None) -> "OrderedDict[str, object]":
    """Measures from manually scored events.

    behaviours: [{"name", "key", "kind": "state"|"hold"|"point"}]
    events: [{"behaviour", "t", "t_end" (state only)}]
    zones: optional {zone: per-frame bool} with frame times t / durations dur → the same measures per zone.
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
            if zones and t is not None and len(t):
                for zn, m in zones.items():
                    zt = [x for x in ts if m[min(max(np.searchsorted(t, x, "right") - 1, 0), len(t) - 1)]]
                    out[f"{name} in {zn}: count"] = len(zt)
                    out[f"{name} in {zn}: latency (s)"] = _r(zt[0] - t0 if zt else T)
                    out[f"{name} in {zn}: rate (/min)"] = _r(len(zt) / (T / 60) if T > 0 else math.nan)
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
            out[f"{name}: longest bout (s)"] = _r(max((bb - a for a, bb in spans), default=0.0))
            out[f"{name}: rate (/min)"] = _r(len(spans) / (T / 60) if T > 0 else math.nan)
            if zones and t is not None and len(t):
                d = dur if dur is not None else np.full(len(t), (t1 - t0) / max(len(t), 1))
                active = np.zeros(len(t), bool)
                for a, bb in spans:
                    active |= (t >= a) & (t < bb)
                for zn, m in zones.items():
                    on = active & m
                    starts = [a for a, _ in spans
                              if m[min(max(np.searchsorted(t, a, "right") - 1, 0), len(t) - 1)]]
                    tz = float(d[on].sum())
                    out[f"{name} in {zn}: count"] = len(starts)
                    out[f"{name} in {zn}: duration (s)"] = _r(tz)
                    out[f"{name} in {zn}: latency (s)"] = _r(starts[0] - t0 if starts else T)
                    out[f"{name} in {zn}: mean bout (s)"] = _r(tz / len(starts) if starts else 0.0)
                    out[f"{name} in {zn}: rate (/min)"] = _r(len(starts) / (T / 60) if T > 0 else math.nan)
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
        if pc is not None:
            _whishaw(res, app, s, k, memb, pc)
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

    elif tpl == "novel_tank":
        res["Latency to top (s)"] = res.get("Top: latency to first entry (s)")
        res["Top entries"] = zent("Top")
        res["Time in top (%)"] = res.get("Top: time (%)")
        res["Time in bottom (%)"] = res.get("Bottom: time (%)")
        tt, tb = ztime("Top"), ztime("Bottom")
        res["Top/bottom ratio"] = _r(tt / tb if tb > 0 else math.nan)
        if app.arena is not None:
            _, y0, _, y1 = app.arena.bounds()
            depth = (k.y - y0) * k.scale
            res[f"Mean depth ({u})"] = _r(np.nanmean(depth), 2)
        # erratic movements: sharp turns (> 90°) at high speed
        h = k.heading
        fast = k.speed > 2 * max(np.nanmedian(k.speed[k.mobile]) if k.mobile.any() else 0, s.mobility_threshold)
        turn = np.zeros(len(t), bool)
        if len(t) > 1:
            dh = np.abs((np.diff(h) + 180) % 360 - 180)
            turn[1:] = np.nan_to_num(dh) > 90
        res["Erratic movements"] = len(runs(turn & fast))

    elif tpl == "cpp":
        chambers = [z.name for z in app.zones if z.name.startswith("Chamber")]
        if len(chambers) >= 2:
            paired = s.paired_chamber if s.paired_chamber in chambers else chambers[0]
            unpaired = next(c for c in chambers if c != paired)
            tp, tu = ztime(paired), ztime(unpaired)
            res["Paired chamber time (s)"] = _r(tp)
            res["Unpaired chamber time (s)"] = _r(tu)
            res["CPP score (s)"] = _r(tp - tu)
            res["Preference index"] = _r((tp - tu) / (tp + tu) if tp + tu > 0 else math.nan)
            res["Paired chamber time (%)"] = _r(100 * tp / (tp + tu) if tp + tu > 0 else math.nan, 2)
            seq = [e[0] for e in zone_sequence(track, app, chambers, s)]
            res["Chamber transitions"] = sum(1 for a, b in zip(seq, seq[1:]) if a != b)

    elif tpl == "hole_board":
        holes = [p for p in app.points if p.name.startswith("Hole")]
        if holes:
            if track.has_head():
                hx, hy = ffill(track.hx), ffill(track.hy)
            else:
                hx, hy = k.x, k.y
            dips_total, dip_time, first, explored, repeats = 0, 0.0, math.inf, 0, 0
            order = []
            for p in holes:
                dd = np.hypot((hx - p.x) * k.scale, (hy - p.y) * k.scale)
                near = drop_short_runs(dd <= (p.radius_cm or 1.0), t, dur, 0.2, value=True)
                rr = runs(near)
                res[f"{p.name}: head dips"] = len(rr)
                dips_total += len(rr)
                dip_time += float(dur[near].sum())
                if rr:
                    explored += 1
                    first = min(first, float(t[rr[0][0]] - k.t0))
                    order += [(float(t[a]), p.name) for a, _ in rr]
            order.sort()
            seen = set()
            for _, nme in order:
                if nme in seen:
                    repeats += 1
                seen.add(nme)
            res["Head dips"] = dips_total
            res["Head-dip time (s)"] = _r(dip_time)
            res["Latency to first head dip (s)"] = _r(first if math.isfinite(first) else
                                                      (T if s.latency_if_never == "duration" else math.nan))
            res["Holes explored"] = explored
            res["Repeated head dips"] = repeats
            res["Head dips (/min)"] = _r(dips_total / (T / 60) if T > 0 else math.nan)

    elif tpl == "thermal_gradient":
        sectors = [z.name for z in app.zones if z.name.startswith("Sector")]
        if sectors:
            times = np.array([ztime(z) for z in sectors])
            res["Preferred sector"] = sectors[int(np.argmax(times))] if times.sum() > 0 else "None"
            idx = np.arange(1, len(sectors) + 1)
            res["Mean sector (time-weighted)"] = _r((idx * times).sum() / times.sum() if times.sum() > 0 else math.nan, 2)
            res["Sector entries"] = sum(zent(z) for z in sectors)

    elif tpl == "activity_wheel":
        if app.arena is not None:
            cx, cy = app.arena.centroid()
            pa = np.degrees(np.arctan2(k.y - cy, k.x - cx))
            cw, acw = count_rotations(pa, s.rotation_reset_deg)
            res["Revolutions clockwise"] = cw
            res["Revolutions anticlockwise"] = acw
            res["Revolutions (/min)"] = _r((cw + acw) / (T / 60) if T > 0 else math.nan, 2)

    elif tpl == "forced_swim":
        if "Time freezing (s)" in res:
            res["Immobility (s)"] = res["Time freezing (s)"]
            res["Immobility (%)"] = res["Freezing (%)"]
            res["Latency to immobility (s)"] = res["Latency to first freezing (s)"]


def _whishaw(res, app, s, k, memb, pc):
    """Whishaw's corridor: a band from the release point (first position or a "Release point" point) to the
    platform; reports how much of the swim to the platform stayed inside it."""
    ok = np.flatnonzero(np.isfinite(k.x))
    if len(ok) < 2:
        return
    rp = app.point("Release point") or app.point("Start")
    sx, sy = (rp.x, rp.y) if rp is not None else (k.x[ok[0]], k.y[ok[0]])
    width = s.whishaw_width
    if not width or width <= 0:
        if app.px_per_cm:
            width = 20.0
        else:
            x0, _, x1, _ = app.arena_or_bounds().bounds()
            width = 0.13 * (x1 - x0)
    wpx = width / k.scale
    inp = memb.get("Platform")
    found = np.flatnonzero(inp) if inp is not None else np.zeros(0, int)
    stop = found[0] if len(found) else len(k.t) - 1
    seg = slice(ok[0], stop + 1)
    d = _point_segment_distance(k.x, k.y, sx, sy, pc.x, pc.y)
    inside = (d <= wpx / 2)[seg]
    dd, st = k.dur[seg], k.step[seg]
    res["Whishaw corridor time (%)"] = _r(100 * dd[inside].sum() / dd.sum() if dd.sum() > 0 else math.nan, 2)
    res["Whishaw corridor path (%)"] = _r(100 * st[inside].sum() / st.sum() if st.sum() > 0 else math.nan, 2)
    res["Left Whishaw corridor"] = "No" if inside.all() else "Yes"


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
    """Whole-test results followed by results per time period (bins / custom periods, then event-anchored
    periods from s.event_periods)."""
    out = [("Whole test", analyse(track, app, s, **kw))]
    dur = track.t[-1] + track.dt if len(track) else 0
    for label, a, b in all_periods(track, app, s, dur, kw.get("events"), kw.get("io_events"),
                                   kw.get("zone_overrides")):
        out.append((label, analyse(track, app, s, t_range=(a, b), **kw)))
    return out


def all_periods(track: Track, app: Apparatus, s: AnalysisSettings, duration: float | None = None, events=None,
                io_events=None, zone_overrides=None) -> list[tuple[str, float, float]]:
    """Time bins / custom periods followed by event-anchored periods."""
    if duration is None:
        duration = track.t[-1] + track.dt if len(track) else 0
    out = time_periods(duration, s)
    if s.event_periods:
        from .periods import event_periods

        out += event_periods(s.event_periods, duration, track, app.with_overrides(zone_overrides), s, events,
                             io_events)
    return out
