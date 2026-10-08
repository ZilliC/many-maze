"""Results and distribution: SYLK / dBase writers, ANY-maze XML and zone-map import, the update check, the live
calibration adjustment stored per test, and live testing at ANY-maze's scale (48 cameras, 40 apparatus)."""

import datetime as dt
import io
import json
import struct
import threading
import time
import urllib.error

import cv2
import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates, updates
from manymaze.core.anymaze import (import_anymaze_xml, is_anymaze_xml, read_anymaze_xml, read_zone_map,
                                   track_from_columns, zone_maps_apparatus)
from manymaze.core.apparatus import CALIBRATION_KEY, Apparatus, calibration_override
from manymaze.core.autosave import RecoveredSession, _json_default
from manymaze.core.camera import SourceSpec
from manymaze.core.export import (TABLE_SUFFIXES, dbf_field_names, export_results, export_xml, read_sylk, write_dbf,
                                  write_sylk, write_table)
from manymaze.core.live import LiveSession
from manymaze.core.livegroup import LiveGroup
from manymaze.core.project import Project
from manymaze.core.session import save_live_test
from manymaze.core.track import Track
from manymaze.core.tracking import DetectionSettings
from manymaze.core.video import MAX_CAMERAS, list_cameras

ROWS = [{"Test": 1, "Animal": "A;1", "Group": "Control", "Total distance (m)": 1.5, "Total distance (cm)": 150.25,
         "Entries": np.int64(12), "Freezing": True, "Latency (s)": float("nan")},
        {"Test": 2, "Animal": "Bé", "Group": "Drug \"x\"", "Total distance (m)": -0.125, "Entries": 3,
         "Freezing": False, "Latency (s)": np.float64(2.5)}]


# ====================================================================== SYLK
def test_sylk_round_trip(tmp_path):
    p = tmp_path / "r.slk"
    write_sylk(ROWS, p)
    raw = p.read_bytes()
    assert raw.startswith(b"ID;P") and raw.rstrip().endswith(b"E") and b"\r\n" in raw
    assert "Bé".encode("cp1252") in raw  # Windows-1252, as Excel expects
    cells = read_sylk(p)
    header = list(ROWS[0])
    assert cells[0] == header
    assert cells[1] == [1.0, "A;1", "Control", 1.5, 150.25, 12.0, True, None]  # NaN -> empty cell
    assert cells[2] == [2.0, "Bé", "Drug \"x\"", -0.125, None, 3.0, False, 2.5]
    assert b'K"A;;1"' in raw  # ';' doubled inside a field
    # numbers are stored as numbers (K without quotes), text as text
    assert b"K1.5" in raw and b'K"Control"' in raw


def test_sylk_no_newlines_in_cells(tmp_path):
    p = tmp_path / "n.slk"
    write_sylk([{"Notes": "line one\nline two"}], p)
    assert read_sylk(p)[1] == ["line one line two"]


# ====================================================================== dBase
def _parse_dbf(raw: bytes):
    version, yy, mm, dd, n, hlen, rlen = struct.unpack("<BBBBIHH", raw[:12])
    fields = []
    pos = 32
    while raw[pos] != 0x0D:
        fd = raw[pos:pos + 32]
        fields.append((fd[:11].split(b"\0")[0].decode("ascii"), chr(fd[11]), fd[16], fd[17]))
        pos += 32
    assert pos + 1 == hlen
    recs = []
    for i in range(n):
        r = raw[hlen + i * rlen: hlen + (i + 1) * rlen]
        assert r[:1] == b" "
        off, vals = 1, []
        for _, typ, length, _ in fields:
            vals.append(r[off:off + length])
            off += length
        assert off == rlen
        recs.append(vals)
    assert raw[hlen + n * rlen:] == b"\x1a"
    return dict(version=version, date=(1900 + yy, mm, dd), n=n, hlen=hlen, rlen=rlen, fields=fields, recs=recs,
                lang=raw[29])


