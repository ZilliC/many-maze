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

from .freezing import fst_states
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
    d.res["Choice latency (s)"] = _r(seq[0][1] - d.k.t0 if seq else d.never())
    d.res["Arm alternations"] = sum(1 for i in range(len(seq) - 1) if seq[i][0] != seq[i + 1][0])


def _water_maze(d: TemplateData):
    res, app, s, k = d.res, d.app, d.s, d.k
    t, dur, u = k.t, k.dur, app.unit
    plat = app.zone("Platform")
    pc = app.point("Platform centre")
    if plat is not None:
        # the platform entries as the zone measures count them (minimum entry duration, initial entry rule)
        stop = _found(d, "Platform")
        res["Escape latency (s)"] = _r(float(t[stop] - k.t0) if stop is not None else d.never())
        res["Found platform"] = "Yes" if stop is not None else "No"
        res[f"Path length to platform ({u})"] = _r(k.step[:(stop if stop is not None else len(t)) + 1].sum(), 2)
        # crossings: the entries, without one at the start of the test (the animal released on the platform)
        res["Platform crossings"] = d.zent("Platform") - (1 if d.initial and stop == 0 and s.count_initial_entry
                                                         else 0)
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


def _found(d: TemplateData, zone: str) -> int | None:
    """Frame (in the period) of the first entry into the zone as the zone measures count entries, or None."""
    seq = d.seq([zone])
    return int(np.searchsorted(d.k.t, seq[0][1] - 1e-9)) if seq else None


def _whishaw(d: TemplateData, pc):
    """Whishaw's corridor: a band from the release point (first position or a "Release point" point) to the
    platform; reports how much of the swim to the platform stayed inside it."""
    res, app, k = d.res, d.app, d.k
    stop = _found(d, "Platform") if d.app.zone("Platform") is not None else None
    w = whishaw_corridor(k, app, d.s, pc.x, pc.y, stop if stop is not None else len(k.t) - 1)
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
        stop = np.searchsorted(k.t, seq[first][1] - 1e-9)
        res[f"Primary path length ({app.unit})"] = _r(k.step[:stop + 1].sum(), 2)
    else:
        res["Primary latency (s)"] = _r(d.never())
        res["Primary errors"] = len(names)
        res[f"Primary path length ({app.unit})"] = _r(k.step.sum(), 2)
    res["Total errors"] = sum(1 for n in names if n != esc_name)
    res["Escape hole visits"] = sum(1 for n in names if n == esc_name)
    res["Hole visit sequence"] = " ".join(n.split()[-1] for n in names)
    s = d.s
    method = s.barnes_strategy_method if s.barnes_strategy_method in BARNES_METHODS else "simple"
    target = _hole_number(esc_name)
    numbers = [_hole_number(n) for n in holes]
    if method == "simple" or target is None or None in numbers:
        res["Search strategy"] = classify_barnes_strategy(names, esc_name, len(holes))
        return
    visits = [("hole", _hole_number(zn), t_in) for zn, t_in, _ in seq]
    centre = s.barnes_centre_zone.strip()
    if centre and centre not in holes:
        visits += [("centre", None, t_in) for _, t_in, _ in d.seq([centre])]
    visits.sort(key=lambda v: v[2])
    rules = BarnesRules(max(len(holes), max(numbers)), target, s.barnes_target_region, s.barnes_serial_visits,
                        s.barnes_serial_skip)
    if method == "classic":
        res.update(barnes_classic(visits, rules))
    else:
        res.update(barnes_unmc(visits, rules, k.t0, k.t0 + k.duration, d.never()))


# Barnes maze search strategy (AnalysisSettings.barnes_strategy_method), with ANY-maze's names Direct, Serial and
# Random. "simple" is mANY-MAZE's earlier rule (classify_barnes_strategy). "classic" is ANY-maze's Barnes maze
# strategy analysis as revised in 7.54 after Gawel et al. 2018 (help topic T1462): one overall strategy for the
# test and a primary one up to the first visit to the escape hole. "unmc" is ANY-maze's UNMC method (University of
# Nebraska Medical Center, T1463): the strategies used in turn, the analysis starting again whenever the animal
# finds the escape hole. Holes are numbered round the maze ("Hole 1" … "Hole N"); the hole and centre visits are
# the zone entries (holes by the head when it is tracked).
BARNES_METHODS = {"simple": "Simple: from the holes visited before the escape hole",
                  "classic": "ANY-maze (Gawel et al. 2018): overall and primary strategy",
                  "unmc": "UNMC method: the strategies used in turn"}
