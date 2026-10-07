"""Geometric shapes used for arenas, zones, points of interest and lines.

All coordinates are in video pixels (x to the right, y downwards).
Shapes serialise to plain dicts so they can be stored in project JSON.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Shape:
    """Base class. Concrete shapes: Polygon, Ellipse."""

    def contains(self, x, y) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_dict(self) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def area(self) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def centroid(self) -> tuple[float, float]:  # pragma: no cover - abstract
        raise NotImplementedError

    def polygon(self, n: int = 72) -> np.ndarray:  # pragma: no cover - abstract
        """Return an (N, 2) float array approximating the outline."""
        raise NotImplementedError

    def bounds(self) -> tuple[float, float, float, float]:
        p = self.polygon()
        return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())

    def mask(self, shape_hw: tuple[int, int]) -> np.ndarray:
        """Binary uint8 mask (255 inside) of the given frame size."""
        m = np.zeros(shape_hw[:2], np.uint8)
        pts = np.round(self.polygon(180)).astype(np.int32)
        cv2.fillPoly(m, [pts], 255)
        return m

    def distance_to_edge(self, x, y) -> np.ndarray:
        """Unsigned distance from points to the outline of the shape."""
        poly = self.polygon(180)
        return _distance_to_polyline(np.asarray(x, float), np.asarray(y, float), poly, closed=True)

    def translated(self, dx: float, dy: float) -> "Shape":  # pragma: no cover - abstract
        raise NotImplementedError

    def scaled(self, sx: float, sy: float, ox: float = 0.0, oy: float = 0.0) -> "Shape":  # pragma: no cover
        raise NotImplementedError

    def similarity(self, dx: float = 0.0, dy: float = 0.0, angle_deg: float = 0.0, scale: float = 1.0,
                   ox: float = 0.0, oy: float = 0.0) -> "Shape":
        """Rotate by angle_deg (clockwise on screen) and scale about (ox, oy), then translate by (dx, dy)."""
        pts = similarity_points(self.polygon(), dx, dy, angle_deg, scale, ox, oy)
        return Polygon([(float(a), float(b)) for a, b in pts])


@dataclass
class Polygon(Shape):
    points: list[tuple[float, float]] = field(default_factory=list)

    def _arr(self) -> np.ndarray:
        return np.asarray(self.points, dtype=float).reshape(-1, 2)

    def contains(self, x, y) -> np.ndarray:
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        p = self._arr()
        if len(p) < 3:
            return np.zeros(np.broadcast(x, y).shape, bool)
        inside = np.zeros(np.broadcast(x, y).shape, bool)
        xj, yj = p[-1]
        for xi, yi in p:
            cond = (yi > y) != (yj > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            inside ^= cond & (x < xint)
            xj, yj = xi, yi
        return inside & np.isfinite(x) & np.isfinite(y)

    def area(self) -> float:
        p = self._arr()
        if len(p) < 3:
            return 0.0
        x, y = p[:, 0], p[:, 1]
        return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)

    def centroid(self) -> tuple[float, float]:
        p = self._arr()
        if len(p) < 3:
            return (float(p[:, 0].mean()), float(p[:, 1].mean())) if len(p) else (0.0, 0.0)
        x, y = p[:, 0], p[:, 1]
        cross = x * np.roll(y, -1) - np.roll(x, -1) * y
        a = cross.sum() / 2
        if abs(a) < 1e-9:
            return float(x.mean()), float(y.mean())
        cx = ((x + np.roll(x, -1)) * cross).sum() / (6 * a)
        cy = ((y + np.roll(y, -1)) * cross).sum() / (6 * a)
        return float(cx), float(cy)

    def polygon(self, n: int = 72) -> np.ndarray:
        return self._arr()

    def to_dict(self) -> dict:
        return {"type": "polygon", "points": [[float(a), float(b)] for a, b in self.points]}

    def translated(self, dx, dy):
        return Polygon([(x + dx, y + dy) for x, y in self.points])

    def scaled(self, sx, sy, ox=0.0, oy=0.0):
        return Polygon([(ox + (x - ox) * sx, oy + (y - oy) * sy) for x, y in self.points])


@dataclass
class Ellipse(Shape):
    cx: float = 0.0
    cy: float = 0.0
    rx: float = 1.0
    ry: float = 1.0

    def contains(self, x, y) -> np.ndarray:
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        with np.errstate(invalid="ignore"):
            r = ((x - self.cx) / self.rx) ** 2 + ((y - self.cy) / self.ry) ** 2
            return np.nan_to_num(r, nan=np.inf) <= 1.0

    def area(self) -> float:
        return float(math.pi * self.rx * self.ry)

    def centroid(self) -> tuple[float, float]:
        return float(self.cx), float(self.cy)

    def polygon(self, n: int = 72) -> np.ndarray:
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        return np.column_stack([self.cx + self.rx * np.cos(t), self.cy + self.ry * np.sin(t)])

    def distance_to_edge(self, x, y) -> np.ndarray:
        if abs(self.rx - self.ry) < 1e-6:
            d = np.hypot(np.asarray(x, float) - self.cx, np.asarray(y, float) - self.cy)
            return np.abs(d - self.rx)
        return super().distance_to_edge(x, y)

    def to_dict(self) -> dict:
        return {"type": "ellipse", "cx": self.cx, "cy": self.cy, "rx": self.rx, "ry": self.ry}

    def translated(self, dx, dy):
        return Ellipse(self.cx + dx, self.cy + dy, self.rx, self.ry)

    def scaled(self, sx, sy, ox=0.0, oy=0.0):
        return Ellipse(ox + (self.cx - ox) * sx, oy + (self.cy - oy) * sy, self.rx * abs(sx), self.ry * abs(sy))

    def similarity(self, dx=0.0, dy=0.0, angle_deg=0.0, scale=1.0, ox=0.0, oy=0.0):
        a = angle_deg % 180.0
        if abs(self.rx - self.ry) < 1e-9 or min(a, 180.0 - a) < 1e-9 or abs(a - 90.0) < 1e-9:
            (cx, cy), = similarity_points([[self.cx, self.cy]], dx, dy, angle_deg, scale, ox, oy)
            rx, ry = (self.ry, self.rx) if abs(a - 90.0) < 1e-9 else (self.rx, self.ry)
            return Ellipse(float(cx), float(cy), rx * scale, ry * scale)  # stays an axis-aligned ellipse
        return super().similarity(dx, dy, angle_deg, scale, ox, oy)


def similarity_points(pts, dx: float = 0.0, dy: float = 0.0, angle_deg: float = 0.0, scale: float = 1.0,
                      ox: float = 0.0, oy: float = 0.0) -> np.ndarray:
    """(N, 2) points rotated by angle_deg (clockwise on screen, y down) and scaled about (ox, oy), then moved."""
    p = np.asarray(pts, float).reshape(-1, 2)
    a = math.radians(angle_deg)
    c, s = math.cos(a) * scale, math.sin(a) * scale
    x, y = p[:, 0] - ox, p[:, 1] - oy
    return np.column_stack([ox + dx + c * x - s * y, oy + dy + s * x + c * y])


def rect(x: float, y: float, w: float, h: float) -> Polygon:
    return Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def circle(cx: float, cy: float, r: float) -> Ellipse:
    return Ellipse(cx, cy, r, r)


def rotated_rect(cx: float, cy: float, length: float, width: float, angle_deg: float, anchor: str = "center") -> Polygon:
    """Rectangle of `length` along angle_deg (0 = +x, 90 = +y/down).

    anchor='start' puts one short edge centred on (cx, cy) and extends outwards.
    """
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    if anchor == "start":
        s0, s1 = 0.0, length
    else:
        s0, s1 = -length / 2, length / 2
    hw = width / 2
    pts = []
    for s, t in ((s0, -hw), (s1, -hw), (s1, hw), (s0, hw)):
        pts.append((cx + ux * s + vx * t, cy + uy * s + vy * t))
    return Polygon(pts)


def shape_from_dict(d: dict) -> Shape:
    t = d.get("type")
    if t == "polygon":
        return Polygon([tuple(p) for p in d["points"]])
    if t in ("ellipse", "circle"):
        if t == "circle":
            return Ellipse(d["cx"], d["cy"], d["r"], d["r"])
        return Ellipse(d["cx"], d["cy"], d["rx"], d["ry"])
    if t == "rect":
        return rect(d["x"], d["y"], d["w"], d["h"])
    raise ValueError(f"Unknown shape type: {t!r}")


def _distance_to_polyline(x: np.ndarray, y: np.ndarray, poly: np.ndarray, closed: bool) -> np.ndarray:
    x, y = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float))
    if x.size > 4096:  # bound the (points × edges) temporaries
        fx, fy = x.ravel(), y.ravel()
        out = np.concatenate([_distance_to_polyline_chunk(fx[i:i + 4096], fy[i:i + 4096], poly, closed)
                              for i in range(0, fx.size, 4096)])
        return out.reshape(x.shape)
    return _distance_to_polyline_chunk(x, y, poly, closed)


def _distance_to_polyline_chunk(x: np.ndarray, y: np.ndarray, poly: np.ndarray, closed: bool) -> np.ndarray:
    pts = np.asarray(poly, float)
    if closed:
        a = pts
        b = np.roll(pts, -1, axis=0)
    else:
        a = pts[:-1]
        b = pts[1:]
    px = np.asarray(x, float)[..., None]
    py = np.asarray(y, float)[..., None]
    abx = b[:, 0] - a[:, 0]
    aby = b[:, 1] - a[:, 1]
    denom = abx**2 + aby**2
    denom = np.where(denom == 0, 1e-12, denom)
    t = np.clip(((px - a[:, 0]) * abx + (py - a[:, 1]) * aby) / denom, 0, 1)
    dx = px - (a[:, 0] + t * abx)
    dy = py - (a[:, 1] + t * aby)
    return np.sqrt(dx**2 + dy**2).min(axis=-1)


def segments_intersect(p1, p2, q1, q2) -> np.ndarray:
    """Vectorised test whether segments p1->p2 intersect the single segment q1->q2.

    p1, p2: (N, 2) arrays; q1, q2: length-2 sequences. Returns (N,) bool array and
    the sign of the crossing (+1 / -1 relative to q direction) as an int array.
    """
    p1 = np.asarray(p1, float)
    p2 = np.asarray(p2, float)
    qx, qy = q2[0] - q1[0], q2[1] - q1[1]

    def side(px, py):
        # points exactly on the line count as being on the positive side so a crossing that
        # touches the line is counted exactly once
        return np.where(qx * (py - q1[1]) - qy * (px - q1[0]) >= 0, 1.0, -1.0)

    s1 = side(p1[:, 0], p1[:, 1])
    s2 = side(p2[:, 0], p2[:, 1])
    rx = p2[:, 0] - p1[:, 0]
    ry = p2[:, 1] - p1[:, 1]
    t1 = np.sign(rx * (q1[1] - p1[:, 1]) - ry * (q1[0] - p1[:, 0]))
    t2 = np.sign(rx * (q2[1] - p1[:, 1]) - ry * (q2[0] - p1[:, 0]))
    hit = (s1 * s2 < 0) & (t1 * t2 <= 0)
    hit &= np.isfinite(p1).all(axis=1) & np.isfinite(p2).all(axis=1)
    return hit, np.where(hit, s2.astype(int), 0)


# --------------------------------------------------------------------------- grids
def annular_sector(cx: float, cy: float, r0: float, r1: float, a0: float, a1: float, n: int | None = None,
                   ry_ratio: float = 1.0) -> Polygon:
    """Sector of an annulus between radii r0..r1 and angles a0..a1 (degrees, 0 = +x, clockwise since y is down).

    A full ring (a1 - a0 >= 360 and r0 > 0) is a 'keyhole' polygon whose two seam edges coincide, which the
    even-odd point-in-polygon test and polygon filling treat as a ring with a hole.
    """
    span = a1 - a0
    if n is None:
        n = max(4, int(math.ceil(abs(span) / 5)) + 1)
    ts = np.linspace(math.radians(a0), math.radians(a1), n)
    outer = [(cx + r1 * math.cos(t), cy + r1 * ry_ratio * math.sin(t)) for t in ts]
    if r0 <= 0:
        if abs(span) >= 360 - 1e-9:
            return Polygon(outer[:-1])
        return Polygon([(cx, cy)] + outer)
    inner = [(cx + r0 * math.cos(t), cy + r0 * ry_ratio * math.sin(t)) for t in ts[::-1]]
    return Polygon(outer + inner)


def clip_convex(shape: Shape, clip: Shape) -> Polygon | None:
    """Intersection of a convex shape with a convex clip shape (None if empty or the clip is not convex)."""
    a = np.asarray(shape.polygon(96), np.float32).reshape(-1, 1, 2)
    b = np.asarray(clip.polygon(96), np.float32).reshape(-1, 1, 2)
    if not cv2.isContourConvex(b.astype(np.float32)):
        hull = cv2.convexHull(b)
        if abs(cv2.contourArea(hull) - cv2.contourArea(b)) > 1e-3 * max(cv2.contourArea(hull), 1.0):
            return None
        b = hull
    area, inter = cv2.intersectConvexConvex(a, b)
    if inter is None or area <= 1e-6:
        return None
    return Polygon([(float(x), float(y)) for x, y in inter.reshape(-1, 2)])


def square_grid(x: float, y: float, w: float, h: float, nx: int, ny: int) -> list[list[Polygon]]:
    """nx × ny rectangular cells covering the box; returned as rows (top to bottom) of cells (left to right)."""
    nx, ny = max(1, int(nx)), max(1, int(ny))
    cw, ch = w / nx, h / ny
    return [[rect(x + i * cw, y + j * ch, cw, ch) for i in range(nx)] for j in range(ny)]


def concentric_rings(cx: float, cy: float, r: float, n: int, ry: float | None = None,
                     radii: list[float] | None = None) -> list[Polygon]:
    """n rings of equal width from the centre outwards (the first is a disc); optional explicit outer radii."""
    ratio = (ry / r) if ry and r else 1.0
    edges = [0.0] + (sorted(radii) if radii else [r * (i + 1) / max(1, int(n)) for i in range(max(1, int(n)))])
    return [annular_sector(cx, cy, edges[i], edges[i + 1], 0, 360, 73, ratio) for i in range(len(edges) - 1)]


def radial_sectors(cx: float, cy: float, r: float, n: int, start_deg: float = -90.0, r_inner: float = 0.0,
                   ry: float | None = None) -> list[Polygon]:
    """n equal pie sectors starting at start_deg (default: top, going clockwise)."""
    n = max(1, int(n))
    ratio = (ry / r) if ry and r else 1.0
    step = 360.0 / n
    return [annular_sector(cx, cy, r_inner * 1.0, r, start_deg + i * step, start_deg + (i + 1) * step,
                           ry_ratio=ratio) for i in range(n)]


# --------------------------------------------------------------------------- body ellipse
_UNIT_DISC = None


def _unit_disc_samples() -> np.ndarray:
    """Fixed sample points (≈ 37) evenly covering the unit disc."""
    global _UNIT_DISC
    if _UNIT_DISC is None:
        pts = [(0.0, 0.0)]
        for ring, k in ((1 / 3, 6), (2 / 3, 12), (0.95, 18)):
            for i in range(k):
                a = 2 * math.pi * (i + 0.5 * (ring > 0.5)) / k
                pts.append((ring * math.cos(a), ring * math.sin(a)))
        _UNIT_DISC = np.asarray(pts)
    return _UNIT_DISC


def body_fraction_inside(shape: Shape, x, y, angle_deg, a, b) -> np.ndarray:
    """Fraction of an elliptical body (centre x, y; semi-axes a along angle_deg, b across) inside the shape.

    All arguments are per-frame arrays (pixels); frames with missing values give NaN.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    ang = np.radians(np.nan_to_num(np.asarray(angle_deg, float) * np.ones_like(x)))
    a = np.asarray(a, float) * np.ones_like(x)
    b = np.asarray(b, float) * np.ones_like(x)
    s = _unit_disc_samples()
    ca, sa = np.cos(ang)[:, None], np.sin(ang)[:, None]
    u = s[None, :, 0] * a[:, None]
    v = s[None, :, 1] * b[:, None]
    px = x[:, None] + u * ca - v * sa
    py = y[:, None] + u * sa + v * ca
    inside = shape.contains(px, py)
    frac = inside.mean(axis=1)
    bad = ~(np.isfinite(x) & np.isfinite(y) & np.isfinite(a) & np.isfinite(b))
    frac = frac.astype(float)
    frac[bad] = np.nan
    return frac
