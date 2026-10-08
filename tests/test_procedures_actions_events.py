"""Procedure actions and events of the ANY-maze parity audit (§13): end-test reasons and continuation, scheduled
tests, zone labels and positions, the video recorder, messages and display text, e-mail / SMS, warnings and
errors, programs and plug-ins, output and speaker settings; investigation, orientation, partial exits, body-part
positions, rearing, encoder direction / speed, speakers, analogue outputs, disk space and recording errors, and
the event-wizard triggers and options."""

import datetime as _dt
import errno
import os
import sys
import time
import wave

import cv2
import numpy as np
import pytest

from manymaze.core import live as live_mod
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.iodevices import DeviceManager
from manymaze.core.iodrivers import NotifyDevice
from manymaze.core.live import LiveOccupancy, LiveRearing, LiveSession, is_disk_full
from manymaze.core.measures import AnalysisSettings
from manymaze.core.procedures import (ACTION_SPECS, EVENT_SPECS, ProcedureEngine, describe_statement, plugins,
                                      spec_defaults, statement_fields, validate)
from manymaze.core.project import Project
from manymaze.core.session import END_USER, save_live_test
from manymaze.core.tracking import Detection, DetectionSettings

FPS = 25


def W(seconds):
    return {"type": "wait", "mode": "seconds", "seconds": seconds}


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def MARK(name):
    return DO("mark", name=name)


def proc(*stmts, name="P"):
    return {"name": name, "enabled": True, "statements": list(stmts)}


def run(procs, seconds, state_fn=None, fps=FPS, before=None, each=None, **kw):
    """Run an engine over `seconds` of frames; state_fn(t) -> state dict; before(eng) before the start; each(eng, t)
    before each frame."""
    eng = ProcedureEngine(procs, seed=1, **kw)
    if before:
        before(eng)
    eng.start(0.0)
    n = int(round(seconds * fps))
    for i in range(n + 1):
        t = i / fps
        if each:
            each(eng, t)
        eng.update_state(t, state_fn(t) if state_fn else {})
        if eng.stopped:
            break
    eng.stop(min(n, i) / fps)
    return eng


def marks(eng, name=None):
    return [round(m["t"], 3) for m in eng.marks if name is None or m["behaviour"] == name]


# ====================================================================== catalogue, validation, descriptions
NEW_EVENTS = ["test_continuation", "time_before_end", "random_interval", "time_of_day", "investigation_start",
              "investigation_end", "oriented_towards", "oriented_away", "hidden_partial_exit", "zone_not_entered",
              "position_changed", "rearing_start", "rearing_end", "encoder_cw", "encoder_ccw", "encoder_reversed",
              "encoder_rpm_above", "encoder_rpm_below", "speaker_start", "speaker_stop", "sound_file_end",
              "analog_output_changed", "condition_held", "value_changes_by", "disk_space_low", "disk_full",
              "recording_error"]
NEW_ACTIONS = ["schedule_test", "warning", "error", "set_zone_label", "remove_zone_label", "move_zone", "video_start",
               "video_stop", "video_pause", "video_unpause", "video_label", "popup", "display_text", "display_remove",
               "display_clear", "send_email", "send_sms", "run_program", "plugin", "set_output_frequency",
               "set_output_duty", "set_output_duration", "set_volume"]
FILL = {"zone": "Centre", "target": "Centre", "channel": "wheel", "cond": "time() > 1", "expr": "time()",
        "text": "hello", "label": "novel", "program": sys.executable, "plugin": "echo", "name": "t1"}


def test_catalogue_counts_and_every_new_spec_validates():
    assert len(EVENT_SPECS) == 90 and len(ACTION_SPECS) == 99
    assert set(NEW_EVENTS) <= set(EVENT_SPECS) and set(NEW_ACTIONS) <= set(ACTION_SPECS)
    assert [p["name"] for p in ACTION_SPECS["end_test"]["params"]] == ["reason", "allow_continuation"]
    plugins.register("echo", lambda arg, info: arg)
    try:
        stmts = []
        for name in NEW_EVENTS:
            st = WHEN(name, [MARK(name)], **spec_defaults(EVENT_SPECS[name]))
            for p in EVENT_SPECS[name]["params"]:
                if p["req"] and st.get(p["name"]) in (None, ""):
                    st[p["name"]] = FILL[p["name"]]
            stmts.append(st)
        for name in NEW_ACTIONS:
            st = DO(name, **spec_defaults(ACTION_SPECS[name]))
            for p in ACTION_SPECS[name]["params"]:
                if p["req"] and st.get(p["name"]) in (None, ""):
                    st[p["name"]] = FILL[p["name"]]
            stmts.append(WHEN("test_start", [st]))
            assert statement_fields(st) == ACTION_SPECS[name]["params"]
            assert describe_statement(st).startswith("Do: ")
        ctx = {"zones": ["Centre", "Arena"], "points": ["Feeder"]}
        assert validate([proc(*stmts)], ctx) == []
    finally:
        plugins.unregister("echo")


