"""Procedure documents: normalising, creating, describing and walking statements."""

from __future__ import annotations

import copy
import re

from .catalog import ACTION_SPECS, EVENT_SPECS, KEEP_SCOPES, STOP_WHAT, WHEN_MODES, P
from .expr import _fmt
from .legacy import convert_rule, describe_rule, is_legacy_rule


def normalize_procedures(procs) -> list[dict]:
    """Convert old rules and fill defaults; returns a new list (the input is not modified)."""
    out = []
    for p in procs or []:
        if is_legacy_rule(p):
            out.append(convert_rule(p))
        elif isinstance(p, dict):
            q = copy.deepcopy(p)
            q.setdefault("name", f"Procedure {len(out) + 1}")
            q.setdefault("enabled", True)
            q.setdefault("statements", [])
            out.append(q)
        else:  # keep indices aligned with the input
            out.append({"name": f"Procedure {len(out) + 1}", "enabled": False, "statements": [], "invalid": True})
    return out


def new_procedure(name: str = "Procedure") -> dict:
    return {"name": name, "enabled": True, "statements": []}


def new_statement(type_: str) -> dict:
    """A statement of this type with sensible defaults (used by the editor)."""
    d: dict = {"type": type_}
    if type_ == "when":
        d.update(event="test_start", mode="ignore", body=[])
    elif type_ == "wait":
        d.update(mode="seconds", seconds=1)
    elif type_ == "if":
        d.update(cond="", body=[])
    elif type_ == "repeat":
        d.update(mode="count", count=3, body=[])
    elif type_ == "set":
        d.update(var="", value="0")
    elif type_ == "do":
        d.update(action="log", text="")
    elif type_ == "stop":
        d.update(what="handler")
    elif type_ == "comment":
        d.update(text="")
    elif type_ == "var":
        d.update(name="", value=0, keep=False, result=False)
    elif type_ == "call":
        d.update(procedure="")
    elif type_ == "label":
        d.update(name="")
    elif type_ == "goto":
        d.update(label="")
    elif type_ == "resolution":
        d.update(ms=1)
    return d


def new_elif() -> dict:
    """An "else if" clause of an If statement (``st["elif"]``)."""
    return {"cond": "", "body": []}


def spec_defaults(spec: dict) -> dict:
    return {p["name"]: p["default"] for p in spec["params"] if p["default"] is not None}


def _short(v) -> str:
    s = _fmt(v) if not isinstance(v, str) else v
    return s if len(s) <= 40 else s[:37] + "…"


_NAME_TYPES = ("zone", "var", "timer", "area", "switch", "schedule", "name", "procedure", "sequence")


def _params_text(spec: dict, st: dict) -> str:
    bits = []
    for p in spec.get("params", []):
        typ = p["type"]
        v = st.get(p["name"], p["default"])
        if v is None or v == "" or typ in ("device", "audio"):
            continue
        if typ == "bool":
            if v and str(v).lower() not in ("false", "0", "no"):
                bits.append(p["label"].lower())
        elif typ in ("point", "plugin", "clock"):
            bits.append(_short(v))
        elif typ in ("input", "output"):
            dev = st.get("device")
            bits.append(f"{dev}/{v}" if dev else str(v))
        elif typ in _NAME_TYPES:
            bits.append(_short(v))
        elif typ in ("text", "file", "key", "spec"):
            bits.append(f"“{_short(v)}”")
        else:
            m = re.match(r"(.*?)\s*\((s|ms|Hz)\)$", p["label"])
            name, unit = (m.group(1), " " + m.group(2)) if m else (p["label"].split(" (")[0], "")
            bits.append(f"{name.lower()} {_short(v)}{unit}")
    return ", ".join(bits)


def describe_event(st: dict) -> str:
    spec = EVENT_SPECS.get(st.get("event", ""), None)
    if spec is None:
        return f"unknown event '{st.get('event')}'"
    p = _params_text(spec, st)
    return f"{spec['label'].lower()}" + (f" ({p})" if p else "")


