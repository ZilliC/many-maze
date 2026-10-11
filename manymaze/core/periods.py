"""Event-anchored analysis periods ("the 30 s after the animal first left the start box", "from the first entry into
the open arm until the animal leaves it", "from the time given by a calculation to the end of the test", ...).

A period definition is a dict::

    {"label": "After first exit", "anchor": "first_exit", "zone": "Start box",
     "offset_s": 0, "duration_s": 30, "occurrence": 1}

anchor (where the period starts): "start" (test start) | "first_entry" / "entry" (entry into `zone`) | "first_exit" /
"exit" (exit from `zone`) | "mark" (a manually scored event or procedure mark called `behaviour`) | "input" (an I/O
input `channel` (optionally `device`) switching on) | "calculation" (the time, in seconds from the start of the
test, given by the whole-test result of the calculation whose results column is `calculation`; ANY-maze T0625).
occurrence: which occurrence anchors the period (1 = first, 2 = second, …; 0 = every occurrence, giving one period
each). offset_s is added to the anchor's time. Periods whose anchor never happens are omitted.

The period lasts duration_s (0 = until the end of the test), or ends at an event or a calculation's time::

    "end": {"anchor": "first_entry", "zone": "Goal", "occurrence": 1, "offset_s": 0}

end anchor: "duration" (duration_s, as without "end") | "first_entry" | "first_exit" | "mark" | "input" (the
occurrence-th such event after the period's start event and its start) | "calculation" (the time given by a
calculation, as above), plus offset_s. A period whose end never happens (no such event, an undefined calculation
result) ends at the end of the test and is flagged (:attr:`Period.no_end`), as in ANY-maze; the results say so in
their Warnings column.

A calculation that defines a period must be worked out from the test's own results (not from other trials or the
information columns), and must not use that period itself (result_for_period), directly or through other
calculations: :func:`check_periods` reports both (the period is then left out, or ends at the end of the test).
"""

from __future__ import annotations

import ast
import math
import re
from typing import Callable, NamedTuple

ANCHORS = {
    "start": "Test start",
    "first_entry": "Entry into a zone",
    "first_exit": "Exit from a zone",
    "mark": "Event mark / scored behaviour",
    "input": "I/O input activation",
    "calculation": "Time given by a calculation",
}
END_ANCHORS = {
    "duration": "After the duration",
    "first_entry": "Entry into a zone",
    "first_exit": "Exit from a zone",
    "mark": "Event mark / scored behaviour",
    "input": "I/O input activation",
    "calculation": "Time given by a calculation",
}
TARGET_KEY = {"first_entry": "zone", "entry": "zone", "first_exit": "zone", "exit": "zone", "mark": "behaviour",
              "input": "channel", "calculation": "calculation"}
NO_END_WARNING = "{label}: the end of the period ({end}) did not happen; it ends at the end of the test"
_ZONE_ANCHORS = ("first_entry", "entry", "first_exit", "exit")


class Period(NamedTuple):
    """A resolved time period, in test time. no_end: its end event never happened (it ends at the test end)."""

    label: str
    t0: float
    t1: float
    no_end: bool = False


def _entry_exit_times(memb, t, dur, zone, s, exits: bool) -> list[float]:
    from .occupancy import zone_visits

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


def calculation_value(calc, name: str) -> float | None:
    """The time (s) a calculation gives: calc is {results column: value} or a callable(column) -> value; None when
    the calculation is unknown or its result is undefined or not a number."""
    if calc is None or not name:
        return None
    try:
        v = calc(name) if callable(calc) else calc.get(name)
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def anchor_times(p: dict, track=None, app=None, s=None, events=None, io_events=None, memb=None,
                 calc=None) -> list[float]:
    """All times at which the anchor of period definition p (or of its "end" dict) occurs (test time, sorted).
    calc: the calculations' whole-test results ({column: value} or a callable), for the "calculation" anchor."""
    anchor = (p.get("anchor") or "start").lower()
    if anchor == "start":
        return [0.0]
    if anchor in _ZONE_ANCHORS:
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
    if anchor == "calculation":
        v = calculation_value(calc, str(p.get("calculation") or ""))
        return [] if v is None else [v]
    return []


def _end_of(p: dict) -> dict | None:
    """The period's "end" definition, or None when it lasts duration_s."""
    end = p.get("end")
    if not isinstance(end, dict) or (end.get("anchor") or "duration").lower() in ("duration", "start"):
        return None
    return end


