"""Animated track plots: the path of a `plots.track_plot` figure drawn progressively up to a time, optionally
showing only the last `trail_s` seconds (ANY-maze's "only show the track and markers for a certain duration") over
the test's video playing underneath, and the rendering of such a playback to a video file.  No Qt: used by the Data
page's playback bar and by the command line / tests."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .lens import corrected
from .plots import TRACK_END_GID, TRACK_FRAME_GID, TRACK_MARKER_GID, TRACK_PATH_GID
from .video import VideoSource

TRAILS = (0.0, 2.0, 5.0, 10.0, 30.0, 60.0)  # seconds shown while playing; 0 = the whole track so far
READ_AHEAD = 30  # frames read on (rather than seeking) to reach a later frame


class VideoFrames:
    """The frames of a test's video by track time, for the background of a playing track plot: the frame shown at
    track time t is the video's frame at video_start_s + t (as the tracked video is exported with overlays),
    corrected by `lens` like the tracked frames.  Reading on is cheaper than seeking, so playing forward reads
    the frames in between.  Call close() (or use it as a context manager) when done."""

    def __init__(self, path, video_start_s: float = 0.0, lens=None):
        self.src = corrected(VideoSource(str(path)), lens)
        self._raw = getattr(self.src, "src", self.src)  # frames skipped while reading on need no correction
        self.fps = self._raw.fps
        self.frame_count = self._raw.frame_count
        self.video_start_s = float(video_start_s or 0.0)
        self._index = -1
        self._frame = None

    def index_at(self, t: float) -> int:
        i = int(round((self.video_start_s + float(t)) * self.fps))
        return max(0, min(i, self.frame_count - 1) if self.frame_count else i)

    def __call__(self, t: float):
        """The frame at track time t (BGR), or None when it cannot be read."""
        i = self.index_at(t)
        if i == self._index:
            return self._frame
        try:
            if self._frame is not None and self._index < i <= self._index + READ_AHEAD:
                for _ in range(i - self._index - 1):
                    if not self._raw.read()[0]:
                        break
                ok, f = self.src.read()
            else:
                f = self.src.frame_at(i)
                ok = f is not None
        except Exception:  # e.g. the lens correction of a frame that could not be read
            ok, f = False, None
        self._index, self._frame = (i, f) if ok else (-1, None)
        return self._frame

    def close(self):
        self.src.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class TrackAnimation:
    """Drives the artists of a track-plot figure: the path, the behaviour markers and the end marker are shown up
    to a time, with the animal's current position as a dot and, given `frames` (a :class:`VideoFrames` or any
    callable t -> frame), the video frame of that moment under the track.  `ok` is False when the figure has no
    track path (e.g. an empty track): show() then does nothing."""

    def __init__(self, fig, track, trail_s: float = 0.0, frames=None):
        self.fig = fig
        self.trail_s = float(trail_s or 0.0)
        self.frames = frames
        self._image = None  # the figure's background frame (track_plot draws it with TRACK_FRAME_GID)
        self.t = np.asarray(track.t, float) if track is not None and len(track) else np.zeros(0)
        self._path = self._now = None
        self._full = None  # LineCollection: (segments, values); Line2D: (x, y)
        self._xy = np.zeros((0, 2))
        self._markers: list[tuple[float, object]] = []
        self._end = []
        for ax in (fig.axes if fig is not None else []):
            for im in ax.images:
                if im.get_gid() == TRACK_FRAME_GID and self._image is None:
                    self._image = im
            for a in list(ax.collections) + list(ax.lines):
                gid = a.get_gid() or ""
                if gid == TRACK_PATH_GID and self._path is None:
                    self._path = a
                elif gid == TRACK_END_GID:
                    self._end.append(a)
                elif gid.startswith(TRACK_MARKER_GID):
                    try:
                        self._markers.append((float(gid[len(TRACK_MARKER_GID):]), a))
                    except ValueError:
                        pass
        if self._path is None or len(self.t) < 2:
            self._path = None
            self.t = np.zeros(0)
            return
        # the whole path, kept on the artist: a second animation of the same figure (e.g. saving the playback
        # after showing a trail) must not take the part currently drawn for the whole track
        full = getattr(self._path, "_manymaze_full", None)
        if full is None:
            if hasattr(self._path, "get_segments"):  # coloured by time / speed / a parameter
                # the positions frame by frame, NaN gaps included: track_plot keeps them on the collection, as
                # matplotlib hands back segments with a NaN end shortened or empty (no use for either)
                xy = getattr(self._path, "_manymaze_xy", None)
                if xy is None:  # a collection drawn elsewhere: rebuilt from the segments, short ones as gaps
                    segs = self._path.get_segments()
                    xy = [s[0] if len(s) == 2 else (np.nan, np.nan) for s in segs]
                    xy += [segs[-1][1] if len(segs[-1]) == 2 else (np.nan, np.nan)] if segs else []
                xy = np.asarray(xy, float).reshape(-1, 2)
                arr = self._path.get_array()
                full = (np.stack([xy[:-1], xy[1:]], axis=1), None if arr is None else np.asarray(arr))
            else:
                full = (np.asarray(self._path.get_xdata(), float), np.asarray(self._path.get_ydata(), float))
            self._path._manymaze_full = full
        self._full = full
        if hasattr(self._path, "get_segments"):
            segs = full[0]
            self._xy = np.concatenate([segs[:, 0], segs[-1:, 1]]) if len(segs) else np.zeros((0, 2))
        else:
            self._xy = np.column_stack([full[0], full[1]])
        n = min(len(self._xy), len(self.t))
        self.t, self._xy = self.t[:n], self._xy[:n]
        self._now, = self._path.axes.plot([], [], "o", ms=8, color="#f97316", mec="white", mew=1.2, zorder=7,
                                          visible=False)

    @property
    def ok(self) -> bool:
        return self._path is not None

    @property
    def path(self):
        return self._path

    @property
    def start(self) -> float:
        return float(self.t[0]) if len(self.t) else 0.0

    @property
    def end(self) -> float:
        return float(self.t[-1]) if len(self.t) else 0.0

    def frames_shown(self, t: float) -> int:
        """Number of track positions drawn at playback time t (the positions up to t)."""
        return int(np.searchsorted(self.t, t + 1e-9, side="right")) if len(self.t) else 0

    def first_shown(self, t: float) -> int:
        """Index of the first position drawn at time t: 0, or the start of the trail."""
        if not self.trail_s or not len(self.t):
            return 0
        return int(np.searchsorted(self.t, t - self.trail_s, side="left"))

    @property
    def plays_video(self) -> bool:
        """Whether show() puts the video frame of the moment under the track."""
        return self.frames is not None and self._image is not None

    def show_frame(self, t: float):
        """Put the video frame at time t under the track (nothing without frames or when it cannot be read)."""
        if not self.plays_video:
            return
        f = self.frames(t)
        if f is not None:
            self._image.set_data(f[..., ::-1] if f.ndim == 3 else f)

    def show(self, t: float, video: bool = True):
        """Draw the track up to time t, over the video frame of that moment when `video` (and frames were given).
        With no trail, the whole track (end marker, every marker) is shown when t reaches the end; with a trail
        only the last trail_s seconds are ever shown."""
        if self._path is None:
            return
        if video:
            self.show_frame(t)
        n = self.frames_shown(t)
        m = self.first_shown(t)
        full = n >= len(self.t) and not self.trail_s
        if hasattr(self._path, "get_segments"):
            segs, vals = self._full
            k = max(0, n - 1)
            self._path.set_segments(segs[m:k])
            if vals is not None:
                self._path.set_array(vals[m:k])
        else:
            x, y = self._full
            self._path.set_data(x[m:n], y[m:n])
        lo = t - self.trail_s if self.trail_s else -math.inf
        for tm, a in self._markers:
            a.set_visible(full or lo <= tm <= t)
        for a in self._end:
            a.set_visible(full)
        ok = np.flatnonzero(np.isfinite(self._xy[m:n, 0])) + m if n > m else []
        if len(ok) and not full:
            self._now.set_data([self._xy[ok[-1], 0]], [self._xy[ok[-1], 1]])
            self._now.set_visible(True)
        else:
            self._now.set_visible(False)


def render_track_video(fig, track, path, speed: float = 4.0, fps: float = 25.0, trail_s: float = 0.0,
                       progress=None, should_stop=None, frames=None) -> Path | None:
    """Save the playback of a track-plot figure as a video: the track drawn up to each moment at `speed` × real
    time, `fps` frames per second (ANY-maze's "save plot-playback as a video"), over the video playing underneath
    when `frames` is given (see :class:`TrackAnimation`).  The figure is rendered with Agg at its own size and DPI
    (made even for the H.264 encoders).  Returns the path, or None when stopped."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from .video import VideoRecorder

    anim = TrackAnimation(fig, track, trail_s, frames)
    if not anim.ok:
        raise ValueError("The track plot has no track to play")
    speed, fps = float(speed) if speed and speed > 0 else 1.0, float(fps) if fps and fps > 0 else 25.0
    canvas = FigureCanvasAgg(fig)
    span = anim.end - anim.start
    frames = max(1, int(math.ceil(span / speed * fps))) + 1  # the last frame shows the whole track / trail
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = None
    try:
        for i in range(frames):
            if should_stop is not None and should_stop():
                return None
            t = min(anim.end, anim.start + i * speed / fps)
            anim.show(t if i < frames - 1 else anim.end)
            canvas.draw()
            img = np.asarray(canvas.buffer_rgba())[:, :, :3][:, :, ::-1]  # RGBA -> BGR
            h, w = img.shape[0] & ~1, img.shape[1] & ~1
            img = np.ascontiguousarray(img[:h, :w])
            if rec is None:
                rec = VideoRecorder(str(path), fps, (w, h))
            rec.write(img)
            if progress is not None:
                progress((i + 1) / frames)
    finally:
        if rec is not None:
            rec.close()
        anim.show(anim.end)
    return path
