"""Error messages shown to the user explain what went wrong in plain words, without Python exception names
(H-P03); the technical details go to the console."""

import errno
import json
import re
import zipfile

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from manymaze.core import pose
from manymaze.core.project import PROJECT_FILE, NotAnExperiment, Project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pose_model import PoseModelBox
from manymaze.gui.widgets import UNEXPECTED_ERROR, user_message

app = QApplication.instance() or QApplication([])

TECHNICAL = re.compile(r"\b\w+(Error|Exception|ZipFile)\b|\[Errno|Traceback")


@pytest.fixture
def shown(monkeypatch):
    out = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: out.append(a[2]) or QMessageBox.Ok)
    return out


def _open(tmp_path, monkeypatch, folder):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    w = MainWindow()
    w.open_project_dialog()
    return w


def test_open_a_folder_that_is_not_an_experiment(tmp_path, monkeypatch, shown, capsys):
    folder = tmp_path / "Videos"
    folder.mkdir()
    w = _open(tmp_path, monkeypatch, folder)
    assert w.project is None
    [msg] = shown
    assert "“Videos” is not a mANY-MAZE experiment" in msg and ".mmaze" in msg and "New experiment" in msg
    assert not TECHNICAL.search(msg), msg
    assert "NotAnExperiment" in capsys.readouterr().err  # the details, for a bug report
    w.close()


def test_open_an_experiment_that_is_gone(tmp_path, monkeypatch, shown):
    w = _open(tmp_path, monkeypatch, tmp_path / "Moved.mmaze")
    assert w.project is None
    [msg] = shown
    assert "“Moved.mmaze” was not found" in msg and not TECHNICAL.search(msg), msg
    w.close()


def test_open_a_damaged_experiment(tmp_path, monkeypatch, shown):
    p = Project(name="Open field")
    p.save(tmp_path / "Open field.mmaze")
    (p.path / PROJECT_FILE).write_text('{"name": "Open field", "tests": [', encoding="utf-8")  # cut short
    w = _open(tmp_path, monkeypatch, p.path)
    assert w.project is None
    [msg] = shown
    assert "is damaged" in msg and "backups" in msg and not TECHNICAL.search(msg), msg
    w.close()


def test_open_experiment_still_works(tmp_path, monkeypatch, shown):
    p = Project(name="Open field")
    p.save(tmp_path / "Open field.mmaze")
    w = _open(tmp_path, monkeypatch, p.path)
    assert w.project is not None and w.project.name == "Open field" and not shown
    w.dirty = False
    w.close()


def test_not_an_experiment_is_still_a_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        Project.load(tmp_path)
    with pytest.raises(NotAnExperiment):
        Project.load(tmp_path / "nothing here")


def test_convert_deeplabcut_model_from_a_file_that_is_not_one(tmp_path, monkeypatch, shown):
    bad = tmp_path / "notes.pt"
    bad.write_text("not a checkpoint", encoding="utf-8")
    box = PoseModelBox()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    assert box.convert_dialog() is None
    [msg] = shown
    assert "“notes.pt” is not a PyTorch model checkpoint" in msg and "DeepLabCut" in msg
    assert not TECHNICAL.search(msg), msg
    with zipfile.ZipFile(tmp_path / "other.pt", "w") as z:  # a zip, but not a checkpoint
        z.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="“other.pt” is not a PyTorch model checkpoint"):
        pose.checkpoint_keypoint_count(tmp_path / "other.pt")


def test_user_message_explains_common_errors(tmp_path):
    f = tmp_path / "data.csv"
    cases = {
        FileNotFoundError(errno.ENOENT, "No such file or directory", str(f)): "“data.csv” was not found",
        PermissionError(errno.EACCES, "Permission denied", str(f)): "not allowed to open or change “data.csv”",
        IsADirectoryError(errno.EISDIR, "Is a directory", str(tmp_path)): "is a folder: choose a file",
        OSError(errno.ENOSPC, "No space left on device", str(f)): "The disk is full",
        zipfile.BadZipFile("File is not a zip file"): "not in the expected format",
        json.JSONDecodeError("Expecting value", "", 0): "damaged or is not in the expected format",
        ValueError("The apparatus has no zones."): "The apparatus has no zones.",  # mANY-MAZE's own messages
        PermissionError("This experiment is read-only."): "This experiment is read-only.",
        KeyError("x"): UNEXPECTED_ERROR,
        TypeError("unsupported operand type(s) for +: 'int' and 'str'"): UNEXPECTED_ERROR,
    }
    for exc, expected in cases.items():
        msg = user_message(exc)
        assert expected in msg and not TECHNICAL.search(msg), (exc, msg)
    assert "console" in UNEXPECTED_ERROR
