"""Procedure structure and variables: else-if, repeat until, go to / labels, sub-procedures, waiting for one of
several events, timer resolution, the pre-test section (prevent / allow test start), built-in variables, variables
kept per animal / apparatus, constrained shuffles, #N/A, ANY-maze maths and analogue outputs in volts."""

import datetime as dt
import math

import numpy as np
import pytest

from manymaze.core import procedures as P
from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.apparatus import PointOfInterest, Sequence
from manymaze.core.iodevices import DeviceManager
from manymaze.core.live import LiveSession
from manymaze.core.procedures import ExprError, ProcedureEngine, validate
from manymaze.core.procedures import edit as pe
from manymaze.core.tracking import DetectionSettings

FPS = 25


def W(seconds):
    return {"type": "wait", "mode": "seconds", "seconds": seconds}


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def SET(var, value):
    return {"type": "set", "var": var, "value": value}


def VAR(name, value=0, **kw):
    return dict({"type": "var", "name": name, "value": value}, **kw)


def proc(*stmts, name="P", **kw):
    return dict({"name": name, "enabled": True, "statements": list(stmts)}, **kw)


def run(procs, seconds, state_fn=None, fps=FPS, **kw):
    marks = []
    eng = ProcedureEngine(procs, on_mark=lambda n, t: marks.append((n, round(t, 4))), seed=1, **kw)
    eng.start(0.0)
    n = int(round(seconds * fps))
    for i in range(n + 1):
        t = i / fps
        eng.update_state(t, state_fn(t) if state_fn else {})
        if eng.stopped:
            break
    eng.stop(min(n, i) / fps)
    eng.marks_list = marks
    return eng


def marks(eng):
    return [n for n, _t in eng.marks_list]


def messages(procs, context=None):
    return [m for _pi, _p, m in validate(procs, context)]


# ====================================================================== if / else-if, repeat until
def test_else_if_chain_runs_the_first_true_branch():
    def chain(x):
        return proc(VAR("x", x), {"type": "if", "cond": "x > 10", "body": [DO("mark", name="big")],
                                  "elif": [{"cond": "x > 5", "body": [DO("mark", name="medium")]},
                                           {"cond": "x > 0", "body": [DO("mark", name="small")]}],
                                  "else": [DO("mark", name="none")]})
    assert [marks(run([chain(x)], 0.1)) for x in (20, 7, 1, -1)] == [["big"], ["medium"], ["small"], ["none"]]
    # without an else nothing runs; a disabled clause is skipped
    p = proc(VAR("x", 1), {"type": "if", "cond": "x > 5", "body": [DO("mark", name="a")],
                           "elif": [{"cond": "x > 0", "enabled": False, "body": [DO("mark", name="b")]}]})
    assert marks(run([p], 0.1)) == []
    # paths and describe
    paths = [pth for pth, _s in P.iter_statements(chain(1)["statements"])]
    assert (1, "elif", 1, "body", 0) in paths and (1, "else", 0) in paths
    assert P.path_text((1, "elif", 1, "body", 0)) == "2.elif.2.body.1"
    assert P.describe_elif({"cond": "x > 0"}) == "Else if x > 0"
    assert P.statement_at(chain(1), (1, "elif", 0))["cond"] == "x > 5"
    # validation of the clauses, with the clause's path
    bad = proc({"type": "if", "cond": "1", "body": [], "elif": [{"cond": "1 +", "body": [{"type": "nope"}]}]})
    issues = validate([bad])
    assert any(p == (0, "elif", 0) and "Else if" in m for _i, p, m in issues)
    assert any(p == (0, "elif", 0, "body", 0) for _i, p, m in issues)


def test_repeat_until_runs_the_body_at_least_once():
    p = proc(VAR("n", 0, result=True), {"type": "repeat", "mode": "until", "until": "true", "body": [
        SET("n", "n + 1")]}, VAR("m", 0, result=True), {"type": "repeat", "mode": "until", "until": "m >= 4",
                                                        "var": "i", "body": [SET("m", "m + 1")]})
    eng = run([p], 0.1)
    assert eng.result_variables == {"n": 1, "m": 4} and not eng.errors
    assert P.repeat_mode({"type": "repeat", "mode": "until"}) == "until"
    assert P.describe_statement(p["statements"][1]) == "Repeat until true"
    assert messages([proc({"type": "repeat", "mode": "until", "until": "", "body": []})]) == \
        ["Repeat: Condition is required", "Repeat: warning: the loop has nothing to do (it only uses up time, one "
                                          "check per frame)"]


