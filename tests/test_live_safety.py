"""Live-test safety: touch/camera thread locking, per-box I/O in multi-test mode, pausing with outputs, recording
timing, crash recovery, the Arduino watchdog and smaller live-engine fixes."""

import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from manymaze.core import iodevices as io
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.iodevices import DeviceManager, DeviceView
from manymaze.core.live import LiveSession
from manymaze.core.livegroup import device_plan, recover_autosaves, save_live_test
from manymaze.core.procedures import ProcedureEngine
from manymaze.core.project import Project
from manymaze.core.tracking import Detection, DetectionSettings


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def proc(*stmts, name="P"):
    return {"name": name, "enabled": True, "statements": list(stmts)}


def _frame(x=100, y=100):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, y, 0)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _session(procs=(), **kw):
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(app, DetectionSettings(background="frame"), procedures=list(procs), **kw)
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    return s


class FakePort:
    def __init__(self):
        self.written = []
        self.incoming = b""
        self.closed = False
        self._lk = threading.Lock()

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        line = data.decode().strip()
        self.written.append(line)
        if line == "?":
            self.feed("MANYMAZE_IO 1.0 uno")

    def read(self, n):
        with self._lk:
            out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def feed(self, *lines):
        with self._lk:
            self.incoming += "".join(ln + "\n" for ln in lines).encode()

    def close(self):
        self.closed = True


def _box(name, watchdog=None):
    c = {"name": name, "type": "arduino", "port": "/dev/fake", "channels": [
        {"name": "lever", "kind": "input", "pin": 2}, {"name": "pellet", "kind": "output", "pin": 8},
        {"name": "shock", "kind": "output", "pin": 9}]}
    if watchdog is not None:
        c["watchdog_ms"] = watchdog
    return c


# ====================================================================== H1: touches take the session lock first
def test_touch_from_gui_thread_while_frames_run_does_not_deadlock():
    procs = [proc(WHEN("touch", [DO("pause_test"), DO("end_test")], area="left"))]
    s = _session(procs, duration_s=0)
    orig = s._update_engine

    def slow_update(*a):  # the camera thread holds the session lock a while before it takes the engine lock
        time.sleep(0.02)
        orig(*a)

    s._update_engine = slow_update
    stop = threading.Event()

    def frames():
        i = 0
        while not stop.is_set() and s.state != "finished":
            s.process(_frame(60 + i % 60), i / 25)
            i += 1

    th = threading.Thread(target=frames, daemon=True)
    th.start()
    while s.state != "running":
        time.sleep(0.001)
    done = threading.Event()

    def touch():
        for _ in range(50):
            s.touch("left", 0.1, 0.2)
        done.set()

    tt = threading.Thread(target=touch, daemon=True)
    tt.start()
    assert done.wait(10), "touch / frame threads deadlocked"
    stop.set()
    th.join(10)
    assert not th.is_alive() and s.state == "finished"
    assert any(e["channel"] == "touch left" for e in s.io_events)


# ====================================================================== H2: one box per test
def _two_virtual_boxes():
    return DeviceManager([{"name": n, "type": "virtual", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"},
        {"name": "shock", "kind": "output"}]} for n in ("A", "B")])


def test_device_views_do_not_steal_inputs_or_share_outputs():
    dm = _two_virtual_boxes()
    pr = [proc(WHEN("input_on", [DO("output_on", channel="pellet"), DO("shock_on", device="A", channel="shock",
                                                                         max_duration=1)], channel="lever"))]
    va, vb = DeviceView(dm, "A"), DeviceView(dm, "B")
    ea, eb = ProcedureEngine(pr, va), ProcedureEngine(pr, vb)
    ea.start(0)
    eb.start(0)
    dm.set_input("B", "lever", 1)  # box B's lever: only engine B reacts
    for i in range(1, 4):
        ea.update_state(i / 10, {})
        eb.update_state(i / 10, {})
    assert not ea.io_events
    assert {(e["device"], e["channel"], e["value"]) for e in eb.io_events} >= {("B", "lever", 1), ("B", "pellet", 1),
                                                                               ("B", "shock", 1)}
    # the procedure names box "A", but the session runs on box B: the shock goes to B, never to A
    assert dm.devices["A"].outputs == {"pellet": 0, "shock": 0}
    assert dm.devices["B"].outputs == {"pellet": 1, "shock": 1}
    ea.stop(1)
    eb.stop(1)
    va.release()
    vb.release()
    assert dm.devices["B"].outputs["shock"] == 0


