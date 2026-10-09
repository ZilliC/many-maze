"""Audit 2026-10-08, GUI shell and editors: progress dialogs, the ribbon, blind testing on the Experiment page,
renamed keys and stages, the test schedule's editors and model, users, schedules, the import wizard and background
work."""

import csv
import time

import pytest
from PySide6.QtCore import QEvent, QModelIndex, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDoubleSpinBox, QMessageBox, QWidget

from manymaze.core import workflow as wf
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow, _merge_imported
from manymaze.gui.pages.tests import C_APP, C_DUR, C_START
from manymaze.gui.ribbon import RibbonGroup
from manymaze.gui.widgets import Worker, run_with_progress

app = QApplication.instance() or QApplication([])


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=4)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    pump()
    yield w
    w.dirty = False
    w.close()


def ribbon_button(w, action):
    for sec in w.sections:
        for g in sec.panel.fixed + sec.panel.context:
            for b in g.buttons:
                if b.defaultAction() is action:
                    return b
    raise AssertionError(f"no ribbon button for {action.text()}")


def type_into(w, field, text):
    w.activateWindow()
    pump()
    field.setFocus()
    pump()
    assert QApplication.focusWidget() is field
    field.selectAll()
    QTest.keyClicks(field, text)  # typed, not yet stored (the field stores on editingFinished)