# ====================================================================== go to / labels
def test_goto_and_labels():
    loop = proc(VAR("n", 0, result=True),
                {"type": "label", "name": "again"},
                SET("n", "n + 1"),
                W(0.1),
                {"type": "if", "cond": "n < 5", "body": [{"type": "goto", "label": "again"}]},
                {"type": "goto", "label": "end"},
                DO("mark", name="skipped"),
                {"type": "label", "name": "end"},
                DO("mark", name="done"))
    eng = run([loop], 1.0)
    assert eng.result_variables == {"n": 5} and marks(eng) == ["done"] and not eng.errors
    assert [round(t, 2) for n, t in eng.marks_list] == [0.52]  # 5 waits of 0.1 s: the first frame after 0.5 s
    # out of a forever loop
    out = proc(VAR("k", 0, result=True), {"type": "repeat", "mode": "forever", "body": [
        SET("k", "k + 1"), {"type": "if", "cond": "k == 3", "body": [{"type": "goto", "label": "out"}]}]},
               {"type": "label", "name": "out"}, DO("mark", name="out"))
    eng = run([out], 0.5)
    assert eng.result_variables == {"k": 3} and marks(eng) == ["out"]
    # validation: unknown label, label inside a nested block, duplicate label
    bad = proc({"type": "goto", "label": "inner"}, {"type": "if", "cond": "1", "body": [
        {"type": "label", "name": "inner"}, {"type": "label", "name": "inner"}]}, {"type": "goto", "label": "zz"})
    msgs = messages([bad])
    assert "Go to: no label 'inner' in this block or a block around it" in msgs
    assert "Go to: unknown label 'zz'" in msgs
    assert "Label: another label is also called 'inner'" in msgs
    # at run time an unreachable label is an error, not a crash
    eng = run([proc({"type": "goto", "label": "nowhere"}, DO("mark", name="after"))], 0.1)
    assert marks(eng) == [] and any("no label 'nowhere'" in e for e in eng.errors)
    assert P.describe_statement({"type": "goto", "label": "x"}) == "Go to x"


# ====================================================================== sub-procedures
SUB = proc(VAR("calls", 0, result=True), SET("calls", "calls + 1"), DO("mark", name="sub {calls}"), W(0.5),
           {"type": "if", "cond": "calls >= 2", "body": [{"type": "stop", "what": "return"}]},
           DO("mark", name="sub end"), name="Sub", sub=True)


def test_call_sub_procedure_runs_in_place_and_waits():
    main = proc({"type": "call", "procedure": "Sub"}, DO("mark", name="back"),
                {"type": "call", "procedure": "Sub"}, DO("mark", name="back again"), name="Main")
    eng = run([main, SUB], 2.0)
    # (marked at the frame times: the first frame after 0.5 s is at 0.52 s)
    assert eng.marks_list == [("sub 1", 0.0), ("sub end", 0.52), ("back", 0.52), ("sub 2", 0.52),
                              ("back again", 1.0)]
    assert eng.result_variables == {"calls": 2} and not eng.errors
    # a sub-procedure never runs by itself, and has no handlers
    assert run([SUB], 1.0).marks_list == []
    assert messages([proc(WHEN("test_start", []), name="S", sub=True)]) == \
        ["When: a sub-procedure has no When handlers (it runs when it is called)"]


def test_run_sub_procedure_action_runs_alongside_and_errors():
    main = proc(DO("run_subprocedure", procedure="Sub"), DO("mark", name="main goes on"), name="Main")
    eng = run([main, SUB], 1.0)
    assert marks(eng)[:2] == ["main goes on", "sub 1"] and "sub end" in marks(eng)
    # validation: unknown / not a sub-procedure
    msgs = messages([proc({"type": "call", "procedure": "Main"}, DO("run_subprocedure", procedure="Nope"),
                          name="Main")])
    assert "Call sub-procedure: 'Main' is not a sub-procedure" in msgs
    assert "Do run sub-procedure: unknown procedure 'Nope'" in msgs
    # recursion is bounded
    rec = proc({"type": "call", "procedure": "R"}, name="R", sub=True)
    eng = run([proc({"type": "call", "procedure": "R"}), rec], 0.1)
    assert any("nested more than" in e for e in eng.errors)
    # errors inside a sub-procedure name it
    bad = proc(SET("x", "1 / 0"), name="Bad", sub=True)
    eng = run([proc({"type": "call", "procedure": "Bad"}), bad], 0.1)
    assert any("'Bad', statement 1" in e for e in eng.errors)


