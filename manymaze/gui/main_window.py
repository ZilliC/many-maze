"""Main window: navigation sidebar, pages, project file handling."""

from __future__ import annotations

import importlib
import os
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core.project import PROJECT_FILE, Project
from ..core.templates import TEMPLATES
from .widgets import error_box, run_with_progress

# (module, class) of every page, in sidebar order
PAGES = [
    ("experiment", "ExperimentPage"),
    ("animals", "AnimalsPage"),
    ("apparatus", "ApparatusPage"),
    ("tests", "TestsPage"),
    ("testview", "TestViewPage"),
    ("live", "LivePage"),
    ("results", "ResultsPage"),
    ("statistics", "StatisticsPage"),
]


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New experiment")
        f = QFormLayout(self)
        self.name = QLineEdit("My experiment")
        self.folder = QLineEdit(str(Path.home() / "Documents"))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        self.protocol = QComboBox()
        for k, t in TEMPLATES.items():
            self.protocol.addItem(t.title, k)
        self.protocol.currentIndexChanged.connect(self._proto_changed)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 1e6)
        self.duration.setSuffix(" s")
        self.duration.setValue(TEMPLATES["open_field"].default_duration_s)
        f.addRow("Name", self.name)
        f.addRow("Save in", row)
        f.addRow("Protocol", self.protocol)
        f.addRow("Test duration", self.duration)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.resize(520, 0)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the experiment", self.folder.text())
        if d:
            self.folder.setText(d)

    def _proto_changed(self):
        self.duration.setValue(TEMPLATES[self.protocol.currentData()].default_duration_s)

    def project_path(self) -> Path:
        safe = "".join(c for c in self.name.text().strip() if c not in '/\\:*?"<>|') or "experiment"
        return Path(self.folder.text()) / f"{safe}.mmaze"


