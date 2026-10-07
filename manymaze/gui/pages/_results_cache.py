"""Cache of computed result rows shared by the Results and Statistics pages.

Rows are keyed on the project object and on whether they are segmented into time periods, and are
invalidated by a cheap fingerprint of everything that changes results (tests, track files, scoring,
analysis settings, apparatus, animals). Segmented rows also serve non-segmented requests (their
"Whole test" rows are identical).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal

from ...core.project import INFO_COLUMNS
from ..widgets import Worker

_lock = threading.Lock()
_cache: dict[tuple[int, bool], tuple[object, list[dict]]] = {}


def info_columns(project) -> list[str]:
    return INFO_COLUMNS + [f for f in (project.animal_fields if project else []) if f not in INFO_COLUMNS]


def has_periods(project) -> bool:
    """True if the analysis settings define time bins or custom periods."""
    if project is None:
        return False
    s = project.analysis
    return bool(s.custom_periods) or (s.bin_length_s or 0) > 0 or bool(getattr(s, "event_periods", None))


# test variables that only affect figures (not results) and must not invalidate cached rows
DISPLAY_VARIABLES = ("heatmap_transform",)


def _result_variables(v: dict | None) -> dict:
    return {k: x for k, x in (v or {}).items() if k not in DISPLAY_VARIABLES}


def fingerprint(project) -> tuple:
    tests = []
    for t in project.tests:
        try:
            p = project.track_path(t)
            mtime = p.stat().st_mtime_ns if p.exists() else 0
        except (ValueError, OSError):
            mtime = 0
        tests.append((t.id, t.status, mtime, len(t.events), t.animal_id, t.stage, t.trial, t.apparatus,
                      tuple(t.extra_animals), json.dumps(_result_variables(t.variables), sort_keys=True, default=str),
                      json.dumps(t.events, sort_keys=True, default=str), t.duration_s,
                      json.dumps(getattr(t, "zone_overrides", None) or {}, sort_keys=True, default=str),
                      json.dumps(getattr(t, "pauses", None) or [], default=str)))
    return (str(project.path), tuple(tests), project.test_duration_s,
            json.dumps(project.analysis.to_dict(), sort_keys=True, default=str),
            json.dumps([a.to_dict() for a in project.apparatus], sort_keys=True, default=str),
            json.dumps([asdict(a) for a in project.animals], sort_keys=True, default=str),
            json.dumps([asdict(b) for b in project.behaviours], sort_keys=True, default=str),
            tuple(project.animal_fields))


def cached_rows(project, segmented: bool, fp=None) -> list[dict] | None:
    """Rows if a valid cached copy exists, else None. Cheap: safe to call on the UI thread."""
    if project is None:
        return []
    fp = fp if fp is not None else fingerprint(project)
    with _lock:
        hit = _cache.get((id(project), segmented))
        if hit and hit[0] == fp:
            return hit[1]
        if not segmented:
            hit = _cache.get((id(project), True))
            if hit and hit[0] == fp:
                rows = [r for r in hit[1] if r.get("Period") == "Whole test"]
                _cache[(id(project), False)] = (fp, rows)
                return rows
    return None


def get_rows(project, segmented: bool, force: bool = False, progress=None) -> list[dict]:
    """Result rows of the project, computed (slow) only when the cache is stale or force=True."""
    if project is None:
        return []
    fp = fingerprint(project)
    if not force:
        rows = cached_rows(project, segmented, fp)
        if rows is not None:
            return rows
    rows = project.results(segmented=segmented, progress=progress)
    with _lock:
        for key in [k for k in _cache if k[0] != id(project)]:
            del _cache[key]
        _cache[(id(project), segmented)] = (fp, rows)
        if segmented:
            _cache[(id(project), False)] = (fp, [r for r in rows if r.get("Period") == "Whole test"])
    return rows


def invalidate(project=None):
    with _lock:
        if project is None:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[0] == id(project)]:
                del _cache[key]


class RowsLoader(QObject):
    """Delivers result rows to a page, computing them in a background Worker when needed.

    Emits ``loaded(rows, segmented)`` (possibly synchronously from ``request`` on a cache hit),
    ``progress(fraction)`` and ``failed(message)``. Results of superseded requests are dropped.
    """

    loaded = Signal(object, bool)
    progress = Signal(float)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._workers: list[Worker] = []
        self.busy = False

    def request(self, project, segmented: bool, force: bool = False):
        self._gen += 1
        gen = self._gen
        if project is None:
            self._set_busy(False)
            self.loaded.emit([], segmented)
            return
        fp = fingerprint(project)
        if not force:
            rows = cached_rows(project, segmented, fp)
            if rows is not None:
                self._set_busy(False)
                self.loaded.emit(rows, segmented)
                return
            # the same calculation is already running (e.g. the page was shown twice): deliver its result
            for w in self._workers:
                if w.key == (id(project), segmented, fp) and w.isRunning() and not w.stopping:
                    w.gen = gen
                    self._set_busy(True)
                    return
        w = Worker(lambda progress, stop: get_rows(project, segmented, force, progress), self)
        w.key = (id(project), segmented, fp)
        w.gen = gen
        w.stopping = False
        w.signals.progress.connect(lambda f: w.gen == self._gen and self.progress.emit(f))
        w.signals.done.connect(lambda rows: self._done(w.gen, rows, segmented))
        w.signals.failed.connect(lambda msg: self._failed(w.gen, msg))
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        self._set_busy(True)
        w.start()

    def cancel(self):
        self._gen += 1
        self._set_busy(False)

    def _done(self, gen, rows, segmented):
        if gen != self._gen:
            return
        self._set_busy(False)
        self.loaded.emit(rows, segmented)

    def _failed(self, gen, msg):
        if gen != self._gen:
            return
        self._set_busy(False)
        self.failed.emit(msg)

    def _set_busy(self, b: bool):
        if b != self.busy:
            self.busy = b
            self.busy_changed.emit(b)

    def wait(self, ms: int = 60000):
        for w in list(self._workers):
            w.wait(ms)

    def shutdown(self):
        self.cancel()
        for w in list(self._workers):
            w.stopping = True
            w.stop()
            w.wait(30000)
