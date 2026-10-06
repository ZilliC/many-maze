"""Touch-screen stimulus window (ANY-maze Touch equivalent) for operant touch-screen chambers.

A full-screen window, normally on a second display, divided into response areas (e.g. the 2, 3 or 5 windows of
a Bussey–Saksida chamber mask). Procedures show and hide stimuli in the areas ("Show stimulus" / "Hide
stimulus" / "Clear screen" actions) and react to touches ("Touch in area" / "Touch outside all areas" events).

Configuration lives in ``Project.settings_extra["touchscreen"]``::

    {"screen": 1, "background": "#000000", "outline": false,
     "areas": [{"name": "left", "x": 0.1, "y": 0.35, "w": 0.2, "h": 0.3}, ...]}   # fractions of the screen

Wiring during a live test::

    win = TouchStimulusWindow.from_project(project)
    engine = ProcedureEngine(..., on_stimulus=win.handle)
    win.connect_engine(engine, clock=lambda: session.elapsed)
    win.show_on_screen()
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QPushButton,
                               QSpinBox, QWidget)

SHAPES = ("circle", "square", "triangle", "star", "cross", "bars")


def default_areas(n: int = 3, y: float = 0.35, h: float = 0.3) -> list[dict]:
    """n equal response windows in a row (with gaps), like a touch-screen chamber mask."""
    n = max(1, int(n))
    gap = 0.4 / (n + 1)
    w = 0.6 / n
    names = {1: ["centre"], 2: ["left", "right"], 3: ["left", "centre", "right"]}.get(n) or \
        [f"window {i + 1}" for i in range(n)]
    return [{"name": names[i], "x": gap + i * (w + gap), "y": y, "w": w, "h": h} for i in range(n)]


class TouchStimulusWindow(QWidget):
    touched = Signal(str, float, float)  # area name ("" = outside every area), x, y as screen fractions
    _command = Signal(str, object)

    def __init__(self, areas: list[dict] | None = None, background: str = "#000000", outline: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("mANY-MAZE touch screen")
        self.areas = [dict(a) for a in (areas if areas is not None else default_areas(3))]
        self.background = QColor(background)
        self.outline = outline
        self.stimuli: dict[str, dict] = {}
        self.touches: list[tuple[str, float, float]] = []
        self._pixmaps: dict[str, QPixmap] = {}
        self._engine = None
        self._clock: Callable[[], float] | None = None
        self._command.connect(self._run_command)  # queued when the engine runs in another thread
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setCursor(Qt.BlankCursor)
        self.resize(800, 600)

    @classmethod
    def from_project(cls, project) -> "TouchStimulusWindow":
        cfg = (getattr(project, "settings_extra", {}) or {}).get("touchscreen", {}) or {}
        w = cls(cfg.get("areas") or None, cfg.get("background", "#000000"), cfg.get("outline", False))
        w.screen_index = cfg.get("screen")
        return w

    # ------------------------------------------------------------------ control
    def show_on_screen(self, index: int | None = None):
        """Full screen on display `index` (default: the last one, i.e. the second display if present)."""
        screens = QGuiApplication.screens()
        if index is None:
            index = getattr(self, "screen_index", None)
        if index is None or not 0 <= index < len(screens):
            index = len(screens) - 1
        if screens:
            self.setGeometry(screens[index].geometry())
        self.showFullScreen()

    def connect_engine(self, engine, clock: Callable[[], float]):
        """Forward touches to a ProcedureEngine (clock() returns the current test time)."""
        self._engine, self._clock = engine, clock

    def handle(self, cmd: str, params: dict):
        """ProcedureEngine on_stimulus callback (safe to call from any thread)."""
        self._command.emit(cmd, dict(params))

    def _run_command(self, cmd: str, params: dict):
        if cmd == "show":
            self.show_stimulus(params.get("area", ""), params.get("image", ""), params.get("shape", "circle"),
                               params.get("color", "#ffffff"))
        elif cmd == "hide":
            self.hide_stimulus(params.get("area", ""))
        elif cmd == "clear":
            self.clear()

    def show_stimulus(self, area: str, image: str = "", shape: str = "circle", color: str = "#ffffff"):
        self.stimuli[area] = {"image": image, "shape": shape, "color": color}
        self.update()

    def hide_stimulus(self, area: str):
        self.stimuli.pop(area, None)
        self.update()

    def clear(self):
        self.stimuli.clear()
        self.update()

    # ------------------------------------------------------------------ geometry
    def area_rect(self, a: dict) -> QRectF:
        W, H = self.width(), self.height()
        return QRectF(a["x"] * W, a["y"] * H, a["w"] * W, a["h"] * H)

    def area_at(self, x: float, y: float) -> str:
        for a in self.areas:
            if self.area_rect(a).contains(QPointF(x, y)):
                return a["name"]
        return ""

    def _touch(self, x: float, y: float):
        area = self.area_at(x, y)
        fx, fy = x / max(1, self.width()), y / max(1, self.height())
        self.touches.append((area, fx, fy))
        self.touched.emit(area, fx, fy)
        if self._engine is not None:
            t = self._clock() if self._clock else self._engine.t
            self._engine.touch(t, area or None, fx, fy)

    def mousePressEvent(self, e):
        p = e.position()
        self._touch(p.x(), p.y())

    def event(self, e):
        if e.type() == e.Type.TouchBegin:
            for pt in e.points():
                p = pt.position()
                self._touch(p.x(), p.y())
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), self.background)
        for a in self.areas:
            r = self.area_rect(a)
            if self.outline:
                qp.setPen(QPen(QColor(90, 90, 90), 2))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(r)
            st = self.stimuli.get(a["name"])
            if st:
                self._draw_stimulus(qp, r, st)
        qp.end()

    def _draw_stimulus(self, qp: QPainter, r: QRectF, st: dict):
        if st.get("image"):
            pm = self._pixmaps.get(st["image"])
            if pm is None:
                pm = self._pixmaps[st["image"]] = QPixmap(st["image"])
            if not pm.isNull():
                s = pm.scaled(r.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                qp.drawPixmap(int(r.center().x() - s.width() / 2), int(r.center().y() - s.height() / 2), s)
                return
        col = QColor(st.get("color") or "#ffffff")
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(col))
        side = min(r.width(), r.height()) * 0.8
        c = r.center()
        box = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
        shape = st.get("shape", "circle")
        if shape == "square":
            qp.drawRect(box)
        elif shape == "triangle":
            qp.drawPolygon(QPolygonF([QPointF(c.x(), box.top()), box.bottomRight(), box.bottomLeft()]))
        elif shape == "star":
            import math

            pts = []
            for k in range(10):
                rad = side / 2 if k % 2 == 0 else side / 5
                ang = -math.pi / 2 + k * math.pi / 5
                pts.append(QPointF(c.x() + rad * math.cos(ang), c.y() + rad * math.sin(ang)))
            qp.drawPolygon(QPolygonF(pts))
        elif shape == "cross":
            t = side / 4
            path = QPainterPath()
            path.addRect(QRectF(c.x() - t / 2, box.top(), t, side))
            path.addRect(QRectF(box.left(), c.y() - t / 2, side, t))
            qp.drawPath(path.simplified())
        elif shape == "bars":
            n = 5
            bw = side / (2 * n - 1)
            for k in range(n):
                qp.drawRect(QRectF(box.left() + 2 * k * bw, box.top(), bw, side))
        else:
            qp.drawEllipse(box)


class TouchScreenDialog(QDialog):
    """Configure the touch screen (``project.settings_extra["touchscreen"]``)."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Touch screen")
        self.project = project
        cfg = dict((project.settings_extra or {}).get("touchscreen") or {})
        f = QFormLayout(self)
        self.enabled = QCheckBox("Use a touch screen in live tests (one-test mode)")
        self.enabled.setChecked(bool(cfg.get("enabled")))
        self.screen = QComboBox()
        for i, sc in enumerate(QGuiApplication.screens()):
            g = sc.geometry()
            self.screen.addItem(f"Display {i + 1}: {sc.name()} ({g.width()}×{g.height()})", i)
        self.screen.setCurrentIndex(max(0, self.screen.findData(cfg.get("screen", self.screen.count() - 1))))
        self.windows = QSpinBox()
        self.windows.setRange(1, 9)
        self.windows.setValue(len(cfg.get("areas") or default_areas(3)))
        self.windows.setToolTip("Response windows in a row (names: centre / left, right / left, centre, right…)")
        self.outline = QCheckBox("Outline the response windows")
        self.outline.setChecked(bool(cfg.get("outline", False)))
        self.preview = QPushButton("Show on screen for 3 s")
        self.preview.clicked.connect(self._preview)
        f.addRow(self.enabled)
        f.addRow("Display", self.screen)
        f.addRow("Response windows", self.windows)
        f.addRow(self.outline)
        f.addRow(self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def config(self) -> dict:
        return {"enabled": self.enabled.isChecked(), "screen": self.screen.currentData(),
                "areas": default_areas(self.windows.value()), "background": "#000000",
                "outline": self.outline.isChecked()}

    def _preview(self):
        c = self.config()
        w = TouchStimulusWindow(c["areas"], c["background"], True, self)
        for a in c["areas"]:
            w.show_stimulus(a["name"])
        w.show_on_screen(c["screen"])
        QTimer.singleShot(3000, w.close)

    def accept(self):
        self.project.settings_extra["touchscreen"] = self.config()
        super().accept()
