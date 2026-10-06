"""Results section in the layout of ANY-maze: Data page views (explorer sub-items), ribbon groups and commands;
Statistics page analyses, property page and report."""

import shutil
import time

import pytest
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QInputDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=2, seconds=6)
    p.save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    yield w
    w.dirty = False
    w.close()


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def group_titles(w):
    sec = w.sections[w._page_section[id(w.current_page())]]
    return [g.title for g in sec.panel.context]


def ribbon_texts(w):
    sec = w.sections[w._page_section[id(w.current_page())]]
    return [b.defaultAction().text() for g in sec.panel.context for b in g.buttons]


def in_ribbon(widget) -> bool:
    p = widget.parentWidget()
    while p is not None:
        if p.objectName() == "RibbonGroup":
            return not widget.isHidden()
        p = p.parentWidget()
    return False


def explorer_children(w, page):
    sec = w.sections[w._page_section[id(page)]]
    it = sec.item_for(page)
    return sec, [it.child(i).text(0) for i in range(it.childCount())]


def test_data_page_views_and_ribbon(win, tmp_path):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    pump()
    sec, children = explorer_children(win, page)
    assert children == ["Spreadsheet", "Track plots", "Heat maps", "Charts", "Video export"]
    assert group_titles(win) == ["Navigation", "Clipboard", "Spreadsheet", "Actions", "Filter", "Time periods"]
    for text in ("Back", "Forward", "Copy", "Copy selection", "Print", "Save", "HTML report", "Select data",
                 "View spreadsheet", "Clear settings", "Set segment length", "Recalculate"):
        assert text in ribbon_texts(win), text
    assert page.title_lbl.text() == "Data" and not page.back_act.isEnabled()
    assert not page.view_sheet_act.isEnabled() and page.save_act.menu() is not None
    # spreadsheet: "Group" is shown as "Treatment", roomy rows, no row numbers
    cols = page.model.columns
    assert page.model.headerData(cols.index("Group"), Qt.Horizontal) == "Treatment"
    assert page.table.verticalHeader().defaultSectionSize() >= 28 and page.table.verticalHeader().isHidden()
    assert "Treatment" in page.table_html()
    # the filter combos live in the ribbon and survive view / page changes
    assert in_ribbon(page.group_combo) and in_ribbon(page.seg_check) and not in_ribbon(page.part_combo)
    # explorer sub-item → track plots view with its own ribbon groups
    sec.explorer.setCurrentItem(sec.item_for(page, "track"))
    pump()
    assert page.view == "track" and page.main_tabs.currentIndex() == 2 and page.tabs.currentIndex() == 0
    assert group_titles(win) == ["Navigation", "Body part", "Colour by", "Show", "Figure", "Test"]
    assert page.test_list.count() == page.proxy.rowCount() == 4
    page.test_list.setCurrentRow(2)
    assert page.current_row() is page.shown_rows()[2]
    page.render_all()
    assert page.track_canvas.figure.axes
    page.mode_b.click()
    assert page.tabs.currentIndex() == 2 and page.speed_canvas.figure.axes
    page.show_item("heat")
    pump()
    assert page.tabs.currentIndex() == 1 and page.title_lbl.text() == "Heat maps" and in_ribbon(page.part_combo)
    assert group_titles(win) == ["Navigation", "Body part", "Heat map of", "Scale", "Align", "Treatments",
                                 "Figure"]
    assert sec.explorer.currentItem() is sec.item_for(page, "heat")
    page.show_item("charts")
    pump()
    assert page.main_tabs.currentIndex() == 1 and page.charts.track is not None
    assert "Measure interval" in ribbon_texts(win) and "Find peaks" in ribbon_texts(win)
    page.measure_act.trigger()
    assert page.charts.measure_check.isChecked()
    page.show_item("video")
    pump()
    assert page.plot_stack.currentIndex() == 1 and "Test" in page.video_lbl.text()
    assert page.video_opts.options().trail_s == 5.0
    # navigation history
    page.go_back()
    assert page.view == "charts" and page.fwd_act.isEnabled()
    page.go_back()
    page.go_back()
    assert page.view == "track"
    page.go_forward()
    assert page.view == "heat"
    page.view_sheet_act.trigger()
    pump()
    assert page.view == "spreadsheet" and page.main_tabs.currentIndex() == 0 and not page.fwd_act.isEnabled()
    # controls hosted in the ribbon are still alive and working after all those ribbon rebuilds
    pump(10)
    page.group_combo.setCurrentIndex(page.group_combo.findData("Control"))
    assert page.proxy.rowCount() == 2 and in_ribbon(page.group_combo)
    page.part_combo.setCurrentIndex(1)  # hidden while not in the ribbon, but not deleted
    assert page.plot_options()["part"] == "head"
    # another page and back
    win.goto("StatisticsPage")
    pump(10)
    win.goto("ResultsPage")
    pump(10)
    assert in_ribbon(page.group_combo) and page.group_combo.currentData() == "Control"


