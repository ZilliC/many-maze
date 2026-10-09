"""Audit 2026-10-08, tracking / video: adaptive-background ghosts, identity memory, per-arena cropping, hidden
animals, motion with a frame step, pose-only recoveries, pose models per job, file pacing, multi-well and Y-maze
templates, entry hysteresis, track smoothing, demo paths."""

import math
import os
import subprocess
import sys
import time

import cv2
import numpy as np
import pytest

from manymaze.core import demo, synthetic as syn, templates, tracking
from manymaze.core.camera import FramePacer
from manymaze.core.occupancy import _entry_hysteresis
from manymaze.core.track import Track
from manymaze.core.tracking import ArenaJob, ArenaTracker, DetectionSettings, compute_background, track_video

FLOOR = 200


def _frame(animals, size=(240, 320), noise=0, seed=0, colour=False, floor=FLOOR):
    """A floor with dark elliptical animals: animals = [(x, y, half length, half width, angle deg), ...]."""
    h, w = size
    img = np.full((h, w), floor, np.uint8)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(int) + rng.normal(0, noise, img.shape).round().astype(int), 0, 255).astype(np.uint8)
    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for x, y, a, b, ang in animals:
        cv2.ellipse(out, (int(round(x)), int(round(y))), (int(a), int(b)), ang, 0, 360,
                    (30, 30, 230) if colour else (40, 40, 40), -1)
        # a tail: thin line behind the body
        r = math.radians(ang)
        cv2.line(out, (int(x - a * math.cos(r)), int(y - a * math.sin(r))),
                 (int(x - 1.8 * a * math.cos(r)), int(y - 1.8 * a * math.sin(r))),
                 (30, 30, 230) if colour else (40, 40, 40), 2)
    return out


# ---------------------------------------------------------------------------------- adaptive background ghosts
def test_adaptive_background_absorbs_ghost_of_animal_present_at_start():
    """The animal sits at x=60 in the first frame (which becomes the model), then walks to x=220: the empty floor
    where it sat must not stay detected as a permanent "ghost" animal."""
    s = DetectionSettings(background="adaptive", contrast="auto")
    tr = ArenaTracker(s)
    xs = [60.0] * 20 + list(np.linspace(60, 220, 40)) + [220.0 + 10 * math.sin(i / 5) for i in range(140)]
    got = []
    for i, x in enumerate(xs):
        dets, fg = tr.process(_frame([(x, 120, 18, 9, 0)], noise=2, seed=i))
        got.append(dets[0].x if dets[0].detected else math.nan)
    late = np.array(got[80:])
    assert np.all(np.isfinite(late))
    assert np.max(np.abs(late - np.array(xs[80:]))) < 6  # the animal, not the ghost at x=60
    # the model now shows the floor where the animal started
    assert abs(int(tr.background[120, 60]) - FLOOR) < 10


def test_adaptive_background_normal_case_unchanged():
    """An animal that enters after the first frame is tracked throughout, and stays detected while it rests (a
    resting animal is not absorbed into the model)."""
    s = DetectionSettings(background="adaptive")
    tr = ArenaTracker(s)
    tr.process(_frame([], noise=2))
    xs = list(np.linspace(40, 260, 60)) + [260.0] * 150  # walks, then freezes for 150 frames
    for i, x in enumerate(xs):
        dets, _ = tr.process(_frame([(x, 100, 18, 9, 0)], noise=2, seed=i + 1))
        assert dets[0].detected and abs(dets[0].x - x) < 6, i


