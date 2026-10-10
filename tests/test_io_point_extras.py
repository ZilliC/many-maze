"""Point, sequence and key extras, I/O extras (inputs, device groups, encoders, analogue baselines, virtual
switches, recorded variables), OPAD and RAPC."""

import math

import numpy as np
import pytest

from manymaze.core import templates
from manymaze.core.apparatus import Apparatus, PointOfInterest, Sequence, Zone, ZoneGroup
from manymaze.core.geometry import rect
from manymaze.core.iomeasures import io_measures, io_track_measures, parse_numbers
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented, behaviour_measures
from manymaze.core.procedures import ProcedureEngine, describe_statement, record_mode
from manymaze.core.project import Behaviour
from manymaze.core.track import Track

FPS = 25.0
S = AnalysisSettings(speed_smoothing_s=0.0, mobility_threshold=1.0, min_immobile_s=0.0, grid_cells=0)


def make_track(points, fps=FPS, head_offset=None):
    p = np.asarray(points, float)
    n = len(p)
    kw = {}
    if head_offset is not None:
        kw.update(hx=p[:, 0] + head_offset[0], hy=p[:, 1] + head_offset[1], tx=p[:, 0] - head_offset[0],
                  ty=p[:, 1] - head_offset[1],
                  angle=np.full(n, math.degrees(math.atan2(head_offset[1], head_offset[0]))))
    return Track(t=np.arange(n) / fps, x=p[:, 0], y=p[:, 1], fps=fps, area=np.full(n, 100.0), **kw)


def line(p0, p1, n):
    return np.column_stack([np.linspace(p0[0], p1[0], n), np.linspace(p0[1], p1[1], n)])


def hold(p, n):
    return np.tile(np.asarray(p, float), (n, 1))


def box_app(**kw) -> Apparatus:
    app = Apparatus(name="Box", arena=rect(0, 0, 400, 400), px_per_cm=10.0, **kw)
    app.zones.append(Zone("Arena", rect(0, 0, 400, 400), "#64748b"))
    return app


def E(t, ch, v, kind="input", dev="box", typ=None):
    e = {"t": t, "device": dev, "channel": ch, "kind": kind, "value": v}
    if typ:
        e["type"] = typ
    return e


# ====================================================================== points
def test_point_extras():
    app = box_app()
    app.points.append(PointOfInterest("P", 300, 100, radius_cm=0))
    # 2 s straight towards the point (100 px/s), 2 s still on it, 2 s straight away from it
    pts = np.vstack([line((100, 100), (300, 100), 50), hold((300, 100), 50), line((300, 100), (300, 300), 50)])
    tr = make_track(pts, head_offset=(10, 0))
    r = analyse(tr, app, S)
    assert r["P: X (cm)"] == 30.0 and r["P: Y (cm)"] == 10.0
    # ANY-maze 4.19, p. 93: the time spent at the point, from the heat map (2 s still on it, and the way in and out)
    assert 2.0 < r["P: approximate time at point (s)"] < 2.6
    assert r["P: initial heading error (deg)"] == 0.0
    # moving towards for 2 s, then perpendicular-ish away: mean |error| between 0 and 180
    assert 0 < r["P: mean absolute heading error (deg)"] < 180
    assert r["P: mean speed moving towards (cm/s)"] == pytest.approx(10.0, rel=0.05)
    # the head is 10 px to the right of the centre: it passes over the point on the way in and ends 20 cm away
    assert r["P: min head distance (cm)"] < 0.5
    assert r["P: max head distance (cm)"] == pytest.approx(math.hypot(1, 20), abs=0.05)
    assert r["P: time head moving towards (s)"] == pytest.approx(2.0, abs=0.2)
    assert r["P: time head moving away (s)"] == pytest.approx(2.0, abs=0.2)
    # a period: the time at the point in the period (1 of the 2 s still on it)
    seg = dict(analyse_segmented(tr, app, AnalysisSettings(**{**S.to_dict(), "bin_length_s": 3.0})))
    assert 1.0 < seg["3-6 s"]["P: approximate time at point (s)"] < 1.5
    # no head: no head measures
    r = analyse(make_track(pts), app, S)
    assert "P: mean head distance (cm)" not in r and "P: initial heading error (deg)" in r


