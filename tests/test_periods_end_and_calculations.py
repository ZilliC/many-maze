"""Time periods that end at an event (zone entry / exit, mark, input, a calculation's time) instead of after a fixed
duration, and periods that start or end at the time given by a calculation (ANY-maze T0625); a period whose end
never happens ends at the end of the test and is flagged. Also: training criteria on a calculation's result."""

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from manymaze.core.apparatus import Zone
from manymaze.core.calculations import Calculation, check_calculation, plan
from manymaze.core.export import protocol_report
from manymaze.core.geometry import rect
from manymaze.core.measures import all_periods, analyse, analyse_segmented
from manymaze.core.periods import (NO_END_WARNING, anchor_times, check_periods, describe_period, event_periods,
                                   resolve_periods)
from manymaze.core.project import Project
from manymaze.core.workflow import Criterion, evaluate_criteria

from test_zone_measures_extras import S, box_app, hold, line, make_track


def _app():
    return box_app(Zone("Left", rect(0, 0, 150, 400)), Zone("Right", rect(250, 0, 150, 400)))  # (a gap between)


def _track():
    """1 s in Left, 1 s to the right, 1 s in Right, 1 s back to Left, 1 s in Left (5 s at 25 fps)."""
    pts = np.vstack([hold((50, 200), 25), line((50, 200), (350, 200), 25), hold((350, 200), 25),
                     line((350, 200), (50, 200), 25), hold((50, 200), 25)])
    return make_track(pts, head=False)


def _times(anchor, zone, s=S):
    return anchor_times({"anchor": anchor, "zone": zone}, _track(), _app(), s)


# ------------------------------------------------------------------ periods that end at an event
def test_period_ends_at_a_zone_event():
    exit_left, = _times("first_exit", "Left")[:1]
    enter_right, = _times("first_entry", "Right")[:1]
    exits_right = _times("first_exit", "Right")
    assert exit_left < enter_right < exits_right[0] < 5.0
    s = replace(S, event_periods=[
        {"label": "Crossing", "anchor": "first_exit", "zone": "Left", "duration_s": 30,
         "end": {"anchor": "first_entry", "zone": "Right"}},
        {"label": "In Right", "anchor": "first_entry", "zone": "Right",
         "end": {"anchor": "first_exit", "zone": "Right", "offset_s": 0.2}},
        {"label": "Never back", "anchor": "first_entry", "zone": "Right", "end": {"anchor": "first_entry",
                                                                                  "zone": "Nowhere"}},
        {"label": "Old form", "anchor": "first_exit", "zone": "Left", "duration_s": 1, "end": {"anchor": "duration"}},
    ])
    per = {p.label: p for p in resolve_periods(s.event_periods, 5.0, _track(), _app(), s)}
    assert (per["Crossing"].t0, per["Crossing"].t1) == (exit_left, enter_right)  # (the duration is not used)
    assert per["In Right"].t1 == pytest.approx(exits_right[0] + 0.2) and not per["In Right"].no_end
    # the end never happens: the period ends at the end of the test, flagged
    assert per["Never back"].t1 == 5.0 and per["Never back"].no_end
    assert per["Old form"].t1 == pytest.approx(exit_left + 1) and not per["Old form"].no_end
    seg = dict(analyse_segmented(_track(), _app(), s))
    assert seg["Crossing"]["Test duration (s)"] == pytest.approx(enter_right - exit_left, abs=0.05)
    assert seg["Never back"]["Warnings"] == NO_END_WARNING.format(label="Never back", end="entry into Nowhere")
    assert "Warnings" not in seg["Crossing"] and "Warnings" not in seg["Whole test"]
    # all_periods (charts, plots) gives the same bounds, and the flag when resolved
    assert ("Crossing", exit_left, enter_right) in all_periods(_track(), _app(), s)
    assert [p.no_end for p in all_periods(_track(), _app(), s, resolved=True)] == [False, False, True, False]


