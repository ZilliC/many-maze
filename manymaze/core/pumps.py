"""Syringe pumps (ANY-maze parity): predefined and custom syringes, pump models and their serial protocols, and the
``syringe_pump`` I/O device (infuse / withdraw at a flow rate, optional target volume, stall detection, volume
infused / withdrawn).

Device configuration (``Project.io_devices``)::

    {"name": "pumps", "type": "syringe_pump", "port": "/dev/cu.usbserial-1", "baud": 19200, "protocol": "new_era",
     "channels": [
        {"name": "pump1", "kind": "pump", "address": 0, "syringe": "BD Plastipak 10 ml"},
        {"name": "pump2", "kind": "pump", "address": 1, "diameter_mm": 14.5, "rate_ml_min": 0.5}]}

Several pumps can share one port (daisy chain) when the protocol has addresses (New Era, Harvard, Cavro DT).
Each pump channel gets derived input channels: ``<pump>.running``, ``<pump>.stalled`` and
``<pump>.target_reached`` (kind "status", 0/1; target_reached pulses 1 for one poll when a target volume has been
delivered) and ``<pump>.infused_ml`` / ``<pump>.withdrawn_ml`` (kind "analog", totals since the device was opened).

Protocols (``PUMP_PROTOCOLS``):

* ``new_era`` — New Era Pump Systems "basic mode" (NE-500/1000/1002X/1010/1200/1600/4000/8000 and OEM pumps such as
  WPI Aladdin). Commands ``DIA``, ``RAT``, ``VOL``, ``DIR``, ``RUN``, ``STP``, ``DIS``, ``CLD``; replies are
  ``STX addr status [data] ETX``; ``A?S`` is the stall alarm.
* ``harvard_ultra`` — Harvard Apparatus Pump 11 Elite / PHD Ultra and KD Scientific Legato ("ultra" command set:
  ``irate``, ``tvolume``, ``irun``, ``wrun``, ``stop``, ``ivolume``...). Prompts ``:`` idle, ``>`` infusing,
  ``<`` withdrawing, ``*`` stalled, ``T*`` target reached.
* ``harvard_legacy`` — the older Harvard command set (PHD 2000, Pump 11, Pump 33 "22 protocol": ``MMD``, ``MLM``,
  ``MLT``, ``RUN``, ``REV``, ``STP``, ``VOL``, ``CLV``) with the same prompts. ``REV`` reverses the direction and
  runs; the driver assumes the pump is set to infuse when the device is opened.
* ``chemyx`` — Chemyx Fusion / Nexus (``set units``, ``set diameter``, ``set rate``, ``set volume`` (negative =
  withdraw), ``start``, ``stop``, ``status``, ``dispensed volume``). One pump per port.
* ``cavro_dt`` — the Cavro / DT "OEM" protocol of syringe drives (Tecan Cavro XCalibur / XLP / Centris, Hamilton
  PSD, TriContinent C-series...): ``/1V<speed>D<steps>R`` (dispense), ``P`` (pick up), ``A`` (absolute), ``TR``
  (terminate), ``Q`` (status: error 9 = plunger overload, i.e. stall), ``?`` (plunger position). The channel needs
  the syringe volume (``syringe`` or ``syringe_volume_ml``), ``full_steps`` (3000 by default) and optionally
  ``velocity_per_step_s`` (speed units per step/s, 1), ``valve`` (true: switch to output / input before moving)
  and ``initialize`` (true: send ``ZR`` when the device opens — this moves the plunger!).
* ``text`` — any pump driven by text lines: ``commands`` (format templates with {addr}, {rate_ml_min},
  {rate_ul_min}, {rate_ml_h}, {rate_ul_h}, {volume_ml}, {volume_ul}, {diameter_mm}) and ``replies`` (regular
  expressions "running", "stopped", "stalled", "target", "infused", "withdrawn"; the volume ones capture the volume
  in ml as their first group; an optional ``(?P<addr>\\d+)`` group selects the pump).
* ``simulated`` — no hardware: the volume is integrated over time from the rate; :meth:`SyringePumpDevice.
  simulate_stall` forces a stall (tests, demonstrations).

The protocols were written from the manufacturers' manuals and have not all been tried on real pumps (see
``PUMP_MODELS`` "verified"). Syringe inner diameters must be checked against the syringe manufacturer's data: the
values below come from published pump-manual tables and catalogues, entries with ``"approx": True`` are
approximate, and syringe barrels change between production series.
"""

from __future__ import annotations

import re
import time
from collections import deque

from .iodevices import _LineDevice

# ======================================================================================== syringes
SYRINGE_NOTE = ("Inner diameters are taken from published pump-manual tables and catalogues. Check the value against "
                "the syringe manufacturer's data before relying on delivered volumes; entries marked approximate are "
                "estimates.")


def _vol_label(ml: float) -> str:
    return f"{ml * 1000:g} µl" if ml < 1 else f"{ml:g} ml"


def _series(maker: str, line: str, sizes, approx: bool = False) -> list[dict]:
    out = []
    for ml, dia in sizes:
        s = {"manufacturer": maker, "model": f"{line} {_vol_label(ml)}".strip(), "volume_ml": ml, "diameter_mm": dia}
        if approx:
            s["approx"] = True
        out.append(s)
    return out


_GASTIGHT_UL = [(0.01, 0.485), (0.025, 0.729), (0.05, 1.03), (0.1, 1.457), (0.25, 2.304), (0.5, 3.256)]

