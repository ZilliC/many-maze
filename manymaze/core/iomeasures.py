"""Result measures from a test's I/O log (``Test.io_events``): inputs, outputs, encoders, analogue signals, pellets,
pulse trains, shockers (with their intensity), speakers, lights, virtual switches, procedure result variables, the
operant plantar assay (OPAD), movement detectors, sensors, syringe pumps, temperature controllers, odours, liquid
dippers and drippers and the animal's weight.

:func:`io_measures` needs only the log; :func:`io_track_measures` combines it with the track (distance travelled
while a virtual switch is on, analogue values per zone visit) and is called by ``measures.analyse``.
"""

from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger(__name__)

_STR_VALUES = {"on": 1.0, "true": 1.0, "yes": 1.0, "high": 1.0, "off": 0.0, "false": 0.0, "no": 0.0, "low": 0.0}

ENCODER_TURN_GAP_S = 1.0  # an encoder is turning between two samples that differ and are at most this far apart
ENCODER_REVERSAL_DEG = 0.0  # turning back by more than this is a reversal (ANY-maze: any pulse the other way)
ENCODER_IRV_WINDOW_S = 0.2  # instantaneous RPM (as ANY-maze): counts turned over windows of at least this length…
ENCODER_IRV_AVERAGE = 10  # … averaged over this many windows
DEVICE_GROUPS = {"shocker": "Shocker", "speaker": "Speaker", "light": "Light", "dipper": "Dipper",
                 "dripper": "Dripper"}
# derived channels "<channel>.<suffix>" reported by drivers / controllers / the engine; their measures are part of
# their channel's (pump, thermostat, sensor, pellet dispenser)
DERIVED = ("setpoint", "at_target", "running", "stalled", "target_reached", "infused_ml", "withdrawn_ml",
           "out_of_range", "errors", "retries")


def _label(device: str, channel: str, dup: set) -> str:
    return f"{device}/{channel}" if channel in dup else channel


def _r(v, nd=3):
    return round(float(v), nd) if v is not None and math.isfinite(float(v)) else math.nan


def _setting(settings, name, default):
    v = getattr(settings, name, default) if settings is not None else default
    return default if v is None else v


class _Log:
    """The I/O log split into channels: series[(kind, device, channel)] = [(t, value)], their logged types, labels
    and configurations."""

    def __init__(self, io_events, devices=None):
        self.cfg, self.dev_type = {}, {}
        self.end = None  # the end of the test, when known: an event at a period's end counts in it only then
        for d in devices or []:
            self.dev_type[d.get("name")] = d.get("type", "")
            for c in d.get("channels", []) or []:
                self.cfg[(d.get("name"), c.get("name"))] = c
        self.series: dict[tuple, list] = {}
        self.types: dict[tuple, set] = {}
        self.events = list(io_events or [])
        good = []
        for e in self.events:
            try:
                t = float(e.get("t"))
            except (TypeError, ValueError):
                t = math.nan
            v = e.get("value", 0)
            if isinstance(v, str):
                v = _STR_VALUES.get(v.strip().lower(), v)
            try:
                v = float(v or 0)
            except (TypeError, ValueError):
                v = math.nan
            if not (math.isfinite(t) and math.isfinite(v)):
                log.warning("I/O event skipped (bad time or value): %r", e)
                continue
            good.append((t, v, e))
        good.sort(key=lambda g: g[0])
        for t, v, e in good:
            key = (e.get("kind", "input"), str(e.get("device", "")), str(e.get("channel", "")))
            self.series.setdefault(key, []).append((t, v))
            self.types.setdefault(key, set()).add(e.get("type") or "")
        io = [k for k in self.series if k[0] != "variable"]
        names = [k[2] for k in io]
        self.dup = {n for n in names if names.count(n) > 1 and len({k[1] for k in io if k[2] == n}) > 1}

    def label(self, key) -> str:
        return _label(key[1], key[2], self.dup)

    def conf(self, key) -> dict:
        return self.cfg.get((key[1], key[2]), {})

    def kind(self, key) -> str:
        """encoder | analog | sensor | pir | input | weight | output | switch | odour | pump | thermostat | variable |
        derived."""
        kind, ty, ck = key[0], self.types[key], self.conf(key).get("kind")
        if kind == "variable":
            return "variable"
        if "." in key[2] and key[2].rsplit(".", 1)[1] in DERIVED:
            return "derived"
        for special in ("odour", "pump", "thermostat", "weight", "sensor", "pir"):
            if special in ty or ck == special:
                return special
        if kind == "input" and (ck == "encoder" or "encoder" in ty):
            return "encoder"
        if kind == "input" and (ck == "analog" or "analog" in ty):
            return "analog"
        if kind == "input":
            return "input"
        return "switch" if "switch" in ty else "output"

    def group(self, key) -> str | None:
        """The device group of an output: shocker, speaker or light (from the logged type or the channel's
        ``role`` option), else None."""
        role = str(self.conf(key).get("role", "") or "").lower()
        if role in DEVICE_GROUPS:
            return role
        ty = self.types[key]
        if "shock" in ty:
            return "shocker"
        if "audio" in ty or self.dev_type.get(key[1]) == "audio":
            return "speaker"
        if "light" in ty:
            return "light"
        if "dipper" in ty:
            return "dipper"
        if "drop" in ty:
            return "dripper"
        return None

    def derived(self, key, suffix) -> list:
        """The series of a derived channel of this channel ("pump1" -> "pump1.infused_ml")."""
        for k in (("input", key[1], f"{key[2]}.{suffix}"), ("output", key[1], f"{key[2]}.{suffix}")):
            if k in self.series:
                return self.series[k]
        return []

    def find(self, channel: str, kinds=("input",)) -> tuple | None:
        """The series of a channel by name ("channel" or "device/channel")."""
        if not channel:
            return None
        for key in self.series:
            if key[0] in kinds and (key[2] == channel or f"{key[1]}/{key[2]}" == channel):
                return key
        return None


def _within(t, t0, t1, end=None) -> bool:
    """t is in the period [t0, t1): half-open, so that an event exactly on a bin boundary counts in one bin only;
    t == t1 counts only when t1 is the end of the test (end)."""
    return t0 <= t < t1 or (end is not None and t == t1 and t1 >= end - 1e-9)


