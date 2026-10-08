"""Expressions: a safe interpreter built on ``ast`` (never ``eval``) and text interpolation."""

from __future__ import annotations

import ast
import math
import operator as op
import random
import re
from typing import Callable

from .catalog import CONSTANTS

MAX_EXPR_LEN = 2000
MAX_SEQ = 100_000


class ExprError(Exception):
    pass


def _rmean(xs):
    xs = list(xs)
    if not xs:
        raise ExprError("mean() of an empty list")
    return sum(xs) / len(xs)


def _seq(a):
    if isinstance(a, (list, tuple, str)):
        return list(a)
    raise ExprError("expected a list")


def _minmax(f):
    def g(*a):
        if len(a) == 1:
            a = _seq(a[0])
            if not a:
                raise ExprError(f"{f.__name__}() of an empty list")
        return f(a)
    return g


def _array(n, fill=0):
    n = int(n)
    if not 0 <= n <= MAX_SEQ:
        raise ExprError(f"array size must be 0..{MAX_SEQ}")
    return [fill] * n


def _range(a, b=None, step=1):
    a, b = (0, a) if b is None else (a, b)
    r = range(int(a), int(b), int(step) or 1)
    if len(r) > MAX_SEQ:
        raise ExprError("range too long")
    return list(r)


def _round(x, nd=0):
    return round(x, int(nd)) if nd else round(x)


def _randint(rng, a, b):
    a, b = int(a), int(b)
    if b < a:
        raise ExprError("randint(a, b) needs a <= b")
    return rng.randint(a, b)


def _choice(rng, a):
    s = _seq(a)
    if not s:
        raise ExprError("choice() of an empty list")
    return rng.choice(s)


def _shuffle(rng, a, max_run=0):
    """A shuffled copy of a list; with max_run > 0, no value appears more than max_run times in a row (ANY-maze's
    "randomise array" with a maximum number of consecutive repeats)."""
    s = _seq(a)
    k = int(max_run or 0)
    if k <= 0 or len(s) < 2:
        rng.shuffle(s)
        return s
    groups: list[list] = []  # [value, count]: values may be lists, so they are grouped by equality
    for v in s:
        g = next((g for g in groups if g[0] == v and type(g[0]) is type(v)), None)
        if g is None:
            groups.append([v, 1])
        else:
            g[1] += 1
    most = max(g[1] for g in groups)
    if most > k * (len(s) - most + 1):
        raise ExprError(f"shuffle(): the list cannot be arranged with at most {k} repeats in a row")
    for _ in range(200):
        out = _bounded_runs(rng, groups, len(s), k)
        if out is not None:
            return out
    raise ExprError(f"shuffle(): no arrangement with at most {k} repeats in a row was found")


def _bounded_runs(rng, groups, n, k):
    """One random arrangement of the grouped values with runs of at most k (None if this attempt got stuck)."""
    left = [g[1] for g in groups]
    out, last, run = [], -1, 0
    for i in range(n):
        rest = n - i
        allowed = [j for j in range(len(groups)) if left[j] and not (j == last and run >= k)]
        if not allowed:
            return None
        # a value that would otherwise have too few separators left must be placed now
        forced = [j for j in allowed if left[j] > k * (rest - left[j])]
        pool = forced or allowed
        j = rng.choices(pool, weights=[left[x] for x in pool])[0]
        out.append(groups[j][0])
        left[j] -= 1
        run = run + 1 if j == last else 1
        last = j
    return out


def _undefined(x) -> int:
    return int(x is None or (isinstance(x, float) and math.isnan(x)))


def _deg(f):
    return lambda x: f(math.radians(x))


def _to_deg(f):
    return lambda *a: math.degrees(f(*a))


def _log10_or_base(x, base=None):
    return math.log10(x) if base is None else math.log(x, base)


# ANY-maze's maths: angles in degrees and Log in base 10. Used by the procedures that have "anymaze_maths" set
# (e.g. imported from ANY-maze protocols); the d-suffixed functions are available everywhere.
ANYMAZE_FUNCTIONS: dict[str, Callable] = {
    "sin": _deg(math.sin), "cos": _deg(math.cos), "tan": _deg(math.tan), "asin": _to_deg(math.asin),
    "acos": _to_deg(math.acos), "atan": _to_deg(math.atan), "atan2": _to_deg(math.atan2), "log": _log10_or_base,
}


