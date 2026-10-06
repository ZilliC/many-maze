"""Live page parity: several simultaneous tests, collective control, pause, start keys, schedules, camera options,
monitoring and observation-only sessions."""

import datetime as dt
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Behaviour, Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.video import VideoSource
from manymaze.gui.live_widgets import CameraOptionsDialog
from manymaze.gui.main_window import MainWindow
from shots import shot_path

app = QApplication.instance() or QApplication([])
SHOT = shot_path("shot_live_parity.png")


def pump(cond, timeout=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.03)
    return cond()


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=1, seconds=6)
    n = 150
    a = syn.random_walk(n, (10, 10, 190, 190), speed=3, seed=1)
    b = syn.random_walk(n, (210, 10, 390, 190), speed=3, seed=2)
    syn.make_video(d / "videos" / "two.avi", a, size=(400, 200), extra_animals=[b], noise=1.0)
    for name, x in (("Left box", 10), ("Right box", 210)):
        ap = templates.build("open_field", x, 10, 180, 180, size_cm=40)
        ap.name = name
        ap.frame_size = (400, 200)
        p.apparatus.append(ap)
    p.save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    for k in ("question", "information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, k, lambda *a, **kw: QMessageBox.Save if a and "Stop" in str(a[1:2])
                            else QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def test_several_tests_at_once(win):
    p = win.project
    page = win.goto("LivePage")
    win.show()
    assert page.set_mode("multi") and page.left_stack.currentIndex() == 1
    assert not page.src_box.isVisible() and page.det_box.isVisible()
    two = str(p.path / "videos" / "two.avi")
    single = p.abs_path(p.tests[0].video)
    k1 = page.add_source(two)
    k2 = page.add_source(single)
    e1 = page.add_session_row(k1, "Left box", "L1")
    e2 = page.add_session_row(k1, "Right box", "R1")
    e3 = page.add_session_row(k2, p.apparatus[0].name, "C1", trial=3)
    assert page.sess_table.rowCount() == 3 and len(page.mosaic.views) == 2
    # the layout of the session table is kept in the project
    saved = p.settings_extra["live"]["multi"]
    assert len(saved["sources"]) == 2 and [s["apparatus"] for s in saved["sessions"]][:2] == ["Left box",
                                                                                             "Right box"]
    page.duration.setValue(3.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.sim_speed.setCurrentIndex(page.sim_speed.findData(2.0))
    page.record_overlay.setChecked(True)
    assert page.start_cameras()
    assert pump(lambda: all(r.background is not None for r in page.group.runners.values()))
    n_tests = len(p.tests)
    assert page.arm_all() == 3
    assert len(p.tests) == n_tests + 3
    assert pump(lambda: all(e.state == "running" and e.elapsed > 1.0 for e in (e1, e2, e3)))
    page.group_pause_all()
    assert all(e.state == "paused" for e in (e1, e2, e3))
    el = [e.elapsed for e in (e1, e2, e3)]
    page.tabs.setCurrentWidget(page.monitor_tab)
    page.sess_table.selectRow(0)
    pump(lambda: False, 0.5)
    assert [e.elapsed for e in (e1, e2, e3)] == el  # the test clocks stop while paused
    assert page.monitor.zones.rowCount() > 3 and "Left box" in page.monitor.title.text()
    assert page.sess_table.item(0, 5).text() == "paused"
    win.grab().save(SHOT)
    page.group_resume_all()
    assert pump(lambda: all(e.saved for e in (e1, e2, e3)), 30)
    for e, app_name, animal in ((e1, "Left box", "L1"), (e2, "Right box", "R1"), (e3, p.apparatus[0].name, "C1")):
        t = next(t for t in p.tests if t.animal_id == animal and t.recorded_at)
        assert t.status == "tracked" and t.apparatus == app_name
        tr = p.load_tracks(t)[0]
        assert tr.duration == pytest.approx(3.0, abs=0.15) and tr.detected.mean() > 0.9
        assert np.max(np.diff(tr.t)) < 0.05 and len(t.pauses) == 1
        assert Path(p.abs_path(t.video)).exists()
    t3 = next(t for t in p.tests if t.animal_id == "C1" and t.recorded_at and t.trial == 3)
    assert t3 is not None and e3.meta["trial"] == 4
    lefts = p.load_tracks(next(t for t in p.tests if t.animal_id == "L1"))[0]
    assert np.nanmax(lefts.x) < 200
    assert not win.dirty


def test_single_test_pause_keys_and_schedule(win, monkeypatch):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = win.goto("LivePage")
    page.set_simulation_file(video)
    page.duration.setValue(2.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    monkeypatch.setattr(page, "start_preview", lambda: True)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.arm()
    assert page.arm_btn.text() == "Start now"
    src = VideoSource(video)
    i = 0
    paused_at = None
    while page.session is not None and i < 200:
        ok, f = src.read()
        page.feed_frame(f, i / 25)
        if i == 4:
            assert page.session.state == "waiting"
            assert page.start_key()  # keyboard / remote start
        if i == 20:
            assert page.toggle_pause() and page.session.state == "paused"
            assert page.pause_btn.text() == "Resume"
            paused_at = page.session.elapsed
        if i == 30:
            assert page.session.elapsed == paused_at
            assert page.start_key()  # the start key also resumes
        i += 1
    src.release()
    test = p.get_test(page.last_test_id)
    assert test.status == "tracked" and len(test.pauses) == 1 and "Paused" in test.notes
    assert i == pytest.approx(5 + 50 + 10, abs=2)
    # scheduled start at a clock time
    page.next_test()
    page.start_mode.setCurrentIndex(page.start_mode.findData("scheduled"))
    assert page.sched_time.isEnabled()
    assert page.arm()
    sch = page._schedule
    assert sch is not None and sch.next_fire > dt.datetime.now() - dt.timedelta(minutes=1)
    page.feed_frame(np.full((400, 400, 3), 200, np.uint8), 0.0)
    assert page.session.state == "waiting"
    sch.next_fire = dt.datetime.now() - dt.timedelta(seconds=1)
    page._tick()
    page.feed_frame(np.full((400, 400, 3), 200, np.uint8), 0.04)
    assert page.session.state == "running"
    page.stop_test(save=False)
    assert page.session is None


def test_observation_mode_hold_and_exclusive(win, monkeypatch):
    p = win.project
    p.behaviours = [Behaviour("Grooming", "g", "state", group="act"), Behaviour("Sniffing", "s", "hold", group="act"),
                    Behaviour("Defecation", "d", "point")]
    page = win.goto("LivePage")
    win.show()
    assert page.set_mode("observe")
    assert not page.src_box.isVisible() and not page.det_box.isVisible() and page.test_box.isVisible()
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.duration.setValue(0)
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("A1")
    n = len(p.tests)
    assert page.obs_start() and page.obs.state == "running"
    assert len(page.obs_panel.pad.buttons) == 3
    assert not page.set_mode("single")  # not while observing
    o = page.obs
    clock = [0.0]
    o.clock = lambda: clock[0]
    o._t0 = 0.0
    clock[0] = 1.0
    assert page.score_key("g")
    clock[0] = 2.0
    target = page.obs_panel.start_btn
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s"))
    assert "Sniffing" in o.open_states and "Grooming" not in o.open_states  # exclusive set
    clock[0] = 2.5
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier, "s", True))  # repeat
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyRelease, Qt.Key_S, Qt.NoModifier, "s", True))
    assert "Sniffing" in o.open_states
    clock[0] = 3.0
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyRelease, Qt.Key_S, Qt.NoModifier, "s"))
    assert "Sniffing" not in o.open_states
    page._pad("Defecation", True)
    assert page.obs_pause() and o.state == "paused"
    clock[0] = 10.0
    assert page.obs_pause() and o.state == "running"
    clock[0] = 11.0
    page.obs_stop(save=True)
    assert page.obs is None and len(p.tests) == n + 1
    t = p.tests[-1]
    assert t.status == "scored" and t.pauses == [[3.0, 3.0]]
    assert [(e["behaviour"], e["t"], e["t_end"]) for e in t.events] == [
        ("Grooming", 1.0, 2.0), ("Sniffing", 2.0, 3.0), ("Defecation", 3.0, None)]
    assert page.last_results and page.set_mode("single")


