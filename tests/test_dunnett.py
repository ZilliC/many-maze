"""Dunnett's test vs control: deterministic p values, exact to numerical precision (H-P02: scipy.stats.dunnett
integrates by randomised quasi-Monte Carlo, so its p changed from one calculation to the next)."""

import math

import numpy as np
import pytest
from scipy import integrate
from scipy import stats as sps

from manymaze.core import stats as st


def _groups(shift, ns=(8, 8, 8), seed=1):
    rng = np.random.default_rng(seed)
    return {f"G{j}": rng.normal(10.0, 2.0, n) + shift * j for j, n in enumerate(ns)}


def _t_stats(groups, control="G0"):
    data = list(groups.values())
    df = sum(len(d) for d in data) - len(data)
    mse = sum(((d - d.mean()) ** 2).sum() for d in data) / df
    c = groups[control]
    return {k: (v.mean() - c.mean()) / math.sqrt(mse * (1 / len(v) + 1 / len(c)))
            for k, v in groups.items() if k != control}, df


def _reference_equal_n(t, m, df):
    """Two-sided Dunnett p for equal n (correlation 1/2), by an independent formulation: 1 - P(all |T_j| < t) with
    Gauss-Hermite quadrature over the shared normal component and adaptive quadrature over s = sqrt(χ²_df / df)."""
    w, wt = np.polynomial.hermite_e.hermegauss(120)
    wt = wt / math.sqrt(2 * math.pi)
    r = math.sqrt(0.5)

    def inside(s):
        a = (t * s - r * w) / r
        b = (-t * s - r * w) / r
        return float(((sps.norm.cdf(a) - sps.norm.cdf(b)) ** m) @ wt)

    def dens(s):
        return 2 * df * s * sps.chi2.pdf(df * s * s, df)
    return 1 - integrate.quad(lambda s: dens(s) * inside(s), 0, 6, epsabs=1e-12, limit=200)[0]


def test_dunnett_is_deterministic():
    g = _groups(3.0)  # large differences: small p, where the Monte Carlo p varied by ±30 %
    first = st.posthoc(g, method="dunnett", control="G0")
    assert all(e["p"] < 0.01 for e in first)
    for _ in range(5):
        assert st.posthoc(g, method="dunnett", control="G0") == first


@pytest.mark.parametrize("shift,ns", [(0.6, (8, 8, 8)), (1.0, (6, 6, 6, 6)), (2.5, (8, 8, 8))])
def test_dunnett_matches_the_exact_value_equal_n(shift, ns):
    g = _groups(shift, ns)
    ts, df = _t_stats(g)
    res = {e["a"]: e for e in st.posthoc(g, method="dunnett", control="G0")}
    assert set(res) == set(ts)
    for name, t in ts.items():
        ref = _reference_equal_n(abs(t), len(ts), df)
        assert res[name]["b"] == "G0" and res[name]["test"] == "Dunnett"
        assert res[name]["p"] == pytest.approx(ref, rel=1e-3 if ref > 1e-4 else 1e-2)


def test_dunnett_critical_values_of_dunnett_1955():
    """At the tabulated two-sided 5 % critical values (Dunnett 1955) p is 0.05 to the table's rounding."""
    for m, df, c in [(2, 20, 2.38), (9, 60, 2.77), (2, 5, 3.03)]:
        p = st._dunnett_p(c, [math.sqrt(0.5)] * m, df)
        assert p == pytest.approx(0.05, abs=6e-4)


def test_dunnett_one_comparison_is_the_t_test():
    """With a single comparison Dunnett's test is the pooled t-test: exact for any p, also very small ones."""
    for df in (3, 10, 40):
        for t in (0.5, 2.0, 5.0, 12.0):
            assert st._dunnett_p(t, [math.sqrt(0.5)], df) == pytest.approx(2 * sps.t.sf(t, df), rel=1e-6)


def test_dunnett_unequal_n_agrees_with_scipy():
    g = _groups(0.8, (20, 6, 10, 15), seed=3)
    ours = [e["p"] for e in st.posthoc(g, method="dunnett", control="G0")]
    data = list(g.values())
    ref = sps.dunnett(*data[1:], control=data[0], random_state=0).pvalue
    for a, b in zip(ours, ref):
        assert a == pytest.approx(b, rel=0.05 if b < 1e-3 else 5e-3)  # scipy's own Monte Carlo error


def test_dunnett_degenerate_groups():
    same = {"A": np.full(4, 2.0), "B": np.full(4, 2.0), "C": np.full(4, 5.0)}
    ps = {e["a"]: e["p"] for e in st.posthoc(same, method="dunnett", control="A")}
    assert ps == {"B": 1.0, "C": 0.0}
    single = {"A": np.array([1.0]), "B": np.array([2.0])}  # no degrees of freedom
    assert math.isnan(st.posthoc(single, method="dunnett")[0]["p"])
