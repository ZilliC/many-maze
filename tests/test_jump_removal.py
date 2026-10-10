"""Automatic jump removal: positions the animal could not have reached, which the track comes back from (a reflection
or another object detected for a moment), are removed before the gaps are filled; real fast runs are kept."""

import numpy as np
import pytest

from manymaze.core.project import Project
from manymaze.core.synthetic import line_path, make_video
from manymaze.core.track import Track
from manymaze.core.tracking import ArenaJob, DetectionSettings, postprocess, track_video

from test_zone_measures_extras import box_app

FPS = 25.0
SCALE = 0.1  # cm per pixel (10 px/cm)


def _walk(n=250) -> np.ndarray:
    """A slow walk around a circle (about 6 cm/s), in pixels."""
    a = np.linspace(0, 2 * np.pi, n)
    return np.column_stack([200 + 100 * np.cos(a), 200 + 100 * np.sin(a)])


def _track(pts) -> Track:
    pts = np.asarray(pts, float)
    t = np.arange(len(pts)) / FPS
    return Track(t=t, x=pts[:, 0].copy(), y=pts[:, 1].copy(), hx=pts[:, 0] + 5, hy=pts[:, 1].copy(),
                 tx=pts[:, 0] - 5, ty=pts[:, 1].copy(), area=np.full(len(pts), 120.0), fps=FPS)


JUMPS = {40: 1, 120: 3, 200: 2}  # first frame: length (frames)


def _jumpy() -> tuple[Track, np.ndarray]:
    pts = _walk()
    bad = np.zeros(len(pts), bool)
    for i, k in JUMPS.items():
        pts[i:i + k] = np.column_stack([np.full(k, 380.0), 20 + 30 * np.arange(k)])  # a reflection, moving a little
        bad[i:i + k] = True
    return _track(pts), bad


def test_injected_jumps_are_removed():
    tr, bad = _jumpy()
    assert tr.remove_jumps(100.0, 0.5, SCALE) == 3  # 100 cm/s
    assert np.array_equal(~tr.detected, bad) and np.isnan(tr.x[bad]).all() and np.isnan(tr.hx[bad]).all()
    assert np.isnan(tr.area[bad]).all() and np.isfinite(tr.x[~bad]).all()
    # nothing more to remove the second time; off when the limit is 0
    assert tr.remove_jumps(100.0, 0.5, SCALE) == 0
    tr2, _ = _jumpy()
    assert tr2.remove_jumps(0, 0.5, SCALE) == 0 and tr2.detected.all()
    # a jump lasting longer than the return time is kept (the animal may really be there)
    tr3, _ = _jumpy()
    assert tr3.remove_jumps(100.0, 0.08, SCALE) == 2  # the 3-frame jump does not come back within 0.08 s


def test_genuine_fast_runs_are_kept():
    # a sprint at 250 cm/s (faster than the limit), straight across the arena, then still
    sprint = np.vstack([np.tile((50.0, 200.0), (25, 1)), line_path((50, 200), (350, 200), 13)[1:],
                        np.tile((350.0, 200.0), (25, 1))])
    tr = _track(sprint)
    assert tr.remove_jumps(100.0, 0.5, SCALE) == 0 and tr.detected.all()
    # a short dash (3 frames at 250 cm/s) that stops where it ended
    dash = np.vstack([np.tile((100.0, 100.0), (10, 1)), line_path((100, 100), (400, 100), 4)[1:],
                      np.tile((400.0, 100.0), (20, 1))])
    assert _track(dash).remove_jumps(100.0, 0.5, SCALE) == 0
    # after a gap in the detection the animal may be far away: the speed counts the time missed
    gap = _walk(100)
    tr = _track(gap)
    tr.detected[30:60] = False
    tr.x[30:60] = tr.y[30:60] = np.nan
    assert tr.remove_jumps(100.0, 0.5, SCALE) == 0


def test_postprocess_removes_jumps_before_filling_gaps(tmp_path):
    tr, bad = _jumpy()
    s = DetectionSettings(max_jump_speed=100.0, max_jump_s=0.5, max_gap_s=1.0)
    out = postprocess(tr, s, SCALE)
    assert out.meta["jumps_removed"] == 3 and tr.detected.all()  # (the tracker's track is left as it was)
    assert np.isfinite(out.x).all() and not out.detected[bad].any()
    true = _walk()
    assert np.abs(out.x[bad] - true[bad, 0]).max() < 3 and np.abs(out.y[bad] - true[bad, 1]).max() < 3
    assert "jumps_removed" not in postprocess(_jumpy()[0], DetectionSettings(), SCALE).meta  # off by default
    # older settings have no jump removal; the count is kept with the track and shown as an information column
    assert DetectionSettings.from_dict({"max_gap_s": 2}).max_jump_speed == 0
    p = Project(name="J")
    p.path = tmp_path / "j.mmaze"
    p.apparatus.append(box_app())
    t = p.add_test(animal_id="A1")
    p.save_tracks(t, [out])
    back = p.load_tracks(t)[0]
    assert p.track_info(t, back)["Jumps removed"] == 3
    assert p.test_info(t)["Jumps removed"] == "" and p.analyse_test(t)[0]["Jumps removed"] == 3


def test_tracking_a_video_with_jumps(tmp_path):
    path = line_path((80, 200), (320, 200), 60)  # 100 px/s
    shown = path.copy()
    for i in (20, 40):
        shown[i] = (200, 340)  # the "animal" (a reflection) seen far away for one frame
    video = tmp_path / "jumps.mp4"
    make_video(video, shown, fps=FPS)
    s = DetectionSettings(max_jump_speed=1000.0, max_jump_s=0.3, max_gap_s=1.0)  # px/s: no apparatus
    tr = track_video(str(video), [ArenaJob(None, s)])[0][0]
    assert tr.meta["jumps_removed"] == 2
    for i in (20, 40):
        assert not tr.detected[i] and abs(tr.y[i] - 200) < 15 and abs(tr.x[i] - path[i, 0]) < 15
    plain = track_video(str(video), [ArenaJob(None, DetectionSettings())])[0][0]
    assert plain.y[20] == pytest.approx(340, abs=15)  # without jump removal the reflection is in the track
