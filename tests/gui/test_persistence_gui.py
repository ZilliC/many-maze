"""GUI tests of the persistence audit fixes: Save As onto the experiment's own folder, the experiment lock."""

import json
import os

import numpy as np
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from manymaze.core import explock
from manymaze.core.project import PROJECT_FILE, Project
from manymaze.core.track import Track
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def _saved(tmp_path, folder="Open field.mmaze") -> Project:
    p = Project(name="Open field")
    p.save(tmp_path / folder)
    t = p.add_test("", "A1")
    p.save_tracks(t, [Track(t=np.arange(5) / 5.0, x=np.ones(5), y=np.ones(5), fps=5.0)])
    p.save()
    return p


def test_save_as_onto_own_folder_with_other_case_is_a_plain_save(tmp_path, monkeypatch):
    p = _saved(tmp_path)
    w = MainWindow()
    w.set_project(Project.load(p.path))
    # the copy's folder spelt differently: "open field" vs "Open field" (same folder on a case-insensitive disk)
    w.project.name = "open field"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    assert w.save_as()
    track = p.path / "tracks" / "test_0001.csv"
    assert track.exists(), "Save As deleted the experiment's own tracks"
    if os.path.exists(tmp_path / "OPEN FIELD.mmaze"):  # case-insensitive disk: no "Replace?" and saved in place
        assert not asked
        assert json.loads((p.path / PROJECT_FILE).read_text(encoding="utf-8"))["name"] == "open field"
    assert len(w.project.load_tracks(w.project.tests[0])) == 1
    w.dirty = False
    w.close()


def test_save_as_to_new_folder_moves_the_lock(tmp_path, monkeypatch):
    p = _saved(tmp_path)
    w = MainWindow()
    w.set_project(Project.load(p.path))
    assert explock.is_mine(explock.read(p.path))
    dest = tmp_path / "copies"
    dest.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(dest))
    assert w.save_as()
    new = dest / "Open field.mmaze"
    assert w.project.path == new and (new / "tracks" / "test_0001.csv").exists()
    assert explock.is_mine(explock.read(new)) and explock.read(p.path) is None
    w.dirty = False
    w.close()
    assert explock.read(new) is None  # released when the window closes


def test_open_locked_experiment_read_only(tmp_path, monkeypatch):
    p = _saved(tmp_path)
    other = {**explock.me(), "pid": os.getppid()}  # a live process of this computer
    explock.lock_path(p.path).write_text(json.dumps(other), encoding="utf-8")
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a))
    w = MainWindow()
    monkeypatch.setattr(w, "_ask_locked", lambda proj, info: "cancel")
    w.load_project(str(p.path))
    assert w.project is None
    monkeypatch.setattr(w, "_ask_locked", lambda proj, info: "read_only")
    w.load_project(str(p.path))
    assert w.project is not None and w.project.read_only and "read-only" in w.windowTitle()
    assert explock.read(p.path)["pid"] == os.getppid()  # the other program's lock is left alone
    w.mark_dirty()
    assert not w.save() and errors  # refused, with a message
    w.dirty = False
    w.set_project(None)
    assert explock.read(p.path)["pid"] == os.getppid()
    monkeypatch.setattr(w, "_ask_locked", lambda proj, info: "anyway")
    w.load_project(str(p.path))
    assert not w.project.read_only and explock.is_mine(explock.read(p.path))
    assert w.save()
    w.dirty = False
    w.close()
