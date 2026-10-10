"""The I/O log of a live test without unbounded growth: fast analogue samples are kept out of the procedure engine's
``io_events`` list of dicts (about 350 bytes each: 1.3 GB per hour of a 1 kHz channel) in a compact store, and the
live monitor's input statistics are kept up to date as events arrive instead of being recomputed from the whole log.

:class:`LiveIOLog` is installed as the engine's ``io_sink``: every logged event passes through it. Board-timed
analogue / sensor samples go to a :class:`SampleStore` (24 bytes a sample, written in chunks to a side file next to
the crash-recovery file when there is one, so that memory stays bounded during a 24 h test); ``io_events`` gets a
decimated copy (at most one sample per channel every ``DECIMATE_S``, marked ``"decimated": True``) for whatever
looks at the log while the test runs. :meth:`LiveIOLog.full_events` rebuilds the complete log (every sample) once,
when the test is saved.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np

SAMPLE_TYPES = ("analog", "sensor")  # logged input types whose board-timed samples are stored compactly
DECIMATED = "decimated"


class SampleStore:
    """Samples (t, value, board ms) of several channels in float64 rows [t, value, ms, channel index]: in memory
    up to ``chunk`` rows, then appended to ``path`` (if given) and dropped from memory."""

    def __init__(self, path: str | None = None, chunk: int = 50000):
        self.path = path
        self.chunk = max(1, int(chunk))
        self.channels: list[tuple[str, str, str]] = []  # (device, channel, type) of each channel index
        self._index: dict[tuple[str, str, str], int] = {}
        self._rows: list[tuple[float, float, float, float]] = []
        self._written = 0  # rows in the side file
        self._lock = threading.Lock()
        self.count = 0
        if path:
            Path(path).unlink(missing_ok=True)  # a new test

    def add(self, t: float, device: str, channel: str, typ: str, value: float, ms):
        key = (device, channel, typ)
        with self._lock:
            i = self._index.get(key)
            if i is None:
                i = self._index[key] = len(self.channels)
                self.channels.append(key)
            self._rows.append((float(t), float(value), float(ms) if ms is not None else math.nan, float(i)))
            self.count += 1
            if self.path and len(self._rows) >= self.chunk:
                self._spill()

    def _spill(self):
        if not self._rows:
            return
        with open(self.path, "ab") as fh:
            np.asarray(self._rows, np.float64).tofile(fh)
        self._written += len(self._rows)
        self._rows = []

    def flush(self) -> dict:
        """Write what is in memory to the side file (crash recovery); returns what the recovery needs."""
        with self._lock:
            if self.path:
                self._spill()
            return {"file": self.path, "rows": self._written, "channels": [list(c) for c in self.channels]}

    def rows(self) -> np.ndarray:
        """Every sample so far: an (n, 4) array."""
        with self._lock:
            parts = []
            if self.path and self._written:
                parts.append(np.fromfile(self.path, np.float64, count=self._written * 4).reshape(-1, 4))
            if self._rows:
                parts.append(np.asarray(self._rows, np.float64))
            return np.concatenate(parts) if parts else np.zeros((0, 4))

    def events(self) -> list[dict]:
        return rows_to_events(self.rows(), self.channels)

    def remove(self):
        with self._lock:
            self._rows = []
            if self.path:
                Path(self.path).unlink(missing_ok=True)


def rows_to_events(rows: np.ndarray, channels) -> list[dict]:
    """I/O log events (as ProcedureEngine._log_io writes them) of stored samples."""
    out = []
    for t, v, ms, i in rows.tolist():
        dev, ch, typ = channels[int(i)]
        e = {"t": round(t, 4), "device": dev, "channel": ch, "kind": "input", "value": v, "type": typ}
        if not math.isnan(ms):
            e["board_ms"] = int(ms) if float(ms).is_integer() else ms
        out.append(e)
    return out


def load_samples(meta: dict | None) -> list[dict]:
    """The samples of a crash-recovery side file (``LiveIOLog.snapshot``), or [] when there is none."""
    if not meta or not meta.get("file"):
        return []
    p = Path(meta["file"])
    if not p.exists():
        return []
    try:
        data = np.fromfile(str(p), np.float64)
    except OSError:
        return []
    n = len(data) // 4  # complete rows only (a crash during a write)
    return rows_to_events(data[:n * 4].reshape(-1, 4), [tuple(c) for c in meta.get("channels") or []])


def merge_events(events, samples) -> list[dict]:
    """The full I/O log: the events without their decimated copies of samples, and every sample."""
    out = [e for e in events if not e.get(DECIMATED)] + list(samples)
    out.sort(key=lambda e: e["t"])
    return out


class InputStats:
    """Running statistics of every input for the live monitor (livemonitor.input_rows): current value, activations,
    time on, latency to the first activation — updated event by event."""

    def __init__(self):
        self._s: dict[tuple[str, str], dict] = {}

    def add(self, e: dict):
        if e.get("kind") != "input":
            return
        key = (str(e.get("device", "")), str(e.get("channel", "")))
        s = self._s.get(key)
        if s is None:
            s = self._s[key] = {"type": "digital", "last": None, "state": False, "since": 0.0, "n": 0, "on": 0.0,
                                "first": None}
        s["type"] = e.get("type") or "digital"
        t, v = float(e.get("t", 0.0)), e.get("value")
        s["last"] = v
        on = bool(v)
        if on and not s["state"]:
            s["n"] += 1
            s["since"] = t
            if s["first"] is None:
                s["first"] = t
        elif not on and s["state"]:
            s["on"] += t - s["since"]
        s["state"] = on

    def rows(self, now: float) -> list[tuple[str, str, int, float, float | None]]:
        """As livemonitor.input_rows: (input, current value, activations, time on s, latency or None)."""
        names = [k[1] for k in self._s]
        out = []
        for key, s in list(self._s.items()):
            label = key[1] if names.count(key[1]) == 1 else f"{key[0]}/{key[1]}"
            last = s["last"]
            if s["type"] != "digital":
                txt = f"{last:.4g}" if isinstance(last, (int, float)) else str(last)
                out.append((label, txt, 0, math.nan, None))
                continue
            on = s["on"] + (max(0.0, now - s["since"]) if s["state"] else 0.0)
            out.append((label, "on" if s["state"] else "off", s["n"], on, s["first"]))
        return out


class LiveIOLog:
    """The engine's ``io_sink`` for a live test (see the module docstring). Called with the engine's io_events list
    and each new event; returns True when it took the event (stored as a sample) instead of the list."""

    DECIMATE_S = 0.1

    def __init__(self, path: str | None = None, chunk: int = 50000):
        self.path, self.chunk = path, chunk
        self._events = None
        self.store = SampleStore(None)
        self.stats = InputStats()
        self._last_emit: dict[tuple[str, str], float] = {}

    def __call__(self, events: list, e: dict) -> bool:
        if events is not self._events:  # the engine started a new log (test start): start again
            self._events = events
            self.store.remove()
            self.store = SampleStore(self.path, self.chunk)
            self.stats = InputStats()
            self._last_emit = {}
        self.stats.add(e)
        if e.get("kind") != "input" or e.get("type") not in SAMPLE_TYPES or e.get("board_ms") is None:
            return False
        self.store.add(e["t"], e["device"], e["channel"], e["type"], e["value"], e.get("board_ms"))
        key = (e["device"], e["channel"])
        if e["t"] - self._last_emit.get(key, -math.inf) >= self.DECIMATE_S - 1e-9:
            self._last_emit[key] = e["t"]
            events.append({**e, DECIMATED: True})
        return True

    def full_events(self, events) -> list[dict]:
        """Every event of the test, with every sample (for saving the test)."""
        if events is not self._events or not self.store.count:
            return [e for e in events if not e.get(DECIMATED)]
        return merge_events(events, self.store.events())

    def snapshot(self) -> dict:
        return self.store.flush()

    def remove(self):
        self.store.remove()


# ====================================================================== review of a test without a video
CONTINUOUS_TYPES = (*SAMPLE_TYPES, "encoder", "weight")  # input types shown as their values, not as on / off
REVIEW_ROWS = 20000  # at most this many lines in the Review page's list of the I/O log (the timeline shows all)
_TEXT_VALUES = {"on": 1.0, "true": 1.0, "yes": 1.0, "high": 1.0, "off": 0.0, "false": 0.0, "no": 0.0, "low": 0.0}


def _value(v) -> float:
    if isinstance(v, str):
        v = _TEXT_VALUES.get(v.strip().lower(), v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _channel_labels(keys) -> dict:
    """Channel names, with the device when the same name is used by several devices (as the I/O measures)."""
    names = [k[2] for k in keys]
    dup = {n for n in names if len({k[1] for k in keys if k[2] == n}) > 1}
    return {k: f"{k[1]}/{k[2]}" if k[2] in dup else k[2] for k in keys}


def review_channels(io_events, events=(), end: float | None = None) -> list[dict]:
    """A test's I/O log as the rows of a timeline (the Review page of a test without a video, e.g. an I/O only
    test): inputs, then outputs, then the scored keys and marks (``test.events``), each {"label", "kind" (input |
    output | event), "spans": [(on, off)], "points": [t], "values": [(t, value)]}. Digital channels and outputs with
    a level are their on spans (a pulse logged on and off at the same time is a point; one still on at the end
    lasts until `end`, else its last event); analogue, encoder, sensor and weight inputs are their values."""
    series: dict[tuple, list] = {}
    types: dict[tuple, set] = {}
    for e in io_events or []:
        kind = e.get("kind", "input")
        t = _value(e.get("t"))
        if kind not in ("input", "output") or not math.isfinite(t):
            continue
        key = (kind, str(e.get("device", "")), str(e.get("channel", "")))
        series.setdefault(key, []).append((t, _value(e.get("value"))))
        types.setdefault(key, set()).add(e.get("type") or "")
    labels = _channel_labels(list(series))
    last = max([t for ev in series.values() for t, _v in ev] + [0.0])
    stop = float(end) if end is not None and end > 0 else last
    rows = []
    for key in sorted(series, key=lambda k: k[0] != "input"):  # inputs first (stable: in order of appearance)
        ev = sorted(series[key], key=lambda x: x[0])
        row = {"label": labels[key], "kind": key[0], "spans": [], "points": [], "values": []}
        finite = [v for _t, v in ev if math.isfinite(v)]
        if key[0] == "input" and (types[key] & set(CONTINUOUS_TYPES) or any(v not in (0.0, 1.0) for v in finite)):
            row["values"] = [(t, v) for t, v in ev if math.isfinite(v)]
        else:
            since = None
            for t, v in ev:
                if v and since is None:
                    since = t
                elif not v and since is not None:
                    if t <= since:
                        row["points"].append(t)
                    else:
                        row["spans"].append((since, t))
                    since = None
            if since is not None:
                row["spans"].append((since, max(stop, since)))
        rows.append(row)
    keys: dict[str, dict] = {}
    for e in sorted(events or [], key=lambda e: e.get("t", 0.0)):
        name = str(e.get("behaviour", ""))
        row = keys.setdefault(name, {"label": name, "kind": "event", "spans": [], "points": [], "values": []})
        t0, t1 = _value(e.get("t")), e.get("t_end")
        if t1 is None:
            row["points"].append(t0)
        else:
            row["spans"].append((t0, _value(t1)))
    return rows + list(keys.values())


def review_rows(io_events, events=(), limit: int = REVIEW_ROWS) -> tuple[list[tuple], int]:
    """A test's I/O log as a list for the Review page, in time order: (t, what, name, value) with what = Input,
    Output, Variable (the procedures' variables) or Event (scored keys and marks, with their duration), and the
    value as text (on / off for digital channels). Analogue and sensor samples are left out (thousands a minute:
    the timeline shows them). Returns (the first `limit` lines, how many more there were)."""
    keys = {(str(e.get("kind", "input")), str(e.get("device", "")), str(e.get("channel", "")))
            for e in io_events or [] if e.get("kind", "input") in ("input", "output")}
    labels = _channel_labels(list(keys))
    out = []
    for e in io_events or []:
        kind = str(e.get("kind", "input"))
        if e.get("type") in SAMPLE_TYPES or e.get(DECIMATED):
            continue
        t, v = _value(e.get("t")), e.get("value")
        if kind == "variable":
            out.append((t, "Variable", str(e.get("channel", "")), str(v)))
            continue
        if kind not in ("input", "output"):
            continue
        key = (kind, str(e.get("device", "")), str(e.get("channel", "")))
        digital = e.get("type") not in (*CONTINUOUS_TYPES, "thermostat") and _value(v) in (0.0, 1.0)
        text = ("on" if _value(v) else "off") if digital else (f"{v:g}" if isinstance(v, (int, float)) else str(v))
        out.append((t, kind.capitalize(), labels[key], text))
    for e in events or []:
        t1 = e.get("t_end")
        out.append((_value(e.get("t")), "Event", str(e.get("behaviour", "")),
                    f"{_value(t1) - _value(e.get('t')):.2f} s" if t1 is not None else ""))
    out.sort(key=lambda r: r[0] if math.isfinite(r[0]) else math.inf)
    return out[:limit], max(0, len(out) - limit)