# random numbers: the implementations take the evaluator's (seedable) generator first
RANDOM_FUNCTIONS: dict[str, Callable] = {
    "random": lambda rng: rng.random(), "uniform": lambda rng, a, b: rng.uniform(float(a), float(b)),
    "randint": _randint, "gauss": lambda rng, mu, sd: rng.gauss(float(mu), float(sd)), "choice": _choice,
    "shuffle": _shuffle,
}

# name: (min args, max args, implementation (None: in RANDOM_FUNCTIONS or an engine function), help)
FUNCTIONS: dict[str, tuple] = {
    "abs": (1, 1, abs, "absolute value"),
    "min": (1, 99, _minmax(min), "smallest of the values or of a list"),
    "max": (1, 99, _minmax(max), "largest of the values or of a list"),
    "round": (1, 2, _round, "round(x, decimals)"),
    "floor": (1, 1, math.floor, ""), "ceil": (1, 1, math.ceil, ""), "sqrt": (1, 1, math.sqrt, ""),
    "exp": (1, 1, math.exp, ""), "log": (1, 2, math.log, "natural log, or log(x, base)"),
    "log10": (1, 1, math.log10, ""), "sin": (1, 1, math.sin, ""), "cos": (1, 1, math.cos, ""),
    "tan": (1, 1, math.tan, ""), "asin": (1, 1, math.asin, ""), "acos": (1, 1, math.acos, ""),
    "atan": (1, 1, math.atan, ""), "atan2": (2, 2, math.atan2, ""), "hypot": (2, 2, math.hypot, ""),
    "degrees": (1, 1, math.degrees, ""), "radians": (1, 1, math.radians, ""),
    "sind": (1, 1, ANYMAZE_FUNCTIONS["sin"], "sine of an angle in degrees"),
    "cosd": (1, 1, ANYMAZE_FUNCTIONS["cos"], "cosine of an angle in degrees"),
    "tand": (1, 1, ANYMAZE_FUNCTIONS["tan"], "tangent of an angle in degrees"),
    "asind": (1, 1, ANYMAZE_FUNCTIONS["asin"], "arc sine in degrees"),
    "acosd": (1, 1, ANYMAZE_FUNCTIONS["acos"], "arc cosine in degrees"),
    "atand": (1, 1, ANYMAZE_FUNCTIONS["atan"], "arc tangent in degrees"),
    "atan2d": (2, 2, ANYMAZE_FUNCTIONS["atan2"], "atan2(y, x) in degrees"),
    "is_undefined": (1, 1, _undefined, "1 if the value is #N/A (NA), none or not a number"),
    "int": (1, 1, int, ""), "float": (1, 1, float, ""), "bool": (1, 1, bool, ""), "str": (1, 1, str, ""),
    "sign": (1, 1, lambda x: (x > 0) - (x < 0), "-1, 0 or 1"),
    "clamp": (3, 3, lambda x, lo, hi: max(lo, min(hi, x)), "clamp(x, low, high)"),
    "len": (1, 1, len, "length of a list or text"), "sum": (1, 1, lambda a: sum(_seq(a)), "sum of a list"),
    "mean": (1, 1, lambda a: _rmean(_seq(a)), "mean of a list"),
    "sorted": (1, 1, lambda a: sorted(_seq(a)), ""), "reversed": (1, 1, lambda a: _seq(a)[::-1], ""),
    "index": (2, 2, lambda a, v: _seq(a).index(v) if v in _seq(a) else -1, "position of a value (-1 if absent)"),
    "count": (2, 2, lambda a, v: _seq(a).count(v), "occurrences of a value in a list"),
    "array": (1, 2, _array, "array(n, fill=0): a list of n values"),
    "range": (1, 3, _range, "list of integers"),
    # random numbers (engine generator, seedable)
    "random": (0, 0, None, "uniform random number in [0, 1)"),
    "uniform": (2, 2, None, "uniform random number in [a, b]"),
    "randint": (2, 2, None, "random integer a..b (inclusive)"),
    "gauss": (2, 2, None, "normal random number (mean, sd)"),
    "choice": (1, 1, None, "random element of a list"),
    "shuffle": (1, 2, None, "shuffled copy of a list; shuffle(list, n): at most n equal values in a row"),
    # live test state
    "time": (0, 0, None, "test time (s)"),
    "zone": (1, 1, None, "1 if the animal's centre is in the zone"),
    "head_zone": (1, 1, None, "1 if the head is in the zone"),
    "zone_time": (1, 1, None, "total time in the zone so far (s)"),
    "zone_entries": (1, 1, None, "entries into the zone so far"),
    "detected": (0, 0, None, "1 if the animal is detected"),
    "freezing": (0, 0, None, "1 while freezing"), "immobile": (0, 0, None, "1 while immobile"),
    "speed": (0, 0, None, "current speed"), "distance": (0, 0, None, "distance travelled"),
    "x": (0, 0, None, "x position"), "y": (0, 0, None, "y position"),
    "input": (1, 2, None, "input([device,] channel): current input value"),
    "output": (1, 2, None, "output([device,] channel): current output value"),
    "analog": (1, 2, None, "analog([device,] channel)"), "encoder": (1, 2, None, "encoder([device,] channel)"),
    "activations": (1, 2, None, "activations([device,] channel): times the input switched on"),
    "pellets": (0, 2, None, "pellets dispensed (all, or of a [device,] channel)"),
    "timer": (1, 1, None, "timer value (s)"), "switch": (1, 1, None, "virtual switch state"),
    "key": (1, 1, None, "1 while the key is held down"),
    "responses": (1, 1, None, "responses registered on a schedule"),
    "reinforcers": (1, 1, None, "reinforcers earned on a schedule"),
    "requirement": (1, 1, None, "current requirement of a schedule"),
    "test_running": (0, 0, None, "1 while the test runs (not paused, not waiting to start)"),
    "test_paused": (0, 0, None, "1 while the test is paused"),
    "stage": (0, 0, None, "the test's stage"), "trial": (0, 0, None, "the test's trial number"),
    "apparatus": (0, 0, None, "the test's apparatus"),
    "treatment": (0, 0, None, "the animal's treatment group (its code when testing blind)"),
    "animal": (0, 0, None, "the animal's number (id)"),
    "animal_field": (1, 1, None, "animal_field('Sex'): a value from the animal's information"),
    "date": (0, 0, None, "today's date, 'YYYY-MM-DD'"),
    "time_of_day": (0, 0, None, "the clock time in seconds since midnight"),
    "freezing_time": (0, 0, None, "total freezing time so far (s)"),
    "immobile_time": (0, 0, None, "total immobile time so far (s)"),
    "zone_distance": (1, 1, None, "distance from the animal's centre to a zone (0 inside it)"),
    "point_distance": (1, 1, None, "distance from the animal's centre to a point"),
    "head_x": (0, 0, None, "head x position"), "head_y": (0, 0, None, "head y position"),
    "tail_x": (0, 0, None, "tail x position"), "tail_y": (0, 0, None, "tail y position"),
    "x_percent": (0, 0, None, "x position as % of the arena's width (0 = left)"),
    "y_percent": (0, 0, None, "y position as % of the arena's height (0 = top)"),
    "sequence_duration": (1, 1, None, "duration of the last completed run of an apparatus zone sequence (s)"),
    "speaker": (0, 1, None, "speaker([device]): 1 while a sound is playing"),
    "output_volts": (1, 2, None, "output_volts([device,] channel): analogue output level in volts"),
}

