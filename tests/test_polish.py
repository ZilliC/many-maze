"""Phase 7 of the 2026-10-09 plan (polish): terminology, recorded video file names, the heat map's hottest spot,
e-mailing reports through the alert device's SMTP server, downscaled tracking and the video glitch check."""

import csv
import datetime as dt
import json
from fractions import Fraction

import cv2
import numpy as np
import pytest

from manymaze.core import mail, plots, recordings
from manymaze.core.apparatus import Apparatus
from manymaze.core.calculations import Calculation
from manymaze.core.demo import create_demo_project
from manymaze.core.export import export_results, html_report, results_workbook
from manymaze.core.geometry import rect
from manymaze.core.project import Project
from manymaze.core.terminology import (TERMS, column_label, column_labels, plural_of, relabel, term,
                                       terminology_from)
from manymaze.core.track import Track
from manymaze.core.tracking import (ArenaJob, Detection, DetectionSettings, downscaled_settings, track_video,
                                    upscaled_detection)
from manymaze.core.video import FrameReader, check_video, downscale_frame, frame_ranges
from manymaze.core.workflow import copy_protocol

from fake_smtp import FakeSMTP, notify_device


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return create_demo_project(tmp_path_factory.mktemp("polish") / "d.mmaze", n_per_group=2, seconds=5)


# ------------------------------------------------------------------ terminology
def test_terms_default_and_custom():
    assert [term(None, k) for k in TERMS] == ["Animal", "Treatment", "Test", "Stage", "Trial", "Apparatus", "Zone"]
    assert term(None, "apparatus", plural=True) == "Apparatus" and term(None, "test", plural=True) == "Tests"
    t = terminology_from({"animal": "Fish", "treatment": {"singular": "Condition"}, "test": {"singular": "Session",
                          "plural": "Sessions"}, "trial": "Trial", "bogus": "X", "stage": {"singular": " "}})
    assert t == {"animal": {"singular": "Fish", "plural": "Fishes"},
                 "treatment": {"singular": "Condition", "plural": "Conditions"},
                 "test": {"singular": "Session", "plural": "Sessions"}}  # defaults, blanks and unknown keys dropped
    assert term(t, "animal", plural=True) == "Fishes" and term(t, "test", lower=True) == "session"
    assert term({"stage": {"singular": "PND", "plural": "PNDs"}}, "stage", lower=True) == "PND"  # abbreviations
    assert plural_of("Box") == "Boxes" and plural_of("Colony") == "Colonies" and plural_of("Day") == "Days"
    assert terminology_from(None) == {} and terminology_from(["x"]) == {}


def test_column_labels_rename_information_columns_only():
    p = Project(terminology=terminology_from({"animal": "Subject", "treatment": "Condition", "test": "Session"}))
    assert column_label(p, "Animal") == "Subject" and column_label(p, "Group") == "Condition"
    assert column_label(p, "Test date") == "Session date"
    assert column_label(p, "Reason for test end") == "Reason for session end"
    assert column_label(p, "Treatment code") == "Condition code"
    assert column_label(p, "Total distance (cm)") == "Total distance (cm)"  # measures keep their names
    assert column_label(p, "Stage") == "Stage"  # unchanged term
    assert column_label(Project(), "Group") == "Group"  # nothing changes without terminology
    # a heading that would clash with another column keeps the standard name
    clash = Project(terminology=terminology_from({"animal": "Group"}))
    assert column_labels(clash, ["Animal", "Group"]) == {"Animal": "Animal", "Group": "Group"}
    rows, cols = relabel(p, [{"Test": 1, "Animal": "M1", "Group": "Saline", "X": 2.0, "hidden": 1}],
                         ["Test", "Animal", "Group", "X"])
    assert cols == ["Session", "Subject", "Condition", "X"] and rows == [{"Session": 1, "Subject": "M1",
                                                                         "Condition": "Saline", "X": 2.0}]
    same = [{"Animal": "M1"}]
    assert relabel(Project(), same, ["Animal"])[0] is same  # unchanged lists without terminology