def test_dbf_layout(tmp_path):
    p = tmp_path / "r.dbf"
    write_dbf(ROWS, p, date=dt.date(2026, 10, 8))
    d = _parse_dbf(p.read_bytes())
    assert d["version"] == 0x03 and d["date"] == (2026, 10, 8) and d["n"] == 2 and d["lang"] == 0x57
    assert d["hlen"] == 32 + 32 * len(ROWS[0]) + 1
    names = [f[0] for f in d["fields"]]
    assert names == ["TEST", "ANIMAL", "GROUP", "TOTAL_DIST", "TOTAL_DI_2", "ENTRIES", "FREEZING", "LATENCY_S"]
    types = {f[0]: f[1:] for f in d["fields"]}
    assert types["TEST"] == ("N", 1, 0) and types["ENTRIES"] == ("N", 2, 0)
    assert types["TOTAL_DIST"] == ("N", 6, 3)  # -0.125 and 1.5 → width 6, 3 decimals
    assert types["FREEZING"] == ("L", 1, 0) and types["ANIMAL"][0] == "C"
    r0, r1 = d["recs"]
    assert r0[0] == b"1" and r0[3] == b" 1.500" and r1[3] == b"-0.125"
    assert r0[4] == b"150.25" and r1[4] == b"      "  # missing number: blank
    assert r0[6] == b"T" and r1[6] == b"F"
    assert r0[7] == b"   " and r1[7] == b"2.5"  # NaN: blank
    assert r1[1].rstrip() == "Bé".encode("cp1252") and r0[1] == b"A;1"
    assert d["rlen"] == 1 + sum(f[2] for f in d["fields"])


def test_dbf_field_names_and_limits(tmp_path):
    assert dbf_field_names(["1st", "a b", "a-b", "", "Zone: time (%)"]) == ["F1ST", "A_B", "A_B_2", "FIELD",
                                                                           "ZONE_TIME"]
    long_text = "x" * 400
    write_dbf([{"Notes": long_text, "Mixed": 1}, {"Notes": "", "Mixed": "a"}], tmp_path / "t.dbf")
    d = _parse_dbf((tmp_path / "t.dbf").read_bytes())
    assert d["fields"][0] == ("NOTES", "C", 254, 0)
    assert d["fields"][1][1] == "C"  # numbers and text mixed: a text field
    with pytest.raises(ValueError):
        write_dbf([{f"c{i}": i for i in range(300)}], tmp_path / "wide.dbf")
    write_dbf([], tmp_path / "empty.dbf", ["A"])
    assert _parse_dbf((tmp_path / "empty.dbf").read_bytes())["n"] == 0


def test_read_back_dbf_and_import_sylk_dbase(tmp_path):
    from manymaze.core.export import read_dbf
    from manymaze.core.importers import guess_mapping, import_track, read_table, TRACK_ROLES

    write_dbf(ROWS, tmp_path / "r.dbf")
    head, rows = read_dbf(tmp_path / "r.dbf")
    assert head[:2] == ["TEST", "ANIMAL"] and rows[0][:4] == [1.0, "A;1", "Control", 1.5]
    assert rows[1][6] is False and rows[0][7] is None and rows[1][1] == "Bé"
    # ANY-maze "Export test data" files saved as SYLK or dBase go through the track import
    n = 50
    track_rows = [{"Time": i / 25, "Centre position X": 10 + i, "Centre position Y": 20.0} for i in range(n)]
    for ext in (".slk", ".dbf"):
        f = tmp_path / f"test 1{ext}"
        write_table(track_rows, f)
        header, body = read_table(f)
        assert len(body) == n
        m = guess_mapping(header, TRACK_ROLES)
        if ext == ".dbf":  # 10-character dBase field names: mapped by hand
            assert header == ["TIME", "CENTRE_POS", "CENTRE_P_2"]
            m = {"t": 0, "x": 1, "y": 2}
        tr = import_track(header, body, m)
        assert len(tr) == n and tr.x[5] == 15 and tr.fps == pytest.approx(25)


def test_write_table_formats_and_export_results(tmp_path):
    assert {".slk", ".dbf"} <= set(TABLE_SUFFIXES)
    write_table(ROWS, tmp_path / "a.slk")
    write_table(ROWS, tmp_path / "a.dbf", ["Test", "Animal"])
    assert read_sylk(tmp_path / "a.slk")[0][0] == "Test"
    assert [f[0] for f in _parse_dbf((tmp_path / "a.dbf").read_bytes())["fields"]] == ["TEST", "ANIMAL"]
    p = _project_with_track(tmp_path)
    export_results(p, tmp_path / "res.slk")
    export_results(p, tmp_path / "res.dbf", columns=["Test", "Animal", "Total distance (cm)"])
    cells = read_sylk(tmp_path / "res.slk")
    assert "Total distance (cm)" in cells[0] and len(cells) == 2
    assert _parse_dbf((tmp_path / "res.dbf").read_bytes())["n"] == 1


