"""Apparatus page: draw the arena, zones, points and lines over a video frame; templates; calibration."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QFontMetricsF, QIcon, QKeySequence,
                           QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsEllipseItem,
                               QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QTabWidget,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from ...core import templates
from ...core.apparatus import (ENTRY_RULES, GRID_KINDS, Apparatus, Line, PointOfInterest, Sequence, Zone,
                               ZoneGroup, make_grid, remove_grid)
from ...core.geometry import Ellipse, Polygon, Shape
from ...core.templates import PALETTE, TEMPLATES
from ...core.video import VIDEO_EXTENSIONS, VideoSource
from ..widgets import FrameView, compact_form, draw_apparatus, fmt_time, shape_path
from .base import Page

ACCENT = "#e11d48"

# key, label, shortcut, tooltip
TOOLS = [
    ("select", "Select", "V", "Select, move and reshape items. Drag the white handles to edit vertices; "
                              "double-click a polygon edge to add a vertex, right-click a vertex to remove it. "
                              "Ctrl+C / Ctrl+V copy and paste the selection (also into another apparatus)."),
    ("rect", "Rectangle", "R", "Draw a rectangular zone (Shift: square)"),
    ("ellipse", "Ellipse", "E", "Draw an elliptical zone (Shift: circle)"),
    ("polygon", "Polygon", "P", "Click the vertices of a zone; double-click, right-click or Enter closes it"),
    ("point", "Point", "O", "Click to add a point of interest (object, platform, cup…)"),
    ("line", "Line", "L", "Drag a line; crossings are counted in each direction"),
    ("arena", "Arena", "A", "Draw the arena boundary (choose rectangle / ellipse / polygon from the arrow menu)"),
    ("calibrate", "Calibrate", "C", "Drag along something of known length, then enter its real length in cm"),
]

HINTS = {
    "select": "Click to select · drag to move · drag handles to reshape · Del deletes · wheel zooms · "
              "middle-drag pans",
    "rect": "Drag to draw a rectangular zone (Shift = square)",
    "ellipse": "Drag to draw an elliptical zone (Shift = circle)",
    "polygon": "Click to add vertices · double-click / right-click / Enter closes · Backspace undoes a vertex · "
               "Esc cancels",
    "point": "Click to place a point of interest",
    "line": "Drag to draw a crossing line",
    "arena": "Draw the arena boundary",
    "calibrate": "Drag along an object of known length (e.g. the arena wall)",
    "template": "Drag a rectangle around the whole apparatus · Esc cancels",
}

SEQ_END = {"entry": "On entering the last step", "exit": "On leaving the last step"}


# ------------------------------------------------------------------ helpers
def unique_name(name: str, taken) -> str:
    taken = set(taken)
    name = name.strip() or "Unnamed"
    if name not in taken:
        return name
    base = name
    i = 2
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"


def _is_axis_rect(shape) -> bool:
    if not isinstance(shape, Polygon) or len(shape.points) != 4:
        return False
    xs = sorted({round(p[0], 6) for p in shape.points})
    ys = sorted({round(p[1], 6) for p in shape.points})
    return len(xs) == 2 and len(ys) == 2


def describe_shape(shape, app: Apparatus | None) -> str:
    if shape is None:
        return "—"
    if isinstance(shape, Ellipse):
        kind = "Circle" if abs(shape.rx - shape.ry) < 1e-6 else "Ellipse"
    elif _is_axis_rect(shape):
        kind = "Rectangle"
    else:
        kind = f"Polygon ({len(shape.points)} vertices)"
    a = shape.area()
    if app is not None and app.px_per_cm:
        return f"{kind} · {a / app.px_per_cm ** 2:,.1f} cm²"
    return f"{kind} · {a:,.0f} px²"


def color_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def tool_icon(kind: str) -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    acc = QColor(ACCENT)
    pen = QPen(acc, 2.2)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QColor(225, 29, 72, 55))
    pt = QPointF
    if kind == "select":
        p.drawPolygon(QPolygonF([pt(10, 5), pt(10, 25), pt(15, 20.5), pt(18.5, 28), pt(22, 26.5), pt(18.5, 19),
                                 pt(25, 19)]))
    elif kind == "rect":
        p.drawRect(QRectF(5, 8, 22, 16))
    elif kind == "ellipse":
        p.drawEllipse(QRectF(4, 8, 24, 16))
    elif kind == "polygon":
        p.drawPolygon(QPolygonF([pt(5, 22), pt(10, 6), pt(22, 5), pt(27, 17), pt(16, 27)]))
    elif kind == "point":
        dpen = QPen(acc, 1.6, Qt.DotLine)
        p.setPen(dpen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pt(16, 16), 11, 11)
        p.setPen(Qt.NoPen)
        p.setBrush(acc)
        p.drawEllipse(pt(16, 16), 4, 4)
    elif kind == "line":
        p.drawLine(pt(6, 26), pt(26, 6))
        p.setBrush(acc)
        p.drawEllipse(pt(6, 26), 2.5, 2.5)
        p.drawEllipse(pt(26, 6), 2.5, 2.5)
    elif kind == "arena":
        dpen = QPen(acc, 2.2, Qt.DashLine)
        p.setPen(dpen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(5, 5, 22, 22), 3, 3)
    elif kind == "calibrate":
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(3, 12, 26, 9))
        p.setPen(QPen(acc, 1.4))
        for i, x in enumerate(range(6, 28, 3)):
            p.drawLine(pt(x, 12), pt(x, 15.5 if i % 2 else 17.5))
    elif kind == "grid":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(4, 4, 24, 24))
        p.setPen(QPen(acc, 1.4))
        p.drawEllipse(QRectF(10, 10, 12, 12))
        for a in range(0, 360, 60):
            r = math.radians(a)
            p.drawLine(pt(16 + 6 * math.cos(r), 16 + 6 * math.sin(r)), pt(16 + 12 * math.cos(r), 16 + 12 * math.sin(r)))
    elif kind in ("copy", "paste"):
        p.setBrush(QColor(225, 29, 72, 40))
        p.drawRoundedRect(QRectF(5, 9, 15, 18), 2, 2)
        if kind == "copy":
            p.drawRoundedRect(QRectF(12, 4, 15, 18), 2, 2)
        else:
            p.setBrush(acc)
            p.drawRect(QRectF(9, 6, 7, 5))
            p.setBrush(Qt.NoBrush)
            p.drawLine(pt(14, 21), pt(27, 21))
            p.drawPolyline(QPolygonF([pt(23, 17), pt(27, 21), pt(23, 25)]))
    elif kind in ("undo", "redo"):
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(pt(9, 13))
        path.cubicTo(pt(15, 6), pt(27, 8), pt(25, 21))
        p.save()
        if kind == "redo":
            p.translate(32, 0)
            p.scale(-1, 1)
        p.drawPath(path)
        p.setBrush(acc)
        p.drawPolygon(QPolygonF([pt(4, 9), pt(12, 18), pt(13, 8)]))
        p.restore()
    elif kind == "fit":
        p.setBrush(Qt.NoBrush)
        for x, y, dx, dy in ((5, 5, 1, 1), (27, 5, -1, 1), (5, 27, 1, -1), (27, 27, -1, -1)):
            p.drawPolyline(QPolygonF([pt(x, y + 7 * dy), pt(x, y), pt(x + 7 * dx, y)]))
        p.drawRect(QRectF(11, 11, 10, 10))
    p.end()
    return QIcon(pm)


def placeholder_frame(w: int, h: int) -> np.ndarray:
    img = np.full((h, w, 3), (59, 41, 30), np.uint8)
    step = max(20, int(round(max(w, h) / 16 / 10)) * 10)
    for x in range(0, w, step):
        cv2.line(img, (x, 0), (x, h), (78, 58, 46), 1)
    for y in range(0, h, step):
        cv2.line(img, (0, y), (w, y), (78, 58, 46), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    sc = max(0.5, w / 900)
    for i, (txt, col) in enumerate((("No background image", (241, 245, 248)),
                                    ("Load a frame from a video (left panel)", (184, 163, 148)))):
        (tw, th), _ = cv2.getTextSize(txt, font, sc * (1.0 if i == 0 else 0.7), 1)
        cv2.putText(img, txt, ((w - tw) // 2, h // 2 + i * int(36 * sc)), font, sc * (1.0 if i == 0 else 0.7),
                    col, 1, cv2.LINE_AA)
    return img


# --------------------------------------------------------------- canvas items
class Label(QGraphicsItem):
    """Screen-sized text label with a dark rounded background; transparent to the mouse."""

    def __init__(self, text: str, parent=None, color: str = "#ffffff", anchor: str = "center"):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.font = QFont()
        self.font.setPointSizeF(8.5)
        self.font.setBold(True)
        self.anchor = anchor
        self.color = QColor(color)
        self.border = QColor(color)
        self.text = ""
        self._rect = QRectF()
        self.set_text(text)

    def set_text(self, text: str, border: str | None = None):
        if border:
            self.border = QColor(border)
        if text == self.text and not self._rect.isNull():
            self.update()
            return
        self.prepareGeometryChange()
        self.text = text
        fm = QFontMetricsF(self.font)
        w = fm.horizontalAdvance(text) + 10
        h = fm.height() + 3
        self._rect = {"center": QRectF(-w / 2, -h / 2, w, h), "top": QRectF(-w / 2, 5, w, h)}.get(
            self.anchor, QRectF(9, -h - 5, w, h))

    def set_anchor(self, anchor: str):
        if anchor != self.anchor:
            self.anchor = anchor
            t, self.text = self.text, ""
            self.set_text(t)

    def boundingRect(self):
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(self, p, opt, widget=None):
        if not self.text:
            return
        p.setPen(QPen(self.border, 1))
        p.setBrush(QColor(15, 23, 42, 185))
        p.drawRoundedRect(self._rect, 3, 3)
        p.setPen(self.color)
        p.setFont(self.font)
        p.drawText(self._rect, Qt.AlignCenter, self.text)


class Handle(QGraphicsRectItem):
    """Screen-sized vertex handle; drags are reported to the owning item."""

    def __init__(self, owner, index: int):
        super().__init__(-4.5, -4.5, 9, 9, owner)
        self.owner = owner
        self.index = index
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#0f172a"), 1))
        self.setZValue(100)
        self.setCursor(Qt.CrossCursor)
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setVisible(False)
        self._dragging = False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.owner.page.push_undo()
            self._dragging = True
            e.accept()
        elif e.button() == Qt.RightButton and hasattr(self.owner, "delete_vertex"):
            QTimer.singleShot(0, lambda o=self.owner, i=self.index: o.delete_vertex(i))
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.owner.move_handle(self.index, self.owner.mapFromScene(e.scenePos()), e.modifiers())

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.owner.page.geometry_edited(self.owner)


class _HandlesMixin:
    handles: list

    def _sync_handles(self, pts):
        while len(self.handles) < len(pts):
            self.handles.append(Handle(self, len(self.handles)))
        while len(self.handles) > len(pts):
            h = self.handles.pop()
            h.setParentItem(None)
            if h.scene() is not None:
                h.scene().removeItem(h)
        sel = self.isSelected()
        for h, (x, y) in zip(self.handles, pts):
            h.setPos(float(x), float(y))
            h.setVisible(sel)


class ShapeItem(_HandlesMixin, QGraphicsPathItem):
    """Editable zone (kind='zone') or arena boundary (kind='arena')."""

    def __init__(self, page, kind: str, index: int, parent):
        super().__init__(parent)
        self.page, self.kind, self.index = page, kind, index
        self.handles = []
        self.label = Label("", self) if kind == "zone" else None
        self.label_at_top = False
        self.halo: QGraphicsPathItem | None = None  # investigation distance outline
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.sync()

    @property
    def model(self):
        return self.page.app.zones[self.index] if self.kind == "zone" else None

    def get_shape(self) -> Shape:
        return self.page.app.arena if self.kind == "arena" else self.model.shape

    def set_shape(self, s: Shape):
        if self.kind == "arena":
            self.page.app.arena = s
        else:
            self.model.shape = s

    def handle_points(self):
        s = self.get_shape()
        if isinstance(s, Ellipse):
            return [(s.cx + s.rx, s.cy), (s.cx - s.rx, s.cy), (s.cx, s.cy + s.ry), (s.cx, s.cy - s.ry)]
        return list(s.points)

    def sync(self):
        s = self.get_shape()
        if s is None:
            return
        self.setPath(shape_path(s))
        sel = self.isSelected()
        if self.kind == "zone":
            c = QColor(self.model.color)
            pen = QPen(QColor(ACCENT) if sel else c)
            pen.setCosmetic(True)
            pen.setWidthF(2.6 if sel else 1.6)
            fc = QColor(c)
            fc.setAlpha(95 if sel else 45)
            z = self.model
            if z.hidden:
                fc.setAlpha(150 if sel else 110)
                self.setBrush(QBrush(fc, Qt.BDiagPattern))
                pen.setStyle(Qt.DashLine)
            else:
                self.setBrush(QBrush(fc))
            self._sync_halo(s, c)
            if self.label_at_top:
                x0, y0, x1, _ = s.bounds()
                self.label.setPos((x0 + x1) / 2, y0)
            else:
                self.label.setPos(*s.centroid())
            self.label.set_anchor("top" if self.label_at_top else "center")
            tags = [t for t, on in (("hidden", z.hidden), ("moveable", z.moveable)) if on]
            self.label.set_text(z.name + (f" ({', '.join(tags)})" if tags else ""), self.model.color)
            self.label.setVisible(self.page.show_labels)
        else:
            pen = QPen(QColor(ACCENT if sel else "#f8fafc"))
            pen.setCosmetic(True)
            pen.setWidthF(2.6 if sel else 2.0)
            pen.setStyle(Qt.DashLine)
            self.setBrush(Qt.NoBrush)
        self.setPen(pen)
        self._sync_handles(self.handle_points())

    def _sync_halo(self, s: Shape, c: QColor):
        """Dotted outline at the investigation distance around the zone."""
        app = self.page.app
        d = self.model.investigation_distance_cm * (app.px_per_cm or 1.0)
        if d <= 0:
            if self.halo is not None:
                self.halo.setVisible(False)
            return
        if self.halo is None:
            self.halo = QGraphicsPathItem(self)
            self.halo.setAcceptedMouseButtons(Qt.NoButton)
        st = QPainterPathStroker()
        st.setWidth(2 * d)
        st.setJoinStyle(Qt.RoundJoin)
        base = shape_path(s)
        self.halo.setPath(st.createStroke(base).united(base).simplified())
        pen = QPen(c, 1.3, Qt.DotLine)
        pen.setCosmetic(True)
        self.halo.setPen(pen)
        self.halo.setBrush(Qt.NoBrush)
        self.halo.setVisible(True)

    def paint(self, p, opt, widget=None):
        p.setPen(self.pen())
        p.setBrush(self.brush())
        p.drawPath(self.path())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged and hasattr(self, "page"):
            self.sync()
        return super().itemChange(change, value)

    def move_handle(self, i: int, p: QPointF, mods=Qt.NoModifier):
        s = self.get_shape()
        if isinstance(s, Ellipse):
            x0, x1, y0, y1 = s.cx - s.rx, s.cx + s.rx, s.cy - s.ry, s.cy + s.ry
            if i == 0:
                x1 = p.x()
            elif i == 1:
                x0 = p.x()
            elif i == 2:
                y1 = p.y()
            else:
                y0 = p.y()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rx, ry = max(1.0, abs(x1 - x0) / 2), max(1.0, abs(y1 - y0) / 2)
            if mods & Qt.ShiftModifier:
                if i in (0, 1):
                    ry = rx
                else:
                    rx = ry
            new = Ellipse(cx, cy, rx, ry)
        else:
            pts = list(s.points)
            pts[i] = (float(p.x()), float(p.y()))
            new = Polygon(pts)
        self.set_shape(new)
        self.sync()

    def delete_vertex(self, i: int):
        s = self.get_shape()
        if not isinstance(s, Polygon) or len(s.points) <= 3:
            return
        self.page.push_undo()
        pts = list(s.points)
        del pts[i]
        self.set_shape(Polygon(pts))
        self.sync()
        self.page.geometry_edited(self)

    def insert_vertex(self, x: float, y: float) -> bool:
        s = self.get_shape()
        if not isinstance(s, Polygon) or len(s.points) < 2:
            return False
        pts = list(s.points)
        best, bi = math.inf, 0
        for i in range(len(pts)):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1e-12)))
            d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
            if d < best:
                best, bi = d, i
        self.page.push_undo()
        pts.insert(bi + 1, (float(x), float(y)))
        self.set_shape(Polygon(pts))
        self.sync()
        self.page.geometry_edited(self)
        return True

    def mouseDoubleClickEvent(self, e):
        if self.isSelected() and self.page.view.tool == "select" and self.insert_vertex(e.pos().x(), e.pos().y()):
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def moved(self) -> bool:
        return self.pos() != QPointF(0, 0)

    def commit_pos(self):
        d = self.pos()
        self.set_shape(self.get_shape().translated(d.x(), d.y()))
        self.setPos(0, 0)
        self.sync()


class PointItem(QGraphicsEllipseItem):
    """Point of interest: screen-sized marker plus a ring showing its radius (when calibrated)."""

    R = 6.5

    def __init__(self, page, index: int, parent):
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R, parent)
        self.page, self.kind, self.index = page, "point", index
        self.ring = QGraphicsEllipseItem(parent)
        self.ring.setAcceptedMouseButtons(Qt.NoButton)
        self.ring.setZValue(5)
        self.label = Label("", self, anchor="right")
        self._ready = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemSendsGeometryChanges)
        self._ready = True
        self.sync()

    @property
    def model(self) -> PointOfInterest:
        return self.page.app.points[self.index]

    def sync(self):
        m = self.model
        self.setPos(m.x, m.y)
        self.label.set_text(m.name, m.color)
        self.label.setVisible(self.page.show_labels)
        self._update_ring()
        self.update()

    def _update_ring(self):
        m, app = self.model, self.page.app
        r = (m.radius_cm or 0) * (app.px_per_cm or 0)
        if r > 0:
            p = self.pos()
            self.ring.setRect(p.x() - r, p.y() - r, 2 * r, 2 * r)
            c = QColor(m.color)
            pen = QPen(c, 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            self.ring.setPen(pen)
            fc = QColor(c)
            fc.setAlpha(25)
            self.ring.setBrush(fc)
            self.ring.setVisible(True)
        else:
            self.ring.setVisible(False)

    def paint(self, p, opt, widget=None):
        c = QColor(self.model.color)
        if self.isSelected():
            p.setPen(QPen(QColor(ACCENT), 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0, 0), self.R + 1.5, self.R + 1.5)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.setBrush(c)
        p.drawEllipse(QPointF(0, 0), self.R - 1, self.R - 1)
        p.setPen(QPen(QColor("#0f172a"), 1.2))
        p.drawLine(QPointF(-2.5, 0), QPointF(2.5, 0))
        p.drawLine(QPointF(0, -2.5), QPointF(0, 2.5))

    def boundingRect(self):
        r = self.R + 3
        return QRectF(-r, -r, 2 * r, 2 * r)

    def itemChange(self, change, value):
        if getattr(self, "_ready", False):
            if change == QGraphicsItem.ItemPositionHasChanged:
                self._update_ring()
            elif change == QGraphicsItem.ItemSelectedHasChanged:
                self.update()
        return super().itemChange(change, value)

    def moved(self) -> bool:
        m = self.model
        return abs(self.pos().x() - m.x) > 1e-9 or abs(self.pos().y() - m.y) > 1e-9

    def commit_pos(self):
        m = self.model
        m.x, m.y = float(self.pos().x()), float(self.pos().y())


class LineItem(_HandlesMixin, QGraphicsPathItem):
    """Crossing line with two endpoint handles."""

    def __init__(self, page, index: int, parent):
        super().__init__(parent)
        self.page, self.kind, self.index = page, "line", index
        self.handles = []
        self.label = Label("", self)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.sync()

    @property
    def model(self) -> Line:
        return self.page.app.lines[self.index]

    def sync(self):
        m = self.model
        path = QPainterPath(QPointF(m.x1, m.y1))
        path.lineTo(m.x2, m.y2)
        self.prepareGeometryChange()
        self.setPath(path)
        sel = self.isSelected()
        pen = QPen(QColor(ACCENT if sel else m.color))
        pen.setCosmetic(True)
        pen.setWidthF(3.5 if sel else 2.5)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)
        self.label.setPos(m.x1 + 0.2 * (m.x2 - m.x1), m.y1 + 0.2 * (m.y2 - m.y1))
        self.label.set_text(m.name, m.color)
        self.label.setVisible(self.page.show_labels)
        self._sync_handles([(m.x1, m.y1), (m.x2, m.y2)])

    def _tol(self) -> float:
        s = self.page.view.transform().m11() or 1.0
        return 10.0 / s

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(self._tol())
        return st.createStroke(self.path())

    def boundingRect(self):
        t = self._tol()
        return self.path().boundingRect().adjusted(-t, -t, t, t)

    def paint(self, p, opt, widget=None):
        p.setPen(self.pen())
        p.drawPath(self.path())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged and hasattr(self, "page"):
            self.sync()
        return super().itemChange(change, value)

    def move_handle(self, i: int, p: QPointF, mods=Qt.NoModifier):
        m = self.model
        if i == 0:
            m.x1, m.y1 = float(p.x()), float(p.y())
        else:
            m.x2, m.y2 = float(p.x()), float(p.y())
        self.sync()

    def moved(self) -> bool:
        return self.pos() != QPointF(0, 0)

    def commit_pos(self):
        d = self.pos()
        m = self.model
        m.x1 += d.x()
        m.x2 += d.x()
        m.y1 += d.y()
        m.y2 += d.y()
        self.setPos(0, 0)
        self.sync()


# --------------------------------------------------------------------- view
class EditorView(FrameView):
    """Drawing canvas: implements the tools; scene coordinates are video pixels."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.tool = "select"
        self._start: QPointF | None = None
        self._poly: list[QPointF] = []
        self._preview: QGraphicsPathItem | None = None
        self._preview_group = None
        self._pan = None
        self.scene().setItemIndexMethod(QGraphicsScene.NoIndex)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setFocusPolicy(Qt.StrongFocus)

    def fit(self):
        # fit the whole scene rect (frame + margin) so no scrollbars appear; fitting only the frame makes
        # scrollbars toggle on every resize, which loops forever
        if self.frame_size and not getattr(self, "_fitting", False):
            self._fitting = True
            try:
                self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            finally:
                self._fitting = False
            self._auto_fit = True

    # ---- tool state ---------------------------------------------------
    def set_tool(self, tool: str):
        self.cancel_drawing()
        self.tool = tool
        self.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)

    def drag_kind(self) -> str:
        if self.tool == "arena":
            return self.page.arena_shape
        return {"line": "line", "calibrate": "line", "template": "rect"}.get(self.tool, self.tool)

    @property
    def drawing(self) -> bool:
        return self._start is not None or bool(self._poly)

    def cancel_drawing(self):
        self._start = None
        self._poly = []
        self._clear_preview()
        self.page.show_measure("")

    def _clear_preview(self):
        sc = self.scene()
        if self._preview is not None:
            sc.removeItem(self._preview)
            self._preview = None
        if self._preview_group is not None:
            sc.removeItem(self._preview_group)
            self._preview_group = None

    def _set_preview(self, path: QPainterPath, closed=True):
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(QColor("#facc15"), 2, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self._preview.setZValue(500)
            self.scene().addItem(self._preview)
        self._preview.setBrush(QColor(250, 204, 21, 45) if closed else Qt.NoBrush)
        self._preview.setPath(path)

    # ---- geometry helpers ------------------------------------------------
    @staticmethod
    def constrain(p0: QPointF, p1: QPointF, square: bool) -> QPointF:
        if not square:
            return p1
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        s = max(abs(dx), abs(dy))
        return QPointF(p0.x() + math.copysign(s, dx or 1), p0.y() + math.copysign(s, dy or 1))

    def _update_drag_preview(self, p: QPointF, mods):
        kind = self.drag_kind()
        p0 = self._start
        square = bool(mods & Qt.ShiftModifier) or (self.tool == "template" and self.page.template_square())
        if kind in ("rect", "ellipse"):
            p = self.constrain(p0, p, square)
        r = QRectF(p0, p).normalized()
        if self.tool == "template":
            self._clear_preview()
            app = self.page.pending_template_preview(r.x(), r.y(), r.width(), r.height())
            if app is not None:
                self._preview_group = draw_apparatus(self.scene(), app)
                self._preview_group.setZValue(500)
            self._set_preview(QPainterPath(), True)
            path = QPainterPath()
            path.addRect(r)
            self._preview.setPath(path)
            self._preview.setBrush(Qt.NoBrush)
            self.page.show_measure(f"{r.width():.0f} × {r.height():.0f} px")
            return
        path = QPainterPath()
        if kind == "rect":
            path.addRect(r)
            self.page.show_measure(f"{r.width():.0f} × {r.height():.0f} px")
        elif kind == "ellipse":
            path.addEllipse(r)
            self.page.show_measure(f"{r.width():.0f} × {r.height():.0f} px")
        elif kind == "line":
            path.moveTo(p0)
            path.lineTo(p)
            self.page.show_measure(f"Length {math.hypot(p.x() - p0.x(), p.y() - p0.y()):.1f} px")
        self._set_preview(path, kind != "line")

    def _update_poly_preview(self, cursor: QPointF | None = None):
        pts = list(self._poly) + ([cursor] if cursor is not None else [])
        if not pts:
            return
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if len(pts) > 2:
            path.closeSubpath()
        self._set_preview(path, len(pts) > 2)
        self.page.show_measure(f"{len(self._poly)} vertices")

    def finish_polygon(self):
        pts = [(p.x(), p.y()) for p in self._poly]
        # drop duplicate consecutive vertices (double-click adds the same point twice)
        clean = []
        for q in pts:
            if not clean or math.hypot(q[0] - clean[-1][0], q[1] - clean[-1][1]) > 1e-6:
                clean.append(q)
        self.cancel_drawing()
        if len(clean) >= 3:
            self.page.finish_polygon(clean)

    # ---- events ---------------------------------------------------------
    def mousePressEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if self.tool == "select":
            super().mousePressEvent(e)
            return
        self.setFocus()
        kind = self.drag_kind()
        if kind == "polygon":
            if e.button() == Qt.RightButton:
                self.finish_polygon()
            elif e.button() == Qt.LeftButton:
                self._poly.append(p)
                self._update_poly_preview()
            e.accept()
            return
        if e.button() != Qt.LeftButton:
            if e.button() == Qt.RightButton:
                self.cancel_drawing()
            return
        if kind == "point":
            self.page.add_point(p.x(), p.y())
            e.accept()
            return
        self._start = p
        e.accept()

    def mouseMoveEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        self.mouse_moved.emit(p.x(), p.y())
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            self._auto_fit = False
            return
        if self.tool == "select":
            QGraphicsView.mouseMoveEvent(self, e)
            return
        if self._start is not None:
            self._update_drag_preview(p, e.modifiers())
        elif self._poly:
            self._update_poly_preview(p)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan is not None:
            self._pan = None
            self.viewport().setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
            return
        if self.tool == "select":
            super().mouseReleaseEvent(e)
            self.page.commit_moves()
            return
        if self._start is not None and e.button() == Qt.LeftButton:
            p0 = self._start
            p1 = self.mapToScene(e.position().toPoint())
            self.cancel_drawing()
            a = self.mapFromScene(p0)
            b = e.position().toPoint()
            if abs(a.x() - b.x()) + abs(a.y() - b.y()) < 4:
                return
            self.page.finish_drag(p0.x(), p0.y(), p1.x(), p1.y(), square=bool(e.modifiers() & Qt.ShiftModifier))

    def mouseDoubleClickEvent(self, e):
        if self.tool != "select" and self.drag_kind() == "polygon":
            self.finish_polygon()
            e.accept()
            return
        if self.tool == "select":
            super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            if self.drawing:
                self.cancel_drawing()
            elif self.tool != "select":
                self.page.set_tool("select")
            else:
                self.scene().clearSelection()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter) and self._poly:
            self.finish_polygon()
            return
        if k == Qt.Key_Backspace and self._poly:
            self._poly.pop()
            if self._poly:
                self._update_poly_preview()
            else:
                self.cancel_drawing()
            return
        if k in (Qt.Key_Delete, Qt.Key_Backspace) and not self.drawing:
            self.page.delete_selected()
            return
        if k in (Qt.Key_Plus, Qt.Key_Equal):
            self.scale(1.25, 1.25)
            self._auto_fit = False
            return
        if k == Qt.Key_Minus:
            self.scale(0.8, 0.8)
            self._auto_fit = False
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- widgets
class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, title="Colour", parent=None):
        super().__init__(parent)
        self.title = title
        self._color = "#3b82f6"
        self.setFixedHeight(24)
        self.setMinimumWidth(90)
        self.clicked.connect(self._choose)
        self.set_color(self._color)

    def color(self) -> str:
        return self._color

    def set_color(self, c: str):
        self._color = c
        q = QColor(c)
        fg = "#000" if q.lightnessF() > 0.6 else "#fff"
        self.setText(c)
        self.setStyleSheet(f"QPushButton{{background:{c};color:{fg};border:1px solid #64748b;border-radius:4px;}}")

    def _choose(self):
        c = QColorDialog.getColor(QColor(self._color), self, self.title)
        if c.isValid():
            self.set_color(c.name())
            self.color_changed.emit(c.name())


