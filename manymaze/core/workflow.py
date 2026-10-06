"""Experiment workflow: behaviour keys, test schedules, test status actions, training criteria, blind codes,
animal ID confirmation and dose calculation."""

from __future__ import annotations

import math
import random
import string
from dataclasses import asdict

from .project import INACTIVE_STATUSES, Animal, Behaviour, Project, Test

MAX_STAGES = 50
MAX_TRIALS = 99
MAX_KEYS = 46
# keys usable for scoring: letters, digits and 10 punctuation keys (Space and arrows control the video)
SCORING_KEYS = "abcdefghijklmnopqrstuvwxyz0123456789-=[];',./`"
BEHAVIOUR_KINDS = ("state", "hold", "point")
ORDERS = ("animal", "trial", "random", "latin")
ID_FIELDS = ("barcode", "microchip", "rfid", "chip", "tag", "transponder")
WEIGHT_FIELD = "Weight (g)"
DOSE_FIELD = "Dose (mg/kg)"
VOLUME_FIELD = "Volume (mL)"
BLIND_COLOR = "#64748b"


# ---------------------------------------------------------------- behaviours
def validate_behaviours(behaviours: list) -> list[str]:
    """Problems with a list of behaviours (empty list = valid)."""
    errs = []
    names, keys = set(), {}
    for b in behaviours:
        if not b.name.strip():
            errs.append("A behaviour has no name.")
        elif b.name in names:
            errs.append(f"Two behaviours are called “{b.name}”.")
        names.add(b.name)
        if b.kind not in BEHAVIOUR_KINDS:
            errs.append(f"“{b.name}”: unknown type “{b.kind}”.")
        k = (b.key or "").lower()
        if not k:
            continue
        if len(k) != 1 or k not in SCORING_KEYS:
            errs.append(f"“{b.name}”: “{b.key}” cannot be used as a scoring key.")
        if k in keys:
            errs.append(f"Key “{k.upper()}” is used by both “{keys[k]}” and “{b.name}”.")
        else:
            keys[k] = b.name
    if len(keys) > MAX_KEYS:
        errs.append(f"At most {MAX_KEYS} scoring keys can be defined ({len(keys)} used).")
    return errs


def free_key(behaviours: list) -> str:
    used = {(b.key or "").lower() for b in behaviours}
    return next((c for c in "123456789qwertyuiopasdfghjklzxcvbnm0" + SCORING_KEYS if c not in used), "")


def exclusive_partners(behaviours: list, b: Behaviour) -> list[Behaviour]:
    """Behaviours stopped when `b` starts (same non-empty exclusive set)."""
    if not b.group:
        return []
    return [o for o in behaviours if o is not b and o.group == b.group and o.has_duration]


# ---------------------------------------------------------------- test status
def data_status(project: Project, test: Test) -> str:
    """Status implied by the test's data: tracked > scored > pending."""
    if project.has_track(test):
        return "tracked"
    if test.events:
        return "scored"
    return "pending"


def refresh_status(project: Project, test: Test) -> str:
    """Update an active test's status from its data (skipped / superseded / excluded tests are left alone)."""
    if test.status not in INACTIVE_STATUSES:
        test.status = data_status(project, test)
    return test.status


def skip_test(test: Test, note: str = ""):
    test.status = "skipped"
    if note and note not in test.notes:
        test.notes = f"{test.notes}; {note}" if test.notes else note


def resume_test(project: Project, test: Test):
    test.status = data_status(project, test)


def reperform_test(project: Project, test: Test) -> Test:
    """A new attempt of `test` (same animal, stage, trial, apparatus, variables); the old one is superseded."""
    d = asdict(test)
    d.update(id=project.next_test_id(), video="", events=[], status="pending", recorded_at="", notes="",
             io_events=[], result_variables={}, pauses=[], attempt=test.attempt + 1, replaces=test.id,
             zone_overrides=dict(test.zone_overrides))
    new = Test.from_dict(d)
    project.tests.insert(project.tests.index(test) + 1, new)
    test.status = "superseded"
    return new


def clear_tracks(project: Project, test: Test) -> int:
    """Delete the test's track files. Returns the number of files removed."""
    n = 0
    if project.path is not None:
        for i in range(test.n_animals):
            p = project.track_path(test, i)
            if p.exists():
                p.unlink()
                n += 1
    refresh_status(project, test)
    return n


