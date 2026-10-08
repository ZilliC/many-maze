"""Measures added after the ANY-maze parity audit of 2026-10-08 (TODO §12): activity per zone, time not hidden,
total and body rotations, Whishaw's corridor (seconds, any zone), average position, latency to the start of the
first sequence, output activation duration / frequency, encoder total rotations and maximum RPM, devices and keys
per zone, the advanced zone measures for grid cells and zone groups, and the definitions changed to match
ANY-maze (mean distance from a zone, initial heading error, body rotations)."""

import math

import numpy as np
import pytest

from manymaze.core import templates
from manymaze.core.apparatus import Line, Sequence, Zone, ZoneGroup, make_grid
from manymaze.core.geometry import rect
from manymaze.core.iomeasures import io_measures
from manymaze.core.measures import AnalysisSettings, analyse
from manymaze.core.project import Behaviour
from manymaze.core.track import Track

from test_zone_measures_extras import FPS, S, box_app, hold, line, make_track, nums


def E(t, ch, v, kind="output", dev="box", typ=None):
    e = {"t": t, "device": dev, "channel": ch, "kind": kind, "value": v}
    if typ:
        e["type"] = typ
    return e


def walk_a_then_b(n_a=100, n_b=100):
    """Hold in zone A (left half) for n_a frames, then in zone B (right half) for n_b frames."""
    return np.vstack([hold((100, 200), n_a), hold((300, 200), n_b)])


def ab_app():
    return box_app(Zone("A", rect(0, 0, 200, 400)), Zone("B", rect(200, 0, 200, 400)))


# ------------------------------------------------------------------ whole test


def test_time_not_hidden():
    app = box_app(Zone("Nest", rect(0, 0, 80, 80), hidden=True))
    pts = np.vstack([line((300, 300), (60, 60), 50), hold((60, 60), 75), line((70, 70), (300, 300), 50)])
    det = np.ones(len(pts), bool)
    det[50:125] = False
    res = analyse(make_track(pts, head=False, detected=det), app, S)
    assert res["Time hidden (s)"] + res["Time not hidden (s)"] == pytest.approx(res["Test duration (s)"], abs=1e-3)
    assert res["Time not hidden (s)"] == pytest.approx(len(pts) / FPS - 3.0, abs=0.05)


def test_average_position():
    app = box_app()
    pts = np.vstack([hold((100, 100), 50), hold((300, 200), 50)])
    res = analyse(make_track(pts, head=False), app, S)
    # as ANY-maze: % of the apparatus width / height from its left / top side, weighted by time
    assert res["Average X position (%)"] == pytest.approx(50.0, abs=0.01)
    assert res["Average Y position (%)"] == pytest.approx(37.5, abs=0.01)


def _circling(n_turns=3, n=300, r=50.0):
    a = np.linspace(0, 2 * np.pi * n_turns, n)
    return np.column_stack([200 + r * np.cos(a), 200 + r * np.sin(a)])


def test_total_rotations_and_body_orientation():
    pts = _circling()
    app = box_app()
    # the body turns twice and a half anticlockwise while the path goes round three times clockwise
    body = -np.linspace(0, 900, len(pts))
    res = analyse(make_track(pts, angle=body), app, S)
    assert res["Rotations anticlockwise"] == 2 and res["Rotations clockwise"] == 0
    assert res["Total rotations"] == 2
    assert res["Path rotations clockwise"] >= 2
    # as ANY-maze, the body is the vector from the centre to the head: a tracked body angle disagreeing with it
    # is not used when the head is tracked
    tr = make_track(pts, angle=body.copy())
    tr.angle[:] = 0.0
    res2 = analyse(tr, app, S)
    assert res2["Rotations anticlockwise"] == 2 and res2["Total rotations"] == 2
    assert "Path rotations clockwise" in res2
    # no head: the tracked body angle (e.g. an imported orientation)
    tr_a = Track(t=np.arange(len(pts)) / FPS, x=pts[:, 0], y=pts[:, 1], angle=body, fps=FPS)
    assert analyse(tr_a, app, S)["Rotations anticlockwise"] == 2
    # nothing but the centre: the direction of travel
    res3 = analyse(make_track(pts, head=False), app, S)
    assert res3["Rotations clockwise"] >= 2 and res3["Total rotations"] == res3["Rotations clockwise"]
    assert "Path rotations clockwise" not in res3


# ------------------------------------------------------------------ zones


