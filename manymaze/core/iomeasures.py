"""Result measures from a test's I/O log (``Test.io_events``): inputs, outputs, encoders, pellets, pulse trains."""

from __future__ import annotations

import math

import numpy as np


def _label(device: str, channel: str, dup: set) -> str:
    return f"{device}/{channel}" if channel in dup else channel


def io_measures(io_events: list, duration: float, t_range: tuple | None = None, devices: list | None = None) -> dict:
    """ANY-maze-style measures from a test's I/O log (``Test.io_events``).

    io_events: [{"t", "device", "channel", "kind": "input"|"output", "value", "type"?}] where the optional
    "type" is "digital", "analog", "encoder", "pwm", "pulse", "train", "pellet", "shock", "sync", "audio",
    "switch" (virtual switch) or "stimulus" (touch screen).
    duration: test duration (s); t_range: optional (t0, t1) restricting the measures to a time period.
    devices: optional ``Project.io_devices`` (encoder counts_per_rev / cm_per_rev, channel kinds).

    Returns {name: value}; channel labels are the channel name, or "device/channel" when two devices use the
    same channel name. Per digital input: activations, time on (s), latency to first activation (s; the period
    length when never activated), mean activation (s), activations per minute. Per analogue input: mean, min,
    max. Per encoder: counts, revolutions, distance, max rate (counts/s, 1-s windows), mean rate (rev/min).
    Per output / virtual switch / audio channel: times on, time on (s), latency to first on (s); plus pellets
    dispensed, pulse trains and pulses where applicable.
    """
    t0, t1 = (0.0, float(duration)) if t_range is None else (float(t_range[0]), float(t_range[1]))
    T = max(0.0, t1 - t0)
    cfg = {}
    for d in devices or []:
        for c in d.get("channels", []) or []:
            cfg[(d.get("name"), c.get("name"))] = c
    series: dict[tuple, list] = {}
    types: dict[tuple, set] = {}
    for e in sorted(io_events or [], key=lambda e: e.get("t", 0)):
        key = (e.get("kind", "input"), str(e.get("device", "")), str(e.get("channel", "")))
        series.setdefault(key, []).append((float(e.get("t", 0)), float(e.get("value", 0) or 0)))
        types.setdefault(key, set()).add(e.get("type") or "")
    names = [k[2] for k in series]
    dup = {n for n in names if names.count(n) > 1 and len({(k[1]) for k in series if k[2] == n}) > 1}
    res: dict[str, float] = {}

    def r(v, nd=3):
        return round(float(v), nd) if v is not None and math.isfinite(v) else math.nan

    for key in sorted(series, key=lambda k: (k[0] != "input", k[1], k[2])):
        kind, dev, ch = key
        ev = series[key]
        ty = types[key]
        c = cfg.get((dev, ch), {})
        ck = c.get("kind")
        lab = _label(dev, ch, dup)
        if kind == "input" and (ck == "encoder" or "encoder" in ty):
            before = [v for t, v in ev if t <= t0]
            inside = [(t, v) for t, v in ev if t0 < t <= t1]
            start = before[-1] if before else 0.0
            end = inside[-1][1] if inside else start
            counts = end - start
            cpr = float(c.get("counts_per_rev", 0) or 0)
            res[f"{lab}: encoder counts"] = r(counts, 0)
            bins = np.zeros(max(1, int(math.ceil(T))))
            prev = start
            for t, v in inside:
                bins[min(len(bins) - 1, int(t - t0))] += abs(v - prev)
                prev = v
            res[f"{lab}: max rate (counts/s)"] = r(bins.max() if len(bins) else 0)
            if cpr > 0:
                revs = counts / cpr
                res[f"{lab}: revolutions"] = r(revs)
                res[f"{lab}: mean rate (rev/min)"] = r(abs(revs) / (T / 60) if T > 0 else math.nan)
                cm = float(c.get("cm_per_rev", 0) or 0)
                if cm > 0:
                    res[f"{lab}: distance (cm)"] = r(abs(revs) * cm, 2)
            continue
        if kind == "input" and (ck == "analog" or "analog" in ty):
            vals = [(t, v) for t, v in ev]
            prior = [v for t, v in vals if t <= t0]
            cur = prior[-1] if prior else (vals[0][1] if vals else math.nan)
            pts = [(t0, cur)] + [(t, v) for t, v in vals if t0 < t < t1] + [(t1, None)]
            acc, tot = 0.0, 0.0
            seen = []
            for (ta, va), (tb, _) in zip(pts[:-1], pts[1:]):
                if va is None or not math.isfinite(va):
                    continue
                seen.append(va)
                acc += va * (tb - ta)
                tot += tb - ta
            res[f"{lab}: mean"] = r(acc / tot if tot > 0 else (seen[0] if seen else math.nan))
            res[f"{lab}: min"] = r(min(seen) if seen else math.nan)
            res[f"{lab}: max"] = r(max(seen) if seen else math.nan)
            continue
        # digital: input, output, switch, audio, stimulus
        prior = [v for t, v in ev if t < t0]
        state = bool(prior[-1]) if prior else False
        on_since = t0 if state else None
        spans, onsets = [], []
        for t, v in ev:
            if t < t0 or t > t1:
                continue
            if v and not state:
                state, on_since = True, t
                onsets.append(t)
            elif not v and state:
                state = False
                spans.append(t - on_since)
        if state:
            spans.append(t1 - on_since)
        total_on = sum(spans)
        if kind == "input":
            n = len(onsets)
            res[f"{lab}: activations"] = n
            res[f"{lab}: time on (s)"] = r(total_on)
            res[f"{lab}: latency to first activation (s)"] = r(onsets[0] - t0 if onsets else T)
            res[f"{lab}: mean activation (s)"] = r(total_on / len(spans) if spans else 0.0)
            res[f"{lab}: activations per minute"] = r(n / (T / 60) if T > 0 else math.nan)
        else:
            res[f"{lab}: times on"] = len(onsets)
            res[f"{lab}: time on (s)"] = r(total_on)
            res[f"{lab}: latency to first on (s)"] = r(onsets[0] - t0 if onsets else T)
            if "pellet" in ty:
                res[f"{lab}: pellets dispensed"] = len(onsets)
            if "train" in ty:
                trains = [e for e in io_events if e.get("device") == dev and e.get("channel") == ch
                          and e.get("train_start") and t0 <= float(e.get("t", 0)) <= t1]
                res[f"{lab}: pulse trains"] = len(trains)
                res[f"{lab}: pulses"] = len(onsets)
    return res
