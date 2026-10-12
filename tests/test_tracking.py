import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.export import export_results, html_report
from manymaze.core.live import LiveSession
from manymaze.core.project import Project
from manymaze.core.tracking import ArenaJob, DetectionSettings, track_video
from manymaze.core.video import VideoSource


@pytest.fixture(scope="module")
def of_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    pos = syn.random_walk(400, (50, 50, 350, 350), speed=3, seed=3)
    p = d / "of.avi"
    noses = syn.make_video(p, pos, arena=("rect", 50, 50, 300, 300))
    return str(p), pos, noses


def test_tracking_accuracy(of_video):
    path, pos, noses = of_video
    app = templates.build("open_field", 50, 50, 300, 300, size_cm=40)
    [[tr]] = track_video(path, [ArenaJob(app, DetectionSettings())])
    assert len(tr) == len(pos)
    err = np.hypot(tr.x - pos[:, 0], tr.y - pos[:, 1])
    assert np.nanmedian(err) < 4
    herr = np.hypot(tr.hx - noses[:, 0], tr.hy - noses[:, 1])
    assert np.mean(herr < 12) > 0.85


@pytest.mark.parametrize("contrast,method", [("dark", "background"), ("auto", "background"),
                                             ("dark", "threshold")])
def test_detection_modes(of_video, contrast, method):
    path, pos, _ = of_video
    app = templates.build("open_field", 50, 50, 300, 300)
    s = DetectionSettings(contrast=contrast, method=method, threshold=25 if method == "background" else 120)
    [[tr]] = track_video(path, [ArenaJob(app, s)])
    assert tr.detected.mean() > 0.95


def test_two_animals_identity(tmp_path):
    n = 200
    a = syn.line_path((80, 100), (320, 100), n)
    b = syn.line_path((320, 300), (80, 300), n)
    p = tmp_path / "two.avi"
    syn.make_video(p, a, extra_animals=[b])
    app = templates.build("open_field", 0, 0, 400, 400)
    [[t1, t2]] = track_video(str(p), [ArenaJob(app, DetectionSettings(n_animals=2))])
    # identity stays with the same animal for the whole video
    first_is_a = abs(t1.y[0] - 100) < abs(t1.y[0] - 300)
    ta = t1 if first_is_a else t2
    assert np.nanmax(np.abs(ta.y - 100)) < 10
    assert abs(ta.x[-1] - 320) < 10


def test_multiple_arenas_one_pass(tmp_path):
    n = 120
    left = syn.line_path((60, 200), (160, 200), n)
    right = syn.line_path((340, 120), (340, 300), n)
    p = tmp_path / "multi.avi"
    syn.make_video(p, left, extra_animals=[right])
    a1 = templates.build("open_field", 0, 0, 200, 400)
    a2 = templates.build("open_field", 200, 0, 200, 400)
    res = track_video(str(p), [ArenaJob(a1, DetectionSettings()), ArenaJob(a2, DetectionSettings())])
    assert abs(np.nanmean(res[0][0].x) - 110) < 10
    assert abs(np.nanmean(res[1][0].x) - 340) < 10


def test_project_roundtrip_and_results(tmp_path, of_video):
    path, _, _ = of_video
    proj = Project(name="t", test_duration_s=10)
    proj.save(tmp_path / "p.mmaze")
    proj.apparatus.append(templates.build("open_field", 50, 50, 300, 300, size_cm=40))
    proj.ensure_animal("M1", "WT")
    t = proj.add_test(path, "M1", stage="D1")
    proj.track_test(t)
    proj.save()
    p2 = Project.load(tmp_path / "p.mmaze")
    assert p2.tests[0].status == "tracked"
    assert p2.groups[0].name == "WT"
    rows = p2.results(segmented=False)
    assert rows[0]["Animal"] == "M1" and rows[0]["Group"] == "WT"
    assert rows[0]["Test duration (s)"] == pytest.approx(10.0, abs=0.05)
    export_results(p2, tmp_path / "r.xlsx", segmented=True)
    export_results(p2, tmp_path / "r.csv")
    html_report(p2, tmp_path / "r.html", stats_measures=["Total distance (cm)"])
    assert (tmp_path / "r.xlsx").stat().st_size > 1000
    assert "Total distance" in (tmp_path / "r.html").read_text()


def test_start_on_detection(tmp_path):
    pos = syn.line_path((100, 200), (300, 200), 100)
    p = tmp_path / "late.avi"
    # animal appears at frame 50: render empty frames first by drawing it off-screen
    off = np.repeat([[-200.0, -200.0]], 50, axis=0)
    syn.make_video(p, np.vstack([off, pos]))
    proj = Project(start_mode="on_detection", test_duration_s=2.0)
    proj.save(tmp_path / "s.mmaze")
    proj.apparatus.append(templates.build("open_field", 0, 0, 400, 400))
    t = proj.add_test(str(p), "A")
    proj.detection.background = "median"
    [tr] = proj.track_test(t)
    assert tr.t[0] == 0
    assert abs(tr.x[0] - 100) < 8
    assert tr.duration == pytest.approx(2.0, abs=0.05)


