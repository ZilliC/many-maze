"""Visual theme in the style of ANY-maze: light Office-like ribbon over a near-white work area, blue headings,
light-blue selection, orange apparatus outlines — and the same layout in a dark scheme.

*View ▸ Appearance* chooses **System** (follow macOS), **Light** or **Dark**; the choice is kept in the app settings
(``appearance``). The colours are named tokens (``LIGHT`` / ``DARK``): the module-level names (``theme.TEXT``,
``theme.WORK_BG`` …) always hold the scheme in use, so code that reads them when it paints or fills a table follows
the scheme. Widgets with a style sheet of their own register it with :func:`style` (a function giving the sheet),
and widgets that draw with theme colours can implement ``theme_changed()``: both are refreshed when the scheme
changes. Matplotlib figures shown in the window follow the scheme (gui.figures); saved, copied and exported figures
and documents stay light.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

ICONS = (Path(__file__).resolve().parent.parent / "resources" / "icons").as_posix()
APPEARANCES = (("system", "System"), ("light", "Light"), ("dark", "Dark"))
SETTINGS_KEY = "appearance"

# the colour tokens of each scheme
LIGHT = {
    "RIBBON_BG": "#f3f3f3", "WORK_BG": "#f9f9f9", "BASE": "#ffffff", "ALT_BASE": "#fafbfd", "BORDER": "#d6d6d6",
    "GRID": "#e3e3e3", "SHEET_GRID": "#e6e6e6", "ROW_LINE": "#f0f0f0", "HEADER_LINE": "#ececec",
    "HEADER_BG": "#fbfbfb", "SPIN_LINE": "#e2e2e2",
    "HEADING": "#3f6fb5",  # page / section titles
    "SELECTION": "#a9c3e8",  # selected rows, pressed ribbon buttons
    "SELECTION_BORDER": "#8fb0de", "HOVER": "#dce8f6", "HOVER_BORDER": "#c5d7ef",
    "TEXT": "#1f1f1f", "MUTED": "#6b7280", "HINT": "#475569", "DISABLED": "#a6a6a6", "PLACEHOLDER": "#9aa1ab",
    "ACCENT": "#2f6fbf",  # links, the selected tab
    "ACCENT_BG": "#2f6fbf", "ACCENT_BG_HOVER": "#255da3", "ON_ACCENT": "#ffffff",  # the File tab, backstage
    "INPUT_BORDER": "#c8c8c8", "BUTTON": "#fdfdfd", "BUTTON_BORDER": "#c4c4c4", "BUTTON_DISABLED": "#f4f4f4",
    "BUTTON_HOVER_BORDER": "#9fbbe0",
    "MID": "#b8b8b8",
    "ERROR": "#dc2626", "ERROR_BG": "#fee2e2", "OK": "#15803d", "WARNING": "#b45309", "LINK": "#1f6fc5",
    "NOTE": "#7c3aed",  # blind testing notes (violet)
    "NOTE_BG": "#fff7e0", "NOTE_BORDER": "#f0d58a",  # remarks boxes
    "READY_BG": "#e8f4e8", "READY_FG": "#1e8e3e",  # the next test of each apparatus (ANY-maze green)
    "INACTIVE": "#a3a3a3", "FAINT": "#94a3b8", "SLATE": "#64748b",  # inactive rows, absent values, secondary text
    "PANEL_HEAD": "#f5f5f5", "PANEL_HEAD_SELECTED": "#e6eef9", "PANEL_BORDER": "#dcdcdc",
    "SLIDER_GROOVE": "#dcdcdc", "SLIDER_FILL": "#7a7a7a", "SLIDER_HANDLE_BORDER": "#8a8a8a",
}
DARK = {
    "RIBBON_BG": "#2b2b2b", "WORK_BG": "#1f1f1f", "BASE": "#262626", "ALT_BASE": "#2b2c2e", "BORDER": "#444444",
    "GRID": "#3a3a3a", "SHEET_GRID": "#3a3a3a", "ROW_LINE": "#313131", "HEADER_LINE": "#363636",
    "HEADER_BG": "#2c2c2c", "SPIN_LINE": "#3d3d3d",
    "HEADING": "#82aee8",
    "SELECTION": "#2f4f7a",
    "SELECTION_BORDER": "#4f73a6", "HOVER": "#2e3b4c", "HOVER_BORDER": "#3f5878",
    "TEXT": "#e8e8e8", "MUTED": "#a3aab5", "HINT": "#b4bcc8", "DISABLED": "#7a7a7a", "PLACEHOLDER": "#8a919b",
    "ACCENT": "#7fb0f0",
    "ACCENT_BG": "#2f6fbf", "ACCENT_BG_HOVER": "#3b7dd0", "ON_ACCENT": "#ffffff",
    "INPUT_BORDER": "#555555", "BUTTON": "#323232", "BUTTON_BORDER": "#565656", "BUTTON_DISABLED": "#2a2a2a",
    "BUTTON_HOVER_BORDER": "#4f73a6",
    "MID": "#5c5c5c",
    "ERROR": "#f87171", "ERROR_BG": "#4a1f1f", "OK": "#4ade80", "WARNING": "#fbbf24", "LINK": "#7fb0f0",
    "NOTE": "#c4b5fd",
    "NOTE_BG": "#3a3220", "NOTE_BORDER": "#6b5a2a",
    "READY_BG": "#1e3a26", "READY_FG": "#5fd38a",
    "INACTIVE": "#808080", "FAINT": "#8d97a6", "SLATE": "#a3aab5",
    "PANEL_HEAD": "#2d2d2d", "PANEL_HEAD_SELECTED": "#2c3b52", "PANEL_BORDER": "#444444",
    "SLIDER_GROOVE": "#4a4a4a", "SLIDER_FILL": "#9a9a9a", "SLIDER_HANDLE_BORDER": "#9a9a9a",
}
SCHEMES = {"light": LIGHT, "dark": DARK}
APPARATUS = "#ff8a1f"  # zone outlines (ANY-maze orange): the same in both schemes
RULER = "#3ddc97"  # calibration ruler (ANY-maze green)

# the scheme in use (module names, see the docstring)
_scheme = "light"
_appearance = "system"
_DARK_ARROWS: str | None = None
RIBBON_BG = WORK_BG = BASE = ALT_BASE = BORDER = GRID = SHEET_GRID = ROW_LINE = HEADER_LINE = HEADER_BG = ""
SPIN_LINE = BUTTON_HOVER_BORDER = ""
HEADING = SELECTION = SELECTION_BORDER = HOVER = HOVER_BORDER = ""
TEXT = MUTED = HINT = DISABLED = PLACEHOLDER = ACCENT = ACCENT_BG = ACCENT_BG_HOVER = ON_ACCENT = ""
INPUT_BORDER = BUTTON = BUTTON_BORDER = BUTTON_DISABLED = MID = ""
ERROR = ERROR_BG = OK = WARNING = LINK = NOTE = NOTE_BG = NOTE_BORDER = READY_BG = READY_FG = ""
INACTIVE = FAINT = SLATE = ""
PANEL_HEAD = PANEL_HEAD_SELECTED = PANEL_BORDER = SLIDER_GROOVE = SLIDER_FILL = SLIDER_HANDLE_BORDER = ""
QSS = ""


def scheme() -> str:
    """The scheme in use: "light" or "dark"."""
    return _scheme


def is_dark() -> bool:
    return _scheme == "dark"


def appearance() -> str:
    """The appearance chosen (View ▸ Appearance): "system", "light" or "dark"."""
    return _appearance


def colours(name: str | None = None) -> dict:
    """The tokens of a scheme (default: the one in use)."""
    return dict(SCHEMES[name or _scheme])


def _set_scheme(name: str):
    global _scheme, QSS
    _scheme = name if name in SCHEMES else "light"
    globals().update(SCHEMES[_scheme])
    QSS = qss()


# check box and radio button marks of the dark scheme (Fusion draws their frames darker than the window: invisible on
# a dark one), drawn by the style sheet
_DARK_MARKS = {
    "check_on.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 13 13"><rect x=".5" '
                    'y=".5" width="12" height="12" rx="2" fill="#2f6fbf" stroke="#7fb0f0"/><path d="M3 6.8 5.4 9.2 '
                    '10 4.2" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" '
                    'stroke-linejoin="round"/></svg>',
    "check_mixed.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 13 13"><rect '
                       'x=".5" y=".5" width="12" height="12" rx="2" fill="#262626" stroke="#7fb0f0"/><path d="M3.5 '
                       '6.5h6" stroke="#7fb0f0" stroke-width="1.8" stroke-linecap="round"/></svg>',
    "radio_on.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 13 13"><circle '
                    'cx="6.5" cy="6.5" r="6" fill="#262626" stroke="#7fb0f0"/><circle cx="6.5" cy="6.5" r="3" '
                    'fill="#7fb0f0"/></svg>',
}


def _arrows_dir() -> str:
    """The folder of the arrow (and check mark) images the style sheet uses: the icons' own, or for the dark scheme
    light-grey copies written once to a temporary folder (a style sheet image cannot be tinted)."""
    global _DARK_ARROWS
    if not is_dark():
        return ICONS
    if _DARK_ARROWS is None:
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="manymaze-arrows-"))
        for name in ("chevron_down", "chevron_up", "chevron_down_disabled", "chevron_up_disabled"):
            src = Path(ICONS) / f"{name}.svg"
            if src.exists():
                data = src.read_text(encoding="utf-8").replace('"#5a6472"', '"#b4bcc8"').replace('"#b0b0b0"',
                                                                                                   '"#666666"')
                (d / src.name).write_text(data, encoding="utf-8")
        for name, svg in _DARK_MARKS.items():
            (d / name).write_text(svg, encoding="utf-8")
        _DARK_ARROWS = d.as_posix()
    return _DARK_ARROWS


def qss() -> str:
    """The application style sheet of the scheme in use."""
    icons = _arrows_dir()
    return f"""
