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
  ``AudioDevice.player``; :mod:`.audio`).
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

The drivers' base class and the ``virtual`` device are in :mod:`.iobase`. Device types, channel kinds and the
configuration rules (new_device, watchdog_ms...) are in :mod:`.ioconfig`.
:func:`.iomeasures.io_measures` turns a test's I/O log (``Test.io_events``) into ANY-maze-style result measures.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from .audio import AudioDevice, _Loop, noise_samples, tone_samples, write_wav  # noqa: F401 (re-exported)
from .iobase import SHOCKER_MAX_ON_S, Device, VirtualDevice  # noqa: F401 (re-exported)
from .iocontrol import AnalogFilter, Thermostat, sensor_value  # noqa: F401 (re-exported)
from .ioconfig import INPUT_KINDS, watchdog_ms

FIRMWARE_ID = "MANYMAZE_IO"
LINE_OVERHEAD_B = 22  # bytes of an "S n first_ms period_us" batch line besides its samples


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
class _LineDevice(Device):
    """Base for devices that talk text lines over a serial port (or an injected transport)."""

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self._buf = b""
        self.sent: list[str] = []
        self._io_lock = threading.RLock()  # the keep-alive thread writes while a test thread reads / writes
        self._last_write = 0.0

    def open(self):
        if self.transport is None and self._injected is not None:
            self.transport = self._injected  # reopened (tests' transports)
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
        self._drop()

    def _drop(self):
        """Close the port without sending anything (after a failed write: the device is gone)."""
        with self._io_lock:
            if self.transport is not None:
                try:
                    self.transport.close()
                except Exception:  # pragma: no cover
                    pass
            self.transport = None
            self.connected = False

    def _encode(self, line: str) -> bytes:
        return (line + self.cfg.get("eol", "\n")).encode()

    def write_line(self, line: str) -> bool:
        """Send a line; False when it was not sent (not connected, or the write failed: the device is then marked
        disconnected and its port closed, so that the device manager opens it again)."""
        with self._io_lock:
            self.sent.append(line)
            if len(self.sent) > 5000:
                del self.sent[:1000]
            if self.transport is None or not self.connected:
                return False
            try:
                self.transport.write(self._encode(line))
                self._last_write = time.monotonic()
                return True
            except Exception as e:
                self._error(f"{self.name}: write failed: {e}")
                self._drop()
                return False

    def poll_ex(self) -> list[tuple[str, float, float | None]]:
        """As Device.poll_ex, but never waits for the port: while another thread uses it (e.g. a balance being
        read, a board's handshake) nothing is read now and the changes come with a later poll."""
        if not self._io_lock.acquire(blocking=False):
            return []
        try:
            self._read()
            return self._take_pending()
        finally:
            self._io_lock.release()

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

    def close(self):
        if self.connected:  # every output's "off" command before the port closes
            try:
                self.all_off()
            except Exception:  # pragma: no cover - hardware dependent
                pass
        super().close()

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

    ID_REPLY_S = 1.5  # identification replies expected this long after a "?"; later banners mean a reset

    def __init__(self, cfg: dict, transport=None):
        super().__init__(cfg, transport)
        self.version = ""
        self.resets = 0  # times the board was seen restarting (power or USB glitch) and was configured again
        self._configured = False
        self._sync_last: tuple[int, int] | None = None  # the board's last SYNC (pin, µs): "SYNC" alone repeats it
        self._query_until = 0.0
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
        self._configured = False
        self.version = ""
        deadline = time.monotonic() + handshake_s
        retry = 0.0
        with self._io_lock:  # the handshake's replies are not taken by a poll meanwhile
            while time.monotonic() < deadline and not self.version and self.connected:
                if time.monotonic() >= retry:  # most boards reset when the port opens: ask again every second
                    self._identify()
                    retry = time.monotonic() + 1.0
                for line in self.read_lines():
                    self._parse(line)
                if not self.version:
                    time.sleep(0.05)
            if not self.version:
                self._error(f"{self.name}: no reply from the mANY-MAZE firmware on {self.cfg.get('port', '?')}")
            if self.connected:
                self.configure()

    def _identify(self):
        self.write_line("?")
        self._query_until = time.monotonic() + self.ID_REPLY_S

    @property
    def firmware_version(self) -> tuple[int, ...]:
        """The firmware version from its identification ("MANYMAZE_IO 1.2 uno" -> (1, 2)); () if unknown."""
        parts = self.version.split()
        try:
            return tuple(int(x) for x in parts[1].split(".")) if len(parts) > 1 else ()
        except ValueError:
            return ()

    def configure(self):
        self._sync_last = None  # (Z forgets the last SYNC too)
        if self.write_line("Z"):
            for ch in self.outputs:  # the board starts again with every output off
                if self.kind(ch) in ("output", "pwm"):
                    self.outputs[ch] = 0
        dht_done = set()
        problems, refuse = bandwidth_check(self.cfg)
        for msg in problems:
            self._error(f"{self.name}: {msg}")
        always = self.firmware_version >= (1, 2)  # deadband -1: every sample reported (filters need them all)
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
                if refuse:
                    continue  # more samples than the serial link carries: not configured (see bandwidth_check)
                period = max(1, int(c.get("period_ms", 50)))
                # filtered channels need every sample (deadband -1; firmware before 1.2: changes only); fast
                # channels are sent in batches of ~10 ms
                deadband = (-1 if always else 0) if n in self.filters else int(c.get("deadband", 2))
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
        self._configured = True

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
            if self._configured and time.monotonic() > self._query_until:
                self._board_reset()

    def _board_reset(self):
        """The board printed its banner by itself: it restarted (power or USB glitch, brown-out from a load on its
        supply…) and lost its configuration and its outputs' states. It is configured again; the outputs are
        reported off (as after a watchdog, so that the test's I/O log shows them off)."""
        self.resets += 1
        self.watchdog_fired += 1
        for ch in self.outputs:
            self.outputs[ch] = 0
        self.errors.append(f"{self.name}: the board restarted (reset no. {self.resets}) — outputs off, configured "
                           "again")
        self.configure()

    def _read(self):
        for line in self.read_lines():
            self._parse(line)

    def close(self):
        if self.connected:
            self.write_line("R")  # every output off before the port closes (the board cannot see it close)
        super().close()

    def times_max_on(self, channel: str) -> bool:
        return self.kind(channel) == "output"  # W pin 1 max_ms; PWM levels are also cut off by the computer

    def set_output(self, channel: str, value: float, max_s: float | None = None) -> bool:
        """False when the command could not be sent (the output's state is then unchanged). With max_s (or a
        capped channel, see max_on_s) the board switches the output off itself after that time."""
        pin = self._pin(channel)
        if pin is None:
            self.outputs[channel] = value
            return True
        caps = [float(x) for x in (max_s, self.max_on_s(channel)) if x is not None and float(x) > 0]
        limit = f" {max(1, math.ceil(min(caps) * 1000))}" if value and caps else ""  # never 0 (= no limit)
        if self.channels[channel].get("kind") == "pwm":
            level = int(round(max(0.0, min(1.0, float(value))) * 255)) if value not in (True, False) else \
                (255 if value else 0)
            # firmware 1.2: P pin level max_ms (older boards ignore it; the computer cuts the level off too)
            line = f"P {pin} {level}{limit if level else ''}"
        else:
            line = f"W {pin} {1 if value else 0}{limit}"
        if not self.write_line(line):
            return False
        self.outputs[channel] = value
        return True

    def pulse_train(self, channel, period_s, width_s, count):
        pin = self._pin(channel)
        if pin is None:
            return False
        cap = self.max_on_s(channel)
        if cap:
            width_s = min(float(width_s), cap)
        # the board times trains in microseconds, or in milliseconds when the period exceeds 60 s (firmware 1.2)
        return self.write_line(f"T {pin} {period_s * 1000:.3f} {width_s * 1000:.3f} {int(count)}")

    def stop_train(self, channel):
        pin = self._pin(channel)
        if pin is None:
            return False
        return self.write_line(f"X {pin}")

    def sync_pulse(self, channel: str, width_s: float) -> bool:
        """``SYNC pin width_us`` (firmware 1.3: the end of the pulse is timed by a timer interrupt; ``SYNC`` alone
        repeats the last pulse), or ``W pin 1 max_ms`` with older firmware (millisecond timing)."""
        pin = self._pin(channel)
        if pin is None or self.kind(channel) != "output":
            if pin is not None:
                self._error(f"{self.name}: synchronisation pulses need a digital output, not '{channel}'")
            return False
        us = max(1, min(1_000_000, int(round(float(width_s) * 1e6))))
        if self.firmware_version >= (1, 3):
            line = "SYNC" if self._sync_last == (pin, us) else f"SYNC {pin} {us}"
            ok = self.write_line(line)
            if ok:
                self._sync_last = (pin, us)
        else:
            ok = self.write_line(f"W {pin} 1 {max(1, math.ceil(us / 1000))}")
        if ok:
            self._count_sync(channel)
        return ok

    def send(self, text: str) -> bool:
        return self.write_line(text)

    def all_off(self):
        for th in self.thermostats.values():
            th.target = th.setpoint = None
        if not self.write_line("R"):
            return  # not sent (a failed write closes the port): the outputs may still be on, not shown off
        for ch in self.outputs:
            self.outputs[ch] = 0


