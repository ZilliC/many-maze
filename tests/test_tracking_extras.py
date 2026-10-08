"""Tracking-derived measures: rearing, whole-body outline, activity, freezing score, head distance and turning,
tracking quality."""

import io
import json
import math

import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.apparatus import Apparatus, Zone
from manymaze.core.geometry import rect
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented, rearing_mask
from manymaze.core.track import Track, simplify_outline, swap_identities
from manymaze.core.tracking import ArenaJob, Detection, DetectionSettings, TrackBuilder, draw_tracking, track_video

FPS = 25.0


def _app():
    return Apparatus(arena=rect(0, 0, 400, 400), px_per_cm=1.0,
                     zones=[Zone("Left", rect(0, 0, 200, 400)), Zone("Right", rect(200, 0, 200, 400))])


def _track(n, x=None, area=None, length=None, motion=None, angle=None, head=True, detected=None):
    """A synthetic track: the body axis along `angle` (deg) with head–tail `length` around the centre."""
    t = np.arange(n) / FPS
    x = np.full(n, 100.0) if x is None else np.asarray(x, float)
    y = np.full(n, 200.0)
    a = np.zeros(n) if angle is None else np.asarray(angle, float)
    L = np.full(n, 40.0) if length is None else np.asarray(length, float)
    kw = dict(area=np.full(n, 800.0) if area is None else area, motion=motion, angle=a, detected=detected)
    if head:
        ux, uy = np.cos(np.radians(a)), np.sin(np.radians(a))
        kw.update(hx=x + ux * L / 2, hy=y + uy * L / 2, tx=x - ux * L / 2, ty=y - uy * L / 2)
    return Track(t=t, x=x, y=y, fps=FPS, **kw)


# ---------------------------------------------------------------- rearing
def _rearing_track(n=500, rears=((100, 150), (300, 330))):
    area = np.full(n, 800.0)
    L = np.full(n, 40.0)
    x = np.full(n, 100.0)
    x[250:] = 300.0  # the second rear is in the right half
    for a, b in rears:
        area[a:b], L[a:b] = 420.0, 22.0
    return _track(n, x=x, area=area, length=L)


def test_rearing_whole_test_and_per_zone():
    tr = _rearing_track()
    r = analyse(tr, _app(), AnalysisSettings(rearing=True))
    assert r["Rears"] == 2
    assert r["Time rearing (s)"] == pytest.approx(80 / FPS)
    assert r["Latency to first rear (s)"] == pytest.approx(100 / FPS)
    assert r["Mean rear duration (s)"] == pytest.approx(40 / FPS)
    assert r["Max rear duration (s)"] == pytest.approx(50 / FPS)
    assert r["Min rear duration (s)"] == pytest.approx(30 / FPS)
    assert r["Left: rears"] == 1 and r["Right: rears"] == 1
    assert r["Right: time rearing (s)"] == pytest.approx(30 / FPS)
    assert r["Right: latency to first rear (s)"] == pytest.approx(300 / FPS)
    assert r["Left: max rear duration (s)"] == pytest.approx(50 / FPS)
    # off by default: no rearing columns
    assert not any("rear" in k.lower() for k in analyse(tr, _app()))


def test_rearing_needs_both_shrinks_and_respects_thresholds():
    n = 300
    area = np.full(n, 800.0)
    area[100:150] = 420.0  # smaller but just as long (e.g. curled up sideways): not a rear
    tr = _track(n, area=area)
    assert not rearing_mask(tr, AnalysisSettings(), tr.t, tr.frame_durations()).any()
    # without head / tail only the area is used
    tr2 = _track(n, area=area, head=False)
    m = rearing_mask(tr2, AnalysisSettings(), tr2.t, tr2.frame_durations())
    assert m[100:150].all() and m.sum() == 50
    # a stricter area threshold misses it; short rears are dropped
    assert not rearing_mask(tr2, AnalysisSettings(rear_area_pct=40), tr2.t, tr2.frame_durations()).any()
    assert not rearing_mask(tr2, AnalysisSettings(min_rear_s=3), tr2.t, tr2.frame_durations()).any()


