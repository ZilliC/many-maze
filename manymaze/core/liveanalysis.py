"""What a live test computes from each frame's detection: the zones the animal is in (entry rules, hidden zones,
orientation), rearing and the running statistics the monitor shows (core.live)."""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from .apparatus import Apparatus
from .freezing import LiveStruggle
from .geometry import body_fraction_inside
from .livemonitor import LivePoints
from .measures import AnalysisSettings
from .tracking import Detection


# ====================================================================== live zone occupancy
class LiveOccupancy:
    """Frame-by-frame zone occupancy with the rules used by the analysis (measures.occupancy): each zone's entry
    rule (centre / head / tail / body proportion with hysteresis / exclusion), investigation distance, hidden zones
    (an animal lost near a hidden zone is in it) and zone groups.  While the animal is not detected the last
    position is kept."""

    def __init__(self, apparatus: Apparatus | None, analysis: AnalysisSettings):
        self.app, self.s = apparatus, analysis
        self._body_state: dict[str, bool] = {}
        self._last: Detection | None = None
        self._hidden: str | None = None  # hidden zone of the current lost episode
        self._lost = False
        self._oriented: dict[str, bool] = {}  # zones entered with the orientation rule (entry_orientation_deg)
        self._angle = math.nan  # last known body orientation
        # for the procedures: zones investigated, angle to each zone / point, events (hidden-zone partial exits)
        self.investigating: dict[str, bool] = {}
        self.orientation: dict[str, float] = {}
        self.events: list[tuple[str, dict]] = []
        self._peek: tuple[str, float] | None = None  # (hidden zone, time seen again) of a possible partial exit
        self._prev_hidden: str | None = None

    def _pos(self, d: Detection, part: str):
        if part == "head" and math.isfinite(d.hx):
            return np.array([d.hx]), np.array([d.hy])
        if part == "tail" and math.isfinite(d.tx):
            return np.array([d.tx]), np.array([d.ty])
        return np.array([d.x]), np.array([d.y])

    def _axes(self, d: Detection):
        L = math.hypot(d.hx - d.tx, d.hy - d.ty) if math.isfinite(d.hx) and math.isfinite(d.tx) else math.nan
        area = d.area if d.area and math.isfinite(d.area) else math.nan
        a = L / 2 if math.isfinite(L) and L > 0 else math.sqrt(2 * area / math.pi) if math.isfinite(area) else math.nan
        b = min(area / (math.pi * a), a) if math.isfinite(area) and a > 0 else a / 2
        return a, b, d.angle if math.isfinite(d.angle) else 0.0

    def update(self, d: Detection, t: float | None = None) -> tuple[dict[str, bool], dict[str, bool]]:
        """(zone and group membership, head membership) for this frame; also updates :attr:`investigating`,
        :attr:`orientation` and :attr:`events` (t: the test time, for the events)."""
        memb, head = self._update(d)
        self.events = []
        if self.app is not None and self._last is not None:
            self._procedure_state(d, t)
        return memb, head

    def _procedure_state(self, d: Detection, t: float | None):
        """Investigation (head in the zone, or within its investigation distance while facing it, as the
        investigation measures), the angle between the body's orientation and the direction to each zone centre
        and point, and hidden-zone partial exits (seen near the hidden zone between two times hidden in it)."""
        app, L, s = self.app, self._last, self.s
        ang = self._angle
        hx, hy = self._pos(L, "head")
        hidden = self._hidden
        for z in app.zones:
            if hidden is not None or not d.detected:
                inv = False
            else:
                inside = bool(z.shape.contains(hx, hy)[0])
                inv = inside
                if not inside and z.investigation_distance_cm and z.investigation_distance_cm > 0:
                    near = float(z.shape.distance_to_edge(hx, hy)[0]) <= z.investigation_distance_cm / app.scale
                    if near and math.isfinite(ang):
                        zx, zy = z.shape.centroid()
                        brg = math.degrees(math.atan2(zy - float(hy[0]), zx - float(hx[0])))
                        near = abs((ang - brg + 180.0) % 360.0 - 180.0) <= s.exploration_facing_deg
                    inv = near
            self.investigating[z.name] = inv
        targets = [(z.name, z.shape.centroid()) for z in app.zones] + [(p.name, (p.x, p.y)) for p in app.points]
        self.orientation = {}
        for name, (px, py) in targets:
            if math.isfinite(ang) and math.isfinite(L.x):
                brg = math.degrees(math.atan2(py - L.y, px - L.x))
                self.orientation[name] = abs((ang - brg + 180.0) % 360.0 - 180.0)
        # partial exits: seen again near the hidden zone, then hidden in it again
        if d.detected:
            if self._prev_hidden is not None:
                self._peek = (self._prev_hidden, t if t is not None else 0.0)
            if self._peek is not None:
                z = app.zone(self._peek[0])
                if z is None or not self._near_hidden(z, L):
                    self._peek = None
        elif self._prev_hidden is None and self._peek is not None:  # lost again
            if self._peek[0] == hidden:
                dur = (t - self._peek[1]) if t is not None else 0.0
                self.events.append(("hidden_partial_exit", {"zone": hidden, "value": round(dur, 3)}))
            self._peek = None
        self._prev_hidden = hidden

    def _hidden_margin(self, z) -> float:
        return (self.s.hidden_zone_margin / self.app.scale if self.s.hidden_zone_margin
                and self.s.hidden_zone_margin > 0 else 0.5 * math.sqrt(max(z.shape.area(), 1.0)))

    def _near_hidden(self, z, L: Detection) -> bool:
        x, y = np.array([L.x]), np.array([L.y])
        return bool(z.shape.contains(x, y)[0]) or float(z.shape.distance_to_edge(x, y)[0]) <= self._hidden_margin(z)

    def _update(self, d: Detection) -> tuple[dict[str, bool], dict[str, bool]]:
        app = self.app
        if app is None:
            return {}, {}
        if d.detected:
            self._last, self._lost, self._hidden = d, False, None
        L = self._last
        if L is None:
            return {}, {}
        s = self.s
        if math.isfinite(L.angle):
            self._angle = L.angle
        zones: dict[str, bool] = {}
        excl = []
        for z in app.zones:
            rule = z.entry_rule or s.zone_body_part or "centre"
            if z.investigation_distance_cm and z.investigation_distance_cm > 0:
                hx, hy = self._pos(L, "head")
                d_px = z.investigation_distance_cm / app.scale
                zones[z.name] = bool(z.shape.contains(hx, hy)[0]) or \
                    bool(z.shape.distance_to_edge(hx, hy)[0] <= d_px)
            elif rule == "body":
                a, b, ang = self._axes(L)
                frac = float(body_fraction_inside(z.shape, [L.x], [L.y], [ang], [a], [b])[0])
                enter = float(min(max(z.body_fraction if z.entry_rule == "body" else s.body_proportion_pct / 100.0,
                                      0.01), 1.0))
                was = self._body_state.get(z.name, False)
                if math.isfinite(frac):
                    was = frac >= enter if not was else (frac >= min(enter, 1.0 - enter) and frac > 0)
                self._body_state[z.name] = was
                zones[z.name] = was
            elif rule == "exclusion":
                excl.append(z)
                zones[z.name] = bool(z.shape.contains(*self._pos(L, "centre"))[0])
            else:
                zones[z.name] = bool(z.shape.contains(*self._pos(L, rule))[0])
            if z.entry_orientation_deg and z.entry_orientation_deg > 0:
                zones[z.name] = self._orientation_rule(z, zones[z.name], L)
        for z in excl:
            for o in app.zones:
                if o is not z and o not in excl and not o.hidden and o.shape.area() < z.shape.area() and zones[o.name]:
                    zones[z.name] = False
        hidden = self._hidden_zone(d)
        if hidden is not None:
            zones = {k: k == hidden for k in zones}
        memb = {k: bool(np.asarray(v).ravel()[0]) for k, v in
                app.combine_groups({k: np.array([v]) for k, v in zones.items()}, (1,)).items()}
        head = {}
        if math.isfinite(L.hx):
            hx, hy = self._pos(L, "head")
            hz = {z.name: (z.name == hidden) if hidden is not None else bool(z.shape.contains(hx, hy)[0])
                  for z in app.zones}
            head = {k: bool(np.asarray(v).ravel()[0]) for k, v in
                    app.combine_groups({k: np.array([v]) for k, v in hz.items()}, (1,)).items()}
        return memb, head

    def _orientation_rule(self, z, inside: bool, L: Detection) -> bool:
        """A visit starts once the animal faces the zone (as occupancy.oriented_visits)."""
        was = self._oriented.get(z.name, False)
        if not inside:
            now = False
        elif was or not math.isfinite(self._angle):
            now = True
        else:
            zx, zy = z.shape.centroid()
            brg = math.degrees(math.atan2(zy - L.y, zx - L.x))
            now = abs((self._angle - brg + 180.0) % 360.0 - 180.0) <= z.entry_orientation_deg
        self._oriented[z.name] = now
        return now

    def _hidden_zone(self, d: Detection) -> str | None:
        if d.detected or self._last is None:
            return None
        if not self._lost:  # new lost episode: the nearest hidden zone around the last seen position
            self._lost = True
            best = None
            x, y = np.array([self._last.x]), np.array([self._last.y])
            for z in self.app.zones:
                if not z.hidden:
                    continue
                margin = self._hidden_margin(z)
                dist = 0.0 if bool(z.shape.contains(x, y)[0]) else float(z.shape.distance_to_edge(x, y)[0])
                if dist <= margin and (best is None or dist < best[0]):
                    best = (dist, z.name)
            self._hidden = best[1] if best else None
        return self._hidden


