"""ANY-maze parity: zone flags and entry rules, grids, points, sequences, event periods, templates, social."""

import math

import numpy as np
import pytest

from manymaze.core import templates
from manymaze.core.apparatus import (Apparatus, PointOfInterest, Sequence, Zone, grid_shapes, make_grid,
                                     remove_grid)
from manymaze.core.geometry import annular_sector, body_fraction_inside, circle, rect
from manymaze.core.measures import (AnalysisSettings, all_periods, analyse, analyse_segmented, behaviour_measures,
                                    occupancy)
from manymaze.core.periods import event_periods
from manymaze.core.project import Behaviour
from manymaze.core.sequences import find_sequences, sequence_measures
from manymaze.core.track import Track

FPS = 25.0


def make_track(points, fps=FPS, head=True, body_len=20.0, **kw):
    """Track from centre positions; head/tail placed along the direction of travel."""
    p = np.asarray(points, float)
    n = len(p)
    t = np.arange(n) / fps
    if head:
        d = np.gradient(p, axis=0) if n > 1 else np.zeros_like(p)
        ang = np.arctan2(d[:, 1], d[:, 0])
        # hold the last heading while stationary
        still = np.hypot(d[:, 0], d[:, 1]) < 1e-9
        for i in range(1, n):
            if still[i]:
                ang[i] = ang[i - 1]
        kw.setdefault("angle", np.degrees(ang))
        a = np.radians(kw["angle"])
        kw.setdefault("hx", p[:, 0] + np.cos(a) * body_len / 2)
        kw.setdefault("hy", p[:, 1] + np.sin(a) * body_len / 2)
        kw.setdefault("tx", p[:, 0] - np.cos(a) * body_len / 2)
        kw.setdefault("ty", p[:, 1] - np.sin(a) * body_len / 2)
    kw.setdefault("area", np.full(n, body_len * body_len / 3))
    return Track(t=t, x=p[:, 0], y=p[:, 1], fps=fps, **kw)


def line(p0, p1, n):
    return np.column_stack([np.linspace(p0[0], p1[0], n), np.linspace(p0[1], p1[1], n)])


def hold(p, n):
    return np.tile(np.asarray(p, float), (n, 1))


def box_app(**kw) -> Apparatus:
    app = Apparatus(name="Box", arena=rect(0, 0, 400, 400), px_per_cm=10.0, **kw)
    app.zones.append(Zone("Arena", rect(0, 0, 400, 400), "#64748b"))
    return app


S = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0)


# ------------------------------------------------------------------ model
def test_zone_flags_roundtrip_and_backward_compat():
    z = Zone("Nest", rect(0, 0, 10, 10), hidden=True, investigation_distance_cm=2.0, moveable=True,
             entry_rule="body", body_fraction=0.6)
    d = z.to_dict()
    z2 = Zone.from_dict(d)
    assert (z2.hidden, z2.investigation_distance_cm, z2.moveable, z2.entry_rule, z2.body_fraction) == \
        (True, 2.0, True, "body", 0.6)
    plain = Zone("A", rect(0, 0, 1, 1)).to_dict()
    assert set(plain) == {"name", "shape", "color"}  # defaults are not written: old files stay identical
    old = Apparatus.from_dict({"name": "x", "zones": [plain]})
    assert old.sequences == [] and old.grids == [] and not old.zones[0].hidden
    app = box_app()
    app.sequences.append(Sequence("ABC", ["A", "B", "C"], bidirectional=True, end="exit", max_duration_s=5))
    make_grid(app, "square", nx=2, ny=2)
    app2 = Apparatus.from_dict(app.to_dict())
    assert app2.sequences[0].to_dict() == app.sequences[0].to_dict()
    assert app2.grids[0].zones == app.grids[0].zones == ["Grid A1", "Grid A2", "Grid B1", "Grid B2"]
    assert app2.group("Grid") is not None


# ------------------------------------------------------------------ grids
def test_square_grid_names_areas_and_cell_size():
    region = rect(0, 0, 400, 200)
    cells = grid_shapes("square", region, nx=4, ny=2)
    assert [n for n, _ in cells] == ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]
    assert sum(s.area() for _, s in cells) == pytest.approx(80000)
    # 5 cm cells on a 10 px/cm calibration → 8 × 4 cells of 50 px
    cells = grid_shapes("square", region, cell_cm=5.0, px_per_cm=10.0)
    assert len(cells) == 32
    assert cells[0][1].area() == pytest.approx(2500)


def test_square_grid_clipped_to_circle():
    cells = grid_shapes("square", circle(200, 200, 200), nx=4, ny=4)
    total = sum(s.area() for _, s in cells)
    assert total == pytest.approx(math.pi * 200 ** 2, rel=0.01)
    assert all(s.contains(*s.centroid()) for _, s in cells)


