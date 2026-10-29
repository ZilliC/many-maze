"""Precisión del rastreo de mANY-MAZE frente a fotogramas anotados a mano (anotar.py).

    .venv/bin/python qa/herramientas/comparar_anotaciones.py EXPERIMENTO.mmaze ANOTACIONES.csv [--prueba ID]

Busca la prueba cuyo video es el anotado (o la indicada con --prueba), lee su trayectoria guardada y compara, en
cada fotograma anotado:
  * centro: error (px y cm), mediana, percentil 90, % a ≤ 1 cm y a ≤ 2 cm;
  * nariz: error frente a la cabeza rastreada, y cabeza/cola invertidas (la nariz anotada más cerca de la cola
    rastreada que de la cabeza);
  * detección: animal visible pero no detectado, y detectado cuando la persona dijo que no se veía.
Criterios orientativos (para ratas/ratones a ~5–10 px/cm): mediana del centro ≤ 1 cm, ≥ 90 % a ≤ 2 cm, inversiones
≤ 5 %, sin detecciones perdidas. Escribe el detalle por fotograma en ANOTACIONES_comparado.csv.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from manymaze.core.project import Project  # noqa: E402


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experimento", type=Path)
    ap.add_argument("anotaciones", type=Path)
    ap.add_argument("--prueba", type=int, help="ID de la prueba (por defecto, la del video anotado)")
    ap.add_argument("--video", help="nombre del video anotado, si no coincide con el de la prueba")
    a = ap.parse_args()
    p = Project.load(a.experimento)
    rows = list(csv.DictReader(a.anotaciones.open(encoding="utf-8")))
    if a.prueba is not None:
        test = next(t for t in p.tests if t.id == a.prueba)
    else:
        stem = (a.video or a.anotaciones.stem).lower()
        cands = [t for t in p.tests if Path(t.video).stem.lower() in stem or stem in Path(t.video).stem.lower()]
        if len(cands) != 1:
            raise SystemExit("no sé qué prueba es: usa --prueba ID (pruebas: "
                             + ", ".join(f"{t.id}={Path(t.video).name}" for t in p.tests) + ")")
        test = cands[0]
    tracks = p.load_tracks(test)
    if not tracks:
        raise SystemExit(f"la prueba {test.id} no está rastreada")
    tr = tracks[0]
    app = p.apparatus_of(test)
    ppc = app.px_per_cm or 1.0
    unit = "cm" if app.px_per_cm else "px"
    fps = tr.fps or 25.0
    start = test.start_s or 0.0
    out, ce, ne, inv, miss, phantom = [], [], [], 0, 0, 0
    n_nose = 0
    for r in rows:
        t_video = num(r["t"])
        i = int(round((t_video - start) * fps))
        if i < 0 or i >= len(tr):
            continue
        det = bool(tr.detected[i]) and np.isfinite(tr.x[i])
        vis = str(r.get("visible", "1")) == "1"
        rec = {"frame": r["frame"], "t": r["t"], "visible": int(vis), "detectado": int(det)}
        if vis and not det:
            miss += 1
        if not vis and det:
            phantom += 1
        if vis and det:
            cx, cy = num(r["cx"]), num(r["cy"])
            e = float(np.hypot(tr.x[i] - cx, tr.y[i] - cy))
            ce.append(e)
            rec["error_centro_px"] = round(e, 2)
            nx, ny = num(r.get("nx")), num(r.get("ny"))
            if np.isfinite(nx) and tr.hx is not None and np.isfinite(tr.hx[i]):
                n_nose += 1
                eh = float(np.hypot(tr.hx[i] - nx, tr.hy[i] - ny))
                et = float(np.hypot(tr.tx[i] - nx, tr.ty[i] - ny)) if tr.tx is not None and np.isfinite(tr.tx[i]) \
                    else np.inf
                ne.append(eh)
                rec["error_nariz_px"] = round(eh, 2)
                if et < eh:
                    inv += 1
                    rec["invertida"] = 1
        out.append(rec)
    ce_a, ne_a = np.array(ce), np.array(ne)
    lines = [f"Prueba {test.id} ({Path(test.video).name}): {len(out)} fotogramas anotados comparados; {ppc:.2f} px/{unit}"]
    if len(ce_a):
        lines += [f"centro: mediana {np.median(ce_a):.1f} px = {np.median(ce_a) / ppc:.2f} {unit}; "
                  f"p90 {np.percentile(ce_a, 90) / ppc:.2f} {unit}; máx {ce_a.max() / ppc:.2f} {unit}",
                  f"        a ≤ 1 cm: {100 * np.mean(ce_a / ppc <= 1):.0f} %   a ≤ 2 cm: {100 * np.mean(ce_a / ppc <= 2):.0f} %"]
    if len(ne_a):
        lines += [f"nariz:  mediana {np.median(ne_a) / ppc:.2f} {unit}; p90 {np.percentile(ne_a, 90) / ppc:.2f} {unit};"
                  f" cabeza/cola invertidas en {inv} de {n_nose} ({100 * inv / n_nose:.1f} %)"]
    lines += [f"detección: {miss} fotogramas con el animal visible y no detectado; {phantom} detectado sin animal"]
    crit = []
    if len(ce_a):
        crit += [("mediana del centro ≤ 1 cm", np.median(ce_a) / ppc <= 1.0),
                 ("≥ 90 % del centro a ≤ 2 cm", np.mean(ce_a / ppc <= 2) >= 0.9)]
    if n_nose:
        crit.append(("inversiones cabeza/cola ≤ 5 %", inv / n_nose <= 0.05))
    crit.append(("ninguna detección perdida", miss == 0))
    lines += ["", "Criterios (orientativos):"] + [f"  {'OK   ' if c else 'FALLA'} {t}" for t, c in crit]
    text = "\n".join(lines)
    print(text)
    det_path = a.anotaciones.with_name(a.anotaciones.stem + "_comparado.csv")
    keys = ["frame", "t", "visible", "detectado", "error_centro_px", "error_nariz_px", "invertida"]
    with det_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(out)
    a.anotaciones.with_name(a.anotaciones.stem + "_resumen.txt").write_text(text + "\n", encoding="utf-8")
    sys.exit(0 if all(c for _, c in crit) else 1)


if __name__ == "__main__":
    main()
