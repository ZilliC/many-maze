"""Live test procedures: rules that react to the animal in real time (I/O control).

A rule has a trigger and an action::

    {"trigger": "zone_enter", "zone": "Open arms", "action": "serial", "payload": "LED1 ON"}
    {"trigger": "time", "time_s": 120, "action": "beep"}
    {"trigger": "time", "time_s": 30, "action": "mark", "payload": "Tone"}
    {"trigger": "freezing_start", "action": "serial", "payload": "SHOCK OFF"}
    {"trigger": "zone_enter", "zone": "Platform", "action": "end_test", "delay_s": 10}

Triggers: start, end, time, zone_enter, zone_exit, freezing_start, freezing_end,
immobile_start, immobile_end, not_detected.
Actions: serial (write payload + newline to the serial port), ttl (alias for serial
"<payload>" — use an Arduino sketch to drive pins), beep, mark (adds a point event),
end_test, log.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

TRIGGERS = ["start", "end", "time", "zone_enter", "zone_exit", "freezing_start", "freezing_end", "immobile_start",
            "immobile_end", "not_detected"]
ACTIONS = ["serial", "ttl", "beep", "mark", "end_test", "log"]


class Outputs:
    """Destination for actions. Serial port is optional (pyserial)."""

    def __init__(self, port: str | None = None, baud: int = 9600):
        self.port_name = port
        self.serial = None
        self.log: list[str] = []
        if port:
            try:
                import serial  # type: ignore

                self.serial = serial.Serial(port, baud, timeout=0)
            except Exception as e:  # pragma: no cover - hardware dependent
                self.log.append(f"Could not open serial port {port}: {e}")

    def write(self, payload: str):
        self.log.append(f"serial: {payload}")
        if self.serial is not None:  # pragma: no cover - hardware dependent
            self.serial.write((payload + "\n").encode())

    def beep(self):
        self.log.append("beep")
        try:
            if sys.platform == "darwin":  # pragma: no cover
                import subprocess

                subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"])
            else:
                print("\a", end="", flush=True)
        except Exception:  # pragma: no cover
            pass

    def close(self):
        if self.serial is not None:  # pragma: no cover
            self.serial.close()


@dataclass
class ProcedureEngine:
    rules: list
    outputs: Outputs = field(default_factory=Outputs)
    on_mark: Callable[[str, float], None] | None = None
    on_end: Callable[[], None] | None = None
    _prev_zones: dict = field(default_factory=dict)
    _fired_time: set = field(default_factory=set)
    _pending: list = field(default_factory=list)  # (due_time, rule)
    _prev_state: dict = field(default_factory=dict)
    ended: bool = False
    fired: list = field(default_factory=list)

    def start(self, t: float = 0.0):
        self._fire_matching("start", t)

    def stop(self, t: float):
        self._fire_matching("end", t)

    def update(self, t: float, zones: dict[str, bool], detected: bool = True, freezing: bool = False,
               immobile: bool = False):
        """Call once per frame with the current zone occupancy (name -> bool)."""
        for i, r in enumerate(self.rules):
            if r.get("trigger") == "time" and i not in self._fired_time and t >= float(r.get("time_s", 0)):
                self._fired_time.add(i)
                self._schedule(r, t)
        for zn, inside in zones.items():
            was = self._prev_zones.get(zn)
            if was is not None and inside != was:
                self._fire_matching("zone_enter" if inside else "zone_exit", t, zone=zn)
            elif was is None and inside:
                self._fire_matching("zone_enter", t, zone=zn)
            self._prev_zones[zn] = inside
        for name, val in (("freezing", freezing), ("immobile", immobile)):
            was = self._prev_state.get(name, False)
            if val and not was:
                self._fire_matching(f"{name}_start", t)
            elif was and not val:
                self._fire_matching(f"{name}_end", t)
            self._prev_state[name] = val
        if not detected and self._prev_state.get("detected", True):
            self._fire_matching("not_detected", t)
        self._prev_state["detected"] = detected
        due = [p for p in self._pending if p[0] <= t]
        self._pending = [p for p in self._pending if p[0] > t]
        for _, r in due:
            self._do(r, t)

    def _fire_matching(self, trigger, t, zone=None):
        for r in self.rules:
            if r.get("trigger") != trigger:
                continue
            if zone is not None and r.get("zone") and r.get("zone") != zone:
                continue
            if zone is not None and not r.get("zone"):
                continue
            self._schedule(r, t)

    def _schedule(self, r, t):
        d = float(r.get("delay_s", 0) or 0)
        if d > 0:
            self._pending.append((t + d, r))
        else:
            self._do(r, t)

    def _do(self, r, t):
        a = r.get("action")
        payload = str(r.get("payload", ""))
        self.fired.append((round(t, 3), r.get("trigger"), a, payload))
        if a in ("serial", "ttl"):
            self.outputs.write(payload)
        elif a == "beep":
            self.outputs.beep()
        elif a == "mark":
            if self.on_mark:
                self.on_mark(payload or "Mark", t)
        elif a == "end_test":
            self.ended = True
            if self.on_end:
                self.on_end()
        else:
            self.outputs.log.append(f"{t:.2f}s {payload}")


def describe(rule: dict) -> str:
    trig = rule.get("trigger", "?")
    if trig == "time":
        trig = f"at {rule.get('time_s', 0)} s"
    elif trig in ("zone_enter", "zone_exit"):
        trig = f"{'enters' if trig == 'zone_enter' else 'exits'} {rule.get('zone', '?')}"
    else:
        trig = trig.replace("_", " ")
    d = rule.get("delay_s")
    delay = f" after {d} s" if d else ""
    act = rule.get("action", "?")
    payload = rule.get("payload", "")
    return f"When {trig}{delay}: {act} {payload}".strip()
