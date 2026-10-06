"""Office-style ribbon (as in ANY-maze): tabs, each a row of captioned groups of large and small buttons.

Pages contribute contextual groups by implementing ``ribbon_groups()``::

    def ribbon_groups(self):
        return [("Spreadsheet", [(self.save_act, "large"), (self.copy_act, "small")]),
                ("Actions", [(self.select_act, "large"), some_widget])]
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QTabBar,
                               QToolButton, QVBoxLayout, QWidget)

LARGE_ICON = QSize(32, 32)
SMALL_ICON = QSize(16, 16)
PANEL_HEIGHT = 92


class RibbonGroup(QFrame):
    """A captioned group: large buttons (icon above text) and columns of up to three small buttons."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonGroup")
        self.title = title
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 6, 1)
        outer.setSpacing(0)
        self.row = QHBoxLayout()
        self.row.setSpacing(2)
        self.row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.row, 1)
        cap = QLabel(title)
        cap.setObjectName("RibbonGroupTitle")
        cap.setAlignment(Qt.AlignCenter)
        outer.addWidget(cap)
        self._small_col: QGridLayout | None = None
        self._small_n = 0
        self.buttons: list[QToolButton] = []

    def _button(self, action: QAction, large: bool) -> QToolButton:
        b = QToolButton()
        b.setObjectName("RibbonLarge" if large else "RibbonSmall")
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.NoFocus)
        if action.menu() is not None:
            b.setPopupMode(QToolButton.InstantPopup)
        if large:
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(LARGE_ICON)
            b.setMinimumWidth(52)
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            text = action.text().replace("&", "")
            if " " in text and len(text) > 9 and "\n" not in text:  # two lines, like the ribbon of ANY-maze
                words = text.split(" ")
                best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
                b.setText(" ".join(words[:best]) + "\n" + " ".join(words[best:]))
        else:
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setIconSize(SMALL_ICON)
        self.buttons.append(b)
        return b

    def add_large(self, action: QAction) -> QToolButton:
        self._small_col = None
        b = self._button(action, True)
        self.row.addWidget(b)
        return b

    def add_small(self, action: QAction) -> QToolButton:
        if self._small_col is None or self._small_n >= 3:
            w = QWidget()
            self._small_col = QGridLayout(w)
            self._small_col.setContentsMargins(0, 0, 0, 0)
            self._small_col.setVerticalSpacing(0)
            self._small_n = 0
            self.row.addWidget(w)
        b = self._button(action, False)
        self._small_col.addWidget(b, self._small_n, 0, Qt.AlignLeft)
        self._small_n += 1
        return b

    def add_widget(self, w: QWidget):
        self._small_col = None
        self.row.addWidget(w)


class RibbonPanel(QWidget):
    """The row of groups shown under a ribbon tab: fixed groups followed by the active page's groups."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonPanel")
        self.setFixedHeight(PANEL_HEIGHT)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(4, 2, 4, 2)
        self.lay.setSpacing(0)
        self.lay.addStretch()
        self.fixed: list[RibbonGroup] = []
        self.context: list[RibbonGroup] = []

    def add_group(self, title: str, fixed: bool = True) -> RibbonGroup:
        g = RibbonGroup(title)
        self.lay.insertWidget(self.lay.count() - 1, g)
        (self.fixed if fixed else self.context).append(g)
        return g

    def set_context(self, groups) -> list[RibbonGroup]:
        """Replace the contextual groups: [(title, [QAction | (QAction, "large"|"small") | QWidget, ...]), ...]."""
        for g in self.context:  # the page owns the actions; only the buttons go
            g.setParent(None)
            g.deleteLater()
        self.context = []
        for title, items in groups or []:
            g = self.add_group(title, fixed=False)
            for it in items:
                if isinstance(it, QWidget):
                    g.add_widget(it)
                    continue
                act, size = it if isinstance(it, tuple) else (it, "large")
                if act is None:
                    continue
                (g.add_large if size == "large" else g.add_small)(act)
        return self.context

    def actions_text(self) -> list[str]:
        return [b.defaultAction().text() for g in self.fixed + self.context for b in g.buttons]


class Ribbon(QWidget):
    """Tab row ("File", "Protocol", …) above a stack of panels. Tab 0 is the File (backstage) tab."""

    tab_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Ribbon")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.tabs = QTabBar()
        self.tabs.setObjectName("RibbonTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        self.panels = QStackedWidget()
        self.panels.setFixedHeight(PANEL_HEIGHT)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 6, 0)
        top.addWidget(self.tabs)
        top.addStretch()
        self.corner = QHBoxLayout()
        top.addLayout(self.corner)
        lay.addLayout(top)
        lay.addWidget(self.panels)
        self.tabs.currentChanged.connect(self._changed)

    def add_tab(self, title: str) -> RibbonPanel:
        p = RibbonPanel()
        self.tabs.addTab(title)
        self.panels.addWidget(p)
        return p

    def panel(self, i: int) -> RibbonPanel:
        return self.panels.widget(i)

    def _changed(self, i: int):
        self.panels.setCurrentIndex(i)
        self.panels.setVisible(i != 0)  # the File tab shows a full-page backstage instead of a panel
        self.tab_changed.emit(i)
