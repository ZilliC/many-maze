"""Smoke tests for the Statistics page."""

import math
import shutil

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=3, seconds=8)
    p.analysis.bin_length_s = 4.0
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


def test_compare_groups(page, tmp_path, monkeypatch):
    assert page.rows and "Period" in page.factors()
    assert page.period.currentData() == "Whole test"
    page.set_inputs(measure="Centre: time (%)", factor="Group", period="Whole test", parametric=True)
    res = page.result
    assert res["test"] == "Welch's t-test"
    assert res["groups"] == ["Control", "Anxious"]
    assert res["p"] < 0.05
    assert res["descriptive"]["Control"]["n"] == 3
    assert page.desc_table.rowCount() == 2 and page.assume_table.rowCount() >= 1
    assert "Welch" in page.test_lbl.text()
    assert page.cmp_canvas.figure.axes
    text = page.summary()
    assert "Centre: time (%)" in text and "Welch" in text
    page.copy_summary()
    assert "Welch" in QApplication.clipboard().text()
    out = tmp_path / "cmp.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page.save_figure()
    assert out.stat().st_size > 1000
    # non-parametric + box plot
    page.set_inputs(parametric=False, plot="box")
    assert page.result["test"] == "Mann-Whitney U"
    # filter + paired within-animal comparison of periods
    page.set_inputs(parametric=True, plot="bar", factor="Period", paired=True, filter=("Group", "Control"))
    assert page.result["test"] == "Paired t-test"
    assert page.result["groups"] == ["0-4 s", "4-8 s"]
    assert page.result["descriptive"]["0-4 s"]["n"] == 3


def test_time_course(page):
    page.tabs.setCurrentIndex(1)
    page.set_inputs(measure="Total distance (cm)", tc_x="Period", tc_by="Group", filter=None)
    ax = page.tc_canvas.figure.axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["0-4 s", "4-8 s"]
    assert len(ax.get_legend().get_texts()) == 2
    effects = [e["effect"] for e in page.anova["effects"]]
    assert effects == ["Group", "Period", "Group × Period"]
    assert page.anova_table.rowCount() == 3
    assert "two-way ANOVA" in page.summary()
    page.set_inputs(tc_by="(none)")
    assert page.anova is None and page.tc_canvas.figure.axes


def test_correlation(page):
    page.tabs.setCurrentIndex(2)
    page.set_inputs(corr_x="Centre: time (%)", corr_y="Thigmotaxis (%)", corr_method="spearman")
    c = page.corr
    assert c["n"] == 6 and math.isfinite(c["r"]) and c["method"] == "spearman"
    assert page.corr_canvas.figure.axes
    page.set_inputs(corr_method="pearson")
    assert page.corr["method"] == "pearson" and "Pearson" in page.summary()
