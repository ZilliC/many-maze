"""Run tests: the test waits until the animal is weighed (a balance is connected), the start switch input and the
delay after the start switch (an input/output only protocol, so that tests are armed without a camera)."""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import scales
from manymaze.core.project import Project
from manymaze.gui import confirm_id
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: info.append(a[2]) or QMessageBox.Ok)
    p = Project(name="Weigh", test_duration_s=0.0)
    p.settings_extra["mode"] = "io_only"
    p.io_devices = [{"name": "box", "type": "virtual", "channels": [{"name": "start", "kind": "input"}]},
                    {"name": "balance", "type": "scale", "protocol": "mt_sics", "port": "/dev/fake"}]
    p.ensure_animal("R1")
    p.save(tmp_path / "w.mmaze")
    w = MainWindow()
    w.settings.setValue("current_user", "")
    w.set_project(p)
    w.info = info
    yield w
    page = w.page("LivePage")
    page.stop_test(save=False, quiet=True)
    page.shutdown()
    w.dirty = False


def test_protocol_option(win):
    ex = win.goto("ExperimentPage")
    assert not ex.weigh_first.isChecked()
    ex.weigh_first.setChecked(True)
    assert win.project.require_weight_before_test and win.dirty


def test_the_test_waits_for_the_weight(win, monkeypatch):
    p = win.project
    p.require_weight_before_test = True
    page = win.goto("LivePage")
    page.scale_reader = lambda cfg, timeout=3.0, stable=True: (23.5, True)
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    page.animal.setCurrentText("R1")
    monkeypatch.setattr(confirm_id, "exec_dialog", lambda dlg: None)  # closed without weighing
    n = len(p.tests)
    assert not page.arm() and page.session is None and len(p.tests) == n  # not armed, the new test removed
    assert win.info and "must be weighed first" in win.info[-1]

    def weigh(dlg):
        assert dlg.animal.id == "R1" and dlg.scale.currentData()["name"] == "balance"
        assert dlg.read_scale() and dlg.record()

    monkeypatch.setattr(confirm_id, "exec_dialog", weigh)
    assert page.arm() and page.session is not None
    a = p.get_animal("R1")
    assert scales.weighed_today(a) and a.fields[scales.WEIGHT_FIELD] == "23.5"
    page.stop_test(save=False, quiet=True)
    monkeypatch.setattr(confirm_id, "exec_dialog", lambda dlg: pytest.fail("weighed twice the same day"))
    assert page.arm()  # already weighed today: no dialog


def test_start_switch_and_delay(win):
    p = win.project
    page = win.goto("LivePage")
    page.start_mode.setCurrentIndex(page.start_mode.findData("input"))
    assert page.start_input.isEnabled()
    page.start_input.setText("box/start")
    page._save_live_settings()
    page.start_delay.setValue(2.5)
    assert p.start_switch_delay_s == 2.5 and p.settings_extra["live"]["start_input"] == "box/start"
    page.animal.setCurrentText("R1")
    assert page.arm()
    s = page.session
    assert s.start_mode == "input" and s.start_input == "box/start" and s.start_delay_s == 2.5
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    assert not page.start_input.isEnabled()
    # kept with the protocol
    page.stop_test(save=False, quiet=True)
    win.save()
    assert Project.load(p.path).start_switch_delay_s == 2.5
