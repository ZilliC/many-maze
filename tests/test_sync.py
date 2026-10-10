"""The synchronisation element (core.sync): pulses at the test start and end, for every captured frame and for
every stored position, sent by each device's fastest path (the Arduino firmware's SYNC, a LabJack's own timing,
otherwise on and off by the computer). The pulses counted by the (fake / simulated) devices equal the frames."""

import re
import time

import pytest

from manymaze.core import iodrivers
from manymaze.core.iodevices import DeviceManager, DeviceView, VirtualDevice
from manymaze.core.live import IOSession
from manymaze.core.project import Project
from manymaze.core.session import save_live_test
from manymaze.core.sync import SYNC_DEFAULTS, SyncOutput, sync_from, sync_on
from manymaze.core.workflow import copy_protocol
from test_audit_live_hw import FIRMWARE, FakePort, _frame, _session, _wait

FPS = 25.0


def _sim(channel="sync"):
    return DeviceManager([{"name": "sim", "type": "virtual", "channels": [{"name": channel, "kind": "output"}]}])


def _run(s, n_frames, pause_at=None, resume_at=None):
    """Feed n_frames frames (the animal moving), pausing / resuming at those frame numbers; returns the frames
    processed while the test was running or paused."""
    in_test = 0
    for k in range(n_frames):
        if k == pause_at:
            s.pause()
        if k == resume_at:
            s.resume()
        before = s.state
        s.process(_frame(60 + k % 40, 100), k / FPS)
        if before in ("running", "paused") or s.state in ("running", "paused", "finished") and before == "waiting":
            in_test += 1
        if s.state == "finished":
            break
    return in_test


def test_settings_and_defaults():
    assert sync_from(None) == SYNC_DEFAULTS and not sync_on({})
    s = sync_from({"enabled": 1, "channel": " sync ", "width_ms": "5000", "per_frame": "yes", "junk": 3})
    assert s["channel"] == "sync" and s["width_ms"] == 1000.0 and s["per_frame"] is True and "junk" not in s
    assert sync_on(s) and not sync_on({**s, "enabled": False})
    assert not sync_on({**s, "test_start": False, "test_end": False, "per_frame": False, "per_position": False})
    assert sync_from({"width_ms": "x"})["width_ms"] == 1.0


def test_a_pulse_per_frame_equals_the_frames():
    dm = _sim()
    s = _session(devices=dm, start_mode="immediate", duration_s=1.0, fps=FPS,
                 sync={"enabled": True, "device": "sim", "channel": "sync", "per_frame": True})
    frames = _run(s, 60)
    assert s.state == "finished" and frames == len(s.cols["t"]) == 26
    so = s.sync_output
    pulses = dm.devices["sim"].sync_pulses["sync"]
    # the test start (its first frame) and its end (its last frame) are marked by those frames' pulses
    assert pulses == frames == so.sent and so.counts == {"test_start": 1, "test_end": 1, "frame": frames,
                                                          "position": 0}
    assert "frames 26" in so.summary() and "26 pulses sent" in so.summary()


def test_frames_while_paused_and_positions():
    for per_frame, per_position in ((True, False), (False, True)):
        dm = _sim()
        s = _session(devices=dm, start_mode="immediate", duration_s=0, fps=FPS,
                     sync={"enabled": True, "channel": "sync", "per_frame": per_frame, "per_position": per_position,
                           "test_start": False, "test_end": False})
        frames = _run(s, 40, pause_at=10, resume_at=20)
        s.finish()
        positions = len(s.cols["t"])
        assert s.pauses and positions < frames
        assert dm.devices["sim"].sync_pulses["sync"] == (frames if per_frame else positions)
        assert s.sync_output.device == "sim"  # found by its channel


