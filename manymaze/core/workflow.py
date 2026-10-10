"""Experiment workflow: behaviour keys, test schedules, test status actions, training criteria, animals and
treatments, blind codes, animal ID confirmation and dose calculation."""

from __future__ import annotations

import math
import random
import string
from dataclasses import asdict, dataclass

from .calculations import calculations_from
from .project import INACTIVE_STATUSES, Animal, Behaviour, Group, Project, Test, without_secrets

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
RESERVED_FIELDS = ("id", "group", "sex", "tests", "animal", "animal id", "status", "treatment", "notes")
STAGE_ENDED = "stage ended"  # note of the tests skipped by end_stage (removed again by reopen_stage)


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


# how a scoring key works, as in ANY-maze: "simple" (active while pressed), "toggle" (first press starts, second
# ends), "radio" (a toggle that also ends when another key of its exclusive set is pressed) or "event"
# (instantaneous). Stored as a behaviour's kind ("hold" / "state" / "point") plus its exclusive set.
RADIO_SET = "radio"  # exclusive set given to a key made a radio key


def key_mode(kind: str, group: str) -> str:
    if kind == "hold":
        return "simple"
    if kind == "point":
        return "event"
    return "radio" if group else "toggle"


def mode_to_kind(mode: str, group: str) -> tuple[str, str]:
    """(kind, exclusive set) of a key working in `mode`."""
    if mode == "simple":
        return "hold", group
    if mode == "event":
        return "point", group
    if mode == "radio":
        return "state", group or RADIO_SET
    return "state", ""


# ---------------------------------------------------------------- test status
def data_status(project: Project, test: Test) -> str:
    """Status implied by the test's data: tracked > scored (keys scored, or run with the I/O devices only) >
    pending."""
    if project.has_track(test):
        return "tracked"
    if test.events or test.io_events or test.result_variables:
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


def _fresh_copy(project: Project, test: Test, **changes) -> Test:
    """A pending copy of `test` with a new id and none of its recorded data (scoring, I/O log, results, pauses)."""
    d = asdict(test)
    d.update(id=project.next_test_id(), events=[], status="pending", recorded_at="", notes="", io_events=[],
             result_variables={}, pauses=[], experimenter="", end_reason="", **changes)
    return Test.from_dict(d)


def reperform_test(project: Project, test: Test) -> Test:
    """A new attempt of `test` (same animal, stage, trial, apparatus, variables, zone positions); the old one is
    superseded."""
    new = _fresh_copy(project, test, video="", attempt=test.attempt + 1, replaces=test.id)
    project.tests.insert(project.tests.index(test) + 1, new)
    test.status = "superseded"
    return new


def duplicate_test(project: Project, test: Test) -> Test:
    """A new pending test like `test` (same video, animal, stage, apparatus, variables), appended to the project;
    per-test zone positions and the attempt history are not copied."""
    new = _fresh_copy(project, test, zone_overrides={}, attempt=1, replaces=0)
    project.tests.append(new)
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


def delete_test(project: Project, test: Test) -> int:
    """Remove the test and its track files (videos are kept). Returns the number of track files removed."""
    n = clear_tracks(project, test)
    project.tests.remove(test)
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