# ---------------------------------------------------------------------------------------- serial bandwidth
def serial_load(cfg: dict) -> tuple[float, float]:
    """(bytes per second the board's reports may need at worst, bytes per second the serial link carries) for an
    ``arduino`` or ``firmata`` device: fast analogue inputs (sent in batches), slower ones (one line per period
    when they change), load cells and encoders. (0, 0) for other device types."""
    typ = cfg.get("type")
    if typ not in ("arduino", "firmata"):
        return 0.0, 0.0
    baud = float(cfg.get("baud") or (115200 if typ == "arduino" else 57600))
    need = 0.0
    if typ == "firmata":
        analog = [c for c in cfg.get("channels") or [] if c.get("kind") in ("analog", "sensor")]
        if analog:
            ms = max(1, min(int(c.get("period_ms", 19) or 19) for c in analog))
            need += 3 * len(analog) * 1000.0 / ms  # one 3-byte message per pin and sampling interval
        return need, baud / 10.0
    for c in cfg.get("channels") or []:
        k = c.get("kind", "input")
        iface = c.get("interface", "analog") if k == "sensor" else None
        if c.get("derived") or c.get("pin") is None:
            continue
        if k == "analog" or iface == "analog":
            period = max(1, int(c.get("period_ms", 50) or 50))
            if period < 10:
                batch = max(1, min(16, 10 // period))
                need += 1000.0 / period * (5 + LINE_OVERHEAD_B / batch)  # " 1023" per sample + line overhead
            else:
                need += 1000.0 / period * 21  # "A 0 1023 4294967295" at worst once per period
        elif iface == "hx711":
            need += 1000.0 / max(100, int(c.get("period_ms", 100) or 100)) * 25
        elif k == "encoder":
            need += 50 * 20  # "E pin count ms" at most every 20 ms
    return need, baud / 10.0


def bandwidth_check(cfg: dict, warn_at: float = 0.7, refuse_at: float = 0.95) -> tuple[list[str], bool]:
    """Problems of a device configuration whose inputs could saturate its serial link (the board then waits
    while sending, delaying pulse edges, maximum on-time cut-offs and its watchdog): ([messages], refuse) —
    refuse when they need more than refuse_at of the link: an Arduino's analogue inputs are then not configured.
    The configuration dialog can call it to warn before the device is used."""
    need, cap = serial_load(cfg)
    if not cap or need <= warn_at * cap:
        return [], False
    pct = 100.0 * need / cap
    baud = int(cap * 10)
    if need > refuse_at * cap:
        what = "they are not configured" if cfg.get("type") == "arduino" else "reports will be lost"
        return [f"the analogue inputs need {need:.0f} bytes/s, {pct:.0f} % of what {baud} baud carries: {what} "
                "(sample them less often: a longer period_ms)"], cfg.get("type") == "arduino"
    return [f"warning: the inputs may use {pct:.0f} % of the serial link ({need:.0f} of {cap:.0f} bytes/s at "
            f"{baud} baud); keep it below {warn_at:.0%} so that the board never waits to send"], False


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
    temperature controllers, times pulse sequences (:meth:`pulse_sequence`) on the computer's clock, switches off
    outputs left on longer than their maximum on-time (:meth:`set_output` ``max_s``, a channel's ``max_on_s`` or
    role "shocker") on devices that cannot time it themselves, and reopens devices whose connection was lost.

    Several tests at once each see their own box through a :class:`DeviceView`; input changes are fanned out to
    every subscriber (:meth:`subscribe`) so that one test never consumes another test's lever presses. Devices are
    polled outside the manager's lock, each by one thread at a time, and a device busy in another thread (a
    balance being read) is skipped, so that no command waits for another device.

    Heartbeats: once tests report that they run (:meth:`tick`), heartbeats are only sent while one of them ticked
    within ``TICK_TIMEOUT_S``: if the program hangs, the boards' watchdogs switch their outputs off.

    ``transports`` maps device name -> file-like object (write/read/in_waiting) to drive arduino/serial
    devices without hardware (tests)."""

    MAX_QUEUE = 10000
    SERVICE_S = 0.1  # controller period
    RECONNECT_S = 5.0  # a lost device is opened again this often
    TICK_TIMEOUT_S = 10.0  # heartbeats stop when no test ticked for this long (generous: camera reconnections)
    HOST_RETRY_S = 0.2  # a host cut-off whose "off" could not be sent is tried again after this
    SYNC_SPIN_S = 0.002  # synchronisation pulses up to this long that the device cannot time: ended by waiting

    def __init__(self, configs=(), open: bool = True, transports: dict | None = None):
        self.configs = [dict(c) for c in (configs or []) if c.get("enabled", True)]
        self.devices: dict[str, Device] = {}
        self.configured = bool(self.configs)
        self._lock = threading.RLock()
        self._subs: dict[int, tuple[set | None, deque]] = {}
        self._sub_seq = 0
        self._default_sub: int | None = None  # read_inputs() without a subscriber
        self._wd_seen: dict[str, int] = {}
        self._ka_stop = threading.Event()
        self._ka_thread: threading.Thread | None = None
        self._sched: list = []  # timed outputs: (due monotonic, seq, device, channel, value, max_s)
        self._sched_seq = 0
        self._sched_wake = threading.Event()
        self._opening: set[str] = set()  # devices being opened (outside the lock)
        self._deadlines: dict[tuple[str, str], tuple[float, float]] = {}  # host cut-offs: (due, max_s)
        self._poll_locks: dict[str, threading.Lock] = {}
        self._ticks: dict[int, float] = {}
        self._next_reconnect = 0.0
        self._reconnecting: threading.Thread | None = None
        self._closed = False
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
            todo = [d for d in self.devices.values() if not d.connected and d.name not in self._opening]
            self._opening.update(d.name for d in todo)
        try:  # outside the lock: a board's handshake takes seconds, the other devices and tests go on meanwhile
            for d in todo:
                try:
                    d.open()
                except Exception as e:  # pragma: no cover - hardware dependent
                    d._error(f"{d.name}: {e}")
                if d.connected:
                    d.ever_connected = True
        finally:
            with self._lock:
                self._opening.difference_update(d.name for d in todo)
                self._start_keepalive()

    def _needs_service(self) -> bool:
        return bool(self._sched) or bool(self._deadlines) or bool(self._lost()) or \
            any(d.keepalive_period() or d.thermostats for d in self.devices.values())

    def _lost(self) -> list:
        """Devices that were connected and lost their connection (reopened by the service thread)."""
        return [d for d in self.devices.values() if d.ever_connected and not d.connected and not self._closed]

    def _start_keepalive(self, force: bool = False):
        if self._ka_thread is not None and self._ka_thread.is_alive():
            return
        if self._closed or not (force or self._needs_service()):
            return
        self._ka_stop.clear()
        self._ka_thread = threading.Thread(target=self._keepalive_loop, name="io-service", daemon=True)
        self._ka_thread.start()

    # -- program alive (heartbeat gating)
    def tick(self, owner=None):
        """A test using these devices is running normally (call it for every frame or safety tick): heartbeats go
        on. Without ticks for TICK_TIMEOUT_S the heartbeats stop and the boards' watchdogs switch outputs off."""
        self._ticks[id(owner)] = time.monotonic()

    def untick(self, owner=None):
        """The test is over: its ticks no longer gate the heartbeats."""
        self._ticks.pop(id(owner), None)

    def _alive(self) -> bool:
        ticks = list(self._ticks.values())
        return not ticks or time.monotonic() - max(ticks) < self.TICK_TIMEOUT_S

    def _keepalive_loop(self):
        next_service = 0.0
        while not self._ka_stop.is_set():
            periods = []
            now = time.monotonic()
            self._run_schedule(now)
            self._run_deadlines(now)
            alive = self._alive()
            for d in list(self.devices.values()):
                per = d.keepalive_period()
                if per:
                    periods.append(per)
                    if alive:
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
            self._reconnect(now)
            with self._lock:
                due = self._sched[0][0] if self._sched else None
                if self._deadlines:
                    dl = min(v[0] for v in self._deadlines.values())
                    due = dl if due is None else min(due, dl)
                if self._lost():
                    periods.append(1.0)
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

    def _reconnect(self, now):
        """Open again (in the background: a handshake takes seconds) the devices whose connection was lost."""
        if now < self._next_reconnect or self._closed:
            return
        th = self._reconnecting
        if th is not None and th.is_alive():
            return
        lost = self._lost()
        if not lost:
            return
        self._next_reconnect = now + self.RECONNECT_S

        def run():
            self.open()
            for d in lost:
                if d.connected:
                    d.errors.append(f"{d.name}: connection restored")

        self._reconnecting = threading.Thread(target=run, name="io-reconnect", daemon=True)
        self._reconnecting.start()

    # -- outputs: the host's maximum on-time
    def _set_output_dev(self, d: Device, channel: str, value, max_s=None):
        """Switch a device's output and arm (or clear) the computer's cut-off of outputs with a maximum on-time
        that the device cannot time itself (see Device.host_max_on)."""
        ok = d.set_output(channel, value, max_s=max_s)
        if ok is False:
            return ok
        limit = d.host_max_on(channel, max_s) if value else None
        key = (d.name, channel)
        with self._lock:
            if limit:
                self._deadlines[key] = (time.monotonic() + limit, limit)
                self._start_keepalive(force=True)
                self._sched_wake.set()
            else:
                self._deadlines.pop(key, None)
        return ok

    def _run_deadlines(self, now):
        with self._lock:
            due = [(k, v) for k, v in self._deadlines.items() if v[0] <= now + 0.0005]
            for key, (_t, limit) in due:
                d = self.devices.get(key[0])
                if d is None:
                    self._deadlines.pop(key, None)
                    continue
                try:
                    ok = d.set_output(key[1], 0) is not False
                except Exception as e:  # pragma: no cover - hardware dependent
                    d._error(f"{d.name}: {e}")
                    ok = False
                if ok:
                    self._deadlines.pop(key, None)
                    d._error(f"{d.name}: {key[1]} switched off by the computer after its maximum on-time "
                             f"({limit:g} s)")
                else:  # not sent: tried again shortly (and the device reopens)
                    self._deadlines[key] = (now + self.HOST_RETRY_S, limit)

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
                        self._set_output_dev(d, ch, value, max_s=max_s)
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
            self._start_keepalive(force=True)
            self._sched_wake.set()
            return True

    def _cancel_schedule(self, device, channel=None):
        """Drop the timed outputs of a channel (None: of every channel) of a device."""
        import heapq

        with self._lock:
            n = len(self._sched)
            self._sched = [e for e in self._sched if not (e[2] == device and channel in (None, e[3]))]
            if len(self._sched) != n:
                heapq.heapify(self._sched)

    def close(self):
        self._closed = True
        self._ka_stop.set()
        self._sched_wake.set()
        with self._lock:
            self._sched = []
            self._deadlines = {}
        th = self._ka_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(2.0)
        self._ka_thread = None
        rc = self._reconnecting
        if rc is not None and rc.is_alive() and rc is not threading.current_thread():
            rc.join(5.0)  # a device being reopened: closed below once its handshake is over
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
            return self._set_output_dev(self.device(device), channel, value, max_s=max_s)

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        with self._lock:
            return self.device(device).pulse_train(channel, period_s, width_s, count)

    def stop_train(self, device, channel) -> bool:
        with self._lock:
            self._cancel_schedule(device, channel)
            return self.device(device).stop_train(channel)

    def sync_pulse(self, device: str, channel: str, width_s: float) -> bool:
        """One synchronisation pulse (core.sync) by the device's fastest path: timed by the device when it can
        (Device.sync_pulse); otherwise the output is switched on at once and off after the width — by waiting here
        for pulses up to SYNC_SPIN_S, else from the service thread on the computer's clock (about 1 ms jitter)."""
        with self._lock:
            return self._sync_dev(self.device(device), channel, width_s)

    def _sync_dev(self, d: Device, channel: str, width_s: float) -> bool:
        import heapq

        width_s = max(0.0, float(width_s))
        r = d.sync_pulse(channel, width_s)
        if r is not None:
            return bool(r)
        if self._set_output_dev(d, channel, 1) is False:
            return False
        d._count_sync(channel)
        if width_s <= self.SYNC_SPIN_S:
            end = time.perf_counter() + width_s
            while time.perf_counter() < end:
                pass
            return self._set_output_dev(d, channel, 0) is not False
        self._sched_seq += 1
        heapq.heappush(self._sched, (time.monotonic() + width_s, self._sched_seq, d.name, channel, 0, None))
        self._start_keepalive(force=True)
        self._sched_wake.set()
        return True

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

    def _poll_device(self, d: Device) -> list[tuple]:
        """Read one device (one thread at a time) and fan its changes out to the subscribers. Outside the
        manager's lock: a slow device delays nobody else."""
        with self._lock:
            lk = self._poll_locks.setdefault(d.name, threading.Lock())
        with lk:
            try:
                changes = d.poll_ex()
            except Exception as e:  # pragma: no cover - hardware dependent
                d._error(f"{d.name}: {e}")
                return []
            out = [(d.name, ch, d.kind(ch) or "input", v, ms) for ch, v, ms in changes]
            with self._lock:
                n = d.watchdog_fired
                if n != self._wd_seen.get(d.name, 0):
                    self._wd_seen[d.name] = n
                    out.append((d.name, "", "watchdog", 1, None))
                if out:
                    for filt, q in self._subs.values():
                        if filt is None or d.name in filt:
                            q.extend(out)
            return out

    def _poll_all(self, devices=None) -> list[tuple]:
        out = []
        for d in list(self.devices.values()):
            if devices is None or d.name in devices:
                out += self._poll_device(d)
        return out

    def read_inputs_ex(self, sub: int | None = None) -> list[tuple]:
        """Like :meth:`read_inputs` with the board time: [(device, channel, kind, value, ms or None)].
        kind "watchdog" (channel "") reports that the device's watchdog switched its outputs off (or that the board
        restarted)."""
        with self._lock:
            if sub is None:
                if self._default_sub is None:
                    self._default_sub = self.subscribe()
                sub = self._default_sub
            q = self._subs.get(sub)
            filt = q[0] if q is not None else set()
        if q is None:
            return []
        self._poll_all(filt)  # only the subscriber's devices: each test reads its own box
        with self._lock:
            res = list(q[1])
            q[1].clear()
            return res

    def read_inputs(self, sub: int | None = None) -> list[tuple[str, str, str, float]]:
        """Input changes since the last call: [(device, channel, kind, value)] (of subscriber `sub` if given)."""
        return [c[:4] for c in self.read_inputs_ex(sub)]

    def input_value(self, device: str, channel: str):
        """An input's value now (e.g. a test's start switch): the device is read first, its changes still reach
        every subscriber; None for an unknown device."""
        d = self.devices.get(device)
        if d is None:
            return None
        self._poll_device(d)
        return d.inputs.get(channel)

    def all_off(self):
        with self._lock:
            self._sched = []
            self._deadlines = {}
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

    def tick(self, owner=None):
        """This test runs normally (see DeviceManager.tick)."""
        if not self._released:
            self.manager.tick(self)

    def untick(self, owner=None):
        self.manager.untick(self)

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
        return self._call(device, lambda d: self.manager._set_output_dev(d, channel, value, max_s=max_s))

    def pulse_train(self, device, channel, period_s, width_s, count) -> bool:
        return self._call(device, lambda d: d.pulse_train(channel, period_s, width_s, count))

    def stop_train(self, device, channel) -> bool:
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            self.manager._cancel_schedule(name, channel)
        return self._call(device, lambda d: d.stop_train(channel))

    def sync_pulse(self, device: str, channel: str, width_s: float) -> bool:
        """A synchronisation pulse on this test's box (see DeviceManager.sync_pulse)."""
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            return self.manager.sync_pulse(name, channel, width_s)
        d = self.device(name)  # a private simulated device
        return bool(d.sync_pulse(channel, width_s))

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

    def input_value(self, device: str, channel: str):
        """An input of this test's box (or of a private simulated device) now (see DeviceManager.input_value)."""
        name = self._map(device)
        if self.alias is not None and name == self.alias:
            return self.manager.input_value(name, channel)
        d = self.device(name, create=False)
        return d.inputs.get(channel) if d is not None else None

    def all_off(self):
        for d in self._own():
            if d.name == self.alias:
                with self.manager._lock:
                    self.manager._cancel_schedule(self.alias)  # its pending pulse sequences too
                    for key in [k for k in self.manager._deadlines if k[0] == self.alias]:
                        self.manager._deadlines.pop(key, None)
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
        self.manager.untick(self)

    close = release
