import math

import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.apparatus import Apparatus, Line, PointOfInterest, Zone, ZoneGroup
from manymaze.core.geometry import Ellipse, Polygon, circle, rect, segments_intersect, shape_from_dict
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented, count_rotations, runs
from manymaze.core.procedures import ProcedureEngine
from manymaze.core.stats import compare_groups, two_way_anova
from manymaze.core.track import Track


def make_track(points, fps=25.0, **kw):
    p = np.asarray(points, float)
    t = np.arange(len(p)) / fps
    return Track(t=t, x=p[:, 0], y=p[:, 1], fps=fps, **kw)


# ---------------------------------------------------------------- geometry
def test_polygon_and_ellipse_contains():
    r = rect(0, 0, 10, 10)
    assert r.contains(5, 5) and not r.contains(11, 5)
    assert r.area() == pytest.approx(100)
    e = circle(0, 0, 5)
    assert e.contains(3, 3) and not e.contains(4, 4)
    assert e.area() == pytest.approx(math.pi * 25)
    assert shape_from_dict(e.to_dict()).contains(1, 1)
    assert np.allclose(r.centroid(), (5, 5))
    d = r.distance_to_edge(np.array([5.0]), np.array([1.0]))
    assert d[0] == pytest.approx(1.0)


def test_segment_intersection_direction():
    p1 = np.array([[0, -1], [0, 1], [5, 5]], float)
    p2 = np.array([[0, 1], [0, -1], [6, 6]], float)
    hit, sign = segments_intersect(p1, p2, (-1, 0), (1, 0))
    assert hit.tolist() == [True, True, False]
    assert sign[0] == -sign[1] != 0


# --------------------------------------------------------------- templates
@pytest.mark.parametrize("key", list(templates.TEMPLATES))
def test_templates_build_and_roundtrip(key):
    app = templates.build(key, 50, 50, 300, 300)
    assert app.arena is not None
    d = app.to_dict()
    app2 = Apparatus.from_dict(d)
    assert [z.name for z in app2.zones] == [z.name for z in app.zones]
    # every zone centroid lies within the arena (with tolerance for zones on the edge)
    arena = app.arena_or_bounds()
    x0, y0, x1, y1 = arena.bounds()
    for z in app.zones:
        cx, cy = z.shape.centroid()
        assert x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1, z.name


def test_epm_groups():
    app = templates.build("epm", 0, 0, 750, 750)
    m = app.zone_membership(np.array([10.0, 375.0]), np.array([375.0, 10.0]))
    assert m["Open arms"].tolist() == [True, False]
    assert m["Closed arms"].tolist() == [False, True]
    assert app.px_per_cm == pytest.approx(10.0)


def test_group_exclude():
    app = templates.build("open_field", 0, 0, 100, 100, size_cm=10)
    m = app.zone_membership(np.array([50.0, 5.0]), np.array([50.0, 5.0]))
    assert m["Centre"].tolist() == [True, False]
    assert m["Periphery"].tolist() == [False, True]


# ---------------------------------------------------------------- measures
def open_field_app():
    app = Apparatus(name="OF", arena=rect(0, 0, 100, 100), px_per_cm=1.0, template="open_field")
    app.zones = [Zone("Left", rect(0, 0, 50, 100)), Zone("Right", rect(50, 0, 50, 100))]
    app.lines = [Line("Mid", 50, 0, 50, 100)]
    app.points = [PointOfInterest("P", 80, 50, radius_cm=5)]
    return app


def test_zone_time_entries_latency_distance():
    # 2 s left (x=25), move right over 1 s, 2 s right, back left
    fps = 10
    pts = [(25, 50)] * 20 + [(25 + 50 * i / 10, 50) for i in range(1, 11)] + [(75, 50)] * 20 + [(25, 50)] * 10
    tr = make_track(pts, fps=fps)
    s = AnalysisSettings(speed_smoothing_s=0, latency_if_never="blank")
    r = analyse(tr, open_field_app(), s)
    assert r["Test duration (s)"] == pytest.approx(6.0)
    assert r["Left: entries"] == 2
    assert r["Right: entries"] == 1
    assert r["Right: latency to first entry (s)"] == pytest.approx(2.5, abs=0.11)
    assert r["Left: time (s)"] + r["Right: time (s)"] == pytest.approx(6.0)
    assert r["Total distance (cm)"] == pytest.approx(100.0, rel=1e-6)
    assert r["Mid: crossings"] == 2
    assert r["Mid: crossings left-to-right"] + r["Mid: crossings right-to-left"] == 2
    assert r["P: time near (s)"] == pytest.approx(2.0, abs=0.11)