def test_device_view_none_is_simulated_only():
    dm = _two_virtual_boxes()
    v = DeviceView(dm, None)
    eng = ProcedureEngine([proc(DO("output_on", device="A", channel="pellet"))], v)
    eng.start(0)
    eng.update_state(0.04, {})
    assert dm.devices["A"].outputs["pellet"] == 0  # simulated: the real box is not touched
    assert any(e["channel"] == "pellet" and e["value"] == 1 for e in eng.io_events)


def test_manager_fans_input_changes_out_to_subscribers():
    dm = _two_virtual_boxes()
    s1, s2 = dm.subscribe({"A"}), dm.subscribe(None)
    dm.set_input("A", "lever", 1)
    dm.set_input("B", "lever", 1)
    assert dm.read_inputs(s1) == [("A", "lever", "input", 1)]
    assert sorted(dm.read_inputs(s2)) == [("A", "lever", "input", 1), ("B", "lever", "input", 1)]
    assert dm.read_inputs(s1) == [] and dm.read_inputs(s2) == []
    dm.unsubscribe(s1)
    dm.unsubscribe(s2)


def test_manager_is_thread_safe_with_a_shared_serial_port():
    port = FakePort()
    dm = DeviceManager([_box("box", watchdog=0)], transports={"box": port})
    n = 400
    got = []
    lock = threading.Lock()

    deadline = time.monotonic() + 10

    def reader():
        while len(got) < n and time.monotonic() < deadline:
            ch = dm.read_inputs()
            with lock:
                got.extend(ch)

    ths = [threading.Thread(target=reader) for _ in range(3)]
    for t in ths:
        t.start()
    for i in range(n):
        port.feed(f"D 2 {i % 2} {i}")
    for t in ths:
        t.join(20)
    assert len(got) == n and all(c[1] == "lever" for c in got)
    dm.close()


def test_device_plan_refuses_unsafe_sharing():
    boxes = [_box("A"), _box("B"), {"name": "spk", "type": "audio"}]
    assert device_plan(boxes, "", []) == ("*", "")  # one test: the whole manager, as before
    alias, msg = device_plan(boxes, "", ["A"])
    assert alias is None and "Device" in msg
    assert device_plan(boxes, "B", ["A"]) == ("B", "")
    alias, msg = device_plan(boxes, "A", ["A"])
    assert alias is None and "already used" in msg
    alias, msg = device_plan(boxes, "B", ["*"])
    assert alias is None and "all the I/O devices" in msg
    assert device_plan(boxes, "-", ["*"]) == ("-", "")
    assert device_plan([{"name": "spk", "type": "audio"}], "", ["*"]) == ("*", "")  # no boxes: nothing to share
    alias, msg = device_plan(boxes, "Z", [])
    assert alias is None and "not configured" in msg


# ====================================================================== H3: pausing
def test_pause_handlers_run_at_once_and_shock_goes_off():
    dm = DeviceManager([{"name": "s", "type": "serial", "port": "x", "channels": [
        {"name": "shock", "kind": "output", "on": "SHOCK ON", "off": "SHOCK OFF"},
        {"name": "light", "kind": "output"}, {"name": "laser", "kind": "output"}]}],
        transports={"s": FakePort()})
    pr = [proc(DO("shock_on", channel="shock", max_duration=20), DO("output_on", channel="light"),
               DO("pulse_train", channel="laser", frequency=10, pulse_width=20, duration=0),
               WHEN("test_paused", [DO("log", text="paused handler")]),
               WHEN("test_resumed", [DO("log", text="resumed handler")]))]
    eng = ProcedureEngine(pr, dm, outputs_off_on_pause=False)
    eng.start(0)
    eng.update_state(0.04, {})
    assert eng.outputs_state[("s", "shock")] == 1
    eng.pause(0.04)
    # without another frame: the handler ran, the shock is off on the device, the train stopped
    assert any(m == "paused handler" for _, m in eng.log_lines)
    assert eng.outputs_state[("s", "shock")] == 0 and dm.devices["s"].outputs["shock"] == 0
    assert "SHOCK OFF" in dm.devices["s"].sent
    assert not eng._trains and eng.outputs_state[("s", "laser")] == 0
    assert eng.outputs_state[("s", "light")] == 1  # option off: other outputs are kept
    eng.resume(0.04)
    assert any(m == "resumed handler" for _, m in eng.log_lines)
    eng.stop(1)


