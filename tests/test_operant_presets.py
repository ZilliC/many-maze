"""Operant chamber presets (core.ioconfig.OPERANT_PRESETS): named inputs and outputs of Med Associates-, Coulbourn-
and Lafayette-style chambers wired to the existing drivers (Arduino firmware, Firmata, NI-DAQmx, LabJack, simulated);
the preset kept with the protocol (optional in project.json)."""

import json

import pytest

from manymaze.core import ioconfig, iodrivers
from manymaze.core.export import protocol_report
from manymaze.core.iodevices import DeviceManager
from manymaze.core.live import IOSession
from manymaze.core.procedures import check_before_test, project_context
from manymaze.core.project import Project
from manymaze.core.workflow import copy_protocol

from test_iodevices import FakePort


def test_every_preset_names_its_channels_and_numbers_its_pins():
    for key, preset in ioconfig.OPERANT_PRESETS.items():
        assert preset["label"] and preset["description"]
        for t in ioconfig.PRESET_DEVICE_TYPES:
            d = ioconfig.operant_device(key, t)
            assert d["type"] == t and d["name"] == "chamber" and d["enabled"]
            names = [c["name"] for c in d["channels"]]
            assert names == [n for n, _k, _r in preset["channels"]] and len(set(names)) == len(names)
            pins = [c.get("pin") for c in d["channels"]]
            if t == "virtual":
                assert all(p is None for p in pins)
            else:
                assert None not in pins and len(set(pins)) == len(pins)
    assert ioconfig.operant_device("custom", "arduino")["channels"] == []
    # the parts a chamber has, between the presets: levers, nose pokes, cue lights, dispenser, house light, shocker
    every = {n for p in ioconfig.OPERANT_PRESETS.values() for n, _k, _r in p["channels"]}
    assert {"left_lever", "lever", "left_poke", "poke_1", "left_light", "cue_light", "pellet", "house_light",
            "shocker", "head_entry", "tone"} <= every
    med = {c["name"]: c for c in ioconfig.operant_device("med_associates", "arduino")["channels"]}
    assert med["left_lever"] == {"name": "left_lever", "kind": "input", "pin": 2}
    assert med["shocker"]["role"] == "shocker" and med["house_light"]["role"] == "light"
    assert med["tone"]["role"] == "speaker" and med["pellet"]["kind"] == "output"


def test_pins_of_each_interface():
    assert ioconfig.preset_pins("arduino", 3, 2) == ([2, 3, 4], [5, 6])
    assert ioconfig.preset_pins("nidaq", 9, 3) == (
        [f"port0/line{i}" for i in range(8)] + ["port1/line0"], ["port2/line0", "port2/line1", "port2/line2"])
    assert ioconfig.preset_pins("nidaq", 0, 2) == ([], ["port0/line0", "port0/line1"])
    ins, outs = ioconfig.preset_pins("labjack", 6, 9)
    assert ins == [f"FIO{i}" for i in range(6)] and outs == ["FIO6", "FIO7"] + [f"EIO{i}" for i in range(7)]
    assert ioconfig.preset_pins("virtual", 2, 1) == ([None, None], [None])
    with pytest.raises(ValueError):
        ioconfig.operant_device("skinner", "arduino")
    with pytest.raises(ValueError):
        ioconfig.operant_device("coulbourn", "audio")


def test_several_chambers_get_a_device_each():
    devs = ioconfig.operant_devices("coulbourn", "arduino", 3)
    assert [d["name"] for d in devs] == ["chamber1", "chamber2", "chamber3"]
    assert all(d["channels"] == devs[0]["channels"] for d in devs)  # each its own board, wired the same way
    assert [d["name"] for d in ioconfig.operant_devices("coulbourn", "virtual", 2, taken=["chamber1"])] == [
        "chamber2", "chamber3"]
    assert [d["name"] for d in ioconfig.operant_devices("lafayette", "virtual", 1)] == ["chamber"]
    assert [d["name"] for d in ioconfig.operant_devices("lafayette", "virtual", 1, ["chamber"])] == ["chamber1"]


