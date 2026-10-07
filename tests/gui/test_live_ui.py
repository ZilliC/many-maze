"""Run tests page in the ANY-maze layout: ribbon groups (All apparatus, Session, View, Mode), test panels in a grid,
explorer entries per panel, view preferences and Undo."""

import shutil

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Behaviour, Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.video import VideoSource
from manymaze.gui.live_widgets import PanelSettingsDialog
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.live import VIEW_DEFAULTS

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    w.show()
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def section(w, page):
    return w.sections[w._page_section[id(page)]]


def test_ribbon_groups_and_modes(win):
    page = win.goto("LivePage")
    sec = section(win, page)
    assert [g.title for g in sec.panel.context] == ["All apparatus", "Session", "View", "Mode"]
    texts = [t.replace("\n", " ") for t in sec.panel.actions_text()]
    for t in ("Stop all", "Pause all", "Resume all", "Add source", "Add test panel",
              "Remove panel", "Capture backgrounds", "Camera options", "Apparatus layout", "Scale to fit",
              "Tracking indicators", "Hide report", "Hide apparatus", "Panel settings", "One test",
              "Several tests", "Observation only"):
        assert t in texts, t
    assert [a.text() for a in page.start_all_act.menu().actions()] == ["Start all now", "Arm all"]
    assert [a.text() for a in page.layout_act.menu().actions()] == ["One apparatus", "2 × 1", "2 × 2", "3 × 2"]
    page.mode_acts["observe"].trigger()
    assert page.mode == "observe" and page.left_stack.currentWidget() is page.obs_panel
    assert page.next_test_act.isEnabled() and not page.add_source_act.isEnabled()
    page.mode_acts["single"].trigger()
    assert page.mode == "single" and page.mode_acts["single"].isChecked()
    assert sec.item_for(page).childCount() == 0  # one test: no panels listed in the explorer


