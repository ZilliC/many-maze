"""Statistics page analyses (core.analyses): ordering, pairing, tables, headline and summary text."""

import math

from manymaze.core import analyses as an
from manymaze.core.project import Group, Project


def _project_rows():
    p = Project(name="a")
    p.groups = [Group("Saline", "#111111"), Group("Drug", "#222222")]  # list order, not alphabetical
    p.stages = ["Day 2", "Day 1"]
    rows = []
    for i in range(8):
        g = "Drug" if i % 2 else "Saline"
        for k, st in enumerate(("Day 1", "Day 2")):
            for per, f in (("Whole test", 1.0), ("0-10 s", 0.4), ("10-20 s", 0.6)):
                rows.append({"Test": i * 2 + k, "Animal": f"M{i}", "Group": g, "Stage": st, "Period": per,
                             "Dist": (10 + i + (5 if g == "Drug" else 0) + 3 * k) * f,
                             "Strategy": "Direct" if g == "Saline" else "Search"})
    return p, rows


def test_level_order_and_paired_data():
    p, rows = _project_rows()
    assert an.level_order(p, rows, "Group") == ["Saline", "Drug"]
    assert an.level_order(p, rows, "Stage") == ["Day 2", "Day 1"]
    assert an.level_order(p, rows, "Period") == ["Whole test", "0-10 s", "10-20 s"]
    whole = [r for r in rows if r["Period"] == "Whole test"]
    gv = an.group_data(p, whole, "Dist", "Stage", paired=True)
    assert list(gv) == ["Day 2", "Day 1"] and len(gv["Day 1"]) == 8  # one mean per animal, same animal order
    assert list(an.group_data(p, rows, "Dist", "Period", paired=False)) == ["0-10 s", "10-20 s"]


def test_compare_two_factor_and_others():
    p, rows = _project_rows()
    whole = [r for r in rows if r["Period"] == "Whole test"]
    a = an.compare(p, whole, "Dist", "Group", period="Whole test", filt=("Stage", "Day 1"))
    assert a.result["groups"] == ["Saline", "Drug"] and a.title == "Dist"
    assert a.subtitle == "by Treatment · Whole test · Stage = Day 1 · N = 16"
    assert [t.name for t in a.tables] == ["Descriptive statistics", "Post-hoc comparisons", "Assumption checks"]
    assert a.tables[0].headers[0] == "Treatment" and a.tables[0].cells()[0][:2] == ["Saline", "8"]
    assert a.tables[0].text().startswith("Treatment\tn\tMean")
    assert a.summary_text.startswith("Compared by Group, period: Whole test, only Stage = Day 1\nDist\n")
    assert "Welch" in a.headline and a.figure.axes
    assert an.compare(p, whole, "", "Group") is None
    t = an.two_factor(p, rows, "Dist", "Period", "Group")
    assert [e["effect"] for e in t.result["effects"]] == ["Group", "Period", "Group × Period"]
    assert "two-way ANOVA" in t.summary_text and "residual df" in t.summary_text
    rm = an.two_factor(p, rows, "Dist", "Period", None, design="mixed")
    assert rm.result["test"] == "Repeated-measures ANOVA" and "residual df" not in rm.summary_text
    assert an.two_factor(p, rows, "Dist", "Period", None).result is None
    c = an.correlate(p, whole, "Dist", "Test", "spearman")
    assert c.result["n"] == 16 and math.isfinite(c.result["regression"]["slope"]) and "Spearman rho" in c.summary_text
    g = an.grouped(p, rows, "Dist", ["Period", "Group"])
    assert len(g.result) == 4 and g.tables[0].headers[:2] == ["Period", "Treatment"]
    k = an.categorical(p, whole, "Group", "Strategy", period="Whole test")
    assert k.tables[0].rows == [["Saline", 8, 0, 8], ["Drug", 0, 8, 8]] and "chi-square(1)" in k.summary_text
    assert an.categorical(p, whole, "Group", "Group").result is None


def test_fmt():
    assert an.fmt(1.23456) == "1.235" and an.fmt(123.456) == "123.5" and an.fmt(3) == "3"
    assert an.fmt(math.nan) == "–" and an.fmt((1.0, 2.0)) == "1.000, 2.000" and an.cell("x") == "x"
