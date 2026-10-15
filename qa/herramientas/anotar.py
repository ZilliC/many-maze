"""Anotación manual de fotogramas: la verdad de referencia para medir la precisión del rastreo en videos reales.

    .venv/bin/python qa/herramientas/anotar.py VIDEO --n 100 --salida anotaciones.csv [--semilla 1] [--inicio S --fin S]

Muestra N fotogramas sorteados (repartidos por todo el video, en orden aleatorio) y en cada uno pide dos clics:
  1.º clic: el CENTRO del cuerpo del animal (el centro del tronco, sin la cola)
  2.º clic: la NARIZ (la punta del hocico)
Teclas:  espacio / Enter = guardar y siguiente    n = no se ve la nariz (solo el centro)
         x = el animal NO se ve en este fotograma  z = borrar los clics de este fotograma
         b = volver al fotograma anterior          q = guardar y salir (se puede continuar después)
Si el archivo de salida ya existe, sigue donde se quedó. Unos 5–8 s por fotograma: 100 fotogramas ≈ 10 min.

CSV: frame, t, cx, cy, nx, ny, visible (px del video original). Para comparar con la app: comparar_anotaciones.py.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

WIN = "mANY-MAZE · anotación (centro, luego nariz; espacio = siguiente, q = salir)"


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {int(r["frame"]): r for r in csv.DictReader(fh)}


def save(path: Path, rows: dict):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame", "t", "cx", "cy", "nx", "ny", "visible"])
        w.writeheader()
        for f in sorted(rows):
            w.writerow(rows[f])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--inicio", type=float, default=0.0, help="segundo del video desde el que sortear")
    ap.add_argument("--fin", type=float, default=0.0, help="segundo hasta el que sortear (0 = el final)")
    a = ap.parse_args()
    cap = cv2.VideoCapture(str(a.video))
    if not cap.isOpened():
        raise SystemExit(f"no se puede abrir {a.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f0 = int(a.inicio * fps)
    f1 = int(a.fin * fps) if a.fin > 0 else n_total
    rng = np.random.default_rng(a.semilla)
    # estratificado: un fotograma al azar de cada uno de N tramos iguales, luego en orden aleatorio
    edges = np.linspace(f0, f1, a.n + 1).astype(int)
    frames = [int(rng.integers(lo, max(lo + 1, hi))) for lo, hi in zip(edges[:-1], edges[1:])]
    rng.shuffle(frames)
    rows = load(a.salida)
    todo = [f for f in frames if f not in rows]
    print(f"{len(rows)} ya anotados; faltan {len(todo)} de {len(frames)}")
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    clicks: list = []

    def on_mouse(ev, x, y, _flags, _p):
        if ev == cv2.EVENT_LBUTTONDOWN and len(clicks) < 2:
            clicks.append((x / scale, y / scale))

    cv2.setMouseCallback(WIN, on_mouse)
    i = 0
    history = []
    while i < len(todo):
        f = todo[i]
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            i += 1
            continue
        h, w = img.shape[:2]
        scale = min(1.0, 1400 / w, 850 / h)
        clicks.clear()
        while True:
            show = cv2.resize(img, (int(w * scale), int(h * scale)))
            for k, (cx, cy) in enumerate(clicks):
                col = (0, 200, 0) if k == 0 else (0, 0, 255)
                cv2.circle(show, (int(cx * scale), int(cy * scale)), 5, col, 2)
                cv2.putText(show, "centro" if k == 0 else "nariz", (int(cx * scale) + 7, int(cy * scale) - 7),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
            msg = (f"{len(rows) + 1}/{len(frames)}  fotograma {f} ({f / fps:.1f} s)  -  "
                   + ("clic en el CENTRO del cuerpo" if not clicks else "clic en la NARIZ" if len(clicks) == 1
                      else "espacio = guardar y siguiente"))
            cv2.rectangle(show, (0, 0), (show.shape[1], 24), (0, 0, 0), -1)
            cv2.putText(show, msg, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow(WIN, show)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord(" "), 13) and clicks:
                nx, ny = clicks[1] if len(clicks) > 1 else ("", "")
                rows[f] = {"frame": f, "t": round(f / fps, 3), "cx": round(clicks[0][0], 1),
                           "cy": round(clicks[0][1], 1), "nx": nx if nx == "" else round(nx, 1),
                           "ny": ny if ny == "" else round(ny, 1), "visible": 1}
                history.append(f)
                i += 1
                break
            if key == ord("n") and clicks:
                rows[f] = {"frame": f, "t": round(f / fps, 3), "cx": round(clicks[0][0], 1),
                           "cy": round(clicks[0][1], 1), "nx": "", "ny": "", "visible": 1}
                history.append(f)
                i += 1
                break
            if key == ord("x"):
                rows[f] = {"frame": f, "t": round(f / fps, 3), "cx": "", "cy": "", "nx": "", "ny": "", "visible": 0}
                history.append(f)
                i += 1
                break
            if key == ord("z"):
                clicks.clear()
            if key == ord("b") and history:
                prev = history.pop()
                rows.pop(prev, None)
                todo.insert(i, prev) if prev not in todo[i:] else None
                i = todo.index(prev)
                break
            if key == ord("q"):
                save(a.salida, rows)
                print(f"guardado {len(rows)} en {a.salida}")
                cv2.destroyAllWindows()
                return
        if len(rows) % 10 == 0:
            save(a.salida, rows)
    save(a.salida, rows)
    cv2.destroyAllWindows()
    print(f"terminado: {len(rows)} fotogramas en {a.salida}")


if __name__ == "__main__":
    main()
