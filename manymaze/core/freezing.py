"""Freezing thresholds: the manual "freezing starts below / ends above" motion levels, or thresholds derived
automatically from the distribution of the motion index of the test (ANY-maze 7: automatic freezing threshold with
a sensitivity setting).

The motion index is the pixel change between frames as a % of the animal's area.  Its distribution over a test
is bimodal when the animal freezes: a peak near zero (only camera noise changes) and a broad hump while it moves.
The automatic threshold separates the two on a log scale (Otsu's method, which maximises the between-class
variance); the sensitivity moves it as in ANY-maze, where a higher sensitivity detects smaller movements: 50 =
the separation itself, every 25 points above halves it (the animal must be stiller to count as freezing), every 25
points below doubles it.  Freezing ends at 1.5 × the start threshold (hysteresis, as
with the manual default 2 % / 3 %).
"""

from __future__ import annotations

import math

import numpy as np

MODES = {"manual": "Manual thresholds", "auto": "Automatic (from the test's motion)"}
MIN_SAMPLES = 50  # fewer motion samples than this: the manual thresholds are used
OFF_RATIO = 1.5  # freezing ends at this multiple of the start threshold
_EPS = 0.1  # % added before the log so that zero motion is finite
_LIMITS = (0.05, 50.0)  # automatic start threshold, % of the body


def _otsu(values: np.ndarray, bins: int = 64) -> float:
    hist, edges = np.histogram(values, bins=bins)
    if hist.sum() == 0:
        return math.nan
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids)
    mu0 = np.divide(m0, w0, out=np.zeros(len(w0)), where=w0 > 0)
    mu1 = np.divide(m0[-1] - m0, w1, out=np.zeros(len(w1)), where=w1 > 0)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return float(edges[int(np.argmax(between)) + 1])


def auto_thresholds(motion_pct, sensitivity: float = 50.0) -> tuple[float, float] | None:
    """(start, end) freezing thresholds (% of body) from a motion-index series, or None with too few samples /
    no spread.  sensitivity 0–100 (higher: smaller movements end freezing, so less freezing)."""
    v = np.asarray(motion_pct, float)
    v = v[np.isfinite(v) & (v >= 0)]
    if len(v) < MIN_SAMPLES:
        return None
    lv = np.log10(v + _EPS)
    if np.ptp(lv) < 1e-6:
        return None
    thr = 10 ** _otsu(lv) - _EPS
    sens = float(np.clip(sensitivity if sensitivity is not None else 50.0, 0.0, 100.0))
    on = float(np.clip(thr * 2.0 ** ((50.0 - sens) / 25.0), *_LIMITS))
    return round(on, 3), round(on * OFF_RATIO, 3)


def thresholds(motion_pct, settings) -> tuple[float, float]:
    """The (start, end) thresholds the analysis uses: automatic when settings.freeze_threshold_mode is "auto" (and
    the test has enough motion samples), else the manual freeze_on_pct / freeze_off_pct."""
    manual = (settings.freeze_on_pct, settings.freeze_off_pct)
    if getattr(settings, "freeze_threshold_mode", "manual") != "auto":
        return manual
    return auto_thresholds(motion_pct, getattr(settings, "freeze_sensitivity", 50.0)) or manual


class LiveThresholds:
    """Automatic thresholds during a live test: re-estimated every `every_s` seconds from the motion seen so far
    (the manual thresholds until there are enough samples)."""

    def __init__(self, settings, every_s: float = 2.0, max_samples: int = 200_000):
        self.s = settings
        self.every_s = every_s
        self._values: list[float] = []
        self._max = max_samples
        self._next = 0.0
        self.current = (settings.freeze_on_pct, settings.freeze_off_pct)
        self.auto = getattr(settings, "freeze_threshold_mode", "manual") == "auto"

    def update(self, t: float, motion_pct: float) -> tuple[float, float]:
        if not self.auto:
            return self.current
        if math.isfinite(motion_pct) and len(self._values) < self._max:
            self._values.append(motion_pct)
        if t >= self._next:
            self._next = t + self.every_s
            self.current = thresholds(self._values, self.s)
        return self.current
