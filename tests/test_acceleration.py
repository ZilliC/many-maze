"""Hardware-oriented paths: fast decoding, hardware encoding, parallel batch tracking, pose-model tracking."""

import math

import cv2
import numpy as np
import pytest

from manymaze.core import pose
from manymaze.core.batch import default_workers, track_tests, tracking_batches
from manymaze.core.demo import create_demo_project
from manymaze.core.synthetic import line_path, make_video
from manymaze.core.tracking import ArenaJob, ArenaTracker, DetectionSettings, track_video
from manymaze.core.video import FrameReader, VideoRecorder, VideoSource, hw_decoder_name
from test_pose import _tiny_meta, _tiny_state_dict, _write_torch_zip


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = tmp_path_factory.mktemp("v") / "clip.avi"
    make_video(p, line_path((80, 200), (320, 200), 60), fps=25)
    return str(p)


@pytest.fixture(scope="module")
def mp4(tmp_path_factory):
    av = pytest.importorskip("av")
    if "libx264" not in av.codecs_available:
        pytest.skip("no H.264 encoder in this FFmpeg build")
    p = tmp_path_factory.mktemp("v") / "clip.mp4"
    out = av.open(str(p), "w")
    st = out.add_stream("libx264", rate=25)
    st.width, st.height, st.pix_fmt = 320, 240, "yuv420p"
    for i in range(50):
        f = np.full((240, 320, 3), 190, np.uint8)
        cv2.circle(f, (40 + 4 * i, 120), 15, (30, 30, 30), -1)
        for pkt in st.encode(av.VideoFrame.from_ndarray(f, format="bgr24")):
            out.mux(pkt)
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    return str(p)


@pytest.mark.parametrize("start", [0, 7, 30])
def test_frame_reader_matches_opencv(mp4, start):
    with FrameReader(mp4, start=start) as r, VideoSource(mp4) as v:
        assert r.backend.startswith("pyav")
        frames = list(r)
        assert [i for i, _ in frames] == list(range(start, 50))
        for i, g in frames[:: 10]:
            ref = cv2.cvtColor(v.frame_at(i), cv2.COLOR_BGR2GRAY)
            assert g.shape == ref.shape and g.dtype == np.uint8
            assert np.abs(g.astype(int) - ref).mean() < 2.5
    with FrameReader(mp4, start=start, gray=False) as r:
        i, f = next(iter(r))
        assert i == start and f.shape == (240, 320, 3)


@pytest.mark.parametrize("start", [0, 7])
def test_frame_reader_falls_back_when_hardware_decoding_fails(mp4, start, monkeypatch):
    # VideoToolbox opens some streams (e.g. old H.264 High 4:4:4) and then fails on the first packet
    if hw_decoder_name() is None:
        pytest.skip("no hardware decoder")
    real = FrameReader._decode

    def failing(self):
        if self.backend != "pyav":
            raise RuntimeError("avcodec_send_packet()")
        yield from real(self)
    monkeypatch.setattr(FrameReader, "_decode", failing)
    with FrameReader(mp4, start=start) as r:
        assert [i for i, _ in r] == list(range(start, 50))
        assert r.backend == "pyav"


def test_frame_reader_opencv_fallback(clip, monkeypatch):
    monkeypatch.setenv("MANYMAZE_DECODER", "opencv")
    with FrameReader(clip, start=5) as r:
        assert r.backend == "opencv"
        idx = [i for i, _ in r]
    assert idx == list(range(5, 60))


def test_tracking_same_with_both_decoders(clip, monkeypatch):
    s = DetectionSettings(contrast="dark")
    a = track_video(clip, [ArenaJob(None, s)])[0][0]
    monkeypatch.setenv("MANYMAZE_DECODER", "opencv")
    b = track_video(clip, [ArenaJob(None, s)])[0][0]
    assert len(a) == len(b) == 60 and b.meta["decoder"] == "opencv"
    assert np.nanmax(np.hypot(a.x - b.x, a.y - b.y)) < 1.0


def test_recorder_roundtrip(tmp_path):
    p = tmp_path / "rec.mp4"
    rec = VideoRecorder(str(p), 25, (160, 120))
    for i in range(30):
        f = np.full((120, 160, 3), 200, np.uint8)
        cv2.rectangle(f, (10 + 3 * i, 40), (30 + 3 * i, 60), (0, 0, 0), -1)
        rec.write(f)
    backend = rec.backend
    rec.close()
    with VideoSource(str(p)) as v:
        assert v.frame_count >= 29 and (v.width, v.height) == (160, 120)
        g = cv2.cvtColor(v.frame_at(10), cv2.COLOR_BGR2GRAY)
        assert g[50, 50] < 100 and g[50, 150] > 150
    print("recorder backend:", backend)


# ---------------------------------------------------------------- parallel batch tracking
@pytest.fixture
def project(tmp_path):
    return create_demo_project(tmp_path / "p.mmaze", n_per_group=1, seconds=4, track=False)


