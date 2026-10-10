"""Dark theme (View ▸ Appearance: System / Light / Dark): the light scheme as before, the dark one applied to the
ribbon, explorer, tables, custom-drawn widgets, icons and matplotlib figures, the choice kept in the app settings;
screenshots of both schemes (MANYMAZE_SHOT_DIR) and contrast checks."""

import shutil
from collections import Counter

import pytest
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui import figures, theme
from manymaze.gui.icons import DARK_TINTS, icon
from manymaze.gui.live_widgets.panels import STATE_STYLE
from manymaze.gui.main_window import MainWindow
from manymaze.gui.statement_tree import BLOCK_COLORS
from shots import shot_path

app = QApplication.instance() or QApplication([])
TEXT_PAIRS = [("TEXT", "BASE"), ("TEXT", "WORK_BG"), ("TEXT", "RIBBON_BG"), ("TEXT", "SELECTION"), ("TEXT", "HOVER"),
              ("TEXT", "BUTTON"), ("TEXT", "HEADER_BG"), ("TEXT", "PANEL_HEAD"), ("TEXT", "NOTE_BG"),
              ("TEXT", "ERROR_BG"), ("TEXT", "READY_BG"), ("MUTED", "WORK_BG"), ("MUTED", "BASE"),
              ("HEADING", "WORK_BG"), ("HEADING", "BASE"), ("ACCENT", "WORK_BG"), ("ACCENT", "BASE"),
              ("ON_ACCENT", "ACCENT_BG"), ("ERROR", "BASE"), ("OK", "BASE"), ("WARNING", "BASE"), ("LINK", "BASE"),
              ("HINT", "BASE"), ("NOTE", "WORK_BG"), ("SLATE", "BASE")]


@pytest.fixture
def restore_theme():
    """Leave the application as the other tests expect it (no theme applied, light colours)."""
    sheet, pal, style = app.styleSheet(), QPalette(app.palette()), app.style().name()
    yield
    theme._set_scheme("light")
    theme._appearance = "system"
    try:
        app.styleHints().unsetColorScheme()
    except AttributeError:
        pass
    app.setStyleSheet(sheet)
    app.setPalette(pal)
    app.setStyle(style)


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("theme") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=5)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch, restore_theme):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def lum(c: QColor) -> float:
    return theme.contrast(c.name(), "#000000") / 21.0


def region_contrast(img: QImage, x0, y0, x1, y1) -> tuple[QColor, float]:
    """The background (most common colour) of a region of a screenshot and the best contrast of any pixel on it
    (the text drawn there)."""
    counts = Counter(img.pixelColor(x, y).name() for x in range(x0, x1, 2) for y in range(y0, y1, 2))
    bg = counts.most_common(1)[0][0]
    best = max(theme.contrast(c, bg) for c in counts)
    return QColor(bg), best


def test_scheme_tokens_and_contrast():
    assert theme.LIGHT["WORK_BG"] == "#f9f9f9" and theme.LIGHT["TEXT"] == "#1f1f1f"  # ANY-maze's light look
    assert set(theme.LIGHT) == set(theme.DARK)
    for a, b in TEXT_PAIRS:  # text: WCAG AA in the dark scheme; the light scheme keeps its (ANY-maze) colours
        assert theme.contrast(theme.DARK[a], theme.DARK[b]) >= 4.5, (a, b)
        assert theme.contrast(theme.LIGHT[a], theme.LIGHT[b]) >= 3.5, (a, b)
    for a in ("INACTIVE", "DISABLED", "READY_FG"):  # dimmed on purpose, still readable
        assert theme.contrast(theme.DARK[a], theme.DARK["BASE"]) >= 3
    # custom-drawn widgets: icons in the dark scheme, procedure blocks, live panel states, live chart line
    for tint in DARK_TINTS.values():
        assert theme.contrast(tint, theme.DARK["RIBBON_BG"]) >= 3 and theme.contrast(tint, theme.DARK["WORK_BG"]) >= 3
    for fill, _border in BLOCK_COLORS.values():  # dark text on the pastel blocks, on either canvas
        assert theme.contrast("#262626", fill) >= 4.5
        assert theme.contrast(fill, theme.DARK["BASE"]) >= 3
    for _text, col in STATE_STYLE.values():  # the state pill of a live panel stands out on the dark panel head
        assert theme.contrast(col, theme.DARK["PANEL_HEAD"]) >= 2
    from manymaze.gui.live_widgets.monitor import LINE, LINE_DARK

    assert theme.contrast(LINE_DARK, theme.DARK["BASE"]) >= 4.5 and theme.contrast(LINE, theme.LIGHT["BASE"]) >= 4.5


