"""Calculations (ANY-maze: Protocol ▸ Analysis ▸ Calculations): results derived from other results with a formula.
The definitions follow ANY-maze's help (topics T0613–T0628: "An introduction to calculations", "Defining the
calculation's formula", the Function wizard); each test names the rule it pins."""

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from manymaze.core import analyses
from manymaze.core.apparatus import Zone
from manymaze.core.calculations import (Calculation, Trials, check_calculation, evaluate, evaluate_calc,
                                        evaluate_test, plan, result_value, round_half_away)
from manymaze.core.export import export_results, export_xml, html_report, protocol_report
from manymaze.core.geometry import rect
from manymaze.core.measures import analyse, analyse_segmented
from manymaze.core.project import Behaviour, Project
from manymaze.core.workflow import copy_protocol

from test_zone_measures_extras import S, box_app, hold, line, make_track


def C(formula, name="Calc", decimals=3, **kw):
    return Calculation(name, formula, decimals, **kw)


def value(formula, row=None, **kw):
    return evaluate_calc(C(formula, **kw), row or {})


# ------------------------------------------------------------------ the formula


def test_discrimination_index_from_two_measures():
    # the NOR guide's example: (novel - familiar) / (novel + familiar)
    row = {"Novel: time investigating (s)": 30.0, "Familiar: time investigating (s)": 10.0}
    f = ("({Novel: time investigating (s)} - {Familiar: time investigating (s)}) / "
         "({Novel: time investigating (s)} + {Familiar: time investigating (s)})")
    assert value(f, row) == 0.5


def test_operators_comparisons_and_named_values():
    row = {"Zone A: entries": 5, "Zone A: time (s)": 12.5}
    # comparison operators return 1 for true and 0 for false; ANY-maze writes "=" for equality
    assert value("{Zone A: entries} = 5", row) == 1
    assert value("{Zone A: entries} > 7", row) == 0
    # named values (T0620): (Number of entries to Zone A > Limit) * Time in Zone A
    calc = C("({Zone A: entries} > Limit) * {Zone A: time (s)}", named_values=[["Limit", 3]])
    assert evaluate_calc(calc, row) == 12.5
    calc.named_values = [["Limit", 6]]
    assert evaluate_calc(calc, row) == 0
    # the usual precedence (ANY-maze works left to right: 2 + 3 * 4 would be 20 there)
    assert value("20 / 2 + 3") == 13 and value("2 + 3 * 4") == 14
    assert value("{x} != 1 and {x} >= 2", {"x": 2}) == 1  # (==, <= and >= are left alone)


def test_undefined_values_make_the_result_undefined():
    # T0619: a measure without a numeric result (or a deleted zone) makes the formula's result undefined
    row = {"A": math.nan, "B": 2.0, "C": "", "D": None}
    for f in ("{A} + {B}", "{B} + {C}", "{B} + {D}", "{Not a measure} * 2", "{B} / 0", "sqrt(0 - {B})"):
        assert math.isnan(value(f, row)), f
    assert value("is_undefined({A}) + is_undefined({Not a measure})", row) == 2
    assert value("#N/A if {B} > 1 else 1", row) != value("#N/A if {B} > 1 else 1", row)  # NaN


def test_decimal_places_round_and_results_types():
    # T0617: rounded (not truncated) to the decimal places, 0 to 9
    assert value("2 / 3", decimals=2) == 0.67
    assert value("2.5", decimals=0) == 3 and isinstance(value("2.5", decimals=0), int)  # half away from zero
    assert value("0 - 2.5", decimals=0) == -3
    assert round_half_away(1.005, 2) == 1.01  # (round() gives 1.0: 1.005 is 1.00499… in binary)
    assert result_value(True) == 1 and result_value("x" * 500) == "x" * 200 and math.isnan(result_value([1]))
    assert value("'high' if {x} > 3 else 'low'", {"x": 5}) == "high"  # a text (category) result
    assert math.isnan(value("1e308 * 10"))
    assert C("x", units="%").column == "Calc (%)" and C("x").column == "Calc"