def _digital(ev, t0, t1, end=None):
    """On spans [(on, off)] inside [t0, t1] (clipped; one carried in at t0 and one still on at t1 included),
    onsets and offsets inside the period [t0, t1) (t1 included when it is the test's end), and whether the
    channel was on at t0."""
    prior = [v for t, v in ev if t < t0]
    state = bool(prior[-1]) if prior else False
    on0 = state
    on_since = t0 if state else None
    spans, onsets, offsets = [], [], []
    for t, v in ev:
        if not _within(t, t0, t1, end):
            continue
        if v and not state:
            state, on_since = True, t
            onsets.append(t)
        elif not v and state:
            state = False
            offsets.append(t)
            spans.append((on_since, t))
    if state:
        spans.append((on_since, t1))
    return spans, onsets, offsets, on0


def _steps(ev, t0, t1):
    """A sample-and-hold signal as segments [(ta, tb, value)] covering [t0, t1] (before the first sample the
    signal has its first value)."""
    if not ev:
        return []
    prior = [v for t, v in ev if t <= t0]
    cur = prior[-1] if prior else ev[0][1]
    pts = [(t0, cur)] + [(t, v) for t, v in ev if t0 < t < t1]
    out = []
    for (ta, va), (tb, _) in zip(pts, pts[1:] + [(t1, None)]):
        if tb > ta and math.isfinite(va):
            out.append((ta, tb, va))
    return out


def _value_at(ev, t):
    prior = [v for tt, v in ev if tt <= t]
    return prior[-1] if prior else (ev[0][1] if ev else math.nan)


def _overlap(spans_a, spans_b) -> float:
    """Total time in both of two lists of intervals."""
    tot = 0.0
    for a0, a1 in spans_a:
        for b0, b1 in spans_b:
            tot += max(0.0, min(a1, b1) - max(a0, b0))
    return tot


def io_measures(io_events: list, duration: float, t_range: tuple | None = None, devices: list | None = None,
                settings=None, test_end: float | None = None, whole: bool | None = None) -> dict:
    """ANY-maze-style measures from a test's I/O log (``Test.io_events``).

    io_events: [{"t", "device", "channel", "kind": "input"|"output"|"variable", "value", "type"?}] where the optional
    "type" is "digital", "analog", "encoder", "pwm", "pulse", "train", "pellet", "shock", "light", "sync", "audio",
    "switch" (virtual switch), "stimulus" (touch screen) or "variable" (a procedure variable's recorded value).
    duration: test duration (s); t_range: optional (t0, t1) restricting the measures to a time period.
    devices: optional ``Project.io_devices`` (encoder counts_per_rev / cm_per_rev, channel kinds, ``role``).
    settings: optional AnalysisSettings (latency_if_never, io_baseline_s, io_deviation_sd, opad_*).
    test_end: the end of the test, so that values recorded at that very moment count in the last period (periods
    are otherwise half-open [t0, t1) for recorded variables). whole: the period is the whole test (default: no
    t_range).

    Returns {name: value}; channel labels are the channel name, or "device/channel" when two devices use the
    same channel name. See the user guide (I/O results) for the measures and their definitions.
    """
    t0, t1 = (0.0, float(duration)) if t_range is None else (float(t_range[0]), float(t_range[1]))
    T = max(0.0, t1 - t0)
    never = T if _setting(settings, "latency_if_never", "duration") == "duration" else math.nan
    end = t1 if test_end is None and t_range is None else (float(test_end) if test_end is not None else None)
    log = _Log(io_events, devices)
    log.end = end
    res: dict[str, object] = {}
    for key in sorted(log.series, key=lambda k: (k[0] != "input", k[0] == "variable", k[1], k[2])):
        kind = log.kind(key)
        ev = log.series[key]
        lab = log.label(key)
        if kind == "variable":
            _variable(res, key[2], ev, t0, t1, end, t_range is None if whole is None else whole)
        elif kind == "derived":
            continue
        elif kind in _SPECIAL:
            _SPECIAL[kind](res, log, key, lab, ev, t0, t1, T, never)
        elif kind == "encoder":
            _encoder(res, lab, ev, log.conf(key), t0, t1, T)
        elif kind == "analog":
            _analog(res, lab, ev, t0, t1, settings, end)
        elif kind == "input":
            spans, onsets, offsets, _ = _digital(ev, t0, t1, end)
            lens = [b - a for a, b in spans]
            total_on = sum(lens)
            n = len(onsets)
            res[f"{lab}: activations"] = n
            res[f"{lab}: time on (s)"] = _r(total_on)
            res[f"{lab}: latency to first activation (s)"] = _r(onsets[0] - t0 if onsets else never)
            res[f"{lab}: mean activation (s)"] = _r(total_on / n if n else 0.0)  # time active / activations
            res[f"{lab}: activations per minute"] = _r(n / (T / 60) if T > 0 else math.nan)
            res[f"{lab}: longest activation (s)"] = _r(max(lens, default=0.0))
            res[f"{lab}: shortest activation (s)"] = _r(min(lens, default=0.0))
            res[f"{lab}: latency to first deactivation (s)"] = _r(offsets[0] - t0 if offsets else never)
            res[f"{lab}: positive reversals"] = n
            res[f"{lab}: negative reversals"] = len(offsets)
        else:
            _output(res, log, key, lab, ev, t0, t1, T, never)
    _index_reversals(res, log, t0, t1)
    _opad(res, log, t0, t1, settings)
    return res


def _index_reversals(res, log, t0, t1):
    """ANY-maze's on/off input reversals: with two or more inputs given an ``index`` option, the order in which
    they are activated rises or falls; a positive reversal is a change from falling to rising, a negative one from
    rising to falling (e.g. a rat running back and forth along a row of beams)."""
    idx = {}
    for key in log.series:
        v = log.conf(key).get("index")
        if key[0] == "input" and log.kind(key) == "input" and v not in (None, ""):
            try:
                idx[key] = float(v)
            except (TypeError, ValueError):
                pass
    if len(idx) < 2:
        return
    acts = sorted((t, idx[key]) for key in idx for t in _digital(log.series[key], -math.inf, math.inf)[1])
    pos = neg = 0
    direction, last = 0, None
    for t, i in acts:
        if last is not None and i != last:
            d = 1 if i > last else -1
            if direction and d != direction and t0 <= t < t1:
                pos, neg = pos + (d > 0), neg + (d < 0)
            direction = d
        last = i
    res["On/off inputs: positive reversals"] = pos
    res["On/off inputs: negative reversals"] = neg