_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod, ast.Pow: op.pow}
_CMP = {ast.Eq: op.eq, ast.NotEq: op.ne, ast.Lt: op.lt, ast.LtE: op.le, ast.Gt: op.gt, ast.GtE: op.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_UNARY = {ast.USub: op.neg, ast.UAdd: op.pos, ast.Not: op.not_}
_cache: dict = {}


def _check_node(n):
    if isinstance(n, ast.Constant):
        if not isinstance(n.value, (int, float, str, bool, type(None))):
            raise ExprError("unsupported constant")
    elif isinstance(n, ast.Name):
        if n.id.startswith("_"):
            raise ExprError(f"invalid name '{n.id}'")
    elif isinstance(n, ast.BinOp):
        if type(n.op) not in _BIN:
            raise ExprError("unsupported operator")
        _check_node(n.left)
        _check_node(n.right)
    elif isinstance(n, ast.UnaryOp):
        if type(n.op) not in _UNARY:
            raise ExprError("unsupported operator")
        _check_node(n.operand)
    elif isinstance(n, ast.BoolOp):
        for v in n.values:
            _check_node(v)
    elif isinstance(n, ast.Compare):
        if any(type(o) not in _CMP for o in n.ops):
            raise ExprError("unsupported comparison")
        _check_node(n.left)
        for c in n.comparators:
            _check_node(c)
    elif isinstance(n, ast.IfExp):
        for c in (n.test, n.body, n.orelse):
            _check_node(c)
    elif isinstance(n, ast.Call):
        if not isinstance(n.func, ast.Name):
            raise ExprError("only functions like max(a, b) can be called")
        if n.keywords or any(isinstance(a, ast.Starred) for a in n.args):
            raise ExprError("keyword or * arguments are not supported")
        if n.func.id not in FUNCTIONS:
            raise ExprError(f"unknown function '{n.func.id}'")
        lo, hi = FUNCTIONS[n.func.id][:2]
        if not lo <= len(n.args) <= hi:
            want = f"{lo}" if lo == hi else f"{lo}–{hi}"
            raise ExprError(f"{n.func.id}() takes {want} argument(s), got {len(n.args)}")
        for a in n.args:
            _check_node(a)
    elif isinstance(n, ast.Subscript):
        _check_node(n.value)
        _check_node(n.slice)
    elif isinstance(n, ast.Slice):
        for c in (n.lower, n.upper, n.step):
            if c is not None:
                _check_node(c)
    elif isinstance(n, (ast.List, ast.Tuple)):
        if len(n.elts) > 10000:
            raise ExprError("list too long")
        for c in n.elts:
            _check_node(c)
    else:
        raise ExprError(f"'{type(n).__name__}' is not allowed in expressions")


# ANY-maze's undefined value; outside quoted text it reads as the constant NA
_NA = re.compile(r"""('[^']*'|"[^"]*")|#N/A""")


def compile_expr(src):
    """Parse and check an expression; returns an AST node (cached). Raises ExprError."""
    if isinstance(src, bool) or isinstance(src, (int, float)):
        return ast.Constant(src)
    if isinstance(src, list):
        return ast.Constant(None) if not src else ast.List([compile_expr(x) for x in src], ast.Load())
    s = "" if src is None else str(src).strip()
    if "#N/A" in s:
        s = _NA.sub(lambda m: m.group(1) or "NA", s)
    hit = _cache.get(s)
    if hit is not None:
        if isinstance(hit, ExprError):
            raise hit
        return hit
    try:
        if not s:
            raise ExprError("empty expression")
        if len(s) > MAX_EXPR_LEN:
            raise ExprError("expression too long")
        try:
            tree = ast.parse(s, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"syntax error: {e.msg}") from None
        except (ValueError, RecursionError, MemoryError):
            raise ExprError("invalid expression") from None
        try:
            _check_node(tree.body)
        except RecursionError:
            raise ExprError("expression too deeply nested") from None
        node = tree.body
    except ExprError as e:
        if len(_cache) < 5000:
            _cache[s] = e
        raise
    if len(_cache) < 5000:
        _cache[s] = node
    return node


def expr_names(src) -> set[str]:
    """Variable names read by an expression (empty on error)."""
    try:
        node = compile_expr(src)
    except ExprError:
        return set()
    funcs = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and id(n) not in funcs}