def test_single_test_from_the_ribbon(win):
    p = win.project
    p.behaviours = [Behaviour("Defecation", "d", "point")]
    video = p.abs_path(p.tests[0].video)
    page = win.goto("LivePage")
    page.set_simulation_file(video)
    assert page.view.frame_size is not None  # the first image is shown before the preview starts
    page.duration.setValue(2.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    page.start_preview = lambda: True
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.view.apparatus is not None and page.view._zone_items  # orange apparatus over the image
    n = len(p.tests)
    page.start_all_act.trigger()
    assert page.session is not None and len(p.tests) == n + 1
    assert page.stop_all_act.isEnabled() and not page.next_test_act.isEnabled()
    src = VideoSource(video)
    for i in range(20):
        ok, f = src.read()
        page.feed_frame(f, i / 25)
    assert page.session.state == "running"
    assert "Animal C1" in page.single_panel.title.text() and " - 0:00" in page.single_panel.title.text()
    assert page.view._active  # the zone the animal is in is highlighted
    assert page.single_panel.slider.value() > 0
    page.pause_all_act.trigger()
    assert page.session.state == "paused" and page.resume_all_act.isEnabled()
    page.resume_all_act.trigger()
    assert page.session.state == "running"
    assert page.score_key("d") and page.single_panel.undo_btn.isEnabled()
    n_ev = len(page.session.events)
    assert page.undo_last_event(page.session) and len(page.session.events) == n_ev - 1
    assert not page.single_panel.undo_btn.isEnabled()
    assert page.single_panel.log.count() > 0  # the panel's Session log
    page.stop_all_act.trigger()  # Discard
    src.release()
    assert page.session is None and len(p.tests) == n


def test_several_tests_panels_and_explorer(win):
    p = win.project
    page = win.goto("LivePage")
    sec = section(win, page)
    video = p.abs_path(p.tests[0].video)
    page.set_mode("multi")
    page.add_source(video)
    e1 = page.add_panel_act.trigger() or page.group.entries[-1]
    e2 = page.add_session_row(animal="A9")
    assert page.mode == "multi" and len(page.mosaic.panels) == 2
    assert sec.item_for(page).childCount() == 2
    page.show_item(e2.id)
    assert page.sess_table.currentRow() == 1 and page.mosaic.current == e2.id
    assert page.row_animal.currentText() == "A9"
    page.row_animal.setCurrentText("B7")
    assert e2.meta["animal"] == "B7" and "Animal B7" in page.mosaic.panels[e2.id].title.text()
    assert sec.item_for(page, e2.id).text(0).endswith("B7")
    page.set_layout("1")
    assert page.mosaic.panels[e2.id].isVisibleTo(page.mosaic)
    assert not page.mosaic.panels[e1.id].isVisibleTo(page.mosaic)
    page.set_layout("2x2")
    assert page.mosaic.panels[e1.id].isVisibleTo(page.mosaic)
    # control a single panel from its toolbar, then everything from the ribbon
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    page.mosaic.panels[e1.id].arm_clicked.emit()
    assert e1.state == "waiting" and e2.state == "idle"
    page.mosaic.panels[e1.id].stop_clicked.emit()
    assert e1.state == "finished"
    page.remove_panel_act.trigger()
    assert len(page.mosaic.panels) == 1 and sec.item_for(page).childCount() == 1


def test_view_preferences(win, monkeypatch):
    page = win.goto("LivePage")
    try:
        page.hide_report_act.trigger()
        assert page.tabs.isHidden()
        page.hide_app_act.trigger()
        assert not page.view.show_apparatus
        page.trail_act.trigger()
        assert not page._show_trail and page.group.trail_len == 0
        page.labels_act.trigger()
        assert page.view.show_labels
        page.fit_act.trigger()
        assert not page.view.scale_to_fit
        monkeypatch.setattr(PanelSettingsDialog, "exec", lambda self: QDialog.Accepted)
        monkeypatch.setattr(PanelSettingsDialog, "result_settings", lambda self: {"toolbar": False, "tabs": False})
        assert page.panel_settings()
        assert page.single_panel.toolbar.isHidden() and page.single_panel.tabs.isHidden()
        assert win.settings.value("live/view")  # kept in the user's settings, not in the experiment
        assert not win.dirty
    finally:
        page.prefs = {k: (v.copy() if isinstance(v, dict) else v) for k, v in VIEW_DEFAULTS.items()}
        page._save_view_prefs()
        page._apply_view_prefs()
    assert not page.tabs.isHidden() and page.view.show_apparatus and page.view.scale_to_fit


def test_observation_panel(win):
    p = win.project
    p.behaviours = [Behaviour("Grooming", "g", "state"), Behaviour("Defecation", "d", "point")]
    page = win.goto("LivePage")
    page.set_mode("observe")
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.duration.setValue(0)
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("A1")
    page.start_all_act.trigger()
    o = page.obs
    assert o is not None and o.state == "running"
    assert page.obs_panel.title.text().startswith("Observation: Animal A1")
    assert page.score_key("g") and page.score_key("g") and page.score_key("d")
    assert [e["behaviour"] for e in o.events] == ["Grooming", "Defecation"]
    assert page.obs_panel.undo_btn.isEnabled()
    assert page.undo_last_event(o) and [e["behaviour"] for e in o.events] == ["Grooming"]
    assert page.undo_last_event(o) and "Grooming" in o.open_states  # the end of Grooming is taken back
    page.pause_all_act.trigger()
    assert o.state == "paused"
    page.stop_all_act.trigger()
    assert page.obs is None and p.tests[-1].status == "scored" and len(p.tests[-1].events) == 1


def _arm_single(win, duration=2.0):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = win.goto("LivePage")
    page.set_simulation_file(video)
    page.duration.setValue(duration)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    page.start_preview = lambda: True
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.arm()
    return page, video


def test_stale_finished_frame_does_not_finalise_a_new_test(win):
    import numpy as np

    page, _ = _arm_single(win)
    s = page.session
    stale = {"session": object(), "state": "finished", "elapsed": 1.0, "duration": 2.0, "events": 0, "fired": [],
             "outputs": [], "proc_log": [], "phase": "", "distance": 0.0, "unit": "cm", "detected": False,
             "zones": []}
    page._on_frame(np.zeros((400, 400, 3), np.uint8), stale)
    assert page.session is s
    page.stop_test(save=False)


def test_procedure_log_lines_are_shown_once(win):
    page, video = _arm_single(win, duration=0)
    page.session.outputs.log.append("serial: A")
    src = VideoSource(video)
    for i in range(3):
        ok, f = src.read()
        page.feed_frame(f, i / 25)
        page.session.log.append((i / 25, f"line {i}"))
    src.release()
    lines = [page.log.item(i).text() for i in range(page.log.count())]
    assert sum("serial: A" in x for x in lines) == 1
    assert [sum(f"line {i}" in x for x in lines) for i in range(2)] == [1, 1]
    page.stop_test(save=False)


def test_scoring_when_the_test_ends_meanwhile(win):
    p = win.project
    p.behaviours = [Behaviour("Grooming", "g", "state", group="g1"), Behaviour("Rearing", "r", "state", group="g1")]
    page = win.goto("LivePage")
    page.set_mode("observe")
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("A1")
    page.start_all_act.trigger()
    assert page.score_key("g")
    page.obs.score = lambda *a, **k: None  # the test ended between the state check and the scoring
    assert not page.score_key("r")
    page.obs_stop(save=False)


def test_daily_schedule_does_not_pile_up(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    page.add_session_row()
    page.start_mode.setCurrentIndex(page.start_mode.findData("scheduled"))
    page.sched_daily.setChecked(True)
    assert page.arm_all() == 1
    assert len(page.group.schedules) == 1
    sch, ents = page.group.schedules[0], list(page._entries())
    for e in ents:
        page.group.stop(e, save=False)
    page._on_group_schedule(sch, ents)  # the next day: the finished tests are armed again and started
    assert len(page.group.schedules) == 1
    assert all(e.session is not None and e.state != "finished" for e in ents)
    page.stop_all_clicked()  # Discard
    assert all(e.state == "finished" for e in ents)


def test_touch_window_follows_its_settings(win):
    p = win.project
    page = win.goto("LivePage")
    p.settings_extra["touchscreen"] = {"enabled": True, "areas": [{"name": "a", "x": 0, "y": 0, "w": 0.5, "h": 1}]}
    try:
        w1 = page._touch_window()
        assert page._touch_window() is w1
        p.settings_extra["touchscreen"]["areas"][0]["name"] = "b"  # edited on the Experiment page
        w2 = page._touch_window()
        assert w2 is not w1 and [a["name"] for a in w2.areas] == ["b"]
        p.settings_extra["touchscreen"]["enabled"] = False
        assert page._touch_window() is None and page._touch is None
    finally:
        p.settings_extra.pop("touchscreen", None)
        page._close_touch()