# ====================================================================== wait for event A or B
def test_wait_for_one_of_several_events():
    p = proc(VAR("which", -1, result=True), VAR("which2", -1, result=True),
             {"type": "wait", "mode": "event", "event": "key_down", "key": "a",
              "or": [{"event": "key_down", "key": "b"}, {"event": "zone_enter", "zone": "Centre"}], "timeout": 5},
             SET("which", "wait_event"), DO("mark", name="{event_name}"),
             {"type": "wait", "mode": "event", "event": "key_down", "key": "a", "or": [{"event": "time_reached",
                                                                                         "time": 50}],
              "timeout": 1},
             SET("which2", "wait_event"))
    eng = ProcedureEngine([p], on_mark=lambda n, t: None)
    eng.start(0)
    eng.update_state(0.5, {"zones": {"Centre": False}})
    eng.update_state(1.0, {"zones": {"Centre": True}})
    eng.key(1.0, "c")  # neither of the second wait's events
    assert eng.vars["which"] == 3 and eng.marks[-1]["behaviour"] == "Centre"
    for i in range(1, 30):
        eng.update_state(1.0 + i / 10)
    eng.stop(4.0)
    assert eng.result_variables == {"which": 3, "which2": 0}  # the second wait timed out
    # the second alternative of a key wait
    p2 = proc(VAR("w", 0, result=True), {"type": "wait", "mode": "event", "event": "key_down", "key": "a",
                                        "or": [{"event": "key_down", "key": "b"}]}, SET("w", "wait_event"))
    eng = ProcedureEngine([p2])
    eng.start(0)
    eng.key(0.2, "b")
    eng.stop(1)
    assert eng.result_variables == {"w": 2}
    st = p2["statements"][1]
    assert P.describe_statement(st) == "Wait for key pressed (“a”) or key pressed (“b”)"
    assert messages([proc({"type": "wait", "mode": "event", "event": "key_down",
                           "or": [{"event": "nope"}, {"event": "zone_enter", "zone": "Q"}]})], {"zones": ["A"]}) == \
        ["Wait: or event 1: unknown event 'nope'", "Wait: or event 2: Zone: unknown zone 'Q'"]


# ====================================================================== timer resolution
def test_set_timer_resolution_is_accepted():
    p = proc({"type": "resolution", "ms": 10}, W(0.2), DO("mark", name="t"))
    eng = run([p], 0.5)
    assert eng.timer_resolution_ms == 10 and eng.marks_list == [("t", 0.2)] and not eng.errors
    assert not validate([p]) and P.describe_statement(p["statements"][0]) == "Set timer resolution 10 ms"
    assert messages([proc({"type": "resolution", "ms": "x +"})])[0].startswith("Set timer resolution: Resolution")


# ====================================================================== before the test starts
def test_pre_test_section_prevents_and_allows_the_start():
    p = proc(VAR("ready", 0), WHEN("test_waiting", [DO("prevent_test_start"), DO("output_on", channel="light"),
                                                    W(1.0), SET("ready", 1), DO("allow_test_start")]),
             WHEN("test_start", [DO("mark", name="ready={ready}")]))
    eng = ProcedureEngine([p])
    assert eng.has_pretest() and eng.start_allowed
    eng.waiting_update(0.0)
    assert not eng.start_allowed and eng.outputs_state[("virtual", "light")] == 1
    eng.waiting_update(0.5)
    assert not eng.start_allowed
    eng.waiting_update(1.0)
    assert eng.start_allowed
    eng.start(0.0)
    eng.update_state(0.1)
    eng.stop(0.2)
    assert [m["behaviour"] for m in eng.marks] == ["ready=1"]  # variables carry over into the test
    # the output left on before the start is in the I/O log of the test, at its start
    assert any(e["channel"] == "light" and e["t"] == 0 and e["value"] == 1 for e in eng.io_events)
    # no pre-test handler: waiting_update does nothing
    eng = ProcedureEngine([proc(DO("mark", name="x"))])
    eng.waiting_update(0.0)
    assert not eng.has_pretest() and not eng.started and eng.start_allowed
    # validation: only in a "test is waiting to start" handler; cannot wait for it
    assert messages([proc(DO("prevent_test_start"))]) == \
        ["Do prevent test start: only in a “test is waiting to start” handler"]
    assert messages([proc({"type": "wait", "mode": "event", "event": "test_waiting"})]) == \
        ["Wait: cannot wait for the test to start"]


