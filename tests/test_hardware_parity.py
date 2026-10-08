"""ANY-maze hardware parity: analogue filters and fast sampling, sensors and alerts, movement detectors,
temperature controllers, lights ramps, optogenetics, odours, liquid delivery, pellet error detection, shock
intensity, looping sounds, syringe-pump actions and the extra drivers (serial lines, Firmata, NI-DAQ, LabJack)."""

import time

import numpy as np
import pytest

from manymaze.core import ioconfig, iodrivers
from manymaze.core import iodevices as io
from manymaze.core.iocontrol import AnalogFilter, Thermostat
from manymaze.core.iodevices import DeviceManager
from manymaze.core.iomeasures import io_measures
from manymaze.core.procedures import ProcedureEngine
from manymaze.core.procedures.catalog import ACTION_SPECS, EVENT_SPECS
from manymaze.core.procedures.validate import validate

from test_iodevices import FakePort


def DO(action, **kw):
    return {"type": "do", "action": action, **kw}


def WHEN(event, body, **kw):
    return {"type": "when", "event": event, "body": body, **kw}


def run(eng, seconds, fps=10, each=None):
    eng.start(0)
    for i in range(int(seconds * fps) + 1):
        t = i / fps
        if each:
            each(t)
        eng.update_state(t, {})
    eng.stop(seconds)
    return eng


def series(eng, ch, typ=None):
    return [(e["t"], e["value"]) for e in eng.io_events if e["channel"] == ch and (typ is None or e.get("type") == typ)]


# ------------------------------------------------------------------------------------------- catalogue / config
def test_catalogue_reaches_any_maze_counts():
    assert len(EVENT_SPECS) >= 50 and len(ACTION_SPECS) >= 70


def test_config_helpers():
    pts = ioconfig.calibration_points("0:0|0.5:1.0|1:2.5")
    assert pts == [(0.0, 0.0), (0.5, 1.0), (1.0, 2.5)]
    assert ioconfig.level_for(0.5, pts) == pytest.approx(0.25)
    assert ioconfig.level_for(1.75, pts) == pytest.approx(0.75)
    assert ioconfig.level_for(9, pts) == 1.0 and ioconfig.level_for(-1, pts) == 0.0
    assert ioconfig.parse_pairs("vanilla:v1| almond : v2") == [("vanilla", "v1"), ("almond", "v2")]
    for t in ("serial_lines", "firmata", "nidaq", "labjack", "notify"):
        assert ioconfig.new_device(t)["type"] == t and t in io.drivers()
    ioconfig.register_device_type("xyz", "XYZ", {"port": ""}, ("pin",), "x")
    assert ioconfig.new_device("xyz", ["x"])["name"] == "x2"


def test_validation_of_new_channel_parameters():
    devs = [{"name": "box", "type": "virtual", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "plate", "kind": "thermostat"},
        {"name": "temp", "kind": "sensor"}, {"name": "olf", "kind": "odour"}]}]
    procs = [{"name": "p", "statements": [
        DO("set_temperature", channel="plate", target=30), DO("set_temperature", channel="lever", target=30),
        DO("tare_sensor", channel="temp"), DO("odour", channel="olf", odour="a")]}]
    errs = [m for _i, _p, m in validate(procs, {"devices": devs})]
    assert len(errs) == 1 and "'lever' is an input channel, not a temperature controller" in errs[0]


