"""Regression tests for analysis defects found in review (pauses, per-period states, entry rules, charts, ...)."""

import math

import numpy as np
import pytest

from manymaze.core import charts, templates
from manymaze.core.apparatus import Apparatus, Zone
from manymaze.core.geometry import circle, rect
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented
from manymaze.core.project import Behaviour
from manymaze.core.track import Track

FPS = 25.0


def _app():
    app = Apparatus(arena=rect(0, 0, 400, 400), px_per_cm=10.0)
    app.zones.append(Zone("Left", rect(0, 0, 200, 400)))
    return app


def _track(x, y=None, fps=FPS, **kw):
    x = np.asarray(x, float)
    t = np.arange(len(x)) / fps
    y = np.full(len(x), 200.0) if y is None else np.asarray(y, float)
    return Track(t=t, x=x, y=y, fps=fps, **kw)


# ---------------------------------------------------------------- 1. zero-length (live) pause markers
def test_pause_marker_breaks_distance_and_speed():
    t = np.arange(250) / FPS
    tr = _track(np.where(t < 5, 100.0, 300.0))
    r = analyse(tr, _app(), AnalysisSettings(), pauses=[[5.0, 5.0]])
    assert r["Total distance (cm)"] == pytest.approx(0.0, abs=1e-6)
    assert r["Max speed (cm/s)"] == pytest.approx(0.0, abs=1e-6)
    assert r["Time mobile (s)"] == pytest.approx(0.0, abs=1e-6)
    assert r["Test duration (s)"] == pytest.approx(10.0)
    # without the marker the jump is (wrongly, for a stopped clock) movement
    assert analyse(tr, _app(), AnalysisSettings())["Total distance (cm)"] > 10


def test_pause_marker_suppresses_turns_and_entries():
    t = np.arange(250) / FPS
    # moving right at y=100 before the pause, moving right at y=300 after: the jump is not a turn
    x = 220 + 6 * (t % 5) * FPS / 5
    y = np.where(t < 5, 100.0, 300.0)
    app = _app()
    from manymaze.core.apparatus import Line
    app.lines.append(Line("Mid", 0, 200, 400, 200))
    r = analyse(_track(x, y), app, AnalysisSettings(speed_smoothing_s=0), pauses=[[5.0, 5.0]])
    assert r["Absolute turn angle (deg)"] == pytest.approx(0.0, abs=1e-6)
    assert r["Mid: crossings"] == 0
    # animal outside "Left" before the pause, inside after: time counts, but no entry across the pause
    x2 = np.where(t < 5, 300.0, 100.0)
    r2 = analyse(_track(x2), _app(), AnalysisSettings(), pauses=[[5.0, 5.0]])
    assert r2["Left: time (s)"] == pytest.approx(5.0)
    assert r2["Left: entries"] == 0


# ---------------------------------------------------------------- 2. per-period states from the whole track
def _same(a, b):
    assert list(a) == list(b)
    for key in a:
        va, vb = a[key], b[key]
        if isinstance(va, float) and isinstance(vb, float) and math.isnan(va) and math.isnan(vb):
            continue
        assert va == vb, key


def _freeze_track():
    n = 500
    t = np.arange(n) / FPS
    motion = np.full(n, 50.0)
    motion[(t >= 4.3) & (t < 5.8)] = 0.0
    return _track(np.full(n, 100.0), np.full(n, 100.0), area=np.full(n, 100.0), motion=motion)


def test_freeze_straddling_bin_edge_is_kept():
    tr = _freeze_track()
    seg = dict(analyse_segmented(tr, _app(), AnalysisSettings(bin_length_s=5.0)))
    whole = seg["Whole test"]
    assert whole["Time freezing (s)"] == pytest.approx(1.48)
    a, b = seg["0-5 s"], seg["5-10 s"]
    assert a["Time freezing (s)"] + b["Time freezing (s)"] == pytest.approx(whole["Time freezing (s)"])
    assert a["Time freezing (s)"] == pytest.approx(0.68)
    assert a["Freezing episodes"] == 1 and b["Freezing episodes"] == 0  # episodes counted where they start
    assert a["Latency to first freezing (s)"] == pytest.approx(4.32)
    assert b["Latency to first freezing (s)"] == pytest.approx(5.0)  # none starts in the bin: bin length
    # analyse(t_range=...) agrees with the segmented path
    _same(analyse(tr, _app(), AnalysisSettings(bin_length_s=5.0), t_range=(5.0, 10.0)), b)


