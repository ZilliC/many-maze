"""Import experiments exported by ANY-maze.

ANY-maze saves experiments (.szd) in a proprietary, partly compressed binary format that its makers describe as
"virtually impossible for any other programs to read"; it is undocumented, so mANY-MAZE does not read it. ANY-maze
offers instead three documented exports on its File page ▸ Export (ANY-maze Help, topics T0067–T0069), which are
read here:

* **Export experiment as XML** — the animals (number, ID, treatment, notes) and their performed tests (number, date
  and time, stage, trial, apparatus, reason the test ended, notes, the position of every zone as its centre and
  bounding box, the scaling in pixels per metre) with one result per position: ``<r>`` with ``<tm>`` (time, s),
  ``<c>`` (centre), ``<h>`` (head), ``<t>`` (tail) and ``<np>`` (no position). Only the result tags are named by
  the documentation, so the other fields are matched by name tolerantly (any case, with or without separators,
  as attributes or child elements); coordinates may be child elements (``<x>``, ``<y>``), attributes or "x,y"
  text. :func:`read_anymaze_xml` / :func:`import_anymaze_xml`.
* **Export zone maps** — one CSV per zone (or one per apparatus) giving, pixel by pixel, which zone each pixel of
  the image belongs to; turned into apparatus zones (outlines traced from the pixels).
  :func:`read_zone_map` / :func:`zone_maps_apparatus`.
* **Export test data** — one spreadsheet per test (CSV / tab-separated / SYLK / dBase) with the positions: imported
  as tracks through the import wizard (importers.import_track).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import math
import re
from pathlib import Path

import numpy as np

from .apparatus import CALIBRATION_KEY, Apparatus, Zone, calibration_override
from .geometry import Polygon
from .track import Track

ZONE_COLORS = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
               "#06b6d4"]


def _norm(tag: str) -> str:
    """Element / attribute name without namespace, case and separators: 'Test_Number' -> 'testnumber'."""
    tag = str(tag).rsplit("}", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def _children(e, *names):
    want = {_norm(n) for n in names}
    return [c for c in e if _norm(c.tag) in want]


def _get(e, *names, default: str = "") -> str:
    """Text of the first attribute or direct child element with one of the names."""
    want = [_norm(n) for n in names]
    attrs = {_norm(k): v for k, v in e.attrib.items()}
    for n in want:
        if n in attrs and str(attrs[n]).strip():
            return str(attrs[n]).strip()
        for c in e:
            if _norm(c.tag) == n and (c.text or "").strip():
                return c.text.strip()
    return default


def _num(s, default=math.nan) -> float:
    s = str(s or "").strip()
    if not s:
        return default
    m = re.fullmatch(r"(-)?(\d+):(\d{1,2})(?::(\d{1,2}(?:\.\d*)?))?(?:\.(\d+))?", s)
    if m:  # h:mm:ss(.s) or m:ss
        parts = [float(x) for x in s.lstrip("-").split(":")]
        v = 0.0
        for x in parts:
            v = v * 60 + x
        return -v if s.startswith("-") else v
    try:
        return float(s.replace(",", ".")) if s.count(",") == 1 and "." not in s else float(s)
    except ValueError:
        m = re.match(r"^[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
        return float(m.group(0)) if m else default


def _xy(e) -> tuple[float, float]:
    """A position element: <c><x>1</x><y>2</y></c>, <c x="1" y="2"/> or <c>1,2</c>."""
    if e is None:
        return math.nan, math.nan
    x, y = _get(e, "x"), _get(e, "y")
    if x or y:
        return _num(x), _num(y)
    parts = re.split(r"[\s,;]+", (e.text or "").strip())
    if len(parts) >= 2:
        return _num(parts[0]), _num(parts[1])
    return math.nan, math.nan


def _when(date: str, time_: str) -> str:
    """ISO date-time of a test from ANY-maze's date and time texts ('' if they cannot be read)."""
    text = f"{date} {time_}".strip()
    if not text:
        return ""
    try:
        return _dt.datetime.fromisoformat(text.replace("T", " ")).isoformat(timespec="seconds")
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d.%m.%Y %H:%M:%S",
                "%d-%m-%Y %H:%M:%S", "%d %B %Y %H:%M:%S", "%d %b %Y %H:%M:%S", "%B %d, %Y %H:%M:%S",
                "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return _dt.datetime.strptime(text, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


