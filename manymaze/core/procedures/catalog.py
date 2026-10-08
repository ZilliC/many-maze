"""Catalogues of the procedure language: statement types, events and actions with their parameters, constants."""

from __future__ import annotations

import math


def P(name, type="number", default=None, label=None, req=False, help=""):
    """Parameter spec. Types: number, int, expr, text, zone, key, device, input, output, sensor, thermostat,
    odour, pump, audio, var, timer, switch, area, name, procedure, file, schedule, spec, sequence, bool,
    choice:a|b|c."""
    return {"name": name, "type": type, "default": default, "label": label or name.replace("_", " ").capitalize(),
            "req": req, "help": help}


_DEV = P("device", "device", "", "Device", help="empty = the device that has this channel")
_IN = P("channel", "input", "", "Input", req=True)
_OUT = P("channel", "output", "", "Output", req=True)
_ZONE_ANY = P("zone", "zone", "", "Zone", help="empty = any zone")
_ZONE = P("zone", "zone", "", "Zone", req=True)
_SENSOR = P("channel", "sensor", "", "Sensor", req=True)
_SENSOR_ANY = P("channel", "sensor", "", "Sensor", help="empty = any sensor")
_THERMO = P("channel", "thermostat", "", "Temperature controller", req=True)
_PUMP = P("channel", "pump", "", "Pump", req=True)
_PUMP_ANY = P("channel", "pump", "", "Pump", help="empty = any pump")

# name: (group, label, params). "generic" events are matched against occurrences; the others are detectors.
EVENT_SPECS: dict[str, dict] = {}


def _ev(name, group, label, params=(), generic=True, help=""):
    EVENT_SPECS[name] = {"group": group, "label": label, "params": list(params), "generic": generic, "help": help}


_ev("test_waiting", "Test", "Test is waiting to start", help="runs before the test starts (from when it is armed); "
    "use Prevent / Allow test start to hold the start until something is ready")
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
_ev("movement_start", "Inputs", "Movement detector: movement starts",
    [_DEV, P("channel", "input", "", "Detector", help="empty = any movement detector (PIR)")])
_ev("movement_end", "Inputs", "Movement detector: movement ends",
    [_DEV, P("channel", "input", "", "Detector", help="empty = any movement detector (PIR)")])
_ev("sensor_above", "Sensors", "Sensor rises above", [_DEV, _SENSOR, P("threshold", "number", 25, "Level", True)],
    False)
_ev("sensor_below", "Sensors", "Sensor falls below", [_DEV, _SENSOR, P("threshold", "number", 20, "Level", True)],
    False)
_ev("sensor_out_of_range", "Sensors", "Sensor leaves its alert range", [_DEV, _SENSOR_ANY],
    help="the channel's alert_min / alert_max options; an alert is also sent if an alert device is configured")
_ev("sensor_in_range", "Sensors", "Sensor back in its alert range", [_DEV, _SENSOR_ANY])
_ev("temperature_reached", "Sensors", "Temperature controller reaches its target",
    [_DEV, P("channel", "thermostat", "", "Temperature controller", help="empty = any")])
_ev("pump_target_reached", "Pumps", "Pump reaches its target volume", [_DEV, _PUMP_ANY])
_ev("pump_stalled", "Pumps", "Pump stalled", [_DEV, _PUMP_ANY])
_ev("pump_volume_reaches", "Pumps", "Volume infused reaches",
    [_DEV, _PUMP, P("volume", "number", 0.1, "Volume (ml)", True)], False)
_ev("output_on", "Outputs", "Output switched on", [_DEV, P("channel", "output", "", "Output")])
_ev("output_off", "Outputs", "Output switched off", [_DEV, P("channel", "output", "", "Output")])
_ev("light_ramp_done", "Outputs", "Light ramp finished", [_DEV, P("channel", "output", "", "Light")])
_ev("pulse_sequence_done", "Outputs", "Pulse sequence finished", [_DEV, P("channel", "output", "", "Output")])
_ev("pellet_dropped", "Outputs", "Pellet detected", [_DEV, P("channel", "output", "", "Dispenser")],
    help="the dispenser's pellet sensor saw the pellet (Dispense pellet with a sensor)")