def test_validation_of_new_parameter_types_and_when_options():
    ctx = {"zones": ["Centre"], "points": ["Feeder"]}
    bad = [WHEN("time_of_day", [], clock="25:00"),
           WHEN("oriented_towards", [], target="Nowhere", angle=30),
           WHEN("oriented_towards", [], target="Feeder", angle=30),  # a point is fine
           WHEN("key_down", [], times="x"),
           WHEN("key_down", [], within=2),
           WHEN("key_down", [], times=3, within=0),
           WHEN("key_down", [], trials="1, 3-x")]
    issues = [m for _pi, _p, m in validate([proc(*bad)], ctx)]
    assert any("not a time of day" in m for m in issues)
    assert any("unknown zone or point 'Nowhere'" in m for m in issues)
    assert not any("Feeder" in m for m in issues)
    assert any("whole number" in m for m in issues)
    assert any("needs a number of times" in m for m in issues)
    assert any("above 0" in m for m in issues)
    assert any("not a list of trials" in m for m in issues)
    st = WHEN("key_down", [], key="a", times=3, within=2, trials="odd")
    assert "3 times within 2 s" in describe_statement(st) and "trials odd" in describe_statement(st)
    assert describe_statement(DO("end_test", reason="done", allow_continuation=True)) == \
        "Do: End the test — “done”, allow the test to be continued"


# ====================================================================== test control
def test_end_test_reason_and_continuation():
    ended = []
    eng = run([proc(W(0.5), DO("end_test", reason="criterion reached"))], 2, on_end=lambda: ended.append(1))
    assert ended == [1] and eng.end_reason == "criterion reached"

    # allowing continuation: "waiting for test end" — nothing pauses, the procedures keep running
    pending = []
    procs = [proc(W(0.5), DO("end_test", reason="check", allow_continuation=True), MARK("after")),
             proc(WHEN("test_continuation", [MARK("continued")]), WHEN("every", [MARK("tick")], interval=0.25),
                  name="C")]
    eng = ProcedureEngine(procs, on_end=lambda: ended.append(2))
    eng.on_end_pending = pending.append
    eng.start(0)
    for i in range(21):
        eng.update_state(i / FPS, {})
    assert pending == [0.52] and eng.awaiting_continuation and not eng.paused and not eng.ended
    assert eng.end_reason == "check" and eng.end_at == 0.52
    assert marks(eng, "after") == [] and marks(eng, "tick") == [0.28, 0.52, 0.76]
    assert eng.continue_test(0.8)
    assert marks(eng, "continued") == [0.8] and not eng.awaiting_continuation and eng.end_reason == ""
    assert eng.end_at is None and not eng.continue_test(0.9)
    for i in range(21, 400):  # continued: it does not end 10 s later
        eng.update_state(i / FPS, {})
    assert not eng.ended
    eng.stop(16)

    # not continued: the test ends 10 s after the action
    eng = run([proc(W(0.5), DO("end_test", reason="done", allow_continuation=True))], 12,
              on_end=lambda: ended.append(3))
    assert ended == [1, 3] and eng.end_reason == "done" and eng.end_at == 0.52
    assert any("not continued" in m for _t, m in eng.log_lines) and eng.t == pytest.approx(10.52)


def _frame(x=60, y=100, angle=0, length=36):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, y, angle, length=length)
    return np.dstack([img] * 3)


def _session(procs=(), **kw):
    app = kw.pop("app", None) or templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(app, DetectionSettings(background="frame"), procedures=list(procs), **kw)
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    return s