def test_concentric_and_radial_grids():
    rings = grid_shapes("rings", circle(0, 0, 90), rings=3)
    assert [n for n, _ in rings] == ["Ring 1", "Ring 2", "Ring 3"]
    assert rings[1][1].contains(45, 0) and not rings[1][1].contains(10, 0) and not rings[1][1].contains(80, 0)
    assert sum(s.area() for _, s in rings) == pytest.approx(math.pi * 90 ** 2, rel=0.01)
    secs = grid_shapes("sectors", circle(0, 0, 90), sectors=4, start_deg=-90)
    # Sector 1 spans from the top clockwise to the right (y down): upper-right quadrant
    assert secs[0][1].contains(30, -30) and secs[1][1].contains(30, 30) and secs[3][1].contains(-30, -30)
    polar = grid_shapes("polar", circle(0, 0, 90), rings=2, sectors=4)
    assert [n for n, _ in polar] == ["R1", "R2 S1", "R2 S2", "R2 S3", "R2 S4"]
    ring = annular_sector(0, 0, 50, 100, 0, 360)
    assert ring.contains(75, 0) and not ring.contains(0, 0)


def test_make_grid_crossings_and_remove():
    app = box_app()
    g = make_grid(app, "square", nx=4, ny=4)
    assert len(g.zones) == 16 and app.zone("Grid A1") is not None
    # walk along the top row from cell A1 to A4: 3 crossings
    tr = make_track(line((20, 50), (380, 50), 100))
    res = analyse(tr, app, S)
    assert res["Grid: crossings"] == 3
    assert res["Grid: cells visited"] == 4
    assert res["Grid: inner cells time (%)"] == 0
    g2 = make_grid(app, "rings", rings=4)
    assert g2.name == "Grid 2"
    res = analyse(make_track(line((200, 200), (390, 200), 60)), app, S)
    assert res["Grid 2: crossings"] == 3 and res["Grid 2: outer ring time (%)"] > 0
    assert remove_grid(app, "Grid")
    assert app.zone("Grid A1") is None and app.group("Grid") is None and app.grid("Grid 2") is not None


# ------------------------------------------------------------------ entry rules
def test_body_fraction_inside_ellipse():
    z = rect(0, 0, 100, 100)
    f = body_fraction_inside(z, np.array([50.0, 100.0, 200.0]), np.array([50.0, 50.0, 50.0]), np.zeros(3),
                             np.full(3, 10.0), np.full(3, 5.0))
    assert f[0] == 1.0 and 0.35 < f[1] < 0.65 and f[2] == 0.0


def test_entry_rules_head_body_exclusion():
    app = box_app()
    app.zones.append(Zone("Centre", rect(150, 150, 100, 100)))
    app.zones.append(Zone("Edge", rect(0, 0, 400, 400), entry_rule="exclusion"))
    app.zones.append(Zone("Head zone", rect(150, 150, 100, 100), entry_rule="head"))
    app.zones.append(Zone("Body 80", rect(150, 150, 100, 100), entry_rule="body", body_fraction=0.8))
    # centre stops 5 px inside the zone edge, facing into the zone: head is inside, most of the body is not
    pts = np.vstack([line((60, 200), (155, 200), 40), hold((155, 200), 40)])
    tr = make_track(pts, body_len=40)
    memb, head_memb, hidden = occupancy(tr, app, S)
    assert memb["Centre"][-1] and memb["Head zone"][-1] and not memb["Body 80"][-1]
    assert not memb["Edge"][-1] and memb["Edge"][0]  # exclusion: arena minus the smaller zones
    # deep inside the zone the whole body is in
    tr2 = make_track(np.vstack([line((60, 200), (200, 200), 40), hold((200, 200), 20)]), body_len=40)
    assert occupancy(tr2, app, S)[0]["Body 80"][-1]
    # head rule switches earlier than centre
    first = lambda m: int(np.flatnonzero(m)[0])
    m2 = occupancy(tr2, app, S)[0]
    assert first(m2["Head zone"]) < first(m2["Centre"]) < first(m2["Body 80"])
    # global body rule from settings
    s = AnalysisSettings(zone_body_part="body", body_proportion_pct=40)
    assert occupancy(tr, app, s)[0]["Centre"][-1]


def test_investigation_zone_counts_head_within_distance():
    app = box_app()
    app.zones.append(Zone("Object", circle(200, 200, 20), investigation_distance_cm=2.0))
    # stop with the head 15 px (1.5 cm) from the object edge
    pts = np.vstack([line((60, 200), (145, 200), 30), hold((145, 200), 50)])
    tr = make_track(pts, body_len=40)
    res = analyse(tr, app, S)
    assert res["Object: time (s)"] >= 1.9
    app.zones[-1].investigation_distance_cm = 1.0
    assert analyse(tr, app, S)["Object: time (s)"] == 0


