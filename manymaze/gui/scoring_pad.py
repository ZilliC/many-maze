"""On-screen scoring buttons (mouse / touch screen), shared by the test viewer and the Run tests page."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLayout, QPushButton, QWidget

BEHAVIOUR_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16",
                    "#f97316", "#06b6d4"]
KIND_TEXT = {"state": "toggle", "hold": "hold", "point": "point"}


def behaviour_color(b, index: int) -> str:
    return b.color or BEHAVIOUR_COLORS[index % len(BEHAVIOUR_COLORS)]


class FlowLayout(QLayout):
    """Lays out widgets left to right, wrapping to new lines as needed."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do_layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h, sp = r.x(), r.y(), 0, self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + sp, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ScoringPad(QWidget):
    """On-screen scoring buttons (mouse / touch screen), one per behaviour.

    Emits pressed(name) / released(name); hold behaviours are scored between the two."""

    pressed = Signal(str)
    released = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, 6)
        self.buttons: dict[str, QPushButton] = {}
        self._colors: dict[str, str] = {}
        self._active: set[str] = set()

    def set_behaviours(self, behaviours):
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()  # until deleted (deferred), it would still be painted
                it.widget().deleteLater()
        self.buttons.clear()
        self._colors.clear()
        for i, b in enumerate(behaviours):
            key = b.key.upper() if b.key else "–"
            btn = QPushButton(f"{b.name}\n[{key}]  {KIND_TEXT.get(b.kind, b.kind)}")
            btn.setFocusPolicy(Qt.NoFocus)  # the keyboard stays with the video
            btn.setMinimumSize(124, 58)
            btn.setToolTip({"hold": "Press and hold while the behaviour lasts",
                            "state": "Press to start, press again to stop",
                            "point": "Press when the event occurs"}.get(b.kind, "")
                           + (f" · exclusive set “{b.group}”" if b.group else ""))
            btn.pressed.connect(lambda n=b.name: self.pressed.emit(n))
            btn.released.connect(lambda n=b.name: self.released.emit(n))
            self.flow.addWidget(btn)
            btn.show()
            self.buttons[b.name] = btn
            self._colors[b.name] = behaviour_color(b, i)
        self.set_active(self._active)
        self.updateGeometry()

    def set_active(self, names):
        self._active = set(names)
        for name, btn in self.buttons.items():
            c = QColor(self._colors[name])
            if name in self._active:
                css = (f"background:{c.name()};color:white;border:2px solid {c.darker(130).name()};"
                       "border-radius:8px;font-weight:bold;padding:4px 10px;")
            else:
                css = (f"background:rgba({c.red()},{c.green()},{c.blue()},38);color:#0f172a;"
                       f"border:2px solid {c.name()};border-radius:8px;padding:4px 10px;")
            btn.setStyleSheet(f"QPushButton{{{css}}}QPushButton:pressed{{background:{c.name()};color:white;}}")
