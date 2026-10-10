"""Plug-ins: Python functions that extend mANY-MAZE, of two kinds.

**Procedure plug-ins** — the "Trigger a plug-in" action calls ``fn(argument: str, info: dict) -> value`` during a
live test. ``info`` holds the test time (``t``), a copy of the procedure variables (``variables``), the test context
(``context``: zones, test, animal …) and ``log(text)``. The value it returns (a number, text or list) can be stored in a
variable; exceptions become procedure errors. They run in the frame thread: they must return quickly (start a thread
for slow work). Register one with :func:`register` or the entry-point group ``manymaze.procedure_plugins``.

**Analysis plug-ins** — bring data recorded by other systems (heart rate, Spike2 channels, fibre photometry,
telemetry …) into the results. ``fn(test, project, options) -> {"series": {name: (t, values)}, "measures": {name:
value}}``: ``series`` are time series in test time (s from the test's start, as the I/O log: pauses included), stored
with the test (``Test.extra_series``, samples in ``tracks/test_NNNN_series.json``) and analysed like analogue
signals — mean, minimum, maximum, baseline and deviations for the whole test, every time period and every zone
(:mod:`.iomeasures`); ``measures`` are per-test values added to the test's results (``Test.extra_measures``).
``options`` is the plug-in's configuration in the protocol (``Project.analysis_plugins``: ``{"plugin": name,
"name": label, "enabled": bool, …}``). Plug-ins run when asked (Protocol ▸ Analysis ▸ Analysis plug-ins ▸ Run, or
``manymaze project DIR plugins``), not every time results are calculated. Register one with
:func:`register_analysis` or the entry-point group ``manymaze.analysis_plugins`` (name = plug-in name, object = the
function; its optional attributes ``title``, ``description`` and ``options`` describe it). The built-in
``csv_import`` reads timestamped columns from CSV / TSV files (:mod:`.datafiles`), so no code is needed for files.

Register in code::

    from manymaze.core import plugins
    plugins.register("reward_server", lambda arg, info: notify_server(arg))
    plugins.register_analysis("hrv", hrv_from_ecg, title="Heart-rate variability",
                              options=[("ecg_file", "ECG file of each test", "file", "")])
"""

from __future__ import annotations

import inspect
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ENTRY_POINT_GROUP = "manymaze.procedure_plugins"
ANALYSIS_GROUP = "manymaze.analysis_plugins"
SERIES_FORMAT = "manymaze-series"
SERIES_DEVICE = "plug-in"  # the device of the analogue samples series become in the analysis (iomeasures)
# option kinds of an analysis plug-in's settings form: "text", "file", "number", "int", "bool", "choice"
OPTION_KINDS = ("text", "file", "number", "int", "bool", "choice")
log = logging.getLogger(__name__)

_PLUGINS: dict[str, Callable] = {}
_loaded = False


# ================================================================================== procedure plug-ins
def register(name: str, fn: Callable) -> None:
    """Make ``fn(argument, info)`` available to procedures as the plug-in ``name``."""
    if not callable(fn):
        raise TypeError("a plug-in must be callable")
    _PLUGINS[str(name)] = fn


def unregister(name: str) -> None:
    _PLUGINS.pop(str(name), None)


def _load_entry_points():
    global _loaded
    if _loaded:
        return
    _loaded = True
    for ep in _entry_points(ENTRY_POINT_GROUP):
        if ep.name not in _PLUGINS:
            try:
                _PLUGINS[ep.name] = ep.load()
            except Exception:  # pragma: no cover - a broken third-party package
                pass


def _entry_points(group: str) -> list:
    try:
        from importlib.metadata import entry_points

        return list(entry_points(group=group))
    except Exception:  # pragma: no cover
        return []


def get(name: str) -> Callable | None:
    _load_entry_points()
    return _PLUGINS.get(str(name))


def names() -> list[str]:
    """The registered procedure plug-ins (for the procedure editor)."""
    _load_entry_points()
    return sorted(_PLUGINS)


