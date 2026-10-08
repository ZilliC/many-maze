"""Keys: scoring (keyboard and on-screen pad, undo), start / stop keys and USB presenters / remotes."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QWidget

from ....core import workflow as wf
from ...widgets import fmt_time
from .common import parse_keys, qt_key


class KeysMixin:
    """Keys: scoring (keyboard and on-screen pad, undo), start / stop keys and USB presenters / remotes."""

    def _update_keys_label(self):
        p = self.project
        bs = [b for b in (p.behaviours if p else []) if b.key]
        parts = []
        sk, tk = parse_keys(self.start_keys.text()), parse_keys(self.stop_keys.text())
        if sk:
            parts.append("Start: " + ", ".join(f"<b>{k}</b>" for k in sk))
        if tk:
            parts.append("Stop: " + ", ".join(f"<b>{k}</b>" for k in tk))
        if bs:
            parts.append("Scoring: " + ", ".join(f"<b>{b.key}</b> {b.name}" for b in bs))
        else:
            parts.append("Define behaviours with keys on the Experiment page to score live.")
        txt = " · ".join(parts)
        self.keys_lbl.setText(txt)
        self.obs_panel.keys.setText(txt)

    def _enable_shortcuts(self, on: bool):
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        for w in self.setup_widgets:
            w.setEnabled(not on)
        app = QApplication.instance()
        if on and not self._key_filter:
            app.installEventFilter(self)
            self._key_filter = True
        elif not on and self._key_filter:
            app.removeEventFilter(self)
            self._key_filter = False
        if not on or self.project is None:
            return
        # scoring keys go through an event filter (press and release: "hold" behaviours, no auto-repeat)
        taken = {qt_key(b.key).lower() for b in self.project.behaviours if b.key}
        for keys, fn in ((parse_keys(self.start_keys.text()), self.start_key),
                         (parse_keys(self.stop_keys.text()), self.stop_key)):
            for k in keys:
                q = qt_key(k)
                if q.lower() in taken or QKeySequence(q).isEmpty():
                    continue
                taken.add(q.lower())
                sc = QShortcut(QKeySequence(q), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(fn)
                self._shortcuts.append(sc)

    def start_key(self) -> bool:
        """Start key (keyboard / USB presenter): start the waiting test(s) or resume paused ones."""
        if self.mode == "observe" and self.obs is not None:
            return self.obs_start()
        if self.mode == "multi":
            if any(e.state in ("waiting", "paused") or getattr(e.session, "waiting_end", False)
                   for e in self.group.entries):
                self.group.start_all()
                self._log("Start key: tests started.")
                return True
            return False
        s = self.session
        if s is None:
            return False
        if s.waiting_end and s.continue_test():  # "waiting for test end": the start key continues the test
            self._log(f"Start key: test continued at {fmt_time(s.elapsed)}.")
            self._update_buttons()
            return True
        if s.state == "waiting":
            s.request_start()
            self._log("Start key: test started.")
            return True
        if s.state == "paused":
            return self.toggle_pause()
        return False

    def stop_key(self) -> bool:
        """Stop key: stop and save the running test(s)."""
        if self.mode == "observe" and self.obs is not None:
            self.obs_stop(save=True)
            return True
        if self.mode == "multi":
            if any(e.state in ("running", "paused") for e in self.group.entries):
                self.group.stop_all(save=True)
                self._save_finished_entries()
                return True
            return False
        if self.session is not None and self.session.state in ("running", "paused"):
            self.stop_test(save=True)
            return True
        return False

    def _scoring_target(self):
        if self.mode == "observe":
            return self.obs
        if self.mode == "multi":
            e = self._selected_entry()
            if e is None or e.state != "running":
                e = next((x for x in self.group.entries if x.state == "running"), None)
            return e.session if e is not None else None
        return self.session

    def score_key(self, key: str, down: bool = True) -> bool:
        """A scoring key was pressed (down) or released: point events, state toggles, hold behaviours (scored
        while the key is down) and exclusive sets (starting one behaviour stops its partners)."""
        if self.project is None:
            return False
        b = next((b for b in self.project.behaviours if b.key and b.key.lower() == key.lower()), None)
        return b is not None and self._score(b, down)

    def _pad(self, name: str, down: bool):
        b = next((b for b in (self.project.behaviours if self.project else []) if b.name == name), None)
        if b is not None:
            self._score(b, down)
        if self.obs is not None:
            self.obs_panel.show_session(self.obs, self.obs.duration_s)

    def _score(self, b, down: bool) -> bool:
        s = self._scoring_target()
        if s is None or s.state != "running":
            return False
        e = self._entry_of(s)
        done = []  # what this key press did, for Undo: (kind, event, behaviour)

        def score(name: str, kind: str) -> bool:
            """kind: "point", "start" or "end". False when the test ended meanwhile (nothing scored)."""
            ev = s.score(name, "point" if kind == "point" else "state")
            if ev is None:
                return False
            what = {"point": "", "start": " starts", "end": " ends"}[kind]
            self._log(f"{fmt_time(ev['t_end'] if kind == 'end' else ev['t'])}  {name}{what}", e)
            done.append((kind, ev, name))
            return True

        if not down:
            if b.kind != "hold" or b.name not in s.open_states:
                return False
            score(b.name, "end")
        else:
            s.key(b.key)
            if b.kind == "point":
                score(b.name, "point")
            elif b.name in s.open_states:
                if b.kind == "hold":
                    return True
                score(b.name, "end")
            elif all(score(o.name, "end") for o in wf.exclusive_partners(self.project.behaviours, b)
                     if o.name in s.open_states):
                score(b.name, "start")
        self._push_undo(s, done)
        if s is self.session:
            self.vals["events"].setText(str(len(s.events)))
        return bool(done)

    def _entry_of(self, session):
        if session is None or session is self.session or session is self.obs:
            return None
        return next((x for x in self.group.entries if x.session is session), None)

    def _push_undo(self, session, done):
        if done:
            self._undo.append((session, done))
            del self._undo[:-200]
            self._update_undo_buttons()

    def _can_undo(self, session) -> bool:
        return session is not None and session.state in ("running", "paused") and \
            any(s is session for s, _ in self._undo)

    def undo_last_event(self, session=None) -> bool:
        """Panel toolbar ↶: take back the last scoring key press of this test (a point event, the start or the end
        of a behaviour)."""
        session = session if session is not None else self._scoring_target()
        i = next((i for i in range(len(self._undo) - 1, -1, -1) if self._undo[i][0] is session), None)
        if session is None or i is None or session.state not in ("running", "paused"):
            return False
        _, done = self._undo.pop(i)
        with session.lock:
            for kind, ev, name in reversed(done):
                if kind in ("point", "start"):
                    session.events[:] = [x for x in session.events if x is not ev]
                    if session.open_states.get(name) is ev:
                        session.open_states.pop(name)
                else:
                    ev["t_end"] = None
                    session.open_states[name] = ev
        self._log("Undo: " + ", ".join(f"{name} {'ends' if k == 'end' else 'starts' if k == 'start' else ''}".strip()
                                       for k, _, name in done), self._entry_of(session))
        if session is self.session:
            self.vals["events"].setText(str(len(session.events)))
        if session is self.obs:
            self.obs_panel.show_session(session, session.duration_s)
        self._update_undo_buttons()
        return True

    def _update_undo_buttons(self):
        self.single_panel.undo_btn.setEnabled(self._can_undo(self.session))
        self.obs_panel.undo_btn.setEnabled(self._can_undo(self.obs))
        for e in self._entries():
            p = self._panels.get(e.id)
            if p is not None:
                p.undo_btn.setEnabled(self._can_undo(e.session))

    def eventFilter(self, obj, e):
        t = e.type()
        if self._key_filter and t in (QEvent.KeyPress, QEvent.KeyRelease) and isinstance(obj, QWidget) \
                and obj.window() is self.window() and self.project is not None \
                and not e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            fw = QApplication.focusWidget()
            editing = (isinstance(fw, QLineEdit) and not fw.isReadOnly()) or isinstance(fw, QAbstractSpinBox) \
                or (isinstance(fw, QComboBox) and fw.isEditable())
            text = e.text().strip()
            b = next((b for b in self.project.behaviours if b.key and text and b.key.lower() == text.lower()),
                     None) if not editing else None
            if b is not None:
                if not e.isAutoRepeat():
                    self._score(b, t == QEvent.KeyPress)
                return True
        return super().eventFilter(obj, e)
