"""Group statistics for results tables."""

from __future__ import annotations

import itertools
import math
from collections import OrderedDict

import numpy as np
from scipy import stats as sps


def _clean(v) -> np.ndarray:
    a = np.asarray([x for x in v if isinstance(x, (int, float, np.number)) and not isinstance(x, bool)], float)
    return a[np.isfinite(a)]


def descriptive(values) -> dict:
    a = _clean(values)
    n = len(a)
    if n == 0:
        return {"n": 0, "mean": math.nan, "sd": math.nan, "sem": math.nan, "median": math.nan, "min": math.nan,
                "max": math.nan, "q1": math.nan, "q3": math.nan}
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    return {"n": n, "mean": float(a.mean()), "sd": sd, "sem": sd / math.sqrt(n) if n > 1 else math.nan,
            "median": float(np.median(a)), "min": float(a.min()), "max": float(a.max()),
            "q1": float(np.percentile(a, 25)), "q3": float(np.percentile(a, 75))}


def group_values(rows: list[dict], measure: str, by: str = "Group") -> "OrderedDict[str, np.ndarray]":
    out: OrderedDict[str, list] = OrderedDict()
    for r in rows:
        key = str(r.get(by, ""))
        v = r.get(measure)
        if isinstance(v, (int, float, np.number)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out.setdefault(key, []).append(float(v))
    return OrderedDict((k, np.asarray(v)) for k, v in out.items())


def compare_groups(groups: "dict[str, np.ndarray]", parametric: bool = True, paired: bool = False) -> dict:
    """Compare a measure between groups.

    2 groups: Welch t-test / Mann-Whitney U (paired: paired t / Wilcoxon).
    >2 groups: one-way ANOVA + Tukey HSD / Kruskal-Wallis + Bonferroni-corrected Mann-Whitney
    (paired: repeated-measures via Friedman).
    """
    names = [k for k, v in groups.items() if len(_clean(v)) > 0]
    data = [_clean(groups[k]) for k in names]
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
    if len(names) == 2:
        a, b = data
        if paired:
            n = min(len(a), len(b))
            if parametric:
                r = sps.ttest_rel(a[:n], b[:n])
                out.update(test="Paired t-test", statistic=float(r.statistic), p=float(r.pvalue), df=n - 1)
            else:
                r = sps.wilcoxon(a[:n], b[:n])
                out.update(test="Wilcoxon signed-rank", statistic=float(r.statistic), p=float(r.pvalue))
        elif parametric:
            r = sps.ttest_ind(a, b, equal_var=False)
            out.update(test="Welch's t-test", statistic=float(r.statistic), p=float(r.pvalue),
                       df=float(getattr(r, "df", len(a) + len(b) - 2)))
        else:
            r = sps.mannwhitneyu(a, b, alternative="two-sided")
            out.update(test="Mann-Whitney U", statistic=float(r.statistic), p=float(r.pvalue))
        # effect size (Cohen's d / Hedges' g)
        sp = math.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
        out["effect_size"] = float((a.mean() - b.mean()) / sp) if sp > 0 else math.nan
        out["effect_size_name"] = "Cohen's d"
        return out
    if paired:
        n = min(len(d) for d in data)
        r = sps.friedmanchisquare(*[d[:n] for d in data])
        out.update(test="Friedman", statistic=float(r.statistic), p=float(r.pvalue))
        pairs = []
        m = len(names) * (len(names) - 1) / 2
        for (i, a), (j, b) in itertools.combinations(enumerate(data), 2):
            pv = float(sps.wilcoxon(a[:n], b[:n]).pvalue)
            pairs.append({"a": names[i], "b": names[j], "p": min(1.0, pv * m), "test": "Wilcoxon (Bonferroni)"})
        out["posthoc"] = pairs
        return out
    if parametric:
        r = sps.f_oneway(*data)
        N = sum(len(d) for d in data)
        k = len(data)
        out.update(test="One-way ANOVA", statistic=float(r.statistic), p=float(r.pvalue), df=(k - 1, N - k))
        grand = np.concatenate(data).mean()
        ssb = sum(len(d) * (d.mean() - grand) ** 2 for d in data)
        sst = sum(((d - grand) ** 2).sum() for d in data)
        out["effect_size"] = float(ssb / sst) if sst > 0 else math.nan
        out["effect_size_name"] = "eta²"
        try:
            tk = sps.tukey_hsd(*data)
            for i, j in itertools.combinations(range(k), 2):
                out["posthoc"].append({"a": names[i], "b": names[j], "diff": float(data[i].mean() - data[j].mean()),
                                       "p": float(tk.pvalue[i, j]), "test": "Tukey HSD"})
        except Exception:  # pragma: no cover
            pass
    else:
        r = sps.kruskal(*data)
        out.update(test="Kruskal-Wallis", statistic=float(r.statistic), p=float(r.pvalue), df=len(data) - 1)
        m = len(names) * (len(names) - 1) / 2
        for (i, a), (j, b) in itertools.combinations(enumerate(data), 2):
            pv = float(sps.mannwhitneyu(a, b, alternative="two-sided").pvalue)
            out["posthoc"].append({"a": names[i], "b": names[j], "p": min(1.0, pv * m),
                                   "test": "Mann-Whitney (Bonferroni)"})
    return out


def two_way_anova(rows: list[dict], measure: str, factor_a: str = "Group", factor_b: str = "Stage") -> dict:
    """Between-subjects two-way ANOVA with Type II sums of squares."""
    recs = []
    for r in rows:
        v = r.get(measure)
        if isinstance(v, (int, float, np.number)) and not isinstance(v, bool) and math.isfinite(float(v)):
            recs.append((str(r.get(factor_a, "")), str(r.get(factor_b, "")), float(v)))
    if len(recs) < 4:
        return {"error": "Not enough data"}
    A = sorted({r[0] for r in recs})
    B = sorted({r[1] for r in recs})
    if len(A) < 2 or len(B) < 2:
        return {"error": f"Need at least 2 levels of {factor_a} and {factor_b}"}
    y = np.array([r[2] for r in recs])

    def dummies(levels, vals):
        return np.column_stack([(np.array(vals) == lv).astype(float) for lv in levels[1:]])

    da = dummies(A, [r[0] for r in recs])
    db = dummies(B, [r[1] for r in recs])
    dab = np.column_stack([da[:, i] * db[:, j] for i in range(da.shape[1]) for j in range(db.shape[1])])
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
    if df_res <= 0:
        return {"error": "Not enough residual degrees of freedom"}
    ms_res = rss_full / df_res
    out = {"factors": [factor_a, factor_b], "df_residual": df_res, "effects": []}
    for name, ss, df in ((factor_a, rss_b - rss_ab, rank_ab - rank_b),
                         (factor_b, rss_a - rss_ab, rank_ab - rank_a),
                         (f"{factor_a} × {factor_b}", rss_ab - rss_full, rank_full - rank_ab)):
        if df <= 0:
            continue
        F = (ss / df) / ms_res if ms_res > 0 else math.nan
        p = float(sps.f.sf(F, df, df_res)) if math.isfinite(F) else math.nan
        out["effects"].append({"effect": name, "SS": ss, "df": df, "F": F, "p": p})
    return out


def correlation(x, y, method: str = "pearson") -> dict:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return {"r": math.nan, "p": math.nan, "n": int(ok.sum())}
    r = sps.pearsonr(x[ok], y[ok]) if method == "pearson" else sps.spearmanr(x[ok], y[ok])
    return {"r": float(r[0]), "p": float(r[1]), "n": int(ok.sum()), "method": method}


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
        df = ", ".join(f"{v:g}" for v in df)
    elif isinstance(df, float):
        df = f"{df:.2f}"
    dfs = f", df={df}" if df is not None else ""
    lines.append(f"  {res.get('test')}: statistic={stat:.3f}{dfs}, {format_p(res.get('p'))} {stars(res.get('p'))}")
    if "effect_size" in res and res["effect_size"] == res["effect_size"]:
        lines.append(f"  {res.get('effect_size_name')}: {res['effect_size']:.3f}")
    for ph in res.get("posthoc", []):
        lines.append(f"    {ph['a']} vs {ph['b']}: {format_p(ph['p'])} {stars(ph['p'])} ({ph['test']})")
    return "\n".join(lines)