# ------------------------------------------------------------------------------------------ identity memory
def test_one_empty_frame_does_not_swap_animals():
    s = DetectionSettings(n_animals=2)
    tr = ArenaTracker(s)
    tr.set_background(np.full((240, 320), FLOOR, np.uint8))
    # the animal on the right is larger: blob order (by area) differs from identity order
    # at first the animal on the left is the larger (animal 1: identities start in blob-area order) ...
    for _ in range(5):
        dets, _ = tr.process(_frame([(60, 120, 20, 10, 0), (220, 120, 14, 8, 0)]))
    assert abs(dets[0].x - 60) < 6 and abs(dets[1].x - 220) < 6
    # ... then the one on the right stretches: blob order and identity order now differ
    small, big = (60, 120, 14, 8, 0), (220, 120, 20, 10, 0)
    for _ in range(5):
        dets, _ = tr.process(_frame([small, big]))
        assert abs(dets[0].x - 60) < 6 and abs(dets[1].x - 220) < 6
    dets, _ = tr.process(_frame([]))  # nothing detected for one frame
    assert not any(d.detected for d in dets)
    for _ in range(3):
        dets, _ = tr.process(_frame([small, big]))
        assert abs(dets[0].x - 60) < 6 and abs(dets[1].x - 220) < 6


def test_identity_memory_gate():
    """A detection far beyond reach of the only animal remembered goes to the animal whose memory has faded."""
    s = DetectionSettings(n_animals=2)
    tr = ArenaTracker(s)
    tr.identity_memory_frames = 5
    tr.set_background(np.full((240, 320), FLOOR, np.uint8))
    for _ in range(3):
        dets, _ = tr.process(_frame([(40, 40, 14, 8, 0), (160, 200, 14, 8, 0)]))
    left = 0 if dets[0].x < 100 else 1
    for _ in range(8):  # the other animal hides long enough to be forgotten
        dets, _ = tr.process(_frame([(40, 40, 14, 8, 0)]))
        assert dets[left].detected and not dets[1 - left].detected
    dets, _ = tr.process(_frame([(280, 200, 14, 8, 0)]))  # 290 px in one frame: not the animal seen last
    assert not dets[left].detected and dets[1 - left].detected and abs(dets[1 - left].x - 280) < 6


# ------------------------------------------------------------------------------------------ hidden animal
def test_hidden_animal_does_not_split_the_visible_one():
    s = DetectionSettings(n_animals=2)
    tr = ArenaTracker(s)
    tr.set_background(np.full((240, 320), FLOOR, np.uint8))
    a, b = (80, 120, 18, 9, 0), (240, 120, 18, 9, 0)
    for _ in range(10):
        dets, _ = tr.process(_frame([a, b]))
    ia = 0 if abs(dets[0].x - 80) < 3 else 1
    area = dets[ia].area
    for _ in range(10):  # b hides (in a shelter)
        dets, _ = tr.process(_frame([a]))
        assert dets[ia].detected and abs(dets[ia].x - 80) < 3 and dets[ia].area > 0.8 * area
        assert not dets[1 - ia].detected
    # two animals huddled together (one blob of twice the area) are still split
    dets, _ = tr.process(_frame([(150, 120, 18, 9, 0), (170, 120, 18, 9, 0)]))
    assert all(d.detected for d in dets)


# ------------------------------------------------------------------------------------- per-arena cropping
class _FullFrame(ArenaTracker):
    """The tracker as it was: every arena processes the whole frame."""

    def _region(self, shape_hw):
        self._mask_c = self.mask
        return None


def _wells(size=(240, 320)):
    masks = []
    for cx, cy in ((70, 60), (230, 60), (70, 180), (230, 180)):
        m = np.zeros(size, np.uint8)
        cv2.circle(m, (cx, cy), 52, 255, -1)
        masks.append(m)
    return masks


