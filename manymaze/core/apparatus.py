"""Apparatus definitions: arena boundary, zones, points, lines, zone groups and calibration."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .atomicfile import write_text_atomic
from .geometry import (Ellipse, Polygon, Shape, clip_convex, concentric_rings, radial_sectors, shape_from_dict,
                       similarity_points, square_grid)

# Test.zone_overrides key holding the position of the whole apparatus map in that test (the camera or the
# apparatus moved between recordings): {"dx", "dy" (px), "angle" (degrees, clockwise), "scale"}, about the centre
# of the arena.
POSITION_KEY = "@position"
# Test.zone_overrides key holding the calibration of that test, when it differs from the apparatus map's (e.g. it was
# adjusted while the live test ran): {"px_per_cm", "calibration_line" ([x1, y1, x2, y2] in video px or None),
# "calibration_length_cm"}. Applied after POSITION_KEY: it is the scale of the test's own video.
CALIBRATION_KEY = "@calibration"
OVERRIDE_KEYS = (POSITION_KEY, CALIBRATION_KEY)  # zone_overrides keys that are not zone / point names

# The unit distances are reported in (ANY-maze 7.36 / 7.37: metres, centimetres or millimetres), per apparatus
# (Apparatus.distance_unit): units per centimetre. The calibration (px_per_cm), the distance settings (mobility
# threshold, thigmotaxis band, investigation distance, point radius …, procedures) and the analysis itself stay in
# centimetres; the results, charts and exports are converted when they are reported (to_report_units). Uncalibrated
# apparatus report pixels whatever the unit.
DISTANCE_UNITS = {"mm": 10.0, "cm": 1.0, "m": 0.01}
# units in centimetres at the end of a result name, and the power of the length in them
_CM_UNITS = {"cm": 1, "cm/s": 1, "cm/s²": 1, "cm²": 2, "cm·s": 1, "deg/cm": -1}
_UNIT_AT_END = re.compile(r"\((cm/s²|cm/s|cm²|cm·s|cm|deg/cm)\)$")


_DISTANCE_UNIT_AT_END = re.compile(r"\((deg/)?(mm|cm|m)(/s²|/s|²|·s)?\)$")


def rename_unit(name: str, old: str, new: str) -> str:
    """A result name with its distance unit `old` changed to `new` ("Total distance (cm)" → "Total distance (m)",
    "Meander (deg/cm)" → "Meander (deg/m)"); other names unchanged."""
    m = _DISTANCE_UNIT_AT_END.search(name)
    if m is None or m.group(2) != old or old == new or (m.group(1) and m.group(3)):
        return name
    return f"{name[:m.start(2)]}{new}{name[m.end(2):]}"


def unit_conversion(unit_text: str, unit: str) -> tuple[str, float] | None:
    """A unit in centimetres ("cm", "cm/s", "cm/s²", "cm²", "cm·s", "deg/cm") in another distance unit: (its text,
    the factor its values are multiplied by), e.g. ("m/s", 0.01); None for other units, or unit "cm"."""
    power = _CM_UNITS.get(unit_text)
    if power is None or unit == "cm" or unit not in DISTANCE_UNITS:
        return None
    return unit_text.replace("cm", unit), DISTANCE_UNITS[unit] ** power


def report_value(v, factor: float):
    """A result value times a unit factor (text and missing values unchanged)."""
    if factor == 1.0 or isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        return v
    x = float(v) * factor
    return round(x, 9) if math.isfinite(x) else x


def to_report_units(res: dict, unit: str) -> dict:
    """Results {name: value} whose names end with a unit in centimetres ("Total distance (cm)", "Mean speed
    (cm/s)", "Meander (deg/cm)" …) with that unit and their values in `unit` ("mm" | "m"); `res` itself with "cm",
    "px" or nothing to convert (so names and values in cm are exactly as before)."""
    if unit == "cm" or unit not in DISTANCE_UNITS:
        return res
    out = type(res)()
    for name, v in res.items():
        m = _UNIT_AT_END.search(name) if isinstance(name, str) else None
        conv = unit_conversion(m.group(1), unit) if m else None
        if conv:
            name, v = f"{name[:m.start(1)]}{conv[0]})", report_value(v, conv[1])
        out[name] = v
    return out

ENTRY_RULES = {
    "": "Default (analysis settings)",
    "centre": "Centre of the animal",
    "head": "Head",
    "tail": "Tail base",
    "body": "Proportion of the body",
    "exclusion": "Not in any other zone",
}


def from_known(cls, d: dict):
    """A dataclass instance from a dict, ignoring keys that are not fields (e.g. written by a newer version)."""
    return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def unique_name(name: str, taken) -> str:
    """`name` (stripped; "Unnamed" if blank), or "name 2", "name 3"… if it is already taken."""
    taken = set(taken)
    name = name.strip() or "Unnamed"
    if name not in taken:
        return name
    i = 2
    while f"{name} {i}" in taken:
        i += 1
    return f"{name} {i}"


@dataclass
class Zone:
    """A zone (an 'area' in ANY-maze terms; zone groups combine several areas).

    hidden: the animal cannot be seen inside it (nest box, tunnel); undetected frames after the animal was last
    seen in or near it count as time in this zone. investigation_distance_cm: if > 0 the animal is in the zone
    when its head is within this distance of the zone (object investigation). moveable: the position may differ
    per test (Test.zone_overrides). entry_rule: body part / rule deciding occupancy ("" = analysis default).
    entry_orientation_deg: if > 0, a visit only starts once the animal is oriented towards the zone (body
    orientation within this many degrees of the direction from its centre to the zone centre); 0 = off.
    whishaw_width_cm: if > 0, the width (units) of the zone's Whishaw's corridor - a band from the animal's start
    position to the zone centre - for the time / distance in Whishaw's corridor measures; 0 = none.
    """

    name: str
    shape: Shape
    color: str = "#3b82f6"
    hidden: bool = False
    investigation_distance_cm: float = 0.0
    moveable: bool = False
    entry_rule: str = ""
    body_fraction: float = 0.8
    entry_orientation_deg: float = 0.0
    whishaw_width_cm: float = 0.0

    def to_dict(self):
        d = {"name": self.name, "shape": self.shape.to_dict(), "color": self.color}
        for k, default in (("hidden", False), ("investigation_distance_cm", 0.0), ("moveable", False),
                           ("entry_rule", ""), ("body_fraction", 0.8), ("entry_orientation_deg", 0.0),
                           ("whishaw_width_cm", 0.0)):
            v = getattr(self, k)
            if v != default:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], shape_from_dict(d["shape"]), d.get("color", "#3b82f6"), bool(d.get("hidden", False)),
                   float(d.get("investigation_distance_cm", 0.0) or 0.0), bool(d.get("moveable", False)),
                   d.get("entry_rule", "") or "", float(d.get("body_fraction", 0.8)),
                   float(d.get("entry_orientation_deg", 0.0) or 0.0), float(d.get("whishaw_width_cm", 0.0) or 0.0))


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
        return from_known(cls, d)


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
        return from_known(cls, d)


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
class Sequence:
    """A movement pattern through zones, e.g. A → B → C.

    steps: zone or zone-group names, in order. from_start: the pattern must begin at the first step (otherwise
    any rotation of a cyclic pattern counts, e.g. ABC, BCA, CAB). allow_other: entering other zones between
    steps does not break the sequence. bidirectional: the reverse order also counts. end: "entry" (complete on
    entering the last step) or "exit" (on leaving it). max_duration_s: attempts longer than this fail (0 = no
    limit). overlap: sequences may overlap (sliding window, e.g. ABCA contains ABC and BCA).
    """

    name: str
    steps: list[str] = field(default_factory=list)
    from_start: bool = True
    allow_other: bool = True
    bidirectional: bool = False
    end: str = "entry"
    max_duration_s: float = 0.0
    overlap: bool = False

    def to_dict(self):
        return {"name": self.name, "steps": list(self.steps), "from_start": self.from_start,
                "allow_other": self.allow_other, "bidirectional": self.bidirectional, "end": self.end,
                "max_duration_s": self.max_duration_s, "overlap": self.overlap}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], list(d.get("steps", [])), bool(d.get("from_start", True)),
                   bool(d.get("allow_other", True)), bool(d.get("bidirectional", False)), d.get("end", "entry"),
                   float(d.get("max_duration_s", 0.0) or 0.0), bool(d.get("overlap", False)))


@dataclass
class Grid:
    """A regular grid of zones made by `make_grid` (square cells, concentric rings, radial sectors or both).

    Grid crossings and cells visited are computed from transitions between its zones.
    """

    name: str
    kind: str = "square"  # square | rings | sectors | polar
    zones: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def to_dict(self):
        return {"name": self.name, "kind": self.kind, "zones": list(self.zones), "params": dict(self.params)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], d.get("kind", "square"), list(d.get("zones", [])), dict(d.get("params", {})))


@dataclass
class Apparatus:
    name: str = "Apparatus"
    arena: Shape | None = None
    zones: list[Zone] = field(default_factory=list)
    points: list[PointOfInterest] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    groups: list[ZoneGroup] = field(default_factory=list)
    sequences: list[Sequence] = field(default_factory=list)
    grids: list[Grid] = field(default_factory=list)
    px_per_cm: float | None = None  # calibration; None → results in pixels
    calibration_line: tuple[float, float, float, float] | None = None
    calibration_length_cm: float | None = None
    template: str = "custom"
    # Optional reference image (frame) size the coordinates refer to
    frame_size: tuple[int, int] | None = None  # (width, height)
    distance_unit: str = "cm"  # the unit distances are reported in: "mm" | "cm" | "m" (DISTANCE_UNITS)

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

    @property
    def report_unit(self) -> str:
        """The unit distances are reported in: distance_unit when calibrated, else "px"."""
        return self.distance_unit if self.px_per_cm and self.distance_unit in DISTANCE_UNITS else self.unit

    def length_text(self, cm: float) -> str:
        """A length in centimetres (e.g. the calibration ruler's) as shown, in the distance unit: "200 mm"."""
        u = self.distance_unit if self.distance_unit in DISTANCE_UNITS else "cm"
        return f"{round(cm * DISTANCE_UNITS[u], 6):g} {u}"

    @property
    def report_factor(self) -> float:
        """Reported units per unit of the analysis (cm, or px when not calibrated)."""
        return DISTANCE_UNITS[self.report_unit] if self.px_per_cm and self.report_unit in DISTANCE_UNITS else 1.0

    # ---- lookup --------------------------------------------------------
    def zone(self, name: str) -> Zone | None:
        return next((z for z in self.zones if z.name == name), None)

    def point(self, name: str) -> PointOfInterest | None:
        return next((p for p in self.points if p.name == name), None)

    def group(self, name: str) -> ZoneGroup | None:
        return next((g for g in self.groups if g.name == name), None)

    def sequence(self, name: str) -> Sequence | None:
        return next((q for q in self.sequences if q.name == name), None)

    def grid(self, name: str) -> Grid | None:
        return next((g for g in self.grids if g.name == name), None)

    # ---- editing ---------------------------------------------------------
    def rename(self, kind: str, index: int, new_name: str) -> str:
        """Rename the index-th zone / point / line / group / sequence, updating the zone groups, sequences and grids
        that refer to it. Zones and groups share one namespace. Returns the name given (made unique), or the old
        name if `new_name` is blank or unchanged."""
        items = getattr(self, kind + "s")
        old, name = items[index].name, new_name.strip()
        if not name or name == old:
            return old
        taken = [x.name for i, x in enumerate(items) if i != index]
        if kind == "zone":
            taken += [g.name for g in self.groups]
        elif kind == "group":
            taken += [z.name for z in self.zones]
        new = items[index].name = unique_name(name, taken)

        def ren(names):
            return [new if n == old else n for n in names]

        if kind == "zone":
            for g in self.groups:
                g.zones, g.exclude = ren(g.zones), ren(g.exclude)
        if kind in ("zone", "group"):
            for q in self.sequences:
                q.steps = ren(q.steps)
            for gr in self.grids:
                gr.zones = ren(gr.zones)
        return new

    def remove(self, kind: str, index: int = 0):
        """Delete the arena or the index-th zone / point / line / group / sequence, and the references to it (a grid
        left without zones is removed)."""
        if kind == "arena":
            self.arena = None
            return
        name = getattr(self, kind + "s").pop(index).name

        def drop(names):
            return [n for n in names if n != name]

        if kind == "zone":
            for g in self.groups:
                g.zones, g.exclude = drop(g.zones), drop(g.exclude)
            for gr in self.grids:
                gr.zones = drop(gr.zones)
            self.grids = [gr for gr in self.grids if gr.zones]
        if kind in ("zone", "group"):
            for q in self.sequences:
                q.steps = drop(q.steps)

    def zone_membership(self, x, y) -> dict[str, np.ndarray]:
        """Boolean in-zone arrays for every zone and zone group."""
        out = {z.name: z.shape.contains(x, y) for z in self.zones}
        return self.combine_groups(out, np.shape(x))

    def combine_groups(self, out: dict, shape=None) -> dict[str, np.ndarray]:
        """Add zone-group membership (union of zones minus excluded zones) to a zone → bool array dict."""
        if shape is None:
            shape = np.shape(next(iter(out.values()))) if out else (0,)
        for g in self.groups:
            m = np.zeros(shape, bool)
            for zn in g.zones:
                if zn in out:
                    m |= out[zn]
            for zn in g.exclude:
                if zn in out:
                    m &= ~out[zn]
            out[g.name] = m
        return out

    def names(self) -> list[str]:
        """Zone and zone-group names (the targets of sequences, periods and procedures)."""
        return [z.name for z in self.zones] + [g.name for g in self.groups]

    def origin(self) -> tuple[float, float]:
        """Centre about which the apparatus map is rotated and scaled (the arena's centre)."""
        try:
            x0, y0, x1, y1 = self.arena_or_bounds().bounds()
            return (x0 + x1) / 2, (y0 + y1) / 2
        except ValueError:
            return 0.0, 0.0

    def positioned(self, dx: float = 0.0, dy: float = 0.0, angle: float = 0.0, scale: float = 1.0) -> "Apparatus":
        """Copy of the whole map moved by (dx, dy) px, rotated by ``angle`` degrees (clockwise) and scaled about
        the arena centre. The calibration follows the scale, so distances in cm are unchanged."""
        scale = float(scale) if scale and scale > 0 else 1.0
        if not (dx or dy or angle) and scale == 1.0:
            return self
        ox, oy = self.origin()
        kw = dict(dx=dx, dy=dy, angle_deg=angle, scale=scale, ox=ox, oy=oy)
        app = self.copy()
        if app.arena is not None:
            app.arena = app.arena.similarity(**kw)
        for z in app.zones:
            z.shape = z.shape.similarity(**kw)
        for pt in app.points:
            (pt.x, pt.y), = similarity_points([[pt.x, pt.y]], **kw).tolist()
        for ln in app.lines:
            (ln.x1, ln.y1), (ln.x2, ln.y2) = similarity_points([[ln.x1, ln.y1], [ln.x2, ln.y2]], **kw).tolist()
        if app.calibration_line:
            a, b = similarity_points(np.reshape(app.calibration_line, (2, 2)), **kw).tolist()
            app.calibration_line = (*a, *b)
        if app.px_per_cm:
            app.px_per_cm *= scale
        return app

    def with_overrides(self, overrides: dict | None) -> "Apparatus":
        """Copy with the per-test map position and positions of moveable zones / points applied.

        overrides: {zone name: shape dict} or {point name: {"x", "y"}}, plus optionally POSITION_KEY: {"dx",
        "dy", "angle", "scale"} for the whole map (applied first; zone and point positions are in video pixels) and
        CALIBRATION_KEY: the test's own calibration (see calibration_override).
        Points lying inside a moved zone (e.g. "Platform centre" in "Platform") move with it.
        """
        if not overrides:
            return self
        pos = overrides.get(POSITION_KEY)
        app = self.positioned(**position_args(pos)) if isinstance(pos, dict) else self
        app = app.copy() if app is self else app
        cal = overrides.get(CALIBRATION_KEY)
        if isinstance(cal, dict) and cal.get("px_per_cm") and float(cal["px_per_cm"]) > 0:
            app.px_per_cm = float(cal["px_per_cm"])
            line = cal.get("calibration_line")
            app.calibration_line = tuple(float(v) for v in line) if line else None
            length = cal.get("calibration_length_cm")
            app.calibration_length_cm = float(length) if length else None
        for name, v in overrides.items():
            if not isinstance(v, dict) or name in OVERRIDE_KEYS:
                continue
            z = app.zone(name)
            if z is not None and "type" in v:
                old = z.shape
                z.shape = shape_from_dict(v)
                (ox, oy), (nx, ny) = old.centroid(), z.shape.centroid()
                for p in app.points:
                    if p.name not in overrides and bool(old.contains(p.x, p.y)):
                        p.x, p.y = p.x + nx - ox, p.y + ny - oy
                for o in app.zones:
                    if o is not z and o.moveable and o.name not in overrides and \
                            bool(old.contains(*o.shape.centroid())):
                        o.shape = o.shape.translated(nx - ox, ny - oy)
                continue
            p = app.point(name)
            if p is not None and "x" in v and "y" in v:
                p.x, p.y = float(v["x"]), float(v["y"])
        return app

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
            "sequences": [q.to_dict() for q in self.sequences],
            "grids": [g.to_dict() for g in self.grids],
            "px_per_cm": self.px_per_cm,
            "calibration_line": list(self.calibration_line) if self.calibration_line else None,
            "calibration_length_cm": self.calibration_length_cm,
            "template": self.template,
            "frame_size": list(self.frame_size) if self.frame_size else None,
            # only when not cm: apparatus maps in cm are saved as before
            **({"distance_unit": self.distance_unit} if self.distance_unit != "cm" else {}),
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
            sequences=[Sequence.from_dict(q) for q in d.get("sequences", [])],
            grids=[Grid.from_dict(g) for g in d.get("grids", [])],
            px_per_cm=d.get("px_per_cm"),
            calibration_line=tuple(d["calibration_line"]) if d.get("calibration_line") else None,
            calibration_length_cm=d.get("calibration_length_cm"),
            template=d.get("template", "custom"),
            frame_size=tuple(d["frame_size"]) if d.get("frame_size") else None,
            distance_unit=d.get("distance_unit") if d.get("distance_unit") in DISTANCE_UNITS else "cm",
        )

    def copy(self) -> "Apparatus":
        return Apparatus.from_dict(self.to_dict())


def calibration_override(px_per_cm: float, line=None, length_cm: float | None = None) -> dict:
    """The CALIBRATION_KEY value of Test.zone_overrides for a test-specific calibration."""
    if not px_per_cm or px_per_cm <= 0:
        raise ValueError("The calibration must be a positive number of pixels per cm")
    return {"px_per_cm": float(px_per_cm), "calibration_line": [float(v) for v in line] if line else None,
            "calibration_length_cm": float(length_cm) if length_cm else None}


def position_args(pos: dict | None) -> dict:
    """Keyword arguments of Apparatus.positioned from a stored POSITION_KEY value."""
    pos = pos or {}
    return {k: float(pos.get(k, d) or d) for k, d in (("dx", 0.0), ("dy", 0.0), ("angle", 0.0), ("scale", 1.0))}


# ------------------------------------------------------------------------------- grids
GRID_KINDS = {"square": "Square cells", "rings": "Concentric rings", "sectors": "Radial sectors",
              "polar": "Rings × sectors"}
ROWS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _row_label(i: int) -> str:
    return ROWS[i] if i < len(ROWS) else ROWS[i // len(ROWS) - 1] + ROWS[i % len(ROWS)]


def grid_shapes(kind: str, region: Shape, nx: int = 4, ny: int = 4, cell_cm: float = 0.0,
                px_per_cm: float | None = None, rings: int = 3, sectors: int = 8, start_deg: float = -90.0,
                clip: bool = True) -> list[tuple[str, Shape]]:
    """Systematically named grid cells covering a region.

    square: nx × ny cells named A1, A2 … (rows top to bottom, columns left to right); with cell_cm > 0 and a
    calibration the cell count follows from the real cell size. rings: concentric rings 1 (centre) … n.
    sectors: pie sectors 1 … n clockwise from start_deg (-90 = top). polar: rings × sectors ("R1 S1" …).
    Square cells are clipped to the region when it is convex (e.g. a circular arena).
    """
    x0, y0, x1, y1 = region.bounds()
    w, h = x1 - x0, y1 - y0
    out: list[tuple[str, Shape]] = []
    if kind == "square":
        if cell_cm and cell_cm > 0 and px_per_cm:
            step = cell_cm * px_per_cm
            nx, ny = max(1, int(round(w / step))), max(1, int(round(h / step)))
            w, h = nx * step, ny * step
            x0, y0 = (x0 + x1 - w) / 2, (y0 + y1 - h) / 2
        rect_region = isinstance(region, Polygon) and len(region.points) == 4 and \
            abs(region.area() - (x1 - x0) * (y1 - y0)) < 1e-6 * max(1.0, region.area())
        for j, row in enumerate(square_grid(x0, y0, w, h, nx, ny)):
            for i, cell in enumerate(row):
                shp = cell
                if clip and not rect_region:
                    shp = clip_convex(cell, region)
                    if shp is None or shp.area() < 0.02 * cell.area():
                        continue
                out.append((f"{_row_label(j)}{i + 1}", shp))
        return out
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if isinstance(region, Ellipse):
        cx, cy = region.cx, region.cy
    rx, ry = w / 2, h / 2
    if kind == "rings":
        return [(f"Ring {i + 1}", s) for i, s in enumerate(concentric_rings(cx, cy, rx, rings, ry))]
    if kind == "sectors":
        return [(f"Sector {i + 1}", s) for i, s in enumerate(radial_sectors(cx, cy, rx, sectors, start_deg, 0, ry))]
    if kind == "polar":
        from .geometry import annular_sector

        step = 360.0 / max(1, sectors)
        for r in range(max(1, rings)):
            r0, r1 = rx * r / rings, rx * (r + 1) / rings
            for k in range(max(1, sectors)):
                if r == 0 and rings > 1:
                    if k == 0:
                        out.append(("R1", annular_sector(cx, cy, 0, r1, 0, 360, 73, ry / rx if rx else 1)))
                    continue
                out.append((f"R{r + 1} S{k + 1}", annular_sector(cx, cy, r0, r1, start_deg + k * step,
                                                                 start_deg + (k + 1) * step, ry_ratio=ry / rx if rx else 1)))
        return out
    raise ValueError(f"Unknown grid kind: {kind!r}")


def make_grid(app: Apparatus, kind: str = "square", region: Shape | None = None, name: str = "Grid",
              colors: list[str] | None = None, group: bool = True, **params) -> Grid:
    """Add a grid of zones to the apparatus (named "<name> <cell>") and record it in app.grids."""
    if region is None:
        region = app.arena_or_bounds()
    taken = set(app.names()) | {g.name for g in app.grids}
    base, i = name, 2
    while name in taken or any(n.startswith(name + " ") for n in taken if n in {z.name for z in app.zones}):
        name = f"{base} {i}"
        i += 1
    colors = colors or ["#64748b", "#94a3b8"]
    cells = grid_shapes(kind, region, px_per_cm=params.pop("px_per_cm", app.px_per_cm), **params)
    names = []
    for k, (label, shp) in enumerate(cells):
        zn = f"{name} {label}"
        app.zones.append(Zone(zn, shp, colors[k % len(colors)]))
        names.append(zn)
    g = Grid(name, kind, names, {k: v for k, v in params.items()})
    app.grids.append(g)
    if group:
        app.groups.append(ZoneGroup(name, list(names)))
    return g


def remove_grid(app: Apparatus, name: str) -> bool:
    g = app.grid(name)
    if g is None:
        return False
    cells = set(g.zones)
    app.zones = [z for z in app.zones if z.name not in cells]
    app.groups = [gr for gr in app.groups if gr.name != name]
    for gr in app.groups:
        gr.zones = [z for z in gr.zones if z not in cells]
        gr.exclude = [z for z in gr.exclude if z not in cells]
    for q in app.sequences:
        q.steps = [s for s in q.steps if s not in cells and s != name]
    app.grids.remove(g)
    return True


# ------------------------------------------------------------------------------- sharing
APPARATUS_FILE_FORMAT = "manymaze-apparatus"


def save_apparatus_file(apps: list[Apparatus], path) -> Path:
    """Save apparatus maps (zones, points, lines, groups, sequences, grids, calibration) to a JSON file to share
    them between experiments or labs."""
    path = Path(path)
    write_text_atomic(path, json.dumps({"format": APPARATUS_FILE_FORMAT, "version": 1,
                                        "apparatus": [a.to_dict() for a in apps]}, indent=1))
    return path


def load_apparatus_file(path) -> list[Apparatus]:
    """Apparatus maps from an apparatus file (save_apparatus_file) or from another experiment (its .mmaze folder
    or project.json)."""
    p = Path(path)
    if p.is_dir():
        p = p / "project.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict) or not isinstance(d.get("apparatus"), list):
        raise ValueError(f"{p.name} contains no apparatus")
    return [Apparatus.from_dict(a) for a in d["apparatus"]]
