"""Camera image options for live testing: region (crop), digital zoom / pan, rotation, flip, merging up to four
cameras into one image (a montage), lens distortion correction (core.lens), camera hardware settings (core.camhw),
frame-rate pacing, reading a source in its own thread and per-camera persistence in ``project.settings_extra``.

A source is an OpenCV camera index (int), a native industrial camera ("pylon:<serial>" …, core.camsources) or a
video file simulating a camera."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from .camhw import CameraHardware, UnsupportedPixelFormat, describe_report
from .camsources import NativeCamera, is_native_source, native_label
from .lens import LensCorrection, lens_from
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


MAX_SOURCES = 4  # cameras in one montage
# montage layouts: "side" (a row, left to right), "stack" (a column, top to bottom), "grid" (two per row)
MERGE_LAYOUTS = [("side", "Side by side (a row)"), ("stack", "One above the other (a column)"),
           ("grid", "In a grid (two per row)")]
_LAYOUT_ALIASES = {"row": "side", "column": "stack"}


def merge_layout(layout) -> str:
    """A montage layout name: "side" | "stack" | "grid" ("row" and "column" are the same as "side" and "stack")."""
    layout = _LAYOUT_ALIASES.get(str(layout or "side"), str(layout or "side"))
    return layout if layout in ("side", "stack", "grid") else "side"


def _montage(sizes: list[tuple[int, int]], layout: str) -> tuple[list[int], list[int], int]:
    """(column widths, row heights, columns) of a montage of images of `sizes` (w, h): each column as wide as its
    widest image and each row as high as its highest one."""
    n = len(sizes)
    layout = merge_layout(layout)
    cols = n if layout == "side" else 1 if layout == "stack" else min(n, 2)
    rows = -(-n // cols)
    widths = [max(sizes[i][0] for i in range(c, n, cols)) for c in range(cols)]
    heights = [max(sizes[i][1] for i in range(r * cols, min(n, (r + 1) * cols))) for r in range(rows)]
    return widths, heights, cols


def merged_size(sizes, layout: str = "side") -> tuple[int, int]:
    """Size (w, h) of the montage of images of `sizes`."""
    sizes = [(int(w), int(h)) for w, h in sizes]
    if len(sizes) <= 1:
        return sizes[0] if sizes else (0, 0)
    widths, heights, _ = _montage(sizes, layout)
    return sum(widths), sum(heights)


def merge_frames(frames, b: np.ndarray | None = None, layout: str = "side") -> np.ndarray:
    """Join up to four camera images into one: "side" / "row" (left to right), "stack" / "column" (top to bottom)
    or "grid" (two per row: 2 × 2 for three or four images; an empty cell stays black).  Smaller images are padded
    with black at their right / bottom.  ``merge_frames(a, b, layout)`` joins two images as before."""
    frames = [frames] + ([b] if b is not None else []) if isinstance(frames, np.ndarray) else list(frames)
    if len(frames) == 1:
        return frames[0]
    if any(f.ndim == 3 for f in frames):
        frames = [f if f.ndim == 3 else cv2.cvtColor(f, cv2.COLOR_GRAY2BGR) for f in frames]
    widths, heights, cols = _montage([(f.shape[1], f.shape[0]) for f in frames], layout)
    out = np.zeros((sum(heights), sum(widths)) + frames[0].shape[2:], frames[0].dtype)
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        y, x = sum(heights[:r]), sum(widths[:c])
        out[y:y + f.shape[0], x:x + f.shape[1]] = f
    return out


class TransformedSource:
    """Wraps a VideoSource-like object (read / seek / release, fps, width, height) applying a CameraView and,
    optionally, lens distortion correction (``lenses``: one LensCorrection or None per source, applied before
    merging) and merging further sources (``second`` then ``more``, up to four in all) into the same image."""

    def __init__(self, src, view: CameraView | None = None, second=None, layout: str = "side", more=(),
                 lenses=None):
        self.src, self.second, self.layout = src, second, merge_layout(layout)
        self.extra = ([second] if second is not None else []) + [m for m in more if m is not None]
        self.lenses = list(lenses or [])[:1 + len(self.extra)]
        self.view = view or CameraView()
        self.source = getattr(src, "source", None)
        self.is_camera = bool(getattr(src, "is_camera", False))
        self.fps = float(getattr(src, "fps", 25.0) or 25.0)
        self.frame_count = int(getattr(src, "frame_count", 0) or 0)
        for other in self.extra:
            if getattr(other, "frame_count", 0):
                self.frame_count = min(self.frame_count or other.frame_count, other.frame_count)
        w, h = int(getattr(src, "width", 0) or 0), int(getattr(src, "height", 0) or 0)
        if self.extra:
            w, h = merged_size([(w, h)] + [(int(o.width), int(o.height)) for o in self.extra], self.layout)
        self.raw_size = (w, h)
        self.last_raw: np.ndarray | None = None  # untransformed frames, for the camera options dialog
        self.last_raw2: np.ndarray | None = None
        self.last_raws: list = [None] * (1 + len(self.extra))
        self.width, self.height = self.view.output_size(w, h) if w and h else (w, h)

    def _lens(self, i: int, f: np.ndarray) -> np.ndarray:
        lens = self.lenses[i] if i < len(self.lenses) else None
        return f if lens is None else lens.apply(f)

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
        self.last_raw = self.last_raws[0] = f
        frames = [self._lens(0, f)]
        for i, other in enumerate(self.extra, start=1):
            ok2, f2 = other.read()
            if not ok2:
                return False, None
            self.last_raws[i] = f2
            if i == 1:
                self.last_raw2 = f2
            frames.append(self._lens(i, f2))
        f = merge_frames(frames, layout=self.layout) if len(frames) > 1 else frames[0]
        return True, self.view.apply(f)

    def seek(self, index: int):
        self.src.seek(index)
        for other in self.extra:
            other.seek(index)

    def frame_at(self, index: int):
        self.seek(index)
        ok, f = self.read()
        return f if ok else None

    def release(self):
        self.src.release()
        for other in self.extra:
            other.release()

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


def source_key(s) -> str:
    """The key of one camera / file in the saved camera options ("camera:0", "file:<path>", a native id)."""
    return _source_key(s)


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
    video file (simulating a camera), optionally merged with up to three more cameras / files into one image (a
    montage: ``second``, then ``others``; see :attr:`merge`), with its CameraView, the hardware settings of the
    (first) camera and the lens distortion correction of each camera (``undistort``: {source key: LensCorrection
    dict}, core.lens)."""

    source: str | int = 0
    second: str | int | None = None  # the first merged source
    layout: str = "side"  # merge layout: "side" | "stack" | "grid" (see MERGE_LAYOUTS)
    view: CameraView = field(default_factory=CameraView)
    size: tuple | None = None  # requested camera resolution (w, h)
    fps: float | None = None  # requested camera frame rate
    name: str = ""
    hardware: CameraHardware = field(default_factory=CameraHardware)
    others: list = field(default_factory=list)  # the third and fourth merged sources
    undistort: dict = field(default_factory=dict)

    @property
    def merge(self) -> list:
        """The sources merged into the image of the first one (at most MAX_SOURCES − 1)."""
        return [s for s in [self.second, *self.others] if s is not None and s != ""][:MAX_SOURCES - 1]

    @merge.setter
    def merge(self, sources):
        sources = [s for s in (sources or []) if s is not None and s != ""][:MAX_SOURCES - 1]
        self.second = sources[0] if sources else None
        self.others = sources[1:]

    @property
    def sources(self) -> list:
        """Every source of the image, the first one first."""
        return [self.source, *self.merge]

    @property
    def is_file(self) -> bool:
        return _is_file(self.source)

    @property
    def is_native(self) -> bool:
        return is_native_source(self.source)

    @property
    def key(self) -> str:
        """Identifier used to persist the camera options (camera index, native camera id or file path, plus the
        merged sources)."""
        return "+".join(_source_key(s) for s in self.sources)

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        from pathlib import Path
        one = lambda s: (native_label(s) if is_native_source(s) else Path(s).name if _is_file(s) else
                         f"Camera {int(s)}")
        sep = {"side": " | ", "stack": " / "}.get(merge_layout(self.layout), " + ")
        return sep.join(one(s) for s in self.sources)

    def lens(self, source) -> LensCorrection | None:
        """The lens correction of one of the sources (None when it has none)."""
        return lens_from(self.undistort.get(_source_key(source)))

    def to_dict(self) -> dict:
        d = {"source": self.source, "second": self.second, "layout": self.layout, "view": self.view.to_dict(),
             "size": list(self.size) if self.size else None, "fps": self.fps, "name": self.name}
        if len(self.merge) > 1:
            d["merge"] = self.merge
        if not self.hardware.is_empty:
            d["hardware"] = self.hardware.to_dict()
        lenses = {k: v for k, v in self.undistort.items() if lens_from(v) is not None}
        if lenses:
            d["undistort"] = lenses
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpec":
        spec = cls(source=d.get("source", 0), second=d.get("second"), layout=merge_layout(d.get("layout", "side")),
                   view=CameraView.from_dict(d.get("view")), size=tuple(d["size"]) if d.get("size") else None,
                   fps=d.get("fps"), name=d.get("name", ""), hardware=CameraHardware.from_dict(d.get("hardware")))
        spec.merge = merged_sources(d)
        und = d.get("undistort")
        spec.undistort = {str(k): LensCorrection.from_dict(v).to_dict() for k, v in und.items()
                          if lens_from(v) is not None} if isinstance(und, dict) else {}
        return spec

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
        merged = []
        try:
            for s in self.merge:
                merged.append(open_one(s))
        except Exception:
            for o in [src, *merged]:
                o.release()
            raise
        report = apply_hardware(src, self.hardware)
        lenses = [self.lens(s) for s in self.sources]
        if not merged and self.view.is_identity and lenses[0] is None:
            out = src
        else:
            out = TransformedSource(src, self.view, merged[0] if merged else None, self.layout, merged[1:], lenses)
        try:
            out.hardware_report = report
        except Exception:  # objects refusing new attributes (tests)
            pass
        return out