@dataclass
class Criterion:
    """A training criterion (stored in ``Project.training_criteria`` as the dict of :meth:`to_dict`): met when the
    measure satisfies `op value` on `consecutive_trials` consecutive trials of the stage; it fails when
    `fail_after_trials` trials were done without meeting it (then `fail_action`: "retire" or "none")."""

    stage: str = ""
    measure: str = ""
    op: str = "<"
    value: float = 0.0
    consecutive_trials: int = 1
    action_met: str = "complete_stage"
    fail_after_trials: int = 0
    fail_action: str = "none"

    @classmethod
    def from_dict(cls, c: dict) -> "Criterion":
        """From a stored criterion, including the older flat form ({"fail_after_trials": n, "action_fail": "..."})."""
        fail = c.get("action_fail") or {}
        if isinstance(fail, str):
            fail = {"action": fail}
        after = int(fail.get("after_trials", c.get("fail_after_trials", 0)) or 0)
        return cls(c.get("stage", ""), c.get("measure", ""), c.get("op", "<"), float(c.get("value", 0) or 0),
                   max(1, int(c.get("consecutive_trials", 1) or 1)), c.get("action_met", "complete_stage"), after,
                   fail.get("action", "retire" if after else "none"))

    def to_dict(self) -> dict:
        return {"stage": self.stage, "measure": self.measure, "op": self.op, "value": self.value,
                "consecutive_trials": self.consecutive_trials, "action_met": self.action_met,
                "action_fail": {"after_trials": self.fail_after_trials, "action": self.fail_action}}

    def met_by(self, v) -> bool:
        return v is not None and OPS.get(self.op, OPS["<"])(v, self.value)

    def text(self) -> str:
        n = self.consecutive_trials
        s = f"{self.stage or 'any stage'}: {self.measure} {self.op} {self.value:g} on {n} consecutive " \
            f"trial{'s' if n != 1 else ''}"
        if self.fail_after_trials and self.fail_action == "retire":
            s += f"; retire if not met after {self.fail_after_trials} trials"
        return s


def normalize_criterion(c: dict) -> dict:
    return Criterion.from_dict(c).to_dict()


def criterion_text(c: dict) -> str:
    return Criterion.from_dict(c).text()


def _number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _first_row(project: Project, test: Test) -> dict:
    """Whole-test results of the test's first animal ({} without results or with unreadable tracks)."""
    if not project.has_results(test):
        return {}
    try:
        rows = project.analyse_test(test)
    except (OSError, ValueError, KeyError, IndexError, StopIteration):  # unreadable or malformed track files
        return {}
    return rows[0] if rows else {}


def measure_value(project: Project, test: Test, measure: str, rows: dict | None = None):
    """Value of a result measure (a saved procedure variable, else whole test, first animal) for a test, or None.
    rows: a cache {test id: first results row} shared between calls."""
    v = (test.result_variables or {}).get(measure)
    if v is None:
        row = rows.get(test.id) if rows is not None else None
        if row is None:
            row = _first_row(project, test)
            if rows is not None:
                rows[test.id] = row
        v = row.get(measure)
    return _number(v)


def evaluate_criteria(project: Project, value_fn=None) -> dict:
    """Evaluate the project's training criteria (see :class:`Criterion`) against the results.

    Returns {"rows": [per animal × criterion dict], "completed": {animal: [stages]}, "retire": {animal: reason}}.
    Trials of a stage are taken in trial order. value_fn(test, measure) -> value (default: measure_value, each
    test analysed at most once).
    """
    rows_cache: dict = {}
    value_fn = value_fn or (lambda t, m: measure_value(project, t, m, rows_cache))
    out = {"rows": [], "completed": {}, "retire": {}}
    for raw in project.training_criteria:
        c = Criterion.from_dict(raw)
        if not c.measure:
            continue
        by_animal: dict[str, list[Test]] = {}
        for t in project.tests:
            if t.status in INACTIVE_STATUSES or t.status == "pending" or not t.animal_id:
                continue
            if c.stage and t.stage != c.stage:
                continue
            by_animal.setdefault(t.animal_id, []).append(t)
        for aid, tests in by_animal.items():
            tests.sort(key=lambda t: (t.trial, t.id))
            run, met_at, values = 0, None, []
            for t in tests:
                v = value_fn(t, c.measure)
                values.append(v)
                run = run + 1 if c.met_by(v) else 0
                if run >= c.consecutive_trials:
                    met_at = t.trial
                    break
            n = len(values)
            failed = met_at is None and c.fail_after_trials > 0 and n >= c.fail_after_trials
            row = {"animal": aid, "stage": c.stage, "criterion": c.text(), "trials": n, "values": values,
                   "met": met_at is not None, "met_at_trial": met_at, "failed": failed,
                   "action": c.action_met if met_at is not None else (c.fail_action if failed else "")}
            out["rows"].append(row)
            if met_at is not None and c.action_met in ("complete_stage", "advance"):
                st = out["completed"].setdefault(aid, [])
                if c.stage not in st:
                    st.append(c.stage)
            if failed and c.fail_action == "retire":
                out["retire"].setdefault(aid, f"did not reach “{c.text()}” within {c.fail_after_trials} trials")
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
        retired.append(aid)
        skipped += retire_animal(project, a, reason)
    return {"completed": n_completed, "retired": retired, "skipped": skipped}


