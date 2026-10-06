"""Several live tests at once: one or more camera sources (each read in its own thread), each feeding one or more
sessions (one per apparatus / arena in the image), with collective start / pause / stop, keyboard and remote
start / stop keys and scheduled starts at a clock time."""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .camera import FramePacer, SourceSpec
from .live import AUTOSAVE_SUFFIX, LiveSession, ObservationSession, read_autosave
from .tracking import draw_overlay, median_background

DEFAULT_START_KEYS = ["Space", "PageDown", "F5"]
DEFAULT_STOP_KEYS = ["B", "PageUp"]
STATE_COLOURS = {"idle": (139, 116, 100), "waiting": (11, 158, 245), "running": (38, 38, 220),
                 "paused": (8, 179, 234), "finished": (237, 58, 124)}


def parse_time(hhmm: str) -> _dt.time:
    h, m = (str(hhmm).strip() + ":0").split(":")[:2]
    return _dt.time(int(h) % 24, int(m) % 60)


@dataclass
class ClockSchedule:
    """Start tests at a clock time (HH:MM), once or every day.  ``entry_ids`` None = every entry."""

    at: str = "05:00"
    daily: bool = False
    entry_ids: list | None = None
    created: _dt.datetime | None = None
    next_fire: _dt.datetime | None = None

    def __post_init__(self):
        now = self.created or _dt.datetime.now()
        t = parse_time(self.at)
        nxt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += _dt.timedelta(days=1)
        self.next_fire = self.next_fire or nxt

    def due(self, now: _dt.datetime) -> bool:
        return self.next_fire is not None and now >= self.next_fire

    def fired(self, now: _dt.datetime):
        if self.daily:
            while self.next_fire <= now:
                self.next_fire += _dt.timedelta(days=1)
        else:
            self.next_fire = None

    def describe(self) -> str:
        if self.next_fire is None:
            return "done"
        return f"{'every day at' if self.daily else 'at'} {self.at} (next {self.next_fire:%a %H:%M})"


@dataclass
class LiveEntry:
    """One test in the group: a session bound to a source (None for observation-only)."""

    id: int
    source_key: str | None
    session: LiveSession | ObservationSession | None = None
    label: str = ""
    apparatus: object = None
    meta: dict = field(default_factory=dict)  # test id, animal, stage, trial, new test, record path …
    aborted: bool = False  # finished without data worth saving (e.g. the video ended before the start)
    saved: bool = False

    @property
    def state(self) -> str:
        return self.session.state if self.session is not None else "idle"

    @property
    def elapsed(self) -> float:
        return self.session.elapsed if self.session is not None else 0.0


class SourceRunner:
    """Reads one source in its own thread and dispatches every frame to the group.

    A rendered display image (all sessions' overlays) is produced only when the UI took the previous one."""

    def __init__(self, group: "LiveGroup", key: str, spec: SourceSpec, opener=None, speed: float = 1.0,
                 loop: bool = True):
        self.group, self.key, self.spec = group, key, spec
        self.opener = opener
        self.speed = speed
        self.loop = loop
        self.fps = 25.0
        self.size: tuple[int, int] | None = None
        self.error = ""
        self.ended = False
        self.opened = False
        self.background: np.ndarray | None = None
        self.last_frame: np.ndarray | None = None
        self.frames = 0
        self.src = None
        self._display: np.ndarray | None = None
        self._want_display = True
        self._stop = False
        self._restart = False
        self.thread = threading.Thread(target=self._run, name=f"live-{key}", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self, wait: float = 5.0):
        self._stop = True
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(wait)

    def restart(self):
        self._restart = True

    def raw_frames(self):
        """(primary, second) untransformed frames for the camera options dialog."""
        src = self.src
        if src is not None and hasattr(src, "last_raw"):
            return src.last_raw, src.last_raw2
        return self.last_frame, None

    def take_display(self) -> np.ndarray | None:
        d, self._display = self._display, None
        if d is not None:
            self._want_display = True
        return d

    def _run(self):
        try:
            src = self.spec.open(self.opener)
        except Exception as e:
            self.error = f"Cannot open {self.spec.label}: {e}"
            self.group._source_failed(self.key, self.error)
            return
        try:
            self.src = src
            self.fps = float(getattr(src, "fps", 25.0) or 25.0)
            self.size = (int(src.width), int(src.height))
            self.opened = True
            if not src.is_camera and getattr(src, "frame_count", 0):
                try:
                    n = src.frame_count
                    self.background = median_background(
                        [f for f in (src.frame_at(int(i)) for i in np.unique(np.linspace(0, n - 1, 15).astype(int)))
                         if f is not None])
                except Exception:
                    self.background = None
                src.seek(0)
            self._loop(src)
        except Exception as e:  # surfaced to the UI through group warnings
            import traceback

            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
            self.group._source_failed(self.key, self.error)
        finally:
            src.release()

    def _loop(self, src):
        pacer = FramePacer(self.fps, src.is_camera, self.speed)
        failures = 0
        while not self._stop:
            if self._restart:
                self._restart = False
                self.ended = False
                src.seek(0)
                pacer.reset()
            if self.ended:
                time.sleep(0.02)
                continue
            ok, frame = src.read()
            if not ok:
                if src.is_camera:
                    failures += 1
                    if failures > 100:
                        raise IOError("the camera stopped delivering frames")
                    time.sleep(0.01)
                    continue
                if self.loop and not self.group.has_active(self.key):
                    src.seek(0)
                    pacer.reset()
                    continue
                self.ended = True
                self.group.source_ended(self.key)
                continue
            failures = 0
            pacer.speed = self.speed
            ts = pacer.next()
            self.last_frame = frame
            self.frames += 1
            self.group.process(self.key, frame, ts)
            if self._want_display:
                self._want_display = False
                self._display = self.group.render(self.key, frame)