def test_check_reports_mistakes():
    def errs(formula, **kw):
        return check_calculation(C(formula, **kw))

    assert errs("({A} - {B}) / {C}") == []
    assert errs("") == ["enter a formula"]
    assert errs("{A} +")[0].startswith("syntax error")
    assert "unknown name “Total”" in errs("Total * 2")[0]
    assert errs("time() + 1") == ["time() cannot be used in a calculation (only in procedures)"]
    assert errs("random()") == ["random() cannot be used in a calculation (only in procedures)"]
    assert "must be a measure in braces" in errs("sum_trials(3)")[0]
    assert "wrong arguments" in errs("sum_trials({A}, 'Day 1', 2)")[0]
    assert "cannot use measures" in errs("result_for_period({A}, {B}, 60)")[0]
    assert errs("{A}", units="123456789") == ["units can have at most 8 characters"]
    assert errs("{A}", y_max=0.0, y_min=1.0) == ["the Y axis maximum must be greater than its minimum"]
    assert errs("{A} * k", named_values=[["2k", 1.0]])[0].startswith("named value “2k”")
    assert "needs a number" in errs("{A} * k", named_values=[["k", None]])[0]
    assert "unknown name “k”" in errs("{A} * k", named_values=[["k", None]])[1]
    # measures that are not in the results, the calculation's own result, info columns, duplicate names
    assert check_calculation(C("{A} + {B}"), measures=["A"]) == ["no measure called “B” in the results"]
    assert check_calculation(C("{A} * {Trial}"), measures=["A"], reserved=["Trial"]) == []
    assert check_calculation(C("{Calc} + 1")) == ["the formula uses its own result"]
    assert check_calculation(C("{A}", name="Animal"), reserved=["Animal"])[0].startswith("“Animal” is an inform")
    assert check_calculation(C("{A} * 2", name="B"), measures=["A", "B"]) == [
        "a measure is called “B”: the calculation's result would replace it"]
    a, b, c = C("{b} + 1", "a"), C("{a} + 1", "b"), C("{X}", "a")
    assert check_calculation(a, calculations=[a, b]) == ["circular reference: the calculations use each "
                                                         "other's results"]
    assert check_calculation(c, calculations=[a, c]) == ["another calculation is called “a”"]


def test_calculations_use_each_other_in_dependency_order():
    a, b, c = C("{b} * 2", "a"), C("{X} + 1", "b"), C("sum_trials({a})", "c")
    loop1, loop2 = C("{loop2}", "loop1"), C("{loop1}", "loop2")
    steps = plan([a, b, c, loop1, loop2])
    assert [(s.calc.name, s.deferred, s.ok) for s in steps] == [
        ("b", False, True), ("a", False, True), ("c", True, True), ("loop1", False, False), ("loop2", False, False)]
    got = evaluate_test([a, b, c, loop1, loop2], {"X": 1})
    assert list(got) == ["a", "b", "c", "loop1", "loop2"]  # the order of the list
    assert got["a"] == 4 and got["b"] == 2
    assert all(math.isnan(got[k]) for k in ("c", "loop1", "loop2"))
    # information columns are only known to the project: a calculation using one is deferred
    assert [s.deferred for s in plan([C("{Trial} * 2")], info={"Trial"})] == [True]


def test_trial_functions():
    # Function wizard (T0622, T0626): Count counts the trials with a result, Min/Max/Sum/Mean leave out undefined
    # results, a range the animal has no trial in is undefined, ResultForLastTrial is the last trial of a stage
    rows = [("Acq", 1, {"X": 1.0}), ("Acq", 2, {"X": math.nan}), ("Acq", 3, {"X": 4.0}), ("Probe", 1, {"X": 10.0})]
    tr = Trials(rows, ["Acq", "Probe"])

    def f(formula):
        return evaluate_calc(C(formula), {}, trials=tr)

    assert f("count_trials({X})") == 3
    assert f("sum_trials({X})") == 15 and f("sum_trials({X}, 'Acq')") == 5
    assert f("mean_trials({X}, 'Acq', 1, 2)") == 1 and f("min_trials({X}, 'Acq', 2, 'Probe', 1)") == 4
    assert f("max_trials({X}, 'Acq', 2, 'Probe', 1)") == 10
    assert math.isnan(f("sum_trials({X}, 'Acq', 4, 9)"))  # no trial in the range: undefined
    assert f("count_trials({X}, 'Acq', 2, 2)") == 0  # a trial, without a result
    assert f("result_for_trial({X}, 'Acq', 3)") == 4 and f("result_for_last_trial({X}, 'Acq')") == 4
    assert math.isnan(f("result_for_trial({X}, 'Acq', 7)")) and math.isnan(f("result_for_trial({X}, 'No', 1)"))
    # without the other trials (one test analysed on its own) the functions are undefined
    assert math.isnan(evaluate_calc(C("sum_trials({X})"), {"X": 1}))