def test_end_after_the_start_event_occurrences_marks_and_inputs():
    events = [{"behaviour": "Tone", "t": t} for t in (1.0, 2.0, 3.5)]
    io = [{"t": t, "device": "v", "channel": "lever", "kind": "input", "value": v}
          for t, v in ((0.5, 1), (0.6, 0), (1.8, 1), (1.9, 0), (2.8, 1), (2.9, 0))]
    defs = [
        # from each tone to the next one (the end is searched after the start event)
        {"label": "Tone", "anchor": "mark", "behaviour": "Tone", "occurrence": 0,
         "end": {"anchor": "mark", "behaviour": "Tone"}},
        # from the first tone to the 2nd lever press after it
        {"label": "Presses", "anchor": "mark", "behaviour": "Tone", "end": {"anchor": "input", "channel": "lever",
                                                                           "occurrence": 2}},
        # a negative start offset: the end is still after the start event
        {"label": "Before", "anchor": "mark", "behaviour": "Tone", "occurrence": 2, "offset_s": -1.5,
         "end": {"anchor": "mark", "behaviour": "Tone"}},
    ]
    got = event_periods(defs, 5.0, events=events, io_events=io)
    assert got == [("Tone #1", 1.0, 2.0), ("Tone #2", 2.0, 3.5), ("Tone #3", 3.5, 5.0), ("Presses", 1.0, 2.8),
                   ("Before", 0.5, 3.5)]
    assert [p.no_end for p in resolve_periods(defs, 5.0, events=events, io_events=io)] == [
        False, False, True, False, False]


# ------------------------------------------------------------------ periods defined by calculations
def test_periods_start_and_end_at_a_calculation():
    app, tr = _app(), _track()
    calcs = [Calculation("Reached right", "{Right: latency to first entry (s)}", 3, units="s"),
             Calculation("Half way", "{Test duration (s)} / 2", 2),
             Calculation("Never", "#N/A", 2),
             Calculation("Distance there", "result_for_period({Total distance (cm)}, 'After reaching')", 2)]
    s = replace(S, event_periods=[
        {"label": "After reaching", "anchor": "calculation", "calculation": "Reached right (s)", "duration_s": 0},
        {"label": "First half", "anchor": "start", "end": {"anchor": "calculation", "calculation": "Half way"}},
        {"label": "Undefined start", "anchor": "calculation", "calculation": "Never"},
        {"label": "Undefined end", "anchor": "start", "end": {"anchor": "calculation", "calculation": "Never"}},
    ])
    whole = analyse(tr, app, s, calculations=calcs)
    t_right = whole["Right: latency to first entry (s)"]
    assert whole["Reached right (s)"] == pytest.approx(t_right) and 1 < t_right < 2
    seg = dict(analyse_segmented(tr, app, s, calculations=calcs))
    assert list(seg) == ["Whole test", "After reaching", "First half", "Undefined end"]  # undefined start: left out
    assert seg["After reaching"]["Test duration (s)"] == pytest.approx(5.0 - t_right, abs=0.05)
    assert seg["First half"]["Test duration (s)"] == pytest.approx(2.5, abs=0.05)
    assert seg["Undefined end"]["Test duration (s)"] == pytest.approx(5.0) and "Warnings" in seg["Undefined end"]
    # result_for_period() of a period defined by another calculation (listed before it): worked out after it
    there = analyse(tr, app, s, t_range=(t_right, 5.0))["Total distance (cm)"]
    assert whole["Distance there"] == pytest.approx(there, abs=0.01) and there > 20
    assert [st.calc.name for st in plan(calcs, periods=s.event_periods)].index("Distance there") > 0
    # the same when only part of the test is analysed (the whole test's calculations are worked out for it)
    part = analyse(tr, app, s, t_range=(0, 1), calculations=calcs)
    assert part["Distance there"] == pytest.approx(there, abs=0.01)


def test_a_period_cannot_be_defined_by_a_calculation_that_uses_it():
    app, tr = _app(), _track()
    loop = Calculation("Start", "result_for_period({Total distance (cm)}, 'Late') / 10", 2)
    other = Calculation("Uses start", "{Start} * 2", 2)
    via = Calculation("Via", "{Late distance}", 2)
    late = Calculation("Late distance", "result_for_period({Total distance (cm)}, 'Late 2')", 2)
    s = replace(S, event_periods=[{"label": "Late", "anchor": "calculation", "calculation": "Start"},
                                  {"label": "Late 2", "anchor": "calculation", "calculation": "Via"},
                                  {"label": "Fine", "anchor": "start", "duration_s": 1}])
    calcs = [loop, other, via, late]
    assert check_calculation(loop, calculations=calcs, periods=s.event_periods) == [
        "circular reference: the time period “Late” is defined by this calculation's result (directly or through "
        "other calculations)"]
    assert check_calculation(via, calculations=calcs, periods=s.event_periods) == [
        "circular reference: the formula uses (through other calculations) a time period defined by this "
        "calculation's result"]
    assert check_calculation(loop, calculations=calcs) == []  # (without the periods, nothing to see)
    assert check_calculation(other, calculations=calcs, periods=s.event_periods) == [
        "it uses a calculation in a circular reference through a time period (its result is blank)"]
    problems = check_periods(s.event_periods, calcs)
    assert [label for label, _ in problems] == ["Late", "Late 2"]
    assert problems[0][1].startswith("circular reference: “Start” uses this time period")
    # worked out anyway: the calculations in the loop are blank and their periods left out (no endless recursion)
    seg = dict(analyse_segmented(tr, app, s, calculations=calcs))
    assert list(seg) == ["Whole test", "Fine"]
    assert all(math.isnan(seg["Whole test"][c.column]) for c in calcs)


