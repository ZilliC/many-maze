"""Input/output only mode, the follow-ups of phase 1: several I/O-only tests at once in a LiveGroup (operant
chambers side by side), the check of procedures that need the animal, and the review of a test's I/O log (timeline,
list, panel text). Devices are virtual ones."""

import datetime as dt
import time

from manymaze.core import ioconfig
from manymaze.core.iodevices import DeviceManager, DeviceView
from manymaze.core.iolog import review_channels, review_rows
from manymaze.core.live import IOSession
from manymaze.core.livegroup import LiveGroup, has_data
from manymaze.core.livemonitor import io_panel_lines
from manymaze.core.plots import io_timeline
from manymaze.core.procedures import check_before_test, project_context, validate
from manymaze.core.procedures.validate import animal_functions
from manymaze.core.project import Project
from manymaze.core.session import END_DURATION, END_ERROR, save_live_test


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


# every lever press gives a pellet (the procedures name chamber1's channels: in a test they mean its own chamber)
FR1 = {"name": "FR1", "enabled": True, "statements": [
    {"type": "var", "name": "presses", "value": 0, "result": True},
    WHEN("input_on", [{"type": "set", "var": "presses", "value": "presses + 1"},
                      DO("output_on", device="chamber1", channel="pellet"), {"type": "wait", "seconds": 0.2},
                      DO("output_off", device="chamber1", channel="pellet")], device="chamber1", channel="lever")]}


def chambers(n=2):
    return DeviceManager(ioconfig.operant_devices("coulbourn", "virtual", n))


def session(dm, box, **kw):
    kw = {"duration_s": 3.0, "start_mode": "manual", **kw}
    return IOSession(procedures=[FR1], devices=DeviceView(dm, box), **kw)


def tick_all(group, t):
    for e in group.entries:
        if e.session is not None:
            e.session.tick(t)


def test_a_group_runs_tests_without_a_camera():
    dm = chambers()
    g = LiveGroup(io_clock=False)  # the test ticks the sessions itself
    e1 = g.add_entry(None, None, "R1 · chamber1", {"device": "chamber1"})
    e2 = g.add_entry(None, None, "R2 · chamber2", {"device": "chamber2"})
    g.arm(e1, session(dm, "chamber1"))
    g.arm(e2, session(dm, "chamber2"))
    assert e1.source_key is None and g.entries_for(None) == [e1, e2] and not g.runners
    tick_all(g, 0.0)
    assert [e.state for e in g.entries] == ["waiting", "waiting"]
    assert g.key("Space") == "start"  # the start key starts every test, as with cameras
    tick_all(g, 0.1)
    assert [e.state for e in g.entries] == ["running", "running"]
    dm.set_input("chamber1", "lever", 1)  # a press in chamber 1 only
    tick_all(g, 0.5)
    dm.set_input("chamber1", "lever", 0)
    tick_all(g, 0.6)
    g.pause_all()
    assert [e.state for e in g.entries] == ["paused", "paused"]
    tick_all(g, 5.0)  # the test clocks stop while paused …
    g.resume_all()
    t = 5.0
    while any(e.state != "finished" for e in g.entries) and t < 20:
        t = round(t + 0.01, 6)
        tick_all(g, t)
    assert [e.session.end_reason for e in g.entries] == [END_DURATION, END_DURATION]
    assert e1.session.result_variables == {"presses": 1} and e2.session.result_variables == {"presses": 0}
    assert [(x["device"], x["channel"]) for x in e1.session.io_events if x["kind"] == "output"] == [
        ("chamber1", "pellet"), ("chamber1", "pellet")]
    assert not [x for x in e2.session.io_events if x["kind"] == "output"]
    assert e1.session.pauses and g.finished_unsaved() == [e1, e2]
    dm.close()