def test_pause_switches_all_outputs_off_by_default():
    dm = _two_virtual_boxes()
    eng = ProcedureEngine([proc(DO("output_on", device="A", channel="pellet"),
                                WHEN("test_paused", [DO("log", text="p")]))], dm)
    eng.start(0)
    eng.update_state(0.04, {})
    eng.pause(0.04)
    assert dm.devices["A"].outputs["pellet"] == 0
    assert [e["value"] for e in eng.io_events if e["channel"] == "pellet"] == [1, 0]


def test_session_pause_runs_handler_immediately_and_safety_ticks_in_wall_time():
    pr = [proc(DO("output_on", channel="light"),
               WHEN("test_paused", [DO("output_off", channel="light")]),
               WHEN("key_down", [DO("resume_test")], key="r"))]
    s = _session(pr, duration_s=0)
    for i in range(5):
        s.process(_frame(), i / 25)
    assert s.engine.outputs_state[("virtual", "light")] == 1
    assert s.pause()
    assert s.engine.outputs_state[("virtual", "light")] == 0  # before any further frame
    s.key("r")  # keys work while paused (a "resume test" key)
    assert s.state == "running"
    s.finish()


def test_paused_tick_runs_safety_tasks_in_wall_clock_time():
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    eng = ProcedureEngine([proc(DO("tone", frequency=1000, duration=0.5, volume=0.1))], dm,
                          outputs_off_on_pause=False)
    eng.start(0)
    eng.update_state(0.1, {})
    eng.pause(0.1)
    eng.paused_tick(0.2)
    assert [e["value"] for e in eng.io_events if e["channel"] == "tone"] == [1000]
    eng.paused_tick(0.6)  # 0.6 s of real time while paused: the tone's end is logged at the pause time
    assert [e["value"] for e in eng.io_events if e["channel"] == "tone"] == [1000, 0]
    eng.stop(1)


# ====================================================================== M1: recording timing
def test_recording_pads_dropped_frames_and_is_fragmented(tmp_path):
    path = tmp_path / "rec.mp4"
    s = _session(duration_s=0, record_path=str(path), fps=25.0)
    ts = [i / 25 for i in range(20)] + [i / 25 for i in range(30, 50)]  # 10 frames lost
    for t in ts:
        s.process(_frame(), t)
    s.finish()
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    assert n == 50  # the video stays aligned with the track time (49 / 25 s)
    assert b"moof" in path.read_bytes()[:200000]  # fragmented MP4: a crash leaves a playable file


# ====================================================================== M2: crash resilience
def test_autosave_and_recovery(tmp_path):
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    test = proj.add_test("", "A1", "")
    side = proj.path / "recordings" / "test_0001_A1.autosave.json"
    side.parent.mkdir(exist_ok=True)
    s = _session(duration_s=0, autosave_path=str(side), autosave_s=0.2,
                 autosave_meta={"test_id": test.id, "animal": "A1", "apparatus": "", "stage": "", "trial": 1})
    for i in range(30):
        s.process(_frame(60 + i), i / 25)
    s.score("Rearing")
    for i in range(30, 40):
        s.process(_frame(60 + i), i / 25)
    s.flush_autosave()
    d = json.loads(side.read_text())
    assert len(d["cols"]["t"]) >= 35 and d["events"][0]["behaviour"] == "Rearing"
    # "crash": the session never finishes; the project is reopened
    proj2 = Project.load(proj.path)
    rec = recover_autosaves(proj2)
    assert [t.id for t in rec] == [test.id]
    t = proj2.get_test(test.id)
    assert t.status == "tracked" and len(proj2.load_tracks(t)[0]) >= 35
    assert "interrupted" in t.notes.lower() and not side.exists()


def test_autosave_removed_after_save(tmp_path):
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    test = proj.add_test("", "A1", "")
    side = tmp_path / "x.autosave.json"
    s = _session(duration_s=0, autosave_path=str(side), autosave_s=0.1, autosave_meta={"test_id": test.id})
    for i in range(10):
        s.process(_frame(), i / 25)
    s.finish()
    assert save_live_test(proj, test, s)
    assert not side.exists()


