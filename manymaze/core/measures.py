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

import logging
import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields, replace
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np

from .apparatus import Apparatus, to_report_units
from .freezing import immobility_from_motion, thresholds as freeze_thresholds
from .geometry import point_segment_distance, segments_intersect
from .iomeasures import io_measures, io_track_measures
from .occupancy import occupancy, occupancy_map
from .pauses import drop_pauses, shift_events
from .series import count_rotations  # noqa: F401 (re-exported)
from .series import (drop_short_runs, ffill, initial_heading_frames, partial_rotation_events, rotation_events,
                     round_result as _r, runs, seg_moving_average, segments)
from .template_measures import TemplateData, template_measures
from .templates import apply_overrides
from .track import Track

if TYPE_CHECKING:
    from .project import Behaviour

_log = logging.getLogger(__name__)


@dataclass
class AnalysisSettings:
    speed_smoothing_s: float = 0.2  # positions are smoothed over this window before distance/speed
    mobility_threshold: float = 2.0  # speed (units/s) below which the animal is immobile
    min_immobile_s: float = 2.0  # immobile episodes shorter than this count as mobile
    immobility_mode: str = "speed"  # "speed" (above) | "motion": forced swim / tail suspension (freezing.py)
    fst_threshold_pct: float = 2.0  # motion mode: mobile (struggling) while the struggle index (% of body) reaches this
    min_fst_immobile_s: float = 1.0  # motion mode: immobile spells shorter than this count as mobile
    fst_three_state: bool = False  # forced swim: the struggle split into climbing and swimming
    fst_climbing_pct: float = 8.0  # … climbing while the struggle index reaches this
    freeze_on_pct: float = 2.0  # motion (% of body area changing) below which freezing starts
    freeze_off_pct: float = 3.0  # motion above which freezing ends (hysteresis)
    min_freeze_s: float = 1.0
    freeze_threshold_mode: str = "manual"  # "manual" (the two thresholds above) | "auto" (see core/freezing.py)
    freeze_sensitivity: float = 50.0  # automatic thresholds: 0–100, higher = smaller movements end freezing
    activity_threshold_pct: float = 5.0  # motion (% of body area changing) at or above which the animal is active
    min_inactive_s: float = 0.5  # inactive episodes shorter than this count as active
    # what "active" means: "pixel_change" (the motion above; experiments made before the option), "mobile_or_keys"
    # (ANY-maze: mobile, or doing a behaviour whose key counts as activity) or "keys" (ANY-maze without immobility
    # detection: only those behaviours) - see activity_frames
    activity_definition: str = "pixel_change"
    rearing: bool = False  # detect rears automatically from the animal's shape (see rearing_mask)
    rear_area_pct: float = 75.0  # rearing: body area below this % of the animal's usual area …
    rear_length_pct: float = 80.0  # … and (head and tail tracked) body length below this % of its usual length
    min_rear_s: float = 0.3  # rears shorter than this are ignored
    entry_min_duration_s: float = 0.0  # zone visits shorter than this are ignored
    count_initial_entry: bool = True  # an animal starting in a zone has entered it
    latency_if_never: str = "duration"  # "duration" (cap at test length) or "blank"
    # an average of nothing (ANY-maze's "Use zero as the result for undefined averages"): "blank" | "zero"; "" in
    # experiments made before the option: as mANY-MAZE gave them (see _undefined)
    undefined_averages: str = ""
    zone_body_part: str = "centre"  # body part used for zone occupancy: centre | head | tail
    thigmotaxis_distance: float = 0.0  # units from the arena wall; 0 = 25 % of arena half-width
    rotation_reset_deg: float = 90.0
    partial_rotation_deg: float = 90.0  # ANY-maze's partial rotation angle: turns of at least this without a rotation
    bin_length_s: float = 0.0  # time bins for segmented results; 0 = none
    custom_periods: list = field(default_factory=list)  # [[label, t0, t1], ...]
    novel_object: str = "Object B"  # for the novel object test
    social_side: str = "Left"  # three chamber: side holding the stranger animal
    exploration_facing_deg: float = 45.0
    orientation_deg: float = 30.0  # oriented towards a zone / point: the head direction within this angle of it
    # initial heading (ANY-maze's Heading error options): "time" - the position after the animal has been mobile for
    # heading_error_time_s - or "distance" - the first position more than heading_error_distance (units) away
    heading_error_by: str = "time"
    heading_error_time_s: float = 1.0
    heading_error_distance: float = 5.0
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
    barnes_strategy_method: str = "simple"  # Barnes maze search strategy: "simple" | "classic" | "unmc"
    barnes_target_region: int = 2  # holes either side of the escape hole in its target region (direct strategy)
    barnes_serial_visits: int = 3  # consecutive hole visits that start a serial strategy
    barnes_serial_skip: int = 1  # holes the animal may skip during a serial strategy
    barnes_centre_zone: str = "Centre"  # entering it breaks a serial strategy (and a direct one)
    io_baseline_s: float = 10.0  # analogue inputs: baseline = the first io_baseline_s seconds of each period
    io_deviation_sd: float = 2.0  # analogue inputs: a deviation is more than this many baseline SDs from it
    opad_contact: str = ""  # operant plantar assay: digital input of the paw contact with the thermal plate
    opad_lick: str = ""  # OPAD: digital input of the lickometer
    opad_temperature: str = ""  # OPAD: analogue input of the plate temperature
    opad_temperatures: str = ""  # OPAD: temperatures of interest, e.g. "10, 45"
    opad_tolerance: float = 1.0  # OPAD: the plate is at a temperature of interest within ± this
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

    @classmethod
    def for_new_experiment(cls) -> "AnalysisSettings":
        """The settings of a new experiment: ANY-maze's definitions where the defaults keep those of experiments made
        before an option existed (activity, undefined averages)."""
        return cls(activity_definition="mobile_or_keys", undefined_averages="blank")


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
    struggle: np.ndarray | None = None  # immobility_mode "motion": the struggle index (freezing.struggle_index)

    def slice(self, sl: slice, t0: float, duration: float) -> "Kinematics":
        """The frames sl, as a period starting at t0 that lasts `duration`."""
        arrays = {f.name: v[sl] for f in fields(self) if isinstance(v := getattr(self, f.name), np.ndarray)}
        return replace(self, t0=t0, duration=duration, **arrays)


def kinematics(track: Track, app: Apparatus, s: AnalysisSettings, t0: float | None = None,
               duration: float | None = None, breaks: np.ndarray | None = None,
               gaps: np.ndarray | None = None) -> Kinematics:
    """breaks: frames that follow a pause (no distance, speed or turn is counted into them, smoothing does not
    cross them, and the frame before lasts one dt). gaps: frames where the animal is seen again after being hidden
    (as ANY-maze, the distance it may have travelled while hidden is not counted: no distance, speed or turn into
    them and no smoothing across them; frame durations are unchanged)."""
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
    cuts = breaks
    if gaps is not None and gaps.any():
        cuts = gaps if breaks is None else (breaks | gaps)
    win = max(1, int(round(s.speed_smoothing_s / max(track.dt, 1e-6))))
    ux = seg_moving_average(x, win, cuts) * scale
    uy = seg_moving_average(y, win, cuts) * scale
    step = np.zeros(len(t))
    if len(t) > 1:
        d = np.hypot(np.diff(ux), np.diff(uy))
        step[1:] = np.nan_to_num(d)
        if cuts is not None:
            step[cuts] = 0.0
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
        on_pct, off_pct = freeze_thresholds(motion_pct, s)
        for i, mp in enumerate(motion_pct):
            if not math.isfinite(mp):
                freezing[i] = state
                continue
            if state:
                state = mp <= off_pct
            else:
                state = mp < on_pct
            freezing[i] = state
        freezing = drop_short_runs(freezing, t, dur, s.min_freeze_s, value=True)
    else:
        motion_pct = np.full(len(t), np.nan)
        freezing = np.zeros(len(t), bool)
    struggle = None
    if s.immobility_mode == "motion" and np.isfinite(motion_pct).any():
        # forced swim / tail suspension: mobile while the animal struggles, wherever its centre goes
        mobile, struggle = immobility_from_motion(motion_pct, t, dur, track.dt, s, breaks)
    heading = np.full(len(t), np.nan)
    if len(t) > 1:
        heading[1:] = np.degrees(np.arctan2(np.diff(uy), np.diff(ux)))
        heading[step < 1e-6] = np.nan
        if cuts is not None:
            heading[cuts] = np.nan
    if t0 is None:
        t0 = float(t[0]) if len(t) else 0.0
    if duration is None:
        duration = float(dur.sum())
    return Kinematics(t, dur, x, y, ux, uy, step, speed, mobile, freezing, motion_pct, heading, t0, duration,
                      scale, unit, breaks, struggle)


def _head_direction(tr: Track) -> np.ndarray:
    """Per frame: the direction (deg) from the centre to the head (NaN where the head is not tracked or is on the
    centre) - ANY-maze's orientation of the animal."""
    with np.errstate(invalid="ignore"):
        a = np.degrees(np.arctan2(tr.hy - tr.y, tr.hx - tr.x))
        a[~(np.hypot(tr.hy - tr.y, tr.hx - tr.x) > 1e-9)] = np.nan
    return a


def _orientation(P) -> tuple | None:
    """Whole test: (head x, head y, orientation in radians), forward filled, or None without a tracked head."""
    def make():
        tr = P.track
        a = _head_direction(tr)
        if not np.isfinite(a).any():
            return None
        return ffill(tr.hx), ffill(tr.hy), np.radians(ffill(a))
    return P.cached("orientation", make)


def _undefined(s: AnalysisSettings, before: float) -> float:
    """An average of nothing (no visit, bout, rear…): blank or 0 as ANY-maze's "Use zero as the result for undefined
    averages" (s.undefined_averages "blank" / "zero"); in experiments made before the option, `before` - the value
    mANY-MAZE gave (0 for the mean visit, investigation bout and rear in a zone, blank for the others)."""
    return {"zero": 0.0, "blank": math.nan}.get(s.undefined_averages, before)


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
    behaviours: list | None = None  # Project.behaviours (the keys that count as activity)
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

    def cuts(self) -> np.ndarray:
        """Whole test: frames that follow a pause or where the animal is seen again after being hidden. As in
        kinematics, no distance, turn or change of distance is counted into them and smoothing does not cross them."""
        def make():
            c = np.asarray(self.breaks, bool).copy()
            h = self.hid_any
            if len(c) > 1 and len(h) == len(c):
                c[1:] |= h[:-1] & ~h[1:]
            return c
        return self.cached("cuts", make)


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
             zone_overrides=None, pauses=None, duration=None, behaviours=None) -> _Prepared:
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
                         others, behaviours)
    memb, head_memb, hidden = occupancy(track, app, s)
    hid_any = np.zeros(n, bool)
    for hm in hidden.values():
        hid_any |= hm
    if hid_any.any():
        # no interpolation through hidden periods: the animal stays where it was last seen
        track = track.copy()
        for c in ("x", "y", "hx", "hy", "tx", "ty"):
            getattr(track, c)[hid_any] = np.nan
    gaps = np.zeros(n, bool)  # frames where the animal is seen again after being hidden
    gaps[1:] = hid_any[:-1] & ~hid_any[1:]
    k = kinematics(track, app, s, t0=float(track.t[0]), duration=duration, breaks=breaks, gaps=gaps)
    return _Prepared(track, clean, app, s, k, breaks, memb, head_memb, hidden, hid_any, events, io_events, others,
                     behaviours)