SYRINGES: list[dict] = (
    _series("BD", "Plastipak", [(1, 4.78), (3, 8.66), (5, 12.06), (10, 14.5), (20, 19.13), (30, 21.7), (50, 26.6)])
    + _series("BD", "Luer-Lok", [(1, 4.699), (3, 8.585), (5, 11.99), (10, 14.43), (20, 19.05), (30, 21.59),
                                 (60, 26.59)])
    + _series("BD", "Glass (Yale)", [(1, 4.64), (2, 8.86), (3, 8.88), (5, 11.86), (10, 14.34), (20, 19.13),
                                     (30, 22.2), (50, 28.6)], approx=True)
    + _series("Terumo", "", [(1, 4.7), (3, 8.95), (5, 13.0), (10, 15.8), (20, 20.15), (30, 23.1), (60, 29.7)])
    + _series("Monoject", "", [(1, 5.74), (3, 8.94), (6, 12.7), (12, 15.72), (20, 20.12), (35, 23.8), (60, 26.7),
                               (140, 38.2)], approx=True)
    + _series("Hamilton", "Gastight 1000 series", [(1, 4.608), (2.5, 7.284), (5, 10.3), (10, 14.57), (25, 23.03),
                                                   (50, 32.57), (100, 46.07)])
    + _series("Hamilton", "Gastight 1700 series", _GASTIGHT_UL)
    + _series("Hamilton", "Microliter 700 series", [(0.005, 0.343)] + _GASTIGHT_UL)
    + _series("SGE", "Gas Tight", [(0.025, 0.73), (0.05, 1.03), (0.1, 1.46), (0.25, 2.3), (0.5, 3.26), (1, 4.61),
                                   (2.5, 7.28), (5, 10.3), (10, 14.6), (25, 23.0)])
    + _series("ILS", "Gastight", _GASTIGHT_UL + [(1, 4.608)], approx=True)
    + _series("Unimetrics", "Gastight", _GASTIGHT_UL + [(1, 4.608)], approx=True)
    + _series("Popper", "Micro-Mate", [(1, 4.64), (2, 8.86), (3, 8.88), (5, 11.86), (10, 14.34), (20, 19.13)],
              approx=True)
    + _series("Air-Tite", "HSW Norm-Ject", [(1, 4.69), (2, 9.65), (3, 9.65), (5, 12.45), (10, 15.9), (20, 20.05),
                                            (30, 22.5)], approx=True)
    + _series("B. Braun", "Omnifix", [(1, 4.69), (3, 9.65), (5, 12.45), (10, 15.9), (20, 20.05), (30, 22.5),
                                      (50, 28.9)], approx=True)
    + _series("B. Braun", "Injekt", [(2, 9.65), (5, 12.45), (10, 15.9), (20, 20.05)], approx=True)
    + _series("Exel", "", [(1, 4.78), (3, 8.66), (5, 12.06), (10, 14.5), (20, 19.13), (30, 21.7), (60, 26.7)],
              approx=True)
    + _series("Nipro", "", [(1, 4.7), (3, 8.95), (5, 13.0), (10, 15.8), (20, 20.15), (30, 23.1), (50, 29.1)],
              approx=True)
    + _series("Codan", "", [(2, 9.0), (5, 12.0), (10, 14.5), (20, 19.13), (50, 26.6)], approx=True)
    + _series("Fortuna", "Optima (glass)", [(1, 4.6), (2, 8.9), (5, 11.9), (10, 14.6), (20, 19.4), (50, 28.0),
                                            (100, 34.8)], approx=True)
)


def syringe_name(s: dict) -> str:
    """"BD Plastipak 10 ml"."""
    return f"{s.get('manufacturer', '')} {s.get('model', '')}".strip()


def _norm(name: str) -> str:
    s = str(name).lower().replace("µ", "u").replace("μ", "u").replace("cc", " ml")
    s = re.sub(r"(\d)\s*(ml|ul)\b", r"\1 \2", s)
    return " ".join(s.split())


def find_syringe(name: str, extra=()) -> dict | None:
    """A predefined (or one of the `extra` custom) syringes by name, e.g. "BD Plastipak 10 ml" (case, "µl"/"ul"
    and "cc"/"ml" insensitive); a name that is part of exactly one syringe's name also matches."""
    want = _norm(name)
    if not want:
        return None
    pool = list(extra) + SYRINGES
    for s in pool:
        if _norm(syringe_name(s)) == want or _norm(s.get("name", "")) == want:
            return dict(s)
    hits = [s for s in pool if want in _norm(syringe_name(s))]
    return dict(hits[0]) if len(hits) == 1 else None


def custom_syringe(diameter_mm: float, volume_ml: float | None = None, name: str = "Custom") -> dict:
    """A syringe that is not in the list: its inner diameter (mm) and optionally its volume (ml)."""
    d = float(diameter_mm)
    if not 0.05 <= d <= 100:
        raise ValueError(f"syringe inner diameter {d:g} mm is out of range")
    return {"manufacturer": "Custom", "model": name, "name": name, "volume_ml": volume_ml, "diameter_mm": d}


def syringe_manufacturers() -> list[str]:
    return sorted({s["manufacturer"] for s in SYRINGES})


# ======================================================================================== pump models
PUMP_PROTOCOLS = {
    "new_era": "New Era (NE-1000 basic mode)",
    "harvard_ultra": "Harvard Apparatus / KD Scientific Legato (ultra command set)",
    "harvard_legacy": "Harvard Apparatus legacy (PHD 2000 / Pump 11 / Pump 33)",
    "chemyx": "Chemyx Fusion / Nexus",
    "cavro_dt": "Cavro / DT OEM protocol (syringe drives)",
    "text": "Text commands (custom templates)",
    "simulated": "Simulated pump (no hardware)",
}


def _m(maker, models, protocol, verified=True, note=""):
    return {"manufacturer": maker, "models": list(models), "protocol": protocol, "verified": verified, "note": note}


