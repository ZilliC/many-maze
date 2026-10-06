"""Export results (CSV / Excel / HTML report) and track data."""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import html
import math
from pathlib import Path

import numpy as np

from .. import __version__
from .project import Project, result_columns


def _fmt(v):
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        return f"{v:.6g}"
    return v


def write_csv(rows: list[dict], path, columns: list[str] | None = None, delimiter=","):
    cols = columns or result_columns(rows)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(cols)
        for r in rows:
            w.writerow([_fmt(r.get(c, "")) for c in cols])


def write_xlsx(sheets: dict[str, list[dict]], path, columns: dict[str, list[str]] | None = None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        cols = (columns or {}).get(name) or result_columns(rows)
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="E11D48")
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c, "")
                if isinstance(v, float) and not math.isfinite(v):
                    v = None
                elif isinstance(v, (np.floating, np.integer)):
                    v = v.item()
                vals.append(v)
            ws.append(vals)
        ws.freeze_panes = "B2"
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = min(40, max(10, len(str(c)) + 2))
    wb.save(path)


def export_results(project: Project, path, segmented: bool = False, columns: list[str] | None = None) -> Path:
    """Export project results; format chosen from the extension (.csv / .xlsx)."""
    path = Path(path)
    rows = project.results(segmented=False)
    if path.suffix.lower() == ".xlsx":
        sheets = {"Results": rows}
        if segmented:
            seg = [r for r in project.results(segmented=True) if r.get("Period") != "Whole test"]
            if seg:
                sheets["Time periods"] = seg
        visits = zone_visit_rows(project)
        if visits:
            sheets["Zone visits"] = visits
        sheets["Animals"] = [{"Animal": a.id, "Group": a.group, "Sex": a.sex, **a.fields} for a in project.animals]
        sheets["Tests"] = [{"Test": t.id, "Animal": t.animal_id, "Stage": t.stage, "Trial": t.trial,
                            "Video": t.video, "Apparatus": t.apparatus, "Start (s)": t.start_s,
                            "Status": t.status, "Notes": t.notes} for t in project.tests]
        sheets["Settings"] = [{"Setting": k, "Value": str(v)} for k, v in
                              {**{f"detection.{k}": v for k, v in project.detection.to_dict().items()},
                               **{f"analysis.{k}": v for k, v in project.analysis.to_dict().items()},
                               "test_duration_s": project.test_duration_s,
                               "software": f"mANY-MAZE {__version__}"}.items()]
        write_xlsx(sheets, path, {"Results": columns} if columns else None)
    else:
        if segmented:
            rows = project.results(segmented=True)
        write_csv(rows, path, columns)
    return path


def zone_visit_rows(project: Project, tests=None) -> list[dict]:
    """One row per zone visit (zone, entry time, exit time, duration) for every tracked test."""
    from .measures import zone_sequence

    rows = []
    for t in tests if tests is not None else project.tests:
        if t.status == "excluded" or not project.has_track(t):
            continue
        app = project.get_apparatus(t.apparatus)
        if app is None or not app.zones:
            continue
        s = project.analysis_for(t)
        tracks = project.load_tracks(t)
        ids = [t.animal_id] + list(t.extra_animals)
        for i, tr in enumerate(tracks):
            for zone, a, b in zone_sequence(tr, app, [z.name for z in app.zones], s):
                rows.append({"Test": t.id, "Animal": ids[i] if i < len(ids) else f"#{i + 1}", "Stage": t.stage,
                             "Zone": zone, "Entry (s)": round(a, 3), "Exit (s)": round(b, 3),
                             "Duration (s)": round(b - a, 3)})
    return rows


def export_track(track, path):
    track.to_csv(path)


def _img(png: bytes, width=320) -> str:
    return f'<img width="{width}" src="data:image/png;base64,{base64.b64encode(png).decode()}">'


