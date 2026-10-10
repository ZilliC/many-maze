"""The built-in analysis plug-in ``csv_import``: time series from CSV / TSV files recorded by other systems (heart
rate from telemetry, fibre photometry, a Spike2 or LabChart text export …), one file per test, aligned with the test.

Each test's file is found from a name pattern with the test's fields — ``{test}`` (number), ``{animal}``,
``{stage}``, ``{trial}``, ``{video}`` (the video file's name without its extension) — relative to the experiment
folder unless absolute, e.g. ``photometry/{animal}_day{trial}.csv``. The file has a header row (rows above it — a
Spike2 summary, comments — are skipped: the header is the last text row before the numbers start), a time column and
the data columns; the delimiter (comma, semicolon, tab, spaces) is detected, and with a semicolon or tab a decimal
comma is understood. Times are in s, ms, min or h.

Alignment: the file's time at the test start is ``offset_s`` (``align`` = "test_start"), or the first rising edge in a
synchronisation column (``align`` = "sync": a channel that recorded the synchronisation element's test-start pulse,
or the first frame pulse; the edge is where the column rises through ``sync_threshold``). Samples before the test
start are left out, and so are those after the test end when the test's duration is known. ``resample_hz`` (> 0)
averages the samples into bins of that rate, to keep long, fast recordings small.

Each imported column becomes a series named after its header (with an optional ``prefix``); the plug-in also
reports, per test, "<name>: test start in the file (s)".
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import numpy as np

TITLE = "Data file (CSV / TSV)"
DESCRIPTION = ("Timestamped columns from a CSV or tab-separated file per test (heart rate, photometry, Spike2 or "
               "LabChart text exports), aligned with the test start or a synchronisation pulse.")
TIME_UNITS = {"s": 1.0, "ms": 0.001, "min": 60.0, "h": 3600.0}
OPTIONS = [
    ("file", "Data file of each test", "file", "",
     "File name pattern with {test}, {animal}, {stage}, {trial} and {video}, relative to the experiment folder, "
     "e.g. data/{animal}_{trial}.csv"),
    ("time_column", "Time column (name or number; empty: the first)", "text", ""),
    ("time_unit", "Times are in", "choice", "s", [("s", "seconds"), ("ms", "milliseconds"), ("min", "minutes"),
                                                  ("h", "hours")]),
    ("columns", "Columns to import (comma-separated; empty: all the others)", "text", ""),
    ("align", "The test starts", "choice", "test_start",
     [("test_start", "At the file time below"), ("sync", "At the first pulse in the synchronisation column")]),
    ("offset_s", "File time at the test start (s)", "number", 0.0),
    ("sync_column", "Synchronisation column", "text", ""),
    ("sync_threshold", "A pulse rises through", "number", 0.5),
    ("resample_hz", "Average into samples of (Hz; 0: keep every sample)", "number", 0.0),
    ("prefix", "Name the series", "text", "", "Put before every column name, e.g. “HR ”"),
]


class DataFileError(ValueError):
    pass


def file_for(project, test, pattern: str) -> Path:
    """The data file of a test from a name pattern (see the module docstring)."""
    if not str(pattern or "").strip():
        raise DataFileError("no data file pattern")
    video = Path(test.video).stem if test.video else ""
    fields = {"test": test.id, "animal": test.animal_id, "stage": test.stage, "trial": test.trial, "video": video}
    try:
        name = str(pattern).strip().format(**fields)
    except (KeyError, IndexError, ValueError) as e:
        raise DataFileError(f"the file pattern {pattern!r} is not valid ({e}): use {{test}}, {{animal}}, "
                            "{stage}, {trial} or {video}") from None
    p = Path(name).expanduser()
    if not p.is_absolute() and project.path is not None:
        p = Path(project.path) / p
    return p


def _number(cell: str, comma: bool) -> float:
    s = cell.strip().strip('"')
    if comma:
        s = s.replace(",", ".")
    try:
        return float(s) if s else math.nan
    except ValueError:
        return math.nan


def read_table(path) -> tuple[list[str], np.ndarray]:
    """(column names, numbers: rows × columns, NaN where empty) of a CSV / TSV file with header rows above."""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise DataFileError(f"{Path(path).name} is empty")
    sample = "\n".join(lines[-50:])
    if "\t" in sample:
        delim = "\t"
    elif ";" in sample:
        delim = ";"
    elif "," in sample:
        delim = ","
    else:
        delim = None  # spaces
    comma = delim in ("\t", ";")
    rows = [r for r in (csv.reader(lines, delimiter=delim) if delim else (ln.split() for ln in lines))]
    numeric = [sum(math.isfinite(_number(c, comma)) for c in r) >= max(1, len(r) // 2) and
               math.isfinite(_number(r[0], comma)) if r else False for r in rows]
    first = next((i for i, ok in enumerate(numeric) if ok), None)
    if first is None:
        raise DataFileError(f"{Path(path).name} has no rows of numbers")
    header = [c.strip().strip('"') for c in rows[first - 1]] if first > 0 else []
    data_rows = [r for r, ok in zip(rows[first:], numeric[first:]) if ok]
    width = max(len(r) for r in data_rows)
    header += [f"Column {i + 1}" for i in range(len(header), width)]
    header = [h or f"Column {i + 1}" for i, h in enumerate(header[:width])]
    data = np.full((len(data_rows), width), math.nan)
    for i, r in enumerate(data_rows):
        data[i, :len(r)] = [_number(c, comma) for c in r[:width]]
    return header, data


def _column(header: list[str], spec, what: str, default: int | None = None) -> int:
    s = str(spec if spec is not None else "").strip()
    if not s:
        if default is None:
            raise DataFileError(f"no {what} column given")
        return default
    low = [h.lower() for h in header]
    if s.lower() in low:
        return low.index(s.lower())
    if re.fullmatch(r"\d+", s) and 1 <= int(s) <= len(header):
        return int(s) - 1
    raise DataFileError(f"no {what} column {s!r} (columns: {', '.join(header)})")


def first_rising_edge(t: np.ndarray, v: np.ndarray, threshold: float) -> float | None:
    """The time at which v first rises through threshold (missing values count as below it)."""
    above = np.nan_to_num(v, nan=-math.inf) >= threshold
    rises = np.flatnonzero(above & ~np.r_[True, above[:-1]])  # a column high from its first row is no edge
    return float(t[rises[0]]) if len(rises) else None


def resample(t: np.ndarray, v: np.ndarray, hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Averages of v over consecutive bins of 1/hz s (times: the bins' starts); empty bins are left out."""
    ok = np.isfinite(t) & np.isfinite(v)
    t, v = t[ok], v[ok]
    if not len(t) or hz <= 0:
        return t, v
    k = np.floor(t * hz).astype(np.int64)
    keys, idx = np.unique(k, return_inverse=True)
    sums = np.bincount(idx, weights=v)
    counts = np.bincount(idx)
    return keys / hz, sums / counts


