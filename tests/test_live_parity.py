"""Live testing parity: several sessions per source, pause, start modes, schedules, keys, camera options,
observation-only sessions and the thin-structure eraser."""

import datetime as dt

import cv2
import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.camera import CameraView, FramePacer, SourceSpec, TransformedSource, camera_settings, \
    merge_frames, set_camera_settings
from manymaze.core.live import LiveSession, ObservationSession, annotate_recording
from manymaze.core.livegroup import ClockSchedule, LiveGroup
from manymaze.core.project import Project
from manymaze.core.session import save_live_test
from manymaze.core.tracking import ArenaTracker, DetectionSettings, median_background
from manymaze.core.video import VideoSource


def _floor(size=(200, 200), level=200):
    return np.full((size[1], size[0]), level, np.uint8)


def _frame(x, y, angle=0.0, size=(200, 200), wire=None):
    img = _floor(size)
    syn.draw_mouse(img, x, y, angle)
    if wire is not None:
        cv2.line(img, *wire, 110, 2)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


@pytest.fixture(scope="module")
def two_arena_video(tmp_path_factory):
    """One camera image (400×200) showing two open fields, one mouse in each."""
    d = tmp_path_factory.mktemp("lp")
    n = 125
    a = syn.random_walk(n, (10, 10, 190, 190), speed=3, seed=1)
    b = syn.random_walk(n, (210, 10, 390, 190), speed=3, seed=2)
    p = d / "two.avi"
    syn.make_video(p, a, size=(400, 200), extra_animals=[b], noise=1.0)
    return str(p), a, b


def _apparatus_pair():
    left = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    left.name = "Left"
    right = templates.build("open_field", 210, 10, 180, 180, size_cm=40)
    right.name = "Right"
    return left, right


def _bg(path):
    with VideoSource(path) as v:
        return median_background([v.frame_at(i) for i in range(0, v.frame_count, 8)])


def test_two_sessions_from_one_video(two_arena_video, tmp_path):
    path, a, b = two_arena_video
    left, right = _apparatus_pair()
    g = LiveGroup()
    key = g.add_source(SourceSpec(path))
    bg = _bg(path)
    sessions = []
    for app, dur in ((left, 4.0), (right, 3.0)):
        s = LiveSession(app, DetectionSettings(background="frame"), duration_s=dur, start_mode="manual",
                        record_path=str(tmp_path / f"{app.name}.avi"), record_overlay=app is left)
        s.set_background(bg)
        sessions.append(s)
        g.add_session(key, s, app.name)
    e_left, e_right = g.entries
    with VideoSource(path) as v:
        for i in range(v.frame_count):
            ok, f = v.read()
            if i == 5:
                assert g.key("PageDown") == "start"  # remote presenter / keyboard collective start
            g.process(key, f, i / 25)
            if i == 10:
                disp = g.render(key, f)
                assert disp.shape == f.shape and not np.array_equal(disp, f)
    assert e_left.state == "finished" and e_right.state == "finished"
    tl, tr = sessions[0].track(), sessions[1].track()
    assert tl.duration == pytest.approx(4.0, abs=0.1) and tr.duration == pytest.approx(3.0, abs=0.1)
    # each session tracks the animal of its own arena
    i0 = 6
    assert np.nanmedian(np.hypot(tl.x - a[i0:i0 + len(tl), 0], tl.y - a[i0:i0 + len(tl), 1])) < 4
    assert np.nanmedian(np.hypot(tr.x - b[i0:i0 + len(tr), 0], tr.y - b[i0:i0 + len(tr), 1])) < 4
    assert np.all(tl.x[np.isfinite(tl.x)] < 200) and np.all(tr.x[np.isfinite(tr.x)] > 200)
    for s in sessions:
        with VideoSource(s.record_path) as v:
            assert v.frame_count == len(s.track())
    # live statistics
    st = sessions[0].stats
    assert st.distance > 10 and set(st.names) >= {"Centre"}
    assert sum(t for n, t, _, _ in st.rows() if n in ("Centre", "Periphery")) == pytest.approx(4.0, abs=0.2)
    assert st.entries["Arena"] == 1 and st.latency["Arena"] == 0.0
    assert len(st.series("speed", 2.0)[0]) > 5


