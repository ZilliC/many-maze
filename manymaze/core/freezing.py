"""Freezing thresholds: the manual "freezing starts below / ends above" motion levels, or thresholds derived
automatically from the distribution of the motion index of the test (ANY-maze 7: automatic freezing threshold with
a sensitivity setting); and the immobility of the forced swim and tail suspension tests, from the struggle seen in
the image (see struggle_index).

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

from .series import drop_short_runs, ffill, seg_moving_average

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


# ---------------------------------------------------------------- forced swim / tail suspension immobility
# ANY-maze's Forced swim / Tail suspension mode (7.30) "works by analysing the frequency of movements in the images.
# 'Struggle' ... is characterised by higher frequency movements, whereas things like drifting and swinging are lower
# frequency movements", over "approximately one second's worth of images"; the animal is immobile once struggle has
# stopped for a short period. Here the struggle index is the motion index (pixel change, % of the body) without its
# slow part (its moving average over HIGH_PASS_S: the steady pixel change of an animal drifting in the water or
# swinging on its tail), averaged over STRUGGLE_WINDOW_S. The position of the animal is not used at all (no
# translation requirement), and the brief paddles that keep the head above water average out over the second.
IMMOBILITY_MODES = {"speed": "From the speed of the animal's centre",
                    "motion": "Forced swim / tail suspension: from the struggle in the image"}
HIGH_PASS_S = 0.12  # at least 3 frames
STRUGGLE_WINDOW_S = 1.0


def struggle_index(motion_pct, dt: float, breaks=None) -> np.ndarray:
    """Per frame: the struggle index (% of the body) of a motion-index series sampled every dt seconds: the
    high-frequency part of the motion averaged over the surrounding second (frames without a motion value keep the
    last one; all NaN without any). No averaging across a pause (breaks: frames that follow one)."""
    m = np.asarray(motion_pct, float)
    if not np.isfinite(m).any():
        return np.full(len(m), np.nan)
    dt = max(float(dt), 1e-6)
    m = ffill(m)
    hp = max(3, int(round(HIGH_PASS_S / dt))) | 1
    hf = np.abs(m - seg_moving_average(m, hp, breaks))
    return seg_moving_average(hf, max(1, int(round(STRUGGLE_WINDOW_S / dt))), breaks)


def immobility_from_motion(motion_pct, t: np.ndarray, dur: np.ndarray, dt: float, settings,
                           breaks=None) -> tuple[np.ndarray, np.ndarray]:
    """(mobile, struggle index) per frame for the forced swim and tail suspension tests: the animal struggles (is
    mobile) while its struggle index is at or above settings.fst_threshold_pct; it is immobile otherwise, once it
    has been for at least settings.min_fst_immobile_s (shorter still spells count as mobile)."""
    s = struggle_index(motion_pct, dt, breaks)
    with np.errstate(invalid="ignore"):
        mobile = np.nan_to_num(s, nan=0.0) >= float(settings.fst_threshold_pct)
    return drop_short_runs(mobile, t, dur, float(settings.min_fst_immobile_s), value=False), s


def fst_states(mobile: np.ndarray, struggle: np.ndarray, t: np.ndarray, dur: np.ndarray,
               settings) -> tuple[np.ndarray, np.ndarray]:
    """(climbing, swimming) per frame, the optional three-state split of the forced swim test: of the frames in
    which the animal struggles, those with a struggle index at or above settings.fst_climbing_pct (the most
    vigorous movements, forepaws scrabbling at the wall) are climbing, the others swimming. Climbing or swimming
    shorter than STRUGGLE_WINDOW_S (the index flickering about the threshold) joins the bout around it."""
    with np.errstate(invalid="ignore"):
        strong = np.nan_to_num(struggle, nan=0.0) >= float(settings.fst_climbing_pct)
    strong = drop_short_runs(drop_short_runs(strong, t, dur, STRUGGLE_WINDOW_S, value=True), t, dur,
                             STRUGGLE_WINDOW_S, value=False)
    mobile = np.asarray(mobile, bool)
    return mobile & strong, mobile & ~strong


class LiveStruggle:
    """The struggle index during a live test, over the motion seen in the last STRUGGLE_WINDOW_S (trailing windows,
    so a change shows about half a second later than in the analysis, which centres them)."""

    def __init__(self):
        self._m: list[tuple[float, float]] = []  # (t, motion %)
        self._hf: list[tuple[float, float]] = []  # (t, high-frequency part)

    def update(self, t: float, motion_pct: float) -> float:
        if math.isfinite(motion_pct):
            self._m.append((t, motion_pct))
        self._m = [x for x in self._m if x[0] >= t - HIGH_PASS_S - 1e-9][-50:]
        if self._m and math.isfinite(motion_pct):
            mean = sum(v for _, v in self._m) / len(self._m)
            self._hf.append((t, abs(motion_pct - mean)))
        self._hf = [x for x in self._hf if x[0] >= t - STRUGGLE_WINDOW_S + 1e-9]
        return sum(v for _, v in self._hf) / len(self._hf) if self._hf else 0.0


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