# ------------------------------------------------------------------------------------------- filters / sampling
def test_analog_filters():
    rate = 1000.0
    t = np.arange(2000) / rate
    x = 5 + np.sin(2 * np.pi * 2 * t) + np.sin(2 * np.pi * 200 * t)
    lp = AnalogFilter.from_channel({"filter": "lowpass", "cutoff_hz": 10, "order": 4, "period_ms": 1})
    y = np.array([lp(v) for v in x])
    assert abs(np.mean(y[1000:]) - 5) < 0.05 and abs(np.std(y[1000:]) - 0.707) < 0.03  # 2 Hz kept, 200 Hz gone
    assert np.abs(np.diff(y[1000:])).max() < 0.02
    hp = AnalogFilter("highpass", rate, cutoff_hz=1)
    y = np.array([hp(v) for v in 5 + np.zeros(500)])
    assert abs(y[-1]) < 1e-6
    bp = AnalogFilter("bandpass", rate, low_hz=100, high_hz=300)
    y = np.array([bp(v) for v in x])
    assert 0.5 < np.std(y[1000:]) < 0.9  # the 200 Hz component only
    av = AnalogFilter.from_channel({"filter": "average", "window_ms": 4, "period_ms": 1})
    assert [av(v) for v in (0, 4, 8, 12, 16)] == [0, 2, 4, 6, 10]
    with pytest.raises(ValueError):
        AnalogFilter("lowpass", 100, cutoff_hz=80)
    dm = DeviceManager([{"name": "v", "type": "virtual", "channels": [
        {"name": "x", "kind": "analog", "filter": "lowpass", "cutoff_hz": 600, "period_ms": 1}]}])
    assert any("cut-off" in e for e in dm.errors)


def test_arduino_fast_analogue_batches_and_sensors():
    port = FakePort()
    cfg = {"name": "box", "type": "arduino", "port": "x", "channels": [
        {"name": "lick", "kind": "analog", "pin": 1, "period_ms": 1, "deadband": 0},
        {"name": "food", "kind": "sensor", "sensor": "weight", "interface": "hx711", "pin": 4, "pin_b": 5,
         "scale": 0.001, "offset": 2.0},
        {"name": "temp", "kind": "sensor", "sensor": "temperature", "interface": "dht22", "pin": 7},
        {"name": "hum", "kind": "sensor", "sensor": "humidity", "interface": "dht22", "pin": 7},
        {"name": "light", "kind": "sensor", "sensor": "light", "pin": 2, "scale": 2.0},
        {"name": "pir", "kind": "pir", "pin": 8}]}
    dm = DeviceManager([cfg], transports={"box": port})
    w = port.written
    assert "A 1 1 0 10" in w and "L 4 5 100" in w and w.count("U 7 2000") == 1 and "A 2 50 2" in w
    assert "I 8 0 20" in w  # PIR: active-high output, no pull-up
    eng = ProcedureEngine([], dm)
    eng.start(0)
    port.feed("S 1 1000 1000 " + " ".join(str(v) for v in range(10)), "L 4 3000 1010", "U 7 215 -0 1011",
              "U 7 -15 553 1012", "A 2 100 1013", "D 8 1 1014")
    eng.update_state(1.0, {})
    lick = series(eng, "lick")
    assert [v for _t, v in lick] == list(range(10))
    assert lick[0][0] == pytest.approx(0.991) and lick[-1][0] == 1.0  # spread before the frame by board time
    assert eng.inputs[("box", "food")] == pytest.approx(5.0)
    assert eng.inputs[("box", "temp")] == -1.5 and eng.inputs[("box", "hum")] == 55.3
    assert eng.inputs[("box", "light")] == 200.0 and eng.inputs[("box", "pir")] == 1
    dm.close()


