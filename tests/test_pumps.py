"""Syringe pumps: syringe table, pump models, protocols (New Era, Harvard ultra / legacy, Chemyx, Cavro DT, text) with
fake serial ports that emulate the pumps, and the simulated pump."""

import re

import pytest

from manymaze.core import pumps
from manymaze.core.pumps import PUMP_MODELS, PUMP_PROTOCOLS, SYRINGES, SyringePumpDevice, find_syringe


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakePump:
    """Base fake port: splits written bytes on the line end and answers each command through handle()."""

    eol = "\r"

    def __init__(self):
        self.written = []
        self.incoming = b""
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def write(self, data):
        for line in data.decode().split(self.eol):
            if line:
                self.written.append(line)
                self.handle(line)

    def feed(self, data):
        self.incoming += data.encode("latin-1") if isinstance(data, str) else data

    def close(self):
        self.closed = True


class FakeNewEra(FakePump):
    def __init__(self):
        super().__init__()
        self.pumps = {}

    def st(self, addr):
        return self.pumps.setdefault(addr, {"status": "S", "dir": "INF", "inf": 0.0, "wdr": 0.0, "alarm": None})

    def handle(self, line):
        m = re.match(r"(\d*)(.*)", line)
        addr, cmd = int(m.group(1) or 0), m.group(2)
        st = self.st(addr)
        if st["alarm"]:
            self.feed(f"\x02{addr:02d}A?{st['alarm']}\x03")
            st["alarm"] = None
            st["status"] = "S"  # a stalled pump stops
            return
        data = ""
        if cmd == "RUN":
            st["status"] = "I" if st["dir"] == "INF" else "W"
        elif cmd == "STP":
            st["status"] = "P"
        elif cmd.startswith("DIR "):
            st["dir"] = cmd[4:]
        elif cmd == "DIS":
            data = f"I{st['inf']:.3f}W{st['wdr']:.3f}ML"
        elif cmd == "CLD INF":
            st["inf"] = 0.0
        elif cmd == "CLD WDR":
            st["wdr"] = 0.0
        elif cmd.startswith("DIA 0"):
            data = "?OOR"
        self.feed(f"\x02{addr:02d}{st['status']}{data}\x03")


def step(dev, clock, dt=0.25, n=3):
    """Advance the clock and poll a few times (queries go out, then their replies are read)."""
    out = []
    for _ in range(n):
        clock.t += dt
        out += dev.poll()
    return out


def test_syringe_table_and_lookup():
    assert len(SYRINGES) >= 120
    assert len({s["manufacturer"] for s in SYRINGES}) >= 14
    names = [pumps.syringe_name(s) for s in SYRINGES]
    assert len(names) == len(set(names))
    for s in SYRINGES:
        assert set(s) >= {"manufacturer", "model", "volume_ml", "diameter_mm"}
        assert 0.1 < s["diameter_mm"] < 50 and s["volume_ml"] > 0
    assert find_syringe("BD Plastipak 10 ml")["diameter_mm"] == 14.5
    assert find_syringe("bd plastipak 10ml")["diameter_mm"] == 14.5
    assert find_syringe("Hamilton Gastight 1700 series 100 ul")["diameter_mm"] == pytest.approx(1.457)
    assert find_syringe("Terumo 10 cc")["diameter_mm"] == 15.8
    assert find_syringe("BD") is None and find_syringe("nothing") is None
    # Hamilton gastight barrels: 60 mm scale length
    for s in SYRINGES:
        if s["model"].startswith("Gastight 1000"):
            area_mm2 = 3.14159265 * (s["diameter_mm"] / 2) ** 2
            assert area_mm2 * 60 / 1000 == pytest.approx(s["volume_ml"], rel=0.01)
    c = pumps.custom_syringe(12.3, 7)
    assert c["diameter_mm"] == 12.3 and find_syringe("My syringe", [{**c, "name": "My syringe"}])["volume_ml"] == 7
    with pytest.raises(ValueError):
        pumps.custom_syringe(0)


def test_pump_models_and_protocols():
    assert len(pumps.pump_manufacturers()) >= 20
    assert {m["protocol"] for m in PUMP_MODELS} <= set(PUMP_PROTOCOLS)
    assert pumps.protocol_of("Pump 11 Elite") == "harvard_ultra" and pumps.protocol_of("NE-1000") == "new_era"
    assert pumps.protocol_of("Legato 110") == "harvard_ultra" and pumps.protocol_of("Aladdin AL-1000") == "new_era"
    assert pumps._ne_num(12.345) == "12.35" and pumps._ne_num(0.1234) == "0.123" and pumps._ne_num(2) == "2"


