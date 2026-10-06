"""Command-line interface.

    manymaze                      # launch the GUI
    manymaze gui [project]
    manymaze demo DIR             # create a demo project with synthetic videos
    manymaze track VIDEO --template open_field --bbox X,Y,W,H [--size-cm 40] [-o results.csv]
    manymaze project DIR track [--all]
    manymaze project DIR results -o results.xlsx [--bins]
    manymaze project DIR report -o report.html
    manymaze templates
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _progress(prefix):
    def cb(f):
        sys.stderr.write(f"\r{prefix} {f * 100:5.1f}%")
        sys.stderr.flush()
        if f >= 1:
            sys.stderr.write("\n")

    return cb


def cmd_track(a):
    from .core import templates
    from .core.export import write_csv
    from .core.measures import AnalysisSettings, analyse_segmented
    from .core.tracking import ArenaJob, DetectionSettings, track_video
    from .core.video import VideoSource

    with VideoSource(a.video) as v:
        W, H = v.width, v.height
    if a.apparatus:
        import json

        from .core.apparatus import Apparatus

        app = Apparatus.from_dict(json.loads(Path(a.apparatus).read_text()))
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
        if a.output.endswith(".xlsx"):
            from .core.export import write_xlsx

            write_xlsx({"Results": rows}, a.output)
        else:
            write_csv(rows, a.output)
        print(f"Wrote {a.output}")
    elif not rows:
        sys.exit("No animal was detected in the video (check --template/--bbox and the detection settings).")
    else:
        for k, v in rows[0].items():
            print(f"{k}: {v}")


def cmd_project(a):
    from .core.project import Project

    p = Project.load(a.dir)
    if a.action == "track":
        from .core.batch import track_tests
        from .core.project import INACTIVE_STATUSES

        todo = [t for t in p.tests if t.video and (a.all or not p.has_track(t)) and t.status not in INACTIVE_STATUSES]
        res = track_tests(p, todo, progress=_progress("tracking"), workers=a.workers)
        p.save()
        for e in res["errors"]:
            print(e, file=sys.stderr)
        print(f"Tracked {len(res['tracked'])} of {len(todo)} tests ({res['workers']} parallel workers)")
    elif a.action == "results":
        from .core.export import export_results

        out = a.output or str(p.exports_dir() / "results.xlsx")
        export_results(p, out, segmented=a.bins)
        print(f"Wrote {out}")
    elif a.action == "report":
        from .core.export import html_report

        out = a.output or str(p.exports_dir() / "report.html")
        html_report(p, out)
        print(f"Wrote {out}")
    elif a.action == "info":
        print(f"{p.name}: {len(p.tests)} tests, {len(p.animals)} animals, apparatus: "
              f"{', '.join(x.name for x in p.apparatus)}")
        for t in p.tests:
            print(f"  #{t.id} {t.animal_id:>8} {t.stage:>8} {t.status:>8} {t.video}")


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
    t.add_argument("-o", "--output")
    pr = sub.add_parser("project", help="batch operations on a project")
    pr.add_argument("dir")
    pr.add_argument("action", choices=["info", "track", "results", "report"])
    pr.add_argument("--all", action="store_true", help="re-track tests that already have tracks")
    pr.add_argument("--workers", type=int, default=0, help="parallel tracking processes (default: all cores but one)")
    pr.add_argument("--bins", action="store_true", help="include time-bin results")
    pr.add_argument("-o", "--output")
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