# ------------------------------------------------------------------------------------------- sensors / alerts / PIR
def test_sensor_alerts_pir_and_measures():
    sent = []
    iodrivers.NotifyDevice.sender = lambda kind, cfg, to, subject, text: sent.append((kind, to, subject, text))
    try:
        dm = DeviceManager([
            {"name": "box", "type": "virtual", "channels": [
                {"name": "temp", "kind": "sensor", "sensor": "temperature", "alert_min": 18, "alert_max": 26},
                {"name": "water", "kind": "sensor", "sensor": "weight"},
                {"name": "pir", "kind": "pir"}]},
            {"name": "alerts", "type": "notify", "email_to": "a@b.org", "sms_to": "+33600000000, 0600@sms.example"}])
        procs = [{"name": "p", "statements": [
            WHEN("sensor_out_of_range", [DO("increment", var="oor")]),
            WHEN("sensor_above", [DO("mark", name="hot")], channel="temp", threshold=30),
            WHEN("movement_start", [DO("increment", var="moves")])]}]
        eng = ProcedureEngine(procs, dm, context={"animal": "M1"})
        steps = {0.0: ("temp", 22), 0.1: ("water", 50), 1.0: ("temp", 27), 1.5: ("temp", 31), 2.0: ("temp", 24),
                 2.5: ("pir", 1), 3.0: ("pir", 0), 3.5: ("temp", 10), 4.0: ("water", 47.5)}

        def each(t):
            for k, (ch, v) in steps.items():
                if abs(k - t) < 1e-9:
                    dm.set_input("box", ch, v)
        run(eng, 5, each=each)
        dm.devices["alerts"].wait()
        assert eng.vars["oor"] == 2 and eng.vars["moves"] == 1
        assert [m["t"] for m in eng.marks] == [1.5]
        # one alert per channel at most every 10 minutes: the second excursion is not sent again
        assert len(eng.alerts) == 1 and "outside its range" in eng.alerts[0][1]
        assert {s[0] for s in sent} == {"email", "sms"} and len(sent) == 3 and "M1" in sent[0][2]
        m = io_measures(eng.io_events, 5, devices=dm.configs)
        assert m["Sensor temp: initial value"] == 22 and m["Sensor temp: final value"] == 10
        assert m["Sensor temp: max"] == 31 and m["Sensor temp: times out of range"] == 2
        assert m["Sensor temp: time out of range (s)"] == pytest.approx(2.5)
        assert m["Sensor water: intake"] == 2.5 and m["Sensor water: change"] == -2.5
        assert m["Movement detector pir: movements"] == 1 and m["Movement detector pir: time moving (s)"] == 0.5
        assert m["Movement detector pir: latency to first movement (s)"] == 2.5
        dm.close()
    finally:
        iodrivers.NotifyDevice.sender = None


def test_tare_and_read_sensor_and_send_alert():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [{"name": "bal", "kind": "sensor"}]}])
    procs = [{"name": "p", "statements": [
        WHEN("time_reached", [DO("tare_sensor", channel="bal")], time=1),
        WHEN("time_reached", [DO("read_sensor", channel="bal", var="w"), DO("send_alert", text="w={w}")], time=2)]}]
    eng = ProcedureEngine(procs, dm)
    run(eng, 3, each=lambda t: dm.set_input("box", "bal", 100 if t < 1.5 else 103))
    assert eng.vars["w"] == pytest.approx(3.0) and eng.alerts[0][1] in ("w=3", "w=3.0")


# ------------------------------------------------------------------------------------------- temperature control
def test_thermostat_pid_ramp_safety_and_events():
    dev = io.VirtualDevice({"name": "v", "channels": [
        {"name": "t", "kind": "sensor"}, {"name": "heater", "kind": "pwm"}, {"name": "fan", "kind": "output"},
        {"name": "plate", "kind": "thermostat", "sensor": "t", "heat": "heater", "cool": "fan", "band": 0.5,
         "max_temp": 45}]})
    th = dev.thermostats["plate"]
    dev.set_input("t", 20.0)
    th.set_target(30, ramp_per_min=60, now=100.0)
    assert th.setpoint == 20.0 and dev.outputs["heater"] == 0
    th.step(105.0)
    assert th.setpoint == pytest.approx(25.0) and dev.outputs["heater"] > 0 and dev.outputs["fan"] == 0
    th.step(200.0)
    assert th.setpoint == 30
    dev.set_input("t", 33.0)
    th.step(201.0)
    assert dev.outputs["heater"] == 0 and dev.outputs["fan"] == 1
    dev.set_input("t", 30.2)
    th.step(202.0)
    changes = dev.poll()
    assert ("plate.at_target", 1) in changes
    dev.set_input("t", 50.0)
    th.step(203.0)
    assert dev.outputs["heater"] == 0 and any("outside" in e for e in dev.errors)
    th.off()
    assert th.target is None and dev.outputs["plate"] == 0
    # a controller with its own regulation, driven by text commands
    port = FakePort()
    sd = io.SerialDevice({"name": "s", "port": "x", "channels": [
        {"name": "bath", "kind": "thermostat", "set_cmd": "SP {value:.1f}", "off_cmd": "STOP"}]}, port)
    sd.open()
    sd.thermostats["bath"].set_target(37, now=0)
    sd.thermostats["bath"].off()
    assert port.written == ["SP 37.0", "STOP"]


