"""A track built one detection at a time, in bounded memory.

:class:`TrackBuilder` holds the columns of a track (track.COLUMNS, then the whole-body outlines) as they are tracked.
Tracking a video file keeps them all in memory. A live test (``spill=True``) keeps only its last rows: every
``chunk`` rows they are appended to a side file and dropped from memory, so that a 24-hour test needs about as much
memory as a one-minute one (Python lists of a row and its outline take 0.6–0.7 KB: 60 MB per hour of test).

The side file of a live test with a crash-recovery file is ``<autosave file>.track`` (in the recordings folder);
otherwise an anonymous temporary file. It is a sequence of chunks, each a header of three little-endian int64
(rows, outline points, bytes per outline coordinate: 2 or 4), the rows as float64 (the columns, then the row's
outline point count, -1 for none) and the outline points as int16 x y pairs (int32 if a coordinate does not fit).
Like the tracks, it is not encrypted (see core.security).

:meth:`TrackBuilder.recovery_state` gives the crash-recovery file what it needs — the rows still in memory and how
many are in the side file — and :meth:`TrackBuilder.restore` rebuilds the builder from it; :meth:`TrackBuilder.build`
reads the whole track back when the test is saved. Every method may be called from any thread: the frame thread
adds rows while the autosave thread, the live charts and the display read them.
"""

from __future__ import annotations

import bisect
import os
import tempfile
import threading
import weakref
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

import numpy as np

from .track import COLUMNS, Track

CHUNK_ROWS = 1500  # rows written to the side file at a time (a minute at 25 frames/s)
NCOL = len(COLUMNS) + 1  # float64 values per row in the side file: the columns, then the outline's point count
_HEADER = 3 * 8  # bytes of a chunk header
_DETECTED = COLUMNS.index("detected")


class _Chunk(NamedTuple):
    first: int  # its first row in the track
    rows: int
    offset: int  # of its header in the side file
    points: int  # outline points
    point_bytes: int  # 2 (int16) or 4 (int32)
    t0: float  # time of its first row

    @property
    def size(self) -> int:
        return _HEADER + self.rows * NCOL * 8 + self.points * 2 * self.point_bytes


class _Files:
    """The side file's handles (closed when the builder is closed or garbage-collected)."""

    def __init__(self):
        self.w = None  # appends chunks (frame thread)
        self.r = None  # reads them back (any thread)
        self.remove: str | None = None  # a path to delete once closed (a temporary file that could not be unlinked)

    def close(self):
        for fh in (self.w, self.r):
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
        self.w = self.r = None
        if self.remove:
            try:
                Path(self.remove).unlink(missing_ok=True)
            except OSError:
                pass
            self.remove = None


def _rows(cols: dict, i0: int = 0, i1: int | None = None) -> tuple[np.ndarray, list]:
    """Rows [i0, i1) of in-memory columns as an (n, NCOL) float64 array (outline point counts last) and their
    outlines (None for none)."""
    outline = list(cols["outline"][i0:i1])
    n = len(outline)
    rows = np.empty((n, NCOL))
    for j, c in enumerate(COLUMNS):
        rows[:, j] = np.asarray(cols[c][i0:i1], float)
    rows[:, -1] = [len(o) if o is not None and len(o) else -1 for o in outline]
    return rows, [o if o is not None and len(o) else None for o in outline]


def _outlines(rows: np.ndarray, pts: np.ndarray) -> list:
    """The per-row outlines (int32 (k, 2) arrays, views of one array) of a chunk's rows and points."""
    counts = rows[:, -1].astype(np.int64)
    pts = pts.astype(np.int32)
    out: list = [None] * len(counts)
    ends = np.cumsum(np.maximum(counts, 0))
    for i in np.flatnonzero(counts > 0):
        out[i] = pts[ends[i] - counts[i]:ends[i]]
    return out