def test_entry_min_duration_filters_brief_visits():
    pts = [(25, 50)] * 20 + [(60, 50)] * 2 + [(25, 50)] * 20
    tr = make_track(pts, fps=10)
    r = analyse(tr, open_field_app(), AnalysisSettings(entry_min_duration_s=0.5, speed_smoothing_s=0))
    assert r["Right: entries"] == 0


def test_rotations():
    a = np.linspace(0, 3 * 360 + 10, 400)
    assert count_rotations(a) == (3, 0)
    assert count_rotations(-a) == (0, 3)
    wobble = np.concatenate([np.linspace(0, 300, 50), np.linspace(300, 100, 50), np.linspace(100, 380, 50)])
    assert count_rotations(wobble) == (0, 0)


def test_freezing_from_motion():
    n = 250
    fps = 25
    motion = np.full(n, 200.0)
    motion[50:150] = 1.0  # 4 s freezing
    tr = make_track([(50, 50)] * n, fps=fps, area=np.full(n, 400.0), motion=motion)
    r = analyse(tr, open_field_app(), AnalysisSettings())
    assert r["Time freezing (s)"] == pytest.approx(4.0, abs=0.1)
    assert r["Freezing episodes"] == 1
    assert r["Latency to first freezing (s)"] == pytest.approx(2.0, abs=0.05)


def test_y_maze_alternation():
    app = templates.build("y_maze", 0, 0, 600, 600)
    centre = app.zone("Centre").shape.centroid()
    arm = {z.name: z.shape.centroid() for z in app.zones if z.name.startswith("Arm")}
    seq = ["Arm A", "Arm B", "Arm C", "Arm A", "Arm C", "Arm B", "Arm B"]
    way = [centre]
    for a in seq:
        way += [arm[a], centre]
    pos = syn.waypoint_path(way, speed_px_per_frame=8)
    tr = make_track(pos)
    r = analyse(tr, app, AnalysisSettings())
    assert r["Arm entry sequence"] == "ABCACBB"
    assert r["Total arm entries"] == 7
    # triplets: ABC, BCA, CAC, ACB, CBB -> 3 alternations of 5
    assert r["Spontaneous alternations"] == 3
    assert r["Alternation (%)"] == pytest.approx(60.0)


def test_water_maze_latency():
    app = templates.build("water_maze", 0, 0, 300, 300)
    pc = app.point("Platform centre")
    pos = syn.line_path((150, 280), (pc.x, pc.y), 100)
    pos = np.vstack([pos, np.repeat(pos[-1:], 25, axis=0)])
    r = analyse(make_track(pos), app, AnalysisSettings())
    assert r["Found platform"] == "Yes"
    assert 3.0 < r["Escape latency (s)"] < 4.0
    assert r["Initial heading error (deg)"] < 5


def test_time_bins():
    pts = [(25, 50)] * 50 + [(75, 50)] * 50
    tr = make_track(pts, fps=10)
    parts = analyse_segmented(tr, open_field_app(), AnalysisSettings(bin_length_s=5, speed_smoothing_s=0))
    labels = [p[0] for p in parts]
    assert labels == ["Whole test", "0-5 s", "5-10 s"]
    assert parts[1][1]["Left: time (s)"] == pytest.approx(5.0)
    assert parts[2][1]["Right: time (s)"] == pytest.approx(5.0)