def test_engine_set_temperature_event_and_measures():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "t", "kind": "sensor"}, {"name": "heater", "kind": "pwm"},
        {"name": "plate", "kind": "thermostat", "sensor": "t", "heat": "heater"}]}])
    procs = [{"name": "p", "statements": [
        DO("set_temperature", channel="plate", target=30),
        WHEN("temperature_reached", [DO("mark", name="warm")], channel="plate")]}]
    eng = ProcedureEngine(procs, dm)

    def each(t):
        dm.set_input("box", "t", 20 + 4 * t)
        dm.devices["box"].service(time.monotonic())
    run(eng, 4, each=each)
    assert [round(m["t"], 1) for m in eng.marks] == [2.4]
    assert dm.devices["box"].thermostats["plate"].target is None  # switched off at the end of the test
    m = io_measures(eng.io_events, 4, devices=dm.configs)
    assert m["Temperature controller plate: mean target"] == 30
    assert m["Temperature controller plate: latency to target (s)"] == pytest.approx(2.4)
    dm.close()


# ------------------------------------------------------------------------------------------- lights / opto / shock
def test_light_level_and_ramp():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "house", "kind": "pwm"}, {"name": "cue", "kind": "output"}]}])
    procs = [{"name": "p", "statements": [
        DO("light_level", channel="house", level=20), DO("light_level", channel="cue", level=30),
        DO("light_ramp", channel="house", level=100, duration=2),
        {"type": "wait", "event": "light_ramp_done", "channel": "house"}, DO("mark", name="done")]}]
    eng = run(ProcedureEngine(procs, dm), 3)
    hv = series(eng, "house")
    assert hv[0] == (0.0, 0.2) and (1.0, 0.6) in hv and (2.0, 1) in hv
    assert series(eng, "cue") == [(0.0, 1), (3.0, 0)] and [m["t"] for m in eng.marks] == [2.0]
    m = io_measures(eng.io_events, 3, devices=dm.configs)
    assert 0.5 < m["Light house: mean level"] < 0.8


def test_opto_duty_cycle_intensity_and_sequence_file(tmp_path):
    f = tmp_path / "pulses.csv"
    f.write_text("onset,duration,intensity\n0, 0.2, 50\n0.5, 0.1\n# done\n")
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "laser", "kind": "output", "intensity": "laser_level"}, {"name": "laser_level", "kind": "pwm"}]}])
    procs = [{"name": "p", "statements": [
        DO("opto_train", channel="laser", frequency=5, duty_cycle=50, duration=1, intensity=25),
        {"type": "wait", "seconds": 1.5},
        DO("opto_sequence", channel="laser", file=str(f), repeat=2),
        {"type": "wait", "event": "pulse_sequence_done", "channel": "laser"}, DO("mark", name="seq")]}]
    eng = run(ProcedureEngine(procs, dm), 4, fps=20)
    on = [t for t, v in series(eng, "laser") if v]
    assert on[:5] == [0.0, 0.2, 0.4, 0.6, 0.8]
    assert [round(t, 2) for t, v in series(eng, "laser") if not v][:1] == [0.1]
    assert on[5:] == [1.5, 2.0, 2.1, 2.6]  # two repetitions of the file (span 0.6 s)
    assert series(eng, "laser_level")[0] == (0.0, 0.25) and (1.5, 0.5) in series(eng, "laser_level")
    assert [m["t"] for m in eng.marks] == [pytest.approx(2.7)]


def test_pulse_sequence_on_device_clock():
    dm = DeviceManager([{"name": "v", "type": "virtual", "channels": [{"name": "o", "kind": "output"}]}])
    seen = []
    dev = dm.devices["v"]
    orig = dev.set_output
    dev.set_output = lambda ch, v, max_s=None: (seen.append((time.monotonic(), v)), orig(ch, v, max_s))
    t0 = time.monotonic()
    assert dm.pulse_sequence("v", "o", [(0.05, 0.02), (0.1, 0.02)])
    time.sleep(0.25)
    dm.close()
    assert [v for _t, v in seen][:4] == [1, 0, 1, 0]
    # order and spacing matter; the absolute lateness depends on the machine (hosted CI runners add ~40 ms)
    assert abs(seen[0][0] - t0 - 0.05) < 0.1 and abs(seen[2][0] - t0 - 0.1) < 0.1
    assert abs((seen[2][0] - seen[0][0]) - 0.05) < 0.03