# "verified": the command set is documented in the manufacturer's manual for these models; False: the pump is sold
# as compatible with (or an OEM version of) the protocol's reference pump — check before use.
PUMP_MODELS: list[dict] = [
    _m("New Era Pump Systems", ["NE-500", "NE-1000", "NE-1002X", "NE-1010", "NE-1200", "NE-1600", "NE-4000",
                                "NE-8000"], "new_era"),
    _m("Syringepump.com", ["SyringeONE (NE-1000)", "SyringeTWO (NE-4000)", "NE-1002X"], "new_era"),
    _m("World Precision Instruments", ["Aladdin AL-1000", "AL-1010", "AL-4000"], "new_era"),
    _m("ProSense", ["NE-1000", "NE-1002X", "NE-4000"], "new_era", note="New Era distributor"),
    _m("Braintree Scientific", ["BS-8000", "BS-9000"], "new_era", False, "New Era OEM"),
    _m("Kent Scientific", ["Genie Plus"], "new_era", False, "reported New Era compatible"),
    _m("Landgraf Laborsysteme", ["LA-30", "LA-100", "LA-120", "LA-160"], "new_era", False,
       "reported New Era compatible"),
    _m("Harvard Apparatus", ["Pump 11 Elite", "Pump 11 Pico Plus Elite", "PHD Ultra", "PHD Ultra CP",
                             "Pump 33 DDS"], "harvard_ultra"),
    _m("Harvard Apparatus", ["PHD 2000", "PHD 22/2000", "Pump 11", "Pump 11 Plus", "Pump 33"], "harvard_legacy",
       note="REV reverses the direction"),
    _m("KD Scientific", ["Legato 100", "Legato 110", "Legato 180", "Legato 200", "Legato 210", "Legato 270"],
       "harvard_ultra"),
    _m("KD Scientific", ["KDS 200", "KDS 210", "KDS 220", "KDS 230", "KDS 250", "KDS 260", "KDS 270"],
       "harvard_legacy", False, "legacy KDS RS-232 commands assumed Harvard 22-compatible"),
    _m("Cole-Parmer", ["Cole-Parmer single / dual syringe pumps (KD Scientific OEM)"], "harvard_legacy", False,
       "KD Scientific OEM"),
    _m("Fisherbrand", ["Fisherbrand syringe pumps (KD Scientific OEM)"], "harvard_legacy", False,
       "KD Scientific OEM"),
    _m("Chemyx", ["Fusion 100", "Fusion 200", "Fusion 4000", "Fusion 6000", "Nexus 3000", "Nexus 6000"], "chemyx"),
    _m("Tecan", ["Cavro XCalibur", "Cavro XLP 6000", "Cavro Centris"], "cavro_dt"),
    _m("Hamilton", ["PSD/2", "PSD/3", "PSD/4", "PSD/6"], "cavro_dt", note="DT protocol"),
    _m("TriContinent", ["C3000", "C24000"], "cavro_dt"),
    _m("Norgren Kloehn", ["V6", "Versa6"], "cavro_dt", False, "DT-compatible command mode"),
    _m("Runze Fluid", ["SY-01B", "SY-08"], "cavro_dt", False, "sold as Cavro compatible"),
    _m("Advanced Microfluidics", ["LSPone"], "cavro_dt", False, "sold as Cavro compatible"),
    _m("Open hardware / DIY", ["Arduino or other microcontroller pumps"], "text", note="write the command templates"),
    _m("Other", ["Any pump with text commands"], "text"),
    _m("mANY-MAZE", ["Simulated pump"], "simulated"),
]


def pump_manufacturers() -> list[str]:
    return sorted({m["manufacturer"] for m in PUMP_MODELS if m["protocol"] not in ("simulated",)})


def protocol_of(model: str) -> str | None:
    """The protocol of a pump model name (e.g. "Pump 11 Elite" → "harvard_ultra")."""
    want = model.strip().lower()
    for m in PUMP_MODELS:
        if any(want == x.lower() for x in m["models"]):
            return m["protocol"]
    return None


# ======================================================================================== units / numbers
_ML = {"ml": 1.0, "ul": 1e-3, "µl": 1e-3, "μl": 1e-3, "nl": 1e-6, "pl": 1e-9, "l": 1000.0}
_NUM = r"[-+]?\d+(?:\.\d*)?|[-+]?\.\d+"


def _to_ml(value: float, unit: str | None, default: str = "ml") -> float:
    return float(value) * _ML.get((unit or default).lower(), 1.0)


def _parse_volume(text: str, default_unit: str = "ml") -> float | None:
    m = re.search(rf"({_NUM})\s*([munpµμ]?l)?\b", text, re.I)
    return _to_ml(float(m.group(1)), m.group(2), default_unit) if m else None


def _ne_num(x: float) -> str:
    """A New Era number: at most 4 digits and a decimal point."""
    x = abs(float(x))
    for dec in (3, 2, 1, 0):
        s = f"{x:.{dec}f}"
        if sum(c.isdigit() for c in s.lstrip("0")) <= 4:
            break
    return (s.rstrip("0").rstrip(".") if "." in s else s) or "0"


def _rate_units(rate_ml_min: float, units=(("ml/min", 1.0), ("ul/min", 1e3), ("ul/hr", 6e4))):
    """(value, unit) with the value >= 1 where possible."""
    for unit, k in units:
        if rate_ml_min * k >= 1:
            return rate_ml_min * k, unit
    unit, k = units[-1]
    return rate_ml_min * k, unit


def _g(x: float) -> str:
    return f"{x:.6g}"


