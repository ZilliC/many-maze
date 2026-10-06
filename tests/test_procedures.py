"""Procedure engine: statements, timing, events, actions, variables, safety of expressions, old format."""

import json

import pytest

from manymaze.core import procedures as P
from manymaze.core.iodevices import DeviceManager
from manymaze.core.operant import Schedule, fleshler_hoffman, parse_spec, progressive_ratio
from manymaze.core.procedures import (ACTION_SPECS, EVENT_SPECS, EXAMPLES, Evaluator, ExprError, ProcedureEngine,
                                      check_expr, convert_rule, normalize_procedures, validate)

FPS = 25


def W(seconds):
    return {"type": "wait", "mode": "seconds", "seconds": seconds}


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def proc(*stmts, name="P"):
    return {"name": name, "enabled": True, "statements": list(stmts)}


def run(procs, seconds, state_fn=None, fps=FPS, **kw):
    """Run an engine over `seconds` of frames; state_fn(t) -> state dict."""
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


def mark_times(eng, name=None):
    return [t for n, t in eng.marks_list if name is None or n == name]


# ====================================================================== expressions
def ev(src, **names):
    def look(n):
        if n in names:
            return names[n]
        raise ExprError(f"unknown variable '{n}'")
    return Evaluator(look).eval(src)


def test_expression_arithmetic_and_functions():
    assert ev("1 + 2 * 3") == 7
    assert ev("(1 + 2) * 3 / 2") == 4.5
    assert ev("7 // 2 + 7 % 2 + 2 ** 3") == 12
    assert ev("x > 2 and not y", x=3, y=0) is True
    assert ev("a if c else b", a=1, b=2, c=0) == 2
    assert ev("1 < x <= 3", x=3) is True
    assert ev("max(1, 5, 3) + min([4, 2])") == 7
    assert ev("round(sqrt(2), 2)") == 1.41
    assert ev("floor(2.7) + ceil(2.1) + abs(-1)") == 6
    assert ev("arr[1] + arr[-1] + len(arr)", arr=[1, 2, 3]) == 8
    assert ev("arr[1:]", arr=[1, 2, 3]) == [2, 3]
    assert ev("'b' in ['a', 'b']") is True
    assert ev("sum(array(4, 2))") == 8
    assert ev("mean([1, 2, 3])") == 2
    assert ev("clamp(12, 0, 10)") == 10
    assert ev("true and pi > 3") is True
    assert ev("[1, 2] + [3]") == [1, 2, 3]
    r = Evaluator(lambda n: 0)
    for _ in range(50):
        assert 3 <= r.eval("randint(3, 5)") <= 5
        assert 0 <= r.eval("random()") < 1
        assert r.eval("choice([7, 8])") in (7, 8)
    assert sorted(r.eval("shuffle([1, 2, 3])")) == [1, 2, 3]


@pytest.mark.parametrize("src", [
    "__import__('os').system('echo hi')", "().__class__.__bases__[0].__subclasses__()", "open('/etc/passwd')",
    "eval('1')", "exec('x=1')", "x.real", "[i for i in range(3)]", "lambda: 1", "{'a': 1}", "{1, 2}",
    "f'{x}'", "x := 3", "getattr(x, 'y')", "globals()", "(yield)", "a = 1", "import os", "*x", "max(*[1, 2])",
    "max(a=1)", "_x", "1 if", "",
])
def test_expression_rejects_unsafe_or_invalid(src):
    with pytest.raises(ExprError):
        ev(src, x=1)
    assert check_expr(src)


def test_expression_resource_limits():
    for src in ("9 ** 9 ** 9", "'a' * 10 ** 9", "[0] * 10 ** 9", "array(10 ** 9)", "range(10 ** 9)",
                "2 ** 100000", "(" * 300 + "1" + ")" * 300, "1" + " + 1" * 1000):
        with pytest.raises(ExprError):
            ev(src)
    with pytest.raises(ExprError):
        ev("1 / 0")
    with pytest.raises(ExprError):
        ev("[1][5]")
    with pytest.raises(ExprError):
        ev("'a' < 1")
    with pytest.raises(ExprError):
        ev("nosuch + 1")


def test_check_expr_reports_names_and_arity():
    assert check_expr("a + b", {"a"}) == ["unknown variable 'b'"]
    assert check_expr("max()", set())[0].startswith("max() takes")
    assert "unknown function 'foo'" in check_expr("foo(1)")[0]
    assert check_expr("zone('Centre') and input('box', 'lever')", set()) == []