# ------------------------------------------------------------------ in the analysis of a test


def _walk():
    # 2 s along y = 200 from x = 50 to 350 (30 cm at 10 px/cm), then 2 s still
    return make_track(np.vstack([line((50, 200), (350, 200), 50), hold((350, 200), 50)]), head=False)


def test_analyse_adds_calculations_and_result_for_period():
    app = box_app(Zone("Right", rect(200, 0, 200, 400)))
    calcs = [C("100 * {Right: time (s)} / {Test duration (s)}", "Right share", 1, units="%"),
             C("result_for_period({Total distance (cm)}, 0, 1)", "First second", 2),
             C("result_for_period({Total distance (cm)}, 2, 60) + 1", "Still", 2),
             C("result_for_period({Total distance (cm)}, 10, 20)", "After the end", 2),
             C("result_for_period({Total distance (cm)}, '0-2 s')", "Bin", 2),
             C("sum_trials({Total distance (cm)})", "Trials", 2)]
    s = replace(S, bin_length_s=2.0)
    res = analyse(_walk(), app, s, calculations=calcs)
    whole = analyse(_walk(), app, s)
    assert list(res)[-6:] == ["Right share (%)", "First second", "Still", "After the end", "Bin", "Trials"]
    assert res["Right share (%)"] == pytest.approx(round(100 * whole["Right: time (s)"] / whole["Test duration (s)"],
                                                          1))
    first = analyse(_walk(), app, s, t_range=(0, 1))["Total distance (cm)"]
    assert res["First second"] == pytest.approx(first, abs=0.01) and 13 < first < 16
    assert res["Still"] == pytest.approx(1.0, abs=0.01)  # the period runs on to the end of the test (4 s)
    assert math.isnan(res["After the end"])  # the test ended before the period: undefined
    assert res["Bin"] == pytest.approx(analyse(_walk(), app, s, t_range=(0, 2))["Total distance (cm)"], abs=0.01)
    assert math.isnan(res["Trials"])  # needs the experiment: worked out by Project.results
    # in each time period, the calculation uses that period's measures; result_for_period is in test time
    seg = dict(analyse_segmented(_walk(), app, s, calculations=calcs))
    assert seg["2-4 s"]["Right share (%)"] == 100.0 and seg["2-4 s"]["First second"] == res["First second"]
    # the measure filter keeps the calculations, which still see every measure
    filt = analyse(_walk(), app, replace(s, measure_filter=["Total distance (cm)"]), calculations=calcs)
    assert filt["Right share (%)"] == res["Right share (%)"] and "Right: time (s)" not in filt


def _project(tmp_path, stages=("Acquisition", "Probe"), trials=3) -> Project:
    """Two animals, a test per stage and trial; animal A2 runs twice as far, trial n runs n times as far."""
    p = Project(name="Calc", test_duration_s=0.0)
    p.path = tmp_path / "calc.mmaze"
    p.path.mkdir()
    p.analysis = replace(S)
    p.apparatus.append(box_app(Zone("Right", rect(200, 0, 200, 400))))
    p.stages = list(stages)
    for aid, speed in (("A1", 1), ("A2", 2)):
        p.ensure_animal(aid, "G")
        for st in stages:
            for trial in range(1, trials + 1):
                t = p.add_test(animal_id=aid, stage=st, trial=trial)
                p.save_tracks(t, [make_track(line((50, 200), (50 + 40 * trial * speed, 200), 50), head=False)])
    return p


def _by(rows, col):
    return {(r["Animal"], r["Stage"], r["Trial"]): r[col] for r in rows}


