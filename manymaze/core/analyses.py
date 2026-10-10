"""Statistical analyses of result rows as reported by the Statistics page: group comparisons with post-hoc tests,
two-factor / repeated-measures designs, correlation and regression, grouped descriptive statistics and categorical
tests.

Each analysis returns an :class:`Analysis` — title, headline (HTML), tables, figure and a plain-text summary — which
the page renders, copies and saves as a report, and the HTML report of the experiment reuses.

The rows passed in are already restricted to the tests to include (time period, filter); `period` and `filt` only
describe that restriction in the titles and summaries.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from html import escape

import numpy as np
from matplotlib.figure import Figure

from . import plots
from . import stats as st
from .stats import ALPHA, format_p, is_number, stars

WHOLE = "Whole test"
NONE = "(none)"
# how factors and choices are shown (internal names stay: the "Group" column holds the treatment)
DISPLAY = {"Group": "Treatment", NONE: "- None -", "(all rows)": "- All tests -"}
STAT_NAMES = {"Welch's t-test": "t", "Student's t-test": "t", "Paired t-test": "t", "Mann-Whitney U": "U",
              "Wilcoxon signed-rank": "W", "One-way ANOVA": "F", "Welch's ANOVA": "F", "Kruskal-Wallis": "H",
              "Friedman": "χ²", "Repeated-measures ANOVA": "F", "Kolmogorov-Smirnov": "D", "Brunner-Munzel": "W",
              "Alexander-Govern": "A", "Mood's median test": "χ²", "One-sample t-test": "t",
              "Wilcoxon signed-rank vs value": "W"}
DESC_KEYS = ["n", "mean", "sd", "sem", "ci95", "median", "min", "max"]
DESC_HEADERS = ["n", "Mean", "SD", "SEM", "95% CI", "Median", "Min", "Max"]


@dataclass
class Table:
    name: str
    headers: list[str]
    rows: list[list]  # str cells are shown as they are, numbers through fmt()

    def cells(self) -> list[list[str]]:
        return [[cell(v) for v in r] for r in self.rows]

    def text(self) -> str:
        """Tab-separated, as shown (for the clipboard)."""
        return "\n".join("\t".join(r) for r in [self.headers] + self.cells()) + "\n"


@dataclass
class Analysis:
    title: str = ""
    subtitle: str = ""
    headline: str = ""  # HTML
    tables: list[Table] = field(default_factory=list)
    figure: Figure | None = None
    summary_text: str = ""
    result: object = None  # the statistics (dict, or the grouped rows)


# ---------------------------------------------------------------- formatting
def label(v) -> str:
    return DISPLAY.get(v, str(v))


def fmt(v, nd=None) -> str:
    """Numbers with 4 significant figures (at most 3 decimals), like the tables of ANY-maze's reports."""
    if v is None:
        return ""
    if isinstance(v, (tuple, list)):
        return ", ".join(fmt(x, nd) for x in v)
    if is_number(v):
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        if not math.isfinite(v):
            return "–"
        if nd is None:
            a = abs(float(v))
            nd = 3 if a < 10 else 2 if a < 100 else 1 if a < 1000 else 0
        return f"{float(v):.{nd}f}"
    return str(v)


def cell(v) -> str:
    return v if isinstance(v, str) else fmt(v)


def p_text(p, alpha: float = ALPHA) -> str:
    s = stars(p, alpha)
    return f"{format_p(p)} {s}".strip() if s else format_p(p)


def significance_level(project) -> float:
    """The significance level set on the Statistics page (Project.statistics["alpha"]; 0.05 by default)."""
    return st.significance_level((getattr(project, "statistics", None) or {}).get("alpha", ALPHA))


def _alpha_note(alpha: float) -> str:
    """How a significance level other than the usual 0.05 is mentioned under a title."""
    return "" if alpha == ALPHA else f"α = {alpha:g}"