def test_text_interpolation():
    assert P.interpolate("n={n}, t={{x}}", lambda e: ev(e, n=3)) == "n=3, t={x}"


# ====================================================================== statements & timing
def test_sequential_timing_is_frame_accurate_and_does_not_drift():
    pr = proc(W(30), DO("mark", name="Tone"), W(5), DO("mark", name="Shock"),
              {"type": "repeat", "mode": "count", "count": 3, "body": [W(0.3), DO("mark", name="Tick")]})
    eng = run([pr], 40, fps=7)  # frame period does not divide the waits
    tone, shock = mark_times(eng, "Tone")[0], mark_times(eng, "Shock")[0]
    assert 30 <= tone < 30 + 1 / 7 and 35 <= shock < 35 + 1 / 7
    ticks = mark_times(eng, "Tick")
    for k, t in enumerate(ticks):  # due times 35.3, 35.6, 35.9: never drift by more than a frame
        assert 35 + 0.3 * (k + 1) <= t + 1e-9 < 35 + 0.3 * (k + 1) + 1 / 7
    assert eng.errors == []


def test_nested_if_else_loops_and_variables():
    pr = proc(
        {"type": "var", "name": "n", "value": 0},
        {"type": "var", "name": "seen", "value": "[]"},
        {"type": "repeat", "mode": "count", "count": 4, "var": "i", "body": [
            {"type": "if", "cond": "i % 2 == 0", "body": [
                {"type": "set", "var": "n", "value": "n + 10"},
                {"type": "repeat", "mode": "while", "while": "n % 3 != 0", "body": [
                    {"type": "set", "var": "n", "value": "n + 1"}]}],
             "else": [DO("array_append", var="seen", value="i"), W(1)]}]},
        DO("mark", name="done {n}"))
    eng = run([pr], 5)
    assert eng.vars["seen"] == [1, 3]
    # i=0: 10 -> 12; i=2: 22 -> 24
    assert eng.vars["n"] == 24
    assert eng.marks_list[0][0] == "done 24"
    assert eng.marks_list[0][1] == pytest.approx(2.0)  # two 1-s waits; the loops themselves take no time
    assert eng.errors == []


def test_concurrent_handlers_and_modes():
    zones = lambda t: {"zones": {"A": 1 <= t < 2 or 3 <= t < 3.5 or 4 <= t < 6}}
    body = [DO("mark", name="in"), W(1), DO("mark", name="out")]
    for mode, n_in, n_out in (("ignore", 2, 2), ("parallel", 3, 3), ("restart", 3, 2)):
        pr = proc(WHEN("zone_enter", [dict(b) for b in body], zone="A", mode=mode))
        eng = run([pr], 8, zones)
        assert len(mark_times(eng, "in")) == n_in, mode
        assert len(mark_times(eng, "out")) == n_out, mode
    # two procedures at once: a timed sequence keeps running while a handler waits
    p1 = proc(W(1), DO("mark", name="p1a"), W(2), DO("mark", name="p1b"), name="one")
    p2 = proc(WHEN("every", [DO("mark", name="tick"), W(0.5), DO("mark", name="tock")], interval=1), name="two")
    eng = run([p1, p2], 3.4, fps=10)
    assert mark_times(eng, "p1a") == [1.0] and mark_times(eng, "p1b") == [3.0]
    assert mark_times(eng, "tick") == [1.0, 2.0, 3.0]
    assert mark_times(eng, "tock") == [1.5, 2.5]


def test_once_wait_until_timeout_and_wait_for_event():
    pr = proc(
        WHEN("zone_enter", [DO("mark", name="first")], zone="A", once=True),
        {"type": "wait", "mode": "until", "until": "zone('A')", "timeout": 10},
        DO("mark", name="arrived {timed_out}"),
        {"type": "wait", "mode": "event", "event": "zone_exit", "zone": "A", "timeout": 5},
        DO("mark", name="left {timed_out}"),
        {"type": "wait", "mode": "event", "event": "key_down", "key": "x", "timeout": 2},
        DO("mark", name="key {timed_out}"))
    eng = run([pr], 12, lambda t: {"zones": {"A": 2 <= t < 3 or t >= 4}})
    names = [n for n, _ in eng.marks_list]
    assert names == ["first", "arrived 0", "left 0", "key 1"]
    assert mark_times(eng) == [2.0, 2.0, 3.0, 5.0]


