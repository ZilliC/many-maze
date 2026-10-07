"""GUI smoke tests for the experiment workflow and behaviour scoring features."""

import shutil

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from manymaze.core import workflow as wf
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Behaviour, Project
from manymaze.gui.confirm_id import confirm_animal_id
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.animals import CriteriaDialog, DoseDialog
from manymaze.gui.pages.tests import C_GROUP, C_STATUS, ScheduleDialog
from shots import shot_path

app = QApplication.instance() or QApplication([])

BEHAVIOURS = [Behaviour("Rearing", "r", "state"), Behaviour("Freezing", "f", "hold", group="posture"),
              Behaviour("Grooming", "g", "state", group="posture"), Behaviour("Defecation", "d", "point")]


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    p = Project.load(d)
    p.behaviours = [Behaviour(b.name, b.key, b.kind, b.group) for b in BEHAVIOURS]
    w.set_project(p)
    yield w
    tv = w.page("TestViewPage")
    tv._reset_clock()
    tv.player.close_video()
    w.dirty = False
    w.close()


def open_view(w, test_id=1):
    w.open_test(test_id)
    v = w.page("TestViewPage")
    v.tabs.setCurrentIndex(2)
    return v


def key_event(widget, kind, key, text, autorepeat=False):
    QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.NoModifier, text, autorepeat))


# ---------------------------------------------------------------- scoring
def test_hold_keys_and_exclusive_sets(win):
    v = open_view(win)
    t = v.test
    t.events = []
    v.reload_current()
    vw = v.player.view
    v.player.seek_time(1.0)
    QTest.keyPress(vw, Qt.Key_F)
    assert "Freezing" in v._open_states
    key_event(vw, QEvent.KeyPress, Qt.Key_F, "f", autorepeat=True)  # auto-repeat ignored
    key_event(vw, QEvent.KeyRelease, Qt.Key_F, "f", autorepeat=True)
    assert "Freezing" in v._open_states and t.events == []
    v.player.seek_time(2.4)
    QTest.keyRelease(vw, Qt.Key_F)
    assert t.events == [{"behaviour": "Freezing", "t": 1.0, "t_end": 2.4}] and not v._open_states
    # exclusive set: starting Freezing stops Grooming
    v.player.seek_time(3.0)
    QTest.keyClick(vw, Qt.Key_G)
    v.player.seek_time(4.0)
    QTest.keyPress(vw, Qt.Key_F)
    assert list(v._open_states) == ["Freezing"]
    assert {"behaviour": "Grooming", "t": 3.0, "t_end": 4.0} in t.events
    v.player.seek_time(5.0)
    QTest.keyRelease(vw, Qt.Key_F)
    assert {"behaviour": "Freezing", "t": 4.0, "t_end": 5.0} in t.events
    assert t.status == "tracked"  # has a track: stays tracked
    v.tabs.setCurrentIndex(0)
    assert v.results_dict()["Freezing: duration (s)"] == "2.4"


def test_onscreen_buttons(win):
    v = open_view(win)
    t = v.test
    t.events = []
    v.reload_current()
    assert list(v.pad.buttons) == ["Rearing", "Freezing", "Grooming", "Defecation"]
    assert all(b.focusPolicy() == Qt.NoFocus and b.isVisible() for b in v.pad.buttons.values())
    hold = v.pad.buttons["Freezing"]
    v.player.seek_time(1.0)
    QTest.mousePress(hold, Qt.LeftButton)
    assert "Freezing" in v._open_states and "background:#" in hold.styleSheet()
    v.player.seek_time(2.0)
    QTest.mouseRelease(hold, Qt.LeftButton)
    assert t.events == [{"behaviour": "Freezing", "t": 1.0, "t_end": 2.0}]
    QTest.mouseClick(v.pad.buttons["Rearing"], Qt.LeftButton)
    assert "Rearing" in v._open_states
    v.player.seek_time(3.0)
    QTest.mouseClick(v.pad.buttons["Rearing"], Qt.LeftButton)
    QTest.mouseClick(v.pad.buttons["Defecation"], Qt.LeftButton)
    assert {"behaviour": "Rearing", "t": 2.0, "t_end": 3.0} in t.events
    assert {"behaviour": "Defecation", "t": 3.0, "t_end": None} in t.events
    # buttons follow the experiment's behaviours
    win.project.behaviours.append(Behaviour("Sniffing", "s", "hold"))
    v.reload_current()
    assert "Sniffing" in v.pad.buttons


