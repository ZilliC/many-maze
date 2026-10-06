"""Statistics page: explicit tests, post-hoc choices, one-sample tests, mixed / non-parametric two-factor designs,
three-level grouping, regression and categorical tests."""

import shutil

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=2, seconds=6)
    p.analysis.bin_length_s = 2.0
    p.save()
    return d


@pytest.fixture
def page(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    p = w.goto("StatisticsPage")
    p.wait_loaded()
    yield p
    w.dirty = False
    w.close()


def test_methods_posthoc_and_one_sample(page):
    page.set_inputs(measure="Total distance (cm)", factor="Period", paired=False, method="anova",
                    posthoc="holm")
    res = page.result
    assert res["test"] == "One-way ANOVA" and len(res["posthoc"]) == 3
    assert all("Holm" in x["test"] for x in res["posthoc"]) and page.posthoc_table.rowCount() == 3
    page.set_inputs(method="welch_anova", posthoc="games_howell")
    assert page.result["test"] == "Welch's ANOVA" and page.result["posthoc"][0]["test"] == "Games-Howell"
    page.set_inputs(method="kruskal", posthoc="dunn")
    assert page.result["posthoc"][0]["test"].startswith("Dunn")
    page.set_inputs(method="anova", posthoc="dunnett", control="2-4 s")
    assert page.control.isEnabled() and len(page.result["posthoc"]) == 2
    assert all(x["b"] == "2-4 s" for x in page.result["posthoc"])
    page.set_inputs(method="rm_anova", posthoc="auto")
    assert page.result["test"] == "Repeated-measures ANOVA" and "p_gg" in page.result
    assert "Greenhouse" in page.test_lbl.text() and "Greenhouse" in page.summary()
    page.set_inputs(method="friedman")
    assert page.result["test"] == "Friedman"
    # assumption checks include normality and variance tests
    checks = [page.assume_table.item(i, 0).text() for i in range(page.assume_table.rowCount())]
    assert any("Shapiro" in c for c in checks) and any("Bartlett" in c for c in checks)
    # one-sample test vs a reference value per group
    page.set_inputs(factor="Group", period="Whole test", method="one_t", mu=0.0)
    assert page.mu.isEnabled() and set(page.result["one_sample"]) == {"Control", "Anxious"}
    assert page.result["one_sample"]["Control"]["test"] == "One-sample t-test" and "vs 0" in page.summary()
    # graph options
    for kind in ("bar", "point", "box", "violin"):
        page.set_inputs(method="auto", plot=kind, error="ci", points=False)
        assert page.cmp_canvas.figure.axes
    page.set_inputs(plot="bar")
    assert "95% CI" in page.cmp_canvas.figure.axes[0].get_ylabel()


def test_two_factor_designs(page):
    page.tabs.setCurrentIndex(1)
    page.set_inputs(measure="Total distance (cm)", factor="Group", method="auto", tc_x="Period", tc_by="Group",
                    design="mixed")
    a = page.anova
    assert a["design"] == "mixed" and [e["effect"] for e in a["effects"]] == ["Group", "Period", "Group × Period"]
    assert page.anova_table.rowCount() == 3 and page.anova_table.item(1, 6).text()  # GG-corrected p
    assert "Mixed two-way ANOVA" in page.summary()
    page.set_inputs(design="srh")
    assert page.anova["design"] == "srh" and page.anova_table.horizontalHeaderItem(3).text() == "H"
    page.set_inputs(design="art", tc_plot="bar")
    assert page.anova["design"] == "art" and page.tc_canvas.figure.axes
    page.set_inputs(design="mixed", tc_by="(none)", tc_plot="line")
    assert page.anova["test"] == "Repeated-measures ANOVA" and page.anova_table.rowCount() == 1
    page.set_inputs(design="between", tc_by="Group")
    assert "two-way ANOVA" in page.summary()


def test_grouped_three_levels(page, tmp_path):
    page.tabs.setCurrentIndex(3)
    page.set_inputs(measure="Total distance (cm)", f1="Period", f2="Group", f3="(none)",
                    plot="bar", error="sem")
    assert len(page.grouped) == 6  # 3 periods × 2 groups
    assert page.grp_table.rowCount() == 6 and page.grp_table.horizontalHeaderItem(0).text() == "Period"
    page.set_inputs(f3="Stage")
    assert len(page.grouped) == 6 and len(page.grp_canvas.figure.axes) == 1  # one stage → one panel
    page.set_inputs(f2="Sex" if "Sex" in page.factors() else "(none)", plot="violin")
    assert page.grp_canvas.figure.axes
    page.copy_grouped()
    assert QApplication.clipboard().text().splitlines()[0].startswith("Period")
    out = page.save_grouped(str(tmp_path / "g.csv"))
    assert open(out).readline().startswith("Period")
    assert "Total distance (cm) by Period" in page.summary()


def test_regression_and_categorical(page):
    page.tabs.setCurrentIndex(2)
    page.set_inputs(corr_x="Centre: time (%)", corr_y="Thigmotaxis (%)", corr_method="kendall", corr_by="(none)")
    assert page.corr["method"] == "kendall" and page.reg["slope"] == page.reg["slope"]
    assert "Linear regression" in page.corr_lbl.text() and "Kendall" in page.summary()
    assert page.corr_canvas.figure.axes[0].collections  # points + confidence band
    # categorical: add a text result to the loaded rows (e.g. a search strategy)
    page.rows = [dict(r, **{"Search strategy": "Direct" if r["Group"] == "Control" else "Thigmotaxis"})
                 for r in page.rows]
    page._populate_controls()
    page.tabs.setCurrentIndex(4)
    page.set_inputs(cat_rows="Group", cat_col="Search strategy", period="Whole test", filter=None)
    c = page.cat
    assert c["table"] == [[2, 0], [0, 2]] or c["table"] == [[0, 2], [2, 0]]
    assert "fisher" in c and page.cat_table.rowCount() == 2
    assert "Fisher" in page.cat_lbl.text() and "chi-square" in page.summary()
    assert page.cat_canvas.figure.axes