def test_live_end_reason_and_continuation():
    s = _session([proc(W(0.4), DO("end_test", reason="Criterion reached"))], duration_s=5)
    for i in range(40):
        s.process(_frame(60 + i), i / FPS)
    assert s.state == "finished" and s.end_reason == "Criterion reached"


def _continuation_session(**kw):
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "led", "kind": "output"}, {"name": "light", "kind": "output"}, {"name": "door", "kind": "input"}]}])
    procs = [proc(DO("output_on", channel="light"), W(0.4), DO("end_test", reason="Island", allow_continuation=True)),
             proc(WHEN("test_continuation", [DO("mark", name="Continued")]),
                  WHEN("time_reached", [DO("output_on", channel="led"), DO("mark", name="Late")], time=0.6),
                  name="C")]
    return _session(procs, devices=dm, experimenter_area_px=3000, **kw)


def _hand_frame():
    img = _frame(60)
    img[20:180, 100:190] = 30  # the experimenter's arm
    return img


def test_live_waiting_for_test_end_times_out_and_cuts_the_data():
    s = _continuation_session(duration_s=0)
    i = 0
    while s.state != "finished" and i < 400:
        s.process(_frame(60 + i % 50), i / FPS)
        if i == 15:
            assert s.waiting_end and s.state == "running" and not s.pause()  # tracking goes on; no pausing
        i += 1
    assert i - 1 == pytest.approx(10.4 * FPS, abs=2)  # ended 10 s after the action
    assert s.end_reason == "Island" and s.cols["t"][-1] == pytest.approx(0.4)
    assert [e["behaviour"] for e in s.events] == []  # "Late" (0.6 s) was after the end: discarded
    led = [e for e in s.io_events if e["channel"] == "led"]
    light = [(e["t"], e["value"]) for e in s.io_events if e["channel"] == "light"]
    assert led == [] and light == [(0.0, 1), (0.4, 0)]  # on at the end: logged off at the cut
    assert any("Waiting for test end" in m for _t, m in s.warnings)


def test_live_continue_test_by_button_and_by_control_input():
    s = _continuation_session(duration_s=0)
    for i in range(20):
        s.process(_frame(60 + i), i / FPS)
    assert s.waiting_end and s.continue_test() and not s.waiting_end
    for i in range(20, 400):
        s.process(_frame(60 + i % 50), i / FPS)
    assert s.state == "running" and [e["behaviour"] for e in s.events] == ["Late", "Continued"]  # nothing lost while waiting
    s.finish()
    assert s.end_reason == END_USER and s.cols["t"][-1] == pytest.approx(399 / FPS)  # no data lost

    s = _continuation_session(duration_s=0, control_input="box/door")
    for i in range(20):
        s.process(_frame(60 + i), i / FPS)
    s.engine.set_input("box", "door", 1)  # the test control switch closes
    s.process(_frame(80), 20 / FPS)
    assert not s.waiting_end and [e["behaviour"] for e in s.events] == ["Late", "Continued"]
    s.finish()


def test_live_waiting_for_test_end_stop_or_experimenter_in_view():
    s = _continuation_session(duration_s=0)
    for i in range(30):
        s.process(_frame(60 + i), i / FPS)
    s.finish()  # Stop: the procedure ended the test
    assert s.end_reason == "Island" and s.cols["t"][-1] == pytest.approx(0.4)

    s = _continuation_session(duration_s=0)
    for i in range(30):
        s.process(_frame(60 + i), i / FPS)
    s.process(_hand_frame(), 30 / FPS)  # the experimenter walks into view to take the animal out
    assert s.state == "finished" and s.end_reason == "Island" and s.cols["t"][-1] == pytest.approx(0.4)


def test_live_zone_actions_scheduled_tests_and_saving(tmp_path):
    p = Project()
    p.save(tmp_path / "x.mmaze")
    t = p.add_test("", "M1", stage="Training", trial=1)
    procs = [proc(W(0.2), DO("set_zone_label", zone="Corner 1", label="novel"),
                  DO("set_zone_label", zone="Corner 2", label="familiar"), DO("remove_zone_label", zone="Corner 2"),
                  DO("move_zone", zone="Centre", x=60, y=100), DO("schedule_test", delay=30),
                  DO("schedule_test", stage="Probe"))]
    s = _session(procs, duration_s=1)
    for i in range(30):
        s.process(_frame(60), i / FPS)
    assert s.state == "finished"
    assert s.apparatus.zone("Centre").shape.centroid() == pytest.approx((60, 100))
    assert s.stats.zone_time  # the moved zone is used from then on: the animal (at x = 60) is in it
    assert s.engine.zone_labels == {"Corner 1": "novel"}
    assert save_live_test(p, t, s)
    assert "Centre" in t.zone_overrides and t.variables["zone_labels"] == {"Corner 1": "novel"}
    new = [x for x in p.tests if x is not t]
    assert [(x.animal_id, x.stage, x.trial) for x in new] == [("M1", "Training", 2), ("M1", "Probe", 1)]
    assert "scheduled_for" in new[0].variables and "Probe" in p.stages


