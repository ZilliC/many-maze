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


# ------------------------------------------------------------------ left after the audit (TODO §16)


def _rest_walk_rest():
    """2 s still, 2 s walking 20 cm to the right, 2 s still (6 s); no pixel change at all."""
    pts = np.vstack([hold((100, 200), 50), line((100, 200), (300, 200), 50), hold((300, 200), 50)])
    return make_track(pts, head=False, motion=np.zeros(len(pts)))


def test_activity_is_mobility_or_keys_that_count_as_activity():
    # 2.23-2.30, p. 26-29: "An animal is defined to be active if it is either mobile OR it's performing some other
    # behaviour which has been specified as an activity - for example, grooming"
    beh = [Behaviour("Groom", "g", "state", activity=True), Behaviour("Sniff", "s", "state")]
    ev = [{"behaviour": "Groom", "t": 4.5, "t_end": 5.5}, {"behaviour": "Sniff", "t": 0.5, "t_end": 1.5}]
    s = AnalysisSettings(**{**S.to_dict(), "activity_definition": "mobile_or_keys"})
    res = analyse(_rest_walk_rest(), box_app(), s, events=ev, behaviours=beh)
    assert res["Time active (s)"] == pytest.approx(res["Time mobile (s)"] + 1.0, abs=0.05)
    # assumed inactive at the start for active episodes and active for inactive ones (2.25 / 2.26): still at the
    # start is an inactive episode
    assert res["Active episodes"] == 2 and res["Inactive episodes"] == 3
    assert res["Arena: time active (s)"] == res["Time active (s)"]  # per zone too, without a pixel-change value
    # "If the immobility detection element specifies that mobility should NOT be detected, then activity analysis
    # will be based purely on the performance of other behaviours"
    keys = analyse(_rest_walk_rest(), box_app(), AnalysisSettings(**{**s.to_dict(), "activity_definition": "keys"}),
                   events=ev, behaviours=beh)
    assert keys["Time active (s)"] == pytest.approx(1.0, abs=0.05) and keys["Active episodes"] == 1
    # experiments made before the option keep the pixel-change activity (none here: no pixel changed)
    old = analyse(_rest_walk_rest(), box_app(), S, events=ev, behaviours=beh)
    assert old["Time active (s)"] == 0


def test_activity_settings_of_new_and_old_experiments(tmp_path):
    from manymaze.core.project import Project

    p = Project(name="new")
    assert p.analysis.activity_definition == "mobile_or_keys" and p.analysis.undefined_averages == "blank"
    p.behaviours = [Behaviour("Groom", "g", activity=True)]
    p.apparatus.append(Apparatus(name="Box", arena=rect(0, 0, 10, 10)))
    p.apparatus[0].points.append(PointOfInterest("Hot", 1, 2, heatmap="freezing"))
    p.save(tmp_path / "new")
    q = Project.load(tmp_path / "new")
    assert q.behaviours[0].activity and q.analysis.activity_definition == "mobile_or_keys"
    assert q.apparatus[0].points[0].heatmap == "freezing"
    # an experiment saved before these options: pixel-change activity, averages as before, no activity keys
    old = Project.from_dict({"name": "old", "analysis": {"mobility_threshold": 2.0},
                             "behaviours": [{"name": "Groom", "key": "g", "kind": "state"}],
                             "apparatus": [{"name": "Box", "zones": [], "points": [{"name": "P", "x": 1, "y": 2}]}]})
    a = old.analysis
    assert (a.activity_definition, a.undefined_averages, a.partial_rotation_deg) == ("pixel_change", "", 90.0)
    assert (a.heading_error_by, a.heading_error_time_s) == ("time", 1.0)
    assert old.behaviours[0].activity is False and old.apparatus[0].points[0].heatmap == ""
    assert "heatmap" not in old.apparatus[0].points[0].to_dict()  # old files are written back as they were


