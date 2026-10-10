"""Protocol ▸ Calculations: the element page (list and Calculation property page), inserting measures from the
Select data tree, and the results under Calculation results on the Data page."""

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.calculations import CATEGORY, Calculation
from manymaze.core.demo import create_demo_project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.results.dialogs import MeasurePickerDialog, fill_measure_tree, measure_groups

app = QApplication.instance() or QApplication([])


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    p = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=4)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    yield w
    w.dirty = False
    w.close()


def picker(monkeypatch, column):
    """Answer the Insert measure dialog with `column`; returns the categories it was shown."""
    seen = {}

    def exec_(dlg):
        seen["cats"] = [dlg.tree.topLevelItem(i).text(0) for i in range(dlg.tree.topLevelItemCount())]
        assert dlg.select(column)
        return MeasurePickerDialog.Accepted

    monkeypatch.setattr(MeasurePickerDialog, "exec", exec_)
    return seen


def test_new_calculation_and_formula(win, monkeypatch):
    p = win.project
    ex = win.goto("ExperimentPage")
    names = {a.text(): a for a in ex.add_item_act.menu().actions()}
    names["New calculation"].trigger()
    pump()
    assert ex.element == "calculations" and len(p.calculations) == 1
    assert ex.calc_editor.isEnabled() and ex.calc_list.count() == 1
    sec = win.sections[0]
    assert "Calculations" in [g.title for g in sec.panel.context]
    assert {"New calculation", "Delete calculation", "Duplicate calculation"} <= set(sec.panel.actions_text())
    ed = ex.calc_editor
    ed.name.setText("Centre share")
    ed.name.editingFinished.emit()
    ed.units.setText("%")
    ed.units.editingFinished.emit()
    ed.decimals.setValue(1)
    assert p.calculations[0].column == "Centre share (%)" and p.calculations[0].decimals == 1
    assert ex.calc_list.item(0).text() == "Centre share (%)" and win.dirty
    # an unknown name is reported in red as the formula is typed
    ed.formula.setPlainText("100 * Centre")
    assert "unknown name “Centre”" in ed.status.text()
    # Insert measure: the results are calculated, the Select data tree offers the measures
    ed.formula.setPlainText("100 * ")
    ed.formula.moveCursor(ed.formula.textCursor().MoveOperation.End)
    seen = picker(monkeypatch, "Centre: time (s)")
    ed._insert_measure()
    t0 = time.time()
    while ex._calc_rows is None and time.time() - t0 < 60:
        pump()
    pump(10)
    assert {"Information", "General", "Zones"} <= set(seen["cats"])
    ed.formula.insertPlainText(" / {Test duration (s)}")
    assert p.calculations[0].formula == "100 * {Centre: time (s)} / {Test duration (s)}"
    assert ed.status.text().startswith("Result for test 1 (animal C1): ")
    # named values and the Y axis range
    ed.named[0][0].setText("Limit")
    ed.named[0][0].editingFinished.emit()
    ed.named[0][1].setText("3")
    ed.named[0][1].editingFinished.emit()
    ed.y_max.setText("100")
    ed.y_max.editingFinished.emit()
    assert p.calculations[0].named_values == [["Limit", 3.0]] and p.calculations[0].y_max == 100.0
    # Insert function: the trial functions ask for the measure and fill in the first stage
    picker(monkeypatch, "Total distance (cm)")
    ed.formula.setPlainText("")
    acts = {a.text(): a for a in ed.insert_function_btn.menu().actions()}
    acts["Sum across the trials of a stage"].trigger()
    assert ed.formula.toPlainText() == "sum_trials({Total distance (cm)}, 'Day 1')"
    assert "uses other trials" in ed.status.text()
    acts["sqrt()"].trigger()
    assert ed.formula.toPlainText().endswith("sqrt()") and ed.formula.textCursor().position() == \
        len(ed.formula.toPlainText()) - 1
    # every element page fits the window
    page = ex.elements["calculations"]
    assert page.widget().minimumSizeHint().width() <= page.viewport().width() + 1