def context(period: str | None = None, filt: tuple | None = None, factor: str | None = None) -> str:
    """The tests included, as shown under a title: the period (unless it is the factor) and the filter."""
    ctx = []
    if factor != "Period" and period is not None:
        ctx.append(str(period))
    if filt is not None:
        ctx.append(f"{filt[0]} = {filt[1]}")
    return " · ".join(ctx)


def _with(text: str, extra: str) -> str:
    return text + (f" · {extra}" if extra else "")


# ---------------------------------------------------------------- levels and data
def _period_key(label_: str):
    try:
        return (0, float(str(label_).split()[0].split("-")[0]))
    except (ValueError, IndexError):
        return (1, 0.0)


# levels of the test-time information columns in calendar / clock order
CLOCK_ORDER = {"Day of week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
               "Time of day": ["Morning", "Afternoon", "Evening", "Night"]}


def level_order(project, rows: list[dict], col: str) -> list[str]:
    """The distinct values of a column in display order: treatments and stages as in the experiment, periods in
    time order (whole test first), trials numerically."""
    vals = list(OrderedDict.fromkeys(str(r.get(col, "")) for r in rows))
    if col in ("Group", "Stage") and project is not None:
        order = [g.name for g in project.groups] if col == "Group" else list(project.stages)
        vals.sort(key=lambda v: (order.index(v) if v in order else len(order), v))
    elif col == "Period":
        vals.sort(key=lambda v: (v != WHOLE, _period_key(v)))
    elif col == "Trial":
        vals.sort(key=lambda v: (0, float(v)) if v.replace(".", "", 1).isdigit() else (1, v))
    elif col in CLOCK_ORDER:
        order = CLOCK_ORDER[col]
        vals.sort(key=lambda v: order.index(v) if v in order else len(order))
    return vals


def group_colors(project, factor: str | None) -> dict:
    if factor == "Group" and project is not None:
        return {g.name: g.color for g in project.groups}
    return {}


def _y_range(project, measure: str, fig, values):
    """The graph Y axis range of a calculation (Calculation.y_min / y_max) on its graphs, as ANY-maze: kept only
    while every plotted result is inside it."""
    from .calculations import apply_y_range, y_range

    apply_y_range(fig, y_range(getattr(project, "calculations", None) or [], measure), values)


def _finite(v) -> bool:
    return is_number(v) and math.isfinite(float(v))


def group_data(project, rows: list[dict], measure: str, factor: str, paired: bool) -> "OrderedDict[str, np.ndarray]":
    """Values of the measure per level of the factor. paired: one value per animal measured at every level
    (the mean of its tests at that level), animals in the same order at each level."""
    levels = [lv for lv in level_order(project, rows, factor) if lv != ""]
    if factor == "Period":
        levels = [lv for lv in levels if lv != WHOLE] or levels
    if not paired:
        out = OrderedDict()
        for lv in levels:
            vals = [float(r[measure]) for r in rows if str(r.get(factor, "")) == lv and _finite(r.get(measure))]
            if vals:
                out[lv] = np.asarray(vals)
        return out
    per_animal: dict[str, dict[str, list]] = {}
    for r in rows:
        v = r.get(measure)
        lv = str(r.get(factor, ""))
        if lv in levels and _finite(v):
            per_animal.setdefault(str(r.get("Animal", "")), {}).setdefault(lv, []).append(float(v))
    animals = sorted(a for a, d in per_animal.items() if all(lv in d for lv in levels))
    return OrderedDict((lv, np.asarray([np.mean(per_animal[a][lv]) for a in animals])) for lv in levels if animals)


def _desc_row(d: dict) -> list:
    return [d[k] for k in DESC_KEYS]