def test_project_results_across_trials(tmp_path):
    p = _project(tmp_path)
    dist = _by(p.results(), "Total distance (cm)")
    p.calculations = [C("sum_trials({Total distance (cm)}, 'Acquisition')", "Acquisition total", 2),
                      C("{Total distance (cm)} / result_for_trial({Total distance (cm)}, 'Acquisition', 1)",
                        "Relative to trial 1", 2),
                      C("result_for_last_trial({Total distance (cm)}, 'Acquisition')", "Last", 2),
                      C("mean_trials({Relative to trial 1}, 'Acquisition', 2, 'Probe', 1)", "Mean relative", 2),
                      C("{Trial} * 10", "Trial x10", 0),
                      C("100 * {Right: time (s)} / {Test duration (s)}", "Right share", 1, units="%")]
    rows = p.results()
    cols = list(rows[0])
    assert cols[-6:] == [c.column for c in p.calculations]  # in the order of the list
    for aid in ("A1", "A2"):
        acq = [dist[(aid, "Acquisition", k)] for k in (1, 2, 3)]
        got = [r for r in rows if r["Animal"] == aid]
        assert all(r["Acquisition total"] == pytest.approx(sum(acq), abs=0.01) for r in got)
        assert all(r["Last"] == pytest.approx(acq[-1], abs=0.01) for r in got)
        rel = {(r["Stage"], r["Trial"]): r["Relative to trial 1"] for r in got}
        assert rel[("Acquisition", 2)] == pytest.approx(2.0, abs=0.02)
        # a calculation across trials of another calculation (worked out for every test first)
        want = (rel[("Acquisition", 2)] + rel[("Acquisition", 3)] + rel[("Probe", 1)]) / 3
        assert got[0]["Mean relative"] == pytest.approx(want, abs=0.01)
        assert [r["Trial x10"] for r in got] == [10, 20, 30, 10, 20, 30]
    # the other tests are analysed when only some are asked for (one test, or a subset)
    t = p.tests[1]
    one = p.analyse_test(t)[0]
    assert one["Acquisition total"] == rows[1]["Acquisition total"] and one["Trial x10"] == 20
    sub = p.results(tests=[p.tests[0]])
    assert len(sub) == 1 and sub[0]["Acquisition total"] == rows[0]["Acquisition total"]
    # time periods: the functions use the same period of the other trials
    p.analysis.bin_length_s = 1.0
    seg = [r for r in p.results(segmented=True) if r["Animal"] == "A1" and r["Period"] == "0-1 s"]
    assert seg[0]["Acquisition total"] == pytest.approx(
        sum(r["Total distance (cm)"] for r in seg if r["Stage"] == "Acquisition"), abs=0.01)


def test_deferred_calculation_with_result_for_period(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=2)
    p.calculations = [C("result_for_period({Total distance (cm)}, 0, 1) / mean_trials({Total distance (cm)})",
                        "Early share", 3)]
    rows = p.results()
    for r, t in zip(rows, p.tests):
        early = analyse(p.load_tracks(t)[0], p.apparatus[0], p.analysis, t_range=(0, 1))["Total distance (cm)"]
        mean = np.mean([x["Total distance (cm)"] for x in rows if x["Animal"] == r["Animal"]])
        assert r["Early share"] == pytest.approx(early / mean, abs=1e-3)


def test_scored_only_tests_and_error_rows(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=1)
    p.behaviours = [Behaviour("Groom", "g", "state")]
    t = p.add_test(animal_id="A3", stage="Day 1", trial=1, duration_s=10.0, status="scored",
                   events=[{"behaviour": "Groom", "t": 1.0, "t_end": 3.0}, {"behaviour": "Groom", "t": 6.0,
                                                                          "t_end": 9.0}])
    p.calculations = [C("result_for_period({Groom: duration (s)}, 5, 10)", "Late grooming", 1),
                      C("{Groom: duration (s)} / {Test duration (s)}", "Grooming share", 2)]
    rows = p.results()
    row = next(r for r in rows if r["Test"] == t.id)
    assert "Groom: duration (s)" in row
    assert row["Late grooming"] == 3.0
    # tests without the measure: undefined
    assert math.isnan(next(r for r in rows if r["Test"] != t.id)["Late grooming"])


def test_saved_with_the_experiment_and_old_files_open(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=1)
    p.calculations = [Calculation("Open arms", "100 * {Open: time (s)} / {Test duration (s)}", 1, "%", 100.0, None,
                                  [["Limit", 3.0]])]
    p.save()
    d = json.loads((p.path / "project.json").read_text())
    assert d["calculations"][0] == {"name": "Open arms", "formula": "100 * {Open: time (s)} / {Test duration (s)}",
                                    "decimals": 1, "units": "%", "y_max": 100.0, "y_min": None,
                                    "named_values": [["Limit", 3.0]]}
    assert Project.load(p.path).calculations == p.calculations
    # experiments saved before calculations existed (no key) and malformed entries
    del d["calculations"]
    assert Project.from_dict(d).calculations == []
    d["calculations"] = [{"name": "x", "formula": "1", "decimals": "a", "y_max": "big"}, "junk"]
    c = Project.from_dict(d).calculations
    assert len(c) == 1 and c[0].decimals == 2 and c[0].y_max is None


