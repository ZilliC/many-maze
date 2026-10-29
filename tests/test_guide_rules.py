"""Measures whose calculation contradicted the user guide's own rules (or ANY-maze's published example), found by
the QA oracle audit (qa/AUDITORIA_ORACULO.md, H-P06 n.º 5, 9, 12, 13): each test fails on the old calculation."""

from types import SimpleNamespace

import numpy as np
import pytest

from manymaze.core.apparatus import Zone
from manymaze.core.geometry import rect
from manymaze.core.measures import analyse
from manymaze.core.series import count_rotations
from manymaze.core.template_measures import whishaw_corridor

from test_zone_measures_extras import FPS, S, box_app, hold, line, make_track


def test_rotation_after_turning_back_counts_from_the_furthest_point():
    # 300° clockwise, then back anticlockwise to -70°: 370° anticlockwise from the furthest point reached (a
    # rotation, as ANY-maze's example); counted from where the turn back was detected (~209°) it was only ~279°
    a = np.r_[np.arange(0, 301, 5), np.arange(300, -71, -5)].astype(float)
    assert count_rotations(a) == (0, 1)


def test_head_distance_in_zone_counts_the_step_out_not_the_step_in():
    z = Zone("Z", rect(150, 150, 100, 100))
    # the centre stops 5 px short of the zone with the head 5 px inside: the head made 2 steps ending in the zone
    # (into it, and one inside), and none after
    pts = np.vstack([line((60, 200), (145, 200), 30), hold((145, 200), 20)])
    res = analyse(make_track(pts, body_len=20, angle=np.zeros(len(pts))), box_app(z), S)
    assert res["Z: head distance (cm)"] == pytest.approx(85 / 29 / 10, abs=0.01)


def test_min_head_distance_to_border_is_zero_once_the_head_left():
    big = Zone("Big", rect(100, 100, 200, 200))
    xs = [200] * 10 + [240, 280, 320] + [320] * 10  # head (10 px ahead) at 210, 250, 290, then out at 330
    pts = np.column_stack([xs, np.full(len(xs), 200.0)])
    res = analyse(make_track(pts, body_len=20, angle=np.zeros(len(pts))), box_app(big), S)
    assert res["Big: min head distance to border when inside (cm)"] == 0  # as the centre's (was 1.0: 10 px)
    assert res["Big: min distance to border when inside (cm)"] == 0


def test_water_maze_whishaw_distance_counts_the_step_out_not_the_step_in():
    # corridor 20 px wide along y = 0 from (0, 0) to (100, 0); the swim goes out of it and back in
    xy = np.array([(0, 0), (10, 0), (20, 30), (30, 30), (30, 0), (40, 0)], float)
    step = np.r_[0.0, np.hypot(*np.diff(xy, axis=0).T)]
    k = SimpleNamespace(x=xy[:, 0], y=xy[:, 1], t=np.arange(len(xy)) / FPS, dur=np.full(len(xy), 1 / FPS),
                        step=step, scale=1.0)
    app = SimpleNamespace(point=lambda name: None, px_per_cm=1.0)
    w = whishaw_corridor(k, app, SimpleNamespace(whishaw_width=20.0), 100.0, 0.0, len(xy) - 1)
    # 10 (inside), 31.6 (the step out), 10 (inside); not the 30 px step back in
    assert w["distance"] == pytest.approx(10 + np.hypot(10, 30) + 10, abs=0.01)
    assert w["left"] == "Yes"
