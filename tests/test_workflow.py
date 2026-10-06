"""Experiment workflow: behaviours, schedules, status actions, training criteria, blind codes, ID and doses."""

import math
from collections import Counter

import pytest

from manymaze.core import workflow as wf
from manymaze.core.project import Animal, Behaviour, Group, Project


def make_project(n=4, stages=("Training",)):
    p = Project(name="wf")
    p.groups = [Group("Saline", "#3b82f6"), Group("Drug", "#ef4444")]
    for i in range(n):
        p.animals.append(Animal(f"M{i + 1}", "Saline" if i % 2 == 0 else "Drug"))
    p.stages = list(stages)
    p.behaviours = [Behaviour("Rearing", "r", "state"), Behaviour("Freeze", "f", "hold", group="posture"),
                    Behaviour("Groom", "g", "state", group="posture"), Behaviour("Bolus", "b", "point")]
    return p


# ---------------------------------------------------------------- behaviours
def test_behaviour_roundtrip_and_validation(tmp_path):
    p = make_project()
    p.save(tmp_path / "x.mmaze")
    q = Project.load(tmp_path / "x.mmaze")
    assert [(b.name, b.kind, b.group) for b in q.behaviours] == [(b.name, b.kind, b.group) for b in p.behaviours]
    assert q.behaviours[1].has_duration and not q.behaviours[3].has_duration
    assert wf.validate_behaviours(p.behaviours) == []
    bad = p.behaviours + [Behaviour("Sniff", "R", "state"), Behaviour("Jump", " ", "point"),
                          Behaviour("Spin", "!", "point")]
    errs = wf.validate_behaviours(bad)
    assert any("Key “R”" in e for e in errs) and any("“!”" in e for e in errs)
    many = [Behaviour(f"b{i}", k) for i, k in enumerate(wf.SCORING_KEYS)]
    assert len(wf.SCORING_KEYS) == wf.MAX_KEYS == 46 and wf.validate_behaviours(many) == []
    assert wf.free_key(p.behaviours) not in "rfgb"
    partners = wf.exclusive_partners(p.behaviours, p.behaviours[1])
    assert [b.name for b in partners] == ["Groom"]
    assert wf.exclusive_partners(p.behaviours, p.behaviours[0]) == []


# ---------------------------------------------------------------- schedules
def test_schedule_orders():
    p = make_project(3, stages=("S1", "S2"))
    rows = wf.generate_schedule(p, trials=2, order="animal")
    assert len(rows) == 3 * 2 * 2
    assert [(r["animal_id"], r["trial"]) for r in rows[:4]] == [("M1", 1), ("M1", 2), ("M2", 1), ("M2", 2)]
    rows = wf.generate_schedule(p, trials=2, order="trial", stages=["S1"])
    assert [(r["animal_id"], r["trial"]) for r in rows] == [("M1", 1), ("M2", 1), ("M3", 1), ("M1", 2), ("M2", 2),
                                                             ("M3", 2)]
    a = wf.generate_schedule(p, trials=5, order="random", seed=3, stages=["S1"])
    b = wf.generate_schedule(p, trials=5, order="random", seed=3, stages=["S1"])
    assert a == b and [r["trial"] for r in a] == sorted(r["trial"] for r in a)
    assert any([r["animal_id"] for r in a[i * 3:(i + 1) * 3]] != ["M1", "M2", "M3"] for i in range(5))
    # latin running order: every animal in every position equally often
    rows = wf.generate_schedule(p, trials=6, order="latin", stages=["S1"])
    pos = Counter((r["animal_id"], i % 3) for i, r in enumerate(rows))
    assert set(pos.values()) == {2}
    # limits
    assert len(wf.generate_schedule(p, trials=500, stages=["S1"], animals=["M1"])) == wf.MAX_TRIALS