def test_cli_writes_sylk_and_dbase(tmp_path):
    from manymaze.cli import main

    p = _project_with_track(tmp_path)
    out = tmp_path / "cli.slk"
    main(["project", str(p.path), "results", "-o", str(out)])
    assert out.exists() and out.stat().st_size > 50
    with pytest.raises(SystemExit, match="255 fields"):  # the full results table has more columns than dBase allows
        main(["project", str(p.path), "results", "-o", str(tmp_path / "cli.dbf")])
    out = tmp_path / "some.dbf"
    main(["project", str(p.path), "results", "-o", str(out), "--column", "Total distance (cm)"])
    names = [f[0] for f in _parse_dbf(out.read_bytes())["fields"]]
    assert names[:2] == ["TEST", "ANIMAL"] and names[-1] == "TOTAL_DIST" and len(names) < 40
    with pytest.raises(SystemExit, match="Unknown measure"):
        main(["project", str(p.path), "results", "-o", str(out), "--column", "Nope"])
    rows = [{f"Measure {i}": i for i in range(300)}]
    with pytest.raises(ValueError, match="at most 255 fields"):
        write_dbf(rows, tmp_path / "wide.dbf")
    assert not (tmp_path / "wide.dbf").exists()


# ====================================================================== calibration per test
def _project_with_track(tmp_path, px_per_cm=5.0):
    p = Project(name="cal")
    app = templates.build("open_field", 0, 0, 200, 200, size_cm=40)
    app.px_per_cm = px_per_cm
    app.name = "OF"
    p.apparatus.append(app)
    p.save(tmp_path / "cal.mmaze")
    p.ensure_animal("A1")
    t = p.add_test("", "A1", "OF")
    n = 101
    tt = np.arange(n) / 25
    x = 20 + np.arange(n) * 1.0  # 100 px straight line
    tr = Track(t=tt, x=x, y=np.full(n, 100.0), detected=np.ones(n, bool), fps=25.0)
    p.save_tracks(t, [tr])
    p.test_duration_s = 4.0
    p.save()
    return p


def test_per_test_calibration_used_by_analysis(tmp_path):
    p = _project_with_track(tmp_path)
    t = p.tests[0]
    d0 = p.results()[0]["Total distance (cm)"]
    assert d0 == pytest.approx(20.0, rel=0.05)  # 100 px at 5 px/cm
    t.zone_overrides[CALIBRATION_KEY] = calibration_override(2.5, [0, 0, 100, 0], 40.0)
    assert p.apparatus_of(t).px_per_cm == 2.5 and p.apparatus_of(t).calibration_line == (0, 0, 100, 0)
    assert p.apparatus[0].px_per_cm == 5.0  # the map itself is unchanged
    assert p.results()[0]["Total distance (cm)"] == pytest.approx(2 * d0, rel=1e-6)
    # survives saving, and the zone overrides of moveable zones still work alongside it
    p.save()
    q = Project.load(p.path)
    assert q.apparatus_of(q.tests[0]).px_per_cm == 2.5
    # positioned map: the explicit calibration wins over the scaled map calibration
    t.zone_overrides["@position"] = {"dx": 5, "dy": 0, "angle": 0, "scale": 2.0}
    assert p.apparatus_of(t).px_per_cm == 2.5
    with pytest.raises(ValueError):
        calibration_override(0)
    out = tmp_path / "x.xml"
    export_xml(p, out)
    assert "<calibration px-per-cm=\"2.5\"" in out.read_text()


