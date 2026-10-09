"""Measure definitions checked against ANY-maze's reference ("A detailed description of the ANY-maze measures"):
each test pins one place where mANY-MAZE used to give a different number on the same track (the section and page
of the reference are given in the test's comment)."""

import math

import numpy as np
import pytest

from manymaze.core.apparatus import Apparatus, PointOfInterest, Sequence, Zone
from manymaze.core.geometry import rect
from manymaze.core.iomeasures import io_measures
from manymaze.core.measures import AnalysisSettings, analyse, analyse_segmented
from manymaze.core.project import Behaviour

from test_zone_measures_extras import FPS, S, box_app, hold, line, make_track


def E(t, ch, v, kind="input", dev="box", typ=None, **kw):
    e = {"t": t, "device": dev, "channel": ch, "kind": kind, "value": v, **kw}
    if typ:
        e["type"] = typ
    return e


# ------------------------------------------------------------------ whole apparatus


def test_distance_while_hidden_is_not_counted():
    # 2.3, p. 19: the distance the animal may have travelled while hidden is not included
    app = box_app(Zone("Nest", rect(0, 0, 80, 80), hidden=True))
    pts = np.vstack([line((300, 60), (60, 60), 50), hold((60, 60), 50), line((60, 300), (300, 300), 50)])
    det = np.ones(len(pts), bool)
    det[50:100] = False  # lost in the nest, seen again at (60, 300)
    res = analyse(make_track(pts, head=False, detected=det), app, S)
    assert res["Time hidden (s)"] > 1.5
    # the two visible runs only (24 + 24 cm), not the 24 cm jump from the nest to where it reappears
    assert res["Total distance (cm)"] == pytest.approx(48.0, abs=0.5)
    # 2.47, p. 34: path efficiency is undefined when the animal was hidden
    assert math.isnan(res["Path efficiency"])


def test_latency_to_last_episode_undefined_when_none():
    # 2.21 / 2.22, p. 25-26: "If the animal is never mobile during the test then this value will be undefined"
    res = analyse(make_track(hold((200, 200), 100), head=False), box_app(), S)
    assert res["Mobile episodes"] == 0
    assert math.isnan(res["Latency to last mobile episode (s)"])
    assert res["Latency to first mobile episode (s)"] == res["Test duration (s)"]  # first: the test duration option


def test_head_turn_angle_uses_centre_to_head_vector():
    # 2.38, p. 31: the vector from the centre to the head (a body angle that disagrees is not used)
    n = 100
    pts = hold((200, 200), n)
    a = np.radians(np.linspace(0, 90, n))
    tr = make_track(pts, angle=np.zeros(n))
    tr.hx, tr.hy = 200 + 10 * np.cos(a), 200 + 10 * np.sin(a)  # head sweeps 90° clockwise, angle column says 0
    res = analyse(tr, box_app(), S)
    assert res["Head turn angle (deg)"] == pytest.approx(90.0, abs=3.0)
    assert res["Head turn angle clockwise (deg)"] == pytest.approx(90.0, abs=3.0)


def test_mean_rear_duration_is_time_over_rears():
    # 2.44, p. 33: the total time rearing divided by the number of rears (a rear under way at the start of a
    # period is time rearing in it, not a rear)
    n = 150
    area = np.full(n, 100.0)
    area[20:60] = 40.0  # one rear, 0.8-2.4 s
    area[100:120] = 40.0  # another, 4.0-4.8 s
    s = AnalysisSettings(**{**S.to_dict(), "rearing": True, "bin_length_s": 3.0})
    tr = make_track(hold((200, 200), n), head=False, area=area)
    periods = dict(analyse_segmented(tr, box_app(), s))
    whole, first, second = periods["Whole test"], periods["0-3 s"], periods["3-6 s"]
    assert whole["Rears"] == 2 and whole["Mean rear duration (s)"] == pytest.approx(2.4 / 2, abs=0.05)
    assert first["Rears"] == 1 and second["Rears"] == 1


# ------------------------------------------------------------------ zones


def test_head_entries_need_the_head_to_come_in():
    # 3.3, p. 39: the head position changing from outside the zone to inside it (a head starting inside has not
    # entered)
    app = box_app(Zone("Z", rect(150, 150, 100, 100)))
    pts = np.vstack([hold((200, 200), 25), line((200, 200), (60, 200), 25), line((60, 200), (200, 200), 25)])
    res = analyse(make_track(pts), app, S)
    assert res["Z: head entries"] == 1
    assert res["Z: latency to head entry (s)"] > 1.0