def test_rename_duplicate_delete(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    p.calculations = [Calculation("Distance", "{Total distance (cm)} / 100", 2, "m"),
                      Calculation("Double", "2 * {Distance (m)}", 2)]
    ex.on_show()
    ex.show_item("calculations")
    ex.calc_list.setCurrentRow(0)
    ed = ex.calc_editor
    assert ed.name.text() == "Distance" and ed.units.text() == "m"
    ed.name.setText("Path")
    ed.name.editingFinished.emit()
    assert p.calculations[1].formula == "2 * {Path (m)}"  # the formulas using it follow the new name
    # a name already used by a measure is reported (its results would replace the measure)
    ex._set_calculation_rows([{"Test": 1, "Animal": "C1", "Total distance (cm)": 3.0, "Path (m)": 0.03}])
    ed.units.setText("cm")
    ed.name.setText("Total distance")
    ed.name.editingFinished.emit()
    assert "a measure is called “Total distance (cm)”" in ed.status.text()
    assert p.calculations[1].formula == "2 * {Total distance (cm)}"
    ed.name.setText("Path")
    ed.units.setText("m")
    ed.name.editingFinished.emit()
    assert ed.status.text().startswith("Result for test 1")
    # (renamed away from a measure's name: the measure's references are left alone)
    assert p.calculations[0].formula == "{Total distance (cm)} / 100"
    assert p.calculations[1].formula == "2 * {Total distance (cm)}"
    p.calculations[1].formula = "2 * {Path (m)}"
    ex.duplicate_calculation()
    assert [c.name for c in p.calculations] == ["Path", "Path copy", "Double"] and ex.calc_list.currentRow() == 1
    ex.calc_list.setCurrentRow(2)
    ex.delete_calculation()
    assert [c.name for c in p.calculations] == ["Path", "Path copy"]
    ex.calc_list.setCurrentRow(0)
    ex.delete_calculation()
    ex.delete_calculation()
    assert p.calculations == [] and not ex.calc_editor.isEnabled()


def test_results_under_calculation_results(win):
    p = win.project
    p.calculations = [Calculation("Centre share", "100 * {Centre: time (s)} / {Test duration (s)}", 1, "%")]
    r = win.goto("ResultsPage")
    r.reload()
    r.wait_loaded()
    pump(5)
    assert "Centre share (%)" in r.measure_columns()
    tops = {r.tree.topLevelItem(i).text(0): r.tree.topLevelItem(i) for i in range(r.tree.topLevelItemCount())}
    assert CATEGORY in tops and tops[CATEGORY].child(0).data(0, Qt.UserRole) == "Centre share (%)"
    assert list(tops)[-1] == CATEGORY  # after the measures
    row = r.rows[0]
    assert row["Centre share (%)"] == round(100 * row["Centre: time (s)"] / row["Test duration (s)"], 1)
    # changing a calculation invalidates the cached results
    p.calculations[0].decimals = 0
    r.reload()
    r.wait_loaded()
    assert isinstance(r.rows[0]["Centre share (%)"], int)


def test_measure_tree_without_tick_boxes():
    from PySide6.QtWidgets import QTreeWidget

    names = {"zones": {"Centre"}, "points": set(), "lines": set(), "behaviours": set(), "io": set(),
             "calculations": {"DI"}}
    cats = measure_groups(["Total distance (m)", "Centre: time (s)", "DI"], names, ["Animal"])
    assert list(cats) == ["Information", "General", "Zones", CATEGORY]
    t = QTreeWidget()
    fill_measure_tree(t, cats)
    item = t.topLevelItem(2).child(0).child(0)
    assert item.text(0) == "time (s)" and item.data(0, Qt.UserRole) == "Centre: time (s)"
    assert item.data(0, Qt.CheckStateRole) is None  # no tick box
