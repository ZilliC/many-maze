"""The procedure engine: runs procedures during a live test as cooperative threads driven by the frames."""

from __future__ import annotations

import copy
import heapq
import math
import random
import threading
from collections import deque
from functools import wraps

from ..iodevices import DeviceManager
from ..operant import Schedule
from .actions import Actions, _Break, _Stop, _Timer, _Train
from .catalog import CONSTANTS, EPS, SAFETY_TASKS, STEP_BUDGET
from .detect import _CONDITION_EVENTS, EventWait, FrameWait, TimeWait, UntilWait, _Detector
from .expr import Evaluator, ExprError, interpolate
from .legacy import Outputs
from .model import _short, normalize_procedures, path_text, record_mode, repeat_mode, wait_mode
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
}


# ---------------------------------------------------------------------- engine
class _Handler:
    def __init__(self, proc_i, path, st, det, label):
        self.proc_i, self.path, self.st, self.det, self.label = proc_i, path, st, det, label
        self.mode = st.get("mode", "ignore")
        self.once = bool(st.get("once"))
        self.threads: list = []
        self.count = 0


class _Thread:
    def __init__(self, proc_i, handler, clock, locals_, label):
        self.proc_i, self.handler, self.clock, self.locals, self.label = proc_i, handler, clock, locals_, label
        self.alive = True
        self.wait = None
        self.steps = 0
        self.waits = 0
        self.gen = None
        self.path = ()
        self.resume_clock = clock


