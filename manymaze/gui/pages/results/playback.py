"""Animated playback of the track plot (Data page ▸ Track plots): the path is drawn progressively at a chosen speed,
with play / pause, a speed selector, a trail length (show only the last seconds) and a time slider."""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

from ....core.playback import TRAILS, TrackAnimation
from ...icons import icon

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
SLIDER_STEPS = 1000
TICK_MS = 40  # 25 redraws per second


class TrackPlayback(QWidget):
    """Play / pause, speed, trail and time slider under a track plot. attach(canvas, track) after each new figure:
    the path, the behaviour markers and the end marker are then shown up to the playback time, with the animal's
    current position as a dot. At the end of the track (and when detached) the whole track is shown, unless a
    trail length is chosen: then only the last seconds are ever drawn."""

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
        self.trail_combo = QComboBox()
        for s in TRAILS:
            self.trail_combo.addItem("Whole track" if not s else f"Last {s:g} s", s)
        self.trail_combo.setToolTip("Trail: show the whole track up to the current moment, or only its last seconds")
        self.trail_combo.currentIndexChanged.connect(lambda *_: self._trail_changed())
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
        lay.addWidget(self.trail_combo)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.time_lbl)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.canvas = None
        self.anim: TrackAnimation | None = None
        self.track = None
        self.time = 0.0
        self._wall = 0.0
        self._setting_slider = False
        self.setEnabled(False)

    # ------------------------------------------------------------------ setup
    def attach(self, canvas, track):
        """Animate the track plot on canvas (a PlotCanvas whose figure was made by core.plots.track_plot)."""
        self.pause()
        self.canvas, self.track = canvas, track
        self.anim = TrackAnimation(canvas.figure if canvas is not None else None, track, self.trail_s)
        if not self.anim.ok:
            self.detach()
            return
        self.setEnabled(True)
        self.time = self.end
        self._show(self.time, draw=False)

    def detach(self):
        self.pause()
        self.canvas, self.anim, self.track = None, None, None
        self.time = 0.0
        self.time_lbl.setText("")
        self.setEnabled(False)

    @property
    def t(self) -> np.ndarray:
        return self.anim.t if self.anim is not None else np.zeros(0)

    @property
    def _path(self):
        return self.anim.path if self.anim is not None else None

    @property
    def _now(self):
        return self.anim._now if self.anim is not None else None

    @property
    def _end(self) -> list:
        return self.anim._end if self.anim is not None else []

    @property
    def start(self) -> float:
        return self.anim.start if self.anim is not None else 0.0

    @property
    def end(self) -> float:
        return self.anim.end if self.anim is not None else 0.0

    @property
    def speed(self) -> float:
        return float(self.speed_combo.currentData() or 1.0)

    def set_speed(self, speed: float):
        i = self.speed_combo.findData(float(speed))
        if i < 0:
            raise ValueError(f"speed {speed} not available: {SPEEDS}")
        self.speed_combo.setCurrentIndex(i)

    @property
    def trail_s(self) -> float:
        """Seconds of track shown while playing (0 = the whole track up to the current moment)."""
        return float(self.trail_combo.currentData() or 0.0)

    def set_trail(self, trail_s: float):
        i = self.trail_combo.findData(float(trail_s))
        if i < 0:
            raise ValueError(f"trail {trail_s} not available: {TRAILS}")
        self.trail_combo.setCurrentIndex(i)

    def _trail_changed(self):
        if self.anim is not None:
            self.anim.trail_s = self.trail_s
            self._show(self.time)

    @property
    def playing(self) -> bool:
        return self.timer.isActive()

    # ------------------------------------------------------------------ control
    def play(self) -> bool:
        if self.anim is None:
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
        if self.anim is None:
            return
        self.time = float(min(max(t, self.start), self.end))
        self._reset_clock()
        self._show(self.time)

    def advance(self, wall_s: float):
        """Move the playback on by wall_s seconds of real time (× speed); stops at the end."""
        if self.anim is None:
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
        if self._setting_slider or self.anim is None:
            return
        self.seek(self.start + (self.end - self.start) * v / SLIDER_STEPS)

    # ------------------------------------------------------------------ drawing
    def frames_shown(self) -> int:
        """Number of track positions drawn at the current playback time (from the start of the track or trail)."""
        return (self.anim.frames_shown(self.time) - self.anim.first_shown(self.time)) if self.anim is not None else 0

    def _show(self, t: float, draw: bool = True):
        if self.anim is None:
            return
        self.anim.show(t)
        span = self.end - self.start
        self._setting_slider = True
        self.slider.setValue(int(round((t - self.start) / span * SLIDER_STEPS)) if span > 0 else SLIDER_STEPS)
        self._setting_slider = False
        self.time_lbl.setText(f"{t:.1f} / {self.end:.1f} s")
        if draw and self.canvas is not None and self.canvas.canvas is not None:
            self.canvas.canvas.draw_idle()