def test_investigation_needs_the_head_outside_the_zone():
    # 3.7, p. 40: investigating = the head outside the zone but within the investigation distance of it
    obj = Zone("Object", rect(180, 180, 40, 40), investigation_distance_cm=3.0)
    app = box_app(obj)
    # 2 s with the head 10 px from the object (investigating), then 2 s with the head inside it (not)
    pts = np.vstack([hold((150, 200), 50), hold((170, 200), 50)])
    res = analyse(make_track(pts, body_len=40, angle=np.zeros(100)), app, S)
    assert res["Object: time investigating (s)"] == pytest.approx(2.0, abs=0.05)
    assert res["Object: investigation bouts"] == 1


def test_distance_in_zone_counts_the_step_out_not_the_step_in():
    # 3.16, p. 44: the step between two positions counts in the zone the animal is leaving
    app = box_app(Zone("Z", rect(100, 100, 100, 100)))
    pts = np.array([[50, 150], [150, 150], [160, 150], [250, 150]], float)  # in by 100 px, 10 px inside, out 90
    res = analyse(make_track(pts, head=False), app, S)
    assert res["Z: distance (cm)"] == pytest.approx((10 + 90) / 10, abs=1e-6)


def test_distance_and_path_efficiency_before_entry_undefined():
    # 3.17 / 3.20, p. 45-46: undefined if the animal never enters / investigates the zone
    app = box_app(Zone("Far", rect(350, 350, 40, 40), investigation_distance_cm=1.0))
    res = analyse(make_track(line((50, 50), (150, 50), 50)), app, S)
    assert math.isnan(res["Far: distance before first entry (cm)"])
    assert math.isnan(res["Far: distance before first investigation (cm)"])
    # 3.82, p. 80: undefined when the route to the zone passed through a hidden zone
    app2 = box_app(Zone("Nest", rect(0, 0, 80, 80), hidden=True), Zone("Goal", rect(300, 300, 50, 50)))
    pts = np.vstack([hold((40, 40), 25), line((60, 60), (320, 320), 50)])
    det = np.ones(len(pts), bool)
    det[5:20] = False
    res2 = analyse(make_track(pts, head=False, detected=det), app2, S)
    assert res2["Goal: entries"] == 1 and math.isnan(res2["Goal: path efficiency to first entry"])


def test_visit_durations_clip_the_visit_under_way_and_mean_is_time_over_entries():
    # 3.31-3.33, p. 50-51: a period spent entirely in the zone has a longest visit of the whole period; the mean
    # visit is the time in the zone / the entries in the period
    app = box_app(Zone("Z", rect(150, 150, 100, 100)))
    pts = np.vstack([line((50, 200), (200, 200), 25), hold((200, 200), 125)])  # in the zone from ~0.7 s to 6 s
    s = AnalysisSettings(**{**S.to_dict(), "bin_length_s": 3.0})
    periods = dict(analyse_segmented(make_track(pts, head=False), app, s))
    late = periods["3-6 s"]
    assert late["Z: entries"] == 0
    assert late["Z: longest visit (s)"] == pytest.approx(3.0, abs=0.05)
    assert late["Z: mean visit (s)"] == 0  # no entry in the period: undefined, reported as 0
    early = periods["0-3 s"]
    assert early["Z: mean visit (s)"] == pytest.approx(early["Z: time (s)"], abs=1e-3)


def test_min_and_max_distance_from_zone():
    # 3.46 / 3.47, p. 57: the max is 0 if the animal never left the zone; the min is 0 once it entered it
    app = box_app(Zone("Z", rect(150, 150, 100, 100)))
    res = analyse(make_track(line((50, 200), (200, 200), 50), head=False), app, S)
    assert res["Z: min distance from zone (cm)"] == 0
    assert res["Z: max distance from zone (cm)"] == pytest.approx(10.0, abs=0.01)
    inside = analyse(make_track(hold((200, 200), 25), head=False), app, S)
    assert inside["Z: max distance from zone (cm)"] == 0
    # 3.50 / 3.51, p. 59: the same for the head
    assert analyse(make_track(hold((200, 200), 25)), app, S)["Z: max head distance from zone (cm)"] == 0
    headed = analyse(make_track(line((50, 200), (200, 200), 50)), app, S)
    assert headed["Z: min head distance from zone (cm)"] == 0


