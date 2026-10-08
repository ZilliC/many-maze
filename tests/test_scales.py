"""Animal scales: reply parsing of each protocol, read_weight / ScaleDevice with fake balances, weight records and
their round trip through save / load."""

import pytest

from manymaze.core import scales
from manymaze.core.project import Animal, Project
from manymaze.core.scales import ScaleDevice, ScaleError, ScaleTimeout, parse_weight, read_weight


class FakeBalance:
    """Answers each query with the next scripted reply (the last one repeats); records the commands."""

    def __init__(self, replies, query=None):
        self.replies = list(replies)
        self.query = query
        self.stream = []
        self.written = []
        self.incoming = b""
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.incoming) or len(self.stream)

    def read(self, n):
        if not self.incoming and self.stream:  # a balance that sends by itself: one line per read
            self.incoming = self.stream.pop(0)
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def write(self, data):
        cmd = data.decode().strip("\r\n")
        self.written.append(cmd)
        if self.query is None or cmd == self.query:
            r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            if r is not None:
                self.incoming += (r + "\r\n").encode()

    def close(self):
        self.closed = True


@pytest.mark.parametrize("protocol,line,expected", [
    ("mt_sics", "S S     12.34 g", (12.34, True)),
    ("mt_sics", "S D     12.30 g", (12.30, False)),
    ("mt_sics", "S S      0.05 kg", (50.0, True)),
    ("mt_sics", "S I", None),
    ("ohaus", "     25.10 g", (25.10, True)),
    ("ohaus", "     25.10 g   ?", (25.10, False)),
    ("ohaus", "      1.25 oz", (1.25 * 28.349523125, True)),
    ("sartorius", "+     31.27 g  ", (31.27, True)),
    ("sartorius", "+     31.20    ", (31.20, False)),
    ("sartorius", "N     +     31.27 g ", (31.27, True)),
    ("sartorius", "-      0.10 g", (-0.10, True)),
    ("and", "ST,+00012.34  g", (12.34, True)),
    ("and", "US,+00012.30  g", (12.30, False)),
    ("and", "ST,+00000.25 kg", (250.0, True)),
    ("kern", "      18.4 g", (18.4, True)),
    ("kern", "    1200 mg", (1.2, True)),
    ("continuous", "Net  0.0441 lb", (0.0441 * 453.59237, True)),
    ("continuous", "ST,GS,+  22.50g", (22.5, True)),
    ("continuous", "US,GS,+  22.40g", (22.4, False)),
    ("continuous", "hello", None),
])
def test_parse_weight(protocol, line, expected):
    r = parse_weight(line, protocol)
    if expected is None:
        assert r is None
    else:
        assert r[0] == pytest.approx(expected[0]) and r[1] == expected[1]


def test_parse_errors():
    for proto, line in (("mt_sics", "S +"), ("mt_sics", "S -"), ("mt_sics", "ES"), ("and", "OL,+9999999 g")):
        with pytest.raises(ScaleError):
            parse_weight(line, proto)
    assert parse_weight("12.5", "continuous", unit="kg") == (12500.0, True)


def test_read_weight_waits_for_a_stable_weight():
    port = FakeBalance(["S D     20.10 g", "S D     20.30 g", "S S     20.25 g"], query="SI")
    g, stable = read_weight({"name": "mt", "type": "scale", "protocol": "mt_sics"}, port, timeout=3)
    assert (g, stable) == (20.25, True) and port.written.count("SI") == 3
    # any weight
    port = FakeBalance(["S D     20.10 g"], query="SI")
    assert read_weight({"name": "mt", "protocol": "mt_sics"}, port, stable=False) == (20.10, False)
    # Kern asks for a stable weight with "s"; Sartorius with ESC P; A&D with Q; Ohaus with IP
    for proto, query, reply, want in (("kern", "s", "   30.0 g", 30.0), ("sartorius", "\x1bP", "+  30.5 g", 30.5),
                                      ("and", "Q", "ST,+00031.00  g", 31.0), ("ohaus", "IP", " 31.5 g", 31.5)):
        port = FakeBalance([reply], query=query)
        assert read_weight({"name": proto, "protocol": proto}, port) == (want, True)
        assert port.written[0] == query


