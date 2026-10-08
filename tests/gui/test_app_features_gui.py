"""GUI of the application features of TODO §14: the monitor's live charts of every parameter and its point /
sequence / input statistics, the orientation beam indicator, adjusting the apparatus during a live test, the zone
map image, the experimenter-leaves start for videos, the automatic freezing threshold and the disk-space warning."""

import collections
import shutil

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import diskspace, templates
from manymaze.core import synthetic as syn
from manymaze.core.apparatus import POSITION_KEY, PointOfInterest, Sequence
from manymaze.core.demo import create_demo_project
from manymaze.core.live import LiveSession
from manymaze.core.project import Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.core.video import VideoSource
from manymaze.gui.live_widgets import MonitorPanel
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.live.calibration import LiveGeometryDialog

app = QApplication.instance() or QApplication([])


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
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def _floor():
    return np.full((200, 200), 200, np.uint8)


def _frame(x, y):
    img = _floor()
    syn.draw_mouse(img, x, y, 0)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_monitor_charts_and_point_sequence_input_statistics():
    ap = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    ap.points.append(PointOfInterest("Object", 150, 100, radius_cm=5))
    zone_names = [z.name for z in ap.zones]
    ap.sequences.append(Sequence("Out and in", zone_names[:2]))
    s = LiveSession(ap, DetectionSettings(background="frame"), duration_s=0, name="mon")
    s.set_background(_floor())
    for i in range(60):
        s.process(_frame(30 + 2 * i, 100), i / 25)
    s.engine.io_events.append({"t": 0.5, "device": "box", "channel": "Lever", "kind": "input", "value": 1})
    m = MonitorPanel()
    m.resize(420, 900)
    m.show()
    m.refresh(s)
    assert m.param.count() >= 40  # ANY-maze charts 40+ parameters live
    assert m.points_box.isVisible() and m.points.rowCount() == 1 and m.points.item(0, 0).text() == "Object"
    assert m.seq_box.isVisible() and m.sequences.item(0, 0).text() == "Out and in"
    assert m.inputs_box.isVisible() and m.inputs.item(0, 0).text() == "Lever" and m.inputs.item(0, 2).text() == "1"
    assert m.set_chart_parameter("Distance from arena centre") and not m.set_chart_parameter("Nope")
    m.refresh(s)
    assert len(m.chart.t) > 10 and np.isfinite(m.chart.y).any() and m.chart.unit == "cm"
    assert m.set_chart_parameter("speed")
    m.refresh(s)
    assert m.chart.unit == "cm/s"
    m.clear()
    assert not m.points_box.isVisible()
    m.close()


def test_beam_indicator_and_experimenter_start_options(win):
    page = win.goto("LivePage")
    assert page.beam_act.isChecked() and page.group.beam
    page.beam_act.trigger()
    assert not page.prefs["beam"] and not page._show_beam and not page.group.beam
    page.beam_act.trigger()
    assert page.group.beam
    exp = win.goto("ExperimentPage")
    i = exp.start_mode.findData("experimenter_leaves")
    assert i >= 0
    exp.start_mode.setCurrentIndex(i)
    assert win.project.start_mode == "experimenter_leaves"
    assert "freeze_threshold_mode" in exp.an_form.editors and "freeze_sensitivity" in exp.an_form.editors
    ed = exp.an_form.editors["freeze_threshold_mode"]
    ed.setCurrentIndex(ed.findData("auto"))
    assert win.project.analysis.freeze_threshold_mode == "auto"
    win.page("LivePage").set_project(win.project)  # the live page follows the experiment's start mode
    assert page.start_mode.currentData() == "experimenter_leaves"


def test_adjust_apparatus_during_live_test(win, monkeypatch):
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
    assert page.adjust_geometry(position={"dx": 5}) is None  # no running test
    assert page.arm()
    page._update_buttons()
    assert page.geometry_act.isEnabled()
    dlg = LiveGeometryDialog(page.session)
    dlg.zone.setCurrentIndex(1)
    dlg.zone_dx.setValue(3)
    v = dlg.values()
    assert v["position"]["scale"] == 1.0 and list(v["zones"]) == [page.session.apparatus.zones[0].name]
    dlg.deleteLater()
    base = p.get_apparatus(page.session.apparatus.name)
    x0 = base.zones[0].shape.centroid()[0]
    src = VideoSource(video)
    i = 0
    while page.session is not None and i < 200:
        ok, f = src.read()
        page.feed_frame(f, i / 25)
        if i == 10:
            geo = page.adjust_geometry(position={"dx": 12, "dy": 0, "angle": 0, "scale": 1})
            assert geo[POSITION_KEY]["dx"] == 12
            assert page._apparatus.zones[0].shape.centroid()[0] == pytest.approx(x0 + 12)
            assert any("geometry adjusted" in page.log.item(r).text() for r in range(page.log.count()))
        i += 1
    src.release()
    test = p.get_test(page.last_test_id)
    assert test.zone_overrides[POSITION_KEY]["dx"] == 12
    assert p.get_apparatus(test.apparatus).zones[0].shape.centroid()[0] == pytest.approx(x0)  # map unchanged
    page._update_buttons()
    assert not page.geometry_act.isEnabled()


def test_export_zone_map_image(win, tmp_path):
    page = win.goto("ApparatusPage")
    out = page.export_map_image(str(tmp_path / "map.png"))
    img = cv2.imread(out)
    assert img is not None and img.shape[0] > 100
    svg = page.export_map_image(str(tmp_path / "map.svg"), background=False)
    assert "<svg" in open(svg, encoding="utf-8").read()
    assert page.map_img_act.isEnabled()


def test_disk_space_warning_on_open(win, monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(diskspace.shutil, "disk_usage", lambda p: usage(100, 50, 100 * diskspace.MB))
    space = win.check_disk_space()
    assert space.level == "critical" and win._disk_box.isVisible() and "full" in win._disk_box.text()
    win._disk_box.close()
    monkeypatch.setattr(diskspace.shutil, "disk_usage", lambda p: usage(100, 50, 500 * diskspace.GB))
    win._disk_box = None
    assert win.check_disk_space().ok and win._disk_box is None
