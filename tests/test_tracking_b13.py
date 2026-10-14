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


def test_a_change_of_the_whole_arena_is_not_an_animal():
    """H-026: the lighting (or the camera's exposure) changed after the background was taken: the whole empty arena
    differs from it, and that one huge region was reported as the animal in every frame (98.8 % detected)."""
    mask = np.zeros((H, W), np.uint8)
    mask[20:220, 20:300] = 255
    tr = ArenaTracker(DetectionSettings(head_tail=False, record_outline=False), mask)
    tr.set_background(np.full((H, W), 60, np.uint8))
    dets = [tr.process(np.full((H, W), 140, np.uint8))[0][0] for _ in range(10)]
    assert not any(d.detected for d in dets)
    # a real animal on the same brighter floor is still found
    f = np.full((H, W), 60, np.uint8)
    cv2.ellipse(f, (150, 120), (20, 9), 0, 0, 360, 200, -1)
    d = tr.process(f)[0][0]
    assert d.detected and abs(d.x - 150) < 3


def test_a_hand_over_the_grid_at_the_start_is_not_a_ghost_in_the_background():
    """H-025 (fC1): the experimenter's head covers the arena in the first frames and hides the grid, which has sharp
    edges in the median background: the edge test took it for a ghost of the animal and copied it into the model,
    spoiling the floor (a brighter patch of the floor became a permanent "animal" that outscored the real one)."""
    floor = _grid(0)
    cv2.rectangle(floor, (225, 150), (290, 215), 75, -1)  # a brighter patch of the floor (also in the median model)
    tr = ArenaTracker(DetectionSettings(head_tail=False, record_outline=False))
    tr.set_background(floor)
    for _ in range(8):
        f = floor.copy()
        cv2.ellipse(f, (255, 180), (50, 45), 0, 0, 360, 12, -1)  # the head: dark, smooth, over grid lines and patch
        f = cv2.GaussianBlur(f, (0, 0), 3)
        tr.process(f)
    assert abs(int(tr.background[182, 258]) - 75) < 10
    errors = []
    for i in range(40):
        f = floor.copy()
        x, y = 60 + 3 * i, 100 + 20 * np.sin(i / 8)
        cv2.ellipse(f, (int(x), int(y)), (16, 8), 0, 0, 360, 130, -1)
        d = tr.process(f)[0][0]
        errors.append(np.hypot(d.x - x, d.y - y) if d.detected else np.inf)
    assert np.percentile(errors, 80) < 12, np.round(errors)  # not the patch, 150 px away
