"""Test procedures: automate live tests (timing, decisions, loops, variables, hardware I/O).

Procedure format (JSON, stored in ``Project.procedures``)
==========================================================
A project has any number of procedures; all enabled procedures run simultaneously during a live test::

    {"name": "Fear conditioning", "enabled": true, "statements": [
        {"type": "var", "name": "shocks", "value": 0, "result": true},
        {"type": "wait", "seconds": 120},
        {"type": "repeat", "count": 3, "body": [
            {"type": "do", "action": "tone", "frequency": 2800, "duration": 30, "volume": 0.8},
            {"type": "wait", "seconds": 28},
            {"type": "do", "action": "shock_pulse", "device": "box", "channel": "shocker", "duration": 2},
            {"type": "set", "var": "shocks", "value": "shocks + 1"},
            {"type": "wait", "seconds": "randint(60, 120)"}]}]}

A procedure's top-level statements are ``when`` handlers, ``var`` declarations, comments and, optionally, plain
statements, which run in order from the start of the test (an implicit "when the test starts").
Each running handler is an independent cooperative thread: waits never block the frame loop.

Statements (``"type"``)::

    when     {"event": name, <event parameters>, "mode": "ignore"|"restart"|"parallel", "once": bool, "body": [...]}
             top level only. mode: what happens when the event recurs while the handler is still running
             (ignore it — default, restart the handler, or run another copy in parallel).
             Event-wizard options: "times": n, "within": s (runs once the event happened n times within s
             seconds), "trials": "1, 3-5, odd" (only in these trials; context["trial"]).
    wait     {"mode": "seconds", "seconds": expr}
             {"mode": "until", "until": condition, "timeout": expr?}
             {"mode": "event", "event": name, <event parameters>, "timeout": expr?}
             after a timeout the local variable ``timed_out`` is 1 (else 0).
    if       {"cond": condition, "body": [...], "else": [...]}           ("else" optional)
    repeat   {"mode": "count", "count": expr, "var": name?, "body": [...]}
             {"mode": "while", "while": condition, "var": name?, "body": [...]}
             {"mode": "forever", "body": [...]}
             "var" receives the iteration number (0, 1, ...). "count" and "while" loops run instantly (a
             thread yields to the next frame after 5000 statements); a "forever" loop whose body did not
             wait advances one iteration per frame (it polls).
    set      {"var": name, "value": expr, "index": expr?}                  (index: set one array element)
    do       {"action": name, <action parameters>}
    stop     {"what": "handler"|"loop"|"procedure"|"all"|"test"}
    comment  {"text": "..."}
    var      {"name": name, "value": expr, "keep": bool, "result": bool, "record": "end"|"changes"|"set"}
             top level only. keep: the value is kept between tests (``Project.variables``); result: a numeric
             variable's final value saved as a test result (``Test.result_variables``); record: "changes" / "set"
             also log each numeric value, time-stamped, when it changes / every time it is set (I/O log entries of
             kind "variable", analysed into mean, max, min, sum, count and the list of values).

Any statement may carry ``"enabled": false`` to skip it. Expressions are strings (or numbers); conditions are
expressions whose truth value is used. Text parameters may embed expressions in braces: ``"count = {count}"``.

Timing: a handler started by a timed event, and the statements after a ``wait``, run on the first frame at or
after the due time; the next wait counts from the due time, not the frame time, so sequences never drift.

Expressions
===========
Evaluated by a safe interpreter built on ``ast`` (never ``eval``): numbers, strings, lists ``[1, 2, 3]``,
``+ - * / // % **``, comparisons, ``and or not``, ``a if c else b``, ``x[i]`` / slices and calls of the
functions listed in ``FUNCTIONS`` (maths, random numbers, and live state such as ``zone("Centre")``,
``input("box", "lever")``, ``timer("iti")``, ``time()``). Variables are global to all procedures; arrays are lists.

Events (``EVENT_SPECS``) and actions (``ACTION_SPECS``) are catalogued with their parameters in ``catalog``.

Engine
======
``ProcedureEngine(procedures, devices, on_mark=, on_end=, on_log=, variables=, context=)``, then
``start(t)``, ``update_state(t, state)`` once per frame, ``key(t, key, down)``, ``touch(t, area)``,
``mark_event(t, name)``, ``stop(t)``. Results: ``ended``, ``errors``, ``io_events`` (``Test.io_events``
format), ``result_variables``, ``marks``, ``pauses``. Old projects stored rules
``{"trigger", "action", "payload", "zone", "time_s", "delay_s"}``; they are converted on load
(:func:`normalize_procedures`) and the old ``ProcedureEngine(rules, outputs).update(t, zones, ...)`` API works.

The live test implements the engine's callbacks ``on_display`` (pop-ups, text on the display), ``on_video`` (the
video recorder actions) and ``on_zone`` (zone labels and positions), and reports ``system_event(t, name)`` (disk
space low, disk full, recording error); see ``live_state``.

Modules: ``catalog`` (statement types, events, actions), ``expr`` (expressions), ``model`` (procedure documents),
``validate`` (edit-time checks), ``detect`` (event detectors and waits), ``engine`` and ``actions`` (running them),
``live_state`` (investigation, orientation, rearing, event-wizard options, callbacks), ``plugins`` (the "trigger a
plug-in" action), ``legacy`` (the old rules), ``examples``.
"""

from ..iomeasures import io_measures
from .catalog import (ACTION_SPECS, CONSTANTS, CONTAINERS, EPS, EVENT_SPECS, LOCAL_NAMES, SAFETY_TASKS, SHOCK_MAX_S,
                      STALL_S, STATEMENT_TYPES, STEP_BUDGET, STOP_WHAT, WHEN_MODES, P)
from .engine import ProcedureEngine
from .examples import EXAMPLES
from .expr import (FUNCTIONS, MAX_EXPR_LEN, MAX_SEQ, RANDOM_FUNCTIONS, Evaluator, ExprError, check_expr, compile_expr,
                   expr_names, interpolate)
from .legacy import ACTIONS, TRIGGERS, Outputs, convert_rule, describe_rule, is_legacy_rule
from .live_state import WHEN_OPTIONS, parse_clock, parse_trials
from . import plugins
from .model import (RECORD_MODES, describe, describe_event, describe_statement, iter_statements, new_procedure,
                    new_statement, normalize_procedures, path_text, record_mode, repeat_mode, spec_defaults,
                    statement_at, statement_fields, wait_mode)
from .validate import declared_names, project_context, validate

# the procedure editor's names from before the split
_wait_mode, _repeat_mode = wait_mode, repeat_mode