_ev("pellet_error", "Outputs", "Pellet dispenser error", [_DEV, P("channel", "output", "", "Dispenser")],
    help="no pellet detected after the retries (jammed or empty dispenser)")
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
# --- test timing (event wizard)
_ev("test_continuation", "Test", "Test continued",
    help="the test was ended by “End the test” with “allow continuation” and the experimenter continued it")
_ev("time_before_end", "Test", "Time until the test ends", [P("seconds", "number", 10, "Time before the end (s)", True)],
    False, "once, this long before the end of the test duration")
_ev("random_interval", "Test", "At random intervals", [P("min", "number", 5, "Shortest interval (s)", True),
                                                        P("max", "number", 15, "Longest interval (s)", True)], False,
    "each interval is drawn uniformly between the shortest and the longest")
_ev("time_of_day", "Test", "Time of day reached", [P("clock", "clock", "12:00", "Time of day (HH:MM[:SS])", True)],
    False, "once, when the computer's clock passes this time during the test")
# --- zones, orientation, investigation
_POINT = P("target", "point", "", "Zone or point", True)
_ev("investigation_start", "Zones", "Investigation of a zone starts", [_ZONE_ANY],
    help="the head is in the zone, or within its investigation distance while facing it")
_ev("investigation_end", "Zones", "Investigation of a zone stops", [_ZONE_ANY])
_ev("oriented_towards", "Zones", "Animal turns towards a zone or point",
    [_POINT, P("angle", "number", 45, "Within (°)", True)], False,
    "the body's orientation comes within this angle of the direction to the zone centre / point")
_ev("oriented_away", "Zones", "Animal turns away from a zone or point",
    [_POINT, P("angle", "number", 45, "Within (°)", True)], False,
    "the animal stops facing the zone / point (the angle grows beyond the given one)")
_ev("hidden_partial_exit", "Zones", "Partial exit from a hidden zone", [_ZONE_ANY],
    help="the animal was seen near a hidden zone between two times it was hidden in it (e.g. peeking out of a "
         "nest); event_value = how long it was out")
_ev("zone_not_entered", "Zones", "Animal fails to enter a zone for a time",
    [_ZONE, P("seconds", "number", 30, "Time (s)", True)], False,
    "since the start of the test or since it last left the zone; once per absence")
# --- animal
_ev("position_changed", "Animal", "Position changes",
    [P("part", "choice:centre|head|tail", "centre", "Body part"),
     P("distance", "number", 0, "By at least (px)", help="0 = any change")], False)
_ev("rearing_start", "Animal", "Rearing starts", help="detected from the animal's shape, as the rearing measures")
_ev("rearing_end", "Animal", "Rearing stops")
# --- rotary encoders
_ENC = P("channel", "input", "", "Encoder", req=True)
_ev("encoder_cw", "Inputs", "Encoder starts turning clockwise", [_DEV, _ENC], False, "clockwise = counts going up")
_ev("encoder_ccw", "Inputs", "Encoder starts turning anticlockwise", [_DEV, _ENC], False)
_ev("encoder_reversed", "Inputs", "Encoder changes direction", [_DEV, _ENC], False)
_ev("encoder_rpm_above", "Inputs", "Encoder speed rises above", [_DEV, _ENC, P("rpm", "number", 60, "RPM", True)],
    False, "revolutions per minute over the last second (the channel's counts_per_rev option, default 1024)")
_ev("encoder_rpm_below", "Inputs", "Encoder speed falls below", [_DEV, _ENC, P("rpm", "number", 10, "RPM", True)],
    False)
# --- speakers and analogue outputs
_ev("speaker_start", "Audio", "Speaker starts", [P("device", "audio", "", "Audio device", help="empty = any")])
_ev("speaker_stop", "Audio", "Speaker stops", [P("device", "audio", "", "Audio device", help="empty = any")])
_ev("sound_file_end", "Audio", "Sound file finished", [P("device", "audio", "", "Audio device", help="empty = any")],
    help="a sound file played to its end (not stopped)")