def test_a_scheduled_start_and_stop_all():
    dm = chambers()
    now = [dt.datetime(2026, 10, 9, 4, 59)]
    g = LiveGroup(clock=lambda: now[0], io_clock=False)
    es = [g.add_session(None, session(dm, f"chamber{i}", duration_s=0.0), meta={"device": f"chamber{i}"})
          for i in (1, 2)]
    g.schedule("05:00", entry_ids=[es[0].id])  # only the first chamber at 05:00
    tick_all(g, 0.0)
    assert g.tick() == []
    now[0] = dt.datetime(2026, 10, 9, 5, 0, 1)
    assert len(g.tick()) == 1
    tick_all(g, 0.1)
    assert [e.state for e in es] == ["running", "waiting"]
    g.start(es[1])
    tick_all(g, 0.2)
    tick_all(g, 1.0)
    g.stop_all()
    assert [e.state for e in es] == ["finished", "finished"] and not any(e.aborted for e in es)
    assert all(has_data(e.session) for e in es) and [e.elapsed for e in es] == [0.9, 0.8]
    dm.close()


def test_a_stopped_test_without_a_camera_is_saved(tmp_path):
    dm = chambers()
    g = LiveGroup(io_clock=False)
    e = g.add_session(None, session(dm, "chamber1", duration_s=0.0, start_mode="immediate"))
    tick_all(g, 0.0)
    dm.set_input("chamber1", "lever", 1)
    tick_all(g, 0.4)
    tick_all(g, 1.0)
    g.stop(e)
    p = Project(name="Chambers")
    p.save(tmp_path / "c.mmaze")
    t = p.add_test("", "R1", "")
    assert not e.aborted and save_live_test(p, t, e.session)
    assert t.status == "scored" and t.duration_s == 1.0 and t.result_variables == {"presses": 1}
    dm.close()


def test_the_group_runs_the_clock_of_tests_without_a_camera():
    dm = chambers()
    g = LiveGroup()
    e = g.add_entry(None, meta={"device": "chamber1"})
    g.arm(e, session(dm, "chamber1", duration_s=0.3, start_mode="immediate"))
    t0 = time.monotonic()
    while e.state != "finished" and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    assert e.state == "finished" and e.session.end_reason == END_DURATION and e.elapsed == 0.3
    g.close()
    dm.close()


def test_an_error_keeps_what_a_test_without_a_camera_recorded():
    g = LiveGroup(io_clock=False)
    s = IOSession(duration_s=0.0, start_mode="immediate")
    e = g.add_session(None, s)
    s.tick(0.0)
    s.tick(0.5)
    g._session_failed(e, RuntimeError("boom"))
    assert s.end_reason == END_ERROR and not e.aborted  # it ran: saved with what it has
    w = IOSession(duration_s=0.0, start_mode="manual")
    e2 = g.add_session(None, w)
    w.tick(0.0)
    g._session_failed(e2, RuntimeError("boom"))
    assert e2.aborted


# ---------------------------------------------------------------------------- procedures that need a camera
def test_procedures_that_need_the_animal_are_refused_in_input_output_only_mode():
    p = Project(name="Operant")
    p.io_devices = ioconfig.operant_devices("med_associates", "virtual")
    procs = [{"name": "A", "statements": [
        {"type": "var", "name": "v", "value": "speed()"},
        WHEN("zone_enter", [DO("video_start")], zone=""),
        WHEN("input_on", [
            {"type": "if", "cond": "zone('Centre') or x() > 3", "body": [DO("log", text="at {distance()}")]},
            {"type": "wait", "mode": "event", "event": "freezing_start", "or": [{"event": "zone_exit"}]},
            DO("output_on", channel="pellet")], channel="left_lever")]}]
    assert check_before_test(procs, project_context(p)) == []  # video tracking: all fine
    p.settings_extra["mode"] = "io_only"
    ctx = project_context(p)
    assert ctx["io_only"] is True
    errors = check_before_test(procs, ctx)
    assert len(errors) == 8 and all("Input/output only" in e for e in errors)
    text = "\n".join(errors)
    for what in ("Variable: initial value: speed()", "When: “Animal enters zone” needs the animal tracked by a "
                 "camera", "Do start video recording: there is no camera to record", "If: Condition: zone()",
                 "If: Condition: x()", "Message: distance()", "Wait: “Freezing starts”",
                 "Wait: or event 1: “Animal leaves zone”"):
        assert what in text, what
    # inputs, outputs, keys, timers and the test's own events are fine
    ok = [{"name": "B", "statements": [
        WHEN("test_start", [DO("light_on", channel="house_light")]),
        WHEN("input_count_reaches", [DO("pellet", channel="pellet", count=1)], channel="head_entry", count=3),
        WHEN("key_down", [DO("mark", name="note")], key="m"),
        {"type": "if", "cond": "time() > 10 and activations('left_lever') > 2", "body": [DO("end_test")]}]}]
    assert check_before_test(ok, ctx) == []
    assert [m for _pi, _path, m in validate(procs, {"io_only": False})] == []
    assert animal_functions("zone('A') + zone('B') * head_x() + max(1, 2)") == ["zone", "head_x"]
    assert animal_functions("((") == []