def test_min_distance_to_border_is_zero_after_leaving():
    # 3.54, p. 61: "If the animal exits the zone, this value is automatically set to zero"
    app = box_app(Zone("Big", rect(100, 100, 200, 200)))
    stay = analyse(make_track(hold((200, 200), 25), head=False), app, S)
    assert stay["Big: min distance to border when inside (cm)"] == pytest.approx(10.0)
    leave = analyse(make_track(np.vstack([hold((200, 200), 25), line((200, 200), (50, 200), 25)]), head=False),
                    app, S)
    assert leave["Big: min distance to border when inside (cm)"] == 0


def test_time_oriented_towards_zone_uses_the_whole_border_and_30_degrees():
    # 3.65, p. 72: the head direction (centre → head) within the critical angle (default 30°) of the direction
    # to ANY point of the zone's border, while outside the zone
    app = box_app(Zone("Wide", rect(300, 0, 20, 400)))  # a long wall to the right
    n = 50
    # facing 60° away from the zone centre (straight right) but still pointing at the far end of the wall
    ang = np.full(n, -60.0)
    res = analyse(make_track(hold((200, 200), n), angle=ang), app, S)
    assert res["Wide: time facing (s)"] == pytest.approx(n / FPS, abs=0.05)
    # 3.66, p. 73: oriented towards the centre when inside uses the same 30° (not the 45° exploration angle)
    app2 = box_app(Zone("Z", rect(100, 100, 200, 200)))
    r2 = analyse(make_track(hold((150, 200), n), angle=np.full(n, 40.0)), app2, S)  # centre straight right
    assert r2["Z: time oriented towards zone centre when inside (s)"] == 0
    r3 = analyse(make_track(hold((150, 200), n), angle=np.full(n, 20.0)), app2, S)
    assert r3["Z: time oriented towards zone centre when inside (s)"] == pytest.approx(n / FPS, abs=0.05)


def test_freezing_bouts_in_zone_count_freezing_onsets():
    # 3.69, p. 74: each time the animal starts to freeze, a check is made whether it is in the zone - entering the
    # zone while frozen does not start a bout there
    app = box_app(Zone("Z", rect(195, 0, 100, 400)))
    n = 150
    pts = np.vstack([hold((150, 200), 50), line((150, 200), (200, 200), 50), hold((200, 200), 50)])
    motion = np.full(n, 50.0)
    motion[25:] = 0.0  # frozen from 1 s on, drifting into the zone
    res = analyse(make_track(pts, head=False, motion=motion), app, S)
    assert res["Freezing episodes"] == 1
    assert res["Z: time freezing (s)"] > 1.0
    assert res["Z: freezing episodes"] == 0


def test_rears_in_zone_are_clipped_to_the_visit():
    # 3.76-3.78, p. 76-77: a rear in the zone also ends when the animal leaves it; mean = time / rears
    app = box_app(Zone("Z", rect(0, 0, 200, 400)))
    n = 100
    pts = np.vstack([hold((150, 200), 25), line((150, 200), (250, 200), 50), hold((250, 200), 25)])
    area = np.full(n, 100.0)
    area[10:60] = 40.0  # rearing 0.4-2.4 s, leaves the zone at ~1.5 s
    s = AnalysisSettings(**{**S.to_dict(), "rearing": True})
    res = analyse(make_track(pts, head=False, area=area), app, s)
    assert res["Z: rears"] == 1
    assert res["Z: max rear duration (s)"] == pytest.approx(res["Z: time rearing (s)"], abs=1e-6)
    assert res["Z: max rear duration (s)"] < res["Max rear duration (s)"] - 0.3


def test_head_in_zone_with_centre_outside_is_geometric():
    # 3.81, p. 79: does not use the zone entry criteria (here: a minimum entry duration)
    app = box_app(Zone("Z", rect(150, 150, 100, 100)))
    pts = np.vstack([hold((140, 200), 5), hold((100, 200), 20)])  # head 10 px in for 0.2 s
    s = AnalysisSettings(**{**S.to_dict(), "entry_min_duration_s": 1.0})
    res = analyse(make_track(pts, body_len=40, angle=np.zeros(25)), app, s)
    assert res["Z: time head in zone with centre outside (s)"] == pytest.approx(5 / FPS, abs=1e-3)


