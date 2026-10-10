"""Small I/O items of phase 6: the delay after the start switch (start keys, remotes, a start switch input), the
start switch input itself, blocking a test until the animal is weighed, and the sound-level / ultrasound sensors."""

import time
from datetime import date, datetime

from manymaze.core import ioconfig, scales
from manymaze.core.iodevices import DeviceManager, DeviceView
from manymaze.core.iomeasures import io_measures
from manymaze.core.live import IOSession
from manymaze.core.livegroup import LiveGroup
from manymaze.core.project import Project
from test_audit_live_hw import _frame, _session

FPS = 25.0


def _feed(s, frames, k0=0):
    for k in range(k0, k0 + frames):
        s.process(_frame(80 + k % 30, 100), k / FPS)
    return k0 + frames


def test_delay_after_the_start_switch():
    s = _session(start_mode="manual", duration_s=0, fps=FPS, start_delay_s=0.3)
    k = _feed(s, 3)
    s.request_start(switch=True)  # a start key / remote
    k = _feed(s, 2, k)
    assert s.state == "waiting" and s.start_phase == "delay"
    assert s.log[-1][1] == "Start switch: the test starts in 0.3 s"
    s.request_start(switch=True)  # pressing again does not restart the count
    due = s._start_due
    time.sleep(0.35)
    _feed(s, 1, k)
    assert s.state == "running" and s.start_phase == "" and due is not None
    # the Start button (not the switch) starts at once
    s2 = _session(start_mode="manual", duration_s=0, fps=FPS, start_delay_s=5.0)
    _feed(s2, 1)
    s2.request_start()
    _feed(s2, 1, 1)
    assert s2.state == "running"


def _box():
    return DeviceManager([{"name": "box", "type": "virtual", "channels": [{"name": "start", "kind": "input"},
                                                                          {"name": "light", "kind": "output"}]}])


def test_start_switch_input():
    dm = _box()
    dm.set_input("box", "start", 1)  # already closed when armed: does not start the test
    s = _session(start_mode="input", start_input="box/start", devices=dm, duration_s=0, fps=FPS)
    k = _feed(s, 3)
    assert s.state == "waiting"
    dm.set_input("box", "start", 0)
    k = _feed(s, 2, k)
    dm.set_input("box", "start", 1)  # closing it starts the test
    _feed(s, 2, k)
    assert s.state == "running"
    # the device's name may be left out; with a delay
    dm2 = _box()
    s2 = _session(start_mode="input", start_input="start", devices=dm2, duration_s=0, fps=FPS, start_delay_s=0.2)
    k = _feed(s2, 2)
    dm2.set_input("box", "start", 1)
    k = _feed(s2, 2, k)
    assert s2.state == "waiting" and s2.start_phase == "delay"
    time.sleep(0.25)
    _feed(s2, 1, k)
    assert s2.state == "running"


def test_start_switch_of_an_io_only_test_and_a_box_of_several():
    dm = DeviceManager([{"name": n, "type": "virtual", "channels": [{"name": "start", "kind": "input"}]}
                        for n in ("b1", "b2")])
    views = [DeviceView(dm, n) for n in ("b1", "b2")]
    tests = [IOSession(None, duration_s=0, start_mode="input", start_input="b1/start", devices=v) for v in views]
    for s in tests:
        s.tick(0.0)
    dm.set_input("b2", "start", 1)  # the second box's switch: "b1/start" means "this test's box"
    for s in tests:
        s.tick(0.1)
    assert [s.state for s in tests] == ["waiting", "running"]


def test_start_keys_of_several_tests_use_the_delay():
    g = LiveGroup()
    s = _session(start_mode="manual", duration_s=0, fps=FPS, start_delay_s=10.0)
    e = g.add_session(None, s, label="A")
    assert g.key("space") == "start"
    _feed(s, 2)
    assert s.state == "waiting" and s.start_phase == "delay"
    g.start(e)  # Start now: at once
    _feed(s, 1, 2)
    assert s.state == "running"


def test_weighing_before_a_test():
    p = Project()
    a = p.ensure_animal("M1")
    assert not scales.weight_needed(p, "M1")  # not asked for
    p.require_weight_before_test = True
    assert not scales.weight_needed(p, "M1")  # no balance connected
    p.io_devices = [{"name": "balance", "type": "scale", "port": "x"}]
    assert scales.weight_needed(p, "M1") and not scales.weight_needed(p, "nobody")
    scales.record_weight(p, a, 21.0, when=datetime(2026, 10, 1, 9, 0))
    assert scales.weight_needed(p, "M1", today=date(2026, 10, 2)) and not scales.weight_needed(p, "M1", date(2026, 10, 1))
    scales.record_weight(p, a, 21.5)
    assert scales.weighed_today(a) and not scales.weight_needed(p, "M1")
    p.io_devices[0]["enabled"] = False
    a.weights.clear()
    assert not scales.weight_needed(p, "M1")  # the balance is disabled
    q = Project.from_dict(p.to_dict())
    assert q.require_weight_before_test and q.to_dict()["require_weight_before_test"] is True


def test_sound_and_ultrasound_sensors():
    assert ioconfig.SENSOR_TYPES["sound"] == "dBA" and ioconfig.SENSOR_TYPES["ultrasound"] == "kHz"
    assert ioconfig.SENSOR_TYPES["ultrasound_level"] == "dB"
    devices = [{"name": "box", "type": "arduino", "channels": [
        {"name": "mic", "kind": "sensor", "sensor": "sound", "pin": 0},
        {"name": "usv", "kind": "sensor", "sensor": "ultrasound", "pin": 1},
        {"name": "usv_db", "kind": "sensor", "sensor": "ultrasound_level", "pin": 2, "alert_max": 65}]}]
    ev = [{"t": t, "device": "box", "channel": ch, "kind": "input", "value": v}
          for t, ch, v in ((0, "mic", 60), (5, "mic", 70), (0, "usv", 22), (4, "usv", 50), (0, "usv_db", 40),
                           (5, "usv_db", 60))]
    ev += [{"t": 5, "device": "box", "channel": "usv_db.out_of_range", "kind": "input", "value": 0, "type": "status"}]
    r = io_measures(ev, 10.0, devices=devices)
    assert r["Sensor mic: mean"] == 65.0 and r["Sensor mic: max"] == 70.0 and r["Sensor mic: change"] == 10.0
    assert r["Sensor mic: equivalent level (Leq)"] == 67.404  # the mean of the energy, above the mean of the dB
    assert r["Sensor usv: max"] == 50.0 and "Sensor usv: equivalent level (Leq)" not in r  # (kHz)
    assert r["Sensor usv_db: equivalent level (Leq)"] == 57.033 and "Sensor usv_db: time out of range (s)" in r
    # a period: the samples in it
    r2 = io_measures(ev, 10.0, t_range=(4.5, 10.0), devices=devices)
    assert r2["Sensor mic: equivalent level (Leq)"] == 70.0
