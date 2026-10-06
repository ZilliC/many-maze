"""Event-anchored analysis periods ("the 30 s after the animal first left the start box", ...).

A period definition is a dict::

    {"label": "After first exit", "anchor": "first_exit", "zone": "Start box",
     "offset_s": 0, "duration_s": 30, "occurrence": 1}

anchor: "start" (test start) | "first_entry" / "entry" (entry into `zone`) | "first_exit" / "exit" (exit from
`zone`) | "mark" (a manually scored event or procedure mark called `behaviour`) | "input" (an I/O input
`channel` (optionally `device`) switching on). occurrence: which occurrence anchors the period (1 = first,
2 = second, …; 0 = every occurrence, giving one period each). duration_s: 0 = until the end of the test.
Periods whose anchor never happens are omitted.
"""

from __future__ import annotations

ANCHORS = {
    "start": "Test start",
    "first_entry": "Entry into a zone",
    "first_exit": "Exit from a zone",
    "mark": "Event mark / scored behaviour",
    "input": "I/O input activation",
}


def _entry_exit_times(memb, t, dur, zone, s, exits: bool) -> list[float]:
    from .measures import zone_visits

    inside = memb.get(zone)
    if inside is None:
        return []
    out = []
    for a, b in zone_visits(t, dur, inside, s):
        if exits:
            if b < len(t):
                out.append(float(t[b - 1] + dur[b - 1]))
        else:
            if a == 0 and not s.count_initial_entry:
                continue
            out.append(float(t[a]))
    return out


def anchor_times(p: dict, track=None, app=None, s=None, events=None, io_events=None, memb=None) -> list[float]:
    """All times at which the anchor of period definition p occurs (test time, sorted)."""
    anchor = (p.get("anchor") or "start").lower()
    if anchor == "start":
        return [0.0]
    if anchor in ("first_entry", "entry", "first_exit", "exit"):
        if track is None or app is None or not len(track):
            return []
        from .measures import AnalysisSettings, occupancy

        s = s or AnalysisSettings()
        if memb is None:
            memb = occupancy(track, app, s)[0]
        return _entry_exit_times(memb, track.t, track.frame_durations(), p.get("zone", ""), s,
                                 exits=anchor.endswith("exit"))
    if anchor == "mark":
        name = p.get("behaviour") or p.get("mark") or ""
        return sorted(float(e["t"]) for e in (events or []) if e.get("behaviour") == name and "t" in e)
    if anchor == "input":
        ch = str(p.get("channel", ""))
        dev = p.get("device") or None
        out, prev = [], {}
        for e in sorted(io_events or [], key=lambda e: e.get("t", 0)):
            if e.get("kind", "input") != "input" or str(e.get("channel")) != ch:
                continue
            if dev and e.get("device") != dev:
                continue
            key = (e.get("device"), e.get("channel"))
            on = bool(e.get("value"))
            if on and not prev.get(key, False):
                out.append(float(e["t"]))
            prev[key] = on
        return out
    return []


def event_periods(defs: list, duration: float, track=None, app=None, s=None, events=None,
                  io_events=None) -> list[tuple[str, float, float]]:
    """(label, t0, t1) for each event-anchored period definition."""
    out = []
    memb = None
    for i, p in enumerate(defs or []):
        if not isinstance(p, dict):
            continue
        anchor = (p.get("anchor") or "start").lower()
        if anchor in ("first_entry", "entry", "first_exit", "exit") and memb is None and track is not None \
                and app is not None and len(track):
            from .measures import AnalysisSettings, occupancy

            memb = occupancy(track, app, s or AnalysisSettings())[0]
        times = anchor_times(p, track, app, s, events, io_events, memb)
        occ = int(p.get("occurrence", 1) or 0)
        if occ > 0:
            times = times[occ - 1:occ]
        label = str(p.get("label") or f"Period {i + 1}")
        off = float(p.get("offset_s", 0) or 0)
        d = float(p.get("duration_s", 0) or 0)
        for k, ta in enumerate(times):
            a = max(0.0, ta + off)
            b = min(duration, a + d) if d > 0 else duration
            if b <= a:
                continue
            out.append((label if occ > 0 else f"{label} #{k + 1}", a, b))
    return out
