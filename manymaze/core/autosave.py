"""Crash recovery of live tests: while a test runs, its track, events and I/O log are rewritten to a side file in
the recordings folder; tests interrupted by a crash are rebuilt from these files when the experiment opens. The
side files of an experiment protected by a password are encrypted with its key (see :mod:`.security`)."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import numpy as np

from . import explock
from .atomicfile import atomic_write
from .iolog import load_samples, merge_events
from .session import END_RECOVERED, Session, save_live_test
from .tracking import DetectionSettings, TrackBuilder, postprocess
from .video import recorded_video

SUFFIX = ".autosave.json"
# a side file written by another computer is taken as that computer's running test until it is this old (live
# tests rewrite it every few seconds)
OTHER_HOST_STALE_S = 900.0


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


def write(path: str, data: dict, key=None):
    """Atomically (write + fsync + rename) write a side file, stamped with the program writing it ("owner": host and
    process) so that another program opening the experiment does not take a running test for a crashed one. With
    ``key`` (the experiment's security.ExperimentKey) the file is encrypted."""
    owner = explock.me()
    data = {**data, "owner": {"host": owner["host"], "pid": owner["pid"]}}
    with atomic_write(path) as fh:
        if key is None:
            json.dump(data, fh, default=_json_default)
        else:
            fh.write(key.encrypt(json.dumps(data, default=_json_default)))


def owner_running(d: dict, path=None) -> bool:
    """The program that wrote a side file is still running (so its test is not interrupted): a process of this
    computer that is alive (this one excepted), or another computer that rewrote the file recently."""
    owner = d.get("owner")
    if not isinstance(owner, dict) or explock.is_mine(owner):
        return False
    if owner.get("host") != explock.me()["host"]:
        try:
            age = time.time() - Path(path).stat().st_mtime if path is not None else 0.0
        except OSError:
            age = 0.0
        return age < OTHER_HOST_STALE_S
    return explock.pid_alive(owner.get("pid"))


def read(path: str, password: str | None = None) -> dict:
    """A side file (decrypted with the experiment password when it is encrypted: security.PasswordRequired
    without it)."""
    from .security import loads

    with open(path, encoding="utf-8") as fh:
        return loads(fh.read(), password, f"The crash-recovery file {Path(path).name}")[0]


class Autosaver:
    """Writes a session's side file in a background thread, so the frame thread never waits for the disk.

    :meth:`request` asks for a write of ``snapshot()`` (taken in the writer thread); requests made while a write
    is under way are merged into one. The writes back off as the file grows: after a write that took d seconds,
    requests are ignored for BACKOFF × d seconds (at most MAX_INTERVAL_S), so that a 24-hour test does not spend
    its time rewriting a file of hundreds of MB; ``force`` (the end of the test, flush) always writes."""

    BACKOFF = 10.0  # at most about a tenth of the time spent writing
    MAX_INTERVAL_S = 300.0

    def __init__(self, path: str, snapshot: Callable[[], dict], on_error: Callable[[str], None], key=None):
        self.path, self.snapshot, self.on_error = path, snapshot, on_error
        self.key = key  # the experiment's security.ExperimentKey: the side file is encrypted (protected experiment)
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._next_ok = 0.0  # monotonic time before which unforced requests are ignored
        self.last_write_s = 0.0

    def request(self, force: bool = False):
        with self._lock:
            if self._closed:
                return
            if not force and time.monotonic() < self._next_ok:
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
            t0 = time.monotonic()
            try:
                write(self.path, self.snapshot(), self.key)
            except Exception as e:
                self.on_error(f"Autosave failed: {e}")
            self.last_write_s = time.monotonic() - t0
            with self._lock:
                self._next_ok = time.monotonic() + min(self.MAX_INTERVAL_S, self.BACKOFF * self.last_write_s)

    def _join(self, timeout: float):
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout)

    def flush(self, timeout: float = 10.0):
        """Write the side file now and wait until it is on disk."""
        self.request(force=True)
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
    """A live session rebuilt from its side file (enough for :func:`session.save_live_test`): the track (none for an
    I/O-only test), events, pauses, I/O log (with the fast samples of their own side file), zones moved by the
    procedures, zone labels, scheduled tests, weights, kept variables, video labels, the recording log and its
    files."""

    def __init__(self, d: dict):
        self.d = d
        self.io_only = bool(d.get("io_only"))  # an I/O-only test (live.IOSession): no track, its own clock
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
        self.end_reason = str(d.get("end_reason") or "") or END_RECOVERED
        self.calibration = dict(d["calibration"]) if isinstance(d.get("calibration"), dict) else None
        self.calibration_log = [(float(t), dict(c)) for t, c in d.get("calibration_log") or []]
        self.geometry = dict(d["geometry"]) if isinstance(d.get("geometry"), dict) else {}
        self.geometry_log = [(float(t), dict(g)) for t, g in d.get("geometry_log") or []]
        self.capture_gaps = [list(g) for g in d.get("capture_gaps") or []]
        self.procedure_zone_overrides = dict(d.get("procedure_zone_overrides") or {})
        self.video_labels = [dict(v) for v in d.get("video_labels") or []]
        self.recording_log = [(float(t), str(m)) for t, m in d.get("recording_log") or []]
        self.record_parts = [str(p) for p in d.get("record_parts") or []]
        # what save_live_test reads from the procedure engine of a live session
        self.engine = SimpleNamespace(
            zone_labels=dict(d.get("zone_labels") or {}),
            scheduled_tests=[dict(s) for s in d.get("scheduled_tests") or []],
            animal_weights=[(float(t), float(g)) for t, g in d.get("animal_weights") or []],
            kept_variables=dict(d.get("kept_variables") or {}),
            result_variables=dict(d.get("result_variables") or {}),
            io_events=[])
        log = [str(x) for x in d.get("procedure_log") or []]
        self.outputs = SimpleNamespace(log=log) if log else None

    @property
    def elapsed(self) -> float:
        if self.io_only:
            return float(self.d.get("elapsed") or 0.0)
        t = self._track.cols["t"]
        return float(t[-1]) if t else 0.0

    @property
    def has_data(self) -> bool:
        if self.io_only:
            return self.elapsed > 0 or bool(self.d.get("io_events") or self.d.get("events"))
        return bool(len(self._track))

    @property
    def io_events(self) -> list:
        evs = [dict(e) for e in self.d.get("io_events") or []]
        return merge_events(evs, load_samples(self.d.get("io_samples"))) if self.d.get("io_samples") else evs

    def samples_file(self) -> str | None:
        """The side file of the fast analogue samples, if any (deleted with the side file)."""
        return (self.d.get("io_samples") or {}).get("file") or None

    @property
    def result_variables(self) -> dict:
        return dict(self.d.get("result_variables") or {})

    def track(self):
        if self.io_only:
            return None
        tr = self._track.build(self.fps)
        tr.meta["source"] = "live"
        tr.meta["recovered"] = True
        return postprocess(tr, self.settings)