def analyse(track: Track, app: Apparatus, s: AnalysisSettings | None = None, events: list | None = None,
            behaviours: list[Behaviour] | None = None, t_range: tuple[float, float] | None = None,
            duration: float | None = None, other_tracks: list[Track] | None = None,
            zone_overrides: dict | None = None, io_events: list | None = None,
            result_variables: dict | None = None, pauses: list | None = None,
            io_devices: list | None = None, calculations: list | None = None) -> "OrderedDict[str, object]":
    """Compute all applicable measures for one animal's track.

    zone_overrides: per-test positions of moveable zones (Test.zone_overrides); io_events: Test.io_events
    (I/O measures; io_devices: Project.io_devices); result_variables: Test.result_variables; pauses: Test.pauses;
    calculations: Project.calculations (or calculations.plan() steps), added after the measures (see
    calculations.evaluate_test: those that need other tests are NaN here and worked out by Project.results).

    Times are *test time*: paused intervals are removed and everything after a pause moves back by its length
    (track, scored events, I/O events and other animals' tracks alike); t_range is in test time. Per-frame states
    (mobility, freezing, zone visits and their minimum durations / hysteresis) are computed on the whole test and
    then restricted to t_range: times count every frame inside the period, episode counts and latencies count the
    episodes that start inside it.
    """
    s = s or AnalysisSettings()
    P = _prepare(track, app, s, events, io_events, other_tracks, zone_overrides, pauses, duration, behaviours)
    return _results(P, t_range, behaviours, result_variables, io_devices, calculations)


def _results(P: _Prepared, t_range, behaviours=None, result_variables=None, io_devices=None, calculations=None):
    """The measures of the whole test (t_range None) or of a period, then the calculations; filtered last
    (AnalysisSettings.measure_filter keeps the calculations, which may use any measure)."""
    res = _measures(P, t_range, behaviours, result_variables, io_devices)
    steps = None
    if calculations:
        from .calculations import evaluate_test, plan

        steps = plan(calculations, periods=P.s.event_periods)  # (plan() steps given are used as they are)
        P.cache["calc_args"] = (behaviours, result_variables, io_devices, steps)
        # the whole test's results, filled as they are worked out: time periods defined by calculations use them
        view = None
        if t_range is None:
            view = P.cache["calc_view"] = {}

        def period(spec):
            return _calc_period(P, spec, behaviours, result_variables, io_devices)

        res.update(evaluate_test(steps, res, period, view=view))
    if P.s.measure_filter:
        keep = set(P.s.measure_filter) | {"Test duration (s)", "Warnings"}
        if steps:
            keep |= {st.calc.column for st in steps}
        res = OrderedDict((key, v) for key, v in res.items() if key in keep)
    return res


def _calc_values(P: _Prepared) -> dict | None:
    """The whole test's results with its calculations (for the time periods they define): those being worked out,
    else worked out now (analyse() of a period only, analyse_period); None without calculations."""
    if "calc_view" not in P.cache and "calc_args" in P.cache:
        _results(P, None, *P.cache["calc_args"])
    return P.cache.get("calc_view")


def _periods(P: _Prepared) -> list:
    """The time bins / custom periods, then the event-anchored periods of a prepared test (periods.Period, in test
    time), their calculation anchors from the whole test's results."""
    from .periods import Period, calculation_columns, calculation_value, resolve_periods

    tr = P.clean
    dur = tr.t[-1] + tr.dt if len(tr) else 0
    defs = P.s.event_periods or []
    cols = sorted(set().union(*(calculation_columns(p) for p in defs if isinstance(p, dict))))
    calc = _calc_values(P) if cols else None
    key = ("periods",) + tuple(calculation_value(calc, c) for c in cols)

    def make():
        out = [Period(label, a, b) for label, a, b in time_periods(dur, P.s)]
        if defs:
            out += resolve_periods(defs, dur, tr, P.app, P.s, P.events, P.io_events, memb=P.memb, calc=calc)
        return out

    return P.cached(key, make)


def add_warning(res: dict, text: str) -> dict:
    """Add a note to the Warnings column of a results row."""
    if text:
        res["Warnings"] = "; ".join(x for x in (res.get("Warnings"), text) if x)
    return res


def _calc_period(P: _Prepared, spec, behaviours, result_variables, io_devices) -> dict | None:
    """The measures of part of the test for a calculation's result_for_period(): spec (from_s, to_s) in test time
    or the name of a time period (time bins, custom and event-anchored periods); None if the test ended before the
    period starts or no period has that name. Cached on the prepared test."""
    if isinstance(spec, str):
        spec = next(((p.t0, p.t1) for p in _periods(P) if p.label == spec), None)
        if spec is None:
            return None
    a, b = float(spec[0]), float(spec[1])
    t = P.track.t
    if not len(t) or a >= float(t[-1]) + P.track.dt:
        return None  # ANY-maze: undefined when the test ended before the period
    return P.cached(("calc_period", a, b), lambda: _measures(P, (a, b), behaviours, result_variables, io_devices))


def _measures(P: _Prepared, t_range, behaviours=None, result_variables=None, io_devices=None):
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
        """(head x, head y, orientation in radians), forward filled, if the head is tracked. As ANY-maze, the
        orientation is the direction from the centre to the head."""
        o = _orientation(self.P)
        if o is None:
            return None
        return o[0][self.sl], o[1][self.sl], o[2][self.sl]

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
        res["Time not hidden (s)"] = _r(dur[~p.hid_any].sum())


def _locomotion(res, p: _Period):
    P, k, K, T, dur, u = p.P, p.k, p.P.k, p.T, p.dur, p.P.app.unit
    total = float(k.step.sum())
    res[f"Total distance ({u})"] = _r(total, 2)
    res[f"Mean speed ({u}/s)"] = _r(total / T if T > 0 else math.nan)
    if p.hidden:
        t_vis = float(dur[~p.hid_any].sum())
        res[f"Mean speed when not hidden ({u}/s)"] = _r(k.step[~p.hid_any].sum() / t_vis if t_vis > 0 else math.nan)
    res[f"Max speed ({u}/s)"] = _r(np.nanmax(p.sp) if np.isfinite(p.sp).any() else math.nan)
    t_mob = float(dur[k.mobile].sum())
    # immobile: not mobile at a known position (an animal never detected is neither mobile nor immobile)
    seen_full = P.cached("seen", lambda: np.isfinite(K.x) & np.isfinite(K.y))
    t_imm = float(dur[~k.mobile & seen_full[p.sl]].sum())
    res["Time mobile (s)"] = _r(t_mob)
    res["Time immobile (s)"] = _r(t_imm)
    imm = p.eps(~K.mobile & seen_full)
    mob = p.eps(K.mobile)
    res["Immobile episodes"] = len(imm)
    res["Latency to first immobility (s)"] = _r(p.lat(imm))
    res[f"Mean speed while mobile ({u}/s)"] = _r(k.step[k.mobile].sum() / t_mob if t_mob > 0 else math.nan)
    res["Mobile episodes"] = len(mob)
    res["Mean mobile episode (s)"] = _r(p.ep_time(mob) / len(mob) if mob else 0.0)
    imm_time = t_imm if p.whole else p.ep_time(imm)
    res["Mean immobile episode (s)"] = _r(imm_time / len(imm) if imm else 0.0)
    res["Longest immobile episode (s)"] = _r(max((dur[a:b].sum() for a, b in imm), default=0.0))
    res["Shortest immobile episode (s)"] = _r(min((dur[a:b].sum() for a, b in imm), default=0.0))
    res["Longest mobile episode (s)"] = _r(max((dur[a:b].sum() for a, b in mob), default=0.0))
    res["Shortest mobile episode (s)"] = _r(min((dur[a:b].sum() for a, b in mob), default=0.0))
    res["Latency to first mobile episode (s)"] = _r(p.lat(mob))
    # as ANY-maze: blank when there is no episode (not the test duration, unlike the first-episode latencies)
    res["Latency to last mobile episode (s)"] = _r(float(p.t[mob[-1][0]] - p.t0) if mob else math.nan)
    res["Latency to last immobile episode (s)"] = _r(float(p.t[imm[-1][0]] - p.t0) if imm else math.nan)
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
    """Path efficiency, turning and rotations (no turn or rotation across a pause or a reappearance; computed on the
    whole test, so that the periods add up - see _turns)."""
    k, u = p.k, p.P.app.unit
    ok = np.isfinite(k.ux)
    if ok.sum() >= 2:
        j0, j1 = np.flatnonzero(ok)[[0, -1]]
        straight = math.hypot(k.ux[j1] - k.ux[j0], k.uy[j1] - k.uy[j0])
        # as ANY-maze: undefined when the animal was hidden (the distance it travelled meanwhile is not known)
        hid = bool(p.hid_any.any())
        res["Path efficiency"] = _r(straight / total if total > 0 and not hid else math.nan)
        res["Path tortuosity"] = _r(total / straight if straight > 0 and not hid else math.nan)
    T = _turns(p.P)
    dturn = T["turn"][p.sl]
    dturn = dturn[np.isfinite(dturn)]
    abs_turn = float(dturn.sum())
    res["Absolute turn angle (deg)"] = _r(abs_turn, 1)
    res[f"Meander (deg/{u})"] = _r(abs_turn / total if total > 0 else math.nan)
    res["Mean turn angle (deg)"] = _r(dturn.mean() if len(dturn) else math.nan, 2)
    # as ANY-maze (2.37): the absolute turn angle / the test duration (or the period's)
    res["Angular velocity (deg/s)"] = _r(abs_turn / p.T if p.T > 0 else math.nan, 2)

    def rotations(key):
        at, sign = T[key]
        sign = sign[(at >= p.i0) & (at < p.i1)]  # a rotation belongs to the period in which it is completed
        return int((sign > 0).sum()), int((sign < 0).sum())

    cw, acw = rotations("body_rot" if T["body"] else "path_rot")
    res["Rotations clockwise"], res["Rotations anticlockwise"] = cw, acw
    res["Total rotations"] = cw + acw
    # ANY-maze's partial rotations (2.34-2.36): turns of at least the partial rotation angle that completed no
    # rotation, in the period in which the turn stopped
    cw, acw = rotations("body_partial" if T["body"] else "path_partial")
    res["Partial rotations"] = cw + acw
    res["Partial rotations clockwise"], res["Partial rotations anticlockwise"] = cw, acw
    if T["body"] and np.isfinite(k.heading).sum() > 2:
        # rotations of the direction of travel (the rotations above follow the body / head orientation)
        res["Path rotations clockwise"], res["Path rotations anticlockwise"] = rotations("path_rot")


