"""Export results (CSV / tab-separated / Excel / HTML report), per-test raw data and whole experiments as XML."""

from __future__ import annotations

import base64
import csv
import re
import datetime as _dt
import html
import math
from dataclasses import asdict
from pathlib import Path
from xml.sax.saxutils import escape as _xesc, quoteattr as _qa

import numpy as np

from .. import __version__
from .project import INACTIVE_STATUSES, Project, result_columns

XML_FORMAT_VERSION = 1


def _fmt(v):
    if isinstance(v, (float, np.floating)):
        if not math.isfinite(v):
            return ""
        f = float(v)
        return str(int(f)) if f.is_integer() and abs(f) < 1e15 else f"{f:.10g}"
    if isinstance(v, np.integer):
        return int(v)
    return v


def write_csv(rows: list[dict], path, columns: list[str] | None = None, delimiter=","):
    cols = columns or result_columns(rows)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(cols)
        for r in rows:
            w.writerow([_fmt(r.get(c, "")) for c in cols])


def write_tsv(rows: list[dict], path, columns: list[str] | None = None):
    """Tab-separated text (opens directly in Excel / Prism / R)."""
    write_csv(rows, path, columns, delimiter="\t")


_ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
                    if isinstance(v, float) and not math.isfinite(v):
                        v = None
                if isinstance(v, np.bool_):
                    v = bool(v)
                elif isinstance(v, str):
                    v = _ILLEGAL_XLSX.sub("", v)
                vals.append(v)
            ws.append(vals)
            for cell in ws[ws.max_row]:  # text that looks like a formula stays text (no formula injection)
                if isinstance(cell.value, str) and cell.value[:1] in "=+-@" and cell.value not in ("", "-"):
                    cell.data_type = "s"
        ws.freeze_panes = "B2"
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = min(40, max(10, len(str(c)) + 2))
    wb.save(path)


def write_table(rows: list[dict], path, columns: list[str] | None = None, sheet: str = "Results") -> Path:
    """Write rows × columns; the format follows the extension: .csv, .tsv / .txt (tab-separated) or .xlsx."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".xlsx":
        write_xlsx({sheet: rows}, path, {sheet: columns} if columns else None)
    elif ext in (".tsv", ".txt", ".tab"):
        write_tsv(rows, path, columns)
    else:
        write_csv(rows, path, columns)
    return path


def table_text(rows: list[dict], columns: list[str], delimiter: str = "\t", header: bool = True) -> str:
    """Rows × columns as delimited text (for the clipboard)."""
    out = []
    if header:
        out.append(delimiter.join(str(c) for c in columns))
    for r in rows:
        out.append(delimiter.join(str(_fmt(r.get(c, ""))).replace(delimiter, " ").replace("\n", " ")
                                  for c in columns))
    return "\n".join(out) + "\n"


def export_results(project: Project, path, segmented: bool = False, columns: list[str] | None = None) -> Path:
    """Export project results; format chosen from the extension (.csv / .tsv / .txt / .xlsx / .xml)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".xml":
        return export_xml(project, path)
    rows = project.results(segmented=False)
    if ext == ".xlsx":
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
        write_csv(rows, path, columns, delimiter="\t" if ext in (".tsv", ".txt", ".tab") else ",")
    return path


def zone_visit_rows(project: Project, tests=None) -> list[dict]:
    """One row per zone visit (zone, entry time, exit time, duration) for every tracked test."""
    from .measures import zone_sequence

    rows = []
    for t in tests if tests is not None else project.tests:
        if t.status in INACTIVE_STATUSES or not project.has_track(t):
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


