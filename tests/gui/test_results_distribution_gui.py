"""GUI of results and distribution: SYLK / dBase saving, animated track playback, chart wheel zoom, the update
check, ANY-maze import, adjusting the calibration during a live test, and 48 cameras × 40 simultaneous tests."""

import shutil
import time

import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

import manymaze.core.video as video_mod
from manymaze.core import templates
from manymaze.core.apparatus import CALIBRATION_KEY
from manymaze.core.demo import create_demo_project
from manymaze.core.export import read_sylk
from manymaze.core.project import Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.updates import UpdateInfo
from manymaze.core.video import VideoSource
from manymaze.gui import updates as gui_updates
from manymaze.gui.live_widgets import LAYOUTS
from manymaze.gui.live_widgets.panels import grid_columns
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def pump(cond, timeout=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=1, seconds=6)
    p.save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    for k in ("question", "information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, k, lambda *a, **kw: QMessageBox.Save if a and "Stop" in str(a[1:2])
                            else QMessageBox.Ok)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a, **k: True)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    yield w
    lp = w.page("LivePage")
    if lp is not None:
        lp.shutdown()
    w.dirty = False
    w.close()


def _results(win):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    return page


# ====================================================================== SYLK / dBase
def test_save_sylk_and_dbase(win, tmp_path, monkeypatch):
    page = _results(win)
    menu_texts = [a.text() for a in page.save_act.menu().actions()] if page.save_act.menu() else []
    assert not menu_texts or any("SYLK" in t for t in menu_texts)
    out = page.save_table(str(tmp_path / "res.slk"))
    cells = read_sylk(out)
    assert cells[0][0] == page.shown_columns()[0] and len(cells) == len(page.shown_rows()) + 1
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a[2]))
    if len(page.shown_columns()) > 255:  # too many fields for dBase: a clear message, nothing written
        assert page.save_table(str(tmp_path / "all.dbf")) is None and "at most 255 fields" in errors.pop()
        page.set_visible_measures(page.visible_measures()[:40])
    out = page.save_table(str(tmp_path / "res.dbf"))
    assert not errors, errors
    raw = open(out, "rb").read()
    assert raw[0] == 0x03 and int.from_bytes(raw[4:8], "little") == len(page.shown_rows())
    # a name without an extension gets the chosen format's
    assert page.save_table(str(tmp_path / "sel"), suffix=".dbf").endswith(".dbf")


# ====================================================================== animated track playback
def test_track_playback(win):
    page = _results(win)
    page.set_view("track")
    page.table.selectRow(0)
    page.render_all()
    pb = page.playback
    assert pb.isVisibleTo(page) and pb.isEnabled() and pb.speed == 4.0
    n = len(pb.t)
    assert n > 100 and pb.frames_shown() == n  # the whole track before playing
    path = pb._path
    full_segments = len(path.get_segments())
    pb.set_speed(16.0)
    with pytest.raises(ValueError):
        pb.set_speed(3.0)
    assert pb.play() and pb.playing and pb.time == pb.start
    pb.advance(0.125)  # 0.125 s of real time × 16 = 2 s of track
    assert pb.time == pytest.approx(pb.start + 2.0)
    k = pb.frames_shown()
    assert 0 < k < n and len(path.get_segments()) == k - 1 < full_segments
    assert pb._now.get_visible() and pb.slider.value() == pytest.approx(2.0 / (pb.end - pb.start) * 1000, abs=2)
    assert all(not a.get_visible() for a in pb._end)
    assert pump(lambda: not pb.playing, 10)  # the timer plays to the end and stops
    assert pb.frames_shown() == n and len(path.get_segments()) == full_segments and not pb._now.get_visible()
    pb.slider.setValue(500)  # scrub to the middle
    assert pb.time == pytest.approx(pb.start + (pb.end - pb.start) / 2, abs=0.05)
    pb.seek(-5)
    assert pb.time == pb.start and pb.frames_shown() == 1
    # single-colour tracks (a plain line) are animated too
    page.set_plot_options(color_by="none")
    page.render_all()
    pb.seek(1.0)
    assert len(pb._path.get_xdata()) == pb.frames_shown() < n
    # leaving the track plot pauses and hides it; split plots have no playback
    pb.play()
    page.set_view("heat")
    assert not pb.playing and not pb.isVisibleTo(page)
    page.set_view("track")
    page.set_plot_options(split=True)
    page.render_all()
    assert not pb.isEnabled()


# ====================================================================== chart wheel zoom
def _scroll(canvas, ax, xdata, step):
    x, y = ax.transData.transform((xdata, sum(ax.get_ylim()) / 2))
    ev = MouseEvent("scroll_event", canvas, x, y, step=step)
    canvas.callbacks.process("scroll_event", ev)