# ====================================================================== messages, warnings, programs, plug-ins
def test_messages_display_warnings_errors_and_callbacks():
    shown = []
    procs = [proc(DO("popup", text="Trial {1 + 1}", title="Note"), DO("display_text", name="a", text="Hi", x=5, y=6),
                  DO("display_text", name="b", text="There"), DO("display_remove", name="a"),
                  DO("warning", text="check the light"), W(0.2), DO("display_clear"),
                  DO("error", text="bad thing", stop=True), MARK("never"))]
    ended = []

    def setup(eng):
        eng.on_display = lambda cmd, prm: shown.append((cmd, dict(prm)))
    eng = run(procs, 1, before=setup, on_end=lambda: ended.append(1))
    assert [c for c, _ in shown] == ["popup", "text", "text", "remove", "clear"]
    assert shown[0][1] == {"text": "Trial 2", "title": "Note"}
    assert shown[1][1] == {"name": "a", "text": "Hi", "x": 5.0, "y": 6.0, "color": "#ffff00"}
    assert eng.display_texts == {} and eng.user_warnings == [(0.0, "check the light")]
    assert any("Error generated: bad thing" in e for e in eng.errors) and ended == [1]
    assert marks(eng, "never") == [] and eng.end_reason == "Error: bad thing"


def test_live_popups_warnings_and_display_texts():
    s = _session([proc(DO("popup", text="Go"), DO("display_text", name="n", text="Trial 1"),
                       DO("warning", text="Low light"))], duration_s=0.5)
    for i in range(5):
        s.process(_frame(), i / FPS)
    assert [p["text"] for p in s.take_popups()] == ["Go"] and s.take_popups() == []
    assert s.display_texts == {"n": {"text": "Trial 1", "x": 10.0, "y": 30.0, "color": "#ffff00"}}
    assert any(m == "Low light" for _t, m in s.warnings)
    img = live_mod.draw_display_texts(_frame(), s.display_texts)
    assert img.shape == (200, 200, 3) and not np.array_equal(img, _frame())
    s.finish()


def test_email_and_sms_through_the_alert_device():
    sent = []
    NotifyDevice.sender = lambda kind, cfg, to, subject, text: sent.append((kind, to, subject, text))
    try:
        dm = DeviceManager([{"name": "alerts", "type": "notify", "email_to": "lab@x.org", "sms_to": "+111"}])
        eng = run([proc(DO("send_email", text="Done {2*2}", subject="Run"), DO("send_sms", text="Hi"),
                        DO("send_email", text="Direct", to="me@y.org"))], 0.2, devices=dm,
                  context={"animal": "M7"})
        dm.device("alerts").wait()
        assert sorted(sent) == sorted([("email", "lab@x.org", "Run — M7", "Done 4"),
                                       ("sms", "+111", "mANY-MAZE — M7", "Hi"),
                                       ("email", "me@y.org", "mANY-MAZE — M7", "Direct")])
        assert not eng.errors
    finally:
        NotifyDevice.sender = None
    eng = run([proc(DO("send_sms", text="x"))], 0.1)
    assert any("no alert" in e for e in eng.errors)