def test_latin_square_balanced():
    for n in (2, 3, 4, 5):
        sq = wf.latin_square(n)
        assert len(sq) == (n if n % 2 == 0 else 2 * n)
        for r in sq:
            assert sorted(r) == list(range(n))
        for c in range(n):
            assert Counter(r[c] for r in sq) == Counter({k: len(sq) // n for k in range(n)})
        # balanced: each ordered pair of neighbours appears equally often
        pairs = Counter((r[i], r[i + 1]) for r in sq for i in range(n - 1))
        assert len(set(pairs.values())) == 1 and len(pairs) == n * (n - 1)


def test_schedule_counterbalance_and_skips():
    p = make_project(4)
    p.add_test("", "M1", stage="Training", trial=1)
    rows = wf.generate_schedule(p, trials=2, counterbalance="variable", variable="novel_object",
                                levels=["Object A", "Object B"])
    assert ("M1", 1) not in [(r["animal_id"], r["trial"]) for r in rows]  # already exists
    by_animal = {}
    for r in wf.generate_schedule(p, trials=2, counterbalance="variable", variable="novel_object",
                                  levels=["Object A", "Object B"], skip_existing=False):
        by_animal.setdefault(r["animal_id"], []).append(r["variables"]["novel_object"])
    assert by_animal["M1"] == ["Object A", "Object B"] and by_animal["M2"] == ["Object B", "Object A"]
    rows = wf.generate_schedule(p, trials=1, counterbalance="apparatus", levels=["Box 1", "Box 2"],
                                skip_existing=False)
    assert [r["apparatus"] for r in rows] == ["Box 1", "Box 2", "Box 1", "Box 2"]
    # retired animals and completed stages are skipped
    p.get_animal("M2").retired = True
    wf.completed_stages(p)["M3"] = ["Training"]
    ids = {r["animal_id"] for r in wf.generate_schedule(p, trials=1, skip_existing=False)}
    assert ids == {"M1", "M4"}


# ---------------------------------------------------------------- status actions
def test_skip_resume_reperform_clear(tmp_path):
    import numpy as np

    from manymaze.core import templates
    from manymaze.core.track import Track
    p = make_project(1)
    p.apparatus.append(templates.build("open_field", 0, 0, 100, 100, size_cm=40))
    p.save(tmp_path / "s.mmaze")
    t = p.add_test("", "M1", stage="Training", trial=1)
    wf.skip_test(t, "sick")
    assert t.status == "skipped" and "sick" in t.notes
    wf.resume_test(p, t)
    assert t.status == "pending"
    t.events = [{"behaviour": "Bolus", "t": 1.0, "t_end": None}]
    assert wf.refresh_status(p, t) == "scored"
    tr = Track(t=np.arange(10) / 5, x=np.ones(10), y=np.ones(10), fps=5)
    p.save_tracks(t, [tr])
    assert wf.refresh_status(p, t) == "tracked"
    new = wf.reperform_test(p, t)
    assert t.status == "superseded" and new.status == "pending" and new.attempt == 2 and new.replaces == t.id
    assert (new.animal_id, new.stage, new.trial) == ("M1", "Training", 1) and new.events == []
    assert p.tests.index(new) == p.tests.index(t) + 1
    # superseded / skipped tests are left out of the results
    assert p.results() == []
    wf.resume_test(p, t)
    assert t.status == "tracked" and len(p.results()) == 1
    assert wf.clear_tracks(p, t) == 1 and t.status == "scored" and not p.has_track(t)


def test_scored_only_results():
    p = make_project(1)
    p.test_duration_s = 0
    t = p.add_test("", "M1", stage="Training", trial=1)
    t.events = [{"behaviour": "Rearing", "t": 2.0, "t_end": 6.0}, {"behaviour": "Bolus", "t": 8.0, "t_end": None},
                {"behaviour": "Freeze", "t": 1.0, "t_end": 10.0}]
    t.status = "scored"
    rows = p.results()
    assert len(rows) == 1
    r = rows[0]
    assert r["Rearing: duration (s)"] == 4 and r["Rearing: duration (%)"] == 40  # duration from the last event
    assert r["Bolus: count"] == 1 and r["Freeze: mean bout (s)"] == 9


# ---------------------------------------------------------------- training criteria
def _criteria_project():
    p = make_project(3)
    lat = {"M1": [30, 9, 8, 7, 20], "M2": [30, 25, 9, 30, 28], "M3": [5, 4]}
    for aid, vals in lat.items():
        for k, v in enumerate(vals):
            t = p.add_test("", aid, stage="Training", trial=k + 1)
            t.status = "tracked"
            t.result_variables = {"Escape latency (s)": v}
        p.add_test("", aid, stage="Training", trial=len(vals) + 1)  # pending
        p.add_test("", aid, stage="Probe", trial=1)
    p.training_criteria = [{"stage": "Training", "measure": "Escape latency (s)", "op": "<", "value": 10,
                            "consecutive_trials": 3, "action_met": "complete_stage",
                            "action_fail": {"after_trials": 5, "action": "retire"}}]
    return p


def test_evaluate_and_apply_criteria():
    p = _criteria_project()
    rep = wf.evaluate_criteria(p)
    rows = {r["animal"]: r for r in rep["rows"]}
    assert rows["M1"]["met"] and rows["M1"]["met_at_trial"] == 4
    assert not rows["M2"]["met"] and rows["M2"]["failed"] and rows["M2"]["trials"] == 5
    assert not rows["M3"]["met"] and not rows["M3"]["failed"]  # 2 good trials so far, still training
    assert rep["completed"] == {"M1": ["Training"]} and list(rep["retire"]) == ["M2"]
    res = wf.apply_criteria(p, rep)
    assert res["retired"] == ["M2"] and res["completed"] == 1
    assert p.get_animal("M2").retired and "within 5 trials" in p.get_animal("M2").retired_reason
    m1_pending = [t for t in p.tests if t.animal_id == "M1" and t.stage == "Training" and t.trial == 6][0]
    assert m1_pending.status == "skipped" and "criterion met" in m1_pending.notes
    assert all(t.status == "skipped" for t in p.tests if t.animal_id == "M2" and not t.result_variables)
    assert [t.status for t in p.tests if t.animal_id == "M3" and not t.result_variables] == ["pending", "pending"]
    # the schedule skips the retired animal and M1's completed stage
    rows = wf.generate_schedule(p, stages=["Training", "Retention"], trials=1, skip_existing=False)
    assert {(r["animal_id"], r["stage"]) for r in rows} == {("M1", "Retention"), ("M3", "Training"),
                                                           ("M3", "Retention")}
    # applying twice is harmless; reinstating resumes the animal's tests
    assert wf.apply_criteria(p)["retired"] == []
    assert wf.reinstate_animal(p, p.get_animal("M2")) == 2
    assert not p.get_animal("M2").retired


def test_criteria_normalisation_and_ops():
    c = wf.normalize_criterion({"stage": "T", "measure": "m", "op": ">=", "value": "5", "fail_after_trials": 4})
    assert c["value"] == 5.0 and c["action_fail"] == {"after_trials": 4, "action": "retire"}
    assert "retire if not met after 4 trials" in wf.criterion_text(c)
    p = make_project(1)
    for k, v in enumerate([5, 6, float("nan"), 7]):
        t = p.add_test("", "M1", stage="T", trial=k + 1)
        t.status = "scored"
    p.training_criteria = [c | {"consecutive_trials": 2}]
    vals = [5, 6, None, 7]
    rep = wf.evaluate_criteria(p, value_fn=lambda t, m: vals[t.trial - 1])
    assert rep["rows"][0]["met_at_trial"] == 2


# ---------------------------------------------------------------- blind, ID, doses
def test_blind_codes_stable():
    p = make_project()
    assert wf.display_group(p, "Saline") == "Saline"
    p.blind = True
    a, b = wf.display_group(p, "Saline"), wf.display_group(p, "Drug")
    assert a != b and "Saline" not in a and a.startswith("Group ")
    assert wf.display_group(p, "Saline") == a and wf.display_color(p, "Drug") == wf.BLIND_COLOR
    q = Project.from_dict(p.to_dict())
    assert wf.display_group(q, "Saline") == a
    p.groups.append(Group("New"))
    assert wf.display_group(p, "New") not in (a, b)


def test_id_matches():
    p = make_project(1)
    p.animal_fields = ["Microchip"]
    p.animals[0].fields["Microchip"] = "985 112 003"
    t = p.add_test("", "M1")
    assert wf.id_matches(p, t, " m1 ") and wf.id_matches(p, t, "985 112 003")
    assert not wf.id_matches(p, t, "M2") and not wf.id_matches(p, t, "")
    assert not wf.confirm_id_enabled(p)
    p.settings_extra["confirm_id"] = True
    assert wf.confirm_id_enabled(p)


def test_dose_maths():
    assert wf.dose_volume_ml(25, 10, 1) == pytest.approx(0.25)
    assert wf.dose_volume_ml("30,0 g", "5 mg/kg", "2.5") == pytest.approx(0.06)
    assert wf.dose_volume_ml(25, 10, 0) is None and wf.dose_volume_ml("", 10, 1) is None
    p = make_project(3)
    p.animal_fields = ["Weight (g)", "Dose (mg/kg)"]
    p.animals[0].fields = {"Weight (g)": "20"}
    p.animals[1].fields = {"Weight (g)": "40", "Dose (mg/kg)": "20"}
    wf.dose_settings(p).update(dose_mg_kg=10, conc_mg_ml=2)
    vols = wf.compute_doses(p)
    assert vols["M1"] == pytest.approx(0.1) and vols["M2"] == pytest.approx(0.4) and vols["M3"] is None
    assert p.animals[0].fields["Volume (mL)"] == "0.100" and "Volume (mL)" in p.animal_fields
    assert math.isclose(wf.parse_number("1.5 mL"), 1.5)
