"""Information columns of the results (time of day, user, animal notes, treatment code, reason for test end, animal
contrast / length, frames tracked, video files, moveable zone positions, segment of test) and experiment management
(animal notes, experimenters, ending a stage for one animal)."""

import csv
import datetime as _dt
import json

import numpy as np
import pytest

from manymaze.core import export, project as project_module, workflow as wf
from manymaze.core.apparatus import POSITION_KEY, Zone
from manymaze.core.autosave import RecoveredSession
from manymaze.core.demo import create_demo_project
from manymaze.core.geometry import circle
from manymaze.core.importers import ANIMAL_ROLES, guess_mapping, import_animals, read_table
from manymaze.core.live import LiveSession, ObservationSession
from manymaze.core.livegroup import LiveGroup
from manymaze.core.project import INFO_COLUMNS, OPTIONAL_INFO_COLUMNS, Animal, Project, moveable_zone_text, time_of_day
from manymaze.core.session import (END_DURATION, END_PROCEDURE, END_RECOVERED, END_SOURCE, END_USER, END_ZONE,
                                   save_live_test)
from manymaze.core.tracking import ArenaTracker, DetectionSettings


@pytest.fixture()
def demo(tmp_path):
    return create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=8)


# ---------------------------------------------------------------- information columns
def test_info_columns_are_listed():
    for c in ("Time of day", "User", "Animal notes", "Treatment code", "Reason for test end",
              "Animal lighter / darker", "Animal length", "Frames tracked (%)", "Source video file",
              "Recorded video file", "Video time at test start (s)", "Moveable zone positions", "Segment of test"):
        assert c in INFO_COLUMNS
    assert set(OPTIONAL_INFO_COLUMNS) <= set(INFO_COLUMNS)
    assert "User" not in OPTIONAL_INFO_COLUMNS  # shown when filled


@pytest.mark.parametrize("hour,name", [(0, "Night"), (4, "Night"), (5, "Morning"), (11, "Morning"),
                                       (12, "Afternoon"), (16, "Afternoon"), (17, "Evening"), (20, "Evening"),
                                       (21, "Night"), (23, "Night")])
def test_time_of_day(hour, name):
    assert time_of_day(_dt.datetime(2026, 10, 5, hour, 30)) == name


def test_test_info_columns(demo):
    p = demo
    t = p.tests[0]
    a = p.get_animal(t.animal_id)
    a.notes = "small scar on the left ear"
    t.recorded_at, t.experimenter = "2026-10-05T19:15:00", "Alice"
    info = p.test_info(t)
    assert info["Time of day"] == "Evening" and info["User"] == "Alice"
    assert info["Animal notes"] == "small scar on the left ear"
    assert info["Treatment code"] == wf.treatment_code(p, a.group) != ""
    assert info["Reason for test end"] == ""  # a video test
    assert set(INFO_COLUMNS) - {"Period", "Segment of test"} <= set(info)
    t.recorded_at = ""
    assert p.test_info(t)["Time of day"] == ""  # blank for video tests, like the test date


def test_treatment_code_while_blind_uses_existing_codes(demo):
    p = demo
    p.blind = True
    codes = wf.blind_codes(p)
    t = p.tests[0]
    g = p.get_animal(t.animal_id).group
    assert p.test_info(t)["Treatment code"] == codes[g]
    p.settings_extra["blind_codes"] = {}
    assert p.test_info(t)["Treatment code"] == ""  # never creates codes (results may run off the UI thread)
    assert p.settings_extra["blind_codes"] == {}