def turn_series(heading: np.ndarray, speed: np.ndarray, mobility_threshold: float,
                cuts: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per frame: the absolute change (deg) of the direction of travel since the previous frame in which the animal
    moved (speed above the mobility threshold, direction known), in the frames where it moves (NaN elsewhere), and
    that previous frame (-1: none). cuts: frames that start a new stretch (after a pause or a reappearance) - no
    turn is counted across them. The absolute turn angle is the sum of the turns."""
    n = len(heading)
    seg_id = np.cumsum(cuts) if cuts is not None else np.zeros(n, int)
    mi = np.flatnonzero(np.isfinite(heading) & (speed > max(mobility_threshold, 1e-9)))
    turn = np.full(n, np.nan)
    prev = np.full(n, -1)
    if len(mi) > 1:
        d = np.abs((np.diff(heading[mi]) + 180) % 360 - 180)
        same = seg_id[mi[1:]] == seg_id[mi[:-1]]
        turn[mi[1:][same]] = d[same]
        prev[mi[1:][same]] = mi[:-1][same]
    return turn, prev


def _turns(P: _Prepared) -> dict:
    """Whole test, so that periods add up: the absolute change of the direction of travel into each frame where the
    animal moves (NaN elsewhere) and the previous moving frame it is measured from (-1: none), and the rotations
    (frame completing each one, ±1) of the body and of the direction of travel. Nothing is counted across a pause or
    a reappearance after being hidden."""
    def make():
        K, s = P.k, P.s
        n = len(K.t)
        cuts = P.cuts()
        h = K.heading
        turn, prev = turn_series(h, K.speed, s.mobility_threshold, cuts)

        def rot(angle, events=rotation_events, **kw):
            at, sign = [], []
            for a, b in segments(n, cuts):
                ia, sa = events(angle[a:b], s.rotation_reset_deg, **kw)
                at.append(ia + a)
                sign.append(sa)
            return np.concatenate(at), np.concatenate(sign)

        def partial(angle):
            return rot(angle, partial_rotation_events, partial_deg=float(s.partial_rotation_deg or 0.0))
        # body rotations follow the body (as ANY-maze: the vector from the centre to the head), else the tracked
        # body angle (e.g. an imported orientation); only without either do they follow the direction of travel
        angle = _body_angle(P.track)
        return {"turn": turn, "prev": prev, "body": angle is not None, "path_rot": rot(h), "path_partial": partial(h),
                "body_rot": rot(angle) if angle is not None else None,
                "body_partial": partial(angle) if angle is not None else None}
    return P.cached("turns", make)


def _body_angle(tr: Track) -> np.ndarray | None:
    """The body orientation (deg) used for body rotations: the direction from the centre to the head (ANY-maze's
    definition), in the frames where the head is tracked, else the tracked body angle, or None when neither is
    tracked (in more than 2 frames)."""
    axis = _head_direction(tr)
    if np.isfinite(axis).sum() > 2:
        return axis
    return tr.angle if np.isfinite(tr.angle).sum() > 2 else None


def _arena_position(res, p: _Period):
    """Average position, thigmotaxis and position in the arena."""
    s, k, app, u = p.P.s, p.k, p.P.app, p.P.app.unit
    try:
        arena = app.arena_or_bounds()
    except ValueError:
        return
    # average position (as ANY-maze): each position weighted by the time the animal stayed there (it stays where it
    # was last seen while hidden or lost), as a % of the apparatus width / height from its left / top side
    x0, y0, x1, y1 = arena.bounds()
    ok = np.isfinite(k.x) & np.isfinite(k.y)
    w = float(p.dur[ok].sum())
    for axis, v, a, b in (("X", k.x, x0, x1), ("Y", k.y, y0, y1)):
        mean = float((v[ok] * p.dur[ok]).sum()) / w if w > 0 else math.nan
        res[f"Average {axis} position (%)"] = _r(100 * (mean - a) / (b - a) if b > a else math.nan, 2)
    dwall = arena.distance_to_edge(k.x, k.y) * k.scale
    thr = s.thigmotaxis_distance
    if not thr or thr <= 0:
        x0, y0, x1, y1 = arena.bounds()
        thr = 0.25 * min(x1 - x0, y1 - y0) / 2 * k.scale
    inside_arena = arena.contains(k.x, k.y)
    din = np.where(inside_arena, dwall, np.nan)
    res[f"Mean distance from wall ({u})"] = _r(np.nanmean(din) if np.isfinite(din).any() else math.nan, 2)
    res["Thigmotaxis (%)"] = p.pct((dwall <= thr) & inside_arena)
    # a position outside the arena (an animal never detected is not outside it)
    res["Time outside arena (s)"] = _r(p.dur[~inside_arena & np.isfinite(k.x) & np.isfinite(k.y)].sum())
    acx, acy = arena.centroid()
    dc = np.hypot(k.x - acx, k.y - acy) * k.scale
    res[f"Mean distance from centre ({u})"] = _r(np.nanmean(dc) if np.isfinite(dc).any() else math.nan, 2)
    res[f"Max distance from centre ({u})"] = _r(np.nanmax(dc) if np.isfinite(dc).any() else math.nan, 2)
    if s.arena_quadrants:
        east, south = k.x >= acx, k.y >= acy
        for q, m in (("NE", east & ~south), ("SE", east & south), ("SW", ~east & south), ("NW", ~east & ~south)):
            res[f"Arena quadrant {q}: time (%)"] = p.pct(m)


def _zones(res, p: _Period):
    P, k, K, app, t, dur, T, u = p.P, p.k, p.P.k, p.P.app, p.t, p.dur, p.T, p.P.app.unit
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
    for zn in p.memb:
        fm = P.visits_mask(zn, P.memb[zn])  # whole test, short visits removed
        vm = fm[p.sl]  # time in the zone: every visit, including the initial one and one carried into the period
        visits = p.eps(fm, entries=True)  # entries
        tz = float(dur[vm].sum())
        res[f"{zn}: time (s)"] = _r(tz)
        res[f"{zn}: time (%)"] = _r(100 * tz / T if T > 0 else math.nan, 2)
        res[f"{zn}: entries"] = len(visits)
        res[f"{zn}: latency to first entry (s)"] = _r(p.lat(visits))
        # as ANY-maze, the step between two positions counts in the zone the animal is leaving (it was in at the
        # first of them): the step into the zone is not counted, the step out of it is
        dz = float(k.step[np.r_[False, fm[:-1]][p.sl]].sum())
        res[f"{zn}: distance ({u})"] = _r(dz, 2)
        res[f"{zn}: mean speed ({u}/s)"] = _r(dz / tz if tz > 0 else _undefined(P.s, math.nan))
        # time in the zone / entries
        res[f"{zn}: mean visit (s)"] = _r(tz / len(visits) if visits else _undefined(P.s, 0.0))
        res[f"{zn}: time immobile (s)"] = _r(dur[vm & ~k.mobile].sum())
        if has_motion:
            res[f"{zn}: time freezing (s)"] = _r(dur[vm & k.freezing].sum())
        if P.head_memb is not None and zn in P.head_memb:
            hfm = P.visits_mask(("head", zn), P.head_memb[zn])
            # as ANY-maze, a head entry is the head going from outside the zone to inside it: a head that starts
            # the test in the zone has not entered it
            hv = [e for e in p.eps(hfm, entries=True) if e[0] + p.i0 > 0]
            res[f"{zn}: head entries"] = len(hv)
            res[f"{zn}: head time (s)"] = _r(dur[hfm[p.sl]].sum())
            res[f"{zn}: latency to head entry (s)"] = _r(p.lat(hv))
        res[f"{zn}: latency to second entry (s)"] = _r(float(t[visits[1][0]] - p.t0) if len(visits) > 1 else p.never)
        # an exit on a bin's first frame is in it; as entries, none across a pause (out of the zone after it)
        exits = [b for a, b in runs(fm) if p.i0 <= b < p.i1 and b > 0 and not P.breaks[b]]
        res[f"{zn}: time of last exit (s)"] = _r(float(K.t[exits[-1] - 1] + K.dur[exits[-1] - 1] - p.t0)
                                                if exits else math.nan)
        res[f"{zn}: exits"] = len(exits)
        res[f"{zn}: latency to first exit (s)"] = _r(float(K.t[exits[0] - 1] + K.dur[exits[0] - 1] - p.t0)
                                                    if exits else p.never)
        res[f"{zn}: latency to last entry (s)"] = _r(float(t[visits[-1][0]] - p.t0) if visits else p.never)
        if zn in zone_names:
            res[f"{zn}: was first zone entered"] = "Yes" if zn == first_zone else "No"
        # every visit in the period, clipped to it (one carried into the period and the initial one included)
        vdur = [float(dur[a:b].sum()) for a, b in runs(vm)]
        res[f"{zn}: longest visit (s)"] = _r(max(vdur, default=0.0))
        res[f"{zn}: shortest visit (s)"] = _r(min(vdur, default=0.0))
        res[f"{zn}: entries (/min)"] = _r(len(visits) / (T / 60) if T > 0 else math.nan)
        res[f"{zn}: time mobile (s)"] = _r(dur[vm & k.mobile].sum())
        res[f"{zn}: immobile episodes"] = len(p.eps(fm & ~K.mobile))
        if has_motion:
            # as ANY-maze: the times the animal starts to freeze while in the zone
            res[f"{zn}: freezing episodes"] = sum(1 for a, _ in p.eps(K.freezing) if vm[a])
        act_full = activity_frames(P)
        if act_full is not None:
            # activity in the zone (see _activity); an inactive episode belongs to the zone it starts in
            t_act = float(dur[vm & act_full[p.sl]].sum())
            res[f"{zn}: time active (s)"] = _r(t_act)
            res[f"{zn}: time inactive (s)"] = _r(tz - t_act)
            res[f"{zn}: inactive episodes"] = len(p.eps(fm & ~act_full))
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
            # undefined when the route passed through a hidden zone (the distance travelled there is not known)
            res[f"{zn}: path efficiency to first entry"] = _r(straight / d_first if d_first > 0
                                                              and not p.hid_any[:j + 1].any() else math.nan)
        else:  # as ANY-maze: undefined if the animal never entered the zone
            res[f"{zn}: distance before first entry ({u})"] = math.nan
            res[f"{zn}: path efficiency to first entry"] = math.nan
        zobj = app.zone(zn)
        if zobj is not None and zn not in grid_cells:
            dz_full = P.cached(("dist", zn), lambda z=zobj, m=P.memb[zn]: np.where(m, 0.0, _edge(P, z)))
            dzs = dz_full[p.sl]
            # as ANY-maze: the distance while outside (0 inside) weighted by the time spent at it, over the period
            fin = np.isfinite(dzs)
            t_fin = float(dur[fin].sum())
            res[f"{zn}: mean distance from zone ({u})"] = _r(float((dzs[fin] * dur[fin]).sum()) / t_fin if t_fin > 0
                                                             else math.nan, 2)
            out_d = dzs[~vm & np.isfinite(dzs)]
            fin = np.flatnonzero(np.isfinite(dzs))
            res[f"{zn}: initial distance from zone ({u})"] = _r(dzs[fin[0]] if len(fin) else math.nan, 2)
            # as ANY-maze: 0 when the animal never left the zone (max) / when it was in the zone (min)
            res[f"{zn}: max distance from zone ({u})"] = _r(out_d.max() if len(out_d) else 0.0, 2)
            res[f"{zn}: min distance from zone ({u})"] = _r(0.0 if vm.any() or not len(out_d) else out_d.min(), 2)
            res[f"{zn}: cumulative distance from zone ({u}·s)"] = _r(np.nansum(dzs * dur), 1)
        if p.facing is not None and zobj is not None:
            # ANY-maze's time oriented towards the zone: outside it, the direction from the centre to the head
            # within the orientation angle of the direction from the head to some point of the zone's border
            towards = P.cached(("facing", zn), lambda z=zobj: _oriented_to_shape(P, z.shape))[p.sl]
            res[f"{zn}: time facing (s)"] = _r(dur[towards & ~vm & ~p.hid_any].sum())
        if zobj is not None and zobj.whishaw_width_cm > 0:
            _zone_whishaw(res, p, zobj)
        _zone_more(res, p, zn, fm, vm, visits, vdur)
    for hz in p.hidden:
        res[f"{hz}: time hidden (s)"] = _r(dur[p.hidden[hz]].sum())
    if app.zones:
        tr_at = P.cached("transitions", lambda: _transitions(P))
        res["Zone transitions"] = int(np.count_nonzero((tr_at >= p.i0) & (tr_at < p.i1)))


def _zone_whishaw(res, p: _Period, z):
    """Whishaw's corridor of a zone (as ANY-maze): a band z.whishaw_width_cm wide centred on the line from the
    animal's first position in the test to the zone centre; the time spent in it in the period and the distance
    travelled in it (as the distance in a zone: the step into the corridor is not counted, the step out of it is)."""
    P, zn, u = p.P, z.name, p.P.app.unit

    def make():
        K = P.k
        ok = np.flatnonzero(np.isfinite(K.x))
        if not len(ok):
            return np.zeros(len(K.t), bool)
        zx, zy = z.shape.centroid()
        d = point_segment_distance(K.x, K.y, K.x[ok[0]], K.y[ok[0]], zx, zy) * K.scale
        with np.errstate(invalid="ignore"):
            return d <= z.whishaw_width_cm / 2
    inside = P.cached(("whishaw", zn), make)
    prev = np.r_[False, inside[:-1]][p.sl]  # as the distance in a zone: the step out of it counts, the step in not
    res[f"{zn}: time in Whishaw's corridor (s)"] = _r(p.dur[inside[p.sl]].sum())
    res[f"{zn}: distance in Whishaw's corridor ({u})"] = _r(p.k.step[prev].sum(), 2)


def _oriented_to_shape(P: _Prepared, shape) -> np.ndarray:
    """Whole test: is the animal oriented towards the shape - the direction from its centre to its head within
    s.orientation_deg of the direction from the head to at least one point of the shape's border?"""
    hx, hy, ang = _orientation(P)
    poly = np.asarray(shape.polygon(72), float)
    # points along the whole border (a polygon gives only its corners): ~64 points spread by edge length
    nxt = np.roll(poly, -1, axis=0)
    seg = np.hypot(*(nxt - poly).T)
    per = np.maximum(1, np.round(64 * seg / max(seg.sum(), 1e-9)).astype(int))
    poly = np.vstack([a + np.linspace(0, 1, k, endpoint=False)[:, None] * (b - a)
                      for a, b, k in zip(poly, nxt, per)])
    out = np.zeros(len(hx), bool)
    tol = math.radians(P.s.orientation_deg)
    for i in range(0, len(hx), 4096):  # bound the (frames × border points) temporaries
        sl = slice(i, i + 4096)
        with np.errstate(invalid="ignore"):
            to = np.arctan2(poly[None, :, 1] - hy[sl, None], poly[None, :, 0] - hx[sl, None])
            d = np.abs((to - ang[sl, None] + np.pi) % (2 * np.pi) - np.pi)
            out[sl] = np.nanmin(np.where(np.isfinite(d), d, np.inf), axis=1) <= tol
    return out


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


class _GroupShape:
    """The outline of a zone group (union of its zones minus the excluded zones), for the zone measures that need
    a shape (distance to the border, heading towards the zone, CIPL). Its border is made of the parts of its zones'
    edges with the group on one side only (the edge shared by two touching zones of the group is not a border);
    its centre is the centre of its area."""

    def __init__(self, app: Apparatus, g):
        self.members = [z.shape for zn in g.zones if (z := app.zone(zn)) is not None]
        self.excluded = [z.shape for zn in g.exclude if (z := app.zone(zn)) is not None]
        edges, mid, side = [], [], []  # (p0, p1, pieces); piece midpoints and normals (1 px)
        for sh in self.members + self.excluded:
            poly = np.asarray(sh.polygon(180), float)
            for p0, p1 in zip(poly, np.roll(poly, -1, axis=0)):
                L = math.hypot(*(p1 - p0))
                if L < 1e-9:
                    continue
                n = max(1, int(math.ceil(L / 2.0)))  # pieces of at most 2 px, tested on both sides
                f = ((np.arange(n) + 0.5) / n)[:, None]
                edges.append((p0, p1, n))
                mid.append(p0 + f * (p1 - p0))
                side.append(np.tile([-(p1[1] - p0[1]) / L, (p1[0] - p0[0]) / L], (n, 1)))
        a, b = [], []
        if edges:
            mid, side = np.vstack(mid), np.vstack(side)
            keep = self.contains(*(mid + side).T) != self.contains(*(mid - side).T)
            i = 0
            for p0, p1, n in edges:
                for r0, r1 in runs(keep[i:i + n]):  # each run of border pieces is one straight segment
                    a.append(p0 + r0 / n * (p1 - p0))
                    b.append(p0 + r1 / n * (p1 - p0))
                i += n
        self.a, self.b = np.asarray(a, float).reshape(-1, 2), np.asarray(b, float).reshape(-1, 2)

    def contains(self, x, y) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        inside = np.zeros(x.shape, bool)
        for sh in self.members:
            inside |= sh.contains(x, y)
        for sh in self.excluded:
            inside &= ~sh.contains(x, y)
        return inside

    def distance_to_edge(self, x, y) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
        fx, fy = x.ravel(), y.ravel()
        if not len(self.a):
            return np.full(x.shape, np.nan)
        out = np.empty(fx.size)
        for i in range(0, fx.size, 2048):  # bound the (points × segments) temporaries
            out[i:i + 2048] = point_segment_distance(fx[i:i + 2048, None], fy[i:i + 2048, None], self.a[:, 0],
                                                     self.a[:, 1], self.b[:, 0], self.b[:, 1]).min(axis=1)
        return out.reshape(x.shape)

    def centroid(self) -> tuple[float, float]:
        if not self.members:
            return math.nan, math.nan
        bx = np.array([sh.bounds() for sh in self.members])
        gx, gy = np.meshgrid(np.linspace(bx[:, 0].min(), bx[:, 2].max(), 200),
                             np.linspace(bx[:, 1].min(), bx[:, 3].max(), 200))
        inside = self.contains(gx, gy)
        return (float(gx[inside].mean()), float(gy[inside].mean())) if inside.any() else self.members[0].centroid()


@dataclass
class _GroupZone:
    """A zone group as a zone (a name and a shape) for _zone_border, _zone_heading and _zone_cipl."""

    name: str
    shape: _GroupShape


def _group_zone(P: _Prepared, name: str) -> _GroupZone | None:
    g = P.app.group(name)
    return P.cached(("group_zone", name), lambda: _GroupZone(name, _GroupShape(P.app, g))) if g is not None else None


def _head(P: _Prepared):
    """Whole test: (head x, head y in px forward filled, distance the head travels into each frame in units), or
    None without a tracked head. The head path is smoothed like the centre's; nothing is counted across a pause or
    a reappearance."""
    def make():
        tr, K = P.track, P.k
        if not tr.has_head():
            return None
        hx, hy = ffill(tr.hx), ffill(tr.hy)
        win = max(1, int(round(P.s.speed_smoothing_s / max(tr.dt, 1e-6))))
        cuts = P.cuts()
        ux = seg_moving_average(hx, win, cuts) * K.scale
        uy = seg_moving_average(hy, win, cuts) * K.scale
        step = np.zeros(len(hx))
        if len(hx) > 1:
            step[1:] = np.nan_to_num(np.hypot(np.diff(ux), np.diff(uy)))
            step[cuts] = 0.0
        return hx, hy, step
    return P.cached("head", make)


def _investigation(P: _Prepared, z) -> np.ndarray:
    """Whole test: frames in which the animal investigates zone z - as ANY-maze, its head is outside the zone but
    within the zone's investigation distance of its border and (when the orientation is tracked) pointing at it
    (the direction from the centre to the head within the exploration facing angle of the direction to the zone
    centre); never while hidden. Bouts shorter than the minimum entry duration are ignored."""
    def make():
        K, s = P.k, P.s
        H = _head(P)
        hx, hy = (H[0], H[1]) if H is not None else (K.x, K.y)
        inside = z.shape.contains(hx, hy)
        with np.errstate(invalid="ignore"):
            inv = ~inside & ((z.shape.distance_to_edge(hx, hy) <= z.investigation_distance_cm / P.app.scale)
                             & np.isfinite(hx))
            o = _orientation(P)
            if o is not None:
                zx, zy = z.shape.centroid()
                diff = _angle_diff(np.arctan2(zy - hy, zx - hx), o[2])
                inv &= ~np.isfinite(diff) | (diff <= s.exploration_facing_deg)
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
    geo = z if z is not None else _group_zone(p.P, zn)  # a zone group: the outline of its zones
    if geo is not None:
        _zone_border(res, p, geo, vm)
        _zone_heading(res, p, geo, vm)
    _zone_turning(res, p, zn, vm, fm)
    if geo is not None:
        _zone_cipl(res, p, geo, visits)
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
    # longest / shortest: every bout in the period, clipped to it (as ANY-maze)
    bclip = [float(dur[a:b].sum()) for a, b in runs(inv)]
    res[f"{zn}: longest investigation bout (s)"] = _r(max(bclip, default=0.0))
    res[f"{zn}: shortest investigation bout (s)"] = _r(min(bclip, default=0.0))
    # time investigating / bouts
    res[f"{zn}: mean investigation bout (s)"] = _r(ti / len(bd) if bd else _undefined(P.s, 0.0))
    res[f"{zn}: investigation durations (s)"] = _list_text(bd)
    di = float(k.step[inv].sum())
    res[f"{zn}: distance while investigating ({u})"] = _r(di, 2)
    if bouts:
        j = bouts[0][0]
        d_first = float(k.step[1:j + 1].sum()) if j > 0 else 0.0
    else:  # as ANY-maze: undefined if the animal never investigated the zone
        d_first = math.nan
    res[f"{zn}: distance before first investigation ({u})"] = _r(d_first, 2)
    res[f"{zn}: mean speed while investigating ({u}/s)"] = _r(di / ti if ti > 0 else _undefined(P.s, math.nan))
    res[f"{zn}: time mobile while investigating (s)"] = _r(dur[inv & k.mobile].sum())
    res[f"{zn}: time immobile while investigating (s)"] = _r(dur[inv & ~k.mobile].sum())
    res[f"{zn}: immobile episodes while investigating"] = len(p.eps(inv_full & ~K.mobile))
    if np.isfinite(k.motion_pct).any():
        res[f"{zn}: time freezing while investigating (s)"] = _r(dur[inv & k.freezing].sum())
        # as ANY-maze: the times the animal starts to freeze while investigating the zone
        res[f"{zn}: freezing episodes while investigating"] = sum(1 for a, _ in p.eps(K.freezing) if inv[a])


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
    # as head entries, no head exit across a pause
    ends = P.cached(("head_exits", zn), lambda: np.array([b for _, b in runs(hfm)
                                                          if b >= len(P.breaks) or not P.breaks[b]], int))
    exits = ends[(ends >= p.i0) & (ends < p.i1) & (ends > 0)]
    res[f"{zn}: latency to first head exit (s)"] = _r(float(K.t[exits[0] - 1] + K.dur[exits[0] - 1] - p.t0)
                                                     if len(exits) else p.never)
    res[f"{zn}: head distance ({u})"] = _r(H[2][p.sl][hin].sum(), 2)
    cin = P.cached(("occ", "centre"), lambda: occupancy(P.track, P.app, P.s, part="centre")[0])
    if z is None:
        if zn in cin:
            res[f"{zn}: time head in zone with centre outside (s)"] = _r(dur[hin & ~cin[zn][p.sl]].sum())
        return
    hx, hy = H[0], H[1]
    h_in = P.cached(("hin", zn), lambda: z.shape.contains(hx, hy))[p.sl]
    # as ANY-maze, without the entry criteria: the head position in the zone and the centre position outside it
    c_in = P.cached(("cin", zn), lambda: z.shape.contains(P.k.x, P.k.y))[p.sl]
    res[f"{zn}: time head in zone with centre outside (s)"] = _r(dur[h_in & ~c_in & ~p.hid_any].sum())
    h_edge = P.cached(("hedge", zn), lambda: z.shape.distance_to_edge(hx, hy) * K.scale)[p.sl]
    seen = ~p.hid_any & np.isfinite(h_edge)
    # as ANY-maze: the head's distance while outside (0 inside) weighted by time, over the period; the max is 0 if
    # the head never left the zone and the min 0 if it entered it
    t_seen = float(dur[seen].sum())
    res[f"{zn}: mean head distance from zone ({u})"] = _r(float((np.where(h_in, 0.0, h_edge) * dur)[seen].sum())
                                                          / t_seen if t_seen > 0 else math.nan, 2)
    _, mx, mn = _stats3(h_edge[~h_in & seen])
    res[f"{zn}: max head distance from zone ({u})"] = _r(mx if math.isfinite(mx) else (0.0 if seen.any()
                                                                                        else math.nan), 2)
    res[f"{zn}: min head distance from zone ({u})"] = _r(0.0 if (h_in & seen).any() else mn, 2)
    mean, mx, mn = _stats3(h_edge[h_in & seen])
    res[f"{zn}: mean head distance to border when inside ({u})"] = _r(mean if math.isfinite(mean)
                                                                      else _undefined(P.s, math.nan), 2)
    res[f"{zn}: max head distance to border when inside ({u})"] = _r(mx, 2)
    res[f"{zn}: min head distance to border when inside ({u})"] = _r(mn, 2)


def _zone_border(res, p: _Period, z, vm: np.ndarray):
    """Distance of the centre from the zone border while the animal is in the zone (and its centre inside it)."""
    P, zn, u = p.P, z.name, p.P.app.unit
    c_in = P.cached(("cin", zn), lambda: z.shape.contains(P.k.x, P.k.y))[p.sl]
    sel = vm & c_in & ~p.hid_any
    e = _edge(P, z)[p.sl]
    _, mx, mn = _stats3(e[sel])
    t_in = float(p.dur[sel & np.isfinite(e)].sum())
    res[f"{zn}: mean distance to border when inside ({u})"] = _r(float((e * p.dur)[sel & np.isfinite(e)].sum())
                                                                 / t_in if t_in > 0 else _undefined(P.s, math.nan), 2)
    res[f"{zn}: max distance to border when inside ({u})"] = _r(mx, 2)
    # as ANY-maze: 0 once the animal has left the zone (in the period)
    left = bool((vm[:-1] & ~vm[1:]).any())
    res[f"{zn}: min distance to border when inside ({u})"] = _r(0.0 if left and sel.any() else mn, 2)


def _zone_heading_arrays(P: _Prepared, z) -> dict:
    """Whole test: change of the (smoothed) distance from the zone into each frame (0 across a pause or a
    reappearance), and the
    signed heading error (direction of travel minus direction to the zone centre, -180..180 deg; positive =
    clockwise of the zone on screen)."""
    K = P.k
    with np.errstate(invalid="ignore"):
        px, py = K.ux / K.scale, K.uy / K.scale
        d = np.where(z.shape.contains(px, py), 0.0, z.shape.distance_to_edge(px, py) * K.scale)
        dd = np.zeros(len(d))
        if len(d) > 1:
            dd[1:] = np.nan_to_num(np.diff(d))
            dd[P.cuts()] = 0.0
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
        # as ANY-maze: within ("less than") the critical angle of the direction to the zone / of the opposite one
        res[f"{zn}: time moving towards (s)"] = _r(dur[out & k.mobile & (ae < tol)].sum())
        res[f"{zn}: time moving away (s)"] = _r(dur[out & k.mobile & (ae > 180.0 - tol)].sum())
    moving = ae[out & k.mobile]
    res[f"{zn}: mean absolute heading error (deg)"] = _r(np.nanmean(moving) if np.isfinite(moving).any()
                                                         else math.nan, 1)
    # initial heading error: the initial heading (see _initial_heading) vs the direction to the zone centre
    signed = math.nan
    ij = _initial_heading(p)
    if ij is not None:
        i0, j = ij
        zx, zy = z.shape.centroid()
        hdx, hdy = k.ux[j] - k.ux[i0], k.uy[j] - k.uy[i0]
        tdx, tdy = zx * k.scale - k.ux[i0], zy * k.scale - k.uy[i0]
        start_in = bool(z.shape.contains(np.array([k.ux[i0] / k.scale]), np.array([k.uy[i0] / k.scale]))[0])
        if not start_in and math.hypot(hdx, hdy) > 1e-9 and math.hypot(tdx, tdy) > 0:
            # positive: the zone is to the animal's right (clockwise of its heading on screen, y pointing down)
            signed = (math.degrees(math.atan2(tdy, tdx) - math.atan2(hdy, hdx)) + 180.0) % 360.0 - 180.0
    # as ANY-maze: the initial heading error is the absolute angle, the signed one is a separate measure
    res[f"{zn}: initial heading error (deg)"] = _r(abs(signed), 1)
    res[f"{zn}: signed initial heading error (deg)"] = _r(signed, 1)
    if p.facing is not None:
        def orient():  # as ANY-maze: the orientation (centre → head) vs the direction from the head to the centre
            hx, hy, ang = _orientation(P)
            zx, zy = z.shape.centroid()
            with np.errstate(invalid="ignore"):
                return _angle_diff(np.arctan2(zy - hy, zx - hx), ang) <= s.orientation_deg
        towards = P.cached(("oriented", zn), orient)[p.sl]
        res[f"{zn}: time oriented towards zone centre when inside (s)"] = _r(dur[vm & ~p.hid_any & towards].sum())


def _initial_heading(p: _Period) -> tuple[int, int] | None:
    """(first position, end position) frames of the animal's initial heading in the period, as ANY-maze's Heading
    error options (s.heading_error_by: after being mobile for heading_error_time_s, or the first position more than
    heading_error_distance away; positions while immobile ignored - see series.initial_heading_frames)."""
    s, k = p.P.s, p.k
    return initial_heading_frames(k.t, k.dur, k.ux, k.uy, k.mobile, s.heading_error_by, s.heading_error_time_s,
                                  s.heading_error_distance)


def _zone_turning(res, p: _Period, zn: str, vm: np.ndarray, fm: np.ndarray):
    """Absolute turn angle (direction of travel, as the whole-test measure: the turns between two moving frames
    both in the zone) and absolute head turn angle (the direction from the centre to the head) while in the zone;
    nothing across a pause or a reappearance."""
    T = _turns(p.P)
    turn, prev = T["turn"][p.sl], T["prev"][p.sl]
    keep = np.isfinite(turn) & fm[p.sl] & fm[np.maximum(prev, 0)] & (prev >= 0)
    res[f"{zn}: absolute turn angle (deg)"] = _r(float(turn[keep].sum()), 1)
    if p.P.clean.has_head():
        # as the whole-test head turn angle; a turn counts in the zone the animal is in after it (as ANY-maze)
        turn_h = p.P.cached("head_motion", lambda: _head_arrays(p.P))["turn"][p.sl]
        res[f"{zn}: absolute head turn angle (deg)"] = _r(float(np.abs(turn_h[vm]).sum()), 1)


def _zone_cipl(res, p: _Period, z, visits: list):
    """Corrected integrated path length (Gallagher): the distance from the zone sampled every second from the
    start of the period to the first entry (or the end of the period), minus the same sum for an ideal path
    straight to the zone at the animal's mean speed over that time."""
    P, k, zn = p.P, p.k, z.name
    cipl = math.nan
    ok = np.flatnonzero(np.isfinite(k.ux))
    stop = visits[0][0] if visits else p.n
    if visits and len(ok) and stop > ok[0]:  # as ANY-maze: undefined if the animal never entered the zone
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
        if pt.heatmap:  # ANY-maze's heat-map point (4.17-4.19): where the whole test's heat map is hottest
            hot = P.cached(("hot", pt.heatmap), lambda b=pt.heatmap: _hot_spot(P, b))
            if hot is None:  # e.g. the animal never froze
                res[f"{pt.name}: X ({u})"] = res[f"{pt.name}: Y ({u})"] = math.nan
                res[f"{pt.name}: approximate time at point (s)"] = math.nan
                continue
            pt = replace(pt, x=hot[0], y=hot[1])
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
        # as ANY-maze: moving towards / away when the direction of travel is within the critical angle of the
        # direction to the point / of the opposite direction (while mobile)
        tol = s.exploration_facing_deg
        with np.errstate(invalid="ignore"):
            err = A["heading_err"][p.sl]
            towards = k.mobile & (err < tol)  # "less than the critical angle"
            away = k.mobile & (err > 180.0 - tol)
        res[f"{pt.name}: time moving towards (s)"] = _r(dur[towards].sum())
        res[f"{pt.name}: time moving away (s)"] = _r(dur[away].sum())
        # distances: how much the distance to the point shrank / grew while the animal was mobile
        res[f"{pt.name}: distance moved towards ({u})"] = _r(-dd[k.mobile & (dd < 0)].sum(), 2)
        res[f"{pt.name}: distance moved away ({u})"] = _r(dd[k.mobile & (dd > 0)].sum(), 2)
        if "head_angle" in A:
            diff = A["head_angle"][p.sl]
            otol = s.orientation_deg
            res[f"{pt.name}: time head oriented towards (s)"] = _r(dur[diff < otol].sum())
            res[f"{pt.name}: time head oriented away (s)"] = _r(dur[diff > 180 - otol].sum())
            res[f"{pt.name}: mean head angle (deg)"] = _r(np.nanmean(diff), 1)
            # the times it turns towards the point (not oriented before, oriented now; not at the start of the test)
            full = P.cached(("head_towards", pt.name), lambda d=A["head_angle"]: d < s.orientation_deg)
            res[f"{pt.name}: head turns towards"] = sum(1 for a, _ in p.eps(full) if a + p.i0 > 0)
        t_tw = float(dur[towards].sum())
        res[f"{pt.name}: mean speed moving towards ({u}/s)"] = _r(k.step[towards].sum() / t_tw if t_tw > 0
                                                                  else math.nan)
        if "hdist" in A:
            hd = A["hdist"][p.sl]
            fin = np.isfinite(hd).any()
            res[f"{pt.name}: mean head distance ({u})"] = _r(np.nanmean(hd) if fin else math.nan, 2)
            res[f"{pt.name}: max head distance ({u})"] = _r(np.nanmax(hd) if fin else math.nan, 2)
            res[f"{pt.name}: min head distance ({u})"] = _r(np.nanmin(hd) if fin else math.nan, 2)
            herr, hmov = A["head_err"][p.sl], A["head_moving"][p.sl]
            with np.errstate(invalid="ignore"):
                res[f"{pt.name}: time head moving towards (s)"] = _r(dur[hmov & (herr < tol)].sum())
                res[f"{pt.name}: time head moving away (s)"] = _r(dur[hmov & (herr > 180.0 - tol)].sum())
        _point_heading(res, p, pt)
        res[f"{pt.name}: X ({u})"] = _r(pt.x * k.scale, 2)
        res[f"{pt.name}: Y ({u})"] = _r(pt.y * k.scale, 2)
        # as ANY-maze: the time the heat map gives at the point's location (approximate: heat maps are smoothed)
        res[f"{pt.name}: approximate time at point (s)"] = _r(_heat_at(p, pt.heatmap or "time", pt.x, pt.y), 2)


HEAT_BINS, HEAT_SIGMA = 60, 1.5  # heat maps of the heat-map points: squares and smoothing, as the Heat maps view


def _heat_map(P: _Prepared, basis: str, i0: int, i1: int) -> tuple | None:
    """The heat map (occupancy.occupancy_map: seconds per square, as the Heat maps view) of frames [i0, i1) of the
    test: of the animal's position ("time"), or of its position while "freezing", "immobile" or "rearing"; None
    without an apparatus or a position."""
    def make():
        tr, K = P.track, P.k
        x, y = tr.x[i0:i1], tr.y[i0:i1]  # hidden-blanked: no time where the animal could not be seen
        ok = np.isfinite(x) & np.isfinite(y)
        if basis == "freezing":
            ok &= K.freezing[i0:i1]
        elif basis == "immobile":
            ok &= ~K.mobile[i0:i1]
        elif basis == "rearing":
            ok &= _rears(P)[i0:i1]
        try:
            bounds = P.app.arena_or_bounds().bounds()
        except ValueError:
            return None
        return occupancy_map(x[ok], y[ok], K.dur[i0:i1][ok], bounds, HEAT_BINS, HEAT_SIGMA)
    return P.cached(("heat", basis, i0, i1), make)


def _hot_spot(P: _Prepared, basis: str) -> tuple[float, float] | None:
    """The centre (px) of the hottest square of the whole test's heat map (see _heat_map), or None."""
    hm = _heat_map(P, basis, 0, len(P.k.t))
    if hm is None or not hm[0].size or hm[0].max() <= 0:
        return None
    H, xe, ye = hm
    ix, iy = np.unravel_index(int(np.argmax(H)), H.shape)
    return float(xe[ix] + xe[ix + 1]) / 2, float(ye[iy] + ye[iy + 1]) / 2


def _heat_at(p: _Period, basis: str, x: float, y: float) -> float:
    """The approximate time (s) spent at the point x, y (px) in the period, from its heat map (see _heat_map): the
    smoothed map's value there × the area of its Gaussian (2π sigma²), so that each position counts by its
    closeness to the point (1 on it, 0.6 one sigma - 1.5 squares - away) and a stay on the point counts in full."""
    hm = _heat_map(p.P, basis, p.i0, p.i1)
    if hm is None:
        return math.nan
    H, xe, ye = hm
    ix, iy = int(np.searchsorted(xe, x, "right")) - 1, int(np.searchsorted(ye, y, "right")) - 1
    inside = 0 <= ix < H.shape[0] and 0 <= iy < H.shape[1]
    return float(H[ix, iy]) * 2 * math.pi * HEAT_SIGMA ** 2 if inside else 0.0


def _point_heading(res, p: _Period, pt):
    """Heading error to a point: the initial one (the initial heading, see _initial_heading, vs the direction to the
    point, as the water maze's) and the mean absolute one over the frames the animal moves."""
    k, name = p.k, pt.name
    err = math.nan
    ij = _initial_heading(p)
    if ij is not None:
        i0, j = ij
        hdx, hdy = k.x[j] - k.x[i0], k.y[j] - k.y[i0]
        tdx, tdy = pt.x - k.x[i0], pt.y - k.y[i0]
        if math.hypot(hdx, hdy) > 0 and math.hypot(tdx, tdy) > 0:
            a = math.degrees(math.atan2(hdy, hdx) - math.atan2(tdy, tdx))
            err = abs((a + 180) % 360 - 180)
    res[f"{name}: initial heading error (deg)"] = _r(err, 1)
    mv = k.mobile & np.isfinite(k.heading)
    if mv.any():
        bearing = np.arctan2(pt.y - k.y[mv], pt.x - k.x[mv])
        res[f"{name}: mean absolute heading error (deg)"] = _r(float(np.mean(_angle_diff(np.radians(k.heading[mv]),
                                                                                         bearing))), 1)
    else:
        res[f"{name}: mean absolute heading error (deg)"] = math.nan


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
        res.update(grid_measures(g, p.memb, p.t, p.dur, p.t0, p.T, s, breaks=p.k.breaks))
    if app.sequences:
        from .sequences import find_sequences, other_zones, sequence_measures

        P, K = p.P, p.P.k
        cum = P.cached("cum_step", lambda: np.cumsum(K.step))

        def travelled(ta, tb):
            """Distance travelled between times ta and tb (into the frames after ta, up to tb)."""
            i, j = np.searchsorted(K.t, [ta, tb], "right") - 1
            return float(cum[max(j, 0)] - cum[max(i, 0)]) if j > i else 0.0

        whole = p if p.whole else P.cached("whole_period", lambda: _Period(P, 0, len(K.t), float(K.t[0]),
                                                                           K.duration, None))
        t1 = p.t0 + p.T
        for q in app.sequences:
            names = list(dict.fromkeys(list(q.steps) + ([] if q.allow_other else other_zones(app, q))))
            # found on the whole test; as ANY-maze, a completed sequence belongs to the period in which it ends
            # (an incomplete attempt to the one in which it starts)
            att = P.cached(("sequence", q.name), lambda q=q, names=names: find_sequences(
                q, whole.seq([zn for zn in names if zn in P.memb]), names))
            att = [a for a in att if p.t0 - 1e-9 <= (a.end if a.completed else a.start) < t1 - 1e-9
                   or (p.whole and a.completed)]
            res.update(sequence_measures(q, att, p.t0, p.T, s.latency_if_never, distance=travelled, unit=app.unit,
                                         undefined=_undefined(s, math.nan)))


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
    """Activity (see activity_frames) and the average freezing score (the motion value that freezing is detected
    from)."""
    k, dur, T = p.k, p.dur, p.T
    has = np.isfinite(k.motion_pct)
    if has.any():
        t_has = float(dur[has].sum())
        res["Average freezing score (% body)"] = _r(float((k.motion_pct[has] * dur[has]).sum()) / t_has
                                                    if t_has > 0 else math.nan, 2)
    act_full = activity_frames(p.P)
    if act_full is None:
        return
    t_act = float(dur[act_full[p.sl]].sum())
    res["Time active (s)"] = _r(t_act)
    res["Time inactive (s)"] = _r(T - t_act)
    for label, ep in (("active", p.eps(act_full)), ("inactive", p.eps(~act_full))):
        d = [float(dur[a:b].sum()) for a, b in ep]
        res[f"{label.capitalize()} episodes"] = len(ep)
        res[f"Longest {label} episode (s)"] = _r(max(d, default=0.0))
        res[f"Shortest {label} episode (s)"] = _r(min(d, default=0.0))


def activity_frames(P: _Prepared) -> np.ndarray | None:
    """Whole test, per frame: is the animal active? As ANY-maze (s.activity_definition "mobile_or_keys"): mobile, or
    doing a behaviour whose key counts as activity (Behaviour.activity: a state / hold key, while it is on); "keys"
    (ANY-maze without immobility detection): only those behaviours; "pixel_change": activity_mask, None without a
    motion value."""
    def make():
        s = P.s
        if s.activity_definition in ("mobile_or_keys", "keys"):
            t = P.k.t
            keys = np.zeros(len(t), bool)
            names = {b.name for b in P.behaviours or () if getattr(b, "activity", False) and b.kind != "point"}
            end = float(t[-1]) + P.track.dt if len(t) else 0.0
            for e in P.events or ():
                if e.get("behaviour") in names:
                    t1 = float(e["t_end"]) if e.get("t_end") is not None else end  # still on: until the end
                    keys |= (t >= float(e["t"])) & (t < t1)
            return keys | P.k.mobile if s.activity_definition == "mobile_or_keys" else keys
        return activity_mask(P.k, s) if np.isfinite(P.k.motion_pct).any() else None
    return P.cached("active", make)


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
    cuts = P.cuts()  # nothing is counted across a pause or a reappearance
    hx = seg_moving_average(ffill(tr.hx), win, cuts) * K.scale
    hy = seg_moving_average(ffill(tr.hy), win, cuts) * K.scale
    step = np.zeros(n)
    turn = np.zeros(n)
    if n > 1:
        step[1:] = np.nan_to_num(np.hypot(np.diff(hx), np.diff(hy)))
        # head direction: as ANY-maze, the direction from the centre to the head. Frames where it is unknown (or
        # the animal is hidden) do not turn, and a jump of more than 90° from one tracked frame to the next is a
        # head / tail swap of the tracker, not a turn. The cumulative direction is smoothed like the positions.
        ang = np.where(P.hid_any, np.nan, _head_direction(tr))
        idx = np.flatnonzero(np.isfinite(ang))
        if len(idx) > 1:
            d = np.zeros(n)
            d[idx[1:]] = (np.diff(ang[idx]) + 180) % 360 - 180
            d[np.abs(d) > 90] = 0.0
            d[cuts] = 0.0
            turn[1:] = np.diff(seg_moving_average(np.cumsum(d), win, cuts))
    step[cuts] = 0.0
    turn[cuts] = 0.0
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
    # bridge gaps of up to 0.2 s with rears on both sides (not short non-rear runs at the start or end of the test)
    for a, b in runs(~rear):
        if 0 < a and b < n and dur[a:b].sum() < 0.2 - 1e-9:
            rear[a:b] = True
    return drop_short_runs(rear, t, dur, s.min_rear_s, value=True)


def _rears(P: _Prepared) -> np.ndarray:
    """The whole test's rearing frames (hidden frames are not rears)."""
    return P.cached("rearing", lambda: rearing_mask(P.track, P.s, P.k.t, P.k.dur, P.hid_any))


_REAR_NAMES = ("rears", "time rearing (s)", "latency to first rear (s)", "mean rear duration (s)",
               "max rear duration (s)", "min rear duration (s)")


def _rear_results(res, p: _Period, eps, in_mask: np.ndarray, prefix: str = ""):
    """Rear count, time, latency and mean / max / min duration ("Rears", … or "{zone}: rears", …). As ANY-maze,
    the mean is the time rearing / the number of rears, and the max / min are over the bouts of in_mask (rearing,
    in the zone), clipped to the period - a bout in a zone also starts by entering it while rearing and ends by
    leaving it."""
    t_rear = float(p.dur[in_mask].sum())
    d = [float(p.dur[a:b].sum()) for a, b in runs(in_mask)]
    # a zone's mean rear without a rear is an undefined average (ANY-maze 3.76; not the whole test's, 2.44)
    none = _undefined(p.P.s, 0.0) if prefix else 0.0
    values = (len(eps), _r(t_rear), _r(p.lat(eps)), _r(t_rear / len(eps) if eps else none),
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
    warnings = []
    for section in _SECTIONS:
        section(res, p)
    if behaviours:
        # as ANY-maze (6.x), an investigation zone's keys are scored while the animal investigates it
        zones = {**p.memb, **_investigating(p)} if s.behaviour_by_zone else None
        res.update(behaviour_measures(P.events or [], behaviours, t0, t0 + T, zones=zones, t=p.t, dur=p.dur,
                                      latency_if_never=s.latency_if_never, step=p.k.step, unit=P.app.unit))
    if P.io_events:
        try:
            res.update(io_measures(P.io_events, T, (t0, t0 + T), io_devices, settings=s,
                                   test_end=P.k.t0 + P.k.duration, whole=p.whole))
        except Exception as e:  # a malformed I/O log must not prevent the other measures
            _log.exception("I/O measures failed")
            warnings.append(f"I/O measures could not be calculated ({e})")
        try:
            res.update(_io_track(p, io_devices))
        except Exception as e:
            _log.exception("I/O measures with the track failed")
            warnings.append(f"I/O measures per zone could not be calculated ({e})")
    for name, v in (result_variables or {}).items():
        try:
            res[f"Variable: {name}"] = _r(float(v))
        except (TypeError, ValueError):
            res[f"Variable: {name}"] = str(v)
    if s.immobility_mode == "motion" and P.k.struggle is None:
        warnings.append("No pixel-change data: immobility comes from the speed of the animal")
    if not P.app.px_per_cm:
        warnings.insert(0, "Apparatus not calibrated: distances, speeds and distance thresholds (mobility, "
                           "thigmotaxis, contact, ...) are in pixels")
    if warnings:
        res["Warnings"] = "; ".join(warnings)
    return to_report_units(res, P.app.report_unit)  # distances in the apparatus's unit (mm / m; cm as computed)


def _io_track(p: _Period, io_devices) -> dict:
    """I/O measures that need the track: virtual switches (distance), analogue inputs per zone visit and the other
    devices per zone (virtual switches: while the animal investigates an investigation zone, as ANY-maze 22.x)."""
    P = p.P
    grid_cells = {c for g in P.app.grids for c in g.zones}
    visits = {zn: p.eps(P.visits_mask(zn, m), entries=True) for zn, m in P.memb.items() if zn not in grid_cells}
    zones = {zn: P.visits_mask(zn, m)[p.sl] for zn, m in P.memb.items() if zn not in grid_cells}
    return io_track_measures(P.io_events, p.t, p.dur, p.k.step, (p.t0, p.t0 + p.T), P.app.unit, visits, io_devices,
                             P.s.latency_if_never, zones, settings=P.s, investigating=_investigating(p))


def _investigating(p: _Period) -> dict:
    """{investigation zone: per frame of the period, the animal investigates it} (see _investigation)."""
    return {z.name: _investigation(p.P, z)[p.sl] for z in p.P.app.zones
            if z.investigation_distance_cm > 0 and z.name in p.P.memb}


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
            o = _orientation(P)
            diff = (_angle_diff(np.arctan2(p.y - hy, p.x - hx), o[2]) if o is not None
                    else np.full(len(hx), np.inf))
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
        dd[P.cuts()] = 0.0
    out["dd"] = dd
    # heading error (deg): the direction of travel into each frame vs the direction from the previous position to
    # the point (ANY-maze: the vector to the next position vs the vector to the point)
    hd_err = np.full(len(K.t), np.nan)
    if len(K.t) > 1:
        with np.errstate(invalid="ignore"):
            brg = np.arctan2(p.y * K.scale - K.uy[:-1], p.x * K.scale - K.ux[:-1])
            hd_err[1:] = _angle_diff(np.radians(K.heading[1:]), brg)
    out["heading_err"] = hd_err
    o = _orientation(P)
    if o is not None:  # as ANY-maze: the orientation (centre → head) vs the direction from the head to the point
        diff = _angle_diff(np.arctan2(p.y - o[1], p.x - o[0]), o[2])
        out["head_angle"] = diff
    if tr.has_head() and np.isfinite(tr.hx).any():
        # the head: its distance from the point, and when it moves (smoothed like the centre) towards / away from it
        hxf, hyf = ffill(tr.hx), ffill(tr.hy)
        out["hdist"] = np.hypot((hxf - p.x) * K.scale, (hyf - p.y) * K.scale)
        win = max(1, int(round(s.speed_smoothing_s / max(tr.dt, 1e-6))))
        cuts = P.cuts()
        hux = seg_moving_average(hxf, win, cuts) * K.scale
        huy = seg_moving_average(hyf, win, cuts) * K.scale
        hdd = np.zeros(len(K.t))
        hstep = np.zeros(len(K.t))
        h_err = np.full(len(K.t), np.nan)  # the head's direction of movement vs the direction to the point
        if len(hdd) > 1:
            hdd[1:] = np.nan_to_num(np.diff(np.hypot(hux - p.x * K.scale, huy - p.y * K.scale)))
            hstep[1:] = np.nan_to_num(np.hypot(np.diff(hux), np.diff(huy)))
            hdd[cuts] = 0.0
            hstep[cuts] = 0.0
            with np.errstate(invalid="ignore"):
                h_err[1:] = _angle_diff(np.arctan2(np.diff(huy), np.diff(hux)),
                                        np.arctan2(p.y * K.scale - huy[:-1], p.x * K.scale - hux[:-1]))
            h_err[(hstep < 1e-9) | cuts] = np.nan
        out["head_err"] = h_err
        dts = np.diff(K.t, prepend=K.t[0] - tr.dt)
        hspeed = hstep / np.where(dts <= 0, tr.dt, dts)
        hsp = seg_moving_average(hspeed, max(1, int(round(0.5 / max(tr.dt, 1e-6)))), P.breaks)
        out["hdd"] = hdd
        out["head_moving"] = np.nan_to_num(hsp) >= s.mobility_threshold
    return out


def grid_measures(g, memb: dict, t: np.ndarray, dur: np.ndarray, t0: float, T: float,
                  s: AnalysisSettings, breaks: np.ndarray | None = None) -> "OrderedDict[str, object]":
    """Crossings and coverage of a grid of zones (any kind: square, rings, sectors). breaks: frames that follow a
    pause (no crossing is counted across a pause)."""
    out: OrderedDict[str, object] = OrderedDict()
    cells = [c for c in g.zones if c in memb]
    n = len(t)
    if not cells or n == 0:
        return out
    cur = np.full(n, -1)
    for i, c in enumerate(cells):
        cur[memb[c] & (cur < 0)] = i
    idx = np.flatnonzero(cur >= 0)
    seen = cur[idx]
    ch = seen[1:] != seen[:-1]
    if breaks is not None and len(breaks) == n and np.any(breaks):
        bc = np.cumsum(np.asarray(breaks, bool))
        ch &= bc[idx[1:]] == bc[idx[:-1]]
    out[f"{g.name}: crossings"] = int(np.count_nonzero(ch))
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


def _on_frames(o: Track, t: np.ndarray) -> Track:
    """The other animal's track at frame times t (its nearest frame within half a frame; none: not detected). Its
    track is a separate file (re-tracked, imported, live...), so the frames are paired by time, not by index; frames
    after its last one are dropped."""
    if len(o) == 0 or len(t) == 0:
        return o.take(slice(0, 0))
    tol = 0.5 * min(o.dt, float(np.median(np.diff(t))) if len(t) > 1 else o.dt) + 1e-9
    t = t[:int(np.searchsorted(t, o.t[-1] + tol, "right"))]
    j = np.clip(np.searchsorted(o.t, t), 1, max(1, len(o) - 1)) if len(o) > 1 else np.zeros(len(t), int)
    if len(o) > 1:
        j = np.where(np.abs(o.t[j - 1] - t) <= np.abs(o.t[j] - t), j - 1, j)
    a = o.take(j)
    a.t = np.asarray(t, float).copy()
    miss = np.abs(o.t[j] - t) > tol
    if miss.any():
        for c in ("x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle"):
            getattr(a, c)[miss] = np.nan
        a.detected[miss] = False
    return a


def social_measures(track: Track, k: Kinematics, o: Track, app: Apparatus, s: AnalysisSettings,
                    label: str) -> "OrderedDict[str, object]":
    """Inter-animal measures for this animal relative to another animal tracked in the same arena."""
    out: OrderedDict[str, object] = OrderedDict()
    o = _on_frames(o, track.t)
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
                       latency_if_never: str = "duration", step: np.ndarray | None = None,
                       unit: str = "") -> "OrderedDict[str, object]":
    """Measures from manually scored events.

    events: [{"behaviour", "t", "t_end" (state only)}]
    zones: optional {zone: per-frame bool} with frame times t / durations dur → the same measures per zone.
    step: optional distance travelled into each frame t → distance travelled before the first press (unit: its
    unit, for the measure name).
    """
    out: OrderedDict[str, object] = OrderedDict()
    T = t1 - t0
    never = T if latency_if_never == "duration" else math.nan
    use_step = step is not None and t is not None and len(t) == len(step) and len(t) > 0
    u = f" ({unit})" if unit else ""

    def dist_before(first, prefix=None):
        """Distance travelled before the first press (at time first; None = never) as "<prefix>: distance before
        first press"."""
        if not use_step:
            return
        if first is None:
            d = float(step[1:].sum()) if latency_if_never == "duration" else math.nan
        else:
            j = int(np.searchsorted(t, first, "right")) - 1
            d = float(step[1:j + 1].sum()) if j > 0 else 0.0
        out[f"{prefix or name}: distance before first press{u}"] = _r(d, 2)

    def frame(x):
        return min(max(int(np.searchsorted(t, x, "right")) - 1, 0), len(t) - 1)

    def zone_time(m):
        d = dur if dur is not None else np.full(len(t), (t1 - t0) / max(len(t), 1))
        return float(d[m].sum())

    for b in behaviours:
        name = b.name
        evs = [e for e in events if e.get("behaviour") == name]
        if b.kind == "point":
            ts = sorted(e["t"] for e in evs if t0 <= e["t"] < t1)
            out[f"{name}: count"] = len(ts)
            out[f"{name}: latency (s)"] = _r(ts[0] - t0 if ts else never)
            out[f"{name}: rate (/min)"] = _r(len(ts) / (T / 60) if T > 0 else math.nan)
            dist_before(ts[0] if ts else None)
            if zones and t is not None and len(t):
                for zn, m in zones.items():
                    zt = [x for x in ts if m[frame(x)]]
                    tz = zone_time(m)  # as ANY-maze: the rate in a zone is per minute spent in the zone
                    out[f"{name} in {zn}: count"] = len(zt)
                    out[f"{name} in {zn}: latency (s)"] = _r(zt[0] - t0 if zt else never)
                    out[f"{name} in {zn}: rate (/min)"] = _r(len(zt) / (tz / 60) if tz > 0 else math.nan)
                    dist_before(zt[0] if zt else None, f"{name} in {zn}")
        else:
            spans = []
            for e in evs:
                a = max(e["t"], t0)
                bb = min(e.get("t_end", e["t"]) if e.get("t_end") is not None else t1, t1)
                if bb > a:
                    spans.append((a, bb))
            spans.sort()  # the bouts in the period, clipped to it (one under way at its start included)
            # as ANY-maze, presses and releases are the moments the key goes down / up in the period: a bout under
            # way at the start of the period is time pressed, not a press
            presses = sorted(e["t"] for e in evs if t0 <= e["t"] < t1)
            releases = sorted(e["t_end"] for e in evs if e.get("t_end") is not None and t0 <= e["t_end"] < t1)
            total = sum(bb - a for a, bb in spans)
            out[f"{name}: count"] = len(presses)
            out[f"{name}: duration (s)"] = _r(total)
            out[f"{name}: duration (%)"] = _r(100 * total / T if T > 0 else math.nan, 2)
            out[f"{name}: latency (s)"] = _r(presses[0] - t0 if presses else never)
            out[f"{name}: mean bout (s)"] = _r(total / len(presses) if presses else 0.0)  # time pressed / presses
            out[f"{name}: longest bout (s)"] = _r(max((bb - a for a, bb in spans), default=0.0))
            out[f"{name}: shortest bout (s)"] = _r(min((bb - a for a, bb in spans), default=0.0))
            out[f"{name}: latency to first release (s)"] = _r(releases[0] - t0 if releases else never)
            out[f"{name}: rate (/min)"] = _r(len(presses) / (T / 60) if T > 0 else math.nan)
            out[f"{name}: press durations (s)"] = ", ".join(f"{_r(bb - a):g}" for a, bb in spans)
            dist_before(presses[0] if presses else None)
            if zones and t is not None and len(t):
                d = dur if dur is not None else np.full(len(t), (t1 - t0) / max(len(t), 1))
                active = np.zeros(len(t), bool)
                for a, bb in spans:
                    active |= (t >= a) & (t < bb)
                for zn, m in zones.items():
                    on = active & m
                    # a press / release counts in the zone the animal is in at that moment (as ANY-maze)
                    starts = [a for a in presses if m[frame(a)]]
                    rel = [x for x in releases if m[frame(x)]]
                    lens = [bb - a for a, bb in spans if m[frame(a)]]  # list: the presses that started in the zone
                    bouts = [float(d[a:bb].sum()) for a, bb in runs(on)]  # pressed while in the zone, continuously
                    tz = float(d[on].sum())
                    t_zone = float(d[m].sum())
                    g = f"{name} in {zn}"
                    out[f"{g}: count"] = len(starts)
                    out[f"{g}: duration (s)"] = _r(tz)
                    out[f"{g}: latency (s)"] = _r(starts[0] - t0 if starts else never)
                    out[f"{g}: mean bout (s)"] = _r(tz / len(starts) if starts else 0.0)
                    out[f"{g}: rate (/min)"] = _r(len(starts) / (t_zone / 60) if t_zone > 0 else math.nan)
                    out[f"{g}: longest bout (s)"] = _r(max(bouts, default=0.0))
                    out[f"{g}: shortest bout (s)"] = _r(min(bouts, default=0.0))
                    out[f"{g}: latency to first release (s)"] = _r(rel[0] - t0 if rel else never)
                    out[f"{g}: press durations (s)"] = ", ".join(f"{_r(x):g}" for x in lens)
                    dist_before(starts[0] if starts else None, g)
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
                 kw.get("zone_overrides"), kw.get("pauses"), kw.get("duration"), kw.get("behaviours"))
    rest = dict(behaviours=kw.get("behaviours"), result_variables=kw.get("result_variables"),
                io_devices=kw.get("io_devices"), calculations=kw.get("calculations"))
    out = [("Whole test", _results(P, None, **rest))]
    # periods in test time, from the pause-free track and test-time events (and the whole test's calculations)
    from .periods import no_end_warning

    for per in _periods(P):
        res = _results(P, (per.t0, per.t1), **rest)
        out.append((per.label, add_warning(res, no_end_warning(per, s.event_periods))))
    return out


