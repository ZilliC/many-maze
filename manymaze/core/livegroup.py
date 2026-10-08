"""Several live tests at once: one or more camera sources (each read in its own thread), each feeding one or more
sessions (one per apparatus / arena in the image), with collective start / pause / stop, keyboard and remote
start / stop keys and scheduled starts at a clock time."""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .camera import SourceReader, SourceSpec
from .live import draw_display_texts
from .session import END_SOURCE, END_SOURCE_FAILED, Session
from .livemonitor import beam_angle
from .tracking import draw_tracking

DEFAULT_START_KEYS = ["Space", "PageDown", "F5"]
DEFAULT_STOP_KEYS = ["B", "PageUp"]


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
    session: Session | None = None
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


class SourceRunner(SourceReader):
    """A source of the group, read in its own thread: every frame goes to the sessions bound to it.  A display
    image (every animal and its trail) is rendered only when the UI took the previous one."""

    def __init__(self, group: "LiveGroup", key: str, spec: SourceSpec, opener=None, speed: float = 1.0):
        super().__init__(spec, opener, speed, name=f"live-{key}")
        self.group, self.key = group, key
        self._display: np.ndarray | None = None
        self._want_display = True

    def take_display(self) -> np.ndarray | None:
        d, self._display = self._display, None
        if d is not None:
            self._want_display = True
        return d

    def on_frame(self, frame: np.ndarray, ts: float):
        self.group.process(self.key, frame, ts)
        if self._want_display:
            self._want_display = False
            self._display = self.group.render(self.key, frame)

    def keep_looping(self) -> bool:
        return not self.group.has_active(self.key)

    def on_ended(self):
        self.group.source_ended(self.key)

    def on_failed(self, msg: str):
        self.group.source_failed(self.key, msg)

    def on_capture_lost(self, msg: str):
        self.group.capture_lost(self.key, msg)

    def on_capture_restored(self, gap_s: float):
        self.group.capture_restored(self.key, gap_s)