@pytest.mark.parametrize("kw", [
    dict(),
    dict(erase_thin_px=3),
    dict(threshold=0, contrast="auto"),
    dict(method="threshold", threshold=120),
    dict(background="adaptive"),
    dict(n_animals=2, morph_close=9, blur=7),
    dict(method="colour", target_colour="#ff0000", blur=3),
])
def test_cropped_arenas_match_whole_frame(kw):
    """Processing only the arena's region gives exactly the whole-frame result (contours, positions, motion, the
    foreground mask), arena edges included."""
    s = DetectionSettings(**kw)
    bg = np.full((240, 320), FLOOR, np.uint8)
    cv2.line(bg, (0, 30), (319, 35), 150, 2)  # a wire across the image (thin-structure eraser)
    pairs = []
    for m in _wells():
        a, b = ArenaTracker(s, m), _FullFrame(s, m)
        if s.method == "background" and s.background != "adaptive":
            a.set_background(bg)
            b.set_background(bg)
        pairs.append((a, b))
    for i in range(25):
        anim = []
        for cx, cy in ((70, 60), (230, 60), (70, 180), (230, 180)):
            # some animals run along / against the arena edge
            r = 30 + 22 * (i % 3) / 2
            ang = i * 0.4 + cx
            anim.append((cx + r * math.cos(ang), cy + r * math.sin(ang), 12, 6, math.degrees(ang) + 90))
            if s.n_animals == 2:
                anim.append((cx - 10, cy + 5 + (i % 4), 10, 5, 0))
        f = _frame(anim, noise=3, seed=i, colour=s.method == "colour")
        if s.method != "colour":
            f[25:40] = np.minimum(f[25:40], cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)[25:40])
        for a, b in pairs:
            cv2.setRNGSeed(i)  # k-means (merged animals) draws from OpenCV's global generator
            da, fa = a.process(f)
            cv2.setRNGSeed(i)
            db, fb = b.process(f)
            assert np.array_equal(fa, fb)
            assert len(da) == len(db)
            for x, y in zip(da, db):
                assert x.detected == y.detected
                for c in ("x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle"):
                    u, v = getattr(x, c), getattr(y, c)
                    assert (math.isnan(u) and math.isnan(v)) or u == v, (c, u, v)
                if x.contour is not None:
                    assert np.array_equal(x.contour, y.contour)


def test_many_arenas_cost_little_more_than_one():
    """24 wells in a 1280×720 frame: each arena processes its own region, not the whole frame."""
    plate = templates.multi_well_plate(100, 40, 1080, 640, n_wells=24, plate_width_cm=12.78)
    wells = templates.split_wells(plate)
    s = DetectionSettings()
    trs = [ArenaTracker(s, w.arena.mask((720, 1280))) for w in wells]
    bg = np.full((720, 1280), FLOOR, np.uint8)
    for t in trs:
        t.set_background(bg)
    f = _frame([(w.arena.centroid()[0], w.arena.centroid()[1], 8, 4, 0) for w in wells], size=(720, 1280))
    for t in trs:
        t.process(f)
    t0 = time.perf_counter()
    for _ in range(5):
        for t in trs:
            dets, _ = t.process(f)
            assert dets[0].detected
    per_frame = (time.perf_counter() - t0) / 5
    full = _FullFrame(s, wells[0].arena.mask((720, 1280)))
    full.set_background(bg)
    full.process(f)
    t0 = time.perf_counter()
    for _ in range(5):
        full.process(f)
    one_full = (time.perf_counter() - t0) / 5
    assert per_frame < 24 * one_full * 0.5


# ------------------------------------------------------------------------------- motion and the frame step
def test_motion_does_not_depend_on_frame_step(tmp_path):
    pos = syn.random_walk(60, (40, 40, 200, 200), speed=4, seed=3)
    vp = tmp_path / "v.avi"
    syn.make_video(vp, pos, size=(240, 240), fps=25, arena=("rect", 40, 40, 160, 160), seed=3)
    s1 = DetectionSettings(max_gap_s=0)
    s2 = DetectionSettings(max_gap_s=0, frame_step=3)
    t1 = track_video(str(vp), [ArenaJob(None, s1)])[0][0]
    t3 = track_video(str(vp), [ArenaJob(None, s2)])[0][0]
    assert len(t3) == math.ceil(len(t1) / 3)
    # every analysed frame's motion is its change from the frame just before, as with every frame analysed
    assert np.allclose(t3.motion[1:], t1.motion[3::3], equal_nan=True)


