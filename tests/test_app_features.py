"""Application features (TODO §14): automatic freezing threshold, live charts of every parameter, live point /
sequence / input statistics, the orientation beam, apparatus geometry changed during a test, start when the
experimenter leaves (videos), automatic recovery of a camera that drops out, zone map images, the disk-space check
and the catalogue of statistical tests."""

import collections
import math
import threading
import time

import cv2
import numpy as np
import pytest

from manymaze.core import diskspace, freezing, plots, templates
from manymaze.core import stats as st
from manymaze.core import synthetic as syn
from manymaze.core.analyses import categorical
from manymaze.core.apparatus import POSITION_KEY, Apparatus, PointOfInterest, Sequence, Zone
from manymaze.core.autostart import experimenter_leaves_start, hand_frames
from manymaze.core.camera import SourceReader, SourceSpec
from manymaze.core.charts import is_cumulative, parameters
from manymaze.core.geometry import Polygon
from manymaze.core.live import LiveSession, LiveStats
from manymaze.core.livemonitor import (CHART_PREFIX, LiveCharts, LivePoints, chart_parameters, input_rows,
                                       sequence_rows)
from manymaze.core.measures import AnalysisSettings, kinematics
from manymaze.core import project as proj
from manymaze.core.project import Project, _trim_all_on_detection
from manymaze.core.session import save_live_test
from manymaze.core.track import Track
from manymaze.core.tracking import Detection, DetectionSettings, draw_tracking, heading_of


def _floor(size=(200, 200), level=200):
    return np.full((size[1], size[0]), level, np.uint8)


def _frame(x, y, angle=0.0, size=(200, 200)):
    img = _floor(size)
    syn.draw_mouse(img, x, y, angle)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _track_with_motion(motion, area=400.0, fps=25.0):
    n = len(motion)
    t = np.arange(n) / fps
    return Track(t, np.full(n, 50.0), np.full(n, 50.0), area=np.full(n, area), motion=np.asarray(motion) * area / 100,
                 fps=fps)


# ====================================================================== automatic freezing threshold
def _bimodal(n_still=400, n_moving=600, seed=0):
    rng = np.random.default_rng(seed)
    still = np.abs(rng.normal(0.3, 0.15, n_still))  # camera noise while the animal freezes
    moving = rng.lognormal(math.log(12), 0.5, n_moving)
    return np.concatenate([moving[:300], still, moving[300:]])


def test_auto_freezing_thresholds_from_the_motion_distribution():
    motion = _bimodal()
    on, off = freezing.auto_thresholds(motion)
    assert 0.6 < on < 8 and off == pytest.approx(1.5 * on, rel=1e-3)  # between the noise and the movement
    # the sensitivity moves the threshold (higher = stricter, as in ANY-maze): +25 halves it, -25 doubles it
    on_hi, _ = freezing.auto_thresholds(motion, 75)
    on_lo, _ = freezing.auto_thresholds(motion, 25)
    assert on_hi == pytest.approx(on / 2, rel=0.01) and on_lo == pytest.approx(2 * on, rel=0.01)
    assert freezing.auto_thresholds(motion[:10]) is None and freezing.auto_thresholds(np.ones(100)) is None


def test_auto_freezing_in_the_analysis_and_manual_default():
    tr = _track_with_motion(_bimodal())
    manual = AnalysisSettings(freeze_on_pct=0.05, freeze_off_pct=0.1, min_freeze_s=0.0)
    assert manual.freeze_threshold_mode == "manual"  # the default, also for existing projects
    assert AnalysisSettings.from_dict({"freeze_on_pct": 2.0}).freeze_threshold_mode == "manual"
    app = templates.build("open_field", 0, 0, 100, 100)
    assert kinematics(tr, app, manual).freezing.mean() < 0.05  # thresholds far too low: hardly any freezing
    auto = AnalysisSettings(freeze_on_pct=0.05, freeze_off_pct=0.1, min_freeze_s=0.0, freeze_threshold_mode="auto")
    frz = kinematics(tr, app, auto).freezing
    assert frz[300:700].mean() > 0.95 and frz[:300].mean() < 0.1  # the still stretch is found
    assert freezing.thresholds(tr.motion, manual) == (0.05, 0.1)
    assert AnalysisSettings.from_dict(auto.to_dict()).freeze_sensitivity == 50.0