# ====================================================================== XML export
def is_anymaze_xml(path) -> bool:
    """Whether a file looks like ANY-maze's experiment XML export (root <Experiment> with animals in it)."""
    import xml.etree.ElementTree as ET

    try:
        for _, e in ET.iterparse(str(path), events=("start",)):
            return _norm(e.tag) == "experiment"
    except ET.ParseError:
        return False
    return False


def _zone_info(z) -> dict:
    name = _get(z, "name", "zonename", "title")
    cx, cy = _xy(next(iter(_children(z, "centre", "center", "centreofmass", "centerofmass", "centroid")), None))
    b = next(iter(_children(z, "boundingbox", "bounds", "boundingrect", "box", "rect", "rectangle")), None)
    if b is not None:
        bx, by = _num(_get(b, "x", "left")), _num(_get(b, "y", "top"))
        bw, bh = _num(_get(b, "w", "width")), _num(_get(b, "h", "height"))
        if not (math.isfinite(bx) and math.isfinite(bw)):
            parts = [_num(p) for p in re.split(r"[\s,;]+", (b.text or "").strip()) if p]
            if len(parts) >= 4:
                bx, by, bw, bh = parts[:4]
    else:
        bx = by = bw = bh = math.nan
    return {"name": name, "centre": (cx, cy), "bbox": (bx, by, bw, bh)}


def _results(test) -> dict[str, np.ndarray]:
    rows = [r for r in test.iter() if _norm(r.tag) in ("r", "result")]
    n = len(rows)
    cols = {k: np.full(n, np.nan) for k in ("t", "x", "y", "hx", "hy", "tx", "ty")}
    zones_in: list[list[str]] = []
    for i, r in enumerate(rows):
        cols["t"][i] = _num(_get(r, "tm", "time"))
        kids = {_norm(c.tag): c for c in r}
        if "np" in kids or "noposition" in kids:
            continue
        for key, tags in (("", ("c", "centre", "center")), ("h", ("h", "head")), ("t", ("t", "tail"))):
            e = next((kids[g] for g in tags if g in kids), None)
            if e is not None:
                cols[key + "x" if key else "x"][i], cols[key + "y" if key else "y"][i] = _xy(e)
    return cols


def read_anymaze_xml(path) -> dict:
    """ANY-maze's experiment XML export as plain data: {"title", "created", "notes", "animals": [{"number", "id",
    "treatment", "notes", "tests": [{"number", "recorded_at", "stage", "trial", "apparatus", "end_reason", "notes",
    "px_per_m", "zones": [{"name", "centre", "bbox"}], "track": {t, x, y, hx, hy, tx, ty}}]}]}."""
    import xml.etree.ElementTree as ET

    root = ET.parse(str(path)).getroot()
    if _norm(root.tag) != "experiment":
        exp = next((e for e in root.iter() if _norm(e.tag) == "experiment"), None)
        if exp is None:
            raise ValueError(f"{Path(path).name} is not an ANY-maze experiment XML export (no <Experiment>)")
        root = exp
    out = {"title": _get(root, "title", "name", "experimenttitle"), "created": _get(root, "creationdate", "created",
                                                                                     "datecreated", "date"),
           "notes": _get(root, "notes", "note"), "animals": []}
    for a in (e for e in root.iter() if _norm(e.tag) == "animal"):
        animal = {"number": _get(a, "number", "animalnumber", "num", "no"),
                  "id": _get(a, "id", "animalid", "idfield", "animalidfield"),
                  "treatment": _get(a, "treatment", "treatmentgroup", "group"),
                  "notes": _get(a, "notes", "note"), "tests": []}
        for t in (e for e in a.iter() if _norm(e.tag) == "test"):
            zones = [_zone_info(z) for z in t.iter() if _norm(z.tag) == "zone"]
            scale = _get(t, "scaling", "scale", "pixelspermetre", "pixelspermeter", "pixelsmetre", "pixelsmeter",
                         "calibration")
            animal["tests"].append({
                "number": _get(t, "number", "testnumber", "num", "no"),
                "recorded_at": _when(_get(t, "date", "testdate"), _get(t, "time", "testtime")),
                "stage": _get(t, "stage", "stagename"),
                "trial": _get(t, "trial", "trialnumber"),
                "apparatus": _get(t, "apparatus", "apparatusname"),
                "end_reason": _get(t, "reasontestended", "endreason", "reasonforend", "testendreason", "reason",
                                   "reasonfortestend", "endedby"),
                "notes": _get(t, "notes", "note", "testnotes"),
                "px_per_m": _num(scale),
                "zones": zones,
                "track": _results(t)})
        out["animals"].append(animal)
    return out