def html_report(project: Project, path, tests=None, include_plots: bool = True, measures: list[str] | None = None,
                stats_measures: list[str] | None = None) -> Path:
    """Self-contained HTML report: summary, per-test track plots/heatmaps, results and statistics."""
    from . import plots
    from .stats import compare_groups, group_values, summary_text
    from .video import VideoSource

    tests = tests if tests is not None else [t for t in project.tests if project.has_track(t)]
    rows = []
    for t in tests:
        rows.extend(project.analyse_test(t))
    cols = result_columns(rows)
    if measures:
        info = ["Test", "Animal", "Group", "Stage", "Trial"]
        cols = [c for c in info if c in cols] + [c for c in measures if c in cols]
    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;margin:24px;color:#0f172a}
    h1{color:#e11d48} table{border-collapse:collapse;font-size:12px} td,th{border:1px solid #e2e8f0;padding:3px 6px}
    th{background:#f1f5f9;position:sticky;top:0} .grid{display:flex;flex-wrap:wrap;gap:12px}
    .card{border:1px solid #e2e8f0;border-radius:8px;padding:8px} pre{background:#f8fafc;padding:8px}
    """
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(project.name)}</title>"
           f"<style>{css}</style></head><body>"]
    out.append(f"<h1>{html.escape(project.name)}</h1>")
    out.append(f"<p>{html.escape(project.description)}</p>")
    out.append(f"<p>Generated {_dt.datetime.now():%Y-%m-%d %H:%M} by mANY-MAZE {__version__}. "
               f"{len(tests)} tests, {len(project.animals)} animals.</p>")
    if include_plots and tests:
        out.append("<h2>Tracks</h2><div class='grid'>")
        for t in tests:
            tracks = project.load_tracks(t)
            if not tracks:
                continue
            app = project.get_apparatus(t.apparatus)
            frame = None
            try:
                with VideoSource(project.abs_path(t.video)) as v:
                    frame = v.frame_at(int(t.start_s * v.fps))
            except Exception:
                pass
            title = f"Test {t.id} · {t.animal_id} · {project.get_animal(t.animal_id).group if project.get_animal(t.animal_id) else ''}"
            tp = plots.fig_to_png(plots.track_plot(tracks[0], app, frame=frame, size=(3.2, 3.2)))
            hm = plots.fig_to_png(plots.heatmap(tracks, app, size=(3.6, 3.2)))
            out.append(f"<div class='card'><b>{html.escape(title)}</b><br>{_img(tp, 260)}{_img(hm, 290)}</div>")
        out.append("</div>")
        # group heatmaps
        groups = {}
        for t in tests:
            a = project.get_animal(t.animal_id)
            groups.setdefault(a.group if a else "", []).extend(project.load_tracks(t)[:1])
        if len(groups) > 1:
            out.append("<h2>Group occupancy</h2><div class='grid'>")
            app0 = project.get_apparatus(tests[0].apparatus)
            for g, trs in groups.items():
                out.append(f"<div class='card'><b>{html.escape(g or 'No group')}</b><br>"
                           f"{_img(plots.fig_to_png(plots.heatmap(trs, app0, size=(3.6, 3.2))), 290)}</div>")
            out.append("</div>")
    if stats_measures and rows:
        out.append("<h2>Statistics</h2>")
        colors = {g.name: g.color for g in project.groups}
        for m in stats_measures:
            gv = group_values(rows, m, "Group")
            if len(gv) < 2:
                continue
            res = compare_groups(gv)
            fig = plots.group_plot(gv, m, colors, posthoc=res.get("posthoc"), p_value=res.get("p"))
            out.append(f"<div class='card'>{_img(plots.fig_to_png(fig), 360)}<pre>{html.escape(summary_text(res, m))}"
                       f"</pre></div>")
    out.append("<h2>Results</h2><table><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in cols) + "</tr>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{html.escape(str(_fmt(r.get(c, ''))))}</td>" for c in cols) + "</tr>")
    out.append("</table></body></html>")
    path = Path(path)
    path.write_text("\n".join(out), encoding="utf-8")
    return path
