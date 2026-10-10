"""Batch tracking: many tests at once, one worker process per video so every CPU core is used.

Tests that share a video and time window (several apparatus filmed together) are tracked in a single pass by one
worker.  Workers are separate processes (``spawn``), each limited to a share of the cores for decoding / OpenCV /
pose inference so the machine is not oversubscribed.  Cancelling never saves partial tracks.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable

WORKER_RAM_MB = 700  # rough peak per worker for HD video (background model + decoder + pose session)


class TrackingCancelled(Exception):
    pass


def _stopper(should_stop):
    """Wrap should_stop so that cancelling aborts tracking before partial tracks are saved."""

    def check():
        if should_stop and should_stop():
            raise TrackingCancelled()
        return False

    return check


def tracking_batches(project, tests) -> list[list]:
    """Group tests that share a video, time window and lens correction so they are tracked in one pass."""
    batches: dict[tuple, list] = {}
    order = []
    for t in tests:
        if not t.video:
            continue
        s = project.detection_for(t)
        if project.start_mode in ("on_detection", "experimenter_leaves"):
            key = ("single", t.id)
        else:
            lens = project.lens_for(t)  # tests tracked together share one corrected image
            key = (project.abs_path(t.video), round(s.start_time_s, 4), round(s.duration_s, 4), s.frame_step,
                   lens.key() if lens is not None else None)
        if key not in batches:
            batches[key] = []
            order.append(key)
        batches[key].append(t)
    return [batches[k] for k in order]


def _total_ram_mb() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**20)
    except (ValueError, OSError, AttributeError):
        return 8192


def default_workers(n_batches: int, pose: bool = False) -> int:
    """Worker processes for n videos: all cores but one, limited by RAM (and to 2 when the Neural Engine /
    GPU is shared by pose inference)."""
    env = os.environ.get("MANYMAZE_WORKERS", "").strip()
    if env:
        try:
            n = int(env)
        except ValueError:
            raise ValueError(f"MANYMAZE_WORKERS must be a whole number of tracking processes (e.g. 4), not "
                             f"{env!r}: correct it or remove it from the environment") from None
        return max(1, min(n_batches, n))
    cores = os.cpu_count() or 1
    n = min(n_batches, max(1, cores - 1), max(1, _total_ram_mb() // WORKER_RAM_MB - 1))
    return max(1, min(n, 2) if pose else n)


def _track_batch(project, batch, progress, should_stop):
    if len(batch) == 1:
        project.track_test(batch[0], progress, should_stop)
    else:
        project.track_video_tests(batch, progress, should_stop)


# ---------------------------------------------------------------- worker process side
_W: dict = {}


def _init_worker(progress_q, stop_event, threads: int):
    os.environ["MANYMAZE_THREADS"] = str(threads)
    import cv2
    cv2.setNumThreads(threads)
    _W.update(q=progress_q, stop=stop_event)


def _run_batch(project_dict: dict, project_path: str, test_ids: list[int], bi: int) -> dict:
    from .project import Project

    p = Project.from_dict(project_dict, project_path)
    batch = [p.get_test(i) for i in test_ids]
    q, stop = _W["q"], _W["stop"]
    last = [0.0]

    def prog(f):
        now = time.monotonic()
        if now - last[0] > 0.2 or f >= 1.0:
            last[0] = now
            q.put((bi, float(f)))

    try:
        _track_batch(p, batch, prog, _stopper(stop.is_set))
    except TrackingCancelled:
        return {"cancelled": True}
    return {"tracked": test_ids}


# ---------------------------------------------------------------- public API
def track_tests(project, tests, progress: Callable[[float], None] | None = None,
                should_stop: Callable[[], bool] | None = None, workers: int = 0) -> dict:
    """Track tests (in parallel when there are several videos).

    Returns {"tracked": [test ids], "cancelled": bool, "errors": [str], "workers": n}.  Track files are written
    by the workers; the statuses of ``project``'s tests are updated here.
    """
    progress = progress or (lambda f: None)
    batches = tracking_batches(project, tests)
    out = {"tracked": [], "cancelled": False, "errors": [], "workers": 1}
    if not batches:
        progress(1.0)
        return out
    pose = any(project.detection_for(t).body_parts == "pose" for b in batches for t in b)
    n_workers = workers or default_workers(len(batches), pose)
    n_workers = max(1, min(n_workers, len(batches)))
    out["workers"] = n_workers
    if n_workers == 1 or project.path is None:
        return _track_serial(project, batches, progress, should_stop, out)
    return _track_parallel(project, batches, progress, should_stop, out, n_workers)


def _err(batch, e) -> str:
    return f"Test {', '.join(str(t.id) for t in batch)}: {type(e).__name__}: {e}"


def _track_serial(project, batches, progress, should_stop, out):
    stop = _stopper(should_stop)
    n = len(batches)
    for bi, batch in enumerate(batches):
        try:
            _track_batch(project, batch, lambda f, bi=bi: progress((bi + min(1.0, f)) / n), stop)
            out["tracked"].extend(t.id for t in batch)
        except TrackingCancelled:
            out["cancelled"] = True
            break
        except Exception as e:  # keep going with the other videos
            out["errors"].append(_err(batch, e))
    progress(1.0)
    return out


def _track_parallel(project, batches, progress, should_stop, out, n_workers):
    ctx = mp.get_context("spawn")
    q, stop = ctx.Queue(), ctx.Event()
    threads = max(1, (os.cpu_count() or 1) // n_workers)
    state = project.to_dict()
    frac = [0.0] * len(batches)
    with ProcessPoolExecutor(n_workers, mp_context=ctx, initializer=_init_worker,
                             initargs=(q, stop, threads)) as pool:
        futs = {pool.submit(_run_batch, state, str(project.path), [t.id for t in b], bi): bi
                for bi, b in enumerate(batches)}
        pending = set(futs)
        while pending:
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for f in done:
                bi = futs[f]
                frac[bi] = 1.0
                if f.cancelled():
                    continue
                try:
                    r = f.result()
                except Exception as e:
                    out["errors"].append(_err(batches[bi], e))
                    continue
                if r.get("cancelled"):
                    out["cancelled"] = True
                for tid in r.get("tracked", []):
                    t = project.get_test(tid)
                    t.status = "tracked"
                    if project.current_user and not t.experimenter:  # as Project.save_tracks does
                        t.experimenter = project.current_user
                    out["tracked"].append(tid)
            try:
                while True:
                    bi, f = q.get_nowait()
                    frac[bi] = max(frac[bi], min(1.0, f))
            except queue.Empty:
                pass
            progress(sum(frac) / len(frac))
            if should_stop and should_stop() and not stop.is_set():
                stop.set()
                out["cancelled"] = True
                for f in pending:
                    f.cancel()
    progress(1.0)
    return out
