"""Audit 2026-10-08, live testing / hardware I/O / firmware: off levels and failed writes of every driver, the
computer's own safety clock and maximum on-times, firmware protocol 1.2 (long pulses, P max_ms, deadband -1, reset
banner, over-long lines), reconnection, serial bandwidth, polling without the global lock, compact analogue samples,
autosave back-off and complete crash recovery, recorder stalls, pumps, balances, one failing test among several,
split-recording labels and heartbeats gated on a running program."""

import json
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from manymaze.core import iodrivers
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.autosave import Autosaver, RecoveredSession, recover as recover_autosaves
from manymaze.core.iodevices import DeviceManager, DeviceView, bandwidth_check
from manymaze.core.live import LiveSession, _RecordingThread
from manymaze.core.livegroup import LiveGroup
from manymaze.core.livemonitor import input_rows
from manymaze.core.project import Project
from manymaze.core.pumps import NewEraProtocol, SyringePumpDevice, crc16
from manymaze.core.scales import ScaleDevice, ScaleError, ScaleTimeout, parse_weight
from manymaze.core.session import END_ERROR
from manymaze.core.tracking import DetectionSettings

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware" / "manymaze_io" / "manymaze_io.ino"


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


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakePort:
    """A board running firmware `version`; `fail` makes every write raise (USB unplugged)."""

    def __init__(self, version="1.1"):
        self.version = version
        self.written = []
        self.incoming = b""
        self.closed = False
        self.fail = False
        self.reads = 0
        self._lk = threading.Lock()

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        if self.fail:
            raise OSError("unplugged")
        line = data.decode().strip()
        self.written.append(line)
        if line == "?":
            self.feed(f"MANYMAZE_IO {self.version} uno")

    def read(self, n):
        self.reads += 1
        with self._lk:
            out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def feed(self, *lines):
        with self._lk:
            self.incoming += "".join(ln + "\n" for ln in lines).encode()

    def close(self):
        self.closed = True


def _box(name="box", watchdog=0, **extra):
    c = {"name": name, "type": "arduino", "port": "/dev/fake", "channels": [
        {"name": "lever", "kind": "input", "pin": 2}, {"name": "pellet", "kind": "output", "pin": 8},
        {"name": "shock", "kind": "output", "pin": 9, "role": "shocker"}, {"name": "dim", "kind": "pwm", "pin": 5}]}
    c["watchdog_ms"] = watchdog
    c.update(extra)
    return c


# ---------------------------------------------------------------------- failing fakes of the other drivers
class LJM:
    """labjack.ljm stand-in: records writes; `fail` makes them raise."""

    fail = False
    writes: list = []

    @staticmethod
    def openS(*a):
        return 7

    @staticmethod
    def eReadName(h, name):
        return 0.0

    @classmethod
    def eWriteName(cls, h, name, v):
        if cls.fail:
            raise OSError("LJME_DEVICE_NOT_FOUND")
        cls.writes.append((name, v))

    @staticmethod
    def close(h):
        pass


@pytest.fixture
def labjack():
    LJM.fail, LJM.writes = False, []
    iodrivers.LabJackDevice.module = LJM
    yield LJM
    iodrivers.LabJackDevice.module = None


LJ_CFG = {"name": "lj", "type": "labjack", "channels": [
    {"name": "led", "kind": "output", "pin": "FIO0"}, {"name": "shock", "kind": "output", "pin": "FIO1"},
    {"name": "dac", "kind": "pwm", "pin": "DAC0"}]}


# ====================================================================== CRITICAL: Firmata inverted outputs
class FakeFirmata(FakePort):
    def write(self, data):
        if self.fail:
            raise OSError("unplugged")
        self.written.append(bytes(data))
        if bytes(data) == bytes([0xF0, 0x79, 0xF7]):
            self.incoming += bytes([0xF0, 0x79, 2, 5, ord("S"), 0, ord("F"), 0, 0xF7])