def test_data_page_actions(win, tmp_path, monkeypatch):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    pump()
    # select data: the measure chooser is a toggleable side panel
    assert page.chooser.isHidden()
    page.select_act.trigger()
    assert not page.chooser.isHidden()
    page.select_act.trigger()
    assert page.chooser.isHidden()
    # copy selection: only the selected cells
    cols = page.model.columns
    c0 = cols.index("Total distance (cm)")
    page.table.selectionModel().select(QItemSelection(page.proxy.index(0, c0), page.proxy.index(1, c0)),
                                       QItemSelectionModel.ClearAndSelect)
    page.copy_selection()
    lines = QApplication.clipboard().text().splitlines()
    assert lines[0] == "Total distance (cm)" and len(lines) == 3
    # clear settings
    page.set_visible_measures(["Centre: time (%)"])
    page.group_combo.setCurrentIndex(1)
    page.clear_settings()
    assert len(page.visible_measures()) == len(page.measure_columns()) and page.proxy.rowCount() == 4
    # set segment length → time bins in the analysis settings and periods shown
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *a, **k: (3.0, True))
    page.set_segment_length()
    page.wait_loaded()
    assert page.project.analysis.bin_length_s == 3.0 and page.seg_check.isChecked() and page.segmented
    assert {r["Period"] for r in page.rows} == {"Whole test", "0-3 s", "3-6 s"} and win.dirty
    # print to a PDF file
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "sheet.pdf"))
    assert page.print_table(printer) is printer
    assert (tmp_path / "sheet.pdf").stat().st_size > 1000


def test_statistics_page_ui(win, tmp_path):
    page = win.goto("StatisticsPage")
    page.wait_loaded()
    pump()
    sec, children = explorer_children(win, page)
    assert children == ["Compare groups", "Two factors", "Correlation", "Grouped", "Categorical"]
    assert group_titles(win) == ["Analysis", "Report"]
    for text in ("Run", "Recalculate", "Copy summary", "Save figure", "Save report"):
        assert text in ribbon_texts(win)
    # ANY-maze terms and sentence-style property page
    assert page.factor.currentText() == "Treatment" and page.factor.currentData() == "Group"
    prompts = [w.text() for views, ws in page._rows_by_view for w in ws[:1] if not w.isHidden()]
    for p in ("Independent variables", "Options", "Select the type of statistical tests to use",
              "Optionally select a post-hoc test to use", "Select the control group to compare to"):
        assert p in prompts, p
    names = [page.posthoc.itemText(i) for i in range(page.posthoc.count())]
    for n in ("- None -", "Bonferroni test", "Duncan's test", "Fisher's LSD test", "Scheffé's test", "Šidák test",
              "Student-Newman-Keuls test", "Tukey test"):
        assert n in names, n
    assert not page.posthoc.itemIcon(names.index("Duncan's test")).isNull()
    # post-hoc tests on three periods
    page.set_inputs(measure="Total distance (cm)", factor="Stage", method="anova")
    page.project.analysis.bin_length_s = 2.0
    page.reload()
    page.wait_loaded()
    for key, name in (("duncan", "Duncan"), ("snk", "Student-Newman-Keuls"), ("lsd", "Fisher's LSD"),
                      ("scheffe", "Scheffé")):
        page.set_inputs(factor="Period", method="anova", posthoc=key)
        assert len(page.result["posthoc"]) == 3 and page.result["posthoc"][0]["test"] == name
        assert page.posthoc_table.rowCount() == 3
    # parametric / non-parametric radio buttons
    page.set_inputs(factor="Group", period="Whole test", method="auto", parametric=False)
    assert page.nonparam_radio.isChecked() and page.result["test"] == "Mann-Whitney U"
    page.param_radio.setChecked(True)
    page.run_act.trigger()
    assert page.result["test"] == "Welch's t-test"
    assert page._heads[0].text() == "Total distance (cm)" and "Treatment" in page._subheads[0].text()
    # explorer sub-items switch the analysis (and the visible settings)
    sec.explorer.setCurrentItem(sec.item_for(page, "correlation"))
    pump()
    assert page.tabs.currentIndex() == 2 and page.title_lbl.text() == "Correlation"
    assert not page.corr_x.isHidden() and page.factor.isHidden() and page.corr is not None
    page.show_item("grouped")
    pump(10)
    assert page.copy_table_act.isEnabled() and not page.f1.isHidden()
    page.show_item("compare")
    pump(10)
    assert not page.copy_table_act.isEnabled()
    # report as HTML (settings, result, tables and the graph)
    out = page.save_report(str(tmp_path / "report.html"))
    html = open(out, encoding="utf-8").read()
    assert "Welch" in html and "data:image/png;base64" in html and "Descriptive statistics" in html
    assert "Select the independent variable" in html and "Treatment" in html
    assert isinstance(page.measure, QComboBox)


def test_ribbon_save_menu_exports(win, tmp_path, monkeypatch):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    out = tmp_path / "r.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page.save_act.menu().actions()[0].trigger()
    assert out.read_text().splitlines()[0].split(",")[:3] == ["Test", "Animal", "Group"]