def _mouse_frame(x, y, size=(200, 200)):
    img = np.full((size[1], size[0]), 200, np.uint8)
    syn.draw_mouse(img, x, y, 0.0)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_live_calibration_adjusted_during_test(tmp_path):
    p = _project_with_track(tmp_path)
    app = p.apparatus[0]
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0.0, start_mode="immediate")
    s.set_background(np.full((200, 200), 200, np.uint8))
    for i in range(20):
        s.process(_mouse_frame(40 + 4 * i, 100), i / 25)
    d_before = s.stats.distance
    assert d_before > 5 and s.stats.unit == "cm"
    cal = s.set_calibration(2.5, line=(0, 0, 100, 0), length_cm=40)
    assert cal == {"px_per_cm": 2.5, "calibration_line": [0.0, 0.0, 100.0, 0.0], "calibration_length_cm": 40.0}
    assert s.stats.distance == pytest.approx(2 * d_before)  # tracked in pixels: converted to the new scale
    assert s.apparatus.px_per_cm == 2.5 and app.px_per_cm == 5.0  # the project's map is not changed
    for i in range(20, 30):
        s.process(_mouse_frame(40 + 4 * i, 100), i / 25)
    assert s.calibration_log[0][0] == pytest.approx(0.76) and "Calibration adjusted" in s.log[-1][1]
    snap = s.autosave_snapshot()
    assert snap["calibration"]["px_per_cm"] == 2.5
    rec = RecoveredSession(json.loads(json.dumps(snap, default=_json_default)))
    assert rec.calibration["px_per_cm"] == 2.5 and rec.calibration_log[0][1]["px_per_cm"] == 2.5
    s.finish()
    with pytest.raises(RuntimeError):
        s.set_calibration(3.0)
    t = p.add_test("", "A1", "OF")
    assert save_live_test(p, t, s)
    assert t.zone_overrides[CALIBRATION_KEY]["px_per_cm"] == 2.5 and "Calibration adjusted at 0.76 s" in t.notes
    assert p.apparatus_of(t).px_per_cm == 2.5
    # a session without an adjustment stores nothing
    s2 = LiveSession(app, DetectionSettings(background="frame"), duration_s=0.0)
    s2.set_background(np.full((200, 200), 200, np.uint8))
    for i in range(5):
        s2.process(_mouse_frame(50, 100), i / 25)
    s2.finish()
    t2 = p.add_test("", "A1", "OF")
    assert save_live_test(p, t2, s2) and CALIBRATION_KEY not in t2.zone_overrides


# ====================================================================== ANY-maze XML export
AM_XML = """<?xml version="1.0" encoding="utf-8"?>
<Experiment>
  <Title>Plus maze</Title><CreationDate>2024-03-01</CreationDate>
  <Animal>
    <Number>1</Number><ID>M01</ID><Treatment>Drug</Treatment><Notes>Calm animal</Notes>
    <Test>
      <Number>3</Number><Date>2024-03-05</Date><Time>10:15:00</Time><Stage>Day 1</Stage><Trial>2</Trial>
      <Apparatus>Box A</Apparatus><ReasonTestEnded>Test duration reached</ReasonTestEnded><Notes>ok</Notes>
      <Scaling>500</Scaling>
      <Zones>
        <Zone><Name>Centre</Name><Centre><x>100</x><y>100</y></Centre>
          <BoundingBox><x>80</x><y>80</y><w>40</w><h>40</h></BoundingBox></Zone>
        <Zone><Name>Arena</Name><Centre><x>100</x><y>100</y></Centre>
          <BoundingBox><x>0</x><y>0</y><w>200</w><h>200</h></BoundingBox></Zone>
      </Zones>
      <Results>
        <r><tm>0.00</tm><c><x>10</x><y>10</y></c><h><x>12</x><y>10</y></h></r>
        <r><tm>0.04</tm><np/></r>
        <r><tm>0.08</tm><c><x>100</x><y>100</y></c></r>
        <r><tm>0.12</tm><c x="104" y="100"/><t>98,100</t></r>
      </Results>
    </Test>
  </Animal>
  <Animal number="2" treatment="Control">
    <Test number="4" stage="Day 1" trial="1" apparatus="Box A" scaling="400">
      <Zone name="Centre"><Centre x="120" y="100"/><BoundingBox x="100" y="80" w="40" h="40"/></Zone>
      <r tm="0"><c>-50 20</c></r>
      <r tm="0.04"><c>-48 20</c></r>
    </Test>
  </Animal>
</Experiment>
"""


