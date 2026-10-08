"""Crash recovery of live tests: while a test runs, its track, events and I/O log are rewritten to a side file in
the recordings folder; tests interrupted by a crash are rebuilt from these files when the experiment opens."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .session import Session, save_live_test
from .tracking import DetectionSettings, TrackBuilder, postprocess
from .video import recorded_video

SUFFIX = ".autosave.json"


def path_for(project, test) -> str:
    """The crash-recovery side file of a live test (in the recordings folder)."""
    safe = re.sub(r"[^\w.-]+", "_", test.animal_id or "animal")
    return str(project.recordings_dir() / f"test_{test.id:04d}_{safe}{SUFFIX}")


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def write(path: str, data: dict):
    """Atomically (write + rename) write a side file."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, default=_json_default)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # pragma: no cover
            pass
    os.replace(tmp, p)


def read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Autosaver:
    """Writes a session's side file in a background thread, so the frame thread never waits for the disk.

    :meth:`request` asks for a write of ``snapshot()`` (taken in the writer thread); requests made while a write
    is under way are merged into one."""

    def __init__(self, path: str, snapshot: Callable[[], dict], on_error: Callable[[str], None]):
        self.path, self.snapshot, self.on_error = path, snapshot, on_error
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False
        self._thread: threading.Thread | None = None

    def request(self):
        with self._lock:
            if self._closed:
                return
            self._pending = True
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="live-autosave", daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._pending or self._closed:
                    self._thread = None
                    return
                self._pending = False
            try:
                write(self.path, self.snapshot())
            except Exception as e:
                self.on_error(f"Autosave failed: {e}")

    def _join(self, timeout: float):
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout)

    def flush(self, timeout: float = 10.0):
        """Write the side file now and wait until it is on disk."""
        self.request()
        self._join(timeout)

    def remove(self):
        """The test was saved or discarded: stop writing and delete the side file."""
        with self._lock:
            self._closed = True
            self._pending = False
        self._join(10.0)
        try:
            Path(self.path).unlink(missing_ok=True)
        except OSError:
            pass


class RecoveredSession(Session):
    """A live session rebuilt from its side file (enough for :func:`session.save_live_test`)."""

    def __init__(self, d: dict):
        self.d = d
        self.apparatus = None
        self.fps = float(d.get("fps") or 25.0)
        self.duration_s = float(d.get("duration_s") or 0.0)
        self.settings = DetectionSettings.from_dict(d.get("settings"))
        self._track = TrackBuilder(d.get("cols"))
        self.events = [dict(e) for e in d.get("events") or []]
        t_end = round(self.elapsed, 3)
        open_states = set(d.get("open_states") or [])
        for e in self.events:  # state events still open at the crash end at the last autosave
            if e.get("t_end") is None and e.get("behaviour") in open_states:
                e["t_end"] = t_end
        self.pauses = [list(p) for p in d.get("pauses") or []]
        self.pause_log = [dict(p) for p in d.get("pause_log") or []]

    @property
    def elapsed(self) -> float:
        t = self._track.cols["t"]
        return float(t[-1]) if t else 0.0

    @property
    def io_events(self) -> list:
        return [dict(e) for e in self.d.get("io_events") or []]

    @property
    def result_variables(self) -> dict:
        return dict(self.d.get("result_variables") or {})

    def track(self):
        tr = self._track.build(self.fps)
        tr.meta["source"] = "live"
        tr.meta["recovered"] = True
        return postprocess(tr, self.settings)


def recover(project) -> list:
    """Tests interrupted by a crash: rebuild them from the side files left in the recordings folder.  Each
    recovered test gets the track, events, pauses and I/O log written up to the last autosave (and its recording,
    playable up to the last fragment).  The project is saved, then the side files are deleted.  Returns the
    recovered tests."""
    if project is None or project.path is None:
        return []
    folder = Path(project.path) / "recordings"
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob(f"*{SUFFIX}")):
        try:
            d = read(str(f))
        except Exception:
            continue
        s = RecoveredSession(d)
        if not len(s._track):
            f.unlink(missing_ok=True)
            continue
        m = d.get("meta") or {}
        test = project.get_test(m["test_id"]) if m.get("test_id") is not None else None
        if test is not None and (test.recorded_at or "") >= str(d.get("saved_at") or "~"):
            f.unlink(missing_ok=True)  # stale: the test was saved after this side file was written
            continue
        if test is None:
            animal = str(m.get("animal") or "")
            if animal and project.get_animal(animal) is None:
                project.ensure_animal(animal)
            test = project.add_test("", animal, str(m.get("apparatus") or ""), stage=str(m.get("stage") or ""),
                                    trial=int(m.get("trial") or 1))
        rec = d.get("record_path")
        try:
            ok = save_live_test(project, test, s, recorded_video(rec))
        except Exception:
            ok = False
        if not ok:
            continue
        test.notes = (test.notes + "\n" + f"Recovered after the live test was interrupted (data up to "
                      f"{s.elapsed:.1f} s, saved {d.get('saved_at', '')}).").strip()
        out.append((test, f))
    if out:
        project.save()  # the side files go only once their data is in the saved experiment
        for _, f in out:
            f.unlink(missing_ok=True)
    return [t for t, _ in out]