def _bbox_apparatus(name: str, zones: list[dict], px_per_m: float) -> Apparatus:
    """An apparatus whose zones are the exported bounding boxes (the XML has no zone outlines: import the zone
    maps for the real shapes)."""
    app = Apparatus(name=name or "ANY-maze apparatus")
    for k, z in enumerate(zones):
        bx, by, bw, bh = z["bbox"]
        if not all(math.isfinite(v) for v in (bx, by, bw, bh)) or bw <= 0 or bh <= 0:
            continue
        app.zones.append(Zone(z["name"] or f"Zone {k + 1}",
                              Polygon([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)]),
                              ZONE_COLORS[k % len(ZONE_COLORS)]))
    if math.isfinite(px_per_m) and px_per_m > 0:
        app.px_per_cm = px_per_m / 100.0
    return app


def _moved_zones(app: Apparatus, zones: list[dict], tol: float = 1.5) -> dict:
    """Test.zone_overrides for the zones of `app` whose exported position differs (moveable zones)."""
    out = {}
    for z in zones:
        zone = app.zone(z["name"])
        bx, by, bw, bh = z["bbox"]
        if zone is None or not all(math.isfinite(v) for v in (bx, by, bw, bh)):
            continue
        x0, y0, x1, y1 = zone.shape.bounds()
        dx, dy = bx - x0, by - y0
        if math.hypot(dx, dy) > tol and abs((x1 - x0) - bw) <= max(2.0, 0.05 * bw):
            out[zone.name] = zone.shape.translated(dx, dy).to_dict()
    return out


def track_from_columns(cols: dict, origin: str = "auto", centre: tuple[float, float] | None = None) -> Track:
    """A Track (video pixels) from exported positions. origin "image": relative to the top left of the image
    (ANY-maze's default); "centre": relative to the apparatus centre with y up (ANY-maze's option); "auto":
    "centre" if any coordinate is negative."""
    t = np.asarray(cols["t"], float)
    good = np.isfinite(t)
    vals = {k: np.asarray(cols[k], float)[good] for k in ("x", "y", "hx", "hy", "tx", "ty")}
    t = t[good]
    if not len(t):
        raise ValueError("the test has no positions")
    if origin == "auto":
        xs = np.concatenate([vals["x"], vals["y"]])
        xs = xs[np.isfinite(xs)]
        origin = "centre" if len(xs) and xs.min() < 0 else "image"
    if origin == "centre":
        cx, cy = centre or (0.0, 0.0)
        for k in ("x", "hx", "tx"):
            vals[k] = vals[k] + cx
        for k in ("y", "hy", "ty"):
            vals[k] = cy - vals[k]
    t = t - t[0]
    dt = np.diff(t)
    fps = 1.0 / float(np.median(dt[dt > 0])) if (dt > 0).any() else 25.0
    detected = np.isfinite(vals["x"]) & np.isfinite(vals["y"])
    tr = Track(t=t, **vals, detected=detected, fps=fps)
    tr.meta["source"] = "ANY-maze XML export"
    return tr