def test_read_anymaze_xml(tmp_path):
    p = tmp_path / "exp.xml"
    p.write_text(AM_XML, encoding="utf-8")
    assert is_anymaze_xml(p)
    d = read_anymaze_xml(p)
    assert d["title"] == "Plus maze" and len(d["animals"]) == 2
    a = d["animals"][0]
    assert (a["number"], a["id"], a["treatment"], a["notes"]) == ("1", "M01", "Drug", "Calm animal")
    t = a["tests"][0]
    assert t["recorded_at"] == "2024-03-05T10:15:00" and t["stage"] == "Day 1" and t["trial"] == "2"
    assert t["apparatus"] == "Box A" and t["end_reason"] == "Test duration reached" and t["px_per_m"] == 500
    assert t["zones"][0] == {"name": "Centre", "centre": (100.0, 100.0), "bbox": (80.0, 80.0, 40.0, 40.0)}
    tr = t["track"]
    np.testing.assert_allclose(tr["t"], [0, 0.04, 0.08, 0.12])
    assert np.isnan(tr["x"][1]) and tr["x"][3] == 104 and tr["hx"][0] == 12 and tr["tx"][3] == 98
    b = d["animals"][1]
    assert b["number"] == "2" and b["treatment"] == "Control" and b["tests"][0]["zones"][0]["bbox"][0] == 100
    # our own XML export is not mistaken for ANY-maze's
    own = tmp_path / "own.xml"
    export_xml(_project_with_track(tmp_path), own)
    assert not is_anymaze_xml(own)


def test_import_anymaze_xml(tmp_path):
    src = tmp_path / "exp.xml"
    src.write_text(AM_XML, encoding="utf-8")
    p = Project(name="imp")
    p.save(tmp_path / "imp.mmaze")
    res = import_anymaze_xml(p, src)
    assert res["animals"] == ["M01", "Animal 2"] and res["apparatus"] == ["Box A"] and not res["warnings"]
    app = p.get_apparatus("Box A")
    assert {z.name for z in app.zones} == {"Centre", "Arena"} and app.px_per_cm == 5.0
    t1, t2 = res["tests"]
    assert (t1.animal_id, t1.stage, t1.trial, t1.status) == ("M01", "Day 1", 2, "tracked")
    assert t1.recorded_at == "2024-03-05T10:15:00" and "Test duration reached" in t1.notes + str(
        getattr(t1, "end_reason", ""))
    a = p.get_animal("M01")
    assert a.group == "Drug" and "Day 1" in p.stages
    tr = p.load_tracks(t1)[0]
    assert len(tr) == 4 and not tr.detected[1] and tr.x[2] == 100 and tr.hx[0] == 12
    # the second test: other scaling (a per-test calibration), moved centre zone, centre-origin coordinates
    assert t2.zone_overrides[CALIBRATION_KEY]["px_per_cm"] == 4.0
    assert p.apparatus_of(t2).zone("Centre").shape.bounds()[0] == pytest.approx(100)
    tr2 = p.load_tracks(t2)[0]
    cx, cy = app.origin()
    assert tr2.x[0] == pytest.approx(cx - 50) and tr2.y[0] == pytest.approx(cy - 20)
    rows = p.results()
    assert len(rows) == 2 and rows[0]["Animal"] == "M01"
    with pytest.raises(ValueError):
        import_anymaze_xml(Project(), src)  # unsaved


def test_track_origin_option():
    cols = {"t": np.array([0, 0.1]), "x": np.array([1.0, 2.0]), "y": np.array([1.0, 2.0]),
            "hx": np.full(2, np.nan), "hy": np.full(2, np.nan), "tx": np.full(2, np.nan), "ty": np.full(2, np.nan)}
    tr = track_from_columns(cols, "centre", (100, 100))
    assert tr.x[1] == 102 and tr.y[1] == 98 and tr.fps == pytest.approx(10)
    assert track_from_columns(cols, "auto").x[0] == 1.0
    with pytest.raises(ValueError):
        track_from_columns({k: np.zeros(0) for k in cols})


# ====================================================================== ANY-maze zone maps
def _write_map(path, header_rows, grid):
    buf = io.StringIO()
    for r in header_rows:
        buf.write(",".join(map(str, r)) + "\r\n")
    for row in grid:
        buf.write(",".join(str(int(v)) for v in row) + "\r\n")
    path.write_text(buf.getvalue())