def analyse_period(track: Track, app: Apparatus, s: AnalysisSettings, spec, calc_values: dict | None = None,
                   **kw) -> dict | None:
    """The measures of part of a test as a calculation's result_for_period() sees them: spec (from_s, to_s) in test
    time or the name of a time period; None if the test ended before it (keywords as analyse()). calc_values: the
    whole test's results with its calculations, for time periods defined by calculations (default: worked out with
    the `calculations` keyword)."""
    P = _prepare(track, app, s, kw.get("events"), kw.get("io_events"), kw.get("other_tracks"),
                 kw.get("zone_overrides"), kw.get("pauses"), kw.get("duration"), kw.get("behaviours"))
    args = (kw.get("behaviours"), kw.get("result_variables"), kw.get("io_devices"))
    if calc_values is not None:
        P.cache["calc_view"] = calc_values
    elif kw.get("calculations"):
        from .calculations import plan

        P.cache["calc_args"] = args + (plan(kw["calculations"], periods=s.event_periods),)
    return _calc_period(P, spec, *args)


def io_only_measures(duration: float, s: AnalysisSettings | None = None, io_events: list | None = None,
                     io_devices: list | None = None, events: list | None = None,
                     behaviours: list[Behaviour] | None = None, result_variables: dict | None = None,
                     t_range: tuple[float, float] | None = None, pauses: list | None = None
                     ) -> "OrderedDict[str, object]":
    """Measures of a test without a track (ANY-maze's I/O only mode): its duration, the scored keys, the I/O log
    (iomeasures.io_measures) and the procedures' result variables, for the whole test or a period t_range (test
    time; up to the end of the test)."""
    s = s or AnalysisSettings()
    events, io_events = shift_events(events, pauses), shift_events(io_events, pauses)
    a, b = (0.0, float(duration)) if t_range is None else (float(t_range[0]), min(float(t_range[1]), duration))
    T = max(0.0, b - a)
    res: OrderedDict[str, object] = OrderedDict([("Test duration (s)", _r(T))])
    if behaviours:
        res.update(behaviour_measures(events or [], behaviours, a, a + T, latency_if_never=s.latency_if_never))
    if io_events:
        try:
            res.update(io_measures(io_events, T, (a, a + T), io_devices, settings=s, test_end=float(duration),
                                   whole=t_range is None))
        except Exception as e:  # a malformed I/O log must not prevent the other measures
            _log.exception("I/O measures failed")
            res["Warnings"] = f"I/O measures could not be calculated ({e})"
    for name, v in (result_variables or {}).items():
        try:
            res[f"Variable: {name}"] = _r(float(v))
        except (TypeError, ValueError):
            res[f"Variable: {name}"] = str(v)
    return res