def test_behaviour_measures():
    tr = make_track([(25, 50)] * 100, fps=10)
    b = [{"name": "Groom", "key": "g", "kind": "state"}, {"name": "Poop", "key": "p", "kind": "point"}]
    ev = [{"behaviour": "Groom", "t": 1, "t_end": 3}, {"behaviour": "Groom", "t": 5, "t_end": 6},
          {"behaviour": "Poop", "t": 4, "t_end": None}]
    r = analyse(tr, open_field_app(), AnalysisSettings(), events=ev, behaviours=b)
    assert r["Groom: duration (s)"] == pytest.approx(3.0)
    assert r["Groom: count"] == 2
    assert r["Groom: latency (s)"] == pytest.approx(1.0)
    assert r["Poop: count"] == 1


def test_runs_helper():
    assert runs(np.array([0, 1, 1, 0, 1], bool)) == [(1, 3), (4, 5)]


# --------------------------------------------------------------- track io
def test_track_csv_roundtrip(tmp_path):
    tr = make_track([(1, 2), (3, 4), (np.nan, np.nan), (5, 6)], area=[1, 2, 3, 4])
    tr.meta["video"] = "x.avi"
    p = tmp_path / "t.csv"
    tr.to_csv(p)
    tr2 = Track.from_csv(p)
    assert np.allclose(tr2.x, tr.x, equal_nan=True)
    assert tr2.meta["video"] == "x.avi"
    filled = tr2.interpolate(1.0)
    assert filled.x[2] == pytest.approx(4.0)


# -------------------------------------------------------------------- stats
def test_stats_two_and_three_groups():
    rng = np.random.default_rng(0)
    a, b, c = rng.normal(10, 1, 12), rng.normal(14, 1, 12), rng.normal(10.2, 1, 12)
    r2 = compare_groups({"A": a, "B": b})
    assert r2["test"] == "Welch's t-test" and r2["p"] < 0.001
    r3 = compare_groups({"A": a, "B": b, "C": c})
    assert r3["test"] == "One-way ANOVA" and r3["p"] < 0.001
    ph = {(p["a"], p["b"]): p["p"] for p in r3["posthoc"]}
    assert ph[("A", "B")] < 0.01 and ph[("A", "C")] > 0.05
    rnp = compare_groups({"A": a, "B": b, "C": c}, parametric=False)
    assert rnp["test"] == "Kruskal-Wallis"


def test_two_way_anova():
    rng = np.random.default_rng(1)
    rows = []
    for g, ge in (("WT", 0), ("KO", 5)):
        for d, de in (("D1", 0), ("D2", -3), ("D3", -6)):
            for _ in range(6):
                rows.append({"Group": g, "Stage": d, "Latency": 30 + ge + de + rng.normal(0, 1)})
    res = two_way_anova(rows, "Latency")
    eff = {e["effect"]: e for e in res["effects"]}
    assert eff["Group"]["p"] < 0.001 and eff["Stage"]["p"] < 0.001
    assert eff["Group × Stage"]["p"] > 0.01


# --------------------------------------------------------------- procedures
def test_procedure_engine():
    rules = [{"trigger": "zone_enter", "zone": "Right", "action": "serial", "payload": "ON"},
             {"trigger": "zone_exit", "zone": "Right", "action": "serial", "payload": "OFF"},
             {"trigger": "time", "time_s": 2, "action": "mark", "payload": "Tone"},
             {"trigger": "zone_enter", "zone": "Goal", "action": "end_test", "delay_s": 1}]
    marks = []
    eng = ProcedureEngine(rules, on_mark=lambda n, t: marks.append((n, t)))
    eng.start(0)
    for i in range(60):
        t = i / 10
        eng.update(t, {"Right": 1.0 <= t < 3.0, "Goal": t >= 4.0})
    assert eng.outputs.log[:2] == ["serial: ON", "serial: OFF"]
    assert marks == [("Tone", 2.0)]
    assert eng.ended


def test_grid_crossings():
    app = templates.build("open_field", 0, 0, 400, 400, size_cm=40)
    # cross from cell column 0 to column 3 along y=50 (row 0): 3 crossings
    pos = syn.line_path((20, 50), (380, 50), 50)
    r = analyse(make_track(pos), app, AnalysisSettings())
    assert r["Grid crossings (4×4)"] == 3