FIRMATA = {"name": "f", "type": "firmata", "port": "x", "channels": [
    {"name": "door", "kind": "output", "pin": 12, "invert": True}, {"name": "led", "kind": "output", "pin": 13},
    {"name": "lamp", "kind": "pwm", "pin": 5, "invert": True}, {"name": "lever", "kind": "input", "pin": 9}]}


def test_firmata_open_writes_the_off_level_of_inverted_outputs():
    port = FakeFirmata()
    dm = DeviceManager([FIRMATA], transports={"f": port})
    w = port.written
    mode = w.index(bytes([0xF4, 12, 1]))
    # port 1 (pins 8-15): pin 12 (inverted) HIGH = off, pin 13 LOW = off; the PWM lamp at 255 (inverted off)
    assert bytes([0x91, 0x10, 0]) in w[mode:] and bytes([0xE5, 0x7F, 1]) in w[mode:]
    w.clear()
    dm.set_output("f", "led", 1)  # another output of the port: the inverted one stays off (HIGH)
    assert w == [bytes([0x91, 0x30, 0])]
    dm.set_output("f", "door", 1)  # inverted on: LOW
    assert w[-1] == bytes([0x91, 0x20, 0])
    w.clear()
    dm.all_off()
    assert bytes([0x91, 0x10, 0]) in w and bytes([0xE5, 0x7F, 1]) in w
    dm.close()


def test_firmata_failed_write_keeps_the_state_and_reconnects():
    port = FakeFirmata()
    dm = DeviceManager([FIRMATA], transports={"f": port})
    d = dm.devices["f"]
    assert dm.set_output("f", "led", 1) is True and d.outputs["led"] == 1
    port.fail = True
    assert dm.set_output("f", "led", 0) is False and d.outputs["led"] == 1 and not d.connected
    dm.close()


# ====================================================================== HIGH: failed writes, all_off / close
def test_labjack_failed_off_is_reported_and_all_off_writes_every_channel(labjack):
    dm = DeviceManager([LJ_CFG])
    d = dm.devices["lj"]
    assert dm.set_output("lj", "shock", 1) is True
    labjack.fail = True
    assert dm.set_output("lj", "shock", 0) is False and d.outputs["shock"] == 1  # not shown off
    labjack.fail = False
    labjack.writes.clear()
    d.all_off()  # every output, even those cached at 0 (an "off" may have been lost)
    assert {n for n, _v in labjack.writes} == {"FIO0", "FIO1", "DAC0"} and d.outputs["shock"] == 0
    labjack.writes.clear()
    dm.close()
    assert {n for n, _v in labjack.writes} >= {"FIO0", "FIO1", "DAC0"}  # off again before closing


def test_nidaq_failed_write_keeps_the_state():
    log, fail = [], [False]

    class Task:
        def __init__(self, name):
            self.name = name
            mk = type("C", (), {})
            for kind in ("di", "do", "ai", "ao", "ci"):
                c = mk()
                for m in ("add_di_chan", "add_do_chan", "add_ai_voltage_chan", "add_ao_voltage_chan",
                          "add_ci_count_edges_chan"):
                    setattr(c, m, lambda *a, **k: None)
                setattr(self, f"{kind}_channels", c)

        def write(self, v):
            if fail[0]:
                raise OSError("device removed")
            log.append((self.name, v))

        def read(self):
            return 0

        def start(self):
            pass

        def close(self):
            pass

    iodrivers.NIDAQDevice.module = type("M", (), {"Task": Task})
    try:
        dm = DeviceManager([{"name": "n", "type": "nidaq", "channels": [
            {"name": "led", "kind": "output", "pin": "port0/line0"}, {"name": "ao", "kind": "pwm", "pin": "ao0"}]}])
        d = dm.devices["n"]
        assert dm.set_output("n", "led", 1) is True
        fail[0] = True
        assert dm.set_output("n", "led", 0) is False and d.outputs["led"] == 1
        fail[0] = False
        log.clear()
        d.all_off()
        assert {n for n, _ in log} == {"n_led", "n_ao"}
        log.clear()
        dm.close()
        assert {n for n, _ in log} == {"n_led", "n_ao"}
    finally:
        iodrivers.NIDAQDevice.module = None


