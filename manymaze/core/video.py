"""Video file / camera access and recording.

Random access (player, background sampling) and cameras use OpenCV (AVFoundation on macOS).  Sequential decoding
for tracking uses PyAV/FFmpeg when available — with Apple VideoToolbox hardware decoding on macOS and greyscale
taken straight from the luma plane — and recording uses the VideoToolbox H.264 encoder when available.
"""

from __future__ import annotations

import os
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


# ------------------------------------------------------------------ fast sequential decoding
_YUV8 = {"yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "yuv411p", "yuv440p", "yuvj440p",
         "nv12", "nv21", "nv16", "gray"}
# limited-range (16–235) luma → full-range grey, matching OpenCV's BGR→grey of the decoded frame
_LIMITED_TO_FULL = np.clip(np.round((np.arange(256) - 16) * 255.0 / 219.0), 0, 255).astype(np.uint8)


def _av():
    if os.environ.get("MANYMAZE_DECODER", "").lower() == "opencv":
        return None
    try:
        import av
        return av
    except ImportError:
        return None


def default_threads() -> int:
    """Threads per decoder / inference session (0 = library default); set per worker by core.batch."""
    try:
        return max(0, int(os.environ.get("MANYMAZE_THREADS", "0")))
    except ValueError:
        return 0


def hw_decoder_name() -> str | None:
    """The hardware decoder FFmpeg can use on this machine (VideoToolbox on macOS), or None."""
    av = _av()
    if av is None or os.environ.get("MANYMAZE_HWACCEL", "1") == "0":
        return None
    try:
        from av.codec.hwaccel import hwdevices_available
        devs = hwdevices_available()
    except Exception:
        return None
    if sys.platform == "darwin" and "videotoolbox" in devs:
        return "videotoolbox"
    return None


