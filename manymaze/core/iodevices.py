"""Hardware I/O for live tests: digital/analogue inputs and outputs, pulse trains, encoders and audio.

Device configurations live in ``Project.io_devices`` (JSON)::

    {"name": "box1", "type": "arduino", "port": "/dev/cu.usbmodem1101", "baud": 115200, "watchdog_ms": 0,
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
  trains (optogenetics, pellet dispensers), analogue inputs, quadrature encoders, heartbeat watchdog.
* ``serial`` — any device driven by text lines; output channels have ``"on"``/``"off"`` command strings,
  input channels have ``"on"``/``"off"`` strings that are matched against received lines.
* ``audio`` — the computer's sound output: tones, white noise and sound files (WAV generated with NumPy and
  played with ``afplay``/``paplay``/``aplay``, or by a player installed by the GUI in ``AudioDevice.player``).

Channel kinds: ``input`` (digital in: lever, nose poke, beam, switch, TTL), ``output`` (digital out: TTL, relay,
light, pellet dispenser, door, shocker trigger, laser), ``pwm`` (analogue / PWM out, level 0..1), ``analog``
(analogue in) and ``encoder`` (quadrature rotary encoder, e.g. running wheel).

pyserial is optional (only needed for ``arduino`` and ``serial`` devices).

:func:`io_measures` turns a test's I/O log (``Test.io_events``) into ANY-maze-style result measures.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

DEVICE_TYPES = {
    "virtual": "Simulated device",
    "arduino": "Arduino (mANY-MAZE I/O firmware)",
    "serial": "Serial port (text commands)",
    "audio": "Audio output (speakers)",
}
CHANNEL_KINDS = {
    "input": "Digital input",
    "output": "Digital output",
    "pwm": "PWM / analogue output",
    "analog": "Analogue input",
    "encoder": "Rotary encoder",
}
INPUT_KINDS = ("input", "analog", "encoder")
OUTPUT_KINDS = ("output", "pwm")
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
        self._pending: list[tuple[str, float]] = []
        for n, c in self.channels.items():
            if c.get("kind", "input") in INPUT_KINDS:
                self.inputs[n] = 0
            else:
                self.outputs[n] = 0

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

    def _changed(self, channel: str, value: float):
        if self.inputs.get(channel) != value:
            self.inputs[channel] = value
            self._pending.append((channel, value))

    # -- API
    def poll(self) -> list[tuple[str, float]]:
        """Input changes since the last call: [(channel, value)]."""
        out, self._pending = self._pending, []
        return out

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

    def set_input(self, channel: str, value: float):
        """Simulate an input (virtual devices; ignored by hardware devices)."""

    def all_off(self):
        for ch in list(self.outputs):
            if self.outputs[ch]:
                self.set_output(ch, 0)


class VirtualDevice(Device):
    type = "virtual"

    def set_input(self, channel: str, value: float):
        if channel not in self.channels:
            self.channels[channel] = {"name": channel, "kind": "input"}
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
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:  # pragma: no cover
                pass
        self.transport = None
        self.connected = False

    def write_line(self, line: str) -> bool:
        self.sent.append(line)
        if self.transport is None:
            return False
        try:
            self.transport.write((line + self.cfg.get("eol", "\n")).encode())
            return True
        except Exception as e:  # pragma: no cover - hardware dependent
            self._error(f"{self.name}: write failed: {e}")
            return False

    def read_lines(self) -> list[str]:
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
        self.write_line(text)
        return True

    def set_output(self, channel: str, value: float, max_s: float | None = None):
        self.outputs[channel] = value
        c = self.channels.get(channel, {})
        cmd = c.get("on" if value else "off")
        if cmd is None:
            cmd = f"{channel} {'ON' if value else 'OFF'}" if not isinstance(value, float) or value in (0, 1) \
                else f"{channel} {value:g}"
        self.write_line(str(cmd))

    def poll(self):
        for line in self.read_lines():
            for n, c in self.channels.items():
                if c.get("kind", "input") != "input":
                    continue
                if c.get("on") and line == c["on"]:
                    self._changed(n, 1)
                elif c.get("off") and line == c["off"]:
                    self._changed(n, 0)
        return super().poll()


class ArduinoDevice(_LineDevice):
    """Arduino running firmware/manymaze_io (see firmware/README.md for the protocol)."""

    type = "arduino"
    hardware_pulses = True

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self.version = ""
        self.by_pin: dict[tuple[str, int], str] = {}
        for n, c in self.channels.items():
            k = c.get("kind", "input")
            tag = {"analog": "A", "encoder": "E"}.get(k, "D")
            if c.get("pin") is not None:
                self.by_pin[(tag, int(c["pin"]))] = n
        self._last_ping = 0.0

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
        for n, c in self.channels.items():
            k, pin = c.get("kind", "input"), c.get("pin")
            if pin is None:
                self._error(f"{self.name}: channel '{n}' has no pin")
                continue
            pin = int(pin)
            if k == "input":
                self.write_line(f"I {pin} {1 if c.get('pullup', True) else 0} {int(c.get('debounce_ms', 20))}")
            elif k in ("output", "pwm"):
                self.write_line(f"O {pin} {1 if c.get('invert') else 0}")
            elif k == "analog":
                self.write_line(f"A {pin} {int(c.get('period_ms', 50))} {int(c.get('deadband', 2))}")
            elif k == "encoder":
                if c.get("pin_b") is None:
                    self._error(f"{self.name}: encoder '{n}' needs pin B")
                    continue
                self.write_line(f"E {pin} {int(c['pin_b'])}")
        wd = int(self.cfg.get("watchdog_ms", 0) or 0)
        if wd:
            self.write_line(f"H {wd}")
        self.write_line("Q")

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
                v = 1 - int(raw) if c.get("pullup", True) else int(raw)
                if c.get("invert"):
                    v = 1 - v
            elif tag == "A":
                v = raw * float(c.get("scale", 1.0))
            else:
                v = int(raw)
            self._changed(n, v)
        elif tag == "ERR":
            self._error(f"{self.name}: firmware error: {' '.join(parts[1:])}")
        elif tag == "WATCHDOG":
            self._error(f"{self.name}: watchdog fired — all outputs switched off")
        elif tag.startswith(FIRMWARE_ID):
            self.version = line

    def poll(self):
        for line in self.read_lines():
            self._parse(line)
        wd = int(self.cfg.get("watchdog_ms", 0) or 0)
        if wd and self.connected and time.monotonic() - self._last_ping > wd / 3000:
            self._last_ping = time.monotonic()
            self.write_line(".")
        return super().poll()

    def set_output(self, channel: str, value: float, max_s: float | None = None):
        pin = self._pin(channel)
        self.outputs[channel] = value
        if pin is None:
            return
        if self.channels[channel].get("kind") == "pwm" and value not in (0, 1, True, False):
            self.write_line(f"P {pin} {int(round(max(0.0, min(1.0, float(value))) * 255))}")
            return
        if self.channels[channel].get("kind") == "pwm":
            self.write_line(f"P {pin} {255 if value else 0}")
            return
        extra = f" {int(max_s * 1000)}" if (value and max_s) else ""
        self.write_line(f"W {pin} {1 if value else 0}{extra}")

    def pulse_train(self, channel, period_s, width_s, count):
        pin = self._pin(channel)
        if pin is None:
            return False
        self.write_line(f"T {pin} {period_s * 1000:.3f} {width_s * 1000:.3f} {int(count)}")
        return True

    def stop_train(self, channel):
        pin = self._pin(channel)
        if pin is None:
            return False
        self.write_line(f"X {pin}")
        return True

    def send(self, text: str) -> bool:
        self.write_line(text)
        return True

    def all_off(self):
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
        return self._play(str(path), vol if cmd == "file" else 1.0)

    def _play(self, path: str, volume: float) -> bool:
        if AudioDevice.player is not None:
            try:
                self._procs.append(AudioDevice.player(path, volume))
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
            self._procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
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


DRIVERS = {"virtual": VirtualDevice, "arduino": ArduinoDevice, "serial": SerialDevice, "audio": AudioDevice}


# ======================================================================================== manager
class DeviceManager:
    """All the I/O devices of a project. Unknown device names used by procedures become virtual devices.

    ``transports`` maps device name -> file-like object (write/read/in_waiting) to drive arduino/serial
    devices without hardware (tests)."""

    def __init__(self, configs=(), open: bool = True, transports: dict | None = None):
        self.configs = [dict(c) for c in (configs or []) if c.get("enabled", True)]
        self.devices: dict[str, Device] = {}
        self.configured = bool(self.configs)
        for c in self.configs:
            cls = DRIVERS.get(c.get("type", "virtual"), VirtualDevice)
            dev = cls(c, (transports or {}).get(c.get("name")))
            self.devices[dev.name] = dev
        self._kinds_cache: dict = {}
        if open:
            self.open()

    @classmethod
    def from_project(cls, project, open: bool = True) -> "DeviceManager":
        return cls(getattr(project, "io_devices", None) or [], open=open)

    def open(self):
        for d in self.devices.values():
            if not d.connected:
                try:
                    d.open()
                except Exception as e:  # pragma: no cover - hardware dependent
                    d._error(f"{d.name}: {e}")

    def close(self):
        for d in self.devices.values():
            try:
                d.all_off()
            except Exception:  # pragma: no cover
                pass
            d.close()

    @property
    def errors(self) -> list[str]:
        return [e for d in self.devices.values() for e in d.errors]

    def has(self, name: str) -> bool:
        return name in self.devices

    def device(self, name: str, create: bool = True) -> Device | None:
        d = self.devices.get(name)
        if d is None and create:
            d = VirtualDevice({"name": name or "virtual"})
            d.open()
            self.devices[d.name] = d
        return d

    def find_channel(self, channel: str, kinds=None) -> str | None:
        """Name of the first device that has this channel (optionally of one of these kinds)."""
        for d in self.devices.values():
            k = d.kind(channel)
            if k is not None and (kinds is None or k in kinds):
                return d.name
        return None

    def find_type(self, type_: str) -> str | None:
        return next((d.name for d in self.devices.values() if d.type == type_), None)

    def channel_kind(self, device: str, channel: str) -> str | None:
        d = self.devices.get(device)
        return d.kind(channel) if d else None

    def channel_config(self, device: str, channel: str) -> dict:
        d = self.devices.get(device)
        return dict(d.channels.get(channel, {})) if d else {}

    def status(self) -> list[tuple[str, str, str, float]]:
        out = []
        for d in self.devices.values():
            for n, c in d.channels.items():
                k = c.get("kind", "input")
                v = d.inputs.get(n, 0) if k in INPUT_KINDS else d.outputs.get(n, 0)
                out.append((d.name, n, k, v))
        return out

    def set_output(self, device: str, channel: str, value: float, max_s: float | None = None):
        self.device(device).set_output(channel, value, max_s=max_s)

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        return self.device(device).pulse_train(channel, period_s, width_s, count)

    def stop_train(self, device, channel) -> bool:
        return self.device(device).stop_train(channel)

    def send(self, device: str, text: str) -> bool:
        d = self.devices.get(device)
        return d.send(text) if d else False

    def audio(self, device: str, cmd: str, **kw) -> bool:
        d = self.devices.get(device)
        return d.audio(cmd, **kw) if d else False

    def set_input(self, device: str, channel: str, value: float):
        d = self.device(device)
        if isinstance(d, VirtualDevice):
            d.set_input(channel, value)

    def read_inputs(self) -> list[tuple[str, str, str, float]]:
        """Input changes since the last call: [(device, channel, kind, value)]."""
        out = []
        for d in list(self.devices.values()):
            try:
                changes = d.poll()
            except Exception as e:  # pragma: no cover - hardware dependent
                d._error(f"{d.name}: {e}")
                continue
            for ch, v in changes:
                out.append((d.name, ch, d.kind(ch) or "input", v))
        return out

    def all_off(self):
        for d in self.devices.values():
            d.all_off()


# ======================================================================================== measures
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