_ev("analog_output_changed", "Outputs", "Analogue output changes",
    [_DEV, P("channel", "output", "", "Output", help="empty = any")],
    help="the level of a PWM / analogue output changes; event_value = the new level")
# --- conditions over time (event wizard)
_ev("condition_held", "Logic", "Condition true for a time",
    [P("cond", "expr", "", "Condition", True), P("seconds", "number", 5, "For (s)", True)], False,
    "once each time the condition stays true this long")
_ev("value_changes_by", "Logic", "Value changes within a time",
    [P("expr", "expr", "", "Value", True), P("amount", "number", 10, "By at least", True),
     P("seconds", "number", 5, "Within (s)", True)], False,
    "the value rises or falls by at least this much within the time")
# --- recording and the computer
_ev("disk_space_low", "System", "Disk space low", help="the recording disk has less free space than the "
                                                       "warning level (1 GB by default)")
_ev("disk_full", "System", "Disk full", help="the recording stopped: no space left on the disk")
_ev("recording_error", "System", "Recording error", help="the video recording failed and stopped")

ACTION_SPECS: dict[str, dict] = {}


def _ac(name, group, label, params=(), help=""):
    ACTION_SPECS[name] = {"group": group, "label": label, "params": list(params), "help": help}


_OUTS = [_DEV, _OUT]
_ac("output_on", "Outputs", "Switch output on", _OUTS)
_ac("output_off", "Outputs", "Switch output off", _OUTS)
_ac("output_toggle", "Outputs", "Toggle output", _OUTS)
_ac("output_pulse", "Outputs", "Pulse output", _OUTS + [P("duration", "number", 0.5, "Duration (s)", True)])
_ac("output_set", "Outputs", "Set output level", _OUTS + [P("value", "number", 0.5, "Level (0–1)", True)])
_ac("output_volts", "Outputs", "Set analogue output (V)", _OUTS + [P("volts", "number", 2.5, "Level (V)", True)],
    "as in ANY-maze; the channel's max_v option is the voltage of level 1 (default 5 V)")
_ac("all_outputs_off", "Outputs", "Switch all outputs off", [P("device", "device", "", "Device", help="empty = all")])
_ac("pulse_train", "Outputs", "Pulse train (optogenetics)",
    _OUTS + [P("frequency", "number", 20, "Frequency (Hz)", True),
             P("pulse_width", "number", 5, "Pulse width (ms)", True),
             P("duration", "number", 1, "Duration (s)", True, "0 = until stopped")])
_ac("pulse_train_stop", "Outputs", "Stop pulse train", _OUTS)
_ac("sync_pulse", "Outputs", "Sync pulse (e-phys / imaging)", _OUTS + [P("width", "number", 10, "Width (ms)", True)])
_ac("pellet", "Operant", "Dispense pellet(s)",
    _OUTS + [P("count", "int", 1, "Pellets", True), P("pulse_width", "number", 50, "Pulse (ms)"),
             P("gap", "number", 0.5, "Gap between pellets (s)"),
             P("sensor", "input", "", "Pellet sensor", help="optional: an input that sees each pellet drop"),
             P("timeout", "number", 1.0, "Detection time (s)"), P("retries", "int", 2, "Retries")])
_ac("dipper", "Operant", "Present liquid dipper", _OUTS + [P("duration", "number", 5, "Duration (s)", True)])
_ac("liquid_drop", "Operant", "Deliver liquid drops (dripper)",
    _OUTS + [P("count", "int", 1, "Drops", True), P("pulse_width", "number", 30, "Valve open (ms)"),
             P("gap", "number", 0.3, "Gap between drops (s)")])
_ac("odour", "Operant", "Present odour",
    [_DEV, P("channel", "odour", "", "Olfactometer", True),
     P("odour", "text", "", "Odour", help="a name from the olfactometer's odours option; empty or none = no odour"),
     P("flow", "number", 0, "Air flow (l/min)", help="0 = unchanged")])