def test_start_and_end_pulses_outside_frames():
    dm = _sim()
    s = _session(devices=dm, start_mode="manual", duration_s=0, fps=FPS,
                 sync={"enabled": True, "channel": "sync"})  # test start and end only (the defaults)
    _run(s, 3)
    assert dm.devices["sim"].sync_pulses.get("sync", 0) == 0  # waiting: no pulse
    s.request_start()
    _run(s, 5)
    s.finish()  # stopped by the user, outside a frame: a pulse of its own
    assert dm.devices["sim"].sync_pulses["sync"] == 2 and s.sync_output.counts["test_end"] == 1
    # an I/O-only test (no frames): its start and end
    dm2 = _sim()
    io = IOSession(None, duration_s=1.0, start_mode="immediate", devices=dm2,
                   sync={"enabled": True, "channel": "sync", "per_frame": True})
    io.tick(0.0)
    io.tick(1.2)
    assert io.state == "finished" and dm2.devices["sim"].sync_pulses["sync"] == 2


def test_arduino_sends_sync_commands():
    port = FakePort("1.3")
    box = {"name": "box", "type": "arduino", "port": "x", "watchdog_ms": 0,
           "channels": [{"name": "sync", "kind": "output", "pin": 8}, {"name": "lever", "kind": "input", "pin": 2}]}
    dm = DeviceManager([box], transports={"box": port})
    s = _session(devices=dm, start_mode="immediate", duration_s=0.4, fps=FPS,
                 sync={"enabled": True, "device": "box", "channel": "sync", "per_frame": True, "width_ms": 2})
    frames = _run(s, 30)
    syncs = [ln for ln in port.written if ln.startswith("SYNC")]
    assert syncs[0] == "SYNC 8 2000" and set(syncs[1:]) == {"SYNC"}  # the short form repeats the last pulse
    assert len(syncs) == frames == dm.devices["box"].sync_pulses["sync"]
    dm.devices["box"].configure()  # Z forgets the last pulse: the full form again
    assert dm.sync_pulse("box", "sync", 0.002) and port.written[-1] == "SYNC 8 2000"
    assert not dm.sync_pulse("box", "lever", 0.001)  # an input
    dm.close()
    # older firmware: the board ends the pulse itself after max_ms
    old = FakePort("1.2")
    dm = DeviceManager([box], transports={"box": old})
    assert dm.sync_pulse("box", "sync", 0.0005) and old.written[-1] == "W 8 1 1"
    assert dm.sync_pulse("box", "sync", 0.0201) and old.written[-1] == "W 8 1 21"
    dm.close()


class _NoTiming(VirtualDevice):
    """A device that cannot time a pulse: the manager switches the output on and off."""

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.writes = []

    def sync_pulse(self, channel, width_s):
        return None

    def set_output(self, channel, value, max_s=None):
        self.writes.append((channel, value, time.perf_counter()))
        return super().set_output(channel, value, max_s)


def test_computer_timed_pulses():
    dm = DeviceManager([])
    d = _NoTiming({"name": "ttl", "channels": [{"name": "sync", "kind": "output"}]})
    d.open()
    dm.devices["ttl"] = d
    assert dm.sync_pulse("ttl", "sync", 0.001)  # short: switched off at once by waiting
    (_, on, t_on), (_, off, t_off) = d.writes
    assert (on, off) == (1, 0) and 0.001 <= t_off - t_on < 0.05 and d.sync_pulses["sync"] == 1
    assert dm.sync_pulse("ttl", "sync", 0.05)  # longer: the service thread switches it off
    assert d.outputs["sync"] == 1 and _wait(lambda: d.outputs["sync"] == 0, 2.0)
    assert d.writes[-1][2] - d.writes[-2][2] >= 0.045
    dm.close()


class _LJM:
    names = []
    singles = []

    @staticmethod
    def openS(*a):
        return 1

    @classmethod
    def eWriteNames(cls, h, n, names, values):
        cls.names.append((n, list(names), list(values)))

    @classmethod
    def eWriteName(cls, h, name, v):
        cls.singles.append((name, v))

    @staticmethod
    def eReadName(h, name):
        return 0.0

    @staticmethod
    def close(h):
        pass