def test_background_window_past_the_end_raises(tmp_path):
    pos = syn.random_walk(25, (40, 40, 200, 200), seed=1)
    vp = tmp_path / "short.avi"
    syn.make_video(vp, pos, size=(240, 240), fps=25, arena=("rect", 40, 40, 160, 160), seed=1)
    s = DetectionSettings(start_time_s=5.0)
    with pytest.raises(ValueError, match="after the end of the video"):
        compute_background(str(vp), s)
    with pytest.raises(ValueError, match="after the end"):
        track_video(str(vp), [ArenaJob(None, s)])


# ---------------------------------------------------------------------------------------------------- pose
class _FakePose:
    """A pose model that always 'sees' an animal in the middle of the box it is given."""

    def __init__(self, provider="FakeProvider"):
        self.provider = provider
        self.calls = 0

    def predict(self, frame, boxes):
        self.calls += 1
        out = []
        for x0, y0, x1, y1 in boxes:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            out.append(np.array([[cx + 5, cy, 0.9], [cx, cy, 0.9], [cx - 5, cy, 0.9]]))
        return out

    def body_parts(self, k):
        return {"nose": tuple(k[0]), "centre": tuple(k[1]), "tail_base": tuple(k[2])}


def test_pose_only_recovery_is_capped():
    s = DetectionSettings(body_parts="pose")
    tr = ArenaTracker(s, pose=_FakePose())
    tr.set_background(np.full((240, 320), FLOOR, np.uint8))
    for _ in range(3):
        dets, _ = tr.process(_frame([(100, 100, 18, 9, 0)]))
    assert dets[0].detected
    seen = 0
    for _ in range(200):  # the animal is gone; the model keeps "seeing" it in the last box
        dets, _ = tr.process(_frame([]))
        seen += dets[0].detected
    assert seen == ArenaTracker.pose_only_max_frames
    dets, _ = tr.process(_frame([(150, 100, 18, 9, 0)]))  # back: found by its blob, pose refines it again
    assert dets[0].detected and abs(dets[0].x - 150) < 10
    dets, _ = tr.process(_frame([]))
    assert dets[0].detected  # a fresh pose-only stretch may begin


def test_pose_models_per_job(tmp_path, monkeypatch):
    made = {}

    def fake_estimator(settings, threads=0):
        key = (settings.pose_model, settings.pose_device)
        made[key] = made.get(key) or _FakePose(f"provider-{settings.pose_model}")
        return made[key]

    monkeypatch.setattr(tracking, "pose_estimator", fake_estimator)
    pos = syn.random_walk(10, (40, 40, 200, 200), seed=2)
    vp = tmp_path / "p.avi"
    syn.make_video(vp, pos, size=(240, 240), fps=25, arena=("rect", 40, 40, 160, 160), seed=2)
    jobs = [ArenaJob(None, DetectionSettings(body_parts="pose", pose_model="model_a")),
            ArenaJob(None, DetectionSettings(body_parts="pose", pose_model="model_b")),
            ArenaJob(None, DetectionSettings(body_parts="pose", pose_model="model_a")),
            ArenaJob(None, DetectionSettings())]
    out = track_video(str(vp), jobs)
    metas = [o[0].meta for o in out]
    assert [m.get("pose_model") for m in metas] == ["model_a", "model_b", "model_a", None]
    assert [m.get("pose_device") for m in metas] == ["provider-model_a", "provider-model_b",
                                                     "provider-model_a", None]
    assert len(made) == 2 and all(p.calls > 0 for p in made.values())


# -------------------------------------------------------------------------------------------- file pacing
class _Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.slept.append(d)
        self.t += d


