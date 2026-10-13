"""Rastreo en la piscina del MWM con luz blanca: ¿en qué parte de los cuadros es la rata lo que se rastrea? (H-P13)

    .venv/bin/python qa/herramientas/rastreo_agua.py VIDEO... [--segundos 60] [--piscina X Y ANCHO ALTO]
                                                              [--codigo DIR_DEL_REPOSITORIO]

Rastrea cada video con la plantilla water_maze y los ajustes de detección por defecto, y compara cada cuadro con una
referencia independiente sacada del color: con luz blanca la rata es cálida (rojo − azul > 30), el agua azul y el
reflejo de la lámpara neutro, así que los píxeles cálidos que difieren del fondo (mediana en color) son la rata. El
rastreador trabaja en gris, donde rata y reflejo tienen el mismo brillo: la referencia no comparte su debilidad.

Clases de cuadro según la referencia:
  * rata: una mancha cálida del tamaño de la rata y nada más cálido — la cifra que decide (≥ 95 % = OK);
  * mano: la rata y además otra mancha cálida grande (el brazo del experimentador al soltarla o sacarla);
  * ausente: nada cálido (la rata aún no está o ya salió): cuenta los cuadros en que igual se «detecta» algo;
  * dudoso: la rata cortada por el borde de la imagen, etc. (no se puntúa).
Acierto = centro rastreado a < 30 px (≈ 7 cm con la piscina de 150 cm) del de la referencia. Da el rastreo crudo (sin
quitar saltos ni rellenar huecos) y el final (como lo guarda la app). La piscina se busca sola (la zona azul de la
mediana) salvo que se dé --piscina. Con luz roja la referencia de color no sirve.
--codigo permite medir otra versión de la app (p. ej. la rama sin el arreglo) sin instalarla.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

ACIERTO_PX = 30.0
UMBRAL_PCT = 95.0
RATA_AREA = (600, 2800)  # px de la rata en 1280x720 con la piscina de ~620 px


def piscina(video: str) -> tuple[float, float, float]:
    """(cx, cy, r) del agua: la zona azul (B − R > 12) más grande de la mediana de 25 cuadros."""
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for t in np.linspace(0, n - 1, 25).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t))
        ok, f = cap.read()
        if ok:
            frames.append(f)
    med = np.median(np.array(frames), 0).astype(np.int16)
    m = cv2.morphologyEx(((med[..., 0] - med[..., 2]) > 12).astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    k, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    ys, xs = np.nonzero(lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])))
    (cx, cy), r = cv2.minEnclosingCircle(np.c_[xs, ys].astype(np.float32))
    return float(cx), float(cy), float(r) - 12  # el círculo envolvente toca la espuma del borde


def referencia(video: str, cx: float, cy: float, r: float, n_max: int) -> np.ndarray:
    """Por cuadro: x, y de la rata por su color y la clase (0 ausente, 1 rata, 2 mano, 3 dudoso)."""
    cap = cv2.VideoCapture(video)
    n = min(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), n_max)
    muestra = []
    for t in np.linspace(0, n - 1, 60).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t))
        ok, f = cap.read()
        if ok:
            muestra.append(f)
    med = np.median(np.array(muestra), 0).astype(np.int16)
    h, w = med.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    agua = np.hypot(xx - cx, yy - cy) < r
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    out, prev = [], None
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            break
        f = f.astype(np.int16)
        m = ((f[..., 2] - f[..., 0]) > 30) & (f.sum(2) > 300) & agua & (np.abs(f - med).sum(2) > 60)
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        k, _, st, ce = cv2.connectedComponentsWithStats(m, 8)
        manchas = [(st[i, cv2.CC_STAT_AREA], ce[i][0], ce[i][1]) for i in range(1, k) if st[i, cv2.CC_STAT_AREA] >= 150]
        ratas = [b for b in manchas if RATA_AREA[0] <= b[0] <= RATA_AREA[1]]
        if not manchas:
            out.append((np.nan, np.nan, 0))
            continue
        if not ratas:
            out.append((np.nan, np.nan, 3))
            continue
        if prev is not None and len(ratas) > 1:
            rata = min(ratas, key=lambda b: (b[1] - prev[0]) ** 2 + (b[2] - prev[1]) ** 2)
        else:
            rata = max(ratas)
        otras = sum(b[0] for b in manchas if b is not rata)
        prev = (rata[1], rata[2])
        out.append((rata[1], rata[2], 1 if otras < 300 else 2))
    return np.array(out, float)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", type=Path, nargs="+")
    ap.add_argument("--segundos", type=float, default=60.0, help="segundos medidos desde el inicio (60: el ensayo)")
    ap.add_argument("--piscina", type=float, nargs=4, metavar=("X", "Y", "ANCHO", "ALTO"),
                    help="rectángulo de la piscina (si no, se busca la zona azul)")
    ap.add_argument("--codigo", type=lambda s: Path(s).resolve(), default=Path(__file__).resolve().parents[2])
    a = ap.parse_args()
    sys.path.insert(0, str(a.codigo))
    from manymaze.core import templates
    from manymaze.core.tracking import ArenaJob, DetectionSettings, postprocess, track_video

    fail = False
    for video in a.videos:
        cap = cv2.VideoCapture(str(video))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.release()
        if a.piscina:
            x0, y0, w0, h0 = a.piscina
            cx, cy, r = x0 + w0 / 2, y0 + h0 / 2, min(w0, h0) / 2
        else:
            cx, cy, r = piscina(str(video))
        app = templates.build("water_maze", cx - r, cy - r, 2 * r, 2 * r)
        app.frame_size = (w, h)
        s = DetectionSettings(duration_s=a.segundos)
        crudo_s = replace(s, remove_implausible_jumps=False, max_jump_speed=0.0, max_gap_s=0.0, smoothing=0)
        crudo = track_video(str(video), [ArenaJob(app, crudo_s)])[0][0]
        final = postprocess(crudo, s, app.scale, bool(app.px_per_cm))
        ref = referencia(str(video), cx, cy, r, int(round(a.segundos * fps)) if a.segundos > 0 else 10**9)
        n = min(len(ref), len(crudo.x))
        clase = ref[:n, 2]
        partes = []
        for nombre, tr in (("crudo", crudo), ("final", final)):
            d = np.hypot(tr.x[:n] - ref[:n, 0], tr.y[:n] - ref[:n, 1])
            for c, cn in ((1, "rata"), (2, "mano")):
                m = clase == c
                if m.any():
                    pct = 100 * float((d[m] < ACIERTO_PX).mean())
                    partes.append(f"{nombre} {cn} {pct:.1f} % de {int(m.sum())}")
                    if nombre == "final" and c == 1 and pct < UMBRAL_PCT:
                        fail = True
            aus = clase == 0
            partes.append(f"{nombre} ausente: algo detectado en {int(np.isfinite(tr.x[:n][aus]).sum())} de "
                          f"{int(aus.sum())}")
        print(f"{video.name} ({a.codigo.name}; piscina {cx:.0f},{cy:.0f} r {r:.0f}): " + "; ".join(partes))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
