"""Test procedures: automate live tests (timing, decisions, loops, variables, hardware I/O).

Procedure format (JSON, stored in ``Project.procedures``)
==========================================================
A project has any number of procedures; all enabled procedures run simultaneously during a live test::

    {"name": "Fear conditioning", "enabled": true, "statements": [
        {"type": "var", "name": "shocks", "value": 0, "result": true},
        {"type": "wait", "seconds": 120},
        {"type": "repeat", "count": 3, "body": [
            {"type": "do", "action": "tone", "frequency": 2800, "duration": 30, "volume": 0.8},
            {"type": "wait", "seconds": 28},
            {"type": "do", "action": "shock_pulse", "device": "box", "channel": "shocker", "duration": 2},
            {"type": "set", "var": "shocks", "value": "shocks + 1"},
            {"type": "wait", "seconds": "randint(60, 120)"}]}]}

A procedure's top-level statements are ``when`` handlers, ``var`` declarations, comments and, optionally, plain
statements, which run in order from the start of the test (an implicit "when the test starts").
Each running handler is an independent cooperative thread: waits never block the frame loop.

Statements (``"type"``)::

    when     {"event": name, <event parameters>, "mode": "ignore"|"restart"|"parallel", "once": bool, "body": [...]}
             top level only. mode: what happens when the event recurs while the handler is still running
             (ignore it — default, restart the handler, or run another copy in parallel).
    wait     {"mode": "seconds", "seconds": expr}
             {"mode": "until", "until": condition, "timeout": expr?}
             {"mode": "event", "event": name, <event parameters>, "timeout": expr?}
             after a timeout the local variable ``timed_out`` is 1 (else 0).
    if       {"cond": condition, "body": [...], "else": [...]}           ("else" optional)
    repeat   {"mode": "count", "count": expr, "var": name?, "body": [...]}
             {"mode": "while", "while": condition, "var": name?, "body": [...]}
             {"mode": "forever", "body": [...]}
             "var" receives the iteration number (0, 1, ...). "count" and "while" loops run instantly (a
             thread yields to the next frame after 5000 statements); a "forever" loop whose body did not
             wait advances one iteration per frame (it polls).
    set      {"var": name, "value": expr, "index": expr?}                  (index: set one array element)
    do       {"action": name, <action parameters>}
    stop     {"what": "handler"|"loop"|"procedure"|"all"|"test"}
    comment  {"text": "..."}
    var      {"name": name, "value": expr, "keep": bool, "result": bool}  top level only.
             keep: the value is kept between tests (``Project.variables``); result: a numeric variable saved as
             a test result (``Test.result_variables``).

Any statement may carry ``"enabled": false`` to skip it. Expressions are strings (or numbers); conditions are
expressions whose truth value is used. Text parameters may embed expressions in braces: ``"count = {count}"``.

Timing: a handler started by a timed event, and the statements after a ``wait``, run on the first frame at or
after the due time; the next wait counts from the due time, not the frame time, so sequences never drift.

Expressions
===========
Evaluated by a safe interpreter built on ``ast`` (never ``eval``): numbers, strings, lists ``[1, 2, 3]``,
``+ - * / // % **``, comparisons, ``and or not``, ``a if c else b``, ``x[i]`` / slices and calls of the
functions listed in ``FUNCTIONS`` (maths, random numbers, and live state such as ``zone("Centre")``,
``input("box", "lever")``, ``timer("iti")``, ``time()``). Variables are global to all procedures; arrays are lists.

Events (``EVENT_SPECS``) and actions (``ACTION_SPECS``) are catalogued below with their parameters.

Engine
======
``ProcedureEngine(procedures, devices, on_mark=, on_end=, on_log=, variables=, context=)``, then
``start(t)``, ``update_state(t, state)`` once per frame, ``key(t, key, down)``, ``touch(t, area)``,
``mark_event(t, name)``, ``stop(t)``. Results: ``ended``, ``errors``, ``io_events`` (``Test.io_events``
format), ``result_variables``, ``marks``, ``pauses``. Old projects stored rules
``{"trigger", "action", "payload", "zone", "time_s", "delay_s"}``; they are converted on load
(:func:`normalize_procedures`) and the old ``ProcedureEngine(rules, outputs).update(t, zones, ...)`` API works.
"""

from __future__ import annotations

import ast
import copy
import heapq
import keyword
import math
import operator as op
import random
import re
import sys
import threading
from collections import deque
from functools import wraps
from typing import Callable

from .operant import Schedule, parse_spec

# ====================================================================== legacy (rule) API
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


# ====================================================================== catalogues
def P(name, type="number", default=None, label=None, req=False, help=""):
    """Parameter spec. Types: number, int, expr, text, zone, key, device, input, output, audio, var, timer,
    switch, area, name, procedure, file, schedule, spec, sequence, bool, choice:a|b|c."""
    return {"name": name, "type": type, "default": default, "label": label or name.replace("_", " ").capitalize(),
            "req": req, "help": help}


_DEV = P("device", "device", "", "Device", help="empty = the device that has this channel")
_IN = P("channel", "input", "", "Input", req=True)
_OUT = P("channel", "output", "", "Output", req=True)
_ZONE_ANY = P("zone", "zone", "", "Zone", help="empty = any zone")
_ZONE = P("zone", "zone", "", "Zone", req=True)

# name: (group, label, params). "generic" events are matched against occurrences; the others are detectors.
EVENT_SPECS: dict[str, dict] = {}


def _ev(name, group, label, params=(), generic=True, help=""):
    EVENT_SPECS[name] = {"group": group, "label": label, "params": list(params), "generic": generic, "help": help}


_ev("test_start", "Test", "Test starts")
_ev("test_end", "Test", "Test ends")
_ev("time_reached", "Test", "Time reached", [P("time", "number", 60, "Time (s)", True)], False,
    "once, when the test time reaches the given time")
_ev("every", "Test", "Every N seconds", [P("interval", "number", 10, "Interval (s)", True),
                                          P("first", "number", None, "First at (s)", help="default: one interval")],
    False)
_ev("test_paused", "Test", "Test paused")
_ev("test_resumed", "Test", "Test resumed")
_ev("zone_enter", "Zones", "Animal enters zone", [_ZONE_ANY])
_ev("zone_exit", "Zones", "Animal leaves zone", [_ZONE_ANY])
_ev("head_zone_enter", "Zones", "Head enters zone", [_ZONE_ANY])
_ev("head_zone_exit", "Zones", "Head leaves zone", [_ZONE_ANY])
_ev("zone_time_reaches", "Zones", "Total time in zone reaches", [_ZONE, P("seconds", "number", 30, "Time (s)", True)],
    False)
_ev("zone_dwell", "Zones", "Time in zone (one visit) reaches", [_ZONE, P("seconds", "number", 5, "Time (s)", True)],
    False, "fires once per visit that lasts at least this long")
_ev("zone_entries_reach", "Zones", "Zone entries reach", [_ZONE, P("count", "int", 5, "Entries", True)], False)
_ev("zone_sequence", "Zones", "Zone sequence completed",
    [P("sequence", "sequence", "", "Zones (in order)", True, "e.g. A, B, C — entries of other zones are ignored")],
    False)
_ev("freezing_start", "Animal", "Freezing starts")
_ev("freezing_end", "Animal", "Freezing ends")
_ev("immobile_start", "Animal", "Immobility starts")
_ev("immobile_end", "Animal", "Immobility ends")
_ev("animal_lost", "Animal", "Animal not detected")
_ev("animal_found", "Animal", "Animal detected again")
_ev("speed_above", "Animal", "Speed rises above", [P("threshold", "number", 10, "Speed", True)], False)
_ev("speed_below", "Animal", "Speed falls below", [P("threshold", "number", 2, "Speed", True)], False)
_ev("distance_reaches", "Animal", "Distance travelled reaches", [P("distance", "number", 100, "Distance", True)],
    False)
_ev("freezing_time_reaches", "Animal", "Total freezing time reaches", [P("seconds", "number", 30, "Time (s)", True)],
    False)
_ev("immobile_time_reaches", "Animal", "Total immobile time reaches", [P("seconds", "number", 30, "Time (s)", True)],
    False)
_ev("key_down", "Keyboard", "Key pressed", [P("key", "key", "", "Key", help="empty = any key")])
_ev("key_up", "Keyboard", "Key released", [P("key", "key", "", "Key", help="empty = any key")])
_ev("input_on", "Inputs", "Input switches on", [_DEV, _IN])
_ev("input_off", "Inputs", "Input switches off", [_DEV, _IN])
_ev("input_changed", "Inputs", "Input changes", [_DEV, _IN])
_ev("input_count_reaches", "Inputs", "Input activations reach", [_DEV, _IN, P("count", "int", 10, "Count", True)],
    False)
_ev("analog_above", "Inputs", "Analogue input rises above", [_DEV, _IN, P("threshold", "number", 512, "Level", True)],
    False)
_ev("analog_below", "Inputs", "Analogue input falls below", [_DEV, _IN, P("threshold", "number", 512, "Level", True)],
    False)
_ev("encoder_reaches", "Inputs", "Encoder count reaches", [_DEV, _IN, P("count", "int", 1000, "Counts", True)], False)
_ev("encoder_every", "Inputs", "Every N encoder counts", [_DEV, _IN, P("count", "int", 1024, "Counts", True)], False,
    "e.g. once per wheel revolution")
_ev("output_on", "Outputs", "Output switched on", [_DEV, P("channel", "output", "", "Output")])
_ev("output_off", "Outputs", "Output switched off", [_DEV, P("channel", "output", "", "Output")])
_ev("variable_changed", "Logic", "Variable changes", [P("var", "var", "", "Variable", True)])
_ev("condition_true", "Logic", "Condition becomes true", [P("cond", "expr", "", "Condition", True)], False)
_ev("condition_false", "Logic", "Condition becomes false", [P("cond", "expr", "", "Condition", True)], False)
_ev("timer_elapsed", "Logic", "Timer elapses", [P("timer", "timer", "", "Timer", True)])
_ev("signal", "Logic", "Signal received", [P("name", "name", "", "Signal", True)],
    help="sent by the “Send signal” action of any procedure")
_ev("virtual_switch_on", "Logic", "Virtual switch on", [P("switch", "switch", "", "Switch", True)])
_ev("virtual_switch_off", "Logic", "Virtual switch off", [P("switch", "switch", "", "Switch", True)])
_ev("event_marked", "Logic", "Event marked", [P("name", "name", "", "Event", help="empty = any")])
_ev("reinforcer_earned", "Logic", "Reinforcer earned", [P("schedule", "schedule", "", "Schedule", True)])
_ev("touch", "Touch screen", "Touch in area", [P("area", "area", "", "Area", help="empty = any area")])
_ev("touch_outside", "Touch screen", "Touch outside all areas")

ACTION_SPECS: dict[str, dict] = {}


def _ac(name, group, label, params=(), help=""):
    ACTION_SPECS[name] = {"group": group, "label": label, "params": list(params), "help": help}


_OUTS = [_DEV, _OUT]
_ac("output_on", "Outputs", "Switch output on", _OUTS)
_ac("output_off", "Outputs", "Switch output off", _OUTS)
_ac("output_toggle", "Outputs", "Toggle output", _OUTS)
_ac("output_pulse", "Outputs", "Pulse output", _OUTS + [P("duration", "number", 0.5, "Duration (s)", True)])
_ac("output_set", "Outputs", "Set output level", _OUTS + [P("value", "number", 0.5, "Level (0–1)", True)])
_ac("all_outputs_off", "Outputs", "Switch all outputs off", [P("device", "device", "", "Device", help="empty = all")])
_ac("pulse_train", "Outputs", "Pulse train (optogenetics)",
    _OUTS + [P("frequency", "number", 20, "Frequency (Hz)", True),
             P("pulse_width", "number", 5, "Pulse width (ms)", True),
             P("duration", "number", 1, "Duration (s)", True, "0 = until stopped")])
_ac("pulse_train_stop", "Outputs", "Stop pulse train", _OUTS)
_ac("sync_pulse", "Outputs", "Sync pulse (e-phys / imaging)", _OUTS + [P("width", "number", 10, "Width (ms)", True)])
_ac("pellet", "Operant", "Dispense pellet(s)",
    _OUTS + [P("count", "int", 1, "Pellets", True), P("pulse_width", "number", 50, "Pulse (ms)"),
             P("gap", "number", 0.5, "Gap between pellets (s)")])
_ac("light_on", "Operant", "Light on", _OUTS)
_ac("light_off", "Operant", "Light off", _OUTS)
_ac("door_open", "Operant", "Open door", _OUTS)
_ac("door_close", "Operant", "Close door", _OUTS)
_ac("lever_extend", "Operant", "Extend lever", _OUTS)
_ac("lever_retract", "Operant", "Retract lever", _OUTS)
_ac("schedule_start", "Operant", "Start reinforcement schedule",
    [P("schedule", "schedule", "", "Schedule name", True), P("spec", "spec", "FR 5", "Schedule", True)])
_ac("schedule_response", "Operant", "Register response",
    [P("schedule", "schedule", "", "Schedule name", True), P("spec", "spec", "", "Schedule", help="if not started"),
     P("var", "var", "", "Store 1/0 (reinforced) in")])
_ac("shock_on", "Shock", "Shock on", _OUTS + [P("max_duration", "number", 2, "Safety cut-off (s)", True)])
_ac("shock_off", "Shock", "Shock off", _OUTS)
_ac("shock_pulse", "Shock", "Shock for a duration", _OUTS + [P("duration", "number", 1, "Duration (s)", True)])
_AUD = P("device", "audio", "", "Audio device", help="empty = first audio device")
_ac("tone", "Audio", "Play tone", [_AUD, P("frequency", "number", 2000, "Frequency (Hz)", True),
                                   P("duration", "number", 1, "Duration (s)", True),
                                   P("volume", "number", 0.5, "Volume (0–1)")])
_ac("white_noise", "Audio", "Play white noise", [_AUD, P("duration", "number", 1, "Duration (s)", True),
                                                 P("volume", "number", 0.5, "Volume (0–1)")])
_ac("play_sound", "Audio", "Play sound file", [_AUD, P("file", "file", "", "File (WAV)", True),
                                               P("duration", "number", 0, "Duration (s)", help="for the I/O log"),
                                               P("volume", "number", 1.0, "Volume (0–1)")])
