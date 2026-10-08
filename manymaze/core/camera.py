"""Camera image options for live testing: region (crop), digital zoom / pan, rotation, flip, merging two
cameras into one image, camera hardware settings (core.camhw), frame-rate pacing, reading a source in its own
thread and per-camera persistence in ``project.settings_extra``.

A source is an OpenCV camera index (int), a native industrial camera ("pylon:<serial>" …, core.camsources) or a
video file simulating a camera."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from .camhw import CameraHardware, describe_report
from .camsources import NativeCamera, is_native_source, native_label
from .tracking import sample_background

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


def _is_file(s) -> bool:
    return isinstance(s, str) and not s.isdigit() and not is_native_source(s)


def _source_key(s) -> str:
    if is_native_source(s):
        return s
    return f"file:{s}" if _is_file(s) else f"camera:{int(s)}"


def hardware_target(src):
    """The camera object of an opened source that has hardware settings (None for files / test fakes)."""
    if isinstance(src, TransformedSource):
        src = src.src
    return src if src is not None and callable(getattr(src, "apply_hardware", None)) else None


def apply_hardware(src, hw: CameraHardware | dict | None) -> dict:
    """Apply hardware settings to an opened source; never raises (a failing camera reports "unsupported")."""
    target = hardware_target(src)
    hw = hw if isinstance(hw, CameraHardware) else CameraHardware.from_dict(hw)
    if target is None or hw.is_empty:
        return {}
    try:
        return target.apply_hardware(hw)
    except Exception:
        return {k: "unsupported" for k in hw.to_dict()}


@dataclass
class SourceSpec:
    """A live image source: an OpenCV camera index, a native camera id ("pylon:<serial>", core.camsources) or a
    video file (simulating a camera), optionally merged with a second camera / file, with its CameraView and the
    hardware settings of the (first) camera."""

    source: str | int = 0
    second: str | int | None = None
    layout: str = "side"  # merge layout: "side" | "stack"
    view: CameraView = field(default_factory=CameraView)
    size: tuple | None = None  # requested camera resolution (w, h)
    fps: float | None = None  # requested camera frame rate
    name: str = ""
    hardware: CameraHardware = field(default_factory=CameraHardware)

    @property
    def is_file(self) -> bool:
        return _is_file(self.source)

    @property
    def is_native(self) -> bool:
        return is_native_source(self.source)

    @property
    def key(self) -> str:
        """Identifier used to persist the camera options (camera index, native camera id or file path, plus the
        merged source)."""
        k = _source_key(self.source)
        if self.second is not None and self.second != "":
            k += "+" + _source_key(self.second)
        return k

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        from pathlib import Path
        one = lambda s: (native_label(s) if is_native_source(s) else Path(s).name if _is_file(s) else
                         f"Camera {int(s)}")
        lbl = one(self.source)
        if self.second is not None and self.second != "":
            lbl += (" | " if self.layout == "side" else " / ") + one(self.second)
        return lbl

    def to_dict(self) -> dict:
        d = {"source": self.source, "second": self.second, "layout": self.layout, "view": self.view.to_dict(),
             "size": list(self.size) if self.size else None, "fps": self.fps, "name": self.name}
        if not self.hardware.is_empty:
            d["hardware"] = self.hardware.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpec":
        return cls(source=d.get("source", 0), second=d.get("second"), layout=d.get("layout", "side"),
                   view=CameraView.from_dict(d.get("view")), size=tuple(d["size"]) if d.get("size") else None,
                   fps=d.get("fps"), name=d.get("name", ""), hardware=CameraHardware.from_dict(d.get("hardware")))

    def open(self, opener=None):
        """Open the source (opener defaults to core.video.VideoSource; native cameras use
        core.camsources.NativeCamera), apply the hardware settings and wrap it in a TransformedSource.  The
        result of applying the settings is ``hardware_report`` of the returned object."""
        if opener is None:
            from .video import VideoSource as opener
        w, h = self.size or (None, None)
        conv = lambda s: int(s) if not isinstance(s, str) or s.isdigit() else s
        open_one = lambda s: (NativeCamera if is_native_source(s) else opener)(conv(s), w, h, self.fps or None)
        src = open_one(self.source)
        second = None
        if self.second is not None and self.second != "":
            try:
                second = open_one(self.second)
            except Exception:
                src.release()
                raise
        report = apply_hardware(src, self.hardware)
        out = src if second is None and self.view.is_identity else TransformedSource(src, self.view, second,
                                                                                       self.layout)
        try:
            out.hardware_report = report
        except Exception:  # objects refusing new attributes (tests)
            pass
        return out


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

    def next(self) -> float:
        """Timestamp (s) of the frame just read; waits until it is due for files."""
        if self.is_camera:
            ts = self.clock() - self.t_start
        else:
            ts = self.index / self.fps
            if self.speed > 0:
                delay = self.t_start + ts / max(self.speed, 1e-3) - self.clock()
                if delay > 0:
                    self.sleep(delay)
        self.index += 1
        return ts


class SourceReader:
    """Reads a :class:`SourceSpec` in its own thread.

    The source is opened (``opener`` defaults to core.video.VideoSource), a background is sampled from video files,
    then every frame is timestamped (:class:`FramePacer`) and handed to :meth:`on_frame`.  At the end of a video
    file reading starts again while :meth:`keep_looping` is True; otherwise :meth:`on_ended` is called and the
    reader waits for :meth:`restart`.  Subclasses implement the hooks, which run in the reader thread; an
    exception in one stops the reader through :meth:`on_failed`.

    A camera that stops delivering frames (unplugged, driver hiccup) is reopened automatically: after
    :meth:`on_capture_lost` the source is released and opened again with a growing delay (``reconnect_delays``)
    until frames arrive (:meth:`on_capture_restored` with the length of the gap) or ``reconnect_timeout_s`` has
    passed, and only then does the reader fail.  Each drop-out is kept in ``capture_log``."""

    reconnect_delays = (0.5, 1.0, 2.0, 4.0, 8.0)  # s before each reopening attempt (the last one repeats)
    reconnect_timeout_s = 120.0
    stall_reads = 100  # consecutive failed reads (~10 ms apart) before the capture counts as lost

    def __init__(self, spec: SourceSpec, opener=None, speed: float = 1.0, name: str = "live-source"):
        self.spec, self.opener, self.speed = spec, opener, speed
        self.fps = 25.0
        self.size: tuple[int, int] | None = None
        self.error = ""
        self.ended = False
        self.background: np.ndarray | None = None
        self.last_frame: np.ndarray | None = None
        self.hardware_report: dict = {}  # result of applying spec.hardware when the camera opened
        self.src = None
        self.capture_log: list[dict] = []  # {"lost": wall time, "gap_s": length or None, "reason", "attempts"}
        self._stop = False
        self._restart = False
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    # ---- hooks
    def on_opened(self):
        """The source is open (fps, size and, for files, background are set)."""

    def on_frame(self, frame: np.ndarray, ts: float):
        pass

    def keep_looping(self) -> bool:
        return True

    def on_ended(self):
        pass

    def on_failed(self, msg: str):
        pass

    def on_capture_lost(self, msg: str):
        """The camera stopped delivering frames; it is being reopened."""

    def on_capture_restored(self, gap_s: float):
        """Frames arrive again, gap_s seconds after the capture was lost."""

    # ---- control (any thread)
    def start(self):
        self.thread.start()

    def stop(self, wait: float = 5.0):
        self._stop = True
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(wait)

    def restart(self):
        """Read the video file from its beginning again."""
        self._restart = True

    def raw_frames(self):
        """(primary, second) untransformed frames for the camera options dialog."""
        src = self.src
        if isinstance(src, TransformedSource):
            return src.last_raw, src.last_raw2
        return self.last_frame, None

    def camera(self):
        """The open camera with hardware settings (VideoSource / NativeCamera), or None (files, not open yet)."""
        return hardware_target(self.src)

    def set_hardware(self, hw: CameraHardware | dict) -> dict:
        """Change hardware settings while the camera runs (any thread); returns the apply report."""
        hw = hw if isinstance(hw, CameraHardware) else CameraHardware.from_dict(hw)
        self.spec.hardware = self.spec.hardware.merged(hw.to_dict())
        return apply_hardware(self.src, hw)

    @property
    def hardware_message(self) -> str:
        """Settings the camera did not accept when it opened ("" when all were applied)."""
        return describe_report({k: v for k, v in self.hardware_report.items() if v != "ok"})

    # ---- thread
    def _run(self):
        try:
            src = self.spec.open(self.opener)
        except Exception as e:
            self.error = f"Cannot open {self.spec.label}: {e}"
            self.on_failed(self.error)
            return
        try:
            self.src = src
            self.hardware_report = dict(getattr(src, "hardware_report", None) or {})
            self.fps = float(src.fps or 25.0)
            self.size = (int(src.width), int(src.height))
            if not src.is_camera:
                try:
                    self.background = sample_background(src)
                except Exception:
                    self.background = None
            self.on_opened()
            self._loop(src)
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
            self.on_failed(self.error)
        finally:
            for s in {id(src): src, id(self.src): self.src}.values():  # the reopened camera too
                if s is not None:
                    try:
                        s.release()
                    except Exception:
                        pass

    def _reopen(self, old, attempt: int, deadline: float):
        """Release a camera that stopped delivering frames and open it again (after a delay growing with
        `attempt`); None when the reader is stopped or the deadline passes first."""
        try:
            old.release()
        except Exception:
            pass
        self.src = None
        while not self._stop and time.monotonic() < deadline:
            wake = time.monotonic() + self.reconnect_delays[min(attempt, len(self.reconnect_delays) - 1)]
            while not self._stop and time.monotonic() < min(wake, deadline):
                time.sleep(0.01)
            if self._stop or time.monotonic() >= deadline:
                break
            attempt += 1
            self.capture_log[-1]["attempts"] = attempt
            try:
                src = self.spec.open(self.opener)
            except Exception as e:
                self.capture_log[-1]["reason"] = f"{type(e).__name__}: {e}"
                continue
            self.src = src
            return src
        return None

    def _loop(self, src):
        pacer = FramePacer(self.fps, src.is_camera, self.speed)
        failures = 0
        lost_at: float | None = None  # monotonic time the capture was lost (being reopened)
        attempt = 0
        while not self._stop:
            if self._restart:
                self._restart = False
                self.ended = False
                src.seek(0)
                pacer.reset()
            if self.ended:
                time.sleep(0.02)
                continue
            try:
                ok, frame = src.read()
            except Exception as e:  # a camera driver error: reopened below like a stalled camera
                if not src.is_camera:
                    raise
                ok, frame, failures = False, None, self.stall_reads
                reason = f"{type(e).__name__}: {e}"
            else:
                reason = "the camera stopped delivering frames"
            if not ok:
                if src.is_camera:
                    cam = hardware_target(src)
                    if cam is not None and getattr(cam, "triggered", False) and lost_at is None:
                        failures = 0  # waiting for the external trigger
                        time.sleep(0.001)
                        continue
                    failures += 1
                    if failures > self.stall_reads:
                        now = time.monotonic()
                        if lost_at is None:
                            lost_at = now
                            self.capture_log.append({"lost": time.time(), "gap_s": None, "reason": reason,
                                                     "attempts": 0})
                            self.on_capture_lost(reason)
                        new = self._reopen(src, attempt, lost_at + self.reconnect_timeout_s)
                        if new is None:
                            if self._stop:
                                return
                            raise IOError(f"{reason}; reopening it failed for {self.reconnect_timeout_s:g} s")
                        src, failures = new, 0
                        attempt = self.capture_log[-1]["attempts"]
                        continue
                    time.sleep(0.01)
                    continue
                if self.keep_looping():
                    src.seek(0)
                    pacer.reset()
                    continue
                self.ended = True
                self.on_ended()
                continue
            failures = 0
            if lost_at is not None:
                gap = time.monotonic() - lost_at
                lost_at, attempt = None, 0
                self.capture_log[-1]["gap_s"] = round(gap, 3)
                self.on_capture_restored(gap)
            pacer.speed = self.speed
            ts = pacer.next()
            self.last_frame = frame
            self.on_frame(frame, ts)


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
