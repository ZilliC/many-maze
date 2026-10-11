"""Zone sequences: multi-step movement patterns between zones (e.g. arm A → B → C) and their measures."""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from .apparatus import Apparatus, Sequence


@dataclass
class Attempt:
    start: float  # entry into the first step
    end: float | None  # completion time (None = incomplete)
    steps: list[str]
    errors: int = 0
    direction: int = 1  # 1 = as defined, -1 = reversed

    @property
    def completed(self) -> bool:
        return self.end is not None

    @property
    def duration(self) -> float:
        return (self.end - self.start) if self.end is not None else math.nan


def other_zones(app: Apparatus, seq: Sequence) -> list[str]:
    """Zones whose entry breaks a sequence that does not allow intervening zones.

    All zones not used by the sequence, except "container" zones (e.g. the whole arena) that contain the
    centre of a step zone, and except hidden zones.
    """
    steps = set(seq.steps)
    cents = []
    for n in steps:
        z = app.zone(n)
        if z is not None:
            cents.append(z.shape.centroid())
        else:
            g = app.group(n)
            for zn in (g.zones if g else []):
                zz = app.zone(zn)
                if zz is not None:
                    cents.append(zz.shape.centroid())
    out = []
    for z in app.zones:
        if z.name in steps or z.hidden:
            continue
        if any(bool(z.shape.contains(cx, cy)) for cx, cy in cents):
            continue
        g_members = {zn for gname in steps for zn in (app.group(gname).zones if app.group(gname) else [])}
        if z.name in g_members:
            continue
        out.append(z.name)
    return out


