"""Valores esperados de (casi) todas las medidas de mANY-MAZE a partir de una trayectoria.

Lo usa oraculo_sintetico.py en dos capas: A, sobre la trayectoria programada (verdad: rastreo + cálculo), y B,
sobre la trayectoria que rastreó la app (solo cálculo). Nada de aquí llama al código de medidas de la app: solo se
usa la forma de las zonas (contains / polygon) para la geometría.

Dos variantes por medida (`doc|impl`):
  doc  – la definición de la guía del usuario (sección 9, *Measures*), leída literalmente.
  impl – la elección concreta de la implementación donde la guía no la fija o dice otra cosa, sacada de leer el
         código (2026-10-12): la movilidad con la velocidad promediada además 0,5 s; el giro de la cabeza con la
         dirección acumulada suavizada 0,2 s; «mirando a la zona» desde la cabeza; las rotaciones que vuelven a
         contar desde la posición tras dar la vuelta (no desde el extremo); moverse hacia/en contra con el estado
         móvil (no cuadro a cuadro); cerca de un punto por la cabeza; explorar también con la cabeza a ≤ medio radio.
Si las dos coinciden, el valor esperado es uno solo. Coincidir con `impl` y no con `doc` da ALTERNATIVA: la app es
coherente pero la guía no lo cuenta (hallazgo de documentación). No coincidir con ninguna es DIFIERE.

Ajustes que se suponen (los de un experimento nuevo): suavizado 0,2 s; inmóvil < 2 cm/s ≥ 2 s; freezing < 2 % del
cuerpo (sale > 3 %) ≥ 1 s; activo = móvil; zona por el centro; empezar en una zona es una entrada; latencia de lo que
no ocurre = duración; media de nada = vacío; orientado a ≤ 30°; ángulo de exploración 45°; rumbo inicial tras 1 s
móvil; tigmotaxis = 25 % del semiancho de la arena; rejilla de cruces 4×4.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

FPS = 25.0
SIZE = (640, 480)
MOBILITY = 2.0
MIN_IMMOBILE = 2.0
MIN_FREEZE = 1.0
SMOOTH_S = 0.2
ORIENT = 30.0
FACING = 45.0
VARIANTS = ("doc", "impl")


# ------------------------------------------------------------------------------------------------ utilidades
def runs(mask):
    """[(inicio, fin)) de cada tramo True."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def wrap(a):
    return (np.asarray(a, float) + 180.0) % 360.0 - 180.0


def ma(v, window):
    """Media móvil centrada con ventana impar; en los extremos, la media de lo disponible."""
    v = np.asarray(v, float)
    window = min(int(window) | 1, len(v) - 1 + len(v) % 2)
    if window < 2:
        return v.copy()
    if v.ndim > 1:
        return np.column_stack([ma(v[:, j], window) for j in range(v.shape[1])])
    k = np.ones(window)
    return np.convolve(v, k, mode="same") / np.convolve(np.ones(len(v)), k, mode="same")


def entries_of(inside):
    inside = np.asarray(inside, bool)
    return int(inside[0]) + int(np.sum(~inside[:-1] & inside[1:]))


def exits_of(inside):
    inside = np.asarray(inside, bool)
    return int(np.sum(inside[:-1] & ~inside[1:]))


def first(mask):
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else None


def drop_short(mask, min_s, value=True):
    """Los tramos de `value` más cortos que min_s pasan al valor contrario."""
    m = np.asarray(mask, bool).copy()
    target = m if value else ~m
    for a, b in runs(target):
        if (b - a) / FPS < min_s - 1e-9:
            m[a:b] = not value
    return m


def polygon_distance(poly, xy):
    """Distancia de cada punto al contorno del polígono (exacta)."""
    P = np.asarray(poly, float)
    Q = np.roll(P, -1, axis=0)
    xy = np.asarray(xy, float)
    out = np.full(len(xy), np.inf)
    for a, b in zip(P, Q):
        ab = b - a
        L2 = float(ab @ ab) or 1e-12
        t = np.clip(((xy - a) @ ab) / L2, 0, 1)
        out = np.minimum(out, np.hypot(*(xy - (a + t[:, None] * ab)).T))
    return out


def mask_image(app, name):
    xs, ys = np.meshgrid(np.arange(SIZE[0], dtype=float), np.arange(SIZE[1], dtype=float))
    m = app.zone_membership(xs.ravel(), ys.ravel())[name]
    return m.reshape(SIZE[1], SIZE[0]).astype(np.uint8)


def sample(img, xy):
    x = np.clip(np.round(xy[:, 0]).astype(int), 0, SIZE[0] - 1)
    y = np.clip(np.round(xy[:, 1]).astype(int), 0, SIZE[1] - 1)
    return img[y, x]


def seg_intersect(p, q, a, b):
    """¿Cruza el paso p→q el segmento a–b? +1/−1 según el lado al que llega, 0 si no."""
    def cross(o, u, v):
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
    d1, d2 = cross(a, b, p), cross(a, b, q)
    d3, d4 = cross(p, q, a), cross(p, q, b)
    if (d1 >= 0) != (d2 >= 0) and (d3 >= 0) != (d4 >= 0):
        return 1 if d2 >= 0 else -1
    return 0