def test_chart_wheel_zoom(win):
    page = _results(win)
    page.main_tabs.setCurrentIndex(1)
    ch = page.charts
    ch.set_selection(test_id=win.project.tests[0].id, params=["Speed", "Distance from wall"])
    home = ch.time_range()
    assert home[0] == pytest.approx(0, abs=0.05) and home[1] == pytest.approx(6.0, abs=0.1)
    assert not ch.reset_zoom_btn.isEnabled()
    ax = ch.figure.axes[0]
    _scroll(ch.canvas, ax, 2.0, 1)  # wheel forward over t = 2 s: zoom in about it
    a, b = ch.time_range()
    assert b - a == pytest.approx(0.8 * (home[1] - home[0]))
    assert (2.0 - a) / (b - a) == pytest.approx((2.0 - home[0]) / (home[1] - home[0]), abs=1e-6)  # 2 s stays put
    assert ch.figure.axes[1].get_xlim() == pytest.approx((a, b)) and ch.reset_zoom_btn.isEnabled()
    for _ in range(40):
        _scroll(ch.canvas, ax, 2.0, 1)
    a, b = ch.time_range()
    assert b - a == pytest.approx(5 / ch.track.fps)  # at most a few frames
    for _ in range(60):
        _scroll(ch.canvas, ax, sum(ch.time_range()) / 2, -1)  # zooming out stops at the whole range
    assert ch.time_range() == pytest.approx(home)
    _scroll(ch.canvas, ax, 3.0, 2)
    xy = ax.transData.transform((3.0, sum(ax.get_ylim()) / 2))
    ev = MouseEvent("button_press_event", ch.canvas, *xy, button=1, dblclick=True)
    ch.canvas.callbacks.process("button_press_event", ev)
    assert ch.time_range() == pytest.approx(home) and not ch.reset_zoom_btn.isEnabled()
    ch.zoom_time(0.5)
    ch.reset_zoom()
    assert ch.time_range() == pytest.approx(home)


