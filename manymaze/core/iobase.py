"""The base class of the I/O device drivers (channels, inputs, outputs, pulses, analogue filters, the event queue)
and the simulated ``virtual`` device; the drivers are in :mod:`.iodevices`, :mod:`.audio`, :mod:`.iodrivers`,
:mod:`.pumps` and :mod:`.scales`."""

from __future__ import annotations

import threading
import time


from .iocontrol import AnalogFilter, Thermostat, sensor_value
from .ioconfig import INPUT_KINDS

SHOCKER_MAX_ON_S = 60.0  # longest continuous on-time of a channel with the role "shocker" (procedures.SHOCK_MAX_S)


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
        self._injected = transport  # a transport given by the caller (tests): used again when the device reopens
        self.ever_connected = False  # opened once: the device manager reopens it when the connection is lost
        self._pending: list[tuple[str, float, float | None]] = []  # (channel, value, board ms or None)
        self._pend_lock = threading.Lock()  # _pending: filled by readers, emptied by pollers (other threads)
        self.watchdog_fired = 0  # times the board's watchdog switched every output off
        self.sync_pulses: dict[str, int] = {}  # synchronisation pulses sent per output channel (core.sync)
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
            with self._pend_lock:
                self._pending.append((channel, value, ms))

    def max_on_s(self, channel: str) -> float | None:
        """The longest an output may stay on (s), whatever the procedures ask: the channel's ``max_on_s`` option,
        else 60 s for channels with the role "shocker"; None: no limit."""
        c = self.channels.get(channel) or {}
        try:
            cap = float(c.get("max_on_s") or 0)
        except (TypeError, ValueError):
            cap = 0.0
        if cap > 0:
            return cap
        if str(c.get("role", "") or "").lower() in ("shocker", "shock"):
            return SHOCKER_MAX_ON_S
        return None

    def times_max_on(self, channel: str) -> bool:
        """Whether the device switches this output off itself after a maximum on-time (board timing)."""
        return False

    def host_max_on(self, channel: str, max_s: float | None) -> float | None:
        """How long (s) the computer must let an output stay on before it switches it off itself: the shorter of
        the requested maximum and the channel's cap, unless the device times it (None: no host cut-off)."""
        if self.type == "virtual" or self.times_max_on(channel):
            return None
        caps = [float(x) for x in (max_s, self.max_on_s(channel)) if x is not None and float(x) > 0]
        return min(caps) if caps else None

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
        return self._take_pending()

    def _take_pending(self) -> list:
        with self._pend_lock:
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
        """Switch an output; drivers return False when the command could not be sent (the cached state in
        ``outputs`` is then left as it was)."""
        self.outputs[channel] = value

    def pulse_train(self, channel: str, period_s: float, width_s: float, count: int) -> bool:
        """Start a hardware-timed train (count 0 = until stopped). False if the device cannot time pulses."""
        return False

    def sync_pulse(self, channel: str, width_s: float) -> bool | None:
        """One synchronisation pulse by the device's fastest path (core.sync): True when the device sends and times
        it itself, False when it could not be sent, None when the device cannot time it (the device manager then
        switches the output on and off itself)."""
        return None

    def _count_sync(self, channel: str):
        self.sync_pulses[channel] = self.sync_pulses.get(channel, 0) + 1

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

    def all_off(self) -> bool:
        """Every output off: 0 is written to every output channel whatever its cached state (a previous "off" may
        not have reached the hardware). False when a write failed."""
        for th in self.thermostats.values():
            if th.target is not None:
                th.off()
        ok = True
        for ch in list(self.outputs):
            if self.kind(ch) in ("thermostat", "odour", "pump"):
                continue
            try:
                ok = self.set_output(ch, 0) is not False and ok
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: {ch}: {e}")
                ok = False
        return ok


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

    def sync_pulse(self, channel: str, width_s: float) -> bool:
        """A simulated pulse: counted (sync_pulses), the output's shown state does not change."""
        if channel not in self.channels:
            self.channels[channel] = {"name": channel, "kind": "output"}
            self.outputs.setdefault(channel, 0)
        self._count_sync(channel)
        return True
