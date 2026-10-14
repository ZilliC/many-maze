"""Compara una tabla de resultados exportada desde la interfaz con la de referencia (línea de comandos).

    .venv/bin/python qa/herramientas/comparar_con_referencia.py EXPORTADO.csv REFERENCIA.csv [--tol 1e-6]

Empareja filas por Test (o por Animal + Stage + Trial) y compara cada columna que esté en las dos. Informa columnas
que faltan / sobran, filas que faltan y cada diferencia numérica. Formatos: csv, tsv, xlsx, slk.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspeccionar import table_rows  # noqa: E402


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def load(p: Path, sheet: str | None = None):
    rows = [r for r in (table_rows(p, sheet) or []) if any(str(c).strip() for c in r)]
    head = [str(h).strip() for h in rows[0]]
    return head, [dict(zip(head, r)) for r in rows[1:]]


def _key_text(v) -> str:
    """Key cell as text; 1, 1.0 and "1" are the same (SYLK and XLSX store numbers as floats)."""
    n = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if n is None:
        try:
            n = float(str(v).strip())
        except ValueError:
            return str(v).strip()
    return str(int(n)) if float(n).is_integer() else str(n)


def key_of(r: dict, keys):
    return tuple(_key_text(r.get(k, "")) for k in keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exportado", type=Path)
    ap.add_argument("referencia", type=Path)
    ap.add_argument("--tol", type=float, default=1e-6, help="tolerancia relativa")
    ap.add_argument("--hoja", help="hoja del .xlsx exportado (por defecto «Results»; con una referencia por intervalos "
                    "—«Segment of test» rellena— se añaden las filas de «Time periods»)")
    ap.add_argument("--ignorar", nargs="*", default=["Source video file", "Recorded video file"],
                    help="columnas que no se comparan (por defecto, las rutas de los videos: cambian con las copias)")
    a = ap.parse_args()
    hr, rr = load(a.referencia)
    sheet = a.hoja
    hx, rx = load(a.exportado, sheet)
    if sheet is None and a.exportado.suffix.lower() == ".xlsx" and any(
            str(r.get("Segment of test", "")).strip() for r in rr):
        rx = rx + load(a.exportado, "Time periods")[1]  # the xlsx keeps the time-period rows in their own sheet
    only_x = [h for h in hx if h not in hr]
    only_r = [h for h in hr if h not in hx]
    print(f"exportado: {len(rx)} filas × {len(hx)} columnas   referencia: {len(rr)} filas × {len(hr)} columnas")
    if only_x:
        print(f"columnas solo en el exportado ({len(only_x)}): {only_x[:15]}")
    if only_r:
        print(f"columnas solo en la referencia ({len(only_r)}): {only_r[:15]}")
    keys = [k for k in ("Test", "Animal", "Stage", "Trial", "Apparatus", "Period", "Time period", "Segment of test")
            if k in hx and k in hr] or [hx[0]]
    idx = {key_of(r, keys): r for r in rr}
    diffs, missing = 0, 0
    for r in rx:
        ref = idx.pop(key_of(r, keys), None)
        if ref is None:
            missing += 1
            print(f"fila sin pareja en la referencia: {key_of(r, keys)}")
            continue
        for c in hx:
            if c not in ref or c in a.ignorar:
                continue
            a_, b_ = r[c], ref[c]
            na, nb = num(a_), num(b_)
            if na is not None and nb is not None:
                if math.isnan(na) and math.isnan(nb):
                    continue
                if not math.isclose(na, nb, rel_tol=a.tol, abs_tol=a.tol):
                    diffs += 1
                    if diffs <= 40:
                        print(f"≠ {key_of(r, keys)} «{c}»: exportado {a_} vs referencia {b_}")
            elif str(a_).strip() != str(b_).strip():
                diffs += 1
                if diffs <= 40:
                    print(f"≠ {key_of(r, keys)} «{c}»: exportado «{a_}» vs referencia «{b_}»")
    for k in idx:
        print(f"fila de la referencia que falta en el exportado: {k}")
    total = diffs + missing + len(idx)
    print(f"\n{'IGUALES' if total == 0 else 'DIFERENTES'}: {diffs} celdas distintas, {missing} filas sin pareja, "
          f"{len(idx)} filas que faltan (clave: {keys})")
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
