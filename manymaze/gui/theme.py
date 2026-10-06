"""Visual theme in the style of ANY-maze: light Office-like ribbon over a near-white work area, blue headings,
light-blue selection, orange apparatus outlines."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette

ICONS = (Path(__file__).resolve().parent.parent / "resources" / "icons").as_posix()

RIBBON_BG = "#f3f3f3"
WORK_BG = "#f9f9f9"
BORDER = "#d6d6d6"
HEADING = "#3f6fb5"  # page / section titles
SELECTION = "#a9c3e8"  # selected rows, pressed ribbon buttons
HOVER = "#dce8f6"
TEXT = "#1f1f1f"
MUTED = "#6b7280"
ACCENT = "#2f6fbf"  # File tab, links
APPARATUS = "#ff8a1f"  # zone outlines (ANY-maze orange)
RULER = "#3ddc97"  # calibration ruler (ANY-maze green)

QSS = f"""
QMainWindow, QDialog {{ background: {WORK_BG}; }}
QWidget#Workspace, QStackedWidget#SectionStack > QWidget {{ background: {WORK_BG}; }}

/* ribbon */
QWidget#Ribbon {{ background: {RIBBON_BG}; border-bottom: 1px solid {BORDER}; }}
QTabBar#RibbonTabs {{ background: {RIBBON_BG}; }}
QTabBar#RibbonTabs::tab {{ background: transparent; border: none; padding: 6px 16px 5px 16px; margin: 0 1px;
    color: {TEXT}; font-size: 13px; }}
QTabBar#RibbonTabs::tab:selected {{ background: {WORK_BG}; border: 1px solid {BORDER}; border-bottom: none;
    color: {ACCENT}; }}
QTabBar#RibbonTabs::tab:first {{ background: {ACCENT}; color: white; margin-right: 6px; }}
QTabBar#RibbonTabs::tab:hover:!selected {{ background: {HOVER}; }}
QTabBar#RibbonTabs::tab:first:hover {{ background: #255da3; }}
QWidget#RibbonPanel {{ background: {WORK_BG}; border-top: 1px solid {BORDER}; }}
QFrame#RibbonGroup {{ background: transparent; border: none; border-right: 1px solid {BORDER}; }}
QLabel#RibbonGroupTitle {{ color: {MUTED}; font-size: 11px; }}
QToolButton#RibbonLarge, QToolButton#RibbonSmall {{ background: transparent; border: 1px solid transparent;
    border-radius: 2px; color: {TEXT}; }}
QToolButton#RibbonLarge {{ padding: 2px 4px; font-size: 12px; }}
QToolButton#RibbonSmall {{ padding: 1px 4px; font-size: 12px; text-align: left; }}
QToolButton#RibbonLarge:hover, QToolButton#RibbonSmall:hover {{ background: {HOVER}; border-color: #c5d7ef; }}
QToolButton#RibbonLarge:pressed, QToolButton#RibbonSmall:pressed,
QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked {{ background: {SELECTION}; border-color: #8fb0de; }}
QToolButton#RibbonLarge:disabled, QToolButton#RibbonSmall:disabled {{ color: #a6a6a6; }}

/* explorer (left list of each section) */
QTreeWidget#Explorer {{ background: white; border: none; border-right: 1px solid {BORDER}; font-size: 13px;
    outline: 0; }}
QTreeWidget#Explorer::item {{ height: 28px; padding-left: 2px; color: {TEXT}; }}
QTreeWidget#Explorer::item:hover {{ background: {HOVER}; }}
QTreeWidget#Explorer::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QLabel#ExplorerTitle {{ background: white; color: {HEADING}; font-size: 15px; padding: 10px 10px 6px 12px;
    border-right: 1px solid {BORDER}; }}

/* page titles (ANY-maze blue headings) */
QLabel#PageTitle {{ color: {HEADING}; font-size: 22px; font-weight: 300; padding: 2px 0 6px 0; }}
QLabel#SectionTitle {{ color: {HEADING}; font-size: 17px; font-weight: 300; padding: 8px 0 2px 0; }}
QLabel#Hint {{ color: {MUTED}; }}

/* group boxes as flat sections with blue captions */
QGroupBox {{ border: none; border-top: 1px solid {BORDER}; margin-top: 22px; padding-top: 8px; font-size: 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; padding: 0 2px; color: {HEADING}; }}

/* tables: ANY-maze spreadsheets have light lines and roomy rows */
QTableView, QTableWidget, QTreeView, QListView, QListWidget {{ background: white; alternate-background-color: #fafbfd;
    gridline-color: #e3e3e3; selection-background-color: {SELECTION}; selection-color: {TEXT};
    border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: white; color: {TEXT}; border: none; border-right: 1px solid #ececec;
    border-bottom: 1px solid {BORDER}; padding: 5px 6px; font-weight: 500; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateTimeEdit, QTimeEdit {{
    background: white; border: 1px solid #c8c8c8; border-radius: 2px; padding: 3px 5px;
    selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ selection-background-color: {SELECTION}; selection-color: {TEXT}; }}
QComboBox {{ padding-right: 20px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 18px; border: none; }}
QComboBox::down-arrow {{ image: url({ICONS}/chevron_down.svg); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url({ICONS}/chevron_down_disabled.svg); }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 16px; border: none; border-left: 1px solid #e2e2e2; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 16px; border: none; border-left: 1px solid #e2e2e2; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {HOVER}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ICONS}/chevron_up.svg); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ICONS}/chevron_down.svg); width: 8px; height: 8px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url({ICONS}/chevron_up_disabled.svg); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: url({ICONS}/chevron_down_disabled.svg); }}
QPushButton {{ background: #fdfdfd; border: 1px solid #c4c4c4; border-radius: 2px; padding: 4px 12px; }}
QPushButton:hover {{ background: {HOVER}; border-color: #9fbbe0; }}
QPushButton:pressed, QPushButton:checked {{ background: {SELECTION}; }}
QPushButton:disabled {{ color: #a6a6a6; background: #f4f4f4; }}
QPushButton:default {{ border-color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {WORK_BG}; top: -1px; }}
QTabBar::tab {{ background: transparent; border: 1px solid transparent; padding: 5px 12px; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {WORK_BG}; border-color: {BORDER}; border-bottom-color: {WORK_BG};
    color: {ACCENT}; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; }}
QStatusBar {{ background: {RIBBON_BG}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: white; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QScrollArea {{ border: none; background: {WORK_BG}; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def apply(app):
    """Apply the theme to the QApplication (Fusion style for identical rendering on every platform)."""
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, WORK_BG), (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#fafbfd"),
                      (QPalette.Button, "#fdfdfd"), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, SELECTION),
                      (QPalette.HighlightedText, TEXT), (QPalette.ToolTipBase, "#ffffff"),
                      (QPalette.ToolTipText, TEXT), (QPalette.Link, ACCENT), (QPalette.PlaceholderText, "#9aa1ab"),
                      (QPalette.Mid, "#b8b8b8")):
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#a6a6a6"))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#a6a6a6"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#a6a6a6"))
    app.setPalette(pal)
    f = QFont(app.font())
    f.setPointSizeF(max(f.pointSizeF(), 10.0))
    app.setFont(f)
    app.setStyleSheet(QSS)