def test_partial_rotations():
    # 2.34-2.36, p. 30-31: the body rotated by at least the partial rotation angle without completing a rotation
    # (ANY-maze's calculation is "still in beta": the turns from one reversal to the next, see the guide)
    ang = np.r_[np.arange(0, 180, 2.0), np.arange(180, 0, -2.0), np.arange(0, 500, 2.0)]
    n = len(ang)
    res = analyse(make_track(hold((200, 200), n), angle=ang), box_app(), S)
    assert res["Rotations clockwise"] == 1 and res["Rotations anticlockwise"] == 0
    assert res["Partial rotations clockwise"] == 1  # the first 180° turn (the 500° one completed a rotation)
    assert res["Partial rotations anticlockwise"] == 1 and res["Partial rotations"] == 2
    big = analyse(make_track(hold((200, 200), n), angle=ang), box_app(),
                  AnalysisSettings(**{**S.to_dict(), "partial_rotation_deg": 200.0}))
    assert big["Partial rotations"] == 0
    # a partial rotation occurs at the time it is completed (where the turn stopped: 180° at 3.6 s)
    s = AnalysisSettings(**{**S.to_dict(), "bin_length_s": 4.0})
    seg = dict(analyse_segmented(make_track(hold((200, 200), n), angle=ang), box_app(), s))
    assert seg["0-4 s"]["Partial rotations clockwise"] == 1 and seg["4-8 s"]["Partial rotations anticlockwise"] == 1


def test_angular_velocity_is_turn_angle_over_test_duration():
    # 2.37, p. 31: "Angular velocity ... is the Absolute turn angle divided by the Test duration"
    pts = np.vstack([line((50, 50), (300, 50), 50), line((300, 50), (300, 300), 50), hold((300, 300), 50)])
    res = analyse(make_track(pts, head=False), box_app(), S)
    assert res["Absolute turn angle (deg)"] == pytest.approx(90, abs=5)
    assert res["Angular velocity (deg/s)"] == pytest.approx(res["Absolute turn angle (deg)"] / 6.0, abs=0.05)


def test_undefined_averages_blank_or_zero():
    # 3.12 / 3.28 / 3.30 / 3.33 / 3.52 / 3.55 / 3.76 / 5.5 / 5.9 / 5.12: undefined when there is nothing to
    # average, or zero with ANY-maze's "Use zero as the result for undefined averages"
    app = box_app(Zone("Far", rect(350, 350, 40, 40)), Zone("Obj", rect(10, 350, 20, 20), investigation_distance_cm=1))
    app.sequences.append(Sequence("Q", ["Far", "Obj"]))
    tr = make_track(line((100, 100), (200, 100), 50))
    names = ["Far: mean visit (s)", "Far: mean speed (cm/s)", "Far: mean distance to border when inside (cm)",
             "Far: mean head distance to border when inside (cm)", "Obj: mean investigation bout (s)",
             "Obj: mean speed while investigating (cm/s)", "Q: mean duration (s)", "Q: mean distance (cm)",
             "Q: mean speed during sequences (cm/s)", "Far: mean rear duration (s)"]

    def run(mode):
        s = AnalysisSettings(**{**S.to_dict(), "undefined_averages": mode, "rearing": True})
        return analyse(tr, app, s)
    blank, zero, before = run("blank"), run("zero"), run("")
    assert all(math.isnan(blank[n]) for n in names)
    assert all(zero[n] == 0 for n in names)
    # experiments made before the option: 0 for the visit, investigation bout and rear in a zone, else blank
    assert [before[n] == 0 for n in names] == [True, False, False, False, True, False, False, False, False, True]
    assert before["Mean rear duration (s)"] == blank["Mean rear duration (s)"] == 0  # 2.44: not an option


