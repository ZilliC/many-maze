"""Video file / camera access and recording (OpenCV backed; AVFoundation on macOS)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import cv2
import numpy as np

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".mpg", ".mpeg", ".webm", ".mts")


def camera_backend() -> int:
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def list_cameras(max_index: int = 6) -> list[int]:
    """Probe camera indices that can be opened."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, camera_backend())
        if cap is not None and cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append(i)
        if cap is not None:
            cap.release()
    return found


class VideoSource:
    """A video file or a live camera.

    Thread-safe for alternating seek/read from one consumer at a time.
    """

    def __init__(self, source: str | int, width: int | None = None, height: int | None = None,
                 fps: float | None = None):
        self.source = source
        self.is_camera = isinstance(source, int) or (isinstance(source, str) and source.isdigit())
        self._lock = threading.Lock()
        if self.is_camera:
            self.cap = cv2.VideoCapture(int(source), camera_backend())
            if width:
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            if height:
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if fps:
                self.cap.set(cv2.CAP_PROP_FPS, fps)
        else:
            if not Path(source).exists():
                raise FileNotFoundError(source)
            self.cap = cv2.VideoCapture(str(source))
        if not self.cap.isOpened():
            raise IOError(f"Cannot open video source {source!r}")
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS)) or 0.0
        if not self.fps or self.fps > 1000 or self.fps != self.fps:
            self.fps = fps or 25.0
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) if not self.is_camera else 0
        self.frame_count = max(n, 0)
        self.pos = 0

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self._lock:
            ok, frame = self.cap.read()
            if ok:
                self.pos += 1
            return ok, frame

    def seek(self, index: int):
        if self.is_camera:
            return
        with self._lock:
            index = int(max(0, min(index, max(self.frame_count - 1, 0))))
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            self.pos = index

    def frame_at(self, index: int) -> np.ndarray | None:
        self.seek(index)
        ok, f = self.read()
        return f if ok else None

    def frame_at_time(self, t: float) -> np.ndarray | None:
        return self.frame_at(int(round(t * self.fps)))

    def release(self):
        with self._lock:
            self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()


def sample_frames(path: str, n: int = 30, start: int = 0, end: int | None = None) -> list[np.ndarray]:
    """Return n frames evenly spaced across [start, end)."""
    with VideoSource(path) as v:
        end = v.frame_count if end is None or end <= 0 else min(end, v.frame_count)
        if end <= start:
            end = v.frame_count
        idx = np.linspace(start, max(start, end - 1), n).astype(int)
        frames = []
        for i in np.unique(idx):
            f = v.frame_at(int(i))
            if f is not None:
                frames.append(f)
        return frames


def first_frame(path: str) -> np.ndarray | None:
    with VideoSource(path) as v:
        ok, f = v.read()
        return f if ok else None


class VideoRecorder:
    """Write frames to a video file (used for recording live camera sessions)."""

    def __init__(self, path: str, fps: float, size: tuple[int, int]):
        self.path = str(path)
        ext = Path(path).suffix.lower()
        fourccs = ["avc1", "mp4v"] if ext in (".mp4", ".m4v", ".mov") else ["MJPG", "XVID"]
        self.writer = None
        for cc in fourccs:
            w = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*cc), fps, size)
            if w.isOpened():
                self.writer = w
                break
            w.release()
        if self.writer is None:
            raise IOError(f"Could not open a video writer for {path}")
        self.frames = 0

    def write(self, frame: np.ndarray):
        self.writer.write(frame)
        self.frames += 1

    def close(self):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
