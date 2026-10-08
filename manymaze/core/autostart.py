"""Automatic test start in recorded videos: when the animal is first detected, or when the experimenter's hand has
put the animal in the apparatus and left the image (Project.start_mode "experimenter_leaves"; live tests do the
same frame by frame, see live.LiveSession).

The hand (or arm) is a much larger object than the animal: frames whose detected object is more than
`factor` times the animal's usual area (its median area once the hand has gone) are "hand" frames.  The test
starts at the first frame showing the animal alone after the first hand episode (hand frames up to `gap_s` apart
belong to one episode — the hand flickers in and out of the arena while it lets go)."""

from __future__ import annotations

import numpy as np

from .track import Track

HAND_FACTOR = 3.0  # a detected object this many times the animal's usual area is the experimenter
HAND_GAP_S = 1.0


def hand_frames(track: Track, factor: float = HAND_FACTOR, area_px: float = 0.0) -> np.ndarray:
    """Frames in which the detected object is the experimenter (area above area_px, or `factor` × the animal's
    usual area)."""
    det = np.asarray(track.detected, bool) & np.isfinite(track.area)
    if not det.any():
        return np.zeros(len(track), bool)
    if not area_px or area_px <= 0:
        areas = track.area[det]
        usual = float(np.median(areas[len(areas) // 2:]))  # the second half: the animal alone
        area_px = factor * usual
    return det & (track.area > area_px)


def experimenter_leaves_start(track: Track, factor: float = HAND_FACTOR, area_px: float = 0.0,
                              gap_s: float = HAND_GAP_S) -> float | None:
    """Time of the first frame with the animal alone after the experimenter's hand left; None when no hand was
    seen (or the animal never shows up after it)."""
    hand = hand_frames(track, factor, area_px)
    idx = np.flatnonzero(hand)
    if not len(idx):
        return None
    t = track.t
    last = idx[0]
    for j in idx[1:]:
        if t[j] - t[last] > gap_s:
            break
        last = j
    after = np.flatnonzero(np.asarray(track.detected, bool) & ~hand & (np.arange(len(track)) > last))
    return float(t[after[0]]) if len(after) else None
