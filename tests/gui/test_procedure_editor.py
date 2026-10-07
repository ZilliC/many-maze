"""Smoke tests for the procedure editor and the I/O devices dialog."""

import json
import os

from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox

from manymaze.core import procedures as pr
from manymaze.core import templates
from manymaze.core.project import Project
from manymaze.gui.procedure_editor import IODevicesDialog, ProcedureEditor

app = QApplication.instance() or QApplication([])

DEVICES = [{"name": "box", "type": "virtual", "enabled": True, "channels": [
    {"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"}, {"name": "laser", "kind": "output"},
    {"name": "house_light", "kind": "output"}, {"name": "shocker", "kind": "output"}]}]


def make_project():
    p = Project()
    p.apparatus = [templates.build("open_field", 0, 0, 400, 400, size_cm=40)]
    p.io_devices = json.loads(json.dumps(DEVICES))
    p.procedures = [{"trigger": "time", "time_s": 5, "action": "mark", "payload": "Tone"}]
    return p


def field(ed, key):
    return ed._form_widgets[key]


def set_line(w, text):
    if isinstance(w, QComboBox):
        w.setCurrentText(text)
        w.lineEdit().editingFinished.emit()
    else:
        w.setText(text)
        w.editingFinished.emit()


def test_editor_builds_edits_nests_validates_and_round_trips():
    p = make_project()
    ed = ProcedureEditor(p)
    changes = []
    ed.changed.connect(lambda: changes.append(1))
    # old rules were converted on load
    assert "statements" in p.procedures[0] and p.procedures[0]["statements"][0]["event"] == "time_reached"
    assert ed.tree.topLevelItemCount() == 1 and ed.tree.topLevelItem(0).childCount() == 1

    ed.add_procedure()
    assert ed.proc_list.count() == 2 and ed.tree.topLevelItemCount() == 0
    # a When handler with an If inside, a Do inside the If and an Else branch
    path = ed.add_statement("when")
    assert path == (0,)
    ev = field(ed, "event")
    ev.setCurrentIndex(ev.findData("input_on"))
    app.processEvents()
    set_line(field(ed, "channel"), "lever")
    path = ed.add_statement("if", inside=True)
    assert path == (0, "body", 0)
    set_line(field(ed, "cond"), "activations('lever') % 5 == 0")
    ed.add_statement("do", inside=True)
    act = field(ed, "action")
    act.setCurrentIndex(act.findData("pellet"))
    app.processEvents()
    set_line(field(ed, "channel"), "pellet")
    ed.tree.setCurrentItem(ed.tree.item_for((0, "body", 0)))
    ed._toggle_else(True)
    ed.tree.setCurrentItem(ed.tree.item_for((0, "body", 0, "else")))
    ed.add_statement("set")
    set_line(field(ed, "var"), "misses")
    set_line(field(ed, "value"), "misses + 1")
    # top-level variable, a wait and a repeat; then indent/outdent/move
    ed.tree.setCurrentItem(ed.tree.item_for((0,)))
    ed.add_statement("var")
    set_line(field(ed, "name"), "misses")
    ed.add_statement("wait")
    set_line(field(ed, "seconds"), "2.5")
    ed.add_statement("repeat")
    ed.add_statement("comment")
    set_line(field(ed, "text"), "inside the loop")
    ed.indent_statement()
    proc = p.procedures[1]
    st = proc["statements"]
    assert [s["type"] for s in st] == ["when", "var", "wait", "repeat"]
    assert st[3]["body"] == [{"type": "comment", "text": "inside the loop"}]
    assert st[2]["seconds"] == 2.5
    assert st[0]["channel"] == "lever" and st[0]["body"][0]["cond"] == "activations('lever') % 5 == 0"
    assert st[0]["body"][0]["body"][0]["action"] == "pellet" and st[0]["body"][0]["body"][0]["channel"] == "pellet"
    assert st[0]["body"][0]["else"] == [{"type": "set", "var": "misses", "value": "misses + 1"}]
    ed.outdent_statement()
    assert [s["type"] for s in st] == ["when", "var", "wait", "repeat", "comment"]
    ed.move_statement(-1)
    assert [s["type"] for s in proc["statements"]] == ["when", "var", "wait", "comment", "repeat"]
    ed.toggle_statement()
    assert proc["statements"][3]["enabled"] is False
    ed.duplicate_statement()
    ed.remove_statement()
    assert len(proc["statements"]) == 5
    assert ed.issues == [] and "No problems" in ed.issue_list.item(0).text()

    # validation errors are shown inline and in the list
    ed.tree.setCurrentItem(ed.tree.item_for((2,)))
    set_line(field(ed, "seconds"), "2 +")
    assert any(path == (2,) for _i, path, _m in ed.issues)
    item = ed.tree.item_for((2,))
    assert "syntax error" in item.toolTip(0)
    assert ed._issue_lbl is not None and ed._issue_lbl.isVisibleTo(ed) or ed._issue_lbl.text()
    ed.issue_list.itemClicked.emit(ed.issue_list.item(0))
    assert ed._cur_path() == (2,)
    set_line(field(ed, "seconds"), "2")
    assert ed.issues == []

    # simulated drag & drop: move the wait into the repeat through the tree, then rebuild from the tree
    tree = ed.tree
    wait_item = tree.takeTopLevelItem(2)
    tree.topLevelItem(3).addChild(wait_item)
    tree.drag_item = wait_item
    ed._tree_dropped()
    assert [s["type"] for s in proc["statements"]] == ["when", "var", "comment", "repeat"]
    assert proc["statements"][3]["body"] == [{"type": "wait", "mode": "seconds", "seconds": 2}]
    assert ed._cur_path() == (3, "body", 0)
    # the else branch survives a rebuild from the tree
    assert proc["statements"][0]["body"][0]["else"][0]["var"] == "misses"

    # round trip through JSON and the engine
    data = json.loads(json.dumps(p.procedures))
    assert data == p.procedures and pr.validate(data, ed.context()) == []
    eng = pr.ProcedureEngine(data, seed=0)
    eng.start(0)
    for i in range(30):
        eng.update_state(i / 10, {})
    eng.stop(3)
    assert eng.errors == []
    ed.set_procedure_json(1, data[1])
    assert ed.tree.topLevelItemCount() == 4

    # procedures list: rename/disable through the item, duplicate, examples, move, remove
    it = ed.proc_list.item(1)
    it.setText("Lever")
    from PySide6.QtCore import Qt
    it.setCheckState(Qt.Unchecked)
    assert p.procedures[1]["name"] == "Lever" and p.procedures[1]["enabled"] is False
    ed.duplicate_procedure()
    assert p.procedures[2]["name"] == "Lever copy"
    for ex in pr.EXAMPLES.values():
        ed.add_procedure(json.loads(json.dumps(ex)))
    ed.move_procedure(-1)
    ed.remove_procedure()
    assert len(p.procedures) == 2 + 1 + len(pr.EXAMPLES) - 1
    assert changes
    ed.set_read_only(True)
    assert not ed.add_btn.isEnabled() and not ed.form_box.isEnabled()