_ac("stop_sound", "Audio", "Stop sounds", [_AUD])
_ac("beep", "Audio", "Beep")
_ac("serial_send", "Communication", "Send serial command", [P("device", "device", "", "Device"),
                                                             P("text", "text", "", "Command", True)])
_ac("signal", "Communication", "Send signal", [P("name", "name", "", "Signal", True)])
_ac("virtual_switch_on", "Communication", "Virtual switch on", [P("switch", "switch", "", "Switch", True)])
_ac("virtual_switch_off", "Communication", "Virtual switch off", [P("switch", "switch", "", "Switch", True)])
_ac("virtual_switch_toggle", "Communication", "Toggle virtual switch", [P("switch", "switch", "", "Switch", True)])
_ac("simulate_input", "Communication", "Simulate input", [_DEV, _IN, P("value", "number", 1, "Value", True)])
_ac("set_variable", "Variables", "Set variable", [P("var", "var", "", "Variable", True),
                                                  P("value", "expr", "0", "Value", True)])
_ac("increment", "Variables", "Increment variable", [P("var", "var", "", "Variable", True), P("by", "number", 1, "By")])
_ac("decrement", "Variables", "Decrement variable", [P("var", "var", "", "Variable", True), P("by", "number", 1, "By")])
_ac("array_append", "Variables", "Append to array", [P("var", "var", "", "Array", True),
                                                     P("value", "expr", "0", "Value", True)])
_ac("start_timer", "Variables", "Start timer", [P("timer", "timer", "", "Timer", True),
                                               P("seconds", "number", 0, "Elapses after (s)", help="0 = never")])
_ac("stop_timer", "Variables", "Stop timer", [P("timer", "timer", "", "Timer", True)])
_ac("reset_timer", "Variables", "Reset timer", [P("timer", "timer", "", "Timer", True)])
_ac("mark", "Test", "Mark event", [P("name", "text", "Mark", "Event", True)])
_ac("mark_start", "Test", "Start state event", [P("name", "text", "", "Event", True)])
_ac("mark_end", "Test", "End state event", [P("name", "text", "", "Event", True)])
_ac("log", "Test", "Write to log", [P("text", "text", "", "Message", True)])
_ac("end_test", "Test", "End the test")
_ac("pause_test", "Test", "Pause the test")
_ac("resume_test", "Test", "Resume the test")
_ac("enable_procedure", "Test", "Enable procedure", [P("procedure", "procedure", "", "Procedure", True)])
_ac("disable_procedure", "Test", "Disable procedure", [P("procedure", "procedure", "", "Procedure", True)])
_ac("show_stimulus", "Touch screen", "Show stimulus",
    [P("area", "area", "", "Area", True), P("image", "file", "", "Image", help="empty = shape"),
     P("shape", "choice:circle|square|triangle|star|cross|bars", "circle", "Shape"),
     P("color", "text", "#ffffff", "Colour")])
_ac("hide_stimulus", "Touch screen", "Hide stimulus", [P("area", "area", "", "Area", True)])
_ac("clear_screen", "Touch screen", "Clear screen")

STATEMENT_TYPES = {
    "when": "When", "wait": "Wait", "if": "If", "repeat": "Repeat", "set": "Set", "do": "Do", "stop": "Stop",
    "comment": "Comment", "var": "Variable",
}
CONTAINERS = ("when", "if", "repeat")
STOP_WHAT = {"handler": "Exit this handler", "loop": "Exit the loop", "procedure": "Stop this procedure",
             "all": "Stop all procedures", "test": "End the test"}
WHEN_MODES = {"ignore": "Ignore while running", "restart": "Restart", "parallel": "Run in parallel"}
LOCAL_NAMES = ("event_time", "event_value", "event_name", "timed_out")
CONSTANTS = {"true": True, "false": False, "True": True, "False": False, "pi": math.pi, "e": math.e,
             "inf": math.inf, "nan": math.nan, "none": None, "None": None}
SHOCK_MAX_S = 60.0  # hard cap on any continuous shock
STALL_S = 0.25  # a software pulse train later than this (frames stalled) is delayed instead of bursting
SAFETY_TASKS = ("shock", "audio")  # scheduled tasks that keep running in real time while the test is paused
STEP_BUDGET = 5000  # statements a thread may run per frame before yielding
EPS = 1e-6

# ====================================================================== expressions
MAX_EXPR_LEN = 2000
MAX_SEQ = 100_000


class ExprError(Exception):
    pass


def _rmean(xs):
    xs = list(xs)
    if not xs:
        raise ExprError("mean() of an empty list")
    return sum(xs) / len(xs)


def _seq(a):
    if isinstance(a, (list, tuple, str)):
        return list(a)
    raise ExprError("expected a list")


def _minmax(f):
    def g(*a):
        if len(a) == 1:
            a = _seq(a[0])
            if not a:
                raise ExprError(f"{f.__name__}() of an empty list")
        return f(a)
    return g


def _array(n, fill=0):
    n = int(n)
    if not 0 <= n <= MAX_SEQ:
        raise ExprError(f"array size must be 0..{MAX_SEQ}")
    return [fill] * n


def _range(a, b=None, step=1):
    a, b = (0, a) if b is None else (a, b)
    r = range(int(a), int(b), int(step) or 1)
    if len(r) > MAX_SEQ:
        raise ExprError("range too long")
    return list(r)


def _round(x, nd=0):
    return round(x, int(nd)) if nd else round(x)


# name: (min args, max args, implementation or None for engine functions, help)
FUNCTIONS: dict[str, tuple] = {
    "abs": (1, 1, abs, "absolute value"),
    "min": (1, 99, _minmax(min), "smallest of the values or of a list"),
    "max": (1, 99, _minmax(max), "largest of the values or of a list"),
    "round": (1, 2, _round, "round(x, decimals)"),
    "floor": (1, 1, math.floor, ""), "ceil": (1, 1, math.ceil, ""), "sqrt": (1, 1, math.sqrt, ""),
    "exp": (1, 1, math.exp, ""), "log": (1, 2, math.log, "natural log, or log(x, base)"),
    "log10": (1, 1, math.log10, ""), "sin": (1, 1, math.sin, ""), "cos": (1, 1, math.cos, ""),
    "tan": (1, 1, math.tan, ""), "asin": (1, 1, math.asin, ""), "acos": (1, 1, math.acos, ""),
    "atan": (1, 1, math.atan, ""), "atan2": (2, 2, math.atan2, ""), "hypot": (2, 2, math.hypot, ""),
    "degrees": (1, 1, math.degrees, ""), "radians": (1, 1, math.radians, ""),
    "int": (1, 1, int, ""), "float": (1, 1, float, ""), "bool": (1, 1, bool, ""), "str": (1, 1, str, ""),
    "sign": (1, 1, lambda x: (x > 0) - (x < 0), "-1, 0 or 1"),
    "clamp": (3, 3, lambda x, lo, hi: max(lo, min(hi, x)), "clamp(x, low, high)"),
    "len": (1, 1, len, "length of a list or text"), "sum": (1, 1, lambda a: sum(_seq(a)), "sum of a list"),
    "mean": (1, 1, lambda a: _rmean(_seq(a)), "mean of a list"),
    "sorted": (1, 1, lambda a: sorted(_seq(a)), ""), "reversed": (1, 1, lambda a: _seq(a)[::-1], ""),
    "index": (2, 2, lambda a, v: _seq(a).index(v) if v in _seq(a) else -1, "position of a value (-1 if absent)"),
    "count": (2, 2, lambda a, v: _seq(a).count(v), "occurrences of a value in a list"),
    "array": (1, 2, _array, "array(n, fill=0): a list of n values"),
    "range": (1, 3, _range, "list of integers"),
    # random numbers (engine generator, seedable)
    "random": (0, 0, None, "uniform random number in [0, 1)"),
    "uniform": (2, 2, None, "uniform random number in [a, b]"),
    "randint": (2, 2, None, "random integer a..b (inclusive)"),
    "gauss": (2, 2, None, "normal random number (mean, sd)"),
    "choice": (1, 1, None, "random element of a list"),
    "shuffle": (1, 1, None, "shuffled copy of a list"),
    # live test state
    "time": (0, 0, None, "test time (s)"),
    "zone": (1, 1, None, "1 if the animal's centre is in the zone"),
    "head_zone": (1, 1, None, "1 if the head is in the zone"),
    "zone_time": (1, 1, None, "total time in the zone so far (s)"),
    "zone_entries": (1, 1, None, "entries into the zone so far"),
    "detected": (0, 0, None, "1 if the animal is detected"),
    "freezing": (0, 0, None, "1 while freezing"), "immobile": (0, 0, None, "1 while immobile"),
    "speed": (0, 0, None, "current speed"), "distance": (0, 0, None, "distance travelled"),
    "x": (0, 0, None, "x position"), "y": (0, 0, None, "y position"),
    "input": (1, 2, None, "input([device,] channel): current input value"),
    "output": (1, 2, None, "output([device,] channel): current output value"),
    "analog": (1, 2, None, "analog([device,] channel)"), "encoder": (1, 2, None, "encoder([device,] channel)"),
    "activations": (1, 2, None, "activations([device,] channel): times the input switched on"),
    "pellets": (0, 2, None, "pellets dispensed (all, or of a [device,] channel)"),
    "timer": (1, 1, None, "timer value (s)"), "switch": (1, 1, None, "virtual switch state"),
    "key": (1, 1, None, "1 while the key is held down"),
    "responses": (1, 1, None, "responses registered on a schedule"),
    "reinforcers": (1, 1, None, "reinforcers earned on a schedule"),
    "requirement": (1, 1, None, "current requirement of a schedule"),
}

_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod, ast.Pow: op.pow}
_CMP = {ast.Eq: op.eq, ast.NotEq: op.ne, ast.Lt: op.lt, ast.LtE: op.le, ast.Gt: op.gt, ast.GtE: op.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_UNARY = {ast.USub: op.neg, ast.UAdd: op.pos, ast.Not: op.not_}
_cache: dict = {}


def _check_node(n):
    if isinstance(n, ast.Constant):
        if not isinstance(n.value, (int, float, str, bool, type(None))):
            raise ExprError("unsupported constant")
    elif isinstance(n, ast.Name):
        if n.id.startswith("_"):
            raise ExprError(f"invalid name '{n.id}'")
    elif isinstance(n, ast.BinOp):
        if type(n.op) not in _BIN:
            raise ExprError("unsupported operator")
        _check_node(n.left)
        _check_node(n.right)
    elif isinstance(n, ast.UnaryOp):
        if type(n.op) not in _UNARY:
            raise ExprError("unsupported operator")
        _check_node(n.operand)
    elif isinstance(n, ast.BoolOp):
        for v in n.values:
            _check_node(v)
    elif isinstance(n, ast.Compare):
        if any(type(o) not in _CMP for o in n.ops):
            raise ExprError("unsupported comparison")
        _check_node(n.left)
        for c in n.comparators:
            _check_node(c)
    elif isinstance(n, ast.IfExp):
        for c in (n.test, n.body, n.orelse):
            _check_node(c)
    elif isinstance(n, ast.Call):
        if not isinstance(n.func, ast.Name):
            raise ExprError("only functions like max(a, b) can be called")
        if n.keywords or any(isinstance(a, ast.Starred) for a in n.args):
            raise ExprError("keyword or * arguments are not supported")
        if n.func.id not in FUNCTIONS:
            raise ExprError(f"unknown function '{n.func.id}'")
        lo, hi = FUNCTIONS[n.func.id][:2]
        if not lo <= len(n.args) <= hi:
            want = f"{lo}" if lo == hi else f"{lo}–{hi}"
            raise ExprError(f"{n.func.id}() takes {want} argument(s), got {len(n.args)}")
        for a in n.args:
            _check_node(a)
    elif isinstance(n, ast.Subscript):
        _check_node(n.value)
        _check_node(n.slice)
    elif isinstance(n, ast.Slice):
        for c in (n.lower, n.upper, n.step):
            if c is not None:
                _check_node(c)
    elif isinstance(n, (ast.List, ast.Tuple)):
        if len(n.elts) > 10000:
            raise ExprError("list too long")
        for c in n.elts:
            _check_node(c)
    else:
        raise ExprError(f"'{type(n).__name__}' is not allowed in expressions")


def compile_expr(src):
    """Parse and check an expression; returns an AST node (cached). Raises ExprError."""
    if isinstance(src, bool) or isinstance(src, (int, float)):
        return ast.Constant(src)
    if isinstance(src, list):
        return ast.Constant(None) if not src else ast.List([compile_expr(x) for x in src], ast.Load())
    s = "" if src is None else str(src).strip()
    hit = _cache.get(s)
    if hit is not None:
        if isinstance(hit, ExprError):
            raise hit
        return hit
    try:
        if not s:
            raise ExprError("empty expression")
        if len(s) > MAX_EXPR_LEN:
            raise ExprError("expression too long")
        try:
            tree = ast.parse(s, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"syntax error: {e.msg}") from None
        except (ValueError, RecursionError, MemoryError):
            raise ExprError("invalid expression") from None
        try:
            _check_node(tree.body)
        except RecursionError:
            raise ExprError("expression too deeply nested") from None
        node = tree.body
    except ExprError as e:
        if len(_cache) < 5000:
            _cache[s] = e
        raise
    if len(_cache) < 5000:
        _cache[s] = node
    return node


def expr_names(src) -> set[str]:
    """Variable names read by an expression (empty on error)."""
    try:
        node = compile_expr(src)
    except ExprError:
        return set()
    funcs = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and id(n) not in funcs}


def check_expr(src, names=None) -> list[str]:
    """Edit-time check of an expression: syntax, allowed constructs, functions, and (if given) variable names."""
    try:
        compile_expr(src)
    except ExprError as e:
        return [str(e)]
    if names is None:
        return []
    unknown = sorted(n for n in expr_names(src) if n not in names and n not in CONSTANTS)
    return [f"unknown variable '{n}'" for n in unknown]


def _limit(v):
    if isinstance(v, (str, list)) and len(v) > MAX_SEQ:
        raise ExprError("result too long")
    if isinstance(v, int) and not isinstance(v, bool) and v.bit_length() > 1024:
        raise ExprError("number too large")
    return v


