"""Compare mANY-MAZE's zone measures with ANY-maze's own zone entries and exits in an ANY-maze experiment XML
export.

ANY-maze writes <zone_entry> / <zone_exit> tags into the results of its XML export, i.e. its own decision of when
the animal entered and left each zone. This script imports the export into a scratch experiment (zones from the
exported bounding boxes, or from an apparatus file with the real outlines), recomputes the zone measures with
mANY-MAZE and reports, per zone, how far entries, time in the zone and latency to first entry are from ANY-maze's:

    uv run python scripts/verify_anymaze.py export.xml [--apparatus real_zones.mmapp] [--csv report.csv]

Bounding-box zones are only exact for rectangular zones; for other shapes import ANY-maze's zone maps first and
pass the resulting apparatus, or expect differences near the zone borders.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manymaze.core.anymaze import import_anymaze_xml, read_anymaze_xml, zone_reference  # noqa: E402
from manymaze.core.project import Project  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xml", type=Path)
    ap.add_argument("--apparatus", type=Path, help="apparatus file with the real zone outlines (.mmapp / JSON)")
    ap.add_argument("--csv", type=Path, help="write one row per test and zone")
    a = ap.parse_args(argv)

    data = read_anymaze_xml(a.xml)
    tmp = Path(tempfile.mkdtemp(prefix="anymaze-verify-"))
    p = Project(name="ANY-maze verification")
    p.save(tmp / "verify.mmaze")
    if a.apparatus:
        from manymaze.core.apparatus import Apparatus
        import json
        p.apparatus.append(Apparatus.from_dict(json.loads(a.apparatus.read_text())))
    res = import_anymaze_xml(p, a.xml)
    refs = [t for an in data["animals"] for t in an["tests"] if len(np.asarray(t["track"]["t"]))]
    if len(refs) != len(res["tests"]):
        print(f"warning: {len(refs)} exported tests, {len(res['tests'])} imported", file=sys.stderr)

    rows = []
    for ref, test in zip(refs, res["tests"]):
        ours = p.analyse_test(test)[0]
        t = np.asarray(ref["track"]["t"], float)
        t = t[np.isfinite(t)]
        for zone, r in zone_reference(ref["zone_events"], float(t[-1] - t[0]) + test_dt(t)).items():
            rows.append({"test": test.id, "zone": zone,
                         "entries (ANY-maze)": r["entries"], "entries": ours.get(f"{zone}: entries", math.nan),
                         "time (ANY-maze)": round(r["time"], 3), "time": ours.get(f"{zone}: time (s)", math.nan),
                         "latency (ANY-maze)": r["latency"],
                         "latency": ours.get(f"{zone}: latency to first entry (s)", math.nan)})
    if not rows:
        print("The export has no <zone_entry> / <zone_exit> tags: nothing to compare.")
        return 1
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    print(f"{len(res['tests'])} tests, {len(rows)} test × zone pairs")
    for zone in sorted({r["zone"] for r in rows}):
        zr = [r for r in rows if r["zone"] == zone]
        de = np.array([float(r["entries"]) - r["entries (ANY-maze)"] for r in zr])
        dt = np.array([float(r["time"]) - r["time (ANY-maze)"] for r in zr])
        lat = np.array([(float(r["latency"]), r["latency (ANY-maze)"]) for r in zr])
        both = np.isfinite(lat).all(axis=1)
        dl = lat[both, 0] - lat[both, 1]
        print(f"{zone:>12}: entries equal in {np.mean(de == 0):6.1%} (mean |Δ| {np.mean(np.abs(de)):.2f}); "
              f"time |Δ| median {np.median(np.abs(dt)):.3f} s, max {np.max(np.abs(dt)):.3f} s; "
              f"latency equal (±1 ms) in {np.mean(np.abs(dl) <= 1e-3) if len(dl) else math.nan:6.1%}")
    return 0


def test_dt(t: np.ndarray) -> float:
    """The duration of one sample (the last sample counts for one frame)."""
    d = np.diff(t)
    return float(np.median(d[d > 0])) if (d > 0).any() else 0.0


if __name__ == "__main__":
    sys.exit(main())