def test_cipl_undefined_without_entry():
    # 3.83, p. 80: by default based on the path until the first entry - #N/A if the animal never enters
    app = box_app(Zone("Goal", rect(350, 350, 40, 40)))
    res = analyse(make_track(line((50, 50), (200, 50), 100), head=False), app, S)
    assert math.isnan(res["Goal: corrected integrated path length (cm·s)"])


def test_head_turn_angle_in_zone_attributed_to_the_zone_entered():
    # 3.68, p. 74: the second of the two positions decides the zone
    app = box_app(Zone("Z", rect(195, 0, 100, 400)))
    n = 50
    pts = np.vstack([hold((190, 200), 25), hold((200, 200), 25)])
    tr = make_track(pts, angle=np.zeros(n))
    a = np.radians(np.r_[np.zeros(25), np.full(25, 90.0)])
    tr.hx, tr.hy = pts[:, 0] + 10 * np.cos(a), pts[:, 1] + 10 * np.sin(a)  # turns 90° as it steps in
    s = AnalysisSettings(**{**S.to_dict(), "speed_smoothing_s": 0.0})
    res = analyse(tr, app, s)
    assert res["Z: absolute head turn angle (deg)"] == pytest.approx(res["Head turn angle (deg)"], abs=1e-6)


# ------------------------------------------------------------------ points


def test_moving_towards_a_point_by_heading():
    # 4.7 / 4.11, p. 83 / 87: the direction of travel within the critical angle (45°) of the direction to the
    # point - not "getting closer"
    app = box_app()
    app.points.append(PointOfInterest("P", 400, 200))
    # moving straight up while the point is to the right: getting closer (slightly) but heading 90° off
    pts = line((300, 300), (300, 210), 50)
    res = analyse(make_track(pts, head=False), app, S)
    assert res["P: time moving towards (s)"] == 0
    assert math.isnan(res["P: mean speed moving towards (cm/s)"])
    pts2 = line((100, 200), (300, 200), 50)
    r2 = analyse(make_track(pts2, head=False), app, S)
    assert r2["P: time moving towards (s)"] == pytest.approx(49 / FPS, abs=0.05)


def test_head_oriented_towards_a_point():
    # 4.14 / 4.16, p. 89 / 91: centre → head vs head → point within 30°; a turn towards is counted when the
    # animal becomes oriented, not for being oriented at the start
    app = box_app()
    app.points.append(PointOfInterest("P", 400, 200))
    n = 75
    ang = np.r_[np.zeros(25), np.full(25, 40.0), np.zeros(25)]  # towards, 40° off, towards again
    res = analyse(make_track(hold((200, 200), n), angle=ang), app, S)
    assert res["P: time head oriented towards (s)"] == pytest.approx(50 / FPS, abs=0.05)
    assert res["P: head turns towards"] == 1


# ------------------------------------------------------------------ sequences


def test_sequence_counts_in_the_period_it_ends():
    # 5.1, p. 94: a sequence is considered to occur in the time period in which it ends
    app = box_app(Zone("A", rect(0, 0, 100, 100)), Zone("B", rect(300, 0, 100, 100)))
    app.sequences.append(Sequence("AB", ["A", "B"]))
    pts = np.vstack([hold((50, 50), 50), line((50, 50), (350, 50), 50), hold((350, 50), 50)])  # A 0-2.4, B 3.9
    s = AnalysisSettings(**{**S.to_dict(), "bin_length_s": 3.0})
    periods = dict(analyse_segmented(make_track(pts, head=False), app, s))
    assert periods["0-3 s"]["AB: completed"] == 0
    assert periods["3-6 s"]["AB: completed"] == 1
    assert periods["Whole test"]["AB: completed"] == 1


# ------------------------------------------------------------------ keys


def test_key_presses_in_a_period():
    # 6.1 / 6.4 / 6.8, p. 98-100: a press is the key going down in the period (a bout under way at its start is
    # time pressed, not a press); the mean is time pressed / presses; the first release is a real release
    app = box_app()
    beh = [Behaviour("Groom", "g", "state")]
    ev = [{"behaviour": "Groom", "t": 2.0, "t_end": 4.0}, {"behaviour": "Groom", "t": 5.0, "t_end": 5.5}]
    s = AnalysisSettings(**{**S.to_dict(), "bin_length_s": 3.0})
    periods = dict(analyse_segmented(make_track(hold((200, 200), 150), head=False), app, s, events=ev,
                                     behaviours=beh))
    late = periods["3-6 s"]
    assert late["Groom: count"] == 1
    assert late["Groom: duration (s)"] == pytest.approx(1.5)
    assert late["Groom: mean bout (s)"] == pytest.approx(1.5)
    assert late["Groom: latency (s)"] == pytest.approx(2.0)
    assert late["Groom: latency to first release (s)"] == pytest.approx(1.0)


