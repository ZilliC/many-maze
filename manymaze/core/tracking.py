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
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from .apparatus import Apparatus
from .track import COLUMNS, Track, simplify_outline
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
    record_outline: bool = True  # store the animal's whole-body outline (a simplified polygon) in each frame
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
    outline: np.ndarray | None = field(default=None, repr=False)  # simplified body outline (k, 2) stored in the track


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
_POSE_LOCK = threading.Lock()


def pose_estimator(settings: DetectionSettings, threads: int = 0):
    """Shared (per process) pose estimator for these settings; creating a session is expensive (Core ML compile)."""
    from . import pose
    from .video import default_threads
    threads = threads or default_threads()
    key = (settings.pose_model, settings.pose_device, threads)
    with _POSE_LOCK:  # live sources build their trackers from several threads
        if key not in _POSE_CACHE:
            model = settings.pose_model
            if model in pose.MODELS and not pose.is_installed(model):
                raise RuntimeError(f"The pose model “{pose.MODELS[model]['title']}” is not installed. Install it "
                                   "in Experiment ▸ Default detection settings ▸ Body parts.")
            _POSE_CACHE[key] = pose.PoseEstimator(model, device=settings.pose_device, threads=threads)
        return _POSE_CACHE[key]


def median_background(frames: Sequence[np.ndarray]) -> np.ndarray:
    stack = np.stack([to_gray(f) for f in frames], axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def sample_background(src, n: int = 21) -> np.ndarray | None:
    """Median of n frames evenly spaced through an open video source (frame_count / frame_at / seek, e.g. a live
    source simulated by a file); the source is rewound."""
    count = src.frame_count
    if not count:
        return None
    frames = [f for f in (src.frame_at(int(i)) for i in np.unique(np.linspace(0, count - 1, n).astype(int)))
              if f is not None]
    src.seek(0)
    return median_background(frames) if frames else None


def _frame_window(settings: DetectionSettings, fps: float, count: int) -> tuple[int, int]:
    """Frames [start, end) of the analysis window. A frame count of 0 (unknown to OpenCV, e.g. some streams) ends
    the window after its duration; with neither, end is 0 (unknown)."""
    start = int(round(settings.start_time_s * fps))
    if count > 0 and start >= count:
        raise ValueError(f"The analysis starts at {settings.start_time_s:g} s, after the end of the video "
                         f"({count / (fps or 25.0):.1f} s): set an earlier start time")
    if not settings.duration_s:
        return start, count
    end = start + int(round(settings.duration_s * fps))
    return start, (min(count, end) if count > 0 else end)


def compute_background(video_path: str, settings: DetectionSettings) -> np.ndarray:
    with VideoSource(video_path) as v:
        if settings.background == "frame":
            f = v.frame_at(settings.background_frame)
            if f is None:
                raise IOError("Could not read background frame")
            return to_gray(f)
        start, end = _frame_window(settings, v.fps, v.frame_count)  # raises if the window is past the end
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
    """Tracks the animal(s) within one arena mask of a frame.

    Only the part of the frame around the arena is processed (its bounding box widened by the reach of the blur,
    thin-structure eraser and morphology, so the result is the same as processing the whole frame), and the
    blurred / erased background of that region is cached, so many small arenas in one large frame (multi-well
    plates) cost little more than one."""

    # an identity not seen for this many frames is forgotten (its last-known position no longer guides assignment)
    identity_memory_frames = 250
    # a pose-only recovery (blob lost, pose model confident) may continue for at most this many consecutive frames
    pose_only_max_frames = 75

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
        n = max(1, settings.n_animals)
        self._last_box: list[tuple | None] = [None] * n
        self._last_area: list[float] = [math.nan] * n
        self._pose_only = [0] * n  # consecutive frames each animal was kept by the pose model alone
        self.background: np.ndarray | None = None
        self._bg_float: np.ndarray | None = None
        self._bg_owned = False  # the background arrays are private copies (the adaptive model writes into them)
        self._bg_version = 0
        self._bgp_key = None  # cache of the blurred / erased background of the processed region
        self._bgp: np.ndarray | None = None
        self._bootstrapped = False  # the background is the first frame seen (no empty-arena image was given)
        self.prev_gray: np.ndarray | None = None  # previous frame of the processed region (motion)
        self._prev_box = None
        self.prev: list[Detection] = []
        # identity memory: last-known position, area and age (frames since seen) of every animal
        self._mem_pos: list[tuple[float, float] | None] = [None] * n
        self._mem_area: list[float] = [math.nan] * n
        self._mem_age: list[int] = [0] * n
        self._flip_votes = [0] * n
        self._history: list[list[tuple[float, float]]] = [[] for _ in range(n)]
        self._single_area: float | None = None
        self.contrast_votes = [0, 0]  # frames in which the animal was lighter / darker than the background
        if settings.method == "colour":
            hex_to_hsv(settings.target_colour)  # a clear error now rather than in the middle of a test
        for c in settings.identity_colour_list():
            hex_to_hsv(c)
        self.set_mask(arena_mask)

    def set_mask(self, arena_mask: np.ndarray | None):
        """The arena (pixels outside are ignored), e.g. after the apparatus moved during a live test."""
        settings = self.s
        self.mask = arena_mask
        if arena_mask is not None:
            ys, xs = np.nonzero(arena_mask)
            m = settings.arena_margin_px
            if len(xs):
                h, w = arena_mask.shape
                self.roi = (max(0, xs.min() - m), max(0, ys.min() - m), min(w, xs.max() + m + 1), min(h, ys.max() + m + 1))
            else:
                self.roi = (0, 0, arena_mask.shape[1], arena_mask.shape[0])
            if m > 0:
                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1, 2 * m + 1))
                self.mask = cv2.dilate(arena_mask, k)
        else:
            self.roi = None
        self._region_key = None
        self._region_box = None
        self._mask_c = self.mask  # the mask of the processed region (see _region)

    def set_background(self, bg_gray: np.ndarray):
        self.background = bg_gray
        self._bg_float = bg_gray.astype(np.float32)
        self._bg_owned = False
        self._bootstrapped = False
        self._bg_version += 1

    def set_motion_reference(self, frame: np.ndarray):
        """The frame just before the next one processed (when analysing every Nth frame): motion is then measured
        over one frame interval, as when every frame is analysed, rather than over N."""
        gray = to_gray(frame)
        box = self._region(gray.shape)
        self.prev_gray = (gray if box is None else gray[box[1]:box[3], box[0]:box[2]]).copy()
        self._prev_box = box

    # ------------------------------------------------------------------ region of the frame processed
    def _pad(self) -> int:
        """How far outside the arena a pixel can still influence detection inside it (blur, thin-structure eraser,
        opening and closing)."""
        s = self.s
        r = _odd(s.blur) // 2 if s.blur and s.blur > 1 else 0
        if s.erase_thin_px and int(s.erase_thin_px) > 0:
            r += 4 * (_odd(int(s.erase_thin_px) + 2) // 2)
        for k in (s.morph_open, s.morph_close):
            if k and k > 1:
                r += 2 * (_odd(k) // 2)
        return r + 2

    def _region(self, shape_hw) -> tuple[int, int, int, int] | None:
        """(x0, y0, x1, y1) of the frame processed for this arena — its box widened by :meth:`_pad`, so that
        detection inside the arena is exactly that of the whole frame — or None for the whole frame."""
        H, W = shape_hw[:2]
        if self.mask is None or self.roi is None or self.mask.shape[:2] != (H, W):
            self._mask_c = self.mask
            return None
        pad = self._pad()
        key = (H, W, pad)
        if self._region_key != key:
            x0, y0, x1, y1 = self.roi
            box = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
            self._region_box = None if box == (0, 0, W, H) else tuple(int(v) for v in box)
            b = self._region_box
            self._mask_c = self.mask if b is None else self.mask[b[1]:b[3], b[0]:b[2]]
            self._region_key = key
        return self._region_box

    def _processed_background(self, box) -> np.ndarray:
        """The background of the processed region blurred and with thin structures erased like the frame (cached
        until the background changes)."""
        s = self.s
        key = (box, self._bg_version, s.blur, s.erase_thin_px)
        if self._bgp_key != key:
            bg = self.background if box is None else self.background[box[1]:box[3], box[0]:box[2]]
            if s.blur and s.blur > 1:
                bg = cv2.GaussianBlur(bg, (_odd(s.blur), _odd(s.blur)), 0)
            self._bgp = self._erase_thin(bg)
            self._bgp_key = key
        return self._bgp

    def _own_background(self):
        """Copy a background shared with the caller before writing into it."""
        if not self._bg_owned:
            self.background = self.background.copy()
            self._bg_owned = True

    def _bootstrap(self, gray_full: np.ndarray):
        """Background tracking without a background: the first frame becomes the model (the "ghost" of an animal
        already present is absorbed once the animal moves away, see :meth:`_absorb_ghosts`)."""
        if self.s.method == "background" and self.background is None:
            self.set_background(gray_full.copy())
            self._bg_owned = True
            self._bootstrapped = True

    # ------------------------------------------------------------------
    def foreground(self, gray: np.ndarray, frame: np.ndarray | None = None) -> np.ndarray:
        """Foreground mask (255 = animal) of a whole frame."""
        self._bootstrap(gray)
        box = self._region(gray.shape)
        if box is None:
            return self._foreground(gray, frame, None)[0]
        x0, y0, x1, y1 = box
        fg = np.zeros(gray.shape[:2], np.uint8)
        fg[y0:y1, x0:x1] = self._foreground(gray[y0:y1, x0:x1], None if frame is None else frame[y0:y1, x0:x1],
                                            box)[0]
        return fg

    def _foreground(self, gray: np.ndarray, frame: np.ndarray | None, box) -> tuple[np.ndarray, np.ndarray | None]:
        """(foreground mask, blurred / erased image) of the processed region (gray and frame already cropped)."""
        s = self.s
        if s.method == "colour":
            if frame is None or frame.ndim != 3:
                return np.zeros(gray.shape[:2], np.uint8), None  # no colour in a greyscale frame
            f = frame
            if s.blur and s.blur > 1:
                f = cv2.GaussianBlur(f, (_odd(s.blur), _odd(s.blur)), 0)
            if box is None and self.roi is not None:  # the whole frame is processed: colour only the arena's box
                fg = np.zeros(gray.shape[:2], np.uint8)
                x0, y0, x1, y1 = self.roi
                fg[y0:y1, x0:x1] = colour_mask(f[y0:y1, x0:x1], s.target_colour, s.colour_tolerance,
                                               s.min_saturation)
            else:
                fg = colour_mask(f, s.target_colour, s.colour_tolerance, s.min_saturation)
            return self._clean(fg), None
        g = gray
        if s.blur and s.blur > 1:
            g = cv2.GaussianBlur(g, (_odd(s.blur), _odd(s.blur)), 0)
        g = self._erase_thin(g)
        if s.method == "threshold" or self.background is None:
            thr = s.threshold if s.threshold > 0 else self._otsu(g)
            if s.contrast == "light":
                fg = (g > thr).astype(np.uint8) * 255
            else:
                fg = (g < thr).astype(np.uint8) * 255
            return self._clean(fg), g
        bg = self._processed_background(box)
        g16 = g.astype(np.int16)
        b16 = bg.astype(np.int16)
        if s.contrast == "dark":
            diff = np.clip(b16 - g16, 0, 255).astype(np.uint8)
        elif s.contrast == "light":
            diff = np.clip(g16 - b16, 0, 255).astype(np.uint8)
        else:
            diff = cv2.absdiff(g, bg)
        thr = s.threshold if s.threshold > 0 else self._otsu(diff)
        fg = self._clean((diff > thr).astype(np.uint8) * 255)
        if s.contrast not in ("dark", "light"):
            self._vote_contrast(g, bg, fg)
        return fg, g

    def _vote_contrast(self, g: np.ndarray, bg: np.ndarray, fg: np.ndarray):
        """With contrast "auto": count the frames in which the detected pixels are lighter / darker than the
        background (see :meth:`animal_contrast`)."""
        if not cv2.countNonZero(fg):
            return
        d = cv2.mean(g, mask=fg)[0] - cv2.mean(bg, mask=fg)[0]
        if d > 0:
            self.contrast_votes[0] += 1
        elif d < 0:
            self.contrast_votes[1] += 1

    def animal_contrast(self) -> str:
        """"lighter" or "darker" (than the apparatus floor): the contrast setting, or with "auto" what most frames
        showed; "" for colour tracking or when unknown."""
        s = self.s
        if s.method == "colour":
            return ""
        if s.contrast == "light":
            return "lighter"
        if s.contrast == "dark" or s.method == "threshold":
            return "darker"
        light, dark = self.contrast_votes
        return "" if light == dark else ("lighter" if light > dark else "darker")

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
        mask = self._mask_c
        vals = img[mask > 0] if mask is not None else img.ravel()
        if vals.size == 0:
            return 25
        t, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return max(float(t), 8.0)

    def _clean(self, fg: np.ndarray) -> np.ndarray:
        s = self.s
        if self._mask_c is not None:
            fg = cv2.bitwise_and(fg, self._mask_c)
        if s.morph_open and s.morph_open > 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(s.morph_open),) * 2)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k)
        if s.morph_close and s.morph_close > 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(s.morph_close),) * 2)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k)
        return fg

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, full_mask: bool = True) -> tuple[list[Detection], np.ndarray]:
        """Detect animals in a frame. Returns (detections, foreground mask of the whole frame — or, with full_mask
        False, of the region processed only, which saves a frame-sized copy per arena when it is not used)."""
        if self.s.method == "background" and self.background is None:
            self._bootstrap(to_gray(frame))
        shape = frame.shape[:2]
        box = self._region(shape)
        if box is None:
            gray, crop, off = to_gray(frame), frame, (0, 0)
        else:
            x0, y0, x1, y1 = box
            crop, off = frame[y0:y1, x0:x1], (x0, y0)
            gray = to_gray(crop)  # only the region is converted
        fg, g = self._foreground(gray, crop, box)
        n = max(1, self.s.n_animals)
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=off)
        blobs = []
        for c in contours:
            a = cv2.contourArea(c)
            if a < self.s.min_area_px:
                continue
            if self.s.max_area_px and a > self.s.max_area_px:
                continue
            blobs.append((a, c))
        if blobs and g is not None and self.s.method == "background" and \
                (self.s.background == "adaptive" or self._bootstrapped):
            blobs = self._absorb_ghosts(blobs, gray, g, fg, box)
        blobs.sort(key=lambda b: -b[0])
        if n == 1:
            blobs = blobs[:1]
        else:
            self._learn_single_area(blobs, n)
            blobs = self._split_merged(blobs, n)
        dets = [self._describe(c, a, shape) for a, c in blobs[:n]]
        if n == 1 and dets and self._single_area is None:
            self._single_area = dets[0].area
        colours = self.s.identity_colour_list()
        if n > 1 and len(colours) >= n and frame.ndim == 3:
            dets = self._assign_by_colour(frame, dets, n, colours[:n])
        else:
            dets = self._assign(dets, n)
        posed = self._apply_pose(frame, dets) if self.pose is not None and frame.ndim == 3 else set()
        self._head_tail_consistency(dets, skip=posed)
        self._motion(gray, dets, box)
        if self.s.background == "adaptive" and self._bg_float is not None and self.s.method == "background":
            # update the background only where no animal is present (and only the processed region: it is the only
            # part of the model this arena ever uses)
            self._own_background()
            upd = (cv2.dilate(fg, np.ones((15, 15), np.uint8)) == 0).astype(np.uint8)
            if box is None:
                cv2.accumulateWeighted(gray.astype(np.float32), self._bg_float, self.s.adaptive_rate, mask=upd)
                self.background = self._bg_float.astype(np.uint8)
            else:
                x0, y0, x1, y1 = box
                sub = np.ascontiguousarray(self._bg_float[y0:y1, x0:x1])
                cv2.accumulateWeighted(gray.astype(np.float32), sub, self.s.adaptive_rate, mask=upd)
                self._bg_float[y0:y1, x0:x1] = sub
                self.background[y0:y1, x0:x1] = sub.astype(np.uint8)
            self._bg_version += 1
        self.prev_gray = gray.copy() if box is not None else gray
        self._prev_box = box
        self.prev = dets
        self._remember(dets)
        if box is not None and full_mask:
            full = np.zeros(shape, np.uint8)
            full[box[1]:box[3], box[0]:box[2]] = fg
            fg = full
        return dets, fg

    # ------------------------------------------------------------------
    def _absorb_ghosts(self, blobs, gray, g, fg, box):
        """Remove "ghosts" from a background learnt from the live image (adaptive, or the first frame taken as the
        background): where the animal sat when the model was taken, the empty floor now differs from the model and
        is detected as a blob the size of the animal that would never fade, since the model is only updated where
        no animal is detected.

        A ghost is told from an animal by its edges: along the blob's outline the model has the sharp edge of the
        animal it contains while the image shows flat floor; for a real animal it is the other way round.  Static
        blobs whose outline is at least twice as sharp in the model as in the image are copied into the model and
        dropped.  gray / g / fg are the processed region (raw, blurred, foreground; fg is cleared in place)."""
        bg = self._processed_background(box)
        ox, oy = (0, 0) if box is None else (box[0], box[1])
        H, W = gray.shape[:2]
        keep = []
        absorbed = False
        for a, c in blobs:
            x, y, w, h = cv2.boundingRect(c)
            x, y = x - ox, y - oy
            p = 9
            px0, py0, px1, py1 = max(0, x - p), max(0, y - p), min(W, x + w + p), min(H, y + h + p)
            local = c - [ox + px0, oy + py0]
            band = np.zeros((py1 - py0, px1 - px0), np.uint8)
            cv2.drawContours(band, [local], -1, 255, 3)

            def edge(img):
                patch = img[py0:py1, px0:px1].astype(np.float32)
                gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
                return cv2.mean(cv2.magnitude(gx, gy), mask=band)[0]

            e_img, e_bg = edge(g), edge(bg)
            ghost = e_bg > 8.0 and e_img < 0.5 * e_bg
            inside = np.zeros_like(band)
            cv2.drawContours(inside, [local], -1, 255, -1)
            if ghost and self.prev_gray is not None and self._prev_box == box:  # and nothing moves in it
                moved = cv2.absdiff(gray[py0:py1, px0:px1], self.prev_gray[py0:py1, px0:px1]) > self.s.motion_threshold
                ghost = np.count_nonzero(moved & (inside > 0)) <= 0.02 * max(1, np.count_nonzero(inside))
            if not ghost:
                keep.append((a, c))
                continue
            # copy the image into the model over the blob and the margin the adaptive update leaves out
            self._own_background()
            region = cv2.dilate(inside, np.ones((15, 15), np.uint8)) > 0
            sl = (slice(oy + py0, oy + py1), slice(ox + px0, ox + px1))
            src = gray[py0:py1, px0:px1]
            self.background[sl][region] = src[region]
            self._bg_float[sl][region] = src[region]
            cv2.drawContours(fg[py0:py1, px0:px1], [local], -1, 0, -1)
            absorbed = True
        if absorbed:
            self._bg_version += 1
        return keep

    def _learn_single_area(self, blobs, n: int):
        """With several animals: the area of one animal, learnt from the frames in which every animal is a blob of
        its own (running mean of their median area)."""
        if len(blobs) < n:
            return
        a = float(np.median([b[0] for b in blobs[:n]]))
        self._single_area = a if self._single_area is None else 0.9 * self._single_area + 0.1 * a

    def _split_merged(self, blobs, n, fg=None):
        """If fewer blobs than animals, split the largest blob(s) with k-means.  Once the area of one animal is
        known only blobs clearly larger (1.5×) are split: an animal hidden (in a shelter, under a lid) does not cut
        the visible one in two."""
        if not blobs:
            return blobs
        areas = [a for a, _ in blobs]
        if len(blobs) >= n:
            return blobs[:n]
        learnt = self._single_area is not None
        single = self._single_area or (np.median(areas) if len(areas) > 1 else areas[0] / n)
        out = list(blobs)
        while len(out) < n:
            out.sort(key=lambda b: -b[0])
            a, c = out[0]
            ratio = a / max(single, 1)
            if learnt and ratio < 1.5:
                break
            k = int(min(n - len(out) + 1, max(2, round(ratio))))
            if k < 2:
                break
            x0, y0, w, h = cv2.boundingRect(c)
            m = np.zeros((h, w), np.uint8)
            cv2.drawContours(m, [c - [x0, y0]], -1, 255, -1)
            pts = (np.column_stack(np.nonzero(m)[::-1]) + [x0, y0]).astype(np.float32)
            if len(pts) < k * 5:
                break
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
            _, labels, _ = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
            new = []
            for j in range(k):
                sub = np.zeros((h, w), np.uint8)
                p = pts[labels.ravel() == j].astype(int)
                sub[p[:, 1] - y0, p[:, 0] - x0] = 255
                cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=(x0, y0))
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
        if self.s.record_outline:
            d.outline = simplify_outline(contour)
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

    def _remember(self, dets: list[Detection]):
        """Update the identity memory: last-known position / area of every animal seen, age of the others."""
        for i in range(len(self._mem_pos)):
            d = dets[i] if i < len(dets) else None
            if d is not None and d.detected and math.isfinite(d.x) and math.isfinite(d.y):
                self._mem_pos[i], self._mem_age[i] = (d.x, d.y), 0
                if math.isfinite(d.area):
                    self._mem_area[i] = d.area
            elif self._mem_pos[i] is not None:
                self._mem_age[i] += 1
                if self._mem_age[i] > self.identity_memory_frames:
                    self._mem_pos[i] = None

    def _gate_px(self, i: int) -> float:
        """How far (px) from its last-known position animal i may be found: four body lengths, plus one per frame
        it has not been seen."""
        a = self._single_area or self._mem_area[i]
        body = max(10.0, math.sqrt(a)) if a and math.isfinite(a) else 10.0
        return body * (4 + self._mem_age[i])

    def _memory_cost(self, n: int, dets: list[Detection]) -> np.ndarray:
        """Assignment cost of animal i to detection j: the distance from the animal's last-known position (kept
        while it is unseen, see :meth:`_remember`); 1e5 (no preference) without a position or beyond the gate."""
        cost = np.full((n, len(dets)), 1e5)
        for i in range(n):
            p = self._mem_pos[i]
            if p is None:
                continue
            gate = self._gate_px(i)
            for j, d in enumerate(dets):
                dist = math.hypot(p[0] - d.x, p[1] - d.y)
                # beyond the gate: no better than an animal without memory (the tiny term still orders them)
                cost[i, j] = dist if dist <= gate else 1e5 + 1e-3 * dist
        return cost

    def _assign(self, dets: list[Detection], n: int) -> list[Detection]:
        """Keep identities stable across frames: Hungarian assignment on the distance to each animal's last-known
        position, remembered while the animal is not detected (e.g. one empty frame, a hidden animal) so that
        animals are not swapped when they reappear."""
        out = [Detection() for _ in range(n)]
        if n == 1:
            if dets:
                out[0] = dets[0]
            return out
        if all(p is None for p in self._mem_pos[:n]):
            for i, d in enumerate(dets[:n]):
                out[i] = d
            return out
        cost = self._memory_cost(n, dets)
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
        for i in range(n):  # tie-break blobs without a visible mark by distance to the last-known position
            p = self._mem_pos[i]
            if p is not None:
                for j, d in enumerate(dets):
                    cost[i, j] += 1e-6 * math.hypot(p[0] - d.x, p[1] - d.y)
        rows, cols = linear_sum_assignment(cost)
        out = [Detection() for _ in range(n)]
        for r, c in zip(rows, cols):
            out[r] = dets[c]
        return out

    def _apply_pose(self, frame: np.ndarray, dets: list[Detection]) -> set[int]:
        """Refine head / centre / tail base with the pose model. Returns indices whose head/tail came from it.

        The crop is the blob's bounding box; if the blob was lost (poor contrast) the last box is re-used and a
        confident pose detection keeps the animal tracked — for at most pose_only_max_frames frames in a row, so
        that a model confidently "seeing" an animal in an empty box cannot keep a lost animal forever."""
        idx, boxes = [], []
        H, W = frame.shape[:2]
        for i, d in enumerate(dets):
            if d.detected and d.contour is not None:
                x, y, w, h = cv2.boundingRect(d.contour)
                box = (x, y, x + w, y + h)
                self._last_box[i], self._last_area[i] = box, d.area
                self._pose_only[i] = 0
            else:
                if self._pose_only[i] >= self.pose_only_max_frames:
                    self._last_box[i] = None  # lost: found again only by its blob
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
                self._pose_only[i] += 1
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

    def _motion(self, gray: np.ndarray, dets: list[Detection], box=None):
        """Pixel change since the previous frame (gray is the processed region, box its place in the frame).  The
        previous frame is the one before this one even when only every Nth frame is analysed
        (:meth:`set_motion_reference`), so motion — and freezing — do not depend on the analysis frame step."""
        if self.prev_gray is None or self._prev_box != box or self.prev_gray.shape != gray.shape:
            for d in dets:
                d.motion = 0.0 if d.detected else math.nan
            return
        diff = cv2.absdiff(gray, self.prev_gray)
        changed = (diff > self.s.motion_threshold).astype(np.uint8)
        if self._mask_c is not None:
            changed &= (self._mask_c > 0).astype(np.uint8)
        if self.s.n_animals == 1:
            total = float(changed.sum())
            for d in dets:
                d.motion = total
            return
        ox, oy = (0, 0) if box is None else (box[0], box[1])
        for d in dets:
            if not d.detected or d.contour is None:
                continue
            x, y, w, h = cv2.boundingRect(d.contour)
            x, y = x - ox, y - oy
            pad = int(0.3 * max(w, h))
            y0, y1 = max(0, y - pad), min(gray.shape[0], y + h + pad)
            x0, x1 = max(0, x - pad), min(gray.shape[1], x + w + pad)
            d.motion = float(changed[y0:y1, x0:x1].sum())


