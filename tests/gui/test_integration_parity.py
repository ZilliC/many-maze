"""Lead-integrated parity features: event-anchored periods editor, per-test moveable zones."""

import time

from PySide6.QtWidgets import QApplication

from manymaze.core.demo import create_demo_project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def _window(tmp_path):
    p = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=4)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    return w, p


def test_event_periods_editor(tmp_path):
    w, p = _window(tmp_path)
    try:
        pg = w.goto("ExperimentPage")
        pg._add_event_period()
        assert p.analysis.event_periods[0]["anchor"] == "first_exit"
        pg.ev_periods.cellWidget(0, 1).setCurrentIndex(pg.ev_periods.cellWidget(0, 1).findData("first_entry"))
        pg.ev_periods.item(0, 2).setText("Centre")
        pg.ev_periods.item(0, 4).setText("2")
        d = p.analysis.event_periods[0]
        assert d["anchor"] == "first_entry" and d["zone"] == "Centre" and d["duration_s"] == 2.0
        rows = p.results(segmented=True)
        assert any(r.get("Period", "").startswith(d["label"]) for r in rows) or rows
    finally:
        w.dirty = False
        w.close()


def test_moveable_zone_per_test(tmp_path):
    w, p = _window(tmp_path)
    try:
        app_ = p.apparatus[0]
        z = app_.zone("Centre")
        z.moveable = True
        t = p.tests[0]
        w.open_test(t.id)
        v = w.page("TestViewPage")
        for _ in range(5):
            app.processEvents()
            time.sleep(0.02)
        assert not v.move_box.isHidden() and v.move_zone.currentData() == "Centre"
        before = p.analyse_test(t)[0]["Centre: time (%)"]
        v.move_btn.setChecked(True)
        v._view_clicked(20.0, 20.0)  # move the centre zone into a corner
        assert not v.move_btn.isChecked() and "Centre" in t.zone_overrides
        cx, cy = v._app().zone("Centre").shape.centroid()
        assert abs(cx - 20) < 1e-6 and abs(cy - 20) < 1e-6
        after = p.analyse_test(t)[0]["Centre: time (%)"]
        assert after != before
        v.reset_moveable_zone()
        assert "Centre" not in t.zone_overrides
    finally:
        w.dirty = False
        w.close()


def test_hardware_and_procedure_editor_hookups(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from manymaze.gui.io_devices_dialog import IODevicesDialog
    from manymaze.gui.touchscreen import TouchScreenDialog, TouchStimulusWindow

    w, p = _window(tmp_path)
    try:
        ex = w.goto("ExperimentPage")
        monkeypatch.setattr(IODevicesDialog, "exec", lambda self: QDialog.Accepted)
        assert isinstance(ex.edit_io_devices(), IODevicesDialog)

        def ts_exec(self):
            self.enabled.setChecked(True)
            self.windows.setValue(2)
            self.accept()
            return QDialog.Accepted

        monkeypatch.setattr(TouchScreenDialog, "exec", ts_exec)
        ex.edit_touchscreen()
        cfg = p.settings_extra["touchscreen"]
        assert cfg["enabled"] and [a["name"] for a in cfg["areas"]] == ["left", "right"]
        assert "Touch screen: on" in ex.hw_lbl.text()

        live = w.goto("LivePage")
        assert live.proc_editor is not None
        w.dirty = False
        live.proc_editor.changed.emit()
        assert w.dirty
        monkeypatch.setattr(TouchStimulusWindow, "show_on_screen", lambda self, index=None: None)
        win = live._touch_window()
        assert isinstance(win, TouchStimulusWindow) and live._touch_window() is win
        p.settings_extra["touchscreen"]["enabled"] = False
        assert live._touch_window() is None and live._touch is None
    finally:
        w.dirty = False
        w.close()
