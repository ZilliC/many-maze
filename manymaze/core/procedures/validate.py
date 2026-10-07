"""Edit-time validation of procedures."""

from __future__ import annotations

import keyword
import re

from ..operant import parse_spec
from .catalog import ACTION_SPECS, CONSTANTS, EVENT_SPECS, LOCAL_NAMES, STATEMENT_TYPES, STOP_WHAT, WHEN_MODES
from .expr import _INTERP, check_expr
from .model import iter_statements, normalize_procedures, statement_fields, wait_mode


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
            "devices": set(_names(devs)) if devs is not None else None,
            "channels": channels or None,
            "areas": set(_names(c.get("areas"))) if c.get("areas") else None}


def project_context(project) -> dict:
    """The validation context of a project: the zones and groups of all its apparatus, its I/O devices (None when
    there are none: device names are then not checked) and its touch-screen areas."""
    zones = [n for a in project.apparatus for n in a.names()]
    areas = [a.get("name") for a in (project.settings_extra.get("touchscreen", {}) or {}).get("areas", [])
             if a.get("name")]
    ctx = {"zones": sorted(set(zones)) if zones else None, "devices": list(project.io_devices) or None}
    if areas:
        ctx["areas"] = areas
    return ctx


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


def _check_param(p, v, names, ctx, st) -> list[str]:
    typ, label = p["type"], p["label"]
    empty = v is None or (isinstance(v, str) and not v.strip())
    if empty:
        return [f"{label} is required"] if p.get("req") else []
    errs = []
    if typ in ("number", "int"):
        errs = check_expr(v, names)
        if not errs and isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0 \
                and p["name"] not in ("value", "by", "threshold"):
            errs = ["must not be negative"]
    elif typ == "expr":
        errs = check_expr(v, names)
    elif typ == "text":
        for m in _INTERP.finditer(str(v).replace("{{", "").replace("}}", "")):
            errs += check_expr(m.group(1), names)
    elif typ == "zone":
        if ctx["zones"] is not None and str(v) not in ctx["zones"]:
            errs = [f"unknown zone '{v}'"]
    elif typ == "sequence":
        zs = [z.strip() for z in str(v).split(",") if z.strip()]
        if len(zs) < 2:
            errs = ["give at least two zones separated by commas"]
        elif ctx["zones"] is not None:
            errs = [f"unknown zone '{z}'" for z in zs if z not in ctx["zones"]]
    elif typ in ("device", "audio"):
        if ctx["devices"] is not None and str(v) not in ctx["devices"]:
            errs = [f"unknown device '{v}'"]
    elif typ in ("input", "output"):
        chans = ctx["channels"]
        if chans is not None:
            dev = st.get("device") or ""
            pool = chans.get(dev, {}) if dev else {k: kd for d in chans.values() for k, kd in d.items()}
            if dev and dev not in chans:
                pool = None
            if pool is not None and str(v) not in pool:
                errs = [f"unknown {'input' if typ == 'input' else 'output'} channel '{v}'"]
            elif pool is not None:
                kd = pool[str(v)]
                want = ("input", "analog", "encoder") if typ == "input" else ("output", "pwm")
                if kd not in want:
                    errs = [f"'{v}' is an {kd} channel, not an {'input' if typ == 'input' else 'output'}"]
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


def validate(procedures, context=None) -> list[tuple[int, tuple, str]]:
    """Edit-time check. Returns [(procedure index, statement path, message)]; path () = the procedure itself.

    context: {"zones": [...], "devices": [names or io_devices configs], "areas": [...]} — optional; names are
    only checked against the lists that are given."""
    procs = normalize_procedures(procedures)
    ctx = _context(context)
    names = declared_names(procs)
    issues: list[tuple[int, tuple, str]] = []
    seen_vars: dict[str, int] = {}
    proc_names = [p.get("name") for p in procs]

    def block(pi, stmts, path, top, in_loop):
        if not isinstance(stmts, list):
            issues.append((pi, path, "statements must be a list"))
            return
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
                block(pi, st.get("body", []), p + ("body",), False, False)
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
            elif t == "wait":
                ev = st.get("event")
                if wait_mode(st) == "event" and ev not in EVENT_SPECS:
                    err(f"unknown event '{ev}'")
                elif wait_mode(st) == "event" and ev == "test_start":
                    err("cannot wait for the test to start")
                else:
                    check(statement_fields(st))
                if st.get("timeout") not in (None, "", 0):
                    for m in check_expr(st["timeout"], names):
                        err(f"timeout: {m}")
            elif t == "if":
                check(statement_fields(st))
                block(pi, st.get("body", []), p + ("body",), False, in_loop)
                if "else" in st:
                    block(pi, st.get("else") or [], p + ("else",), False, in_loop)
            elif t == "repeat":
                check(statement_fields(st))
                if st.get("var"):
                    e = _bad_var_name(st["var"])
                    if e:
                        err(e)
                block(pi, st.get("body", []), p + ("body",), False, True)
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

    for pi, proc in enumerate(procs):
        if not str(proc.get("name", "")).strip():
            issues.append((pi, (), "the procedure has no name"))
        elif proc_names.count(proc.get("name")) > 1:
            issues.append((pi, (), f"another procedure is also called '{proc.get('name')}'"))
        block(pi, proc.get("statements"), (), True, False)
    return issues