def test_live_freezing_uses_automatic_thresholds():
    s = AnalysisSettings(freeze_threshold_mode="auto", freeze_on_pct=0.01, freeze_off_pct=0.02)
    lt = freezing.LiveThresholds(s, every_s=0.5)
    motion = _bimodal()
    for i, m in enumerate(motion):
        cur = lt.update(i / 25, m)
    assert cur[0] > 0.5  # re-estimated from the motion seen so far
    assert freezing.LiveThresholds(AnalysisSettings()).update(100.0, 1.0) == (2.0, 3.0)


# ====================================================================== live charts / statistics
def _app_with_points():
    app = Apparatus("Box", arena=Polygon([(0, 0), (200, 0), (200, 200), (0, 200)]), px_per_cm=2.0)
    for name, x0 in (("A", 0), ("B", 70), ("C", 140)):
        app.zones.append(Zone(name, Polygon([(x0, 0), (x0 + 60, 0), (x0 + 60, 200), (x0, 200)])))
    app.points.append(PointOfInterest("Object", 170, 100, radius_cm=10))
    app.sequences.append(Sequence("ABC", ["A", "B", "C"]))
    return app


def test_live_charts_cover_the_chart_parameters():
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    params = chart_parameters(app)
    keys = [k for k, _l, _u in params]
    assert len(params) >= 40 and keys[:5] == ["speed", "distance", "motion", "detected", "freezing"]
    names = {p.name for p in parameters(app)}
    assert {k[len(CHART_PREFIX):] for k in keys[5:]} == names - {"Speed", "Distance travelled", "Motion",
                                                                     "Detected", "Freezing"}
    by_name = {p.name: p for p in parameters(app)}
    assert is_cumulative(by_name["Time mobile"]) and not is_cumulative(by_name["X position"])
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0)
    s.set_background(_floor())
    for i in range(80):
        s.process(_frame(40 + i, 100), i / 25)
    lc = LiveCharts()
    t, x = lc.series(s, "X position", window_s=1.0)
    assert len(t) and t[-1] == pytest.approx(s.elapsed) and t[0] >= s.elapsed - 1.0 - 1e-6
    assert x[-1] == pytest.approx((40 + 79) / app.px_per_cm, rel=0.05)
    t, d = lc.series(s, "Time mobile", window_s=1.0)  # a running total: computed over the whole test
    assert np.all(np.diff(d) >= 0)
    t2, d2 = lc.series(s, "Time mobile", window_s=1.0)  # cached
    assert np.array_equal(d, d2)
    assert lc.series(s, "No such parameter")[0].size == 0


def test_live_point_sequence_and_input_statistics():
    app = _app_with_points()
    st_ = LiveStats(app, 25.0, AnalysisSettings())
    xs = list(range(10, 190, 4))  # walks A → B → C
    for i, x in enumerate(xs):
        zones = {z.name: bool(z.shape.contains(x, 100)) for z in app.zones}
        st_.update(i / 25, Detection(x=x, y=100, area=300, motion=1, detected=True), zones, False)
    (name, dist, near, n, lat), = st_.points.rows()
    assert name == "Object" and dist == pytest.approx(abs(xs[-1] - 170) / 2, abs=0.01)
    assert n == 1 and near > 0 and lat == pytest.approx(next(i for i, x in enumerate(xs) if abs(x - 170) <= 20) / 25)
    assert [v[0] for v in st_.visits] == ["A", "B", "C"] and st_.visits[0][2] is not None
    (sname, done, attempts, errors, slat), = sequence_rows(app, st_.visits, len(xs) / 25)
    assert (sname, done, attempts, errors) == ("ABC", 1, 1, 0) and slat == pytest.approx(st_.visits[2][1])
    assert sequence_rows(None, [], 0) == []
    io = [{"t": 1.0, "device": "box", "channel": "Lever", "kind": "input", "value": 1},
          {"t": 1.5, "device": "box", "channel": "Lever", "kind": "input", "value": 0},
          {"t": 3.0, "device": "box", "channel": "Lever", "kind": "input", "value": 1},
          {"t": 2.0, "device": "box", "channel": "Temp", "kind": "input", "value": 21.5, "type": "analog"},
          {"t": 2.0, "device": "box", "channel": "Light", "kind": "output", "value": 1}]
    rows = {r[0]: r for r in input_rows(io, 4.0)}
    assert set(rows) == {"Lever", "Temp"}
    assert rows["Lever"][1:] == ("on", 2, pytest.approx(1.5), 1.0)
    assert rows["Temp"][1] == "21.5"
    # points follow a calibration change
    p = LivePoints(app)
    app2 = app.copy()
    app2.px_per_cm = 1.0
    p.set_apparatus(app2)
    p.update(0.0, 0.04, 150, 100, True)
    assert p.distance["Object"] == pytest.approx(20.0)


