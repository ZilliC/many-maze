"""Animal scales (balances on a serial port): read a weight in grams, as an I/O device (analogue channel "weight")
and from the Animals page ("Weigh"), which records it with the date on the animal.

Device configuration (``Project.io_devices``)::

    {"name": "scale", "type": "scale", "port": "/dev/cu.usbserial-2", "baud": 9600, "protocol": "mt_sics",
     "poll_s": 0.5}

Protocols (``SCALE_PROTOCOLS``) — the command asked for an immediate reading, and how a reply looks:

* ``mt_sics`` — Mettler Toledo MT-SICS: ``SI`` → ``S S     12.34 g`` (``S D``: dynamic, i.e. unstable;
  ``S I``: busy; ``S +`` / ``S -``: over / underload).
* ``ohaus`` — Ohaus: ``IP`` → ``     12.34 g`` (a ``?`` marks an unstable weight).
* ``sartorius`` — Sartorius SBI: ``ESC P`` → ``+     12.34 g`` (the unit is left out while the weight is unstable;
  22-character replies start with an identification such as ``N``).
* ``and`` — A&D: ``Q`` → ``ST,+00012.34  g`` (``ST`` stable, ``US`` unstable, ``OL`` overload).
* ``kern`` — Kern: ``w`` (any weight) / ``s`` (stable weight) → ``     12.34 g``.
* ``continuous`` — the balance sends weights by itself (continuous or print-key output): the first number of each
  line with its unit; ``?``, ``US`` or ``S D`` mark unstable weights.

Units g, kg, mg, ct, oz and lb are converted to grams (no unit: the configuration's ``unit``, default g).
pyserial is optional (only needed with real balances). The protocols follow the manufacturers' interface manuals
and are tested here with simulated replies only.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from .iodevices import _LineDevice

SCALE_PROTOCOLS = {
    "mt_sics": "Mettler Toledo (MT-SICS)",
    "ohaus": "Ohaus",
    "sartorius": "Sartorius (SBI)",
    "and": "A&D",
    "kern": "Kern",
    "continuous": "Continuous output (any balance)",
}
# (command for a stable weight, command for an immediate weight); None: the balance sends by itself
_COMMANDS = {"mt_sics": ("SI", "SI"), "ohaus": ("IP", "IP"), "sartorius": ("\x1bP", "\x1bP"), "and": ("Q", "Q"),
             "kern": ("s", "w"), "continuous": (None, None)}
_EOL = {"kern": ""}
GRAMS = {"g": 1.0, "kg": 1000.0, "mg": 0.001, "ct": 0.2, "oz": 28.349523125, "lb": 453.59237, "lbs": 453.59237}
WEIGHT_FIELD = "Weight (g)"

_NUM = r"[-+]?\s*\d+(?:\.\d*)?|[-+]?\s*\.\d+"
_UNIT = r"(?:(kg|mg|g|ct|oz|lbs|lb)\b)?"  # optional unit (group)


class ScaleError(RuntimeError):
    """The balance could not be read (not configured, no pyserial, over / underload, error reply)."""


class ScaleTimeout(ScaleError, TimeoutError):
    """No (stable) weight in time."""


def to_grams(value: float, unit: str | None, default: str = "g") -> float:
    u = (unit or default or "g").lower()
    if u not in GRAMS:
        raise ScaleError(f"unknown weight unit '{unit}'")
    return float(value) * GRAMS[u]


def _number(s: str) -> float:
    return float(s.replace(" ", ""))


def parse_weight(line: str, protocol: str = "continuous", unit: str = "g") -> tuple[float, bool] | None:
    """(grams, stable) from one line sent by a balance; None if the line holds no weight (busy, echo...).
    Raises :class:`ScaleError` for over / underload and error replies."""
    s = line.strip().strip("\x02\x03")
    if not s:
        return None
    if protocol == "mt_sics":
        if re.match(r"^(ES|ET|EL)\b", s):
            raise ScaleError(f"balance error {s.split()[0]}")
        m = re.match(r"^S\s+([SDI+\-])\s*(.*)$", s)
        if m is None:
            return None
        flag, rest = m.groups()
        if flag in "+-":
            raise ScaleError("balance overload" if flag == "+" else "balance underload")
        if flag == "I":
            return None
        w = re.match(rf"({_NUM})\s*{_UNIT}", rest, re.I)
        return (to_grams(_number(w.group(1)), w.group(2), unit), flag == "S") if w else None
    if protocol == "and":
        m = re.match(rf"^(ST|US|OL|QT|WT|NT|GS),\s*({_NUM})?\s*([A-Za-z]*)", s)
        if m is None:
            return None
        if m.group(1) == "OL":
            raise ScaleError("balance overload")
        if m.group(2) is None:
            return None
        return to_grams(_number(m.group(2)), m.group(3) or None, unit), m.group(1) != "US"
    if protocol == "sartorius":
        if re.search(r"\b(Err|ERROR)\b|^[HL]\s*$|^\s*[HL]{1,2}\s", s):
            raise ScaleError(f"balance error: {s}")
        s2 = re.sub(r"^[A-Za-z]{1,6}\s+(?=[-+]|\d)", "", s)  # 22-character format: identification first
        m = re.match(rf"^({_NUM})\s*{_UNIT}", s2, re.I)
        if m is None:
            return None
        return to_grams(_number(m.group(1)), m.group(2), unit), m.group(2) is not None
    # ohaus, kern, continuous: the first number (with its unit) on the line
    m = re.search(rf"({_NUM})\s*{_UNIT}", s, re.I)
    if m is None:
        return None
    unstable = "?" in s or bool(re.match(r"^(US\b|S\s+D\b)", s)) or (protocol == "continuous" and " D " in f" {s} ")
    if protocol == "kern" and re.search(r"\bError\b|\bErr\b", s, re.I):
        raise ScaleError(f"balance error: {s}")
    return to_grams(_number(m.group(1)), m.group(2), unit), not unstable


class ScaleDevice(_LineDevice):
    """A balance: analogue input channel "weight" (grams, updated when the balance sends or is polled every
    ``poll_s`` seconds) and "weight.stable" (status 0/1); :meth:`read` waits for a weight."""

    type = "scale"

    def __init__(self, cfg: dict, transport=None):
        cfg = dict(cfg)
        self.protocol = cfg.get("protocol") or "mt_sics"
        if self.protocol not in _COMMANDS:
            raise ScaleError(f"unknown scale protocol '{self.protocol}'")
        cfg.setdefault("eol", _EOL.get(self.protocol, "\r\n"))
        cfg.setdefault("baud", 9600)
        super().__init__(cfg, transport)
        for ch, kind in (("weight", "analog"), ("weight.stable", "status")):
            if ch not in self.channels:
                self.channels[ch] = {"name": ch, "kind": kind}
            self.inputs[ch] = 0
            self.outputs.pop(ch, None)
        self.unit = str(cfg.get("unit") or "g")
        self.poll_s = float(cfg.get("poll_s", 0.5) or 0)
        self._next_poll = 0.0
        self.last: tuple[float, bool] | None = None

    def _cmd(self, stable: bool) -> str | None:
        return _COMMANDS[self.protocol][0 if stable else 1]

    def _take(self, line: str) -> tuple[float, bool] | None:
        r = parse_weight(line, self.protocol, self.unit)
        if r is not None:
            self.last = r
            self._changed("weight", round(r[0], 6))
            self._changed("weight.stable", 1 if r[1] else 0)
        return r

    def _read(self):
        for line in self.read_lines():
            try:
                self._take(line)
            except ScaleError as e:
                self._error(f"{self.name}: {e}")
        cmd = self._cmd(False)
        if cmd and self.poll_s > 0 and self.connected and time.monotonic() >= self._next_poll:
            self._next_poll = time.monotonic() + self.poll_s
            self.write_line(cmd)

    def read(self, timeout: float = 3.0, stable: bool = True) -> tuple[float, bool]:
        """Wait for a weight: (grams, stable). With ``stable`` the balance is asked again until the weight settles.
        Raises :class:`ScaleTimeout` (none in time) or :class:`ScaleError`."""
        if not self.connected:
            raise ScaleError(self.errors[-1] if self.errors else f"{self.name}: the scale is not connected")
        deadline = time.monotonic() + float(timeout)
        cmd = self._cmd(stable)
        again = 0.3 if cmd != "s" else max(0.5, timeout / 3)  # Kern "s" answers once the weight is stable
        with self._io_lock:
            self.read_lines()  # discard old replies
            self._buf = b""
            next_q = 0.0
            last = None
            while True:
                now = time.monotonic()
                if cmd and now >= next_q:
                    self.write_line(cmd)
                    next_q = now + again
                for line in self.read_lines():
                    r = self._take(line)
                    if r is None:
                        continue
                    last = r
                    if r[1] or not stable:
                        return r
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        extra = f" (last reading {last[0]:g} g, not stable)" if last else ""
        raise ScaleTimeout(f"{self.name}: no {'stable ' if stable else ''}weight within {timeout:g} s{extra}")


def read_weight(cfg: dict, transport=None, timeout: float = 3.0, stable: bool = True) -> tuple[float, bool]:
    """Open the balance of an I/O device configuration, read one weight and close it: (grams, stable).
    Raises :class:`ScaleError` (no port, pyserial missing, error reply) or :class:`ScaleTimeout`."""
    dev = ScaleDevice(cfg, transport)
    dev.poll_s = 0
    dev.open()
    if not dev.connected:
        raise ScaleError(dev.errors[-1] if dev.errors else f"{dev.name}: could not open the scale")
    try:
        return dev.read(timeout, stable)
    finally:
        if transport is None:
            dev.close()


# ======================================================================================== animals
def scale_configs(project) -> list[dict]:
    """The project's enabled scale devices."""
    return [c for c in (project.io_devices or []) if c.get("type") == "scale" and c.get("enabled", True)]


def format_grams(g: float) -> str:
    return f"{round(float(g), 2):g}"


def record_weight(project, animal, grams: float, when: datetime | None = None, field: str = WEIGHT_FIELD) -> dict:
    """Store a weight on an animal: its ``field`` column (added to the project if needed) and its weight history.
    Returns the history entry {"date", "grams"}."""
    g = round(float(grams), 3)
    if not g > 0:
        raise ValueError(f"invalid weight {grams!r} g")
    entry = {"date": (when or datetime.now()).isoformat(timespec="minutes"), "grams": g}
    if field not in project.animal_fields:
        project.animal_fields.append(field)
    animal.fields[field] = format_grams(g)
    animal.weights.append(entry)
    return entry
