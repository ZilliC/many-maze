"""The synchronisation element in the GUI: Protocol ▸ Hardware ▸ Synchronisation, and Run tests sending its pulses
(an input/output only test with a simulated box)."""

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    p = Project(name="Sync", test_duration_s=0.5)
    p.settings_extra["mode"] = "io_only"
    p.io_devices = [{"name": "box", "type": "virtual", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "ttl", "kind": "output"}, {"name": "light", "kind": "pwm"}]}]
    p.procedures = [{"name": "Light", "enabled": True, "statements": [  # (an I/O log, so the test is saved)
        {"type": "when", "event": "test_start", "body": [
            {"type": "do", "action": "output_on", "device": "box", "channel": "light"}]}]}]
    p.ensure_animal("R1")
    p.save(tmp_path / "s.mmaze")
    w = MainWindow()
    w.settings.setValue("current_user", "")
    w.set_project(p)
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False


def test_hardware_page_sets_the_element(win):
    p = win.project
    ex = win.goto("ExperimentPage")
    ex.show_item("hardware")
    outs = [ex.sync_out.itemText(i) for i in range(ex.sync_out.count())]
    assert outs == ["box/ttl"]  # digital outputs only
    assert not ex.sync_on.isChecked() and not ex.sync_width.isEnabled()
    ex.sync_on.setChecked(True)
    ex.sync_checks["per_frame"].setChecked(True)
    ex.sync_width.setValue(2.5)
    assert p.sync == {"enabled": True, "device": "box", "channel": "ttl", "test_start": True, "test_end": True,
                      "per_frame": True, "per_position": False, "width_ms": 2.5}
    assert win.dirty
    # a channel that no longer exists is shown as such
    p.sync = dict(p.sync, channel="gone")
    ex.on_show()
    assert ex.sync_out.currentText() == "box/gone (not configured)"


def test_live_test_sends_the_pulses(win):
    p = win.project
    p.sync = {"enabled": True, "device": "box", "channel": "ttl", "test_start": True, "test_end": True}
    page = win.goto("LivePage")
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.animal.setCurrentText("R1")
    assert page.arm()
    s = page.session
    assert s.sync_output is not None and s.sync_output.ok
    dev = s.devices.devices["box"]
    for _ in range(200):
        if page.session is None:
            break
        wait(20)
    assert page.session is None and dev.sync_pulses["ttl"] == 2  # start and end
    t = p.tests[-1]
    assert "Synchronisation pulses on box/ttl (1 ms): test start 1, test end 1 (2 pulses sent)" in t.notes