# ====================================================================== orientation beam
def test_orientation_beam():
    d = Detection(x=100, y=100, hx=120, hy=100, tx=80, ty=100, area=300, detected=True)
    assert heading_of(d) == pytest.approx(0.0)
    assert heading_of(Detection(x=1, y=1, angle=90.0)) == 90.0 and math.isnan(heading_of(Detection(x=1, y=1)))
    base = np.zeros((200, 200), np.uint8)
    plain = draw_tracking(base, [d])
    lit = draw_tracking(base, [d], beam=True)
    ahead, behind = lit[100, 160], lit[100, 40]
    assert ahead.sum() > 0 and np.array_equal(plain[100, 160], [0, 0, 0])  # lit in front of the head
    assert behind.sum() == 0
    lit2 = draw_tracking(base, [Detection(x=100, y=100, angle=90.0, area=300, detected=True)], beam=True)
    assert lit2[140, 100].sum() > 0 and lit2[60, 100].sum() == 0  # 90° = facing down (y points down)


# ====================================================================== geometry during a test
def test_change_apparatus_geometry_during_live_test(tmp_path):
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0, name="geo")
    s.set_background(_floor())
    for i in range(10):
        s.process(_frame(100, 100), i / 25)
    centre = s.apparatus.zones[1]
    x0 = centre.shape.centroid()[0]
    roi = s.tracker.roi
    geo = s.set_geometry(position={"dx": 20, "dy": 0})
    assert s.tracker.roi[0] == roi[0] + 20  # the tracker's arena follows the map
    assert geo[POSITION_KEY]["dx"] == 20 and s.apparatus.zones[1].shape.centroid()[0] == pytest.approx(x0 + 20)
    assert app.zones[1].shape.centroid()[0] == pytest.approx(x0)  # the project's map is not changed
    assert s.occupancy.app is s.apparatus and "Apparatus geometry adjusted" in s.log[-1][1]
    moved = s.apparatus.zones[1].shape.translated(5, 5).to_dict()
    geo = s.set_geometry(zones={app.zones[1].name: moved})
    assert app.zones[1].name in geo and POSITION_KEY in geo
    # a procedure moving another zone keeps the geometry changed by the user (and vice versa)
    s._zone_cmd("move", {"zone": app.zones[2].name, "x": 60, "y": 60})
    assert s.apparatus.zones[1].shape.centroid()[0] == pytest.approx(x0 + 25)
    assert s.apparatus.zones[2].shape.centroid() == pytest.approx((60, 60))
    for i in range(10, 20):
        s.process(_frame(100, 100), i / 25)
    s.finish()
    with pytest.raises(RuntimeError):
        s.set_geometry(position={"dx": 1})
    p = Project(name="g")
    p.path = tmp_path / "g.mmaze"
    p.apparatus.append(app)
    test = proj.Test(1, "A1", apparatus=app.name)
    p.tests.append(test)
    assert save_live_test(p, test, s)
    assert test.zone_overrides[POSITION_KEY]["dx"] == 20 and app.zones[1].name in test.zone_overrides
    assert "Apparatus geometry adjusted" in test.notes
    snap = s.autosave_snapshot()
    assert snap["geometry"][POSITION_KEY]["dx"] == 20


# ====================================================================== experimenter leaves (videos)
def test_experimenter_leaves_start_in_videos():
    n = 100
    area = np.full(n, 250.0)
    det = np.ones(n, bool)
    area[5:20] = 3000  # the hand puts the animal down …
    area[22:24] = 2800  # … flickers at the edge, and leaves
    det[:5] = False
    tr = Track(np.arange(n) / 25, np.full(n, 50.0), np.full(n, 50.0), area=area, detected=det)
    assert hand_frames(tr).sum() == 17
    t0 = experimenter_leaves_start(tr)
    assert t0 == pytest.approx(24 / 25)
    trimmed, = _trim_all_on_detection([tr], 2.0, t0)
    assert trimmed.t[0] == 0.0 and trimmed.meta["video_start_s"] == pytest.approx(t0)
    no_hand = Track(np.arange(n) / 25, np.full(n, 50.0), np.full(n, 50.0), area=np.full(n, 250.0))
    assert experimenter_leaves_start(no_hand) is None
    assert experimenter_leaves_start(tr, area_px=5000) is None  # an explicit size no object reaches