def test_rearing_bridges_short_dropouts_and_periods():
    tr = _rearing_track(rears=((100, 150),))  # 4.0–6.0 s
    tr.detected[120:123] = False  # a 0.12 s detection dropout inside the rear
    s = AnalysisSettings(rearing=True, latency_if_never="blank",
                         custom_periods=[["early", 0, 4.4], ["late", 5, 8], ["none", 12, 16]])
    res = dict(analyse_segmented(tr, _app(), s))
    whole = res["Whole test"]
    assert whole["Rears"] == 1
    assert whole["Time rearing (s)"] == pytest.approx(2.0)  # the short dropout is bridged
    assert whole["Max rear duration (s)"] == pytest.approx(2.0)
    # periods: a rear counts where it starts; time rearing counts the frames inside the period
    assert res["early"]["Rears"] == 1 and res["early"]["Latency to first rear (s)"] == pytest.approx(4.0)
    assert res["early"]["Time rearing (s)"] == pytest.approx(0.4)
    assert res["late"]["Rears"] == 0 and math.isnan(res["late"]["Latency to first rear (s)"])
    assert res["late"]["Time rearing (s)"] == pytest.approx(1.0)
    assert res["none"]["Rears"] == 0 and res["none"]["Time rearing (s)"] == 0


def test_rearing_detected_in_tracked_video(tmp_path):
    """End to end: a synthetic mouse that rears (looks half as long from above) twice, tracked from video."""
    n = 400
    pos = syn.random_walk(n, (50, 50, 350, 350), speed=2.5, seed=3, pause_prob=0)
    lengths = np.full(n, 36.0)
    for a, b in ((100, 150), (250, 280)):
        lengths[a:b] = 18
        pos[a:b] = pos[a]
        pos[b:] += pos[a] - pos[b]
    vp = tmp_path / "rear.avi"
    syn.make_video(vp, np.clip(pos, 70, 330), fps=FPS, arena=("rect", 50, 50, 300, 300), lengths=lengths)
    app = templates.build("open_field", 50, 50, 300, 300, size_cm=40)
    tr = track_video(str(vp), [ArenaJob(app, DetectionSettings())])[0][0]
    r = analyse(tr, app, AnalysisSettings(rearing=True))
    assert r["Rears"] == 2
    assert r["Time rearing (s)"] == pytest.approx(80 / FPS, abs=0.2)
    assert r["Latency to first rear (s)"] == pytest.approx(4.0, abs=0.1)
    # the whole-body outline was recorded in every frame, compactly, and survives the track file
    assert tr.has_outline() and all(o is not None and 3 <= len(o) <= 24 for o in tr.outline)
    buf = io.StringIO()
    tr.to_csv(buf)
    back = Track.from_csv(io.StringIO(buf.getvalue()))
    assert all(np.array_equal(a, b) for a, b in zip(tr.outline, back.outline))
    # off: nothing stored
    tr_off = track_video(str(vp), [ArenaJob(app, DetectionSettings(record_outline=False, duration_s=1))])[0][0]
    assert tr_off.outline is None and not tr_off.has_outline()