def test_period_calculation_checks():
    calcs = [Calculation("Across trials", "mean_trials({Total distance (cm)})", 2),
             Calculation("By trial", "{Trial} * 10", 0), Calculation("Fine", "{Test duration (s)} / 2", 1)]
    defs = [{"label": "A", "anchor": "calculation", "calculation": "Across trials"},
            {"label": "B", "anchor": "start", "end": {"anchor": "calculation", "calculation": "By trial"}},
            {"label": "C", "anchor": "calculation", "calculation": "Missing"},
            {"label": "D", "anchor": "calculation", "calculation": "Fine"}]
    problems = dict(check_periods(defs, calcs, info=["Trial"]))
    assert set(problems) == {"A", "B", "C"}
    assert "worked out from the test's own results" in problems["A"] and "information" in problems["B"]
    assert problems["C"] == "no calculation called “Missing”"
    assert describe_period(defs[1]) == "Starts at Test start; until the time given by By trial, else the end of " \
                                       "the test"
    assert describe_period({"label": "x", "anchor": "first_exit", "zone": "Start", "offset_s": 2,
                            "duration_s": 30}) == "Starts at Exit from a zone “Start” (occurrence 1) + 2 s; lasts 30 s"


# ------------------------------------------------------------------ in the experiment
def _project(tmp_path) -> Project:
    p = Project(name="Periods", test_duration_s=0.0)
    p.path = tmp_path / "per.mmaze"
    p.path.mkdir()
    p.analysis = replace(S)
    p.apparatus.append(_app())
    p.stages = ["Day 1"]
    for aid in ("A1", "A2"):
        p.ensure_animal(aid, "G")
        for trial in (1, 2, 3):
            t = p.add_test(animal_id=aid, stage="Day 1", trial=trial)
            p.save_tracks(t, [_track()])
    return p


def test_project_results_persistence_rename_and_report(tmp_path):
    p = _project(tmp_path)
    p.calculations = [Calculation("Reached", "{Right: latency to first entry (s)}", 3),
                      Calculation("Share there", "result_for_period({Total distance (cm)}, 'There') / "
                                                 "{Total distance (cm)}", 3),
                      Calculation("Mean share", "mean_trials({Share there})", 3)]
    p.analysis.event_periods = [
        {"label": "There", "anchor": "calculation", "calculation": "Reached", "end": {"anchor": "first_exit",
                                                                                    "zone": "Right"}},
        {"label": "Back", "anchor": "first_exit", "zone": "Right", "end": {"anchor": "first_entry", "zone": "Right"}}]
    rows = p.results(segmented=True)
    whole = [r for r in rows if r["Period"] == "Whole test"]
    there = [r for r in rows if r["Period"] == "There"]
    back = [r for r in rows if r["Period"] == "Back"]
    assert len(whole) == len(there) == len(back) == 6
    assert there[0]["Test duration (s)"] == pytest.approx(
        _times("first_exit", "Right")[0] - whole[0]["Reached"], abs=0.05)
    assert "the end of the period (entry into Right) did not happen" in back[0]["Warnings"]
    assert 0 < whole[0]["Share there"] < 1 and whole[0]["Mean share"] == pytest.approx(whole[0]["Share there"])
    # one test, and the charts' periods
    one = p.analyse_test(p.tests[0], segmented=True)
    assert [r["Period"] for r in one] == ["Whole test", "There", "Back"]
    tr = p.load_tracks(p.tests[0])[0]
    assert [x[0] for x in p.test_periods(p.tests[0], tr)] == ["There", "Back"]
    # saved in project.json as written; older experiments (no "end") still open
    p.save()
    q = Project.load(p.path)
    assert q.analysis.event_periods == p.analysis.event_periods
    d = json.loads((p.path / "project.json").read_text())
    d["analysis"]["event_periods"] = [{"label": "Old", "anchor": "first_exit", "zone": "Left", "duration_s": 1}]
    (p.path / "project.json").write_text(json.dumps(d))
    old = Project.load(p.path)
    assert [r["Period"] for r in old.analyse_test(old.tests[0], segmented=True)] == ["Whole test", "Old"]
    # renaming a calculation: the periods and criteria follow; what uses it is listed
    p.training_criteria = [{"stage": "Day 1", "measure": "Reached", "op": "<", "value": 5}]
    p.calculations.append(Calculation("Twice", "2 * {Reached}", 1))
    assert p.calculation_users("Reached") == ["calculation “Twice”", "time period “There”",
                                              "training criterion of stage “Day 1”"]
    p.rename_calculation("Reached", "Arrived")
    assert p.analysis.event_periods[0]["calculation"] == "Arrived" and p.training_criteria[0]["measure"] == "Arrived"
    assert p.calculations[-1].formula == "2 * {Arrived}"
    # the protocol report describes the periods
    out = protocol_report(p, tmp_path / "protocol.html")
    text = out.read_text(encoding="utf-8")
    assert "<h2>Time periods</h2>" in text and "until exit from Right, else the end of the test" in text


