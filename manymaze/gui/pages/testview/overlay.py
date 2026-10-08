"""Test view overlay: the apparatus, tracks and HUD drawn over the video frame."""

from __future__ import annotations

import html
import math

import cv2
import numpy as np

from ....core.track import Track
from ....core.tracking import ANIMAL_COLORS, draw_overlay
from ...widgets import fmt_time


def _put_text(img, text, org, color, scale):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (0, 0, 0), max(2, int(3 * scale)),
                cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, color, max(1, int(scale)), cv2.LINE_AA)


class OverlayMixin:
    """Video/test time mapping and the overlay drawn on each frame of TestViewPage."""

    def _app(self):
        if self.test is None:
            return None
        app = self.project.get_apparatus(self.test.apparatus)
        return app.with_overrides(self.test.zone_overrides) if app is not None else None

    def video_start(self, tr: Track | None = None) -> float:
        """Video time (s) at which track time 0 occurs."""
        for x in ([tr] if tr is not None else self.tracks):
            v = x.meta.get("video_start_s")
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return self.test.start_s if self.test is not None else 0.0

    def test_time(self) -> float:
        """Current time relative to the test start (the time base of the track and of scored events).

        Without a video this is the observation clock."""
        if self.player.source is None:
            return self.clock.elapsed()
        return self.player.time - self.video_start()

    @staticmethod
    def sample_at(tr: Track, t: float) -> int | None:
        if len(tr) == 0:
            return None
        i = int(np.searchsorted(tr.t, t))
        cands = [j for j in (i - 1, i) if 0 <= j < len(tr)]
        j = min(cands, key=lambda j: abs(tr.t[j] - t))
        if abs(tr.t[j] - t) > max(1.5 * tr.dt, 0.02):
            return None
        return j

    def _overlay(self, index: int, frame: np.ndarray) -> np.ndarray:
        if self.test is None or self.project is None:
            return frame
        app = self._app()
        h, w = frame.shape[:2]
        sc = max(1.0, w / 640)
        vt = index / self.player.fps
        hud: list[tuple[str, str]] = []
        if self.chk_preview.isChecked():
            img = self._preview(frame, app, self.chk_zones.isChecked(), hud)
        elif self.chk_zones.isChecked() and app is not None:
            img = draw_overlay(frame, [], app)
        else:
            img = frame.copy()
        if self.chk_zones.isChecked() and app is not None and app.arena is not None:
            cv2.polylines(img, [np.round(app.arena.polygon()).astype(np.int32)], True, (235, 235, 235),
                          max(1, int(sc)), cv2.LINE_AA)
        if self.chk_animal.isChecked() and not self.chk_preview.isChecked():
            self._draw_tracks(img, vt, sc)
        # test window + scoring state
        start = self.video_start()
        dur = self.test.duration_s or self.project.test_duration_s
        if vt < start - 1e-6:
            hud.insert(0, (f"before test start ({fmt_time(start)})", "#fbbf24"))
        elif dur and vt > start + dur + 1e-6:
            hud.insert(0, ("after test end", "#fbbf24"))
        else:
            hud.insert(0, (f"test time {fmt_time(vt - start)}", "#ffffff"))
        for name in self._open_states:
            hud.append((f"● {name}", "#4ade80"))
        if self._range[0] is not None or self._range[1] is not None:
            tt = vt - start
            a, b = self._range
            if a is not None and b is not None and min(a, b) <= tt <= max(a, b):
                cv2.rectangle(img, (0, 0), (w - 1, h - 1), (0, 200, 255), max(2, int(2 * sc)))
        if self.mark_btn.isChecked():
            hud.append(("click on the animal to mark its position", "#22d3ee"))
        self._set_hud(hud)
        return img

    def _draw_tracks(self, img, vt, sc):
        trail = self.trail_spin.value() if self.chk_trail.isChecked() else 0
        r = max(3, int(4 * sc))
        lw = max(1, int(sc))
        ids = [self.test.animal_id] + list(self.test.extra_animals)
        for ai, tr in enumerate(self.tracks):
            if len(tr) == 0:
                continue
            col = ANIMAL_COLORS[ai % len(ANIMAL_COLORS)]
            tt = vt - self.video_start(tr)
            if trail > 0:
                m = (tr.t <= tt) & (tr.t >= tt - trail)
                xs, ys = tr.x[m], tr.y[m]
                ok = np.isfinite(xs) & np.isfinite(ys)
                # split the trail at missing samples
                for run in np.split(np.arange(len(xs)), np.flatnonzero(~ok)):
                    run = run[ok[run]] if len(run) else run
                    if len(run) > 1:
                        pts = np.column_stack([xs[run], ys[run]]).round().astype(np.int32)
                        cv2.polylines(img, [pts], False, (255, 170, 40), lw, cv2.LINE_AA)
            j = self.sample_at(tr, tt)
            if j is None or not (math.isfinite(tr.x[j]) and math.isfinite(tr.y[j])):
                continue
            c = (int(round(tr.x[j])), int(round(tr.y[j])))
            outline = tr.outline[j] if tr.outline is not None else None
            if outline is not None and len(outline) > 2:  # whole-body outline recorded by the tracker
                cv2.polylines(img, [outline.reshape(-1, 1, 2)], True, col, lw, cv2.LINE_AA)
            if math.isfinite(tr.hx[j]) and math.isfinite(tr.tx[j]):
                hp = (int(round(tr.hx[j])), int(round(tr.hy[j])))
                tp = (int(round(tr.tx[j])), int(round(tr.ty[j])))
                cv2.line(img, tp, hp, col, lw, cv2.LINE_AA)
                cv2.circle(img, hp, r, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.circle(img, tp, max(2, r - 1), (255, 80, 0), -1, cv2.LINE_AA)
            cv2.circle(img, c, r, col, -1 if tr.detected[j] else lw, cv2.LINE_AA)
            if len(self.tracks) > 1:
                label = ids[ai] if ai < len(ids) and ids[ai] else f"#{ai + 1}"
                _put_text(img, label, (c[0] + r + 2, c[1] - r - 2), col, sc * 0.9)

    def _set_hud(self, lines: list[tuple[str, str]]):
        """Crisp text drawn over the top-left corner of the video (not scaled with it)."""
        if not lines:
            self.hud.setVisible(False)
            return
        body = "<br>".join(f"<span style='color:{c}'>{html.escape(t)}</span>" for t, c in lines)
        self.hud.setHtml(f"<div style='background-color:rgba(15,23,42,170);'>&nbsp;{body}&nbsp;</div>")
        self.hud.setVisible(True)