def test_serial_lines_failed_write_keeps_the_state():
    class Lines:
        def __init__(self):
            self.fail = False
            self._rts = self.dtr = False
            self.cts = self.dsr = self.ri = self.cd = False

        @property
        def rts(self):
            return self._rts

        @rts.setter
        def rts(self, v):
            if self.fail:
                raise OSError("unplugged")
            self._rts = v

        def close(self):
            pass

    port = Lines()
    dm = DeviceManager([{"name": "ttl", "type": "serial_lines", "port": "x", "channels": [
        {"name": "out", "kind": "output", "pin": "RTS"}]}], transports={"ttl": port})
    d = dm.devices["ttl"]
    assert dm.set_output("ttl", "out", 1) is True and port.rts is True
    port.fail = True
    assert dm.set_output("ttl", "out", 0) is False and d.outputs["out"] == 1
    port.fail = False
    port._rts = True
    d.outputs["out"] = 0  # cached off, but the line is still on
    d.all_off()
    assert port.rts is False


def test_text_serial_all_off_and_close_send_every_off_command():
    port = FakePort()
    dm = DeviceManager([{"name": "s", "type": "serial", "port": "x", "channels": [
        {"name": "a", "kind": "output", "on": "A1", "off": "A0"}, {"name": "b", "kind": "output", "on": "B1",
                                                                     "off": "B0"}]}], transports={"s": port})
    port.written.clear()
    dm.devices["s"].all_off()
    assert port.written == ["A0", "B0"]
    port.written.clear()
    dm.close()
    assert "A0" in port.written and "B0" in port.written and port.closed


# ====================================================================== HIGH: the computer's safety clock
def test_host_cuts_off_outputs_that_the_device_cannot_time(labjack):
    dm = DeviceManager([LJ_CFG])
    assert dm.set_output("lj", "shock", 1, max_s=0.2)
    assert _wait(lambda: dm.devices["lj"].outputs["shock"] == 0, 2.0)
    assert ("FIO1", 0) in labjack.writes and any("maximum on-time" in e for e in dm.errors)
    dm.close()


def test_session_without_frames_still_turns_the_shock_off_at_max_s(labjack):
    dm = DeviceManager([LJ_CFG])
    s = _session([proc(DO("shock_on", device="lj", channel="shock", max_duration=0.3))], duration_s=0,
                 devices=dm)
    for i in range(3):
        s.process(_frame(60 + i), i / 25)
    assert s.engine.outputs_state[("lj", "shock")] == 1
    # the camera stops delivering frames: the safety thread runs the procedures' clock
    assert _wait(lambda: s.engine.outputs_state[("lj", "shock")] == 0, 3.0)
    off = [e for e in s.engine.io_events if e["channel"] == "shock" and e["value"] == 0]
    assert off and off[0]["t"] == pytest.approx(0.3)  # logged when the cut-off was due
    assert dm.devices["lj"].outputs["shock"] == 0 and ("FIO1", 0) in labjack.writes
    s.finish()
    dm.close()


def test_shocker_role_caps_on_time_and_arduino_pwm_has_a_limit():
    port = FakePort()
    dm = DeviceManager([_box()], transports={"box": port})
    port.written.clear()
    dm.set_output("box", "shock", 1)  # no max_s given: the shocker role caps it at 60 s on the board
    dm.set_output("box", "dim", 0.5, max_s=2)
    dm.set_output("box", "pellet", 1, max_s=0.0004)  # sub-millisecond: never 0 (= no limit)
    assert port.written == ["W 9 1 60000", "P 5 128 2000", "W 8 1 1"]
    assert ("box", "dim") in dm._deadlines  # the computer also cuts the PWM level off (older firmware)
    assert ("box", "shock") not in dm._deadlines  # timed by the board
    dm.close()


def test_long_trains_and_pulses_are_sent_whole():
    port = FakePort()
    dm = DeviceManager([_box()], transports={"box": port})
    port.written.clear()
    assert dm.pulse_train("box", "pellet", 3600.0, 3600.0, 1)  # a one-hour pulse: timed in ms by firmware 1.2
    assert dm.pulse_train("box", "shock", 120.0, 100.0, 1)  # shocker: width capped at 60 s
    assert port.written == ["T 8 3600000.000 3600000.000 1", "T 9 120000.000 60000.000 1"]
    dm.close()


