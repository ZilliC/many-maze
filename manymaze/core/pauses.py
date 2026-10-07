"""Test time: removing the intervals a test was paused (``Test.pauses``) from tracks and event times."""

from __future__ import annotations

import math

import numpy as np

from .track import Track


def merged_pauses(pauses) -> list[tuple[float, float]]:
    """Sorted, merged pause intervals [(a, b)]: b == a for a live pause marker (the clock was stopped, nothing to
    remove), b = inf for a pause that never ended."""
    iv = []
    for p in pauses or []:
        try:
            a = float(p[0])
            b = float(p[1]) if len(p) > 1 and p[1] is not None else math.inf
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(a):
            iv.append((a, max(a, b)))
    iv.sort()
    out: list[list[float]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def to_test_time(times, pauses) -> np.ndarray:
    """Map recording (video) times to test time: paused time is removed, i.e. times after a pause [a, b] move back
    by b - a and times inside it map to a. Live pause markers [t, t] (the clock was stopped) change nothing."""
    v = np.asarray(times, float)
    out = v.copy()
    for a, b in merged_pauses(pauses):
        if b > a:
            out = out - np.clip(v - a, 0.0, b - a)
    return out


def shift_events(evs, pauses, keys=("t", "t_end")):
    """Copies of event dicts with their times converted to test time (see to_test_time)."""
    iv = [p for p in merged_pauses(pauses) if p[1] > p[0]]
    if not evs or not iv:
        return evs
    out = []
    for e in evs:
        e2 = dict(e)
        for key in keys:
            v = e2.get(key)
            if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool):
                e2[key] = float(to_test_time([float(v)], iv)[0])
        out.append(e2)
    return out


def drop_pauses(track: Track, pauses) -> tuple[Track, np.ndarray]:
    """Remove frames inside paused intervals and convert times to test time (see to_test_time).

    Returns the track and the frames that follow a pause ("breaks": the first frame at or after each pause start,
    including zero-length live pause markers [t, t] where no frame is removed but the animal may have moved).
    """
    iv = merged_pauses(pauses)
    n = len(track)
    if not iv or n == 0:
        return track, np.zeros(n, bool)
    t = track.t
    keep = np.ones(n, bool)
    for a, b in iv:
        if b > a:
            keep &= ~((t >= a) & (t < b))
    kt = t[keep]
    breaks = np.zeros(len(kt), bool)
    for a, _b in iv:
        j = int(np.searchsorted(kt, a, "left"))
        if 0 < j < len(kt):
            breaks[j] = True
    if keep.all() and not any(b > a for a, b in iv):
        return track, breaks
    out = track.take(keep)
    out.t = to_test_time(out.t, iv)
    return out, breaks
