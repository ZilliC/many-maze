"""Animated playback of the track plot (Data page ▸ Track plots): the path is drawn progressively at a chosen speed,
with play / pause, a speed selector and a time slider."""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

from ....core.plots import TRACK_END_GID, TRACK_MARKER_GID, TRACK_PATH_GID
from ...icons import icon

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
SLIDER_STEPS = 1000
TICK_MS = 40  # 25 redraws per second


class TrackPlayback(QWidget):
    """Play / pause, speed and time slider under a track plot. attach(canvas, track) after each new figure: the
    path, the behaviour markers and the end marker are then shown up to the playback time, with the animal's
    current position as a dot. At the end of the track (and when detached) the whole track is shown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.play_btn = QToolButton()
        self.play_btn.setIcon(icon("play"))
        self.play_btn.setToolTip("Play the track (Space)")
        self.play_btn.setAutoRaise(True)
        self.play_btn.clicked.connect(self.toggle)
        self.speed_combo = QComboBox()
        for s in SPEEDS:
            self.speed_combo.addItem(f"{s:g}×", s)
        self.speed_combo.setCurrentIndex(SPEEDS.index(4.0))
        self.speed_combo.setToolTip("Playback speed (× real time)")
        self.speed_combo.currentIndexChanged.connect(lambda *_: self._reset_clock())
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.setValue(SLIDER_STEPS)
        self.slider.setToolTip("Time in the test: drag to show the track up to that moment")
        self.slider.valueChanged.connect(self._slider_moved)
        self.time_lbl = QLabel("")
        self.time_lbl.setMinimumWidth(110)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        lay.addWidget(self.play_btn)
        lay.addWidget(self.speed_combo)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.time_lbl)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.canvas = None
        self.t = np.zeros(0)
        self.time = 0.0
        self._wall = 0.0
        self._path = None
        self._full = None  # LineCollection: (segments, values); Line2D: (x, y)
        self._xy = np.zeros((0, 2))
        self._markers: list[tuple[float, object]] = []
        self._end = []
        self._now = None
        self._setting_slider = False
        self.setEnabled(False)

    # ------------------------------------------------------------------ setup
    def attach(self, canvas, track):
        """Animate the track plot on canvas (a PlotCanvas whose figure was made by core.plots.track_plot)."""
        self.pause()
        self.canvas, self._path, self._full, self._now = canvas, None, None, None
        self._markers, self._end = [], []
        self.t = np.asarray(track.t, float) if track is not None and len(track) else np.zeros(0)
        fig = canvas.figure if canvas is not None else None
        for ax in (fig.axes if fig is not None else []):
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
            self.detach()
            return
        if hasattr(self._path, "get_segments"):  # coloured by time / speed / a parameter
            segs = self._path.get_segments()
            arr = self._path.get_array()
            self._full = (segs, None if arr is None else np.asarray(arr))
            pts = [s[0] for s in segs] + ([segs[-1][1]] if segs else [])
            self._xy = np.asarray(pts, float).reshape(-1, 2)
        else:
            x, y = np.asarray(self._path.get_xdata(), float), np.asarray(self._path.get_ydata(), float)
            self._full = (x, y)
            self._xy = np.column_stack([x, y])
        n = min(len(self._xy), len(self.t))
        self.t, self._xy = self.t[:n], self._xy[:n]
        ax = self._path.axes
        self._now, = ax.plot([], [], "o", ms=8, color="#f97316", mec="white", mew=1.2, zorder=7, visible=False)
        self.setEnabled(True)
        self.time = self.end
        self._show(self.time, draw=False)

    def detach(self):
        self.pause()
        self.canvas, self._path, self._full, self._now = None, None, None, None
        self._markers, self._end = [], []
        self.t = np.zeros(0)
        self.time = 0.0
        self.time_lbl.setText("")
        self.setEnabled(False)

    @property
    def start(self) -> float:
        return float(self.t[0]) if len(self.t) else 0.0

    @property
    def end(self) -> float:
        return float(self.t[-1]) if len(self.t) else 0.0

    @property
    def speed(self) -> float:
        return float(self.speed_combo.currentData() or 1.0)

    def set_speed(self, speed: float):
        i = self.speed_combo.findData(float(speed))
        if i < 0:
            raise ValueError(f"speed {speed} not available: {SPEEDS}")
        self.speed_combo.setCurrentIndex(i)

    @property
    def playing(self) -> bool:
        return self.timer.isActive()

    # ------------------------------------------------------------------ control
    def play(self) -> bool:
        if self._path is None:
            return False
        if self.time >= self.end - 1e-9:  # at the end: start again
            self.time = self.start
        self._reset_clock()
        self.timer.start()
        self.play_btn.setIcon(icon("pause"))
        self.play_btn.setToolTip("Pause")
        self._show(self.time)
        return True

    def pause(self):
        self.timer.stop()
        self.play_btn.setIcon(icon("play"))
        self.play_btn.setToolTip("Play the track")

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def seek(self, t: float):
        """Show the track up to time t (s, in the track's time)."""
        if self._path is None:
            return
        self.time = float(min(max(t, self.start), self.end))
        self._reset_clock()
        self._show(self.time)

    def advance(self, wall_s: float):
        """Move the playback on by wall_s seconds of real time (× speed); stops at the end."""
        if self._path is None:
            return
        self.time = min(self.end, self.time + wall_s * self.speed)
        self._show(self.time)
        if self.time >= self.end:
            self.pause()

    def _reset_clock(self):
        self._wall = time.monotonic()

    def _tick(self):
        now = time.monotonic()
        dt, self._wall = now - self._wall, now
        self.advance(dt)

    def _slider_moved(self, v: int):
        if self._setting_slider or self._path is None:
            return
        self.seek(self.start + (self.end - self.start) * v / SLIDER_STEPS)

    # ------------------------------------------------------------------ drawing
    def frames_shown(self) -> int:
        """Number of track positions drawn at the current playback time."""
        return int(np.searchsorted(self.t, self.time + 1e-9, side="right")) if len(self.t) else 0

    def _show(self, t: float, draw: bool = True):
        n = self.frames_shown()
        full = n >= len(self.t)
        if hasattr(self._path, "get_segments"):
            segs, vals = self._full
            k = max(0, n - 1)
            self._path.set_segments(segs[:k])
            if vals is not None:
                self._path.set_array(vals[:k])
        else:
            x, y = self._full
            self._path.set_data(x[:n], y[:n])
        for tm, a in self._markers:
            a.set_visible(full or tm <= t)
        for a in self._end:
            a.set_visible(full)
        ok = np.flatnonzero(np.isfinite(self._xy[:n, 0])) if n else []
        if self._now is not None:
            if len(ok) and not full:
                self._now.set_data([self._xy[ok[-1], 0]], [self._xy[ok[-1], 1]])
                self._now.set_visible(True)
            else:
                self._now.set_visible(False)
        span = self.end - self.start
        self._setting_slider = True
        self.slider.setValue(int(round((t - self.start) / span * SLIDER_STEPS)) if span > 0 else SLIDER_STEPS)
        self._setting_slider = False
        self.time_lbl.setText(f"{t:.1f} / {self.end:.1f} s")
        if draw and self.canvas is not None and self.canvas.canvas is not None:
            self.canvas.canvas.draw_idle()