def test_takenote_observation_clock(win):
    p = win.project
    t = p.add_test("", "C1", stage="Day 1", trial=2, duration_s=20)
    v = open_view(win, t.id)
    assert v.player.source is None and not v.clock_box.isHidden()
    assert "direct observation" in v.hud.toPlainText()
    vw = v.player.view
    now = [100.0]
    v.clock.time_fn = lambda: now[0]
    QTest.keyClick(vw, Qt.Key_D)  # clock not running: nothing scored
    assert t.events == []
    assert v.clock_start()
    assert v.clock.state == "running" and not v.clock_start_btn.isEnabled()
    now[0] = 102.0
    QTest.keyClick(vw, Qt.Key_R)
    now[0] = 105.0
    QTest.keyClick(vw, Qt.Key_R)
    assert t.events == [{"behaviour": "Rearing", "t": 2.0, "t_end": 5.0}] and t.status == "scored"
    now[0] = 106.0
    v.clock_pause()
    now[0] = 110.0
    assert v.clock.elapsed() == pytest.approx(6.0) and v.clock_start_btn.text() == "Resume"
    v.clock_start()
    now[0] = 112.0
    QTest.mousePress(v.pad.buttons["Freezing"], Qt.LeftButton)
    now[0] = 113.0
    QTest.keyClick(vw, Qt.Key_D)
    now[0] = 114.0
    v.clock_stop()  # running hold behaviour closed at the stop time
    assert v.clock.state == "stopped" and not v._open_states
    assert {"behaviour": "Freezing", "t": 8.0, "t_end": 10.0} in t.events
    assert {"behaviour": "Defecation", "t": 9.0, "t_end": None} in t.events
    QTest.mouseRelease(v.pad.buttons["Freezing"], Qt.LeftButton)
    assert t.duration_s == 10.0 and t.recorded_at and t.status == "scored"
    rows = p.analyse_test(t)
    assert rows[0]["Rearing: duration (s)"] == 3 and rows[0]["Defecation: count"] == 1
    assert any(r["Test"] == t.id for r in p.results())
    v.tabs.setCurrentIndex(0)
    assert "manual scoring" in v.results_lbl.text()
    # starting again replaces the events (after confirmation); the duration limit stops the clock
    v.clock_start()
    assert t.events == [] and t.status == "pending"
    now[0] = 130.0
    v._clock_tick()
    assert v.clock.state == "stopped" and t.duration_s == 10.0


def test_scored_video_without_track(win):
    p = win.project
    v = open_view(win)
    t = v.test
    p.track_path(t).unlink()
    t.status, t.events = "pending", []
    v.reload_current()
    v.player.seek_time(2.0)
    QTest.keyClick(v.player.view, Qt.Key_D)
    assert t.status == "scored" and "[scored]" in v.test_combo.currentText()
    v.tabs.setCurrentIndex(0)
    res = v.results_dict()
    assert res["Defecation: count"] == "1" and "Total distance (cm)" not in res
    v.events_table.selectRow(0)
    v.delete_selected_events()
    assert t.status == "pending"


def test_animal_id_confirmation(win, monkeypatch):
    p = win.project
    p.settings_extra["confirm_id"] = True
    p.animal_fields = ["Microchip"]
    p.get_animal("C1").fields["Microchip"] = "9851"
    answers = [("A1", True), ("9851", True)]
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]) or QMessageBox.Cancel)
    v = open_view(win)
    t = v.test
    assert t.animal_id == "C1"
    t.events = []
    v.player.seek_time(1.0)
    QTest.keyClick(v.player.view, Qt.Key_D)  # wrong animal → blocked
    assert t.events == [] and warnings and "does not match" in warnings[0]
    QTest.keyClick(v.player.view, Qt.Key_D)  # microchip matches
    QTest.keyClick(v.player.view, Qt.Key_D)  # asked only once per test
    assert len(t.events) == 2 and answers == []
    # the helper used by the Live page
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("c1", True))
    assert confirm_animal_id(v, t)
    p.settings_extra["confirm_id"] = False
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: pytest.fail("should not ask"))
    assert confirm_animal_id(v, t)


def test_notes_in_test_view(win):
    v = open_view(win)
    v.notes_edit.setPlainText("Animal jumped out at 3 min")
    assert v.test.notes == "Animal jumped out at 3 min" and win.dirty
    v.step_test(1)
    assert v.notes_edit.toPlainText() == v.test.notes