def end_stage(project: Project, animal_id: str, stage: str) -> int:
    """End a stage for one animal before it has done all its trials (ANY-maze: "end stage for this animal"), as a
    met training criterion does: the stage is recorded as completed for the animal (new schedules leave it out)
    and its pending tests of the stage are skipped. Returns the number of tests skipped."""
    done = completed_stages(project).setdefault(animal_id, [])
    if stage not in done:
        done.append(stage)
    n = 0
    for t in project.tests:
        if t.animal_id == animal_id and t.stage == stage and t.status == "pending":
            skip_test(t, STAGE_ENDED)
            n += 1
    return n


def stage_ended(project: Project, animal_id: str, stage: str) -> bool:
    return stage in project.settings_extra.get("completed_stages", {}).get(animal_id, [])


def reopen_stage(project: Project, animal_id: str, stage: str) -> int:
    """Undo end_stage (or a met criterion): the stage is no longer completed for the animal and the tests that
    end_stage skipped are resumed. Returns the number of tests resumed."""
    done = completed_stages(project)
    if stage in done.get(animal_id, []):
        done[animal_id].remove(stage)
        if not done[animal_id]:
            del done[animal_id]
    n = 0
    for t in project.tests:
        if t.animal_id == animal_id and t.stage == stage and t.status == "skipped" and STAGE_ENDED in t.notes:
            t.notes = "; ".join(x for x in t.notes.split("; ") if x != STAGE_ENDED)
            resume_test(project, t)
            n += 1
    return n


def _animals(test: Test) -> list[str]:
    return [test.animal_id, *test.extra_animals]


def retire_animal(project: Project, animal: Animal, reason: str = "", skip_pending: bool = True) -> int:
    """Retire the animal and (by default) skip its pending tests, including those it takes part in as a further
    animal. Returns the number of tests skipped."""
    animal.retired, animal.retired_reason = True, reason
    n = 0
    if skip_pending:
        for t in project.tests:
            if animal.id in _animals(t) and t.status == "pending":
                skip_test(t, "animal retired")
                n += 1
    return n


def reinstate_animal(project: Project, animal: Animal) -> int:
    """Undo retire_animal: resume the tests it skipped unless another of their animals is still retired."""
    animal.retired, animal.retired_reason = False, ""
    retired = {a.id for a in project.animals if a.retired}
    n = 0
    for t in project.tests:
        if animal.id in _animals(t) and t.status == "skipped" and "animal retired" in t.notes \
                and not retired.intersection(_animals(t)):
            t.notes = "; ".join(x for x in t.notes.split("; ") if x != "animal retired")
            resume_test(project, t)
            n += 1
    return n


# ---------------------------------------------------------------- animals and treatments
def rename_animal(project: Project, animal: Animal, new_id: str) -> bool:
    """Give the animal a new (unique, non-empty) ID, updating the tests it takes part in."""
    new_id = new_id.strip()
    if not new_id or new_id == animal.id or project.get_animal(new_id) is not None:
        return False
    old, animal.id = animal.id, new_id
    for t in project.tests:
        if t.animal_id == old:
            t.animal_id = new_id
        if old in t.extra_animals:
            t.extra_animals = [new_id if x == old else x for x in t.extra_animals]
    done = project.settings_extra.get("completed_stages", {})
    if old in done:
        done[new_id] = done.pop(old)
    return True


def add_group(project: Project, name: str, color: str | None = None) -> Group | None:
    """A new treatment (None if the name is empty or taken)."""
    name = name.strip()
    if not name or project.get_group(name) is not None:
        return None
    g = Group(name, color or project.next_group_color())
    project.groups.append(g)
    return g