BARNES_STRATEGIES = ("Direct", "Serial", "Random")


def _hole_number(name: str | None) -> int | None:
    """The number of a hole zone ("Hole 7" -> 7), None if its name does not end with one."""
    tail = (name or "").split()[-1:]
    return int(tail[0]) if tail and tail[0].isdigit() else None


@dataclass
class BarnesRules:
    """The settings of ANY-maze's Barnes maze strategy analysis (help topic T1434)."""

    n_holes: int
    target: int  # the escape hole
    region: int = 2  # the target region: the escape hole and this many holes either side of it
    serial_visits: int = 3  # consecutive hole visits that start a serial strategy
    skip: int = 1  # holes the animal may skip between two visits of a serial strategy

    def gap(self, a: int, b: int) -> int:
        """Holes from a to b the shorter way round."""
        return min((a - b) % self.n_holes, (b - a) % self.n_holes)

    def step(self, a: int, b: int) -> int | None:
        """A move from hole a to hole b: 1 (up the numbers) / -1 (down) when it can continue a serial strategy (to
        one of the next skip + 1 holes), 0 back to the same hole, None otherwise."""
        g = self.gap(a, b)
        if g == 0:
            return 0
        if g > max(0, self.skip) + 1:
            return None
        return 1 if (b - a) % self.n_holes == g else -1

    def in_region(self, hole: int) -> bool:
        return self.gap(hole, self.target) <= self.region


def _moves(holes: list[int]) -> int:
    """Holes visited one after another (going back into the same hole is not a new one)."""
    return 1 + sum(1 for a, b in zip(holes, holes[1:]) if a != b) if holes else 0


def _direct(visits: list, r: BarnesRules) -> bool:
    """Visits [(kind, hole, t)] from the first hole visit that reach the escape hole without a hole outside its
    target region or the centre."""
    return any(kind == "hole" and h == r.target for kind, h, _ in visits) and \
        all(kind == "hole" and r.in_region(h) for kind, h, _ in visits)


def classic_strategy(visits: list, r: BarnesRules) -> str:
    """ANY-maze's strategy (T1462) of hole and centre visits [(kind "hole" | "centre", hole, t)] in time order.
    Direct: to the escape hole within its target region, without the centre. Serial: one serial strategy from the
    first hole visit to the end: every move to one of the next holes (skip + 1) either way, reversals allowed (as
    1, 2, 3, 2, 1), at least serial_visits holes, no centre ("entering the centre always breaks a serial
    strategy"). Random: the rest, and an animal that never finds the escape hole. "None" without a hole visit."""
    first = next((i for i, v in enumerate(visits) if v[0] == "hole"), None)
    if first is None:
        return "None"
    visits = visits[first:]  # the centre where the animal starts the test does not count
    if not any(kind == "hole" and h == r.target for kind, h, _ in visits):
        return "Random"
    if _direct(visits, r):
        return "Direct"
    holes = [h for kind, h, _ in visits if kind == "hole"]
    serial = len(holes) == len(visits) and _moves(holes) >= max(2, r.serial_visits) and \
        all(r.step(a, b) is not None for a, b in zip(holes, holes[1:]))
    return "Serial" if serial else "Random"


def _barnes_errors(holes: list[int], target: int) -> tuple[int, int, int]:
    """(reference, working, perseverative) errors of hole visits in order (ANY-maze T1464): visits to a hole other
    than the escape hole, visits to such a hole already visited, and visits to the hole visited just before."""
    seen: set = set()
    ref = work = pers = 0
    for i, h in enumerate(holes):
        if h != target:
            ref += 1
            work += h in seen
        pers += i > 0 and holes[i - 1] == h
        seen.add(h)
    return ref, work, pers