def test_terminology_is_optional_saved_and_copied(tmp_path, demo):
    old = json.loads((demo.path / "project.json").read_text())
    old.pop("terminology", None)
    old.pop("recording_name_fields", None)
    p = Project.from_dict(old, demo.path)  # an experiment saved before these fields existed
    assert p.terminology == {} and p.recording_name_fields == [] and not p.unknown
    p.terminology = terminology_from({"animal": "Subject"})
    p.recording_name_fields = ["animal", "date"]
    p.save(tmp_path / "t.mmaze")
    q = Project.load(tmp_path / "t.mmaze")
    assert q.terminology == {"animal": {"singular": "Subject", "plural": "Subjects"}}
    assert q.recording_name_fields == ["animal", "date"]
    new = copy_protocol(q, Project(name="copy"))
    assert new.terminology == q.terminology and new.recording_name_fields == ["animal", "date"]


def test_terminology_in_exports_but_not_in_results_rows(tmp_path, demo):
    p = Project.load(demo.path)
    p.terminology = terminology_from({"animal": "Subject", "treatment": "Condition", "trial": "Run"})
    p.calculations = [Calculation("Twice the trial", "{Trial} * 2", 0)]
    rows = p.results()
    assert "Animal" in rows[0] and "Group" in rows[0] and rows[0]["Twice the trial"] == 2  # formulas keep names
    export_results(p, tmp_path / "r.csv")
    head = next(csv.reader(open(tmp_path / "r.csv", encoding="utf-8")))
    assert head[:3] == ["Test", "Subject", "Condition"]
    assert "Subject" in head and "Condition" in head and "Run" in head and "Animal" not in head
    assert "Total distance (cm)" in head
    sheets, cols = results_workbook(p, rows, ["Test", "Animal", "Group", "Total distance (cm)"])
    assert cols["Results"] == ["Test", "Subject", "Condition", "Total distance (cm)"]
    assert "Subject" in sheets["Animals"][0] and "Run" in sheets["Tests"][0]
    assert "Setting" in sheets["Settings"][0]
    html = html_report(p, tmp_path / "r.html", include_plots=False).read_text("utf-8")
    assert "<th>Subject</th>" in html and "<th>Condition</th>" in html and "subjects" in html


# ------------------------------------------------------------------ recorded video file names
def test_recording_names_from_fields(demo):
    p = Project.load(demo.path)
    t = p.tests[0]
    when = dt.datetime(2026, 10, 9, 14, 5, 31)
    a = p.get_animal(t.animal_id)
    assert recordings.recording_name(p, t, when) == f"test_{t.id:04d}_{t.animal_id}"  # the default, as before
    t.stage = "Day 1"
    name = recordings.recording_name(p, t, when, ["animal", "treatment", "stage", "trial", "date", "time", "test"])
    assert name == f"{t.animal_id}_{a.group}_Day_1_trial_1_2026-10-09_14-05-31_test_{t.id:04d}"
    p.blind = True  # testing blind: the code, never the treatment's name
    p.settings_extra["blind_codes"] = {a.group: "K7"}
    assert recordings.recording_name(p, t, when, ["treatment", "animal"]) == f"K7_{t.animal_id}"
    p.settings_extra["blind_codes"] = {}
    assert a.group not in recordings.recording_name(p, t, when, ["treatment", "animal"])
    t.stage = ""
    assert recordings.recording_name(p, t, when, ["stage"]) == f"test_{t.id:04d}"  # all blank
    p.recording_name_fields = ["date", "bogus", "date"]
    assert recordings.name_fields(p) == ["date"]
    assert recordings.field_label(Project(terminology=terminology_from({"test": "Session"})), "test") == \
        "Session number"


def test_recording_names_never_collide(tmp_path):
    (tmp_path / "M1.mp4").write_bytes(b"x")
    (tmp_path / "M1_2_part001.mp4").write_bytes(b"x")  # a split recording's part
    (tmp_path / "M1_3.m3u").write_text("")  # and a playlist
    assert recordings.reserve_recording(tmp_path, "M1", owner=("e", 1)).name == "M1_4"
    assert recordings.reserve_recording(tmp_path, "M1", owner=("e", 2)).name == "M1_5"  # set up at the same time
    assert recordings.reserve_recording(tmp_path, "M1", owner=("e", 1)).name == "M1_4"  # set up again: same name
    recordings.release_recording(tmp_path / "M1_5.mp4")
    assert recordings.reserve_recording(tmp_path, "M1", owner=("e", 3)).name == "M1_5"
    assert recordings.reserve_recording(tmp_path, "a.b", owner=("e", 4)).name == "a.b"  # names with dots
    (tmp_path / "a.b_6.mp4").write_bytes(b"x")
    assert recordings.reserve_recording(tmp_path, "a.b_6", owner=("e", 5)).name == "a.b_6_2"


