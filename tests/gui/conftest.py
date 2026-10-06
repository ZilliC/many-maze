import gc

import pytest


@pytest.fixture(autouse=True)
def _free_windows():
    """Delete the windows a test created: main windows hold every page (and their figures), which adds up."""
    yield
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        if hasattr(w, "dirty"):
            w.dirty = False  # no "save changes?" prompt
        w.close()
        w.deleteLater()
    for _ in range(3):
        app.processEvents()
    gc.collect()