def import_anymaze_xml(project, path, origin: str = "auto", progress=None) -> dict:
    """Add the animals, tests (with their tracks, zone positions and calibration) of an ANY-maze experiment XML
    export to a saved project. Apparatus missing from the project are created from the exported zone bounding boxes
    (import the zone maps first for exact zone shapes: an apparatus of the same name is then used). Returns
    {"animals": [...ids], "tests": [...], "apparatus": [...created names], "warnings": [...]}."""
    if project.path is None:
        raise ValueError("Save the experiment before importing tests")
    data = read_anymaze_xml(path)
    out = {"animals": [], "tests": [], "apparatus": [], "warnings": []}
    total = sum(len(a["tests"]) for a in data["animals"]) or 1
    done = 0
    for a in data["animals"]:
        aid = a["id"] or (f"Animal {a['number']}" if a["number"] else f"Animal {len(out['animals']) + 1}")
        animal = project.ensure_animal(aid, a["treatment"])
        if a["treatment"] and not animal.group:
            animal.group = a["treatment"]
        if a["notes"]:
            if hasattr(animal, "notes"):
                animal.notes = (str(getattr(animal, "notes") or "") + "\n" + a["notes"]).strip()
            else:
                if "Notes" not in project.animal_fields:
                    project.animal_fields.append("Notes")
                animal.fields["Notes"] = a["notes"]
        if a["number"] and a["id"]:
            if "ANY-maze number" not in project.animal_fields:
                project.animal_fields.append("ANY-maze number")
            animal.fields["ANY-maze number"] = a["number"]
        out["animals"].append(aid)
        for t in a["tests"]:
            done += 1
            name = t["apparatus"] or "ANY-maze apparatus"
            app = project.get_apparatus(name) if any(x.name == name for x in project.apparatus) else None
            if app is None:
                app = _bbox_apparatus(name, t["zones"], t["px_per_m"])
                project.apparatus.append(app)
                out["apparatus"].append(app.name)
            try:
                centre = app.origin()
                tr = track_from_columns(t["track"], origin, centre)
            except ValueError as e:
                out["warnings"].append(f"Animal {aid}, test {t['number'] or '?'}: {e}")
                continue
            trial = _num(t["trial"])
            test = project.add_test("", aid, app.name, stage=project.add_stage(t["stage"]),
                                    trial=int(trial) if math.isfinite(trial) else 1)
            test.recorded_at = t["recorded_at"]
            notes = [t["notes"]] if t["notes"] else []
            if t["number"]:
                notes.append(f"ANY-maze test {t['number']}")
            if t["end_reason"]:
                if hasattr(test, "end_reason"):
                    test.end_reason = t["end_reason"]
                else:
                    notes.append(f"Test ended: {t['end_reason']}")
            test.notes = "\n".join(notes)
            test.zone_overrides.update(_moved_zones(app, t["zones"]))
            ppm = t["px_per_m"]
            if math.isfinite(ppm) and ppm > 0 and not (app.px_per_cm and abs(app.px_per_cm - ppm / 100) < 1e-6):
                test.zone_overrides[CALIBRATION_KEY] = calibration_override(ppm / 100.0)
            tr.meta["video_start_s"] = 0.0
            project.save_tracks(test, [tr])
            test.duration_s = round(float(tr.t[-1] + tr.dt), 3) if len(tr) else 0.0
            out["tests"].append(test)
            if progress:
                progress(done / total)
    return out