# ====================================================================== update check
def test_check_for_updates(win, monkeypatch):
    shown = []

    class Box:
        Information = QMessageBox.Information
        Open, Close = QMessageBox.Open, QMessageBox.Close

        def __init__(self, icon, title, text, buttons, parent):
            shown.append(text)

        def setInformativeText(self, t):
            pass

        def setDetailedText(self, t):
            shown.append("notes:" + t)

        def setDefaultButton(self, b):
            pass

        def exec(self):
            return QMessageBox.Open

    opened = []
    monkeypatch.setattr(gui_updates, "QMessageBox", type("QMB", (), {
        "Information": QMessageBox.Information, "Open": QMessageBox.Open, "Close": QMessageBox.Close,
        "__new__": lambda cls, *a: Box(*a),
        "information": staticmethod(lambda *a: shown.append(("info", a[2]))),
        "warning": staticmethod(lambda *a: shown.append(("warn", a[2])))}))
    monkeypatch.setattr(gui_updates.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    newer = UpdateInfo(True, "0.1.0", "0.2.0", "https://github.com/ZilliC/many-maze/releases/tag/v0.2.0", "0.2.0",
                       "What's new")
    w = win.check_updates(checker=lambda: newer)
    assert w is not None and win.check_updates(checker=lambda: newer) is None  # one check at a time
    assert pump(lambda: win._update_worker is None)
    assert "0.2.0" in shown[0] and opened == [newer.url] and win.last_update_info is newer
    assert win.settings.value(gui_updates.LAST_CHECK_KEY)
    shown.clear()
    win.check_updates(checker=lambda: UpdateInfo(True, "0.1.0", "0.1.0"))
    assert pump(lambda: win._update_worker is None)
    assert shown == [("info", "mANY-MAZE 0.1.0 is up to date (latest release: 0.1.0).")]
    shown.clear()
    win.check_updates(checker=lambda: UpdateInfo(False, error="no connection (offline)"))
    assert pump(lambda: win._update_worker is None)
    assert shown[0][0] == "warn" and "no connection" in shown[0][1]
    # the check at startup: off by default; when on, at most once a week and silent unless there is an update
    shown.clear()
    assert not win.update_startup_act.isChecked() and gui_updates.startup_check(win, lambda: newer) is None
    win.update_startup_act.setChecked(True)
    assert gui_updates.startup_check_enabled(win.settings)
    assert gui_updates.startup_check(win, lambda: newer) is None  # checked a moment ago
    win.settings.setValue(gui_updates.LAST_CHECK_KEY, "2000-01-01T00:00:00")
    assert gui_updates.startup_check(win, lambda: UpdateInfo(False, error="x")) is not None
    assert pump(lambda: win._update_worker is None) and shown == []
    win.update_startup_act.setChecked(False)
    assert not gui_updates.startup_check_enabled(win.settings)
    help_tab = win.ribbon.tabs.tabText(win._help_tab)
    assert help_tab == "Help"


# ====================================================================== ANY-maze import
AM_XML = """<?xml version="1.0"?>
<Experiment><Title>EPM</Title>
  <Animal><Number>7</Number><ID>X7</ID><Treatment>Drug</Treatment>
    <Test><Number>1</Number><Stage>Day 1</Stage><Trial>1</Trial><Apparatus>Box Z</Apparatus><Scaling>500</Scaling>
      <Zone><Name>Centre</Name><BoundingBox><x>30</x><y>20</y><w>20</w><h>20</h></BoundingBox></Zone>
      <r><tm>0</tm><c><x>10</x><y>10</y></c></r><r><tm>0.04</tm><c><x>12</x><y>10</y></c></r>
      <r><tm>0.08</tm><np/></r><r><tm>0.12</tm><c><x>40</x><y>30</y></c></r>
    </Test>
  </Animal>
</Experiment>
"""


def test_import_from_anymaze(win, tmp_path):
    p = win.project
    h, w = 60, 80
    rows = ["Zone area map for,Box Z,Centre", f"Zone area map dimensions (w x h),{w},{h}"]
    grid = np.zeros((h, w), int)
    grid[20:40, 30:50] = 1
    rows += [",".join(map(str, r)) for r in grid]
    zm = tmp_path / "Individual zone area map for Box Z, Centre zone.csv"
    zm.write_text("\n".join(rows) + "\n")
    apps = win.import_zone_maps([str(zm)])
    assert [a.name for a in apps] == ["Box Z"] and p.get_apparatus("Box Z").zone("Centre") is not None
    assert win.current_page() is win.page("ApparatusPage")
    xml = tmp_path / "exp.xml"
    xml.write_text(AM_XML)
    n = len(p.tests)
    win.import_anymaze_xml(str(xml))
    assert pump(lambda: getattr(win, "last_import", None) is not None, 30)
    res = win.last_import
    assert res["apparatus"] == [] and len(res["tests"]) == 1 and len(p.tests) == n + 1  # the zone-map apparatus
    t = p.tests[-1]
    assert t.animal_id == "X7" and t.apparatus == "Box Z" and t.status == "tracked"
    assert p.apparatus_of(t).px_per_cm == 5.0 and p.get_animal("X7").group == "Drug"
    assert len(p.load_tracks(t)[0]) == 4 and win.current_page() is win.page("TestsPage")
    bad = tmp_path / "bad.xml"
    bad.write_text("<root/>")
    assert win.import_anymaze_xml(str(bad)) is None


# ====================================================================== live calibration
def test_adjust_calibration_during_live_test(win, monkeypatch):
    p = win.project
    video = p.abs_path(p.tests[0].video)
    page = win.goto("LivePage")
    page.set_simulation_file(video)
    page.duration.setValue(2.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)
    page.animal.setCurrentText("C1")
    monkeypatch.setattr(page, "start_preview", lambda: True)
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=21)))
    page._on_opened(400, 400, 25.0)
    assert page.adjust_calibration(3.0) is None  # no running test
    assert page.arm()
    page._update_buttons()
    assert page.calibrate_act.isEnabled()
    app_px = p.get_apparatus(page.session.apparatus.name).px_per_cm
    src = VideoSource(video)
    i = 0
    while page.session is not None and i < 200:
        ok, f = src.read()
        page.feed_frame(f, i / 25)
        if i == 10:
            cal = page.adjust_calibration(px_per_cm=app_px / 2)
            assert cal["px_per_cm"] == pytest.approx(app_px / 2)
            assert page.session.stats.unit == "cm" and page._apparatus.px_per_cm == pytest.approx(app_px / 2)
            assert any("Calibration adjusted" in page.log.item(r).text() for r in range(page.log.count()))
        i += 1
    src.release()
    test = p.get_test(page.last_test_id)
    assert test.status == "tracked" and test.zone_overrides[CALIBRATION_KEY]["px_per_cm"] == pytest.approx(app_px / 2)
    assert p.get_apparatus(test.apparatus).px_per_cm == pytest.approx(app_px)  # the map is unchanged
    # the results use the test's calibration: twice the distance of the same track at the map's scale
    row = p.analyse_test(test)[0]
    test.zone_overrides.pop(CALIBRATION_KEY)
    assert row["Total distance (cm)"] == pytest.approx(2 * p.analyse_test(test)[0]["Total distance (cm)"], rel=0.01)
    page._update_buttons()
    assert not page.calibrate_act.isEnabled()


