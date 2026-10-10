"""Lens distortion correction: straightening the image of a wide-angle (fish-eye / barrel) lens before tracking.

A :class:`LensCorrection` is either a single *barrel strength* (the radial coefficient k1 of OpenCV's lens model,
centred in the image) or the result of a checkerboard calibration (``cv2.calibrateCamera`` over at least
:data:`MIN_VIEWS` views of a printed checkerboard: camera matrix and k1 k2 p1 p2 k3).  It is kept per camera (with
the camera options, see core.camera) and per video test (``Test.undistort``).  The corrected image has the size of
the original, so the apparatus is drawn on it and tracked in it as on any other image; the undistortion maps are
computed once per lens and frame size (:func:`undistort_maps`) and every frame is then a single ``cv2.remap``."""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

import cv2
import numpy as np

MIN_VIEWS = 8  # checkerboard views needed for a calibration
DEFAULT_PATTERN = (9, 6)  # inner corners (columns, rows) of OpenCV's usual printed checkerboard
BARREL_K1_MAX = 0.14  # |k1| at barrel strength 100: with k1 alone, stronger corrections fold the image corners
METHODS = ("barrel", "checkerboard")


@dataclass
class LensCorrection:
    """How to undo the distortion of a lens.

    method "barrel": ``strength`` (−100…100, positive for barrel, negative for pincushion distortion) sets k1 with
    the optical centre in the middle of the image and the focal length half its diagonal, whatever its size.
    method "checkerboard": ``camera`` [fx, fy, cx, cy] and ``coefficients`` [k1, k2, p1, p2, k3] were measured on
    images of ``size`` [w, h] (scaled to other sizes); ``views`` and ``error_px`` (RMS reprojection error) describe
    the calibration.  ``alpha`` 0 fills the corrected image (its edges are cropped), 1 keeps all of the original
    image (black corners appear)."""

    method: str = ""  # "" (none) | "barrel" | "checkerboard"
    strength: float = 0.0
    camera: list = field(default_factory=list)
    coefficients: list = field(default_factory=list)
    size: list = field(default_factory=list)
    alpha: float = 0.0
    views: int = 0
    error_px: float = 0.0

    def to_dict(self) -> dict:
        """The stored form ({} when there is no correction)."""
        if self.is_identity:
            return {}
        d = asdict(self)
        if self.method == "barrel":
            for k in ("camera", "coefficients", "size", "views", "error_px"):
                d.pop(k)
        else:
            d.pop("strength")
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "LensCorrection":
        """From the stored form; anything malformed gives no correction."""
        if isinstance(d, LensCorrection):
            return d
        d = d if isinstance(d, dict) else {}
        try:
            lens = cls(method=str(d.get("method") or ""), strength=float(d.get("strength") or 0.0),
                       camera=[float(v) for v in d.get("camera") or []],
                       coefficients=[float(v) for v in d.get("coefficients") or []],
                       size=[int(v) for v in d.get("size") or []], alpha=float(d.get("alpha") or 0.0),
                       views=int(d.get("views") or 0), error_px=float(d.get("error_px") or 0.0))
        except (TypeError, ValueError):
            return cls()
        if lens.method not in METHODS:
            return cls()
        lens.strength = float(np.clip(lens.strength, -100.0, 100.0))
        lens.alpha = float(np.clip(lens.alpha, 0.0, 1.0))
        if lens.method == "checkerboard":
            ok = (len(lens.camera) == 4 and len(lens.size) == 2 and min(lens.size) > 0 and lens.camera[0] > 0
                  and lens.camera[1] > 0 and 1 <= len(lens.coefficients) <= 14
                  and all(math.isfinite(v) for v in lens.camera + lens.coefficients))
            if not ok:
                return cls()
        return lens

    @classmethod
    def barrel(cls, strength: float, alpha: float = 0.0) -> "LensCorrection":
        return cls.from_dict({"method": "barrel", "strength": strength, "alpha": alpha})

    @property
    def is_identity(self) -> bool:
        if self.method == "barrel":
            return abs(self.strength) < 1e-9
        if self.method == "checkerboard":
            return not self.camera
        return True

    @property
    def k1(self) -> float:
        if self.method == "barrel":
            return -self.strength / 100.0 * BARREL_K1_MAX
        return self.coefficients[0] if self.coefficients else 0.0

    def key(self) -> tuple:
        """Identifies the maps (equal corrections share them)."""
        return (self.method, round(self.strength, 6), tuple(round(v, 9) for v in self.camera),
                tuple(round(v, 12) for v in self.coefficients), tuple(self.size), round(self.alpha, 6))

    def matrices(self, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
        """(camera matrix K, distortion coefficients D) for frames of w × h pixels."""
        if self.method == "checkerboard" and self.camera:
            sx, sy = w / self.size[0], h / self.size[1]
            fx, fy, cx, cy = self.camera
            K = np.array([[fx * sx, 0, cx * sx], [0, fy * sy, cy * sy], [0, 0, 1]], np.float64)
            D = np.array(self.coefficients, np.float64).reshape(1, -1)
            return K, D
        f = math.hypot(w, h) / 2.0
        K = np.array([[f, 0, (w - 1) / 2.0], [0, f, (h - 1) / 2.0], [0, 0, 1]], np.float64)
        D = np.array([[self.k1, 0.0, 0.0, 0.0, 0.0]], np.float64)
        return K, D

    def apply(self, frame: np.ndarray) -> np.ndarray:
        """The corrected frame (same size; grey or colour)."""
        if frame is None or self.is_identity:
            return frame
        h, w = frame.shape[:2]
        m1, m2 = undistort_maps(self, w, h)
        return cv2.remap(frame, m1, m2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def describe(self) -> str:
        if self.is_identity:
            return "None"
        if self.method == "barrel":
            kind = "barrel" if self.strength > 0 else "pincushion"
            return f"{kind} strength {abs(self.strength):g}"
        return f"checkerboard calibration, {self.views} views, error {self.error_px:.2f} px"


def lens_from(d) -> LensCorrection | None:
    """A LensCorrection from its stored form, or None when there is none."""
    lens = LensCorrection.from_dict(d)
    return None if lens.is_identity else lens


# ------------------------------------------------------------------ maps (cached)
_MAPS: OrderedDict[tuple, tuple[np.ndarray, np.ndarray]] = OrderedDict()
_MAPS_LOCK = threading.Lock()
_MAPS_KEEP = 8


def _matrices(lens: LensCorrection, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """K, D and the camera matrix of the corrected image: scaled so that it is filled (alpha 0) or shows all of the
    original image (alpha 1)."""
    K, D = lens.matrices(w, h)
    newK, _ = cv2.getOptimalNewCameraMatrix(K, D, (w, h), lens.alpha, (w, h), centerPrincipalPoint=False)
    if not np.all(np.isfinite(newK)) or newK[0, 0] <= 0:
        newK = K
    return K, D, newK


def undistort_maps(lens: LensCorrection, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """The remap maps of a correction for frames of w × h pixels, computed once (several sources and threads share
    the cache)."""
    key = (lens.key(), int(w), int(h))
    with _MAPS_LOCK:
        maps = _MAPS.get(key)
        if maps is not None:
            _MAPS.move_to_end(key)
            return maps
    K, D, newK = _matrices(lens, w, h)
    m1, m2 = cv2.initUndistortRectifyMap(K, D, None, newK, (w, h), cv2.CV_16SC2)
    with _MAPS_LOCK:
        _MAPS[key] = (m1, m2)
        while len(_MAPS) > _MAPS_KEEP:
            _MAPS.popitem(last=False)
    return m1, m2


def corrected_point(lens: LensCorrection, w: int, h: int, x: float, y: float) -> tuple[float, float]:
    """Where a point of the original image is in the corrected one."""
    K, D, newK = _matrices(lens, w, h)
    p = cv2.undistortPoints(np.array([[[x, y]]], np.float64), K, D, P=newK)
    return float(p[0, 0, 0]), float(p[0, 0, 1])


# ------------------------------------------------------------------ sources
class CorrectedSource:
    """A VideoSource-like object (read / seek / frame_at / release, fps, width, height, frame_count) whose frames are
    corrected by ``lens``."""

    def __init__(self, src, lens: LensCorrection):
        self.src, self.lens = src, lens
        for a in ("source", "is_camera", "fps", "width", "height", "frame_count"):
            if hasattr(src, a):
                setattr(self, a, getattr(src, a))

    @property
    def pos(self) -> int:
        return getattr(self.src, "pos", 0)

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if getattr(self, "fps", 0) else 0.0

    def read(self):
        ok, f = self.src.read()
        return (ok, self.lens.apply(f)) if ok else (ok, f)

    def seek(self, index: int):
        self.src.seek(index)

    def frame_at(self, index: int):
        return self.lens.apply(self.src.frame_at(index))

    def frame_at_time(self, t: float):
        return self.frame_at(int(round(t * self.fps)))

    def release(self):
        self.src.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()


def corrected(src, lens: LensCorrection | dict | None):
    """``src`` with the correction applied to its frames (``src`` itself without a correction)."""
    lens = lens_from(lens) if not isinstance(lens, LensCorrection) else (None if lens.is_identity else lens)
    return src if lens is None else CorrectedSource(src, lens)


# ------------------------------------------------------------------ checkerboard calibration
def find_checkerboard(frame: np.ndarray, pattern: Sequence[int] = DEFAULT_PATTERN) -> np.ndarray | None:
    """The inner corners (N × 1 × 2, sub-pixel) of a checkerboard of ``pattern`` (columns, rows) inner corners, or
    None when it is not (entirely) in the image."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    size = (int(pattern[0]), int(pattern[1]))
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, size, flags=flags)
    if ok:
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.01)
        return cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), crit)
    if hasattr(cv2, "findChessboardCornersSB"):
        ok, corners = cv2.findChessboardCornersSB(gray, size, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        if ok:
            return corners.reshape(-1, 1, 2).astype(np.float32)
    return None


def distinct_view(corners: np.ndarray, kept: Sequence[np.ndarray], size: Sequence[int], min_shift: float = 0.03
                  ) -> bool:
    """The checkerboard is somewhere else (or tilted differently) than in every kept view: its corners moved by more
    than ``min_shift`` of the image diagonal on average.  Near-identical views add nothing to a calibration."""
    diag = math.hypot(*size)
    c = corners.reshape(-1, 2)
    return all(float(np.mean(np.linalg.norm(c - k.reshape(-1, 2), axis=1))) > min_shift * diag for k in kept)


def calibrate_checkerboard(views: Sequence[np.ndarray], size: Sequence[int], pattern: Sequence[int] = DEFAULT_PATTERN,
                           alpha: float = 0.0) -> LensCorrection:
    """A correction from the checkerboard corners of several views (``cv2.calibrateCamera``) of images of ``size``
    [w, h]; at least MIN_VIEWS views, showing the board in different places and at different angles."""
    views = [np.asarray(v, np.float32).reshape(-1, 1, 2) for v in views]
    n = int(pattern[0]) * int(pattern[1])
    views = [v for v in views if len(v) == n]
    if len(views) < MIN_VIEWS:
        raise ValueError(f"A calibration needs at least {MIN_VIEWS} views of the checkerboard ({len(views)} so far): "
                         "show it in other places and at other angles")
    grid = np.zeros((n, 3), np.float32)
    grid[:, :2] = np.mgrid[0:int(pattern[0]), 0:int(pattern[1])].T.reshape(-1, 2)
    w, h = int(size[0]), int(size[1])
    # k3 stays 0: fitted, it bends the image corners that few views reach (OpenCV's advice for all but fish-eye
    # lenses); k1 and k2 correct the barrel distortion of the wide-angle lenses used over arenas
    rms, K, D, _r, _t = cv2.calibrateCamera([grid] * len(views), views, (w, h), None, None,
                                            flags=cv2.CALIB_FIX_K3)
    D = np.asarray(D, np.float64).ravel()[:5]
    if not (np.all(np.isfinite(K)) and np.all(np.isfinite(D))) or K[0, 0] <= 0:
        raise ValueError("The calibration failed: use sharper views of the whole checkerboard")
    return LensCorrection(method="checkerboard", camera=[float(K[0, 0]), float(K[1, 1]), float(K[0, 2]),
                                                         float(K[1, 2])],
                          coefficients=[float(v) for v in D], size=[w, h], alpha=float(np.clip(alpha, 0, 1)),
                          views=len(views), error_px=float(rms))


def checkerboard_views_in_video(path: str, pattern: Sequence[int] = DEFAULT_PATTERN, samples: int = 80,
                                max_views: int = 25, progress: Callable[[float], None] | None = None,
                                should_stop: Callable[[], bool] | None = None
                                ) -> tuple[list[np.ndarray], tuple[int, int], list[int]]:
    """Checkerboard views found in frames evenly spread through a video (a film of the board moved around under the
    camera): (corners of each distinct view, frame size (w, h), frame numbers of the views)."""
    from .video import VideoSource

    views: list[np.ndarray] = []
    frames: list[int] = []
    with VideoSource(path) as v:
        size = (int(v.width), int(v.height))
        count = int(v.frame_count or 0)
        idx = np.unique(np.linspace(0, max(0, count - 1), max(1, samples)).astype(int)) if count else [0]
        for k, i in enumerate(idx):
            if should_stop is not None and should_stop():
                break
            f = v.frame_at(int(i))
            if f is not None:
                c = find_checkerboard(f, pattern)
                if c is not None and distinct_view(c, views, size):
                    views.append(c)
                    frames.append(int(i))
            if progress is not None:
                progress((k + 1) / len(idx))
            if len(views) >= max_views:
                break
    return views, size, frames