# ------------------------------------------------------------------------------------------------ cinemática
class Kin:
    """Cinemática de una trayectoria según una variante (doc / impl)."""

    def __init__(self, pos, ang, nose, px_per_cm, variant="doc", freezing=None, detected=None):
        self.variant = variant
        self.detected = None if detected is None else np.asarray(detected, bool)
        self.pos, self.ang, self.nose = np.asarray(pos, float), np.asarray(ang, float), np.asarray(nose, float)
        self.n = len(self.pos)
        self.dur = self.n / FPS
        self.ppc = px_per_cm or 1.0
        w = max(1, int(round(SMOOTH_S * FPS)))
        self.spos = ma(self.pos, w)
        self.step = np.r_[0.0, np.hypot(*np.diff(self.spos, axis=0).T)] / self.ppc
        self.speed = self.step * FPS
        d = np.diff(self.spos, axis=0, prepend=self.spos[:1])
        self.heading = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
        self.heading[self.step < 1e-6] = np.nan
        self.moving = np.isfinite(self.heading) & (self.speed > MOBILITY)
        sp = self.speed if variant == "doc" else ma(self.speed, int(round(0.5 * FPS)))
        self.mobile = drop_short(sp >= MOBILITY, MIN_IMMOBILE, value=False)
        self.immobile = ~self.mobile
        if freezing is None:
            still = np.r_[False, (np.hypot(*np.diff(self.pos, axis=0).T) < 1e-9) & (np.abs(np.diff(self.ang)) < 1e-9)]
        else:
            still = np.asarray(freezing, bool)
        self.freezing = drop_short(still, MIN_FREEZE, value=True)
        self.w = w
        self.snose = ma(self.nose, w)
        self.hstep = np.r_[0.0, np.hypot(*np.diff(self.snose, axis=0).T)] / self.ppc
        dh = np.r_[0.0, wrap(np.diff(self.ang))]
        dh[np.abs(dh) > 90] = 0.0  # un salto > 90° es un cambio cabeza/cola del rastreador, no un giro
        self.head_turn = dh if variant == "doc" else np.r_[0.0, np.diff(ma(np.cumsum(dh), w))]
        # giro de la dirección de desplazamiento desde el último cuadro en que se movía (a través de las paradas)
        mi = np.flatnonzero(self.moving)
        self.turn = np.full(self.n, np.nan)
        self.turn_prev = np.full(self.n, -1)
        if len(mi) > 1:
            self.turn[mi[1:]] = np.abs(wrap(np.diff(self.heading[mi])))
            self.turn_prev[mi[1:]] = mi[:-1]

    def mins(self, mask):
        return round(float(np.asarray(mask, bool).sum()) / FPS, 2)


# ------------------------------------------------------------------------------------------------ medidas
def rotations(angle_deg, from_reversal_point, max_step=None):
    """Vueltas completas y giros parciales (≥ 90° sin completar una vuelta) de una dirección. Un giro termina al
    volver atrás > 90°. from_reversal_point: la vuelta siguiente cuenta desde donde se detectó la vuelta atrás
    (impl) en lugar de desde el extremo al que llegó (doc). max_step: un cambio de más de esos grados entre dos
    muestras no es un giro (la dirección de desplazamiento al dar marcha atrás: ~180°, de sentido ambiguo; H-P04)."""
    a = np.asarray(angle_deg, float)
    a = a[np.isfinite(a)]
    out = {"cw": 0, "ccw": 0, "pcw": 0, "pccw": 0}
    if len(a) < 2:
        return out
    if max_step is None:
        u = np.degrees(np.unwrap(np.radians(a)))
    else:
        d = wrap(np.diff(a))
        d[np.abs(d) > max_step] = 0.0
        u = a[0] + np.r_[0.0, np.cumsum(d)]
    ref, hi, lo = u[0], u[0], u[0]
    st = {"d": 0, "start": u[0], "ext": u[0], "full": False}
    thi = tlo = u[0]
    thi_i = tlo_i = 0

    def end():
        if not st["full"] and abs(st["ext"] - st["start"]) >= 90 - 1e-9:
            out["pcw" if st["d"] > 0 else "pccw"] += 1

    for i, v in enumerate(u[1:], 1):
        d = st["d"]
        if d == 0:
            if v > thi:
                thi, thi_i = v, i
            if v < tlo:
                tlo, tlo_i = v, i
            if thi - tlo > 90:
                st.update(d=1, start=tlo, ext=thi) if thi_i > tlo_i else st.update(d=-1, start=thi, ext=tlo)
        elif d * (v - st["ext"]) > 0:
            st["ext"] = v
        elif d * (st["ext"] - v) > 90:
            end()
            st.update(d=-d, start=st["ext"], ext=v, full=False)
        hi, lo = max(hi, v), min(lo, v)
        if v - ref >= 360:
            out["cw"] += 1
            ref, hi, lo = v, v, v
            st["full"] = True
        elif ref - v >= 360:
            out["ccw"] += 1
            ref, hi, lo = v, v, v
            st["full"] = True
        elif hi - v > 90 and hi > ref:
            ref = v if from_reversal_point else hi
            hi = lo = v
        elif v - lo > 90 and lo < ref:
            ref = v if from_reversal_point else lo
            hi = lo = v
    if st["d"]:
        end()
    elif thi - tlo >= 90 - 1e-9:
        out["pcw" if thi_i > tlo_i else "pccw"] += 1
    return out