class LiveGroup:
    """Coordinates live sessions fed by one or more sources.

    Frames can be pushed synchronously with :meth:`process` (tests, custom drivers) or read by per-source threads
    started with :meth:`start_sources`.  Call :meth:`tick` regularly to run clock schedules.
    """

    def __init__(self, start_keys=None, stop_keys=None, clock: Callable[[], _dt.datetime] = _dt.datetime.now):
        self.sources: dict[str, SourceSpec] = {}
        self.runners: dict[str, SourceRunner] = {}
        self.entries: list[LiveEntry] = []
        self.schedules: list[ClockSchedule] = []
        self.start_keys = list(DEFAULT_START_KEYS if start_keys is None else start_keys)
        self.stop_keys = list(DEFAULT_STOP_KEYS if stop_keys is None else stop_keys)
        self.clock = clock
        self.warnings: list[tuple[str, str]] = []  # (time of day, message) for source-level problems
        self.on_schedule: Callable[[ClockSchedule, list[LiveEntry]], None] | None = None
        self._lock = threading.RLock()
        self._next_id = 1
        self._last_dets: dict[int, list] = {}

    # ------------------------------------------------------------------ setup
    def add_source(self, spec: SourceSpec, key: str | None = None) -> str:
        """Register a source; returns its id in the group (the spec key, made unique if needed)."""
        key = key or spec.key
        base, n = key, 2
        while key in self.sources and self.sources[key] is not spec:
            key, n = f"{base}#{n}", n + 1
        self.sources[key] = spec
        return key

    def add_entry(self, source_key: str | None, apparatus=None, label: str = "", meta: dict | None = None,
                  session=None) -> LiveEntry:
        with self._lock:
            e = LiveEntry(self._next_id, source_key, session, label or f"Test {self._next_id}", apparatus,
                          dict(meta or {}))
            if session is not None and apparatus is None:
                e.apparatus = getattr(session, "apparatus", None)
            self._next_id += 1
            self.entries.append(e)
            return e

    def add_session(self, source_key: str | None, session, label: str = "", meta: dict | None = None) -> LiveEntry:
        return self.add_entry(source_key, getattr(session, "apparatus", None), label, meta, session)

    def arm(self, entry: LiveEntry, session):
        with self._lock:
            entry.session = session
            entry.aborted = entry.saved = False
            if getattr(session, "apparatus", None) is not None:
                entry.apparatus = session.apparatus

    def remove(self, entry: LiveEntry):
        with self._lock:
            if entry.session is not None and entry.session.state != "finished":
                entry.session.finish()
            if entry in self.entries:
                self.entries.remove(entry)
            self._last_dets.pop(entry.id, None)

    def entry(self, entry_id: int) -> LiveEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def entries_for(self, key: str) -> list[LiveEntry]:
        return [e for e in self.entries if e.source_key == key]

    def has_active(self, key: str) -> bool:
        return any(e.state in ("waiting", "running", "paused") for e in self.entries_for(key))

    # ------------------------------------------------------------------ sources / threads
    def start_sources(self, opener=None, speed: float = 1.0, keys=None):
        for key in (keys or list(self.sources)):
            r = self.runners.get(key)
            if r is not None and r.thread.is_alive():
                continue
            r = SourceRunner(self, key, self.sources[key], opener, speed)
            self.runners[key] = r
            r.start()

    def stop_sources(self, keys=None):
        for key in list(keys or self.runners):
            r = self.runners.pop(key, None)
            if r is not None:
                r.stop()

    def restart_source(self, key: str):
        r = self.runners.get(key)
        if r is not None:
            r.restart()

    def set_speed(self, speed: float):
        for r in self.runners.values():
            r.speed = speed

    def _source_failed(self, key, msg):
        self.warnings.append((f"{_dt.datetime.now():%H:%M:%S}", msg))
        for e in self.entries_for(key):
            s = e.session
            if s is not None and s.state != "finished":
                e.aborted = s.state == "waiting" or not len(getattr(s, "cols", {}).get("t", []))
                s.finish()

    def source_ended(self, key: str):
        """End of a video file: running tests finish (and are saved), waiting tests are abandoned."""
        for e in self.entries_for(key):
            s = e.session
            if s is None or s.state == "finished":
                continue
            if s.state == "waiting":
                e.aborted = True
            s.finish()

    # ------------------------------------------------------------------ frames
    def process(self, key: str, frame: np.ndarray, ts: float):
        """Track one frame of source `key` in every session bound to it."""
        for e in self.entries_for(key):
            s = e.session
            if s is None or isinstance(s, ObservationSession):
                continue
            self._last_dets[e.id] = s.process(frame, ts)

    def render(self, key: str, frame: np.ndarray) -> np.ndarray:
        """The frame with every session's apparatus, detection, trail and a state label drawn on it."""
        img = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        img = img.copy()
        for e in self.entries_for(key):
            s = e.session
            dets = self._last_dets.get(e.id, []) if s is not None else []
            trail = None
            if s is not None and getattr(s, "cols", None) and s.state in ("running", "paused"):
                with s.lock:
                    trail = list(zip(s.cols["x"][-150:], s.cols["y"][-150:]))
            img = draw_overlay(img, dets, e.apparatus, trail)
            _label(img, e)
        return img

    # ------------------------------------------------------------------ control
    def start(self, entry: LiveEntry):
        """Start now (waiting) or resume (paused)."""
        s = entry.session
        if s is None:
            return
        if s.state == "waiting":
            s.request_start()
        elif s.state == "paused":
            s.resume()

    def pause(self, entry: LiveEntry):
        if entry.session is not None:
            entry.session.pause()

    def resume(self, entry: LiveEntry):
        if entry.session is not None:
            entry.session.resume()

    def stop(self, entry: LiveEntry, save: bool = True):
        s = entry.session
        if s is None or s.state == "finished":
            return
        if not save or s.state == "waiting":
            entry.aborted = True
        s.finish()

    def start_all(self):
        for e in self.entries:
            self.start(e)

    def pause_all(self):
        for e in self.entries:
            self.pause(e)

    def resume_all(self):
        for e in self.entries:
            self.resume(e)

    def stop_all(self, save: bool = True):
        for e in self.entries:
            self.stop(e, save)

    def key(self, key: str) -> str | None:
        """Handle a key press (keyboard or USB presenter / remote). Returns "start", "stop" or None."""
        k = key.strip().lower()
        if k in (x.lower() for x in self.start_keys) and any(e.state in ("waiting", "paused") for e in self.entries):
            self.start_all()
            return "start"
        if k in (x.lower() for x in self.stop_keys) and any(e.state in ("running", "paused") for e in self.entries):
            self.stop_all()
            return "stop"
        for e in self.entries:
            if e.session is not None:
                e.session.key(key)
        return None

    # ------------------------------------------------------------------ schedules
    def schedule(self, at: str, daily: bool = False, entry_ids=None) -> ClockSchedule:
        sch = ClockSchedule(at, daily, list(entry_ids) if entry_ids is not None else None, created=self.clock())
        self.schedules.append(sch)
        return sch

    def tick(self, now: _dt.datetime | None = None) -> list[ClockSchedule]:
        """Fire due schedules: by default request the start of their waiting sessions."""
        now = now or self.clock()
        fired = []
        for sch in list(self.schedules):
            if not sch.due(now):
                continue
            sch.fired(now)
            ents = [e for e in self.entries if sch.entry_ids is None or e.id in sch.entry_ids]
            if self.on_schedule is not None:
                self.on_schedule(sch, ents)
            else:
                for e in ents:
                    if e.state == "waiting":
                        e.session.request_start()
            fired.append(sch)
            if sch.next_fire is None:
                self.schedules.remove(sch)
        return fired

    # ------------------------------------------------------------------ results
    def finished_unsaved(self) -> list[LiveEntry]:
        return [e for e in self.entries if e.session is not None and e.state == "finished" and not e.saved]

    def all_warnings(self) -> list[str]:
        out = [f"{t}  {m}" for t, m in self.warnings]
        for e in self.entries:
            s = e.session
            if s is not None:
                out += [f"{e.label} · {t:.1f} s  {m}" for t, m in s.warnings]
        return out

    def close(self):
        self.stop_sources()
        for e in self.entries:
            if e.session is not None and e.session.state != "finished":
                e.aborted = True
                e.session.finish()