def test_zone_activity():
    app = ab_app()
    motion = np.r_[np.full(100, 50.0), np.full(100, 0.0)]  # active in A, inactive in B
    tr = make_track(walk_a_then_b(), head=False, motion=motion)
    res = analyse(tr, app, S)
    assert res["A: time active (s)"] == pytest.approx(4.0, abs=0.05) and res["A: time inactive (s)"] == 0
    assert res["B: time active (s)"] == 0 and res["B: time inactive (s)"] == pytest.approx(4.0, abs=0.05)
    assert res["A: inactive episodes"] == 0 and res["B: inactive episodes"] == 1
    assert res["A: time active (s)"] + res["B: time active (s)"] == pytest.approx(res["Time active (s)"], abs=0.05)
    assert "A: time active (s)" not in analyse(make_track(walk_a_then_b(), head=False), app, S)


def test_mean_distance_from_zone_time_weighted_over_the_period():
    app = box_app(Zone("G", rect(0, 0, 100, 400)))
    # 1 s inside, 3 s 200 px (20 cm) away, 1 s 100 px away: ANY-maze sums distance × time while outside and divides
    # by the whole period (so an animal inside the zone all the time is 0 from it)
    pts = np.vstack([hold((50, 200), 25), hold((300, 200), 75), hold((200, 200), 25)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["G: mean distance from zone (cm)"] == pytest.approx((20 * 3 + 10 * 1) / 5, abs=0.01)
    assert analyse(make_track(hold((50, 200), 50), head=False), app, S)["G: mean distance from zone (cm)"] == 0


def test_initial_heading_error_absolute_and_signed():
    app = box_app(Zone("B", rect(300, 150, 50, 100)))
    res = analyse(make_track(line((50, 200), (150, 100), 50), head=False), app, S)
    assert res["B: initial heading error (deg)"] == pytest.approx(45, abs=0.5)
    # heading up-right, zone straight right: as ANY-maze, positive = the zone is to the animal's right
    assert res["B: signed initial heading error (deg)"] == pytest.approx(45, abs=0.5)
    assert "B: initial absolute heading error (deg)" not in res


def test_whishaw_corridor_seconds_and_any_zone():
    app = templates.build("water_maze", 0, 0, 300, 300)
    pc = app.point("Platform centre")
    straight = np.vstack([line((150, 295), (pc.x, pc.y), 60), hold((pc.x, pc.y), 10)])
    res = analyse(make_track(straight, head=False), app, AnalysisSettings())
    assert res["Whishaw corridor time (%)"] == 100
    assert res["Whishaw corridor time (s)"] > 2.0
    # any zone with a corridor width (as ANY-maze): from the animal's start position to the zone centre, the time
    # spent and the distance travelled in it over the whole period
    goal = Zone("Goal", rect(340, 180, 40, 40), whishaw_width_cm=10.0)
    app2 = box_app(goal, Zone("Plain", rect(0, 0, 40, 40)))
    pts = np.vstack([line((50, 200), (360, 200), 50), hold((360, 200), 25),  # straight there, wait 1 s
                     line((360, 200), (360, 50), 25)])  # leave the corridor sideways
    r = analyse(make_track(pts, head=False), app2, S)
    assert "Plain: time in Whishaw's corridor (s)" not in r
    # 75 frames on the way and waiting, then 9 frames (up to 50 px = 5 cm sideways) leaving
    assert r["Goal: time in Whishaw's corridor (s)"] == pytest.approx(84 / FPS, abs=1e-3)
    # the step out of the corridor counts, the step into it not (as the distance in a zone)
    assert r["Goal: distance in Whishaw's corridor (cm)"] == pytest.approx(31.0 + 5.0 + 0.625, abs=0.1)
    assert Zone.from_dict(goal.to_dict()).whishaw_width_cm == 10.0


def test_grid_cells_get_the_advanced_zone_measures():
    app = box_app()
    make_grid(app, "square", nx=2, ny=2)
    app.lines.append(Line("Mid", 100, 0, 100, 200))
    pts = np.vstack([line((20, 50), (180, 50), 50), line((180, 50), (180, 150), 25), hold((180, 150), 25)])
    res = analyse(make_track(pts), app, S)
    cell = "Grid A1"
    for m in ("visit durations (s)", "latency to first head exit (s)", "mean distance to border when inside (cm)",
              "initial heading error (deg)", "absolute turn angle (deg)",
              "corrected integrated path length (cm·s)", "line crossings"):
        assert f"{cell}: {m}" in res, m
    assert res[f"{cell}: line crossings"] == 1
    assert len(nums(res[f"{cell}: visit durations (s)"])) == res[f"{cell}: entries"]
    # still not listed among the visited zones (the grid has its own measures)
    assert cell not in res["Visited zones"]


def test_zone_group_border_heading_and_cipl():
    app = box_app(Zone("N", rect(150, 0, 100, 100)), Zone("S", rect(150, 300, 100, 100)),
                  Zone("Hole", rect(175, 25, 50, 50)))
    app.groups.append(ZoneGroup("Arms", ["N", "S"], exclude=["Hole"]))
    # outside, heading straight for N, then inside N, 10 px from its border
    pts = np.vstack([line((200, 200), (200, 90), 50), hold((200, 90), 50)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["Arms: mean distance to border when inside (cm)"] == pytest.approx(1.0, abs=0.05)
    assert "Arms: initial heading error (deg)" in res and "Arms: time getting closer (s)" in res
    assert res["Arms: time getting closer (s)"] > 1.5
    assert math.isfinite(res["Arms: corrected integrated path length (cm·s)"])
    # the border of a group of touching zones is its outline: no border between the cells of a grid
    app2 = box_app()
    make_grid(app2, "square", nx=2, ny=2)
    r2 = analyse(make_track(hold((200, 200), 25), head=False), app2, S)  # at the shared corner of 4 cells
    assert r2["Grid: mean distance to border when inside (cm)"] == pytest.approx(20.0, abs=0.05)


# ------------------------------------------------------------------ sequences


def test_sequence_latency_to_start_of_first():
    app = box_app(Zone("A", rect(0, 0, 100, 100)), Zone("B", rect(300, 0, 100, 100)))
    app.sequences.append(Sequence("AB", ["A", "B"]))
    pts = np.vstack([hold((200, 200), 25), line((200, 200), (50, 50), 25), line((50, 50), (350, 50), 50),
                     hold((350, 50), 10)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["AB: completed"] == 1
    assert res["AB: latency to start of first (s)"] == pytest.approx(res["A: latency to first entry (s)"], abs=1e-3)
    assert res["AB: latency to start of first (s)"] < res["AB: latency to first (s)"]


# ------------------------------------------------------------------ I/O


def test_output_mean_activation_and_frequency():
    ev = [E(1.0, "door", 1), E(2.0, "door", 0), E(4.0, "door", 1), E(7.0, "door", 0),
          E(1.0, "laser", 1, typ="train"), E(1.5, "laser", 0, typ="train"),
          E(0.0, "sw", 1, dev="virtual", typ="switch"), E(3.0, "sw", 0, dev="virtual", typ="switch"),
          E(0.0, "house", 1, typ="light"), E(5.0, "house", 0, typ="light"),
          E(1.0, "shock", 1, typ="shock"), E(2.0, "shock", 0, typ="shock"),
          E(2.0, "tone", 1, typ="audio"), E(3.0, "tone", 0, typ="audio")]
    m = io_measures(ev, 60.0)
    assert m["door: mean on (s)"] == 2.0 and m["door: activations per minute"] == 2.0
    assert m["laser: mean on (s)"] == 0.5 and m["laser: activations per minute"] == 1.0
    assert m["sw: mean on (s)"] == 3.0 and m["sw: activations per minute"] == 1.0
    assert m["Light house: mean on (s)"] == 5.0 and m["Light house: activations per minute"] == 1.0
    assert m["Shocker shock: activations per minute"] == 1.0
    assert m["Speaker tone: activations per minute"] == 1.0


def test_encoder_total_rotations_and_max_rpm():
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 100}]}]
    ev = [E(0.0, "wheel", 0, "input", typ="encoder")]
    v = 0
    for i in range(1, 21):  # +250 counts over 2 s
        v += 12.5
        ev.append(E(i / 10, "wheel", v, "input", typ="encoder"))
    for i in range(1, 11):  # -120 counts over 1 s
        v -= 12
        ev.append(E(5 + i / 10, "wheel", v, "input", typ="encoder"))
    m = io_measures(ev, 10.0, devices=cfg)
    assert m["wheel: revolutions"] == 1.3  # net
    # ANY-maze's number of rotations: complete rotations in either direction (2 clockwise + 1 anticlockwise)
    assert m["wheel: total rotations"] == 3
    # maximum RPM: counts over windows of at least 0.2 s, averaged over 10 windows - 125 counts/s at the fastest
    assert m["wheel: max rate (rev/min)"] == pytest.approx(125 / 100 * 60)


def test_devices_per_zone():
    app = ab_app()
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 100}]}]
    ev = [E(1.0, "lever", 1, "input"), E(1.5, "lever", 0, "input"), E(6.0, "lever", 1, "input"),
          E(7.0, "lever", 0, "input"),
          E(2.0, "pellet", 1, typ="pellet"), E(2.1, "pellet", 0, typ="pellet"),
          E(5.0, "shock", 1, typ="shock"), E(6.0, "shock", 0, typ="shock"),
          E(0.0, "laser", 1, typ="train"), E(6.0, "laser", 0, typ="train"),
          E(0.0, "wheel", 0, "input", typ="encoder"), E(1.0, "wheel", 50, "input", typ="encoder"),
          E(5.0, "wheel", 250, "input", typ="encoder")]
    res = analyse(make_track(walk_a_then_b(), head=False), app, S, io_events=ev, io_devices=cfg)  # A 0-4 s, B 4-8 s
    assert res["lever in A: activations"] == 1 and res["lever in B: activations"] == 1
    assert res["lever in A: time on (s)"] == pytest.approx(0.5, abs=0.05)
    assert res["lever in B: latency to first activation (s)"] == pytest.approx(6.0)
    assert res["lever in A: activations per minute"] == pytest.approx(15.0)  # 1 activation in 4 s in the zone
    assert res["pellet in A: pellets dispensed"] == 1 and res["pellet in B: pellets dispensed"] == 0
    assert res["Shocker shock in B: shocks"] == 1 and res["Shocker shock in A: shocks"] == 0
    assert res["Shocker shock in A: latency to first shock (s)"] == pytest.approx(8.0)  # never: the test length
    assert res["laser in A: time on (s)"] == pytest.approx(4.0, abs=0.05)
    assert res["laser in B: time on (s)"] == pytest.approx(2.0, abs=0.05)
    assert res["wheel in A: encoder counts"] == 50 and res["wheel in B: encoder counts"] == 200
    assert res["wheel in B: total rotations"] == 2


