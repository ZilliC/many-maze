"""Command-line interface.

    manymaze                      # launch the GUI
    manymaze gui [project]
    manymaze demo DIR             # create a demo project with synthetic videos
    manymaze track VIDEO --template open_field --bbox X,Y,W,H [--size-cm 40] [-o results.csv]
    manymaze project DIR track [--all]
    manymaze project DIR relink --folder VIDEOS   # find moved videos by file name
    manymaze project DIR results -o results.xlsx [--bins]   # or .csv / .tsv / .slk / .dbf / .xml
    manymaze project DIR results --report NAME -o results.csv   # a results report saved on the Data page
    manymaze project DIR report -o report.html [--report NAME]
    manymaze project DIR plugins                 # run the protocol's analysis plug-ins on every test performed
    manymaze templates

An experiment protected by a password is opened with the password in the environment variable MANYMAZE_PASSWORD.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__


TABLE_OUTPUT_HELP = ("the format follows the extension: .csv, .tsv / .txt, .xlsx, .slk (SYLK) or .dbf (dBase III); "
                     "results also .xml")
PASSWORD_ENV = "MANYMAZE_PASSWORD"  # the password of an experiment protected by one


def _password() -> str | None:
    return os.environ.get(PASSWORD_ENV) or None


def _load_project(path):
    """The experiment at path; a protected one is opened with MANYMAZE_PASSWORD (exit with a message otherwise)."""
    from .core.project import Project
    from .core.security import PasswordRequired

    try:
        return Project.load(path, password=_password())
    except PasswordRequired as e:
        sys.exit(f"{e}. Set the environment variable {PASSWORD_ENV} to its password." if not e.wrong else
                 f"{e} (the environment variable {PASSWORD_ENV}).")


def _progress(prefix):
    def cb(f):
        sys.stderr.write(f"\r{prefix} {f * 100:5.1f}%")
        sys.stderr.flush()
        if f >= 1:
            sys.stderr.write("\n")

    return cb


def cmd_track(a):
    from .core import templates
    from .core.measures import AnalysisSettings, analyse_segmented
    from .core.tracking import ArenaJob, DetectionSettings, track_video
    from .core.video import VideoSource

    with VideoSource(a.video) as v:
        W, H = v.width, v.height
    if a.apparatus:
        from .core.apparatus import Apparatus
        from .core.security import PasswordRequired, loads

        try:  # an apparatus file, or an experiment's project.json (MANYMAZE_PASSWORD if it is protected)
            d = loads(Path(a.apparatus).read_text(encoding="utf-8"), _password(), a.apparatus)[0]
        except PasswordRequired as e:
            sys.exit(f"{e} (set the environment variable {PASSWORD_ENV})")
        if isinstance(d, dict) and isinstance(d.get("apparatus"), list):  # apparatus file or experiment
            if not d["apparatus"]:
                sys.exit(f"{a.apparatus} contains no apparatus")
            d = d["apparatus"][0]
        app = Apparatus.from_dict(d)
    else:
        try:
            x, y, w, h = map(float, a.bbox.split(",")) if a.bbox else (0, 0, W, H)
        except ValueError:
            sys.exit("--bbox must be X,Y,W,H in pixels, e.g. --bbox 18,48,594,412")
        params = {}
        if a.size_cm:
            for key in ("size_cm", "width_cm", "pool_diameter_cm", "diameter_cm", "outer_diameter_cm"):
                params[key] = a.size_cm
        app = templates.build(a.template, x, y, w, h, **params)
    s = DetectionSettings(contrast=a.contrast, threshold=a.threshold, duration_s=a.duration or 0.0,
                          start_time_s=a.start or 0.0, n_animals=a.animals)
    tracks = track_video(a.video, [ArenaJob(app, s)], progress=_progress("tracking"))[0]
    an = AnalysisSettings(bin_length_s=a.bin or 0.0)
    rows = []
    for i, tr in enumerate(tracks):
        if a.save_track:
            p = Path(a.save_track)
            tr.to_csv(p if len(tracks) == 1 else p.with_name(f"{p.stem}_a{i + 1}{p.suffix}"))
        for label, res in analyse_segmented(tr, app, an, other_tracks=[t for t in tracks if t is not tr] or None):
            rows.append({"Video": a.video, "Animal": i + 1, "Period": label, **res})
    if a.output:
        from .core.export import write_table

        write_table(rows, a.output)
        print(f"Wrote {a.output}")
    elif not rows:
        sys.exit("No animal was detected in the video (check --template/--bbox and the detection settings).")
    else:
        for k, v in rows[0].items():
            print(f"{k}: {v}")


def _lock_for_writing(p):
    """Lock the experiment while this command changes it; exit if another program has it open or it cannot be
    saved by this version."""
    from .core import explock

    try:
        p.check_writable()
    except ValueError as e:
        sys.exit(str(e))
    other = explock.acquire(p.path)
    if other is not None:
        sys.exit(f"The experiment is open in {explock.describe(other)}: close it there first (this command saves "
                 f"the experiment and would overwrite its changes).")


def cmd_project(a):
    from .core import explock

    p = _load_project(a.dir)
    if a.action in ("track", "relink", "plugins"):
        _lock_for_writing(p)
        try:
            return _change_project(p, a)
        finally:
            explock.release(p.path)
    if a.action == "results":
        from .core.export import export_results

        out = a.output or str(p.exports_dir() / ("results by animal.xlsx" if a.wide else "results.xlsx"))
        if a.report:
            _export_report(p, a, out)
        elif a.wide:
            from .core.export import wide_rows, write_table
            from .core.project import result_columns

            if Path(out).suffix.lower() == ".xml":
                sys.exit("--wide writes a table (one row per animal): use .csv, .tsv, .txt, .xlsx, .slk or .dbf, or "
                         "leave --wide out for the XML export of the whole experiment")
            rows = p.results(segmented=a.bins)
            info = {"Test", "Animal", "Group", "Sex", "Stage", "Trial", "Apparatus", "Period", *p.animal_fields}
            measures = [c for c in result_columns(rows) if c not in info]
            if a.column:  # only the chosen measures
                missing = [c for c in a.column if c not in measures]
                if missing:
                    sys.exit(f"Unknown measure(s): {', '.join(missing)}")
                measures = list(dict.fromkeys(a.column))
            wide = wide_rows(rows, measures)
            try:
                write_table(wide, out, result_columns(wide), sheet="By animal")
            except ValueError as e:
                sys.exit(str(e))
        else:
            cols = None
            if a.column:  # the information columns, then the chosen measures
                from .core.project import INFO_COLUMNS

                rows = p.results(segmented=a.bins)
                have = {k for r in rows for k in r}
                missing = [c for c in a.column if c not in have]
                if missing:
                    sys.exit(f"Unknown measure(s): {', '.join(missing)}")
                info = [c for c in (*INFO_COLUMNS, "Period", *p.animal_fields) if c in have and c not in a.column]
                cols = list(dict.fromkeys(info + a.column))
            try:
                export_results(p, out, segmented=a.bins, columns=cols)
            except ValueError as e:  # e.g. more columns than a dBase table can hold
                sys.exit(str(e))
        print(f"Wrote {out}")
    elif a.action == "events":
        from .core.export import event_log_rows, write_table
        from .core.project import INACTIVE_STATUSES

        out = a.output or str(p.exports_dir() / "event log.csv")
        rows = [{"Test": t.id, "Stage": t.stage, "Trial": t.trial, **r} for t in p.tests
                if t.status not in INACTIVE_STATUSES for r in event_log_rows(p, t)]
        try:
            write_table(rows, out, sheet="Event log")
        except ValueError as e:
            sys.exit(str(e))
        print(f"Wrote {out} ({len(rows)} events)")
    elif a.action == "protocol":
        from .core.export import protocol_report

        out = a.output or str(p.exports_dir() / "protocol.html")
        protocol_report(p, out)
        print(f"Wrote {out}")
    elif a.action == "archive":
        from .core.archive import archive_project

        out = a.output or str(Path(a.dir).resolve().parent / f"{Path(a.dir).stem} archive.zip")
        archive_project(p, out, progress=_progress("archiving"))
        print(f"Wrote {out}")
    elif a.action == "report":
        from .core.export import html_report

        out = a.output or str(p.exports_dir() / "report.html")
        if a.report:  # the tests, rows and measures of a saved results report
            from .core.reports import find_report

            rows, cols = _report_table(p, a.report)
            if not find_report(p.reports, a.report)["period"]:  # as the Data page: one period, else whole tests
                rows = [r for r in rows if r.get("Period", "Whole test") == "Whole test"]
            info = set(p.info_columns())
            tests = [t for t in (p.get_test(i) for i in dict.fromkeys(r.get("Test") for r in rows)) if t is not None]
            html_report(p, out, tests=tests, measures=[c for c in cols if c not in info], rows=rows, report=a.report)
        else:
            html_report(p, out)
        print(f"Wrote {out}")
    elif a.action == "info":
        print(f"{p.name}: {len(p.tests)} tests, {len(p.animals)} animals, apparatus: "
              f"{', '.join(x.name for x in p.apparatus)}")
        for t in p.tests:
            print(f"  #{t.id} {t.animal_id:>8} {t.stage:>8} {t.status:>8} {t.video}")
        missing = p.missing_videos()
        if missing:
            print(f"{len(missing)} test(s) with a missing video: {', '.join(str(t.id) for t in missing)} (find them "
                  f"with: manymaze project DIR relink --folder FOLDER)")


def _report_table(p, name):
    """The rows and columns of a saved results report; exit if the experiment has none of that name."""
    try:
        return p.report_table(name)
    except KeyError:
        names = ", ".join(f"“{r['name']}”" for r in p.reports) or "none"
        sys.exit(f"The experiment has no results report called “{name}” (saved reports: {names}). Reports are "
                 f"saved on the Data page (Report ▸ Save as…).")


def _export_report(p, a, out):
    """results --report NAME: the rows and columns of a saved results report (--wide: one row per animal)."""
    from .core.export import results_workbook, wide_rows, write_table, write_xlsx
    from .core.project import result_columns
    from .core.reports import find_report

    if a.column or a.bins:
        sys.exit("--report sets the columns and the time periods: leave out --column and --bins")
    if Path(out).suffix.lower() == ".xml":
        sys.exit("--report writes a table: use .csv, .tsv, .txt, .xlsx, .slk or .dbf (the XML export is the whole "
                 "experiment; leave --report out for it)")
    rows, cols = _report_table(p, a.report)
    try:
        if a.wide:
            info = set(p.info_columns())
            wide = wide_rows(rows, [c for c in cols if c not in info])
            write_table(wide, out, result_columns(wide), sheet="By animal")
        elif out.lower().endswith(".xlsx"):
            sheets, colmap = results_workbook(p, rows, cols, find_report(p.reports, a.report)["segmented"])
            write_xlsx(sheets, out, colmap)
        else:
            write_table(rows, out, cols)
    except ValueError as e:  # e.g. more columns than a dBase table can hold
        sys.exit(str(e))


def _change_project(p, a):
    """The project actions that save the experiment (run with the experiment locked)."""
    if a.action == "track":
        from .core.batch import track_tests
        from .core.project import INACTIVE_STATUSES

        todo = [t for t in p.tests if t.video and (a.all or not p.has_track(t)) and t.status not in INACTIVE_STATUSES]
        try:
            res = track_tests(p, todo, progress=_progress("tracking"), workers=a.workers)
        except ValueError as e:  # e.g. MANYMAZE_WORKERS is not a number
            sys.exit(str(e))
        for e in res["errors"]:
            print(e, file=sys.stderr)
        try:
            p.save()
        except Exception as e:
            ids = ", ".join(str(i) for i in res["tracked"]) or "none"
            sys.exit(f"The tracks were written (tests {ids}) but the experiment could not be saved, so these tests "
                     f"are not marked as tracked: {e}\nFix the problem and run the command again (with --all to "
                     f"track them again).")
        print(f"Tracked {len(res['tracked'])} of {len(todo)} tests ({res['workers']} parallel workers)")
    elif a.action == "plugins":
        from .core.plugins import run_analysis_plugins

        if not p.analysis_plugins:
            sys.exit("The protocol has no analysis plug-ins (Protocol ▸ Analysis ▸ Analysis plug-ins)")
        res = run_analysis_plugins(p, progress=_progress("plug-ins"))
        for tid, msg in res["errors"]:
            print(f"  #{tid}: {msg}", file=sys.stderr)
        try:
            p.save()
        except Exception as e:
            sys.exit(f"The experiment could not be saved: {e}")
        print(f"Ran the analysis plug-ins on {len(res['done'])} test(s); {len(res['errors'])} problem(s)")
    elif a.action == "relink":
        from .core.project import relink_videos

        if not a.folder:
            sys.exit("relink needs --folder: the folder (searched with its subfolders) holding the moved videos")
        if not Path(a.folder).is_dir():
            sys.exit(f"Folder not found: {a.folder}")
        res = relink_videos(p, a.folder)
        if res["relinked"]:
            try:
                p.save()
            except Exception as e:
                sys.exit(f"The experiment could not be saved: {e}")
        for tid, path in res["relinked"].items():
            print(f"  #{tid} -> {path}")
        print(f"Relinked {len(res['relinked'])} video(s); not found: {len(res['not_found'])}"
              + (f"; several files of the same name (left alone): tests {', '.join(map(str, res['ambiguous']))}"
                 if res["ambiguous"] else ""))


def cmd_demo(a):
    from .core.demo import create_demo_project

    p = create_demo_project(a.dir, progress=_progress("creating demo"))
    print(f"Demo project created at {p.path}")


def cmd_templates(_a):
    from .core.templates import TEMPLATES

    for k, t in TEMPLATES.items():
        print(f"{k:20s} {t.title} — {t.description}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="manymaze", description="mANY-MAZE: libre behavioural video tracking")
    ap.add_argument("--version", action="version", version=f"mANY-MAZE {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("gui", help="launch the graphical interface")
    g.add_argument("project", nargs="?")
    t = sub.add_parser("track", help="track a single video and print/export measures")
    t.add_argument("video")
    t.add_argument("--template", default="open_field")
    t.add_argument("--apparatus", help="apparatus JSON file (overrides --template)")
    t.add_argument("--bbox", help="X,Y,W,H of the apparatus in pixels (default: whole frame)")
    t.add_argument("--size-cm", type=float, help="real width of the apparatus in cm (calibration)")
    t.add_argument("--contrast", default="auto", choices=["auto", "dark", "light"])
    t.add_argument("--threshold", type=int, default=25)
    t.add_argument("--animals", type=int, default=1)
    t.add_argument("--start", type=float)
    t.add_argument("--duration", type=float)
    t.add_argument("--bin", type=float, help="time bin length (s)")
    t.add_argument("--save-track", help="write per-frame track CSV")
    t.add_argument("-o", "--output", help=TABLE_OUTPUT_HELP)
    pr = sub.add_parser("project", help="batch operations on a project")
    pr.add_argument("dir")
    pr.add_argument("action", choices=["info", "track", "results", "report", "events", "protocol", "archive",
                                       "relink", "plugins"])
    pr.add_argument("--all", action="store_true", help="re-track tests that already have tracks")
    pr.add_argument("--workers", type=int, default=0, help="parallel tracking processes (default: all cores but one)")
    pr.add_argument("--bins", action="store_true", help="include time-bin results")
    pr.add_argument("--wide", action="store_true", help="results: one row per animal, stages / trials as columns")
    pr.add_argument("--column", action="append", metavar="MEASURE",
                    help="results: export only this measure (repeat for several; the information columns are kept; "
                         "with --wide: these measures only)")
    pr.add_argument("--report", metavar="NAME",
                    help="results / report: the columns, time periods and rows of the results report saved under "
                         "this name on the Data page")
    pr.add_argument("--folder", help="relink: folder holding the moved videos (searched with its subfolders)")
    pr.add_argument("-o", "--output", help="output file; for results and events " + TABLE_OUTPUT_HELP)
    d = sub.add_parser("demo", help="create a demo project with synthetic videos")
    d.add_argument("dir")
    sub.add_parser("templates", help="list apparatus templates")
    a = ap.parse_args(argv)
    if a.cmd is None or a.cmd == "gui":
        from .gui.app import main as gui_main

        return gui_main(getattr(a, "project", None), argv_project=a.cmd is None)
    if a.cmd == "project" and not Path(a.dir).exists():
        sys.exit(f"Experiment folder not found: {a.dir}")
    return {"track": cmd_track, "project": cmd_project, "demo": cmd_demo, "templates": cmd_templates}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
