"""Detection: "Ignore lighting changes" (global illumination matched to the background, per arena) and the animal
presets (mouse, rat, hooded rat, white animal on sawdust, zebrafish), with a synthetic two-tone hooded rat."""

import math

import cv2
import numpy as np
import pytest

from manymaze.core.tracking import (ANIMAL_PRESETS, ArenaTracker, DetectionSettings, animal_preset,
                                    apply_animal_preset, median_grey)

W, H = 320, 240


@pytest.fixture(scope="module")
def floor():
    rng = np.random.default_rng(0)
    f = np.clip(150 + rng.normal(0, 4, (H, W)), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(f, (3, 3), 0)


def arena(x0, y0, x1, y1):
    m = np.zeros((H, W), np.uint8)
    m[y0:y1, x0:x1] = 255
    return m


def animal_frame(floor, t, gain, region=None):
    """A dark animal moving through the arena; the light changes (gain) over the whole image or one region."""
    img = floor.astype(np.float32)
    cx, cy = 60 + 2 * t, 120 + 40 * math.sin(t / 10)
    cv2.ellipse(img, (int(round(cx)), int(round(cy))), (14, 7), 0, 0, 360, 40, -1)
    if region is None:
        img *= gain
    else:
        x0, y0, x1, y1 = region
        img[y0:y1, x0:x1] *= gain
    return np.clip(img, 0, 255).astype(np.uint8), (cx, cy)


def run(floor, settings, gains, mask, region=None):
    tr = ArenaTracker(settings, mask)
    tr.set_background(floor)
    errs, motion = [], []
    for t, g in enumerate(gains):
        f, (cx, cy) = animal_frame(floor, t, g, region)
        d = tr.process(f)[0][0]
        errs.append(math.hypot(d.x - cx, d.y - cy) if d.detected else math.inf)
        motion.append(d.motion)
    return np.array(errs), np.array(motion), tr


GAINS = [1.0] * 30 + [0.6] * 30 + [1.4] * 30  # the lights are dimmed, then turned up


@pytest.mark.parametrize("contrast", ["auto", "dark"])
def test_lighting_step_keeps_the_centre(floor, contrast):
    mask = arena(20, 20, 300, 220)
    s = DetectionSettings(contrast=contrast, min_area_px=40, max_area_px=2000, blur=3)
    errs, _, _ = run(floor, s, GAINS, mask)
    assert np.all(errs[:30] < 2)
    assert np.isinf(errs[30:60]).mean() > 0.9  # without the option the dimmed floor swamps the animal
    s.lighting_compensation = True
    errs, motion, tr = run(floor, s, GAINS, mask)
    assert errs.max() < 4
    assert tr.lighting_gain == pytest.approx(1 / 1.4, abs=0.03)
    # a light switched on is not movement (the motion reference is scaled too)
    assert motion[30] < 3 * np.median(motion[1:30]) + 50 and motion[60] < 3 * np.median(motion[1:30]) + 50


def test_lighting_is_matched_per_arena(floor):
    """Two arenas in one image; a lamp dims the right-hand one only: each tracker matches its own arena."""
    left, right = arena(10, 20, 140, 220), arena(180, 20, 310, 220)
    s = DetectionSettings(contrast="auto", min_area_px=40, max_area_px=2000, blur=3, lighting_compensation=True)
    a = ArenaTracker(s, left)
    b = ArenaTracker(s, right)
    for tr in (a, b):
        tr.set_background(floor)
    img = floor.astype(np.float32)
    cv2.ellipse(img, (80, 120), (14, 7), 0, 0, 360, 40, -1)
    cv2.ellipse(img, (240, 100), (14, 7), 0, 0, 360, 40, -1)
    img[:, 160:] *= 0.55
    img = np.clip(img, 0, 255).astype(np.uint8)
    da, db = a.process(img)[0][0], b.process(img)[0][0]
    assert math.hypot(da.x - 80, da.y - 120) < 2 and math.hypot(db.x - 240, db.y - 100) < 2
    assert a.lighting_gain == pytest.approx(1.0, abs=0.02) and b.lighting_gain == pytest.approx(1 / 0.55, abs=0.05)


def test_lighting_with_an_adaptive_background(floor):
    s = DetectionSettings(contrast="dark", background="adaptive", min_area_px=40, max_area_px=2000, blur=3,
                          lighting_compensation=True)
    errs, _, _ = run(floor, s, GAINS, arena(20, 20, 300, 220))
    assert errs.max() < 4


def test_median_grey():
    img = np.zeros((10, 10), np.uint8)
    img[:, 6:] = 200
    assert median_grey(img) == 0
    mask = np.zeros((10, 10), np.uint8)
    mask[:, 5:] = 255
    assert median_grey(img, mask) == 200 and math.isnan(median_grey(img, np.zeros_like(mask)))


def test_presets_set_the_expected_fields():
    assert [p["title"] for p in ANIMAL_PRESETS.values()] == ["Mouse", "Rat", "Hooded rat", "White animal on sawdust",
                                                             "Zebrafish"]
    for key in ANIMAL_PRESETS:
        uncal, cal = animal_preset(key), animal_preset(key, 20.0)
        # contrast, clean-up ("erosion"), the lighting option; the body-size limits only with a calibration
        for f in ("contrast", "morph_open", "morph_close", "blur", "lighting_compensation", "tail_strip"):
            assert f in uncal and f in cal, (key, f)
        assert "min_area_px" not in uncal and 0 < cal["min_area_px"] < cal["max_area_px"]
        assert all(cal[k] == 0 or cal[k] % 2 == 1 for k in ("blur", "morph_open", "morph_close"))
    assert animal_preset("hooded_rat")["contrast"] == "auto"  # both darker and lighter parts
    assert animal_preset("white_on_sawdust")["contrast"] == "light"
    assert animal_preset("white_on_sawdust")["lighting_compensation"] is True
    assert animal_preset("zebrafish")["contrast"] == "dark" and animal_preset("zebrafish")["morph_open"] == 0
    mouse, rat = animal_preset("mouse", 10.0), animal_preset("rat", 10.0)
    assert (mouse["min_area_px"], mouse["max_area_px"]) == (200, 4000) and rat["max_area_px"] == 30000
    assert (mouse["blur"], mouse["morph_open"], mouse["morph_close"]) == (5, 3, 7)  # today's defaults at 10 px/cm
    assert animal_preset("rat", 30.0)["morph_close"] > rat["morph_close"]  # sizes follow the image scale
    s = DetectionSettings(contrast="dark", min_area_px=5)
    changed = apply_animal_preset(s, "white_on_sawdust", 10.0)
    assert s.contrast == "light" and s.lighting_compensation and s.min_area_px == 200
    assert {"contrast", "lighting_compensation", "min_area_px", "max_area_px"} <= set(changed)
    assert apply_animal_preset(s, "white_on_sawdust", 10.0) == []
    keep = DetectionSettings(min_area_px=123, max_area_px=4567)
    apply_animal_preset(keep, "rat")
    assert (keep.min_area_px, keep.max_area_px) == (123, 4567)  # not calibrated: limits left as they were


def hooded_rat(bg=128):
    """A two-tone animal on a mid-grey floor: black hood (front third), white body."""
    img = np.full((H, W), bg, np.uint8)
    body = np.zeros((H, W), np.uint8)
    cv2.ellipse(body, (160, 120), (40, 18), 0, 0, 360, 255, -1)
    img[body > 0] = 230
    hood = body.copy()
    hood[:, :173] = 0
    img[hood > 0] = 25
    return np.full((H, W), bg, np.uint8), img, body


def test_hooded_rat_is_tracked_whole():
    bg, img, body = hooded_rat()
    ys, xs = np.nonzero(body)
    mask = arena(10, 10, 310, 230)
    for contrast in ("dark", "light"):  # either part alone: half an animal, centre off by a quarter body
        tr = ArenaTracker(DetectionSettings(contrast=contrast), mask)
        tr.set_background(bg)
        d = tr.process(img)[0][0]
        assert d.area < 0.75 * len(xs) and math.hypot(d.x - xs.mean(), d.y - ys.mean()) > 8
    s = DetectionSettings(contrast="dark")
    apply_animal_preset(s, "hooded_rat", 4.0)
    tr = ArenaTracker(s, mask)
    tr.set_background(bg)
    d = tr.process(img)[0][0]
    assert d.detected and abs(d.area - len(xs)) < 0.1 * len(xs)
    assert math.hypot(d.x - xs.mean(), d.y - ys.mean()) < 2
    # a narrow neck of floor colour between hood and body is closed by the preset
    img2 = img.copy()
    img2[:, 171:174][body[:, 171:174] > 0] = 128
    tr = ArenaTracker(s, mask)
    tr.set_background(bg)
    d = tr.process(img2)[0][0]
    assert abs(d.area - len(xs)) < 0.1 * len(xs) and math.hypot(d.x - xs.mean(), d.y - ys.mean()) < 2


def test_settings_round_trip():
    s = DetectionSettings(lighting_compensation=True, pose_species="rat")
    assert DetectionSettings.from_dict(s.to_dict()) == s
    old = DetectionSettings.from_dict({"contrast": "dark", "threshold": 30})  # saved before these options
    assert old.lighting_compensation is False and old.pose_species == "mouse"