def _label(img, e: LiveEntry):
    st = e.state
    txt = e.label
    if e.session is not None:
        m, s = divmod(max(0.0, e.elapsed), 60)
        txt += f"  {st.upper()} {int(m):02d}:{s:04.1f}" if st in ("running", "paused", "finished") else \
            f"  {st.upper()}"
    x, y = 4, 4
    if e.apparatus is not None:
        try:
            x0, y0, _, _ = e.apparatus.arena_or_bounds().bounds()
            x, y = int(max(0, x0)), int(max(0, y0 - 18))
        except Exception:
            pass
    scale = max(0.38, img.shape[1] / 1600)
    (tw, th), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.rectangle(img, (x, y), (x + tw + 8, y + th + base + 6), STATE_COLOURS.get(st, (100, 100, 100)), -1)
    cv2.putText(img, txt, (x + 4, y + th + 3), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)


# ====================================================================== saving
def save_live_test(project, test, session, record_path: str | None = None) -> bool:
    """Store a finished live session in its test: track (camera sessions), recording, events, pauses, I/O events
    and procedure result variables.  Returns False if there was nothing to save."""
    tr = session.track()
    if tr is not None:
        if not len(tr):
            return False
        project.save_tracks(test, [tr])
        test.status = "tracked"
    else:
        if not session.events:
            return False
        test.status = "scored"
        if session.duration_s and session.elapsed < session.duration_s - 0.05:
            test.duration_s = round(session.elapsed, 3)
    if record_path and Path(record_path).exists():
        test.video = project.rel_path(record_path)
        test.start_s = 0.0
    test.events = sorted(list(test.events) + [dict(e) for e in session.events], key=lambda e: e.get("t", 0))
    test.pauses = [list(p) for p in session.pauses]
    test.io_events = list(test.io_events) + list(session.io_events)
    rv = session.result_variables
    if rv:
        test.result_variables = {**test.result_variables, **rv}
    kept = getattr(session, "kept_variables", None)
    if kept:  # procedure variables kept between tests: only from tests that are saved
        if getattr(project, "variables", None) is None:
            project.variables = {}
        project.variables.update(copy.deepcopy(kept))
    test.recorded_at = _dt.datetime.now().isoformat(timespec="seconds")
    try:
        from .workflow import refresh_status
        refresh_status(project, test)
    except Exception:
        pass
    notes = []
    outs = getattr(session, "outputs", None)
    if outs is not None and getattr(outs, "log", None):
        notes.append("Live procedures: " + "; ".join(outs.log[:50]))
    if session.pause_log:
        notes.append("Paused: " + "; ".join(f"at {p['t']:.2f} s for {p['duration_s']:.1f} s"
                                            for p in session.pause_log))
    if notes:
        test.notes = (test.notes + "\n" + "\n".join(notes)).strip()
    remove = getattr(session, "remove_autosave", None)
    if remove is not None:
        remove()
    return True