def check_expr(src, names=None) -> list[str]:
    """Edit-time check of an expression: syntax, allowed constructs, functions, and (if given) variable names."""
    try:
        compile_expr(src)
    except ExprError as e:
        return [str(e)]
    if names is None:
        return []
    unknown = sorted(n for n in expr_names(src) if n not in names and n not in CONSTANTS)
    return [f"unknown variable '{n}'" for n in unknown]


def _limit(v):
    if isinstance(v, (str, list)) and len(v) > MAX_SEQ:
        raise ExprError("result too long")
    if isinstance(v, int) and not isinstance(v, bool) and v.bit_length() > 1024:
        raise ExprError("number too large")
    return v


class Evaluator:
    """Safe evaluation of a checked AST. lookup(name) -> value (raise ExprError if unknown);
    call(name, args) -> value for engine functions. anymaze: ANY-maze's maths (ANYMAZE_FUNCTIONS: degrees, Log in
    base 10)."""

    def __init__(self, lookup: Callable, call: Callable | None = None, rng: random.Random | None = None,
                 anymaze: bool = False):
        self.lookup = lookup
        self.call_engine = call
        self.rng = rng or random.Random()
        self.anymaze = anymaze

    def eval(self, src):
        return self._ev(compile_expr(src))

    def _ev(self, n):
        t = type(n)
        if t is ast.Constant:
            return n.value
        if t is ast.Name:
            if n.id in CONSTANTS:
                try:
                    return self.lookup(n.id)
                except ExprError:
                    return CONSTANTS[n.id]
            return self.lookup(n.id)
        if t is ast.BinOp:
            a, b = self._ev(n.left), self._ev(n.right)
            return _limit(self._binop(type(n.op), a, b))
        if t is ast.UnaryOp:
            try:
                return _UNARY[type(n.op)](self._ev(n.operand))
            except TypeError as e:
                raise ExprError(str(e)) from None
        if t is ast.BoolOp:
            if isinstance(n.op, ast.And):
                v = True
                for x in n.values:
                    v = self._ev(x)
                    if not v:
                        return v
                return v
            v = False
            for x in n.values:
                v = self._ev(x)
                if v:
                    return v
            return v
        if t is ast.Compare:
            left = self._ev(n.left)
            for o, c in zip(n.ops, n.comparators):
                right = self._ev(c)
                try:
                    if not _CMP[type(o)](left, right):
                        return False
                except TypeError as e:
                    raise ExprError(f"cannot compare: {e}") from None
                left = right
            return True
        if t is ast.IfExp:
            return self._ev(n.body) if self._ev(n.test) else self._ev(n.orelse)
        if t is ast.Call:
            name = n.func.id
            args = [self._ev(a) for a in n.args]
            return _limit(self._call(name, args))
        if t is ast.Subscript:
            v = self._ev(n.value)
            if not isinstance(v, (list, tuple, str)):
                raise ExprError("only lists and text can be indexed")
            if isinstance(n.slice, ast.Slice):
                s = n.slice
                lo, hi, st = (None if x is None else int(self._ev(x)) for x in (s.lower, s.upper, s.step))
                if st == 0:
                    raise ExprError("slice step cannot be zero")
                return v[lo:hi:st]
            i = self._ev(n.slice)
            if isinstance(i, float) and i.is_integer():
                i = int(i)
            if not isinstance(i, int):
                raise ExprError("index must be an integer")
            try:
                return v[i]
            except IndexError:
                raise ExprError(f"index {i} out of range (length {len(v)})") from None
        if t is ast.List or t is ast.Tuple:
            return [self._ev(x) for x in n.elts]
        raise ExprError(f"'{t.__name__}' is not allowed")

    def _binop(self, o, a, b):
        try:
            if o is ast.Pow:
                if isinstance(b, (int, float)) and abs(b) > 1000 and isinstance(a, (int, float)) and abs(a) > 1:
                    raise ExprError("exponent too large")
                if isinstance(a, int) and isinstance(b, int) and b > 0 and a.bit_length() * b > 4096:
                    raise ExprError("number too large")
            if o is ast.Mult:
                for s_, n_ in ((a, b), (b, a)):
                    if isinstance(s_, (str, list)) and isinstance(n_, int) and len(s_) * n_ > MAX_SEQ:
                        raise ExprError("result too long")
            if o is ast.Add and isinstance(a, list) and isinstance(b, list):
                return a + b
            r = _BIN[o](a, b)
            if isinstance(r, complex):
                raise ExprError("complex result")
            return r
        except ZeroDivisionError:
            raise ExprError("division by zero") from None
        except OverflowError:
            raise ExprError("numeric overflow") from None
        except TypeError as e:
            raise ExprError(f"invalid operands: {e}") from None

    def _call(self, name, args):
        fn = ANYMAZE_FUNCTIONS.get(name) if self.anymaze else None
        fn = fn or FUNCTIONS[name][2]
        try:
            if fn is not None:
                return fn(*args)
            if name in RANDOM_FUNCTIONS:
                return RANDOM_FUNCTIONS[name](self.rng, *args)
            if self.call_engine is None:
                raise ExprError(f"{name}() is only available during a test")
            return self.call_engine(name, args)
        except ExprError:
            raise
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as e:
            raise ExprError(f"{name}(): {e}") from None


_INTERP = re.compile(r"\{([^{}]+)\}")


def _fmt(v) -> str:
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def interpolate(text: str, evaluate: Callable) -> str:
    """Replace {expr} in text with the value of expr ({{ and }} give literal braces)."""
    s = str(text or "")
    if "{" not in s:
        return s
    s = s.replace("{{", "\x00").replace("}}", "\x01")
    s = _INTERP.sub(lambda m: _fmt(evaluate(m.group(1))), s)
    return s.replace("\x00", "{").replace("\x01", "}")

