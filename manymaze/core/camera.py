"""Camera image options for live testing: region (crop), digital zoom / pan, rotation, flip, merging two
cameras into one image, frame-rate pacing and per-camera persistence in ``project.settings_extra``."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass
class CameraView:
    """Geometric transform applied to every frame of a source, in this order: rotate, flip, crop, zoom / pan.

    crop: [x, y, w, h] in the rotated / flipped image (None = whole image).  zoom ≥ 1 magnifies the centre of the
    cropped region around ``pan`` (fractions of the cropped image) and keeps its size, so apparatus drawn on the
    transformed image stay valid as long as the view is unchanged.
    """

    crop: list | None = None
    zoom: float = 1.0
    pan: list = field(default_factory=lambda: [0.5, 0.5])
    rotate: int = 0  # 0 | 90 | 180 | 270 (clockwise)
    flip: str = ""  # "" | "h" | "v" | "hv"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "CameraView":
        d = d or {}
        v = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        v.rotate = int(v.rotate) % 360 if int(v.rotate) % 90 == 0 else 0
        v.zoom = max(1.0, float(v.zoom or 1.0))
        v.pan = [float(np.clip(p, 0.0, 1.0)) for p in (list(v.pan) + [0.5, 0.5])[:2]]
        if v.crop is not None:
            v.crop = [int(round(c)) for c in v.crop][:4]
            if len(v.crop) < 4 or v.crop[2] <= 1 or v.crop[3] <= 1:
                v.crop = None
        return v

    @property
    def is_identity(self) -> bool:
        return not self.crop and self.zoom <= 1.0 and not self.rotate and not self.flip

    def _crop_rect(self, w: int, h: int) -> tuple[int, int, int, int]:
        if not self.crop:
            return 0, 0, w, h
        x, y, cw, ch = self.crop
        x, y = int(np.clip(x, 0, w - 2)), int(np.clip(y, 0, h - 2))
        return x, y, int(np.clip(cw, 2, w - x)), int(np.clip(ch, 2, h - y))

    def output_size(self, w: int, h: int) -> tuple[int, int]:
        if self.rotate in (90, 270):
            w, h = h, w
        _, _, cw, ch = self._crop_rect(w, h)
        return cw, ch

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if self.is_identity:
            return frame
        img = frame
        if self.rotate in _ROT:
            img = cv2.rotate(img, _ROT[self.rotate])
        if self.flip:
            code = {"h": 1, "v": 0, "hv": -1, "vh": -1}.get(self.flip)
            if code is not None:
                img = cv2.flip(img, code)
        h, w = img.shape[:2]
        x, y, cw, ch = self._crop_rect(w, h)
        img = img[y:y + ch, x:x + cw]
        if self.zoom > 1.0:
            zw, zh = max(2, int(round(cw / self.zoom))), max(2, int(round(ch / self.zoom)))
            cx, cy = self.pan[0] * cw, self.pan[1] * ch
            x0 = int(np.clip(round(cx - zw / 2), 0, cw - zw))
            y0 = int(np.clip(round(cy - zh / 2), 0, ch - zh))
            img = cv2.resize(img[y0:y0 + zh, x0:x0 + zw], (cw, ch), interpolation=cv2.INTER_LINEAR)
        return np.ascontiguousarray(img)


def merge_frames(a: np.ndarray, b: np.ndarray, layout: str = "side") -> np.ndarray:
    """Join two camera images into one: "side" (a left of b) or "stack" (a above b); the smaller is padded."""
    if a.ndim != b.ndim:
        a = a if a.ndim == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
        b = b if b.ndim == 3 else cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
    if layout == "stack":
        w = max(a.shape[1], b.shape[1])
        pad = lambda f: cv2.copyMakeBorder(f, 0, 0, 0, w - f.shape[1], cv2.BORDER_CONSTANT, value=0)
        return np.vstack([pad(a), pad(b)])
    h = max(a.shape[0], b.shape[0])
    pad = lambda f: cv2.copyMakeBorder(f, 0, h - f.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=0)
    return np.hstack([pad(a), pad(b)])


class TransformedSource:
    """Wraps a VideoSource-like object (read / seek / release, fps, width, height) applying a CameraView and,
    optionally, merging a second source into the same image."""

    def __init__(self, src, view: CameraView | None = None, second=None, layout: str = "side"):
        self.src, self.second, self.layout = src, second, layout
        self.view = view or CameraView()
        self.source = getattr(src, "source", None)
        self.is_camera = bool(getattr(src, "is_camera", False))
        self.fps = float(getattr(src, "fps", 25.0) or 25.0)
        self.frame_count = int(getattr(src, "frame_count", 0) or 0)
        if second is not None and getattr(second, "frame_count", 0):
            self.frame_count = min(self.frame_count or second.frame_count, second.frame_count)
        w, h = int(getattr(src, "width", 0) or 0), int(getattr(src, "height", 0) or 0)
        if second is not None:
            w2, h2 = int(second.width), int(second.height)
            w, h = (max(w, w2), h + h2) if layout == "stack" else (w + w2, max(h, h2))
        self.raw_size = (w, h)
        self.last_raw: np.ndarray | None = None  # untransformed frames, for the camera options dialog
        self.last_raw2: np.ndarray | None = None
        self.width, self.height = self.view.output_size(w, h) if w and h else (w, h)

    @property
    def pos(self) -> int:
        return getattr(self.src, "pos", 0)

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    def read(self):
        ok, f = self.src.read()
        if not ok:
            return ok, f
        self.last_raw = f
        if self.second is not None:
            ok2, f2 = self.second.read()
            if not ok2:
                return False, None
            self.last_raw2 = f2
            f = merge_frames(f, f2, self.layout)
        return True, self.view.apply(f)

    def seek(self, index: int):
        self.src.seek(index)
        if self.second is not None:
            self.second.seek(index)

    def frame_at(self, index: int):
        self.seek(index)
        ok, f = self.read()
        return f if ok else None

    def release(self):
        self.src.release()
        if self.second is not None:
            self.second.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()


@dataclass
class SourceSpec:
    """A live image source: a camera index or a video file (simulating a camera), optionally merged with a second
    camera / file, with its CameraView."""

    source: str | int = 0
    second: str | int | None = None
    layout: str = "side"  # merge layout: "side" | "stack"
    view: CameraView = field(default_factory=CameraView)
    size: tuple | None = None  # requested camera resolution (w, h)
    fps: float | None = None  # requested camera frame rate
    name: str = ""

    @property
    def is_file(self) -> bool:
        return isinstance(self.source, str) and not self.source.isdigit()

    @property
    def key(self) -> str:
        """Identifier used to persist the camera options (camera index or file path, plus the merged source)."""
        k = f"camera:{int(self.source)}" if not self.is_file else f"file:{self.source}"
        if self.second is not None and self.second != "":
            s = self.second
            k += f"+{'camera:' + str(int(s)) if not isinstance(s, str) or s.isdigit() else 'file:' + s}"
        return k

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        from pathlib import Path
        one = lambda s: (f"Camera {int(s)}" if not isinstance(s, str) or s.isdigit() else Path(s).name)
        lbl = one(self.source)
        if self.second is not None and self.second != "":
            lbl += (" | " if self.layout == "side" else " / ") + one(self.second)
        return lbl

    def to_dict(self) -> dict:
        return {"source": self.source, "second": self.second, "layout": self.layout, "view": self.view.to_dict(),
                "size": list(self.size) if self.size else None, "fps": self.fps, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpec":
        return cls(source=d.get("source", 0), second=d.get("second"), layout=d.get("layout", "side"),
                   view=CameraView.from_dict(d.get("view")), size=tuple(d["size"]) if d.get("size") else None,
                   fps=d.get("fps"), name=d.get("name", ""))

    def open(self, opener=None):
        """Open the source (opener defaults to core.video.VideoSource) and wrap it in a TransformedSource."""
        if opener is None:
            from .video import VideoSource as opener
        w, h = self.size or (None, None)
        conv = lambda s: int(s) if not isinstance(s, str) or s.isdigit() else s
        src = opener(conv(self.source), w, h, self.fps or None)
        second = None
        if self.second is not None and self.second != "":
            try:
                second = opener(conv(self.second), w, h, self.fps or None)
            except Exception:
                src.release()
                raise
        if second is None and self.view.is_identity:
            return src
        return TransformedSource(src, self.view, second, self.layout)


class FramePacer:
    """Timestamps frames and paces video files at their frame rate × speed (cameras are paced by the device).

    ``clock`` is injectable for tests."""

    def __init__(self, fps: float, is_camera: bool, speed: float = 1.0, clock=time.monotonic, sleep=time.sleep):
        self.fps = fps or 25.0
        self.is_camera = is_camera
        self.speed = speed
        self.clock, self.sleep = clock, sleep
        self.reset()

    def reset(self):
        self.t_start = self.clock()
        self.index = 0
        self.dropped = 0
        self._last_ts: float | None = None

    def next(self) -> float:
        """Timestamp (s) of the frame just read; waits until it is due for files."""
        if self.is_camera:
            ts = self.clock() - self.t_start
            if self._last_ts is not None and ts - self._last_ts > 2.5 / self.fps:
                self.dropped += int((ts - self._last_ts) * self.fps) - 1
            self._last_ts = ts
        else:
            ts = self.index / self.fps
            if self.speed > 0:
                delay = self.t_start + ts / max(self.speed, 1e-3) - self.clock()
                if delay > 0:
                    self.sleep(delay)
        self.index += 1
        return ts


# ------------------------------------------------------------------ persistence
def camera_settings(project, key: str) -> dict:
    """Saved options of one camera / source (``{"view": {...}, "second": ..., "layout": ...}``) or {}."""
    if project is None:
        return {}
    return dict((project.settings_extra.get("cameras") or {}).get(key) or {})


def set_camera_settings(project, key: str, settings: dict):
    if project is None:
        return
    cams = project.settings_extra.setdefault("cameras", {})
    if settings:
        cams[key] = settings
    else:
        cams.pop(key, None)
