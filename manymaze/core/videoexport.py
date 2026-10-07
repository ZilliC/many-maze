"""Render a test's video with tracking overlays (zones, coloured track trail, head/tail, behaviour and event
labels, time stamp) and encode it to a new file (H.264 via VideoRecorder where available).

Frames are decoded, drawn and encoded one at a time, so memory use does not depend on the video length.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .measures import AnalysisSettings, kinematics, moving_average
from .track import Track
from .tracking import ANIMAL_COLORS, hex_to_bgr
from .video import FrameReader, VideoRecorder, VideoSource


@dataclass
class OverlayOptions:
    zones: bool = True
    zone_labels: bool = True
    zone_fill: float = 0.15  # opacity of zone fill (0 = outlines only)
    trail_s: float = 5.0  # length of the track trail in seconds; 0 = none; < 0 = whole track so far
    trail_color: str = "speed"  # speed | time | fixed
    body_points: bool = True  # centre, head and tail
    behaviours: bool = True  # manually scored behaviours active at this time / recent point events
    freezing: bool = True  # "Freezing" badge while the animal is freezing
    timestamp: bool = True
    info: bool = True  # test / animal caption
    scale: float = 1.0  # output size relative to the video
    speed: float = 1.0  # playback speed of the output (2 = twice as fast; 0.5 = slow motion)
    t_start: float = 0.0  # test time (s) to start at
    t_end: float | None = None  # test time to stop at (None = end of the track)


def _cmap_bgr(values: np.ndarray, vmin: float, vmax: float, cmap: str = "turbo") -> np.ndarray:
    from matplotlib import colormaps

    v = np.nan_to_num((np.asarray(values, float) - vmin) / max(vmax - vmin, 1e-9), nan=0.0)
    rgba = colormaps[cmap](np.clip(v, 0, 1))
    return (rgba[:, [2, 1, 0]] * 255).astype(np.uint8)


def _text(img, txt, org, scale, color=(255, 255, 255), bg=(0, 0, 0), thick=1):
    (w, h), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = org
    cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + base + 1), bg, -1)
    cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)
    return h + base + 8


class OverlayRenderer:
    """Draws overlays for one test onto frames; `render(frame, t)` with t in test time."""

    def __init__(self, tracks: list[Track], app=None, options: OverlayOptions | None = None, events=None,
                 behaviours=None, settings: AnalysisSettings | None = None, caption: str = ""):
        self.o = options or OverlayOptions()
        self.tracks = tracks
        self.app = app
        self.events = [e for e in (events or []) if e.get("behaviour")]
        kinds = {}
        for b in behaviours or []:
            d = b if isinstance(b, dict) else asdict(b)
            kinds[d["name"]] = d.get("kind", "state")
        self.kinds = kinds
        self.caption = caption
        s = settings or AnalysisSettings()
        self.colors = []
        self.freezing = []
        for tr in tracks:
            if len(tr) == 0:
                self.colors.append(np.zeros((0, 3), np.uint8))
                self.freezing.append(np.zeros(0, bool))
                continue
            if self.o.trail_color == "time":
                col = _cmap_bgr(tr.t, float(tr.t[0]), float(tr.t[-1]), "viridis")
            elif self.o.trail_color == "speed" and app is not None:
                k = kinematics(tr, app, s)
                sp = moving_average(k.speed, max(1, int(round(0.3 / max(tr.dt, 1e-6)))))
                fin = sp[np.isfinite(sp)]
                col = _cmap_bgr(sp, 0.0, float(np.percentile(fin, 95)) if len(fin) else 1.0)
            else:
                col = np.tile(np.array(ANIMAL_COLORS[len(self.colors) % len(ANIMAL_COLORS)], np.uint8), (len(tr), 1))
            self.colors.append(col)
            try:
                self.freezing.append(kinematics(tr, app, s).freezing if app is not None else np.zeros(len(tr), bool))
            except Exception:
                self.freezing.append(np.zeros(len(tr), bool))
        self._static = None
        self._trail_layer = None
        self._trail_upto = [-1] * len(tracks)

    # ------------------------------------------------------------------
    def _scaled_pts(self, pts) -> np.ndarray:
        return np.round(np.asarray(pts, float) * self.o.scale).astype(np.int32)

    def _static_layer(self, shape):
        """Zone fill layer (BGR) + mask, computed once."""
        if self._static is not None and self._static[0].shape == shape:
            return self._static
        layer = np.zeros(shape, np.uint8)
        mask = np.zeros(shape[:2], np.uint8)
        if self.app is not None and self.o.zones and self.o.zone_fill > 0:
            for z in self.app.zones:
                pts = self._scaled_pts(z.shape.polygon())
                cv2.fillPoly(layer, [pts], hex_to_bgr(z.color))
                cv2.fillPoly(mask, [pts], 255)
        self._static = (layer, mask > 0)
        return self._static

    def sample_index(self, tr: Track, t: float) -> int:
        """Index of the last track sample at or before test time t (-1 if before the first)."""
        return int(np.searchsorted(tr.t, t + 1e-9, side="right")) - 1

    def render(self, frame: np.ndarray, t: float) -> np.ndarray:
        o = self.o
        img = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if o.scale != 1.0:
            h, w = img.shape[:2]
            img = cv2.resize(img, (max(2, int(round(w * o.scale))), max(2, int(round(h * o.scale)))),
                             interpolation=cv2.INTER_AREA)
        else:
            img = img.copy()
        fs = max(0.35, min(img.shape[:2]) / 900)
        if self.app is not None and o.zones:
            if o.zone_fill > 0:
                layer, m = self._static_layer(img.shape)
                img[m] = (img[m] * (1 - o.zone_fill) + layer[m] * o.zone_fill).astype(np.uint8)
            if self.app.arena is not None:
                cv2.polylines(img, [self._scaled_pts(self.app.arena.polygon())], True, (240, 240, 240), 1,
                              cv2.LINE_AA)
            used = []
            for z in self.app.zones:
                pts = self._scaled_pts(z.shape.polygon())
                cv2.polylines(img, [pts], True, hex_to_bgr(z.color), 1, cv2.LINE_AA)
                if o.zone_labels:
                    (tw, th), _ = cv2.getTextSize(z.name, cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8, 1)
                    cx, cy = z.shape.centroid()
                    x, y = int(cx * o.scale) - tw // 2, int(cy * o.scale) + th // 2
                    while any(abs(x - ux) < tw and abs(y - uy) < th + 4 for ux, uy in used):
                        y += th + 6
                    used.append((x, y))
                    cv2.putText(img, z.name, (x, y), cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8, hex_to_bgr(z.color), 1,
                                cv2.LINE_AA)
            for p in self.app.points:
                cv2.circle(img, tuple(self._scaled_pts([p.x, p.y])), 4, hex_to_bgr(p.color), -1, cv2.LINE_AA)
            for ln in self.app.lines:
                a = tuple(self._scaled_pts([ln.x1, ln.y1]))
                b = tuple(self._scaled_pts([ln.x2, ln.y2]))
                cv2.line(img, a, b, hex_to_bgr(ln.color), 2, cv2.LINE_AA)
        active = []
        for ai, tr in enumerate(self.tracks):
            j = self.sample_index(tr, t)
            if j < 0 or len(tr) == 0:
                continue
            j = min(j, len(tr) - 1)
            self._draw_trail(img, ai, tr, j)
            if o.body_points and np.isfinite(tr.x[j]):
                col = ANIMAL_COLORS[ai % len(ANIMAL_COLORS)]
                c = tuple(self._scaled_pts([tr.x[j], tr.y[j]]))
                cv2.circle(img, c, 5, col, -1, cv2.LINE_AA)
                cv2.circle(img, c, 5, (0, 0, 0), 1, cv2.LINE_AA)
                if np.isfinite(tr.hx[j]):
                    cv2.line(img, tuple(self._scaled_pts([tr.tx[j], tr.ty[j]])),
                             tuple(self._scaled_pts([tr.hx[j], tr.hy[j]])), (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.circle(img, tuple(self._scaled_pts([tr.hx[j], tr.hy[j]])), 4, (0, 0, 255), -1, cv2.LINE_AA)
                    cv2.circle(img, tuple(self._scaled_pts([tr.tx[j], tr.ty[j]])), 3, (255, 0, 0), -1, cv2.LINE_AA)
            if o.freezing and j < len(self.freezing[ai]) and self.freezing[ai][j]:
                active.append(("Freezing" if len(self.tracks) == 1 else f"Animal {ai + 1}: freezing",
                               (255, 200, 0)))
        if o.behaviours:
            for e in self.events:
                b = e["behaviour"]
                point = self.kinds.get(b) == "point" or (e.get("t_end") is None and self.kinds.get(b) != "state")
                if point:
                    if e["t"] <= t < e["t"] + 1.0:
                        active.append((f"{b} (event)", (0, 220, 255)))
                else:
                    t1 = e.get("t_end") if e.get("t_end") is not None else math.inf
                    if e["t"] <= t < t1:
                        active.append((b, (80, 255, 120)))
        y = int(22 * fs / 0.5)
        for label, col in active:
            y += _text(img, label, (8, y), fs, (0, 0, 0), col, 1)
        h, w = img.shape[:2]
        if o.timestamp:
            m, s = divmod(max(0.0, t), 60)
            _text(img, f"{int(m):02d}:{s:05.2f}", (8, h - 10), fs * 1.1)
        if o.info and self.caption:
            (tw, _), _ = cv2.getTextSize(self.caption, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
            _text(img, self.caption, (w - tw - 10, h - 10), fs)
        return img

    def _draw_trail(self, img, ai, tr: Track, j: int):
        o = self.o
        if o.trail_s == 0 or j < 1:
            return
        col = self.colors[ai]
        if o.trail_s < 0:
            # whole trail so far: accumulate segments on a persistent layer
            if self._trail_layer is None or self._trail_layer[0].shape != img.shape:
                self._trail_layer = (np.zeros(img.shape, np.uint8), np.zeros(img.shape[:2], np.uint8))
                self._trail_upto = [-1] * len(self.tracks)
            layer, mask = self._trail_layer
            start = max(1, self._trail_upto[ai] + 1)
            if j < self._trail_upto[ai]:  # went backwards: restart
                layer[:] = 0
                mask[:] = 0
                start = 1
            if start <= j:
                xs, ys = tr.x[start - 1:j + 1], tr.y[start - 1:j + 1]
                P = self._scaled_pts(np.column_stack([np.nan_to_num(xs), np.nan_to_num(ys)]))
                ok = np.isfinite(xs) & np.isfinite(ys)
                for k in range(1, len(P)):
                    if ok[k - 1] and ok[k]:
                        c = tuple(int(v) for v in col[start - 1 + k])
                        cv2.line(layer, tuple(P[k - 1]), tuple(P[k]), c, 1, cv2.LINE_AA)
                        cv2.line(mask, tuple(P[k - 1]), tuple(P[k]), 255, 1, cv2.LINE_AA)
            self._trail_upto[ai] = j
            m = mask > 0
            img[m] = layer[m]
            return
        i0 = max(1, int(np.searchsorted(tr.t, tr.t[j] - o.trail_s)))
        if i0 > j:
            return
        xs, ys = tr.x[i0 - 1:j + 1], tr.y[i0 - 1:j + 1]
        P = self._scaled_pts(np.column_stack([np.nan_to_num(xs), np.nan_to_num(ys)]))
        ok = np.isfinite(xs) & np.isfinite(ys)
        for k in range(1, len(P)):
            if ok[k - 1] and ok[k]:
                c = tuple(int(v) for v in col[i0 - 1 + k])
                cv2.line(img, tuple(P[k - 1]), tuple(P[k]), c, 2, cv2.LINE_AA)


def export_video(project, test, path, options: OverlayOptions | None = None, progress=None, should_stop=None
                 ) -> Path | None:
    """Write the test's video with overlays to `path` (.mp4 recommended). Returns the path, or None if cancelled."""
    from . import charts

    o = options or OverlayOptions()
    video = project.abs_path(test.video)
    if not video or not Path(video).exists():
        raise FileNotFoundError(f"Video not found: {test.video}")
    tracks = project.load_tracks(test) if project.has_track(test) else []
    app = charts.apparatus_of_test(project, test)
    with VideoSource(video) as src:
        fps, w, h, nframes = src.fps, src.width, src.height, src.frame_count
    video_start = float(tracks[0].meta.get("video_start_s", test.start_s) or 0.0) if tracks else float(test.start_s)
    if tracks and len(tracks[0]):
        t_last = float(tracks[0].t[-1])
    else:
        t_last = (test.duration_s or project.test_duration_s or (nframes / fps)) - 1.0 / fps
    t0 = max(0.0, o.t_start or 0.0)
    t1 = t_last if o.t_end is None else min(o.t_end, t_last)
    f0 = int(round((video_start + t0) * fps))
    f1 = min(nframes - 1 if nframes else 10 ** 9, int(round((video_start + t1) * fps)))
    if f1 < f0:
        raise ValueError("Empty time range")
    step = max(1, int(round(o.speed))) if o.speed >= 1 else 1
    out_fps = fps if o.speed >= 1 else max(1.0, fps * o.speed)
    ow, oh = int(round(w * o.scale)), int(round(h * o.scale))
    ow, oh = ow - ow % 2, oh - oh % 2
    caption = f"Test {test.id} - {test.animal_id}" if test.animal_id else f"Test {test.id}"
    rend = OverlayRenderer(tracks, app, o, test.events, [asdict(b) for b in project.behaviours],
                           project.analysis_for(test), caption)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = VideoRecorder(str(path), out_fps, (ow, oh))
    total = (f1 - f0) // step + 1
    written = 0
    cancelled = False
    try:
        with FrameReader(video, start=f0, gray=False, threads=1) as reader:
            for i, frame in reader:
                if i > f1:
                    break
                if (i - f0) % step:
                    continue
                if should_stop and should_stop():
                    cancelled = True
                    break
                img = rend.render(frame, i / fps - video_start)
                if img.shape[1] != ow or img.shape[0] != oh:
                    img = img[:oh, :ow]
                rec.write(np.ascontiguousarray(img))
                written += 1
                if progress and written % 5 == 0:
                    progress(min(1.0, written / max(1, total)))
    finally:
        rec.close()
    if cancelled:
        path.unlink(missing_ok=True)
        return None
    if progress:
        progress(1.0)
    return path