class Evaluator:
    """Safe evaluation of a checked AST. lookup(name) -> value (raise ExprError if unknown);
    call(name, args) -> value for engine functions."""

    def __init__(self, lookup: Callable, call: Callable | None = None, rng: random.Random | None = None):
        self.lookup = lookup
        self.call_engine = call
        self.rng = rng or random.Random()

    def eval(self, src):
        return self._ev(compile_expr(src))

    def _ev(self, n):
        t = type(n)
        if t is ast.Constant:
            return n.value
        if t is ast.Name:
            if n.id in CONSTANTS:
                try:
                    return self.lookup(n.id)
                except ExprError:
                    return CONSTANTS[n.id]
            return self.lookup(n.id)
        if t is ast.BinOp:
            a, b = self._ev(n.left), self._ev(n.right)
            return _limit(self._binop(type(n.op), a, b))
        if t is ast.UnaryOp:
            try:
                return _UNARY[type(n.op)](self._ev(n.operand))
            except TypeError as e:
                raise ExprError(str(e)) from None
        if t is ast.BoolOp:
            if isinstance(n.op, ast.And):
                v = True
                for x in n.values:
                    v = self._ev(x)
                    if not v:
                        return v
                return v
            v = False
            for x in n.values:
                v = self._ev(x)
                if v:
                    return v
            return v
        if t is ast.Compare:
            left = self._ev(n.left)
            for o, c in zip(n.ops, n.comparators):
                right = self._ev(c)
                try:
                    if not _CMP[type(o)](left, right):
                        return False
                except TypeError as e:
                    raise ExprError(f"cannot compare: {e}") from None
                left = right
            return True
        if t is ast.IfExp:
            return self._ev(n.body) if self._ev(n.test) else self._ev(n.orelse)
        if t is ast.Call:
            name = n.func.id
            args = [self._ev(a) for a in n.args]
            return _limit(self._call(name, args))
        if t is ast.Subscript:
            v = self._ev(n.value)
            if not isinstance(v, (list, tuple, str)):
                raise ExprError("only lists and text can be indexed")
            if isinstance(n.slice, ast.Slice):
                s = n.slice
                lo, hi, st = (None if x is None else int(self._ev(x)) for x in (s.lower, s.upper, s.step))
                if st == 0:
                    raise ExprError("slice step cannot be zero")
                return v[lo:hi:st]
            i = self._ev(n.slice)
            if isinstance(i, float) and i.is_integer():
                i = int(i)
            if not isinstance(i, int):
                raise ExprError("index must be an integer")
            try:
                return v[i]
            except IndexError:
                raise ExprError(f"index {i} out of range (length {len(v)})") from None
        if t is ast.List or t is ast.Tuple:
            return [self._ev(x) for x in n.elts]
        raise ExprError(f"'{t.__name__}' is not allowed")

    def _binop(self, o, a, b):
        try:
            if o is ast.Pow:
                if isinstance(b, (int, float)) and abs(b) > 1000 and isinstance(a, (int, float)) and abs(a) > 1:
                    raise ExprError("exponent too large")
                if isinstance(a, int) and isinstance(b, int) and b > 0 and a.bit_length() * b > 4096:
                    raise ExprError("number too large")
            if o is ast.Mult:
                for s_, n_ in ((a, b), (b, a)):
                    if isinstance(s_, (str, list)) and isinstance(n_, int) and len(s_) * n_ > MAX_SEQ:
                        raise ExprError("result too long")
            if o is ast.Add and isinstance(a, list) and isinstance(b, list):
                return a + b
            r = _BIN[o](a, b)
            if isinstance(r, complex):
                raise ExprError("complex result")
            return r
        except ZeroDivisionError:
            raise ExprError("division by zero") from None
        except OverflowError:
            raise ExprError("numeric overflow") from None
        except TypeError as e:
            raise ExprError(f"invalid operands: {e}") from None

    def _call(self, name, args):
        lo, hi, fn, _h = FUNCTIONS[name]
        try:
            if fn is not None:
                return fn(*args)
            rng = self.rng
            if name == "random":
                return rng.random()
            if name == "uniform":
                return rng.uniform(float(args[0]), float(args[1]))
            if name == "randint":
                a, b = int(args[0]), int(args[1])
                if b < a:
                    raise ExprError("randint(a, b) needs a <= b")
                return rng.randint(a, b)
            if name == "gauss":
                return rng.gauss(float(args[0]), float(args[1]))
            if name == "choice":
                s = _seq(args[0])
                if not s:
                    raise ExprError("choice() of an empty list")
                return rng.choice(s)
            if name == "shuffle":
                s = _seq(args[0])
                rng.shuffle(s)
                return s
            if self.call_engine is None:
                raise ExprError(f"{name}() is only available during a test")
            return self.call_engine(name, args)
        except ExprError:
            raise
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as e:
            raise ExprError(f"{name}(): {e}") from None


_INTERP = re.compile(r"\{([^{}]+)\}")


def _fmt(v) -> str:
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def interpolate(text: str, evaluate: Callable) -> str:
    """Replace {expr} in text with the value of expr ({{ and }} give literal braces)."""
    s = str(text or "")
    if "{" not in s:
        return s
    s = s.replace("{{", "\x00").replace("}}", "\x01")
    s = _INTERP.sub(lambda m: _fmt(evaluate(m.group(1))), s)
    return s.replace("\x00", "{").replace("\x01", "}")


# ====================================================================== format helpers
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
    elif act in ("beep", "end_test"):
        body.append({"type": "do", "action": act})
    else:
        body.append({"type": "do", "action": act})
    when["body"] = body
    return {"name": describe(rule), "enabled": True, "statements": [when]}


def normalize_procedures(procs) -> list[dict]:
    """Convert old rules and fill defaults; returns a new list (the input is not modified)."""
    out = []
    for p in procs or []:
        if is_legacy_rule(p):
            out.append(convert_rule(p))
        elif isinstance(p, dict):
            q = copy.deepcopy(p)
            q.setdefault("name", f"Procedure {len(out) + 1}")
            q.setdefault("enabled", True)
            q.setdefault("statements", [])
            out.append(q)
        else:  # keep indices aligned with the input
            out.append({"name": f"Procedure {len(out) + 1}", "enabled": False, "statements": [], "invalid": True})
    return out


def new_procedure(name: str = "Procedure") -> dict:
    return {"name": name, "enabled": True, "statements": []}


def new_statement(type_: str) -> dict:
    """A statement of this type with sensible defaults (used by the editor)."""
    d: dict = {"type": type_}
    if type_ == "when":
        d.update(event="test_start", mode="ignore", body=[])
    elif type_ == "wait":
        d.update(mode="seconds", seconds=1)
    elif type_ == "if":
        d.update(cond="", body=[])
    elif type_ == "repeat":
        d.update(mode="count", count=3, body=[])
    elif type_ == "set":
        d.update(var="", value="0")
    elif type_ == "do":
        d.update(action="log", text="")
    elif type_ == "stop":
        d.update(what="handler")
    elif type_ == "comment":
        d.update(text="")
    elif type_ == "var":
        d.update(name="", value=0, keep=False, result=False)
    return d


def spec_defaults(spec: dict) -> dict:
    return {p["name"]: p["default"] for p in spec["params"] if p["default"] is not None}


def _short(v) -> str:
    s = _fmt(v) if not isinstance(v, str) else v
    return s if len(s) <= 40 else s[:37] + "…"


_NAME_TYPES = ("zone", "var", "timer", "area", "switch", "schedule", "name", "procedure", "sequence")


def _params_text(spec: dict, st: dict) -> str:
    bits = []
    for p in spec.get("params", []):
        typ = p["type"]
        v = st.get(p["name"], p["default"])
        if v is None or v == "" or typ in ("device", "audio"):
            continue
        if typ in ("input", "output"):
            dev = st.get("device")
            bits.append(f"{dev}/{v}" if dev else str(v))
        elif typ in _NAME_TYPES:
            bits.append(_short(v))
        elif typ in ("text", "file", "key", "spec"):
            bits.append(f"“{_short(v)}”")
        else:
            m = re.match(r"(.*?)\s*\((s|ms|Hz)\)$", p["label"])
            name, unit = (m.group(1), " " + m.group(2)) if m else (p["label"].split(" (")[0], "")
            bits.append(f"{name.lower()} {_short(v)}{unit}")
    return ", ".join(bits)


def describe_event(st: dict) -> str:
    spec = EVENT_SPECS.get(st.get("event", ""), None)
    if spec is None:
        return f"unknown event '{st.get('event')}'"
    p = _params_text(spec, st)
    return f"{spec['label'].lower()}" + (f" ({p})" if p else "")


def describe_statement(st: dict) -> str:
    """One line summary of a statement, for the editor tree."""
    t = st.get("type")
    if t == "when":
        extra = []
        if st.get("once"):
            extra.append("once")
        if st.get("mode", "ignore") != "ignore":
            extra.append(WHEN_MODES.get(st.get("mode"), st.get("mode", "")).lower())
        return f"When {describe_event(st)}" + (f"  [{', '.join(extra)}]" if extra else "")
    if t == "wait":
        mode = _wait_mode(st)
        to = f" (timeout {_short(st['timeout'])} s)" if st.get("timeout") not in (None, "", 0) else ""
        if mode == "until":
            return f"Wait until {_short(st.get('until', ''))}{to}"
        if mode == "event":
            return f"Wait for {describe_event(st)}{to}"
        return f"Wait {_short(st.get('seconds', 0))} s"
    if t == "if":
        return f"If {_short(st.get('cond', '')) or '?'}"
    if t == "repeat":
        mode = _repeat_mode(st)
        v = f" [{st['var']}]" if st.get("var") else ""
        if mode == "count":
            return f"Repeat {_short(st.get('count', 0))} times{v}"
        if mode == "while":
            return f"Repeat while {_short(st.get('while', ''))}{v}"
        return f"Repeat forever{v}"
    if t == "set":
        idx = f"[{st['index']}]" if st.get("index") not in (None, "") else ""
        return f"Set {st.get('var', '?')}{idx} = {_short(st.get('value', ''))}"
    if t == "do":
        spec = ACTION_SPECS.get(st.get("action", ""))
        if spec is None:
            return f"Do: unknown action '{st.get('action')}'"
        p = _params_text(spec, st)
        return f"Do: {spec['label']}" + (f" — {p}" if p else "")
    if t == "stop":
        return STOP_WHAT.get(st.get("what", "handler"), "Stop")
    if t == "comment":
        return f"# {st.get('text', '')}"
    if t == "var":
        flags = [f for f, k in (("kept between tests", "keep"), ("saved as result", "result")) if st.get(k)]
        return f"Variable {st.get('name', '?')} = {_short(st.get('value', 0))}" + (f"  ({', '.join(flags)})"
                                                                                   if flags else "")
    return f"Unknown statement '{t}'"


def describe(rule: dict) -> str:
    """Summary of a legacy rule or of a procedure."""
    if not is_legacy_rule(rule):
        if isinstance(rule, dict) and "statements" in rule:
            n = len(rule.get("statements") or [])
            return f"{rule.get('name', 'Procedure')} ({n} statement{'s' if n != 1 else ''})"
        return describe_statement(rule) if isinstance(rule, dict) else str(rule)
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


def _wait_mode(st):
    m = st.get("mode")
    if m in ("seconds", "until", "event"):
        return m
    if st.get("event"):
        return "event"
    if st.get("until") not in (None, ""):
        return "until"
    return "seconds"


def _repeat_mode(st):
    m = st.get("mode")
    if m in ("count", "while", "forever"):
        return m
    if st.get("while") not in (None, ""):
        return "while"
    if st.get("count") not in (None, ""):
        return "count"
    return "forever"


def iter_statements(stmts, path=()):
    """Yield (path, statement) for a statement list and all nested blocks."""
    for i, st in enumerate(stmts or []):
        if not isinstance(st, dict):
            continue
        p = path + (i,)
        yield p, st
        for br in ("body", "else"):
            if isinstance(st.get(br), list):
                yield from iter_statements(st[br], p + (br,))


def statement_at(proc: dict, path: tuple) -> dict | None:
    cur = proc.get("statements", [])
    st = None
    for k in path:
        if isinstance(k, int):
            if not isinstance(cur, list) or not 0 <= k < len(cur):
                return None
            st = cur[k]
        else:
            cur = st.get(k) if isinstance(st, dict) else None
    return st


def path_text(path: tuple) -> str:
    """(0, 'body', 2, 'else', 0) -> '1.3.else.1' (1-based for people)."""
    return ".".join(str(k + 1) if isinstance(k, int) else k for k in path)


# ====================================================================== validation
def _names(lst) -> list[str]:
    out = []
    for z in lst or []:
        n = z if isinstance(z, str) else (z.get("name") if isinstance(z, dict) else getattr(z, "name", None))
        if n:
            out.append(str(n))
    return out


def _context(context) -> dict:
    c = dict(context or {})
    devs = c.get("devices")
    channels: dict[str, dict[str, str]] = {}
    if devs and isinstance(devs[0], dict):
        for d in devs:
            channels[d.get("name", "")] = {ch.get("name"): ch.get("kind", "input") for ch in d.get("channels", []) or []
                                           if ch.get("name")}
    return {"zones": set(_names(c.get("zones"))) if c.get("zones") is not None else None,
            "devices": set(_names(devs)) if devs is not None else None,
            "channels": channels or None,
            "areas": set(_names(c.get("areas"))) if c.get("areas") else None}


def declared_names(procedures) -> set[str]:
    """Variables declared or assigned anywhere (they are global), plus loop and event locals."""
    names = set(LOCAL_NAMES)
    for p in normalize_procedures(procedures):
        for _path, st in iter_statements(p.get("statements")):
            t = st.get("type")
            if t == "var" and st.get("name"):
                names.add(str(st["name"]))
            elif t == "set" and st.get("var"):
                names.add(str(st["var"]))
            elif t == "repeat" and st.get("var"):
                names.add(str(st["var"]))
            elif t == "do" and st.get("action") in ("set_variable", "increment", "decrement", "array_append",
                                                     "schedule_response") and st.get("var"):
                names.add(str(st["var"]))
    return names


_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _bad_var_name(n) -> str | None:
    n = str(n or "")
    if not n:
        return "a variable name is required"
    if not _IDENT.match(n) or keyword.iskeyword(n):
        return f"'{n}' is not a valid name (letters, digits and _ ; start with a letter)"
    if n in CONSTANTS:
        return f"'{n}' is a reserved name"
    return None