# ======================================================================================== pump state
class _Pump:
    """State of one pump channel (as last reported by the pump)."""

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.addr = int(cfg.get("address", 0) or 0)
        self.diameter: float | None = None
        self.syringe_ml: float | None = None
        self.running = False
        self.stalled = False
        self.direction = "inf"  # current / last direction ("inf" or "wdr")
        self.rate = 0.0  # ml/min
        self.target_ml = 0.0
        self.armed = False  # a run was started and has not ended yet
        self.stop_requested = False
        self.target_hit = False
        self.pulse_on = False
        self.inf_counter = self.wdr_counter = 0.0  # the pump's own counters (ml)
        self.inf_base = self.wdr_base = 0.0  # volume moved before the counters were last cleared
        self.position: int | None = None  # cavro_dt plunger position (steps)
        self.sim_stall = False
        self.sim_done = 0.0
        self.sim_t = 0.0

    @property
    def infused_ml(self) -> float:
        return self.inf_base + self.inf_counter

    @property
    def withdrawn_ml(self) -> float:
        return self.wdr_base + self.wdr_counter

    def fold_counters(self, which: str = "both"):
        """The pump cleared its counters: keep what they held in the totals."""
        if which in ("both", "inf"):
            self.inf_base += self.inf_counter
            self.inf_counter = 0.0
        if which in ("both", "wdr"):
            self.wdr_base += self.wdr_counter
            self.wdr_counter = 0.0

    def set_counter(self, ml: float, direction: str | None = None):
        if (direction or self.direction) == "wdr":
            self.wdr_counter = max(0.0, ml)
        else:
            self.inf_counter = max(0.0, ml)


# ======================================================================================== protocols
class _Protocol:
    """One pump command set. Methods return [(line, tag)]: every line with a tag expects one reply frame, which
    :meth:`parse` receives with the tag (tags: "ack", "run", "stop", "clear"/"clear_i"/"clear_w", queries)."""

    id = ""
    eol = "\r"
    baud = 9600
    content_match = False  # replies are matched by content (parse may return False: not the expected reply)

    def split(self, buf: bytes) -> tuple[list[str], bytes]:
        *lines, rest = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        return [ln.decode(errors="replace").strip() for ln in lines if ln.strip()], rest

    def setup(self, dev, p) -> list:
        return self.set_diameter(dev, p)

    def set_diameter(self, dev, p) -> list:
        return []

    def run(self, dev, p, direction: str, rate: float, volume: float) -> list:
        raise NotImplementedError

    def stop(self, dev, p) -> list:
        raise NotImplementedError

    def poll(self, dev, p) -> list:
        return []

    def parse(self, dev, frame: str, p, tag: str) -> bool:
        return True

    def tick(self, dev):
        """Called on every read (simulation)."""


class NewEraProtocol(_Protocol):
    id = "new_era"
    baud = 19200
    _ALARMS = {"S": "stalled", "R": "pump was reset (power interrupted)", "T": "safe mode communication time-out",
               "E": "safe mode command error", "O": "pumping program error"}
    _ERRORS = {"?": "unrecognised command", "?NA": "command not applicable now", "?OOR": "value out of range",
               "?COM": "invalid communications packet", "?IGN": "command ignored"}

    def split(self, buf):
        frames = []
        while True:
            i = buf.find(b"\x02")
            if i < 0:
                return frames, b""
            j = buf.find(b"\x03", i + 1)
            if j < 0:
                return frames, buf[i:]
            frames.append(buf[i + 1:j].decode(errors="replace"))
            buf = buf[j + 1:]

    def _a(self, p):
        return str(p.addr)

    def set_diameter(self, dev, p):
        return [(f"{self._a(p)}DIA {_ne_num(p.diameter)}", "ack")] if p.diameter else []

    def setup(self, dev, p):
        return self.set_diameter(dev, p) + [(f"{self._a(p)}CLD INF", "clear_i"), (f"{self._a(p)}CLD WDR", "clear_w")]

    def run(self, dev, p, direction, rate, volume):
        a = self._a(p)
        r, unit = _rate_units(rate, (("MM", 1.0), ("UM", 1e3), ("UH", 6e4)))
        vu, v = ("UL", volume * 1000) if 0 < volume < 1 else ("ML", volume)
        return [(f"{a}DIS", "vol"), (f"{a}CLD INF", "clear_i"), (f"{a}CLD WDR", "clear_w"),
                (f"{a}DIR {'INF' if direction == 'inf' else 'WDR'}", "ack"), (f"{a}RAT {_ne_num(r)} {unit}", "ack"),
                (f"{a}VOL {vu}", "ack"), (f"{a}VOL {_ne_num(v)}", "ack"), (f"{a}RUN", "run")]

    def stop(self, dev, p):
        return [(f"{self._a(p)}STP", "stop")]

    def poll(self, dev, p):
        return [(f"{self._a(p)}DIS", "vol")]

    def parse(self, dev, frame, p, tag):
        m = re.match(r"\s*(\d{1,2})(A\?(\w+)|[IWSPTUX])(.*)$", frame, re.S)
        if not m:
            dev._fail(p, f"unexpected reply {frame!r}")
            return True
        p = dev._pump_at(int(m.group(1))) or p
        status, alarm, data = m.group(2), m.group(3), m.group(4).strip()
        if alarm:
            if alarm == "S":
                dev._status(p, running=False, stalled=True)
            else:
                dev._fail(p, f"alarm: {self._ALARMS.get(alarm, alarm)}")
                dev._status(p, running=False)
            return True
        if data.startswith("?"):
            dev._fail(p, f"{self._ERRORS.get(data, 'error ' + data)} ({tag})")
        dv = re.match(rf"I\s*({_NUM})\s*W\s*({_NUM})\s*(UL|ML)", data, re.I)
        if dv:
            k = _ML[dv.group(3).lower()]
            p.inf_counter, p.wdr_counter = float(dv.group(1)) * k, float(dv.group(2)) * k
        if status in "IW":
            p.direction = "inf" if status == "I" else "wdr"
        dev._status(p, running=status in "IWX")
        return True


_PROMPT = re.compile(r"\s*(\d{0,2})(T\*|[:<>*])\s*")