def test_pause_stops_the_clock(two_arena_video):
    path, a, _ = two_arena_video
    left, _ = _apparatus_pair()
    s = LiveSession(left, DetectionSettings(background="frame"), duration_s=2.0)
    s.set_background(_bg(path))
    s.score("Ignored")  # not running yet
    with VideoSource(path) as v:
        i = 0
        while s.state != "finished":
            ok, f = v.read()
            assert ok
            if i == 20:
                assert s.pause() and s.state == "paused"
                assert s.score("Rearing", "state") is None
            if i == 35:
                assert s.resume()
            if i == 10:
                s.score("Rearing", "state")
            if i == 40:
                s.score("Rearing", "state")
            s.process(f, i / 25)
            i += 1
    t = s.track().t
    assert np.max(np.diff(t)) == pytest.approx(0.04, abs=1e-6)  # no jump: the clock stopped while paused
    assert len(t) == 51 and i == 51 + 15
    assert s.pauses == [[pytest.approx(0.76), pytest.approx(0.76)]]
    assert s.pause_log[0]["duration_s"] == pytest.approx(0.6, abs=0.05)
    ev = s.events[0]
    assert ev["behaviour"] == "Rearing" and ev["t"] == pytest.approx(0.36) and ev["t_end"] == pytest.approx(0.96)


def test_experimenter_leaves_start():
    app = templates.build("open_field", 10, 10, 180, 180)
    frames = []
    for i in range(60):
        img = _floor()
        if 5 <= i < 25:  # the experimenter's hand comes in from the edge, puts the mouse down and leaves
            reach = min(i - 5, 8) * 12 if i < 17 else max(0, 25 - i) * 12
            cv2.rectangle(img, (0, 70), (20 + reach, 140), 60, -1)
        if i >= 12:
            syn.draw_mouse(img, 110, 100, 0)
        frames.append(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    bg = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)

    def run(mode):
        s = LiveSession(app, DetectionSettings(background="frame"), duration_s=10, start_mode=mode,
                        start_hold_s=0.2)
        s.set_background(bg)
        phases = []
        for i, f in enumerate(frames):
            s.process(f, i / 25)
            phases.append(s.start_phase)
            if s.state == "running":
                return i, phases
        return None, phases

    i_det, _ = run("on_detection")
    i_exp, phases = run("experimenter_leaves")
    assert i_det is not None and i_det < 12  # the hand alone already counts as an "animal"
    assert i_exp is not None and 25 <= i_exp <= 31
    assert "leaving" in phases and "animal" in phases
    # nothing ever enters: never starts
    s = LiveSession(app, DetectionSettings(background="frame"), start_mode="experimenter_leaves")
    s.set_background(bg)
    for i in range(10):
        s.process(frames[40], i / 25)
    assert s.state == "waiting" and s.start_phase == "experimenter"


def test_schedule_and_keys():
    now = dt.datetime(2026, 10, 6, 4, 59, 0)
    clock = [now]
    g = LiveGroup(clock=lambda: clock[0])
    app = templates.build("open_field", 10, 10, 180, 180)
    s = LiveSession(app, DetectionSettings(background="frame"), start_mode="manual", duration_s=0)
    s.set_background(_floor())
    e = g.add_session("cam", s, "A")
    sch = g.schedule("05:00", daily=True)
    assert sch.next_fire == dt.datetime(2026, 10, 6, 5, 0)
    assert g.tick(dt.datetime(2026, 10, 6, 4, 59, 30)) == []
    g.process("cam", _frame(100, 100), 0.0)
    assert e.state == "waiting"
    assert g.tick(dt.datetime(2026, 10, 6, 5, 0, 1)) == [sch]
    g.process("cam", _frame(100, 100), 0.04)
    assert e.state == "running"
    assert sch.next_fire == dt.datetime(2026, 10, 7, 5, 0) and sch in g.schedules
    # one-off schedule set after today's time fires tomorrow, then is removed
    one = ClockSchedule("03:30", created=dt.datetime(2026, 10, 6, 6, 0))
    assert one.next_fire == dt.datetime(2026, 10, 7, 3, 30)
    one.fired(dt.datetime(2026, 10, 7, 3, 30))
    assert one.next_fire is None
    # collective pause / resume / stop through keys and buttons
    g.pause_all()
    assert e.state == "paused"
    assert g.key("space") == "start" and e.state == "running"
    assert g.key("x") is None
    assert g.key("B") == "stop" and e.state == "finished" and not e.aborted
    assert g.finished_unsaved() == [e]


def test_source_end_and_save(two_arena_video, tmp_path):
    path, _, _ = two_arena_video
    left, right = _apparatus_pair()
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    proj.apparatus += [left, right]
    g = LiveGroup()
    key = g.add_source(SourceSpec(path))
    bg = _bg(path)
    s1 = LiveSession(left, DetectionSettings(background="frame"), duration_s=0)
    s2 = LiveSession(right, DetectionSettings(background="frame"), duration_s=0, start_mode="manual")
    for s in (s1, s2):
        s.set_background(bg)
    e1, e2 = g.add_session(key, s1, "L"), g.add_session(key, s2, "R")
    with VideoSource(path) as v:
        for i in range(30):
            ok, f = v.read()
            g.process(key, f, i / 25)
    s1.score("Grooming")
    g.source_ended(key)
    assert e1.state == e2.state == "finished" and not e1.aborted and e2.aborted
    t = proj.add_test("", "A1", "Left")
    assert save_live_test(proj, t, s1)
    assert t.status == "tracked" and len(proj.load_tracks(t)[0]) == 30
    assert t.events[0]["behaviour"] == "Grooming" and t.pauses == []
    assert not save_live_test(proj, proj.add_test("", "A2", "Right"), s2)