# ====================================================================== zone maps
def read_zone_map(path) -> dict:
    """An ANY-maze zone map CSV: {"apparatus", "zones": {name: bool mask (h × w)}, "position" (moveable zones,
    individual maps) or None, "kind": "individual" | "combined"}. Border maps are filled."""
    import cv2

    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"{Path(path).name} is empty")
    head = [c.strip() for c in rows[0]]
    codes: dict[int, str] = {}
    i = 0
    kind = "individual"
    app_name, zone, position = "", "", None
    if head[0].lower().startswith("zone names and codes"):
        kind = "combined"
        i = 1
        while i < len(rows) and len(rows[i]) >= 2 and not rows[i][0].lower().startswith("zone area map") \
                and re.fullmatch(r"\s*\d+\s*", rows[i][1] or ""):
            codes[int(rows[i][1])] = rows[i][0].strip()
            i += 1
    while i < len(rows) and not rows[i][0].strip().lower().startswith("zone area map dimensions"):
        r = [c.strip() for c in rows[i]]
        if r[0].lower().startswith("zone area map for") or r[0].lower().startswith("combined zone area map for"):
            app_name = r[1] if len(r) > 1 else ""
            zone = r[2] if len(r) > 2 else ""
            position = r[3] if len(r) > 3 and r[3] else None
        i += 1
    if i >= len(rows):
        raise ValueError(f"{Path(path).name} is not an ANY-maze zone map (no 'Zone area map dimensions' row)")
    dims = [c.strip() for c in rows[i]]
    w, h = int(float(dims[1])), int(float(dims[2]))
    body = rows[i + 1:i + 1 + h]
    grid = np.zeros((h, w), np.int32)
    for y, r in enumerate(body):
        vals = [int(float(c)) if c.strip() else 0 for c in r[:w]]
        grid[y, :len(vals)] = vals
    if kind == "combined":
        if not codes:
            codes = {int(v): f"Zone {int(v)}" for v in np.unique(grid) if v}
        zones = {n: grid == code for code, n in codes.items()}
    else:
        mask = grid > 0
        filled = mask.copy()
        # a border map: the zone is the border and everything it encloses
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        img = np.zeros((h, w), np.uint8)
        cv2.drawContours(img, cs, -1, 1, thickness=cv2.FILLED)
        filled |= img.astype(bool)
        zones = {zone or Path(path).stem: filled}
    if not app_name:  # from the file name: "Individual zone area map for <apparatus>, <zone> zone[, <pos> position]"
        m = re.search(r"map for (.+?)(?:, (.+?)(?: zone)?)?(?:, (.+?) position)?(?: \(\d+\))?$", Path(path).stem)
        if m:
            app_name = m.group(1)
            if kind == "individual" and not zone and m.group(2):
                zones = {m.group(2): next(iter(zones.values()))}
            position = position or m.group(3)
    return {"apparatus": app_name, "zones": zones, "position": position, "kind": kind, "size": (w, h)}


def mask_polygon(mask: np.ndarray, epsilon: float = 0.5) -> list[tuple[float, float]]:
    """Outline of the largest region of a pixel mask as polygon vertices (pixel coordinates, slightly
    simplified)."""
    import cv2

    cs, _ = cv2.findContours(np.ascontiguousarray(mask.astype(np.uint8)), cv2.RETR_EXTERNAL,
                             cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return []
    c = cv2.approxPolyDP(max(cs, key=cv2.contourArea), epsilon, True)
    return [(float(x), float(y)) for x, y in c.reshape(-1, 2)]


def zone_maps_apparatus(paths, project=None) -> list[Apparatus]:
    """Apparatus built from ANY-maze zone map files (individual and / or combined; several apparatus are told apart
    by the names in the files). With a project, they are added to it, replacing the zones of an apparatus of the
    same name (its calibration is kept). A moveable zone exported in several positions gets the first one."""
    by_app: dict[str, Apparatus] = {}
    seen_pos: set[tuple[str, str]] = set()
    for p in paths:
        zm = read_zone_map(p)
        name = zm["apparatus"] or "ANY-maze apparatus"
        app = by_app.setdefault(name, Apparatus(name=name, frame_size=zm["size"]))
        for zn, mask in zm["zones"].items():
            pts = mask_polygon(mask)
            if len(pts) < 3:
                continue
            if zm["position"]:
                if (name, zn) in seen_pos:
                    continue
                seen_pos.add((name, zn))
            existing = app.zone(zn)
            shape = Polygon(pts)
            if existing is not None:
                existing.shape = shape
            else:
                app.zones.append(Zone(zn, shape, ZONE_COLORS[len(app.zones) % len(ZONE_COLORS)],
                                      moveable=bool(zm["position"])))
    apps = list(by_app.values())
    if project is None:
        return apps
    out = []
    for app in apps:
        old = next((a for a in project.apparatus if a.name == app.name), None)
        if old is None:
            project.apparatus.append(app)
            out.append(app)
            continue
        for z in app.zones:  # an existing apparatus keeps its other zones, points, lines and calibration
            oz = old.zone(z.name)
            if oz is not None:
                oz.shape = z.shape
            else:
                z.color = ZONE_COLORS[len(old.zones) % len(ZONE_COLORS)]
                old.zones.append(z)
        old.frame_size = old.frame_size or app.frame_size
        out.append(old)
    return out
