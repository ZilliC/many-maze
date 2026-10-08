"""More ANY-maze zone measures: investigation, visit / investigation lists, head and border distances, heading
towards a zone, turning in a zone, CIPL, line crossings in a zone, hidden-zone partial exits, Whishaw corridor
distance, and zone entries that require the animal to face the zone."""

import math

import numpy as np
import pytest

from manymaze.core import templates
from manymaze.core.apparatus import Apparatus, Line, Zone
from manymaze.core.geometry import circle, rect
from manymaze.core.live import LiveOccupancy
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented
from manymaze.core.occupancy import occupancy
from manymaze.core.stats import numeric_columns
from manymaze.core.track import Track
from manymaze.core.tracking import Detection

FPS = 25.0
S = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0)


def make_track(points, fps=FPS, head=True, body_len=20.0, angle=None, **kw):
    """Track from centre positions; head / tail along `angle` (deg) or else the direction of travel."""
    p = np.asarray(points, float)
    n = len(p)
    t = np.arange(n) / fps
    if head:
        if angle is None:
            d = np.gradient(p, axis=0) if n > 1 else np.zeros_like(p)
            angle = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
            still = np.hypot(d[:, 0], d[:, 1]) < 1e-9
            for i in range(1, n):
                if still[i]:
                    angle[i] = angle[i - 1]
        angle = np.asarray(angle, float)
        a = np.radians(angle)
        kw.update(angle=angle, hx=p[:, 0] + np.cos(a) * body_len / 2, hy=p[:, 1] + np.sin(a) * body_len / 2,
                  tx=p[:, 0] - np.cos(a) * body_len / 2, ty=p[:, 1] - np.sin(a) * body_len / 2)
    kw.setdefault("area", np.full(n, body_len * body_len / 3))
    return Track(t=t, x=p[:, 0], y=p[:, 1], fps=fps, **kw)


def line(p0, p1, n):
    return np.column_stack([np.linspace(p0[0], p1[0], n), np.linspace(p0[1], p1[1], n)])


def hold(p, n):
    return np.tile(np.asarray(p, float), (n, 1))


def box_app(*zones, **kw) -> Apparatus:
    app = Apparatus(name="Box", arena=rect(0, 0, 400, 400), px_per_cm=10.0, **kw)
    app.zones.append(Zone("Arena", rect(0, 0, 400, 400)))
    app.zones.extend(zones)
    return app


def nums(text):
    return [float(v) for v in text.split(", ")] if text else []


# ------------------------------------------------------------------ lists