def test_run_program_is_not_blocking_and_plugins(tmp_path):
    out = tmp_path / "ran.txt"
    code = f"import time, pathlib; time.sleep(0.3); pathlib.Path(r'{out}').write_text('ok')"
    t0 = time.monotonic()
    eng = run([proc(DO("run_program", program=sys.executable, arguments=f'-c "{code}"'),
                    DO("run_program", program="/no/such/program"))], 0.1)
    assert time.monotonic() - t0 < 0.3  # the test did not wait for the program
    assert len(eng.programs) == 1 and any("program not found" in e for e in eng.errors)
    eng.programs[0].wait(10)
    assert out.read_text() == "ok"

    calls = []

    def plug(arg, info):
        calls.append((arg, info["t"], info["context"].get("animal")))
        info["log"]("called")
        return len(arg)
    plugins.register("count", plug)
    try:
        eng = run([proc({"type": "var", "name": "n", "value": 0}), proc(
            W(0.2), DO("plugin", plugin="count", argument="abc{1}", var="n"), DO("plugin", plugin="nope"),
            name="Q")], 0.4, context={"animal": "A"})
        assert calls == [("abc1", 0.2, "A")] and eng.vars["n"] == 4
        assert any("count: called" in m for _t, m in eng.log_lines)
        assert any("no plug-in called 'nope'" in e for e in eng.errors)
        assert "count" in plugins.names()
    finally:
        plugins.unregister("count")


# ====================================================================== outputs and speakers
def test_output_settings_and_analog_output_event():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "led", "kind": "output"}, {"name": "dim", "kind": "pwm"}]}])
    procs = [proc(DO("set_output_frequency", channel="led", frequency=10), DO("set_output_duty", channel="led",
                                                                              duty_cycle=20),
                  DO("set_output_duration", channel="led", duration=0.5), DO("output_on", channel="led"),
                  W(1), DO("output_set", channel="dim", value=0.3), DO("output_set", channel="dim", value=0.3),
                  DO("output_set", channel="dim", value=0.7)),
             proc(WHEN("analog_output_changed", [MARK("aout")], channel="dim", mode="parallel"), name="A")]
    eng = run(procs, 1.5, devices=dm)
    ons = [e["t"] for e in eng.io_events if e["channel"] == "led" and e["value"] == 1]
    offs = [e["t"] for e in eng.io_events if e["channel"] == "led" and e["value"] == 0]
    assert ons == pytest.approx([0, 0.1, 0.2, 0.3, 0.4], abs=0.041)  # 10 Hz for 0.5 s
    assert offs[0] == pytest.approx(0.04, abs=0.041)  # 20 % duty cycle: 20 ms pulses (frame-timed)
    assert marks(eng, "aout") == [1.0, 1.0]  # 0.3 then 0.7 (setting 0.3 again is no change)


def _wav(path, seconds=0.3, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.zeros(int(seconds * rate), np.int16).tobytes())
    return str(path)


def test_speaker_events_sound_end_and_volume(tmp_path):
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    f = _wav(tmp_path / "s.wav")
    procs = [proc(DO("set_volume", volume=0.5), DO("tone", frequency=1000, duration=0.2, volume=0.8), W(0.4),
                  DO("play_sound", file=f), W(0.6), DO("loop_sound", file=f), W(0.2), DO("stop_sound")),
             proc(WHEN("speaker_start", [MARK("start")], mode="parallel"),
                  WHEN("speaker_stop", [MARK("stop")], mode="parallel"),
                  WHEN("sound_file_end", [MARK("end")], device="spk", mode="parallel"), name="E")]
    eng = run(procs, 1.5, devices=dm)
    assert marks(eng, "start") == [0, 0.4, 1.0]
    assert marks(eng, "stop") == [0.2, 0.72, 1.2]  # the file ends at 0.7 s: on the next frame
    assert marks(eng, "end") == [0.72]  # the file played to its end (the looped one was stopped)
    tone = [kw for cmd, kw in dm.device("spk").played if cmd == "tone"]
    assert tone[0]["volume"] == pytest.approx(0.4)  # 0.8 at a speaker volume of 0.5