def test_shock_intensity_calibration():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "grid", "kind": "output", "role": "shocker", "intensity": "grid_level",
         "calibration": "0:0|1:2.0"}, {"name": "grid_level", "kind": "pwm"},
        {"name": "other", "kind": "output"}]}])
    procs = [{"name": "p", "statements": [
        DO("shock_pulse", channel="grid", duration=0.5, intensity=0.5), {"type": "wait", "seconds": 1},
        DO("shock_intensity", channel="grid", intensity=1.0), DO("shock_pulse", channel="grid", duration=0.5),
        DO("shock_intensity", channel="other", intensity=1.0)]}]
    eng = run(ProcedureEngine(procs, dm), 2)
    assert series(eng, "grid_level") == [(0.0, 0.25), (1.0, 0.5), (2.0, 0)]
    assert any("no intensity option" in e for e in eng.errors)
    m = io_measures(eng.io_events, 2, devices=dm.configs)
    assert m["Shocker grid: shocks"] == 2 and m["Shocker grid: mean intensity (mA)"] == 0.75
    assert m["Shocker grid: max intensity (mA)"] == 1.0


# ------------------------------------------------------------------------------------------- operant extras
def test_pellet_detection_retry_and_error():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "feeder", "kind": "output"}, {"name": "trough", "kind": "input"}]}])
    procs = [{"name": "p", "statements": [
        DO("pellet", channel="feeder", count=1, sensor="trough", timeout=0.5, retries=1),
        {"type": "wait", "seconds": 3},
        DO("pellet", channel="feeder", count=1, sensor="trough", timeout=0.5, retries=1),
        WHEN("pellet_dropped", [DO("increment", var="ok")]),
        WHEN("pellet_error", [DO("increment", var="jam")])]}]

    def each(t):
        if abs(t - 1.2) < 1e-9:  # the first pellet only drops on the retry
            dm.set_input("box", "trough", 1)
        if abs(t - 1.3) < 1e-9:
            dm.set_input("box", "trough", 0)
    eng = run(ProcedureEngine(procs, dm), 6, each=each)
    assert eng.vars == {"ok": 1, "jam": 1}
    m = io_measures(eng.io_events, 6, devices=dm.configs)
    assert m["feeder: pellets dispensed"] == 4  # 2 dispenses, each retried once
    assert m["feeder: pellets not dispensed (errors)"] == 1 and m["feeder: dispenser retries"] == 2


def test_dipper_dripper_and_odours():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "dip", "kind": "output"}, {"name": "drip", "kind": "output", "drop_ul": 10},
        {"name": "v0", "kind": "output"}, {"name": "v1", "kind": "output"}, {"name": "v2", "kind": "output"},
        {"name": "mfc", "kind": "pwm"},
        {"name": "olf", "kind": "odour", "odours": "vanilla:v1|almond:v2", "blank": "v0", "flow": "mfc",
         "max_flow": 2.0}]}])
    procs = [{"name": "p", "statements": [
        DO("dipper", channel="dip", duration=1), DO("liquid_drop", channel="drip", count=3, gap=0.2),
        DO("odour", channel="olf", odour="vanilla", flow=1), {"type": "wait", "seconds": 2},
        DO("odour", channel="olf", odour="almond"), {"type": "wait", "seconds": 1},
        DO("odour_off", channel="olf"), DO("odour", channel="olf", odour="rose")]}]
    eng = run(ProcedureEngine(procs, dm), 5)
    assert series(eng, "v1") == [(0.0, 1), (2.0, 0)] and series(eng, "v2") == [(2.0, 1), (3.0, 0)]
    assert series(eng, "v0") == [(3.0, 1), (5.0, 0)] and series(eng, "mfc")[0] == (0.0, 0.5)
    assert any("unknown odour 'rose'" in e for e in eng.errors)
    m = io_measures(eng.io_events, 5, devices=dm.configs)
    assert m["Odour vanilla: time presented (s)"] == 2 and m["Odour almond: presentations"] == 1
    assert m["Odour almond: latency to first presentation (s)"] == 2
    assert m["Olfactometer olf: time with an odour (s)"] == 3
    assert m["Dipper dip: presentations"] == 1 and m["Dipper dip: time on (s)"] == 1
    assert m["Dripper drip: drops"] == 3 and m["Dripper drip: volume (µl)"] == 30