def test_stop_statements():
    pr = proc(
        {"type": "repeat", "mode": "forever", "var": "i", "body": [
            W(1), {"type": "if", "cond": "i == 2", "body": [{"type": "stop", "what": "loop"}]}]},
        DO("mark", name="after loop"),
        WHEN("time_reached", [DO("mark", name="t5"), {"type": "stop", "what": "handler"}, DO("mark", name="no")],
             time=5),
        WHEN("time_reached", [{"type": "stop", "what": "test"}, DO("mark", name="never")], time=7))
    ended = []
    eng = run([pr, proc(WHEN("every", [DO("mark", name="ev")], interval=2), name="Q")], 10)
    assert mark_times(eng, "after loop") == [3.0]
    assert mark_times(eng, "t5") == [5.0] and not mark_times(eng, "no") and not mark_times(eng, "never")
    assert eng.ended and eng.stopped and mark_times(eng, "ev") == [2.0, 4.0, 6.0]
    pr2 = proc(WHEN("time_reached", [{"type": "stop", "what": "procedure"}], time=1),
               WHEN("every", [DO("mark", name="x")], interval=0.5))
    eng = run([pr2], 3, fps=10)
    assert mark_times(eng, "x") == [0.5]
    del ended


def test_end_test_callback_and_test_end_handlers():
    calls = []
    pr = proc(WHEN("time_reached", [DO("end_test")], time=2),
              WHEN("test_end", [DO("log", text="bye at {time()}")]))
    eng = ProcedureEngine([pr], on_end=lambda: calls.append("end"), on_log=lambda m, t: calls.append(m))
    eng.start(0)
    for i in range(100):
        eng.update_state(i / 10, {})
        if eng.stopped:
            break
    assert calls == ["end", "bye at 2"] and eng.ended and eng.stopped


def test_runtime_errors_are_reported_not_raised():
    pr = proc({"type": "set", "var": "x", "value": "1 / 0"}, DO("log", text="{nosuch}"),
              {"type": "if", "cond": "[1][3]", "body": [DO("mark", name="no")]},
              {"type": "set", "var": "arr", "value": "[1]"}, {"type": "set", "var": "arr", "index": 5, "value": 1},
              W("bad +"), DO("mark", name="still running"), DO("no_such_action"))
    eng = run([pr], 1)
    assert mark_times(eng, "still running") == [0.0]
    assert len(eng.errors) == 6
    assert any("division by zero" in e for e in eng.errors)


def test_infinite_loop_without_wait_does_not_hang():
    pr = proc({"type": "repeat", "mode": "forever", "body": [{"type": "set", "var": "n", "value": "n + 1"}]},
              {"type": "var", "name": "n", "value": 0})
    eng = run([pr], 1)  # polls once per frame: at start() and in each of the 26 frames
    assert eng.vars["n"] == 27
    pr = proc({"type": "repeat", "mode": "count", "count": 10 ** 6, "body": [{"type": "set", "var": "m", "value": 1}]})
    eng = run([pr], 0.2)  # step budget spreads a huge counted loop over frames
    assert eng.errors == [] and not eng.threads


def test_signals_variables_and_condition_events():
    pr = proc(
        {"type": "var", "name": "count", "value": 0},
        WHEN("every", [DO("increment", var="count", by=1)], interval=1),
        WHEN("variable_changed", [{"type": "if", "cond": "count == 3", "body": [DO("signal", name="go")]}],
             var="count"),
        WHEN("signal", [DO("mark", name="go {count}")], name="go"),
        WHEN("condition_true", [DO("mark", name="big")], cond="count >= 4"),
        WHEN("condition_false", [DO("mark", name="small")], cond="count >= 4"))
    eng = run([pr], 6)
    assert eng.marks_list[0] == ("small", 0.0)
    assert ("go 3", 3.0) in eng.marks_list and ("big", 4.0) in eng.marks_list


def test_event_loop_guard():
    pr = proc(WHEN("signal", [DO("signal", name="a")], name="a", mode="parallel"),
              WHEN("test_start", [DO("signal", name="a")]))
    eng = run([pr], 0.2)
    assert any("too many events" in e for e in eng.errors)


