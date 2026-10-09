"""Input/output only mode (ANY-maze's "Input/output only mode", e.g. operant chambers): live tests without a camera,
run by the procedures and the I/O devices on the computer's clock, stored without a track and analysed from their
I/O log. Devices are the virtual (simulated) ones."""

import json
import math
import time

import pytest

from manymaze.core.autosave import recover as recover_autosaves
from manymaze.core.calculations import Calculation
from manymaze.core.iodevices import DeviceManager
from manymaze.core.live import IOSession
from manymaze.core.measures import io_only_measures
from manymaze.core.project import Behaviour, Project
from manymaze.core.session import END_DURATION, END_PROCEDURE, END_USER, save_live_test
from manymaze.core.workflow import data_status


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def proc(*stmts, name="P"):
    return {"name": name, "enabled": True, "statements": list(stmts)}


# a fixed-ratio 1 chamber: every lever press gives a pellet (the output on for 0.2 s)
FR1 = proc({"type": "var", "name": "presses", "value": 0, "result": True},
           WHEN("input_on", [{"type": "set", "var": "presses", "value": "presses + 1"},
                             DO("output_on", device="box", channel="pellet"), {"type": "wait", "seconds": 0.2},
                             DO("output_off", device="box", channel="pellet")], device="box", channel="lever"))


def box():
    return DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"}]}])


def run(s, until=10.0, presses=(), step=0.01):
    """Tick the session (clock times t) until it ends; lever presses of 0.1 s at the given clock times."""
    t = 0.0
    while s.state != "finished" and t <= until + 1e-9:
        for p in presses:
            if abs(t - p) < step / 2:
                s.devices.set_input("box", "lever", 1)
            if abs(t - p - 0.1) < step / 2:
                s.devices.set_input("box", "lever", 0)
        s.tick(t)
        t = round(t + step, 6)
    return s


def test_procedures_and_devices_run_on_the_clock():
    dm = box()
    s = run(IOSession(duration_s=3.0, start_mode="immediate", procedures=[FR1], devices=dm), presses=(1.0, 2.0))
    assert s.state == "finished" and s.end_reason == END_DURATION and s.elapsed == 3.0
    assert s.result_variables == {"presses": 2}
    pellets = [(e["t"], e["value"]) for e in s.io_events if e["channel"] == "pellet"]
    assert pellets == [(1.0, 1), (1.2, 0), (2.0, 1), (2.2, 0)]
    assert s.track() is None and s.trail(10) == [] and s.has_data and s.io_only
    assert not s.warnings and not s.engine.errors
    dm.close()


def test_start_modes_pause_and_end_by_procedure():
    # manual: armed until a start key / the Start button; the "test is waiting to start" procedures run before it
    pre = proc(WHEN("test_waiting", [DO("prevent_test_start"), {"type": "wait", "mode": "event", "event": "input_on",
                                                                 "device": "box", "channel": "lever"},
                                     DO("allow_test_start")]))
    dm = box()
    s = IOSession(duration_s=0.0, start_mode="immediate", procedures=[pre], devices=dm)
    for i in range(50):
        s.tick(i * 0.01)
    assert s.state == "waiting" and not s.has_data  # held by the pre-test section
    dm.set_input("box", "lever", 1)
    s.tick(0.5)
    s.tick(0.51)
    assert s.state == "running"
    s.finish()
    assert s.end_reason == END_USER
    dm.close()

    s = IOSession(duration_s=5.0, start_mode="manual", procedures=[proc({"type": "wait", "seconds": 2},
                                                                         DO("end_test"))])
    for i in range(20):
        s.tick(i * 0.01)
    assert s.state == "waiting"
    s.request_start()
    s.tick(0.2)
    assert s.state == "running" and s.elapsed == 0.0
    s.tick(1.2)
    assert s.pause() and s.state == "paused"
    s.tick(4.0)  # the test clock stops while paused …
    assert s.resume()
    s.tick(4.01)
    assert s.elapsed == pytest.approx(1.0, abs=1e-6)  # … and goes on from where it was
    t = 4.01
    while s.state != "finished" and t < 10:
        t = round(t + 0.01, 6)
        s.tick(t)
    assert s.end_reason == END_PROCEDURE and s.elapsed == pytest.approx(2.0, abs=0.02)
    assert s.pauses == [[1.0, 1.0]] and s.pause_log[0]["t"] == 1.0


def test_clock_thread_runs_the_test():
    s = IOSession(duration_s=0.3, start_mode="immediate", procedures=[proc(DO("mark", name="go"))])
    s.start_clock()
    t0 = time.monotonic()
    while s.state != "finished" and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    assert s.state == "finished" and s.elapsed == 0.3 and [e["behaviour"] for e in s.events] == ["go"]


def _project(tmp_path) -> Project:
    p = Project(name="Operant", test_duration_s=3.0)
    p.save(tmp_path / "op.mmaze")
    p.settings_extra["mode"] = "io_only"
    p.io_devices = [{"name": "box", "type": "virtual", "channels": [{"name": "lever", "kind": "input"},
                                                                    {"name": "pellet", "kind": "output"}]}]
    return p