# ====================================================================== I/O devices of simultaneous tests
SHARED_DEVICE_TYPES = ("audio",)


def device_plan(configs, choice: str, others) -> tuple[str | None, str]:
    """Which I/O devices a test may use when several tests run at once.

    configs: ``Project.io_devices``; choice: the test panel's Device choice ("" automatic, "-" none / simulated,
    or a device name); others: what the tests already running use ("*" = all devices, "-" or a device name).
    Returns (plan, "") with plan "*" (the whole device manager: the only test, as in one-test mode), "-" (no
    hardware) or a device name (a :class:`iodevices.DeviceView` of that box), or (None, message) when the tests
    would share a box's inputs and outputs."""
    boxes = [str(c.get("name")) for c in configs or [] if c.get("enabled", True)
             and c.get("type", "virtual") not in SHARED_DEVICE_TYPES]
    others = [o for o in others or [] if o]
    choice = (choice or "").strip()
    if choice == "-":
        return "-", ""
    if not boxes:
        return "*", ""  # nothing that tests could share (no devices, or only the computer's speakers)
    if "*" in others:
        return None, ("Another test is running with all the I/O devices. Choose a Device for each test panel so "
                      "that every box has its own inputs and outputs.")
    if not choice:
        if not others:
            return "*", ""
        return None, ("Several tests are running at once: choose an I/O Device for each test panel (in the panel "
                      "settings) so that the tests do not share levers, outputs or shocks.")
    if choice not in boxes:
        return None, f"The I/O device '{choice}' is not configured (Experiment › I/O devices)."
    if choice in others:
        return None, f"The I/O device '{choice}' is already used by another running test."
    return choice, ""


