"""Saved results reports (Project.reports: named selections of the Data page's spreadsheet), the Statistics page's
saved settings (Project.statistics) and the user-set significance level."""

import csv
import json
import math
from dataclasses import replace

import numpy as np
import pytest

from manymaze import cli
from manymaze.core import analyses, plots
from manymaze.core import stats as st
from manymaze.core.apparatus import Zone
from manymaze.core.export import html_report
from manymaze.core.geometry import rect
from manymaze.core.project import INFO_COLUMNS, OPTIONAL_INFO_COLUMNS, Project
from manymaze.core.reports import (default_report, find_report, report_columns, report_rows, reports_from,
                                   set_default)
from manymaze.core.workflow import copy_protocol

from test_zone_measures_extras import S, box_app, line, make_track


def _project(tmp_path) -> Project:
    """Two treatments of two animals, two stages; animals of "Fast" run twice as far."""
    p = Project(name="Reports", test_duration_s=0.0)
    p.path = tmp_path / "rep.mmaze"
    p.path.mkdir()
    p.analysis = replace(S, bin_length_s=1.0)
    p.apparatus.append(box_app(Zone("Right", rect(200, 0, 200, 400))))
    p.stages = ["Day 1", "Day 2"]
    for k, (aid, group) in enumerate((("A1", "Slow"), ("A2", "Slow"), ("B1", "Fast"), ("B2", "Fast"))):
        p.ensure_animal(aid, group)
        speed = (2 if group == "Fast" else 1) * (1 + 0.1 * k)
        for st_ in p.stages:
            t = p.add_test(animal_id=aid, stage=st_, trial=1)
            p.save_tracks(t, [make_track(line((50, 200), (50 + 60 * speed, 200), 50), head=False)])
    p.save()
    return p


REPORT = {"name": "Distance", "measures": ["Total distance (cm)", "Right: time (s)"],
          "info_columns": ["Test", "Animal", "Group", "Period"], "segmented": True, "period": "0-1 s",
          "treatment": "", "stage": "Day 2", "default": True}


# ------------------------------------------------------------------ the report model
def test_reports_from_project_json_are_cleaned():
    items = [REPORT, {"name": "  "}, "nonsense", {"name": "Distance", "measures": []},
             {"name": "All", "measures": None, "default": True, "period": None, "extra": 1}]
    reps = reports_from(items)
    assert [r["name"] for r in reps] == ["Distance", "All"]  # blank, malformed and repeated names left out
    assert reps[1]["measures"] is None and reps[1]["period"] == "" and "extra" not in reps[1]
    assert [r["default"] for r in reps] == [True, False]  # at most one default
    assert default_report(reps) is reps[0] and find_report(reps, "All") is reps[1]
    set_default(reps, "All")
    assert default_report(reps)["name"] == "All" and not reps[0]["default"]
    set_default(reps, None)
    assert default_report(reps) is None
    assert reports_from(None) == [] and reports_from({"name": "x"}) == []


def test_report_rows_and_columns():
    rows = [{"Test": 1, "Animal": "A1", "Group": "Slow", "Stage": "Day 1", "Period": "Whole test", "Sex": "",
             "Treatment code": "A", "Total distance (cm)": 6.0, "Right: time (s)": 0.0, "Mean speed (cm/s)": 3.0},
            {"Test": 1, "Animal": "A1", "Group": "Slow", "Stage": "Day 1", "Period": "0-1 s", "Sex": "",
             "Treatment code": "A", "Total distance (cm)": 3.0, "Right: time (s)": 0.0, "Mean speed (cm/s)": 3.0},
            {"Test": 2, "Animal": "A1", "Group": "Slow", "Stage": "Day 2", "Period": "0-1 s", "Sex": "",
             "Treatment code": "A", "Total distance (cm)": 4.0, "Right: time (s)": 0.0, "Mean speed (cm/s)": 4.0}]
    rep = reports_from([REPORT])[0]
    shown = report_rows(rep, rows)
    assert [r["Test"] for r in shown] == [2]  # the report's period and stage
    assert report_columns(rep, shown, INFO_COLUMNS, OPTIONAL_INFO_COLUMNS) == [
        "Test", "Animal", "Group", "Period", "Total distance (cm)", "Right: time (s)"]  # in the results' order
    whole = dict(rep, segmented=False, stage="", period="")
    assert [r["Period"] for r in report_rows(whole, rows)] == ["Whole test"]
    # no report: every measure and the usual information columns (empty and optional ones left out)
    assert report_columns(None, rows[:1], INFO_COLUMNS, OPTIONAL_INFO_COLUMNS) == [
        "Test", "Animal", "Group", "Stage", "Total distance (cm)", "Right: time (s)", "Mean speed (cm/s)"]