class HarvardUltraProtocol(_Protocol):
    id = "harvard_ultra"
    _ERR = re.compile(r"error|out of range|unknown|invalid|not (?:allowed|valid)", re.I)

    def split(self, buf):
        """Frames end with a prompt ("01:", ">", "T*"...), which has no line end; data lines come before it."""
        text = buf.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        frames, data, start, pos = [], [], 0, 0
        *lines, tail = text.split("\n")
        for ln in lines:
            pos += len(ln) + 1
            s = ln.strip()
            if s and _PROMPT.fullmatch(s):
                frames.append("\n".join(data + [s]))
                data, start = [], pos
            elif s:
                data.append(s)
        if tail.strip() and _PROMPT.fullmatch(tail.strip()):
            frames.append("\n".join(data + [tail.strip()]))
            return frames, b""
        return frames, text[start:].encode()

    def _a(self, p):
        return f"{p.addr:02d}" if p.addr else ""

    def set_diameter(self, dev, p):
        return [(f"{self._a(p)}diameter {_g(p.diameter)}", "ack")] if p.diameter else []

    def setup(self, dev, p):
        return self.set_diameter(dev, p) + [(f"{self._a(p)}civolume", "clear_i"), (f"{self._a(p)}cwvolume", "clear_w")]

    def run(self, dev, p, direction, rate, volume):
        a, d = self._a(p), "i" if direction == "inf" else "w"
        r, unit = _rate_units(rate)
        tv = (f"{a}tvolume {_g(volume)} ml" if volume >= 1 else f"{a}tvolume {_g(volume * 1000)} ul") if volume \
            else f"{a}ctvolume"
        return [(f"{a}ivolume", "ivol"), (f"{a}wvolume", "wvol"), (f"{a}civolume", "clear_i"),
                (f"{a}cwvolume", "clear_w"), (f"{a}{d}rate {_g(r)} {unit}", "ack"), (tv, "ack"), (f"{a}{d}run", "run")]

    def stop(self, dev, p):
        return [(f"{self._a(p)}stop", "stop")]

    def poll(self, dev, p):
        return [(f"{self._a(p)}ivolume", "ivol"), (f"{self._a(p)}wvolume", "wvol")]

    def _prompt(self, dev, p, prompt: str):
        m = _PROMPT.fullmatch(prompt)
        if m.group(1):
            p = dev._pump_at(int(m.group(1))) or p
        s = m.group(2)
        if s == "*":
            dev._status(p, running=False, stalled=True)
        elif s == "T*":
            dev._status(p, running=False, target=True)
        else:
            if s in "<>":
                p.direction = "inf" if s == ">" else "wdr"
            dev._status(p, running=s in "<>")
        return p

    def parse(self, dev, frame, p, tag):
        *data, prompt = frame.split("\n")
        for ln in data:
            if self._ERR.search(ln):
                dev._fail(p, f"{ln} ({tag})")
            elif tag in ("ivol", "wvol", "vol"):
                v = _parse_volume(ln)
                if v is not None:
                    p.set_counter(v, {"ivol": "inf", "wvol": "wdr"}.get(tag))
        self._prompt(dev, p, prompt)
        return True


class HarvardLegacyProtocol(HarvardUltraProtocol):
    id = "harvard_legacy"

    def set_diameter(self, dev, p):
        return [(f"{self._a(p)}MMD {_g(p.diameter)}", "ack")] if p.diameter else []

    def setup(self, dev, p):
        return self.set_diameter(dev, p) + [(f"{self._a(p)}CLV", "clear")]

    def run(self, dev, p, direction, rate, volume):
        a = self._a(p)
        r, unit = _rate_units(rate, (("MLM", 1.0), ("ULM", 1e3), ("ULH", 6e4)))
        go = "RUN" if direction == p.direction else "REV"  # REV reverses the direction and runs
        # the VOL reply belongs to the direction the pump ran in so far, not to the new one
        cmds = [(f"{a}VOL", "wvol" if p.direction == "wdr" else "ivol"), (f"{a}CLV", "clear"),
                (f"{a}{unit} {_g(r)}", "ack")]
        cmds.append((f"{a}MLT {_g(volume)}", "ack") if volume else (f"{a}CLT", "ack"))
        p.direction = direction
        return cmds + [(f"{a}{go}", "run")]

    def stop(self, dev, p):
        return [(f"{self._a(p)}STP", "stop")]

    def poll(self, dev, p):
        # tagged with the direction at the time of the query: a reply that is still pending when a run reverses
        # the pump must not land in the other counter
        return [(f"{self._a(p)}VOL", "wvol" if p.direction == "wdr" else "ivol")]


class ChemyxProtocol(_Protocol):
    id = "chemyx"
    content_match = True
    _STATUS = re.compile(r"\s*(?:status\s*[=:]?\s*)?([0-5])\s*", re.I)
    _VOL = re.compile(rf"\s*(?:dispensed volume\s*[=:]?\s*)?({_NUM})\s*([munpµμ]?l)?\s*", re.I)

    def set_diameter(self, dev, p):
        return [("set units 0", None), (f"set diameter {_g(p.diameter)}", None)] if p.diameter else []

    def run(self, dev, p, direction, rate, volume):
        p.fold_counters()  # the pump restarts its dispensed volume at each start
        v = volume if volume else max(9999.0, p.syringe_ml or 0)  # Chemyx always needs a volume
        sign = "-" if direction == "wdr" else ""
        dev._status(p, running=True)
        return [("set units 0", None), (f"set rate {_g(rate)}", None), (f"set volume {sign}{_g(v)}", None),
                ("start", None)]

    def stop(self, dev, p):
        return [("stop", None)]

    def poll(self, dev, p):
        return [("status", "status"), ("dispensed volume", "vol")]

    def parse(self, dev, frame, p, tag):
        if tag == "status":
            m = self._STATUS.fullmatch(frame)
            if not m:
                return False
            s = int(m.group(1))
            if s == 4:
                dev._status(p, running=False, stalled=True)
            else:
                dev._status(p, running=s in (1, 3))
            return True
        if tag == "vol":
            m = self._VOL.fullmatch(frame)
            if not m:
                return False
            p.set_counter(abs(_to_ml(float(m.group(1)), m.group(2))))
            return True
        return False


