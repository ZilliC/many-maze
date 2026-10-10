"""Helpers for per-frame series: runs of a mask, forward filling, NaN-aware moving averages, rotations, rounding."""

from __future__ import annotations

import math

import numpy as np


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
    """Centred moving average over an odd window (made odd; at most the series length), ignoring NaNs; NaN samples
    stay NaN."""
    window = min(int(window) | 1, len(v) - 1 + len(v) % 2)
    if window < 2:
        return v.copy()
    ok = np.isfinite(v)
    vv = np.where(ok, v, 0.0)
    k = np.ones(window)
    num = np.convolve(vv, k, mode="same")
    den = np.convolve(ok.astype(float), k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~ok] = np.nan
    return out


def segments(n: int, breaks: np.ndarray | None) -> list[tuple[int, int]]:
    """[start, end) index ranges of the stretches of track between pauses (breaks mark a segment's first frame)."""
    if breaks is None or not np.any(breaks):
        return [(0, n)]
    b = np.flatnonzero(breaks)
    edges = [0] + b[b > 0].tolist() + [n]
    return list(zip(edges[:-1], edges[1:]))


def seg_moving_average(v: np.ndarray, window: int, breaks: np.ndarray | None) -> np.ndarray:
    """moving_average applied separately to each stretch between pauses (no smoothing across a pause)."""
    if breaks is None or not np.any(breaks):
        return moving_average(v, window)
    out = np.empty(len(v))
    for a, b in segments(len(v), breaks):
        out[a:b] = moving_average(v[a:b], window)
    return out


def round_result(v, nd=3):
    """A result value: ints kept, floats rounded to nd decimals, None / inf -> NaN."""
    if v is None:
        return math.nan
    if isinstance(v, (int, np.integer)):
        return int(v)
    v = float(v)
    return round(v, nd) if math.isfinite(v) else math.nan


def count_rotations(angle_deg: np.ndarray, reset_deg: float = 90.0) -> tuple[int, int]:
    """Count full 360° rotations (clockwise, anticlockwise) of an orientation series.

    A rotation is counted when the cumulative turn since the last reference reaches 360°
    in one direction; the reference is reset if the animal turns back by more than reset_deg.
    In image coordinates (y down) a positive angle change is clockwise.
    """
    _, sign = rotation_events(angle_deg, reset_deg)
    return int((sign > 0).sum()), int((sign < 0).sum())


def rotation_events(angle_deg: np.ndarray, reset_deg: float = 90.0) -> tuple[np.ndarray, np.ndarray]:
    """The rotations count_rotations() counts, as (index in angle_deg of the sample completing each rotation,
    +1 clockwise / -1 anticlockwise) - so that the rotations of a whole test can be shared out between periods."""
    return _rotation_walk(angle_deg, reset_deg)[:2]


def partial_rotation_events(angle_deg: np.ndarray, reset_deg: float = 90.0,
                            partial_deg: float = 90.0) -> tuple[np.ndarray, np.ndarray]:
    """ANY-maze's partial rotations: the turns of the orientation in one direction (from one reversal to the next -
    turning back by more than reset_deg, as for rotations - or to the start / end of the series) of at least
    partial_deg during which no rotation was completed, as (index in angle_deg of the sample where each turn stopped,
    +1 clockwise / -1 anticlockwise)."""
    return _rotation_walk(angle_deg, reset_deg, partial_deg)[2:]


def _rotation_walk(angle_deg, reset_deg, partial_deg=0.0) -> tuple[np.ndarray, ...]:
    """(rotation samples, signs, partial rotation samples, signs): see rotation_events / partial_rotation_events."""
    a = np.asarray(angle_deg, float)
    idx = np.flatnonzero(np.isfinite(a))
    if len(idx) < 2:
        return (np.zeros(0, int),) * 4
    u = np.degrees(np.unwrap(np.radians(a[idx])))
    at, sign, p_at, p_sign = [], [], [], []
    ref = u[0]
    hi = lo = u[0]
    # turns, for the partial rotations: d the direction (0 until the orientation has moved by more than reset_deg),
    # start the extreme it started from, ext the furthest it has gone, full: a rotation was completed in it
    d, start, ext, ext_i, full = 0, u[0], u[0], 0, False
    thi = tlo = u[0]
    thi_i = tlo_i = 0

    def end_turn():
        if partial_deg > 0 and not full and abs(ext - start) >= partial_deg - 1e-9:
            p_at.append(idx[ext_i])
            p_sign.append(d)

    for i, v in enumerate(u[1:], 1):
        if d == 0:
            if v > thi:
                thi, thi_i = v, i
            if v < tlo:
                tlo, tlo_i = v, i
            if thi - tlo > reset_deg:  # the first turn: from the earlier extreme to the later one
                d, start, ext, ext_i = (1, tlo, thi, thi_i) if thi_i > tlo_i else (-1, thi, tlo, tlo_i)
        elif d * (v - ext) > 0:
            ext, ext_i = v, i
        elif d * (ext - v) > reset_deg:  # a reversal: v is the furthest the new turn has gone
            end_turn()
            d, start, ext, ext_i, full = -d, ext, v, i, False
        hi = max(hi, v)
        lo = min(lo, v)
        if v - ref >= 360:
            at.append(idx[i])
            sign.append(1)
            ref = v
            hi = lo = v
            full = True
        elif ref - v >= 360:
            at.append(idx[i])
            sign.append(-1)
            ref = v
            hi = lo = v
            full = True
        elif hi - v > reset_deg and hi > ref:
            ref = v
            hi = lo = v
        elif v - lo > reset_deg and lo < ref:
            ref = v
            hi = lo = v
    if d:
        end_turn()
    elif partial_deg > 0 and thi - tlo >= partial_deg - 1e-9:  # a single turn of no more than reset_deg
        p_at.append(idx[max(thi_i, tlo_i)])
        p_sign.append(1 if thi_i > tlo_i else -1)
    return np.asarray(at, int), np.asarray(sign, int), np.asarray(p_at, int), np.asarray(p_sign, int)


def initial_heading_frames(t: np.ndarray, dur: np.ndarray, x: np.ndarray, y: np.ndarray, mobile: np.ndarray,
                           by: str = "time", time_s: float = 1.0, distance: float = 0.0) -> tuple[int, int] | None:
    """The animal's initial heading as ANY-maze's Heading error options define it: (first position, end position)
    frames of the vector from the first position to the first position after the animal has been mobile for time_s
    (by "time"), or to the first position more than `distance` (in the units of x / y) from it (by "distance").
    Positions while the animal is immobile are ignored. Without enough mobility for the time, the last mobile
    position; None when there is none (or, by distance, when the animal never gets that far)."""
    ok = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if len(ok) < 3:
        return None
    i0 = int(ok[0])
    mob = ok[(ok > i0) & np.asarray(mobile, bool)[ok]]
    if not len(mob):
        return None
    if by == "distance":
        far = mob[np.hypot(x[mob] - x[i0], y[mob] - y[i0]) > max(float(distance), 0.0)]
        return (i0, int(far[0])) if len(far) else None
    # mobile time elapsed since the first position when each frame starts
    m_dur = np.where(np.asarray(mobile, bool), dur, 0.0)
    elapsed = np.concatenate([[0.0], np.cumsum(m_dur[i0:])])[mob - i0]
    j = np.flatnonzero(elapsed >= float(time_s) - 1e-9)
    return i0, int(mob[j[0]] if len(j) else mob[-1])