def test_looping_sound(tmp_path):
    p = io.write_wav(tmp_path / "s.wav", "tone", 0.05, rate=8000)
    dev = io.AudioDevice({"name": "a", "backend": "none"})
    assert dev.audio("file", file=p, repeat=3)
    time.sleep(0.4)
    assert dev._procs[0].plays == 3
    assert dev.audio("file", file=p, repeat=0)
    time.sleep(0.2)
    dev.stop()
    n = dev._procs and dev._procs[0].plays
    assert not dev._procs and n is not False
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    eng = run(ProcedureEngine([{"name": "p", "statements": [
        DO("loop_sound", file=p), {"type": "wait", "seconds": 1}, DO("stop_sound")]}], dm), 2)
    assert series(eng, "sound") == [(0.0, 1), (1.0, 0)]
    assert dm.devices["spk"].played[0][1]["repeat"] == 0


# ------------------------------------------------------------------------------------------- pumps (engine side)
class FakePump(io.Device):
    type = "fakepump"

    def __init__(self, cfg, transport=None):
        super().__init__(cfg, transport)
        self.cmds = []
        for n in list(self.channels):
            for s, k in (("running", "status"), ("stalled", "status"), ("target_reached", "status")):
                self.add_status(f"{n}.{s}", k)

    def pump(self, channel, op, **kw):
        self.cmds.append((channel, op, kw))
        return True


def test_pump_actions_events_and_estimated_volumes():
    io.DRIVERS["fakepump"] = FakePump
    try:
        dm = DeviceManager([{"name": "p", "type": "fakepump", "channels": [{"name": "pump1", "kind": "pump"}]}])
        procs = [{"name": "x", "statements": [
            DO("pump_syringe", channel="pump1", syringe="14.5"),
            DO("pump_infuse", channel="pump1", rate=6, volume=0.2),
            {"type": "wait", "seconds": 3}, DO("pump_withdraw", channel="pump1", rate=1.2),
            WHEN("pump_stalled", [DO("pump_stop", channel="pump1")])]}]

        def each(t):
            if abs(t - 4) < 1e-9:
                dm.devices["p"]._changed("pump1.stalled", 1)
        eng = run(ProcedureEngine(procs, dm), 6, each=each)
        cmds = dm.devices["p"].cmds
        assert cmds[0] == ("pump1", "set_syringe", {"diameter_mm": 14.5})
        assert cmds[1] == ("pump1", "infuse", {"rate_ml_min": 6.0, "volume_ml": 0.2})
        assert cmds[2][1] == "withdraw" and cmds[3][1] == "stop" and len(cmds) == 4
        m = io_measures(eng.io_events, 6, devices=dm.configs)
        assert m["Pump pump1: volume infused (ml)"] == pytest.approx(0.2)  # 6 ml/min up to the 0.2 ml target
        assert m["Pump pump1: volume withdrawn (ml)"] == pytest.approx(0.02)  # 1.2 ml/min for 1 s
        # two commands at the same time (a stop and an infusion in one block) sort without comparing the events
        same_t = [dict(e, t=5.0) for e in eng.io_events if e.get("type") == "pump"]
        m2 = io_measures(list(eng.io_events) + same_t, 6, devices=dm.configs)
        assert m2["Pump pump1: infusions"] >= 1
        assert m["Pump pump1: infusions"] == 1 and m["Pump pump1: withdrawals"] == 1
        assert m["Pump pump1: stalls"] == 1 and m["Pump pump1: time pumping (s)"] == pytest.approx(3.0)
    finally:
        del io.DRIVERS["fakepump"]


# ------------------------------------------------------------------------------------------- drivers
class FakeLines:
    def __init__(self):
        self.rts = self.dtr = False
        self.cts = self.dsr = self.ri = self.cd = False
        self.closed = False

    def close(self):
        self.closed = True