# ---------------------------------------------------------------- per-test raw data
def export_raw_data(project: Project, out_dir, tests=None, parameters: list[str] | None = None,
                    derived: bool = True, delimiter: str = ",", progress=None, should_stop=None) -> list[Path]:
    """One CSV per test and animal: time, raw track columns and (derived=True) every per-frame parameter of
    core.charts (or only `parameters`). Returns the written paths."""
    from . import charts
    from .track import COLUMNS

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tests = [t for t in (tests if tests is not None else project.tests) if project.has_track(t)]
    written = []
    beh = [asdict(b) for b in project.behaviours]
    for k, t in enumerate(tests):
        if should_stop and should_stop():
            break
        tracks = project.load_tracks(t)
        app = charts.apparatus_of_test(project, t)
        ids = [t.animal_id] + list(t.extra_animals)
        for i, tr in enumerate(tracks):
            raw_cols = [c for c in COLUMNS if c != "t"]
            cols = ["Time (s)"] + [f"raw {c}" for c in raw_cols]
            arrays = [tr.t] + [getattr(tr, c).astype(float) for c in raw_cols]
            if derived and app is not None and len(tr):
                others = [o for j, o in enumerate(tracks) if j != i]
                names = parameters
                if names is not None:
                    avail = {p.name for p in charts.parameters(app, tr, beh, len(others))}
                    names = [n for n in names if n in avail]
                dcols, arr = charts.per_frame_table(tr, app, project.analysis_for(t), t.events if i == 0 else [],
                                                    beh, names, others or None)
                cols += dcols[1:]
                arrays += [arr[:, j] for j in range(1, arr.shape[1])]
            aid = ids[i] if i < len(ids) else f"{t.animal_id}#{i + 1}"
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(aid))
            p = out_dir / f"test_{t.id:04d}_{safe}.{'tsv' if delimiter == chr(9) else 'csv'}"
            with open(p, "w", newline="", encoding="utf-8") as f:
                f.write(f"# Test {t.id}, animal {aid}, stage {t.stage}, trial {t.trial}, "
                        f"unit {app.unit if app else 'px'} (raw columns in pixels)\n")
                w = csv.writer(f, delimiter=delimiter)
                w.writerow(cols)
                M = np.column_stack(arrays) if arrays and len(tr) else np.zeros((0, len(cols)))
                for row in M:
                    w.writerow(["" if not np.isfinite(v) else f"{v:.6g}" for v in row])
            written.append(p)
        if progress:
            progress((k + 1) / max(1, len(tests)))
    return written


# ---------------------------------------------------------------- XML export
def _attrs(**kw) -> str:
    parts = []
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (float, np.floating)):
            v = "" if not math.isfinite(v) else f"{float(v):.10g}"
        parts.append(f"{k.replace('_', '-')}={_qa(str(v))}")
    return (" " + " ".join(parts)) if parts else ""


def _shape_xml(tag: str, d: dict | None, ind: str) -> str:
    if not d:
        return ""
    scal = {k: v for k, v in d.items() if k not in ("points", "type")}
    pts = d.get("points") or []
    if not pts:
        return f"{ind}<{tag}{_attrs(type=d.get('type'), **scal)}/>\n"
    inner = "".join(f"{ind}  <vertex{_attrs(x=float(p[0]), y=float(p[1]))}/>\n" for p in pts)
    return f"{ind}<{tag}{_attrs(type=d.get('type'), **scal)}>\n{inner}{ind}</{tag}>\n"


def _value_attrs(v) -> dict:
    if isinstance(v, (bool, np.bool_)):
        return {"value": bool(v), "type": "bool"}
    if isinstance(v, (int, np.integer)):
        return {"value": int(v), "type": "integer"}
    if isinstance(v, (float, np.floating)):
        return {"value": float(v), "type": "number"}
    if isinstance(v, (list, tuple, dict)):
        import json

        return {"value": json.dumps(v, default=str), "type": "json"}
    return {"value": "" if v is None else str(v), "type": "text"}