def import_csv(test, project, options: dict) -> dict:
    """The csv_import plug-in: {"series": {name: (t, values)}, "measures": {...}} of a test's data file."""
    o = {k: d for k, _l, _kind, d, *_ in OPTIONS}
    o.update({k: v for k, v in (options or {}).items() if v is not None})
    path = file_for(project, test, o["file"])
    if not path.exists():
        raise DataFileError(f"no data file {path}")
    header, data = read_table(path)
    tc = _column(header, o["time_column"], "time", default=0)
    unit = TIME_UNITS.get(str(o["time_unit"]), 1.0)
    t = data[:, tc] * unit
    if str(o["align"]) == "sync":
        sc = _column(header, o["sync_column"], "synchronisation")
        t0 = first_rising_edge(t, data[:, sc], float(o["sync_threshold"] or 0.5))
        if t0 is None:
            raise DataFileError(f"no pulse in the column {header[sc]!r} of {path.name}")
        skip = {tc, sc}
    else:
        t0 = float(o["offset_s"] or 0.0)
        skip = {tc}
    wanted = [c.strip() for c in str(o["columns"] or "").split(",") if c.strip()]
    cols = [_column(header, c, "data") for c in wanted] if wanted else \
        [i for i in range(len(header)) if i not in skip and np.isfinite(data[:, i]).any()]
    if not cols:
        raise DataFileError(f"{path.name} has no data columns")
    tt = t - t0
    dur = float(test.duration_s or project.test_duration_s or 0.0)
    keep = np.isfinite(tt) & (tt >= -1e-9) & ((tt <= dur + 1e-9) if dur > 0 else True)
    series = {}
    for c in cols:
        st, sv = resample(tt[keep], data[keep, c], float(o["resample_hz"] or 0.0))
        ok = np.isfinite(sv)
        series[f"{o['prefix'] or ''}{header[c]}"] = (st[ok], sv[ok])
    label = str(o.get("name") or TITLE)
    return {"series": series, "measures": {f"{label}: test start in the file (s)": round(t0, 6)}}