class TrackBuilder:
    """See the module docstring. ``cols``: rows to start from (track.COLUMNS lists and "outline"); ``path``: the
    side file (``spill`` without it: a temporary file); ``chunk``: rows per chunk."""

    def __init__(self, cols: dict | None = None, path: str | None = None, spill: bool = False,
                 chunk: int = CHUNK_ROWS):
        cols = cols or {}
        tail: dict[str, list] = {c: list(cols.get(c) or []) for c in COLUMNS}
        # older side files have no outlines: those frames have none
        given = [np.asarray(o, np.int32).reshape(-1, 2) if o is not None and len(o) else None
                 for o in list(cols.get("outline") or [])[:len(tail["t"])]]  # (nested lists when read from JSON)
        tail["outline"] = given + [None] * (len(tail["t"]) - len(given))
        self._tail = tail
        self.chunk = max(1, int(chunk)) if spill else 0
        self.path = str(path) if spill and path else None
        self.error = ""  # why the last write to the side file failed (the rows stay in memory)
        self._lock = threading.Lock()  # the rows in memory, the chunk index, the write handle
        self._rlock = threading.Lock()  # the read handle
        self._chunks: list[_Chunk] = []
        self._spilled = 0  # rows in the side file
        self._end = 0  # bytes of complete chunks in the side file
        self._next_spill = self.chunk
        self._last: tuple[int, np.ndarray] | None = None  # (first row, rows) of the last chunk written
        self._files = _Files()
        self._finalizer = weakref.finalize(self, self._files.close)
        if self.path:
            Path(self.path).unlink(missing_ok=True)  # a new test

    # ------------------------------------------------------------------ recovery
    @classmethod
    def restore(cls, cols: dict | None, info: dict | None = None) -> TrackBuilder:
        """A builder of the track saved in a crash-recovery file: the side file's first ``info["rows"]`` rows,
        then ``cols`` (the rows that were in memory; older crash-recovery files have the whole track in them)."""
        b = cls(cols)
        if not info or not info.get("file") or not info.get("rows"):
            return b
        b.path = str(info["file"])  # (deleted by close(remove=True) once the test is saved)
        want = int(info["rows"])
        try:
            b._files.r = open(info["file"], "rb")
            size = os.fstat(b._files.r.fileno()).st_size
        except OSError:
            b._files.close()
            b.error = f"The track file {info['file']} could not be read"
            return b
        off = first = 0
        while first < want and off + _HEADER <= size:
            b._files.r.seek(off)
            hdr = np.frombuffer(b._files.r.read(_HEADER), "<i8")
            n, npts, pb = (int(v) for v in hdr)
            t0 = np.frombuffer(b._files.r.read(8), "<f8")
            ch = _Chunk(first, n, off, npts, pb, float(t0[0]) if len(t0) else 0.0)
            if n <= 0 or npts < 0 or pb not in (2, 4) or off + ch.size > size:
                break  # cut short by the crash
            b._chunks.append(ch)
            off += ch.size
            first += n
        b._spilled = min(first, want)
        b._end = off
        if b._spilled < want:
            b.error = f"The track file has {b._spilled} of its {want} rows"
        return b

    # ------------------------------------------------------------------ adding
    def __len__(self) -> int:
        return self._spilled + len(self._tail["t"])

    def add(self, t: float, d):
        """A row: time ``t`` and the columns of detection ``d`` (tracking.Detection)."""
        with self._lock:
            tail = self._tail
            tail["outline"].append(d.outline)
            tail["t"].append(t)
            for c in COLUMNS[1:]:
                tail[c].append(getattr(d, c))
            if self.chunk and len(tail["t"]) >= self._next_spill:
                self._spill()

    def _spill(self):
        """Append the rows in memory to the side file as a chunk (under the lock)."""
        tail = self._tail
        rows, outlines = _rows(tail)
        n = len(rows)
        pts = [o.reshape(-1, 2) for o in outlines if o is not None]
        pts = np.concatenate(pts) if pts else np.zeros((0, 2))
        small = not len(pts) or (pts.min() >= -32768 and pts.max() <= 32767)
        pb = 2 if small else 4
        data = (np.array([n, len(pts), pb], "<i8").tobytes() + rows.astype("<f8").tobytes()
                + pts.astype("<i2" if small else "<i4").tobytes())
        try:
            fh = self._writer()
            fh.seek(self._end)
            fh.write(data)
            fh.flush()
        except (OSError, ValueError) as e:  # disk full …: kept in memory, tried again a chunk later
            self.error = f"The track could not be written to its side file: {e}"
            self._next_spill = n + self.chunk
            return
        self.error = ""
        ch = _Chunk(self._spilled, n, self._end, len(pts), pb, float(rows[0, 0]))
        self._chunks.append(ch)
        self._end += ch.size
        self._spilled += n
        self._last = (ch.first, rows)
        self._tail = {c: [] for c in tail}
        self._next_spill = self.chunk

    def _writer(self):
        f = self._files
        if f.w is None:
            if self.path:
                f.w = open(self.path, "w+b")
                f.r = open(self.path, "rb")
            else:
                fd, name = tempfile.mkstemp(prefix="manymaze-track-", suffix=".track")
                f.w = os.fdopen(fd, "w+b")
                f.r = open(name, "rb")
                try:
                    os.unlink(name)  # gone with its handles (POSIX)
                except OSError:
                    f.remove = name  # Windows: deleted when closed
        return f.w

    # ------------------------------------------------------------------ reading
    def _read_chunk(self, ch: _Chunk, outline: bool) -> tuple[np.ndarray, list | None]:
        with self._rlock:
            fh = self._files.r
            if fh is None:
                raise OSError("the track's side file is closed")
            fh.seek(ch.offset + _HEADER)
            nb = ch.rows * NCOL * 8
            raw = fh.read(nb + (ch.points * 2 * ch.point_bytes if outline else 0))
        rows = np.frombuffer(raw, "<f8", count=ch.rows * NCOL).reshape(ch.rows, NCOL)
        if not outline:
            return rows, None
        pts = np.frombuffer(raw, "<i2" if ch.point_bytes == 2 else "<i4", count=ch.points * 2, offset=nb)
        return rows, _outlines(rows, pts.reshape(-1, 2))

    def read(self, i0: int = 0, i1: int | None = None, columns: Iterable[str] = COLUMNS,
             outline: bool = False) -> dict:
        """Rows [i0, i1) as numpy arrays of the given columns ("detected" bool, the others float), with
        "outline" (an object array of (k, 2) int32 polygons or None) when ``outline``."""
        columns = list(columns)
        with self._lock:
            n = len(self)
            i1 = n if i1 is None else max(0, min(int(i1), n))
            i0 = max(0, min(int(i0), i1))
            s = self._spilled
            chunks = [ch for ch in self._chunks if ch.first < min(i1, s) and ch.first + ch.rows > i0]
            last = self._last
            tail = None
            if i1 > s:
                lo = max(i0 - s, 0)
                tail = _rows(self._tail, lo, i1 - s)
        parts, outl = [], []
        for ch in chunks:
            if not outline and last is not None and last[0] == ch.first:
                rows, ol = last[1], None
            else:
                rows, ol = self._read_chunk(ch, outline)
            lo, hi = max(i0, ch.first) - ch.first, min(i1, s, ch.first + ch.rows) - ch.first
            parts.append(rows[lo:hi])
            if outline:
                outl.extend(ol[lo:hi])
        if tail is not None:
            parts.append(tail[0])
            outl.extend(tail[1])
        rows = np.concatenate(parts) if parts else np.zeros((0, NCOL))
        out = {c: rows[:, COLUMNS.index(c)].astype(bool if c == "detected" else float) for c in columns}
        if outline:
            arr = np.empty(len(rows), object)
            for i, o in enumerate(outl):  # (one by one: a list of equal-shaped polygons would broadcast)
                if o is not None:
                    arr[i] = o
            out["outline"] = arr
        return out

    def last(self, column: str = "t"):
        """The column's value in the last row (a Python float, bool for "detected"), or None without rows."""
        with self._lock:
            v = self._tail[column]
            if v:
                x = v[-1]
            elif self._last is not None and self._last[0] + len(self._last[1]) == self._spilled:
                x = self._last[1][-1, COLUMNS.index(column)]
            elif self._spilled:
                x = None
            else:
                return None
        if x is None:
            x = self.read(len(self) - 1, None, [column])[column][-1]
        return bool(x) if column == "detected" else float(x)

    def index_of_time(self, t: float, hi: int | None = None) -> int:
        """The first row (before ``hi``) whose time is at least ``t`` (rows are in time order)."""
        with self._lock:
            hi = len(self) if hi is None else max(0, min(int(hi), len(self)))
            s = self._spilled
            tail_t = list(self._tail["t"][:max(0, hi - s)])
            chunks = [ch for ch in self._chunks if ch.first < hi]
        if tail_t and tail_t[0] < t:
            return min(hi, s + bisect.bisect_left(tail_t, t))
        k = bisect.bisect_left([ch.t0 for ch in chunks], t) - 1
        if k < 0:
            return 0
        ch = chunks[k]
        tt = self.read(ch.first, min(hi, ch.first + ch.rows), ["t"])["t"]
        return min(hi, ch.first + int(np.searchsorted(tt, t, "left")))

    def build(self, fps: float) -> Track:
        """The whole track."""
        cols = self.read(outline=True)
        outline = cols.pop("outline")
        has = any(o is not None for o in outline)
        return Track(**cols, fps=fps, outline=outline if has else None)

    # ------------------------------------------------------------------ crash recovery
    def snapshot(self, n: int | None = None) -> dict[str, list]:
        """The first ``n`` rows (all by default) as lists of track.COLUMNS and "outline" (read back from the side
        file when they were written there: a crash-recovery file needs only :meth:`recovery_state`)."""
        cols = self.read(0, n, outline=True)
        out = {c: cols[c].tolist() for c in COLUMNS}
        out["outline"] = list(cols["outline"])
        return out

    def recovery_state(self, n: int | None = None) -> dict:
        """What a crash-recovery file needs of the first ``n`` rows (all by default): {"cols": the rows not in the
        side file, as lists, "track_file": {"file", "rows": rows to read from it} (only once rows were written
        there)}. The side file is flushed to the disk first."""
        with self._lock:
            n = len(self) if n is None else min(int(n), len(self))
            s = self._spilled
            out: dict = {"cols": {c: v[:max(0, n - s)] for c, v in self._tail.items()}}
            if s and self.path:
                out["track_file"] = {"file": self.path, "rows": min(s, n)}
            fh = self._files.w
        if fh is not None and "track_file" in out:
            try:
                os.fsync(fh.fileno())
            except (OSError, ValueError):
                pass
        return out

    # ------------------------------------------------------------------ the end
    def truncate(self, n: int):
        """Keep only the first ``n`` rows."""
        with self._lock:
            n = max(0, int(n))
            if n >= len(self):
                return
            s = self._spilled
            if n >= s:
                for v in self._tail.values():
                    del v[n - s:]
                return
            k = next(i for i, ch in enumerate(self._chunks) if ch.first + ch.rows > n)
            ch = self._chunks[k]
            rows, outl = self._read_chunk(ch, True)
            keep = n - ch.first
            self._chunks = self._chunks[:k]
            self._spilled, self._end = ch.first, ch.offset
            self._last = (self._chunks[-1].first, self._read_chunk(self._chunks[-1], False)[0]) \
                if self._chunks else None  # (the last row stays readable without the file: last())
            tail = {c: rows[:keep, j].tolist() for j, c in enumerate(COLUMNS)}
            tail["detected"] = [bool(v) for v in tail["detected"]]
            tail["outline"] = list(outl[:keep])
            self._tail = tail

    def close(self, remove: bool = False):
        """Close the side file (and delete it with ``remove``); the rows in it can no longer be read."""
        with self._lock, self._rlock:
            self._files.close()
            if remove and self.path:
                try:
                    Path(self.path).unlink(missing_ok=True)
                except OSError:
                    pass

    def side_file(self) -> str | None:
        """The side file, if any rows were written to a named one."""
        return self.path if self.path and self._chunks else None