# ------------------------------------------------------------------ persistence
def test_reports_and_statistics_are_saved_and_optional(tmp_path):
    p = _project(tmp_path)
    p.reports = reports_from([REPORT])
    p.statistics = {"measure": "Total distance (cm)", "factor": "Group", "alpha": 0.01, "posthoc": "holm"}
    p.save()
    d = json.loads((p.path / "project.json").read_text())
    assert d["reports"][0]["name"] == "Distance" and d["statistics"]["alpha"] == 0.01
    q = Project.load(p.path)
    assert q.reports == p.reports and q.statistics == p.statistics
    # older experiments (no "reports" / "statistics") still open, with neither
    del d["reports"], d["statistics"]
    (p.path / "project.json").write_text(json.dumps(d))
    old = Project.load(p.path)
    assert old.reports == [] and old.statistics == {} and "reports" not in old.unknown
    # malformed values are dropped
    d.update(reports="nonsense", statistics=[1, 2])
    (p.path / "project.json").write_text(json.dumps(d))
    bad = Project.load(p.path)
    assert bad.reports == [] and bad.statistics == {}
    # a new experiment based on this protocol gets its reports and statistics settings
    new = copy_protocol(q, Project(name="Copy"))
    assert new.reports == q.reports and new.reports is not q.reports and new.statistics == q.statistics