def test_io_only_periods_end_at_an_input_and_start_at_a_calculation(tmp_path):
    p = Project(name="Operant", test_duration_s=10.0)
    p.save(tmp_path / "op.mmaze")
    p.settings_extra["mode"] = "io_only"
    t = p.add_test("", "R1", "", duration_s=10.0, status="scored",
                   io_events=[{"t": x, "device": "box", "channel": ch, "kind": "input", "value": v}
                              for x, ch, v in ((1.0, "lever", 1), (1.1, "lever", 0), (4.0, "lever", 1),
                                               (4.1, "lever", 0), (6.0, "poke", 1), (6.2, "poke", 0))])
    p.calculations = [Calculation("First press", "{lever: latency to first activation (s)}", 2),
                      Calculation("Presses after", "result_for_period({lever: activations}, 'Press to poke')", 0)]
    p.analysis.event_periods = [
        {"label": "Press to poke", "anchor": "calculation", "calculation": "First press",
         "end": {"anchor": "input", "channel": "poke"}},
        {"label": "To 3rd press", "anchor": "start", "end": {"anchor": "input", "channel": "lever", "occurrence": 3}}]
    rows = {r["Period"]: r for r in p.analyse_test(t, segmented=True)}
    assert list(rows) == ["Whole test", "Press to poke", "To 3rd press"]
    assert rows["Press to poke"]["Test duration (s)"] == pytest.approx(5.0)
    assert rows["Press to poke"]["lever: activations"] == 2 and rows["Whole test"]["Presses after"] == 2
    assert rows["To 3rd press"]["Test duration (s)"] == pytest.approx(10.0) and rows["To 3rd press"]["Warnings"]


# ------------------------------------------------------------------ training criteria on a calculation
def test_training_criterion_on_a_calculation(tmp_path):
    p = _project(tmp_path)
    for t in p.tests:
        t.status = "tracked"
    # the per-test calculation and one across the animal's trials, as a criterion's measure
    p.calculations = [Calculation("Speed x10", "10 * {Mean speed (cm/s)}", 1),
                      Calculation("Trials so far", "count_trials({Total distance (cm)})", 0)]
    speed = p.analyse_test(p.tests[0])[0]["Speed x10"]
    assert speed > 0
    p.training_criteria = [Criterion("Day 1", "Speed x10", ">", speed - 1, 2).to_dict(),
                           Criterion("Day 1", "Trials so far", ">=", 3, 1).to_dict()]
    rep = evaluate_criteria(p)
    rows = {(r["animal"], r["criterion"].split(":")[1].split()[0]): r for r in rep["rows"]}
    assert rows[("A1", "Speed")]["met"] and rows[("A1", "Speed")]["met_at_trial"] == 2
    assert rows[("A1", "Speed")]["values"] == [speed, speed]
    assert rows[("A2", "Trials")]["met_at_trial"] == 1  # (the animal has 3 trials: Count is 3 in each)
    assert rep["completed"] == {"A1": ["Day 1"], "A2": ["Day 1"]}


def test_zone_anchors_are_flagged_in_io_only_mode():
    defs = [{"label": "After entry", "anchor": "first_entry", "zone": "Start", "duration_s": 10},
            {"label": "To exit", "anchor": "start", "end": {"anchor": "first_exit", "zone": "Start"}},
            {"label": "Lever", "anchor": "input", "input": "lever", "duration_s": 5}]
    assert check_periods(defs, []) == []
    problems = check_periods(defs, [], io_only=True)
    assert [lbl for lbl, _ in problems] == ["After entry", "To exit"]
    assert "left out" in problems[0][1] and "end of the test" in problems[1][1]
