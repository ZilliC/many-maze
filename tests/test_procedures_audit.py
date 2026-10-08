"""Procedures: the fixes of the 2026-10-08 audit (run_program authorisation, loops that cannot hang the frame
thread, contained errors, expression limits, sounds stopped, Stop / Disable procedure, event timing, the event
guard, indexed sets, encoders, dispensers, ramps, old rules and validation)."""

import sys
import time

import pytest

from manymaze.core import procedures as P
from manymaze.core.iodevices import AudioDevice, DeviceManager
from manymaze.core.procedures import Evaluator, ExprError, ProcedureEngine, programs, validate
from manymaze.core.procedures.detect import _Detector

FPS = 25


def W(seconds):
    return {"type": "wait", "mode": "seconds", "seconds": seconds}


def DO(action, **kw):
    return dict({"type": "do", "action": action}, **kw)


def MARK(name):
    return DO("mark", name=name)


def WHEN(event, body, **kw):
    return dict({"type": "when", "event": event, "body": body}, **kw)


def VAR(name, value, **kw):
    return dict({"type": "var", "name": name, "value": value}, **kw)


def SET(var, value, **kw):
    return dict({"type": "set", "var": var, "value": value}, **kw)


def REPEAT(body, **kw):
    return dict({"type": "repeat", "body": body}, **kw)


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


def marks(eng, name=None):
    return [t for n, t in eng.marks_list if name is None or n == name]


# ====================================================================== run a program
def test_unauthorised_program_is_not_run(tmp_path):
    out = tmp_path / "ran.txt"
    code = f"import pathlib; pathlib.Path(r'{out}').write_text('ok')"
    st = DO("run_program", program=sys.executable, arguments=f'-c "{code}"')
    eng = run([proc(st)], 0.1, program_policy=programs.ProgramPolicy([], persist=False))
    assert eng.programs == [] and not out.exists()
    assert any("was not run" in e and "allowed" in e for e in eng.errors)
    # the default policy: the per-user list (empty in the tests) and no confirmation callback -> refused
    assert programs.policy.confirm is None
    eng = run([proc(st)], 0.1)
    assert eng.programs == [] and any("was not run" in e for e in eng.errors)
    time.sleep(0.2)
    assert not out.exists()


def test_program_confirmation_and_allow_list(tmp_path):
    store = tmp_path / "allowed.json"
    asked = []
    pol = programs.ProgramPolicy(path=store, confirm=lambda path, args, ctx: asked.append(path) or False)
    st = DO("run_program", program=sys.executable, arguments='-c "pass"')
    eng = run([proc(st, W(0.1), st)], 0.3, program_policy=pol)
    assert eng.programs == [] and len(asked) == 1  # refused once: not asked again in this session
    assert programs.unauthorised_programs([proc(st)], pol) == [sys.executable]
    pol.confirm = lambda path, args, ctx: True  # the user allows it: remembered on this computer
    pol._refused.clear()
    eng = run([proc(st)], 0.1, program_policy=pol)
    assert len(eng.programs) == 1
    eng.programs[0].wait(10)
    again = programs.ProgramPolicy(path=store)  # a new session reads the list
    assert again.is_allowed(sys.executable) and programs.unauthorised_programs([proc(st)], again) == []
    again.disallow(sys.executable)
    assert not programs.ProgramPolicy(path=store).is_allowed(sys.executable)


def test_validation_warns_about_run_program():
    issues = validate([proc(DO("run_program", program="ls"))])
    assert len(issues) == 1 and P.is_warning(issues[0][2]) and "runs a program" in issues[0][2]
    assert validate([proc(DO("run_program", program="ls"))], warnings=False) == []
    assert P.check_before_test([proc(DO("run_program", program="ls"))]) == []


# ====================================================================== loops that must not hang
@pytest.mark.parametrize("body", [[], [{"type": "comment", "text": "x"}], [{"type": "label", "name": "l"}],
                                  [dict(MARK("off"), enabled=False)]])
def test_empty_loop_bodies_do_not_hang(body):
    t0 = time.monotonic()
    eng = run([proc(REPEAT(body, mode="while", **{"while": "1"})),
               proc(REPEAT(body, mode="until", until="zone('A')"), name="Q"),
               proc(W(0.2), MARK("alive"), name="R")], 0.5)
    assert time.monotonic() - t0 < 2
    assert marks(eng, "alive") == [0.2] and not eng.errors