def test_report_table_and_command_line(tmp_path, capsys):
    p = _project(tmp_path)
    p.reports = reports_from([REPORT, {"name": "Whole", "measures": ["Mean speed (cm/s)"], "treatment": "Fast"}])
    p.save()
    rows, cols = p.report_table("Distance")
    assert {r["Stage"] for r in rows} == {"Day 2"} and {r["Period"] for r in rows} == {"0-1 s"} and len(rows) == 4
    assert cols == ["Test", "Animal", "Group", "Period", "Total distance (cm)", "Right: time (s)"]
    with pytest.raises(KeyError):
        p.report_table("Nope")
    # manymaze project DIR results --report NAME
    out = tmp_path / "whole.csv"
    cli.main(["project", str(p.path), "results", "--report", "Whole", "-o", str(out)])
    with open(out, newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert list(got[0]) == ["Test", "Animal", "Group", "Stage", "Trial", "Apparatus", "Mean speed (cm/s)"]
    assert {r["Group"] for r in got} == {"Fast"} and len(got) == 4
    wide = tmp_path / "wide.csv"
    cli.main(["project", str(p.path), "results", "--report", "Whole", "--wide", "-o", str(wide)])
    assert "Mean speed (cm/s) [Day 2 · 1]" in wide.read_text(encoding="utf-8")
    xlsx = tmp_path / "rep.xlsx"
    cli.main(["project", str(p.path), "results", "--report", "Distance", "-o", str(xlsx)])
    assert xlsx.exists()
    for bad in (["--report", "Nope"], ["--report", "Whole", "--bins"], ["--report", "Whole", "--column", "x"]):
        with pytest.raises(SystemExit) as e:
            cli.main(["project", str(p.path), "results", *bad, "-o", str(tmp_path / "x.csv")])
        assert "report" in str(e.value)
    # manymaze project DIR report --report NAME: the report's tests, rows and measures
    html = tmp_path / "r.html"
    cli.main(["project", str(p.path), "report", "--report", "Whole", "-o", str(html)])
    text = html.read_text(encoding="utf-8")
    assert "Results report: <b>Whole</b>" in text and "<th>Mean speed (cm/s)</th>" in text
    assert "<th>Total distance (cm)</th>" not in text
    # the experiment's terms head the columns of every format, and the statistics name the factors with them
    p.terminology = {"animal": {"singular": "Fish", "plural": "Fish"}, "treatment": {"singular": "Condition"}}
    p.save()
    cli.main(["project", str(p.path), "results", "--report", "Whole", "-o", str(out)])
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("Test,Fish,Condition,")
    cli.main(["project", str(p.path), "results", "--report", "Whole", "--wide", "-o", str(wide)])
    assert "Condition" in wide.read_text(encoding="utf-8").splitlines()[0]
    from manymaze.core import analyses
    assert analyses.label("Group", p) == "Condition" and analyses.label("Animal", p) == "Fish"
    assert analyses.label("Group") == "Treatment" and analyses.label("Stage", p) == "Stage"


# ------------------------------------------------------------------ significance level
def test_stars_follow_the_significance_level():
    assert [st.stars(p) for p in (0.0005, 0.005, 0.03, 0.2)] == ["***", "**", "*", "ns"]  # as before at 0.05
    assert st.stars(0.03, 0.01) == "ns" and st.stars(0.005, 0.01) == "**"
    assert st.stars(0.07, 0.1) == "*" and st.stars(math.nan, 0.1) == ""
    assert st.significance_level("0.01") == 0.01
    assert st.significance_level(0) == st.significance_level("x") == st.significance_level(0.9) == st.ALPHA
    assert analyses.significance_level(Project(statistics={"alpha": 0.001})) == 0.001
    assert analyses.significance_level(Project()) == 0.05


def test_compare_marks_significance_at_alpha():
    rng = np.random.default_rng(3)
    rows = [{"Group": g, "Animal": f"{g}{i}", "m": v} for g, mu in (("A", 0.0), ("B", 0.75))
            for i, v in enumerate(rng.normal(mu, 1.0, 20))]
    a05 = analyses.compare(None, rows, "m", "Group", method="student")
    p = a05.result["p"]
    assert 0.01 < p < 0.05  # (seeded: significant at 0.05, not at 0.01)
    a01 = analyses.compare(None, rows, "m", "Group", method="student", alpha=0.01)
    assert "<b style='color:#16a34a'>*</b>" in a05.headline and ">ns</b>" in a01.headline
    assert "α = 0.01" in a01.subtitle and "α" not in a05.subtitle
    assert "significance level 0.01" in a01.summary_text and " ns" in a01.summary_text
    # the graph's significance bracket only below the level
    assert len(a05.figure.axes[0].texts) == 1 and not a01.figure.axes[0].texts
    fig = plots.group_plot({"A": [1, 2, 3], "B": [7, 8, 9]}, "m", p_value=0.03, alpha=0.01)
    assert not fig.axes[0].texts
    # two factors, correlation and categorical tests star at the level too
    rows2 = [dict(r, Stage=s) for r in rows for s in ("1", "2")]
    tf = analyses.two_factor(None, rows2, "m", "Stage", "Group", alpha=0.0001)
    assert tf.tables[0].rows[0][5] in ("ns", "*", "**", "***") and "α = 0.0001" in tf.subtitle
    xs = [{"x": i, "y": i + (i % 3), "Group": "A"} for i in range(12)]
    assert "α = 0.2" in analyses.correlate(None, xs, "x", "y", alpha=0.2).subtitle


def test_html_report_statistics_use_the_saved_level(tmp_path):
    p = _project(tmp_path)
    p.statistics = {"alpha": 0.0001}
    out = tmp_path / "r.html"
    html_report(p, out, include_plots=False, stats_measures=["Total distance (cm)"])
    assert "significance level 0.0001" in out.read_text(encoding="utf-8")