def describe_statement(st: dict) -> str:
    """One line summary of a statement, for the editor tree."""
    t = st.get("type")
    if t == "when":
        extra = []
        if st.get("once"):
            extra.append("once")
        if st.get("mode", "ignore") != "ignore":
            extra.append(WHEN_MODES.get(st.get("mode"), st.get("mode", "")).lower())
        if st.get("times") not in (None, "", 0, 1):
            extra.append(f"{st['times']} times" + (f" within {st['within']} s" if st.get("within") not in (None, "")
                                                    else ""))
        if st.get("trials") not in (None, ""):
            extra.append(f"trials {st['trials']}")
        if str(st.get("record_as") or "").strip():
            extra.append(f"recorded as “{str(st['record_as']).strip()}”")
        return f"When {describe_event(st)}" + (f"  [{', '.join(extra)}]" if extra else "")
    if t == "wait":
        mode = wait_mode(st)
        to = f" (timeout {_short(st['timeout'])} s)" if st.get("timeout") not in (None, "", 0) else ""
        if mode == "until":
            return f"Wait until {_short(st.get('until', ''))}{to}"
        if mode == "event":
            alts = "".join(f" or {describe_event(a)}" for a in wait_alternatives(st))
            return f"Wait for {describe_event(st)}{alts}{to}"
        return f"Wait {_short(st.get('seconds', 0))} s"
    if t == "if":
        return f"If {_short(st.get('cond', '')) or '?'}"
    if t == "repeat":
        mode = repeat_mode(st)
        v = f" [{st['var']}]" if st.get("var") else ""
        if mode == "count":
            return f"Repeat {_short(st.get('count', 0))} times{v}"
        if mode == "while":
            return f"Repeat while {_short(st.get('while', ''))}{v}"
        if mode == "until":
            return f"Repeat until {_short(st.get('until', ''))}{v}"
        return f"Repeat forever{v}"
    if t == "set":
        idx = f"[{st['index']}]" if st.get("index") not in (None, "") else ""
        return f"Set {st.get('var', '?')}{idx} = {_short(st.get('value', ''))}"
    if t == "do":
        spec = ACTION_SPECS.get(st.get("action", ""))
        if spec is None:
            return f"Do: unknown action '{st.get('action')}'"
        p = _params_text(spec, st)
        return f"Do: {spec['label']}" + (f" — {p}" if p else "")
    if t == "stop":
        return STOP_WHAT.get(st.get("what", "handler"), "Stop")
    if t == "comment":
        return f"# {st.get('text', '')}"
    if t == "call":
        return f"Call sub-procedure {st.get('procedure') or '?'}"
    if t == "label":
        return f"Label {st.get('name') or '?'}"
    if t == "goto":
        return f"Go to {st.get('label') or '?'}"
    if t == "resolution":
        return f"Set timer resolution {_short(st.get('ms', 1))} ms"
    if t == "var":
        flags = [f for f, k in (("kept between tests", "keep"), ("saved as result", "result")) if st.get(k)]
        if keep_scope(st) in ("animal", "apparatus"):
            flags[0] = f"kept {KEEP_SCOPES[keep_scope(st)].lower()}"
        if record_mode(st) != "end":
            flags.append(f"recorded {RECORD_MODES[record_mode(st)].lower()}")
        return f"Variable {st.get('name', '?')} = {_short(st.get('value', 0))}" + (f"  ({', '.join(flags)})"
                                                                                   if flags else "")
    return f"Unknown statement '{t}'"


def describe_elif(clause: dict) -> str:
    return f"Else if {_short(clause.get('cond', '')) or '?'}"


def keep_scope(st) -> str | None:
    """How a variable is kept between tests: None (not kept), "experiment" (old projects: keep = true), "animal"
    or "apparatus"."""
    k = st.get("keep")
    if not k:
        return None
    return k if isinstance(k, str) and k in KEEP_SCOPES else "experiment"


def wait_alternatives(st) -> list[dict]:
    """The other events a "wait for an event" also ends on (``st["or"]``: [{"event", <parameters>}])."""
    alts = st.get("or")
    return [a for a in alts if isinstance(a, dict)] if isinstance(alts, list) else []


def describe(rule: dict) -> str:
    """Summary of a legacy rule, a procedure or a statement."""
    if is_legacy_rule(rule):
        return describe_rule(rule)
    if isinstance(rule, dict) and "statements" in rule:
        n = len(rule.get("statements") or [])
        return f"{rule.get('name', 'Procedure')} ({n} statement{'s' if n != 1 else ''})"
    return describe_statement(rule) if isinstance(rule, dict) else str(rule)


def wait_mode(st):
    m = st.get("mode")
    if m in ("seconds", "until", "event"):
        return m
    if st.get("event"):
        return "event"
    if st.get("until") not in (None, ""):
        return "until"
    return "seconds"


