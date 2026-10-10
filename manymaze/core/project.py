"""Experiment (project) model and persistence.

A project is a directory (``Name.mmaze``) containing ``project.json`` plus
``tracks/`` (per-test track CSVs), ``recordings/`` (videos captured live) and
``exports/``.  Video paths are stored relative to the project when possible so
projects can be moved between machines (videos outside the folder also keep their absolute path, tried when the
relative one no longer leads to the file; :func:`relink_videos` finds moved videos by name).

Passwords and tokens of I/O devices (alert e-mail / SMS) are kept out of ``project.json`` in ``io-secrets.json``
(readable by the owner only; left out of archives, reports and protocol copies). ``.manymaze.lock`` says which
program has the experiment open (see :mod:`.explock`).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .apparatus import Apparatus, from_known
from .atomicfile import write_text_atomic
from .calculations import Calculation, Trials, calculations_from, evaluate_calc, evaluate_test, parse, plan
from .ioconfig import is_secret
from .measures import (AnalysisSettings, add_warning, all_periods, analyse, analyse_period, analyse_segmented,
                       behaviour_measures, io_only_measures, io_only_periods, time_periods)
from .periods import Period, calculation_columns, no_end_warning, uses_calculations
from .reports import find_report, report_columns, report_rows, reports_from
from .session import END_ZONE
from .templates import apply_overrides
from .track import Track
from .tracking import ArenaJob, DetectionSettings, track_video
from .video import VideoSource

PROJECT_FILE = "project.json"
SECRETS_FILE = "io-secrets.json"  # I/O device passwords and tokens (not in project.json)
BACKUP_DIR = "backups"
BACKUP_INTERVAL_S = 600.0  # at most one automatic backup per 10 minutes of saving
BACKUP_KEEP = 30
FORMAT_VERSION = 1
# columns of a results row that describe the test rather than measure it (animal fields are added to these)
INFO_COLUMNS = ["Test", "Animal", "Group", "Treatment code", "Sex", "Animal notes", "Stage", "Trial", "Apparatus",
                "Test date", "Day of week", "Test time", "Time of day", "User", "Test notes", "Reason for test end",
                "Animal lighter / darker", "Animal length", "Frames tracked (%)", "Jumps removed", "Source video file",
                "Recorded video file", "Video time at test start (s)", "Moveable zone positions", "Period",
                "Segment of test"]
# information columns not shown in the results table until ticked in its column chooser (rarely needed)
OPTIONAL_INFO_COLUMNS = ["Treatment code", "Animal notes", "Time of day", "Reason for test end",
                         "Animal lighter / darker", "Animal length", "Frames tracked (%)", "Jumps removed",
                         "Source video file",
                         "Recorded video file", "Video time at test start (s)", "Moveable zone positions",
                         "Segment of test"]
# ANY-maze "time of day" of a live test: (first hour, name), the name of the last band whose hour has passed
TIME_OF_DAY = ((0, "Night"), (5, "Morning"), (12, "Afternoon"), (17, "Evening"), (21, "Night"))
ERROR_COLUMN = "Analysis error"  # results row of a test whose analysis failed (the other tests still get results)
log = logging.getLogger(__name__)


# test status values: "pending" (to do), "tracked" (has a track), "scored" (manually scored, no track),
# "skipped" (not performed for now, can be resumed), "superseded" (replaced by a re-performed attempt),
# "excluded" (left out of results)
STATUSES = ("pending", "tracked", "scored", "skipped", "superseded", "excluded")
INACTIVE_STATUSES = frozenset({"skipped", "superseded", "excluded"})  # left out of results and statistics
GROUP_PALETTE = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#64748b"]


@dataclass
class Animal:
    id: str
    group: str = ""
    sex: str = ""
    fields: dict = field(default_factory=dict)
    retired: bool = False  # withdrawn from the experiment (e.g. failed a training criterion)
    retired_reason: str = ""
    notes: str = ""  # free-form notes about the animal
    weights: list = field(default_factory=list)  # weight history: [{"date": ISO date/time, "grams": float}]

    @classmethod
    def from_dict(cls, d):
        weights = [{"date": str(w.get("date", "")), "grams": float(w["grams"])}
                   for w in d.get("weights", []) or [] if isinstance(w, dict) and w.get("grams") is not None]
        return cls(str(d["id"]), d.get("group", ""), d.get("sex", ""), dict(d.get("fields", {})),
                   bool(d.get("retired", False)), d.get("retired_reason", ""), str(d.get("notes", "") or ""),
                   weights)


@dataclass
class Group:
    name: str
    color: str = "#3b82f6"

    @classmethod
    def from_dict(cls, d):
        return from_known(cls, d)


@dataclass
class Behaviour:
    """A manually scored behaviour.

    kind = "state" (key toggles it on/off), "hold" (scored while the key is held down) or "point" (instantaneous).
    Behaviours sharing a non-empty ``group`` are mutually exclusive: starting one stops the others.
    """

    name: str
    key: str = ""
    kind: str = "state"
    group: str = ""
    color: str = ""

    @classmethod
    def from_dict(cls, d):
        return from_known(cls, d)

    @property
    def has_duration(self) -> bool:
        return self.kind != "point"


@dataclass
class Test:
    id: int
    animal_id: str = ""
    video: str = ""
    apparatus: str = ""
    stage: str = ""
    trial: int = 1
    start_s: float = 0.0  # time in the video at which the test starts
    duration_s: float = 0.0  # 0 = project default
    extra_animals: list = field(default_factory=list)  # ids of further animals tracked in the same arena
    detection: dict = field(default_factory=dict)  # overrides of project detection settings
    variables: dict = field(default_factory=dict)  # per-test variables (e.g. novel object, social side)
    events: list = field(default_factory=list)  # manual scoring [{"behaviour", "t", "t_end"}]
    status: str = "pending"  # see STATUSES
    notes: str = ""
    recorded_at: str = ""
    io_events: list = field(default_factory=list)  # live I/O log [{"t", "device", "channel", "kind": "input"|"output", "value"}]
    result_variables: dict = field(default_factory=dict)  # numeric procedure variables saved as results
    zone_overrides: dict = field(default_factory=dict)  # moveable zones: {zone name: shape dict} for this test
    pauses: list = field(default_factory=list)  # [[t_start, t_end], ...] test-time intervals the test was paused
    attempt: int = 1  # re-performed tests get attempt 2, 3, ...
    replaces: int = 0  # id of the test this attempt re-performs (0 = none)
    experimenter: str = ""  # the user who ran (live) or tracked / scored the test
    end_reason: str = ""  # why a live test ended (END_* values); "" for tests tracked from a video

    @classmethod
    def from_dict(cls, d):
        return from_known(cls, d)

    @property
    def n_animals(self) -> int:
        return 1 + len(self.extra_animals)


@dataclass
class Project:
    name: str = "Untitled experiment"
    description: str = ""
    protocol: str = "open_field"
    test_duration_s: float = 300.0
    # "manual" (start_s) | "on_detection" (first frame the animal is in the arena) | "experimenter_leaves" (the
    # first frame with the animal alone after the experimenter's hand left the image, see autostart.py)
    start_mode: str = "manual"
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    apparatus: list[Apparatus] = field(default_factory=list)
    animals: list[Animal] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    behaviours: list[Behaviour] = field(default_factory=list)
    tests: list[Test] = field(default_factory=list)
    animal_fields: list = field(default_factory=list)  # extra animal column names
    stages: list = field(default_factory=list)
    procedures: list = field(default_factory=list)  # live procedures (see procedures.py)
    io_devices: list = field(default_factory=list)  # I/O device configurations (see iodevices.py)
    variables: dict = field(default_factory=dict)  # procedure variables kept between tests
    training_criteria: list = field(default_factory=list)  # per-stage criteria (see project workflow)
    calculations: list[Calculation] = field(default_factory=list)  # results from other results (calculations.py)
    reports: list = field(default_factory=list)  # saved results reports of the Data page (reports.py)
    statistics: dict = field(default_factory=dict)  # the Statistics page's settings (factors, test, alpha …)
    blind: bool = False  # hide group / treatment while testing and scoring
    experimenters: list = field(default_factory=list)  # user names offered as the current user / test experimenter
    settings_extra: dict = field(default_factory=dict)  # misc. UI / workflow settings
    created: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    path: Path | None = None
    current_user: str = ""  # who is using the app (not saved: set by the GUI); stamped on tests run or tracked
    file_version: int = FORMAT_VERSION  # the version of the file it was loaded from (not saved)
    unknown: dict = field(default_factory=dict)  # top-level keys this version does not know: written back as read
    read_only: bool = False  # opened while another program has it open (not saved): save() refuses
    # stored video path -> absolute path saved with it, for videos outside the experiment folder (not saved as such)
    video_alternatives: dict = field(default_factory=dict, repr=False)

    # ---- persistence -----------------------------------------------------
    @staticmethod
    def project_dir(path: str | os.PathLike) -> Path:
        p = Path(path)
        if p.name == PROJECT_FILE:
            p = p.parent
        return p

    def save(self, path: str | os.PathLike | None = None):
        if path is not None:
            self.path = self.project_dir(path)
        if self.path is None:
            raise ValueError("No project path")
        self.check_writable()
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "tracks").mkdir(exist_ok=True)
        text = dumps_json(self.to_dict())
        if self.settings_extra.get("backups", True):
            self.backup(min_interval_s=BACKUP_INTERVAL_S)
        write_text_atomic(self.path / PROJECT_FILE, text)
        self._save_secrets()

    def check_writable(self):
        """Raise ValueError if this experiment must not be saved: opened read-only, or written by a newer version."""
        if self.read_only:
            raise ValueError("This experiment was opened read-only because another mANY-MAZE has it open. Close it "
                             "there and open it again here to save, or save a copy (Save as).")
        if self.file_version > FORMAT_VERSION:
            raise ValueError(f"This experiment was saved by a newer version of mANY-MAZE (file version "
                             f"{self.file_version}, this version reads {FORMAT_VERSION}); saving it here would lose "
                             f"what this version does not know. Update mANY-MAZE to save it.")

    def save_as(self, dest: str | os.PathLike) -> bool:
        """Save a copy of the experiment (file and tracks) in the folder ``dest`` and continue in the copy; video
        paths are rewritten so they still lead to the videos. Recordings and exports stay in the original folder.

        A ``dest`` that is this experiment's own folder (whatever the spelling: letter case, symbolic link, relative
        path) is a plain save. An existing experiment in ``dest`` is replaced: its tracks are swapped for a complete
        copy of this experiment's tracks only once that copy is made. Returns True when a copy was made."""
        dest = self.project_dir(dest)
        old = self.path
        if old is not None and same_folder(dest, old):
            self.save()
            return False
        if self.file_version > FORMAT_VERSION:
            self.check_writable()
        videos, read_only = [t.video for t in self.tests], self.read_only
        dest.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        staged, trash = dest / f"tracks.copy-{tag}", dest / f"tracks.old-{tag}"
        moved_old = False
        try:
            # keep video paths valid from the new location: make them absolute, then relative to the copy
            for t in self.tests:
                t.video = self.abs_path(t.video)
            if old is not None and (old / "tracks").exists():
                shutil.copytree(old / "tracks", staged)  # a full copy first: nothing is deleted before this
            if (dest / "tracks").exists():
                os.replace(dest / "tracks", trash)  # replacing: no stale tracks of the old experiment mixed in
                moved_old = True
            if staged.exists():
                os.replace(staged, dest / "tracks")
            self.path, self.read_only = dest, False
            for t in self.tests:
                t.video = self.rel_path(t.video)
            self.save()
        except BaseException:
            self.path, self.read_only = old, read_only
            for t, v in zip(self.tests, videos):
                t.video = v
            if moved_old:
                shutil.rmtree(dest / "tracks", ignore_errors=True)
                try:
                    os.replace(trash, dest / "tracks")
                except OSError:
                    pass
            shutil.rmtree(staged, ignore_errors=True)
            raise
        shutil.rmtree(trash, ignore_errors=True)
        return True

    # ---- I/O device secrets ------------------------------------------------------------------------
    def io_secrets(self) -> dict:
        """{device name: {secret field: value}} of the I/O devices (alert passwords and tokens that are set)."""
        out = {}
        for d in self.io_devices:
            s = {k: v for k, v in d.items() if is_secret(k) and v not in (None, "")}
            if s and d.get("name"):
                out[str(d["name"])] = s
        return out

    def _save_secrets(self):
        """Write io-secrets.json (owner read / write only), or remove it when no device has a secret."""
        f = self.path / SECRETS_FILE
        sec = self.io_secrets()
        if not sec:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
            return
        write_text_atomic(f, json.dumps({"format": "manymaze-io-secrets", "devices": sec}, indent=1))
        try:
            os.chmod(f, 0o600)
        except OSError:  # pragma: no cover - file systems without permissions
            pass

    def _load_secrets(self):
        """Put the secrets of io-secrets.json back into the device configurations (values still in project.json,
        written by older versions, are kept until the next save moves them)."""
        if self.path is None:
            return
        try:
            d = json.loads((self.path / SECRETS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        devices = d.get("devices") if isinstance(d, dict) else None
        if not isinstance(devices, dict):
            return
        for dev in self.io_devices:
            s = devices.get(str(dev.get("name", "")))
            if isinstance(s, dict):
                for k, v in s.items():
                    if is_secret(k) and not dev.get(k):
                        dev[k] = v

    # ---- backups ---------------------------------------------------------------------------------
    def backups_dir(self) -> Path:
        return self.path / BACKUP_DIR

    def list_backups(self) -> list[Path]:
        """Backup copies of the experiment file, newest first."""
        if self.path is None or not self.backups_dir().exists():
            return []
        return sorted(self.backups_dir().glob("project-*.json"), reverse=True)

    def backup(self, min_interval_s: float = 0.0, keep: int = BACKUP_KEEP) -> Path | None:
        """Copy the saved experiment file (as it is before this save) to ``backups/project-<time>.json`` unless the
        newest backup is younger than min_interval_s; keeps the ``keep`` newest. Track files are not copied: edits
        to tracks are kept by the track editor's undo."""
        if self.path is None or not (self.path / PROJECT_FILE).exists():
            return None
        now = _dt.datetime.now()
        old = self.list_backups()
        if old and min_interval_s > 0 and now.timestamp() - old[0].stat().st_mtime < min_interval_s:
            return None
        self.backups_dir().mkdir(exist_ok=True)
        dest = self.backups_dir() / f"project-{now:%Y%m%d-%H%M%S}.json"
        if not dest.exists():
            import shutil

            shutil.copy2(self.path / PROJECT_FILE, dest)
            os.utime(dest)
        for extra in self.list_backups()[max(1, keep):]:
            try:
                extra.unlink()
            except OSError:
                pass
        return dest

    def restore_backup(self, backup: str | os.PathLike) -> "Project":
        """The experiment as stored in a backup (the current file is backed up first). Save it to restore."""
        self.check_writable()
        data = json.loads(Path(backup).read_text(encoding="utf-8"))  # read first: backup() prunes the oldest copy
        self.backup()
        return Project.from_dict(data, self.path)

    def to_dict(self) -> dict:
        return {
            **self.unknown,
            "format": "manymaze-project",
            "version": FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "protocol": self.protocol,
            "test_duration_s": self.test_duration_s,
            "start_mode": self.start_mode,
            "detection": self.detection.to_dict(),
            "analysis": self.analysis.to_dict(),
            "apparatus": [a.to_dict() for a in self.apparatus],
            "animals": [asdict(a) for a in self.animals],
            "groups": [asdict(g) for g in self.groups],
            "behaviours": [asdict(b) for b in self.behaviours],
            "tests": [self._test_dict(t) for t in self.tests],
            "animal_fields": self.animal_fields,
            "stages": self.stages,
            "procedures": self.procedures,
            "io_devices": [without_secrets(d) for d in self.io_devices],
            "variables": self.variables,
            "training_criteria": self.training_criteria,
            "calculations": [c.to_dict() for c in self.calculations],
            "reports": self.reports,
            "statistics": self.statistics,
            "blind": self.blind,
            "experimenters": self.experimenters,
            "settings_extra": self.settings_extra,
            "created": self.created,
        }

    def _test_dict(self, t: Test) -> dict:
        d = asdict(t)
        alt = self._outside_video(t.video)
        if alt:
            d["video_abs"] = alt
        return d

    def _outside_video(self, v: str) -> str:
        """The absolute path of a video stored relative to the experiment but outside its folder ("../.."), saved
        with it so the video is still found when the experiment folder moves without it ("" otherwise)."""
        if not v or Path(v).is_absolute() or not Path(v).parts or Path(v).parts[0] != "..":
            return ""
        if self.path is None:
            return self.video_alternatives.get(v, "")
        return self.abs_path(v)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        pdir = cls.project_dir(path)
        return cls.from_dict(json.loads((pdir / PROJECT_FILE).read_text(encoding="utf-8")), pdir)

    @classmethod
    def from_dict(cls, d: dict, path: str | os.PathLike | None = None) -> "Project":
        pdir = Path(path) if path is not None else None
        p = cls(
            name=d.get("name", pdir.stem if pdir else ""),
            description=d.get("description", ""),
            protocol=d.get("protocol", "open_field"),
            test_duration_s=d.get("test_duration_s", 300.0),
            start_mode=d.get("start_mode", "manual"),
            detection=DetectionSettings.from_dict(d.get("detection")),
            analysis=AnalysisSettings.from_dict(d.get("analysis")),
            apparatus=[Apparatus.from_dict(a) for a in d.get("apparatus", [])],
            animals=[Animal.from_dict(a) for a in d.get("animals", [])],
            groups=[Group.from_dict(g) for g in d.get("groups", [])],
            behaviours=[Behaviour.from_dict(b) for b in d.get("behaviours", [])],
            tests=[Test.from_dict(t) for t in d.get("tests", [])],
            animal_fields=d.get("animal_fields", []),
            stages=d.get("stages", []),
            procedures=d.get("procedures", []),
            io_devices=d.get("io_devices", []),
            variables=d.get("variables", {}),
            training_criteria=d.get("training_criteria", []),
            calculations=calculations_from(d.get("calculations")),
            reports=reports_from(d.get("reports")),
            statistics=dict(d["statistics"]) if isinstance(d.get("statistics"), dict) else {},
            blind=d.get("blind", False),
            experimenters=[str(u) for u in d.get("experimenters", []) if str(u).strip()],
            settings_extra=d.get("settings_extra", {}),
            created=d.get("created", ""),
        )
        p.path = pdir
        try:
            p.file_version = int(d.get("version", FORMAT_VERSION))
        except (TypeError, ValueError):
            p.file_version = FORMAT_VERSION
        known = set(cls().to_dict())
        p.unknown = {k: v for k, v in d.items() if k not in known}
        p.video_alternatives = {str(t["video"]): str(t["video_abs"]) for t in d.get("tests", [])
                                if isinstance(t, dict) and t.get("video") and t.get("video_abs")}
        p._load_secrets()
        return p

    # ---- lookup -----------------------------------------------------------
    def get_apparatus(self, name: str) -> Apparatus | None:
        return next((a for a in self.apparatus if a.name == name), self.apparatus[0] if self.apparatus else None)

    def find_apparatus(self, name: str) -> Apparatus | None:
        """The apparatus called exactly `name`, or None (no fallback to the first one, unlike get_apparatus)."""
        return next((a for a in self.apparatus if a.name == name), None)

    def apparatus_of(self, test: Test) -> Apparatus | None:
        """The test's apparatus as the analysis uses it: per-test zone positions (Test.zone_overrides) applied."""
        app = self.get_apparatus(test.apparatus)
        return apply_overrides(app, test.zone_overrides) if app is not None else None

    def tests_using(self, apparatus_name: str) -> list[Test]:
        """Tests analysed with this apparatus (their apparatus name resolved as get_apparatus does)."""
        return [t for t in self.tests if getattr(self.get_apparatus(t.apparatus), "name", None) == apparatus_name]

    def rename_in_apparatus(self, app: Apparatus, kind: str, index: int, new_name: str) -> str:
        """Apparatus.rename, also moving the per-test positions (Test.zone_overrides) of a renamed zone or point."""
        old = getattr(app, kind + "s")[index].name
        new = app.rename(kind, index, new_name)
        if new != old and kind in ("zone", "point"):
            for t in self.tests_using(app.name):
                if old in t.zone_overrides:
                    t.zone_overrides[new] = t.zone_overrides.pop(old)
        return new

    def get_animal(self, aid: str) -> Animal | None:
        return next((a for a in self.animals if a.id == aid), None)

    def get_test(self, tid: int) -> Test | None:
        return next((t for t in self.tests if t.id == tid), None)

    def get_group(self, name: str) -> Group | None:
        return next((g for g in self.groups if g.name == name), None)

    def group_color(self, name: str) -> str:
        g = self.get_group(name)
        return g.color if g else "#64748b"

    def next_group_color(self) -> str:
        """The first palette colour no group uses (cycling through the palette once all are used)."""
        used = {g.color.lower() for g in self.groups}
        return next((c for c in GROUP_PALETTE if c not in used), GROUP_PALETTE[len(self.groups) % len(GROUP_PALETTE)])

    def ensure_group(self, name: str) -> Group:
        g = self.get_group(name)
        if g is None:
            g = Group(name, self.next_group_color())
            self.groups.append(g)
        return g

    def next_test_id(self) -> int:
        return max((t.id for t in self.tests), default=0) + 1

    def add_test(self, video: str = "", animal_id: str = "", apparatus: str = "", **kw) -> Test:
        t = Test(id=self.next_test_id(), animal_id=animal_id, video=self.rel_path(video) if video else "",
                 apparatus=apparatus or (self.apparatus[0].name if self.apparatus else ""), **kw)
        self.tests.append(t)
        return t

    def add_stage(self, name: str) -> str:
        """Add the stage to the experiment's stages if it is new. Returns the name."""
        if name and name not in self.stages:
            self.stages.append(name)
        return name

    def rename_stage(self, old: str, new: str) -> int:
        """A stage was renamed: its tests, training criteria and the animals' completed stages follow (stages are
        linked by name). Returns the number of tests moved."""
        if not old or not new or old == new:
            return 0
        self.stages = [new if s == old else s for s in self.stages]
        n = 0
        for t in self.tests:
            if t.stage == old:
                t.stage, n = new, n + 1
        for c in self.training_criteria:
            if isinstance(c, dict) and c.get("stage") == old:
                c["stage"] = new
        for aid, done in (self.settings_extra.get("completed_stages") or {}).items():
            if old in done:
                done[:] = [new if s == old else s for s in done]
        return n

    def key_events(self, name: str) -> tuple[int, int]:
        """(scored events, tests) of a key (behaviour), e.g. to warn before deleting it."""
        counts = [sum(1 for e in t.events if e.get("behaviour") == name) for t in self.tests]
        return sum(counts), sum(1 for c in counts if c)

    def rename_key(self, old: str, new: str) -> int:
        """A key (behaviour) was renamed: its scored events, the time periods marked by it and the training
        criteria on its measures ("Rearing: count") follow (keys are linked by name). Returns the events renamed."""
        if not old or not new or old == new:
            return 0
        n = 0
        for t in self.tests:
            for e in t.events:
                if e.get("behaviour") == old:
                    e["behaviour"], n = new, n + 1
        for d in self.analysis.event_periods:
            for x in (d, d.get("end") if isinstance(d, dict) else None):  # the period's start and its end
                if isinstance(x, dict) and x.get("anchor") == "mark":
                    for k in ("behaviour", "mark"):
                        if x.get(k) == old:
                            x[k] = new
        for c in self.training_criteria:
            m = c.get("measure", "") if isinstance(c, dict) else ""
            if m.startswith(old + ":") or m.startswith(old + " in "):
                c["measure"] = new + m[len(old):]
        return n

    def rename_calculation(self, old: str, new: str, formulas: bool = True, skip: Calculation | None = None):
        """A calculation's results column was renamed: the formulas of the other calculations (but `skip`; not with
        formulas=False), the time periods it starts or ends and the training criteria on it follow."""
        if not old or not new or old == new:
            return
        if formulas:
            for c in self.calculations:
                if c is not skip:
                    c.formula = c.formula.replace("{" + old + "}", "{" + new + "}")
        for d in self.analysis.event_periods:
            for x in (d, d.get("end") if isinstance(d, dict) else None):
                if isinstance(x, dict) and x.get("anchor") == "calculation" and x.get("calculation") == old:
                    x["calculation"] = new
        for c in self.training_criteria:
            if isinstance(c, dict) and c.get("measure") == old:
                c["measure"] = new

    def calculation_users(self, column: str) -> list[str]:
        """What uses a calculation's results: other calculations, time periods and training criteria (as text)."""
        out = [f"calculation “{c.column}”" for c in self.calculations
               if c.column != column and "{" + column + "}" in c.formula]
        out += [f"time period “{d.get('label', '')}”" for d in self.analysis.event_periods
                if isinstance(d, dict) and column in calculation_columns(d)]
        out += [f"training criterion of {('stage “' + c.get('stage') + '”') if c.get('stage') else 'any stage'}"
                for c in self.training_criteria if isinstance(c, dict) and c.get("measure") == column]
        return out

    def ensure_animal(self, aid: str, group: str = "") -> Animal:
        a = self.get_animal(aid)
        if a is None:
            a = Animal(aid, group)
            self.animals.append(a)
        if group:
            self.ensure_group(group)
        return a

    # ---- paths ----------------------------------------------------------------
    def rel_path(self, p: str) -> str:
        if not p or self.path is None:
            return p
        try:
            return os.path.relpath(Path(p).resolve(), self.path.resolve()) if Path(p).is_absolute() else p
        except ValueError:  # different drive on Windows
            return str(p)

    def abs_path(self, p: str) -> str:
        """The absolute path of a stored path (relative to the experiment folder). A video outside the folder
        whose relative path no longer leads to it (the experiment was moved) is looked for at the absolute path saved
        with it."""
        if not p:
            return p
        pp = Path(p)
        if pp.is_absolute() or self.path is None:
            return str(pp)
        out = (self.path / pp).resolve()
        alt = self.video_alternatives.get(p)
        if alt and not out.exists() and Path(alt).exists():
            return alt
        return str(out)

    def missing_videos(self) -> list[Test]:
        """Tests whose video file cannot be found."""
        return [t for t in self.tests if t.video and not Path(self.abs_path(t.video)).exists()]

    def track_path(self, test: Test, animal_index: int = 0) -> Path:
        if self.path is None:
            raise ValueError("Save the project first")
        suffix = "" if animal_index == 0 else f"_a{animal_index + 1}"
        return self.path / "tracks" / f"test_{test.id:04d}{suffix}.csv"

    def start_frame(self, test: Test) -> np.ndarray | None:
        """The video frame at the start of the test (None without a readable video), e.g. under track plots."""
        try:
            with VideoSource(self.abs_path(test.video)) as v:
                return v.frame_at(int(round(test.start_s * v.fps)))
        except Exception:
            return None

    def recordings_dir(self) -> Path:
        d = (self.path or Path.cwd()) / "recordings"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def exports_dir(self) -> Path:
        d = (self.path or Path.cwd()) / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- tracking ------------------------------------------------------------
    def detection_for(self, test: Test) -> DetectionSettings:
        d = self.detection.to_dict()
        d.update(test.detection or {})
        s = DetectionSettings.from_dict(d)
        s.n_animals = test.n_animals
        s.start_time_s = test.start_s
        s.duration_s = test.duration_s or self.test_duration_s or 0.0
        return s

    def has_track(self, test: Test) -> bool:
        return self.path is not None and self.track_path(test).exists()

    def load_tracks(self, test: Test) -> list[Track]:
        out = []
        if self.path is None:
            return out
        for i in range(test.n_animals):
            p = self.track_path(test, i)
            if p.exists():
                tr = Track.from_csv(p)
                tr.meta["animal_index"] = i + 1
                out.append(tr)
        return out

    def save_tracks(self, test: Test, tracks: list[Track]):
        for i, tr in enumerate(tracks):
            p = self.track_path(test, i)
            p.parent.mkdir(parents=True, exist_ok=True)
            tr.to_csv(p)
        test.status = "tracked"
        if self.current_user and not test.experimenter:
            test.experimenter = self.current_user

    def track_test(self, test: Test, progress: Callable[[float], None] | None = None,
                   should_stop: Callable[[], bool] | None = None, frame_callback=None) -> list[Track]:
        app = self.apparatus_of(test)
        settings = self.detection_for(test)
        video = self.abs_path(test.video)
        if self.start_mode in ("on_detection", "experimenter_leaves"):
            dur = settings.duration_s
            settings.duration_s = 0.0
            raw = track_video(video, [ArenaJob(app, settings)], progress, should_stop, frame_callback)[0]
            t0 = None
            if self.start_mode == "experimenter_leaves" and raw:
                from .autostart import experimenter_leaves_start

                t0 = experimenter_leaves_start(raw[0], area_px=settings.max_area_px or 0)
            tracks = _trim_all_on_detection(raw, dur, t0)
            if self.start_mode == "experimenter_leaves":
                for tr in tracks:
                    tr.meta["start"] = ("experimenter left" if t0 is not None else
                                        "first detection (no experimenter seen)")
        else:
            tracks = track_video(video, [ArenaJob(app, settings)], progress, should_stop, frame_callback)[0]
        self.save_tracks(test, tracks)
        return tracks

    def track_video_tests(self, tests: list[Test], progress=None, should_stop=None) -> dict[int, list[Track]]:
        """Track several tests sharing one video (multiple apparatus) in a single pass."""
        if not tests:
            return {}
        video = self.abs_path(tests[0].video)
        jobs = [ArenaJob(self.apparatus_of(t), self.detection_for(t)) for t in tests]
        res = track_video(video, jobs, progress, should_stop)
        out = {}
        for t, tracks in zip(tests, res):
            self.save_tracks(t, tracks)
            out[t.id] = tracks
        return out

    # ---- analysis -------------------------------------------------------------
    def analysis_for(self, test: Test) -> AnalysisSettings:
        s = AnalysisSettings.from_dict(self.analysis.to_dict())
        v = test.variables or {}
        if "novel_object" in v:
            s.novel_object = v["novel_object"]
        if "social_side" in v:
            s.social_side = v["social_side"]
        if "paired_chamber" in v:
            s.paired_chamber = v["paired_chamber"]
        return s

    def test_periods(self, test: Test, track: Track, app: Apparatus | None = None) -> list[tuple[str, float, float]]:
        """The time bins, custom and event-anchored periods of a test, in test time (as the segmented results).
        app: the test's apparatus without its per-test zone positions (default: the test's). Periods defined by
        calculations analyse the track for their results."""
        app = app or self.get_apparatus(test.apparatus)
        s = self.analysis_for(test)
        calc = None
        if uses_calculations(s.event_periods) and app is not None:
            calc = analyse(track, app, s, **self._analysis_kw(test, [track], 0, self.calculation_steps()))
        return all_periods(track, app, s, None, test.events, test.io_events, test.zone_overrides, test.pauses,
                           calc=calc)

    def test_info(self, test: Test, animal_id: str | None = None) -> dict:
        """The information columns of a results row (INFO_COLUMNS and the animal fields). The columns that come
        from the track (animal contrast and length, frames tracked, video times) are filled by :meth:`track_info`."""
        aid = animal_id if animal_id is not None else test.animal_id
        a = self.get_animal(aid)
        # testing blind: the treatment's code, never its name (as the Experiment page shows it)
        group = (self.treatment_code(a.group) or ("??" if a.group else "")) if a and self.blind else \
            (a.group if a else "")
        info = {"Test": test.id, "Animal": aid, "Group": group,
                "Treatment code": self.treatment_code(a.group) if a else "", "Sex": a.sex if a else "",
                "Animal notes": a.notes if a else "", "Stage": test.stage, "Trial": test.trial,
                "Apparatus": test.apparatus, "Test date": "", "Day of week": "", "Test time": "", "Time of day": "",
                "User": test.experimenter or "", "Test notes": test.notes or "",
                "Reason for test end": test.end_reason or "", "Animal lighter / darker": "", "Animal length": "",
                "Frames tracked (%)": "", "Jumps removed": "", "Source video file": "", "Recorded video file": "",
                "Video time at test start (s)": "", "Moveable zone positions": moveable_zone_text(test.zone_overrides)}
        try:
            when = _dt.datetime.fromisoformat(test.recorded_at) if test.recorded_at else None
        except ValueError:
            when = None
        if when is not None:
            info.update({"Test date": when.date().isoformat(), "Day of week": when.strftime("%A"),
                         "Test time": when.strftime("%H:%M:%S"), "Time of day": time_of_day(when)})
        if test.video and not test.end_reason:  # a video tracked or scored afterwards
            info["Source video file"] = self.abs_path(test.video)
            info["Video time at test start (s)"] = round(float(test.start_s), 3)
        elif test.video:  # recorded live
            info["Recorded video file"] = self.abs_path(test.video)
            info["Video time at test start (s)"] = 0.0
        if a:
            for f in self.animal_fields:
                info[f] = a.fields.get(f, "")
        return info

    def treatment_code(self, group: str) -> str:
        """The treatment's code as in the Animals sheet: its blind code while testing blind (already assigned codes
        only: results may be computed off the UI thread), else a letter A, B, … in treatment order."""
        if not group:
            return ""
        if self.blind:
            return str(self.settings_extra.get("blind_codes", {}).get(group, ""))
        names = [g.name for g in self.groups]
        if group not in names:
            return ""
        n, s = names.index(group) + 1, ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def track_info(self, test: Test, track: Track, app: Apparatus | None = None) -> dict:
        """Information columns taken from a test's track: whether the animal is lighter or darker than the apparatus
        (as detected; else the detection setting), its body length (apparatus units), the percentage of frames in
        which it was detected, the jumps removed, the video times, and "Animal reached the end zone" when the analysis
        ended the test there (AnalysisSettings.end_zone)."""
        from .measures import body_length, end_of_test
        from .pauses import drop_pauses

        out = {}
        contrast = str(track.meta.get("animal_contrast", "") or "")
        if not contrast:
            c = self.detection_for(test)
            contrast = "" if c.method == "colour" else {"dark": "darker", "light": "lighter"}.get(c.contrast, "")
        out["Animal lighter / darker"] = contrast.capitalize()
        app = app or self.get_apparatus(test.apparatus)
        n = len(track)
        if n:
            L = body_length(track, app.scale if app is not None else 1.0)
            out["Animal length"] = round(float(L), 2) if math.isfinite(L) else ""
            out["Frames tracked (%)"] = round(100.0 * float(np.count_nonzero(track.detected)) / n, 2)
        try:  # positions removed as jumps by tracking or in Review (blank when jump removal was off)
            out["Jumps removed"] = int(float(track.meta["jumps_removed"]))
        except (KeyError, TypeError, ValueError):
            pass
        if track.meta.get("source") == "live" or test.end_reason:  # recorded live (the video, if any, is its recording)
            out.update({"Source video file": "", "Recorded video file": self.abs_path(test.video) if test.video else "",
                        "Video time at test start (s)": 0.0 if test.video else ""})
        elif track.meta.get("video") or test.video:
            out["Source video file"] = self.abs_path(test.video) if test.video else str(track.meta["video"])
            try:
                out["Video time at test start (s)"] = round(float(track.meta.get("video_start_s", test.start_s)), 3)
            except (TypeError, ValueError):
                out["Video time at test start (s)"] = round(float(test.start_s), 3)
        s = self.analysis_for(test)
        if s.end_zone and app is not None and n:
            try:
                if end_of_test(drop_pauses(track, test.pauses)[0], self.apparatus_of(test), s) is not None:
                    out["Reason for test end"] = END_ZONE
            except Exception:  # a zone that no longer exists, a malformed track …: no reason rather than no results
                pass
        return out

    def analyse_test(self, test: Test, segmented: bool = False, deferred: bool = True) -> list[dict]:
        """Rows of results for a test: one per animal (and per time period if segmented). Calculations that use the
        other trials are worked out with the results of the animal's other tests (analysed for this); with
        deferred=False they are left NaN, for finish_calculations() on the rows of several tests."""
        steps = self.calculation_steps()
        rows = self._analyse_test(test, segmented, steps)
        if deferred:
            self._deferred_calculations(rows, segmented, steps)
        return rows

    def finish_calculations(self, rows: list[dict], segmented: bool = False) -> list[dict]:
        """Work out, in place, the calculations of rows from analyse_test(..., deferred=False) that need the rest
        of the experiment (other trials, information columns). Returns the rows."""
        self._deferred_calculations(rows, segmented, self.calculation_steps())
        return rows

    def _analysis_kw(self, test: Test, tracks: list[Track], i: int, steps=None) -> dict:
        """analyse() keywords for animal i of a test (scored events, keys and result variables: the first animal)."""
        others = [o for j, o in enumerate(tracks) if j != i]
        return dict(events=test.events if i == 0 else [], behaviours=self.behaviours if i == 0 else None,
                    other_tracks=others or None, zone_overrides=test.zone_overrides or None,
                    io_events=test.io_events or None, pauses=test.pauses or None,
                    io_devices=self.io_devices or None,
                    result_variables=test.result_variables if i == 0 else None, calculations=steps or None)

    def _scored_duration(self, test: Test) -> float:
        """Length of a test without a track (scored by hand, or I/O only): its duration, else the protocol's, else
        its last event."""
        dur = test.duration_s or self.test_duration_s
        if not dur or dur <= 0:
            dur = max([e["t_end"] if e.get("t_end") is not None else e["t"] for e in test.events] +
                      [float(e.get("t", 0) or 0) for e in test.io_events], default=0.0)
        return dur

    @staticmethod
    def _io_only(test: Test) -> bool:
        """A test without a track analysed from its I/O log (ANY-maze's I/O only mode), not only scored keys."""
        return bool(test.io_events or test.result_variables)

    def _untracked_periods(self, test: Test, calc: dict | None = None) -> list[Period]:
        """The time periods of a test without a track (calc: its whole-test results, for periods defined by
        calculations)."""
        dur, s = self._scored_duration(test), self.analysis_for(test)
        if not self._io_only(test):
            return [Period(label, a, b) for label, a, b in time_periods(dur, s)]
        return io_only_periods(dur, s, test.events, test.io_events, self.get_apparatus(test.apparatus), test.pauses,
                               calc=calc, resolved=True)

    def _untracked_measures(self, test: Test, t_range=None) -> dict | None:
        """Measures of a test without a track, whole or for a period (None: the test ended before it)."""
        dur = self._scored_duration(test)
        if t_range is not None and t_range[0] >= dur:
            return None
        if not self._io_only(test):  # TakeNote: the scored keys
            a, b = (0.0, dur) if t_range is None else (t_range[0], min(t_range[1], dur))
            return behaviour_measures(test.events, self.behaviours, a, b)
        return io_only_measures(dur, self.analysis_for(test), test.io_events, self.io_devices or None, test.events,
                                self.behaviours, test.result_variables, t_range, test.pauses)

    def _scored_period(self, test: Test, spec, calc: dict | None = None) -> dict | None:
        """result_for_period() of a test without a track: its measures for a part of the test (a time period's
        name: the test's time periods; calc: its whole-test results, for periods defined by calculations)."""
        if isinstance(spec, str):
            spec = next(((p.t0, p.t1) for p in self._untracked_periods(test, calc) if p.label == spec), None)
            if spec is None:
                return None
        return self._untracked_measures(test, tuple(spec))

    def _untracked_rows(self, test: Test, segmented: bool, steps=None) -> list[dict]:
        """Results of a test without a track: keys scored by hand (TakeNote; whole test) or the I/O log (I/O only
        mode; with its time periods when segmented)."""
        whole: dict = {}  # the whole test's results as they are worked out (for periods defined by calculations)

        def period(spec):
            return self._scored_period(test, spec, whole)

        row = self.test_info(test)
        row.update({"Period": "Whole test", "Segment of test": ""})
        row.update(self._untracked_measures(test))
        if steps:
            row.update(evaluate_test(steps, row, period, view=whole))
        rows = [row]
        if not (segmented and self._io_only(test)):
            return rows
        defs = self.analysis_for(test).event_periods
        for k, per in enumerate(self._untracked_periods(test, whole), 1):
            res = self._untracked_measures(test, (per.t0, per.t1))
            if res is None:
                continue
            row = self.test_info(test)
            row["Period"] = per.label
            row["Segment of test"] = k  # ANY-maze "segment of test": 1, 2, …
            row.update(add_warning(res, no_end_warning(per, defs)))
            if steps:
                row.update(evaluate_test(steps, row, period))
            rows.append(row)
        return rows

    def _analyse_test(self, test: Test, segmented: bool, steps=None) -> list[dict]:
        tracks = self.load_tracks(test)
        app = self.get_apparatus(test.apparatus)
        s = self.analysis_for(test)
        rows = []
        behaviours = self.behaviours
        ids = [test.animal_id] + list(test.extra_animals)
        if not tracks and (test.events and behaviours or self._io_only(test)):
            # manual scoring only (TakeNote / observation, or a video scored without tracking), or a test run with
            # the I/O devices only
            return self._untracked_rows(test, segmented, steps)
        for i, tr in enumerate(tracks):
            kw = self._analysis_kw(test, tracks, i, steps)
            if segmented:
                parts = analyse_segmented(tr, app, s, **kw)
            else:
                parts = [("Whole test", analyse(tr, app, s, **kw))]
            info = self.test_info(test, ids[i] if i < len(ids) else f"{test.animal_id}#{i + 1}")
            info.update(self.track_info(test, tr, app))
            k = 0
            for label, res in parts:
                row = dict(info)
                row["Period"] = label
                # ANY-maze "segment of test": 1, 2, … for the periods of a segmented analysis; blank: whole test
                if label == "Whole test":
                    row["Segment of test"] = ""
                else:
                    k += 1
                    row["Segment of test"] = k
                row.update(res)
                rows.append(row)
        return rows

    def calculation_steps(self) -> list:
        """The calculations in the order they are worked out (calculations.plan), deferred to the whole experiment
        when they use the other trials or the information columns; calculations named like an information column
        are left out (their results would replace it)."""
        if not self.calculations:
            return []
        info = set(INFO_COLUMNS) | set(self.animal_fields) | {ERROR_COLUMN}
        return plan([c for c in self.calculations if c.column and c.column not in info | {"Warnings"}], info,
                    self.analysis.event_periods)

    def _deferred_calculations(self, rows: list[dict], segmented: bool, steps) -> None:
        """Work out the deferred calculations of result rows in place: with the information columns and, for the
        trial functions, the results of each animal's trials (the same time period; the animals' other tests are
        analysed when they are not among the rows)."""
        todo = [s for s in steps if s.deferred and s.ok]
        if not todo or not rows:
            return
        every = list(rows)
        if any(parse(s.calc.formula).functions - {"result_for_period"} for s in todo):
            have = {r.get("Test") for r in rows}
            animals = {str(r.get("Animal")) for r in rows}
            for t in self.tests:
                if t.id in have or t.status in INACTIVE_STATUSES or not self.has_results(t) or \
                        not animals & {str(a) for a in [t.animal_id, *t.extra_animals]}:
                    continue
                try:
                    every.extend(self._analyse_test(t, segmented, steps))
                except Exception as e:  # an unreadable track: that trial has no results
                    log.warning("analysis of test %s failed: %s", t.id, e)
        every = [r for r in every if ERROR_COLUMN not in r]
        groups: dict[tuple, list] = {}
        for r in every:
            groups.setdefault((str(r.get("Animal")), r.get("Period")), []).append(
                (r.get("Stage", ""), r.get("Trial", 1), r))
        trials = {k: Trials(v, self.stages) for k, v in groups.items()}
        # the whole-test row of each test and animal: the time periods defined by calculations use its results
        whole = {(r.get("Test"), str(r.get("Animal"))): r for r in every
                 if r.get("Period", "Whole test") == "Whole test"}
        cache: dict = {}
        for s in todo:
            for r in every:
                w = whole.get((r.get("Test"), str(r.get("Animal"))))
                r[s.calc.column] = evaluate_calc(s.calc, r, lambda spec, r=r, w=w: self._calc_period(r, spec, cache, w),
                                                 trials.get((str(r.get("Animal")), r.get("Period"))))

    def _calc_period(self, row: dict, spec, cache: dict, whole: dict | None = None) -> dict | None:
        """result_for_period() of a results row in the deferred calculations: its test analysed for part of the
        test (cached per test, animal and period; whole: the test's whole-test row, for periods defined by
        calculations)."""
        key = (row.get("Test"), str(row.get("Animal")), spec)
        if key not in cache:
            cache[key] = None
            test = self.get_test(row.get("Test"))
            if test is not None:
                tracks = self.load_tracks(test)
                if not tracks:
                    cache[key] = self._scored_period(test, spec, whole) if self.has_results(test) else None
                else:
                    ids = [test.animal_id] + list(test.extra_animals)
                    i = ids.index(row.get("Animal")) if row.get("Animal") in ids else 0
                    if i < len(tracks):
                        cache[key] = analyse_period(tracks[i], self.get_apparatus(test.apparatus),
                                                    self.analysis_for(test), spec, calc_values=whole,
                                                    **self._analysis_kw(test, tracks, i))
        return cache[key]

    def has_results(self, test: Test) -> bool:
        """The test has data to analyse: a track, manually scored events, or an I/O log (I/O only mode)."""
        return self.has_track(test) or bool(test.events and self.behaviours) or self._io_only(test)

    def results(self, tests: list[Test] | None = None, segmented: bool = False,
                progress: Callable[[float], None] | None = None) -> list[dict]:
        tests = [t for t in (tests if tests is not None else self.tests) if t.status not in INACTIVE_STATUSES]
        rows = []
        steps = self.calculation_steps()
        for i, t in enumerate(tests):
            if self.has_results(t):
                try:
                    rows.extend(self._analyse_test(t, segmented, steps))
                except Exception as e:  # one unreadable track must not lose the results of every other test
                    log.warning("analysis of test %s failed: %s", t.id, e)
                    rows.append(self.error_row(t, e))
            if progress:
                progress((i + 1) / max(1, len(tests)))
        self._deferred_calculations(rows, segmented, steps)
        return rows

    def info_columns(self) -> list[str]:
        """The information columns of the results: INFO_COLUMNS, then the animal columns."""
        return INFO_COLUMNS + [f for f in self.animal_fields if f not in INFO_COLUMNS]

    def report_table(self, name: str, progress: Callable[[float], None] | None = None
                     ) -> tuple[list[dict], list[str]]:
        """The rows and columns of the saved results report called `name` (see reports.py), as the Data page shows
        them; KeyError if the experiment has no such report."""
        rep = find_report(self.reports, name)
        if rep is None:
            raise KeyError(name)
        rows = report_rows(rep, self.results(segmented=rep["segmented"], progress=progress))
        return rows, report_columns(rep, rows, self.info_columns(), OPTIONAL_INFO_COLUMNS)

    def error_row(self, test: Test, error: Exception) -> dict:
        """A results row noting that a test could not be analysed (its information columns and the error)."""
        try:
            row = self.test_info(test)
        except Exception:
            row = {"Test": test.id, "Animal": test.animal_id}
        row.update({"Period": "Whole test", "Segment of test": "", ERROR_COLUMN: f"{type(error).__name__}: {error}"})
        return row


def same_folder(a, b) -> bool:
    """Two paths are the same folder (also when spelt with another letter case on a case-insensitive disk, through
    a symbolic link or as a relative path)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def without_secrets(device: dict) -> dict:
    """An I/O device configuration without its passwords and tokens (see ioconfig.is_secret)."""
    return {k: v for k, v in device.items() if not is_secret(k)}


def _finite_json(o):
    """o with NaN / infinite floats replaced by None (JSON has no NaN: other programs reject such a file)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite_json(v) for v in o]
    if isinstance(o, np.generic):
        return _finite_json(o.item())
    return o


def dumps_json(d: dict) -> str:
    """Experiment JSON text: standard JSON (missing / infinite numbers as null). Files with NaN / Infinity
    written by older versions are still read."""
    return json.dumps(_finite_json(d), indent=1)


def relink_videos(project: "Project", folder, tests: list[Test] | None = None) -> dict:
    """Find the missing videos of tests (default: every test whose video is missing) under ``folder`` and its
    subfolders by file name (letter case ignored) and point the tests to them. When several files have the name, the
    one whose parent folders best match the stored path is used; a tie is left alone.

    Returns {"relinked": {test id: new path}, "not_found": [test ids], "ambiguous": [test ids]}. Save the
    experiment to keep the new paths."""
    folder = Path(folder)
    todo = [t for t in (tests if tests is not None else project.missing_videos()) if t.video]
    out = {"relinked": {}, "not_found": [], "ambiguous": []}
    if not todo:
        return out
    index: dict[str, list[Path]] = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            index.setdefault(f.lower(), []).append(Path(root) / f)
    for t in todo:
        parts = [x for x in re.split(r"[\\/]", t.video) if x and x not in (".", "..")]
        cands = index.get(parts[-1].lower(), []) if parts else []
        if not cands:
            out["not_found"].append(t.id)
            continue

        def score(c: Path) -> int:  # number of trailing path parts in common
            n = 0
            for a, b in zip(reversed(parts), reversed(c.parts)):
                if a.lower() != b.lower():
                    break
                n += 1
            return n

        ranked = sorted(((score(c), c) for c in cands), key=lambda x: -x[0])
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            out["ambiguous"].append(t.id)
            continue
        t.video = project.rel_path(str(ranked[0][1]))
        out["relinked"][t.id] = str(ranked[0][1])
    return out


def _trim_all_on_detection(tracks: list[Track], duration: float, t0: float | None = None) -> list[Track]:
    """Trim every animal of a test at t0, by default the earliest first detection of any of them (keeps tracks
    aligned)."""
    if t0 is None:
        firsts = [float(tr.t[np.flatnonzero(tr.detected)[0]]) for tr in tracks if len(tr) and tr.detected.any()]
        if not firsts:
            return list(tracks)
        t0 = min(firsts)
    return [_trim_on_detection(tr, duration, t0) for tr in tracks]


def _trim_on_detection(tr: Track, duration: float, t0: float | None = None) -> Track:
    """Start the test at t0 (default: this track's first detection) and keep `duration` seconds.

    Multi-animal tests pass the earliest first detection of all animals so their tracks stay aligned frame by
    frame (social measures compare them by index)."""
    if t0 is None:
        idx = np.flatnonzero(tr.detected)
        if len(idx) == 0:
            return tr
        t0 = tr.t[idx[0]]
    end = t0 + duration if duration and duration > 0 else math.inf
    out = tr.slice_time(t0, end)
    out.t = out.t - t0
    out.meta["video_start_s"] = float(tr.meta.get("video_start_s", 0) or 0) + float(t0)
    return out


def time_of_day(when: _dt.datetime) -> str:
    """Morning (5–12 h), Afternoon (12–17 h), Evening (17–21 h) or Night."""
    name = TIME_OF_DAY[0][1]
    for hour, label in TIME_OF_DAY:
        if when.hour >= hour:
            name = label
    return name


def moveable_zone_text(overrides: dict | None) -> str:
    """The per-test positions of moveable zones and points (Test.zone_overrides) as text: "Object: 312, 140 px;
    Platform: 80, 95 px" (zone centres / point positions in video pixels; a moved apparatus map is listed too)."""
    from .apparatus import POSITION_KEY
    from .geometry import shape_from_dict

    parts = []
    for name, v in (overrides or {}).items():
        if not isinstance(v, dict):
            continue
        if name == POSITION_KEY:
            dx, dy = float(v.get("dx", 0) or 0), float(v.get("dy", 0) or 0)
            ang, sc = float(v.get("angle", 0) or 0), float(v.get("scale", 1) or 1)
            txt = f"Apparatus moved by {dx:.0f}, {dy:.0f} px"
            if ang:
                txt += f", rotated {ang:g}°"
            if sc != 1:
                txt += f", scaled ×{sc:g}"
            parts.append(txt)
            continue
        try:
            x, y = (float(v["x"]), float(v["y"])) if "type" not in v else shape_from_dict(v).centroid()
        except (KeyError, TypeError, ValueError):
            continue
        parts.append(f"{name}: {x:.0f}, {y:.0f} px")
    return "; ".join(parts)


def result_columns(rows: list[dict]) -> list[str]:
    """Union of keys across rows preserving first-seen order."""
    cols: list[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return cols