def test_hidden_zone_time_and_no_interpolation():
    app = box_app()
    app.zones.append(Zone("Nest", rect(0, 0, 80, 80), hidden=True))
    pts = np.vstack([line((300, 300), (60, 60), 50), hold((60, 60), 75), line((70, 70), (300, 300), 50)])
    det = np.ones(len(pts), bool)
    det[50:125] = False
    pts = pts.copy()
    # the tracker would interpolate a straight jump across the gap; the hidden logic must ignore it
    pts[50:125] = line((60, 60), (70, 70), 75)
    tr = make_track(pts, head=False, detected=det)
    res = analyse(tr, app, S)
    assert 3.0 < res["Nest: time (s)"] < 3.5  # visible in the nest just before / after hiding
    assert res["Time hidden (s)"] == pytest.approx(3.0, abs=0.05)
    assert res["Time not detected (s)"] == 0
    assert res["Nest: entries"] == 1
    # lost far from the nest → not hidden
    det2 = np.ones(len(pts), bool)
    det2[10:20] = False
    res2 = analyse(make_track(pts, head=False, detected=det2), app, S)
    assert res2["Time not detected (s)"] == pytest.approx(0.4, abs=0.05) and "Time hidden (s)" not in res2


def test_moveable_zone_overrides():
    app = templates.build("water_maze", 0, 0, 300, 300)
    plat = app.zone("Platform")
    assert plat.moveable
    cx, cy = plat.shape.centroid()
    # swim straight to the original platform position
    pts = np.vstack([line((150, 290), (cx, cy), 60), hold((cx, cy), 20)])
    tr = make_track(pts, head=False)
    res = analyse(tr, app, AnalysisSettings())
    assert res["Found platform"] == "Yes"
    moved = circle(60, 60, plat.shape.rx).to_dict()
    res2 = analyse(tr, app, AnalysisSettings(), zone_overrides={"Platform": moved})
    assert res2["Found platform"] == "No"
    app2 = app.with_overrides({"Platform": moved})
    assert app2.point("Platform centre").x == pytest.approx(60) and app.point("Platform centre").x == cx
    assert app2.zone("Platform annulus").shape.centroid() == pytest.approx((60, 60))


# ------------------------------------------------------------------ points / facing
def test_point_towards_away_and_head_orientation():
    app = box_app()
    app.points.append(PointOfInterest("Obj", 300, 200, radius_cm=3.0))
    pts = np.vstack([line((100, 200), (250, 200), 50), line((250, 200), (100, 200), 50)])
    tr = make_track(pts)
    res = analyse(tr, app, S)
    assert res["Obj: time moving towards (s)"] == pytest.approx(2.0, abs=0.15)
    assert res["Obj: time moving away (s)"] == pytest.approx(2.0, abs=0.15)
    assert res["Obj: distance moved towards (cm)"] == pytest.approx(15, abs=0.5)
    assert res["Obj: time head oriented towards (s)"] == pytest.approx(2.0, abs=0.15)
    assert res["Obj: time head oriented away (s)"] == pytest.approx(2.0, abs=0.15)
    assert 60 < res["Obj: mean head angle (deg)"] < 120
    assert res["Obj: max distance (cm)"] == pytest.approx(20, abs=0.1)


def test_time_facing_zone():
    app = box_app()
    app.zones.append(Zone("Target", rect(350, 150, 50, 100)))
    pts = np.vstack([line((100, 200), (200, 200), 25), hold((200, 200), 50)])
    res = analyse(make_track(pts), app, S)
    assert res["Target: time facing (s)"] == pytest.approx(3.0, abs=0.1)


# ------------------------------------------------------------------ sequences
def E(*names):
    return [(n, float(i), float(i) + 0.5) for i, n in enumerate(names)]


