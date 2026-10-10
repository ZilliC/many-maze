"""Saved results reports (ANY-maze: the reports of the Results page): named selections of the Data page's
spreadsheet — the measures and information columns shown, whether the time periods are shown, and the rows shown
(one time period, one treatment, one stage). They are kept in the experiment (``Project.reports``, a list of the
dicts below); one of them may be the default report, which the Data page shows when the experiment is opened.
The spreadsheet exports and the HTML report follow the report shown, and ``manymaze project DIR results --report
NAME`` exports one from the command line.

A report::

    {"name": "Open arms", "measures": ["Open arms: time (%)", ...], "info_columns": ["Test", "Animal", ...],
     "segmented": False, "period": "", "treatment": "", "stage": "", "default": False}

measures / info_columns: the columns shown, in the spreadsheet's order (None: every measure / the usual information
columns; measures that appear later, e.g. those of a new zone, are not added to a report that lists its measures);
period, treatment, stage: show only these rows ("" = all; the period only with ``segmented``).
"""

from __future__ import annotations

SEGMENT_COLUMNS = ("Period", "Segment of test")  # information columns that only mean something with time periods
WHOLE = "Whole test"
FIELDS = ("name", "measures", "info_columns", "segmented", "period", "treatment", "stage", "default")


def report_from(d) -> dict | None:
    """A report as stored in project.json, cleaned (None for anything that is not one)."""
    if not isinstance(d, dict) or not str(d.get("name", "") or "").strip():
        return None

    def names(v):
        return [str(x) for x in v if isinstance(x, (str, int, float))] if isinstance(v, (list, tuple)) else None

    return {"name": str(d["name"]).strip(), "measures": names(d.get("measures")),
            "info_columns": names(d.get("info_columns")), "segmented": bool(d.get("segmented", False)),
            "period": str(d.get("period") or ""), "treatment": str(d.get("treatment") or ""),
            "stage": str(d.get("stage") or ""), "default": bool(d.get("default", False))}


def reports_from(items) -> list[dict]:
    """The reports of project.json (malformed entries and repeated names are left out; at most one default)."""
    out, seen, default = [], set(), False
    for d in items if isinstance(items, list) else []:
        r = report_from(d)
        if r is None or r["name"] in seen:
            continue
        seen.add(r["name"])
        r["default"], default = r["default"] and not default, default or r["default"]
        out.append(r)
    return out


def find_report(reports: list[dict], name: str) -> dict | None:
    return next((r for r in reports or [] if r.get("name") == name), None)


def default_report(reports: list[dict]) -> dict | None:
    """The report the Data page shows when the experiment is opened, or None."""
    return next((r for r in reports or [] if r.get("default")), None)


def set_default(reports: list[dict], name: str | None):
    """Make the report called `name` the default (None: no default report)."""
    for r in reports or []:
        r["default"] = name is not None and r.get("name") == name


def report_rows(report: dict, rows: list[dict]) -> list[dict]:
    """The results rows a report shows: the whole tests (or, with time periods, every period or the report's
    period), of the report's treatment and stage."""
    seg = bool(report.get("segmented"))
    period = report.get("period") or ""
    out = []
    for r in rows:
        p = str(r.get("Period", WHOLE) or WHOLE)
        if (not seg and p != WHOLE) or (seg and period and p != period):
            continue
        if report.get("treatment") and str(r.get("Group", "")) != report["treatment"]:
            continue
        if report.get("stage") and str(r.get("Stage", "")) != report["stage"]:
            continue
        out.append(r)
    return out


def report_columns(report: dict | None, rows: list[dict], info: list[str], optional: tuple | list = ()) -> list[str]:
    """The columns a report shows for these rows, as the Data page shows them: its information columns that have a
    value (Test and Animal always; Period and Segment of test with time periods), then its measures, in the order of
    the results. info: the information columns (Project.info_columns); optional: those not shown unless the report
    lists them (OPTIONAL_INFO_COLUMNS). report None: every measure and the usual information columns."""
    report = report or {}
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    seg = bool(report.get("segmented"))
    want_info, want = report.get("info_columns"), report.get("measures")
    shown = []
    for c in info:
        if c not in cols or (c in SEGMENT_COLUMNS and not seg):
            continue
        if c not in ("Test", "Animal") and not any(str(r.get(c, "")).strip() for r in rows):
            continue
        if (c not in want_info) if want_info is not None else (c in optional):
            continue
        shown.append(c)
    info_set = set(info)
    measures = [c for c in cols if c not in info_set]
    if want is not None:
        keep = set(want)
        measures = [m for m in measures if m in keep]
    return shown + measures
