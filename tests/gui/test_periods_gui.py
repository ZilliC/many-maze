"""Protocol ▸ Analysis ▸ Time periods based on a time marker: the end of a period (after its duration, at an event or
at a calculation's time), periods defined by calculations with their checks, and calculations renamed or deleted
while periods use them."""

import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.calculations import Calculation
from manymaze.core.demo import create_demo_project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def win(tmp_path, monkeypatch):
    p = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=4)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    yield w
    w.dirty = False
    w.close()


def choose(table, row, col, value):
    combo = table.cellWidget(row, col)
    combo.setCurrentIndex(combo.findData(value))


def test_event_period_end_and_calculation_anchor(win, monkeypatch):
    p = win.project
    pg = win.goto("ExperimentPage")
    pg.show_item("analysis")
    t = pg.ev_periods
    pg._add_event_period()
    assert "end" not in p.analysis.event_periods[0]  # ends after its duration, as before
    # the period ends at an event
    choose(t, 0, 1, "first_entry")
    t.item(0, 2).setText("Centre")
    choose(t, 0, 6, "first_exit")
    t.item(0, 7).setText("Centre")
    t.item(0, 8).setText("0.5")
    d = p.analysis.event_periods[0]
    assert d["anchor"] == "first_entry" and d["zone"] == "Centre"
    assert d["end"] == {"anchor": "first_exit", "zone": "Centre", "offset_s": 0.5, "occurrence": 1}
    rows = p.results(segmented=True)
    assert any(r["Period"] == d["label"] for r in rows)
    # a period starting at the time given by a calculation: reported while the calculation is missing …
    choose(t, 0, 1, "calculation")
    t.item(0, 2).setText("Start time (s)")
    assert p.analysis.event_periods[0]["calculation"] == "Start time (s)"
    assert pg.ev_periods_lbl.isVisible() and "no calculation called “Start time (s)”" in pg.ev_periods_lbl.text()
    p.calculations.append(Calculation("Start time", "{Test duration (s)} / 2", 2, units="s"))
    pg.show_item("calculations")
    pg.show_item("analysis")
    assert not pg.ev_periods_lbl.isVisible()
    label = p.analysis.event_periods[0]["label"]
    rows = [r for r in p.results(segmented=True) if r["Period"] == label]
    assert rows and 0 < rows[0]["Test duration (s)"] <= 2.05  # from half the test (2 s) to the exit or the end
    # … and a calculation using the period it defines is a circular reference, on both pages
    p.calculations[0].formula = f"result_for_period({{Total distance (cm)}}, '{label}')"
    pg.show_item("analysis")
    assert "circular reference" in pg.ev_periods_lbl.text()
    pg._fill_calculations()  # (the list of the Calculations element)
    pg.show_item("calculations")
    pg.calc_list.setCurrentRow(0)
    pg._show_calculation()
    assert "circular reference: the time period" in pg.calc_editor.status.text()
    # renaming the calculation: the period follows (as do formulas and criteria)
    p.calculations[0].formula = "{Test duration (s)} / 2"
    renamed = Calculation("Half time", "{Test duration (s)} / 2", 2, units="s")
    pg._calculation_edited(renamed)
    assert p.analysis.event_periods[0]["calculation"] == "Half time (s)"
    # deleting it says the period uses it
    asked = {}
    monkeypatch.setattr(QMessageBox, "question", lambda _w, _t, msg: asked.setdefault("msg", msg) and QMessageBox.No)
    pg.delete_calculation()
    assert "time period “" in asked["msg"] and p.calculations
    # the table shows a stored end again when the page is reloaded
    pg.set_project(p)
    pump()
    assert t.cellWidget(0, 6).currentData() == "first_exit" and t.item(0, 7).text() == "Centre"
    assert t.cellWidget(0, 1).currentData() == "calculation" and t.item(0, 2).text() == "Half time (s)"