def _output(res, log, key, lab, ev, t0, t1, T, never):
    """Outputs, virtual switches, sounds and touch-screen stimuli; shockers, speakers and lights get measures named
    after their group ("Shocker <channel>: shocks", ...)."""
    spans, onsets, offsets, _ = _digital(ev, t0, t1, log.end)
    lens = [b - a for a, b in spans]
    total_on = sum(lens)
    ty = log.types[key]
    group = log.group(key)
    g, n_name, what = _output_names(log, key, lab)
    res[f"{g}: {n_name}"] = len(onsets)
    res[f"{g}: time on (s)"] = _r(total_on)
    if what:
        res[f"{g}: latency to first {what} (s)"] = _r(onsets[0] - t0 if onsets else never)
        res[f"{g}: longest {what} (s)"] = _r(max(lens, default=0.0))
        res[f"{g}: shortest {what} (s)"] = _r(min(lens, default=0.0))
        res[f"{g}: mean {what} (s)"] = _r(total_on / len(onsets) if onsets else 0.0)
    else:
        res[f"{g}: latency to first on (s)"] = _r(onsets[0] - t0 if onsets else never)
        res[f"{g}: longest on (s)"] = _r(max(lens, default=0.0))
        res[f"{g}: shortest on (s)"] = _r(min(lens, default=0.0))
        res[f"{g}: mean on (s)"] = _r(total_on / len(onsets) if onsets else 0.0)  # time on / times on
    res[f"{g}: latency to first off (s)"] = _r(offsets[0] - t0 if offsets else never)
    res[f"{g}: activations per minute"] = _r(len(onsets) / (T / 60) if T > 0 else math.nan)
    if group == "light" and any(0 < v < 1 for _t, v in ev):
        seg = _steps([(t, min(max(v, 0.0), 1.0)) for t, v in ev], t0, t1)
        tot = sum(b - a for a, b, _ in seg)
        res[f"{g}: mean level"] = _r(sum((b - a) * v for a, b, v in seg) / tot if tot > 0 else math.nan)
    if "pellet" in ty:
        res[f"{g}: pellets dispensed"] = len(onsets)
        res[f"{g}: latency to first pellet (s)"] = _r(onsets[0] - t0 if onsets else never)
        errs = [v for t, v in log.derived(key, "errors") if _within(t, t0, t1, log.end)]
        if errs or log.derived(key, "retries"):
            res[f"{g}: pellets not dispensed (errors)"] = int(sum(errs))
            res[f"{g}: dispenser retries"] = sum(1 for t, _v in log.derived(key, "retries") if _within(t, t0, t1, log.end))
    if group == "dripper" and log.conf(key).get("drop_ul"):
        res[f"{g}: volume (µl)"] = _r(len(onsets) * float(log.conf(key)["drop_ul"]))
    if group == "shocker":
        ich = log.conf(key).get("intensity")
        ma = [(float(e["t"]), float(e["ma"])) for e in log.events
              if e.get("ma") is not None and str(e.get("device")) == key[1] and (not ich or e.get("channel") == ich)]
        if ma:
            vals = [_value_at(sorted(ma), x) for x in onsets]
            vals = [v for v in vals if math.isfinite(v)]
            res[f"{g}: mean intensity (mA)"] = _r(np.mean(vals) if vals else math.nan)
            res[f"{g}: max intensity (mA)"] = _r(max(vals) if vals else math.nan)
    if "train" in ty:
        trains = [e for e in log.events if str(e.get("device")) == key[1] and str(e.get("channel")) == key[2]
                  and e.get("train_start") and _within(float(e.get("t", 0)), t0, t1, log.end)]
        res[f"{g}: pulse trains"] = len(trains)
        res[f"{g}: pulses"] = len(onsets)


def _output_names(log, key, lab) -> tuple[str, str, str | None]:
    """(measure prefix, name of the count, name of one activation or None) of an output: shockers, speakers,
    dippers, drippers and lights are named after their group ("Shocker <channel>", "shocks", "shock")."""
    group = log.group(key)
    if group == "shocker":
        return f"Shocker {lab}", "shocks", "shock"
    if group == "speaker":
        return f"Speaker {lab}", "sounds", "sound"
    if group == "dipper":
        return f"Dipper {lab}", "presentations", "presentation"
    if group == "dripper":
        return f"Dripper {lab}", "drops", "drop"
    return (f"Light {lab}" if group == "light" else lab), "times on", None


def _pir(res, log, key, lab, ev, t0, t1, T, never):
    """A movement detector (PIR): each activation is a movement."""
    spans, onsets, _offsets, _ = _digital(ev, t0, t1, log.end)
    lens = [b - a for a, b in spans]
    g = f"Movement detector {lab}"
    res[f"{g}: movements"] = len(onsets)
    res[f"{g}: time moving (s)"] = _r(sum(lens))
    res[f"{g}: time not moving (s)"] = _r(max(0.0, T - sum(lens)))
    res[f"{g}: latency to first movement (s)"] = _r(onsets[0] - t0 if onsets else never)
    res[f"{g}: mean movement (s)"] = _r(sum(lens) / len(lens) if lens else 0.0)


def _sensor(res, log, key, lab, ev, t0, t1, T, never):
    """A sensor (weight, light, temperature, humidity): its values and the time it spent out of its alert range."""
    c = log.conf(key)
    seg = _steps(ev, t0, t1)
    vals = [v for _, _, v in seg]
    tot = sum(b - a for a, b, _ in seg)
    first, last = (vals[0], vals[-1]) if vals else (math.nan, math.nan)
    g = f"Sensor {lab}"
    res[f"{g}: initial value"] = _r(first)
    res[f"{g}: final value"] = _r(last)
    smp = [v for t, v in ev if _within(t, t0, t1, log.end) and math.isfinite(v)]  # as ANY-maze: the average of the readings
    res[f"{g}: mean"] = _r(sum(smp) / len(smp) if smp else first)
    res[f"{g}: max"] = _r(max(vals) if vals else math.nan)
    res[f"{g}: min"] = _r(min(vals) if vals else math.nan)
    res[f"{g}: change"] = _r(last - first)
    if c.get("sensor") == "weight":  # food / liquid intake: what the container lost
        res[f"{g}: intake"] = _r(max(0.0, first - last) if vals else math.nan)
    oor = log.derived(key, "out_of_range")
    if oor or c.get("alert_min") not in (None, "") or c.get("alert_max") not in (None, ""):
        spans, onsets, _, _ = _digital(oor, t0, t1, log.end)
        res[f"{g}: time out of range (s)"] = _r(sum(b - a for a, b in spans))
        res[f"{g}: times out of range"] = len(onsets)


