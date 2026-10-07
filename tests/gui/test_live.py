"""Smoke tests for the Live testing page, simulating a camera with a demo video."""

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.video import VideoSource
from manymaze.gui.main_window import MainWindow
from shots import shot_path

app = QApplication.instance() or QApplication([])


def wait(ms):
    """Run the event loop for ms (QTest.qWait keeps the GIL in PySide6 and starves worker threads)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=8)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    yield w
    page = w.page("LivePage")
    page.shutdown()
    w.dirty = False
    w.close()


def setup_page(w, video, duration=3.0):
    page = w.goto("LivePage")
    page.set_simulation_file(str(video))
    page.duration.setValue(duration)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)  # new test
    page.animal.setCurrentText("C1")
    page.stage.setCurrentText("Day 1")
    page.trial.setValue(2)
    # rules of older experiments: the procedure editor converts them to procedures
    w.project.procedures = [{"trigger": "time", "time_s": 1.0, "action": "mark", "payload": "Tone"},
                            {"trigger": "zone_enter", "zone": "Centre", "action": "serial", "payload": "LED ON"}]
    page.on_show()
    return page


def test_simulated_live_test_synchronous(win, monkeypatch):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = setup_page(win, video)
    assert len(p.procedures) == 2 and all("statements" in pr for pr in p.procedures)
    n_tests = len(p.tests)
    # drive frames ourselves: no grabber thread
    monkeypatch.setattr(page, "start_preview", lambda: True)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.arm()
    test = page.test
    assert len(p.tests) == n_tests + 1 and test.animal_id == "C1" and test.trial == 2
    page.on_hide()  # keeps running while a test is in progress
    assert page.session is not None
    win.goto("LivePage")
    win.show()
    src = VideoSource(video)
    i = 0
    while page.session is not None and i < 200:
        ok, f = src.read()
        assert ok
        page.feed_frame(f, i / 25.0)
        if i == 20:
            assert page.score_key("r")  # Rearing starts
        if i == 35:
            assert page.score_key("r")  # Rearing ends
            assert page.score_key("d")  # Defecation (point)
        if i == 45:
            app.processEvents()
            win.grab().save(shot_path("shot_live.png"))
        i += 1
    src.release()
    assert page.session is None
    assert 74 <= i <= 78
    assert test.status == "tracked" and test.recorded_at
    assert test.video.startswith("recordings") and test.start_s == 0.0
    rec = Path(p.abs_path(test.video))
    assert rec.exists() and rec.name == f"test_{test.id:04d}_C1.mp4"
    with VideoSource(str(rec)) as v:
        assert v.frame_count >= 70
    tracks = p.load_tracks(test)
    assert len(tracks) == 1 and len(tracks[0]) >= 70 and tracks[0].detected.mean() > 0.9
    names = [(e["behaviour"], e["t"], e["t_end"]) for e in test.events]
    assert any(n == "Tone" and abs(t - 1.0) < 0.05 for n, t, _ in names)
    assert any(n == "Rearing" and te is not None and te > t for n, t, te in names)
    assert any(n == "Defecation" for n, _, _ in names)
    assert not win.dirty  # saved
    saved = Project.load(p.path).get_test(test.id)
    assert saved.status == "tracked" and saved.video == test.video
    assert page.results.rowCount() > 5 and page.last_results
    assert any("distance" in page.results.item(r, 0).text().lower() for r in range(page.results.rowCount()))
    assert page.vals["distance"].text() != "—"
    # next test picks the next pending test
    t2 = p.add_test("", "C2", p.apparatus[0].name, stage="Day 1", trial=2)
    page.on_show()
    page.next_test()
    assert page.test_combo.currentData() == t2.id and page.animal.currentText() == "C2"


def test_simulated_live_test_threaded(win):
    p = win.project
    video = p.abs_path(p.tests[1].video)
    page = setup_page(win, video, duration=2.0)
    # real time: at higher speeds the 8 s video can run out before the 2 s test ends on slow machines
    page.sim_speed.setCurrentIndex(page.sim_speed.findData(1.0))
    page.start_mode.setCurrentIndex(page.start_mode.findData("on_detection"))
    assert page.start_preview()
    for _ in range(100):
        wait(50)
        if page._file_background is not None and page._last_frame is not None:
            break
    assert page._last_frame is not None
    assert page.arm()
    test = page.test
    for _ in range(300):
        wait(50)
        if page.session is None:
            break
    assert page.session is None
    assert test.status == "tracked"
    tr = p.load_tracks(test)[0]
    assert len(tr) >= 25 and tr.t[-1] >= 1.7  # the 2 s test ran to the end (frames may drop on slow machines)
    assert any(e["behaviour"] == "Tone" for e in test.events)
    assert page.grabber is not None  # preview keeps running after the test
    page.on_hide()
    assert page.grabber is None


def test_running_test_is_saved_or_kept_when_the_experiment_changes(win, monkeypatch):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = setup_page(win, video, duration=0)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page.start_preview = lambda: True
    assert page.arm()
    test = page.test
    src = VideoSource(video)
    for i in range(10):
        ok, f = src.read()
        page.feed_frame(f, i / 25)
    src.release()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    win.close_project()
    assert win.project is p and page.session is not None  # cancelled: the test keeps running
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Save)
    win.close_project()
    assert win.project is None and page.session is None
    saved = Project.load(p.path).get_test(test.id)
    assert saved is not None and saved.status == "tracked"


def test_abort_discards_new_test(win):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = setup_page(win, video)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page.start_preview = lambda: True
    n = len(p.tests)
    assert page.arm()
    src = VideoSource(video)
    for i in range(10):
        ok, f = src.read()
        page.feed_frame(f, i / 25)
    src.release()
    rec = page._record_path
    side = page.session.autosave_path
    page.session.flush_autosave()
    assert side and Path(side).exists()  # crash-recovery side file while the test runs
    page.stop_test(save=False)
    assert page.session is None and len(p.tests) == n
    assert not Path(rec).exists() and not Path(side).exists()


def test_touch_window_goes_through_the_session_lock(win):
    from manymaze.gui.touchscreen import TouchStimulusWindow

    calls = []

    class FakeSession:
        def touch(self, area, x, y):
            calls.append((area, round(x, 2), round(y, 2)))

    w = TouchStimulusWindow()
    w.resize(800, 600)
    w.connect_session(FakeSession())
    a = w.areas[0]
    w._touch((a["x"] + a["w"] / 2) * 800, (a["y"] + a["h"] / 2) * 600)
    w._touch(2, 2)
    assert calls == [(a["name"], round(a["x"] + a["w"] / 2, 2), 0.5), (None, 0.0, 0.0)]
    w.close()
    # the live page connects the touch window to the session, not straight to its engine
    p = win.project
    p.settings_extra["touchscreen"] = {"enabled": True, "areas": w.areas}
    video = p.abs_path(p.tests[0].video)
    page = setup_page(win, video)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page.start_preview = lambda: True
    assert page.arm()
    assert page._touch is not None and page._touch._session is page.session and page._touch._engine is None
    page.stop_test(save=False)
    p.settings_extra.pop("touchscreen", None)


def test_several_tests_need_one_io_device_each(win, monkeypatch):
    from manymaze.core.livegroup import LiveEntry

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]) or QMessageBox.Ok)
    p = win.project
    p.io_devices = [{"name": n, "type": "virtual", "channels": [{"name": "lever", "kind": "input"}]}
                    for n in ("box1", "box2")]
    page = win.goto("LivePage")

    class Running:
        state = "running"

    other = LiveEntry(99, "src", Running(), "other", meta={"io_plan": "box1"})
    me = LiveEntry(100, "src", None, "me", meta={"device": ""})
    page.group.entries += (other,)
    try:
        plan, msg = page._io_plan(me)
        assert plan is None and "Device" in msg  # automatic is refused while another test runs
        me.meta["device"] = "box1"
        assert page._io_plan(me)[0] is None  # box1 is taken
        me.meta["device"] = "box2"
        assert page._io_plan(me) == ("box2", "")
        view = page._session_devices("box2")
        assert view.alias == "box2" and view.find_channel("lever") == "box2"
        view.release()
        # the panel editor offers the configured boxes
        page._load_row_editor()
        assert [page.row_device.itemData(i) for i in range(page.row_device.count())] == ["", "box1", "box2", "-"]
    finally:
        page.group.entries = tuple(x for x in page.group.entries if x is not other)
        page._close_devices()


def test_interrupted_live_test_is_recovered_when_the_experiment_opens(win, tmp_path):
    import numpy as np

    from manymaze.core import synthetic as syn
    from manymaze.core import templates
    from manymaze.core.live import LiveSession
    from manymaze.core.autosave import path_for as autosave_path_for

    p = win.project
    test = p.add_test("", "C9", p.apparatus[0].name)
    p.save()
    app_ = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(app_, DetectionSettings(background="frame"), duration_s=0,
                    autosave_path=autosave_path_for(p, test), autosave_meta={"test_id": test.id})
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    for i in range(20):
        img = np.full((200, 200), 200, np.uint8)
        syn.draw_mouse(img, 60 + i, 100, 0)
        s.process(np.dstack([img] * 3), i / 25)
    s.flush_autosave()  # ... and the program crashes here
    win.set_project(Project.load(p.path))
    t = win.project.get_test(test.id)
    assert t.status == "tracked" and "interrupted" in t.notes
    assert not Path(autosave_path_for(win.project, t)).exists()
    assert Project.load(p.path).get_test(test.id).status == "tracked"  # saved at once


def test_camera_scan_and_missing_camera(win, monkeypatch):
    import manymaze.gui.pages.live.single as live_mod

    page = win.goto("LivePage")
    monkeypatch.setattr(live_mod, "list_cameras", lambda: [0, 2])
    page.scan_cameras()
    for _ in range(100):
        wait(20)
        if page._scan_worker is None:
            break
    assert [page.camera.itemData(i) for i in range(page.camera.count())] == [0, 2]
    # no camera on this machine: opening fails gracefully
    page.cam_radio.setChecked(True)
    page.camera.setCurrentIndex(1)
    monkeypatch.setattr(live_mod, "VideoSource", lambda *a, **k: (_ for _ in ()).throw(IOError("no camera")))
    assert page.start_preview()
    for _ in range(100):
        wait(20)
        if page.grabber is None:
            break
    assert page.grabber is None
    assert any("no camera" in page.log.item(i).text() for i in range(page.log.count()))