class WelcomePage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch()
        icon = QLabel()
        icon.setPixmap(QIcon(str(Path(__file__).resolve().parent.parent / "resources" / "icon.svg")).pixmap(96, 96))
        icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)
        title = QLabel(f"<h1 style='color:#e11d48'>{APP_NAME}</h1>"
                       f"<p>Libre video tracking and behavioural analysis · version {__version__}</p>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        row = QHBoxLayout()
        row.addStretch()
        for text, fn in (("New experiment…", main.new_project), ("Open experiment…", main.open_project_dialog),
                         ("Open demo experiment", main.create_demo)):
            b = QPushButton(text)
            b.setMinimumSize(QSize(190, 44))
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.recent = QListWidget()
        self.recent.setMaximumWidth(600)
        self.recent.itemActivated.connect(lambda it: main.load_project(it.data(Qt.UserRole)))
        rl = QHBoxLayout()
        rl.addStretch()
        box = QVBoxLayout()
        box.addWidget(QLabel("Recent experiments"))
        box.addWidget(self.recent)
        rl.addLayout(box)
        rl.addStretch()
        lay.addLayout(rl)
        lay.addStretch()
        self.main = main

    def refresh(self):
        self.recent.clear()
        for p in self.main.recent_projects():
            it = QListWidgetItem(f"{Path(p).stem}    —    {p}")
            it.setData(Qt.UserRole, p)
            self.recent.addItem(it)


class MainWindow(QMainWindow):
    project_loaded = Signal(object)

    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.dirty = False
        ini = os.environ.get("MANYMAZE_SETTINGS")  # e.g. tests: keep the user's preferences untouched
        self.settings = QSettings(ini, QSettings.IniFormat) if ini else QSettings("manymaze", "mANY-MAZE")
        self.resize(1400, 880)
        icon = Path(__file__).resolve().parent.parent / "resources" / "icon.svg"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self.nav = QListWidget()
        self.nav.setFixedWidth(170)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setStyleSheet("QListWidget{font-size:14px;border:none;background:palette(window);}"
                               "QListWidget::item{padding:9px 10px;border-radius:6px;}"
                               "QListWidget::item:selected{background:#e11d48;color:white;}")
        self.stack = QStackedWidget()
        self.welcome = WelcomePage(self)
        self.stack.addWidget(self.welcome)
        self.pages = []
        for mod, cls in PAGES:
            try:
                m = importlib.import_module(f".pages.{mod}", __package__)
                page = getattr(m, cls)(self)
            except Exception:
                traceback.print_exc()
                page = QLabel(f"Page {cls} failed to load — see console.")
                page.title = cls
            self.pages.append(page)
            self.stack.addWidget(page)
            self.nav.addItem(QListWidgetItem(getattr(page, "title", cls)))
        self.nav.currentRowChanged.connect(self._nav_changed)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._build_menus()
        self._current_page = None
        self.set_project(None)
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------ menus
    def _build_menus(self):
        mb = self.menuBar()
        fm = mb.addMenu("&File")

        def act(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        act(fm, "New experiment…", self.new_project, QKeySequence.New)
        act(fm, "Open experiment…", self.open_project_dialog, QKeySequence.Open)
        self.recent_menu = fm.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        act(fm, "Create demo experiment", self.create_demo)
        fm.addSeparator()
        self.save_act = act(fm, "Save", self.save, QKeySequence.Save)
        act(fm, "Save as…", self.save_as, QKeySequence.SaveAs)
        act(fm, "Close experiment", self.close_project)
        fm.addSeparator()
        act(fm, "Reveal experiment folder", self.reveal_folder)
        fm.addSeparator()
        act(fm, "Quit", self.close, QKeySequence.Quit)
        gm = mb.addMenu("&Go")
        for i, page in enumerate(self.pages):
            a = act(gm, getattr(page, "title", f"Page {i}"), lambda _=False, i=i: self.nav.setCurrentRow(i),
                    f"Ctrl+{i + 1}")
            a.setShortcutContext(Qt.ApplicationShortcut)
        hm = mb.addMenu("&Help")
        act(hm, "User guide", self._open_guide)
        act(hm, f"About {APP_NAME}", self.about)

    def _open_guide(self):
        from .help import show_user_guide

        show_user_guide(self)

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3><p>A libre (GPL-3.0-or-later) video tracking and "
                          "behavioural analysis suite for animal behaviour experiments — open field, plus mazes, "
                          "water maze, Barnes maze, Y/T/radial mazes, novel object, light/dark, three-chamber, "
                          "fear conditioning and more.</p><p>Built with Python, OpenCV, NumPy, SciPy, matplotlib "
                          "and Qt (PySide6). Runs natively on Apple Silicon.</p>")

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in self.recent_projects():
            a = self.recent_menu.addAction(p)
            a.triggered.connect(lambda _=False, p=p: self.load_project(p))

    # ---------------------------------------------------------------- project
    def recent_projects(self) -> list[str]:
        v = self.settings.value("recent", []) or []
        if isinstance(v, str):
            v = [v]
        return [p for p in v if (Path(p) / PROJECT_FILE).exists()]

    def _add_recent(self, path: Path):
        lst = [str(path)] + [p for p in self.recent_projects() if p != str(path)]
        self.settings.setValue("recent", lst[:10])

    def set_project(self, project: Project | None):
        self.project = project
        self.dirty = False
        has = project is not None
        self.nav.setEnabled(has)
        for page in self.pages:
            if hasattr(page, "set_project"):
                try:
                    page.set_project(project)
                except Exception:
                    traceback.print_exc()
        if has:
            self.nav.setCurrentRow(0)
            self._nav_changed(0)
        else:
            self.welcome.refresh()
            self.stack.setCurrentWidget(self.welcome)
            self.nav.clearSelection()
        self.update_title()
        self.project_loaded.emit(project)

    def _nav_changed(self, row: int):
        if row < 0 or self.project is None:
            return
        page = self.pages[row]
        if self._current_page is not None and self._current_page is not page and hasattr(self._current_page, "on_hide"):
            self._current_page.on_hide()
        self.stack.setCurrentWidget(page)
        self._current_page = page
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception as e:
                traceback.print_exc()
                self.status(f"Error: {e}")

    def page(self, cls_name: str):
        return next((p for p in self.pages if type(p).__name__ == cls_name), None)

    def goto(self, cls_name: str):
        for i, p in enumerate(self.pages):
            if type(p).__name__ == cls_name:
                self.nav.setCurrentRow(i)
                return p
        return None

    def open_test(self, test_id: int):
        page = self.page("TestViewPage")
        if page is None:
            return
        self.goto("TestViewPage")
        page.load_test(test_id)

    def mark_dirty(self, *_):
        if self.project is None:
            return
        self.dirty = True
        self.update_title()

    def update_title(self):
        if self.project is None:
            self.setWindowTitle(APP_NAME)
        else:
            self.setWindowTitle(f"{self.project.name}{' •' if self.dirty else ''} — {APP_NAME}")

    def status(self, msg: str, ms: int = 6000):
        self.statusBar().showMessage(msg, ms)

    def maybe_save(self) -> bool:
        if self.project is None or not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to “{self.project.name}”?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def new_project(self):
        if not self.maybe_save():
            return
        dlg = NewProjectDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        path = dlg.project_path()
        if (path / PROJECT_FILE).exists():
            if QMessageBox.question(self, APP_NAME, f"{path} already exists. Open it instead?") == QMessageBox.Yes:
                self.load_project(str(path))
            return
        p = Project(name=dlg.name.text().strip() or "Experiment", protocol=dlg.protocol.currentData(),
                    test_duration_s=dlg.duration.value())
        try:
            p.save(path)
        except Exception as e:
            error_box(self, "New experiment", e)
            return
        self._add_recent(path)
        self.set_project(p)
        self.status(f"Created {path}. Next: add an apparatus, then tests.")
        self.goto("ApparatusPage")

    def open_project_dialog(self):
        if not self.maybe_save():
            return
        d = QFileDialog.getExistingDirectory(self, "Open experiment folder (.mmaze)", str(Path.home()))
        if d:
            self.load_project(d)

    def load_project(self, path: str):
        if self.project is not None and not self.maybe_save():
            return
        try:
            p = Project.load(path)
        except Exception as e:
            error_box(self, "Open experiment", e)
            return
        self._add_recent(p.path)
        self.set_project(p)
        self.status(f"Opened {p.path}")

    def save(self) -> bool:
        if self.project is None:
            return False
        for page in self.pages:
            if hasattr(page, "commit"):
                try:
                    page.commit()
                except Exception:
                    traceback.print_exc()
        try:
            self.project.save()
        except Exception as e:
            error_box(self, "Save", e)
            return False
        self.dirty = False
        self.update_title()
        self.status(f"Saved {self.project.path}")
        return True

    def save_as(self):
        if self.project is None:
            return
        d = QFileDialog.getExistingDirectory(self, "Choose a folder for the copy")
        if not d:
            return
        import shutil

        dest = Path(d) / f"{self.project.name}.mmaze"
        old = self.project.path
        # keep video paths valid: make them absolute before moving
        for t in self.project.tests:
            t.video = self.project.abs_path(t.video)
        if old and (old / "tracks").exists():
            shutil.copytree(old / "tracks", dest / "tracks", dirs_exist_ok=True)
        self.project.save(dest)
        for t in self.project.tests:
            t.video = self.project.rel_path(t.video)
        self.project.save()
        self._add_recent(dest)
        self.dirty = False
        self.update_title()
        self.status(f"Saved copy at {dest}")

    def close_project(self):
        if self.maybe_save():
            self.set_project(None)

    def reveal_folder(self):
        if self.project and self.project.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.path)))

    def create_demo(self):
        if not self.maybe_save():
            return
        d = QFileDialog.getExistingDirectory(self, "Where should the demo experiment be created?",
                                             str(Path.home() / "Documents"))
        if not d:
            return
        path = Path(d) / "Demo open field.mmaze"
        if (path / PROJECT_FILE).exists():
            self.load_project(str(path))
            return
        from ..core.demo import create_demo_project

        run_with_progress(self, "Creating demo experiment",
                          lambda progress, stop: create_demo_project(path, progress=progress),
                          on_done=lambda p: (self._add_recent(p.path), self.set_project(p)), cancellable=False)

    def closeEvent(self, e):
        if self.maybe_save():
            for page in self.pages:
                if hasattr(page, "shutdown"):
                    try:
                        page.shutdown()
                    except Exception:
                        traceback.print_exc()
            e.accept()
        else:
            e.ignore()
