"""Cache of computed result rows shared by the Results and Statistics pages.

Rows are keyed on the project object and on whether they are segmented into time periods, and are
invalidated by a cheap fingerprint of everything that changes results (tests, track files, scoring,
analysis settings, apparatus, animals). Segmented rows also serve non-segmented requests (their
"Whole test" rows are identical).
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal

from ...core.project import INFO_COLUMNS
from ..widgets import Worker

_lock = threading.Lock()
_cache: dict[tuple[int, bool], tuple[object, list[dict]]] = {}


# information columns that only mean something in a segmented (time period) analysis
SEGMENT_COLUMNS = ("Period", "Segment of test")


def info_columns(project) -> list[str]:
    return INFO_COLUMNS + [f for f in (project.animal_fields if project else []) if f not in INFO_COLUMNS]


def has_periods(project) -> bool:
    """True if the analysis settings define time bins or custom periods."""
    if project is None:
        return False
    s = project.analysis
    return bool(s.custom_periods) or (s.bin_length_s or 0) > 0 or bool(s.event_periods)


# test variables that only affect figures (not results) and must not invalidate cached rows
DISPLAY_VARIABLES = ("heatmap_transform",)


def _result_variables(v: dict | None) -> dict:
    return {k: x for k, x in (v or {}).items() if k not in DISPLAY_VARIABLES}


def _dump(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _mtimes(project, test) -> tuple:
    """Modification times of the test's track files (one per animal; 0 = missing)."""
    out = []
    for i in range(test.n_animals):
        try:
            p = project.track_path(test, i)
            out.append(p.stat().st_mtime_ns if p.exists() else 0)
        except (ValueError, OSError):
            out.append(0)
    return tuple(out)


def fingerprint(project) -> tuple:
    tests = tuple((t.id, t.status, _mtimes(project, t), t.animal_id, t.stage, t.trial, t.apparatus,
                   tuple(t.extra_animals), _dump(_result_variables(t.variables)), _dump(t.events), t.duration_s,
                   _dump(t.zone_overrides), _dump(t.pauses), _dump(t.io_events), _dump(t.result_variables),
                   t.notes, t.recorded_at, t.experimenter, t.end_reason, t.video, t.start_s)  # (information columns)
                  for t in project.tests)
    return (str(project.path), tests, project.test_duration_s, _dump(project.analysis.to_dict()),
            _dump([a.to_dict() for a in project.apparatus]), _dump([asdict(a) for a in project.animals]),
            _dump([asdict(b) for b in project.behaviours]), _dump(project.io_devices), tuple(project.animal_fields),
            tuple(g.name for g in project.groups), project.blind, _dump(project.settings_extra.get("blind_codes")))


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
    return _compute(id(project), project, segmented, fp, progress)


def _compute(key: int, project, segmented: bool, fp, progress=None) -> list[dict]:
    """Rows of `project` (the project itself, or a snapshot of the project `key` taken with fingerprint fp), cached
    under `key`."""
    rows = project.results(segmented=segmented, progress=progress)
    with _lock:
        for k in [k for k in _cache if k[0] != key]:
            del _cache[k]
        _cache[(key, segmented)] = (fp, rows)
        if segmented:
            _cache[(key, False)] = (fp, [r for r in rows if r.get("Period") == "Whole test"])
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
        # the worker reads a snapshot taken here: the pages go on editing the project while it computes
        snap = copy.deepcopy(project)
        w = Worker(lambda progress, stop: _compute(id(project), snap, segmented, fp, progress), self)
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
