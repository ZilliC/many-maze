"""GUI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt


def main(project: str | None = None, argv_project: bool = True) -> int:
    """argv_project: also accept an experiment folder as the last command-line argument (double-click / Finder)."""
    # Qt on macOS: keep the app native and crisp on Retina displays
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from . import theme
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("manymaze")
    icon = Path(__file__).resolve().parent.parent / "resources" / "icon.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    try:  # colours, overlays and plots are designed for a light theme (Qt >= 6.8)
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    theme.apply(app)
    w = MainWindow()
    w.show()
    app.installEventFilter(_FileOpenFilter(w))
    if os.environ.get("MANYMAZE_SMOKE_TEST"):
        return _smoke_test(app, w)
    if project is None and argv_project and len(sys.argv) > 1 and (Path(sys.argv[-1]) / "project.json").exists():
        project = sys.argv[-1]
    if project:
        w.load_project(project)
    from .updates import startup_check

    startup_check(w)  # only if turned on (Help ▸ Check for updates at startup), at most once a week
    rc = app.exec()
    from .widgets import Worker

    if Worker.running:  # still finishing after the window closed (asked to stop): Qt aborts if they are destroyed
        Worker.stop_all(ms=None)
    return rc


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

    from ..core import pose
    from ..core.batch import track_tests
    from ..core.video import FrameReader, VideoRecorder, hw_decoder_name

    with tempfile.TemporaryDirectory() as d:
        proj = create_demo_project(Path(d) / "smoke.mmaze", n_per_group=1, seconds=4, track=False)
        res = track_tests(proj, proj.tests, workers=2)  # worker processes inside the frozen app
        assert sorted(res["tracked"]) == [t.id for t in proj.tests] and not res["errors"], res
        rows = proj.results()
        assert rows and rows[0]["Total distance (cm)"] > 0, rows
        with FrameReader(proj.abs_path(proj.tests[0].video)) as r:
            decoder = r.backend
            assert sum(1 for _ in r) > 0
        rec = VideoRecorder(str(Path(d) / "rec.mp4"), 25, (64, 48))
        import numpy as np
        for _ in range(10):
            rec.write(np.zeros((48, 64, 3), np.uint8))
        rec.close()
        ck = os.environ.get("MANYMAZE_SMOKE_POSE")  # path to the SuperAnimal checkpoint: test the AI pipeline
        if ck:
            os.environ["MANYMAZE_MODELS"] = str(Path(d) / "models")
            pose.install_model("topviewmouse_rtmpose_s", source_path=ck)
            proj.detection.body_parts = "pose"
            tr = proj.track_test(proj.tests[0])[0]
            assert tr.detected.mean() > 0.9 and tr.meta["pose_device"], tr.meta
            print(f"smoke test: pose model ran on {tr.meta['pose_device']}")
        print(f"smoke test: {res['workers']} parallel workers, decoder {decoder}, hardware decoder "
              f"{hw_decoder_name()}, recorder {rec.backend}, inference providers {pose.available_providers()}")
        from ..core.project import Project

        proj.set_experiment_password("smoke")  # the cryptography package is in the app (protected experiments)
        proj.save()
        assert Project.load(proj.path, "smoke").protected
        window.set_project(proj)
        for page in window.pages:
            window.show_page(page)
            app.processEvents()
            assert window.current_page() is page, page
        window.dirty = False
        print(f"smoke test ok: {len(rows)} result rows, {len(window.pages)} pages")
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