def export_xml(project: Project, path, tests=None, include_tracks: bool = True, include_results: bool = True,
               rows: list[dict] | None = None, segmented: bool = False, progress=None, should_stop=None) -> Path:
    """Write the whole experiment (settings, apparatus, animals, tests with scoring, I/O events, results and the
    per-frame tracking data) as one XML document. Written incrementally, one test at a time.

    rows: precomputed result rows (e.g. from the Results page); otherwise results are calculated per test.
    Track columns are space-separated numbers ("NaN" = missing) so they load with str2num / sscanf in MATLAB.
    """
    from .track import COLUMNS

    path = Path(path)
    tests = [t for t in (tests if tests is not None else project.tests)]
    by_test: dict = {}
    if rows is not None:
        for r in rows:
            by_test.setdefault(r.get("Test"), []).append(r)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        w = f.write
        w('<?xml version="1.0" encoding="UTF-8"?>\n')
        w(f"<manymaze-experiment{_attrs(format_version=XML_FORMAT_VERSION, software=f'mANY-MAZE {__version__}', exported=_dt.datetime.now().isoformat(timespec='seconds'))}>\n")
        w(f"  <experiment{_attrs(name=project.name, protocol=project.protocol, test_duration_s=float(project.test_duration_s), start_mode=project.start_mode, created=project.created, blind=bool(getattr(project, 'blind', False)))}>\n")
        w(f"    <description>{_xesc(project.description or '')}</description>\n")
        for tag, d in (("detection-settings", project.detection.to_dict()),
                       ("analysis-settings", project.analysis.to_dict())):
            w(f"    <{tag}>\n")
            for k, v in d.items():
                w(f"      <setting{_attrs(name=k, **_value_attrs(v))}/>\n")
            w(f"    </{tag}>\n")
        variables = getattr(project, "variables", {}) or {}
        if variables:
            w("    <variables>\n")
            for k, v in variables.items():
                w(f"      <variable{_attrs(name=k, **_value_attrs(v))}/>\n")
            w("    </variables>\n")
        w("  </experiment>\n")
        w("  <groups>\n" + "".join(f"    <group{_attrs(name=g.name, color=g.color)}/>\n" for g in project.groups)
          + "  </groups>\n")
        w("  <stages>\n" + "".join(f"    <stage{_attrs(name=s)}/>\n" for s in project.stages) + "  </stages>\n")
        w("  <behaviours>\n" + "".join(f"    <behaviour{_attrs(name=b.name, key=b.key, kind=b.kind)}/>\n"
                                       for b in project.behaviours) + "  </behaviours>\n")
        w("  <apparatus-list>\n")
        for a in project.apparatus:
            fs = a.frame_size or (None, None)
            w(f"    <apparatus{_attrs(name=a.name, template=a.template, unit=a.unit, px_per_cm=a.px_per_cm, frame_width=fs[0], frame_height=fs[1])}>\n")
            w(_shape_xml("arena", a.arena.to_dict() if a.arena else None, "      "))
            for z in a.zones:
                w(f"      <zone{_attrs(name=z.name, color=z.color)}>\n")
                w(_shape_xml("shape", z.shape.to_dict(), "        "))
                w("      </zone>\n")
            for g in a.groups:
                w(f"      <zone-group{_attrs(name=g.name)}>\n")
                for zn in g.zones:
                    w(f"        <member{_attrs(zone=zn)}/>\n")
                for zn in g.exclude:
                    w(f"        <exclude{_attrs(zone=zn)}/>\n")
                w("      </zone-group>\n")
            for p in a.points:
                w(f"      <point{_attrs(name=p.name, x=p.x, y=p.y, radius_cm=p.radius_cm, color=p.color)}/>\n")
            for ln in a.lines:
                w(f"      <line{_attrs(name=ln.name, x1=ln.x1, y1=ln.y1, x2=ln.x2, y2=ln.y2, color=ln.color)}/>\n")
            w("    </apparatus>\n")
        w("  </apparatus-list>\n")
        w("  <animals>\n")
        for an in project.animals:
            w(f"    <animal{_attrs(id=an.id, group=an.group, sex=an.sex)}")
            if an.fields:
                w(">\n" + "".join(f"      <field{_attrs(name=k, **_value_attrs(v))}/>\n" for k, v in an.fields.items())
                  + "    </animal>\n")
            else:
                w("/>\n")
        w("  </animals>\n")
        w(f"  <tests{_attrs(count=len(tests))}>\n")
        for k, t in enumerate(tests):
            if should_stop and should_stop():
                break
            w(f"    <test{_attrs(id=t.id, animal=t.animal_id, stage=t.stage, trial=t.trial, apparatus=t.apparatus, video=t.video, start_s=float(t.start_s), duration_s=float(t.duration_s or project.test_duration_s), status=t.status, recorded_at=t.recorded_at)}>\n")
            for ea in t.extra_animals:
                w(f"      <extra-animal{_attrs(id=ea)}/>\n")
            if t.notes:
                w(f"      <notes>{_xesc(t.notes)}</notes>\n")
            if t.variables:
                w("      <variables>\n" + "".join(f"        <variable{_attrs(name=n, **_value_attrs(v))}/>\n"
                                                 for n, v in t.variables.items()) + "      </variables>\n")
            ov = getattr(t, "zone_overrides", None) or {}
            if ov:
                w("      <zone-overrides>\n")
                for zn, sd in ov.items():
                    w(f"        <zone{_attrs(name=zn)}>\n" + _shape_xml("shape", sd, "          ") + "        </zone>\n")
                w("      </zone-overrides>\n")
            pauses = getattr(t, "pauses", None) or []
            if pauses:
                w("      <pauses>\n" + "".join(f"        <pause{_attrs(start=float(a), end=float(b))}/>\n"
                                              for a, b in pauses) + "      </pauses>\n")
            w("      <events>\n" + "".join(
                f"        <event{_attrs(behaviour=e.get('behaviour'), t=float(e['t']), t_end=None if e.get('t_end') is None else float(e['t_end']))}/>\n"
                for e in t.events) + "      </events>\n")
            io = getattr(t, "io_events", None) or []
            if io:
                w("      <io-events>\n" + "".join(
                    f"        <io{_attrs(t=float(e.get('t', 0)), device=e.get('device'), channel=e.get('channel'), kind=e.get('kind'), value=e.get('value'))}/>\n"
                    for e in io) + "      </io-events>\n")
            rv = getattr(t, "result_variables", None) or {}
            if rv:
                w("      <result-variables>\n" + "".join(f"        <variable{_attrs(name=n, **_value_attrs(v))}/>\n"
                                                        for n, v in rv.items()) + "      </result-variables>\n")
            has = project.has_track(t)
            if include_results and t.status not in INACTIVE_STATUSES:
                trows = by_test.get(t.id) if rows is not None else None
                if trows is None and project.has_results(t):
                    try:
                        trows = project.analyse_test(t, segmented)
                    except Exception:
                        trows = []
                info = {"Test", "Animal", "Group", "Sex", "Stage", "Trial", "Apparatus", "Period",
                        *project.animal_fields}
                for r in trows or []:
                    w(f"      <results{_attrs(animal=r.get('Animal'), period=r.get('Period', 'Whole test'))}>\n")
                    for c, v in r.items():
                        if c in info:
                            continue
                        w(f"        <result{_attrs(name=c, **_value_attrs(v))}/>\n")
                    w("      </results>\n")
            if include_tracks and has:
                ids = [t.animal_id] + list(t.extra_animals)
                for i, tr in enumerate(project.load_tracks(t)):
                    aid = ids[i] if i < len(ids) else f"{t.animal_id}#{i + 1}"
                    w(f"      <track{_attrs(animal=aid, index=i + 1, fps=float(tr.fps), samples=len(tr), video_start_s=tr.meta.get('video_start_s'), units='px')}>\n")
                    for c in COLUMNS:
                        v = getattr(tr, c).astype(float)
                        txt = " ".join("NaN" if not np.isfinite(x) else (f"{x:.5f}" if c == "t" else f"{x:.3f}")
                                       for x in v) if c != "detected" else " ".join("1" if x else "0" for x in v)
                        w(f"        <column{_attrs(name=c)}>{txt}</column>\n")
                    w("      </track>\n")
            w("    </test>\n")
            if progress:
                progress((k + 1) / max(1, len(tests)))
        w("  </tests>\n</manymaze-experiment>\n")
    if should_stop and should_stop():
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(path)
    return path