def test_pacer_speed_change_keeps_position():
    c = _Clock()
    p = FramePacer(25, False, 1.0, clock=c, sleep=c.sleep)
    for _ in range(250):  # 10 s at normal speed
        p.next()
    c.slept.clear()
    p.speed = 0.2  # slow motion: the next frame comes 0.04 / 0.2 s later, not 40 s
    p.next()
    assert sum(c.slept) == pytest.approx(0.2, abs=0.05)
    assert max(c.slept) <= FramePacer.slice_s + 1e-9  # slept in short slices
    p.speed = 5.0  # fast: frames 8 ms apart, no burst to "catch up"
    c.slept.clear()
    p.next()
    p.next()
    assert sum(c.slept) == pytest.approx(0.016, abs=0.009) and len(c.slept) >= 1
    p.speed = 0  # as fast as possible
    c.slept.clear()
    for _ in range(10):
        p.next()
    assert not c.slept
    p.speed = 1.0
    t = c.t
    p.next()
    p.next()
    assert c.t - t == pytest.approx(0.04, abs=0.02)


def test_pacer_wait_is_interruptible():
    c = _Clock()
    flag = [False]
    p = FramePacer(25, False, 0.001, clock=c, sleep=c.sleep, stop=lambda: flag[0])
    p.next()
    real = time.monotonic()

    def sleep(d):
        c.sleep(d)
        if len(c.slept) >= 3:
            flag[0] = True

    p.sleep = sleep
    p.next()  # would wait 40 s at 0.001×
    assert len(c.slept) == 3 and time.monotonic() - real < 1
    # a speed change read while waiting shortens the wait at once
    c2 = _Clock()
    speed = [0.001]
    p2 = FramePacer(25, False, 0.001, clock=c2, sleep=c2.sleep, speed_source=lambda: speed[0])
    p2.next()

    def sleep2(d):
        c2.sleep(d)
        if len(c2.slept) == 2:
            speed[0] = 1.0

    p2.sleep = sleep2
    p2.next()
    assert sum(c2.slept) < 0.2


# ---------------------------------------------------------------------------------------------- templates
@pytest.mark.parametrize("n, pitch_cm", [(24, 1.93), (96, 0.9), (6, 3.912), (12, 2.601), (48, 1.308)])
def test_multi_well_pitch_matches_calibration(n, pitch_cm):
    ppc = 50.0
    w, h = 12.776 * ppc, 8.548 * ppc
    app = templates.multi_well_plate(10, 20, w, h, n_wells=n, plate_width_cm=12.776)
    assert app.px_per_cm == pytest.approx(ppc)
    c = {z.name: z.shape.centroid() for z in app.zones}
    a1, a2, b1 = c["Well A1"], c["Well A2"], c["Well B1"]
    assert (a2[0] - a1[0]) / app.px_per_cm == pytest.approx(pitch_cm, abs=1e-6)
    assert (b1[1] - a1[1]) / app.px_per_cm == pytest.approx(pitch_cm, abs=1e-6)
    ox, oy = templates.WELL_A1_OFFSET_CM[n]
    assert (a1[0] - 10) / ppc == pytest.approx(ox, abs=1e-6) and (a1[1] - 20) / ppc == pytest.approx(oy, abs=1e-6)
    if n == 96:  # ANSI/SLAS 4-2004: A1 at 14.38 mm × 11.24 mm
        assert (ox, oy) == (pytest.approx(1.438), pytest.approx(1.124))


def test_y_maze_fits_the_box():
    for (aw, al) in ((8.0, 35.0), (5.0, 40.0)):
        app = templates.y_maze(100, 50, 300, 400, arm_length_cm=al, arm_width_cm=aw)
        x0, y0, x1, y1 = app.arena.bounds()
        assert y0 == pytest.approx(50, abs=0.01) and y1 == pytest.approx(450, abs=0.01)
        assert app.px_per_cm == pytest.approx(400 / (1.5 * al + 1.183 * aw), rel=1e-3)
        # the arms are their real length in the calibration
        arm = next(z for z in app.zones if z.name == "Arm A").shape.bounds()
        assert (arm[3] - arm[1]) / app.px_per_cm == pytest.approx(al, rel=1e-6)