def test_every_statement_event_and_action_form_builds():
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    ed = ProcedureEditor(parent=host)
    assert ed.parent() is host and ed.project is None
    p = make_project()
    ed = ProcedureEditor(p)
    ed.add_procedure()
    for t in ("when", "wait", "if", "repeat", "set", "do", "stop", "comment", "var"):
        ed.add_statement(t)
        assert ed._form_widgets or t == "if"
    ed.tree.setCurrentItem(ed.tree.item_for((0,)))
    ev = field(ed, "event")
    for i in range(ev.count()):
        if ev.itemData(i):
            ev.setCurrentIndex(i)
            app.processEvents()
            ev = field(ed, "event")
    ed.tree.setCurrentItem(ed.tree.item_for((5,)))
    act = field(ed, "action")
    for i in range(act.count()):
        if act.itemData(i):
            act.setCurrentIndex(i)
            app.processEvents()
            act = field(ed, "action")
            assert ed.tree.currentItem().text(0).startswith("Do: ")
    for mode in ("until", "event", "seconds"):
        ed.tree.setCurrentItem(ed.tree.item_for((1,)))
        m = field(ed, "mode")
        m.setCurrentIndex(m.findData(mode))
        app.processEvents()
    assert p.procedures[1]["statements"][1]["mode"] == "seconds"


