"""Test view scoring: behaviour keys / pad, scored events, notes and the TakeNote observation clock."""

from __future__ import annotations

import datetime as _dt
import html
import time

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget, QVBoxLayout, QWidget)

from ....core import workflow as wf
from ...confirm_id import confirm_animal_id
from ...scoring_pad import ScoringPad
from ...widgets import fmt_time
from .common import _num_item, _ro_item


class ObservationClock:
    """Start / pause / stop clock for scoring by direct observation (no video)."""

    def __init__(self, time_fn=time.monotonic):
        self.time_fn = time_fn
        self.state = "stopped"  # stopped | running | paused
        self._acc = 0.0
        self._t0 = 0.0

    def elapsed(self) -> float:
        return self._acc + (self.time_fn() - self._t0 if self.state == "running" else 0.0)

    def start(self):
        if self.state == "stopped":
            self._acc = 0.0
        if self.state != "running":
            self._t0 = self.time_fn()
            self.state = "running"

    def pause(self):
        if self.state == "running":
            self._acc = self.elapsed()
            self.state = "paused"

    def stop(self, at: float | None = None) -> float:
        """Stop the clock (at a given elapsed time, e.g. the end of the test). Returns the elapsed time."""
        self._acc = self.elapsed() if at is None else float(at)
        self.state = "stopped"
        return self._acc

    def reset(self):
        self.state, self._acc = "stopped", 0.0


