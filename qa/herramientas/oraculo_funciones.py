"""Oráculo de las funciones que los experimentos por plantilla no activan: zonas de investigación, reglas de entrada
(cabeza / cola), secuencias, rejillas, rearing automático, teclas puntuadas (también por zona), periodos con nombre y
anclados a eventos, intervalos regulares, cálculos y zonas ocultas.

Lo usa oraculo_sintetico.py (experimentos «funciones», «open_field_intervalos» y «home_cage_escondido»). Como el resto
del oráculo: los valores esperados salen de la trayectoria programada y de las definiciones de la guía (sección 9),
sin el código de medidas de la app.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from oraculo_medidas import FPS, SIZE, Kin, first, polygon_distance, runs, wrap

BEHAVIOURS = [("Levantarse", "r", "state"), ("Defecar", "d", "point"), ("Acicalarse", "g", "hold")]
REAR_FACTOR = 0.55  # largo del cuerpo mientras se levanta (visto desde arriba se ve más corto y más pequeño)


# ------------------------------------------------------------------------------------------------ dibujo
def render(path, pos, ang, lengths, visible, bg=200, noise=2.0, seed=5):
    """Un animal (synthetic.draw_mouse) con largo por cuadro y cuadros en que no se ve (escondido)."""
    from manymaze.core import synthetic as syn

    rng = np.random.default_rng(seed)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, SIZE)
    base = np.full((SIZE[1], SIZE[0]), bg, np.uint8)
    for i in range(len(pos)):
        img = base.copy()
        if visible[i]:
            syn.draw_mouse(img, pos[i, 0], pos[i, 1], ang[i], length=float(lengths[i]), width=float(lengths[i]) / 2)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        w.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    w.release()


# ------------------------------------------------------------------------------------------------ pertenencia
def tail_points(truth):
    if getattr(truth, "tail", None) is not None:
        return truth.tail  # capa B: la base de la cola que rastreó la app
    a = np.radians(truth.ang)
    back = 0.45 * truth.nose_len / 0.77
    return np.column_stack([truth.pos[:, 0] - back * np.cos(a), truth.pos[:, 1] - back * np.sin(a)])


def membership(truth, app, hidden=None):
    """Por zona, según su regla: centro (por defecto), cabeza, base de la cola; una zona de investigación cuenta
    con la cabeza dentro o a ≤ la distancia de investigación de su borde; una zona oculta, además, los cuadros en que
    el animal no se ve tras haber estado en ella o cerca. Los grupos: unión menos excluidas."""
    ppc = truth.px_per_cm or 1.0
    tail = tail_points(truth)
    out = {}
    for z in app.zones:
        rule = (z.entry_rule or "centre").lower()
        if z.investigation_distance_cm and z.investigation_distance_cm > 0:
            dh = polygon_distance(z.shape.polygon(72), truth.nose) / ppc
            m = np.asarray(z.shape.contains(truth.nose[:, 0], truth.nose[:, 1]), bool) | (dh <= z.investigation_distance_cm)
        else:
            pts = truth.nose if rule == "head" else tail if rule == "tail" else truth.pos
            m = np.asarray(z.shape.contains(pts[:, 0], pts[:, 1]), bool)
        if hidden is not None and hidden.any():
            m = m & ~hidden  # sin verlo no se sabe dónde está…
            if z.hidden:  # …salvo en la zona oculta en la que (o cerca de la que) desapareció
                for a, b in runs(hidden):
                    if a > 0 and (m[a - 1] or near_zone(z, truth.pos[a - 1])):
                        m[a:b] = True
        out[z.name] = m
    for g in app.groups:
        m = np.zeros(truth.n, bool)
        for zn in g.zones:
            m |= out.get(zn, np.zeros(truth.n, bool))
        for zn in g.exclude:
            m &= ~out.get(zn, np.zeros(truth.n, bool))
        out[g.name] = m
    return out


def near_zone(z, p):
    """¿Desaparece en la zona oculta o cerca (a ≤ la mitad del tamaño de la zona, la distancia por defecto)?"""
    b = z.shape.bounds()
    margin = 0.5 * min(b[2] - b[0], b[3] - b[1])
    if z.shape.contains(np.array([p[0]]), np.array([p[1]]))[0]:
        return True
    return polygon_distance(z.shape.polygon(72), np.array([p]))[0] <= margin


# ------------------------------------------------------------------------------------------------ medidas
def investigation(truth, e, app, k: Kin, unit):
    """Zonas de investigación: la cabeza fuera de la zona, a ≤ la distancia de su borde y mirando a ella (el
    vector centro→cabeza a ≤ 45° de la dirección del centro a la zona)."""
    ppc = truth.px_per_cm or 1.0
    investigated = []
    for z in app.zones:
        d = z.investigation_distance_cm
        if not d or d <= 0:
            continue
        head_in = np.asarray(z.shape.contains(truth.nose[:, 0], truth.nose[:, 1]), bool)
        dh = polygon_distance(z.shape.polygon(72), truth.nose) / ppc
        zx, zy = z.shape.centroid()
        to = np.degrees(np.arctan2(zy - truth.pos[:, 1], zx - truth.pos[:, 0]))
        facing = np.abs(wrap(truth.ang - to)) <= 45.0
        inv = ~head_in & (dh <= d) & facing
        bouts = runs(inv)
        durs = [(b - a) / FPS for a, b in bouts]
        n = len(bouts)
        bt = round(0.12 * 2 * max(n, 1) + 0.3, 2)
        e.add(f"{z.name}: investigation bouts", n, 1, "cabeza fuera, a ≤ la distancia del borde y mirando a la zona")
        e.add(f"{z.name}: time investigating (s)", round(sum(durs), 2), bt, "")
        if bouts:
            investigated.append((bouts[0][0], z.name))
            e.add(f"{z.name}: latency to first investigation (s)", round(bouts[0][0] / FPS, 2), 0.25, "")
            e.add(f"{z.name}: latency to end of first investigation (s)", round(bouts[0][1] / FPS, 2), 0.3, "")
            e.add(f"{z.name}: longest investigation bout (s)", round(max(durs), 2), 0.3, "")
            e.add(f"{z.name}: shortest investigation bout (s)", round(min(durs), 2), 0.3, "")
            e.add(f"{z.name}: mean investigation bout (s)", round(sum(durs) / n, 2), bt, "tiempo / episodios")
            dist_inv = float(k.step[inv].sum())
            e.add(f"{z.name}: distance while investigating ({unit})", f"{dist_inv:.2f}|{float(k.step[1:][inv[:-1]].sum()):.2f}",
                  round(0.08 * dist_inv + 0.5, 2), "pasos hacia cuadros investigando (o desde)")
            before = float(k.step[1:bouts[0][0] + 1].sum())
            e.add(f"{z.name}: distance before first investigation ({unit})", round(before, 2), round(0.05 * before + 0.5, 2), "")
            e.add(f"{z.name}: time immobile while investigating (s)", k.mins(inv & k.immobile), bt + 0.3, "")
            e.add(f"{z.name}: time mobile while investigating (s)", k.mins(inv & k.mobile), bt, "")
            e.add(f"{z.name}: time freezing while investigating (s)", k.mins(inv & k.freezing), bt, "")
            e.add(f"{z.name}: mean speed while investigating ({unit}/s)", round(dist_inv / sum(durs), 3),
                  round(0.1 * dist_inv / sum(durs) + 0.3, 3), "distancia investigando / tiempo")
            e.add(f"{z.name}: immobile episodes while investigating", len(runs(inv & k.immobile)), 1, "")
            e.add(f"{z.name}: freezing episodes while investigating", sum(1 for a, _ in runs(k.freezing) if inv[a]), 1,
                  "comienzos de freezing investigando")
            e.add(f"{z.name}: investigation durations (s)", ", ".join(f"{d:.10g}" for d in [round(d, 2) for d in durs]),
                  "lista", "")
        else:
            e.add(f"{z.name}: latency to first investigation (s)", round(k.dur, 2), 0.25, "")
    investigated.sort()
    for z in app.zones:
        if z.investigation_distance_cm and z.investigation_distance_cm > 0 and investigated:
            e.add(f"{z.name}: was first zone investigated", "Yes" if investigated[0][1] == z.name else "No", "texto", "")
    if investigated:
        e.add("Investigated zones", ", ".join(n for _, n in investigated), "texto", "en el orden de la primera investigación")


def rearing(truth, e, k: Kin, memb, lengths):
    """Rears: el cuerpo visto desde arriba más corto y más pequeño (< 80 % del largo y < 75 % del área habitual);
    rears de menos de 0,3 s no cuentan."""
    usual = float(np.median(lengths))
    rear = lengths < 0.8 * usual
    bouts = [(a, b) for a, b in runs(rear) if (b - a) / FPS >= 0.3]
    durs = [(b - a) / FPS for a, b in bouts]
    tol = round(0.15 * 2 * max(len(bouts), 1) + 0.2, 2)
    e.add("Rears", len(bouts), 0, "cuerpo al 55 % de su largo (programado)")
    e.add("Time rearing (s)", round(sum(durs), 2), tol, "")
    e.add("Latency to first rear (s)", round(bouts[0][0] / FPS, 2) if bouts else round(k.dur, 2), 0.3, "")
    if durs:
        e.add("Mean rear duration (s)", round(sum(durs) / len(durs), 2), tol, "tiempo / rears")
        e.add("Max rear duration (s)", round(max(durs), 2), 0.3, "")
        e.add("Min rear duration (s)", round(min(durs), 2), 0.3, "")
    for z, m in memb.items():
        zb = [(a, b) for a, b in bouts if m[a]]
        if zb or m.any():
            e.add(f"{z}: rears", len(zb), 0, "un rear pertenece a la zona en que empieza")
            e.add(f"{z}: time rearing (s)", round(float((m & rear).sum()) / FPS, 2), tol, "")
            e.add(f"{z}: latency to first rear (s)", round(zb[0][0] / FPS, 2) if zb else round(k.dur, 2), 0.3, "")
            zd = [float((m[a:b] & rear[a:b]).sum()) / FPS for a, b in zb]
            if zd:
                e.add(f"{z}: mean rear duration (s)", round(sum(zd) / len(zd), 2), tol, "tiempo / rears")
                e.add(f"{z}: max rear duration (s)", round(max(zd), 2), 0.3, "")
                e.add(f"{z}: min rear duration (s)", round(min(zd), 2), 0.3, "")


def keys(truth, e, k: Kin, memb, events, unit):
    """Teclas: puntuales (recuento, latencia, tasa) y de estado / mantener (pulsaciones, duración, %, latencia, bout
    medio, más largo / corto, latencia a la primera suelta, tasa, lista de duraciones, distancia antes de la primera
    pulsación); y por zona (la pulsación cuenta en la zona en que está el animal en ese momento)."""
    cumdist = np.cumsum(k.step)
    for name, _, kind in BEHAVIOURS:
        evs = [ev for ev in events if ev["behaviour"] == name]
        presses = [ev["t"] for ev in evs]
        tol = 0.05
        e.add(f"{name}: count", len(evs), 0, "")
        e.add(f"{name}: latency (s)", round(min(presses), 2) if presses else round(k.dur, 2), tol, "")
        e.add(f"{name}: rate (/min)", round(len(evs) / (k.dur / 60), 3), 0.02, "")
        if presses:
            i0 = int(round(min(presses) * FPS))
            e.add(f"{name}: distance before first press ({unit})", round(float(cumdist[min(i0, k.n - 1)]), 2),
                  round(0.03 * cumdist[min(i0, k.n - 1)] + 0.5, 2), "")
        if kind != "point":
            durs = [ev["t_end"] - ev["t"] for ev in evs]
            e.add(f"{name}: duration (s)", round(sum(durs), 2), tol, "")
            e.add(f"{name}: duration (%)", round(100 * sum(durs) / k.dur, 2), 0.3, "")
            e.add(f"{name}: press durations (s)", ", ".join(f"{d:.10g}" for d in [round(d, 2) for d in durs]), "lista", "")
            if durs:
                e.add(f"{name}: mean bout (s)", round(sum(durs) / len(durs), 2), tol, "duración / pulsaciones")
                e.add(f"{name}: longest bout (s)", round(max(durs), 2), tol, "")
                e.add(f"{name}: shortest bout (s)", round(min(durs), 2), tol, "")
                e.add(f"{name}: latency to first release (s)", round(min(ev["t_end"] for ev in evs), 2), tol, "")
        for z, m in memb.items():
            zt = m.sum() / FPS
            zp = [ev for ev in evs if m[min(int(round(ev["t"] * FPS)), k.n - 1)]]
            e.add(f"{name} in {z}: count", len(zp), 0, "la pulsación cuenta en la zona en que está el animal")
            if zt > 0:
                e.add(f"{name} in {z}: rate (/min)", round(len(zp) / (zt / 60), 3), 0.3,
                      "por minuto pasado en la zona")
            if zp:
                e.add(f"{name} in {z}: latency (s)", round(min(ev["t"] for ev in zp), 2), tol, "")
                i0 = min(int(round(min(ev["t"] for ev in zp) * FPS)), k.n - 1)
                e.add(f"{name} in {z}: distance before first press ({unit})", round(float(cumdist[i0]), 2),
                      round(0.03 * cumdist[i0] + 0.5, 2), "")
            if kind != "point":
                pressed = np.zeros(k.n, bool)
                for ev in evs:
                    pressed[int(round(ev["t"] * FPS)):int(round(ev["t_end"] * FPS))] = True
                e.add(f"{name} in {z}: duration (s)", round(float((pressed & m).sum()) / FPS, 2), 0.1,
                      "tiempo pulsada estando en la zona")
                stretches = [(b - a) / FPS for a, b in runs(pressed & m)]
                if stretches and zp:
                    e.add(f"{name} in {z}: longest bout (s)", round(max(stretches), 2), 0.1,
                          "el tramo más largo pulsada dentro de la zona")
                    e.add(f"{name} in {z}: shortest bout (s)", round(min(stretches), 2), 0.1, "")
                    e.add(f"{name} in {z}: mean bout (s)", round(float((pressed & m).sum()) / FPS / len(zp), 2), 0.1,
                          "tiempo pulsada en la zona / pulsaciones en la zona")
                rel = [ev["t_end"] for ev in evs if m[min(int(round(ev["t_end"] * FPS)), k.n - 1)]]
                if rel:
                    e.add(f"{name} in {z}: latency to first release (s)", round(min(rel), 2), tol, "")


def sequences(truth, e, app, k: Kin, memb, unit):
    """Secuencias (opciones por defecto: empezar en el primer paso, otras zonas permitidas entre pasos, completar al
    entrar en el último): intentos, completadas, errores (entrar en un paso fuera de orden termina el intento),
    latencias, duraciones, distancia y velocidad durante ellas."""
    for sq in app.sequences:
        steps = sq.steps
        enter = {s: set(np.flatnonzero(np.r_[memb[s][0], ~memb[s][:-1] & memb[s][1:]])) for s in steps}
        events = sorted((i, s) for s in steps for i in enter[s])
        attempts, done, errors = [], [], 0
        cur = None  # (paso esperado, inicio)
        for i, s in events:
            if cur is None:
                if s == steps[0]:
                    cur = (1, i)
                    attempts.append(i)
                continue
            expect, start = cur
            if s == steps[expect]:
                if expect + 1 == len(steps):
                    done.append((start, i))
                    cur = None
                else:
                    cur = (expect + 1, start)
            elif s == steps[0]:
                cur = (1, i)  # vuelve a empezar
                attempts.append(i)
            else:
                errors += 1
                cur = None
        n = sq.name
        e.add(f"{n}: completed", len(done), 0, "")
        e.add(f"{n}: attempts", len(attempts), 0, "")
        e.add(f"{n}: incomplete", len(attempts) - len(done), 0, "")
        e.add(f"{n}: errors", errors, 0, "entradas fuera de orden")
        if attempts:
            e.add(f"{n}: completion (%)", round(100 * len(done) / len(attempts), 2), 0.5, "")
        if done:
            durs = [(b - a) / FPS for a, b in done]
            dists = [float(k.step[a + 1:b + 1].sum()) for a, b in done]
            e.add(f"{n}: latency to first (s)", round(done[0][1] / FPS, 2), 0.25, "al completarla")
            e.add(f"{n}: latency to start of first (s)", round(done[0][0] / FPS, 2), 0.25, "entrada en el primer paso")
            e.add(f"{n}: first duration (s)", round(durs[0], 2), 0.3, "")
            e.add(f"{n}: mean duration (s)", round(sum(durs) / len(durs), 2), 0.3, "")
            e.add(f"{n}: min duration (s)", round(min(durs), 2), 0.3, "")
            gaps = [(done[j + 1][0] - done[j][1]) / FPS for j in range(len(done) - 1)]
            e.add(f"{n}: mean time between (s)", round(sum(gaps) / len(gaps), 2) if gaps else "", 0.3,
                  "entre el final de una y el inicio de la siguiente (vacío con una sola)")
            e.add(f"{n}: max duration (s)", round(max(durs), 2), 0.3, "")
            e.add(f"{n}: total time in sequences (s)", round(sum(durs), 2), 0.3, "")
            e.add(f"{n}: total distance in sequences ({unit})", round(sum(dists), 2), round(0.05 * sum(dists) + 0.5, 2), "")
            e.add(f"{n}: mean speed during sequences ({unit}/s)", round(sum(dists) / sum(durs), 3), 0.5, "")
            e.add(f"{n}: rate (/min)", round(len(done) / (k.dur / 60), 3), 0.02, "")


def grids(truth, e, app, k: Kin):
    """Rejillas: cruces (cambios de celda), celdas visitadas (n, %), latencia a visitarlas todas, % del tiempo en
    las celdas interiores."""
    for g in app.grids:
        cells = list(g.zones)
        shapes = {z.name: z.shape for z in app.zones if z.name in cells}
        idx = np.full(k.n, -1)
        for j, c in enumerate(cells):
            idx[np.asarray(shapes[c].contains(truth.pos[:, 0], truth.pos[:, 1]), bool)] = j
        seen = idx[idx >= 0]
        e.add(f"{g.name}: crossings", int(np.sum(seen[1:] != seen[:-1])), 0, "cambios de celda del centro")
        visited = len(set(seen.tolist()))
        e.add(f"{g.name}: cells visited", visited, 0, "")
        e.add(f"{g.name}: cells visited (%)", round(100 * visited / len(cells), 2), 0.1, "")
        if visited == len(cells):
            firsts = [first(idx == j) for j in range(len(cells))]
            e.add(f"{g.name}: latency to visit all cells (s)", round(max(firsts) / FPS, 2), 0.25, "")
        else:
            e.add(f"{g.name}: latency to visit all cells (s)", round(k.dur, 2), 0.05, "no las visita todas: la duración")
        side = int(round(math.sqrt(len(cells))))
        inner = [c for c in cells if side > 2 and c.split()[-1][0] not in "A" + chr(64 + side)
                 and c.split()[-1][1:] not in ("1", str(side))]
        if inner:
            m = np.isin(idx, [cells.index(c) for c in inner])
            e.add(f"{g.name}: inner cells time (%)", round(100 * float(m.mean()), 2), 1.5,
                  "celdas que no tocan el borde")


def hidden_measures(truth, e, app, k: Kin, memb, hidden):
    if hidden is None or not hidden.any():
        return
    e.add("Time hidden (s)", round(float(hidden.sum()) / FPS, 2), 0.2, "cuadros en que no se ve")
    e.add("Time not hidden (s)", round(float((~hidden).sum()) / FPS, 2), 0.2, "")
    e.add("Time not detected (s)", 0.0, 0.1, "escondido en la zona oculta no es «no detectado»")
    e.add("Detection (%)", round(100 * float((~hidden).mean()), 1), 1.0, "")
    for z in app.zones:
        if not z.hidden:
            continue
        hz = hidden & memb[z.name]
        e.add(f"{z.name}: time hidden (s)", round(float(hz.sum()) / FPS, 2), 0.2, "")
        # salidas parciales: visible entre dos tramos escondido, sin alejarse más de la distancia de la zona oculta
        part = 0
        hb = runs(hidden)
        for (a1, b1), (a2, _b2) in zip(hb, hb[1:]):
            seg = truth.pos[b1:a2]
            if len(seg) and all(near_zone(z, p) for p in seg):
                part += 1
        e.add(f"{z.name}: partial exits", part, 0, "visible entre dos ratos escondido, sin alejarse de la zona")
        tpart = sum((a2 - b1) for (a1, b1), (a2, _b2) in zip(hb, hb[1:])
                    if len(truth.pos[b1:a2]) and all(near_zone(z, p) for p in truth.pos[b1:a2])) / FPS
        e.add(f"{z.name}: time partially exited (s)", round(tpart, 2), 0.2, "")
    vis = ~hidden
    if vis.any():
        # la distancia por tramos visibles, suavizando dentro de cada uno (ni el suavizado ni la distancia cruzan el
        # rato escondido: no se sabe por dónde fue)
        from oraculo_medidas import ma

        dist = 0.0
        for a, b in runs(vis):
            sp = ma(truth.pos[a:b], max(1, int(round(0.2 * FPS))))
            dist += float(np.hypot(*np.diff(sp, axis=0).T).sum()) / (truth.px_per_cm or 1.0)
        e.add("Mean speed when not hidden (cm/s)", round(dist / (vis.sum() / FPS), 3), 0.5,
              "distancia a la vista (por tramos visibles) / tiempo a la vista")


def periods(truth, rows_out, animal, apparatus, defs, unit, Expect, memb_fn):
    """Filas por periodo: duración, distancia, tiempo / entradas / latencia por zona dentro de la ventana."""
    for label, a, b in defs:
        ia, ib = int(round(a * FPS)), min(int(round(b * FPS)), truth.n)
        if ib - ia < 2:
            continue
        e = Expect(animal, apparatus)
        k = Kin(truth.pos, truth.ang, truth.nose, truth.px_per_cm)
        e.add("Test duration (s)", round((ib - ia) / FPS, 2), 0.1, f"periodo {a:g}–{b:g} s")
        d = float(k.step[ia:ib].sum())  # el paso que llega a cada cuadro del periodo: los periodos suman el total
        e.add(f"Total distance ({unit})", round(d, 2), round(0.05 * d + 0.5, 2), "")
        memb = memb_fn()
        for z, m in memb.items():
            w = m[ia:ib]
            ent_in = int(np.sum(~w[:-1] & w[1:]))
            e.add(f"{z}: time (s)", round(float(w.sum()) / FPS, 2), round(0.12 * 2 * max(ent_in + int(w[0]), 1) + 0.2, 2), "")
            e.add(f"{z}: entries", ent_in + int(w[0]) if ia == 0 else f"{ent_in + int(w[0])}|{ent_in}", 0,
                  "dentro de la ventana; estar dentro al empezar cuenta (o no) como entrada")
            fi = first(w)
            fe = first(~w[:-1] & w[1:])
            lat_a = round(fi / FPS, 2) if fi is not None else round((ib - ia) / FPS, 2)
            lat_b = round((fe + 1) / FPS, 2) if fe is not None else round((ib - ia) / FPS, 2)
            if ia == 0:
                lat_b = lat_a  # el periodo empieza con la prueba: estar dentro es la entrada inicial
            e.add(f"{z}: latency to first entry (s)", lat_a if lat_a == lat_b else f"{lat_a}|{lat_b}", 0.25,
                  "desde el inicio del periodo; impl: la visita ya en curso no es una entrada (latencia «nunca»)")
        for r in e.rows:
            r["Period"] = label
        rows_out.extend(e.rows)


# ------------------------------------------------------------------------------------------------ varios animales
def social(e, a_pos, b_pos, ppc, body_px, other_label):
    """Distancia entre centros (media, mín, máx) y contacto (centros a ≤ un largo de cuerpo). La guía no define con
    qué largo (dibujado / rastreado, que incluye parte de la cola): el escenario deja los centros o muy cerca o muy
    lejos, y las dos definiciones dan casi lo mismo."""
    d = np.hypot(a_pos[:, 0] - b_pos[:, 0], a_pos[:, 1] - b_pos[:, 1]) / ppc
    tol = round(0.03 * d.max() + 0.3, 2)
    e.add(f"{other_label}: mean distance (cm)", round(float(d.mean()), 2), tol, "entre los centros")
    e.add(f"{other_label}: min distance (cm)", round(float(d.min()), 2), tol, "")
    e.add(f"{other_label}: max distance (cm)", round(float(d.max()), 2), tol, "")
    vals = []
    for L in (body_px, 1.35 * body_px):
        c = d <= L / ppc
        vals.append((round(float(c.sum()) / FPS, 2), len(runs(c))))
    e.add(f"{other_label}: time in contact (s)", f"{vals[0][0]}|{vals[1][0]}", 0.4,
          "centros a ≤ un largo de cuerpo (dibujado | rastreado ≈ 1,35×)")
    e.add(f"{other_label}: contacts", f"{vals[0][1]}|{vals[1][1]}", 0, "")


# ------------------------------------------------------------------------------------------------ entradas / salidas
def io_measures(e, events, duration, baseline_s=10.0, dev_sd=2.0):
    """Entradas digitales, salidas y entradas analógicas a partir del registro de eventos (guía, «I/O results»)."""
    by = {}
    for ev in sorted(events, key=lambda v: v["t"]):
        by.setdefault((ev["channel"], ev["kind"]), []).append(ev)
    for (ch, kind), evs in by.items():
        values = [float(v["value"]) for v in evs]
        analog = any(v not in (0.0, 1.0) for v in values)
        if analog:
            t = np.array([v["t"] for v in evs])
            x = np.array(values)
            e.add(f"{ch}: mean", round(float(x.mean()), 3), 0.01, "media simple de las muestras")
            e.add(f"{ch}: min", round(float(x.min()), 3), 0.001, "")
            e.add(f"{ch}: max", round(float(x.max()), 3), 0.001, "")
            e.add(f"{ch}: time of max (s)", round(float(t[np.argmax(x)]), 2), 0.01, "")
            e.add(f"{ch}: time of min (s)", round(float(t[np.argmin(x)]), 2), 0.01, "")
            base = x[t < baseline_s]
            b = float(base.mean())
            sd0, sd1 = float(base.std()), float(base.std(ddof=1)) if len(base) > 1 else 0.0
            e.add(f"{ch}: baseline", round(b, 3), 0.01, f"media de los primeros {baseline_s:g} s")
            e.add(f"{ch}: baseline SD", f"{sd1:.3f}|{sd0:.3f}", 0.002, "DE muestral (n−1) | poblacional (n)")
            after = t >= baseline_s
            e.add(f"{ch}: mean deviation from baseline", round(float(np.abs(x[after] - b).mean()), 3), 0.01, "")
            dt = np.diff(np.r_[t, duration])
            above = float(((x - b).clip(min=0) * dt)[after].sum())
            below = float(((b - x).clip(min=0) * dt)[after].sum())
            e.add(f"{ch}: integral above baseline", round(above, 2), 0.05, "valor×s, cada muestra hasta la siguiente")
            e.add(f"{ch}: integral below baseline", round(below, 2), 0.05, "")
            e.add(f"{ch}: end of baseline (s)", baseline_s, 0.01, "")
            for sign, word in ((1, "positive"), (-1, "negative")):
                vals = []
                for sd in (sd1, sd0):
                    dev = np.flatnonzero(after & (sign * (x - b) > dev_sd * sd))
                    if len(dev):
                        back = np.flatnonzero((np.arange(len(x)) > dev[0]) & (np.abs(x - b) <= dev_sd * sd))
                        vals.append((round(float(t[dev[0]]), 2), round(float(t[back[0]]), 2) if len(back) else ""))
                    else:
                        vals.append(("", ""))
                f_ = vals[0][0] if vals[0][0] == vals[1][0] else f"{vals[0][0]}|{vals[1][0]}"
                r_ = vals[0][1] if vals[0][1] == vals[1][1] else f"{vals[0][1]}|{vals[1][1]}"
                e.add(f"{ch}: first {word} deviation (s)", f_, 0.01, f"más de {dev_sd:g} DE de la línea base (DE n−1 | n)")
                e.add(f"{ch}: return to baseline after {word} deviation (s)", r_, 0.01, "de vuelta dentro de la banda")
            continue
        on = [v["t"] for i, v in enumerate(evs) if v["value"] and (i == 0 or not evs[i - 1]["value"])]
        off = [v["t"] for i, v in enumerate(evs) if not v["value"] and i > 0 and evs[i - 1]["value"]]
        durs = [b - a for a, b in zip(on, off)]
        mins = duration / 60
        if kind == "input":
            e.add(f"{ch}: activations", len(on), 0, "")
            e.add(f"{ch}: time on (s)", round(sum(durs), 3), 0.01, "")
            e.add(f"{ch}: latency to first activation (s)", round(on[0], 3) if on else round(duration, 2), 0.01, "")
            e.add(f"{ch}: activations per minute", round(len(on) / mins, 3), 0.01, "")
            if durs:
                e.add(f"{ch}: mean activation (s)", round(sum(durs) / len(durs), 3), 0.01, "tiempo / activaciones")
                e.add(f"{ch}: longest activation (s)", round(max(durs), 3), 0.01, "")
                e.add(f"{ch}: shortest activation (s)", round(min(durs), 3), 0.01, "")
                e.add(f"{ch}: latency to first deactivation (s)", round(off[0], 3), 0.01, "")
            e.add(f"{ch}: positive reversals", len(on), 0, "de apagada a encendida")
            e.add(f"{ch}: negative reversals", len(off), 0, "de encendida a apagada")
        else:
            e.add(f"{ch}: times on", len(on), 0, "")
            e.add(f"{ch}: time on (s)", round(sum(durs), 3), 0.01, "")
            e.add(f"{ch}: latency to first on (s)", round(on[0], 3) if on else round(duration, 2), 0.01, "")
            e.add(f"{ch}: activations per minute", round(len(on) / mins, 3), 0.01, "")
            if durs:
                e.add(f"{ch}: mean on (s)", round(sum(durs) / len(durs), 3), 0.01, "")
                e.add(f"{ch}: longest on (s)", round(max(durs), 3), 0.01, "")
                e.add(f"{ch}: shortest on (s)", round(min(durs), 3), 0.01, "")
                e.add(f"{ch}: latency to first off (s)", round(off[0], 3), 0.01, "")