def test_threaded_sources(two_arena_video):
    import time

    path, _, _ = two_arena_video
    left, right = _apparatus_pair()
    g = LiveGroup()
    k1 = g.add_source(SourceSpec(path))
    k2 = g.add_source(SourceSpec(path, view=CameraView(flip="h")))
    assert k1 != k2
    s1 = LiveSession(left, DetectionSettings(background="adaptive"), duration_s=1.0)
    s2 = LiveSession(left, DetectionSettings(background="adaptive"), duration_s=1.0)
    g.add_session(k1, s1)
    g.add_session(k2, s2)
    g.start_sources(speed=8.0)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10 and not (s1.state == s2.state == "finished"):
        time.sleep(0.05)
    disp = g.runners[k1].take_display()
    g.close()
    assert s1.state == s2.state == "finished"
    assert g.runners == {} and disp is not None and disp.shape == (200, 400, 3)
    # the flipped camera sees the right-hand mouse inside the left arena
    assert abs(np.nanmedian(s1.track().x) - np.nanmedian(s2.track().x)) > 10


def test_camera_view_and_merge(tmp_path):
    f = np.zeros((100, 200, 3), np.uint8)
    f[10:20, 30:40] = 255
    v = CameraView(crop=[20, 0, 100, 50])
    assert v.apply(f).shape == (50, 100, 3) and v.apply(f)[10:20, 10:20].min() == 255
    r = CameraView(rotate=90)
    assert r.apply(f).shape == (200, 100, 3) and r.output_size(200, 100) == (100, 200)
    z = CameraView(zoom=2.0, pan=[0.175, 0.15])
    out = z.apply(f)
    assert out.shape == f.shape and out[:, :, 0].mean() > f[:, :, 0].mean() * 3
    assert CameraView(flip="h").apply(f)[10:20, 160:170].min() == 255
    assert CameraView.from_dict(CameraView(crop=[1, 2, 3, 4], rotate=270).to_dict()).crop == [1, 2, 3, 4]
    assert CameraView().is_identity and CameraView.from_dict({"zoom": 0.3}).zoom == 1.0
    assert merge_frames(f, f[:50], "side").shape == (100, 400, 3)
    assert merge_frames(f, f[:, :50], "stack").shape == (200, 200, 3)
    # merging two "cameras" (video files) into one source
    pos = syn.line_path((50, 50), (150, 150), 10)
    p = tmp_path / "m.avi"
    syn.make_video(p, pos, size=(200, 200))
    src = SourceSpec(str(p), second=str(p), layout="stack", view=CameraView(crop=[0, 0, 200, 300])).open()
    assert isinstance(src, TransformedSource) and (src.width, src.height) == (200, 300)
    ok, fr = src.read()
    assert ok and fr.shape == (300, 200, 3)
    src.release()
    assert SourceSpec(str(p)).key == f"file:{p}" and SourceSpec(1, 2).key == "camera:1+camera:2"
    assert SourceSpec.from_dict(SourceSpec(3, view=CameraView(rotate=180)).to_dict()).view.rotate == 180
    proj = Project(name="p")
    set_camera_settings(proj, "camera:0", {"view": CameraView(flip="v").to_dict()})
    assert camera_settings(proj, "camera:0")["view"]["flip"] == "v" and camera_settings(proj, "camera:9") == {}
    # pacing: files are paced at fps × speed with an injectable clock
    clock, slept = [0.0], []
    pacer = FramePacer(25, False, 2.0, clock=lambda: clock[0], sleep=slept.append)
    assert [pacer.next() for _ in range(3)] == [0.0, 0.04, 0.08] and slept == [pytest.approx(0.02),
                                                                                pytest.approx(0.04)]
    cam = FramePacer(25, True, clock=lambda: clock[0])
    cam.next()
    clock[0] = 0.2
    assert cam.next() == pytest.approx(0.2)  # cameras: wall-clock time (the session detects dropped frames)
    img = annotate_recording(f, 61.5, ["Tone"])
    assert img.shape == f.shape and img.sum() > f.sum()