def test_sequence_matching_rules():
    q = Sequence("ABC", ["A", "B", "C"])
    att = find_sequences(q, E("A", "B", "C", "A", "C", "B", "A", "B", "C"))
    done = [a for a in att if a.completed]
    assert len(done) == 2 and sum(a.errors for a in att) == 1
    m = sequence_measures(q, att, 0.0, 60.0)
    assert m["ABC: completed"] == 2 and m["ABC: latency to first (s)"] == 2.0
    assert m["ABC: first duration (s)"] == 2.0 and m["ABC: rate (/min)"] == 2.0
    assert m["ABC: incomplete"] == 1 and m["ABC: completion (%)"] == pytest.approx(66.67, abs=0.01)
    assert len([k for k in m if k.startswith("ABC: ")]) >= 12
    # reverse order counts only when bidirectional
    assert sum(a.completed for a in find_sequences(q, E("C", "B", "A"))) == 0
    qb = Sequence("ABC", ["A", "B", "C"], bidirectional=True)
    att = find_sequences(qb, E("C", "B", "A"))
    assert att[0].completed and att[0].direction == -1
    # cyclic rotations when the pattern need not start at the first step
    qr = Sequence("ABC", ["A", "B", "C"], from_start=False)
    assert sum(a.completed for a in find_sequences(qr, E("B", "C", "A"))) == 1
    # intervening zones
    qn = Sequence("AB", ["A", "B"], allow_other=False)
    assert sum(a.completed for a in find_sequences(qn, E("A", "X", "B"), others=["X"])) == 0
    assert sum(a.completed for a in find_sequences(Sequence("AB", ["A", "B"]), E("A", "X", "B"), ["X"])) == 1
    # time limit and end on exit
    slow = [("A", 0.0, 1.0), ("B", 10.0, 12.0)]
    assert not find_sequences(Sequence("AB", ["A", "B"], max_duration_s=5), slow)[0].completed
    assert find_sequences(Sequence("AB", ["A", "B"], end="exit"), slow)[0].end == 12.0
    # overlapping: A B A B with pattern A B A → 1 without overlap
    qo = Sequence("ABA", ["A", "B", "A"], overlap=True)
    assert sum(a.completed for a in find_sequences(qo, E("A", "B", "A", "B", "A"))) == 2
    assert sum(a.completed for a in find_sequences(Sequence("ABA", ["A", "B", "A"]), E("A", "B", "A", "B", "A"))) == 1


def test_sequence_measures_in_analysis_y_maze():
    app = templates.build("y_maze", 0, 0, 300, 300)
    app.sequences.append(Sequence("Alternation", ["Arm A", "Arm B", "Arm C"], from_start=False, overlap=True))
    c = app.zone("Centre").shape.centroid()
    arms = [app.zone(n).shape.centroid() for n in ("Arm A", "Arm B", "Arm C")]
    pts = [hold(c, 5)]
    for a in [0, 1, 2, 0]:
        pts += [line(c, arms[a], 20), line(arms[a], c, 20)]
    tr = make_track(np.vstack(pts), head=False)
    res = analyse(tr, app, AnalysisSettings())
    assert res["Spontaneous alternations"] == 2
    assert res["Alternation: completed"] == 2
    assert res["Alternation: errors"] == 0


# ------------------------------------------------------------------ periods / pauses
def test_event_anchored_periods():
    app = box_app()
    app.zones.append(Zone("Start", rect(0, 0, 100, 400)))
    pts = np.vstack([hold((50, 200), 50), line((50, 200), (350, 200), 50), hold((350, 200), 150)])
    tr = make_track(pts, head=False)
    s = AnalysisSettings(event_periods=[
        {"label": "2 s after leaving start", "anchor": "first_exit", "zone": "Start", "duration_s": 2},
        {"label": "After mark", "anchor": "mark", "behaviour": "Tone", "offset_s": 1, "duration_s": 0},
        {"label": "Input", "anchor": "input", "channel": "lever", "duration_s": 1, "occurrence": 0},
        {"label": "Never", "anchor": "first_entry", "zone": "Nowhere"},
    ])
    events = [{"behaviour": "Tone", "t": 5.0, "t_end": None}]
    io = [{"t": 1.0, "device": "v", "channel": "lever", "kind": "input", "value": 1},
          {"t": 1.5, "device": "v", "channel": "lever", "kind": "input", "value": 0},
          {"t": 3.0, "device": "v", "channel": "lever", "kind": "input", "value": 1}]
    per = all_periods(tr, app, s, events=events, io_events=io)
    lab = {p[0]: (p[1], p[2]) for p in per}
    a, b = lab["2 s after leaving start"]
    assert a == pytest.approx(2.36, abs=0.05) and b - a == pytest.approx(2.0)
    assert lab["After mark"] == (6.0, pytest.approx(10.0))
    assert lab["Input #1"] == (1.0, 2.0) and lab["Input #2"] == (3.0, 4.0)
    assert "Never" not in lab
    seg = analyse_segmented(tr, app, s, events=events, io_events=io)
    assert [x[0] for x in seg][:2] == ["Whole test", "2 s after leaving start"]
    assert dict(seg)["2 s after leaving start"]["Test duration (s)"] == pytest.approx(2.0)
    assert event_periods([{"anchor": "start", "offset_s": 2, "duration_s": 3}], 10)[0] == ("Period 1", 2.0, 5.0)


def test_pauses_are_excluded():
    app = box_app()
    pts = np.vstack([line((50, 200), (150, 200), 50), line((150, 200), (350, 200), 50), hold((350, 200), 50)])
    tr = make_track(pts, head=False)
    full = analyse(tr, app, S)
    res = analyse(tr, app, S, pauses=[[2.0, 4.0]])
    assert full["Test duration (s)"] == pytest.approx(6.0)
    assert res["Test duration (s)"] == pytest.approx(4.0)
    assert res["Total distance (cm)"] == pytest.approx(10.0, abs=0.3)