# ---------------------------------------------------------------- compare groups
def compare(project, rows: list[dict], measure: str, factor: str, method: str = "auto", parametric: bool = True,
            paired: bool = False, posthoc: str = "auto", control: str | None = None, mu: float = 0.0,
            plot: str = "bar", error: str = "sem", points: bool = True, period: str | None = None,
            filt: tuple | None = None, alpha: float = ALPHA) -> Analysis | None:
    """The measure compared between the levels of a factor (t-test / ANOVA / rank tests chosen automatically or
    `method`), with post-hoc tests, assumption checks and a group graph; one-sample tests vs `mu`. alpha: the
    significance level of the "significant" marks (colour, stars, graph brackets, failed assumption checks)."""
    if not measure or not factor:
        return None
    paired = paired or method in st.PAIRED_METHODS
    gv = group_data(project, rows, measure, factor, paired)
    one = method in ("one_t", "one_wilcoxon")
    if one:
        res = {"groups": list(gv), "descriptive": {k: st.descriptive(v) for k, v in gv.items()}, "posthoc": [],
               "one_sample": {k: st.one_sample(v, mu, method == "one_t") for k, v in gv.items()}, "mu": mu,
               "test": "One-sample t-test" if method == "one_t" else "Wilcoxon signed-rank vs value"}
        ps = [r["p"] for r in res["one_sample"].values() if r["p"] == r["p"]]
        res["p"] = min(ps) if ps else math.nan
        res["statistic"] = math.nan
    else:
        res = st.compare_groups(gv, parametric=parametric, paired=paired, method=method, posthoc_method=posthoc,
                                control=control)
    gv = OrderedDict((k, gv[k]) for k in res["groups"] if k in gv)
    if gv:
        fig = plots.group_plot(gv, measure, group_colors(project, factor), kind=plot,
                               posthoc=None if one else res.get("posthoc"), p_value=None if one else res.get("p"),
                               error=error, points=points, ref_value=res.get("mu") if one else None, alpha=alpha)
        _y_range(project, measure, fig, [v for vs in gv.values() for v in vs])
    else:
        fig = plots.message_figure(f"No values of “{measure}”")
    # headline
    p = res.get("p", math.nan)
    html = f"<div style='font-size:16px'><b>{escape(str(res.get('test')))}</b></div>"
    if one:
        lines = []
        for g, r in res["one_sample"].items():
            sym = STAT_NAMES.get(r.get("test"), "stat")
            lines.append(f"{escape(g)}: {sym} = {fmt(r.get('statistic'))}, {escape(p_text(r.get('p'), alpha))}, "
                         f"d = {fmt(r.get('effect_size'))}")
        html += f"<div style='font-size:13px'>vs {res['mu']:g}<br>{'<br>'.join(lines)}</div>"
    else:
        sym = STAT_NAMES.get(res.get("test"), "statistic")
        parts = []
        if res.get("statistic") == res.get("statistic") and res.get("statistic") is not None:
            parts.append(f"{sym} = {res['statistic']:.3f}")
        if res.get("df") is not None:
            parts.append(f"df = {fmt(res['df'], 2)}")
        parts.append(format_p(p))
        colour = "#16a34a" if p == p and p < alpha else "#475569"
        html += (f"<div style='font-size:14px'>{escape(', '.join(parts))} "
                 f"<b style='color:{colour}'>{stars(p, alpha)}</b></div>")
        if "p_gg" in res:
            html += f"<div>Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}, {escape(format_p(res['p_gg']))}</div>"
        es = dict(res.get("effect_sizes") or {})
        if res.get("effect_size") is not None and res["effect_size"] == res["effect_size"]:
            es = {res.get("effect_size_name"): res["effect_size"], **es}
        es = {k: v for k, v in es.items() if v == v}
        if es:
            html += "<div>" + ", ".join(f"{escape(str(k))} = {v:.3f}" for k, v in es.items()) + "</div>"
    n_total = sum(d["n"] for d in res["descriptive"].values())
    sub = _with(f"by {label(factor)}", context(period, filt, factor))
    if paired:
        sub += " · same animals at each level"
    sub = _with(sub, _alpha_note(alpha))
    checks = []
    for g, v in gv.items():
        for name, pv in st.normality(v).items():
            checks.append([f"{name} (normality)", g, fmt(pv), "non-normal" if pv < alpha else ""])
    for name, pv in st.variance_tests(gv).items():
        checks.append([f"{name} (equal variances)", "all", fmt(pv), "unequal" if pv < alpha else ""])
    tables = [Table("Descriptive statistics", [label(factor)] + DESC_HEADERS,
                    [[g] + _desc_row(d) for g, d in res["descriptive"].items()]),
              Table("Post-hoc comparisons", ["Comparison", "Difference", "p", "", "Method"],
                    [[f"{x['a']} vs {x['b']}", fmt(x.get("diff")), format_p(x["p"]), stars(x["p"], alpha),
                      x.get("test", "")] for x in res.get("posthoc") or []]),
              Table("Assumption checks", ["Check", "Group", "p", ""], checks)]
    # summary
    head = f"Compared by {factor}"
    if factor != "Period" and period is not None:
        head += f", period: {period}"
    if filt is not None:
        head += f", only {filt[0]} = {filt[1]}"
    if alpha != ALPHA:
        head += f", significance level {alpha:g}"
    if one:
        lines = [measure, f"  {res['test']} vs {res['mu']:g}"]
        for g, r in res["one_sample"].items():
            lines.append(f"    {g}: n={r['descriptive']['n']}, statistic={r['statistic']:.3f}, "
                         f"{format_p(r['p'])} {stars(r['p'], alpha)}")
        text = "\n".join(lines)
    else:
        text = st.summary_text(res, measure, alpha)
    check_lines = [f"    {c[0]} {c[1]}: {format_p(float(c[2])) if c[2] not in ('', '–') else 'n/a'}" for c in checks]
    summary = f"{head}\n{text}" + ("\n  Assumption checks:\n" + "\n".join(check_lines) if check_lines else "")
    return Analysis(measure, f"{sub} · N = {n_total}", html, tables, fig, summary, res)


