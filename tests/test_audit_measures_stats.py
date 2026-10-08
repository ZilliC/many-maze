"""Fixes from the whole-program audit of 2026-10-08, "Measures, statistics and plots": plots and heat maps of a
paused test, template latencies and the zone entry rules, exits / grid crossings across a pause, paired post-hoc
tests, Mood's median test on constant data, turn angle / rotations additive over periods and not across a hidden
gap, an animal never detected, and chart parameters that reproduce the result measures."""

import math
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
import pytest

from manymaze.core import charts, plots
from manymaze.core import stats as st
from manymaze.core.apparatus import PointOfInterest, Zone, ZoneGroup, make_grid
from manymaze.core.geometry import rect
from manymaze.core.measures import AnalysisSettings, all_periods, analyse, analyse_segmented
from manymaze.core.pauses import period_frames, to_recording_time, to_test_time
from manymaze.core.track import Track

from test_zone_measures_extras import FPS, S, box_app, hold, line, make_track


def _circling(n_turns=3, n=300, r=50.0):
    a = np.linspace(0, 2 * np.pi * n_turns, n)
    return np.column_stack([200 + r * np.cos(a), 200 + r * np.sin(a)])


# ------------------------------------------------------------------ pauses: plots and heat maps


def _paused_track():
    """20 s recording paused from 5 to 10 s: 0-5 s at (50, 50), the paused 5-10 s at (350, 50), 10-15 s at
    (350, 350) and 15-20 s at (50, 350)."""
    n = int(5 * FPS)
    pts = np.vstack([hold((50, 50), n), hold((350, 50), n), hold((350, 350), n), hold((50, 350), n)])
    return make_track(pts, head=False)


def test_pause_time_conversions():
    pauses = [[5.0, 10.0]]
    assert to_recording_time([0.0, 4.0, 5.0, 7.0], pauses).tolist() == [0.0, 4.0, 10.0, 12.0]
    assert to_recording_time([5.0], pauses, after_pause=False).tolist() == [5.0]
    t = np.array([1.0, 12.0, 19.0])
    assert np.allclose(to_recording_time(to_test_time(t, pauses), pauses), t)
    tr = _paused_track()
    keep = period_frames(tr, pauses)
    assert not keep[(tr.t >= 5) & (tr.t < 10)].any() and keep[tr.t < 5].all() and keep[tr.t >= 10].all()
    # the test-time period 5-10 s is recorded from 10 to 15 s
    sel = tr.t[period_frames(tr, pauses, (5.0, 10.0))]
    assert sel.min() == pytest.approx(10.0) and sel.max() < 15.0


def _fake_project(tr, app, pauses, s):
    test = SimpleNamespace(id="T1", pauses=pauses, events=[], io_events=[], zone_overrides=None, variables={})
    return SimpleNamespace(
        groups=[SimpleNamespace(name="G")], apparatus_of=lambda t: app, load_tracks=lambda t: [tr],
        analysis_for=lambda t: s, behaviours=[],
        test_periods=lambda t, track: all_periods(track, app, s, None, t.events, t.io_events, t.zone_overrides,
                                                  t.pauses)), test


def test_group_heatmap_uses_test_time_and_leaves_out_paused_frames(monkeypatch):
    tr, app = _paused_track(), box_app()
    s = AnalysisSettings(bin_length_s=5.0)
    proj, test = _fake_project(tr, app, [[5.0, 10.0]], s)
    got = {}
    monkeypatch.setattr(plots, "group_heatmap_figure", lambda data, **kw: got.setdefault("data", data))
    plots.group_heatmap(proj, {"G": [test]}, period="5-10 s")
    sub = got.pop("data")[0][1][0]
    # the second 5 s of the test: recorded at 10-15 s, at (350, 350) - not the paused frames at (350, 50)
    assert len(sub) == int(5 * FPS) and np.allclose(sub.x, 350) and np.allclose(sub.y, 350)
    plots.group_heatmap(proj, {"G": [test]})
    whole = got.pop("data")[0][1][0]
    assert len(whole) == int(15 * FPS) and not ((whole.x == 350) & (whole.y == 50)).any()


def test_segmented_track_plot_selects_periods_in_test_time():
    tr, app = _paused_track(), box_app()
    fig = plots.segmented_track_plot(tr, app, [("0-5 s", 0.0, 5.0), ("5-10 s", 5.0, 10.0)], color_by="none",
                                     pauses=[[5.0, 10.0]])
    paths = [ax.lines[0].get_xydata() for ax in fig.axes if ax.lines]
    assert np.allclose(paths[0], [50, 50]) and np.allclose(paths[1], [350, 350])


# ------------------------------------------------------------------ exits, head exits and crossings across a pause