def test_initial_heading_ignores_immobility_and_can_use_a_distance():
    # 3.60 / 4.12, p. 64 / 88: the heading from the first position to the first position after the specified time
    # (or more than the specified distance away); "positions that are detected while the animal is considered to
    # be immobile are ignored - thus in the first case, the animal must be mobile for the period that is specified"
    app = box_app(Zone("Goal", rect(350, 150, 40, 100)))
    app.points.append(PointOfInterest("P", 370, 200))
    # 3 s still, then 1 cm up and on to the right
    pts = np.vstack([hold((100, 200), 75), line((100, 200), (100, 190), 10), line((100, 190), (300, 190), 50)])
    res = analyse(make_track(pts, head=False), app, S)
    assert res["Goal: initial heading error (deg)"] < 30  # 1 s of moving: mostly to the right, not undefined
    assert res["P: initial heading error (deg)"] < 30
    s = AnalysisSettings(**{**S.to_dict(), "heading_error_by": "distance", "heading_error_distance": 0.5})
    up = analyse(make_track(pts, head=False), app, s)
    assert up["Goal: initial heading error (deg)"] == pytest.approx(90, abs=2)  # the first 0.5 cm is upwards
    assert up["Goal: signed initial heading error (deg)"] == pytest.approx(90, abs=2)  # the zone is to its right
    far = analyse(make_track(pts, head=False), app, AnalysisSettings(**{**s.to_dict(), "heading_error_distance": 50}))
    assert math.isnan(far["Goal: initial heading error (deg)"])  # never 50 cm from the start


def test_heat_map_points():
    # 4.17-4.19, p. 92-93: a point at the hottest spot of a heat map (where the animal spent the longest time, or
    # the longest time doing something such as freezing); its X / Y and the approximate time spent there
    app = box_app()
    app.points.append(PointOfInterest("Hot", 0, 0, radius_cm=0, heatmap="time"))
    app.points.append(PointOfInterest("Frozen", 0, 0, radius_cm=0, heatmap="freezing"))
    app.points.append(PointOfInterest("Fixed", 300, 100, radius_cm=0))
    pts = np.vstack([line((50, 300), (300, 100), 50), hold((300, 100), 100), line((300, 100), (100, 100), 50),
                     hold((100, 100), 50)])
    motion = np.full(len(pts), 50.0)
    motion[200:] = 0.0  # frozen at (100, 100) only
    res = analyse(make_track(pts, head=False, motion=motion), app, S)
    assert res["Hot: X (cm)"] == pytest.approx(30, abs=0.7) and res["Hot: Y (cm)"] == pytest.approx(10, abs=0.7)
    assert 4.0 <= res["Hot: approximate time at point (s)"] < 4.6  # 4 s on the spot, more on the way in / out
    assert res["Hot: min distance (cm)"] < 0.7  # the point's measures use the spot
    assert res["Frozen: X (cm)"] == pytest.approx(10, abs=0.7) and res["Frozen: Y (cm)"] == pytest.approx(10, abs=0.7)
    assert 1.9 < res["Frozen: approximate time at point (s)"] < 2.2
    # a point placed in the protocol: the time spent at its location, from the same heat map
    assert res["Fixed: approximate time at point (s)"] == pytest.approx(res["Hot: approximate time at point (s)"],
                                                                        abs=0.3)
    none = analyse(make_track(pts, head=False, motion=np.full(len(pts), 50.0)), app, S)
    assert math.isnan(none["Frozen: X (cm)"])  # never froze: no spot


def test_keys_and_switches_in_investigation_zones_use_investigating():
    # 6.1-6.10 and 22.1-22.10, p. 98-101 / 145-148: in a zone "or for an investigation zone, while the animal was
    # investigating the zone"
    obj = Zone("Object", rect(180, 180, 40, 40), investigation_distance_cm=3.0)
    app = box_app(obj)
    # 2 s with the head 10 px from the object (investigating), then 2 s with the head inside it (not)
    pts = np.vstack([hold((150, 200), 50), hold((170, 200), 50)])
    tr = make_track(pts, body_len=40, angle=np.zeros(100))
    beh = [Behaviour("Groom", "g", "state")]
    ev = [{"behaviour": "Groom", "t": 0.5, "t_end": 1.0}, {"behaviour": "Groom", "t": 2.5, "t_end": 3.0}]
    io = [E(0.2, "sw", 1, kind="output", dev="virtual", typ="switch"),
          E(0.6, "sw", 0, kind="output", dev="virtual", typ="switch"),
          E(2.2, "sw", 1, kind="output", dev="virtual", typ="switch"),
          E(2.8, "sw", 0, kind="output", dev="virtual", typ="switch")]
    s = AnalysisSettings(**{**S.to_dict(), "behaviour_by_zone": True})
    res = analyse(tr, app, s, events=ev, behaviours=beh, io_events=io)
    assert res["Object: time (s)"] == pytest.approx(4.0, abs=0.05)  # in the zone all along (head near or in it)
    assert res["Groom in Object: count"] == 1 and res["Groom in Object: duration (s)"] == pytest.approx(0.5, abs=0.05)
    assert res["Groom in Object: rate (/min)"] == pytest.approx(1 / (2 / 60), abs=0.5)  # per time investigating
    assert res["sw in Object: times on"] == 1 and res["sw in Object: time on (s)"] == pytest.approx(0.4, abs=0.05)