# ---------------------------------------------------------------- two factors
def two_factor(project, rows: list[dict], measure: str, x: str, by: str | None = None, design: str = "between",
               plot: str = "line", error: str = "sem", points: bool = True, period: str | None = None,
               filt: tuple | None = None, alpha: float = ALPHA) -> Analysis:
    """The measure across the levels of `x` (stages, trials, periods), optionally by a 2nd factor: two-way /
    mixed / rank-based ANOVA, or a repeated-measures ANOVA over `x` alone (design "mixed"); effects significant at
    the level alpha are starred."""
    if not measure or not x:
        return Analysis(figure=plots.message_figure("Needs stages, trials or time periods"))
    if x == "Period":
        rows = [r for r in rows if r.get("Period") != WHOLE]
    rows = [r for r in rows if is_number(r.get(measure))]
    by = by if by and by != NONE else None
    sub = _with(_with(f"by {label(x)}" + (f" and {label(by)}" if by else ""), context(period, filt, x)),
                _alpha_note(alpha))
    if by is None:
        rows = [{**r, "_all": "All"} for r in rows]
    if not rows:
        return Analysis(measure, sub, figure=plots.message_figure(f"No values of “{measure}”"))
    order = level_order(project, rows, x)
    rows = sorted(rows, key=lambda r: order.index(str(r.get(x, ""))) if str(r.get(x, "")) in order else 0)
    if plot == "line":
        fig = plots.time_course(rows, measure, x=x, by=by or "_all", colors=group_colors(project, by),
                                size=(5.2, 3.6), error=error, points=points, order=order)
    else:
        fig = plots.factor_plot(rows, measure, [x] + ([by] if by else []), kind=plot, error=error, points=points,
                                colors=group_colors(project, by),
                                orders={f: level_order(project, rows, f) for f in (x, by) if f}, size=(5.4, 3.6))
    _y_range(project, measure, fig, [r.get(measure) for r in rows])
    if by is None:
        if design != "mixed":
            hint = (f"<b>{escape(measure)}</b> across {escape(label(x))}.<br>Optionally select a 2nd independent "
                    "variable for a two-factor analysis, or the “Repeated” design for a repeated-measures ANOVA.")
            return Analysis(measure, sub, hint, figure=fig)
        res = st.rm_anova(rows, measure, within=x, levels=order)
        if "error" not in res:
            res["effects"] = [{"effect": x, "SS": res["SS"], "df": res["df"][0], "F": res["F"], "p": res["p"],
                               "p_gg": res["p_gg"], "df_error": res["df"][1]}]
            res["factors"] = [x]
        title, desc = "Repeated-measures ANOVA", f"Within animals: {label(x)}"
    elif design == "mixed":
        res = st.mixed_anova(rows, measure, between=by, within=x, levels=order)
        desc = f"{label(by)} (between animals) × {label(x)} (within animals)"
    elif design == "srh":
        res = st.scheirer_ray_hare(rows, measure, factor_a=by, factor_b=x)
        desc = f"{label(by)} × {label(x)}, rank-based (H statistics, χ² p-values)"
    elif design == "art":
        res = st.art_anova(rows, measure, factor_a=by, factor_b=x)
        desc = f"{label(by)} × {label(x)}, aligned rank transform"
    else:
        res = st.two_way_anova(rows, measure, factor_a=by, factor_b=x)
        desc = f"{label(by)} × {label(x)} (between-subjects, type II SS)"
    if by is not None:
        title = res.get("test", "Two-way ANOVA")
    if "error" in res:
        err = " ".join(label(w) for w in str(res["error"]).split(" "))
        return Analysis(measure, sub, f"<div style='font-size:16px'><b>{escape(title)}</b></div>{escape(err)}",
                        figure=fig, result=res)
    extra = f", residual df = {res['df_residual']}" if res.get("df_residual") is not None else ""
    if "epsilon_gg" in res:
        extra += f", Greenhouse-Geisser ε = {res['epsilon_gg']:.3f}"
    h = any("H" in e for e in res["effects"])
    table = Table("Analysis of variance", ["Effect", "SS", "df", "H" if h else "F", "p", "", "p (GG)"],
                  [[" × ".join(label(f) for f in str(e["effect"]).split(" × ")), e["SS"], e["df"], e.get("H", e["F"]),
                    format_p(e["p"]), stars(e["p"], alpha), format_p(e["p_gg"]) if "p_gg" in e else ""]
                   for e in res["effects"]])
    text = st.anova_text(res, measure, alpha)
    if res.get("design", "between") == "between" and "df_residual" in res:
        text = text.replace("Two-way ANOVA", "two-way ANOVA", 1) + f"\n  residual df = {res['df_residual']}"
    return Analysis(measure, sub, f"<div style='font-size:16px'><b>{escape(title)}</b></div>{escape(desc)}{extra}",
                    [table], fig, text, res)