def merged_sources(d: dict | None) -> list:
    """The merged sources of saved camera options or a saved SourceSpec: ``merge`` (three or four cameras) or the
    ``second`` of a two-camera montage."""
    d = d or {}
    m = d.get("merge")
    if isinstance(m, list) and m:
        return [s for s in m if s is not None and s != ""][:MAX_SOURCES - 1]
    s = d.get("second")
    return [s] if s is not None and s != "" else []


class FramePacer:
    """Timestamps frames and paces video files at their frame rate × speed (cameras are paced by the device).

    Changing ``speed`` keeps the playback position: the frames still to come are paced at the new speed from now
    on (not as if the whole file had been played at it).  Waits are slept in short slices so that ``stop`` (a
    callable, True to stop waiting) and a speed change read from ``speed_source`` (a callable) take effect at once.
    ``clock`` and ``sleep`` are injectable for tests."""

    slice_s = 0.05  # longest single sleep

    def __init__(self, fps: float, is_camera: bool, speed: float = 1.0, clock=time.monotonic, sleep=time.sleep,
                 stop=None, speed_source=None):
        self.fps = fps or 25.0
        self.is_camera = is_camera
        self._speed = speed
        self.clock, self.sleep = clock, sleep
        self.stop, self.speed_source = stop, speed_source
        self.reset()

    def reset(self):
        self.t_start = self.clock()
        self.index = 0

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float):
        old, self._speed = self._speed, value
        if value == old or self.is_camera:
            return
        # rebase: the current playback position is reached now at the new speed
        now = self.clock()
        pos = self.index / self.fps
        if old and old > 0:
            pos = min(pos, max(0.0, (now - self.t_start) * old))
        if value and value > 0:
            self.t_start = now - pos / value

    def next(self) -> float:
        """Timestamp (s) of the frame just read; waits until it is due for files."""
        if self.is_camera:
            ts = self.clock() - self.t_start
        else:
            ts = self.index / self.fps
            if self.speed > 0:
                delay = self.t_start + ts / max(self.speed, 1e-3) - self.clock()
                while delay > 0:
                    if self.stop is not None and self.stop():
                        break
                    if self.speed_source is not None:
                        sp = self.speed_source()
                        if sp != self.speed:
                            self.speed = sp
                            if not sp or sp <= 0:
                                break
                            delay = self.t_start + ts / max(self.speed, 1e-3) - self.clock()
                            continue
                    chunk = min(delay, self.slice_s)
                    self.sleep(chunk)
                    delay -= chunk
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

    def raw_frames_all(self) -> list:
        """The untransformed frame of every source of the image (None for those not read yet)."""
        src = self.src
        if isinstance(src, TransformedSource):
            return list(src.last_raws)
        return [self.last_frame] + [None] * len(self.spec.merge)

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
        pacer = FramePacer(self.fps, src.is_camera, self.speed, stop=lambda: self._stop,
                           speed_source=lambda: self.speed)
        failures = 0
        lost_at: float | None = None  # monotonic time the capture was lost (being reopened)
        attempt = 0
        shape: tuple | None = None  # frame size before the capture was lost
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
                if not src.is_camera or isinstance(e, UnsupportedPixelFormat):
                    raise  # reopening cannot help: the reader fails with this message
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
                if shape is not None and frame.shape[:2] != shape:
                    # the arena masks, background and calibration were made for the old image size
                    raise IOError(f"The camera came back at {frame.shape[1]}×{frame.shape[0]} instead of "
                                  f"{shape[1]}×{shape[0]} after reconnecting: the apparatus no longer fits the "
                                  "image. Set the camera's image size again and restart.")
                gap = time.monotonic() - lost_at
                lost_at, attempt = None, 0
                self.capture_log[-1]["gap_s"] = round(gap, 3)
                self.size = (int(frame.shape[1]), int(frame.shape[0]))
                self.on_capture_restored(gap)
            shape = frame.shape[:2]
            pacer.speed = self.speed
            ts = pacer.next()
            if self._stop:
                break
            self.last_frame = frame
            self.on_frame(frame, ts)


# ------------------------------------------------------------------ persistence
def camera_settings(project, key: str) -> dict:
    """Saved options of one camera / source (``{"view": {...}, "second": ..., "merge": [...], "layout": ...,
    "hardware": {...}, "undistort": {...}}``) or {}."""
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


def camera_lenses(project, sources) -> dict:
    """{source key: lens correction dict} of the cameras of a montage that have one (each camera keeps its own
    correction with its camera options; core.lens)."""
    out = {}
    for s in sources:
        if s is None or s == "":
            continue
        k = _source_key(s)
        lens = lens_from(camera_settings(project, k).get("undistort"))
        if lens is not None:
            out[k] = lens.to_dict()
    return out
