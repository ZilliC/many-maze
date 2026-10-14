"""QA block B13, tracking on real video: structures of the floor that shift by a pixel, a track glued to the arena's
edge, a track on an empty arena, the hand at the start and end of a Morris water maze test."""

import cv2
import numpy as np

from manymaze.core.tracking import ArenaTracker, DetectionSettings

H, W = 240, 320


def _grid(shift=0):
    """A dark floor with a grid of bright thin lines (cage grid) and a bright vertical bar along the left edge
    (a glass wall); `shift`: how far the whole picture has moved sideways (camera or wall vibration)."""
    img = np.full((H, W), 30, np.uint8)
    for x in range(20, W, 60):
        cv2.line(img, (x + shift, 0), (x + shift, H - 1), 200, 3)
    for y in range(20, H, 60):
        cv2.line(img, (0, y), (W - 1, y), 200, 3)
    cv2.line(img, (6 + shift, 0), (6 + shift, H - 1), 220, 5)
    return img


def test_a_grid_that_shifts_one_pixel_does_not_hide_the_animal():
    """H-025: a grid or glass wall that moves a pixel against the median background differs from it by far more than
    the threshold along every line: the lines joined into one huge blob (the animal inside it) or the wall stood alone
    as a thin blob that outscored the animal, and the track stuck to the arena's edge."""
    tr = ArenaTracker(DetectionSettings(head_tail=False, record_outline=False))
    tr.set_background(_grid(0))
    errors = []
    for i in range(60):
        f = _grid(1 if i % 2 else 0)
        x, y = 80 + 3 * i, 120 + 30 * np.sin(i / 8)
        cv2.ellipse(f, (int(x), int(y)), (14, 7), 0, 0, 360, 100, -1)
        d = tr.process(f)[0][0]
        errors.append(np.hypot(d.x - x, d.y - y) if d.detected else np.inf)
    assert np.percentile(errors, 80) < 6, np.round(errors)