def test_live_session_with_procedures(tmp_path, of_video):
    path, pos, _ = of_video
    app = templates.build("open_field", 50, 50, 300, 300, size_cm=40)
    rules = [{"trigger": "zone_enter", "zone": "Centre", "action": "serial", "payload": "LED"},
             {"trigger": "time", "time_s": 1, "action": "mark", "payload": "Tone"}]
    rec = tmp_path / "rec.avi"
    sess = LiveSession(app, DetectionSettings(background="adaptive"), duration_s=4, start_mode="on_detection",
                       procedures=rules, record_path=str(rec))
    with VideoSource(path) as v:
        while sess.state != "finished":
            ok, f = v.read()
            if not ok:
                break
            sess.process(f)
    tr = sess.track()
    assert sess.state == "finished"
    assert tr.duration == pytest.approx(4.0, abs=0.1)
    assert any(e["behaviour"] == "Tone" for e in sess.events)
    assert rec.exists() and rec.stat().st_size > 0


def test_epm_video_end_to_end(tmp_path):
    app = templates.build("epm", 50, 50, 300, 300)
    c = app.zone("Centre").shape.centroid()
    west = app.zone("Open arm W").shape.centroid()
    north = app.zone("Closed arm N").shape.centroid()
    # centre -> closed arm N (long stay) -> centre -> open arm W -> centre
    way = [c, north, c, west, c]
    pos = syn.waypoint_path(way, speed_px_per_frame=3, pauses={1: 100, 3: 40})
    p = tmp_path / "epm.avi"
    syn.make_video(p, pos, size=(400, 400))
    [[tr]] = track_video(str(p), [ArenaJob(app, DetectionSettings())])
    from manymaze.core.measures import AnalysisSettings, analyse

    r = analyse(tr, app, AnalysisSettings())
    assert r["Open arm entries"] == 1 and r["Closed arm entries"] == 1
    assert r["Open arm entries (%)"] == pytest.approx(50.0)
    assert 15 < r["Open arm time (%)"] < 50


def test_empty_arena_mask_does_not_crash():
    from manymaze.core.tracking import ArenaTracker

    tr = ArenaTracker(DetectionSettings(), np.zeros((10, 10), np.uint8))
    assert tr.roi == (0, 0, 10, 10)
    dets, _ = tr.process(np.zeros((10, 10), np.uint8))
    assert not dets[0].detected


def _segments(start, parts):
    """Positions and headings (degrees) for (heading, px per frame, frames) segments; speed 0 = still."""
    pos, ang = [np.asarray(start, float)], [parts[0][0]]
    for heading, speed, n in parts:
        u = np.array([np.cos(np.radians(heading)), np.sin(np.radians(heading))])
        for _ in range(n):
            pos.append(pos[-1] + speed * u)
            ang.append(heading)
    return np.array(pos), np.array(ang, float)


def _head_error(tmp_path, pos, ang):
    p = tmp_path / "v.avi"
    noses = syn.make_video(p, pos, size=(640, 480), angles=ang, seed=7)
    app = templates.build("open_field", 20, 20, 600, 440)
    [[tr]] = track_video(str(p), [ArenaJob(app, DetectionSettings())])
    return np.hypot(tr.hx - noses[:, 0], tr.hy - noses[:, 1])


def test_head_after_slow_approach_and_stop(tmp_path):
    # H-P01: a quick about-turn, then a slow approach (2 px per frame) and a stop.  The movement-direction vote
    # used to need ¼ body length in 5 frames, so it never corrected the inversion caught at the about-turn and the
    # head stayed on the tail for the whole stop.  (It walks away at the end: an animal still for most of the video
    # would fade into the median background.)
    pos, ang = _segments((60, 240), [(0, 4, 40), (180, 4, 8), (0, 2, 30), (0, 0, 60), (90, 4, 50)])
    herr = _head_error(tmp_path, pos, ang)
    assert np.mean(herr < 12) > 0.8  # each about-turn costs a few frames
    assert np.mean(herr[79:139] < 12) > 0.95  # the stop


def test_head_after_turning_in_place(tmp_path):
    # stopping and then turning round in place is not an inversion: the older movement must not flip the head back
    parts = [(0, 4, 40), (0, 0, 5)] + [(15 * (k + 1), 0, 1) for k in range(12)] + [(180, 0, 50), (180, 4, 40)]
    pos, ang = _segments((100, 240), parts)
    herr = _head_error(tmp_path, pos, ang)
    assert np.mean(herr[58:108] < 12) > 0.95  # still after the turn