def test_icons_follow_the_scheme(restore_theme):
    ic = icon("back")
    light = {ic.pixmap(32, 32).toImage().pixelColor(16, 16).name()}
    theme._set_scheme("dark")
    img = ic.pixmap(32, 32).toImage()
    dark = {img.pixelColor(x, y).name() for x in range(32) for y in range(32) if img.pixelColor(x, y).alpha() > 250}
    assert dark == {DARK_TINTS["#2f6fbf"]} and light != dark
    pm = ic.pixmap(QSize(24, 24), 2.0)  # Retina: drawn at the device's resolution
    assert pm.size() == QSize(48, 48) and pm.devicePixelRatio() == 2.0


def test_appearance_menu_screenshots_and_figures(win, tmp_path):
    assert set(win.appearance_actions) == {"system", "light", "dark"}
    shots = {}
    for scheme in ("light", "dark"):
        win.appearance_actions[scheme].trigger()
        assert theme.scheme() == scheme and theme.appearance() == scheme
        assert win.settings.value("appearance") == scheme and theme.saved_appearance(win.settings) == scheme
        assert win.appearance_actions[scheme].isChecked()
        assert app.palette().color(QPalette.Window) == QColor(theme.WORK_BG)
        for name in ("TestsPage", "ExperimentPage", "LivePage", "ResultsPage"):
            page = win.goto(name)
            if name == "ResultsPage":
                page.wait_loaded()
                page.set_view("heat")
                page.table.selectRow(0)
                page.tabs.setCurrentIndex(1)
                page.render_all()
            app.processEvents()
            img = win.grab().toImage()
            assert img.save(shot_path(f"shot_{scheme}_{name}.png"))
            shots[scheme, name] = img
        # the ribbon, the explorer and the test schedule: the scheme's background, readable text
        img = shots[scheme, "TestsPage"]
        for box in ((0, 22, 1400, 140), (0, 150, 198, 260), (220, 200, 1380, 350)):
            bg, best = region_contrast(img, *box)
            assert (lum(bg) < 0.3) == (scheme == "dark"), (scheme, box, bg.name())
            assert best >= 4.5, (scheme, box)
    # restyled widgets, the live view and the heat map figure follow the scheme
    win.set_appearance("dark")
    res = win.goto("ResultsPage")
    fig = res.heat_canvas.figure
    res.heat_canvas.canvas.draw()
    assert lum(QColor(figures.to_hex(fig.patch.get_facecolor()))) < 0.3
    assert lum(QColor(figures.to_hex(fig.axes[1].yaxis.label.get_color()))) > 0.5  # colour bar label
    out = tmp_path / "heat.png"
    res.save_figure(str(out))  # saved figures stay light
    saved = QImage(str(out))
    assert lum(saved.pixelColor(2, 2)) > 0.9
    assert lum(QColor(figures.to_hex(fig.patch.get_facecolor()))) < 0.3  # still dark on screen
    live = win.page("LivePage")
    assert lum(live.view.backgroundBrush().color()) < 0.3
    assert theme.contrast(theme.TEXT, theme.BASE) >= 4.5
    # System follows macOS (here: whatever the platform says)
    win.set_appearance("system")
    assert theme.appearance() == "system" and theme.scheme() == theme.system_scheme(app)
    assert win.settings.value("appearance") == "system"
    # light again: exactly the light tokens
    win.set_appearance("light")
    assert theme.WORK_BG == "#f9f9f9" and app.palette().color(QPalette.Base) == QColor("#ffffff")
    assert lum(QColor(figures.to_hex(fig.patch.get_facecolor()))) > 0.9