def test_parallel_equals_serial(project):
    p = project
    assert len(tracking_batches(p, p.tests)) == 2
    r1 = track_tests(p, p.tests, workers=1)
    serial = [p.load_tracks(t)[0] for t in p.tests]
    for t in p.tests:
        t.status = "pending"
    fr = []
    r2 = track_tests(p, p.tests, progress=fr.append, workers=2)
    assert r1["workers"] == 1 and r2["workers"] == 2
    assert sorted(r2["tracked"]) == [t.id for t in p.tests] and not r2["errors"] and not r2["cancelled"]
    assert all(t.status == "tracked" for t in p.tests)
    assert fr[-1] == 1.0 and all(0 <= f <= 1 for f in fr)
    for t, a in zip(p.tests, serial):
        b = p.load_tracks(t)[0]
        np.testing.assert_allclose(a.x, b.x)


def test_parallel_cancel_saves_nothing_partial(project):
    p = project
    r = track_tests(p, p.tests, workers=2, should_stop=lambda: True)
    assert r["cancelled"]
    for t in p.tests:
        assert (t.id in r["tracked"]) == p.has_track(t) == (t.status == "tracked")


def test_default_workers(monkeypatch):
    assert default_workers(1) == 1
    assert 1 <= default_workers(50) <= 50
    assert default_workers(50, pose=True) <= 2
    monkeypatch.setenv("MANYMAZE_WORKERS", "3")
    assert default_workers(10) == 3


# ---------------------------------------------------------------- pose model in the tracker
class StubPose:
    """Keypoints 10 px ahead / behind the box centre along x."""

    provider = "stub"

    def predict(self, frame, boxes):
        out = []
        for x0, y0, x1, y1 in boxes:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            out.append(np.array([[cx + 10, cy, 0.9], [cx, cy, 0.9], [cx - 10, cy, 0.9]], np.float32))
        return out

    def body_parts(self, k):
        return {"nose": tuple(k[0]), "centre": tuple(k[1]), "tail_base": tuple(k[2])}


def test_tracker_uses_pose_and_recovers_lost_animal():
    s = DetectionSettings(contrast="dark", body_parts="pose", method="background")
    bg = np.full((200, 300), 200, np.uint8)
    tr = ArenaTracker(s, None, pose=StubPose())
    tr.set_background(bg)
    f = np.full((200, 300, 3), 200, np.uint8)
    cv2.ellipse(f, (150, 100), (25, 10), 0, 0, 360, (20, 20, 20), -1)
    [d], _ = tr.process(f)
    assert d.detected and d.keypoints is not None
    assert (d.hx, d.hy) == pytest.approx((160, 100), abs=1.5) and (d.tx, d.ty) == pytest.approx((140, 100), abs=1.5)
    assert d.angle == pytest.approx(0, abs=5)
    # the blob disappears (e.g. poor contrast): the pose model keeps the animal tracked from the last box
    [d2], _ = tr.process(np.full((200, 300, 3), 200, np.uint8))
    assert d2.detected and (d2.x, d2.y) == pytest.approx((150, 100), abs=1.5)


def test_pose_unavailable_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path / "none"))
    s = DetectionSettings(body_parts="pose")
    tr = ArenaTracker(s, None)
    assert tr.pose is None and "not installed" in tr.pose_error
    with pytest.raises(RuntimeError, match="not installed"):
        track_video("missing.avi", [ArenaJob(None, s)])


def test_track_video_with_real_pose_session(clip, tmp_path, monkeypatch):
    """End to end with a tiny random-weights RTMPose converted to ONNX and run by ONNX Runtime."""
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path / "models"))
    ck = tmp_path / "tiny.pt"
    _write_torch_zip(ck, {"model": _tiny_state_dict()})
    spec = dict(pose.MODELS["topviewmouse_rtmpose_s"])
    spec.update(_tiny_meta(), url=ck.as_uri(), checkpoint="tiny.pt", sha256=None, onnx="tiny.onnx",
                meta="tiny.json", size_bytes=0)
    monkeypatch.setitem(pose.MODELS, "tiny", spec)
    pose.install_model("tiny", source_path=str(ck))
    s = DetectionSettings(contrast="dark", body_parts="pose", pose_model="tiny", pose_device="cpu",
                          pose_min_conf=0.0)
    tr = track_video(clip, [ArenaJob(None, s)])[0][0]
    assert len(tr) == 60 and tr.detected.all()
    assert tr.meta["pose_model"] == "tiny" and tr.meta["pose_device"] == "CPUExecutionProvider"
    assert np.isfinite(tr.hx).all() and (tr.hx >= 0).all() and (tr.hx < 400).all()
    assert not math.isnan(float(np.nanmean(tr.angle)))


def test_recorder_closed_without_frames(tmp_path):
    VideoRecorder(str(tmp_path / "probe.mp4"), 25, (64, 48)).close()