# ====================================================================== sequences
def test_sequence_distance():
    app = box_app()
    app.zones += [Zone("A", rect(0, 0, 100, 400)), Zone("B", rect(150, 0, 100, 400)), Zone("C", rect(300, 0, 100, 400))]
    app.sequences.append(Sequence("ABC", ["A", "B", "C"]))
    pts = np.vstack([line((50, 200), (350, 200), 100), hold((350, 200), 25)])  # 30 cm at 30 cm/4 s
    r = analyse(make_track(pts), app, S)
    assert r["ABC: completed"] == 1
    # from the entry into A (start) to the entry into C (x = 300): 25 cm
    assert r["ABC: total distance in sequences (cm)"] == pytest.approx(25.0, abs=0.5)
    assert r["ABC: mean distance (cm)"] == r["ABC: max distance (cm)"] == r["ABC: min distance (cm)"]
    assert r["ABC: mean speed during sequences (cm/s)"] == pytest.approx(
        r["ABC: total distance in sequences (cm)"] / r["ABC: total time in sequences (s)"], rel=1e-3)
    r = analyse(make_track(hold((50, 200), 50)), app, S)
    assert r["ABC: total distance in sequences (cm)"] == 0 and math.isnan(r["ABC: mean distance (cm)"])


# ====================================================================== keys
def test_key_distance_before_first_press_and_durations():
    t = np.arange(100) / 10.0
    step = np.ones(100)
    beh = [Behaviour("Groom", "g", "state"), Behaviour("Rear", "r", "point"), Behaviour("Sniff", "s", "state")]
    ev = [{"behaviour": "Groom", "t": 2.0, "t_end": 3.5}, {"behaviour": "Groom", "t": 5.0, "t_end": 5.25},
          {"behaviour": "Rear", "t": 4.0}]
    out = behaviour_measures(ev, beh, 0.0, 10.0, t=t, step=step, unit="cm")
    assert out["Groom: distance before first press (cm)"] == 20.0
    assert out["Rear: distance before first press (cm)"] == 40.0
    assert out["Groom: press durations (s)"] == "1.5, 0.25"
    assert out["Sniff: press durations (s)"] == ""
    assert out["Sniff: distance before first press (cm)"] == 99.0  # never: the whole distance
    out = behaviour_measures(ev, beh, 0.0, 10.0, t=t, step=step, latency_if_never="blank")
    assert math.isnan(out["Sniff: distance before first press"])
    # without a track no distance
    assert "Groom: distance before first press (cm)" not in behaviour_measures(ev, beh, 0.0, 10.0)


def test_key_distance_through_analyse():
    app = box_app()
    tr = make_track(line((50, 50), (350, 50), 101))  # 30 cm in 4 s
    beh = [Behaviour("Groom", "g", "state")]
    r = analyse(tr, app, S, events=[{"behaviour": "Groom", "t": 2.0, "t_end": 3.0}], behaviours=beh)
    assert r["Groom: distance before first press (cm)"] == pytest.approx(15.0, abs=0.2)