def test_zone_maps(tmp_path):
    h, w = 60, 80
    filled = np.zeros((h, w), int)
    filled[10:30, 20:50] = 1
    _write_map(tmp_path / "Individual zone area map for Box A, Centre zone.csv",
               [["Zone area map for", "Box A", "Centre"], ["Zone area map dimensions (w x h)", w, h]], filled)
    border = np.zeros((h, w), np.uint8)
    cv2.circle(border, (60, 40), 12, 1, 1)
    _write_map(tmp_path / "Individual zone area map for Box A, Hole zone.csv",
               [["Zone area map for", "Box A", "Hole"], ["Zone area map dimensions (w x h)", w, h]], border)
    combined = np.zeros((h, w), int)
    combined[:, :40] = 1
    combined[:, 40:] = 2
    _write_map(tmp_path / "Combined zone area map for Box B.csv",
               [["Zone names and codes:"], ["Left", 1], ["Right", 2],
                ["Zone area map dimensions (w x h)", w, h]], combined)
    z = read_zone_map(tmp_path / "Individual zone area map for Box A, Hole zone.csv")
    assert z["apparatus"] == "Box A" and z["kind"] == "individual" and z["size"] == (80, 60)
    hole = z["zones"]["Hole"]
    assert hole[40, 60] and hole[40, 50] and not hole[5, 5]  # the border map is filled
    c = read_zone_map(tmp_path / "Combined zone area map for Box B.csv")
    assert c["kind"] == "combined" and set(c["zones"]) == {"Left", "Right"} and c["apparatus"] == "Box B"
    p = Project(name="zm")
    old = Apparatus(name="Box A", px_per_cm=3.0)
    p.apparatus.append(old)
    apps = zone_maps_apparatus(sorted(tmp_path.glob("*.csv")), p)
    assert sorted(a.name for a in apps) == ["Box A", "Box B"] and old in apps and old.px_per_cm == 3.0
    centre = old.zone("Centre").shape
    assert bool(centre.contains(35, 20)) and not bool(centre.contains(10, 20))
    assert bool(old.zone("Hole").shape.contains(60, 40))
    right = p.get_apparatus("Box B").zone("Right").shape
    assert bool(right.contains(70, 30)) and not bool(right.contains(10, 30))
    with pytest.raises(ValueError):
        (tmp_path / "bad.csv").write_text("a,b\n1,2\n")
        read_zone_map(tmp_path / "bad.csv")


# ====================================================================== update check
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_version_comparison():
    assert updates.is_newer("v0.2.0", "0.1.0") and updates.is_newer("1.10", "1.9.3")
    assert not updates.is_newer("0.1.0", "0.1.0") and not updates.is_newer("v0.1", "0.1.0")
    assert updates.is_newer("1.0.0", "1.0.0rc2") and not updates.is_newer("1.0.0-beta.1", "1.0.0")
    assert updates.is_newer("1.0.0rc2", "1.0.0rc1") and not updates.is_newer("nightly", "0.1.0")


