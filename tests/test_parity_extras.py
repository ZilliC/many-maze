"""Per-test apparatus position, protocol copy, protocol report, one-row-per-animal layout, event log and ANCOVA."""

import math
from pathlib import Path

import numpy as np
import pytest

from manymaze.core import export, workflow
from manymaze.core.apparatus import POSITION_KEY, Apparatus, PointOfInterest, Zone
from manymaze.core.demo import create_demo_project
from manymaze.core.geometry import Ellipse, circle, rect
from manymaze.core.measures import analyse
from manymaze.core.project import Project
from manymaze.core.stats import ancova, ancova_text


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return create_demo_project(tmp_path_factory.mktemp("demo") / "d.mmaze", n_per_group=1, seconds=8)


def test_similarity_transform_shapes():
    a = Apparatus(arena=circle(100, 100, 50), px_per_cm=2.0,
                  zones=[Zone("Square", rect(90, 90, 20, 20)), Zone("Oval", Ellipse(100, 100, 30, 10)),
                         Zone("Tilted", Ellipse(130, 100, 10, 5))],
                  points=[PointOfInterest("P", 120, 100)])
    b = a.positioned(dx=10, angle=90, scale=2)
    assert b is not a and a.points[0].x == 120  # the original is untouched
    assert b.arena.to_dict() == {"type": "ellipse", "cx": 110.0, "cy": 100.0, "rx": 100.0, "ry": 100.0}
    # 90° clockwise on screen (y down): +x goes to +y
    assert (b.points[0].x, b.points[0].y) == pytest.approx((110.0, 140.0))
    assert b.zone("Oval").shape.to_dict() == pytest.approx({"type": "ellipse", "cx": 110.0, "cy": 100.0,
                                                            "rx": 20.0, "ry": 60.0})
    assert b.zone("Square").shape.area() == pytest.approx(4 * 400)
    # distances in cm are unchanged by the scale
    assert b.px_per_cm == 4.0
    c = a.positioned(angle=30)
    assert c.zone("Oval").shape.to_dict()["type"] == "polygon"
    assert c.zone("Oval").shape.area() == pytest.approx(math.pi * 300, rel=0.01)
    assert a.positioned() is a
    assert a.with_overrides({POSITION_KEY: {"dx": 0, "dy": 0, "angle": 0, "scale": 1}}).zones[0].shape.to_dict() == \
        a.zones[0].shape.to_dict()


def test_position_moves_analysis_with_the_apparatus(demo):
    p = demo
    t = p.tests[0]
    tr = p.load_tracks(t)[0]
    app = p.get_apparatus(t.apparatus)
    s = p.analysis_for(t)
    base = analyse(tr, app, s)
    # the camera moved: the animal is filmed 40 px right / 25 px lower; the apparatus map follows
    moved = tr.copy()
    for c in ("x", "hx", "tx"):
        setattr(moved, c, getattr(moved, c) + 40)
    for c in ("y", "hy", "ty"):
        setattr(moved, c, getattr(moved, c) + 25)
    unaligned = analyse(moved, app, s)
    aligned = analyse(moved, app, s, zone_overrides={POSITION_KEY: {"dx": 40, "dy": 25}})
    key = "Centre: time (s)"
    assert unaligned[key] != pytest.approx(base[key], abs=0.05)
    assert aligned[key] == pytest.approx(base[key], abs=1e-6)
    assert aligned["Mean distance from wall (cm)"] == pytest.approx(base["Mean distance from wall (cm)"])
    # Project.apparatus_of applies it (used by tracking, charts and exports)
    t.zone_overrides[POSITION_KEY] = {"dx": 40, "dy": 25, "angle": 0, "scale": 1}
    try:
        a2 = p.apparatus_of(t)
        assert a2.arena.centroid()[0] == pytest.approx(app.arena.centroid()[0] + 40)
        xml = export.export_xml(p, p.exports_dir() / "pos.xml", tests=[t], include_tracks=False)
        text = open(xml, encoding="utf-8").read()
        assert "<apparatus-position" in text and 'dx="40' in text and 'name="@position"' not in text
    finally:
        t.zone_overrides.pop(POSITION_KEY, None)


