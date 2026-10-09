"""Audit 2026-10-08, "GUI live, apparatus designer, review/score and results": scoring keys only on the Run tests
page and never from text editors, keys for the procedures, the scoring target of several tests, apparatus renames
reaching the test panels, zone-rename undo, camera options regions, arming before the source is open, the px/cm
box, Review undo / timing / scoring limits, the touch screen, live geometry, saving selected cells, scheduled
re-arming without dialogs, live charts, dialogs deleted after use and deferred saves."""

import shutil
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox, QPlainTextEdit

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.demo import create_demo_project
from manymaze.core.geometry import shape_from_dict
from manymaze.core.live import LiveSession
from manymaze.core.livegroup import ClockSchedule
from manymaze.core.livemonitor import LiveCharts
from manymaze.core.project import Behaviour, Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.video import VideoSource
from manymaze.gui.live_widgets import CameraOptionsDialog
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.live.calibration import LiveGeometryDialog

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=1, seconds=6)
    walk = syn.random_walk(60, (40, 40, 360, 360), speed=3, seed=3)
    syn.make_video(d / "videos" / "slow.avi", walk, size=(400, 400), fps=10.0)  # not the default 25 fps
    p.save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
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


def pump(cond, timeout=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def wait_armed(page, timeout=20.0):
    return pump(lambda: (page._tick(), not page._pending_entries())[1], timeout)


def key(target, k, text, press=True, repeat=False):
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress if press else QEvent.KeyRelease, k, Qt.NoModifier,
                                             text, repeat))