# ------------------------------------------------------------------ apparatus measures
def test_apparatus_measures_stable_names_and_new():
    app = templates.build("open_field", 0, 0, 400, 400)
    rng = np.random.default_rng(1)
    pts = np.cumsum(rng.normal(0, 3, (500, 2)), axis=0) + 200
    pts = np.clip(pts, 5, 395)
    res = analyse(make_track(pts, motion=rng.uniform(0, 30, 500)), app, AnalysisSettings(arena_quadrants=True))
    for key in ["Test duration (s)", "Detection (%)", "Total distance (cm)", "Mean speed (cm/s)", "Max speed (cm/s)",
                "Time mobile (s)", "Time immobile (s)", "Immobile episodes", "Latency to first immobility (s)",
                "Time freezing (s)", "Freezing episodes", "Path efficiency", "Absolute turn angle (deg)",
                "Rotations clockwise", "Thigmotaxis (%)", "Centre: time (s)", "Centre: entries",
                "Centre: latency to first entry (s)", "Centre: head entries", "Grid crossings (4×4)",
                "Inner grid squares time (%)", "Periphery: time (%)",
                # new
                "Time not detected (s)", "Mobile episodes", "Path tortuosity", "Mean distance from centre (cm)",
                "Arena quadrant NE: time (%)", "Centre: latency to second entry (s)", "Centre: longest visit (s)",
                "Centre: entries (/min)", "Centre: time mobile (s)", "Centre: immobile episodes",
                "Centre: time facing (s)", "Zone transitions", "Path rotations clockwise"]:
        assert key in res, key
    q = sum(res[f"Arena quadrant {q}: time (%)"] for q in ("NE", "SE", "SW", "NW"))
    assert q == pytest.approx(100, abs=0.1)
    zone_measures = [k for k in res if k.startswith("Centre: ")]
    assert len(zone_measures) >= 20


def test_whishaw_corridor():
    app = templates.build("water_maze", 0, 0, 300, 300)
    pc = app.point("Platform centre")
    straight = np.vstack([line((150, 295), (pc.x, pc.y), 60), hold((pc.x, pc.y), 10)])
    res = analyse(make_track(straight, head=False), app, AnalysisSettings())
    assert res["Whishaw corridor time (%)"] == 100 and res["Left Whishaw corridor"] == "No"
    detour = np.vstack([line((150, 295), (20, 150), 60), line((20, 150), (pc.x, pc.y), 60), hold((pc.x, pc.y), 5)])
    res2 = analyse(make_track(detour, head=False), app, AnalysisSettings())
    assert res2["Whishaw corridor time (%)"] < 50 and res2["Left Whishaw corridor"] == "Yes"


# ------------------------------------------------------------------ templates
def test_new_templates():
    for key in ("novel_tank", "multi_well", "cpp", "hole_board", "thermal_gradient", "home_cage", "activity_wheel"):
        assert key in templates.TEMPLATES
    tank = templates.build("novel_tank", 0, 0, 300, 150)
    assert [z.name for z in tank.zones] == ["Top", "Middle", "Bottom"]
    plates = templates.build_many("multi_well", 0, 0, 400, 270, n_wells=24)
    assert len(plates) == 24 and plates[0].name == "Well A1" and plates[-1].name == "Well D6"
    assert all(p.arena is not None and p.group("Edge") for p in plates)
    assert len(templates.build_many("multi_well", 0, 0, 400, 270, n_wells=96)) == 96
    assert len(templates.build_many("open_field", 0, 0, 100, 100)) == 1
    assert [z.name for z in templates.build("cpp", 0, 0, 300, 100, n_chambers=3).zones] == \
        ["Chamber A", "Centre", "Chamber B"]
    assert len(templates.build("hole_board", 0, 0, 300, 300).points) == 16
    assert len(templates.build("thermal_gradient", 0, 0, 300, 300, n_sectors=8).zones) == 8
    assert templates.build("home_cage", 0, 0, 300, 200).zone("Nest").hidden


@pytest.mark.parametrize("key", list(templates.TEMPLATES))
def test_every_template_analyses(key):
    app = templates.build_many(key, 0, 0, 300, 300)[0]
    x0, y0, x1, y1 = app.arena_or_bounds().bounds()
    rng = np.random.default_rng(3)
    pts = np.cumsum(rng.normal(0, 2, (250, 2)), axis=0) + ((x0 + x1) / 2, (y0 + y1) / 2)
    pts = np.clip(pts, (x0 + 1, y0 + 1), (x1 - 1, y1 - 1))
    res = analyse(make_track(pts, motion=rng.uniform(0, 20, 250)), app, AnalysisSettings())
    assert res["Test duration (s)"] == pytest.approx(10.0)