# ====================================================================== firmware 1.2
def test_firmware_source_has_the_protocol_fixes():
    src = FIRMWARE.read_text()
    version = re.search(r'#define FW_VERSION "(\d+)\.(\d+)"', src)
    assert version and tuple(map(int, version.groups())) >= (1, 2)  # 1.2's fixes, kept by later versions
    setup = src[src.index("void setup()"):src.index("void loop()")]
    assert "printId();" in setup  # the banner at start-up: resets are noticed
    dht = src[src.index("bool readDHT"):src.index("void reportEnc")]
    assert "noInterrupts" not in dht  # no 5 ms with the interrupts off
    hx = src[src.index("uint8_t hxPulse"):src.index("bool readHX")]
    assert "noInterrupts" in hx and "interrupts();" in hx  # off for one clock pulse only
    assert 'err(F("line too long"))' in src and "overflow = true" in src
    w = src[src.index("case 'W'"):src.index("case 'P'")]
    t = src[src.index("case 'T'"):src.index("case 'X'")]
    p = src[src.index("case 'P'"):src.index("case 'T'")]
    assert "o->timed = 0" in w and "o->timed = 0" in t and "o->timed = 0" in p and "offAt" in p
    assert "t.ms" in t and "millis()" in t
    assert "a.deadband < 0" in src and "int16_t deadband" in src
    assert re.search(r"if \(outs\[i\]\.pwm\) writePwm\(&outs\[i\], 0\)", src)


def test_firmware_readme_documents_protocol_1_2():
    txt = (FIRMWARE.parents[1] / "README.md").read_text()
    assert "P pin 0..255 [max_ms]" in txt and "deadband -1" in txt and "line too long" in txt
    assert "banner" in txt and "70 %" in txt and "60 s" in txt


def test_filtered_channels_ask_for_every_sample_from_firmware_1_2():
    ch = {"name": "force", "kind": "analog", "pin": 0, "period_ms": 20, "filter": "average", "window": 5}
    for version, want in (("1.2", "A 0 20 -1"), ("1.1", "A 0 20 0")):
        port = FakePort(version)
        dm = DeviceManager([{"name": "b", "type": "arduino", "port": "x", "watchdog_ms": 0, "channels": [ch]}],
                           transports={"b": port})
        assert want in port.written
        dm.close()


def test_board_reset_is_noticed_and_the_board_configured_again():
    port = FakePort("1.2")
    dm = DeviceManager([_box()], transports={"box": port})
    dev = dm.devices["box"]
    dm.set_output("box", "pellet", 1)
    dev._query_until = 0.0  # long after the handshake
    port.written.clear()
    port.feed("MANYMAZE_IO 1.2 uno")  # printed by setup(): the board restarted
    ch = dm.read_inputs_ex()
    assert ("box", "", "watchdog", 1, None) in ch and dev.resets == 1
    assert port.written[0] == "Z" and "O 8 0" in port.written and dev.outputs["pellet"] == 0
    assert any("restarted" in e for e in dm.errors)
    dm.close()


def test_lost_device_is_reopened_by_the_service_thread():
    port = FakePort()
    dm = DeviceManager([_box(watchdog=2000)], transports={"box": port})
    dm.RECONNECT_S = 0.1
    dev = dm.devices["box"]
    port.fail = True
    assert dm.set_output("box", "pellet", 1) is False and not dev.connected
    port.fail = False
    assert _wait(lambda: dev.connected, 5.0)
    assert any("connection restored" in e for e in dm.errors) and "Z" in port.written
    dm.close()