def _check_param(p, v, names, ctx, st) -> list[str]:
    typ, label = p["type"], p["label"]
    empty = v is None or (isinstance(v, str) and not v.strip())
    if empty:
        return [f"{label} is required"] if p.get("req") else []
    errs = []
    if typ in ("number", "int"):
        errs = check_expr(v, names)
        if not errs and isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0 \
                and p["name"] not in ("value", "by", "threshold"):
            errs = ["must not be negative"]
    elif typ == "expr":
        errs = check_expr(v, names)
    elif typ == "text":
        for m in _INTERP.finditer(str(v).replace("{{", "").replace("}}", "")):
            errs += check_expr(m.group(1), names)
    elif typ == "zone":
        if ctx["zones"] is not None and str(v) not in ctx["zones"]:
            errs = [f"unknown zone '{v}'"]
    elif typ == "sequence":
        zs = [z.strip() for z in str(v).split(",") if z.strip()]
        if len(zs) < 2:
            errs = ["give at least two zones separated by commas"]
        elif ctx["zones"] is not None:
            errs = [f"unknown zone '{z}'" for z in zs if z not in ctx["zones"]]
    elif typ in ("device", "audio"):
        if ctx["devices"] is not None and str(v) not in ctx["devices"]:
            errs = [f"unknown device '{v}'"]
    elif typ in ("input", "output"):
        chans = ctx["channels"]
        if chans is not None:
            dev = st.get("device") or ""
            pool = chans.get(dev, {}) if dev else {k: kd for d in chans.values() for k, kd in d.items()}
            if dev and dev not in chans:
                pool = None
            if pool is not None and str(v) not in pool:
                errs = [f"unknown {'input' if typ == 'input' else 'output'} channel '{v}'"]
            elif pool is not None:
                kd = pool[str(v)]
                want = ("input", "analog", "encoder") if typ == "input" else ("output", "pwm")
                if kd not in want:
                    errs = [f"'{v}' is an {kd} channel, not an {'input' if typ == 'input' else 'output'}"]
    elif typ == "var":
        e = _bad_var_name(v)
        errs = [e] if e else []
    elif typ == "spec":
        try:
            parse_spec(v)
        except ValueError as e:
            errs = [str(e)]
    elif typ == "area":
        if ctx["areas"] is not None and str(v) not in ctx["areas"]:
            errs = [f"unknown touch-screen area '{v}'"]
    elif typ.startswith("choice:"):
        if str(v) not in typ[7:].split("|"):
            errs = [f"must be one of {typ[7:].replace('|', ', ')}"]
    return [f"{label}: {e}" for e in errs]


def validate(procedures, context=None) -> list[tuple[int, tuple, str]]:
    """Edit-time check. Returns [(procedure index, statement path, message)]; path () = the procedure itself.

    context: {"zones": [...], "devices": [names or io_devices configs], "areas": [...]} — optional; names are
    only checked against the lists that are given."""
    procs = normalize_procedures(procedures)
    ctx = _context(context)
    names = declared_names(procs)
    issues: list[tuple[int, tuple, str]] = []
    seen_vars: dict[str, int] = {}
    proc_names = [p.get("name") for p in procs]

    def block(pi, stmts, path, top, in_loop):
        if not isinstance(stmts, list):
            issues.append((pi, path, "statements must be a list"))
            return
        for i, st in enumerate(stmts):
            p = path + (i,)
            if not isinstance(st, dict):
                issues.append((pi, p, "invalid statement"))
                continue
            t = st.get("type")
            lab = STATEMENT_TYPES.get(t, str(t))

            def err(msg, p=p, lab=lab):
                issues.append((pi, p, f"{lab}: {msg}"))

            if t not in STATEMENT_TYPES:
                err(f"unknown statement type '{t}'")
                continue
            if t in ("when", "var") and not top:
                err("only allowed at the top level of a procedure")
            if t == "when":
                ev = st.get("event")
                spec = EVENT_SPECS.get(ev)
                if spec is None:
                    err(f"unknown event '{ev}'")
                else:
                    for prm in spec["params"]:
                        for m in _check_param(prm, st.get(prm["name"], prm["default"]), names, ctx, st):
                            err(m)
                if st.get("mode", "ignore") not in WHEN_MODES:
                    err(f"unknown mode '{st.get('mode')}'")
                block(pi, st.get("body", []), p + ("body",), False, False)
            elif t == "var":
                e = _bad_var_name(st.get("name"))
                if e:
                    err(e)
                else:
                    n = st["name"]
                    if n in seen_vars:
                        err(f"'{n}' is declared more than once")
                    seen_vars[n] = pi
                for m in check_expr(st.get("value", 0), names):
                    err(f"initial value: {m}")
            elif t == "wait":
                mode = _wait_mode(st)
                if mode == "seconds":
                    for m in _check_param(P("seconds", "number", None, "Seconds", True), st.get("seconds"), names, ctx,
                                          st):
                        err(m)
                elif mode == "until":
                    for m in _check_param(P("until", "expr", None, "Condition", True), st.get("until"), names, ctx,
                                          st):
                        err(m)
                else:
                    ev = st.get("event")
                    spec = EVENT_SPECS.get(ev)
                    if spec is None:
                        err(f"unknown event '{ev}'")
                    elif ev == "test_start":
                        err("cannot wait for the test to start")
                    else:
                        for prm in spec["params"]:
                            for m in _check_param(prm, st.get(prm["name"], prm["default"]), names, ctx, st):
                                err(m)
                if st.get("timeout") not in (None, "", 0):
                    for m in check_expr(st["timeout"], names):
                        err(f"timeout: {m}")
            elif t == "if":
                for m in _check_param(P("cond", "expr", None, "Condition", True), st.get("cond"), names, ctx, st):
                    err(m)
                block(pi, st.get("body", []), p + ("body",), False, in_loop)
                if "else" in st:
                    block(pi, st.get("else") or [], p + ("else",), False, in_loop)
            elif t == "repeat":
                mode = _repeat_mode(st)
                if mode == "count":
                    for m in _check_param(P("count", "int", None, "Count", True), st.get("count"), names, ctx, st):
                        err(m)
                elif mode == "while":
                    for m in _check_param(P("while", "expr", None, "Condition", True), st.get("while"), names, ctx,
                                          st):
                        err(m)
                if st.get("var"):
                    e = _bad_var_name(st["var"])
                    if e:
                        err(e)
                block(pi, st.get("body", []), p + ("body",), False, True)
            elif t == "set":
                e = _bad_var_name(st.get("var"))
                if e:
                    err(e)
                for m in _check_param(P("value", "expr", None, "Value", True), st.get("value"), names, ctx, st):
                    err(m)
                if st.get("index") not in (None, ""):
                    for m in check_expr(st["index"], names):
                        err(f"index: {m}")
            elif t == "do":
                a = st.get("action")
                spec = ACTION_SPECS.get(a)
                if spec is None:
                    err(f"unknown action '{a}'")
                else:
                    lab2 = f"Do {spec['label'].lower()}"
                    for prm in spec["params"]:
                        for m in _check_param(prm, st.get(prm["name"], prm["default"]), names, ctx, st):
                            issues.append((pi, p, f"{lab2}: {m}"))
                    if a in ("enable_procedure", "disable_procedure") and st.get("procedure") \
                            and st["procedure"] not in proc_names:
                        issues.append((pi, p, f"{lab2}: unknown procedure '{st['procedure']}'"))
                    if a == "schedule_response" and not st.get("spec"):
                        started = any(s2.get("action") == "schedule_start" and s2.get("schedule") == st.get("schedule")
                                      for q in procs for _pp, s2 in iter_statements(q.get("statements")))
                        if not started:
                            issues.append((pi, p, f"{lab2}: give a schedule (e.g. FR 5) or start it first"))
            elif t == "stop":
                w = st.get("what", "handler")
                if w not in STOP_WHAT:
                    err(f"unknown option '{w}'")
                elif w == "loop" and not in_loop:
                    err("“exit the loop” is not inside a repeat")

    for pi, proc in enumerate(procs):
        if not str(proc.get("name", "")).strip():
            issues.append((pi, (), "the procedure has no name"))
        elif proc_names.count(proc.get("name")) > 1:
            issues.append((pi, (), f"another procedure is also called '{proc.get('name')}'"))
        block(pi, proc.get("statements"), (), True, False)
    return issues


# ====================================================================== engine
class _Stop(Exception):
    pass


class _Break(Exception):
    pass


class _Detector:
    """Watches for one event (with its parameters) for a handler or a waiting thread."""

    def __init__(self, eng: "ProcedureEngine", st: dict, th=None, path=(), initial: bool = False):
        self.event = st.get("event", "")
        self.spec = EVENT_SPECS.get(self.event, {"params": [], "generic": True})
        self.generic = self.spec.get("generic", True)
        self.args: dict = {}
        self.hits: list = []
        for p in self.spec["params"]:
            v = st.get(p["name"], p["default"])
            if p["type"] in ("number", "int"):
                v = None if v in (None, "") else eng._num(th, v, path, what=p["label"])
            elif p["type"] != "expr":
                v = "" if v is None else str(v).strip()
            if p["type"] == "device" and v:
                v = eng._devname(v)
            self.args[p["name"]] = v
        self.cond = st.get("cond")
        self.th, self.path = th, path
        self.done = False
        self.fired_n = 0
        self.prev = None
        self.seq: list[str] = []
        a, e = self.args, self.event
        if e == "every":
            iv = a.get("interval") or 0
            self.next_t = a["first"] if a.get("first") not in (None, "") else iv
            if iv <= 0:
                eng._error(th, path, "Every: the interval must be positive")
                self.done = True
        elif initial:  # a handler created at the start: a condition already true at the start counts
            self.prev = None
        elif e in ("speed_above", "speed_below"):
            self.prev = eng._speed
        elif e in ("analog_above", "analog_below"):
            self.prev = eng._input_value(a.get("device"), a.get("channel"))
        elif e in ("condition_true", "condition_false"):
            self.prev = eng._truth(th, self.cond, path, quiet=True)
        if e == "encoder_every":
            self.prev = eng._input_value(a.get("device"), a.get("channel")) or 0

    def matches(self, name: str, args: dict) -> bool:
        if name != self.event:
            return False
        for k, want in self.args.items():
            if want in (None, ""):
                continue
            got = args.get(k)
            if k == "key":
                if str(got or "").lower() != str(want).lower():
                    return False
            elif str(got) != str(want):
                return False
        return True

    def poll(self, eng: "ProcedureEngine", t: float) -> list:
        """Stateful detection, once per frame: [(t_occurrence, args)]."""
        if self.done:
            return []
        a, e = self.args, self.event
        out = []
        if e == "time_reached":
            if t + EPS >= (a.get("time") or 0):
                out.append((a.get("time") or 0, {}))
                self.done = True
        elif e == "every":
            iv = a["interval"]
            n = 0
            while self.next_t <= t + EPS and n < 100:
                out.append((self.next_t, {}))
                self.next_t += iv
                n += 1
        elif e == "zone_time_reaches":
            if eng.zone_time.get(a["zone"], 0.0) + EPS >= a["seconds"]:
                out.append((t, {"zone": a["zone"]}))
                self.done = True
        elif e == "zone_entries_reach":
            if eng.zone_entries.get(a["zone"], 0) >= a["count"]:
                out.append((t, {"zone": a["zone"]}))
                self.done = True
        elif e == "zone_dwell":
            since = eng.zone_since.get(a["zone"])
            if since is None:
                self.prev = None
            elif self.prev != since and t - since + EPS >= a["seconds"]:
                self.prev = since
                out.append((since + a["seconds"], {"zone": a["zone"]}))
        elif e == "zone_sequence":
            want = [z.strip() for z in str(a.get("sequence", "")).split(",") if z.strip()]
            for z in eng._entered_now:
                if z in want and (not self.seq or self.seq[-1] != z):
                    self.seq.append(z)
                    self.seq = self.seq[-len(want):]
                    if self.seq == want:
                        out.append((t, {"sequence": a.get("sequence")}))
        elif e in ("speed_above", "speed_below"):
            v = eng._speed
            if v is not None and math.isfinite(v):
                thr = a["threshold"]
                above = v > thr if e == "speed_above" else v < thr
                was = None if self.prev is None else (self.prev > thr if e == "speed_above" else self.prev < thr)
                if above and not was:
                    out.append((t, {"value": v}))
                self.prev = v
        elif e == "distance_reaches":
            if eng._distance + EPS >= a["distance"]:
                out.append((t, {"value": eng._distance}))
                self.done = True
        elif e in ("freezing_time_reaches", "immobile_time_reaches"):
            v = eng.freeze_time if e.startswith("freezing") else eng.immobile_time
            if v + EPS >= a["seconds"]:
                out.append((t, {"value": v}))
                self.done = True
        elif e == "input_count_reaches":
            if eng._input_count(a.get("device"), a.get("channel")) >= a["count"]:
                out.append((t, {"channel": a.get("channel")}))
                self.done = True
        elif e in ("analog_above", "analog_below"):
            v = eng._input_value(a.get("device"), a.get("channel"))
            if v is not None:
                thr = a["threshold"]
                cur = v > thr if e == "analog_above" else v < thr
                was = None if self.prev is None else (self.prev > thr if e == "analog_above" else self.prev < thr)
                if cur and not was:
                    out.append((t, {"value": v}))
                self.prev = v
        elif e == "encoder_reaches":
            v = eng._input_value(a.get("device"), a.get("channel")) or 0
            if abs(v) >= a["count"]:
                out.append((t, {"value": v}))
                self.done = True
        elif e == "encoder_every":
            v = eng._input_value(a.get("device"), a.get("channel")) or 0
            step = max(1, int(a["count"] or 1))
            while abs(v - self.prev) >= step:
                self.prev += step if v > self.prev else -step
                out.append((t, {"value": v}))
        elif e in ("condition_true", "condition_false"):
            v = eng._truth(self.th, self.cond, self.path)
            want = v if e == "condition_true" else not v
            was = None if self.prev is None else (self.prev if e == "condition_true" else not self.prev)
            if want and not was:
                out.append((t, {"value": int(v)}))
            self.prev = v
        return out