def test_template_specific_measures():
    tank = templates.build("novel_tank", 0, 0, 300, 150)
    pts = np.vstack([hold((150, 140), 50), line((150, 140), (150, 10), 25), hold((150, 10), 25)])
    res = analyse(make_track(pts, head=False), tank, AnalysisSettings())
    assert res["Latency to top (s)"] == pytest.approx(2.0 + 25 * 0.66 / FPS, abs=0.2)
    assert res["Top entries"] == 1 and res["Time in bottom (%)"] > 50
    cpp = templates.build("cpp", 0, 0, 300, 100)
    pts = np.vstack([hold((50, 50), 75), line((50, 50), (250, 50), 25), hold((250, 50), 25)])
    res = analyse(make_track(pts, head=False), cpp, AnalysisSettings(paired_chamber="Chamber B"))
    assert res["CPP score (s)"] < 0 and res["Chamber transitions"] == 1
    hb = templates.build("hole_board", 0, 0, 300, 300)
    h1, h2 = hb.points[0], hb.points[5]
    pts = np.vstack([hold((h1.x, h1.y), 25), line((h1.x, h1.y), (h2.x, h2.y), 25), hold((h2.x, h2.y), 25),
                     line((h2.x, h2.y), (h1.x, h1.y), 25), hold((h1.x, h1.y), 25)])
    res = analyse(make_track(pts, head=False), hb, AnalysisSettings())
    assert res["Head dips"] == 3 and res["Holes explored"] == 2 and res["Repeated head dips"] == 1
    wheel = templates.build("activity_wheel", 0, 0, 300, 300)
    ang = np.linspace(0, 6.4 * np.pi, 300)
    pts = np.column_stack([150 + 100 * np.cos(ang), 150 + 100 * np.sin(ang)])
    res = analyse(make_track(pts, head=False), wheel, AnalysisSettings())
    assert res["Revolutions clockwise"] == 3
    ring = templates.build("thermal_gradient", 0, 0, 300, 300, n_sectors=4)
    res = analyse(make_track(hold(ring.zone("Sector 2").shape.centroid(), 50), head=False), ring, AnalysisSettings())
    assert res["Preferred sector"] == "Sector 2"


# ------------------------------------------------------------------ social / behaviours / I/O
def test_social_contacts_following_approaches():
    app = box_app()
    # animal 1 walks to animal 2 (stationary, facing it) → nose to nose; then both walk right, 1 following 2
    a2 = np.vstack([hold((300, 200), 75), line((300, 200), (380, 200), 50)])
    a1 = np.vstack([line((100, 200), (270, 200), 75), line((270, 200), (350, 200), 50)])
    t1 = make_track(a1, body_len=20)
    t2 = make_track(a2, body_len=20, angle=np.r_[np.full(75, 180.0), np.zeros(50)])
    t2.meta["animal_index"] = 2
    s = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0,
                         nose_contact_distance=1.2, contact_distance=4.0)
    res = analyse(t1, app, s, other_tracks=[t2])
    assert res["Animal 2: nose-to-nose contacts"] == 1
    assert res["Animal 2: approaches"] == 1 and res["Animal 2: approached by"] == 0
    assert res["Animal 2: time following (s)"] > 1.0
    assert res["Animal 2: min distance (cm)"] == pytest.approx(3.0, abs=0.2)


def test_behaviours_per_zone_and_hold_keys():
    beh = [Behaviour("Groom", "g", "hold"), Behaviour("Rear", "r", "point")]
    ev = [{"behaviour": "Groom", "t": 1.0, "t_end": 3.0}, {"behaviour": "Groom", "t": 6.0, "t_end": 7.0},
          {"behaviour": "Rear", "t": 2.0}, {"behaviour": "Rear", "t": 8.0}]
    t = np.arange(0, 10, 0.04)
    zone = {"Centre": t < 5}
    m = behaviour_measures(ev, beh, 0.0, 10.0, zones=zone, t=t, dur=np.full(len(t), 0.04))
    assert m["Groom: duration (s)"] == 3.0 and m["Groom: longest bout (s)"] == 2.0 and m["Groom: rate (/min)"] == 12
    assert m["Groom in Centre: count"] == 1 and m["Groom in Centre: duration (s)"] == pytest.approx(2.0, abs=0.05)
    assert m["Rear in Centre: count"] == 1 and m["Rear in Centre: latency (s)"] == 2.0