def test_saved_without_a_track_and_analysed_from_the_i_o_log(tmp_path):
    p = _project(tmp_path)
    test = p.add_test("", "R1", "")
    s = run(IOSession(duration_s=3.0, start_mode="immediate", procedures=[FR1], devices=box()),
            presses=(0.5, 1.0, 2.5))
    assert save_live_test(p, test, s)
    assert test.status == "scored" and not p.has_track(test) and data_status(p, test) == "scored"
    assert test.result_variables == {"presses": 3} and p.has_results(test)
    [row] = p.analyse_test(test)
    assert row["Test duration (s)"] == 3.0 and row["Variable: presses"] == 3
    assert row["lever: activations"] == 3 and row["lever: latency to first activation (s)"] == 0.5
    assert row["pellet: times on"] == 3 and row["pellet: time on (s)"] == pytest.approx(0.6)
    # time periods: bins and a period anchored on the lever (no track needed)
    p.analysis.bin_length_s = 1.0
    p.analysis.event_periods = [{"label": "After 1st press", "anchor": "input", "channel": "lever",
                                 "duration_s": 1.0}]
    rows = {r["Period"]: r for r in p.analyse_test(test, segmented=True)}
    assert list(rows) == ["Whole test", "0-1 s", "1-2 s", "2-3 s", "After 1st press"]
    assert [rows[k]["lever: activations"] for k in ("0-1 s", "1-2 s", "2-3 s")] == [1, 1, 1]
    assert rows["After 1st press"]["lever: activations"] == 2 and rows["1-2 s"]["Segment of test"] == 2
    assert rows["Whole test"]["Segment of test"] == ""
    # calculations, also for part of the test
    p.calculations = [Calculation("Rate", "60 * {lever: activations} / {Test duration (s)}", 1),
                      Calculation("Late", "result_for_period({lever: activations}, 2, 3)", 0)]
    [row] = p.analyse_test(test)
    assert row["Rate"] == 60.0 and row["Late"] == 1


def test_until_stopped_keeps_how_long_it_ran(tmp_path):
    p = _project(tmp_path)
    p.test_duration_s = 0.0
    test = p.add_test("", "R1", "")
    s = IOSession(duration_s=0.0, start_mode="immediate", procedures=[FR1], devices=box())
    run(s, until=1.5, presses=(0.5,))
    s.finish()
    assert save_live_test(p, test, s) and test.duration_s == 1.5
    assert p.analyse_test(test)[0]["Test duration (s)"] == 1.5
    # a test that never started has nothing to save
    s = IOSession(duration_s=0.0, start_mode="manual")
    s.tick(0.0)
    s.finish()
    assert not s.has_data and not save_live_test(p, p.add_test("", "R2", ""), s)


def test_keys_scored_during_an_i_o_test(tmp_path):
    p = _project(tmp_path)
    p.behaviours = [Behaviour("Grooming", "g", "state")]
    test = p.add_test("", "R1", "")
    s = IOSession(duration_s=2.0, start_mode="immediate", procedures=[FR1], devices=box())
    s.tick(0.0)
    s.tick(0.5)
    s.score("Grooming", "state")
    s.tick(1.0)
    s.score("Grooming", "state")
    run(s, until=3.0)
    assert save_live_test(p, test, s)
    [row] = p.analyse_test(test)
    assert row["Grooming: duration (s)"] == pytest.approx(0.5) and row["Test duration (s)"] == 2.0


def test_takenote_results_are_unchanged(tmp_path):
    # tests scored by hand only (no I/O log) keep their results: no test duration column, no time periods
    p = _project(tmp_path)
    p.behaviours = [Behaviour("Grooming", "g", "state")]
    p.analysis.bin_length_s = 1.0
    t = p.add_test("", "R1", "", duration_s=2.0, status="scored",
                   events=[{"behaviour": "Grooming", "t": 0.5, "t_end": 1.0}])
    rows = p.analyse_test(t, segmented=True)
    assert len(rows) == 1 and "Test duration (s)" not in rows[0] and rows[0]["Grooming: duration (s)"] == 0.5


def test_io_only_measures_of_a_period():
    evs = [{"t": 0.5, "device": "box", "channel": "lever", "kind": "input", "value": 1},
           {"t": 0.6, "device": "box", "channel": "lever", "kind": "input", "value": 0},
           {"t": 2.5, "device": "box", "channel": "lever", "kind": "input", "value": 1},
           {"t": 2.6, "device": "box", "channel": "lever", "kind": "input", "value": 0}]
    whole = io_only_measures(3.0, io_events=evs, result_variables={"x": "abc"})
    assert whole["lever: activations"] == 2 and whole["Variable: x"] == "abc"
    late = io_only_measures(3.0, io_events=evs, t_range=(2.0, 10.0))
    assert late["Test duration (s)"] == 1.0 and late["lever: activations"] == 1
    assert math.isclose(late["lever: latency to first activation (s)"], 0.5)


def test_crash_recovery(tmp_path):
    p = _project(tmp_path)
    test = p.add_test("", "R1", "")
    p.save()
    side = p.path / "recordings" / "test_0001_R1.autosave.json"
    side.parent.mkdir(exist_ok=True)
    s = IOSession(duration_s=0.0, start_mode="immediate", procedures=[FR1], devices=box(),
                  autosave_path=str(side), autosave_meta={"test_id": test.id, "animal": "R1"})
    run(s, until=1.2, presses=(0.3, 0.8))
    s.flush_autosave()
    d = json.loads(side.read_text())
    assert d["io_only"] and d["elapsed"] == pytest.approx(1.2) and not d["cols"]["t"]
    p2 = Project.load(p.path)
    [t] = recover_autosaves(p2)
    assert t.id == test.id and t.status == "scored" and not p2.has_track(t)
    assert p2.analyse_test(t)[0]["lever: activations"] == 2
    assert not side.exists()
    s.finish()
    # a side file of an I/O test that never started has nothing to recover
    s = IOSession(duration_s=0.0, start_mode="manual", autosave_path=str(side),
                  autosave_meta={"test_id": test.id, "animal": "R1"})
    s.tick(0.0)
    s.flush_autosave()
    assert recover_autosaves(Project.load(p.path)) == [] and not side.exists()
    s.finish()
