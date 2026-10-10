"""Analysis plug-ins (core.plugins): registration and entry points, running them on tests, their series stored with
the test and analysed like analogue signals (whole test, time periods, zones), their per-test measures, the
built-in CSV / TSV importer (core.datafiles: header rows, delimiters, units, alignment with the test start or a
synchronisation pulse, resampling), the command line and experiments without these fields."""

import json
import math

import numpy as np
import pytest

from manymaze.cli import main as cli_main
from manymaze.core import datafiles, plugins
from manymaze.core import templates
from manymaze.core.procedures import plugins as proc_plugins
from manymaze.core.project import Project
from manymaze.core.track import Track
from manymaze.core.workflow import delete_test, reperform_test


def _project(tmp_path, tracked=True) -> Project:
    """An open-field experiment with one 60 s test (a track that stays in the left half for 30 s, then the right)."""
    p = Project(name="plug", test_duration_s=60.0)
    app = templates.build("open_field", 0, 0, 400, 400, size_cm=40)
    p.apparatus = [app]
    p.ensure_animal("M1")
    p.save(tmp_path / "plug.mmaze")
    t = p.add_test("", "M1", app.name)
    if tracked:
        n = 25 * 60
        tt = np.arange(n) / 25.0
        x = np.where(tt < 30, 100.0, 300.0)
        tr = Track(t=tt, x=x, y=np.full(n, 200.0), detected=np.ones(n, bool), fps=25.0)
        p.save_tracks(t, [tr])
    else:
        t.status = "scored"
        t.duration_s = 60.0
    p.save()
    return p


def _whole(rows):
    return next(r for r in rows if r["Period"] == "Whole test")


@pytest.fixture
def hr_plugin():
    def hr(test, project, options):
        t = np.arange(0, 60, 0.5)
        return {"series": {"Heart rate": (t, np.where(t < 30, 400.0, 600.0))},
                "measures": {"HR: recording quality": options.get("quality", 1.0), "HR: device": "telemetry"}}

    plugins.register_analysis("hr", hr, title="Heart rate", options=[("quality", "Quality", "number", 0.9)])
    yield hr
    plugins.unregister_analysis("hr")


def test_registry_and_procedure_plugins_unchanged(hr_plugin):
    assert plugins.analysis_names()[0] == "csv_import" and "hr" in plugins.analysis_names()
    pl = plugins.analysis_plugin("hr")
    assert pl.title == "Heart rate" and pl.defaults() == {"quality": 0.9}
    cfg = plugins.new_config("hr", ["Heart rate"])
    assert cfg == {"plugin": "hr", "name": "Heart rate 2", "enabled": True, "quality": 0.9}
    with pytest.raises(TypeError):
        plugins.register_analysis("bad", 3)
    # the procedure plug-ins: the same registry under both module names
    proc_plugins.register("echo", lambda a, i: a)
    try:
        assert plugins.get("echo")("x", {}) == "x" and "echo" in proc_plugins.names()
    finally:
        plugins.unregister("echo")
    assert plugins.get("echo") is None


def test_entry_points_are_loaded(monkeypatch):
    class EP:
        name = "from_package"

        @staticmethod
        def load():
            def fn(test, project):  # two arguments: called without the options
                return {"measures": {"Package value": 7}}

            fn.title = "A packaged plug-in"
            return fn

    monkeypatch.setattr(plugins, "_entry_points", lambda group: [EP] if group == plugins.ANALYSIS_GROUP else [])
    monkeypatch.setattr(plugins, "_analysis_loaded", False)
    try:
        pl = plugins.analysis_plugin("from_package")
        assert pl.title == "A packaged plug-in" and pl.call(None, None, {}) == {"measures": {"Package value": 7}}
    finally:
        plugins.unregister_analysis("from_package")