class CavroProtocol(_Protocol):
    id = "cavro_dt"
    _ERRORS = {1: "initialisation error", 2: "invalid command", 3: "invalid operand", 4: "invalid command sequence",
               6: "EEPROM failure", 7: "device not initialised", 9: "plunger overload", 10: "valve overload",
               11: "plunger move not allowed", 15: "command overflow"}

    def split(self, buf):
        frames = []
        while True:
            i = buf.find(b"/0")
            if i < 0:
                return frames, buf[-1:] if buf.endswith(b"/") else b""
            j = buf.find(b"\x03", i + 2)
            if j < 0:
                return frames, buf[i:]
            frames.append(buf[i + 2:j].decode("latin-1"))
            buf = buf[j + 1:]

    def _a(self, p):
        return "/" + chr(ord("1") + p.addr)

    def _steps(self, p) -> tuple[int, float | None]:
        full = int(p.cfg.get("full_steps", 3000))
        vol = p.cfg.get("syringe_volume_ml") or p.syringe_ml
        return full, (float(vol) / full if vol else None)

    def setup(self, dev, p):
        cmds = [(f"{self._a(p)}ZR", "ack")] if p.cfg.get("initialize") else []
        return cmds + [(f"{self._a(p)}?", "pos")]

    def run(self, dev, p, direction, rate, volume):
        full, per = self._steps(p)
        if per is None:
            dev._fail(p, "the syringe volume is needed (syringe or syringe_volume_ml)")
            return None
        speed = rate / 60.0 / per * float(p.cfg.get("velocity_per_step_s", 1))
        speed = max(1, min(int(p.cfg.get("max_speed", 6000)), round(speed)))
        n = round(volume / per)
        if direction == "inf":
            move = f"D{n}" if volume else "A0"
        else:
            move = f"P{n}" if volume else f"A{full}"
        valve = ("O" if direction == "inf" else "I") if p.cfg.get("valve") else ""
        return [(f"{self._a(p)}{valve}V{speed}{move}R", "run")]

    def stop(self, dev, p):
        return [(f"{self._a(p)}TR", "stop")]

    def poll(self, dev, p):
        return [(f"{self._a(p)}Q", "status"), (f"{self._a(p)}?", "pos")]

    def parse(self, dev, frame, p, tag):
        if not frame:
            return True
        code, data = ord(frame[0]), frame[1:].strip()
        err = code & 0x0F
        if err == 9:
            dev._status(p, running=False, stalled=True)
        elif err:
            dev._fail(p, f"{self._ERRORS.get(err, f'error {err}')} ({tag})")
        if tag == "pos" and re.fullmatch(r"\d+", data):
            pos = int(data)
            _, per = self._steps(p)
            if p.position is not None and per:
                delta = pos - p.position
                if delta < 0:
                    p.inf_base += -delta * per
                else:
                    p.wdr_base += delta * per
            p.position = pos
        if tag in ("status", "pos") and err != 9:
            dev._status(p, running=not (code & 0x20))
        return True


class TextProtocol(_Protocol):
    id = "text"
    content_match = True

    def _cmds(self, dev):
        return dev.cfg.get("commands") or {}

    def _fmt(self, dev, p, key, rate=0.0, volume=0.0):
        t = self._cmds(dev).get(key)
        if not t:
            return []
        kw = dict(addr=p.addr, rate_ml_min=rate, rate_ul_min=rate * 1e3, rate_ml_h=rate * 60, rate_ul_h=rate * 6e4,
                  volume_ml=volume, volume_ul=volume * 1e3, diameter_mm=p.diameter or 0)
        return [(line.format(**kw), None) for line in (t if isinstance(t, list) else [t])]

    def setup(self, dev, p):
        return self._fmt(dev, p, "setup") + self.set_diameter(dev, p)

    def set_diameter(self, dev, p):
        return self._fmt(dev, p, "diameter") if p.diameter else []

    def run(self, dev, p, direction, rate, volume):
        cmds = self._fmt(dev, p, "infuse" if direction == "inf" else "withdraw", rate, volume)
        if not cmds:
            dev._fail(p, f"no '{'infuse' if direction == 'inf' else 'withdraw'}' command template")
            return None
        p.fold_counters()
        dev._status(p, running=True)
        return cmds

    def stop(self, dev, p):
        return self._fmt(dev, p, "stop")

    def poll(self, dev, p):
        return self._fmt(dev, p, "poll")

    def parse(self, dev, frame, p, tag):
        rx = dev.cfg.get("replies") or {}
        for key in ("stalled", "target", "stopped", "running", "infused", "withdrawn"):
            pat = rx.get(key)
            m = re.search(pat, frame) if pat else None
            if not m:
                continue
            q = p
            if "addr" in m.groupdict() and m.group("addr") is not None:
                q = dev._pump_at(int(m.group("addr"))) or p
            if q is None:
                continue
            if key == "stalled":
                dev._status(q, running=False, stalled=True)
            elif key == "target":
                dev._status(q, running=False, target=True)
            elif key in ("stopped", "running"):
                dev._status(q, running=key == "running")
            elif m.groups():
                q.set_counter(float(m.group(1)), "inf" if key == "infused" else "wdr")
        return False  # replies are not matched to commands