def whole_test(k: Kin, e, app, unit, mobility=True):
    dist = float(k.step.sum())
    e.add(f"Mean speed ({unit}/s)", round(dist / k.dur, 3), round(0.05 * dist / k.dur + 0.3, 3), "distancia / duración")
    vmax = float(k.speed.max() if k.variant == "doc" else ma(k.speed, k.w).max())
    e.add(f"Max speed ({unit}/s)", round(vmax, 2), round(0.12 * vmax + 0.5, 2),
          "máximo de la velocidad con posiciones suavizadas 0,2 s (impl: la velocidad, promediada otra vez 0,2 s)")
    if mobility:
        mob_t, imm_t = k.mobile.sum() / FPS, k.immobile.sum() / FPS
        rm, ri = runs(k.mobile), runs(k.immobile)
        tol = round(0.12 * (len(rm) + len(ri)) + 0.3, 2)
        e.add("Time mobile (s)", round(mob_t, 2), tol, "")
        e.add("Time immobile (s)", round(imm_t, 2), tol, "inmóvil < 2 cm/s durante ≥ 2 s")
        if mob_t > 0:
            e.add(f"Mean speed while mobile ({unit}/s)", round(float(k.step[k.mobile].sum()) / mob_t, 3),
                  round(0.06 * dist / mob_t + 0.3, 3), "distancia estando móvil / tiempo móvil")
        for r, word in ((rm, "mobile"), (ri, "immobile")):
            d = [(b - a) / FPS for a, b in r]
            e.add(f"{word.capitalize()} episodes", len(r), 0, "")
            if d:
                e.add(f"Mean {word} episode (s)", round(sum(d) / len(d), 2), tol, "")
                e.add(f"Longest {word} episode (s)", round(max(d), 2), 0.3, "")
                e.add(f"Shortest {word} episode (s)", round(min(d), 2), 0.3, "")
                e.add(f"Latency to last {word} episode (s)", round(r[-1][0] / FPS, 2), 0.3, "inicio del último")
        e.add("Latency to first mobile episode (s)", round(rm[0][0] / FPS, 2) if rm else round(k.dur, 2), 0.3, "")
        e.add("Latency to first immobility (s)", round(ri[0][0] / FPS, 2) if ri else round(k.dur, 2), 0.3, "")
        e.add("Time active (s)", round(mob_t, 2), tol, "activo = móvil (ANY-maze, sin teclas que cuenten)")
        e.add("Time inactive (s)", round(imm_t, 2), tol, "")
        e.add("Active episodes", len(rm), 0, "")
        e.add("Inactive episodes", len(ri), 0, "")
        for r, word in ((rm, "active"), (ri, "inactive")):
            if r:
                d = [(b - a) / FPS for a, b in r]
                e.add(f"Longest {word} episode (s)", round(max(d), 2), 0.3, "")
                e.add(f"Shortest {word} episode (s)", round(min(d), 2), 0.3, "")
    fr = runs(k.freezing)
    fd = [(b - a) / FPS for a, b in fr]
    ftol = round(0.1 * len(fr) + 0.2, 2)
    e.add("Time freezing (s)", round(sum(fd), 2), ftol, "movimiento < 2 % del cuerpo (o quieto del todo) ≥ 1 s")
    e.add("Freezing (%)", round(100 * sum(fd) / k.dur, 2), round(100 * ftol / k.dur, 2), "")
    e.add("Freezing episodes", len(fr), 0, "")
    e.add("Latency to first freezing (s)", round(fr[0][0] / FPS, 2) if fr else round(k.dur, 2), 0.2, "")
    if fd:
        e.add("Mean freezing episode (s)", round(sum(fd) / len(fd), 2), 0.2, "")
        e.add("Longest freezing episode (s)", round(max(fd), 2), 0.2, "")
        e.add("Shortest freezing episode (s)", round(min(fd), 2), 0.2, "")
    tt = k.turn[np.isfinite(k.turn)]
    abs_turn = float(tt.sum())
    ttol = round(0.06 * abs_turn + 15, 1)
    e.add("Absolute turn angle (deg)", round(abs_turn, 1), ttol,
          "Σ|cambio de dirección| desde el último cuadro en que se movía (más rápido que el umbral)")
    e.add("Angular velocity (deg/s)", round(abs_turn / k.dur, 2), round(ttol / k.dur, 2), "giro absoluto / duración")
    if len(tt):
        e.add("Mean turn angle (deg)", round(float(tt.mean()), 2), round(0.08 * tt.mean() + 0.5, 2), "media de los giros")
    if dist > 0:
        e.add(f"Meander (deg/{unit})", round(abs_turn / dist, 3), round(ttol / dist + 0.05, 3), "giro absoluto / distancia")
    cw, ccw = float(k.head_turn[k.head_turn > 0].sum()), float(-k.head_turn[k.head_turn < 0].sum())
    htol = round(0.06 * (cw + ccw) + 20, 1)
    e.add("Head turn angle clockwise (deg)", round(cw, 1), htol, "orientación centro→cabeza, sentido horario en la imagen")
    e.add("Head turn angle anticlockwise (deg)", round(ccw, 1), htol, "")
    e.add("Head turn angle (deg)", round(cw + ccw, 1), htol, "absoluto")
    e.add(f"Head distance ({unit})", round(float(k.hstep.sum()), 2), round(0.06 * k.hstep.sum() + 1, 2),
          "recorrido de la cabeza, suavizado como el del centro")
    rot = rotations(k.ang, from_reversal_point=False)  # desde el extremo, como el ejemplo de ANY-maze
    e.add("Rotations clockwise", rot["cw"], 0, "vueltas de 360° de la orientación (un giro termina al volver > 90°)")
    e.add("Rotations anticlockwise", rot["ccw"], 0, "")
    e.add("Total rotations", rot["cw"] + rot["ccw"], 0, "")
    e.add("Partial rotations clockwise", rot["pcw"], 0, "giros ≥ 90° sin completar una vuelta")
    e.add("Partial rotations anticlockwise", rot["pccw"], 0, "")
    e.add("Partial rotations", rot["pcw"] + rot["pccw"], 0, "")
    prot = rotations(k.heading, from_reversal_point=False, max_step=90.0)
    hf = k.heading[np.isfinite(k.heading)]
    amb = int(np.sum(np.abs(np.abs(wrap(np.diff(hf))) - 90.0) <= 1.0)) if len(hf) > 1 else 0
    e.add("Path rotations clockwise", prot["cw"], amb, "las vueltas de la dirección de desplazamiento; un cambio de "
          "> 90° en un cuadro (marcha atrás) no es un giro (H-P04); tolerancia = cambios de 89–91°")
    e.add("Path rotations anticlockwise", prot["ccw"], amb, "")
    straight = float(np.hypot(*(k.spos[-1] - k.spos[0]))) / k.ppc
    if dist > 0:
        e.add("Path efficiency", round(straight / dist, 4), round(0.05 * straight / dist + 0.005, 4),
              "recta (posiciones suavizadas) inicio→fin / recorrido")
    if straight > 0.5:
        e.add("Path tortuosity", round(dist / straight, 3), round(0.06 * dist / straight + 0.05, 3), "recorrido / recta")
    ar = app.arena_or_bounds()
    bx0, by0, bx1, by1 = ar.bounds()
    cx, cy = ar.centroid()
    dc = np.hypot(k.pos[:, 0] - cx, k.pos[:, 1] - cy) / k.ppc
    e.add(f"Mean distance from centre ({unit})", round(float(dc.mean()), 2), round(0.03 * dc.mean() + 0.3, 2), "")
    e.add(f"Max distance from centre ({unit})", round(float(dc.max()), 2), round(0.03 * dc.max() + 0.3, 2), "")
    inside = np.asarray(ar.contains(k.pos[:, 0], k.pos[:, 1]), bool)
    dwall = polygon_distance(ar.polygon(72), k.pos) / k.ppc
    if inside.any():
        din = dwall[inside]
        e.add(f"Mean distance from wall ({unit})", round(float(din.mean()), 2), round(0.04 * din.mean() + 0.3, 2), "")
    band = 0.25 * min(bx1 - bx0, by1 - by0) / 2 / k.ppc
    e.add("Thigmotaxis (%)", round(100 * float(((dwall <= band) & inside).mean()), 2), 3.0,
          "a ≤ 25 % del semiancho de la arena de la pared")
    e.add("Average X position (%)", round(100 * float((k.pos[:, 0].mean() - bx0) / (bx1 - bx0)), 2), 1.0,
          "media ponderada por tiempo, % del ancho desde la izquierda")
    e.add("Average Y position (%)", round(100 * float((k.pos[:, 1].mean() - by0) / (by1 - by0)), 2), 1.0, "")
    e.add("Time outside arena (s)", round(float((~inside).sum() / FPS), 2), 0.3, "")
    e.add("Video time at test start (s)", 0.0, 0.01, "")
    npos = k.n if k.detected is None else int(np.sum(k.detected))
    if k.detected is None:
        e.add("Time not detected (s)", 0.0, 0.1, "siempre a la vista")
        e.add("Tracking quality (%)", 100.0, 5.0, "detectado, cabeza encontrada, área normal y sin saltos")
        e.add("Frames tracked (%)", 100.0, 0.5, "")
        e.add("Head tracked (% of tracked frames)", 100.0, 3.0, "")
        e.add("Implausible jumps", 0, 0, "ningún salto imposible en un recorrido programado")
        e.add("Jumps removed", 0, 0, "")
    e.add("Centre positions recorded", npos, 0, "posiciones detectadas")
    e.add("Head positions recorded", npos, 0, "")
    zs = sorted(app.zones, key=lambda z: z.shape.area())
    if zs:
        zm = getattr(k, "zone_memb", None)
        memb = [np.asarray(zm[z.name] if zm is not None and z.name in zm
                           else z.shape.contains(k.pos[:, 0], k.pos[:, 1]), bool) for z in zs]
        smallest = np.full(k.n, -1)
        for i in range(len(zs) - 1, -1, -1):
            smallest[memb[i]] = i
        if k.variant == "doc":
            tr_n = int(np.sum(smallest[1:] != smallest[:-1]))
        else:  # impl: solo entre cuadros dentro de alguna zona (los tramos fuera de todas no cuentan)
            inz = smallest[smallest >= 0]
            tr_n = int(np.sum(inz[1:] != inz[:-1]))
        e.add("Zone transitions", tr_n, 1, "cambios de la zona más pequeña en la que está el centro (la guía no lo "
              "define; impl: sin contar los tramos fuera de toda zona)")
    gx = np.clip(((k.pos[:, 0] - bx0) / (bx1 - bx0) * 4).astype(int), 0, 3)
    gy = np.clip(((k.pos[:, 1] - by0) / (by1 - by0) * 4).astype(int), 0, 3)
    cell = gx * 4 + gy
    e.add("Grid crossings (4×4)", int(np.sum(cell[1:] != cell[:-1])), 2,
          "(opcional) cambios de celda de una rejilla 4×4 sobre la caja de la arena")
    inner = (gx >= 1) & (gx <= 2) & (gy >= 1) & (gy <= 2)
    e.add("Inner grid squares time (%)", round(100 * float(inner.mean()), 2), 2.0, "(opcional) ")


