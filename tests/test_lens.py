"""Lens distortion correction (core.lens): a distorted straight grid comes out straight, with the barrel strength
and with a checkerboard calibration; corrections per video test (tracking, background, start frame, video export,
batches) and per camera (SourceSpec, saved camera options)."""

import json

import cv2
import numpy as np
import pytest

from manymaze.core import lens as L
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.batch import tracking_batches
from manymaze.core.camera import SourceSpec, TransformedSource, camera_lenses, set_camera_settings
from manymaze.core.project import Project
from manymaze.core.tracking import ArenaJob, DetectionSettings, compute_background, track_video
from manymaze.core.video import VideoSource

W, H = 640, 480


def distort(img: np.ndarray, K, D, fill=230) -> np.ndarray:
    """The image a lens with K, D makes of a scene whose ideal (pinhole) image is `img`."""
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    und = cv2.undistortPoints(np.stack([xs.ravel(), ys.ravel()], 1).reshape(-1, 1, 2), K, D, P=K).reshape(h, w, 2)
    return cv2.remap(img, und[..., 0], und[..., 1], cv2.INTER_LINEAR, borderValue=fill)


def lines_image(horizontal: bool, step=80) -> np.ndarray:
    img = np.full((H, W), 230, np.uint8)
    if horizontal:
        for y in range(40, H, step):
            cv2.line(img, (0, y), (W - 1, y), 20, 3)
    else:
        for x in range(40, W, step):
            cv2.line(img, (x, 0), (x, H - 1), 20, 3)
    return img


def max_bend(img: np.ndarray, horizontal: bool) -> float:
    """The largest distance (px) of a dark line of the image from the straight line fitted to it."""
    dark = (img < 110).astype(np.uint8)
    n, lab = cv2.connectedComponents(dark)
    worst = 0.0
    for i in range(1, n):
        ys, xs = np.nonzero(lab == i)
        along, across = (xs, ys) if horizontal else (ys, xs)
        if np.ptp(along) < 0.5 * (W if horizontal else H):  # a stub cut by the image edge
            continue
        keys = np.unique(along)[3:-3]  # (the ends of a line fade out where the image ends)
        centre = np.array([across[along == k].mean() for k in keys])
        fit = np.polyval(np.polyfit(keys, centre, 1), keys)
        worst = max(worst, float(np.abs(centre - fit).max()))
    return worst


@pytest.mark.parametrize("horizontal", [True, False])
def test_barrel_strength_straightens_a_grid(horizontal):
    lens = L.LensCorrection.barrel(80)
    K, D = lens.matrices(W, H)
    bent = distort(lines_image(horizontal), K, D)
    assert max_bend(bent, horizontal) > 7  # the lens bends the lines
    straight = lens.apply(bent)
    assert straight.shape == bent.shape
    assert max_bend(straight, horizontal) < 1.0
    # keeping the whole image: still straight, smaller, black corners
    whole = L.LensCorrection.barrel(80, alpha=1.0).apply(bent)
    assert max_bend(np.where(whole == 0, 230, whole).astype(np.uint8), horizontal) < 1.0
    assert (whole == 0).sum() > 1000 and not (straight == 0).any()


def _board(cols, rows, sq=40):
    img = np.full(((rows + 3) * sq, (cols + 3) * sq), 255, np.uint8)
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                img[(r + 1) * sq:(r + 2) * sq, (c + 1) * sq:(c + 2) * sq] = 0
    return img


def checkerboard_views(K, D, n=12, pattern=(9, 6), seed=1):
    """Images of a checkerboard held at n poses in front of a camera with K, D."""
    tex = _board(*pattern)
    th, tw = tex.shape
    corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
    world = np.float64([[x / 40 - 2, y / 40 - 2, 0] for x, y in corners])
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        rvec = np.array([rng.uniform(-0.45, 0.45), rng.uniform(-0.45, 0.45), rng.uniform(-0.3, 0.3)])
        tvec = np.array([rng.uniform(-7, -1), rng.uniform(-5, 0), rng.uniform(14, 20)])
        pts, _ = cv2.projectPoints(world, rvec, tvec, K, None)
        flat = cv2.warpPerspective(tex, cv2.getPerspectiveTransform(corners, pts.reshape(-1, 2).astype(np.float32)),
                                   (W, H), borderValue=170)
        out.append(distort(flat, K, D, fill=170))
    return out