def test_result_variables_and_io_measures(monkeypatch):
    import manymaze.core.measures as measures

    calls = []

    def fake(io_events, duration, t_range, devices=None, **kw):
        calls.append((len(io_events), duration, t_range))
        return {"lever: presses": 2}

    monkeypatch.setattr(measures, "io_measures", fake)
    app = box_app()
    tr = make_track(line((50, 50), (300, 300), 100), head=False)
    io = [{"t": 1.0, "device": "v", "channel": "lever", "kind": "input", "value": 1}]
    res = analyse(tr, app, S, io_events=io, result_variables={"Rewards": 3, "Label": "x"})
    assert res["lever: presses"] == 2 and calls[0][1] == pytest.approx(4.0)
    assert res["Variable: Rewards"] == 3.0 and res["Variable: Label"] == "x"
    res = analyse(tr, app, S)
    assert "lever: presses" not in res and len(calls) == 1


# ------------------------------------------------------------------ forced swim / tail suspension
FST = AnalysisSettings(immobility_mode="motion", speed_smoothing_s=0.2, mobility_threshold=2.0)
AREA = 400.0  # px², so a motion of 4 px is 1 % of the body


def _fst_motion(kind: str, seconds: float, rng) -> np.ndarray:
    """Motion index (% of the body) of an animal that floats ("float": camera and water noise, and every 1.5 s a
    0.2 s paddle that keeps its head above water), struggles ("swim": strong, irregular; "climb": stronger) or
    swings on its tail ("swing": large but smooth, the pixel change of a pendulum at 1 Hz)."""
    n = int(round(seconds * FPS))
    t = np.arange(n) / FPS
    noise = np.abs(rng.normal(0, 0.4, n))
    if kind == "float":
        m = noise.copy()
        for k in range(0, n, int(1.5 * FPS)):
            m[k:k + 5] += [2, 5, 6, 4, 2][:len(m[k:k + 5])]
        return m
    if kind == "swing":
        return 15 * np.abs(np.sin(2 * np.pi * t)) + noise
    level, spread = (15, 8) if kind == "swim" else (35, 15)
    return np.clip(level + spread * rng.normal(0, 1, n), 0, None)


def _fst_track(parts, app, drift=True, seed=3):
    """A track made of (kind, seconds) parts; the animal drifts 6 cm across the cylinder (or stays put)."""
    rng = np.random.default_rng(seed)
    motion = np.concatenate([_fst_motion(k, s, rng) for k, s in parts])
    n = len(motion)
    x0, y0, x1, y1 = app.arena_or_bounds().bounds()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pts = line((cx - 30, cy), (cx + 30, cy), n) if drift else hold((cx, cy), n)
    return make_track(pts, head=False, area=np.full(n, AREA), motion=motion * AREA / 100)


def test_forced_swim_immobility_from_the_struggle():
    app = templates.build("forced_swim", 0, 0, 200, 200)  # 10 px/cm: the drift is 60 px in the test
    # floating and drifting, paddling now and then: immobile all along although its centre moves (speed above the
    # mobility threshold below) and its motion keeps rising above the freezing threshold
    tr = _fst_track([("float", 20)], app)
    res = analyse(tr, app, FST)
    assert res["Time immobile (s)"] == pytest.approx(20.0, abs=0.05) and res["Immobile episodes"] == 1
    assert res["Time immobile (%)"] == pytest.approx(100.0, abs=0.3) and res["Latency to first immobility (s)"] == 0
    assert "Immobility (s)" not in res and "Time climbing (s)" not in res  # no renamed freezing, no split
    speed = analyse(tr, app, AnalysisSettings(speed_smoothing_s=0.2, mobility_threshold=0.1))
    assert speed["Time immobile (s)"] < 1  # the speed-based immobility sees a moving animal
    # a swimming animal is mobile wherever it is
    res = analyse(_fst_track([("swim", 20)], app, drift=False), app, FST)
    assert res["Time immobile (s)"] == 0 and res["Immobile episodes"] == 0 and res["Time mobile (s)"] > 19.9
    # struggle, float, struggle, float: two immobile episodes, the first after 10 s
    tr = _fst_track([("swim", 10), ("float", 10), ("climb", 5), ("float", 10)], app)
    res = analyse(tr, app, FST)
    assert res["Immobile episodes"] == 2
    assert res["Latency to first immobility (s)"] == pytest.approx(10.0, abs=0.6)
    assert res["Time immobile (s)"] == pytest.approx(20.0, abs=1.2)
    assert res["Latency to last immobile episode (s)"] == pytest.approx(25.0, abs=0.6)
    # the optional three-state split: climbing (the most vigorous struggle) and swimming
    res = analyse(tr, app, AnalysisSettings(**{**FST.to_dict(), "fst_three_state": True}))
    # (the second around a change of state, where the index passes through the swimming range, may go either way)
    assert res["Time climbing (s)"] == pytest.approx(5.0, abs=1.5) and res["Climbing episodes"] == 1
    assert res["Time swimming (s)"] == pytest.approx(10.0, abs=1.5)
    assert res["Latency to first climbing (s)"] == pytest.approx(20.0, abs=0.8)
    assert res["Latency to first swimming (s)"] == 0
    total = res["Time climbing (s)"] + res["Time swimming (s)"] + res["Time immobile (s)"]
    assert total == pytest.approx(res["Test duration (s)"], abs=0.05)
    assert res["Time climbing (%)"] == pytest.approx(100 * res["Time climbing (s)"] / 35, abs=0.05)
    # per time period: the split of each bin
    rows = dict(analyse_segmented(tr, app, AnalysisSettings(**{**FST.to_dict(), "fst_three_state": True,
                                                                 "bin_length_s": 15.0})))
    assert rows["0-15 s"]["Time immobile (s)"] == pytest.approx(5.0, abs=0.6) and rows["0-15 s"]["Climbing episodes"] == 0
    assert rows["15-30 s"]["Time climbing (s)"] == pytest.approx(5.0, abs=1.5)
    assert rows["15-30 s"]["Climbing episodes"] == 1 and rows["30-35 s"]["Time climbing (s)"] == 0