def test_experimenter_leaves_start_when_tracking_a_video(tmp_path):
    path = tmp_path / "hand.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (200, 200))
    for i in range(120):
        img = _floor()
        if 5 <= i < 30:  # the hand comes in from the edge, puts the mouse down and leaves
            reach = min(i - 5, 8) * 12 if i < 22 else max(0, 30 - i) * 12
            cv2.rectangle(img, (0, 70), (20 + reach, 140), 60, -1)
        if i >= 12:
            syn.draw_mouse(img, 110 + (i - 12) * 0.3, 100, 0)
        writer.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    writer.release()
    proj = Project(start_mode="experimenter_leaves", test_duration_s=2.0)
    proj.save(tmp_path / "s.mmaze")
    proj.apparatus.append(templates.build("open_field", 0, 0, 200, 200))
    t = proj.add_test(str(path), "A")
    proj.detection.background = "frame"
    [tr] = proj.track_test(t)
    assert tr.meta["start"] == "experimenter left" and tr.t[0] == 0
    assert 29 / 25 <= tr.meta["video_start_s"] <= 33 / 25  # not at the first detection (the hand, frame 5)
    assert tr.duration == pytest.approx(2.0, abs=0.05)


# ====================================================================== capture recovery
class _FlakyCamera:
    """A camera that delivers `good` frames, then stops (reads fail) until it is reopened."""

    opened = 0

    def __init__(self, source, w=None, h=None, fps=None, good=5, broken_opens=()):
        type(self).opened += 1
        if type(self).opened in broken_opens:
            raise IOError("device busy")
        self.is_camera = True
        self.fps, self.width, self.height, self.frame_count = 25.0, 32, 24, 0
        self.n = good

    def read(self):
        if self.n <= 0:
            return False, None
        self.n -= 1
        return True, np.zeros((24, 32, 3), np.uint8)

    def seek(self, i):
        pass

    def release(self):
        pass


class _Reader(SourceReader):
    reconnect_delays = (0.01, 0.02)
    stall_reads = 3

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.events = []
        self.frames = 0
        self.failed = threading.Event()

    def on_frame(self, frame, ts):
        self.frames += 1

    def on_capture_lost(self, msg):
        self.events.append(("lost", msg))

    def on_capture_restored(self, gap_s):
        self.events.append(("restored", gap_s))

    def on_failed(self, msg):
        self.events.append(("failed", msg))
        self.failed.set()


def _wait(cond, timeout=5.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        time.sleep(0.01)
    return cond()


def test_camera_drop_out_is_recovered():
    _FlakyCamera.opened = 0
    opener = lambda *a: _FlakyCamera(*a, broken_opens=(2,))  # the first reopening fails, the next works
    r = _Reader(SourceSpec(0), opener=opener)
    r.start()
    assert _wait(lambda: r.frames >= 8)
    r.stop()
    kinds = [e[0] for e in r.events]
    assert kinds[:2] == ["lost", "restored"] and "failed" not in kinds
    log = r.capture_log[0]
    assert log["gap_s"] is not None and log["attempts"] >= 2 and "device busy" in log["reason"]


def test_camera_that_never_comes_back_fails():
    _FlakyCamera.opened = 0
    r = _Reader(SourceSpec(0), opener=lambda *a: _FlakyCamera(*a, broken_opens=range(2, 1000)))
    r.reconnect_timeout_s = 0.3
    r.start()
    assert r.failed.wait(5)
    assert [e[0] for e in r.events] == ["lost", "failed"] and "reopening it failed" in r.events[-1][1]


def test_capture_gap_is_marked_in_the_live_track():
    app = templates.build("open_field", 10, 10, 180, 180)
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0)
    s.set_background(_floor())
    for i in range(10):
        s.process(_frame(100, 100), i / 25)
    s.capture_lost("the camera stopped delivering frames")
    s.capture_lost("again")  # one gap at a time
    s.capture_restored(2.0)
    s.process(_frame(100, 100), 9 / 25 + 2.0)
    assert s.capture_gaps == [[pytest.approx(0.36), pytest.approx(2.36)]]
    tr = s.track()
    assert tr.meta["capture_gaps"] == s.capture_gaps
    i = int(np.searchsorted(tr.t, 0.39))
    assert tr.t[i] == pytest.approx(0.40) and not s.cols["detected"][i]  # an undetected row opens the gap
    assert not any("dropped" in w for _t, w in s.warnings)  # reported as a capture gap, not dropped frames
    assert any("Video capture lost" in w for _t, w in s.warnings)
    assert any("restored" in w for _t, w in s.warnings)