def initial_heading(k: Kin):
    """Del primer punto al punto tras 1 s móvil (posiciones suavizadas; los cuadros inmóviles no cuentan).
    doc: el cuadro en que el tiempo móvil acumulado llega a 1 s; impl: el primer cuadro móvil con ≥ 1 s móvil antes."""
    if k.variant == "doc":
        i = first(np.cumsum(k.mobile) / FPS >= 1.0)
    else:
        before = np.r_[0.0, np.cumsum(k.mobile)[:-1]] / FPS
        cand = np.flatnonzero(k.mobile & (before >= 1.0 - 1e-9) & (np.arange(k.n) > 0))
        mob = np.flatnonzero(k.mobile & (np.arange(k.n) > 0))
        i = int(cand[0]) if len(cand) else (int(mob[-1]) if len(mob) else None)
    if i is None:
        return None
    d = k.spos[i] - k.spos[0]
    if np.hypot(*d) / k.ppc <= 0.5:
        # tras «1 s móvil» apenas se ha desplazado (paradas < 2 s cuentan como móvil): el rumbo sería el del temblor
        # del rastreo (H-P07). El rumbo es entonces el del punto tras 1 s desplazándose de verdad (cuadros móviles
        # más rápidos que el umbral); vacío si tampoco se ha alejado más de 0,5 cm.
        mv = k.mobile & k.moving
        before = np.r_[0.0, np.cumsum(mv)[:-1]] / FPS
        cand = np.flatnonzero(mv & (before >= 1.0 - 1e-9) & (np.arange(k.n) > 0))
        mvi = np.flatnonzero(mv & (np.arange(k.n) > 0))
        if not len(mvi):
            return None
        d = k.spos[int(cand[0]) if len(cand) else int(mvi[-1])] - k.spos[0]
        if np.hypot(*d) / k.ppc <= 0.5:
            return None
    return math.degrees(math.atan2(d[1], d[0])) if np.hypot(*d) > 1e-9 else None