def test_confirm_id_blocks_start(win, monkeypatch):
    p = win.project
    p.settings_extra["confirm_id"] = True
    page = win.goto("LivePage")
    page.set_mode("observe")
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("A1")
    n = len(p.tests)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("C1", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Cancel)
    assert not page.obs_start() and len(p.tests) == n
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("A1", True))
    assert page.obs_start()
    page.obs_stop(save=False)
    assert len(p.tests) == n


def test_camera_options(win, tmp_path):
    p = win.project
    page = win.goto("LivePage")
    video = p.abs_path(p.tests[0].video)
    page.set_simulation_file(video)
    frame = VideoSource(video).frame_at(0)
    dlg = CameraOptionsDialog(frame, page._view, None, "side", page._merge_choices(video))
    dlg.select_region(40, 30, 360, 380)
    dlg.rotate.setCurrentIndex(dlg.rotate.findData(90))
    res = dlg.result()
    assert res["view"]["rotate"] == 90 and res["second"] is None
    dlg.select_region(10, 20, 210, 220)
    res = dlg.result()
    assert res["view"]["crop"] == [10, 20, 200, 200]
    assert dlg.out_view.frame_size == (200, 200)
    dlg.close()
    page._apply_single_view(res)
    key = f"file:{video}"
    assert p.settings_extra["cameras"][key]["view"]["crop"] == [10, 20, 200, 200]
    assert "region 200×200" in page.view_lbl.text() and "rotated 90°" in page.view_lbl.text()
    # the preview applies the options
    assert page.start_preview()
    assert pump(lambda: page._frame_size is not None and page._last_frame is not None)
    assert page._frame_size == (200, 200) and page._last_frame.shape[:2] == (200, 200)
    page.stop_preview()
    # options are restored with the source; merging two "cameras" into one image
    page._apply_single_view({"view": {}, "second": video, "layout": "side"})
    page.set_simulation_file(video)
    assert page._second == video and "merged with" in page.view_lbl.text()
    assert page.start_preview()
    assert pump(lambda: page._frame_size is not None and page._frame_size[0] == 800)
    page.stop_preview()
    # multi-test source options
    page.set_mode("multi")
    k = page.add_source(video)
    assert page.group.sources[k].second == video  # saved options of this camera
    page.apply_source_options(k, {"view": {"flip": "h"}, "second": None, "layout": "side"})
    assert page.group.sources[k].view.flip == "h" and "second" not in p.settings_extra["cameras"][key]