def read_experiment_xml(path) -> dict:
    """Parse an XML export back into plain Python data (tests with results, events and track arrays)."""
    import xml.etree.ElementTree as ET

    def val(e):
        v, t = e.get("value", ""), e.get("type", "text")
        if t == "number":
            return float(v) if v != "" else math.nan
        if t == "integer":
            return int(v)
        if t == "bool":
            return v == "true"
        if t == "json":
            import json

            return json.loads(v)
        return v

    root = ET.parse(path).getroot()
    exp = root.find("experiment")
    out = {"name": exp.get("name"), "format_version": int(root.get("format-version", "1")),
           "animals": [dict(a.attrib) for a in root.iter("animal")],
           "apparatus": [a.get("name") for a in root.find("apparatus-list")], "tests": []}
    for t in root.find("tests"):
        d = dict(t.attrib)
        d["events"] = [dict(e.attrib) for e in t.findall("events/event")]
        d["io_events"] = [dict(e.attrib) for e in t.findall("io-events/io")]
        d["results"] = [{"animal": r.get("animal"), "period": r.get("period"),
                         "values": {x.get("name"): val(x) for x in r.findall("result")}} for r in t.findall("results")]
        d["tracks"] = []
        for tr in t.findall("track"):
            cols = {c.get("name"): np.array(c.text.split(), float) if c.text else np.zeros(0)
                    for c in tr.findall("column")}
            d["tracks"].append({"animal": tr.get("animal"), "fps": float(tr.get("fps")), "columns": cols})
        out["tests"].append(d)
    return out


