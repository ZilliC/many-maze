"""Main window in the layout of ANY-maze: a ribbon (File · Protocol · Experiment · Test · Results · Help) over a
work area where each tab has an explorer list on the left and the selected page on the right."""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core.project import PROJECT_FILE, Project
from ..core.templates import TEMPLATES
from . import theme
from .icons import icon
from .ribbon import Ribbon
from .widgets import error_box, run_with_progress

# (module, class) of every page
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

# ribbon tabs and the explorer entries of each: (label, icon, page class)
SECTIONS = [
    ("Protocol", [("Protocol", "protocol", "ExperimentPage"), ("Apparatus", "zone", "ApparatusPage")]),
    ("Experiment", [("Experiment", "animal", "AnimalsPage")]),
    ("Test", [("Test schedule", "schedule", "TestsPage"), ("Run tests", "play", "LivePage"),
              ("Review and score", "video", "TestViewPage")]),
    ("Results", [("Data", "table", "ResultsPage"), ("Statistics", "bars", "StatisticsPage")]),
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
    """The File tab ("backstage"): experiment commands on a blue strip, recent experiments on the right."""

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        self.setObjectName("Backstage")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setObjectName("BackstageSide")
        side.setFixedWidth(250)
        side.setStyleSheet(f"QWidget#BackstageSide{{background:{theme.ACCENT};}}"
                           "QPushButton{color:white;background:transparent;border:none;text-align:left;"
                           "padding:10px 26px;font-size:15px;border-radius:0;}"
                           "QPushButton:hover{background:rgba(255,255,255,0.18);}"
                           "QPushButton:disabled{color:rgba(255,255,255,0.45);background:transparent;}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 18, 0, 12)
        sl.setSpacing(0)
        self.side_buttons = {}
        for key, text, fn in (("new", "New experiment", main.new_project),
                              ("open", "Open experiment", main.open_project_dialog),
                              ("demo", "Open demo experiment", main.create_demo),
                              ("save", "Save", main.save), ("save_as", "Save as", main.save_as),
                              ("close", "Close experiment", main.close_project),
                              ("import", "Import from ANY-maze", main.import_menu),
                              ("folder", "Show in folder", main.reveal_folder),
                              ("help", "User guide", main._open_guide), ("info", "About", main.about)):
            b = QPushButton(text)
            b.setFlat(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(fn)
            sl.addWidget(b)
            self.side_buttons[key] = b
            if key in ("demo", "close"):
                sl.addSpacing(14)
        sl.addStretch()
        lay.addWidget(side)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(40, 26, 40, 26)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(Path(__file__).resolve().parent.parent / "resources" / "icon.svg")).pixmap(56, 56))
        head.addWidget(logo)
        title = QLabel(f"<span style='font-size:26px;color:{theme.HEADING};font-weight:300'>{APP_NAME}</span><br>"
                       f"<span style='color:{theme.MUTED}'>Libre video tracking and behavioural analysis · "
                       f"version {__version__}</span>")
        head.addWidget(title, 1)
        bl.addLayout(head)
        bl.addSpacing(18)
        cap = QLabel("Recent experiments")
        cap.setObjectName("SectionTitle")
        bl.addWidget(cap)
        self.recent = QListWidget()
        self.recent.setStyleSheet("QListWidget{border:none;background:transparent;font-size:14px;}"
                                  "QListWidget::item{padding:8px 6px;}")
        self.recent.itemActivated.connect(lambda it: main.load_project(it.data(Qt.UserRole)))
        self.recent.itemClicked.connect(lambda it: main.load_project(it.data(Qt.UserRole)))
        bl.addWidget(self.recent, 1)
        lay.addWidget(body, 1)

    def refresh(self):
        self.recent.clear()
        for p in self.main.recent_projects():
            it = QListWidgetItem(icon("folder"), f"{Path(p).stem}\n{p}")
            it.setData(Qt.UserRole, p)
            self.recent.addItem(it)
        if not self.recent.count():
            it = QListWidgetItem("No recent experiments — create a new one or open the demo experiment.")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
        has = self.main.project is not None
        for k in ("save", "save_as", "close", "folder", "import"):
            self.side_buttons[k].setEnabled(has)


class SectionView(QWidget):
    """A ribbon tab's work area: an explorer list on the left and the selected page on the right.

    A page can list sub-items under its explorer entry (protocol elements, each apparatus, result views…) by
    implementing ``explorer_items() -> [(label, icon name, key), ...]`` and ``show_item(key)``; it calls
    ``main.refresh_explorer(page)`` when the list changes and ``main.select_explorer(page, key)`` to follow."""

    page_changed = Signal(object)

    def __init__(self, title: str):
        super().__init__()
        self.title = title
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(0)
        cap = QLabel(title)
        cap.setObjectName("ExplorerTitle")
        left.addWidget(cap)
        self.explorer = QTreeWidget()
        self.explorer.setObjectName("Explorer")
        self.explorer.setHeaderHidden(True)
        self.explorer.setRootIsDecorated(False)
        self.explorer.setItemsExpandable(False)
        self.explorer.setIndentation(14)
        self.explorer.setIconSize(QSize(20, 20))
        self.explorer.setFixedWidth(200)
        self.explorer.currentItemChanged.connect(self._item_changed)
        left.addWidget(self.explorer, 1)
        lay.addLayout(left)
        self.stack = QStackedWidget()
        self.stack.setObjectName("SectionStack")
        lay.addWidget(self.stack, 1)
        self.pages: list = []

    def add_page(self, label: str, icon_name: str, page):
        it = QTreeWidgetItem([label])
        it.setIcon(0, icon(icon_name))
        it.setData(0, Qt.UserRole, len(self.pages))
        self.explorer.addTopLevelItem(it)
        self.pages.append(page)
        self.stack.addWidget(page)

    def item_for(self, page, key=None):
        for i in range(self.explorer.topLevelItemCount()):
            it = self.explorer.topLevelItem(i)
            if self.pages[it.data(0, Qt.UserRole)] is page:
                if key is None:
                    return it
                for j in range(it.childCount()):
                    if it.child(j).data(0, Qt.UserRole + 1) == key:
                        return it.child(j)
                return None
        return None

    def refresh_children(self, page):
        """Rebuild the sub-items a page lists under its explorer entry."""
        it = self.item_for(page)
        if it is None:
            return
        cur = self.explorer.currentItem()
        cur_key = cur.data(0, Qt.UserRole + 1) if cur is not None and cur.parent() is it else None
        self.explorer.blockSignals(True)
        it.takeChildren()
        items = []
        if hasattr(page, "explorer_items"):
            try:
                items = page.explorer_items() or []
            except Exception:
                traceback.print_exc()
        for label, icon_name, key in items:
            c = QTreeWidgetItem([label])
            c.setIcon(0, icon(icon_name))
            c.setData(0, Qt.UserRole, it.data(0, Qt.UserRole))
            c.setData(0, Qt.UserRole + 1, key)
            it.addChild(c)
        it.setExpanded(True)
        if cur_key is not None:
            again = self.item_for(page, cur_key)
            if again is not None:
                self.explorer.setCurrentItem(again)
        self.explorer.blockSignals(False)

    def select(self, page):
        it = self.item_for(page)
        if it is not None:
            if self.explorer.currentItem() is it:
                self._item_changed(it)
            else:
                self.explorer.setCurrentItem(it)

    def current_page(self):
        return self.stack.currentWidget()

    def _item_changed(self, it, _prev=None):
        if it is None:
            return
        page = self.pages[it.data(0, Qt.UserRole)]
        self.stack.setCurrentWidget(page)
        self.page_changed.emit(page)
        key = it.data(0, Qt.UserRole + 1)
        if key is not None and hasattr(page, "show_item"):
            try:
                page.show_item(key)
            except Exception:
                traceback.print_exc()


class MainWindow(QMainWindow):
    project_loaded = Signal(object)

    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.dirty = False
        ini = os.environ.get("MANYMAZE_SETTINGS")  # e.g. tests: keep the user's preferences untouched
        self.settings = QSettings(ini, QSettings.IniFormat) if ini else QSettings("manymaze", "mANY-MAZE")
        self.resize(1400, 880)
        app_icon = Path(__file__).resolve().parent.parent / "resources" / "icon.svg"
        if app_icon.exists():
            self.setWindowIcon(QIcon(str(app_icon)))

        self.pages = []
        self._page_section: dict[int, int] = {}
        for mod, cls in PAGES:
            try:
                m = importlib.import_module(f".pages.{mod}", __package__)
                page = getattr(m, cls)(self)
            except Exception:
                traceback.print_exc()
                page = QLabel(f"Page {cls} failed to load — see console.")
                page.title = cls
            self.pages.append(page)

        self.ribbon = Ribbon()
        self.ribbon.tabs.addTab("File")
        self.ribbon.panels.addWidget(QWidget())
        self.sections: list[SectionView] = []
        self._section_tabs: list[int] = []
        by_name = {type(p).__name__: p for p in self.pages}
        self.workspace = QStackedWidget()
        self.workspace.setObjectName("Workspace")
        self.welcome = WelcomePage(self)
        self.workspace.addWidget(self.welcome)
        for title, entries in SECTIONS:
            sec = SectionView(title)
            panel = self.ribbon.add_tab(title)
            nav = panel.add_group(title)
            sec.nav_actions = []
            for label, icon_name, cls in entries:
                page = by_name.get(cls)
                if page is None:
                    continue
                sec.add_page(label, icon_name, page)
                self._page_section[id(page)] = len(self.sections)
                a = QAction(icon(icon_name), label, self)
                a.setCheckable(True)
                a.triggered.connect(lambda _=False, p=page: self.show_page(p))
                a.page = page
                nav.add_large(a)
                sec.nav_actions.append(a)
            sec.panel = panel
            sec.page_changed.connect(self._page_changed)
            self.sections.append(sec)
            self.workspace.addWidget(sec)
        help_panel = self.ribbon.add_tab("Help")
        hg = help_panel.add_group("Help")
        for text, ic, fn in (("User guide", "help", self._open_guide), (f"About {APP_NAME}", "info", self.about)):
            a = QAction(icon(ic), text, self)
            a.triggered.connect(fn)
            hg.add_large(a)
        self._help_tab = self.ribbon.tabs.count() - 1
        self.save_quick = QToolButton()
        self.save_quick.setIcon(icon("save"))
        self.save_quick.setToolTip("Save the experiment (Ctrl+S)")
        self.save_quick.setAutoRaise(True)
        self.save_quick.clicked.connect(self.save)
        self.ribbon.corner.addWidget(self.save_quick)
        self.ribbon.tab_changed.connect(self._tab_changed)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.ribbon)
        lay.addWidget(self.workspace, 1)
        self.setCentralWidget(central)
        self._build_menus()
        self._current_page = None
        self.set_project(None)
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------ menus / shortcuts
    def _build_menus(self):
        mb = self.menuBar()
        fm = mb.addMenu("&File")

        def act(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            self.addAction(a)  # shortcuts work even where the menu bar is hidden
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
            a = act(gm, getattr(page, "title", f"Page {i}"), lambda _=False, p=page: self.show_page(p),
                    f"Ctrl+{i + 1}")
            a.setShortcutContext(Qt.ApplicationShortcut)
        hm = mb.addMenu("&Help")
        act(hm, "User guide", self._open_guide)
        act(hm, f"About {APP_NAME}", self.about)
        # the ribbon replaces the menu bar, except on macOS where the menu bar lives at the top of the screen
        mb.setVisible(sys.platform == "darwin")
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
        if self.project is not None and project is not self.project:
            self._hide_current()  # the old project's page flushes against the old project, not the new one
            self._current_page = None
        self.project = project
        self.dirty = False
        has = project is not None
        for i in range(1, self._help_tab):
            self.ribbon.tabs.setTabEnabled(i, has)
        self.save_quick.setEnabled(has)
        for page in self.pages:
            if hasattr(page, "set_project"):
                try:
                    page.set_project(project)
                except Exception:
                    traceback.print_exc()
        for page in self.pages:
            self.refresh_explorer(page)
        if has:
            self.show_page(self.pages[0])
        else:
            self._show_backstage()
        self.dirty = False  # page set-up must not count as user edits
        self.update_title()
        self.project_loaded.emit(project)

    # ------------------------------------------------------------------ navigation
    def _show_backstage(self):
        self.welcome.refresh()
        self.workspace.setCurrentWidget(self.welcome)
        if self.ribbon.tabs.currentIndex() != 0:
            self.ribbon.tabs.blockSignals(True)
            self.ribbon.tabs.setCurrentIndex(0)
            self.ribbon.tabs.blockSignals(False)
            self.ribbon._changed(0)

    def _tab_changed(self, i: int):
        if i == 0:
            self._hide_current()
            self._current_page = None
            self.welcome.refresh()
            self.workspace.setCurrentWidget(self.welcome)
        elif i == self._help_tab:
            pass  # the Help tab only offers its ribbon commands; the work area stays as it is
        elif self.project is None:
            self._show_backstage()
        else:
            sec = self.sections[i - 1]
            self.workspace.setCurrentWidget(sec)
            page = sec.current_page()
            if page is not None and page is not self._current_page:
                self._page_changed(page)
            elif sec.explorer.currentItem() is None and sec.pages:
                sec.select(sec.pages[0])

    def _hide_current(self):
        cur = self._current_page
        if cur is not None and hasattr(cur, "on_hide"):
            try:
                cur.on_hide()
            except Exception:
                traceback.print_exc()

    def _page_changed(self, page):
        """A section switched to `page` (explorer or ribbon): run hide/show hooks and refresh the ribbon."""
        if self.project is None:
            return
        idx = self._page_section.get(id(page))
        if idx is None:
            return
        sec = self.sections[idx]
        if self.workspace.currentWidget() is not sec:
            self.workspace.setCurrentWidget(sec)
        if self.ribbon.tabs.currentIndex() != idx + 1:
            self.ribbon.tabs.blockSignals(True)
            self.ribbon.tabs.setCurrentIndex(idx + 1)
            self.ribbon.tabs.blockSignals(False)
            self.ribbon._changed(idx + 1)
        if page is not self._current_page:
            self._hide_current()
        self._current_page = page
        for a in sec.nav_actions:
            a.setChecked(a.page is page)
        groups = []
        if hasattr(page, "ribbon_groups"):
            try:
                groups = page.ribbon_groups() or []
            except Exception:
                traceback.print_exc()
        sec.panel.set_context(groups)
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception as e:
                traceback.print_exc()
                self.status(f"Error: {e}")

    def show_page(self, page):
        idx = self._page_section.get(id(page))
        if idx is None or self.project is None:
            return None
        sec = self.sections[idx]
        self.workspace.setCurrentWidget(sec)
        cur = sec.explorer.currentItem()
        if sec.current_page() is page and cur is not None:
            self._page_changed(page)
        else:
            sec.select(page)
        return page

    def refresh_ribbon(self):
        """Pages call this when their ribbon groups change (e.g. enabled commands depend on a selection)."""
        if self._current_page is not None:
            self._page_changed(self._current_page)

    def current_page(self):
        return self._current_page if self.project is not None else None

    def refresh_explorer(self, page):
        idx = self._page_section.get(id(page))
        if idx is not None:
            self.sections[idx].refresh_children(page)

    def select_explorer(self, page, key):
        """Highlight a page's explorer sub-item without triggering show_item again."""
        idx = self._page_section.get(id(page))
        if idx is None:
            return
        sec = self.sections[idx]
        it = sec.item_for(page, key)
        if it is not None and sec.explorer.currentItem() is not it:
            sec.explorer.blockSignals(True)
            sec.explorer.setCurrentItem(it)
            sec.explorer.blockSignals(False)

    def page(self, cls_name: str):
        return next((p for p in self.pages if type(p).__name__ == cls_name), None)

    def goto(self, cls_name: str):
        page = self.page(cls_name)
        return self.show_page(page) if page is not None else None

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
        """Before the experiment closes: stop (and save) live tests still running, then offer to save changes."""
        if not self._stop_live_tests():
            return False
        if self.project is None or not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to “{self.project.name}”?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        self.dirty = False  # discarded: don't ask again on the way to the next experiment
        return True

    def _stop_live_tests(self) -> bool:
        """Live tests keep running in the background (Run tests): they are stopped and saved, or nothing happens."""
        live = self.page("LivePage")
        if live is None or not live.any_active():
            return True
        r = QMessageBox.question(self, APP_NAME, "Tests are still running on the Run tests page.\n\nStop them and "
                                 "save the data recorded so far?", QMessageBox.Save | QMessageBox.Cancel,
                                 QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return False
        live.stop_and_save_all()
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
        d = QFileDialog.getExistingDirectory(self, "Open experiment folder (.mmaze)", self._last_dir())
        if d:
            self._remember_dir(d)
            self.load_project(d)

    def _last_dir(self) -> str:
        d = self.settings.value("last_dir", "")
        return d if d and Path(d).exists() else str(Path.home() / "Documents" if (Path.home() / "Documents").exists()
                                                    else Path.home())

    def _remember_dir(self, path):
        p = Path(path)
        self.settings.setValue("last_dir", str(p.parent if p.suffix == ".mmaze" or p.is_file() else p))

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
        d = QFileDialog.getExistingDirectory(self, "Choose a folder for the copy", self._last_dir())
        if not d:
            return
        import shutil

        for page in self.pages:
            if hasattr(page, "commit"):
                try:
                    page.commit()
                except Exception:
                    traceback.print_exc()
        safe = "".join(c for c in self.project.name.strip() if c not in '/\\:*?"<>|') or "experiment"
        dest = Path(d) / f"{safe}.mmaze"
        old = self.project.path
        if dest == old:
            return self.save()
        if dest.exists() and QMessageBox.question(
                self, "Save as", f"{dest} already exists. Replace its experiment file and tracks?") != QMessageBox.Yes:
            return False
        videos = [t.video for t in self.project.tests]
        try:
            # keep video paths valid from the new location: make them absolute, then relative to the copy
            for t in self.project.tests:
                t.video = self.project.abs_path(t.video)
            if old and (old / "tracks").exists():
                shutil.copytree(old / "tracks", dest / "tracks", dirs_exist_ok=True)
            self.project.save(dest)
            for t in self.project.tests:
                t.video = self.project.rel_path(t.video)
            self.project.save()
        except Exception as e:
            self.project.path = old
            for t, v in zip(self.project.tests, videos):
                t.video = v
            error_box(self, "Save as", e)
            return False
        self._remember_dir(dest)
        self._add_recent(dest)
        self.dirty = False
        self.update_title()
        self.status(f"Saved copy at {dest} (recordings stay in the original folder).")
        return True

    def close_project(self):
        if self.maybe_save():
            self.set_project(None)

    def import_menu(self):
        """Import animals or a test schedule from spreadsheets saved by ANY-maze (or other software)."""
        if self.project is None:
            return
        m = QMenu(self)
        m.addAction(icon("animal"), "Animals and treatments…", lambda: self.import_table("animals"))
        m.addAction(icon("schedule"), "Test schedule…", lambda: self.import_table("tests"))
        btn = self.welcome.side_buttons.get("import")
        m.exec(btn.mapToGlobal(btn.rect().topRight()) if btn is not None and btn.isVisible() else QCursor.pos())

    def import_table(self, kind: str, path: str | None = None):
        from .import_wizard import ImportDialog

        dlg = ImportDialog(self.project, kind, self, path=path)
        if dlg.exec() != QDialog.Accepted:
            return None
        n = len(dlg.result or [])
        self.mark_dirty()
        self.status(f"Imported {n} {'animals' if kind == 'animals' else 'tests'}.")
        self.show_page(self.page("AnimalsPage" if kind == "animals" else "TestsPage"))
        return dlg.result

    def reveal_folder(self):
        if self.project and self.project.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.path)))

    def create_demo(self):
        if not self.maybe_save():
            return
        d = QFileDialog.getExistingDirectory(self, "Where should the demo experiment be created?",
                                             self._last_dir())
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