# ====================================================================== animal events from the live state
def test_investigation_orientation_position_and_rearing_events():
    def state(t):
        return {"investigating": {"Obj": 0.5 <= t < 1.0}, "orientation": {"Feeder": 90 if t < 0.4 or t >= 1.2 else 10},
                "x": 50, "y": 50, "hx": 60 + (10 if t >= 0.8 else 0), "hy": 50, "rearing": 1.0 <= t < 1.4}
    procs = [proc(WHEN("investigation_start", [MARK("inv+")], zone="Obj"),
                  WHEN("investigation_end", [MARK("inv-")]),
                  WHEN("oriented_towards", [MARK("to")], target="Feeder", angle=30),
                  WHEN("oriented_away", [MARK("away")], target="Feeder", angle=30),
                  WHEN("position_changed", [MARK("head")], part="head", distance=5),
                  WHEN("position_changed", [MARK("centre")]),
                  WHEN("rearing_start", [MARK("rear+")]), WHEN("rearing_end", [MARK("rear-")]),
                  WHEN("condition_true", [MARK("cond")], cond="rearing() and rears() == 1"))]
    eng = run(procs, 2, state)
    assert marks(eng, "inv+") == [0.52] and marks(eng, "inv-") == [1.0]
    assert marks(eng, "to") == [0.4] and marks(eng, "away") == [1.2]
    assert marks(eng, "head") == [0.8] and marks(eng, "centre") == []
    assert marks(eng, "rear+") == [1.0] and marks(eng, "rear-") == [1.4] and marks(eng, "cond") == [1.0]
    assert eng.rear_count == 1 and eng.rear_time == pytest.approx(0.4, abs=0.05)


def test_live_occupancy_investigation_orientation_and_partial_exits():
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    app.zone("Corner 1").hidden = True
    occ = LiveOccupancy(app, AnalysisSettings())
    seen = []
    det = Detection(x=40, y=40, hx=35, hy=35, tx=50, ty=50, area=200, angle=-135.0, detected=True)
    lost = Detection()
    for i, d in enumerate([det, lost, lost, det, det, lost, det,
                           Detection(x=100, y=100, hx=105, hy=100, area=200, angle=0.0, detected=True), lost]):
        occ.update(d, i * 0.5)
        seen += [(i, name, a.get("zone"), a.get("value")) for name, a in occ.events]
        if i == 0:
            assert occ.investigating["Corner 1"] and not occ.investigating["Centre"]
            assert occ.orientation["Corner 1"] < 5 and occ.orientation["Corner 4"] > 175
    # seen again near the hidden corner (1.5 s) and hidden in it again (2.5 s): one partial exit of 1 s; the
    # second time the animal went to the centre before it was lost
    assert seen == [(5, "hidden_partial_exit", "Corner 1", 1.0)]


def test_live_rearing_from_the_body_area():
    r = LiveRearing(AnalysisSettings(min_rear_s=0.3), fps=10)
    states = []
    for i in range(60):
        t = i / 10
        small = 3.0 <= t < 4.0 or 5.0 <= t < 5.2  # a rear, then a dip too short to count
        states.append(r.update(t, Detection(x=0, y=0, area=100.0 if small else 200.0, detected=True)))
    on = [i / 10 for i in range(1, 60) if states[i] and not states[i - 1]]
    off = [i / 10 for i in range(1, 60) if states[i - 1] and not states[i]]
    assert on == [3.3] and off == [4.2]


def test_live_session_feeds_rearing_and_partial_exit_events():
    s = _session([proc(WHEN("rearing_start", [MARK("Rear")]))], duration_s=0, fps=FPS)
    for i in range(int(2.5 * FPS)):
        t = i / FPS
        s.process(_frame(80, length=18 if 1.5 <= t < 2.2 else 36), t)
    assert [round(e["t"], 1) for e in s.events if e["behaviour"] == "Rear"] == [1.8]
    s.finish()


# ====================================================================== encoders
def test_encoder_direction_reversal_and_rpm():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "wheel", "kind": "encoder", "counts_per_rev": 100}]}])
    procs = [proc(WHEN("encoder_cw", [MARK("cw")], channel="wheel"),
                  WHEN("encoder_ccw", [MARK("ccw")], channel="wheel"),
                  WHEN("encoder_reversed", [MARK("rev")], channel="wheel"),
                  WHEN("encoder_rpm_above", [MARK("fast")], channel="wheel", rpm=100),
                  WHEN("encoder_rpm_below", [MARK("slow")], channel="wheel", rpm=10))]
    count = [0.0]

    def turn(eng, t):  # 200 counts/s (120 rpm) clockwise for 1 s, still, then 100 counts/s anticlockwise
        count[0] += (8 if 0.04 <= t <= 1.0 else -4 if 2.0 <= t <= 3.0 else 0)
        eng.set_input("box", "wheel", count[0])
    eng = run(procs, 4, each=turn, devices=dm)
    # the first count is seen at 0.04 s: turning is known from the next one
    assert marks(eng, "cw") == [0.08] and marks(eng, "ccw") == [2.0] and marks(eng, "rev") == [2.0]
    assert marks(eng, "fast") == [0.28]  # the speed over at least 0.2 s of samples: 120 rpm
    assert marks(eng, "slow") == [1.92, 3.84]  # the speed over the last second falls after each run