def test_track_info_columns(demo):
    p = demo
    t = p.tests[0]
    row = p.analyse_test(t)[0]
    assert row["Animal lighter / darker"] == "Darker"  # the demo animals are dark on a light floor (auto contrast)
    assert row["Animal length"] > 0
    assert row["Frames tracked (%)"] == pytest.approx(100.0)
    assert row["Source video file"] == p.abs_path(t.video) and row["Recorded video file"] == ""
    assert row["Video time at test start (s)"] == 0.0
    # fewer frames detected
    tr = p.load_tracks(t)[0]
    tr.detected[: len(tr) // 4] = False
    tr.meta.pop("animal_contrast")
    p.detection.contrast = "light"
    info = p.track_info(t, tr)
    assert info["Frames tracked (%)"] == pytest.approx(100.0 * (len(tr) - len(tr) // 4) / len(tr), abs=0.01)
    assert info["Animal lighter / darker"] == "Lighter"  # from the detection setting for older tracks


def test_contrast_votes():
    bg = np.full((60, 60), 200, np.uint8)
    frame = bg.copy()
    frame[20:30, 20:35] = 40  # a dark animal
    trk = ArenaTracker(DetectionSettings(min_area_px=10))
    trk.set_background(bg)
    trk.process(frame)
    assert trk.animal_contrast() == "darker"
    trk2 = ArenaTracker(DetectionSettings(min_area_px=10))
    trk2.set_background(np.full((60, 60), 30, np.uint8))
    light = np.full((60, 60), 30, np.uint8)
    light[10:20, 10:25] = 230
    trk2.process(light)
    assert trk2.animal_contrast() == "lighter"
    assert ArenaTracker(DetectionSettings(method="colour")).animal_contrast() == ""
    assert ArenaTracker(DetectionSettings(contrast="light")).animal_contrast() == "lighter"


def test_end_zone_reason(demo):
    p = demo
    t = p.tests[0]
    app = p.get_apparatus(t.apparatus)
    tr = p.load_tracks(t)[0]
    x, y = float(tr.x[len(tr) // 2]), float(tr.y[len(tr) // 2])
    app.zones.append(Zone("Goal", circle(x, y, 400)))  # contains the whole track: the test ends at once
    p.analysis.end_zone = "Goal"
    assert p.analyse_test(t)[0]["Reason for test end"] == END_ZONE
    p.analysis.end_zone = ""
    assert p.analyse_test(t)[0]["Reason for test end"] == ""


def test_segment_of_test(demo):
    p = demo
    p.analysis.bin_length_s = 2.0
    rows = p.analyse_test(p.tests[0], segmented=True)
    assert rows[0]["Period"] == "Whole test" and rows[0]["Segment of test"] == ""
    segs = [r["Segment of test"] for r in rows[1:]]
    assert segs == list(range(1, len(rows)))
    assert all(r["Period"] != "Whole test" for r in rows[1:])


def test_moveable_zone_text():
    ov = {"Object": {"type": "ellipse", "cx": 310.4, "cy": 140.0, "rx": 20, "ry": 20},
          "Start": {"x": 12.0, "y": 30.6}, POSITION_KEY: {"dx": 5, "dy": -3, "angle": 0, "scale": 1}}
    assert moveable_zone_text(ov) == "Object: 310, 140 px; Start: 12, 31 px; Apparatus moved by 5, -3 px"
    assert moveable_zone_text({}) == ""


def test_moveable_zone_positions_in_results(demo):
    t = demo.tests[0]
    t.zone_overrides = {"Centre": {"type": "ellipse", "cx": 100.0, "cy": 120.0, "rx": 30.0, "ry": 30.0}}
    assert demo.test_info(t)["Moveable zone positions"] == "Centre: 100, 120 px"


# ---------------------------------------------------------------- reason for test end (live)
def _frames(n, w=80, h=60):
    out = []
    for i in range(n):
        f = np.full((h, w, 3), 200, np.uint8)
        f[20:30, 10 + i % 40:20 + i % 40] = 30
        out.append(f)
    return out


def _session(**kw):
    s = LiveSession(None, DetectionSettings(min_area_px=10), fps=10.0, **kw)
    s.set_background(np.full((60, 80, 3), 200, np.uint8))
    return s


def test_live_end_reasons():
    s = _session(duration_s=1.0)
    for i, f in enumerate(_frames(30)):
        s.process(f, i / 10)
    assert s.state == "finished" and s.end_reason == END_DURATION
    s = _session(duration_s=0.0)
    for i, f in enumerate(_frames(5)):
        s.process(f, i / 10)
    s.finish()
    assert s.end_reason == END_USER
    s.finish(END_SOURCE)  # already finished: the first reason stays
    assert s.end_reason == END_USER
    s = _session(duration_s=0.0)  # an "end test" procedure action
    for i, f in enumerate(_frames(3)):
        s.process(f, i / 10)
    s.engine._end_test()
    assert s.state == "finished" and s.end_reason == END_PROCEDURE


def test_livegroup_source_end_reason():
    g = LiveGroup()
    s = _session(duration_s=0.0)
    e = g.add_session("cam", s)
    for i, f in enumerate(_frames(5)):
        g.process("cam", f, i / 10)
    g.source_ended("cam")
    assert e.session.end_reason == END_SOURCE


def test_observation_end_reason():
    clock = iter(float(x) for x in range(100))
    o = ObservationSession(duration_s=3.0, start_mode="immediate", clock=lambda: next(clock))
    for _ in range(5):
        o.tick()
    assert o.state == "finished" and o.end_reason == END_DURATION
    o2 = ObservationSession(duration_s=0.0, start_mode="immediate")
    o2.finish()
    assert o2.end_reason == END_USER


def test_save_live_test_stamps_reason_and_user(tmp_path):
    p = Project()
    p.save(tmp_path / "x.mmaze")
    p.current_user = "Bob"
    t = p.add_test("", "M1")
    t.experimenter = "Planned"
    s = _session(duration_s=1.0)
    for i, f in enumerate(_frames(30)):
        s.process(f, i / 10)
    assert save_live_test(p, t, s)
    assert t.end_reason == END_DURATION and t.experimenter == "Bob"
    info = {**p.test_info(t), **p.track_info(t, p.load_tracks(t)[0])}
    assert info["Reason for test end"] == END_DURATION and info["User"] == "Bob"
    assert info["Source video file"] == "" and info["Recorded video file"] == ""  # live, not recorded
    assert info["Time of day"] != ""


def test_recovered_session_reason():
    assert RecoveredSession({"cols": None}).end_reason == END_RECOVERED
    assert RecoveredSession({"cols": None, "end_reason": END_DURATION}).end_reason == END_DURATION


def test_snapshot_keeps_reason():
    s = _session(duration_s=0.5)
    for i, f in enumerate(_frames(10)):
        s.process(f, i / 10)
    assert s.autosave_snapshot()["end_reason"] == END_DURATION


# ---------------------------------------------------------------- experimenters
def test_experimenter_stamped_when_tracked(demo):
    p = demo
    t = p.tests[0]
    t.experimenter = ""
    p.current_user = "Carol"
    p.save_tracks(t, p.load_tracks(t))
    assert t.experimenter == "Carol"
    p.current_user = "Dan"
    p.save_tracks(t, p.load_tracks(t))
    assert t.experimenter == "Carol"  # re-tracking keeps who tracked it first


def test_experimenter_list_helpers():
    p = Project()
    assert wf.add_experimenter(p, "  Alice   Smith ") == "Alice Smith"
    assert wf.add_experimenter(p, "Alice Smith") == "Alice Smith" and p.experimenters == ["Alice Smith"]
    assert wf.add_experimenter(p, "  ") == "" and p.experimenters == ["Alice Smith"]
    t1, t2 = p.add_test(), p.add_test()
    assert wf.set_experimenter(p, [t1, t2], "Bob") == 2
    assert p.experimenters == ["Alice Smith", "Bob"] and t1.experimenter == "Bob"
    assert wf.set_experimenter(p, [t1], "") == 1 and t1.experimenter == ""
    p.current_user = "Bob"
    assert wf.remove_experimenter(p, "Bob") and p.current_user == "" and t2.experimenter == "Bob"
    assert not wf.remove_experimenter(p, "Nobody")


def test_copies_do_not_inherit_user_or_reason(demo):
    t = demo.tests[0]
    t.experimenter, t.end_reason = "Alice", END_USER
    new = wf.reperform_test(demo, t)
    assert new.experimenter == "" and new.end_reason == ""


def test_copy_protocol_copies_experimenters():
    src, dst = Project(experimenters=["Alice", "Bob"]), Project(experimenters=["Bob"])
    wf.copy_protocol(src, dst)
    assert dst.experimenters == ["Bob", "Alice"]


# ---------------------------------------------------------------- persistence
def test_round_trip_and_old_projects(tmp_path):
    p = Project(experimenters=["Alice"])
    p.animals.append(Animal("M1", notes="limps"))
    t = p.add_test("", "M1")
    t.experimenter, t.end_reason = "Alice", END_DURATION
    p.current_user = "Alice"
    p.save(tmp_path / "a.mmaze")
    d = json.loads((tmp_path / "a.mmaze" / "project.json").read_text())
    assert "current_user" not in d  # who uses the app is a preference, not experiment data
    q = Project.load(tmp_path / "a.mmaze")
    assert q.experimenters == ["Alice"] and q.animals[0].notes == "limps" and q.current_user == ""
    assert (q.tests[0].experimenter, q.tests[0].end_reason) == ("Alice", END_DURATION)
    # an experiment saved before these fields existed
    for a in d["animals"]:
        a.pop("notes")
    for x in d["tests"]:
        x.pop("experimenter"), x.pop("end_reason")
    d.pop("experimenters")
    old = Project.from_dict(d)
    assert old.experimenters == [] and old.animals[0].notes == "" and old.tests[0].experimenter == ""
    assert project_module.Test.from_dict({"id": 3}).end_reason == ""


def test_xml_export(demo, tmp_path):
    p = demo
    p.experimenters = ["Alice"]
    p.animals[0].notes = "nervous"
    p.tests[0].experimenter, p.tests[0].end_reason = "Alice", END_USER
    path = export.export_xml(p, tmp_path / "e.xml", include_tracks=False)
    d = export.read_experiment_xml(path)
    assert d["experimenters"] == ["Alice"]
    assert d["animals"][0]["notes"] == "nervous"
    t0 = next(x for x in d["tests"] if x["id"] == str(p.tests[0].id))
    assert t0["experimenter"] == "Alice" and t0["end-reason"] == END_USER
    # info columns are not written as results
    names = {k for r in t0["results"] for k in r["values"]}
    assert not names & {"User", "Animal notes", "Segment of test", "Frames tracked (%)"}


def test_workbook_sheets(demo):
    p = demo
    p.animals[0].notes = "nervous"
    p.tests[0].experimenter = "Alice"
    sheets, _ = export.results_workbook(p, p.results())
    assert sheets["Animals"][0]["Notes"] == "nervous"
    assert sheets["Tests"][0]["User"] == "Alice" and "Reason for test end" in sheets["Tests"][0]


def test_animal_notes_csv_round_trip(tmp_path):
    p = Project()
    p.animals += [Animal("M1", "Control", "Male", notes="limps, check daily"), Animal("M2")]
    out = tmp_path / "animals.csv"
    export.export_animals(p, out)
    rows = list(csv.reader(out.open()))
    assert rows[0] == ["ID", "Treatment", "Sex", "Notes"] and rows[1][-1] == "limps, check daily"
    q = Project()
    header, data = read_table(out)
    mapping = guess_mapping(header, ANIMAL_ROLES)
    assert mapping["notes"] == 3
    import_animals(q, header, data, mapping)
    assert q.get_animal("M1").notes == "limps, check daily" and q.get_animal("M2").notes == ""
    assert "Notes" not in q.animal_fields
    assert not wf.add_field(q, "Notes")  # reserved: notes are their own column


# ---------------------------------------------------------------- ending a stage for one animal
def _staged_project():
    p = Project(stages=["Training", "Probe"])
    for aid in ("M1", "M2"):
        p.ensure_animal(aid)
        for st in ("Training", "Probe"):
            for k in (1, 2, 3):
                p.add_test("", aid, stage=st, trial=k)
    return p


def test_end_stage_for_one_animal():
    p = _staged_project()
    p.tests[0].status = "tracked"  # M1 Training trial 1 done
    n = wf.end_stage(p, "M1", "Training")
    assert n == 2
    m1_training = [t for t in p.tests if t.animal_id == "M1" and t.stage == "Training"]
    assert [t.status for t in m1_training] == ["tracked", "skipped", "skipped"]
    assert all(wf.STAGE_ENDED in t.notes for t in m1_training[1:])
    assert all(t.status == "pending" for t in p.tests if t.animal_id == "M2" or t.stage == "Probe")
    assert wf.stage_ended(p, "M1", "Training") and not wf.stage_ended(p, "M2", "Training")
    # new schedules leave the stage out for that animal, as for a met training criterion
    rows = wf.generate_schedule(p, stages=["Training"], trials=4, skip_existing=False)
    assert {r["animal_id"] for r in rows} == {"M2"}
    # ending it again changes nothing
    assert wf.end_stage(p, "M1", "Training") == 0
    assert wf.completed_stages(p)["M1"] == ["Training"]


def test_reopen_stage():
    p = _staged_project()
    p.tests[1].notes = "camera fault"
    wf.skip_test(p.tests[2], "experimenter's choice")  # skipped by hand: stays skipped
    wf.end_stage(p, "M1", "Training")
    n = wf.reopen_stage(p, "M1", "Training")
    assert n == 2 and not wf.stage_ended(p, "M1", "Training") and "M1" not in wf.completed_stages(p)
    assert p.tests[1].status == "pending" and p.tests[1].notes == "camera fault"
    assert p.tests[2].status == "skipped"
