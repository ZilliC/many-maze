"""Test-specific measures chosen by the apparatus template (EPM, Y maze, water maze, Barnes maze, NOR, ...).

``TEMPLATE_MEASURES`` maps a template key to the functions that add its measures, in order; each receives a
:class:`TemplateData` and adds its measures to ``d.res`` (which already holds the general, zone and point
measures of the analysed period).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .geometry import point_segment_distance
from .series import count_rotations, drop_short_runs, ffill, round_result as _r, runs


@dataclass
class TemplateData:
    """The analysed period, for the template measures."""

    res: dict  # the results so far (measures are added to it)
    track: object  # Track of the period
    app: object  # Apparatus
    s: object  # AnalysisSettings
    k: object  # Kinematics of the period
    memb: dict  # zone / group membership per frame
    head_memb: dict | None
    seq: Callable  # seq(zone names, body part=None) -> [(zone, t_enter, t_exit)] entries in the period
    initial: bool  # the period starts at the start of the test

    def ztime(self, name) -> float:
        return float(self.res.get(f"{name}: time (s)", 0) or 0)

    def zent(self, name) -> int:
        return int(self.res.get(f"{name}: entries", 0) or 0)

    def never(self) -> float:
        """Latency when something never happens."""
        return self.k.duration if self.s.latency_if_never == "duration" else math.nan


def _grid(d: TemplateData):
    """Crossings of an N×N grid laid over the arena."""
    s, k, app = d.s, d.k, d.app
    if not (s.grid_cells and s.grid_cells > 1 and app.arena is not None):
        return
    x0, y0, x1, y1 = app.arena.bounds()
    n = int(s.grid_cells)
    gx = np.clip(((k.x - x0) / max(x1 - x0, 1e-9) * n).astype(int), 0, n - 1)
    gy = np.clip(((k.y - y0) / max(y1 - y0, 1e-9) * n).astype(int), 0, n - 1)
    cell = gx * n + gy
    moved = np.diff(cell) != 0
    if k.breaks is not None and len(moved):
        moved &= ~k.breaks[1:]  # not across a pause
    d.res[f"Grid crossings ({n}×{n})"] = int(np.count_nonzero(moved))
    inner = (gx > 0) & (gx < n - 1) & (gy > 0) & (gy < n - 1)
    T = k.duration
    d.res["Inner grid squares time (%)"] = _r(100 * k.dur[inner].sum() / T if T > 0 else math.nan, 2)


def _plus_maze(d: TemplateData):
    """Elevated plus maze (arms) and elevated zero maze (quadrants)."""
    res, app, tpl = d.res, d.app, d.app.template
    o, c = ("Open arms", "Closed arms") if tpl == "epm" else ("Open quadrants", "Closed quadrants")
    to, tc = d.ztime(o), d.ztime(c)
    eo, ec = d.zent(o), d.zent(c)
    # count arm entries per arm (entries into the group may merge adjacent arms)
    arms_o = app.group(o).zones if app.group(o) else []
    arms_c = app.group(c).zones if app.group(c) else []
    eo = sum(d.zent(a) for a in arms_o) or eo
    ec = sum(d.zent(a) for a in arms_c) or ec
    lbl = "arm" if tpl == "epm" else "quadrant"
    res[f"Open {lbl} time (%)"] = _r(100 * to / (to + tc) if to + tc > 0 else math.nan, 2)
    res[f"Open {lbl} entries (%)"] = _r(100 * eo / (eo + ec) if eo + ec > 0 else math.nan, 2)
    res[f"Total {lbl} entries"] = eo + ec
    res[f"Open {lbl} entries"] = eo
    res[f"Closed {lbl} entries"] = ec
    # head dips approximation: head outside the apparatus while body in an open arm
    if d.head_memb is not None and app.arena is not None:
        t, dur = d.k.t, d.k.dur
        head_out = ~app.arena.contains(ffill(d.track.hx), ffill(d.track.hy))
        body_open = d.memb.get(o, np.zeros(len(t), bool))
        res["Head dips"] = len(runs(drop_short_runs(head_out & body_open, t, dur, 0.2, value=True)))


def _y_maze(d: TemplateData):
    arms = [z.name for z in d.app.zones if z.name.startswith("Arm")]
    seq = [e[0] for e in d.seq(arms)]
    # consecutive entries into the same arm are kept (a short gap can split a visit): they break an alternation
    # triplet and are counted as same arm returns
    res = d.res
    res["Arm entry sequence"] = "".join(a.split()[-1] for a in seq)
    n = len(seq)
    alt = sum(1 for i in range(n - 2) if len({seq[i], seq[i + 1], seq[i + 2]}) == 3)
    res["Total arm entries"] = n
    res["Spontaneous alternations"] = alt
    res["Alternation (%)"] = _r(100 * alt / (n - 2) if n > 2 else math.nan, 2)
    res["Same arm returns"] = sum(1 for i in range(n - 1) if seq[i] == seq[i + 1])
    res["Alternate arm returns"] = sum(1 for i in range(n - 2) if seq[i] == seq[i + 2] and seq[i] != seq[i + 1])


def _radial_arm_maze(d: TemplateData):
    arms = [z.name for z in d.app.zones if z.name.startswith("Arm")]
    seq = [e[0] for e in d.seq(arms)]
    visited = set()
    errors = 0
    first_err = None
    for i, a in enumerate(seq):
        if a in visited:
            errors += 1
            if first_err is None:
                first_err = i
        visited.add(a)
    res = d.res
    res["Total arm entries"] = len(seq)
    res["Different arms visited"] = len(visited)
    res["Working memory errors (re-entries)"] = errors
    res["Correct entries before first error"] = first_err if first_err is not None else len(seq)
    res[f"Different arms in first {len(arms)} entries"] = len(set(seq[: len(arms)]))
    res["Arm entry sequence"] = " ".join(a.split()[-1] for a in seq)
    all_idx = None
    seen = set()
    for i, a in enumerate(seq):
        seen.add(a)
        if len(seen) == len(arms):
            all_idx = i
            break
    res["Entries to visit all arms"] = (all_idx + 1) if all_idx is not None else math.nan


def _rapc(d: TemplateData):
    """Radial arm place conditioning. Arm entries in order; baited arms are the "Baited arms" group (all arms when
    there is no such group). Type 1 error (working memory): re-entry into a baited arm already entered in the
    period. Type 2 error (reference memory): any entry into an arm that is not baited. The door sequence lists the
    doors (arm numbers) the animal went through, in order."""
    arms = [z.name for z in d.app.zones if z.name.startswith("Arm")]
    g = d.app.group("Baited arms")
    baited = [a for a in (g.zones if g is not None else arms) if a in arms]
    seq = [e[0] for e in d.seq(arms)]
    seen: set = set()
    t1 = t2 = 0
    first_err = None
    for i, a in enumerate(seq):
        err = a not in baited or a in seen
        if a not in baited:
            t2 += 1
        elif a in seen:
            t1 += 1
        if err and first_err is None:
            first_err = i
        seen.add(a)
    res = d.res
    res["Type 1 errors"] = t1
    res["Type 2 errors"] = t2
    res["Total errors"] = t1 + t2
    res["Door sequence"] = " ".join(a.split()[-1] for a in seq)
    res["Total arm entries"] = len(seq)
    res["Baited arms visited"] = len(seen & set(baited))
    res["Correct entries before first error"] = first_err if first_err is not None else len(seq)
    got, all_idx = set(), None
    for i, a in enumerate(seq):
        if a in baited:
            got.add(a)
        if baited and len(got) == len(baited):
            all_idx = i
            break
    res["Entries to visit all baited arms"] = (all_idx + 1) if all_idx is not None else math.nan


def _t_maze(d: TemplateData):
    seq = d.seq(["Left arm", "Right arm"])
    d.res["First choice"] = seq[0][0].split()[0] if seq else "None"
    d.res["Choice latency (s)"] = _r(seq[0][1] - d.k.t0 if seq else d.k.duration)
    d.res["Arm alternations"] = sum(1 for i in range(len(seq) - 1) if seq[i][0] != seq[i + 1][0])


def _water_maze(d: TemplateData):
    res, app, s, k = d.res, d.app, d.s, d.k
    t, dur, T, u = k.t, k.dur, k.duration, app.unit
    plat = app.zone("Platform")
    pc = app.point("Platform centre")
    if plat is not None:
        inp = d.memb.get("Platform", np.zeros(len(t), bool))
        idx = np.flatnonzero(inp)
        res["Escape latency (s)"] = _r(float(t[idx[0]] - k.t0) if len(idx) else T)
        res["Found platform"] = "Yes" if len(idx) else "No"
        stop = idx[0] if len(idx) else len(t)
        res[f"Path length to platform ({u})"] = _r(k.step[:stop + 1].sum(), 2)
        res["Platform crossings"] = d.zent("Platform") - (1 if d.initial and inp[0] and s.count_initial_entry else 0)
        others = [z.name for z in app.zones if z.name.startswith("Platform position")]
        if others:
            res["Mean crossings of other platform positions"] = _r(np.mean([d.zent(o) for o in others]), 2)
    if pc is not None:
        dist = np.hypot((k.x - pc.x) * k.scale, (k.y - pc.y) * k.scale)
        res[f"Mean distance to platform ({u})"] = _r(np.nanmean(dist), 2)
        res[f"Cumulative distance to platform ({u}·s)"] = _r(np.nansum(dist * dur), 1)
        # initial heading error: direction from start to position after ~1 s vs direction to platform
        ok = np.flatnonzero(np.isfinite(k.ux))
        if len(ok) > 2:
            i0 = ok[0]
            j = np.searchsorted(t, t[i0] + 1.0)
            j = min(max(j, i0 + 1), len(t) - 1)
            hdx, hdy = k.x[j] - k.x[i0], k.y[j] - k.y[i0]
            tdx, tdy = pc.x - k.x[i0], pc.y - k.y[i0]
            if math.hypot(hdx, hdy) > 0 and math.hypot(tdx, tdy) > 0:
                a = math.degrees(math.atan2(hdy, hdx) - math.atan2(tdy, tdx))
                res["Initial heading error (deg)"] = _r(abs((a + 180) % 360 - 180), 1)
        _whishaw(d, pc)
    if app.group("Target quadrant") is not None:
        res["Target quadrant time (%)"] = res.get("Target quadrant: time (%)")
        res["Opposite quadrant time (%)"] = res.get("Opposite quadrant: time (%)")
    if "Thigmotaxis zone: time (%)" in res:
        res["Wall-hugging (%)"] = res["Thigmotaxis zone: time (%)"]
    res["Search strategy"] = classify_water_maze_strategy(res, u)


def _whishaw(d: TemplateData, pc):
    """Whishaw's corridor: a band from the release point (first position or a "Release point" point) to the
    platform; reports how much of the swim to the platform stayed inside it."""
    res, app, k = d.res, d.app, d.k
    inp = d.memb.get("Platform")
    found = np.flatnonzero(inp) if inp is not None else np.zeros(0, int)
    w = whishaw_corridor(k, app, d.s, pc.x, pc.y, found[0] if len(found) else len(k.t) - 1)
    if w is None:
        return
    res["Whishaw corridor time (s)"] = w["time"]
    res["Whishaw corridor time (%)"] = w["time_pct"]
    res["Whishaw corridor path (%)"] = w["path_pct"]
    res[f"Whishaw corridor distance ({app.unit})"] = w["distance"]
    res["Left Whishaw corridor"] = w["left"]


def whishaw_corridor(k, app, s, gx: float, gy: float, stop: int) -> dict | None:
    """Whishaw's corridor from the release point (a "Release point" / "Start" point, else the first position) to the
    goal (gx, gy) px, *s.whishaw_width* wide (0 = 20 cm, or 13 % of the arena when not calibrated), over frames
    from the first position to frame ``stop`` (the arrival): time (s and % of that time), path (% of the distance)
    and distance inside it, and whether the animal left it. None without two positions."""
    ok = np.flatnonzero(np.isfinite(k.x))
    if len(ok) < 2:
        return None
    rp = app.point("Release point") or app.point("Start")
    sx, sy = (rp.x, rp.y) if rp is not None else (k.x[ok[0]], k.y[ok[0]])
    width = s.whishaw_width
    if not width or width <= 0:
        if app.px_per_cm:
            width = 20.0
        else:
            x0, _, x1, _ = app.arena_or_bounds().bounds()
            width = 0.13 * (x1 - x0)
    wpx = width / k.scale
    seg = slice(ok[0], stop + 1)
    dist = point_segment_distance(k.x, k.y, sx, sy, gx, gy)
    inside = (dist <= wpx / 2)[seg]
    dd, st = k.dur[seg], k.step[seg]
    return {"time": _r(dd[inside].sum()),
            "time_pct": _r(100 * dd[inside].sum() / dd.sum() if dd.sum() > 0 else math.nan, 2),
            "path_pct": _r(100 * st[inside].sum() / st.sum() if st.sum() > 0 else math.nan, 2),
            "distance": _r(st[inside].sum(), 2),
            "left": "No" if inside.all() else "Yes"}


def classify_water_maze_strategy(res: dict, u: str) -> str:
    """Very simple heuristic search-strategy classification (Garthe et al.-inspired)."""
    eff = res.get("Path efficiency", math.nan)
    thig = res.get("Wall-hugging (%)", res.get("Thigmotaxis (%)", 0)) or 0
    tq = res.get("Target quadrant time (%)", 0) or 0
    found = res.get("Found platform") == "Yes"
    if found and isinstance(eff, float) and eff >= 0.6:
        return "Direct"
    if thig >= 50:
        return "Thigmotaxis"
    if found and tq >= 50:
        return "Focal search"
    if found and isinstance(eff, float) and eff >= 0.3:
        return "Directed search"
    return "Random / scanning"


def _barnes_maze(d: TemplateData):
    res, app, k = d.res, d.app, d.k
    holes = [z.name for z in app.zones if z.name.startswith("Hole")]
    esc = app.group("Escape hole zone")
    esc_name = esc.zones[0] if esc and esc.zones else None
    part = "head" if d.track.has_head() else d.s.zone_body_part
    seq = d.seq(holes, part)
    names = [e[0] for e in seq]
    if esc_name in names:
        first = names.index(esc_name)
        res["Primary latency (s)"] = _r(seq[first][1] - k.t0)
        res["Primary errors"] = first
        stop = np.searchsorted(k.t, seq[first][1])
        res[f"Primary path length ({app.unit})"] = _r(k.step[:stop + 1].sum(), 2)
    else:
        res["Primary latency (s)"] = _r(k.duration)
        res["Primary errors"] = len(names)
        res[f"Primary path length ({app.unit})"] = _r(k.step.sum(), 2)
    res["Total errors"] = sum(1 for n in names if n != esc_name)
    res["Escape hole visits"] = sum(1 for n in names if n == esc_name)
    res["Hole visit sequence"] = " ".join(n.split()[-1] for n in names)
    res["Search strategy"] = classify_barnes_strategy(names, esc_name, len(holes))


def classify_barnes_strategy(names: list[str], esc: str | None, n_holes: int) -> str:
    if not names:
        return "None"
    if esc is None:
        return "Unknown"
    before = names[:names.index(esc)] if esc in names else names
    if len(before) <= 2:
        return "Direct"
    nums = [int(n.split()[-1]) for n in before]
    adj = sum(1 for a, b in zip(nums, nums[1:]) if min((a - b) % n_holes, (b - a) % n_holes) == 1)
    if len(nums) > 2 and adj / (len(nums) - 1) >= 0.6:
        return "Serial"
    return "Random"


def _novel_object(d: TemplateData):
    res = d.res
    novel = d.s.novel_object
    names = [p.name for p in d.app.points]
    if len(names) < 2:
        return
    fam = [n for n in names if n != novel][0] if novel in names else names[0]
    nov = novel if novel in names else names[1]
    key = "time exploring (s)" if d.track.has_head() else "time near (s)"
    tn = float(res.get(f"{nov}: {key}", 0) or 0)
    tf = float(res.get(f"{fam}: {key}", 0) or 0)
    res["Novel object exploration (s)"] = _r(tn)
    res["Familiar object exploration (s)"] = _r(tf)
    res["Total exploration (s)"] = _r(tn + tf)
    res["Discrimination index"] = _r((tn - tf) / (tn + tf) if tn + tf > 0 else math.nan)
    res["Recognition index (%)"] = _r(100 * tn / (tn + tf) if tn + tf > 0 else math.nan, 2)


def _light_dark(d: TemplateData):
    res = d.res
    dark = d.memb.get("Dark compartment")
    if dark is None:
        return
    res["Latency to enter dark (s)"] = res.get("Dark compartment: latency to first entry (s)")
    light = d.memb.get("Light compartment", ~dark)
    state = np.where(dark, 1, np.where(light, 0, -1))
    st = state[state >= 0]
    res["Transitions"] = int(np.sum(np.diff(st) != 0)) if len(st) > 1 else 0
    res["Time in light (%)"] = res.get("Light compartment: time (%)")


def _three_chamber(d: TemplateData):
    res = d.res
    left_side = d.s.social_side.lower().startswith("l")
    left = float(res.get("Left cup interaction zone: time (s)", 0) or 0)
    right = float(res.get("Right cup interaction zone: time (s)", 0) or 0)
    soc, obj = (left, right) if left_side else (right, left)
    res["Social interaction (s)"] = _r(soc)
    res["Object/empty interaction (s)"] = _r(obj)
    res["Sociability index"] = _r((soc - obj) / (soc + obj) if soc + obj > 0 else math.nan)
    lc, rc = d.ztime("Left chamber"), d.ztime("Right chamber")
    sc, oc = (lc, rc) if left_side else (rc, lc)
    res["Social chamber preference index"] = _r((sc - oc) / (sc + oc) if sc + oc > 0 else math.nan)


def _novel_tank(d: TemplateData):
    res, k, s = d.res, d.k, d.s
    res["Latency to top (s)"] = res.get("Top: latency to first entry (s)")
    res["Top entries"] = d.zent("Top")
    res["Time in top (%)"] = res.get("Top: time (%)")
    res["Time in bottom (%)"] = res.get("Bottom: time (%)")
    tt, tb = d.ztime("Top"), d.ztime("Bottom")
    res["Top/bottom ratio"] = _r(tt / tb if tb > 0 else math.nan)
    if d.app.arena is not None:
        _, y0, _, _ = d.app.arena.bounds()
        res[f"Mean depth ({d.app.unit})"] = _r(np.nanmean((k.y - y0) * k.scale), 2)
    # erratic movements: sharp turns (> 90°) at high speed
    fast = k.speed > 2 * max(np.nanmedian(k.speed[k.mobile]) if k.mobile.any() else 0, s.mobility_threshold)
    turn = np.zeros(len(k.t), bool)
    if len(k.t) > 1:
        dh = np.abs((np.diff(k.heading) + 180) % 360 - 180)
        turn[1:] = np.nan_to_num(dh) > 90
    res["Erratic movements"] = len(runs(turn & fast))


def _cpp(d: TemplateData):
    res = d.res
    chambers = [z.name for z in d.app.zones if z.name.startswith("Chamber")]
    if len(chambers) < 2:
        return
    paired = d.s.paired_chamber if d.s.paired_chamber in chambers else chambers[0]
    unpaired = next(c for c in chambers if c != paired)
    tp, tu = d.ztime(paired), d.ztime(unpaired)
    res["Paired chamber time (s)"] = _r(tp)
    res["Unpaired chamber time (s)"] = _r(tu)
    res["CPP score (s)"] = _r(tp - tu)
    res["Preference index"] = _r((tp - tu) / (tp + tu) if tp + tu > 0 else math.nan)
    res["Paired chamber time (%)"] = _r(100 * tp / (tp + tu) if tp + tu > 0 else math.nan, 2)
    seq = [e[0] for e in d.seq(chambers)]
    res["Chamber transitions"] = sum(1 for a, b in zip(seq, seq[1:]) if a != b)


def _hole_board(d: TemplateData):
    res, k, track = d.res, d.k, d.track
    t, dur = k.t, k.dur
    holes = [p for p in d.app.points if p.name.startswith("Hole")]
    if not holes:
        return
    hx, hy = (ffill(track.hx), ffill(track.hy)) if track.has_head() else (k.x, k.y)
    dips_total, dip_time, first, explored, repeats = 0, 0.0, math.inf, 0, 0
    order = []
    for p in holes:
        dd = np.hypot((hx - p.x) * k.scale, (hy - p.y) * k.scale)
        near = drop_short_runs(dd <= (p.radius_cm or 1.0), t, dur, 0.2, value=True)
        rr = runs(near)
        res[f"{p.name}: head dips"] = len(rr)
        dips_total += len(rr)
        dip_time += float(dur[near].sum())
        if rr:
            explored += 1
            first = min(first, float(t[rr[0][0]] - k.t0))
            order += [(float(t[a]), p.name) for a, _ in rr]
    order.sort()
    seen = set()
    for _, name in order:
        if name in seen:
            repeats += 1
        seen.add(name)
    T = k.duration
    res["Head dips"] = dips_total
    res["Head-dip time (s)"] = _r(dip_time)
    res["Latency to first head dip (s)"] = _r(first if math.isfinite(first) else d.never())
    res["Holes explored"] = explored
    res["Repeated head dips"] = repeats
    res["Head dips (/min)"] = _r(dips_total / (T / 60) if T > 0 else math.nan)


def _thermal_gradient(d: TemplateData):
    sectors = [z.name for z in d.app.zones if z.name.startswith("Sector")]
    if not sectors:
        return
    times = np.array([d.ztime(z) for z in sectors])
    d.res["Preferred sector"] = sectors[int(np.argmax(times))] if times.sum() > 0 else "None"
    idx = np.arange(1, len(sectors) + 1)
    d.res["Mean sector (time-weighted)"] = _r((idx * times).sum() / times.sum() if times.sum() > 0 else math.nan, 2)
    d.res["Sector entries"] = sum(d.zent(z) for z in sectors)


def _activity_wheel(d: TemplateData):
    if d.app.arena is None:
        return
    k, T = d.k, d.k.duration
    cx, cy = d.app.arena.centroid()
    cw, acw = count_rotations(np.degrees(np.arctan2(k.y - cy, k.x - cx)), d.s.rotation_reset_deg)
    d.res["Revolutions clockwise"] = cw
    d.res["Revolutions anticlockwise"] = acw
    d.res["Revolutions (/min)"] = _r((cw + acw) / (T / 60) if T > 0 else math.nan, 2)


def _forced_swim(d: TemplateData):
    res = d.res
    if "Time freezing (s)" in res:
        res["Immobility (s)"] = res["Time freezing (s)"]
        res["Immobility (%)"] = res["Freezing (%)"]
        res["Latency to immobility (s)"] = res["Latency to first freezing (s)"]


TEMPLATE_MEASURES: dict[str, tuple[Callable[[TemplateData], None], ...]] = {
    "open_field": (_grid,), "custom": (_grid,), "novel_object": (_grid, _novel_object),
    "epm": (_plus_maze,), "ezm": (_plus_maze,),
    "y_maze": (_y_maze,), "radial_arm_maze": (_radial_arm_maze,), "rapc": (_rapc,), "t_maze": (_t_maze,),
    "water_maze": (_water_maze,), "barnes_maze": (_barnes_maze,),
    "light_dark": (_light_dark,), "three_chamber": (_three_chamber,), "novel_tank": (_novel_tank,), "cpp": (_cpp,),
    "hole_board": (_hole_board,), "thermal_gradient": (_thermal_gradient,), "activity_wheel": (_activity_wheel,),
    "forced_swim": (_forced_swim,),
}


def template_measures(d: TemplateData):
    """Add the measures of the apparatus template to d.res."""
    for fn in TEMPLATE_MEASURES.get(d.app.template, ()):
        fn(d)
