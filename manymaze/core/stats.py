"""Statistics for results tables: descriptive statistics, group comparisons, post-hoc tests, repeated-measures and
two-factor ANOVAs, non-parametric alternatives, categorical tests, correlation / regression and assumption checks.

Everything is implemented on NumPy / SciPy; `TESTS` lists the supported procedures.
"""

from __future__ import annotations

import functools
import itertools
import math
import warnings
from collections import OrderedDict

import numpy as np
from scipy import stats as sps

# name -> category (the catalogue shown in the documentation)
TESTS = OrderedDict([
    ("Student's t-test", "two groups"), ("Welch's t-test", "two groups"), ("Paired t-test", "two groups"),
    ("Mann-Whitney U", "two groups"), ("Wilcoxon signed-rank", "two groups"),
    ("Kolmogorov-Smirnov (two-sample)", "two groups"), ("Brunner-Munzel", "two groups"),
    ("One-sample t-test", "one sample"), ("Wilcoxon signed-rank vs value", "one sample"),
    ("One-way ANOVA", "several groups"), ("Welch's ANOVA", "several groups"), ("Alexander-Govern", "several groups"),
    ("Kruskal-Wallis", "several groups"), ("Mood's median test", "several groups"),
    ("Repeated-measures ANOVA", "repeated measures"), ("Friedman", "repeated measures"),
    ("Two-way ANOVA", "two factors"), ("Mixed two-way ANOVA", "two factors"),
    ("Scheirer-Ray-Hare", "two factors"), ("Aligned rank transform ANOVA", "two factors"),
    ("ANCOVA (analysis of covariance)", "two factors"),
    ("Tukey HSD", "post-hoc"), ("Bonferroni", "post-hoc"), ("Holm", "post-hoc"), ("Šidák", "post-hoc"),
    ("Benjamini-Hochberg (FDR)", "post-hoc"), ("Dunnett (vs control)", "post-hoc"), ("Games-Howell", "post-hoc"),
    ("Dunn", "post-hoc"), ("Duncan's multiple range test", "post-hoc"), ("Fisher's LSD", "post-hoc"),
    ("Scheffé's test", "post-hoc"), ("Student-Newman-Keuls", "post-hoc"),
    ("Chi-square test of independence", "categorical"), ("Fisher's exact test", "categorical"),
    ("G-test (log-likelihood ratio)", "categorical"), ("Chi-square goodness of fit", "categorical"),
    ("Pearson correlation", "correlation"), ("Spearman correlation", "correlation"),
    ("Kendall's tau", "correlation"), ("Linear regression", "correlation"),
    ("Shapiro-Wilk", "assumptions"), ("D'Agostino-Pearson", "assumptions"), ("Levene", "assumptions"),
    ("Brown-Forsythe", "assumptions"), ("Bartlett", "assumptions"), ("Fligner-Killeen", "assumptions"),
])

# compare_groups(method=...) choices
METHODS_TWO = OrderedDict([("auto", "Automatic"), ("student", "Student's t-test"), ("welch", "Welch's t-test"),
                           ("mannwhitney", "Mann-Whitney U"), ("ks", "Kolmogorov-Smirnov"),
                           ("brunnermunzel", "Brunner-Munzel"), ("paired_t", "Paired t-test"),
                           ("wilcoxon", "Wilcoxon signed-rank")])
METHODS_K = OrderedDict([("auto", "Automatic"), ("anova", "One-way ANOVA"), ("welch_anova", "Welch's ANOVA"),
                         ("alexander_govern", "Alexander-Govern"), ("kruskal", "Kruskal-Wallis"),
                         ("median", "Mood's median test"), ("rm_anova", "Repeated-measures ANOVA"),
                         ("friedman", "Friedman")])
POSTHOC = OrderedDict([("auto", "Automatic"), ("none", "None"), ("bonferroni", "Bonferroni test"),
                       ("duncan", "Duncan's test"), ("lsd", "Fisher's LSD test"), ("scheffe", "Scheffé's test"),
                       ("sidak", "Šidák test"), ("snk", "Student-Newman-Keuls test"), ("tukey", "Tukey test"),
                       ("holm", "Holm test"), ("fdr", "Benjamini-Hochberg (FDR)"),
                       ("dunnett", "Dunnett test (vs control)"), ("games_howell", "Games-Howell test"),
                       ("dunn", "Dunn's test")])
# post-hoc tests based on the ANOVA error term (mean square error), as in ANY-maze / SPSS / agricolae
ERROR_TERM_POSTHOC = {"lsd": "Fisher's LSD", "scheffe": "Scheffé", "snk": "Student-Newman-Keuls",
                      "duncan": "Duncan"}
_TWO_TO_K = {"student": "anova", "welch": "welch_anova", "mannwhitney": "kruskal", "ks": "kruskal",
             "brunnermunzel": "kruskal", "paired_t": "rm_anova", "wilcoxon": "friedman"}
PAIRED_METHODS = {"paired_t", "wilcoxon", "rm_anova", "friedman"}
NONPARAMETRIC = {"mannwhitney", "ks", "brunnermunzel", "wilcoxon", "kruskal", "median", "friedman"}