def test_encoder_in_zones():
    # 8.1-8.14 in zones, p. 105-110: time turning, reversals, (half / quarter) rotations made while the animal was
    # in the zone, minimum and mean RPM in it
    app = box_app(Zone("A", rect(0, 0, 200, 400)))
    pts = np.vstack([hold((100, 200), 100), hold((300, 200), 100)])  # in A for 0-4 s
    cfg = [{"name": "box", "channels": [{"name": "wheel", "kind": "encoder", "counts_per_rev": 4}]}]
    v, ev = 0, [E(0.0, "wheel", 0, typ="encoder")]
    for i in range(1, 81):  # a sample every 0.1 s: clockwise 0-2 s, still 2-3 s, anticlockwise 3-5 s
        x = i / 10
        v += 1 if x <= 2.0 + 1e-9 else -1 if 3.0 + 1e-9 < x <= 5.0 + 1e-9 else 0
        ev.append(E(round(x, 1), "wheel", v, typ="encoder"))
    res = analyse(make_track(pts, head=False), app, S, io_events=ev, io_devices=cfg)
    g = "wheel in A"
    assert res[f"{g}: time turning (s)"] == pytest.approx(3.0, abs=0.05)
    assert res[f"{g}: reversals"] == 1 and res["wheel: reversals"] == 1
    assert res[f"{g}: clockwise rotations"] == 5 and res[f"{g}: anticlockwise rotations"] == 2  # 20 and 9 counts
    assert res[f"{g}: half rotations"] == 14 and res[f"{g}: quarter rotations"] == 29
    assert res[f"{g}: min rate (rev/min)"] == 0  # it stopped while the animal was in A
    assert res[f"{g}: mean rate (rev/min)"] == pytest.approx(29 / 4 / (4 / 60), abs=0.5)
    assert res[f"{g}: mean rate while turning (rev/min)"] == pytest.approx(29 / 4 / (3 / 60), abs=0.5)


def test_analogue_signal_and_pump_in_zones():
    # 9.4 / 9.5 / 9.14 / 9.15 in zones, p. 111-115: the times of the max / min and the integrals above / below the
    # baseline while the animal was in the zone; 17.1 / 17.2 in zones, p. 135: the volume infused in the zone
    app = box_app(Zone("A", rect(0, 0, 200, 400)))
    pts = np.vstack([hold((100, 200), 100), hold((300, 200), 100)])  # in A for 0-4 s of 8 s
    ev = [E(0.0, "sig", 10, typ="analog"), E(0.5, "sig", 10, typ="analog"), E(2.0, "sig", 30, typ="analog"),
          E(5.0, "sig", 50, typ="analog"), E(6.0, "sig", 0, typ="analog"),
          E(1.0, "p1", 1, kind="output", dev="pumps", typ="pump", direction="infuse", rate=6.0),
          E(5.0, "p1", 0, kind="output", dev="pumps", typ="pump")]
    s = AnalysisSettings(**{**S.to_dict(), "io_baseline_s": 1.0})
    res = analyse(make_track(pts, head=False), app, s, io_events=ev)
    assert res["sig: baseline"] == 10 and res["sig: integral above baseline"] == pytest.approx(20 * 3 + 40)
    assert res["sig in A: time of max (s)"] == 2.0 and res["sig in A: time of min (s)"] == 0.0
    assert res["sig in A: integral above baseline"] == pytest.approx(20 * 2, abs=0.01)  # 30 from 2 s to 4 s
    assert res["sig in A: integral below baseline"] == 0 and res["sig: integral below baseline"] == pytest.approx(20)
    assert res["Pump p1: volume infused (ml)"] == pytest.approx(0.4)
    assert res["Pump p1 in A: volume infused (ml)"] == pytest.approx(0.3, abs=0.005)  # 1-4 s at 0.1 ml/s
    assert res["Pump p1 in A: volume withdrawn (ml)"] == 0


