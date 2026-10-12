"""Genera qa/INVENTARIO_UI.md: la lista de TODOS los controles visibles de mANY-MAZE (cintas, menús, páginas,
sub-páginas del explorador, pestañas y modos), cada uno con un ID estable, para que la prueba con computer use
pueda marcar cuál se probó.

Se ejecuta sin pantalla (Qt offscreen) sobre el experimento de demostración:

    QT_QPA_PLATFORM=offscreen .venv/bin/python qa/herramientas/generar_inventario.py

Vuelve a generarlo cuando cambie la interfaz; los IDs dependen del contexto y del texto del control, no del orden.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MANYMAZE_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.ini")

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox,
                               QComboBox, QFormLayout, QGroupBox, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QSlider, QTabWidget, QTextEdit, QToolButton, QWidget)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "qa" / "INVENTARIO_UI.md"

app = QApplication(sys.argv)
from manymaze.core.demo import create_demo_project  # noqa: E402
from manymaze.gui.main_window import MainWindow  # noqa: E402


def pump(n=3, workers_s: float = 20.0):
    """Procesa eventos, y espera (hasta workers_s) a los trabajos en segundo plano que lanzó la última acción: un
    diálogo que se abre al terminar uno (p. ej. *Check video*) se atribuye así al botón que lo abrió y no al
    siguiente que se pulse."""
    import time

    from manymaze.gui.widgets import Worker

    for _ in range(n):
        app.processEvents()
    t0 = time.monotonic()
    while Worker.running and time.monotonic() - t0 < workers_s:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(n):
        app.processEvents()


def clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("&", "")).strip()


def label_of(w: QWidget) -> str:
    """El nombre que un usuario vería junto al control."""
    if isinstance(w, QAbstractButton) and clean(w.text()):
        return clean(w.text())
    par = w.parentWidget()
    lay = par.layout() if par is not None else None
    # etiqueta de formulario
    p = w
    while p is not None and p is not w.window():
        pl = p.parentWidget().layout() if p.parentWidget() is not None else None
        if isinstance(pl, QFormLayout):
            lbl = pl.labelForField(p)
            if isinstance(lbl, QLabel) and clean(lbl.text()):
                return clean(lbl.text()).rstrip(":")
        if pl is not None:
            for fl in pl.findChildren(QFormLayout):
                lbl = fl.labelForField(p)
                if isinstance(lbl, QLabel) and clean(lbl.text()):
                    return clean(lbl.text()).rstrip(":")
        if p.parentWidget() is not None and p.parentWidget().layout() is not None:
            break
        p = p.parentWidget()
    # QLabel inmediatamente antes en el mismo layout
    if lay is not None:
        idx = lay.indexOf(w)
        for i in range(idx - 1, -1, -1):
            it = lay.itemAt(i)
            ww = it.widget() if it is not None else None
            if isinstance(ww, QLabel) and clean(ww.text()) and len(clean(ww.text())) < 60:
                return clean(ww.text()).rstrip(":")
            if ww is not None:
                break
    for t in (w.accessibleName(), w.toolTip(), getattr(w, "placeholderText", lambda: "")(), w.objectName()):
        if clean(t):
            return clean(t)[:80]
    return ""


def group_of(w: QWidget, stop: QWidget) -> str:
    p = w.parentWidget()
    while p is not None and p is not stop:
        if isinstance(p, QGroupBox) and clean(p.title()):
            return clean(p.title())
        p = p.parentWidget()
    return ""


def kind(w) -> str:
    for cls, k in ((QCheckBox, "casilla"), (QRadioButton, "opción"), (QComboBox, "lista"),
                   (QAbstractSpinBox, "número"), (QSlider, "deslizador"), (QLineEdit, "texto"),
                   (QTextEdit, "texto largo"), (QPlainTextEdit, "texto largo"), (QAbstractItemView, "tabla/lista"),
                   (QToolButton, "botón"), (QPushButton, "botón")):
        if isinstance(w, cls):
            return k
    return type(w).__name__


def details(w) -> str:
    if isinstance(w, QComboBox):
        items = [clean(w.itemText(i)) for i in range(w.count())]
        items = [i for i in items if i]
        if items:
            s = " | ".join(items[:25]) + (f" | …(+{len(items) - 25})" if len(items) > 25 else "")
            return f"valores: {s}"
    if isinstance(w, QAbstractButton):
        m = w.menu() if isinstance(w, (QToolButton, QPushButton)) else None
        if m is None and isinstance(w, QToolButton) and w.defaultAction() is not None:
            m = w.defaultAction().menu()
        if m is not None:
            try:
                m.aboutToShow.emit()  # menús que se rellenan al abrirse
            except Exception:  # noqa: BLE001
                pass
            acts = [clean(a.text()) for a in m.actions() if clean(a.text())]
            if acts:
                return "menú: " + " | ".join(acts)
    if isinstance(w, QAbstractItemView):
        model = w.model()
        if model is not None:
            try:
                ncols = model.columnCount(QModelIndex())
            except TypeError:  # modelos de lista: una sola columna
                ncols = 1
            heads = [clean(str(model.headerData(c, Qt.Horizontal) or "")) for c in range(min(ncols, 30))]
            heads = [h for h in heads if h]
            if heads:
                return "columnas: " + " | ".join(heads)
    return ""


CONTROL_TYPES = (QAbstractButton, QComboBox, QAbstractSpinBox, QSlider, QLineEdit, QTextEdit, QPlainTextEdit,
                 QAbstractItemView)

seen_widgets: set[int] = set()
rows: list[tuple[str, str, list]] = []  # (contexto, ruta para llegar, controles)


def collect(container: QWidget, context: str, route: str):
    out = []
    for w in container.findChildren(QWidget):
        if not isinstance(w, CONTROL_TYPES) or not w.isVisible():
            continue
        if isinstance(w, QHeaderView) or isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox)):
            continue
        if isinstance(w, QLineEdit) and isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox)):
            continue
        if isinstance(w, QToolButton) and w.parentWidget() is not None and \
                type(w.parentWidget()).__name__ in ("QTabBar", "QScrollBar"):
            continue
        if id(w) in seen_widgets:
            continue
        lab = label_of(w)
        if not lab and isinstance(w, QToolButton) and not w.text():
            continue  # flechas internas sin nombre
        seen_widgets.add(id(w))
        out.append((group_of(w, container), kind(w), lab or "(sin etiqueta)", details(w),
                    "" if w.isEnabled() else "deshabilitado al inicio"))
    if out:
        rows.append((context, route, out))


ribbon_seen: set = set()


def ribbon_buttons(win) -> list:
    """Los botones de la cinta visibles ahora que no se hayan listado ya en esta misma pestaña de la cinta."""
    res = []
    tab = clean(win.ribbon.tabs.tabText(win.ribbon.tabs.currentIndex()))
    for b in win.ribbon.findChildren(QToolButton):
        if b.isVisible() and clean(b.text()):
            grp = ""
            p = b.parentWidget()
            while p is not None and p is not win.ribbon:
                if type(p).__name__ == "RibbonGroup":
                    grp = clean(getattr(p, "title", ""))
                    break
                p = p.parentWidget()
            if (tab, grp, clean(b.text())) in ribbon_seen:
                continue
            ribbon_seen.add((tab, grp, clean(b.text())))
            res.append((grp, "botón de cinta", clean(b.text()), details(b),
                        "" if b.isEnabled() else "deshabilitado al inicio"))
    return res


def walk_tabs(container: QWidget, context: str, route: str):
    """Recorre cada pestaña de cada QTabWidget visible (y anidadas) recogiendo sus controles."""
    for tw in container.findChildren(QTabWidget):
        if not tw.isVisible():
            continue
        cur = tw.currentIndex()
        for i in range(tw.count()):
            tw.setCurrentIndex(i)
            pump()
            sub = f"{context} › pestaña «{clean(tw.tabText(i))}»"
            walk_tabs(tw.widget(i), sub, f"{route} › pestaña «{clean(tw.tabText(i))}»")
            collect(tw.widget(i), sub, f"{route} › pestaña «{clean(tw.tabText(i))}»")
        tw.setCurrentIndex(cur)
        pump()


def mac_menu(menu: str, act) -> str:
    """El menú en que macOS muestra la acción: Qt pasa About, Quit y Settings/Preferences al menú de la aplicación
    («mANY-MAZE»), por su menuRole o, con el rol por defecto, por su texto."""
    from PySide6.QtGui import QAction

    R = QAction.MenuRole
    role = act.menuRole()
    t = clean(act.text()).lower()
    if role in (R.AboutRole, R.AboutQtRole, R.QuitRole, R.PreferencesRole) or (
            role == R.TextHeuristicRole and (t.startswith("about") or t in ("quit", "exit")
                                             or t.startswith(("settings", "preferences", "setup", "config", "options")))):
        return f"mANY-MAZE (menú de la aplicación; en el código, {menu})"
    return menu


def main():
    win = MainWindow()
    win.show()
    pump(5)
    # pantalla de bienvenida y barra de menús
    menus = []
    for m in win.menuBar().actions():
        if m.menu() is None:
            continue
        for a in m.menu().actions():
            if clean(a.text()):
                sub = ""
                if a.menu():
                    sub = "submenú: " + " | ".join(clean(b.text()) for b in a.menu().actions() if clean(b.text()))
                menus.append((mac_menu(clean(m.text()), a), "menú", clean(a.text()), sub, a.shortcut().toString()))
    rows.append(("Barra de menús de macOS", "barra superior de la pantalla", menus))
    collect(win.welcome, "Pestaña File (sin experimento abierto)", "pestaña azul File")

    print("fase 1/4: páginas, sub-páginas, pestañas y modos de Run tests", flush=True)
    tmp = Path(tempfile.mkdtemp())
    proj = create_demo_project(tmp / "Demo open field.mmaze")
    demo_src = Path(tempfile.mkdtemp()) / "Demo open field.mmaze"
    shutil.copytree(proj.path, demo_src)
    win.set_project(proj)
    pump(5)
    collect(win.welcome, "Pestaña File (con experimento abierto)", "pestaña azul File")
    corner = []
    for i in range(win.ribbon.corner.count()):
        b = win.ribbon.corner.itemAt(i).widget()
        if b is not None and b.isVisible():
            corner.append(("esquina de la cinta", "botón", clean(b.text()) or clean(b.toolTip()) or "(icono)",
                           details(b), ""))
    if corner:
        rows.append(("Esquina superior derecha de la cinta", "siempre visible", corner))

    for sec in win.sections:
        for page in sec.pages:
            win.show_page(page)
            pump(5)
            it = sec.item_for(page)
            pname = clean(it.text(0)) if it is not None else type(page).__name__
            stitle = clean(getattr(sec, "title", "")) or clean(win.ribbon.tabs.tabText(win.sections.index(sec) + 1))
            children = [(clean(it.child(j).text(0)), it.child(j).data(0, 0x0100 + 1)) for j in
                        range(it.childCount())] if it is not None else []
            targets = [(None, None)] + children
            for cname, key in targets:
                if key is not None:
                    try:
                        page.show_item(key)
                    except Exception as e:  # noqa: BLE001
                        print("show_item falló", pname, key, e)
                    pump(5)
                ctx = f"{stitle} ▸ {pname}" + (f" ▸ {cname}" if cname else "")
                route = f"pestaña {stitle} → explorador «{pname}»" + (f" → «{cname}»" if cname else "")
                rb = ribbon_buttons(win)
                if rb:
                    rows.append((f"{ctx} — CINTA", route, rb))
                walk_tabs(page, ctx, route)
                collect(page, ctx, route)
            # modos de la página Run tests
            if type(page).__name__ == "LivePage":
                for mode in ("Several tests", "Observation only", "One test"):
                    btn = next((b for b in win.ribbon.findChildren(QToolButton)
                                if b.isVisible() and clean(b.text()) == mode), None)
                    if btn is None:
                        continue
                    btn.click()
                    pump(8)
                    ctx = f"{stitle} ▸ {pname} ▸ modo «{mode}»"
                    route = f"pestaña {stitle} → «{pname}» → botón de cinta «{mode}»"
                    rb = ribbon_buttons(win)
                    if rb:
                        rows.append((f"{ctx} — CINTA", route, rb))
                    walk_tabs(page, ctx, route)
                    collect(page, ctx, route)
    print("fase 2/4: modos del protocolo", flush=True)
    mode_pass(win, demo_src)
    print("fase 3/4: pulsar todo (modo video tracking)", flush=True)
    explore(win, demo_src)
    for mode in ("takenote", "io_only"):  # Protocol y Test cambian con el modo
        print(f"fase 3/4: pulsar todo (modo {mode})", flush=True)
        explore(win, demo_src, mode, sections={0, 2})
    print("fase 4/4: catálogos", flush=True)
    static_catalogs()
    report_exploration()
    if captured["errors"]:
        (OUT.parent / "errores_al_pulsar.txt").write_text(
            "\n\n".join(f"== {r}\n{tb}" for r, tb in captured["errors"]), encoding="utf-8")
    # diálogos (títulos sacados del código)
    titles = set()
    for f in (ROOT / "manymaze" / "gui").rglob("*.py"):
        for m in re.finditer(r'setWindowTitle\(f?"([^"]+)"', f.read_text(encoding="utf-8")):
            t = m.group(1)
            if "APP_NAME" in t or "self.project.name" in t:
                continue
            titles.add(re.sub(r"\{[^}]*\}", "…", t))
    opened = {re.sub(r" \(ventana\)$", "", t) for _, t, _ in captured["dialogs"]}
    rows.append(("Diálogos que deben abrirse al menos una vez", "desde el botón o menú que los abre",
                 [("", "diálogo", t, "abrir, revisar cada control, Cancelar y luego usarlo de verdad",
                   "abierto en la exploración automática" if any(t.split("…")[0] in o for o in opened)
                   else "NO se abrió solo: hace falta preparar datos para llegar")
                  for t in sorted(titles)]))
    write(win)
    win.dirty = False
    win.close()


# ================================================================================================ pulsar todo
# Segunda fase: con el experimento de demostración, pulsa cada botón de la cinta, cada ítem de sus menús y cada
# botón «…» de las páginas, en cada sub-página del explorador y en los tres modos del protocolo. Los diálogos se
# interceptan: se inventarían (con sus pestañas y un nivel de sub-diálogos) y se cancelan; los diálogos de archivos
# devuelven «cancelado»; los mensajes se anotan. Las excepciones que salten quedan en «Errores al pulsar».
import shutil  # noqa: E402
import traceback  # noqa: E402

from PySide6.QtGui import QColor, QDesktopServices  # noqa: E402
from PySide6.QtWidgets import (QColorDialog, QDialog, QFileDialog, QInputDialog, QMenu,  # noqa: E402
                               QMessageBox)

SKIP = {  # no se pulsan: cierran la app, arrancan pruebas en vivo / rastreos largos, o salen a internet
    "Quit", "Start all", "Stop all", "Pause all", "Resume all", "Track all untracked", "Track selected",
    "Track this test", "Play", "Check for updates", "Check for updates…", "Close experiment", "Start observation",
    "Arm / Start test", "Start", "Stop", "Pause", "Stop and save", "Scan", "Capture backgrounds", "Camera image",
    "Show the camera image (preview)", "Capture the empty-arena background", "Export video", "Export video…",
    "Save playback", "Run on the tests", "Play the track", "Next test", "Install…", "Install pose model",
    "Recalculate", "Run",
    # crean un experimento en segundo plano tras una barra de progreso modal; la exploración cambiaría de experimento
    # a mitad (un usuario no puede): se prueban en la app real (B01-03)
    "Create demo experiment", "Open demo experiment",
}
NAV = {"Protocol", "Apparatus", "Experiment", "Test schedule", "Run tests", "Review and score", "Data",
       "Statistics", "One test", "Several tests", "Observation only"}

captured = {"dialogs": [], "messages": [], "files": [], "menus": [], "urls": [], "errors": []}
inventoried_dialogs: set = set()
_depth = [0]
_orig = {}
_route = [""]


def _dialog_controls(dlg, title):
    """Inventaría un diálogo mostrado, sus pestañas y (un nivel) los sub-diálogos de sus botones «…». Cada diálogo
    (por título) se inventaría una vez; las demás veces solo se anota desde dónde se abre."""
    if title in inventoried_dialogs:
        return 0
    inventoried_dialogs.add(title)
    dlg.show()
    pump(3)
    ctx = f"Diálogo «{title}»"
    before = len(rows)
    walk_tabs(dlg, ctx, f"{_route[0]} → diálogo «{title}»")
    collect(dlg, ctx, f"{_route[0]} → diálogo «{title}»")
    added = sum(len(r[2]) for r in rows[before:])
    if _depth[0] < 2:
        for b in [b for b in dlg.findChildren(QPushButton) if b.isVisible() and b.isEnabled()
                  and clean(b.text()).endswith("…")]:
            old = _route[0]
            _route[0] = f"{old} → diálogo «{title}» → «{clean(b.text())}»"
            try:
                b.click()
                pump(3)
            except Exception:  # noqa: BLE001
                captured["errors"].append((_route[0], traceback.format_exc()))
            _route[0] = old
    dlg.hide()
    return added


def _fake_exec(self, *a, **k):
    if isinstance(self, QMessageBox):
        captured["messages"].append((_route[0], clean(self.windowTitle()), clean(self.text())))
        return QMessageBox.Cancel
    title = clean(self.windowTitle()) or type(self).__name__
    _depth[0] += 1
    try:
        n = _dialog_controls(self, title)
        captured["dialogs"].append((_route[0], title, n))
    except Exception:  # noqa: BLE001
        captured["errors"].append((_route[0] + f" (diálogo {title})", traceback.format_exc()))
    finally:
        _depth[0] -= 1
    return QDialog.Rejected


def _fake_box(kind):
    def f(parent, title, text, *a, **k):
        captured["messages"].append((_route[0], f"{kind}: {clean(title)}", clean(text)))
        return QMessageBox.Cancel if kind == "question" else QMessageBox.Ok
    return staticmethod(f)


FIXTURES: dict = {}  # extensión → archivo de prueba que se devuelve al «abrir» (lo rellena make_fixtures)


def make_fixtures(demo_src: Path):
    """Archivos de prueba para que los diálogos de abrir lleguen más lejos: un video, un CSV de DeepLabCut, un CSV de
    animales y un XML / JSON cualquiera."""
    d = Path(tempfile.mkdtemp())
    vid = next(iter(sorted((demo_src / "videos").glob("*.avi"))), None)
    dlc = d / "track_dlc.csv"
    lines = ["scorer," + ",".join(["DLC"] * 6), "bodyparts,nose,nose,nose,tail,tail,tail",
             "coords,x,y,likelihood,x,y,likelihood"]
    lines += [f"{i},{100 + i},{100},{0.99},{80 + i},{100},{0.99}" for i in range(50)]
    dlc.write_text("\n".join(lines) + "\n")
    animals = d / "animales.csv"
    animals.write_text("Animal ID,Treatment,Sex\nZ1,Control,Male\nZ2,Drug,Female\n")
    FIXTURES.update({"video": str(vid) if vid else "", "csv": str(animals), "dlc": str(dlc),
                     "json": str(demo_src / "project.json"), "dir": str(d)})


def _fixture_for(caption: str, filt: str) -> str:
    text = f"{caption} {filt}".lower()
    if "deeplabcut" in text or "track" in text and "csv" in text:
        return FIXTURES.get("dlc", "")
    if any(e in text for e in ("*.avi", "*.mp4", "*.mov", "video")):
        return FIXTURES.get("video", "")
    if "project.json" in text or "*.json" in text:
        return FIXTURES.get("json", "")
    if any(e in text for e in ("*.csv", "*.xlsx", "*.txt", "spreadsheet", "table")):
        return FIXTURES.get("csv", "")
    return ""


def _fake_file(name):
    def f(*a, **k):
        args = [x for x in a if isinstance(x, str)]
        cap = args[0] if args else k.get("caption", "")
        filt = args[2] if len(args) > 2 else k.get("filter", "")
        captured["files"].append((_route[0], name, clean(cap)))
        if name == "getExistingDirectory":
            return tempfile.mkdtemp()
        if name == "getSaveFileName":
            ext = re.search(r"\*(\.[A-Za-z0-9]+)", filt or "")
            return str(Path(tempfile.mkdtemp()) / ("salida" + (ext.group(1) if ext else ".dat"))), filt or ""
        path = _fixture_for(cap, filt or "")
        if "Names" in name:
            return ([path] if path else []), filt or ""
        return path, filt or ""
    return staticmethod(f)


def _fake_menu_exec(self, *a, **k):
    try:
        self.aboutToShow.emit()
    except Exception:  # noqa: BLE001
        pass
    captured["menus"].append((_route[0], [clean(x.text()) for x in self.actions() if clean(x.text())]))
    return None


def patch_modals():
    _orig["exec"] = QDialog.exec
    from PySide6.QtWidgets import QErrorMessage, QFontDialog, QProgressDialog, QWizard

    modal = [QDialog, QMessageBox, QFileDialog, QColorDialog, QFontDialog, QInputDialog, QErrorMessage, QWizard]
    try:  # los diálogos de impresión tienen su propio exec (y son nativos: se quedarían esperando)
        from PySide6.QtPrintSupport import QPageSetupDialog, QPrintDialog, QPrintPreviewDialog

        modal += [QPrintDialog, QPageSetupDialog, QPrintPreviewDialog]
    except ImportError:
        pass
    for cls in modal:
        cls.exec = _fake_exec
    QProgressDialog.exec = lambda self, *a, **k: 0
    for kind in ("warning", "critical", "question", "information"):
        setattr(QMessageBox, kind, _fake_box(kind))
    for name in ("getOpenFileName", "getSaveFileName", "getExistingDirectory", "getOpenFileNames"):
        setattr(QFileDialog, name, _fake_file(name))
    QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("", False))
    QColorDialog.getColor = staticmethod(lambda *a, **k: QColor())
    QMenu.exec = _fake_menu_exec
    QDesktopServices.openUrl = staticmethod(lambda url: captured["urls"].append((_route[0], url.toString())) or True)

    def hook(et, ev, tb):
        captured["errors"].append((_route[0], "".join(traceback.format_exception(et, ev, tb))))
    sys.excepthook = hook

    # una acción que cierra la aplicación queda anotada en lugar de terminar la exploración
    def no_exit(*a, **k):
        captured["errors"].append((_route[0], f"La acción intentó cerrar la aplicación (exit {a[:1]})"))
    if "sys_exit" not in _orig:
        _orig["sys_exit"], _orig["os_exit"] = sys.exit, os._exit
    sys.exit = no_exit
    os._exit = no_exit
    QApplication.quit = staticmethod(lambda: no_exit("quit"))
    QApplication.exit = staticmethod(lambda *a: no_exit("exit", *a))


def guarded(fn):
    """Pulsa algo con un vigilante: una ventana modal no interceptada que bloquee más de 15 s se cierra y se anota."""
    from PySide6.QtCore import QTimer

    route = _route[0]
    if os.environ.get("QA_TRAZA"):
        with open(os.environ["QA_TRAZA"], "a", encoding="utf-8") as fh:
            fh.write(route + "\n")

    def unblock():
        w = QApplication.activeModalWidget()
        if w is not None:
            captured["errors"].append((route, f"Bloqueado: ventana modal «{clean(w.windowTitle())}» "
                                              f"({type(w).__name__}) sin interceptar; cerrada a los 15 s"))
            w.close()
    t = QTimer()
    t.setSingleShot(True)
    t.timeout.connect(unblock)
    t.start(15000)
    try:
        fn()
    finally:
        t.stop()


def wait_workers():
    from manymaze.gui.widgets import Worker

    import time

    end = time.monotonic() + 20
    while any(w.isRunning() for w in list(Worker.running)) and time.monotonic() < end:
        pump(2)
        time.sleep(0.02)
    if any(w.isRunning() for w in list(Worker.running)):
        Worker.stop_all(5000)


def close_strays(win):
    """Cierra (tras inventariarlas) las ventanas no modales que haya abierto la acción."""
    for w in QApplication.topLevelWidgets():
        if w is win or not w.isVisible() or isinstance(w, QMenu):
            continue
        title = clean(w.windowTitle()) or type(w).__name__
        try:
            _dialog_controls(w, title)
            captured["dialogs"].append((_route[0], title + " (ventana)", 0))
        except Exception:  # noqa: BLE001
            pass
        w.close()
    pump(2)


def fresh_project(win, demo_src: Path):
    from manymaze.core.project import Project

    dst = Path(tempfile.mkdtemp()) / demo_src.name
    shutil.copytree(demo_src, dst)
    win.dirty = False
    win.set_project(Project.load(dst))
    pump(5)


def select_first_rows(page):
    """Hace clic (como un usuario) en la primera fila de cada tabla / lista visible: habilita los comandos que piden
    una selección. Antes espera a que las tablas que se calculan en segundo plano (Results) tengan filas."""
    import time

    from PySide6.QtTest import QTest

    wait_workers()
    end = time.monotonic() + 20
    while time.monotonic() < end:
        empty = [v for v in page.findChildren(QAbstractItemView) if v.isVisible() and not isinstance(v, QHeaderView)
                 and v.objectName() == "ResultsTable" and v.model() is not None and v.model().rowCount() == 0]
        if not empty:
            break
        pump(3)
        time.sleep(0.05)
    for v in page.findChildren(QAbstractItemView):
        if isinstance(v, QHeaderView) or not v.isVisible():
            continue
        try:
            m = v.model()
            if m is None or m.rowCount() == 0:
                continue
            idx = m.index(0, 0)
            rect = v.visualRect(idx)
            if rect.isValid():
                QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
            else:
                v.setCurrentIndex(idx)
        except Exception:  # noqa: BLE001
            pass
    pump(3)


def triggers_here(win, page):
    """(etiqueta, función) de lo que se puede pulsar ahora: cinta (y sus menús) y botones «…» de la página."""
    out, seen = [], set()
    for b in win.ribbon.findChildren(QToolButton):
        if not (b.isVisible() and b.isEnabled() and clean(b.text())):
            continue
        lab = clean(b.text())
        act = b.defaultAction()
        menu = b.menu() or (act.menu() if act is not None else None)
        if menu is not None:
            try:
                menu.aboutToShow.emit()
            except Exception:  # noqa: BLE001
                pass
            for a in menu.actions():
                if clean(a.text()) and a.isEnabled() and not a.menu():
                    out.append((f"cinta «{lab}» ▸ «{clean(a.text())}»", a.trigger))
        if lab not in NAV and lab not in seen:
            seen.add(lab)
            out.append((f"cinta «{lab}»", b.click))
    for b in page.findChildren(QPushButton):
        if b.isVisible() and b.isEnabled() and clean(b.text()).endswith("…"):
            out.append((f"botón «{clean(b.text())}»", b.click))
    def skipped(lab):
        last = lab.split("«")[-1].rstrip("»")
        return last in SKIP or last.startswith(("Start", "Arm", "Track", "Stop", "Pause", "Resume"))
    return [(lab, fn) for lab, fn in out if not skipped(lab)]


def set_mode(win, mode: str):
    """Protocol ▸ Mode: tracking | takenote | io_only (como lo haría el usuario, con su lista)."""
    prot = win.page("ExperimentPage")
    cb = getattr(prot, "mode", None)
    if isinstance(cb, QComboBox):
        cb.setCurrentIndex(max(0, cb.findData(mode)))
        pump(3)


def explore(win, demo_src: Path, mode: str = "tracking", sections=None):
    patch_modals()
    make_fixtures(demo_src)
    # pestaña File y barra de menús
    fresh_project(win, demo_src)
    set_mode(win, mode)
    for m in (win.menuBar().actions() if mode == "tracking" else []):
        if m.menu() is None:
            continue
        for a in m.menu().actions():
            lab = clean(a.text())
            if not lab or a.menu() or lab in SKIP or not a.isEnabled():
                continue
            _route[0] = f"menú {clean(m.text())} ▸ {lab}"
            try:
                guarded(a.trigger)
                pump(3)
                wait_workers()
            except Exception:  # noqa: BLE001
                captured["errors"].append((_route[0], traceback.format_exc()))
            close_strays(win)
            if win.project is None or win.dirty:
                fresh_project(win, demo_src)
    for si, sec in enumerate(win.sections):
        if sections is not None and si not in sections:
            continue
        for page in list(sec.pages):
            it = sec.item_for(page)
            if it is None:
                continue
            pname = clean(it.text(0))
            keys = [None] + [it.child(j).data(0, 0x0100 + 1) for j in range(it.childCount())]
            cnames = [None] + [clean(it.child(j).text(0)) for j in range(it.childCount())]
            for key, cname in zip(keys, cnames):
                fresh_project(win, demo_src)
                set_mode(win, mode)
                win.show_page(page)
                pump(3)
                if key is not None:
                    page.show_item(key)
                    pump(3)
                select_first_rows(page)
                where = (f"[modo {mode}] " if mode != "tracking" else "") + f"{pname}" + (f" ▸ {cname}" if cname
                                                                                          else "")
                done = set()
                for lab, fn in triggers_here(win, page):
                    if lab in done:
                        continue
                    done.add(lab)
                    _route[0] = f"{where}: {lab}"
                    try:
                        guarded(fn)
                        pump(3)
                        wait_workers()
                    except Exception:  # noqa: BLE001
                        captured["errors"].append((_route[0], traceback.format_exc()))
                    close_strays(win)
                    if win.project is None or win.dirty or win.current_page() is not page:
                        fresh_project(win, demo_src)
                        set_mode(win, mode)
                        win.show_page(page)
                        pump(2)
                        if key is not None:
                            page.show_item(key)
                            pump(2)
                        select_first_rows(page)
    sys.excepthook = sys.__excepthook__
    sys.exit, os._exit = _orig["sys_exit"], _orig["os_exit"]


def mode_pass(win, demo_src: Path):
    """Las páginas que cambian con el modo del protocolo (TakeNote, solo I/O), y con varios aparatos."""
    from manymaze.core.project import Project

    for mode_label in ("TakeNote", "Input/output only"):
        dst = Path(tempfile.mkdtemp()) / demo_src.name
        shutil.copytree(demo_src, dst)
        pr = Project.load(dst)
        win.dirty = False
        win.set_project(pr)
        pump(5)
        set_mode(win, {"TakeNote": "takenote", "Input/output only": "io_only"}[mode_label])
        for cls in ("ExperimentPage", "LivePage", "TestViewPage", "TestsPage"):
            page = win.page(cls)
            if page is None:
                continue
            win.show_page(page)
            pump(5)
            sec = win.sections[win._page_section[id(page)]]
            pname = clean(sec.item_for(page).text(0))
            ctx = f"Modo «{mode_label}» ▸ {pname}"
            rb = ribbon_buttons(win)
            if rb:
                rows.append((f"{ctx} — CINTA", f"Protocol ▸ Mode = {mode_label} → {pname}", rb))
            walk_tabs(page, ctx, f"Protocol ▸ Mode = {mode_label} → {pname}")
            collect(page, ctx, f"Protocol ▸ Mode = {mode_label} → {pname}")


def static_catalogs():
    """Mensajes de la app (del código), elementos de los procedimientos y pruebas estadísticas."""
    import ast

    msgs = []
    for f in sorted((ROOT / "manymaze" / "gui").rglob("*.py")):
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    funcs.setdefault(id(sub), node.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    isinstance(node.func.value, ast.Name) and node.func.value.id == "QMessageBox" and \
                    node.func.attr in ("warning", "critical", "question", "information"):
                parts = [ast.unparse(a)[:160] for a in node.args[1:3]]
                rel = f.relative_to(ROOT)
                msgs.append((node.func.attr, f"{rel}:{node.lineno}", funcs.get(id(node), "—"),
                             " — ".join(parts)))
    rows.append(("Mensajes de la app que hay que provocar al menos una vez",
                 "cada uno sale de la función indicada: deduce qué lo provoca (entrada inválida, archivo que falta, "
                 "confirmación…) y provócalo", [("", f"mensaje ({k})", text.replace("\n", " "), f"{where} · función "
                                                 f"{fn}", "") for k, where, fn, text in msgs]))
    from manymaze.core.procedures import ACTION_SPECS, EVENT_SPECS, FUNCTIONS

    rows.append(("Procedimientos: eventos (cada uno en un «when»)", "Protocol ▸ Procedures → Add ▾ → when",
                 [(v.get("group", ""), "evento", v.get("label", k),
                   "parámetros: " + ", ".join(pp.get("label", pp["name"]) for pp in v.get("params", [])), k)
                  for k, v in EVENT_SPECS.items()]))
    rows.append(("Procedimientos: acciones (cada una en un «do»)", "Protocol ▸ Procedures → Add ▾ → do",
                 [(v.get("group", ""), "acción", v.get("label", k),
                   "parámetros: " + ", ".join(pp.get("label", pp["name"]) for pp in v.get("params", [])), k)
                  for k, v in ACTION_SPECS.items()]))
    rows.append(("Procedimientos: funciones de las expresiones", "Procedures → Functions…",
                 [("", "función", k, str(v[3]) if len(v) > 3 else "", f"{v[0]}–{v[1]} argumentos")
                  for k, v in FUNCTIONS.items()]))
    from manymaze.core.stats import TESTS

    rows.append(("Estadística: las 47 pruebas (cada una al menos una vez)", "Results ▸ Statistics (ver "
                 "ESPERADO_ESTADISTICA.md)", [(kind, "prueba", name, "", "") for name, kind in TESTS.items()]))


def report_exploration():
    if captured["messages"]:
        seen = set()
        items = []
        for route, title, text in captured["messages"]:
            if (title, text) in seen:
                continue
            seen.add((title, text))
            items.append(("", "mensaje", f"{title}: {text}"[:200], f"aparece al pulsar: {route}", ""))
        rows.append(("Mensajes que aparecen al pulsar sin preparar datos (comprueba que sean claros)",
                     "pulsando lo indicado con el demo recién abierto", items))
    if captured["errors"]:
        items = []
        for route, tb in captured["errors"]:
            last = [ln for ln in tb.strip().splitlines() if ln.strip()][-1]
            items.append(("", "ERROR", last[:200], f"al pulsar: {route}", "posible fallo: reprodúcelo en la app"))
        rows.append(("⚠ Errores (excepciones) al pulsar — posibles fallos a confirmar", "ver «al pulsar»", items))
    return captured


def write(win):
    lines = ["# Inventario de la interfaz de mANY-MAZE (generado)", "",
             "Generado por `qa/herramientas/generar_inventario.py` con el experimento de demostración abierto. "
             "**No editar a mano**: vuelve a generarlo cuando cambie la interfaz.", "",
             "Cada fila es un control que la prueba debe tocar al menos una vez. El ID es estable (contexto + "
             "nombre). En la bitácora, anota cada ID con su resultado: `OK`, `FALLA` (con el ID del hallazgo), "
             "`BLOQUEADO` (motivo) o `N/A` (motivo, p. ej. sin hardware).", "",
             "Leyenda de tipos: botón, botón de cinta, menú, casilla, opción, lista (desplegable), número, texto, "
             "deslizador, tabla/lista, diálogo.", ""]
    total = 0
    used = set()
    for ctx, route, items in rows:
        lines += [f"## {ctx}", "", f"Cómo llegar: {route}", "", "| ID | Grupo | Tipo | Control | Detalles | Notas |",
                  "|---|---|---|---|---|---|"]
        prefix = re.sub(r"[^A-Z0-9]", "", "".join(w[:3].upper() for w in re.findall(r"\w+", ctx)))[:12] or "UI"
        for grp, k, lab, det, note in items:
            h = hashlib.sha1(f"{ctx}|{grp}|{k}|{lab}".encode()).hexdigest()[:4].upper()
            iid = f"{prefix}-{h}"
            n = 2
            while iid in used:
                iid = f"{prefix}-{h}{n}"
                n += 1
            used.add(iid)
            esc = lambda s: str(s).replace("|", "¦")  # noqa: E731
            lines.append(f"| `{iid}` | {esc(grp)} | {k} | {esc(lab)} | {esc(det)} | {esc(note)} |")
            total += 1
        lines.append("")
    lines.insert(2, f"**{total} controles en {len(rows)} contextos.**\n")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{OUT}: {total} controles, {len(rows)} contextos")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # sin esto, el cierre de Qt offscreen con hilos de páginas vivos aborta (ya se escribió el archivo)