# ================================================================================== analysis plug-ins
@dataclass
class AnalysisPlugin:
    """An analysis plug-in: ``fn(test, project, options)`` (or ``fn(test, project)``), its title, description
    and the options of its settings form: [(key, label, kind, default[, choices or tooltip])], kind one of
    OPTION_KINDS (choices: [(value, label)] for "choice")."""

    name: str
    fn: Callable
    title: str = ""
    description: str = ""
    options: list = field(default_factory=list)

    def call(self, test, project, options: dict) -> dict:
        try:
            n = len(inspect.signature(self.fn).parameters)
        except (TypeError, ValueError):
            n = 3
        return self.fn(test, project, options) if n >= 3 else self.fn(test, project)

    def defaults(self) -> dict:
        return {o[0]: o[3] for o in self.options}


_ANALYSIS: dict[str, AnalysisPlugin] = {}
_analysis_loaded = False


def register_analysis(name: str, fn: Callable, title: str = "", description: str = "", options=None) -> None:
    """Make ``fn(test, project, options)`` available as the analysis plug-in ``name`` (see the module docstring)."""
    if not callable(fn):
        raise TypeError("an analysis plug-in must be callable")
    _ANALYSIS[str(name)] = AnalysisPlugin(str(name), fn, title or str(name), description, list(options or []))


def unregister_analysis(name: str) -> None:
    _ANALYSIS.pop(str(name), None)


def _load_analysis():
    global _analysis_loaded
    if _analysis_loaded:
        return
    _analysis_loaded = True
    from . import datafiles  # the built-in CSV / TSV importer

    if "csv_import" not in _ANALYSIS:
        register_analysis("csv_import", datafiles.import_csv, datafiles.TITLE, datafiles.DESCRIPTION,
                          datafiles.OPTIONS)
    for ep in _entry_points(ANALYSIS_GROUP):
        if ep.name in _ANALYSIS:
            continue
        try:
            obj = ep.load()
        except Exception as e:  # pragma: no cover - a broken third-party package
            log.warning("analysis plug-in %s could not be loaded: %s", ep.name, e)
            continue
        if isinstance(obj, AnalysisPlugin):
            _ANALYSIS[ep.name] = obj
        elif callable(obj):
            register_analysis(ep.name, obj, getattr(obj, "title", ""), getattr(obj, "description", ""),
                              getattr(obj, "options", None))


def analysis_plugin(name: str) -> AnalysisPlugin | None:
    _load_analysis()
    return _ANALYSIS.get(str(name))


def analysis_names() -> list[str]:
    """The registered analysis plug-ins, the built-in CSV / TSV importer first."""
    _load_analysis()
    return sorted(_ANALYSIS, key=lambda n: (n != "csv_import", n))


def new_config(plugin: str, taken=()) -> dict:
    """The configuration of a new analysis plug-in in the protocol: its defaults and a unique name."""
    pl = analysis_plugin(plugin)
    if pl is None:
        raise KeyError(f"Unknown analysis plug-in {plugin!r}")
    base = pl.title or plugin
    name, k = base, 2
    while name in set(taken):
        name, k = f"{base} {k}", k + 1
    return {"plugin": plugin, "name": name, "enabled": True, **pl.defaults()}


# ---------------------------------------------------------------------------------- running them
def _clean(name) -> str:
    return " ".join(str(name).split())


def run_on_test(project, test) -> list[str]:
    """Run the protocol's enabled analysis plug-ins on a test and store what they return: its series (replacing
    those of earlier runs) and measures. Returns the problems (a plug-in that failed keeps nothing of this run)."""
    series: dict[str, tuple] = {}
    meta: dict[str, dict] = {}
    measures: dict[str, object] = {}
    problems = []
    for cfg in project.analysis_plugins:
        if not cfg.get("enabled", True):
            continue
        label = str(cfg.get("name") or cfg.get("plugin", "?"))
        pl = analysis_plugin(cfg.get("plugin", ""))
        if pl is None:
            problems.append(f"{label}: the plug-in {cfg.get('plugin')!r} is not installed")
            continue
        try:
            out = pl.call(test, project, dict(cfg)) or {}
            got_s, got_m = _checked(out)
        except Exception as e:
            problems.append(f"{label}: {e}")
            continue
        for name, (t, v, unit) in got_s.items():
            series[name] = (t, v)
            meta[name] = {"source": label, "samples": int(len(t)), "unit": unit}
        measures.update(got_m)
    save_series(project, test, series)
    test.extra_series = meta
    test.extra_measures = measures
    return problems