# ---------------------------------------------------------------------------- reviewing the I/O log
IO = [{"t": 0.1, "device": "box", "channel": "force", "kind": "input", "value": 3.5, "type": "analog"},
      {"t": 0.2, "device": "box", "channel": "force", "kind": "input", "value": 4.5, "type": "analog"},
      {"t": 0.5, "device": "box", "channel": "lever", "kind": "input", "value": 1, "type": "digital"},
      {"t": 0.6, "device": "box", "channel": "lever", "kind": "input", "value": 0, "type": "digital"},
      {"t": 0.6, "device": "box", "channel": "pellet", "kind": "output", "value": 1, "type": "digital"},
      {"t": 0.6, "device": "box", "channel": "pellet", "kind": "output", "value": 0, "type": "digital"},
      {"t": 0.7, "device": "procedure", "channel": "presses", "kind": "variable", "value": 1},
      {"t": 1.0, "device": "box", "channel": "light", "kind": "output", "value": 1, "type": "digital"},
      {"t": 1.0, "device": "box2", "channel": "lever", "kind": "input", "value": "on"}]
EVENTS = [{"behaviour": "Grooming", "t": 0.2, "t_end": 0.9}, {"behaviour": "go", "t": 0.3, "t_end": None}]


def test_review_channels_and_rows():
    chans = review_channels(IO, EVENTS, end=2.0)
    by = {c["label"]: c for c in chans}
    assert [c["label"] for c in chans] == ["force", "box/lever", "box2/lever", "pellet", "light", "Grooming", "go"]
    assert by["box/lever"]["spans"] == [(0.5, 0.6)] and by["box2/lever"]["spans"] == [(1.0, 2.0)]
    assert by["force"]["values"] == [(0.1, 3.5), (0.2, 4.5)] and by["pellet"]["points"] == [0.6]
    assert by["light"]["spans"] == [(1.0, 2.0)] and by["light"]["kind"] == "output"
    assert by["Grooming"]["spans"] == [(0.2, 0.9)] and by["go"]["points"] == [0.3] and by["go"]["kind"] == "event"
    rows, more = review_rows(IO, EVENTS)
    assert more == 0 and rows[:3] == [(0.2, "Event", "Grooming", "0.70 s"), (0.3, "Event", "go", ""),
                                      (0.5, "Input", "box/lever", "on")]
    assert (0.7, "Variable", "presses", "1") in rows and (0.6, "Output", "pellet", "off") in rows
    assert not [r for r in rows if r[2] == "force"]  # analogue samples: in the timeline only
    rows, more = review_rows(IO, EVENTS, limit=3)
    assert len(rows) == 3 and more == 6
    fig = io_timeline(chans, 2.0)
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_yticklabels()][::-1] == [c["label"] for c in chans]
    assert ax.get_xlim() == (0.0, 2.0)
    assert io_timeline([], 1.0).axes[0].texts  # "No inputs, outputs or events…"


def test_panel_lines_of_a_test_without_a_camera():
    status = [("chamber1", "lever", "input", 1), ("chamber1", "force", "analog", 2.5),
              ("chamber1", "pellet", "output", 0), ("chamber1", "pellet.errors", "output", 0),
              ("chamber1", "house_light", "output", 1)]
    inputs = [("lever", "on", 3, 1.2, 0.4)]
    assert io_panel_lines(status, inputs) == ["Inputs: lever ON (3×) · force 2.5",
                                              "Outputs: pellet off · house_light ON"]
    two = io_panel_lines([("a", "lever", "input", 0), ("b", "lever", "input", 1)], [])
    assert two == ["Inputs: a/lever off (0×) · b/lever ON (0×)"]
    assert io_panel_lines([], []) == []
