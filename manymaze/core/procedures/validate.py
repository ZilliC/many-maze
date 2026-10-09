"""Edit-time validation of procedures."""

from __future__ import annotations

import keyword
import re

from .. import ioconfig
from ..operant import parse_spec
from .catalog import (ACTION_SPECS, CONSTANTS, EVENT_SPECS, KEEP_SCOPES, LOCAL_NAMES, STATEMENT_TYPES, STOP_WHAT,
                      WHEN_MODES)
from .expr import _INTERP, check_expr
from .model import iter_statements, normalize_procedures, path_text, statement_fields, wait_alternatives, wait_mode


def _names(lst) -> list[str]:
    out = []
    for z in lst or []:
        n = z if isinstance(z, str) else (z.get("name") if isinstance(z, dict) else getattr(z, "name", None))
        if n:
            out.append(str(n))
    return out


def _context(context) -> dict:
    c = dict(context or {})
    devs = c.get("devices")
    channels: dict[str, dict[str, str]] = {}
    if devs and isinstance(devs[0], dict):
        for d in devs:
            channels[d.get("name", "")] = {ch.get("name"): ch.get("kind", "input") for ch in d.get("channels", []) or []
                                           if ch.get("name")}
    return {"zones": set(_names(c.get("zones"))) if c.get("zones") is not None else None,
            "points": set(_names(c.get("points"))) if c.get("points") is not None else None,
            "devices": set(_names(devs)) if devs is not None else None,
            "channels": channels or None,
            "areas": set(_names(c.get("areas"))) if c.get("areas") else None}


def project_context(project) -> dict:
    """The validation context of a project: the zones and groups of all its apparatus, its I/O devices (None when
    there are none: device names are then not checked) and its touch-screen areas."""
    zones = [n for a in project.apparatus for n in a.names()]
    areas = [a.get("name") for a in (project.settings_extra.get("touchscreen", {}) or {}).get("areas", [])
             if a.get("name")]
    points = [p.name for a in project.apparatus for p in getattr(a, "points", [])]
    ctx = {"zones": sorted(set(zones)) if zones else None, "devices": list(project.io_devices) or None}
    if points:
        ctx["points"] = sorted(set(points))
    if areas:
        ctx["areas"] = areas
    return ctx


def test_context(project, test) -> dict:
    """What the procedures know about the test they run in (``context["test"]`` of the engine: the stage(),
    trial(), apparatus(), treatment(), animal() and animal_field() functions, and the per-animal / per-apparatus
    kept variables)."""
    a = project.get_animal(test.animal_id)
    group = a.group if a else ""
    return {"test": test.id, "animal": test.animal_id, "apparatus": test.apparatus, "stage": test.stage,
            "trial": test.trial, "treatment": project.treatment_code(group) if project.blind else group,
            "fields": dict(a.fields, Sex=a.sex) if a else {}}


def declared_names(procedures) -> set[str]:
    """Variables declared or assigned anywhere (they are global), plus loop and event locals."""
    names = set(LOCAL_NAMES)
    for p in normalize_procedures(procedures):
        for _path, st in iter_statements(p.get("statements")):
            t = st.get("type")
            if t == "var" and st.get("name"):
                names.add(str(st["name"]))
            elif t == "set" and st.get("var"):
                names.add(str(st["var"]))
            elif t == "repeat" and st.get("var"):
                names.add(str(st["var"]))
            elif t == "do" and st.get("action") in ("set_variable", "increment", "decrement", "array_append",
                                                     "schedule_response") and st.get("var"):
                names.add(str(st["var"]))
    return names


_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _bad_var_name(n) -> str | None:
    n = str(n or "")
    if not n:
        return "a variable name is required"
    if not _IDENT.match(n) or keyword.iskeyword(n):
        return f"'{n}' is not a valid name (letters, digits and _ ; start with a letter)"
    if n in CONSTANTS:
        return f"'{n}' is a reserved name"
    return None


class ValidationWarning(str):
    """A validation message that is a warning, not an error: the procedures run as written (e.g. "Run a program",
    or a loop with nothing in it). It is a str (the message); :func:`is_warning` tells them apart."""


def is_warning(message) -> bool:
    return isinstance(message, ValidationWarning)


# number parameters that may be negative (a value, a step, a level or a temperature); the others (times, counts,
# rates, sizes …) must not be
_NEGATIVE_OK = {"value", "by", "threshold", "target"}
_NUMBER = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$")


