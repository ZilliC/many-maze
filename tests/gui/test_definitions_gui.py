"""The settings of the measure definitions left after the ANY-maze audit: keys that count as activity (Key page),
the activity / undefined averages / heading error / partial rotation settings (Protocol ▸ Analysis), heat-map
points (Apparatus) and procedure events recorded as event measures (When statements)."""

import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import procedures as pr
from manymaze.core.demo import create_demo_project
from manymaze.core.measures import AnalysisSettings
from manymaze.gui.main_window import MainWindow
from manymaze.gui.procedure_editor import ProcedureEditor

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


def element(w, page, key):
    sec = w.sections[0]
    sec.explorer.setCurrentItem(sec.item_for(page, key))
    pump()


def test_key_counts_as_activity(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    element(win, ex, "keys")
    ex.new_key()
    r = ex.beh.currentRow()
    ed = ex.key_editor
    assert not ed.activity.isChecked() and ed.activity.isEnabled()
    ed.activity.setChecked(True)
    assert p.behaviours[r].activity is True
    ex.duplicate_key()  # the copy counts as activity too
    assert p.behaviours[-1].activity is True
    ex.beh.setCurrentCell(r, 0)  # shown again from the table
    assert ed.activity.isChecked()
    ed.mode.setCurrentIndex(ed.mode.findData("event"))  # an instant has no duration
    assert not ed.activity.isEnabled()


def test_analysis_settings_of_the_definitions(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    element(win, ex, "analysis")
    form = ex.an_form
    for attr in ("activity_definition", "undefined_averages", "heading_error_by", "heading_error_time_s",
                 "heading_error_distance", "partial_rotation_deg"):
        assert attr in form.editors
    w = form.editors["activity_definition"]
    w.setCurrentIndex(w.findData("pixel_change"))
    assert p.analysis.activity_definition == "pixel_change"
    w = form.editors["undefined_averages"]
    w.setCurrentIndex(w.findData("zero"))
    assert p.analysis.undefined_averages == "zero"
    form.editors["partial_rotation_deg"].setValue(180)
    assert p.analysis.partial_rotation_deg == 180
    ex.restore_analysis_defaults()  # a new experiment's defaults: ANY-maze's definitions
    new = AnalysisSettings.for_new_experiment()
    assert (p.analysis.activity_definition, p.analysis.undefined_averages) == ("mobile_or_keys", "blank")
    assert p.analysis.partial_rotation_deg == new.partial_rotation_deg


def test_heat_map_point(win):
    page = win.goto("ApparatusPage")
    a = page.app
    page.add_point(100, 100, "Hot")
    page.select_item("point", len(a.points) - 1)
    combo = page.panel.point_heatmap
    assert combo.isEnabled() and combo.currentData() == ""
    combo.setCurrentIndex(combo.findData("freezing"))
    assert a.points[-1].heatmap == "freezing"
    assert page.undo() and a.points[-1].heatmap == ""
    assert page.panel.point_heatmap.currentData() == ""


def test_when_recorded_as_an_event():
    from manymaze.core.project import Project

    p = Project()
    ed = ProcedureEditor(p)
    ed.add_procedure()
    ed.add_statement("when")
    w = ed._form_widgets["record_as"]
    w.setText("Lever pressed")
    w.editingFinished.emit()
    st = p.procedures[-1]["statements"][0]
    assert st["record_as"] == "Lever pressed"
    assert "recorded as “Lever pressed”" in pr.describe_statement(st)
    w.setText("")
    w.editingFinished.emit()
    assert "record_as" not in st
