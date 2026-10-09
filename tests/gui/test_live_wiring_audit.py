"""Audit 2026-10-08, GUI wiring of the core fixes: Run tests tells the Test schedule when it removes a test and which
tests are live, procedures are checked (and their programs allowed) before a test is armed, the procedure editor
shows warnings apart from errors, the Data page's treatment filter while testing blind, and the print dialog."""

import shutil
import sys

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from manymaze.core import procedures as pr
from manymaze.core import workflow as wf
from manymaze.core.demo import create_demo_project
from manymaze.core.procedures import programs
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def proc(*stmts, name="P"):
    return {"name": name, "enabled": True, "statements": list(stmts)}


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=4)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    # a fresh list of allowed programs, never the user's
    monkeypatch.setattr(programs, "policy", programs.ProgramPolicy(path=tmp_path / "allowed.json"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    w.show()
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def single_ready(win):
    """The Run tests page set up for a new test of animal C1 (preview stubbed, source "open")."""
    p = win.project
    page = win.goto("LivePage")
    page.set_simulation_file(p.abs_path(p.tests[0].video))
    page.start_mode.setCurrentIndex(page.start_mode.findData("manual"))
    page.test_combo.setCurrentIndex(0)  # a new test
    page.animal.setCurrentText("C1")
    page.record.setChecked(False)
    page.start_preview = lambda: True
    page._on_opened(400, 400, 25.0)
    return page


def notices():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and not w.isModal()
            and w.isVisible()]


# ------------------------------------------------------------------ the Test schedule follows Run tests
def test_a_test_removed_by_run_tests_leaves_the_test_schedule(win, monkeypatch):
    import manymaze.gui.pages.live.single as single

    p = win.project
    sched = win.goto("TestsPage")
    n = len(p.tests)
    page = single_ready(win)
    monkeypatch.setattr(single, "confirm_animal_id", lambda *a: False)  # the animal's ID is not confirmed
    changed = []
    win.tests_changed.connect(lambda: changed.append(1))
    assert not page.arm()
    assert len(p.tests) == n and changed
    assert sched.model.rowCount() == n and all(t in p.tests for t in sched.model.tests)

    # a test armed then discarded: its new test goes, and the schedule's rows with it
    monkeypatch.setattr(single, "confirm_animal_id", lambda *a: True)
    assert page.arm() and len(p.tests) == n + 1
    changed.clear()
    page.stop_test(save=False)
    assert len(p.tests) == n and changed and sched.model.rowCount() == n


def test_only_the_live_tests_are_refused_on_the_test_schedule(win):
    p = win.project
    sched = win.goto("TestsPage")
    page = single_ready(win)
    assert page.arm() and page.session is not None
    t_live, t_other = page.test, p.tests[0]
    assert t_live is not t_other
    assert page.active_test_ids() == {t_live.id}
    why = sched.busy_tests([t_live])
    assert f"Test {t_live.id}" in why and "Run tests" in why
    assert sched.busy_tests([t_other]) == ""  # other tests may be deleted while one runs
    sched.select_ids({t_other.id})
    sched.delete_selected(confirm=False)
    assert t_other not in p.tests and t_live in p.tests
    page.stop_test(save=False)
    assert page.active_test_ids() == set()


def test_active_test_ids_of_several_tests(win):
    p = win.project
    page = win.goto("LivePage")
    page.set_mode("multi")
    page.record.setChecked(False)
    page.add_source(p.abs_path(p.tests[0].video))
    e = page.add_session_row()
    assert page.arm_all() == 1
    tid = e.meta.get("test_id")
    assert tid is not None and page.active_test_ids() == {tid}  # armed (or being armed)
    page.stop_all_clicked()
    page._save_finished_entries()
    assert tid not in page.active_test_ids()


# ------------------------------------------------------------------ procedures checked before arming
def test_procedures_with_errors_are_not_armed(win, monkeypatch):
    p = win.project
    p.procedures = [proc(DO("tone", duration=-1), name="Bad")]
    told = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: told.append(a[2]) or QMessageBox.Ok)
    page = single_ready(win)
    n = len(p.tests)
    assert not page.arm()
    assert page.session is None and len(p.tests) == n
    assert told and "'Bad', statement 1" in told[0] and "must not be negative" in told[0]

    # several tests: refused once, and a scheduled start opens no modal dialog
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    e = page.add_session_row()
    told.clear()
    assert page.arm_all() == 0 and e.session is None and len(told) == 1
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail("modal dialog at a scheduled start"))
    assert page.arm_all([e], interactive=False) == 0
    assert any("must not be negative" in b.text() for b in notices())