def test_labjack_times_short_pulses_itself():
    _LJM.names, _LJM.singles = [], []
    iodrivers.LabJackDevice.module = _LJM
    try:
        dm = DeviceManager([{"name": "lj", "type": "labjack", "channels": [
            {"name": "sync", "kind": "output", "pin": "FIO0"}, {"name": "inv", "kind": "output", "pin": "FIO1",
                                                               "invert": True}]}])
        assert dm.sync_pulse("lj", "sync", 0.001)
        assert _LJM.names[-1] == (3, ["FIO0", "WAIT_US_BLOCKING", "FIO0"], [1, 1000, 0])
        assert dm.sync_pulse("lj", "inv", 0.0001) and _LJM.names[-1][2] == [0, 100, 1]
        n = len(_LJM.singles)
        assert dm.sync_pulse("lj", "sync", 0.02)  # longer than 5 ms: on, then off by the computer
        assert _LJM.singles[n] == ("FIO0", 1) and _wait(lambda: _LJM.singles[-1] == ("FIO0", 0), 2.0)
        assert dm.devices["lj"].sync_pulses["sync"] == 2
        dm.close()
    finally:
        iodrivers.LabJackDevice.module = None


def test_several_tests_pulse_their_own_box():
    boxes = [{"name": n, "type": "virtual", "channels": [{"name": "sync", "kind": "output"}]} for n in ("b1", "b2")]
    dm = DeviceManager(boxes)
    v2 = DeviceView(dm, "b2")
    so = SyncOutput(v2, {"enabled": True, "device": "b1", "channel": "sync", "per_frame": True})
    assert so.ok and so.pulse("frame")
    assert dm.devices["b2"].sync_pulses == {"sync": 1} and not dm.devices["b1"].sync_pulses
    bad = SyncOutput(dm, {"enabled": True, "device": "b1", "channel": "nope", "per_frame": True})
    assert bad.problem and not bad.pulse("frame")
    assert SyncOutput(None, {"enabled": True, "channel": "sync"}).problem


def test_saved_with_the_test_and_the_protocol(tmp_path):
    p = Project(name="sync")
    p.sync = {"enabled": True, "channel": "sync", "per_frame": True}
    p.save(tmp_path / "s.mmaze")
    assert Project.load(p.path).sync == p.sync
    q = Project(name="copy")
    copy_protocol(p, q)
    assert q.sync == p.sync
    dm = _sim()
    s = _session(devices=dm, start_mode="immediate", duration_s=0.2, fps=FPS, sync=p.sync)
    _run(s, 20)
    t = p.add_test("", "M1")
    assert save_live_test(p, t, s)
    assert "Synchronisation pulses on sim/sync (1 ms): test start 1, test end 1, frames 6" in t.notes
    # a problem with the element is a warning of the test (and no note)
    s2 = _session(devices=dm, start_mode="immediate", duration_s=0.1, fps=FPS,
                  sync={"enabled": True, "channel": "missing", "per_frame": True})
    assert any("Synchronisation" in w for _, w in s2.warnings)


def test_firmware_has_the_sync_command():
    src = FIRMWARE.read_text()
    assert '#define FW_VERSION "1.3"' in src
    assert "case 'S'" in src and "ISR(TIMER1_COMPA_vect)" in src and 'err(F("SYNC: not an output"))' in src
    sync = src[src.index("void syncPulse"):src.index("void allOff")]
    assert "TIMSK1 |= _BV(OCIE1A)" in sync and "syncOn = 1" in sync  # timer interrupt, or the loop elsewhere
    assert "if (syncOn == 1 && us - syncStart >= syncWidth) stopSync(-1);" in src
    assert re.search(r"void allOff\(\) \{\s+stopSync\(-1\);", src)  # R / the watchdog end a pulse too
    readme = (FIRMWARE.parents[1] / "README.md").read_text()
    assert "SYNC pin width_us" in readme and "Timer1" in readme


@pytest.mark.parametrize("width", [0.0001, 0.5])
def test_widths_are_sent_in_microseconds(width):
    port = FakePort("1.3")
    dm = DeviceManager([{"name": "b", "type": "arduino", "port": "x", "watchdog_ms": 0,
                         "channels": [{"name": "s", "kind": "output", "pin": 3}]}], transports={"b": port})
    assert dm.sync_pulse("b", "s", width)
    assert port.written[-1] == f"SYNC 3 {int(round(width * 1e6))}"
    dm.close()