class TrackBuilder:
    """The columns of a track (track.COLUMNS, then the body outlines), one detection at a time.  Rows are only ever
    appended, so a copy of the first n rows (:meth:`snapshot`) is safe while another thread keeps adding."""

    def __init__(self, cols: dict | None = None):
        cols = cols or {}
        self.cols: dict[str, list] = {c: list(cols.get(c) or []) for c in COLUMNS}
        # older side files have no outlines: those frames have none
        self.cols["outline"] = list(cols.get("outline") or []) + [None] * (len(self.cols["t"])
                                                                          - len(cols.get("outline") or []))

    def __len__(self) -> int:
        return len(self.cols["t"])

    def add(self, t: float, d: Detection):
        self.cols["outline"].append(d.outline)
        self.cols["t"].append(t)
        for c in COLUMNS[1:]:
            self.cols[c].append(getattr(d, c))

    def snapshot(self, n: int | None = None) -> dict[str, list]:
        n = len(self.cols[COLUMNS[-1]]) if n is None else n  # the last column is appended last
        return {c: v[:n] for c, v in self.cols.items()}

    def build(self, fps: float) -> Track:
        cols = self.snapshot()
        outline = cols.pop("outline")
        return Track(**cols, fps=fps, outline=outline if any(o is not None for o in outline) else None)


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
    # one estimator per distinct (model, device): jobs may use different pose models
    poses: dict[tuple, object] = {}
    for j in jobs:
        if j.settings.body_parts == "pose":
            key = (j.settings.pose_model, j.settings.pose_device)
            if key not in poses:
                poses[key] = pose_estimator(j.settings, threads=decode_threads)

    def pose_of(settings: DetectionSettings):
        return poses.get((settings.pose_model, settings.pose_device)) if settings.body_parts == "pose" else None

    pose = next(iter(poses.values()), None)
    with VideoSource(video_path) as v:
        W, H = v.width, v.height
        trackers = []
        bg_cache: dict[tuple, np.ndarray] = {}
        for job in jobs:
            mask = job.mask
            if mask is None and job.apparatus is not None:
                mask = job.apparatus.arena_or_bounds().mask((H, W))
            tr = ArenaTracker(job.settings, mask, pose=pose_of(job.settings))
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
        start, end = _frame_window(s0, fps, v.frame_count)
        if end <= 0:  # neither a frame count nor a duration: read to the end of the video
            end = 10**12
        step = max(1, int(s0.frame_step))
        builders = [[TrackBuilder() for _ in range(max(1, j.settings.n_animals))] for j in jobs]
        total = max(1, end - start)
    # colour is only needed for the pose model, colour tracking and preview callbacks; otherwise decode to grey
    colour = any(j.settings.needs_colour() for j in jobs)
    reader = FrameReader(video_path, start, gray=pose is None and frame_callback is None and not colour,
                         threads=decode_threads)
    with reader:
        for i, frame in reader:
            if i >= end:
                break
            if step > 1 and (i - start) % step == step - 1:
                for tr in trackers:  # the frame before the next analysed one: motion over one frame interval
                    tr.set_motion_reference(frame)
            if (i - start) % step == 0:
                t = (i - start) / fps
                all_dets = []
                for tr, bl in zip(trackers, builders):
                    dets, _ = tr.process(frame, full_mask=False)
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
    for job, trk, bl in zip(jobs, trackers, builders):
        tracks = []
        for b in bl:
            tr = b.build(fps / step)
            tr.meta["video"] = str(video_path)
            tr.meta["video_start_s"] = s0.start_time_s
            if trk.animal_contrast():
                tr.meta["animal_contrast"] = trk.animal_contrast()
            tr.meta["decoder"] = reader.backend
            est = pose_of(job.settings)
            if est is not None:
                tr.meta["pose_model"] = job.settings.pose_model
                tr.meta["pose_device"] = est.provider
            tracks.append(postprocess(tr, job.settings))
        out.append(tracks)
    return out