def _pump(res, log, key, lab, ev, t0, t1, T, never):
    """A syringe pump: the commands (infuse / withdraw at a rate, up to a volume) and, when the pump reports them,
    the volumes it delivered; without reports the volumes are computed from the rates and times."""
    cmds = sorted(((float(e["t"]), e) for e in log.events
                   if e.get("type") == "pump" and str(e.get("device")) == key[1] and str(e.get("channel")) == key[2]),
                  key=lambda te: te[0])  # stable: same-time commands keep their logged order
    runs = []  # (start, end, direction, rate, volume target)
    cur = None
    for t, e in cmds:
        if cur is not None:
            runs.append((cur[0], t, *cur[1:]))
            cur = None
        if e.get("value"):
            cur = (t, e.get("direction", "infuse"), float(e.get("rate", 0) or 0), float(e.get("volume", 0) or 0))
    if cur is not None:
        runs.append((cur[0], math.inf, *cur[1:]))
    g = f"Pump {lab}"
    for direction, word in (("infuse", "infused"), ("withdraw", "withdrawn")):
        rep = log.derived(key, f"{word}_ml")
        if rep:  # counters reported by the pump, with their values when each command was given as baselines
            base = [(t, 0, float(e[f"{word}_ml"])) for t, e in cmds if e.get(f"{word}_ml") is not None]
            pts = [(t, v) for t, _o, v in sorted(base + [(t, 1, v) for t, v in rep])]  # baselines first
            before = [v for t, v in pts if t < t0]
            vol = _value_at(pts, t1) - (before[-1] if before else pts[0][1])
        else:
            vol = 0.0
            for a, b, d, rate, target in runs:
                if d != direction:
                    continue
                end = min(b, a + target / rate * 60.0) if target > 0 and rate > 0 else b
                vol += rate / 60.0 * max(0.0, min(end, t1) - max(a, t0))
        res[f"{g}: volume {word} (ml)"] = _r(vol, 4)
        mine = [r for r in runs if r[2] == direction and r[0] < t1 and r[1] > t0]
        res[f"{g}: {'infusions' if direction == 'infuse' else 'withdrawals'}"] = sum(1 for r in mine if r[0] >= t0)
    running = log.derived(key, "running")
    if running:
        spans = _digital(running, t0, t1, log.end)[0]
    else:
        spans = [(max(a, t0), min(b if b != math.inf else t1, t1,
                                   a + target / rate * 60.0 if target > 0 and rate > 0 else math.inf))
                 for a, b, _d, rate, target in runs]
    res[f"{g}: time pumping (s)"] = _r(sum(max(0.0, b - a) for a, b in spans))
    starts = [r[0] for r in runs if _within(r[0], t0, t1, log.end)]
    res[f"{g}: latency to first start (s)"] = _r(starts[0] - t0 if starts else never)
    res[f"{g}: stalls"] = len(_digital(log.derived(key, "stalled"), t0, t1, log.end)[1])


def _thermostat(res, log, key, lab, ev, t0, t1, T, never):
    """A temperature controller: its targets and the time the temperature was at the target."""
    on = [(a, b, v) for a, b, v in _steps(ev, t0, t1) if v]
    tot = sum(b - a for a, b, _ in on)
    g = f"Temperature controller {lab}"
    res[f"{g}: time on (s)"] = _r(tot)
    res[f"{g}: mean target"] = _r(sum((b - a) * v for a, b, v in on) / tot if tot > 0 else math.nan, 2)
    at = log.derived(key, "at_target")
    spans, onsets, _, _ = _digital(at, t0, t1, log.end)
    res[f"{g}: time at target (s)"] = _r(sum(b - a for a, b in spans))
    starts = [t for t, v in ev if v and _within(t, t0, t1, log.end)]
    first = next((x for x in onsets if starts and x >= starts[0]), None)
    res[f"{g}: latency to target (s)"] = _r(first - starts[0] if first is not None else never)
    sp = log.derived(key, "setpoint")
    if sp:
        seg = [(a, b, v) for a, b, v in _steps(sp, t0, t1) if v]
        st = sum(b - a for a, b, _ in seg)
        res[f"{g}: mean set-point"] = _r(sum((b - a) * v for a, b, v in seg) / st if st > 0 else math.nan, 2)


def _odour(res, log, key, lab, ev, t0, t1, T, never):
    """An olfactometer: presentations of each odour (the odour's name is logged with each change)."""
    per: dict[str, list] = {}
    for e in sorted((e for e in log.events if e.get("type") == "odour" and str(e.get("device")) == key[1]
                     and str(e.get("channel")) == key[2]), key=lambda e: e.get("t", 0)):
        per.setdefault(str(e.get("odour", "?")), []).append((float(e["t"]), float(e.get("value", 0) or 0)))
    tot_all = 0.0
    for name, s in sorted(per.items()):
        spans, onsets, _, _ = _digital(s, t0, t1, log.end)
        tot = sum(b - a for a, b in spans)
        tot_all += tot
        g = f"Odour {name}"
        res[f"{g}: presentations"] = len(onsets)
        res[f"{g}: time presented (s)"] = _r(tot)
        res[f"{g}: latency to first presentation (s)"] = _r(onsets[0] - t0 if onsets else never)
    res[f"Olfactometer {lab}: time with an odour (s)"] = _r(tot_all)


def _weight(res, log, key, lab, ev, t0, t1, T, never):
    vals = [v for t, v in ev if _within(t, t0, t1, log.end)]
    res["Animal weight (g)"] = _r(vals[-1] if vals else math.nan, 2)


_SPECIAL = {"pir": _pir, "sensor": _sensor, "pump": _pump, "thermostat": _thermostat, "odour": _odour,
            "weight": _weight}