NE_CFG = {"name": "pumps", "type": "syringe_pump", "port": "x", "protocol": "new_era", "channels": [
    {"name": "p1", "kind": "pump", "address": 0, "syringe": "BD Plastipak 10 ml"},
    {"name": "p2", "kind": "pump", "address": 1, "diameter_mm": 26.7, "rate_ml_min": 2}]}


def test_new_era_protocol():
    port, clock = FakeNewEra(), Clock()
    dev = SyringePumpDevice(NE_CFG, port)
    dev.clock = clock
    assert dev.cfg["baud"] == 19200 and dev.cfg["eol"] == "\r"
    for ch in ("p1.running", "p1.stalled", "p1.target_reached", "p2.infused_ml", "p2.withdrawn_ml"):
        assert ch in dev.channels and dev.inputs[ch] == 0
    assert dev.kind("p1.running") == "status" and dev.kind("p1.infused_ml") == "analog"
    dev.open()
    assert port.written[:3] == ["0DIA 14.5", "0CLD INF", "0CLD WDR"] and "1DIA 26.7" in port.written
    step(dev, clock)
    assert not dev.errors
    port.written.clear()
    assert dev.pump("p1", "infuse", rate_ml_min=0.5, volume_ml=0.2)
    assert port.written == ["0DIS", "0CLD INF", "0CLD WDR", "0DIR INF", "0RAT 500 UM", "0VOL UL", "0VOL 200",
                            "0RUN"]
    ch = step(dev, clock)
    assert ("p1.running", 1) in ch and dev.outputs["p1"] == 0.5
    port.st(0)["inf"] = 0.1
    ch = step(dev, clock)
    assert ("p1.infused_ml", 0.1) in ch and dev.inputs["p1.running"] == 1
    port.st(0)["inf"], port.st(0)["status"] = 0.2, "S"  # target volume delivered: the pump stops
    ch = step(dev, clock, n=1) + step(dev, clock, n=1)
    assert ("p1.running", 0) in ch and ("p1.target_reached", 1) in ch
    ch = step(dev, clock)
    assert ("p1.target_reached", 0) in ch and dev.inputs["p1.infused_ml"] == pytest.approx(0.2)
    # a second run: the counters are cleared, the totals go on
    port.written.clear()
    assert dev.pump("p1", "infuse", rate_ml_min=2, volume_ml=0)
    assert "0RAT 2 MM" in port.written and "0VOL 0" in port.written
    step(dev, clock)
    port.st(0)["inf"] = 0.05
    step(dev, clock)
    assert dev.inputs["p1.infused_ml"] == pytest.approx(0.25)
    assert dev.pump("p1", "stop")
    ch = step(dev, clock)
    assert dev.inputs["p1.running"] == 0 and ("p1.target_reached", 1) not in ch
    # withdraw on the second pump of the chain, then a stall alarm
    port.written.clear()
    assert dev.pump("p2", "withdraw", rate_ml_min=0.01)
    assert "1DIR WDR" in port.written and "1RAT 10 UM" in port.written and "1VOL 0" in port.written
    step(dev, clock)
    port.st(1)["wdr"] = 0.3
    step(dev, clock)
    assert dev.inputs["p2.withdrawn_ml"] == pytest.approx(0.3) and dev.inputs["p2.running"] == 1
    port.st(1)["alarm"] = "S"
    ch = step(dev, clock)
    assert ("p2.stalled", 1) in ch and dev.inputs["p2.running"] == 0
    # a new run clears the stall
    assert dev.pump("p2", "infuse")
    step(dev, clock)
    assert dev.inputs["p2.stalled"] == 0 and dev.inputs["p2.running"] == 1 and dev.outputs["p2"] == 2
    # error replies are recorded; all_off stops every pump
    assert dev.pump("p1", "set_syringe", diameter_mm=0.09)
    step(dev, clock)
    assert any("out of range" in e for e in dev.errors)
    port.written.clear()
    dev.all_off()
    assert port.written == ["0STP", "1STP"]