# ====================================================================== serial bandwidth
def test_bandwidth_is_checked_and_too_many_samples_refused():
    fast = {"name": "b", "type": "arduino", "port": "x", "watchdog_ms": 0, "channels": [
        {"name": f"a{i}", "kind": "analog", "pin": i, "period_ms": 1} for i in range(4)]}
    msgs, refuse = bandwidth_check(fast)
    assert refuse and "not configured" in msgs[0]
    port = FakePort()
    dm = DeviceManager([fast], transports={"b": port})
    assert not any(w.startswith("A ") for w in port.written) and any("bytes/s" in e for e in dm.errors)
    dm.close()
    one = dict(fast, channels=fast["channels"][:1])  # one 1 kHz channel: ~62 % of 115200 baud
    assert bandwidth_check(one) == ([], False)
    two = dict(fast, channels=fast["channels"][:2])  # two saturate the link
    assert bandwidth_check(two)[1]
    warn = dict(fast, channels=fast["channels"][:1] + [dict(fast["channels"][1], period_ms=5)])  # ~90 %
    msgs, refuse = bandwidth_check(warn)
    assert msgs and msgs[0].startswith("warning") and not refuse


# ====================================================================== polling / balances
def test_a_balance_being_read_delays_no_other_device():
    class Silent:
        in_waiting = 0

        def write(self, data):
            pass

        def read(self, n):
            return b""

        def close(self):
            pass

    dm = DeviceManager([{"name": "scale", "type": "scale", "port": "x", "protocol": "mt_sics", "poll_s": 0},
                        {"name": "box", "type": "virtual", "channels": [{"name": "shock", "kind": "output"}]}],
                       transports={"scale": Silent()})
    scale = dm.devices["scale"]
    errs = []

    def weigh():
        try:
            scale.read(timeout=1.0)
        except ScaleTimeout as e:
            errs.append(e)

    th = threading.Thread(target=weigh)
    th.start()
    hold = threading.Thread(target=lambda: (scale._io_lock.acquire(), time.sleep(0.8), scale._io_lock.release()))
    hold.start()  # the port held for a long time by another thread (a reading in progress)
    time.sleep(0.05)
    t0 = time.monotonic()
    dm.read_inputs()  # the poll skips the busy balance
    dm.set_output("box", "shock", 0)
    assert time.monotonic() - t0 < 0.2
    th.join(3)
    hold.join(3)
    assert errs  # the weighing itself still times out normally
    dm.close()


def test_balance_read_sees_replies_taken_by_a_poll():
    class Port:
        def __init__(self):
            self.incoming = b""
            self.lk = threading.Lock()

        @property
        def in_waiting(self):
            return len(self.incoming)

        def write(self, data):
            if data.startswith(b"SI"):
                threading.Timer(0.05, self._reply).start()

        def _reply(self):
            with self.lk:
                self.incoming += b"S S     21.50 g\r\n"

        def read(self, n):
            with self.lk:
                out, self.incoming = self.incoming[:n], self.incoming[n:]
            return out

        def close(self):
            pass

    dev = ScaleDevice({"name": "s", "port": "x", "protocol": "mt_sics", "poll_s": 0}, Port())
    dev.open()
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            dev.poll()
            time.sleep(0.001)

    th = threading.Thread(target=poll)
    th.start()
    try:
        assert dev.read(timeout=2.0) == (21.5, True)
    finally:
        stop.set()
        th.join()


def test_each_test_reads_only_its_own_box():
    pa, pb = FakePort(), FakePort()
    dm = DeviceManager([_box("a"), _box("b")], transports={"a": pa, "b": pb})
    view = DeviceView(dm, "a")
    before = pb.reads
    for _ in range(20):
        view.read_inputs()
    assert pb.reads == before and pa.reads > 0
    pb.feed("D 2 0 5")
    assert view.read_inputs() == []  # b's lever never reaches the test of box a
    dm.close()


def test_scale_units():
    assert parse_weight("S S     1.000 ozt", "mt_sics")[0] == pytest.approx(31.1034768)
    assert parse_weight("     2 dwt", "kern")[0] == pytest.approx(3.11034768)
    with pytest.raises(ScaleError):
        parse_weight("S S     1.000 tl", "mt_sics")  # unknown (regional) unit: never read as grams
    with pytest.raises(ScaleError):
        parse_weight("   12 pcs", "continuous")
    assert parse_weight("S S     12.34 g", "mt_sics") == (12.34, True)


