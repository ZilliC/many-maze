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

from .apparatus import Apparatus
from .measures import AnalysisSettings, analyse, analyse_segmented, behaviour_measures
from .track import Track
from .tracking import ArenaJob, DetectionSettings, track_video

PROJECT_FILE = "project.json"
FORMAT_VERSION = 1


@dataclass
class Animal:
    id: str
    group: str = ""
    sex: str = ""
    fields: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        return cls(str(d["id"]), d.get("group", ""), d.get("sex", ""), dict(d.get("fields", {})))


@dataclass
class Group:
    name: str
    color: str = "#3b82f6"


@dataclass
class Behaviour:
    """A manually scored behaviour. kind = "state" (has duration) or "point" (instantaneous)."""

    name: str
    key: str = ""
    kind: str = "state"


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
    status: str = "pending"  # pending | tracked | excluded
    notes: str = ""
    recorded_at: str = ""
    io_events: list = field(default_factory=list)  # live I/O log [{"t", "device", "channel", "kind": "input"|"output", "value"}]
    result_variables: dict = field(default_factory=dict)  # numeric procedure variables saved as results
    zone_overrides: dict = field(default_factory=dict)  # moveable zones: {zone name: shape dict} for this test
    pauses: list = field(default_factory=list)  # [[t_start, t_end], ...] test-time intervals the test was paused

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @property
    def n_animals(self) -> int:
        return 1 + len(self.extra_animals)


