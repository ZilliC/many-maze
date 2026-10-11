"""Users and security in the GUI: choosing a user with a password, the administrator-only reveal of the treatment
coding, the locked protocol, the Users and security dialog and File ▸ Protect experiment."""

import json
import shutil

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import security as sec
from manymaze.core.demo import create_demo_project
from manymaze.core.project import PROJECT_FILE, Project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.security_dialog import PasswordDialog, ProtectDialog, UsersDialog

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    p = Project.load(d)
    p.experimenters = ["Ann", "Bob"]
    sec.set_password(p, "Ann", "ann-pw")  # the administrator
    sec.set_password(p, "Bob", "bob-pw")
    p.save()
    w = MainWindow()
    w.settings.setValue("current_user", "")
    w.set_project(Project.load(d))
    w.answers = []
    w.ask_password = lambda title, label: w.answers.pop(0) if w.answers else None
    yield w
    w.settings.setValue("current_user", "")


def test_choosing_a_user_with_a_password_asks_for_it(win):
    p = win.project
    win.answers = ["wrong", "bob-pw"]
    assert win.sign_in("Bob") and p.current_user == "Bob" and win.current_user() == "Bob"
    win.answers = ["nope", "nope", "nope"]
    assert not win.sign_in("Ann") and p.current_user == "Bob"  # three wrong tries: unchanged
    win.answers = []
    assert not win.sign_in("Ann") and p.current_user == "Bob"  # cancelled
    win.answers = ["ann-pw"]
    assert win.sign_in("Ann") and win.user_btn.text() == "User: Ann (administrator)"
    win._fill_user_menu()
    texts = [a.text() for a in win.user_menu.actions()]
    assert "Ann (password)" in texts and "Set password…" in texts and "Users and security…" in texts
    # the user remembered on this computer is asked for the password when the experiment is opened again
    win.dirty = False
    q = Project.load(p.path)
    win._signed_in = None
    win.answers = ["bad"]
    win.set_project(q)
    assert q.current_user == "" and win.current_user() == ""
    win.set_current_user("Ann")
    win._signed_in = None
    win.answers = ["ann-pw"]
    q2 = Project.load(p.path)
    win.dirty = False
    win.set_project(q2)
    assert q2.current_user == "Ann"


def test_only_administrators_reveal_the_treatment_coding(win):
    p = win.project
    sec.set_security(p, reveal_codes="admin")
    page = win.goto("AnimalsPage")
    page.set_blind(True)
    win.answers = ["bob-pw"]
    win.sign_in("Bob")
    assert p.blind and not page.a_reveal.isEnabled()
    page._reveal_toggled(True)
    assert p.blind  # refused
    proto = win.goto("ExperimentPage")
    proto.blind.setChecked(False)
    assert p.blind and proto.blind.isChecked()  # unticking Blind testing is revealing too
    win.answers = ["ann-pw"]
    win.sign_in("Ann")
    page = win.goto("AnimalsPage")
    assert page.a_reveal.isEnabled()
    page._reveal_toggled(True)
    assert not p.blind
    # anyone may reveal by default
    sec.set_security(p, reveal_codes="anyone")
    page.set_blind(True)
    win.answers = ["bob-pw"]
    win.sign_in("Bob")
    assert page.a_reveal.isEnabled()


