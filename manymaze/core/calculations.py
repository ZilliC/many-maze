"""Calculations (ANY-maze: Protocol ▸ Analysis ▸ Calculations): results worked out from other results with a formula,
e.g. a discrimination index from the time spent investigating two objects, or the percentage of time in the open
arms of a plus maze.

A calculation has a name, the number of decimal places of its result (0–9, rounded half away from zero), optional
units (at most 8 characters, quoted in the column heading: "Open arms (%)"), an optional graph Y axis range and
named values (constants with a name, e.g. ``Limit = 3``) for its formula. Its results are a column of the results
(ANY-maze's "Calculation results") like any measure: shown on the Data page, exported, compared in the statistics
and usable in other calculations.

The formula is an expression of the procedure language (:mod:`.procedures.expr`: + - * / ** %, comparisons, ``and``
/ ``or`` / ``not``, ``x if c else y`` and the maths functions) in which ``{Measure name}`` is the value of a column
of the same results row (a measure, another calculation or an information column such as ``{Trial}``), plus
ANY-maze's functions:

* ``result_for_period({m}, from_s, to_s)`` or ``result_for_period({m}, "time period")``: the measure for part of
  the test (ResultForPeriod; up to the end of a test that ends during the period, undefined if it ended before)
* ``result_for_trial({m}, stage, trial)`` and ``result_for_last_trial({m}, stage)`` (ResultForTrial,
  ResultForLastTrial)
* ``count_trials``, ``sum_trials``, ``mean_trials``, ``max_trials``, ``min_trials`` (Count, Sum, Mean, Max, Min)
  across the animal's trials: ``({m})`` all of them, ``({m}, stage)`` the trials of a stage, ``({m}, stage, first,
  last)`` some trials of a stage, ``({m}, first stage, first trial, last stage, last trial)`` a range across stages.
  Trials without a result are left out; the result is undefined when the animal has no trial in the range.

As in ANY-maze, a formula whose values are undefined (blank) gives an undefined result (``is_undefined({m})``
tests for it) and ``=`` compares like ``==``. Unlike ANY-maze, which works a formula out from left to right, the
usual operator precedence applies (``2 + 3 * 4`` is 14).

Calculations are worked out by :func:`measures.analyse` for every test and time period (:func:`evaluate_test`);
those that use the trial functions or information columns (directly or through another calculation) need the rest
of the experiment and are worked out by ``Project.results`` (:func:`evaluate`, :class:`Trials`).
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Callable, Iterable

import numpy as np

from .procedures.catalog import CONSTANTS
from .procedures.expr import FUNCTIONS, Evaluator, ExprError, compile_expr

CATEGORY = "Calculation results"  # the measure list category of the calculations (ANY-maze's name)
MAX_DECIMALS = 9
MAX_UNITS = 8  # ANY-maze: units of the result of up to 8 characters
MAX_NAMED_VALUES = 2  # ANY-maze: up to two named values per calculation
MAX_TEXT = 200  # text results (e.g. a category) are cut to this length

PERIOD_FUNCTION = "result_for_period"
AGGREGATES = {"count_trials": "Count", "sum_trials": "Sum", "mean_trials": "Mean", "max_trials": "Max",
              "min_trials": "Min"}
# name: (min args, max args, help); the first argument is always a {measure}
CALC_FUNCTIONS: dict[str, tuple] = {
    PERIOD_FUNCTION: (2, 3, "result_for_period({measure}, from_s, to_s) or ({measure}, \"time period\"): the "
                            "measure for part of the test"),
    "result_for_trial": (3, 3, "result_for_trial({measure}, stage, trial): the measure in a trial of a stage"),
    "result_for_last_trial": (2, 2, "result_for_last_trial({measure}, stage): the measure in the animal's last "
                                    "trial of a stage"),
    "count_trials": (1, 5, "count_trials({measure}[, stage[, first, last]]): the animal's trials with a result"),
    "sum_trials": (1, 5, "sum_trials({measure}[, stage[, first, last]]): sum across the animal's trials"),
    "mean_trials": (1, 5, "mean_trials({measure}[, stage[, first, last]]): mean across the animal's trials"),
    "max_trials": (1, 5, "max_trials({measure}[, stage[, first, last]]): largest value across the animal's trials"),
    "min_trials": (1, 5, "min_trials({measure}[, stage[, first, last]]): smallest value across the animal's "
                         "trials"),
}
TRIAL_FUNCTIONS = frozenset(CALC_FUNCTIONS) - {PERIOD_FUNCTION}

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(r"[MF]__\d+$")
_TOKENS = re.compile(r"""('[^']*'|"[^"]*")|\{([^{}]*)\}|(#N/A)""")
_LONE_EQ = re.compile(r"(?<![=!<>])=(?!=)")


