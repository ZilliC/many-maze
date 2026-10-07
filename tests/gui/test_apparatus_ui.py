"""Apparatus page in the ANY-maze layout: ribbon groups, explorer sub-items, page title, map styling, sentence-style
properties, select all / delete selection, points attract and label decluttering."""

import shutil

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox, QToolBar

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui import theme
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.apparatus import RulerItem
from shots import shot_path
from test_apparatus import approx, drag, mouse

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=4)
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


def section(page):
    main = page.main
    return main.sections[main._page_section[id(page)]]


def explorer_children(page):
    it = section(page).item_for(page)
    return [it.child(i).text(0) for i in range(it.childCount())]


def test_ribbon_groups_and_tools(page):
    titles = [t for t, _ in page.ribbon_groups()]
    assert titles == ["Apparatus", "Apparatus map", "Define", "Calibration", "Background", "View"]
    panel = section(page).panel
    assert [g.title for g in panel.context] == titles
    texts = panel.actions_text()
    for t in ("From template", "New", "Duplicate", "Rename", "Delete", "Select objects", "Multiline tool",
              "Rectangle tool", "Ellipse tool", "Line tool", "Select all", "Delete selection", "Points attract",
              "Arena", "Point", "Zone grid", "Zone group", "Sequence", "Ruler", "Video file…", "Test video",
              "Scale to fit", "Show labels"):
        assert t in texts, t
    # large buttons as in ANY-maze's "Apparatus map" group
    big = {b.defaultAction().text() for g in panel.context for b in g.buttons if b.objectName() == "RibbonLarge"}
    assert {"Select objects", "Multiline tool", "From template"} <= big
    # the in-page toolbar is gone and everything fits the 1400 px window
    assert not page.findChildren(QToolBar)
    assert panel.sizeHint().width() <= 1400 and page.main.width() <= 1400
    assert page.view.width() > 700 and page.panel.tabs.width() <= 320
    # the drawing tools are an exclusive group of checkable actions
    acts = page.tool_actions
    assert all(a.isCheckable() for a in acts.values()) and acts["select"].isChecked()
    acts["rect"].trigger()
    assert page.view.tool == "rect" and [k for k, a in acts.items() if a.isChecked()] == ["rect"]
    acts["polygon"].trigger()
    assert page.view.tool == "polygon" and not acts["rect"].isChecked()
    page.arena_actions["ellipse"].trigger()
    assert page.view.tool == "arena" and page.arena_shape == "ellipse" and acts["arena"].isChecked()
    acts["calibrate"].trigger()
    assert page.view.tool == "calibrate"
    acts["select"].trigger()
    assert page.view.tool == "select" and [k for k, a in acts.items() if a.isChecked()] == ["select"]
    # "Show labels" hides the names on the map
    page.labels_act.setChecked(False)
    assert not page.view.item("zone", 0).label.isVisible()
    page.labels_act.setChecked(True)


def test_explorer_lists_apparatus_and_switches(page):
    first = page.app
    assert explorer_children(page) == [a.name for a in page.project.apparatus]
    assert page.page_title.objectName() == "PageTitle" and page.page_title.text() == first.name
    new = page.add_apparatus("Second")
    assert explorer_children(page) == [first.name, "Second"]
    sec = section(page)
    assert sec.explorer.currentItem().text(0) == "Second" and page.page_title.text() == "Second"
    # selecting an apparatus in the explorer switches the map
    sec.explorer.setCurrentItem(sec.item_for(page, first.name))
    app.processEvents()
    assert page.app is first and page.page_title.text() == first.name
    page.show_item("Second")
    assert page.app is new
    # renaming follows in the explorer and the title
    assert page.rename_apparatus("Box B")
    assert explorer_children(page) == [first.name, "Box B"] and page.page_title.text() == "Box B"
    assert sec.explorer.currentItem().text(0) == "Box B"
    page.del_act.trigger()
    assert explorer_children(page) == [first.name] and page.app is first