def test_copy_protocol(demo, tmp_path):
    src = demo
    src.stages = ["Day 1", "Day 2"]
    src.settings_extra["confirm_id"] = True
    src.settings_extra["blind_codes"] = {"Control": "X"}
    dst = Project(name="Second cohort")
    workflow.copy_protocol(src, dst)
    assert [a.name for a in dst.apparatus] == [a.name for a in src.apparatus]
    assert dst.apparatus[0] is not src.apparatus[0]
    assert dst.stages == ["Day 1", "Day 2"] and [b.name for b in dst.behaviours] == [b.name for b in src.behaviours]
    assert dst.analysis.to_dict() == src.analysis.to_dict() and dst.detection.to_dict() == src.detection.to_dict()
    assert dst.settings_extra == {"confirm_id": True}  # data such as blind codes is not copied
    assert dst.animals == [] and dst.tests == [] and dst.groups == []
    dst.apparatus[0].zones[0].name = "changed"
    assert src.apparatus[0].zones[0].name != "changed"
    workflow.copy_protocol(src, dst, treatments=True)
    assert [g.name for g in dst.groups] == [g.name for g in src.groups]
    dst.save(tmp_path / "second.mmaze")
    assert Project.load(tmp_path / "second.mmaze").stages == ["Day 1", "Day 2"]


def test_protocol_report(demo, tmp_path):
    p = demo
    p.procedures = [{"name": "Light", "enabled": True, "statements": [
        {"type": "when", "event": "zone_enter", "zone": "Centre", "body": [
            {"type": "do", "action": "mark", "name": "In centre"}]}]}]
    p.training_criteria = [{"stage": "Day 1", "measure": "Total distance (cm)", "op": ">", "value": 1,
                            "consecutive_trials": 2}]
    try:
        html = export.protocol_report(p, tmp_path / "protocol.html").read_text(encoding="utf-8")
    finally:
        p.procedures, p.training_criteria = [], []
    for text in ("protocol</h1>", "Apparatus: ", "Centre", "Periphery", "<img", "Animal tracking", "Analysis",
                 "Keys", "Rearing", "Procedures", "When ", "Mark event", "Training criteria", "Total distance (cm) &gt; 1"):
        assert text in html, text


def test_wide_rows():
    rows = [{"Test": 1, "Animal": "A", "Group": "G1", "Stage": "Day 1", "Trial": 1, "Period": "Whole test", "X": 1},
            {"Test": 2, "Animal": "A", "Group": "G1", "Stage": "Day 2", "Trial": 1, "Period": "Whole test", "X": 2},
            {"Test": 3, "Animal": "B", "Group": "G2", "Stage": "Day 1", "Trial": 1, "Period": "Whole test", "X": 3},
            {"Test": 3, "Animal": "B", "Group": "G2", "Stage": "Day 1", "Trial": 1, "Period": "0-60 s", "X": 4}]
    w = export.wide_rows(rows, ["X"])
    assert w == [{"Animal": "A", "Group": "G1", "X [Day 1 · 1]": 1, "X [Day 2 · 1]": 2},
                 {"Animal": "B", "Group": "G2", "X [Day 1 · 1]": 3, "X [Day 1 · 1 · 0-60 s]": 4}]


