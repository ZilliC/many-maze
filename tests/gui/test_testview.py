"""Smoke tests for the Test view page."""

import math
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow
from shots import shot_path

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=8)
    return d


@pytest.fixture
def view(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    w.open_test(w.project.tests[0].id)
    v = w.page("TestViewPage")
    QTest.qWait(50)
    yield v
    v.player.close_video()
    w.dirty = False
    w.close()


def wait_for(cond, timeout=60):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()  # qWait holds the GIL, starving the worker thread
        time.sleep(0.03)
    assert cond()


def test_load_seek_overlay(view):
    t = view.test
    assert t.id == 1 and view.player.source is not None
    assert len(view.tracks) == 1
    assert view.main.current_page() is view
    view.player.seek_time(4.0)
    raw = view.player.current_frame
    shown = view._overlay(view.player.index, raw)
    assert shown.shape == raw.shape and (shown != raw).any()
    # the animal marker is drawn at the tracked position
    tr = view.tracks[0]
    j = view.sample_at(tr, 4.0)
    x, y = int(round(tr.x[j])), int(round(tr.y[j]))
    assert tuple(shown[y, x]) == (0, 200, 255)
    assert view.hud.isVisible() and "test time" in view.hud.toPlainText()
    assert "centre" in view.pos_lbl.text()
    # next / previous test
    view.step_test(1)
    assert view.test.id == 2
    view.step_test(-1)
    assert view.test.id == 1


def test_results_and_plots(view):
    res = view.results_dict()
    assert view.results_table.rowCount() > 20
    assert float(res["Test duration (s)"]) == pytest.approx(8, abs=0.1)
    assert "Total distance (cm)" in res
    # a long text value (visit durations, visited zones) is elided with a tooltip instead of widening the Value
    # column until the numbers end up off-screen
    from manymaze.gui.pages.testview.page import RESULT_VALUE_MAX_WIDTH

    long = "Quadrant NE: visit durations (s): " + ", ".join(["1.267", "4.383"] * 20)
    analyse = view.project.analyse_test
    view.project.analyse_test = lambda t: [dict(r, **{"Visit durations (s)": long}) for r in analyse(t)]
    try:
        view.refresh_results()
    finally:
        del view.project.analyse_test
    tbl = view.results_table
    row = [r for r in range(tbl.rowCount()) if tbl.item(r, 0).text() == "Visit durations (s)"][0]
    assert tbl.columnWidth(1) <= RESULT_VALUE_MAX_WIDTH
    assert tbl.item(row, 1).text() == tbl.item(row, 1).toolTip() == long
    assert tbl.columnWidth(0) + tbl.columnWidth(1) <= tbl.viewport().width()  # no horizontal scrolling
    view.tabs.setCurrentIndex(1)
    assert view.track_canvas.figure is not None and view.heat_canvas.figure is not None
    assert view.speed_canvas.figure is not None


def test_detection_preview_and_overrides(view):
    p, t = view.project, view.test
    view.player.seek_time(2.0)
    view.chk_preview.setChecked(True)
    key = view.bg_key(p.detection_for(t))
    wait_for(lambda: key in view._bg_cache)
    QTest.qWait(50)
    view._refresh_frame()
    assert "Detected 1/1" in view.preview_info
    # edit a setting → per-test override only
    view.det_form.editors["threshold"].setValue(40)
    assert t.detection == {"threshold": 40}
    assert view.main.dirty
    assert "differ" in view.det_lbl.text()
    view.apply_detection_to_all(confirm=False)
    assert all(x.detection == {"threshold": 40} for x in p.tests)
    view.reset_detection()
    assert t.detection == {}
    view.det_form.editors["min_area_px"].setValue(55)
    view.save_detection_as_default()
    assert p.detection.min_area_px == 55 and t.detection == {}


def test_timing(view):
    t = view.test
    view.player.seek_time(1.0)
    view.set_start_now()
    assert t.start_s == pytest.approx(1.0)
    view.player.seek_time(6.0)
    view.set_end_now()
    assert t.duration_s == pytest.approx(5.0)


def test_track_this_test(view):
    p, t = view.project, view.test
    for i in range(t.n_animals):
        p.track_path(t, i).unlink()
    t.status = "pending"
    t.events = []
    view.reload_current()
    assert view.tracks == [] and "Not tracked" in view.results_lbl.text()
    t.start_s = 2.0
    w = view.track_this_test()
    assert w is not None
    wait_for(lambda: not view._tracking)
    QTest.qWait(50)
    assert t.status == "tracked" and len(view.tracks) == 1
    assert view.video_start() == pytest.approx(2.0)
    assert view.tabs.currentIndex() == 0 and view.results_table.rowCount() > 20
    assert float(view.results_dict()["Test duration (s)"]) == pytest.approx(6, abs=0.1)
    assert not view.main.dirty


def test_manual_scoring_keys(view):
    t = view.test
    t.events = []
    view.reload_current()
    vw = view.player.view
    view.player.seek_time(1.0)
    QTest.keyClick(vw, Qt.Key_R, Qt.NoModifier)  # Rearing on
    assert "Rearing" in view._open_states and "Rearing" in view.active_lbl.text()
    assert "Rearing" in view.hud.toPlainText()
    view.player.seek_time(2.4)
    QTest.keyClick(vw, Qt.Key_R, Qt.NoModifier)  # off
    QTest.keyClick(vw, Qt.Key_D, Qt.NoModifier)  # point event
    assert t.events == [{"behaviour": "Rearing", "t": 1.0, "t_end": 2.4},
                        {"behaviour": "Defecation", "t": 2.4, "t_end": None}]
    assert view.events_table.rowCount() == 2
    # a state left open is closed when leaving the page
    view.player.seek_time(3.0)
    QTest.keyClick(vw, Qt.Key_G, Qt.NoModifier)
    view.on_hide()
    assert {"behaviour": "Grooming", "t": 3.0, "t_end": 3.0} not in t.events  # zero-length dropped
    view.player.seek_time(3.0)
    QTest.keyClick(vw, Qt.Key_G, Qt.NoModifier)
    view.player.seek_time(4.0)
    view.commit()  # saving must not end a behaviour that is still being scored
    assert "Grooming" in view._open_states and not any(e["behaviour"] == "Grooming" for e in t.events)
    QTest.keyClick(vw, Qt.Key_G, Qt.NoModifier)
    assert {"behaviour": "Grooming", "t": 3.0, "t_end": 4.0} in t.events
    # results include the scored behaviour
    view.tabs.setCurrentIndex(0)
    assert view.results_dict()["Rearing: duration (s)"] == "1.4"
    # space still controls the player when the video has focus
    QTest.keyClick(vw, Qt.Key_Space)
    assert view.player.playing
    QTest.keyClick(vw, Qt.Key_Space)
    assert not view.player.playing
    QTest.keyClick(vw, Qt.Key_Right)
    assert view.player.index == int(round(4.0 * view.player.fps)) + 1
    # click-to-seek and delete
    view.events_table.cellClicked.emit(0, 0)
    assert view.player.time == pytest.approx(1.0, abs=0.05)
    view.events_table.selectRow(0)
    view.delete_selected_events()
    assert len(t.events) == 2 and all(e["behaviour"] != "Rearing" for e in t.events)
    assert view.main.dirty


def test_track_editing(view):
    p, t = view.project, view.test
    tr = view.tracks[0]
    view.player.seek_time(2.0)
    j = view.sample_at(tr, 2.0)
    old = (tr.x[j], tr.y[j])
    view.mark_btn.setChecked(True)
    view.advance_spin.setValue(0)
    view.player.view.clicked.emit(123.0, 145.0)
    assert (tr.x[j], tr.y[j]) == (123.0, 145.0)
    assert p.load_tracks(t)[0].x[j] == pytest.approx(123.0)
    view.undo_edit()
    assert view.tracks[0].x[j] == pytest.approx(old[0])
    view.mark_btn.setChecked(False)
    view.set_range(0, 3.0)
    view.set_range(1, 4.0)
    assert view.delete_range()
    tr = view.tracks[0]
    m = (tr.t >= 3.0 - 1e-6) & (tr.t <= 4.0 + 1e-6)
    assert np.isnan(tr.x[m]).all() and not tr.detected[m].any()
    assert view.interpolate_range()
    tr = view.tracks[0]
    assert np.isfinite(tr.x[m]).all() and not tr.detected[m].any()
    saved = p.load_tracks(t)[0]
    assert np.isfinite(saved.x[m]).all()


def test_results_warn_when_the_test_period_changed(view):
    view.tabs.setCurrentIndex(0)
    view.refresh_results()
    assert "again" not in view.results_lbl.text()
    view.start_spin.setValue(view.start_spin.value() + 1.0)
    assert "Track this test</b> again" in view.results_lbl.text()
    view.start_spin.setValue(view.start_spin.value() - 1.0)
    assert "again" not in view.results_lbl.text()


def test_interpolate_never_wipes_the_track(view):
    tr = view.tracks[0]
    before = tr.x.copy()
    view.set_range(0, float(tr.t[0]))
    view.set_range(1, float(tr.t[-1]))
    assert not view.interpolate_range()
    assert np.array_equal(view.tracks[0].x, before, equal_nan=True)
    assert "Nothing to interpolate" in view.edit_lbl.text()


def test_manual_track_without_existing_track(view):
    p, t = view.project, view.test
    p.track_path(t).unlink()
    view.reload_current()
    assert view.tracks == []
    view.player.seek_time(1.0)
    view.advance_spin.setValue(2)
    i0 = view.player.index
    assert view.mark_position(200.0, 210.0)
    assert len(view.tracks) == 1 and view.player.index == i0 + 2
    tr = p.load_tracks(t)[0]
    j = view.sample_at(tr, 1.0)
    assert (tr.x[j], tr.y[j]) == (200.0, 210.0) and math.isnan(tr.x[j + 1])
    assert t.status == "tracked"


def test_no_video_and_hide(view):
    p = view.project
    t = p.add_test("", "C1", stage="Day 1", trial=2)
    view.load_test(t.id)
    assert view.player.source is None and not view.track_btn.isEnabled()
    assert "No video" in view.hud.toPlainText()
    view.load_test(1)
    view.player.play()
    view.on_hide()
    assert not view.player.playing


def test_screenshot(view):
    view.player.seek_time(5.0)
    view.trail_spin.setValue(4)
    view.tabs.setCurrentIndex(0)
    QTest.qWait(150)
    view.main.grab().save(shot_path("shot_testview.png"))


def test_h041_preview_with_a_bad_colour_says_so_instead_of_a_traceback(view, capsys):
    view.project.detection.method = "colour"
    view.project.detection.target_colour = "red"
    view.player.seek_time(2.0)
    view.chk_preview.setChecked(True)
    for _ in range(3):
        view._refresh_frame()
    assert "Invalid colour" in view.preview_info
    assert "Traceback" not in capsys.readouterr().err


def test_missing_video_says_so_instead_of_offering_observation(view):  # H-072
    p, t = view.project, view.test
    assert t.video
    path = Path(p.abs_path(t.video))
    path.rename(str(path) + ".bak")
    try:
        view.load_test(t.id)
        QTest.qWait(50)
        html = view.hud.toPlainText()
        assert "Video not found" in html and "scoring by direct observation" not in html
        assert not view.clock_box.isVisible()
    finally:
        (path.parent / (path.name + ".bak")).rename(path)
