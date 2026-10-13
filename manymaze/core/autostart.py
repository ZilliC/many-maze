"""Automatic test start in recorded videos: when the animal is first detected, or when the experimenter's hand has
put the animal in the apparatus and left the image (Project.start_mode "experimenter_leaves"; live tests do the
same frame by frame, see live.LiveSession).

The hand (or arm) is a larger object than the animal: frames whose detected object is more than `factor` times the
animal's usual area (the median area of the track) are "hand" frames.  Only the hand that puts the animal in counts:
the hand frames seen before the animal has been alone for `alone_s` seconds (a hand that flickers in and out of the
arena while it lets go, or comes back at once to put the animal right, is the same release).  A hand seen after the
animal has swum / walked alone that long is the experimenter taking it out at the end (or stepping in during the
test) and is ignored: a video without a release hand starts at the first detection.

After the last hand frame the arm, partly outside the arena, is often still tracked for a moment at about the
animal's size, until the tracked object jumps to the animal: the test starts at that jump (within HAND_END_S), or,
when the tracker stayed on the animal, at the first frame back at the animal's size."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .track import Track

HAND_FACTOR = 1.6  # a detected object this many times the animal's usual (median) area is the experimenter
HAND_END_FACTOR = 1.25  # back at the animal's size: below this many times its usual area
HAND_END_S = 1.0  # the arm may still be tracked this long after the last hand frame
JUMP_BODY = 2.0  # ... until the tracked object jumps this many times the animal's size (√ usual area)
HAND_ALONE_S = 2.0  # a hand seen after the animal has been alone this long is not the release (it is the removal)
SHORT_START_SHARE = 0.5  # warn when the automatic start leaves less than this share of the tracked video


@dataclass
class AutoStart:
    """The result of :func:`find_start`. t: the test start (s, video time) or None (start at the first detection);
    release: (first, last) time of the hand that put the animal in; ignored: time of the largest object seen after
    the animal had been alone (the hand taking it out, ignored); warning: the start leaves little of the video."""

    t: float | None
    release: tuple[float, float] | None = None
    ignored: float | None = None
    warning: str = ""

    @property
    def note(self) -> str:
        """How the test start was found (the track's ``start`` note)."""
        if self.t is not None:
            return "experimenter left"
        if self.ignored is not None:
            return (f"first detection (no hand putting the animal in; the large object at {self.ignored:.2f} s, after "
                    f"the animal had been alone, was taken as the hand taking it out)")
        return "first detection (no experimenter seen)"


def _usual_and_threshold(track: Track, factor: float, area_px: float) -> tuple[np.ndarray, float, float]:
    det = np.asarray(track.detected, bool) & np.isfinite(track.area)
    if not det.any():
        return det, 0.0, 0.0
    usual = float(np.median(track.area[det]))  # the animal alone most of the time: its usual area
    return det, usual, (area_px if area_px and area_px > 0 else factor * usual)


def hand_frames(track: Track, factor: float = HAND_FACTOR, area_px: float = 0.0) -> np.ndarray:
    """Frames in which the detected object is the experimenter (area above area_px, or `factor` × the animal's
    usual area), wherever they are in the video."""
    det, _usual, thr = _usual_and_threshold(track, factor, area_px)
    return det & (track.area > thr) if det.any() else det


def find_start(track: Track, factor: float = HAND_FACTOR, area_px: float = 0.0,
               alone_s: float = HAND_ALONE_S) -> AutoStart:
    """When the experimenter's hand that put the animal in has left (see the module notes)."""
    det, usual, thr = _usual_and_threshold(track, factor, area_px)
    if not det.any():
        return AutoStart(None)
    t = track.t
    area = track.area
    hand = det & (area > thr)
    det_idx = np.flatnonzero(det)
    last = None  # the last hand frame of the release
    first_hand = None
    alone_from = None  # first frame with the animal alone since the start / the last hand frame
    settled = None  # the animal has been alone for alone_s here: later hands are not the release
    for i in det_idx:
        if hand[i]:
            if alone_from is not None and t[i] - t[alone_from] >= alone_s:
                settled = i
                break
            if first_hand is None:
                first_hand = i
            last, alone_from = i, None
        else:
            if alone_from is None:
                alone_from = i
            if t[i] - t[alone_from] >= alone_s:
                settled = i
                break
    later = det_idx[(det_idx >= settled) & hand[det_idx]] if settled is not None else ()
    ignored = float(t[later[np.argmax(area[later])]]) if len(later) else None  # the hand taking the animal out
    if last is None:
        return AutoStart(None, ignored=ignored)
    release = (float(t[first_hand]), float(t[last]))
    # the arm leaving (see the module notes)
    jump = JUMP_BODY * np.sqrt(usual)
    end = min(HAND_END_FACTOR * usual, thr)
    x, y = track.x, track.y
    start = small = None
    prev = last
    for j in det_idx[det_idx > last]:
        if t[j] > t[last] + HAND_END_S:
            break
        if np.hypot(x[j] - x[prev], y[j] - y[prev]) > jump:
            start = j
            break
        if small is None and area[j] <= end:
            small = j
        prev = j
    if start is None:
        start = small
    if start is None:
        rest = det_idx[(det_idx > last) & ~hand[det_idx]]
        if len(rest) and t[rest[-1]] > t[last] + HAND_END_S:
            rest = rest[t[rest] > t[last] + HAND_END_S]
        if not len(rest):
            return AutoStart(None, release, ignored)
        start = rest[0]
    t0 = float(t[start])
    t_first, t_end = float(t[np.flatnonzero(det)[0]]), float(t[-1])
    warning = ""
    if t_end > t_first and t_end - t0 < SHORT_START_SHARE * (t_end - t_first):
        warning = (f"Automatic start at {t0:.2f} s (experimenter's hand left) leaves {t_end - t0:.1f} s of "
                   f"{t_end - t_first:.1f} s of video: check the start")
    return AutoStart(t0, release, ignored, warning)


def experimenter_leaves_start(track: Track, factor: float = HAND_FACTOR, area_px: float = 0.0,
                              alone_s: float = HAND_ALONE_S) -> float | None:
    """Time of the first frame with the animal alone after the experimenter's hand that put it in left; None when
    there is no such hand (or the animal never shows up after it): the test starts at the first detection."""
    return find_start(track, factor, area_px, alone_s).t
