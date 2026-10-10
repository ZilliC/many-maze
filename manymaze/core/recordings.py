"""File names of the videos recorded during live tests (ANY-maze: the recorded video file name built from chosen
fields).

``Project.recording_name_fields`` lists the fields, in order, joined by ``_``: the test number (``test_0007``), the
animal, the treatment (its code while testing blind, never its name), the stage, the trial, the date
(``2026-10-09``) and the time (``14-05-31``) when the test was set up. Empty means mANY-MAZE's name,
``test_0007_<animal>``. Names never replace a file: one already used (by a recording, a split recording's parts or
playlist, or a test of another panel being set up at the same time) gets ``_2``, ``_3`` …
"""

from __future__ import annotations

import datetime as _dt
import re
import threading
from pathlib import Path

from .terminology import term

# field: the label of the field (None: its term, see field_label)
FIELDS = {"test": None, "animal": None, "treatment": None, "stage": None, "trial": None, "date": "Date",
          "time": "Time"}
DEFAULT_FIELDS = ["test", "animal"]
VIDEO_SUFFIXES = (".mp4", ".avi", ".mov", ".m3u")

_lock = threading.Lock()
_reserved: dict[str, object] = {}  # recording base path (no suffix) → owner (experiment folder, test id)


def name_fields(project) -> list[str]:
    """The experiment's fields, known ones only, in order (the default without any)."""
    fields = [f for f in (getattr(project, "recording_name_fields", None) or []) if f in FIELDS]
    return list(dict.fromkeys(fields)) or list(DEFAULT_FIELDS)


def field_label(project, field: str) -> str:
    """The field as the file name dialog lists it, in the experiment's terminology."""
    if field == "test":
        return f"{term(project, 'test')} number"
    return FIELDS[field] or term(project, field)


def safe_part(text) -> str:
    """Text usable in a file name on every system: letters, digits, '.', '-'; anything else becomes '_'."""
    return re.sub(r"[^\w.-]+", "_", str(text or "")).strip("_.")


def field_value(project, test, field: str, when: _dt.datetime) -> str:
    """The text of one field for a test (blank when the test has none, e.g. no stage)."""
    if field == "test":
        return f"test_{test.id:04d}"
    if field == "animal":
        return safe_part(test.animal_id)
    if field == "treatment":
        a = project.get_animal(test.animal_id) if test.animal_id else None
        group = a.group if a is not None else ""
        if project.blind:  # testing blind: the code (never the name), nothing if the code is not assigned yet
            return safe_part(project.treatment_code(group))
        return safe_part(group)
    if field == "stage":
        return safe_part(test.stage)
    if field == "trial":
        return f"trial_{int(test.trial)}" if test.trial else ""
    if field == "date":
        return when.strftime("%Y-%m-%d")
    if field == "time":
        return when.strftime("%H-%M-%S")
    return ""


def recording_name(project, test, when: _dt.datetime | None = None, fields: list[str] | None = None) -> str:
    """The name (without folder and suffix) of a test's recording from the chosen fields; ``test_<number>`` if
    every chosen field is blank. The default fields give mANY-MAZE's ``test_0007_<animal>`` (``animal`` when
    the test has none)."""
    when = when or _dt.datetime.now()
    fields = list(fields) if fields is not None else name_fields(project)
    if fields == DEFAULT_FIELDS:
        return f"test_{test.id:04d}_{safe_part(test.animal_id) or 'animal'}"
    parts = [v for v in (field_value(project, test, f, when) for f in fields) if v]
    return "_".join(parts) or f"test_{test.id:04d}"


def _used(base: Path) -> bool:
    """A recording with this base name exists: the video, a split recording's playlist or parts, or a second file
    of a restarted recording."""
    if any(base.with_name(base.name + s).exists() for s in VIDEO_SUFFIXES):  # (names may contain dots)
        return True
    folder = base.parent
    if not folder.exists():
        return False
    prefix = base.name + "_part"
    return any(p.name.startswith(prefix) for p in folder.iterdir())


def reserve_recording(folder, name: str, owner=None) -> Path:
    """A base path (no suffix) in ``folder`` for a new recording called ``name`` that no file and no other owner's
    recording being set up uses: ``name``, else ``name_2``, ``name_3`` … The path stays reserved for ``owner`` (e.g.
    the experiment and test) so two tests set up at the same moment never share a name; the owner's previous
    reservation is given up (setting the same test up again gets the same name back)."""
    folder = Path(folder)
    with _lock:
        if owner is not None:
            for k in [k for k, o in _reserved.items() if o == owner]:
                del _reserved[k]
        i = 1
        while True:
            base = folder / (name if i == 1 else f"{name}_{i}")
            key = str(base)
            if key not in _reserved and not _used(base):
                _reserved[key] = owner
                return base
            i += 1


def release_recording(path) -> None:
    """Give up a reservation (e.g. the test was set up but never recorded); ``path`` with or without its suffix."""
    p = Path(path)
    with _lock:
        _reserved.pop(str(p.with_name(p.stem) if p.suffix.lower() in VIDEO_SUFFIXES else p), None)