def _variable(res, name, ev, t0, t1, end, whole=False):
    """A procedure variable recorded during the test (every change / every time it is set). As ANY-maze, a
    variable never recorded in the whole test has a max / min of 0 (variables start at 0); in a period, blank."""
    vals = [v for t, v in ev if _within(t, t0, t1, end)]
    p = f"Variable: {name}"
    none = 0.0 if whole else math.nan
    res[f"{p} (count)"] = len(vals)
    res[f"{p} (mean)"] = _r(np.mean(vals) if vals else math.nan)
    res[f"{p} (max)"] = _r(max(vals) if vals else none)
    res[f"{p} (min)"] = _r(min(vals) if vals else none)
    res[f"{p} (sum)"] = _r(sum(vals) if vals else 0.0)
    res[f"{p} (values)"] = ", ".join(f"{v:g}" for v in vals)


def _encoder(res, lab, ev, c, t0, t1, T):
    """Rotary encoder: counts (positive = clockwise), revolutions, rates, turning, rotations and reversals."""
    before = [v for t, v in ev if t <= t0]
    inside = [(t, v) for t, v in ev if t0 < t <= t1]
    start = before[-1] if before else 0.0
    end = inside[-1][1] if inside else start
    counts = end - start
    cpr = float(c.get("counts_per_rev", 0) or 0)
    res[f"{lab}: encoder counts"] = _r(counts, 0)
    bins = np.zeros(max(1, int(math.ceil(T))))
    prev = start
    t_prev = max((t for t, _ in ev if t <= t0), default=-math.inf)
    # turning: between two samples that differ and are at most ENCODER_TURN_GAP_S apart; a change after a longer
    # still spell counts from one typical sample interval before it
    gaps = np.diff([t for t, _ in ev])
    gaps = gaps[(gaps > 0) & (gaps <= ENCODER_TURN_GAP_S)]
    typical = float(np.median(gaps)) if len(gaps) else ENCODER_TURN_GAP_S
    turning = []  # intervals during which the encoder turned
    deltas = []
    for t, v in inside:
        bins[min(len(bins) - 1, int(t - t0))] += abs(v - prev)
        if v != prev:
            deltas.append(v - prev)
            ta = max(t0, t_prev if t - t_prev <= ENCODER_TURN_GAP_S else t - typical)
            if turning and ta <= turning[-1][1]:
                turning[-1][1] = t
            else:
                turning.append([ta, t])
        prev, t_prev = v, t
    res[f"{lab}: max rate (counts/s)"] = _r(bins.max() if len(bins) else 0)
    t_turn = sum(b - a for a, b in turning)
    res[f"{lab}: time turning (s)"] = _r(t_turn)
    # reversals (as ANY-maze): a count in one direction followed by one in the other; each unbroken run in one
    # direction contributes its completed rotations
    runs_cw, runs_acw, reversals = _encoder_runs(deltas, cpr)
    res[f"{lab}: reversals"] = reversals
    if cpr > 0:
        revs = counts / cpr
        res[f"{lab}: revolutions"] = _r(revs)  # net, clockwise positive
        d = np.asarray(deltas, float)
        # ANY-maze's average RPM: the rotational velocity in either direction (turning back does not cancel it)
        res[f"{lab}: mean rate (rev/min)"] = _r(np.abs(d).sum() / cpr / (T / 60) if T > 0 else math.nan)
        res[f"{lab}: degrees clockwise"] = _r(d[d > 0].sum() * 360 / cpr, 1)
        res[f"{lab}: degrees anticlockwise"] = _r(-d[d < 0].sum() * 360 / cpr, 1)
        eps = 1e-9
        n_cw = int(sum(math.floor(x / cpr + eps) for x in runs_cw))
        n_acw = int(sum(math.floor(x / cpr + eps) for x in runs_acw))
        res[f"{lab}: clockwise rotations"] = n_cw
        res[f"{lab}: anticlockwise rotations"] = n_acw
        res[f"{lab}: total rotations"] = n_cw + n_acw  # ANY-maze's number of rotations (either direction)
        cm = float(c.get("cm_per_rev", 0) or 0)
        if cm > 0:  # as ANY-maze: the number of rotations × the wheel's circumference
            res[f"{lab}: distance (cm)"] = _r((n_cw + n_acw) * cm, 2)
        # as ANY-maze, a half / quarter rotation is an unbroken run of counts per revolution / 2 (/ 4) counts,
        # rounded down
        half, quarter = max(1, int(cpr) // 2), max(1, int(cpr) // 4)
        res[f"{lab}: half rotations"] = int(sum(math.floor(x / half + eps) for x in runs_cw + runs_acw))
        res[f"{lab}: quarter rotations"] = int(sum(math.floor(x / quarter + eps) for x in runs_cw + runs_acw))
        # minimum RPM: the slowest 1-s window of the period during which the encoder turned throughout (a change
        # logged at t happened during the sample interval ending at t)
        nb = max(1, int(math.ceil(T - 1e-9)))
        fb = np.zeros(nb)
        for (t, v), pv in zip(inside, [start] + [v for _, v in inside[:-1]]):
            fb[min(nb - 1, max(0, int(math.ceil(t - t0 - 1e-9)) - 1))] += abs(v - pv)
        full = [i for i in range(nb) if t0 + i + 1 <= t1 + 1e-9
                and any(a <= t0 + i + 1e-9 and b >= t0 + i + 1 - 1e-9 for a, b in turning)]
        moving = fb[full] if full else fb[fb > 0]
        res[f"{lab}: min rate while turning (rev/min)"] = _r(moving.min() / cpr * 60 if len(moving) else math.nan)
        irv = _encoder_irv(ev, t0, t1, cpr)
        res[f"{lab}: max rate (rev/min)"] = _r(max(v for _, v in irv) if irv else 0.0)
        # ANY-maze's minimum RPM: the lowest instantaneous velocity, 0 if the encoder stopped in the period
        still = t_turn < T - 1e-6
        res[f"{lab}: min rate (rev/min)"] = _r(0.0 if still or not irv else min(v for _, v in irv))
        res[f"{lab}: mean rate while turning (rev/min)"] = _r(np.abs(d).sum() / cpr / (t_turn / 60) if t_turn > 0
                                                             else math.nan)


def _encoder_runs(deltas, cpr: float) -> tuple[list, list, int]:
    """Runs of an encoder in one direction: (counts turned in each clockwise run, in each anticlockwise run, number
    of reversals). The direction changes after turning back by more than ENCODER_REVERSAL_DEG (any change without a
    counts per revolution)."""
    hyst = cpr * ENCODER_REVERSAL_DEG / 360 if cpr > 0 else 0.0
    runs_cw, runs_acw = [], []
    pos, ref, ext, direction, reversals = 0.0, 0.0, 0.0, 0, 0
    for dv in deltas:
        pos += dv
        if direction >= 0 and pos > ext:
            ext = pos
            direction = direction or 1
        elif direction <= 0 and pos < ext:
            ext = pos
            direction = direction or -1
        if direction > 0 and ext - pos > hyst:
            runs_cw.append(ext - ref)
            ref, ext, direction, reversals = ext, pos, -1, reversals + 1
        elif direction < 0 and pos - ext > hyst:
            runs_acw.append(ref - ext)
            ref, ext, direction, reversals = ext, pos, 1, reversals + 1
    if direction > 0:
        runs_cw.append(ext - ref)
    elif direction < 0:
        runs_acw.append(ref - ext)
    return runs_cw, runs_acw, reversals


def _encoder_irv(ev, t0, t1, cpr: float) -> list[tuple[float, float]]:
    """Instantaneous rotational velocity of an encoder (rev/min) as ANY-maze computes it, at the end of each
    window: a window starts with a change of the count and takes the following changes in the same direction until
    at least ENCODER_IRV_WINDOW_S has elapsed (the counts turned / the time); each value is the moving average of
    the last ENCODER_IRV_AVERAGE windows. A change logged at t happened during the sample interval ending at t (or
    one typical interval, after a still spell)."""
    gaps = np.diff([t for t, _ in ev])
    gaps = gaps[(gaps > 0) & (gaps <= ENCODER_TURN_GAP_S)]
    typical = float(np.median(gaps)) if len(gaps) else ENCODER_TURN_GAP_S
    raw = []  # (end time, rev/min)
    start, counts, sign = None, 0.0, 0
    prev_t, prev_v = None, None
    for t, v in ev:
        if prev_v is not None and t0 < t <= t1 and v != prev_v:
            dv = v - prev_v
            if start is not None and (np.sign(dv) != sign or t - prev_t > ENCODER_TURN_GAP_S):
                start = None  # the direction changed, or the encoder stopped: the window ends with its last change
            if start is None:
                start = max(t0, prev_t if t - prev_t <= ENCODER_TURN_GAP_S else t - typical)
                counts, sign = 0.0, np.sign(dv)
            counts += abs(dv)
            if t - start >= ENCODER_IRV_WINDOW_S - 1e-9:
                raw.append((t, counts / cpr / (t - start) * 60))
                start, counts = t, 0.0
        prev_t, prev_v = t, v
    out = []
    for i, (t, _v) in enumerate(raw):
        w = [v for _, v in raw[max(0, i - ENCODER_IRV_AVERAGE + 1):i + 1]]
        out.append((t, sum(w) / len(w)))
    return out


def _analog(res, lab, ev, t0, t1, settings, end=None):
    """Analogue signal: mean, min, max and their times, baseline and deviations from it. As ANY-maze, the mean,
    min, max and baseline are over the samples (their simple average); a period without a sample has the value
    held from before it."""
    seg = _steps(ev, t0, t1)
    smp = [(t, v) for t, v in ev if _within(t, t0, t1, end) and math.isfinite(v)] or [(a, v) for a, _, v in seg[:1]]
    vals = [v for _, v in smp]
    res[f"{lab}: mean"] = _r(sum(vals) / len(vals) if vals else math.nan)
    res[f"{lab}: min"] = _r(min(vals) if vals else math.nan)
    res[f"{lab}: max"] = _r(max(vals) if vals else math.nan)
    if not seg:
        return
    res[f"{lab}: time of max (s)"] = _r(next(t for t, v in smp if v == max(vals)) - t0)
    res[f"{lab}: time of min (s)"] = _r(next(t for t, v in smp if v == min(vals)) - t0)
    base_s = float(_setting(settings, "io_baseline_s", 10.0))
    k_sd = float(_setting(settings, "io_deviation_sd", 2.0))
    if base_s <= 0:
        return
    tb = min(t1, t0 + base_s)
    bvals = [v for t, v in smp if t < tb]  # the samples in the baseline period
    if not bvals:
        return
    base = sum(bvals) / len(bvals)
    sd = math.sqrt(sum((v - base) ** 2 for v in bvals) / len(bvals))
    res[f"{lab}: baseline"] = _r(base)
    res[f"{lab}: baseline SD"] = _r(sd)
    res[f"{lab}: end of baseline (s)"] = _r(tb - t0)
    after = [(max(a, tb), b, v) for a, b, v in seg if b > tb]
    at = sum(b - a for a, b, _ in after)
    thr = k_sd * sd
    res[f"{lab}: mean deviation from baseline"] = _r(sum((b - a) * abs(v - base) for a, b, v in after) / at
                                                     if at > 0 else math.nan)
    res[f"{lab}: integral above baseline"] = _r(sum((b - a) * max(0.0, v - base) for a, b, v in after))
    res[f"{lab}: integral below baseline"] = _r(sum((b - a) * max(0.0, base - v) for a, b, v in after))
    for sign, word in ((1, "positive"), (-1, "negative")):
        dev = next((i for i, (a, b, v) in enumerate(after) if sign * (v - base) > thr), None)
        back = None
        if dev is not None:
            back = next((a for a, b, v in after[dev + 1:] if abs(v - base) <= thr), None)
        res[f"{lab}: first {word} deviation (s)"] = _r(after[dev][0] - t0 if dev is not None else math.nan)
        res[f"{lab}: return to baseline after {word} deviation (s)"] = _r(back - t0 if back is not None else math.nan)


def _opad(res, log, t0, t1, settings):
    """Operant plantar assay: paw contacts with the thermal plate (a digital input), licks (a digital input) and the
    plate temperature (an analogue input), overall and per temperature of interest."""
    ck = log.find(str(_setting(settings, "opad_contact", "") or ""))
    if ck is None:
        return
    lk = log.find(str(_setting(settings, "opad_lick", "") or ""))
    tk = log.find(str(_setting(settings, "opad_temperature", "") or ""))
    spans, made, broken, _ = _digital(log.series[ck], t0, t1, log.end)
    licks = _digital(log.series[lk], t0, t1, log.end)[1] if lk is not None else []
    res["OPAD: contacts made"] = len(made)
    res["OPAD: contacts broken"] = len(broken)
    res["OPAD: time in contact (s)"] = _r(sum(b - a for a, b in spans))
    res["OPAD: licks"] = len(licks)
    res["OPAD: non-lick contacts"] = sum(1 for a, b in spans if not any(a <= x < b for x in licks))
    if tk is None:
        return
    tev = log.series[tk]
    temps = [_value_at(tev, x) for x in broken]
    res["OPAD: mean temperature when contact broken"] = _r(np.mean(temps) if temps else math.nan, 2)
    res["OPAD: temperatures when contact broken"] = ", ".join(f"{v:g}" for v in temps)
    tol = abs(float(_setting(settings, "opad_tolerance", 1.0)))
    for target in parse_numbers(str(_setting(settings, "opad_temperatures", "") or "")):
        def near(x, target=target):
            return abs(x - target) <= tol + 1e-9

        at = [(a, b) for a, b, v in _steps(tev, t0, t1) if near(v)]
        g = f"OPAD at {target:g}°"
        res[f"{g}: time in contact (s)"] = _r(_overlap(spans, at))
        res[f"{g}: contacts made"] = sum(1 for x in made if near(_value_at(tev, x)))
        res[f"{g}: contacts broken"] = sum(1 for x in broken if near(_value_at(tev, x)))
        res[f"{g}: licks"] = sum(1 for x in licks if near(_value_at(tev, x)))


def parse_numbers(text: str) -> list[float]:
    """Numbers in a comma / space separated list ("10, 45" -> [10.0, 45.0]); other words are ignored."""
    out = []
    for part in text.replace(";", ",").replace(",", " ").split():
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


def io_track_measures(io_events: list, t: np.ndarray, dur: np.ndarray, step: np.ndarray, t_range: tuple,
                      unit: str = "cm", visits: dict | None = None, devices: list | None = None,
                      latency_if_never: str = "duration", zones: dict | None = None) -> dict:
    """I/O measures that need the track, for frames t (durations dur, distance travelled into each frame step) of a
    period t_range = (t0, t1):

    * virtual switches — distance travelled before the first activation and while the switch is on;
    * analogue inputs, per zone (visits: {zone: [(start, end) frame indices of each entry in the period]}) — mean of
      the maximum and minimum of each visit and of the time from the entry to them, mean value at entry and exit;
    * inputs, outputs (shockers, speakers, lights, lasers, pellet dispensers…), virtual switches and rotary
      encoders, per zone (zones: {zone: per-frame bool, in the zone in the period}) — see _zone_device.
    """
    res: dict[str, object] = {}
    n = len(t)
    if n == 0:
        return res
    t0, _t1 = float(t_range[0]), float(t_range[1])
    log = _Log(io_events, devices)
    total = float(step[1:].sum()) if n > 1 else 0.0
    for key in sorted(log.series, key=lambda k: (k[1], k[2])):
        kind = log.kind(key)
        ev = log.series[key]
        lab = log.label(key)
        if kind == "switch":
            ts = np.asarray([x for x, _ in ev])
            vs = np.asarray([v for _, v in ev])
            j = np.searchsorted(ts, t, "right") - 1
            on = np.where(j >= 0, vs[np.clip(j, 0, len(vs) - 1)] != 0, False)
            first = np.flatnonzero(on & ~np.concatenate([[on[0] and _was_on(ev, t0)], on[:-1]]))
            res[f"{lab}: distance while active ({unit})"] = _r(step[on].sum(), 2)
            if len(first):
                f = int(first[0])
                res[f"{lab}: distance before first activation ({unit})"] = _r(step[1:f + 1].sum() if f > 0 else 0.0,
                                                                              2)
            else:
                res[f"{lab}: distance before first activation ({unit})"] = _r(
                    total if latency_if_never == "duration" else math.nan, 2)
        elif kind == "analog" and visits:
            ts = np.asarray([x for x, _ in ev])
            vs = np.asarray([v for _, v in ev])
            j = np.clip(np.searchsorted(ts, t, "right") - 1, 0, len(vs) - 1)
            val = vs[j]
            for zn, vv in visits.items():
                if not vv:
                    continue
                mx, mn, tmx, tmn, ent, ext = [], [], [], [], [], []
                for a, b in vv:
                    seg = val[a:b]
                    ia, ib = int(np.argmax(seg)), int(np.argmin(seg))
                    mx.append(seg[ia])
                    mn.append(seg[ib])
                    tmx.append(t[a + ia] - t[a])
                    tmn.append(t[a + ib] - t[a])
                    ent.append(seg[0])
                    ext.append(seg[-1])
                g = f"{lab} in {zn}"
                res[f"{g}: mean max"] = _r(np.mean(mx))
                res[f"{g}: mean time to max (s)"] = _r(np.mean(tmx))
                res[f"{g}: mean min"] = _r(np.mean(mn))
                res[f"{g}: mean time to min (s)"] = _r(np.mean(tmn))
                res[f"{g}: mean at entry"] = _r(np.mean(ent))
                res[f"{g}: mean at exit"] = _r(np.mean(ext))
        if zones and kind in ("input", "output", "switch", "encoder", "analog", "sensor", "variable"):
            _zone_device(res, log, key, lab, kind, ev, t, dur, t_range, zones, latency_if_never, step, unit)
    return res


def _zone_device(res, log, key, lab, kind, ev, t, dur, t_range, zones, latency_if_never, step=None, unit="cm"):
    """A device's activity per zone, as ANY-maze: the activations that start while the animal is in the zone
    (their count, latency, and their rate per minute spent in the zone), the latency to the first deactivation
    in the zone, the time the channel is on while the animal is in the zone and the longest / shortest stretch
    of it; pellets dispensed in the zone and the latency to the first; the distance travelled in the zone while a
    virtual switch is on; for a rotary encoder the counts turned while the animal is in the zone and, with counts
    per revolution, the degrees each way, the rotations made entirely in it, the distance (wheels) and the maximum
    RPM in it. Measures are named "<channel> in <zone>: …" (groups as in io_measures: "Shocker <channel> in
    <zone>: shocks", …)."""
    t0, t1 = float(t_range[0]), float(t_range[1])
    T = max(0.0, t1 - t0)
    never = T if latency_if_never == "duration" else math.nan
    n = len(t)

    def frame(x):
        """The frame of time x, or -1 outside the period's frames."""
        j = int(np.searchsorted(t, x, "right")) - 1
        return j if 0 <= j < n and t0 <= x <= t1 else -1

    if kind in ("analog", "sensor", "variable"):
        # the values recorded while the animal was in the zone (ANY-maze 9.1-9.3, 10.2-10.4, 20.2-20.7)
        for zn, m in zones.items():
            vz = [v for x, v in ev if (f := frame(x)) >= 0 and m[f] and math.isfinite(v)
                  and (kind != "variable" or x < t1)]
            if kind == "variable":
                g = f"Variable: {key[2]} in {zn}"
                res[f"{g} (count)"] = len(vz)
                res[f"{g} (mean)"] = _r(sum(vz) / len(vz) if vz else math.nan)
                res[f"{g} (max)"] = _r(max(vz) if vz else math.nan)
                res[f"{g} (min)"] = _r(min(vz) if vz else math.nan)
                res[f"{g} (sum)"] = _r(sum(vz))
                res[f"{g} (values)"] = ", ".join(f"{v:g}" for v in vz)
                continue
            g = f"Sensor {lab} in {zn}" if kind == "sensor" else f"{lab} in {zn}"
            res[f"{g}: mean"] = _r(sum(vz) / len(vz) if vz else math.nan)
            res[f"{g}: max"] = _r(max(vz) if vz else math.nan)
            res[f"{g}: min"] = _r(min(vz) if vz else math.nan)
        return
    if kind == "encoder":
        cpr = float(log.conf(key).get("counts_per_rev", 0) or 0)
        start = [v for x, v in ev if x <= t0]
        prev = start[-1] if start else 0.0
        moved = []  # (frame, change) of each change
        for x, v in ev:
            if x > t0 and v != prev:
                moved.append((frame(x), v - prev))
            prev = v
        irv = [(frame(x), v) for x, v in _encoder_irv(ev, t0, t1, cpr)] if cpr > 0 else []
        for zn, m in zones.items():
            inz = [j >= 0 and bool(m[j]) for j, _ in moved]
            res[f"{lab} in {zn}: encoder counts"] = _r(sum(abs(d) for (_, d), i in zip(moved, inz) if i), 0)
            if cpr > 0:
                res[f"{lab} in {zn}: degrees clockwise"] = _r(sum(d for (_, d), i in zip(moved, inz) if i and d > 0)
                                                             * 360 / cpr, 1)
                res[f"{lab} in {zn}: degrees anticlockwise"] = _r(-sum(d for (_, d), i in zip(moved, inz)
                                                                       if i and d < 0) * 360 / cpr, 1)
                # a rotation counts in the zone when the animal was in it for the whole rotation: the runs are found
                # in each stretch of changes made while it was in the zone
                n_rot, seg = 0, []
                for (_, d), i in zip(moved + [(-1, 0.0)], inz + [False]):
                    if i:
                        seg.append(d)
                    elif seg:
                        r_cw, r_acw, _ = _encoder_runs(seg, cpr)
                        n_rot += int(sum(math.floor(x / cpr + 1e-9) for x in r_cw + r_acw))
                        seg = []
                res[f"{lab} in {zn}: total rotations"] = n_rot
                cm = float(log.conf(key).get("cm_per_rev", 0) or 0)
                if cm > 0:
                    res[f"{lab} in {zn}: distance (cm)"] = _r(n_rot * cm, 2)
                vz = [v for j, v in irv if j >= 0 and m[j]]
                res[f"{lab} in {zn}: max rate (rev/min)"] = _r(max(vz) if vz else 0.0)
        return
    if kind == "input":
        g, n_name, what = lab, "activations", "activation"
    else:
        g, n_name, what = _output_names(log, key, lab)
    _, onsets, offsets, _ = _digital(ev, t0, t1)
    ts = np.asarray([x for x, _ in ev])
    vs = np.asarray([v for _, v in ev])
    j = np.searchsorted(ts, t, "right") - 1
    on = np.where(j >= 0, vs[np.clip(j, 0, len(vs) - 1)] != 0, False)
    pellets = "pellet" in log.types[key]
    for zn, m in zones.items():
        zo = [x for x in onsets if (f := frame(x)) >= 0 and m[f]]
        p = f"{g} in {zn}"
        res[f"{p}: {n_name}"] = len(zo)
        res[f"{p}: time on (s)"] = _r(dur[on & m].sum())
        res[f"{p}: latency to first {what or 'on'} (s)"] = _r(zo[0] - t0 if zo else never)
        tz = float(dur[m].sum())  # as ANY-maze: the activations in the zone per minute spent in the zone
        res[f"{p}: activations per minute"] = _r(len(zo) / (tz / 60) if tz > 0 else math.nan)
        zoff = [x for x in offsets if (f := frame(x)) >= 0 and m[f]]
        res[f"{p}: latency to first {'deactivation' if kind == 'input' else 'off'} (s)"] = _r(zoff[0] - t0 if zoff
                                                                                           else never)
        bouts = [float(dur[a:b].sum()) for a, b in _runs(on & m)]  # on while in the zone, continuously
        res[f"{p}: longest {what or 'on'} (s)"] = _r(max(bouts, default=0.0))
        res[f"{p}: shortest {what or 'on'} (s)"] = _r(min(bouts, default=0.0))
        if pellets:
            res[f"{p}: pellets dispensed"] = len(zo)
            res[f"{p}: latency to first pellet (s)"] = _r(zo[0] - t0 if zo else never)
        if kind == "switch" and step is not None:
            res[f"{p}: distance while active ({unit})"] = _r(step[on & m].sum(), 2)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of True."""
    m = np.r_[False, np.asarray(mask, bool), False]
    d = np.flatnonzero(np.diff(m.astype(np.int8)))
    return list(zip(d[::2], d[1::2]))


def _was_on(ev, t0) -> bool:
    prior = [v for t, v in ev if t < t0]
    return bool(prior[-1]) if prior else False