def test_keys_per_zone():
    app = ab_app()
    beh = [Behaviour("Groom", "g", "state"), Behaviour("Poop", "p", "point")]
    ev = [{"behaviour": "Groom", "t": 1.0, "t_end": 1.5}, {"behaviour": "Groom", "t": 2.0, "t_end": 3.0},
          {"behaviour": "Groom", "t": 5.0, "t_end": 7.0}, {"behaviour": "Poop", "t": 6.0}]
    s = AnalysisSettings(**{**S.to_dict(), "behaviour_by_zone": True})
    pts = np.vstack([line((50, 200), (150, 200), 100), line((250, 200), (350, 200), 100)])
    res = analyse(make_track(pts, head=False), app, s, events=ev, behaviours=beh)
    assert res["Groom in A: count"] == 2 and res["Groom in B: count"] == 1
    assert res["Groom in A: longest bout (s)"] == pytest.approx(1.0, abs=0.05)
    assert res["Groom in A: shortest bout (s)"] == pytest.approx(0.5, abs=0.05)
    assert res["Groom in A: latency to first release (s)"] == 1.5
    assert res["Groom in B: latency to first release (s)"] == 7.0
    assert nums(res["Groom in A: press durations (s)"]) == [0.5, 1.0]
    assert res["Groom in A: distance before first press (cm)"] == pytest.approx(1.0 * 25 * 1.0 / 10, abs=0.15)
    assert res["Groom in B: distance before first press (cm)"] > res["Groom in A: distance before first press (cm)"]
    assert res["Poop in B: distance before first press (cm)"] > 0
    assert res["Poop in A: distance before first press (cm)"] == pytest.approx(res["Total distance (cm)"])  # never


# ------------------------------------------------------------------ results table


def test_whole_test_measures_are_general():
    pytest.importorskip("PySide6")
    from manymaze.gui.pages.results.table import measure_category

    names = {"zones": {"A"}, "points": set(), "lines": set(), "behaviours": set(), "io": set()}
    for col in ("Mobile episodes", "Latency to last mobile episode (s)", "Longest mobile episode (s)",
                "Mean speed when not hidden (cm/s)", "Path tortuosity", "First zone entered", "Visited zones",
                "Total rotations", "Average X position (%)", "Time not hidden (s)", "Arena quadrant NE: time (%)",
                "Zone transitions", "Total line crossings"):
        assert measure_category(col, names) == ("General", ""), col
    assert measure_category("A: time (s)", names) == ("Zones", "A")
    assert measure_category("Escape latency (s)", names) == ("Test-specific", "")