def test_key_rate_in_zone_per_time_in_zone():
    # 6.9, p. 100: in a zone, the presses in the zone / the time in the zone
    app = box_app(Zone("A", rect(0, 0, 200, 400)))
    beh = [Behaviour("Poop", "p", "point")]
    s = AnalysisSettings(**{**S.to_dict(), "behaviour_by_zone": True})
    pts = np.vstack([hold((100, 200), 75), hold((300, 200), 75)])  # 3 s in A of 6 s
    res = analyse(make_track(pts, head=False), app, s, events=[{"behaviour": "Poop", "t": 1.0}], behaviours=beh)
    assert res["Poop in A: rate (/min)"] == pytest.approx(20.0)  # 1 in 3 s
    assert res["Poop: rate (/min)"] == pytest.approx(10.0)  # 1 in 6 s


# ------------------------------------------------------------------ I/O


def test_mean_activation_is_time_over_activations():
    # 7.7, p. 104: time active / number of activations (an activation under way at the start of a period is time
    # active, not an activation)
    ev = [E(0.0, "lever", 1), E(4.0, "lever", 0), E(5.0, "lever", 1), E(6.0, "lever", 0)]
    m = io_measures(ev, 10.0, t_range=(2.0, 10.0))
    assert m["lever: activations"] == 1
    assert m["lever: mean activation (s)"] == pytest.approx(3.0)  # (2 + 1) s / 1


def test_encoder_reversals_half_rotations_distance_and_min_rpm():
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 5,
                                         "cm_per_rev": 50}]}]
    ev = [E(0.0, "wheel", 0, typ="encoder")] + [E(i / 10, "wheel", i, typ="encoder") for i in range(1, 11)] \
        + [E(1.1, "wheel", 9, typ="encoder")]
    m = io_measures(ev, 2.0, devices=cfg)
    # 8.6, p. 106: a count one way followed by a count the other way is a reversal
    assert m["wheel: reversals"] == 1
    # 8.7, p. 106: a half rotation is floor(5 / 2) = 2 counts: 10 counts -> 5 half rotations
    assert m["wheel: half rotations"] == 5
    # 8.3, p. 105: distance = complete rotations (2) × circumference
    assert m["wheel: distance (cm)"] == 100
    # 8.12, p. 109: the minimum RPM is 0 when the encoder stopped
    assert m["wheel: min rate (rev/min)"] == 0


def test_analog_mean_is_the_sample_average():
    # 9.1 / 9.6, p. 111-112: the samples are summed and divided by their count (not weighted by time)
    ev = [E(0.0, "force", 10, typ="analog"), E(1.0, "force", 20, typ="analog"), E(1.5, "force", 30, typ="analog")]
    s = AnalysisSettings(io_baseline_s=10.0)
    m = io_measures(ev, 10.0, settings=s)
    assert m["force: mean"] == pytest.approx(20.0)
    assert m["force: baseline"] == pytest.approx(20.0)


def test_sensor_mean_is_the_reading_average():
    # 10.2, p. 118: simple average of the values reported
    cfg = [{"name": "box", "channels": [{"name": "lux", "kind": "sensor"}]}]
    ev = [E(0.0, "lux", 100, typ="sensor"), E(9.0, "lux", 200, typ="sensor")]
    m = io_measures(ev, 10.0, devices=cfg)
    assert m["Sensor lux: mean"] == pytest.approx(150.0)


def test_variable_never_recorded_has_max_min_zero():
    # 20.3 / 20.4, p. 141: never noted -> 0 (variables start at 0); in a period: undefined
    ev = [E(8.0, "score", 5, kind="variable", typ="variable")]
    m = io_measures(ev, 10.0, t_range=(0.0, 5.0), whole=False)
    assert math.isnan(m["Variable: score (max)"])
    app = box_app()
    res = analyse(make_track(hold((200, 200), 50), head=False), app, S,
                  io_events=[E(1.0, "score", 5, kind="variable", typ="variable")])
    assert res["Variable: score (max)"] == 5
    res = analyse(make_track(hold((200, 200), 50), head=False), app, S,
                  io_events=[E(1.0, "other", 1, kind="variable", typ="variable"),
                             E(9.0, "score", 5, kind="variable", typ="variable")])  # after the end of the test
    assert res["Variable: score (max)"] == 0