def test_pump_api_is_safe():
    port, clock = FakeNewEra(), Clock()
    dev = SyringePumpDevice(NE_CFG, port)
    dev.clock = clock
    dev.open()
    assert not dev.pump("nope", "infuse") and any("unknown pump" in e for e in dev.errors)
    assert not dev.pump("p1", "fly") and not dev.pump("p1", "infuse", rate_ml_min=-1)
    assert not dev.pump("p1", "set_syringe", syringe="No such syringe")
    assert dev.pump("p1", "set_syringe", syringe="Terumo 20 ml") and port.written[-1] == "0DIA 20.15"

    class Boom:
        in_waiting = 0

        def write(self, data):
            raise OSError("unplugged")

        def read(self, n):
            raise OSError("unplugged")

    dev2 = SyringePumpDevice(NE_CFG, Boom())
    dev2.open()
    assert dev2.pump("p1", "infuse") is False  # write errors are recorded, never raised
    dev2.poll()
    dev2.protocol.run = lambda *a: 1 / 0
    assert dev2.pump("p1", "infuse") is False and any("failed" in e for e in dev2.errors)
    # no syringe configured
    dev3 = SyringePumpDevice({"name": "d", "protocol": "new_era", "channels": [{"name": "p", "kind": "pump"}]},
                             FakeNewEra())
    dev3.open()
    assert not dev3.pump("p", "infuse") and any("no syringe" in e for e in dev3.errors)
    # no port
    dev4 = SyringePumpDevice({"name": "d", "protocol": "new_era", "channels": []})
    dev4.open()
    assert not dev4.connected and dev4.errors
    dev4.all_off()
    # replies that never come are reported
    silent = FakeNewEra()
    silent.handle = lambda line: None
    dev5 = SyringePumpDevice(NE_CFG, silent)
    dev5.clock = clock
    dev5.open()
    step(dev5, clock, dt=1.0)
    assert any("no reply" in e for e in dev5.errors)


class FakeHarvard(FakePump):
    """Harvard "ultra" command set: data lines then a prompt ("01:", ">", "<", "*", "T*")."""

    def __init__(self, legacy=False, direction="INFUSE"):
        super().__init__()
        self.legacy = legacy
        self.direction = direction
        self.state = {}

    def st(self, addr):
        return self.state.setdefault(addr, {"p": ":", "i": 0.0, "w": 0.0})

    def handle(self, line):
        m = re.match(r"(\d\d)?(.*)", line)
        addr, cmd = int(m.group(1) or 0), m.group(2)
        st = self.st(addr)
        data = []
        if cmd in ("irun", "RUN"):
            st["p"] = ">"
        elif cmd in ("wrun", "REV"):
            st["p"] = "<"
        elif cmd in ("stop", "STP"):
            st["p"] = ":"
        elif cmd == "ivolume":
            data = [f"{st['i'] * 1000:g} ul" if st["i"] < 1 else f"{st['i']:g} ml"]
        elif cmd == "wvolume":
            data = [f"{st['w']:g} ml"]
        elif cmd == "VOL":
            data = [f"{st['i']:g}"]
        elif cmd == "civolume" or cmd == "CLV":
            st["i"] = 0.0
        elif cmd == "cwvolume":
            st["w"] = 0.0
        elif cmd == "DIR":
            data = [self.direction]
        elif cmd.startswith("bogus"):
            data = ["Command error"]
        prompt = (f"{addr:02d}" if addr else "") + st["p"]
        self.feed("".join(f"\r\n{d}" for d in data) + "\r\n" + prompt)


def test_harvard_ultra_protocol():
    port, clock = FakeHarvard(), Clock()
    cfg = {"name": "h", "protocol": "harvard_ultra", "channels": [
        {"name": "a", "kind": "pump", "syringe": "Hamilton Gastight 1000 series 1 ml"},
        {"name": "b", "kind": "pump", "address": 1, "syringe": "BD Luer-Lok 60 ml"}]}
    dev = SyringePumpDevice(cfg, port)
    dev.clock = clock
    dev.open()
    assert port.written[:3] == ["diameter 4.608", "civolume", "cwvolume"] and "01diameter 26.59" in port.written
    port.written.clear()
    assert dev.pump("a", "infuse", rate_ml_min=0.02, volume_ml=0.5)
    assert port.written[-3:] == ["irate 20 ul/min", "tvolume 500 ul", "irun"]
    step(dev, clock)
    assert dev.inputs["a.running"] == 1 and dev.inputs["b.running"] == 0
    port.st(0)["i"] = 0.25
    step(dev, clock)
    assert dev.inputs["a.infused_ml"] == pytest.approx(0.25)
    port.st(0)["i"], port.st(0)["p"] = 0.5, "T*"
    ch = step(dev, clock, n=1) + step(dev, clock, n=1)
    assert ("a.target_reached", 1) in ch and dev.inputs["a.running"] == 0
    ch = step(dev, clock)  # the pump keeps answering "T*" until the next run: no new edge
    assert ("a.target_reached", 1) not in ch and dev.inputs["a.target_reached"] == 0
    assert dev.pump("b", "withdraw", rate_ml_min=3)
    assert port.written[-3:] == ["01wrate 3 ml/min", "01ctvolume", "01wrun"]
    step(dev, clock)
    assert dev.inputs["b.running"] == 1
    port.st(1)["w"] = 1.5
    port.st(1)["p"] = "*"
    step(dev, clock)
    assert dev.inputs["b.withdrawn_ml"] == pytest.approx(1.5) and dev.inputs["b.stalled"] == 1
    dev.send("x")  # Device.send is not a pump command: ignored
    port.written.clear()
    dev.all_off()
    assert port.written == ["stop", "01stop"]
    # the frame splitter keeps partial replies
    proto = pumps.HarvardUltraProtocol()
    frames, rest = proto.split(b"\r\n1.5 ml\r\n01")
    assert frames == [] and rest.endswith(b"01")
    frames, rest = proto.split(rest + b":\r\n2 ml\r\n>")
    assert frames == ["1.5 ml\n01:", "2 ml\n>"] and rest == b""


