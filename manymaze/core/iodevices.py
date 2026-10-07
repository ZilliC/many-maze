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
* ``audio`` — the computer's sound output: tones, white noise and sound files (WAV generated with NumPy and
  played with ``afplay``/``paplay``/``aplay``, or by a player installed by the GUI in ``AudioDevice.player``).

Channel kinds: ``input`` (digital in: lever, nose poke, beam, switch, TTL), ``output`` (digital out: TTL, relay,
light, pellet dispenser, door, shocker trigger, laser), ``pwm`` (analogue / PWM out, level 0..1), ``analog``
(analogue in) and ``encoder`` (quadrature rotary encoder, e.g. running wheel).

pyserial is optional (only needed for ``arduino`` and ``serial`` devices).

Device types, channel kinds and the configuration rules (new_device, watchdog_ms...) are in :mod:`.ioconfig`,
re-exported here. :func:`io_measures` (in :mod:`.iomeasures`, re-exported here) turns a test's I/O log
(``Test.io_events``) into ANY-maze-style result measures.
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

from .ioconfig import (AUDIO_BACKENDS, CHANNEL_FIELDS, CHANNEL_KINDS, DEFAULT_WATCHDOG_MS,  # noqa: F401
                       DEVICE_FIELDS, DEVICE_TYPES, INPUT_KINDS, OUTPUT_KINDS, new_device, next_free_pin, watchdog_ms)
from .iomeasures import io_measures  # noqa: F401  (re-exported)

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

    def _changed(self, channel: str, value: float, ms: float | None = None):
        if self.inputs.get(channel) != value:
            self.inputs[channel] = value
            self._pending.append((channel, value, ms))

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
        with self._io_lock:
            self.sent.append(line)
            if len(self.sent) > 5000:
                del self.sent[:1000]
            if self.transport is None:
                return False
            try:
                self.transport.write((line + self.cfg.get("eol", "\n")).encode())
                self._last_write = time.monotonic()
                return True
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: write failed: {e}")
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
        for n, c in self.channels.items():
            k = c.get("kind", "input")
            tag = {"analog": "A", "encoder": "E"}.get(k, "D")
            if c.get("pin") is not None:
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
            ms = None
            if len(parts) >= 4:  # the board's millis() clock
                try:
                    ms = int(parts[3])
                except ValueError:
                    ms = None
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

    Thread-safe: tests run in their own threads, the GUI polls the status and a keep-alive thread sends the
    watchdog heartbeats of boards that have one (so a paused test or a stalled camera does not trip it).

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
        with self._lock:
            for d in self.devices.values():
                if not d.connected:
                    try:
                        d.open()
                    except Exception as e:  # pragma: no cover - hardware dependent
                        d._error(f"{d.name}: {e}")
            self._start_keepalive()

    def _start_keepalive(self):
        if self._ka_thread is not None and self._ka_thread.is_alive():
            return
        if not any(d.keepalive_period() for d in self.devices.values()):
            return
        self._ka_stop.clear()
        self._ka_thread = threading.Thread(target=self._keepalive_loop, name="io-keepalive", daemon=True)
        self._ka_thread.start()

    def _keepalive_loop(self):
        while not self._ka_stop.is_set():
            periods = []
            for d in list(self.devices.values()):
                per = d.keepalive_period()
                if per:
                    periods.append(per)
                    try:
                        d.keepalive()
                    except Exception as e:  # pragma: no cover - hardware dependent
                        d._error(f"{d.name}: keep-alive failed: {e}")
            if not periods:
                return
            self._ka_stop.wait(max(0.02, min(periods) / 4))

    def close(self):
        self._ka_stop.set()
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
        with self._lock:
            self.device(device).set_output(channel, value, max_s=max_s)

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        with self._lock:
            return self.device(device).pulse_train(channel, period_s, width_s, count)

    def stop_train(self, device, channel) -> bool:
        with self._lock:
            return self.device(device).stop_train(channel)

    def send(self, device: str, text: str) -> bool:
        with self._lock:
            d = self.devices.get(device)
            return d.send(text) if d else False

    def audio(self, device: str, cmd: str, **kw) -> bool:
        with self._lock:
            d = self.devices.get(device)
            return d.audio(cmd, **kw) if d else False

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
            for d in self.devices.values():
                d.all_off()


class DeviceView:
    """One test's view of a shared :class:`DeviceManager` (several tests at once): the test only sees its own
    box ``device``. Every configured hardware device name used by the procedures resolves to that box, channel
    names are looked up in it, and only its input changes are delivered. Other names become virtual devices
    private to the test. ``device`` None: no hardware at all (simulated outputs). Audio devices (the computer's
    speakers) stay shared."""

    SHARED_TYPES = ("audio",)

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
        self._call(device, lambda d: d.set_output(channel, value, max_s=max_s))

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        return self._call(device, lambda d: d.pulse_train(channel, period_s, width_s, count))

    def stop_train(self, device, channel) -> bool:
        return self._call(device, lambda d: d.stop_train(channel))

    def send(self, device: str, text: str) -> bool:
        return self._call(device, lambda d: d.send(text)) if self.has(device) else False

    def audio(self, device: str, cmd: str, **kw) -> bool:
        return self._call(device, lambda d: d.audio(cmd, **kw)) if self.has(device) else False

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
