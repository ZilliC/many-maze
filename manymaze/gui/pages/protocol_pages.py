"""Building blocks of the Protocol section's element pages (ANY-maze property pages): a scrollable page with a
big blue title, flat section headings and sentence-style rows, and the "Key" property panel of the Keys element."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ...core import workflow as wf
from ..icons import icon
from .base import property_form, section_title

# how a key works, as in ANY-maze ("Specify how you'd like this key to work"); internally a behaviour's kind
# ("hold" / "state" / "point") plus an exclusive set (radio keys share a set)
KEY_MODES = [("simple", "Simple - key activity is occurring while key is pressed"),
             ("toggle", "Toggle - key activity starts on first press and ends on second press"),
             ("radio", "Radio - like toggle but also ends if any other radio key is pressed"),
             ("event", "Event - an instantaneous event, scored when the key is pressed")]
KEY_MODE_SHORT = {"simple": "Simple", "toggle": "Toggle", "radio": "Radio", "event": "Event"}
RADIO_SET = "radio"  # exclusive set given to a key made a radio key


def key_mode(kind: str, group: str) -> str:
    if kind == "hold":
        return "simple"
    if kind == "point":
        return "event"
    return "radio" if group else "toggle"


def mode_to_kind(mode: str, group: str) -> tuple[str, str]:
    """(kind, exclusive set) of a key working in `mode`."""
    if mode == "simple":
        return "hold", group
    if mode == "event":
        return "point", group
    if mode == "radio":
        return "state", group or RADIO_SET
    return "state", ""


def separator() -> QFrame:
    """Thin line between blocks of a property page."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet("background:#dcdcdc;border:none;margin:0;")
    return line


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def small_button(text: str, icon_name: str | None = None, tip: str = "", slot=None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name))
    if tip:
        b.setToolTip(tip)
    if slot is not None:
        b.clicked.connect(lambda _=False: slot())
    return b


def button_row(*buttons) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    for b in buttons:
        row.addWidget(b)
    row.addStretch()
    return row