# ====================================================================== events
def test_zone_and_animal_events():
    def st(t):
        return {"zones": {"A": 1 <= t < 4, "B": t >= 4}, "head_zones": {"A": 0.8 <= t < 4},
                "freezing": 2 <= t < 3, "immobile": 2 <= t < 3.5, "detected": not 5 <= t < 5.5,
                "x": t * 10, "y": 0.0, "speed": 10.0 if 1 <= t < 2 else 1.0}
    events = ["zone_enter", "zone_exit", "head_zone_enter", "head_zone_exit", "freezing_start", "freezing_end",
              "immobile_start", "immobile_end", "animal_lost", "animal_found"]
    pr = proc(*[WHEN(e, [DO("mark", name=e + " {event_name}")], mode="parallel") for e in events],
              WHEN("zone_time_reaches", [DO("mark", name="ztime")], zone="A", seconds=2),
              WHEN("zone_dwell", [DO("mark", name="dwell")], zone="B", seconds=1.5),
              WHEN("zone_entries_reach", [DO("mark", name="entries")], zone="B", count=1),
              WHEN("speed_above", [DO("mark", name="fast")], threshold=5),
              WHEN("speed_below", [DO("mark", name="slow")], threshold=5),
              WHEN("distance_reaches", [DO("mark", name="dist")], distance=25),
              WHEN("freezing_time_reaches", [DO("mark", name="frz")], seconds=0.5),
              WHEN("immobile_time_reaches", [DO("mark", name="imm")], seconds=1.4))
    eng = run([pr], 7, st, fps=10)
    m = dict((n, t) for n, t in reversed(eng.marks_list))
    assert m["zone_enter A"] == 1.0 and m["zone_exit A"] == 4.0 and m["zone_enter B"] == 4.0
    assert m["head_zone_enter A"] == 0.8 and m["head_zone_exit A"] == 4.0
    assert m["freezing_start "] == 2.0 and m["freezing_end "] == 3.0
    assert m["immobile_start "] == 2.0 and m["immobile_end "] == 3.5
    assert m["animal_lost "] == 5.0 and m["animal_found "] == 5.5
    assert m["ztime"] == 3.0 and m["dwell"] == 5.5 and m["entries"] == 4.0
    assert m["fast"] == 1.0 and m["slow"] == 0.0
    assert m["dist"] == 2.5 and m["frz"] == 2.5 and m["imm"] == 3.4
    assert eng.zone_entries == {"A": 1, "B": 1} and eng.zone_time["A"] == pytest.approx(3.0)


def test_zone_sequence_event():
    seq = ["C", "A", "B", "C", "A", "C", "B", "A", "B", "C"]

    def st(t):
        i = int(t)
        return {"zones": {z: (i < len(seq) and seq[i] == z) for z in "ABC"}}
    pr = proc(WHEN("zone_sequence", [DO("mark", name="abc")], sequence="A, B, C", mode="parallel"))
    eng = run([pr], len(seq), st, fps=4)
    assert mark_times(eng, "abc") == [3.0, 9.0]


def test_keys_timers_and_switches():
    pr = proc(
        WHEN("key_down", [DO("start_timer", timer="t1", seconds=2), DO("virtual_switch_on", switch="s")], key="s"),
        WHEN("key_up", [DO("stop_timer", timer="t1"), DO("mark", name="held {round(timer('t1'), 2)}")], key="S"),
        WHEN("timer_elapsed", [DO("mark", name="elapsed")], timer="t1"),
        WHEN("virtual_switch_on", [DO("mark", name="sw")], switch="s"),
        WHEN("key_down", [DO("mark", name="any {event_name}")], mode="parallel"))
    eng = ProcedureEngine([pr])
    marks = []
    eng.on_mark = lambda n, t: marks.append((n, t))
    eng.start(0)
    for i in range(0, 80):
        t = i / 10
        if i == 10:
            eng.key(t, "s", True)
            eng.key(t, "s", True)  # auto-repeat is ignored
        if i == 15:
            eng.key(t, "s", False)
        if i == 20:
            eng.key(t, "S", True)
        if i == 21:
            eng.key(t, "q", True)
        eng.update_state(t, {})
    eng.stop(8)
    names = [n for n, _ in marks]
    assert names[:4] == ["any s", "sw", "held 0.5", "any S"]
    assert ("elapsed", pytest.approx(3.5)) in [(n, pytest.approx(t)) for n, t in marks if n == "elapsed"] or \
        [t for n, t in marks if n == "elapsed"] == [pytest.approx(3.5)]
    assert "any q" in names
    assert any(e["device"] == "virtual" and e["channel"] == "s" and e["type"] == "switch" for e in eng.io_events)


