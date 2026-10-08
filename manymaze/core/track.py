"""Per-frame track data for one animal and helpers to clean, slice and store it."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import numpy as np

from .series import moving_average

COLUMNS = ["t", "x", "y", "hx", "hy", "tx", "ty", "area", "motion", "angle", "detected"]


@dataclass
class Track:
    """Arrays of equal length: one entry per analysed video frame.

    t: time (s) since test start; x, y: body centre (px); hx, hy / tx, ty: head / tail (px);
    area: blob area (px²); motion: changed pixels since previous frame (for freezing);
    angle: body orientation in degrees (tail→head, 0 = +x, clockwise positive since y is down);
    detected: True if the animal was found in this frame (False = interpolated / missing).
    """

    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    hx: np.ndarray = None
    hy: np.ndarray = None
    tx: np.ndarray = None
    ty: np.ndarray = None
    area: np.ndarray = None
    motion: np.ndarray = None
    angle: np.ndarray = None
    detected: np.ndarray = None
    fps: float = 25.0
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        n = len(self.t)
        self.t = np.asarray(self.t, float)
        self.x = np.asarray(self.x, float)
        self.y = np.asarray(self.y, float)
        for name in ("hx", "hy", "tx", "ty", "area", "motion", "angle"):
            v = getattr(self, name)
            setattr(self, name, np.full(n, np.nan) if v is None else np.asarray(v, float))
        if self.detected is None:
            self.detected = np.isfinite(self.x) & np.isfinite(self.y)
        self.detected = np.asarray(self.detected, bool)

    # ------------------------------------------------------------------
    def __len__(self):
        return len(self.t)

    @property
    def duration(self) -> float:
        if len(self.t) == 0:
            return 0.0
        return float(self.t[-1] - self.t[0] + self.dt)

    @property
    def dt(self) -> float:
        if len(self.t) > 1:
            return float(np.median(np.diff(self.t)))
        return 1.0 / self.fps if self.fps else 0.04

    def frame_durations(self) -> np.ndarray:
        """Time each sample represents (used for time-in-zone sums)."""
        if len(self.t) == 0:
            return np.zeros(0)
        d = np.diff(self.t, append=self.t[-1] + self.dt)
        return np.clip(d, 0, 10 * self.dt)

    def bodypart(self, part: str = "centre") -> tuple[np.ndarray, np.ndarray]:
        if part == "head":
            return self.hx, self.hy
        if part == "tail":
            return self.tx, self.ty
        return self.x, self.y

    def has_head(self) -> bool:
        return bool(np.isfinite(self.hx).any())

    def take(self, index) -> "Track":
        """The frames selected by `index` (a slice gives views of the columns; a mask or indices give copies)."""
        return Track(**{c: getattr(self, c)[index] for c in COLUMNS}, fps=self.fps, meta=dict(self.meta))

    def copy(self) -> "Track":
        return self.take(np.arange(len(self)))

    def slice_time(self, t0: float, t1: float) -> "Track":
        return self.take((self.t >= t0) & (self.t < t1))

    def slice_index(self, i0: int, i1: int) -> "Track":
        return self.take(slice(i0, i1))

    # ------------------------------------------------------------------
    def interpolate(self, max_gap_s: float = 1.0) -> "Track":
        """Linearly fill gaps (missing detections) up to max_gap_s long. Returns a new Track."""
        out = self.copy()
        for name in ("x", "y", "hx", "hy", "tx", "ty", "area"):
            out_v = getattr(out, name)
            _fill_gaps(out.t, out_v, max_gap_s)
        # orientation: interpolate unwrapped angle
        a = out.angle.copy()
        ok = np.isfinite(a)
        if ok.sum() > 1:
            a[ok] = np.degrees(np.unwrap(np.radians(a[ok])))
            _fill_gaps(out.t, a, max_gap_s)
            out.angle = (a + 180) % 360 - 180
        return out

    def smooth(self, window: int = 5) -> "Track":
        """Centred moving-average smoothing of positions (window in frames, odd)."""
        if window is None or window < 2:
            return self.copy()
        window = int(window) | 1
        out = self.copy()
        for name in ("x", "y", "hx", "hy", "tx", "ty"):
            setattr(out, name, moving_average(getattr(out, name), window))
        return out

    def to_units(self, scale: float) -> "Track":
        """Return a copy with spatial columns multiplied by scale (e.g. 1/px_per_cm)."""
        out = self.copy()
        for name in ("x", "y", "hx", "hy", "tx", "ty"):
            setattr(out, name, getattr(out, name) * scale)
        out.area = out.area * scale * scale
        return out

    # ------------------------------------------------------------------
    def to_csv(self, path_or_buf):
        close = False
        if isinstance(path_or_buf, (str, bytes)) or hasattr(path_or_buf, "__fspath__"):
            f = open(path_or_buf, "w", newline="")
            close = True
        else:
            f = path_or_buf
        try:
            f.write(f"# fps={self.fps}\n")
            for k, v in self.meta.items():
                f.write(f"# {k}={v}\n")
            w = csv.writer(f)
            w.writerow(COLUMNS)
            arr = np.column_stack([getattr(self, c).astype(float) for c in COLUMNS])
            for row in arr:
                w.writerow(["" if not np.isfinite(v) else (f"{v:.4f}" if i else f"{v:.5f}") for i, v in enumerate(row)])
        finally:
            if close:
                f.close()

    @classmethod
    def from_csv(cls, path_or_buf) -> "Track":
        if isinstance(path_or_buf, (str, bytes)) or hasattr(path_or_buf, "__fspath__"):
            with open(path_or_buf, newline="") as f:
                text = f.read()
        else:
            text = path_or_buf.read()
        fps = 25.0
        meta = {}
        lines = []
        for line in text.splitlines():
            if line.startswith("#"):
                k, _, v = line[1:].strip().partition("=")
                if k == "fps":
                    fps = float(v)
                else:
                    meta[k] = v
            elif line.strip():
                lines.append(line)
        r = csv.reader(io.StringIO("\n".join(lines)))
        header = next(r)
        rows = list(r)
        data = {c: np.full(len(rows), np.nan) for c in header}
        for i, row in enumerate(rows):
            for c, v in zip(header, row):
                data[c][i] = float(v) if v != "" else np.nan
        kw = {c: data[c] for c in COLUMNS if c in data}
        if "detected" in kw:
            kw["detected"] = np.nan_to_num(kw["detected"]).astype(bool)
        return cls(**kw, fps=fps, meta=meta)

    @classmethod
    def empty(cls, fps=25.0) -> "Track":
        z = np.zeros(0)
        return cls(t=z, x=z, y=z, fps=fps)


def swap_identities(a: Track, b: Track, t0: float, t1: float | None = None) -> int:
    """Exchange two animals' positions (all per-frame columns except time) from t0 to t1 (None = the end), in
    place, to correct an identity swap made by the tracker when animals touched. Returns the number of samples
    exchanged. Both tracks must come from the same test (the same frames)."""
    if len(a) != len(b) or not np.allclose(a.t, b.t, atol=1e-6):
        raise ValueError("The tracks do not have the same frames")
    m = a.t >= t0 - 1e-6
    if t1 is not None:
        m &= a.t <= t1 + 1e-6
    for c in COLUMNS:
        if c == "t":
            continue
        va, vb = getattr(a, c), getattr(b, c)
        va[m], vb[m] = vb[m].copy(), va[m].copy()
    return int(m.sum())


def _fill_gaps(t: np.ndarray, v: np.ndarray, max_gap_s: float):
    ok = np.isfinite(v)
    if ok.sum() < 2 or ok.all():
        return
    idx = np.flatnonzero(ok)
    missing = np.flatnonzero(~ok)
    missing = missing[(missing > idx[0]) & (missing < idx[-1])]
    if len(missing) == 0:
        return
    # find gap lengths
    nxt = idx[np.searchsorted(idx, missing)]
    prv = idx[np.searchsorted(idx, missing) - 1]
    gap = t[nxt] - t[prv]
    fill = missing[gap <= max_gap_s + 1e-9]
    if len(fill):
        v[fill] = np.interp(t[fill], t[ok], v[ok])


def import_deeplabcut_csv(path, fps: float, centre_parts=None, head_part=None, tail_part=None,
                          likelihood_min: float = 0.6) -> Track:
    """Import a DeepLabCut (or compatible) multi-header CSV as a Track.

    centre_parts: list of bodypart names averaged for the centre point (default: all parts).
    """
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    # DLC: scorer row, bodyparts row, coords row, then data
    hdr_idx = next(i for i, r in enumerate(rows) if r and r[0].lower() in ("bodyparts",))
    bodyparts = rows[hdr_idx]
    coords = rows[hdr_idx + 1]
    data = np.array([[float(v) if v not in ("", "nan") else np.nan for v in r] for r in rows[hdr_idx + 2:] if r],
                    float)
    parts: dict[str, dict[str, np.ndarray]] = {}
    for j in range(1, len(bodyparts)):
        parts.setdefault(bodyparts[j], {})[coords[j]] = data[:, j]
    for p in parts.values():
        if "likelihood" in p:
            bad = p["likelihood"] < likelihood_min
            p["x"] = np.where(bad, np.nan, p["x"])
            p["y"] = np.where(bad, np.nan, p["y"])
    names = centre_parts or list(parts)
    with np.errstate(invalid="ignore"):
        cx = np.nanmean(np.vstack([parts[n]["x"] for n in names]), axis=0)
        cy = np.nanmean(np.vstack([parts[n]["y"] for n in names]), axis=0)
    n = len(cx)
    t = np.arange(n) / fps
    kw = {}
    if head_part and head_part in parts:
        kw["hx"], kw["hy"] = parts[head_part]["x"], parts[head_part]["y"]
    if tail_part and tail_part in parts:
        kw["tx"], kw["ty"] = parts[tail_part]["x"], parts[tail_part]["y"]
    tr = Track(t=t, x=cx, y=cy, fps=fps, **kw)
    if head_part and tail_part and head_part in parts and tail_part in parts:
        tr.angle = np.degrees(np.arctan2(tr.hy - tr.ty, tr.hx - tr.tx))
    tr.meta["source"] = "deeplabcut"
    return tr