def entry_stream(entries: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Sort entries by time and merge consecutive entries into the same zone."""
    out: list[list] = []
    for zn, a, b in sorted(entries, key=lambda e: (e[1], e[2])):
        if out and out[-1][0] == zn:
            out[-1][2] = max(out[-1][2], b)
            continue
        out.append([zn, a, b])
    return [tuple(e) for e in out]


def _patterns(seq: Sequence) -> list[tuple[list[str], int]]:
    steps = list(seq.steps)
    pats = []
    rots = [steps] if seq.from_start or len(steps) < 2 else [steps[i:] + steps[:i] for i in range(len(steps))]
    for r in rots:
        pats.append((r, 1))
        if seq.bidirectional and len(steps) > 1:
            pats.append((r[::-1], -1))
    # drop duplicate patterns (e.g. reversed rotation equals another rotation)
    seen, uniq = set(), []
    for p, d in pats:
        if tuple(p) not in seen:
            seen.add(tuple(p))
            uniq.append((p, d))
    return uniq


def find_sequences(seq: Sequence, entries: list[tuple[str, float, float]], others=()) -> list[Attempt]:
    """Match a sequence definition against a time-ordered zone entry stream.

    entries: (zone, t_enter, t_exit) for the step zones (and `others`, the zones that break the sequence when
    seq.allow_other is False). An attempt starts on entering a first step; entering a step zone out of order
    (or a forbidden other zone) is an error that ends the attempt (the zone may start a new one).
    """
    steps = list(seq.steps)
    if not steps:
        return []
    stream = entry_stream([e for e in entries if e[0] in set(steps) or (not seq.allow_other and e[0] in set(others))])
    pats = _patterns(seq)
    starts = {p[0] for p, _ in pats}
    attempts: list[Attempt] = []
    cur: list | None = None  # [candidate patterns, progress, start time, visited, errors, start index]
    last_done = -1  # stream index of the last completion (overlap re-scans do not report failures before it)

    def begin(i):
        zn, a, _ = stream[i]
        cands = [(p, d) for p, d in pats if p[0] == zn]
        return [cands, 1, a, [zn], 0, i] if cands else None

    def fail(c, visited, errs):
        if c[5] > last_done:
            attempts.append(Attempt(c[2], None, visited, errs, c[0][0][1]))

    i = 0
    while i < len(stream):
        zn, a, b = stream[i]
        if cur is None:
            if zn in starts:
                cur = begin(i)
                if len(steps) == 1:  # (one step: "return" to it means leaving it first, so completes on exit)
                    attempts.append(Attempt(a, b if seq.end in ("exit", "return") else a, [zn]))
                    cur = None
            i += 1
            continue
        cands, prog, t_start, visited, errs, i0 = cur
        if seq.max_duration_s and seq.max_duration_s > 0 and a - t_start > seq.max_duration_s:
            fail(cur, visited, errs + 1)
            cur = None
            continue  # re-process this entry as a possible new start
        # "return": after the last step the pattern completes on re-entering its first step
        ok = [(p, d) for p, d in cands if (p + [p[0]] if seq.end == "return" else p)[prog] == zn]
        if ok:
            prog += 1
            visited = visited + [zn]
            if prog >= len(steps) + (1 if seq.end == "return" else 0):
                p, d = ok[0]
                end = b if seq.end == "exit" else a
                attempts.append(Attempt(t_start, end, visited, errs, d))
                cur = None
                if seq.overlap:
                    last_done = i
                    i = i0 + 1  # sliding window: the next pattern may start at the following entry
                elif seq.end != "return":
                    i += 1
                # ("return": the re-entry into the first step that completed this one may start the next)
                continue
            cur = [ok, prog, t_start, visited, errs, i0]
            i += 1
            continue
        if zn == visited[-1]:
            i += 1
            continue
        # wrong zone: the attempt fails; the zone may start a new attempt
        fail(cur, visited + [zn], errs + 1)
        cur = None
    if cur is not None:
        fail(cur, cur[3], cur[4])
    return attempts


def sequence_measures(seq: Sequence, attempts: list[Attempt], t0: float, T: float,
                      latency_if_never: str = "duration", distance=None, unit: str = "cm",
                      undefined: float = math.nan) -> "OrderedDict[str, object]":
    """Measures for one sequence (prefixed with its name). distance: optional function (t_a, t_b) -> distance
    travelled between two times, for the distance travelled during each completed sequence (from the entry into
    its first step to its completion) and the mean speed during the sequences. undefined: the mean duration,
    distance and speed without a sequence (ANY-maze's "Use zero as the result for undefined averages": 0)."""
    n = seq.name
    done = [a for a in attempts if a.completed]
    failed = [a for a in attempts if not a.completed]
    durs = np.array([a.duration for a in done], float)
    never = float(T) if latency_if_never == "duration" else math.nan
    out: OrderedDict[str, object] = OrderedDict()

    def r(v, nd=3):
        return round(float(v), nd) if v is not None and math.isfinite(float(v)) else math.nan

    out[f"{n}: completed"] = len(done)
    out[f"{n}: attempts"] = len(attempts)
    out[f"{n}: incomplete"] = len(failed)
    out[f"{n}: errors"] = int(sum(a.errors for a in attempts))
    out[f"{n}: completion (%)"] = r(100 * len(done) / len(attempts) if attempts else math.nan, 2)
    out[f"{n}: latency to first (s)"] = r(done[0].end - t0 if done else never)
    out[f"{n}: latency to start of first (s)"] = r(max(0.0, done[0].start - t0) if done else never)
    out[f"{n}: first duration (s)"] = r(durs[0] if len(durs) else math.nan)
    out[f"{n}: mean duration (s)"] = r(durs.mean() if len(durs) else undefined)
    out[f"{n}: min duration (s)"] = r(durs.min() if len(durs) else math.nan)
    out[f"{n}: max duration (s)"] = r(durs.max() if len(durs) else math.nan)
    gaps = [b.start - a.end for a, b in zip(done, done[1:])]
    out[f"{n}: mean time between (s)"] = r(np.mean(gaps) if gaps else math.nan)
    out[f"{n}: rate (/min)"] = r(len(done) / (T / 60) if T > 0 else math.nan)
    out[f"{n}: total time in sequences (s)"] = r(durs.sum() if len(durs) else 0.0)
    if seq.bidirectional:
        out[f"{n}: completed reversed"] = sum(1 for a in done if a.direction < 0)
    if distance is not None:
        dist = np.array([distance(a.start, a.end) for a in done], float)
        out[f"{n}: total distance in sequences ({unit})"] = r(dist.sum() if len(dist) else 0.0, 2)
        out[f"{n}: mean distance ({unit})"] = r(dist.mean() if len(dist) else undefined, 2)
        out[f"{n}: max distance ({unit})"] = r(dist.max() if len(dist) else math.nan, 2)
        out[f"{n}: min distance ({unit})"] = r(dist.min() if len(dist) else math.nan, 2)
        t_seq = durs.sum() if len(durs) else 0.0
        out[f"{n}: mean speed during sequences ({unit}/s)"] = r(dist.sum() / t_seq if t_seq > 0 else undefined)
    return out