# ====================================================================== 48 cameras, 40 apparatus
class FakeCamera:
    """A camera (any index) delivering two mice in two open fields (400 × 200)."""

    def __init__(self, source, width=None, height=None, fps=None):
        from manymaze.core import synthetic as syn
        import cv2

        self.is_camera = True
        self.fps, self.width, self.height = 25.0, 400, 200
        self.i = 0
        idx = int(source)
        a = syn.random_walk(30, (20, 20, 180, 180), speed=3, seed=idx)
        b = syn.random_walk(30, (220, 20, 380, 180), speed=3, seed=100 + idx)
        self.frames = []
        for (ax, ay), (bx, by) in zip(a, b):
            img = np.full((200, 400), 200, np.uint8)
            syn.draw_mouse(img, ax, ay, 0.0)
            syn.draw_mouse(img, bx, by, 0.0)
            self.frames.append(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))

    def read(self):
        time.sleep(0.02)
        self.i += 1
        return True, self.frames[self.i % len(self.frames)]

    def seek(self, i):
        self.i = i

    def release(self):
        pass


def test_grid_layouts():
    assert grid_columns("auto", 40) == 7 and grid_columns("auto", 48) == 7 and grid_columns("auto", 1) == 1
    assert grid_columns("8x5", 40) == 8 and grid_columns("8x6", 3) == 3 and grid_columns("bogus", 9) == 2
    assert {"8x5", "8x6", "auto"} <= set(LAYOUTS)


def test_48_cameras_40_simultaneous_tests(win, monkeypatch):
    monkeypatch.setattr(video_mod, "VideoSource", FakeCamera)  # SourceSpec.open uses core.video.VideoSource
    p = win.project
    for name, x in (("Left box", 10), ("Right box", 210)):
        ap = templates.build("open_field", x, 10, 180, 180, size_cm=40)
        ap.name = name
        ap.frame_size = (400, 200)
        p.apparatus.append(ap)
    for k in range(40):
        p.ensure_animal(f"M{k + 1:02d}")
    page = win.goto("LivePage")
    win.show()
    assert page.set_mode("multi")
    keys = [page.add_source(i) for i in range(48)]
    assert len(set(keys)) == 48 and len(page.group.sources) == 48
    entries = []
    for k in range(40):  # cameras 0-3 film two apparatus each, cameras 4-35 one, cameras 36-47 none
        cam = k // 2 if k < 8 else k - 4
        entries.append(page.add_session_row(keys[cam], "Left box" if k >= 8 or k % 2 == 0 else "Right box",
                                            f"M{k + 1:02d}"))
    assert all(e is not None for e in entries)
    assert page.sess_table.rowCount() == 40 and len(page.mosaic.panels) == 40
    page.set_layout("auto")
    app.processEvents()
    assert page.mosaic.grid.columnCount() == 7  # 40 panels: 7 × 6
    assert page.mosaic_scroll.widget() is page.mosaic
    page.set_layout("8x5")
    app.processEvents()
    assert page.mosaic.grid.columnCount() == 8
    page.duration.setValue(1.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.record.setChecked(False)
    assert page.start_cameras()
    assert pump(lambda: len(page.group.runners) == 48 and all(r.last_frame is not None
                                                                for r in page.group.runners.values()), 30)
    assert page.capture_group_backgrounds() == 48
    for k in keys:  # as if captured with the arenas empty
        page._group_bgs[k] = np.full((200, 400), 200, np.uint8)
    n_tests = len(p.tests)
    assert page.arm_all() == 40
    assert pump(lambda: all(e.saved for e in entries), 120), [e.state for e in entries]
    new = [t for t in p.tests[n_tests:] if t.recorded_at]
    assert len(new) == 40 and {t.animal_id for t in new} == {f"M{k + 1:02d}" for k in range(40)}
    for t in new:
        trs = p.load_tracks(t)
        tr = trs[0]
        assert len(tr) >= 5 and tr.detected.mean() > 0.8, (t.id, t.apparatus, t.animal_id, t.extra_animals, len(trs),
                                                           [x.detected.mean() for x in trs], tr.meta, t.detection)
        x0 = 10 if t.apparatus == "Left box" else 210
        assert np.all((tr.x[tr.detected] > x0 - 5) & (tr.x[tr.detected] < x0 + 185))
    assert not [w for w in page.group.warnings if "Cannot open" in w[1]]