# ------------------------------------------------------------------ hottest spot of a heat map
def test_hottest_spot_is_the_argmax_of_the_smoothed_map():
    app = Apparatus("A", arena=rect(0, 0, 200, 100))
    t = np.arange(0, 60, 0.04)
    rng = np.random.default_rng(0)
    x = np.where(t < 45, 150 + rng.normal(0, 2, len(t)), 40 + rng.normal(0, 2, len(t)))
    y = np.where(t < 45, 30 + rng.normal(0, 2, len(t)), 70 + rng.normal(0, 2, len(t)))
    tr = Track(t=t, x=x, y=y, fps=25.0)
    H, extent = plots.occupancy([tr], app, bins=60, sigma=1.5)
    hx, hy = plots.hottest_spot(H, extent, app.arena)
    assert abs(hx - 150) < 4 and abs(hy - 30) < 4
    fig = plots.heatmap([tr], app)  # the same from the drawn image
    im = fig.axes[0].images[0]
    assert plots.hottest_spot(np.ma.filled(im.get_array(), np.nan), im.get_extent()) == pytest.approx((hx, hy))
    small = rect(0, 50, 100, 100)  # only bins inside a region count
    rx, ry = plots.hottest_spot(H, extent, small)
    assert abs(rx - 40) < 4 and abs(ry - 70) < 4
    assert plots.hottest_spot(np.zeros((4, 4)), extent) is None


# ------------------------------------------------------------------ e-mail
def test_send_email_with_attachments_through_the_fake_server(tmp_path):
    f = tmp_path / "results.csv"
    f.write_text("Test,Animal\n1,M1\n", encoding="utf-8")
    with FakeSMTP() as srv:
        cfg = notify_device(srv.port)
        rcpt = mail.send_email(cfg, "a@example.org; b@example.org", "Results", "See attached.", [f])
        assert rcpt == ["a@example.org", "b@example.org"]
    (m,) = srv.messages
    assert m["to"] == ["a@example.org", "b@example.org"]
    msg = m["message"]
    assert msg["Subject"] == "Results" and msg["From"] == "lab@example.org"
    assert msg.get_body(("plain",)).get_content().strip() == "See attached."
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == "results.csv" and b"1,M1" in att.get_payload(decode=True)


def test_send_email_refuses_a_remote_server_without_starttls(monkeypatch):
    assert mail.is_local("localhost") and mail.is_local("127.0.0.1") and mail.is_local("::1")
    assert not mail.is_local("smtp.example.org")
    with FakeSMTP() as srv:
        monkeypatch.setattr(mail, "is_local", lambda host: False)  # as if the server were across the network
        with pytest.raises(RuntimeError, match="STARTTLS"):
            mail.send_email(notify_device(srv.port, smtp_user="me", smtp_password="secret"), "a@example.org",
                            "x", "y")
    assert srv.messages == []
    monkeypatch.undo()
    with pytest.raises(RuntimeError, match="address"):
        mail.send_email(notify_device(1), "", "x", "y")
    with pytest.raises(RuntimeError, match="SMTP"):
        mail.send_email({"smtp_host": ""}, "a@example.org", "x", "y")


def test_mail_devices_use_the_secrets_file(tmp_path):
    p = Project(io_devices=[notify_device(25, smtp_user="me", smtp_password="pw"), {"type": "arduino", "name": "A"},
                            {"type": "notify", "name": "SMS only", "smtp_host": ""}])
    p.save(tmp_path / "m.mmaze")
    assert "pw" not in (tmp_path / "m.mmaze" / "project.json").read_text()
    q = Project.load(tmp_path / "m.mmaze")
    (d,) = mail.mail_devices(q)
    assert d["name"] == "Alerts" and d["smtp_password"] == "pw"  # from io-secrets.json