# ====================================================================== on/off inputs and outputs
def test_inputs_longest_shortest_deactivation_reversals():
    ev = [E(1.0, "lever", 1), E(1.5, "lever", 0), E(4.0, "lever", 1), E(6.0, "lever", 0)]
    m = io_measures(ev, 10.0)
    assert m["lever: longest activation (s)"] == 2.0 and m["lever: shortest activation (s)"] == 0.5
    assert m["lever: latency to first deactivation (s)"] == 1.5
    assert m["lever: positive reversals"] == 2 and m["lever: negative reversals"] == 2
    # a period starting while the lever is held: the activation is clipped, no positive reversal for it
    m = io_measures(ev, 10.0, t_range=(5.0, 10.0))
    assert m["lever: positive reversals"] == 0 and m["lever: negative reversals"] == 1
    assert m["lever: longest activation (s)"] == 1.0 and m["lever: latency to first deactivation (s)"] == 1.0
    assert m["lever: latency to first activation (s)"] == 5.0
    m = io_measures(ev, 10.0, t_range=(7.0, 10.0), settings=AnalysisSettings(latency_if_never="blank"))
    assert math.isnan(m["lever: latency to first activation (s)"])
    assert math.isnan(m["lever: latency to first deactivation (s)"])


def test_device_groups():
    ev = [E(1.0, "shock", 1, "output", typ="shock"), E(3.0, "shock", 0, "output", typ="shock"),
          E(5.0, "shock", 1, "output", typ="shock"), E(5.5, "shock", 0, "output", typ="shock"),
          E(2.0, "tone", 2800, "output", "audio", "audio"), E(4.0, "tone", 0, "output", "audio", "audio"),
          E(0.0, "house", 1, "output", typ="light"), E(6.0, "house", 0, "output", typ="light"),
          E(0.0, "cue", 0.5, "output", typ="pwm"), E(5.0, "cue", 1.0, "output", typ="pwm"),
          E(1.0, "door", 1, "output"), E(2.0, "door", 0, "output")]
    devices = [{"name": "box", "type": "arduino", "channels": [{"name": "cue", "kind": "pwm", "role": "light"}]}]
    m = io_measures(ev, 10.0, devices=devices)
    assert m["Shocker shock: shocks"] == 2 and m["Shocker shock: time on (s)"] == 2.5
    assert m["Shocker shock: longest shock (s)"] == 2.0 and m["Shocker shock: shortest shock (s)"] == 0.5
    assert m["Shocker shock: latency to first shock (s)"] == 1.0
    assert m["Shocker shock: latency to first off (s)"] == 3.0
    assert m["Speaker tone: sounds"] == 1 and m["Speaker tone: time on (s)"] == 2.0
    assert m["Light house: times on"] == 1 and m["Light house: time on (s)"] == 6.0
    assert m["Light cue: mean level"] == pytest.approx(0.75)  # 0.5 for 5 s, 1.0 for 5 s
    assert m["door: times on"] == 1 and m["door: longest on (s)"] == 1.0
    assert not any(k.startswith("shock:") for k in m)


def test_light_action_logs_light():
    eng = ProcedureEngine([{"name": "p", "statements": [{"type": "do", "action": "light_on", "channel": "house"}]}])
    eng.start(0.0)
    eng.stop(2.0)
    assert any(e["channel"] == "house" and e.get("type") == "light" for e in eng.io_events)
    assert io_measures(eng.io_events, 2.0)["Light house: time on (s)"] == 2.0


