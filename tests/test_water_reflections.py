"""One animal among several blobs (H-P13): on water the moving reflection of a lamp breaks into pieces as large as
the animal but faint, and the experimenter's arm is far larger than the animal. The animal is the blob that stands
out most (area times contrast past the threshold), with continuity and the animal's usual size, not the largest."""

from pathlib import Path

import cv2
import numpy as np

from manymaze.core.tracking import ArenaTracker, DetectionSettings, downscaled_settings

H, W = 400, 400
WATER = 120


def _scene(animal=None, blobs=()):
    """A grey frame of water with the animal (an ellipse, grey 200: 80 above the water) at ``animal`` and extra
    ``blobs`` (x, y, rx, ry, grey)."""
    img = np.full((H, W), WATER, np.uint8)
    for x, y, rx, ry, g in blobs:
        cv2.ellipse(img, (int(x), int(y)), (int(rx), int(ry)), 0, 0, 360, int(g), -1)
    if animal is not None:
        cv2.ellipse(img, (int(animal[0]), int(animal[1])), (16, 7), 0, 0, 360, 200, -1)
    return img


def _tracker(**kw):
    tr = ArenaTracker(DetectionSettings(head_tail=False, record_outline=False, **kw))
    tr.set_background(np.full((H, W), WATER, np.uint8))
    return tr


def _found_at(tr, frame, xy, tol=4.0):
    dets, _ = tr.process(frame)
    d = dets[0]
    return d.detected and np.hypot(d.x - xy[0], d.y - xy[1]) < tol


def test_faint_reflection_larger_than_the_animal_is_not_the_animal():
    # a reflection piece three times the animal's area, only 30 grey levels brighter than the water (threshold 25)
    frame = _scene((100, 100), [(280, 280, 30, 20, WATER + 30)])
    assert _found_at(_tracker(), frame, (100, 100))


def test_reflection_in_the_first_frame_and_later_ones():
    """No history yet (first frame) and with it: the sharp animal wins either way, wherever the reflection is."""
    tr = _tracker()
    for i in range(30):
        ax = 80 + 4 * i
        refl = [(320, 80 + 8 * i, 34, 18, WATER + 32), (90, 320, 26, 22, WATER + 28)]
        assert _found_at(tr, _scene((ax, 200), refl), (ax, 200)), i


def test_far_decoy_of_similar_contrast_does_not_steal_the_track():
    """An object as sharp as the animal and somewhat larger appearing far away (another animal's toy, a floating
    pellet...) does not take the track from the animal it was following."""
    tr = _tracker()
    for i in range(10):
        assert _found_at(tr, _scene((100 + 2 * i, 100)), (100 + 2 * i, 100))
    decoy = [(320, 320, 20, 9, 200)]  # ~1.6 times the animal's area, same grey
    for i in range(10):
        assert _found_at(tr, _scene((120 + 2 * i, 100), decoy), (120 + 2 * i, 100)), i


def test_the_experimenters_arm_does_not_take_the_track():
    """Once the animal's usual size is known, a much larger blob next to it (the arm reaching in to pick it up),
    even one standing out more, is not the animal."""
    tr = _tracker()
    for i in range(40):
        assert _found_at(tr, _scene((150 + i, 200)), (150 + i, 200))
    for i in range(20):
        arm = [(190 + i, 120, 60, 18, 40)]  # dark sleeve, 80 grey levels below the water, ~10 times the animal
        assert _found_at(tr, _scene((190, 200), arm), (190, 200)), i


def test_a_hand_brighter_than_the_animal_does_not_take_the_track():
    """Under red light the experimenter's hand stands out far more than the animal: contrast beyond the animal's
    usual contrast does not count, so a hand somewhat larger than the animal, away from it, does not win."""
    tr = _tracker()
    dim = 150  # the animal only 30 grey levels above the water
    for i in range(40):
        f = _scene(None, [(120 + i, 200, 16, 7, dim)])
        assert _found_at(tr, f, (120 + i, 200))
    for i in range(20):
        hand = [(320, 60 + 2 * i, 20, 9, 250)]  # 1.6 times the animal's area, 130 grey levels above the water
        assert _found_at(tr, _scene(None, [(160, 200, 16, 7, dim)] + hand), (160, 200)), i


def test_threshold_method_prefers_the_brightest_blob():
    """Plain thresholding (a light animal brighter than a level): the blob furthest past the level wins."""
    tr = ArenaTracker(DetectionSettings(method="threshold", contrast="light", threshold=160, head_tail=False,
                                        record_outline=False))
    frame = _scene((100, 100), [(280, 280, 26, 18, 166)])  # four times the area, just past the level
    assert _found_at(tr, frame, (100, 100))


def test_single_blob_unchanged():
    tr = _tracker()
    dets, _ = tr.process(_scene((200, 150)))
    assert dets[0].detected and abs(dets[0].x - 200) < 2 and abs(dets[0].y - 150) < 2


def test_real_water_maze_frames():
    """Regression fixture from real video (H-P13): white-light Morris water maze, 29.04.2025, the first three trials
    (``2025-04-29 20-00-56``, ``20-01-42``, ``20-11-06``), pool crop (350, 0, 620, 610) halved to 310x305 grey.
    Per trial the median background and three frames: two where the previous tracker took a piece of the lamp's
    reflection (at this size) and one where it did not. Labels (trial, frame, x, y) are the rat's centre from its
    colour (warm against the blue water and the neutral reflection), checked by eye. Each frame is tracked on its
    own (no history): the contrast of the blobs alone has to find the rat."""
    z = np.load(Path(__file__).parent / "data" / "mwm_reflections.npz")
    s = downscaled_settings(DetectionSettings(), 2)
    h, w = z["frames"].shape[1:]
    yy, xx = np.mgrid[0:h, 0:w]
    pool = ((np.hypot(xx - w / 2, yy - h / 2) < min(w, h) / 2) * 255).astype(np.uint8)
    for (k, fr, x, y), img in zip(z["labels"], z["frames"]):
        tr = ArenaTracker(s, pool)
        tr.set_background(z["backgrounds"][int(k)])
        d = tr.process(img)[0][0]
        assert d.detected and np.hypot(d.x - x, d.y - y) < 8, (int(k), int(fr), d.x, d.y, x, y)


def test_ghost_of_the_animal_in_a_median_background_does_not_hold_the_track():
    """Novel tank (H-006): the animal sat in one place for most of the video, so the median background contains it.
    Where it sat the empty tank now differs from the model: a ghost the size of the animal that never fades. The
    animal swimming away elsewhere (somewhat larger, so not near-tied) must not be dropped for being far from the
    ghost the track had been following: the ghost is absorbed into the model."""
    ghost_at = (200, 330)
    bg = np.full((H, W), WATER, np.uint8)
    cv2.ellipse(bg, ghost_at, (16, 7), 0, 0, 360, 200, -1)
    tr = ArenaTracker(DetectionSettings(head_tail=False, record_outline=False))
    tr.set_background(bg)
    # the animal darts away from its old spot and swims on up the tank, a little bigger
    for i in range(30):
        f = np.full((H, W), WATER, np.uint8)
        tr.process(f)  # empty tank: only the ghost is seen, and must not stay a detection
    last = None
    for i in range(30):
        y = 200 - 4 * i  # appears far above the ghost (a quick dart), then swims on
        f = np.full((H, W), WATER, np.uint8)
        cv2.ellipse(f, (200, y), (22, 9), 0, 0, 360, 200, -1)
        dets, _ = tr.process(f)
        last = dets[0]
    assert last.detected and np.hypot(last.x - 200, last.y - 84) < 4, (last.x, last.y)