def end_text(end: dict | None) -> str:
    """How the end of a period is described ("exit from Start box", "mark Rearing" …)."""
    if end is None:
        return "after its duration"
    a = (end.get("anchor") or "").lower()
    what = {"first_entry": "entry into", "entry": "entry into", "first_exit": "exit from", "exit": "exit from",
            "mark": "mark", "input": "input", "calculation": "the time given by"}.get(a, a)
    return f"{what} {end.get(TARGET_KEY.get(a, 'zone'), '')}".strip()


def resolve_periods(defs: list, duration: float, track=None, app=None, s=None, events=None, io_events=None,
                    memb=None, calc=None) -> list[Period]:
    """The periods of the event-anchored period definitions in a test of `duration` seconds (see the module's doc;
    memb: the zone occupancy of the track, if already known; calc: the calculations' whole-test results)."""
    out: list[Period] = []
    needs_memb = any(isinstance(p, dict) and ((p.get("anchor") or "").lower() in _ZONE_ANCHORS or
                                              ((_end_of(p) or {}).get("anchor") or "").lower() in _ZONE_ANCHORS)
                     for p in defs or [])
    if needs_memb and memb is None and track is not None and app is not None and len(track):
        from .measures import AnalysisSettings, occupancy

        memb = occupancy(track, app, s or AnalysisSettings())[0]
    for i, p in enumerate(defs or []):
        if not isinstance(p, dict):
            continue
        times = anchor_times(p, track, app, s, events, io_events, memb, calc)
        anchor = (p.get("anchor") or "start").lower()
        occ = 1 if anchor in ("start", "calculation") else int(p.get("occurrence", 1) or 0)
        if occ > 0:
            times = times[occ - 1:occ]
        label = str(p.get("label") or f"Period {i + 1}")
        off = float(p.get("offset_s", 0) or 0)
        d = float(p.get("duration_s", 0) or 0)
        end = _end_of(p)
        ends = anchor_times(end, track, app, s, events, io_events, memb, calc) if end is not None else []
        for k, ta in enumerate(times):
            a = max(0.0, ta + off)
            no_end = False
            if end is None:
                b = min(duration, a + d) if d > 0 else duration
            else:
                if (end.get("anchor") or "").lower() == "calculation":
                    hit = ends[0] if ends else None
                else:  # the occurrence-th end event after the start event and the start of the period
                    after = [t for t in ends if t > max(ta, a) + 1e-9]
                    n = max(1, int(end.get("occurrence", 1) or 1))
                    hit = after[n - 1] if len(after) >= n else None
                if hit is None:
                    b, no_end = duration, True
                else:
                    b = min(duration, hit + float(end.get("offset_s", 0) or 0))
            if b <= a:
                continue
            out.append(Period(label if occ > 0 else f"{label} #{k + 1}", a, b, no_end))
    return out


def event_periods(defs: list, duration: float, track=None, app=None, s=None, events=None,
                  io_events=None, calc=None) -> list[tuple[str, float, float]]:
    """(label, t0, t1) for each event-anchored period definition (see resolve_periods)."""
    return [(p.label, p.t0, p.t1) for p in resolve_periods(defs, duration, track, app, s, events, io_events,
                                                           calc=calc)]


def no_end_warning(p: Period, defs: list) -> str:
    """The note of a period that ended at the test end because its end never happened ("" for the others)."""
    if not p.no_end:
        return ""
    d = next((x for x in defs or [] if isinstance(x, dict) and (str(x.get("label") or "") == p.label or
                                                                p.label.startswith(f"{x.get('label')} #"))), {})
    return NO_END_WARNING.format(label=p.label, end=end_text(_end_of(d)))


# ---------------------------------------------------------------- periods defined by calculations
def calculation_columns(p: dict) -> set[str]:
    """The results columns of the calculations a period definition's start or end uses."""
    out = set()
    for x in (p, p.get("end") if isinstance(p.get("end"), dict) else None):
        if isinstance(x, dict) and (x.get("anchor") or "").lower() == "calculation" and x.get("calculation"):
            out.add(str(x["calculation"]))
    return out


def uses_calculations(defs) -> bool:
    return any(isinstance(p, dict) and calculation_columns(p) for p in defs or [])


def period_names(p: dict, name: str) -> bool:
    """`name` is a label of the periods of definition p (with occurrence 0: "label #k")."""
    label = str(p.get("label") or "")
    if name == label:
        return True
    return int(p.get("occurrence", 1) or 0) == 0 and bool(re.fullmatch(rf"{re.escape(label)} #\d+", name))


