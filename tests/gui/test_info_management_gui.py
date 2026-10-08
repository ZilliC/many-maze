"""GUI for the new information columns (column chooser, segment of test, statistics factors) and experiment
management: animal notes, the current user and test experimenters, ending a stage for one animal."""

import shutil

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from manymaze.core import workflow as wf
from manymaze.core.demo import create_demo_project
from manymaze.core.project import OPTIONAL_INFO_COLUMNS, Project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.tests import C_NOTES, C_USER

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.settings.setValue("current_user", "")
    w.set_project(Project.load(d))
    yield w
    w.settings.setValue("current_user", "")


def source_index(page, test_id, col):
    row = next(i for i, t in enumerate(page.model.tests) if t.id == test_id)
    return page.model.index(row, col)


# ---------------------------------------------------------------- results
def test_optional_info_columns_start_hidden(win):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    cols = page.shown_columns()
    assert "Source video file" not in cols and "Segment of test" not in cols and "Period" not in cols
    # tick an optional column in the chooser: it is shown
    page.hidden.discard("Frames tracked (%)")
    page._update_columns()
    assert "Frames tracked (%)" in page.shown_columns()
    assert page.rows[0]["Frames tracked (%)"] > 0
    page.clear_settings()
    assert set(OPTIONAL_INFO_COLUMNS) <= page.hidden and "Frames tracked (%)" not in page.shown_columns()
    # the information branch of the chooser lists them
    top = next(page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount())
               if page.tree.topLevelItem(i).text(0) == "Information")
    names = set()
    stack = [top]
    while stack:
        it = stack.pop()
        names.add(it.data(0, Qt.UserRole))
        stack += [it.child(i) for i in range(it.childCount())]
    assert {"Animal lighter / darker", "Source video file", "Treatment code"} <= names


def test_segment_of_test_column(win):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    win.project.analysis.bin_length_s = 2.0
    page.hidden.discard("Segment of test")
    page.seg_check.setChecked(True)
    page.wait_loaded()
    assert "Segment of test" in page.shown_columns() and "Period" in page.shown_columns()
    segs = {r["Period"]: r["Segment of test"] for r in page.rows if r["Test"] == page.rows[0]["Test"]}
    numbers = sorted(v for k, v in segs.items() if k != "Whole test")
    assert segs["Whole test"] == "" and numbers == [1, 2, 3]
    page.seg_check.setChecked(False)
    page.wait_loaded()
    assert "Segment of test" not in page.shown_columns()


def test_user_column_and_statistics_factor(win):
    p = win.project
    for i, t in enumerate(p.tests):
        t.experimenter = "Alice" if i % 2 else "Bob"
    page = win.goto("ResultsPage")
    page.reload(force=True)
    page.wait_loaded()
    assert "User" in page.shown_columns()
    assert {r["User"] for r in page.rows} == {"Alice", "Bob"}
    stats = win.goto("StatisticsPage")
    stats.loader.wait()
    for _ in range(20):
        app.processEvents()
    assert stats.rows and "User" in stats.factors()


def test_wide_export_keeps_shown_animal_info(win):
    win.project.animals[0].notes = "nervous"
    page = win.goto("ResultsPage")
    page.reload(force=True)
    page.wait_loaded()
    rows, cols = page.wide_rows()
    assert "Animal notes" not in cols
    page.hidden.discard("Animal notes")
    page._update_columns()
    rows, cols = page.wide_rows()
    assert "Animal notes" in cols
    assert any(r.get("Animal notes") == "nervous" for r in rows)


# ---------------------------------------------------------------- animals
def test_animal_notes_column(win):
    page = win.goto("AnimalsPage")
    page.refresh()
    c = page.col_of("notes")
    assert c >= 0 and page.table.horizontalHeaderItem(c).text() == "Notes"
    a = page.table.item(0, 0).data(Qt.UserRole)
    page.table.item(0, c).setText("limps on the left hind paw")
    assert a.notes == "limps on the left hind paw" and win.dirty
    page.refresh()
    assert page.table.item(0, c).text() == "limps on the left hind paw"


# ---------------------------------------------------------------- users
def test_current_user(win, monkeypatch):
    p = win.project
    assert win.user_btn.text() == "No user"
    win.set_current_user("Alice")
    assert p.current_user == "Alice" and "Alice" in p.experimenters
    assert win.settings.value("current_user") == "Alice" and win.user_btn.text() == "User: Alice"
    win._fill_user_menu()
    texts = [a.text() for a in win.user_menu.actions()]
    assert "Alice" in texts and "No user" in texts and "New user…" in texts
    # remembered for the next experiment opened
    q = Project.load(p.path)
    win.dirty = False
    win.set_project(q)
    assert q.current_user == "Alice" and "Alice" in q.experimenters
    # choosing a new user through the dialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Bob", True))
    assert win.choose_user(new=True) == "Bob" and q.current_user == "Bob"
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Bob", True))
    win._remove_user_dialog()
    assert "Bob" not in q.experimenters and win.current_user() == "" and q.current_user == ""


def test_tracking_stamps_user(win):
    p = win.project
    win.set_current_user("Carol")
    t = p.tests[0]
    t.experimenter = ""
    p.save_tracks(t, p.load_tracks(t))
    assert t.experimenter == "Carol"


def test_schedule_user_column(win, monkeypatch):
    page = win.goto("TestsPage")
    t = win.project.tests[0]
    assert page.model.headerData(C_USER, Qt.Horizontal) == "User" and C_NOTES == C_USER + 1
    assert page.model.setData(source_index(page, t.id, C_USER), "Dana")
    assert t.experimenter == "Dana" and "Dana" in win.project.experimenters
    assert page.model.data(source_index(page, t.id, C_USER)) == "Dana"
    page.select_ids({x.id for x in win.project.tests})
    assert page.set_user_selected("Eve") == len(win.project.tests)
    assert all(x.experimenter == "Eve" for x in win.project.tests)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", True))
    page.select_ids({t.id})
    assert page.set_user_selected() == 1 and t.experimenter == ""
    headers, rows = page.schedule_rows()
    assert "User" in headers


# ---------------------------------------------------------------- ending a stage
def test_end_stage_for_animal(win):
    p = win.project
    page = win.goto("TestsPage")
    aid = p.tests[0].animal_id
    stage = p.tests[0].stage
    extra = page.add_tests([{"animal_id": aid, "stage": stage, "trial": k} for k in (2, 3)])
    page.select_ids({extra[0].id})
    assert page.a_end_stage.isEnabled() and not page.a_reopen_stage.isEnabled()
    assert page.end_stage_selected() == 2
    assert all(t.status == "skipped" for t in extra) and wf.stage_ended(p, aid, stage)
    assert p.tests[0].status == "tracked"
    page.select_ids({extra[0].id})
    assert not page.a_end_stage.isEnabled() and page.a_reopen_stage.isEnabled()
    tip = page.model.data(source_index(page, extra[0].id, 7), Qt.ToolTipRole)
    assert "was ended" in tip
    assert page.reopen_stage_selected() == 2
    assert all(t.status == "pending" for t in extra) and not wf.stage_ended(p, aid, stage)