def test_series_and_measures_in_the_results(tmp_path, hr_plugin):
    p = _project(tmp_path)
    p.analysis = type(p.analysis).from_dict({**p.analysis.to_dict(), "bin_length_s": 30.0})
    p.analysis_plugins = [{"plugin": "hr", "name": "HR", "enabled": True, "quality": 0.75}]
    t = p.tests[0]
    res = plugins.run_analysis_plugins(p)
    assert res == {"done": [t.id], "errors": []}
    assert t.extra_series == {"Heart rate": {"source": "HR", "samples": 120, "unit": ""}}
    assert t.extra_measures == {"HR: recording quality": 0.75, "HR: device": "telemetry"}
    assert plugins.series_path(p, t).exists()
    p.save()
    q = Project.load(p.path)  # stored with the test
    rows = q.results(segmented=True)
    whole = _whole(rows)
    assert whole["Heart rate: mean"] == 500.0 and whole["Heart rate: max"] == 600.0
    assert whole["Heart rate: min"] == 400.0 and whole["HR: recording quality"] == 0.75
    first, second = [r for r in rows if r["Period"] != "Whole test"]
    assert first["Heart rate: mean"] == 400.0 and second["Heart rate: mean"] == 600.0  # per time period
    assert first["HR: device"] == "telemetry"  # per-test measures in every row, as result variables
    zones = [k for k in whole if k.startswith("Heart rate in ") and k.endswith(": mean")]
    assert zones  # per zone (the animal sits in the left half, then the right)
    left = [whole[k] for k in zones if np.isfinite(whole[k])]
    assert 400.0 in left and 600.0 in left


def test_untracked_test_and_failures(tmp_path, hr_plugin):
    p = _project(tmp_path, tracked=False)
    t = p.tests[0]

    def broken(test, project, options):
        raise RuntimeError("no telemetry for this animal")

    plugins.register_analysis("broken", broken)
    try:
        p.analysis_plugins = [{"plugin": "hr", "name": "HR"}, {"plugin": "broken", "name": "B"},
                              {"plugin": "missing", "name": "M"}, {"plugin": "hr", "name": "Off", "enabled": False}]
        res = plugins.run_analysis_plugins(p)
        assert [m for _, m in res["errors"]] == ["B: no telemetry for this animal",
                                                 "M: the plug-in 'missing' is not installed"]
        assert list(t.extra_series) == ["Heart rate"]  # what worked is kept
        row = _whole(p.results())
        assert row["Heart rate: mean"] == 500.0 and row["Test duration (s)"] == 60.0
        # nothing returned any more: the series file goes
        p.analysis_plugins = []
        plugins.run_on_test(p, t)
        assert t.extra_series == {} and not plugins.series_path(p, t).exists()
    finally:
        plugins.unregister_analysis("broken")


def test_bad_output_is_reported(tmp_path):
    p = _project(tmp_path)
    plugins.register_analysis("odd", lambda test, project, o: {"series": {"x": ([0, 1], [1])}})
    try:
        p.analysis_plugins = [{"plugin": "odd", "name": "Odd"}]
        assert "2 times but 1 values" in plugins.run_on_test(p, p.tests[0])[0]
    finally:
        plugins.unregister_analysis("odd")


def test_reperformed_and_deleted_tests(tmp_path, hr_plugin):
    p = _project(tmp_path)
    p.analysis_plugins = [{"plugin": "hr", "name": "HR"}]
    t = p.tests[0]
    plugins.run_on_test(p, t)
    new = reperform_test(p, t)
    assert new.extra_series == {} and new.extra_measures == {}
    path = plugins.series_path(p, t)
    delete_test(p, t)
    assert not path.exists()


def test_old_tests_open_without_the_fields(tmp_path):
    p = _project(tmp_path)
    d = json.loads((p.path / "project.json").read_text())
    for t in d["tests"]:
        t.pop("extra_series"), t.pop("extra_measures")
    (p.path / "project.json").write_text(json.dumps(d))
    q = Project.load(p.path)
    assert q.tests[0].extra_series == {} and q.analysis_io_events(q.tests[0]) == q.tests[0].io_events


# ---------------------------------------------------------------------------------- the CSV / TSV importer
SPIKE2 = '''"INFORMATION"
"Spike2 text export"
"CHANNEL","HR","Sync"
"Time","HR","Sync"
0.0,350,0
1.0,355,0
2.0,360,1
2.5,362,1
3.0,380,0
4.0,400,1
32.0,500,0
62.5,999,0
'''


