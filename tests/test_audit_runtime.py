"""Runtime audit fixes: recorders closed when a procedure ends the test from a public call, pre-test outputs off
when a waiting test is aborted, per-box pulse sequences cancelled, outputs not shown off when the off command
was not sent, the encoder thread ending on close, sound loops and player processes, and opening devices outside
the manager lock."""

import threading
import time

import numpy as np

from manymaze.core import iodevices as io
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.iodevices import DeviceManager, DeviceView
from manymaze.core.live import LiveSession, _RecordingThread
from manymaze.core.pumps import SyringePumpDevice
from manymaze.core.tracking import DetectionSettings


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def proc(*stmts):
    return {"name": "P", "enabled": True, "statements": list(stmts)}


def _frame(x=100):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, 100, 0)
    return np.dstack([img] * 3)


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
        self.fail = False
        self.on_hello = None

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        if self.fail:
            raise OSError("unplugged")
        line = data.decode().strip()
        self.written.append(line)
        if line == "?":
            if self.on_hello:
                self.on_hello()
            self.incoming += b"MANYMAZE_IO 1.0 uno\n"

    def read(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def close(self):
        self.closed = True


BOX = {"name": "box", "type": "arduino", "port": "/dev/fake", "channels": [{"name": "pellet", "kind": "output",
                                                                            "pin": 8}]}


# ---------------------------------------------------------------------- 1: recorder closed after score() ends it
def test_scored_key_ending_the_test_closes_the_recording(tmp_path):
    path = tmp_path / "rec.mp4"
    s = _session([proc(WHEN("event_marked", [DO("end_test")], name="Stop"))], record_path=str(path), fps=25.0)
    for i in range(10):
        s.process(_frame(60 + i), i / 25)
    assert s.recorder is not None
    s.score("Stop")
    assert s.state == "finished" and s.recorder is None and not s._closing


# ---------------------------------------------------------------------- 2: abort while waiting to start
def test_aborting_a_waiting_test_switches_the_pre_test_outputs_off():
    s = _session([proc(WHEN("test_waiting", [DO("prevent_test_start"), DO("output_on", channel="light")]))],
                 start_mode="immediate")
    for i in range(5):
        s.process(_frame(80), i / 25)
    assert s.state == "waiting" and s.engine.outputs_state[("virtual", "light")] == 1
    s.finish()
    assert s.state == "finished" and s.engine.outputs_state[("virtual", "light")] == 0
    assert not s.engine.threads and not s.engine._pretest


# ---------------------------------------------------------------------- 3: a box's pulse sequences cancelled
def test_device_view_all_off_cancels_its_pulse_sequences():
    dm = DeviceManager([BOX], transports={"box": FakePort()})
    view = DeviceView(dm, "box")
    dm.pulse_sequence("box", "pellet", [(5.0, 1.0)])
    assert dm._sched
    view.release()
    assert not dm._sched
    dm.close()


# ---------------------------------------------------------------------- 4: off not sent: not shown off
def test_arduino_all_off_failed_write_keeps_the_output_state():
    port = FakePort()
    dm = DeviceManager([BOX], transports={"box": port})
    dev = dm.devices["box"]
    dev.set_output("pellet", 1)
    port.fail = True
    dev.all_off()
    assert dev.outputs["pellet"] == 1


NE_CFG = {"name": "pumps", "type": "syringe_pump", "port": "x", "protocol": "new_era", "channels": [
    {"name": "p1", "kind": "pump", "address": 0, "syringe": "BD Plastipak 10 ml"}]}


class PumpPort(FakePort):
    def write(self, data):
        if self.fail:
            raise OSError("unplugged")


def test_pump_stop_not_sent_keeps_the_pump_shown_running():
    port = PumpPort()
    dev = SyringePumpDevice(NE_CFG, port)
    dev.open()
    assert dev.pump("p1", "infuse", rate_ml_min=1, volume_ml=1) and dev.outputs["p1"] == 1
    port.fail = True
    assert dev.pump("p1", "stop") is False and dev.outputs["p1"] == 1
    dev.all_off()
    assert dev.outputs["p1"] == 1 and any("could not stop" in e for e in dev.errors)


# ---------------------------------------------------------------------- 5: encoder thread ends on close
def test_recording_thread_exits_when_the_close_sentinel_did_not_fit():
    go, closed = threading.Event(), []

    class SlowRecorder:
        def write(self, frame):
            go.wait(5)

        def close(self):
            closed.append(True)

    rt = _RecordingThread(SlowRecorder(), maxsize=1)
    rt.write(0)
    time.sleep(0.05)  # the encoder holds the first frame
    rt.write(1)  # the queue is full
    try:
        rt.close(timeout=0.1)
        raise AssertionError("close should time out")
    except TimeoutError:
        pass
    go.set()
    rt._thread.join(3)
    assert not rt._thread.is_alive() and closed


# ---------------------------------------------------------------------- 6 / 7: sound loops and players
class Handle:
    def __init__(self, done=False):
        self.done, self.stopped = done, False

    def poll(self):
        return 0 if self.done else None

    def stop(self):
        self.stopped = True


def test_loop_stopped_while_a_play_starts_stops_that_play():
    h = Handle()

    class Dev:
        backend = None

        def _play(self, path, volume, track=True):
            loop.stop()  # stop() arrives while this play is being started
            return h

    loop = io._Loop(Dev(), "none.wav", 1.0, 0)
    loop.start()
    loop._thread.join(2)
    assert not loop._thread.is_alive() and h.stopped


def test_finished_sounds_are_forgotten():
    io.AudioDevice.player = lambda path, vol: Handle(done=True)
    try:
        dev = io.AudioDevice({"name": "spk", "type": "audio", "backend": "none"})
        for _ in range(5):
            dev._play("x.wav", 1.0)
        assert len(dev._procs) == 1
    finally:
        io.AudioDevice.player = None


# ---------------------------------------------------------------------- 8: open outside the manager lock
def test_device_handshake_does_not_hold_the_manager_lock():
    port = FakePort()
    dm = DeviceManager([BOX], open=False, transports={"box": port})
    free = []

    def try_lock():
        ok = dm._lock.acquire(timeout=0.5)
        free.append(ok)
        if ok:
            dm._lock.release()

    def hello():
        if not free:
            th = threading.Thread(target=try_lock)
            th.start()
            th.join()

    port.on_hello = hello
    dm.open()
    assert free == [True] and dm.devices["box"].connected
    dm.close()