def _frame(x):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, 100, 0)
    return np.dstack([img] * 3)


def test_live_session_waits_for_the_pre_test_section():
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    procs = [proc(WHEN("test_waiting", [DO("prevent_test_start"), {"type": "wait", "mode": "event",
                                                                   "event": "key_down", "key": "g"},
                                        DO("allow_test_start")]))]
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=1.0, start_mode="immediate",
                    procedures=procs, test_info={"animal": "7", "trial": 2})
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    for i in range(10):
        s.process(_frame(80), i / 25)
    assert s.state == "waiting"
    s.key("x")
    s.process(_frame(80), 10 / 25)
    assert s.state == "waiting"
    s.key("g")  # keys reach the pre-test handlers
    s.process(_frame(80), 11 / 25)
    assert s.state == "running"
    info = s.engine._test_info()
    assert (info["animal"], info["trial"]) == ("7", 2)


# ====================================================================== built-in variables
def test_built_in_test_information_and_clock():
    info = {"test": 3, "animal": "12", "apparatus": "Box 1", "stage": "Training", "trial": 4, "treatment": "Saline",
            "fields": {"Sex": "F", "Weight": "23.5"}}
    p = proc(*(VAR(n, v) for n, v in (
        ("a", "animal()"), ("st", "stage()"), ("tr", "trial()"), ("ap", "apparatus()"), ("tm", "treatment()"),
        ("sex", "animal_field('Sex')"), ("w", "animal_field('Weight')"), ("nofield", "animal_field('Nope')"),
        ("d", "date()"), ("tod", "time_of_day()"), ("run", "test_running()"))))
    eng = ProcedureEngine([p], context={"test": info})
    eng.clock = lambda: dt.datetime(2026, 10, 8, 13, 30, 15)
    eng.start(0)
    assert eng.vars == {"a": 12, "st": "Training", "tr": 4, "ap": "Box 1", "tm": "Saline", "sex": "F", "w": 23.5,
                        "nofield": "", "d": "2026-10-08", "tod": 13 * 3600 + 30 * 60 + 15, "run": 1}
    eng.pause(1)
    assert eng._eval(None, "test_paused() and not test_running()")
    # without a test: empty values
    eng = ProcedureEngine([proc(VAR("s", "stage()"), VAR("t", "trial()"))])
    eng.start(0)
    assert eng.vars == {"s": "", "t": 0}


def test_built_in_position_variables():
    app = templates.build("open_field", 0, 0, 200, 100, size_cm=40)  # 5 px per cm
    app.points.append(PointOfInterest("Obj", 150, 50))
    zone = app.zones[0].name
    app.sequences.append(Sequence("S", [zone, "Other"]))
    eng = ProcedureEngine([proc()], context={"apparatus_map": app})
    eng.start(0)
    eng.update_state(0.0, {"x": 50, "y": 50, "hx": 55, "hy": 50, "tx": 45, "ty": 51, "freezing": True})
    eng.update_state(2.0, {"freezing": False, "immobile": True})
    eng.update_state(3.0, {"zones": {zone: True}})
    eng.update_state(4.5, {"zones": {zone: False, "Other": True}})
    v = eng._eval
    assert v(None, "[head_x(), head_y(), tail_x(), tail_y()]") == [55, 50, 45, 51]
    assert v(None, "x_percent()") == pytest.approx(25) and v(None, "y_percent()") == pytest.approx(50)
    assert v(None, "point_distance('Obj')") == pytest.approx(20)  # 100 px = 20 cm
    assert v(None, "freezing_time()") == pytest.approx(2) and v(None, "immobile_time()") == pytest.approx(2.5)
    assert v(None, "sequence_duration('S')") == pytest.approx(1.5)
    assert v(None, "sequence_duration('none')") == 0
    inside = app.zones[0].shape.contains(50, 50)
    d = v(None, f"zone_distance('{zone}')")
    assert (d == 0) if inside else d > 0
    with pytest.raises(ExprError):
        v(None, "zone_distance('nope')")