def test_no_exit_or_head_exit_across_a_pause():
    app = box_app(Zone("A", rect(0, 0, 200, 400)), Zone("B", rect(200, 0, 200, 400)))
    n = int(4 * FPS)
    pts = np.vstack([hold((100, 200), n), line((100, 200), (300, 200), int(2 * FPS)), hold((300, 200), n)])
    res = analyse(make_track(pts), app, S, pauses=[[4.0, 6.0]])
    assert res["B: entries"] == 0  # as before: no entry across a pause …
    assert res["A: exits"] == 0  # … and no exit either
    assert math.isnan(res["A: time of last exit (s)"])
    assert res["A: latency to first exit (s)"] == pytest.approx(res["Test duration (s)"])
    assert res["A: latency to first head exit (s)"] == pytest.approx(res["Test duration (s)"])
    # without the pause the animal leaves A
    res2 = analyse(make_track(pts), app, S)
    assert res2["A: exits"] == 1 and res2["B: entries"] == 1


def test_no_grid_crossing_across_a_pause():
    app = box_app()
    make_grid(app, "square", name="G", nx=2, ny=1)
    n = int(4 * FPS)
    pts = np.vstack([hold((100, 200), n), line((100, 200), (300, 200), int(2 * FPS)), hold((300, 200), n)])
    assert analyse(make_track(pts, head=False), app, S)["G: crossings"] == 1
    assert analyse(make_track(pts, head=False), app, S, pauses=[[4.0, 6.0]])["G: crossings"] == 0


# ------------------------------------------------------------------ turn angle and rotations


def test_turn_angle_and_rotations_add_up_over_bins():
    tr = make_track(_circling(n_turns=4, n=400))
    s = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0,
                         bin_length_s=1.0)
    out = analyse_segmented(tr, box_app(), s)
    whole, bins = out[0][1], [r for _, r in out[1:]]
    assert len(bins) == 16
    assert sum(r["Absolute turn angle (deg)"] for r in bins) == pytest.approx(whole["Absolute turn angle (deg)"],
                                                                             abs=0.05 * len(bins))
    for key in ("Rotations clockwise", "Rotations anticlockwise", "Total rotations", "Path rotations clockwise"):
        assert sum(r.get(key, 0) for r in bins) == whole.get(key, 0), key
    assert whole["Total rotations"] >= 3
    # in a zone too: the turns between two frames in the zone
    s.bin_length_s = 0.0
    app = box_app(Zone("Left", rect(0, 0, 200, 400)))
    whole = analyse(tr, app, s)
    s.bin_length_s = 1.0
    bins = [r for _, r in analyse_segmented(tr, app, s)[1:]]
    assert sum(r["Left: absolute turn angle (deg)"] for r in bins) == pytest.approx(
        whole["Left: absolute turn angle (deg)"], abs=0.05 * len(bins))


def test_no_turn_across_a_hidden_gap():
    app = box_app(Zone("Nest", rect(0, 0, 80, 80), hidden=True))
    # in towards the nest (heading south-west), hidden, then out heading south: 135° apart
    pts = np.vstack([line((300, 300), (60, 60), 50), hold((60, 60), 75), line((70, 70), (70, 350), 50)])
    det = np.ones(len(pts), bool)
    det[50:125] = False
    res = analyse(make_track(pts, head=False, detected=det), app, S)
    assert res["Time hidden (s)"] > 2.5
    assert res["Absolute turn angle (deg)"] < 1.0


# ------------------------------------------------------------------ an animal never detected


def test_never_detected_animal_is_not_outside_the_arena_or_immobile():
    n = 100
    nan = np.full(n, np.nan)
    tr = Track(t=np.arange(n) / FPS, x=nan.copy(), y=nan.copy(), fps=FPS, detected=np.zeros(n, bool))
    res = analyse(tr, box_app(), S)
    assert res["Time outside arena (s)"] == 0
    assert res["Time immobile (s)"] == 0 and res["Immobile episodes"] == 0
    assert res["Time mobile (s)"] == 0


# ------------------------------------------------------------------ template measures and the entry rules


def _water_maze_app():
    app = box_app(Zone("Platform", rect(180, 180, 40, 40)))
    app.template = "water_maze"
    return app


def test_water_maze_escape_honours_the_entry_rules():
    app = _water_maze_app()
    # the animal swims across the platform in 0.2 s and away from it, never stopping on it
    pts = np.vstack([line((50, 200), (350, 200), 50), hold((350, 200), 100)])
    tr = make_track(pts, head=False)
    s = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0,
                         entry_min_duration_s=0.5)
    res = analyse(tr, app, s)
    assert res["Platform: entries"] == 0
    assert res["Found platform"] == "No"
    assert res["Escape latency (s)"] == pytest.approx(res["Test duration (s)"])
    assert res["Path length to platform (cm)"] == pytest.approx(res["Total distance (cm)"], abs=0.02)
    s.latency_if_never = "blank"
    assert math.isnan(analyse(tr, app, s)["Escape latency (s)"])
    # it stops on the platform: found, at the first entry as the zone measures count it
    pts2 = np.vstack([line((50, 200), (200, 200), 50), hold((200, 200), 100)])
    res2 = analyse(make_track(pts2, head=False), app, s)
    assert res2["Found platform"] == "Yes" and res2["Platform crossings"] == 1
    assert res2["Escape latency (s)"] == pytest.approx(res2["Platform: latency to first entry (s)"])
    # released on the platform with the initial entry not counted: not found
    s.count_initial_entry = False
    res3 = analyse(make_track(hold((200, 200), 100), head=False), app, s)
    assert res3["Found platform"] == "No" and res3["Platform crossings"] == 0