# ---------------------------------------------------------------- schedules
def latin_square(n: int, balanced: bool = True) -> list[list[int]]:
    """Rows of a Latin square of size n. Balanced = Williams design (each level follows every other level equally
    often; 2n rows when n is odd)."""
    if n <= 0:
        return []
    if not balanced:
        return [[(i + j) % n for j in range(n)] for i in range(n)]
    first, lo, hi = [0], 1, n - 1
    while len(first) < n:
        first.append(lo)
        lo += 1
        if len(first) < n:
            first.append(hi)
            hi -= 1
    rows = [[(x + i) % n for x in first] for i in range(n)]
    if n % 2:
        rows += [list(reversed(r)) for r in rows]
    return rows


def completed_stages(project: Project) -> dict[str, list[str]]:
    return project.settings_extra.setdefault("completed_stages", {})


def generate_schedule(project: Project, animals: list[str] | None = None, stages: list[str] | None = None,
                      trials: int = 1, order: str = "animal", apparatus: str = "", seed: int | None = None,
                      counterbalance: str = "", levels: list | None = None, variable: str = "",
                      skip_existing: bool = True, skip_retired: bool = True, skip_completed: bool = True) -> list[dict]:
    """Rows {animal_id, stage, trial, apparatus, variables} for new tests, in running order.

    order: "animal" (all trials of an animal, then the next animal), "trial" (trial 1 of every animal, then trial
    2…), "random" (trial by trial, animals in random order) or "latin" (trial by trial, animals in Latin-square
    order so every animal occupies every running position equally often).
    counterbalance: "" | "apparatus" | "variable": the `levels` (apparatus names, or values of test variable
    `variable`) are assigned to each animal's successive tests from the rows of a balanced Latin square.
    """
    trials = max(1, min(MAX_TRIALS, int(trials)))
    if animals is None:
        animals = [a.id for a in project.animals]
    if skip_retired:
        animals = [a for a in animals if not (project.get_animal(a) and project.get_animal(a).retired)]
    stages = list(stages if stages is not None else (project.stages or [""]))[:MAX_STAGES]
    done = completed_stages(project) if skip_completed else {}
    have = {(t.animal_id, t.stage, t.trial) for t in project.tests if t.status != "superseded"}
    rng = random.Random(seed)
    levels = list(levels or [])
    square = latin_square(len(levels)) if counterbalance and levels else []
    pos_square = latin_square(len(animals)) if order == "latin" and animals else []
    rows = []
    for si, st in enumerate(stages):
        if order == "animal":
            seq = [(a, k) for a in animals for k in range(1, trials + 1)]
        else:
            seq = []
            for k in range(1, trials + 1):
                block = list(animals)
                if order == "random":
                    rng.shuffle(block)
                elif order == "latin" and pos_square:
                    row = pos_square[(si * trials + k - 1) % len(pos_square)]
                    block = [animals[i] for i in row]
                seq += [(a, k) for a in block]
        for a, k in seq:
            if st in done.get(a, []):
                continue
            if skip_existing and (a, st, k) in have:
                continue
            r = {"animal_id": a, "stage": st, "trial": k, "apparatus": apparatus, "variables": {}}
            if square:
                ai = animals.index(a)
                lv = levels[square[ai % len(square)][(si * trials + k - 1) % len(levels)]]
                if counterbalance == "apparatus":
                    r["apparatus"] = lv
                else:
                    r["variables"] = {variable or "condition": lv}
            rows.append(r)
    return rows


# ---------------------------------------------------------------- training criteria
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
       "=": lambda a, b: a == b}


def normalize_criterion(c: dict) -> dict:
    fail = c.get("action_fail") or {}
    if isinstance(fail, str):
        fail = {"action": fail}
    after = int(fail.get("after_trials", c.get("fail_after_trials", 0)) or 0)
    return {"stage": c.get("stage", ""), "measure": c.get("measure", ""), "op": c.get("op", "<"),
            "value": float(c.get("value", 0) or 0), "consecutive_trials": max(1, int(c.get("consecutive_trials", 1) or 1)),
            "action_met": c.get("action_met", "complete_stage"),
            "action_fail": {"after_trials": after, "action": fail.get("action", "retire" if after else "none")}}