def barnes_classic(visits: list, r: BarnesRules) -> dict:
    """ANY-maze's Barnes maze strategy analysis measures (T1464): the overall strategy (the "Search strategy"), the
    primary strategy (up to the first visit to the escape hole), the errors and the hole deviation score. The
    errors and the score are undefined without any hole visit, the primary ones without a visit to the escape
    hole."""
    holes = [h for kind, h, _ in visits if kind == "hole"]
    found = next((i for i, v in enumerate(visits) if v[0] == "hole" and v[1] == r.target), None)
    nan = (math.nan,) * 3
    out = {"Search strategy": classic_strategy(visits, r),
           "Primary strategy": classic_strategy(visits[:found + 1], r) if found is not None else "None"}
    tot = _barnes_errors(holes, r.target) if holes else nan
    out["Total reference errors"], out["Total working errors"], out["Total perseverative errors"] = tot
    out["Hole deviation score"] = r.gap(holes[0], r.target) if holes else math.nan
    prim = _barnes_errors([h for kind, h, _ in visits[:found] if kind == "hole"], r.target) \
        if found is not None else nan
    out["Primary reference errors"], out["Primary working errors"], out["Primary perseverative errors"] = prim
    return out


def _serial_runs(holes: list[int], r: BarnesRules) -> list[tuple[int, int]]:
    """[first, last] indices of the serial runs of a list of hole visits (UNMC method): moves to one of the next
    holes in one direction (going back into the same hole does not end them), at least serial_visits holes. A
    reversal ends a run, and the next one may start from the hole where the animal turned."""
    out, start, direction = [], 0, 0
    for i in range(1, len(holes) + 1):
        st = r.step(holes[i - 1], holes[i]) if i < len(holes) else None
        if st is not None and (st == 0 or direction in (0, st)):
            direction = direction or st
            continue
        if _moves(holes[start:i]) >= max(2, r.serial_visits):
            out.append((start, i - 1))
        start, direction = (i - 1, st) if st else (i, 0)
    return out


def unmc_strategies(visits: list, r: BarnesRules, t0: float) -> list[tuple[str, float, int]]:
    """The UNMC method's strategies in turn [(strategy, start time, Direct errors)] from hole and centre visits
    [(kind, hole, t)] in time order (ANY-maze T1463). The analysis starts again at every visit to the escape hole,
    which ends one stretch of visits and starts the next; a stretch's first strategy starts with it (at t0, the
    start of the test, or on that visit). Direct (the first stretch only): to the escape hole within its target
    region, without the centre; its errors are the visits to the other holes of the region. Serial: the serial
    runs (_serial_runs), one strategy while a run follows another at once ("visits to holes 1, 2, 3 and then to
    holes 14, 15, 16" are one). Random: the other visits, from the centre entry that ended a serial strategy, else
    from the first of them. Consecutive uses of one strategy are one use."""
    holes = [(i, v[1], v[2]) for i, v in enumerate(visits) if v[0] == "hole"]
    cuts = [j for j, (_, h, _) in enumerate(holes) if h == r.target]
    stretches, a = [], 0
    for c in cuts:
        stretches.append((a, c, True))
        a = c
    if not cuts or a < len(holes) - 1:
        stretches.append((a, len(holes) - 1, False))
    out: list[list] = []

    def use(strategy, t, errors=0):
        if out and out[-1][0] == strategy:
            out[-1][2] += errors
        else:
            out.append([strategy, t, errors])

    def random_from(part, j):  # the centre entry after the previous hole visit, else this visit
        return next((visits[i][2] for i in range(part[j - 1][0] + 1, part[j][0]) if visits[i][0] == "centre"),
                    part[j][2])

    for n, (a, b, to_target) in enumerate(stretches if holes else []):
        part = holes[a:b + 1]
        start = t0 if n == 0 else part[0][2]
        if n == 0 and to_target and _direct(visits[part[0][0]:part[-1][0] + 1], r):
            use("Direct", start, sum(1 for _, h, _ in part if h != r.target and r.in_region(h)))
            continue
        runs_: list[list[int]] = []
        for ra, rb in _serial_runs([h for _, h, _ in part], r):
            if runs_ and ra <= runs_[-1][1] + 1:
                runs_[-1][1] = max(runs_[-1][1], rb)
            else:
                runs_.append([ra, rb])
        first, last = (1 if n else 0), len(part) - (1 if to_target else 0)  # not just the escape-hole visits
        j = 0
        for ra, rb in runs_:
            if max(j, first) < min(ra, last):
                use("Random", start if j == 0 else random_from(part, j))
            use("Serial", start if ra == 0 else part[ra][2])
            j = rb + 1
        if max(j, first) < last:
            use("Random", start if j == 0 else random_from(part, j))
    return [(st, t, e) for st, t, e in out]