@dataclass
class Calculation:
    name: str = "Calculation"
    formula: str = ""
    decimals: int = 2  # decimal places of the result (ANY-maze's default is 0)
    units: str = ""
    y_max: float | None = None  # graph Y axis range (None: automatic)
    y_min: float | None = None
    named_values: list = field(default_factory=list)  # [[name, value], ...]

    @classmethod
    def from_dict(cls, d: dict) -> "Calculation":
        def num(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        try:
            dp = int(d.get("decimals", 2))
        except (TypeError, ValueError):
            dp = 2
        named = []
        for nv in d.get("named_values") or []:
            if isinstance(nv, (list, tuple)) and len(nv) == 2:
                named.append([str(nv[0] or "").strip(), num(nv[1])])
        return cls(str(d.get("name", "") or ""), str(d.get("formula", "") or ""), max(0, min(MAX_DECIMALS, dp)),
                   str(d.get("units", "") or ""), num(d.get("y_max")), num(d.get("y_min")), named)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def column(self) -> str:
        """The results column: the name, with the units in brackets ("Discrimination index", "Open arms (%)")."""
        u = self.units.strip()
        return f"{self.name.strip()} ({u})" if u else self.name.strip()

    def values(self) -> dict:
        """The named values with a name and a number, {name: value}."""
        return {n: v for n, v in self.named_values if n and v is not None}


def calculations_from(items) -> list[Calculation]:
    """Calculations from project.json (a list of dicts; anything else is ignored)."""
    return [Calculation.from_dict(c) for c in items or [] if isinstance(c, dict)]


# ---------------------------------------------------------------- parsing
@dataclass
class _Parsed:
    expr: str = ""  # the expression for compile_expr: measures and function calls replaced by M__k / F__k
    measures: dict = field(default_factory=dict)  # placeholder -> measure name
    calls: list = field(default_factory=list)  # [(placeholder, function, measure, [argument expressions])]
    error: str = ""

    @property
    def references(self) -> set[str]:
        """The names of every results column the formula reads."""
        return set(self.measures.values()) | {c[2] for c in self.calls}

    @property
    def functions(self) -> set[str]:
        return {c[1] for c in self.calls}


_parsed: dict[str, _Parsed] = {}


def _substitute(formula: str) -> tuple[str, dict]:
    """The formula with {measure} replaced by placeholders, ``=`` by ``==`` and #N/A by NA (outside quoted text)."""
    out, names, pos = [], {}, 0

    def plain(s):
        return _LONE_EQ.sub("==", s)

    for m in _TOKENS.finditer(formula):
        out.append(plain(formula[pos:m.start()]))
        if m.group(1):
            out.append(m.group(1))
        elif m.group(3):
            out.append("NA")
        else:
            name = m.group(2).strip()
            if not name:
                raise ExprError("empty measure name {}")
            out.append(names.setdefault(name, f"M__{len(names)}"))
        pos = m.end()
    out.append(plain(formula[pos:]))
    return "".join(out).strip(), {ph: n for n, ph in names.items()}


def parse(formula: str) -> _Parsed:
    """The checked structure of a formula (cached); ``error`` says what is wrong with it."""
    formula = str(formula or "")
    hit = _parsed.get(formula)
    if hit is not None:
        return hit
    out = _Parsed()
    try:
        src, measures = _substitute(formula)
        out.measures = measures
        try:
            tree = ast.parse(src, mode="eval")
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            compile_expr(src)  # raises the expression language's message
            raise ExprError("invalid formula") from None

        class Calls(ast.NodeTransformer):
            def visit_Call(self, n):
                fn = n.func.id if isinstance(n.func, ast.Name) else ""
                if fn not in CALC_FUNCTIONS:
                    self.generic_visit(n)
                    return n
                lo, hi = CALC_FUNCTIONS[fn][:2]
                if n.keywords or not lo <= len(n.args) <= hi or (fn in AGGREGATES and len(n.args) == 3):
                    use = CALC_FUNCTIONS[fn][2].split(": ")[0]
                    if fn in AGGREGATES:
                        use += f" or {fn}({{measure}}, first stage, first trial, last stage, last trial)"
                    raise ExprError(f"wrong arguments: use {use}")
                first = n.args[0]
                if not (isinstance(first, ast.Name) and first.id in measures):
                    raise ExprError(f"the first argument of {fn}() must be a measure in braces, e.g. "
                                    "{Total distance (m)}")
                args = []
                for a in n.args[1:]:
                    for x in ast.walk(a):
                        if isinstance(x, ast.Name) and x.id in measures or \
                                isinstance(x, ast.Call) and getattr(x.func, "id", "") in CALC_FUNCTIONS:
                            raise ExprError(f"the arguments of {fn}() after the measure cannot use measures")
                    text = ast.unparse(a)
                    compile_expr(text)
                    args.append(text)
                ph = f"F__{len(out.calls)}"
                out.calls.append((ph, fn, measures[first.id], args))
                return ast.Name(ph, ast.Load())

        body = Calls().visit(tree.body)
        out.expr = ast.unparse(body)
        node = compile_expr(out.expr)
        used = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
        out.measures = {ph: n for ph, n in measures.items() if ph in used}
        for x in [y for t in [out.expr] + [a for c in out.calls for a in c[3]] for y in ast.walk(compile_expr(t))]:
            if isinstance(x, ast.Call) and FUNCTIONS[x.func.id][2] is None:
                raise ExprError(f"{x.func.id}() cannot be used in a calculation (only in procedures)")
    except ExprError as e:
        out.error = str(e)
    except RecursionError:
        out.error = "formula too deeply nested"
    if len(_parsed) < 2000:
        _parsed[formula] = out
    return out


def check_calculation(calc: Calculation, measures: Iterable[str] | None = None,
                      calculations: list[Calculation] | None = None, reserved: Iterable[str] = ()) -> list[str]:
    """Edit-time problems of a calculation: its name (blank, used by another calculation or an information column),
    decimal places, units, Y axis range, named values and formula (syntax, functions, unknown names, circular
    references). measures: the measure columns of the current results, without the calculations' (unknown measures
    are reported); calculations: all the experiment's calculations (names and circular references); reserved: the
    information columns."""
    errs = []
    col = calc.column
    if not calc.name.strip():
        errs.append("the calculation needs a name")
    elif col in set(reserved):
        errs.append(f"“{col}” is an information column: choose another name")
    elif measures is not None and col in set(measures):
        errs.append(f"a measure is called “{col}”: the calculation's result would replace it")
    others = [c for c in calculations or [] if c is not calc]
    if col and col in {c.column for c in others}:
        errs.append(f"another calculation is called “{col}”")
        calculations = None  # (the second one is never worked out: no circular reference to report)
    if not 0 <= int(calc.decimals) <= MAX_DECIMALS:
        errs.append(f"decimal places must be 0–{MAX_DECIMALS}")
    if len(calc.units.strip()) > MAX_UNITS:
        errs.append(f"units can have at most {MAX_UNITS} characters")
    if calc.y_max is not None and calc.y_min is not None and calc.y_max <= calc.y_min:
        errs.append("the Y axis maximum must be greater than its minimum")
    seen = set()
    for n, v in calc.named_values:
        if not n and v is None:
            continue
        if not _NAME.match(n or "") or _PLACEHOLDER.match(n):
            errs.append(f"named value “{n}”: names start with a letter and have only letters, digits and _")
        elif n in seen:
            errs.append(f"named value “{n}” is defined twice")
        elif v is None:
            errs.append(f"named value “{n}” needs a number")
        seen.add(n)
    if not calc.formula.strip():
        return errs + ["enter a formula"]
    p = parse(calc.formula)
    if p.error:
        return errs + [p.error]
    known = set(calc.values()) | set(CONSTANTS) | set(p.measures) | {c[0] for c in p.calls}
    texts = [p.expr] + [a for c in p.calls for a in c[3]]
    for name in sorted({x.id for t in texts for x in ast.walk(compile_expr(t)) if isinstance(x, ast.Name)}):
        if name not in known and name not in FUNCTIONS:
            errs.append(f"unknown name “{name}” (write measures in braces, e.g. {{Total distance (m)}})")
    if measures is not None:
        have = set(measures) | {c.column for c in calculations or []} | set(reserved)
        errs += [f"no measure called “{m}” in the results" for m in sorted(p.references) if m not in have]
    if col and col in p.references:
        errs.append("the formula uses its own result")
    elif calculations and any(s.calc is calc and not s.ok for s in plan(calculations)):
        errs.append("circular reference: the calculations use each other's results")
    return errs


# ---------------------------------------------------------------- order of evaluation
@dataclass
class Step:
    calc: Calculation
    deferred: bool  # needs the other trials or the information columns: worked out by Project.results
    index: int = 0  # position in the experiment's list (the order of the result columns)
    ok: bool = True  # False: part of a circular reference (or using one), never worked out (NaN)


def plan(calculations, info: Iterable[str] = ()) -> list[Step]:
    """The calculations in an order in which they can be worked out (each after the calculations it uses), with
    whether each is deferred to Project.results (it uses a trial function or one of the `info` columns, or a deferred
    calculation); then, not ok, those in a circular reference. A second calculation with the column of another is
    left out. Steps given instead of calculations are returned as they are."""
    calculations = list(calculations or [])
    if calculations and isinstance(calculations[0], Step):
        return calculations
    info = set(info)
    by_col: dict[str, tuple[int, Calculation]] = {}
    for i, c in enumerate(calculations):
        if c.column:
            by_col.setdefault(c.column, (i, c))
    deps = {col: {r for r in parse(c.formula).references if r in by_col} for col, (_i, c) in by_col.items()}
    out, deferred = [], {}
    pending = list(by_col)
    while pending:
        ready = [col for col in pending if deps[col] <= set(deferred)]
        if not ready:
            break  # the rest use each other's results
        for col in ready:
            i, c = by_col[col]
            p = parse(c.formula)
            deferred[col] = bool(p.functions & TRIAL_FUNCTIONS) or bool(p.references & info) or \
                any(deferred[d] for d in deps[col])
            out.append(Step(c, deferred[col], i))
        pending = [col for col in pending if col not in deferred]
    return out + [Step(by_col[col][1], False, by_col[col][0], ok=False) for col in pending]


# ---------------------------------------------------------------- evaluation
def _value(v):
    """A results value as a formula sees it: numbers as int / float (NaN for undefined), text as text, None when
    missing or blank."""
    if v is None or isinstance(v, str) and not v.strip():
        return None
    if isinstance(v, (bool, np.bool_, int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v)
    return v if isinstance(v, str) else None


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def round_half_away(v: float, decimals: int) -> float:
    """v rounded to `decimals` places, halves away from zero (ANY-maze rounds; Python's round() goes to even)."""
    try:
        return float(Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return float(v)


def result_value(v, decimals: int = 2):
    """A formula's value as a result: numbers rounded to `decimals` places (an int with 0 places), text kept,
    anything else (and infinite / undefined numbers) NaN."""
    if isinstance(v, (bool, np.bool_)):
        v = int(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        if not math.isfinite(v):
            return math.nan
        d = max(0, min(MAX_DECIMALS, int(decimals)))
        r = round_half_away(v, d)
        return int(r) if d == 0 and abs(r) < 2 ** 63 else r
    if isinstance(v, str):
        return v[:MAX_TEXT]
    return math.nan


def _trial(t) -> int:
    try:
        return int(t)
    except (TypeError, ValueError):
        return 0


class Trials:
    """The results rows of one animal (one time period each) by stage and trial: rows [(stage, trial, row)],
    stages the experiment's stages in order (other stages follow in the order met)."""

    def __init__(self, rows: list[tuple], stages: Iterable[str] = ()):
        self.order: dict[str, int] = {}
        for s in list(stages) + [str(r[0]) for r in rows]:
            self.order.setdefault(str(s), len(self.order))
        self.rows = sorted(((str(s), _trial(t), r) for s, t, r in rows), key=lambda x: (self.order[x[0]], x[1]))

    def _key(self, stage, trial, last=False) -> tuple:
        s = str(stage)
        if s not in self.order:
            raise ExprError(f"no stage called “{s}”")
        return self.order[s], (math.inf if last else -math.inf) if trial is None else int(trial)

    def trial(self, stage, trial) -> dict | None:
        k = self._key(stage, trial)
        return next((r for s, t, r in self.rows if (self.order[s], t) == k), None)

    def last(self, stage) -> dict | None:
        s = str(stage)
        if s not in self.order:
            raise ExprError(f"no stage called “{s}”")
        rows = [r for st, _t, r in self.rows if st == s]
        return rows[-1] if rows else None

    def between(self, first: tuple | None, last: tuple | None) -> list[dict]:
        """Rows from (stage, trial) to (stage, trial) inclusive (trial None: the stage's first / last; None: the
        animal's first / last trial)."""
        lo = self._key(*first) if first else (-math.inf, -math.inf)
        hi = self._key(*last, last=True) if last else (math.inf, math.inf)
        return [r for s, t, r in self.rows if lo <= (self.order[s], t) <= hi]


def _aggregate(fn: str, measure: str, args: list, trials: Trials):
    if len(args) == 0:
        rows = trials.between(None, None)
    elif len(args) == 1:
        rows = trials.between((args[0], None), (args[0], None))
    elif len(args) == 2:
        rows = trials.between((args[0], args[1]), (args[0], args[1]))  # pragma: no cover - rejected by parse()
    elif len(args) == 3:
        rows = trials.between((args[0], args[1]), (args[0], args[2]))
    else:
        rows = trials.between((args[0], args[1]), (args[2], args[3]))
    if not rows:
        return None  # ANY-maze: undefined when the animal has no trial in the range
    vals = [float(v) for v in (_value(r.get(measure)) for r in rows) if _number(v)]
    if fn == "count_trials":
        return len(vals)
    if not vals:
        return None
    return {"sum_trials": sum, "mean_trials": lambda x: sum(x) / len(x), "max_trials": max,
            "min_trials": min}[fn](vals)


def evaluate_calc(calc: Calculation, row: dict, period: Callable | None = None, trials: Trials | None = None):
    """The result of one calculation for a results row. period(spec) -> the results ({column: value}) of part of
    the test, spec (from_s, to_s) or a time period's name, or None if the test ended before it; trials: the
    animal's rows for the trial functions. Without them those functions are undefined. Never raises: a formula
    that cannot be worked out gives NaN (undefined)."""
    p = parse(calc.formula)
    if p.error:
        return math.nan
    named = calc.values()
    calls = {c[0]: c for c in p.calls}
    done: dict = {}

    def lookup_named(name):
        if name in named:
            return named[name]
        raise ExprError(f"unknown name '{name}'")

    args_eval = Evaluator(lookup_named)

    def call(ph):
        _ph, fn, measure, arg_texts = calls[ph]
        args = [args_eval.eval(a) for a in arg_texts]
        if fn == PERIOD_FUNCTION:
            if period is None:
                return None
            if len(args) == 1:
                spec = str(args[0])
            else:
                a, b = float(args[0]), float(args[1])
                if not (math.isfinite(a) and math.isfinite(b)) or a < 0 or b <= a:
                    raise ExprError("result_for_period(): the period must end after it starts")
                spec = (a, b)
            res = period(spec)
            return None if res is None else _value(res.get(measure))
        if trials is None:
            return None
        if fn == "result_for_trial":
            r = trials.trial(args[0], args[1])
        elif fn == "result_for_last_trial":
            r = trials.last(args[0])
        else:
            return _aggregate(fn, measure, args, trials)
        return None if r is None else _value(r.get(measure))

    def lookup(name):
        if name in p.measures:
            return _value(row.get(p.measures[name]))
        if name in calls:
            if name not in done:
                done[name] = call(name)
            return done[name]
        return lookup_named(name)

    try:
        v = Evaluator(lookup).eval(p.expr)
    except ExprError:
        return math.nan
    except Exception:  # a callback failing (e.g. an unreadable track for a period) must not lose the other results
        return math.nan
    return result_value(v, calc.decimals)


def evaluate(steps, row: dict, period: Callable | None = None, trials: Trials | None = None,
             deferred: bool | None = None) -> dict:
    """{column: result} of the calculations (a list of Calculation or of plan() steps) for a results row, each
    seeing the results of those before it, in the order of the experiment's list. deferred: None every step, False
    the steps worked out per test (NaN for the deferred ones, worked out later), True only the deferred ones."""
    view = dict(row)
    got = {}
    for s in plan(steps):
        if deferred is not None and s.deferred != deferred:
            if not deferred:
                got[s.index] = (s.calc.column, math.nan)
            continue
        v = evaluate_calc(s.calc, view, period, trials) if s.ok else math.nan
        view[s.calc.column] = v
        got[s.index] = (s.calc.column, v)
    return dict(got[i] for i in sorted(got))


def evaluate_test(calculations, row: dict, period: Callable | None = None) -> dict:
    """The calculations of a test's results row (measures.analyse): those that need only this test; NaN for the
    deferred ones (Project.results works them out) and for those that cannot be worked out."""
    return evaluate(calculations, row, period, deferred=False)


def y_range(calculations: Iterable[Calculation], column: str) -> tuple[float | None, float | None] | None:
    """The (minimum, maximum) graph Y axis range set for the calculation with this column, or None."""
    for c in calculations or []:
        if c.column == column and (c.y_max is not None or c.y_min is not None):
            return c.y_min, c.y_max
    return None


def apply_y_range(fig, rng: tuple | None, values: Iterable = ()):
    """Fix the Y axis of a graph (every panel) to a calculation's range, as ANY-maze, unless one of the plotted
    results falls outside it (automatic scaling then). A missing minimum is 0 (or the automatic one below 0), a
    missing maximum the automatic one."""
    if not rng or fig is None or not fig.axes:
        return
    vals = [float(v) for v in values if _number(_value(v))]
    for ax in fig.axes:
        lo, hi = ax.get_ylim()
        want_lo = (lo if lo < 0 else 0.0) if rng[0] is None else rng[0]
        want_hi = hi if rng[1] is None else rng[1]
        if want_hi <= want_lo or vals and (min(vals) < want_lo - 1e-9 or max(vals) > want_hi + 1e-9):
            return
    for ax in fig.axes:
        lo, hi = ax.get_ylim()
        ax.set_ylim((lo if lo < 0 else 0.0) if rng[0] is None else rng[0], hi if rng[1] is None else rng[1])


FUNCTION_HELP = [(name, spec[2]) for name, spec in CALC_FUNCTIONS.items()]