def test_empty_count_loop_counts_against_the_step_budget():
    eng = run([proc(VAR("i", 0), REPEAT([], mode="count", count=12000, var="i"), MARK("done"))], 0.5)
    assert marks(eng, "done") == [0.04]  # 5000 empty iterations per pass: the start, then the frames at 0 and 0.04


def test_validation_flags_empty_loop_bodies():
    for body in ([], [{"type": "comment", "text": "x"}], [dict(MARK("x"), enabled=False)]):
        issues = validate([proc(REPEAT(body, mode="forever"))])
        assert [P.is_warning(m) and "nothing to do" in m for _pi, _p, m in issues] == [True]
    assert validate([proc(REPEAT([W(1)], mode="forever"))]) == []


@pytest.mark.parametrize("wait", [{"type": "wait", "mode": "until", "until": "1"}, W(0)])
def test_forever_loop_with_a_wait_that_does_not_wait_polls_once_per_frame(wait):
    eng = run([proc(VAR("n", 0, result=True), REPEAT([SET("n", "n + 1"), wait], mode="forever"))], 1.0)
    assert eng.result_variables["n"] <= 30 and not eng.errors


def test_forever_loop_with_real_waits_keeps_exact_times():
    eng = run([proc(REPEAT([MARK("tick"), W(0.5)], mode="forever"))], 2.0, fps=20)
    assert marks(eng, "tick") == [0, 0.5, 1.0, 1.5, 2.0]


# ====================================================================== contained errors
def test_empty_required_number_in_a_when_uses_its_default():
    pr = proc(WHEN("zone_time_reaches", [MARK("30 s")], zone="A", seconds=""),
              WHEN("every", [MARK("every")], interval=None),
              WHEN("zone_entries_reach", [MARK("entries")], zone="A", count="nope"),
              WHEN("time_reached", [MARK("other")], time=0.5))
    eng = run([pr], 40, lambda t: {"zones": {"A": True}}, fps=10)
    assert marks(eng, "other") == [0.5]  # the other handlers run
    assert marks(eng, "30 s") == [30.0] and marks(eng, "every")[:2] == [10.0, 20.0]
    assert marks(eng, "entries") == []  # default 5 entries, there was one
    assert any("Time (s) is empty or not a number — using 30" in e for e in eng.errors)


def test_empty_required_number_without_a_default_ignores_the_event(monkeypatch):
    spec = dict(P.EVENT_SPECS["distance_reaches"])
    spec["params"] = [dict(spec["params"][0], default=None)]
    monkeypatch.setitem(P.EVENT_SPECS, "distance_reaches", spec)
    eng = run([proc(WHEN("distance_reaches", [MARK("far")], distance=""), WHEN("time_reached", [MARK("t")], time=0.2))],
              0.5, lambda t: {"distance": 1000.0})
    assert marks(eng) == [0.2] and any("the event is ignored" in e for e in eng.errors)


def test_a_failing_detector_handler_or_variable_disables_only_itself(monkeypatch):
    poll = _Detector.poll

    def bad_poll(self, eng, t):
        if self.event == "speed_above":
            raise TypeError("boom")
        return poll(self, eng, t)
    monkeypatch.setattr(_Detector, "poll", bad_poll)
    pr = proc(VAR("v", "'%d%' % 5"), VAR("w", 2),
              WHEN("speed_above", [MARK("fast")], threshold=1),
              WHEN("key_down", [MARK("key")], key="k"),
              WHEN("time_reached", [MARK("t")], time=0.2))
    eng = ProcedureEngine([pr], seed=1)
    got = []
    eng.on_mark = lambda n, t: got.append(n)
    eng.start(0)
    for i in range(10):
        eng.update_state(i / 10, {"speed": 5.0})
    original = eng._fire

    def fire(h, t_occ, args):  # the key handler breaks
        if h.label == "key_down":
            raise RuntimeError("handler broke")
        return original(h, t_occ, args)
    eng._fire = fire
    eng.key(1.0, "k")
    eng.key(1.0, "k", down=False)
    eng.key(1.1, "k")
    eng.update_state(1.2, {})
    eng.stop(1.5)
    assert got == ["t"]
    assert eng.vars == {"v": 0, "w": 2}
    assert sum("internal error" in e for e in eng.errors) == 2
    assert any("Variable v: invalid operands: incomplete format" in e for e in eng.errors)