# ====================================================================== zone map image
def test_zone_map_image(tmp_path):
    app = _app_with_points()
    app.zones[0].hidden = True
    png = plots.save_zone_map(app, tmp_path / "map")
    assert png.endswith(".png")
    img = cv2.imread(png)
    assert img is not None and img.shape[0] > 100 and img.shape[1] > 100
    svg = plots.save_zone_map(app, tmp_path / "map.svg", labels=True)
    text = open(svg, encoding="utf-8").read()
    assert "<svg" in text and "ABC" not in text  # zones, points … (sequences are not drawn)
    bg = np.full((200, 200, 3), 90, np.uint8)
    over = cv2.imread(plots.save_zone_map(app, tmp_path / "bg.png", background=bg, dpi=50))
    assert over is not None
    pdf = plots.save_zone_map(app, tmp_path / "map.pdf")
    assert open(pdf, "rb").read(4) == b"%PDF"


# ====================================================================== disk space
def test_disk_space_check(tmp_path, monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    free = {"v": 50 * diskspace.GB}
    monkeypatch.setattr(diskspace.shutil, "disk_usage", lambda p: usage(100, 50, free["v"]))
    assert diskspace.free_bytes(tmp_path / "not" / "yet" / "there") == 50 * diskspace.GB  # nearest parent
    c = diskspace.check(tmp_path)
    assert c.ok and c.level == "ok" and c.message == ""
    free["v"] = diskspace.GB
    c = diskspace.check(tmp_path)
    assert c.level == "low" and not c.ok and "disk space is low" in c.message and "1.0 GB" in c.message
    free["v"] = 50 * diskspace.MB
    assert diskspace.check(tmp_path).level == "critical" and "full" in diskspace.check(tmp_path).message
    free["v"] = 10 * diskspace.GB
    c = diskspace.check(tmp_path, needed=20 * diskspace.GB)
    assert c.level == "critical" and "recording may need" in c.message
    assert diskspace.check(tmp_path, needed=9 * diskspace.GB).level == "low"
    assert diskspace.recording_bytes(640, 480, 25, 0) == 0
    assert 1e8 < diskspace.recording_bytes(1280, 720, 25, 3600) < 1e10
    assert diskspace.check(None).level == "unknown" and diskspace.format_bytes(1536) == "1.5 KB"


# ====================================================================== statistics catalogue
def test_more_than_30_statistical_tests():
    tests = [n for n, cat in st.TESTS.items() if cat != "post-hoc"]
    posthoc = [n for n, cat in st.TESTS.items() if cat == "post-hoc"]
    assert len(tests) > 30 and len(set(tests)) == len(tests)  # ANY-maze: "more than 30"
    assert len(st.TESTS) == len(tests) + len(posthoc) and len(posthoc) >= 12
    # every procedure of the catalogue is reachable through the statistics functions
    rng = np.random.default_rng(1)
    g = {"A": rng.normal(0, 1, 12), "B": rng.normal(1, 1, 12), "C": rng.normal(0.5, 1, 12)}
    assert set(st.normality(g["A"])) == {"Shapiro-Wilk", "D'Agostino-Pearson"}
    assert set(st.variance_tests(g)) == {"Levene", "Brown-Forsythe", "Bartlett", "Fligner-Killeen"}
    assert st.chi_square_gof([10, 20, 30])["p"] < 0.05
    rows = [{"Group": grp, "Strategy": strat} for grp, strat in
            [("A", "direct")] * 8 + [("A", "random")] * 2 + [("B", "direct")] * 3 + [("B", "random")] * 7]
    a = categorical(None, rows, "Group", "Strategy")
    assert "goodness_of_fit" in a.result and "Goodness of fit" in a.headline and "goodness of fit" in a.summary_text and "fisher" in a.result