# ====================================================================== event-wizard triggers and options
def test_wizard_time_triggers():
    eng = run([proc(WHEN("time_before_end", [MARK("end-2")], seconds=2),
                    WHEN("random_interval", [MARK("rnd")], min=0.5, max=0.5, mode="parallel"))], 3,
              context={"duration_s": 3})
    assert marks(eng, "end-2") == [1.0] and marks(eng, "rnd") == [0.52, 1.0, 1.52, 2.0, 2.52, 3.0]
    eng = run([proc(WHEN("time_before_end", [MARK("x")], seconds=2))], 1)
    assert marks(eng) == []  # no test duration known

    clock = [_dt.datetime(2026, 1, 1, 9, 59, 59)]

    def setup(eng):
        eng.wall_clock = lambda: clock[0]

    def tick(eng, t):
        clock[0] = _dt.datetime(2026, 1, 1, 9, 59, 59) + _dt.timedelta(seconds=t)
    eng = run([proc(WHEN("time_of_day", [MARK("ten")], clock="10:00"))], 3, before=setup, each=tick)
    assert marks(eng, "ten") == [1.0]


def test_wizard_zone_not_entered_held_and_change_within():
    def state(t):
        return {"zones": {"A": 2.0 <= t < 2.5}}
    procs = [proc(WHEN("zone_not_entered", [MARK("absent")], zone="A", seconds=1, mode="parallel"),
                  WHEN("condition_held", [MARK("held")], cond="time() > 1", seconds=0.5),
                  WHEN("value_changes_by", [MARK("jump")], expr="10 if zone('A') else 0", amount=5, seconds=1,
                       mode="parallel"))]
    eng = run(procs, 4, state)
    assert marks(eng, "absent") == [1.0, 3.52]  # left the zone on the frame at 2.52 s
    assert marks(eng, "held") == [1.56]  # true from 1.04 s: 1.54 s is seen on the next frame
    assert marks(eng, "jump") == [2.0, 2.52]


def test_wizard_times_within_and_trials():
    procs = [proc(WHEN("key_down", [MARK("3 in 1 s")], key="a", times=3, within=1, mode="parallel"),
                  WHEN("key_down", [MARK("every 2nd")], key="a", times=2, mode="parallel"),
                  WHEN("key_down", [MARK("odd trials")], key="a", trials="odd", mode="parallel"),
                  WHEN("key_down", [MARK("trial 2-3")], key="a", trials="2-3", mode="parallel"))]
    eng = ProcedureEngine(procs, context={"trial": 2})
    eng.start(0)
    for t in (0.1, 0.5, 2.0, 2.2, 2.4, 2.6):
        eng.update_state(t, {})
        eng.key(t, "a")
        eng.key(t, "a", down=False)
    eng.stop(3)
    assert marks(eng, "3 in 1 s") == [2.4]  # 0.1, 0.5 then 2.0: too far apart
    assert marks(eng, "every 2nd") == [0.5, 2.2, 2.6]
    assert marks(eng, "odd trials") == [] and len(marks(eng, "trial 2-3")) == 6


# ====================================================================== the video recorder and the disk
def _count_frames(path):
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def test_video_recorder_actions(tmp_path):
    path = tmp_path / "rec.mp4"
    procs = [proc(W(0.4), DO("video_pause"), W(0.4), DO("video_unpause"), DO("video_label", text="Tone", duration=0.2),
                  W(0.4), DO("video_stop"), W(0.2), DO("video_start"))]
    s = _session(procs, duration_s=2, record_path=str(path), fps=FPS)
    for i in range(int(2 * FPS) + 1):
        s.process(_frame(60 + i), i / FPS)
    assert s.state == "finished"
    assert s.record_parts == [str(path), str(tmp_path / "rec_part2.mp4")]
    assert _count_frames(path) == pytest.approx(20, abs=2)  # 0–0.4 s and 0.8–1.2 s (paused 0.4–0.8 s)
    assert _count_frames(tmp_path / "rec_part2.mp4") == pytest.approx(15, abs=2)  # 1.4–2.0 s
    # the label is a marker at its time in the video file (0.4 s recorded before it: paused 0.4–0.8 s)
    assert s.video_labels == [{"t": 0.8, "video_t": pytest.approx(0.4, abs=0.05), "text": "Tone",
                               "file": "rec.mp4"}]
    assert [m for _t, m in s.recording_log] == ["recording paused", "recording resumed", "label “Tone”",
                                                "recording stopped", "recording started"]

    s = _session([proc(DO("video_start"))], duration_s=0.2, fps=FPS)  # not set to record
    for i in range(6):
        s.process(_frame(), i / FPS)
    assert any("not set to record video" in m for _t, m in s.warnings)


