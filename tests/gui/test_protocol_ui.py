"""The Protocol section in the style of ANY-maze: protocol elements in the explorer, one property page each, ribbon
groups, keys with Simple / Toggle / Radio / Event modes and procedure statements painted as coloured blocks."""

import json
import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem

from manymaze.core import procedures as pr
from manymaze.core.demo import create_demo_project
from manymaze.core.workflow import key_mode, mode_to_kind
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.base import DETECTION_SECTIONS, DETECTION_SPEC, SettingsForm
from manymaze.gui.pages.experiment import ELEMENTS
from manymaze.gui.procedure_editor import ProcedureEditor
from manymaze.gui.statement_tree import ELSE, TYPE_ROLE, block_parts
from shots import shot_path

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


def click_element(w, page, key):
    sec = w.sections[0]
    sec.explorer.setCurrentItem(sec.item_for(page, key))
    pump()


def test_elements_listed_in_explorer_and_shown_one_at_a_time(win):
    ex = win.goto("ExperimentPage")
    sec = win.sections[0]
    top = sec.item_for(ex)
    assert [top.child(i).text(0) for i in range(top.childCount())] == [label for _k, label, _i in ELEMENTS]
    for key, label, _icon in ELEMENTS:
        click_element(win, ex, key)
        assert ex.element == key and ex.stack.currentWidget() is ex.elements[key]
        assert ex.elements[key].title_lbl.text() == label
        assert sec.explorer.currentItem() is sec.item_for(ex, key)
        titles = [g.title for g in sec.panel.context]
        assert titles[0] == "Protocol" and titles[-1] == "Templates"
        # every element page fits the window without horizontal scrolling
        page = ex.elements[key]
        assert page.widget().minimumSizeHint().width() <= page.viewport().width() + 1
    # contextual commands follow the element
    click_element(win, ex, "keys")
    assert {"New key", "Delete key"} <= set(sec.panel.actions_text())
    click_element(win, ex, "procedures")
    assert {"New procedure", "Duplicate procedure", "Delete procedure"} <= set(sec.panel.actions_text())
    # going back to the page keeps the element
    win.goto("AnimalsPage")
    win.goto("ExperimentPage")
    assert ex.element == "procedures" and ex.stack.currentWidget() is ex.elements["procedures"]