def test_min_durations_not_restarted_at_bin_edges():
    n = 500
    t = np.arange(n) / FPS
    # inside "Left" only between 4 s and 6 s; minimum visit 1.5 s (whole visit 2 s qualifies)
    x = np.where((t >= 4) & (t < 6), 100.0, 300.0)
    s = AnalysisSettings(bin_length_s=5.0, entry_min_duration_s=1.5)
    seg = dict(analyse_segmented(_track(x), _app(), s))
    assert seg["Whole test"]["Left: time (s)"] == pytest.approx(2.0)
    assert seg["0-5 s"]["Left: time (s)"] == pytest.approx(1.0)
    assert seg["5-10 s"]["Left: time (s)"] == pytest.approx(1.0)
    assert seg["0-5 s"]["Left: entries"] == 1 and seg["5-10 s"]["Left: entries"] == 0
    # 3 s immobile episode across the edge with min_immobile_s = 2: still immobile in both bins
    xs = np.where(t < 3.5, 100 + 40 * t, np.where(t < 6.5, 240.0, 240 + 40 * (t - 6.5)))
    seg2 = dict(analyse_segmented(_track(xs), _app(), AnalysisSettings(bin_length_s=5.0, speed_smoothing_s=0)))
    imm = seg2["Whole test"]["Time immobile (s)"]
    assert imm > 2.5
    assert seg2["0-5 s"]["Time immobile (s)"] + seg2["5-10 s"]["Time immobile (s)"] == pytest.approx(imm, abs=0.05)
    assert seg2["0-5 s"]["Time immobile (s)"] > 1.0 and seg2["5-10 s"]["Time immobile (s)"] > 1.0


# ---------------------------------------------------------------- 3. count_initial_entry=False keeps the time
def test_initial_visit_time_counts_without_entry():
    t = np.arange(250) / FPS
    tr = _track(np.where(t < 3, 100.0, 300.0))
    on = analyse(tr, _app(), AnalysisSettings(count_initial_entry=True))
    off = analyse(tr, _app(), AnalysisSettings(count_initial_entry=False))
    assert on["Left: time (s)"] == off["Left: time (s)"] == pytest.approx(3.0)
    assert off["Left: distance (cm)"] == on["Left: distance (cm)"]
    assert on["Left: entries"] == 1 and off["Left: entries"] == 0
    assert off["Left: latency to first entry (s)"] == pytest.approx(10.0)


# ---------------------------------------------------------------- 4. multi-animal trim on detection
def test_trim_on_detection_aligns_animals():
    from manymaze.core.project import _trim_all_on_detection

    n = 100
    t = np.arange(n) / 10.0
    d1 = t >= 1.0
    d2 = t >= 2.0
    a = Track(t=t, x=np.where(d1, t, np.nan), y=np.zeros(n), detected=d1, fps=10)
    b = Track(t=t, x=np.where(d2, t, np.nan), y=np.zeros(n), detected=d2, fps=10)
    ta, tb = _trim_all_on_detection([a, b], 5.0)
    assert len(ta) == len(tb) == 50
    np.testing.assert_allclose(ta.t, tb.t)
    assert ta.t[0] == 0.0 and ta.meta["video_start_s"] == pytest.approx(1.0)
    assert tb.meta["video_start_s"] == pytest.approx(1.0)


# ---------------------------------------------------------------- 5. charts follow the analysis occupancy
def test_charts_use_entry_rules():
    app = Apparatus(arena=rect(0, 0, 400, 400), px_per_cm=10.0)
    app.zones.append(Zone("Obj", circle(200, 200, 20), investigation_distance_cm=3.0))
    n = 100
    t = np.arange(n) / FPS
    # centre at 245 (outside the object), head at 235 (within 3 cm = 30 px of its edge at 220)
    tr = Track(t=t, x=np.full(n, 245.0), y=np.full(n, 200.0), hx=np.full(n, 235.0), hy=np.full(n, 200.0),
               tx=np.full(n, 255.0), ty=np.full(n, 200.0), angle=np.full(n, 180.0), fps=FPS)
    s = AnalysisSettings()
    assert analyse(tr, app, s)["Obj: time (s)"] == pytest.approx(4.0)
    ser = charts.compute(tr, app, ["Obj: in zone"], s)
    assert ser["Obj: in zone"].all()
    bands = charts.zone_bands(tr, app, ["Obj"], s)
    assert bands[0][2] and bands[0][2][0][0] == 0.0


