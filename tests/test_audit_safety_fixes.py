"""Regression tests of the safety audit: the end-of-test cleanup survives failing devices, hardware trains always
end off, failed writes are not recorded as done, the I/O service thread restarts for new pulse sequences, the
recording thread never blocks the frame thread, "end the test" waits for the engine, atomic saves and newer
experiment files."""

import json
import time

import numpy as np
import pytest

from manymaze.core.iodevices import DeviceManager
from manymaze.core.live import _RecordingThread
from manymaze.core.procedures import ProcedureEngine
from manymaze.core.project import FORMAT_VERSION, PROJECT_FILE, Project
from manymaze.core.track import Track
from test_iodevices import BOX, FakePort
from test_procedures_actions_events import DO, FPS, W, WHEN, _frame, _session, proc


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_stop_switches_outputs_off_when_another_device_fails():
    dm = DeviceManager([{"name": "v", "type": "virtual", "channels": [
        {"name": "light", "kind": "output"}, {"name": "plate", "kind": "thermostat"}]}])
    eng = ProcedureEngine([proc(DO("output_on", device="v", channel="light"),
                                DO("set_temperature", device="v", channel="plate", target=30))], dm)
    eng.start(0)
    eng.update_state(0.1, {})
    assert dm.devices["v"].outputs["light"] == 1

    def broken(*a, **k):
        raise OSError("controller unplugged")
    dm.control = broken
    eng.stop(1)
    assert eng.stopped and dm.devices["v"].outputs["light"] == 0
    assert any("controller unplugged" in e for e in eng.errors)


def test_stopping_a_hardware_train_always_switches_the_output_off():
    port = FakePort()
    dm = DeviceManager([BOX], transports={"box": port})
    eng = ProcedureEngine([proc(DO("pulse_train", device="box", channel="pellet", frequency=2, pulse_width=100,
                                   duration=10))], dm)
    eng.start(0)
    eng.update_state(0.3, {})  # between two pulses: the engine thinks the output is off
    port.written.clear()
    eng.stop(0.3)
    assert port.written[:2] == ["X 8", "W 8 0"]


class _FailingPort(FakePort):
    fail = False

    def write(self, data):
        if self.fail:
            raise OSError("device unplugged")
        super().write(data)


def test_failed_output_write_is_not_recorded_and_disconnects():
    port = _FailingPort()
    dm = DeviceManager([BOX], transports={"box": port})
    dev = dm.devices["box"]
    eng = ProcedureEngine([proc(W(0.5), DO("output_on", device="box", channel="pellet"))], dm)
    eng.start(0)
    port.fail = True
    eng.update_state(0.6, {})
    assert dev.outputs["pellet"] == 0 and not dev.connected and port.closed
    assert not eng.outputs_state.get(("box", "pellet"))
    assert not [e for e in eng.io_events if e["channel"] == "pellet"]
    assert any("pellet" in e for e in eng.errors)
    assert dm.set_output("box", "pellet", 1) is False
    eng.stop(1)


def test_io_service_thread_restarts_for_new_pulse_sequences():
    dm = DeviceManager([{"name": "v", "type": "virtual", "channels": [{"name": "led", "kind": "output"}]}])
    assert dm.pulse_sequence("v", "led", [(0.0, 0.02)])
    assert _wait(lambda: dm._ka_thread is None)  # the thread clears itself when it exits
    assert dm.pulse_sequence("v", "led", [(0.0, 0.5)])
    assert _wait(lambda: dm.devices["v"].outputs["led"] == 1)
    dm.close()


class _SlowRecorder:
    def __init__(self, delay=0.0):
        self.frames, self.delay, self.closed = 0, delay, False

    def write(self, frame):
        time.sleep(self.delay)
        self.frames += 1

    def close(self):
        self.closed = True


def test_recording_padding_is_repeated_by_the_encoder():
    rec = _SlowRecorder()
    rt = _RecordingThread(rec, maxsize=2)
    frame = np.zeros((4, 4, 3), np.uint8)
    t0 = time.monotonic()
    for _ in range(3):
        rt.repeat(frame, 1000)  # a long capture gap: one queue item, not 1000
    assert time.monotonic() - t0 < 1.0
    rt.close()
    assert rec.frames == 3000 and rec.closed


def test_recording_close_has_a_timeout():
    rt = _RecordingThread(_SlowRecorder(delay=0.2), maxsize=1)
    for _ in range(3):
        rt.write(np.zeros((4, 4, 3), np.uint8))
    with pytest.raises(TimeoutError):
        rt.close(timeout=0.1)


def test_end_test_action_finishes_after_the_engine_run():
    procs = [proc(W(0.2), DO("end_test", reason="done")), proc(WHEN("test_end", [DO("log", text="bye")]))]
    s = _session(procs, duration_s=5, fps=FPS)
    seen = []
    s.devices = type("Box", (), {"release": lambda self: seen.append(s.engine.stopped)})()
    for i in range(20):
        s.process(_frame(60 + i), i / FPS)
    assert s.state == "finished" and s.end_reason == "done"
    assert seen == [True]  # the test-end handlers ran before the devices were released
    assert any("bye" in m for _t, m in s.log)


def test_project_save_is_atomic_and_keeps_unknown_keys(tmp_path):
    p = Project(name="x")
    p.save(tmp_path / "e")
    d = json.loads((tmp_path / "e" / PROJECT_FILE).read_text())
    d["future_feature"] = {"a": 1}
    (tmp_path / "e" / PROJECT_FILE).write_text(json.dumps(d))
    q = Project.load(tmp_path / "e")
    q.save()
    assert json.loads((tmp_path / "e" / PROJECT_FILE).read_text())["future_feature"] == {"a": 1}
    assert not list((tmp_path / "e").glob("*.tmp"))
    d["version"] = FORMAT_VERSION + 1
    (tmp_path / "e" / PROJECT_FILE).write_text(json.dumps(d))
    with pytest.raises(ValueError, match="newer version"):
        Project.load(tmp_path / "e").save()


def test_track_csv_written_atomically(tmp_path):
    path = tmp_path / "t.csv"
    tr = Track(t=np.arange(5) / 10, x=np.arange(5.0), y=np.arange(5.0))
    tr.to_csv(path)
    path.write_text("old")

    class Boom(Exception):
        pass
    bad = tr.copy()

    def explode(f):
        f.write("partial")
        raise Boom()
    bad._write_csv = explode
    with pytest.raises(Boom):
        bad.to_csv(path)
    assert path.read_text() == "old" and not list(tmp_path.glob("*.tmp"))
    tr.to_csv(str(path))
    assert len(Track.from_csv(path).t) == 5
