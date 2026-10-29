"""Importing spreadsheets exported by ANY-maze (or other software)."""

import numpy as np
import pytest
from openpyxl import Workbook

from manymaze.core.importers import (ANIMAL_ROLES, TEST_ROLES, TRACK_ROLES, guess_mapping, import_animals,
                                     import_tests, import_track, parse_number, read_table)
from manymaze.core.project import Animal, Project


def test_parse_number():
    assert parse_number("2.168m") == pytest.approx(2.168)
    assert parse_number("18.9s") == pytest.approx(18.9)
    assert parse_number("0:43") == 43 and parse_number("1:02:03") == 3723
    assert parse_number("1,5") == 1.5 and np.isnan(parse_number("")) and np.isnan(parse_number("n/a"))


def test_animals_and_tests_from_anymaze_style_tables(tmp_path):
    f = tmp_path / "animals.csv"
    f.write_text("Animal,Animal ID,Status,Treatment,Animal weight,Sex\n"
                 "1,C1-A1,Normal,C - Drug X 1mg/kg,120.5,Male\n2,C1-A2,Normal,A - Drug x 5 mg/kg,117.2,Male\n"
                 "3,C2-A1,Normal,B - Saline,118.7,Female\n")
    h, rows = read_table(f)
    m = guess_mapping(h, ANIMAL_ROLES)
    assert h[m["id"]] == "Animal ID" and h[m["group"]] == "Treatment" and h[m["sex"]] == "Sex"
    p = Project()
    ids = import_animals(p, h, rows, m, extra_fields=[h.index("Animal weight")])
    assert ids == ["C1-A1", "C1-A2", "C2-A1"]
    a = p.get_animal("C2-A1")
    assert a.group == "B - Saline" and a.sex == "Female" and a.fields["Animal weight"] == "118.7"
    assert {g.name for g in p.groups} == {"C - Drug X 1mg/kg", "A - Drug x 5 mg/kg", "B - Saline"}

    wb = Workbook()
    ws = wb.active
    ws.append(["Test", "Animal", "Treatment", "Stage", "Trial", "Apparatus", "Distance"])
    ws.append([99, "C1-A1", "Saline", "Treated", 1, "Water-maze 1", "2.168m"])
    ws.append([100, "C1-A2", "Compound X", "Treated", 2, "Water-maze 1", "6.720m"])
    x = tmp_path / "tests.xlsx"
    wb.save(x)
    h, rows = read_table(x)
    m = guess_mapping(h, TEST_ROLES)
    assert h[m["animal"]] == "Animal" and h[m["stage"]] == "Stage" and h[m["trial"]] == "Trial"
    new = import_tests(p, h, rows, m)
    assert [(t.animal_id, t.stage, t.trial) for t in new] == [("C1-A1", "Treated", 1), ("C1-A2", "Treated", 2)]
    assert "Treated" in p.stages


def test_track_table(tmp_path):
    f = tmp_path / "track.txt"
    lines = ["Time\tCentre position X\tCentre position Y\tHead position X\tHead position Y"]
    for i in range(50):
        lines.append(f"{i * 0.04:.2f}\t{0.1 + i * 0.002:.3f}\t0.2\t{0.12 + i * 0.002:.3f}\t0.2")
    f.write_text("\n".join(lines))
    h, rows = read_table(f)
    m = guess_mapping(h, TRACK_ROLES)
    assert [h[m[k]] for k in ("t", "x", "y", "hx", "hy")] == ["Time", "Centre position X", "Centre position Y",
                                                              "Head position X", "Head position Y"]
    tr = import_track(h, rows, m, scale=1000.0, offset=(10, 20))  # metres -> px at 1000 px/m
    assert len(tr) == 50 and tr.fps == pytest.approx(25, rel=1e-3)
    assert tr.x[0] == pytest.approx(110) and tr.y[0] == pytest.approx(220) and tr.hx[-1] > tr.x[-1]
    with pytest.raises(ValueError):
        import_track(["a", "b"], [["1", "2"]], {"t": None, "x": None, "y": None})