def test_io_devices_dialog(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    p = make_project()
    dlg = IODevicesDialog(p)
    changed = []
    dlg.changed.connect(lambda: changed.append(1))
    assert dlg.dev_list.count() == 1 and dlg.ch_table.rowCount() == 5
    dlg.add_device("arduino")
    assert dlg._cur()["name"] == "box2" and dlg.ch_table.isColumnHidden(5) and not dlg.ch_table.isColumnHidden(2)
    dlg.add_channel({"name": "wheel", "kind": "encoder", "pin": 2, "pin_b": 3, "counts_per_rev": 400})
    dlg.add_channel()
    dlg.ch_table.item(1, 7).setText("debounce_ms=30, pullup=0")
    assert dlg._cur()["channels"][1]["debounce_ms"] == 30 and dlg._cur()["channels"][0]["counts_per_rev"] == 400
    dlg.add_device("audio")
    assert dlg.ch_box.isHidden()
    # live status with the simulated device
    dlg.dev_list.setCurrentRow(0)
    dlg.configs = [c for c in dlg.configs if c["type"] != "arduino"]
    dlg._refresh_list(0)
    dlg.connect_devices()
    rows = dlg.status.rowCount()
    assert rows == 5
    dlg.toggle_channel("box", "pellet", "output")
    assert dlg.manager.devices["box"].outputs["pellet"] == 1
    dlg.toggle_channel("box", "lever", "input")
    vals = {dlg.status.item(r, 1).text(): dlg.status.item(r, 3).text() for r in range(rows)}
    assert vals["pellet"] == "ON" and vals["lever"] == "ON"
    dlg.status.selectRow(2)
    dlg.test_selected()
    assert dlg.manager.devices["box"].outputs["laser"] == 1
    dlg.accept()
    assert changed and [d["name"] for d in p.io_devices] == ["box", "speakers"]
    assert dlg.manager is None


def test_screenshot(tmp_path):
    """Render the editor (with the examples) and the I/O dialog side by side for visual inspection."""
    from PySide6.QtGui import QPainter, QPixmap

    p = make_project()
    p.procedures = [json.loads(json.dumps(ex)) for ex in pr.EXAMPLES.values()]
    p.procedures[1]["statements"][2]["body"][1]["cond"] = "due and"  # show an error
    ed = ProcedureEditor(p)
    ed.resize(1100, 560)
    ed.proc_list.setCurrentRow(1)
    ed.tree.setCurrentItem(ed.tree.item_for((2, "body", 1, "body", 0)))
    ed.show()
    app.processEvents()
    dlg = IODevicesDialog(p)
    dlg.connect_devices()
    dlg.toggle_channel("box", "house_light", "output")
    dlg.resize(1100, 560)
    dlg.show()
    app.processEvents()
    a, b = ed.grab(), dlg.grab()
    out = QPixmap(max(a.width(), b.width()), a.height() + b.height())
    out.fill()
    qp = QPainter(out)
    qp.drawPixmap(0, 0, a)
    qp.drawPixmap(0, a.height(), b)
    qp.end()
    path = os.environ.get("MANYMAZE_SHOT", str(tmp_path / "shot_procedures.png"))
    assert out.save(path)
    dlg.reject()
    ed.close()


def test_touchscreen_window_drives_engine():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from manymaze.gui.touchscreen import TouchStimulusWindow, default_areas

    p = make_project()
    p.settings_extra = {"touchscreen": {"areas": default_areas(2), "outline": True}}
    win = TouchStimulusWindow.from_project(p)
    win.resize(400, 300)
    proc = {"name": "touch", "statements": [
        {"type": "do", "action": "show_stimulus", "area": "left", "shape": "star", "color": "#ffcc00"},
        {"type": "do", "action": "show_stimulus", "area": "right", "shape": "bars"},
        {"type": "when", "event": "touch", "area": "left", "body": [
            {"type": "do", "action": "clear_screen"}, {"type": "do", "action": "mark", "name": "correct"}]},
        {"type": "when", "event": "touch", "area": "right",
         "body": [{"type": "do", "action": "mark", "name": "wrong"}]},
        {"type": "when", "event": "touch_outside", "body": [{"type": "do", "action": "mark", "name": "outside"}]}]}
    assert pr.validate([proc], {"areas": ["left", "right"]}) == []
    marks = []
    eng = pr.ProcedureEngine([proc], on_stimulus=win.handle, on_mark=lambda n, t: marks.append(n))
    win.connect_engine(eng, clock=lambda: 1.5)
    eng.start(0)
    assert set(win.stimuli) == {"left", "right"}
    win.show()
    app.processEvents()
    assert not win.grab().isNull()
    r = win.area_rect(win.areas[1])
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    r = win.area_rect(win.areas[0])
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
    assert marks == ["wrong", "outside", "correct"] and win.stimuli == {}
    assert [a for a, _x, _y in win.touches] == ["right", "", "left"]
    eng.stop(2)
    from manymaze.core.iodevices import io_measures
    m = io_measures(eng.io_events, 2)
    assert m["touch left: activations"] == 1 and m["left: times on"] == 1
    win.close()


def test_io_dialog_watchdog_default_and_reserved_options():
    p = make_project()
    p.io_devices = []
    dlg = IODevicesDialog(p)
    dlg.add_device("arduino")
    dlg.add_channel({"name": "lever", "kind": "input", "pin": 2})
    dlg._save_channels()
    assert dlg.f_watchdog.value() == 0  # input-only board: the core default is off
    dlg.f_name.setText("box")
    dlg._save_device()
    assert "watchdog_ms" not in dlg._cur()
    dlg.add_channel({"name": "pellet", "kind": "output", "pin": 8})
    dlg._save_channels()
    assert dlg.f_watchdog.value() == 2000 and "watchdog_ms" not in dlg._cur()
    dlg.f_watchdog.setValue(500)
    assert dlg._cur()["watchdog_ms"] == 500
    # the Options column cannot override the structured columns
    dlg.ch_table.item(0, 7).setText("pin=99, kind=output, debounce_ms=5")
    assert dlg._cur()["channels"][0] == {"name": "lever", "kind": "input", "pin": 2, "debounce_ms": 5}
    dlg.accept()
    assert p.io_devices[0]["watchdog_ms"] == 500


def test_procedure_list_read_only_and_unique_rename():
    from PySide6.QtCore import Qt

    p = make_project()
    ed = ProcedureEditor(p)
    ed.add_procedure()
    first = p.procedures[0]["name"]
    ed.proc_list.item(1).setText(first)
    assert p.procedures[1]["name"] == f"{first} 2" and ed.proc_list.item(1).text() == f"{first} 2"
    ed.set_read_only(True)
    flags = ed.proc_list.item(0).flags()
    assert not flags & Qt.ItemIsEditable and not flags & Qt.ItemIsUserCheckable
    ed.set_read_only(False)
    assert ed.proc_list.item(0).flags() & Qt.ItemIsEditable
