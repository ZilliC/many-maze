"""The procedure engine: runs procedures during a live test as cooperative threads driven by the frames."""

from __future__ import annotations

import copy
import datetime as _dt
import heapq
import math
import random
import threading
from collections import deque
from functools import wraps

from .. import ioconfig
from ..iodevices import DeviceManager
from ..operant import Schedule
from .actions import Actions, _Break, _Stop, _Timer, _Train
from .catalog import (CONSTANTS, EPS, EVENT_SPECS, MAX_CALL_DEPTH, MAX_EVENT_CHAIN, MAX_EVENTS_PER_RUN,
                      SAFETY_TASKS, STEP_BUDGET)
from .detect import _CONDITION_EVENTS, EventWait, FrameWait, TimeWait, UntilWait, _Detector
from .expr import Evaluator, ExprError, interpolate
from .legacy import Outputs
from .live_state import LIVE_FUNCTIONS, LiveState
from .model import (_short, keep_scope, normalize_procedures, path_text, record_mode, repeat_mode, wait_alternatives,
                    wait_mode)
from .validate import _bad_var_name


# ---------------------------------------------------------------------- live-state expression functions
def _input(eng, *a):
    v = eng._input_value(*eng._resolve_in(a))
    return 0 if v is None else v


def _output(eng, *a):
    dev, ch = eng._resolve_in(a)
    if dev:
        return eng.outputs_state.get((dev, ch), 0)
    return next((v for (d, c), v in eng.outputs_state.items() if c == ch), 0)


def _pellets(eng, *a):
    if not a:
        return sum(eng.pellet_counts.values())
    dev, ch = eng._resolve_in(a)
    return sum(v for (d, c), v in eng.pellet_counts.items() if c == ch and (not dev or d == dev))


def _schedule_value(attr):
    def value(eng, name):
        s = eng.schedules.get(str(name))
        if s is None:
            return 0
        v = getattr(s, attr)
        return v if math.isfinite(v) else -1
    return value


def _number_text(v):
    """Text that is a number (animal ids, animal fields) as a number, so that it can be compared and calculated."""
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return v
        return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
    return v


def _output_volts(eng, *a):
    dev, ch = eng._resolve_in(a)
    key = (dev, ch) if dev else next((k for k in eng.outputs_state if k[1] == ch), ("", ch))
    return eng.outputs_state.get(key, 0) * eng._max_v(*key)


def _speaker(eng, device=None):
    dev = eng._devname(str(device)) if device else ""
    return int(any(not dev or k[0] == dev for k in eng._audio_on))


def _percent(axis):
    def pct(eng):
        app = eng.context.get("apparatus_map")
        v = eng._x if axis == 0 else eng._y
        if app is None or not math.isfinite(v):
            return math.nan
        try:
            b = app.arena_or_bounds().bounds()
        except ValueError:
            return math.nan
        lo, hi = b[axis], b[axis + 2]
        return (v - lo) / (hi - lo) * 100 if hi > lo else math.nan
    return pct


def _zone_distance(eng, name):
    app = eng.context.get("apparatus_map")
    z = app.zone(str(name)) if app is not None else None
    if z is None:
        raise ExprError(f"zone_distance(): no zone '{name}' in the apparatus")
    if not (math.isfinite(eng._x) and math.isfinite(eng._y)):
        return math.nan
    if bool(z.shape.contains(eng._x, eng._y)):
        return 0.0
    return float(z.shape.distance_to_edge(eng._x, eng._y)) * app.scale


def _point_distance(eng, name):
    app = eng.context.get("apparatus_map")
    pt = app.point(str(name)) if app is not None else None
    if pt is None:
        raise ExprError(f"point_distance(): no point '{name}' in the apparatus")
    return math.hypot(eng._x - pt.x, eng._y - pt.y) * app.scale


def _info(key, default=""):
    return lambda eng: _number_text(eng._test_info().get(key, default))


def _time_of_day(eng):
    d = eng.clock()
    return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6


# the FUNCTIONS (expr.py) without an implementation there: fn(engine, *args)
_ENGINE_FUNCTIONS = {
    "time": lambda eng: eng.t,
    "zone": lambda eng, z: int(bool(eng._zones.get(str(z)))),
    "head_zone": lambda eng, z: int(bool(eng._head.get(str(z)))),
    "zone_time": lambda eng, z: eng.zone_time.get(str(z), 0.0),
    "zone_entries": lambda eng, z: eng.zone_entries.get(str(z), 0),
    "detected": lambda eng: int(eng._detected),
    "freezing": lambda eng: int(eng._freezing),
    "immobile": lambda eng: int(eng._immobile),
    "speed": lambda eng: eng._speed,
    "distance": lambda eng: eng._distance,
    "x": lambda eng: eng._x,
    "y": lambda eng: eng._y,
    "input": _input, "analog": _input, "encoder": _input,
    "activations": lambda eng, *a: eng._input_count(*eng._resolve_in(a)),
    "output": _output,
    "pellets": _pellets,
    "timer": lambda eng, name: eng._timer_value(str(name)),
    "switch": lambda eng, name: eng.switches.get(str(name), 0),
    "key": lambda eng, k: int(str(k).lower() in eng.keys_down),
    "responses": _schedule_value("responses"),
    "reinforcers": _schedule_value("reinforcers"),
    "requirement": _schedule_value("requirement"),
    "test_running": lambda eng: int(eng.started and not eng.paused and not eng.stopped),
    "test_paused": lambda eng: int(eng.paused),
    "stage": lambda eng: str(eng._test_info().get("stage", "")),
    "trial": _info("trial", 0), "apparatus": lambda eng: str(eng._test_info().get("apparatus", "")),
    "treatment": lambda eng: str(eng._test_info().get("treatment", "")), "animal": _info("animal"),
    "animal_field": lambda eng, n: _number_text((eng._test_info().get("fields") or {}).get(str(n), "")),
    "date": lambda eng: eng.clock().strftime("%Y-%m-%d"), "time_of_day": _time_of_day,
    "freezing_time": lambda eng: eng.freeze_time, "immobile_time": lambda eng: eng.immobile_time,
    "zone_distance": _zone_distance, "point_distance": _point_distance,
    "head_x": lambda eng: eng._hx, "head_y": lambda eng: eng._hy, "tail_x": lambda eng: eng._tx,
    "tail_y": lambda eng: eng._ty, "x_percent": _percent(0), "y_percent": _percent(1),
    "sequence_duration": lambda eng, name: eng.sequence_durations.get(str(name), 0.0),
    "speaker": _speaker, "output_volts": _output_volts,
}
_ENGINE_FUNCTIONS.update({name: v[0] for name, v in LIVE_FUNCTIONS.items()})


class _Goto(Exception):
    """A Go to statement: caught by the block (or an enclosing block) that has the label."""

    def __init__(self, label):
        super().__init__(label)
        self.label = label


class _Return(Exception):
    """Stop "return from the sub-procedure"."""


def merge_kept_variables(store: dict, kept: dict):
    """Merge the kept variables of a test (ProcedureEngine.kept_variables) into the store (Project.variables):
    experiment-wide values by name, per-animal / per-apparatus values under "@animal" / "@apparatus" ->
    {animal or apparatus: {name: value}}."""
    for name, v in kept.items():
        if name.startswith("@") and isinstance(v, dict):
            dst = store.get(name)
            if not isinstance(dst, dict):
                dst = store[name] = {}
            for who, values in v.items():
                dst.setdefault(who, {}).update(copy.deepcopy(values))
        else:
            store[name] = copy.deepcopy(v)