def test_add_item_menu(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    names = {a.text(): a for a in ex.add_item_act.menu().actions()}
    assert {"New apparatus", "New stage", "New key", "New procedure", "New time period"} <= set(names)
    n = len(p.stages)
    names["New stage"].trigger()
    assert ex.element == "stages" and len(p.stages) == n + 1
    n = len(p.behaviours)
    names["New key"].trigger()
    assert ex.element == "keys" and len(p.behaviours) == n + 1 and ex.beh.currentRow() == n
    assert ex.key_editor.name.text() == p.behaviours[-1].name
    n = len(p.procedures)
    names["New procedure"].trigger()
    assert ex.element == "procedures" and len(p.procedures) == n + 1
    assert ex.proc_editor.proc_list.count() == n + 1
    names["New time period"].trigger()
    assert ex.element == "analysis" and p.analysis.custom_periods
    ex.new_event_period()
    assert p.analysis.event_periods
    n = len(p.apparatus)
    names["New apparatus"].trigger()
    assert len(p.apparatus) == n + 1 and win.current_page() is win.page("ApparatusPage")


def test_period_and_criterion_tables(win):
    ex = win.goto("ExperimentPage")
    p = win.project
    p.analysis.custom_periods = [["First minute", 0.0, 60.0]]
    p.stages = ["Training"]
    ex.on_show()
    assert ex.periods.rowCount() == 1 and ex.periods.item(0, 2).text() == "60"
    ex.new_time_period()
    assert p.analysis.custom_periods[-1] == ["Period 2", 60.0, 120.0]
    ex.periods.item(1, 2).setText("oops")  # an invalid row is left out
    assert p.analysis.custom_periods == [["First minute", 0.0, 60.0]]
    ex.periods.setCurrentCell(1, 0)
    ex.delete_time_period()
    assert ex.periods.rowCount() == 1
    ex.new_criterion()
    c = p.training_criteria[0]
    assert c["stage"] == "Training" and c["action_fail"] == {"after_trials": 0, "action": "none"}
    ex.crit.cellWidget(0, 6).setValue(4)
    ex.crit.item(0, 3).setText("2,5")
    c = p.training_criteria[0]
    assert c["value"] == 2.5 and c["action_fail"] == {"after_trials": 4, "action": "retire"}
    ex.on_show()  # shown again from the project
    assert ex.crit.cellWidget(0, 6).value() == 4 and ex.crit.item(0, 3).text() == "2.5"


def test_keys_property_page_modes(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    click_element(win, ex, "keys")
    ex.new_key()
    ex.new_key()
    r = ex.beh.currentRow()
    ed = ex.key_editor
    # Radio: a toggle in an exclusive set
    ed.mode.setCurrentIndex(ed.mode.findData("radio"))
    b = p.behaviours[r]
    assert (b.kind, b.group) == ("state", "radio") and ex.beh.cellWidget(r, 2).currentText() == "Radio"
    assert ed.group.isVisibleTo(ed)
    ed.mode.setCurrentIndex(ed.mode.findData("simple"))
    assert p.behaviours[r].kind == "hold"
    ed.mode.setCurrentIndex(ed.mode.findData("event"))
    assert p.behaviours[r].kind == "point"
    ed.mode.setCurrentIndex(ed.mode.findData("toggle"))
    assert (p.behaviours[r].kind, p.behaviours[r].group) == ("state", "")
    ed.name.setText("Sniffing")
    ed.name.editingFinished.emit()
    ed.stroke.setCurrentIndex(ed.stroke.findData("x"))
    assert p.behaviours[r].name == "Sniffing" and p.behaviours[r].key == "x"
    assert ex.beh.item(r, 0).text() == "Sniffing" and ex.beh.item(r, 1).text() == "X"
    # the table drives the property page too
    ex.beh.cellWidget(r, 2).setCurrentIndex(2)  # Radio
    assert p.behaviours[r].group == "radio" and ed.mode.currentData() == "radio"
    ex.duplicate_key()
    assert p.behaviours[-1].name == "Sniffing copy" and p.behaviours[-1].key != "x"
    ex._remove_behaviour()
    assert p.behaviours[-1].name == "Sniffing"
    assert [key_mode(*mode_to_kind(m, "")) for m in ("simple", "toggle", "radio", "event")] == \
        ["simple", "toggle", "radio", "event"]


def test_protocol_mode_template_and_blind(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    ex.mode.setCurrentIndex(ex.mode.findData("takenote"))
    assert p.settings_extra["mode"] == "takenote" and not ex.takenote_lbl.isHidden()
    ex.apply_template("water_maze")
    assert p.protocol == "water_maze" and p.test_duration_s == 60
    assert ex.protocol.currentData() == "water_maze"
    ex.blind_act.trigger()
    assert p.blind and ex.blind.isChecked()
    ex.blind.setChecked(False)
    assert not ex.blind_act.isChecked() and not p.blind
    p.detection.threshold = 77
    ex.on_show()
    ex.restore_detection_defaults()
    assert p.detection.threshold == 25 and ex.det_form.editors["threshold"].value() == 25


def test_procedure_editors_stay_in_sync(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    click_element(win, ex, "procedures")
    p.procedures.append(json.loads(json.dumps(next(iter(pr.EXAMPLES.values())))))
    click_element(win, ex, "keys")
    click_element(win, ex, "procedures")  # reloads from the project (another editor may have changed it)
    assert ex.proc_editor.proc_list.count() == len(p.procedures)


def test_settings_form_sections():
    from manymaze.core.tracking import DetectionSettings

    form = SettingsForm(DETECTION_SPEC + [("extra_attr", "Some new setting", "int", (0, 9, 1), "")],
                        sections=DETECTION_SECTIONS)
    assert list(form.forms) == [t for t, _a in DETECTION_SECTIONS]
    assert form.forms["Tracking quality"].rowCount() == 6  # unknown attributes land in the last section
    assert all(w.minimumWidth() >= 200 for a, w in form.editors.items() if a in form.labels)
    s = DetectionSettings()
    s.extra_attr = 3
    form.load(s)
    form.editors["threshold"].setValue(40)
    assert s.threshold == 40


def test_statement_blocks(tmp_path):
    assert block_parts("Do: Pellet — pellet", "do") == ("Action:", "Pellet — pellet")
    assert block_parts("Wait until x > 3", "wait") == ("Wait until:", "x > 3")
    assert block_parts("# hello", "comment") == ("Note:", "hello")
    assert block_parts("Else", ELSE) == ("Else:", "")
    from manymaze.core.project import Project

    p = Project()
    p.procedures = [json.loads(json.dumps(ex)) for ex in pr.EXAMPLES.values()]
    ed = ProcedureEditor(p)
    ed.resize(1100, 520)
    ed.proc_list.setCurrentRow(1)
    ed.show()
    pump()
    it = ed.tree.topLevelItem(0)
    assert it.data(0, TYPE_ROLE) == p.procedures[1]["statements"][0]["type"]
    assert ed.tree.itemDelegate().sizeHint(QStyleOptionViewItem(), ed.tree.indexFromItem(it)).height() >= 30
    assert ed.grab().save(shot_path("shot_procedure_blocks.png"))
    ed.close()


def test_screenshots(win):
    ex = win.goto("ExperimentPage")
    for key, *_ in ELEMENTS:
        click_element(win, ex, key)
        pump(3)
        assert win.grab().save(shot_path(f"shot_protocol_{key}.png"))