@dataclass
class Project:
    name: str = "Untitled experiment"
    description: str = ""
    protocol: str = "open_field"
    test_duration_s: float = 300.0
    start_mode: str = "manual"  # "manual" (start_s) | "on_detection" (first frame the animal is in the arena)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    apparatus: list = field(default_factory=list)  # list[Apparatus]
    animals: list = field(default_factory=list)  # list[Animal]
    groups: list = field(default_factory=list)  # list[Group]
    behaviours: list = field(default_factory=list)  # list[Behaviour]
    tests: list = field(default_factory=list)  # list[Test]
    animal_fields: list = field(default_factory=list)  # extra animal column names
    stages: list = field(default_factory=list)
    procedures: list = field(default_factory=list)  # live procedures (see procedures.py)
    io_devices: list = field(default_factory=list)  # I/O device configurations (see iodevices.py)
    variables: dict = field(default_factory=dict)  # procedure variables kept between tests
    training_criteria: list = field(default_factory=list)  # per-stage criteria (see project workflow)
    blind: bool = False  # hide group / treatment while testing and scoring
    settings_extra: dict = field(default_factory=dict)  # misc. UI / workflow settings
    created: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    path: Path | None = None

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
        os.replace(tmp, self.path / PROJECT_FILE)

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
            protocol=d.get("protocol", "custom"),
            test_duration_s=d.get("test_duration_s", 300.0),
            start_mode=d.get("start_mode", "manual"),
            detection=DetectionSettings.from_dict(d.get("detection")),
            analysis=AnalysisSettings.from_dict(d.get("analysis")),
            apparatus=[Apparatus.from_dict(a) for a in d.get("apparatus", [])],
            animals=[Animal.from_dict(a) for a in d.get("animals", [])],
            groups=[Group(**g) for g in d.get("groups", [])],
            behaviours=[Behaviour(**b) for b in d.get("behaviours", [])],
            tests=[Test.from_dict(t) for t in d.get("tests", [])],
            animal_fields=d.get("animal_fields", []),
            stages=d.get("stages", []),
            procedures=d.get("procedures", []),
            io_devices=d.get("io_devices", []),
            variables=d.get("variables", {}),
            training_criteria=d.get("training_criteria", []),
            blind=d.get("blind", False),
            settings_extra=d.get("settings_extra", {}),
            created=d.get("created", ""),
        )
        p.path = pdir
        return p

    # ---- lookup -----------------------------------------------------------
    def get_apparatus(self, name: str) -> Apparatus | None:
        return next((a for a in self.apparatus if a.name == name), self.apparatus[0] if self.apparatus else None)

    def get_animal(self, aid: str) -> Animal | None:
        return next((a for a in self.animals if a.id == aid), None)

    def get_test(self, tid: int) -> Test | None:
        return next((t for t in self.tests if t.id == tid), None)

    def group_color(self, name: str) -> str:
        g = next((g for g in self.groups if g.name == name), None)
        return g.color if g else "#64748b"

    def next_test_id(self) -> int:
        return max((t.id for t in self.tests), default=0) + 1

    def add_test(self, video: str = "", animal_id: str = "", apparatus: str = "", **kw) -> Test:
        t = Test(id=self.next_test_id(), animal_id=animal_id, video=self.rel_path(video) if video else "",
                 apparatus=apparatus or (self.apparatus[0].name if self.apparatus else ""), **kw)
        self.tests.append(t)
        return t

    def ensure_animal(self, aid: str, group: str = "") -> Animal:
        a = self.get_animal(aid)
        if a is None:
            a = Animal(aid, group)
            self.animals.append(a)
        if group and not any(g.name == group for g in self.groups):
            palette = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6"]
            self.groups.append(Group(group, palette[len(self.groups) % len(palette)]))
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

    def track_test(self, test: Test, progress: Callable[[float], None] | None = None,
                   should_stop: Callable[[], bool] | None = None, frame_callback=None) -> list[Track]:
        app = self.get_apparatus(test.apparatus)
        settings = self.detection_for(test)
        video = self.abs_path(test.video)
        if self.start_mode == "on_detection":
            dur = settings.duration_s
            settings.duration_s = 0.0
            raw = track_video(video, [ArenaJob(app, settings)], progress, should_stop, frame_callback)[0]
            raw = [_trim_on_detection(tr, dur) for tr in raw]
            tracks = raw
        else:
            tracks = track_video(video, [ArenaJob(app, settings)], progress, should_stop, frame_callback)[0]
        self.save_tracks(test, tracks)
        return tracks

    def track_video_tests(self, tests: list[Test], progress=None, should_stop=None) -> dict[int, list[Track]]:
        """Track several tests sharing one video (multiple apparatus) in a single pass."""
        if not tests:
            return {}
        video = self.abs_path(tests[0].video)
        jobs = [ArenaJob(self.get_apparatus(t.apparatus), self.detection_for(t)) for t in tests]
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
        return s

    def test_info(self, test: Test, animal_id: str | None = None) -> dict:
        aid = animal_id if animal_id is not None else test.animal_id
        a = self.get_animal(aid)
        info = {"Test": test.id, "Animal": aid, "Group": a.group if a else "", "Sex": a.sex if a else "",
                "Stage": test.stage, "Trial": test.trial, "Apparatus": test.apparatus}
        if a:
            for f in self.animal_fields:
                info[f] = a.fields.get(f, "")
        return info

    def analyse_test(self, test: Test, segmented: bool = False) -> list[dict]:
        """Rows of results for a test: one per animal (and per time period if segmented)."""
        tracks = self.load_tracks(test)
        app = self.get_apparatus(test.apparatus)
        s = self.analysis_for(test)
        rows = []
        behaviours = [asdict(b) for b in self.behaviours]
        ids = [test.animal_id] + list(test.extra_animals)
        if not tracks and test.events and behaviours:
            # manual scoring only
            dur = test.duration_s or self.test_duration_s
            row = self.test_info(test)
            row["Period"] = "Whole test"
            row.update(behaviour_measures(test.events, behaviours, 0.0, dur))
            return [row]
        for i, tr in enumerate(tracks):
            others = [o for j, o in enumerate(tracks) if j != i]
            kw = dict(events=test.events if i == 0 else [], behaviours=behaviours if i == 0 else None,
                      other_tracks=others or None)
            if segmented:
                parts = analyse_segmented(tr, app, s, **kw)
            else:
                parts = [("Whole test", analyse(tr, app, s, **kw))]
            for label, res in parts:
                row = self.test_info(test, ids[i] if i < len(ids) else f"{test.animal_id}#{i + 1}")
                row["Period"] = label
                row.update(res)
                rows.append(row)
        return rows

    def results(self, tests: list[Test] | None = None, segmented: bool = False,
                progress: Callable[[float], None] | None = None) -> list[dict]:
        tests = [t for t in (tests if tests is not None else self.tests) if t.status != "excluded"]
        rows = []
        for i, t in enumerate(tests):
            if self.has_track(t) or (t.events and self.behaviours):
                rows.extend(self.analyse_test(t, segmented))
            if progress:
                progress((i + 1) / max(1, len(tests)))
        return rows


def _trim_on_detection(tr: Track, duration: float) -> Track:
    idx = np.flatnonzero(tr.detected)
    if len(idx) == 0:
        return tr
    t0 = tr.t[idx[0]]
    end = t0 + duration if duration and duration > 0 else math.inf
    out = tr.slice_time(t0, end)
    out.t = out.t - t0
    out.meta["video_start_s"] = float(tr.meta.get("video_start_s", 0) or 0) + float(t0)
    return out


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