class LiveGroup:
    """Coordinates live sessions fed by one or more sources.

    Frames can be pushed synchronously with :meth:`process` (tests, custom drivers) or read by per-source threads
    started with :meth:`start_sources`.  Call :meth:`tick` regularly to run clock schedules.
    """

    def __init__(self, start_keys=None, stop_keys=None, clock: Callable[[], _dt.datetime] = _dt.datetime.now):
        self.sources: dict[str, SourceSpec] = {}
        self.runners: dict[str, SourceRunner] = {}
        self._stopping: dict[str, SourceRunner] = {}  # stopped runners whose thread had not ended yet
        self.entries: tuple[LiveEntry, ...] = ()  # copy-on-write: runner threads iterate it without the lock
        self.schedules: list[ClockSchedule] = []
        self.start_keys = list(DEFAULT_START_KEYS if start_keys is None else start_keys)
        self.stop_keys = list(DEFAULT_STOP_KEYS if stop_keys is None else stop_keys)
        self.clock = clock
        self.warnings: list[tuple[str, str]] = []  # (time of day, message) for source-level problems
        self.on_schedule: Callable[[ClockSchedule, list[LiveEntry]], None] | None = None
        self._lock = threading.RLock()
        self._next_id = 1
        self._last_dets: dict[int, list] = {}
        self.trail_len = 250  # positions drawn behind each animal on the display images (0 = none)
        self.beam = True  # draw each animal's orientation as a "flashlight beam"

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
                e.apparatus = session.apparatus
            self._next_id += 1
            self.entries += (e,)
            return e

    def add_session(self, source_key: str | None, session, label: str = "", meta: dict | None = None) -> LiveEntry:
        return self.add_entry(source_key, session.apparatus, label, meta, session)

    def arm(self, entry: LiveEntry, session):
        with self._lock:
            entry.session = session
            entry.aborted = entry.saved = False
            if session.apparatus is not None:
                entry.apparatus = session.apparatus

    def remove(self, entry: LiveEntry):
        with self._lock:
            if entry.session is not None and entry.session.state != "finished":
                entry.session.finish()
            self.entries = tuple(x for x in self.entries if x is not entry)
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
            old = self._stopping.get(key)
            if old is not None:
                old.stop()  # wait for it again: never two threads reading the same camera
                if old.thread.is_alive():
                    self.warnings.append((f"{_dt.datetime.now():%H:%M:%S}",
                                          f"{key}: the previous capture has not stopped yet; not restarted"))
                    continue
                del self._stopping[key]
            r = SourceRunner(self, key, self.sources[key], opener, speed)
            self.runners[key] = r
            r.start()

    def stop_sources(self, keys=None):
        for key in list(keys or self.runners):
            r = self.runners.pop(key, None)
            if r is not None:
                r.stop()
                if r.thread.is_alive():  # the join timed out: kept until its thread really ends
                    self._stopping[key] = r

    def restart_source(self, key: str):
        r = self.runners.get(key)
        if r is not None:
            r.restart()

    def set_speed(self, speed: float):
        for r in self.runners.values():
            r.speed = speed

    def source_failed(self, key, msg):
        self.warnings.append((f"{_dt.datetime.now():%H:%M:%S}", msg))
        for e in self.entries_for(key):
            s = e.session
            if s is not None and s.state != "finished":
                e.aborted = s.state == "waiting" or not len(s.cols["t"])  # sources feed camera sessions
                s.finish(END_SOURCE_FAILED)

    def capture_lost(self, key: str, msg: str):
        """A camera stopped delivering frames and is being reopened: its tests mark the gap."""
        self.warnings.append((f"{_dt.datetime.now():%H:%M:%S}", f"{key}: video capture lost ({msg}), reconnecting…"))
        for e in self.entries_for(key):
            if e.session is not None and hasattr(e.session, "capture_lost"):
                e.session.capture_lost(msg)

    def capture_restored(self, key: str, gap_s: float):
        self.warnings.append((f"{_dt.datetime.now():%H:%M:%S}", f"{key}: video capture restored after {gap_s:.1f} s"))
        for e in self.entries_for(key):
            if e.session is not None and hasattr(e.session, "capture_restored"):
                e.session.capture_restored(gap_s)

    def source_ended(self, key: str):
        """End of a video file: running tests finish (and are saved), waiting tests are abandoned."""
        for e in self.entries_for(key):
            s = e.session
            if s is None or s.state == "finished":
                continue
            if s.state == "waiting":
                e.aborted = True
            s.finish(END_SOURCE)

    # ------------------------------------------------------------------ frames
    def process(self, key: str, frame: np.ndarray, ts: float):
        """Track one frame of source `key` in every session bound to it."""
        for e in self.entries_for(key):
            s = e.session
            if s is None:
                continue
            self._last_dets[e.id] = s.process(frame, ts)

    def render(self, key: str, frame: np.ndarray) -> np.ndarray:
        """A copy of the frame with every session's animal and its last ``trail_len`` positions drawn on it (the
        GUI draws the apparatus over the image)."""
        img = draw_tracking(frame, [])
        for e in self.entries_for(key):
            s = e.session
            if s is not None:
                draw_tracking(img, self._last_dets.get(e.id, []), s.trail(self.trail_len) if self.trail_len else None,
                              copy=False, beam=self.beam and beam_angle(s))
                texts = getattr(s, "display_texts", None)
                if texts:  # the procedures' "output text on the display"
                    img = draw_display_texts(img, texts)
        return img

    # ------------------------------------------------------------------ control
    def start(self, entry: LiveEntry):
        """Start now (waiting), resume (paused) or continue a test waiting for its end."""
        s = entry.session
        if s is None:
            return
        if getattr(s, "waiting_end", False):
            s.continue_test()
        elif s.state == "waiting":
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
        if k in (x.lower() for x in self.start_keys) and any(
                e.state in ("waiting", "paused") or getattr(e.session, "waiting_end", False) for e in self.entries):
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