def _locked(fn):
    @wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class ProcedureEngine(Actions):
    """Runs procedures during a live test. Call start(t), update_state(t, state) every frame, stop(t).

    The public methods are thread-safe (e.g. frames processed in a worker thread, keys and touches from the GUI);
    callbacks run in the thread that called the engine.

    Pausing (:meth:`pause`) runs the "test paused" handlers at once, stops pulse trains, switches shock outputs
    off and, with ``outputs_off_on_pause`` (default), every output and sound. While paused call
    :meth:`paused_tick` with the real time since the pause so that safety tasks still run.
    Variables declared "keep" are copied to ``variables`` at :meth:`stop` unless ``commit_kept`` is False; they
    are always available in ``kept_variables`` (e.g. to be stored only when the test is saved)."""

    def __init__(self, procedures=None, devices=None, on_mark=None, on_end=None, on_log=None, variables=None,
                 context=None, *, on_pause=None, on_resume=None, on_stimulus=None, seed=None,
                 outputs_off_at_end: bool = True, outputs_off_on_pause: bool = True, commit_kept: bool = True):
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
        self._lock = threading.RLock()
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self):
        self.started = self.stopped = self.ended = self.paused = False
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
        self._shock_keys: set[tuple] = set()
        self._pause_t: float | None = None

    def _start_procs(self, pis, t, defer=False):
        """Start procedures: declare their variables (all of them first), create their handlers, then start their
        top-level plain statements. defer: started by another thread's action, so the new threads run from the frame
        loop instead of nested inside that thread."""
        self._started_procs.update(pis)
        for pi in pis:
            for i, st in enumerate(self.procedures[pi].get("statements") or []):
                if isinstance(st, dict) and st.get("type") == "var" and st.get("enabled", True) is not False:
                    self._declare(pi, (i,), st)
        for pi in pis:
            for i, st in enumerate(self.procedures[pi].get("statements") or []):
                if isinstance(st, dict) and st.get("type") == "when" and st.get("enabled", True) is not False:
                    th = _Thread(pi, None, t, {}, "when")
                    det = _Detector(self, st, th, (i,), initial=True)
                    self.handlers.append(_Handler(pi, (i,), st, det, st.get("event", "?")))
        for pi in pis:
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
        """Queue an occurrence of an event (dispatched to the handlers and waiting threads by _run)."""
        self._queue.append(("event", name, args, t))

    # ------------------------------------------------------------------ public API
    @_locked
    def start(self, t: float = 0.0):
        if self.started:
            return
        self._reset()
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
        if self.stopped or not self.started:
            if not self.started:
                self.stopped = True
            return
        if self._busy:
            self._pending_stop = t if self._pending_stop is None else max(self._pending_stop, t)
            return
        self._busy = True
        try:
            self.t = max(self.t, t)
            t = self.t
            self._tick(t)
            self._emit("test_end", {}, t)
            self._run(t, final=True)
            for th in self.threads:
                self._kill(th)
            self.threads = []
            for key in list(self._trains):
                self._stop_train(key, t)
            self._tasks, self._task_keys, self._cancelled = [], {}, set()
            if self.outputs_off_at_end:
                for (dev, ch), v in list(self.outputs_state.items()):
                    if v:
                        self._set_out(dev, ch, 0, t, "digital")
                for key in list(self._audio_on):
                    self._audio_off(key, t)
                for name, v in list(self.switches.items()):
                    if v:
                        self._set_switch(name, 0, t)
            for m in self.state_events:
                if m["t_end"] is None:
                    m["t_end"] = t
            for pz in self.pauses:
                if pz[1] is None:
                    pz[1] = t
            for name, flags in self.var_flags.items():
                v = self.vars.get(name)
                if flags.get("keep"):
                    self.kept_variables[name] = copy.deepcopy(v)
                    if self.commit_kept:
                        self.variables[name] = copy.deepcopy(v)
                if flags.get("result") and isinstance(v, (int, float)) and not isinstance(v, str):
                    self.result_variables[name] = float(v) if isinstance(v, float) else int(v)
            self.io_events.sort(key=lambda e: e["t"])
        finally:
            self._busy = False
            self.stopped = True

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
        return Evaluator(self._lookup(th), self._call, self.rng).eval(src)

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
        self.var_flags[name] = {"keep": bool(st.get("keep")), "result": bool(st.get("result")),
                                "record": record_mode(st)}
        if st.get("keep") and name in self.variables:
            self.vars[name] = copy.deepcopy(self.variables[name])
            return
        th = _Thread(pi, None, self.t, {}, "")
        try:
            self.vars[name] = self._eval(th, st.get("value", 0))
        except ExprError as e:
            self._error(th, path, f"Variable {name}: {e}")
            self.vars[name] = 0

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
            self._emit("variable_changed", {"var": name, "value": value}, self.t)

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
        for dev, ch, kind, value, ms in changes:
            if kind == "watchdog":
                self._watchdog_fired(t, dev)
            elif quiet:  # paused: keep the input states, no events
                self.inputs[(dev, ch)] = value if kind in ("analog", "encoder") else (1 if value else 0)
            else:
                self._input_changed(t, dev, ch, kind, value, ms)
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
        if kind in ("analog", "encoder"):
            self.inputs[key] = value
            self._log_io(t, dev, ch, "input", value, kind, **extra)
            if old is None or value != old:
                self._emit("input_changed", {"device": dev, "channel": ch, "value": value}, t)
            return
        v = 1 if value else 0
        if old is not None and v == (1 if old else 0):
            return
        if old is None and v == 0:
            self.inputs[key] = 0
            return
        self.inputs[key] = v
        self._log_io(t, dev, ch, "input", v, "digital", **extra)
        args = {"device": dev, "channel": ch, "value": v}
        self._emit("input_changed", args, t)
        self._emit("input_on" if v else "input_off", args, t)
        if v:
            self.input_counts[key] = self.input_counts.get(key, 0) + 1

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
        if not self.started or self.stopped or self._busy:
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
            if not h.det.generic and self.proc_enabled[h.proc_i] \
                    and (h.det.event in _CONDITION_EVENTS) == conditions:
                for t_occ, args in h.det.poll(self, t):
                    self._queue.append(("fire", h, args, t_occ))
                    found = True
        for th in self.threads:
            w = th.wait
            if th.alive and isinstance(w, EventWait) and not w.det.generic \
                    and (w.det.event in _CONDITION_EVENTS) == conditions:
                hits = w.det.poll(self, t)
                w.det.hits.extend(hits)
                found = found or bool(hits)
        return found

    def _run(self, t, final=False):
        guard = 0
        while True:
            progressed = False
            while self._queue:
                guard += 1
                if guard > 2000:
                    self._error(None, (), "too many events in one frame (do signals or variable changes trigger "
                                          "each other in a loop?)")
                    self._queue.clear()
                    break
                self._dispatch(self._queue.popleft())
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
        kind, a, args, t_occ = item
        if kind == "fire":
            self._fire(a, t_occ, args)
            return
        name = a
        waiting = [th for th in self.threads if th.alive and isinstance(th.wait, EventWait)
                   and th.wait.det.generic and th.wait.det.matches(name, args)]
        for h in list(self.handlers):
            if h.det.generic and self.proc_enabled[h.proc_i] and h.det.matches(name, args):
                self._fire(h, t_occ, dict(args, event=name))
        for th in waiting:  # threads that started waiting during this dispatch do not see this occurrence
            if th.alive and isinstance(th.wait, EventWait):
                th.wait.det.hits.append((t_occ, dict(args, event=name)))

    def _event_locals(self, t_occ, args):
        name = next((args[k] for k in ("zone", "channel", "key", "var", "timer", "area", "name", "switch",
                                       "schedule") if args.get(k) not in (None, "")), "")
        v = args.get("value", 1)
        if isinstance(v, tuple):
            v = [0 if x is None else x for x in v]
        if not isinstance(v, (int, float, str, list)):
            v = 0
        return {"event_time": t_occ, "event_value": v, "event_name": name, "timed_out": 0}

    def _fire(self, h: _Handler, t_occ, args):
        if not self.proc_enabled[h.proc_i]:
            return
        h.threads = [x for x in h.threads if x.alive]
        if h.once and h.count:
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
        except (_Stop, _Break):
            return

    def _exec(self, th, stmts, path):
        for i, st in enumerate(stmts or []):
            if not isinstance(st, dict) or st.get("enabled", True) is False:
                continue
            t = st.get("type")
            if t in ("comment", "var", "when", None):
                continue
            p = path + (i,)
            th.path = p
            th.steps += 1
            if th.steps > STEP_BUDGET:
                yield FrameWait(self._update_no)
                th.clock = self.t
            if t == "set":
                try:
                    self._st_set(th, st, p)
                except ExprError as e:
                    self._error(th, p, f"Set {st.get('var', '')}: {e}")
            elif t == "do":
                self._st_do(th, st, p)
            elif t == "if":
                br = "body" if self._truth(th, st.get("cond"), p) else "else"
                yield from self._exec(th, st.get(br) or [], p + (br,))
            elif t == "repeat":
                yield from self._st_repeat(th, st, p)
            elif t == "wait":
                yield from self._st_wait(th, st, p)
            elif t == "stop":
                self._st_stop(th, st, p)
            else:
                self._error(th, p, f"unknown statement type '{t}'")

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
        new = list(arr)
        new[i] = copy.deepcopy(value)
        self._setvar(th, name, new, p)

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
            before = th.waits
            try:
                yield from self._exec(th, st.get("body") or [], p + ("body",))
            except _Break:
                break
            i += 1
            if mode == "forever" and th.waits == before:
                yield FrameWait(self._update_no)  # a forever loop that did not wait polls once per frame
                th.clock = self.t

    def _st_wait(self, th, st, p):
        mode = wait_mode(st)
        to = st.get("timeout")
        tdue = th.clock + self._num(th, to, p, "timeout") if to not in (None, "", 0) else None
        th.locals["timed_out"] = 0
        th.waits += 1
        if mode == "seconds":
            due = th.clock + max(0.0, self._num(th, st.get("seconds"), p, "Wait"))
            yield TimeWait(due)
            th.clock = due
        elif mode == "until":
            if self._truth(th, st.get("until"), p):
                return
            th.resume_clock = self.t
            yield UntilWait(st.get("until"), tdue, p)
            th.clock = th.resume_clock
        else:
            det = _Detector(self, st, th, p)
            th.resume_clock = self.t
            yield EventWait(det, tdue)
            th.clock = th.resume_clock

    def _st_stop(self, th, st, p):
        w = st.get("what", "handler")
        if w == "loop":
            raise _Break()
        if w == "procedure":
            self._disable_proc(th.proc_i, keep=th)
        elif w == "all":
            for pi in range(len(self.procedures)):
                self._disable_proc(pi, keep=th)
        elif w == "test":
            self._end_test(th)
        raise _Stop()

    def _disable_proc(self, pi, keep=None):
        self.proc_enabled[pi] = False
        for x in self.threads:
            if x.proc_i == pi and x is not keep and x.alive:
                self._kill(x)
