"""More I/O device drivers: USB-serial cable control lines, Firmata boards, National Instruments DAQ devices,
LabJack devices and e-mail / SMS alerts. Importing the module registers them in :data:`.iodevices.DRIVERS`.

* ``serial_lines`` — any USB-serial adapter or cable as a tiny TTL interface (the role of ANY-maze's USB TTL
  cable): outputs on the RTS and DTR lines, inputs from CTS, DSR, RI and CD (channel ``pin`` = line name).
  The adapter's lines are RS-232 / TTL levels depending on the cable; use an opto-isolator for anything else.
* ``firmata`` — a board running StandardFirmata (Arduino IDE ▸ Examples ▸ Firmata), 57600 baud: digital inputs
  (with pull-up), PIR detectors, digital and PWM outputs, analogue inputs and analogue sensors. The protocol is
  implemented here (no extra package).
* ``nidaq`` — National Instruments devices through the ``nidaqmx`` package and NI-DAQmx: digital lines
  (``port0/line0``), analogue inputs (``ai0``, volts × ``scale``), analogue outputs (``ao0``, level 0..1 ×
  ``max_v``) and edge-counting encoders (``ctr0``).
* ``labjack`` — LabJack T4 / T7 / T8 through the ``labjack-ljm`` package: digital I/O (``FIO0``, ``EIO2``…),
  analogue inputs (``AIN0``), analogue outputs (``DAC0``) and quadrature encoders (``DIO0`` + ``pin_b``).
* ``notify`` — sends alerts (sensor out of range, procedure "Send alert" action) by e-mail (SMTP) and by SMS
  (Twilio, or an e-mail-to-SMS gateway address in ``sms_to``) from a background thread.

The hardware packages are optional and imported when a device opens; tests inject fakes through ``transport``
(serial ports) or the ``module`` class attributes.
"""

from __future__ import annotations

import base64
import smtplib
import ssl
import threading
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .iodevices import DRIVERS, Device, _open_serial

# ============================================================================== USB-serial cable control lines
OUT_LINES = ("RTS", "DTR")
IN_LINES = {"CTS": "cts", "DSR": "dsr", "RI": "ri", "CD": "cd", "DCD": "cd"}


class SerialLinesDevice(Device):
    """The control lines of a USB-serial cable as digital inputs and outputs."""

    type = "serial_lines"
    POLL_S = 0.005

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self._next = 0.0

    def open(self):
        if self.transport is None and self._injected is not None:
            self.transport = self._injected
        if self.transport is None:
            port = self.cfg.get("port")
            if not port:
                self._error(f"{self.name}: no serial port configured")
                return
            try:
                self.transport = _open_serial(port, 9600)
            except ImportError:
                self._error(f"{self.name}: pyserial is not installed (pip install pyserial)")
                return
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: could not open {port}: {e}")
                return
        for n, c in self.channels.items():
            line = str(c.get("pin", "")).upper()
            k = c.get("kind", "input")
            if k in ("output", "pwm") and line not in OUT_LINES:
                self._error(f"{self.name}: output '{n}' must use RTS or DTR")
            elif k in ("input", "pir") and line not in IN_LINES:
                self._error(f"{self.name}: input '{n}' must use CTS, DSR, RI or CD")
        self.connected = True
        for n in self.outputs:
            self.set_output(n, 0)
        self._read(force=True)

    def close(self):
        if self.transport is not None and self.connected:
            self.all_off()  # every line at its off level before the port closes
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:  # pragma: no cover
                pass
        self.transport = None
        self.connected = False

    def set_output(self, channel, value, max_s=None) -> bool:
        """False (and the cached state unchanged) when the line could not be set."""
        c = self.channels.get(channel, {})
        line = str(c.get("pin", "")).upper()
        if self.transport is None or line not in OUT_LINES:
            return False
        level = bool(value) ^ bool(c.get("invert"))
        try:
            setattr(self.transport, line.lower(), level)
        except Exception as e:  # hardware dependent
            self._error(f"{self.name}: could not set {line}: {e}")
            return False
        self.outputs[channel] = 1 if value else 0
        return True

    def _read(self, force=False):
        if self.transport is None:
            return
        now = time.monotonic()
        if not force and now < self._next:
            return
        self._next = now + self.POLL_S
        for n, c in self.channels.items():
            attr = IN_LINES.get(str(c.get("pin", "")).upper())
            if attr is None or c.get("kind", "input") not in ("input", "pir"):
                continue
            try:
                v = bool(getattr(self.transport, attr))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: could not read {attr.upper()}: {e}")
                continue
            self._changed(n, int(v ^ bool(c.get("invert"))))


