"""Signal processing and closed-loop control for I/O devices: streaming filters of analogue inputs, sensor
calibration and temperature controllers (heating / cooling with set-point ramps).

Analogue filters (channel option ``filter``) run on every sample as it arrives, so they need evenly spaced samples:
drivers report every sample of a filtered channel (no deadband). Options::

    filter=lowpass,  cutoff_hz=10, order=2          Butterworth low-pass
    filter=highpass, cutoff_hz=0.5, order=2         Butterworth high-pass (removes drift)
    filter=bandpass, low_hz=1, high_hz=30, order=2  Butterworth band-pass
    filter=average,  window=20                      moving average of the last 20 samples (or window_ms=...)

The sample rate is the channel's ``rate_hz`` or 1000 / ``period_ms`` (up to 1 kHz on the mANY-MAZE firmware).

A temperature controller is a channel of kind ``thermostat`` on any device that has the parts it names::

    {"name": "plate", "kind": "thermostat", "sensor": "plate_temp", "heat": "heater", "cool": "peltier",
     "kp": 0.5, "ki": 0.02, "kd": 0.0, "band": 0.5, "max_temp": 45, "min_temp": 2}

``sensor`` is a sensor / analogue channel reading degrees, ``heat`` and ``cool`` are outputs (PWM outputs get the
PID level, digital outputs switch with a hysteresis of ``band``). A text device (type ``serial``) that has its
own controller is driven with ``set_cmd`` (e.g. ``"SP {value:.1f}"``) and ``off_cmd`` instead. The controller
reports two status channels, ``<name>.setpoint`` (the current, possibly ramping, set-point) and
``<name>.at_target`` (1 while the temperature is within ``band`` of the final target), and switches its outputs
off when the temperature leaves [min_temp, max_temp] or the sensor stops reporting.
"""

from __future__ import annotations

import math
import time
from collections import deque

import numpy as np

FILTER_TYPES = ("none", "lowpass", "highpass", "bandpass", "average")
SENSOR_TIMEOUT_S = 10.0  # a thermostat whose sensor has not reported for this long switches its outputs off


def sample_rate(c: dict) -> float:
    """Samples per second of an analogue channel (``rate_hz`` or 1000 / ``period_ms``)."""
    try:
        if c.get("rate_hz"):
            return max(0.1, float(c["rate_hz"]))
        return 1000.0 / max(1.0, float(c.get("period_ms", 50)))
    except (TypeError, ValueError):
        return 20.0


class AnalogFilter:
    """A streaming filter of one analogue channel: ``f(x)`` returns the filtered value of the next sample."""

    def __init__(self, kind: str, rate_hz: float, cutoff_hz=None, low_hz=None, high_hz=None, order: int = 2,
                 window: int | None = None):
        self.kind = kind
        self.rate = float(rate_hz)
        self._zi = None
        self._sos = None
        self._win: deque | None = None
        nyq = self.rate / 2.0
        if kind == "average":
            self._win = deque(maxlen=max(1, int(window or 10)))
            self._sum = 0.0
        elif kind in ("lowpass", "highpass", "bandpass"):
            from scipy.signal import butter

            order = max(1, min(8, int(order or 2)))
            if kind == "bandpass":
                lo, hi = float(low_hz or 0.0), float(high_hz or 0.0)
                if not 0 < lo < hi < nyq:
                    raise ValueError(f"band-pass needs 0 < low_hz < high_hz < {nyq:g} Hz (half the sample rate)")
                self._sos = butter(order, [lo, hi], btype="bandpass", fs=self.rate, output="sos")
            else:
                fc = float(cutoff_hz or 0.0)
                if not 0 < fc < nyq:
                    raise ValueError(f"the cut-off must be between 0 and {nyq:g} Hz (half the sample rate)")
                self._sos = butter(order, fc, btype=kind, fs=self.rate, output="sos")
        elif kind not in ("none", "", None):
            raise ValueError(f"unknown filter '{kind}' (one of {', '.join(FILTER_TYPES)})")

    @classmethod
    def from_channel(cls, c: dict) -> "AnalogFilter | None":
        kind = str(c.get("filter") or "none").lower()
        if kind in ("none", ""):
            return None
        rate = sample_rate(c)
        window = c.get("window")
        if window is None and c.get("window_ms"):
            window = max(1, int(round(float(c["window_ms"]) * rate / 1000.0)))
        return cls(kind, rate, c.get("cutoff_hz"), c.get("low_hz"), c.get("high_hz"), c.get("order", 2), window)

    def reset(self):
        self._zi = None
        if self._win is not None:
            self._win.clear()
            self._sum = 0.0

    def __call__(self, x: float) -> float:
        x = float(x)
        if self._win is not None:
            if len(self._win) == self._win.maxlen:
                self._sum -= self._win[0]
            self._win.append(x)
            self._sum += x
            return self._sum / len(self._win)
        if self._sos is None:
            return x
        from scipy.signal import sosfilt, sosfilt_zi

        if self._zi is None:  # start in the steady state of the first sample (no start-up transient)
            self._zi = sosfilt_zi(self._sos) * (x if self.kind == "lowpass" else 0.0)
            if self.kind != "lowpass":
                self._x0 = x
        if self.kind != "lowpass":
            x -= self._x0  # high-pass / band-pass: no step from the initial offset
        y, self._zi = sosfilt(self._sos, np.array([x]), zi=self._zi)
        return float(y[0])