def rename_group(project: Project, old: str, new: str) -> bool:
    """Rename a treatment, moving its animals and its blind code to the new name."""
    new = new.strip()
    g = project.get_group(old)
    if g is None or not new or new == old or project.get_group(new) is not None:
        return False
    g.name = new
    for a in project.animals:
        if a.group == old:
            a.group = new
    codes = project.settings_extra.get("blind_codes", {})
    if old in codes:
        codes[new] = codes.pop(old)
    return True


def delete_group(project: Project, name: str):
    """Delete a treatment; its animals are left without one."""
    project.groups = [g for g in project.groups if g.name != name]
    for a in project.animals:
        if a.group == name:
            a.group = ""


def add_field(project: Project, name: str) -> bool:
    """Add a custom animal field (column); False if the name is empty, taken or reserved."""
    name = name.strip()
    if not name or name in project.animal_fields or name.lower() in RESERVED_FIELDS:
        return False
    project.animal_fields.append(name)
    return True


def rename_field(project: Project, old: str, new: str) -> bool:
    new = new.strip()
    f = project.animal_fields
    if old not in f or not new or new in f or new.lower() in RESERVED_FIELDS:
        return False
    f[f.index(old)] = new
    for a in project.animals:
        if old in a.fields:
            a.fields[new] = a.fields.pop(old)
    dose = project.settings_extra.get("dose")
    if isinstance(dose, dict) and dose.get("weight_field") == old:
        dose["weight_field"] = new
    return True


def remove_field(project: Project, name: str) -> bool:
    if name not in project.animal_fields:
        return False
    project.animal_fields.remove(name)
    for a in project.animals:
        a.fields.pop(name, None)
    dose = project.settings_extra.get("dose")
    if isinstance(dose, dict) and dose.get("weight_field") == name:
        dose.pop("weight_field")  # back to the default weight field (dose_settings)
    return True


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


def treatment_code(project: Project | None, name: str) -> str:
    """Code of a treatment: its blind code while testing blind, else a letter (A, B, …) in list order."""
    if not name or project is None:
        return ""
    if project.blind:
        return blind_codes(project).get(name, "??")
    names = [g.name for g in project.groups]
    if name not in names:
        return ""
    n, s = names.index(name) + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def treatment_text(project: Project | None, name: str, with_code: bool = True) -> str:
    """A treatment as shown to the experimenter: "A - Saline", or only its code while testing blind."""
    if not name or project is None:
        return name or ""
    code = treatment_code(project, name)
    if project.blind:
        return code
    return f"{code} - {name}" if with_code and code else name


def display_group(project: Project, name: str) -> str:
    """Group name as shown to the experimenter (a code when testing blind)."""
    if not name or not project.blind:
        return name
    return f"Group {blind_codes(project).get(name, '??')}"


def display_color(project: Project, name: str) -> str:
    return BLIND_COLOR if project.blind else project.group_color(name)


# ---------------------------------------------------------------- users (experimenters)
def add_experimenter(project: Project, name: str) -> str:
    """Add a user name to the experiment's experimenters (if new). Returns the cleaned name ("" if empty)."""
    name = " ".join(str(name or "").split())
    if name and name not in project.experimenters:
        project.experimenters.append(name)
    return name


def remove_experimenter(project: Project, name: str) -> bool:
    """Remove a user from the list (the tests keep the name they were stamped with)."""
    if name not in project.experimenters:
        return False
    project.experimenters.remove(name)
    if project.current_user == name:
        project.current_user = ""
    return True


def set_experimenter(project: Project, tests: list[Test], name: str) -> int:
    """Set the experimenter of the tests (added to the experiment's list). Returns the number changed."""
    name = add_experimenter(project, name) if str(name or "").strip() else ""
    n = 0
    for t in tests:
        if t.experimenter != name:
            t.experimenter = name
            n += 1
    return n


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