def test_harvard_legacy_protocol():
    port, clock = FakeHarvard(legacy=True), Clock()
    dev = SyringePumpDevice({"name": "h", "protocol": "harvard_legacy", "channels": [
        {"name": "a", "kind": "pump", "syringe": "BD Plastipak 20 ml"}]}, port)
    dev.clock = clock
    dev.open()
    assert port.written == ["MMD 19.13", "CLV", "DIR"]  # the direction the pump is set to is asked
    step(dev, clock)
    dev.pump("a", "infuse", rate_ml_min=0.5, volume_ml=2)
    assert port.written[-3:] == ["ULM 500", "MLT 2", "RUN"]
    step(dev, clock)
    port.st(0)["i"] = 2.0
    port.st(0)["p"] = ":"
    ch = step(dev, clock)
    assert ("a.target_reached", 1) in ch and dev.inputs["a.infused_ml"] == 2
    dev.pump("a", "withdraw", rate_ml_min=1)
    assert port.written[-2:] == ["CLT", "REV"]
    step(dev, clock)  # the VOL read before CLV belongs to the infusion, not to the withdrawal that follows
    assert dev.inputs["a.infused_ml"] == 2 and dev.inputs["a.withdrawn_ml"] == 0
    dev.pump("a", "withdraw", rate_ml_min=1)
    assert port.written[-1] == "RUN"  # already withdrawing


class FakeChemyx(FakePump):
    eol = "\r\n"

    def __init__(self):
        super().__init__()
        self.status, self.vol = 0, 0.0

    def handle(self, line):
        self.feed(line + "\r\n")  # echo
        if line == "start":
            self.status = 1
        elif line == "stop":
            self.status = 0
        elif line == "status":
            self.feed(f"{self.status}\r\n")
        elif line == "dispensed volume":
            self.feed(f"{self.vol} ml\r\n")


def test_chemyx_protocol():
    port, clock = FakeChemyx(), Clock()
    dev = SyringePumpDevice({"name": "c", "protocol": "chemyx", "eol": "\r\n", "channels": [
        {"name": "a", "kind": "pump", "syringe": "BD Plastipak 50 ml"}]}, port)
    dev.clock = clock
    dev.open()
    assert port.written == ["set units 0", "set diameter 26.6"]
    dev.pump("a", "withdraw", rate_ml_min=1.5, volume_ml=3)
    assert port.written[-3:] == ["set rate 1.5", "set volume -3", "start"]
    step(dev, clock)
    port.vol = 1.25
    step(dev, clock)
    assert dev.inputs["a.withdrawn_ml"] == pytest.approx(1.25) and dev.inputs["a.running"] == 1
    port.status = 4
    step(dev, clock)
    assert dev.inputs["a.stalled"] == 1 and dev.inputs["a.running"] == 0
    assert not dev.errors


class FakeCavro(FakePump):
    def __init__(self):
        super().__init__()
        self.pos, self.busy, self.err = 3000, False, 0

    def handle(self, line):
        assert line.startswith("/1")
        cmd = line[2:]
        code = (0x40 if self.busy else 0x60) | self.err
        data = str(self.pos) if cmd == "?" else ""
        if cmd.endswith("R") and cmd != "TR":
            self.busy = True
        if cmd == "TR":
            self.busy = False
        self.feed(b"\xff/0" + bytes([code]) + data.encode() + b"\x03\r\n")