# ====================================================================== I/O log growth / autosave / recovery
def test_fast_samples_kept_out_of_the_live_log_and_saved_whole(tmp_path):
    port = FakePort()
    cfg = {"name": "box", "type": "arduino", "port": "x", "watchdog_ms": 0, "channels": [
        {"name": "lick", "kind": "analog", "pin": 1, "period_ms": 1, "deadband": 0},
        {"name": "lever", "kind": "input", "pin": 2}]}
    dm = DeviceManager([cfg], transports={"box": port})
    side = tmp_path / "t.autosave.json"
    s = _session(duration_s=0, devices=dm, autosave_path=str(side), autosave_s=1000)
    s._io_log.chunk = 300  # small chunks: the side file is written during the test
    ms = 1000
    for i in range(50):  # 2 s at 25 fps, 40 samples (1 kHz) per frame
        lines = []
        for _b in range(4):
            lines.append("S 1 %d 1000 %s" % (ms, " ".join(str((ms + k) % 1024) for k in range(10))))
            ms += 10
        port.feed(*lines)
        if i == 20:
            port.feed("D 2 0 %d" % ms)
        s.process(_frame(60 + i), i / 25)
    live = [e for e in s.engine.io_events if e["channel"] == "lick"]
    assert 15 <= len(live) <= 25 and all(e.get("decimated") for e in live)  # ~10 per second
    full = [e for e in s.all_io_events() if e["channel"] == "lick"]
    assert len(full) == 2000 and not any(e.get("decimated") for e in full)
    rows = s.input_rows()
    assert dict((r[0], r[2]) for r in rows)["lever"] == 1 and input_rows(s.input_stats, s.elapsed) == rows
    s.flush_autosave()
    d = json.loads(side.read_text())
    assert not [e for e in d["io_events"] if e["channel"] == "lick"]  # samples are not in the JSON
    assert d["io_samples"]["rows"] == 2000 and Path(d["io_samples"]["file"]).exists()
    rec = RecoveredSession(d)
    assert len([e for e in rec.io_events if e["channel"] == "lick"]) == 2000
    s.finish()
    s.remove_autosave()
    assert not Path(d["io_samples"]["file"]).exists()
    dm.close()


def test_autosave_backs_off_after_slow_writes(tmp_path):
    calls = []

    def snap():
        calls.append(time.monotonic())
        time.sleep(0.05)
        return {"x": 1}

    a = Autosaver(str(tmp_path / "a.json"), snap, lambda m: None)
    a.flush()
    assert a.last_write_s >= 0.05
    a.request()  # within BACKOFF x 0.05 s of the last write: ignored
    a._join(1)
    assert len(calls) == 1
    a.request(force=True)
    a._join(1)
    assert len(calls) == 2
    a.remove()


def test_recovered_test_keeps_moved_zones_labels_schedules_weights_and_kept_variables(tmp_path):
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    test = proj.add_test("", "A1", "")
    side = proj.path / "recordings" / "test_0001_A1.autosave.json"
    side.parent.mkdir(exist_ok=True)
    s = _session([proc({"type": "var", "name": "streak", "value": "3", "keep": "animal"})], duration_s=0,
                 autosave_path=str(side), autosave_meta={"test_id": test.id, "animal": "A1"},
                 test_info={"test": {"animal": "A1"}})
    for i in range(20):
        s.process(_frame(60 + i), i / 25)
    eng = s.engine
    eng.zone_labels["Centre"] = "novel"
    eng.scheduled_tests.append({"t": 0.4, "stage": "Retest", "apparatus": "", "delay_min": 60})
    eng.animal_weights.append((0.5, 24.5))
    s.procedure_zone_overrides["Centre"] = {"x": 1}
    s.video_labels.append({"t": 0.2, "video_t": 0.2, "text": "go", "file": "v.mp4"})
    s.recording_log.append((0.2, "label go"))
    s.record_parts.extend(["a.mp4", "a_part2.mp4"])
    s.flush_autosave()
    d = json.loads(side.read_text())
    assert d["kept_variables"] and d["zone_labels"] == {"Centre": "novel"}
    proj2 = Project.load(proj.path)
    [t] = recover_autosaves(proj2)
    assert t.variables["zone_labels"] == {"Centre": "novel"} and t.variables["video_labels"][0]["text"] == "go"
    assert t.zone_overrides["Centre"] == {"x": 1}
    assert any(x.stage == "Retest" for x in proj2.tests)
    assert proj2.get_animal("A1").weights[-1]["grams"] == 24.5
    assert "Recorded files" in t.notes and "Video recording" in t.notes
    assert proj2.variables.get("@animal", {}).get("A1", {}).get("streak") is not None
    s.finish()