def test_check_for_updates_mocked(monkeypatch):
    seen = {}

    def ok(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        seen["agent"] = req.get_header("User-agent")
        return _Resp(json.dumps({"tag_name": "v9.1.0", "html_url": "https://github.com/x/y/releases/tag/v9.1.0",
                                 "name": "9.1", "body": "notes", "published_at": "2026-01-01T00:00:00Z"}).encode())

    info = updates.check_for_updates("x/y", current="0.1.0", timeout=3, opener=ok)
    assert seen["url"] == "https://api.github.com/repos/x/y/releases/latest" and seen["timeout"] == 3
    assert seen["agent"].startswith("mANY-MAZE/")
    assert info.ok and info.newer and info.latest == "9.1.0" and info.url.endswith("v9.1.0")
    assert "9.1.0 is available" in info.message()
    same = updates.check_for_updates("x/y", "9.1.0", opener=ok)
    assert same.ok and not same.newer and "up to date" in same.message()

    def http(code):
        def f(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return f

    assert "no release" in updates.check_for_updates("x/y", opener=http(404)).error
    assert "rate limit" in updates.check_for_updates("x/y", opener=http(403)).error

    def offline(req, timeout):
        raise urllib.error.URLError("Name or service not known")

    off = updates.check_for_updates("x/y", opener=offline)
    assert not off.ok and not off.newer and "no connection" in off.error and "Could not" in off.message()

    def timeout(req, timeout):
        raise TimeoutError("timed out")

    assert not updates.check_for_updates("x/y", opener=timeout).ok
    bad = updates.check_for_updates("x/y", opener=lambda r, timeout: _Resp(b"<html>"))
    assert not bad.ok and "unexpected" in bad.error
    assert not updates.check_for_updates("x/y", opener=lambda r, timeout: _Resp(b"{}")).ok
    monkeypatch.setenv("MANYMAZE_UPDATE_REPO", "someone/fork")
    assert updates.update_repo() == "someone/fork"
    monkeypatch.setenv("MANYMAZE_UPDATE_REPO", "not a repo")
    assert updates.update_repo() == updates.DEFAULT_REPO


def test_no_network_in_default_opener(monkeypatch):
    """The default opener is urllib's; make sure tests could never reach it by accident."""
    import urllib.request

    def boom(*a, **kw):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert not updates.check_for_updates("x/y").ok


def test_check_due():
    now = dt.datetime(2026, 10, 8, 12)
    assert updates.check_due(None, now) and updates.check_due("garbage", now)
    assert not updates.check_due((now - dt.timedelta(days=2)).isoformat(), now)
    assert updates.check_due((now - dt.timedelta(days=8)).isoformat(), now)
    assert updates.check_due((now + dt.timedelta(days=3)).isoformat(), now)  # clock went back


# ====================================================================== ANY-maze scale: 48 cameras, 40 apparatus
class FakeCamera:
    """A camera delivering a synthetic mouse walking in two open fields (400 × 200), paced at fps."""

    instances = 0
    lock = threading.Lock()

    def __init__(self, source, width=None, height=None, fps=None):
        self.index = int(source)
        self.is_camera = True
        self.fps = 25.0
        self.width, self.height = 400, 200
        self.i = 0
        with FakeCamera.lock:
            FakeCamera.instances += 1
        a = syn.random_walk(40, (20, 20, 180, 180), speed=3, seed=self.index)
        b = syn.random_walk(40, (220, 20, 380, 180), speed=3, seed=100 + self.index)
        self.frames = []
        for (ax, ay, *_), (bx, by, *_) in zip(a, b):
            img = np.full((200, 400), 200, np.uint8)
            syn.draw_mouse(img, ax, ay, 0.0)
            syn.draw_mouse(img, bx, by, 0.0)
            self.frames.append(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))

    def read(self):
        time.sleep(0.01)
        f = self.frames[self.i % len(self.frames)]
        self.i += 1
        return True, f

    def seek(self, i):
        self.i = i

    def release(self):
        pass


def test_list_cameras_scans_beyond_six():
    class Cap:
        def __init__(self, ok):
            self.ok = ok

        def isOpened(self):
            return self.ok

        def read(self):
            return self.ok, None

        def release(self):
            pass

    present = set(range(48)) - {3}  # a gap is skipped
    assert list_cameras(opener=lambda i: Cap(i in present)) == sorted(present)
    assert MAX_CAMERAS >= 48
    assert list_cameras(opener=lambda i: Cap(False)) == []


def test_48_cameras_40_apparatus():
    """A live group of 48 cameras (fake, threaded) and 40 simultaneous tests: 32 cameras with one apparatus and 4
    cameras with two (shared image), the other 12 cameras streaming without a test."""
    FakeCamera.instances = 0
    g = LiveGroup()
    keys = [g.add_source(SourceSpec(i)) for i in range(48)]
    assert len(set(keys)) == 48
    left = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    right = templates.build("open_field", 210, 10, 180, 180, size_cm=40)
    sessions = []
    for k, key in enumerate(keys[:36]):
        for app in ((left, right) if k < 4 else (left,)):
            s = LiveSession(app, DetectionSettings(background="adaptive"), duration_s=1.5, start_mode="immediate")
            s.set_background(np.full((200, 400), 200, np.uint8))
            g.add_session(key, s, f"{key}:{'L' if app is left else 'R'}")
            sessions.append((s, app))
    assert len(sessions) == 40 and len(g.entries) == 40
    g.start_sources(opener=FakeCamera)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90 and not all(s.state == "finished" for s, _ in sessions):
            time.sleep(0.1)
        running = len(g.runners)
    finally:
        g.close()
    assert running == 48 and FakeCamera.instances == 48
    assert all(s.state == "finished" for s, _ in sessions), [s.state for s, _ in sessions]
    for s, app in sessions:
        tr = s.track()
        assert len(tr) >= 5 and tr.detected.mean() > 0.8
        x0, y0, x1, y1 = app.arena_or_bounds().bounds()
        xs = tr.x[tr.detected]
        assert np.all((xs > x0 - 5) & (xs < x1 + 5))  # each test tracks the animal of its own apparatus
    assert not [w for w in g.warnings if "Cannot open" in w[1]]