def test_csv_import_aligned_on_a_sync_pulse(tmp_path):
    p = _project(tmp_path)
    t = p.tests[0]
    (p.path / "data").mkdir()
    (p.path / "data" / "M1_1.csv").write_text(SPIKE2)
    cfg = {**plugins.new_config("csv_import"), "name": "Telemetry", "file": "data/{animal}_{trial}.csv",
           "time_column": "Time", "columns": "HR", "align": "sync", "sync_column": "Sync"}
    out = datafiles.import_csv(t, p, cfg)
    tt, v = out["series"]["HR"]
    assert tt[0] == 0.0 and v[0] == 360.0  # t = 0 at the first rising edge (2.0 s in the file)
    assert list(tt) == [0.0, 0.5, 1.0, 2.0, 30.0] and 999 not in v  # before the test and after its end: left out
    assert out["measures"] == {"Telemetry: test start in the file (s)": 2.0}
    # through the protocol, with a prefix and resampling
    p.analysis_plugins = [{**cfg, "prefix": "Rat ", "resample_hz": 1.0}]
    assert plugins.run_on_test(p, t) == []
    ts, vs = plugins.load_series(p, t)["Rat HR"]
    assert list(ts) == [0.0, 1.0, 2.0, 30.0] and vs[0] == 361.0  # (360 + 362) / 2
    row = _whole(p.results())
    assert row["Rat HR: max"] == 500.0 and row["Telemetry: test start in the file (s)"] == 2.0


def test_csv_import_formats_and_problems(tmp_path):
    p = _project(tmp_path)
    t = p.tests[0]
    f = p.path / "hr.tsv"
    f.write_text("time_ms\tHeart rate\tTemp\n0\t400,5\t37,1\n1000\t401,5\t37,2\n5000\t410\t\n")  # decimal commas
    cfg = {**plugins.new_config("csv_import"), "file": "hr.tsv", "time_unit": "ms", "offset_s": 1.0}
    s = datafiles.import_csv(t, p, cfg)["series"]
    assert set(s) == {"Heart rate", "Temp"}  # every other column
    assert list(s["Heart rate"][0]) == [0.0, 4.0] and s["Heart rate"][1][0] == 401.5
    assert list(s["Temp"][0]) == [0.0]  # empty cells left out
    (p.path / "sp.txt").write_text("1 2\n2 3\n3 4\n")  # spaces, no header
    s = datafiles.import_csv(t, p, {**cfg, "file": "sp.txt", "time_unit": "s", "offset_s": 0})["series"]
    assert list(s) == ["Column 2"] and list(s["Column 2"][1]) == [2.0, 3.0, 4.0]
    for bad, msg in (({"file": ""}, "no data file pattern"), ({"file": "{nope}.csv"}, "not valid"),
                     ({"file": "none.csv"}, "no data file"), ({"time_column": "Clock"}, "no time column"),
                     ({"align": "sync", "sync_column": "Temp"}, "no pulse"),
                     ({"columns": "Pulse"}, "no data column")):
        with pytest.raises(datafiles.DataFileError, match=msg):
            datafiles.import_csv(t, p, {**cfg, **bad})
    assert datafiles.first_rising_edge(np.arange(3.0), np.array([1, 0, 1.0]), 0.5) == 2.0  # high at first: no edge
    assert datafiles.resample(np.array([0.1, 0.2, 1.5]), np.array([1, 3, 5.0]), 1.0)[1].tolist() == [2.0, 5.0]
    assert math.isnan(datafiles._number("x", False))


def test_cli_runs_the_plugins(tmp_path, capsys):
    p = _project(tmp_path)
    (p.path / "M1.csv").write_text("t,HR\n0,1\n1,3\n")
    p.analysis_plugins = [{**plugins.new_config("csv_import"), "file": "{animal}.csv"}]
    p.save()
    cli_main(["project", str(p.path), "plugins"])
    assert "Ran the analysis plug-ins on 1 test(s); 0 problem(s)" in capsys.readouterr().out
    assert Project.load(p.path).tests[0].extra_series["HR"]["samples"] == 2


def test_series_events_feed_event_periods(tmp_path, hr_plugin):
    """A series is an analogue input of the I/O log: time periods anchored to inputs can use it too."""
    p = _project(tmp_path)
    p.analysis_plugins = [{"plugin": "hr", "name": "HR"}]
    plugins.run_on_test(p, p.tests[0])
    evs = p.analysis_io_events(p.tests[0])
    assert len(evs) == 120 and evs[0]["device"] == plugins.SERIES_DEVICE and evs[0]["type"] == "analog"
    p.analysis.event_periods = [{"label": "After HR", "anchor": "input", "channel": "Heart rate", "duration_s": 10}]
    assert any(label == "After HR" for label, _a, _b in p.test_periods(p.tests[0], p.load_tracks(p.tests[0])[0]))