def test_checkerboard_calibration_straightens_a_grid(tmp_path):
    K = np.array([[520.0, 0, W / 2], [0, 520.0, H / 2], [0, 0, 1]])
    D = np.array([[-0.28, 0.08, 0, 0, 0]])
    images = checkerboard_views(K, D)
    views = [c for c in (L.find_checkerboard(im) for im in images) if c is not None]
    assert len(views) >= L.MIN_VIEWS
    with pytest.raises(ValueError, match="at least 8 views"):
        L.calibrate_checkerboard(views[:L.MIN_VIEWS - 1], (W, H))
    lens = L.calibrate_checkerboard(views, (W, H))
    assert lens.method == "checkerboard" and lens.views == len(views) and lens.error_px < 0.5
    assert lens.camera[0] == pytest.approx(520, rel=0.02) and lens.coefficients[0] == pytest.approx(-0.28, abs=0.03)
    assert lens.coefficients[4] == 0  # k3 is not fitted
    for horizontal in (True, False):
        bent = distort(lines_image(horizontal), K, D)
        assert max_bend(bent, horizontal) > 8
        assert max_bend(lens.apply(bent), horizontal) < 1.2
    # the views can also be found in a video of the board moved under the camera
    vp = tmp_path / "board.avi"
    wr = cv2.VideoWriter(str(vp), cv2.VideoWriter_fourcc(*"MJPG"), 10, (W, H))
    for im in images:
        for _ in range(3):
            wr.write(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR))
    wr.release()
    found, size, frames = L.checkerboard_views_in_video(str(vp), samples=36)
    assert size == (W, H) and len(found) >= L.MIN_VIEWS and len(set(frames)) == len(found)
    assert L.calibrate_checkerboard(found, size).error_px < 1.0


def test_stored_form_and_cache():
    lens = L.LensCorrection.barrel(40, alpha=1)
    d = lens.to_dict()
    assert d == {"method": "barrel", "strength": 40.0, "alpha": 1.0}
    assert L.LensCorrection.from_dict(json.loads(json.dumps(d))) == lens
    assert L.LensCorrection().to_dict() == {} and L.lens_from({}) is None and L.lens_from(None) is None
    for bad in ({"method": "fisheye"}, {"method": "checkerboard", "camera": [1, 2]}, {"method": "barrel",
                                                                                      "strength": "x"}, [1]):
        assert L.lens_from(bad) is None
    assert L.LensCorrection.barrel(500).strength == 100  # clipped
    assert L.lens_from({"method": "barrel", "strength": 0}) is None  # no correction
    m1 = L.undistort_maps(lens, 320, 240)[0]
    assert L.undistort_maps(L.LensCorrection.barrel(40, alpha=1), 320, 240)[0] is m1  # cached
    # a checkerboard calibration is scaled to other image sizes
    cb = L.LensCorrection.from_dict({"method": "checkerboard", "camera": [500, 500, 320, 240],
                                     "coefficients": [-0.2, 0, 0, 0, 0], "size": [640, 480], "views": 9})
    K, _ = cb.matrices(320, 240)
    assert K[0, 0] == 250 and K[0, 2] == 160
    assert "checkerboard calibration, 9 views" in cb.describe() and "barrel strength 40" in lens.describe()
    x, y = L.corrected_point(lens, 320, 240, 159.5, 119.5)
    assert abs(x - 159.5) < 0.5 and abs(y - 119.5) < 0.5  # the centre stays put


