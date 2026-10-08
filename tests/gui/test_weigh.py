"""Animals page: Weigh (scale reading, typed weights, "Record & next", weight history, save / load)."""

import shutil

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.core.scales import WEIGHT_FIELD, ScaleTimeout
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=8)
    return d


@pytest.fixture
def page(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    p = Project.load(d)
    p.io_devices = list(p.io_devices or []) + [{"name": "balance", "type": "scale", "protocol": "mt_sics",
                                                "port": "/dev/fake"}]
    w.set_project(p)
    pg = w.goto("AnimalsPage")
    yield pg
    w.dirty = False
    w.close()


def test_weigh_reads_scale_and_records_history(page):
    readings = iter([21.5, 22.25, 19.0])
    calls = []

    def reader(cfg, timeout=3.0, stable=True):
        calls.append(cfg["name"])
        return next(readings), True

    page.scale_reader = reader
    p = page.project
    assert page.a_weigh.isEnabled() and page.a_weigh in [a for _, acts in page.ribbon_groups() for a in acts]
    first = page._sheet_animals()[0]
    page.table.selectRow(page._row_of(first))
    dlg = page.weigh_dialog(exec_=False)
    assert dlg.animal is first and dlg.scale.currentData()["name"] == "balance"
    assert dlg.read_scale() and dlg.grams.value() == 21.5
    assert dlg.record(next_animal=True)
    second = dlg.animal
    assert second is not first and dlg.grams.value() == 22.25  # read automatically for the next animal
    assert dlg.record()
    assert first.fields[WEIGHT_FIELD] == "21.5" and second.fields[WEIGHT_FIELD] == "22.25"
    assert WEIGHT_FIELD in p.animal_fields and calls == ["balance", "balance"]
    assert len(first.weights) == 1 and first.weights[0]["grams"] == 21.5
    # weigh the same animal again: the history grows and is shown in the dialog
    page.table.selectRow(page._row_of(first))
    dlg = page.weigh_dialog(exec_=False)
    dlg.scale.setCurrentIndex(dlg.scale.count() - 1)  # type the weight
    assert not dlg.read_btn.isEnabled() and not dlg.read_scale()
    assert not dlg.record()  # no weight yet
    dlg.grams.setValue(23.0)
    assert dlg.record()
    assert [w["grams"] for w in first.weights] == [21.5, 23.0] and first.fields[WEIGHT_FIELD] == "23"
    assert dlg.history.rowCount() == 2 and dlg.history.item(0, 1).text() == "23"  # newest first
    c = page.col_of("field", WEIGHT_FIELD)
    assert "Weight history" in page.table.item(page._row_of(first), c).toolTip()
    assert page.main.dirty
    p.save()
    q = Project.load(p.path)
    assert [w["grams"] for w in q.get_animal(first.id).weights] == [21.5, 23.0]


def test_weigh_scale_errors_are_shown(page):
    def reader(cfg, timeout=3.0, stable=True):
        raise ScaleTimeout("balance: no stable weight within 5 s")

    page.scale_reader = reader
    dlg = page.weigh_dialog(exec_=False)
    assert not dlg.read_scale() and "no stable weight" in dlg.state.text()
    assert dlg.grams.value() == 0 and not dlg.record()
    assert all(not a.weights for a in page.project.animals)