def test_event_log(demo):
    p = demo
    t = p.tests[0]
    beh = p.behaviours[0].name
    t.events = [{"behaviour": beh, "t": 1.0, "t_end": 2.5}]
    t.io_events = [{"t": 3.0, "device": "box1", "channel": "led", "kind": "output", "value": 1}]
    t.pauses = [[4.0, 5.0]]
    try:
        log = export.event_log_rows(p, t)
    finally:
        t.events, t.io_events, t.pauses = [], [], []
    times = [r["Time (s)"] for r in log]
    assert times == sorted(times)
    kinds = [(r["Event"], r["Detail"]) for r in log]
    assert ("Key on", beh) in kinds and ("Key off", beh) in kinds and ("Output", "box1:led = 1") in kinds
    assert ("Paused", "") in kinds and ("Resumed", "") in kinds
    entries = [r for r in log if r["Event"] == "Zone entry"]
    assert entries and entries[0]["Time (s)"] == 0.0
    # entries and exits of a zone alternate
    for zone in {r["Detail"] for r in entries}:
        seq = [r["Event"] for r in log if r["Detail"] == zone and r["Event"].startswith("Zone")]
        assert all(a != b for a, b in zip(seq, seq[1:]))


def test_ancova():
    rng = np.random.default_rng(3)
    rows = []
    for g, off in (("A", 0.0), ("B", 3.0), ("C", 0.0)):
        for _ in range(12):
            w = rng.normal(25, 3)
            rows.append({"Group": g, "Weight": w, "Y": 1.5 * w + off + rng.normal(0, 0.5)})
    res = ancova(rows, "Y", "Weight")
    g, c = res["effects"]
    assert g["effect"] == "Group" and g["df"] == 2 and g["df_error"] == 32 and g["p"] < 1e-6
    assert c["effect"] == "Weight" and c["p"] < 1e-6
    assert res["slope"] == pytest.approx(1.5, abs=0.1)
    adj = res["adjusted_means"]
    assert adj["B"]["adjusted_mean"] - adj["A"]["adjusted_mean"] == pytest.approx(3.0, abs=0.5)
    assert res["slopes"]["p"] > 0.01
    # reference: residual sums of squares of nested least-squares fits
    y = np.array([r["Y"] for r in rows])
    X = np.column_stack([np.ones(len(rows)), [r["Weight"] for r in rows]])
    D = np.column_stack([[r["Group"] == "B" for r in rows], [r["Group"] == "C" for r in rows]]).astype(float)

    def rss(M):
        b, *_ = np.linalg.lstsq(M, y, rcond=None)
        return float(((y - M @ b) ** 2).sum())

    full = rss(np.hstack([X, D]))
    F = ((rss(X) - full) / 2) / (full / 32)
    assert g["F"] == pytest.approx(F)
    assert "adjusted mean" in ancova_text(res, "Y")
    assert "error" in ancova([{"Group": "A", "Weight": 1, "Y": 2}], "Y", "Weight")


def test_swap_identities():
    from manymaze.core.track import Track, swap_identities

    t = np.arange(10) / 10
    a = Track(t, np.zeros(10), np.zeros(10), area=np.full(10, 5.0))
    b = Track(t, np.ones(10), np.ones(10), area=np.full(10, 9.0))
    assert swap_identities(a, b, 0.5) == 5
    assert a.x.tolist() == [0] * 5 + [1] * 5 and b.x.tolist() == [1] * 5 + [0] * 5
    assert a.area[-1] == 9.0 and a.t.tolist() == t.tolist()
    assert swap_identities(a, b, 0.2, 0.3) == 2
    assert a.x[2] == 1 and b.x[3] == 0
    with pytest.raises(ValueError):
        swap_identities(a, Track(t[:5], np.zeros(5), np.zeros(5)), 0)


def test_backups(tmp_path, monkeypatch):
    import manymaze.core.project as project_mod

    p = Project(name="B")
    p.stages = ["One"]
    p.save(tmp_path / "b.mmaze")
    assert p.list_backups() == []  # nothing saved before the first save
    p.stages = ["Two"]
    p.save()
    (first,) = p.list_backups()
    assert first.parent.name == "backups" and '"One"' in first.read_text()
    p.stages = ["Three"]
    p.save()  # within the backup interval: no new backup
    assert len(p.list_backups()) == 1
    monkeypatch.setattr(project_mod, "BACKUP_INTERVAL_S", 0.0)
    for i in range(3):
        (p.backups_dir() / f"project-2020010{i}-000000.json").write_text(first.read_text())
    assert p.backup(keep=3) is not None and len(p.list_backups()) == 3
    restored = p.restore_backup(first)
    assert restored.stages == ["One"] and restored.path == p.path
    p.settings_extra["backups"] = False
    n = len(p.list_backups())
    p.save()
    assert len(p.list_backups()) == n