def barnes_unmc(visits: list, r: BarnesRules, t0: float, t1: float, never: float) -> dict:
    """The UNMC method's measures (ANY-maze T1465) of the period [t0, t1]: the initial strategy (also the "Search
    strategy"), the list, and per strategy the number of times used, the latency and the time using it (a
    strategy lasts until the next starts or the period ends); Direct also its errors."""
    used = unmc_strategies(visits, r, t0)
    out = {"Search strategy": used[0][0] if used else "None",
           "Initial strategy used": used[0][0] if used else "None",
           "List of strategies used": ", ".join(st for st, _, _ in used)}
    ends = [t for _, t, _ in used[1:]] + [t1]
    for name in BARNES_STRATEGIES:
        mine = [(t, end, e) for (st, t, e), end in zip(used, ends) if st == name]
        out[f"{name} strategy - number times used"] = len(mine)
        if name == "Direct":
            out["Direct strategy - errors"] = sum(e for _, _, e in mine)
        else:
            out[f"{name} strategy - latency (s)"] = _r(mine[0][0] - t0 if mine else never)
        out[f"{name} strategy - time using (s)"] = _r(sum(max(0.0, end - t) for t, end, _ in mine))
    return out


def classify_barnes_strategy(names: list[str], esc: str | None, n_holes: int) -> str:
    """The "simple" search strategy: Direct with at most 2 holes visited before the escape hole, Serial when at
    least 60 % of the moves between the holes visited before it are to an adjacent hole, else Random."""
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
    """Forced swim (Porsolt) and tail suspension tests. Time immobile, immobile episodes, latency to first immobility
    and the immobile episode durations are the general measures, which come from the struggle seen in the image when
    immobility_mode is "motion" (as the FST / TST templates set it; freezing.immobility_from_motion). Added here:
    the time immobile as a % of the period and, in the forced swim test with the three-state split, the time
    climbing and swimming (freezing.fst_states). An episode under way at the start of a later period is not one of
    its episodes."""
    res, k, s = d.res, d.k, d.s
    T = k.duration
    if "Time immobile (s)" in res:
        res["Time immobile (%)"] = _r(100 * res["Time immobile (s)"] / T if T > 0 else math.nan, 2)
    if d.app.template != "forced_swim" or not s.fst_three_state or k.struggle is None:
        return
    for name, m in zip(("climbing", "swimming"), fst_states(k.mobile, k.struggle, k.t, k.dur, s)):
        ep = [r for r in runs(m) if r[0] > 0 or d.initial]
        tm = float(k.dur[m].sum())
        res[f"Time {name} (s)"] = _r(tm)
        res[f"Time {name} (%)"] = _r(100 * tm / T if T > 0 else math.nan, 2)
        res[f"{name.capitalize()} episodes"] = len(ep)
        res[f"Latency to first {name} (s)"] = _r(float(k.t[ep[0][0]] - k.t0) if ep else d.never())


TEMPLATE_MEASURES: dict[str, tuple[Callable[[TemplateData], None], ...]] = {
    "open_field": (_grid,), "custom": (_grid,), "novel_object": (_grid, _novel_object),
    "epm": (_plus_maze,), "ezm": (_plus_maze,),
    "y_maze": (_y_maze,), "radial_arm_maze": (_radial_arm_maze,), "rapc": (_rapc,), "t_maze": (_t_maze,),
    "water_maze": (_water_maze,), "barnes_maze": (_barnes_maze,),
    "light_dark": (_light_dark,), "three_chamber": (_three_chamber,), "novel_tank": (_novel_tank,), "cpp": (_cpp,),
    "hole_board": (_hole_board,), "thermal_gradient": (_thermal_gradient,), "activity_wheel": (_activity_wheel,),
    "forced_swim": (_forced_swim,), "tail_suspension": (_forced_swim,),
}
# apparatus templates (and protocol types) of the forced swim / tail suspension family: their protocols detect
# immobility from the struggle in the image (AnalysisSettings.immobility_mode "motion")
FST_TEMPLATES = ("forced_swim", "tail_suspension")


def template_measures(d: TemplateData):
    """Add the measures of the apparatus template to d.res."""
    for fn in TEMPLATE_MEASURES.get(d.app.template, ()):
        fn(d)