def _quiet(fn):
    """Silence SciPy's runtime warnings on degenerate data (constant groups etc.); results are NaN instead."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)
            warnings.filterwarnings("ignore", message=".*(constant|identical|exact).*")
            return fn(*a, **kw)
    return wrapper


def _clean(v) -> np.ndarray:
    a = np.asarray([x for x in v if isinstance(x, (int, float, np.number)) and not isinstance(x, bool)], float)
    return a[np.isfinite(a)]


def _clean_rows(groups: list) -> list[np.ndarray]:
    """Paired data (matched by position): only the rows where every group has a finite number, so that dropping a
    missing value does not shift the pairing."""
    cols = [np.asarray([float(x) if is_number(x) else math.nan for x in v], float) for v in groups]
    n = min((len(c) for c in cols), default=0)
    ok = np.ones(n, bool)
    for c in cols:
        ok &= np.isfinite(c[:n])
    return [c[:n][ok] for c in cols]


def is_number(v) -> bool:
    """A numeric value (not a bool); may be NaN or infinite."""
    return isinstance(v, (int, float, np.number)) and not isinstance(v, (bool, np.bool_))


def _num(v) -> bool:
    return is_number(v) and math.isfinite(float(v))


def numeric_columns(rows: list[dict], cols: list[str]) -> list[str]:
    """The columns holding a number in at least one row."""
    return [c for c in cols if any(is_number(r.get(c)) for r in rows)]


def descriptive(values) -> dict:
    a = _clean(values)
    n = len(a)
    if n == 0:
        return {"n": 0, "mean": math.nan, "sd": math.nan, "sem": math.nan, "ci95": math.nan, "median": math.nan,
                "min": math.nan, "max": math.nan, "q1": math.nan, "q3": math.nan}
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    sem = sd / math.sqrt(n) if n > 1 else math.nan
    ci = float(sps.t.ppf(0.975, n - 1) * sem) if n > 1 else math.nan
    return {"n": n, "mean": float(a.mean()), "sd": sd, "sem": sem, "ci95": ci,
            "median": float(np.median(a)), "min": float(a.min()), "max": float(a.max()),
            "q1": float(np.percentile(a, 25)), "q3": float(np.percentile(a, 75))}


def error_value(values, kind: str = "sem") -> float:
    """Half-width of an error bar: "sem", "sd" or "ci" (95 % confidence interval of the mean)."""
    d = descriptive(values)
    return d["ci95"] if kind == "ci" else d["sd"] if kind == "sd" else d["sem"]


def group_values(rows: list[dict], measure: str, by: str = "Group") -> "OrderedDict[str, np.ndarray]":
    out: OrderedDict[str, list] = OrderedDict()
    for r in rows:
        key = str(r.get(by, ""))
        v = r.get(measure)
        if _num(v):
            out.setdefault(key, []).append(float(v))
    return OrderedDict((k, np.asarray(v)) for k, v in out.items())


def describe_by(rows: list[dict], measure: str, factors: list[str], orders: dict | None = None) -> list[dict]:
    """Descriptive statistics of a measure for every combination of up to three grouping factors."""
    factors = [f for f in factors if f][:3]
    cells: OrderedDict[tuple, list] = OrderedDict()
    for r in rows:
        key = tuple(str(r.get(f, "")) for f in factors)
        cells.setdefault(key, [])
        if _num(r.get(measure)):
            cells[key].append(float(r[measure]))
    keys = list(cells)
    if orders:
        def sk(k):
            out = []
            for f, v in zip(factors, k):
                o = [str(x) for x in orders.get(f, [])]
                out.append((o.index(v) if v in o else len(o), v))
            return out
        keys.sort(key=sk)
    out = []
    for k in keys:
        d = descriptive(cells[k])
        out.append({**dict(zip(factors, k)), **d})
    return out


# ---------------------------------------------------------------- multiple comparisons
def p_adjust(pvals, method: str = "holm") -> list[float]:
    """Adjust p-values: bonferroni, holm, sidak, fdr (Benjamini-Hochberg) or none."""
    p = np.asarray(pvals, float)
    if len(p) == 0 or method in (None, "none"):
        return p.tolist()
    fin = np.isfinite(p)
    if not fin.all():  # NaN p-values stay NaN and do not count in m (nor poison the others)
        res = np.full(len(p), np.nan)
        res[fin] = p_adjust(p[fin], method)
        return res.tolist()
    m = len(p)
    if method == "bonferroni":
        out = np.minimum(1.0, p * m)
    elif method == "sidak":
        out = 1.0 - (1.0 - p) ** m
    elif method == "holm":
        order = np.argsort(p)
        adj = np.maximum.accumulate((m - np.arange(m)) * p[order])
        out = np.empty(m)
        out[order] = np.minimum(1.0, adj)
    elif method in ("fdr", "bh"):
        order = np.argsort(p)[::-1]
        ranks = m - np.arange(m)
        adj = np.minimum.accumulate(p[order] * m / ranks)
        out = np.empty(m)
        out[order] = np.minimum(1.0, adj)
    else:
        raise ValueError(f"Unknown adjustment {method!r}")
    return out.tolist()


def _games_howell(names, data):
    out = []
    k = len(data)
    for i, j in itertools.combinations(range(k), 2):
        a, b = data[i], data[j]
        va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
        se = math.sqrt(va + vb)
        diff = float(a.mean() - b.mean())
        if se == 0:
            p = 1.0 if diff == 0 else 0.0
        else:
            df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
            p = float(sps.studentized_range.sf(abs(diff) / se * math.sqrt(2), k, df))
        out.append({"a": names[i], "b": names[j], "diff": diff, "p": min(1.0, p), "test": "Games-Howell"})
    return out


def _dunn(names, data, adjust="bonferroni"):
    allv = np.concatenate(data)
    ranks = sps.rankdata(allv)
    N = len(allv)
    _, counts = np.unique(allv, return_counts=True)
    tie = (counts ** 3 - counts).sum() / (12 * (N - 1)) if N > 1 else 0
    pos = np.cumsum([0] + [len(d) for d in data])
    mr = [ranks[pos[i]:pos[i + 1]].mean() for i in range(len(data))]
    pairs, ps = [], []
    for i, j in itertools.combinations(range(len(data)), 2):
        se = math.sqrt(max((N * (N + 1) / 12 - tie) * (1 / len(data[i]) + 1 / len(data[j])), 1e-300))
        z = (mr[i] - mr[j]) / se
        pairs.append((i, j, z))
        ps.append(float(2 * sps.norm.sf(abs(z))))
    adj = p_adjust(ps, adjust)
    return [{"a": names[i], "b": names[j], "diff": float(np.median(data[i]) - np.median(data[j])), "z": float(z),
             "p": float(pa), "test": f"Dunn ({_ADJ_NAMES.get(adjust, adjust)})"}
            for (i, j, z), pa in zip(pairs, adj)]


_ADJ_NAMES = {"bonferroni": "Bonferroni", "holm": "Holm", "sidak": "Šidák", "fdr": "FDR", "none": "unadjusted"}


def anova_error_term(data, paired: bool = False) -> tuple[float, float]:
    """(mean square error, error df) of a one-way design: the within-groups term of a between-subjects ANOVA, or
    the condition × subject residual of a repeated-measures ANOVA (`paired`, rows matched by position)."""
    data = [np.asarray(d, float) for d in data]
    k = len(data)
    if paired:
        Y = np.column_stack([d[:min(len(x) for x in data)] for d in data])
        n = Y.shape[0]
        grand = Y.mean()
        ss_err = ((Y - Y.mean(axis=1, keepdims=True) - Y.mean(axis=0, keepdims=True) + grand) ** 2).sum()
        df = (n - 1) * (k - 1)
    else:
        ss_err = sum(((d - d.mean()) ** 2).sum() for d in data)
        df = sum(len(d) for d in data) - k
    return (float(ss_err / df) if df > 0 else math.nan), float(df)


def range_test_p(q: float, r: int, df: float, method: str = "snk") -> float:
    """p-value of a studentized range statistic q for two means r steps apart (r = number of ordered means spanned,
    both included) in a multiple range test.

    snk: Student-Newman-Keuls, P(Q_{r,df} ≥ q) — the Tukey test for the whole range r = k.
    duncan: Duncan's new multiple range test, whose protection level for r means is α_r = 1 - (1 - α)^(r - 1);
    the p-value is the α at which q equals the critical range: 1 - P(Q_{r,df} < q)^(1 / (r - 1)).
    """
    if not (q == q) or not (df == df) or df <= 0 or r < 2:
        return math.nan
    if math.isinf(q):
        return 0.0
    sf = float(sps.studentized_range.sf(q, r, df))
    sf = min(max(sf, 0.0), 1.0)
    if method == "duncan":
        return float(min(1.0, -math.expm1(math.log1p(-sf) / (r - 1)))) if sf < 1 else 1.0
    return sf


def range_test_critical(r: int, df: float, method: str = "snk", alpha: float = 0.05) -> float:
    """Critical studentized range for r means (SNK / Tukey: q_{1-α}(r, df); Duncan: q_{(1-α)^(r-1)}(r, df)).
    Multiply by sqrt(MSE / n) for the least significant range."""
    level = (1 - alpha) ** (r - 1) if method == "duncan" else 1 - alpha
    return float(sps.studentized_range.ppf(level, r, df))


def _error_term_posthoc(names, data, method: str, paired: bool = False, stepwise: bool = True) -> list[dict]:
    """Fisher's LSD, Scheffé, Student-Newman-Keuls and Duncan's multiple range test, all using the ANOVA error term.

    Unequal group sizes use the harmonic (Tukey-Kramer) standard error of each difference. For the stepwise
    multiple range tests (SNK, Duncan) the reported p-value of a pair is the largest p of the ranges that contain it
    (a pair cannot differ when a wider range enclosing it does not), so p ≤ α reproduces the step-down decisions;
    `p_unadjusted` holds the pair's own range p-value (as reported by e.g. R's agricolae).
    """
    k = len(data)
    mse, df = anova_error_term(data, paired)
    means = np.array([d.mean() for d in data])
    ns = np.array([len(d) for d in data], float)
    if paired:
        ns[:] = min(len(d) for d in data)
    rank = np.empty(k, int)
    rank[np.argsort(means, kind="stable")] = np.arange(k)
    label = ERROR_TERM_POSTHOC[method]
    out = []
    for i, j in itertools.combinations(range(k), 2):
        diff = float(means[i] - means[j])
        se2 = mse * (1 / ns[i] + 1 / ns[j])
        e = {"a": names[i], "b": names[j], "diff": diff, "test": label}
        if not (se2 == se2) or df <= 0:
            p = math.nan
        elif se2 <= 0:
            p = 1.0 if diff == 0 else 0.0
        elif method == "lsd":
            t = abs(diff) / math.sqrt(se2)
            e["t"], e["df"] = t, df
            p = float(2 * sps.t.sf(t, df))
        elif method == "scheffe":
            F = diff ** 2 / (se2 * (k - 1))
            e["F"], e["df"] = F, (k - 1, df)
            p = float(sps.f.sf(F, k - 1, df))
        else:
            q = abs(diff) / math.sqrt(se2 / 2)
            r = abs(int(rank[i]) - int(rank[j])) + 1
            e["q"], e["steps"], e["df"] = q, r, df
            p = range_test_p(q, r, df, method)
        e["p"] = e["p_unadjusted"] = float(min(1.0, p)) if p == p else math.nan
        out.append(e)
    if stepwise and method in ("snk", "duncan"):
        span = {id(e): sorted((int(rank[names.index(e["a"])]), int(rank[names.index(e["b"])]))) for e in out}
        for e in out:
            lo, hi = span[id(e)]
            enclosing = [x["p_unadjusted"] for x in out if span[id(x)][0] <= lo and span[id(x)][1] >= hi
                         and x["p_unadjusted"] == x["p_unadjusted"]]
            if enclosing:
                e["p"] = float(max(enclosing))
    return out


@_quiet
def posthoc(groups: "dict[str, np.ndarray]", method: str = "tukey", parametric: bool = True, paired: bool = False,
            control: str | None = None) -> list[dict]:
    """Pairwise comparisons between groups.

    tukey / games_howell / dunnett (vs `control`, default first group) / dunn; the ANOVA-error-term tests lsd
    (Fisher's LSD), scheffe, snk (Student-Newman-Keuls) and duncan (Duncan's multiple range test; these use the
    repeated-measures error term when paired); or pairwise tests (t-tests, Welch t, paired t; Mann-Whitney / Wilcoxon
    when non-parametric) adjusted by bonferroni / holm / sidak / fdr.
    """
    names = [k for k, v in groups.items() if len(_clean(v)) > 0]
    data = _clean_rows([groups[k] for k in names]) if paired else [_clean(groups[k]) for k in names]
    if len(names) < 2:
        return []
    if paired:
        n = min(len(d) for d in data)
        data = [d[:n] for d in data]
    if method in ERROR_TERM_POSTHOC:
        return _error_term_posthoc(names, data, method, paired)
    if method == "tukey" and not paired:
        tk = sps.tukey_hsd(*data)
        return [{"a": names[i], "b": names[j], "diff": float(data[i].mean() - data[j].mean()),
                 "p": float(tk.pvalue[i, j]), "test": "Tukey HSD"} for i, j in itertools.combinations(range(len(data)), 2)]
    if method == "games_howell" and not paired:
        return _games_howell(names, data)
    if method == "dunn" and not paired:
        return _dunn(names, data, "bonferroni")
    if method == "dunnett" and not paired:
        ci = names.index(control) if control in names else 0
        others = [i for i in range(len(data)) if i != ci]
        r = sps.dunnett(*[data[i] for i in others], control=data[ci])
        return [{"a": names[i], "b": names[ci], "diff": float(data[i].mean() - data[ci].mean()),
                 "p": float(p), "test": "Dunnett"} for i, p in zip(others, r.pvalue)]
    adjust = method if method in ("bonferroni", "holm", "sidak", "fdr", "none") else "bonferroni"
    pairs, ps = [], []
    for i, j in itertools.combinations(range(len(data)), 2):
        a, b = data[i], data[j]
        if paired:
            if parametric:
                p, name = sps.ttest_rel(a, b).pvalue, "paired t"
            else:
                p, name = (sps.wilcoxon(a, b).pvalue if np.any(a != b) else 1.0), "Wilcoxon"
        elif parametric:
            p, name = sps.ttest_ind(a, b, equal_var=False).pvalue, "Welch t"
        else:
            p, name = sps.mannwhitneyu(a, b, alternative="two-sided").pvalue, "Mann-Whitney"
        pairs.append((i, j, name))
        ps.append(float(p) if p == p else 1.0)
    adj = p_adjust(ps, adjust)
    label = "Mann-Whitney (Bonferroni)" if (not parametric and not paired and adjust == "bonferroni") else None
    return [{"a": names[i], "b": names[j], "diff": float(data[i].mean() - data[j].mean()), "p": float(pa),
             "p_unadjusted": float(pu), "test": label or f"{name} ({_ADJ_NAMES[adjust]})"}
            for (i, j, name), pa, pu in zip(pairs, adj, ps)]


# ---------------------------------------------------------------- effect sizes
def _cohen_d(a, b, paired=False):
    if paired:
        d = a - b
        sd = d.std(ddof=1)
        return float(d.mean() / sd) if sd > 0 else math.nan
    sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else math.nan


def _hedges_g(d, n1, n2):
    df = n1 + n2 - 2
    return d * (1 - 3 / (4 * df - 1)) if df > 0 and d == d else math.nan


# ---------------------------------------------------------------- comparisons
def _welch_anova(data):
    k = len(data)
    n = np.array([len(d) for d in data], float)
    m = np.array([d.mean() for d in data])
    v = np.array([d.var(ddof=1) for d in data])
    if np.any(v <= 0):
        return math.nan, (k - 1, math.nan), math.nan
    w = n / v
    sw = w.sum()
    mw = (w * m).sum() / sw
    A = (w * (m - mw) ** 2).sum() / (k - 1)
    tmp = ((1 - w / sw) ** 2 / (n - 1)).sum()
    B = 1 + 2 * (k - 2) / (k ** 2 - 1) * tmp
    F = A / B
    df2 = (k ** 2 - 1) / (3 * tmp)
    return float(F), (k - 1, float(df2)), float(sps.f.sf(F, k - 1, df2))


@_quiet
def rm_anova_matrix(Y) -> dict:
    """One-way repeated-measures ANOVA on a subjects × conditions matrix (Greenhouse-Geisser corrected p too)."""
    Y = np.asarray(Y, float)
    n, k = Y.shape
    if n < 2 or k < 2:
        return {"error": "Need at least 2 subjects and 2 conditions"}
    grand = Y.mean()
    ss_subj = k * ((Y.mean(axis=1) - grand) ** 2).sum()
    ss_cond = n * ((Y.mean(axis=0) - grand) ** 2).sum()
    ss_tot = ((Y - grand) ** 2).sum()
    ss_err = max(ss_tot - ss_subj - ss_cond, 0.0)
    df1, df2 = k - 1, (n - 1) * (k - 1)
    ms_err = ss_err / df2
    F = (ss_cond / df1) / ms_err if ms_err > 0 else (math.inf if ss_cond > 0 else math.nan)
    p = float(sps.f.sf(F, df1, df2)) if F == F else math.nan
    S = np.cov(Y, rowvar=False)
    C = np.eye(k) - 1.0 / k
    Sc = C @ S @ C
    den = (k - 1) * (Sc ** 2).sum()
    eps = float(np.trace(Sc) ** 2 / den) if den > 0 else 1.0
    eps = min(1.0, max(1.0 / (k - 1), eps))
    p_gg = float(sps.f.sf(F, eps * df1, eps * df2)) if F == F else math.nan
    return {"F": float(F), "df": (df1, df2), "p": p, "epsilon_gg": eps, "p_gg": p_gg, "SS": float(ss_cond),
            "SS_error": float(ss_err), "partial_eta2": float(ss_cond / (ss_cond + ss_err)) if ss_cond + ss_err > 0
            else math.nan, "n_subjects": n}


@_quiet
def compare_groups(groups: "dict[str, np.ndarray]", parametric: bool = True, paired: bool = False,
                   method: str = "auto", posthoc_method: str = "auto", control: str | None = None) -> dict:
    """Compare a measure between groups.

    Automatic choice (method="auto"):
    2 groups: Welch t-test / Mann-Whitney U (paired: paired t / Wilcoxon).
    >2 groups: one-way ANOVA + Tukey HSD / Kruskal-Wallis + Bonferroni-corrected Mann-Whitney
    (paired: repeated-measures ANOVA + Bonferroni paired t / Friedman + Bonferroni Wilcoxon).
    method: any key of METHODS_TWO / METHODS_K; posthoc_method: any key of POSTHOC.
    Paired data (paired=True or a repeated-measures method) are matched by position in each array.
    """
    if method in PAIRED_METHODS:
        paired = True
    names = [k for k, v in groups.items() if len(_clean(v)) > 0]
    data = _clean_rows([groups[k] for k in names]) if paired else [_clean(groups[k]) for k in names]
    if method in NONPARAMETRIC:
        parametric = False
    elif method != "auto":
        parametric = True
    out = {"groups": names, "descriptive": {k: descriptive(v) for k, v in zip(names, data)}, "posthoc": []}
    if len(names) < 2:
        out.update(test="Not enough groups", statistic=math.nan, p=math.nan)
        return out
    if any(len(d) < 2 for d in data):
        out.update(test="Not enough data (need n ≥ 2 per group)", statistic=math.nan, p=math.nan)
        return out
    # assumption checks
    try:
        out["normality_p"] = {k: float(sps.shapiro(d).pvalue) if len(d) >= 3 else math.nan for k, d in zip(names, data)}
        out["levene_p"] = float(sps.levene(*data).pvalue)
    except Exception:  # pragma: no cover - degenerate data
        pass
    if paired:
        n = min(len(d) for d in data)
        data = [d[:n] for d in data]
    k = len(data)
    if k > 2 and method in _TWO_TO_K:
        method = _TWO_TO_K[method]
    if k == 2 and method in ("auto", "student", "welch", "mannwhitney", "ks", "brunnermunzel", "paired_t",
                             "wilcoxon"):
        a, b = data
        if method == "auto":
            method = ("paired_t" if parametric else "wilcoxon") if paired else ("welch" if parametric
                                                                                 else "mannwhitney")
        if method == "paired_t":
            r = sps.ttest_rel(a, b)
            out.update(test="Paired t-test", statistic=float(r.statistic), p=float(r.pvalue), df=len(a) - 1)
        elif method == "wilcoxon":
            r = sps.wilcoxon(a, b) if np.any(a != b) else None
            out.update(test="Wilcoxon signed-rank", statistic=float(r.statistic) if r else 0.0,
                       p=float(r.pvalue) if r else 1.0)
        elif method == "student":
            r = sps.ttest_ind(a, b, equal_var=True)
            out.update(test="Student's t-test", statistic=float(r.statistic), p=float(r.pvalue), df=len(a) + len(b) - 2)
        elif method == "welch":
            r = sps.ttest_ind(a, b, equal_var=False)
            out.update(test="Welch's t-test", statistic=float(r.statistic), p=float(r.pvalue),
                       df=float(getattr(r, "df", len(a) + len(b) - 2)))
        elif method == "ks":
            r = sps.ks_2samp(a, b)
            out.update(test="Kolmogorov-Smirnov", statistic=float(r.statistic), p=float(r.pvalue))
        elif method == "brunnermunzel":
            r = sps.brunnermunzel(a, b)
            out.update(test="Brunner-Munzel", statistic=float(r.statistic), p=float(r.pvalue))
        else:
            r = sps.mannwhitneyu(a, b, alternative="two-sided")
            out.update(test="Mann-Whitney U", statistic=float(r.statistic), p=float(r.pvalue))
        d = _cohen_d(a, b, paired)
        out["effect_size"] = d
        out["effect_size_name"] = "Cohen's d" + (" (paired)" if paired else "")
        es = {"Cohen's d": d}
        if not paired:
            es["Hedges' g"] = _hedges_g(d, len(a), len(b))
        if not parametric and not paired:
            U = sps.mannwhitneyu(a, b, alternative="two-sided").statistic
            es["rank-biserial r"] = float(2 * U / (len(a) * len(b)) - 1)
        out["effect_sizes"] = es
        return out
    # ---- k groups ----
    if method == "auto":
        method = ("rm_anova" if parametric else "friedman") if paired else ("anova" if parametric else "kruskal")
    N = sum(len(d) for d in data)
    if method == "rm_anova":
        res = rm_anova_matrix(np.column_stack(data))
        out.update(test="Repeated-measures ANOVA", statistic=res["F"], p=res["p"], df=res["df"],
                   p_gg=res["p_gg"], epsilon_gg=res["epsilon_gg"])
        out["effect_size"] = res["partial_eta2"]
        out["effect_size_name"] = "partial eta²"
        default_ph = "bonferroni"
    elif method == "friedman":
        r = sps.friedmanchisquare(*data) if k > 2 else None
        if r is None:  # Friedman needs ≥ 3 conditions; fall back to Wilcoxon
            return compare_groups(OrderedDict(zip(names, data)), method="wilcoxon")
        out.update(test="Friedman", statistic=float(r.statistic), p=float(r.pvalue), df=k - 1)
        out["effect_size"] = float(r.statistic / (len(data[0]) * (k - 1)))
        out["effect_size_name"] = "Kendall's W"
        default_ph = "bonferroni"
    elif method in ("anova", "welch_anova", "alexander_govern"):
        grand = np.concatenate(data).mean()
        ssb = sum(len(d) * (d.mean() - grand) ** 2 for d in data)
        sst = sum(((d - grand) ** 2).sum() for d in data)
        if method == "anova":
            r = sps.f_oneway(*data)
            out.update(test="One-way ANOVA", statistic=float(r.statistic), p=float(r.pvalue), df=(k - 1, N - k))
            default_ph = "tukey"
        elif method == "welch_anova":
            F, df, p = _welch_anova(data)
            out.update(test="Welch's ANOVA", statistic=F, p=p, df=df)
            default_ph = "games_howell"
        else:
            r = sps.alexandergovern(*data)
            out.update(test="Alexander-Govern", statistic=float(r.statistic), p=float(r.pvalue), df=k - 1)
            default_ph = "games_howell"
        out["effect_size"] = float(ssb / sst) if sst > 0 else math.nan
        out["effect_size_name"] = "eta²"
        msw = (sst - ssb) / (N - k) if N > k else math.nan
        out["effect_sizes"] = {"eta²": out["effect_size"],
                               "omega²": float((ssb - (k - 1) * msw) / (sst + msw)) if msw == msw and sst > 0
                               else math.nan}
    elif method == "median":
        r = sps.median_test(*data)
        out.update(test="Mood's median test", statistic=float(r.statistic), p=float(r.pvalue), df=k - 1)
        default_ph = "bonferroni"
    else:
        r = sps.kruskal(*data)
        out.update(test="Kruskal-Wallis", statistic=float(r.statistic), p=float(r.pvalue), df=k - 1)
        out["effect_size"] = float((r.statistic - k + 1) / (N - k)) if N > k else math.nan
        out["effect_size_name"] = "eta²H"  # (H - k + 1) / (N - k), Kruskal-Wallis eta squared
        default_ph = "mw_bonferroni"
    ph = default_ph if posthoc_method in (None, "auto") else posthoc_method
    if ph != "none":
        try:
            if ph == "mw_bonferroni":
                out["posthoc"] = posthoc(OrderedDict(zip(names, data)), "bonferroni", parametric=False)
            else:
                out["posthoc"] = posthoc(OrderedDict(zip(names, data)), ph, parametric=parametric, paired=paired,
                                         control=control)
        except Exception as e:  # pragma: no cover - surfaced in the result
            out["posthoc_error"] = str(e)
    out["posthoc_method"] = ph
    return out


@_quiet
def one_sample(values, mu: float = 0.0, parametric: bool = True) -> dict:
    """One-sample t-test or Wilcoxon signed-rank test of values against a reference value mu."""
    a = _clean(values)
    out = {"descriptive": descriptive(a), "mu": float(mu)}
    if len(a) < 2:
        out.update(test="Not enough data", statistic=math.nan, p=math.nan)
        return out
    if parametric:
        r = sps.ttest_1samp(a, mu)
        out.update(test="One-sample t-test", statistic=float(r.statistic), p=float(r.pvalue), df=len(a) - 1)
    else:
        d = a - mu
        r = sps.wilcoxon(d) if np.any(d != 0) else None
        out.update(test="Wilcoxon signed-rank vs value", statistic=float(r.statistic) if r else 0.0,
                   p=float(r.pvalue) if r else 1.0)
    sd = a.std(ddof=1)
    out["effect_size"] = float((a.mean() - mu) / sd) if sd > 0 else math.nan
    out["effect_size_name"] = "Cohen's d"
    return out


# ---------------------------------------------------------------- two factors
def _records(rows, measure, a, b, subject=None):
    recs = []
    for r in rows:
        v = r.get(measure)
        if _num(v):
            rec = (str(r.get(a, "")), str(r.get(b, "")), float(v))
            if subject:
                rec += (str(r.get(subject, "")),)
            recs.append(rec)
    return recs


def _anova2(A_vals, B_vals, y):
    """Type II two-way ANOVA via nested least-squares models. Returns (effects list, df_res, ms_res)."""
    A = sorted(set(A_vals))
    B = sorted(set(B_vals))

    def dummies(levels, vals):
        cols = [(np.array(vals) == lv).astype(float) for lv in levels[1:]]
        return np.column_stack(cols) if cols else np.zeros((len(vals), 0))

    da = dummies(A, A_vals)
    db = dummies(B, B_vals)
    dab = (np.column_stack([da[:, i] * db[:, j] for i in range(da.shape[1]) for j in range(db.shape[1])])
           if da.shape[1] and db.shape[1] else np.zeros((len(y), 0)))
    one = np.ones((len(y), 1))

    def rss(X):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        e = y - X @ beta
        return float(e @ e), np.linalg.matrix_rank(X)

    rss_full, rank_full = rss(np.hstack([one, da, db, dab]))
    rss_ab, rank_ab = rss(np.hstack([one, da, db]))
    rss_a, rank_a = rss(np.hstack([one, da]))
    rss_b, rank_b = rss(np.hstack([one, db]))
    df_res = len(y) - rank_full
    effects = [("A", rss_b - rss_ab, rank_ab - rank_b), ("B", rss_a - rss_ab, rank_ab - rank_a),
               ("AB", rss_ab - rss_full, rank_full - rank_ab)]
    return effects, df_res, rss_full


@_quiet
def two_way_anova(rows: list[dict], measure: str, factor_a: str = "Group", factor_b: str = "Stage") -> dict:
    """Between-subjects two-way ANOVA with Type II sums of squares."""
    recs = _records(rows, measure, factor_a, factor_b)
    if len(recs) < 4:
        return {"error": "Not enough data"}
    if len({r[0] for r in recs}) < 2 or len({r[1] for r in recs}) < 2:
        return {"error": f"Need at least 2 levels of {factor_a} and {factor_b}"}
    y = np.array([r[2] for r in recs])
    effects, df_res, rss_full = _anova2([r[0] for r in recs], [r[1] for r in recs], y)
    if df_res <= 0:
        return {"error": "Not enough residual degrees of freedom"}
    ms_res = rss_full / df_res
    names = {"A": factor_a, "B": factor_b, "AB": f"{factor_a} × {factor_b}"}
    out = {"factors": [factor_a, factor_b], "df_residual": df_res, "effects": [], "design": "between",
           "test": "Two-way ANOVA"}
    for key, ss, df in effects:
        if df <= 0:
            continue
        F = (ss / df) / ms_res if ms_res > 0 else math.nan
        p = float(sps.f.sf(F, df, df_res)) if math.isfinite(F) else math.nan
        out["effects"].append({"effect": names[key], "SS": ss, "df": df, "F": F, "p": p, "df_error": df_res,
                               "partial_eta2": ss / (ss + rss_full) if ss + rss_full > 0 else math.nan})
    return out


@_quiet
def ancova(rows: list[dict], measure: str, covariate: str, factor: str = "Group") -> dict:
    """One-way analysis of covariance: ``measure`` ~ ``factor`` + ``covariate`` (Type II sums of squares), with
    covariate-adjusted group means and the homogeneity-of-regression-slopes test (factor × covariate)."""
    recs = []
    for r in rows:
        y, c = r.get(measure), r.get(covariate)
        if _num(y) and _num(c):
            recs.append((str(r.get(factor, "")), float(c), float(y)))
    levels = sorted({r[0] for r in recs})
    if len(levels) < 2:
        return {"error": f"Need at least 2 levels of {factor}"}
    if len(recs) < len(levels) + 2:
        return {"error": "Not enough data"}
    g = [r[0] for r in recs]
    x = np.array([r[1] for r in recs])
    y = np.array([r[2] for r in recs])
    one = np.ones((len(y), 1))
    d = np.column_stack([(np.array(g) == lv).astype(float) for lv in levels[1:]])
    xc = (x - x.mean())[:, None]

    def fit(X):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        e = y - X @ beta
        return float(e @ e), int(np.linalg.matrix_rank(X)), beta

    rss_full, rank_full, beta = fit(np.hstack([one, d, xc]))
    rss_f, rank_f, _ = fit(np.hstack([one, d]))
    rss_c, rank_c, _ = fit(np.hstack([one, xc]))
    rss_int, rank_int, _ = fit(np.hstack([one, d, xc, d * xc]))
    df_res = len(y) - rank_full
    if df_res <= 0 or rank_full - rank_f < 1:
        return {"error": "Not enough residual degrees of freedom (or the covariate does not vary)"}
    ms_res = rss_full / df_res
    out = {"factors": [factor], "df_residual": df_res, "effects": [], "design": "between",
           "test": f"ANCOVA, covariate {covariate}", "covariate": covariate}
    for name, ss, df in ((factor, rss_c - rss_full, rank_full - rank_c),
                         (covariate, rss_f - rss_full, rank_full - rank_f)):
        F = (ss / df) / ms_res if ms_res > 0 and df > 0 else math.nan
        p = float(sps.f.sf(F, df, df_res)) if math.isfinite(F) else math.nan
        out["effects"].append({"effect": name, "SS": ss, "df": df, "F": F, "p": p, "df_error": df_res,
                               "partial_eta2": ss / (ss + rss_full) if ss + rss_full > 0 else math.nan})
    df_int, df_int_res = rank_int - rank_full, len(y) - rank_int
    if df_int > 0 and df_int_res > 0 and rss_int > 0:
        F = ((rss_full - rss_int) / df_int) / (rss_int / df_int_res)
        out["slopes"] = {"F": F, "df": df_int, "df_error": df_int_res, "p": float(sps.f.sf(F, df_int, df_int_res))}
    slope = float(beta[-1])
    out["slope"] = slope
    masks = {lv: np.array(g) == lv for lv in levels}
    ss_within_x = sum(float(((x[m] - x[m].mean()) ** 2).sum()) for m in masks.values())
    adj = OrderedDict()
    for lv, m in masks.items():
        se = math.sqrt(ms_res * (1.0 / m.sum() + (x[m].mean() - x.mean()) ** 2 / max(1e-300, ss_within_x)))
        adj[lv] = {"n": int(m.sum()), "mean": float(y[m].mean()), "covariate_mean": float(x[m].mean()),
                   "adjusted_mean": float(y[m].mean() - slope * (x[m].mean() - x.mean())), "adjusted_se": se}
    out["adjusted_means"] = adj
    return out


def ancova_text(res: dict, measure: str) -> str:
    if "error" in res:
        return f"{measure}: {res['error']}"
    lines = [anova_text(res, measure), f"  Common slope ({res['covariate']}): {res['slope']:.4g}"]
    if "slopes" in res:
        sl = res["slopes"]
        lines.append(f"  Homogeneity of slopes: F({sl['df']}, {sl['df_error']}) = {sl['F']:.3f}, {format_p(sl['p'])}"
                     + ("  — slopes differ, ANCOVA assumption violated" if sl["p"] < 0.05 else ""))
    for lv, a in res["adjusted_means"].items():
        lines.append(f"  {lv}: n={a['n']}, mean={a['mean']:.3f}, adjusted mean={a['adjusted_mean']:.3f} ± "
                     f"{a['adjusted_se']:.3f} SE (covariate mean {a['covariate_mean']:.3f})")
    return "\n".join(lines)


@_quiet
def scheirer_ray_hare(rows: list[dict], measure: str, factor_a: str = "Group", factor_b: str = "Stage") -> dict:
    """Non-parametric two-way analysis on ranks (Scheirer-Ray-Hare extension of Kruskal-Wallis)."""
    recs = _records(rows, measure, factor_a, factor_b)
    if len(recs) < 4 or len({r[0] for r in recs}) < 2 or len({r[1] for r in recs}) < 2:
        return {"error": f"Need at least 2 levels of {factor_a} and {factor_b}"}
    y = sps.rankdata([r[2] for r in recs])
    effects, df_res, rss_full = _anova2([r[0] for r in recs], [r[1] for r in recs], y)
    ms_total = float(((y - y.mean()) ** 2).sum()) / (len(y) - 1)
    names = {"A": factor_a, "B": factor_b, "AB": f"{factor_a} × {factor_b}"}
    out = {"factors": [factor_a, factor_b], "effects": [], "design": "srh", "test": "Scheirer-Ray-Hare",
           "df_residual": df_res}
    for key, ss, df in effects:
        if df <= 0:
            continue
        H = ss / ms_total if ms_total > 0 else math.nan
        out["effects"].append({"effect": names[key], "SS": ss, "df": df, "H": H, "F": H,
                               "p": float(sps.chi2.sf(H, df)) if H == H else math.nan})
    return out


@_quiet
def art_anova(rows: list[dict], measure: str, factor_a: str = "Group", factor_b: str = "Stage") -> dict:
    """Aligned rank transform two-way ANOVA (Wobbrock et al. 2011): align, rank, ANOVA per effect."""
    recs = _records(rows, measure, factor_a, factor_b)
    if len(recs) < 4 or len({r[0] for r in recs}) < 2 or len({r[1] for r in recs}) < 2:
        return {"error": f"Need at least 2 levels of {factor_a} and {factor_b}"}
    Av = np.array([r[0] for r in recs])
    Bv = np.array([r[1] for r in recs])
    y = np.array([r[2] for r in recs])
    grand = y.mean()
    ma = {a: y[Av == a].mean() for a in set(Av)}
    mb = {b: y[Bv == b].mean() for b in set(Bv)}
    cell = {(a, b): y[(Av == a) & (Bv == b)].mean() for a, b in set(zip(Av, Bv))}
    resid = y - np.array([cell[(a, b)] for a, b in zip(Av, Bv)])
    est = {"A": np.array([ma[a] - grand for a in Av]), "B": np.array([mb[b] - grand for b in Bv]),
           "AB": np.array([cell[(a, b)] - ma[a] - mb[b] + grand for a, b in zip(Av, Bv)])}
    names = {"A": factor_a, "B": factor_b, "AB": f"{factor_a} × {factor_b}"}
    out = {"factors": [factor_a, factor_b], "effects": [], "design": "art", "test": "Aligned rank transform ANOVA"}
    for key in ("A", "B", "AB"):
        ry = sps.rankdata(resid + est[key])
        effects, df_res, rss_full = _anova2(list(Av), list(Bv), ry)
        out["df_residual"] = df_res
        ss, df = next((e[1], e[2]) for e in effects if e[0] == key)
        if df <= 0 or df_res <= 0:
            continue
        ms_res = rss_full / df_res
        F = (ss / df) / ms_res if ms_res > 0 else math.nan
        out["effects"].append({"effect": names[key], "SS": ss, "df": df, "F": F, "df_error": df_res,
                               "p": float(sps.f.sf(F, df, df_res)) if math.isfinite(F) else math.nan})
    return out


def _subject_matrix(rows, measure, within, subject, levels=None):
    per: dict[str, dict[str, list]] = {}
    for r in rows:
        v = r.get(measure)
        if _num(v):
            per.setdefault(str(r.get(subject, "")), {}).setdefault(str(r.get(within, "")), []).append(float(v))
    levels = levels or list(dict.fromkeys(str(r.get(within, "")) for r in rows))
    subs = [s for s, d in per.items() if all(lv in d for lv in levels)]
    Y = np.array([[np.mean(per[s][lv]) for lv in levels] for s in subs]) if subs else np.zeros((0, len(levels)))
    return subs, levels, Y


def rm_anova(rows: list[dict], measure: str, within: str = "Stage", subject: str = "Animal",
             levels: list | None = None) -> dict:
    """One-way repeated-measures ANOVA (within-subject factor; animals with all levels are used)."""
    subs, levels, Y = _subject_matrix(rows, measure, within, subject, levels)
    if len(levels) < 2 or len(subs) < 2:
        return {"error": f"Need ≥ 2 animals measured at ≥ 2 levels of {within}"}
    res = rm_anova_matrix(Y)
    res.update(test="Repeated-measures ANOVA", factor=within, levels=levels, subjects=subs)
    return res


@_quiet
def mixed_anova(rows: list[dict], measure: str, between: str = "Group", within: str = "Stage",
                subject: str = "Animal", levels: list | None = None) -> dict:
    """Two-way mixed ANOVA: between-subjects factor × within-subjects (repeated) factor.

    Animals missing a level of the within factor are dropped. The within effects also get Greenhouse-Geisser
    corrected p-values (p_gg).
    """
    subs, levels, Y = _subject_matrix(rows, measure, within, subject, levels)
    grp = {}
    for r in rows:
        grp.setdefault(str(r.get(subject, "")), str(r.get(between, "")))
    G = list(dict.fromkeys(grp[s] for s in subs))
    if len(levels) < 2 or len(G) < 2 or len(subs) < len(G) + 1:
        return {"error": f"Need ≥ 2 levels of {between} and {within} with repeated measures per animal"}
    g_of = np.array([grp[s] for s in subs])
    N, k = Y.shape
    grand = Y.mean()
    m_j = Y.mean(axis=0)
    ss_total = ((Y - grand) ** 2).sum()
    ss_b = ss_cells = ss_errb = 0.0
    for g in G:
        Yg = Y[g_of == g]
        ng = len(Yg)
        ss_b += k * ng * (Yg.mean() - grand) ** 2
        ss_cells += ng * ((Yg.mean(axis=0) - grand) ** 2).sum()
        ss_errb += k * ((Yg.mean(axis=1) - Yg.mean()) ** 2).sum()
    ss_w = N * ((m_j - grand) ** 2).sum()
    ss_bw = max(ss_cells - ss_b - ss_w, 0.0)
    ss_errw = max(ss_total - ss_cells - ss_errb, 0.0)
    nG = len(G)
    df_b, df_errb = nG - 1, N - nG
    df_w, df_bw, df_errw = k - 1, (nG - 1) * (k - 1), (N - nG) * (k - 1)
    # Greenhouse-Geisser epsilon from the pooled within-group covariance
    R = np.vstack([Y[g_of == g] - Y[g_of == g].mean(axis=0) for g in G])
    S = R.T @ R / max(df_errb, 1)
    C = np.eye(k) - 1.0 / k
    Sc = C @ S @ C
    den = (k - 1) * (Sc ** 2).sum()
    eps = min(1.0, max(1.0 / (k - 1), float(np.trace(Sc) ** 2 / den) if den > 0 else 1.0))

    def row(name, ss, df, ss_err, df_err, gg=False):
        ms_err = ss_err / df_err if df_err > 0 else math.nan
        F = (ss / df) / ms_err if ms_err and ms_err > 0 else math.nan
        p = float(sps.f.sf(F, df, df_err)) if F == F else math.nan
        e = {"effect": name, "SS": float(ss), "df": df, "F": float(F), "p": p, "df_error": df_err,
             "partial_eta2": float(ss / (ss + ss_err)) if ss + ss_err > 0 else math.nan}
        if gg:
            e["p_gg"] = float(sps.f.sf(F, eps * df, eps * df_err)) if F == F else math.nan
        return e

    return {"test": "Mixed two-way ANOVA", "design": "mixed", "factors": [between, within], "levels": levels,
            "epsilon_gg": eps, "n_subjects": N, "df_residual": df_errw,
            "effects": [row(between, ss_b, df_b, ss_errb, df_errb),
                        row(within, ss_w, df_w, ss_errw, df_errw, True),
                        row(f"{between} × {within}", ss_bw, df_bw, ss_errw, df_errw, True)]}


# ---------------------------------------------------------------- categorical
def contingency_table(rows: list[dict], row_factor: str, col_factor: str) -> tuple[list, list, np.ndarray]:
    rl = list(dict.fromkeys(str(r.get(row_factor, "")) for r in rows))
    cl = list(dict.fromkeys(str(r.get(col_factor, "")) for r in rows if str(r.get(col_factor, "")) != ""))
    T = np.zeros((len(rl), len(cl)), int)
    for r in rows:
        c = str(r.get(col_factor, ""))
        if c in cl:
            T[rl.index(str(r.get(row_factor, ""))), cl.index(c)] += 1
    keep = T.sum(axis=1) > 0
    return [x for x, k in zip(rl, keep) if k], cl, T[keep]


@_quiet
def categorical_test(table, row_labels=None, col_labels=None) -> dict:
    """Chi-square test of independence (+ G-test, Cramér's V) and Fisher's exact test for 2 × 2 tables."""
    T = np.asarray(table, float)
    out = {"table": T.astype(int).tolist(), "rows": row_labels, "cols": col_labels}
    T = T[T.sum(axis=1) > 0][:, T.sum(axis=0) > 0] if T.size else T
    if T.ndim != 2 or T.shape[0] < 2 or T.shape[1] < 2:
        out.update(test="Not enough categories", statistic=math.nan, p=math.nan)
        return out
    chi2, p, dof, exp = sps.chi2_contingency(T, correction=False)
    out.update(test="Chi-square test of independence", statistic=float(chi2), p=float(p), df=int(dof),
               expected=exp.tolist(), low_expected=bool((exp < 5).any()))
    n = T.sum()
    out["effect_size"] = float(math.sqrt(chi2 / (n * (min(T.shape) - 1)))) if n > 0 else math.nan
    out["effect_size_name"] = "Cramér's V"
    try:
        g, gp, _, _ = sps.chi2_contingency(T, correction=False, lambda_="log-likelihood")
        out["g_test"] = {"statistic": float(g), "p": float(gp)}
    except Exception:  # pragma: no cover
        pass
    if T.shape == (2, 2):
        odds, fp = sps.fisher_exact(T)
        out["fisher"] = {"odds_ratio": float(odds), "p": float(fp)}
    return out


def chi_square_gof(counts, expected=None) -> dict:
    """Chi-square goodness of fit of observed counts against expected counts (default: uniform)."""
    c = np.asarray(counts, float)
    if len(c) < 2 or c.sum() <= 0:
        return {"test": "Not enough categories", "statistic": math.nan, "p": math.nan}
    if expected is not None:
        e = np.asarray(expected, float)
        e = e * c.sum() / e.sum()
    else:
        e = None
    r = sps.chisquare(c, e)
    return {"test": "Chi-square goodness of fit", "statistic": float(r.statistic), "p": float(r.pvalue),
            "df": len(c) - 1}


# ---------------------------------------------------------------- correlation / regression
@_quiet
def correlation(x, y, method: str = "pearson") -> dict:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return {"r": math.nan, "p": math.nan, "n": int(ok.sum()), "method": method}
    if method == "spearman":
        r = sps.spearmanr(x[ok], y[ok])
    elif method == "kendall":
        r = sps.kendalltau(x[ok], y[ok])
    else:
        r = sps.pearsonr(x[ok], y[ok])
    out = {"r": float(r[0]), "p": float(r[1]), "n": int(ok.sum()), "method": method}
    if method == "pearson" and ok.sum() > 3 and abs(out["r"]) < 1:
        z = math.atanh(out["r"])
        se = 1 / math.sqrt(ok.sum() - 3)
        out["ci95"] = (math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se))
    return out


@_quiet
def regression(x, y, alpha: float = 0.05, n_band: int = 60) -> dict:
    """Least-squares line y = slope·x + intercept with confidence intervals and a confidence band."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 3 or np.ptp(x) == 0:
        return {"n": n, "slope": math.nan, "intercept": math.nan, "r2": math.nan, "p": math.nan,
                "band_x": None, "band_y": None, "band_lo": None, "band_hi": None}
    r = sps.linregress(x, y)
    resid = y - (r.intercept + r.slope * x)
    s = math.sqrt((resid ** 2).sum() / (n - 2))
    tq = sps.t.ppf(1 - alpha / 2, n - 2)
    sxx = ((x - x.mean()) ** 2).sum()
    bx = np.linspace(x.min(), x.max(), n_band)
    by = r.intercept + r.slope * bx
    half = tq * s * np.sqrt(1 / n + (bx - x.mean()) ** 2 / sxx)
    return {"n": n, "slope": float(r.slope), "intercept": float(r.intercept), "r2": float(r.rvalue ** 2),
            "p": float(r.pvalue), "slope_se": float(r.stderr), "intercept_se": float(r.intercept_stderr),
            "slope_ci": (float(r.slope - tq * r.stderr), float(r.slope + tq * r.stderr)),
            "intercept_ci": (float(r.intercept - tq * r.intercept_stderr),
                             float(r.intercept + tq * r.intercept_stderr)),
            "residual_se": s, "band_x": bx, "band_y": by, "band_lo": by - half, "band_hi": by + half}


# ---------------------------------------------------------------- assumption checks
@_quiet
def normality(values) -> dict:
    """Shapiro-Wilk (n ≥ 3) and D'Agostino-Pearson K² (n ≥ 8) normality tests: {name: p}."""
    a = _clean(values)
    out = {}
    if len(a) >= 3 and np.ptp(a) > 0:
        out["Shapiro-Wilk"] = float(sps.shapiro(a).pvalue)
    if len(a) >= 8 and np.ptp(a) > 0:
        out["D'Agostino-Pearson"] = float(sps.normaltest(a).pvalue)
    return out


@_quiet
def variance_tests(groups: "dict[str, np.ndarray]") -> dict:
    """Equality of variances: Levene (mean), Brown-Forsythe (median), Bartlett, Fligner-Killeen: {name: p}."""
    data = [d for d in (_clean(v) for v in groups.values()) if len(d) >= 2]
    if len(data) < 2:
        return {}
    out = {}
    for name, fn in (("Levene", lambda: sps.levene(*data, center="mean")),
                     ("Brown-Forsythe", lambda: sps.levene(*data, center="median")),
                     ("Bartlett", lambda: sps.bartlett(*data)), ("Fligner-Killeen", lambda: sps.fligner(*data))):
        try:
            p = float(fn().pvalue)
            out[name] = p
        except Exception:  # pragma: no cover - degenerate data
            pass
    return out


# ---------------------------------------------------------------- formatting
def format_p(p: float) -> str:
    if p is None or not math.isfinite(p):
        return "n/a"
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}"