def test_observation_session(tmp_path):
    clock = [100.0]
    s = ObservationSession(duration_s=10, clock=lambda: clock[0])
    assert s.state == "waiting" and s.elapsed == 0
    s.request_start()
    clock[0] = 102.0
    s.score("Grooming", "state")
    clock[0] = 103.0
    s.pause()
    clock[0] = 108.0
    assert s.elapsed == pytest.approx(3.0)
    s.resume()
    clock[0] = 109.0
    s.score("Defecation")
    s.score("Grooming", "state")
    clock[0] = 200.0
    s.tick()
    assert s.state == "finished" and s.elapsed == 10.0
    assert [(e["behaviour"], e["t"], e["t_end"]) for e in s.events] == [("Grooming", 2.0, 4.0),
                                                                         ("Defecation", 4.0, None)]
    assert s.pauses == [[3.0, 3.0]] and s.pause_log[0]["duration_s"] == 5.0
    proj = Project(name="o")
    proj.save(tmp_path / "o.mmaze")
    t = proj.add_test("", "A1")
    assert save_live_test(proj, t, s) and t.status == "scored" and len(t.events) == 2 and t.video == ""


@pytest.mark.parametrize("method", ["background", "threshold"])
def test_erase_thin_wires(method):
    wire = ((100, 0), (100, 199))  # a 2 px wire across the arena, over the animal
    clean = _frame(100, 100)
    wired = _frame(100, 100, wire=wire)
    bg = cv2.cvtColor(_frame(-100, -100, wire=wire), cv2.COLOR_BGR2GRAY)  # empty arena (mouse off-image)

    def detect(frame, erase):
        s = DetectionSettings(method=method, threshold=25 if method == "background" else 150, blur=0,
                              morph_open=0, morph_close=0, erase_thin_px=erase, contrast="dark")
        tr = ArenaTracker(s)
        tr.set_background(bg)
        dets, fg = tr.process(frame)
        n_blobs = len([c for c in cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
                       if cv2.contourArea(c) > 20])
        return dets[0], n_blobs

    ref, _ = detect(clean, 0)
    raw, n_raw = detect(wired, 0)
    fixed, n_fixed = detect(wired, 2)
    if method == "background":
        assert n_raw >= 2 and raw.area < 0.75 * ref.area  # the wire splits the animal
    else:
        assert raw.area > 1.2 * ref.area  # the wire is taken for (part of) the animal
    assert n_fixed == 1
    assert abs(fixed.x - ref.x) < 2 and abs(fixed.y - ref.y) < 2
    assert fixed.area == pytest.approx(ref.area, rel=0.3)
    # default settings with the eraser on still track normally
    s = DetectionSettings(erase_thin_px=3)
    tr = ArenaTracker(s)
    tr.set_background(bg)
    d = tr.process(_frame(60, 60, wire=wire))[0][0]
    assert d.detected and abs(d.x - 60) < 4


def test_live_occupancy_rules_and_moveable_zones():
    from manymaze.core.apparatus import Apparatus, Zone
    from manymaze.core.geometry import Polygon
    from manymaze.core.live import LiveOccupancy
    from manymaze.core.measures import AnalysisSettings
    from manymaze.core.tracking import Detection

    sq = lambda x0, y0, x1, y1: Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    app = Apparatus(zones=[Zone("Arena", sq(0, 0, 200, 200)), Zone("Food", sq(150, 0, 200, 50), entry_rule="head"),
                           Zone("Nest", sq(0, 150, 50, 200), hidden=True),
                           Zone("Object", sq(90, 90, 110, 110), investigation_distance_cm=10.0)])
    occ = LiveOccupancy(app, AnalysisSettings())
    det = lambda x, y, hx, hy: Detection(x=x, y=y, hx=hx, hy=hy, tx=2 * x - hx, ty=2 * y - hy, area=300.0,
                                         angle=0.0, detected=True)
    z, h = occ.update(det(140, 30, 160, 30))  # centre outside "Food", head inside: head entry rule
    assert z["Food"] and h["Food"] and not z["Object"]
    z, _ = occ.update(det(70, 100, 85, 100))  # head 5 px from the object (< 10 px investigation distance)
    assert z["Object"] and z["Arena"]
    occ.update(det(40, 140, 40, 150))
    z, _ = occ.update(Detection())  # lost next to the hidden nest: in the nest
    assert z["Nest"] and not z["Arena"]
    z, _ = occ.update(Detection())
    assert z["Nest"]
    # moveable zones: the test's own position of a zone
    of = templates.build("open_field", 10, 10, 180, 180)
    moved = of.zone("Centre").shape.translated(40, 0).to_dict()
    s = LiveSession(of, DetectionSettings(background="frame"), zone_overrides={"Centre": moved})
    assert s.apparatus.zone("Centre").shape.centroid()[0] == pytest.approx(of.zone("Centre").shape.centroid()[0] + 40)
    assert of.zone("Centre").shape.to_dict() != moved  # the project's apparatus is untouched
