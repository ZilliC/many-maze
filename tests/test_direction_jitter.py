"""H-P05: the measures that depend on the DIRECTION of movement (turning, rotations, getting closer / further, moving
towards / away) ignore the tracking jitter - a centre that wobbles by a fraction of a millimetre while the animal sits
still - but follow real movement."""

import math
from dataclasses import replace

import numpy as np
import pytest

from manymaze.core.geometry import rect
from manymaze.core.apparatus import Zone
from manymaze.core.measures import AnalysisSettings, analyse
from manymaze.core.series import simplify_path

from test_zone_measures_extras import FPS, box_app, make_track

S = AnalysisSettings(mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0)  # the default 0.2 s smoothing


def jittery_still(n=250, amp_px=0.5, seed=3):
    """An animal that does not move: its centre wobbles by amp_px (0.05 cm at 10 px/cm) per frame."""
    rng = np.random.default_rng(seed)
    return np.array([300.0, 200.0]) + rng.normal(0, amp_px, (n, 2))


def test_a_still_animal_does_not_turn_or_approach():
    app = box_app(Zone("Cheese", rect(20, 20, 60, 60)))
    res = analyse(make_track(jittery_still(), head=False), app, S)
    for m in ("Absolute turn angle (deg)", "Angular velocity (deg/s)", "Total rotations", "Cheese: time getting closer (s)", "Cheese: time getting further away (s)",
              "Cheese: time moving towards (s)", "Cheese: time moving away (s)", "Cheese: absolute turn angle (deg)"):
        assert res[m] == 0, m
    assert res["Meander (deg/cm)"] == 0


def test_a_slow_drift_under_the_noise_floor_is_not_a_direction():
    """A shiver of +-0.02 cm around a point never reaches the minimum displacement."""
    xy = np.zeros((200, 2))
    xy[::2, 0] = 0.2  # +-0.2 px = 0.02 cm
    sx, sy = simplify_path(xy[:, 0], xy[:, 1], 0.5 * 10.0, 5)
    assert np.ptp(sx) == 0 and np.ptp(sy) == 0


def test_a_circle_keeps_its_turning_with_jitter():
    """Real movement is kept: 8 cm radius at 10 cm/s turns 10/8 rad/s = 71.6 deg/s, with or without 0.05 cm of noise (unsimplified, the noise alone adds ~10 %)."""
    n = 250
    th = np.arange(n) / FPS * (10.0 / 8.0)
    ring = np.c_[200 + 80 * np.cos(th), 200 + 80 * np.sin(th)]  # px (10 px/cm)
    noisy = ring + np.random.default_rng(1).normal(0, 0.5, ring.shape)
    for xy in (ring, noisy):
        res = analyse(make_track(xy, head=False), box_app(), S)
        assert res["Angular velocity (deg/s)"] == pytest.approx(math.degrees(10.0 / 8.0), rel=0.12)


def test_straight_walk_with_jitter_does_not_turn():
    n = 250
    xy = np.c_[50 + np.arange(n) * 1.2, np.full(n, 200.0)] + np.random.default_rng(2).normal(0, 0.5, (n, 2))
    raw = analyse(make_track(xy, head=False), box_app(), replace(S, min_direction_step=0.0))
    res = analyse(make_track(xy, head=False), box_app(), S)
    assert res["Absolute turn angle (deg)"] < 0.2 * raw["Absolute turn angle (deg)"]
