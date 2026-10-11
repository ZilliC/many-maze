"""Benchmark mANY-MAZE's hardware paths on this machine and print a Markdown report.

    python scripts/benchmark.py [--video real.mp4] [--checkpoint superanimal_topviewmouse_rtmpose_s.pt]
                                [--download] [--workdir DIR]

* decoding: OpenCV vs PyAV software vs PyAV + VideoToolbox (1080p H.264)
* recording: hardware (VideoToolbox) vs OpenCV encoder
* tracking: contour vs pose model (Core ML vs CPU), Core ML node coverage and CoreML-vs-CPU agreement
* batch: serial vs parallel worker processes
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manymaze.core import pose  # noqa: E402
from manymaze.core.batch import track_tests  # noqa: E402
from manymaze.core.demo import create_demo_project  # noqa: E402
from manymaze.core.tracking import ArenaJob, DetectionSettings, _POSE_CACHE, track_video  # noqa: E402
from manymaze.core.video import FrameReader, VideoRecorder, hw_decoder_name  # noqa: E402

ROWS: list[tuple[str, str]] = []


def row(name, value):
    ROWS.append((name, value))
    print(f"  {name}: {value}", flush=True)


def make_hd(path: Path, n=300):
    import av
    out = av.open(str(path), "w")
    st = out.add_stream("libx264", rate=30)
    st.width, st.height, st.pix_fmt = 1920, 1080, "yuv420p"
    bg = np.random.default_rng(0).integers(150, 200, (1080, 1920, 3)).astype(np.uint8)
    for i in range(n):
        f = bg.copy()
        cv2.circle(f, (200 + 5 * i, 540), 40, (20, 20, 20), -1)
        for p in st.encode(av.VideoFrame.from_ndarray(f, format="bgr24")):
            out.mux(p)
    for p in st.encode():
        out.mux(p)
    out.close()


def fps_of(fn) -> float:
    t = time.perf_counter()
    n = fn()
    return n / (time.perf_counter() - t)


def bench_decode(hd: Path):
    def ocv():
        cap, n = cv2.VideoCapture(str(hd)), 0
        while True:
            ok, f = cap.read()
            if not ok:
                return n
            cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            n += 1

    def reader(hw):
        def run():
            with FrameReader(str(hd), hwaccel=hw) as r:
                run.backend = r.backend
                return sum(1 for _ in r)
        return run

    row("1080p decode, OpenCV (BGR → grey)", f"{fps_of(ocv):.0f} fps")
    sw = reader(False)
    v = fps_of(sw)
    row(f"1080p decode, FrameReader without hardware ({sw.backend}, luma)", f"{v:.0f} fps")
    hw = reader(True)
    v = fps_of(hw)
    row(f"1080p decode, FrameReader default ({hw.backend})", f"{v:.0f} fps")


def bench_record(work: Path):
    frame = np.random.default_rng(1).integers(0, 255, (1080, 1920, 3)).astype(np.uint8)
    n = 90
    for name, force_cv in (("default", False), ("OpenCV", True)):
        p = work / f"rec_{name}.mp4"
        if force_cv:
            os.environ["MANYMAZE_HWACCEL"] = "0"
        try:
            t = time.perf_counter()
            rec = VideoRecorder(str(p), 30, (1920, 1080))
            for i in range(n):
                rec.write(np.roll(frame, 8 * i, axis=1))
            rec.close()
            row(f"1080p recording, {name} ({rec.backend})", f"{n / (time.perf_counter() - t):.0f} fps")
        finally:
            os.environ.pop("MANYMAZE_HWACCEL", None)


def coreml_coverage(onnx_path: str) -> str:
    code = ("import onnxruntime as ort; so=ort.SessionOptions(); so.log_severity_level=0;"
            "so.add_free_dimension_override_by_name('batch',1);"
            f"ort.InferenceSession({onnx_path!r}, so, providers=[('CoreMLExecutionProvider',"
            "{'ModelFormat':'MLProgram','MLComputeUnits':'ALL'}),'CPUExecutionProvider'])")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    log = r.stdout + r.stderr
    m = re.findall(r"number of nodes in the graph: (\d+) number of nodes supported by CoreML: (\d+)", log)
    if m:
        total, sup = m[-1]
        parts = re.findall(r"number of partitions supported by CoreML: (\d+)", log)
        return f"{sup}/{total} nodes on Core ML" + (f" in {parts[-1]} partition(s)" if parts else "")
    return "unknown (no CoreML log line)" + (f"; error: {log[-300:]}" if r.returncode else "")


def bench_pose(video: str, key: str):
    est_cpu = pose.PoseEstimator(key, device="cpu")
    est = pose.PoseEstimator(key, device="auto")
    row("pose: inference provider (auto)", est.provider)
    if est.provider == "CoreMLExecutionProvider":
        row("pose: Core ML graph coverage", coreml_coverage(str(est.path)))
    with FrameReader(video, gray=False) as r:
        frames = [f for i, f in r if i % 15 == 0][:20]
    s = DetectionSettings(contrast="dark")
    from manymaze.core.tracking import ArenaTracker, compute_background
    trk = ArenaTracker(s)
    trk.set_background(compute_background(video, s))
    pairs = []
    for f in frames:
        [d], _ = trk.process(f)
        if d.contour is not None:
            x, y, w, h = cv2.boundingRect(d.contour)
            pairs.append((f, (x, y, x + w, y + h)))
    frames, boxes = [p[0] for p in pairs], [p[1] for p in pairs]
    for e in (est_cpu, est) if est.provider != est_cpu.provider else (est_cpu,):
        e.predict(frames[0], [boxes[0]])  # warm-up / compile
        t = time.perf_counter()
        for f, b in zip(frames, boxes):
            e.predict(f, [b])
        row(f"pose: latency per crop ({e.provider})", f"{(time.perf_counter() - t) / len(frames) * 1000:.1f} ms")
    if est.provider != "CPUExecutionProvider":
        diffs = []
        for f, b in zip(frames, boxes):
            a, c = est_cpu.predict(f, [b])[0], est.predict(f, [b])[0]
            ok = (a[:, 2] > 0.5) & (c[:, 2] > 0.5)
            diffs.append(np.hypot(*(a[ok, :2] - c[ok, :2]).T))
        dd = np.concatenate(diffs)
        row("pose: Core ML vs CPU keypoints (confident)", f"median {np.median(dd):.2f} px, max {dd.max():.2f} px")
    for dev in ("auto", "cpu"):
        _POSE_CACHE.clear()
        sp = DetectionSettings(contrast="dark", body_parts="pose", pose_model=key, pose_device=dev, duration_s=10)
        t = time.perf_counter()
        tr = track_video(video, [ArenaJob(None, sp)])[0][0]
        row(f"tracking with pose model, device={dev} ({tr.meta.get('pose_device')})",
            f"{len(tr) / (time.perf_counter() - t):.0f} fps, detected {tr.detected.mean() * 100:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="real top-view mouse video (e.g. DeepLabCut's m3v1mp4.mp4)")
    ap.add_argument("--checkpoint", help="superanimal_topviewmouse_rtmpose_s.pt (else --download)")
    ap.add_argument("--download", action="store_true", help="download the pose model from Hugging Face")
    ap.add_argument("--workdir")
    a = ap.parse_args()
    work = Path(a.workdir or tempfile.mkdtemp(prefix="mm-bench-"))
    work.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MANYMAZE_MODELS", str(work / "models"))
    print(f"# mANY-MAZE benchmark — {platform.platform()} ({platform.machine()}), {os.cpu_count()} cores\n")
    row("hardware decoder", hw_decoder_name() or "none")
    row("ONNX Runtime providers", ", ".join(pose.available_providers()))

    hd = work / "hd.mp4"
    make_hd(hd)
    bench_decode(hd)
    bench_record(work)

    if a.video:
        s = DetectionSettings(contrast="dark")
        t = time.perf_counter()
        tr = track_video(a.video, [ArenaJob(None, s)])[0][0]
        row(f"tracking, animal shape ({tr.meta['decoder']})", f"{len(tr) / (time.perf_counter() - t):.0f} fps")
        key = "topviewmouse_rtmpose_s"
        if a.checkpoint or a.download:
            if not pose.is_installed(key):
                pose.install_model(key, source_path=a.checkpoint)
            bench_pose(a.video, key)

    d = work / "batch.mmaze"
    shutil.rmtree(d, ignore_errors=True)
    p = create_demo_project(d, n_per_group=4, seconds=30, track=False)
    times = {}
    for w in (1, 0):
        for t in p.tests:
            t.status = "pending"
        t0 = time.perf_counter()
        r = track_tests(p, p.tests, workers=w)
        times[r["workers"]] = time.perf_counter() - t0
        assert len(r["tracked"]) == len(p.tests), r
    (_, t1), (wn, tn) = sorted(times.items())[0], sorted(times.items())[-1]
    row(f"batch of {len(p.tests)} videos", f"{t1:.1f} s serial → {tn:.1f} s with {wn} workers "
                                          f"({t1 / tn:.1f}× faster)")

    print("\n| Benchmark | Result |\n| --- | --- |")
    for k, v in ROWS:
        print(f"| {k} | {v} |")


if __name__ == "__main__":
    main()