def test_map_style_and_sentences(page, monkeypatch):
    a = page.app
    it = page.view.item("zone", 0)
    assert it.pen().color() == QColor(theme.APPARATUS) and it.pen().widthF() < 2
    page.select_item("zone", 0)
    assert it.pen().color() == QColor(theme.APPARATUS)
    assert all(h.isVisible() and h.brush().color() == QColor(theme.APPARATUS) for h in it.handles)
    assert it.brush().color().blue() > it.brush().color().red()  # highlighted light blue
    # the view sits on the near-white work area
    assert page.view.backgroundBrush().color() == QColor(theme.WORK_BG)
    # sentence-style zone properties
    assert page.panel.zone_hidden.currentText() == "This is not a hidden zone"
    page.panel.zone_hidden.setCurrentIndex(1)
    assert a.zones[0].hidden and page.panel.zone_hidden.currentText() == "This is a hidden zone"
    page.panel.zone_moveable.setCurrentIndex(1)
    assert a.zones[0].moveable
    assert page.panel.zone_rule.currentText().startswith("Zone entry:")
    page.panel.zone_rule.setCurrentIndex(page.panel.zone_rule.findData("body"))
    assert page.panel.zone_frac.isVisibleTo(page)
    page.panel.zone_rule.setCurrentIndex(page.panel.zone_rule.findData("centre"))
    assert a.zones[0].entry_rule == "centre" and not page.panel.zone_frac.isVisibleTo(page)
    # the calibration ruler is green with ticks
    page.calibrate_from_line(50, 380, 250, 380, 20.0)
    assert isinstance(page.view.ruler, RulerItem) and page.view.ruler.ppc == pytest.approx(10.0)
    assert page.view.ruler_label is not None and page.view.ruler_label.text == "20 cm"


def test_select_all_delete_selection_and_points_attract(page):
    a = page.app
    n = len(a.zones)
    assert page.select_all() == n  # zones, not the arena boundary
    assert not page.view.item("arena").isSelected()
    page.delete_sel_act.trigger()
    assert not a.zones and a.arena is not None
    page.undo_act.trigger()
    assert len(a.zones) == n
    # points attract: a new rectangle starting near a zone corner starts exactly on it
    cx0, cy0, _, _ = a.zone("Centre").shape.bounds()
    page.snap_act.setChecked(True)
    assert page.view.snap(QPointF(cx0 + 1.5, cy0 - 1.0)) == QPointF(cx0, cy0)
    assert page.view.snap(QPointF(cx0 + 60, cy0 + 60)) == QPointF(cx0 + 60, cy0 + 60)
    page.set_tool("rect")
    drag(page.view, (cx0 + 1.5, cy0 + 1.5), (cx0 + 40, cy0 + 30))
    assert approx(a.zones[-1].shape.bounds()[:2], (cx0, cy0), 1e-6)
    page.snap_act.setChecked(False)
    assert page.view.snap(QPointF(cx0 + 1.5, cy0)) == QPointF(cx0 + 1.5, cy0)


def test_rename_moves_per_test_positions(page):
    a = page.app
    t = page.project.tests_using(a.name)[0]
    ci = [z.name for z in a.zones].index("Centre")
    t.zone_overrides["Centre"] = a.zones[ci].shape.translated(5, 5).to_dict()
    page.add_point(100, 100, "Object")
    t.zone_overrides["Object"] = {"x": 120.0, "y": 90.0}
    assert page.rename("zone", ci, "Middle") and page.rename("point", len(a.points) - 1, "Toy")
    assert set(t.zone_overrides) == {"Middle", "Toy"}
    assert a.zones[ci].name == "Middle" and page.panel.zone.list.item(ci).text() == "Middle"
    assert not page.rename("zone", ci, "  ")  # blank: unchanged


def test_undo_steps_only_for_real_changes(page):
    a = page.app
    page.add_point(100, 100, "P")
    assert page.undo() and page.redo()
    # clicking a vertex handle without dragging is not an edit: redo survives
    page.select_item("zone", 0)
    h = page.view.item("zone", 0).handles[0].scenePos()
    mouse(page.view, "press", h.x(), h.y())
    mouse(page.view, "release", h.x(), h.y())
    assert page.undo() and page.redo()
    # spin box steps of one field make one undo step
    page.select_item("point", len(a.points) - 1)
    for x in (101, 102, 103):
        page.panel.point_x.setValue(x)
    assert a.points[-1].x == 103
    assert page.undo() and a.points[-1].x == 100


def test_test_video_menu_and_labels_declutter(page):
    p = page.project
    page.bg.fill_test_menu()
    acts = page.bg.test_menu.actions()
    assert len(acts) == len([t for t in p.tests if t.video])
    acts[-1].trigger()
    t = [t for t in p.tests if t.video][-1]
    assert page.bg.path == p.abs_path(t.video)
    # a fine grid: overlapping name tags are hidden, the selected zone keeps its label
    page.add_grid("polar", "arena", "P", rings=3, sectors=12)
    k = [z.name for z in page.app.zones].index("P R3 S5")
    page.select_item("zone", k)
    for _ in range(3):
        app.processEvents()
    labels = [page.view.item("zone", i).label for i in range(len(page.app.zones))]
    assert page.view.item("zone", k).label.isVisible()
    assert 0 < sum(lb.isVisible() for lb in labels) < len(labels)
    page.main.grab().save(shot_path("shot_apparatus_ui.png"))