def io_only_periods(duration: float, s: AnalysisSettings, events=None, io_events=None, app=None,
                    pauses=None, calc=None, resolved: bool = False) -> list:
    """The time periods of a test without a track: time bins / custom periods, then the event-anchored periods
    that need no track (test start, a key mark, an input switching on, a calculation's time; calc: the whole test's
    results). (label, t0, t1) each, or periods.Period (with the no-end flag) when resolved."""
    from .periods import Period, resolve_periods

    out = [Period(label, a, b) for label, a, b in time_periods(duration, s)]
    if s.event_periods:
        out += resolve_periods(s.event_periods, duration, None, app, s, shift_events(events, pauses),
                               shift_events(io_events, pauses), calc=calc)
    return out if resolved else [(x.label, x.t0, x.t1) for x in out]


def all_periods(track: Track, app: Apparatus, s: AnalysisSettings, duration: float | None = None, events=None,
                io_events=None, zone_overrides=None, pauses=None, calc=None, resolved: bool = False) -> list:
    """Time bins / custom periods followed by event-anchored periods, in test time (pauses removed, as analyse()).
    calc: the whole test's results with its calculations ({column: value}), for periods defined by calculations.
    (label, t0, t1) each, or periods.Period (with the no-end flag) when resolved."""
    from .periods import Period, resolve_periods

    track = drop_pauses(track, pauses)[0]
    events, io_events = shift_events(events, pauses), shift_events(io_events, pauses)
    if duration is None:
        duration = track.t[-1] + track.dt if len(track) else 0
    out = [Period(label, a, b) for label, a, b in time_periods(duration, s)]
    if s.event_periods:
        out += resolve_periods(s.event_periods, duration, track, apply_overrides(app, zone_overrides), s, events,
                               io_events, calc=calc)
    return out if resolved else [(x.label, x.t0, x.t1) for x in out]
