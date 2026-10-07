"""Catalogues of the procedure language: statement types, events and actions with their parameters, constants."""

from __future__ import annotations

import math


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