def stars(p: float) -> str:
    if p is None or not math.isfinite(p):
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def summary_text(res: dict, measure: str) -> str:
    lines = [f"{measure}"]
    for g, d in res.get("descriptive", {}).items():
        lines.append(f"  {g}: n={d['n']}, mean={d['mean']:.3f} ± {d['sem']:.3f} SEM (SD {d['sd']:.3f}), "
                     f"median={d['median']:.3f}")
    stat = res.get("statistic", math.nan)
    df = res.get("df")
    if isinstance(df, tuple):
        df = ", ".join(f"{v:.4g}" for v in df)
    elif isinstance(df, float):
        df = f"{df:.2f}"
    dfs = f", df={df}" if df is not None else ""
    lines.append(f"  {res.get('test')}: statistic={stat:.3f}{dfs}, {format_p(res.get('p'))} {stars(res.get('p'))}")
    if "p_gg" in res:
        lines.append(f"  Greenhouse-Geisser: epsilon={res['epsilon_gg']:.3f}, {format_p(res['p_gg'])}")
    if "effect_size" in res and res["effect_size"] == res["effect_size"]:
        lines.append(f"  {res.get('effect_size_name')}: {res['effect_size']:.3f}")
    for name, v in (res.get("effect_sizes") or {}).items():
        if name != res.get("effect_size_name") and v == v:
            lines.append(f"  {name}: {v:.3f}")
    for ph in res.get("posthoc", []):
        lines.append(f"    {ph['a']} vs {ph['b']}: {format_p(ph['p'])} {stars(ph['p'])} ({ph['test']})")
    return "\n".join(lines)


def anova_text(res: dict, measure: str) -> str:
    if "error" in res:
        return f"{measure}: {res['error']}"
    lines = [f"{measure}: {res.get('test', 'ANOVA')} ({' × '.join(res.get('factors', []))})"]
    if "epsilon_gg" in res:
        lines[0] += f", Greenhouse-Geisser epsilon = {res['epsilon_gg']:.3f}"
    for e in res.get("effects", []):
        if "H" in e:
            lines.append(f"  {e['effect']}: H({e['df']}) = {e['H']:.3f}, {format_p(e['p'])} {stars(e['p'])}")
        else:
            lines.append(f"  {e['effect']}: F({e['df']}, {e.get('df_error', res.get('df_residual'))}) = "
                         f"{e['F']:.3f}, {format_p(e['p'])} {stars(e['p'])}"
                         + (f" (GG {format_p(e['p_gg'])})" if "p_gg" in e else ""))
    return "\n".join(lines)