def test_all_catalogued_events_and_actions_run():
    """Every event and action can be used with its default parameters without internal errors."""
    procs = []
    for name in EVENT_SPECS:
        st = WHEN(name, [DO("mark", name=name)], **P.spec_defaults(EVENT_SPECS[name]))
        for prm in EVENT_SPECS[name]["params"]:
            if prm["req"] and prm["default"] is None or prm["default"] == "":
                st[prm["name"]] = {"cond": "time() > 1", "sequence": "A, B"}.get(prm["name"], "x")
        procs.append(proc(st, name=name))
    body = []
    for name, spec in ACTION_SPECS.items():
        if name in ("end_test", "disable_procedure"):
            continue
        st = DO(name, **P.spec_defaults(spec))
        for prm in spec["params"]:
            if prm["req"] and not st.get(prm["name"]):
                st[prm["name"]] = {"spec": "FR 2", "procedure": "time_reached", "value": "1"}.get(prm["name"], "x")
        body.append(st)
    procs.append(proc(W(0.5), *body, name="actions"))
    stim = []
    eng = run(procs, 3, lambda t: {"zones": {"A": t > 1}}, on_stimulus=lambda c, p: stim.append(c))
    bad = [e for e in eng.errors if "internal" in e or "unexpected" in e]
    assert not bad, bad
    assert len(EVENT_SPECS) >= 40 and len(ACTION_SPECS) >= 40
    assert stim == ["show", "hide", "clear"]


# ====================================================================== I/O through the engine
def test_virtual_inputs_outputs_and_io_log():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"}, {"name": "light", "kind": "output"},
        {"name": "wheel", "kind": "encoder", "counts_per_rev": 100}, {"name": "force", "kind": "analog"}]}])
    pr = proc(
        {"type": "var", "name": "rewards", "value": 0, "result": True},
        WHEN("test_start", [DO("schedule_start", schedule="s", spec="FR 2"), DO("output_on", channel="light")]),
        WHEN("input_on", [DO("schedule_response", schedule="s", var="due"),
                          {"type": "if", "cond": "due", "body": [
                              DO("pellet", channel="pellet", count=1, pulse_width=50),
                              DO("increment", var="rewards")]}], device="box", channel="lever", mode="parallel"),
        WHEN("input_count_reaches", [DO("mark", name="5 presses")], channel="lever", count=5),
        WHEN("encoder_every", [DO("mark", name="rev")], channel="wheel", count=100, mode="parallel"),
        WHEN("encoder_reaches", [DO("mark", name="250")], channel="wheel", count=250),
        WHEN("analog_above", [DO("mark", name="push")], channel="force", threshold=500))
    eng = ProcedureEngine([pr], dm)
    marks = []
    eng.on_mark = lambda n, t: marks.append((n, t))
    eng.start(0)
    box = dm.devices["box"]
    for i in range(0, 101):
        t = i / 10
        if i % 10 == 1 and i < 60:
            box.set_input("lever", 1)
        if i % 10 == 3:
            box.set_input("lever", 0)
        if i % 5 == 0:
            box.add_counts("wheel", 30)
        box.set_input("force", 600 if 40 <= i < 50 else 100)
        eng.update_state(t, {})
    eng.stop(10)
    assert eng.result_variables == {"rewards": 3}
    assert eng.input_counts[("box", "lever")] == 6 and eng.vars["due"] == 1
    pel = [e for e in eng.io_events if e["channel"] == "pellet"]
    assert [e["value"] for e in pel] == [1, 0] * 3 and all(e["type"] == "pellet" for e in pel)
    assert pel[1]["t"] - pel[0]["t"] == pytest.approx(0.05)
    assert [n for n, _ in marks].count("rev") == 6 and ("250", pytest.approx(4.0)) == (marks[-3][0], marks[-3][1]) \
        or "250" in [n for n, _ in marks]
    assert [t for n, t in marks if n == "push"] == [4.0]
    assert [t for n, t in marks if n == "5 presses"] == [pytest.approx(4.1)]
    light = [e for e in eng.io_events if e["channel"] == "light"]
    assert [(e["t"], e["value"]) for e in light] == [(0, 1), (10, 0)]  # all outputs off at the end
    assert box.outputs["light"] == 0
    ts = [e["t"] for e in eng.io_events]
    assert ts == sorted(ts)
    for e in eng.io_events:
        assert set(e) >= {"t", "device", "channel", "kind", "value"} and e["kind"] in ("input", "output")
    json.dumps(eng.io_events)