@pytest.fixture(scope="module")
def bent_video(tmp_path_factory):
    """A synthetic open-field video filmed through a barrel lens, and the same video corrected."""
    d = tmp_path_factory.mktemp("lens")
    pos = np.vstack([syn.line_path((110, 90), (530, 90), 60), syn.line_path((530, 90), (530, 390), 40),
                     syn.random_walk(40, (110, 90, 530, 390), speed=4, seed=5)])
    straight = d / "straight.avi"
    syn.make_video(straight, pos, size=(W, H), arena=("rect", 80, 60, 480, 360))
    lens = L.LensCorrection.barrel(70)
    K, D = lens.matrices(W, H)
    paths = {"bent": d / "bent.avi", "fixed": d / "fixed.avi"}
    writers = {k: cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 25, (W, H)) for k, p in paths.items()}
    with VideoSource(str(straight)) as v:
        for _ in range(v.frame_count):
            ok, f = v.read()
            bent = distort(f, K, D, fill=200)
            writers["bent"].write(bent)
            writers["fixed"].write(lens.apply(bent))
    for w in writers.values():
        w.release()
    return {k: str(p) for k, p in paths.items()}, lens


def test_tracking_reads_corrected_frames(bent_video):
    paths, lens = bent_video
    app = templates.build("open_field", 60, 40, 520, 400)
    [[ref]] = track_video(paths["fixed"], [ArenaJob(app, DetectionSettings())])
    [[tr]] = track_video(paths["bent"], [ArenaJob(app, DetectionSettings())], lens=lens)
    [[raw]] = track_video(paths["bent"], [ArenaJob(app, DetectionSettings())])
    assert tr.detected.mean() > 0.95 and tr.meta["lens_correction"] == lens.describe()
    err = np.hypot(tr.x - ref.x, tr.y - ref.y)
    assert np.nanmedian(err) < 1.0
    assert np.nanpercentile(np.hypot(raw.x - ref.x, raw.y - ref.y), 90) > 3  # without it they are bent
    bg = compute_background(paths["bent"], DetectionSettings(), lens)
    with VideoSource(paths["fixed"]) as v:
        assert bg.shape == (H, W) and np.abs(bg.astype(int) - cv2.cvtColor(v.frame_at(0), cv2.COLOR_BGR2GRAY)
                                             ).mean() < 6
    # every Nth frame: the motion reference is corrected too
    s = DetectionSettings(frame_step=3)
    [[a]] = track_video(paths["bent"], [ArenaJob(app, s)], lens=lens)
    [[b]] = track_video(paths["fixed"], [ArenaJob(app, s)])
    assert np.nanmedian(np.abs(a.motion - b.motion)) <= 0.1 * max(1.0, float(np.nanmedian(b.motion)))


def test_corrected_and_downscaled_tracking(bent_video):
    """Lens correction together with tracking downscaled frames: the (downscaled) frames are corrected with maps
    scaled to their size, the background is corrected at full size, and the track is in the corrected video's
    pixels."""
    paths, lens = bent_video
    app = templates.build("open_field", 60, 40, 520, 400)
    [[ref]] = track_video(paths["fixed"], [ArenaJob(app, DetectionSettings())])
    for factor in (2, 4):
        [[tr]] = track_video(paths["bent"], [ArenaJob(app, DetectionSettings(downscale=factor))], lens=lens)
        assert tr.meta["downscale"] == factor and tr.meta["lens_correction"] == lens.describe()
        assert tr.detected.mean() > 0.95 and len(tr) == len(ref)
        err = np.hypot(tr.x - ref.x, tr.y - ref.y)
        assert np.nanmedian(err) < 1.0 + factor / 2  # (a downscaled frame loses some precision)
        assert np.nanmedian(tr.area / ref.area) == pytest.approx(1.0, abs=0.15)
    [[raw]] = track_video(paths["bent"], [ArenaJob(app, DetectionSettings(downscale=2))])
    assert np.nanpercentile(np.hypot(raw.x - ref.x, raw.y - ref.y), 90) > 3  # without the correction: bent
    # preview frames are given at the video's size, corrected
    sizes = []
    track_video(paths["bent"], [ArenaJob(app, DetectionSettings(downscale=2, duration_s=0.2))], lens=lens,
                frame_callback=lambda i, f, d: sizes.append(f.shape[:2]))
    assert sizes and set(sizes) == {(H, W)}