QMainWindow, QDialog {{ background: {WORK_BG}; }}
QWidget#Workspace, QStackedWidget#SectionStack > QWidget {{ background: {WORK_BG}; }}

/* ribbon */
QWidget#Ribbon {{ background: {RIBBON_BG}; border-bottom: 1px solid {BORDER}; }}
QTabBar#RibbonTabs {{ background: {RIBBON_BG}; }}
QTabBar#RibbonTabs::tab {{ background: transparent; border: none; padding: 6px 16px 5px 16px; margin: 0 1px;
    color: {TEXT}; font-size: 13px; }}
QTabBar#RibbonTabs::tab:selected {{ background: {WORK_BG}; border: 1px solid {BORDER}; border-bottom: none;
    color: {ACCENT}; }}
QTabBar#RibbonTabs::tab:first {{ background: {ACCENT_BG}; color: {ON_ACCENT}; margin-right: 6px; }}
QTabBar#RibbonTabs::tab:hover:!selected {{ background: {HOVER}; }}
QTabBar#RibbonTabs::tab:first:hover {{ background: {ACCENT_BG_HOVER}; }}
QWidget#RibbonPanel {{ background: {WORK_BG}; border-top: 1px solid {BORDER}; }}
QFrame#RibbonGroup {{ background: transparent; border: none; border-right: 1px solid {BORDER}; }}
QLabel#RibbonGroupTitle {{ color: {MUTED}; font-size: 11px; }}
QToolButton#RibbonLarge, QToolButton#RibbonSmall {{ background: transparent; border: 1px solid transparent;
    border-radius: 2px; color: {TEXT}; }}