def test_rapc_doors():
    # 2.54-2.56, p. 37-38: 12 switch inputs with the indices 1-12; the last door opened in each chamber is the
    # unlatched one: type 1 errors open latched doors, type 2 errors open the unlatched door without going through
    cfg = [{"name": "box", "channels": [{"name": f"d{i}", "kind": "input", "index": i} for i in range(1, 13)]}]
    opens = [(1, 2), (2, 1), (3, 1), (4, 6), (5, 8), (6, 7), (7, 8), (8, 10)]  # (time, door)
    ev = []
    for t, d in opens:
        ev += [E(float(t), f"d{d}", 1), E(t + 0.5, f"d{d}", 0)]
    m = io_measures(ev, 10.0, devices=cfg)
    assert m["RAPC: door sequence"] == "1321"  # the example of the reference
    assert m["RAPC: type 1 errors"] == 2 and m["RAPC: type 2 errors"] == 2
    early = io_measures(ev, 10.0, t_range=(0.0, 4.0), devices=cfg)
    assert early["RAPC: type 1 errors"] == 1 and early["RAPC: type 2 errors"] == 1
    eleven = [{"name": "box", "channels": cfg[0]["channels"][:11]}]
    assert "RAPC: door sequence" not in io_measures(ev, 10.0, devices=eleven)  # only with all 12 doors


def test_movement_detector_does_not_count_repeated_breaks_of_a_beam():
    # 11.1-11.3, p. 120-121: repeated breaks of the same beam are not counted; a movement lasts the detector's
    # time-out after a break; beams already broken at the test start do not count
    cfg = [{"name": "box", "channels": [{"name": f"b{i}", "kind": "input", "detector": "Cage", "timeout_s": 1.0}
                                        for i in (1, 2)]}]
    ev = [E(0.0, "b2", 1), E(0.5, "b2", 0),  # broken when the test starts
          E(1.0, "b1", 1), E(1.2, "b1", 0), E(1.5, "b1", 1), E(1.7, "b1", 0),  # the same beam twice: one movement
          E(2.0, "b2", 1), E(2.2, "b2", 0), E(5.0, "b1", 1), E(5.2, "b1", 0)]
    m = io_measures(ev, 10.0, devices=cfg)
    g = "Movement detector Cage"
    assert m[f"{g}: movements"] == 3 and m[f"{g}: latency to first movement (s)"] == 1.0
    assert m[f"{g}: time moving (s)"] == pytest.approx(3.0)  # 1-3 s (extended by the break at 2 s) and 5-6 s


def test_event_measures():
    # 21.1 / 21.2, p. 144: the number of events and the latency to the first, undefined if it never occurred
    from manymaze.core.procedures import ProcedureEngine

    when = {"type": "when", "event": "zone_enter", "zone": "A", "mode": "parallel", "record_as": "Entered A",
            "body": []}
    eng = ProcedureEngine([{"name": "P", "enabled": True, "statements": [when]}])
    eng.start(0.0)
    for i in range(0, 126):
        t = i / 25
        eng.update_state(t, {"zones": {"A": 1.0 <= t < 2.0 or 3.0 <= t < 4.0}})
    eng.stop(5.0)
    assert [(e["t"], e["kind"]) for e in eng.io_events if e["channel"] == "Entered A"] == [(1.0, "event"),
                                                                                         (3.0, "event")]
    m = io_measures(eng.io_events, 5.0)
    assert m["Event Entered A: count"] == 2 and m["Event Entered A: latency (s)"] == 1.0
    late = io_measures(eng.io_events, 5.0, t_range=(4.0, 5.0), settings=AnalysisSettings())
    assert late["Event Entered A: count"] == 0 and math.isnan(late["Event Entered A: latency (s)"])
