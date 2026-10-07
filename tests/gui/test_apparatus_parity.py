"""Apparatus designer: grids, zone properties, copy/paste, sequences and the new templates."""

import os
import shutil

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.measures import AnalysisSettings, analyse
from manymaze.core.project import Project
from manymaze.core.templates import TEMPLATES
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.apparatus import ApparatusPage, GridDialog, TemplateDialog
from shots import shot_path

app = QApplication.instance() or QApplication([])
SHOT = shot_path("shot_apparatus_parity.png")


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
    ApparatusPage._clipboard = None
    w.dirty = False
    w.close()


def zone_names(page):
    return [z.name for z in page.app.zones]


def test_grid_tool_square_rings_sectors(page, monkeypatch):
    n0 = len(page.app.zones)
    g = page.add_grid("square", "arena", "Cells", nx=3, ny=2)
    assert g.zones == [f"Cells {c}" for c in ("A1", "A2", "A3", "B1", "B2", "B3")]
    assert len(page.app.zones) == n0 + 6 and page.view.item("zone", n0) is not None
    assert page.app.group("Cells").zones == g.zones
    # via the dialog: concentric rings
    def fake_exec(dlg):
        dlg.kind.setCurrentIndex(dlg.kind.findData("rings"))
        dlg.rings.setValue(4)
        dlg.name.setText("Rings")
        assert not dlg._form.isRowVisible(dlg.nx) and dlg._form.isRowVisible(dlg.rings)
        return QDialog.Accepted
    monkeypatch.setattr(GridDialog, "exec", fake_exec)
    g2 = page.add_grid_dialog()
    assert g2.kind == "rings" and len(g2.zones) == 4 and "Rings Ring 4" in zone_names(page)
    g3 = page.add_grid("sectors", "arena", "Pie", sectors=6)
    assert len(g3.zones) == 6
    # deleting the grid removes all its zones and its group; undo brings them back
    idx = zone_names(page).index("Pie Sector 2")
    page.panel.zone.list.setCurrentRow(idx)
    assert page.panel.btn_grid_del.isVisibleTo(page)
    assert page.delete_grid_of(idx)
    assert not any(n.startswith("Pie ") for n in zone_names(page)) and page.app.grid("Pie") is None
    assert page.undo() and page.app.grid("Pie") is not None


def test_zone_properties_and_undo(page):
    page.add_grid("square", "arena", "G", nx=2, ny=1)
    i = zone_names(page).index("G A1")
    page.panel.zone.list.setCurrentRow(i)
    app.processEvents()
    page.panel.zone_hidden.setChecked(True)
    page.panel.zone_moveable.setChecked(True)
    page.panel.zone_inv.setValue(2.0)
    page.panel.zone_rule.setCurrentIndex(page.panel.zone_rule.findData("body"))
    assert page.panel.zone_frac.isEnabled()
    page.panel.zone_frac.setValue(60)
    z = page.app.zones[i]
    assert (z.hidden, z.moveable, z.investigation_distance_cm, z.entry_rule, z.body_fraction) == \
        (True, True, 2.0, "body", 0.6)
    it = page.view.item("zone", i)
    assert it.halo is not None and it.halo.isVisible()
    assert "hidden" in it.label.text
    page.undo()
    page.undo()
    z = page.app.zones[i]
    assert z.entry_rule == "" and z.investigation_distance_cm == 2.0
    # saved and reloaded with the project
    page.project.save()
    p2 = Project.load(page.project.path)
    z2 = p2.get_apparatus(page.app.name).zone("G A1")
    assert z2.hidden and z2.moveable


def test_copy_paste_between_apparatus(page):
    src = page.app
    n0 = len(src.zones)
    page.select_item("zone", 0)
    name0 = src.zones[0].name
    c0 = src.zones[0].shape.centroid()
    page.copy_act.trigger()
    page.paste_act.trigger()
    assert len(src.zones) == n0 + 1 and src.zones[-1].name == f"{name0} copy"
    c1 = src.zones[-1].shape.centroid()
    assert c1[0] - c0[0] == pytest.approx(12) and c1[1] - c0[1] == pytest.approx(12)
    assert page.view.selected_key() == ("zone", n0)
    # paste into another apparatus at the same position
    page.add_apparatus("Other")
    assert page.paste() == 1
    z = page.app.zones[0]
    assert z.name == name0 and z.shape.centroid() == pytest.approx(c0)


