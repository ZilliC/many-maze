"""Saved results reports on the Data page (Report ▾ on the ribbon; the default report shown on opening) and the
Statistics page's settings and significance level saved in the experiment."""

import shutil
import time

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from manymaze.core.calculations import Calculation
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=2, seconds=6)
    p.analysis.bin_length_s = 3.0
    p.save()
    return d


@pytest.fixture
def folder(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    return d


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def _window(folder) -> MainWindow:
    w = MainWindow()
    w.set_project(Project.load(folder))
    return w


def _results(w):
    page = w.goto("ResultsPage")
    page.wait_loaded()
    pump()
    return page


def test_save_restore_and_change_reports(folder, monkeypatch):
    w = _window(folder)
    page = _results(w)
    assert page.report_name is None and page.reports_combo.currentText() == "- None -"
    assert not page.save_report_act.isEnabled() and page.save_report_as_act.isEnabled()
    keep = ["Total distance (cm)", "Mean speed (cm/s)"]
    page.set_visible_measures(keep)
    page.hidden.discard("Frames tracked (%)")  # an optional information column, ticked
    page.seg_check.setChecked(True)
    page.wait_loaded()
    pump()
    page.period_combo.setCurrentIndex(page.period_combo.findData("3-6 s"))
    page.group_combo.setCurrentIndex(page.group_combo.findData("Control"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Speed", True))
    assert page.save_report_as() == "Speed" and w.dirty
    rep = w.project.reports[0]
    assert rep["measures"] == keep and rep["segmented"] and rep["period"] == "3-6 s"
    assert rep["treatment"] == "Control" and "Frames tracked (%)" in rep["info_columns"]
    assert page.reports_combo.currentData() == "Speed" and page.save_report_act.isEnabled()
    page.set_default_report(True)
    assert w.project.reports[0]["default"] and page.reports_combo.currentText() == "Speed (default)"
    assert page.default_report_act.isChecked()
    # exports and the HTML report follow the report shown
    assert page._default_path(".csv").endswith("d results - Speed (time periods).csv")
    assert page.visible_measures() == keep and {r["Period"] for r in page.shown_rows()} == {"3-6 s"}
    assert w.save()
    w.dirty = False
    w.close()

    # opening the experiment shows its default report (Select data no longer resets)
    w = _window(folder)
    page = _results(w)
    assert page.report_name == "Speed" and page.seg_check.isChecked()
    assert page.visible_measures() == keep and "Frames tracked (%)" in page.shown_columns()
    assert {r["Period"] for r in page.shown_rows()} == {"3-6 s"}
    assert {r["Group"] for r in page.shown_rows()} == {"Control"}
    assert not w.dirty
    # a measure that appears later (a new calculation) is not added to a report listing its measures
    w.project.calculations.append(Calculation("Double distance", "2 * {Total distance (cm)}", 1))
    page.reload(force=True)
    page.wait_loaded()
    pump()
    assert "Double distance" in page.measure_columns() and page.visible_measures() == keep
    # a new report shows every measure, whole tests, no filter
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Everything", True))
    assert page.new_report() == "Everything"
    page.wait_loaded()
    pump()
    assert not page.seg_check.isChecked() and "Double distance" in page.visible_measures()
    assert page.group_combo.currentData() is None and {r["Period"] for r in page.shown_rows()} == {"Whole test"}
    page.set_visible_measures(["Double distance"])
    page.save_report()
    assert w.project.reports[1]["measures"] == ["Double distance"]
    # switching between saved reports
    page.reports_combo.setCurrentIndex(page.reports_combo.findData("Speed"))
    page.wait_loaded()
    pump()
    assert page.visible_measures() == keep and page.seg_check.isChecked()
    page.reports_combo.setCurrentIndex(page.reports_combo.findData("Everything"))
    page.wait_loaded()
    pump()
    assert page.visible_measures() == ["Double distance"]
    # deleting the shown report keeps what the spreadsheet shows; Clear settings leaves the report
    assert page.delete_report()
    assert [r["name"] for r in w.project.reports] == ["Speed"] and page.report_name is None
    assert page.visible_measures() == ["Double distance"]
    page.reports_combo.setCurrentIndex(page.reports_combo.findData("Speed"))
    page.wait_loaded()
    pump()
    page.clear_settings()
    assert page.report_name is None and page.reports_combo.currentText() == "- None -"
    w.dirty = False
    w.close()


def test_statistics_settings_and_significance_level(folder):
    w = _window(folder)
    page = w.goto("StatisticsPage")
    page.wait_loaded()
    assert page.alpha.value() == 0.05 and w.project.statistics == {}
    page.set_inputs(measure="Total distance (cm)", factor="Group", posthoc="holm", plot="box", points=False,
                    alpha=0.01)
    s = w.project.statistics
    assert s["measure"] == "Total distance (cm)" and s["posthoc"] == "holm" and s["plot"] == "box"
    assert s["alpha"] == 0.01 and s["points"] is False and w.dirty
    assert page.analysis() is not None and "α = 0.01" in page.analysis().subtitle
    # a change made by the user is kept as well
    page.error.setCurrentIndex(page.error.findData("sd"))
    assert w.project.statistics["error"] == "sd"
    # the significance level is a setting of the report
    assert "<td>Select the significance level (α)</td><td>0.01</td>" in page.report_html()
    assert w.save()
    w.dirty = False
    w.close()

    w = _window(folder)
    page = w.goto("StatisticsPage")
    page.wait_loaded()
    pump()
    assert page.alpha.value() == 0.01 and page.posthoc.currentData() == "holm"
    assert page.measure.currentData() == "Total distance (cm)" and page.plot_kind.currentData() == "box"
    assert not page.points.isChecked() and page.error.currentData() == "sd"
    assert not w.dirty  # restoring is not an edit
    w.dirty = False
    w.close()