def test_pulse_train_and_shock_safety():
    pr = proc(DO("pulse_train", channel="laser", frequency=10, pulse_width=20, duration=1),
              DO("shock_on", channel="shock", max_duration=1.5),
              W(3), DO("pulse_train", channel="laser", frequency=5, pulse_width=100, duration=0),
              W(1.1), DO("pulse_train_stop", channel="laser"),
              DO("output_pulse", channel="ttl", duration=0.25),
              DO("sync_pulse", channel="sync", width=10),
              DO("shock_pulse", channel="shock", duration=0.5))
    eng = run([pr], 6, fps=10)
    laser = [e for e in eng.io_events if e["channel"] == "laser"]
    ons = [e["t"] for e in laser if e["value"] == 1]
    assert ons[:10] == pytest.approx([k / 10 for k in range(10)])
    assert len(ons) == 10 + 6  # 1 s at 10 Hz, then 1.1 s at 5 Hz (pulses at 0, .2, .4, .6, .8, 1.0)
    offs = [e["t"] for e in laser if e["value"] == 0]
    assert offs[0] == pytest.approx(0.02) and len(offs) == len(ons)
    assert sum(1 for e in laser if e.get("train_start")) == 2
    shock = [(e["t"], e["value"]) for e in eng.io_events if e["channel"] == "shock"]
    assert shock[:2] == [(0, 1), (1.5, 0)]  # safety cut-off
    assert shock[2:] == [(pytest.approx(4.1), 1), (pytest.approx(4.6), 0)]
    assert any("safety" in m for _, m in eng.log_lines)
    ttl = [(e["t"], e["value"]) for e in eng.io_events if e["channel"] == "ttl"]
    assert ttl == [(pytest.approx(4.1), 1), (pytest.approx(4.35), 0)]
    m = P.io_measures(eng.io_events, 6)
    assert m["laser: pulse trains"] == 2 and m["laser: pulses"] == 16
    assert m["shock: times on"] == 2 and m["shock: time on (s)"] == pytest.approx(2.0)


def test_audio_actions_logged_without_device():
    pr = proc(DO("tone", frequency=3000, duration=2, volume=0.3), W(1), DO("white_noise", duration=5),
              W(1), DO("stop_sound"))
    eng = run([pr], 4)
    aud = [(e["channel"], e["t"], e["value"]) for e in eng.io_events if e.get("type") == "audio"]
    assert aud == [("tone", 0, 3000), ("noise", 1, 1), ("tone", 2, 0), ("noise", 2, 0)]


def test_audio_device_receives_commands():
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    run([proc(DO("tone", frequency=4000, duration=0.05), DO("beep"))], 0.2, devices=dm)
    assert [c for c, _ in dm.devices["spk"].played] == ["tone", "tone"]
    assert dm.devices["spk"].played[0][1]["frequency"] == 4000


def test_serial_action_and_unknown_device_report():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [{"name": "led", "kind": "output"}]}])
    eng = run([proc(DO("output_on", device="nobox", channel="led"), DO("serial_send", text="HELLO"))], 0.1,
              devices=dm)
    assert any("nobox" in e and "not configured" in e for e in eng.errors)
    assert eng.outputs.log == ["serial: HELLO"]


def test_touch_marks_pause_and_procedure_enable():
    paused = []
    pr = proc(WHEN("touch", [DO("mark", name="hit {event_name}"), DO("pause_test")], area="left"),
              WHEN("touch_outside", [DO("mark", name="miss")]),
              WHEN("test_paused", [W(1), DO("resume_test")]),
              WHEN("time_reached", [DO("mark_start", name="Light"), DO("enable_procedure", procedure="Later")], time=1),
              WHEN("event_marked", [DO("log", text="marked {event_name}")], name="hit left"))
    later = proc(WHEN("every", [DO("mark", name="later")], interval=1), name="Later")
    later["enabled"] = False
    eng = ProcedureEngine([pr, later], on_pause=lambda t: paused.append(t), on_resume=lambda t: paused.append(-t))
    marks = []
    eng.on_mark = lambda n, t: marks.append((n, t))
    eng.start(0)
    for i in range(50):
        t = i / 10
        if i == 20:
            eng.touch(t, "left", 0.1, 0.2)
        if i == 25:
            eng.touch(t, None)
        eng.update_state(t, {})
    eng.stop(5)
    assert [n for n, _ in marks if n != "later"] == ["hit left", "miss"]
    assert paused == [2.0, -3.0] and eng.pauses == [[2.0, 3.0]]
    assert eng.state_events == [{"behaviour": "Light", "t": 1.0, "t_end": 5.0}]
    assert [t for n, t in marks if n == "later"] == [1.1, 2.0, 3.0, 4.0]  # enabled at 1 s: catches up
    assert any(m == "marked hit left" for _, m in eng.log_lines)