# ---------------------------------------------------------------- 6. interval pauses: one test-time convention
def test_interval_pause_uses_test_time():
    app = _app()
    t = np.arange(250) / FPS  # 10 s of video, paused 2-4 s => 8 s of test
    x = np.where(t < 6, 300.0, 100.0)  # enters "Left" at 6 s video time = 4 s test time
    tr = _track(x)
    beh = [Behaviour("Rear", "r", "point"), Behaviour("Groom", "g", "state")]
    ev = [{"behaviour": "Rear", "t": 9.0, "t_end": None}, {"behaviour": "Groom", "t": 8.0, "t_end": 9.5}]
    io = [{"t": 9.0, "device": "v", "channel": "lever", "kind": "input", "value": 1},
          {"t": 9.2, "device": "v", "channel": "lever", "kind": "input", "value": 0}]
    other = _track(x)
    r = analyse(tr, app, AnalysisSettings(), events=ev, behaviours=beh, io_events=io, pauses=[[2.0, 4.0]],
                other_tracks=[other])
    assert r["Test duration (s)"] == pytest.approx(8.0)
    assert r["Left: latency to first entry (s)"] == pytest.approx(4.0)
    assert r["Rear: count"] == 1 and r["Rear: latency (s)"] == pytest.approx(7.0)
    assert r["Groom: duration (s)"] == pytest.approx(1.5) and r["Groom: latency (s)"] == pytest.approx(6.0)
    assert r["lever: activations"] == 1
    assert r["Animal 1: mean distance (cm)"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------- 7. water maze follows a moved platform
def test_water_maze_target_follows_moved_platform():
    app = templates.build("water_maze", 0, 0, 400, 400)
    plat = app.zone("Platform")
    px, py = plat.shape.centroid()
    cx, cy = app.arena.centroid()
    moved = circle(2 * cx - px, 2 * cy - py, 12).to_dict()  # mirror NE -> SW
    sw = app.zone("Quadrant SW").shape.centroid()
    n = 250
    tr = _track(np.full(n, sw[0]), np.full(n, sw[1]))
    r = analyse(tr, app, AnalysisSettings(), zone_overrides={"Platform": moved})
    assert r["Target quadrant: time (%)"] == pytest.approx(100.0)
    assert r["Opposite quadrant: time (%)"] == pytest.approx(0.0)
    assert r["Target quadrant time (%)"] == pytest.approx(100.0)
    assert "Platform position NE: entries" in r and "Platform position SW: entries" not in r
    al = templates.align_water_maze(app.with_overrides({"Platform": moved}))
    assert al.group("Target quadrant").zones == ["Quadrant SW"]
    assert al.group("Opposite quadrant").zones == ["Quadrant NE"]
    # unmoved apparatus is untouched
    assert templates.align_water_maze(app) is app


# ---------------------------------------------------------------- low priority
def test_behaviour_latency_honours_latency_if_never():
    tr = _track(np.full(100, 100.0))
    beh = [Behaviour("Rear", "r", "point"), Behaviour("Groom", "g", "state")]
    r = analyse(tr, _app(), AnalysisSettings(latency_if_never="blank"), events=[], behaviours=beh)
    assert math.isnan(r["Rear: latency (s)"]) and math.isnan(r["Groom: latency (s)"])
    r2 = analyse(tr, _app(), AnalysisSettings(), events=[], behaviours=beh)
    assert r2["Rear: latency (s)"] == pytest.approx(4.0)


def test_uncalibrated_warning():
    app = Apparatus(arena=rect(0, 0, 400, 400))
    r = analyse(_track(np.full(50, 100.0)), app, AnalysisSettings())
    assert "pixels" in r["Warnings"]
    assert "Warnings" not in analyse(_track(np.full(50, 100.0)), _app(), AnalysisSettings())


def test_tracks_shorter_than_the_smoothing_window():
    from manymaze.core.series import moving_average

    assert moving_average(np.array([1.0, 2.0, 6.0]), 5).tolist() == [1.5, 3.0, 4.0]
    assert moving_average(np.array([1.0, 3.0]), 5).tolist() == [1.0, 3.0]
    r = analyse(_track([10.0, 20.0, 30.0]), _app(), AnalysisSettings())
    assert r["Total distance (cm)"] > 0
    assert _track(np.arange(10.0)).smooth(25).x.shape == (10,)


def test_water_maze_periods_use_the_moved_platform():
    """Event periods are computed on the apparatus as analysed: the target quadrant follows a moved platform."""
    app = templates.build("water_maze", 0, 0, 400, 400)
    ov = {"Platform": {"type": "ellipse", "cx": 100, "cy": 300, "rx": 8, "ry": 8}}  # moved to SW
    tr = _track(np.r_[np.full(50, 300.0), np.full(50, 100.0)], np.r_[np.full(50, 100.0), np.full(50, 300.0)])
    s = AnalysisSettings(event_periods=[{"label": "Target", "anchor": "first_entry", "zone": "Target quadrant",
                                         "duration_s": 0, "occurrence": 1}])
    from manymaze.core.measures import all_periods

    assert all_periods(tr, app, s, None, zone_overrides=ov) == [("Target", 2.0, 4.0)]
    assert templates.apply_overrides(app, ov).group("Target quadrant").zones == ["Quadrant SW"]
    assert templates.apply_overrides(app, None) is app


def test_kruskal_effect_size_label():
    from manymaze.core.stats import compare_groups

    rng = np.random.default_rng(0)
    out = compare_groups({"a": rng.normal(0, 1, 10), "b": rng.normal(1, 1, 10), "c": rng.normal(2, 1, 10)},
                         parametric=False)
    assert out["effect_size_name"] != "epsilon²"


def test_crop_box_padding_keeps_aspect():
    pytest.importorskip("cv2")
    from manymaze.core.pose import crop_box

    frame = np.zeros((400, 400), np.uint8)
    crop, off, scale = crop_box(frame, (100, 180, 300, 220), (128, 64), context=False)
    assert crop.shape == (64, 128)
    assert scale[0] == pytest.approx(scale[1], rel=0.03)
    crop, off, scale = crop_box(frame, (180, 100, 220, 300), (128, 64), context=False)
    assert scale[0] == pytest.approx(scale[1], rel=0.03)
