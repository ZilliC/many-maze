"""The actions of the procedure engine ("do" statements): outputs, pulse trains, shocks, audio, communication,
variables, timers, schedules, test control and the touch screen; lights, optogenetics, odours, liquid delivery,
syringe pumps, temperature controllers, sensors, balances and alerts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import ioconfig
from ..operant import Schedule
from .catalog import ACTION_SPECS, EPS, SHOCK_MAX_S, STALL_S
from .expr import MAX_SEQ, ExprError
from .model import _params_text


class _Stop(Exception):
    pass


class _Break(Exception):
    pass


@dataclass
class _Train:
    """A running pulse train (or single pulse): pulse k is on at t0 + k * period for `width` seconds."""

    t0: float
    period: float
    width: float
    n: int  # number of pulses (0 = until stopped)
    hw: bool  # timed by the device itself
    typ: str  # I/O log type
    k: int = 0
    on: bool = False
    first: bool = True
    delayed: float = 0.0  # total delay after stalled frames
    pulses: list | None = None  # explicit (onset from t0, width) of each pulse (pulse sequences)
    levels: list | None = None  # intensity of each pulse of a sequence (or None)

    def on_time(self, k):
        return self.t0 + (self.pulses[k][0] if self.pulses is not None else k * self.period)

    def width_of(self, k):
        return self.pulses[k][1] if self.pulses is not None else self.width


@dataclass
class _Ramp:
    """An output level changing linearly from v0 at t0 to v1 at t0 + duration."""

    t0: float
    v0: float
    v1: float
    duration: float
    typ: str


@dataclass
class _PelletCheck:
    """Pellets dispensed with a sensor: the pellets the sensor has not seen yet are dispensed again."""

    dev: str
    ch: str
    sensor: tuple
    want: int
    timeout: float
    retries: int
    pulse_width: float
    gap: float
    seen: int = 0
    tries: list = field(default_factory=list)


@dataclass
class _Timer:
    acc: float = 0.0  # time accumulated before the current run
    start: float | None = None  # start of the current run (None: stopped)
    due: float | None = None  # when it elapses


# actions that are an output switched on / off under a name of its own
_ALIASES = {"door_open": "output_on", "door_close": "output_off", "lever_extend": "output_on",
            "lever_retract": "output_off"}


class Actions:
    """The ``do`` statements of :class:`ProcedureEngine` (a mixin: the engine provides the state and helpers)."""

    def _st_do(self, th, st, p):
        name = st.get("action")
        spec = ACTION_SPECS.get(name)
        if spec is None:
            self._error(th, p, f"unknown action '{name}'")
            return
        args = {}
        for prm in spec["params"]:
            v = st.get(prm["name"], prm["default"])
            typ = prm["type"]
            if typ in ("number", "int") and prm["default"] is None and (v is None or str(v).strip() == ""):
                v = None  # optional, not given
            elif typ in ("number", "int"):
                d = prm["default"] if isinstance(prm["default"], (int, float)) else 0
                v = self._num(th, v, p, prm["label"], default=d)
                if typ == "int":
                    v = int(round(v))
            elif typ == "expr":
                try:
                    v = self._eval(th, v)
                except ExprError as e:
                    self._error(th, p, f"{prm['label']}: {e}")
                    return
            elif typ == "text":
                v = self._text(th, v if v is not None else "", p)
            elif typ == "bool":
                v = bool(v) and str(v).strip().lower() not in ("false", "0", "no", "")
            else:
                v = "" if v is None else str(v).strip()
            args[prm["name"]] = v
        self.fired.append((round(self.t, 3), th.label, name, _params_text(spec, st)))
        try:
            getattr(self, "_a_" + _ALIASES.get(name, name))(th, p, **args)
        except (_Stop, _Break):
            raise
        except Exception as e:  # never crash the test (ExprError: a bad parameter value)
            self._error(th, p, f"{spec['label']}: {e}")

    # -- outputs
    def _resolve(self, th, p, device, channel, kinds=("output", "pwm")):
        dev = self._devname(device) or self.devices.find_channel(channel, kinds) or ""
        if not dev:
            dev = "virtual"
        if self.devices.configured and not self.devices.has(dev):
            self._error(th, p, f"device '{dev}' is not configured — simulated")
        return dev, channel

    def _set_out(self, dev, ch, value, t, typ="digital", hw=False, max_s=None, **extra):
        key = (dev, ch)
        old = self.outputs_state.get(key, 0)
        if not hw and (value != old or key not in self.outputs_state):
            try:
                self.devices.set_output(dev, ch, value, max_s=max_s)
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"output {dev}/{ch}: {e}")
        if value != old or extra:
            self.outputs_state[key] = value
            self._log_io(t, dev, ch, "output", value, typ, **extra)
            if bool(value) != bool(old):
                self._emit("output_on" if value else "output_off", {"device": dev, "channel": ch, "value": value}, t)
            if value != old and (typ == "pwm" or self.devices.channel_kind(dev, ch) == "pwm"):
                self._emit("analog_output_changed", {"device": dev, "channel": ch, "value": value}, t)

    def _a_output_on(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        prm = self.output_params.get((dev, ch))
        if prm and (prm.get("frequency") or prm.get("duration")):
            self._output_with_params(th, p, dev, ch, prm)
            return
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, typ)

    def _output_with_params(self, th, p, dev, ch, prm):
        """Switch an output on with its settings (set output frequency / duty cycle / on-duration)."""
        f, dur = float(prm.get("frequency") or 0), float(prm.get("duration") or 0)
        if f > 0:
            period = 1.0 / f
            count = max(1, int(round(dur * f))) if dur > 0 else 0
            self._start_train(th, p, dev, ch, period, period * float(prm.get("duty", 50.0)) / 100.0, count, "train")
        else:
            self._start_train(th, p, dev, ch, dur, dur, 1, "pulse")

    def _output_setting(self, th, p, device, channel, **kw):
        dev, ch = self._resolve(th, p, device, channel)
        key = (dev, ch)
        self.output_params.setdefault(key, {}).update(kw)
        if key in self._trains or self.outputs_state.get(key):  # running: changes at once
            self._output_with_params(th, p, dev, ch, self.output_params[key]) \
                if (self.output_params[key].get("frequency") or self.output_params[key].get("duration")) \
                else self._a_output_on(th, p, dev, ch)

    def _a_set_output_frequency(self, th, p, device, channel, frequency):
        if frequency is not None and frequency < 0:
            raise ExprError("the frequency must not be negative")
        self._output_setting(th, p, device, channel, frequency=float(frequency or 0))

    def _a_set_output_duty(self, th, p, device, channel, duty_cycle):
        if not 0 < duty_cycle <= 100:
            raise ExprError("the duty cycle must be between 0 and 100 %")
        self._output_setting(th, p, device, channel, duty=float(duty_cycle))

    def _a_set_output_duration(self, th, p, device, channel, duration):
        self._output_setting(th, p, device, channel, duration=max(0.0, float(duration or 0)))

    def _a_output_off(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 0, self.t, typ)

    def _a_light_on(self, th, p, device, channel):
        """An output switched on, logged as a light (the "Light" measures of the I/O results)."""
        self._a_output_on(th, p, device, channel, "light")

    def _a_light_off(self, th, p, device, channel):
        self._a_output_off(th, p, device, channel, "light")

    def _a_light_level(self, th, p, device, channel, level):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, self._level(dev, ch, level), self.t, "light")

    def _level(self, dev, ch, percent):
        """A level 0..1 for an output from a percentage; digital outputs are on for any level above 0."""
        v = max(0.0, min(1.0, float(percent) / 100.0))
        if self.devices.channel_kind(dev, ch) == "output":
            return 1 if v > 0 else 0
        return 1 if v == 1 else 0 if v == 0 else round(v, 4)

    def _a_light_ramp(self, th, p, device, channel, level, duration, start):
        dev, ch = self._resolve(th, p, device, channel)
        key = (dev, ch)
        self._stop_train(key, self.t)
        v0 = self._level(dev, ch, start) if start not in (None, "") else float(self.outputs_state.get(key, 0))
        v1 = max(0.0, min(1.0, float(level) / 100.0))
        if duration <= 0:
            self._set_out(dev, ch, self._level(dev, ch, level), self.t, "light")
            self._emit("light_ramp_done", {"device": dev, "channel": ch}, self.t)
            return
        self._ramps[key] = _Ramp(self.t, v0, v1, float(duration), "light")
        self._ramp_tick(key, self.t)

    def _ramp_tick(self, key, t):
        r = self._ramps.get(key)
        if r is None:
            return
        f = min(1.0, max(0.0, (t - r.t0) / r.duration))
        v = r.v0 + (r.v1 - r.v0) * f
        dev, ch = key
        if self.devices.channel_kind(dev, ch) == "output":
            v = 1 if v > 0 else 0
        else:
            v = 1 if v >= 1 else 0 if v <= 0 else round(v, 3)
        if abs(float(self.outputs_state.get(key, -1)) - v) >= 1 / 255 or f >= 1:
            self._set_out(dev, ch, v, min(t, r.t0 + r.duration), r.typ)
        if f >= 1:
            del self._ramps[key]
            self._emit("light_ramp_done", {"device": dev, "channel": ch}, r.t0 + r.duration)

    def _a_output_toggle(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, 0 if self.outputs_state.get((dev, ch)) else 1, self.t)

    def _a_output_set(self, th, p, device, channel, value):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, max(0.0, min(1.0, float(value))), self.t, "pwm")

    def _a_all_outputs_off(self, th, p, device):
        device = self._devname(device)
        for key in list(self._trains):
            if not device or key[0] == device:
                self._stop_train(key, self.t)
        for (dev, ch), v in list(self.outputs_state.items()):
            if v and (not device or dev == device):
                self._set_out(dev, ch, 0, self.t)

    def _start_train(self, th, p, device, channel, period, width, count, typ, t0=None, pulses=None, levels=None):
        dev, ch = self._resolve(th, p, device, channel)
        key = (dev, ch)
        self._stop_train(key, self.t)
        width = max(0.0005, float(width))
        period = max(width, float(period))
        hw = False
        if pulses is not None:
            pulses = [(max(0.0, float(a)), max(0.0005, float(b))) for a, b in pulses]
            count = len(pulses)
            seq = getattr(self.devices, "pulse_sequence", None)
            if seq is not None and levels is None and self.devices.has(dev):
                try:  # timed on the computer's clock by the device manager, not by the frames
                    hw = bool(seq(dev, ch, pulses))
                except Exception as e:  # pragma: no cover - hardware dependent
                    self._error(th, p, f"pulse sequence: {e}")
        elif getattr(self.devices.device(dev, create=True), "hardware_pulses", False):
            try:
                hw = bool(self.devices.pulse_train(dev, ch, period, width, count))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(th, p, f"pulse train: {e}")
        t0 = self.t if t0 is None else t0
        self._ramps.pop(key, None)
        self._trains[key] = _Train(t0, period, width, count, hw, typ, pulses=pulses, levels=levels)
        self._train_tick(key, self.t)

    def _train_tick(self, key, t):
        tr = self._trains.get(key)
        if tr is None:
            return
        dev, ch = key
        guard = 0
        while guard < 100000:
            guard += 1
            if tr.n and tr.k >= tr.n and not tr.on:
                del self._trains[key]
                if tr.pulses is not None:
                    self._emit("pulse_sequence_done", {"device": dev, "channel": ch},
                               tr.on_time(tr.n - 1) + tr.width_of(tr.n - 1) if tr.n else t)
                elif tr.typ == "pellet":
                    self._pellet_train_done(key, t)
                return
            on_t = tr.on_time(tr.k)
            if not tr.on:
                if on_t > t + EPS:
                    return
                if not tr.hw and t - on_t > STALL_S:
                    # the frames stalled: deliver the remaining pulses from now instead of a burst of zero-length
                    # pulses that the device cannot follow (e.g. pellets counted but not dispensed)
                    lag = t - on_t
                    tr.t0 += lag
                    on_t = t
                    if not tr.delayed:
                        self._log_line(t, f"Output {dev}/{ch}: pulses delayed by {lag:.2f} s (the test stalled)")
                    tr.delayed += lag
                extra = {"train_start": True} if tr.first and tr.typ == "train" else {}
                tr.first = False
                if tr.levels is not None and tr.levels[tr.k] is not None:
                    self._intensity(dev, ch, tr.levels[tr.k], on_t)
                self._set_out(dev, ch, 1, on_t, tr.typ, hw=tr.hw, **extra)
                tr.on = True
            else:
                off_t = on_t + tr.width_of(tr.k)
                if off_t > t + EPS:
                    return
                if not tr.hw and t - off_t > STALL_S:
                    off_t = t  # the output really stayed on until now
                    tr.t0 = t - tr.width_of(tr.k) - (tr.on_time(tr.k) - tr.t0)
                self._set_out(dev, ch, 0, off_t, tr.typ, hw=tr.hw)
                tr.on = False
                tr.k += 1

    def _stop_train(self, key, t):
        self._ramps.pop(key, None)
        tr = self._trains.pop(key, None)
        if tr is None:
            return
        if tr.hw:
            try:
                self.devices.stop_train(*key)
            except Exception:  # pragma: no cover
                pass
        if tr.on:
            self._set_out(key[0], key[1], 0, t, tr.typ, hw=tr.hw)
            if tr.hw:
                try:
                    self.devices.set_output(key[0], key[1], 0)
                except Exception:  # pragma: no cover
                    pass

    def _a_output_pulse(self, th, p, device, channel, duration, typ="pulse"):
        self._start_train(th, p, device, channel, duration, duration, 1, typ)

    def _a_pulse_train(self, th, p, device, channel, frequency, pulse_width, duration):
        if frequency <= 0:
            raise ExprError("the frequency must be positive")
        period = 1.0 / frequency
        count = int(round(duration * frequency)) if duration > 0 else 0
        if duration > 0 and count == 0:
            count = 1
        self._start_train(th, p, device, channel, period, pulse_width / 1000.0, count, "train")

    def _a_pulse_train_stop(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)

    def _a_sync_pulse(self, th, p, device, channel, width):
        self._start_train(th, p, device, channel, width / 1000.0, width / 1000.0, 1, "sync")

    def _a_pellet(self, th, p, device, channel, count, pulse_width, gap, sensor="", timeout=1.0, retries=0):
        n = max(1, int(count))
        width = max(0.001, pulse_width / 1000.0)
        dev, ch = self._resolve(th, p, device, channel)
        self.pellet_counts[(dev, ch)] = self.pellet_counts.get((dev, ch), 0) + n
        self._start_train(th, p, dev, ch, max(width * 2, gap), width, n, "pellet")
        if sensor:
            sdev = self.devices.find_channel(sensor, ioconfig.INPUT_KINDS) or dev
            self._pellet_checks[(dev, ch)] = _PelletCheck(dev, ch, (sdev, sensor), n, max(0.05, float(timeout)),
                                                          max(0, int(retries)), width, max(width * 2, gap))
            if (dev, ch) not in self._trains:
                self._pellet_train_done((dev, ch), self.t)

    def _pellet_train_done(self, key, t):
        """The pellets of a dispense went out: wait for the sensor to see the missing ones."""
        chk = self._pellet_checks.get(key)
        if chk is not None:
            self._add_task(t + chk.timeout, lambda tt, key=key: self._pellet_timeout(key, tt), ("pellet",) + key)

    def _pellet_seen(self, sensor_key, t):
        for key, chk in list(self._pellet_checks.items()):
            if chk.sensor == sensor_key and chk.seen < chk.want:
                chk.seen += 1
                self._emit("pellet_dropped", {"device": chk.dev, "channel": chk.ch}, t)
                if chk.seen >= chk.want:
                    self._cancel_task(("pellet",) + key)
                    del self._pellet_checks[key]
                return

    def _pellet_timeout(self, key, t):
        chk = self._pellet_checks.get(key)
        if chk is None:
            return
        missing = chk.want - chk.seen
        if missing <= 0:
            del self._pellet_checks[key]
            return
        if chk.retries > 0:
            chk.retries -= 1
            chk.tries.append(t)
            self._log_line(t, f"Pellet dispenser {key[0]}/{key[1]}: {missing} pellet(s) not detected — retrying")
            self._log_io(t, key[0], f"{key[1]}.retries", "output", missing, "pellet_retry")
            self._start_train(None, (), key[0], key[1], chk.gap, chk.pulse_width, missing, "pellet")
            return
        del self._pellet_checks[key]
        self._log_line(t, f"Pellet dispenser {key[0]}/{key[1]}: {missing} pellet(s) not dispensed (jammed or empty)")
        self._log_io(t, key[0], f"{key[1]}.errors", "output", missing, "pellet_error")
        self._emit("pellet_error", {"device": key[0], "channel": key[1], "value": missing}, t)

    def _a_dipper(self, th, p, device, channel, duration):
        self._start_train(th, p, device, channel, duration, max(0.001, duration), 1, "dipper")

    def _a_liquid_drop(self, th, p, device, channel, count, pulse_width, gap):
        width = max(0.001, pulse_width / 1000.0)
        self._start_train(th, p, device, channel, max(width * 2, gap), width, max(1, int(count)), "drop")

    # -- odours
    def _a_odour(self, th, p, device, channel, odour, flow):
        dev = self._devname(device) or self.devices.find_channel(channel, ("odour",)) or "virtual"
        cfg = self.devices.channel_config(dev, channel)
        if not cfg:
            self._error(th, p, f"odour: '{channel}' is not an olfactometer channel — simulated")
        valves = dict(ioconfig.parse_pairs(cfg.get("odours", "")))
        name = "" if str(odour).strip().lower() in ("", "none") else str(odour).strip()
        if name and valves and name not in valves:
            raise ExprError(f"unknown odour '{name}' (the olfactometer has {', '.join(valves)})")
        blank = cfg.get("blank")
        for n, v in valves.items():
            if n != name and self.outputs_state.get((dev, v)):
                self._set_out(dev, v, 0, self.t, "valve")
        if name and name in valves:
            if blank:
                self._set_out(dev, blank, 0, self.t, "valve")
            self._set_out(dev, valves[name], 1, self.t, "valve")
        elif blank:
            self._set_out(dev, blank, 1, self.t, "valve")
        if flow and flow > 0 and cfg.get("flow"):
            level = min(1.0, float(flow) / float(cfg.get("max_flow", 1.0) or 1.0))
            self._set_out(dev, cfg["flow"], round(level, 4), self.t, "pwm", flow=float(flow))
        old = self._odours.get((dev, channel), "")
        if name != old:
            if old:
                self._log_io(self.t, dev, channel, "output", 0, "odour", odour=old)
            if name:
                self._log_io(self.t, dev, channel, "output", 1, "odour", odour=name)
            self._odours[(dev, channel)] = name

    def _a_odour_off(self, th, p, device, channel):
        self._a_odour(th, p, device, channel, "", 0)

    # -- shock intensity and optogenetics
    def _intensity(self, dev, ch, value, t, unit="%"):
        """Set the intensity output of an output channel (its ``intensity`` option): a level in % or, for a
        shocker, a current in mA through the ``calibration`` table (level:mA|...) or ``max_ma``."""
        cfg = self.devices.channel_config(dev, ch)
        ich = cfg.get("intensity")
        if not ich:
            raise ExprError(f"'{ch}' has no intensity option (the output that sets its intensity)")
        if unit == "mA":
            pts = ioconfig.calibration_points(cfg.get("calibration"))
            if len(pts) >= 2:
                level = ioconfig.level_for(float(value), pts)
            elif cfg.get("max_ma"):
                level = max(0.0, min(1.0, float(value) / float(cfg["max_ma"])))
            else:
                raise ExprError(f"shocker '{ch}' is not calibrated (calibration or max_ma option)")
            extra = {"ma": round(float(value), 4)}
        else:
            level = max(0.0, min(1.0, float(value) / 100.0))
            extra = {}
        self._set_out(dev, ich, round(level, 4), t, "pwm", **extra)
        self.intensities[(dev, ch)] = float(value)
        return level

    def _a_shock_intensity(self, th, p, device, channel, intensity):
        dev, ch = self._resolve(th, p, device, channel)
        self._intensity(dev, ch, intensity, self.t, "mA")

    def _a_opto_intensity(self, th, p, device, channel, intensity):
        dev, ch = self._resolve(th, p, device, channel)
        self._intensity(dev, ch, intensity, self.t)

    def _a_opto_train(self, th, p, device, channel, frequency, duty_cycle, duration, intensity):
        if frequency <= 0:
            raise ExprError("the frequency must be positive")
        if not 0 < duty_cycle <= 100:
            raise ExprError("the duty cycle must be between 0 and 100 %")
        dev, ch = self._resolve(th, p, device, channel)
        if intensity not in (None, ""):
            self._intensity(dev, ch, intensity, self.t)
        period = 1.0 / frequency
        count = max(1, int(round(duration * frequency))) if duration > 0 else 0
        self._start_train(th, p, dev, ch, period, period * duty_cycle / 100.0, count, "train")

    def _a_opto_sequence(self, th, p, device, channel, file, repeat, intensity):
        rows = read_pulse_file(file)
        if not rows:
            raise ExprError(f"no pulses in '{file}'")
        dev, ch = self._resolve(th, p, device, channel)
        if intensity not in (None, ""):
            self._intensity(dev, ch, intensity, self.t)
        reps = int(repeat) if repeat is not None else 1
        span = max(a + b for a, b, _ in rows)
        if reps <= 0:
            reps = max(1, int(3600 // max(span, 0.001)))  # "until stopped": an hour of repetitions
        reps = min(reps, max(1, 100000 // len(rows)))
        pulses = [(r * span + a, b) for r in range(reps) for a, b, _ in rows]
        levels = [lv for _r in range(reps) for _a, _b, lv in rows]
        if all(lv is None for lv in levels) or not self.devices.channel_config(dev, ch).get("intensity"):
            levels = None
        self._start_train(th, p, dev, ch, 1, 0.001, len(pulses), "train", pulses=pulses, levels=levels)

    def _a_shock_on(self, th, p, device, channel, max_duration, intensity=0):
        dev, ch = self._resolve(th, p, device, channel)
        if intensity and intensity > 0:
            self._intensity(dev, ch, intensity, self.t, "mA")
        m = max_duration if 0 < max_duration <= SHOCK_MAX_S else (2.0 if max_duration <= 0 else SHOCK_MAX_S)
        if max_duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: safety cut-off limited to {SHOCK_MAX_S:g} s")
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, "shock", max_s=m)
        self._shock_keys.add((dev, ch))
        due = self.t + m

        def cut(tt, dev=dev, ch=ch):
            if self.outputs_state.get((dev, ch)):
                self._set_out(dev, ch, 0, tt, "shock")
                self._log_line(tt, f"Shock {dev}/{ch} switched off by the safety cut-off")
        self._add_task(due, cut, ("shock", dev, ch))

    def _a_shock_off(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._cancel_task(("shock", dev, ch))
        self._set_out(dev, ch, 0, self.t, "shock")

    def _a_shock_pulse(self, th, p, device, channel, duration, intensity=0):
        d = min(max(0.0, duration), SHOCK_MAX_S)
        if duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: duration limited to {SHOCK_MAX_S:g} s")
        dev, ch = self._resolve(th, p, device, channel)
        if intensity and intensity > 0:
            self._intensity(dev, ch, intensity, self.t, "mA")
        self._shock_keys.add((dev, ch))
        self._start_train(th, p, dev, ch, d, d, 1, "shock")

    # -- audio
    def _audio_dev(self, device):
        return self._devname(device) or self.devices.find_type("audio") or "audio"

    def _audio(self, th, p, device, cmd, channel, value, duration, **kw):
        dev = self._audio_dev(device)
        if "volume" in kw and kw["volume"] is not None:  # the "set speaker volume" level scales every sound
            kw["volume"] = max(0.0, min(1.0, float(kw["volume"]) * self.volumes.get(dev, 1.0)))
        if self.devices.has(dev):
            try:
                self.devices.audio(dev, cmd, duration=duration, **kw)
            except Exception as e:  # pragma: no cover
                self._error(th, p, f"audio: {e}")
            for err in self.devices.device(dev).errors:
                self._error(th, p, err)
        key = (dev, channel)
        was_on = any(k[0] == dev for k in self._audio_on)
        self._audio_on[key] = value
        self._log_io(self.t, dev, channel, "output", value, "audio")
        if not was_on:
            self._emit("speaker_start", {"device": dev, "value": value}, self.t)
        if duration and duration > 0:
            self._add_task(self.t + duration, lambda tt, key=key: self._audio_off(key, tt, ended=True),
                           ("audio",) + key)

    def _audio_off(self, key, t, ended=False):
        """A sound stops: stopped, or (ended) played to its end."""
        if key in self._audio_on:
            del self._audio_on[key]
            self._cancel_task(("audio",) + key)
            self._log_io(t, key[0], key[1], "output", 0, "audio")
            if ended and key[1] == "sound":
                self._emit("sound_file_end", {"device": key[0]}, t)
            if not any(k[0] == key[0] for k in self._audio_on):
                self._emit("speaker_stop", {"device": key[0]}, t)

    def _a_tone(self, th, p, device, frequency, duration, volume):
        self._audio(th, p, device, "tone", "tone", frequency, duration, frequency=frequency, volume=volume)

    def _a_white_noise(self, th, p, device, duration, volume):
        self._audio(th, p, device, "noise", "noise", 1, duration, volume=volume)

    def _a_play_sound(self, th, p, device, file, duration, volume, repeat=1):
        if (not duration or duration <= 0) and repeat and repeat > 0:
            # the length of a WAV file: the end of the sound is logged and fires "sound file finished"
            duration = wav_duration(file) * int(repeat)
        self._audio(th, p, device, "file", "sound", 1, duration, file=file, volume=volume, repeat=repeat)

    def _a_loop_sound(self, th, p, device, file, volume):
        self._audio(th, p, device, "file", "sound", 1, 0, file=file, volume=volume, repeat=0)

    def _a_stop_sound(self, th, p, device):
        dev = self._audio_dev(device)
        if self.devices.has(dev):
            self.devices.audio(dev, "stop")
        for key in list(self._audio_on):
            if key[0] == dev:
                self._audio_off(key, self.t)

    def _a_beep(self, th, p):
        dev = self.devices.find_type("audio")
        if dev:
            self._a_tone(th, p, dev, 1000, 0.2, 0.5)
        else:
            self.outputs.beep()

    # -- syringe pumps, temperature controllers, sensors, balances, alerts
    def _channel_dev(self, th, p, device, channel, kinds, what):
        dev = self._devname(device) or self.devices.find_channel(channel, kinds) or ""
        if not dev or not self.devices.has(dev):
            self._error(th, p, f"{what} '{channel}': no such device — simulated")
            return dev or "virtual", False
        return dev, True

    def _pump(self, th, p, device, channel, op, **kw):
        dev, ok = self._channel_dev(th, p, device, channel, ("pump",), "pump")
        if ok:
            try:
                ok = bool(self.devices.pump(dev, channel, op, **kw))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(th, p, f"pump: {e}")
                ok = False
            for err in self.devices.device(dev).errors:
                self._error(th, p, err)
        return dev

    def _a_pump_infuse(self, th, p, device, channel, rate, volume, op="infuse"):
        if rate <= 0:
            raise ExprError("the rate must be positive")
        dev = self._pump(th, p, device, channel, op, rate_ml_min=float(rate), volume_ml=float(volume or 0))
        self._log_io(self.t, dev, channel, "output", 1, "pump", direction=op, rate=float(rate),
                     volume=float(volume or 0), **self._pump_counters(dev, channel))
        self._pumps_on[(dev, channel)] = op

    def _a_pump_withdraw(self, th, p, device, channel, rate, volume):
        self._a_pump_infuse(th, p, device, channel, rate, volume, op="withdraw")

    def _a_pump_stop(self, th, p, device, channel):
        dev = self._pump(th, p, device, channel, "stop")
        if self._pumps_on.pop((dev, channel), None) is not None:
            self._log_io(self.t, dev, channel, "output", 0, "pump", **self._pump_counters(dev, channel))

    def _pump_counters(self, dev, channel) -> dict:
        """The pump's volume counters when a command is given (they count from when the device opened): the
        baseline of the volume measures."""
        d = self.devices.device(dev, create=False)
        out = {}
        for word in ("infused", "withdrawn"):
            v = d.inputs.get(f"{channel}.{word}_ml") if d is not None else None
            if v is not None and f"{channel}.{word}_ml" in d.channels:
                out[f"{word}_ml"] = float(v)
        return out

    def _a_pump_syringe(self, th, p, device, channel, syringe):
        try:
            kw = {"diameter_mm": float(syringe)}
        except ValueError:
            kw = {"syringe": syringe}
        self._pump(th, p, device, channel, "set_syringe", **kw)
        self._log_line(self.t, f"Pump {channel}: syringe {syringe}")

    def _a_set_temperature(self, th, p, device, channel, target, ramp):
        dev, ok = self._channel_dev(th, p, device, channel, ("thermostat",), "temperature controller")
        if ok:
            self.devices.control(dev, channel, "target", target=float(target), ramp=float(ramp or 0))
        self._log_io(self.t, dev, channel, "output", float(target), "thermostat", ramp=float(ramp or 0))
        self._thermostats_on.add((dev, channel))

    def _a_temperature_off(self, th, p, device, channel):
        dev, ok = self._channel_dev(th, p, device, channel, ("thermostat",), "temperature controller")
        if ok:
            self.devices.control(dev, channel, "off")
        if (dev, channel) in self._thermostats_on:
            self._thermostats_on.discard((dev, channel))
            self._log_io(self.t, dev, channel, "output", 0, "thermostat")

    def _a_tare_sensor(self, th, p, device, channel):
        dev, ok = self._channel_dev(th, p, device, channel, ("sensor", "analog"), "sensor")
        if not ok:
            return
        d = self.devices.device(dev, create=False)
        c = d.channels.get(channel) if d is not None else None
        if c is None:
            raise ExprError(f"unknown sensor '{channel}'")
        v = d.inputs.get(channel, 0)
        c["tare"] = float(c.get("tare", 0.0)) + float(v or 0)  # readings are reported net of the tare
        self._log_line(self.t, f"Sensor {channel} tared at {float(v or 0):g}")

    def _a_read_sensor(self, th, p, device, channel, var):
        v = self._input_value(device, channel)
        self._setvar(th, var, float(v) if v is not None else float("nan"), p)

    def _a_weigh_animal(self, th, p, device, var):
        dev = self._devname(device) or self.devices.find_type("scale")
        d = self.devices.device(dev, create=False) if dev else None
        if d is None or not hasattr(d, "read"):
            raise ExprError("no balance configured (an I/O device of type scale)")
        res = d.read(timeout=3.0)
        grams = float(res[0] if isinstance(res, tuple) else res)
        self.animal_weights.append((self.t, grams))
        self._log_io(self.t, dev, "weight", "input", grams, "weight")
        self._log_line(self.t, f"Animal weighed: {grams:g} g")
        if var:
            self._setvar(th, var, grams, p)

    def _a_send_alert(self, th, p, text):
        self._alert(self.t, text)

    def _alert(self, t, text, key=None, repeat_s=0.0):
        """Log an alert and send it by e-mail / SMS (alert devices); with `key`, at most once every repeat_s."""
        if key is not None and repeat_s > 0:
            last = self._alert_times.get(key)
            if last is not None and t - last < repeat_s:
                return
            self._alert_times[key] = t
        subject = "mANY-MAZE alert"
        ctx = self.context or {}
        who = ", ".join(str(ctx[k]) for k in ("test", "animal", "apparatus") if ctx.get(k))
        self._log_line(t, f"Alert: {text}")
        self.alerts.append((t, text))
        notify = getattr(self.devices, "notify", None)
        if notify is not None:
            try:
                notify(subject + (f" — {who}" if who else ""), f"{text}\n\n(test time {t:.1f} s)")
            except Exception as e:  # pragma: no cover - network dependent
                self._error(None, (), f"alert: {e}")

    # -- communication
    def _a_serial_send(self, th, p, device, text):
        device = self._devname(device)
        dev = device or self.devices.find_type("serial") or self.devices.find_type("arduino")
        if dev and self.devices.has(dev):
            self.devices.send(dev, text)
            self.outputs.log.append(f"serial: {text}")
        elif device:
            self._error(th, p, f"Send serial command: device '{device}' is not configured")
            self.outputs.log.append(f"serial: {text}")
        else:
            self.outputs.write(text)

    def _a_signal(self, th, p, name):
        self._emit("signal", {"name": name}, self.t)

    def _set_switch(self, name, v, t):
        old = self.switches.get(name, 0)
        self.switches[name] = v
        if v != old:
            self._log_io(t, "virtual", name, "output", v, "switch")
            self._emit(f"virtual_switch_{'on' if v else 'off'}", {"switch": name}, t)

    def _a_virtual_switch_on(self, th, p, switch):
        self._set_switch(switch, 1, self.t)

    def _a_virtual_switch_off(self, th, p, switch):
        self._set_switch(switch, 0, self.t)

    def _a_virtual_switch_toggle(self, th, p, switch):
        self._set_switch(switch, 0 if self.switches.get(switch) else 1, self.t)

    def _a_simulate_input(self, th, p, device, channel, value):
        dev = self._devname(device) or self.devices.find_channel(channel, ioconfig.INPUT_KINDS) or "virtual"
        self.devices.set_input(dev, channel, value)
        self._poll_inputs(self.t)

    # -- variables, timers, schedules
    def _a_set_variable(self, th, p, var, value):
        self._setvar(th, var, value, p)

    def _a_increment(self, th, p, var, by):
        cur = self._lookup(th)(var) if var in self.vars or var in th.locals else 0
        if not isinstance(cur, (int, float)):
            raise ExprError(f"'{var}' is not a number")
        self._setvar(th, var, cur + by, p)

    def _a_decrement(self, th, p, var, by):
        self._a_increment(th, p, var, -by)

    def _a_array_append(self, th, p, var, value):
        cur = self.vars.get(var, [])
        if not isinstance(cur, list):
            raise ExprError(f"'{var}' is not an array")
        if len(cur) >= MAX_SEQ:
            raise ExprError("array too long")
        self._setvar(th, var, cur + [value], p)

    def _timer_value(self, name):
        tm = self.timers.get(name)
        if not tm:
            return 0.0
        return tm.acc + (self.t - tm.start if tm.start is not None else 0.0)

    def _a_start_timer(self, th, p, timer, seconds):
        tm = self.timers.setdefault(timer, _Timer())
        if tm.start is None:
            tm.start = th.clock if th.clock <= self.t else self.t
        tm.due = (tm.start - tm.acc + seconds) if seconds and seconds > 0 else None

    def _a_stop_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm and tm.start is not None:
            tm.acc += self.t - tm.start
            tm.start = tm.due = None

    def _a_reset_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm:
            tm.acc, tm.start, tm.due = 0.0, (self.t if tm.start is not None else None), None

    def _a_schedule_start(self, th, p, schedule, spec):
        self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)

    def _a_schedule_response(self, th, p, schedule, spec, var):
        s = self.schedules.get(schedule)
        if s is None:
            if not spec:
                raise ExprError(f"schedule '{schedule}' has not been started")
            s = self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)
        ok = s.response(self.t)
        if var:
            self._setvar(th, var, int(ok), p)
        if ok:
            self._emit("reinforcer_earned", {"schedule": schedule}, self.t)

    # -- test
    def _log_line(self, t, msg):
        self.log_lines.append((t, msg))
        self.outputs.log.append(f"{t:.2f}s {msg}")
        if self.on_log:
            try:
                self.on_log(msg, t)
            except Exception:  # pragma: no cover
                pass

    def _a_mark(self, th, p, name):
        name = name or "Mark"
        self.marks.append({"behaviour": name, "t": self.t, "t_end": None})
        if self.on_mark:
            self.on_mark(name, self.t)
        self._emit("event_marked", {"name": name}, self.t)

    def _a_mark_start(self, th, p, name):
        if any(m["behaviour"] == name and m["t_end"] is None for m in self.state_events):
            return
        m = {"behaviour": name, "t": self.t, "t_end": None}
        self.state_events.append(m)
        self.marks.append(m)
        self._emit("event_marked", {"name": name}, self.t)

    def _a_mark_end(self, th, p, name):
        for m in self.state_events:
            if m["behaviour"] == name and m["t_end"] is None:
                m["t_end"] = self.t

    def _a_log(self, th, p, text):
        self._log_line(self.t, text)

    def _end_test(self, th=None):
        if self.ended:
            return
        self.ended = True
        if self.on_end:
            self.on_end()

    def _a_end_test(self, th, p, reason="", allow_continuation=False):
        """End the test (with a reason, stored as the test's end reason). With allow_continuation the test pauses
        instead: continuing it fires "test continued"; stopping it ends it with the reason."""
        self.end_reason = str(reason or "")
        if allow_continuation and not self.paused:
            self.awaiting_continuation = True
            self._log_line(self.t, "Test ended by procedure" + (f" ({self.end_reason})" if self.end_reason else "")
                           + " — it can be continued")
            self._pause(self.t)
        elif not allow_continuation:
            self._end_test(th)
        raise _Stop()

    def _pause(self, t):
        if self.paused:
            return
        self.paused = True
        self._pause_t = t
        self.pauses.append([t, None])
        if self.started and not self.stopped:
            self._pause_safety(t)
        self._emit("test_paused", {}, t)
        if self.on_pause:
            self.on_pause(t)
        self._dispatch_now(t)  # "when test paused" runs now, not after the pause

    def _pause_safety(self, t):
        """Pulse trains stop and shocks go off (they cannot be timed while the test clock is stopped); with
        outputs_off_on_pause every output and sound goes off too, pumps stop and odours go off (temperature
        controllers keep regulating)."""
        self._ramps.clear()
        for key in list(self._trains):
            self._stop_train(key, t)
        for key in list(self._shock_keys):
            self._cancel_task(("shock",) + key)
            if self.outputs_state.get(key):
                self._set_out(key[0], key[1], 0, t, "shock")
        if not self.outputs_off_on_pause:
            return
        n = len(self._pumps_on) + sum(1 for v in self._odours.values() if v)
        for dev, ch in list(self._pumps_on):
            self._a_pump_stop(None, (), dev, ch)
        for (dev, ch), name in list(self._odours.items()):
            if name:
                self._a_odour_off(None, (), dev, ch)
        for (dev, ch), v in list(self.outputs_state.items()):
            if v:
                self._set_out(dev, ch, 0, t, "pwm" if isinstance(v, float) and v not in (0.0, 1.0) else "digital")
                n += 1
        for dev in {k[0] for k in self._audio_on}:
            try:
                if self.devices.has(dev):
                    self.devices.audio(dev, "stop")
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"audio: {e}")
        for key in list(self._audio_on):
            self._audio_off(key, t)
            n += 1
        if n:
            self._log_line(t, "Test paused: outputs switched off")

    def _dispatch_now(self, t):
        if not self.started or self.stopped or self._busy:
            return  # inside a frame / handler: the queued event is dispatched by the running loop
        self._busy = True
        try:
            self.t = max(self.t, t)
            self._run(self.t)
        finally:
            self._busy = False
        self._after()

    def _resume_test(self, t):
        if not self.paused:
            return
        self.paused = False
        self._pause_t = None
        if self.pauses and self.pauses[-1][1] is None:
            self.pauses[-1][1] = t
        self._emit("test_resumed", {}, t)
        if self.awaiting_continuation:
            self.awaiting_continuation = False
            self.end_reason = ""
            self._emit("test_continuation", {}, t)
        if self.on_resume:
            self.on_resume(t)
        self._dispatch_now(t)

    def _a_pause_test(self, th, p):
        self._pause(self.t)

    def _a_resume_test(self, th, p):
        self._resume_test(self.t)

    def _procs_named(self, name):
        idx = [i for i, q in enumerate(self.procedures) if q.get("name") == name]
        if not idx:
            raise ExprError(f"no procedure called '{name}'")
        return idx

    def _a_enable_procedure(self, th, p, procedure):
        idx = self._procs_named(procedure)
        for i in idx:
            self.proc_enabled[i] = True
        self._start_procs([i for i in idx if i not in self._started_procs], self.t, defer=True)

    def _a_disable_procedure(self, th, p, procedure):
        for i in self._procs_named(procedure):
            self._disable_proc(i, keep=th)
            if i == th.proc_i:
                raise _Stop()

    # -- touch screen
    def _stimulus(self, cmd, params):
        if self.on_stimulus:
            self.on_stimulus(cmd, params)

    def _a_show_stimulus(self, th, p, area, image, shape, color):
        self.stimuli[area] = {"image": image, "shape": shape, "color": color}
        self._log_io(self.t, "screen", area, "output", 1, "stimulus")
        self._stimulus("show", {"area": area, "image": image, "shape": shape, "color": color})

    def _a_hide_stimulus(self, th, p, area):
        if self.stimuli.pop(area, None) is not None:
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self._stimulus("hide", {"area": area})

    def _a_clear_screen(self, th, p):
        for area in list(self.stimuli):
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self.stimuli.clear()
        self._stimulus("clear", {})

    # -- callbacks the live test implements (pop-ups, display text, video recorder, zones)
    def _callback(self, th, p, name, cmd, params):
        fn = getattr(self, name, None)
        if fn is None:
            return False
        try:
            fn(cmd, params)
        except Exception as e:
            self._error(th, p, f"{cmd}: {e}")
        return True

    # -- test control
    def _a_schedule_test(self, th, p, stage, apparatus, delay):
        s = {"t": self.t, "stage": stage, "apparatus": apparatus, "delay_min": max(0.0, float(delay or 0))}
        self.scheduled_tests.append(s)
        self._log_line(self.t, "Another test scheduled for this animal"
                       + (f" (stage {stage})" if stage else "") + (f" in {s['delay_min']:g} min" if delay else ""))

    def _a_warning(self, th, p, text):
        self.user_warnings.append((self.t, text))
        self._log_line(self.t, f"Warning: {text}")

    def _a_error(self, th, p, text, stop=False):
        self._error(th, p, f"Error generated: {text}")
        if stop:
            self.end_reason = self.end_reason or f"Error: {text}"
            self._end_test(th)
            raise _Stop()

    # -- zones
    def _a_set_zone_label(self, th, p, zone, label):
        self.zone_labels[zone] = label
        self._log_line(self.t, f"Zone {zone} labelled “{label}”")
        self._callback(th, p, "on_zone", "label", {"zone": zone, "label": label})

    def _a_remove_zone_label(self, th, p, zone):
        if self.zone_labels.pop(zone, None) is not None:
            self._log_line(self.t, f"Zone {zone}: label removed")
        self._callback(th, p, "on_zone", "label", {"zone": zone, "label": ""})

    def _a_move_zone(self, th, p, zone, x, y):
        self.zone_moves[zone] = (float(x), float(y))
        self._log_line(self.t, f"Zone {zone} moved to ({float(x):g}, {float(y):g})")
        self._callback(th, p, "on_zone", "move", {"zone": zone, "x": float(x), "y": float(y)})

    # -- video recording
    def _video(self, th, p, cmd, **params):
        self.video_commands.append((self.t, cmd, params))
        if not self._callback(th, p, "on_video", cmd, params):
            self._log_line(self.t, f"Video recording: {cmd} (no recorder)")

    def _a_video_start(self, th, p):
        self._video(th, p, "start")

    def _a_video_stop(self, th, p):
        self._video(th, p, "stop")

    def _a_video_pause(self, th, p):
        self._video(th, p, "pause")

    def _a_video_unpause(self, th, p):
        self._video(th, p, "unpause")

    def _a_video_label(self, th, p, text, duration):
        self._video(th, p, "label", text=text, duration=float(duration or 0))

    # -- display and messages
    def _display(self, th, p, cmd, params):
        self.display_log.append((self.t, cmd, params))
        self._callback(th, p, "on_display", cmd, params)

    def _a_popup(self, th, p, text, title):
        self._log_line(self.t, f"Message: {text}")
        self._display(th, p, "popup", {"text": text, "title": title or "Procedure"})

    def _a_display_text(self, th, p, name, text, x, y, color):
        self.display_texts[name] = {"text": text, "x": float(x or 0), "y": float(y or 0), "color": color}
        self._display(th, p, "text", dict(self.display_texts[name], name=name))

    def _a_display_remove(self, th, p, name):
        self.display_texts.pop(name, None)
        self._display(th, p, "remove", {"name": name})

    def _a_display_clear(self, th, p):
        self.display_texts.clear()
        self._display(th, p, "clear", {})

    def _send(self, kinds, subject, text, to):
        notify = getattr(self.devices, "notify", None)
        ok = False
        if notify is not None:
            ctx = self.context or {}
            who = ", ".join(str(ctx[k]) for k in ("test", "animal", "apparatus") if ctx.get(k))
            ok = notify(subject + (f" — {who}" if who else ""), text, kinds=kinds, to=to or None)
        self._log_line(self.t, f"{'E-mail' if kinds == ('email',) else 'SMS'}{f' to {to}' if to else ''}: {text}"
                       + ("" if ok else " (not sent: no alert device)"))
        self.alerts.append((self.t, text))
        if not ok:
            raise ExprError("no alert (e-mail / SMS) device is configured")

    def _a_send_email(self, th, p, text, subject, to):
        self._send(("email",), subject or "mANY-MAZE", text, to)

    def _a_send_sms(self, th, p, text, to):
        self._send(("sms",), "mANY-MAZE", text, to)

    # -- programs and plug-ins
    def _a_run_program(self, th, p, program, arguments):
        import shlex
        import shutil
        import subprocess

        exe = str(program).strip()
        found = exe if Path(exe).expanduser().is_file() else shutil.which(exe)
        if not found:
            raise ExprError(f"program not found: {exe}")
        try:
            args = shlex.split(str(arguments or ""))
        except ValueError as e:
            raise ExprError(f"arguments: {e}") from None
        proc = subprocess.Popen([str(Path(found).expanduser())] + args, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # no shell, not waited for
        self.programs.append(proc)
        self._log_line(self.t, f"Program started: {exe}" + (f" {arguments}" if arguments else ""))

    def _a_plugin(self, th, p, plugin, argument, var):
        from . import plugins

        fn = plugins.get(plugin)
        if fn is None:
            raise ExprError(f"no plug-in called '{plugin}'")
        info = {"t": self.t, "variables": dict(self.vars), "context": dict(self.context or {}),
                "log": lambda text: self._log_line(self.t, f"{plugin}: {text}")}
        try:
            v = fn(argument, info)
        except Exception as e:
            raise ExprError(f"plug-in '{plugin}' failed: {e}") from None
        if var:
            self._setvar(th, var, v if isinstance(v, (int, float, str, list)) else (0 if v is None else str(v)), p)

    # -- speaker
    def _a_set_volume(self, th, p, device, volume):
        dev = self._audio_dev(device)
        self.volumes[dev] = max(0.0, min(1.0, float(volume)))
        self._log_line(self.t, f"Speaker {dev}: volume {self.volumes[dev]:g}")


_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def read_pulse_file(path) -> list[tuple[float, float, float | None]]:
    """Pulses from a text / CSV file: one per line, "onset (s), duration (s)[, intensity %]" separated by commas,
    semicolons, tabs or spaces; lines without two numbers (headers, comments) are skipped. Sorted by onset."""
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise ExprError(f"pulse file not found: {path}")
    rows = []
    for line in p.read_text(errors="replace").splitlines():
        line = line.split("#", 1)[0]
        nums = _NUM.findall(line)
        if len(nums) < 2 or not re.match(r"\s*[-+.\d]", line):  # a header or a comment
            continue
        a, b = float(nums[0]), float(nums[1])
        if a < 0 or b <= 0:
            continue
        rows.append((a, b, float(nums[2]) if len(nums) > 2 else None))
    return sorted(rows)


def wav_duration(path) -> float:
    """The length of a WAV file in seconds (0 if it cannot be read, e.g. another format)."""
    import wave

    try:
        with wave.open(str(Path(str(path)).expanduser()), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0