def _checked(out: dict) -> tuple[dict, dict]:
    """A plug-in's output, checked: {name: (t, values, unit)} with finite, sorted times, and {name: value}."""
    if not isinstance(out, dict):
        raise TypeError("an analysis plug-in must return a dict with 'series' and / or 'measures'")
    series = {}
    for name, s in (out.get("series") or {}).items():
        unit = ""
        if isinstance(s, dict):
            t, v, unit = s.get("t"), s.get("values", s.get("v")), str(s.get("unit") or "")
        else:
            t, v = s
        t, v = np.asarray(t, float).ravel(), np.asarray(v, float).ravel()
        if len(t) != len(v):
            raise ValueError(f"series {name!r}: {len(t)} times but {len(v)} values")
        ok = np.isfinite(t)
        order = np.argsort(t[ok], kind="stable")
        series[_clean(name)] = (t[ok][order], v[ok][order], unit)
    measures = {}
    for name, v in (out.get("measures") or {}).items():
        if isinstance(v, (bool, np.bool_)):
            v = int(v)
        elif isinstance(v, (int, float, np.integer, np.floating)):
            v = float(v) if math.isfinite(float(v)) else math.nan
        else:
            v = str(v)
        measures[_clean(name)] = v
    return series, measures


def run_analysis_plugins(project, tests=None, progress: Callable[[float], None] | None = None) -> dict:
    """Run the analysis plug-ins on tests (default: every test that was performed — tracked or scored — and is not
    left out of the results). Returns {"done": [test ids], "errors": [(test id, problem)]}; save the experiment to
    keep the results."""
    from .project import INACTIVE_STATUSES

    todo = [t for t in (tests if tests is not None else project.tests)
            if t.status not in INACTIVE_STATUSES and (tests is not None or t.status != "pending")]
    out = {"done": [], "errors": []}
    for i, t in enumerate(todo):
        for msg in run_on_test(project, t):
            out["errors"].append((t.id, msg))
        out["done"].append(t.id)
        if progress:
            progress((i + 1) / len(todo))
    return out


# ---------------------------------------------------------------------------------- stored series
def series_path(project, test) -> Path:
    if project.path is None:
        raise ValueError("Save the experiment first")
    return project.path / "tracks" / f"test_{test.id:04d}_series.json"


def save_series(project, test, series: dict):
    """Write a test's series ({name: (t, values)}) next to its tracks (or remove the file when there are none)."""
    from .atomicfile import write_text_atomic

    p = series_path(project, test)
    if not series:
        p.unlink(missing_ok=True)
        _cache.pop(str(p), None)
        return
    p.parent.mkdir(parents=True, exist_ok=True)

    def nums(a):
        return [round(float(x), 6) if math.isfinite(x) else None for x in a]

    write_text_atomic(p, json.dumps({"format": SERIES_FORMAT, "version": 1, "series": {
        name: {"t": nums(t), "v": nums(v)} for name, (t, v) in series.items()}}, separators=(",", ":")))


_cache: dict[str, tuple] = {}  # file -> (mtime_ns, series)


def load_series(project, test) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """A test's stored series: {name: (t, values)} (empty without any)."""
    if not test.extra_series or project.path is None:
        return {}
    p = series_path(project, test)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _cache.get(str(p))
    if hit is None or hit[0] != mt:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            series = {name: (np.asarray([math.nan if x is None else x for x in s["t"]], float),
                             np.asarray([math.nan if x is None else x for x in s["v"]], float))
                      for name, s in (d.get("series") or {}).items()}
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("series of test %s unreadable: %s", test.id, e)
            series = {}
        hit = (mt, series)
        _cache[str(p)] = hit
        if len(_cache) > 64:
            _cache.pop(next(iter(_cache)))
    return {k: v for k, v in hit[1].items() if k in test.extra_series}


def series_events(project, test) -> list[dict]:
    """A test's series as analogue samples of the I/O log ({"t", "device": "plug-in", "channel": name, "kind":
    "input", "type": "analog", "value"}): the analysis gives them the analogue-signal measures, whole test, per time
    period and per zone."""
    out = []
    for name, (t, v) in load_series(project, test).items():
        ok = np.isfinite(t) & np.isfinite(v)
        out += [{"t": float(a), "device": SERIES_DEVICE, "channel": name, "kind": "input", "type": "analog",
                 "value": float(b)} for a, b in zip(t[ok], v[ok])]
    out.sort(key=lambda e: e["t"])
    return out