APPARATUS_BGR = (31, 138, 255)  # ANY-maze style orange apparatus outlines
ANIMAL_COLORS = [(0, 200, 255), (255, 0, 200), (0, 255, 0), (255, 255, 0), (0, 128, 255), (200, 120, 255)]  # BGR, one per animal
TRAIL_BGR = (214, 120, 37)  # live images: blue trail, green centre, orange head
CENTRE_BGR = (60, 200, 60)
HEAD_BGR = (31, 138, 255)
OUTLINE_BGR = (230, 230, 60)  # whole-body outline (cyan)
BEAM_BGR = (80, 230, 255)  # orientation "flashlight beam" (light yellow)
BEAM_HALF_ANGLE = 20.0  # degrees either side of the head direction


def hex_to_bgr(h: str | None) -> tuple[int, int, int]:
    h = (h or "#ffffff").lstrip("#")
    try:
        return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)
    except ValueError:
        return (255, 255, 255)


def heading_of(d: Detection) -> float:
    """The direction the animal faces (degrees, 0 = +x, clockwise as y points down): tail → head, else the body
    orientation; NaN when unknown."""
    if math.isfinite(d.hx) and math.isfinite(d.tx) and (d.hx, d.hy) != (d.tx, d.ty):
        return math.degrees(math.atan2(d.hy - d.ty, d.hx - d.tx))
    return d.angle if math.isfinite(d.angle) else math.nan