def recover(project) -> list:
    """Tests interrupted by a crash: rebuild them from the side files left in the recordings folder.  Each
    recovered test gets the track, events, pauses and I/O log written up to the last autosave (and its recording,
    playable up to the last fragment).  The project is saved, then the side files are deleted.  Returns the
    recovered tests.

    Nothing is recovered from an experiment opened read-only or locked by another program that is still running,
    nor from side files whose writer is still running: those tests are not interrupted, they are running elsewhere.
    A side file whose test id now belongs to another animal's test becomes a new test."""
    if project is None or project.path is None or getattr(project, "read_only", False):
        return []
    if explock.held_by_other(project.path) is not None:
        return []
    folder = Path(project.path) / "recordings"
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob(f"*{SUFFIX}")):
        try:
            d = read(str(f), getattr(project, "password", None))
        except Exception:  # unreadable, or encrypted with a password the experiment no longer has: left alone
            continue
        if owner_running(d, f):
            continue
        s = RecoveredSession(d)
        if not s.has_data:
            f.unlink(missing_ok=True)
            _remove_samples(s)
            continue
        m = d.get("meta") or {}
        test = project.get_test(m["test_id"]) if m.get("test_id") is not None else None
        if test is not None and str(m.get("animal") or "") not in ("", test.animal_id):
            test = None  # the test id was given to another animal's test since: recover as a new test
        if test is not None and (test.recorded_at or "") >= str(d.get("saved_at") or "~"):
            f.unlink(missing_ok=True)  # stale: the test was saved after this side file was written
            _remove_samples(s)
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
        out.append((test, f, s))
    if out:
        project.save()  # the side files go only once their data is in the saved experiment
        for _, f, s in out:
            f.unlink(missing_ok=True)
            _remove_samples(s)
    return [t for t, _, _ in out]


def _remove_samples(s: RecoveredSession):
    p = s.samples_file()
    if p:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