_ac("odour_off", "Operant", "Stop odour", [_DEV, P("channel", "odour", "", "Olfactometer", True)])
_ac("light_level", "Lights", "Set light level", _OUTS + [P("level", "number", 100, "Level (%)", True)])
_ac("light_ramp", "Lights", "Ramp light level",
    _OUTS + [P("level", "number", 100, "To level (%)", True), P("duration", "number", 10, "Over (s)", True),
             P("start", "number", None, "From level (%)", help="empty = the current level")])
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
_INTENSITY = P("intensity", "number", 0, "Intensity (mA)", help="0 = as set; needs the shocker's intensity option")
_ac("shock_on", "Shock", "Shock on", _OUTS + [P("max_duration", "number", 2, "Safety cut-off (s)", True),
                                              _INTENSITY])
_ac("shock_off", "Shock", "Shock off", _OUTS)
_ac("shock_pulse", "Shock", "Shock for a duration", _OUTS + [P("duration", "number", 1, "Duration (s)", True),
                                                             _INTENSITY])
_ac("shock_intensity", "Shock", "Set shock intensity", _OUTS + [P("intensity", "number", 0.3, "Intensity (mA)",
                                                                  True)])
_LEVEL = P("intensity", "number", None, "Intensity (%)", help="empty = unchanged; needs the intensity option")
_ac("opto_train", "Optogenetics", "Laser pulse train (duty cycle)",
    _OUTS + [P("frequency", "number", 20, "Frequency (Hz)", True), P("duty_cycle", "number", 10, "Duty cycle (%)",
                                                                       True),
             P("duration", "number", 1, "Duration (s)", True, "0 = until stopped"), _LEVEL])
_ac("opto_sequence", "Optogenetics", "Laser pulse sequence from a file",
    _OUTS + [P("file", "file", "", "File (CSV)", True, "one pulse per line: onset (s), duration (s)[, intensity %]"),
             P("repeat", "int", 1, "Repeat", help="0 = until stopped"), _LEVEL])
_ac("opto_intensity", "Optogenetics", "Set laser intensity", _OUTS + [P("intensity", "number", 50, "Intensity (%)",
                                                                       True)])
_ac("set_temperature", "Temperature", "Set temperature",
    [_DEV, _THERMO, P("target", "number", 37, "Target (°C)", True),
     P("ramp", "number", 0, "Ramp (°C/min)", help="0 = go to the target at once")])
_ac("temperature_off", "Temperature", "Temperature control off", [_DEV, _THERMO])
_ac("pump_infuse", "Pumps", "Infuse", [_DEV, _PUMP, P("rate", "number", 0.1, "Rate (ml/min)", True),
                                       P("volume", "number", 0, "Volume (ml)", help="0 = until stopped")])
_ac("pump_withdraw", "Pumps", "Withdraw", [_DEV, _PUMP, P("rate", "number", 0.1, "Rate (ml/min)", True),
                                           P("volume", "number", 0, "Volume (ml)", help="0 = until stopped")])
_ac("pump_stop", "Pumps", "Stop pump", [_DEV, _PUMP])
_ac("pump_syringe", "Pumps", "Select syringe", [_DEV, _PUMP, P("syringe", "text", "", "Syringe", True,
                                                               "a predefined syringe or an inner diameter in mm")])
_ac("tare_sensor", "Sensors", "Tare sensor (zero)", [_DEV, _SENSOR])
_ac("read_sensor", "Sensors", "Store sensor reading", [_DEV, _SENSOR, P("var", "var", "", "Store in", True)])
_ac("weigh_animal", "Sensors", "Weigh the animal (balance)",
    [P("device", "device", "", "Balance", help="empty = the first balance"), P("var", "var", "", "Store in")])
_ac("send_alert", "Communication", "Send alert (e-mail / SMS)", [P("text", "text", "", "Message", True)])
_AUD = P("device", "audio", "", "Audio device", help="empty = first audio device")
_ac("tone", "Audio", "Play tone", [_AUD, P("frequency", "number", 2000, "Frequency (Hz)", True),
                                   P("duration", "number", 1, "Duration (s)", True),
                                   P("volume", "number", 0.5, "Volume (0–1)")])
_ac("white_noise", "Audio", "Play white noise", [_AUD, P("duration", "number", 1, "Duration (s)", True),
                                                 P("volume", "number", 0.5, "Volume (0–1)")])
