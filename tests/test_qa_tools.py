"""The command-line helpers of the QA kit (qa/herramientas): they read what the interface exports in every format."""

import subprocess
import sys
from pathlib import Path

from manymaze.core.export import write_table, write_xlsx

TOOLS = Path(__file__).resolve().parents[1] / "qa" / "herramientas"
ROWS = [{"Test": i, "Animal": f"A{i}", "Period": "Whole test", "Total distance (cm)": 1.5 * i} for i in (1, 2)]


def run(tool, *args):
    return subprocess.run([sys.executable, str(TOOLS / tool), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_compare_slk_and_xlsx_match_csv(tmp_path):
    write_table(ROWS, tmp_path / "ref.csv")
    for ext in ("slk", "xlsx", "tsv"):
        write_table(ROWS, tmp_path / f"out.{ext}")
        r = run("comparar_con_referencia.py", tmp_path / f"out.{ext}", tmp_path / "ref.csv")
        assert r.returncode == 0 and "IGUALES" in r.stdout, (ext, r.stdout, r.stderr)


def test_compare_xlsx_with_time_periods_sheet(tmp_path):
    whole = [{"Test": 1, "Animal": "A1", "Period": "Whole test", "Segment of test": "", "Total distance (cm)": 3.0}]
    bins = [{"Test": 1, "Animal": "A1", "Period": "0-10 s", "Segment of test": 1, "Total distance (cm)": 1.0}]
    write_table(whole + bins, tmp_path / "ref.csv")
    write_xlsx({"Results": whole, "Time periods": bins}, tmp_path / "out.xlsx")
    r = run("comparar_con_referencia.py", tmp_path / "out.xlsx", tmp_path / "ref.csv")
    assert r.returncode == 0 and "IGUALES" in r.stdout, r.stdout + r.stderr


def test_inspect_missing_file_and_raw_data_comment_line(tmp_path):
    r = run("inspeccionar.py", tmp_path / "nope.csv")
    assert r.returncode == 1 and "Traceback" not in r.stderr and "no existe" in r.stdout
    raw = tmp_path / "raw.csv"
    raw.write_text("# Test 1, animal C1, unit cm\nTime (s),x,y\n0.0,1,2\n0.1,2,3\n")
    r = run("inspeccionar.py", raw)
    assert r.returncode == 0 and "Traceback" not in r.stderr and "filas de datos: 2" in r.stdout, r.stdout + r.stderr
    one = tmp_path / "one.csv"  # a single column: no delimiter to sniff
    one.write_text("a\n1\n2\n")
    assert run("inspeccionar.py", one).returncode == 0


def test_cabeza_real_threshold_follows_the_frame_rate():
    """H-029: "advances" was fixed at 2 px per frame (60 px/s at 30 fps), so at 60 fps a rat walking at that
    speed (1 px per frame) counted as standing still."""
    import importlib.util
    from types import SimpleNamespace

    import numpy as np

    spec = importlib.util.spec_from_file_location("cabeza_real", TOOLS / "cabeza_real.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for fps in (30.0, 60.0):
        n = 120
        x = np.arange(n) * (90.0 / fps)  # 90 px/s, head in front
        tr = SimpleNamespace(x=x, y=np.zeros(n), hx=x + 5, hy=np.zeros(n))
        moving, against, _, _ = mod.medir(tr, fps)
        assert moving > 100 and against == 0, (fps, moving)
        slow = SimpleNamespace(x=x / 10, y=np.zeros(n), hx=x / 10 + 5, hy=np.zeros(n))  # 9 px/s: standing
        assert mod.medir(slow, fps)[0] == 0