# ---------------------------------------------------------------- outline storage
def test_outline_csv_round_trip_and_backwards_compatibility():
    tr = _track(5)
    tr.outline = np.empty(5, object)
    tr.outline[1] = np.array([[1, 2], [3, 4], [5, 7]])
    tr.outline[3] = np.array([[10, 20], [30, 40], [50, 70], [11, 12]])
    buf = io.StringIO()
    tr.to_csv(buf)
    text = buf.getvalue()
    assert "outline" in text.splitlines()[1]
    back = Track.from_csv(io.StringIO(text))
    assert back.outline[0] is None and back.outline[2] is None
    assert back.outline[1].tolist() == [[1, 2], [3, 4], [5, 7]] and back.outline[3].shape == (4, 2)
    np.testing.assert_allclose(back.x, tr.x)
    # an old track file (no outline column) still loads, without outlines, and is written back without one
    old = io.StringIO()
    _track(5).to_csv(old)
    assert "outline" not in old.getvalue()
    tr_old = Track.from_csv(io.StringIO(old.getvalue()))
    assert tr_old.outline is None and not tr_old.has_outline()
    # slicing, copies and interpolation keep the outline frame for frame
    assert back.slice_index(1, 4).outline[0].tolist() == [[1, 2], [3, 4], [5, 7]]
    assert back.take(np.array([False, False, False, True, True])).outline[0].shape == (4, 2)
    assert back.interpolate().outline[3].shape == (4, 2)


def test_outline_swap_identities_and_builder():
    a, b = _track(4), _track(4)
    a.outline = [None, [[0, 0], [1, 0], [1, 1]], None, None]
    a.__post_init__()
    assert a.outline.dtype == object and a.outline[1].dtype == np.int32
    swap_identities(a, b, t0=0.0)
    assert a.outline[1] is None and b.outline[1].shape == (3, 2)
    # the builder stores the detection's outline; a crash-recovery file from an older version has none
    bl = TrackBuilder()
    d = Detection(x=1, y=2, detected=True, outline=np.array([[0, 0], [4, 0], [4, 4]], np.int32))
    bl.add(0.0, d)
    bl.add(0.04, Detection())
    cols = json.loads(json.dumps(bl.snapshot(), default=lambda o: o.tolist()))
    tr = TrackBuilder(cols).build(FPS)
    assert tr.outline[0].tolist() == [[0, 0], [4, 0], [4, 4]] and tr.outline[1] is None
    del cols["outline"]
    old = TrackBuilder(cols)
    old.add(0.08, d)
    tr_old = old.build(FPS)
    assert len(tr_old) == 3 and tr_old.outline[0] is None and tr_old.outline[2].shape == (3, 2)
    assert TrackBuilder().build(FPS).outline is None


def test_simplify_outline_is_compact():
    import cv2

    m = np.zeros((200, 200), np.uint8)
    cv2.ellipse(m, (100, 100), (60, 25), 30, 0, 360, 255, -1)
    c = max(cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], key=cv2.contourArea)
    poly = simplify_outline(c)
    assert 6 <= len(poly) <= 24
    assert cv2.contourArea(poly.reshape(-1, 1, 2)) == pytest.approx(cv2.contourArea(c), rel=0.08)
    assert len(simplify_outline(c, max_points=5)) <= 5
    # live images draw the outline
    img = draw_tracking(np.zeros((200, 200), np.uint8), [Detection(x=100, y=100, detected=True, outline=poly)])
    assert img[poly[0][1], poly[0][0]].any()


# ---------------------------------------------------------------- activity and freezing score
def test_activity_episodes_and_freezing_score():
    n = 250  # 10 s
    motion = np.full(n, 400.0)  # 50 % of the 800 px² body: active
    motion[50:100] = 8.0  # 1 % for 2 s: inactive
    motion[150:155] = 8.0  # 0.2 s: too short, still active
    motion[200:250] = 0.0  # last 2 s inactive
    tr = _track(n, motion=motion)
    r = analyse(tr, _app(), AnalysisSettings())
    assert r["Time inactive (s)"] == pytest.approx(4.0)
    assert r["Time active (s)"] == pytest.approx(6.0)
    assert r["Inactive episodes"] == 2 and r["Active episodes"] == 2
    assert r["Longest active episode (s)"] == pytest.approx(4.0)
    assert r["Shortest active episode (s)"] == pytest.approx(2.0)
    assert r["Longest inactive episode (s)"] == r["Shortest inactive episode (s)"] == pytest.approx(2.0)
    expected = (motion / 800 * 100).mean()
    assert r["Average freezing score (% body)"] == pytest.approx(expected, abs=0.01)
    # the short dip counts as inactive with a shorter minimum; a higher threshold makes everything inactive
    r2 = analyse(tr, _app(), AnalysisSettings(min_inactive_s=0.1))
    assert r2["Inactive episodes"] == 3
    r3 = analyse(tr, _app(), AnalysisSettings(activity_threshold_pct=60))
    assert r3["Time active (s)"] == 0 and r3["Inactive episodes"] == 1
    # no motion signal (e.g. an imported track): no activity measures
    assert "Time active (s)" not in analyse(_track(n), _app())