class SimulatedProtocol(_Protocol):
    id = "simulated"

    def run(self, dev, p, direction, rate, volume):
        p.fold_counters()
        p.sim_done = 0.0
        p.sim_t = dev.clock()
        p.direction = direction
        if p.sim_stall:
            dev._status(p, running=False, stalled=True)
        else:
            dev._status(p, running=True)
        return []

    def stop(self, dev, p):
        dev._status(p, running=False)
        return []

    def tick(self, dev):
        now = dev.clock()
        for p in dev.pumps.values():
            if not p.running:
                continue
            dt, p.sim_t = max(0.0, now - p.sim_t), now
            if p.sim_stall:
                dev._status(p, running=False, stalled=True)
                continue
            dv = p.rate * dt / 60.0
            done = p.target_ml > 0 and p.sim_done + dv >= p.target_ml - 1e-12
            if done:
                dv = max(0.0, p.target_ml - p.sim_done)
            p.sim_done += dv
            p.set_counter(p.sim_done)
            if done:
                dev._status(p, running=False, target=True)


_PROTOCOL_CLASSES = {c.id: c for c in (NewEraProtocol, HarvardUltraProtocol, HarvardLegacyProtocol, ChemyxProtocol,
                                       CavroProtocol, TextProtocol, SimulatedProtocol)}