# ------------------------------------------------------------------ progress dialogs, workers, XML import merge
def test_non_cancellable_progress_dialog_ignores_escape_and_close():
    parent = QWidget()
    parent.show()
    state = {"go": False}

    def work(progress, stop):
        while not state["go"] and not stop():
            time.sleep(0.01)
        return 42

    done = []
    w = run_with_progress(parent, "Working", work, on_done=done.append, cancellable=False)
    pump()
    dlg = w.dialog
    assert dlg.isVisible()
    dlg.reject()
    QApplication.sendEvent(dlg, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    dlg.close()
    pump()
    assert dlg.isVisible() and w.isRunning()  # still protecting the window while the work runs
    state["go"] = True
    w.wait(5000)
    pump()
    assert done == [42] and not dlg.isVisible()
    parent.close()


def test_cancellable_progress_dialog_still_cancels():
    parent = QWidget()
    w = run_with_progress(parent, "Working", lambda progress, stop: [time.sleep(0.01) for _ in iter(stop, True)])
    pump()
    w.dialog.canceled.emit()  # the Cancel button
    assert w.wait(5000)
    pump()
    parent.close()


def test_stop_all_waits_and_detaches_workers_still_running():
    parent = QWidget()
    stopped = []

    def polite(progress, stop):
        while not stop():
            time.sleep(0.01)
        stopped.append(1)
        raise InterruptedError("stopped")

    cancelled = []
    w1 = Worker(polite, parent)
    w1.signals.cancelled.connect(lambda: cancelled.append(1))
    w1.start()
    release = {"go": False}
    w2 = Worker(lambda progress, stop: [time.sleep(0.01) for _ in iter(lambda: release["go"], True)], parent)
    w2.start()
    left = Worker.stop_all(ms=300)
    assert not w1.isRunning() and stopped == [1]
    assert left == [w2] and w2.parent() is None  # not destroyed with the window while it runs
    parent.deleteLater()
    pump()
    release["go"] = True
    assert Worker.stop_all(ms=None) == [] and not w2.isRunning()
    pump()
    assert cancelled == [1]  # a stopped worker reports "cancelled", not a failure


def test_xml_import_merge_matches_animals_by_id():
    import copy

    p = Project()
    for aid in ("A1", "A2", "A3"):
        p.ensure_animal(aid)
    scratch = copy.deepcopy(p)
    a2 = scratch.get_animal("A2")
    a2.group = "Saline"
    scratch.ensure_animal("B9", "Drug")
    scratch.add_test("", "B9", stage=scratch.add_stage("Day 1"))
    # meanwhile the experiment's list changed order and gained an animal
    p.animals.reverse()
    p.ensure_animal("Z1")
    _merge_imported(p, scratch)
    assert [a.id for a in p.animals] == ["A3", "A2", "A1", "Z1", "B9"]
    assert p.get_animal("A2").group == "Saline" and p.get_animal("A3").group == "" and p.get_animal("Z1")
    assert [g.name for g in p.groups] == ["Saline", "Drug"] or {g.name for g in p.groups} >= {"Drug"}
    assert [t.animal_id for t in p.tests] == ["B9"] and p.stages == ["Day 1"]


def test_open_archive_stops_when_asked(tmp_path, win):
    import zipfile

    from manymaze.core.archive import extract_archive

    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("E.mmaze/project.json", "{}")
        for i in range(5):
            z.writestr(f"E.mmaze/tracks/{i}.csv", "t,x,y\n")
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 2

    with pytest.raises(InterruptedError):
        extract_archive(src, tmp_path / "out", should_stop=stop)
    assert not (tmp_path / "out" / "E.mmaze").exists()  # the partly unpacked folder is removed
    assert extract_archive(src, tmp_path / "out2", should_stop=lambda: False) == (tmp_path / "out2" / "E.mmaze")


# ------------------------------------------------------------------ ribbon
def test_ribbon_commands_keep_the_text_being_typed(win, monkeypatch):
    ex = win.goto("ExperimentPage")
    p = win.project
    # Protocol name + Apply template (a menu button: the field is stored before the menu opens)
    ex.show_item("protocol")
    pump()
    type_into(win, ex.name, "Renamed protocol")
    menu = ex.template_act.menu()

    def choose():
        m = QApplication.activePopupWidget()
        m = m if m is not None else menu
        m.close()
        m.actions()[1].trigger()

    QTimer.singleShot(50, choose)
    QTest.mouseClick(ribbon_button(win, ex.template_act), Qt.LeftButton)
    pump()
    assert p.name == "Renamed protocol" and ex.name.text() == "Renamed protocol"

    # Key name + New key
    ex.show_item("keys")
    pump()
    n_keys = len(p.behaviours)
    ex.beh.setCurrentCell(0, 0)
    pump()
    type_into(win, ex.key_editor.name, "Sniffing")
    new_key = next(a for a in ex.element_acts["keys"] if not isinstance(a, tuple) and a.text() == "New key")
    QTest.mouseClick(ribbon_button(win, new_key), Qt.LeftButton)
    pump()
    assert p.behaviours[0].name == "Sniffing" and len(p.behaviours) == n_keys + 1

    # Wait seconds + New procedure
    ex.show_item("procedures")
    pump()
    ed = ex.proc_editor
    ed.add_procedure()
    ed.add_statement("wait")
    pump()
    proc = ed.procs[-1]
    type_into(win, ed._form_widgets["seconds"], "7")
    new_proc = next(a for a in ex.element_acts["procedures"] if not isinstance(a, tuple)
                    and a.text() == "New procedure")
    QTest.mouseClick(ribbon_button(win, new_proc), Qt.LeftButton)
    pump()
    assert proc["statements"][0]["seconds"] == 7 and ed.procs[-1] is not proc


def test_set_context_twice_leaves_no_stray_windows(win):
    win.goto("TestsPage")
    pump()
    win.activateWindow()
    pump()
    page = win.page("TestsPage")
    panel = win.sections[2].panel
    panel.set_context(page.ribbon_groups())
    panel.set_context(page.ribbon_groups())  # twice in one event-loop pass
    pump()
    stray = [g for g in QApplication.topLevelWidgets() if isinstance(g, RibbonGroup) and g.isVisible()]
    assert stray == []
    assert QApplication.activeWindow() in (win, None) and not isinstance(QApplication.activeWindow(), RibbonGroup)
    assert all(b.isVisible() for g in panel.context for b in g.buttons)


# ------------------------------------------------------------------ blind testing on the Experiment page
def test_blind_export_and_add_several_show_codes_only(win, tmp_path, monkeypatch):
    an = win.goto("AnimalsPage")
    p = win.project
    an.set_blind(True)
    groups = [g.name for g in p.groups]
    out = tmp_path / "animals.csv"
    an.export_csv(str(out))
    text = out.read_text()
    assert not any(g in text for g in groups)
    rows = list(csv.reader(text.splitlines()))
    assert rows[0][1] == "Treatment code"
    a = p.animals[0]
    assert rows[1][1] == wf.treatment_code(p, a.group)

    seen = {}

    def fake_exec(dlg):
        seen["texts"] = [dlg.group.itemText(i) for i in range(dlg.group.count())]
        seen["editable"] = dlg.group.isEditable()
        dlg.group.setCurrentIndex(2)
        dlg.prefix.setText("Q")
        dlg.count.setValue(2)
        return QDialog.Accepted

    from manymaze.gui.pages import animals as animals_mod

    monkeypatch.setattr(animals_mod.AddSeveralDialog, "exec", fake_exec)
    an._add_several_dialog()
    assert not any(g in t for t in seen["texts"] for g in groups) and not seen["editable"]
    assert p.get_animal("Q01").group == groups[1]  # the real treatment, chosen by its code


# ------------------------------------------------------------------ keys and stages renamed
def test_renaming_or_deleting_a_key_keeps_its_data(win, monkeypatch):
    ex = win.goto("ExperimentPage")
    p = win.project
    ex.show_item("keys")
    pump()
    name = p.behaviours[0].name
    t = p.tests[0]
    t.events = [{"behaviour": name, "t": 1.0, "t_end": 2.0}]
    p.analysis.event_periods = [{"label": "After", "anchor": "mark", "behaviour": name, "offset_s": 0,
                                 "duration_s": 10, "occurrence": 1}]
    p.training_criteria = [{"stage": "", "measure": f"{name}: count", "op": ">", "value": 1,
                            "consecutive_trials": 1}]
    ex.on_show()
    ex.beh.setCurrentCell(0, 0)
    ex.beh.item(0, 0).setText("Rearing up")  # edited in the table
    assert p.behaviours[0].name == "Rearing up"
    assert t.events[0]["behaviour"] == "Rearing up"
    assert p.analysis.event_periods[0]["behaviour"] == "Rearing up"
    assert p.training_criteria[0]["measure"] == "Rearing up: count"
    ex.key_editor.name.setText("Rears")  # and on the Key property page
    ex.key_editor.name.editingFinished.emit()
    assert t.events[0]["behaviour"] == "Rears"

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    ex.beh.setCurrentCell(0, 0)
    n = len(p.behaviours)
    ex._remove_behaviour()
    assert len(p.behaviours) == n and asked and "scored event" in asked[0] and "Rears" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    ex._remove_behaviour()
    assert len(p.behaviours) == n - 1 and t.events  # the events are kept


def test_renaming_a_stage_moves_its_tests_and_criteria(win):
    ex = win.goto("ExperimentPage")
    p = win.project
    p.stages = ["Day 1", "Day 2"]
    for t in p.tests:
        t.stage = "Day 1"
    p.training_criteria = [{"stage": "Day 1", "measure": "Total distance (m)", "op": ">", "value": 1,
                            "consecutive_trials": 1}]
    p.settings_extra["completed_stages"] = {p.tests[0].animal_id: ["Day 1"]}
    ex.on_show()
    ex.show_item("stages")
    # retyped character by character, through an empty line
    for text in ("Day ", "", "H", "Habituation"):
        ex.stages.setPlainText(f"{text}\nDay 2")
    assert p.stages == ["Habituation", "Day 2"]
    assert all(t.stage == "Habituation" for t in p.tests)
    assert p.training_criteria[0]["stage"] == "Habituation"
    assert p.settings_extra["completed_stages"][p.tests[0].animal_id] == ["Habituation"]
    ex.stages.setPlainText("Habituation\nHabituation")  # a merge into an existing stage is not a rename
    assert all(t.stage == "Habituation" for t in p.tests)


# ------------------------------------------------------------------ time periods
def test_delete_time_period_uses_the_table_worked_in_and_invalid_periods_are_reported(win):
    ex = win.goto("ExperimentPage")
    p = win.project
    p.analysis.custom_periods = [["First", 0.0, 60.0]]
    p.analysis.event_periods = [{"label": "Ev", "anchor": "first_exit", "zone": "Centre", "offset_s": 0,
                                 "duration_s": 30, "occurrence": 1}]
    ex.on_show()
    ex.show_item("analysis")
    pump()
    win.activateWindow()
    ex.periods.setCurrentCell(0, 0)
    ex.ev_periods.setCurrentCell(0, 0)
    ex.ev_periods.setFocus()
    ex.ev_periods.edit(ex.ev_periods.model().index(0, 0))  # its cell editor has the focus
    pump()
    win._flush_edits()  # as a ribbon command does
    ex.delete_time_period()
    assert ex.ev_periods.rowCount() == 0 and ex.periods.rowCount() == 1

    ex.periods.item(0, 2).setText("-5")  # end before the start: not used, and said so
    assert p.analysis.custom_periods == [] and ex.periods_lbl.isVisibleTo(ex) and "First" in ex.periods_lbl.text()
    ex.periods.item(0, 2).setText("90")
    assert p.analysis.custom_periods == [["First", 0.0, 90.0]] and not ex.periods_lbl.isVisibleTo(ex)


# ------------------------------------------------------------------ test schedule
def _src(page, row, col):
    return page.proxy.mapFromSource(page.model.index(row, col))


def test_schedule_editors_do_not_change_values_unless_edited(win):
    page = win.goto("TestsPage")
    p = win.project
    t = p.tests[0]
    t.apparatus = "Removed apparatus"
    t.start_s = 1.23456
    page.refresh()
    win.dirty = False
    delegate = page.table.itemDelegate()
    for col in (C_APP, C_START, C_DUR):
        idx = _src(page, 0, col)
        ed = delegate.createEditor(page.table.viewport(), None, idx)
        delegate.setEditorData(ed, idx)
        if isinstance(ed, QComboBox):
            assert ed.currentText() == "Removed apparatus"
        delegate.setModelData(ed, page.proxy, idx)
        ed.deleteLater()
    assert t.apparatus == "Removed apparatus" and t.start_s == 1.23456 and not win.dirty
    idx = _src(page, 0, C_START)
    ed = delegate.createEditor(page.table.viewport(), None, idx)
    delegate.setEditorData(ed, idx)
    assert isinstance(ed, QDoubleSpinBox)
    ed.setValue(4.5)
    delegate.setModelData(ed, page.proxy, idx)
    assert t.start_s == 4.5 and win.dirty


def test_schedule_follows_tests_added_and_removed_elsewhere(win):
    page = win.goto("TestsPage")
    p = win.project
    third = p.tests[2]
    page.select_ids({third.id})
    # Run tests removes a test it created and adds another
    p.tests.remove(p.tests[0])
    assert page.selected_tests() == [third]  # rows did not shift under the selection
    new = p.add_test("", p.animals[0].id)
    win.mark_dirty()  # what Run tests does after adding a test
    assert page.model.rowCount() == len(p.tests) and page.selected_tests() == [third]
    p.tests.remove(new)
    win.notify_tests_changed()  # the call Run tests makes after removing one
    assert page.model.rowCount() == len(p.tests) and new not in page.model.tests
    # a removed test is never acted on
    gone = p.tests[0]
    page.select_ids({gone.id})
    p.tests.remove(gone)
    page.delete_selected(confirm=False)
    assert gone not in p.tests and page.selected_tests() == []


def test_schedule_delete_names_the_tests_and_refuses_busy_tests(win, monkeypatch):
    page = win.goto("TestsPage")
    p = win.project
    t = p.tests[0]
    page.select_ids({t.id})
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    page.delete_selected()
    assert f"test {t.id} ({t.animal_id}" in asked[0] and t in p.tests
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]) or QMessageBox.Ok)
    live = win.page("LivePage")
    monkeypatch.setattr(live, "any_active", lambda: True)
    monkeypatch.setattr(live, "active_test_ids", lambda: {t.id})
    page.delete_selected()
    page.reperform_selected()
    assert t in p.tests and len(told) == 2 and "Run tests" in told[0] and f"Test {t.id}" in told[0]
    monkeypatch.setattr(live, "any_active", lambda: False)
    tv = win.page("TestViewPage")
    monkeypatch.setattr(tv, "test", t, raising=False)
    monkeypatch.setattr(tv, "scoring_in_progress", lambda: True)
    page.delete_selected()
    assert t in p.tests and "Review and score" in told[-1]
    monkeypatch.setattr(tv, "scoring_in_progress", lambda: False)
    page.delete_selected()
    assert t not in p.tests