class ColumnsView:
    """Read-only access to a builder's columns as sequences (``view["t"][-1]``, ``len(view["x"])``,
    ``view["x"][-250:]``) for code written when they were plain lists."""

    def __init__(self, builder: TrackBuilder):
        self._b = builder

    def __getitem__(self, column: str) -> _Column:
        if column not in COLUMNS and column != "outline":
            raise KeyError(column)
        return _Column(self._b, column)

    def __contains__(self, column) -> bool:
        return column in COLUMNS or column == "outline"

    def keys(self):
        return [*COLUMNS, "outline"]


class _Column:
    def __init__(self, builder: TrackBuilder, column: str):
        self._b, self._c = builder, column

    def __len__(self) -> int:
        return len(self._b)

    def _get(self, i0, i1) -> list:
        if self._c == "outline":
            return list(self._b.read(i0, i1, [], outline=True)["outline"])
        return self._b.read(i0, i1, [self._c])[self._c].tolist()

    def __getitem__(self, i):
        n = len(self._b)
        if isinstance(i, slice):
            a, b, step = i.indices(n)
            return self._get(a, b)[::step] if step > 0 else self._get(0, n)[i]
        j = int(i) + n if int(i) < 0 else int(i)
        if not 0 <= j < n:
            raise IndexError("track row out of range")
        return self._get(j, j + 1)[0]

    def __iter__(self):
        return iter(self._get(0, None))