def test_arduino_and_firmata_drivers_configure_the_preset():
    cfg = ioconfig.operant_device("med_associates", "arduino")
    port = FakePort()
    dm = DeviceManager([cfg], transports={"chamber": port})
    assert dm.devices["chamber"].connected and not dm.errors
    w = port.written
    for c in cfg["channels"]:  # inputs with the pull-up (switch to ground), outputs off
        assert (f"I {c['pin']} 1 20" if c["kind"] == "input" else f"O {c['pin']} 0") in w
    dm.set_output("chamber", "pellet", 1)
    assert "W 10 1" in w
    port.feed("D 2 0 1000")  # the left lever pressed (pin 2 low: the switch closed to ground)
    assert ("chamber", "left_lever", "input", 1) in dm.read_inputs()
    dm.close()

    from test_hardware_parity import FakeFirmata

    fport = FakeFirmata()
    cfg = ioconfig.operant_device("coulbourn", "firmata")
    dm = DeviceManager([cfg], transports={"chamber": fport})
    assert not dm.errors
    assert bytes([0xF4, 2, 11]) in fport.written and bytes([0xF4, 13, 1]) in fport.written  # lever in, shocker out
    dm.close()


def test_ni_and_labjack_drivers_open_the_preset():
    from test_hardware_parity import FakeTask

    FakeTask.log = []
    iodrivers.NIDAQDevice.module = type("M", (), {"Task": FakeTask})
    try:
        dm = DeviceManager([ioconfig.operant_device("lafayette", "nidaq")])
        assert ("chamber_poke_1", "add_di_chan", "Dev1/port0/line0") in FakeTask.log
        assert ("chamber_tone", "add_do_chan", "Dev1/port2/line0") in FakeTask.log
        dm.set_output("chamber", "house_light", 1)
        assert ("chamber_house_light", "write", True) in FakeTask.log
        dm.close()
    finally:
        iodrivers.NIDAQDevice.module = None

    writes = []

    class LJM:
        @staticmethod
        def openS(*a):
            return 3

        @staticmethod
        def eReadName(h, name):
            return 1.0 if name == "FIO1" else 0.0

        @staticmethod
        def eWriteName(h, name, v):
            writes.append((name, v))

        @staticmethod
        def close(h):
            pass
    iodrivers.LabJackDevice.module = LJM
    try:
        dm = DeviceManager([ioconfig.operant_device("med_associates", "labjack")])
        assert ("chamber", "right_lever", "input", 1) in dm.read_inputs()
        dm.set_output("chamber", "shocker", 1)
        assert ("EIO2", 1) in writes
        dm.close()
    finally:
        iodrivers.LabJackDevice.module = None


def test_a_simulated_chamber_runs_the_example_lever_procedure():
    from manymaze.core.procedures.examples import EXAMPLES

    dm = DeviceManager(ioconfig.operant_devices("coulbourn", "virtual"))
    s = IOSession(duration_s=5.0, start_mode="immediate", procedures=[EXAMPLES["Lever press for food (FR 5)"]],
                  devices=dm)
    assert check_before_test(s.procedures, {"devices": dm.configs if hasattr(dm, "configs") else None}) == []
    t = 0.0
    while s.state != "finished" and t <= 6:
        k = round(t * 100)
        if k % 20 == 10:  # a press every 0.2 s from 0.1 s
            dm.set_input("chamber", "lever", 1)
        elif k % 20 == 15:
            dm.set_input("chamber", "lever", 0)
        s.tick(t)
        t = round(t + 0.01, 6)
    assert s.state == "finished" and s.result_variables["rewards"] == 5  # 25 presses on FR 5
    outs = {e["channel"] for e in s.io_events if e["kind"] == "output"}
    assert {"house_light", "pellet"} <= outs
    dm.close()


def test_the_preset_is_kept_with_the_protocol(tmp_path):
    p = Project(name="Operant")
    p.save(tmp_path / "op.mmaze")
    raw = json.loads((p.path / "project.json").read_text())
    assert ioconfig.PRESET_KEY not in raw["settings_extra"]  # optional: older experiments have none
    p.settings_extra.update(mode="io_only", operant_preset="lafayette")
    p.io_devices = ioconfig.operant_devices("lafayette", "virtual", 2)
    p.save()
    q = Project.load(p.path)
    assert q.settings_extra[ioconfig.PRESET_KEY] == "lafayette" and [d["name"] for d in q.io_devices] == [
        "chamber1", "chamber2"]
    new = copy_protocol(q, Project(name="Next"))
    assert new.settings_extra["operant_preset"] == "lafayette" and new.settings_extra["mode"] == "io_only"
    html = protocol_report(q, tmp_path / "protocol.html").read_text()
    assert "Input/output only" in html and "Lafayette-style chamber" in html
    assert project_context(q)["io_only"] is True