def per_zone(k: Kin, e, app, unit, names, memb=None):
    memb = memb if memb is not None else app.zone_membership(k.pos[:, 0], k.pos[:, 1])
    hmemb = app.zone_membership(k.nose[:, 0], k.nose[:, 1])
    zones = {z.name: z for z in app.zones}
    first_entry = {z: f for z in names if z in zones and (f := first(memb[z])) is not None}
    order = sorted(first_entry, key=lambda z: first_entry[z])
    unique_first = len(order) > 0 and sum(first_entry[z] == first_entry[order[0]] for z in order) == 1
    if unique_first:
        e.add("First zone entered", order[0], "texto", "la primera zona (no grupo) en la que entra")
    if order:
        e.add("Visited zones", ", ".join(order), "texto", "zonas (no grupos) en el orden de su primera entrada")
    ih = initial_heading(k)
    for z in names:
        ins = np.asarray(memb[z], bool)
        hin = np.asarray(hmemb[z], bool)
        n_ent = entries_of(ins)
        tt = round(0.12 * 2 * max(n_ent, 1) + 0.2, 2)
        t_in = ins.sum() / FPS
        e.add(f"{z}: time (%)", round(100 * t_in / k.dur, 2), round(100 * tt / k.dur, 2), "")
        e.add(f"{z}: entries (/min)", round(n_ent / (k.dur / 60), 3), 0.05, "entradas / minutos")
        ent = ([0] if ins[0] else []) + list(np.flatnonzero(~ins[:-1] & ins[1:]) + 1)
        ex = list(np.flatnonzero(ins[:-1] & ~ins[1:]) + 1)
        e.add(f"{z}: latency to second entry (s)", round(ent[1] / FPS, 2) if len(ent) > 1 else round(k.dur, 2), 0.25, "")
        e.add(f"{z}: exits", len(ex), 0, "")
        e.add(f"{z}: latency to first exit (s)", round(ex[0] / FPS, 2) if ex else round(k.dur, 2), 0.25, "")
        if ent:
            e.add(f"{z}: latency to last entry (s)", round(ent[-1] / FPS, 2), 0.25, "")
        if ex:
            e.add(f"{z}: time of last exit (s)", round(ex[-1] / FPS, 2), 0.25, "")
        visits = [(b - a) / FPS for a, b in runs(ins)]
        e.add(f"{z}: visit durations (s)", ", ".join(f"{v:.10g}" for v in [round(v, 2) for v in visits]), "lista",
              "la duración de cada visita, separadas por comas")
        if visits:
            e.add(f"{z}: mean visit (s)", round(t_in / n_ent, 2), round(tt / n_ent + 0.1, 2), "tiempo / entradas")
            e.add(f"{z}: longest visit (s)", round(max(visits), 2), 0.3, "")
            e.add(f"{z}: shortest visit (s)", round(min(visits), 2), 0.3, "")
        dz = float(k.step[1:][ins[:-1]].sum())
        e.add(f"{z}: distance ({unit})", round(dz, 2), round(0.05 * dz + 0.5, 2),
              "pasos que parten de dentro (el de entrada no cuenta, el de salida sí)")
        if t_in > 0:
            e.add(f"{z}: mean speed ({unit}/s)", round(dz / t_in, 3), round(0.06 * dz / t_in + 0.2, 3), "")
            spz = (k.speed if k.variant == "doc" else ma(k.speed, k.w))[ins]
            e.add(f"{z}: max speed ({unit}/s)", round(float(spz.max()), 2), round(0.12 * spz.max() + 0.5, 2), "")
        e.add(f"{z}: time mobile (s)", k.mins(ins & k.mobile), tt + 0.3, "")
        e.add(f"{z}: time immobile (s)", k.mins(ins & k.immobile), tt + 0.3, "")
        e.add(f"{z}: time freezing (s)", k.mins(ins & k.freezing), tt + 0.2, "")
        e.add(f"{z}: immobile episodes", len(runs(k.immobile & ins)), 0, "también empieza uno al entrar ya inmóvil")
        e.add(f"{z}: freezing episodes", sum(1 for a, _ in runs(k.freezing) if ins[a]), 0, "comienzos dentro")
        e.add(f"{z}: time active (s)", k.mins(ins & k.mobile), tt + 0.3, "")
        e.add(f"{z}: time inactive (s)", k.mins(ins & k.immobile), tt + 0.3, "")
        e.add(f"{z}: inactive episodes", len(runs(k.immobile & ins)), 0, "")
        h_ent = int(np.sum(~hin[:-1] & hin[1:]))
        htol = round(0.12 * 2 * max(h_ent, 1) + 0.3, 2)
        e.add(f"{z}: head entries", h_ent, 0, "la cabeza pasa de fuera a dentro (empezar dentro no cuenta)")
        e.add(f"{z}: head time (s)", k.mins(hin), htol, "")
        fh = first(~hin[:-1] & hin[1:])
        e.add(f"{z}: latency to head entry (s)", round((fh + 1) / FPS, 2) if fh is not None else round(k.dur, 2),
              0.25, "")
        fhx = first(hin[:-1] & ~hin[1:])
        e.add(f"{z}: latency to first head exit (s)", round((fhx + 1) / FPS, 2) if fhx is not None else round(k.dur, 2),
              0.25, "")
        zsh = zones.get(z)
        cin = (np.asarray(zsh.shape.contains(k.pos[:, 0], k.pos[:, 1]), bool) if zsh is not None else ins)
        e.add(f"{z}: time head in zone with centre outside (s)", k.mins(hin & ~cin), htol,
              "por posición, no por la regla de entrada (la guía)")
        dhz = float(k.hstep[1:][hin[:-1]].sum())
        e.add(f"{z}: head distance ({unit})", round(dhz, 2), round(0.06 * dhz + 0.5, 2),
              "suavizada como la del centro; como el centro (guía): cuenta el paso que sale de la zona, no el que entra")
        fe = first(ins)
        if fe is not None:
            before = float(k.step[1:fe + 1].sum()) if fe > 0 else 0.0
            e.add(f"{z}: distance before first entry ({unit})", round(before, 2), round(0.05 * before + 0.5, 2), "")
            if before > 0:
                eff = float(np.hypot(*(k.spos[fe] - k.spos[0]))) / k.ppc / before
                e.add(f"{z}: path efficiency to first entry", round(eff, 3), round(0.06 * eff + 0.02, 3), "")
        zobj = zones.get(z)
        keep = np.isfinite(k.turn) & ins & (k.turn_prev >= 0) & ins[np.maximum(k.turn_prev, 0)]
        ta = float(k.turn[keep].sum())
        e.add(f"{z}: absolute turn angle (deg)", round(ta, 1), round(0.08 * ta + 15, 1), "")
        ha = float(np.abs(k.head_turn[ins]).sum())
        e.add(f"{z}: absolute head turn angle (deg)", round(ha, 1), round(0.08 * ha + 15, 1),
              "un giro cuenta en la zona en que queda el animal")
        if zobj is not None and unique_first:
            e.add(f"{z}: was first zone entered", "Yes" if order[0] == z else "No", "texto", "")
        if app.lines:
            both = 0
            for ln in app.lines:
                for i in range(1, k.n):
                    if ins[i - 1] and ins[i] and seg_intersect(k.pos[i - 1], k.pos[i], (ln.x1, ln.y1), (ln.x2, ln.y2)):
                        both += 1
            e.add(f"{z}: line crossings", both, 0, "cruces de cualquier línea, dentro de la zona antes y después")
        grid_cell = any(z in g.zones for g in getattr(app, "grids", []))
        if zobj is not None and not grid_cell:
            zone_geometry(k, e, unit, zobj, ins, hin, ih, cin)
        elif zobj is None:
            group_border(k, e, app, unit, z, ins)


