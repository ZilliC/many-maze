"""Measures of grid cells, of the other animals in the arena (social contacts, following) and of the manually
scored behaviours and keys; computed per test or time period by core.measures."""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import TYPE_CHECKING

import numpy as np

from .apparatus import Apparatus
from .geometry import point_segment_distance
from .series import drop_short_runs, ffill, round_result as _r, runs
from .track import Track

if TYPE_CHECKING:
    from .measures import AnalysisSettings, Kinematics
    from .project import Behaviour


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
    from .measures import _angle_diff, body_length, kinematics  # (core.measures imports this module)

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