# ====================================================================== live rearing
class LiveRearing:
    """Rearing frame by frame with the rules of the rearing measures (measures.rearing_mask): the body area falls
    below rear_area_pct % of the animal's usual area and, with head and tail tracked, the body length below
    rear_length_pct % of its usual length; rears shorter than min_rear_s are ignored and gaps up to 0.2 s inside
    a rear are bridged. The usual area / length is the median over the last 30 s of frames that are not rears."""

    BASELINE_S = 30.0
    MIN_BASELINE_S = 1.0
    GAP_S = 0.2

    def __init__(self, analysis: AnalysisSettings, fps: float):
        self.s = analysis
        n = max(10, int(self.BASELINE_S * (fps or 25.0)))
        self._areas: deque = deque(maxlen=n)
        self._lengths: deque = deque(maxlen=n)
        self._fps = fps or 25.0
        self.rearing = False
        self._cand_since: float | None = None
        self._off_since: float | None = None

    def update(self, t: float, d: Detection) -> bool:
        cand = False
        if d.detected and d.area and math.isfinite(d.area):
            L = math.hypot(d.hx - d.tx, d.hy - d.ty) if math.isfinite(d.hx) and math.isfinite(d.tx) else math.nan
            if len(self._areas) >= self.MIN_BASELINE_S * self._fps:
                base_a = float(np.median(self._areas))
                cand = d.area < base_a * self.s.rear_area_pct / 100.0
                if cand and math.isfinite(L) and len(self._lengths) >= self.MIN_BASELINE_S * self._fps:
                    cand = L < float(np.median(self._lengths)) * self.s.rear_length_pct / 100.0
            if not cand and not self.rearing:
                self._areas.append(float(d.area))
                if math.isfinite(L):
                    self._lengths.append(L)
        if cand:
            self._off_since = None
            if self._cand_since is None:
                self._cand_since = t
            if not self.rearing and t - self._cand_since + 1e-9 >= self.s.min_rear_s:
                self.rearing = True
        else:
            self._cand_since = None if not self.rearing else self._cand_since
            if self.rearing:
                if self._off_since is None:
                    self._off_since = t
                if t - self._off_since >= self.GAP_S:
                    self.rearing = False
                    self._cand_since = self._off_since = None
        return self.rearing