def test_locked_protocol_is_read_only_for_other_users(win):
    p = win.project
    sec.set_security(p, lock_protocol=True)
    win.answers = ["bob-pw"]
    win.sign_in("Bob")
    proto = win.goto("ExperimentPage")
    assert proto.locked and proto.lock_lbl.isVisibleTo(proto)
    assert not proto.elements["protocol"].widget().isEnabled() and not proto.add_item_act.isEnabled()
    appar = win.goto("ApparatusPage")
    assert appar.locked and not appar.new_act.isEnabled() and not appar.tool_actions["polygon"].isEnabled()
    assert appar.tool_actions["select"].isEnabled() and not appar.view.isInteractive()
    live = win.goto("LivePage")
    assert not live.proc_editor.isEnabled() and not live.rec_names_btn.isEnabled()
    live.set_recording_name_fields(["animal"])  # the file names are part of the protocol
    assert p.recording_name_fields != ["animal"]
    win.answers = ["ann-pw"]
    win.sign_in("Ann")  # the administrator: everything editable again (the pages follow at once)
    assert not proto.locked and proto.elements["protocol"].widget().isEnabled() and proto.add_item_act.isEnabled()
    assert appar.view.isInteractive() and live.proc_editor.isEnabled() and live.rec_names_btn.isEnabled()
    live.set_recording_name_fields(["animal"])
    assert p.recording_name_fields == ["animal"]


def test_users_dialog(win):
    p = win.project
    p.users.clear()  # nobody has a password yet
    p.experimenters = ["Ann", "Bob"]
    win.set_current_user("Bob")
    dlg = win.users_dialog(modal=False)
    assert "No user is an administrator" in dlg.state.text() and not dlg.lock.isEnabled()
    pw = PasswordDialog("Set password", "", allow_empty=False)
    pw.new.setText("b")
    pw.again.setText("x")
    assert not dlg.set_password("Bob", pw)  # the two passwords differ
    pw.again.setText("b")
    assert dlg.set_password("Bob", pw)
    assert sec.admins(p) == ["Bob"] and win.dirty and "administrator" in win.user_btn.text()
    assert dlg.lock.isEnabled()
    dlg.lock.setChecked(True)
    dlg.reveal.setCurrentIndex(dlg.reveal.findData("admin"))
    assert p.security == {"reveal_codes": "admin", "lock_protocol": True}
    pw2 = PasswordDialog("Set password", "", allow_empty=False)
    pw2.new.setText("a")
    pw2.again.setText("a")
    assert dlg.set_password("Ann", pw2) and sec.role_of(p, "Ann") == "user"
    dlg.table.selectRow(p.experimenters.index("Ann"))
    assert dlg.role_btn.isEnabled() and dlg.toggle_role() and sorted(sec.admins(p)) == ["Ann", "Bob"]
    dlg.table.selectRow(p.experimenters.index("Bob"))
    assert dlg.clear_password() and sec.admins(p) == ["Ann"] and not sec.has_password(p, "Bob")
    assert not dlg.lock.isEnabled()  # Bob is no longer an administrator
    dlg.close()


def test_protect_experiment(win, monkeypatch):
    p = win.project
    win.answers = ["ann-pw"]
    win.sign_in("Ann")
    dlg = ProtectDialog(p, win)
    dlg.new.setText("exp-pw")
    dlg.again.setText("exp-pw")
    assert win.protect_experiment(dlg)
    raw = json.loads((p.path / PROJECT_FILE).read_text())
    assert sec.is_encrypted(raw) and p.protected and not win.dirty
    # opening asks for the password (cancel: nothing opened)
    path = str(p.path)
    win.set_project(None)
    win.answers = []
    win.load_project(path, confirmed=True)
    assert win.project is None
    win.answers = ["wrong", "exp-pw", "ann-pw"]  # the experiment password (twice), then Ann's
    win.load_project(path, confirmed=True)
    assert win.project is not None and win.project.protected and win.project.name == p.name
    # changing it needs the current password; an empty new password removes the protection
    q = win.project
    dlg = ProtectDialog(q, win)
    dlg.current.setText("nope")
    assert not win.protect_experiment(dlg)
    dlg.current.setText("exp-pw")
    assert win.protect_experiment(dlg) and not q.protected
    assert json.loads((q.path / PROJECT_FILE).read_text())["name"] == q.name
    # only an administrator may change it
    win.answers = ["bob-pw"]
    win.sign_in("Bob")
    assert not win.protect_experiment(ProtectDialog(q, win))