def test_visit_duration_list_and_visited_zones():
    app = box_app(Zone("A", rect(0, 0, 100, 100)), Zone("B", rect(300, 300, 100, 100)))
    pts = np.vstack([hold((200, 200), 25), line((200, 200), (50, 50), 25), hold((50, 50), 25),
                     line((50, 50), (200, 200), 25), line((200, 200), (50, 50), 25), hold((50, 50), 50),
                     line((50, 50), (350, 350), 50), hold((350, 350), 25)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["Visited zones"] == "Arena, A, B"
    a = nums(res["A: visit durations (s)"])
    assert len(a) == res["A: entries"] == 2
    assert sum(a) == pytest.approx(res["A: time (s)"], abs=1e-6)
    assert max(a) == pytest.approx(res["A: longest visit (s)"], abs=1e-3)
    b = res["B: visit durations (s)"]
    assert isinstance(b, str) and len(nums(b)) == 1  # a one-element list is still text
    # list columns are text: never offered as numeric measures for statistics
    cols = numeric_columns([res], list(res))
    assert "A: visit durations (s)" not in cols and "B: visit durations (s)" not in cols
    assert "Visited zones" not in cols and "A: entries" in cols
    # never entered: empty list
    res2 = analyse(make_track(hold((200, 200), 50), head=False), app, S)
    assert res2["A: visit durations (s)"] == "" and res2["Visited zones"] == "Arena"


# ------------------------------------------------------------------ investigation


def _investigation_track():
    """Approach the object facing it (2 s still), stand beside it with the head near but pointing elsewhere (2 s),
    walk away, come back and face it again (1 s)."""
    pts = [line((60, 200), (145, 200), 30), hold((145, 200), 50),  # facing: head at 165, 15 px from the edge
           hold((170, 225), 50),  # beside: head at (190, 225), 7 px from the edge, pointing past the object
           line((170, 225), (60, 300), 25), line((60, 300), (60, 200), 25), line((60, 200), (145, 200), 20),
           hold((145, 200), 25)]
    p = np.vstack(pts)
    ang = np.zeros(len(p))
    ang[155:205] = np.degrees(np.arctan2(np.diff(p[154:205, 1]), np.diff(p[154:205, 0])))
    return make_track(p, body_len=40, angle=ang)


def test_investigation_measures():
    obj = Zone("Object", circle(200, 200, 20), investigation_distance_cm=2.0)
    other = Zone("Other", circle(350, 50, 10), investigation_distance_cm=2.0)
    app = box_app(obj, other)
    tr = _investigation_track()
    res = analyse(tr, app, S)
    # head 15 px from the edge, facing the object: investigating; beside it with the head 7 px from the edge but
    # pointing past it: in the (investigation) zone, not investigating
    assert res["Object: investigation bouts"] == 2
    t_inv = res["Object: time investigating (s)"]
    assert 3.0 <= t_inv <= 3.3
    assert res["Object: time (s)"] >= t_inv + 1.9
    d = nums(res["Object: investigation durations (s)"])
    assert len(d) == 2 and sum(d) == pytest.approx(t_inv, abs=1e-3)
    assert res["Object: longest investigation bout (s)"] == pytest.approx(max(d), abs=1e-3)
    assert res["Object: shortest investigation bout (s)"] == pytest.approx(min(d), abs=1e-3)
    assert res["Object: mean investigation bout (s)"] == pytest.approx(t_inv / 2, abs=1e-3)
    assert 1.0 <= res["Object: latency to first investigation (s)"] <= 1.2
    assert res["Object: latency to end of first investigation (s)"] == pytest.approx(80 / FPS, abs=0.05)
    assert res["Object: was first zone investigated"] == "Yes" and res["Other: was first zone investigated"] == "No"
    assert res["Investigated zones"] == "Object"
    assert res["Other: investigation bouts"] == 0
    assert res["Other: latency to first investigation (s)"] == res["Test duration (s)"]
    assert res["Other: distance before first investigation (cm)"] == res["Total distance (cm)"]
    # the animal stands still while investigating, apart from the last approach steps
    assert res["Object: time immobile while investigating (s)"] > 2.0
    assert res["Object: time mobile while investigating (s)"] + res["Object: time immobile while investigating (s)"] \
        == pytest.approx(t_inv, abs=1e-3)
    assert res["Object: immobile episodes while investigating"] == 2
    assert res["Object: distance before first investigation (cm)"] == pytest.approx(8.0, abs=0.5)
    # last approach steps: 2 x 85/29 px and 2 x 85/19 px
    assert res["Object: distance while investigating (cm)"] == pytest.approx((2 * 85 / 29 + 2 * 85 / 19) / 10, abs=0.05)
    # time bins: times add up, bouts are counted where they start
    s = AnalysisSettings(**{**S.to_dict(), "bin_length_s": 4.0})
    seg = analyse_segmented(tr, app, s)
    assert sum(r["Object: time investigating (s)"] for _, r in seg[1:]) == pytest.approx(t_inv, abs=1e-3)
    assert sum(r["Object: investigation bouts"] for _, r in seg[1:]) == 2
    # without a body orientation, investigating = head within the distance (the zone's occupancy)
    tr2 = make_track(np.vstack([line((60, 200), (165, 200), 30), hold((165, 200), 50)]), head=False)
    res2 = analyse(tr2, app, S)
    assert res2["Object: time (s)"] > 1.9 and res2["Object: time investigating (s)"] == res2["Object: time (s)"]


# ------------------------------------------------------------------ head


def test_head_measures():
    z = Zone("Z", rect(150, 150, 100, 100))
    app = box_app(z)
    # centre stops 5 px outside the zone with the head 5 px inside (2 s), then backs out
    pts = np.vstack([line((60, 200), (145, 200), 30), hold((145, 200), 50), line((145, 200), (60, 200), 30)])
    ang = np.zeros(len(pts))
    res = analyse(make_track(pts, body_len=20, angle=ang), app, S)
    t_head_out = res["Z: latency to first head exit (s)"]
    assert t_head_out == pytest.approx((80 + 2) / FPS, abs=0.1)  # head leaves 5 px after the centre starts back
    assert res["Z: head time (s)"] == pytest.approx(res["Z: time head in zone with centre outside (s)"], abs=1e-6)
    assert res["Z: time head in zone with centre outside (s)"] >= 2.0
    # steps into frames with the head in the zone: 2 on the way in, 1 on the way out (85/29 px each)
    assert res["Z: head distance (cm)"] == pytest.approx(3 * 85 / 29 / 10, abs=0.02)
    assert res["Z: min head distance from zone when outside (cm)"] < 0.5
    assert res["Z: max head distance from zone (cm)"] == pytest.approx(8.0, abs=0.01)  # head 80 px away
    assert res["Z: mean head distance to border when inside (cm)"] <= 0.5
    # inside, the centre and head distances to the border
    big = Zone("Big", rect(100, 100, 200, 200))
    res = analyse(make_track(hold((200, 200), 50), body_len=20, angle=np.zeros(50)), box_app(big), S)
    assert res["Big: mean distance to border when inside (cm)"] == pytest.approx(10.0)
    assert res["Big: max distance to border when inside (cm)"] == pytest.approx(10.0)
    assert res["Big: min distance to border when inside (cm)"] == pytest.approx(10.0)
    assert res["Big: mean head distance to border when inside (cm)"] == pytest.approx(9.0)
    assert res["Big: mean head distance from zone (cm)"] == 0
    assert math.isnan(res["Big: max head distance from zone (cm)"])
    assert res["Big: latency to first head exit (s)"] == res["Test duration (s)"]


# ------------------------------------------------------------------ towards / away, heading, orientation


def test_towards_away_and_heading_errors():
    app = box_app(Zone("B", rect(300, 150, 50, 100)))
    pts = np.vstack([line((50, 200), (250, 200), 50), line((250, 200), (50, 200), 50)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["B: time getting closer (s)"] == pytest.approx(49 / FPS, abs=0.05)
    assert res["B: time getting further away (s)"] == pytest.approx(49 / FPS, abs=0.05)
    assert res["B: time moving towards (s)"] == pytest.approx(49 / FPS, abs=0.1)
    assert res["B: time moving away (s)"] == pytest.approx(49 / FPS, abs=0.1)
    assert res["B: initial heading error (deg)"] == 0 and res["B: signed initial heading error (deg)"] == 0
    assert res["B: mean absolute heading error (deg)"] == pytest.approx(90, abs=3)
    # setting off 45 deg clockwise (on screen) of the zone direction: the zone is to the animal's left
    pts2 = line((50, 200), (150, 300), 50)
    res2 = analyse(make_track(pts2, head=False), app, S)
    assert res2["B: signed initial heading error (deg)"] == pytest.approx(-45, abs=0.5)  # zone to its left
    assert res2["B: initial heading error (deg)"] == pytest.approx(45, abs=0.5)
    pts3 = line((50, 200), (150, 100), 50)
    res3 = analyse(make_track(pts3, head=False), app, S)
    assert res3["B: signed initial heading error (deg)"] == pytest.approx(45, abs=0.5)  # zone to its right
    assert res3["B: initial heading error (deg)"] == pytest.approx(45, abs=0.5)  # ANY-maze: the absolute angle


def test_time_oriented_towards_centre_when_inside():
    app = box_app(Zone("Big", rect(100, 100, 200, 200)))
    pts = hold((150, 200), 100)
    ang = np.r_[np.zeros(50), np.full(50, 180.0)]  # facing the zone centre, then away from it
    res = analyse(make_track(pts, angle=ang), app, S)
    assert res["Big: time oriented towards zone centre when inside (s)"] == pytest.approx(2.0, abs=0.05)


def test_turn_angles_in_zone():
    z = Zone("Left", rect(0, 0, 200, 400))
    app = box_app(z)
    sq = np.vstack([line((50, 50), (150, 50), 25), line((150, 50), (150, 150), 25), line((150, 150), (50, 150), 25),
                    line((50, 150), (50, 300), 25), line((50, 300), (300, 300), 50), line((300, 300), (300, 50), 50)])
    res = analyse(make_track(sq, head=False), app, S)
    # the whole path is in the arena: the arena's turn angle is the whole-test one
    assert res["Arena: absolute turn angle (deg)"] == res["Absolute turn angle (deg)"] == pytest.approx(450, abs=1)
    assert res["Left: absolute turn angle (deg)"] == pytest.approx(360, abs=1)  # four of the five corners in "Left"
    # turning on the spot in the zone: the head (body orientation) turns a full circle
    ang = np.linspace(0, 359, 100)
    res2 = analyse(make_track(hold((100, 200), 100), angle=ang), app, S)
    assert res2["Left: absolute head turn angle (deg)"] == pytest.approx(359, abs=1)
    assert res2["Left: absolute turn angle (deg)"] == 0


# ------------------------------------------------------------------ CIPL, lines, hidden zones, Whishaw


def test_cipl_straight_vs_detour():
    app = box_app(Zone("Goal", circle(350, 200, 20)))
    straight = np.vstack([line((50, 200), (330, 200), 250), hold((340, 200), 10)])
    detour = np.vstack([line((50, 200), (50, 50), 125), line((50, 50), (330, 200), 125), hold((340, 200), 10)])
    c1 = analyse(make_track(straight, head=False), app, S)["Goal: corrected integrated path length (cm·s)"]
    c2 = analyse(make_track(detour, head=False), app, S)["Goal: corrected integrated path length (cm·s)"]
    assert abs(c1) < 3.0  # sampled once a second, a straight swim at constant speed is (almost) ideal
    assert c2 > 20 + c1
    # never reaching the zone: still defined; never moving: not
    assert math.isfinite(analyse(make_track(line((50, 200), (50, 50), 100), head=False), app, S)
                         ["Goal: corrected integrated path length (cm·s)"])
    assert math.isnan(analyse(make_track(hold((50, 200), 100), head=False), app, S)
                      ["Goal: corrected integrated path length (cm·s)"])


def test_line_crossings_in_zone():
    app = box_app(Zone("Mid", rect(150, 0, 100, 400)), Zone("Left", rect(0, 0, 150, 400)))
    app.lines.append(Line("L", 200, 0, 200, 400))
    pts = np.vstack([line((50, 200), (180, 200), 20), line((180, 200), (220, 200), 10),
                     line((220, 200), (180, 200), 10), line((180, 200), (220, 200), 10)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["L: crossings"] == 3
    assert res["Mid: line crossings"] == 3 and res["Left: line crossings"] == 0
    assert res["Arena: line crossings"] == 3
    # no lines, no measure
    assert "Mid: line crossings" not in analyse(make_track(pts, head=False), box_app(Zone("Mid", rect(150, 0, 100,
                                                                                                    400))), S)


def test_hidden_zone_partial_exits():
    app = box_app(Zone("Nest", rect(0, 0, 80, 80), hidden=True))
    pts = np.vstack([line((300, 300), (60, 60), 50), hold((60, 60), 50), hold((70, 70), 20), hold((60, 60), 50),
                     line((60, 60), (300, 300), 50)])
    det = np.ones(len(pts), bool)
    det[50:100] = False
    det[120:170] = False
    res = analyse(make_track(pts, head=False, detected=det), app, S)
    assert res["Nest: entries"] == 1
    assert res["Nest: partial exits"] == 1
    assert res["Nest: time partially exited (s)"] == pytest.approx(20 / FPS, abs=0.01)
    # peeking out further than the hidden-zone distance is a real exit
    pts2 = pts.copy()
    pts2[100:120] = (250, 250)
    res2 = analyse(make_track(pts2, head=False, detected=det), app, S)
    assert res2["Nest: partial exits"] == 0 and res2["Nest: time partially exited (s)"] == 0


def test_whishaw_corridor_distance():
    app = templates.build("water_maze", 0, 0, 300, 300)
    pc = app.point("Platform centre")
    straight = np.vstack([line((150, 295), (pc.x, pc.y), 60), hold((pc.x, pc.y), 10)])
    res = analyse(make_track(straight, head=False), app, AnalysisSettings())
    u = app.unit
    assert res[f"Whishaw corridor distance ({u})"] == pytest.approx(res[f"Path length to platform ({u})"], rel=0.02)
    detour = np.vstack([line((150, 295), (20, 150), 60), line((20, 150), (pc.x, pc.y), 60), hold((pc.x, pc.y), 5)])
    res2 = analyse(make_track(detour, head=False), app, AnalysisSettings())
    assert 0 < res2[f"Whishaw corridor distance ({u})"] < 0.5 * res2[f"Path length to platform ({u})"]


# ------------------------------------------------------------------ entries that require facing the zone


def test_entry_requires_orientation():
    z = Zone("Z", rect(200, 150, 100, 100), entry_orientation_deg=45.0)
    app = box_app(z)
    pts = np.vstack([line((60, 200), (250, 200), 60), hold((250, 200), 40)])
    fwd = make_track(pts, angle=np.zeros(len(pts)))
    back = make_track(pts, angle=np.full(len(pts), 180.0))
    turn = make_track(pts, angle=np.r_[np.full(80, 180.0), np.zeros(20)])  # backs in, turns round at frame 80
    first = lambda m: int(np.flatnonzero(m)[0]) if m.any() else None
    m_fwd = occupancy(fwd, app, S)[0]["Z"]
    plain = occupancy(fwd, box_app(Zone("Z", rect(200, 150, 100, 100))), S)[0]["Z"]
    assert first(m_fwd) == first(plain)  # walking in facing the zone: the usual entry
    assert not occupancy(back, app, S)[0]["Z"].any()  # backing in never counts
    assert first(occupancy(turn, app, S)[0]["Z"]) == 80
    res = analyse(back, app, S)
    assert res["Z: entries"] == 0 and res["Z: time (s)"] == 0
    assert analyse(turn, app, S)["Z: entries"] == 1
    # off: the plain entry rule
    z.entry_orientation_deg = 0.0
    assert occupancy(back, app, S)[0]["Z"].any()


def test_entry_orientation_live_parity_and_persistence():
    z = Zone("Z", rect(200, 150, 100, 100), entry_orientation_deg=30.0)
    app = box_app(z)
    pts = np.vstack([line((60, 200), (250, 200), 60), hold((250, 200), 40)])
    ang = np.r_[np.full(70, 180.0), np.full(10, 90.0), np.zeros(20)]
    tr = make_track(pts, angle=ang)
    offline = occupancy(tr, app, S)[0]["Z"]
    occ = LiveOccupancy(app, S)
    live = np.array([occ.update(Detection(x=tr.x[i], y=tr.y[i], hx=tr.hx[i], hy=tr.hy[i], tx=tr.tx[i], ty=tr.ty[i],
                                          area=100.0, angle=tr.angle[i], detected=True))[0]["Z"]
                     for i in range(len(tr))])
    assert offline.any() and (live == offline).all()
    # saved only when set; older files load with the option off
    d = z.to_dict()
    assert d["entry_orientation_deg"] == 30.0
    assert Zone.from_dict(d).entry_orientation_deg == 30.0
    z.entry_orientation_deg = 0.0
    assert "entry_orientation_deg" not in z.to_dict()
    assert Zone.from_dict({"name": "Old", "shape": rect(0, 0, 1, 1).to_dict()}).entry_orientation_deg == 0.0


def test_new_zone_measures_respect_never_blank():
    app = box_app(Zone("Object", circle(200, 200, 20), investigation_distance_cm=2.0), Zone("Z", rect(0, 0, 50, 50)))
    s = AnalysisSettings(**{**S.to_dict(), "latency_if_never": "blank"})
    res = analyse(make_track(hold((350, 350), 50)), app, s)
    assert math.isnan(res["Object: latency to first investigation (s)"])
    assert math.isnan(res["Object: latency to end of first investigation (s)"])
    assert math.isnan(res["Object: distance before first investigation (cm)"])
    assert math.isnan(res["Arena: latency to first head exit (s)"])