def test_cavro_dt_protocol():
    port, clock = FakeCavro(), Clock()
    dev = SyringePumpDevice({"name": "c", "protocol": "cavro_dt", "channels": [
        {"name": "a", "kind": "pump", "syringe_volume_ml": 1.0, "full_steps": 3000, "valve": True}]}, port)
    dev.clock = clock
    dev.open()
    assert port.written == ["/1?"]
    step(dev, clock)
    assert dev.pump("a", "infuse", rate_ml_min=0.6, volume_ml=0.1)
    assert port.written[-1] == "/1OV30D300R"  # 0.01 ml/s = 30 steps/s; 0.1 ml = 300 steps
    step(dev, clock)
    assert dev.inputs["a.running"] == 1
    port.pos, port.busy = 2700, False
    ch = step(dev, clock)
    assert dev.inputs["a.infused_ml"] == pytest.approx(0.1) and ("a.target_reached", 1) in ch
    assert dev.pump("a", "withdraw", rate_ml_min=0.6)
    assert port.written[-1] == "/1IV30A3000R"
    step(dev, clock)
    port.err = 9  # plunger overload
    step(dev, clock)
    assert dev.inputs["a.stalled"] == 1
    no_vol = SyringePumpDevice({"name": "c", "protocol": "cavro_dt", "channels": [{"name": "a", "kind": "pump"}]},
                               FakeCavro())
    no_vol.open()
    assert not no_vol.pump("a", "infuse") and any("syringe volume" in e for e in no_vol.errors)


def test_text_protocol():
    port, clock = FakePump(), Clock()
    port.handle = lambda line: None
    port.eol = "\n"
    cfg = {"name": "t", "protocol": "text", "eol": "\n",
           "commands": {"diameter": "D{addr} {diameter_mm}", "infuse": "I{addr} {rate_ul_min:g} {volume_ul:g}",
                        "withdraw": "W{addr} {rate_ul_min:g} {volume_ul:g}", "stop": "S{addr}", "poll": "?{addr}"},
           "replies": {"running": r"^(?P<addr>\d) RUN", "stopped": r"^(?P<addr>\d) IDLE", "stalled": r"^(?P<addr>\d) STALL",
                       "infused": r"^(?P<addr>\d) .*INF=([\d.]+)"},
           "channels": [{"name": "a", "kind": "pump", "diameter_mm": 10}]}
    dev = SyringePumpDevice(cfg, port)
    dev.clock = clock
    dev.open()
    assert port.written == ["D0 10.0"]
    dev.pump("a", "infuse", rate_ml_min=0.1, volume_ml=0.05)
    assert port.written[-1] == "I0 100 50"
    port.feed("0 RUN INF=0.02\n")
    step(dev, clock)
    assert dev.inputs["a.running"] == 1
    port.feed("0 IDLE INF=0.05\n")
    ch = step(dev, clock)
    assert ("a.target_reached", 1) in ch
    port.feed("0 STALL\n")
    step(dev, clock)
    assert dev.inputs["a.stalled"] == 1 and "?0" in port.written


def test_simulated_pump():
    clock = Clock()
    dev = SyringePumpDevice({"name": "sim", "protocol": "simulated", "channels": [
        {"name": "p", "kind": "pump", "syringe": "BD Plastipak 10 ml"}]})
    dev.clock = clock
    dev.open()
    assert dev.connected and dev.pump("p", "infuse", rate_ml_min=6, volume_ml=1)  # 0.1 ml/s
    clock.t += 5
    dev.poll()
    assert dev.inputs["p.infused_ml"] == pytest.approx(0.5) and dev.inputs["p.running"] == 1
    clock.t += 10
    ch = dev.poll()
    assert ("p.target_reached", 1) in ch and dev.inputs["p.infused_ml"] == pytest.approx(1.0)
    assert ("p.target_reached", 0) in dev.poll()
    dev.pump("p", "withdraw", rate_ml_min=60)  # until stopped
    clock.t += 1
    dev.poll()
    dev.simulate_stall("p")
    clock.t += 1
    ch = dev.poll()
    assert ("p.stalled", 1) in ch and dev.inputs["p.withdrawn_ml"] == pytest.approx(1.0)
    dev.simulate_stall("p", False)
    dev.set_output("p", 1)  # output-style control: infuse at the channel's rate
    clock.t += 60
    dev.poll()
    assert dev.inputs["p.infused_ml"] == pytest.approx(2.0) and dev.inputs["p.stalled"] == 0
    dev.all_off()
    dev.poll()
    assert dev.inputs["p.running"] == 0 and dev.outputs["p"] == 0
    bad = SyringePumpDevice({"name": "x", "protocol": "warp", "channels": []})
    assert bad.simulated and any("unknown pump protocol" in e for e in bad.errors)
