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
