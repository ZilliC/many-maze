import math

import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.apparatus import Apparatus, Line, PointOfInterest, Zone, ZoneGroup, rename_factor
from manymaze.core.geometry import circle, rect, segments_intersect, shape_from_dict
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented, count_rotations, runs
from manymaze.core.procedures import ProcedureEngine
from manymaze.core.project import Behaviour
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
    b = [Behaviour("Groom", "g", "state"), Behaviour("Poop", "p", "point")]
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


# --------------------------------------------------------------- editing apparatus and projects
def _editable_app():
    from manymaze.core.apparatus import Grid, Sequence

    app = Apparatus(name="Box", arena=rect(0, 0, 100, 100))
    app.zones = [Zone(n, rect(i * 10, 0, 10, 10)) for i, n in enumerate(["A", "B", "C", "G 1", "G 2"])]
    app.points = [PointOfInterest("P", 5, 5), PointOfInterest("Q", 6, 6)]
    app.groups = [ZoneGroup("AB", ["A", "B"], ["C"]), ZoneGroup("Grp", ["A"])]
    app.sequences = [Sequence("S", ["A", "AB", "C"])]
    app.grids = [Grid("G", "square", ["G 1", "G 2"])]
    return app


def test_unique_name():
    from manymaze.core.apparatus import unique_name

    assert unique_name(" A ", ["B"]) == "A" and unique_name("A", ["A", "A 2"]) == "A 3"
    assert unique_name("  ", []) == "Unnamed"


def test_apparatus_rename_updates_references():
    app = _editable_app()
    assert app.rename("zone", 0, "Alpha") == "Alpha"
    assert app.groups[0].zones == ["Alpha", "B"] and app.groups[1].zones == ["Alpha"]
    assert app.sequences[0].steps == ["Alpha", "AB", "C"]
    assert app.rename("zone", 2, "AB") == "AB 2"  # zones and groups share names
    assert app.groups[0].exclude == ["AB 2"] and app.sequences[0].steps[2] == "AB 2"
    assert app.rename("zone", 3, "Cell") == "Cell" and app.grids[0].zones == ["Cell", "G 2"]
    assert app.rename("group", 0, "Pair") == "Pair" and app.sequences[0].steps == ["Alpha", "Pair", "AB 2"]
    assert app.rename("point", 0, "Q") == "Q 2" and app.rename("point", 1, " ") == "Q"
    assert app.rename("sequence", 0, "Path") == "Path"


def test_apparatus_remove_cascades():
    app = _editable_app()
    app.remove("zone", 0)
    assert app.groups[0].zones == ["B"] and app.groups[1].zones == [] and app.sequences[0].steps == ["AB", "C"]
    app.remove("group", 0)
    assert [g.name for g in app.groups] == ["Grp"] and app.sequences[0].steps == ["C"]
    app.remove("zone", app.zones.index(app.zone("G 1")))
    app.remove("zone", app.zones.index(app.zone("G 2")))
    assert app.grids == []
    app.remove("point", 1)
    app.remove("sequence", 0)
    app.remove("arena")
    assert [p.name for p in app.points] == ["P"] and app.sequences == [] and app.arena is None


def test_remove_grid_drops_sequence_steps():
    from manymaze.core.apparatus import Sequence, make_grid, remove_grid

    app = templates.build("open_field", 0, 0, 400, 400)
    make_grid(app, "square", name="G", nx=2, ny=1)
    app.sequences.append(Sequence("S", ["G A1", "Centre", "G"]))
    assert remove_grid(app, "G") and app.sequences[0].steps == ["Centre"]


