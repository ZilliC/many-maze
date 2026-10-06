"""Built-in apparatus templates for standard behavioural tests.

Each builder receives the bounding box of the apparatus in the video (pixels)
and the real size (cm) so the apparatus can be auto-calibrated.  Every
template returns an :class:`Apparatus` with sensible zones/points/groups which
the user can then tweak in the apparatus editor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .apparatus import Apparatus, Line, PointOfInterest, Zone, ZoneGroup
from .geometry import Ellipse, Polygon, circle, rect, rotated_rect

PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16", "#06b6d4"]


def _calibrate_width(app: Apparatus, w_px: float, w_cm: float):
    if w_cm and w_cm > 0:
        app.px_per_cm = w_px / w_cm
        app.calibration_length_cm = w_cm


def _sector(cx, cy, r0, r1, a0, a1, n=24) -> Polygon:
    """Annular sector between radii r0..r1 and angles a0..a1 (degrees, y down)."""
    ts = np.linspace(math.radians(a0), math.radians(a1), n)
    outer = [(cx + r1 * math.cos(t), cy + r1 * math.sin(t)) for t in ts]
    if r0 <= 0:
        return Polygon([(cx, cy)] + outer)
    inner = [(cx + r0 * math.cos(t), cy + r0 * math.sin(t)) for t in ts[::-1]]
    return Polygon(outer + inner)


# --------------------------------------------------------------------------
def open_field(x, y, w, h, size_cm=40.0, centre_fraction=0.5, corners=True, circular=False) -> Apparatus:
    """Open field. centre_fraction is the side length of the centre relative to the arena."""
    app = Apparatus(name="Open field", template="open_field")
    if circular:
        cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
        app.arena = circle(cx, cy, r)
        app.zones.append(Zone("Arena", circle(cx, cy, r), "#64748b"))
        app.zones.append(Zone("Centre", circle(cx, cy, r * centre_fraction), PALETTE[0]))
    else:
        app.arena = rect(x, y, w, h)
        app.zones.append(Zone("Arena", rect(x, y, w, h), "#64748b"))
        cw, ch = w * centre_fraction, h * centre_fraction
        app.zones.append(Zone("Centre", rect(x + (w - cw) / 2, y + (h - ch) / 2, cw, ch), PALETTE[0]))
        if corners:
            cs_w, cs_h = w * (1 - centre_fraction) / 2, h * (1 - centre_fraction) / 2
            for i, (zx, zy) in enumerate([(x, y), (x + w - cs_w, y), (x, y + h - cs_h), (x + w - cs_w, y + h - cs_h)]):
                app.zones.append(Zone(f"Corner {i + 1}", rect(zx, zy, cs_w, cs_h), PALETTE[(i + 2) % len(PALETTE)]))
            app.groups.append(ZoneGroup("Corners", [f"Corner {i + 1}" for i in range(4)]))
    app.groups.append(ZoneGroup("Periphery", ["Arena"], ["Centre"]))
    _calibrate_width(app, w, size_cm)
    return app


def elevated_plus_maze(x, y, w, h, arm_length_cm=35.0, arm_width_cm=5.0, open_arms_horizontal=True) -> Apparatus:
    """Elevated plus maze fitted to the bounding box of the whole cross."""
    app = Apparatus(name="Elevated plus maze", template="epm")
    cx, cy = x + w / 2, y + h / 2
    total_cm = 2 * arm_length_cm + arm_width_cm
    sx, sy = w / total_cm, h / total_cm
    aw_x, aw_y = arm_width_cm * sx, arm_width_cm * sy
    centre = rect(cx - aw_x / 2, cy - aw_y / 2, aw_x, aw_y)
    north = rect(cx - aw_x / 2, y, aw_x, cy - aw_y / 2 - y)
    south = rect(cx - aw_x / 2, cy + aw_y / 2, aw_x, y + h - (cy + aw_y / 2))
    west = rect(x, cy - aw_y / 2, cx - aw_x / 2 - x, aw_y)
    east = rect(cx + aw_x / 2, cy - aw_y / 2, x + w - (cx + aw_x / 2), aw_y)
    if open_arms_horizontal:
        opens = [("Open arm W", west), ("Open arm E", east)]
        closeds = [("Closed arm N", north), ("Closed arm S", south)]
    else:
        opens = [("Open arm N", north), ("Open arm S", south)]
        closeds = [("Closed arm W", west), ("Closed arm E", east)]
    app.zones.append(Zone("Centre", centre, "#64748b"))
    for n, s in opens:
        app.zones.append(Zone(n, s, PALETTE[1]))
    for n, s in closeds:
        app.zones.append(Zone(n, s, PALETTE[0]))
    app.groups.append(ZoneGroup("Open arms", [n for n, _ in opens]))
    app.groups.append(ZoneGroup("Closed arms", [n for n, _ in closeds]))
    hx0, hx1 = cx - aw_x / 2, cx + aw_x / 2
    hy0, hy1 = cy - aw_y / 2, cy + aw_y / 2
    app.arena = Polygon([
        (hx0, y), (hx1, y), (hx1, hy0), (x + w, hy0), (x + w, hy1), (hx1, hy1), (hx1, y + h),
        (hx0, y + h), (hx0, hy1), (x, hy1), (x, hy0), (hx0, hy0),
    ])
    _calibrate_width(app, w, total_cm)
    return app


def elevated_zero_maze(x, y, w, h, outer_diameter_cm=60.0, track_width_cm=5.0) -> Apparatus:
    app = Apparatus(name="Elevated zero maze", template="ezm")
    cx, cy = x + w / 2, y + h / 2
    r1 = min(w, h) / 2
    r0 = r1 * (1 - 2 * track_width_cm / outer_diameter_cm)
    names = [("Open quadrant NE", -90, 0, True), ("Closed quadrant SE", 0, 90, False),
             ("Open quadrant SW", 90, 180, True), ("Closed quadrant NW", 180, 270, False)]
    for n, a0, a1, is_open in names:
        app.zones.append(Zone(n, _sector(cx, cy, r0, r1, a0, a1), PALETTE[1] if is_open else PALETTE[0]))
    app.groups.append(ZoneGroup("Open quadrants", [n for n, *_, o in names if o]))
    app.groups.append(ZoneGroup("Closed quadrants", [n for n, *_, o in names if not o]))
    app.arena = circle(cx, cy, r1)
    _calibrate_width(app, 2 * r1, outer_diameter_cm)
    return app


def _arms_maze(name, template, cx, cy, arm_len, arm_w, angles, arm_names, centre_poly_r=None) -> Apparatus:
    app = Apparatus(name=name, template=template)
    n = len(angles)
    # Centre polygon: regular polygon whose edges match the arm width
    r_c = centre_poly_r if centre_poly_r is not None else (arm_w / 2) / math.tan(math.pi / n) if n > 2 else arm_w / 2
    r_c = max(r_c, arm_w / 2)
    centre_pts = []
    for a in angles:
        ar = math.radians(a)
        ux, uy = math.cos(ar), math.sin(ar)
        vx, vy = -uy, ux
        centre_pts.append((cx + ux * r_c + vx * arm_w / 2, cy + uy * r_c + vy * arm_w / 2))
        centre_pts.append((cx + ux * r_c - vx * arm_w / 2, cy + uy * r_c - vy * arm_w / 2))
    # order the centre polygon by angle around the centre
    centre_pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    app.zones.append(Zone("Centre", Polygon(centre_pts), "#64748b"))
    arena_pts = []
    for i, (a, an) in enumerate(zip(angles, arm_names)):
        ar = math.radians(a)
        sx, sy = cx + math.cos(ar) * r_c, cy + math.sin(ar) * r_c
        arm = rotated_rect(sx, sy, arm_len, arm_w, a, anchor="start")
        app.zones.append(Zone(an, arm, PALETTE[i % len(PALETTE)]))
        arena_pts.extend(arm.points)
    # arena: convex-ish union approximated by hull of all arm corners and centre
    import cv2

    hull = cv2.convexHull(np.array(arena_pts + centre_pts, np.float32)).reshape(-1, 2)
    app.arena = Polygon([tuple(map(float, p)) for p in hull])
    return app


def y_maze(x, y, w, h, arm_length_cm=35.0, arm_width_cm=8.0) -> Apparatus:
    """Y maze with arms A (up), B (lower right), C (lower left)."""
    # Fit: the Y spans arm_len above centre and arm_len*sin(30) below.
    cx = x + w / 2
    total_h_units = arm_length_cm * (1 + 0.5) + arm_width_cm
    s = h / total_h_units
    arm_len, arm_w = arm_length_cm * s, arm_width_cm * s
    cy = y + (arm_length_cm + arm_width_cm / 2) * s
    app = _arms_maze("Y maze", "y_maze", cx, cy, arm_len, arm_w, [-90, 30, 150], ["Arm A", "Arm B", "Arm C"])
    app.px_per_cm = s
    return app


def t_maze(x, y, w, h, arm_length_cm=30.0, stem_length_cm=40.0, arm_width_cm=8.0) -> Apparatus:
    app = Apparatus(name="T maze", template="t_maze")
    total_w = 2 * arm_length_cm + arm_width_cm
    s = w / total_w
    aw = arm_width_cm * s
    cx = x + w / 2
    junction = rect(cx - aw / 2, y, aw, aw)
    left = rect(x, y, cx - aw / 2 - x, aw)
    right = rect(cx + aw / 2, y, x + w - (cx + aw / 2), aw)
    stem_len = min(stem_length_cm * s, h - aw)
    stem = rect(cx - aw / 2, y + aw, aw, stem_len)
    app.zones += [Zone("Junction", junction, "#64748b"), Zone("Left arm", left, PALETTE[0]),
                  Zone("Right arm", right, PALETTE[1]), Zone("Stem", stem, PALETTE[2])]
    app.arena = Polygon([(x, y), (x + w, y), (x + w, y + aw), (cx + aw / 2, y + aw), (cx + aw / 2, y + aw + stem_len),
                         (cx - aw / 2, y + aw + stem_len), (cx - aw / 2, y + aw), (x, y + aw)])
    app.px_per_cm = s
    return app


def radial_arm_maze(x, y, w, h, n_arms=8, arm_length_cm=35.0, arm_width_cm=10.0, centre_diameter_cm=30.0) -> Apparatus:
    total = centre_diameter_cm + 2 * arm_length_cm
    s = min(w, h) / total
    cx, cy = x + w / 2, y + h / 2
    angles = [-90 + i * 360 / n_arms for i in range(n_arms)]
    app = _arms_maze("Radial arm maze", "radial_arm_maze", cx, cy, arm_length_cm * s, arm_width_cm * s, angles,
                     [f"Arm {i + 1}" for i in range(n_arms)], centre_poly_r=centre_diameter_cm * s / 2)
    app.px_per_cm = s
    return app


def morris_water_maze(x, y, w, h, pool_diameter_cm=150.0, platform_diameter_cm=10.0, platform_quadrant="NE",
                      platform_distance_fraction=0.5) -> Apparatus:
    """Water maze with four quadrants and a platform in the given quadrant."""
    app = Apparatus(name="Water maze", template="water_maze")
    cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
    app.arena = circle(cx, cy, r)
    quads = {"NE": (-90, 0), "SE": (0, 90), "SW": (90, 180), "NW": (180, 270)}
    for i, (q, (a0, a1)) in enumerate(quads.items()):
        app.zones.append(Zone(f"Quadrant {q}", _sector(cx, cy, 0, r, a0, a1), PALETTE[i]))
    a0, a1 = quads[platform_quadrant]
    am = math.radians((a0 + a1) / 2)
    pr = r * platform_distance_fraction
    px, py = cx + pr * math.cos(am), cy + pr * math.sin(am)
    s = 2 * r / pool_diameter_cm
    prad = platform_diameter_cm / 2 * s
    app.zones.append(Zone("Platform", circle(px, py, prad), "#f43f5e", moveable=True))
    app.zones.append(Zone("Platform annulus", circle(px, py, prad * 2), "#fb7185", moveable=True))
    app.points.append(PointOfInterest("Platform centre", px, py, radius_cm=platform_diameter_cm / 2))
    app.points.append(PointOfInterest("Pool centre", cx, cy, radius_cm=0))
    app.zones.append(Zone("Thigmotaxis zone", _sector(cx, cy, r * 0.9, r, 0, 360, 72), "#94a3b8"))
    # opposite-quadrant platform positions for annulus crossing comparison
    opp = {"NE": "SW", "SW": "NE", "NW": "SE", "SE": "NW"}
    for q in quads:
        if q == platform_quadrant:
            continue
        b0, b1 = quads[q]
        bm = math.radians((b0 + b1) / 2)
        app.zones.append(Zone(f"Platform position {q}", circle(cx + pr * math.cos(bm), cy + pr * math.sin(bm), prad),
                              "#cbd5e1"))
    app.groups.append(ZoneGroup("Target quadrant", [f"Quadrant {platform_quadrant}"]))
    app.groups.append(ZoneGroup("Opposite quadrant", [f"Quadrant {opp[platform_quadrant]}"]))
    app.px_per_cm = s
    return app


_WM_QUADS = ("NE", "SE", "SW", "NW")  # clockwise in image coordinates (y down)


def align_water_maze(app: Apparatus) -> Apparatus:
    """Make the water-maze target / opposite quadrant groups and "Platform position X" zones follow the platform.

    After a per-test platform move (``Apparatus.with_overrides``) the target quadrant is the "Quadrant X" zone
    containing the platform centre, the opposite quadrant is the one across the pool, and the comparison platform
    positions are the platform rotated by 90°, 180° and 270° about the pool centre. Returns ``app`` unchanged
    when it is not a water maze, has no platform / quadrants, or already matches; otherwise an adjusted copy.
    """
    if app.template != "water_maze":
        return app
    plat = app.zone("Platform")
    quads = {q: app.zone(f"Quadrant {q}") for q in _WM_QUADS}
    if plat is None or any(z is None for z in quads.values()):
        return app
    px, py = plat.shape.centroid()
    target = next((q for q, z in quads.items() if bool(z.shape.contains(px, py))), None)
    if target is None:
        return app
    opp = _WM_QUADS[(_WM_QUADS.index(target) + 2) % 4]
    try:
        cx, cy = app.arena_or_bounds().centroid()
    except ValueError:
        return app
    # comparison positions: the platform rotated about the pool centre, named after the quadrant they fall in
    positions = {}
    for k in (1, 2, 3):
        a = math.radians(90 * k)
        rx = cx + (px - cx) * math.cos(a) - (py - cy) * math.sin(a)
        ry = cy + (px - cx) * math.sin(a) + (py - cy) * math.cos(a)
        q = next((q for q, z in quads.items() if q != target and bool(z.shape.contains(rx, ry))),
                 _WM_QUADS[(_WM_QUADS.index(target) + k) % 4])
        positions[q] = (rx, ry)
    tq, oq = app.group("Target quadrant"), app.group("Opposite quadrant")
    old_pos = {z.name: z for z in app.zones if z.name.startswith("Platform position")}
    same_groups = (tq is None or list(tq.zones) == [f"Quadrant {target}"]) and \
                  (oq is None or list(oq.zones) == [f"Quadrant {opp}"])
    same_pos = not old_pos or (set(old_pos) == {f"Platform position {q}" for q in positions} and all(
        math.hypot(old_pos[f"Platform position {q}"].shape.centroid()[0] - x,
                   old_pos[f"Platform position {q}"].shape.centroid()[1] - y) < 0.5 for q, (x, y) in positions.items()))
    if same_groups and same_pos:
        return app
    app = app.copy()
    for g, q in ((app.group("Target quadrant"), target), (app.group("Opposite quadrant"), opp)):
        if g is not None:
            g.zones = [f"Quadrant {q}"]
            g.exclude = []
    if old_pos:
        colour = next(iter(old_pos.values())).color
        idx = min(i for i, z in enumerate(app.zones) if z.name.startswith("Platform position"))
        app.zones = [z for z in app.zones if not z.name.startswith("Platform position")]
        plat = app.zone("Platform")
        for j, q in enumerate(q for q in _WM_QUADS if q in positions):
            x, y = positions[q]
            app.zones.insert(idx + j, Zone(f"Platform position {q}", plat.shape.translated(x - px, y - py), colour))
    return app


def barnes_maze(x, y, w, h, diameter_cm=122.0, n_holes=20, hole_diameter_cm=5.0, escape_hole=1,
                hole_ring_fraction=0.9) -> Apparatus:
    app = Apparatus(name="Barnes maze", template="barnes_maze")
    cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
    s = 2 * r / diameter_cm
    app.arena = circle(cx, cy, r)
    rr = r * hole_ring_fraction - hole_diameter_cm * s
    names = []
    for i in range(n_holes):
        a = math.radians(-90 + i * 360 / n_holes)
        hx, hy = cx + rr * math.cos(a), cy + rr * math.sin(a)
        n = f"Hole {i + 1}"
        names.append(n)
        is_escape = i + 1 == escape_hole
        # zone a little larger than the hole so "head pokes" count
        app.zones.append(Zone(n, circle(hx, hy, hole_diameter_cm * s), "#f43f5e" if is_escape else "#94a3b8"))
        if is_escape:
            app.points.append(PointOfInterest("Escape hole", hx, hy, radius_cm=hole_diameter_cm))
    app.groups.append(ZoneGroup("All holes", names))
    app.groups.append(ZoneGroup("Escape hole zone", [f"Hole {escape_hole}"]))
    app.groups.append(ZoneGroup("Error holes", [n for n in names if n != f"Hole {escape_hole}"]))
    app.px_per_cm = s
    return app


def novel_object(x, y, w, h, size_cm=40.0, object_radius_cm=3.0, exploration_radius_cm=2.0) -> Apparatus:
    app = open_field(x, y, w, h, size_cm, corners=False)
    app.name, app.template = "Novel object recognition", "novel_object"
    s = w / size_cm
    for i, (fx, fy, n) in enumerate([(0.3, 0.3, "Object A"), (0.7, 0.7, "Object B")]):
        ox, oy = x + w * fx, y + h * fy
        app.points.append(PointOfInterest(n, ox, oy, radius_cm=object_radius_cm + exploration_radius_cm,
                                          color=PALETTE[3 + i]))
        app.zones.append(Zone(f"{n} zone", circle(ox, oy, (object_radius_cm + exploration_radius_cm) * s),
                              PALETTE[3 + i]))
    return app


def light_dark_box(x, y, w, h, width_cm=45.0, dark_fraction=1 / 3, dark_on_left=False) -> Apparatus:
    app = Apparatus(name="Light/dark box", template="light_dark")
    app.arena = rect(x, y, w, h)
    dw = w * dark_fraction
    if dark_on_left:
        dark, light = rect(x, y, dw, h), rect(x + dw, y, w - dw, h)
        lx = x + dw
    else:
        light, dark = rect(x, y, w - dw, h), rect(x + w - dw, y, dw, h)
        lx = x + w - dw
    app.zones += [Zone("Light compartment", light, "#facc15"), Zone("Dark compartment", dark, "#334155")]
    app.lines.append(Line("Doorway", lx, y, lx, y + h))
    _calibrate_width(app, w, width_cm)
    return app


def three_chamber(x, y, w, h, width_cm=60.0, cup_radius_cm=5.0, interaction_cm=3.0) -> Apparatus:
    app = Apparatus(name="Three-chamber sociability", template="three_chamber")
    app.arena = rect(x, y, w, h)
    cw = w / 3
    names = ["Left chamber", "Centre chamber", "Right chamber"]
    for i, n in enumerate(names):
        app.zones.append(Zone(n, rect(x + i * cw, y, cw, h), PALETTE[i]))
    s = w / width_cm
    for n, fx in (("Left cup", 1 / 6), ("Right cup", 5 / 6)):
        px, py = x + w * fx, y + h / 2
        app.points.append(PointOfInterest(n, px, py, radius_cm=cup_radius_cm + interaction_cm))
        app.zones.append(Zone(f"{n} interaction zone", circle(px, py, (cup_radius_cm + interaction_cm) * s), "#f43f5e"))
    app.lines.append(Line("Left doorway", x + cw, y, x + cw, y + h))
    app.lines.append(Line("Right doorway", x + 2 * cw, y, x + 2 * cw, y + h))
    app.px_per_cm = s
    return app


def fear_conditioning(x, y, w, h, width_cm=30.0) -> Apparatus:
    app = Apparatus(name="Fear conditioning chamber", template="fear_conditioning")
    app.arena = rect(x, y, w, h)
    app.zones.append(Zone("Chamber", rect(x, y, w, h), "#64748b"))
    _calibrate_width(app, w, width_cm)
    return app


def forced_swim(x, y, w, h, diameter_cm=20.0) -> Apparatus:
    app = Apparatus(name="Forced swim / tail suspension", template="forced_swim")
    cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
    app.arena = Ellipse(cx, cy, w / 2, h / 2)
    app.zones.append(Zone("Cylinder", Ellipse(cx, cy, w / 2, h / 2), "#64748b"))
    _calibrate_width(app, w, diameter_cm)
    return app


def custom(x, y, w, h, width_cm=0.0) -> Apparatus:
    app = Apparatus(name="Custom apparatus", template="custom")
    app.arena = rect(x, y, w, h)
    if width_cm:
        _calibrate_width(app, w, width_cm)
    return app


def novel_tank(x, y, w, h, width_cm=20.0, n_layers=3) -> Apparatus:
    """Novel tank diving test (side view): horizontal layers Top / Middle / Bottom (or n equal layers)."""
    app = Apparatus(name="Novel tank diving test", template="novel_tank")
    app.arena = rect(x, y, w, h)
    n = max(2, int(n_layers))
    names = ["Top", "Middle", "Bottom"] if n == 3 else ["Top", "Bottom"] if n == 2 else \
        ["Top"] + [f"Layer {i + 1}" for i in range(1, n - 1)] + ["Bottom"]
    lh = h / n
    for i, nm in enumerate(names):
        app.zones.append(Zone(nm, rect(x, y + i * lh, w, lh), ["#38bdf8", "#0ea5e9", "#0369a1"][min(i, 2)]
                              if n == 3 else PALETTE[i % len(PALETTE)]))
    app.groups.append(ZoneGroup("Upper half", [nm for i, nm in enumerate(names) if (i + 0.5) * lh <= h / 2]))
    app.groups.append(ZoneGroup("Lower half", [nm for i, nm in enumerate(names) if (i + 0.5) * lh > h / 2]))
    _calibrate_width(app, w, width_cm)
    return app


WELL_LAYOUTS = {6: (2, 3, 0.89), 12: (3, 4, 0.85), 24: (4, 6, 0.81), 48: (6, 8, 0.83), 96: (8, 12, 0.71)}
# rows, columns, well diameter / pitch for standard SBS plates


def multi_well_plate(x, y, w, h, n_wells=24, plate_width_cm=12.78, centre_fraction=0.5) -> Apparatus:
    """Multi-well plate (e.g. larval zebrafish): every well is a zone "Well A1" …; see split_wells()."""
    rows, cols, frac = WELL_LAYOUTS.get(int(n_wells), WELL_LAYOUTS[24])
    app = Apparatus(name=f"{int(n_wells)}-well plate", template="multi_well")
    app.arena = rect(x, y, w, h)
    pitch = min(w / cols, h / rows)
    ox, oy = x + (w - cols * pitch) / 2, y + (h - rows * pitch) / 2
    r = pitch * frac / 2
    names = []
    for i in range(rows):
        for j in range(cols):
            n = f"Well {'ABCDEFGH'[i]}{j + 1}"
            app.zones.append(Zone(n, circle(ox + (j + 0.5) * pitch, oy + (i + 0.5) * pitch, r), "#38bdf8"))
            names.append(n)
    app.groups.append(ZoneGroup("All wells", names))
    _calibrate_width(app, w, plate_width_cm)
    app.calibration_length_cm = plate_width_cm
    return app


def split_wells(plate: Apparatus, centre_fraction: float = 0.5) -> list[Apparatus]:
    """One apparatus per well (arena = the well; zones Well, Centre and the Edge group) for multi-arena tracking."""
    out = []
    for z in plate.zones:
        if not z.name.startswith("Well"):
            continue
        cx, cy = z.shape.centroid()
        x0, y0, x1, y1 = z.shape.bounds()
        r = (x1 - x0) / 2
        a = Apparatus(name=z.name, template="multi_well", px_per_cm=plate.px_per_cm,
                      calibration_length_cm=plate.calibration_length_cm, frame_size=plate.frame_size)
        a.arena = circle(cx, cy, r)
        a.zones.append(Zone("Well", circle(cx, cy, r), "#64748b"))
        a.zones.append(Zone("Centre", circle(cx, cy, r * centre_fraction), PALETTE[0]))
        a.groups.append(ZoneGroup("Edge", ["Well"], ["Centre"]))
        out.append(a)
    return out


def conditioned_place_preference(x, y, w, h, width_cm=60.0, n_chambers=2, centre_fraction=0.2) -> Apparatus:
    """Conditioned place preference box: 2 chambers, or 3 with a neutral centre compartment."""
    app = Apparatus(name="Conditioned place preference", template="cpp")
    app.arena = rect(x, y, w, h)
    if int(n_chambers) >= 3:
        cw = w * centre_fraction
        sw = (w - cw) / 2
        parts = [("Chamber A", x, sw, PALETTE[0]), ("Centre", x + sw, cw, "#64748b"), ("Chamber B", x + sw + cw, sw,
                                                                                         PALETTE[1])]
    else:
        parts = [("Chamber A", x, w / 2, PALETTE[0]), ("Chamber B", x + w / 2, w / 2, PALETTE[1])]
    for n, zx, zw, c in parts:
        app.zones.append(Zone(n, rect(zx, y, zw, h), c))
    for i in range(len(parts) - 1):
        lx = parts[i + 1][1]
        app.lines.append(Line(f"Doorway {i + 1}" if len(parts) > 2 else "Doorway", lx, y, lx, y + h))
    _calibrate_width(app, w, width_cm)
    return app


def hole_board(x, y, w, h, size_cm=40.0, holes_per_side=4, hole_diameter_cm=3.0, dip_margin_cm=1.0) -> Apparatus:
    """Hole board: holes on a regular grid as points; a head dip is the head within the hole radius + margin."""
    app = open_field(x, y, w, h, size_cm, corners=False)
    app.name, app.template = "Hole board", "hole_board"
    n = max(1, int(holes_per_side))
    for i in range(n):
        for j in range(n):
            px, py = x + w * (j + 1) / (n + 1), y + h * (i + 1) / (n + 1)
            app.points.append(PointOfInterest(f"Hole {i * n + j + 1}", px, py,
                                              radius_cm=hole_diameter_cm / 2 + dip_margin_cm, color="#f43f5e"))
    return app


def thermal_gradient_ring(x, y, w, h, outer_diameter_cm=60.0, track_width_cm=8.0, n_sectors=12) -> Apparatus:
    """Thermal gradient ring: annular track divided into equal sectors (Sector 1 at the top, clockwise)."""
    app = Apparatus(name="Thermal gradient ring", template="thermal_gradient")
    cx, cy = x + w / 2, y + h / 2
    r1 = min(w, h) / 2
    r0 = r1 * max(0.0, 1 - 2 * track_width_cm / outer_diameter_cm)
    n = max(2, int(n_sectors))
    step = 360.0 / n
    for i in range(n):
        app.zones.append(Zone(f"Sector {i + 1}", _sector(cx, cy, r0, r1, -90 + i * step, -90 + (i + 1) * step),
                              PALETTE[i % len(PALETTE)]))
    app.arena = circle(cx, cy, r1)
    _calibrate_width(app, 2 * r1, outer_diameter_cm)
    return app


def home_cage(x, y, w, h, width_cm=30.0, nest_fraction=0.3, food_fraction=0.25) -> Apparatus:
    """Home cage: whole cage, food hopper area and a hidden nest (time in the nest counts when not visible)."""
    app = Apparatus(name="Home cage", template="home_cage")
    app.arena = rect(x, y, w, h)
    app.zones.append(Zone("Cage", rect(x, y, w, h), "#64748b"))
    fw, fh = w * food_fraction, h * food_fraction
    app.zones.append(Zone("Food", rect(x + w - fw, y, fw, fh), PALETTE[3]))
    nw, nh = w * nest_fraction, h * nest_fraction
    app.zones.append(Zone("Nest", rect(x, y + h - nh, nw, nh), "#8b5cf6", hidden=True))
    _calibrate_width(app, w, width_cm)
    return app


def activity_wheel(x, y, w, h, diameter_cm=12.0) -> Apparatus:
    """Running wheel seen side-on (or a circular runway): revolutions of the animal around the centre."""
    app = Apparatus(name="Activity wheel", template="activity_wheel")
    cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2
    app.arena = circle(cx, cy, r)
    app.zones.append(Zone("Wheel", circle(cx, cy, r), "#64748b"))
    _calibrate_width(app, 2 * r, diameter_cm)
    return app


@dataclass
class TemplateInfo:
    key: str
    title: str
    builder: Callable[..., Apparatus]
    description: str
    params: dict  # parameter name -> default (for UI forms)
    default_duration_s: float = 300.0
    choices: dict = field(default_factory=dict)  # parameter name -> allowed values (combo box in the UI)
    multi: bool = False  # build_many() gives one apparatus per arena (e.g. one per well)


TEMPLATES: dict[str, TemplateInfo] = {t.key: t for t in [
    TemplateInfo("open_field", "Open field", open_field,
                 "Square or circular arena with centre, periphery and corners.",
                 {"size_cm": 40.0, "centre_fraction": 0.5, "corners": True, "circular": False}, 600),
    TemplateInfo("epm", "Elevated plus maze", elevated_plus_maze,
                 "Two open and two closed arms with a centre square.",
                 {"arm_length_cm": 35.0, "arm_width_cm": 5.0, "open_arms_horizontal": True}, 300),
    TemplateInfo("ezm", "Elevated zero maze", elevated_zero_maze,
                 "Annular track with two open and two closed quadrants.",
                 {"outer_diameter_cm": 60.0, "track_width_cm": 5.0}, 300),
    TemplateInfo("y_maze", "Y maze (spontaneous alternation)", y_maze,
                 "Three arms at 120°. Spontaneous alternation is computed from arm entries.",
                 {"arm_length_cm": 35.0, "arm_width_cm": 8.0}, 480),
    TemplateInfo("t_maze", "T maze", t_maze, "Stem with left and right goal arms.",
                 {"arm_length_cm": 30.0, "stem_length_cm": 40.0, "arm_width_cm": 8.0}, 300),
    TemplateInfo("radial_arm_maze", "Radial arm maze", radial_arm_maze,
                 "Central platform with N arms; computes working/reference memory errors.",
                 {"n_arms": 8, "arm_length_cm": 35.0, "arm_width_cm": 10.0, "centre_diameter_cm": 30.0}, 600),
    TemplateInfo("water_maze", "Morris water maze", morris_water_maze,
                 "Circular pool with quadrants, hidden platform and annulus zones.",
                 {"pool_diameter_cm": 150.0, "platform_diameter_cm": 10.0, "platform_quadrant": "NE",
                  "platform_distance_fraction": 0.5}, 60, choices={"platform_quadrant": ["NE", "NW", "SE", "SW"]}),
    TemplateInfo("barnes_maze", "Barnes maze", barnes_maze,
                 "Circular platform with holes around the edge and an escape hole.",
                 {"diameter_cm": 122.0, "n_holes": 20, "hole_diameter_cm": 5.0, "escape_hole": 1}, 180),
    TemplateInfo("novel_object", "Novel object recognition", novel_object,
                 "Open field with two objects; computes exploration times and discrimination index.",
                 {"size_cm": 40.0, "object_radius_cm": 3.0, "exploration_radius_cm": 2.0}, 300),
    TemplateInfo("light_dark", "Light/dark box", light_dark_box,
                 "Light and dark compartments; transitions and latency to dark.",
                 {"width_cm": 45.0, "dark_fraction": 1 / 3, "dark_on_left": False}, 300),
    TemplateInfo("three_chamber", "Three-chamber sociability", three_chamber,
                 "Three chambers with cups; computes sociability / social novelty indices.",
                 {"width_cm": 60.0, "cup_radius_cm": 5.0, "interaction_cm": 3.0}, 600),
    TemplateInfo("fear_conditioning", "Fear conditioning / freezing", fear_conditioning,
                 "Single chamber; freezing analysis based on pixel change.", {"width_cm": 30.0}, 300),
    TemplateInfo("forced_swim", "Forced swim / tail suspension", forced_swim,
                 "Immobility analysis (Porsolt / tail suspension).", {"diameter_cm": 20.0}, 360),
    TemplateInfo("novel_tank", "Novel tank diving test (fish)", novel_tank,
                 "Side view of a tank divided into top, middle and bottom layers; latency to top, bottom "
                 "dwelling, erratic movements.", {"width_cm": 20.0, "n_layers": 3}, 360),
    TemplateInfo("multi_well", "Multi-well plate (larvae)", multi_well_plate,
                 "6–96 well plate; one apparatus (arena) per well so every well is tracked and analysed "
                 "separately.", {"n_wells": 24, "plate_width_cm": 12.78, "centre_fraction": 0.5}, 600,
                 choices={"n_wells": [6, 12, 24, 48, 96]}, multi=True),
    TemplateInfo("cpp", "Conditioned place preference", conditioned_place_preference,
                 "Two chambers (or three with a neutral centre); preference score for the paired chamber.",
                 {"width_cm": 60.0, "n_chambers": 2, "centre_fraction": 0.2}, 900, choices={"n_chambers": [2, 3]}),
    TemplateInfo("hole_board", "Hole board", hole_board,
                 "Floor with a grid of holes (points); counts head dips per hole.",
                 {"size_cm": 40.0, "holes_per_side": 4, "hole_diameter_cm": 3.0, "dip_margin_cm": 1.0}, 300),
    TemplateInfo("thermal_gradient", "Thermal gradient ring", thermal_gradient_ring,
                 "Annular runway divided into sectors along a temperature gradient; preferred sector.",
                 {"outer_diameter_cm": 60.0, "track_width_cm": 8.0, "n_sectors": 12}, 3600),
    TemplateInfo("home_cage", "Home cage", home_cage,
                 "Cage with a food area and a hidden nest zone.", {"width_cm": 30.0, "nest_fraction": 0.3,
                                                                   "food_fraction": 0.25}, 3600),
    TemplateInfo("activity_wheel", "Activity wheel / circular runway", activity_wheel,
                 "Circular arena; counts revolutions of the animal around the centre.", {"diameter_cm": 12.0}, 3600),
    TemplateInfo("custom", "Custom", custom, "Empty rectangular arena; draw your own zones.", {"width_cm": 0.0}, 300),
]}


def build(key: str, x, y, w, h, **params) -> Apparatus:
    info = TEMPLATES[key]
    kw = dict(info.params)
    kw.update({k: v for k, v in params.items() if k in info.params})
    return info.builder(x, y, w, h, **kw)


def build_many(key: str, x, y, w, h, **params) -> list[Apparatus]:
    """Like build(), but templates with several arenas (multi-well plates) give one apparatus per arena."""
    app = build(key, x, y, w, h, **params)
    if key == "multi_well":
        return split_wells(app, float(params.get("centre_fraction", 0.5)))
    return [app]