# ---------------------------------------------------------------- HTML report
def _img(png: bytes, width=320) -> str:
    return f'<img width="{width}" src="data:image/png;base64,{base64.b64encode(png).decode()}">'


def html_report(project: Project, path, tests=None, include_plots: bool = True, measures: list[str] | None = None,
                stats_measures: list[str] | None = None, heatmap_norm: str = "auto",
                chart_parameters: list[str] | None = None, color_by: str = "time") -> Path:
    """Self-contained HTML report: summary, per-test track plots/heat maps (+ optional charts of per-frame
    parameters), group heat maps on a common scale, results and statistics."""
    from . import charts, plots
    from .stats import compare_groups, group_values, summary_text
    from .video import VideoSource

    tests = tests if tests is not None else [t for t in project.tests
                                             if t.status not in INACTIVE_STATUSES and project.has_results(t)]
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
    beh = [asdict(b) for b in project.behaviours]
    if include_plots and tests:
        out.append("<h2>Tracks</h2><div class='grid'>")
        hm_vmax = None
        if heatmap_norm == "fixed":
            hm_vmax = 0.0
            for t in tests:
                trs = project.load_tracks(t)
                if trs:
                    H, _ = plots.occupancy(trs[:1], charts.apparatus_of_test(project, t))
                    hm_vmax = max(hm_vmax, float(H.max()) if H.size else 0.0)
            hm_vmax = hm_vmax or None
        for t in tests:
            tracks = project.load_tracks(t)
            if not tracks:
                continue
            app = charts.apparatus_of_test(project, t)
            frame = None
            try:
                with VideoSource(project.abs_path(t.video)) as v:
                    frame = v.frame_at(int(t.start_s * v.fps))
            except Exception:
                pass
            an = project.get_animal(t.animal_id)
            title = f"Test {t.id} · {t.animal_id} · {an.group if an else ''}"
            markers = plots.behaviour_markers(tracks[0], app, project.analysis_for(t), t.events, beh)
            tp = plots.fig_to_png(plots.track_plot(tracks[0], app, frame=frame, size=(3.2, 3.2), color_by=color_by,
                                                   markers=markers, settings=project.analysis_for(t)))
            hm = plots.fig_to_png(plots.heatmap(tracks[:1], app, size=(3.6, 3.2), vmax=hm_vmax,
                                                norm="auto" if heatmap_norm == "fixed" else heatmap_norm))
            card = f"<div class='card'><b>{html.escape(title)}</b><br>{_img(tp, 260)}{_img(hm, 290)}"
            if chart_parameters:
                names = [p.name for p in charts.parameters(app, tracks[0], beh) if p.name in chart_parameters]
                if names:
                    fig = charts.chart_figure(tracks[0], app, names, project.analysis_for(t), t.events, beh,
                                              size=(6.5, 1.0 + 1.1 * len(names)))
                    card += "<br>" + _img(plots.fig_to_png(fig), 560)
            out.append(card + "</div>")
        out.append("</div>")
        # group heatmaps
        groups = {}
        for t in tests:
            a = project.get_animal(t.animal_id)
            groups.setdefault(a.group if a else "", []).append(t)
        if len(groups) > 1:
            app0 = charts.apparatus_of_test(project, tests[0])
            data = []
            for g, ts in groups.items():
                trs = []
                for t in ts:
                    for tr in project.load_tracks(t)[:1]:
                        trs.append(plots.align_track(tr, charts.apparatus_of_test(project, t),
                                                     (t.variables or {}).get("heatmap_transform", "none"), app0))
                data.append((g or "No group", trs, app0))
            fig = plots.group_heatmap_figure(data, norm="auto" if heatmap_norm == "fixed" else heatmap_norm)
            out.append("<h2>Group occupancy</h2><div class='grid'><div class='card'>"
                       f"{_img(plots.fig_to_png(fig), 300 * min(3, len(data)) + 60)}</div></div>")
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