def test_import_wizard(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog

    from manymaze.core.demo import create_demo_project
    from manymaze.gui.import_wizard import ImportDialog
    from manymaze.gui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    p = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=3)
    f = tmp_path / "animals.csv"
    f.write_text("Animal ID,Treatment,Sex,Animal weight\nZ1,Saline,Male,21\nZ2,Drug,Female,19\n")
    w = MainWindow()
    w.set_project(p)
    monkeypatch.setattr(ImportDialog, "exec", lambda self: (self.accept(), QDialog.Accepted)[1])
    ids = w.import_table("animals", str(f))
    assert ids == ["Z1", "Z2"] and p.get_animal("Z2").fields["Animal weight"] == "19" and w.dirty
    assert w.current_page() is w.page("AnimalsPage")
    # track data into a test
    t = p.tests[0]
    tf = tmp_path / "track.csv"
    tf.write_text("Time,Centre position X,Centre position Y\n0,100,100\n0.04,101,100\n0.08,102,101\n")
    d = ImportDialog(p, "track", path=str(tf), test=t)
    d.accept()
    assert d.result is not None and len(p.load_tracks(t)[0]) == 3
    w.dirty = False
    w.close()


def _import(p, text, tmp_path, extra=()):
    f = tmp_path / "a.csv"
    f.write_text(text, encoding="utf-8")
    h, rows = read_table(f)
    return import_animals(p, h, rows, guess_mapping(h, ANIMAL_ROLES), [h.index(x) for x in extra])


def test_import_animals_reports_what_it_skips_merges_and_empties(tmp_path):
    p = Project()
    res = _import(p, "Animal ID,Treatment,Sex,Peso\nP01,Control,Male,250\nP01,Control,Male,251\n,HFD,Female,240\n"
                     "P03,,Male,abc\nP04,Control,F,-5\nP05,Control,M,1e9\n", tmp_path, extra=["Peso"])
    assert list(res) == ["P01", "P03", "P04", "P05"] and len(p.animals) == 4  # the count is the real one
    text = res.summary()
    assert text.startswith("4 animals imported (4 new, 0 already")
    assert "row 4: no animal ID" in text and "P01 appears on rows 2, 3" in text
    assert "P03 (row 5): “abc”" in text and "P04 (row 6): “-5”" in text and "P05 (row 7)" in text
    assert p.get_animal("P01").fields["Peso"] == "251" and p.get_animal("P03").fields["Peso"] == ""
    assert "New treatments created: Control" in text
    again = _import(p, "Animal ID,Sex\nP01,Male\n", tmp_path)
    assert (again.created, again.updated, again.warnings) == (0, 1, [])


def test_import_animals_status_column_and_round_trip(tmp_path):
    p = Project()
    res = _import(p, "Animal,Animal ID,Status,Treatment\n1,R01,Active,C\n2,R02,Retired,C\n3,R03,Normal,C\n"
                     "4,R04,???,C\n", tmp_path)
    assert [a.retired for a in p.animals] == [False, True, False, False]
    assert "Unrecognised status on row 5" in res.summary()
    from manymaze.core import export

    q = Project()
    q.animal_fields.append("Litter")
    q.animals += [Animal("A", "T", "Male", {"Litter": "=1+1"}, True, "died", "=SUM(1,2)"),
                  Animal("B", notes="+cmd|' /C calc'!A0"), Animal("C", notes="'quote")]
    f = tmp_path / "out.csv"
    export.export_animals(q, f)
    assert "'=SUM(1,2)" in f.read_text()
    h, rows = read_table(f)
    r = Project()
    import_animals(r, h, rows, guess_mapping(h, ANIMAL_ROLES), [h.index("Litter")])
    for a, b in zip(q.animals, r.animals):
        assert (a.id, a.group, a.sex, a.fields.get("Litter", ""), a.retired, a.retired_reason, a.notes) == \
               (b.id, b.group, b.sex, b.fields.get("Litter", ""), b.retired, b.retired_reason, b.notes)