def test_tail_suspension_ignores_swinging():
    app = templates.build("tail_suspension", 0, 0, 200, 300)
    assert app.template == "tail_suspension" and templates.TEMPLATES["tail_suspension"].default_duration_s == 360
    tr = _fst_track([("swing", 10), ("swim", 5), ("swing", 10)], app, drift=False)
    res = analyse(tr, app, FST)
    assert res["Immobile episodes"] == 2 and res["Time immobile (s)"] == pytest.approx(20.0, abs=1.2)
    split = analyse(tr, app, AnalysisSettings(**{**FST.to_dict(), "fst_three_state": True}))
    assert "Time climbing (s)" not in split and "Warnings" not in split  # the forced swim test only
    # without pixel-change data the motion mode cannot work: immobility from the speed, with a warning
    res = analyse(make_track(hold((100, 150), 100), head=False), app, FST)
    assert res["Time immobile (s)"] == pytest.approx(4.0) and "No pixel-change data" in res["Warnings"]


def test_forced_swim_protocol_and_live_immobility():
    from manymaze.core.live import LiveStats
    from manymaze.core.project import Project
    from manymaze.core.tracking import Detection

    # the forced swim / tail suspension types of test detect immobility from the struggle …
    p = Project()
    assert p.analysis.immobility_mode == "speed"
    p.set_protocol("tail_suspension")
    assert p.protocol == "tail_suspension" and p.analysis.immobility_mode == "motion"
    p.set_protocol("forced_swim")
    assert p.analysis.immobility_mode == "motion"
    p.set_protocol("open_field")  # … and leaving them goes back to the speed
    assert p.analysis.immobility_mode == "speed"
    p.analysis.immobility_mode = "motion"  # chosen by hand for another test: kept
    p.set_protocol("epm")
    assert p.analysis.immobility_mode == "motion"
    # experiments saved before the motion mode: forced swim ones get it, an explicit choice is kept
    d = Project(protocol="forced_swim").to_dict()
    del d["analysis"]["immobility_mode"]
    assert Project.from_dict(d).analysis.immobility_mode == "motion"
    d["analysis"]["immobility_mode"] = "speed"
    assert Project.from_dict(d).analysis.immobility_mode == "speed"
    d = Project(protocol="open_field").to_dict()
    del d["analysis"]["immobility_mode"]
    assert Project.from_dict(d).analysis.immobility_mode == "speed"
    # live: immobile once the struggle has stopped for the shortest immobile period, wherever the animal goes
    app = templates.build("forced_swim", 0, 0, 200, 200)
    st = LiveStats(app, FPS, FST)
    rng = np.random.default_rng(5)
    motion = np.concatenate([_fst_motion("swim", 4, rng), _fst_motion("float", 4, rng)])
    states = []
    for i, m in enumerate(motion):
        x = 70 + 0.6 * i  # drifting 1.5 cm/s
        st.update(i / FPS, Detection(x, 100, area=AREA, motion=m * AREA / 100, detected=True), {}, False)
        states.append(st.immobile)
    assert not any(states[:100]) and all(states[-25:])
    first = states.index(True) / FPS
    assert 4.5 < first < 6.5  # the struggle stops at 4 s, then 1 s without struggle (trailing windows)
    speed = LiveStats(app, FPS, AnalysisSettings(mobility_threshold=1.0, min_immobile_s=1.0))
    for i, m in enumerate(motion):
        speed.update(i / FPS, Detection(70 + 0.6 * i, 100, area=AREA, motion=m * AREA / 100, detected=True), {}, False)
    assert not speed.immobile  # the speed-based immobility sees the drift