class ElementPage(QScrollArea):
    """One protocol element: a page title, an optional hint and the content in ``self.body``.

    ``max_width`` keeps forms at a readable width (inputs on the right are still wide); ``scroll=False`` gives
    the content the whole height (e.g. the procedure editor)."""

    def __init__(self, title: str, hint_text: str = "", max_width: int | None = None, scroll: bool = True):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if not scroll:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("ElementBody")
        if max_width:
            inner.setMaximumWidth(max_width)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 14, 28, 18)
        self.body.setSpacing(6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("PageTitle")
        self.body.addWidget(self.title_lbl)
        self.hint_lbl = hint(hint_text)
        self.hint_lbl.setVisible(bool(hint_text))
        self.body.addWidget(self.hint_lbl)
        self.body.addSpacing(4)
        self.setWidget(inner)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def add(self, w, stretch: int = 0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w

    def section(self, title: str) -> QLabel:
        return self.add(section_title(title))

    def finish(self):
        self.body.addStretch()


class ColorButton(QPushButton):
    """A swatch that opens a colour dialog; property ``color`` holds the colour name."""

    color_changed = Signal(str)

    def __init__(self, color: str = "#22c55e", title: str = "Colour", parent=None):
        super().__init__(parent)
        self._title = title
        self.setFixedSize(64, 26)
        self.clicked.connect(self.pick)
        self.set_color(color)

    def set_color(self, color: str):
        self.setProperty("color", color)
        self.setStyleSheet(f"QPushButton{{background:{color};border:1px solid #9aa3ad;border-radius:3px;}}")

    def color(self) -> str:
        return self.property("color") or ""

    def pick(self):
        c = QColorDialog.getColor(QColor(self.color()), self, self._title)
        if c.isValid():
            self.set_color(c.name())
            self.color_changed.emit(c.name())


class KeyEditor(QWidget):
    """The "Key" property page of ANY-maze: key name, key stroke, how the key works, radio set and colour.

    ``load(name, key, kind, group, color)`` shows a key; ``edited`` is emitted with a dict of the new values
    (name, key, kind, group, color) when the user changes one."""

    edited = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QLabel("Key")
        head.setObjectName("PageTitle")
        lay.addWidget(head)

        f = property_form()
        self.name = QLineEdit()
        self.name.setMinimumWidth(220)
        self.name.editingFinished.connect(self._emit)
        f.addRow(self._label("Key name"), self.name)
        lay.addLayout(f)
        lay.addWidget(separator())

        f = property_form()
        self.stroke = QComboBox()
        self.stroke.setMinimumWidth(220)
        for c in wf.SCORING_KEYS:
            self.stroke.addItem(icon("key"), c.upper() if c.isalpha() else c, c)
        self.stroke.currentIndexChanged.connect(self._emit)
        f.addRow(self._label("Key stroke"), self.stroke)
        lay.addLayout(f)
        lay.addWidget(separator())

        lay.addWidget(QLabel("Specify how you'd like this key to work"))
        self.mode = QComboBox()
        for v, text in KEY_MODES:
            self.mode.addItem(icon("key"), text, v)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        lay.addWidget(self.mode)

        f = property_form()
        self.group = QLineEdit()
        self.group.setMinimumWidth(220)
        self.group.setPlaceholderText(RADIO_SET)
        self.group.setToolTip("Keys in the same set cannot be active together: pressing one ends the others. "
                              "Radio keys share a set; give different sets to independent groups of radio keys.")
        self.group.editingFinished.connect(self._emit)
        self.group_lbl = QLabel("Radio set (keys in a set end each other)")
        f.addRow(self.group_lbl, self.group)
        self.color = ColorButton(title="Key colour")
        self.color.setToolTip("Colour of the on-screen scoring button and of the key's bouts in plots")
        self.color.color_changed.connect(lambda _c: self._emit())
        f.addRow("Colour of the scoring button", self.color)
        lay.addLayout(f)
        lay.addStretch()
        self.setEnabled(False)

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setMinimumWidth(110)
        return lbl

    def load(self, name: str | None, key: str = "", kind: str = "state", group: str = "", color: str = ""):
        """Show a key (name None = no key selected)."""
        self._loading = True
        self.setEnabled(name is not None)
        self.name.setText(name or "")
        i = self.stroke.findData((key or "").lower())
        if i < 0 and key:
            self.stroke.addItem(icon("key"), key, key)
            i = self.stroke.count() - 1
        self.stroke.setCurrentIndex(max(i, 0) if key else -1)
        self.mode.setCurrentIndex(max(0, self.mode.findData(key_mode(kind, group))))
        self.group.setText(group)
        if color:
            self.color.set_color(color)
        self._update_group_row()
        self._loading = False

    def _update_group_row(self):
        on = self.mode.currentData() == "radio" or bool(self.group.text().strip())
        self.group.setVisible(on)
        self.group_lbl.setVisible(on)

    def _mode_changed(self, *_):
        if self._loading:
            return
        kind, group = mode_to_kind(self.mode.currentData(), self.group.text().strip())
        self._loading = True
        self.group.setText(group)
        self._loading = False
        self._update_group_row()
        self._emit()

    def values(self) -> dict:
        kind, group = mode_to_kind(self.mode.currentData(), self.group.text().strip())
        if self.mode.currentData() in ("simple", "event"):
            group = self.group.text().strip()
        return {"name": self.name.text().strip(), "key": self.stroke.currentData() or "", "kind": kind,
                "group": group, "color": self.color.color()}

    def _emit(self, *_):
        if self._loading or not self.isEnabled():
            return
        self._update_group_row()
        self.edited.emit(self.values())


def fixed_width(w: QWidget, width: int) -> QWidget:
    w.setMaximumWidth(width)
    w.setSizePolicy(QSizePolicy.Expanding, w.sizePolicy().verticalPolicy())
    return w
