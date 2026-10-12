"""Oráculo de verdad conocida: un experimento por plantilla (las 23) con un video sintético cuyo recorrido está
programado, y los resultados ESPERADOS calculados a partir de ese recorrido, sin usar el motor de medidas de la app.

    .venv/bin/python qa/herramientas/oraculo_sintetico.py generar DEST          # crea DEST/<plantilla>.mmaze
    .venv/bin/python qa/herramientas/oraculo_sintetico.py verificar EXPORTADO.csv DEST/<plantilla>.mmaze/esperado.csv
    .venv/bin/python qa/herramientas/oraculo_sintetico.py autoverificar DEST     # rastrea con el motor y compara todo

Qué es independiente y qué no:
  * El recorrido (posición y orientación del animal en cada cuadro) lo decide este script: es la verdad.
  * Para saber si un punto está dentro de una zona se usa la forma de la zona (`shape.contains`), que es geometría
    sencilla. Los puntos de parada se eligen en lo más profundo de cada zona (lejos de los bordes) para que ese
    juicio no sea ambiguo.
  * TODO lo demás se calcula aquí, a partir de las definiciones de la guía del usuario y de la literatura: tiempos,
    entradas, latencias, distancias, inmovilidad, alternancia, errores, índices, cabeceos, revoluciones…
  * Cuando una medida tiene varias definiciones publicadas, `esperado.csv` lleva la principal y, en la nota, la
    alternativa: si la app coincide con la alternativa no es un fallo de cálculo, pero sí hay que documentarlo.

Cada `esperado.csv` tiene: Animal, Apparatus, Measure, Expected, Tolerance, Note. Para comparar, la tabla exportada
(Results ▸ Data ▸ Save ▸ CSV, todas las medidas) debe tener las columnas Animal, Apparatus y las medidas.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from manymaze.core import synthetic as syn  # noqa: E402
from manymaze.core import templates  # noqa: E402
from manymaze.core.measures import AnalysisSettings  # noqa: E402
from manymaze.core.project import Project  # noqa: E402

FPS = 25.0
SIZE = (640, 480)
BBOX = (60, 40, 520, 400)  # x, y, w, h donde se coloca cada plantilla
BODY = 36  # largo del cuerpo dibujado (px)
NOSE = 0.77 * BODY  # distancia centro → nariz (synthetic.draw_mouse)


# ------------------------------------------------------------------------------------------------ recorrido
@dataclass
class Path_:
    pos: list = field(default_factory=list)  # (x, y) por cuadro
    ang: list = field(default_factory=list)  # grados por cuadro
    speed: float = 4.0  # px por cuadro al desplazarse
    turn_rate: float = 15.0  # grados por cuadro al girar sobre sí mismo (180 = media vuelta en un cuadro)

    @property
    def here(self):
        return np.array(self.pos[-1], float)

    def start(self, p, angle=0.0):
        self.pos.append(tuple(map(float, p)))
        self.ang.append(float(angle))

    def turn_to(self, heading, deg_per_frame=None):
        """Gira sobre sí mismo hasta `heading` (por el lado más corto; 180° exactos, en sentido horario)."""
        deg_per_frame = deg_per_frame or self.turn_rate
        cur = self.ang[-1]
        delta = (heading - cur + 180.0) % 360.0 - 180.0
        if abs(delta) == 180.0:
            delta = 180.0
        steps = int(math.ceil(abs(delta) / deg_per_frame))
        for k in range(1, steps + 1):
            self.pos.append(self.pos[-1])
            self.ang.append(cur + delta * k / steps)

    def move(self, p, speed=None):
        a, b = self.here, np.asarray(p, float)
        d = float(np.hypot(*(b - a)))
        if d < 1e-9:
            return
        n = max(2, int(math.ceil(d / (speed or self.speed))))
        heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if abs((heading - self.ang[-1] + 180.0) % 360.0 - 180.0) > 1.0:
            self.turn_to(heading)
        for k in range(1, n + 1):
            q = a + (b - a) * k / n
            self.pos.append((float(q[0]), float(q[1])))
            self.ang.append(heading)

    def stay(self, seconds):
        for _ in range(int(round(seconds * FPS))):
            self.pos.append(self.pos[-1])
            self.ang.append(self.ang[-1])

    def struggle(self, seconds, amp=25.0, hz=3.0):
        """Quieto en el sitio pero forcejeando (el cuerpo gira ±amp grados): mucho cambio de píxeles."""
        base = self.ang[-1]
        for k in range(int(round(seconds * FPS))):
            self.pos.append(self.pos[-1])
            self.ang.append(base + amp * math.sin(2 * math.pi * hz * (k + 1) / FPS))
        self.ang[-1] = base

    def swim(self, seconds, c, r, amp=25.0, hz=3.0, speed=3.0):
        """Nada (se desplaza en círculo alrededor de c) forcejeando: el cuerpo oscila ±amp grados."""
        cx, cy = c
        a0 = math.atan2(self.here[1] - cy, self.here[0] - cx)
        n = int(round(seconds * FPS))
        da = speed / max(r, 1e-6)
        for k in range(1, n + 1):
            a = a0 + da * k
            self.pos.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            self.ang.append(math.degrees(a) + 90 + amp * math.sin(2 * math.pi * hz * k / FPS))

    def circle(self, c, r, laps, clockwise=True, speed=None):
        """Vueltas completas alrededor de c (en la imagen, y hacia abajo: «clockwise» = ángulo creciente)."""
        cx, cy = c
        a0 = math.atan2(self.here[1] - cy, self.here[0] - cx)
        self.move((cx + r * math.cos(a0), cy + r * math.sin(a0)), speed)
        step = (speed or self.speed) / r
        n = int(math.ceil(2 * math.pi * laps / step))
        sgn = 1 if clockwise else -1
        self.turn_to(math.degrees(a0) + sgn * 90)
        for k in range(1, n + 1):
            a = a0 + sgn * 2 * math.pi * laps * k / n
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            self.ang.append(math.degrees(a) + sgn * 90)
            self.pos.append((x, y))

    def arrays(self):
        return np.array(self.pos, float), np.array(self.ang, float)


def deep_point(app, include: str, exclude=(), avoid_px=0.0):
    """El punto más alejado del borde de la región (include − exclude): ahí la pertenencia no es ambigua."""
    xs, ys = np.meshgrid(np.arange(0, SIZE[0], 2.0), np.arange(0, SIZE[1], 2.0))
    memb = app.zone_membership(xs.ravel(), ys.ravel())
    m = memb[include].copy()
    for e in exclude:
        m &= ~memb[e]
    img = m.reshape(xs.shape).astype(np.uint8)
    if not img.any():
        raise ValueError(f"región vacía: {include} − {exclude}")
    dist = cv2.distanceTransform(img, cv2.DIST_L2, 5)
    iy, ix = np.unravel_index(int(np.argmax(dist)), dist.shape)
    depth = float(dist[iy, ix]) * 2
    if depth < avoid_px:
        print(f"  aviso: la región {include} solo tiene {depth:.0f} px de profundidad (< {avoid_px:.0f})")
    return float(xs[iy, ix]), float(ys[iy, ix])


def point_xy(app, name):
    p = app.point(name)
    return float(p.x), float(p.y)


# ------------------------------------------------------------------------------------------------ verdad → esperado
@dataclass
class Truth:
    app: object
    pos: np.ndarray
    ang: np.ndarray
    px_per_cm: float

    @property
    def n(self):
        return len(self.pos)

    @property
    def duration(self):
        return self.n / FPS

    nose_len: float = NOSE

    @property
    def nose(self):
        a = np.radians(self.ang)
        return np.column_stack([self.pos[:, 0] + self.nose_len * np.cos(a), self.pos[:, 1] + self.nose_len * np.sin(a)])

    def heading(self):
        return np.radians(self.ang)

    def facing(self, x, y, max_deg=45.0):
        """El cuerpo (centro → nariz) apunta al punto (x, y) con un error ≤ max_deg."""
        to = np.arctan2(y - self.pos[:, 1], x - self.pos[:, 0])
        err = np.degrees(np.abs((to - self.heading() + np.pi) % (2 * np.pi) - np.pi))
        return err <= max_deg

    def nose_near(self, x, y, r_px):
        nz = self.nose
        return np.hypot(nz[:, 0] - x, nz[:, 1] - y) <= r_px

    def memb(self, xy=None):
        if xy is None and getattr(self, "_memb", None) is not None:
            return self._memb  # pertenencia según la regla de cada zona (oraculo_funciones.membership)
        xy = self.pos if xy is None else xy
        return self.app.zone_membership(xy[:, 0], xy[:, 1])

    def cm(self, px):
        return px / self.px_per_cm if self.px_per_cm else px


def _smooth_steps(t):
    """Pasos (cm) con las posiciones suavizadas 0,2 s, como define la guía las distancias."""
    from oraculo_medidas import Kin

    return Kin(t.pos, t.ang, t.nose, t.px_per_cm).step


def entries(inside: np.ndarray) -> int:
    """Entradas = paso de fuera a dentro; empezar dentro cuenta como entrada (count_initial_entry, por defecto)."""
    inside = inside.astype(bool)
    return int(inside[0]) + int(np.sum(~inside[:-1] & inside[1:]))


def latency(inside: np.ndarray, duration: float):
    idx = np.flatnonzero(inside)
    return float(idx[0] / FPS) if len(idx) else duration  # latencia de algo que nunca ocurre = duración


def runs(mask: np.ndarray):
    """[(inicio, fin)) de cada tramo True."""
    m = np.concatenate([[False], mask.astype(bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def entry_sequence(t: Truth, names, xy=None):
    """Secuencia de zonas en las que entra el centro (o los puntos `xy`, p. ej. la nariz), en orden."""
    m = t.memb(xy)
    seq = []
    for i in range(t.n):
        for z in names:
            if m[z][i] and (i == 0 or not m[z][i - 1]):
                seq.append(z)
    return seq


class TrackTruth(Truth):
    """La trayectoria que rastreó la app (capa B del oráculo): mismas fórmulas, otra entrada. Así una diferencia en
    la capa B es de cálculo (o de definición), no de rastreo."""

    def __init__(self, app, track, px_per_cm):
        x, y = _ffill(track.x), _ffill(track.y)
        hx = _ffill(track.hx) if track.hx is not None else x
        hy = _ffill(track.hy) if track.hy is not None else y
        ang = np.degrees(np.arctan2(hy - y, hx - x))  # orientación: centro → cabeza (la guía)
        super().__init__(app, np.column_stack([x, y]), ang, px_per_cm)
        self._nose = np.column_stack([hx, hy])
        if track.tx is not None:
            self.tail = np.column_stack([_ffill(track.tx), _ffill(track.ty)])
        self.detected = None if track.detected is None else np.asarray(track.detected, bool)
        self.freezing = None
        if track.motion is not None and track.area is not None:
            area = np.where(np.asarray(track.area, float) > 0, track.area, np.nan)
            pct = 100 * np.asarray(track.motion, float) / np.nanmedian(area)
            frozen = np.zeros(len(pct), bool)
            on = False
            for i, v in enumerate(pct):  # histéresis: empieza < 2 %, termina > 3 %
                on = (v < 2.0) if not on else not (v > 3.0)
                frozen[i] = on
            self.freezing = frozen

    @property
    def nose(self):
        return self._nose


def _ffill(a):
    a = np.asarray(a, float).copy()
    for i in range(1, len(a)):
        if not np.isfinite(a[i]):
            a[i] = a[i - 1]
    if len(a) and not np.isfinite(a[0]):
        good = a[np.isfinite(a)]
        a[~np.isfinite(a)] = good[0] if len(good) else 0.0
    return a


class Expect:
    def __init__(self, animal, apparatus, scale=1.0):
        self.animal, self.apparatus, self.rows, self.scale = animal, apparatus, [], scale

    def add(self, measure, value, tol, note=""):
        """Añade un valor esperado; si la medida ya estaba (p. ej. general y luego específica), gana la última."""
        self.rows = [r for r in self.rows if r["Measure"] != measure]
        if self.scale != 1.0 and isinstance(tol, (int, float)) and tol:
            tol = round(tol * self.scale + 0.02, 3)  # capa B: misma trayectoria, solo cabe la diferencia de cálculo
        self.rows.append({"Animal": self.animal, "Apparatus": self.apparatus, "Measure": measure,
                          "Expected": value, "Tolerance": tol, "Note": note})


def generic(t: Truth, e: Expect, unit="cm", zones=None, pauses_s=(), distance=True, still_s=0.0, mobility=True,
            minimal=False):
    """minimal: solo la duración (tail suspension: el animal colgado casi inmóvil se funde con el fondo «mediana»,
    B03-26; ahí se comprueba solo la inmovilidad por el forcejeo, que no necesita la posición)."""
    if minimal:
        e.add("Test duration (s)", round(t.duration, 2), 0.1)
        return
    """Medidas generales: distancia, duración, inmovilidad por pausas, y por zona tiempo / entradas / latencia."""
    from oraculo_medidas import Kin

    dist = float(Kin(t.pos, t.ang, t.nose, t.px_per_cm).step.sum())  # posiciones suavizadas 0,2 s (la guía)
    if distance:
        # tolerancia: 5 % + 1 unidad + el temblor del centro detectado con el animal quieto (≈0,3 px por cuadro)
        tol = 0.05 * dist + 1 + t.cm(0.3 * FPS) * still_s
        e.add(f"Total distance ({unit})", round(dist, 2), round(tol, 2), "suma de los pasos del recorrido")
    e.add("Test duration (s)", round(t.duration, 2), 0.1)
    from oraculo_medidas import all_measures

    all_measures(t, e, unit=unit, zones=zones, mobility=mobility, freezing=getattr(t, "freezing", None))
    m = t.memb()
    for z in zones if zones is not None else list(m):
        inside = m[z]
        n_ent = entries(inside)
        tol_t = round(0.12 * 2 * max(n_ent, 1) + 0.2, 2)
        e.add(f"{z}: time (s)", round(inside.sum() / FPS, 2), tol_t, "cuadros con el centro dentro / fps")
        e.add(f"{z}: entries", n_ent, 0, "transiciones fuera→dentro, más 1 si empieza dentro")
        e.add(f"{z}: latency to first entry (s)", round(latency(inside, t.duration), 2), 0.25,
              "0 si empieza dentro; la duración si nunca entra")


# ------------------------------------------------------------------------------------------------ escenarios
SLOW = 2.0  # px por cuadro (≈4 cm/s en el NOR): la aproximación lenta que invertía cabeza y cola (2026-10-12)


def scenario(key, app, slow=False, mostly_still=False, instant_turn=False):
    """Devuelve (recorrido, lista de pausas en s, función que añade las medidas específicas).
    slow: en el NOR, acercarse despacio al objeto nuevo (caso de prueba de la orientación de la cabeza)."""
    P = Path_(speed=max(4.0, 8 * (app.px_per_cm or 1) / FPS),  # al menos 8 cm/s al moverse
              turn_rate=180.0 if instant_turn else 15.0)
    pauses = []
    spec = None

    def visit(region, dwell, exclude=()):
        P.move(deep_point(app, region, exclude, 6))
        if dwell:
            P.stay(dwell)
            pauses.append(dwell)

    def first(region, exclude=(), angle=0.0):
        P.start(deep_point(app, region, exclude, 6), angle)

    if key == "open_field" and mostly_still:
        first("Corner 1")
        visit("Centre", 0)
        visit("Corner 3", 24)
    elif key == "open_field":
        first("Corner 1")
        P.stay(3)
        pauses.append(3)
        for z, d in (("Centre", 4), ("Corner 2", 1), ("Corner 3", 3), ("Centre", 2), ("Corner 4", 0),
                     ("Corner 1", 2.5)):
            visit(z, d)
    elif key in ("epm", "ezm"):
        arms = ["Open arm W", "Closed arm N", "Open arm E", "Closed arm S"] if key == "epm" else \
            ["Open quadrant NE", "Closed quadrant SE", "Open quadrant SW", "Closed quadrant NW"]
        if key == "epm":
            first("Centre")
        else:
            first(arms[0])
        for i, z in enumerate(arms + arms[:2]):
            if key == "epm":
                visit("Centre", 0.5)
            visit(z, 2 + i % 3)

        def spec(t, e):
            o, c = ("Open arms", "Closed arms") if key == "epm" else ("Open quadrants", "Closed quadrants")
            m = t.memb()
            to, tc = m[o].sum() / FPS, m[c].sum() / FPS
            seq = entry_sequence(t, arms)
            eo = sum(1 for z in seq if z.startswith("Open"))
            ec = sum(1 for z in seq if z.startswith("Closed"))
            word = "arm" if key == "epm" else "quadrant"
            e.add(f"Open {word} time (%)", f"{100 * to / (to + tc):.2f}|{100 * to / t.duration:.2f}", 2.0,
                  "100·abiertos/(abiertos+cerrados) (Pellow 1985) o 100·abiertos/duración")
            e.add(f"Open {word} entries (%)", round(100 * eo / (eo + ec), 2), 0.5, "100·entradas abiertos/total")
            e.add(f"Open {word} entries", eo, 0, "entradas a cada brazo/cuadrante abierto")
            e.add(f"Closed {word} entries", ec, 0, "")
            e.add(f"Total {word} entries", eo + ec, 0, "")
    elif key == "y_maze":
        first("Centre")
        for z in ["Arm A", "Arm B", "Arm C", "Arm A", "Arm C", "Arm C", "Arm B", "Arm A", "Arm B"]:
            visit("Centre", 0.4)
            visit(z, 1.5)

        def spec(t, e):
            seq = [z[-1] for z in entry_sequence(t, ["Arm A", "Arm B", "Arm C"])]
            sa = sum(1 for i in range(len(seq) - 2) if len(set(seq[i:i + 3])) == 3)
            e.add("Total arm entries", len(seq), 0, "")
            e.add("Arm entry sequence", " ".join(seq), "texto", "letras de los brazos en orden (formato puede variar)")
            e.add("Spontaneous alternations", sa, 0, "tríos consecutivos con los tres brazos distintos")
            e.add("Alternation (%)", round(100 * sa / (len(seq) - 2), 2), 0.5, "100·SA/(entradas−2)")
            e.add("Same arm returns", sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1]), 0, "A A")
            e.add("Alternate arm returns", sum(1 for i in range(2, len(seq)) if seq[i] == seq[i - 2]
                                               and seq[i] != seq[i - 1]), 0, "A B A")
    elif key == "t_maze":
        first("Stem")
        P.stay(2)
        pauses.append(2)
        for z in ["Junction", "Left arm", "Junction", "Right arm", "Junction", "Left arm"]:
            visit(z, 1.0 if z == "Junction" else 2.0)

        def spec(t, e):
            seq = entry_sequence(t, ["Left arm", "Right arm"])
            m = t.memb()
            e.add("First choice", seq[0], "texto", "primer brazo en el que entra (puede salir como 'Left')")
            e.add("Choice latency (s)", round(latency(m[seq[0]], t.duration), 2), 0.25, "latencia de esa entrada")
            e.add("Arm alternations", sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1]), 0,
                  "cambios izquierda↔derecha entre entradas consecutivas a brazos")
    elif key in ("radial_arm_maze", "rapc"):
        first("Centre")
        order = [1, 3, 2, 3, 5, 8, 1, 4, 6, 7, 2]
        for a in order:
            visit("Centre", 0.4)
            visit(f"Arm {a}", 1.2)

        def spec(t, e):
            arms = [f"Arm {i}" for i in range(1, 9)]
            seq = [int(z.split()[1]) for z in entry_sequence(t, arms)]
            seen, errs, before, all_at = set(), 0, None, None
            for i, a in enumerate(seq):
                if a in seen:
                    errs += 1
                    if before is None:
                        before = i
                seen.add(a)
                if all_at is None and len(seen) == 8:
                    all_at = i + 1
            e.add("Total arm entries", len(seq), 0, "")
            if key == "radial_arm_maze":
                e.add("Different arms visited", len(set(seq)), 0, "")
                e.add("Working memory errors (re-entries)", errs, 0, "re-entradas a brazos ya visitados")
                e.add("Correct entries before first error", before if before is not None else len(seq), 0, "")
                e.add("Different arms in first 8 entries", len(set(seq[:8])), 0, "")
                e.add("Entries to visit all arms", all_at if all_at else "", 0, "")
                e.add("Arm entry sequence", " ".join(map(str, seq)), "texto", "")
            else:
                baited = {1, 3, 5, 7}
                t1 = t2 = 0
                seen_b, first_err, all_b = set(), None, None
                for i, a in enumerate(seq):
                    err = False
                    if a in baited:
                        if a in seen_b:
                            t1 += 1
                            err = True
                        seen_b.add(a)
                    else:
                        t2 += 1
                        err = True
                    if err and first_err is None:
                        first_err = i
                    if all_b is None and seen_b == baited:
                        all_b = i + 1
                e.add("Type 1 errors", t1, 0, "re-entrada a un brazo cebado ya visitado (guía §9 RAPC)")
                e.add("Type 2 errors", t2, 0, "entrada a un brazo no cebado (incluidas re-entradas)")
                e.add("Total errors", t1 + t2, 0, "")
                e.add("Baited arms visited", len(seen_b), 0, "")
                e.add("Correct entries before first error", first_err if first_err is not None else len(seq), 0, "")
                e.add("Entries to visit all baited arms", all_b if all_b else "", 0, "")
                from oraculo_medidas import seg_intersect

                doors, inward = [], []
                cx, cy = t.app.zone("Centre").shape.centroid()
                for i in range(1, t.n):
                    for ln in t.app.lines:
                        if seg_intersect(t.pos[i - 1], t.pos[i], (ln.x1, ln.y1), (ln.x2, ln.y2)):
                            doors.append(ln.name.split()[-1])
                            if np.hypot(t.pos[i, 0] - cx, t.pos[i, 1] - cy) > np.hypot(t.pos[i - 1, 0] - cx,
                                                                                      t.pos[i - 1, 1] - cy):
                                inward.append(ln.name.split()[-1])  # hacia fuera del centro: entra en el brazo
                e.add("Door sequence", " ".join(inward), "texto", "puertas cruzadas al entrar en los brazos, en orden "
                      f"(con las salidas también sería: {' '.join(doors)})")
    elif key == "water_maze":
        first("Quadrant SW", ("Thigmotaxis zone",))
        P.stay(1)
        for z, d in (("Quadrant NW", 1), ("Thigmotaxis zone", 2)):
            visit(z, d, ("Platform annulus",) if z != "Thigmotaxis zone" else ())
        visit("Quadrant SE", 1, ("Thigmotaxis zone", "Platform annulus"))
        P.move(deep_point(app, "Platform"))
        P.stay(3)
        pauses.append(3)

        def spec(t, e):
            m = t.memb()
            plat = m["Platform"]
            lat = latency(plat, t.duration)
            k = int(round(lat * FPS))
            steps = _smooth_steps(t)[1:k + 1] * t.px_per_cm
            e.add("Escape latency (s)", round(lat, 2), 0.25, "primera entrada a Platform")
            e.add("Found platform", "True" if plat.any() else "No", "texto", "puede salir como 1/Yes")
            e.add("Path length to platform (cm)", round(t.cm(steps.sum()), 2), round(0.05 * t.cm(steps.sum()) + 1, 2),
                  "distancia hasta la primera entrada a Platform")
            e.add("Platform crossings", entries(plat) if not plat[0] else f"{entries(plat)}|{entries(plat) - 1}", 0,
                  "entradas a Platform (empezar encima: ¿cuenta como cruce?)")
            e.add("Target quadrant time (%)", round(100 * m["Target quadrant"].sum() / FPS / t.duration, 2), 2.0,
                  "100·tiempo en el cuadrante objetivo (el que contiene la plataforma)/duración")
            e.add("Opposite quadrant time (%)", round(100 * m["Opposite quadrant"].sum() / FPS / t.duration, 2), 2.0,
                  "100·tiempo en el cuadrante opuesto (SW)/duración")
            e.add("Wall-hugging (%)", round(100 * m["Thigmotaxis zone"].sum() / FPS / t.duration, 2), 2.5,
                  "100·tiempo en la zona de tigmotaxis/duración")
            others = [z.name for z in t.app.zones if z.name.startswith("Platform position")]
            if others:
                e.add("Mean crossings of other platform positions", round(sum(entries(m[o]) for o in others) / len(others),
                                                                          3), 0.34, "media de las entradas a las otras "
                      "posiciones de la plataforma")
            pc = t.app.point("Platform centre")
            if pc is not None and plat.any():
                # corredor de Whishaw: banda de 20 cm centrada en la recta del primer punto al centro de la plataforma,
                # desde el principio hasta llegar a ella
                from oraculo_medidas import Kin

                kk = Kin(t.pos, t.ang, t.nose, t.px_per_cm)
                sx, sy = t.pos[0]
                ab = np.array([pc.x - sx, pc.y - sy])
                L2 = float(ab @ ab) or 1e-12
                tt_ = np.clip(((t.pos - [sx, sy]) @ ab) / L2, 0, 1)
                dcor = np.hypot(*(t.pos - ([sx, sy] + tt_[:, None] * ab)).T) / t.px_per_cm
                seg = slice(0, k + 1)
                inside = (dcor <= 10.0)[seg]
                stp = kk.step[seg]
                tw = inside.sum() / FPS
                e.add("Whishaw corridor time (s)", round(tw, 2), 0.3, "en la banda hasta encontrar la plataforma")
                e.add("Whishaw corridor time (%)", round(100 * tw / ((k + 1) / FPS), 2), 2.0, "")
                out_ = float(stp[1:][inside[:-1]].sum())
                e.add("Whishaw corridor distance (cm)", f"{out_:.2f}", round(0.05 * out_ + 0.5, 2),
                      "como la distancia en una zona (guía y ANY-maze): cuenta el paso que sale, no el que entra")
                e.add("Whishaw corridor path (%)", f"{100 * out_ / stp.sum():.2f}"
                      if stp.sum() > 0 else "", 2.0, "vacío si llega sin recorrer nada")
                from oraculo_medidas import initial_heading

                kk.detected = getattr(t, "detected", None)
                ih = initial_heading(kk)
                if ih is not None:
                    to0 = math.degrees(math.atan2(pc.y - kk.spos[0, 1], pc.x - kk.spos[0, 0]))
                    e.add("Initial heading error (deg)", round(abs(float((to0 - ih + 180) % 360 - 180)), 1), 8.0,
                          "rumbo inicial frente a la dirección al centro de la plataforma")
                e.add("Left Whishaw corridor", "No" if inside.all() else "Yes", "texto", "")
            if pc is not None:
                dpl = np.hypot(t.pos[:, 0] - pc.x, t.pos[:, 1] - pc.y) / t.px_per_cm
                e.add("Mean distance to platform (cm)", f"{dpl.mean():.2f}|{dpl[:k + 1].mean():.2f}",
                      round(0.03 * dpl.mean() + 0.3, 2), "media de la distancia a la plataforma (toda la prueba, o "
                      "hasta encontrarla)")
                sec = dpl[::int(FPS)]
                e.add("Cumulative distance to platform (cm·s)", f"{dpl.sum() / FPS:.1f}|{sec.sum():.1f}|"
                      f"{dpl[:k + 1].sum() / FPS:.1f}|{dpl[:k + 1][::int(FPS)].sum():.1f}",
                      round(0.04 * dpl.sum() / FPS + 2, 1), "Gallagher: Σ distancia·dt, o muestreada cada segundo; "
                      "toda la prueba o hasta la plataforma")
    elif key == "barnes_maze":
        first("Centre")
        centre = np.array(deep_point(app, "Centre"))
        for h in (5, 3, 3, 19, 1):
            b = app.zone(f"Hole {h}").shape.bounds()
            hole = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
            u = (hole - centre) / np.linalg.norm(hole - centre)
            P.move(hole - u * (NOSE + 40))
            P.move(hole - u * NOSE)  # la nariz sobre el agujero (la app cuenta las visitas por la cabeza)
            P.stay(0.8)
            if h != 1:
                P.move(hole - u * (NOSE + 40))
                P.move(centre)
        P.stay(2.6)
        pauses.append(2.6)

        def spec(t, e):
            holes = [f"Hole {i}" for i in range(1, 21)]
            seq = [int(z.split()[1]) for z in entry_sequence(t, holes, xy=t.nose)]
            m = t.memb(t.nose)
            lat = latency(m["Hole 1"], t.duration)
            k = int(round(lat * FPS))
            prim = [h for h in seq[:seq.index(1)] if h != 1] if 1 in seq else seq
            steps = _smooth_steps(t)[1:k + 1] * t.px_per_cm
            e.add("Primary latency (s)", round(lat, 2), 0.25, "primera entrada de la nariz al agujero de escape (Hole 1)")
            e.add("Primary errors", len(prim), 0, "entradas a otros agujeros antes del de escape")
            e.add("Primary path length (cm)", round(t.cm(steps.sum()), 2), round(0.05 * t.cm(steps.sum()) + 1, 2), "")
            e.add("Total errors", sum(1 for h in seq if h != 1), 0, "todas las entradas a agujeros de error")
            e.add("Escape hole visits", seq.count(1), 0, "")
            e.add("Hole visit sequence", " ".join(map(str, seq)), "texto", "")
    elif key in ("novel_object", "three_chamber"):
        if key == "novel_object":
            first("Periphery", ("Object A zone", "Object B zone"))
            P.stay(1)
            plan = [("Object A", 4.0), ("Object B", 8.0), ("Object A", 2.0)]
        else:
            first("Centre chamber")
            P.stay(1)
            plan = [("Left cup", 6.0), ("Right cup", 3.0), ("Left cup", 2.0)]
        for obj, d in plan:
            ox, oy = point_xy(app, obj)
            # la nariz a mitad del anillo de exploración / interacción, entre el borde del objeto (o de la taza) y
            # el radio del punto (objeto + distancia de exploración); el animal mirando al objeto
            ring = scen_params(key).get("exploration_radius_cm") or scen_params(key).get("interaction_cm", 3.0)
            nose_d = (app.point(obj).radius_cm - 0.5 * ring) * app.px_per_cm
            if key == "novel_object":
                u = np.array([ox, oy]) - np.array([SIZE[0] / 2, SIZE[1] / 2])
            else:  # desde abajo: el animal sigue dentro de la cámara de la taza
                u = np.array([0.0, -1.0])
            u = u / (np.linalg.norm(u) or 1)
            c = np.array([ox, oy]) - u * (nose_d + NOSE)  # centro del animal, mirando al objeto
            P.move(c - u * 60)
            P.move(c, speed=SLOW if slow and obj == plan[1][0] else None)
            P.stay(d)
            pauses.append(d)
            P.move(c - u * 80)

        def explore(t, name):
            pt = app.point(name)
            near = t.nose_near(pt.x, pt.y, pt.radius_cm * app.px_per_cm) & t.facing(pt.x, pt.y, 45.0)
            return near.sum() / FPS

        def spec(t, e, plan=plan):
            why = ("nariz a ≤ radio del punto y cuerpo mirando al objeto (±45°), contado desde la trayectoria "
                   "verdadera")
            if key == "novel_object":
                fam, nov = explore(t, "Object A"), explore(t, "Object B")
                e.add("Novel object exploration (s)", round(nov, 2), 0.5, "objeto nuevo por defecto: Object B; " + why
                      + (". APROXIMACIÓN LENTA al objeto nuevo (≈4 cm/s): prueba la orientación de la cabeza"
                         if slow else ""))
                e.add("Familiar object exploration (s)", round(fam, 2), 0.5, why)
                e.add("Total exploration (s)", round(fam + nov, 2), 1.0, "")
                e.add("Discrimination index", round((nov - fam) / (nov + fam), 3), 0.05, "(N−F)/(N+F)")
                e.add("Recognition index (%)", round(100 * nov / (nov + fam), 2), 3.0, "100·N/(N+F)")
            else:
                m = t.memb()
                zs, zo = m["Left cup interaction zone"].sum() / FPS, m["Right cup interaction zone"].sum() / FPS
                soc, obj = explore(t, "Left cup"), explore(t, "Right cup")
                why3 = ("tiempo con el centro en la zona de interacción de la taza (ANY-maze); alternativa: " + why)
                e.add("Social interaction (s)", f"{zs:.2f}|{soc:.2f}", 0.6, "lado social por defecto: Left; " + why3)
                e.add("Object/empty interaction (s)", f"{zo:.2f}|{obj:.2f}", 0.6, why3)
                e.add("Sociability index", f"{(zs - zo) / (zs + zo):.3f}|{(soc - obj) / (soc + obj):.3f}", 0.06,
                      "(S−O)/(S+O)")
                m = t.memb()
                ls, rs = m["Left chamber"].sum() / FPS, m["Right chamber"].sum() / FPS
                e.add("Social chamber preference index", round((ls - rs) / (ls + rs), 3), 0.05,
                      "(cámara social − la otra)/(suma)")
    elif key == "light_dark":
        first("Light compartment")
        P.stay(3)
        pauses.append(3)
        for z, d in (("Dark compartment", 4), ("Light compartment", 2), ("Dark compartment", 5),
                     ("Light compartment", 1)):
            visit(z, d)

        def spec(t, e):
            m = t.memb()
            seq = entry_sequence(t, ["Light compartment", "Dark compartment"])
            e.add("Latency to enter dark (s)", round(latency(m["Dark compartment"], t.duration), 2), 0.25, "")
            e.add("Transitions", len(seq) - 1, 0, "cambios de compartimento (sin contar la entrada inicial)")
            e.add("Time in light (%)", round(100 * m["Light compartment"].sum() / FPS / t.duration, 2), 2.0,
                  "100·luz/duración")
    elif key == "cpp":
        first("Chamber A")
        P.stay(5)
        pauses.append(5)
        for z, d in (("Chamber B", 3), ("Chamber A", 6), ("Chamber B", 2)):
            visit(z, d)

        def spec(t, e):
            m = t.memb()
            a, b = m["Chamber A"].sum() / FPS, m["Chamber B"].sum() / FPS
            e.add("Paired chamber time (s)", round(a, 2), 0.6, "cámara asociada por defecto: Chamber A")
            e.add("Unpaired chamber time (s)", round(b, 2), 0.6, "")
            e.add("CPP score (s)", round(a - b, 2), 1.0, "asociada − no asociada")
            e.add("Preference index", f"{a / (a + b):.3f}|{(a - b) / (a + b):.3f}", 0.03,
                  "A/(A+B) o (A−B)/(A+B): ambas definiciones se publican; documenta cuál usa la app")
            e.add("Paired chamber time (%)", f"{100 * a / (a + b):.2f}|{100 * a / t.duration:.2f}", 2.0,
                  "100·A/(A+B) o 100·A/duración")
            e.add("Chamber transitions", len(entry_sequence(t, ["Chamber A", "Chamber B"])) - 1, 0, "")
    elif key == "novel_tank":
        first("Bottom")
        P.stay(4)
        pauses.append(4)
        for z, d in (("Middle", 1), ("Top", 3), ("Bottom", 5), ("Top", 2)):
            visit(z, d)

        def spec(t, e):
            m = t.memb()
            top, bot = m["Top"].sum() / FPS, m["Bottom"].sum() / FPS
            e.add("Latency to top (s)", round(latency(m["Top"], t.duration), 2), 0.25, "")
            e.add("Top entries", entries(m["Top"]), 0, "")
            e.add("Time in top (%)", round(100 * top / t.duration, 2), 2.0, "")
            e.add("Time in bottom (%)", round(100 * bot / t.duration, 2), 2.0, "")
            e.add("Top/bottom ratio", round(top / bot, 3), 0.08, "tiempo arriba / tiempo abajo")
            y0 = t.app.arena_or_bounds().bounds()[1]
            e.add("Mean depth (cm)", round(float((t.pos[:, 1] - y0).mean()) / t.px_per_cm, 2), 0.3,
                  "media de la distancia del centro a la superficie (borde superior del tanque)")
    elif key == "hole_board":
        x, y, w, h = BBOX
        n = 4
        dx, dy = w / (n + 1), h / (n + 1)
        P.start((x + 2.5 * dx, y + 2.5 * dy))  # punto medio entre los agujeros 6, 7, 10 y 11
        dips = [1, 6, 6, 11, 16, 1]
        for hole in dips:
            i, j = divmod(hole - 1, n)
            hx, hy = x + (j + 1) * dx, y + (i + 1) * dy
            mid = (hx + dx / 2, hy + dy / 2)  # punto medio abajo a la derecha del agujero
            P.move((mid[0], P.here[1]))  # por las «calles» entre filas y columnas de agujeros
            P.move(mid)
            u = np.array([hx, hy]) - np.array(mid)
            u = u / np.linalg.norm(u)
            P.move(np.array([hx, hy]) - u * NOSE)  # la nariz sobre el agujero
            P.stay(1.0)
            P.move(mid)

        def spec(t, e, dips=dips):
            within = {}
            for hp in app.points:
                within[hp.name] = t.nose_near(hp.x, hp.y, hp.radius_cm * app.px_per_cm)
            counts = {k: entries(v) for k, v in within.items()}
            anyin = np.any(np.vstack(list(within.values())), axis=0)
            total = sum(counts.values())
            why = "nariz dentro del radio del agujero (diámetro/2 + margen), desde la trayectoria verdadera"
            e.add("Head dips", total, 0, why)
            e.add("Holes explored", sum(1 for v in counts.values() if v), 0, "")
            e.add("Repeated head dips", total - sum(1 for v in counts.values() if v), 0, "")
            e.add("Head-dip time (s)", round(anyin.sum() / FPS, 2), round(0.12 * 2 * total + 0.2, 2), why)
            e.add("Latency to first head dip (s)", round(latency(anyin, t.duration), 2), 0.25, "")
            e.add("Head dips (/min)", round(total / (t.duration / 60), 3), 0.05, "cabeceos / minutos")
            for name, c in counts.items():
                if c:
                    e.add(f"{name}: head dips", c, 0, "el nombre de la columna puede variar")
    elif key == "thermal_gradient":
        first("Sector 1")
        for s, d in ((2, 1), (3, 6), (4, 1), (3, 2), (12, 1), (11, 2)):
            visit(f"Sector {s}", d)

        def spec(t, e):
            m = t.memb()
            secs = {i: m[f"Sector {i}"].sum() / FPS for i in range(1, 13)}
            tot = sum(secs.values())
            e.add("Preferred sector", f"Sector {max(secs, key=secs.get)}", "texto", "el de más tiempo (puede salir "
                  "solo el número)")
            e.add("Mean sector (time-weighted)", round(sum(i * v for i, v in secs.items()) / tot, 3), 0.15, "")
            e.add("Sector entries", len(entry_sequence(t, [f"Sector {i}" for i in range(1, 13)])), 1,
                  "entradas a sectores (±1 según cuente la inicial)")
    elif key == "home_cage":
        first("Cage", ("Food", "Nest"))
        P.stay(2)
        pauses.append(2)
        for z, d in (("Food", 4), ("Nest", 6), ("Food", 2)):
            visit(z, d)
    elif key == "activity_wheel":
        c = app.zones[0].shape.bounds()
        cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        r = 0.35 * min(c[2] - c[0], c[3] - c[1])
        P.start((cx + r, cy), 90)
        P.circle((cx, cy), r, 3.3, clockwise=True)  # 3,3 vueltas: la 3.ª no termina justo en el límite
        P.stay(2.6)
        pauses.append(2.6)
        P.circle((cx, cy), r, 2.3, clockwise=False)

        def spec(t, e):
            e.add("Revolutions clockwise", 3, 0, "3,3 vueltas en el sentido de las agujas del reloj (en la imagen)")
            e.add("Revolutions anticlockwise", 2, 0, "2,3 vueltas al revés")
            e.add("Revolutions (/min)", round(5 / (t.duration / 60), 3), 0.05, "(3 + 2) / minutos")
    elif key in ("forced_swim", "tail_suspension", "fear_conditioning"):
        b = app.zones[0].shape.bounds()
        c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        r = 0.25 * min(b[2] - b[0], b[3] - b[1])
        if key == "tail_suspension":
            first(app.zones[0].name)  # colgado de la cola: forcejea sin desplazarse
        else:
            P.start((c[0] + r, c[1]), 90)  # nada por el cilindro / se mueve por la cámara entre episodios
        plan = [("move", 3), ("still", 5), ("move", 4), ("still", 8), ("move", 2), ("still", 3)]
        for kind, d in plan:
            if kind == "still":
                P.stay(d)
            elif key == "tail_suspension":
                P.struggle(d)
            else:
                P.swim(d, c, r)

        def spec(t, e, plan=plan):
            still = sum(d for k, d in plan if k == "still")
            if key == "fear_conditioning":
                e.add("Time freezing (s)", still, 0.5 * 3 + 0.3, "tramos sin ningún movimiento ≥ 1 s")
                e.add("Freezing episodes", sum(1 for k, _ in plan if k == "still"), 0, "")
                e.add("Latency to first freezing (s)", 3.0, 0.4, "")
            else:
                tol = 0.5 * 3 + 0.3
                e.add("Time immobile (s)", still, tol, "tramos sin forcejeo (modo forcejeo de la imagen); ±0,5 s "
                      "por borde de episodio")
                e.add("Time immobile (%)", round(100 * still / t.duration, 2), round(100 * tol / t.duration, 2), "")
                e.add("Immobile episodes", sum(1 for k, _ in plan if k == "still"), 0, "")
                e.add("Latency to first immobility (s)", 3.0, 0.4, "")
    elif key == "multi_well":
        pass  # se trata aparte (varios animales)
    elif key == "custom":
        a = app.arena_or_bounds().bounds()
        cx, cy = (a[0] + a[2]) / 2 + 30, (a[1] + a[3]) / 2 + 25  # dentro de una celda, no sobre una línea
        P.start((cx, cy))
        for dx, dy, d in ((120, 0, 2), (120, 100, 0), (-120, 100, 3), (-120, -100, 0), (0, 0, 1)):
            P.move((cx + dx, cy + dy))
            if d:
                P.stay(d)
                pauses.append(d)
    else:
        raise KeyError(key)
    return P, pauses, spec


PARAMS = {  # parámetros de plantilla que dan zonas holgadas para un animal de 36 px
    "three_chamber": {"interaction_cm": 8.0},
    "novel_object": {"exploration_radius_cm": 2.0},
    "multi_well": {"n_wells": 6},
    "custom": {"width_cm": 50.0},
}


def scen_params(key):
    return PARAMS.get(key, {})


# ------------------------------------------------------------------------------------------------ generar
FEATURES = ("funciones", "open_field_intervalos", "home_cage_escondido", "rata_grande_lenta")
MULTI = ("social", "movil", "io")  # experimentos con varias pruebas o animales (build_multi)

EXTRA = {  # experimentos de casos concretos además de uno por plantilla: nombre → (plantilla, opciones)
    "novel_object_aproximacion_lenta": ("novel_object", {"slow": True}),
    # robustez (H-P01): media vuelta en UN cuadro (imposible para un roedor; posible con un reflejo o un cruce):
    # la cabeza queda invertida durante la parada siguiente y el objeto nuevo sale con 0 s de exploración
    "novel_object_giro_instantaneo": ("novel_object", {"slow": True, "instant_turn": True}),
    # quieto el 80 % de la prueba: con el fondo «mediana» se funde con el fondo. No es un fallo de cálculo; la prueba
    # es si la app lo AVISA (detección baja, aviso de calidad) y si otro modo de fondo lo resuelve (B03-26)
    "open_field_casi_inmovil": ("open_field", {"mostly_still": True}),
    # funciones que las plantillas no activan (oraculo_funciones.py)
    "funciones": ("open_field", {}),
    "open_field_intervalos": ("open_field", {}),
    "home_cage_escondido": ("home_cage", {}),
    # escala de una rata real a 60 fps: cuerpo de ~110 px que avanza poco por cuadro (H-P01)
    "rata_grande_lenta": ("open_field", {}),
    "social": ("open_field", {}),
    "movil": ("water_maze", {}),
    "io": ("custom", {}),
}


def build_project(dest: Path, name: str) -> tuple[Project, list[dict]]:
    if name in FEATURES:
        return build_features(dest, name)
    if name in MULTI:
        return build_multi(dest, name)
    key, opts = EXTRA.get(name, (name, {}))
    path = dest / f"{name}.mmaze"
    info = templates.TEMPLATES[key]
    p = Project(name=f"Oráculo {info.title}", protocol=key)
    p.analysis = AnalysisSettings.for_new_experiment()
    p.set_protocol(key)
    p.path = path
    path.mkdir(parents=True, exist_ok=True)
    (path / "videos").mkdir(exist_ok=True)
    apps = templates.build_many(key, *BBOX, **scen_params(key))
    for a in apps:
        a.frame_size = SIZE
        p.apparatus.append(a)
    p.stages = ["Oráculo"]
    rows: list[dict] = []
    if key == "multi_well":
        # todas las larvas se mueven todo el tiempo (1, 2 o 3 vueltas en el mismo tiempo, a distinta velocidad): un
        # animal quieto más de la mitad de la prueba se funde con el fondo «mediana» y deja de detectarse (es otra
        # prueba, B03-26)
        paths = []
        dur_frames = None
        for i, a in enumerate(apps):
            b = a.arena_or_bounds().bounds()
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            r = 0.36 * min(b[2] - b[0], b[3] - b[1])  # 0,72 del radio del pocillo: entre la zona centro y el borde
            laps = 1 + i % 3
            if dur_frames is None:
                dur_frames = int(3 * 2 * math.pi * r / 3.0)  # lo que tarda en dar 3 vueltas a 3 px por cuadro
            P = Path_()
            P.start((cx + r, cy), 90)
            P.circle((cx, cy), r, laps, clockwise=bool(i % 2), speed=laps * 2 * math.pi * r / dur_frames)
            P.stay(1)
            paths.append(P)
        n = max(len(P.pos) for P in paths)
        for P in paths:
            P.stay((n - len(P.pos)) / FPS)
        pos = [P.arrays()[0][:n] for P in paths]
        angs = [P.arrays()[1][:n] for P in paths]
        vp = path / "videos" / f"{key}.avi"
        render_many(vp, pos, angs, length=20)
        for i, a in enumerate(apps):
            aid = f"W{i + 1}"
            p.ensure_animal(aid, "Oráculo")
            p.add_test(str(vp), aid, a.name, stage="Oráculo", trial=1)
            t = Truth(a, pos[i], angs[i], a.px_per_cm)
            e = Expect(aid, a.name)
            generic(t, e, zones=[z.name for z in a.zones], still_s=1.0)
            rows += e.rows
        p.test_duration_s = n / FPS
        p.save()
        return p, rows
    app = apps[0]
    P, pauses, spec = scenario(key, app, **opts)
    pos, ang = P.arrays()
    vp = path / "videos" / f"{key}.avi"
    lengths = np.full(len(pos), 22.0) if key == "novel_tank" else None  # un pez pequeño cabe en cada capa
    syn.make_video(vp, pos, size=SIZE, fps=FPS, angles=ang, seed=7, lengths=lengths)
    p.ensure_animal("O1", "Oráculo")
    p.add_test(str(vp), "O1", app.name, stage="Oráculo", trial=1)
    p.test_duration_s = len(pos) / FPS
    p.save()
    t = Truth(app, pos, ang, app.px_per_cm)
    if key == "novel_tank":
        t.nose_len = 0.77 * 22.0
    e = Expect("O1", app.name)
    unit = "cm" if app.px_per_cm else "px"
    motion_only = key in ("forced_swim", "tail_suspension", "fear_conditioning")  # el forcejeo mueve el centroide
    generic(t, e, unit=unit, pauses_s=() if motion_only else pauses, distance=not motion_only,
            still_s=sum(pauses), mobility=not motion_only, minimal=key == "tail_suspension")
    if spec:
        spec(t, e)
    return p, e.rows


def features_setup(name):
    """Aparato, escenario y ajustes de los experimentos de funciones. Devuelve (app, Path_, extras)."""
    from manymaze.core.apparatus import Sequence, Zone, make_grid
    from manymaze.core.geometry import Ellipse, Polygon

    x, y, w, h = BBOX
    extras = {"lengths": None, "visible": None, "events": [], "analysis": {}, "calcs": [], "behaviours": False}
    if name == "home_cage_escondido":
        app = templates.build("home_cage", *BBOX)
        P = Path_(speed=max(4.0, 8 * app.px_per_cm / FPS))
        nest = app.zone("Nest")
        nb = nest.shape.bounds()
        P.start(deep_point(app, "Cage", ("Food", "Nest"), 6))
        P.stay(1.5)
        inside = (nb[0] + nb[2]) / 2, (nb[1] + nb[3]) / 2
        P.move(inside)
        P.stay(0.5)
        n0 = len(P.pos)
        P.stay(3.0)  # escondido
        n1 = len(P.pos)
        # se asoma justo fuera del nido (a la derecha del borde, dentro de la distancia de la zona oculta)
        peek = (nb[2] + 12, inside[1])
        P.pos.append(peek)
        P.ang.append(0.0)
        P.stay(1.0)
        n2 = len(P.pos)
        P.pos.append(inside)
        P.ang.append(180.0)
        P.stay(2.0)  # escondido otra vez
        n3 = len(P.pos)
        P.move(peek)
        P.move(deep_point(app, "Food", (), 6))
        P.stay(2.0)
        vis = np.ones(len(P.pos), bool)
        vis[n0:n1] = False
        vis[n2:n3] = False
        extras["visible"] = vis
        return app, P, extras
    app = templates.build("open_field", *BBOX, size_cm=50)
    P = Path_(speed=max(4.0, 8 * app.px_per_cm / FPS))
    if name == "rata_grande_lenta":
        big = 110.0
        P = Path_(speed=2.5, turn_rate=6.0)  # ~2,5 px/cuadro: menos de ¼ del cuerpo en 5 cuadros
        m = 90
        corners = [(x + m, y + m), (x + w - m, y + m), (x + w - m, y + h - m), (x + m, y + h - m)]
        P.start(corners[0], 0.0)
        for lap in range(2):
            for c in corners[1:] + corners[:1]:
                P.move(c)
                P.stay(1.5)
        extras["lengths"] = np.full(len(P.pos), big)
        extras["nose_len"] = 0.77 * big
        return app, P, extras
    if name == "open_field_intervalos":
        P, _, _ = scenario("open_field", app)
        extras["analysis"] = {"bin_length_s": 5.0}
        return app, P, extras
    # «funciones»
    def rect(x0, y0, x1, y1):
        return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    obj = (x + 0.6 * w, y + 0.18 * h)
    app.zones.append(Zone("Objeto", Ellipse(obj[0], obj[1], 20, 20), investigation_distance_cm=3.0))
    head_z = (x + 0.30 * w, y + 0.80 * h, x + 0.45 * w, y + 0.95 * h)
    tail_z = (x + 0.55 * w, y + 0.80 * h, x + 0.70 * w, y + 0.95 * h)
    app.zones.append(Zone("Por cabeza", rect(*head_z), entry_rule="head"))
    app.zones.append(Zone("Por cola", rect(*tail_z), entry_rule="tail"))
    app.sequences.append(Sequence("Esquina-Centro-Esquina", ["Corner 1", "Centre", "Corner 3"]))
    make_grid(app, "square", nx=3, ny=3, name="Rejilla")
    lengths = []
    P.start(deep_point(app, "Corner 1", (), 6))
    P.stay(2.5)
    P.move(deep_point(app, "Centre", (), 6))
    n_rear1 = len(P.pos)
    P.stay(3.0)
    rear1 = (n_rear1 + int(0.5 * FPS), n_rear1 + int(2.5 * FPS))
    P.move(deep_point(app, "Corner 3", (), 6))
    t_def = len(P.pos) / FPS + 0.5
    P.stay(1.0)
    # investiga el objeto: la nariz a 1,5 cm del borde, mirándolo, el centro fuera de la zona
    u = np.array([0.0, -1.0])
    c = np.array(obj) - u * (20 + 1.5 * app.px_per_cm + NOSE)
    P.move(c - u * 50)
    P.move(c)
    P.stay(3.0)
    P.move(c - u * 50)
    # mete la cabeza en «Por cabeza» (el centro fuera)
    hx = (head_z[0] + head_z[2]) / 2
    P.move((hx, head_z[1] - 60))
    P.move((hx, head_z[1] - 10))
    P.stay(2.0)
    P.move((hx, head_z[1] - 60))
    # mete la base de la cola en «Por cola» mirando hacia arriba (el centro fuera)
    tx = (tail_z[0] + tail_z[2]) / 2
    P.move((tx, tail_z[1] - 70))
    P.turn_to(-90.0)
    P.move((tx, tail_z[1] - 8), speed=2.0)  # retrocede: se desplaza hacia abajo mirando hacia arriba
    P.ang[-1] = -90.0
    t_groom = len(P.pos) / FPS + 0.2
    n_tail = len(P.pos)
    P.stay(2.0)
    for i in range(n_tail - 40, len(P.ang)):
        P.ang[i] = -90.0  # sigue mirando hacia arriba mientras retrocede y espera
    P.move(deep_point(app, "Centre", (), 6))
    n_rear2 = len(P.pos)
    P.stay(3.0)
    rear2 = (n_rear2 + int(0.5 * FPS), n_rear2 + int(2.5 * FPS))
    lengths = np.full(len(P.pos), float(BODY))
    for a, b in (rear1, rear2):
        lengths[a:b] = BODY * 0.55
    extras["lengths"] = lengths
    extras["events"] = [{"behaviour": "Levantarse", "t": round(rear1[0] / FPS, 2), "t_end": round(rear1[1] / FPS, 2)},
                        {"behaviour": "Defecar", "t": round(t_def, 2), "t_end": None},
                        {"behaviour": "Acicalarse", "t": round(t_groom, 2), "t_end": round(t_groom + 1.5, 2)},
                        {"behaviour": "Levantarse", "t": round(rear2[0] / FPS, 2), "t_end": round(rear2[1] / FPS, 2)}]
    extras["behaviours"] = True
    extras["analysis"] = {"rearing": True, "behaviour_by_zone": True,
                          "custom_periods": [["Primeros 5 s", 0.0, 5.0]],
                          "event_periods": [{"label": "Tras entrar al centro", "anchor": "first_entry",
                                             "zone": "Centre", "duration_s": 5.0}]}
    extras["calcs"] = [("Indice centro", "100 * {Centre: time (s)} / {Test duration (s)}", 2, "%")]
    return app, P, extras


def features_expected(name, truth, app, extras, unit, layer_b=False):
    """Valores esperados de un experimento de funciones (capa A con la verdad; capa B con el track)."""
    import oraculo_funciones as of
    from oraculo_medidas import Kin

    hidden = None if extras["visible"] is None else ~extras["visible"][:truth.n]
    memb = of.membership(truth, app, hidden)
    truth._memb = memb
    if not layer_b:  # quieto de verdad: ni se desplaza, ni gira, ni cambia de tamaño (levantarse mueve píxeles)
        L = extras["lengths"][:truth.n] if extras["lengths"] is not None else np.full(truth.n, float(BODY))
        truth.freezing = np.r_[False, (np.hypot(*np.diff(truth.pos, axis=0).T) < 1e-9)
                               & (np.abs(np.diff(truth.ang)) < 1e-9) & (np.abs(np.diff(L)) < 1e-9)]
    e = Expect("O1", app.name, scale=0.25 if layer_b else 1.0)
    rows = []
    if name == "home_cage_escondido":
        generic(truth, e, unit=unit, zones=[z.name for z in app.zones], minimal=True)
        m = memb
        for z in [z.name for z in app.zones]:
            ins = m[z]
            n_ent = entries(ins)
            e.add(f"{z}: time (s)", round(ins.sum() / FPS, 2), round(0.12 * 2 * max(n_ent, 1) + 0.2, 2),
                  "incluye el tiempo escondido en la zona oculta")
            e.add(f"{z}: entries", n_ent, 0, "")
        of.hidden_measures(truth, e, app, Kin(truth.pos, truth.ang, truth.nose, truth.px_per_cm), memb, hidden)
        return e.rows
    grid_cells = {c for g in app.grids for c in g.zones}
    names = [n for n in memb if n not in grid_cells]
    generic(truth, e, unit=unit, zones=names)
    k = Kin(truth.pos, truth.ang, truth.nose, truth.px_per_cm, freezing=getattr(truth, "freezing", None))
    if name == "funciones":
        of.investigation(truth, e, app, k, unit)
        if not layer_b:
            of.rearing(truth, e, k, {n: memb[n] for n in names}, extras["lengths"][:truth.n])
        of.keys(truth, e, k, {n: memb[n] for n in names}, extras["events"], unit)
        of.sequences(truth, e, app, k, memb, unit)
        of.grids(truth, e, app, k)
        ct = memb["Centre"].sum() / FPS
        e.add("Indice centro (%)", round(100 * ct / truth.duration, 2), 1.0, "cálculo: 100·{Centre: time}/{Test duration}")
        tc = of.first(memb["Centre"])
        defs = [("Primeros 5 s", 0.0, 5.0)] + ([("Tras entrar al centro", tc / FPS, tc / FPS + 5.0)] if tc is not None else [])
        of.periods(truth, rows, "O1", app.name, defs, unit, Expect, lambda: {n: memb[n] for n in names})
    elif name == "open_field_intervalos":
        dur = truth.duration
        defs = [(f"{a:g}-{min(a + 5, dur):g} s", a, min(a + 5, dur)) for a in np.arange(0, dur, 5.0)]
        defs = [(f"{int(a)}-{int(round(b))} s", a, b) for _, a, b in defs]
        of.periods(truth, rows, "O1", app.name, defs, unit, Expect, lambda: {n: memb[n] for n in names})
    return e.rows + rows


def build_features(dest: Path, name: str):
    from manymaze.core.calculations import Calculation
    from manymaze.core.project import Behaviour

    import oraculo_funciones as of

    path = dest / f"{name}.mmaze"
    app, P, extras = features_setup(name)
    key = app.template
    p = Project(name=f"Oráculo {name}", protocol=key)
    p.analysis = AnalysisSettings.for_new_experiment()
    p.set_protocol(key)
    for k_, v in extras["analysis"].items():
        setattr(p.analysis, k_, v)
    if extras["behaviours"]:
        p.behaviours = [Behaviour(n, k_, kind) for n, k_, kind in of.BEHAVIOURS]
    p.calculations = [Calculation(n, f, d, u) for n, f, d, u in extras["calcs"]]
    p.path = path
    (path / "videos").mkdir(parents=True, exist_ok=True)
    app.frame_size = SIZE
    p.apparatus.append(app)
    p.stages = ["Oráculo"]
    pos, ang = P.arrays()
    lengths = extras["lengths"] if extras["lengths"] is not None else np.full(len(pos), float(BODY))
    visible = extras["visible"] if extras["visible"] is not None else np.ones(len(pos), bool)
    vp = path / "videos" / f"{name}.avi"
    of.render(vp, pos, ang, lengths, visible)
    p.ensure_animal("O1", "Oráculo")
    t = p.add_test(str(vp), "O1", app.name, stage="Oráculo", trial=1)
    t.events = extras["events"]
    p.test_duration_s = len(pos) / FPS
    p.save()
    truth = Truth(app, pos, ang, app.px_per_cm)
    if extras.get("nose_len"):
        truth.nose_len = extras["nose_len"]
    rows = features_expected(name, truth, app, extras, "cm" if app.px_per_cm else "px")
    return p, rows


# ------------------------------------------------------------------------------------------------ social / móvil / io
IO_EVENTS = ([{"t": a, "device": "box", "channel": "lever", "kind": "input", "value": v}
              for a0, a1 in ((2.0, 2.5), (5.0, 5.2), (9.0, 10.0), (14.5, 14.6)) for a, v in ((a0, 1), (a1, 0))]
             + [{"t": a, "device": "box", "channel": "pellet", "kind": "output", "value": v}
                for a0, a1 in ((3.0, 3.5), (11.0, 11.25)) for a, v in ((a0, 1), (a1, 0))]
             + [{"t": round(i * 0.5, 2), "device": "box", "channel": "temp", "kind": "input",
                 "value": round(20 + (0.1 * i if i < 30 else 3.0 - 0.15 * (i - 30)), 3)} for i in range(40)])
IO_DURATION = 20.0


def multi_setup(name):
    """(app, [(animal, Path_)], overrides por prueba) de los experimentos con varias pruebas o animales."""
    x, y, w, h = BBOX
    if name == "social":
        app = templates.build("open_field", *BBOX, size_cm=50)
        A, B = Path_(speed=6), Path_(speed=6)
        A.start((x + 90, y + 90), 0)
        B.start((x + w - 90, y + h - 90), 180)
        # dos encuentros en sitios distintos (un sitio ocupado mucho tiempo se funde con el fondo «mediana»): lado a
        # lado, 30 px entre centros, mirando hacia el mismo lado
        spots = [(x + 0.35 * w, y + 0.5 * h), (x + 0.65 * w, y + 0.45 * h)]
        for k in range(2):
            mx, my = spots[k]
            A.move((mx, my - 15))
            B.move((mx, my + 15))
            n = max(len(A.pos), len(B.pos))
            A.stay((n - len(A.pos)) / FPS)
            B.stay((n - len(B.pos)) / FPS)
            A.turn_to(0.0)
            B.turn_to(0.0)
            n = max(len(A.pos), len(B.pos))
            A.stay((n - len(A.pos)) / FPS + 3.0)
            B.stay((n - len(B.pos)) / FPS + 3.0)
            A.move((x + 90 + 200 * k, y + 90))
            B.move((x + w - 90 - 200 * k, y + h - 90))
            n = max(len(A.pos), len(B.pos))
            A.stay((n - len(A.pos)) / FPS + 1.0)
            B.stay((n - len(B.pos)) / FPS + 1.0)
        return app, [("S1", A), ("S2", B)], [None]
    if name == "movil":
        from manymaze.core.apparatus import CALIBRATION_KEY, POSITION_KEY

        app = templates.build("water_maze", *BBOX)
        P, _, _ = scenario("water_maze", app)
        sw = app.zone("Platform position SW").shape.to_dict()
        ov = [None, {"Platform": sw}, {POSITION_KEY: {"dx": 30.0, "dy": -20.0, "angle": 0.0, "scale": 1.0}},
              {CALIBRATION_KEY: {"px_per_cm": app.px_per_cm * 1.5}}]
        return app, [("M1", P), ("M2", P), ("M3", P), ("M4", P)], ov
    app = templates.build("custom", *BBOX, width_cm=50)
    return app, [("I1", None)], [None]


def overridden(app, ov):
    """El aparato de una prueba con sus ajustes, calculado aquí (no con Apparatus.with_overrides): la zona movida (y
    los puntos dentro de ella), el mapa desplazado entero o la calibración propia."""
    from manymaze.core.apparatus import CALIBRATION_KEY, POSITION_KEY
    from manymaze.core.geometry import shape_from_dict

    a = app.copy()
    if not ov:
        return a
    if POSITION_KEY in ov:
        dx, dy = ov[POSITION_KEY]["dx"], ov[POSITION_KEY]["dy"]
        a.arena = a.arena.translated(dx, dy) if a.arena is not None else None
        for z in a.zones:
            z.shape = z.shape.translated(dx, dy)
        for q in a.points:
            q.x, q.y = q.x + dx, q.y + dy
        for ln in a.lines:
            ln.x1, ln.y1, ln.x2, ln.y2 = ln.x1 + dx, ln.y1 + dy, ln.x2 + dx, ln.y2 + dy
    if CALIBRATION_KEY in ov:
        a.px_per_cm = float(ov[CALIBRATION_KEY]["px_per_cm"])
    moved = False
    for zn, v in ov.items():
        if zn.startswith("@"):
            continue
        z = a.zone(zn)
        old = z.shape
        z.shape = shape_from_dict(v)
        moved = True
        (ox, oy), (nx, ny) = old.centroid(), z.shape.centroid()
        for q in a.points:  # los puntos dentro de la zona movida se mueven con ella
            if old.contains(np.array([q.x]), np.array([q.y]))[0]:
                q.x, q.y = q.x + nx - ox, q.y + ny - oy
        for o in a.zones:  # y las zonas móviles centradas en ella (el anillo de la plataforma)
            c = o.shape.centroid()
            if o is not z and o.moveable and o.name not in ov and old.contains(np.array([c[0]]), np.array([c[1]]))[0]:
                o.shape = o.shape.translated(nx - ox, ny - oy)
    if moved and a.template == "water_maze":
        # el cuadrante objetivo sigue a la plataforma; las posiciones de comparación son la plataforma girada 90°,
        # 180° y 270° alrededor del centro de la piscina, con el nombre del cuadrante en que caen
        quads = ["NE", "SE", "SW", "NW"]
        plat = a.zone("Platform")
        px, py = plat.shape.centroid()
        qz = {q: a.zone(f"Quadrant {q}") for q in quads}
        target = next(q for q in quads if qz[q].shape.contains(np.array([px]), np.array([py]))[0])
        opp = quads[(quads.index(target) + 2) % 4]
        a.group("Target quadrant").zones, a.group("Target quadrant").exclude = [f"Quadrant {target}"], []
        a.group("Opposite quadrant").zones, a.group("Opposite quadrant").exclude = [f"Quadrant {opp}"], []
        cx, cy = a.arena_or_bounds().centroid()
        old_pos = [z for z in a.zones if z.name.startswith("Platform position")]
        if old_pos:
            idx = a.zones.index(old_pos[0])
            a.zones = [z for z in a.zones if not z.name.startswith("Platform position")]
            for j, k in enumerate((1, 2, 3)):
                ang = math.radians(90 * k)
                rx = cx + (px - cx) * math.cos(ang) - (py - cy) * math.sin(ang)
                ry = cy + (px - cx) * math.sin(ang) + (py - cy) * math.cos(ang)
                q = next((q for q in quads if q != target and qz[q].shape.contains(np.array([rx]), np.array([ry]))[0]),
                         quads[(quads.index(target) + k) % 4])
                from manymaze.core.apparatus import Zone

                a.zones.insert(idx + j, Zone(f"Platform position {q}", plat.shape.translated(rx - px, ry - py)))
    return a


def multi_expected(name, app, tracks_or_paths, ovs, layer_b=False):
    """tracks_or_paths: [(animal, Truth)] — la verdad (capa A) o lo rastreado (capa B)."""
    import oraculo_funciones as of

    rows = []
    if name == "io":
        e = Expect("I1", "", scale=0.25 if layer_b else 1.0)
        e.add("Test duration (s)", IO_DURATION, 0.05)
        of.io_measures(e, IO_EVENTS, IO_DURATION)
        return e.rows
    if name == "social":
        truths = dict(tracks_or_paths)
        for i, (aid, t) in enumerate(tracks_or_paths):
            e = Expect(aid, app.name, scale=0.25 if layer_b else 1.0)
            generic(t, e, unit="cm")
            other_id, other = [(a, o) for a, o in tracks_or_paths if a != aid][0]
            of.social(e, t.pos, other.pos, app.px_per_cm, BODY, f"Animal {2 if i == 0 else 1}")
            rows += e.rows
        _ = truths
        return rows
    for (aid, t), ov in zip(tracks_or_paths, ovs):
        a = overridden(app, ov)
        tt = type(t).__new__(type(t))
        tt.__dict__.update(t.__dict__)
        tt.app, tt.px_per_cm = a, a.px_per_cm
        e = Expect(aid, app.name, scale=0.25 if layer_b else 1.0)
        generic(tt, e, unit="cm")
        _, _, spec = scenario("water_maze", a)
        if spec:
            spec(tt, e)
        rows += e.rows
    return rows


def build_multi(dest: Path, name: str):
    path = dest / f"{name}.mmaze"
    app, paths, ovs = multi_setup(name)
    key = app.template
    p = Project(name=f"Oráculo {name}", protocol=key)
    p.analysis = AnalysisSettings.for_new_experiment()
    p.set_protocol(key)
    p.path = path
    (path / "videos").mkdir(parents=True, exist_ok=True)
    app.frame_size = SIZE
    p.apparatus.append(app)
    p.stages = ["Oráculo"]
    if name == "io":
        p.settings_extra["mode"] = "io_only"
        p.io_devices = [{"name": "box", "type": "simulated",
                         "channels": [{"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"},
                                      {"name": "temp", "kind": "analog"}]}]
        p.ensure_animal("I1", "Oráculo")
        t = p.add_test("", "I1", "", stage="Oráculo", trial=1)
        t.io_events, t.duration_s, t.status = IO_EVENTS, IO_DURATION, "scored"
        p.save()
        return p, multi_expected(name, app, [("I1", None)], ovs)
    vp = path / "videos" / f"{name}.avi"
    arrays = [P.arrays() for _, P in paths]
    if name == "social":
        render_many(vp, [a for a, _ in arrays], [b for _, b in arrays], length=BODY)
        for aid, _ in paths:
            p.ensure_animal(aid, "Oráculo")
        t = p.add_test(str(vp), paths[0][0], app.name, stage="Oráculo", trial=1)
        t.extra_animals = [aid for aid, _ in paths[1:]]
    else:
        pos, ang = arrays[0]
        syn.make_video(vp, pos, size=SIZE, fps=FPS, angles=ang, seed=7)
        for (aid, _), ov in zip(paths, ovs):
            p.ensure_animal(aid, "Oráculo")
            t = p.add_test(str(vp), aid, app.name, stage="Oráculo", trial=1)
            if ov:
                t.zone_overrides = dict(ov)
    n = len(arrays[0][0])
    p.test_duration_s = n / FPS
    p.save()
    truths = [(aid, Truth(app, a, b, app.px_per_cm)) for (aid, _), (a, b) in zip(paths, arrays)]
    return p, multi_expected(name, app, truths, ovs)


def render_many(path: Path, pos: list, angs: list, length=20, bg=200, noise=2.0, seed=1):
    """Varios animales pequeños (larvas en pocillos), cada uno con su orientación (synthetic.draw_mouse)."""
    rng = np.random.default_rng(seed)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, SIZE)
    base = np.full((SIZE[1], SIZE[0]), bg, np.uint8)
    for i in range(len(pos[0])):
        img = base.copy()
        for p_, a_ in zip(pos, angs):
            syn.draw_mouse(img, p_[i, 0], p_[i, 1], a_[i], length=length, width=length / 2)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        w.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    w.release()


def write_expected(path: Path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Animal", "Apparatus", "Period", "Measure", "Expected", "Tolerance", "Note"])
        w.writeheader()
        for r in rows:
            w.writerow({"Period": r.get("Period", "Whole test"), **r})


def generate(dest: Path, keys=None):
    dest.mkdir(parents=True, exist_ok=True)
    for key in keys or [*templates.TEMPLATES, *EXTRA]:
        p, rows = build_project(dest, key)
        write_expected(p.path / "esperado.csv", rows)
        print(f"{key:32s} {len(rows):4d} valores esperados  →  {p.path}")
    (dest / "LEEME.txt").write_text(
        "Oráculo de verdad conocida: un experimento por plantilla y por función, con un video sintético de recorrido\n"
        "programado (sin rastrear) y su esperado.csv. Ver qa/bloques/B17_oraculo_trayectorias.md.\n"
        "Verificar (capa A contra la verdad; capa B, el cálculo, con el experimento rastreado en la app):\n"
        "  .venv/bin/python qa/herramientas/oraculo_sintetico.py verificar EXPORTADO.csv <exp>/esperado.csv \\\n"
        "      --experimento <copia rastreada del exp>\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------------ verificar
def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _norm(s):
    return str(s).strip().lower().replace("arm ", "").replace(" ", "")


def compare(table: list[dict], expected: list[dict]):
    """Devuelve [(animal, aparato, medida, esperado, obtenido, tolerancia, estado, nota)]."""
    out = []
    by = {}
    for r in table:
        per = str(r.get("Period", "") or "Whole test").strip()
        by[(str(r.get("Animal", "")).strip(), str(r.get("Apparatus", "")).strip(), per)] = r
    whole = {k: v for k, v in by.items() if k[2] == "Whole test"}
    for x in expected:
        per = x.get("Period") or "Whole test"
        k = (x["Animal"], x["Apparatus"])
        row = by.get((*k, per))
        if row is None and per == "Whole test" and len(whole) == 1:
            row = next(iter(whole.values()))
        meas, exp, tol = x["Measure"], x["Expected"], x["Tolerance"]
        if row is None:
            out.append((*k, meas, exp, "", tol, "SIN FILA", x["Note"]))
            continue
        if meas not in row:
            status = "NO APLICA" if str(x["Note"]).startswith("(opcional)") else "SIN COLUMNA"
            out.append((*k, meas, exp, "", tol, status, x["Note"]))
            continue
        got = row[meas]
        if tol == "lista":  # listas de duraciones: mismos números (±0,05)
            ga = [_num(v) for v in str(got).split(",") if str(v).strip()]
            ea = [_num(v) for v in str(exp).split(",") if str(v).strip()]
            ok = len(ga) == len(ea) and all(a is not None and b is not None and abs(a - b) <= 0.05 + 1e-9
                                            for a, b in zip(ga, ea))
        elif tol == "texto":
            ok = _norm(got) == _norm(exp) or _norm(exp) in _norm(got) or _norm(got) in _norm(exp)
            if str(exp).lower() == "true":
                ok = str(got).strip().lower() in ("true", "1", "yes", "sí", "1.0")
        elif exp in ("", None):
            ok = str(got).strip() in ("", "nan", "None")
        else:  # "principal|alternativa|…": la principal da OK, una alternativa da ALTERNATIVA (hay que documentarla)
            g = _num(got)
            options = [_num(v) for v in str(exp).split("|")]
            hit = [i for i, e_ in enumerate(options) if g is not None and e_ is not None
                   and abs(g - e_) <= float(tol) + 1e-9]
            ok = "OK" if hit and hit[0] == 0 else ("ALTERNATIVA" if hit else False)
        status = ok if isinstance(ok, str) else ("OK" if ok else "DIFIERE")
        out.append((*k, meas, exp, got, tol, status, x["Note"]))
    return out


def read_csv(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text[:4000], delimiters=",\t;")
    return list(csv.DictReader(text.splitlines(), dialect=dialect))


verbose_known = [False]  # --todo: listar también las diferencias conocidas


def report(res, title=""):
    """Imprime lo que no es OK. Devuelve False si algo DIFIERE o falta (ALTERNATIVA y CONOCIDO no cuentan)."""
    shown = [r for r in res if r[6] not in ("OK", "NO APLICA")]
    bad = [r for r in shown if r[6] not in ("ALTERNATIVA", "CONOCIDO")]
    if title:
        alt = sum(1 for r in shown if r[6] == "ALTERNATIVA")
        known = sum(1 for r in shown if r[6] == "CONOCIDO")
        print(f"\n== {title}: {len(res) - len(shown)}/{len(res)} OK" + (f", {alt} con definición alternativa"
                                                                       if alt else "")
              + (f", {known} diferencias de rastreo conocidas (ver la etiqueta [H-P0x] de cada una)" if known else ""))
    for a, ap, m, e, g, tol, st, note in shown:
        if st == "CONOCIDO" and not verbose_known[0]:
            continue
        print(f"  {st:11s} {a}/{ap} «{m}»: esperado {e} (±{tol}), obtenido {g}   [{note}]")
    return not bad


CAL_FILE = "calibracion_capa_A.csv"


def known_finding(experiment: str, measure: str) -> str:
    """A qué hallazgo previo corresponde una diferencia de la capa A que ya salía al calibrar."""
    m = measure.lower()
    if experiment == "open_field_casi_inmovil" or experiment == "tail_suspension":
        return "B03-26"
    if experiment == "novel_object_giro_instantaneo":
        return "H-P01"
    if "rear" in m:
        return "H-P09"
    if "initial heading error" in m:  # (el error de rumbo medio es temblor: H-P05)
        return "H-P07"
    if "path rotations" in m:
        return "H-P04"
    if experiment == "social" and not m.startswith("animal "):
        return "identidades (B17-15)"
    return "H-P05"


def apply_calibration(res, cal_path: Path):
    """Capa A: una diferencia que ya salía al calibrar (mismo valor ± tolerancia) es un efecto de rastreo conocido
    (H-P05), no un hallazgo nuevo."""
    if not cal_path.exists():
        return res
    cal = {(r["Animal"], r["Apparatus"], r["Measure"]): r["Obtenido"]
           for r in csv.DictReader(cal_path.open(encoding="utf-8"))}
    out = []
    for a, ap, m, e, g, tol, st, note in res:
        if st == "DIFIERE" and (a, ap, m) in cal:
            c, gv = _num(cal[(a, ap, m)]), _num(g)
            try:
                t = float(tol)
            except (TypeError, ValueError):
                t = 0.0
            blank = {"", "nan", "none"}
            same = (c is not None and gv is not None and abs(c - gv) <= max(t, 1e-9) + 1e-9) or \
                str(cal[(a, ap, m)]).strip().lower() == str(g).strip().lower() or \
                (str(cal[(a, ap, m)]).strip().lower() in blank and str(g).strip().lower() in blank)
            if same:
                st = "CONOCIDO"
                note = f"[{known_finding(cal_path.parent.stem, m)}] {note}"
        out.append((a, ap, m, e, g, tol, st, note))
    return out


def verify(exported: Path, expected: Path, experiment: Path | None = None) -> bool:
    """Capa A contra esperado.csv (con la calibración si existe); capa B si se da el experimento rastreado."""
    exp = list(csv.DictReader(expected.open(encoding="utf-8")))
    table = read_csv(exported)
    res = apply_calibration(compare(table, exp), expected.parent / CAL_FILE)
    ok = report(res, f"{expected.parent.name} [A: verdad programada]")
    if experiment is not None:
        p = Project.load(experiment)
        if all(t.status == "tracked" or not t.video for t in p.tests):
            ok &= report(compare(table, expected_from_tracks(p, expected.parent.stem)),
                         f"{experiment.name} [B: cálculo sobre la trayectoria rastreada]")
        else:
            print("  (capa B: hay pruebas sin rastrear en el experimento)")
    return ok


def expected_from_tracks(p: Project, name: str) -> list[dict]:
    """Capa B: los valores esperados calculados con las fórmulas del oráculo sobre las trayectorias que rastreó la
    app (guardadas en el experimento). Tolerancias 4 veces más estrechas que las de la capa A."""
    key, opts = EXTRA.get(name, (name, {}))
    rows = []
    if name in MULTI:
        app, paths, ovs = multi_setup(name)
        if name == "io":
            return multi_expected(name, app, [("I1", None)], ovs, layer_b=True)
        trs = []
        for t in p.tests:
            for i, tr in enumerate(p.load_tracks(t)):
                aid = ([t.animal_id] + list(t.extra_animals))[i]
                trs.append((aid, TrackTruth(app, tr, app.px_per_cm)))
        return multi_expected(name, app, trs, ovs, layer_b=True)
    if name in FEATURES:
        app = p.apparatus[0]
        _, _, extras = features_setup(name)
        t = p.tests[0]
        tt = TrackTruth(app, p.load_tracks(t)[0], app.px_per_cm)
        if extras["visible"] is not None:
            extras["visible"] = np.asarray(p.load_tracks(t)[0].detected, bool)
        return features_expected(name, tt, app, extras, "cm" if app.px_per_cm else "px", layer_b=True)
    for t in p.tests:
        tr = p.load_tracks(t)[0]
        app = p.apparatus_of(t)
        tt = TrackTruth(app, tr, app.px_per_cm)
        e = Expect(t.animal_id, app.name, scale=0.25)
        unit = "cm" if app.px_per_cm else "px"
        motion_only = key in ("forced_swim", "tail_suspension", "fear_conditioning")
        if key == "multi_well":
            generic(tt, e, zones=[z.name for z in app.zones], still_s=1.0)
        else:
            generic(tt, e, unit=unit, distance=not motion_only, mobility=not motion_only,
                    minimal=key == "tail_suspension")
            _, _, spec = scenario(key, app, **opts)
            if spec and key not in ("forced_swim", "tail_suspension", "fear_conditioning", "hole_board",
                                    "novel_object", "three_chamber", "activity_wheel"):
                spec(tt, e)  # las específicas que dependen solo de la trayectoria (no de lo programado)
        rows += e.rows
    return rows


def autoverify(dest: Path, keys=None, layer="AB", calibrate_into: Path | None = None) -> bool:
    """A: contra la verdad programada (fin a fin: rastreo + cálculo). B: contra las fórmulas aplicadas a la
    trayectoria rastreada (solo cálculo). calibrate_into: carpeta del oráculo (sin rastrear) donde guardar, por
    experimento, las diferencias de la capa A de esta calibración (efectos de rastreo conocidos)."""
    ok = True
    for proj_dir in sorted(dest.glob("*.mmaze")):
        if keys and proj_dir.stem not in keys:
            continue
        p = Project.load(proj_dir)
        for t in p.tests:
            if t.status != "tracked" and t.video:  # una prueba solo de I/O no tiene video que rastrear
                p.track_test(t)
        p.save()
        rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in p.results(segmented=True)]
        if "A" in layer:
            exp = list(csv.DictReader((proj_dir / "esperado.csv").open(encoding="utf-8")))
            resA = compare(rows, exp)
            if calibrate_into is not None:
                target = calibrate_into / proj_dir.name / CAL_FILE
                with target.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(["Animal", "Apparatus", "Measure", "Esperado", "Obtenido"])
                    for a, ap, m, e, g, tol, st, note in resA:
                        if st == "DIFIERE":
                            w.writerow([a, ap, m, e, g])
            else:
                resA = apply_calibration(resA, proj_dir / CAL_FILE)
            ok &= report(resA, f"{proj_dir.stem} [A: verdad programada]")
        if "B" in layer:
            ok &= report(compare(rows, expected_from_tracks(p, proj_dir.stem)),
                         f"{proj_dir.stem} [B: cálculo sobre la trayectoria rastreada]")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("dest", type=Path)
    g.add_argument("--plantillas", nargs="*")
    v = sub.add_parser("verificar")
    v.add_argument("exportado", type=Path)
    v.add_argument("esperado", type=Path)
    v.add_argument("--experimento", type=Path, help="el experimento rastreado en la app: añade la capa B")
    v.add_argument("--todo", action="store_true", help="listar también las diferencias de rastreo conocidas")
    c = sub.add_parser("calibrar", help="rastrea una COPIA del oráculo y guarda en el oráculo los efectos de rastreo")
    c.add_argument("dest", type=Path)
    a = sub.add_parser("autoverificar")
    a.add_argument("dest", type=Path)
    a.add_argument("--plantillas", nargs="*")
    a.add_argument("--capa", default="AB", choices=["A", "B", "AB"])
    args = ap.parse_args()
    if args.cmd == "generar":
        generate(args.dest, args.plantillas)
    elif args.cmd == "verificar":
        verbose_known[0] = args.todo
        sys.exit(0 if verify(args.exportado, args.esperado, args.experimento) else 1)
    elif args.cmd == "calibrar":
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "calibracion"
        shutil.copytree(args.dest, tmp)
        autoverify(tmp, layer="A", calibrate_into=args.dest)
        print(f"calibración guardada en cada experimento de {args.dest} ({CAL_FILE})")
    else:
        sys.exit(0 if autoverify(args.dest, args.plantillas, args.capa) else 1)


if __name__ == "__main__":
    main()