def test_run_program_is_allowed_once_before_arming(win, monkeypatch):
    p = win.project
    p.procedures = [proc(DO("run_program", program=sys.executable, arguments='-c "pass"'))]
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    page = single_ready(win)
    assert not page.arm() and page.session is None
    assert len(asked) == 1 and sys.executable in asked[0] and "Allow them?" in asked[0]
    assert not programs.policy.is_allowed(sys.executable)
    assert programs.policy.confirm is None  # never asked from the frame thread

    # several tests, scheduled: nobody is asked, a notice says why
    page.set_mode("multi")
    page.add_source(p.abs_path(p.tests[0].video))
    e = page.add_session_row()
    asked.clear()
    assert page.arm_all([e], interactive=False) == 0 and not asked
    assert any(sys.executable in b.text() for b in notices())
    page.set_mode("single")

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    assert page.arm() and page.session is not None
    assert programs.policy.is_allowed(sys.executable)
    page.stop_test(save=False)
    asked.clear()
    assert page.arm() and not asked  # allowed: not asked again
    page.stop_test(save=False)


def test_allowed_programs_dialog_lists_and_removes(win):
    programs.policy.allow(sys.executable)
    dlg = win.allowed_programs_dialog(modal=False)
    lst = dlg.program_list
    assert [lst.item(i).text() for i in range(lst.count())] == sorted(programs.policy.allowed())
    lst.item(0).setSelected(True)
    dlg.remove_selected()
    assert lst.count() == 0 and not programs.policy.is_allowed(sys.executable)
    dlg.deleteLater()


# ------------------------------------------------------------------ procedure editor: warnings apart from errors
def test_procedure_editor_shows_warnings_apart_from_errors(win):
    from manymaze.gui.procedure_editor import ERROR_COLOUR, WARNING_COLOUR, ProcedureEditor

    p = win.project
    ed = ProcedureEditor(p)
    ed.procs[:] = [proc(DO("run_program", program="ls"))]
    ed.validate()
    texts = [ed.issue_list.item(i).text() for i in range(ed.issue_list.count())]
    assert texts[0].startswith("✓ No problems found") and "1 warning" in texts[0]
    warn = ed.issue_list.item(1)
    assert "warning" in warn.text().lower()
    assert warn.foreground().color().name() == WARNING_COLOUR
    ed.procs[:] = [proc(DO("tone", duration=-1), DO("run_program", program="ls"))]
    ed.validate()
    texts = [ed.issue_list.item(i).text() for i in range(ed.issue_list.count())]
    assert not any("No problems" in t for t in texts)
    assert ed.issue_list.item(0).foreground().color().name() == ERROR_COLOUR  # errors first
    assert ed.issue_list.item(1).foreground().color().name() == WARNING_COLOUR
    ed.deleteLater()


# ------------------------------------------------------------------ Data page: the treatment filter while blind
def test_results_treatment_filter_shows_codes_while_blind(win):
    p = win.project
    page = win.goto("ResultsPage")
    page._fill_filter_combos()
    names = [g.name for g in p.groups]
    items = [page.group_combo.itemText(i) for i in range(1, page.group_combo.count())]
    assert set(names) <= set(items)
    p.blind = True
    codes = wf.blind_codes(p)
    page._fill_filter_combos()
    items = [page.group_combo.itemText(i) for i in range(1, page.group_combo.count())]
    assert not set(names) & set(items)
    assert set(items) == {codes[n] for n in names}
    assert p.test_info(p.tests[0])["Group"] in items  # the filter matches the rows' Group column


# ------------------------------------------------------------------ the print dialog is deleted after use
def test_print_dialog_is_deleted_after_use(win, monkeypatch):
    from PySide6.QtPrintSupport import QPrintDialog

    page = win.goto("ResultsPage")
    page.rows = [{"Test": 1}]
    monkeypatch.setattr(QPrintDialog, "exec", lambda self: QDialog.Rejected)
    assert page.print_table() is None
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not page.findChildren(QPrintDialog)
