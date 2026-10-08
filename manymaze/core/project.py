"""Experiment (project) model and persistence.

A project is a directory (``Name.mmaze``) containing ``project.json`` plus
``tracks/`` (per-test track CSVs), ``recordings/`` (videos captured live) and
``exports/``.  Video paths are stored relative to the project when possible so
projects can be moved between machines.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .apparatus import Apparatus, from_known
from .measures import AnalysisSettings, all_periods, analyse, analyse_segmented, behaviour_measures
from .session import END_ZONE
from .templates import apply_overrides
from .track import Track
from .tracking import ArenaJob, DetectionSettings, track_video
from .video import VideoSource

PROJECT_FILE = "project.json"
BACKUP_DIR = "backups"
BACKUP_INTERVAL_S = 600.0  # at most one automatic backup per 10 minutes of saving
BACKUP_KEEP = 30
FORMAT_VERSION = 1
# columns of a results row that describe the test rather than measure it (animal fields are added to these)
INFO_COLUMNS = ["Test", "Animal", "Group", "Treatment code", "Sex", "Animal notes", "Stage", "Trial", "Apparatus",
                "Test date", "Day of week", "Test time", "Time of day", "User", "Test notes", "Reason for test end",
                "Animal lighter / darker", "Animal length", "Frames tracked (%)", "Source video file",
                "Recorded video file", "Video time at test start (s)", "Moveable zone positions", "Period",
                "Segment of test"]
# information columns not shown in the results table until ticked in its column chooser (rarely needed)
OPTIONAL_INFO_COLUMNS = ["Treatment code", "Animal notes", "Time of day", "Reason for test end",
                         "Animal lighter / darker", "Animal length", "Frames tracked (%)", "Source video file",
                         "Recorded video file", "Video time at test start (s)", "Moveable zone positions",
                         "Segment of test"]
# ANY-maze "time of day" of a live test: (first hour, name), the name of the last band whose hour has passed
TIME_OF_DAY = ((0, "Night"), (5, "Morning"), (12, "Afternoon"), (17, "Evening"), (21, "Night"))


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
    blind: bool = False  # hide group / treatment while testing and scoring
    experimenters: list = field(default_factory=list)  # user names offered as the current user / test experimenter
    settings_extra: dict = field(default_factory=dict)  # misc. UI / workflow settings
    created: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    path: Path | None = None
    current_user: str = ""  # who is using the app (not saved: set by the GUI); stamped on tests run or tracked

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
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "tracks").mkdir(exist_ok=True)
        tmp = self.path / (PROJECT_FILE + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1))
        if self.settings_extra.get("backups", True):
            self.backup(min_interval_s=BACKUP_INTERVAL_S)
        os.replace(tmp, self.path / PROJECT_FILE)

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
        self.backup()
        return Project.from_dict(json.loads(Path(backup).read_text()), self.path)

    def to_dict(self) -> dict:
        return {
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
            "tests": [asdict(t) for t in self.tests],
            "animal_fields": self.animal_fields,
            "stages": self.stages,
            "procedures": self.procedures,
            "io_devices": self.io_devices,
            "variables": self.variables,
            "training_criteria": self.training_criteria,
            "blind": self.blind,
            "experimenters": self.experimenters,
            "settings_extra": self.settings_extra,
            "created": self.created,
        }

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        pdir = cls.project_dir(path)
        return cls.from_dict(json.loads((pdir / PROJECT_FILE).read_text()), pdir)

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
            blind=d.get("blind", False),
            experimenters=[str(u) for u in d.get("experimenters", []) if str(u).strip()],
            settings_extra=d.get("settings_extra", {}),
            created=d.get("created", ""),
        )
        p.path = pdir
        return p

    # ---- lookup -----------------------------------------------------------
    def get_apparatus(self, name: str) -> Apparatus | None:
        return next((a for a in self.apparatus if a.name == name), self.apparatus[0] if self.apparatus else None)

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
        if not p:
            return p
        pp = Path(p)
        if pp.is_absolute() or self.path is None:
            return str(pp)
        return str((self.path / pp).resolve())

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
        app: the test's apparatus without its per-test zone positions (default: the test's)."""
        return all_periods(track, app or self.get_apparatus(test.apparatus), self.analysis_for(test), None,
                           test.events, test.io_events, test.zone_overrides, test.pauses)

    def test_info(self, test: Test, animal_id: str | None = None) -> dict:
        """The information columns of a results row (INFO_COLUMNS and the animal fields). The columns that come
        from the track (animal contrast and length, frames tracked, video times) are filled by :meth:`track_info`."""
        aid = animal_id if animal_id is not None else test.animal_id
        a = self.get_animal(aid)
        info = {"Test": test.id, "Animal": aid, "Group": a.group if a else "",
                "Treatment code": self.treatment_code(a.group) if a else "", "Sex": a.sex if a else "",
                "Animal notes": a.notes if a else "", "Stage": test.stage, "Trial": test.trial,
                "Apparatus": test.apparatus, "Test date": "", "Day of week": "", "Test time": "", "Time of day": "",
                "User": test.experimenter or "", "Test notes": test.notes or "",
                "Reason for test end": test.end_reason or "", "Animal lighter / darker": "", "Animal length": "",
                "Frames tracked (%)": "", "Source video file": "", "Recorded video file": "",
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
        which it was detected, the video times, and "Animal reached the end zone" when the analysis ended the test
        there (AnalysisSettings.end_zone)."""
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

    def analyse_test(self, test: Test, segmented: bool = False) -> list[dict]:
        """Rows of results for a test: one per animal (and per time period if segmented)."""
        tracks = self.load_tracks(test)
        app = self.get_apparatus(test.apparatus)
        s = self.analysis_for(test)
        rows = []
        behaviours = self.behaviours
        ids = [test.animal_id] + list(test.extra_animals)
        if not tracks and test.events and behaviours:
            # manual scoring only (TakeNote / observation, or a video scored without tracking)
            dur = test.duration_s or self.test_duration_s
            if not dur or dur <= 0:
                dur = max((e["t_end"] if e.get("t_end") is not None else e["t"] for e in test.events), default=0.0)
            row = self.test_info(test)
            row["Period"] = "Whole test"
            row["Segment of test"] = ""
            row.update(behaviour_measures(test.events, behaviours, 0.0, dur))
            return [row]
        for i, tr in enumerate(tracks):
            others = [o for j, o in enumerate(tracks) if j != i]
            kw = dict(events=test.events if i == 0 else [], behaviours=behaviours if i == 0 else None,
                      other_tracks=others or None, zone_overrides=test.zone_overrides or None,
                      io_events=test.io_events or None, pauses=test.pauses or None,
                      io_devices=self.io_devices or None,
                      result_variables=test.result_variables if i == 0 else None)
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

    def has_results(self, test: Test) -> bool:
        """The test has data to analyse: a track, or manually scored events."""
        return self.has_track(test) or bool(test.events and self.behaviours)

    def results(self, tests: list[Test] | None = None, segmented: bool = False,
                progress: Callable[[float], None] | None = None) -> list[dict]:
        tests = [t for t in (tests if tests is not None else self.tests) if t.status not in INACTIVE_STATUSES]
        rows = []
        for i, t in enumerate(tests):
            if self.has_results(t):
                rows.extend(self.analyse_test(t, segmented))
            if progress:
                progress((i + 1) / max(1, len(tests)))
        return rows


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