_ac("play_sound", "Audio", "Play sound file", [_AUD, P("file", "file", "", "File (WAV)", True),
                                               P("duration", "number", 0, "Duration (s)", help="for the I/O log"),
                                               P("volume", "number", 1.0, "Volume (0–1)"),
                                               P("repeat", "int", 1, "Play", help="times; 0 = until stopped")])
_ac("loop_sound", "Audio", "Play sound file repeatedly", [_AUD, P("file", "file", "", "File (WAV)", True),
                                                          P("volume", "number", 1.0, "Volume (0–1)")],
    "until a Stop sounds action or the end of the test")
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
_ac("end_test", "Test", "End the test",
    [P("reason", "text", "", "Reason", help="stored as the reason for the test end; empty = ended by procedure"),
     P("allow_continuation", "bool", False, "Allow the test to be continued")],
    "with “allow continuation” the test pauses: continuing it fires “Test continued”, stopping it ends it")
_ac("pause_test", "Test", "Pause the test")
_ac("resume_test", "Test", "Resume the test")
_ac("prevent_test_start", "Test", "Prevent test start",
    help="in a “test is waiting to start” handler: the test does not start (not even when asked to) until Allow test "
         "start")
_ac("allow_test_start", "Test", "Allow test start")
_ac("run_subprocedure", "Test", "Run sub-procedure", [P("procedure", "procedure", "", "Sub-procedure", True)],
    "starts it alongside this handler (the Call statement runs it in place and waits for it)")
_ac("enable_procedure", "Test", "Enable procedure", [P("procedure", "procedure", "", "Procedure", True)])
_ac("disable_procedure", "Test", "Disable procedure", [P("procedure", "procedure", "", "Procedure", True)])
_ac("show_stimulus", "Touch screen", "Show stimulus",
    [P("area", "area", "", "Area", True), P("image", "file", "", "Image", help="empty = shape"),
     P("shape", "choice:circle|square|triangle|star|cross|bars", "circle", "Shape"),
     P("color", "text", "#ffffff", "Colour")])
_ac("hide_stimulus", "Touch screen", "Hide stimulus", [P("area", "area", "", "Area", True)])
_ac("clear_screen", "Touch screen", "Clear screen")
# --- test control
_ac("schedule_test", "Test", "Schedule another test for this animal",
    [P("stage", "text", "", "Stage", help="empty = this test's stage"),
     P("apparatus", "text", "", "Apparatus", help="empty = this test's apparatus"),
     P("delay", "number", 0, "After (minutes)", help="when it is due, from now")],
    "the test is added to the experiment when this test is saved")
_ac("warning", "Test", "Generate a warning", [P("text", "text", "", "Message", True)],
    "shown in the test's warnings and log")
_ac("error", "Test", "Generate an error", [P("text", "text", "", "Message", True),
                                           P("stop", "bool", False, "End the test")],
    "reported as a procedure error")
# --- zones
_ac("set_zone_label", "Zones", "Set zone label", [_ZONE, P("label", "text", "", "Label", True)],
    "e.g. which object is novel; saved with the test (zone_labels)")
_ac("remove_zone_label", "Zones", "Remove zone label", [_ZONE])
_ac("move_zone", "Zones", "Set moveable zone location",
    [P("zone", "point", "", "Zone or point", True), P("x", "number", 0, "Centre x (px)", True),
     P("y", "number", 0, "Centre y (px)", True)],
    "moves the zone (or point) for the rest of the test; saved as the test's zone position")
# --- video recording
_ac("video_start", "Video", "Start video recording", help="starts again after a stop (a new file)")
_ac("video_stop", "Video", "Stop video recording")
_ac("video_pause", "Video", "Pause video recording")
_ac("video_unpause", "Video", "Resume video recording")
_ac("video_label", "Video", "Label the video recording",
    [P("text", "text", "", "Label", help="empty = remove the label"),
     P("duration", "number", 0, "For (s)", help="0 = until changed")], "burned into the recorded frames")
# --- display and messages
_ac("popup", "Communication", "Show a pop-up message", [P("text", "text", "", "Message", True),
                                                         P("title", "text", "Procedure", "Title")])