def zone_geometry(k, e, unit, zobj, ins, hin, ih, cin=None):
    """Distancias a la zona y a su borde, mirar a la zona, acercarse / alejarse, rumbo (solo zonas, no grupos)."""
    z = zobj.name
    poly = np.asarray(zobj.shape.polygon(72), float)
    d_c = polygon_distance(poly, k.pos) / k.ppc
    d_h = polygon_distance(poly, k.nose) / k.ppc
    do = np.where(ins, 0.0, d_c)
    ho = np.where(hin, 0.0, d_h)
    dtol = round(0.03 * max(do.max(), 1) + 0.15, 2)
    e.add(f"{z}: mean distance from zone ({unit})", round(float(do.mean()), 2), dtol,
          "fuera, ponderada por tiempo, / duración (0 dentro)")
    e.add(f"{z}: max distance from zone ({unit})", round(float(do.max()) if (~ins).any() else 0.0, 2), dtol, "")
    e.add(f"{z}: min distance from zone ({unit})", round(0.0 if ins.any() else float(do.min()), 2), dtol,
          "0 una vez ha estado dentro")
    e.add(f"{z}: initial distance from zone ({unit})", round(float(do[0]), 2), dtol, "")
    e.add(f"{z}: cumulative distance from zone ({unit}·s)", round(float(do.sum() / FPS), 1),
          round(0.03 * do.sum() / FPS + 1, 1), "Σ distancia·dt")
    htol = round(dtol + 0.2, 2)
    e.add(f"{z}: mean head distance from zone ({unit})", round(float(ho.mean()), 2), htol, "")
    e.add(f"{z}: max head distance from zone ({unit})", round(float(ho.max()) if (~hin).any() else 0.0, 2), htol, "")
    e.add(f"{z}: min head distance from zone ({unit})", round(0.0 if hin.any() else float(ho.min()), 2), htol, "")
    cin = ins if cin is None else cin  # el centro dentro de la forma (no la regla de entrada)
    if cin.any():
        di = d_c[cin]
        e.add(f"{z}: mean distance to border when inside ({unit})", round(float(di.mean()), 2), dtol, "el centro dentro")
        e.add(f"{z}: max distance to border when inside ({unit})", round(float(di.max()), 2), dtol, "")
        e.add(f"{z}: min distance to border when inside ({unit})", round(0.0 if exits_of(cin) else float(di.min()), 2),
              dtol, "0 una vez ha salido")
    if hin.any():
        hi = d_h[hin]
        e.add(f"{z}: mean head distance to border when inside ({unit})", round(float(hi.mean()), 2), htol, "")
        e.add(f"{z}: max head distance to border when inside ({unit})", round(float(hi.max()), 2), htol, "")
        mn_h = 0.0 if exits_of(hin) else float(hi.min())
        e.add(f"{z}: min head distance to border when inside ({unit})", round(mn_h, 2), htol,
              "0 una vez la cabeza ha salido (como el centro, guía y ANY-maze)")
    # CIPL (Gallagher): la distancia a la zona muestreada cada segundo hasta la primera entrada, menos la de un camino
    # ideal recto a la velocidad media hasta la entrada (la otra lectura, la velocidad de toda la prueba con el
    # recorrido hasta la entrada, no es ninguno de los dos modos de ANY-maze: auditoría del oráculo)
    fe = first(ins)
    if fe is not None and fe > 0:
        span = fe / FPS
        idx = np.minimum((np.arange(0.0, span, 1.0) * FPS).astype(int), fe - 1)
        dsec = do[idx]
        v = float(k.step[1:fe].sum()) / span
        ideal = np.maximum(float(dsec[0]) - v * np.arange(len(dsec)), 0.0)
        cipl = float(dsec.sum() - ideal.sum())
        e.add(f"{z}: corrected integrated path length ({unit}·s)", round(cipl, 1), round(0.05 * abs(cipl) + 1.0, 1),
              "Σ distancia cada 1 s hasta entrar − lo mismo para un camino recto a la velocidad media")
    nxt = np.roll(poly, -1, axis=0)
    seg = np.hypot(*(nxt - poly).T)
    per = np.maximum(1, np.round(64 * seg / max(seg.sum(), 1e-9)).astype(int))
    border = np.vstack([a + np.linspace(0, 1, m, endpoint=False)[:, None] * (b - a)
                        for a, b, m in zip(poly, nxt, per)])
    origin = k.nose  # ANY-maze mide la dirección al borde desde la cabeza (auditoría del oráculo)
    to = np.degrees(np.arctan2(border[None, :, 1] - origin[:, 1, None], border[None, :, 0] - origin[:, 0, None]))
    facing = (np.min(np.abs(wrap(to - k.ang[:, None])), axis=1) <= ORIENT) & ~ins
    e.add(f"{z}: time facing (s)", k.mins(facing), round(0.04 * facing.sum() / FPS + 0.3, 2),
          "fuera, orientación (centro→cabeza) a ≤ 30° de la dirección desde la cabeza a algún punto del borde")
    sin = np.asarray(zobj.shape.contains(k.spos[:, 0], k.spos[:, 1]), bool)
    ds = np.where(sin, 0.0, polygon_distance(poly, k.spos))
    dd = np.r_[0.0, np.diff(ds)]
    out = ~ins
    gt = round(0.06 * out.sum() / FPS + 0.8, 2)
    e.add(f"{z}: time getting closer (s)", k.mins(out & (dd < -1e-9)), gt, "")
    e.add(f"{z}: time getting further away (s)", k.mins(out & (dd > 1e-9)), gt, "")
    zx, zy = zobj.shape.centroid()
    brg = np.degrees(np.arctan2(zy - k.spos[:, 1], zx - k.spos[:, 0]))
    err = np.abs(wrap(k.heading - brg))
    mov = (k.moving if k.variant == "doc" else k.mobile) & out & np.isfinite(err)
    mt = round(0.05 * mov.sum() / FPS + 0.3, 2)
    e.add(f"{z}: time moving towards (s)", k.mins(mov & (err < FACING)), mt, "móvil, fuera, rumbo < 45° del centro")
    e.add(f"{z}: time moving away (s)", k.mins(mov & (err > 180 - FACING)), mt, "")
    if mov.any():
        e.add(f"{z}: mean absolute heading error (deg)", round(float(err[mov].mean()), 1), 3.0,
              "media sobre los cuadros móviles fuera")
    if ih is not None and not zobj.shape.contains(k.spos[:1, 0], k.spos[:1, 1])[0]:
        to0 = math.degrees(math.atan2(zy - k.spos[0, 1], zx - k.spos[0, 0]))
        signed = float(wrap(to0 - ih))
        e.add(f"{z}: initial heading error (deg)", round(abs(signed), 1), 5.0, "rumbo inicial frente al centro de la zona")
        e.add(f"{z}: signed initial heading error (deg)", round(signed, 1), 5.0, "positivo = zona a la derecha")
    hz = np.degrees(np.arctan2(zy - k.nose[:, 1], zx - k.nose[:, 0]))
    ori = ins & (np.abs(wrap(k.ang - hz)) <= ORIENT)
    e.add(f"{z}: time oriented towards zone centre when inside (s)", k.mins(ori), round(0.04 * ori.sum() / FPS + 0.3, 2),
          "")


