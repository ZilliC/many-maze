"""The old rules format ({"trigger", "action", ...}) and its serial / beep destination; rules are converted to
procedures on load."""

from __future__ import annotations

import sys

TRIGGERS = ["start", "end", "time", "zone_enter", "zone_exit", "freezing_start", "freezing_end", "immobile_start",
            "immobile_end", "not_detected"]
ACTIONS = ["serial", "ttl", "beep", "mark", "end_test", "log"]


class Outputs:
    """Legacy destination for "serial" / "beep" actions (and the action log). Serial port is optional."""

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


def is_legacy_rule(d) -> bool:
    return isinstance(d, dict) and "trigger" in d and "statements" not in d


_LEGACY_EVENT = {"start": "test_start", "end": "test_end", "time": "time_reached", "zone_enter": "zone_enter",
                 "zone_exit": "zone_exit", "freezing_start": "freezing_start", "freezing_end": "freezing_end",
                 "immobile_start": "immobile_start", "immobile_end": "immobile_end", "not_detected": "animal_lost"}


def convert_rule(rule: dict) -> dict:
    """Old {"trigger", "action", ...} rule -> equivalent procedure."""
    trig = rule.get("trigger", "start")
    when = {"type": "when", "event": _LEGACY_EVENT.get(trig, trig), "mode": "parallel"}
    if trig == "time":
        when["time"] = rule.get("time_s", 0)
    if trig in ("zone_enter", "zone_exit"):
        when["zone"] = rule.get("zone", "")
    body = []
    if rule.get("delay_s"):
        body.append({"type": "wait", "mode": "seconds", "seconds": rule["delay_s"]})
    act, payload = rule.get("action", "log"), str(rule.get("payload", "") or "")
    if act in ("serial", "ttl"):
        body.append({"type": "do", "action": "serial_send", "device": "", "text": payload})
    elif act == "mark":
        body.append({"type": "do", "action": "mark", "name": payload or "Mark"})
    elif act == "log":
        body.append({"type": "do", "action": "log", "text": payload})
    else:
        body.append({"type": "do", "action": act})
    when["body"] = body
    stmts = [when]
    if trig in ("zone_enter", "zone_exit") and not str(when.get("zone") or "").strip():
        # the old rule matched no zone, so it never fired; as a procedure an empty zone means "any zone"
        when["enabled"] = False
        stmts.insert(0, {"type": "comment", "text": "Converted from an old rule without a zone, which never ran: "
                                                    "disabled (choose a zone and enable it to use it)"})
    return {"name": describe_rule(rule), "enabled": True, "statements": stmts}


def describe_rule(rule: dict) -> str:
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