def test_protocol_copy_report_and_export(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=1)
    p.calculations = [C("{Total distance (cm)} / 100", "Distance", 2, units="m")]
    q = copy_protocol(p, Project())
    assert q.calculations == p.calculations and q.calculations[0] is not p.calculations[0]
    html = protocol_report(p, tmp_path / "protocol.html").read_text()
    assert "<h2>Calculations</h2>" in html and "Distance (m)" in html and "{Total distance (cm)} / 100" in html
    # the protocol settings added later are listed too
    p.start_switch_delay_s, p.require_weight_before_test = 2.5, True
    p.terminology = {"animal": {"singular": "Fish", "plural": "Fish"}}
    p.recording_name_fields = ["animal", "date"]
    p.reports = [{"name": "Main", "measures": None}]
    p.statistics = {"alpha": 0.01}
    p.security = {"reveal_codes": "anyone", "lock_protocol": True}
    html = protocol_report(p, tmp_path / "protocol.html").read_text()
    for row in ("<td>Delay after the start switch (s)</td><td>2.5</td>",
                "<td>Block the test until the animal is weighed</td><td>yes</td>", "<td>Animal → Fish</td>",
                "<td>Saved results reports</td><td>Main</td>", "<td>Significance level (Statistics)</td><td>0.01</td>",
                "<td>Protocol locked</td><td>yes"):
        assert row in html, row
    assert "<td>Recorded video file names</td><td>Fish, " in html
    out = export_results(p, tmp_path / "results.csv")
    header = out.read_text().splitlines()[0]
    assert "Distance (m)" in header


def test_reports_and_xml_work_out_calculations_across_trials(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=2)
    p.calculations = [C("sum_trials({Total distance (cm)})", "Both trials", 1)]
    want = {r["Test"]: r["Both trials"] for r in p.results()}
    assert all(v == v and v > 0 for v in want.values())
    rows = [r for t in p.tests for r in p.analyse_test(t, deferred=False)]
    assert all(math.isnan(r["Both trials"]) for r in rows)
    assert {r["Test"]: r["Both trials"] for r in p.finish_calculations(rows)} == want
    html = html_report(p, tmp_path / "r.html", include_plots=False, measures=["Both trials"]).read_text()
    assert "Both trials" in html and f"<td>{want[1]:g}</td>" in html
    xml = export_xml(p, tmp_path / "e.xml", include_tracks=False).read_text()
    assert f'name="Both trials" value="{want[1]:g}" type="number"' in xml


def test_calculation_named_like_an_information_column_is_ignored(tmp_path):
    p = _project(tmp_path, stages=("Day 1",), trials=1)
    p.calculations = [C("1", "Animal"), C("2", "Ok")]
    rows = p.results()
    assert rows[0]["Animal"] == "A1" and rows[0]["Ok"] == 2


def test_graph_y_axis_range(tmp_path):
    # T0618: a fixed Y axis range for graphs of the calculation, automatic when a result falls outside it
    rows = [{"Group": g, "Animal": f"{g}{i}", "Share (%)": v} for g, vals in (("A", (40, 60)), ("B", (70, 80)))
            for i, v in enumerate(vals)]
    p = Project()
    p.calculations = [C("1", "Share", units="%", y_max=100.0)]
    a = analyses.compare(p, rows, "Share (%)", "Group")
    assert a.figure.axes[0].get_ylim() == (0.0, 100.0)
    rows[0]["Share (%)"] = 140.0
    a = analyses.compare(p, rows, "Share (%)", "Group")
    assert a.figure.axes[0].get_ylim()[1] > 140
    g = analyses.grouped(p, rows[1:], "Share (%)", ["Group"])
    assert g.figure.axes[0].get_ylim() == (0.0, 100.0)
    # measures that are not calculations keep the automatic scale
    a = analyses.compare(Project(), rows[1:], "Share (%)", "Group")
    assert a.figure.axes[0].get_ylim()[1] < 100


def test_evaluate_without_calculations_is_empty():
    assert evaluate([], {"X": 1}) == {} and evaluate_test(None, {}) == {}
