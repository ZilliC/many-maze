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