QToolButton#RibbonLarge {{ padding: 2px 4px; font-size: 12px; }}
QToolButton#RibbonSmall {{ padding: 1px 4px; font-size: 12px; text-align: left; }}
QToolButton#RibbonLarge:hover, QToolButton#RibbonSmall:hover {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
QToolButton#RibbonLarge:pressed, QToolButton#RibbonSmall:pressed,
QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked {{ background: {SELECTION};
    border-color: {SELECTION_BORDER}; }}
QToolButton#RibbonLarge:disabled, QToolButton#RibbonSmall:disabled {{ color: {DISABLED}; }}

/* explorer (left list of each section) */
QTreeWidget#Explorer {{ background: {BASE}; border: none; border-right: 1px solid {BORDER}; font-size: 13px;
    outline: 0; }}
QTreeWidget#Explorer::item {{ height: 28px; padding-left: 2px; color: {TEXT}; }}
QTreeWidget#Explorer::item:hover {{ background: {HOVER}; }}
QTreeWidget#Explorer::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QLabel#ExplorerTitle {{ background: {BASE}; color: {HEADING}; font-size: 15px; padding: 10px 10px 6px 12px;
    border-right: 1px solid {BORDER}; }}

/* page titles (ANY-maze blue headings) */
QLabel#PageTitle {{ color: {HEADING}; font-size: 22px; font-weight: 300; padding: 2px 0 6px 0; }}
QLabel#SectionTitle {{ color: {HEADING}; font-size: 17px; font-weight: 300; padding: 8px 0 2px 0; }}
QLabel#Hint {{ color: {MUTED}; }}

