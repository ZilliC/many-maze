"""Prueba de larga duración de las pruebas en vivo: varias sesiones a la vez (el mismo código que la página Run
tests), horas de tiempo de prueba, midiendo memoria, coste por cuadro y que al final todo se guarde bien.

    .venv/bin/python qa/herramientas/prueba_larga.py --horas 2 --sesiones 4             # lo más rápido posible
    .venv/bin/python qa/herramientas/prueba_larga.py --horas 0.5 --sesiones 8 --tiempo-real
    .venv/bin/python qa/herramientas/prueba_larga.py --horas 24 --sesiones 1 --salida DIR  # una prueba de 24 h
    .venv/bin/python qa/herramientas/prueba_larga.py --horas 1 --sesiones 4 --autosave      # con archivos de rescate

Cada sesión recibe cuadros sintéticos (un ratón que camina por un campo abierto, con pausas) a 25 cuadros/s de tiempo
de prueba. Sin --tiempo-real se procesan tan rápido como se pueda (24 h de prueba tardan bastante menos de 24 h); con
--tiempo-real se respeta el reloj, como con cámaras. Al terminar, cada sesión se guarda como una prueba de un
experimento (save_live_test) y se calculan sus resultados. Con --autosave cada sesión escribe su archivo de rescate
(crash recovery) cada 5 s en la carpeta recordings del experimento, como en la página Run tests.

Informe (en --salida, por defecto la carpeta actual): prueba_larga.csv con una fila por minuto de tiempo de prueba
(memoria, ms por cuadro por sesión) y prueba_larga.txt con el resumen y los criterios:
  * memoria: crecimiento por hora de prueba y sesión, extrapolado a 24 h con 1, 8 y 40 pruebas frente a la memoria
    del equipo (la escala anunciada: 40 pruebas simultáneas, pruebas de 24 h);
  * velocidad: ms por cuadro × sesiones < 40 ms (25 cuadros/s) para poder ir en tiempo real;
  * al final: cada prueba guardada con todos sus cuadros, sus resultados calculados, distancia > 0, sin errores.
"""

from __future__ import annotations

import argparse
import csv
import math
import resource
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from manymaze.core import templates  # noqa: E402
from manymaze.core.live import LiveSession  # noqa: E402
from manymaze.core.measures import AnalysisSettings  # noqa: E402
from manymaze.core.project import Project  # noqa: E402
from manymaze.core.session import save_live_test  # noqa: E402
from manymaze.core.tracking import DetectionSettings  # noqa: E402

FPS = 25.0


def rss_mb() -> float:
    """Memoria máxima residente del proceso (MB; en macOS ru_maxrss viene en bytes)."""
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def current_rss_mb() -> float:
    try:
        import subprocess

        out = subprocess.run(["ps", "-o", "rss=", "-p", str(__import__("os").getpid())], capture_output=True, text=True)
        return int(out.stdout.strip()) / 1024
    except Exception:  # noqa: BLE001
        return rss_mb()