# ====================================================================== rotary encoder
def test_encoder_extras():
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 100}]}]
    # 0-2 s: +250 counts (2.5 turns clockwise) in 0.1 s steps; 2-5 s still; 5-6 s: -120 counts (anticlockwise)
    ev = [E(0.0, "wheel", 0, typ="encoder")]
    v = 0
    for i in range(1, 21):
        v += 12.5
        ev.append(E(i / 10, "wheel", v, typ="encoder"))
    for i in range(1, 11):
        v -= 12
        ev.append(E(5 + i / 10, "wheel", v, typ="encoder"))
    m = io_measures(ev, 10.0, devices=cfg)
    assert m["wheel: encoder counts"] == 130
    assert m["wheel: degrees clockwise"] == 900.0 and m["wheel: degrees anticlockwise"] == 432.0
    assert m["wheel: clockwise rotations"] == 2 and m["wheel: anticlockwise rotations"] == 1
    assert m["wheel: half rotations"] == 5 + 2 and m["wheel: quarter rotations"] == 10 + 4
    assert m["wheel: reversals"] == 1
    assert m["wheel: time turning (s)"] == pytest.approx(3.0)  # 0-2 s and 5-6 s
    assert m["wheel: mean rate while turning (rev/min)"] == pytest.approx((2.5 + 1.2) / 3 * 60)
    assert m["wheel: min rate while turning (rev/min)"] == pytest.approx(72.0)  # 120 counts/s in the last second
    # as ANY-maze, every count in the other direction is a reversal (29 changes back and forth: 28 reversals)
    ev2 = [E(0.0, "wheel", 0, typ="encoder")] + [E(i / 10, "wheel", (i % 2), typ="encoder") for i in range(1, 30)]
    assert io_measures(ev2, 5.0, devices=cfg)["wheel: reversals"] == 28
    # without counts per revolution only the counts-based measures
    m = io_measures(ev, 10.0)
    assert "wheel: degrees clockwise" not in m and m["wheel: reversals"] == 1


# ====================================================================== analogue
def test_analog_baseline_and_deviations():
    ev = [E(0.0, "force", 10, typ="analog"), E(5.0, "force", 12, typ="analog"), E(10.0, "force", 10, typ="analog"),
          E(12.0, "force", 30, typ="analog"), E(14.0, "force", 10, typ="analog"), E(16.0, "force", 2, typ="analog"),
          E(17.0, "force", 10, typ="analog")]
    s = AnalysisSettings(io_baseline_s=10.0, io_deviation_sd=2.0)
    m = io_measures(ev, 20.0, settings=s)
    assert m["force: baseline"] == 11.0 and m["force: baseline SD"] == 1.0
    assert m["force: end of baseline (s)"] == 10.0
    assert m["force: time of max (s)"] == 12.0 and m["force: time of min (s)"] == 16.0
    assert m["force: first positive deviation (s)"] == 12.0
    assert m["force: return to baseline after positive deviation (s)"] == 14.0
    assert m["force: first negative deviation (s)"] == 16.0
    assert m["force: return to baseline after negative deviation (s)"] == 17.0
    assert m["force: integral above baseline"] == pytest.approx(2 * 19)
    # below: 10 for 2+2+3 s (1 below each) and 2 for 1 s (9 below)
    assert m["force: integral below baseline"] == pytest.approx(7 * 1 + 9)
    assert m["force: mean deviation from baseline"] == pytest.approx((2 * 19 + 7 + 9) / 10)
    # a period
    m = io_measures(ev, 20.0, t_range=(10.0, 20.0), settings=s)
    # as ANY-maze: the average of the samples in the baseline period (10, 30, 10, 2, 10)
    assert m["force: baseline"] == pytest.approx((10 + 30 + 10 + 2 + 10) / 5)


def test_analog_per_zone_visit():
    app = box_app()
    app.zones.append(Zone("Left", rect(0, 0, 200, 400)))
    # left 0-2 s, right 2-4 s, left 4-6 s
    pts = np.vstack([hold((100, 200), 50), hold((300, 200), 50), hold((100, 200), 50)])
    ev = [E(0.0, "temp", 20, typ="analog"), E(1.0, "temp", 25, typ="analog"), E(3.0, "temp", 18, typ="analog"),
          E(5.0, "temp", 15, typ="analog")]
    r = analyse(make_track(pts), app, S, io_events=ev)
    assert r["temp in Left: mean max"] == pytest.approx((25 + 18) / 2)
    assert r["temp in Left: mean min"] == pytest.approx((20 + 15) / 2)
    assert r["temp in Left: mean time to max (s)"] == pytest.approx((1.0 + 0.0) / 2)
    assert r["temp in Left: mean time to min (s)"] == pytest.approx((0.0 + 1.0) / 2)
    assert r["temp in Left: mean at entry"] == pytest.approx((20 + 18) / 2)
    assert r["temp in Left: mean at exit"] == pytest.approx((25 + 15) / 2)