def criterion_text(c: dict) -> str:
    c = normalize_criterion(c)
    s = f"{c['stage'] or 'any stage'}: {c['measure']} {c['op']} {c['value']:g} on {c['consecutive_trials']} " \
        f"consecutive trial{'s' if c['consecutive_trials'] != 1 else ''}"
    if c["action_fail"]["after_trials"] and c["action_fail"]["action"] == "retire":
        s += f"; retire if not met after {c['action_fail']['after_trials']} trials"
    return s


def measure_value(project: Project, test: Test, measure: str):
    """Value of a result measure (whole test, first animal) for a test, or None."""
    v = (test.result_variables or {}).get(measure)
    if v is None and project.has_results(test):
        try:
            rows = project.analyse_test(test)
        except Exception:
            rows = []
        if rows:
            v = rows[0].get(measure)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def evaluate_criteria(project: Project, value_fn=None) -> dict:
    """Evaluate the project's training criteria against the results.

    Returns {"rows": [per animal × criterion dict], "completed": {animal: [stages]}, "retire": {animal: reason}}.
    A criterion is met when the measure satisfies `op value` on `consecutive_trials` consecutive trials of the
    stage (ordered by trial). It fails when `action_fail.after_trials` trials were done without meeting it.
    """
    value_fn = value_fn or (lambda t, m: measure_value(project, t, m))
    out = {"rows": [], "completed": {}, "retire": {}}
    for raw in project.training_criteria:
        c = normalize_criterion(raw)
        if not c["measure"]:
            continue
        op = OPS.get(c["op"], OPS["<"])
        by_animal: dict[str, list[Test]] = {}
        for t in project.tests:
            if t.status in INACTIVE_STATUSES or t.status == "pending" or not t.animal_id:
                continue
            if c["stage"] and t.stage != c["stage"]:
                continue
            by_animal.setdefault(t.animal_id, []).append(t)
        for aid, tests in by_animal.items():
            tests.sort(key=lambda t: (t.trial, t.id))
            run, met_at, values = 0, None, []
            for t in tests:
                v = value_fn(t, c["measure"])
                values.append(v)
                run = run + 1 if v is not None and op(v, c["value"]) else 0
                if run >= c["consecutive_trials"] and met_at is None:
                    met_at = t.trial
                    break
            n = len(values)
            after = c["action_fail"]["after_trials"]
            failed = met_at is None and after > 0 and n >= after
            row = {"animal": aid, "stage": c["stage"], "criterion": criterion_text(c), "trials": n,
                   "values": values, "met": met_at is not None, "met_at_trial": met_at, "failed": failed,
                   "action": c["action_met"] if met_at is not None else (c["action_fail"]["action"] if failed else "")}
            out["rows"].append(row)
            if met_at is not None and c["action_met"] in ("complete_stage", "advance"):
                st = out["completed"].setdefault(aid, [])
                if c["stage"] not in st:
                    st.append(c["stage"])
            if failed and c["action_fail"]["action"] == "retire":
                out["retire"].setdefault(aid, f"did not reach “{criterion_text(c)}” within {after} trials")
    return out


def apply_criteria(project: Project, report: dict | None = None) -> dict:
    """Act on evaluate_criteria(): stages reached → remaining trials skipped; failures → animal retired and its
    pending tests skipped. Returns {"completed": n animal-stages, "retired": [ids], "skipped": n tests}."""
    report = report if report is not None else evaluate_criteria(project)
    done = completed_stages(project)
    n_completed, skipped, retired = 0, 0, []
    for aid, stages in report["completed"].items():
        for st in stages:
            if st not in done.setdefault(aid, []):
                done[aid].append(st)
                n_completed += 1
            for t in project.tests:
                if t.animal_id == aid and t.stage == st and t.status == "pending":
                    skip_test(t, "criterion met")
                    skipped += 1
    for aid, reason in report["retire"].items():
        a = project.get_animal(aid)
        if a is None or a.retired:
            continue
        a.retired, a.retired_reason = True, reason
        retired.append(aid)
        for t in project.tests:
            if aid in [t.animal_id] + list(t.extra_animals) and t.status == "pending":
                skip_test(t, "animal retired")
                skipped += 1
    return {"completed": n_completed, "retired": retired, "skipped": skipped}