def draw_beam(img: np.ndarray, d: Detection, alpha: float = 0.35, half_angle: float = BEAM_HALF_ANGLE) -> bool:
    """The animal's orientation as a translucent "flashlight beam" wedge from its head, in place (ANY-maze's shaded
    orientation area): half_angle degrees either side of the direction it faces.  Returns False when the
    orientation is unknown."""
    ang = heading_of(d)
    if not math.isfinite(ang) or not math.isfinite(d.x):
        return False
    ox, oy = (d.hx, d.hy) if math.isfinite(d.hx) else (d.x, d.y)
    body = math.hypot(d.hx - d.tx, d.hy - d.ty) if math.isfinite(d.hx) and math.isfinite(d.tx) else math.nan
    if not math.isfinite(body) or body < 2:
        body = math.sqrt(d.area) if d.area and math.isfinite(d.area) else 20.0
    length = max(3.0 * body, 0.06 * img.shape[1])
    pts = [(ox, oy)] + [(ox + length * math.cos(math.radians(a)), oy + length * math.sin(math.radians(a)))
                        for a in np.linspace(ang - half_angle, ang + half_angle, 9)]
    poly = np.round(np.array(pts)).astype(np.int32)
    h, w = img.shape[:2]
    x0, y0 = np.clip(poly.min(axis=0), 0, [w, h])
    x1, y1 = np.clip(poly.max(axis=0) + 1, 0, [w, h])
    if x1 <= x0 or y1 <= y0:
        return True
    roi = img[y0:y1, x0:x1]
    layer = roi.copy()
    cv2.fillPoly(layer, [poly - [x0, y0]], BEAM_BGR, cv2.LINE_AA)
    img[y0:y1, x0:x1] = cv2.addWeighted(layer, alpha, roi, 1.0 - alpha, 0)
    return True


