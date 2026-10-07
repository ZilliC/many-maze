"""Smoke tests for the Animals page."""

import csv
import shutil

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.export import export_animals
from manymaze.core.importers import ANIMAL_ROLES, guess_mapping, import_animals, read_table
from manymaze.core.project import Project
from manymaze.core.workflow import treatment_code
from manymaze.gui.main_window import MainWindow
from shots import shot_path

app = QApplication.instance() or QApplication([])


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
    w.set_project(Project.load(d))
    p = w.goto("AnimalsPage")
    yield p
    w.dirty = False
    w.close()


def col(page, name):
    for c in range(page.table.columnCount()):
        if page.table.horizontalHeaderItem(c).text() == name:
            return c
    raise KeyError(name)


def row_of(page, aid):
    c = col(page, "Animal ID")
    for r in range(page.table.rowCount()):
        if page.table.item(r, c).text() == aid:
            return r
    return -1


def test_table_and_counts(page):
    p = page.project
    assert page.table.rowCount() == len(p.animals) == 4
    r = row_of(page, "C1")
    assert page.table.item(r, col(page, "Tests")).data(Qt.DisplayRole) == 1
    assert page.treatments.rowCount() == 2
    assert [page.table.horizontalHeaderItem(c).text() for c in range(4)] == ["Animal", "Animal ID", "Status",
                                                                             "Treatment"]
    assert page.table.item(r, col(page, "Status")).text() == "Normal"


def test_add_duplicate_delete(page):
    p = page.project
    a = page.add_animal("C1")  # taken -> incremented
    assert a.id not in ("C1",) and p.get_animal(a.id) is a
    added = page.add_several("M", 3, group="New group")
    assert [x.id for x in added] == ["M01", "M02", "M03"]
    assert any(g.name == "New group" for g in p.groups)
    assert page.add_several("M", 4) and len([x for x in p.animals if x.id.startswith("M")]) == 4
    page.table.selectRow(row_of(page, "M01"))
    dup = page.duplicate_selected()
    assert len(dup) == 1 and dup[0].group == "New group" and dup[0].id not in ("M01", "M02", "M03", "M04")
    n = len(p.animals)
    page.table.clearSelection()
    page.table.selectRow(row_of(page, "M02"))
    page.delete_selected_interactive()
    assert len(p.animals) == n - 1 and p.get_animal("M02") is None
    assert page.main.dirty


def test_inline_edit_rename_and_group(page):
    p = page.project
    tests_c1 = [t for t in p.tests if t.animal_id == "C1"]
    r = row_of(page, "C1")
    page.table.item(r, col(page, "Animal ID")).setText("Ctrl-1")
    assert p.get_animal("Ctrl-1") is not None and p.get_animal("C1") is None
    assert all(t.animal_id == "Ctrl-1" for t in tests_c1)
    # duplicate ID rejected
    r = row_of(page, "C2")
    page.table.item(r, col(page, "Animal ID")).setText("A1")
    assert p.get_animal("C2") is not None and page.table.item(row_of(page, "C2"), col(page, "Animal ID")).text() == "C2"
    # typing a new treatment creates it
    page.table.item(row_of(page, "C2"), col(page, "Treatment")).setText("Vehicle")
    assert p.get_animal("C2").group == "Vehicle"
    g = next(g for g in p.groups if g.name == "Vehicle")
    assert g.color not in ("#3b82f6", "#ef4444")
    page.table.item(row_of(page, "C2"), col(page, "Sex")).setText("Female")
    assert p.get_animal("C2").sex == "Female"


def test_groups_and_fields(page, monkeypatch):
    p = page.project
    assert page.rename_group("Control", "Saline")
    assert all(a.group != "Control" for a in p.animals)
    assert sum(a.group == "Saline" for a in p.animals) == 2
    assert not page.rename_group("Saline", "Anxious")  # name taken
    page.set_group_color("Saline", "#123456")
    assert p.group_color("Saline") == "#123456"
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog, QInputDialog
    page.treatments.setCurrentCell(0, 0)
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#abcdef"))
    page._color_group_dialog()
    assert p.groups[0].color == "#abcdef"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Treated", True))
    page._add_group_dialog()
    assert any(g.name == "Treated" for g in p.groups)
    page.delete_group("Anxious")
    assert all(a.group != "Anxious" for a in p.animals) and not any(g.name == "Anxious" for g in p.groups)

    assert page.add_field("Weight (g)")
    assert "Weight (g)" in p.animal_fields
    c = col(page, "Weight (g)")
    page.table.item(row_of(page, "A1"), c).setText("31.5")
    assert p.get_animal("A1").fields["Weight (g)"] == "31.5"
    assert page.rename_field("Weight (g)", "Weight")
    assert p.get_animal("A1").fields == {"Weight": "31.5"}
    page.remove_field("Weight")
    assert p.animal_fields == [] and "Weight" not in p.get_animal("A1").fields


def test_csv_roundtrip(page, tmp_path):
    p = page.project
    src = tmp_path / "in.csv"
    src.write_text("ID;Group;Sex;Genotype;Weight\nC1;Control;Male;WT;30\nK7;KO;Female;KO;25\n")
    header, rows = read_table(src)
    import_animals(p, header, rows, guess_mapping(header, ANIMAL_ROLES), extra_fields=[3, 4])
    assert p.animal_fields == ["Genotype", "Weight"]
    k7 = p.get_animal("K7")
    assert k7.group == "KO" and k7.sex == "Female" and k7.fields == {"Genotype": "KO", "Weight": "25"}
    assert any(g.name == "KO" for g in p.groups)
    page.refresh()
    assert page.table.rowCount() == 5
    out = tmp_path / "out.csv"
    export_animals(p, out)
    rows = list(csv.reader(out.open()))
    assert rows[0] == ["ID", "Treatment", "Sex", "Genotype", "Weight"]
    assert ["K7", "KO", "Female", "KO", "25"] in rows
    assert len(rows) == 6


def test_rename_treatment_keeps_blind_code(page):
    p = page.project
    p.blind = True
    code = treatment_code(p, "Control")
    page.set_blind(False)
    page.treatments.item(0, 0).setText("Saline")
    assert p.groups[0].name == "Saline" and treatment_code(p, "Saline") == "A"
    p.blind = True
    assert treatment_code(p, "Saline") == code and "Control" not in p.settings_extra["blind_codes"]


def test_screenshot(page):
    page.add_field("Genotype")
    page.add_field("Weight (g)")
    gen = ["WT", "KO"]
    for i, a in enumerate(page.project.animals):
        a.sex = "Male" if i % 2 else "Female"
        a.fields = {"Genotype": gen[i % 2], "Weight (g)": f"{24 + i * 1.7:.1f}"}
    page.add_several("M", 8, group="Vehicle", start=1)
    page.refresh()
    w = page.main
    w.resize(1400, 880)
    w.show()
    app.processEvents()
    w.grab().save(shot_path("shot_animals.png"))