def test_speaker_and_output_volts():
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"},
                        {"name": "box", "type": "virtual", "channels": [
                            {"name": "ao", "kind": "pwm", "max_v": 10}, {"name": "ao5", "kind": "pwm"}]}])
    p = proc(DO("tone", frequency=1000, duration=0.5), VAR("on", 0, result=True), SET("on", "speaker()"),
             DO("output_volts", channel="ao", volts=2.5), DO("output_volts", channel="ao5", volts=2.5),
             VAR("v", 0, result=True), SET("v", "output_volts('ao')"), DO("output_volts", channel="ao", volts=20))
    eng = run([p], 1.0, devices=dm, outputs_off_at_end=False)
    assert eng.result_variables == {"on": 1, "v": 2.5}
    assert eng.outputs_state[("box", "ao5")] == 0.5 and eng.outputs_state[("box", "ao")] == 1.0
    assert any("outside 0 – 10 V" in e for e in eng.errors)
    assert eng._eval(None, "speaker()") == 0  # the tone has ended
    # the old 0-1 level is unchanged
    eng = run([proc(DO("output_set", channel="ao", value=0.25))], 0.1, devices=dm, outputs_off_at_end=False)
    assert eng.outputs_state[("box", "ao")] == 0.25


# ====================================================================== kept variables per animal / apparatus
def test_variables_kept_per_animal_and_apparatus():
    p = proc(VAR("sessions", 0, keep="animal"), VAR("box_uses", 0, keep="apparatus"), VAR("total", 0, keep=True),
             SET("sessions", "sessions + 1"), SET("box_uses", "box_uses + 1"), SET("total", "total + 1"))
    store = {"total": 10}  # an old project's kept variable
    for animal, box in (("A1", "Box 1"), ("A1", "Box 2"), ("A2", "Box 1")):
        run([p], 0.1, variables=store, context={"test": {"animal": animal, "apparatus": box}})
    assert store == {"total": 13, "@animal": {"A1": {"sessions": 2}, "A2": {"sessions": 1}},
                     "@apparatus": {"Box 1": {"box_uses": 2}, "Box 2": {"box_uses": 1}}}
    # kept_variables (stored when the test is saved) and merging them
    eng = run([p], 0.1, variables=store, context={"test": {"animal": "A3", "apparatus": "Box 1"}},
              commit_kept=False)
    assert eng.kept_variables == {"total": 14, "@animal": {"A3": {"sessions": 1}},
                                  "@apparatus": {"Box 1": {"box_uses": 3}}}
    P.merge_kept_variables(store, eng.kept_variables)
    assert store["@animal"]["A3"] == {"sessions": 1} and store["@animal"]["A1"] == {"sessions": 2}
    assert store["total"] == 14 and store["@apparatus"]["Box 1"] == {"box_uses": 3}
    # describe / validate
    assert P.describe_statement(p["statements"][0]) == "Variable sessions = 0  (kept per animal)"
    assert messages([proc(VAR("x", 0, keep="cage"))]) == ["Variable: unknown keep option 'cage'"]
    assert P.keep_scope({"keep": True}) == "experiment" and P.keep_scope({}) is None


# ====================================================================== functions and constants
def test_shuffle_with_a_maximum_run_of_repeats():
    import random

    rng = random.Random(3)
    base = ["L"] * 10 + ["R"] * 10
    for _ in range(50):
        out = P.RANDOM_FUNCTIONS["shuffle"](rng, base, 2)
        assert sorted(out) == sorted(base)
        assert all(not (out[i] == out[i + 1] == out[i + 2]) for i in range(len(out) - 2))
    out = P.RANDOM_FUNCTIONS["shuffle"](rng, [1] * 4 + [2] * 2, 2)
    assert sorted(out) == [1, 1, 1, 1, 2, 2]
    with pytest.raises(ExprError):
        P.RANDOM_FUNCTIONS["shuffle"](rng, [1] * 5 + [2], 1)
    eng = run([proc(VAR("a", "shuffle([1, 1, 2, 2, 3, 3], 1)"))], 0.1)
    a = eng.vars["a"]
    assert sorted(a) == [1, 1, 2, 2, 3, 3] and all(a[i] != a[i + 1] for i in range(5))
    assert not P.check_expr("shuffle([1, 2], 1)")