def test_check_before_test_lists_errors_only():
    procs = [proc(DO("tone", duration=-1), REPEAT([], mode="forever"), DO("run_program", program="ls"),
                  name="Bad"), proc(W(1), name="Fine")]
    assert P.check_before_test(procs) == ["'Bad', statement 1: Do play tone: Duration (s): must not be negative"]
    assert P.check_before_test([proc(W(1))]) == []


# ====================================================================== expressions
ATTACKS = ["'%d%' % 5", "'abc'['x':]", "'%9000000000000000000s' % 1", "'%400000000s' % 1", "'%.400000000f' % 1",
           "str([[0]*99999]*3000)", "'%s' % [[0]*99999]*3000", "[[0] * 99999] * 3000", "[array(60000), array(60000)]",
           "'{}' * 2 + str(array(100000))", "int('9' * 5000)", "1" + "+1" * 990, "-" * 1500 + "1", "'a' + 1",
           "[1] < 'a'", "None + 1", "sorted([1, 'a'])"]


@pytest.mark.parametrize("src", ATTACKS)
def test_expression_failures_are_expression_errors(src):
    t0 = time.monotonic()
    with pytest.raises(ExprError):
        Evaluator(lambda n: 0).eval(src)
    assert time.monotonic() - t0 < 0.5


def test_deep_nesting_inside_the_engine_is_a_procedure_error():
    deep = "1" + "+1" * 990
    eng = run([proc(SET("x", deep), MARK("after"))], 0.1)
    assert marks(eng, "after") == [0] and (eng.vars.get("x") == 991 or any("Set x" in e for e in eng.errors))


def test_interpolated_text_and_str_are_size_checked():
    eng = run([proc(VAR("a", "[[0] * 50000] * 2"), DO("log", text="{a}{a}"), MARK("m {str(a)}"))], 0.1)
    assert any("result too long" in e for e in eng.errors)


def test_shuffle_is_fast_and_bounded():
    ev = Evaluator(lambda n: 0)
    t0 = time.monotonic()
    out = ev.eval("shuffle(range(6000), 1)")
    assert sorted(out) == list(range(6000)) and time.monotonic() - t0 < 0.5
    import itertools
    for src, k in (("shuffle([1, 1, 2, 2, 3, 3] * 500, 1)", 1), ("shuffle([0] * 10 + [1] * 20, 2)", 2),
                   ("shuffle([[1], [1], [2], 'a', 'a', 1.0, 1] * 30, 2)", 2)):
        out = ev.eval(src)
        assert max(len(list(g)) for _k, g in itertools.groupby(out, key=lambda v: (type(v), repr(v)))) <= k
    with pytest.raises(ExprError):
        ev.eval("shuffle([0] * 10 + [1], 1)")


# ====================================================================== sounds
class _Stub:
    def __init__(self, log, path):
        self.log, self.path = log, path

    def poll(self):
        return None

    def stop(self):
        self.log.append(("stop", self.path))


@pytest.fixture
def stub_player(monkeypatch):
    log = []

    def player(path, volume):
        log.append(("play", path))
        return _Stub(log, path)
    monkeypatch.setattr(AudioDevice, "player", staticmethod(player))
    return log


def test_sounds_stop_on_the_device_at_test_end(stub_player):
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    eng = run([proc(DO("tone", frequency=1000, duration=100))], 0.5, devices=dm)
    assert [c for c, _ in dm.devices["spk"].played] == ["tone", "stop"]
    assert [a for a, _p in stub_player] == ["play", "stop"]
    assert eng._audio_on == {}


def test_sounds_stop_on_the_device_when_the_pre_test_ends(stub_player):
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    eng = ProcedureEngine([proc(WHEN("test_waiting", [DO("tone", frequency=500, duration=60)]))], dm)
    eng.waiting_update(0.0)
    eng.waiting_update(0.5)
    eng.start(0.0)
    assert [c for c, _ in dm.devices["spk"].played] == ["tone", "stop"]
    assert [a for a, _p in stub_player] == ["play", "stop"]
    eng.stop(0.1)