def test_video_test_correction(bent_video, tmp_path):
    paths, lens = bent_video
    p = Project(name="Lens")
    p.path = tmp_path / "Lens.mmaze"
    p.apparatus.append(templates.build("open_field", 60, 40, 520, 400))
    t1 = p.add_test(paths["bent"], "A1", p.apparatus[0].name)
    t2 = p.add_test(paths["bent"], "A2", p.apparatus[0].name)
    assert p.lens_for(t1) is None and t1.undistort == {}
    t1.undistort = lens.to_dict()
    assert p.lens_for(t1) == lens and p.lens_for_video(paths["bent"]) == lens
    # tests with different corrections are not tracked in the same pass
    assert len(tracking_batches(p, [t1, t2])) == 2
    t2.undistort = lens.to_dict()
    assert len(tracking_batches(p, [t1, t2])) == 1
    [tr] = p.track_test(t1)
    assert tr.meta["lens_correction"] == lens.describe()
    with VideoSource(paths["fixed"]) as v:
        ref = v.frame_at(0)
    assert np.abs(p.start_frame(t1).astype(int) - ref).mean() < 3
    # saved with the test; an older project file (no "undistort") opens without a correction
    p.save()
    q = Project.load(p.path)
    assert q.lens_for(q.tests[0]) == lens
    d = json.loads((p.path / "project.json").read_text())
    for t in d["tests"]:
        t.pop("undistort")
    old = Project.from_dict(d, p.path)
    assert old.tests[0].undistort == {} and old.lens_for(old.tests[0]) is None


def test_video_export_is_corrected(bent_video, tmp_path):
    from manymaze.core.videoexport import OverlayOptions, export_video

    paths, lens = bent_video
    p = Project(name="Export")
    p.path = tmp_path / "Export.mmaze"
    p.apparatus.append(templates.build("open_field", 60, 40, 520, 400))
    t = p.add_test(paths["bent"], "A1", p.apparatus[0].name, undistort=lens.to_dict())
    p.track_test(t)
    out = export_video(p, t, tmp_path / "o.avi", OverlayOptions(t_end=0.2, trail_s=0, zones=False))
    with VideoSource(str(out)) as v, VideoSource(paths["fixed"]) as ref:
        a, b = v.frame_at(0), ref.frame_at(0)
    mask = np.ones(a.shape[:2], bool)
    mask[:60] = False  # the caption
    assert np.abs(a.astype(int) - b.astype(int))[mask].mean() < 8


def test_camera_correction(tmp_path, bent_video):
    paths, lens = bent_video
    spec = SourceSpec(paths["bent"])
    assert spec.open().__class__.__name__ == "VideoSource"  # nothing to do: the source itself
    spec.undistort = {spec.key: lens.to_dict()}
    src = spec.open()
    assert isinstance(src, TransformedSource) and (src.width, src.height) == (W, H)
    ok, f = src.read()
    with VideoSource(paths["fixed"]) as v:
        assert np.abs(f.astype(int) - v.frame_at(0).astype(int)).mean() < 3
    assert src.last_raw is not None and np.abs(src.last_raw.astype(int) - f.astype(int)).mean() > 3  # raw kept
    src.release()
    # saved with the camera options of each camera; a spec carries those of its cameras
    p = Project()
    set_camera_settings(p, f"file:{paths['bent']}", {"undistort": lens.to_dict()})
    assert camera_lenses(p, [paths["bent"], paths["fixed"]]) == {f"file:{paths['bent']}": lens.to_dict()}
    back = SourceSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
    assert back.lens(paths["bent"]) == lens and back.lens(paths["fixed"]) is None
    assert "undistort" not in SourceSpec(0).to_dict()
    assert SourceSpec.from_dict({"source": 0, "undistort": {"camera:0": {"method": "nope"}}}).undistort == {}