# ====================================================================== live statistics
class LiveStats:
    """Running per-zone time / entries / latency, distance, speed, immobility and a short history for charts."""

    CHART_PARAMS = {"speed": "Speed", "distance": "Distance", "motion": "Motion (% body)", "detected": "Detected",
                    "freezing": "Freezing"}

    def __init__(self, apparatus: Apparatus | None, fps: float, analysis: AnalysisSettings,
                 history_s: float = 300.0, sample_s: float = 0.1):
        self.set_scale(apparatus)
        self.names = ([z.name for z in apparatus.zones] + [g.name for g in apparatus.groups]) if apparatus else []
        self.a = analysis
        self.fps = fps or 25.0
        self.zone_time = dict.fromkeys(self.names, 0.0)
        self.entries = dict.fromkeys(self.names, 0)
        self.latency: dict[str, float | None] = dict.fromkeys(self.names, None)
        self.inside = dict.fromkeys(self.names, False)
        self.distance = 0.0
        self.speed = 0.0
        self.motion_pct = 0.0
        self.detected = False
        self.freezing = False
        self.immobile = False
        self.sample_s = sample_s
        self.history: deque = deque(maxlen=int(history_s / sample_s) + 1)  # (t, speed, distance, motion, det, frz)
        self._last_t: float | None = None
        self._last_hist = -1e9
        self._xy = None
        self._slow_since: float | None = None
        self._first = True
        self.visits: list[list] = []  # [zone, t_in, t_out or None] in order: the live sequence statistics
        self._open_visit: dict[str, list] = {}
        self.points = LivePoints(apparatus)
        self.struggle = LiveStruggle() if analysis.immobility_mode == "motion" else None  # forced swim / TST

    def set_scale(self, apparatus: Apparatus | None):
        """Use the apparatus calibration; the distance and speed so far (tracked in pixels) are converted to it."""
        scale = apparatus.scale if apparatus else 1.0
        old = getattr(self, "scale", None)
        if old:
            self.distance *= scale / old
            self.speed *= scale / old
        self.scale = scale
        # distance and speed stay in cm (px) for the procedures and the mobility threshold, and are shown in the
        # apparatus's distance unit: unit, factor (Apparatus.report_unit / report_factor)
        self.unit = apparatus.report_unit if apparatus else "px"
        self.factor = apparatus.report_factor if apparatus else 1.0
        if hasattr(self, "points"):
            self.points.set_apparatus(apparatus)

    def update(self, t: float, d: Detection, zones: dict[str, bool], freezing: bool):
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        self.detected = bool(d.detected)
        if zones:
            for n in self.names:
                now = bool(zones.get(n, False))
                if now and not self.inside[n] and (not self._first or self.a.count_initial_entry):
                    self.entries[n] += 1
                    if self.latency[n] is None:
                        self.latency[n] = t
                if now and not self.inside[n]:
                    self._open_visit[n] = v = [n, t, None]
                    self.visits.append(v)
                elif not now and self.inside[n] and n in self._open_visit:
                    self._open_visit.pop(n)[2] = t
                self.inside[n] = now
            self._first = False
        if d.detected:
            # exponential smoothing ≈ the analysis speed-smoothing window, so jitter is not counted
            n_win = max(1.0, self.a.speed_smoothing_s * self.fps)
            alpha = 2.0 / (n_win + 1.0)
            if self._xy is None:
                self._xy = (d.x, d.y)
                step = 0.0
            else:
                lx, ly = self._xy
                nx, ny = lx + alpha * (d.x - lx), ly + alpha * (d.y - ly)
                step = math.hypot(nx - lx, ny - ly) * self.scale
                self._xy = (nx, ny)
            self.distance += step
            if dt > 0:
                self.speed += alpha * (step / dt - self.speed)
            if d.area and not math.isnan(d.motion):
                self.motion_pct = d.motion / max(d.area, 1) * 100
        for n in self.names:
            if self.inside[n]:
                self.zone_time[n] += dt
        self.points.update(t, dt, d.x, d.y, bool(d.detected))
        self.freezing = bool(freezing)
        if self.struggle is not None:  # forced swim / tail suspension: immobile once the struggle has stopped
            still = self.detected and self.struggle.update(t, self.motion_pct) < self.a.fst_threshold_pct
            min_s = self.a.min_fst_immobile_s
        else:
            still, min_s = self.detected and self.speed < self.a.mobility_threshold, self.a.min_immobile_s
        if still:
            if self._slow_since is None:
                self._slow_since = t
            self.immobile = t - self._slow_since >= min_s
        else:
            self._slow_since = None
            self.immobile = False
        if t - self._last_hist >= self.sample_s:
            self._last_hist = t
            self.history.append((t, self.speed, self.distance, self.motion_pct, float(self.detected),
                                 float(self.freezing)))

    def current_zones(self) -> list[str]:
        return [n for n in self.names if self.inside[n]]

    def rows(self) -> list[tuple[str, float, int, float | None]]:
        """(zone, time s, entries, latency s or None) for every zone and zone group."""
        return [(n, self.zone_time[n], self.entries[n], self.latency[n]) for n in self.names]

    def series(self, param: str, window_s: float = 60.0) -> tuple[np.ndarray, np.ndarray]:
        col = {"speed": 1, "distance": 2, "motion": 3, "detected": 4, "freezing": 5}.get(param, 1)
        if not self.history:
            return np.zeros(0), np.zeros(0)
        h = list(self.history)
        t_end = h[-1][0]
        h = [r for r in h if r[0] >= t_end - window_s]
        v = np.array([r[col] for r in h])
        return np.array([r[0] for r in h]), v * self.factor if col in (1, 2) else v