def test_recovery_never_overwrites_another_animals_test(tmp_path):
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    test = proj.add_test("", "A1", "")
    proj.save()
    side = proj.path / "recordings" / "x.autosave.json"
    side.parent.mkdir(exist_ok=True)
    s = _session(duration_s=0, autosave_path=str(side), autosave_meta={"test_id": test.id, "animal": "B7"})
    for i in range(10):
        s.process(_frame(60 + i), i / 25)
    s.flush_autosave()
    s.finish()
    proj2 = Project.load(proj.path)
    rec = recover_autosaves(proj2)
    assert len(rec) == 1 and rec[0].id != test.id and rec[0].animal_id == "B7"
    assert proj2.get_test(test.id).status != "tracked"


# ====================================================================== recorder
def test_stalled_encoder_is_a_recording_error_not_a_hang():
    go = threading.Event()

    class Stuck:
        def write(self, frame):
            go.wait(5)

        def close(self):
            pass

    rt = _RecordingThread(Stuck(), maxsize=1)
    rt.PUT_TIMEOUT_S = 0.1
    rt.write(0)
    time.sleep(0.05)
    rt.write(1)  # fills the queue
    t0 = time.monotonic()
    with pytest.raises(OSError):
        rt.write(2)
    assert time.monotonic() - t0 < 1.0
    go.set()
    rt.close(timeout=3)


def test_stopped_recording_is_closed_in_the_background():
    s = _session(duration_s=0)
    s.process(_frame(), 0.0)
    done = []

    class SlowClose:
        def close(self):
            time.sleep(0.5)
            done.append(True)

    with s.lock:
        s.recorder = SlowClose()
        s._close_recorder()  # e.g. a procedure's "stop video recording"
    t0 = time.monotonic()
    s._close_pending_recorders()
    assert time.monotonic() - t0 < 0.2 and not done  # the frame thread does not wait for the encoder
    assert s.wait_recordings(3) and done
    s.finish()


def test_split_recording_labels_name_the_part_file():
    s = _session(duration_s=0, fps=10.0, split_minutes=1.0)
    s.record_parts.append("/rec/test.mp4")
    s._rec_frames = 600 + 25  # 2nd part (600 frames each), 2.5 s into it
    part, t = s._recording_position()
    assert part == "/rec/test_part002.mp4" and t == pytest.approx(2.5)
    s.finish()