# ---------------------------------------------------------------------- engine
class _Handler:
    def __init__(self, proc_i, path, st, det, label):
        self.proc_i, self.path, self.st, self.det, self.label = proc_i, path, st, det, label
        self.mode = st.get("mode", "ignore")
        self.once = bool(st.get("once"))
        self.threads: list = []
        self.count = 0
        self.broken = False  # an internal error: the handler no longer runs (the others do)


class _Thread:
    """A running block. ``owner`` is the procedure it belongs to (Stop / Disable procedure); ``proc_i`` is the
    procedure whose statements it is running (a sub-procedure while inside a call: its maths and error
    messages)."""

    def __init__(self, proc_i, handler, clock, locals_, label, owner=None):
        self.proc_i, self.handler, self.clock, self.locals, self.label = proc_i, handler, clock, locals_, label
        self.owner = proc_i if owner is None else owner
        self.alive = True
        self.wait = None
        self.steps = 0  # statements run since the thread last yielded (the step budget)
        self.ran = 0  # statements run in all
        self.waits = 0  # waits that suspended the thread
        self.gen = None
        self.path = ()
        self.resume_clock = clock
        self.depth = 0  # sub-procedures called


def _locked(fn):
    @wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class ProcedureEngine(Actions, LiveState):
    """Runs procedures during a live test. Call start(t), update_state(t, state) every frame, stop(t).

    The public methods are thread-safe (e.g. frames processed in a worker thread, keys and touches from the GUI);
    callbacks run in the thread that called the engine.

    Pausing (:meth:`pause`) runs the "test paused" handlers at once, stops pulse trains, switches shock outputs
    off and, with ``outputs_off_on_pause`` (default), every output and sound. While paused call
    :meth:`paused_tick` with the real time since the pause so that safety tasks still run.
    Variables declared "keep" are copied to ``variables`` at :meth:`stop` unless ``commit_kept`` is False; they
    are always available in ``kept_variables`` (e.g. to be stored only when the test is saved); per-animal and
    per-apparatus ones under "@animal" / "@apparatus" (see :func:`merge_kept_variables`), for the animal and
    apparatus of ``context["test"]``.

    "Run a program" starts only the programs this computer allows (``program_policy``, default
    :data:`programs.policy`: the per-user allow-list, and a confirmation callback the GUI may set).

    Before the test starts: call :meth:`waiting_update` on every frame while the test waits to start; the "test is
    waiting to start" handlers run (from the first call) and may Prevent / Allow the test start
    (:attr:`start_allowed`). At :meth:`start` they stop (pulse trains stop, shocks and sounds go off); the values of
    the variables and the outputs' states carry over into the test.

    context: {"zones", "points", "keys", "test": {test, animal, apparatus, stage, trial, treatment, fields},
    "apparatus_map": an apparatus.Apparatus (zone / point distances, position in %, zone sequences)}."""

    def __init__(self, procedures=None, devices=None, on_mark=None, on_end=None, on_log=None, variables=None,
                 context=None, *, on_pause=None, on_resume=None, on_stimulus=None, seed=None,
                 outputs_off_at_end: bool = True, outputs_off_on_pause: bool = True, commit_kept: bool = True,
                 program_policy=None):
        if isinstance(devices, Outputs):
            self.outputs, devices = devices, None
        else:
            self.outputs = Outputs()
        self.devices = devices if devices is not None else DeviceManager([], open=False)
        self.procedures = normalize_procedures(procedures or [])
        self.on_mark, self.on_end, self.on_log = on_mark, on_end, on_log
        self.on_pause, self.on_resume, self.on_stimulus = on_pause, on_resume, on_stimulus
        self.variables = variables if variables is not None else {}
        self.context = context or {}
        self.rng = random.Random(seed)
        self.outputs_off_at_end = outputs_off_at_end
        self.outputs_off_on_pause = outputs_off_on_pause
        self.commit_kept = commit_kept
        self.clock = _dt.datetime.now  # date() and time_of_day()
        # "Run a program": which programs may run on this computer (None: programs.policy, the per-user allow-list)
        self.program_policy = program_policy
        self._lock = threading.RLock()
        self._pretest = False
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self):
        self.started = self.stopped = self.ended = self.paused = False
        self.start_blocked = False  # Prevent / Allow test start (before the test starts)
        self.t = 0.0
        self.vars: dict = {}
        self.var_flags: dict[str, dict] = {}
        self.errors: list[str] = []
        self._error_keys: set = set()
        self.io_events: list[dict] = []
        self.result_variables: dict = {}
        self.kept_variables: dict = {}
        self.marks: list[dict] = []
        self.state_events: list[dict] = []
        self.pauses: list[list] = []
        self.log_lines: list[tuple[float, str]] = []
        self.fired: list = []
        self.handlers: list[_Handler] = []
        self.threads: list[_Thread] = []
        self.proc_enabled = [bool(p.get("enabled", True)) for p in self.procedures]
        self._started_procs: set[int] = set()
        self._queue: deque = deque()
        self._depth = 0  # how many events led to the one being dispatched (signal / variable loops)
        self._tasks: list = []
        self._task_seq = 0
        self._task_keys: dict = {}
        self._cancelled: set = set()
        self._trains: dict[tuple, _Train] = {}
        self._update_no = 0
        self._busy = False
        self._pending_stop = None
        self._have_t = False
        # live state
        self._zones: dict[str, bool] = {}
        self._head: dict[str, bool] = {}
        self.zone_time: dict[str, float] = {}
        self.zone_entries: dict[str, int] = {}
        self.zone_since: dict[str, float] = {}
        self._entered_now: list[str] = []
        self._detected = True
        self._freezing = self._immobile = False
        self.freeze_time = self.immobile_time = 0.0
        self._x = self._y = math.nan
        self._hx = self._hy = self._tx = self._ty = math.nan
        self._seq_progress: dict[str, tuple[int, float]] = {}
        self.sequence_durations: dict[str, float] = {}
        self.timer_resolution_ms = 1.0  # "Set timer resolution": accepted, see _exec
        self._speed = 0.0
        self._distance = 0.0
        self._last_xy = None
        self.inputs: dict[tuple, float] = {}
        self.input_counts: dict[tuple, int] = {}
        self.outputs_state: dict[tuple, float] = {}
        self.pellet_counts: dict[tuple, int] = {}
        self.keys_down: set[str] = set()
        self.switches: dict[str, int] = {}
        self.timers: dict[str, _Timer] = {}
        self.schedules: dict[str, Schedule] = {}
        self.stimuli: dict[str, dict] = {}
        self._audio_on: dict[tuple, float] = {}
        self._audio_devs: set[str] = set()  # audio devices a sound was played on (stopped at the end)
        self._shock_keys: set[tuple] = set()
        self._pause_t: float | None = None
        self._ramps: dict = {}
        self._pellet_checks: dict = {}
        self._odours: dict[tuple, str] = {}
        self.intensities: dict[tuple, float] = {}
        self._pumps_on: dict[tuple, str] = {}
        self._thermostats_on: set[tuple] = set()
        self.animal_weights: list[tuple[float, float]] = []
        self.alerts: list[tuple[float, str]] = []
        self._alert_times: dict = {}
        self._sensor_alarm: dict[tuple, bool] = {}
        self._chan_cfg: dict[tuple, dict] = {}
        self._last_sample_t: dict[tuple, float] = {}
        self._reset_extra()

    def _start_procs(self, pis, t, defer=False, pretest=False):
        """Start procedures: declare their variables (all of them first), create their handlers, then start their
        top-level plain statements. defer: started by another thread's action, so the new threads run from the frame
        loop instead of nested inside that thread. pretest: before the test starts, only the "test is waiting to
        start" handlers. Sub-procedures only declare their variables (they run when called)."""
        if not pretest:
            self._started_procs.update(pis)
        for pi in pis:
            for i, st in enumerate(self.procedures[pi].get("statements") or []):
                if isinstance(st, dict) and st.get("type") == "var" and st.get("enabled", True) is not False:
                    self._declare(pi, (i,), st)
        pis = [pi for pi in pis if not self.procedures[pi].get("sub")]
        for pi in pis:
            for i, st in enumerate(self.procedures[pi].get("statements") or []):
                if isinstance(st, dict) and st.get("type") == "when" and st.get("enabled", True) is not False \
                        and (st.get("event") == "test_waiting") == pretest:
                    th = _Thread(pi, None, t, {}, "when")
                    try:
                        det = _Detector(self, st, th, (i,), initial=True)
                    except Exception as e:  # defensive: only this handler is left out
                        self._error(th, (i,), f"When: internal error: {e!r}")
                        continue
                    self.handlers.append(_Handler(pi, (i,), st, det, st.get("event", "?")))
        for pi in [] if pretest else pis:
            stmts = self.procedures[pi].get("statements") or []
            if any(isinstance(s, dict) and s.get("type") not in ("when", "var", "comment")
                   and s.get("enabled", True) is not False for s in stmts):
                th = _Thread(pi, None, t, {}, "start")
                th.gen = self._thread_main(th, stmts, ())
                self.threads.append(th)
                if defer:
                    th.wait = TimeWait(t)
                else:
                    self._resume(th)

    def _emit(self, name, args, t):
        """Queue an occurrence of an event (dispatched to the handlers and waiting threads by _run). Its depth is
        one more than the event being dispatched (a handler signalling another …): see _run."""
        self._queue.append(("event", name, args, t, self._depth + 1))

    def _listened(self, name, args) -> bool:
        """Whether a handler or a waiting thread would see this occurrence of a generic event."""
        if any(h.det.generic and not h.broken and self.proc_enabled[h.proc_i] and h.det.matches(name, args)
               for h in self.handlers):
            return True
        return any(d.generic and d.matches(name, args) for th in self.threads
                   if th.alive and isinstance(th.wait, EventWait) for d in th.wait.dets)

    # ------------------------------------------------------------------ public API
    def has_pretest(self) -> bool:
        """Whether an enabled procedure has a "test is waiting to start" handler."""
        return any(isinstance(st, dict) and st.get("type") == "when" and st.get("event") == "test_waiting"
                   and st.get("enabled", True) is not False
                   for pi, p in enumerate(self.procedures) if self.proc_enabled[pi] and not p.get("sub")
                   for st in p.get("statements") or [])

    @property
    def start_allowed(self) -> bool:
        """False while a "test is waiting to start" handler prevents the test from starting."""
        return not self.start_blocked

    @_locked
    def waiting_update(self, t: float, state: dict | None = None):
        """Call once per frame while the test is waiting to start (t: seconds since it started waiting; state as
        for update_state). The first call runs the "test is waiting to start" handlers (without any, it does
        nothing)."""
        if self.started or self.stopped:
            return
        if self._pretest:
            self._frame(t, state)
            return
        if not self.has_pretest():
            return
        self._reset()
        self._pretest = True
        self.t = t
        self._have_t = True
        self._busy = True
        try:
            self._start_procs([pi for pi in range(len(self.procedures)) if self.proc_enabled[pi]], t, pretest=True)
            self._emit("test_waiting", {}, t)
            self._run(t)
        finally:
            self._busy = False
        self._after()

    def _end_pretest(self, t) -> dict:
        """The test starts: the pre-test threads stop, pulse trains stop, shocks and sounds go off; returns what
        carries over into the test (variables, output, input and switch states, errors)."""
        for th in self.threads:
            self._kill(th)
        self.threads = []
        for key in list(self._trains):
            self._stop_train(key, t)
        for key in list(self._shock_keys):
            if self.outputs_state.get(key):
                self._set_out(key[0], key[1], 0, t, "shock")
        self._stop_audio_devices(t)
        for key in list(self._audio_on):
            self._audio_off(key, t)
        self._pretest = False
        return {k: getattr(self, k) for k in ("vars", "var_flags", "outputs_state", "switches", "inputs", "keys_down",
                                               "intensities", "_odours", "_pumps_on", "_thermostats_on", "errors",
                                               "_error_keys", "timer_resolution_ms")}

    @_locked
    def start(self, t: float = 0.0):
        if self.started:
            return
        carry = self._end_pretest(t) if self._pretest else None
        self._reset()
        if carry:
            self.__dict__.update(carry)
            for (dev, ch), v in self.outputs_state.items():  # outputs left on before the start, in the I/O log
                if v:
                    self._log_io(t, dev, ch, "output", v, "digital" if v in (0, 1) else "pwm")
        self.started = True
        self.t = t
        self._have_t = True
        self._busy = True
        try:
            self._start_procs([pi for pi in range(len(self.procedures)) if self.proc_enabled[pi]], t)
            self._emit("test_start", {}, t)
            self._run(t)
        finally:
            self._busy = False
        self._after()

    @_locked
    def update_state(self, t: float, state: dict | None = None):
        """Call once per frame. state: {"zones": {name: bool}, "head_zones": {...}, "detected", "freezing",
        "immobile", "x", "y", "speed", "distance"} (all optional)."""
        if not self.started:
            self.start(t)
        if self.stopped:
            return
        self._frame(t, state)

    def _frame(self, t, state):
        self._busy = True
        try:
            dt = max(0.0, t - self.t) if self._have_t else 0.0
            self.t = max(self.t, t)
            self._have_t = True
            self._update_no += 1
            self._observe(t, dt, state or {})
            self._poll_inputs(t)
            self._tick(t)
            self._poll_detectors(t)
            self._run(t)
        finally:
            self._busy = False
        self._after()

    def update(self, t: float, zones: dict, detected: bool = True, freezing: bool = False, immobile: bool = False):
        """Legacy API (rules engine): zone occupancy name -> bool."""
        self.update_state(t, {"zones": zones, "detected": detected, "freezing": freezing, "immobile": immobile})

    @_locked
    def key(self, t: float, key: str, down: bool = True):
        k = str(key)
        if down:
            if k.lower() in self.keys_down:
                return
            self.keys_down.add(k.lower())
        else:
            self.keys_down.discard(k.lower())
        self._external(t, "key_down" if down else "key_up", {"key": k})

    @_locked
    def touch(self, t: float, area: str | None, x: float | None = None, y: float | None = None):
        """A touch on the stimulus screen inside `area` (None/"" = outside all areas)."""
        if area:
            self._log_io(t, "screen", f"touch {area}", "input", 1, "digital")
            self._log_io(t, "screen", f"touch {area}", "input", 0, "digital")
            self._external(t, "touch", {"area": str(area), "value": (x, y)})
        else:
            self._external(t, "touch_outside", {"value": (x, y)})

    @_locked
    def mark_event(self, t: float, name: str):
        """A manually scored event (fires "event marked")."""
        self._external(t, "event_marked", {"name": str(name)})

    @_locked
    def set_input(self, device: str, channel: str, value: float):
        """Simulate an input (virtual devices); picked up on the next frame."""
        self.devices.set_input(device, channel, value)

    @_locked
    def pause(self, t: float):
        """Pause the test now: safety outputs off and the "test paused" handlers run at once."""
        self._pause(t)

    @_locked
    def resume(self, t: float):
        self._resume_test(t)

    @_locked
    def paused_tick(self, wall_s: float):
        """While paused (the test clock is stopped): run the safety tasks (shock cut-offs, end of sounds) that are
        due `wall_s` seconds of real time after the pause, logged at the pause time, and read the inputs (changes
        during a pause update the input states without firing events). Call it regularly while paused."""
        if not self.paused or not self.started or self.stopped or self._busy:
            return
        t = self._pause_t if self._pause_t is not None else self.t
        self._busy = True
        try:
            self._poll_inputs(t, quiet=True)
            limit = t + max(0.0, float(wall_s))
            due = [x for x in self._tasks if x[3] is not None and isinstance(x[3], tuple) and x[3]
                   and x[3][0] in SAFETY_TASKS and x[0] <= limit + EPS]
            if due:
                self._tasks = [x for x in self._tasks if x not in due]
                heapq.heapify(self._tasks)
                for _due, seq, fn, key in sorted(due, key=lambda x: (x[0], x[1])):
                    if seq in self._cancelled:
                        self._cancelled.discard(seq)
                        continue
                    if self._task_keys.get(key) == seq:
                        del self._task_keys[key]
                    fn(t)
        finally:
            self._busy = False

    @_locked
    def stop(self, t: float):
        if self.stopped:
            return
        if not self.started and not self._pretest:
            self.stopped = True
            return
        if self._busy:
            self._pending_stop = t if self._pending_stop is None else max(self._pending_stop, t)
            return
        self._busy = True
        try:
            if not self.started:  # ended while waiting to start: the pre-test procedures' outputs go off
                self._pretest = False
                t = max(self.t, t)
                for key in list(self._shock_keys):
                    if self.outputs_state.get(key):
                        self._safely(f"output {key[0]}/{key[1]}", self._set_out, key[0], key[1], 0, t, "shock")
                self._cleanup(t)
                return
            try:
                self.t = max(self.t, t)
                t = self.t
                self._tick(t)
                self._emit("test_end", {}, t)
                self._run(t, final=True)
            finally:
                try:
                    self._cleanup(t)
                finally:
                    self._finalise(t)
        finally:
            self._busy = False
            self.stopped = True

    def _cleanup(self, t):
        """Threads, pulse trains and timers stopped; outputs, sounds, switches and devices off (end of the test)."""
        # every cleanup step on its own: one failing device must not leave the others on
        for th in self.threads:
            self._safely("stop", self._kill, th)
        self.threads = []
        for key in list(self._trains):
            self._safely(f"output {key[0]}/{key[1]}", self._stop_train, key, t)
        self._tasks, self._task_keys, self._cancelled = [], {}, set()
        self._ramps.clear()
        if self.outputs_off_at_end:
            self._devices_off(t)
            for (dev, ch), v in list(self.outputs_state.items()):
                if v:
                    self._safely(f"output {dev}/{ch}", self._set_out, dev, ch, 0, t, "digital")
            self._stop_audio_devices(t)
            for key in list(self._audio_on):
                self._safely("audio", self._audio_off, key, t)
            for name, v in list(self.switches.items()):
                if v:
                    self._safely(f"switch {name}", self._set_switch, name, 0, t)

    def _stop_audio_devices(self, t):
        """The sounds stop on every audio device the procedures played on (looping and long sounds included)."""
        for dev in sorted(self._audio_devs | {k[0] for k in self._audio_on}):
            if self.devices.has(dev):
                self._safely(f"audio {dev}", self.devices.audio, dev, "stop")

    def _safely(self, what, fn, *args):
        """A cleanup step at the end of the test: an error is reported and the next step still runs."""
        try:
            fn(*args)
        except Exception as e:
            self._error(None, (), f"{what}: {e}")

    def _finalise(self, t):
        """Close open state events and pauses; store the kept and result variables (end of the test)."""
        for m in self.state_events:
            if m["t_end"] is None:
                m["t_end"] = t
        for pz in self.pauses:
            if pz[1] is None:
                pz[1] = t
        for name, flags in self.var_flags.items():
            v = self.vars.get(name)
            if flags.get("keep"):
                self._kept_store(self.kept_variables, flags["keep"], create=True)[name] = copy.deepcopy(v)
                if self.commit_kept:
                    self._kept_store(self.variables, flags["keep"], create=True)[name] = copy.deepcopy(v)
            if flags.get("result") and isinstance(v, (int, float)) and not isinstance(v, str):
                self.result_variables[name] = float(v) if isinstance(v, float) else int(v)
        self.io_events.sort(key=lambda e: e["t"])

    def _devices_off(self, t):
        """Pumps stopped, temperature control off and odours off (end of the test)."""
        for dev, ch in list(self._pumps_on):
            self._safely(f"pump {dev}/{ch}", self._a_pump_stop, None, (), dev, ch)
        for dev, ch in list(self._thermostats_on):
            self._safely(f"temperature {dev}/{ch}", self._a_temperature_off, None, (), dev, ch)
        for (dev, ch), name in list(self._odours.items()):
            if name:
                self._safely(f"odour {dev}/{ch}", self._a_odour_off, None, (), dev, ch)

    # ------------------------------------------------------------------ internals: errors & helpers
    def _proc_name(self, pi):
        if pi is None or not 0 <= pi < len(self.procedures):
            return "?"
        return self.procedures[pi].get("name", f"Procedure {pi + 1}")

    def _error(self, th, path, msg):
        pi = th.proc_i if th is not None else None
        key = (pi, tuple(path or ()), msg)
        if key in self._error_keys:
            return
        self._error_keys.add(key)
        where = f"'{self._proc_name(pi)}'" + (f", statement {path_text(path)}" if path else "")
        if pi is None and not path:
            where = "I/O" if msg.startswith(("I/O", "output")) or ":" in msg else "procedures"
        text = f"{self.t:.2f} s — {where}: {msg}"
        self.errors.append(text)
        if self.on_log:
            try:
                self.on_log(f"Error: {where}: {msg}", self.t)
            except Exception:  # pragma: no cover
                pass

    def _lookup(self, th):
        def look(name):
            if th is not None and name in th.locals:
                return th.locals[name]
            if name in self.vars:
                return self.vars[name]
            if name in CONSTANTS:
                return CONSTANTS[name]
            raise ExprError(f"unknown variable '{name}'")
        return look

    def _eval(self, th, src):
        procs = self.procedures
        anymaze = th is not None and th.proc_i is not None and 0 <= th.proc_i < len(procs) \
            and bool(procs[th.proc_i].get("anymaze_maths"))
        return Evaluator(self._lookup(th), self._call, self.rng, anymaze).eval(src)

    def _num(self, th, src, path, what="", default=0.0):
        if src is None or (isinstance(src, str) and not src.strip()):
            return default
        if isinstance(src, (int, float)) and not isinstance(src, bool):
            return src
        try:
            v = self._eval(th, src)
            if isinstance(v, bool):
                v = int(v)
            if not isinstance(v, (int, float)):
                raise ExprError(f"expected a number, got {_short(v)!r}")
            return v
        except ExprError as e:
            self._error(th, path, f"{what + ': ' if what else ''}{e}")
            return default

    def _truth(self, th, src, path, quiet=False):
        try:
            return bool(self._eval(th, src))
        except ExprError as e:
            if not quiet:
                self._error(th, path, f"condition: {e}")
            return False

    def _text(self, th, src, path):
        try:
            return interpolate(src, lambda e: self._eval(th, e))
        except ExprError as e:
            self._error(th, path, f"text: {e}")
            return str(src)

    # ------------------------------------------------------------------ expression functions
    def _resolve_in(self, args):
        if len(args) == 2:
            return str(args[0] or ""), str(args[1])
        return "", str(args[0])

    def _input_key(self, dev, ch):
        if dev:
            return (self._devname(dev), ch)
        for k in self.inputs:
            if k[1] == ch:
                return k
        d = self.devices.find_channel(ch)
        return (d or "", ch)

    def _input_value(self, dev, ch):
        if not ch:
            return None
        return self.inputs.get(self._input_key(dev or "", ch))

    def _input_count(self, dev, ch):
        return self.input_counts.get(self._input_key(dev or "", ch or ""), 0)

    def _test_info(self) -> dict:
        """The test the procedures run in: the context itself ({"test": id, "animal", "trial", …}, as
        procedures.test_context gives it), or an older {"test": {…}} context."""
        if not isinstance(self.context, dict):
            return {}
        info = self.context.get("test")
        return info if isinstance(info, dict) else self.context

    def _max_v(self, dev, ch) -> float:
        """The voltage of an analogue output at level 1 (its max_v option, default 5 V)."""
        try:
            v = float(self._cfg(dev, ch).get("max_v") or 5.0) if dev else 5.0
        except (TypeError, ValueError):
            v = 5.0
        return v if v > 0 else 5.0

    def _call(self, name, args):
        fn = _ENGINE_FUNCTIONS.get(name)
        if fn is None:
            raise ExprError(f"unknown function '{name}'")
        return fn(self, *args)

    # ------------------------------------------------------------------ variables
    def _declare(self, pi, path, st):
        name = str(st.get("name") or "")
        if _bad_var_name(name):
            self._error(_Thread(pi, None, self.t, {}, ""), path, f"Variable: {_bad_var_name(name)}")
            return
        if name in self.var_flags:
            return
        scope = keep_scope(st)
        self.var_flags[name] = {"keep": scope, "result": bool(st.get("result")), "record": record_mode(st)}
        store = self._kept_store(self.variables, scope) if scope else None
        if store is not None and name in store:
            self.vars[name] = copy.deepcopy(store[name])
            return
        th = _Thread(pi, None, self.t, {}, "")
        try:
            self.vars[name] = self._eval(th, st.get("value", 0))
        except ExprError as e:
            self._error(th, path, f"Variable {name}: {e}")
            self.vars[name] = 0
        except Exception as e:  # defensive: the variable starts at 0, the procedures still start
            self._error(th, path, f"Variable {name}: internal error: {e!r}")
            self.vars[name] = 0

    def _kept_store(self, store, scope, create=False):
        """The kept values of a scope in a store (Project.variables or kept_variables): the store itself for the
        whole experiment, store["@animal"][animal] / store["@apparatus"][apparatus] per animal / apparatus."""
        if scope == "experiment":
            return store
        who = str(self._test_info().get(scope, ""))
        group = store.get("@" + scope)
        if not isinstance(group, dict):
            if not create:
                return None
            group = store["@" + scope] = {}
        if who not in group and create:
            group[who] = {}
        return group.get(who)

    def _setvar(self, th, name, value, path=()):
        name = str(name or "")
        bad = _bad_var_name(name)
        if bad:
            raise ExprError(bad)
        if isinstance(value, (list, tuple)):
            value = copy.deepcopy(list(value))
        elif not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ExprError("unsupported value")
        if th is not None and name in th.locals:
            th.locals[name] = value
            return
        old = self.vars.get(name, None)
        self.vars[name] = value
        changed = old != value or type(old) is not type(value)
        rec = self.var_flags.get(name, {}).get("record")
        if (rec == "set" or (rec == "changes" and changed)) and isinstance(value, (int, float)):
            # a time-stamped history of the variable, analysed into mean / max / min / sum / count / list measures
            self._log_io(self.t, "procedure", name, "variable", float(value) if isinstance(value, float)
                         else int(value), "variable")
        if changed:
            self._var_changed(name, value)

    def _var_changed(self, name, value):
        """Fire "variable changes" — only when a handler or a waiting thread listens for it (a loop setting a
        variable thousands of times must not fill the event queue). Lists are passed as a copy: an indexed set
        changes the variable's own list in place."""
        args = {"var": name, "value": value}
        if self._listened("variable_changed", args):
            if isinstance(value, list):
                args["value"] = copy.deepcopy(value)
            self._emit("variable_changed", args, self.t)

    # ------------------------------------------------------------------ observation
    def _observe(self, t, dt, st):
        self._entered_now = []
        for name, inside in self._zones.items():
            if inside:
                self.zone_time[name] = self.zone_time.get(name, 0.0) + dt
        if self._freezing:
            self.freeze_time += dt
        if self._immobile:
            self.immobile_time += dt
        zones = st.get("zones")
        if zones:
            for name, inside in zones.items():
                inside = bool(inside)
                was = self._zones.get(name)
                if inside and not was:
                    self.zone_entries[name] = self.zone_entries.get(name, 0) + 1
                    self.zone_since[name] = t
                    self._entered_now.append(name)
                    self._emit("zone_enter", {"zone": name}, t)
                elif was and not inside:
                    self.zone_since.pop(name, None)
                    self._emit("zone_exit", {"zone": name}, t)
                self._zones[name] = inside
        head = st.get("head_zones")
        if head:
            for name, inside in head.items():
                inside = bool(inside)
                was = self._head.get(name)
                if inside and not was:
                    self._emit("head_zone_enter", {"zone": name}, t)
                elif was and not inside:
                    self._emit("head_zone_exit", {"zone": name}, t)
                self._head[name] = inside
        for key, attr in (("freezing", "_freezing"), ("immobile", "_immobile")):
            if key in st:
                v = bool(st[key])
                if v != getattr(self, attr):
                    self._emit(f"{key}_{'start' if v else 'end'}", {}, t)
                setattr(self, attr, v)
        if "detected" in st:
            v = bool(st["detected"])
            if v != self._detected:
                self._emit("animal_found" if v else "animal_lost", {}, t)
            self._detected = v
        x, y = st.get("x"), st.get("y")
        if x is not None and y is not None:
            self._x, self._y = float(x), float(y)
        for key in ("hx", "hy", "tx", "ty"):
            if st.get(key) is not None:
                setattr(self, "_" + key, float(st[key]))
        if self._entered_now:
            self._track_sequences(t)
        prev_d = self._distance
        if st.get("distance") is not None:
            self._distance = float(st["distance"])
        elif x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y)):
            if self._last_xy is not None:
                self._distance += math.hypot(float(x) - self._last_xy[0], float(y) - self._last_xy[1])
            self._last_xy = (float(x), float(y))
        if st.get("speed") is not None:
            self._speed = float(st["speed"])
        elif dt > 0:
            self._speed = (self._distance - prev_d) / dt
        self._observe_extra(t, dt, st)

    def _track_sequences(self, t):
        """The apparatus's zone sequences, for sequence_duration(): entering a sequence's steps in order completes a
        run (entering the first step again restarts it, another of its steps breaks it, other zones are ignored)."""
        app = self.context.get("apparatus_map")
        for q in getattr(app, "sequences", None) or []:
            steps = list(q.steps)
            if len(steps) < 1:
                continue
            idx, t0 = self._seq_progress.get(q.name, (0, t))
            for z in self._entered_now:
                if idx < len(steps) and z == steps[idx]:
                    idx, t0 = idx + 1, t if idx == 0 else t0
                elif z == steps[0]:
                    idx, t0 = 1, t
                elif z in steps:
                    idx = 0
                if idx == len(steps):
                    self.sequence_durations[q.name] = t - t0
                    idx = 0
            self._seq_progress[q.name] = (idx, t0)

    def _devname(self, name):
        """The device a name used by the procedures refers to (a per-test DeviceView maps box names to its box)."""
        fn = getattr(self.devices, "resolve_name", None)
        return fn(name) if fn is not None and name else name

    def _poll_inputs(self, t, quiet=False):
        try:
            if hasattr(self.devices, "read_inputs_ex"):
                changes = self.devices.read_inputs_ex()
            else:
                changes = [tuple(c) + (None,) for c in self.devices.read_inputs()]
        except Exception as e:  # pragma: no cover - hardware dependent
            self._error(None, (), f"I/O: {e}")
            return
        # samples timed by the board (fast analogue inputs): placed before the frame by their board time, the
        # newest sample of the poll at the frame time
        newest: dict = {}
        for dev, ch, kind, _v, ms in changes:
            if ms is not None and kind in ioconfig.VALUE_KINDS:
                newest[(dev, ch)] = max(ms, newest.get((dev, ch), ms))
        for dev, ch, kind, value, ms in changes:
            if kind == "watchdog":
                self._watchdog_fired(t, dev)
            elif quiet:  # paused: keep the input states, no events
                self.inputs[(dev, ch)] = value if kind in ioconfig.VALUE_KINDS else (1 if value else 0)
            else:
                ts = t
                if ms is not None and (dev, ch) in newest:
                    ts = max(self._last_sample_t.get((dev, ch), -math.inf), t - (newest[(dev, ch)] - ms) / 1000.0)
                    self._last_sample_t[(dev, ch)] = ts
                self._input_changed(ts, dev, ch, kind, value, ms)
        for e in self.devices.errors:
            self._error(None, (), e)

    def _watchdog_fired(self, t, dev):
        """The board's watchdog switched all its outputs off: record it (no command is sent to the board)."""
        for key in [k for k in self._trains if k[0] == dev]:
            tr = self._trains.pop(key)
            if tr.on:
                self._set_out(dev, key[1], 0, t, tr.typ, hw=True, reason="watchdog")
        for (d, ch), v in list(self.outputs_state.items()):
            if d == dev and v:
                self._cancel_task(("shock", d, ch))
                self._set_out(d, ch, 0, t, "digital", hw=True, reason="watchdog")
        self._log_line(t, f"{dev}: the watchdog switched all outputs off")

    def _input_changed(self, t, dev, ch, kind, value, ms=None):
        key = (dev, ch)
        old = self.inputs.get(key)
        extra = {"board_ms": ms} if ms is not None else {}
        if kind in ioconfig.VALUE_KINDS:
            self.inputs[key] = value
            self._log_io(t, dev, ch, "input", value, kind, **extra)
            if old is None or value != old:
                self._emit("input_changed", {"device": dev, "channel": ch, "value": value}, t)
            if kind == "sensor":
                self._sensor_range(t, dev, ch, value)
            return
        v = 1 if value else 0
        if old is not None and v == (1 if old else 0):
            return
        if old is None and v == 0:
            self.inputs[key] = 0
            return
        self.inputs[key] = v
        typ = {"pir": "pir", "status": "status"}.get(kind, "digital")
        self._log_io(t, dev, ch, "input", v, typ, **extra)
        args = {"device": dev, "channel": ch, "value": v}
        self._emit("input_changed", args, t)
        self._emit("input_on" if v else "input_off", args, t)
        if v:
            self.input_counts[key] = self.input_counts.get(key, 0) + 1
            if self._pellet_checks:
                self._pellet_seen(key, t)
        if kind == "pir":
            self._emit("movement_start" if v else "movement_end", args, t)
        elif kind == "status" and v and "." in ch:
            base, suffix = ch.rsplit(".", 1)
            ev = ioconfig.STATUS_EVENTS.get(suffix)
            if ev:
                self._emit(ev, {"device": dev, "channel": base, "value": v}, t)

    def _cfg(self, dev, ch) -> dict:
        key = (dev, ch)
        if key not in self._chan_cfg:
            try:
                self._chan_cfg[key] = self.devices.channel_config(dev, ch)
            except Exception:  # pragma: no cover - devices without configurations
                self._chan_cfg[key] = {}
        return self._chan_cfg[key]

    def _sensor_range(self, t, dev, ch, value):
        """A sensor leaving / returning to its alert range (alert_min / alert_max options)."""
        c = self._cfg(dev, ch)
        lo, hi = c.get("alert_min"), c.get("alert_max")
        if lo in (None, "") and hi in (None, ""):
            return
        try:
            out = (lo not in (None, "") and value < float(lo)) or (hi not in (None, "") and value > float(hi))
        except (TypeError, ValueError):
            return
        key = (dev, ch)
        if out == self._sensor_alarm.get(key, False):
            return
        self._sensor_alarm[key] = out
        args = {"device": dev, "channel": ch, "value": value}
        self._log_io(t, dev, f"{ch}.out_of_range", "input", 1 if out else 0, "status")
        self._emit("sensor_out_of_range" if out else "sensor_in_range", args, t)
        if out and c.get("alert", True) not in (False, 0, "false", "no"):
            units = c.get("units") or ioconfig.SENSOR_TYPES.get(c.get("sensor", "generic"), "")
            rng = f"{lo if lo not in (None, '') else '-∞'} – {hi if hi not in (None, '') else '∞'}"
            self._alert(t, f"{dev}/{ch} = {value:g} {units} is outside its range {rng} {units}".replace("  ", " "),
                        key=("sensor",) + key, repeat_s=float(c.get("alert_repeat_s", 600)))

    def _log_io(self, t, dev, ch, kind, value, typ=None, **extra):
        e = {"t": round(float(t), 4), "device": str(dev), "channel": str(ch), "kind": kind,
             "value": float(value) if isinstance(value, float) else int(value) if isinstance(value, (bool, int))
             else value}
        if typ:
            e["type"] = typ
        e.update(extra)
        self.io_events.append(e)

    def _external(self, t, name, args):
        self._emit(name, args, t)
        if not (self.started or self._pretest) or self.stopped or self._busy:
            return
        self._busy = True
        try:
            self.t = max(self.t, t)
            self._run(self.t)
        finally:
            self._busy = False
        self._after()

    # ------------------------------------------------------------------ scheduling
    def _add_task(self, due, fn, key=None):
        self._task_seq += 1
        if key is not None:
            self._cancel_task(key)
            self._task_keys[key] = self._task_seq
        heapq.heappush(self._tasks, (due, self._task_seq, fn, key))
        return self._task_seq

    def _cancel_task(self, key):
        seq = self._task_keys.pop(key, None)
        if seq is not None:
            self._cancelled.add(seq)

    def _tick(self, t):
        for key in list(self._trains):
            self._train_tick(key, t)
        for key in list(self._ramps):
            self._ramp_tick(key, t)
        while self._tasks and self._tasks[0][0] <= t + EPS:
            due, seq, fn, key = heapq.heappop(self._tasks)
            if seq in self._cancelled:
                self._cancelled.discard(seq)
                continue
            if key is not None and self._task_keys.get(key) == seq:
                del self._task_keys[key]
            fn(due)
        for name, tm in self.timers.items():
            if tm.due is not None and tm.due <= t + EPS:
                due, tm.due = tm.due, None
                self._emit("timer_elapsed", {"timer": name}, due)
        for name, s in self.schedules.items():
            while s.tick(t):
                self._emit("reinforcer_earned", {"schedule": name}, s.last_t)

    def _poll_detectors(self, t, conditions=False):
        """Stateful detectors, once per frame; condition detectors also after every change within the frame."""
        found = False
        for h in self.handlers:
            if not h.det.generic and not h.broken and self.proc_enabled[h.proc_i] \
                    and (h.det.event in _CONDITION_EVENTS) == conditions:
                for t_occ, args in self._poll(h.det, t):
                    self._queue.append(("fire", h, args, t_occ, 1))
                    found = True
        for th in self.threads:
            w = th.wait
            if th.alive and isinstance(w, EventWait):
                for d in w.dets:
                    if not d.generic and (d.event in _CONDITION_EVENTS) == conditions:
                        hits = self._poll(d, t)
                        d.hits.extend(hits)
                        found = found or bool(hits)
        return found

    def _poll(self, det, t) -> list:
        """One detector's poll; an internal error stops only that detector (it is reported once)."""
        try:
            return det.poll(self, t)
        except Exception as e:  # defensive: never stop the other handlers
            det.done = True
            self._error(det.th, det.path, f"{EVENT_SPECS.get(det.event, {}).get('label', det.event)}: "
                                          f"internal error: {e!r}")
            return []

    def _run(self, t, final=False):
        """Dispatch the queued events and resume the threads that are ready, until nothing more happens. An event
        caused by a chain of more than MAX_EVENT_CHAIN events (signals or variable changes triggering each other
        in a loop) is dropped; more than MAX_EVENTS_PER_RUN events in one call clears the queue."""
        guard = 0
        events = 0
        while True:
            progressed = False
            while self._queue:
                item = self._queue.popleft()
                events += 1
                if events > MAX_EVENTS_PER_RUN:
                    self._error(None, (), f"more than {MAX_EVENTS_PER_RUN} events in one frame: the rest are "
                                          "dropped (do signals or variable changes trigger each other?)")
                    self._queue.clear()
                    break
                if item[4] > MAX_EVENT_CHAIN:
                    what = item[1] if item[0] == "event" else item[1].label
                    self._error(None, (), f"event '{what}' dropped: signals or variable changes trigger each "
                                          "other in a loop")
                    continue
                self._dispatch(item)
                progressed = True
                if self.ended and not final:
                    break
            if self.ended and not final:
                self._queue.clear()
                break
            ready = [th for th in self.threads if th.alive and th.wait is not None and th.wait.ready(self, th, t)]
            for th in ready:
                if th.alive:
                    self._resume(th)
                    progressed = True
            self.threads = [th for th in self.threads if th.alive]
            if self._poll_detectors(t, conditions=True):
                progressed = True
            if not progressed and not self._queue:
                break
            guard += 1
            if guard > 5000:
                self._error(None, (), "procedures did not settle within one frame")
                break

    def _dispatch(self, item):
        kind, a, args, t_occ, depth = item
        outer, self._depth = self._depth, depth
        try:
            if kind == "fire":
                self._fire_safely(a, t_occ, args)
                return
            self._dispatch_event(a, args, t_occ)
        finally:
            self._depth = outer

    def _fire_safely(self, h, t_occ, args):
        """Run a handler; an internal error disables only that handler (reported once)."""
        try:
            self._fire(h, t_occ, args)
        except Exception as e:  # defensive: never stop the other handlers
            h.broken = True
            self._error(h.det.th, h.path, f"When: internal error, the handler is disabled: {e!r}")

    def _dispatch_event(self, name, args, t_occ):
        waiting = [(th, d) for th in self.threads if th.alive and isinstance(th.wait, EventWait)
                   for d in th.wait.dets if d.generic and d.matches(name, args)]
        for h in list(self.handlers):
            if h.det.generic and not h.broken and self.proc_enabled[h.proc_i] and h.det.matches(name, args):
                self._fire_safely(h, t_occ, dict(args, event=name))
        for th, d in waiting:  # threads that started waiting during this dispatch do not see this occurrence
            if th.alive and isinstance(th.wait, EventWait) and any(x is d for x in th.wait.dets):
                d.hits.append((t_occ, dict(args, event=name)))

    def _event_locals(self, t_occ, args):
        name = next((args[k] for k in ("zone", "channel", "key", "var", "timer", "area", "name", "switch",
                                       "schedule") if args.get(k) not in (None, "")), "")
        v = args.get("value", 1)
        if isinstance(v, tuple):
            v = [0 if x is None else x for x in v]
        if not isinstance(v, (int, float, str, list)):
            v = 0
        return {"event_time": t_occ, "event_value": v, "event_name": name, "timed_out": 0, "wait_event": 0}

    def _fire(self, h: _Handler, t_occ, args):
        if not self.proc_enabled[h.proc_i] or h.broken:
            return
        h.threads = [x for x in h.threads if x.alive]
        if h.once and h.count:
            return
        if not self._when_ok(h, t_occ):
            return
        if h.threads:
            if h.mode == "ignore":
                return
            if h.mode == "restart":
                for x in h.threads:
                    self._kill(x)
                h.threads = []
        h.count += 1
        th = _Thread(h.proc_i, h, t_occ, self._event_locals(t_occ, args), h.label)
        th.gen = self._thread_main(th, h.st.get("body") or [], h.path + ("body",))
        self.threads.append(th)
        h.threads.append(th)
        self._resume(th)

    def _resume(self, th):
        if not th.alive:
            return
        th.steps = 0
        try:
            th.wait = th.gen.send(None)
        except StopIteration:
            th.alive, th.wait = False, None
        except Exception as e:  # defensive: never crash the test
            self._error(th, th.path, f"internal error: {e!r}")
            th.alive, th.wait = False, None

    def _kill(self, th):
        th.alive = False
        th.wait = None
        if th.gen is not None:
            try:
                th.gen.close()
            except Exception:  # pragma: no cover
                pass

    def _after(self):
        if self._pending_stop is not None:
            t, self._pending_stop = self._pending_stop, None
            self.stop(t)
        elif self.ended and not self.stopped:
            self.stop(self.t)

    # ------------------------------------------------------------------ statements
    def _thread_main(self, th, stmts, path):
        try:
            yield from self._exec(th, stmts, path)
        except (_Stop, _Break, _Return):
            return
        except _Goto as g:
            self._error(th, th.path, f"Go to: no label '{g.label}' in this block or a block around it")

    def _exec(self, th, stmts, path):
        stmts = stmts or []
        i = 0
        while i < len(stmts):
            st = stmts[i]
            i += 1
            if not isinstance(st, dict) or st.get("enabled", True) is False:
                continue
            t = st.get("type")
            if t in ("comment", "var", "when", "label", None):
                continue
            p = path + (i - 1,)
            th.path = p
            th.steps += 1
            th.ran += 1
            if th.steps > STEP_BUDGET:
                yield FrameWait(self._update_no)
                th.clock = self.t
            try:
                if t == "set":
                    try:
                        self._st_set(th, st, p)
                    except ExprError as e:
                        self._error(th, p, f"Set {st.get('var', '')}: {e}")
                elif t == "do":
                    self._st_do(th, st, p)
                elif t == "if":
                    stmts2, p2 = self._st_if(th, st, p)
                    yield from self._exec(th, stmts2, p2)
                elif t == "repeat":
                    yield from self._st_repeat(th, st, p)
                elif t == "wait":
                    yield from self._st_wait(th, st, p)
                elif t == "stop":
                    self._st_stop(th, st, p)
                elif t == "goto":
                    raise _Goto(str(st.get("label") or ""))
                elif t == "call":
                    yield from self._st_call(th, st, p)
                elif t == "resolution":
                    # ANY-maze's timer resolution: waits and timers here are kept on exact due times and run on the
                    # first frame at or after them (see the module notes), so the value is only recorded
                    self.timer_resolution_ms = self._num(th, st.get("ms"), p, "resolution", 1.0)
                else:
                    self._error(th, p, f"unknown statement type '{t}'")
            except _Goto as g:
                j = next((k for k, s2 in enumerate(stmts) if isinstance(s2, dict) and s2.get("type") == "label"
                          and str(s2.get("name") or "") == g.label), None)
                if j is None:
                    raise
                i = j + 1

    def _st_if(self, th, st, p):
        """The block an If runs: its body, the first else-if clause whose condition holds, or its Else."""
        if self._truth(th, st.get("cond"), p):
            return st.get("body") or [], p + ("body",)
        clauses = st.get("elif") if isinstance(st.get("elif"), list) else []
        for k, c in enumerate(clauses):
            if isinstance(c, dict) and c.get("enabled", True) is not False \
                    and self._truth(th, c.get("cond"), p + ("elif", k)):
                return c.get("body") or [], p + ("elif", k, "body")
        return st.get("else") or [], p + ("else",)

    def _sub_index(self, name):
        return next((i for i, q in enumerate(self.procedures) if q.get("sub") and q.get("name") == name), None)

    def _st_call(self, th, st, p):
        """Run a sub-procedure's statements in this thread (its waits wait here); Stop "return" ends it early."""
        name = str(st.get("procedure") or "")
        pi = self._sub_index(name)
        if pi is None:
            self._error(th, p, f"Call: no sub-procedure called '{name}'")
            return
        if th.depth >= MAX_CALL_DEPTH:
            self._error(th, p, f"Call: sub-procedures nested more than {MAX_CALL_DEPTH} deep")
            return
        caller, th.proc_i = th.proc_i, pi
        th.depth += 1
        try:
            yield from self._exec(th, self.procedures[pi].get("statements") or [], ())
        except _Return:
            pass
        except _Goto as g:
            self._error(th, th.path, f"Go to: no label '{g.label}' in this block or a block around it")
        finally:
            th.depth -= 1
            th.proc_i = caller

    def _st_set(self, th, st, p):
        name = st.get("var")
        value = self._eval(th, st.get("value"))
        idx = st.get("index")
        if idx in (None, ""):
            self._setvar(th, name, value, p)
            return
        arr = self._lookup(th)(str(name))
        if not isinstance(arr, list):
            raise ExprError(f"'{name}' is not an array")
        i = self._eval(th, idx)
        if isinstance(i, float) and i.is_integer():
            i = int(i)
        if not isinstance(i, int) or not -len(arr) <= i < len(arr):
            raise ExprError(f"index {i!r} out of range (length {len(arr)})")
        if isinstance(value, list):
            value = copy.deepcopy(value)
        elif not isinstance(value, (int, float, str, bool)) and value is not None:
            raise ExprError("unsupported value")
        name = str(name)
        if (th is not None and name in th.locals) or self.vars.get(name) is not arr:
            new = list(arr)  # a local (it may be an event's value, shared with other threads): a copy
            new[i] = value
            self._setvar(th, name, new, p)
            return
        # a variable's list is its own (_setvar stores a copy): only the element changes, no copy of the array
        old = arr[i]
        arr[i] = value
        if old != value or type(old) is not type(value):
            self._var_changed(name, arr)

    def _st_repeat(self, th, st, p):
        mode = repeat_mode(st)
        var = str(st.get("var") or "")
        n = int(self._num(th, st.get("count"), p, "count")) if mode == "count" else 0
        i = 0
        while True:
            if mode == "count" and i >= n:
                break
            if mode == "while" and not self._truth(th, st.get("while"), p):
                break
            if var:
                try:
                    self._setvar(th, var, i, p)
                except ExprError as e:
                    self._error(th, p, f"Repeat: {e}")
            before, ran = th.waits, th.ran
            try:
                yield from self._exec(th, st.get("body") or [], p + ("body",))
            except _Break:
                break
            i += 1
            if mode == "until" and self._truth(th, st.get("until"), p):
                break  # "repeat until": the body runs at least once
            if th.waits != before:
                continue
            if th.ran == ran:
                th.steps += 1  # an iteration with nothing in its body still counts against the step budget
            if mode == "forever" or (mode != "count" and th.ran == ran) or th.steps > STEP_BUDGET:
                # a forever loop that did not wait, and a while / until loop whose body ran nothing, poll once per
                # frame; any loop yields to the next frame once the thread has used up its step budget
                yield FrameWait(self._update_no)
                th.clock = self.t
                th.steps = 0

    def _st_wait(self, th, st, p):
        """A wait. It counts as a wait (a forever loop then does not poll) only when it takes time: a wait of 0 s
        or until a condition that is already true does not. The thread's clock never goes back: an event dated
        before the wait started (e.g. a dwell time already reached) resumes it at the wait's start."""
        mode = wait_mode(st)
        to = st.get("timeout")
        tdue = th.clock + self._num(th, to, p, "timeout") if to not in (None, "", 0) else None
        th.locals["timed_out"] = th.locals["wait_event"] = 0
        start = th.clock
        if mode == "seconds":
            secs = max(0.0, self._num(th, st.get("seconds"), p, "Wait"))
            if secs > 0:
                th.waits += 1
            due = th.clock + secs
            yield TimeWait(due)
            th.clock = due
        elif mode == "until":
            if self._truth(th, st.get("until"), p):
                return
            th.waits += 1
            th.resume_clock = self.t
            yield UntilWait(st.get("until"), tdue, p)
            th.clock = max(th.resume_clock, start)
        else:
            th.waits += 1
            det = _Detector(self, st, th, p)
            others = [_Detector(self, a, th, p) for a in wait_alternatives(st)]
            th.resume_clock = self.t
            yield EventWait(det, tdue, others)
            th.clock = max(th.resume_clock, start)

    def _st_stop(self, th, st, p):
        w = st.get("what", "handler")
        if w == "loop":
            raise _Break()
        if w == "procedure":
            self._disable_proc(th.owner, keep=th)
        elif w == "all":
            for pi in range(len(self.procedures)):
                self._disable_proc(pi, keep=th)
        elif w == "test":
            self._end_test(th)
        elif w == "return" and th.depth:
            raise _Return()
        raise _Stop()

    def _disable_proc(self, pi, keep=None):
        """A procedure stops: its handlers no longer run and its threads stop (also those inside a call of a
        sub-procedure: a thread belongs to the procedure that started it)."""
        self.proc_enabled[pi] = False
        for x in self.threads:
            if x.owner == pi and x is not keep and x.alive:
                self._kill(x)

    # ------------------------------------------------------------------ actions of the procedure structure
    def _a_prevent_test_start(self, th, p):
        if self._pretest:
            self.start_blocked = True
            self._log_line(self.t, "Test start prevented")

    def _a_allow_test_start(self, th, p):
        if self._pretest and self.start_blocked:
            self.start_blocked = False
            self._log_line(self.t, "Test start allowed")

    def _a_run_subprocedure(self, th, p, procedure):
        """Start a sub-procedure in a thread of its own (from the frame loop), alongside the calling one."""
        pi = self._sub_index(procedure)
        if pi is None:
            raise ExprError(f"no sub-procedure called '{procedure}'")
        new = _Thread(th.owner, None, self.t, {}, f"sub {procedure}")
        new.gen = self._thread_main(new, [{"type": "call", "procedure": procedure}], ())
        new.wait = TimeWait(self.t)
        self.threads.append(new)

    def _a_output_volts(self, th, p, device, channel, volts):
        dev, ch = self._resolve(th, p, device, channel)
        fs = self._max_v(dev, ch)
        if not 0 <= volts <= fs + EPS:
            self._error(th, p, f"{volts:g} V is outside 0 – {fs:g} V (the channel's max_v): clipped")
        self._set_out(dev, ch, round(max(0.0, min(1.0, float(volts) / fs)), 6), self.t, "pwm")
