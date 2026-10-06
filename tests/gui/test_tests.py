"""Smoke tests for the Tests page."""

import shutil
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.core.track import Track
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages import tests as tests_mod
from manymaze.gui.pages.tests import (C_ANIMAL, C_DUR, C_STAGE, C_START, C_TRIAL, AddVideosDialog, DlcImportDialog,
                                      VariablesDialog,
                                      track_tests_job, tracking_batches)
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
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    p = w.goto("TestsPage")
    yield p
    w.dirty = False
    w.close()


def wait_tracking(page, timeout=20):
    t0 = time.time()
    while page._tracking and time.time() - t0 < timeout:
        app.processEvents()  # qWait holds the GIL, starving the worker thread
        time.sleep(0.03)
    if page._tracking:
        import faulthandler, sys
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    assert not page._tracking
    QTest.qWait(50)


def source_index(page, test_id, col):
    r = next(i for i, t in enumerate(page.project.tests) if t.id == test_id)
    return page.model.index(r, col)


def test_table_and_summary(page):
    assert page.proxy.rowCount() == 4
    assert "4</b> tests" in page.summary.text() and "4 tracked" in page.summary.text()
    # group column comes from the animal; status coloured
    assert page.model.index(0, 2).data() in ("Control", "Anxious")
    # sorting by animal
    page.table.sortByColumn(C_ANIMAL, Qt.AscendingOrder)
    names = [page.proxy.index(r, C_ANIMAL).data() for r in range(4)]
    assert names == sorted(names)
    page.select_ids({2, 3})
    assert page.selected_ids() == [2, 3]
    assert page.a_track.isEnabled() and not page.a_open.isEnabled()


def test_inline_editing(page):
    p = page.project
    m = page.model
    assert m.setData(source_index(page, 1, C_ANIMAL), "NEW1")
    assert p.get_test(1).animal_id == "NEW1" and p.get_animal("NEW1") is not None
    assert m.setData(source_index(page, 1, C_STAGE), "Day 2")
    assert "Day 2" in p.stages
    m.setData(source_index(page, 1, C_TRIAL), 3)
    m.setData(source_index(page, 1, C_START), 1.5)
    m.setData(source_index(page, 1, C_DUR), 5.0)
    t = p.get_test(1)
    assert (t.trial, t.start_s, t.duration_s) == (3, 1.5, 5.0)
    assert page.main.dirty
    # delegate offers an editable animal combo
    from PySide6.QtWidgets import QComboBox, QStyleOptionViewItem
    ed = page.table.itemDelegate().createEditor(page.table, QStyleOptionViewItem(), source_index(page, 2, C_ANIMAL))
    assert isinstance(ed, QComboBox) and ed.isEditable() and ed.count() == len(p.animals)


def test_add_duplicate_exclude_delete(page, monkeypatch, tmp_path):
    p = page.project
    vid = tmp_path / "mouse7.avi"
    shutil.copy(p.abs_path(p.tests[0].video), vid)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(vid)], ""))
    monkeypatch.setattr(AddVideosDialog, "exec", lambda self: QDialog.Accepted)
    page.add_from_videos()
    t = p.tests[-1]
    assert t.animal_id == "mouse7" and p.get_animal("mouse7") is not None and t.status == "pending"
    assert p.abs_path(t.video) == str(vid.resolve())
    page.add_blank()
    assert p.tests[-1].video == "" and len(p.tests) == 6
    new = page.create_tests_for([("C1", "Day 1", 1), ("C1", "Day 1", 2), ("A1", "Day 1", 2)], "Open field")
    assert len(new) == 2  # (C1, Day 1, 1) already exists
    page.select_ids({1})
    page.duplicate_selected()
    dup = p.tests[-1]
    assert dup.video == p.get_test(1).video and dup.status == "pending" and dup.events == []
    page.select_ids({1, 2})
    page.toggle_exclude()
    assert p.get_test(1).status == p.get_test(2).status == "excluded"
    assert page.a_excl.text() == "Include"
    page.toggle_exclude()
    assert p.get_test(1).status == "tracked"
    n = len(p.tests)
    tp = p.track_path(p.get_test(4))
    assert tp.exists()
    page.select_ids({4})
    page.delete_selected()
    assert len(p.tests) == n - 1 and p.get_test(4) is None and not tp.exists()
    assert page.proxy.rowCount() == n - 1


def test_open_on_double_click(page):
    idx = page.proxy.mapFromSource(source_index(page, 3, 0))
    page.table.doubleClicked.emit(idx)
    tv = page.main.page("TestViewPage")
    assert page.main.stack.currentWidget() is tv and tv.test.id == 3