def test_arduino_watchdog_on_by_default_when_outputs_are_configured():
    port = FakePort()
    dm = DeviceManager([_box("box")], transports={"box": port})
    assert "H 2000" in port.written
    dm.close()
    port2 = FakePort()
    dm = DeviceManager([_box("box", watchdog=0)], transports={"box": port2})
    assert not any(w.startswith("H") for w in port2.written)
    dm.close()
    port3 = FakePort()
    dm = DeviceManager([{"name": "box", "type": "arduino", "port": "x", "channels": [
        {"name": "lever", "kind": "input", "pin": 2}]}], transports={"box": port3})
    assert not any(w.startswith("H") for w in port3.written)  # inputs only: nothing to switch off
    dm.close()


def test_firmware_readme_does_not_claim_outputs_off_on_port_close():
    txt = (Path(__file__).resolve().parents[1] / "firmware" / "README.md").read_text()
    assert "the port closes" not in txt


# ====================================================================== M3: watchdog keep-alive and firing
def test_keepalive_sent_from_a_timer_thread():
    port = FakePort()
    dm = DeviceManager([_box("box", watchdog=300)], transports={"box": port})
    port.written.clear()
    time.sleep(0.6)  # nobody polls the device (test paused, camera stalled …)
    assert "." in port.written
    dm.close()


def test_watchdog_marks_outputs_off_in_the_engine():
    port = FakePort()
    dm = DeviceManager([_box("box", watchdog=0)], transports={"box": port})
    eng = ProcedureEngine([proc(DO("output_on", channel="pellet"))], dm)
    eng.start(0)
    eng.update_state(0.04, {})
    assert eng.outputs_state[("box", "pellet")] == 1
    port.feed("WATCHDOG")
    eng.update_state(0.08, {})
    assert eng.outputs_state[("box", "pellet")] == 0
    off = [e for e in eng.io_events if e["channel"] == "pellet" and e["value"] == 0]
    assert off and off[0].get("reason") == "watchdog"
    eng.stop(1)
    dm.close()


# ====================================================================== low-priority fixes
def test_kept_variables_are_committed_only_when_the_test_is_saved(tmp_path):
    shared = {}
    pr = [proc({"type": "var", "name": "n", "value": 0, "keep": True}, {"type": "set", "var": "n", "value": "n + 1"})]
    s = _session(pr, duration_s=0, variables=shared)
    for i in range(5):
        s.process(_frame(), i / 25)
    s.finish()
    assert shared == {}  # discarded tests do not change the project variables
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    proj.variables = shared
    assert save_live_test(proj, proj.add_test("", "A1", ""), s)
    assert proj.variables == {"n": 1}


def test_live_freezing_uses_hysteresis():
    s = _session(duration_s=0)
    s.analysis.freeze_on_pct, s.analysis.freeze_off_pct, s.analysis.min_freeze_s = 2.0, 3.0, 0.0
    out = []
    for i, pct in enumerate([1.0, 2.5, 2.9, 3.5, 2.5, 1.0]):
        s.cols["t"].append(i / 25)
        out.append(s._freezing_now(Detection(x=1, y=1, area=100.0, motion=pct, detected=True)))
    assert out == [True, True, True, False, False, True]


def test_board_timestamps_kept_in_io_events():
    port = FakePort()
    dm = DeviceManager([_box("box", watchdog=0)], transports={"box": port})
    eng = ProcedureEngine([], dm)
    eng.start(0)
    port.feed("D 2 0 123456")
    eng.update_state(0.04, {})
    ev = [e for e in eng.io_events if e["channel"] == "lever"]
    assert ev and ev[0]["board_ms"] == 123456
    eng.stop(1)
    dm.close()


def test_stall_does_not_burst_pellets():
    dm = _two_virtual_boxes()
    eng = ProcedureEngine([proc(DO("pellet", device="A", channel="pellet", count=3, pulse_width=50, gap=0.5))], dm)
    eng.start(0)
    eng.update_state(0.0, {})
    eng.update_state(3.0, {})  # a 3 s stall
    for i in range(1, 40):
        eng.update_state(3.0 + i / 25, {})
    on = [e["t"] for e in eng.io_events if e["channel"] == "pellet" and e["value"] == 1]
    assert len(on) == 3 and min(np.diff(on)) >= 0.5 - 1e-6
    eng.stop(5)
