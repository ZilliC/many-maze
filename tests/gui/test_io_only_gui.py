"""Input/output only mode in the GUI: the Protocol's mode, and Run tests running a test without a camera (the I/O
devices are virtual ones)."""

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.live import IOSession
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])

FR1 = {"name": "FR1", "enabled": True, "statements": [
    {"type": "var", "name": "presses", "value": 0, "result": True},
    {"type": "when", "event": "input_on", "device": "box", "channel": "lever", "body": [
        {"type": "set", "var": "presses", "value": "presses + 1"},
        {"type": "do", "action": "output_on", "device": "box", "channel": "pellet"},
        {"type": "wait", "seconds": 0.2},
        {"type": "do", "action": "output_off", "device": "box", "channel": "pellet"}]}]}


def wait(ms):
    """Run the event loop for ms (the I/O session's clock runs in its own thread meanwhile)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    p = Project(name="Operant", test_duration_s=1.0)
    p.save(tmp_path / "op.mmaze")
    p.settings_extra["mode"] = "io_only"
    p.io_devices = [{"name": "box", "type": "virtual", "channels": [{"name": "lever", "kind": "input"},
                                                                    {"name": "pellet", "kind": "output"}]}]
    p.procedures = [FR1]
    p.ensure_animal("R1")
    p.save()
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def test_protocol_mode(win):
    ex = win.goto("ExperimentPage")
    assert ex.mode.currentData() == "io_only"
    ex.show_item("tracking")
    assert not ex.takenote_lbl.isHidden() and "Input/output only" in ex.takenote_lbl.text()
    ex.mode.setCurrentIndex(ex.mode.findData("tracking"))
    assert win.project.settings_extra["mode"] == "tracking" and ex.takenote_lbl.isHidden()


def test_run_a_test_without_a_camera(win):
    p = win.project
    page = win.goto("LivePage")
    assert page.io_only and page.src_box.isHidden() and page.det_box.isHidden()
    assert page.mode_acts["multi"].isEnabled() and not page.camera_act.isEnabled()  # (several: chambers)
    assert page.single_panel.view.message.startswith("Input/output only")
    page.start_mode.setCurrentIndex(page.start_mode.findData("on_detection"))  # (no camera: starts at once)
    page.animal.setCurrentText("R1")
    assert page.arm()
    s = page.session
    assert isinstance(s, IOSession) and page.grabber is None
    for _ in range(100):
        if s.state == "running":
            break
        wait(20)
    assert s.state == "running"
    s.devices.set_input("box", "lever", 1)
    wait(100)
    s.devices.set_input("box", "lever", 0)
    page.tabs.setCurrentWidget(page.monitor_tab)
    page._refresh_monitor(force=True)
    assert page.monitor.vals["zone"].text() == "no camera (I/O only)"
    for _ in range(200):
        if page.session is None:
            break
        wait(20)
    assert page.session is None  # finished at its duration and saved
    t = p.tests[-1]
    assert t.status == "scored" and t.animal_id == "R1" and t.duration_s == 1.0
    assert t.result_variables == {"presses": 1} and any(e["channel"] == "pellet" for e in t.io_events)
    results = dict(r for r in page.last_results[0].items())
    assert results["lever: activations"] == 1 and results["Test duration (s)"] == 1.0
    assert any("I/O only: the test starts as soon as it is armed" in page.log.item(i).text()
               for i in range(page.log.count()))


def test_stop_and_save_by_hand(win):
    page = win.goto("LivePage")
    page.duration.setValue(0.0)  # until stopped
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.animal.setCurrentText("R1")
    assert page.arm()
    s = page.session
    for _ in range(100):
        if s.state == "running" and s.elapsed > 0.2:
            break
        wait(20)
    page._stop_clicked()  # Save
    assert page.session is None
    t = win.project.tests[-1]
    assert t.status == "scored" and t.duration_s >= 0.2
