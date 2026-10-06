"""Apparatus definitions: arena boundary, zones, points, lines, zone groups and calibration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geometry import Polygon, Shape, shape_from_dict


@dataclass
class Zone:
    name: str
    shape: Shape
    color: str = "#3b82f6"

    def to_dict(self):
        return {"name": self.name, "shape": self.shape.to_dict(), "color": self.color}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], shape_from_dict(d["shape"]), d.get("color", "#3b82f6"))


@dataclass
class PointOfInterest:
    """A point (e.g. an object in novel object recognition, or a hidden platform centre)."""

    name: str
    x: float
    y: float
    radius_cm: float = 2.0  # used for "near point" / object-exploration measures
    color: str = "#f59e0b"

    def to_dict(self):
        return {"name": self.name, "x": self.x, "y": self.y, "radius_cm": self.radius_cm, "color": self.color}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d["x"], d["y"], d.get("radius_cm", 2.0), d.get("color", "#f59e0b"))


@dataclass
class Line:
    """A line; the analysis counts crossings in each direction."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    color: str = "#10b981"

    def to_dict(self):
        return {"name": self.name, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2, "color": self.color}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d["x1"], d["y1"], d["x2"], d["y2"], d.get("color", "#10b981"))


@dataclass
class ZoneGroup:
    """A named union of zones minus an optional set of excluded zones.

    e.g. "Open arms" = North arm + South arm, or "Periphery" = Arena - Centre.
    """

    name: str
    zones: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"name": self.name, "zones": list(self.zones), "exclude": list(self.exclude)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], list(d.get("zones", [])), list(d.get("exclude", [])))


@dataclass
class Apparatus:
    name: str = "Apparatus"
    arena: Shape | None = None
    zones: list[Zone] = field(default_factory=list)
    points: list[PointOfInterest] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    groups: list[ZoneGroup] = field(default_factory=list)
    px_per_cm: float | None = None  # calibration; None → results in pixels
    calibration_line: tuple[float, float, float, float] | None = None
    calibration_length_cm: float | None = None
    template: str = "custom"
    # Optional reference image (frame) size the coordinates refer to
    frame_size: tuple[int, int] | None = None  # (width, height)

    # ---- calibration -------------------------------------------------
    def calibrate(self, x1, y1, x2, y2, length_cm: float):
        px = math.hypot(x2 - x1, y2 - y1)
        if px <= 0 or length_cm <= 0:
            raise ValueError("Calibration line and length must be positive")
        self.px_per_cm = px / length_cm
        self.calibration_line = (x1, y1, x2, y2)
        self.calibration_length_cm = length_cm

    @property
    def unit(self) -> str:
        return "cm" if self.px_per_cm else "px"

    @property
    def scale(self) -> float:
        """Multiply pixels by this to obtain output units."""
        return 1.0 / self.px_per_cm if self.px_per_cm else 1.0

    # ---- lookup --------------------------------------------------------
    def zone(self, name: str) -> Zone | None:
        return next((z for z in self.zones if z.name == name), None)

    def point(self, name: str) -> PointOfInterest | None:
        return next((p for p in self.points if p.name == name), None)

    def group(self, name: str) -> ZoneGroup | None:
        return next((g for g in self.groups if g.name == name), None)

    def zone_membership(self, x, y) -> dict[str, np.ndarray]:
        """Boolean in-zone arrays for every zone and zone group."""
        out = {z.name: z.shape.contains(x, y) for z in self.zones}
        for g in self.groups:
            m = np.zeros(np.shape(x), bool)
            for zn in g.zones:
                if zn in out:
                    m |= out[zn]
            for zn in g.exclude:
                if zn in out:
                    m &= ~out[zn]
            out[g.name] = m
        return out

    def arena_or_bounds(self) -> Shape:
        if self.arena is not None:
            return self.arena
        if self.zones:
            pts = np.vstack([z.shape.polygon() for z in self.zones])
            x0, y0 = pts.min(axis=0)
            x1, y1 = pts.max(axis=0)
            return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        if self.frame_size:
            w, h = self.frame_size
            return Polygon([(0, 0), (w, 0), (w, h), (0, h)])
        raise ValueError("Apparatus has no arena, zones or frame size")

    # ---- serialisation ------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "arena": self.arena.to_dict() if self.arena else None,
            "zones": [z.to_dict() for z in self.zones],
            "points": [p.to_dict() for p in self.points],
            "lines": [l.to_dict() for l in self.lines],
            "groups": [g.to_dict() for g in self.groups],
            "px_per_cm": self.px_per_cm,
            "calibration_line": list(self.calibration_line) if self.calibration_line else None,
            "calibration_length_cm": self.calibration_length_cm,
            "template": self.template,
            "frame_size": list(self.frame_size) if self.frame_size else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Apparatus":
        return cls(
            name=d.get("name", "Apparatus"),
            arena=shape_from_dict(d["arena"]) if d.get("arena") else None,
            zones=[Zone.from_dict(z) for z in d.get("zones", [])],
            points=[PointOfInterest.from_dict(p) for p in d.get("points", [])],
            lines=[Line.from_dict(l) for l in d.get("lines", [])],
            groups=[ZoneGroup.from_dict(g) for g in d.get("groups", [])],
            px_per_cm=d.get("px_per_cm"),
            calibration_line=tuple(d["calibration_line"]) if d.get("calibration_line") else None,
            calibration_length_cm=d.get("calibration_length_cm"),
            template=d.get("template", "custom"),
            frame_size=tuple(d["frame_size"]) if d.get("frame_size") else None,
        )

    def copy(self) -> "Apparatus":
        return Apparatus.from_dict(self.to_dict())