def group_border(k, e, app, unit, name, ins):
    """Grupos: distancia al borde (el contorno de la unión de sus zonas) estando dentro."""
    m = mask_image(app, name)
    if not m.any() or not ins.any():
        return
    din = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    dtol = round(10 / k.ppc + 0.2, 2)  # (máscara de píxeles: aproximada)
    di = sample(din, k.pos)[ins] / k.ppc
    e.add(f"{name}: mean distance to border when inside ({unit})", round(float(di.mean()), 2), dtol, "")
    e.add(f"{name}: max distance to border when inside ({unit})", round(float(di.max()), 2), dtol, "")


def per_point(k: Kin, e, app, unit):
    for p in app.points:
        px, py = p.x, p.y
        e.add(f"{p.name}: X ({unit})", round(px / k.ppc, 2), 0.02, "")
        e.add(f"{p.name}: Y ({unit})", round(py / k.ppc, 2), 0.02, "")
        d = np.hypot(k.pos[:, 0] - px, k.pos[:, 1] - py) / k.ppc
        tol = round(0.02 * d.max() + 0.2, 2)
        e.add(f"{p.name}: mean distance ({unit})", round(float(d.mean()), 2), tol, "")
        e.add(f"{p.name}: min distance ({unit})", round(float(d.min()), 2), tol, "")
        e.add(f"{p.name}: max distance ({unit})", round(float(d.max()), 2), tol, "")
        hd = np.hypot(k.nose[:, 0] - px, k.nose[:, 1] - py) / k.ppc
        if not p.radius_cm or p.radius_cm <= 0:
            continue
        near = (d if k.variant == "doc" else hd) <= p.radius_cm
        nt = round(0.12 * 2 * max(entries_of(near), 1) + 0.3, 2)
        e.add(f"{p.name}: time near (s)", k.mins(near), nt, "a ≤ el radio del punto (doc: el centro; impl: la cabeza)")
        e.add(f"{p.name}: approaches", len(runs(near)), 0, "")
        fa = first(near)
        e.add(f"{p.name}: latency to approach (s)", round(fa / FPS, 2) if fa is not None else round(k.dur, 2), 0.25, "")
        hto = np.degrees(np.arctan2(py - k.nose[:, 1], px - k.nose[:, 0]))
        herr = np.abs(wrap(k.ang - hto))
        expl = (hd <= p.radius_cm) & (herr <= FACING)
        if k.variant == "impl":
            expl |= hd <= 0.5 * p.radius_cm
        e.add(f"{p.name}: time exploring (s)", k.mins(expl), round(0.12 * 2 * max(len(runs(expl)), 1) + 0.3, 2),
              "cabeza a ≤ el radio y mirando al punto (±45°)")
        e.add(f"{p.name}: latency to explore (s)", round(first(expl) / FPS, 2) if expl.any() else round(k.dur, 2),
              0.25, "")
        bouts = runs(expl) if k.variant == "doc" else runs(drop_short(expl, 0.2, value=True))
        e.add(f"{p.name}: exploration bouts", len(bouts), 0, "doc: cada tramo; impl: tramos de ≥ 0,2 s")
        hspeed = k.hstep * FPS
        hsp = hspeed if k.variant == "doc" else ma(hspeed, int(round(0.5 * FPS)))
        dh = np.diff(k.snose, axis=0, prepend=k.snose[:1])
        hdir = np.degrees(np.arctan2(dh[:, 1], dh[:, 0]))
        hbrg = np.degrees(np.arctan2(py - k.snose[:, 1], px - k.snose[:, 0]))
        herr2 = np.abs(wrap(hdir - hbrg))
        hmov = hsp > MOBILITY
        mt = round(0.08 * hmov.sum() / FPS + 0.5, 2)
        e.add(f"{p.name}: time head moving towards (s)", k.mins(hmov & (herr2 < FACING)), mt,
              "la cabeza se mueve (> umbral) hacia el punto (±45°)")
        e.add(f"{p.name}: time head moving away (s)", k.mins(hmov & (herr2 > 180 - FACING)), mt, "")
        base = k.spos if k.variant == "doc" else np.vstack([k.spos[:1], k.spos[:-1]])
        brg = np.degrees(np.arctan2(py - base[:, 1], px - base[:, 0]))
        err = np.abs(wrap(k.heading - brg))
        mov = (k.moving if k.variant == "doc" else k.mobile) & np.isfinite(err)
        mt = round(0.05 * mov.sum() / FPS + 0.3, 2)
        tow = mov & (err < FACING)
        e.add(f"{p.name}: time moving towards (s)", k.mins(tow), mt, "")
        e.add(f"{p.name}: time moving away (s)", k.mins(mov & (err > 180 - FACING)), mt, "")
        ud = np.hypot(k.spos[:, 0] - px, k.spos[:, 1] - py) / k.ppc
        dd = np.r_[0.0, np.diff(ud)]
        mm = k.mobile if k.variant == "impl" else k.moving
        e.add(f"{p.name}: distance moved towards ({unit})", round(float(-dd[mm & (dd < 0)].sum()), 2),
              round(0.05 * float(np.abs(dd).sum()) + 0.5, 2), "cuánto bajó la distancia estando móvil")
        e.add(f"{p.name}: distance moved away ({unit})", round(float(dd[mm & (dd > 0)].sum()), 2),
              round(0.05 * float(np.abs(dd).sum()) + 0.5, 2), "")
        if tow.any():
            e.add(f"{p.name}: mean speed moving towards ({unit}/s)", round(float(k.step[tow].sum() / (tow.sum() / FPS)), 3),
                  0.5, "")
        ot = round(0.03 * k.dur + 0.3, 2)
        e.add(f"{p.name}: time head oriented towards (s)", k.mins(herr < ORIENT), ot, "")
        e.add(f"{p.name}: time head oriented away (s)", k.mins(herr > 180 - ORIENT), ot, "")
        e.add(f"{p.name}: mean head angle (deg)", round(float(herr.mean()), 1), 3.0, "")
        e.add(f"{p.name}: head turns towards", sum(1 for a, _ in runs(herr < ORIENT) if a > 0), 1,
              "veces que pasa a estar orientado hacia el punto (no al empezar)")
        e.add(f"{p.name}: mean head distance ({unit})", round(float(hd.mean()), 2), tol + 0.2, "")
        e.add(f"{p.name}: min head distance ({unit})", round(float(hd.min()), 2), tol + 0.2, "")
        e.add(f"{p.name}: max head distance ({unit})", round(float(hd.max()), 2), tol + 0.2, "")