class FrameReader:
    """Sequential frame decoding for tracking: yields (frame_index, frame).

    With ``gray=True`` frames are 2-D greyscale (the luma plane, no colour conversion).  Backends, in order of
    preference: PyAV with VideoToolbox hardware decoding (macOS), PyAV multi-threaded software decoding, OpenCV.
    """

    def __init__(self, path: str, start: int = 0, gray: bool = True, threads: int = 0, hwaccel: bool = True):
        self.path = str(path)
        self.start = max(0, int(start))
        self.gray = gray
        self.threads = threads or default_threads()
        self.backend = "opencv"
        self._container = None
        self._cap = None
        av = _av()
        if av is not None:
            try:
                self._open_av(av, hwaccel)
            except Exception:
                self._container = None
        if self._container is None:
            self._open_cv()

    def _open_av(self, av, hwaccel: bool):
        hw = hw_decoder_name() if hwaccel else None
        kw = {}
        if hw:
            from av.codec.hwaccel import HWAccel
            kw["hwaccel"] = HWAccel(device_type=hw, allow_software_fallback=True)
        c = av.open(self.path, **kw)
        try:
            st = c.streams.video[0]
            cc = st.codec_context
            if self.threads == 1:
                cc.thread_type = "NONE"
            else:
                cc.thread_type = "AUTO"
                if self.threads:
                    cc.thread_count = self.threads
            rate = st.average_rate or st.guessed_rate or st.base_rate
            self.fps = float(rate) if rate else 25.0
            self.width, self.height = cc.width, cc.height
            self._tb = float(st.time_base) if st.time_base else 0.0
            self._t0 = st.start_time or 0
            self._stream = st
            if self.start > 0 and self._tb:
                target = self._t0 + int((self.start - 0.5) / self.fps / self._tb)
                c.seek(max(self._t0, target), stream=st, backward=True)
        except Exception:
            c.close()
            raise
        self._container = c
        self.backend = f"pyav+{hw}" if hw else "pyav"

    def _open_cv(self):
        self._cap = cv2.VideoCapture(self.path)
        if not self._cap.isOpened():
            raise IOError(f"Cannot open video {self.path!r}")
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS)) or 25.0
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.start:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, self.start)
        self.backend = "opencv"

    def _index(self, frame, fallback: int) -> int:
        if frame.pts is None or not self._tb:
            return fallback
        return int(round((frame.pts - self._t0) * self._tb * self.fps))

    def _to_array(self, frame) -> np.ndarray:
        if not self.gray:
            return frame.to_ndarray(format="bgr24")
        fmt = frame.format.name
        if fmt in _YUV8:
            p = frame.planes[0]
            y = np.frombuffer(p, np.uint8).reshape(frame.height, p.line_size)[:, :frame.width]
            full = fmt.startswith("yuvj") or fmt == "gray" or int(frame.color_range or 0) == 2
            return y.copy() if full else cv2.LUT(y, _LIMITED_TO_FULL)
        return frame.to_ndarray(format="gray")

    def __iter__(self):
        if self._cap is not None:
            i = self.start
            while True:
                ok, f = self._cap.read()
                if not ok:
                    return
                yield i, (cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if self.gray else f)
                i += 1
        elif self.backend != "pyav":
            # VideoToolbox opens streams it can't decode (e.g. H.264 High 4:4:4) and only fails on the
            # first packet; allow_software_fallback doesn't catch that, so reopen in software
            frames = self._decode()
            try:
                first = next(frames)
            except StopIteration:
                return
            except Exception:
                self._container.close()
                self._open_av(_av(), hwaccel=False)
                yield from self._decode()
                return
            yield first
            yield from frames
        else:
            yield from self._decode()

    def _decode(self):
        expected = None
        for frame in self._container.decode(self._stream):
            i = self._index(frame, expected if expected is not None else self.start)
            if expected is not None and i < expected:  # duplicate / out-of-order timestamps
                i = expected
            expected = i + 1
            if i < self.start:
                continue
            yield i, self._to_array(frame)

    def close(self):
        if self._container is not None:
            self._container.close()
            self._container = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class VideoRecorder:
    """Write frames to a video file (live test recordings, exported overlay videos).

    MP4 / MOV files of even size are H.264-encoded with PyAV: Apple's VideoToolbox hardware encoder on macOS, else
    x264, MPEG-4 part 2 as a last resort.  Other formats, odd sizes or no working encoder fall back to OpenCV.
    ``fragmented`` writes a fragmented MP4 (a keyframe every 2 s) so that a crash or a power cut leaves a file
    playable up to the last fragment."""

    CODECS = ("h264_videotoolbox", "libx264", "mpeg4")

    def __init__(self, path: str, fps: float, size: tuple[int, int], fragmented: bool = False):
        self.path, self.fps, self.size = str(path), float(fps or 25.0), (int(size[0]), int(size[1]))
        self.fragmented = fragmented
        self.frames = 0
        self.backend = ""
        self.writer = None
        self._av = self._stream = None
        self._codecs: list[str] = []
        av = _av()
        if av is not None and Path(self.path).suffix.lower() in (".mp4", ".m4v", ".mov") \
                and self.size[0] % 2 == 0 and self.size[1] % 2 == 0:
            hw = os.environ.get("MANYMAZE_HWACCEL", "1") != "0"
            self._codecs = [c for c in self.CODECS if c in av.codecs_available and (hw or "videotoolbox" not in c)]
        if not self._open_next():
            self._open_cv()

    def _open_next(self) -> bool:
        """Open the next PyAV encoder candidate; False when none is left."""
        from fractions import Fraction

        av = _av()
        opts = {"movflags": "frag_keyframe+empty_moov+default_base_moof", "flush_packets": "1"} \
            if self.fragmented else {}
        while self._codecs:
            codec = self._codecs.pop(0)
            try:
                c = av.open(self.path, "w", format="mov" if self.path.lower().endswith(".mov") else "mp4",
                            options=opts)
            except Exception:
                return False
            try:
                st = c.add_stream(codec, rate=Fraction(self.fps).limit_denominator(1001))
                st.width, st.height = self.size
                st.pix_fmt = "nv12" if codec == "h264_videotoolbox" else "yuv420p"
                gop = str(max(1, int(round(self.fps * 2))))
                if codec == "libx264":
                    st.options = {"preset": "veryfast", "crf": "20", "g": gop}
                else:
                    st.bit_rate = int(self.size[0] * self.size[1] * self.fps * (0.15 if "264" in codec else 0.4))
                    st.options = {"g": gop}
            except Exception:
                c.close()
                Path(self.path).unlink(missing_ok=True)
                continue
            self._av, self._stream, self.backend = c, st, codec
            return True
        return False

    def _open_cv(self):
        ext = Path(self.path).suffix.lower()
        for cc in (["avc1", "mp4v"] if ext in (".mp4", ".m4v", ".mov") else ["MJPG", "XVID"]):
            w = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*cc), self.fps, self.size)
            if w.isOpened():
                self.writer, self.backend = w, "opencv"
                return
            w.release()
        raise IOError(f"Could not open a video writer for {self.path}")

    def write(self, frame: np.ndarray):
        img = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        while self.writer is None:
            import av

            vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(img), format="bgr24")
            vf.pts = self.frames
            try:
                for pkt in self._stream.encode(vf):
                    self._av.mux(pkt)
                self.frames += 1
                return
            except Exception:
                if self.frames:
                    raise
                # encoders open lazily (e.g. no VideoToolbox in a VM): try the next one
                try:
                    self._av.close()
                except Exception:
                    pass
                self._av = None
                Path(self.path).unlink(missing_ok=True)
                if not self._open_next():
                    self._open_cv()
        self.writer.write(img)
        self.frames += 1

    def close(self):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        av_, self._av = self._av, None
        if av_ is None:
            return
        if not self.frames:  # nothing written: the encoder may never have opened
            try:
                av_.close()
            except Exception:
                pass
            return
        try:
            for pkt in self._stream.encode():
                av_.mux(pkt)
        finally:
            av_.close()

