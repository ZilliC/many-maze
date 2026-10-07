"""Test view track editing: marking positions, range interpolate / delete, undo, moveable zones."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ....core.track import Track


class TrackEditMixin:
    """The Track editing tab of TestViewPage."""

    def _build_edit_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Fix tracking errors: mark the animal by clicking on the video, or select a time range and "
                      "interpolate / delete the positions in it. Changes are saved to the track immediately.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#475569;")
        lay.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Animal"))
        self.edit_animal = QComboBox()
        row.addWidget(self.edit_animal, 1)
        lay.addLayout(row)

        mk = QGroupBox("Mark position")
        ml = QVBoxLayout(mk)
        adv = QHBoxLayout()
        adv.addWidget(QLabel("Then advance"))
        self.advance_spin = QSpinBox()
        self.advance_spin.setRange(0, 1000)
        self.advance_spin.setValue(1)
        self.advance_spin.setSuffix(" frame(s)")
        adv.addWidget(self.advance_spin)
        adv.addStretch()
        mrow = QHBoxLayout()
        mrow.addWidget(self._tool(self.mark_btn))
        mrow.addWidget(QLabel("then click on the animal in the video"), 1)
        ml.addLayout(mrow)
        ml.addLayout(adv)
        lay.addWidget(mk)

        rg = QGroupBox("Time range")
        rl = QVBoxLayout(rg)
        r1 = QHBoxLayout()
        r1.addWidget(self._tool(self.a_range_start))
        r1.addWidget(self._tool(self.a_range_end))
        r1.addStretch()
        self.range_lbl = QLabel()
        r2 = QHBoxLayout()
        r2.addWidget(self._tool(self.a_interp))
        r2.addWidget(self._tool(self.a_del_range))
        r2.addStretch()
        rl.addLayout(r1)
        rl.addWidget(self.range_lbl)
        rl.addLayout(r2)
        lay.addWidget(rg)
        mv = QGroupBox("Moveable zones in this test (e.g. the platform position)")
        mvl = QHBoxLayout(mv)
        self.move_zone = QComboBox()
        self.move_btn = QPushButton("Place (click on video)")
        self.move_btn.setCheckable(True)
        self.move_btn.toggled.connect(self._mark_toggled)
        self.move_reset = QPushButton("Reset")
        self.move_reset.setToolTip("Use the apparatus position for this test")
        self.move_reset.clicked.connect(self.reset_moveable_zone)
        mvl.addWidget(self.move_zone, 1)
        mvl.addWidget(self.move_btn)
        mvl.addWidget(self.move_reset)
        self.move_box = mv
        lay.addWidget(mv)
        r3 = QHBoxLayout()
        self.edit_lbl = QLabel()
        r3.addWidget(self._tool(self.undo_btn))
        r3.addWidget(self.edit_lbl, 1)
        lay.addLayout(r3)
        lay.addStretch()
        return w

    def _fill_moveable(self):
        base = self.project.get_apparatus(self.test.apparatus) if self.test is not None else None
        names = [z.name for z in base.zones if z.moveable] if base is not None else []
        self.move_zone.clear()
        for n in names:
            moved = self.test is not None and n in self.test.zone_overrides
            self.move_zone.addItem(f"{n}{'  (moved)' if moved else ''}", n)
        self.move_box.setVisible(bool(names))

    def place_moveable_zone(self, x: float, y: float):
        """Centre the selected moveable zone on (x, y) for this test only."""
        name = self.move_zone.currentData()
        base = self.project.get_apparatus(self.test.apparatus) if self.test is not None else None
        z = base.zone(name) if base is not None and name else None
        if z is None:
            return
        cx, cy = z.shape.centroid()
        self.test.zone_overrides[name] = z.shape.translated(x - cx, y - cy).to_dict()
        self._moveable_changed(f"{name} placed at ({x:.0f}, {y:.0f}) px for this test.")

    def reset_moveable_zone(self):
        name = self.move_zone.currentData()
        if self.test is not None and name and self.test.zone_overrides.pop(name, None) is not None:
            self._moveable_changed(f"{name} reset to the apparatus position.")

    def _moveable_changed(self, msg):
        i = self.move_zone.currentIndex()
        self._fill_moveable()
        self.move_zone.setCurrentIndex(i)
        self.main.mark_dirty()
        self._mark_stale("results", "plots")
        self._refresh_frame()
        self.edit_lbl.setText(msg)

    def _edit_index(self) -> int:
        return self.edit_animal.currentData() or 0

    def _mark_toggled(self, on):
        v = self.player.view
        v.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        v.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)
        if on:
            self.player.pause()
            self.player.view.setFocus()
        self._refresh_frame()

    def _view_clicked(self, x, y):
        if self.move_btn.isChecked():
            self.place_moveable_zone(x, y)
            self.move_btn.setChecked(False)
        elif self.mark_btn.isChecked():
            self.mark_position(x, y)

    def _new_track(self) -> Track:
        p, t = self.project, self.test
        fps = self.player.fps
        dur = t.duration_s or p.test_duration_s
        if not dur and self.player.source is not None:
            dur = max(0.0, self.player.source.duration - t.start_s)
        n = max(1, int(round(dur * fps)))
        nan = np.full(n, np.nan)
        tr = Track(t=np.arange(n) / fps, x=nan.copy(), y=nan.copy(), fps=fps, detected=np.zeros(n, bool))
        tr.meta["video_start_s"] = t.start_s
        tr.meta["source"] = "manual"
        return tr

    def _push_undo(self, ai):
        self._undo.append((ai, self.tracks[ai].copy() if ai < len(self.tracks) else None))
        del self._undo[:-30]

    def _save_tracks(self, msg=""):
        self.project.save_tracks(self.test, self.tracks)
        self.main.mark_dirty()
        self._update_info()
        self._mark_stale("results", "plots")
        self._refresh_frame()
        self.edit_lbl.setText(msg)

    def mark_position(self, x, y):
        if self.test is None or self.player.source is None or self.project.path is None:
            return False
        ai = self._edit_index()
        created = False
        while len(self.tracks) <= ai:
            self.tracks.append(self._new_track())
            created = True
        tr = self.tracks[ai]
        j = self.sample_at(tr, self.player.time - self.video_start(tr))
        if j is None:
            self.edit_lbl.setText("The current frame is outside the tracked period.")
            return False
        if not created:
            self._push_undo(ai)
        else:
            self._undo.append((ai, None))
        if math.isfinite(tr.x[j]) and math.isfinite(tr.y[j]):
            dx, dy = x - tr.x[j], y - tr.y[j]
            for cx, cy in (("hx", "hy"), ("tx", "ty")):
                getattr(tr, cx)[j] += dx
                getattr(tr, cy)[j] += dy
        else:
            for c in ("hx", "hy", "tx", "ty", "angle"):
                getattr(tr, c)[j] = np.nan
        tr.x[j], tr.y[j] = x, y
        tr.detected[j] = True
        self._save_tracks(f"Marked ({x:.0f}, {y:.0f}) at {tr.t[j]:.2f} s")
        if created:
            self._load_tracks()
        if self.advance_spin.value():
            self.player.seek(self.player.index + self.advance_spin.value())
        return True

    def set_range(self, which: int, t: float | None = None):
        if self.test is None:
            return
        self._range[which] = round(self.test_time() if t is None else t, 3)
        self._update_range_lbl()
        self._refresh_frame()

    def _update_range_lbl(self):
        a, b = self._range
        f = lambda v: "—" if v is None else f"{v:.2f} s"  # noqa: E731
        self.range_lbl.setText(f"Range (test time): {f(a)} → {f(b)}")

    def _range_indices(self, tr):
        a, b = self._range
        if a is None or b is None or len(tr) == 0:
            self.edit_lbl.setText("Set the range start and end first.")
            return None
        a, b = sorted((a, b))
        idx = np.flatnonzero((tr.t >= a - 1e-6) & (tr.t <= b + 1e-6))
        if len(idx) == 0:
            self.edit_lbl.setText("No track samples in the range.")
            return None
        return idx

    def interpolate_range(self):
        ai = self._edit_index()
        if self.test is None or ai >= len(self.tracks):
            return False
        tr = self.tracks[ai]
        idx = self._range_indices(tr)
        if idx is None:
            return False
        self._push_undo(ai)
        for c in ("x", "y", "hx", "hy", "tx", "ty"):
            v = getattr(tr, c)
            ok = np.isfinite(v)
            ok[idx] = False
            v[idx] = np.interp(tr.t[idx], tr.t[ok], v[ok]) if ok.sum() >= 1 else np.nan
        with np.errstate(invalid="ignore"):
            tr.angle[idx] = np.degrees(np.arctan2(tr.hy[idx] - tr.ty[idx], tr.hx[idx] - tr.tx[idx]))
        tr.detected[idx] = False
        self._save_tracks(f"Interpolated {len(idx)} samples.")
        return True

    def delete_range(self):
        ai = self._edit_index()
        if self.test is None or ai >= len(self.tracks):
            return False
        tr = self.tracks[ai]
        idx = self._range_indices(tr)
        if idx is None:
            return False
        self._push_undo(ai)
        for c in ("x", "y", "hx", "hy", "tx", "ty", "angle"):
            getattr(tr, c)[idx] = np.nan
        tr.detected[idx] = False
        self._save_tracks(f"Deleted {len(idx)} positions.")
        return True

    def undo_edit(self):
        if not self._undo or self.test is None:
            return
        ai, prev = self._undo.pop()
        if prev is None:  # the track was created by the edit
            if ai < len(self.tracks):
                self.tracks.pop(ai)
                tp = self.project.track_path(self.test, ai)
                if tp.exists():
                    tp.unlink()
            if not self.tracks:
                self.test.status = "pending"
                self.main.mark_dirty()
                self._update_info()
                self._mark_stale("results", "plots")
                self._refresh_frame()
                self.edit_lbl.setText("Undone.")
                return
        else:
            self.tracks[ai] = prev
        self._save_tracks("Undone.")