# ---------------------------------------------------------------- tests page
def test_skip_reperform_clear(win):
    p = win.project
    page = win.goto("TestsPage")
    page.select_ids({1})
    assert page.a_skip.isEnabled() and not page.a_resume.isEnabled()
    page.skip_selected()
    assert p.get_test(1).status == "skipped" and page.a_resume.isEnabled() and not page.a_skip.isEnabled()
    assert all(r["Test"] != 1 for r in p.results())
    page.resume_selected()
    assert p.get_test(1).status == "tracked"
    new = page.reperform_selected()
    assert len(new) == 1 and p.get_test(1).status == "superseded" and new[0].attempt == 2
    assert page.selected_ids() == [new[0].id]
    assert "superseded" in page.summary.text()
    row = next(r for r, t in enumerate(p.tests) if t.id == new[0].id)
    assert "attempt 2" in page.model.index(row, C_STATUS).data()
    page.select_ids({2})
    assert page.clear_selected_tracks(confirm=False) == 1
    assert p.get_test(2).status == "scored" and not p.has_track(p.get_test(2))  # demo events kept


def test_schedule_dialog(win, monkeypatch):
    p = win.project
    p.stages = ["Day 1", "Day 2"]
    page = win.goto("TestsPage")
    p.get_animal("A1").retired = True

    def fake_exec(dlg):
        assert dlg.trials.maximum() == wf.MAX_TRIALS
        dlg.trials.setValue(2)
        dlg.order.setCurrentIndex(dlg.order.findData("trial"))
        dlg.cb.setCurrentIndex(dlg.cb.findData("variable"))
        dlg.var_name.setCurrentText("novel_object")
        dlg.levels.setText("Object A, Object B")
        assert "will be created" in dlg.preview.text()
        return QDialog.Accepted

    monkeypatch.setattr(ScheduleDialog, "exec", fake_exec)
    n0 = len(p.tests)
    page.create_schedule()
    new = p.tests[n0:]
    # C1 only (A1 retired); (C1, Day 1, 1) exists
    assert [(t.animal_id, t.stage, t.trial) for t in new] == [("C1", "Day 1", 2), ("C1", "Day 2", 1),
                                                               ("C1", "Day 2", 2)]
    assert [t.variables["novel_object"] for t in new] == ["Object B", "Object A", "Object B"]


# ---------------------------------------------------------------- blind testing
def test_blind_display(win, monkeypatch):
    p = win.project
    p.blind = True
    tests = win.goto("TestsPage")
    shown = tests.model.index(0, C_GROUP).data()
    codes = wf.blind_codes(p)
    assert shown in (codes["Control"], codes["Anxious"]) and shown not in ("Control", "Anxious")
    assert tests.model.headerData(C_GROUP, Qt.Horizontal) == "Code"
    animals = win.goto("AnimalsPage")
    c_id, c_tr = animals.col_of("id"), animals.col_of("treatment")
    r = next(r for r in range(animals.table.rowCount()) if animals.table.item(r, c_id).text() == "C1")
    assert animals.table.item(r, c_tr).text() == codes["Control"]
    assert not animals._group_acts[0].isEnabled() and not animals.a_reveal.isChecked()
    assert "Control" not in animals.treatments.item(0, 0).text()
    assert animals.treatments.item(0, 1).text() == codes["Control"]
    v = open_view(win)
    assert "Control" not in v.info_lbl.text() and codes["Control"] in v.info_lbl.text()
    assert "Control" not in v.title_lbl.text()
    # results keep the real groups
    assert {r["Group"] for r in p.results()} == {"Control", "Anxious"}
    # unblinding asks for confirmation
    exp = win.goto("ExperimentPage")
    assert exp.blind.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    exp.blind.setChecked(False)
    assert p.blind and exp.blind.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    exp.blind.setChecked(False)
    assert not p.blind
    assert tests.model.index(0, C_GROUP).data() in ("Control", "Anxious")
    assert tests.model.headerData(C_GROUP, Qt.Horizontal) == "Treatment"
    # the Experiment ribbon's "Reveal treatment coding" switches blind testing too (asking before revealing)
    animals = win.goto("AnimalsPage")
    assert animals.a_reveal.isChecked()
    animals.a_reveal.setChecked(False)
    assert p.blind
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    animals.a_reveal.setChecked(True)
    assert p.blind and not animals.a_reveal.isChecked()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    animals.a_reveal.setChecked(True)
    assert not p.blind


