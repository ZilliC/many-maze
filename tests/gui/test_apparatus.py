"""Tests for the Apparatus page (zone editor), driving the canvas with real mouse/key events."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog, QFileDialog, QInputDialog,
                               QMessageBox)

from manymaze.core.apparatus import Apparatus
from manymaze.core.demo import create_demo_project
from manymaze.core.geometry import Ellipse, Polygon
from manymaze.core.project import Project
from manymaze.core.templates import TEMPLATES
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.apparatus import CalibrationDialog, TemplateDialog
from shots import shot_path

app = QApplication.instance() or QApplication([])

EVT = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease,
       "dbl": QEvent.MouseButtonDblClick}


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=8)
    return d


@pytest.fixture
def page(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    p = w.goto("ApparatusPage")
    app.processEvents()
    yield p
    w.dirty = False
    w.close()


# ------------------------------------------------------------------ helpers
def mouse(view, kind, x, y, button=Qt.LeftButton, mods=Qt.NoModifier):
    vp = view.viewport()
    pos = QPointF(view.mapFromScene(QPointF(x, y)))
    btn = Qt.NoButton if kind == "move" else button
    buttons = Qt.NoButton if kind == "release" else button
    ev = QMouseEvent(EVT[kind], pos, QPointF(vp.mapToGlobal(pos.toPoint())), btn, buttons, mods)
    QApplication.sendEvent(vp, ev)


def drag(view, p0, p1, steps=5, mods=Qt.NoModifier):
    mouse(view, "press", *p0, mods=mods)
    for i in range(1, steps + 1):
        f = i / steps
        mouse(view, "move", p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f, mods=mods)
    mouse(view, "release", *p1, mods=mods)
    app.processEvents()


def click(view, x, y):
    mouse(view, "press", x, y)
    mouse(view, "release", x, y)


def bounds(shape):
    return shape.bounds()


def approx(a, b, tol=2.0):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# -------------------------------------------------------------------- tests
def test_layout_and_auto_background(page):
    assert page.minimumSizeHint().width() < 1150
    assert page.main.width() <= 1400
    a = page.app
    assert a is not None and a.name == "Open field"
    # background auto-loaded from the first test using this apparatus
    assert page.bg.real and page.view.frame_size == (400, 400)
    first = next(t for t in page.project.tests if t.apparatus == a.name)
    assert page.bg.path == page.project.abs_path(first.video)
    assert a.frame_size == (400, 400)
    assert len(page.view.map_items) == 1 + len(a.zones)  # arena + zones
    assert page.panel.zone.list.count() == len(a.zones)
    assert "1 cm = 7.50 px" in page.cal_label.text()


def test_template_dialog_params():
    dlg = TemplateDialog(None, "water_maze", "Pool", ["Pool"])
    w = dlg.form.editors["platform_quadrant"]
    assert isinstance(w, QComboBox) and [w.itemText(i) for i in range(w.count())] == ["NE", "NW", "SE", "SW"]
    dlg.set_param("platform_quadrant", "SW")
    assert dlg.params()["platform_quadrant"] == "SW"
    assert dlg.is_replace() and dlg.name() == "Pool"
    dlg.add_new.setChecked(True)
    assert dlg.name() == "Morris water maze"
    for key in TEMPLATES:  # every template builds from the generated form
        dlg.select_template(key)
        assert dlg.key() == key
        assert set(dlg.params()) == set(TEMPLATES[key].params)
        assert dlg.preview.scene().items()
    dlg.deleteLater()


def test_templates_fit_and_drag(page, monkeypatch):
    def fake_epm(self):
        self.select_template("epm")
        self.set_param("arm_length_cm", 30.0)
        self.place_fit.setChecked(True)
        self.add_new.setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(TemplateDialog, "exec", fake_epm)
    n = len(page.project.apparatus)
    page.create_from_template()
    assert len(page.project.apparatus) == n + 1
    epm = page.app
    assert epm.name == "Elevated plus maze" and epm.template == "epm"
    assert {"Open arm W", "Open arm E", "Closed arm N", "Closed arm S", "Centre"} <= {z.name for z in epm.zones}
    assert epm.group("Open arms").zones == ["Open arm W", "Open arm E"]
    assert epm.px_per_cm == pytest.approx(400 / 65)  # fitted to the 400×400 frame
    assert epm.frame_size == (400, 400)
    assert page.bg.real  # inherited the background of the apparatus it was created from

    # replace it with an open field placed by dragging on the image
    def fake_of(self):
        self.select_template("open_field")
        self.set_param("size_cm", 50.0)
        self.place_drag.setChecked(True)
        self.replace.setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(TemplateDialog, "exec", fake_of)
    page.create_from_template()
    assert page.view.tool == "template"
    drag(page.view, (50, 50), (350, 350))
    assert page.view.tool == "select"
    a = page.app
    assert a is epm and a.name == "Elevated plus maze" and a.template == "open_field"
    assert approx(bounds(a.arena), (50, 50, 350, 350))
    assert a.px_per_cm == pytest.approx(300 / 50, rel=0.02)
    assert a.zone("Centre") is not None and a.group("Periphery").exclude == ["Centre"]
    assert page.undo()  # back to the EPM
    assert page.app.template == "epm" and page.app.name == "Elevated plus maze"
    assert page.redo() and page.app.template == "open_field"


def test_draw_move_and_edit_zones(page, monkeypatch):
    a = page.app
    v = page.view
    nz = len(a.zones)
    # rectangle tool
    page.set_tool("rect")
    drag(v, (60, 60), (160, 120))
    assert len(a.zones) == nz + 1
    rect = a.zones[-1]
    assert rect.name == f"Zone {nz + 1}" and isinstance(rect.shape, Polygon)
    assert approx(bounds(rect.shape), (60, 60, 160, 120))
    assert page.panel.zone.list.currentRow() == nz and page.panel.zone.name.text() == rect.name
    assert page.main.dirty

    # polygon tool: clicks + Enter
    page.set_tool("polygon")
    for x, y in [(200, 60), (300, 80), (280, 160), (210, 150)]:
        click(v, x, y)
    QTest.keyClick(v, Qt.Key_Return)
    poly = a.zones[-1]
    assert len(a.zones) == nz + 2 and len(poly.shape.points) == 4
    assert approx(poly.shape.points[1], (300, 80))

    # move a vertex of the rectangle by dragging its handle
    page.set_tool("select")
    ri = a.zones.index(rect)
    page.select_item("zone", ri)
    it = page.view.item("zone", ri)
    assert it.isSelected() and all(h.isVisible() for h in it.handles)
    drag(v, (160, 120), (180, 140))
    assert approx(rect.shape.points[2], (180, 140))
    assert approx(rect.shape.points[0], (60, 60))

    # move the whole zone
    before = list(rect.shape.points)
    drag(v, (145, 100), (155, 105))
    assert all(approx((p[0] - 10, p[1] - 5), q) for p, q in zip(rect.shape.points, before))
    assert page.view.item("zone", ri).pos() == QPointF(0, 0)

    # ellipse tool + resize with a handle
    page.set_tool("ellipse")
    drag(v, (220, 220), (300, 280))
    el = a.zones[-1].shape
    assert isinstance(el, Ellipse) and approx((el.cx, el.cy, el.rx, el.ry), (260, 250, 40, 30))
    page.set_tool("select")
    ei = len(a.zones) - 1
    page.select_item("zone", ei)
    drag(v, (300, 250), (320, 250))
    el = a.zones[ei].shape
    assert approx((el.cx, el.rx, el.ry), (270, 50, 30))

    # Delete key removes, undo restores
    page.select_item("zone", ei)
    v.setFocus()
    QTest.keyClick(v, Qt.Key_Delete)
    assert len(a.zones) == nz + 2
    page.undo_act.trigger()
    assert len(a.zones) == nz + 3 and isinstance(a.zones[-1].shape, Ellipse)

    # list <-> canvas selection
    page.panel.zone.list.setCurrentRow(0)
    assert page.view.item("zone", 0).isSelected()
    page.select_item("zone", 1)
    assert page.panel.zone.list.currentRow() == 1 and page.panel.tabs.currentIndex() == 0

    # rename a zone used by a group; groups follow
    ci = a.zones.index(a.zone("Centre"))
    page.panel.zone.list.setCurrentRow(ci)
    page.panel.zone.name.setText("Middle")
    page.panel.zone.name.editingFinished.emit()
    assert a.zones[ci].name == "Middle" and a.group("Periphery").exclude == ["Middle"]
    assert page.view.item("zone", ci).label.text == "Middle"
    # duplicate names are made unique
    page.panel.zone.list.setCurrentRow(ri)
    page.panel.zone.name.setText("Middle")
    page.panel.zone.name.editingFinished.emit()
    assert a.zones[ri].name == "Middle 2"

    # colour
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#123456"))
    page.panel.zone.list.setCurrentRow(ri)
    page.panel.zone.color.click()
    assert a.zones[ri].color == "#123456"

    # double-click on a polygon edge inserts a vertex
    pi = nz + 1  # undo rebuilt the Zone objects, so look the polygon up again
    poly = a.zones[pi]
    page.select_item("zone", pi)
    click(v, 250, 70)
    mouse(v, "dbl", 250, 70)
    mouse(v, "release", 250, 70)
    assert len(poly.shape.points) == 5


def test_points_lines_arena_calibration_groups(page, monkeypatch):
    a = page.app
    v = page.view
    # point
    page.set_tool("point")
    click(v, 100, 300)
    pt = a.points[-1]
    assert approx((pt.x, pt.y), (100, 300)) and page.panel.tabs.currentIndex() == 1
    page.panel.point_radius.setValue(3.0)
    assert pt.radius_cm == 3.0
    ring = page.view.item("point", len(a.points) - 1).ring
    assert ring.isVisible() and ring.rect().width() == pytest.approx(2 * 3.0 * a.px_per_cm)
    page.panel.point.name.setText("Object A")
    page.panel.point.name.editingFinished.emit()
    assert pt.name == "Object A"
    # move the point by dragging it
    page.set_tool("select")
    drag(v, (100, 300), (120, 310))
    assert approx((pt.x, pt.y), (120, 310))

    # line
    page.set_tool("line")
    drag(v, (60, 330), (340, 330))
    ln = a.lines[-1]
    assert approx((ln.x1, ln.y1, ln.x2, ln.y2), (60, 330, 340, 330)) and page.panel.tabs.currentIndex() == 2
    page.panel.line.name.setText("Midline")
    page.panel.line.name.editingFinished.emit()
    assert ln.name == "Midline"

    # arena as polygon (double-click closes)
    page.set_arena_shape("polygon")
    for x, y in [(40, 40), (360, 40), (360, 360)]:
        click(v, x, y)
    click(v, 40, 360)
    mouse(v, "dbl", 40, 360)
    mouse(v, "release", 40, 360)
    assert isinstance(a.arena, Polygon) and len(a.arena.points) == 4
    assert approx(bounds(a.arena), (40, 40, 360, 360)) and v.tool == "select"

    # calibrate with a line
    monkeypatch.setattr(CalibrationDialog, "exec", lambda dlg: (dlg.length.setValue(20.0), QDialog.Accepted)[1])
    page.btn_cal.click()
    assert v.tool == "calibrate"
    drag(v, (50, 380), (250, 380))
    assert a.px_per_cm == pytest.approx(10.0, rel=0.02) and a.calibration_length_cm == 20.0
    assert "1 cm = " in page.cal_label.text() and "20 cm line" in page.cal_label.text()
    # manual entry
    page.ppc_spin.setValue(5.0)
    page.ppc_spin.editingFinished.emit()
    assert a.px_per_cm == 5.0 and a.calibration_line is None
    page.clear_calibration()
    assert a.px_per_cm is None and "Not calibrated" in page.cal_label.text()

    # zone groups
    g = page.add_group()
    assert page.panel.tabs.currentIndex() == 3 and g.name == "Group 3"
    names = [page.panel.group_inc.item(i).text() for i in range(page.panel.group_inc.count())]
    page.panel.group_inc.item(names.index("Arena")).setCheckState(Qt.Checked)
    page.panel.group_inc.item(names.index("Corner 1")).setCheckState(Qt.Checked)
    page.panel.group_exc.item(names.index("Centre")).setCheckState(Qt.Checked)
    assert g.zones == ["Arena", "Corner 1"] and g.exclude == ["Centre"]
    page.panel.group.name.setText("Outer")
    page.panel.group.name.editingFinished.emit()
    assert g.name == "Outer"
    memb = a.zone_membership([200.0, 60.0], [200.0, 60.0])
    assert list(memb["Outer"]) == [False, True]
    # deleting a zone removes it from groups
    page.delete_item("zone", [z.name for z in a.zones].index("Corner 1"))
    assert g.zones == ["Arena"]


def test_calibration_units(page, monkeypatch):
    p, a = page.project, page.app
    p.apparatus.append(Apparatus(name="Second", px_per_cm=4.0))
    assert page.unit_combo.currentData() == "cm" and page.unit_combo.isEnabled()
    # the calibration dialog: the ruler's length in mm, cm or m, which becomes the unit of the results
    dlg = CalibrationDialog(200.0, 20.0, "cm")
    assert dlg.length.value() == 20.0
    dlg.units.setCurrentIndex(dlg.units.findData("mm"))
    assert dlg.length.value() == pytest.approx(200.0) and dlg.length_cm() == pytest.approx(20.0)

    def fake_exec(d):
        d.units.setCurrentIndex(d.units.findData("mm"))
        d.length.setValue(250.0)
        return QDialog.Accepted
    monkeypatch.setattr(CalibrationDialog, "exec", fake_exec)
    assert page.calibrate_from_line(0, 0, 100, 0)
    assert a.calibration_length_cm == pytest.approx(25.0) and a.px_per_cm == pytest.approx(4.0)
    assert [x.distance_unit for x in p.apparatus] == ["mm", "mm"] and page.unit_combo.currentData() == "mm"
    assert "250 mm line" in page.cal_label.text() and "results in mm" in page.cal_label.text()
    rows = p.results(tests=[t for t in p.tests if t.apparatus == a.name][:1])
    assert "Total distance (mm)" in rows[0] and "Total distance (cm)" not in rows[0]
    # the unit alone, from the calibration row; a new apparatus gets the experiment's unit
    page.unit_combo.setCurrentIndex(page.unit_combo.findData("m"))
    assert p.distance_unit == "m" and all(x.distance_unit == "m" for x in p.apparatus)
    assert page.add_apparatus().distance_unit == "m"
    page.unit_combo.setCurrentIndex(page.unit_combo.findData("cm"))
    assert all(x.distance_unit == "cm" for x in p.apparatus)


def test_apparatus_list_and_background(page, monkeypatch, tmp_path):
    p = page.project
    a = page.app
    users = [t for t in p.tests if t.apparatus == a.name]
    assert users
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Box A", True))
    assert page.rename_apparatus()
    assert a.name == "Box A" and all(t.apparatus == "Box A" for t in users)
    assert page.app_list.item(0).text() == "Box A"

    dup = page.duplicate_apparatus()
    assert dup.name == "Box A copy" and len(dup.zones) == len(a.zones) and dup is page.app
    assert page.bg.real  # duplicate keeps the background
    assert not page.rename_apparatus("Box A")  # duplicate name refused
    assert dup.name == "Box A copy"
    new = page.add_apparatus()
    assert new.name == "Apparatus 3" and new.frame_size == (400, 400) and not new.zones
    assert page.delete_apparatus()
    assert len(p.apparatus) == 2

    # deleting an apparatus that tests use reassigns them
    page.app_list.setCurrentRow(0)
    assert page.app is a
    assert page.delete_apparatus()
    assert all(t.apparatus == "Box A copy" for t in users)

    # backgrounds: from a test video at a given time, and from a file dialog
    t = users[-1]
    t.start_s = 2.0
    page.bg.refresh_tests()
    assert page.bg.test_combo.count() == 1 + len([x for x in p.tests if x.video])
    assert page.bg.use_test_video(t.id)
    assert page.bg.path == p.abs_path(t.video) and page.bg.time_spin.value() == pytest.approx(2.0)
    page.bg.time_spin.setValue(4.0)
    page.bg.seek()
    assert page.bg.memory[id(page.app)][1] == pytest.approx(4.0)
    vid = p.abs_path(p.tests[0].video)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (vid, ""))
    page.bg.load_dialog()
    assert page.bg.path == vid and page.bg.time_slider.value() == 0

    # persisted
    page.main.save()
    q = Project.load(p.path)
    assert [x.name for x in q.apparatus] == ["Box A copy"]
    assert all(x.apparatus == "Box A copy" for x in q.tests if x.id in {u.id for u in users})


def test_screenshot(page):
    a = page.app
    page.apply_template("open_field", 50, 50, 300, 300, {"size_cm": 40.0}, replace=True)
    a.points.clear()
    page.add_point(110, 290, "Object")
    page.app.points[-1].radius_cm = 3
    page.select_item("zone", [z.name for z in a.zones].index("Centre"))
    page.set_tool("select")
    for _ in range(5):
        app.processEvents()
    page.main.grab().save(shot_path("shot_apparatus.png"))
