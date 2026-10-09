"""Regression tests for the persistence audit: backups, discard-then-cancel, renames, bad schedule edits."""

import os

from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import workflow as wf
from manymaze.core.project import BACKUP_KEEP, Project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def test_restore_oldest_backup(tmp_path):
    p = Project(name="v0")
    p.save(tmp_path / "e.mmaze")
    for i in range(BACKUP_KEEP):  # a full set: the next backup() prunes the oldest
        p.name = f"v{i}"
        p.save()
        dest = p.backup()
        os.rename(dest, dest.with_name(f"project-2000010{i % 10}-0000{i:02d}.json"))
        os.utime(dest.with_name(f"project-2000010{i % 10}-0000{i:02d}.json"), (1000 + i, 1000 + i))
    assert len(p.list_backups()) == BACKUP_KEEP
    backups = p.list_backups()
    oldest = backups[-1]
    expected = Project.from_dict(__import__("json").loads(oldest.read_text(encoding="utf-8"))).name
    q = p.restore_backup(oldest)
    assert q.name == expected


def test_discard_keeps_dirty_until_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    w = MainWindow()
    w.set_project(Project(name="x"))
    w.mark_dirty()
    assert w.maybe_save() is True
    assert w.dirty  # the user may still cancel the next dialog
    w.set_project(Project(name="y"))
    assert not w.dirty
    w.dirty = False
    w.close()


def test_rename_migrates_settings():
    p = Project(name="r")
    a = p.ensure_animal("M1")
    p.animal_fields.append("Mass")
    p.settings_extra["completed_stages"] = {"M1": ["Training"]}
    p.settings_extra["dose"] = {"weight_field": "Mass"}
    assert wf.rename_animal(p, a, "M9")
    assert p.settings_extra["completed_stages"] == {"M9": ["Training"]}
    assert wf.rename_field(p, "Mass", "Weight2")
    assert p.settings_extra["dose"]["weight_field"] == "Weight2"
    assert wf.remove_field(p, "Weight2")
    assert wf.dose_settings(p)["weight_field"] == wf.WEIGHT_FIELD


def test_tests_model_rejects_bad_numbers(monkeypatch):
    w = MainWindow()
    p = Project(name="t")
    p.ensure_animal("M1")
    t = p.add_test("", "M1", stage="Training", trial=1)
    w.set_project(p)
    page = w.goto("TestsPage")
    m = page.model
    from manymaze.gui.pages import tests as tp

    for col, attr in ((tp.C_TRIAL, "trial"), (tp.C_START, "start_s"), (tp.C_DUR, "duration_s")):
        before = getattr(t, attr)
        assert m.setData(m.index(0, col), "abc") is False
        assert getattr(t, attr) == before
    w.dirty = False
    w.close()