def test_procedure_edit_operations():
    from manymaze.core.procedures import edit as pe, new_procedure, new_statement

    def types(stmts):
        return [s["type"] for s in stmts]

    p = new_procedure()
    assert pe.add(p, None, new_statement("when")) == (0,)
    assert pe.add(p, (0,), new_statement("if"), inside=True) == (0, "body", 0)
    assert pe.add(p, (0, "body", 0), new_statement("set"), inside=True) == (0, "body", 0, "body", 0)
    assert pe.add(p, (0, "body", 0, "body", 0), new_statement("do"), inside=True) is None  # not a container
    p["statements"][0]["body"][0]["else"] = []
    assert pe.add(p, (0, "body", 0, "else"), new_statement("stop")) == (0, "body", 0, "else", 0)
    assert pe.add(p, (0,), new_statement("wait")) == (1,)
    assert pe.add(p, (1,), new_statement("repeat")) == (2,)
    assert pe.add(p, (2,), new_statement("comment")) == (3,)
    st = p["statements"]
    assert types(st) == ["when", "wait", "repeat", "comment"]
    # indent into the repeat above, outdent back out, move, duplicate, toggle, remove
    assert pe.indent(p, (3,)) == (2, "body", 0) and types(st) == ["when", "wait", "repeat"]
    assert pe.indent(p, (1,)) == (0, "body", 1) and types(st[0]["body"]) == ["if", "wait"]
    assert pe.indent(p, (0, "body", 1)) == (0, "body", 0, "else", 1)  # into the If's Else branch
    assert pe.outdent(p, (0, "body", 0, "else", 1)) == (0, "body", 1)
    assert pe.outdent(p, (0,)) is None and pe.move(p, (0,), -1) is None
    assert pe.move(p, (0, "body", 1), -1) == (0, "body", 0) and types(st[0]["body"]) == ["wait", "if"]
    assert pe.duplicate(p, (1,)) == (2,) and types(st) == ["when", "repeat", "repeat"]
    assert pe.toggle(p, (2,)) == (2,) and st[2]["enabled"] is False
    assert pe.toggle(p, (2,)) == (2,) and "enabled" not in st[2]
    assert pe.remove(p, (2,)) == (1,) and pe.remove(p, (1, "body", 0)) == (1,)
    assert pe.remove(p, (0, "body", 1, "else")) == (0, "body", 1) and "else" not in st[0]["body"][1]
    # drag and drop: the destination is resolved before the statement leaves its place
    pe.add(p, (1,), new_statement("var"))
    assert types(st) == ["when", "repeat", "var"]
    assert pe.move_to(p, (0,), (1, "body"), 0) == (0, "body", 0)
    assert types(st) == ["repeat", "var"] and types(st[0]["body"]) == ["when"]
    assert pe.move_to(p, (0, "body", 0), (), 2) == (2,) and types(st) == ["repeat", "var", "when"]
    assert pe.remove(p, (0,)) == (0,) and pe.remove(p, (0,)) == (0,) and pe.remove(p, (0,)) == ()


def test_project_rename_moves_zone_overrides():
    from manymaze.core.project import Project

    p = Project()
    app, other = _editable_app(), Apparatus(name="Other")
    p.apparatus = [app, other]
    ov = {"A": {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]}, "P": {"x": 1, "y": 2}}
    t1 = p.add_test(apparatus="Box", zone_overrides=dict(ov))
    t2 = p.add_test(apparatus="Missing", zone_overrides=dict(ov))  # resolves to the first apparatus
    t3 = p.add_test(apparatus="Other", zone_overrides=dict(ov))
    assert p.tests_using("Box") == [t1, t2]
    assert p.rename_in_apparatus(app, "zone", 0, "Alpha") == "Alpha"
    assert p.rename_in_apparatus(app, "point", 0, "Pt") == "Pt"
    assert set(t1.zone_overrides) == set(t2.zone_overrides) == {"Alpha", "Pt"} and t3.zone_overrides == ov
    assert app.with_overrides(t1.zone_overrides).point("Pt").x == 1
    assert Project.from_dict({}).protocol == Project().protocol


def test_project_round_trip_ignores_unknown_keys():
    from manymaze.core.project import Animal, Behaviour, Group, Project

    p = Project(name="R")
    p.apparatus = [templates.build("light_dark", 0, 0, 300, 200)]
    p.animals = [Animal("A1", "G", fields={"w": "20"})]
    p.groups = [Group("G", "#ffffff")]
    p.behaviours = [Behaviour("Rear", "r")]
    p.add_test(animal_id="A1", zone_overrides={"Doorway": {"x": 1, "y": 2}})
    d = p.to_dict()
    assert Project.from_dict(d).to_dict() == d
    for key in ("groups", "behaviours", "tests"):
        d[key][0]["from_the_future"] = 1
    d["apparatus"][0]["lines"][0]["from_the_future"] = 1
    q = Project.from_dict(d)
    assert q.groups == p.groups and q.tests == p.tests and q.apparatus[0].lines == p.apparatus[0].lines


def test_project_apparatus_of_and_test_periods():
    from manymaze.core.project import Project

    p = Project()
    p.apparatus = [templates.build("water_maze", 0, 0, 400, 400)]
    p.analysis.bin_length_s = 2
    t = p.add_test(zone_overrides={"Platform": {"type": "ellipse", "cx": 100, "cy": 300, "rx": 8, "ry": 8}})
    app = p.apparatus_of(t)
    assert app.zone("Platform").shape.centroid() == pytest.approx((100, 300))
    assert app.group("Target quadrant").zones == ["Quadrant SW"] and p.apparatus[0].zone("Platform") is not None
    t.pauses = [[1.0, 2.0]]
    tr = make_track(np.full((125, 2), 200.0))  # 5 s, 1 s of it paused
    assert p.test_periods(t, tr) == [("0-2 s", 0, 2), ("2-4 s", 2, 4.0)]