# ---------------------------------------------------------------- correlation
def correlate(project, rows: list[dict], mx: str, my: str, method: str = "pearson", by: str | None = None,
              period: str | None = None, filt: tuple | None = None, alpha: float = ALPHA) -> Analysis | None:
    """Correlation (Pearson / Spearman / Kendall) and linear regression of two measures; result["regression"]
    holds the regression. alpha: the significance level of the stars."""
    if not mx or not my:
        return None
    rows = [r for r in rows if is_number(r.get(mx)) and is_number(r.get(my))]
    xs = [float(r[mx]) for r in rows]
    ys = [float(r[my]) for r in rows]
    res = st.correlation(xs, ys, method)
    res.setdefault("method", method)
    g = res["regression"] = st.regression(xs, ys)
    by = by if by and by != NONE else None
    groups = [str(r.get(by, "")) if by else "" for r in rows]
    if rows:
        fig = plots.scatter_plot(xs, ys, groups, mx, my, group_colors(project, by), res, g)
    else:
        fig = plots.message_figure("No paired values")
    name = {"pearson": "Pearson r", "spearman": "Spearman ρ", "kendall": "Kendall τ"}[method]
    r = res.get("r", math.nan)
    p = res.get("p", math.nan)
    strength = ""
    if r == r:
        a = abs(r)
        strength = "very strong" if a >= 0.8 else "strong" if a >= 0.6 else "moderate" if a >= 0.4 else \
            "weak" if a >= 0.2 else "negligible"
        strength = f"{strength} {'positive' if r > 0 else 'negative'} correlation"
    ci = f"<div>95% CI {res['ci95'][0]:.3f} to {res['ci95'][1]:.3f}</div>" if "ci95" in res else ""
    reg = ""
    if g.get("slope") == g.get("slope"):
        reg = (f"<br><b>Linear regression</b><div>y = {g['slope']:.4g}·x + {g['intercept']:.4g}</div>"
               f"<div>slope 95% CI {g['slope_ci'][0]:.4g} to {g['slope_ci'][1]:.4g}</div>"
               f"<div>R² = {g['r2']:.3f}, {escape(format_p(g['p']))}</div>")
    # ANCOVA: compare the levels of the colouring factor on Y, adjusted for X as the covariate
    anc = ""
    if by and len(set(groups)) >= 2:
        a = st.ancova(rows, my, mx, by)
        if "error" in a:
            anc = f"<br><b>ANCOVA</b><div>{escape(str(a['error']))}</div>"
        else:
            res["ancova"] = a
            eff = a["effects"][0]
            anc = (f"<br><b>ANCOVA</b> ({escape(label(by))} adjusted for {escape(mx)})"
                   f"<div>F({eff['df']}, {eff['df_error']}) = {eff['F']:.3f}, {escape(format_p(eff['p']))} "
                   f"{stars(eff['p'], alpha)}</div>"
                   + "".join(f"<div>{escape(label(lv))}: adjusted mean {d['adjusted_mean']:.4g} ± "
                             f"{d['adjusted_se']:.3g} SE</div>" for lv, d in a["adjusted_means"].items()))
            sl = a.get("slopes")
            if sl:
                anc += (f"<div>Homogeneity of slopes: {escape(format_p(sl['p']))}"
                        + (" — <span style='color:#dc2626'>slopes differ</span>" if sl["p"] < alpha else "")
                        + "</div>")
    html = (f"<div style='font-size:16px'><b>{name} = {fmt(r)}</b></div>"
            f"<div style='font-size:14px'>{escape(p_text(p, alpha))}, n = {res.get('n', 0)}</div>{ci}"
            f"<div>{strength}</div>"
            f"{reg}{anc}")
    name = {"pearson": "Pearson r", "spearman": "Spearman rho", "kendall": "Kendall tau"}[method]
    text = f"{mx} vs {my}: {name} = {res['r']:.3f}, {format_p(res['p'])}, n = {res['n']}"
    if g.get("slope") == g.get("slope") and g.get("slope") is not None:
        text += (f"\n  Linear regression: slope = {g['slope']:.4g} (95% CI {g['slope_ci'][0]:.4g} to "
                 f"{g['slope_ci'][1]:.4g}), intercept = {g['intercept']:.4g}, R² = {g['r2']:.3f}, {format_p(g['p'])}")
    if "ancova" in res:
        text += "\n" + st.ancova_text(res["ancova"], my, alpha)
    return Analysis(f"{my} against {mx}", _with(_with(f"N = {len(rows)}", context(period, filt)),
                                                 _alpha_note(alpha)), html, [], fig, text, res)