_ac("display_text", "Communication", "Output text on the display",
    [P("name", "name", "text", "Text name", True, "one text per name; output again to change it"),
     P("text", "text", "", "Text", True), P("x", "number", 10, "x (px)"), P("y", "number", 30, "y (px)"),
     P("color", "text", "#ffff00", "Colour")])
_ac("display_remove", "Communication", "Remove text from the display", [P("name", "name", "text", "Text name", True)])
_ac("display_clear", "Communication", "Clear the text on the display")
_ac("send_email", "Communication", "Send an e-mail",
    [P("text", "text", "", "Message", True), P("subject", "text", "mANY-MAZE", "Subject"),
     P("to", "text", "", "To", help="empty = the alert device's e-mail addresses")],
    "through the alert (e-mail / SMS) devices")
_ac("send_sms", "Communication", "Send an SMS",
    [P("text", "text", "", "Message", True), P("to", "text", "", "To", help="empty = the alert device's numbers")],
    "through the alert (e-mail / SMS) devices")
_ac("run_program", "Communication", "Run a program",
    [P("program", "file", "", "Program", True), P("arguments", "text", "", "Arguments", help="split like a shell")],
    "started in the background (no shell); the test does not wait for it")
_ac("plugin", "Communication", "Trigger a plug-in",
    [P("plugin", "plugin", "", "Plug-in", True), P("argument", "text", "", "Argument"),
     P("var", "var", "", "Store the result in")], "plug-ins: see manymaze.core.procedures.plugins")
# --- output and speaker settings
_ac("set_output_frequency", "Outputs", "Set output frequency",
    _OUTS + [P("frequency", "number", 10, "Frequency (Hz)", help="0 = steady (no pulses)")],
    "used when the output is switched on; a running output changes at once")
_ac("set_output_duty", "Outputs", "Set output duty cycle", _OUTS + [P("duty_cycle", "number", 50, "Duty cycle (%)",
                                                                        True)],
    "of the pulses when a frequency is set")
_ac("set_output_duration", "Outputs", "Set output on-duration",
    _OUTS + [P("duration", "number", 1, "Duration (s)", help="0 = until switched off")],
    "switching the output on then switches it off after this time")
_ac("set_volume", "Audio", "Set speaker volume", [_AUD, P("volume", "number", 1.0, "Volume (0–1)", True)],
    "scales the volume of the sounds played afterwards")

STATEMENT_TYPES = {
    "when": "When", "wait": "Wait", "if": "If", "repeat": "Repeat", "set": "Set", "do": "Do", "stop": "Stop",
    "comment": "Comment", "var": "Variable", "call": "Call sub-procedure", "label": "Label", "goto": "Go to",
    "resolution": "Set timer resolution",
}
CONTAINERS = ("when", "if", "repeat")
STOP_WHAT = {"handler": "Exit this handler", "loop": "Exit the loop", "procedure": "Stop this procedure",
             "all": "Stop all procedures", "test": "End the test", "return": "Return from the sub-procedure"}
WHEN_MODES = {"ignore": "Ignore while running", "restart": "Restart", "parallel": "Run in parallel"}
LOCAL_NAMES = ("event_time", "event_value", "event_name", "timed_out", "wait_event")
# how a variable declared "keep" is kept between tests: one value for the experiment (old projects: true), one per
# animal or one per apparatus
KEEP_SCOPES = {"experiment": "For the whole experiment", "animal": "Per animal", "apparatus": "Per apparatus"}
CONSTANTS = {"true": True, "false": False, "True": True, "False": False, "pi": math.pi, "e": math.e,
             "inf": math.inf, "nan": math.nan, "none": None, "None": None,
             "NA": math.nan}  # NA: ANY-maze's #N/A (undefined), see is_undefined()
SHOCK_MAX_S = 60.0  # hard cap on any continuous shock
STALL_S = 0.25  # a software pulse train later than this (frames stalled) is delayed instead of bursting
SAFETY_TASKS = ("shock", "audio")  # scheduled tasks that keep running in real time while the test is paused
STEP_BUDGET = 5000  # statements a thread may run per frame before yielding
MAX_CALL_DEPTH = 32  # sub-procedures calling sub-procedures
EPS = 1e-6