# ======================================================================================== device
class SyringePumpDevice(_LineDevice):
    """Syringe pumps on one serial port (or simulated). Drive them with :meth:`pump`; their state is reported on
    derived input channels (see the module docstring)."""

    type = "syringe_pump"
    POLL_S = 0.2
    REPLY_TIMEOUT_S = 1.0
    STATUS_CHANNELS = (("running", "status"), ("stalled", "status"), ("target_reached", "status"),
                       ("infused_ml", "analog"), ("withdrawn_ml", "analog"))

    def __init__(self, cfg: dict, transport=None):
        cfg = dict(cfg)
        pid = cfg.get("protocol") or "new_era"
        cls = _PROTOCOL_CLASSES.get(pid)
        self.protocol = (cls or SimulatedProtocol)()
        cfg.setdefault("eol", self.protocol.eol)
        cfg.setdefault("baud", self.protocol.baud)
        super().__init__(cfg, transport)
        if cls is None:
            self._error(f"{self.name}: unknown pump protocol '{pid}' (simulated instead)")
        self.clock = time.monotonic
        self._rx = b""
        self._expect: deque = deque()  # (pump name, tag, deadline) of the replies still to come
        self._next_poll = 0.0
        self._frame_error = False
        self.pumps: dict[str, _Pump] = {}
        for n, c in list(self.channels.items()):
            if c.get("kind") != "pump":
                continue
            p = _Pump(n, c)
            self._resolve_syringe(p, c)
            self.pumps[n] = p
            for suffix, kind in self.STATUS_CHANNELS:
                ch = f"{n}.{suffix}"
                self.channels[ch] = {"name": ch, "kind": kind, "pump": n}
                self.inputs[ch] = 0
                self.outputs.pop(ch, None)

    @property
    def simulated(self) -> bool:
        return isinstance(self.protocol, SimulatedProtocol)

    # -- syringes
    def _resolve_syringe(self, p: _Pump, spec: dict) -> bool:
        extra = self.cfg.get("custom_syringes") or []
        if spec.get("diameter_mm") not in (None, ""):
            try:
                s = custom_syringe(float(spec["diameter_mm"]), spec.get("syringe_volume_ml") or spec.get("volume_ml"))
            except (TypeError, ValueError) as e:
                self._error(f"{self.name}: {p.name}: {e}")
                return False
        elif spec.get("syringe"):
            s = find_syringe(str(spec["syringe"]), extra)
            if s is None:
                self._error(f"{self.name}: {p.name}: unknown syringe '{spec['syringe']}'")
                return False
        else:
            return False
        p.diameter = float(s["diameter_mm"])
        p.syringe_ml = float(s["volume_ml"]) if s.get("volume_ml") else p.syringe_ml
        return True

    def _pump_at(self, addr: int) -> _Pump | None:
        return next((p for p in self.pumps.values() if p.addr == addr), None)

    # -- lifecycle
    def open(self):
        if self.simulated:
            self.connected = True
            return
        super().open()
        if self.connected:
            with self._io_lock:
                for p in self.pumps.values():
                    self._send(p, self.protocol.setup(self, p))

    # -- protocol callbacks
    def _fail(self, p: _Pump | None, msg: str):
        self._frame_error = True
        self._error(f"{self.name}: {p.name + ': ' if p else ''}{msg}")

    def _status(self, p: _Pump, running: bool | None = None, stalled: bool = False, target: bool = False):
        """A status reported by the pump: running or not, stalled, target volume reached."""
        if stalled:
            p.stalled = True
            running = False
        if target:
            if p.armed:  # the "ultra" pumps keep reporting T* until the next run: one edge per target
                p.target_hit = True
            p.armed = False
            running = False
        if running is None:
            return
        if p.running and not running and p.armed and not p.stop_requested and not p.stalled and p.target_ml > 0:
            p.target_hit = True  # stopped on its own after a target volume
        if running:
            p.armed = True
        elif p.running or stalled:
            p.armed = False
        if p.running and not running:
            self.outputs[p.name] = 0
        p.running = bool(running)

    # -- I/O
    def _send(self, p: _Pump, cmds) -> bool:
        if cmds is None:
            return False
        ok = True
        for line, tag in cmds:
            if not self.write_line(line):
                ok = False
                continue
            if tag is not None:
                self._expect.append((p.name, tag, self.clock() + self.REPLY_TIMEOUT_S))
        if not ok:
            self._error(f"{self.name}: not connected")
        return ok

    def _read_bytes(self) -> bytes:
        with self._io_lock:
            if self.transport is None:
                return b""
            try:
                n = getattr(self.transport, "in_waiting", 0)
                data = self.transport.read(n or 4096) if n is None or n > 0 else b""
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(f"{self.name}: read failed: {e}")
                return b""
            return data or b""

    def _expire(self):
        now = self.clock()
        while self._expect and self._expect[0][2] < now:
            name, tag, _ = self._expect.popleft()
            self._error(f"{self.name}: {name}: no reply from the pump")

    def _handle(self, frame: str):
        self._expire()
        head = self._expect[0] if self._expect else None
        p = self.pumps.get(head[0]) if head else None
        tag = head[1] if head else "unsolicited"
        if p is None:
            p = next(iter(self.pumps.values()), None)
            if p is None:
                return
        self._frame_error = False
        used = self.protocol.parse(self, frame, p, tag)
        if not used:
            return
        if head:
            self._expect.popleft()
        if self._frame_error:
            return
        if tag in ("clear", "clear_i", "clear_w"):
            p.fold_counters({"clear": "both", "clear_i": "inf", "clear_w": "wdr"}[tag])
        elif tag == "run" and not p.stalled:
            p.armed = True
            p.running = True

    def _read(self):
        with self._io_lock:
            if self.simulated:
                if self.connected:
                    self.protocol.tick(self)
            else:
                data = self._read_bytes()
                if data or self._rx:
                    frames, self._rx = self.protocol.split(self._rx + data)
                    for f in frames:
                        try:
                            self._handle(f)
                        except Exception as e:  # never let a garbled reply stop the polling
                            self._error(f"{self.name}: bad reply {f!r}: {e}")
                self._expire()
                now = self.clock()
                if self.connected and now >= self._next_poll and len(self._expect) < 6 * max(1, len(self.pumps)):
                    self._next_poll = now + self.POLL_S
                    for p in self.pumps.values():
                        self._send(p, self.protocol.poll(self, p))
            for p in self.pumps.values():
                self._publish(p)

    def _publish(self, p: _Pump):
        n = p.name
        self._changed(f"{n}.running", 1 if p.running else 0)
        self._changed(f"{n}.stalled", 1 if p.stalled else 0)
        self._changed(f"{n}.infused_ml", round(p.infused_ml, 6))
        self._changed(f"{n}.withdrawn_ml", round(p.withdrawn_ml, 6))
        if p.target_hit:
            p.target_hit = False
            p.pulse_on = True
            if self.inputs.get(f"{n}.target_reached"):  # still high from a previous pulse: make it a new edge
                self._changed(f"{n}.target_reached", 0)
            self._changed(f"{n}.target_reached", 1)
        elif p.pulse_on:
            p.pulse_on = False
            self._changed(f"{n}.target_reached", 0)

    # -- API
    def pump(self, channel: str, op: str, **kw) -> bool:
        """Drive one pump: op "infuse" / "withdraw" (rate_ml_min, volume_ml: 0 = until stopped), "stop" or
        "set_syringe" (syringe name or diameter_mm). False (and an error in ``errors``) if it could not be sent."""
        try:
            with self._io_lock:
                p = self.pumps.get(channel)
                if p is None:
                    self._error(f"{self.name}: unknown pump '{channel}'")
                    return False
                if op in ("infuse", "withdraw"):
                    rate = float(kw.get("rate_ml_min", p.cfg.get("rate_ml_min", 1.0)) or 0)
                    vol = float(kw.get("volume_ml", 0) or 0)
                    if rate <= 0 or vol < 0:
                        self._error(f"{self.name}: {channel}: invalid rate {rate:g} ml/min or volume {vol:g} ml")
                        return False
                    if p.diameter is None and not self.simulated and not isinstance(self.protocol, CavroProtocol):
                        self._error(f"{self.name}: {channel}: no syringe set")
                        return False
                    direction = "inf" if op == "infuse" else "wdr"
                    p.stalled = False
                    p.stop_requested = False
                    p.target_hit = False
                    p.armed = False
                    p.rate, p.target_ml = rate, vol
                    ok = self._send(p, self.protocol.run(self, p, direction, rate, vol))
                    p.direction = direction
                    if ok:
                        self.outputs[channel] = rate if direction == "inf" else -rate
                    return ok
                if op == "stop":
                    p.stop_requested = True
                    self.outputs[channel] = 0
                    return self._send(p, self.protocol.stop(self, p))
                if op == "set_syringe":
                    if not self._resolve_syringe(p, kw):
                        if not kw.get("syringe") and kw.get("diameter_mm") in (None, ""):
                            self._error(f"{self.name}: {channel}: set_syringe needs a syringe or diameter_mm")
                        return False
                    return self._send(p, self.protocol.set_diameter(self, p))
                self._error(f"{self.name}: {channel}: unknown pump operation '{op}'")
                return False
        except Exception as e:  # hardware or protocol problem: never raise into a running test
            self._error(f"{self.name}: {channel}: {op} failed: {e}")
            return False

    def set_output(self, channel: str, value: float, max_s: float | None = None):
        """Output-style control of a pump channel: > 0 infuse, < 0 withdraw (at the channel's rate_ml_min), 0 stop."""
        if channel not in self.pumps:
            self.outputs[channel] = value
            return
        if not value:
            self.pump(channel, "stop")
        else:
            self.pump(channel, "infuse" if value > 0 else "withdraw")

    def simulate_stall(self, channel: str, on: bool = True):
        """Simulated pumps: force (or clear) a stall."""
        p = self.pumps.get(channel)
        if p is not None:
            p.sim_stall = bool(on)
            if not on:
                p.stalled = False

    def all_off(self):
        """Stop every pump."""
        for name, p in self.pumps.items():
            if self.connected:
                self.pump(name, "stop")
            else:
                p.running = False
                self.outputs[name] = 0
