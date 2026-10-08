"""Behavioural measures computed from a track and an apparatus.

`analyse()` returns an ordered dict of measure name -> value (float / int / str).
Measures are grouped into:

* general locomotion (distance, speed, mobility, freezing, rotations, path shape)
* per zone / zone group (time, entries, latency, distance, head entries, ...)
* per point of interest (distance to, time near, time exploring / facing)
* per line (crossings in each direction)
* test-specific measures chosen from the apparatus template (template_measures.py)
* manually scored behaviours
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields, replace
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np

from .apparatus import Apparatus
from .geometry import point_segment_distance, segments_intersect
from .iomeasures import io_measures
from .occupancy import occupancy
from .pauses import drop_pauses, shift_events
from .series import count_rotations, drop_short_runs, ffill, round_result as _r, runs, seg_moving_average, segments
from .template_measures import TemplateData, template_measures
from .templates import apply_overrides
from .track import Track

if TYPE_CHECKING:
    from .project import Behaviour


@dataclass
class AnalysisSettings:
    speed_smoothing_s: float = 0.2  # positions are smoothed over this window before distance/speed
    mobility_threshold: float = 2.0  # speed (units/s) below which the animal is immobile
    min_immobile_s: float = 2.0  # immobile episodes shorter than this count as mobile
    freeze_on_pct: float = 2.0  # motion (% of body area changing) below which freezing starts
    freeze_off_pct: float = 3.0  # motion above which freezing ends (hysteresis)
    min_freeze_s: float = 1.0
    activity_threshold_pct: float = 5.0  # motion (% of body area changing) at or above which the animal is active
    min_inactive_s: float = 0.5  # inactive episodes shorter than this count as active
    rearing: bool = False  # detect rears automatically from the animal's shape (see rearing_mask)
    rear_area_pct: float = 75.0  # rearing: body area below this % of the animal's usual area …
    rear_length_pct: float = 80.0  # … and (head and tail tracked) body length below this % of its usual length
    min_rear_s: float = 0.3  # rears shorter than this are ignored
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
    end_zone: str = ""  # the test ends when the animal has stayed in this zone (or group) for end_zone_s seconds
    end_zone_s: float = 0.0  # e.g. 2 s on the water-maze platform; 0 = on entering it

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AnalysisSettings":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# helpers

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
    breaks: np.ndarray | None = None  # frames that follow a pause

    def slice(self, sl: slice, t0: float, duration: float) -> "Kinematics":
        """The frames sl, as a period starting at t0 that lasts `duration`."""
        arrays = {f.name: v[sl] for f in fields(self) if isinstance(v := getattr(self, f.name), np.ndarray)}
        return replace(self, t0=t0, duration=duration, **arrays)


def kinematics(track: Track, app: Apparatus, s: AnalysisSettings, t0: float | None = None,
               duration: float | None = None, breaks: np.ndarray | None = None) -> Kinematics:
    """breaks: frames that follow a pause (no distance, speed or turn is counted into them, smoothing does not
    cross them, and the frame before lasts one dt)."""
    t = track.t
    dur = track.frame_durations()
    if breaks is not None and not breaks.any():
        breaks = None
    if breaks is not None:
        prev = np.flatnonzero(breaks) - 1
        dur[prev[prev >= 0]] = track.dt
    scale = app.scale
    unit = app.unit
    x = ffill(track.x)
    y = ffill(track.y)
    win = max(1, int(round(s.speed_smoothing_s / max(track.dt, 1e-6))))
    ux = seg_moving_average(x, win, breaks) * scale
    uy = seg_moving_average(y, win, breaks) * scale
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
    sp_s = seg_moving_average(speed, max(1, int(round(0.5 / max(track.dt, 1e-6)))), breaks)
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
        if breaks is not None:
            heading[breaks] = np.nan
    if t0 is None:
        t0 = float(t[0]) if len(t) else 0.0
    if duration is None:
        duration = float(dur.sum())
    return Kinematics(t, dur, x, y, ux, uy, step, speed, mobile, freezing, motion_pct, heading, t0, duration,
                      scale, unit, breaks)


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


@dataclass
class _Prepared:
    """Per-frame states of a whole test, computed once and sliced per analysis period."""

    track: Track  # test time (pauses removed); positions blanked while the animal is hidden
    clean: Track  # test time, positions as tracked
    app: Apparatus
    s: AnalysisSettings
    k: Kinematics | None
    breaks: np.ndarray
    memb: dict
    head_memb: dict | None
    hidden: dict
    hid_any: np.ndarray
    events: list | None
    io_events: list | None
    others: list
    cache: dict = field(default_factory=dict)

    def cached(self, key, fn):
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    def visits_mask(self, key, inside: np.ndarray) -> np.ndarray:
        """Zone membership with visits shorter than the minimum entry duration removed (whole test)."""
        return self.cached(("visits",) + tuple(key) if isinstance(key, tuple) else ("visits", key),
                           lambda: drop_short_runs(inside, self.k.t, self.k.dur, self.s.entry_min_duration_s,
                                                   value=True))


def end_of_test(track: Track, app: Apparatus, s: AnalysisSettings) -> float | None:
    """Test time at which the test ends because the animal has stayed in ``s.end_zone`` for ``s.end_zone_s``
    seconds without leaving (the water-maze platform, the Barnes escape box…), or None."""
    if not s.end_zone or not len(track):
        return None
    memb = occupancy(track, app, s)[0].get(s.end_zone)
    if memb is None:
        return None
    t, dur = track.t, track.frame_durations()
    need = max(0.0, float(s.end_zone_s or 0.0))
    for a, b in runs(memb):
        if float(t[b - 1] + dur[b - 1] - t[a]) >= need - 1e-9:
            return float(t[a] + need)
    return None


def _prepare(track: Track, app: Apparatus, s: AnalysisSettings, events=None, io_events=None, other_tracks=None,
             zone_overrides=None, pauses=None, duration=None) -> _Prepared:
    app = apply_overrides(app, zone_overrides)
    track, breaks = drop_pauses(track, pauses)
    events = shift_events(events, pauses)
    io_events = shift_events(io_events, pauses)
    others = [drop_pauses(o, pauses)[0] for o in (other_tracks or [])]
    t_end = end_of_test(track, app, s) if s.end_zone else None
    if t_end is not None:  # the test ended there: nothing after it is analysed
        keep = track.t <= t_end + 1e-9
        track, breaks = track.slice_index(0, int(keep.sum())), breaks[:int(keep.sum())]
        others = [o.slice_time(-math.inf, t_end + 1e-9) for o in others]
        events = [{**e, "t_end": min(float(e["t_end"]), t_end)} if e.get("t_end") is not None else e
                  for e in (events or []) if e["t"] <= t_end]
        io_events = [e for e in (io_events or []) if float(e.get("t", 0)) <= t_end]
        T = t_end - float(track.t[0]) if len(track) else 0.0
        duration = T if duration is None else min(duration, T)
    clean = track
    n = len(track)
    if n == 0:
        return _Prepared(track, clean, app, s, None, breaks, {}, None, {}, np.zeros(0, bool), events, io_events,
                         others)
    memb, head_memb, hidden = occupancy(track, app, s)
    hid_any = np.zeros(n, bool)
    for hm in hidden.values():
        hid_any |= hm
    if hid_any.any():
        # no interpolation through hidden periods: the animal stays where it was last seen
        track = track.copy()
        for c in ("x", "y", "hx", "hy", "tx", "ty"):
            getattr(track, c)[hid_any] = np.nan
    k = kinematics(track, app, s, t0=float(track.t[0]), duration=duration, breaks=breaks)
    return _Prepared(track, clean, app, s, k, breaks, memb, head_memb, hidden, hid_any, events, io_events, others)


def analyse(track: Track, app: Apparatus, s: AnalysisSettings | None = None, events: list | None = None,
            behaviours: list[Behaviour] | None = None, t_range: tuple[float, float] | None = None,
            duration: float | None = None, other_tracks: list[Track] | None = None,
            zone_overrides: dict | None = None, io_events: list | None = None,
            result_variables: dict | None = None, pauses: list | None = None,
            io_devices: list | None = None) -> "OrderedDict[str, object]":
    """Compute all applicable measures for one animal's track.

    zone_overrides: per-test positions of moveable zones (Test.zone_overrides); io_events: Test.io_events
    (I/O measures; io_devices: Project.io_devices); result_variables: Test.result_variables; pauses: Test.pauses.

    Times are *test time*: paused intervals are removed and everything after a pause moves back by its length
    (track, scored events, I/O events and other animals' tracks alike); t_range is in test time. Per-frame states
    (mobility, freezing, zone visits and their minimum durations / hysteresis) are computed on the whole test and
    then restricted to t_range: times count every frame inside the period, episode counts and latencies count the
    episodes that start inside it.
    """
    s = s or AnalysisSettings()
    P = _prepare(track, app, s, events, io_events, other_tracks, zone_overrides, pauses, duration)
    return _results(P, t_range, behaviours, result_variables, io_devices)


def _results(P: _Prepared, t_range, behaviours=None, result_variables=None, io_devices=None):
    n = len(P.track)
    if t_range is None:
        if n == 0:
            return OrderedDict([("Test duration (s)", 0.0)])
        return _period_results(P, 0, n, float(P.track.t[0]), P.k.duration, None, behaviours, result_variables,
                               io_devices)
    a, b = float(t_range[0]), float(t_range[1])
    t = P.track.t
    i0, i1 = int(np.searchsorted(t, a, "left")), int(np.searchsorted(t, b, "left"))
    if i1 <= i0:
        return OrderedDict([("Test duration (s)", 0.0)])
    end = min(b, float(t[-1]) + P.track.dt)
    return _period_results(P, i0, i1, a, end - a, (a, b), behaviours, result_variables, io_devices)


@dataclass
class _Period:
    """Frames [i0, i1) of a prepared test: t0 the period start, T its length; per-frame arrays restricted to it."""

    P: _Prepared
    i0: int
    i1: int
    t0: float
    T: float
    t_range: tuple | None

    def __post_init__(self):
        P, sl = self.P, slice(self.i0, self.i1)
        self.sl = sl
        self.whole = self.i0 == 0 and self.i1 == len(P.k.t)
        self.k = replace(P.k.slice(sl, self.t0, self.T), breaks=P.breaks[sl])
        self.t, self.dur, self.n = self.k.t, self.k.dur, len(self.k.t)
        self.track = P.track if self.whole else P.track.slice_index(self.i0, self.i1)
        self.memb = P.memb if self.whole else {zn: m[sl] for zn, m in P.memb.items()}
        self.head_memb = P.head_memb if self.whole or P.head_memb is None else \
            {zn: m[sl] for zn, m in P.head_memb.items()}
        self.hidden = {hz: m[sl] for hz, m in P.hidden.items()}
        self.hid_any = P.hid_any[sl]
        self.never = float(self.T) if P.s.latency_if_never == "duration" else math.nan

    def eps(self, mask_full: np.ndarray, entries: bool = False) -> list[tuple[int, int]]:
        """Episodes (runs) of a whole-test mask that start inside the period, as local [start, end) indices.
        entries: zone entries (the initial visit only counts with count_initial_entry; none across a pause)."""
        out = []
        for a, b in runs(mask_full):
            if a >= self.i1:
                break
            if a < self.i0:
                continue
            if entries and ((a == 0 and not self.P.s.count_initial_entry) or (a > 0 and self.P.breaks[a])):
                continue
            out.append((a - self.i0, min(b, self.i1) - self.i0))
        return out

    def lat(self, ep) -> float:
        return float(self.t[ep[0][0]] - self.t0) if ep else self.never

    def ep_time(self, ep) -> float:
        return float(self.dur[_mask(ep, self.n)].sum())

    def pct(self, mask) -> float:
        """Time in the frames of mask, as % of the period."""
        return _r(100 * self.dur[mask].sum() / self.T if self.T > 0 else math.nan, 2)

    @cached_property
    def sp(self) -> np.ndarray:
        """Speed smoothed over 0.2 s."""
        P = self.P
        return P.cached("sp", lambda: seg_moving_average(P.k.speed, max(1, int(round(0.2 / max(P.track.dt, 1e-6)))),
                                                         P.k.breaks))[self.sl]

    @cached_property
    def facing(self) -> tuple | None:
        """(head x, head y, body angle in radians), forward filled, if the head and orientation are tracked."""
        tr = self.track
        if not (tr.has_head() and np.isfinite(tr.angle).any()):
            return None
        return ffill(tr.hx), ffill(tr.hy), np.radians(ffill(tr.angle))

    def seq(self, names, part=None) -> list[tuple[str, float, float]]:
        """Entries (zone, t_enter, t_exit) into the given zones in the period, in order, from the occupancy of the
        analysed (hidden-blanked) track as zone_sequence() computes it."""
        P, t, dur = self.P, self.t, self.dur
        m_all = P.cached(("occ", part), lambda: occupancy(P.track, P.app, P.s, part=part)[0])
        out = []
        for zn in names:
            if zn not in m_all:
                continue
            fm = P.visits_mask(("seq", part, zn), m_all[zn])
            for a, b in self.eps(fm, entries=True):
                out.append((zn, float(t[a]), float(t[b - 1] + dur[b - 1])))
        out.sort(key=lambda e: e[1])
        return out


def _detection(res, p: _Period):
    dur, det = p.dur, p.track.detected
    res["Test duration (s)"] = _r(p.T)
    res["Detection (%)"] = _r(100.0 * det.mean(), 1)
    res["Time not detected (s)"] = _r(dur[~det & ~p.hid_any].sum())
    if p.hidden:
        res["Time hidden (s)"] = _r(dur[p.hid_any].sum())


def _locomotion(res, p: _Period):
    k, K, T, dur, u = p.k, p.P.k, p.T, p.dur, p.P.app.unit
    total = float(k.step.sum())
    res[f"Total distance ({u})"] = _r(total, 2)
    res[f"Mean speed ({u}/s)"] = _r(total / T if T > 0 else math.nan)
    if p.hidden:
        t_vis = float(dur[~p.hid_any].sum())
        res[f"Mean speed when not hidden ({u}/s)"] = _r(k.step[~p.hid_any].sum() / t_vis if t_vis > 0 else math.nan)
    res[f"Max speed ({u}/s)"] = _r(np.nanmax(p.sp) if np.isfinite(p.sp).any() else math.nan)
    t_mob = float(dur[k.mobile].sum())
    res["Time mobile (s)"] = _r(t_mob)
    res["Time immobile (s)"] = _r(T - t_mob)
    imm = p.eps(~K.mobile)
    mob = p.eps(K.mobile)
    res["Immobile episodes"] = len(imm)
    res["Latency to first immobility (s)"] = _r(p.lat(imm))
    res[f"Mean speed while mobile ({u}/s)"] = _r(k.step[k.mobile].sum() / t_mob if t_mob > 0 else math.nan)
    res["Mobile episodes"] = len(mob)
    res["Mean mobile episode (s)"] = _r(p.ep_time(mob) / len(mob) if mob else 0.0)
    imm_time = (T - t_mob) if p.whole else p.ep_time(imm)
    res["Mean immobile episode (s)"] = _r(imm_time / len(imm) if imm else 0.0)
    res["Longest immobile episode (s)"] = _r(max((dur[a:b].sum() for a, b in imm), default=0.0))
    res["Shortest immobile episode (s)"] = _r(min((dur[a:b].sum() for a, b in imm), default=0.0))
    res["Longest mobile episode (s)"] = _r(max((dur[a:b].sum() for a, b in mob), default=0.0))
    res["Shortest mobile episode (s)"] = _r(min((dur[a:b].sum() for a, b in mob), default=0.0))
    res["Latency to first mobile episode (s)"] = _r(p.lat(mob))
    res["Latency to last mobile episode (s)"] = _r(float(p.t[mob[-1][0]] - p.t0) if mob else p.never)
    res["Latency to last immobile episode (s)"] = _r(float(p.t[imm[-1][0]] - p.t0) if imm else p.never)
    if np.isfinite(k.motion_pct).any():
        fr = p.eps(K.freezing)
        t_fr = float(dur[k.freezing].sum())
        res["Time freezing (s)"] = _r(t_fr)
        res["Freezing (%)"] = _r(100 * t_fr / T if T > 0 else math.nan, 2)
        res["Freezing episodes"] = len(fr)
        res["Latency to first freezing (s)"] = _r(p.lat(fr))
        res["Mean freezing episode (s)"] = _r(p.ep_time(fr) / len(fr) if fr else 0.0)
        res["Mean motion (% body)"] = _r(np.nanmean(k.motion_pct), 2)
        res["Longest freezing episode (s)"] = _r(max((dur[a:b].sum() for a, b in fr), default=0.0))
        res["Shortest freezing episode (s)"] = _r(min((dur[a:b].sum() for a, b in fr), default=0.0))
    _path_shape(res, p, total, t_mob)


def _path_shape(res, p: _Period, total: float, t_mob: float):
    """Path efficiency, turning and rotations (no turn or rotation across a pause)."""
    s, k, n, u = p.P.s, p.k, p.n, p.P.app.unit
    ok = np.isfinite(k.ux)
    if ok.sum() >= 2:
        j0, j1 = np.flatnonzero(ok)[[0, -1]]
        straight = math.hypot(k.ux[j1] - k.ux[j0], k.uy[j1] - k.uy[j0])
        res["Path efficiency"] = _r(straight / total if total > 0 else math.nan)
        res["Path tortuosity"] = _r(total / straight if straight > 0 else math.nan)
    h = k.heading.copy()
    seg_id = np.cumsum(k.breaks) if k.breaks is not None else np.zeros(n, int)
    moving = np.isfinite(h) & (k.speed > max(s.mobility_threshold, 1e-9))
    mi = np.flatnonzero(moving)
    if len(mi) > 1:
        dturn = np.abs((np.diff(h[mi]) + 180) % 360 - 180)
        dturn = dturn[seg_id[mi[1:]] == seg_id[mi[:-1]]]
        abs_turn = float(dturn.sum())
    else:
        dturn = np.zeros(0)
        abs_turn = 0.0
    res["Absolute turn angle (deg)"] = _r(abs_turn, 1)
    res[f"Meander (deg/{u})"] = _r(abs_turn / total if total > 0 else math.nan)
    res["Mean turn angle (deg)"] = _r(dturn.mean() if len(dturn) else math.nan, 2)
    res["Angular velocity (deg/s)"] = _r(abs_turn / t_mob if t_mob > 0 else math.nan, 2)

    def rotations(angle):
        cw = acw = 0
        for a, b in segments(n, k.breaks):
            c1, c2 = count_rotations(angle[a:b], s.rotation_reset_deg)
            cw, acw = cw + c1, acw + c2
        return cw, acw

    angle = p.track.angle
    res["Rotations clockwise"], res["Rotations anticlockwise"] = rotations(angle if np.isfinite(angle).sum() > 2
                                                                           else h)
    if np.isfinite(angle).sum() > 2 and np.isfinite(h).sum() > 2:
        # rotations of the direction of travel (the rotations above follow the body / head orientation)
        res["Path rotations clockwise"], res["Path rotations anticlockwise"] = rotations(h)


def _arena_position(res, p: _Period):
    """Thigmotaxis and position in the arena."""
    s, k, app, u = p.P.s, p.k, p.P.app, p.P.app.unit
    try:
        arena = app.arena_or_bounds()
    except ValueError:
        return
    dwall = arena.distance_to_edge(k.x, k.y) * k.scale
    thr = s.thigmotaxis_distance
    if not thr or thr <= 0:
        x0, y0, x1, y1 = arena.bounds()
        thr = 0.25 * min(x1 - x0, y1 - y0) / 2 * k.scale
    inside_arena = arena.contains(k.x, k.y)
    res[f"Mean distance from wall ({u})"] = _r(np.nanmean(np.where(inside_arena, dwall, np.nan)), 2)
    res["Thigmotaxis (%)"] = p.pct((dwall <= thr) & inside_arena)
    res["Time outside arena (s)"] = _r(p.dur[~inside_arena].sum())
    acx, acy = arena.centroid()
    dc = np.hypot(k.x - acx, k.y - acy) * k.scale
    res[f"Mean distance from centre ({u})"] = _r(np.nanmean(dc), 2)
    res[f"Max distance from centre ({u})"] = _r(np.nanmax(dc) if np.isfinite(dc).any() else math.nan, 2)
    if s.arena_quadrants:
        east, south = k.x >= acx, k.y >= acy
        for q, m in (("NE", east & ~south), ("SE", east & south), ("SW", ~east & south), ("NW", ~east & ~south)):
            res[f"Arena quadrant {q}: time (%)"] = p.pct(m)


def _zones(res, p: _Period):
    P, s, k, K, app, t, dur, T, u = p.P, p.P.s, p.k, p.P.k, p.P.app, p.t, p.dur, p.T, p.P.app.unit
    has_motion = np.isfinite(k.motion_pct).any()
    grid_cells = {c for g in app.grids for c in g.zones}
    zone_names = [z.name for z in app.zones if z.name in p.memb and z.name not in grid_cells]
    first_zone = None
    if zone_names:
        firsts = [(t[v[0][0]], i) for i, zn in enumerate(zone_names)
                  if (v := p.eps(P.visits_mask(zn, P.memb[zn]), entries=True))]
        first_zone = zone_names[min(firsts)[1]] if firsts else None
        res["First zone entered"] = first_zone or "None"
        _zone_lists(res, p, zone_names)
    total = float(k.step.sum())
    for zn in p.memb:
        fm = P.visits_mask(zn, P.memb[zn])  # whole test, short visits removed
        vm = fm[p.sl]  # time in the zone: every visit, including the initial one and one carried into the period
        visits = p.eps(fm, entries=True)  # entries
        tz = float(dur[vm].sum())
        res[f"{zn}: time (s)"] = _r(tz)
        res[f"{zn}: time (%)"] = _r(100 * tz / T if T > 0 else math.nan, 2)
        res[f"{zn}: entries"] = len(visits)
        res[f"{zn}: latency to first entry (s)"] = _r(p.lat(visits))
        dz = float(k.step[vm].sum())
        res[f"{zn}: distance ({u})"] = _r(dz, 2)
        res[f"{zn}: mean speed ({u}/s)"] = _r(dz / tz if tz > 0 else math.nan)
        res[f"{zn}: mean visit (s)"] = _r(p.ep_time(visits) / len(visits) if visits else 0.0)
        res[f"{zn}: time immobile (s)"] = _r(dur[vm & ~k.mobile].sum())
        if has_motion:
            res[f"{zn}: time freezing (s)"] = _r(dur[vm & k.freezing].sum())
        if P.head_memb is not None and zn in P.head_memb:
            hfm = P.visits_mask(("head", zn), P.head_memb[zn])
            hv = p.eps(hfm, entries=True)
            res[f"{zn}: head entries"] = len(hv)
            res[f"{zn}: head time (s)"] = _r(dur[hfm[p.sl]].sum())
            res[f"{zn}: latency to head entry (s)"] = _r(p.lat(hv))
        res[f"{zn}: latency to second entry (s)"] = _r(float(t[visits[1][0]] - p.t0) if len(visits) > 1 else p.never)
        exits = [b for a, b in runs(fm) if p.i0 < b < p.i1]
        res[f"{zn}: time of last exit (s)"] = _r(float(K.t[exits[-1] - 1] + K.dur[exits[-1] - 1] - p.t0)
                                                if exits else math.nan)
        res[f"{zn}: exits"] = len(exits)
        res[f"{zn}: latency to first exit (s)"] = _r(float(K.t[exits[0] - 1] + K.dur[exits[0] - 1] - p.t0)
                                                    if exits else p.never)
        res[f"{zn}: latency to last entry (s)"] = _r(float(t[visits[-1][0]] - p.t0) if visits else p.never)
        if zn in zone_names:
            res[f"{zn}: was first zone entered"] = "Yes" if zn == first_zone else "No"
        vdur = [float(dur[a:b].sum()) for a, b in visits]
        res[f"{zn}: longest visit (s)"] = _r(max(vdur, default=0.0))
        res[f"{zn}: shortest visit (s)"] = _r(min(vdur, default=0.0))
        res[f"{zn}: entries (/min)"] = _r(len(visits) / (T / 60) if T > 0 else math.nan)
        res[f"{zn}: time mobile (s)"] = _r(dur[vm & k.mobile].sum())
        res[f"{zn}: immobile episodes"] = len(p.eps(fm & ~K.mobile))
        if has_motion:
            res[f"{zn}: freezing episodes"] = len(p.eps(fm & K.freezing))
        sp = p.sp
        res[f"{zn}: max speed ({u}/s)"] = _r(np.nanmax(sp[vm]) if vm.any() and np.isfinite(sp[vm]).any() else math.nan)
        # route to the zone: distance travelled and path efficiency (straight line / path) up to the first entry
        if visits:
            j = visits[0][0]
            d_first = float(k.step[1:j + 1].sum()) if j > 0 else 0.0
            ok_xy = np.flatnonzero(np.isfinite(k.ux[:j + 1]))
            straight = (math.hypot(k.ux[j] - k.ux[ok_xy[0]], k.uy[j] - k.uy[ok_xy[0]])
                        if len(ok_xy) and math.isfinite(k.ux[j]) else math.nan)
            res[f"{zn}: distance before first entry ({u})"] = _r(d_first, 2)
            res[f"{zn}: path efficiency to first entry"] = _r(straight / d_first if d_first > 0 else math.nan)
        else:
            res[f"{zn}: distance before first entry ({u})"] = _r(total if s.latency_if_never == "duration"
                                                                 else math.nan, 2)
            res[f"{zn}: path efficiency to first entry"] = math.nan
        zobj = app.zone(zn)
        if zobj is not None and zn not in grid_cells:
            dz_full = P.cached(("dist", zn), lambda z=zobj, m=P.memb[zn]: np.where(m, 0.0, _edge(P, z)))
            dzs = dz_full[p.sl]
            res[f"{zn}: mean distance from zone ({u})"] = _r(np.nanmean(dzs) if np.isfinite(dzs).any() else math.nan,
                                                             2)
            out_d = dzs[~vm & np.isfinite(dzs)]
            fin = np.flatnonzero(np.isfinite(dzs))
            res[f"{zn}: initial distance from zone ({u})"] = _r(dzs[fin[0]] if len(fin) else math.nan, 2)
            res[f"{zn}: max distance from zone ({u})"] = _r(out_d.max() if len(out_d) else math.nan, 2)
            res[f"{zn}: min distance from zone when outside ({u})"] = _r(out_d.min() if len(out_d) else math.nan, 2)
            res[f"{zn}: cumulative distance from zone ({u}·s)"] = _r(np.nansum(dzs * dur), 1)
        if p.facing is not None and zobj is not None:
            hxf, hyf, ang_body = p.facing
            zx, zy = zobj.shape.centroid()
            diff = _angle_diff(np.arctan2(zy - hyf, zx - hxf), ang_body)
            head_in = p.head_memb[zn] if p.head_memb is not None and zn in p.head_memb else np.zeros(p.n, bool)
            res[f"{zn}: time facing (s)"] = _r(dur[(diff <= s.exploration_facing_deg) & ~head_in & ~p.hid_any].sum())
        if zn not in grid_cells:
            _zone_more(res, p, zn, fm, vm, visits, vdur)
    for hz in p.hidden:
        res[f"{hz}: time hidden (s)"] = _r(dur[p.hidden[hz]].sum())
    if app.zones:
        tr_at = P.cached("transitions", lambda: _transitions(P))
        res["Zone transitions"] = int(np.count_nonzero((tr_at >= p.i0) & (tr_at < p.i1)))


def _transitions(P: _Prepared) -> np.ndarray:
    """Frames of the whole test where the smallest zone the animal is in changes (none across a pause)."""
    zones = P.app.zones
    order = sorted(range(len(zones)), key=lambda i: zones[i].shape.area())
    cur = np.full(len(P.k.t), -1)
    for i in reversed(order):
        m = P.memb.get(zones[i].name)
        if m is not None:
            cur[m] = i
    idx = np.flatnonzero(cur >= 0)
    ch = cur[idx[1:]] != cur[idx[:-1]]
    if P.breaks.any():
        bc = np.cumsum(P.breaks)
        ch &= bc[idx[1:]] == bc[idx[:-1]]
    return idx[1:][ch]


# ---------------------------------------------------------------------------
# more zone measures: visited / investigated zone lists, investigation, hidden-zone partial exits, head, distance
# to the border, heading towards the zone, turning, CIPL and line crossings in the zone

def _list_text(values) -> str:
    """A list result (e.g. visit durations): comma-separated values, as text so that statistics skip it."""
    return ", ".join(format(_r(v), ".10g") for v in values)


def _stats3(v: np.ndarray) -> tuple[float, float, float]:
    """(mean, max, min) of the finite values (NaN if none)."""
    v = v[np.isfinite(v)]
    return (float(v.mean()), float(v.max()), float(v.min())) if len(v) else (math.nan,) * 3


def _edge(P: _Prepared, z) -> np.ndarray:
    """Whole test: distance of the centre from the edge of zone z (units), inside or outside."""
    K = P.k
    return P.cached(("edge", z.name), lambda: z.shape.distance_to_edge(K.x, K.y) * K.scale)


def _head(P: _Prepared):
    """Whole test: (head x, head y in px forward filled, distance the head travels into each frame in units), or
    None without a tracked head. The head path is smoothed like the centre's; nothing is counted across a pause."""
    def make():
        tr, K = P.track, P.k
        if not tr.has_head():
            return None
        hx, hy = ffill(tr.hx), ffill(tr.hy)
        win = max(1, int(round(P.s.speed_smoothing_s / max(tr.dt, 1e-6))))
        ux = seg_moving_average(hx, win, P.breaks) * K.scale
        uy = seg_moving_average(hy, win, P.breaks) * K.scale
        step = np.zeros(len(hx))
        if len(hx) > 1:
            step[1:] = np.nan_to_num(np.hypot(np.diff(ux), np.diff(uy)))
            step[P.breaks] = 0.0
        return hx, hy, step
    return P.cached("head", make)


def _investigation(P: _Prepared, z) -> np.ndarray:
    """Whole test: frames in which the animal investigates zone z - its head is in the zone, or within the zone's
    investigation distance and (when the body orientation is tracked) pointing at it (within the exploration
    facing angle of the direction to the zone centre); never while hidden. Bouts shorter than the minimum entry
    duration are ignored."""
    def make():
        K, s = P.k, P.s
        H = _head(P)
        hx, hy = (H[0], H[1]) if H is not None else (K.x, K.y)
        inside = z.shape.contains(hx, hy)
        with np.errstate(invalid="ignore"):
            inv = inside | ((z.shape.distance_to_edge(hx, hy) <= z.investigation_distance_cm / P.app.scale)
                            & np.isfinite(hx))
            ang = ffill(P.track.angle)
            if np.isfinite(ang).any():
                zx, zy = z.shape.centroid()
                diff = _angle_diff(np.arctan2(zy - hy, zx - hx), np.radians(ang))
                inv &= inside | ~np.isfinite(diff) | (diff <= s.exploration_facing_deg)
        return drop_short_runs(inv & ~P.hid_any, K.t, K.dur, s.entry_min_duration_s, value=True)
    return P.cached(("inv", z.name), make)


def _investigation_firsts(p: _Period) -> list[tuple[float, str]]:
    """(time of the first investigation, zone) of the investigation zones investigated in the period, in order."""
    P = p.P

    def make():
        out = []
        for z in P.app.zones:
            if z.investigation_distance_cm > 0 and z.name in P.memb:
                ep = p.eps(_investigation(P, z))
                if ep:
                    out.append((float(p.t[ep[0][0]]), z.name))
        return sorted(out, key=lambda e: e[0])
    return P.cached(("inv_firsts", p.i0, p.i1), make)


def _zone_lists(res, p: _Period, zone_names: list[str]):
    """Visited zones (in the order of their first entry) and investigated zones (order of first investigation)."""
    P = p.P
    firsts = []
    for zn in zone_names:
        v = p.eps(P.visits_mask(zn, P.memb[zn]), entries=True)
        if v:
            firsts.append((float(p.t[v[0][0]]), zn))
    res["Visited zones"] = ", ".join(zn for _, zn in sorted(firsts, key=lambda e: e[0]))
    if any(z.investigation_distance_cm > 0 for z in P.app.zones):
        res["Investigated zones"] = ", ".join(zn for _, zn in _investigation_firsts(p))


def _zone_more(res, p: _Period, zn: str, fm: np.ndarray, vm: np.ndarray, visits: list, vdur: list):
    """The zone measures beyond time / entries / distance (fm: whole-test visits, vm: in the zone in the period,
    visits: entries in the period, vdur: their durations)."""
    z = p.P.app.zone(zn)  # None for a zone group
    res[f"{zn}: visit durations (s)"] = _list_text(vdur)
    if z is not None and z.investigation_distance_cm > 0:
        _zone_investigation(res, p, z)
    if z is not None and z.hidden:
        _zone_partial_exits(res, p, z)
    _zone_head(res, p, zn, z)
    if z is not None:
        _zone_border(res, p, z, vm)
        _zone_heading(res, p, z, vm)
    _zone_turning(res, p, zn, vm)
    if z is not None:
        _zone_cipl(res, p, z, visits)
    _zone_lines(res, p, zn, fm)


def _zone_investigation(res, p: _Period, z):
    """Investigation of an investigation zone as separate measures (see _investigation)."""
    P, K, k, dur, u, zn = p.P, p.P.k, p.k, p.dur, p.P.app.unit, z.name
    inv_full = _investigation(P, z)
    inv = inv_full[p.sl]
    bouts = p.eps(inv_full)
    bd = [float(dur[a:b].sum()) for a, b in bouts]
    ti = float(dur[inv].sum())
    res[f"{zn}: investigation bouts"] = len(bouts)
    res[f"{zn}: time investigating (s)"] = _r(ti)
    res[f"{zn}: latency to first investigation (s)"] = _r(p.lat(bouts))
    res[f"{zn}: latency to end of first investigation (s)"] = _r(
        float(p.t[bouts[0][1] - 1] + dur[bouts[0][1] - 1] - p.t0) if bouts else p.never)
    firsts = _investigation_firsts(p)
    res[f"{zn}: was first zone investigated"] = "Yes" if firsts and firsts[0][1] == zn else "No"
    res[f"{zn}: longest investigation bout (s)"] = _r(max(bd, default=0.0))
    res[f"{zn}: shortest investigation bout (s)"] = _r(min(bd, default=0.0))
    res[f"{zn}: mean investigation bout (s)"] = _r(sum(bd) / len(bd) if bd else 0.0)
    res[f"{zn}: investigation durations (s)"] = _list_text(bd)
    di = float(k.step[inv].sum())
    res[f"{zn}: distance while investigating ({u})"] = _r(di, 2)
    if bouts:
        j = bouts[0][0]
        d_first = float(k.step[1:j + 1].sum()) if j > 0 else 0.0
    else:
        d_first = float(k.step.sum()) if P.s.latency_if_never == "duration" else math.nan
    res[f"{zn}: distance before first investigation ({u})"] = _r(d_first, 2)
    res[f"{zn}: mean speed while investigating ({u}/s)"] = _r(di / ti if ti > 0 else math.nan)
    res[f"{zn}: time mobile while investigating (s)"] = _r(dur[inv & k.mobile].sum())
    res[f"{zn}: time immobile while investigating (s)"] = _r(dur[inv & ~k.mobile].sum())
    res[f"{zn}: immobile episodes while investigating"] = len(p.eps(inv_full & ~K.mobile))
    if np.isfinite(k.motion_pct).any():
        res[f"{zn}: time freezing while investigating (s)"] = _r(dur[inv & k.freezing].sum())
        res[f"{zn}: freezing episodes while investigating"] = len(p.eps(inv_full & K.freezing))


def _zone_partial_exits(res, p: _Period, z):
    """Hidden zone: a partial exit is a stretch in which the animal is seen between two times it is hidden in the
    zone without going further from the zone than the hidden-zone distance (e.g. peeking out of a nest)."""
    P, zn = p.P, z.name

    def make():
        hm = P.hidden.get(zn)
        out = np.zeros(len(P.k.t), bool)
        if hm is None:
            return out
        margin = (P.s.hidden_zone_margin if P.s.hidden_zone_margin and P.s.hidden_zone_margin > 0
                  else 0.5 * math.sqrt(max(z.shape.area(), 1.0)) * P.app.scale)
        near = P.cached(("cin", zn), lambda: z.shape.contains(P.k.x, P.k.y)) | (_edge(P, z) <= margin)
        hr = runs(hm)
        for (_, e0), (s1, _) in zip(hr[:-1], hr[1:]):
            if near[e0:s1].all() and not P.breaks[e0:s1 + 1].any():
                out[e0:s1] = True
        return out
    part = P.cached(("partial", zn), make)
    res[f"{zn}: partial exits"] = len(p.eps(part))
    res[f"{zn}: time partially exited (s)"] = _r(p.dur[part[p.sl]].sum())


def _zone_head(res, p: _Period, zn: str, z):
    """Head measures: first head exit, distance travelled by the head in the zone, time the head is in while the
    centre is out, head distance from the zone and from its border when inside."""
    P, K, dur, u = p.P, p.P.k, p.dur, p.P.app.unit
    H = _head(P)
    if H is None or P.head_memb is None or zn not in P.head_memb:
        return
    hfm = P.visits_mask(("head", zn), P.head_memb[zn])
    hin = hfm[p.sl]
    ends = P.cached(("head_exits", zn), lambda: np.array([b for _, b in runs(hfm)], int))
    exits = ends[(ends > p.i0) & (ends < p.i1)]
    res[f"{zn}: latency to first head exit (s)"] = _r(float(K.t[exits[0] - 1] + K.dur[exits[0] - 1] - p.t0)
                                                     if len(exits) else p.never)
    res[f"{zn}: head distance ({u})"] = _r(H[2][p.sl][hin].sum(), 2)
    cin = P.cached(("occ", "centre"), lambda: occupancy(P.track, P.app, P.s, part="centre")[0])
    if zn in cin:
        res[f"{zn}: time head in zone with centre outside (s)"] = _r(dur[hin & ~cin[zn][p.sl]].sum())
    if z is None:
        return
    hx, hy = H[0], H[1]
    h_in = P.cached(("hin", zn), lambda: z.shape.contains(hx, hy))[p.sl]
    h_edge = P.cached(("hedge", zn), lambda: z.shape.distance_to_edge(hx, hy) * K.scale)[p.sl]
    seen = ~p.hid_any
    mean = np.where(h_in, 0.0, h_edge)[seen]
    res[f"{zn}: mean head distance from zone ({u})"] = _r(np.nanmean(mean) if np.isfinite(mean).any()
                                                          else math.nan, 2)
    _, mx, mn = _stats3(h_edge[~h_in & seen])
    res[f"{zn}: max head distance from zone ({u})"] = _r(mx, 2)
    res[f"{zn}: min head distance from zone when outside ({u})"] = _r(mn, 2)
    mean, mx, mn = _stats3(h_edge[h_in & seen])
    res[f"{zn}: mean head distance to border when inside ({u})"] = _r(mean, 2)
    res[f"{zn}: max head distance to border when inside ({u})"] = _r(mx, 2)
    res[f"{zn}: min head distance to border when inside ({u})"] = _r(mn, 2)


def _zone_border(res, p: _Period, z, vm: np.ndarray):
    """Distance of the centre from the zone border while the animal is in the zone (and its centre inside it)."""
    P, zn, u = p.P, z.name, p.P.app.unit
    c_in = P.cached(("cin", zn), lambda: z.shape.contains(P.k.x, P.k.y))[p.sl]
    mean, mx, mn = _stats3(_edge(P, z)[p.sl][vm & c_in & ~p.hid_any])
    res[f"{zn}: mean distance to border when inside ({u})"] = _r(mean, 2)
    res[f"{zn}: max distance to border when inside ({u})"] = _r(mx, 2)
    res[f"{zn}: min distance to border when inside ({u})"] = _r(mn, 2)


def _zone_heading_arrays(P: _Prepared, z) -> dict:
    """Whole test: change of the (smoothed) distance from the zone into each frame (0 across a pause), and the
    signed heading error (direction of travel minus direction to the zone centre, -180..180 deg; positive =
    clockwise of the zone on screen)."""
    K = P.k
    with np.errstate(invalid="ignore"):
        px, py = K.ux / K.scale, K.uy / K.scale
        d = np.where(z.shape.contains(px, py), 0.0, z.shape.distance_to_edge(px, py) * K.scale)
        dd = np.zeros(len(d))
        if len(d) > 1:
            dd[1:] = np.nan_to_num(np.diff(d))
            dd[P.breaks] = 0.0
        zx, zy = z.shape.centroid()
        brg = np.degrees(np.arctan2(zy * K.scale - K.uy, zx * K.scale - K.ux))
        err = (K.heading - brg + 180.0) % 360.0 - 180.0
    return {"dd": dd, "err": err}


def _zone_heading(res, p: _Period, z, vm: np.ndarray):
    """Getting closer / further away, moving towards / away, heading errors and orientation towards the zone."""
    P, s, k, dur, zn = p.P, p.P.s, p.k, p.dur, z.name
    A = P.cached(("zheading", zn), lambda: _zone_heading_arrays(P, z))
    dd, err = A["dd"][p.sl], A["err"][p.sl]
    out = ~vm & ~p.hid_any
    tol = s.exploration_facing_deg
    res[f"{zn}: time getting closer (s)"] = _r(dur[out & (dd < -1e-9)].sum())
    res[f"{zn}: time getting further away (s)"] = _r(dur[out & (dd > 1e-9)].sum())
    with np.errstate(invalid="ignore"):
        ae = np.abs(err)
        res[f"{zn}: time moving towards (s)"] = _r(dur[out & k.mobile & (ae <= tol)].sum())
        res[f"{zn}: time moving away (s)"] = _r(dur[out & k.mobile & (ae >= 180.0 - tol)].sum())
    moving = ae[out & k.mobile]
    res[f"{zn}: mean absolute heading error (deg)"] = _r(np.nanmean(moving) if np.isfinite(moving).any()
                                                         else math.nan, 1)
    # initial heading error: direction from the first position to the position ~1 s later vs direction to the zone
    signed = math.nan
    ok = np.flatnonzero(np.isfinite(k.ux))
    if len(ok) > 2:
        i0 = ok[0]
        j = min(max(int(np.searchsorted(p.t, p.t[i0] + 1.0)), i0 + 1), p.n - 1)
        zx, zy = z.shape.centroid()
        hdx, hdy = k.ux[j] - k.ux[i0], k.uy[j] - k.uy[i0]
        tdx, tdy = zx * k.scale - k.ux[i0], zy * k.scale - k.uy[i0]
        start_in = bool(z.shape.contains(np.array([k.ux[i0] / k.scale]), np.array([k.uy[i0] / k.scale]))[0])
        if not start_in and math.hypot(hdx, hdy) > 1e-9 and math.hypot(tdx, tdy) > 0:
            signed = (math.degrees(math.atan2(hdy, hdx) - math.atan2(tdy, tdx)) + 180.0) % 360.0 - 180.0
    res[f"{zn}: initial heading error (deg)"] = _r(signed, 1)
    res[f"{zn}: initial absolute heading error (deg)"] = _r(abs(signed), 1)
    if p.facing is not None:
        def orient():
            K, (zx, zy) = P.k, z.shape.centroid()
            with np.errstate(invalid="ignore"):
                return _angle_diff(np.arctan2(zy - K.y, zx - K.x), np.radians(ffill(P.track.angle))) <= tol
        towards = P.cached(("oriented", zn), orient)[p.sl]
        res[f"{zn}: time oriented towards zone centre when inside (s)"] = _r(dur[vm & ~p.hid_any & towards].sum())


def _zone_turning(res, p: _Period, zn: str, vm: np.ndarray):
    """Absolute turn angle (direction of travel, as the whole-test measure) and absolute head turn angle (body
    orientation) while in the zone; nothing across a pause."""
    s, k = p.P.s, p.k
    brk = np.asarray(k.breaks, bool) if k.breaks is not None else np.zeros(p.n, bool)
    seg_id = np.cumsum(brk)
    h = k.heading
    mi = np.flatnonzero(np.isfinite(h) & (k.speed > max(s.mobility_threshold, 1e-9)))
    turn = 0.0
    if len(mi) > 1:
        d = np.abs((np.diff(h[mi]) + 180) % 360 - 180)
        keep = (seg_id[mi[1:]] == seg_id[mi[:-1]]) & vm[mi[1:]] & vm[mi[:-1]]
        turn = float(d[keep].sum())
    res[f"{zn}: absolute turn angle (deg)"] = _r(turn, 1)
    ang = p.track.angle
    if np.isfinite(ang).sum() > 2:
        d = np.abs((np.diff(ang) + 180) % 360 - 180)
        keep = np.isfinite(d) & ~brk[1:] & vm[1:] & vm[:-1]
        res[f"{zn}: absolute head turn angle (deg)"] = _r(float(d[keep].sum()), 1)


def _zone_cipl(res, p: _Period, z, visits: list):
    """Corrected integrated path length (Gallagher): the distance from the zone sampled every second from the
    start of the period to the first entry (or the end of the period), minus the same sum for an ideal path
    straight to the zone at the animal's mean speed over that time."""
    P, k, zn = p.P, p.k, z.name
    cipl = math.nan
    ok = np.flatnonzero(np.isfinite(k.ux))
    stop = visits[0][0] if visits else p.n
    if len(ok) and stop > ok[0]:
        i0 = ok[0]
        dz = P.cached(("dist", zn), lambda: np.where(P.memb[zn], 0.0, _edge(P, z)))[p.sl]
        t_stop = float(p.t[stop]) if stop < p.n else p.t0 + p.T
        span = t_stop - float(p.t[i0])
        idx = np.searchsorted(p.t, p.t[i0] + np.arange(0.0, span, 1.0), "right") - 1
        d = dz[np.clip(idx, i0, stop - 1)]
        d = d[np.isfinite(d)]
        v = float(k.step[i0 + 1:stop].sum()) / span if span > 0 else 0.0
        if len(d) and v > 0:  # the ideal path, sampled over the same seconds, stays at 0 once it arrives
            ideal = np.maximum(float(d[0]) - v * np.arange(len(d)), 0.0)
            cipl = float(d.sum() - ideal.sum())
    res[f"{zn}: corrected integrated path length ({p.P.app.unit}·s)"] = _r(cipl, 1)


def _zone_lines(res, p: _Period, zn: str, fm: np.ndarray):
    """Crossings of any line while the animal is in the zone (in it before and after the crossing)."""
    P = p.P
    N = len(P.k.t)
    if not P.app.lines or N < 2:
        return
    j = np.arange(1, N)
    inside = fm[1:] & fm[:-1] & (j >= p.i0) & (j < p.i1)
    res[f"{zn}: line crossings"] = int(sum(int((_line_hits(P, ln)[0] & inside).sum()) for ln in P.app.lines))


def _points(res, p: _Period):
    P, s, k, dur, u = p.P, p.P.s, p.k, p.dur, p.P.app.unit
    for pt in P.app.points:
        A = P.cached(("point", pt.name), lambda pt=pt: _point_arrays(P, pt))
        dist = A["dist"][p.sl]
        res[f"{pt.name}: mean distance ({u})"] = _r(np.nanmean(dist), 2)
        res[f"{pt.name}: min distance ({u})"] = _r(np.nanmin(dist), 2)
        if "near" in A:
            if "explore" in A:
                res[f"{pt.name}: time exploring (s)"] = _r(dur[A["explore"][p.sl]].sum())
                res[f"{pt.name}: exploration bouts"] = len(p.eps(A["explore_bouts"]))
                res[f"{pt.name}: latency to explore (s)"] = _r(p.lat(p.eps(A["explore"])))
            res[f"{pt.name}: time near (s)"] = _r(dur[A["near"][p.sl]].sum())
            ap = p.eps(A["near"])
            res[f"{pt.name}: approaches"] = len(ap)
            res[f"{pt.name}: latency to approach (s)"] = _r(p.lat(ap))
        res[f"{pt.name}: max distance ({u})"] = _r(np.nanmax(dist), 2)
        dd = A["dd"][p.sl]
        towards = k.mobile & (dd < 0)
        away = k.mobile & (dd > 0)
        res[f"{pt.name}: time moving towards (s)"] = _r(dur[towards].sum())
        res[f"{pt.name}: time moving away (s)"] = _r(dur[away].sum())
        res[f"{pt.name}: distance moved towards ({u})"] = _r(-dd[towards].sum(), 2)
        res[f"{pt.name}: distance moved away ({u})"] = _r(dd[away].sum(), 2)
        if p.facing is not None:
            hxf, hyf, ang_body = p.facing
            diff = _angle_diff(np.arctan2(pt.y - hyf, pt.x - hxf), ang_body)
            res[f"{pt.name}: time head oriented towards (s)"] = _r(dur[diff <= s.exploration_facing_deg].sum())
            res[f"{pt.name}: time head oriented away (s)"] = _r(dur[diff >= 180 - s.exploration_facing_deg].sum())
            res[f"{pt.name}: mean head angle (deg)"] = _r(np.nanmean(diff), 1)
            res[f"{pt.name}: head turns towards"] = len(p.eps(A["head_towards"]))


def _lines(res, p: _Period):
    """Line crossings (none across a pause)."""
    P, K = p.P, p.P.k
    N = len(K.t)
    if not P.app.lines or N < 2:
        return
    n_cross = 0
    for ln in P.app.lines:
        hit, sign = _line_hits(P, ln)
        j = np.arange(1, N)  # frame each segment ends in
        hit = hit & (j >= p.i0) & (j < p.i1)
        res[f"{ln.name}: crossings"] = int(hit.sum())
        res[f"{ln.name}: crossings left-to-right"] = int((hit & (sign > 0)).sum())
        res[f"{ln.name}: crossings right-to-left"] = int((hit & (sign < 0)).sum())
        idx = np.flatnonzero(hit)
        res[f"{ln.name}: latency to first crossing (s)"] = _r(float(K.t[idx[0] + 1] - p.t0) if len(idx) else p.never)
        n_cross += int(hit.sum())
    res["Total line crossings"] = n_cross


def _line_hits(P: _Prepared, ln) -> tuple[np.ndarray, np.ndarray]:
    """Whole test: (crossed, direction sign) of line ln for each step between consecutive frames (none across a
    pause); step i ends in frame i + 1."""
    K = P.k

    def cross():
        xy = P.cached("xy", lambda: np.column_stack([K.x, K.y]))
        hit, sign = segments_intersect(xy[:-1], xy[1:], (ln.x1, ln.y1), (ln.x2, ln.y2))
        return np.asarray(hit, bool) & ~P.breaks[1:], np.asarray(sign)
    return P.cached(("line", ln.name), cross)


def _grids_and_sequences(res, p: _Period):
    s, app = p.P.s, p.P.app
    for g in app.grids:
        res.update(grid_measures(g, p.memb, p.t, p.dur, p.t0, p.T, s))
    if app.sequences:
        from .sequences import find_sequences, other_zones, sequence_measures

        for q in app.sequences:
            names = list(dict.fromkeys(list(q.steps) + ([] if q.allow_other else other_zones(app, q))))
            att = find_sequences(q, p.seq([zn for zn in names if zn in p.memb]), names)
            res.update(sequence_measures(q, att, p.t0, p.T, s.latency_if_never))


def _template(res, p: _Period):
    P = p.P
    template_measures(TemplateData(res, p.track, P.app, P.s, p.k, p.memb, p.head_memb, p.seq, p.i0 == 0))


def _social(res, p: _Period):
    for j, ot in enumerate(p.P.others):
        o = ot.slice_time(*p.t_range) if p.t_range is not None else ot
        res.update(social_measures(p.track, p.k, o, p.P.app, p.P.s, f"Animal {ot.meta.get('animal_index', j + 1)}"))


def _tracking_quality(res, p: _Period):
    """How well the animal was tracked: positions recorded and frames where the tracking looks sound."""
    tr = p.track
    centre = tr.detected & np.isfinite(tr.x) & np.isfinite(tr.y)
    head = centre & np.isfinite(tr.hx) & np.isfinite(tr.hy)
    n_c, n_h = int(centre.sum()), int(head.sum())
    res["Centre positions recorded"] = n_c
    res["Head positions recorded"] = n_h
    if p.P.clean.has_head():
        res["Head tracked (% of tracked frames)"] = _r(100 * n_h / n_c if n_c else math.nan, 1)
    q = p.P.cached("quality", lambda: _quality_mask(p.P))[p.sl]
    res["Tracking quality (%)"] = _r(100 * q.mean() if p.n else math.nan, 1)


def _quality_mask(P: _Prepared) -> np.ndarray:
    """Frames tracked soundly: the animal detected (not interpolated or lost), its head found when the head is
    tracked at all, and its body area within half to twice its usual area (a larger or smaller blob is usually a
    shadow, a reflection, part of the animal or a merge with another object). Frames where the animal is hidden
    in a hidden zone count as sound."""
    tr = P.track
    ok = tr.detected & np.isfinite(tr.x) & np.isfinite(tr.y)
    if P.clean.has_head():
        ok &= np.isfinite(tr.hx) & np.isfinite(tr.hy)
    a = tr.area
    if np.isfinite(a[ok]).any():
        med = float(np.nanmedian(a[ok]))
        if med > 0:
            with np.errstate(invalid="ignore"):
                ok &= (a >= 0.5 * med) & (a <= 2.0 * med)
    return ok | P.hid_any


def _activity(res, p: _Period):
    """Pixel-change activity (separate from mobility, which comes from the centre's speed) and the average freezing
    score (the motion value that freezing is detected from)."""
    k, dur, T = p.k, p.dur, p.T
    has = np.isfinite(k.motion_pct)
    if not has.any():
        return
    act_full = p.P.cached("active", lambda: activity_mask(p.P.k, p.P.s))
    t_has = float(dur[has].sum())
    res["Average freezing score (% body)"] = _r(float((k.motion_pct[has] * dur[has]).sum()) / t_has
                                                if t_has > 0 else math.nan, 2)
    t_act = float(dur[act_full[p.sl]].sum())
    res["Time active (s)"] = _r(t_act)
    res["Time inactive (s)"] = _r(T - t_act)
    for label, ep in (("active", p.eps(act_full)), ("inactive", p.eps(~act_full))):
        d = [float(dur[a:b].sum()) for a, b in ep]
        res[f"{label.capitalize()} episodes"] = len(ep)
        res[f"Longest {label} episode (s)"] = _r(max(d, default=0.0))
        res[f"Shortest {label} episode (s)"] = _r(min(d, default=0.0))


def activity_mask(k: Kinematics, s: AnalysisSettings) -> np.ndarray:
    """Per frame: is the animal active, its pixel change (as a % of its body area) at or above
    s.activity_threshold_pct? Frames without a motion value keep the previous state; inactive episodes shorter
    than s.min_inactive_s count as active."""
    with np.errstate(invalid="ignore"):
        act = np.nan_to_num(ffill(k.motion_pct), nan=0.0) >= s.activity_threshold_pct
    return drop_short_runs(act, k.t, k.dur, s.min_inactive_s, value=False)


def _head_motion(res, p: _Period):
    """Distance travelled by the head and how much the head turned (none across a pause)."""
    if not p.P.clean.has_head():
        return
    H = p.P.cached("head_motion", lambda: _head_arrays(p.P))
    turn = H["turn"][p.sl]
    res[f"Head distance ({p.P.app.unit})"] = _r(H["step"][p.sl].sum(), 2)
    res["Head turn angle (deg)"] = _r(np.abs(turn).sum(), 1)
    res["Head turn angle clockwise (deg)"] = _r(turn[turn > 0].sum(), 1)
    res["Head turn angle anticlockwise (deg)"] = _r(-turn[turn < 0].sum(), 1)


def _head_arrays(P: _Prepared) -> dict:
    """Whole-test head path: distance travelled into each frame (units; positions smoothed as the centre's) and the
    signed change of head direction into each frame (deg, clockwise positive since y points down)."""
    tr, K, s = P.track, P.k, P.s
    n = len(K.t)
    win = max(1, int(round(s.speed_smoothing_s / max(tr.dt, 1e-6))))
    hx = seg_moving_average(ffill(tr.hx), win, K.breaks) * K.scale
    hy = seg_moving_average(ffill(tr.hy), win, K.breaks) * K.scale
    step = np.zeros(n)
    turn = np.zeros(n)
    if n > 1:
        step[1:] = np.nan_to_num(np.hypot(np.diff(hx), np.diff(hy)))
        # head direction: the body axis (tail → head). Frames where it is unknown (or the animal is hidden) do not
        # turn, and a jump of more than 90° from one tracked frame to the next is a head / tail swap of the
        # tracker, not a turn. The cumulative direction is smoothed like the positions.
        ang = np.where(P.hid_any, np.nan, tr.angle)
        idx = np.flatnonzero(np.isfinite(ang))
        if len(idx) > 1:
            d = np.zeros(n)
            d[idx[1:]] = (np.diff(ang[idx]) + 180) % 360 - 180
            d[np.abs(d) > 90] = 0.0
            if K.breaks is not None:
                d[K.breaks] = 0.0
            turn[1:] = np.diff(seg_moving_average(np.cumsum(d), win, K.breaks))
    if K.breaks is not None:
        step[K.breaks] = 0.0
        turn[K.breaks] = 0.0
    return {"step": step, "turn": turn}


def rearing_mask(track: Track, s: AnalysisSettings, t: np.ndarray, dur: np.ndarray,
                 exclude: np.ndarray | None = None) -> np.ndarray:
    """Per frame: is the animal rearing?

    Seen from above, a rodent standing on its hind legs covers a smaller area and looks shorter than on all fours.
    The animal's usual size is the median over the frames where it was detected (it spends most of a test on all
    fours). A frame is a rear when the body area is below s.rear_area_pct % of the usual area and, when the head
    and tail are tracked (from the blob shape or the pose model's nose and tail base), the head–tail length is also
    below s.rear_length_pct % of the usual length. Frames where the animal was not detected (or excluded, e.g.
    hidden) are not rears, except gaps of up to 0.2 s inside a rear, which are bridged; rears shorter than
    s.min_rear_s are dropped."""
    n = len(t)
    det = track.detected & np.isfinite(track.area)
    if exclude is not None:
        det &= ~exclude
    if det.sum() < 3:
        return np.zeros(n, bool)
    area = track.area
    base_a = float(np.median(area[det]))
    if not base_a > 0:
        return np.zeros(n, bool)
    with np.errstate(invalid="ignore"):
        rear = det & (area < base_a * s.rear_area_pct / 100.0)
        L = np.hypot(track.hx - track.tx, track.hy - track.ty)
        ok_l = det & np.isfinite(L)
        if ok_l.sum() >= 3:
            base_l = float(np.median(L[ok_l]))
            if base_l > 0:
                rear &= ~ok_l | (L < base_l * s.rear_length_pct / 100.0)
    rear = drop_short_runs(rear, t, dur, 0.2, value=False)
    return drop_short_runs(rear, t, dur, s.min_rear_s, value=True)


def _rears(P: _Prepared) -> np.ndarray:
    """The whole test's rearing frames (hidden frames are not rears)."""
    return P.cached("rearing", lambda: rearing_mask(P.track, P.s, P.k.t, P.k.dur, P.hid_any))


_REAR_NAMES = ("rears", "time rearing (s)", "latency to first rear (s)", "mean rear duration (s)",
               "max rear duration (s)", "min rear duration (s)")


def _rear_results(res, p: _Period, eps, in_mask: np.ndarray, prefix: str = ""):
    """Rear count, time, latency and mean / max / min duration ("Rears", … or "{zone}: rears", …)."""
    d = [float(p.dur[a:b].sum()) for a, b in eps]
    values = (len(eps), _r(p.dur[in_mask].sum()), _r(p.lat(eps)), _r(sum(d) / len(d) if d else 0.0),
              _r(max(d, default=0.0)), _r(min(d, default=0.0)))
    for name, v in zip(_REAR_NAMES, values):
        res[prefix + name if prefix else name[0].upper() + name[1:]] = v


def _rearing(res, p: _Period):
    """Automatically detected rears (s.rearing): count, time, latency, mean / max / min duration."""
    if not p.P.s.rearing:
        return
    full = _rears(p.P)
    _rear_results(res, p, p.eps(full), full[p.sl])


def _zone_rearing(res, p: _Period):
    """Rears per zone: a rear belongs to the zone the animal was in when it started; time rearing counts the
    frames spent rearing in the zone."""
    P = p.P
    if not P.s.rearing:
        return
    full = _rears(P)
    eps = p.eps(full)
    for zn in p.memb:
        vm = P.visits_mask(zn, P.memb[zn])[p.sl]
        _rear_results(res, p, [e for e in eps if vm[e[0]]], vm & full[p.sl], f"{zn}: ")


# the measures of a period, in column order
_SECTIONS = (_detection, _tracking_quality, _locomotion, _activity, _head_motion, _rearing, _arena_position, _zones,
             _zone_rearing, _points, _lines, _grids_and_sequences, _template, _social)


def _period_results(P: _Prepared, i0: int, i1: int, t0: float, T: float, t_range, behaviours, result_variables,
                    io_devices) -> "OrderedDict[str, object]":
    """Measures for frames [i0, i1) of a prepared test (t0: period start, T: period length)."""
    p = _Period(P, i0, i1, t0, T, t_range)
    s = P.s
    res: OrderedDict[str, object] = OrderedDict()
    for section in _SECTIONS:
        section(res, p)
    if behaviours:
        res.update(behaviour_measures(P.events or [], behaviours, t0, t0 + T, zones=p.memb if s.behaviour_by_zone
                                      else None, t=p.t, dur=p.dur, latency_if_never=s.latency_if_never))
    if P.io_events:
        try:
            res.update(io_measures(P.io_events, T, (t0, t0 + T), io_devices))
        except Exception:  # a malformed I/O log must not prevent the other measures
            pass
    for name, v in (result_variables or {}).items():
        try:
            res[f"Variable: {name}"] = _r(float(v))
        except (TypeError, ValueError):
            res[f"Variable: {name}"] = str(v)
    if not P.app.px_per_cm:
        res["Warnings"] = ("Apparatus not calibrated: distances, speeds and distance thresholds (mobility, "
                           "thigmotaxis, contact, ...) are in pixels")
    if s.measure_filter:
        keep = set(s.measure_filter) | {"Test duration (s)", "Warnings"}
        res = OrderedDict((key, v) for key, v in res.items() if key in keep)
    return res


def _point_arrays(P: _Prepared, p) -> dict:
    """Whole-test per-frame arrays for a point of interest."""
    K, s, tr = P.k, P.s, P.track
    out = {}
    dist = np.hypot((K.x - p.x) * K.scale, (K.y - p.y) * K.scale)
    out["dist"] = dist
    radius = p.radius_cm  # in output units
    if radius and radius > 0:
        if tr.has_head():
            hx, hy = ffill(tr.hx), ffill(tr.hy)
            hd = np.hypot((hx - p.x) * K.scale, (hy - p.y) * K.scale)
            near = hd <= radius
            diff = _angle_diff(np.arctan2(p.y - hy, p.x - hx), np.radians(tr.angle))
            explore = near & ((diff <= s.exploration_facing_deg) | (hd <= radius * 0.5))
            out["explore"] = explore
            out["explore_bouts"] = drop_short_runs(explore, K.t, K.dur, 0.2, value=True)
        else:
            near = dist <= radius
        out["near"] = near
    ud = np.hypot(K.ux - p.x * K.scale, K.uy - p.y * K.scale)
    dd = np.zeros(len(K.t))
    if len(dd) > 1:
        dd[1:] = np.nan_to_num(np.diff(ud))
        dd[P.breaks] = 0.0
    out["dd"] = dd
    if tr.has_head() and np.isfinite(tr.angle).any():
        hxf, hyf = ffill(tr.hx), ffill(tr.hy)
        diff = _angle_diff(np.arctan2(p.y - hyf, p.x - hxf), np.radians(ffill(tr.angle)))
        out["head_towards"] = diff <= s.exploration_facing_deg
    return out


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
        nb = (point_segment_distance(mhx, mhy, otx, oty, ohx, ohy) <= nd) & ~nn
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


def behaviour_measures(events: list, behaviours: list[Behaviour], t0: float, t1: float, zones: dict | None = None,
                       t: np.ndarray | None = None, dur: np.ndarray | None = None,
                       latency_if_never: str = "duration") -> "OrderedDict[str, object]":
    """Measures from manually scored events.

    events: [{"behaviour", "t", "t_end" (state only)}]
    zones: optional {zone: per-frame bool} with frame times t / durations dur → the same measures per zone.
    """
    out: OrderedDict[str, object] = OrderedDict()
    T = t1 - t0
    never = T if latency_if_never == "duration" else math.nan
    for b in behaviours:
        name = b.name
        evs = [e for e in events if e.get("behaviour") == name]
        if b.kind == "point":
            ts = sorted(e["t"] for e in evs if t0 <= e["t"] < t1)
            out[f"{name}: count"] = len(ts)
            out[f"{name}: latency (s)"] = _r(ts[0] - t0 if ts else never)
            out[f"{name}: rate (/min)"] = _r(len(ts) / (T / 60) if T > 0 else math.nan)
            if zones and t is not None and len(t):
                for zn, m in zones.items():
                    zt = [x for x in ts if m[min(max(np.searchsorted(t, x, "right") - 1, 0), len(t) - 1)]]
                    out[f"{name} in {zn}: count"] = len(zt)
                    out[f"{name} in {zn}: latency (s)"] = _r(zt[0] - t0 if zt else never)
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
            out[f"{name}: latency (s)"] = _r(spans[0][0] - t0 if spans else never)
            out[f"{name}: mean bout (s)"] = _r(total / len(spans) if spans else 0.0)
            out[f"{name}: longest bout (s)"] = _r(max((bb - a for a, bb in spans), default=0.0))
            out[f"{name}: shortest bout (s)"] = _r(min((bb - a for a, bb in spans), default=0.0))
            out[f"{name}: latency to first release (s)"] = _r(spans[0][1] - t0 if spans else never)
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
                    out[f"{name} in {zn}: latency (s)"] = _r(starts[0] - t0 if starts else never)
                    out[f"{name} in {zn}: mean bout (s)"] = _r(tz / len(starts) if starts else 0.0)
                    out[f"{name} in {zn}: rate (/min)"] = _r(len(starts) / (T / 60) if T > 0 else math.nan)
    return out


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
    periods from s.event_periods). Per-frame states are computed once on the whole test (see analyse) and periods
    are in test time (pauses removed)."""
    kw = dict(kw)
    kw.pop("t_range", None)
    P = _prepare(track, app, s, kw.get("events"), kw.get("io_events"), kw.get("other_tracks"),
                 kw.get("zone_overrides"), kw.get("pauses"), kw.get("duration"))
    rest = dict(behaviours=kw.get("behaviours"), result_variables=kw.get("result_variables"),
                io_devices=kw.get("io_devices"))
    out = [("Whole test", _results(P, None, **rest))]
    tr = P.clean
    dur = tr.t[-1] + tr.dt if len(tr) else 0
    # periods in test time, from the pause-free track and test-time events
    for label, a, b in all_periods(tr, P.app, s, dur, P.events, P.io_events):
        out.append((label, _results(P, (a, b), **rest)))
    return out


def all_periods(track: Track, app: Apparatus, s: AnalysisSettings, duration: float | None = None, events=None,
                io_events=None, zone_overrides=None, pauses=None) -> list[tuple[str, float, float]]:
    """Time bins / custom periods followed by event-anchored periods, in test time (pauses removed, as analyse())."""
    track = drop_pauses(track, pauses)[0]
    events, io_events = shift_events(events, pauses), shift_events(io_events, pauses)
    if duration is None:
        duration = track.t[-1] + track.dt if len(track) else 0
    out = time_periods(duration, s)
    if s.event_periods:
        from .periods import event_periods

        out += event_periods(s.event_periods, duration, track, apply_overrides(app, zone_overrides), s, events,
                             io_events)
    return out