def test_na_constant_and_is_undefined():
    ev = P.Evaluator(lambda n: (_ for _ in ()).throw(ExprError(n))).eval
    assert ev("is_undefined(#N/A)") == 1 and ev("is_undefined(NA)") == 1 and ev("is_undefined(none)") == 1
    assert ev("is_undefined(0)") == 0 and ev("is_undefined('')") == 0
    assert ev("'#N/A'") == "#N/A"  # text is left alone
    assert math.isnan(ev("#N/A + 1"))
    assert P.validate([proc(VAR("NA", 0))])  # a reserved name


def test_anymaze_maths_per_procedure():
    def look(n):
        raise ExprError(n)
    ev = P.Evaluator(look).eval
    assert ev("sind(30)") == pytest.approx(0.5) and ev("cosd(60)") == pytest.approx(0.5)
    assert ev("atan2d(1, 1)") == pytest.approx(45) and ev("asind(1)") == pytest.approx(90)
    assert ev("sin(pi / 2)") == pytest.approx(1) and ev("log(e)") == pytest.approx(1)  # Python's, as before
    am = P.Evaluator(look, anymaze=True).eval
    assert am("sin(30)") == pytest.approx(0.5) and am("log(1000)") == pytest.approx(3)
    assert am("atan(1)") == pytest.approx(45) and am("log(8, 2)") == pytest.approx(3)
    # in the engine: only the procedures with the option
    eng = run([proc(VAR("a", "sin(90)"), name="A", anymaze_maths=True), proc(VAR("b", "sin(90)"), name="B")], 0.1)
    assert eng.vars["a"] == pytest.approx(1) and eng.vars["b"] == pytest.approx(math.sin(90))


# ====================================================================== model, validation and edits
def test_new_statements_model_and_edits():
    for t in ("call", "label", "goto", "resolution"):
        st = P.new_statement(t)
        assert t in P.STATEMENT_TYPES and not P.describe_statement(st).startswith("Unknown")
    p = proc({"type": "if", "cond": "1", "body": [DO("mark", name="a")]}, DO("mark", name="b"))
    # add an else-if clause, add into it, indent into it and outdent back
    c = pe.add_elif(p, (0,))
    assert c == (0, "elif", 0) and P.is_branch(c) and P.branch_block(c) == (0, "elif", 0, "body")
    new = pe.add(p, c, P.new_statement("comment"))
    assert new == (0, "elif", 0, "body", 0)
    moved = pe.indent(p, (1,))
    assert moved == (0, "elif", 0, "body", 1) and P.statement_at(p, moved)["name"] == "b"
    assert pe.outdent(p, moved) == (1,) and P.statement_at(p, (1,))["name"] == "b"
    assert pe.duplicate(p, c) is None and pe.move(p, c, 1) is None
    assert pe.remove(p, c) == (0,) and "elif" not in p["statements"][0]
    # sub-procedures and the ANY-maze maths option are kept by normalisation
    q = P.normalize_procedures([proc(name="S", sub=True, anymaze_maths=True)])[0]
    assert q["sub"] and q["anymaze_maths"]
    # an old project file without any of this still loads, validates and runs
    old = [{"trigger": "time", "time_s": 1, "action": "mark", "payload": "Tone"}]
    assert not validate(old) and marks(run(old, 1.5)) == ["Tone"]


def test_catalogue_entries():
    assert P.EVENT_SPECS["test_waiting"]["group"] == "Test"
    for a in ("prevent_test_start", "allow_test_start", "run_subprocedure", "output_volts"):
        assert a in P.ACTION_SPECS and hasattr(ProcedureEngine, "_a_" + a)
    assert "wait_event" in P.LOCAL_NAMES and "return" in P.STOP_WHAT
    assert P.test_context.__doc__