# ---------------------------------------------------------------------------- protocol copy
# settings_extra entries that belong to the protocol (others, e.g. blind codes and completed stages, are data)
PROTOCOL_EXTRAS = ("touchscreen", "live", "cameras", "mode", "confirm_id", "dose", "operant_preset")


def copy_protocol(src: Project, dst: Project, treatments: bool = False) -> Project:
    """Give ``dst`` the protocol of ``src`` (ANY-maze: new experiment based on another one's protocol).

    Copies the apparatus, stages, keys, test duration and start, animal tracking and analysis settings,
    calculations, results reports, statistics settings, procedures, I/O devices, training criteria, blind testing and
    animal ID options, the animal columns and the experimenters (users); I/O device passwords and tokens are not
    copied (enter them again);
    with ``treatments`` also the treatments (groups). Animals, tests and results are not copied.
    """
    import copy as _copy

    from .measures import AnalysisSettings
    from .tracking import DetectionSettings

    dst.protocol = src.protocol
    dst.test_duration_s = src.test_duration_s
    dst.start_mode = src.start_mode
    dst.detection = DetectionSettings.from_dict(src.detection.to_dict())
    dst.analysis = AnalysisSettings.from_dict(_copy.deepcopy(src.analysis.to_dict()))
    dst.calculations = calculations_from(c.to_dict() for c in src.calculations)
    dst.reports = _copy.deepcopy(src.reports)
    dst.statistics = _copy.deepcopy(src.statistics)
    dst.apparatus = [a.copy() for a in src.apparatus]
    dst.behaviours = [Behaviour.from_dict(asdict(b)) for b in src.behaviours]
    dst.stages = list(src.stages)
    dst.procedures = _copy.deepcopy(src.procedures)
    dst.io_devices = [without_secrets(d) for d in _copy.deepcopy(src.io_devices)]  # passwords stay behind
    dst.training_criteria = _copy.deepcopy(src.training_criteria)
    dst.blind = src.blind
    dst.animal_fields = list(src.animal_fields)
    dst.experimenters += [u for u in src.experimenters if u not in dst.experimenters]
    for k in PROTOCOL_EXTRAS:
        if k in src.settings_extra:
            dst.settings_extra[k] = _copy.deepcopy(src.settings_extra[k])
    if treatments:
        have = {g.name for g in dst.groups}
        dst.groups += [Group(g.name, g.color) for g in src.groups if g.name not in have]
    return dst


# ---------------------------------------------------------------------------- random allocation
def randomise_treatments(project: Project, animals: list[Animal] | None = None, groups: list[str] | None = None,
                         stratify_by: str = "", seed: int | None = None) -> dict[str, str]:
    """Allocate animals to treatments at random in balanced numbers (block randomisation).

    Each stratum (all animals, or the animals sharing a value of ``stratify_by`` — "Sex" or an animal column) is
    shuffled and dealt round-robin over the treatments in a random order, so group sizes differ by at most one and
    each stratum is spread evenly. Writes the treatments and returns {animal id: treatment}."""
    animals = [a for a in (animals if animals is not None else project.animals) if not a.retired]
    groups = list(groups if groups is not None else [g.name for g in project.groups])
    if not groups:
        raise ValueError("Add treatments first")
    rng = random.Random(seed)

    def key(a: Animal) -> str:
        if not stratify_by:
            return ""
        return str(a.sex if stratify_by == "Sex" else a.fields.get(stratify_by, ""))

    strata: dict[str, list[Animal]] = {}
    for a in animals:
        strata.setdefault(key(a), []).append(a)
    counts = {g: 0 for g in groups}
    out = {}
    for k in sorted(strata):
        members = strata[k][:]
        rng.shuffle(members)
        # deal from the currently smallest treatments first so the totals stay balanced across strata
        order = sorted(groups, key=lambda g: (counts[g], rng.random()))
        for i, a in enumerate(members):
            g = order[i % len(order)]
            a.group = g
            counts[g] += 1
            out[a.id] = g
    have = {g.name for g in project.groups}
    for g in groups:
        if g not in have:
            project.groups.append(Group(g))
    return out