def per_line(k: Kin, e, app):
    total = 0
    for ln in app.lines:
        a = b = 0
        lat = None
        for i in range(1, k.n):
            s = seg_intersect(k.pos[i - 1], k.pos[i], (ln.x1, ln.y1), (ln.x2, ln.y2))
            if s:
                a, b = (a + 1, b) if s > 0 else (a, b + 1)
                lat = i / FPS if lat is None else lat
        total += a + b
        e.add(f"{ln.name}: crossings", a + b, 0, "pasos del centro que cortan la línea")
        e.add(f"{ln.name}: crossings left-to-right", f"{a}|{b}", 0, "uno de los dos sentidos (la guía no define cuál)")
        e.add(f"{ln.name}: crossings right-to-left", f"{b}|{a}", 0, "")
        if lat is not None:
            e.add(f"{ln.name}: latency to first crossing (s)", round(lat, 2), 0.2, "")
    if app.lines:
        e.add("Total line crossings", total, 0, "")


def one_variant(truth, e, unit, zones, mobility, freezing, variant):
    k = Kin(truth.pos, truth.ang, truth.nose, truth.px_per_cm, variant=variant, freezing=freezing,
            detected=getattr(truth, "detected", None))
    k.zone_memb = getattr(truth, "_memb", None)
    app = truth.app
    whole_test(k, e, app, unit, mobility)
    if mobility:
        names = zones if zones is not None else list(app.zone_membership(k.pos[:1, 0], k.pos[:1, 1]))
        per_zone(k, e, app, unit, names, memb=getattr(truth, "_memb", None))
    per_point(k, e, app, unit)
    per_line(k, e, app)


def all_measures(truth, e, unit="cm", zones=None, mobility=True, freezing=None):
    """Añade a `e` todas las medidas calculables de `truth` (oraculo_sintetico.Truth / TrackTruth), con las dos
    variantes doc / impl fundidas («doc|impl» cuando difieren). Las tolerancias son las de la capa A; `e` las
    escala si es de la capa B."""
    rows = {}
    for v in VARIANTS:
        sub = type(e)(e.animal, e.apparatus)  # sin escalar: e.add escala una sola vez
        one_variant(truth, sub, unit, zones, mobility, freezing, v)
        rows[v] = {r["Measure"]: r for r in sub.rows}
    for m, r in rows["doc"].items():
        alt = rows["impl"].get(m)
        val = r["Expected"]
        if alt is not None and str(alt["Expected"]) != str(val):
            try:
                same = abs(float(alt["Expected"]) - float(val)) <= 1e-9
            except (TypeError, ValueError):
                same = False
            if not same:
                val = f"{val}|{alt['Expected']}"
        e.add(m, val, r["Tolerance"], r["Note"])
