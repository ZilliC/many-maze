"""Graphics items of the apparatus map: editable zones / arena, points, lines, vertex handles, name tags, the
calibration ruler and sequence step badges."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem

from ....core.apparatus import Line, PointOfInterest
from ....core.geometry import Ellipse, Polygon, Shape
from ... import theme
from ...widgets import shape_path
from .tool_icons import ACCENT, HIGHLIGHT

class Label(QGraphicsItem):
    """Small, subtle screen-sized name tag (light pill with a colour dot); transparent to the mouse."""

    def __init__(self, text: str, parent=None, color: str = "#ffffff", anchor: str = "center"):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.font = QFont()
        self.font.setPointSizeF(8.0)
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
        w = fm.horizontalAdvance(text) + 18
        h = fm.height() + 2
        self._rect = {"center": QRectF(-w / 2, -h / 2, w, h), "top": QRectF(-w / 2, 4, w, h)}.get(
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
        p.setRenderHint(QPainter.Antialiasing)
        r = self._rect
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 215))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setBrush(self.border)
        p.drawEllipse(QPointF(r.left() + 7, r.center().y()), 3, 3)
        p.setPen(QColor("#374151"))
        p.setFont(self.font)
        p.drawText(r.adjusted(12, 0, -3, 0), Qt.AlignCenter, self.text)


class Handle(QGraphicsRectItem):
    """Small square orange vertex handle (screen-sized); drags are reported to the owning item."""

    def __init__(self, owner, index: int):
        super().__init__(-3.5, -3.5, 7, 7, owner)
        self.owner = owner
        self.index = index
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setBrush(QBrush(QColor(ACCENT)))
        self.setPen(QPen(QColor(ACCENT).darker(125), 1))
        self.setZValue(100)
        self.setCursor(Qt.CrossCursor)
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setVisible(False)
        self._before: dict | None = None  # the apparatus when a drag started (undo step if it changes)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._before = self.owner.view.app.to_dict()
            e.accept()
        elif e.button() == Qt.RightButton and hasattr(self.owner, "delete_vertex"):
            QTimer.singleShot(0, lambda o=self.owner, i=self.index: o.delete_vertex(i))
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e):
        if self._before is not None:
            sp = self.owner.view.snap(e.scenePos(), exclude=self.owner)
            self.owner.move_handle(self.index, self.owner.mapFromScene(sp), e.modifiers())

    def mouseReleaseEvent(self, e):
        before, self._before = self._before, None
        view = self.owner.view
        if before is not None and before != view.app.to_dict():
            view.page.push_undo(before)
            view.page.geometry_edited()


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

    def __init__(self, view, kind: str, index: int, parent):
        super().__init__()
        self.view, self.kind, self.index = view, kind, index
        self.handles = []
        self.label = Label("", self) if kind == "zone" else None
        self.label_at_top = False
        self.halo: QGraphicsPathItem | None = None  # investigation distance outline
        self.setParentItem(parent)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.sync()

    @property
    def model(self):
        return self.view.app.zones[self.index] if self.kind == "zone" else None

    def get_shape(self) -> Shape:
        return self.view.app.arena if self.kind == "arena" else self.model.shape

    def set_shape(self, s: Shape):
        if self.kind == "arena":
            self.view.app.arena = s
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
        pen = QPen(QColor(ACCENT))
        pen.setCosmetic(True)
        if self.kind == "zone":
            c = QColor(self.model.color)
            pen.setWidthF(2.0 if sel else 1.3)
            z = self.model
            if sel:  # ANY-maze highlights the selected zone in light blue
                fc = QColor(HIGHLIGHT)
                fc.setAlpha(120)
            else:
                fc = QColor(c)
                fc.setAlpha(26)
            if z.hidden:
                hc = QColor(HIGHLIGHT if sel else c)
                hc.setAlpha(160 if sel else 120)
                self.setBrush(QBrush(hc, Qt.BDiagPattern))
                pen.setStyle(Qt.DashLine)
            else:
                self.setBrush(QBrush(fc))
            self._sync_halo(s, QColor(ACCENT))
            if self.label_at_top:
                x0, y0, x1, _ = s.bounds()
                self.label.setPos((x0 + x1) / 2, y0)
            else:
                self.label.setPos(*s.centroid())
            self.label.set_anchor("top" if self.label_at_top else "center")
            tags = [t for t, on in (("hidden", z.hidden), ("moveable", z.moveable)) if on]
            self.label.set_text(z.name + (f" ({', '.join(tags)})" if tags else ""), self.model.color)
            self.label.setVisible(self.view.show_labels)
            self.view.schedule_cull()
        else:
            pen.setWidthF(2.0 if sel else 1.3)
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([6, 4])
            self.setBrush(Qt.NoBrush)
        self.setPen(pen)
        self._sync_handles(self.handle_points())

    def _sync_halo(self, s: Shape, c: QColor):
        """Dotted outline at the investigation distance around the zone."""
        app = self.view.app
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
        pen = QPen(c, 1.2, Qt.DotLine)
        pen.setCosmetic(True)
        self.halo.setPen(pen)
        self.halo.setBrush(Qt.NoBrush)
        self.halo.setVisible(True)

    def paint(self, p, opt, widget=None):
        p.setPen(self.pen())
        p.setBrush(self.brush())
        p.drawPath(self.path())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
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
        self.view.page.push_undo()
        pts = list(s.points)
        del pts[i]
        self.set_shape(Polygon(pts))
        self.sync()
        self.view.page.geometry_edited()

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
        self.view.page.push_undo()
        pts.insert(bi + 1, (float(x), float(y)))
        self.set_shape(Polygon(pts))
        self.sync()
        self.view.page.geometry_edited()
        return True

    def mouseDoubleClickEvent(self, e):
        if self.isSelected() and self.view.tool == "select" and self.insert_vertex(e.pos().x(), e.pos().y()):
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

    R = 5.5

    def __init__(self, view, index: int, parent):
        super().__init__(-self.R, -self.R, 2 * self.R, 2 * self.R)
        self.view, self.kind, self.index = view, "point", index
        self.ring = QGraphicsEllipseItem(parent)
        self.ring.setAcceptedMouseButtons(Qt.NoButton)
        self.ring.setZValue(5)
        self.label = Label("", self, anchor="right")
        self.setParentItem(parent)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemSendsGeometryChanges)
        self.sync()

    @property
    def model(self) -> PointOfInterest:
        return self.view.app.points[self.index]

    def sync(self):
        m = self.model
        self.setPos(m.x, m.y)
        self.label.set_text(m.name, m.color)
        self.label.setVisible(self.view.show_labels)
        self.view.schedule_cull()
        self._update_ring()
        self.update()

    def _update_ring(self):
        m, app = self.model, self.view.app
        r = (m.radius_cm or 0) * (app.px_per_cm or 0)
        if r > 0:
            p = self.pos()
            self.ring.setRect(p.x() - r, p.y() - r, 2 * r, 2 * r)
            pen = QPen(QColor(ACCENT), 1.2, Qt.DotLine)
            pen.setCosmetic(True)
            self.ring.setPen(pen)
            fc = QColor(m.color)
            fc.setAlpha(22)
            self.ring.setBrush(fc)
            self.ring.setVisible(True)
        else:
            self.ring.setVisible(False)

    def paint(self, p, opt, widget=None):
        c = QColor(self.model.color)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.setBrush(c)
        p.drawEllipse(QPointF(0, 0), self.R - 1, self.R - 1)
        p.setPen(QPen(QColor(ACCENT), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), self.R, self.R)
        if self.isSelected():  # square orange handles around the point, as for zones
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            d = self.R + 3
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                p.drawRect(QRectF(sx * d - 2.5, sy * d - 2.5, 5, 5))

    def boundingRect(self):
        r = self.R + 6
        return QRectF(-r, -r, 2 * r, 2 * r)

    def itemChange(self, change, value):
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

    def __init__(self, view, index: int, parent):
        super().__init__()
        self.view, self.kind, self.index = view, "line", index
        self.handles = []
        self.label = Label("", self)
        self.setParentItem(parent)
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self.sync()

    @property
    def model(self) -> Line:
        return self.view.app.lines[self.index]

    def sync(self):
        m = self.model
        path = QPainterPath(QPointF(m.x1, m.y1))
        path.lineTo(m.x2, m.y2)
        self.prepareGeometryChange()
        self.setPath(path)
        sel = self.isSelected()
        pen = QPen(QColor(ACCENT if sel else m.color))
        pen.setCosmetic(True)
        pen.setWidthF(2.6 if sel else 2.0)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)
        self.label.setPos(m.x1 + 0.2 * (m.x2 - m.x1), m.y1 + 0.2 * (m.y2 - m.y1))
        self.label.set_text(m.name, m.color)
        self.label.setVisible(self.view.show_labels)
        self.view.schedule_cull()
        self._sync_handles([(m.x1, m.y1), (m.x2, m.y2)])

    def _tol(self) -> float:
        s = self.view.transform().m11() or 1.0
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
        if change == QGraphicsItem.ItemSelectedHasChanged:
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


class RulerItem(QGraphicsItem):
    """The calibration ruler: a green line with tick marks (every cm, longer every 5 and 10 cm), as in ANY-maze.
    Ticks keep a constant on-screen length; transparent to the mouse."""

    def __init__(self, x1, y1, x2, y2, px_per_cm: float | None = None, parent=None):
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.p1, self.p2 = QPointF(x1, y1), QPointF(x2, y2)
        self.ppc = px_per_cm or 0.0

    def set_line(self, x1, y1, x2, y2, px_per_cm: float | None = None):
        self.prepareGeometryChange()
        self.p1, self.p2 = QPointF(x1, y1), QPointF(x2, y2)
        self.ppc = px_per_cm or 0.0
        self.update()

    def boundingRect(self):
        s = self._scale()
        m = 14.0 / s
        return QRectF(self.p1, self.p2).normalized().adjusted(-m, -m, m, m)

    def _scale(self) -> float:
        sc = self.scene()
        views = sc.views() if sc is not None else []
        return (views[0].transform().m11() if views else 1.0) or 1.0

    def paint(self, p, opt, widget=None):
        s = p.transform().m11() or self._scale()
        p.setRenderHint(QPainter.Antialiasing)
        dx, dy = self.p2.x() - self.p1.x(), self.p2.y() - self.p1.y()
        L = math.hypot(dx, dy)
        pen = QPen(QColor(theme.RULER), 2.2)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawLine(self.p1, self.p2)
        if L < 1e-6:
            return
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        # tick spacing: 1 cm when calibrated (thinned out until ticks are ≥ 5 screen px apart), else 10 px
        unit = self.ppc if self.ppc else 10.0
        step_units = 1
        for k in (1, 2, 5, 10, 20, 50, 100, 200, 500):
            step_units = k
            if unit * k * s >= 5:
                break
        major = 10 ** (int(math.log10(step_units)) + 1)  # long ticks at the decade above the step, medium halfway
        tp = QPen(QColor(theme.RULER), 1.3)
        tp.setCosmetic(True)
        p.setPen(tp)
        n = int(L / (unit * step_units)) + 1
        for i in range(n):
            d = i * unit * step_units
            u = i * step_units
            ln = (9.0 if u % major == 0 else 6.5 if 2 * u % major == 0 else 4.0) / s
            x, y = self.p1.x() + ux * d, self.p1.y() + uy * d
            p.drawLine(QPointF(x, y), QPointF(x + nx * ln, y + ny * ln))
        ln = 9.0 / s
        p.drawLine(self.p2, QPointF(self.p2.x() + nx * ln, self.p2.y() + ny * ln))


class Badge(QGraphicsItem):
    """Numbered round badge (sequence steps); screen-sized and transparent to the mouse."""

    R = 8.5

    def __init__(self, text: str, parent=None, color: str = theme.HEADING):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.text, self.color = text, QColor(color)

    def boundingRect(self):
        r = self.R + 1.5
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.setBrush(self.color)
        p.drawEllipse(QPointF(0, 0), self.R, self.R)
        f = QFont()
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(self.boundingRect(), Qt.AlignCenter, self.text)