# ====================================================================== virtual switches
def test_virtual_switch_distance():
    app = box_app()
    tr = make_track(line((0, 200), (400, 200), 251))  # 40 cm in 10 s: 4 cm/s
    ev = [E(2.0, "sw", 1, "output", "virtual", "switch"), E(5.0, "sw", 0, "output", "virtual", "switch")]
    r = analyse(tr, app, S, io_events=ev)
    assert r["sw: distance before first activation (cm)"] == pytest.approx(8.0, abs=0.1)
    assert r["sw: distance while active (cm)"] == pytest.approx(12.0, abs=0.2)
    seg = dict(analyse_segmented(tr, app, AnalysisSettings(**{**S.to_dict(), "bin_length_s": 4.0}), io_events=ev))
    assert seg["4-8 s"]["sw: distance before first activation (cm)"] == pytest.approx(16.0, abs=0.2)  # never
    assert seg["4-8 s"]["sw: distance while active (cm)"] == pytest.approx(4.0, abs=0.2)
    # pauses: the switch times move with the test time
    r = analyse(tr, app, S, io_events=ev, pauses=[[0.0, 1.0]])
    assert r["sw: distance before first activation (cm)"] == pytest.approx(4.0, abs=0.2)
    out = io_track_measures(ev, np.arange(0, 10, 0.5), np.full(20, 0.5), np.ones(20), (0, 10), "cm",
                            latency_if_never="blank")
    assert out["sw: distance before first activation (cm)"] == 4.0


# ====================================================================== recorded result variables
def _var_run(record):
    st = [{"type": "var", "name": "score", "value": 0, "result": True, "record": record},
          {"type": "set", "var": "score", "value": 5}, {"type": "wait", "mode": "seconds", "seconds": 2},
          {"type": "set", "var": "score", "value": 5}, {"type": "wait", "mode": "seconds", "seconds": 2},
          {"type": "set", "var": "score", "value": 2}]
    eng = ProcedureEngine([{"name": "p", "statements": st}])
    eng.start(0.0)
    for i in range(1, 61):
        eng.update_state(i / 10, {})
    eng.stop(6.0)
    return eng


def test_result_variable_history():
    eng = _var_run("changes")
    hist = [(e["t"], e["value"]) for e in eng.io_events if e["kind"] == "variable"]
    assert hist == [(0.0, 5), (4.0, 2)]
    assert eng.result_variables == {"score": 2}
    m = io_measures(eng.io_events, 6.0)
    assert m["Variable: score (count)"] == 2 and m["Variable: score (mean)"] == 3.5
    assert m["Variable: score (max)"] == 5 and m["Variable: score (min)"] == 2 and m["Variable: score (sum)"] == 7
    assert m["Variable: score (values)"] == "5, 2"
    eng = _var_run("set")
    assert [e["value"] for e in eng.io_events if e["kind"] == "variable"] == [5, 5, 2]
    m = io_measures(eng.io_events, 6.0, t_range=(1.0, 4.0))  # half-open: the value set at 4 s is in the next one
    assert m["Variable: score (values)"] == "5"
    m = io_measures(eng.io_events, 6.0, t_range=(4.0, 6.0), test_end=6.0)
    assert m["Variable: score (values)"] == "2"
    # default (old projects): only the final value
    eng = _var_run(None)
    assert not any(e["kind"] == "variable" for e in eng.io_events)
    assert record_mode({"type": "var"}) == "end" and record_mode({"record": "changes"}) == "changes"
    assert "recorded every time it changes" in describe_statement({"type": "var", "name": "x", "record": "changes"})
    # analysed with the track, variables are not mistaken for outputs
    app = box_app()
    r = analyse(make_track(hold((100, 100), 150)), app, S, io_events=_var_run("changes").io_events,
                result_variables={"score": 2})
    assert r["Variable: score"] == 2 and r["Variable: score (count)"] == 2
    assert "score: times on" not in r