def period_calculation_deps(defs) -> Callable[[str], set[str]]:
    """name -> the calculation columns that define the periods called `name` (for a calculation's
    result_for_period('name'): it can only be worked out after them)."""
    defs = [p for p in defs or [] if isinstance(p, dict) and calculation_columns(p)]

    def deps(name: str) -> set[str]:
        out: set[str] = set()
        for p in defs:
            if period_names(p, name):
                out |= calculation_columns(p)
        return out

    return deps


def fixed_period_name(arg_texts: list[str]) -> str | None:
    """The period name of result_for_period's argument texts, when it is a fixed text (else None)."""
    if len(arg_texts) != 1:
        return None
    try:
        v = ast.literal_eval(arg_texts[0])
    except (ValueError, SyntaxError):
        return None
    return v if isinstance(v, str) else None


def check_periods(defs, calculations, info=(), io_only: bool = False) -> list[tuple[str, str]]:
    """Problems of period definitions, as (label, message): a calculation that does not exist, one worked out from
    other trials or the information columns (not known per test), or a circular reference (the calculation uses
    the period itself through result_for_period, directly or through other calculations); with ``io_only`` (tests
    without a track) a start or end at a zone entry or exit, which never happens."""
    from .calculations import in_loop, plan

    calcs = [c for c in calculations or [] if getattr(c, "column", "")]
    cols = {c.column for c in calcs}
    info = set(info)
    loose = {st.calc.column: st for st in plan(calcs, info)}
    tied = {st.calc.column: st for st in plan(calcs, info, defs)}
    out = []
    for i, p in enumerate(defs or []):
        if not isinstance(p, dict):
            continue
        label = str(p.get("label") or f"Period {i + 1}")
        if io_only:
            end = p.get("end") if isinstance(p.get("end"), dict) else {}
            if str(p.get("anchor") or "").lower() in _ZONE_ANCHORS:
                out.append((label, "starts at a zone entry or exit, which input/output only tests (no video) never "
                                   "have: it is left out"))
            if str(end.get("anchor") or "").lower() in _ZONE_ANCHORS:
                out.append((label, "ends at a zone entry or exit, which input/output only tests (no video) never "
                                   "have: it runs to the end of the test"))
        for col in sorted(calculation_columns(p)):
            if col not in cols:
                out.append((label, f"no calculation called “{col}”"))
                continue
            st = loose.get(col)
            if st is not None and st.deferred:
                out.append((label, f"“{col}” uses other trials or information columns (directly or through other "
                                   f"calculations): a time period can only use a calculation worked out from the "
                                   f"test's own results"))
            elif st is not None and st.ok and not tied[col].ok:
                out.append((label, f"circular reference: “{col}” uses this time period (result_for_period), "
                                   f"directly or through other calculations" if in_loop(calcs, col, defs) else
                                   f"“{col}” uses a calculation in a circular reference (its result is blank)"))
    return out


def describe_period(p: dict) -> str:
    """A period definition in words (protocol report)."""
    anchor = (p.get("anchor") or "start").lower()
    target = p.get(TARGET_KEY.get(anchor, "zone"), "")
    start = ANCHORS.get({"entry": "first_entry", "exit": "first_exit"}.get(anchor, anchor), anchor)
    if target and anchor != "start":
        start += f" “{target}”"
    occ = int(p.get("occurrence", 1) or 0)
    if anchor not in ("start", "calculation"):
        start += " (every occurrence)" if occ == 0 else f" (occurrence {occ})"
    off = float(p.get("offset_s", 0) or 0)
    if off:
        start += f" {'+' if off > 0 else '−'} {abs(off):g} s"
    end = _end_of(p)
    if end is None:
        d = float(p.get("duration_s", 0) or 0)
        stop = f"lasts {d:g} s" if d > 0 else "until the end of the test"
    else:
        stop = f"until {end_text(end)}"
        if (end.get("anchor") or "").lower() != "calculation" and int(end.get("occurrence", 1) or 1) > 1:
            stop += f" (occurrence {int(end['occurrence'])})"
        eo = float(end.get("offset_s", 0) or 0)
        if eo:
            stop += f" {'+' if eo > 0 else '−'} {abs(eo):g} s"
        stop += ", else the end of the test"
    return f"Starts at {start}; {stop}"
