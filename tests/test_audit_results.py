"""Regression tests for the results/export audit findings (p_adjust NaN, I/O strings, CSV text, heat maps)."""

import math

import numpy as np
import pytest

from manymaze.core import plots, templates
from manymaze.core.export import _csv_text, value_text, write_csv
from manymaze.core.iomeasures import _Log
from manymaze.core.stats import p_adjust
from manymaze.core.track import Track


@pytest.mark.parametrize("method", ["bonferroni", "holm", "sidak", "fdr"])
def test_p_adjust_nan_is_ignored(method):
    clean = p_adjust([0.01, 0.02, 0.04], method)
    mixed = p_adjust([0.01, math.nan, 0.02, 0.04], method)
    assert math.isnan(mixed[1])
    assert [mixed[0], mixed[2], mixed[3]] == pytest.approx(clean)
    assert all(math.isnan(v) for v in p_adjust([math.nan, math.nan], method))


def test_io_log_string_values_and_bad_times():
    ev = [{"t": 2, "device": "d", "channel": "c", "value": "off"},
          {"t": 1, "device": "d", "channel": "c", "value": "ON"},
          {"t": None, "device": "d", "channel": "c", "value": 1},
          {"t": 3, "device": "d", "channel": "c", "value": "garbage"},
          {"t": "4", "device": "d", "channel": "c", "value": "true"}]
    log = _Log(ev)
    assert log.series[("input", "d", "c")] == [(1.0, 1.0), (2.0, 0.0), (4.0, 1.0)]


def test_csv_text_keeps_labels_and_guards_formulas():
    for ok in ("+/+", "-ctrl", "-", "+", "WT", "-/-"):
        assert _csv_text(ok) == ok
    for bad in ("=1+1", "@SUM(A1)", "+cmd|' /C calc'!A0", "-2+3(A1)"):
        assert _csv_text(bad) == "'" + bad
    assert _csv_text(-1.5) == "-1.5"


def test_value_text_round_trips():
    for v in (0.1234567890123, 1 / 3, 1e-7, 123456.789012345):
        assert float(value_text(v)) == v
    assert value_text(3.0) == "3" and value_text(math.nan) == ""


def test_write_csv_atomic_leaves_no_temp(tmp_path):
    p = tmp_path / "r.csv"
    write_csv([{"A": "+/+", "B": 1 / 3}], p)
    assert p.read_text().splitlines()[1] == f"+/+,{1 / 3!r}"
    assert [f.name for f in tmp_path.iterdir()] == ["r.csv"]


def test_occupancy_ignores_non_finite(monkeypatch):
    n = 50
    t = np.arange(n) / 25.0
    x = 100 + 10 * np.cos(t)
    y = 100 + 10 * np.sin(t)
    y[5] = math.nan
    tr = Track(t=t, x=x, y=y, area=np.full(n, 300.0), motion=np.full(n, 40.0), fps=25.0)
    w = tr.frame_durations().copy()
    w[10] = math.nan
    monkeypatch.setattr(Track, "frame_durations", lambda self: w)
    app = templates.build("open_field", 50, 50, 300, 300, size_cm=40)
    H, _ = plots.occupancy([tr], app)
    assert np.isfinite(H).all() and H.sum() > 0