def _a(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"


def _check_param(p, v, names, ctx, st) -> list[str]:
    typ, label = p["type"], p["label"]
    empty = v is None or (isinstance(v, str) and not v.strip())
    if empty:
        return [f"{label} is required"] if p.get("req") else []
    errs = []
    if typ in ("number", "int"):
        errs = check_expr(v, names)
        lit = v if isinstance(v, (int, float)) and not isinstance(v, bool) else \
            float(v) if isinstance(v, str) and _NUMBER.match(v) else None
        if not errs and lit is not None and lit < 0 and p["name"] not in _NEGATIVE_OK:
            errs = ["must not be negative"]
    elif typ == "expr":
        errs = check_expr(v, names)
    elif typ == "text":
        for m in _INTERP.finditer(str(v).replace("{{", "").replace("}}", "")):
            errs += check_expr(m.group(1), names)
    elif typ == "zone":
        if ctx["zones"] is not None and str(v) not in ctx["zones"]:
            errs = [f"unknown zone '{v}'"]
    elif typ == "point":  # a zone or a point
        if ctx["zones"] is not None and str(v) not in ctx["zones"] | (ctx.get("points") or set()):
            errs = [f"unknown zone or point '{v}'"]
    elif typ == "clock":
        from .live_state import parse_clock

        if parse_clock(v) is None:
            errs = [f"'{v}' is not a time of day (HH:MM or HH:MM:SS)"]
    elif typ == "plugin":
        from . import plugins

        if plugins.names() and str(v) not in plugins.names():
            errs = [f"unknown plug-in '{v}' (installed: {', '.join(plugins.names())})"]
    elif typ == "sequence":
        zs = [z.strip() for z in str(v).split(",") if z.strip()]
        if len(zs) < 2:
            errs = ["give at least two zones separated by commas"]
        elif ctx["zones"] is not None:
            errs = [f"unknown zone '{z}'" for z in zs if z not in ctx["zones"]]
    elif typ in ("device", "audio"):
        if ctx["devices"] is not None and str(v) not in ctx["devices"]:
            errs = [f"unknown device '{v}'"]
    elif typ in ("input", "output", "sensor", "thermostat", "odour", "pump"):
        chans = ctx["channels"]
        what = {"thermostat": "temperature controller", "odour": "olfactometer"}.get(typ, typ)
        if chans is not None:
            dev = st.get("device") or ""
            pool = chans.get(dev, {}) if dev else {k: kd for d in chans.values() for k, kd in d.items()}
            if dev and dev not in chans:
                pool = None
            if pool is not None and str(v) not in pool:
                errs = [f"unknown {what} channel '{v}'"]
            elif pool is not None:
                kd = pool[str(v)]
                if kd not in ioconfig.channel_kinds_of(typ):
                    errs = [f"'{v}' is {_a(kd)} {kd} channel, not {_a(what)} {what}"]
    elif typ == "var":
        e = _bad_var_name(v)
        errs = [e] if e else []
    elif typ == "spec":
        try:
            parse_spec(v)
        except ValueError as e:
            errs = [str(e)]
    elif typ == "area":
        if ctx["areas"] is not None and str(v) not in ctx["areas"]:
            errs = [f"unknown touch-screen area '{v}'"]
    elif typ.startswith("choice:"):
        if str(v) not in typ[7:].split("|"):
            errs = [f"must be one of {typ[7:].replace('|', ', ')}"]
    return [f"{label}: {e}" for e in errs]


def _check_when_options(st) -> list[str]:
    """The event-wizard options of a "when": times (≥ 1) within seconds (> 0), trials ("1, 3-5, odd")."""
    from .live_state import parse_trials

    errs = []
    times, within = st.get("times"), st.get("within")
    if times not in (None, ""):
        try:
            if int(times) < 1 or float(times) != int(times):
                raise ValueError
        except (TypeError, ValueError):
            errs.append("“times”: give a whole number of at least 1")
    if within not in (None, ""):
        try:
            if float(within) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errs.append("“within”: give a time in seconds above 0")
        if times in (None, ""):
            errs.append("“within” needs a number of times")
    if st.get("trials") not in (None, "") and parse_trials(st["trials"]) is None:
        errs.append(f"trials: '{st['trials']}' is not a list of trials (e.g. 1, 3-5, odd)")
    return errs


def _runs_something(stmts) -> bool:
    """Whether a block has a statement that does something (not only comments, labels and disabled statements)."""
    return any(isinstance(st, dict) and st.get("enabled", True) is not False
               and st.get("type") not in ("comment", "label", "var", "when", None) for st in stmts or [])


def validate(procedures, context=None, warnings: bool = True) -> list[tuple[int, tuple, str]]:
    """Edit-time check. Returns [(procedure index, statement path, message)]; path () = the procedure itself.
    Messages that are warnings (:func:`is_warning`: "Run a program" runs a command on this computer, a loop with
    nothing in it) are included unless ``warnings`` is False; the others are errors.

    context: {"zones": [...], "devices": [names or io_devices configs], "areas": [...]} — optional; names are
    only checked against the lists that are given."""
    procs = normalize_procedures(procedures)
    ctx = _context(context)
    names = declared_names(procs)
    issues: list[tuple[int, tuple, str]] = []
    seen_vars: dict[str, int] = {}
    proc_names = [p.get("name") for p in procs]
    subs = {p.get("name") for p in procs if p.get("sub")}

    def block(pi, stmts, path, top, in_loop, visible=(), event=None):
        """visible: the label names of the enclosing blocks of the same thread (a Go to may jump to a label of its
        own block or of an enclosing one, not into a nested block); event: the When handler's event."""
        if not isinstance(stmts, list):
            issues.append((pi, path, "statements must be a list"))
            return
        here = {str(s.get("name") or "") for s in stmts if isinstance(s, dict) and s.get("type") == "label"}
        visible = visible + (here,)
        for i, st in enumerate(stmts):
            p = path + (i,)
            if not isinstance(st, dict):
                issues.append((pi, p, "invalid statement"))
                continue
            t = st.get("type")
            lab = STATEMENT_TYPES.get(t, str(t))

            def err(msg, p=p, lab=lab):
                issues.append((pi, p, f"{lab}: {msg}"))

            if t not in STATEMENT_TYPES:
                err(f"unknown statement type '{t}'")
                continue
            if t in ("when", "var") and not top:
                err("only allowed at the top level of a procedure")
            if t == "when" and procs[pi].get("sub"):
                err("a sub-procedure has no When handlers (it runs when it is called)")

            def check(fields, report=err):
                for prm in fields:
                    for m in _check_param(prm, st.get(prm["name"], prm["default"]), names, ctx, st):
                        report(m)

            if t == "when":
                if st.get("event") not in EVENT_SPECS:
                    err(f"unknown event '{st.get('event')}'")
                check(statement_fields(st))
                if st.get("mode", "ignore") not in WHEN_MODES:
                    err(f"unknown mode '{st.get('mode')}'")
                for m in _check_when_options(st):
                    err(m)
                block(pi, st.get("body", []), p + ("body",), False, False, (), st.get("event"))
            elif t == "var":
                e = _bad_var_name(st.get("name"))
                if e:
                    err(e)
                else:
                    n = st["name"]
                    if n in seen_vars:
                        err(f"'{n}' is declared more than once")
                    seen_vars[n] = pi
                for m in check_expr(st.get("value", 0), names):
                    err(f"initial value: {m}")
                if st.get("keep") not in (None, False, True, 0, 1) and str(st.get("keep")) not in KEEP_SCOPES:
                    err(f"unknown keep option '{st.get('keep')}'")
            elif t == "wait":
                ev = st.get("event")
                if wait_mode(st) == "event" and ev not in EVENT_SPECS:
                    err(f"unknown event '{ev}'")
                elif wait_mode(st) == "event" and ev in ("test_start", "test_waiting"):
                    err("cannot wait for the test to start")
                else:
                    check(statement_fields(st))
                if wait_mode(st) == "event":
                    for k, alt in enumerate(wait_alternatives(st)):
                        a_ev = alt.get("event")

                        def err3(msg, k=k):
                            err(f"or event {k + 1}: {msg}")
                        if a_ev not in EVENT_SPECS:
                            err3(f"unknown event '{a_ev}'")
                        elif a_ev in ("test_start", "test_waiting"):
                            err3("cannot wait for the test to start")
                        else:
                            for prm in EVENT_SPECS[a_ev]["params"]:
                                for m in _check_param(prm, alt.get(prm["name"], prm["default"]), names, ctx, alt):
                                    err3(m)
                if st.get("timeout") not in (None, "", 0):
                    for m in check_expr(st["timeout"], names):
                        err(f"timeout: {m}")
            elif t == "if":
                check(statement_fields(st))
                block(pi, st.get("body", []), p + ("body",), False, in_loop, visible, event)
                clauses = st.get("elif", [])
                if not isinstance(clauses, list):
                    err("the else-if clauses must be a list")
                    clauses = []
                for k, clause in enumerate(clauses):
                    cp = p + ("elif", k)
                    if not isinstance(clause, dict):
                        issues.append((pi, cp, "Else if: invalid clause"))
                        continue
                    for m in _check_param(statement_fields({"type": "if"})[0], clause.get("cond"), names, ctx, clause):
                        issues.append((pi, cp, f"Else if: {m}"))
                    block(pi, clause.get("body", []), cp + ("body",), False, in_loop, visible, event)
                if "else" in st:
                    block(pi, st.get("else") or [], p + ("else",), False, in_loop, visible, event)
            elif t == "repeat":
                check(statement_fields(st))
                if st.get("var"):
                    e = _bad_var_name(st["var"])
                    if e:
                        err(e)
                if warnings and not _runs_something(st.get("body")) and isinstance(st.get("body", []), list):
                    issues.append((pi, p, ValidationWarning(f"{lab}: warning: the loop has nothing to do (it only "
                                                            "uses up time, one check per frame)")))
                block(pi, st.get("body", []), p + ("body",), False, True, visible, event)
            elif t in ("call", "label", "goto", "resolution"):
                check(statement_fields(st))
                if t == "call" and st.get("procedure") and st["procedure"] not in subs:
                    err(f"'{st['procedure']}' is not a sub-procedure" if st["procedure"] in proc_names
                        else f"unknown procedure '{st['procedure']}'")
                elif t == "label" and st.get("name") and labels[pi].count(str(st["name"])) > 1:
                    err(f"another label is also called '{st['name']}'")
                elif t == "goto" and st.get("label") and not any(str(st["label"]) in v for v in visible):
                    err(f"no label '{st['label']}' in this block or a block around it" if str(st["label"])
                        in labels[pi] else f"unknown label '{st['label']}'")
            elif t == "set":
                e = _bad_var_name(st.get("var"))
                if e:
                    err(e)
                check(statement_fields(st))
                if st.get("index") not in (None, ""):
                    for m in check_expr(st["index"], names):
                        err(f"index: {m}")
            elif t == "do":
                a = st.get("action")
                spec = ACTION_SPECS.get(a)
                if spec is None:
                    err(f"unknown action '{a}'")
                else:
                    lab2 = f"Do {spec['label'].lower()}"

                    def err2(msg, p=p, lab2=lab2):
                        issues.append((pi, p, f"{lab2}: {msg}"))

                    check(statement_fields(st), err2)
                    if a in ("enable_procedure", "disable_procedure") and st.get("procedure") \
                            and st["procedure"] not in proc_names:
                        err2(f"unknown procedure '{st['procedure']}'")
                    if a == "run_program" and warnings:
                        issues.append((pi, p, ValidationWarning(f"{lab2}: warning: runs a program on this computer — "
                                                                "only if it is allowed on this computer")))
                    if a == "run_subprocedure" and st.get("procedure") and st["procedure"] not in subs:
                        err2(f"'{st['procedure']}' is not a sub-procedure" if st["procedure"] in proc_names
                             else f"unknown procedure '{st['procedure']}'")
                    if a in ("prevent_test_start", "allow_test_start") and event != "test_waiting" \
                            and not procs[pi].get("sub"):
                        err2("only in a “test is waiting to start” handler")
                    if a == "schedule_response" and not st.get("spec"):
                        started = any(s2.get("action") == "schedule_start" and s2.get("schedule") == st.get("schedule")
                                      for q in procs for _pp, s2 in iter_statements(q.get("statements")))
                        if not started:
                            err2("give a schedule (e.g. FR 5) or start it first")
            elif t == "stop":
                w = st.get("what", "handler")
                if w not in STOP_WHAT:
                    err(f"unknown option '{w}'")
                elif w == "loop" and not in_loop:
                    err("“exit the loop” is not inside a repeat")

    labels = [[str(s.get("name") or "") for _p, s in iter_statements(q.get("statements")) if s.get("type") == "label"]
              for q in procs]
    for pi, proc in enumerate(procs):
        if not str(proc.get("name", "")).strip():
            issues.append((pi, (), "the procedure has no name"))
        elif proc_names.count(proc.get("name")) > 1:
            issues.append((pi, (), f"another procedure is also called '{proc.get('name')}'"))
        block(pi, proc.get("statements"), (), True, False)
    return issues


def check_before_test(procedures, context=None) -> list[str]:
    """The errors that should stop a live test from being armed (warnings left out), as readable lines
    ("'Procedure', statement 2.1: message"); [] when the procedures can run. The procedures of a project are
    checked against its zones, devices and areas with ``project_context(project)`` (disabled procedures too: another
    procedure may enable them)."""
    procs = normalize_procedures(procedures)
    out = []
    for pi, path, msg in validate(procs, context, warnings=False):
        name = procs[pi].get("name") or f"Procedure {pi + 1}"
        out.append(f"'{name}'" + (f", statement {path_text(path)}" if path else "") + f": {msg}")
    return out
