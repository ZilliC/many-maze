"""Application-level behaviour: opening experiments from the OS (macOS Finder)."""

from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from manymaze.core.demo import create_demo_project
from manymaze.gui.app import _FileOpenFilter
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def test_file_open_event(tmp_path):
    d = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=3).path
    w = MainWindow()
    f = _FileOpenFilter(w)
    app.installEventFilter(f)
    try:
        QApplication.sendEvent(app, QFileOpenEvent(str(d / "project.json")))
        assert w.project is not None and w.project.path == d
    finally:
        app.removeEventFilter(f)
        w.dirty = False
        w.close()