class ScoringMixin:
    """The Scoring tab of TestViewPage: manual scoring with keys or buttons, with or without video."""

    def _build_scoring_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel("Score with the keys (click on the video first) or the buttons. Toggle behaviours switch on/off, "
                      "hold behaviours last while the key or button is held, point behaviours are logged at the "
                      "current time. Space = play/pause, ←/→ = step (Shift: 1 s).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#475569;")
        lay.addWidget(hint)

        self.clock_box = QGroupBox("TakeNote observation clock (no video: score by direct observation)")
        cl = QHBoxLayout(self.clock_box)
        self.clock_lbl = QLabel("00:00.0")
        self.clock_lbl.setStyleSheet("font-size:22px;font-weight:bold;font-family:monospace;")
        cl.addWidget(self.clock_lbl)
        cl.addStretch()
        for a in (self.clock_start_btn, self.clock_pause_btn, self.clock_stop_btn):
            cl.addWidget(self._tool(a))
        self.clock_box.hide()
        lay.addWidget(self.clock_box)

        self.pad = ScoringPad()
        self.pad.pressed.connect(self._pad_pressed)
        self.pad.released.connect(self._pad_released)
        lay.addWidget(self.pad)
        self.active_lbl = QLabel()
        self.active_lbl.setWordWrap(True)
        lay.addWidget(self.active_lbl)
        self.events_table = QTableWidget(0, 4)
        self.events_table.setHorizontalHeaderLabels(["Behaviour", "Start (s)", "End (s)", "Duration (s)"])
        self.events_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.events_table.verticalHeader().hide()
        self.events_table.verticalHeader().setDefaultSectionSize(26)
        self.events_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.events_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events_table.cellClicked.connect(self._event_clicked)
        lay.addWidget(self.events_table, 1)
        row = QHBoxLayout()
        dele = QPushButton("Delete selected")
        dele.clicked.connect(self.delete_selected_events)
        clr = QPushButton("Clear all")
        clr.clicked.connect(self.clear_events)
        self.events_lbl = QLabel()
        row.addWidget(self.events_lbl, 1)
        row.addWidget(dele)
        row.addWidget(clr)
        lay.addLayout(row)
        lay.addWidget(QLabel("Notes"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Notes about this test (exported with the results)")
        self.notes_edit.setMaximumHeight(60)
        self.notes_edit.textChanged.connect(self._notes_changed)
        lay.addWidget(self.notes_edit)
        return w

    def _fill_behaviours(self):
        p = self.project
        self.pad.set_behaviours(p.behaviours if p is not None else [])
        self._update_active()

    def behaviour_for_key(self, text: str):
        text = text.lower()
        if not text or self.project is None:
            return None
        return next((b for b in self.project.behaviours if b.key and b.key.lower() == text), None)

    def behaviour_named(self, name: str):
        return next((b for b in self.project.behaviours if b.name == name), None) if self.project else None

    def scoring_ready(self) -> bool:
        """Whether key / button scoring may happen now (asks for the animal ID first when the experiment requires
        it; without a video the observation clock must be running)."""
        t, p = self.test, self.project
        if t is None or p is None:
            return False
        if self.player.source is None and self.clock.state != "running":
            self.main.status("No video: start the observation clock (Scoring tab) to score by direct observation.")
            return False
        return self.confirm_animal()

    def confirm_animal(self) -> bool:
        t = self.test
        if t is None or t.id in self._confirmed or not wf.confirm_id_enabled(self.project):
            return True
        self.player.pause()
        if not confirm_animal_id(self, t, self.project):
            self.main.status("Animal not confirmed — scoring blocked.")
            return False
        self._confirmed.add(t.id)
        return True

    def score(self, b, t: float | None = None):
        """Toggle a state / hold behaviour or log a point behaviour at test time t (default: now)."""
        if self.test is None:
            return
        t = round(self.test_time() if t is None else t, 3)
        if b.kind == "point":
            self.test.events.append({"behaviour": b.name, "t": t, "t_end": None})
            self.main.status(f"{b.name} at {t:.2f} s")
            self._scoring_changed()
        elif b.name in self._open_states:
            self.stop_behaviour(b, t)
        else:
            self.start_behaviour(b, t)

    def start_behaviour(self, b, t: float | None = None):
        """Start a state / hold behaviour (stopping the others of its exclusive set)."""
        if self.test is None or b.name in self._open_states:
            return
        t = round(self.test_time() if t is None else t, 3)
        for o in wf.exclusive_partners(self.project.behaviours, b):
            if o.name in self._open_states:
                self._end_state(o.name, t)
        self._open_states[b.name] = t
        self.main.status(f"{b.name} on at {t:.2f} s")
        self._scoring_changed()

    def stop_behaviour(self, b, t: float | None = None):
        if self.test is None or b.name not in self._open_states:
            return
        t = round(self.test_time() if t is None else t, 3)
        self._end_state(b.name, t)
        self.main.status(f"{b.name} off at {t:.2f} s")
        self._scoring_changed()

    def _end_state(self, name, t):
        t0 = self._open_states.pop(name)
        a, z = sorted((t0, t))
        if z > a:
            self.test.events.append({"behaviour": name, "t": a, "t_end": z})

    def _scoring_changed(self):
        self.test.events.sort(key=lambda e: e["t"])
        user = self.project.current_user if self.project is not None else ""
        if user and self.test.events and not self.test.experimenter:  # the user who scored it
            self.test.experimenter = user
        self.main.mark_dirty()
        self._events_changed()

    def press_behaviour(self, b) -> bool:
        """Key / button pressed: start (hold), toggle (state) or log (point)."""
        if not self.scoring_ready():
            return False
        if b.kind == "hold":
            self.start_behaviour(b)
        else:
            self.score(b)
        return True

    def release_behaviour(self, b) -> bool:
        if b.kind != "hold" or b.name not in self._open_states:
            return False
        self.stop_behaviour(b)
        return True

    def _pad_pressed(self, name):
        b = self.behaviour_named(name)
        if b is not None:
            self.press_behaviour(b)

    def _pad_released(self, name):
        b = self.behaviour_named(name)
        if b is not None:
            self.release_behaviour(b)

    def _close_open_states(self):
        if not self._open_states or self.test is None:
            self._open_states.clear()
            return
        t = round(self.test_time(), 3)
        for name, t0 in list(self._open_states.items()):
            a, z = sorted((t0, t))
            if z > a:
                self.test.events.append({"behaviour": name, "t": a, "t_end": z})
        self._open_states.clear()
        self.test.events.sort(key=lambda e: e["t"])
        self.main.mark_dirty()
        self._events_changed()

    def _events_changed(self):
        if self.test is not None and self.project is not None:
            old = self.test.status
            if wf.refresh_status(self.project, self.test) != old:
                self._fill_combo()
                self._update_info()
        self._fill_events()
        self._update_active()
        self._mark_stale("results")
        self._refresh_frame()
        self._update_observation_hud()

    def _update_active(self):
        now = self.test_time() if self.test is not None and (self.player.source is not None
                                                             or self.clock.state != "stopped") else None
        if self._open_states:
            parts = [f"<b style='color:#16a34a'>{html.escape(n)}</b> since {t0:.2f} s" +
                     (f" ({now - t0:.1f} s)" if now is not None else "") for n, t0 in self._open_states.items()]
            self.active_lbl.setText("Active: " + ", ".join(parts))
        else:
            self.active_lbl.setText("<span style='color:#94a3b8'>No state behaviour active.</span>")
        self.pad.set_active(self._open_states)

    # ---- observation clock (TakeNote mode) ------------------------------------
    def _update_clock_ui(self):
        no_video = self.test is not None and self.player.source is None
        self.clock_box.setVisible(no_video)
        st = self.clock.state
        self.clock_start_btn.setText("Resume" if st == "paused" else "Start")
        self.clock_start_btn.setIconText(self.clock_start_btn.text())
        self.clock_start_btn.setEnabled(no_video and st != "running")
        self.clock_pause_btn.setEnabled(st == "running")
        self.clock_stop_btn.setEnabled(st != "stopped")
        e = self.clock.elapsed()
        self.clock_lbl.setText(f"{int(e // 60):02d}:{e % 60:04.1f}")
        if no_video:
            self._update_title(e)
        self.clock_lbl.setStyleSheet("font-size:22px;font-weight:bold;font-family:monospace;color:"
                                     + {"running": "#16a34a", "paused": "#d97706"}.get(st, "#334155") + ";")
        self._update_observation_hud()

    def _update_observation_hud(self):
        if self.test is None or self.player.source is not None:
            return
        e = self.clock.elapsed()
        lines = [("No video — scoring by direct observation", "#e2e8f0"),
                 (f"observation clock {fmt_time(e)}  ({self.clock.state})",
                  {"running": "#4ade80", "paused": "#fbbf24"}.get(self.clock.state, "#e2e8f0"))]
        if self.clock.state == "stopped" and not self.test.events:
            lines.append(("Start the TakeNote clock (Scoring)", "#94a3b8"))
        lines += [(f"● {n}", "#4ade80") for n in self._open_states]
        self._set_hud(lines)

    def _clock_tick(self):
        if self.clock.state != "running" or self.test is None:
            self._clock_timer.stop()
            return
        dur = self.test.duration_s or self.project.test_duration_s
        if dur and self.clock.elapsed() >= dur:
            self.clock_stop(at=dur)
            self.main.status(f"Observation finished ({dur:g} s).")
            return
        self._update_clock_ui()
        if self._open_states:
            self._update_active()

    def clock_start(self, confirm: bool = True) -> bool:
        t = self.test
        if t is None or self.player.source is not None or self.clock.state == "running":
            return False
        if self.clock.state == "stopped":
            if t.events and confirm and QMessageBox.question(
                    self, "Observation", f"Test {t.id} already has {len(t.events)} scored events. Delete them and "
                    "score the test again?") != QMessageBox.Yes:
                return False
            if not self.confirm_animal():
                return False
            if t.events:
                t.events = []
                self.main.mark_dirty()
                self._events_changed()
        self.clock.start()
        self._clock_timer.start()
        self._update_clock_ui()
        self.main.status("Observation clock running — score with the keys or the buttons.")
        return True

    def clock_pause(self):
        if self.clock.state != "running":
            return
        self.clock.pause()
        self._clock_timer.stop()
        self._update_clock_ui()

    def clock_stop(self, at: float | None = None):
        """Stop the observation: close running behaviours, store the duration and mark the test scored."""
        t, p = self.test, self.project
        if self.clock.state == "stopped" or t is None:
            return
        e = round(self.clock.stop(at), 3)
        self._clock_timer.stop()
        for name in list(self._open_states):
            self._end_state(name, e)
        dur = t.duration_s or p.test_duration_s
        if not dur or e < dur - 1e-6:
            t.duration_s = round(e, 2)
            self._loading = True
            self.dur_spin.setValue(t.duration_s)
            self._loading = False
        if not t.recorded_at:
            t.recorded_at = _dt.datetime.now().isoformat(timespec="seconds")
        if p.current_user and not t.experimenter:
            t.experimenter = p.current_user
        self._scoring_changed()
        self._update_info()
        self._update_clock_ui()
        self.main.status(f"Observation of test {t.id} stopped at {e:.1f} s — {len(t.events)} events.")

    def _reset_clock(self):
        self._clock_timer.stop()
        self.clock.reset()

    # ---- notes ----------------------------------------------------------------------
    def _load_notes(self):
        self._loading = True
        self.notes_edit.setPlainText(self.test.notes if self.test is not None else "")
        self.notes_edit.setEnabled(self.test is not None)
        self._loading = False

    def _notes_changed(self):
        if self._loading or self.test is None:
            return
        self.test.notes = self.notes_edit.toPlainText()
        self.main.mark_dirty()

    def _fill_events(self):
        tbl = self.events_table
        tbl.setRowCount(0)
        if self.test is None:
            return
        rows = [(e["t"], e, False) for e in self.test.events]
        rows += [(t0, {"behaviour": n, "t": t0, "t_end": None}, True) for n, t0 in self._open_states.items()]
        rows.sort(key=lambda r: r[0])
        self._event_rows = [(e, running) for _, e, running in rows]
        for t0, e, running in rows:
            r = tbl.rowCount()
            tbl.insertRow(r)
            tbl.setItem(r, 0, _ro_item(e["behaviour"]))
            tbl.setItem(r, 1, _num_item(f"{e['t']:.2f}", e["t"]))
            end = e.get("t_end")
            tbl.setItem(r, 2, _num_item("running…" if running else ("—" if end is None else f"{end:.2f}")))
            tbl.setItem(r, 3, _num_item("" if end is None else f"{end - e['t']:.2f}"))
        n = len(self.test.events)
        self.events_lbl.setText(f"{n} event{'s' if n != 1 else ''}")

    def _event_clicked(self, row, _col):
        it = self.events_table.item(row, 1)
        if it is not None and self.player.source is not None:
            self.player.seek_time(self.video_start() + float(it.data(Qt.UserRole)))

    def delete_selected_events(self):
        if self.test is None:
            return
        rows = {i.row() for i in self.events_table.selectionModel().selectedRows()}
        if not rows:
            return
        sel = [self._event_rows[r] for r in rows if r < len(self._event_rows)]
        ids = {id(e) for e, running in sel if not running}
        for e, running in sel:
            if running:
                self._open_states.pop(e["behaviour"], None)
        self.test.events = [e for e in self.test.events if id(e) not in ids]
        self.main.mark_dirty()
        self._events_changed()

    def clear_events(self):
        if self.test is None or not (self.test.events or self._open_states):
            return
        if QMessageBox.question(self, "Clear events", f"Delete all {len(self.test.events)} scored events of "
                                f"test {self.test.id}?") != QMessageBox.Yes:
            return
        self.test.events = []
        self._open_states.clear()
        self.main.mark_dirty()
        self._events_changed()

    def _handle_key(self, e) -> bool:
        if self.test is None or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        b = self.behaviour_for_key(e.text().strip())
        if b is None:
            return False
        if not e.isAutoRepeat():
            self.press_behaviour(b)
        return True

    def _handle_key_release(self, e) -> bool:
        if self.test is None or e.isAutoRepeat():
            return False
        b = self.behaviour_for_key(e.text().strip())
        return b is not None and self.release_behaviour(b)

    def keyReleaseEvent(self, e):
        if self._handle_key_release(e):
            return
        super().keyReleaseEvent(e)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyRelease and self._handle_key_release(e):
            return True
        if e.type() == QEvent.KeyPress:
            if self._handle_key(e):
                return True
            if obj is not self.player and e.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right) \
                    and not (obj is self.player.slider and e.key() != Qt.Key_Space):
                self.player.keyPressEvent(e)
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if self._handle_key(e):
            return
        if e.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right):
            self.player.keyPressEvent(e)
            return
        super().keyPressEvent(e)