def test_a_loop_replacing_a_timed_sound_is_not_ended_by_its_timer(tmp_path, stub_player):
    import numpy as np
    import wave
    f = tmp_path / "s.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(np.zeros(8000, np.int16).tobytes())  # 1 s
    dm = DeviceManager([{"name": "spk", "type": "audio", "backend": "none"}])
    pr = proc(DO("play_sound", file=str(f), duration=1), W(0.5), DO("loop_sound", file=str(f)),
              WHEN("sound_file_end", [MARK("ended")]))
    eng = run([pr], 3, devices=dm, fps=20)
    aud = [(e["t"], e["value"]) for e in eng.io_events if e.get("type") == "audio"]
    assert aud == [(0, 1), (0.5, 1), (3.0, 0)] and marks(eng, "ended") == []


# ====================================================================== stop / disable procedure
def test_disable_procedure_stops_a_thread_inside_a_sub_procedure():
    sub = proc(W(1), DO("output_on", channel="light"), name="S", sub=True)
    a = proc({"type": "call", "procedure": "S"}, name="A")
    b = proc(W(0.5), DO("disable_procedure", procedure="A"), name="B")
    eng = run([a, sub, b], 2)
    assert not eng.outputs_state.get(("virtual", "light"))
    assert not any(e["channel"] == "light" for e in eng.io_events)


def test_stop_procedure_inside_a_sub_procedure_stops_the_caller():
    sub = proc(W(0.5), {"type": "stop", "what": "procedure"}, name="S", sub=True)
    a = proc(WHEN("test_start", [{"type": "call", "procedure": "S"}]),
             WHEN("every", [MARK("tick")], interval=0.2), name="A")
    eng = run([a, sub], 2)
    assert marks(eng, "tick") == [0.2, 0.4] and eng.proc_enabled == [False, True]


# ====================================================================== event timing
def test_every_counts_from_when_its_procedure_is_enabled():
    pellets = proc(WHEN("every", [DO("pellet", channel="pellet", count=1)], interval=1), name="Feed", enabled=False)
    starter = proc(W(5), DO("enable_procedure", procedure="Feed"))
    eng = run([starter, pellets], 7.5)
    t = [e["t"] for e in eng.io_events if e["channel"] == "pellet" and e["value"] == 1]
    assert t == [6.0, 7.0]  # no back-dated pellets at 5 s


def test_a_wait_never_moves_the_thread_clock_back():
    pr = proc(W(3), {"type": "wait", "mode": "event", "event": "time_reached", "time": 1}, W(1), MARK("m"),
              {"type": "wait", "mode": "event", "event": "zone_dwell", "zone": "A", "seconds": 0.5}, W(1), MARK("n"))
    eng = run([pr], 8, lambda t: {"zones": {"A": True}})
    assert marks(eng) == [4.0, 5.0]


def test_wait_for_every_counts_from_the_wait():
    pr = proc(W(2.5), {"type": "wait", "mode": "event", "event": "every", "interval": 1}, MARK("m"))
    assert marks(run([pr], 5, fps=20), "m") == [3.5]


# ====================================================================== the event guard
def test_a_long_loop_does_not_drop_later_events():
    pr = proc(VAR("x", 0), VAR("y", 0),
              WHEN("test_start", [REPEAT([SET("x", "i")], mode="count", count=2500, var="i"),
                                  DO("signal", name="done")]),
              WHEN("signal", [MARK("done")], name="done"),
              WHEN("variable_changed", [MARK("y")], var="y"))
    eng = run([pr], 0.5)
    assert marks(eng, "done") == [0] and not eng.errors


def test_a_long_loop_with_a_listener_still_works():
    pr = proc(VAR("x", 0), VAR("n", 0, result=True),
              WHEN("test_start", [REPEAT([SET("x", "i + 1")], mode="count", count=2500, var="i"),
                                  DO("signal", name="done")]),
              WHEN("variable_changed", [DO("increment", var="n")], var="x", mode="parallel"),
              WHEN("signal", [MARK("done")], name="done"))
    eng = run([pr], 0.5)
    assert marks(eng, "done") == [0] and eng.result_variables["n"] == 2500 and not eng.errors