_CONDITION_EVENTS = ("condition_true", "condition_false")


class _Handler:
    def __init__(self, proc_i, path, st, det, label):
        self.proc_i, self.path, self.st, self.det, self.label = proc_i, path, st, det, label
        self.mode = st.get("mode", "ignore")
        self.once = bool(st.get("once"))
        self.threads: list = []
        self.enabled = True
        self.count = 0


class _Thread:
    def __init__(self, eng, proc_i, handler, clock, locals_, label):
        self.proc_i, self.handler, self.clock, self.locals, self.label = proc_i, handler, clock, locals_, label
        self.alive = True
        self.wait = None
        self.steps = 0
        self.waits = 0
        self.gen = None
        self.path = ()
        self.resume_clock = clock


def _locked(fn):
    @wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class ProcedureEngine:
    """Runs procedures during a live test. Call start(t), update_state(t, state) every frame, stop(t).

    The public methods are thread-safe (e.g. frames processed in a worker thread, keys and touches from the GUI);
    callbacks run in the thread that called the engine.

    Pausing (:meth:`pause`) runs the "test paused" handlers at once, stops pulse trains, switches shock outputs
    off and, with ``outputs_off_on_pause`` (default), every output and sound. While paused call
    :meth:`paused_tick` with the real time since the pause so that safety tasks still run.
    Variables declared "keep" are copied to ``variables`` at :meth:`stop` unless ``commit_kept`` is False; they
    are always available in ``kept_variables`` (e.g. to be stored only when the test is saved)."""

    def __init__(self, procedures=None, devices=None, on_mark=None, on_end=None, on_log=None, variables=None,
                 context=None, *, on_pause=None, on_resume=None, on_stimulus=None, seed=None,
                 outputs_off_at_end: bool = True, outputs_off_on_pause: bool = True, commit_kept: bool = True):
        from .iodevices import DeviceManager

        if isinstance(devices, Outputs):
            self.outputs, devices = devices, None
        else:
            self.outputs = Outputs()
        self.devices = devices if devices is not None else DeviceManager([], open=False)
        self.rules = procedures or []
        self.procedures = normalize_procedures(self.rules)
        self.on_mark, self.on_end, self.on_log = on_mark, on_end, on_log
        self.on_pause, self.on_resume, self.on_stimulus = on_pause, on_resume, on_stimulus
        self.variables = variables if variables is not None else {}
        self.context = context or {}
        self.rng = random.Random(seed)
        self.outputs_off_at_end = outputs_off_at_end
        self.outputs_off_on_pause = outputs_off_on_pause
        self.commit_kept = commit_kept
        self._lock = threading.RLock()
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self):
        self.started = self.stopped = self.ended = self.paused = False
        self.started_ok = False
        self.t = 0.0
        self.vars: dict = {}
        self.var_flags: dict[str, dict] = {}
        self.errors: list[str] = []
        self._error_keys: set = set()
        self.io_events: list[dict] = []
        self.result_variables: dict = {}
        self.kept_variables: dict = {}
        self.marks: list[dict] = []
        self.state_events: list[dict] = []
        self.pauses: list[list] = []
        self.log_lines: list[tuple[float, str]] = []
        self.fired: list = []
        self.handlers: list[_Handler] = []
        self.threads: list[_Thread] = []
        self.proc_enabled = [bool(p.get("enabled", True)) for p in self.procedures]
        self._queue: deque = deque()
        self._tasks: list = []
        self._task_seq = 0
        self._task_keys: dict = {}
        self._cancelled: set = set()
        self._trains: dict = {}
        self._update_no = 0
        self._busy = False
        self._pending_stop = None
        self._have_t = False
        # live state
        self._zones: dict[str, bool] = {}
        self._head: dict[str, bool] = {}
        self.zone_time: dict[str, float] = {}
        self.zone_entries: dict[str, int] = {}
        self.zone_since: dict[str, float] = {}
        self._entered_now: list[str] = []
        self._detected = True
        self._freezing = self._immobile = False
        self.freeze_time = self.immobile_time = 0.0
        self._x = self._y = math.nan
        self._speed = 0.0
        self._distance = 0.0
        self._last_xy = None
        self.inputs: dict[tuple, float] = {}
        self.input_counts: dict[tuple, int] = {}
        self.outputs_state: dict[tuple, float] = {}
        self.pellet_counts: dict[tuple, int] = {}
        self.keys_down: set[str] = set()
        self.switches: dict[str, int] = {}
        self.timers: dict[str, dict] = {}
        self.schedules: dict[str, Schedule] = {}
        self.stimuli: dict[str, dict] = {}
        self._audio_on: dict[tuple, float] = {}
        self._io_last: dict[tuple, tuple] = {}
        self._shock_keys: set[tuple] = set()
        self._pause_t: float | None = None

    # ------------------------------------------------------------------ public API
    @_locked
    def start(self, t: float = 0.0):
        if self.started:
            return
        self._reset()
        self.started = True
        self.t = t
        self._have_t = True
        self._busy = True
        try:
            for pi, proc in enumerate(self.procedures):
                if not self.proc_enabled[pi]:
                    continue
                for i, st in enumerate(proc.get("statements") or []):
                    if isinstance(st, dict) and st.get("type") == "var" and st.get("enabled", True) is not False:
                        self._declare(pi, (i,), st)
            self.started_ok = True
            for pi, proc in enumerate(self.procedures):
                if not self.proc_enabled[pi]:
                    continue
                stmts = proc.get("statements") or []
                for i, st in enumerate(stmts):
                    if isinstance(st, dict) and st.get("type") == "when" and st.get("enabled", True) is not False:
                        th = _Thread(self, pi, None, t, {}, "when")
                        det = _Detector(self, st, th, (i,), initial=True)
                        self.handlers.append(_Handler(pi, (i,), st, det, st.get("event", "?")))
            for pi, proc in enumerate(self.procedures):
                if not self.proc_enabled[pi]:
                    continue
                stmts = proc.get("statements") or []
                if any(isinstance(s, dict) and s.get("type") not in ("when", "var", "comment")
                       and s.get("enabled", True) is not False for s in stmts):
                    th = _Thread(self, pi, None, t, {}, "start")
                    th.gen = self._thread_main(th, stmts, ())
                    self.threads.append(th)
                    self._resume(th)
            self._queue.append(("event", "test_start", {}, t, None))
            self._run(t)
        finally:
            self._busy = False
        self._after()

    @_locked
    def update_state(self, t: float, state: dict | None = None):
        """Call once per frame. state: {"zones": {name: bool}, "head_zones": {...}, "detected", "freezing",
        "immobile", "x", "y", "speed", "distance"} (all optional)."""
        if not self.started:
            self.start(t)
        if self.stopped:
            return
        self._busy = True
        try:
            dt = max(0.0, t - self.t) if self._have_t else 0.0
            self.t = max(self.t, t)
            self._have_t = True
            self._update_no += 1
            self._observe(t, dt, state or {})
            self._poll_inputs(t)
            self._tick(t)
            self._poll_detectors(t)
            self._run(t)
        finally:
            self._busy = False
        self._after()

    def update(self, t: float, zones: dict, detected: bool = True, freezing: bool = False, immobile: bool = False):
        """Legacy API (rules engine): zone occupancy name -> bool."""
        self.update_state(t, {"zones": zones, "detected": detected, "freezing": freezing, "immobile": immobile})

    @_locked
    def key(self, t: float, key: str, down: bool = True):
        k = str(key)
        if down:
            if k.lower() in self.keys_down:
                return
            self.keys_down.add(k.lower())
        else:
            self.keys_down.discard(k.lower())
        self._external(t, "key_down" if down else "key_up", {"key": k})

    @_locked
    def touch(self, t: float, area: str | None, x: float | None = None, y: float | None = None):
        """A touch on the stimulus screen inside `area` (None/"" = outside all areas)."""
        if area:
            self._log_io(t, "screen", f"touch {area}", "input", 1, "digital")
            self._log_io(t, "screen", f"touch {area}", "input", 0, "digital")
            self._external(t, "touch", {"area": str(area), "value": (x, y)})
        else:
            self._external(t, "touch_outside", {"value": (x, y)})

    @_locked
    def mark_event(self, t: float, name: str):
        """A manually scored event (fires "event marked")."""
        self._external(t, "event_marked", {"name": str(name)})

    @_locked
    def set_input(self, device: str, channel: str, value: float):
        """Simulate an input (virtual devices); picked up on the next frame."""
        self.devices.set_input(device, channel, value)

    @_locked
    def pause(self, t: float):
        """Pause the test now: safety outputs off and the "test paused" handlers run at once."""
        self._pause(t)

    @_locked
    def resume(self, t: float):
        self._resume_test(t)

    @_locked
    def paused_tick(self, wall_s: float):
        """While paused (the test clock is stopped): run the safety tasks (shock cut-offs, end of sounds) that are
        due `wall_s` seconds of real time after the pause, logged at the pause time, and read the inputs (changes
        during a pause update the input states without firing events). Call it regularly while paused."""
        if not self.paused or not self.started or self.stopped or self._busy:
            return
        t = self._pause_t if self._pause_t is not None else self.t
        self._busy = True
        try:
            self._poll_inputs(t, quiet=True)
            limit = t + max(0.0, float(wall_s))
            due = [x for x in self._tasks if x[3] is not None and isinstance(x[3], tuple) and x[3]
                   and x[3][0] in SAFETY_TASKS and x[0] <= limit + EPS]
            if due:
                self._tasks = [x for x in self._tasks if x not in due]
                heapq.heapify(self._tasks)
                for _due, seq, fn, key in sorted(due, key=lambda x: (x[0], x[1])):
                    if seq in self._cancelled:
                        self._cancelled.discard(seq)
                        continue
                    if self._task_keys.get(key) == seq:
                        del self._task_keys[key]
                    fn(t)
        finally:
            self._busy = False

    @_locked
    def stop(self, t: float):
        if self.stopped or not self.started:
            if not self.started:
                self.stopped = True
            return
        if self._busy:
            self._pending_stop = t if self._pending_stop is None else max(self._pending_stop, t)
            return
        self._busy = True
        try:
            self.t = max(self.t, t)
            t = self.t
            self._tick(t)
            self._queue.append(("event", "test_end", {}, t, None))
            self._run(t, final=True)
            for th in self.threads:
                self._kill(th)
            self.threads = []
            for key in list(self._trains):
                self._stop_train(key, t)
            self._tasks, self._task_keys, self._cancelled = [], {}, set()
            if self.outputs_off_at_end:
                for (dev, ch), v in list(self.outputs_state.items()):
                    if v:
                        self._set_out(dev, ch, 0, t, "digital")
                for key in list(self._audio_on):
                    self._audio_off(key, t)
                for name, v in list(self.switches.items()):
                    if v:
                        self._set_switch(name, 0, t)
            for m in self.state_events:
                if m["t_end"] is None:
                    m["t_end"] = t
            for pz in self.pauses:
                if pz[1] is None:
                    pz[1] = t
            for name, flags in self.var_flags.items():
                v = self.vars.get(name)
                if flags.get("keep"):
                    self.kept_variables[name] = copy.deepcopy(v)
                    if self.commit_kept:
                        self.variables[name] = copy.deepcopy(v)
                if flags.get("result") and isinstance(v, (int, float)) and not isinstance(v, str):
                    self.result_variables[name] = float(v) if isinstance(v, float) else int(v)
            self.io_events.sort(key=lambda e: e["t"])
        finally:
            self._busy = False
            self.stopped = True

    # ------------------------------------------------------------------ internals: errors & helpers
    def _proc_name(self, pi):
        if pi is None or not 0 <= pi < len(self.procedures):
            return "?"
        return self.procedures[pi].get("name", f"Procedure {pi + 1}")

    def _error(self, th, path, msg):
        pi = th.proc_i if th is not None else None
        key = (pi, tuple(path or ()), msg)
        if key in self._error_keys:
            return
        self._error_keys.add(key)
        where = f"'{self._proc_name(pi)}'" + (f", statement {path_text(path)}" if path else "")
        if pi is None and not path:
            where = "I/O" if msg.startswith(("I/O", "output")) or ":" in msg else "procedures"
        text = f"{self.t:.2f} s — {where}: {msg}"
        self.errors.append(text)
        if self.on_log:
            try:
                self.on_log(f"Error: {where}: {msg}", self.t)
            except Exception:  # pragma: no cover
                pass

    def _lookup(self, th):
        def look(name):
            if th is not None and name in th.locals:
                return th.locals[name]
            if name in self.vars:
                return self.vars[name]
            if name in CONSTANTS:
                return CONSTANTS[name]
            raise ExprError(f"unknown variable '{name}'")
        return look

    def _evaluator(self, th):
        return Evaluator(self._lookup(th), self._call, self.rng)

    def _eval(self, th, src, path, what=""):
        return self._evaluator(th).eval(src)

    def _num(self, th, src, path, what="", default=0.0):
        if src is None or (isinstance(src, str) and not src.strip()):
            return default
        if isinstance(src, (int, float)) and not isinstance(src, bool):
            return src
        try:
            v = self._eval(th, src, path)
            if isinstance(v, bool):
                v = int(v)
            if not isinstance(v, (int, float)):
                raise ExprError(f"expected a number, got {_short(v)!r}")
            return v
        except ExprError as e:
            self._error(th, path, f"{what + ': ' if what else ''}{e}")
            return default

    def _truth(self, th, src, path, quiet=False):
        try:
            return bool(self._eval(th, src, path))
        except ExprError as e:
            if not quiet:
                self._error(th, path, f"condition: {e}")
            return False

    def _text(self, th, src, path):
        try:
            return interpolate(src, lambda e: self._eval(th, e, path))
        except ExprError as e:
            self._error(th, path, f"text: {e}")
            return str(src)

    # ------------------------------------------------------------------ expression functions
    def _resolve_in(self, args):
        if len(args) == 2:
            return str(args[0] or ""), str(args[1])
        return "", str(args[0])

    def _input_key(self, dev, ch):
        if dev:
            return (self._devname(dev), ch)
        for k in self.inputs:
            if k[1] == ch:
                return k
        d = self.devices.find_channel(ch)
        return (d or "", ch)

    def _input_value(self, dev, ch):
        if not ch:
            return None
        return self.inputs.get(self._input_key(dev or "", ch))

    def _input_count(self, dev, ch):
        return self.input_counts.get(self._input_key(dev or "", ch or ""), 0)

    def _call(self, name, args):
        if name == "time":
            return self.t
        if name == "zone":
            return int(bool(self._zones.get(str(args[0]))))
        if name == "head_zone":
            return int(bool(self._head.get(str(args[0]))))
        if name == "zone_time":
            return self.zone_time.get(str(args[0]), 0.0)
        if name == "zone_entries":
            return self.zone_entries.get(str(args[0]), 0)
        if name == "detected":
            return int(self._detected)
        if name == "freezing":
            return int(self._freezing)
        if name == "immobile":
            return int(self._immobile)
        if name == "speed":
            return self._speed
        if name == "distance":
            return self._distance
        if name == "x":
            return self._x
        if name == "y":
            return self._y
        if name in ("input", "analog", "encoder"):
            v = self._input_value(*self._resolve_in(args))
            return 0 if v is None else v
        if name == "activations":
            return self._input_count(*self._resolve_in(args))
        if name == "output":
            dev, ch = self._resolve_in(args)
            if dev:
                return self.outputs_state.get((dev, ch), 0)
            return next((v for (d, c), v in self.outputs_state.items() if c == ch), 0)
        if name == "pellets":
            if not args:
                return sum(self.pellet_counts.values())
            dev, ch = self._resolve_in(args)
            return sum(v for (d, c), v in self.pellet_counts.items() if c == ch and (not dev or d == dev))
        if name == "timer":
            return self._timer_value(str(args[0]))
        if name == "switch":
            return self.switches.get(str(args[0]), 0)
        if name == "key":
            return int(str(args[0]).lower() in self.keys_down)
        if name in ("responses", "reinforcers", "requirement"):
            s = self.schedules.get(str(args[0]))
            if s is None:
                return 0
            v = getattr(s, name)
            return v if math.isfinite(v) else -1
        raise ExprError(f"unknown function '{name}'")

    # ------------------------------------------------------------------ variables
    def _declare(self, pi, path, st):
        name = str(st.get("name") or "")
        if _bad_var_name(name):
            self._error(_Thread(self, pi, None, self.t, {}, ""), path, f"Variable: {_bad_var_name(name)}")
            return
        if name in self.var_flags:
            return
        self.var_flags[name] = {"keep": bool(st.get("keep")), "result": bool(st.get("result"))}
        if st.get("keep") and name in self.variables:
            self.vars[name] = copy.deepcopy(self.variables[name])
            return
        th = _Thread(self, pi, None, self.t, {}, "")
        try:
            self.vars[name] = self._eval(th, st.get("value", 0), path)
        except ExprError as e:
            self._error(th, path, f"Variable {name}: {e}")
            self.vars[name] = 0

    def _setvar(self, th, name, value, path=()):
        name = str(name or "")
        bad = _bad_var_name(name)
        if bad:
            raise ExprError(bad)
        if isinstance(value, (list, tuple)):
            value = copy.deepcopy(list(value))
        elif not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ExprError("unsupported value")
        if th is not None and name in th.locals:
            th.locals[name] = value
            return
        old = self.vars.get(name, None)
        self.vars[name] = value
        if old != value or type(old) is not type(value):
            self._queue.append(("event", "variable_changed", {"var": name, "value": value}, self.t, None))

    # ------------------------------------------------------------------ observation
    def _observe(self, t, dt, st):
        self._entered_now = []
        q = self._queue
        for name, inside in self._zones.items():
            if inside:
                self.zone_time[name] = self.zone_time.get(name, 0.0) + dt
        if self._freezing:
            self.freeze_time += dt
        if self._immobile:
            self.immobile_time += dt
        zones = st.get("zones")
        if zones:
            for name, inside in zones.items():
                inside = bool(inside)
                was = self._zones.get(name)
                if inside and not was:
                    self.zone_entries[name] = self.zone_entries.get(name, 0) + 1
                    self.zone_since[name] = t
                    self._entered_now.append(name)
                    q.append(("event", "zone_enter", {"zone": name}, t, None))
                elif was and not inside:
                    self.zone_since.pop(name, None)
                    q.append(("event", "zone_exit", {"zone": name}, t, None))
                self._zones[name] = inside
        head = st.get("head_zones")
        if head:
            for name, inside in head.items():
                inside = bool(inside)
                was = self._head.get(name)
                if inside and not was:
                    q.append(("event", "head_zone_enter", {"zone": name}, t, None))
                elif was and not inside:
                    q.append(("event", "head_zone_exit", {"zone": name}, t, None))
                self._head[name] = inside
        for key, attr in (("freezing", "_freezing"), ("immobile", "_immobile")):
            if key in st:
                v = bool(st[key])
                if v != getattr(self, attr):
                    q.append(("event", f"{key}_{'start' if v else 'end'}", {}, t, None))
                setattr(self, attr, v)
        if "detected" in st:
            v = bool(st["detected"])
            if v != self._detected:
                q.append(("event", "animal_found" if v else "animal_lost", {}, t, None))
            self._detected = v
        x, y = st.get("x"), st.get("y")
        if x is not None and y is not None:
            self._x, self._y = float(x), float(y)
        prev_d = self._distance
        if st.get("distance") is not None:
            self._distance = float(st["distance"])
        elif x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y)):
            if self._last_xy is not None:
                self._distance += math.hypot(float(x) - self._last_xy[0], float(y) - self._last_xy[1])
            self._last_xy = (float(x), float(y))
        if st.get("speed") is not None:
            self._speed = float(st["speed"])
        elif dt > 0:
            self._speed = (self._distance - prev_d) / dt

    def _devname(self, name):
        """The device a name used by the procedures refers to (a per-test DeviceView maps box names to its box)."""
        fn = getattr(self.devices, "resolve_name", None)
        return fn(name) if fn is not None and name else name

    def _poll_inputs(self, t, quiet=False):
        try:
            if hasattr(self.devices, "read_inputs_ex"):
                changes = self.devices.read_inputs_ex()
            else:
                changes = [tuple(c) + (None,) for c in self.devices.read_inputs()]
        except Exception as e:  # pragma: no cover - hardware dependent
            self._error(None, (), f"I/O: {e}")
            return
        for dev, ch, kind, value, ms in changes:
            if kind == "watchdog":
                self._watchdog_fired(t, dev)
            elif quiet:  # paused: keep the input states, no events
                self.inputs[(dev, ch)] = value if kind in ("analog", "encoder") else (1 if value else 0)
            else:
                self._input_changed(t, dev, ch, kind, value, ms)
        for e in self.devices.errors:
            self._error(None, (), e)

    def _watchdog_fired(self, t, dev):
        """The board's watchdog switched all its outputs off: record it (no command is sent to the board)."""
        for key in [k for k in self._trains if k[0] == dev]:
            tr = self._trains.pop(key)
            if tr["on"]:
                self._set_out(dev, key[1], 0, t, tr["typ"], hw=True, reason="watchdog")
        for (d, ch), v in list(self.outputs_state.items()):
            if d == dev and v:
                self._cancel_task(("shock", d, ch))
                self._set_out(d, ch, 0, t, "digital", hw=True, reason="watchdog")
        self._log_line(t, f"{dev}: the watchdog switched all outputs off")

    def _input_changed(self, t, dev, ch, kind, value, ms=None):
        key = (dev, ch)
        old = self.inputs.get(key)
        extra = {"board_ms": ms} if ms is not None else {}
        if kind in ("analog", "encoder"):
            self.inputs[key] = value
            self._log_io(t, dev, ch, "input", value, kind, **extra)
            if old is None or value != old:
                self._queue.append(("event", "input_changed", {"device": dev, "channel": ch, "value": value}, t, None))
            return
        v = 1 if value else 0
        if old is not None and v == (1 if old else 0):
            return
        if old is None and v == 0:
            self.inputs[key] = 0
            return
        self.inputs[key] = v
        self._log_io(t, dev, ch, "input", v, "digital", **extra)
        args = {"device": dev, "channel": ch, "value": v}
        self._queue.append(("event", "input_changed", args, t, None))
        self._queue.append(("event", "input_on" if v else "input_off", args, t, None))
        if v:
            self.input_counts[key] = self.input_counts.get(key, 0) + 1

    def _log_io(self, t, dev, ch, kind, value, typ=None, **extra):
        e = {"t": round(float(t), 4), "device": str(dev), "channel": str(ch), "kind": kind,
             "value": float(value) if isinstance(value, float) else int(value) if isinstance(value, (bool, int))
             else value}
        if typ:
            e["type"] = typ
        e.update(extra)
        self.io_events.append(e)

    def _external(self, t, name, args):
        self._queue.append(("event", name, args, t, None))
        if not self.started or self.stopped or self._busy:
            return
        self._busy = True
        try:
            self.t = max(self.t, t)
            self._run(self.t)
        finally:
            self._busy = False
        self._after()

    # ------------------------------------------------------------------ scheduling
    def _add_task(self, due, fn, key=None):
        self._task_seq += 1
        if key is not None:
            self._cancel_task(key)
            self._task_keys[key] = self._task_seq
        heapq.heappush(self._tasks, (due, self._task_seq, fn, key))
        return self._task_seq

    def _cancel_task(self, key):
        seq = self._task_keys.pop(key, None)
        if seq is not None:
            self._cancelled.add(seq)

    def _tick(self, t):
        for key in list(self._trains):
            self._train_tick(key, t)
        while self._tasks and self._tasks[0][0] <= t + EPS:
            due, seq, fn, key = heapq.heappop(self._tasks)
            if seq in self._cancelled:
                self._cancelled.discard(seq)
                continue
            if key is not None and self._task_keys.get(key) == seq:
                del self._task_keys[key]
            fn(due)
        for name, tm in self.timers.items():
            if tm.get("due") is not None and tm["due"] <= t + EPS:
                due, tm["due"] = tm["due"], None
                self._queue.append(("event", "timer_elapsed", {"timer": name}, due, None))
        for name, s in self.schedules.items():
            while s.tick(t):
                self._queue.append(("event", "reinforcer_earned", {"schedule": name}, s.last_t, None))

    def _poll_detectors(self, t, conditions=False):
        """Stateful detectors, once per frame; condition detectors also after every change within the frame."""
        found = False
        for h in self.handlers:
            if h.enabled and not h.det.generic and self.proc_enabled[h.proc_i] \
                    and (h.det.event in _CONDITION_EVENTS) == conditions:
                for t_occ, args in h.det.poll(self, t):
                    self._queue.append(("fire", h, args, t_occ, None))
                    found = True
        for th in self.threads:
            w = th.wait
            if th.alive and w and w[0] == "event" and not w[1].generic \
                    and (w[1].event in _CONDITION_EVENTS) == conditions:
                hits = w[1].poll(self, t)
                w[1].hits.extend(hits)
                found = found or bool(hits)
        return found

    def _run(self, t, final=False):
        guard = 0
        while True:
            progressed = False
            while self._queue:
                guard += 1
                if guard > 2000:
                    self._error(None, (), "too many events in one frame (do signals or variable changes trigger "
                                          "each other in a loop?)")
                    self._queue.clear()
                    break
                self._dispatch(self._queue.popleft())
                progressed = True
                if self.ended and not final:
                    break
            if self.ended and not final:
                self._queue.clear()
                break
            ready = [th for th in self.threads if th.alive and self._ready(th, t)]
            for th in ready:
                if th.alive:
                    self._resume(th)
                    progressed = True
            self.threads = [th for th in self.threads if th.alive]
            if self._poll_detectors(t, conditions=True):
                progressed = True
            if not progressed and not self._queue:
                break
            guard += 1
            if guard > 5000:
                self._error(None, (), "procedures did not settle within one frame")
                break

    def _dispatch(self, item):
        kind, a, args, t_occ, _ = item
        if kind == "fire":
            self._fire(a, t_occ, args)
            return
        name = a
        waiting = [th for th in self.threads if th.alive and th.wait and th.wait[0] == "event"
                   and th.wait[1].generic and th.wait[1].matches(name, args)]
        for h in list(self.handlers):
            if h.enabled and h.det.generic and self.proc_enabled[h.proc_i] and h.det.matches(name, args):
                self._fire(h, t_occ, dict(args, event=name))
        for th in waiting:  # threads that started waiting during this dispatch do not see this occurrence
            if th.alive and th.wait and th.wait[0] == "event":
                th.wait[1].hits.append((t_occ, dict(args, event=name)))

    def _event_locals(self, t_occ, args):
        name = next((args[k] for k in ("zone", "channel", "key", "var", "timer", "area", "name", "switch",
                                       "schedule") if args.get(k) not in (None, "")), "")
        v = args.get("value", 1)
        if isinstance(v, tuple):
            v = [0 if x is None else x for x in v]
        if not isinstance(v, (int, float, str, list)):
            v = 0
        return {"event_time": t_occ, "event_value": v, "event_name": name, "timed_out": 0}

    def _fire(self, h: _Handler, t_occ, args):
        if not h.enabled or not self.proc_enabled[h.proc_i]:
            return
        h.threads = [x for x in h.threads if x.alive]
        if h.once and h.count:
            return
        if h.threads:
            if h.mode == "ignore":
                return
            if h.mode == "restart":
                for x in h.threads:
                    self._kill(x)
                h.threads = []
        h.count += 1
        th = _Thread(self, h.proc_i, h, t_occ, self._event_locals(t_occ, args), h.label)
        th.gen = self._thread_main(th, h.st.get("body") or [], h.path + ("body",))
        self.threads.append(th)
        h.threads.append(th)
        self._resume(th)

    def _ready(self, th, t):
        w = th.wait
        if w is None:
            return False
        k = w[0]
        if k == "time":
            return t + EPS >= w[1]
        if k == "frame":
            return self._update_no > w[1]
        if k == "until":
            if self._truth(th, w[1], w[3]):
                th.resume_clock, th.locals["timed_out"] = t, 0
                return True
            if w[2] is not None and t + EPS >= w[2]:
                th.resume_clock, th.locals["timed_out"] = w[2], 1
                return True
            return False
        if k == "event":
            det = w[1]
            if det.hits:
                t_occ, args = det.hits.pop(0)
                th.resume_clock = t_occ
                th.locals.update(self._event_locals(t_occ, args))
                th.locals["timed_out"] = 0
                return True
            if w[2] is not None and t + EPS >= w[2]:
                th.resume_clock, th.locals["timed_out"] = w[2], 1
                return True
        return False

    def _resume(self, th):
        if not th.alive:
            return
        th.steps = 0
        try:
            th.wait = th.gen.send(None)
        except StopIteration:
            th.alive, th.wait = False, None
        except Exception as e:  # defensive: never crash the test
            self._error(th, th.path, f"internal error: {e!r}")
            th.alive, th.wait = False, None

    def _kill(self, th):
        th.alive = False
        th.wait = None
        if th.gen is not None:
            try:
                th.gen.close()
            except Exception:  # pragma: no cover
                pass

    def _after(self):
        if self._pending_stop is not None:
            t, self._pending_stop = self._pending_stop, None
            self.stop(t)
        elif self.ended and not self.stopped:
            self.stop(self.t)

    # ------------------------------------------------------------------ statements
    def _thread_main(self, th, stmts, path):
        try:
            yield from self._exec(th, stmts, path)
        except (_Stop, _Break):
            return

    def _exec(self, th, stmts, path):
        for i, st in enumerate(stmts or []):
            if not isinstance(st, dict) or st.get("enabled", True) is False:
                continue
            t = st.get("type")
            if t in ("comment", "var", "when", None):
                continue
            p = path + (i,)
            th.path = p
            th.steps += 1
            if th.steps > STEP_BUDGET:
                yield ("frame", self._update_no)
                th.clock = self.t
            if t == "set":
                try:
                    self._st_set(th, st, p)
                except ExprError as e:
                    self._error(th, p, f"Set {st.get('var', '')}: {e}")
            elif t == "do":
                self._st_do(th, st, p)
            elif t == "if":
                br = "body" if self._truth(th, st.get("cond"), p) else "else"
                yield from self._exec(th, st.get(br) or [], p + (br,))
            elif t == "repeat":
                yield from self._st_repeat(th, st, p)
            elif t == "wait":
                yield from self._st_wait(th, st, p)
            elif t == "stop":
                self._st_stop(th, st, p)
            else:
                self._error(th, p, f"unknown statement type '{t}'")

    def _st_set(self, th, st, p):
        name = st.get("var")
        value = self._eval(th, st.get("value"), p)
        idx = st.get("index")
        if idx in (None, ""):
            self._setvar(th, name, value, p)
            return
        arr = self._lookup(th)(str(name))
        if not isinstance(arr, list):
            raise ExprError(f"'{name}' is not an array")
        i = self._eval(th, idx, p)
        if isinstance(i, float) and i.is_integer():
            i = int(i)
        if not isinstance(i, int) or not -len(arr) <= i < len(arr):
            raise ExprError(f"index {i!r} out of range (length {len(arr)})")
        new = list(arr)
        new[i] = copy.deepcopy(value)
        self._setvar(th, name, new, p)

    def _st_repeat(self, th, st, p):
        mode = _repeat_mode(st)
        var = str(st.get("var") or "")
        n = int(self._num(th, st.get("count"), p, "count")) if mode == "count" else 0
        i = 0
        while True:
            if mode == "count" and i >= n:
                break
            if mode == "while" and not self._truth(th, st.get("while"), p):
                break
            if var:
                try:
                    self._setvar(th, var, i, p)
                except ExprError as e:
                    self._error(th, p, f"Repeat: {e}")
            before = th.waits
            try:
                yield from self._exec(th, st.get("body") or [], p + ("body",))
            except _Break:
                break
            i += 1
            if mode == "forever" and th.waits == before:
                yield ("frame", self._update_no)  # a forever loop that did not wait polls once per frame
                th.clock = self.t

    def _st_wait(self, th, st, p):
        mode = _wait_mode(st)
        to = st.get("timeout")
        tdue = th.clock + self._num(th, to, p, "timeout") if to not in (None, "", 0) else None
        th.locals["timed_out"] = 0
        th.waits += 1
        if mode == "seconds":
            due = th.clock + max(0.0, self._num(th, st.get("seconds"), p, "Wait"))
            yield ("time", due)
            th.clock = due
        elif mode == "until":
            if self._truth(th, st.get("until"), p):
                return
            th.resume_clock = self.t
            yield ("until", st.get("until"), tdue, p)
            th.clock = th.resume_clock
        else:
            det = _Detector(self, st, th, p)
            th.resume_clock = self.t
            yield ("event", det, tdue)
            th.clock = th.resume_clock

    def _st_stop(self, th, st, p):
        w = st.get("what", "handler")
        if w == "loop":
            raise _Break()
        if w == "procedure":
            self._disable_proc(th.proc_i, keep=th)
        elif w == "all":
            for pi in range(len(self.procedures)):
                self._disable_proc(pi, keep=th)
        elif w == "test":
            self._end_test(th)
        raise _Stop()

    def _disable_proc(self, pi, keep=None):
        self.proc_enabled[pi] = False
        for x in self.threads:
            if x.proc_i == pi and x is not keep and x.alive:
                self._kill(x)

    # ------------------------------------------------------------------ actions
    def _st_do(self, th, st, p):
        name = st.get("action")
        spec = ACTION_SPECS.get(name)
        if spec is None:
            self._error(th, p, f"unknown action '{name}'")
            return
        args = {}
        for prm in spec["params"]:
            v = st.get(prm["name"], prm["default"])
            typ = prm["type"]
            if typ in ("number", "int"):
                d = prm["default"] if isinstance(prm["default"], (int, float)) else 0
                v = self._num(th, v, p, prm["label"], default=d)
                if typ == "int":
                    v = int(round(v))
            elif typ == "expr":
                try:
                    v = self._eval(th, v, p)
                except ExprError as e:
                    self._error(th, p, f"{prm['label']}: {e}")
                    return
            elif typ == "text":
                v = self._text(th, v if v is not None else "", p)
            else:
                v = "" if v is None else str(v).strip()
            args[prm["name"]] = v
        self.fired.append((round(self.t, 3), th.label, name, _params_text(spec, st)))
        try:
            getattr(self, "_a_" + name)(th, p, **args)
        except (_Stop, _Break):
            raise
        except ExprError as e:
            self._error(th, p, f"{spec['label']}: {e}")
        except Exception as e:  # never crash the test
            self._error(th, p, f"{spec['label']}: {e}")

    # -- outputs
    def _resolve(self, th, p, device, channel, kinds=("output", "pwm")):
        dev = self._devname(device) or self.devices.find_channel(channel, kinds) or ""
        if not dev:
            dev = "virtual"
        if self.devices.configured and not self.devices.has(dev):
            self._error(th, p, f"device '{dev}' is not configured — simulated")
        return dev, channel

    def _set_out(self, dev, ch, value, t, typ="digital", hw=False, max_s=None, **extra):
        key = (dev, ch)
        old = self.outputs_state.get(key, 0)
        if not hw and (value != old or key not in self.outputs_state):
            try:
                self.devices.set_output(dev, ch, value, max_s=max_s)
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"output {dev}/{ch}: {e}")
        if value != old or extra:
            self.outputs_state[key] = value
            self._log_io(t, dev, ch, "output", value, typ, **extra)
            if bool(value) != bool(old):
                self._queue.append(("event", "output_on" if value else "output_off",
                                    {"device": dev, "channel": ch, "value": value}, t, None))

    def _a_output_on(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, typ)

    def _a_output_off(self, th, p, device, channel, typ="digital"):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 0, self.t, typ)

    def _a_output_toggle(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, 0 if self.outputs_state.get((dev, ch)) else 1, self.t)

    def _a_output_set(self, th, p, device, channel, value):
        dev, ch = self._resolve(th, p, device, channel)
        self._set_out(dev, ch, max(0.0, min(1.0, float(value))), self.t, "pwm")

    def _a_all_outputs_off(self, th, p, device):
        device = self._devname(device)
        for key in list(self._trains):
            if not device or key[0] == device:
                self._stop_train(key, self.t)
        for (dev, ch), v in list(self.outputs_state.items()):
            if v and (not device or dev == device):
                self._set_out(dev, ch, 0, self.t)

    def _start_train(self, th, p, device, channel, period, width, count, typ, t0=None):
        dev, ch = self._resolve(th, p, device, channel)
        key = (dev, ch)
        self._stop_train(key, self.t)
        width = max(0.0005, float(width))
        period = max(width, float(period))
        hw = False
        if getattr(self.devices.device(dev, create=True), "hardware_pulses", False):
            try:
                hw = bool(self.devices.pulse_train(dev, ch, period, width, count))
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(th, p, f"pulse train: {e}")
        t0 = self.t if t0 is None else t0
        self._trains[key] = {"t0": t0, "period": period, "width": width, "n": count, "k": 0, "on": False,
                             "hw": hw, "typ": typ, "first": True}
        self._train_tick(key, self.t)

    def _train_tick(self, key, t):
        tr = self._trains.get(key)
        if tr is None:
            return
        dev, ch = key
        guard = 0
        while guard < 100000:
            guard += 1
            on_t = tr["t0"] + tr["k"] * tr["period"]
            if not tr["on"]:
                if tr["n"] and tr["k"] >= tr["n"]:
                    del self._trains[key]
                    return
                if on_t > t + EPS:
                    return
                if not tr["hw"] and t - on_t > STALL_S:
                    # the frames stalled: deliver the remaining pulses from now instead of a burst of zero-length
                    # pulses that the device cannot follow (e.g. pellets counted but not dispensed)
                    lag = t - on_t
                    tr["t0"] += lag
                    on_t = t
                    if not tr.get("delayed"):
                        self._log_line(t, f"Output {dev}/{ch}: pulses delayed by {lag:.2f} s (the test stalled)")
                    tr["delayed"] = tr.get("delayed", 0.0) + lag
                extra = {"train_start": True} if tr["first"] and tr["typ"] == "train" else {}
                tr["first"] = False
                self._set_out(dev, ch, 1, on_t, tr["typ"], hw=tr["hw"], **extra)
                tr["on"] = True
            else:
                off_t = on_t + tr["width"]
                if off_t > t + EPS:
                    return
                if not tr["hw"] and t - off_t > STALL_S:
                    off_t = t  # the output really stayed on until now
                    tr["t0"] = t - tr["width"] - tr["k"] * tr["period"]
                self._set_out(dev, ch, 0, off_t, tr["typ"], hw=tr["hw"])
                tr["on"] = False
                tr["k"] += 1

    def _stop_train(self, key, t):
        tr = self._trains.pop(key, None)
        if tr is None:
            return
        if tr["hw"]:
            try:
                self.devices.stop_train(*key)
            except Exception:  # pragma: no cover
                pass
        if tr["on"]:
            self._set_out(key[0], key[1], 0, t, tr["typ"], hw=tr["hw"])
            if tr["hw"]:
                try:
                    self.devices.set_output(key[0], key[1], 0)
                except Exception:  # pragma: no cover
                    pass

    def _a_output_pulse(self, th, p, device, channel, duration, typ="pulse"):
        self._start_train(th, p, device, channel, duration, duration, 1, typ)

    def _a_pulse_train(self, th, p, device, channel, frequency, pulse_width, duration):
        if frequency <= 0:
            raise ExprError("the frequency must be positive")
        period = 1.0 / frequency
        count = int(round(duration * frequency)) if duration > 0 else 0
        if duration > 0 and count == 0:
            count = 1
        self._start_train(th, p, device, channel, period, pulse_width / 1000.0, count, "train")

    def _a_pulse_train_stop(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._stop_train((dev, ch), self.t)

    def _a_sync_pulse(self, th, p, device, channel, width):
        self._start_train(th, p, device, channel, width / 1000.0, width / 1000.0, 1, "sync")

    def _a_pellet(self, th, p, device, channel, count, pulse_width, gap):
        n = max(1, int(count))
        width = max(0.001, pulse_width / 1000.0)
        dev, ch = self._resolve(th, p, device, channel)
        self.pellet_counts[(dev, ch)] = self.pellet_counts.get((dev, ch), 0) + n
        self._start_train(th, p, dev, ch, max(width * 2, gap), width, n, "pellet")

    def _a_shock_on(self, th, p, device, channel, max_duration):
        dev, ch = self._resolve(th, p, device, channel)
        m = max_duration if 0 < max_duration <= SHOCK_MAX_S else (2.0 if max_duration <= 0 else SHOCK_MAX_S)
        if max_duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: safety cut-off limited to {SHOCK_MAX_S:g} s")
        self._stop_train((dev, ch), self.t)
        self._set_out(dev, ch, 1, self.t, "shock", max_s=m)
        self._shock_keys.add((dev, ch))
        due = self.t + m

        def cut(tt, dev=dev, ch=ch):
            if self.outputs_state.get((dev, ch)):
                self._set_out(dev, ch, 0, tt, "shock")
                self._log_line(tt, f"Shock {dev}/{ch} switched off by the safety cut-off")
        self._add_task(due, cut, ("shock", dev, ch))

    def _a_shock_off(self, th, p, device, channel):
        dev, ch = self._resolve(th, p, device, channel)
        self._cancel_task(("shock", dev, ch))
        self._set_out(dev, ch, 0, self.t, "shock")

    def _a_shock_pulse(self, th, p, device, channel, duration):
        d = min(max(0.0, duration), SHOCK_MAX_S)
        if duration > SHOCK_MAX_S:
            self._error(th, p, f"Shock: duration limited to {SHOCK_MAX_S:g} s")
        dev, ch = self._resolve(th, p, device, channel)
        self._shock_keys.add((dev, ch))
        self._start_train(th, p, dev, ch, d, d, 1, "shock")

    # -- audio
    def _audio_dev(self, device):
        return self._devname(device) or self.devices.find_type("audio") or "audio"

    def _audio(self, th, p, device, cmd, channel, value, duration, **kw):
        dev = self._audio_dev(device)
        if self.devices.has(dev):
            try:
                self.devices.audio(dev, cmd, duration=duration, **kw)
            except Exception as e:  # pragma: no cover
                self._error(th, p, f"audio: {e}")
            for err in self.devices.device(dev).errors:
                self._error(th, p, err)
        key = (dev, channel)
        self._audio_on[key] = value
        self._log_io(self.t, dev, channel, "output", value, "audio")
        if duration and duration > 0:
            self._add_task(self.t + duration, lambda tt, key=key: self._audio_off(key, tt), ("audio",) + key)

    def _audio_off(self, key, t):
        if key in self._audio_on:
            del self._audio_on[key]
            self._cancel_task(("audio",) + key)
            self._log_io(t, key[0], key[1], "output", 0, "audio")

    def _a_tone(self, th, p, device, frequency, duration, volume):
        self._audio(th, p, device, "tone", "tone", frequency, duration, frequency=frequency, volume=volume)

    def _a_white_noise(self, th, p, device, duration, volume):
        self._audio(th, p, device, "noise", "noise", 1, duration, volume=volume)

    def _a_play_sound(self, th, p, device, file, duration, volume):
        self._audio(th, p, device, "file", "sound", 1, duration, file=file, volume=volume)

    def _a_stop_sound(self, th, p, device):
        dev = self._audio_dev(device)
        if self.devices.has(dev):
            self.devices.audio(dev, "stop")
        for key in list(self._audio_on):
            if key[0] == dev:
                self._audio_off(key, self.t)

    def _a_beep(self, th, p):
        dev = self.devices.find_type("audio")
        if dev:
            self._a_tone(th, p, dev, 1000, 0.2, 0.5)
        else:
            self.outputs.beep()

    # -- communication
    def _a_serial_send(self, th, p, device, text):
        device = self._devname(device)
        dev = device or self.devices.find_type("serial") or self.devices.find_type("arduino")
        if dev and self.devices.has(dev):
            self.devices.send(dev, text)
            self.outputs.log.append(f"serial: {text}")
        elif device:
            self._error(th, p, f"Send serial command: device '{device}' is not configured")
            self.outputs.log.append(f"serial: {text}")
        else:
            self.outputs.write(text)

    def _a_signal(self, th, p, name):
        self._queue.append(("event", "signal", {"name": name}, self.t, None))

    def _set_switch(self, name, v, t):
        old = self.switches.get(name, 0)
        self.switches[name] = v
        if v != old:
            self._log_io(t, "virtual", name, "output", v, "switch")
            self._queue.append(("event", f"virtual_switch_{'on' if v else 'off'}", {"switch": name}, t, None))

    def _a_virtual_switch_on(self, th, p, switch):
        self._set_switch(switch, 1, self.t)

    def _a_virtual_switch_off(self, th, p, switch):
        self._set_switch(switch, 0, self.t)

    def _a_virtual_switch_toggle(self, th, p, switch):
        self._set_switch(switch, 0 if self.switches.get(switch) else 1, self.t)

    def _a_simulate_input(self, th, p, device, channel, value):
        dev = self._devname(device) or self.devices.find_channel(channel, ("input", "analog", "encoder")) or "virtual"
        self.devices.set_input(dev, channel, value)
        self._poll_inputs(self.t)

    # -- variables, timers, schedules
    def _a_set_variable(self, th, p, var, value):
        self._setvar(th, var, value, p)

    def _a_increment(self, th, p, var, by):
        cur = self._lookup(th)(var) if var in self.vars or var in th.locals else 0
        if not isinstance(cur, (int, float)):
            raise ExprError(f"'{var}' is not a number")
        self._setvar(th, var, cur + by, p)

    def _a_decrement(self, th, p, var, by):
        self._a_increment(th, p, var, -by)

    def _a_array_append(self, th, p, var, value):
        cur = self.vars.get(var, [])
        if not isinstance(cur, list):
            raise ExprError(f"'{var}' is not an array")
        if len(cur) >= MAX_SEQ:
            raise ExprError("array too long")
        self._setvar(th, var, cur + [value], p)

    def _timer_value(self, name):
        tm = self.timers.get(name)
        if not tm:
            return 0.0
        return tm["acc"] + (self.t - tm["start"] if tm["start"] is not None else 0.0)

    def _a_start_timer(self, th, p, timer, seconds):
        tm = self.timers.setdefault(timer, {"acc": 0.0, "start": None, "due": None})
        if tm["start"] is None:
            tm["start"] = th.clock if th.clock <= self.t else self.t
        tm["due"] = (tm["start"] - tm["acc"] + seconds) if seconds and seconds > 0 else None

    def _a_stop_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm and tm["start"] is not None:
            tm["acc"] += self.t - tm["start"]
            tm["start"] = None
            tm["due"] = None

    def _a_reset_timer(self, th, p, timer):
        tm = self.timers.get(timer)
        if tm:
            running = tm["start"] is not None
            tm.update(acc=0.0, start=self.t if running else None, due=None)

    def _a_schedule_start(self, th, p, schedule, spec):
        self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)

    def _a_schedule_response(self, th, p, schedule, spec, var):
        s = self.schedules.get(schedule)
        if s is None:
            if not spec:
                raise ExprError(f"schedule '{schedule}' has not been started")
            s = self.schedules[schedule] = Schedule(spec, self.rng, t0=self.t)
        ok = s.response(self.t)
        if var:
            self._setvar(th, var, int(ok), p)
        if ok:
            self._queue.append(("event", "reinforcer_earned", {"schedule": schedule}, self.t, None))

    # -- test
    def _log_line(self, t, msg):
        self.log_lines.append((t, msg))
        self.outputs.log.append(f"{t:.2f}s {msg}")
        if self.on_log:
            try:
                self.on_log(msg, t)
            except Exception:  # pragma: no cover
                pass

    def _a_mark(self, th, p, name):
        name = name or "Mark"
        self.marks.append({"behaviour": name, "t": self.t, "t_end": None})
        if self.on_mark:
            self.on_mark(name, self.t)
        self._queue.append(("event", "event_marked", {"name": name}, self.t, None))

    def _a_mark_start(self, th, p, name):
        if any(m["behaviour"] == name and m["t_end"] is None for m in self.state_events):
            return
        m = {"behaviour": name, "t": self.t, "t_end": None}
        self.state_events.append(m)
        self.marks.append(m)
        self._queue.append(("event", "event_marked", {"name": name}, self.t, None))

    def _a_mark_end(self, th, p, name):
        for m in self.state_events:
            if m["behaviour"] == name and m["t_end"] is None:
                m["t_end"] = self.t

    def _a_log(self, th, p, text):
        self._log_line(self.t, text)

    def _end_test(self, th=None):
        if self.ended:
            return
        self.ended = True
        if self.on_end:
            self.on_end()

    def _a_end_test(self, th, p):
        self._end_test(th)
        raise _Stop()

    def _pause(self, t):
        if self.paused:
            return
        self.paused = True
        self._pause_t = t
        self.pauses.append([t, None])
        if self.started and not self.stopped:
            self._pause_safety(t)
        self._queue.append(("event", "test_paused", {}, t, None))
        if self.on_pause:
            self.on_pause(t)
        self._dispatch_now(t)  # "when test paused" runs now, not after the pause

    def _pause_safety(self, t):
        """Pulse trains stop and shocks go off (they cannot be timed while the test clock is stopped); with
        outputs_off_on_pause every output and sound goes off too."""
        for key in list(self._trains):
            self._stop_train(key, t)
        for key in list(self._shock_keys):
            self._cancel_task(("shock",) + key)
            if self.outputs_state.get(key):
                self._set_out(key[0], key[1], 0, t, "shock")
        if not self.outputs_off_on_pause:
            return
        n = 0
        for (dev, ch), v in list(self.outputs_state.items()):
            if v:
                self._set_out(dev, ch, 0, t, "pwm" if isinstance(v, float) and v not in (0.0, 1.0) else "digital")
                n += 1
        for dev in {k[0] for k in self._audio_on}:
            try:
                if self.devices.has(dev):
                    self.devices.audio(dev, "stop")
            except Exception as e:  # pragma: no cover - hardware dependent
                self._error(None, (), f"audio: {e}")
        for key in list(self._audio_on):
            self._audio_off(key, t)
            n += 1
        if n:
            self._log_line(t, "Test paused: outputs switched off")

    def _dispatch_now(self, t):
        if not self.started or self.stopped or self._busy:
            return  # inside a frame / handler: the queued event is dispatched by the running loop
        self._busy = True
        try:
            self.t = max(self.t, t)
            self._run(self.t)
        finally:
            self._busy = False
        self._after()

    def _resume_test(self, t):
        if not self.paused:
            return
        self.paused = False
        self._pause_t = None
        if self.pauses and self.pauses[-1][1] is None:
            self.pauses[-1][1] = t
        self._queue.append(("event", "test_resumed", {}, t, None))
        if self.on_resume:
            self.on_resume(t)
        self._dispatch_now(t)

    def _a_pause_test(self, th, p):
        self._pause(self.t)

    def _a_resume_test(self, th, p):
        self._resume_test(self.t)

    def _procs_named(self, name):
        idx = [i for i, q in enumerate(self.procedures) if q.get("name") == name]
        if not idx:
            raise ExprError(f"no procedure called '{name}'")
        return idx

    def _a_enable_procedure(self, th, p, procedure):
        for i in self._procs_named(procedure):
            self.proc_enabled[i] = True
            if not any(h.proc_i == i for h in self.handlers):
                for j, st in enumerate(self.procedures[i].get("statements") or []):
                    if isinstance(st, dict) and st.get("type") == "when" and st.get("enabled", True) is not False:
                        det = _Detector(self, st, th, (j,), initial=True)
                        self.handlers.append(_Handler(i, (j,), st, det, st.get("event")))

    def _a_disable_procedure(self, th, p, procedure):
        for i in self._procs_named(procedure):
            self._disable_proc(i, keep=th)
            if i == th.proc_i:
                raise _Stop()

    # -- touch screen
    def _stimulus(self, cmd, params):
        if self.on_stimulus:
            self.on_stimulus(cmd, params)

    def _a_show_stimulus(self, th, p, area, image, shape, color):
        self.stimuli[area] = {"image": image, "shape": shape, "color": color}
        self._log_io(self.t, "screen", area, "output", 1, "stimulus")
        self._stimulus("show", {"area": area, "image": image, "shape": shape, "color": color})

    def _a_hide_stimulus(self, th, p, area):
        if self.stimuli.pop(area, None) is not None:
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self._stimulus("hide", {"area": area})

    def _a_clear_screen(self, th, p):
        for area in list(self.stimuli):
            self._log_io(self.t, "screen", area, "output", 0, "stimulus")
        self.stimuli.clear()
        self._stimulus("clear", {})


