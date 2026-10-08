"""Apparatus designer: the zone option "entry only when facing the zone"."""

import shutil

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

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
    w.close()


def test_zone_entry_orientation_option(page):
    panel = page.panel
    assert page.app.zones
    panel.zone.list.setCurrentRow(0)
    app.processEvents()
    z = page.app.zones[0]
    assert panel.zone_orient.isEnabled() and panel.zone_orient.value() == 0
    assert panel.zone_orient.text() == "Entry does not need facing the zone"  # 0 = off
    panel.zone_orient.setValue(40)
    assert z.entry_orientation_deg == 40.0
    panel.zone_orient.setValue(60)
    assert z.entry_orientation_deg == 60.0
    page.undo()  # spin-box steps merge into one undo step
    assert page.app.zones[0].entry_orientation_deg == 0.0
    assert panel.zone_orient.value() == 0
    page.redo()
    assert page.app.zones[0].entry_orientation_deg == 60.0
    # selecting another zone and back shows each zone's own value
    if len(page.app.zones) > 1:
        panel.zone.list.setCurrentRow(1)
        app.processEvents()
        assert panel.zone_orient.value() == int(page.app.zones[1].entry_orientation_deg)
        panel.zone.list.setCurrentRow(0)
        app.processEvents()
    assert panel.zone_orient.value() == 60
    # saved with the project
    name = page.app.zones[0].name
    page.project.save()
    p2 = Project.load(page.project.path)
    assert p2.get_apparatus(page.app.name).zone(name).entry_orientation_deg == 60.0
