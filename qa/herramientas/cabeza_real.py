"""Orientación de la cabeza en un video real: ¿apunta la cabeza rastreada hacia atrás mientras el animal avanza?

    .venv/bin/python qa/herramientas/cabeza_real.py VIDEO [--segundos 150] [--inicio 0] [--recorte X Y ANCHO ALTO]...
                                                          [--codigo DIR_DEL_REPOSITORIO]

Rastrea el video (o cada arena, con un --recorte por arena) y, en los cuadros en que el animal avanza más de 2 px por
cuadro (dirección de marcha medida sobre 5 cuadros), mira si la cabeza rastreada apunta contra la marcha. Un roedor
camina con la cabeza por delante. Da dos cifras:

  * contra la marcha (%): todos los cuadros en movimiento con la cabeza hacia atrás. Incluye los ratos en que la
    rata retrocede de verdad (al bajar de un rearing o apartarse de la pared), que no son errores;
  * invertida sostenida (%): solo los tramos de ≥ 1 s seguidos con la cabeza contra la marcha. Una rata no
    retrocede tanto rato: esto es la inversión cabeza/cola de H-P01, y es la cifra que decide (≤ 5 % = OK).

Se descartan los saltos de posición (el centro se mueve más de 4 veces la mediana de lo que avanza en un cuadro:
fragmentos del animal sobre un fondo sin contraste, manos del experimentador), porque en ellos la dirección de
marcha no significa nada.

Arenas: si el video tiene dos aparatos (los OFT de las ratas: dos campos abiertos lado a lado, una rata en cada
uno), sin --recorte el rastreo salta de una rata a otra y la cifra no vale. Para los videos conocidos los recortes
se ponen solos (ARENAS, abajo). En agua (MWM, FST) la cola va sumergida y hay reflejos: usa --recorte con la piscina
o el cilindro y toma el resultado como orientativo.
--codigo permite medir otra versión de la app (p. ej. la rama con un arreglo) sin instalarla.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# recortes (x, y, ancho, alto) de cada arena en los videos con dos campos abiertos (H-P01, sesión del arreglo)
ARENAS = {
    "2025-04-22 20-10-36": {"izquierda": (130, 80, 525, 520), "derecha": (655, 80, 505, 520)},
    "2025-04-22 22-35-14": {"izquierda": (95, 85, 520, 515), "derecha": (615, 80, 505, 515)},
    "2025-04-22 20-32-43": {"izquierda": (130, 60, 525, 580), "derecha": (655, 60, 505, 580)},
}
SOSTENIDA_S = 1.0
UMBRAL_PCT = 5.0


def runs(mask):
    """(inicio, fin) de los tramos True."""
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def medir(tr, fps):
    """(cuadros en movimiento, % contra la marcha, % invertida sostenida, saltos descartados)."""
    vx = np.r_[np.full(5, np.nan), tr.x[5:] - tr.x[:-5]]
    vy = np.r_[np.full(5, np.nan), tr.y[5:] - tr.y[:-5]]
    step = np.r_[np.nan, np.hypot(np.diff(tr.x), np.diff(tr.y))]
    moving = (np.hypot(vx, vy) / 5 > 2) & np.isfinite(tr.hx)
    typical = np.nanmedian(step[moving]) if moving.any() else np.nan
    jump = np.zeros(len(step), bool)
    if np.isfinite(typical) and typical > 0:
        big = np.nan_to_num(step, nan=0.0) > 4 * typical
        for k in np.flatnonzero(big):  # la dirección sobre 5 cuadros queda contaminada por el salto
            jump[k:k + 5] = True
    moving &= ~jump
    against = moving & ((tr.hx - tr.x) * vx + (tr.hy - tr.y) * vy < 0)
    n = int(moving.sum())
    # tramos seguidos (entre los cuadros en movimiento) con la cabeza contra la marcha
    seq = against[moving]
    sustained = sum(b - a for a, b in runs(seq) if b - a >= SOSTENIDA_S * fps)
    return n, 100 * against.sum() / max(n, 1), 100 * sustained / max(n, 1), int(jump.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--segundos", type=float, default=150.0)
    ap.add_argument("--inicio", type=float, default=0.0, help="segundo del video desde el que medir")
    ap.add_argument("--recorte", type=int, nargs=4, action="append", metavar=("X", "Y", "ANCHO", "ALTO"),
                    help="rastrear solo este rectángulo (una arena, la piscina…); se puede repetir, una vez por arena")
    ap.add_argument("--codigo", type=lambda s: Path(s).resolve(), default=Path(__file__).resolve().parents[2])
    a = ap.parse_args()
    sys.path.insert(0, str(a.codigo))
    from manymaze.core import templates
    from manymaze.core.tracking import ArenaJob, DetectionSettings, track_video

    cap = cv2.VideoCapture(str(a.video))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()
    if a.recorte:
        boxes = {f"recorte {i + 1}": tuple(r) for i, r in enumerate(a.recorte)}
    else:
        boxes = ARENAS.get(a.video.stem, {"imagen entera": (0, 0, w, h)})
    jobs = []
    for x0, y0, w0, h0 in boxes.values():
        app = templates.build("custom", x0, y0, w0, h0, width_cm=0)
        app.frame_size = (w, h)
        jobs.append(ArenaJob(app, DetectionSettings(start_time_s=a.inicio, duration_s=a.segundos)))
    tracks = track_video(str(a.video), jobs)
    fail = False
    for name, tr in zip(boxes, (t[0] for t in tracks)):
        n, pct, sus, jumps = medir(tr, fps)
        ok = sus <= UMBRAL_PCT
        fail |= not ok
        print(f"{a.video.name} [{name}] ({a.codigo.name}): {n} cuadros en movimiento; cabeza contra la marcha "
              f"{pct:.1f} %; invertida sostenida (≥ {SOSTENIDA_S:g} s) {sus:.1f} % ({'OK' if ok else 'FALLA: H-P01'})"
              f"; {jumps} cuadros descartados por saltos")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