def test_serial_lines_device():
    port = FakeLines()
    dm = DeviceManager([{"name": "ttl", "type": "serial_lines", "port": "x", "channels": [
        {"name": "out", "kind": "output", "pin": "RTS"}, {"name": "inv", "kind": "output", "pin": "dtr",
                                                          "invert": True},
        {"name": "beam", "kind": "input", "pin": "CTS"}, {"name": "bad", "kind": "input", "pin": "RTS"}]}],
        transports={"ttl": port})
    assert port.dtr is True and port.rts is False and any("bad" in e for e in dm.errors)
    dm.set_output("ttl", "out", 1)
    dm.set_output("ttl", "inv", 1)
    assert port.rts is True and port.dtr is False
    port.cts = True
    time.sleep(0.01)
    assert ("ttl", "beam", "input", 1) in dm.read_inputs()
    dm.close()
    assert port.closed and port.rts is False


class FakeFirmata(FakePort):
    def write(self, data):
        self.written.append(bytes(data))
        if bytes(data) == bytes([0xF0, 0x79, 0xF7]):
            self.incoming += bytes([0xF0, 0x79, 2, 5, ord("S"), 0, ord("F"), 0, 0xF7])


def test_firmata_device():
    port = FakeFirmata()
    dm = DeviceManager([{"name": "f", "type": "firmata", "port": "x", "channels": [
        {"name": "lever", "kind": "input", "pin": 2}, {"name": "pir", "kind": "pir", "pin": 9},
        {"name": "led", "kind": "output", "pin": 13}, {"name": "dim", "kind": "pwm", "pin": 5},
        {"name": "force", "kind": "analog", "pin": 0, "scale": 0.5, "period_ms": 10}]}], transports={"f": port})
    d = dm.devices["f"]
    assert d.firmware == "Firmata 2.5 SF" and not dm.errors
    w = port.written
    assert bytes([0xF4, 2, 11]) in w and bytes([0xF4, 9, 0]) in w and bytes([0xF4, 13, 1]) in w
    assert bytes([0xF4, 5, 3]) in w and bytes([0xC0, 1]) in w and bytes([0xD0, 1]) in w and bytes([0xD1, 1]) in w
    assert bytes([0xF0, 0x7A, 10, 0, 0xF7]) in w
    w.clear()
    dm.set_output("f", "led", 1)
    dm.set_output("f", "dim", 0.5)
    assert w == [bytes([0x91, 0x20, 0]), bytes([0xE5, 0, 1])]
    # lever pressed (pin 2 low with pull-up), PIR (pin 9 high), analogue 600
    port.incoming += bytes([0x90, 0b1111011, 0, 0x91, 0b10, 0, 0xE0, 600 & 0x7F, 600 >> 7])
    ch = dm.read_inputs()
    assert ("f", "lever", "input", 1) in ch and ("f", "pir", "pir", 1) in ch and ("f", "force", "analog", 300.0) in ch


class FakeTask:
    log = []

    def __init__(self, name):
        self.name, self.value = name, 0
        mk = type("C", (), {})
        for kind in ("di", "do", "ai", "ao", "ci"):
            c = mk()
            for m in ("add_di_chan", "add_do_chan", "add_ai_voltage_chan", "add_ao_voltage_chan",
                      "add_ci_count_edges_chan"):
                setattr(c, m, lambda phys, *a, _m=m, **k: FakeTask.log.append((self.name, _m, phys)))
            setattr(self, f"{kind}_channels", c)

    def start(self):
        pass

    def read(self):
        return {"n_beam": True, "n_ai": 2.5, "n_wheel": 42}.get(self.name, 0)

    def write(self, v):
        FakeTask.log.append((self.name, "write", v))

    def close(self):
        pass