# ------------------------------------------------------------------ downscaled tracking
def test_downscaled_frames_and_settings(demo):
    path = demo.abs_path(demo.tests[0].video)
    with FrameReader(path, gray=True, downscale=2) as r:
        i, f = next(iter(r))
        assert (r.width, r.height) == (400, 400) and f.shape == (200, 200)
    assert downscale_frame(np.zeros((101, 63), np.uint8), 4).shape == (25, 15)
    s = DetectionSettings(min_area_px=40, max_area_px=4000, blur=5, morph_open=3, morph_close=7, erase_thin_px=4)
    h = downscaled_settings(s, 2)
    assert (h.min_area_px, h.max_area_px, h.blur, h.morph_open, h.morph_close, h.erase_thin_px) == \
        (10, 1000, 2, 2, 4, 2)
    assert downscaled_settings(s, 1) is s
    d = Detection(x=10.0, y=20.0, hx=12.0, hy=20.0, tx=8.0, ty=20.0, area=50.0, motion=4.0, detected=True,
                  outline=np.array([[0, 0], [10, 0], [10, 10]], np.int32))
    u = upscaled_detection(d, 2.0, 2.0)
    assert (u.x, u.y, u.hx, u.area, u.motion) == (20.5, 40.5, 24.5, 200.0, 16.0)
    assert u.outline.tolist() == [[0, 0], [20, 0], [20, 20]] and d.x == 10.0  # (rounded) and a copy


def test_downscaled_tracking_gives_results_in_the_same_units(demo):
    p = Project.load(demo.path)
    t = p.tests[0]
    s = p.detection_for(t)
    full = track_video(p.abs_path(t.video), [ArenaJob(p.apparatus_of(t), s)])[0][0]
    s.downscale = 2
    half = track_video(p.abs_path(t.video), [ArenaJob(p.apparatus_of(t), s)])[0][0]
    assert half.meta["downscale"] == 2 and len(half) == len(full)
    err = np.hypot(half.x - full.x, half.y - full.y)
    assert np.nanmedian(err) < 1.0 and np.nanmax(err) < 3.0  # positions in the video's pixels
    assert np.nanmedian(half.area / full.area) == pytest.approx(1.0, abs=0.1)
    before = p.results()[0]["Total distance (cm)"]
    p.detection.downscale = 4
    p.track_test(t)
    after = p.results()[0]["Total distance (cm)"]
    assert after == pytest.approx(before, rel=0.05)
    old = DetectionSettings.from_dict({"threshold": 20})  # older experiments: full resolution
    assert old.downscale == 1


# ------------------------------------------------------------------ video glitches
def test_check_video_finds_duplicate_black_and_missing_frames(tmp_path):
    rng = np.random.default_rng(1)
    frames = [rng.integers(40, 220, (48, 64, 3), dtype=np.uint8) for _ in range(40)]
    for i in (10, 11, 12):
        frames[i] = frames[9].copy()
    for i in (20, 21):
        frames[i] = np.zeros_like(frames[0])
    w = cv2.VideoWriter(str(tmp_path / "g.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
    for f in frames:
        w.write(f)
    w.release()
    c = check_video(tmp_path / "g.avi")
    assert (c.frames, c.duplicate, c.black, c.gaps) == (40, [10, 11, 12], [20, 21], [])
    assert not c.ok and c.summary() == "3 duplicate frames (10–12); 2 black frames (20–21)"
    assert frame_ranges([1, 3, 4, 5, 9]) == "1, 3–5, 9"

    av = pytest.importorskip("av")
    out = av.open(str(tmp_path / "gap.mkv"), "w")  # timestamps jump over 5 frames after frame 29
    st = out.add_stream("mjpeg", rate=25)
    st.width, st.height, st.pix_fmt = 64, 48, "yuvj420p"
    st.time_base = Fraction(1, 1000)
    t = 0.0
    for i in range(40):
        vf = av.VideoFrame.from_ndarray(rng.integers(40, 220, (48, 64, 3), dtype=np.uint8), format="bgr24")
        vf.pts, vf.time_base = int(round(t * 1000)), Fraction(1, 1000)
        for pkt in st.encode(vf):
            out.mux(pkt)
        t += 0.04 if i != 29 else 0.24
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    c = check_video(tmp_path / "gap.mkv")
    assert c.frames == 40 and c.gaps == [(1.4, 5)] and c.missing == 5 and not c.duplicate and not c.black
    assert "5 missing frames in 1 gap (at 1.40 s)" in c.summary()
    assert check_video(tmp_path / "none.mp4").error  # reported, not raised