def draw_tracking(frame: np.ndarray, dets: Sequence[Detection], trail=None, copy: bool = True,
                  beam: bool | float = False) -> np.ndarray:
    """The animal's position (green centre, orange head, cyan body outline) and trail drawn on a BGR copy of the frame (live camera
    images: the GUI draws the apparatus on top of the image, so it stays sharp at any zoom).  beam: the animal's
    orientation as a "flashlight beam" from its head (:func:`draw_beam`); a number is the beam's half angle."""
    img = frame
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif copy:
        img = img.copy()
    s = max(1, int(round(img.shape[1] / 480)))
    if trail is not None and len(trail) > 1:
        pts = np.array([p for p in trail if math.isfinite(p[0]) and math.isfinite(p[1])], np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, TRAIL_BGR, s, cv2.LINE_AA)
    for d in dets or []:
        if not d.detected or not math.isfinite(d.x):
            continue
        if beam:
            draw_beam(img, d, half_angle=BEAM_HALF_ANGLE if beam is True else float(beam))
        if d.outline is not None and len(d.outline) > 2:
            cv2.polylines(img, [d.outline.reshape(-1, 1, 2)], True, OUTLINE_BGR, s, cv2.LINE_AA)
        cv2.circle(img, (int(d.x), int(d.y)), 2 + 2 * s, CENTRE_BGR, -1, cv2.LINE_AA)
        if math.isfinite(d.hx) and math.isfinite(d.hy):
            cv2.circle(img, (int(d.hx), int(d.hy)), 1 + 2 * s, HEAD_BGR, -1, cv2.LINE_AA)
    return img


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
            c = zone_color if zone_color is not None else hex_to_bgr(z.color)
            cv2.polylines(img, [np.round(z.shape.polygon()).astype(np.int32)], True, c, 1, cv2.LINE_AA)
        for p in apparatus.points:
            cv2.circle(img, (int(p.x), int(p.y)), 4, hex_to_bgr(p.color), -1, cv2.LINE_AA)
        for l in apparatus.lines:
            cv2.line(img, (int(l.x1), int(l.y1)), (int(l.x2), int(l.y2)), hex_to_bgr(l.color), 1, cv2.LINE_AA)
    if trail is not None and len(trail) > 1:
        pts = np.array([p for p in trail if np.isfinite(p[0])], np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (255, 160, 0), 1, cv2.LINE_AA)
    for i, d in enumerate(dets):
        if not d.detected:
            continue
        col = ANIMAL_COLORS[i % len(ANIMAL_COLORS)]
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
