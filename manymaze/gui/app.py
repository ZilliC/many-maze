"""GUI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt


def main(project: str | None = None) -> int:
    # Qt on macOS: keep the app native and crisp on Retina displays
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("manymaze")
    icon = Path(__file__).resolve().parent.parent / "resources" / "icon.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    if sys.platform != "darwin":
        app.setStyle("Fusion")
    try:  # colours, overlays and plots are designed for a light theme (Qt >= 6.8)
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    pal = app.palette()
    pal.setColor(QPalette.Highlight, QColor("#e11d48"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)
    w = MainWindow()
    w.show()
    app.installEventFilter(_FileOpenFilter(w))
    if os.environ.get("MANYMAZE_SMOKE_TEST"):
        return _smoke_test(app, w)
    if project is None and len(sys.argv) > 1 and Path(sys.argv[-1]).exists():
        project = sys.argv[-1]
    if project:
        w.load_project(project)
    return app.exec()


class _FileOpenFilter(QObject):
    """macOS: open experiments double-clicked in Finder or dropped on the Dock icon."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FileOpen and event.file():
            p = Path(event.file())
            self.window.load_project(str(p.parent if p.name == "project.json" else p))
            return True
        return False


def _smoke_test(app, window) -> int:
    """Used by CI on the frozen .app: create and track a tiny demo experiment, visit every page, exit."""
    import tempfile

    from ..core.demo import create_demo_project

    with tempfile.TemporaryDirectory() as d:
        proj = create_demo_project(Path(d) / "smoke.mmaze", n_per_group=1, seconds=4)
        rows = proj.results()
        assert rows and rows[0]["Total distance (cm)"] > 0, rows
        window.set_project(proj)
        for i in range(window.nav.count()):
            window.nav.setCurrentRow(i)
            app.processEvents()
        window.dirty = False
        print(f"smoke test ok: {len(rows)} result rows, {window.nav.count()} pages")
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