def test_pellet_latency_and_index_reversals():
    # 15.2, p. 131: latency to the first pellet dispensed
    ev = [E(2.0, "pellet", 1, kind="output", typ="pellet"), E(2.1, "pellet", 0, kind="output", typ="pellet")]
    assert io_measures(ev, 10.0)["pellet: latency to first pellet (s)"] == 2.0
    # 2.49 / 2.50, p. 35: inputs with index values activated 1 2 3 2 1 2 -> one negative and one positive reversal
    cfg = [{"name": "box", "channels": [{"name": f"b{i}", "kind": "input", "index": i} for i in (1, 2, 3)]}]
    order = [1, 2, 3, 2, 1, 2]
    ev = []
    for j, i in enumerate(order):
        ev += [E(j * 1.0, f"b{i}", 1), E(j * 1.0 + 0.5, f"b{i}", 0)]
    m = io_measures(ev, 10.0, devices=cfg)
    assert m["On/off inputs: negative reversals"] == 1 and m["On/off inputs: positive reversals"] == 1


def test_devices_in_zones():
    # 7.4-7.6 / 22.6 / 9.1-9.3 in zones: deactivation latency, longest / shortest stretch on in the zone, distance
    # while a virtual switch is on in the zone, a signal's mean / max / min in the zone
    app = box_app(Zone("A", rect(0, 0, 200, 400)))
    pts = np.vstack([line((50, 200), (150, 200), 100), hold((300, 200), 100)])  # A 0-4 s
    ev = [E(1.0, "lever", 1), E(2.0, "lever", 0), E(3.0, "lever", 1), E(6.0, "lever", 0),
          E(0.0, "sw", 1, kind="output", dev="virtual", typ="switch"),
          E(0.0, "temp", 20, typ="analog"), E(2.0, "temp", 30, typ="analog"), E(5.0, "temp", 50, typ="analog")]
    res = analyse(make_track(pts, head=False), app, S, io_events=ev)
    assert res["lever in A: latency to first deactivation (s)"] == pytest.approx(2.0)
    assert res["lever in A: longest activation (s)"] == pytest.approx(1.0, abs=0.05)
    assert res["sw in A: distance while active (cm)"] == pytest.approx(10.0, abs=0.2)
    assert res["temp in A: mean"] == pytest.approx(25.0) and res["temp in A: max"] == 30


def test_encoder_mean_rpm_is_unsigned():
    # 8.13, p. 109: the average of the rotational velocity, whichever the direction (back and forth does not cancel)
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 10}]}]
    ev = [E(0.0, "wheel", 0, typ="encoder"), E(1.0, "wheel", 10, typ="encoder"), E(2.0, "wheel", 0, typ="encoder")]
    m = io_measures(ev, 6.0, devices=cfg)
    assert m["wheel: revolutions"] == 0
    assert m["wheel: mean rate (rev/min)"] == pytest.approx(2 / 6 * 60)


def test_sensors_and_variables_in_zones():
    # 10.2-10.4 and 20.2-20.7 in zones: the values recorded while the animal was in the zone
    app = box_app(Zone("A", rect(0, 0, 200, 400)))
    pts = np.vstack([hold((100, 200), 75), hold((300, 200), 75)])  # A 0-3 s
    cfg = [{"name": "box", "channels": [{"name": "lux", "kind": "sensor"}]}]
    ev = [E(1.0, "lux", 100, typ="sensor"), E(2.0, "lux", 200, typ="sensor"), E(4.0, "lux", 900, typ="sensor"),
          E(0.5, "score", 2, kind="variable", typ="variable"), E(2.5, "score", 4, kind="variable", typ="variable"),
          E(5.0, "score", 10, kind="variable", typ="variable")]
    res = analyse(make_track(pts, head=False), app, S, io_events=ev, io_devices=cfg)
    assert res["Sensor lux in A: mean"] == pytest.approx(150.0) and res["Sensor lux in A: max"] == 200
    assert res["Variable: score in A (count)"] == 2 and res["Variable: score in A (sum)"] == 6
    assert res["Variable: score in A (values)"] == "2, 4"