def test_sequences_editor(page):
    names = page.app.names()
    assert len(names) >= 3
    q = page.add_sequence("Route")
    assert page.panel.tabs.currentIndex() == 4 and page.panel.sequence.list.currentRow() == 0
    for n in names[:3]:
        page.panel.seq_zone.setCurrentText(n)
        page.panel.btn_step_add.click()
    assert q.steps == names[:3]
    assert page.panel.seq_steps.count() == 3 and page.panel.seq_steps.item(0).text() == f"1. {names[0]}"
    page.panel.seq_steps.setCurrentRow(2)
    page.panel.btn_step_up.click()
    assert q.steps == [names[0], names[2], names[1]]
    page.panel.seq_bidir.setChecked(True)
    page.panel.seq_from_start.setChecked(False)
    page.panel.seq_end.setCurrentIndex(page.panel.seq_end.findData("exit"))
    page.panel.seq_max.setValue(30)
    assert (q.bidirectional, q.from_start, q.end, q.max_duration_s) == (True, False, "exit", 30.0)
    assert page.view.seq_overlay is not None and page.view.seq_overlay.scene() is page.view.scene()
    page.panel.tabs.setCurrentIndex(0)
    assert page.view.seq_overlay is None
    # renaming / deleting a zone keeps the steps consistent
    zi = [z.name for z in page.app.zones].index(names[0]) if names[0] in [z.name for z in page.app.zones] else None
    if zi is not None:
        page.rename("zone", zi, "Renamed")
        assert q.steps[0] == "Renamed"
        page.delete_item("zone", zi)
        assert "Renamed" not in q.steps
    page.panel.sequence.name.setText("Path")
    page.commit()
    assert page.app.sequences[0].name == "Path"
    # sequence measures appear in the analysis of the apparatus
    p = page.project
    t = p.tests[0]
    tr = p.load_tracks(t)[0]
    res = analyse(tr, page.app, AnalysisSettings())
    assert "Path: completed" in res
    assert page.delete_item("sequence", 0) and not page.app.sequences


def test_template_dialog_choices_and_multi_well(page):
    dlg = TemplateDialog(None, "multi_well")
    w = dlg.form.editors["n_wells"]
    assert isinstance(w, QComboBox) and [w.itemData(i) for i in range(w.count())] == [6, 12, 24, 48, 96]
    dlg.set_param("n_wells", 6)
    assert dlg.params()["n_wells"] == 6
    dlg.select_template("water_maze")
    assert dlg.params()["platform_quadrant"] == "NE"
    for key in ("novel_tank", "cpp", "hole_board", "thermal_gradient", "home_cage", "activity_wheel"):
        dlg.select_template(key)
        assert dlg.key() == key and dlg.preview.scene().items()
    dlg.deleteLater()
    n = len(page.project.apparatus)
    first = page.apply_template("multi_well", 0, 0, 300, 200, {"n_wells": 6}, replace=False)
    assert len(page.project.apparatus) == n + 6 and first.name == "Well A1" and page.app is first
    assert {a.name for a in page.project.apparatus[-6:]} == {f"Well {r}{c}" for r in "AB" for c in (1, 2, 3)}
    assert all(k in TEMPLATES for k in ("novel_tank", "multi_well", "cpp", "hole_board"))


def test_screenshot_polar_grid_and_sequence(page):
    page.add_grid("polar", "arena", "Polar", rings=3, sectors=8)
    q = page.add_sequence("Inward", ["Polar R3 S2", "Polar R2 S2", "Polar R1", "Polar R2 S6", "Polar R3 S6"])
    q.bidirectional = True
    page.panel.sequence.list.setCurrentRow(0)
    page.panel.load_seq_editor()
    page.show_sequence_overlay()
    page.view.fit()
    for _ in range(5):
        app.processEvents()
    assert page.view.seq_overlay is not None
    os.makedirs(os.path.dirname(SHOT), exist_ok=True)
    assert page.main.grab().save(SHOT)
    assert os.path.getsize(SHOT) > 10_000
