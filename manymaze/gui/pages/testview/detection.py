"""Test view detection: per-test detection settings and the live detection preview."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ....core.tracking import ArenaTracker, DetectionSettings, compute_background, draw_overlay
from ...widgets import Worker
from ..base import DETECTION_SPEC, SettingsForm

SPEC_ATTRS = [a for a, *_ in DETECTION_SPEC]


class DetectionMixin:
    """The Detection tab of TestViewPage: per-test settings and the detection preview on the video."""

    def _build_detection_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.det_lbl = QLabel()
        self.det_lbl.setWordWrap(True)
        lay.addWidget(self.det_lbl)
        self.det_form = SettingsForm(DETECTION_SPEC)
        self.det_form.changed.connect(self._detection_changed)
        sc = QScrollArea()
        sc.setWidget(self.det_form)
        sc.setWidgetResizable(True)
        lay.addWidget(sc, 1)
        self.preview_lbl = QLabel()
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet("color:#475569;")
        lay.addWidget(self.preview_lbl)
        row = QGridLayout()
        reset = QPushButton("Reset to experiment defaults")
        reset.clicked.connect(self.reset_detection)
        allb = QPushButton("Apply to all tests")
        allb.setToolTip("Give every test of the experiment these per-test settings")
        allb.clicked.connect(lambda: self.apply_detection_to_all())
        dflt = QPushButton("Save as experiment default")
        dflt.clicked.connect(self.save_detection_as_default)
        row.addWidget(reset, 0, 0)
        row.addWidget(dflt, 0, 1)
        row.addWidget(allb, 1, 0)
        lay.addLayout(row)
        return w

    def _preview_settings(self) -> DetectionSettings:
        return self.project.detection_for(self.test)

    def _bg_settings(self, s: DetectionSettings) -> DetectionSettings:
        b = DetectionSettings.from_dict(s.to_dict())
        if b.background == "adaptive":
            b.background = "median"
        return b

    def bg_key(self, s: DetectionSettings) -> tuple:
        b = self._bg_settings(s)
        lens = self.project.lens_for(self.test)
        return (self.project.abs_path(self.test.video), b.background, b.background_frame, b.background_samples,
                round(b.start_time_s, 3), round(b.duration_s, 3), lens.key() if lens is not None else None)

    def _preview(self, frame, app, draw_zones=True, hud=None):
        hud = hud if hud is not None else []
        s = self._preview_settings()
        h, w = frame.shape[:2]
        mask = None
        if app is not None:
            try:
                mask = app.arena_or_bounds().mask((h, w))
            except ValueError:
                mask = None
        tracker = ArenaTracker(s, mask)
        if s.method == "background":
            key = self.bg_key(s)
            bg = self._bg_cache.get(key)
            if bg is None or bg.shape != (h, w):
                self.request_background()
                img = draw_overlay(frame, [], app if draw_zones else None)
                msg = self._bg_error or "computing background model…"
                hud.append((msg, "#fbbf24"))
                self._set_preview_info(f"Background: {msg}")
                return img
            tracker.set_background(bg)
        dets, fg = tracker.process(frame)
        img = draw_overlay(frame, dets, app if draw_zones else None, fg=fg)
        hud.append(("detection preview", "#f87171"))
        if tracker.pose_error:
            hud.append((f"pose model unavailable: {tracker.pose_error}", "#fbbf24"))
        found = [d for d in dets if d.detected]
        if found:
            info = "; ".join(f"animal {i + 1}: ({d.x:.0f}, {d.y:.0f}) px, area {d.area:.0f} px²"
                             for i, d in enumerate(dets) if d.detected)
            info = f"Detected {len(found)}/{len(dets)} — {info}"
        else:
            info = "<span style='color:#dc2626'>No animal detected in this frame.</span> Try a lower threshold, " \
                   "a smaller minimum area or a different contrast."
        fg_px = int((fg > 0).sum())
        self._set_preview_info(f"{info}<br>Foreground pixels: {fg_px}")
        return img

    def _set_preview_info(self, text):
        self.preview_info = text
        self.preview_lbl.setText(text)

    def request_background(self):
        if self.test is None or not self.test.video:
            return
        s = self._bg_settings(self._preview_settings())
        key = self.bg_key(s)
        if key in self._bg_cache or self._bg_key == key:
            return
        self._bg_error = ""
        if self._bg_worker is not None:  # one at a time; re-requested when the current one finishes
            return
        video = key[0]
        lens = self.project.lens_for(self.test)
        self._bg_key = key
        w = Worker(lambda progress, stop: (key, compute_background(video, s, lens)), self)
        w.signals.done.connect(self._bg_done)
        w.signals.failed.connect(self._bg_failed)
        w.finished.connect(w.deleteLater)
        self._bg_worker = w
        w.start()

    def _bg_done(self, res):
        key, bg = res
        self._bg_cache[key] = bg
        self._bg_worker = None
        self._bg_key = None
        if self.chk_preview.isChecked():
            self._refresh_frame()

    def _bg_failed(self, msg):
        self._bg_worker = None
        self._bg_key = None
        self._bg_error = f"background failed: {msg}"
        self.main.status(self._bg_error)

    def _load_detection_form(self):
        if self.test is None or self.project is None:
            return
        self.det_obj = self.project.detection_for(self.test)
        self.det_form.load(self.det_obj)
        self._update_det_lbl()

    def overrides(self) -> dict:
        ov = self.test.detection or {}
        return {k: v for k, v in ov.items() if k in SPEC_ATTRS and v != getattr(self.project.detection, k, None)}

    def _update_det_lbl(self):
        if self.test is None:
            return
        ov = self.overrides()
        labels = {a: lbl for a, lbl, *_ in DETECTION_SPEC}
        if ov:
            self.det_lbl.setText(f"<b>{len(ov)}</b> setting{'s' if len(ov) != 1 else ''} differ from the experiment "
                                 "defaults for this test: " + ", ".join(f"<i>{labels[k]}</i>" for k in ov))
        else:
            self.det_lbl.setText("This test uses the experiment's default detection settings. Changes made here "
                                 "apply to this test only.")

    def _detection_changed(self):
        if self.test is None or self.det_obj is None:
            return
        base = self.project.detection
        ov = {k: v for k, v in (self.test.detection or {}).items() if k not in SPEC_ATTRS}
        for a in SPEC_ATTRS:
            v = getattr(self.det_obj, a)
            if v != getattr(base, a):
                ov[a] = v
        self.test.detection = ov
        self.main.mark_dirty()
        self._update_det_lbl()
        if not self.chk_preview.isChecked():
            self.chk_preview.setChecked(True)  # refreshes the frame
        else:
            self._refresh_frame()

    def reset_detection(self):
        if self.test is None:
            return
        self.test.detection = {}
        self.main.mark_dirty()
        self._load_detection_form()
        self._refresh_frame()

    def apply_detection_to_all(self, confirm: bool = True):
        p, t = self.project, self.test
        if t is None:
            return
        others = [x for x in p.tests if x is not t]
        if not others:
            return
        if confirm and QMessageBox.question(
                self, "Apply to all tests", f"Use this test's detection settings for all {len(others)} other "
                "tests? (Their own per-test settings are replaced.)") != QMessageBox.Yes:
            return
        for x in others:
            x.detection = dict(t.detection or {})
        self.main.mark_dirty()
        self.main.status(f"Detection settings applied to {len(others)} tests.")

    def save_detection_as_default(self):
        p, t = self.project, self.test
        if t is None or self.det_obj is None:
            return
        for a in SPEC_ATTRS:
            setattr(p.detection, a, getattr(self.det_obj, a))
        t.detection = {k: v for k, v in (t.detection or {}).items() if k not in SPEC_ATTRS}
        self.main.mark_dirty()
        self._load_detection_form()
        self.main.status("Saved as the experiment's default detection settings.")