# ------------------------------------------------------------------------------------- entry hysteresis
def _entry_hysteresis_loop(frac, enter):
    """The original per-frame implementation."""
    leave = min(enter, 1.0 - enter)
    out = np.zeros(len(frac), bool)
    state = False
    for i, f in enumerate(frac):
        if np.isfinite(f):
            state = f >= enter if not state else f >= leave and f > 0
        out[i] = state
    return out


@pytest.mark.parametrize("enter", [0.0, 0.01, 0.2, 0.5, 0.7, 0.99, 1.0])
def test_entry_hysteresis_vectorised_matches_loop(enter):
    rng = np.random.default_rng(int(enter * 100))
    for k in range(20):
        n = int(rng.integers(0, 400))
        frac = rng.random(n)
        if k % 2:
            frac = np.round(frac, 1)  # values exactly at the thresholds
        frac[rng.random(n) < 0.1] = np.nan
        frac[rng.random(n) < 0.05] = 0.0
        frac[rng.random(n) < 0.05] = 1.0
        assert np.array_equal(_entry_hysteresis(frac, enter), _entry_hysteresis_loop(frac, enter))


# ------------------------------------------------------------------------------------------ track smoothing
def test_smooth_recomputes_angle():
    n = 50
    rng = np.random.default_rng(0)
    x = np.linspace(0, 100, n) + rng.normal(0, 2, n)
    y = 50 + rng.normal(0, 2, n)
    ang = np.linspace(0, 90, n)
    hx, hy = x + 10 * np.cos(np.radians(ang)) + rng.normal(0, 2, n), y + 10 * np.sin(np.radians(ang))
    tx, ty = x - 10 * np.cos(np.radians(ang)), y - 10 * np.sin(np.radians(ang))
    raw_angle = np.degrees(np.arctan2(hy - ty, hx - tx))
    tr = Track(t=np.arange(n) / 25, x=x, y=y, hx=hx, hy=hy, tx=tx, ty=ty, angle=raw_angle)
    sm = tr.smooth(5)
    assert np.allclose(sm.angle, np.degrees(np.arctan2(sm.hy - sm.ty, sm.hx - sm.tx)))
    assert not np.allclose(sm.angle, raw_angle)
    # orientation only (no head / tail): averaged as a direction, across ±180°
    a = np.where(np.arange(n) % 2, 179.0, -179.0)
    sm2 = Track(t=np.arange(n) / 25, x=x, y=y, angle=a).smooth(5)
    assert np.all(np.abs(np.abs(sm2.angle[2:-2]) - 180) < 1.0)
    # NaN stays NaN
    a[10] = np.nan
    assert np.isnan(Track(t=np.arange(n) / 25, x=x, y=y, angle=a).smooth(5).angle[10])


# ----------------------------------------------------------------------------------------------- demo
@pytest.mark.parametrize("n", [1, 750, 20000])
def test_wall_walk_returns_n_frames(n):
    p = demo._wall_walk(n, (50, 50, 350, 350), seed=4)
    assert len(p) == n


# ------------------------------------------------------------------------------- PyAV stays out of capture
def test_capture_modules_do_not_load_pyav():
    """PyAV (whose libavdevice duplicates OpenCV's, see docs/libavdevice-duplicate-classes.md) is loaded only when a
    file is decoded or recorded, not by the camera / live / tracking modules."""
    code = ("import sys; import manymaze.core.camera, manymaze.core.camsources, manymaze.core.live, "
            "manymaze.core.livegroup, manymaze.core.tracking; print('av' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False" and "AVFFrameReceiver" not in out.stderr