def _param_label(key: str) -> str:
    unit = ""
    if key.endswith("_cm"):
        key, unit = key[:-3], " (cm)"
    elif key.endswith("_s"):
        key, unit = key[:-2], " (s)"
    return key.replace("_", " ").capitalize() + unit


class TemplateDialog(QDialog):
    """Choose a built-in template, edit its parameters and how it is placed."""

    NON_SQUARE = {"light_dark", "three_chamber", "t_maze", "fear_conditioning", "custom", "novel_tank", "multi_well",
                  "cpp", "home_cage"}

    def __init__(self, parent=None, key: str = "open_field", current_name: str | None = None,
                 taken_names=(), has_background: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Create apparatus from template")
        self.current_name = current_name
        self.taken = list(taken_names)
        self._editors: dict[str, tuple[QWidget, type]] = {}
        self._name_touched = False

        self.list = QListWidget()
        self.list.setFixedWidth(230)
        for k, t in TEMPLATES.items():
            it = QListWidgetItem(t.title)
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.params_box = QGroupBox("Parameters")
        self.params_form = QFormLayout(self.params_box)
        self.params_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.preview = QGraphicsView()
        self.preview.setScene(QGraphicsScene(self.preview))
        self.preview.setFixedSize(220, 220)
        self.preview.setRenderHints(QPainter.Antialiasing)
        self.preview.setBackgroundBrush(QColor("#1e293b"))
        self.preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview.setInteractive(False)

        place = QGroupBox("Placement on the video image")
        pl = QVBoxLayout(place)
        self.place_drag = QRadioButton("Drag a rectangle around the apparatus on the image")
        self.place_fit = QRadioButton("Fit to the whole frame")
        (self.place_drag if has_background else self.place_fit).setChecked(True)
        self.square = QCheckBox("Keep square proportions")
        pl.addWidget(self.place_drag)
        pl.addWidget(self.place_fit)
        pl.addWidget(self.square)

        tgt = QGroupBox("Apparatus")
        tl = QFormLayout(tgt)
        self.replace = QRadioButton(f"Replace “{current_name}”" if current_name else "Replace current apparatus")
        self.add_new = QRadioButton("Add as a new apparatus")
        self.replace.setEnabled(current_name is not None)
        (self.replace if current_name is not None else self.add_new).setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.replace)
        bg.addButton(self.add_new)
        self.replace.toggled.connect(self._update_name)
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(lambda _: setattr(self, "_name_touched", True))
        tl.addRow(self.replace)
        tl.addRow(self.add_new)
        tl.addRow("Name", self.name_edit)

        right = QVBoxLayout()
        top = QHBoxLayout()
        tv = QVBoxLayout()
        tv.addWidget(self.title)
        tv.addWidget(self.desc)
        tv.addWidget(self.params_box)
        tv.addStretch()
        top.addLayout(tv, 1)
        top.addWidget(self.preview, 0, Qt.AlignTop)
        right.addLayout(top)
        right.addWidget(place)
        right.addWidget(tgt)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay = QHBoxLayout(self)
        lay.addWidget(self.list)
        lay.addLayout(right, 1)
        self.resize(820, 560)

        self.list.currentRowChanged.connect(self._template_changed)
        self.square.toggled.connect(self._update_preview)
        self.select_template(key if key in TEMPLATES else "open_field")

    # ---- public API ----------------------------------------------------------
    def select_template(self, key: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == key:
                self.list.setCurrentRow(i)
                return

    def key(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else "open_field"

    def params(self) -> dict:
        out = {}
        for k, (w, typ) in self._editors.items():
            if typ is bool:
                out[k] = w.isChecked()
            elif typ is int:
                out[k] = int(w.value())
            elif typ is float:
                out[k] = float(w.value())
            elif isinstance(w, QComboBox):
                out[k] = w.currentData() if w.currentData() is not None else w.currentText()
            else:
                out[k] = w.text()
        return out

    def set_param(self, k: str, v):
        w, typ = self._editors[k]
        if typ is bool:
            w.setChecked(bool(v))
        elif typ in (int, float):
            w.setValue(v)
        elif isinstance(w, QComboBox):
            i = w.findData(v)
            if i >= 0:
                w.setCurrentIndex(i)
            else:
                w.setCurrentText(str(v))
        else:
            w.setText(str(v))

    def name(self) -> str:
        return self.name_edit.text().strip()

    def is_replace(self) -> bool:
        return self.replace.isChecked()

    def placement(self) -> str:
        return "fit" if self.place_fit.isChecked() else "drag"

    # ---- internals -----------------------------------------------------------
    def _template_changed(self, *_):
        info = TEMPLATES[self.key()]
        self.title.setText(info.title)
        self.desc.setText(info.description)
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self._editors = {}
        for k, v in info.params.items():
            if k in info.choices:
                w = QComboBox()
                for c in info.choices[k]:
                    w.addItem(str(c), c)
                i = w.findData(v)
                w.setCurrentIndex(max(0, i))
                w.currentIndexChanged.connect(self._update_preview)
                typ = object
            elif isinstance(v, bool):
                w = QCheckBox()
                w.setChecked(v)
                w.toggled.connect(self._update_preview)
                typ = bool
            elif isinstance(v, int):
                w = QSpinBox()
                w.setRange(3 if k.startswith("n_") else (1 if k == "escape_hole" else 0), 100 if k.startswith("n_") or k == "escape_hole" else 100000)
                w.setValue(v)
                w.valueChanged.connect(self._update_preview)
                typ = int
            elif isinstance(v, float):
                w = QDoubleSpinBox()
                if "fraction" in k:
                    w.setRange(0.01, 1.0)
                    w.setDecimals(3)
                    w.setSingleStep(0.05)
                else:
                    w.setRange(0.0, 100000.0)
                    w.setDecimals(1)
                    w.setSingleStep(1.0)
                    if k.endswith("_cm"):
                        w.setSuffix(" cm")
                w.setValue(v)
                w.valueChanged.connect(self._update_preview)
                typ = float
            else:
                w = QLineEdit(str(v))
                w.editingFinished.connect(self._update_preview)
                typ = str
            self._editors[k] = (w, typ)
            self.params_form.addRow(_param_label(k), w)
        self.params_box.setVisible(bool(info.params))
        self.square.setChecked(self.key() not in self.NON_SQUARE)
        self._update_name()
        self._update_preview()

    def _update_name(self, *_):
        if self._name_touched:
            return
        if self.replace.isChecked() and self.current_name:
            self.name_edit.setText(self.current_name)
        else:
            self.name_edit.setText(unique_name(TEMPLATES[self.key()].title, self.taken))

    def _update_preview(self, *_):
        sc = self.preview.scene()
        sc.clear()
        w, h = (200, 200) if self.square.isChecked() else (240, 160)
        try:
            app = templates.build(self.key(), 0, 0, w, h, **self.params())
        except Exception:
            return
        draw_apparatus(sc, app, labels=False, fill_alpha=70)
        self.preview.fitInView(QRectF(-12, -12, w + 24, h + 24), Qt.KeepAspectRatio)


# ---------------------------------------------------------------------- page
class GridDialog(QDialog):
    """Regularly spaced grid of zones: square cells, concentric rings, radial sectors or rings × sectors."""

    def __init__(self, parent=None, app: Apparatus | None = None, has_selection: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Add grid")
        self.app = app
        f = QFormLayout(self)
        self.kind = QComboBox()
        for k, lbl in GRID_KINDS.items():
            self.kind.addItem(lbl, k)
        self.region = QComboBox()
        self.region.addItem("Arena boundary", "arena")
        if has_selection:
            self.region.addItem("Selected zone", "zone")
        self.region.addItem("Whole image", "frame")
        self.name = QLineEdit(unique_name("Grid", (app.names() + [g.name for g in app.grids]) if app else []))
        self.nx, self.ny, self.rings, self.sectors = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for w, v in ((self.nx, 4), (self.ny, 4), (self.rings, 3), (self.sectors, 8)):
            w.setRange(1, 60)
            w.setValue(v)
        self.cell = QDoubleSpinBox()
        self.cell.setRange(0, 10000)
        self.cell.setDecimals(1)
        self.cell.setSuffix(" cm")
        self.cell.setSpecialValueText("Use column / row counts")
        self.cell.setToolTip("Real-world cell size (needs a calibration); overrides the counts")
        self.cell.setEnabled(bool(app and app.px_per_cm))
        self.start = QDoubleSpinBox()
        self.start.setRange(-360, 360)
        self.start.setValue(-90)
        self.start.setSuffix("°")
        self.start.setToolTip("Angle of the first sector edge: -90 = top, 0 = right; sectors run clockwise")
        self.clip = QCheckBox("Clip square cells to the region")
        self.clip.setChecked(True)
        self.group = QCheckBox("Add a zone group containing all cells")
        self.group.setChecked(True)
        f.addRow("Grid type", self.kind)
        f.addRow("Cover", self.region)
        f.addRow("Name", self.name)
        f.addRow("Columns", self.nx)
        f.addRow("Rows", self.ny)
        f.addRow("Cell size", self.cell)
        f.addRow("Rings", self.rings)
        f.addRow("Sectors", self.sectors)
        f.addRow("First sector at", self.start)
        f.addRow(self.clip)
        f.addRow(self.group)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add grid")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._form = f
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self, *_):
        k = self.kind.currentData()
        for w, on in ((self.nx, k == "square"), (self.ny, k == "square"), (self.cell, k == "square"),
                      (self.clip, k == "square"), (self.rings, k in ("rings", "polar")),
                      (self.sectors, k in ("sectors", "polar")), (self.start, k in ("sectors", "polar"))):
            self._form.setRowVisible(w, on)

    def spec(self) -> dict:
        k = self.kind.currentData()
        params = {}
        if k == "square":
            params.update(nx=self.nx.value(), ny=self.ny.value(), clip=self.clip.isChecked())
            if self.cell.value() > 0:
                params["cell_cm"] = self.cell.value()
        if k in ("rings", "polar"):
            params["rings"] = self.rings.value()
        if k in ("sectors", "polar"):
            params.update(sectors=self.sectors.value(), start_deg=self.start.value())
        return {"kind": k, "region": self.region.currentData(), "name": self.name.text().strip() or "Grid",
                "group": self.group.isChecked(), "params": params}


class ApparatusPage(Page):
    title = "Apparatus"

    def __init__(self, main):
        super().__init__(main)
        self._loading = False
        self._syncing = False
        self.show_labels = True
        self.arena_shape = "rect"
        self._items: dict[tuple[str, int], QGraphicsItem] = {}
        self._root: QGraphicsPathItem | None = None
        self._undo: dict[int, list[dict]] = {}
        self._redo: dict[int, list[dict]] = {}
        self._bg_memory: dict[int, tuple[str, float]] = {}
        self._bg_src: VideoSource | None = None
        self._bg_path: str | None = None
        self._bg_real = False
        self._pending_template: dict | None = None
        self._prev_tool = "select"
        self._seq_overlay: QGraphicsPathItem | None = None

        self.view = EditorView(self)
        self.view.scene().selectionChanged.connect(self._on_scene_selection)
        self.view.mouse_moved.connect(self._on_mouse_moved)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_centre())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setCollapsible(1, False)
        splitter.setSizes([240, 760, 320])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)

        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(60)
        self._bg_timer.timeout.connect(self._seek_background)
        self._set_enabled(False)

    # ================================================================ layout
    def _build_left(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(240)
        w.setMaximumWidth(330)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 4, 0)

        g = QGroupBox("Apparatus")
        gl = QVBoxLayout(g)
        self.app_list = QListWidget()
        self.app_list.setMinimumHeight(70)
        self.app_list.setMaximumHeight(170)
        self.app_list.currentRowChanged.connect(self._app_row_changed)
        self.app_list.itemDoubleClicked.connect(lambda _: self.rename_apparatus())
        gl.addWidget(self.app_list, 1)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.btn_add = QPushButton("Add")
        self.btn_add.setToolTip("Add an empty apparatus")
        self.btn_add.clicked.connect(lambda: self.add_apparatus())
        self.btn_dup = QPushButton("Duplicate")
        self.btn_dup.clicked.connect(lambda: self.duplicate_apparatus())
        self.btn_ren = QPushButton("Rename…")
        self.btn_ren.clicked.connect(lambda: self.rename_apparatus())
        self.btn_del = QPushButton("Delete")
        self.btn_del.clicked.connect(lambda: self.delete_apparatus())
        grid.addWidget(self.btn_add, 0, 0)
        grid.addWidget(self.btn_dup, 0, 1)
        grid.addWidget(self.btn_ren, 1, 0)
        grid.addWidget(self.btn_del, 1, 1)
        gl.addLayout(grid)
        self.btn_tpl = QPushButton("Create from template…")
        self.btn_tpl.setStyleSheet(f"QPushButton{{background:{ACCENT};color:white;font-weight:bold;padding:6px;"
                                   "border-radius:4px;} QPushButton:disabled{background:#94a3b8;}")
        self.btn_tpl.clicked.connect(lambda: self.create_from_template())
        gl.addWidget(self.btn_tpl)
        self.app_info = QLabel()
        self.app_info.setWordWrap(True)
        self.app_info.setStyleSheet("color:palette(placeholder-text);")
        gl.addWidget(self.app_info)
        lay.addWidget(g)

        bg = QGroupBox("Background image")
        bl = QVBoxLayout(bg)
        self.btn_bg = QPushButton("Load background from video…")
        self.btn_bg.clicked.connect(lambda: self.load_background_dialog())
        bl.addWidget(self.btn_bg)
        self.test_combo = QComboBox()
        self.test_combo.setToolTip("Use a frame from the video of one of the experiment's tests")
        self.test_combo.activated.connect(self._test_combo_activated)
        bl.addWidget(self.test_combo)
        trow = QHBoxLayout()
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.valueChanged.connect(self._slider_changed)
        self.time_spin = QDoubleSpinBox()
        self.time_spin.setDecimals(2)
        self.time_spin.setSuffix(" s")
        self.time_spin.setMaximumWidth(92)
        self.time_spin.valueChanged.connect(self._spin_changed)
        trow.addWidget(self.time_slider, 1)
        trow.addWidget(self.time_spin)
        bl.addLayout(trow)
        self.bg_label = QLabel("No background")
        self.bg_label.setWordWrap(True)
        self.bg_label.setStyleSheet("color:palette(placeholder-text);")
        bl.addWidget(self.bg_label)
        lay.addWidget(bg)

        cal = QGroupBox("Calibration")
        cl = QVBoxLayout(cal)
        self.cal_label = QLabel()
        self.cal_label.setWordWrap(True)
        cl.addWidget(self.cal_label)
        f = QFormLayout()
        self.ppc_spin = QDoubleSpinBox()
        self.ppc_spin.setRange(0.0, 100000.0)
        self.ppc_spin.setDecimals(3)
        self.ppc_spin.setSpecialValueText("Not calibrated")
        self.ppc_spin.setSuffix(" px/cm")
        self.ppc_spin.setToolTip("Pixels per centimetre. 0 = not calibrated (results in pixels).")
        self.ppc_spin.editingFinished.connect(self._ppc_edited)
        f.addRow("Scale", self.ppc_spin)
        cl.addLayout(f)
        crow = QHBoxLayout()
        self.btn_cal = QPushButton("Calibrate with a line")
        self.btn_cal.clicked.connect(lambda: self.set_tool("calibrate"))
        self.btn_cal_clear = QPushButton("Clear")
        self.btn_cal_clear.clicked.connect(self.clear_calibration)
        crow.addWidget(self.btn_cal, 1)
        crow.addWidget(self.btn_cal_clear)
        cl.addLayout(crow)
        lay.addWidget(cal)
        tips = QLabel("<b>How to</b><ol style='margin-left:-20px'>"
                      "<li>Create from template and drag it over the apparatus in the image.</li>"
                      "<li>Adjust zones: select, drag, or drag their white handles.</li>"
                      "<li>Check the calibration (or calibrate with a line of known length).</li>"
                      "<li>Draw extra zones, points or lines with the toolbar.</li></ol>")
        tips.setWordWrap(True)
        tips.setStyleSheet("color:palette(placeholder-text);")
        tips.setAlignment(Qt.AlignTop)
        lay.addWidget(tips, 1)
        return w

    def _build_centre(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)
        tb = QToolBar()
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setStyleSheet("QToolBar{spacing:0px;}")
        self.toolbar = tb
        self.tool_actions: dict[str, QAction] = {}
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for key, label, sc, tip in TOOLS:
            a = QAction(tool_icon(key), label, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(sc))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.setToolTip(f"{tip}  [{sc}]")
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            grp.addAction(a)
            tb.addAction(a)
            self.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        # arena shape menu
        menu = QMenu(self)
        self.arena_actions = {}
        ag = QActionGroup(self)
        for k, lbl in (("rect", "Rectangle"), ("ellipse", "Ellipse / circle"), ("polygon", "Polygon")):
            a = menu.addAction(lbl)
            a.setCheckable(True)
            a.setChecked(k == "rect")
            ag.addAction(a)
            a.triggered.connect(lambda _=False, k=k: self.set_arena_shape(k))
            self.arena_actions[k] = a
        btn = tb.widgetForAction(self.tool_actions["arena"])
        if isinstance(btn, QToolButton):
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.grid_act = QAction(tool_icon("grid"), "Grid…", self)
        self.grid_act.setShortcut(QKeySequence("G"))
        self.grid_act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.grid_act.setToolTip("Add a regular grid of zones: square cells (in real-world units), concentric rings "
                                 "or radial sectors  [G]")
        self.grid_act.triggered.connect(lambda: self.add_grid_dialog())
        tb.addAction(self.grid_act)
        self.addAction(self.grid_act)
        b = tb.widgetForAction(self.grid_act)
        if isinstance(b, QToolButton):
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.addSeparator()
        self.copy_act = QAction(tool_icon("copy"), "Copy", self)
        self.copy_act.setShortcut(QKeySequence.Copy)
        self.copy_act.setToolTip("Copy the selected zones, points and lines (Ctrl+C)")
        self.copy_act.triggered.connect(lambda: self.copy_selected())
        self.paste_act = QAction(tool_icon("paste"), "Paste", self)
        self.paste_act.setShortcut(QKeySequence.Paste)
        self.paste_act.setToolTip("Paste copied items into this apparatus (Ctrl+V)")
        self.paste_act.triggered.connect(lambda: self.paste())
        self.undo_act = QAction(tool_icon("undo"), "Undo", self)
        self.undo_act.setShortcut(QKeySequence.Undo)
        self.undo_act.setToolTip("Undo (Ctrl+Z)")
        self.undo_act.triggered.connect(self.undo)
        self.redo_act = QAction(tool_icon("redo"), "Redo", self)
        self.redo_act.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        self.redo_act.setToolTip("Redo (Ctrl+Shift+Z)")
        self.redo_act.triggered.connect(self.redo)
        self.fit_act = QAction(tool_icon("fit"), "Fit", self)
        self.fit_act.setShortcut(QKeySequence("F"))
        self.fit_act.setToolTip("Fit the image to the window [F]")
        self.fit_act.triggered.connect(self.view.fit)
        self.labels_act = QAction("Labels", self)
        self.labels_act.setCheckable(True)
        self.labels_act.setChecked(True)
        self.labels_act.setToolTip("Show zone / point / line names on the image")
        self.labels_act.toggled.connect(self._toggle_labels)
        self.labels_act.setIcon(self._labels_icon())
        for a in (self.copy_act, self.paste_act):
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.addAction(a)
        for a in (self.undo_act, self.redo_act, self.fit_act, self.labels_act):
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            tb.addAction(a)
            self.addAction(a)
            b = tb.widgetForAction(a)
            if isinstance(b, QToolButton):
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        lay.addWidget(tb)
        lay.addWidget(self.view, 1)
        sb = QHBoxLayout()
        self.hint = QLabel(HINTS["select"])
        self.hint.setStyleSheet("color:palette(placeholder-text);")
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.measure = QLabel()
        self.coords = QLabel()
        self.coords.setMinimumWidth(130)
        self.coords.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sb.addWidget(self.hint, 1)
        sb.addWidget(self.measure)
        sb.addWidget(self.coords)
        lay.addLayout(sb)
        return w

    @staticmethod
    def _labels_icon() -> QIcon:
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(ACCENT), 1.8))
        p.setBrush(QColor(225, 29, 72, 55))
        p.drawRoundedRect(QRectF(3, 9, 26, 14), 3, 3)
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(3, 9, 26, 14), Qt.AlignCenter, "Abc")
        p.end()
        return QIcon(pm)

    def _build_right(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(318)
        self.tabs.setMaximumWidth(400)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setStyleSheet("QTabBar::tab{padding:4px 5px;}")
        self.tabs.currentChanged.connect(self._tab_changed)

        # ---- zones
        zw = QWidget()
        zl = QVBoxLayout(zw)
        self.zone_list = QListWidget()
        self.zone_list.currentRowChanged.connect(lambda r: self._list_row_changed("zone", r))
        zl.addWidget(self.zone_list, 1)
        f = compact_form()
        self.zone_name = QLineEdit()
        self.zone_name.editingFinished.connect(lambda: self.rename_zone(self.zone_list.currentRow(),
                                                                        self.zone_name.text()))
        self.zone_color = ColorButton("Zone colour")
        self.zone_color.color_changed.connect(lambda c: self.set_item_color("zone", self.zone_list.currentRow(), c))
        self.zone_info = QLabel("—")
        self.zone_info.setWordWrap(True)
        f.addRow("Name", self.zone_name)
        f.addRow("Colour", self.zone_color)
        f.addRow("Shape", self.zone_info)
        self.zone_rule = QComboBox()
        for k, lbl in ENTRY_RULES.items():
            self.zone_rule.addItem(lbl, k)
        self.zone_rule.setToolTip("When is the animal in this zone: by its centre, head or tail base, when a "
                                  "proportion of its body is inside, or when it is in no other zone")
        self.zone_rule.currentIndexChanged.connect(
            lambda _: self.set_zone_property(self.zone_list.currentRow(), "entry_rule", self.zone_rule.currentData()))
        self.zone_frac = QSpinBox()
        self.zone_frac.setRange(1, 100)
        self.zone_frac.setSuffix(" % of body")
        self.zone_frac.valueChanged.connect(
            lambda v: self.set_zone_property(self.zone_list.currentRow(), "body_fraction", v / 100.0))
        self.zone_inv = QDoubleSpinBox()
        self.zone_inv.setRange(0, 10000)
        self.zone_inv.setDecimals(1)
        self.zone_inv.setSpecialValueText("Off")
        self.zone_inv.setToolTip("Investigation zone: the animal is in the zone while its head is within this "
                                 "distance of it (e.g. sniffing an object)")
        self.zone_inv.valueChanged.connect(
            lambda v: self.set_zone_property(self.zone_list.currentRow(), "investigation_distance_cm", float(v)))
        self.zone_hidden = QCheckBox("Hidden zone")
        self.zone_hidden.setToolTip("The animal cannot be seen in it (nest box, tunnel): when it disappears in or "
                                    "near this zone it is counted as in the zone rather than lost")
        self.zone_hidden.toggled.connect(
            lambda on: self.set_zone_property(self.zone_list.currentRow(), "hidden", bool(on)))
        self.zone_moveable = QCheckBox("Moveable")
        self.zone_moveable.setToolTip("The position can differ in each test (e.g. a water-maze platform); set it "
                                      "per test in the test view")
        self.zone_moveable.toggled.connect(
            lambda on: self.set_zone_property(self.zone_list.currentRow(), "moveable", bool(on)))
        flags = QHBoxLayout()
        flags.addWidget(self.zone_hidden)
        flags.addWidget(self.zone_moveable)
        flags.addStretch()
        f.addRow("Entry", self.zone_rule)
        f.addRow("", self.zone_frac)
        f.addRow("Investigate", self.zone_inv)
        f.addRow("", flags)
        zl.addLayout(f)
        zr = QHBoxLayout()
        self.btn_zone_dup = QPushButton("Duplicate")
        self.btn_zone_dup.clicked.connect(lambda: self.duplicate_zone(self.zone_list.currentRow()))
        self.btn_zone_arena = QPushButton("Use as arena")
        self.btn_zone_arena.setToolTip("Copy this zone's outline to the arena boundary")
        self.btn_zone_arena.clicked.connect(lambda: self.zone_to_arena(self.zone_list.currentRow()))
        self.btn_zone_del = QPushButton("Delete")
        self.btn_zone_del.clicked.connect(lambda: self.delete_item("zone", self.zone_list.currentRow()))
        self.btn_grid_del = QPushButton("Delete grid")
        self.btn_grid_del.setToolTip("Delete the whole grid this zone belongs to")
        self.btn_grid_del.clicked.connect(lambda: self.delete_grid_of(self.zone_list.currentRow()))
        for b in (self.btn_zone_dup, self.btn_zone_arena, self.btn_zone_del, self.btn_grid_del):
            zr.addWidget(b)
        zl.addLayout(zr)
        arena_box = QGroupBox("Arena boundary")
        abl = QVBoxLayout(arena_box)
        self.arena_info = QLabel()
        self.arena_info.setWordWrap(True)
        abl.addWidget(self.arena_info)
        ar = QHBoxLayout()
        ar.addStretch()
        self.btn_arena_sel = QPushButton("Select")
        self.btn_arena_sel.setToolTip("Select the arena boundary on the image")
        self.btn_arena_sel.clicked.connect(lambda: self.select_item("arena", 0))
        self.btn_arena_clear = QPushButton("Remove")
        self.btn_arena_clear.clicked.connect(lambda: self.delete_item("arena", 0))
        ar.addWidget(self.btn_arena_sel)
        ar.addWidget(self.btn_arena_clear)
        abl.addLayout(ar)
        zl.addWidget(arena_box)
        self.tabs.addTab(zw, "Zones")

        # ---- points
        pw = QWidget()
        pl = QVBoxLayout(pw)
        self.point_list = QListWidget()
        self.point_list.currentRowChanged.connect(lambda r: self._list_row_changed("point", r))
        pl.addWidget(self.point_list, 1)
        f = compact_form()
        self.point_name = QLineEdit()
        self.point_name.editingFinished.connect(lambda: self.rename_point(self.point_list.currentRow(),
                                                                          self.point_name.text()))
        self.point_color = ColorButton("Point colour")
        self.point_color.color_changed.connect(lambda c: self.set_item_color("point", self.point_list.currentRow(), c))
        self.point_radius = QDoubleSpinBox()
        self.point_radius.setRange(0, 1000)
        self.point_radius.setDecimals(1)
        self.point_radius.setSuffix(" cm")
        self.point_radius.setToolTip("Distance counted as 'near' the point (object exploration, platform proximity)")
        self.point_radius.valueChanged.connect(self._point_radius_changed)
        self.point_x = QDoubleSpinBox()
        self.point_y = QDoubleSpinBox()
        for s in (self.point_x, self.point_y):
            s.setRange(-100000, 100000)
            s.setDecimals(1)
            s.setSuffix(" px")
            s.valueChanged.connect(self._point_xy_changed)
        f.addRow("Name", self.point_name)
        f.addRow("Colour", self.point_color)
        f.addRow("Radius", self.point_radius)
        f.addRow("X", self.point_x)
        f.addRow("Y", self.point_y)
        pl.addLayout(f)
        self.btn_point_del = QPushButton("Delete point")
        self.btn_point_del.clicked.connect(lambda: self.delete_item("point", self.point_list.currentRow()))
        pl.addWidget(self.btn_point_del)
        self.tabs.addTab(pw, "Points")

        # ---- lines
        lw = QWidget()
        ll = QVBoxLayout(lw)
        self.line_list = QListWidget()
        self.line_list.currentRowChanged.connect(lambda r: self._list_row_changed("line", r))
        ll.addWidget(self.line_list, 1)
        f = compact_form()
        self.line_name = QLineEdit()
        self.line_name.editingFinished.connect(lambda: self.rename_line(self.line_list.currentRow(),
                                                                        self.line_name.text()))
        self.line_color = ColorButton("Line colour")
        self.line_color.color_changed.connect(lambda c: self.set_item_color("line", self.line_list.currentRow(), c))
        self.line_info = QLabel("—")
        f.addRow("Name", self.line_name)
        f.addRow("Colour", self.line_color)
        f.addRow("Length", self.line_info)
        ll.addLayout(f)
        self.btn_line_del = QPushButton("Delete line")
        self.btn_line_del.clicked.connect(lambda: self.delete_item("line", self.line_list.currentRow()))
        ll.addWidget(self.btn_line_del)
        self.tabs.addTab(lw, "Lines")

        # ---- groups
        gw = QWidget()
        gl = QVBoxLayout(gw)
        self.group_list = QListWidget()
        self.group_list.setMaximumHeight(130)
        self.group_list.currentRowChanged.connect(self._group_row_changed)
        gl.addWidget(self.group_list)
        gr = QHBoxLayout()
        self.btn_group_add = QPushButton("Add group")
        self.btn_group_add.clicked.connect(lambda: self.add_group())
        self.btn_group_del = QPushButton("Delete")
        self.btn_group_del.clicked.connect(lambda: self.delete_group(self.group_list.currentRow()))
        gr.addWidget(self.btn_group_add)
        gr.addWidget(self.btn_group_del)
        gl.addLayout(gr)
        f = compact_form()
        self.group_name = QLineEdit()
        self.group_name.editingFinished.connect(lambda: self.rename_group(self.group_list.currentRow(),
                                                                         self.group_name.text()))
        f.addRow("Name", self.group_name)
        gl.addLayout(f)
        gl.addWidget(QLabel("Zones included:"))
        self.group_inc = QListWidget()
        self.group_inc.itemChanged.connect(self._group_members_changed)
        gl.addWidget(self.group_inc, 1)
        gl.addWidget(QLabel("Minus these zones:"))
        self.group_exc = QListWidget()
        self.group_exc.itemChanged.connect(self._group_members_changed)
        gl.addWidget(self.group_exc, 1)
        self.tabs.addTab(gw, "Groups")

        # ---- sequences
        sw = QWidget()
        sl = QVBoxLayout(sw)
        self.seq_list = QListWidget()
        self.seq_list.setMaximumHeight(110)
        self.seq_list.currentRowChanged.connect(self._seq_row_changed)
        sl.addWidget(self.seq_list)
        sr = QHBoxLayout()
        self.btn_seq_add = QPushButton("Add sequence")
        self.btn_seq_add.clicked.connect(lambda: self.add_sequence())
        self.btn_seq_del = QPushButton("Delete")
        self.btn_seq_del.clicked.connect(lambda: self.delete_sequence(self.seq_list.currentRow()))
        sr.addWidget(self.btn_seq_add)
        sr.addWidget(self.btn_seq_del)
        sl.addLayout(sr)
        f = compact_form()
        self.seq_name = QLineEdit()
        self.seq_name.editingFinished.connect(lambda: self.rename_sequence(self.seq_list.currentRow(),
                                                                          self.seq_name.text()))
        f.addRow("Name", self.seq_name)
        sl.addLayout(f)
        sl.addWidget(QLabel("Steps (zones in order):"))
        self.seq_steps = QListWidget()
        sl.addWidget(self.seq_steps, 1)
        st = QHBoxLayout()
        self.seq_zone = QComboBox()
        self.seq_zone.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.seq_zone.setMinimumContentsLength(8)
        self.btn_step_add = QPushButton("Add")
        self.btn_step_add.clicked.connect(lambda: self.add_sequence_step(self.seq_list.currentRow(),
                                                                         self.seq_zone.currentText()))
        st.addWidget(self.seq_zone, 1)
        st.addWidget(self.btn_step_add)
        sl.addLayout(st)
        st2 = QHBoxLayout()
        self.btn_step_up = QPushButton("Up")
        self.btn_step_up.clicked.connect(lambda: self.move_sequence_step(self.seq_list.currentRow(),
                                                                         self.seq_steps.currentRow(), -1))
        self.btn_step_down = QPushButton("Down")
        self.btn_step_down.clicked.connect(lambda: self.move_sequence_step(self.seq_list.currentRow(),
                                                                           self.seq_steps.currentRow(), 1))
        self.btn_step_del = QPushButton("Remove")
        self.btn_step_del.clicked.connect(lambda: self.remove_sequence_step(self.seq_list.currentRow(),
                                                                            self.seq_steps.currentRow()))
        for b in (self.btn_step_up, self.btn_step_down, self.btn_step_del):
            st2.addWidget(b)
        sl.addLayout(st2)
        self.seq_from_start = QCheckBox("Must begin at the first step")
        self.seq_from_start.setToolTip("Unchecked: any rotation of the steps counts (e.g. ABC, BCA, CAB)")
        self.seq_allow_other = QCheckBox("Other zones allowed between steps")
        self.seq_bidir = QCheckBox("Both directions")
        self.seq_overlap = QCheckBox("Sequences may overlap")
        self.seq_overlap.setToolTip("Sliding window: A B C A contains A B C and B C A")
        for w, attr in ((self.seq_from_start, "from_start"), (self.seq_allow_other, "allow_other"),
                        (self.seq_bidir, "bidirectional"), (self.seq_overlap, "overlap")):
            w.toggled.connect(lambda on, a=attr: self.set_sequence_option(self.seq_list.currentRow(), a, bool(on)))
            sl.addWidget(w)
        f = compact_form()
        self.seq_end = QComboBox()
        for k, lbl in SEQ_END.items():
            self.seq_end.addItem(lbl, k)
        self.seq_end.currentIndexChanged.connect(
            lambda _: self.set_sequence_option(self.seq_list.currentRow(), "end", self.seq_end.currentData()))
        self.seq_max = QDoubleSpinBox()
        self.seq_max.setRange(0, 1e6)
        self.seq_max.setDecimals(1)
        self.seq_max.setSuffix(" s")
        self.seq_max.setSpecialValueText("No limit")
        self.seq_max.valueChanged.connect(
            lambda v: self.set_sequence_option(self.seq_list.currentRow(), "max_duration_s", float(v)))
        f.addRow("Complete", self.seq_end)
        f.addRow("Time limit", self.seq_max)
        sl.addLayout(f)
        self.tabs.addTab(sw, "Sequences")
        return self.tabs

    # ============================================================ page API
    @property
    def app(self) -> Apparatus | None:
        p = self.project
        r = self.app_list.currentRow()
        if p is None or not (0 <= r < len(p.apparatus)):
            return None
        return p.apparatus[r]

    def set_project(self, project):
        self._close_bg()
        self._bg_memory.clear()
        self._undo.clear()
        self._redo.clear()
        self._pending_template = None
        self.view.set_tool("select")
        self.tool_actions["select"].setChecked(True)
        self._refresh_app_list(select_row=0)
        self._refresh_test_combo()

    def on_show(self):
        if self.project is None:
            return
        cur = self.app
        self._refresh_app_list(select=cur)
        self._refresh_test_combo()
        self.view.setFocus()

    def on_hide(self):
        self.commit()
        self.view.cancel_drawing()

    def commit(self):
        """Flush half-edited names (e.g. when saving while a name field has focus)."""
        if self.app is None or self._loading:
            return
        for lst, edit, fn in ((self.zone_list, self.zone_name, self.rename_zone),
                              (self.point_list, self.point_name, self.rename_point),
                              (self.line_list, self.line_name, self.rename_line),
                              (self.group_list, self.group_name, self.rename_group),
                              (self.seq_list, self.seq_name, self.rename_sequence)):
            r = lst.currentRow()
            it = lst.item(r) if r >= 0 else None
            if it is not None and edit.text().strip() and edit.text().strip() != it.text():
                fn(r, edit.text())

    def shutdown(self):
        self._close_bg()

    # ========================================================= apparatus list
    def _refresh_app_list(self, select: Apparatus | None = None, select_row: int | None = None):
        p = self.project
        self._loading = True
        self.app_list.clear()
        row = -1
        if p is not None:
            for i, a in enumerate(p.apparatus):
                it = QListWidgetItem(a.name)
                n = sum(1 for t in p.tests if t.apparatus == a.name)
                info = TEMPLATES.get(a.template)
                it.setToolTip(f"{info.title if info else a.template} · {n} test(s)")
                self.app_list.addItem(it)
                if a is select:
                    row = i
            if row < 0 and p.apparatus:
                row = min(select_row or 0, len(p.apparatus) - 1)
        self.app_list.setCurrentRow(row)
        self._loading = False
        self._app_selected()

    def _app_row_changed(self, _row):
        if not self._loading:
            self._app_selected()

    def _app_selected(self):
        app = self.app
        self.view.cancel_drawing()
        self._set_enabled(app is not None)
        self._show_background()
        self.rebuild_canvas(keep_selection=False)
        self._refresh_side_lists()
        self._refresh_info()

    def _set_enabled(self, on: bool):
        for a in list(self.tool_actions.values()) + [self.undo_act, self.redo_act, self.grid_act, self.copy_act,
                                                     self.paste_act]:
            a.setEnabled(on)
        for w in (self.tabs, self.btn_dup, self.btn_ren, self.btn_del, self.btn_bg, self.test_combo,
                  self.time_slider, self.time_spin, self.ppc_spin, self.btn_cal, self.btn_cal_clear):
            w.setEnabled(on)
        self.btn_add.setEnabled(self.project is not None)
        self.btn_tpl.setEnabled(self.project is not None)

    def _names(self, exclude: Apparatus | None = None):
        return [a.name for a in self.project.apparatus if a is not exclude]

    def add_apparatus(self, name: str | None = None) -> Apparatus:
        cur = self.app
        app = Apparatus(name=unique_name(name or f"Apparatus {len(self.project.apparatus) + 1}", self._names()))
        app.frame_size = (cur.frame_size if cur else None) or self._real_frame_size()
        self._inherit_background(cur, app)
        self.project.apparatus.append(app)
        self._changed()
        self._refresh_app_list(select=app)
        return app

    def duplicate_apparatus(self) -> Apparatus | None:
        cur = self.app
        if cur is None:
            return None
        app = cur.copy()
        app.name = unique_name(f"{cur.name} copy", self._names())
        self._inherit_background(cur, app)
        self.project.apparatus.insert(self.project.apparatus.index(cur) + 1, app)
        self._changed()
        self._refresh_app_list(select=app)
        return app

    def rename_apparatus(self, new_name: str | None = None) -> bool:
        app = self.app
        if app is None:
            return False
        if new_name is None:
            new_name, ok = QInputDialog.getText(self, "Rename apparatus", "Name:", QLineEdit.Normal, app.name)
            if not ok:
                return False
        if not self._rename_apparatus(app, new_name):
            return False
        self._refresh_app_list(select=app)
        return True

    def _rename_apparatus(self, app: Apparatus, new_name: str) -> bool:
        new_name = new_name.strip()
        if not new_name or new_name == app.name:
            return False
        if new_name in self._names(exclude=app):
            QMessageBox.warning(self, "Rename apparatus", f"An apparatus called “{new_name}” already exists.")
            return False
        old = app.name
        n = 0
        for t in self.project.tests:
            if t.apparatus == old:
                t.apparatus = new_name
                n += 1
        app.name = new_name
        self._changed()
        if n:
            self.main.status(f"Renamed “{old}” to “{new_name}” and updated {n} test(s)")
        return True

    def delete_apparatus(self, confirm: bool = True) -> bool:
        app = self.app
        if app is None:
            return False
        p = self.project
        users = [t for t in p.tests if t.apparatus == app.name]
        others = [a for a in p.apparatus if a is not app]
        if confirm:
            msg = f"Delete the apparatus “{app.name}”?"
            if users:
                msg += (f"\n\n{len(users)} test(s) use it; they will be assigned to "
                        f"“{others[0].name}”." if others else
                        f"\n\n{len(users)} test(s) use it and will have no apparatus.")
            if QMessageBox.question(self, "Delete apparatus", msg) != QMessageBox.Yes:
                return False
        row = p.apparatus.index(app)
        for t in users:
            t.apparatus = others[0].name if others else ""
        p.apparatus.remove(app)
        self._undo.pop(id(app), None)
        self._redo.pop(id(app), None)
        self._bg_memory.pop(id(app), None)
        self._changed()
        self._refresh_app_list(select_row=max(0, row - 1))
        return True

    def _refresh_info(self):
        app = self.app
        if app is None:
            self.app_info.setText("No apparatus yet. Create one from a template (recommended) or add an empty "
                                  "one and draw the zones yourself." if self.project else "")
            self.cal_label.setText("")
            self.arena_info.setText("")
            self._update_tab_titles()
            return
        info = TEMPLATES.get(app.template)
        n = sum(1 for t in self.project.tests if t.apparatus == app.name)
        fs = f"{app.frame_size[0]}×{app.frame_size[1]} px" if app.frame_size else "frame size unknown"
        self.app_info.setText(f"Template: {info.title if info else app.template}<br>"
                              f"{len(app.zones)} zones · {len(app.points)} points · {len(app.lines)} lines<br>"
                              f"Used by {n} test(s) · {fs}")
        self.arena_info.setText(describe_shape(app.arena, app) if app.arena is not None else
                                "Not set — draw it with the Arena tool. Without an arena the tracker searches "
                                "the whole frame.")
        self.btn_arena_sel.setEnabled(app.arena is not None)
        self.btn_arena_clear.setEnabled(app.arena is not None)
        self._refresh_calibration()
        self._update_tab_titles()

    def _refresh_calibration(self):
        app = self.app
        self._loading, was = True, self._loading
        if app is not None and app.px_per_cm:
            extra = ""
            if app.calibration_line and app.calibration_length_cm:
                extra = f"<br><span style='color:gray'>from a {app.calibration_length_cm:g} cm line</span>"
            elif app.calibration_length_cm:
                extra = f"<br><span style='color:gray'>from template ({app.calibration_length_cm:g} cm wide)</span>"
            self.cal_label.setText(f"<b>1 cm = {app.px_per_cm:.2f} px</b>{extra}")
            self.ppc_spin.setValue(app.px_per_cm)
        else:
            self.cal_label.setText("<b style='color:#d97706'>Not calibrated</b> (results in pixels)")
            self.ppc_spin.setValue(0)
        self._loading = was

    def _update_tab_titles(self):
        app = self.app
        for i, (lbl, lst) in enumerate((("Zones", "zones"), ("Points", "points"), ("Lines", "lines"),
                                        ("Groups", "groups"), ("Sequences", "sequences"))):
            n = len(getattr(app, lst)) if app else 0
            self.tabs.setTabText(i, f"{lbl} {n}" if n else lbl)

    # ============================================================ background
    def _real_frame_size(self):
        return self.view.frame_size if self._bg_real else None

    def _inherit_background(self, src: Apparatus | None, dst: Apparatus):
        if src is not None and id(src) in self._bg_memory:
            self._bg_memory[id(dst)] = self._bg_memory[id(src)]

    def _close_bg(self):
        if self._bg_src is not None:
            self._bg_src.release()
        self._bg_src = None
        self._bg_path = None
        self._bg_real = False

    def _show_background(self):
        app = self.app
        p = self.project
        if app is not None:
            mem = self._bg_memory.get(id(app))
            if mem and self.load_background(mem[0], mem[1], quiet=True):
                return
            for t in p.tests:
                if t.apparatus == app.name and t.video:
                    path = p.abs_path(t.video)
                    if Path(path).exists() and self.load_background(path, t.start_s, quiet=True):
                        return
        self._close_bg()
        w, h = (app.frame_size if app is not None and app.frame_size else (640, 480))
        self.view.set_frame(placeholder_frame(int(w), int(h)))
        self._loading, was = True, self._loading
        self.time_slider.setRange(0, 0)
        self.time_spin.setRange(0, 0)
        self._loading = was
        self.bg_label.setText("No background image." if app is None or not app.frame_size else
                              f"No background image ({w}×{h} px).")

    def load_background(self, path: str, t: float = 0.0, quiet: bool = False) -> bool:
        """Show the frame at time t of a video as the background for the current apparatus."""
        try:
            if self._bg_src is None or self._bg_path != path:
                self._close_bg()
                self._bg_src = VideoSource(path)
                self._bg_path = path
            src = self._bg_src
            idx = int(round(max(0.0, t) * src.fps))
            if src.frame_count:
                idx = min(idx, src.frame_count - 1)
            f = src.frame_at(idx)
            if f is None:
                idx = 0
                f = src.frame_at(0)
            if f is None:
                raise IOError("cannot read a frame from this video")
        except Exception as e:
            self._close_bg()
            msg = f"Cannot load background from {Path(path).name}: {e}"
            if quiet:
                self.main.status(msg)
            else:
                QMessageBox.warning(self, "Background image", msg)
            return False
        self._bg_real = True
        self.view.set_frame(f)
        h, w = f.shape[:2]
        app = self.app
        tt = idx / src.fps if src.fps else 0.0
        if app is not None:
            self._bg_memory[id(app)] = (path, tt)
            if tuple(app.frame_size or ()) != (w, h):
                app.frame_size = (w, h)
                self._changed()
                self._refresh_info()
        self._loading, was = True, self._loading
        self.time_slider.setRange(0, max(0, src.frame_count - 1))
        self.time_slider.setValue(idx)
        self.time_spin.setRange(0, max(0.0, src.duration))
        self.time_spin.setValue(tt)
        self._loading = was
        self.bg_label.setText(f"{Path(path).name} · {fmt_time(tt)} · {w}×{h} px")
        self.bg_label.setToolTip(path)
        return True

    def load_background_dialog(self):
        start = str(self.project.path) if self.project and self.project.path else str(Path.home())
        if self._bg_path:
            start = str(Path(self._bg_path).parent)
        pats = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a video for the background image", start,
                                              f"Videos ({pats});;All files (*)")
        if path:
            self.load_background(path, 0.0)

    def _refresh_test_combo(self):
        self.test_combo.clear()
        self.test_combo.addItem("Use a test's video…", None)
        p = self.project
        if p is None:
            return
        for t in p.tests:
            if t.video:
                self.test_combo.addItem(f"Test {t.id}: {t.animal_id or '—'} · {Path(t.video).name}", t.id)
        self.test_combo.setEnabled(self.app is not None and self.test_combo.count() > 1)

    def _test_combo_activated(self, i: int):
        tid = self.test_combo.itemData(i)
        self.test_combo.setCurrentIndex(0)
        if tid is None or self.project is None:
            return
        self.use_test_video(tid)

    def use_test_video(self, test_id: int) -> bool:
        t = self.project.get_test(test_id)
        if t is None or not t.video:
            return False
        return self.load_background(self.project.abs_path(t.video), t.start_s)

    def _slider_changed(self, v):
        if self._loading or self._bg_src is None:
            return
        self._loading = True
        self.time_spin.setValue(v / self._bg_src.fps)
        self._loading = False
        self._bg_timer.start()

    def _spin_changed(self, v):
        if self._loading or self._bg_src is None:
            return
        self._loading = True
        self.time_slider.setValue(int(round(v * self._bg_src.fps)))
        self._loading = False
        self._bg_timer.start()

    def _seek_background(self):
        if self._bg_path:
            self.load_background(self._bg_path, self.time_spin.value(), quiet=True)

    # ================================================================ canvas
    def rebuild_canvas(self, keep_selection: bool = True):
        sel = self.selected_key() if keep_selection else None
        sc = self.view.scene()
        self._syncing = True
        if self._root is not None:
            sc.removeItem(self._root)
        self._root = QGraphicsPathItem()
        self._root.setZValue(0)
        sc.addItem(self._root)
        self._items = {}
        app = self.app
        if app is not None:
            if app.arena is not None:
                it = ShapeItem(self, "arena", 0, self._root)
                it.setZValue(1)
                self._items[("arena", 0)] = it
            order = sorted(range(len(app.zones)), key=lambda i: -app.zones[i].shape.area())
            for rank, i in enumerate(order):
                it = ShapeItem(self, "zone", i, self._root)
                it.setZValue(2 + rank * 0.01)
                self._items[("zone", i)] = it
            self._declutter_labels(order)
            for i in range(len(app.lines)):
                it = LineItem(self, i, self._root)
                it.setZValue(40)
                self._items[("line", i)] = it
            for i in range(len(app.points)):
                it = PointItem(self, i, self._root)
                it.setZValue(50)
                self._items[("point", i)] = it
            if app.calibration_line:
                x1, y1, x2, y2 = app.calibration_line
                ln = QGraphicsLineItem(x1, y1, x2, y2, self._root)
                pen = QPen(QColor("#facc15"), 1.5, Qt.DashDotLine)
                pen.setCosmetic(True)
                ln.setPen(pen)
                ln.setZValue(45)
                ln.setAcceptedMouseButtons(Qt.NoButton)
                if app.calibration_length_cm:
                    lb = Label(f"{app.calibration_length_cm:g} cm", self._root, "#facc15")
                    lb.setPos((x1 + x2) / 2, (y1 + y2) / 2)
                    lb.setZValue(46)
                    lb.setVisible(self.show_labels)
        if sel is not None and sel in self._items:
            self._items[sel].setSelected(True)
        self._syncing = False
        self._seq_overlay = None
        self._draw_sequence_overlay()

    def _step_centre(self, name: str):
        app = self.app
        z = app.zone(name)
        if z is not None:
            return z.shape.centroid()
        g = app.group(name)
        pts = [app.zone(n).shape.centroid() for n in (g.zones if g else []) if app.zone(n) is not None]
        if not pts:
            return None
        return float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))

    def _draw_sequence_overlay(self):
        """Numbered arrows between the steps of the selected sequence (while the Sequences tab is shown)."""
        if self._seq_overlay is not None and self._seq_overlay.scene() is not None:
            self.view.scene().removeItem(self._seq_overlay)
        self._seq_overlay = None
        app = self.app
        r = self.seq_list.currentRow()
        if app is None or self._root is None or self.tabs.currentIndex() != 4 or not (0 <= r < len(app.sequences)):
            return
        q = app.sequences[r]
        cents = [c for c in (self._step_centre(n) for n in q.steps) if c is not None]
        root = QGraphicsPathItem(self._root)
        root.setZValue(60)
        root.setAcceptedMouseButtons(Qt.NoButton)
        self._seq_overlay = root
        col = QColor("#facc15")
        path = QPainterPath()
        L = max(8.0, 0.025 * max(self.view.frame_size or (640, 480)))
        for (x0, y0), (x1, y1) in zip(cents, cents[1:]):
            d = math.hypot(x1 - x0, y1 - y0)
            if d < 1e-6:
                continue
            ux, uy = (x1 - x0) / d, (y1 - y0) / d
            sh = min(0.18 * d, 2.2 * L)
            a, b = QPointF(x0 + ux * sh, y0 + uy * sh), QPointF(x1 - ux * sh, y1 - uy * sh)
            path.moveTo(a)
            path.lineTo(b)
            for sgn in (1, -1):
                path.moveTo(b)
                path.lineTo(b.x() - L * ux + sgn * 0.5 * L * uy, b.y() - L * uy - sgn * 0.5 * L * ux)
        root.setPath(path)
        pen = QPen(col, 3)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.RoundCap)
        root.setPen(pen)
        for i, (x, y) in enumerate(cents):
            lb = Label(f"{i + 1}", root, "#facc15")
            lb.setPos(x, y)
            lb.setZValue(61)
        if q.bidirectional and len(cents) > 1:
            lb = Label("both directions", root, "#facc15", anchor="right")
            lb.setPos(*cents[0])

    def _declutter_labels(self, order):
        """Zones sharing a centre (e.g. Arena around Centre) get their label at the top edge instead."""
        tol = 18.0 / (self.view.transform().m11() or 1.0)
        placed = []
        for i in reversed(order):  # smallest first
            it = self._items[("zone", i)]
            c = it.get_shape().centroid()
            clash = any(math.hypot(c[0] - q[0], c[1] - q[1]) < tol for q in placed)
            if clash != it.label_at_top:
                it.label_at_top = clash
                it.sync()
            placed.append(c if not clash else (it.label.pos().x(), it.label.pos().y()))

    def selected_key(self) -> tuple[str, int] | None:
        for k, it in self._items.items():
            if it.isSelected():
                return k
        return None

    def item(self, kind: str, index: int = 0):
        return self._items.get((kind, index))

    def select_item(self, kind: str, index: int):
        it = self._items.get((kind, index))
        self._syncing = True
        self.view.scene().clearSelection()
        if it is not None:
            it.setSelected(True)
            self.view.ensureVisible(it.sceneBoundingRect(), 20, 20)
        self._syncing = False
        self._sync_lists_from_canvas()

    def _toggle_labels(self, on: bool):
        self.show_labels = on
        self.rebuild_canvas()

    def set_tool(self, tool: str):
        if tool != "template":
            self._pending_template = None
        if self.app is None and tool not in ("select", "template"):
            tool = "select"
        self.view.set_tool(tool)
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        else:
            for a in self.tool_actions.values():
                a.setChecked(False)
        hint = HINTS.get(tool, "")
        if tool == "arena":
            hint = {"rect": "Drag a rectangle for the arena boundary",
                    "ellipse": "Drag an ellipse for the arena boundary (Shift = circle)",
                    "polygon": "Click the vertices of the arena boundary; double-click or Enter closes it"}[
                self.arena_shape]
        self.hint.setText(hint)
        self.view.setFocus()

    def set_arena_shape(self, shape: str):
        self.arena_shape = shape
        self.arena_actions[shape].setChecked(True)
        self.set_tool("arena")

    def show_measure(self, text: str):
        self.measure.setText(text)

    def _on_mouse_moved(self, x, y):
        self.coords.setText(f"x {x:.0f}  y {y:.0f} px")

    # ---- creation (called by the view's tools) ----------------------------------
    def finish_drag(self, x0, y0, x1, y1, square: bool = False):
        """A drag of the current tool finished: create the corresponding item."""
        tool = self.view.tool
        kind = self.view.drag_kind()
        if tool == "template":
            if square or self.template_square():
                p1 = EditorView.constrain(QPointF(x0, y0), QPointF(x1, y1), True)
                x1, y1 = p1.x(), p1.y()
            spec = self._pending_template
            self._pending_template = None
            self.set_tool("select")
            if spec:
                self.apply_template(spec["key"], min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0),
                                    spec["params"], spec["name"], spec["replace"])
            return
        if tool in ("line", "calibrate"):
            if tool == "line":
                self.add_line(x0, y0, x1, y1)
            else:
                self.set_tool("select")
                self.calibrate_from_line(x0, y0, x1, y1)
            return
        if square:
            p1 = EditorView.constrain(QPointF(x0, y0), QPointF(x1, y1), True)
            x1, y1 = p1.x(), p1.y()
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        if kind == "ellipse":
            shape = Ellipse((xa + xb) / 2, (ya + yb) / 2, (xb - xa) / 2, (yb - ya) / 2)
        else:
            shape = Polygon([(xa, ya), (xb, ya), (xb, yb), (xa, yb)])
        if tool == "arena":
            self.set_arena(shape)
        else:
            self.add_zone(shape)

    def finish_polygon(self, points):
        shape = Polygon([(float(x), float(y)) for x, y in points])
        if self.view.tool == "arena":
            self.set_arena(shape)
        else:
            self.add_zone(shape)

    def add_zone(self, shape: Shape, name: str | None = None, color: str | None = None) -> Zone | None:
        app = self.app
        if app is None:
            return None
        self.push_undo()
        taken = [z.name for z in app.zones] + [g.name for g in app.groups]
        z = Zone(unique_name(name or f"Zone {len(app.zones) + 1}", taken), shape,
                 color or PALETTE[len(app.zones) % len(PALETTE)])
        app.zones.append(z)
        self._model_changed(select=("zone", len(app.zones) - 1))
        return z

    def set_arena(self, shape: Shape):
        app = self.app
        if app is None:
            return
        self.push_undo()
        app.arena = shape
        self.set_tool("select")
        self._model_changed(select=("arena", 0))
        self.main.status("Arena boundary set")

    def add_point(self, x: float, y: float, name: str | None = None) -> PointOfInterest | None:
        app = self.app
        if app is None:
            return None
        self.push_undo()
        pt = PointOfInterest(unique_name(name or f"Point {len(app.points) + 1}", [p.name for p in app.points]),
                             float(x), float(y), color=PALETTE[(3 + len(app.points)) % len(PALETTE)])
        app.points.append(pt)
        self._model_changed(select=("point", len(app.points) - 1))
        return pt

    def add_line(self, x1, y1, x2, y2, name: str | None = None) -> Line | None:
        app = self.app
        if app is None:
            return None
        self.push_undo()
        ln = Line(unique_name(name or f"Line {len(app.lines) + 1}", [l.name for l in app.lines]),
                  float(x1), float(y1), float(x2), float(y2), PALETTE[(2 + len(app.lines)) % len(PALETTE)])
        app.lines.append(ln)
        self._model_changed(select=("line", len(app.lines) - 1))
        return ln

    def calibrate_from_line(self, x1, y1, x2, y2, length_cm: float | None = None) -> bool:
        app = self.app
        if app is None:
            return False
        px = math.hypot(x2 - x1, y2 - y1)
        if px < 1:
            return False
        if length_cm is None:
            length_cm, ok = QInputDialog.getDouble(
                self, "Calibrate", f"The line is {px:.1f} px long.\nWhat is its real length (cm)?",
                app.calibration_length_cm or 10.0, 0.01, 1e6, 2)
            if not ok:
                return False
        self.push_undo()
        try:
            app.calibrate(float(x1), float(y1), float(x2), float(y2), float(length_cm))
        except ValueError as e:
            QMessageBox.warning(self, "Calibrate", str(e))
            return False
        self._model_changed()
        self.main.status(f"Calibrated: 1 cm = {app.px_per_cm:.2f} px")
        return True

    def set_px_per_cm(self, v: float):
        app = self.app
        if app is None:
            return
        v = float(v) or None
        if v == app.px_per_cm:
            return
        self.push_undo()
        app.px_per_cm = v
        app.calibration_line = None
        app.calibration_length_cm = None
        self._model_changed()

    def _ppc_edited(self):
        if not self._loading:
            self.set_px_per_cm(self.ppc_spin.value())

    def clear_calibration(self):
        self.set_px_per_cm(0)

    # ---- edits -------------------------------------------------------------------
    def geometry_edited(self, item=None):
        """Called after a vertex drag or other geometry change of an item."""
        self.main.mark_dirty()
        self._refresh_selected_info()
        self._refresh_info()

    def commit_moves(self):
        moved = [it for it in self._items.values() if it.moved()]
        if not moved:
            return
        self.push_undo()
        for it in moved:
            it.commit_pos()
        self.geometry_edited()
        self._load_editor()

    def delete_selected(self) -> bool:
        keys = [k for k, it in self._items.items() if it.isSelected()]
        if not keys or self.app is None:
            return False
        self.push_undo()
        for kind, idx in sorted(keys, key=lambda k: -k[1]):
            self._delete(kind, idx)
        self._model_changed()
        return True

    def delete_item(self, kind: str, index: int) -> bool:
        app = self.app
        if app is None or index < 0:
            return False
        if kind == "arena" and app.arena is None:
            return False
        if kind != "arena" and index >= len(getattr(app, kind + "s")):
            return False
        self.push_undo()
        self._delete(kind, index)
        self._model_changed()
        return True

    def _delete(self, kind: str, idx: int):
        app = self.app
        if kind == "arena":
            app.arena = None
        elif kind == "zone":
            name = app.zones.pop(idx).name
            for g in app.groups:
                g.zones = [z for z in g.zones if z != name]
                g.exclude = [z for z in g.exclude if z != name]
            for q in app.sequences:
                q.steps = [z for z in q.steps if z != name]
            for gr in app.grids:
                gr.zones = [z for z in gr.zones if z != name]
            app.grids = [gr for gr in app.grids if gr.zones]
        elif kind == "point":
            app.points.pop(idx)
        elif kind == "line":
            app.lines.pop(idx)

    def duplicate_zone(self, index: int) -> Zone | None:
        app = self.app
        if app is None or not (0 <= index < len(app.zones)):
            return None
        z = app.zones[index]
        off = 10.0
        return self.add_zone(z.shape.translated(off, off), f"{z.name} copy", z.color)

    def zone_to_arena(self, index: int):
        app = self.app
        if app is None or not (0 <= index < len(app.zones)):
            return
        self.push_undo()
        app.arena = Polygon(list(app.zones[index].shape.points)) if isinstance(app.zones[index].shape, Polygon) \
            else Ellipse(**{k: getattr(app.zones[index].shape, k) for k in ("cx", "cy", "rx", "ry")})
        self._model_changed(select=("zone", index))

    def rename_zone(self, index: int, name: str) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.zones)):
            return False
        old = app.zones[index].name
        name = name.strip()
        if not name or name == old:
            return False
        taken = [z.name for i, z in enumerate(app.zones) if i != index] + [g.name for g in app.groups]
        new = unique_name(name, taken)
        if new != name:
            self.main.status(f"“{name}” is already used; renamed to “{new}”")
        self.push_undo()
        app.zones[index].name = new
        for g in app.groups:
            g.zones = [new if z == old else z for z in g.zones]
            g.exclude = [new if z == old else z for z in g.exclude]
        self._rename_refs(old, new)
        self._model_changed(select=("zone", index))
        return True

    def rename_point(self, index: int, name: str) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.points)) or not name.strip():
            return False
        if name.strip() == app.points[index].name:
            return False
        self.push_undo()
        app.points[index].name = unique_name(name, [p.name for i, p in enumerate(app.points) if i != index])
        self._model_changed(select=("point", index))
        return True

    def rename_line(self, index: int, name: str) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.lines)) or not name.strip():
            return False
        if name.strip() == app.lines[index].name:
            return False
        self.push_undo()
        app.lines[index].name = unique_name(name, [l.name for i, l in enumerate(app.lines) if i != index])
        self._model_changed(select=("line", index))
        return True

    def set_item_color(self, kind: str, index: int, color: str):
        app = self.app
        lst = getattr(app, kind + "s", []) if app else []
        if not (0 <= index < len(lst)):
            return
        self.push_undo()
        lst[index].color = color
        self._model_changed(select=(kind, index))

    def _point_radius_changed(self, v):
        app = self.app
        r = self.point_list.currentRow()
        if self._loading or app is None or not (0 <= r < len(app.points)):
            return
        self.push_undo()
        app.points[r].radius_cm = float(v)
        it = self._items.get(("point", r))
        if it is not None:
            it.sync()
        self.main.mark_dirty()

    def _point_xy_changed(self, *_):
        app = self.app
        r = self.point_list.currentRow()
        if self._loading or app is None or not (0 <= r < len(app.points)):
            return
        self.push_undo()
        app.points[r].x = float(self.point_x.value())
        app.points[r].y = float(self.point_y.value())
        it = self._items.get(("point", r))
        if it is not None:
            it.sync()
        self.main.mark_dirty()

    # ---- groups --------------------------------------------------------------------
    def add_group(self, name: str | None = None, zones=(), exclude=()) -> ZoneGroup | None:
        app = self.app
        if app is None:
            return None
        self.push_undo()
        taken = [z.name for z in app.zones] + [g.name for g in app.groups]
        g = ZoneGroup(unique_name(name or f"Group {len(app.groups) + 1}", taken), list(zones), list(exclude))
        app.groups.append(g)
        self.main.mark_dirty()
        self._refresh_side_lists()
        self.tabs.setCurrentIndex(3)
        self.group_list.setCurrentRow(len(app.groups) - 1)
        self._refresh_info()
        return g

    def delete_group(self, index: int) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.groups)):
            return False
        self.push_undo()
        name = app.groups.pop(index).name
        for q in app.sequences:
            q.steps = [z for z in q.steps if z != name]
        self.main.mark_dirty()
        self._refresh_side_lists()
        self.group_list.setCurrentRow(min(index, len(app.groups) - 1))
        self._refresh_info()
        return True

    def rename_group(self, index: int, name: str) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.groups)) or not name.strip():
            return False
        if name.strip() == app.groups[index].name:
            return False
        self.push_undo()
        taken = [z.name for z in app.zones] + [g.name for i, g in enumerate(app.groups) if i != index]
        old = app.groups[index].name
        app.groups[index].name = unique_name(name, taken)
        self._rename_refs(old, app.groups[index].name)
        self.main.mark_dirty()
        self._refresh_side_lists()
        self.group_list.setCurrentRow(index)
        return True

    def set_group_members(self, index: int, zones, exclude=()):
        app = self.app
        if app is None or not (0 <= index < len(app.groups)):
            return
        self.push_undo()
        order = [z.name for z in app.zones]
        g = app.groups[index]
        g.zones = [n for n in order if n in set(zones)]
        g.exclude = [n for n in order if n in set(exclude)]
        self.main.mark_dirty()
        self._load_group_editor()

    def _group_members_changed(self, _item):
        if self._loading:
            return
        inc = [self.group_inc.item(i).text() for i in range(self.group_inc.count())
               if self.group_inc.item(i).checkState() == Qt.Checked]
        exc = [self.group_exc.item(i).text() for i in range(self.group_exc.count())
               if self.group_exc.item(i).checkState() == Qt.Checked]
        self.set_group_members(self.group_list.currentRow(), inc, exc)

    def _group_row_changed(self, _r):
        if not self._loading:
            self._load_group_editor()

    def _load_group_editor(self):
        app = self.app
        r = self.group_list.currentRow()
        g = app.groups[r] if app is not None and 0 <= r < len(app.groups) else None
        self._loading, was = True, self._loading
        self.group_name.setText(g.name if g else "")
        for w in (self.group_name, self.group_inc, self.group_exc, self.btn_group_del):
            w.setEnabled(g is not None)
        for lst, members in ((self.group_inc, g.zones if g else []), (self.group_exc, g.exclude if g else [])):
            lst.clear()
            if app is None:
                continue
            for z in app.zones:
                it = QListWidgetItem(color_icon(z.color), z.name)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if z.name in members else Qt.Unchecked)
                lst.addItem(it)
        if g is not None:
            it = self.group_list.item(r)
            if it is not None:
                it.setToolTip(self._group_text(g))
        self._loading = was

    @staticmethod
    def _group_text(g: ZoneGroup) -> str:
        s = " + ".join(g.zones) or "(empty)"
        if g.exclude:
            s += " − " + " − ".join(g.exclude)
        return s

    def _rename_refs(self, old: str, new: str):
        app = self.app
        for q in app.sequences:
            q.steps = [new if z == old else z for z in q.steps]
        for gr in app.grids:
            gr.zones = [new if z == old else z for z in gr.zones]

    # ---- zone properties -------------------------------------------------------------
    def set_zone_property(self, index: int, attr: str, value) -> bool:
        """Set a zone flag: hidden, moveable, investigation_distance_cm, entry_rule or body_fraction."""
        app = self.app
        if self._loading or app is None or not (0 <= index < len(app.zones)):
            return False
        z = app.zones[index]
        if getattr(z, attr) == value:
            return False
        self.push_undo()
        setattr(z, attr, value)
        self.main.mark_dirty()
        it = self._items.get(("zone", index))
        if it is not None:
            it.sync()
        self._load_editor()
        return True

    # ---- grids ---------------------------------------------------------------------------
    def add_grid_dialog(self):
        app = self.app
        if app is None:
            return None
        key = self.selected_key()
        dlg = GridDialog(self, app, has_selection=key is not None and key[0] == "zone")
        if dlg.exec() != QDialog.Accepted:
            return None
        spec = dlg.spec()
        return self.add_grid(spec["kind"], spec["region"], spec["name"], spec["group"], **spec["params"])

    def add_grid(self, kind: str = "square", region="arena", name: str = "Grid", group: bool = True, **params):
        """Add a grid of zones covering the arena ("arena"), the selected zone ("zone"), the whole image
        ("frame") or a given shape."""
        app = self.app
        if app is None:
            return None
        if isinstance(region, Shape):
            shp = region
        elif region == "zone" and self.selected_key() and self.selected_key()[0] == "zone":
            shp = app.zones[self.selected_key()[1]].shape
        elif region == "frame" or (region == "arena" and app.arena is None and not app.zones):
            w, h = self.view.frame_size or app.frame_size or (640, 480)
            shp = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
        else:
            shp = app.arena_or_bounds()
        self.push_undo()
        try:
            g = make_grid(app, kind, shp, name or "Grid", group=group, **params)
        except ValueError as e:
            QMessageBox.warning(self, "Add grid", str(e))
            return None
        self._model_changed()
        self.main.status(f"Added grid “{g.name}” with {len(g.zones)} zones")
        return g

    def delete_grid_of(self, index: int) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.zones)):
            return False
        g = next((g for g in app.grids if app.zones[index].name in g.zones), None)
        if g is None:
            return False
        self.push_undo()
        remove_grid(app, g.name)
        for q in app.sequences:
            q.steps = [z for z in q.steps if app.zone(z) is not None or app.group(z) is not None]
        self._model_changed()
        return True

    # ---- copy / paste --------------------------------------------------------------------
    _clipboard: dict | None = None  # shared by all apparatus (class attribute)

    def copy_selected(self) -> int:
        app = self.app
        keys = [k for k, it in self._items.items() if it.isSelected() and k[0] in ("zone", "point", "line")]
        if app is None or not keys:
            return 0
        clip = {"zones": [], "points": [], "lines": [], "source": id(app)}
        for kind, i in sorted(keys):
            clip[kind + "s"].append(getattr(app, kind + "s")[i].to_dict())
        ApparatusPage._clipboard = clip
        n = len(keys)
        self.main.status(f"Copied {n} item(s)")
        return n

    def paste(self) -> int:
        """Paste copied zones / points / lines (offset when pasted into the apparatus they came from)."""
        app = self.app
        clip = ApparatusPage._clipboard
        if app is None or not clip:
            return 0
        k = clip.setdefault("pasted", {}).get(id(app), 0) + (1 if clip.get("source") == id(app) else 0)
        off = 12.0 * k
        self.push_undo()
        new_keys = []
        for d in clip["zones"]:
            z = Zone.from_dict(d)
            z.shape = z.shape.translated(off, off)
            z.name = unique_name(z.name if z.name not in app.names() else f"{z.name} copy", app.names())
            app.zones.append(z)
            new_keys.append(("zone", len(app.zones) - 1))
        for d in clip["points"]:
            p = PointOfInterest.from_dict(d)
            p.x, p.y = p.x + off, p.y + off
            p.name = unique_name(p.name, [q.name for q in app.points])
            app.points.append(p)
            new_keys.append(("point", len(app.points) - 1))
        for d in clip["lines"]:
            ln = Line.from_dict(d)
            ln.x1, ln.y1, ln.x2, ln.y2 = ln.x1 + off, ln.y1 + off, ln.x2 + off, ln.y2 + off
            ln.name = unique_name(ln.name, [q.name for q in app.lines])
            app.lines.append(ln)
            new_keys.append(("line", len(app.lines) - 1))
        self._model_changed(select=new_keys[0] if new_keys else None)
        self._syncing = True
        for k in new_keys:
            if k in self._items:
                self._items[k].setSelected(True)
        self._syncing = False
        clip["pasted"][id(app)] = clip["pasted"].get(id(app), 0) + 1  # pasting again offsets further
        self.main.status(f"Pasted {len(new_keys)} item(s)")
        return len(new_keys)

    # ---- sequences -----------------------------------------------------------------------
    def add_sequence(self, name: str | None = None, steps=()) -> Sequence | None:
        app = self.app
        if app is None:
            return None
        self.push_undo()
        q = Sequence(unique_name(name or f"Sequence {len(app.sequences) + 1}", [x.name for x in app.sequences]),
                     [s for s in steps if s in app.names()])
        app.sequences.append(q)
        self.main.mark_dirty()
        self.tabs.setCurrentIndex(4)
        self._refresh_side_lists()
        self.seq_list.setCurrentRow(len(app.sequences) - 1)
        return q

    def delete_sequence(self, index: int) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.sequences)):
            return False
        self.push_undo()
        app.sequences.pop(index)
        self.main.mark_dirty()
        self._refresh_side_lists()
        self.seq_list.setCurrentRow(min(index, len(app.sequences) - 1))
        return True

    def rename_sequence(self, index: int, name: str) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.sequences)) or not name.strip():
            return False
        if name.strip() == app.sequences[index].name:
            return False
        self.push_undo()
        app.sequences[index].name = unique_name(name, [q.name for i, q in enumerate(app.sequences) if i != index])
        self.main.mark_dirty()
        self._refresh_side_lists()
        self.seq_list.setCurrentRow(index)
        return True

    def _edit_steps(self, index: int, fn, select: int | None = None) -> bool:
        app = self.app
        if app is None or not (0 <= index < len(app.sequences)):
            return False
        steps = list(app.sequences[index].steps)
        new = fn(steps)
        if new is None or new == app.sequences[index].steps:
            return False
        self.push_undo()
        app.sequences[index].steps = new
        self.main.mark_dirty()
        it = self.seq_list.item(index)
        if it is not None:
            it.setToolTip(" → ".join(new))
        self._load_seq_editor()
        if select is not None:
            self.seq_steps.setCurrentRow(select)
        self._draw_sequence_overlay()
        return True

    def add_sequence_step(self, index: int, zone: str) -> bool:
        app = self.app
        if app is None or zone not in app.names():
            return False
        n = len(app.sequences[index].steps) if 0 <= index < len(app.sequences) else 0
        return self._edit_steps(index, lambda st: st + [zone], n)

    def remove_sequence_step(self, index: int, step: int) -> bool:
        return self._edit_steps(index, lambda st: st[:step] + st[step + 1:] if 0 <= step < len(st) else None,
                                max(0, step - 1))

    def move_sequence_step(self, index: int, step: int, delta: int) -> bool:
        def mv(st):
            j = step + delta
            if not (0 <= step < len(st) and 0 <= j < len(st)):
                return None
            st[step], st[j] = st[j], st[step]
            return st
        return self._edit_steps(index, mv, step + delta)

    def set_sequence_option(self, index: int, attr: str, value) -> bool:
        app = self.app
        if self._loading or app is None or not (0 <= index < len(app.sequences)):
            return False
        q = app.sequences[index]
        if getattr(q, attr) == value:
            return False
        self.push_undo()
        setattr(q, attr, value)
        self.main.mark_dirty()
        self._draw_sequence_overlay()
        return True

    def _seq_row_changed(self, _r):
        if not self._loading:
            self._load_seq_editor()
            self._draw_sequence_overlay()

    def _load_seq_editor(self):
        app = self.app
        r = self.seq_list.currentRow()
        q = app.sequences[r] if app is not None and 0 <= r < len(app.sequences) else None
        self._loading, was = True, self._loading
        self.seq_name.setText(q.name if q else "")
        cur = self.seq_steps.currentRow()
        self.seq_steps.clear()
        for i, st in enumerate(q.steps if q else []):
            z = app.zone(st)
            it = QListWidgetItem(color_icon(z.color) if z else color_icon("#94a3b8"), f"{i + 1}. {st}")
            self.seq_steps.addItem(it)
        if q and q.steps:
            self.seq_steps.setCurrentRow(min(max(cur, 0), len(q.steps) - 1))
        self.seq_from_start.setChecked(q.from_start if q else True)
        self.seq_allow_other.setChecked(q.allow_other if q else True)
        self.seq_bidir.setChecked(q.bidirectional if q else False)
        self.seq_overlap.setChecked(q.overlap if q else False)
        self.seq_end.setCurrentIndex(max(0, self.seq_end.findData(q.end if q else "entry")))
        self.seq_max.setValue(q.max_duration_s if q else 0.0)
        for w in (self.seq_name, self.seq_steps, self.seq_zone, self.btn_step_add, self.btn_step_up,
                  self.btn_step_down, self.btn_step_del, self.seq_from_start, self.seq_allow_other, self.seq_bidir,
                  self.seq_overlap, self.seq_end, self.seq_max, self.btn_seq_del):
            w.setEnabled(q is not None)
        self._loading = was

    # ---- side lists ------------------------------------------------------------------
    def _refresh_side_lists(self):
        app = self.app
        self._loading, was = True, self._loading
        for lst, items in ((self.zone_list, app.zones if app else []), (self.point_list, app.points if app else []),
                           (self.line_list, app.lines if app else [])):
            r = lst.currentRow()
            lst.clear()
            for m in items:
                lst.addItem(QListWidgetItem(color_icon(m.color), m.name))
            lst.setCurrentRow(-1)
        gr = self.group_list.currentRow()
        self.group_list.clear()
        for g in (app.groups if app else []):
            it = QListWidgetItem(g.name)
            it.setToolTip(self._group_text(g))
            self.group_list.addItem(it)
        if app and app.groups:
            self.group_list.setCurrentRow(min(max(gr, 0), len(app.groups) - 1))
        sr = self.seq_list.currentRow()
        self.seq_list.clear()
        for q in (app.sequences if app else []):
            it = QListWidgetItem(q.name)
            it.setToolTip(" → ".join(q.steps) or "(no steps)")
            self.seq_list.addItem(it)
        if app and app.sequences:
            self.seq_list.setCurrentRow(min(max(sr, 0), len(app.sequences) - 1))
        self.seq_zone.clear()
        self.seq_zone.addItems(app.names() if app else [])
        self._loading = was
        self._sync_lists_from_canvas()
        self._load_group_editor()
        self._load_seq_editor()
        self._update_tab_titles()

    def _sync_lists_from_canvas(self):
        key = self.selected_key()
        self._loading, was = True, self._loading
        for kind, lst in (("zone", self.zone_list), ("point", self.point_list), ("line", self.line_list)):
            if key is not None and key[0] == kind:
                lst.setCurrentRow(key[1])
                lst.scrollToItem(lst.item(key[1]), QAbstractItemView.EnsureVisible)
            else:
                lst.setCurrentRow(-1)
                lst.clearSelection()
        self._loading = was
        if key is not None and key[0] in ("zone", "point", "line"):
            self.tabs.setCurrentIndex(("zone", "point", "line").index(key[0]))
        elif key is not None and key[0] == "arena":
            self.tabs.setCurrentIndex(0)
        self._load_editor()

    def _on_scene_selection(self):
        if self._syncing:
            return
        self._sync_lists_from_canvas()

    def _list_row_changed(self, kind: str, row: int):
        if self._loading:
            return
        if row >= 0:
            self.select_item(kind, row)
        self._load_editor()

    def _tab_changed(self, _i):
        if self.app is not None:
            self._draw_sequence_overlay()

    def _load_editor(self):
        app = self.app
        self._loading, was = True, self._loading
        r = self.zone_list.currentRow()
        z = app.zones[r] if app is not None and 0 <= r < len(app.zones) else None
        self.zone_name.setText(z.name if z else "")
        self.zone_color.set_color(z.color if z else "#94a3b8")
        for w in (self.zone_name, self.zone_color, self.btn_zone_dup, self.btn_zone_arena, self.btn_zone_del,
                  self.zone_rule, self.zone_inv, self.zone_hidden, self.zone_moveable):
            w.setEnabled(z is not None)
        self.zone_info.setText(describe_shape(z.shape, app) if z else "—")
        self.zone_rule.setCurrentIndex(max(0, self.zone_rule.findData(z.entry_rule if z else "")))
        self.zone_frac.setValue(int(round((z.body_fraction if z else 0.8) * 100)))
        self.zone_frac.setEnabled(z is not None and z.entry_rule == "body")
        self.zone_inv.setValue(z.investigation_distance_cm if z else 0.0)
        self.zone_inv.setSuffix(f" {app.unit}" if app is not None else " cm")
        self.zone_hidden.setChecked(bool(z and z.hidden))
        self.zone_moveable.setChecked(bool(z and z.moveable))
        self.btn_grid_del.setVisible(z is not None and any(z.name in g.zones for g in app.grids))

        r = self.point_list.currentRow()
        p = app.points[r] if app is not None and 0 <= r < len(app.points) else None
        self.point_name.setText(p.name if p else "")
        self.point_color.set_color(p.color if p else "#94a3b8")
        self.point_radius.setValue(p.radius_cm if p else 0)
        self.point_x.setValue(p.x if p else 0)
        self.point_y.setValue(p.y if p else 0)
        for w in (self.point_name, self.point_color, self.point_radius, self.point_x, self.point_y,
                  self.btn_point_del):
            w.setEnabled(p is not None)

        r = self.line_list.currentRow()
        ln = app.lines[r] if app is not None and 0 <= r < len(app.lines) else None
        self.line_name.setText(ln.name if ln else "")
        self.line_color.set_color(ln.color if ln else "#94a3b8")
        if ln:
            L = math.hypot(ln.x2 - ln.x1, ln.y2 - ln.y1)
            self.line_info.setText(f"{L / app.px_per_cm:.1f} cm" if app.px_per_cm else f"{L:.0f} px")
        else:
            self.line_info.setText("—")
        for w in (self.line_name, self.line_color, self.btn_line_del):
            w.setEnabled(ln is not None)
        self._loading = was

    def _refresh_selected_info(self):
        self._load_editor()

    def _model_changed(self, select: tuple[str, int] | None = None):
        """Model changed structurally: redraw canvas and lists, mark dirty."""
        self.main.mark_dirty()
        self.rebuild_canvas(keep_selection=select is None)
        if select is not None and select in self._items:
            self._syncing = True
            self.view.scene().clearSelection()
            self._items[select].setSelected(True)
            self._syncing = False
        self._refresh_side_lists()
        self._refresh_info()

    def _changed(self):
        self.main.mark_dirty()

    # ================================================================== undo
    def push_undo(self):
        app = self.app
        if app is None:
            return
        st = self._undo.setdefault(id(app), [])
        d = app.to_dict()
        if not st or st[-1] != d:
            st.append(d)
            del st[:-200]
        self._redo[id(app)] = []

    def undo(self) -> bool:
        return self._undo_redo(self._undo, self._redo, "Undo")

    def redo(self) -> bool:
        return self._undo_redo(self._redo, self._undo, "Redo")

    def _undo_redo(self, src, dst, what) -> bool:
        app = self.app
        if app is None or self.view.drawing:
            if app is not None:
                self.view.cancel_drawing()
            return False
        st = src.get(id(app), [])
        cur = app.to_dict()
        while st and st[-1] == cur:
            st.pop()
        if not st:
            self.main.status(f"Nothing to {what.lower()}")
            return False
        dst.setdefault(id(app), []).append(cur)
        name = app.name
        new = Apparatus.from_dict(st.pop())
        app.__dict__.update(new.__dict__)
        app.name = name
        self._model_changed()
        return True

    # ============================================================= templates
    def template_square(self) -> bool:
        return bool(self._pending_template and self._pending_template.get("square"))

    def pending_template_preview(self, x, y, w, h) -> Apparatus | None:
        spec = self._pending_template
        if not spec or w < 2 or h < 2:
            return None
        try:
            return templates.build(spec["key"], x, y, w, h, **spec["params"])
        except Exception:
            return None

    def create_from_template(self):
        if self.project is None:
            return
        cur = self.app
        key = cur.template if cur is not None and cur.template in TEMPLATES and cur.template != "custom" \
            else self.project.protocol
        dlg = TemplateDialog(self, key, cur.name if cur else None, self._names(), self._bg_real)
        if dlg.exec() != QDialog.Accepted:
            return
        spec = {"key": dlg.key(), "params": dlg.params(), "name": dlg.name(), "replace": dlg.is_replace(),
                "square": dlg.square.isChecked()}
        if dlg.placement() == "fit":
            w, h = self.view.frame_size or (640, 480)
            x, y = 0.0, 0.0
            if spec["square"]:
                s = min(w, h)
                x, y, w, h = (w - s) / 2, (h - s) / 2, s, s
            return self.apply_template(spec["key"], x, y, w, h, spec["params"], spec["name"], spec["replace"])
        self.start_template_placement(spec)

    def start_template_placement(self, spec: dict):
        self._pending_template = spec
        self.set_tool("template")
        self.main.status("Drag a rectangle around the apparatus on the image (Esc cancels)", 15000)

    def _apply_multi(self, key: str, built: list, name: str | None, replace: bool) -> Apparatus:
        """One apparatus per arena (e.g. per well); the first replaces the current apparatus if asked."""
        cur = self.app
        fs = (cur.frame_size if cur else None) or self._real_frame_size()
        prefix = (name + " ") if name and name != TEMPLATES[key].title and not replace else ""
        first = None
        if replace and cur is not None:
            self.push_undo()
        for i, a in enumerate(built):
            a.frame_size = fs
            a.name = prefix + a.name
            if i == 0 and replace and cur is not None:
                old = cur.name
                cur.__dict__.update(a.__dict__)
                cur.name = old
                first = cur
                continue
            a.name = unique_name(a.name, self._names())
            self._inherit_background(cur, a)
            self.project.apparatus.append(a)
            first = first or a
        self.main.mark_dirty()
        self._refresh_app_list(select=first)
        self.main.status(f"Created {len(built)} apparatus from the {TEMPLATES[key].title} template "
                         "(one per arena; tests on the same video are tracked together)")
        return first

    def apply_template(self, key: str, x, y, w, h, params: dict | None = None, name: str | None = None,
                       replace: bool = False) -> Apparatus:
        """Build a template into the bounding box (x, y, w, h); replace the current apparatus or add a new one."""
        built = templates.build_many(key, float(x), float(y), float(w), float(h), **(params or {}))
        cur = self.app
        if len(built) > 1:
            return self._apply_multi(key, built, name, replace)
        new = built[0]
        new.frame_size = (cur.frame_size if cur else None) or self._real_frame_size()
        if replace and cur is not None:
            self.push_undo()
            old = cur.name
            cur.__dict__.update(new.__dict__)
            cur.name = old
            if name and name != old:
                self._rename_apparatus(cur, name)
            target = cur
        else:
            new.name = unique_name(name or new.name, self._names())
            self._inherit_background(cur, new)
            self.project.apparatus.append(new)
            target = new
        self.main.mark_dirty()
        self._refresh_app_list(select=target)
        info = TEMPLATES[key]
        self.main.status(f"Created “{target.name}” from the {info.title} template — adjust zones as needed")
        return target
