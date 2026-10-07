"""Video tracking engine.

Detects animals as foreground blobs against a background model (median of
sampled frames, a chosen empty-arena frame, or an adaptive running model for
live cameras) or by plain intensity thresholding.  Estimates body centre,
head and tail points, orientation, blob area and a pixel-change "motion" value
used for freezing / immobility analysis.  Supports several arenas per video
(e.g. four open fields filmed together) and several animals per arena with
identity maintenance.  Optionally refines head / body centre / tail base with a
deep-learning pose model (core.pose) run on a crop around each detected animal —
on Apple Silicon through Core ML (Neural Engine / GPU).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from .apparatus import Apparatus
from .track import Track
from .video import FrameReader, VideoSource


@dataclass
class DetectionSettings:
    method: str = "background"  # "background" | "threshold" | "colour"
    contrast: str = "auto"  # "dark": animal darker than background, "light": brighter, "auto": either
    threshold: int = 25  # grey-level difference (or absolute grey level for method="threshold"); 0 = Otsu
    min_area_px: int = 40
    max_area_px: int = 0  # 0 = no limit
    blur: int = 5
    morph_open: int = 3
    morph_close: int = 7
    erase_thin_px: int = 0  # erase thin structures (wires, tubes, cage bars) up to this width before detection
    background: str = "median"  # "median" | "frame" | "adaptive"
    background_frame: int = 0  # frame index used when background == "frame"
    background_samples: int = 31
    adaptive_rate: float = 0.01
    n_animals: int = 1
    head_tail: bool = True
    tail_strip: float = 0.3  # opening kernel relative to sqrt(area) used to strip the tail before head/tail
    motion_threshold: int = 20  # grey-level change counted as motion (freezing)
    max_gap_s: float = 1.0  # interpolate gaps up to this length
    smoothing: int = 0  # moving-average window (frames), 0 = off
    start_time_s: float = 0.0  # analyse from this time in the video
    duration_s: float = 0.0  # analyse this many seconds (0 = to end)
    frame_step: int = 1  # analyse every Nth frame
    arena_margin_px: int = 4
    body_parts: str = "contour"  # "contour": head/tail from blob shape; "pose": deep-learning keypoints
    pose_model: str = "topviewmouse_rtmpose_s"  # core.pose.MODELS key or path to a custom .onnx
    pose_min_conf: float = 0.3  # keypoints below this confidence fall back to the contour estimate
    pose_device: str = "auto"  # "auto" (Core ML on macOS) | "cpu"
    # colour: method "colour" detects pixels of target_colour (a coloured animal, dye mark or collar); with several
    # animals, identity_colours ("#ff0000, #0000ff", one per animal) identifies each animal by its colour mark
    target_colour: str = "#ff0000"
    colour_tolerance: int = 20  # hue difference (degrees) still counted as the colour
    min_saturation: int = 60  # 0–255: greyer pixels are never coloured
    identity_colours: str = ""

    def identity_colour_list(self) -> list[str]:
        return [c.strip() for c in str(self.identity_colours or "").replace(";", ",").split(",") if c.strip()]

    def needs_colour(self) -> bool:
        """Tracking needs colour frames (otherwise greyscale is decoded, which is faster)."""
        return self.method == "colour" or (self.n_animals > 1 and len(self.identity_colour_list()) >= self.n_animals)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "DetectionSettings":
        d = d or {}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Detection:
    x: float = math.nan
    y: float = math.nan
    hx: float = math.nan
    hy: float = math.nan
    tx: float = math.nan
    ty: float = math.nan
    area: float = math.nan
    angle: float = math.nan
    motion: float = math.nan
    detected: bool = False
    contour: np.ndarray | None = field(default=None, repr=False)
    keypoints: np.ndarray | None = field(default=None, repr=False)  # (K, 3) x, y, confidence from the pose model


def hex_to_hsv(colour: str) -> tuple[int, int, int]:
    """OpenCV HSV (hue 0–179) of a "#rrggbb" colour."""
    c = str(colour).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        if len(c) != 6:
            raise ValueError
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"Invalid colour {colour!r}: use #rrggbb, e.g. #ff0000 for red") from None
    h, s_, v = cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2HSV)[0, 0]
    return int(h), int(s_), int(v)


def colour_mask(frame_bgr: np.ndarray, colour: str, tolerance_deg: float = 20, min_saturation: int = 60,
                min_value: int = 40) -> np.ndarray:
    """uint8 mask (255) of the pixels whose hue is within tolerance_deg of ``colour`` and that are saturated and
    bright enough to have a colour at all."""
    h0, _, _ = hex_to_hsv(colour)
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    dh = np.abs(hsv[..., 0].astype(np.int16) - h0)
    dh = np.minimum(dh, 180 - dh)
    ok = (dh <= max(0.5, tolerance_deg / 2)) & (hsv[..., 1] >= min_saturation) & (hsv[..., 2] >= min_value)
    return ok.astype(np.uint8) * 255


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


_POSE_CACHE: dict[tuple, object] = {}


def pose_estimator(settings: DetectionSettings, threads: int = 0):
    """Shared (per process) pose estimator for these settings; creating a session is expensive (Core ML compile)."""
    from . import pose
    from .video import default_threads
    threads = threads or default_threads()
    key = (settings.pose_model, settings.pose_device, threads)
    if key not in _POSE_CACHE:
        model = settings.pose_model
        if model in pose.MODELS and not pose.is_installed(model):
            raise RuntimeError(f"The pose model “{pose.MODELS[model]['title']}” is not installed. Install it in "
                               "Experiment ▸ Default detection settings ▸ Body parts.")
        _POSE_CACHE[key] = pose.PoseEstimator(model, device=settings.pose_device, threads=threads)
    return _POSE_CACHE[key]


def median_background(frames: Sequence[np.ndarray]) -> np.ndarray:
    stack = np.stack([to_gray(f) for f in frames], axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def compute_background(video_path: str, settings: DetectionSettings) -> np.ndarray:
    with VideoSource(video_path) as v:
        if settings.background == "frame":
            f = v.frame_at(settings.background_frame)
            if f is None:
                raise IOError("Could not read background frame")
            return to_gray(f)
        start = int(settings.start_time_s * v.fps)
        end = int((settings.start_time_s + settings.duration_s) * v.fps) if settings.duration_s else v.frame_count
        end = min(end, v.frame_count) if v.frame_count else end
        n = max(3, settings.background_samples)
        idx = np.unique(np.linspace(start, max(start, end - 1), n).astype(int))
        frames = []
        for i in idx:
            f = v.frame_at(int(i))
            if f is not None:
                frames.append(f)
        if not frames:
            raise IOError("Could not read frames for background")
        return median_background(frames)


def _odd(k: int) -> int:
    k = int(k)
    return k if k % 2 == 1 else k + 1


class ArenaTracker:
    """Tracks the animal(s) within one arena mask of a frame."""

    def __init__(self, settings: DetectionSettings, arena_mask: np.ndarray | None = None, pose=None):
        self.s = settings
        self.mask = arena_mask
        self.pose = pose
        self.pose_error = ""
        if pose is None and settings.body_parts == "pose":
            try:  # previews / live sessions fall back to the animal shape (track_video reports the error instead)
                self.pose = pose_estimator(settings)
            except Exception as e:
                self.pose_error = str(e)
        self._last_box: list[tuple | None] = [None] * max(1, settings.n_animals)
        self._last_area: list[float] = [math.nan] * max(1, settings.n_animals)
        self.background: np.ndarray | None = None
        self._bg_float: np.ndarray | None = None
        self.prev_gray: np.ndarray | None = None
        self.prev: list[Detection] = []
        self._flip_votes = [0] * max(1, settings.n_animals)
        self._history: list[list[tuple[float, float]]] = [[] for _ in range(max(1, settings.n_animals))]
        self._single_area: float | None = None
        if settings.method == "colour":
            hex_to_hsv(settings.target_colour)  # a clear error now rather than in the middle of a test
        for c in settings.identity_colour_list():
            hex_to_hsv(c)
        if arena_mask is not None:
            ys, xs = np.nonzero(arena_mask)
            if len(xs):
                m = settings.arena_margin_px
                h, w = arena_mask.shape
                self.roi = (max(0, xs.min() - m), max(0, ys.min() - m), min(w, xs.max() + m + 1), min(h, ys.max() + m + 1))
            else:
                self.roi = (0, 0, arena_mask.shape[1], arena_mask.shape[0])
            if m > 0:
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1, 2 * m + 1))
                self.mask = cv2.dilate(arena_mask, k)
        else:
            self.roi = None

    def set_background(self, bg_gray: np.ndarray):
        self.background = bg_gray
        self._bg_float = bg_gray.astype(np.float32)

    # ------------------------------------------------------------------
    def foreground(self, gray: np.ndarray, frame: np.ndarray | None = None) -> np.ndarray:
        s = self.s
        if s.method == "colour":
            if frame is None or frame.ndim != 3:
                return np.zeros(gray.shape[:2], np.uint8)  # no colour in a greyscale frame
            f = frame
            if s.blur and s.blur > 1:
                f = cv2.GaussianBlur(f, (_odd(s.blur), _odd(s.blur)), 0)
            fg = np.zeros(gray.shape[:2], np.uint8)
            x0, y0, x1, y1 = self.roi if self.roi is not None else (0, 0, gray.shape[1], gray.shape[0])
            fg[y0:y1, x0:x1] = colour_mask(f[y0:y1, x0:x1], s.target_colour, s.colour_tolerance, s.min_saturation)
            return self._clean(fg)
        g = gray
        if s.blur and s.blur > 1:
            g = cv2.GaussianBlur(g, (_odd(s.blur), _odd(s.blur)), 0)
        g = self._erase_thin(g)
        if s.method == "threshold" or self.background is None:
            if s.method == "background" and self.background is None:
                # bootstrap adaptive background from first frame
                self.set_background(g.copy())
            if s.method == "threshold":
                thr = s.threshold if s.threshold > 0 else self._otsu(g)
                if s.contrast == "light":
                    fg = (g > thr).astype(np.uint8) * 255
                else:
                    fg = (g < thr).astype(np.uint8) * 255
                return self._clean(fg)
        bg = self.background
        if s.blur and s.blur > 1:
            bg = cv2.GaussianBlur(bg, (_odd(s.blur), _odd(s.blur)), 0)
        bg = self._erase_thin(bg)
        g16 = g.astype(np.int16)
        b16 = bg.astype(np.int16)
        if s.contrast == "dark":
            diff = np.clip(b16 - g16, 0, 255).astype(np.uint8)
        elif s.contrast == "light":
            diff = np.clip(g16 - b16, 0, 255).astype(np.uint8)
        else:
            diff = cv2.absdiff(g, bg)
        thr = s.threshold if s.threshold > 0 else self._otsu(diff)
        fg = (diff > thr).astype(np.uint8) * 255
        return self._clean(fg)

    def _erase_thin(self, g: np.ndarray) -> np.ndarray:
        """Grey-level closing then opening that removes dark and light structures thinner than erase_thin_px
        (wires, tubes, cage bars — dark against the floor, light against a dark animal), applied alike to frame and
        background so they neither split nor mimic the animal."""
        n = int(self.s.erase_thin_px or 0)
        if n <= 0:
            return g
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(n + 2),) * 2)
        return cv2.morphologyEx(cv2.morphologyEx(g, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k)

    def _otsu(self, img: np.ndarray) -> float:
        vals = img[self.mask > 0] if self.mask is not None else img.ravel()
        if vals.size == 0:
            return 25
        t, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return max(float(t), 8.0)

    def _clean(self, fg: np.ndarray) -> np.ndarray:
        s = self.s
        if self.mask is not None:
            fg = cv2.bitwise_and(fg, self.mask)
        if s.morph_open and s.morph_open > 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(s.morph_open),) * 2)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k)
        if s.morph_close and s.morph_close > 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(s.morph_close),) * 2)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k)
        return fg

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray) -> tuple[list[Detection], np.ndarray]:
        """Detect animals in a frame. Returns (detections, foreground mask)."""
        gray = to_gray(frame)
        fg = self.foreground(gray, frame)
        n = max(1, self.s.n_animals)
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        blobs = []
        for c in contours:
            a = cv2.contourArea(c)
            if a < self.s.min_area_px:
                continue
            if self.s.max_area_px and a > self.s.max_area_px:
                continue
            blobs.append((a, c))
        blobs.sort(key=lambda b: -b[0])
        if n == 1:
            blobs = blobs[:1]
        else:
            blobs = self._split_merged(blobs, n, fg)
        dets = [self._describe(c, a, fg.shape) for a, c in blobs[:n]]
        if n == 1 and dets and self._single_area is None:
            self._single_area = dets[0].area
        colours = self.s.identity_colour_list()
        if n > 1 and len(colours) >= n and frame.ndim == 3:
            dets = self._assign_by_colour(frame, dets, n, colours[:n])
        else:
            dets = self._assign(dets, n)
        posed = self._apply_pose(frame, dets) if self.pose is not None and frame.ndim == 3 else set()
        self._head_tail_consistency(dets, skip=posed)
        self._motion(gray, dets)
        if self.s.background == "adaptive" and self._bg_float is not None:
            # update background only where no animal is present
            upd = cv2.dilate(fg, np.ones((15, 15), np.uint8)) == 0
            cv2.accumulateWeighted(gray.astype(np.float32), self._bg_float, self.s.adaptive_rate,
                                   mask=upd.astype(np.uint8))
            self.background = self._bg_float.astype(np.uint8)
        self.prev_gray = gray
        self.prev = dets
        return dets, fg

    # ------------------------------------------------------------------
    def _split_merged(self, blobs, n, fg):
        """If fewer blobs than animals, split the largest blob(s) with k-means."""
        if not blobs:
            return blobs
        areas = [a for a, _ in blobs]
        if len(blobs) >= n:
            return blobs[:n]
        single = self._single_area or (np.median(areas) if len(areas) > 1 else areas[0] / n)
        out = list(blobs)
        while len(out) < n:
            out.sort(key=lambda b: -b[0])
            a, c = out[0]
            k = int(min(n - len(out) + 1, max(2, round(a / max(single, 1)))))
            if k < 2:
                break
            m = np.zeros(fg.shape, np.uint8)
            cv2.drawContours(m, [c], -1, 255, -1)
            pts = np.column_stack(np.nonzero(m)[::-1]).astype(np.float32)
            if len(pts) < k * 5:
                break
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
            _, labels, _ = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
            new = []
            for j in range(k):
                sub = np.zeros(fg.shape, np.uint8)
                p = pts[labels.ravel() == j].astype(int)
                sub[p[:, 1], p[:, 0]] = 255
                cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                if cs:
                    cc = max(cs, key=cv2.contourArea)
                    new.append((float(len(p)), cc))
            if len(new) < 2:
                break
            out = new + out[1:]
        return out

    def _describe(self, contour, area, shape_hw) -> Detection:
        M = cv2.moments(contour)
        if M["m00"] == 0:
            pts = contour.reshape(-1, 2).astype(float)
            cx, cy = pts.mean(axis=0)
        else:
            cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
        d = Detection(x=float(cx), y=float(cy), area=float(area), detected=True, contour=contour)
        if not self.s.head_tail:
            return d
        # strip the tail with an opening, then find extremes along the major axis
        x0, y0, w, h = cv2.boundingRect(contour)
        pad = 4
        m = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
        cv2.drawContours(m, [contour - [x0 - pad, y0 - pad]], -1, 255, -1)
        k = _odd(max(3, int(self.s.tail_strip * math.sqrt(max(area, 1)))))
        body = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        ys, xs = np.nonzero(body)
        if len(xs) < 5:
            ys, xs = np.nonzero(m)
        pts = np.column_stack([xs + x0 - pad, ys + y0 - pad]).astype(float)
        mean = pts.mean(axis=0)
        # body centre excludes the tail (ANY-maze style "centre of the animal's body")
        d.x, d.y = float(mean[0]), float(mean[1])
        cov = np.cov((pts - mean).T)
        evals, evecs = np.linalg.eigh(cov)
        axis = evecs[:, np.argmax(evals)]
        proj = (pts - mean) @ axis
        lo, hi = np.percentile(proj, [2, 98])
        e1 = pts[proj <= lo].mean(axis=0)
        e2 = pts[proj >= hi].mean(axis=0)
        # default guess: the end nearer the full-blob centroid is the head (tail pulls centroid back
        # only weakly after stripping, so use distance of raw contour extremes instead)
        raw = contour.reshape(-1, 2).astype(float)
        rproj = (raw - mean) @ axis
        # the side whose raw extreme extends further beyond the stripped body has the tail
        ext_lo = lo - rproj.min()
        ext_hi = rproj.max() - hi
        if ext_hi > ext_lo:
            head, tail = e1, e2
        else:
            head, tail = e2, e1
        d.hx, d.hy = map(float, head)
        d.tx, d.ty = map(float, tail)
        d.angle = math.degrees(math.atan2(d.hy - d.ty, d.hx - d.tx))
        return d

    def _assign(self, dets: list[Detection], n: int) -> list[Detection]:
        """Keep identities stable across frames (Hungarian assignment on distance)."""
        out = [Detection() for _ in range(n)]
        if n == 1:
            if dets:
                out[0] = dets[0]
            return out
        prev_ok = [i for i, p in enumerate(self.prev) if p.detected] if self.prev else []
        if not prev_ok:
            for i, d in enumerate(dets[:n]):
                out[i] = d
            return out
        cost = np.full((n, len(dets)), 1e6)
        for i, p in enumerate(self.prev):
            for j, d in enumerate(dets):
                if p.detected:
                    cost[i, j] = math.hypot(p.x - d.x, p.y - d.y)
                else:
                    cost[i, j] = 1e5
        rows, cols = linear_sum_assignment(cost)
        used = set()
        for r, c in zip(rows, cols):
            out[r] = dets[c]
            used.add(c)
        free = [i for i in range(n) if not out[i].detected]
        for j, d in enumerate(dets):
            if j not in used and free:
                out[free.pop(0)] = d
        return out

    def _assign_by_colour(self, frame: np.ndarray, dets: list[Detection], n: int, colours: list[str]
                          ) -> list[Detection]:
        """Identity from colour marks: animal i is the blob with the largest share of pixels of colours[i]
        (optimal assignment); falls back to the position-based assignment when no mark is visible."""
        score = np.zeros((n, len(dets)))
        for j, d in enumerate(dets):
            if d.contour is None:
                continue
            x, y, w, h = cv2.boundingRect(d.contour)
            blob = np.zeros((h, w), np.uint8)
            cv2.drawContours(blob, [d.contour - [x, y]], -1, 255, -1)
            crop = frame[y:y + h, x:x + w]
            area = max(1, int((blob > 0).sum()))
            for i, c in enumerate(colours):
                m = colour_mask(crop, c, self.s.colour_tolerance, self.s.min_saturation)
                score[i, j] = float(((m > 0) & (blob > 0)).sum()) / area
        if not dets or score.max() <= 0:
            return self._assign(dets, n)
        cost = -score
        if self.prev:  # tie-break blobs without a visible mark by distance to the previous position
            for i, p in enumerate(self.prev[:n]):
                for j, d in enumerate(dets):
                    if p.detected:
                        cost[i, j] += 1e-6 * math.hypot(p.x - d.x, p.y - d.y)
        rows, cols = linear_sum_assignment(cost)
        out = [Detection() for _ in range(n)]
        for r, c in zip(rows, cols):
            out[r] = dets[c]
        return out

    def _apply_pose(self, frame: np.ndarray, dets: list[Detection]) -> set[int]:
        """Refine head / centre / tail base with the pose model. Returns indices whose head/tail came from it.

        The crop is the blob's bounding box; if the blob was lost (poor contrast) the last box is re-used and a
        confident pose detection keeps the animal tracked."""
        idx, boxes = [], []
        H, W = frame.shape[:2]
        for i, d in enumerate(dets):
            if d.detected and d.contour is not None:
                x, y, w, h = cv2.boundingRect(d.contour)
                box = (x, y, x + w, y + h)
                self._last_box[i], self._last_area[i] = box, d.area
            else:
                box = self._last_box[i]
            if box is not None:
                idx.append(i)
                boxes.append(box)
        if not boxes:
            return set()
        posed = set()
        thr = self.s.pose_min_conf
        for i, k in zip(idx, self.pose.predict(frame, boxes)):
            d = dets[i]
            d.keypoints = k
            parts = self.pose.body_parts(k)
            nose, centre, tail = parts.get("nose"), parts.get("centre"), parts.get("tail_base")
            ok = lambda p: p is not None and p[2] >= thr and math.isfinite(p[0]) and 0 <= p[0] < W and 0 <= p[1] < H
            if not d.detected:
                if not ok(centre):
                    continue
                d.detected, d.area = True, self._last_area[i]
                d.x, d.y = centre[0], centre[1]
                x0, y0, x1, y1 = self._last_box[i]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                self._last_box[i] = (x0 + d.x - cx, y0 + d.y - cy, x1 + d.x - cx, y1 + d.y - cy)
            elif ok(centre):
                d.x, d.y = centre[0], centre[1]
            if ok(nose) and ok(tail):
                d.hx, d.hy, d.tx, d.ty = nose[0], nose[1], tail[0], tail[1]
                d.angle = math.degrees(math.atan2(d.hy - d.ty, d.hx - d.tx))
                posed.add(i)
        return posed

    def _head_tail_consistency(self, dets: list[Detection], skip: set[int] = frozenset()):
        for i, d in enumerate(dets):
            hist = self._history[i]
            if not d.detected:
                hist.clear()
                continue
            hist.append((d.x, d.y))
            if len(hist) > 6:
                del hist[0]
            if math.isnan(d.hx) or i in skip:
                continue
            p = self.prev[i] if i < len(self.prev) else None
            flip = False
            if p is not None and p.detected and not math.isnan(p.hx):
                keep = math.hypot(d.hx - p.hx, d.hy - p.hy) + math.hypot(d.tx - p.tx, d.ty - p.ty)
                swap = math.hypot(d.tx - p.hx, d.ty - p.hy) + math.hypot(d.hx - p.tx, d.hy - p.ty)
                flip = swap < keep
            # movement direction vote: animals mostly move head first
            if len(hist) >= 4:
                vx, vy = hist[-1][0] - hist[0][0], hist[-1][1] - hist[0][1]
                body_len = math.hypot(d.hx - d.tx, d.hy - d.ty)
                if math.hypot(vx, vy) > 0.25 * max(body_len, 1):
                    hx, hy = (d.tx, d.ty) if flip else (d.hx, d.hy)
                    tx, ty = (d.hx, d.hy) if flip else (d.tx, d.ty)
                    dot = (hx - tx) * vx + (hy - ty) * vy
                    self._flip_votes[i] = self._flip_votes[i] + 1 if dot < 0 else max(0, self._flip_votes[i] - 2)
                    if self._flip_votes[i] >= 5:
                        flip = not flip
                        self._flip_votes[i] = 0
            if flip:
                d.hx, d.hy, d.tx, d.ty = d.tx, d.ty, d.hx, d.hy
                d.angle = math.degrees(math.atan2(d.hy - d.ty, d.hx - d.tx))

    def _motion(self, gray: np.ndarray, dets: list[Detection]):
        if self.prev_gray is None:
            for d in dets:
                d.motion = 0.0 if d.detected else math.nan
            return
        diff = cv2.absdiff(gray, self.prev_gray)
        changed = (diff > self.s.motion_threshold).astype(np.uint8)
        if self.mask is not None:
            changed &= (self.mask > 0).astype(np.uint8)
        if self.s.n_animals == 1:
            total = float(changed.sum())
            for d in dets:
                d.motion = total
            return
        for d in dets:
            if not d.detected or d.contour is None:
                continue
            x, y, w, h = cv2.boundingRect(d.contour)
            pad = int(0.3 * max(w, h))
            y0, y1 = max(0, y - pad), min(gray.shape[0], y + h + pad)
            x0, x1 = max(0, x - pad), min(gray.shape[1], x + w + pad)
            d.motion = float(changed[y0:y1, x0:x1].sum())


class _TrackBuilder:
    def __init__(self):
        self.cols = {c: [] for c in ("t", "x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle", "detected")}

    def add(self, t, d: Detection):
        self.cols["t"].append(t)
        for c in ("x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle"):
            self.cols[c].append(getattr(d, c))
        self.cols["detected"].append(d.detected)

    def build(self, fps) -> Track:
        return Track(**{c: np.asarray(v) for c, v in self.cols.items()}, fps=fps)


@dataclass
class ArenaJob:
    """One arena in a video to be tracked: its mask and detection settings."""

    apparatus: Apparatus | None
    settings: DetectionSettings
    mask: np.ndarray | None = None


def postprocess(track: Track, settings: DetectionSettings) -> Track:
    out = track
    if settings.max_gap_s and settings.max_gap_s > 0:
        out = out.interpolate(settings.max_gap_s)
    if settings.smoothing and settings.smoothing > 1:
        out = out.smooth(settings.smoothing)
    return out


def track_video(video_path: str, jobs: list[ArenaJob],
                progress: Callable[[float], None] | None = None,
                should_stop: Callable[[], bool] | None = None,
                frame_callback: Callable[[int, np.ndarray, list[list[Detection]]], None] | None = None,
                background: np.ndarray | None = None, decode_threads: int = 0) -> list[list[Track]]:
    """Track every arena in a video file in a single pass.

    Returns tracks[job_index][animal_index].  Times are relative to start_time_s
    of the first job's settings.
    """
    if not jobs:
        return []
    s0 = jobs[0].settings
    pose = None
    if any(j.settings.body_parts == "pose" for j in jobs):
        pose = pose_estimator(next(j.settings for j in jobs if j.settings.body_parts == "pose"), threads=decode_threads)
    with VideoSource(video_path) as v:
        W, H = v.width, v.height
        trackers = []
        bg_cache: dict[tuple, np.ndarray] = {}
        for job in jobs:
            mask = job.mask
            if mask is None and job.apparatus is not None:
                mask = job.apparatus.arena_or_bounds().mask((H, W))
            tr = ArenaTracker(job.settings, mask, pose=pose if job.settings.body_parts == "pose" else None)
            if job.settings.method == "background" and job.settings.background != "adaptive":
                if background is not None:
                    tr.set_background(background)
                else:
                    key = (job.settings.background, job.settings.background_frame, job.settings.background_samples,
                           job.settings.start_time_s, job.settings.duration_s)
                    if key not in bg_cache:
                        bg_cache[key] = compute_background(video_path, job.settings)
                    tr.set_background(bg_cache[key])
            trackers.append(tr)
        fps = v.fps
        start = int(round(s0.start_time_s * fps))
        end = v.frame_count if not s0.duration_s else min(v.frame_count, start + int(round(s0.duration_s * fps)))
        if end <= 0:
            end = 10**12
        step = max(1, int(s0.frame_step))
        builders = [[_TrackBuilder() for _ in range(max(1, j.settings.n_animals))] for j in jobs]
        total = max(1, end - start)
    # colour is only needed for the pose model, colour tracking and preview callbacks; otherwise decode to grey
    colour = any(j.settings.needs_colour() for j in jobs)
    reader = FrameReader(video_path, start, gray=pose is None and frame_callback is None and not colour,
                         threads=decode_threads)
    with reader:
        for i, frame in reader:
            if i >= end:
                break
            if (i - start) % step == 0:
                t = (i - start) / fps
                all_dets = []
                for tr, bl in zip(trackers, builders):
                    dets, _ = tr.process(frame)
                    for b, d in zip(bl, dets):
                        b.add(t, d)
                    all_dets.append(dets)
                if frame_callback:
                    frame_callback(i, frame, all_dets)
            if progress and (i + 1 - start) % 25 == 0:
                progress(min(1.0, (i + 1 - start) / total))
            if should_stop and should_stop():
                break
        if progress:
            progress(1.0)
    out = []
    for job, bl in zip(jobs, builders):
        tracks = []
        for b in bl:
            tr = b.build(fps / step)
            tr.meta["video"] = str(video_path)
            tr.meta["video_start_s"] = s0.start_time_s
            tr.meta["decoder"] = reader.backend
            if job.settings.body_parts == "pose" and pose is not None:
                tr.meta["pose_model"] = job.settings.pose_model
                tr.meta["pose_device"] = pose.provider
            tracks.append(postprocess(tr, job.settings))
        out.append(tracks)
    return out


APPARATUS_BGR = (31, 138, 255)  # ANY-maze style orange apparatus outlines


def draw_overlay(frame: np.ndarray, dets: Sequence[Detection], apparatus: Apparatus | None = None,
                 trail: Sequence[tuple[float, float]] | None = None, fg: np.ndarray | None = None,
                 zone_color: tuple[int, int, int] | None = APPARATUS_BGR) -> np.ndarray:
    """Render zones, detections and trail onto a copy of the frame (BGR). Zones are outlined in `zone_color`
    (orange, as in ANY-maze), or in each zone's own colour when zone_color is None."""
    img = frame.copy()
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if fg is not None:
        tint = np.zeros_like(img)
        tint[..., 2] = fg
        img = cv2.addWeighted(img, 1.0, tint, 0.5, 0)
    if apparatus is not None:
        for z in apparatus.zones:
            c = zone_color if zone_color is not None else _hex_to_bgr(z.color)
            cv2.polylines(img, [np.round(z.shape.polygon()).astype(np.int32)], True, c, 1, cv2.LINE_AA)
        for p in apparatus.points:
            cv2.circle(img, (int(p.x), int(p.y)), 4, _hex_to_bgr(p.color), -1, cv2.LINE_AA)
        for l in apparatus.lines:
            cv2.line(img, (int(l.x1), int(l.y1)), (int(l.x2), int(l.y2)), _hex_to_bgr(l.color), 1, cv2.LINE_AA)
    if trail is not None and len(trail) > 1:
        pts = np.array([p for p in trail if np.isfinite(p[0])], np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (255, 160, 0), 1, cv2.LINE_AA)
    colors = [(0, 200, 255), (255, 0, 200), (0, 255, 0), (255, 255, 0)]
    for i, d in enumerate(dets):
        if not d.detected:
            continue
        col = colors[i % len(colors)]
        if d.contour is not None:
            cv2.drawContours(img, [d.contour], -1, col, 1, cv2.LINE_AA)
        if d.keypoints is not None:
            for kx, ky, kc in d.keypoints:
                if kc >= 0.3 and math.isfinite(kx):
                    cv2.circle(img, (int(kx), int(ky)), 2, (255, 255, 0), -1, cv2.LINE_AA)
        cv2.circle(img, (int(d.x), int(d.y)), 4, col, -1, cv2.LINE_AA)
        if not math.isnan(d.hx):
            cv2.circle(img, (int(d.hx), int(d.hy)), 4, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.circle(img, (int(d.tx), int(d.ty)), 3, (255, 0, 0), -1, cv2.LINE_AA)
    return img


def _hex_to_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return (255, 255, 255)
    return (b, g, r)
