"""I/O devices: virtual, Arduino (protocol, with a fake serial port), serial text, audio WAVs, io_measures."""

import wave

import numpy as np
import pytest

from manymaze.core import ioconfig
from manymaze.core import iodevices as io
from manymaze.core.iodevices import DeviceManager
from manymaze.core.iomeasures import io_measures
from manymaze.core.procedures import ProcedureEngine


class FakePort:
    """Stands in for a pyserial port: records writes, replays queued board output, emulates the handshake."""

    def __init__(self, reply_to_hello=True):
        self.written = []
        self.incoming = b""
        self.reply = reply_to_hello
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        line = data.decode().strip()
        self.written.append(line)
        if line == "?" and self.reply:
            self.feed("MANYMAZE_IO 1.0 uno")

    def read(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def feed(self, *lines):
        self.incoming += "".join(ln + "\n" for ln in lines).encode()

    def close(self):
        self.closed = True


BOX = {"name": "box", "type": "arduino", "port": "/dev/fake", "channels": [
    {"name": "lever", "kind": "input", "pin": 2, "debounce_ms": 15},
    {"name": "beam", "kind": "input", "pin": 6, "pullup": False},
    {"name": "pellet", "kind": "output", "pin": 8},
    {"name": "light", "kind": "pwm", "pin": 5},
    {"name": "wheel", "kind": "encoder", "pin": 3, "pin_b": 4, "counts_per_rev": 100, "cm_per_rev": 40},
    {"name": "force", "kind": "analog", "pin": 0, "period_ms": 20, "scale": 0.5}]}


def test_virtual_device_manager():
    dm = DeviceManager([{"name": "v", "type": "virtual", "channels": [
        {"name": "in1", "kind": "input"}, {"name": "out1", "kind": "output"}]}])
    assert dm.status() == [("v", "in1", "input", 0), ("v", "out1", "output", 0)]
    dm.set_output("v", "out1", 1)
    dm.set_input("v", "in1", 1)
    dm.set_input("v", "in1", 1)  # unchanged: one report only
    assert dm.read_inputs() == [("v", "in1", "input", 1)]
    assert dm.read_inputs() == []
    assert ("v", "out1", "output", 1) in dm.status()
    assert dm.find_channel("out1") == "v" and dm.find_channel("in1", ("output",)) is None
    # unknown devices become virtual ones (simulation)
    dm.set_output("sim", "laser", 1)
    assert dm.has("sim") and ("sim", "laser", "output", 1) in dm.status()
    dm.all_off()
    assert dm.devices["v"].outputs["out1"] == 0
    dm.close()


def test_arduino_protocol_with_fake_port():
    port = FakePort()
    dm = DeviceManager([BOX], transports={"box": port})
    dev = dm.devices["box"]
    assert dev.version.startswith("MANYMAZE_IO") and dev.connected and not dm.errors
    w = port.written
    assert w[0] == "?" and "Z" in w
    assert "I 2 1 15" in w and "I 6 0 20" in w and "O 8 0" in w and "O 5 0" in w
    assert "E 3 4" in w and "A 0 20 2" in w and w[-1] == "Q"
    port.written.clear()
    dm.set_output("box", "pellet", 1, max_s=2)
    dm.set_output("box", "pellet", 0)
    dm.set_output("box", "light", 0.5)
    assert dm.pulse_train("box", "pellet", 0.05, 0.02, 3)
    assert dm.stop_train("box", "pellet")
    dm.send("box", "CUSTOM 1")
    assert port.written == ["W 8 1 2000", "W 8 0", "P 5 128", "T 8 50.000 20.000 3", "X 8", "CUSTOM 1"]
    # board -> host: lever pressed (pull-up: LOW = on), beam (no pull-up: HIGH = on), encoder, analogue, errors
    port.feed("D 2 0 1000", "D 6 1 1001", "E 3 57 1002", "A 0 300 1003", "garbage", "D 99 1 5", "ERR bad pin")
    changes = dm.read_inputs()
    assert ("box", "lever", "input", 1) in changes and ("box", "beam", "input", 1) in changes
    assert ("box", "wheel", "encoder", 57) in changes and ("box", "force", "analog", 150.0) in changes
    assert any("bad pin" in e for e in dm.errors)
    port.written.clear()
    dm.close()
    assert port.written[0] == "R" and port.closed


def test_arduino_without_firmware_reports_error():
    port = FakePort(reply_to_hello=False)
    dev = io.ArduinoDevice(BOX, port)
    dev.open(handshake_s=0.2)
    assert any("no reply" in e for e in dev.errors)


def test_arduino_without_port_or_pyserial():
    dm = DeviceManager([{"name": "a", "type": "arduino", "channels": []}])
    assert any("no serial port" in e for e in dm.errors)


def test_engine_drives_arduino_hardware_pulses():
    port = FakePort()
    dm = DeviceManager([BOX], transports={"box": port})
    pr = {"name": "p", "statements": [
        {"type": "when", "event": "input_on", "channel": "lever", "body": [
            {"type": "do", "action": "pellet", "channel": "pellet", "count": 2, "pulse_width": 40, "gap": 0.3},
            {"type": "do", "action": "pulse_train", "device": "box", "channel": "light", "frequency": 20,
             "pulse_width": 5, "duration": 0.5}]}]}
    eng = ProcedureEngine([pr], dm)
    eng.start(0)
    port.written.clear()
    for i in range(30):
        if i == 5:
            port.feed("D 2 0 100")
        eng.update_state(i / 10, {})
    eng.stop(3)
    assert "T 8 300.000 40.000 2" in port.written and "T 5 50.000 5.000 10" in port.written
    assert not any(x.startswith("W 8") for x in port.written[:3])  # pulses are timed by the board
    pel = [(e["t"], e["value"]) for e in eng.io_events if e["channel"] == "pellet"]
    assert pel == [(0.5, 1), (0.54, 0), (0.8, 1), (0.84, 0)]
    lever = [(e["t"], e["value"]) for e in eng.io_events if e["channel"] == "lever"]
    assert lever == [(0.5, 1)]


def test_serial_text_device():
    port = FakePort()
    dm = DeviceManager([{"name": "s", "type": "serial", "port": "x", "channels": [
        {"name": "led", "kind": "output", "on": "LED1 ON", "off": "LED1 OFF"},
        {"name": "door", "kind": "output"},
        {"name": "poke", "kind": "input", "on": "POKE IN", "off": "POKE OUT"}]}], transports={"s": port})
    dm.set_output("s", "led", 1)
    dm.set_output("s", "door", 1)
    dm.send("s", "RAW")
    assert port.written == ["LED1 ON", "door ON", "RAW"]
    port.feed("POKE IN", "noise", "POKE OUT", "POKE IN")
    assert [c[3] for c in dm.read_inputs()] == [1, 0, 1]


def test_audio_wav_generation(tmp_path):
    y = io.tone_samples(1000, 0.1, rate=8000, volume=1.0)
    assert y.dtype == np.int16 and len(y) == 800 and abs(int(y[0])) < 50 and np.abs(y).max() > 30000
    spec = np.abs(np.fft.rfft(y.astype(float)))
    assert np.argmax(spec) * 8000 / 800 == pytest.approx(1000, abs=10)
    p = io.write_wav(tmp_path / "n.wav", "noise", 2.5, volume=0.2, rate=8000)
    with wave.open(p) as w:
        assert w.getnframes() == 20000 and w.getframerate() == 8000
        data = np.frombuffer(w.readframes(20000), np.int16)
    assert 0.05 * 32767 < np.abs(data).mean() < 0.15 * 32767
    dev = io.AudioDevice({"name": "a", "backend": "none"})
    assert dev.backend is None and not dev.audio("tone", frequency=500, duration=0.05)
    assert not dev.audio("file", file=str(tmp_path / "missing.wav")) and dev.errors
    played = []
    io.AudioDevice.player = lambda path, vol: played.append(path) or type("H", (), {"stop": lambda self: None})()
    try:
        assert dev.audio("noise", duration=0.05) and played and played[0].endswith(".wav")
    finally:
        io.AudioDevice.player = None


def test_io_measures():
    ev = [
        {"t": 1.0, "device": "box", "channel": "lever", "kind": "input", "value": 1},
        {"t": 1.5, "device": "box", "channel": "lever", "kind": "input", "value": 0},
        {"t": 4.0, "device": "box", "channel": "lever", "kind": "input", "value": 1},
        {"t": 5.0, "device": "box", "channel": "lever", "kind": "input", "value": 0},
        {"t": 2.0, "device": "box", "channel": "pellet", "kind": "output", "value": 1, "type": "pellet"},
        {"t": 2.05, "device": "box", "channel": "pellet", "kind": "output", "value": 0, "type": "pellet"},
        {"t": 0.0, "device": "box", "channel": "wheel", "kind": "input", "value": 0, "type": "encoder"},
        {"t": 2.0, "device": "box", "channel": "wheel", "kind": "input", "value": 150, "type": "encoder"},
        {"t": 2.5, "device": "box", "channel": "wheel", "kind": "input", "value": 250, "type": "encoder"},
        {"t": 0.0, "device": "box", "channel": "force", "kind": "input", "value": 10, "type": "analog"},
        {"t": 3.0, "device": "box", "channel": "force", "kind": "input", "value": 40, "type": "analog"},
        {"t": 0.5, "device": "virtual", "channel": "sw", "kind": "output", "value": 1, "type": "switch"},
        {"t": 0.0, "device": "b2", "channel": "lever", "kind": "input", "value": 1},
    ]
    m = io_measures(ev, 6.0, devices=[BOX])
    assert m["box/lever: activations"] == 2 and m["box/lever: time on (s)"] == 1.5
    assert m["box/lever: latency to first activation (s)"] == 1.0 and m["box/lever: mean activation (s)"] == 0.75
    assert m["box/lever: activations per minute"] == 20.0
    assert m["b2/lever: activations"] == 1 and m["b2/lever: time on (s)"] == 6.0
    assert m["pellet: times on"] == 1 and m["pellet: pellets dispensed"] == 1
    assert m["pellet: time on (s)"] == pytest.approx(0.05)
    assert m["wheel: encoder counts"] == 250 and m["wheel: revolutions"] == 2.5
    # as ANY-maze: complete rotations (2) × the circumference
    assert m["wheel: distance (cm)"] == 80 and m["wheel: max rate (counts/s)"] == 250
    assert m["wheel: mean rate (rev/min)"] == 25
    assert m["force: mean"] == 25 and m["force: min"] == 10 and m["force: max"] == 40
    assert m["sw: times on"] == 1 and m["sw: time on (s)"] == 5.5 and m["sw: latency to first on (s)"] == 0.5
    # a time period
    m = io_measures(ev, 6.0, t_range=(3, 6), devices=[BOX])
    assert m["box/lever: activations"] == 1 and m["box/lever: latency to first activation (s)"] == 1.0
    assert m["pellet: times on"] == 0 and m["pellet: latency to first on (s)"] == 3.0
    assert m["wheel: encoder counts"] == 0 and m["force: mean"] == 40
    assert io_measures([], 10) == {}


def test_new_device_free_pin_and_watchdog_default():
    assert ioconfig.new_device("arduino", ["box"]) == {"name": "box2", "type": "arduino", "enabled": True, "channels": [],
                                                 "port": "", "baud": 115200}
    assert ioconfig.new_device("audio")["backend"] == "auto" and ioconfig.new_device("virtual")["name"] == "sim"
    cfg = {"channels": [{"name": "a", "pin": 2}, {"name": "b", "pin": "3"}, {"name": "c", "kind": "analog"}]}
    assert ioconfig.next_free_pin(cfg) == 4
    assert ioconfig.watchdog_ms(cfg) == 0  # no outputs: off unless set
    cfg["channels"].append({"name": "led", "kind": "output", "pin": 13})
    assert ioconfig.watchdog_ms(cfg) == ioconfig.DEFAULT_WATCHDOG_MS and ioconfig.watchdog_ms({**cfg, "watchdog_ms": 0}) == 0


def test_player_command_lines():
    assert io.player_args("afplay", "/a/b.wav", 0.5) == ["afplay", "-v", "0.5", "/a/b.wav"]
    assert io.player_args("aplay", "/a/b.wav", 0.5) == ["aplay", "/a/b.wav"]
    ps = io.player_args("powershell", "C:\\Users\\O'Neil\\t.wav", 1.0)
    assert ps[:2] == ["powershell", "-NoProfile"] and ps[-1] == \
        "(New-Object Media.SoundPlayer 'C:\\Users\\O''Neil\\t.wav').PlaySync()"