def test_read_weight_errors():
    port = FakeBalance(["S D     20.10 g"], query="SI")
    with pytest.raises(ScaleTimeout, match="last reading 20.1 g"):
        read_weight({"name": "mt", "protocol": "mt_sics"}, port, timeout=0.3)
    with pytest.raises(ScaleTimeout):
        read_weight({"name": "mt", "protocol": "mt_sics"}, FakeBalance([None]), timeout=0.2)
    with pytest.raises(ScaleError, match="overload"):
        read_weight({"name": "mt", "protocol": "mt_sics"}, FakeBalance(["S +"]), timeout=1)
    with pytest.raises(ScaleError, match="no serial port"):
        read_weight({"name": "mt", "protocol": "mt_sics"})
    with pytest.raises(ScaleError, match="unknown scale protocol"):
        read_weight({"name": "x", "protocol": "nope"}, FakeBalance(["1 g"]))


def test_scale_device_channels():
    port = FakeBalance(["S S     41.00 g"], query="SI")
    dev = ScaleDevice({"name": "s", "type": "scale", "protocol": "mt_sics", "poll_s": 0.01}, port)
    assert dev.kind("weight") == "analog" and dev.inputs["weight"] == 0
    dev.open()
    dev.poll()  # sends the first query
    assert ("weight", 41.0) in dev.poll()
    assert dev.inputs["weight.stable"] == 1
    assert dev.read(1.0) == (41.0, True)
    # a continuously sending balance
    port = FakeBalance(["x"], query="never")
    dev = ScaleDevice({"name": "c", "protocol": "continuous"}, port)
    dev.open()
    port.incoming = b"ST,GS,+  22.50g\r\nUS,GS,+  22.90g\r\n"
    assert dev.poll() == [("weight", 22.5), ("weight.stable", 1), ("weight", 22.9), ("weight.stable", 0)]
    assert port.written == []
    port.incoming = b"ST,GS,+  99.00g\r\n"  # old output is discarded: read() waits for a new weight
    port.stream = [b"US,GS,+  22.70g\r\n", b"ST,GS,+  23.00g\r\n"]
    assert dev.read(1.0) == (23.0, True)


def test_record_weight_round_trip(tmp_path):
    p = Project(name="w")
    p.animals.append(Animal("M1"))
    p.io_devices = [{"name": "bal", "type": "scale", "protocol": "and"}, {"name": "box", "type": "arduino"},
                    {"name": "off", "type": "scale", "enabled": False}]
    assert [c["name"] for c in scales.scale_configs(p)] == ["bal"]
    a = p.animals[0]
    e = scales.record_weight(p, a, 23.456)
    assert e["grams"] == 23.456 and len(e["date"]) == 16
    scales.record_weight(p, a, 24.1)
    assert a.fields[scales.WEIGHT_FIELD] == "24.1" and scales.WEIGHT_FIELD in p.animal_fields
    with pytest.raises(ValueError):
        scales.record_weight(p, a, 0)
    p.save(tmp_path / "w.mmaze")
    q = Project.load(tmp_path / "w.mmaze")
    assert q.animals[0].weights == a.weights and q.animals[0].fields[scales.WEIGHT_FIELD] == "24.1"
    # projects saved before weight histories existed still load
    old = Animal.from_dict({"id": "X", "group": "g", "fields": {"Weight (g)": "20"}})
    assert old.weights == [] and old.fields["Weight (g)"] == "20"
    bad = Animal.from_dict({"id": "Y", "weights": [{"date": "2026-01-01", "grams": "21.5"}, "junk", {"date": "x"}]})
    assert bad.weights == [{"date": "2026-01-01", "grams": 21.5}]
