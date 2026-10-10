"""Users and security (File ▸ Users and security…): the experiment's users with their roles and passwords, who may
reveal the treatment coding and whether the protocol is locked; a user's own password (Set password…) and the
experiment password (File ▸ Protect experiment…). Convenience-grade ("casual") security: see core.security."""

from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import security as sec

CASUAL = ("This is convenience (“casual”) security: it keeps colleagues from changing the protocol or unblinding the "
          "experiment by mistake, not someone determined who can edit the experiment's files.")


def ask_password(parent, title: str, label: str) -> str | None:
    """A password typed in a dialog (hidden), or None when cancelled."""
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.Password)
    return text if ok else None


def _password_field() -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setMinimumWidth(240)
    return e


class PasswordDialog(QDialog):
    """A new password, typed twice, optionally after the current one (``ask_current``). An empty new password
    removes the password when ``allow_empty``."""

    def __init__(self, title: str, intro: str, ask_current: bool = False, allow_empty: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.allow_empty = allow_empty
        lay = QVBoxLayout(self)
        lbl = QLabel(intro)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        f = QFormLayout()
        self.current = _password_field() if ask_current else None
        if self.current is not None:
            f.addRow("Current password", self.current)
        self.new, self.again = _password_field(), _password_field()
        f.addRow("New password", self.new)
        f.addRow("Type it again", self.again)
        lay.addLayout(f)
        self.error = QLabel()
        self.error.setStyleSheet("color:#dc2626;")
        self.error.hide()
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.check_current = None  # (password) -> bool, given by the caller

    def _accept(self):
        msg = self.problem()
        if msg:
            self.error.setText(msg)
            self.error.show()
            return
        self.accept()

    def problem(self) -> str:
        """Why the typed passwords are not accepted ("" when they are)."""
        if self.current is not None and self.check_current is not None and not self.check_current(self.current.text()):
            return "The current password is wrong."
        if self.new.text() != self.again.text():
            return "The two new passwords differ."
        if not self.new.text() and not self.allow_empty:
            return "Type a password."
        return ""


class UsersDialog(QDialog):
    """File ▸ Users and security: the users (role, password set), their passwords and roles, and the security
    settings. Administrators manage everything; other users may only set their own password."""

    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.setWindowTitle("Users and security")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        self.state = QLabel()
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["User", "Role", "Password"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._update)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.pw_btn = QPushButton("Set password…")
        self.pw_btn.clicked.connect(self.set_password)
        self.clear_btn = QPushButton("Clear password")
        self.clear_btn.clicked.connect(self.clear_password)
        self.role_btn = QPushButton("Make administrator")
        self.role_btn.clicked.connect(self.toggle_role)
        for b in (self.pw_btn, self.clear_btn, self.role_btn):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        box = QGroupBox("Security")
        f = QFormLayout(box)
        self.reveal = QComboBox()
        for k, label in sec.REVEAL.items():
            self.reveal.addItem(label, k)
        self.reveal.setToolTip("Who may reveal the treatment coding of a blind experiment (Experiment ▸ Reveal "
                               "treatment coding, or unticking Blind testing)")
        self.reveal.currentIndexChanged.connect(self._store_security)
        self.lock = QCheckBox("Lock the protocol: only administrators can change it")
        self.lock.setToolTip("Protocol elements, apparatus and procedures are read-only for other users")
        self.lock.toggled.connect(self._store_security)
        f.addRow("Reveal treatment coding", self.reveal)
        f.addRow(self.lock)
        lay.addWidget(box)
        note = QLabel(CASUAL)
        note.setWordWrap(True)
        note.setObjectName("Hint")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._loading = False
        self.refresh()

    @property
    def project(self):
        return self.main.project

    def selected(self) -> str | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.text() if it is not None else None

    def refresh(self):
        p = self.project
        cur = self.selected()
        self._loading = True
        names = list(p.experimenters)
        self.table.setRowCount(len(names))
        for r, n in enumerate(names):
            items = (QTableWidgetItem(n), QTableWidgetItem(sec.ROLES[sec.role_of(p, n)]),
                     QTableWidgetItem("set" if sec.has_password(p, n) else "—"))
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
            if n == cur or (cur is None and n == p.current_user):
                self.table.selectRow(r)
        s = sec.security_from(p.security)
        self.reveal.setCurrentIndex(max(0, self.reveal.findData(s["reveal_codes"])))
        self.lock.setChecked(s["lock_protocol"])
        self._loading = False
        on = sec.security_on(p)
        who = p.current_user or "no user"
        if not on:
            self.state.setText("No user is an administrator yet, so nothing is restricted and nobody is asked for a "
                               "password. The first user who sets a password becomes the administrator.")
        else:
            self.state.setText(f"Administrators: {', '.join(sec.admins(p))}. You are {who}"
                               + (" (administrator)." if sec.is_admin(p) else "."))
        self._update()

    def _update(self):
        p = self.project
        name = self.selected()
        manage = sec.can(p, "manage")
        own = bool(name) and name == p.current_user
        self.pw_btn.setEnabled(bool(name) and (own or manage))
        self.clear_btn.setEnabled(bool(name) and sec.has_password(p, name) and (own or manage))
        admin = bool(name) and sec.role_of(p, name) == "admin"
        self.role_btn.setText("Make ordinary user" if admin else "Make administrator")
        self.role_btn.setEnabled(bool(name) and sec.security_on(p) and sec.is_admin(p)
                                 and (admin or sec.has_password(p, name)))
        on = sec.security_on(p) and sec.is_admin(p)
        self.reveal.setEnabled(on)
        self.lock.setEnabled(on)

    def _changed(self):
        self.main.mark_dirty()
        self.main.security_changed()
        self.refresh()

    def set_password(self, name: str | None = None, dlg: PasswordDialog | None = None) -> bool:
        """Set the password of the selected user (their own, after the current one; an administrator may set
        anyone's)."""
        p = self.project
        name = name or self.selected()
        if not name:
            return False
        own = name == p.current_user
        ask_current = own and sec.has_password(p, name)
        if dlg is None:
            dlg = PasswordDialog("Set password", f"The password of {name}. " + (
                "" if sec.security_on(p) else "As the first user with a password, you become the administrator."),
                ask_current=ask_current, allow_empty=False, parent=self)
            dlg.check_current = lambda pw: sec.verify(p, name, pw)
            if dlg.exec() != QDialog.Accepted:
                return False
        elif dlg.problem():
            return False
        sec.set_password(p, name, dlg.new.text())
        self._changed()
        return True

    def clear_password(self) -> bool:
        p = self.project
        name = self.selected()
        if not name or not sec.has_password(p, name):
            return False
        last = sec.admins(p) == [name]
        if last and QMessageBox.question(
                self, "Clear password", f"{name} is the only administrator: without the password nobody is one any "
                "more and the security settings stop applying. Clear it?") != QMessageBox.Yes:
            return False
        sec.set_password(p, name, None)
        self._changed()
        return True

    def toggle_role(self) -> bool:
        p = self.project
        name = self.selected()
        if not name:
            return False
        try:
            sec.set_role(p, name, "user" if sec.role_of(p, name) == "admin" else "admin")
        except ValueError as e:
            QMessageBox.warning(self, "Users and security", str(e))
            return False
        self._changed()
        return True

    def _store_security(self, *_):
        if self._loading:
            return
        p = self.project
        if not (sec.security_on(p) and sec.is_admin(p)):
            return
        sec.set_security(p, self.reveal.currentData(), self.lock.isChecked())
        self.main.mark_dirty()
        self.main.security_changed()


class ProtectDialog(PasswordDialog):
    """File ▸ Protect experiment: the experiment password (project.json stored encrypted). An empty new password
    removes the protection."""

    def __init__(self, project, parent=None):
        protected = project.protected
        super().__init__("Protect experiment", (
            "The experiment is protected by a password: change it, or leave the new password empty to remove the "
            "protection." if protected else
            "Protect the experiment with a password: the experiment file and its backups are stored encrypted, and "
            "the password is asked when the experiment is opened (the command line reads it from the environment "
            "variable MANYMAZE_PASSWORD). A forgotten password cannot be recovered.") + "\n\n" + CASUAL +
            " Tracks and recorded videos are not encrypted.", ask_current=protected, allow_empty=protected,
            parent=parent)
        self.check_current = lambda pw: pw == project.password
