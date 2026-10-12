"""Findings of the QA pilot run (computer use on macOS): the window must fit a laptop screen, the welcome page and
the File menu call the demo the same, tests added from a broken playlist or an empty file say so, and tracking
errors are explained in plain words."""

import errno
import os
import shutil
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from manymaze.core.batch import _err
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.schedule_dialogs import AddVideosDialog

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pilot") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=4)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def test_window_fits_a_laptop_screen_with_every_page(win):
    # the pages that needed most room (Review, Results, the apparatus editor, Run tests) scroll instead of making
    # the window bigger than the screen (it was ~1206 × 919 px here and 1588 × 982 pt on a 1440 × 900 Mac)
    for name in ("TestViewPage", "ResultsPage", "ApparatusPage", "LivePage", "ExperimentPage"):
        assert win.goto(name) is win.current_page() and win.current_page() is win.page(name)
        app.processEvents()
        hint = win.minimumSizeHint()
        assert hint.width() <= 1000 and hint.height() <= 700, (name, hint)


def test_demo_has_the_same_name_on_the_welcome_page_and_in_the_menu():
    w = MainWindow()
    assert w.welcome.side_buttons["demo"].text() == "Create demo experiment"
    texts = [a.text() for m in w.menuBar().findChildren(type(w.menuBar().actions()[0].menu()))
             for a in m.actions()]
    assert "Create demo experiment" in texts and "Open demo experiment" not in texts
    w.close()


def test_add_tests_warns_about_broken_playlists_and_empty_files(tmp_path, win):
    (tmp_path / "list.m3u").write_text("#EXTM3U\nnot_here.mp4\n")
    (tmp_path / "empty.mp4").write_bytes(b"")
    dlg = AddVideosDialog(win.project, [str(tmp_path / "list.m3u"), str(tmp_path / "empty.mp4")], win)
    text = " ".join(lb.text() for lb in dlg.findChildren(QLabel))
    assert "not_here.mp4" in text and "empty.mp4: the file is empty" in text
    assert dlg.table.rowCount() == 2  # the tests can still be added
    dlg.close()


def test_tracking_errors_are_in_plain_words(capsys):
    batch = [SimpleNamespace(id=5)]
    e = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "/videos/rat 1.mp4")
    assert _err(batch, e) == ("Test 5: “rat 1.mp4” was not found. It may have been moved, renamed or deleted, or "
                              "its drive may not be connected.")
    assert "FileNotFoundError" in capsys.readouterr().err  # the technical details go to the console
    assert "KeyError" not in _err(batch, KeyError("x")) and "unexpectedly" in _err(batch, KeyError("x"))
