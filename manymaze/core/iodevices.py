"""Hardware I/O for live tests: digital/analogue inputs and outputs, pulse trains, encoders and audio.

Device configurations live in ``Project.io_devices`` (JSON)::

    {"name": "box1", "type": "arduino", "port": "/dev/cu.usbmodem1101", "baud": 115200, "watchdog_ms": 2000,
     "channels": [
        {"name": "lever",  "kind": "input",   "pin": 2, "pullup": true, "debounce_ms": 20, "invert": false},
        {"name": "pellet", "kind": "output",  "pin": 8},
        {"name": "laser",  "kind": "output",  "pin": 9},
        {"name": "light",  "kind": "pwm",     "pin": 5},
        {"name": "wheel",  "kind": "encoder", "pin": 3, "pin_b": 4, "counts_per_rev": 1024, "cm_per_rev": 50},
        {"name": "force",  "kind": "analog",  "pin": 0, "period_ms": 50, "deadband": 2, "scale": 0.00489}]}

Device types:

* ``virtual`` — simulation: outputs are kept in memory, inputs are set from code or the GUI
  (:meth:`DeviceManager.set_input`). Unknown device names used by procedures become virtual devices.
* ``arduino`` — an Arduino running ``firmware/manymaze_io`` (line protocol, see ``firmware/README.md``):
  debounced digital inputs, outputs with optional maximum on-time, PWM, hardware-timed pulses and pulse
  trains (optogenetics, pellet dispensers), analogue inputs, quadrature encoders, heartbeat watchdog (on by
  default, 2000 ms, when the board has outputs; ``"watchdog_ms": 0`` turns it off).
* ``serial`` — any device driven by text lines; output channels have ``"on"``/``"off"`` command strings,
  input channels have ``"on"``/``"off"`` strings that are matched against received lines.
* ``audio`` — the computer's sound output: tones, white noise and sound files, once or repeated (WAV generated
  with NumPy and played with ``afplay``/``paplay``/``aplay``, or by a player installed by the GUI in
  ``AudioDevice.player``).
* ``serial_lines``, ``firmata``, ``nidaq``, ``labjack`` and ``notify`` (e-mail / SMS alerts): see :mod:`.iodrivers`;
  syringe pumps and balances: :mod:`.pumps` and :mod:`.scales`.

Channel kinds: ``input`` (digital in: lever, nose poke, beam, switch, TTL), ``pir`` (movement detector, a digital
input), ``output`` (digital out: TTL, relay, light, pellet dispenser, door, shocker trigger, laser), ``pwm``
(analogue / PWM out, level 0..1), ``analog`` (analogue in, optionally filtered, see :mod:`.iocontrol`),
``sensor`` (a calibrated analogue, HX711 load-cell or DHT22 reading: weight, light, temperature, humidity),
``encoder`` (quadrature rotary encoder, e.g. running wheel), ``thermostat`` (closed-loop temperature control),
``odour`` (olfactometer valves) and ``status`` (derived channels reported by drivers and controllers, e.g.
``plate.at_target`` or ``pump1.stalled``).

pyserial is optional (only needed for serial devices).

Device types, channel kinds and the configuration rules (new_device, watchdog_ms...) are in :mod:`.ioconfig`.
:func:`.iomeasures.io_measures` turns a test's I/O log (``Test.io_events``) into ANY-maze-style result measures.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
import wave
from pathlib import Path

import numpy as np

from .iocontrol import AnalogFilter, Thermostat, sensor_value
from .ioconfig import INPUT_KINDS, watchdog_ms

FIRMWARE_ID = "MANYMAZE_IO"


def serial_ports() -> list[str] | None:
    """Available serial ports, or None when pyserial is not installed."""
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None
    return [p.device for p in list_ports.comports()]


def _open_serial(port: str, baud: int):
    import serial  # type: ignore

    return serial.Serial(port, baud, timeout=0, write_timeout=0.5)


# ======================================================================================== devices
class Device:
    type = "virtual"
    hardware_pulses = False

    def __init__(self, cfg: dict, transport=None):
        self.cfg = dict(cfg)
        self.name = str(cfg.get("name") or self.type)
        self.channels: dict[str, dict] = {}
        for c in cfg.get("channels", []) or []:
            if c.get("name"):
                self.channels[str(c["name"])] = dict(c)
        self.inputs: dict[str, float] = {}
        self.outputs: dict[str, float] = {}
        self.errors: list[str] = []
        self.connected = False
        self.transport = transport
        self._pending: list[tuple[str, float, float | None]] = []  # (channel, value, board ms or None)
        self.watchdog_fired = 0  # times the board's watchdog switched every output off
        self.input_times: dict[str, float] = {}  # monotonic time of each input's last report
        self.filters: dict[str, AnalogFilter] = {}
        for n, c in list(self.channels.items()):
            if c.get("kind", "input") in INPUT_KINDS:
                self.inputs[n] = 0
            else:
                self.outputs[n] = 0
            if c.get("kind") in ("analog", "sensor") and c.get("filter"):
                try:
                    f = AnalogFilter.from_channel(c)
                except (ValueError, TypeError) as e:
                    self._error(f"{self.name}: channel '{n}': {e}")
                    f = None
                if f is not None:
                    self.filters[n] = f
        self.thermostats: dict[str, Thermostat] = {n: Thermostat(self, n, c) for n, c in list(self.channels.items())
                                                   if c.get("kind") == "thermostat"}

    # -- lifecycle
    def open(self):
        self.connected = True

    def close(self):
        self.connected = False

    # -- helpers
    def kind(self, channel: str) -> str | None:
        c = self.channels.get(channel)
        return c.get("kind", "input") if c else None

    def _error(self, msg: str):
        if msg not in self.errors:
            self.errors.append(msg)

    def _changed(self, channel: str, value: float, ms: float | None = None, force: bool = False):
        """An input value; unchanged values are dropped unless `force` (every sample of a sampled signal)."""
        self.input_times[channel] = time.monotonic()
        if self.inputs.get(channel) != value or force or (ms is not None and channel in self.filters):
            self.inputs[channel] = value
            self._pending.append((channel, value, ms))

    def add_status(self, name: str, kind: str = "status"):
        """A derived input channel reported by the driver itself (pump running / stalled, thermostat set-point)."""
        self.channels.setdefault(name, {"name": name, "kind": kind, "derived": True})
        self.inputs.setdefault(name, 0)

    def _analog_in(self, channel: str, value: float, ms: float | None = None, force: bool = False):
        """An analogue / sensor sample (already scaled): calibrated, filtered and reported."""
        c = self.channels.get(channel, {})
        if c.get("kind") == "sensor":
            value = sensor_value({k: v for k, v in c.items() if k != "scale"}, value)
        f = self.filters.get(channel)
        if f is not None:
            value = round(f(value), 6)
        self._changed(channel, value, ms, force)

    def _read(self):
        """Read the hardware (subclasses): input changes go to ``_pending`` through :meth:`_changed`."""

    # -- API
    def poll_ex(self) -> list[tuple[str, float, float | None]]:
        """Input changes since the last call: [(channel, value, board time in ms or None)]."""
        self._read()
        out, self._pending = self._pending, []
        return out

    def poll(self) -> list[tuple[str, float]]:
        """Input changes since the last call: [(channel, value)]."""
        return [(c, v) for c, v, _ in self.poll_ex()]

    def keepalive(self):
        """Heartbeat for devices with a watchdog (called from the manager's keep-alive thread)."""

    def keepalive_period(self) -> float:
        """Seconds between heartbeats (0 = no heartbeat needed)."""
        return 0.0

    def set_output(self, channel: str, value: float, max_s: float | None = None):
        self.outputs[channel] = value

    def pulse_train(self, channel: str, period_s: float, width_s: float, count: int) -> bool:
        """Start a hardware-timed train (count 0 = until stopped). False if the device cannot time pulses."""
        return False

    def stop_train(self, channel: str) -> bool:
        return False

    def send(self, text: str) -> bool:
        return False

    def audio(self, cmd: str, **kw) -> bool:
        return False

    def pump(self, channel: str, op: str, **kw) -> bool:
        """Syringe pump command (see :mod:`.pumps`); False when the device has no pumps."""
        return False

    def control(self, channel: str, op: str, **kw) -> bool:
        """Controller command: ``target`` (target, ramp) / ``off`` of a thermostat channel."""
        th = self.thermostats.get(channel)
        if th is None:
            self._error(f"{self.name}: '{channel}' is not a temperature controller")
            return False
        if op == "off":
            th.off()
        else:
            th.set_target(kw.get("target"), kw.get("ramp", 0.0))
        return True

    def notify(self, subject: str, text: str, kinds=None, to=None) -> bool:
        """Send an alert (alert devices); kinds: ("email",) / ("sms",) / None = both; to: addresses / numbers
        (comma-separated) instead of the configured ones."""
        return False

    def service(self, now: float):
        """Periodic work (controllers), from the manager's service thread."""
        for th in self.thermostats.values():
            th.step(now)

    def set_input(self, channel: str, value: float):
        """Simulate an input (virtual devices; ignored by hardware devices)."""

    def all_off(self):
        for th in self.thermostats.values():
            if th.target is not None:
                th.off()
        for ch in list(self.outputs):
            if self.outputs[ch] and self.kind(ch) not in ("thermostat", "odour"):
                self.set_output(ch, 0)


class VirtualDevice(Device):
    type = "virtual"

    def set_input(self, channel: str, value: float):
        if channel not in self.channels:
            self.channels[channel] = {"name": channel, "kind": "input"}
        if self.kind(channel) in ("analog", "sensor") and (channel in self.filters or
                                                          self.kind(channel) == "sensor"):
            self._analog_in(channel, value)
        else:
            self._changed(channel, value)

    def add_counts(self, channel: str, n: int):
        """Advance a simulated encoder by n counts."""
        if channel not in self.channels:
            self.channels[channel] = {"name": channel, "kind": "encoder"}
        self._changed(channel, self.inputs.get(channel, 0) + n)

    def set_output(self, channel: str, value: float, max_s: float | None = None):
        if channel not in self.channels:
            self.channels[channel] = {"name": channel, "kind": "output"}
        self.outputs[channel] = value


class _LineDevice(Device):
    """Base for devices that talk text lines over a serial port (or an injected transport)."""

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._buf = b""
        self.sent: list[str] = []
        self._io_lock = threading.RLock()  # the keep-alive thread writes while a test thread reads / writes
        self._last_write = 0.0

    def open(self):
        if self.transport is None:
            port = self.cfg.get("port")
            if not port:
                self._error(f"{self.name}: no serial port configured")
                return
            try:
                self.transport = _open_serial(port, int(self.cfg.get("baud", 115200)))
            except ImportError:
                self._error(f"{self.name}: pyserial is not installed (pip install pyserial)")
                return
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: could not open {port}: {e}")
                return
        self.connected = True

    def close(self):
        with self._io_lock:
            if self.transport is not None:
                try:
                    self.transport.close()
                except Exception:  # pragma: no cover
                    pass
            self.transport = None
            self.connected = False

    def write_line(self, line: str) -> bool:
        """Send a line; False when it was not sent (not connected, or the write failed: the device is then marked
        disconnected and its port closed, so that DeviceManager.open() opens it again)."""
        with self._io_lock:
            self.sent.append(line)
            if len(self.sent) > 5000:
                del self.sent[:1000]
            if self.transport is None or not self.connected:
                return False
            try:
                self.transport.write((line + self.cfg.get("eol", "\n")).encode())
                self._last_write = time.monotonic()
                return True
            except Exception as e:
                self._error(f"{self.name}: write failed: {e}")
                self.close()
                return False

    def read_lines(self) -> list[str]:
        with self._io_lock:
            if self.transport is None:
                return []
            try:
                n = getattr(self.transport, "in_waiting", 0)
                data = self.transport.read(n or 4096) if n is None or n > 0 else b""
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: read failed: {e}")
                return []
            if not data:
                return []
            self._buf += data
            *lines, self._buf = self._buf.split(b"\n")
            return [ln.decode(errors="replace").strip() for ln in lines if ln.strip()]


class SerialDevice(_LineDevice):
    """Raw text commands: output channels send their "on"/"off" strings; input channels match received lines."""

    type = "serial"

    def send(self, text: str) -> bool:
        return self.write_line(text)

    def set_output(self, channel: str, value: float, max_s: float | None = None) -> bool:
        c = self.channels.get(channel, {})
        cmd = c.get("on" if value else "off")
        if cmd is None:
            cmd = f"{channel} {'ON' if value else 'OFF'}" if not isinstance(value, float) or value in (0, 1) \
                else f"{channel} {value:g}"
        if not self.write_line(str(cmd)):
            return False
        self.outputs[channel] = value
        return True

    def _read(self):
        for line in self.read_lines():
            for n, c in self.channels.items():
                if c.get("kind", "input") != "input":
                    continue
                if c.get("on") and line == c["on"]:
                    self._changed(n, 1)
                elif c.get("off") and line == c["off"]:
                    self._changed(n, 0)


class ArduinoDevice(_LineDevice):
    """Arduino running firmware/manymaze_io (see firmware/README.md for the protocol)."""

    type = "arduino"
    hardware_pulses = True

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self.version = ""
        self.by_pin: dict[tuple[str, int], str] = {}
        self.dht: dict[int, dict[str, str]] = {}  # DHT22 pin -> {"temperature": channel, "humidity": channel}
        for n, c in self.channels.items():
            k = c.get("kind", "input")
            tag = {"analog": "A", "encoder": "E"}.get(k, "D")
            if k == "sensor":
                tag = {"hx711": "L", "dht22": "U"}.get(c.get("interface", "analog"), "A")
            if c.get("pin") is None or c.get("derived"):
                continue
            if tag == "U":
                self.dht.setdefault(int(c["pin"]), {})["humidity" if c.get("sensor") == "humidity"
                                                       else "temperature"] = n
            else:
                self.by_pin[(tag, int(c["pin"]))] = n

    def open(self, handshake_s: float = 3.0):
        super().open()
        if not self.connected:
            return
        deadline = time.monotonic() + handshake_s
        retry = 0.0
        while time.monotonic() < deadline and not self.version:
            if time.monotonic() >= retry:  # most boards reset when the port opens: ask again every second
                self.write_line("?")
                retry = time.monotonic() + 1.0
            for line in self.read_lines():
                self._parse(line)
            if not self.version:
                time.sleep(0.05)
        if not self.version:
            self._error(f"{self.name}: no reply from the mANY-MAZE firmware on {self.cfg.get('port', '?')}")
        self.configure()

    def configure(self):
        self.write_line("Z")
        dht_done = set()
        for n, c in self.channels.items():
            k, pin = c.get("kind", "input"), c.get("pin")
            if k in ("thermostat", "odour") or c.get("derived"):
                continue  # built from other channels
            if pin is None:
                self._error(f"{self.name}: channel '{n}' has no pin")
                continue
            pin = int(pin)
            iface = c.get("interface", "analog") if k == "sensor" else None
            if k in ("input", "pir"):
                self.write_line(f"I {pin} {1 if c.get('pullup', k == 'input') else 0} "
                                f"{int(c.get('debounce_ms', 20))}")
            elif k in ("output", "pwm"):
                self.write_line(f"O {pin} {1 if c.get('invert') else 0}")
            elif k == "analog" or iface == "analog":
                period = max(1, int(c.get("period_ms", 50)))
                # filtered channels need every sample; fast channels are sent in batches of ~10 ms
                deadband = 0 if n in self.filters else int(c.get("deadband", 2))
                batch = max(1, min(16, 10 // period)) if period < 10 else 1
                self.write_line(f"A {pin} {period} {deadband}" + (f" {batch}" if batch > 1 else ""))
            elif iface == "hx711":
                if c.get("pin_b") is None:
                    self._error(f"{self.name}: HX711 sensor '{n}' needs pin B (SCK)")
                    continue
                self.write_line(f"L {pin} {int(c['pin_b'])} {max(100, int(c.get('period_ms', 100)))}")
            elif iface == "dht22":
                if pin not in dht_done:
                    dht_done.add(pin)
                    self.write_line(f"U {pin} {max(2000, int(c.get('period_ms', 2000)))}")
            elif k == "encoder":
                if c.get("pin_b") is None:
                    self._error(f"{self.name}: encoder '{n}' needs pin B")
                    continue
                self.write_line(f"E {pin} {int(c['pin_b'])}")
        wd = self.watchdog_ms()
        if wd:
            self.write_line(f"H {wd}")
        self.write_line("Q")

    def watchdog_ms(self) -> int:
        return watchdog_ms(self.cfg)

    def keepalive_period(self) -> float:
        wd = self.watchdog_ms()
        return wd / 3000.0 if wd and self.connected else 0.0

    def keepalive(self):
        """Send a heartbeat unless another line was written recently (any line resets the board's watchdog)."""
        per = self.keepalive_period()
        if per and time.monotonic() - self._last_write >= per:
            self.write_line(".")

    def _pin(self, channel: str) -> int | None:
        c = self.channels.get(channel)
        if not c or c.get("pin") is None:
            self._error(f"{self.name}: unknown output channel '{channel}'")
            return None
        return int(c["pin"])

    def _parse(self, line: str):
        parts = line.split()
        if not parts:
            return
        tag = parts[0]
        if tag == "S" and len(parts) >= 5:  # batch of analogue samples: S n first_ms period_us v1 v2 ...
            try:
                pin, ms0, per_us = int(parts[1]), float(parts[2]), float(parts[3])
                vals = [float(x) for x in parts[4:]]
            except ValueError:
                return
            n = self.by_pin.get(("A", pin))
            if n is not None:
                sc = float(self.channels[n].get("scale", 1.0))
                for i, raw in enumerate(vals):
                    self._analog_in(n, raw * sc, ms0 + i * per_us / 1000.0, force=True)
            return
        if tag == "U" and len(parts) >= 4:  # DHT22: U pin temperature_x10 humidity_x10 ms
            try:
                pin, tx10, hx10 = int(parts[1]), float(parts[2]), float(parts[3])
                ms = int(parts[4]) if len(parts) >= 5 else None
            except ValueError:
                return
            for what, v in (("temperature", tx10 / 10.0), ("humidity", hx10 / 10.0)):
                n = self.dht.get(pin, {}).get(what)
                if n is not None:
                    self._analog_in(n, v, ms)
            return
        if tag == "L" and len(parts) >= 3:  # HX711: L dout raw ms
            try:
                pin, raw = int(parts[1]), float(parts[2])
                ms = int(parts[3]) if len(parts) >= 4 else None
            except ValueError:
                return
            n = self.by_pin.get(("L", pin))
            if n is not None:
                self._analog_in(n, raw * float(self.channels[n].get("scale", 1.0)), ms)
            return
        if tag in ("D", "A", "E") and len(parts) >= 3:
            try:
                pin, raw = int(parts[1]), float(parts[2])
            except ValueError:
                return
            n = self.by_pin.get((tag, pin))
            if n is None:
                return
            c = self.channels[n]
            if tag == "D":  # raw pin level; with the pull-up a closed switch reads LOW, i.e. "on"
                v = 1 - int(raw) if c.get("pullup", c.get("kind", "input") == "input") else int(raw)
                if c.get("invert"):
                    v = 1 - v
            elif tag == "A":
                v = raw * float(c.get("scale", 1.0))
            else:
                v = int(raw)
            ms = None
            if len(parts) >= 4:  # the board's millis() clock
                try:
                    ms = int(parts[3])
                except ValueError:
                    ms = None
            if tag == "A":
                self._analog_in(n, v, ms)
            else:
                self._changed(n, v, ms)
        elif tag == "ERR":
            self._error(f"{self.name}: firmware error: {' '.join(parts[1:])}")
        elif tag == "WATCHDOG":
            self.watchdog_fired += 1
            for ch in self.outputs:
                self.outputs[ch] = 0
            self.errors.append(f"{self.name}: watchdog fired — all outputs switched off")
        elif tag.startswith(FIRMWARE_ID):
            self.version = line

    def _read(self):
        for line in self.read_lines():
            self._parse(line)

    def set_output(self, channel: str, value: float, max_s: float | None = None) -> bool:
        """False when the command could not be sent (the output's state is then unchanged)."""
        pin = self._pin(channel)
        if pin is None:
            self.outputs[channel] = value
            return True
        if self.channels[channel].get("kind") == "pwm" and value not in (0, 1, True, False):
            line = f"P {pin} {int(round(max(0.0, min(1.0, float(value))) * 255))}"
        elif self.channels[channel].get("kind") == "pwm":
            line = f"P {pin} {255 if value else 0}"
        else:
            extra = f" {int(max_s * 1000)}" if (value and max_s) else ""
            line = f"W {pin} {1 if value else 0}{extra}"
        if not self.write_line(line):
            return False
        self.outputs[channel] = value
        return True

    def pulse_train(self, channel, period_s, width_s, count):
        pin = self._pin(channel)
        if pin is None:
            return False
        return self.write_line(f"T {pin} {period_s * 1000:.3f} {width_s * 1000:.3f} {int(count)}")

    def stop_train(self, channel):
        pin = self._pin(channel)
        if pin is None:
            return False
        return self.write_line(f"X {pin}")

    def send(self, text: str) -> bool:
        return self.write_line(text)

    def all_off(self):
        for th in self.thermostats.values():
            th.target = th.setpoint = None
        self.write_line("R")
        for ch in self.outputs:
            self.outputs[ch] = 0


# ---------------------------------------------------------------------------------------- audio
def tone_samples(freq: float, duration: float, rate: int = 44100, volume: float = 0.5, ramp_s: float = 0.005,
                 start: int = 0, total: int | None = None) -> np.ndarray:
    """int16 sine samples [start, start+n) of a tone of `duration` s with raised-cosine on/off ramps."""
    total = int(round(duration * rate)) if total is None else total
    n = max(0, min(int(round(duration * rate)), total) - start)
    i = np.arange(start, start + n)
    y = np.sin(2 * np.pi * float(freq) * i / rate)
    return _finish(y, i, total, rate, volume, ramp_s)


def noise_samples(duration: float, rate: int = 44100, volume: float = 0.5, ramp_s: float = 0.005, start: int = 0,
                  total: int | None = None, seed: int | None = None) -> np.ndarray:
    total = int(round(duration * rate)) if total is None else total
    n = max(0, total - start)
    i = np.arange(start, start + n)
    y = np.random.default_rng(seed).uniform(-1, 1, n)
    return _finish(y, i, total, rate, volume, ramp_s)


def _finish(y, i, total, rate, volume, ramp_s):
    r = max(1, int(ramp_s * rate))
    env = np.minimum(1.0, np.minimum(i / r, (total - 1 - i) / r).clip(0))
    env = 0.5 - 0.5 * np.cos(np.pi * env)
    return (y * env * max(0.0, min(1.0, float(volume))) * 32767).astype(np.int16)


def write_wav(path, kind: str, duration: float, freq: float = 1000.0, volume: float = 0.5,
              rate: int = 44100) -> str:
    """Write a tone ('tone') or white noise ('noise') WAV in 1-second chunks (low memory)."""
    duration = max(0.01, min(float(duration), 3600.0))
    total = int(round(duration * rate))
    rng_seed = 12345
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for s in range(0, total, rate):
            if kind == "tone":
                block = tone_samples(freq, duration, rate, volume, start=s, total=total)[:rate]
            else:
                block = noise_samples(duration, rate, volume, start=s, total=total, seed=rng_seed + s)[:rate]
            w.writeframes(block.tobytes())
    return str(path)


def _audio_dir() -> Path:
    d = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "manymaze_audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


class AudioDevice(Device):
    """Computer audio. ``player`` (class attribute) may be set by the GUI to a callable(path, volume) returning
    an object with ``stop()`` (e.g. a Qt Multimedia player); otherwise a command-line player is used."""

    type = "audio"
    player = None

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.played: list[tuple[str, dict]] = []
        self._procs: list = []
        self.backend = self._pick_backend(cfg.get("backend", "auto"))

    @staticmethod
    def _pick_backend(want: str):
        if want == "none":
            return None
        if want not in ("auto", None, ""):
            return want if shutil.which(want) else None
        cands = ["afplay"] if sys.platform == "darwin" else ["paplay", "aplay"]
        return next((c for c in cands if shutil.which(c)), None)

    def audio(self, cmd: str, **kw) -> bool:
        self.played.append((cmd, dict(kw)))
        if cmd == "stop":
            self.stop()
            return True
        rate = int(self.cfg.get("rate", 44100))
        vol = float(kw.get("volume", 0.5))
        if cmd == "tone":
            f, d = float(kw.get("frequency", 1000)), float(kw.get("duration", 1))
            if f >= rate / 2:
                rate = int(min(192000, max(rate, 2.2 * f)))
            path = _audio_dir() / f"tone_{f:g}_{d:g}_{vol:g}_{rate}.wav"
            if not path.exists():
                write_wav(path, "tone", d, f, vol, rate)
        elif cmd == "noise":
            d = float(kw.get("duration", 1))
            path = _audio_dir() / f"noise_{d:g}_{vol:g}_{rate}.wav"
            if not path.exists():
                write_wav(path, "noise", d, volume=vol, rate=rate)
        elif cmd == "file":
            path = Path(str(kw.get("file", "")))
            if not path.exists():
                self._error(f"{self.name}: sound file not found: {path}")
                return False
        else:
            return False
        repeat = int(kw.get("repeat", 1) or 0) if cmd == "file" else 1
        if repeat != 1:
            loop = _Loop(self, str(path), vol, repeat)
            self._procs.append(loop)
            loop.start()
            return True
        return self._play(str(path), vol if cmd == "file" else 1.0)

    def _play(self, path: str, volume: float, track: bool = True):
        if AudioDevice.player is not None:
            try:
                h = AudioDevice.player(path, volume)
                if not track:
                    return h
                self._procs.append(h)
                return True
            except Exception as e:  # pragma: no cover - GUI dependent
                self._error(f"{self.name}: audio player failed: {e}")
                return False
        if self.backend is None:
            return False
        args = [self.backend, path]
        if self.backend == "afplay":  # pragma: no cover - macOS
            args = ["afplay", "-v", f"{volume:g}", path]
        try:  # pragma: no cover - depends on the sound system
            h = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not track:
                return h
            self._procs.append(h)
            return True
        except Exception as e:  # pragma: no cover
            self._error(f"{self.name}: could not play sound: {e}")
            return False

    def stop(self):
        for p in self._procs:
            try:
                if hasattr(p, "terminate"):
                    p.terminate()
                else:
                    p.stop()
            except Exception:  # pragma: no cover
                pass
        self._procs = []

    def close(self):
        self.stop()
        super().close()


def _wav_seconds(path: str) -> float | None:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return None


class _Loop:
    """A sound file played `repeat` times (0 = until stopped) from a background thread; ``stop()`` ends it."""

    def __init__(self, dev: AudioDevice, path: str, volume: float, repeat: int):
        self.dev, self.path, self.volume, self.repeat = dev, path, volume, max(0, int(repeat))
        self.plays = 0
        self._stop = threading.Event()
        self._cur = None
        self._thread = threading.Thread(target=self._run, name="audio-loop", daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        length = _wav_seconds(self.path)
        while not self._stop.is_set() and (not self.repeat or self.plays < self.repeat):
            self._cur = self.dev._play(self.path, self.volume, track=False)
            self.plays += 1
            if self._cur is None or self._cur is False:
                if self.dev.backend is None and AudioDevice.player is None:
                    # no sound system (e.g. tests): keep counting plays at the file's pace
                    if self._stop.wait(length or 0.05):
                        return
                    continue
                return
            if hasattr(self._cur, "wait"):
                while not self._stop.is_set():
                    try:
                        self._cur.wait(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            elif self._stop.wait(length or 1.0):  # a GUI player: wait for the length of the file
                return

    def stop(self):
        self._stop.set()
        cur = self._cur
        if cur is not None and cur is not False:
            try:
                cur.terminate() if hasattr(cur, "terminate") else cur.stop()
            except Exception:  # pragma: no cover
                pass

    terminate = stop


DRIVERS = {"virtual": VirtualDevice, "arduino": ArduinoDevice, "serial": SerialDevice, "audio": AudioDevice}


def drivers() -> dict:
    """Every device driver: the core ones and those of :mod:`.iodrivers`, :mod:`.pumps` and :mod:`.scales`."""
    if "scale" not in DRIVERS:
        from . import iodrivers  # noqa: F401  (registers its drivers)
        from .pumps import SyringePumpDevice
        from .scales import ScaleDevice

        DRIVERS.update({"syringe_pump": SyringePumpDevice, "scale": ScaleDevice})
    return DRIVERS


# ======================================================================================== manager
class DeviceManager:
    """All the I/O devices of a project. Unknown device names used by procedures become virtual devices.

    Thread-safe: tests run in their own threads, the GUI polls the status and a service thread sends the
    watchdog heartbeats of boards that have one (so a paused test or a stalled camera does not trip it), runs the
    temperature controllers and times pulse sequences (:meth:`pulse_sequence`) on the computer's clock.

    Several tests at once each see their own box through a :class:`DeviceView`; input changes are fanned out to
    every subscriber (:meth:`subscribe`) so that one test never consumes another test's lever presses.

    ``transports`` maps device name -> file-like object (write/read/in_waiting) to drive arduino/serial
    devices without hardware (tests)."""

    MAX_QUEUE = 10000

    def __init__(self, configs=(), open: bool = True, transports: dict | None = None):
        self.configs = [dict(c) for c in (configs or []) if c.get("enabled", True)]
        self.devices: dict[str, Device] = {}
        self.configured = bool(self.configs)
        self._lock = threading.RLock()
        self._subs: dict[int, tuple[set | None, deque]] = {}
        self._sub_seq = 0
        self._wd_seen: dict[str, int] = {}
        self._ka_stop = threading.Event()
        self._ka_thread: threading.Thread | None = None
        self._sched: list = []  # timed outputs: (due monotonic, seq, device, channel, value, max_s)
        self._sched_seq = 0
        self._sched_wake = threading.Event()
        for c in self.configs:
            cls = drivers().get(c.get("type", "virtual"), VirtualDevice)
            dev = cls(c, (transports or {}).get(c.get("name")))
            self.devices[dev.name] = dev
        self._kinds_cache: dict = {}
        if open:
            self.open()

    @classmethod
    def from_project(cls, project, open: bool = True) -> "DeviceManager":
        return cls(project.io_devices or [], open=open)

    def open(self):
        with self._lock:
            for d in self.devices.values():
                if not d.connected:
                    try:
                        d.open()
                    except Exception as e:  # pragma: no cover - hardware dependent
                        d._error(f"{d.name}: {e}")
            self._start_keepalive()

    SERVICE_S = 0.1  # controller period

    def _needs_service(self) -> bool:
        return bool(self._sched) or any(d.keepalive_period() or d.thermostats for d in self.devices.values())

    def _start_keepalive(self):
        if self._ka_thread is not None and self._ka_thread.is_alive():
            return
        if not self._needs_service():
            return
        self._ka_stop.clear()
        self._ka_thread = threading.Thread(target=self._keepalive_loop, name="io-service", daemon=True)
        self._ka_thread.start()

    def _keepalive_loop(self):
        next_service = 0.0
        while not self._ka_stop.is_set():
            periods = []
            now = time.monotonic()
            self._run_schedule(now)
            for d in list(self.devices.values()):
                per = d.keepalive_period()
                if per:
                    periods.append(per)
                    try:
                        d.keepalive()
                    except Exception as e:  # pragma: no cover - hardware dependent
                        d._error(f"{d.name}: keep-alive failed: {e}")
                if d.thermostats:
                    periods.append(self.SERVICE_S)
                    if now >= next_service:
                        with self._lock:
                            try:
                                d.service(now)
                            except Exception as e:  # pragma: no cover - hardware dependent
                                d._error(f"{d.name}: {e}")
            if now >= next_service:
                next_service = now + self.SERVICE_S
            with self._lock:
                due = self._sched[0][0] if self._sched else None
                if not periods and due is None:
                    # exit under the lock: pulse_sequence (which queues under it) then sees no thread and
                    # starts a new one, instead of a thread about to return
                    if self._ka_thread is threading.current_thread():
                        self._ka_thread = None
                    return
            wait = max(0.02, min(periods) / 4) if periods else 0.5
            if due is not None:
                wait = min(wait, max(0.0, due - time.monotonic()))
            self._sched_wake.clear()
            if wait > 0:
                self._sched_wake.wait(wait)

    # -- timed outputs (pulse sequences)
    def _run_schedule(self, now):
        import heapq

        while True:
            with self._lock:  # all_off / close / _cancel_schedule / pulse_sequence edit the heap under the lock
                if not self._sched or self._sched[0][0] > now + 0.0005:
                    return
                _due, _seq, dev, ch, value, max_s = heapq.heappop(self._sched)
                d = self.devices.get(dev)
                if d is not None:
                    try:
                        d.set_output(ch, value, max_s=max_s)
                    except Exception as e:  # pragma: no cover - hardware dependent
                        d._error(f"{d.name}: {e}")

    def pulse_sequence(self, device: str, channel: str, pulses, level: float = 1) -> bool:
        """Switch an output on for each (delay from now in s, width in s) on the computer's clock (about 1 ms
        jitter), independently of the video frames. Boards that time pulses switch each one off themselves."""
        import heapq

        with self._lock:
            d = self.device(device)
            self._cancel_schedule(device, channel)
            t0 = time.monotonic()
            hw_off = getattr(d, "hardware_pulses", False) and d.kind(channel) != "pwm"
            for delay, width in pulses:
                for dt, v, mx in ((delay, level, width if hw_off else None), (delay + width, 0, None)):
                    if v == 0 and hw_off:
                        v = None  # the board switches it off; still update the shown state
                    self._sched_seq += 1
                    heapq.heappush(self._sched, (t0 + max(0.0, dt), self._sched_seq, device, channel,
                                                 0 if v is None else v, mx if v else None))
            if self._ka_thread is None or not self._ka_thread.is_alive():
                self._ka_stop.clear()
                self._ka_thread = threading.Thread(target=self._keepalive_loop, name="io-service", daemon=True)
                self._ka_thread.start()
            self._sched_wake.set()
            return True

    def _cancel_schedule(self, device, channel):
        import heapq

        with self._lock:
            n = len(self._sched)
            self._sched = [e for e in self._sched if not (e[2] == device and e[3] == channel)]
            if len(self._sched) != n:
                heapq.heapify(self._sched)

    def close(self):
        self._ka_stop.set()
        self._sched_wake.set()
        with self._lock:
            self._sched = []
        th = self._ka_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(2.0)
        self._ka_thread = None
        with self._lock:
            for d in self.devices.values():
                try:
                    d.all_off()
                except Exception:  # pragma: no cover
                    pass
                d.close()

    @property
    def errors(self) -> list[str]:
        with self._lock:
            return [e for d in self.devices.values() for e in d.errors]

    def has(self, name: str) -> bool:
        return name in self.devices

    def resolve_name(self, name: str) -> str:
        """The device a name used by procedures refers to (itself; see DeviceView)."""
        return name

    def device(self, name: str, create: bool = True) -> Device | None:
        with self._lock:
            d = self.devices.get(name)
            if d is None and create:
                d = VirtualDevice({"name": name or "virtual"})
                d.open()
                self.devices[d.name] = d
            return d

    def find_channel(self, channel: str, kinds=None) -> str | None:
        """Name of the first device that has this channel (optionally of one of these kinds)."""
        for d in list(self.devices.values()):
            k = d.kind(channel)
            if k is not None and (kinds is None or k in kinds):
                return d.name
        return None

    def find_type(self, type_: str) -> str | None:
        return next((d.name for d in list(self.devices.values()) if d.type == type_), None)

    def channel_kind(self, device: str, channel: str) -> str | None:
        d = self.devices.get(device)
        return d.kind(channel) if d else None

    def channel_config(self, device: str, channel: str) -> dict:
        d = self.devices.get(device)
        return dict(d.channels.get(channel, {})) if d else {}

    def status(self, devices=None) -> list[tuple[str, str, str, float]]:
        out = []
        with self._lock:
            for d in self.devices.values():
                if devices is not None and d.name not in devices:
                    continue
                for n, c in d.channels.items():
                    k = c.get("kind", "input")
                    v = d.inputs.get(n, 0) if k in INPUT_KINDS else d.outputs.get(n, 0)
                    out.append((d.name, n, k, v))
        return out

    def set_output(self, device: str, channel: str, value: float, max_s: float | None = None):
        """False when the device could not switch it (e.g. disconnected); None / True otherwise."""
        with self._lock:
            return self.device(device).set_output(channel, value, max_s=max_s)

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        with self._lock:
            return self.device(device).pulse_train(channel, period_s, width_s, count)

    def stop_train(self, device, channel) -> bool:
        with self._lock:
            self._cancel_schedule(device, channel)
            return self.device(device).stop_train(channel)

    def send(self, device: str, text: str) -> bool:
        with self._lock:
            d = self.devices.get(device)
            return d.send(text) if d else False

    def audio(self, device: str, cmd: str, **kw) -> bool:
        with self._lock:
            d = self.devices.get(device)
            return d.audio(cmd, **kw) if d else False

    def pump(self, device: str, channel: str, op: str, **kw) -> bool:
        with self._lock:
            d = self.devices.get(device)
            return d.pump(channel, op, **kw) if d else False

    def control(self, device: str, channel: str, op: str, **kw) -> bool:
        with self._lock:
            d = self.devices.get(device)
            ok = d.control(channel, op, **kw) if d else False
        self._start_keepalive()
        return ok

    def notify(self, subject: str, text: str, kinds=None, to=None) -> bool:
        """Send an alert through every alert device (e-mail / SMS, in the background); see Device.notify."""
        kw = {k: v for k, v in (("kinds", kinds), ("to", to)) if v}
        with self._lock:
            sent = [d.notify(subject, text, **kw) for d in self.devices.values() if d.type == "notify"]
        return any(sent)

    def set_input(self, device: str, channel: str, value: float):
        with self._lock:
            d = self.device(device)
            if isinstance(d, VirtualDevice):
                d.set_input(channel, value)

    # -- inputs (fanned out to subscribers)
    def subscribe(self, devices=None) -> int:
        """A private queue of input changes (of these devices; None = all) for :meth:`read_inputs`."""
        with self._lock:
            self._sub_seq += 1
            self._subs[self._sub_seq] = (set(devices) if devices is not None else None, deque(maxlen=self.MAX_QUEUE))
            return self._sub_seq

    def unsubscribe(self, sub: int):
        with self._lock:
            self._subs.pop(sub, None)

    def _poll_all(self) -> list[tuple]:
        out = []
        for d in list(self.devices.values()):
            try:
                changes = d.poll_ex()
            except Exception as e:  # pragma: no cover - hardware dependent
                d._error(f"{d.name}: {e}")
                continue
            for ch, v, ms in changes:
                out.append((d.name, ch, d.kind(ch) or "input", v, ms))
            n = d.watchdog_fired
            if n != self._wd_seen.get(d.name, 0):
                self._wd_seen[d.name] = n
                out.append((d.name, "", "watchdog", 1, None))
        for filt, q in self._subs.values():
            q.extend(c for c in out if filt is None or c[0] in filt)
        return out

    def read_inputs_ex(self, sub: int | None = None) -> list[tuple]:
        """Like :meth:`read_inputs` with the board time: [(device, channel, kind, value, ms or None)].
        kind "watchdog" (channel "") reports that the device's watchdog switched its outputs off."""
        with self._lock:
            out = self._poll_all()
            if sub is None:
                return out
            q = self._subs.get(sub)
            if q is None:
                return []
            res = list(q[1])
            q[1].clear()
            return res

    def read_inputs(self, sub: int | None = None) -> list[tuple[str, str, str, float]]:
        """Input changes since the last call: [(device, channel, kind, value)] (of subscriber `sub` if given)."""
        return [c[:4] for c in self.read_inputs_ex(sub)]

    def all_off(self):
        with self._lock:
            self._sched = []
            for d in self.devices.values():
                d.all_off()


class DeviceView:
    """One test's view of a shared :class:`DeviceManager` (several tests at once): the test only sees its own
    box ``device``. Every configured hardware device name used by the procedures resolves to that box, channel
    names are looked up in it, and only its input changes are delivered. Other names become virtual devices
    private to the test. ``device`` None: no hardware at all (simulated outputs). Audio devices (the computer's
    speakers) stay shared."""

    SHARED_TYPES = ("audio", "notify")

    def __init__(self, manager: DeviceManager, device: str | None):
        self.manager = manager
        self.alias = device or None
        if self.alias is not None and not manager.has(self.alias):
            raise KeyError(f"I/O device '{self.alias}' is not configured")
        self.devices: dict[str, Device] = {}  # private virtual devices
        self._sub = manager.subscribe({self.alias} if self.alias else set())
        self._released = False

    # -- name resolution
    @property
    def configured(self) -> bool:
        return self.alias is not None

    def _shared(self, name) -> bool:
        d = self.manager.devices.get(name)
        return d is not None and d.type in self.SHARED_TYPES

    def _map(self, name: str) -> str:
        if self.alias is not None and name and self.manager.has(name) and not self._shared(name):
            return self.alias  # the procedures' box name means "this test's box"
        return name

    def resolve_name(self, name: str) -> str:
        return self._map(name)

    def has(self, name: str) -> bool:
        name = self._map(name)
        return name == self.alias and name is not None or name in self.devices or self._shared(name)

    def device(self, name: str, create: bool = True) -> Device | None:
        name = self._map(name)
        if self.alias is not None and name == self.alias or self._shared(name):
            return self.manager.device(name, create=False)
        d = self.devices.get(name)
        if d is None and create:
            d = VirtualDevice({"name": name or "virtual"})
            d.open()
            self.devices[d.name] = d
        return d

    def _own(self):
        return ([self.manager.devices[self.alias]] if self.alias is not None else []) + list(self.devices.values())

    def find_channel(self, channel: str, kinds=None) -> str | None:
        for d in self._own():
            k = d.kind(channel)
            if k is not None and (kinds is None or k in kinds):
                return d.name
        return None

    def find_type(self, type_: str) -> str | None:
        if type_ in self.SHARED_TYPES:
            return self.manager.find_type(type_)
        return next((d.name for d in self._own() if d.type == type_), None)

    def channel_kind(self, device: str, channel: str) -> str | None:
        d = self.device(device, create=False)
        return d.kind(channel) if d else None

    def channel_config(self, device: str, channel: str) -> dict:
        d = self.device(device, create=False)
        return dict(d.channels.get(channel, {})) if d else {}

    @property
    def errors(self) -> list[str]:
        return [e for d in self._own() for e in d.errors]

    def status(self) -> list[tuple[str, str, str, float]]:
        out = self.manager.status({self.alias}) if self.alias is not None else []
        for d in self.devices.values():
            for n, c in d.channels.items():
                k = c.get("kind", "input")
                out.append((d.name, n, k, d.inputs.get(n, 0) if k in INPUT_KINDS else d.outputs.get(n, 0)))
        return out

    # -- outputs
    def _call(self, name, fn):
        name = self._map(name)
        if self.alias is not None and name == self.alias or self._shared(name):
            with self.manager._lock:
                return fn(self.manager.devices[name])
        return fn(self.device(name))

    def set_output(self, device: str, channel: str, value: float, max_s: float | None = None):
        return self._call(device, lambda d: d.set_output(channel, value, max_s=max_s))

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        return self._call(device, lambda d: d.pulse_train(channel, period_s, width_s, count))

    def stop_train(self, device, channel) -> bool:
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            self.manager._cancel_schedule(name, channel)
        return self._call(device, lambda d: d.stop_train(channel))

    def send(self, device: str, text: str) -> bool:
        return self._call(device, lambda d: d.send(text)) if self.has(device) else False

    def audio(self, device: str, cmd: str, **kw) -> bool:
        return self._call(device, lambda d: d.audio(cmd, **kw)) if self.has(device) else False

    def pump(self, device: str, channel: str, op: str, **kw) -> bool:
        return self._call(device, lambda d: d.pump(channel, op, **kw)) if self.has(device) else False

    def control(self, device: str, channel: str, op: str, **kw) -> bool:
        ok = self._call(device, lambda d: d.control(channel, op, **kw)) if self.has(device) else False
        self.manager._start_keepalive()
        return ok

    def notify(self, subject: str, text: str, kinds=None, to=None) -> bool:
        return self.manager.notify(subject, text, kinds=kinds, to=to)

    def pulse_sequence(self, device: str, channel: str, pulses, level: float = 1) -> bool:
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            return self.manager.pulse_sequence(name, channel, pulses, level)
        return False  # private simulated devices: timed by the engine

    def set_input(self, device: str, channel: str, value: float):
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            self.manager.set_input(name, channel, value)
            return
        d = self.device(name)
        if isinstance(d, VirtualDevice):
            d.set_input(channel, value)

    # -- inputs
    def read_inputs_ex(self) -> list[tuple]:
        out = self.manager.read_inputs_ex(self._sub) if not self._released else []
        for d in list(self.devices.values()):
            out += [(d.name, ch, d.kind(ch) or "input", v, ms) for ch, v, ms in d.poll_ex()]
        return out

    def read_inputs(self) -> list[tuple[str, str, str, float]]:
        return [c[:4] for c in self.read_inputs_ex()]

    def all_off(self):
        for d in self._own():
            if d.name == self.alias:
                with self.manager._lock:
                    d.all_off()
            else:
                d.all_off()

    def release(self):
        """The test is over: its box's outputs off, stop receiving input changes (the manager stays open)."""
        if self._released:
            return
        self._released = True
        try:
            self.all_off()
        except Exception:  # pragma: no cover - hardware dependent
            pass
        self.manager.unsubscribe(self._sub)

    close = release