def sensor_value(c: dict, raw: float) -> float:
    """A sensor reading in its units: ``raw * scale + offset`` minus the tare."""
    try:
        return float(raw) * float(c.get("scale", 1.0)) + float(c.get("offset", 0.0)) - float(c.get("tare", 0.0))
    except (TypeError, ValueError):
        return float(raw)


# ---------------------------------------------------------------------------------------------- thermostat
class Thermostat:
    """Closed-loop temperature control of one ``thermostat`` channel of a device (see the module docstring).

    :meth:`set_target` changes the target (optionally ramping the set-point at ``ramp`` degrees per minute) and
    :meth:`step` runs the loop (the device manager calls it about 10 times a second from its service thread)."""

    def __init__(self, device, name: str, cfg: dict):
        self.device, self.name, self.cfg = device, name, dict(cfg)
        self.target: float | None = None
        self.setpoint: float | None = None
        self.ramp = 0.0  # degrees per minute (0 = jump)
        self.at_target = False
        self._i = 0.0
        self._prev_err = None
        self._last = None
        self._sent_sp = None
        self._fault = ""
        f = self.cfg
        self.kp, self.ki, self.kd = (float(f.get(k, d)) for k, d in (("kp", 0.5), ("ki", 0.02), ("kd", 0.0)))
        self.band = float(f.get("band", 0.5))
        self.max_temp = float(f.get("max_temp", 45.0))
        self.min_temp = float(f.get("min_temp", -10.0))
        for suffix, kind in (("setpoint", "analog"), ("at_target", "status")):
            device.add_status(f"{name}.{suffix}", kind)

    # -- commands
    def set_target(self, target: float | None, ramp_per_min: float = 0.0, now: float | None = None):
        now = time.monotonic() if now is None else now
        if target is None:
            self.off(now)
            return
        target = max(self.min_temp, min(self.max_temp, float(target)))
        self.ramp = max(0.0, float(ramp_per_min or 0.0))
        if self.setpoint is None or not self.ramp:
            start = self.temperature() if self.ramp else None
            self.setpoint = start if start is not None else target
        self.target = target
        self._last = now
        self._fault = ""
        self.device.outputs[self.name] = target
        self.step(now)

    def off(self, now: float | None = None):
        self.target = self.setpoint = None
        self._i, self._prev_err = 0.0, None
        self.device.outputs[self.name] = 0
        self._drive(0.0)
        if self.cfg.get("off_cmd"):
            self.device.send(str(self.cfg["off_cmd"]))
        self._sent_sp = None
        self._report(None)

    # -- loop
    def temperature(self) -> float | None:
        s = self.cfg.get("sensor")
        if not s or s not in self.device.inputs:
            return None
        if s not in getattr(self.device, "input_times", {}):
            return None  # never reported
        if time.monotonic() - self.device.input_times[s] > SENSOR_TIMEOUT_S:
            return None
        v = self.device.inputs.get(s)
        return float(v) if v is not None and math.isfinite(float(v)) else None

    def step(self, now: float | None = None):
        if self.target is None:
            return
        now = time.monotonic() if now is None else now
        dt = max(0.0, now - (self._last if self._last is not None else now))
        self._last = now
        if self.ramp and self.setpoint is not None and self.setpoint != self.target:
            d = self.ramp / 60.0 * dt
            self.setpoint = min(self.target, self.setpoint + d) if self.target > self.setpoint else \
                max(self.target, self.setpoint - d)
        sp = self.setpoint if self.setpoint is not None else self.target
        if self.cfg.get("set_cmd"):  # the device controls the temperature itself
            if self._sent_sp is None or abs(sp - self._sent_sp) >= 0.05:
                try:
                    self.device.send(str(self.cfg["set_cmd"]).format(value=sp, target=self.target))
                except (KeyError, IndexError, ValueError) as e:
                    self.device._error(f"{self.device.name}: thermostat '{self.name}': bad set_cmd: {e}")
                self._sent_sp = sp
        temp = self.temperature()
        if temp is None:
            if self.cfg.get("sensor"):
                self._drive(0.0)
                self._fault_msg("no reading from the sensor — outputs off")
            self._report(sp)
            return
        if temp > self.max_temp or temp < self.min_temp:
            self._drive(0.0)
            self._fault_msg(f"temperature {temp:.1f} outside {self.min_temp:g}..{self.max_temp:g} — outputs off")
            self._report(sp, temp)
            return
        self._fault = ""
        if not self.cfg.get("set_cmd"):
            err = sp - temp
            if dt > 0:
                self._i = max(-1.0 / max(self.ki, 1e-9), min(1.0 / max(self.ki, 1e-9), self._i + err * dt))
            deriv = (err - self._prev_err) / dt if dt > 0 and self._prev_err is not None else 0.0
            self._prev_err = err
            self._drive(max(-1.0, min(1.0, self.kp * err + self.ki * self._i + self.kd * deriv)), err)
        self._report(sp, temp)

    def _fault_msg(self, msg):
        if self._fault != msg:
            self._fault = msg
            self.device._error(f"{self.device.name}: thermostat '{self.name}': {msg}")

    def _drive(self, u: float, err: float | None = None):
        for part, level in (("heat", max(0.0, u)), ("cool", max(0.0, -u))):
            ch = self.cfg.get(part)
            if not ch or ch not in self.device.channels:
                continue
            if self.device.kind(ch) == "pwm":
                v = round(level, 3)
            else:  # on / off with hysteresis
                cur = self.device.outputs.get(ch, 0)
                want_on = err is not None and ((err > self.band / 2) if part == "heat" else (err < -self.band / 2))
                keep_on = err is not None and ((err > 0) if part == "heat" else (err < 0))
                v = 1 if want_on or (cur and keep_on) else 0
            if self.device.outputs.get(ch) != v:
                self.device.set_output(ch, v)

    def _report(self, sp, temp=None):
        ch = self.device
        ch._changed(f"{self.name}.setpoint", round(sp, 2) if sp is not None else 0.0)
        at = self.target is not None and temp is not None and abs(temp - self.target) <= self.band
        if self.cfg.get("set_cmd") and self.target is not None and temp is None and not self.cfg.get("sensor"):
            at = sp == self.target  # no sensor: "at target" when the ramp is over
        if at != self.at_target:
            self.at_target = at
            ch._changed(f"{self.name}.at_target", 1 if at else 0)