def test_nidaq_device():
    iodrivers.NIDAQDevice.module = type("M", (), {"Task": FakeTask})
    try:
        dm = DeviceManager([{"name": "n", "type": "nidaq", "device_id": "Dev2", "channels": [
            {"name": "beam", "kind": "input", "pin": "port0/line1"}, {"name": "ai", "kind": "analog", "pin": "ai0",
                                                                      "scale": 2},
            {"name": "wheel", "kind": "encoder", "pin": "ctr0"}, {"name": "ao", "kind": "pwm", "pin": "ao0"},
            {"name": "led", "kind": "output", "pin": "port0/line2"}]}])
        assert ("n_beam", "add_di_chan", "Dev2/port0/line1") in FakeTask.log
        dm.set_output("n", "ao", 0.5)
        assert ("n_ao", "write", 2.5) in FakeTask.log
        ch = dm.read_inputs()
        assert ("n", "beam", "input", 1) in ch and ("n", "ai", "analog", 5.0) in ch
        assert ("n", "wheel", "encoder", 42) in ch
    finally:
        iodrivers.NIDAQDevice.module = None


def test_labjack_device():
    regs = {"FIO0": 1.0, "AIN0": 1.25}
    writes = []

    class LJM:
        @staticmethod
        def openS(*a):
            return 7

        @staticmethod
        def eReadName(h, name):
            return regs.get(name, 0.0)

        @staticmethod
        def eWriteName(h, name, v):
            writes.append((name, v))

        @staticmethod
        def close(h):
            writes.append(("close", h))
    iodrivers.LabJackDevice.module = LJM
    try:
        dm = DeviceManager([{"name": "lj", "type": "labjack", "channels": [
            {"name": "poke", "kind": "input", "pin": "FIO0"}, {"name": "pres", "kind": "analog", "pin": "AIN0"},
            {"name": "dac", "kind": "pwm", "pin": "DAC0"}, {"name": "wheel", "kind": "encoder", "pin": "DIO2",
                                                            "pin_b": "DIO3"}]}])
        assert ("DIO2_EF_INDEX", 10) in writes and ("DIO3_EF_INDEX", 10) in writes
        dm.set_output("lj", "dac", 1)
        assert ("DAC0", 5.0) in writes
        regs["DIO2_EF_READ_A"] = 17
        ch = dm.read_inputs()
        assert ("lj", "poke", "input", 1) in ch and ("lj", "pres", "analog", 1.25) in ch
        assert ("lj", "wheel", "encoder", 17) in ch
        dm.close()
        assert ("close", 7) in writes
    finally:
        iodrivers.LabJackDevice.module = None


def test_missing_optional_packages_are_reported():
    dm = DeviceManager([{"name": "n", "type": "nidaq", "channels": []},
                        {"name": "lj", "type": "labjack", "channels": []}], open=False)
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name in ("nidaqmx", "labjack"):
            raise ImportError(name)
        return real(name, *a, **k)
    builtins.__import__ = fake
    try:
        dm.open()
    finally:
        builtins.__import__ = real
    assert any("nidaqmx" in e for e in dm.errors) and any("labjack-ljm" in e for e in dm.errors)


def test_thermostat_class_is_exported():
    assert Thermostat is io.Thermostat


def test_pump_and_balance_drivers_are_registered_and_driven_by_procedures():
    from manymaze.core import pumps

    assert io.drivers()["syringe_pump"] is pumps.SyringePumpDevice and "scale" in io.drivers()
    assert ioconfig.new_device("syringe_pump")["protocol"] == "new_era"
    dm = DeviceManager([{"name": "pumps", "type": "syringe_pump", "protocol": "simulated", "channels": [
        {"name": "p1", "kind": "pump", "syringe": "BD Plastipak 10 ml"}]}])
    procs = [{"name": "x", "statements": [
        DO("pump_infuse", channel="p1", rate=60, volume=0.5),
        WHEN("pump_target_reached", [DO("mark", name="done")], channel="p1")]}]
    eng = ProcedureEngine(procs, dm)
    eng.start(0)
    for i in range(16):
        time.sleep(0.1)  # the simulated pump runs on the computer's clock
        eng.update_state(i / 10, {})
    eng.stop(1.6)
    assert eng.marks and not [e for e in eng.errors if "simulated" in e]
    m = io_measures(eng.io_events, 1.6, devices=dm.configs)
    assert m["Pump p1: volume infused (ml)"] == pytest.approx(0.5, abs=0.05) and m["Pump p1: infusions"] == 1
    dm.close()