def _colour_frame(pos, colours, size=(240, 320)):
    import cv2

    img = np.full(size + (3,), 128, np.uint8)  # grey floor
    for (x, y), c in zip(pos, colours):
        cv2.ellipse(img, (int(x), int(y)), (16, 9), 0, 0, 360, (60, 60, 60), -1)  # dark grey body
        cv2.circle(img, (int(x), int(y)), 6, c, -1)  # colour mark on the back
    return img


def test_colour_detection_and_identity(tmp_path):
    import cv2

    from manymaze.core.tracking import ArenaTracker, DetectionSettings, colour_mask, hex_to_hsv, track_video

    assert hex_to_hsv("#ff0000")[0] in (0, 179) and hex_to_hsv("#0000ff")[0] == 120
    frame = _colour_frame([(80, 120)], [(0, 0, 255)])
    m = colour_mask(frame, "#ff0000")
    ys, xs = np.nonzero(m)
    assert abs(xs.mean() - 80) < 1 and abs(ys.mean() - 120) < 1 and m[10, 10] == 0
    assert not colour_mask(frame, "#00ff00").any()
    # detect a coloured mark (no background model needed)
    s = DetectionSettings(method="colour", target_colour="#ff0000", min_area_px=20, morph_close=0)
    dets, _ = ArenaTracker(s).process(frame)
    assert dets[0].detected and abs(dets[0].x - 80) < 1.5 and abs(dets[0].y - 120) < 1.5
    dets, _ = ArenaTracker(s).process(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    assert not dets[0].detected  # no colour in a greyscale frame
    # two dark animals with a red and a blue mark keep their identity even when they swap places
    s2 = DetectionSettings(method="threshold", contrast="dark", threshold=100, min_area_px=50, n_animals=2,
                           identity_colours="#0000ff, #ff0000", head_tail=False, morph_close=0)
    red, blue = (0, 0, 255), (255, 0, 0)
    tracker = ArenaTracker(s2)
    for k, (xr, xb) in enumerate([(60, 260), (260, 60), (100, 220)]):
        dets, _ = tracker.process(_colour_frame([(xr, 120), (xb, 120)], [red, blue]))
        assert abs(dets[0].x - xb) < 2 and abs(dets[1].x - xr) < 2, k  # animal 1 = blue, animal 2 = red
    assert s2.needs_colour() and not DetectionSettings().needs_colour()
    # a video tracked by colour (colour frames are decoded only when needed)
    path = str(tmp_path / "colour.avi")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    for i in range(20):
        vw.write(_colour_frame([(40 + 10 * i, 100)], [(0, 0, 255)]))
    vw.release()
    from manymaze.core.tracking import ArenaJob

    (tr,), = track_video(path, [ArenaJob(None, DetectionSettings(method="colour", min_area_px=20, max_gap_s=0))])
    assert tr.detected.all() and np.allclose(tr.x, 40 + 10 * np.arange(20), atol=2.5)


def test_randomise_treatments():
    from collections import Counter

    from manymaze.core.project import Animal, Group

    p = Project()
    p.groups = [Group("Vehicle"), Group("Drug")]
    p.animals = [Animal(f"M{i:02d}", sex="M" if i < 7 else "F", fields={"Litter": str(i % 3)}) for i in range(13)]
    p.animals[0].retired = True
    res = workflow.randomise_treatments(p, stratify_by="Sex", seed=5)
    assert len(res) == 12 and "M00" not in res
    n = Counter(res.values())
    assert abs(n["Vehicle"] - n["Drug"]) <= 1
    by_sex = Counter((a.sex, a.group) for a in p.animals if not a.retired)
    assert abs(by_sex[("M", "Vehicle")] - by_sex[("M", "Drug")]) <= 1
    assert abs(by_sex[("F", "Vehicle")] - by_sex[("F", "Drug")]) <= 1
    assert workflow.randomise_treatments(p, stratify_by="Sex", seed=5) == res  # reproducible with a seed
    sub = p.animals[1:5]
    res2 = workflow.randomise_treatments(p, sub, ["Drug", "Saline"], seed=1)
    assert set(res2) == {a.id for a in sub} and Counter(res2.values()) == {"Drug": 2, "Saline": 2}
    assert [g.name for g in p.groups] == ["Vehicle", "Drug", "Saline"]
    with pytest.raises(ValueError):
        workflow.randomise_treatments(Project(), [], [])


def test_route_to_zone_measures():
    from manymaze.core.measures import AnalysisSettings
    from manymaze.core.track import Track

    n = 101
    t = np.arange(n) / 10.0
    x = np.linspace(0.0, 100.0, n)  # 1 px per frame along y = 50
    tr = Track(t, x, np.full(n, 50.0), fps=10.0)
    app = Apparatus(zones=[Zone("Goal", rect(60, 40, 20, 20)), Zone("Far", rect(0, 200, 10, 10))])
    s = AnalysisSettings(speed_smoothing_s=0.0, latency_if_never="duration")
    r = analyse(tr, app, s)
    assert r["Goal: latency to first entry (s)"] == pytest.approx(6.0)
    assert r["Goal: distance before first entry (px)"] == pytest.approx(60.0)
    assert r["Goal: path efficiency to first entry"] == pytest.approx(1.0)
    # as ANY-maze: the distance to the zone's nearest edge while outside (0 inside), weighted by time, over the test
    d = np.where((x >= 60) & (x <= 80), 0.0, np.where(x < 60, 60 - x, x - 80))
    assert r["Goal: mean distance from zone (px)"] == pytest.approx(d.mean(), abs=0.01)
    assert r["Far: distance before first entry (px)"] == pytest.approx(r["Total distance (px)"])
    assert math.isnan(r["Far: path efficiency to first entry"])
    # a detour halves the efficiency
    y = np.where(x < 30, 50 + x, 50 + 60 - x)  # up 30 px, then back down by the time it reaches x = 60
    r2 = analyse(Track(t, x, y, fps=10.0), app, s)
    assert r2["Goal: path efficiency to first entry"] == pytest.approx(60 / (60 * math.sqrt(2)), rel=0.01)


def _write_video(path, frames, fps=10):
    import cv2

    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    for f in frames:
        vw.write(f)
    vw.release()
    return str(path)


def _numbered_frames(start, n):
    """Frames whose brightness encodes their number (k * 8), with a bright square moving right."""
    out = []
    for k in range(start, start + n):
        f = np.full((120, 160, 3), (k * 8) % 200, np.uint8)
        f[50:62, 10 + 4 * k:22 + 4 * k] = 255
        out.append(f)
    return out


def test_playlist_videos(tmp_path):
    from manymaze.core.tracking import ArenaJob, DetectionSettings, track_video
    from manymaze.core.video import FrameReader, VideoSource, is_playlist, playlist_parts, write_playlist

    (tmp_path / "rec").mkdir()
    parts = [_write_video(tmp_path / "rec" / f"part{i}.avi", _numbered_frames(5 * i, 5)) for i in range(3)]
    pl = write_playlist(tmp_path / "joined.m3u", parts)
    assert is_playlist(pl) and pl.read_text().splitlines()[1] == "rec/part0.avi"
    assert playlist_parts(pl) == [str(tmp_path / "rec" / f"part{i}.avi") for i in range(3)]
    with VideoSource(str(pl)) as v:
        assert v.frame_count == 15 and v.fps == pytest.approx(10) and (v.width, v.height) == (160, 120)
        for k in (0, 4, 5, 9, 12, 14):  # random access across the part boundaries
            assert abs(float(v.frame_at(k)[5, 5, 0]) - k * 8) < 6, k
        v.seek(4)
        got = [v.read()[1][5, 5, 0] for _ in range(3)]  # sequential reading continues into the next part
        assert np.allclose(got, [32, 40, 48], atol=6)
    for gray in (True, False):
        with FrameReader(str(pl), start=3, gray=gray) as r:
            idx = [i for i, _ in r]
        assert idx == list(range(3, 15))
    (tr,), = track_video(str(pl), [ArenaJob(None, DetectionSettings(method="threshold", contrast="light",
                                                                     threshold=230, min_area_px=20,
                                                                     max_gap_s=0, head_tail=False))])
    assert len(tr) == 15 and tr.detected.all()
    assert np.allclose(tr.x, 15.5 + 4 * np.arange(15), atol=1.0)
    (tmp_path / "empty.m3u").write_text("#EXTM3U\n")
    with pytest.raises(IOError):
        VideoSource(str(tmp_path / "empty.m3u"))


def test_split_recorder(tmp_path):
    from manymaze.core.video import SplitRecorder, recorded_video
    from manymaze.core.video import VideoSource, playlist_parts

    base = tmp_path / "test_0001.avi"
    rec = SplitRecorder(str(base), 10.0, (160, 120), part_frames=4)
    for f in _numbered_frames(0, 10):
        rec.write(f)
    rec.close()
    assert rec.path.endswith("test_0001.m3u") and rec.frames == 10
    assert [p.rsplit("/", 1)[-1] for p in playlist_parts(rec.path)] == \
        ["test_0001_part001.avi", "test_0001_part002.avi", "test_0001_part003.avi"]
    with VideoSource(rec.path) as v:
        assert v.frame_count == 10 and abs(float(v.frame_at(9)[5, 5, 0]) - 72) < 6
    assert recorded_video(str(base)) == rec.path
    assert recorded_video(str(tmp_path / "none.mp4")) is None


def test_archive_roundtrip(demo, tmp_path):
    import shutil

    from manymaze.core.archive import archive_project, extract_archive
    from manymaze.core.video import VideoSource, write_playlist

    src = tmp_path / "src.mmaze"
    shutil.copytree(demo.path, src)
    p = Project.load(src)
    # one test filmed outside the experiment folder, one as an external playlist
    ext = tmp_path / "elsewhere"
    ext.mkdir()
    v1 = _write_video(ext / "single.avi", _numbered_frames(0, 4))
    parts = [_write_video(ext / f"p{i}.avi", _numbered_frames(0, 3)) for i in range(2)]
    pl = write_playlist(ext / "long.m3u", parts)
    t1, t2 = p.add_test(v1, "C1"), p.add_test(str(pl), "C1")
    p.save()
    (src / "backups").mkdir(exist_ok=True)
    (src / "backups" / "project-x.json").write_text("{}")
    before = t1.video
    z = archive_project(p, tmp_path / "exp.zip")
    assert p.get_test(t1.id).video == before  # the experiment itself is unchanged
    out = extract_archive(z, tmp_path / "restored")
    q = Project.load(out)
    assert out.name == f"{p.name}.mmaze" and not (out / "backups").exists()
    assert len(q.tests) == len(p.tests) and q.has_track(q.tests[0])
    for t, n in ((q.get_test(t1.id), 4), (q.get_test(t2.id), 6)):
        assert not Path(t.video).is_absolute() and t.video.startswith("videos/external/")
        with VideoSource(q.abs_path(t.video)) as v:
            assert v.frame_count == n
    # videos inside the experiment folder keep their place
    assert q.tests[0].video == p.tests[0].video
    with pytest.raises(FileExistsError):
        extract_archive(z, tmp_path / "restored")
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("x.mmaze/project.json", "{}")
        zf.writestr("x.mmaze/../../evil.txt", "no")
    with pytest.raises(ValueError):
        extract_archive(bad, tmp_path / "r2")


def test_cli_new_project_actions(demo, tmp_path, capsys):
    import shutil

    from manymaze.cli import main

    d = tmp_path / "c.mmaze"
    shutil.copytree(demo.path, d)
    main(["project", str(d), "results", "--wide", "-o", str(tmp_path / "w.csv")])
    head = (tmp_path / "w.csv").read_text().splitlines()
    assert head[0].startswith("Animal,Group") and len(head) == 1 + len(demo.animals)
    main(["project", str(d), "events", "-o", str(tmp_path / "e.csv")])
    assert "Zone entry" in (tmp_path / "e.csv").read_text()
    main(["project", str(d), "protocol", "-o", str(tmp_path / "p.html")])
    main(["project", str(d), "archive", "-o", str(tmp_path / "a.zip")])
    assert (tmp_path / "p.html").exists() and (tmp_path / "a.zip").stat().st_size > 1000


def test_test_ends_in_zone():
    from manymaze.core.measures import AnalysisSettings, end_of_test
    from manymaze.core.project import Behaviour
    from manymaze.core.track import Track

    n = 101
    t = np.arange(n) / 10.0
    x = np.concatenate([np.linspace(0, 70, 71), np.full(30, 70.0)])  # reaches the goal at 6 s and stays
    tr = Track(t, x, np.full(n, 50.0), fps=10.0)
    app = Apparatus(zones=[Zone("Goal", rect(60, 40, 20, 20))])
    s = AnalysisSettings(speed_smoothing_s=0.0, end_zone="Goal", end_zone_s=2.0)
    assert end_of_test(tr, app, s) == pytest.approx(8.0)
    r = analyse(tr, app, s, events=[{"behaviour": "X", "t": 7.0, "t_end": 9.5}, {"behaviour": "X", "t": 9.0}],
                behaviours=[Behaviour("X", kind="state")])
    assert r["Test duration (s)"] == pytest.approx(8.0)
    assert r["Goal: time (s)"] == pytest.approx(2.1, abs=0.11)
    assert r["X: duration (s)"] == pytest.approx(1.0)
    full = analyse(tr, app, AnalysisSettings(speed_smoothing_s=0.0))
    assert full["Test duration (s)"] == pytest.approx(10.1, abs=0.11)
    # a stay that is too short does not end the test
    assert end_of_test(tr, app, AnalysisSettings(end_zone="Goal", end_zone_s=5.0)) is None
    assert end_of_test(tr, app, AnalysisSettings(end_zone="Goal")) == pytest.approx(6.0)
    assert end_of_test(tr, app, AnalysisSettings(end_zone="Nowhere")) is None


def test_invalid_colours_are_reported():
    from manymaze.core.tracking import ArenaTracker, DetectionSettings, hex_to_hsv

    assert hex_to_hsv("#f00") == hex_to_hsv("ff0000")
    for bad in ("red", "#12345", "#gg0000"):
        with pytest.raises(ValueError, match="Invalid colour"):
            hex_to_hsv(bad)
    with pytest.raises(ValueError, match="Invalid colour"):
        ArenaTracker(DetectionSettings(method="colour", target_colour="blue"))
    with pytest.raises(ValueError, match="Invalid colour"):
        ArenaTracker(DetectionSettings(n_animals=2, identity_colours="#ff0000, purple"))


def test_trial_means():
    rows = [{"Test": i, "Animal": a, "Group": "G", "Stage": st, "Trial": k, "Period": "Whole test", "Lat": v,
             "Strategy": "direct"}
            for i, (a, st, k, v) in enumerate([("A", "D1", 1, 10.0), ("A", "D1", 2, 20.0), ("A", "D2", 1, 5.0),
                                               ("B", "D1", 1, 30.0), ("B", "D1", 2, float("nan"))])]
    out = export.trial_means(rows, ["Lat", "Strategy"])
    assert out == [{"Animal": "A", "Group": "G", "Stage": "D1", "Trials": 2, "Lat": 15.0},
                   {"Animal": "A", "Group": "G", "Stage": "D2", "Trials": 1, "Lat": 5.0},
                   {"Animal": "B", "Group": "G", "Stage": "D1", "Trials": 2, "Lat": 30.0}]


def test_episode_exit_and_first_zone_measures():
    from manymaze.core.apparatus import Line
    from manymaze.core.measures import AnalysisSettings, behaviour_measures
    from manymaze.core.project import Behaviour
    from manymaze.core.track import Track

    fps = 10.0
    # 0-1 s still at (50, 200); 1-2 s run into the left zone; 2-3 s still there; 3-4 s run to the right zone;
    # 4-6 s still there
    pts = np.concatenate([np.tile([50.0, 200.0], (10, 1)),
                          np.column_stack([np.linspace(50, 100, 10), np.full(10, 200.0)]),
                          np.tile([100.0, 200.0], (10, 1)),
                          np.column_stack([np.linspace(100, 300, 10), np.full(10, 200.0)]),
                          np.tile([300.0, 200.0], (20, 1))])
    tr = Track(t=np.arange(len(pts)) / fps, x=pts[:, 0], y=pts[:, 1], fps=fps)
    app = Apparatus(arena=rect(0, 0, 400, 400), px_per_cm=1.0,
                    zones=[Zone("Left", rect(80, 150, 40, 100)), Zone("Right", rect(280, 150, 40, 100))],
                    lines=[Line("Mid", 200, 0, 200, 400), Line("Quarter", 150, 0, 150, 400)])
    s = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0)
    r = analyse(tr, app, s)
    assert r["First zone entered"] == "Left"
    assert r["Left: was first zone entered"] == "Yes" and r["Right: was first zone entered"] == "No"
    assert r["Left: exits"] == 1 and r["Right: exits"] == 0
    assert r["Left: latency to first exit (s)"] == pytest.approx(r["Left: time of last exit (s)"])
    assert r["Right: latency to first exit (s)"] == pytest.approx(r["Test duration (s)"])  # never: the duration
    assert r["Left: latency to last entry (s)"] == pytest.approx(r["Left: latency to first entry (s)"])
    assert r["Left: shortest visit (s)"] == pytest.approx(r["Left: longest visit (s)"])
    assert r["Total line crossings"] == r["Mid: crossings"] + r["Quarter: crossings"] == 2
    assert r["Right: initial distance from zone (cm)"] == pytest.approx(230, abs=1)
    assert r["Right: max distance from zone (cm)"] == pytest.approx(230, abs=1)
    assert 0 < r["Right: min distance from zone when outside (cm)"] < 30
    assert r["Mobile episodes"] == 2
    assert r["Shortest mobile episode (s)"] <= r["Longest mobile episode (s)"]
    assert r["Shortest immobile episode (s)"] <= r["Longest immobile episode (s)"]
    assert r["Latency to first mobile episode (s)"] == pytest.approx(1.0, abs=0.15)
    assert r["Latency to last mobile episode (s)"] == pytest.approx(3.0, abs=0.15)
    assert r["Latency to last immobile episode (s)"] == pytest.approx(4.0, abs=0.3)

    b = behaviour_measures([{"behaviour": "Groom", "t": 1.0, "t_end": 3.0}, {"behaviour": "Groom", "t": 5.0,
                                                                             "t_end": 5.5}],
                           [Behaviour("Groom", kind="state")], 0.0, 10.0)
    assert b["Groom: shortest bout (s)"] == pytest.approx(0.5)
    assert b["Groom: latency to first release (s)"] == pytest.approx(3.0)


def test_test_date_time_and_notes_info_columns(demo):
    t = demo.tests[0]
    t.recorded_at, t.notes = "2026-10-05T14:30:00", "lights flickered"
    info = demo.test_info(t)
    assert (info["Test date"], info["Day of week"], info["Test time"], info["Test notes"]) == \
        ("2026-10-05", "Monday", "14:30:00", "lights flickered")
    t.recorded_at = ""
    assert demo.test_info(t)["Test date"] == ""