# ====================================================================== variables persistence & results
def test_variables_persist_between_tests_and_results():
    pr = proc({"type": "var", "name": "session", "value": 0, "keep": True, "result": True},
              {"type": "var", "name": "trials", "value": "[]", "keep": True},
              {"type": "var", "name": "tmp", "value": 5, "result": True},
              {"type": "var", "name": "label", "value": "'x'", "result": True},
              {"type": "set", "var": "session", "value": "session + 1"},
              DO("array_append", var="trials", value="session * 10"),
              {"type": "set", "var": "tmp", "value": "tmp * 2"})
    shared = {}
    for k in (1, 2, 3):
        eng = ProcedureEngine([pr], variables=shared)
        eng.start(0)
        eng.update_state(0.04, {})
        eng.stop(1)
        assert eng.result_variables == {"session": k, "tmp": 10}
    assert shared == {"session": 3, "trials": [10, 20, 30]}
    json.dumps(shared)


def test_array_element_assignment_does_not_alias():
    pr = proc({"type": "var", "name": "a", "value": "[1, 2, 3]"},
              {"type": "set", "var": "b", "value": "a"},
              {"type": "set", "var": "b", "index": 0, "value": 99},
              {"type": "set", "var": "a", "index": "-1", "value": "a[0] + b[0]"})
    eng = run([pr], 0.1)
    assert eng.vars["a"] == [1, 2, 100] and eng.vars["b"] == [99, 2, 3]


# ====================================================================== old format
OLD_RULES = [{"trigger": "zone_enter", "zone": "Right", "action": "serial", "payload": "ON"},
             {"trigger": "zone_exit", "zone": "Right", "action": "serial", "payload": "OFF"},
             {"trigger": "time", "time_s": 2, "action": "mark", "payload": "Tone"},
             {"trigger": "freezing_start", "action": "log", "payload": "froze"},
             {"trigger": "not_detected", "action": "beep"},
             {"trigger": "zone_enter", "zone": "Goal", "action": "end_test", "delay_s": 1}]


def test_old_rules_are_converted_and_old_api_works():
    procs = normalize_procedures(OLD_RULES)
    assert len(procs) == len(OLD_RULES) and all("statements" in p for p in procs)
    assert procs[5]["statements"][0]["body"][0] == {"type": "wait", "mode": "seconds", "seconds": 1}
    assert validate(OLD_RULES) == []
    assert normalize_procedures(procs) == procs  # idempotent
    json.dumps(procs)
    marks, ended = [], []
    eng = ProcedureEngine(OLD_RULES, P.Outputs(), on_mark=lambda n, t: marks.append((n, t)),
                          on_end=lambda: ended.append(1))
    eng.start(0)
    for i in range(60):
        t = i / 10
        eng.update(t, {"Right": 1.0 <= t < 3.0, "Goal": t >= 4.0}, detected=True, freezing=t >= 3.2)
    assert eng.outputs.log[:2] == ["serial: ON", "serial: OFF"]
    assert "3.20s froze" in eng.outputs.log
    assert marks == [("Tone", 2.0)]
    assert eng.ended and ended == [1] and eng.stopped
    assert eng.fired and all(len(f) == 4 for f in eng.fired)


def test_convert_rule_describes_and_mixed_lists():
    p = convert_rule({"trigger": "time", "time_s": 30, "action": "mark", "payload": "Tone", "delay_s": 2})
    assert p["name"] == "When at 30 s after 2 s: mark Tone"
    mixed = normalize_procedures([OLD_RULES[0], EXAMPLES["Fear conditioning (tone + shock)"]])
    assert mixed[1]["name"] == "Fear conditioning"