def retire_animal(project: Project, animal: Animal, reason: str = "", skip_pending: bool = True) -> int:
    animal.retired, animal.retired_reason = True, reason
    n = 0
    if skip_pending:
        for t in project.tests:
            if t.animal_id == animal.id and t.status == "pending":
                skip_test(t, "animal retired")
                n += 1
    return n


def reinstate_animal(project: Project, animal: Animal) -> int:
    animal.retired, animal.retired_reason = False, ""
    n = 0
    for t in project.tests:
        if t.animal_id == animal.id and t.status == "skipped" and "animal retired" in t.notes:
            t.notes = "; ".join(x for x in t.notes.split("; ") if x != "animal retired")
            resume_test(project, t)
            n += 1
    return n


# ---------------------------------------------------------------- blind testing
def blind_codes(project: Project) -> dict[str, str]:
    """Stable random code per group (created on demand, stored in settings_extra["blind_codes"])."""
    codes = project.settings_extra.setdefault("blind_codes", {})
    used = set(codes.values())
    rng = random.SystemRandom()
    for g in project.groups:
        if g.name not in codes:
            while True:
                c = "".join(rng.choice(string.ascii_uppercase.replace("O", "").replace("I", "")) for _ in range(2)) \
                    + "".join(rng.choice("23456789") for _ in range(2))
                if c not in used:
                    break
            codes[g.name] = c
            used.add(c)
    return codes


def display_group(project: Project, name: str) -> str:
    """Group name as shown to the experimenter (a code when testing blind)."""
    if not name or not project.blind:
        return name
    return f"Group {blind_codes(project).get(name, '??')}"


def display_color(project: Project, name: str) -> str:
    return BLIND_COLOR if project.blind else project.group_color(name)


# ---------------------------------------------------------------- animal identification
def id_matches(project: Project, test: Test, scanned: str) -> bool:
    """Whether a scanned / typed identifier matches the test's animal (its ID, or a barcode / microchip field)."""
    s = (scanned or "").strip().lower()
    if not s:
        return False
    if s == (test.animal_id or "").strip().lower():
        return True
    a = project.get_animal(test.animal_id)
    if a is None:
        return False
    return any(any(k in f.lower() for k in ID_FIELDS) and str(v).strip().lower() == s
               for f, v in a.fields.items() if str(v).strip())


def confirm_id_enabled(project: Project) -> bool:
    return bool(project.settings_extra.get("confirm_id", False))


# ---------------------------------------------------------------- dose calculation
def parse_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip().replace(",", ".")
    for unit in ("mg/kg", "mg/ml", "ml", "kg", "mg", "g"):
        if s.lower().endswith(unit):
            s = s[: -len(unit)].strip()
            break
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def dose_volume_ml(weight_g, dose_mg_per_kg, conc_mg_per_ml) -> float | None:
    """Injection volume (mL) = weight (kg) × dose (mg/kg) / concentration (mg/mL)."""
    w, d, c = parse_number(weight_g), parse_number(dose_mg_per_kg), parse_number(conc_mg_per_ml)
    if w is None or d is None or c is None or c <= 0 or w < 0 or d < 0:
        return None
    return w / 1000.0 * d / c


def dose_settings(project: Project) -> dict:
    d = project.settings_extra.setdefault("dose", {})
    d.setdefault("weight_field", WEIGHT_FIELD)
    d.setdefault("dose_mg_kg", 10.0)
    d.setdefault("conc_mg_ml", 1.0)
    return d


def compute_doses(project: Project, animals: list[Animal] | None = None, write: bool = True) -> dict[str, float | None]:
    """Volume (mL) for each animal from its weight field, its own "Dose (mg/kg)" field (if any) or the experiment
    default dose, and the default concentration. With write=True the volume is stored in the animal's
    "Volume (mL)" field (the column is added if needed)."""
    ds = dose_settings(project)
    out = {}
    for a in (animals if animals is not None else project.animals):
        dose = parse_number(a.fields.get(DOSE_FIELD)) if a.fields.get(DOSE_FIELD) not in (None, "") else None
        vol = dose_volume_ml(a.fields.get(ds["weight_field"]), dose if dose is not None else ds["dose_mg_kg"],
                             ds["conc_mg_ml"])
        out[a.id] = vol
        if write:
            a.fields[VOLUME_FIELD] = "" if vol is None else f"{vol:.3f}"
    if write and VOLUME_FIELD not in project.animal_fields:
        project.animal_fields.append(VOLUME_FIELD)
    return out