# ============================================================================== Firmata
START_SYSEX, END_SYSEX = 0xF0, 0xF7
DIGITAL_MESSAGE, ANALOG_MESSAGE, REPORT_ANALOG, REPORT_DIGITAL = 0x90, 0xE0, 0xC0, 0xD0
SET_PIN_MODE, REPORT_VERSION, REPORT_FIRMWARE, SAMPLING_INTERVAL, EXTENDED_ANALOG = 0xF4, 0xF9, 0x79, 0x7A, 0x6F
SYSTEM_RESET = 0xFF
MODE_INPUT, MODE_OUTPUT, MODE_ANALOG, MODE_PWM, MODE_PULLUP = 0, 1, 2, 3, 11


class FirmataDevice(Device):
    """A board running StandardFirmata (protocol version 2.x)."""

    type = "firmata"

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.firmware = ""
        self._buf = bytearray()
        self._ports: dict[int, int] = {}  # digital output port -> bit mask written
        self._in_ports: dict[int, int] = {}
        self.sent: list[bytes] = []
        self._lock = threading.RLock()

    def _write(self, data) -> bool:
        """False when not sent (not connected, or the write failed: the port is then closed and the device
        manager opens it again)."""
        data = bytes(data)
        with self._lock:
            self.sent.append(data)
            if len(self.sent) > 5000:
                del self.sent[:1000]
            if self.transport is None or not self.connected:
                return False
            try:
                self.transport.write(data)
                return True
            except Exception as e:  # hardware dependent
                self._error(f"{self.name}: write failed: {e}")
                self._drop()
                return False

    def _drop(self):
        with self._lock:
            if self.transport is not None:
                try:
                    self.transport.close()
                except Exception:  # pragma: no cover
                    pass
            self.transport = None
            self.connected = False

    def open(self, handshake_s: float = 3.0):
        if self.transport is None and self._injected is not None:
            self.transport = self._injected
        if self.transport is None:
            port = self.cfg.get("port")
            if not port:
                self._error(f"{self.name}: no serial port configured")
                return
            try:
                self.transport = _open_serial(port, int(self.cfg.get("baud", 57600)))
            except ImportError:
                self._error(f"{self.name}: pyserial is not installed (pip install pyserial)")
                return
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: could not open {port}: {e}")
                return
        self.connected = True
        self.firmware = ""
        deadline = time.monotonic() + handshake_s
        retry = 0.0
        while time.monotonic() < deadline and not self.firmware and self.connected:
            if time.monotonic() >= retry:  # the board resets when the port opens
                self._write([START_SYSEX, REPORT_FIRMWARE, END_SYSEX])
                retry = time.monotonic() + 1.0
            self._read()
            if not self.firmware:
                time.sleep(0.05)
        if not self.firmware:
            self._error(f"{self.name}: no reply from Firmata on {self.cfg.get('port', '?')}")
        if self.connected:
            self.configure()

    def _off_level(self, c) -> int:
        """The pin level (digital) or PWM value of an output that is off: inverted (active-low) outputs are off
        when HIGH."""
        if c.get("kind") == "pwm":
            return 255 if c.get("invert") else 0
        return 1 if c.get("invert") else 0

    def _write_pwm(self, pin: int, v: int) -> bool:
        if pin < 16:
            return self._write([ANALOG_MESSAGE | pin, v & 0x7F, (v >> 7) & 0x7F])
        return self._write([START_SYSEX, EXTENDED_ANALOG, pin, v & 0x7F, (v >> 7) & 0x7F, END_SYSEX])

    def _write_port(self, port: int, mask: int) -> bool:
        return self._write([DIGITAL_MESSAGE | (port & 0x0F), mask & 0x7F, (mask >> 7) & 0x7F])

    def _outputs_off(self) -> bool:
        """Every output at its off level (inverted outputs HIGH), whatever the cached state."""
        ok, ports = True, {}
        for n, c in self.channels.items():
            if c.get("kind") not in ("output", "pwm") or c.get("pin") is None or c.get("derived"):
                continue
            pin = int(c["pin"])
            if c.get("kind") == "pwm":
                ok = self._write_pwm(pin, self._off_level(c)) and ok
            else:
                port, bit = pin // 8, pin % 8
                ports[port] = ports.get(port, 0) | (self._off_level(c) << bit)
        for port, mask in sorted(ports.items()):
            if self._write_port(port, mask):
                self._ports[port] = mask
            else:
                ok = False
        if ok:
            for n, c in self.channels.items():
                if c.get("kind") in ("output", "pwm"):
                    self.outputs[n] = 0
        return ok

    def configure(self):
        periods = [int(c.get("period_ms", 19)) for c in self.channels.values()
                   if c.get("kind") in ("analog", "sensor")]
        if periods:
            ms = max(1, min(periods))
            self._write([START_SYSEX, SAMPLING_INTERVAL, ms & 0x7F, (ms >> 7) & 0x7F, END_SYSEX])
        ports = set()
        for n, c in self.channels.items():
            k, pin = c.get("kind", "input"), c.get("pin")
            if c.get("derived") or k in ("thermostat", "odour"):
                continue
            if pin is None:
                self._error(f"{self.name}: channel '{n}' has no pin")
                continue
            pin = int(pin)
            if k in ("input", "pir"):
                pull = c.get("pullup", k == "input")
                self._write([SET_PIN_MODE, pin, MODE_PULLUP if pull else MODE_INPUT])
                ports.add(pin // 8)
            elif k == "output":
                self._write([SET_PIN_MODE, pin, MODE_OUTPUT])
            elif k == "pwm":
                self._write([SET_PIN_MODE, pin, MODE_PWM])
            elif k in ("analog", "sensor"):
                self._write([REPORT_ANALOG | (pin & 0x0F), 1])
            else:
                self._error(f"{self.name}: Firmata does not support {k} channels ('{n}')")
        for port in sorted(ports):
            self._write([REPORT_DIGITAL | (port & 0x0F), 1])
        # the outputs start at their off level: an active-low (inverted) output is driven HIGH at once, and the
        # port masks written later keep the other outputs of the port off
        self._ports = {}
        self._outputs_off()

    def close(self):
        with self._lock:
            if self.transport is not None and self.connected:
                self._outputs_off()  # every output off before the port closes
            self._drop()

    def set_output(self, channel, value, max_s=None) -> bool:
        """False (and the cached state unchanged) when the command could not be sent."""
        c = self.channels.get(channel)
        if not c or c.get("pin") is None:
            self._error(f"{self.name}: unknown output channel '{channel}'")
            return False
        pin = int(c["pin"])
        with self._lock:
            if c.get("kind") == "pwm":
                v = int(round(max(0.0, min(1.0, float(value))) * 255))
                if c.get("invert"):
                    v = 255 - v
                if not self._write_pwm(pin, v):
                    return False
                self.outputs[channel] = value
                return True
            on = bool(value) ^ bool(c.get("invert"))
            port, bit = pin // 8, pin % 8
            mask = self._ports.get(port, 0)
            mask = mask | (1 << bit) if on else mask & ~(1 << bit)
            if not self._write_port(port, mask):
                return False
            self._ports[port] = mask
            self.outputs[channel] = value
            return True

    def _read(self):
        if self.transport is None:
            return
        with self._lock:
            try:
                n = getattr(self.transport, "in_waiting", 0)
                data = self.transport.read(n or 4096) if n is None or n > 0 else b""
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: read failed: {e}")
                return
            self._buf += data or b""
            self._parse()

    def _parse(self):
        b = self._buf
        while b:
            cmd = b[0]
            if cmd == START_SYSEX:
                end = b.find(bytes([END_SYSEX]))
                if end < 0:
                    break
                self._sysex(bytes(b[1:end]))
                del b[:end + 1]
            elif cmd & 0xF0 in (DIGITAL_MESSAGE, ANALOG_MESSAGE) or cmd == REPORT_VERSION:
                if len(b) < 3:
                    break
                lsb, msb = b[1], b[2]
                del b[:3]
                if cmd == REPORT_VERSION:
                    continue
                v = lsb | (msb << 7)
                if cmd & 0xF0 == DIGITAL_MESSAGE:
                    self._digital(cmd & 0x0F, v)
                else:
                    self._analog(cmd & 0x0F, v)
            else:
                del b[:1]  # out of sync: skip a byte

    def _sysex(self, data: bytes):
        if data and data[0] == REPORT_FIRMWARE and len(data) >= 3:
            name = bytes(data[i] | (data[i + 1] << 7) for i in range(3, len(data) - 1, 2)).decode(errors="replace")
            self.firmware = f"Firmata {data[1]}.{data[2]} {name}".strip()

    def _digital(self, port, mask):
        self._in_ports[port] = mask
        for n, c in self.channels.items():
            if c.get("kind", "input") in ("input", "pir") and c.get("pin") is not None and int(c["pin"]) // 8 == port:
                raw = (mask >> (int(c["pin"]) % 8)) & 1
                v = 1 - raw if c.get("pullup", c.get("kind", "input") == "input") else raw
                self._changed(n, v ^ int(bool(c.get("invert"))))

    def _analog(self, apin, value):
        for n, c in self.channels.items():
            if c.get("kind") in ("analog", "sensor") and c.get("pin") is not None and int(c["pin"]) == apin:
                self._analog_in(n, value * float(c.get("scale", 1.0)))

    def all_off(self) -> bool:
        for th in self.thermostats.values():
            th.target = th.setpoint = None
        with self._lock:
            return self._outputs_off()


# ============================================================================== National Instruments
class NIDAQDevice(Device):
    """A National Instruments device through NI-DAQmx (``pip install nidaqmx`` and the NI-DAQmx driver)."""

    type = "nidaq"
    module = None  # the nidaqmx module (tests inject a fake)
    POLL_S = 0.005

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self._tasks: dict[str, object] = {}
        self._next: dict[str, float] = {}

    def _phys(self, c):
        return f"{self.cfg.get('device_id', 'Dev1')}/{c.get('pin')}"

    def open(self):
        mod = NIDAQDevice.module
        if mod is None:
            try:
                import nidaqmx as mod  # type: ignore
            except Exception:
                self._error(f"{self.name}: the nidaqmx package is not installed (pip install nidaqmx; needs the "
                            "NI-DAQmx driver)")
                return
        for n, c in self.channels.items():
            k = c.get("kind", "input")
            if c.get("derived") or k in ("thermostat", "odour"):
                continue
            try:
                task = mod.Task(f"{self.name}_{n}")
                if k in ("input", "pir"):
                    task.di_channels.add_di_chan(self._phys(c))
                elif k == "output":
                    task.do_channels.add_do_chan(self._phys(c))
                elif k == "pwm":
                    task.ao_channels.add_ao_voltage_chan(self._phys(c), min_val=0.0,
                                                         max_val=float(c.get("max_v", 5.0)))
                elif k in ("analog", "sensor"):
                    task.ai_channels.add_ai_voltage_chan(self._phys(c))
                elif k == "encoder":
                    task.ci_channels.add_ci_count_edges_chan(self._phys(c))
                    task.start()
                self._tasks[n] = task
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: channel '{n}' ({self._phys(c)}): {e}")
        self.connected = True
        for n in self.outputs:
            if self.kind(n) in ("output", "pwm"):
                self.set_output(n, 0)

    def close(self):
        if self.connected:
            self.all_off()  # every output written off before the tasks close
        for t in self._tasks.values():
            try:
                t.close()
            except Exception:  # pragma: no cover
                pass
        self._tasks = {}
        self.connected = False

    def set_output(self, channel, value, max_s=None) -> bool:
        """False (and the cached state unchanged) when the value could not be written."""
        t, c = self._tasks.get(channel), self.channels.get(channel, {})
        if t is None:
            return False
        try:
            if c.get("kind") == "pwm":
                t.write(max(0.0, min(1.0, float(value))) * float(c.get("max_v", 5.0)))
            else:
                t.write(bool(value) ^ bool(c.get("invert")))
        except Exception as e:  # hardware dependent
            self._error(f"{self.name}: {channel}: {e}")
            return False
        self.outputs[channel] = value
        return True

    def _read(self):
        now = time.monotonic()
        for n, t in list(self._tasks.items()):
            c = self.channels[n]
            k = c.get("kind", "input")
            if k in ("output", "pwm"):
                continue
            per = self.POLL_S if k in ("input", "pir", "encoder") else int(c.get("period_ms", 50)) / 1000.0
            if now < self._next.get(n, 0.0):
                continue
            self._next[n] = now + per
            try:
                v = t.read()
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: {n}: {e}")
                continue
            if k in ("input", "pir"):
                self._changed(n, int(bool(v) ^ bool(c.get("invert"))))
            elif k == "encoder":
                self._changed(n, int(v))
            else:
                self._analog_in(n, float(v) * float(c.get("scale", 1.0)))


# ============================================================================== LabJack
class LabJackDevice(Device):
    """A LabJack T4 / T7 / T8 through LJM (``pip install labjack-ljm`` and the LJM library)."""

    type = "labjack"
    module = None  # labjack.ljm (tests inject a fake)
    POLL_S = 0.005

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.handle = None
        self.ljm = None
        self._next: dict[str, float] = {}

    def open(self):
        ljm = LabJackDevice.module
        if ljm is None:
            try:
                from labjack import ljm  # type: ignore
            except Exception:
                self._error(f"{self.name}: the labjack-ljm package is not installed (pip install labjack-ljm; "
                            "needs the LJM library)")
                return
        self.ljm = ljm
        try:
            self.handle = ljm.openS("ANY", "ANY", str(self.cfg.get("identifier", "ANY")))
        except Exception as e:  # pragma: no cover - hardware dependent
            self._error(f"{self.name}: could not open the LabJack: {e}")
            return
        for n, c in self.channels.items():
            if c.get("kind") == "encoder" and c.get("pin") is not None:
                a = str(c["pin"]).upper()  # DIO0 / FIO0 ... : quadrature on this line and pin_b
                idx = int("".join(ch for ch in a if ch.isdigit()) or 0)
                try:
                    for line in (idx, int(str(c.get("pin_b", idx + 1)).upper().lstrip("DIOFE") or idx + 1)):
                        ljm.eWriteName(self.handle, f"DIO{line}_EF_ENABLE", 0)
                        ljm.eWriteName(self.handle, f"DIO{line}_EF_INDEX", 10)  # quadrature in
                        ljm.eWriteName(self.handle, f"DIO{line}_EF_ENABLE", 1)
                    c["_reg"] = f"DIO{idx}_EF_READ_A"
                except Exception as e:  # pragma: no cover - hardware dependent
                    self._error(f"{self.name}: encoder '{n}': {e}")
        self.connected = True
        for n in self.outputs:
            if self.kind(n) in ("output", "pwm"):
                self.set_output(n, 0)

    def close(self):
        if self.handle is not None and self.ljm is not None:
            if self.connected:
                self.all_off()  # every output written off before the handle closes
            try:
                self.ljm.close(self.handle)
            except Exception:  # pragma: no cover
                pass
        self.handle = None
        self.connected = False

    def set_output(self, channel, value, max_s=None) -> bool:
        """False (and the cached state unchanged) when the value could not be written."""
        c = self.channels.get(channel, {})
        if self.handle is None or c.get("pin") is None:
            return False
        try:
            if c.get("kind") == "pwm":
                self.ljm.eWriteName(self.handle, str(c["pin"]),
                                    max(0.0, min(1.0, float(value))) * float(c.get("max_v", 5.0)))
            else:
                self.ljm.eWriteName(self.handle, str(c["pin"]), int(bool(value) ^ bool(c.get("invert"))))
        except Exception as e:  # hardware dependent
            self._error(f"{self.name}: {channel}: {e}")
            return False
        self.outputs[channel] = value
        return True

    SYNC_DEVICE_MAX_S = 0.005  # longer synchronisation pulses are ended by the computer (the call blocks meanwhile)

    def sync_pulse(self, channel, width_s) -> bool | None:
        """A synchronisation pulse on a digital line timed by the LabJack itself: one eWriteNames call writes the
        line on, waits ``WAIT_US_BLOCKING`` microseconds on the device and writes it off (pulses up to 5 ms; longer
        ones are switched on and off by the device manager)."""
        c = self.channels.get(channel, {})
        if c.get("kind") != "output" or float(width_s) > self.SYNC_DEVICE_MAX_S:
            return None
        if self.handle is None or c.get("pin") is None:
            return False
        on = int(not c.get("invert"))
        try:
            self.ljm.eWriteNames(self.handle, 3, [str(c["pin"]), "WAIT_US_BLOCKING", str(c["pin"])],
                                 [on, max(1, int(round(float(width_s) * 1e6))), 1 - on])
        except Exception as e:  # hardware dependent
            self._error(f"{self.name}: {channel}: {e}")
            return False
        self._count_sync(channel)
        return True

    def _read(self):
        if self.handle is None:
            return
        now = time.monotonic()
        for n, c in self.channels.items():
            k = c.get("kind", "input")
            if k not in ("input", "pir", "analog", "sensor", "encoder") or c.get("pin") is None or c.get("derived"):
                continue
            per = self.POLL_S if k in ("input", "pir", "encoder") else int(c.get("period_ms", 50)) / 1000.0
            if now < self._next.get(n, 0.0):
                continue
            self._next[n] = now + per
            try:
                v = self.ljm.eReadName(self.handle, c.get("_reg") or str(c["pin"]))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: {n}: {e}")
                continue
            if k in ("input", "pir"):
                self._changed(n, int(bool(round(v)) ^ bool(c.get("invert"))))
            elif k == "encoder":
                self._changed(n, int(v))
            else:
                self._analog_in(n, float(v) * float(c.get("scale", 1.0)))


# ============================================================================== alerts
class NotifyDevice(Device):
    """E-mail and SMS alerts. ``email_to`` / ``sms_to`` are comma-separated; an ``sms_to`` entry containing "@"
    is an e-mail-to-SMS gateway address, other entries are phone numbers sent through Twilio (``twilio_sid``,
    ``twilio_token``, ``twilio_from``). ``sender`` (class attribute) can replace the network calls in tests."""

    type = "notify"
    sender = None  # callable(kind, cfg, to, subject, text)

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.sent: list[tuple[str, str, str]] = []  # (kind, to, subject)
        self._threads: list[threading.Thread] = []

    def notify(self, subject: str, text: str, kinds=None, to=None) -> bool:
        """kinds: ("email",) / ("sms",) / None = both; to: comma-separated addresses / numbers instead of the
        configured ones (an e-mail given to an SMS goes to an e-mail-to-SMS gateway)."""
        def split(v):
            return [a.strip() for a in str(v or "").split(",") if a.strip()]
        kinds = tuple(kinds) if kinds else ("email", "sms")
        emails = split(to if to and kinds == ("email",) else self.cfg.get("email_to", "")) \
            if "email" in kinds else []
        sms = split(to if to and kinds == ("sms",) else self.cfg.get("sms_to", "")) if "sms" in kinds else []
        if to and kinds == ("email", "sms"):
            emails, sms = [a for a in split(to) if "@" in a], [a for a in split(to) if "@" not in a]
        jobs = [("email", a) for a in emails + [s for s in sms if "@" in s]] + \
               [("sms", s) for s in sms if "@" not in s]
        if not jobs:
            self._error(f"{self.name}: no e-mail address or phone number to send alerts to")
            return False
        th = threading.Thread(target=self._send_all, args=(jobs, subject, text), name="io-alert", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [th]
        th.start()
        return True

    def wait(self, timeout: float = 5.0):
        """Wait for the alerts being sent (tests, closing)."""
        for t in list(self._threads):
            t.join(timeout)

    def _send_all(self, jobs, subject, text):
        for kind, to in jobs:
            try:
                if NotifyDevice.sender is not None:
                    NotifyDevice.sender(kind, self.cfg, to, subject, text)
                elif kind == "email":
                    self._email(to, subject, text)
                else:
                    self._twilio(to, f"{subject}: {text}")
                self.sent.append((kind, to, subject))
            except Exception as e:  # network dependent
                self._error(f"{self.name}: could not send the alert to {to}: {e}")

    def _email(self, to, subject, text):  # pragma: no cover - network dependent
        c = self.cfg
        host, port = str(c.get("smtp_host", "")), int(c.get("smtp_port", 587) or 587)
        if not host:
            raise RuntimeError("no SMTP server configured")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.get("from_addr") or c.get("smtp_user") or "manymaze@localhost"
        msg["To"] = to
        msg.set_content(text)
        ctx = ssl.create_default_context()
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            srv = smtplib.SMTP(host, port, timeout=20)
            srv.starttls(context=ctx)
        with srv:
            if c.get("smtp_user"):
                srv.login(str(c["smtp_user"]), str(c.get("smtp_password", "")))
            srv.send_message(msg)

    def _twilio(self, to, text):  # pragma: no cover - network dependent
        c = self.cfg
        sid, token, frm = (str(c.get(k, "")) for k in ("twilio_sid", "twilio_token", "twilio_from"))
        if not (sid and token and frm):
            raise RuntimeError("SMS needs twilio_sid, twilio_token and twilio_from (or an e-mail-to-SMS address)")
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=urllib.parse.urlencode({"To": to, "From": frm, "Body": text[:1500]}).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()


DRIVERS.update({"serial_lines": SerialLinesDevice, "firmata": FirmataDevice, "nidaq": NIDAQDevice,
                "labjack": LabJackDevice, "notify": NotifyDevice})