# ---------------------------------------------------------------- experiment page
def test_experiment_behaviour_and_criteria_editor(win):
    p = win.project
    exp = win.goto("ExperimentPage")
    assert exp.beh.rowCount() == 4
    assert exp.beh.cellWidget(1, 2).currentData() == "hold" and exp.beh.item(1, 3).text() == "posture"
    exp.beh.item(3, 1).setText("R")  # duplicate key
    assert not exp.beh_lbl.isHidden() and "Key “R”" in exp.beh_lbl.text()
    exp.beh.item(3, 1).setText("d")
    assert exp.beh_lbl.isHidden()
    exp.beh.cellWidget(0, 2).setCurrentIndex(exp.beh.cellWidget(0, 2).findData("hold"))
    assert p.behaviours[0].kind == "hold" and p.behaviours[0].color
    exp.stages.setPlainText("\n".join(f"S{i}" for i in range(60)))
    assert len(p.stages) == wf.MAX_STAGES and not exp.stages_lbl.isHidden()
    exp.stages.setPlainText("Training\nProbe")
    exp._add_criterion()
    exp.crit.item(0, 1).setText("Escape latency (s)")
    exp.crit.cellWidget(0, 6).setValue(5)
    c = p.training_criteria[0]
    assert c["stage"] == "Training" and c["measure"] == "Escape latency (s)" and c["consecutive_trials"] == 3
    assert c["action_fail"] == {"after_trials": 5, "action": "retire"}
    exp.confirm_id.setChecked(True)
    assert wf.confirm_id_enabled(p)
    exp.on_show()
    assert exp.crit.rowCount() == 1 and exp.crit.cellWidget(0, 6).value() == 5


# ---------------------------------------------------------------- animals page
def test_retire_criteria_and_doses(win, monkeypatch):
    p = win.project
    page = win.goto("AnimalsPage")
    for aid, vals in (("C1", [5, 4, 3]), ("A1", [30, 40, 35])):
        for k, val in enumerate(vals):
            t = p.add_test("", aid, stage="Training", trial=k + 1)
            t.status = "scored"
            t.result_variables = {"Escape latency (s)": val}
        p.add_test("", aid, stage="Training", trial=4)
    p.training_criteria = [{"stage": "Training", "measure": "Escape latency (s)", "op": "<", "value": 10,
                            "consecutive_trials": 2, "action_fail": {"after_trials": 3, "action": "retire"}}]
    monkeypatch.setattr(CriteriaDialog, "exec", lambda self: QDialog.Accepted)
    res = page.criteria_dialog()
    assert res["retired"] == ["A1"] and p.get_animal("A1").retired
    assert wf.completed_stages(p) == {"C1": ["Training"]}
    status_col, id_col = page.col_of("status"), page.col_of("id")
    r = next(r for r in range(page.table.rowCount()) if page.table.item(r, id_col).text() == "A1")
    assert page.table.item(r, status_col).text() == "Retired"
    assert "1 retired" in page.summary.text()
    page.table.selectRow(r)
    assert page.retire_btn.text() == "Reinstate"
    page.toggle_retire_selected()
    assert not p.get_animal("A1").retired
    page.table.selectRow(r)
    page.toggle_retire_selected(reason="injured")
    assert p.get_animal("A1").retired_reason == "injured"
    # dose calculator
    page.add_field("Weight (g)")
    p.get_animal("C1").fields["Weight (g)"] = "25"
    p.get_animal("A1").fields["Weight (g)"] = "30,0"

    def fake_exec(dlg):
        assert dlg.weight.currentText() == "Weight (g)"
        dlg.dose.setValue(10)
        dlg.conc.setValue(2)
        dlg.only_sel.setChecked(False)
        return QDialog.Accepted

    monkeypatch.setattr(DoseDialog, "exec", fake_exec)
    vols = page.dose_dialog()
    assert vols["C1"] == pytest.approx(0.125) and vols["A1"] == pytest.approx(0.15)
    c = next(c for c in range(page.table.columnCount()) if page.table.horizontalHeaderItem(c).text() == "Volume (mL)")
    r = next(r for r in range(page.table.rowCount()) if page.table.item(r, id_col).text() == "C1")
    assert page.table.item(r, c).text() == "0.125"
    # the Status drop-down retires / reinstates too
    r = next(r for r in range(page.table.rowCount()) if page.table.item(r, id_col).text() == "C1")
    page.table.item(r, status_col).setText("Retired")
    assert p.get_animal("C1").retired
    page.table.item(r, status_col).setText("Normal")
    assert not p.get_animal("C1").retired


# ---------------------------------------------------------------- screenshot
def test_screenshot(win):
    v = open_view(win)
    t = v.test
    t.events = [{"behaviour": "Rearing", "t": 0.6, "t_end": 1.8}, {"behaviour": "Grooming", "t": 2.0, "t_end": 2.9},
                {"behaviour": "Defecation", "t": 2.4, "t_end": None}]
    v.reload_current()
    v.notes_edit.setPlainText("Scored blind by observer 2")
    v.player.seek_time(3.2)
    v.start_behaviour(win.project.behaviours[1], 3.0)
    v.player.seek_time(3.6)
    QTest.qWait(150)
    win.grab().save(shot_path("shot_workflow.png"))