# how a variable's values are recorded for the results ("record" in a var statement; old projects: "end")
RECORD_MODES = {"end": "Final value only", "changes": "Every time it changes", "set": "Every time it is set"}


def record_mode(st) -> str:
    """How a variable is recorded: "end" (only its final value, with "result"), "changes" (time-stamped every time
    its value changes) or "set" (every time a statement sets it, even to the same value)."""
    m = st.get("record")
    return m if m in RECORD_MODES else "end"


def repeat_mode(st):
    m = st.get("mode")
    if m in ("count", "while", "until", "forever"):
        return m
    if st.get("while") not in (None, ""):
        return "while"
    if st.get("count") not in (None, ""):
        return "count"
    return "forever"


_SECONDS = P("seconds", "number", None, "Seconds", True)
_COND = P("cond", "expr", None, "Condition", True)
_UNTIL = P("until", "expr", None, "Condition", True)
_COUNT = P("count", "int", None, "Count", True)
_WHILE = P("while", "expr", None, "Condition", True)
_VALUE = P("value", "expr", None, "Value", True)
_STATEMENT_FIELDS = {
    "if": [_COND], "set": [_VALUE],
    "call": [P("procedure", "procedure", "", "Sub-procedure", True)],
    "label": [P("name", "label", "", "Label", True)],
    "goto": [P("label", "label", "", "Go to label", True)],
    "resolution": [P("ms", "number", 1, "Resolution (ms)", True,
                     "accepted for ANY-maze protocols: waits and timers are already exact to the frame")],
}


def statement_fields(st: dict) -> list[dict]:
    """Parameter specs (see P) of a statement's main fields: its event's or action's parameters, the seconds,
    condition or count of a wait / if / repeat, the value of a set. Other fields (timeout, index, var, ...) are not
    included."""
    t = st.get("type")
    if t == "when" or (t == "wait" and wait_mode(st) == "event"):
        return list(EVENT_SPECS.get(st.get("event"), {}).get("params", []))
    if t == "do":
        return list(ACTION_SPECS.get(st.get("action"), {}).get("params", []))
    if t == "wait":
        return [_SECONDS if wait_mode(st) == "seconds" else _UNTIL]
    if t == "repeat":
        return {"count": [_COUNT], "while": [_WHILE], "until": [_UNTIL]}.get(repeat_mode(st), [])
    return list(_STATEMENT_FIELDS.get(t, []))


def iter_statements(stmts, path=()):
    """Yield (path, statement) for a statement list and all nested blocks."""
    for i, st in enumerate(stmts or []):
        if not isinstance(st, dict):
            continue
        p = path + (i,)
        yield p, st
        if isinstance(st.get("body"), list):
            yield from iter_statements(st["body"], p + ("body",))
        for k, clause in enumerate(st.get("elif") or [] if isinstance(st.get("elif"), list) else []):
            if isinstance(clause, dict) and isinstance(clause.get("body"), list):
                yield from iter_statements(clause["body"], p + ("elif", k, "body"))
        if isinstance(st.get("else"), list):
            yield from iter_statements(st["else"], p + ("else",))


def statement_at(proc: dict, path: tuple) -> dict | None:
    """The statement at a path (for the path of an If followed by ("elif", k): its k-th else-if clause)."""
    cur = proc.get("statements", [])
    st = None
    for k in path:
        if isinstance(k, int):
            if not isinstance(cur, list) or not 0 <= k < len(cur):
                return None
            st = cur[k]
        else:
            cur = st.get(k) if isinstance(st, dict) else None
    return st


def is_branch(path) -> bool:
    """The path of an Else branch (``(..., "else")``) or of an else-if clause (``(..., "elif", k)``), not of a
    statement."""
    return bool(path) and (path[-1] == "else" or (len(path) >= 2 and path[-2] == "elif"))


def branch_block(path: tuple) -> tuple:
    """The block path of the statements inside a branch (see is_branch)."""
    return path if path[-1] == "else" else path + ("body",)


def path_text(path: tuple) -> str:
    """(0, 'body', 2, 'else', 0) -> '1.3.else.1', (0, 'elif', 1, 'body', 0) -> '1.elif.2.body.1' (1-based for
    people)."""
    return ".".join(str(k + 1) if isinstance(k, int) else k for k in path)