def test_batches_and_tracking(page):
    p = page.project
    page.select_ids({1})
    page.duplicate_selected()  # same video & window as test 1 → one pass
    dup = p.tests[-1]
    for t in p.tests:
        if t.id != dup.id:
            t.status = "tracked"
    p.get_test(2).status = "pending"
    p.track_path(p.get_test(2)).unlink()
    p.get_test(2).start_s = 1.0
    batches = tracking_batches(p, [p.get_test(1), dup, p.get_test(2)])
    assert sorted(len(b) for b in batches) == [1, 2]
    # cancelling stops cleanly: nothing saved, status unchanged
    res = track_tests_job(p, [dup])(lambda f: None, lambda: True)
    assert res["cancelled"] and res["tracked"] == []
    assert dup.status == "pending" and not p.track_path(dup).exists()
    # track all untracked through the page (background worker)
    w = page.track_untracked()
    assert w is not None
    wait_tracking(page)
    assert dup.status == "tracked" and p.track_path(dup).exists()
    assert p.get_test(2).status == "tracked"
    assert abs(float(p.load_tracks(p.get_test(2))[0].meta["video_start_s"]) - 1.0) < 1e-6
    assert not page.main.dirty  # saved after tracking
    assert "0 pending" in page.summary.text()


def test_import_tracks(page, tmp_path, monkeypatch):
    p = page.project
    t = p.add_test("", "C1", stage="Day 1", trial=9)
    page.refresh()
    # mANY-MAZE track CSV
    tr = Track(t=np.arange(50) / 25, x=np.linspace(60, 300, 50), y=np.full(50, 200.0), fps=25)
    f = tmp_path / "track.csv"
    tr.to_csv(f)
    out = page.import_track_file(t, str(f))
    assert out is not None and t.status == "tracked"
    assert len(p.load_tracks(t)[0]) == 50
    # DeepLabCut CSV (three header rows)
    n = 100
    rows = ["scorer," + ",".join(["DLC"] * 9), "bodyparts," + ",".join(p for p in ("nose", "body", "tailbase")
                                                                      for _ in range(3)),
            "coords," + ",".join(["x", "y", "likelihood"] * 3)]
    for i in range(n):
        vals = []
        for dx in (10, 0, -10):
            vals += [f"{100 + i + dx}", "150", "0.99"]
        rows.append(f"{i}," + ",".join(vals))
    dlc = tmp_path / "videoDLC_resnet50.csv"
    dlc.write_text("\n".join(rows) + "\n")
    assert tests_mod.dlc_bodyparts(dlc) == ["nose", "body", "tailbase"]
    t2 = p.add_test("", "C2", stage="Day 1", trial=9)
    t2.start_s = 1.0
    t2.duration_s = 2.0

    def fake_exec(self):
        assert self.head.currentData() == "nose" and self.tail.currentData() == "tailbase"
        self.fps.setValue(25)
        return QDialog.Accepted

    monkeypatch.setattr(DlcImportDialog, "exec", fake_exec)
    out = page.import_track_file(t2, str(dlc))
    assert out is not None
    loaded = p.load_tracks(t2)[0]
    assert len(loaded) == 50  # 2 s at 25 fps, trimmed from 1 s
    assert loaded.x[0] == pytest.approx(125.0)  # body centre = mean of the three parts at frame 25
    assert loaded.hx[0] == pytest.approx(135.0)
    assert float(loaded.meta["video_start_s"]) == 1.0


def test_variables(page, monkeypatch):
    from manymaze.core.apparatus import PointOfInterest
    p = page.project
    app = p.get_apparatus(p.tests[0].apparatus)
    app.points += [PointOfInterest("Object A", 100, 100, 2), PointOfInterest("Object B", 300, 300, 2)]
    t1, t2 = p.get_test(1), p.get_test(2)
    t2.variables = {"social_side": "Left"}

    def fake_exec(self):
        assert self.side.currentData() == "__keep__"  # mixed values across the selection
        self.novel.setCurrentIndex(self.novel.findData("Object A"))
        return QDialog.Accepted

    monkeypatch.setattr(VariablesDialog, "exec", fake_exec)
    page.select_ids({1, 2})
    assert page.a_vars.isEnabled()
    page.edit_variables()
    assert t1.variables == {"novel_object": "Object A"}
    assert t2.variables == {"social_side": "Left", "novel_object": "Object A"}
    assert p.analysis_for(t2).novel_object == "Object A" and p.analysis_for(t2).social_side == "Left"
    assert page.main.dirty
    # back to the experiment default
    dlg = VariablesDialog(p, [t1])
    assert dlg.novel.currentData() == "Object A"
    dlg.novel.setCurrentIndex(0)
    dlg.apply([t1])
    assert t1.variables == {}


def test_screenshot(page):
    page.select_ids({2})
    QTest.qWait(100)
    page.main.grab().save(shot_path("shot_tests.png"))