def _output_alias(on: bool):
    def action(self, th, p, device, channel):
        (self._a_output_on if on else self._a_output_off)(th, p, device, channel)
    return action


for _name, _on in (("light_on", True), ("light_off", False), ("door_open", True), ("door_close", False),
                   ("lever_extend", True), ("lever_retract", False)):
    setattr(ProcedureEngine, "_a_" + _name, _output_alias(_on))


def io_measures(io_events, duration, t_range=None, devices=None) -> dict:
    """See :func:`manymaze.core.iodevices.io_measures`."""
    from .iodevices import io_measures as f

    return f(io_events, duration, t_range, devices)


# ====================================================================== examples
EXAMPLES = {
    "Fear conditioning (tone + shock)": {"name": "Fear conditioning", "enabled": True, "statements": [
        {"type": "comment", "text": "2 min baseline, then 3 tone–shock pairings with random intervals"},
        {"type": "var", "name": "pairings", "value": 0, "result": True},
        {"type": "wait", "mode": "seconds", "seconds": 120},
        {"type": "repeat", "mode": "count", "count": 3, "body": [
            {"type": "do", "action": "tone", "frequency": 2800, "duration": 30, "volume": 0.8},
            {"type": "do", "action": "mark_start", "name": "Tone"},
            {"type": "wait", "mode": "seconds", "seconds": 28},
            {"type": "do", "action": "shock_pulse", "device": "", "channel": "shocker", "duration": 2},
            {"type": "wait", "mode": "seconds", "seconds": 2},
            {"type": "do", "action": "mark_end", "name": "Tone"},
            {"type": "set", "var": "pairings", "value": "pairings + 1"},
            {"type": "wait", "mode": "seconds", "seconds": "randint(60, 120)"}]}]},
    "Lever press for food (FR 5)": {"name": "FR5 lever", "enabled": True, "statements": [
        {"type": "var", "name": "rewards", "value": 0, "result": True},
        {"type": "when", "event": "test_start", "body": [
            {"type": "do", "action": "schedule_start", "schedule": "lever", "spec": "FR 5"},
            {"type": "do", "action": "light_on", "device": "", "channel": "house_light"}]},
        {"type": "when", "event": "input_on", "device": "", "channel": "lever", "mode": "parallel", "body": [
            {"type": "do", "action": "schedule_response", "schedule": "lever", "spec": "", "var": "due"},
            {"type": "if", "cond": "due", "body": [
                {"type": "do", "action": "pellet", "device": "", "channel": "pellet", "count": 1},
                {"type": "do", "action": "increment", "var": "rewards", "by": 1}]}]},
        {"type": "when", "event": "variable_changed", "var": "rewards", "body": [
            {"type": "if", "cond": "rewards >= 50", "body": [{"type": "stop", "what": "test"}]}]}]},
    "Optogenetic stimulation in a zone": {"name": "Opto in zone", "enabled": True, "statements": [
        {"type": "when", "event": "zone_enter", "zone": "", "mode": "restart", "body": [
            {"type": "if", "cond": "event_name == 'Centre'", "body": [
                {"type": "do", "action": "pulse_train", "device": "", "channel": "laser", "frequency": 20,
                 "pulse_width": 5, "duration": 0}]}]},
        {"type": "when", "event": "zone_exit", "zone": "", "body": [
            {"type": "if", "cond": "event_name == 'Centre'", "body": [
                {"type": "do", "action": "pulse_train_stop", "device": "", "channel": "laser"}]}]}]},
    "Spontaneous alternation counter": {"name": "Alternations", "enabled": True, "statements": [
        {"type": "var", "name": "arms", "value": "[]"},
        {"type": "var", "name": "alternations", "value": 0, "result": True},
        {"type": "when", "event": "zone_enter", "zone": "", "mode": "parallel", "body": [
            {"type": "if", "cond": "event_name in ['A', 'B', 'C']", "body": [
                {"type": "do", "action": "array_append", "var": "arms", "value": "event_name"},
                {"type": "if", "cond": "len(arms) >= 3 and arms[-1] != arms[-2] and arms[-2] != arms[-3] "
                                       "and arms[-1] != arms[-3]",
                 "body": [{"type": "do", "action": "increment", "var": "alternations", "by": 1}]}]}]}]},
}