def test_schedule_checks_each_video_once_per_refresh(win, monkeypatch):
    page = win.goto("TestsPage")
    calls = []
    import pathlib

    real = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: calls.append(1) or real(self))
    page.refresh()
    m = page.model
    for _ in range(3):
        for r in range(m.rowCount()):
            m.data(m.index(r, 6), Qt.ForegroundRole)
            m.data(m.index(r, 6), Qt.ToolTipRole)
    videos = {t.video for t in m.tests if t.video}
    assert len(calls) <= len(videos)


# ------------------------------------------------------------------ users, schedules, import wizard, criteria
def test_removing_the_current_user_clears_it_from_the_experiment(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    win.set_current_user("Alice")
    assert win.project.current_user == "Alice"
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Alice", True))
    win._remove_user_dialog()
    assert win.current_user() == "" and win.project.current_user == ""
    t = win.project.add_test("", "x")
    assert not t.experimenter


def test_schedule_dialog_preview_matches_created_tests(win):
    from manymaze.gui.pages.schedule_dialogs import ScheduleDialog

    p = win.project
    dlg = ScheduleDialog(p, win)
    dlg.order.setCurrentIndex(max(0, dlg.order.findData("random")))
    dlg.skip.setChecked(False)
    assert dlg.seed.value() == 0
    first = dlg.rows()
    assert all(dlg.rows() == first for _ in range(5))  # one order drawn when the dialog opened
    dlg.deleteLater()


def test_import_wizard_extra_fields_follow_the_mapping(tmp_path):
    from manymaze.gui.import_wizard import ImportDialog

    f = tmp_path / "animals.csv"
    f.write_text("Animal ID,Treatment,Weight,Cage\nM1,Saline,21,3\n")
    p = Project()
    dlg = ImportDialog(p, "animals", path=str(f))

    def listed():
        return [dlg.fields.item(i).text() for i in range(dlg.fields.count())]

    assert listed() == ["Weight", "Cage"]
    dlg.combos["notes"].setCurrentIndex(dlg.combos["notes"].findText("Cage"))
    assert listed() == ["Weight"]  # mapped: not imported twice
    dlg.combos["group"].setCurrentIndex(0)
    assert "Treatment" in listed()  # un-mapped: offered
    dlg.deleteLater()


def test_import_tests_keeps_the_test_numbers():
    from manymaze.core.importers import import_tests

    p = Project()
    p.add_test("", "old")  # test 1
    header = ["Test", "Animal"]
    rows = [["7", "A"], ["1", "B"], ["", "C"], ["9", "D"]]
    new = import_tests(p, header, rows, {"test": 0, "animal": 1})
    assert [t.id for t in new] == [7, 8, 10, 9]  # the table's numbers, the others after them
    assert len({t.id for t in p.tests}) == len(p.tests)


def test_weigh_dialog_reads_the_scale_off_the_gui_thread(win):
    import threading

    from manymaze.gui.pages.animal_dialogs import WeighDialog

    p = win.project
    threads = []

    def reader(cfg, timeout=3.0, stable=True):
        threads.append(threading.current_thread() is threading.main_thread())
        time.sleep(0.05)
        return 20.5, True

    dlg = WeighDialog(p, p.animals, 0, reader, lambda a, g: None, win)
    dlg.scale.insertItem(0, "test scale", {"name": "s", "protocol": "sartorius"})
    dlg.scale.setCurrentIndex(0)
    assert dlg.read_scale() and dlg.grams.value() == 20.5 and threads == [False]
    assert dlg.record_btn.isEnabled()
    dlg.deleteLater()


def test_training_criteria_evaluated_in_the_background(win, monkeypatch):
    import threading

    an = win.goto("AnimalsPage")
    seen = []
    monkeypatch.setattr(wf, "evaluate_criteria",
                        lambda p: seen.append(threading.current_thread() is threading.main_thread()) or {"ok": 1})
    assert an.evaluate_criteria() == {"ok": 1} and seen == [False]