/* group boxes as flat sections with blue captions */
QGroupBox {{ border: none; border-top: 1px solid {BORDER}; margin-top: 22px; padding-top: 8px; font-size: 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0 2px; color: {HEADING}; }}

/* tables: ANY-maze spreadsheets have light lines and roomy rows */
QTableView, QTableWidget, QTreeView, QListView, QListWidget {{ background: {BASE};
    alternate-background-color: {ALT_BASE}; gridline-color: {GRID}; selection-background-color: {SELECTION};
    selection-color: {TEXT}; border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {BASE}; color: {TEXT}; border: none; border-right: 1px solid {HEADER_LINE};
    border-bottom: 1px solid {BORDER}; padding: 5px 6px; font-weight: 500; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateTimeEdit, QTimeEdit {{
    background: {BASE}; border: 1px solid {INPUT_BORDER}; border-radius: 2px; padding: 3px 5px;
    selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QComboBox {{ padding-right: 20px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 18px; border: none; }}
QComboBox::down-arrow {{ image: url({icons}/chevron_down.svg); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({icons}/chevron_down_disabled.svg); }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 16px; border: none; border-left: 1px solid {SPIN_LINE}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 16px; border: none; border-left: 1px solid {SPIN_LINE}; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {HOVER}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({icons}/chevron_up.svg); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({icons}/chevron_down.svg); width: 8px; height: 8px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url({icons}/chevron_up_disabled.svg); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: url({icons}/chevron_down_disabled.svg); }}
QPushButton {{ background: {BUTTON}; color: {TEXT}; border: 1px solid {BUTTON_BORDER}; border-radius: 2px;
    padding: 4px 12px; }}
QPushButton:hover {{ background: {HOVER}; border-color: {BUTTON_HOVER_BORDER}; }}
QPushButton:pressed, QPushButton:checked {{ background: {SELECTION}; }}
QPushButton:disabled {{ color: {DISABLED}; background: {BUTTON_DISABLED}; }}
QPushButton:default {{ border-color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {WORK_BG}; top: -1px; }}
QTabBar::tab {{ background: transparent; border: 1px solid transparent; padding: 5px 12px; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {WORK_BG}; border-color: {BORDER}; border-bottom-color: {WORK_BG};
    color: {ACCENT}; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; }}
QStatusBar {{ background: {RIBBON_BG}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: {BASE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QScrollArea {{ border: none; background: {WORK_BG}; }}
QSplitter::handle {{ background: {BORDER}; }}
""" + (_dark_marks_qss(icons) if is_dark() else "")


def _dark_marks_qss(icons: str) -> str:
    """Check boxes and radio buttons drawn by the style sheet in the dark scheme."""
    box = "width: 13px; height: 13px; border: 1px solid #8a8a8a; background: #262626;"
    return f"""
QCheckBox::indicator, QAbstractItemView::indicator, QGroupBox::indicator {{ {box} border-radius: 2px; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked, QGroupBox::indicator:checked {{
    border: none; image: url({icons}/check_on.svg); }}
QCheckBox::indicator:indeterminate, QAbstractItemView::indicator:indeterminate {{
    border: none; image: url({icons}/check_mixed.svg); }}
QRadioButton::indicator {{ {box} border-radius: 7px; }}
QRadioButton::indicator:checked {{ border: none; image: url({icons}/radio_on.svg); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled, QAbstractItemView::indicator:disabled {{
    border-color: #555555; background: #2a2a2a; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: #b4bcc8; }}
"""


def palette() -> QPalette:
    """The Qt palette of the scheme in use (also for the widgets the style sheet does not reach)."""
    pal = QPalette()
    for role, col in ((QPalette.Window, WORK_BG), (QPalette.Base, BASE), (QPalette.AlternateBase, ALT_BASE),
                      (QPalette.Button, BUTTON), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, SELECTION),
                      (QPalette.HighlightedText, TEXT), (QPalette.ToolTipBase, BASE),
                      (QPalette.ToolTipText, TEXT), (QPalette.Link, ACCENT), (QPalette.PlaceholderText, PLACEHOLDER),
                      (QPalette.Mid, MID), (QPalette.BrightText, ON_ACCENT)):
        pal.setColor(role, QColor(col))
    if is_dark():  # the bevels Fusion draws (frames, sliders, scroll bars)
        for role, col in ((QPalette.Light, "#4a4a4a"), (QPalette.Midlight, "#3a3a3a"), (QPalette.Dark, "#151515"),
                          (QPalette.Shadow, "#0a0a0a")):
            pal.setColor(role, QColor(col))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(DISABLED))
    return pal


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two colours (1 to 21; text needs 4.5, large text and graphics 3)."""
    def lum(c: str) -> float:
        q = QColor(c)
        out = []
        for v in (q.redF(), q.greenF(), q.blueF()):
            out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# ------------------------------------------------------------------ appearance
def system_scheme(app=None) -> str:
    """The scheme macOS uses (Qt >= 6.5; light when Qt cannot tell)."""
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    try:
        return "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    except AttributeError:
        return "light"


def saved_appearance(settings) -> str:
    """The appearance kept in the app settings (QSettings), "system" by default."""
    v = str(settings.value(SETTINGS_KEY, "system") or "system") if settings is not None else "system"
    return v if v in dict(APPEARANCES) else "system"


def apply(app, appearance: str | None = None, settings=None):
    """Apply the theme to the QApplication (Fusion style for identical rendering on every platform) in the given
    appearance — default: the one saved in ``settings``, else System — and restyle the widgets already shown. With
    ``settings`` the choice is saved."""
    global _appearance
    if appearance is None:
        appearance = saved_appearance(settings)
    _appearance = appearance if appearance in dict(APPEARANCES) else "system"
    if settings is not None:
        settings.setValue(SETTINGS_KEY, _appearance)
    hints = app.styleHints()
    try:  # the native parts (menu bar, title bar, dialogs) follow the same scheme (Qt >= 6.8)
        if _appearance == "system":
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(Qt.ColorScheme.Dark if _appearance == "dark" else Qt.ColorScheme.Light)
    except AttributeError:
        pass
    _set_scheme(system_scheme(app) if _appearance == "system" else _appearance)
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(palette())
    f = QFont(app.font())
    if f.pointSizeF() < 10.0:
        f.setPointSizeF(10.0)
        app.setFont(f)
    app.setStyleSheet(QSS)
    restyle(app)
    if not getattr(app, "_manymaze_scheme_watch", False):  # System: follow macOS switching light / dark
        try:
            hints.colorSchemeChanged.connect(lambda *_: _appearance == "system" and _follow_system(app))
            app._manymaze_scheme_watch = True
        except AttributeError:
            pass


def _follow_system(app):
    if system_scheme(app) != _scheme:
        apply(app, "system")


def style(widget, sheet: Callable[[], str]):
    """Give ``widget`` the style sheet ``sheet()`` (written with the theme's names, e.g.
    ``lambda: f"color:{theme.MUTED}"``) and again whenever the scheme changes."""
    widget._theme_sheet = sheet
    widget.setStyleSheet(sheet())


def restyle(app=None):
    """After a scheme change: re-apply the registered style sheets (:func:`style`), call ``theme_changed()`` on the
    widgets that have it, and repaint."""
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        sheet = getattr(w, "_theme_sheet", None)
        if sheet is not None:
            try:
                w.setStyleSheet(sheet())
            except RuntimeError:  # deleted meanwhile
                continue
        hook = getattr(w, "theme_changed", None)
        if callable(hook):
            hook()
    for w in app.topLevelWidgets():
        w.update()


_set_scheme("light")
