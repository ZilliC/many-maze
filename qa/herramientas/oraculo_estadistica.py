"""Oráculo de estadística: un experimento con diseño conocido y los resultados ESPERADOS de cada prueba de
Results ▸ Statistics, calculados con scipy y con fórmulas de libro (sin el módulo de estadística de la app).

    .venv/bin/python qa/herramientas/oraculo_estadistica.py generar DEST     # DEST/estadistica.mmaze + esperado

Diseño: 3 tratamientos (Control, HFD, Fármaco) × 2 sexos × 4 animales = 24 animales, 3 etapas (Día 1–3) = 72
pruebas de campo abierto sintéticas de 12 s, rastreadas. Cada tratamiento se mueve a otra velocidad, así que
*Total distance (cm)* difiere entre grupos; *Centre: time (%)* varía sin patrón.

Crea DEST/estadistica.mmaze (rastreado), DEST/estadistica_resultados.csv (la tabla de la que salen los números) y
DEST/ESPERADO_ESTADISTICA.md: para cada vista y cada prueba, qué elegir en la página y qué valores deben salir.
Los p-valores se comparan con 3 cifras significativas; los estadísticos con 3 decimales.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from manymaze.core import synthetic as syn  # noqa: E402
from manymaze.core import templates  # noqa: E402
from manymaze.core.measures import AnalysisSettings  # noqa: E402
from manymaze.core.project import Group, Project  # noqa: E402

GROUPS = [("Control", "#3b82f6", 2.6), ("HFD", "#ef4444", 3.4), ("Fármaco", "#10b981", 4.2)]  # px por cuadro
SEXES = ["Male", "Female"]
STAGES = ["Día 1", "Día 2", "Día 3"]
PER_CELL = 4
SECONDS, FPS = 12.0, 25.0
M1, M2 = "Total distance (cm)", "Centre: time (%)"


# ------------------------------------------------------------------------------------------------ experimento
def build(dest: Path) -> Project:
    path = dest / "estadistica.mmaze"
    p = Project(name="Oráculo estadística", protocol="open_field", test_duration_s=SECONDS)
    p.analysis = AnalysisSettings.for_new_experiment()
    p.path = path
    (path / "videos").mkdir(parents=True, exist_ok=True)
    app = templates.build("open_field", 50, 50, 300, 300, size_cm=40)
    app.frame_size = (400, 400)
    p.apparatus.append(app)
    p.groups = [Group(g, c) for g, c, _ in GROUPS]
    p.stages = list(STAGES)
    if "Genotipo" not in p.animal_fields:  # so the app offers it (Categorical, factors)
        p.animal_fields.append("Genotipo")
    n = int(SECONDS * FPS)
    k = 0
    for gi, (g, _, speed) in enumerate(GROUPS):
        for sx in SEXES:
            for i in range(PER_CELL):
                aid = f"{g[0]}{'M' if sx == 'Male' else 'F'}{i + 1}"
                a = p.ensure_animal(aid, g)
                a.sex = sx
                # campo categórico desequilibrado entre tratamientos, para la vista Categorical
                a.fields["Genotipo"] = "KO" if i < (1, 2, 3)[gi] + (sx == "Female") else "WT"
                for si, st in enumerate(STAGES):
                    seed = 1000 * gi + 100 * (sx == "Female") + 10 * i + si
                    sp = speed * (1 + 0.08 * si) * (1.1 if sx == "Female" else 1.0)
                    pos = syn.random_walk(n, (50, 50, 350, 350), speed=sp, seed=seed, pause_prob=0.004)
                    vp = path / "videos" / f"{aid}_{si + 1}.avi"
                    syn.make_video(vp, pos, size=(400, 400), fps=FPS, arena=("rect", 50, 50, 300, 300), seed=seed)
                    p.add_test(str(vp), aid, app.name, stage=st, trial=1)
                    k += 1
    p.save()
    for t in p.tests:
        p.track_test(t)
    p.save()
    return p


# ------------------------------------------------------------------------------------------------ fórmulas de libro
def welch_anova(groups):
    k = len(groups)
    n = np.array([len(g) for g in groups], float)
    m = np.array([np.mean(g) for g in groups])
    v = np.array([np.var(g, ddof=1) for g in groups])
    w = n / v
    mw = np.sum(w * m) / np.sum(w)
    a = np.sum(w * (m - mw) ** 2) / (k - 1)
    tmp = np.sum((1 - w / np.sum(w)) ** 2 / (n - 1))
    b = 1 + 2 * (k - 2) / (k ** 2 - 1) * tmp
    f = a / b
    df2 = (k ** 2 - 1) / (3 * tmp)
    return f, k - 1, df2, sps.f.sf(f, k - 1, df2)


def rm_anova(Y):
    """Y: sujetos × condiciones."""
    n, k = Y.shape
    gm = Y.mean()
    ss_cond = n * np.sum((Y.mean(0) - gm) ** 2)
    ss_subj = k * np.sum((Y.mean(1) - gm) ** 2)
    ss_tot = np.sum((Y - gm) ** 2)
    ss_err = ss_tot - ss_cond - ss_subj
    df1, df2 = k - 1, (k - 1) * (n - 1)
    f = (ss_cond / df1) / (ss_err / df2)
    return f, df1, df2, sps.f.sf(f, df1, df2)


def two_way_balanced(y, a, b):
    """ANOVA de dos factores con diseño equilibrado (tipo I = II = III)."""
    y, a, b = np.asarray(y, float), np.asarray(a), np.asarray(b)
    la, lb = sorted(set(a)), sorted(set(b))
    gm = y.mean()
    n = len(y) / (len(la) * len(lb))
    ma = {x: y[a == x].mean() for x in la}
    mb = {x: y[b == x].mean() for x in lb}
    mab = {(i, j): y[(a == i) & (b == j)].mean() for i in la for j in lb}
    ss_a = n * len(lb) * sum((ma[i] - gm) ** 2 for i in la)
    ss_b = n * len(la) * sum((mb[j] - gm) ** 2 for j in lb)
    ss_ab = n * sum((mab[(i, j)] - ma[i] - mb[j] + gm) ** 2 for i in la for j in lb)
    ss_e = sum(((y[(a == i) & (b == j)] - mab[(i, j)]) ** 2).sum() for i in la for j in lb)
    df_a, df_b = len(la) - 1, len(lb) - 1
    df_ab, df_e = df_a * df_b, len(y) - len(la) * len(lb)
    mse = ss_e / df_e
    out = {}
    for name, ss, df in (("A", ss_a, df_a), ("B", ss_b, df_b), ("A×B", ss_ab, df_ab)):
        f = (ss / df) / mse
        out[name] = (f, df, df_e, sps.f.sf(f, df, df_e))
    return out


def adjust(ps, method):
    p = np.asarray(ps, float)
    m = len(p)
    if method == "bonferroni":
        return np.minimum(1, p * m)
    if method == "sidak":
        return 1 - (1 - p) ** m
    if method == "holm":
        o = np.argsort(p)
        adj = np.maximum.accumulate((m - np.arange(m)) * p[o])
        r = np.empty(m)
        r[o] = np.minimum(1, adj)
        return r
    if method == "fdr":
        return sps.false_discovery_control(p)
    raise ValueError(method)


def mse_of(groups):
    ss = sum(((g - g.mean()) ** 2).sum() for g in groups)
    df = sum(len(g) for g in groups) - len(groups)
    return ss / df, df


def games_howell(a, b, k):
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    q = abs(a.mean() - b.mean()) / math.sqrt((va + vb) / 2)
    return sps.studentized_range.sf(q, k, df)


def dunnett_exact(G, control=0):
    """Dunnett bilateral con n iguales: p = 1 − P(max|T_j| < |t|), T multivariante t con correlación ρ = n/(n+n)
    = 0,5, por integración numérica determinista (scipy.stats.dunnett usa Monte Carlo y varía con p pequeños)."""
    from scipy import integrate
    mse, df = mse_of(G)
    others = [i for i in range(len(G)) if i != control]
    m = len(others)
    rho = 0.5

    def p_inside(c):
        def given_s(s):
            def f(w):
                a = (c * s - math.sqrt(rho) * w) / math.sqrt(1 - rho)
                b = (-c * s - math.sqrt(rho) * w) / math.sqrt(1 - rho)
                return sps.norm.pdf(w) * (sps.norm.cdf(a) - sps.norm.cdf(b)) ** m
            return integrate.quad(f, -9, 9, epsabs=1e-12, limit=200)[0]
        # s = sqrt(χ²_df / df)
        dens = lambda s: 2 * df * s * sps.chi2.pdf(df * s * s, df)  # noqa: E731
        return integrate.quad(lambda s: dens(s) * given_s(s), 0, 6, epsabs=1e-12, limit=200)[0]

    out = []
    for i in others:
        t = (G[i].mean() - G[control].mean()) / math.sqrt(mse * (1 / len(G[i]) + 1 / len(G[control])))
        out.append(1 - p_inside(abs(t)))
    return out


def snk_duncan(G):
    """SNK y Duncan con el término de error del ANOVA: medias ordenadas, r = pasos entre ellas + 1;
    SNK p = P(q_r > q), Duncan p = 1 − (1 − P(q_r > q))^(1/(r−1))."""
    mse, df = mse_of(G)
    order = sorted(range(len(G)), key=lambda i: G[i].mean())
    rank = {g: k for k, g in enumerate(order)}
    snk, dun = [], []
    for i, j in itertools.combinations(range(len(G)), 2):
        r = abs(rank[i] - rank[j]) + 1
        q = abs(G[i].mean() - G[j].mean()) / math.sqrt(mse / 2 * (1 / len(G[i]) + 1 / len(G[j])))
        pq = sps.studentized_range.sf(q, r, df)
        snk.append(pq)
        dun.append(1 - (1 - pq) ** (1 / (r - 1)))
    return snk, dun


def dunn(groups):
    allv = np.concatenate(groups)
    r = sps.rankdata(allv)
    N = len(allv)
    _, c = np.unique(allv, return_counts=True)
    tie = (c ** 3 - c).sum() / (12 * (N - 1))
    pos = np.cumsum([0] + [len(g) for g in groups])
    mr = [r[pos[i]:pos[i + 1]].mean() for i in range(len(groups))]
    out = []
    for i, j in itertools.combinations(range(len(groups)), 2):
        se = math.sqrt((N * (N + 1) / 12 - tie) * (1 / len(groups[i]) + 1 / len(groups[j])))
        out.append(2 * sps.norm.sf(abs((mr[i] - mr[j]) / se)))
    return out


# ------------------------------------------------------------------------------------------------ esperado
def fmt(x, nd=4):
    if isinstance(x, str):
        return x
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if abs(x) < 1e-3 and x != 0:
        return f"{x:.3e}"
    return f"{x:.{nd}f}"


def expected(rows: list[dict]) -> str:
    def col(m, filt=lambda r: True):
        return [r for r in rows if filt(r)]

    d1 = [r for r in rows if r["Stage"] == "Día 1"]
    by_g = {g: np.array([float(r[M1]) for r in d1 if r["Group"] == g]) for g, _, _ in GROUPS}
    by_s = {s: np.array([float(r[M1]) for r in d1 if r["Sex"] == s]) for s in SEXES}
    gnames = [g for g, _, _ in GROUPS]
    G = [by_g[g] for g in gnames]
    L = ["# Valores esperados — Results ▸ Statistics (oráculo)", "",
         "Experimento: `estadistica.mmaze`. Salvo que se diga otra cosa: **periodo = toda la prueba**, filtro "
         "**Only: Stage = Día 1** (24 animales), α = 0,05. Compara estadístico, gl y p. Una diferencia en la "
         "4.ª cifra decimal es redondeo; más que eso es un hallazgo (anota ambos valores).", ""]

    def t(title, rows_):
        L.extend([f"### {title}", "", "| Qué | Esperado |", "|---|---|"])
        L.extend(f"| {a} | {b} |" for a, b in rows_)
        L.append("")

    # descriptivos
    t(f"E01 Descriptivos de «{M1}» por Group (Día 1)",
      [(g, f"n={len(v)} media={fmt(v.mean())} DE={fmt(v.std(ddof=1))} EEM={fmt(sps.sem(v))} "
           f"mediana={fmt(float(np.median(v)))}") for g, v in by_g.items()])
    # dos grupos: factor Sex
    a, b = by_s["Male"], by_s["Female"]
    st = sps.ttest_ind(a, b)
    we = sps.ttest_ind(a, b, equal_var=False)
    mw = sps.mannwhitneyu(a, b, alternative="two-sided")
    ks = sps.ks_2samp(a, b)
    bm = sps.brunnermunzel(a, b)
    sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    t(f"E02 Dos grupos: «{M1}», factor **Sex**, Día 1 (Male n={len(a)}, Female n={len(b)})", [
        ("Student's t-test", f"t={fmt(st.statistic)} gl={len(a) + len(b) - 2} p={fmt(st.pvalue)}"),
        ("Welch's t-test (Automatic, paramétrico)", f"t={fmt(we.statistic)} gl={fmt(we.df, 2)} p={fmt(we.pvalue)}"),
        ("Mann-Whitney U (Automatic, no paramétrico)", f"U={fmt(mw.statistic, 1)} p={fmt(mw.pvalue)} "
                                                       "(exacto o normal según la app: anota cuál)"),
        ("Kolmogorov-Smirnov", f"D={fmt(ks.statistic)} p={fmt(ks.pvalue)}"),
        ("Brunner-Munzel", f"W={fmt(bm.statistic)} p={fmt(bm.pvalue)}"),
        ("Cohen's d (DE combinada)", fmt((a.mean() - b.mean()) / sp)),
    ])
    # pareadas: Día 1 vs Día 2 por animal (todos)
    ids = sorted({r["Animal"] for r in rows})
    val = {(r["Animal"], r["Stage"]): float(r[M1]) for r in rows}
    Y = np.array([[val[(i, s)] for s in STAGES] for i in ids])
    pt = sps.ttest_rel(Y[:, 0], Y[:, 1])
    wx = sps.wilcoxon(Y[:, 0], Y[:, 1])
    t(f"E03 Medidas repetidas: «{M1}», factor **Stage**, *Repeated measures* activado, sin filtro (24 animales)", [
        ("Paired t-test (solo Día 1 y Día 2: filtra Stage ≠ Día 3)", f"t={fmt(pt.statistic)} gl={len(ids) - 1} "
                                                                    f"p={fmt(pt.pvalue)}"),
        ("Wilcoxon signed-rank (Día 1 vs Día 2)", f"W={fmt(wx.statistic, 1)} p={fmt(wx.pvalue)}"),
        ("Repeated-measures ANOVA (3 etapas)", "F={} gl=({}, {}) p={}".format(
            *(fmt(x) if isinstance(x, float) else x for x in rm_anova(Y)))),
        ("Friedman (3 etapas)", "χ²={} p={}".format(*map(fmt, sps.friedmanchisquare(*Y.T)))),
    ])
    # una muestra
    allv = np.concatenate(G)
    os_ = sps.ttest_1samp(allv, 200.0)
    ow = sps.wilcoxon(allv - 200.0)
    t(f"E04 Una muestra: «{M1}» vs *Test value* = 200, Día 1, todos juntos (o por grupo: calcula cada uno)", [
        ("One-sample t-test (todos)", f"t={fmt(os_.statistic)} gl={len(allv) - 1} p={fmt(os_.pvalue)}"),
        ("Wilcoxon signed-rank vs value (todos)", f"W={fmt(ow.statistic, 1)} p={fmt(ow.pvalue)}"),
        *[(f"One-sample t-test {g}", "t={} p={}".format(*map(fmt, sps.ttest_1samp(v, 200.0)))) for g, v in
          by_g.items()],
    ])
    # varios grupos
    fa = sps.f_oneway(*G)
    wa = welch_anova(G)
    ag = sps.alexandergovern(*G)
    kw = sps.kruskal(*G)
    md = sps.median_test(*G)
    n_tot = sum(len(g) for g in G)
    t(f"E05 Varios grupos: «{M1}», factor **Group** (3 niveles), Día 1", [
        ("One-way ANOVA (Automatic, paramétrico)", f"F={fmt(fa.statistic)} gl=(2, {n_tot - 3}) p={fmt(fa.pvalue)}"),
        ("Welch's ANOVA", "F={} gl=({}, {}) p={}".format(fmt(wa[0]), wa[1], fmt(wa[2], 2), fmt(wa[3]))),
        ("Alexander-Govern", f"A={fmt(ag.statistic)} p={fmt(ag.pvalue)}"),
        ("Kruskal-Wallis (Automatic, no paramétrico)", f"H={fmt(kw.statistic)} gl=2 p={fmt(kw.pvalue)}"),
        ("Mood's median test", f"χ²={fmt(md.statistic)} p={fmt(md.pvalue)}"),
        ("η² (eta cuadrado) del ANOVA", fmt(fa.statistic * 2 / (fa.statistic * 2 + n_tot - 3))),
    ])
    # post-hoc
    pairs = list(itertools.combinations(range(3), 2))
    tk = sps.tukey_hsd(*G)
    dn = dunnett_exact(G)
    snk, dun_ = snk_duncan(G)
    mse, dfe = mse_of(G)
    ph = []
    ph.append(("Tukey", [tk.pvalue[i, j] for i, j in pairs]))
    raw_pooled = [sps.ttest_ind(G[i], G[j]).pvalue for i, j in pairs]
    raw_welch = [sps.ttest_ind(G[i], G[j], equal_var=False).pvalue for i, j in pairs]
    raw_mw = [sps.mannwhitneyu(G[i], G[j], alternative="two-sided").pvalue for i, j in pairs]
    for mname in ("bonferroni", "holm", "sidak", "fdr"):
        ph.append((f"{mname} (t de Student por pares)", list(adjust(raw_pooled, mname))))
        ph.append((f"{mname} (t de Welch por pares; alternativa)", list(adjust(raw_welch, mname))))
    ph.append(("Bonferroni (Mann-Whitney por pares; Automatic no paramétrico)", list(adjust(raw_mw, "bonferroni"))))
    ph.append(("Games-Howell", [games_howell(G[i], G[j], 3) for i, j in pairs]))
    ph.append(("Dunn (Bonferroni)", list(adjust(dunn(G), "bonferroni"))))
    lsd = [2 * sps.t.sf(abs(G[i].mean() - G[j].mean()) / math.sqrt(mse * (1 / len(G[i]) + 1 / len(G[j]))), dfe)
           for i, j in pairs]
    ph.append(("Fisher's LSD", lsd))
    sch = [sps.f.sf((G[i].mean() - G[j].mean()) ** 2 / (mse * (1 / len(G[i]) + 1 / len(G[j]))) / 2, 2, dfe)
           for i, j in pairs]
    ph.append(("Scheffé", sch))
    ph.append(("Student-Newman-Keuls", snk))
    ph.append(("Duncan", dun_))
    head = " · ".join(f"{gnames[i]}–{gnames[j]}" for i, j in pairs)
    t(f"E06 Post-hoc de «{M1}» por Group, Día 1 — p de cada par ({head})",
      [(n_, " · ".join(fmt(float(x)) for x in v)) for n_, v in ph] +
      [("Dunnett (control = Control): Control–HFD · Control–Fármaco", " · ".join(fmt(float(x)) for x in dn))])
    # dos factores
    yy = np.array([float(r[M1]) for r in d1])
    aa = np.array([r["Group"] for r in d1])
    bb = np.array([r["Sex"] for r in d1])
    tw = two_way_balanced(yy, aa, bb)
    t(f"E07 Dos factores: «{M1}», 1.º **Group**, 2.º **Sex**, diseño entre sujetos, Día 1 (equilibrado: 4 por celda)",
      [(f"{'Group' if k == 'A' else 'Sex' if k == 'B' else 'Group × Sex'}",
        f"F={fmt(v[0])} gl=({v[1]}, {v[2]}) p={fmt(v[3])}") for k, v in tw.items()] +
      [("Mixed two-way ANOVA (Group entre, Stage dentro)", "comprueba gl: Group (2, 21), Stage (2, 42), "
                                                          "Group×Stage (4, 42)"),
       ("Scheirer-Ray-Hare / ART / ANCOVA", "sin oráculo: deben correr sin error y dar gl coherentes")])
    # correlación
    x = np.array([float(r[M1]) for r in d1])
    y = np.array([float(r[M2]) for r in d1])
    pr, sr, kt, lr = sps.pearsonr(x, y), sps.spearmanr(x, y), sps.kendalltau(x, y), sps.linregress(x, y)
    t(f"E08 Correlación: X = «{M1}», Y = «{M2}», Día 1 (n=24)", [
        ("Pearson", f"r={fmt(pr.statistic)} p={fmt(pr.pvalue)}"),
        ("Spearman", f"ρ={fmt(sr.statistic)} p={fmt(sr.pvalue)}"),
        ("Kendall", f"τ={fmt(kt.statistic)} p={fmt(kt.pvalue)}"),
        ("Linear regression", f"pendiente={fmt(lr.slope)} ordenada={fmt(lr.intercept)} R²={fmt(lr.rvalue ** 2)} "
                              f"p={fmt(lr.pvalue)}"),
    ])
    # supuestos
    t(f"E09 Supuestos de «{M1}» por Group, Día 1", [
        *[(f"Shapiro-Wilk {g}", "W={} p={}".format(*map(fmt, sps.shapiro(v)))) for g, v in by_g.items()],
        ("D'Agostino-Pearson (todos, n=24)", "K²={} p={}".format(*map(fmt, sps.normaltest(allv)))),
        ("Levene (centro = media)", "W={} p={}".format(*map(fmt, sps.levene(*G, center="mean")))),
        ("Brown-Forsythe (Levene con mediana)", "W={} p={}".format(*map(fmt, sps.levene(*G, center="median")))),
        ("Bartlett", "T={} p={}".format(*map(fmt, sps.bartlett(*G)))),
        ("Fligner-Killeen", "X²={} p={}".format(*map(fmt, sps.fligner(*G)))),
    ])
    # categórica: genotipo × Group
    cat = "Genotipo"
    if cat in rows[0]:
        levels = sorted({r[cat] for r in d1})
        table = np.array([[sum(1 for r in d1 if r["Group"] == g and r[cat] == lv) for lv in levels] for g in gnames])
        rows_ = [("Tabla (filas Group, columnas " + " / ".join(levels) + ")", "; ".join(
            f"{g}: " + ", ".join(map(str, row)) for g, row in zip(gnames, table)))]
        keep = table[:, table.sum(0) > 0]
        if keep.shape[1] > 1:
            c2 = sps.chi2_contingency(keep, correction=False)
            g2 = sps.chi2_contingency(keep, correction=False, lambda_="log-likelihood")
            rows_ += [("Chi-square test of independence", f"χ²={fmt(c2[0])} gl={c2[2]} p={fmt(c2[1])}"),
                      ("G-test", f"G={fmt(g2[0])} gl={g2[2]} p={fmt(g2[1])}")]
        tot = keep.sum(0)
        if len(tot) > 1:
            gof = sps.chisquare(tot)
            rows_.append(("Chi-square goodness of fit (frecuencias iguales)", f"χ²={fmt(gof.statistic)} "
                                                                              f"p={fmt(gof.pvalue)}"))
        if keep.shape == (3, 2):
            pass
        t(f"E10 Categórica: resultado «{cat}» (campo del animal), agrupar por Group, Día 1", rows_)
    L.append("Notas: si la app da otro valor y coincide con una variante conocida (p. ej. Mann-Whitney exacto frente a "
             "la aproximación normal, Welch frente a Student en los post-hoc por pares), anótalo como Observación "
             "con la variante; si no coincide con ninguna, hallazgo S1.")
    return "\n".join(L) + "\n"


def generate(dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    p = build(dest)
    rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in p.results()]
    if "Genotipo" not in rows[0]:  # por si la tabla no trae los campos del animal
        geno = {a.id: a.fields.get("Genotipo", "") for a in p.animals}
        for r in rows:
            r["Genotipo"] = geno.get(r["Animal"], "")
    with (dest / "estadistica_resultados.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (dest / "ESPERADO_ESTADISTICA.md").write_text(expected(rows), encoding="utf-8")
    print(f"listo: {p.path} y {dest / 'ESPERADO_ESTADISTICA.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generar")
    g.add_argument("dest", type=Path)
    a = ap.parse_args()
    generate(a.dest)


if __name__ == "__main__":
    main()