# ====================================================================== validation
def test_validate_reports_errors_with_paths():
    bad = proc(
        WHEN("zone_enter", [{"type": "when", "event": "test_start", "body": []}], zone="Nowhere"),
        WHEN("no_event", []),
        {"type": "if", "cond": "x >", "body": [{"type": "stop", "what": "loop"}], "else": [DO("tone", frequency="")]},
        {"type": "repeat", "mode": "while", "while": "", "body": []},
        {"type": "set", "var": "1x", "value": "2"},
        {"type": "set", "var": "pi", "value": "unknown_var + 1"},
        DO("output_on", device="box", channel="lever"),
        DO("pellet", device="nobox", channel="feeder", count="-"),
        DO("schedule_response", schedule="s"),
        DO("schedule_start", schedule="s2", spec="XR 3"),
        DO("log", text="value {1 +}"),
        {"type": "var", "name": "v", "value": 1}, {"type": "var", "name": "v", "value": 2},
        {"type": "wait", "mode": "event", "event": "zone_sequence", "sequence": "A"},
        {"type": "bogus"}, name="")
    ctx = {"zones": ["A", "B"], "devices": [{"name": "box", "type": "arduino", "channels": [
        {"name": "lever", "kind": "input"}, {"name": "pellet", "kind": "output"}]}]}
    issues = validate([bad, proc(name="X"), proc(name="X")], ctx)
    paths = {(pi, path) for pi, path, _ in issues}
    text = "\n".join(f"{pi} {P.path_text(path)} {m}" for pi, path, m in issues)
    for expect in ["unknown zone 'Nowhere'", "only allowed at the top level", "unknown event 'no_event'",
                   "syntax error", "not inside a repeat", "Frequency (Hz) is required", "Condition is required",
                   "'1x' is not a valid name", "'pi' is a reserved name", "unknown variable 'unknown_var'",
                   "'lever' is an input channel, not an output", "unknown device 'nobox'",
                   "give a schedule", "unknown schedule 'XR 3'", "declared more than once",
                   "at least two zones", "unknown statement type 'bogus'", "the procedure has no name",
                   "another procedure is also called 'X'"]:
        assert expect in text, expect
    assert (0, (0, "body", 0)) in paths and (0, (2, "else", 0)) in paths and (0, ()) in paths
    for ex in EXAMPLES.values():
        assert validate([ex]) == []


def test_describe_statements():
    assert P.describe_statement(W(5)) == "Wait 5 s"
    assert P.describe_statement({"type": "repeat", "mode": "while", "while": "x < 3"}) == "Repeat while x < 3"
    when = WHEN("zone_enter", [], zone="A", mode="restart")
    assert P.describe_statement(when).startswith("When animal enters zone (A)")
    assert "laser" in P.describe_statement(DO("pulse_train", device="box", channel="laser", frequency=20))
    for t in P.STATEMENT_TYPES:
        assert P.describe_statement(P.new_statement(t))


# ====================================================================== operant schedules
def test_operant_schedules():
    import random
    s = Schedule("FR 3")
    assert [s.response(i) for i in range(7)] == [False, False, True, False, False, True, False]
    s = Schedule("CRF")
    assert all(s.response(i) for i in range(3))
    s = Schedule("EXT")
    assert not any(s.response(i) for i in range(10))
    s = Schedule("VR 5", random.Random(0))
    n = sum(s.response(i) for i in range(5000))
    assert 850 < n < 1150
    s = Schedule("FI 10")
    assert [t for t in range(0, 35) if s.response(t)] == [10, 20, 30]
    s = Schedule("PR")
    req = []
    for _ in range(6):
        req.append(s.requirement)
        while not s.response(0):
            pass
    assert req == [1, 2, 4, 6, 9, 12] and s.breakpoint == 12
    assert [progressive_ratio(k, 3) for k in range(3)] == [3, 6, 9]
    assert sum(fleshler_hoffman(30)) / 10 == pytest.approx(30)
    s = Schedule("FT 5")
    assert [t for t in range(0, 21) if s.tick(t)] == [5, 10, 15, 20]
    s = Schedule("VI 10", random.Random(1))
    assert 0 < sum(s.response(t * 0.5) for t in range(400)) < 30
    for bad in ("XR 3", "FR", "FR 0", ""):
        with pytest.raises(ValueError):
            parse_spec(bad)