def test_activity_respects_pauses():
    n = 250
    motion = np.full(n, 400.0)
    motion[100:150] = 0.0
    tr = _track(n, motion=motion)
    r = analyse(tr, _app(), AnalysisSettings(), pauses=[[4.0, 6.0]])  # the inactive stretch is paused
    assert r["Test duration (s)"] == pytest.approx(8.0)
    assert r["Time inactive (s)"] == pytest.approx(0.0) and r["Inactive episodes"] == 0


# ---------------------------------------------------------------- head distance and turning
def test_head_distance_and_turn_angle():
    n = 200
    x = 100 + np.arange(n) * 1.0  # 25 px/s
    angle = np.zeros(n)
    angle[50:140] = np.arange(1, 91)  # turn 90° clockwise (y down) …
    angle[140:] = 90 - np.minimum(np.arange(1, 61), 30)  # … then 30° anticlockwise
    angle[180] += 180  # a one-frame head / tail swap is not a turn
    tr = _track(n, x=x, angle=angle)
    s = AnalysisSettings(speed_smoothing_s=0)
    r = analyse(tr, _app(), s)
    assert r["Head turn angle clockwise (deg)"] == pytest.approx(90, abs=0.5)
    assert r["Head turn angle anticlockwise (deg)"] == pytest.approx(30, abs=0.5)
    assert r["Head turn angle (deg)"] == pytest.approx(120, abs=1)
    # the head moves with the centre plus its swing around it
    assert r["Head distance (cm)"] > r["Total distance (cm)"] - 1e-6
    straight = _track(n, x=x)
    rs = analyse(straight, _app(), s)
    assert rs["Head distance (cm)"] == pytest.approx(n - 1, rel=1e-6)
    assert rs["Head turn angle (deg)"] == 0
    # no head distance across a pause; periods split it
    rp = analyse(straight, _app(), s, pauses=[[2.0, 4.0]])
    assert rp["Head distance (cm)"] == pytest.approx(n - 1 - 50, abs=1.01)
    seg = dict(analyse_segmented(straight, _app(), AnalysisSettings(speed_smoothing_s=0, bin_length_s=4.0)))
    assert seg["0-4 s"]["Head distance (cm)"] + seg["4-8 s"]["Head distance (cm)"] == pytest.approx(n - 1)
    # no head tracked: no head measures
    assert "Head distance (cm)" not in analyse(_track(n, x=x, head=False), _app(), s)


# ---------------------------------------------------------------- tracking quality
def test_tracking_quality_and_positions_recorded():
    n = 100
    area = np.full(n, 800.0)
    area[10:15] = 3000.0  # merged with a shadow
    det = np.ones(n, bool)
    det[20:30] = False  # lost (interpolated)
    tr = _track(n, area=area, detected=det)
    tr.hx[40:50] = np.nan  # head not found
    r = analyse(tr, _app())
    assert r["Centre positions recorded"] == 90
    assert r["Head positions recorded"] == 80
    assert r["Head tracked (% of tracked frames)"] == pytest.approx(100 * 80 / 90, abs=0.1)
    assert r["Tracking quality (%)"] == pytest.approx(75.0)  # 100 - 5 - 10 - 10
    r2 = analyse(_track(n, head=False), _app())
    assert r2["Head positions recorded"] == 0 and "Head tracked (% of tracked frames)" not in r2
    assert r2["Tracking quality (%)"] == 100.0