def test_recording_starts_only_from_the_procedure(tmp_path):
    path = tmp_path / "late.mp4"
    s = _session([proc(W(1), DO("video_start"))], duration_s=2, record_path=str(path), fps=FPS,
                 record_from_start=False)
    for i in range(int(2 * FPS) + 1):
        s.process(_frame(60 + i), i / FPS)
    assert _count_frames(path) == pytest.approx(26, abs=2)


def test_disk_space_events(tmp_path, monkeypatch):
    free = [5000.0]
    monkeypatch.setattr(live_mod, "disk_free_mb", lambda path: free[0])
    procs = [proc(WHEN("disk_space_low", [MARK("low")]), WHEN("disk_full", [MARK("full")]),
                  WHEN("recording_error", [MARK("rec error")]))]
    s = _session(procs, duration_s=0, record_path=str(tmp_path / "d.mp4"), fps=FPS, disk_check_s=0.2)
    for i in range(60):
        free[0] = 5000.0 if i < 10 else 800.0 if i < 30 else 20.0
        s.process(_frame(60 + i), i / FPS)
    got = {e["behaviour"]: round(e["t"], 2) for e in s.events}
    assert set(got) == {"low", "full", "rec error"} and got["low"] < got["full"] == got["rec error"]
    assert s.recorder is None and any("Disk full" in m for _t, m in s.warnings)
    s.finish()


def test_disk_full_errors_are_recognised(tmp_path):
    assert is_disk_full(OSError(errno.ENOSPC, "No space left on device"))
    assert is_disk_full(RuntimeError("ffmpeg: No space left on device"))
    assert not is_disk_full(RuntimeError("codec not found"))
    assert live_mod.disk_free_mb(str(tmp_path / "not" / "yet" / "there.mp4")) > 0


def test_recording_error_event(tmp_path):
    s = _session([proc(WHEN("recording_error", [MARK("err")]))], duration_s=0, record_path=str(tmp_path / "r.mp4"),
                 fps=FPS)
    for i in range(5):
        s.process(_frame(), i / FPS)

    class Broken:
        def write(self, frame):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            pass
    s.recorder = Broken()
    s.process(_frame(), 5 / FPS)
    assert [e["behaviour"] for e in s.events] == ["err"] and s.recorder is None
    s.finish()


# ====================================================================== the procedure editor
def test_editor_shows_new_parameters_and_when_options():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox

    from manymaze.gui.procedure_editor import ProcedureEditor

    app = QApplication.instance() or QApplication([])
    p = Project()
    p.apparatus = [templates.build("open_field", 0, 0, 400, 400, size_cm=40)]
    p.procedures = [proc(WHEN("oriented_towards", [DO("end_test", reason="x", allow_continuation=True)],
                              target="Centre", angle=30, times=2, within=5))]
    ed = ProcedureEditor(p)
    ed.tree.setCurrentItem(ed.tree.item_for((0,)))
    app.processEvents()
    w = ed._form_widgets
    assert {"target", "angle", "times", "within", "trials"} <= set(w)
    assert "Centre" in [w["target"].itemText(i) for i in range(w["target"].count())]
    w["trials"].setText("odd")
    w["trials"].editingFinished.emit()
    assert ed.procedures()[0]["statements"][0]["trials"] == "odd"
    ed.tree.setCurrentItem(ed.tree.item_for((0, "body", 0)))
    app.processEvents()
    assert isinstance(ed._form_widgets["allow_continuation"], QCheckBox)
    assert ed._form_widgets["allow_continuation"].isChecked()
    ed.close()