# ---------------------------------------------------------------- grouped descriptive statistics
def grouped(project, rows: list[dict], measure: str, factors: list[str], plot: str = "bar", error: str = "sem",
            points: bool = True, period: str | None = None, filt: tuple | None = None) -> Analysis | None:
    """Descriptive statistics of the measure for every combination of up to three factors, with a graph
    (1st factor on the x axis, 2nd as colours, 3rd as panels); result = st.describe_by rows."""
    if not measure or not factors:
        return None
    if "Period" in factors:
        rows = [r for r in rows if r.get("Period") != WHOLE] or rows
    orders = {f: level_order(project, rows, f) for f in factors}
    sub = _with("by " + " and ".join(label(f) for f in factors),
                context(period, filt, "Period" if "Period" in factors else None))
    res = st.describe_by(rows, measure, factors, orders)
    fig = plots.factor_plot(rows, measure, factors, kind=plot, error=error, points=points,
                            colors=group_colors(project, factors[1] if len(factors) > 1 else factors[0]),
                            orders=orders, size=(6.4, 3.8))
    _y_range(project, measure, fig, [r.get(measure) for r in rows])
    table = Table("Descriptive statistics", [label(f) for f in factors] + DESC_HEADERS,
                  [[d[f] for f in factors] + _desc_row(d) for d in res])
    lines = [f"{measure} by {' > '.join(factors)}"]
    for d in res:
        lab = " / ".join(d[f] for f in factors)
        lines.append(f"  {lab}: n={d['n']}, mean={d['mean']:.3f}, SD={d['sd']:.3f}, SEM={d['sem']:.3f}")
    return Analysis(measure, sub, "", [table], fig, "\n".join(lines) if res else "", res)


