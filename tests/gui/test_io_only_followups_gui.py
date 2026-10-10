"""Input/output only mode in the GUI, the follow-ups of phase 1: several tests at once without cameras (operant
chambers side by side), the operant chamber presets (Protocol and I/O devices dialog), procedures that need the
animal refused when arming, and the Review page of a test without a video (its I/O log instead of the video). The
I/O devices are virtual ones."""

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QToolButton

from manymaze.core import ioconfig
from manymaze.core.live import IOSession
from manymaze.core.project import Behaviour, Project
from manymaze.gui.io_devices_dialog import IODevicesDialog, OperantPresetDialog
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])

# every lever press gives a pellet (written for chamber1: each test's own chamber when several run)
FR1 = {"name": "FR1", "enabled": True, "statements": [
    {"type": "var", "name": "presses", "value": 0, "result": True},
    {"type": "when", "event": "input_on", "device": "chamber1", "channel": "lever", "body": [
        {"type": "set", "var": "presses", "value": "presses + 1"},
        {"type": "do", "action": "output_on", "device": "chamber1", "channel": "pellet"},
        {"type": "wait", "seconds": 0.1},
        {"type": "do", "action": "output_off", "device": "chamber1", "channel": "pellet"}]}]}


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout_ms=6000):
    for _ in range(timeout_ms // 20):
        if cond():
            return True
        wait(20)
    return cond()


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    p = Project(name="Operant", test_duration_s=1.0)
    p.save(tmp_path / "op.mmaze")
    p.settings_extra["mode"] = "io_only"
    p.io_devices = ioconfig.operant_devices("coulbourn", "virtual", 2)
    p.procedures = [FR1]
    for a in ("R1", "R2"):
        p.ensure_animal(a)
    p.save()
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def test_several_chambers_at_once(win):
    p = win.project
    page = win.goto("LivePage")
    assert page.mode_acts["multi"].isEnabled()
    page.mode_acts["multi"].trigger()
    e1, e2 = page.add_session_row(), page.add_session_row()
    assert (e1.source_key, e1.meta["device"], e1.meta["animal"], e1.meta["apparatus"]) == (None, "chamber1", "R1", "")
    assert (e2.meta["device"], e2.meta["animal"]) == ("chamber2", "R2")  # the next free chamber and animal
    assert page.sess_table.rowCount() == 2 and page.sess_table.horizontalHeaderItem(0).text() == "I/O device"
    assert [page.sess_table.item(r, 0).text() for r in (0, 1)] == ["chamber1", "chamber2"]
    assert not page.row_form.isRowVisible(page.row_source)
    assert page.row_device.currentData() == "chamber2"  # (the panel added last is selected)
    assert not page.add_source_act.isEnabled() and not page.camera_act.isEnabled()
    assert page.add_panel_act.isEnabled() and page.explorer_items()[0][0] == "chamber1: R1"
    panel = page.mosaic.panels[e1.id]
    assert panel.view.message.startswith("Input/output only") and panel.tabs.tabText(2) == "Inputs"
    assert "Camera options…" not in [a.text() for a in panel.menu.actions()]
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.duration.setValue(1.5)
    n = len(p.tests)
    page.start_all_act.trigger()  # arm every panel: each test starts at once on its own clock
    s1, s2 = e1.session, e2.session
    assert isinstance(s1, IOSession) and isinstance(s2, IOSession) and not page.group.runners
    assert s1.devices.alias == "chamber1" and s2.devices.alias == "chamber2" and len(p.tests) == n + 2
    assert wait_for(lambda: s1.state == s2.state == "running")
    page.devices.set_input("chamber1", "lever", 1)  # a press in chamber 1 only
    assert wait_for(lambda: s1.result_variables.get("presses") == 1 or any(
        e["channel"] == "pellet" for e in s1.io_events))
    page.devices.set_input("chamber1", "lever", 0)
    page._update_panels()
    assert "Inputs: lever" in panel.view.message and "(1×)" in panel.view.message
    assert "Outputs:" in panel.view.message and panel.vals["zone"].text() == "—"
    panel.tabs.setCurrentIndex(2)
    page._update_panels()
    assert panel.zones.item(0, 0).text() == "lever" and panel.zones.item(0, 2).text() == "1"
    page.pause_all_act.trigger()
    assert s1.state == s2.state == "paused"
    page.resume_all_act.trigger()
    assert s1.state == s2.state == "running"
    page.tabs.setCurrentWidget(page.monitor_tab)
    page.sess_table.selectRow(0)
    page._refresh_monitor(force=True)
    assert page.monitor.vals["zone"].text() == "no camera (I/O only)"
    assert wait_for(lambda: e1.saved and e2.saved)  # ended at their duration and saved
    by_animal = {t.animal_id: t for t in p.tests[n:]}
    assert set(by_animal) == {"R1", "R2"} and by_animal["R2"].result_variables == {"presses": 0}
    assert by_animal["R1"].status == "scored" and by_animal["R1"].result_variables == {"presses": 1}
    assert by_animal["R1"].duration_s == 1.5 and not by_animal["R1"].video
    assert any(e["device"] == "chamber1" and e["channel"] == "pellet" for e in by_animal["R1"].io_events)
    assert e1.meta["trial"] == 2  # the next trial is ready
    layout = p.settings_extra["live"]["multi"]
    assert layout["sources"] == [] and [(d["source"], d["device"]) for d in layout["sessions"]] == [
        (-1, "chamber1"), (-1, "chamber2")]


def test_layout_kept_and_mode_switch(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_mode("multi")
    e = page.add_session_row(animal="R2", device="chamber2")
    page.row_trial.setValue(4)
    assert e.meta["trial"] == 4
    win.save()
    win.set_project(Project.load(p.path))
    page = win.goto("LivePage")
    page.set_mode("multi")
    [r] = page._entries()
    assert r.source_key is None and (r.meta["device"], r.meta["animal"], r.meta["trial"]) == ("chamber2", "R2", 4)
    p = win.project
    p.settings_extra["mode"] = "tracking"  # the camera panels are shown, the chambers' layout is kept
    page.on_show()
    assert page._entries() == [] and page.sess_table.rowCount() == 0
    assert page.sess_table.horizontalHeaderItem(0).text() == "Source"
    page._save_group_layout()
    assert [d["source"] for d in p.settings_extra["live"]["multi"]["sessions"]] == [-1]
    p.settings_extra["mode"] = "io_only"
    page.on_show()
    assert page._entries() == [r] and page.sess_table.rowCount() == 1


def test_a_scheduled_start_of_the_chambers(win):
    page = win.goto("LivePage")
    page.set_mode("multi")
    e1, e2 = page.add_session_row(), page.add_session_row()
    page.duration.setValue(0.0)  # until stopped
    page.start_mode.setCurrentIndex(page.start_mode.findData("scheduled"))
    assert page.arm_all() == 2
    [sch] = page.group.schedules
    assert sch.entry_ids == [e1.id, e2.id]
    wait(100)
    assert e1.state == e2.state == "waiting"  # their clocks run: waiting for the start time
    page.group.tick(sch.next_fire)
    assert wait_for(lambda: e1.state == e2.state == "running")
    page.stop_all_act.trigger()  # Save
    assert wait_for(lambda: e1.saved and e2.saved)


def test_procedures_that_need_the_animal_are_refused(win, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a[2]) or QMessageBox.Ok)
    p = win.project
    p.procedures = [FR1, {"name": "Zones", "enabled": True, "statements": [
        {"type": "when", "event": "zone_enter", "zone": "", "body": [
            {"type": "do", "action": "output_on", "device": "chamber1", "channel": "house_light"}]}]}]
    page = win.goto("LivePage")
    n = len(p.tests)
    assert not page.arm() and page.session is None and len(p.tests) == n
    assert shown and "“Animal enters zone” needs the animal tracked by a camera" in shown[-1]
    assert "Input/output only mode" in shown[-1]
    page.set_mode("multi")
    page.add_session_row()
    assert page.arm_all() == 0
    ex = win.goto("ExperimentPage")
    ex.show_item("procedures")
    ex.mode.setCurrentIndex(ex.mode.findData("tracking"))  # with a camera the same procedures are fine
    assert ex.proc_editor.issue_list.item(0).text().startswith("✓")
    ex.mode.setCurrentIndex(ex.mode.findData("io_only"))
    assert "needs the animal" in ex.proc_editor.issue_list.item(0).text()


def test_chamber_presets_from_the_protocol_and_the_io_devices_dialog(win, monkeypatch):
    p = win.project

    def choose(preset, type_, n):
        def exec_(dlg):
            dlg.f_preset.setCurrentIndex(dlg.f_preset.findData(preset))
            dlg.f_type.setCurrentIndex(dlg.f_type.findData(type_))
            dlg.f_n.setValue(n)
            return QDialog.Accepted
        monkeypatch.setattr(OperantPresetDialog, "exec", exec_)

    ex = win.goto("ExperimentPage")
    ex.show_item("protocol")
    assert ex.protocol_form.isRowVisible(ex.chambers_row) and "2 I/O devices" in ex.chambers_lbl.text()
    choose("med_associates", "arduino", 2)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)  # replace the devices
    assert ex.set_up_chambers()
    assert [(d["name"], d["type"]) for d in p.io_devices] == [("chamber1", "arduino"), ("chamber2", "arduino")]
    assert {c["name"] for c in p.io_devices[0]["channels"]} >= {"left_lever", "right_lever", "shocker"}
    assert p.settings_extra["operant_preset"] == "med_associates" and win.dirty
    assert "Med Associates-style chamber" in ex.chambers_lbl.text()
    assert ex.io_list.count() == 2
    choose("lafayette", "virtual", 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)  # added to them
    assert ex.set_up_chambers()
    assert [d["name"] for d in p.io_devices] == ["chamber1", "chamber2", "chamber"]
    assert p.settings_extra["operant_preset"] == "lafayette"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    assert not ex.set_up_chambers() and len(p.io_devices) == 3
    ex.mode.setCurrentIndex(ex.mode.findData("tracking"))
    assert not ex.protocol_form.isRowVisible(ex.chambers_row)

    # the I/O devices dialog: Add ▸ Operant chamber (preset)…
    dlg = IODevicesDialog(p)
    add = next(b for b in dlg.findChildren(QToolButton) if b.text() == "Add ▾")
    assert "Operant chamber (preset)…" in [a.text() for a in add.menu().actions()]
    choose("coulbourn", "nidaq", 2)
    assert dlg.add_chambers()
    names = [c["name"] for c in dlg.configs]
    assert names[-2:] == ["chamber3", "chamber4"] and dlg.configs[-1]["channels"][0]["pin"] == "port0/line0"
    assert dlg.dev_list.currentRow() == 3 and dlg.f_type.currentData() == "nidaq"
    dlg.accept()
    assert [d["name"] for d in p.io_devices][-2:] == ["chamber3", "chamber4"]
    assert p.settings_extra["operant_preset"] == "coulbourn"
    dlg.deleteLater()