def test_t_maze_and_barnes_never_latencies_follow_the_setting():
    s = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0,
                         latency_if_never="blank", entry_min_duration_s=0.5)
    pts = np.vstack([line((50, 200), (350, 200), 50), hold((350, 200), 100)])  # 0.2 s in each zone on the way
    app = box_app(Zone("Left arm", rect(180, 180, 40, 40)), Zone("Right arm", rect(0, 0, 10, 10)))
    app.template = "t_maze"
    res = analyse(make_track(pts, head=False), app, s)
    assert res["First choice"] == "None" and math.isnan(res["Choice latency (s)"])
    app = box_app(Zone("Hole 1", rect(180, 180, 40, 40)), Zone("Hole 2", rect(0, 0, 10, 10)))
    app.groups.append(ZoneGroup("Escape hole zone", ["Hole 1"]))
    app.template = "barnes_maze"
    res = analyse(make_track(pts, head=False), app, s)
    assert math.isnan(res["Primary latency (s)"]) and res["Escape hole visits"] == 0


# ------------------------------------------------------------------ statistics


def _paired_groups():
    rng = np.random.default_rng(3)
    base = rng.normal(10, 2, 12)
    return OrderedDict([("A", base), ("B", base + 1 + rng.normal(0, 0.3, 12)),
                        ("C", base + 2 + rng.normal(0, 0.3, 12))])


@pytest.mark.parametrize("method", ["tukey", "games_howell", "dunn"])
def test_paired_posthoc_reports_the_method_used(method):
    res = st.compare_groups(_paired_groups(), paired=True, posthoc_method=method)
    assert res["posthoc_method"] == "bonferroni"
    assert len(res["posthoc"]) == 3 and all("Bonferroni" in ph["test"] for ph in res["posthoc"])
    assert st.compare_groups(_paired_groups(), posthoc_method=method)["posthoc_method"] == method


def test_paired_dunnett_keeps_the_comparisons_with_the_control():
    g = _paired_groups()
    res = st.compare_groups(g, paired=True, posthoc_method="dunnett", control="B")
    assert res["posthoc_method"] == "dunnett_paired"
    ph = res["posthoc"]
    assert {(e["a"], e["b"]) for e in ph} == {("A", "B"), ("C", "B")}
    assert all("vs control" in e["test"] for e in ph)
    # Bonferroni over the k - 1 comparisons with the control
    assert all(e["p"] == pytest.approx(min(1.0, 2 * e["p_unadjusted"])) for e in ph)


def test_moods_median_test_on_constant_data_is_nan():
    res = st.compare_groups(OrderedDict([("A", np.ones(5)), ("B", np.ones(5)), ("C", np.ones(5))]),
                            method="median")
    assert res["test"] == "Mood's median test" and math.isnan(res["p"]) and math.isnan(res["statistic"])


# ------------------------------------------------------------------ chart parameters = result measures


def test_chart_parameters_reproduce_the_results():
    pts = _circling(n_turns=2, n=300)
    body = np.linspace(0, 500, len(pts))
    tr = make_track(pts, angle=body.copy())
    tr.angle[:] = 0.0  # a tracked body angle disagreeing with the head: the results use centre → head
    app = box_app()
    app.points.append(PointOfInterest("Obj", 250, 200, radius_cm=4.0))
    res = analyse(tr, app, S)
    data = charts.compute(tr, app, ["Absolute turn angle", "Obj: near", "Obj: head-to-point angle", "Head angle",
                                    "Time immobile"], S)
    assert data["Absolute turn angle"][-1] == pytest.approx(res["Absolute turn angle (deg)"], abs=0.1)
    dur = tr.frame_durations()
    assert float(dur[data["Obj: near"] > 0.5].sum()) == pytest.approx(res["Obj: time near (s)"], abs=1e-3)
    assert float(np.nanmean(data["Obj: head-to-point angle"])) == pytest.approx(res["Obj: mean head angle (deg)"],
                                                                                abs=0.1)
    assert np.allclose(np.mod(data["Head angle"], 360), np.mod(body, 360), atol=1e-6)
    assert data["Time immobile"][-1] == pytest.approx(res["Time immobile (s)"], abs=1e-3)