# --------------------------------------------------------------- distance units
def _units_track():
    # 2 s still, 50 px to the right in 1 s, 2 s still, 50 px back: 100 cm at 1 px/cm
    pts = [(25, 50)] * 20 + [(25 + 50 * i / 10, 50) for i in range(1, 11)] + [(75, 50)] * 20 + \
          [(75 - 50 * i / 10, 50) for i in range(1, 11)]
    return make_track(pts, fps=10)


def test_distance_units_in_the_results():
    from manymaze.core.apparatus import DISTANCE_UNITS, to_report_units
    from manymaze.core.calculations import Calculation

    s = AnalysisSettings(speed_smoothing_s=0, mobility_threshold=5.0, bin_length_s=3.0)
    tr = _units_track()
    app = open_field_app()
    cm = analyse(tr, app, s)
    app.distance_unit = "cm"
    assert list(analyse(tr, app, s).items()) == list(cm.items())  # cm: names and values exactly as before
    assert cm["Total distance (cm)"] == pytest.approx(100.0)
    for unit, f in DISTANCE_UNITS.items():
        app.distance_unit = unit
        res = analyse(tr, app, s)
        assert res[f"Total distance ({unit})"] == pytest.approx(100.0 * f)
        assert res[f"Mean speed ({unit}/s)"] == pytest.approx(cm["Mean speed (cm/s)"] * f)
        assert res[f"Max speed ({unit}/s)"] == pytest.approx(cm["Max speed (cm/s)"] * f)
        assert res[f"Left: distance ({unit})"] == pytest.approx(cm["Left: distance (cm)"] * f)
        assert res[f"P: mean distance ({unit})"] == pytest.approx(cm["P: mean distance (cm)"] * f)
        assert res[f"Meander (deg/{unit})"] == pytest.approx(cm["Meander (deg/cm)"] / f)
        # the settings stay in cm: the same mobility, the same times and counts
        assert res["Time mobile (s)"] == cm["Time mobile (s)"] and res["Left: entries"] == cm["Left: entries"]
        assert len(res) == len(cm) and (unit == "cm") == ("Total distance (cm)" in res)
    # time bins, and calculations naming the measures in the unit of the results
    app.distance_unit = "m"
    calc = Calculation("Distance per entry", "{Total distance (m)} / {Left: entries}", decimals=3)
    rows = dict(analyse_segmented(tr, app, s, calculations=[calc]))
    assert rows["0-3 s"]["Total distance (m)"] == pytest.approx(0.5, abs=0.01)
    assert rows["Whole test"]["Distance per entry"] == pytest.approx(0.5)
    # an uncalibrated apparatus reports pixels whatever the unit
    app.px_per_cm = None
    assert "Total distance (px)" in analyse(tr, app, s) and app.report_unit == "px" and app.report_factor == 1
    assert to_report_units({"Body (cm²)": 2.0, "A (cm·s)": 3.0, "Text (cm)": "x", "N": 4}, "mm") == \
        {"Body (mm²)": 200.0, "A (mm·s)": 30.0, "Text (mm)": "x", "N": 4}