def test_preset_dialog_preview():
    dlg = OperantPresetDialog(preset="coulbourn")
    assert dlg.preset() == "coulbourn" and dlg.device_type() == "arduino"
    assert dlg.table.rowCount() == len(ioconfig.OPERANT_PRESETS["coulbourn"]["channels"])
    assert [dlg.table.item(0, c).text() for c in range(4)] == ["lever", "Digital input", "2", "—"]
    assert "Coulbourn" not in dlg.desc.text() and "nose pokes" in dlg.desc.text()
    dlg.f_type.setCurrentIndex(dlg.f_type.findData("labjack"))
    assert dlg.table.item(0, 2).text() == "FIO0"
    dlg.f_preset.setCurrentIndex(dlg.f_preset.findData("custom"))
    assert dlg.table.rowCount() == 0
    dlg.f_n.setValue(3)
    assert [d["name"] for d in dlg.devices(["chamber1"])] == ["chamber2", "chamber3", "chamber4"]
    dlg.deleteLater()


def test_review_of_a_test_without_a_video(win):
    p = win.project
    p.behaviours = [Behaviour("Grooming", "g", "state")]
    io = [{"t": 0.5, "device": "chamber1", "channel": "lever", "kind": "input", "value": 1, "type": "digital"},
          {"t": 0.6, "device": "chamber1", "channel": "lever", "kind": "input", "value": 0, "type": "digital"},
          {"t": 0.5, "device": "chamber1", "channel": "pellet", "kind": "output", "value": 1, "type": "digital"},
          {"t": 0.7, "device": "chamber1", "channel": "pellet", "kind": "output", "value": 0, "type": "digital"}]
    t = p.add_test("", "R1", "", duration_s=2.0, status="scored", io_events=io, result_variables={"presses": 1},
                   events=[{"behaviour": "Grooming", "t": 1.0, "t_end": 1.5}])
    scored = p.add_test("", "R2", "", duration_s=2.0, status="scored",
                        events=[{"behaviour": "Grooming", "t": 0.2, "t_end": 0.4}])  # TakeNote: no I/O log
    tv = win.goto("TestViewPage")
    win.open_test(t.id)
    assert tv.view_stack.currentWidget() is tv.io_review and tv.clock_box.isHidden()
    rv = tv.io_review
    assert "1 input, 1 output, 4 I/O events, 1 scored event" in rv.summary.text()
    assert "presses = 1" in rv.summary.text() and "test duration 2 s" in rv.summary.text()
    rows = [[rv.table.item(r, c).text() for c in range(4)] for r in range(rv.table.rowCount())]
    assert rows[0] == ["0.500", "Input", "lever", "on"] and ["1.000", "Event", "Grooming", "0.50 s"] in rows
    labels = [x.get_text() for x in rv.canvas.figure.axes[0].get_yticklabels()]
    assert set(labels) == {"lever", "pellet", "Grooming"}
    rv.filter.setText("pellet")
    assert sum(not rv.table.isRowHidden(r) for r in range(rv.table.rowCount())) == 2
    tv.tabs.setCurrentIndex(0)
    tv.refresh_results()
    res = tv.results_dict()
    assert res["lever: activations"] == "1" and res["Variable: presses"] == "1"
    assert "from the I/O log" in tv.results_lbl.text()
    win.open_test(scored.id)  # scored by hand without a video: the observation clock, as before
    assert tv.view_stack.currentWidget() is tv.player and not tv.clock_box.isHidden()
    win.goto("TestsPage")
    win.open_test(t.id)  # (the Test schedule opens tests the same way)
    assert win.current_page() is tv and tv.view_stack.currentWidget() is tv.io_review