# ====================================================================== pumps / actions
class NewEra:
    def __init__(self):
        self.written = []
        self.incoming = b""
        self.status = "S"

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        line = data.decode().strip()
        self.written.append(line)
        if line.endswith("RUN"):
            self.status = "I"
        elif line.endswith("STP"):
            self.status = "P"
        self.incoming += f"\x0200{self.status}\x03".encode()

    def read(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def close(self):
        pass


NE = {"name": "p", "type": "syringe_pump", "port": "x", "protocol": "new_era", "channels": [
    {"name": "p1", "kind": "pump", "syringe": "BD Plastipak 10 ml"}]}


def test_new_parameters_while_running_stop_the_pump_first():
    port = NewEra()
    dev = SyringePumpDevice(NE, port)
    dev.open()
    assert dev.pump("p1", "infuse", rate_ml_min=1)
    dev.poll()
    assert dev.pumps["p1"].running
    port.written.clear()
    assert dev.pump("p1", "infuse", rate_ml_min=2)
    assert port.written[0] == "0STP" and port.written[-1] == "0RUN"


def test_new_era_safe_mode_frames_packets():
    port = NewEra()
    dev = SyringePumpDevice(dict(NE, safe_mode_s=10), port)
    port.write = lambda data: port.written.append(bytes(data))
    dev.open()
    assert b"0SAF10\r" in port.written and dev.safe_mode_s == 10
    port.written.clear()
    dev.pump("p1", "stop")
    pkt = port.written[0]
    assert pkt == NewEraProtocol.encode_safe("0STP") and pkt[1] == len(b"0STP") + 3
    assert dev.keepalive_period() == pytest.approx(10 / 3)
    body = b"00S"
    frame = b"\x02" + bytes([len(body) + 3]) + body + crc16(body).to_bytes(2, "big") + b"\x03"
    frames, rest = NewEraProtocol().split(frame + b"\x0200I\x03")
    assert frames == ["00S", "00I"] and rest == b""
    bad = b"\x02" + bytes([6]) + body + b"\x00\x00\x03"
    assert NewEraProtocol().split(bad)[0] == []  # wrong CRC: dropped
    assert crc16(b"123456789") == 0x31C3  # CRC-16/XMODEM check value


def test_text_pump_without_stop_template_reports_failure():
    class Port:
        in_waiting = 0

        def write(self, data):
            pass

        def read(self, n):
            return b""

        def close(self):
            pass

    dev = SyringePumpDevice({"name": "t", "protocol": "text", "port": "x", "commands": {"infuse": "GO"},
                             "channels": [{"name": "a", "kind": "pump", "diameter_mm": 10}]}, Port())
    dev.open()
    assert dev.pump("a", "infuse", rate_ml_min=1) and dev.pump("a", "stop") is False
    assert any("stop" in e for e in dev.errors)


def test_pump_infusion_not_logged_when_the_command_failed():
    from manymaze.core.procedures import ProcedureEngine

    dm = DeviceManager([{"name": "p", "type": "syringe_pump", "protocol": "new_era", "channels": [
        {"name": "p1", "kind": "pump"}]}], open=False)  # never opened: commands cannot be sent
    eng = ProcedureEngine([proc(DO("pump_infuse", channel="p1", rate=1, volume=0.1))], dm)
    eng.start(0)
    eng.update_state(0.1, {})
    assert not [e for e in eng.io_events if e.get("type") == "pump"] and ("p", "p1") not in eng._pumps_on
    eng.stop(1)


# ====================================================================== one failing test among several
def test_one_sessions_error_ends_only_that_test():
    g = LiveGroup()
    good, bad = _session(duration_s=0), _session(duration_s=0)
    g.add_session("cam", good)
    g.add_session("cam", bad)

    def boom(*a, **k):
        raise RuntimeError("tracker exploded")

    bad._process = boom
    g.process("cam", _frame(), 0.0)
    g.process("cam", _frame(), 0.04)
    assert bad.state == "finished" and bad.end_reason == END_ERROR
    assert good.state == "running" and len(good.cols["t"]) == 2
    assert any("tracker exploded" in w for _t, w in g.warnings)
    good.finish()


# ====================================================================== heartbeats gated on a running program
def test_heartbeats_stop_when_the_tests_stop_ticking():
    port = FakePort()
    dm = DeviceManager([_box(watchdog=300)], transports={"box": port})
    dm.TICK_TIMEOUT_S = 0.3
    owner = object()
    dm.tick(owner)
    time.sleep(0.5)  # the program "hangs": no tick for longer than the timeout
    port.written.clear()
    time.sleep(0.4)
    assert "." not in port.written  # the board's watchdog will switch the outputs off
    dm.untick(owner)  # the test is over: heartbeats again (outside tests)
    assert _wait(lambda: "." in port.written, 2.0)
    dm.close()


def test_live_session_ticks_the_devices_and_unticks_at_the_end():
    port = FakePort()
    dm = DeviceManager([_box(watchdog=300)], transports={"box": port})
    s = _session(duration_s=0, devices=dm)
    s.process(_frame(), 0.0)
    assert id(s) in dm._ticks
    s.finish()
    assert id(s) not in dm._ticks
    dm.close()
