"""Zone occupancy per frame (entry rules, investigation distance, exclusion and hidden zones) and zone visits."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from .apparatus import Apparatus
from .geometry import body_fraction_inside
from .series import drop_short_runs, ffill, runs
from .track import Track

if TYPE_CHECKING:
    from .measures import AnalysisSettings


def zone_visits(t: np.ndarray, dur: np.ndarray, inside: np.ndarray, s: AnalysisSettings,
                all_runs: bool = False) -> list[tuple[int, int]]:
    """[start, end) visits of a zone (runs shorter than s.entry_min_duration_s are ignored).

    By default these are the *entries*: with s.count_initial_entry False a visit the animal starts the test in is
    not one. all_runs=True returns every visit (use it for time in the zone, which includes the initial visit).
    """
    m = drop_short_runs(inside, t, dur, s.entry_min_duration_s, value=True)
    v = runs(m)
    if not all_runs and not s.count_initial_entry and v and v[0][0] == 0:
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
    """In-zone state from the fraction of the body inside: enters at >= enter, leaves below min(enter, 1-enter)
    (or at 0); frames without a fraction (NaN) keep the state.

    Vectorised: a frame whose fraction gives the same next state whether the animal was in or out decides the
    state; the others (between the leave and enter levels) hold the last decided state."""
    frac = np.asarray(frac, float)
    n = len(frac)
    leave = min(enter, 1.0 - enter)
    fin = np.isfinite(frac)
    with np.errstate(invalid="ignore"):
        from_out = fin & (frac >= enter)  # next state when out
        from_in = fin & (frac >= leave) & (frac > 0)  # next state when in
    if np.any(fin & from_out & ~from_in):  # toggles with the state (only with enter <= 0): step through it
        out = np.zeros(n, bool)
        state = False
        for i in range(n):
            if fin[i]:
                state = bool(from_in[i]) if state else bool(from_out[i])
            out[i] = state
        return out
    decided = fin & (from_out == from_in)
    idx = np.where(decided, np.arange(n), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, from_out[np.maximum(idx, 0)], False)


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
        if z.entry_orientation_deg and z.entry_orientation_deg > 0 and not part:
            zones[z.name] = oriented_visits(zones[z.name], z, *pos("centre"), track.angle)
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


def oriented_visits(inside: np.ndarray, z, x: np.ndarray, y: np.ndarray, angle_deg: np.ndarray) -> np.ndarray:
    """Zone membership where each visit only starts at the first frame the animal is oriented towards zone z: its
    body orientation (angle_deg, forward filled) within z.entry_orientation_deg of the direction from its centre
    (x, y) to the zone centre. A visit in which it never is does not count. Without any orientation the rule
    cannot apply and inside is returned unchanged."""
    ang = ffill(np.asarray(angle_deg, float))
    if not np.isfinite(ang).any():
        return inside
    zx, zy = z.shape.centroid()
    with np.errstate(invalid="ignore"):
        brg = np.degrees(np.arctan2(zy - y, zx - x))
        diff = np.abs((ang - brg + 180.0) % 360.0 - 180.0)
        ok = ~np.isfinite(diff) | (diff <= float(z.entry_orientation_deg))
    out = np.asarray(inside, bool).copy()
    for a, b in runs(out):
        hit = np.flatnonzero(ok[a:b])
        out[a:a + (hit[0] if len(hit) else b - a)] = False
    return out


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


def occupancy_map(x: np.ndarray, y: np.ndarray, w: np.ndarray, bounds, bins: int = 60,
                  sigma: float = 1.5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Time spent in each square of a grid over bounds (x0, y0, x1, y1), the longer side cut in `bins` squares,
    smoothed with a Gaussian of `sigma` squares: the heat map of the positions x, y weighted by w (their frame
    durations) - drawn by plots.occupancy and used for the heat-map points (ANY-maze 4.17-4.19). Returns
    (H [x square, y square], x edges, y edges)."""
    from scipy.ndimage import gaussian_filter

    x0, y0, x1, y1 = bounds
    span = max(x1 - x0, y1 - y0, 1e-6)
    nx = max(2, int(round(bins * (x1 - x0) / span)))
    ny = max(2, int(round(bins * (y1 - y0) / span)))
    H, xe, ye = np.histogram2d(x, y, bins=[nx, ny], range=[[x0, x1], [y0, y1]], weights=w)
    if sigma > 0:
        H = gaussian_filter(H, sigma)
    return H, xe, ye