# ====================================================================== crash recovery
def autosave_path_for(project, test) -> str:
    """The crash-recovery side file of a live test (in the recordings folder)."""
    safe = re.sub(r"[^\w.-]+", "_", getattr(test, "animal_id", "") or "animal")
    return str(project.recordings_dir() / f"test_{test.id:04d}_{safe}{AUTOSAVE_SUFFIX}")


class _RecoveredSession:
    """A live session rebuilt from its autosave side file (enough for :func:`save_live_test`)."""

    def __init__(self, d: dict):
        from .tracking import DetectionSettings

        self.d = d
        self.fps = float(d.get("fps") or 25.0)
        self.duration_s = float(d.get("duration_s") or 0.0)
        names = {f.name for f in dataclasses.fields(DetectionSettings)}
        self.settings = DetectionSettings(**{k: v for k, v in (d.get("settings") or {}).items() if k in names})
        self.cols = {k: list(v) for k, v in (d.get("cols") or {}).items()}
        self.events = [dict(e) for e in d.get("events") or []]
        t_end = round(self.elapsed, 3)
        open_states = set(d.get("open_states") or [])
        for e in self.events:  # state events still open at the crash end at the last autosave
            if e.get("t_end") is None and e.get("behaviour") in open_states:
                e["t_end"] = t_end
        self.pauses = [list(p) for p in d.get("pauses") or []]
        self.pause_log = [dict(p) for p in d.get("pause_log") or []]
        self.io_events = [dict(e) for e in d.get("io_events") or []]
        self.result_variables = dict(d.get("result_variables") or {})
        self.outputs = None

    @property
    def elapsed(self) -> float:
        t = self.cols.get("t") or []
        return float(t[-1]) if t else 0.0

    def track(self):
        from .track import Track
        from .tracking import postprocess

        cols = {}
        for c, v in self.cols.items():
            a = np.asarray([np.nan if x is None else x for x in v], dtype=bool if c == "detected" else float)
            cols[c] = a
        tr = Track(**cols, fps=self.fps)
        tr.meta["source"] = "live"
        tr.meta["recovered"] = True
        return postprocess(tr, self.settings)


def recover_autosaves(project) -> list:
    """Tests interrupted by a crash: rebuild them from the autosave side files left in the recordings folder.
    Each recovered test gets the track, events, pauses and I/O log written up to the last autosave (and its
    recording, playable up to the last fragment). Returns the recovered tests; the side files are deleted."""
    if project is None or getattr(project, "path", None) is None:
        return []
    folder = Path(project.path) / "recordings"
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob(f"*{AUTOSAVE_SUFFIX}")):
        try:
            d = read_autosave(str(f))
        except Exception:
            continue
        s = _RecoveredSession(d)
        if not s.cols.get("t"):
            f.unlink(missing_ok=True)
            continue
        m = d.get("meta") or {}
        test = project.get_test(m["test_id"]) if m.get("test_id") is not None else None
        if test is not None and (getattr(test, "recorded_at", "") or "") >= str(d.get("saved_at") or "~"):
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
            ok = save_live_test(project, test, s, rec if rec and Path(rec).exists() else None)
        except Exception:
            ok = False
        if not ok:
            continue
        when = d.get("saved_at", "")
        test.notes = (test.notes + "\n" + f"Recovered after the live test was interrupted (data up to "
                      f"{s.elapsed:.1f} s, saved {when}).").strip()
        f.unlink(missing_ok=True)
        out.append(test)
    return out