def test_distance_unit_stored_charts_and_exports(tmp_path):
    from manymaze.core import charts, export
    from manymaze.core.calculations import Calculation
    from manymaze.core.project import Project

    app = open_field_app()
    assert "distance_unit" not in app.to_dict()  # cm: apparatus maps saved as before
    app.distance_unit = "mm"
    d = app.to_dict()
    assert d["distance_unit"] == "mm" and Apparatus.from_dict(d).distance_unit == "mm"
    assert app.copy().distance_unit == "mm" and app.with_overrides({"P": {"x": 1, "y": 2}}).distance_unit == "mm"
    assert Apparatus.from_dict({**d, "distance_unit": "furlong"}).distance_unit == "cm"
    assert app.length_text(12.5) == "125 mm"
    # charts and the per-frame export follow the unit
    tr = _units_track()
    params = {p.name: p for p in charts.parameters(app, tr)}
    assert params["Speed"].label == "Speed (mm/s)" and params["Body area"].unit == "mm²"
    assert params["X position"].label == "X position (mm)" and params["Motion"].unit == "% body"
    v = charts.compute(tr, app, ["X position", "Distance travelled"], AnalysisSettings(speed_smoothing_s=0))
    assert v["X position"][0] == pytest.approx(250.0) and v["Distance travelled"][-1] == pytest.approx(1000.0)
    # the experiment: one unit for every apparatus; formulas, criteria and the measure filter follow it
    p = Project(name="units")
    p.apparatus = [open_field_app(), open_field_app()]
    p.calculations = [Calculation("Ratio", "{Total distance (cm)} / {Mean speed (cm/s)} + {Left: entries}"),
                      Calculation("Path", "{Left: entries} * 10", units="cm", y_min=0, y_max=500)]
    p.training_criteria = [{"stage": "", "measure": "Total distance (cm)", "op": ">", "value": 1000,
                            "variability": {"stat": "sd", "trials": 3, "max": 1, "measure": "Max speed (cm/s)"}},
                           {"stage": "", "measure": "Total distance (cm)", "op": ">", "value": 50,
                            "variability": {"stat": "cv", "trials": 3, "max": 20, "measure": ""}}]
    p.analysis.measure_filter = ["Total distance (cm)", "Left: entries"]
    p.reports = [{"name": "R", "measures": ["Total distance (cm)", "Ratio"]}]
    p.statistics = {"measure": "Mean speed (cm/s)", "corr_x": "Ratio", "alpha": 0.05}
    assert p.distance_unit == "cm"
    assert p.set_distance_unit("m") == ["Ratio"]  # its constants may need changing by hand
    assert [a.distance_unit for a in p.apparatus] == ["m", "m"] and p.distance_unit == "m"
    assert p.calculations[0].formula == "{Total distance (m)} / {Mean speed (m/s)} + {Left: entries}"
    assert p.training_criteria[0]["measure"] == "Total distance (m)"
    assert p.training_criteria[0]["variability"]["measure"] == "Max speed (m/s)"
    assert p.analysis.measure_filter == ["Total distance (m)", "Left: entries"]
    # the numbers compared with the renamed measures are converted too
    c0, c1 = p.training_criteria
    assert c0["value"] == pytest.approx(10) and c0["variability"]["max"] == pytest.approx(0.01)
    assert c1["value"] == pytest.approx(0.5) and c1["variability"]["max"] == 20  # (a CV has no unit)
    path = p.calculations[1]
    assert (path.units, path.y_min, path.y_max) == ("m", 0, pytest.approx(5))
    assert p.reports[0]["measures"] == ["Total distance (m)", "Ratio"]
    assert p.statistics == {"measure": "Mean speed (m/s)", "corr_x": "Ratio", "alpha": 0.05}
    p.rename_calculation("Ratio", "Index")
    assert p.reports[0]["measures"] == ["Total distance (m)", "Index"] and p.statistics["corr_x"] == "Index"
    assert rename_factor("Body area (cm²)", "cm", "mm") == pytest.approx(100)
    assert rename_factor("Meander (deg/cm)", "cm", "m") == pytest.approx(100)
    assert rename_factor("Left: entries", "cm", "m") == 1.0
    with pytest.raises(ValueError):
        p.set_distance_unit("in")
    q = Project.from_dict(p.to_dict())
    assert q.distance_unit == "m"
    # exports: the XML apparatus and the track files say which unit the values are in
    p.path = tmp_path / "units.mmaze"
    p.path.mkdir()
    t = p.add_test("", "A1", apparatus=p.apparatus[0].name)
    p.save_tracks(t, [tr])
    raw = export.export_raw_data(p, tmp_path / "raw", parameters=["X position", "Speed"])
    lines = raw[0].read_text().splitlines()
    assert "unit m " in lines[0] and "X position (m)" in lines[1] and "Speed (m/s)" in lines[1]
    xml = export.export_xml(p, tmp_path / "e.xml").read_text()
    assert 'unit="m"' in xml


def test_distance_unit_live():
    from manymaze.core.live import LiveStats
    from manymaze.core.livemonitor import chart_parameters
    from manymaze.core.tracking import Detection

    app = open_field_app()
    app.distance_unit = "mm"
    st = LiveStats(app, 10.0, AnalysisSettings(speed_smoothing_s=0.1, mobility_threshold=5.0))
    for i in range(30):  # 2 px (2 cm) per frame at 10 frames/s: 20 cm/s
        st.update(i / 10, Detection(10 + 2 * i, 50, area=50.0, motion=0.0, detected=True), {}, False)
    assert st.unit == "mm" and st.factor == 10.0
    assert st.distance == pytest.approx(58.0, abs=1.0) and not st.immobile  # cm, compared with the threshold
    t, v = st.series("distance")
    assert v[-1] == pytest.approx(st.distance * 10)  # shown in mm
    keys = {k: u for k, _label, u in chart_parameters(app)}
    assert keys["speed"] == "mm/s" and keys["distance"] == "mm" and keys["chart:X position"] == "mm"