class Walker:
    """Un ratón sintético: caminata aleatoria con pausas, dibujado sobre un suelo claro."""

    def __init__(self, size, seed):
        self.w, self.h = size
        self.rng = np.random.default_rng(seed)
        self.p = np.array([self.w / 2, self.h / 2])
        self.a = self.rng.uniform(0, 2 * math.pi)
        self.pause = 0
        self.floor = np.full((self.h, self.w), 200, np.uint8)

    def frame(self):
        if self.pause > 0:
            self.pause -= 1
        else:
            if self.rng.random() < 0.004:
                self.pause = int(self.rng.integers(25, 100))
            self.a += self.rng.normal(0, 0.3)
            q = self.p + 3.0 * np.array([math.cos(self.a), math.sin(self.a)])
            m = 30
            if not (m < q[0] < self.w - m and m < q[1] < self.h - m):
                self.a += math.pi * self.rng.uniform(0.6, 1.4)
            else:
                self.p = q
        img = self.floor.copy()
        cv2.ellipse(img, (int(self.p[0]), int(self.p[1])), (14, 7), math.degrees(self.a), 0, 360, 40, -1)
        img = np.clip(img.astype(np.int16) + self.rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horas", type=float, default=2.0, help="horas de tiempo de prueba")
    ap.add_argument("--sesiones", type=int, default=4)
    ap.add_argument("--tamano", default="320x240")
    ap.add_argument("--tiempo-real", action="store_true")
    ap.add_argument("--salida", type=Path, default=Path("."))
    ap.add_argument("--autosave", action="store_true", help="archivo de rescate cada 5 s, como en Run tests")
    a = ap.parse_args()
    w, h = map(int, a.tamano.lower().split("x"))
    out = a.salida.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    app = templates.build("open_field", 20, 20, w - 40, h - 40, size_cm=40)
    app.frame_size = (w, h)
    walkers = [Walker((w, h), seed=i) for i in range(a.sesiones)]
    proj_dir = Path(tempfile.mkdtemp()) / "larga.mmaze"
    (proj_dir / "recordings").mkdir(parents=True)
    sessions = []
    for i in range(a.sesiones):
        side = {"autosave_path": str(proj_dir / "recordings" / f"test_{i + 1:04d}_L{i + 1}.autosave.json"),
                "autosave_meta": {"test_id": i + 1, "animal": f"L{i + 1}"}} if a.autosave else {}
        s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0, name=f"larga{i + 1}", **side)
        s.set_background(cv2.cvtColor(walkers[i].floor, cv2.COLOR_GRAY2BGR))
        sessions.append(s)
    n_frames = int(a.horas * 3600 * FPS)
    per_min = int(60 * FPS)
    rows = []
    t_proc = np.zeros(a.sesiones)
    t0_wall = time.monotonic()
    rss0 = current_rss_mb()
    errors = []
    print(f"{a.sesiones} sesiones × {a.horas:g} h de prueba = {n_frames * a.sesiones:,} cuadros; memoria inicial "
          f"{rss0:.0f} MB", flush=True)
    for f in range(n_frames):
        ts = f / FPS
        for i, s in enumerate(sessions):
            fr = walkers[i].frame()
            c0 = time.perf_counter()
            try:
                s.process(fr, ts)
            except Exception as e:  # noqa: BLE001
                errors.append(f"cuadro {f} sesión {i + 1}: {type(e).__name__}: {e}")
                if len(errors) > 20:
                    break
            t_proc[i] += time.perf_counter() - c0
        if a.tiempo_real:
            ahead = ts - (time.monotonic() - t0_wall)
            if ahead > 0:
                time.sleep(ahead)
        if (f + 1) % per_min == 0:
            rss = current_rss_mb()
            ms = 1000 * t_proc / per_min
            rows.append({"minuto": (f + 1) // per_min, "memoria_MB": round(rss, 1),
                         "ms_por_cuadro_medio": round(float(ms.mean()), 2), "ms_por_cuadro_max": round(float(ms.max()), 2),
                         "reloj_s": round(time.monotonic() - t0_wall, 1)})
            t_proc[:] = 0
            if rows[-1]["minuto"] % 10 == 0:
                print(f"  {rows[-1]['minuto']:5d} min de prueba: {rss:.0f} MB, {ms.mean():.2f} ms/cuadro/sesión", flush=True)
        if len(errors) > 20:
            break
    with (out / "prueba_larga.csv").open("w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["minuto"])
        wtr.writeheader()
        wtr.writerows(rows)
    # guardar cada sesión como una prueba y calcular sus resultados
    p = Project(name="Prueba larga", protocol="open_field")
    p.analysis = AnalysisSettings.for_new_experiment()
    p.path = proj_dir
    p.apparatus.append(app)
    saved = []
    side_mb, side_s = [], []
    for i, s in enumerate(sessions):
        try:
            s.finish()
            if a.autosave:
                s.flush_autosave(60.0)
                side_mb.append(sum(f.stat().st_size for f in (proj_dir / "recordings").glob(f"test_{i + 1:04d}_*"))
                               / 2 ** 20)
                side_s.append(s._autosaver.last_write_s)
            p.ensure_animal(f"L{i + 1}", "G")
            t = p.add_test("", f"L{i + 1}", app.name, stage="S", trial=1)
            ok = save_live_test(p, t, s)
            tr = p.load_tracks(t)[0] if ok else None
            saved.append((ok, len(tr) if tr is not None else 0))
            s.remove_autosave()
        except Exception as e:  # noqa: BLE001
            errors.append(f"guardar sesión {i + 1}: {type(e).__name__}: {e}")
            saved.append((False, 0))
    p.save()
    try:
        res = p.results()
    except Exception as e:  # noqa: BLE001
        res = []
        errors.append(f"resultados: {type(e).__name__}: {e}")
    # resumen
    lines = [f"Prueba larga: {a.sesiones} sesiones × {a.horas:g} h ({'tiempo real' if a.tiempo_real else 'lo más rápido'})",
             f"reloj: {time.monotonic() - t0_wall:.0f} s; memoria máxima del proceso (pico) {rss_mb():.0f} MB"]
    if side_mb:
        lines.append(f"archivos de rescate al final: {max(side_mb):.1f} MB por sesión; la última escritura tardó "
                     f"{max(side_s):.2f} s")
    if rows:
        mem = np.array([r["memoria_MB"] for r in rows])
        hours = np.array([r["minuto"] for r in rows]) / 60
        half = len(mem) // 2
        slope = float(np.polyfit(hours[half:], mem[half:], 1)[0]) if len(mem) >= 4 else math.nan
        ms_mean = float(np.mean([r["ms_por_cuadro_medio"] for r in rows]))
        ms_p95 = float(np.percentile([r["ms_por_cuadro_max"] for r in rows], 95))
        lines += [f"memoria: inicial {rss0:.0f} MB, final {mem[-1]:.0f} MB, crecimiento en la segunda mitad "
                  f"{slope:.1f} MB por hora de prueba",
                  f"coste: {ms_mean:.2f} ms por cuadro y sesión de media (p95 de los máximos por minuto {ms_p95:.2f});"
                  f" {a.sesiones} sesiones necesitan {ms_mean * a.sesiones:.1f} ms por cuadro (límite de 25 cuadros/s: 40)"]
        import subprocess

        ram_gb = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0) / 2 ** 30
        per_session_h = slope / a.sesiones if math.isfinite(slope) else math.nan
        lines.append(f"memoria por sesión: {per_session_h:.0f} MB por hora de prueba "
                     f"({per_session_h * 1024 / (3600 * FPS):.2f} KB por cuadro); este equipo tiene {ram_gb:.0f} GB")
        for n_s, hh in ((1, 24), (8, 24), (40, 24), (40, 2)):
            need = per_session_h * n_s * hh / 1024
            lines.append(f"  {n_s:2d} pruebas de {hh:2d} h → {need:5.1f} GB ({'cabe' if need < 0.5 * ram_gb else 'NO cabe'} "
                         f"en la mitad de la memoria)")
        crit = [(f"el escenario pedido cabe en la mitad de la memoria ({a.sesiones} × {a.horas:g} h)",
                 per_session_h * a.sesiones * a.horas / 1024 < 0.5 * ram_gb),
                ("40 pruebas de 24 h (la escala anunciada) caben en la mitad de la memoria",
                 per_session_h * 40 * 24 / 1024 < 0.5 * ram_gb),
                ("cabe en tiempo real (sesiones × ms medio < 40 ms)", ms_mean * a.sesiones < 40)]
    else:
        crit = []
    expected = n_frames
    for i, (ok, n) in enumerate(saved):
        lines.append(f"sesión {i + 1}: guardada {'sí' if ok else 'NO'}, {n:,} posiciones de {expected:,}")
        crit.append((f"sesión {i + 1} guardada con todos sus cuadros", ok and n >= expected - 1))
    if res:
        d = [r.get("Total distance (cm)") for r in res]
        lines.append("distancias (cm): " + ", ".join(f"{v:.0f}" for v in d if v is not None))
        crit.append(("resultados con distancia > 0", all(v and v > 0 for v in d)))
    crit.append(("sin errores", not errors))
    lines += ["", "Criterios:"] + [f"  {'OK   ' if c else 'FALLA'} {t}" for t, c in crit]
    if errors:
        lines += ["", "Errores:"] + [f"  {e}" for e in errors[:20]]
    text = "\n".join(lines)
    (out / "prueba_larga.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    sys.exit(0 if all(c for _, c in crit) else 1)


if __name__ == "__main__":
    main()