def arm_running(win):
    """One test armed with the preview stubbed and started (frames fed by hand)."""
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = win.goto("LivePage")
    page.set_simulation_file(video)
    page.duration.setValue(0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    page.start_preview = lambda: True
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.arm()
    src = VideoSource(video)
    for i in range(5):
        ok, f = src.read()
        page.feed_frame(f, i / 25)
    src.release()
    assert page.session.state == "running"
    return page


# ------------------------------------------------------------------ keys
def test_scoring_keys_only_on_the_live_page_and_never_from_text_editors(win):
    win.project.behaviours = [Behaviour("Defecation", "d", "point")]
    page = arm_running(win)
    s = page.session
    assert page._key_filter
    editor = QPlainTextEdit(page)
    editor.show()
    editor.setFocus()
    key(editor, Qt.Key_D, "d")
    assert len(s.events) == 0 and editor.toPlainText() == "d"  # typed, not scored
    key(page.single_panel, Qt.Key_D, "d")
    key(page.single_panel, Qt.Key_D, "d", press=False)
    assert len(s.events) == 1
    win.goto("TestViewPage")  # the test keeps running; its keys stay on the Run tests page
    assert not page._key_filter
    key(win.page("TestViewPage"), Qt.Key_D, "d")
    assert len(s.events) == 1
    win.goto("LivePage")
    assert page._key_filter
    page.stop_test(save=False)
    assert not page._key_filter


def test_every_key_press_and_release_goes_to_the_procedures(win):
    win.project.behaviours = [Behaviour("Defecation", "d", "point")]
    page = arm_running(win)
    s = page.session
    got = []
    s.key = lambda k, down=True: got.append((k, down))
    w = page.single_panel
    key(w, Qt.Key_X, "x")
    key(w, Qt.Key_X, "x", repeat=True)  # auto-repeat: not again
    key(w, Qt.Key_X, "x", press=False)
    key(w, Qt.Key_D, "d")
    key(w, Qt.Key_D, "d", press=False)
    assert got == [("x", True), ("x", False), ("d", True), ("d", False)]
    assert len(s.events) == 1
    page.toggle_pause()
    assert s.state == "paused"
    key(w, Qt.Key_Y, "y")  # also while paused
    key(w, Qt.Key_Y, "y", press=False)
    assert got[-2:] == [("y", True), ("y", False)]
    page.stop_test(save=False)


class FakeSession:
    def __init__(self, state):
        self.state, self.open_states, self.events, self.warnings = state, {}, [], []
        self.lock = threading.RLock()
        self.keys, self.name, self.devices, self.stats, self.waiting_end = [], "fake", None, None, False
        self.elapsed, self.duration_s, self.apparatus = 1.0, 0.0, None

    def score(self, name, kind="point"):
        if self.state != "running":
            return None
        if kind == "state":
            ev = self.open_states.pop(name, None)
            if ev is not None:
                ev["t_end"] = 2.0
                return ev
            ev = {"behaviour": name, "t": 1.0, "t_end": None}
            self.open_states[name] = ev
        else:
            ev = {"behaviour": name, "t": 1.0, "t_end": None}
        self.events.append(ev)
        return ev

    def key(self, k, down=True):
        self.keys.append((k, down))

    def finish(self, *a):
        self.state = "finished"


def test_several_tests_score_the_selected_test_and_holds_end_where_they_started(win):
    p = win.project
    p.behaviours = [Behaviour("Grooming", "g", "state"), Behaviour("Sniffing", "s", "hold")]
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    e1, e2 = page.add_session_row(), page.add_session_row(animal="B9")
    s1, s2 = FakeSession("running"), FakeSession("paused")
    page.group.arm(e1, s1)
    page.group.arm(e2, s2)
    try:
        page.sess_table.selectRow(1)  # the paused test is selected: nothing goes to the running one
        assert page._scoring_target() is s2
        assert not page.score_key("g") and not s1.events and not s2.events
        assert ("g", True) in s1.keys and ("g", True) in s2.keys  # both tests' procedures see the key
        page.sess_table.selectRow(0)
        assert page.score_key("s") and "Sniffing" in s1.open_states
        page.sess_table.selectRow(1)
        s2.state = "running"
        assert page.score_key("s", down=False)  # released while the other test is selected
        assert "Sniffing" not in s1.open_states and s1.events[0]["t_end"] == 2.0 and not s2.events
        page.sess_table.clearSelection()
        page.sess_table.setCurrentCell(-1, -1)
        s2.state = "paused"
        assert page._scoring_target() is s1  # nothing selected: the first running test
    finally:
        for e in (e1, e2):
            page.group.remove(e)


# ------------------------------------------------------------------ apparatus
def test_apparatus_rename_and_delete_reach_the_test_panels(win):
    p = win.project
    extra = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    extra.name = "Spare box"
    p.apparatus.append(extra)
    first = p.apparatus[0]
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    e = page.add_session_row(apparatus=first.name)
    ap = win.goto("ApparatusPage")
    assert ap._rename_apparatus(first, "Renamed box")
    layout = p.settings_extra["live"]["multi"]["sessions"]
    assert e.meta["apparatus"] == "Renamed box" and layout[0]["apparatus"] == "Renamed box"
    assert "Renamed box" in e.label
    ap.show_item("Renamed box")
    assert ap.app is first and ap.delete_apparatus(confirm=False)
    assert e.meta["apparatus"] == "" and layout[0]["apparatus"] == "" and e.apparatus is None
    win.goto("LivePage")
    assert not page.arm_row(e) and e.session is None and not e.meta.get("arm_pending")  # never another box
    e.meta["apparatus"] = "No such box"
    assert not page.arm_row(e) and e.session is None
    assert p.find_apparatus("No such box") is None and p.get_apparatus("No such box") is not None


def test_undoing_a_zone_rename_restores_the_moveable_zone_positions(win):
    p = win.project
    ap = win.goto("ApparatusPage")
    a = ap.app
    old = a.zones[0].name
    users = p.tests_using(a.name)
    assert users
    shape = a.zones[0].shape.translated(5, 5).to_dict()
    for t in users:
        t.zone_overrides = {old: dict(shape)}
    assert ap.rename("zone", 0, "Platform X")
    assert all("Platform X" in t.zone_overrides and old not in t.zone_overrides for t in users)
    assert ap.undo()
    assert a.zones[0].name == old and all(t.zone_overrides == {old: shape} for t in users)
    assert ap.redo()
    assert a.zones[0].name == "Platform X" and all("Platform X" in t.zone_overrides for t in users)


def test_px_per_cm_box_keeps_the_ruler_without_an_edit(win):
    ap = win.goto("ApparatusPage")
    a = ap.app
    assert ap.calibrate_from_line(0, 0, 100, 0, length_cm=7.0)
    line, n = a.calibration_line, len(ap._undo[id(a)])
    ap.ppc_spin.editingFinished.emit()  # focus in and out: 14.286 shown for 14.2857…
    assert a.calibration_line == line and len(ap._undo[id(a)]) == n
    ap.ppc_spin.setValue(20.0)
    ap.ppc_spin.editingFinished.emit()
    assert a.px_per_cm == 20.0 and a.calibration_line is None


def test_map_image_fits_the_apparatus_ribbon_group(win):
    ap = win.goto("ApparatusPage")
    assert ap.map_img_act.text() == "Map image…"


# ------------------------------------------------------------------ camera options
def test_camera_options_regions_follow_rotation_and_never_come_from_a_placeholder():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    from manymaze.core.camera import CameraView

    dlg = CameraOptionsDialog(frame, CameraView())
    dlg.rotate.setCurrentIndex(dlg.rotate.findData(90))
    assert (dlg.cw.value(), dlg.ch.value()) == (1080, 1920) and dlg.result_view().crop is None
    dlg.select_region(100, 100, 500, 700)
    assert dlg.result_view().crop == [100, 100, 400, 600]
    dlg.flip.setCurrentIndex(dlg.flip.findData("h"))  # the region of the previous image is dropped
    assert dlg.result_view().crop is None
    dlg.deleteLater()
    # no camera image: a grey placeholder, on which no region is chosen or stored
    ph = CameraOptionsDialog(None, CameraView())
    assert not ph.cw.isEnabled()
    ph.select_region(10, 10, 300, 300)
    ph.rotate.setCurrentIndex(ph.rotate.findData(90))
    assert ph.result_view().crop is None and ph.result_view().rotate == 90
    ph.deleteLater()
    kept = CameraOptionsDialog(None, CameraView(crop=[10, 20, 800, 600]))
    assert kept.result_view().crop == [10, 20, 800, 600]  # the saved region is kept while unchanged
    kept.rotate.setCurrentIndex(kept.rotate.findData(180))
    assert kept.result_view().crop is None
    kept.reject()  # the region filter is removed: no error at teardown
    kept.deleteLater()


def test_dialogs_are_deleted_after_use(win, monkeypatch):
    p = win.project
    page = win.goto("LivePage")
    page.set_simulation_file(p.abs_path(p.tests[0].video))
    monkeypatch.setattr(CameraOptionsDialog, "exec", lambda self: QDialog.Rejected)
    assert not page.camera_options()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not page.findChildren(CameraOptionsDialog)


# ------------------------------------------------------------------ arming before the source is open
def test_single_test_armed_without_preview_uses_the_real_fps_and_file_background(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_simulation_file(str(p.path / "videos" / "slow.avi"))
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    page.record.setChecked(False)
    assert page.grabber is None
    assert page.arm()
    assert page.session is None and page._pending_arm is not None and page.any_active()
    assert pump(lambda: page.session is not None)
    s = page.session
    assert s.fps == pytest.approx(10.0) and s.settings.background == "frame"  # the video's median background
    page.stop_test(save=False)
    assert page.session is None


def test_cancelling_a_test_armed_while_its_source_opens(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_simulation_file(str(p.path / "videos" / "slow.avi"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    n = len(p.tests)
    page.start_preview()
    assert page.arm() and page._pending_arm is not None
    page.stop_test(save=False)  # stopped before the source was open
    assert page._pending_arm is None and len(p.tests) == n
    page.stop_preview()


def test_several_tests_armed_without_cameras_use_the_real_fps(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.record.setChecked(False)
    page.add_source(str(p.path / "videos" / "slow.avi"))
    e = page.add_session_row()
    assert not page.group.runners
    assert page.arm_all() == 1
    assert e.session is None and e.meta.get("arm_pending")
    assert wait_armed(page)
    assert e.session.fps == pytest.approx(10.0) and e.session.settings.background == "frame"
    page.stop_all_clicked()
    assert e.state == "finished"


def test_scheduled_rearm_opens_no_dialog(win, monkeypatch):
    p = win.project
    p.settings_extra["confirm_id"] = True
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: pytest.fail("modal dialog at a scheduled start"))
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    e = page.add_session_row()
    page._on_group_schedule(ClockSchedule("05:00", True, [e.id]), [e])
    assert e.meta.get("arm_pending") or e.session is not None
    assert any("not checked" in page.log.item(i).text() for i in range(page.log.count()))
    assert any(isinstance(w, QMessageBox) and not w.isModal() and w.isVisible()
               for w in QApplication.topLevelWidgets())
    assert wait_armed(page)
    assert e.state in ("waiting", "running")  # started by the schedule once armed
    page.stop_all_clicked()


# ------------------------------------------------------------------ touch screen, geometry, saving
def test_touch_window_closed_with_escape_is_shown_again(win):
    p = win.project
    page = win.goto("LivePage")
    p.settings_extra["touchscreen"] = {"enabled": True, "areas": [{"name": "a", "x": 0, "y": 0, "w": 0.5, "h": 1}]}
    try:
        w = page._touch_window()
        assert w.isVisible()
        key(w, Qt.Key_Escape, "")
        assert not w.isVisible()
        assert page._touch_window() is w and w.isVisible()  # the next test shows it again
    finally:
        p.settings_extra.pop("touchscreen", None)
        page._close_touch()


def test_live_geometry_moves_a_zone_from_where_the_moved_map_puts_it():
    a = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(a, DetectionSettings(background="frame"), duration_s=0)
    dlg = LiveGeometryDialog(s)
    name = dlg.zone.itemData(1)
    x0 = a.zone(name).shape.centroid()[0]
    dlg.pos["dx"].setValue(50.0)
    dlg.zone.setCurrentIndex(1)
    dlg.zone_dx.setValue(10.0)
    v = dlg.values()
    assert shape_from_dict(v["zones"][name]).centroid()[0] == pytest.approx(x0 + 60.0)
    geo = s.set_geometry(**v)
    assert s.apparatus.zone(name).shape.centroid()[0] == pytest.approx(x0 + 60.0) and geo
    dlg.deleteLater()


def test_finished_tests_are_saved_once_from_the_event_loop_while_others_run(win, monkeypatch):
    page = win.goto("LivePage")
    saves, removed = [], []

    class S:
        def __init__(self, n):
            self.n = n

        def remove_autosave(self):
            removed.append(self.n)

    monkeypatch.setattr(win, "save", lambda *a, **k: saves.append(1) or True)
    monkeypatch.setattr(page, "any_active", lambda: True)
    page._save_soon(S(1))
    page._save_soon(S(2))
    assert not saves
    pump(lambda: bool(saves), 2.0)
    assert saves == [1] and removed == [1, 2]
    monkeypatch.setattr(page, "any_active", lambda: False)
    page._save_soon(S(3))  # nothing else running: saved at once
    assert saves == [1, 1] and removed[-1] == 3


# ------------------------------------------------------------------ live charts
def test_live_charts_copy_only_new_frames_and_total_in_the_background():
    a = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(a, DetectionSettings(background="frame"), duration_s=0)
    floor = np.full((200, 200, 3), 200, np.uint8)
    s.set_background(floor)

    def frame(x):
        f = floor.copy()
        f[95:105, x - 5:x + 5] = 30
        return f

    for i in range(40):
        s.process(frame(40 + i), i / 25)
    lc = LiveCharts(total_every_s=0.0)
    t, x = lc.series(s, "X position", window_s=1.0)
    t_all, d = lc.series(s, "Time mobile", window_s=100.0)  # computed now (first time)
    assert len(t) and len(t_all) == 40
    s._track.snapshot = lambda *a: pytest.fail("the whole track copied again")
    for i in range(40, 60):
        s.process(frame(40 + i), i / 25)
    t, x = lc.series(s, "X position", window_s=1.0)  # only the 20 new frames are copied
    assert t[-1] == pytest.approx(s.elapsed)
    t2, _ = lc.series(s, "Time mobile", window_s=100.0)  # the cached total meanwhile, recomputed in a thread
    assert len(t2) == 40
    assert pump(lambda: len(lc.series(s, "Time mobile", window_s=100.0)[0]) == 60, 5.0)


# ------------------------------------------------------------------ results
def test_save_selected_cells_saves_one_cell(win, tmp_path):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    cols = page.shown_columns()
    page.table.selectionModel().select(QItemSelection(page.proxy.index(0, 2), page.proxy.index(0, 2)),
                                       QItemSelectionModel.ClearAndSelect)
    out = page.save_table(str(tmp_path / "one.tsv"), selection=True)
    lines = open(out).read().splitlines()
    assert lines[0] == cols[2] and len(lines) == 2


# ------------------------------------------------------------------ review and score
@pytest.fixture
def view(win):
    win.open_test(win.project.tests[0].id)
    return win.page("TestViewPage")


def test_review_reload_timing_scoring_limits_and_manual_track_undo(view):
    p, t = view.project, view.test
    view._undo.append((0, None))
    view.reload_current()  # tracks re-read from disk: the undo steps of the old ones go
    assert view._undo == []
    view.tabs.setCurrentIndex(2)  # neither Results nor Plots shown: both wait to be recalculated
    view._stale.update(results=False, plots=False)
    view.start_spin.setValue(0.5)
    assert view._stale == {"results": True, "plots": True}
    view.start_spin.setValue(0.0)
    t.events = []
    t.duration_s = 3.0
    d = next(b for b in p.behaviours if b.kind == "point")
    g = next(b for b in p.behaviours if b.kind == "state")
    view.score(d, -0.5)
    view.score(d, 3.5)
    assert t.events == []  # before the start / after the end: not stored
    view.score(g, 2.0)
    view.score(g, 5.0)
    assert t.events == [{"behaviour": g.name, "t": 2.0, "t_end": 3.0}]  # ends with the test
    # undoing a manual track made from scratch: the test keeps its scored events
    p.track_path(t).unlink()
    view.reload_current()
    view.player.seek_time(1.0)
    assert view.mark_position(200.0, 210.0) and t.status == "tracked"
    view.undo_edit()
    assert view.tracks == [] and t.status == "scored"
    view.player.close_video()