# ---------------------------------------------------------------- categorical
def categorical(project, rows: list[dict], rf: str, cf: str, period: str | None = None,
                filt: tuple | None = None, alpha: float = ALPHA) -> Analysis:
    """Counts of a categorical result (or factor) `cf` per level of `rf`, with χ² / G / Fisher's exact tests (stars
    at the significance level alpha)."""
    if not rf or not cf:
        return Analysis(figure=plots.message_figure("No categorical results (e.g. search strategy) in this "
                                                    "experiment"))
    if rf == cf:
        return Analysis(figure=plots.message_figure("Choose a category different from the rows"))
    rl, cl, T = st.contingency_table(rows, rf, cf)
    res = st.categorical_test(T, rl, cl)
    fig = plots.proportions_figure(rl, cl, T) if len(rl) and len(cl) else plots.message_figure("No data")
    table = Table("Counts", [label(rf)] + cl + ["Total"],
                  [[r] + [int(v) for v in row] + [int(row.sum())] for r, row in zip(rl, T)])
    html = f"<div style='font-size:16px'><b>{escape(str(res.get('test')))}</b></div>"
    text = ""
    if res.get("p") == res.get("p"):
        html += (f"<div style='font-size:14px'>χ²({res['df']}) = {res['statistic']:.3f}, "
                 f"{escape(p_text(res['p'], alpha))}</div><div>Cramér's V = {res['effect_size']:.3f}</div>")
        if "g_test" in res:
            html += f"<div>G-test: G = {res['g_test']['statistic']:.3f}, {escape(format_p(res['g_test']['p']))}</div>"
        if "fisher" in res:
            odds = res["fisher"]["odds_ratio"]
            html += (f"<div>Fisher's exact test: odds ratio = {'∞' if odds == math.inf else fmt(odds)}, "
                     f"{escape(p_text(res['fisher']['p'], alpha))}</div>")
        if res.get("low_expected"):
            html += ("<div style='color:#b45309'>Some expected counts are below 5: prefer Fisher's exact test "
                     "(2 × 2) or pool categories.</div>")
        text = (f"{cf} by {rf}: chi-square({res['df']}) = {res['statistic']:.3f}, {format_p(res['p'])}, "
                f"Cramér's V = {res['effect_size']:.3f}")
        if "fisher" in res:
            text += f"\n  Fisher's exact test: {format_p(res['fisher']['p'])}"
    if len(cl) > 1:  # are the categories equally frequent overall? (also with a single row level)
        gof = st.chi_square_gof(T.sum(axis=0))
        res["goodness_of_fit"] = gof
        if gof.get("p") == gof.get("p"):
            html += (f"<div>Goodness of fit (equal proportions of {escape(label(cf))}): "
                     f"χ²({gof['df']}) = {gof['statistic']:.3f}, {escape(p_text(gof['p'], alpha))}</div>")
            piece = (f"Chi-square goodness of fit (equal proportions): chi-square({gof['df']}) = "
                     f"{gof['statistic']:.3f}, {format_p(gof['p'])}")
            text = f"{text}\n  {piece}" if text else piece
    return Analysis(f"{label(cf)} by {label(rf)}", _with(context(period, filt), _alpha_note(alpha)), html, [table],
                    fig, text, res)