# ====================================================================== indexed set
def test_filling_a_large_array_is_fast():
    pr = proc(VAR("a", "array(10000)"), REPEAT([{"type": "set", "var": "a", "index": "i", "value": "i * 2"}],
                                               mode="count", count=10000, var="i"), MARK("full"))
    t0 = time.monotonic()
    eng = run([pr], 1)
    assert time.monotonic() - t0 < 1.5
    assert eng.vars["a"] == [i * 2 for i in range(10000)] and marks(eng, "full")


def test_indexed_set_events_carry_a_copy():
    pr = proc(VAR("a", "[0, 0]"), VAR("seen", "[]"),
              WHEN("variable_changed", [DO("array_append", var="seen", value="event_value")], var="a",
                   mode="parallel"),
              {"type": "set", "var": "a", "index": 0, "value": 1}, W(0.1),
              {"type": "set", "var": "a", "index": 1, "value": 2}, W(0.1),
              {"type": "set", "var": "a", "index": 1, "value": 2})  # unchanged: no event
    eng = run([pr], 0.5)
    assert eng.vars["seen"] == [[1, 0], [1, 2]] and eng.vars["a"] == [1, 2]


# ====================================================================== encoders
def test_encoder_every_counts_from_the_first_reading_and_is_capped():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [{"name": "wheel", "kind": "encoder"}]}])
    box = dm.devices["box"]
    eng = ProcedureEngine([proc(VAR("n", 0, result=True),
                                WHEN("encoder_every", [DO("increment", var="n")], channel="wheel", count=1024,
                                     mode="parallel"))], dm)
    eng.start(0)
    box.add_counts("wheel", 3_000_000)  # the counter already ran before the test
    eng.update_state(0.04, {})
    assert eng.vars["n"] == 0
    box.add_counts("wheel", 2048)
    eng.update_state(0.08, {})
    assert eng.vars["n"] == 2
    t0 = time.monotonic()
    box.add_counts("wheel", 3_000_000)
    eng.update_state(0.12, {})
    assert eng.vars["n"] == 2 + 100 and time.monotonic() - t0 < 0.5  # capped, the rest dropped
    box.add_counts("wheel", 1024)
    eng.update_state(0.16, {})
    eng.stop(0.2)
    assert eng.result_variables["n"] == 103


# ====================================================================== dispensers, ramps
def test_dispensing_zero_does_nothing():
    eng = run([proc(DO("pellet", channel="pellet", count=0), DO("liquid_drop", channel="valve", count="0"),
                    DO("pellet", channel="pellet", count="1 - 1"))], 1)
    assert eng.pellet_counts.get(("virtual", "pellet"), 0) == 0 and eng.io_events == [] and not eng.errors


def test_all_outputs_off_stops_light_ramps():
    dm = DeviceManager([{"name": "box", "type": "virtual", "channels": [{"name": "lamp", "kind": "pwm"}]}])
    eng = run([proc(DO("light_ramp", channel="lamp", level=100, duration=10), W(1), DO("all_outputs_off"))], 3,
              devices=dm)
    assert eng.outputs_state[("box", "lamp")] == 0
    assert max(e["t"] for e in eng.io_events if e["channel"] == "lamp") == 1.0


# ====================================================================== old rules, validation
def test_old_rule_without_a_zone_is_converted_disabled():
    rule = {"trigger": "zone_enter", "zone": "", "action": "mark", "payload": "in"}
    p = P.convert_rule(rule)
    when = next(s for s in p["statements"] if s["type"] == "when")
    assert when["enabled"] is False and p["statements"][0]["type"] == "comment"
    eng = run([rule], 1, lambda t: {"zones": {"A": t > 0.5}})
    assert marks(eng) == [] and validate([rule]) == []


def test_validation_of_negative_literals():
    def errs(st):
        return [m for _pi, _p, m in validate([proc(st)])]
    assert errs(DO("set_temperature", channel="hot", target=-5)) == []
    assert errs(DO("set_temperature", channel="hot", target="-5")) == []
    assert errs(WHEN("sensor_below", [], channel="t", threshold=-20)) == []
    assert errs(DO("increment", var="x", by=-2)) == []
    assert errs(DO("tone", duration=-1)) == ["Do play tone: Duration (s): must not be negative"]
    assert errs(DO("tone", duration="-1")) == ["Do play tone: Duration (s): must not be negative"]
    assert errs(W("-1 + 3")) == []  # an expression: worked out when it runs