# ====================================================================== OPAD
def test_opad():
    ev = [E(0.0, "temp", 10, typ="analog"), E(10.0, "temp", 45, typ="analog"), E(20.0, "temp", 30, typ="analog"),
          # contacts: 1-4 s (2 licks), 6-7 s (no lick), 12-15 s (1 lick), 22-23 s
          E(1.0, "paw", 1), E(4.0, "paw", 0), E(6.0, "paw", 1), E(7.0, "paw", 0), E(12.0, "paw", 1),
          E(15.0, "paw", 0), E(22.0, "paw", 1), E(23.0, "paw", 0),
          E(2.0, "lick", 1), E(2.1, "lick", 0), E(3.0, "lick", 1), E(3.1, "lick", 0), E(13.0, "lick", 1),
          E(13.1, "lick", 0), E(22.5, "lick", 1), E(22.6, "lick", 0)]
    s = AnalysisSettings(opad_contact="paw", opad_lick="lick", opad_temperature="temp", opad_temperatures="10, 45",
                         opad_tolerance=0.5)
    m = io_measures(ev, 30.0, settings=s)
    assert m["OPAD: contacts made"] == 4 and m["OPAD: contacts broken"] == 4
    assert m["OPAD: time in contact (s)"] == 8.0 and m["OPAD: licks"] == 4
    assert m["OPAD: non-lick contacts"] == 1
    assert m["OPAD: temperatures when contact broken"] == "10, 10, 45, 30"
    assert m["OPAD: mean temperature when contact broken"] == pytest.approx(23.75)
    assert m["OPAD at 10°: time in contact (s)"] == 4.0 and m["OPAD at 10°: contacts made"] == 2
    assert m["OPAD at 10°: licks"] == 2 and m["OPAD at 45°: licks"] == 1
    assert m["OPAD at 45°: time in contact (s)"] == 3.0 and m["OPAD at 45°: contacts broken"] == 1
    # not configured: no OPAD measures
    assert not any(k.startswith("OPAD") for k in io_measures(ev, 30.0))
    assert parse_numbers("10, 45; x 3.5") == [10.0, 45.0, 3.5]


# ====================================================================== RAPC
def test_rapc_template_and_errors():
    app = templates.build("rapc", 0, 0, 400, 400, n_arms=4, baited_arms="1, 3")
    assert app.template == "rapc" and [ln.name for ln in app.lines] == [f"Door {i}" for i in range(1, 5)]
    assert app.group("Baited arms").zones == ["Arm 1", "Arm 3"]
    cx = cy = 200
    arm_pt = {}
    for z in app.zones:
        if z.name.startswith("Arm"):
            arm_pt[z.name] = z.shape.centroid()
    path = []
    # visit arms 1, 2, 1, 3, 4 (back through the centre each time)
    for a in ["Arm 1", "Arm 2", "Arm 1", "Arm 3", "Arm 4"]:
        path += [hold((cx, cy), 10), line((cx, cy), arm_pt[a], 15), hold(arm_pt[a], 10), line(arm_pt[a], (cx, cy), 15)]
    tr = make_track(np.vstack(path))
    r = analyse(tr, app, AnalysisSettings(grid_cells=0))
    assert r["Door sequence"] == "1 2 1 3 4"
    assert r["Type 1 errors"] == 1  # re-entry into baited arm 1
    assert r["Type 2 errors"] == 2  # entries into unbaited arms 2 and 4
    assert r["Total errors"] == 3 and r["Baited arms visited"] == 2
    assert r["Correct entries before first error"] == 1 and r["Entries to visit all baited arms"] == 4
    assert r["Door 1: crossings"] >= 2
