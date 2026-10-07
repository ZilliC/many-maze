"""The apparatus map canvas: the map objects over the background frame (scene coordinates are video pixels) and
the drawing tools."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView

from ....core import templates
from ....core.apparatus import Apparatus, Sequence
from ....core.geometry import Ellipse
from ... import theme
from ...widgets import FrameView, draw_apparatus, loading
from .canvas_items import Badge, Handle, Label, LineItem, PointItem, RulerItem, ShapeItem
from .tool_icons import HIGHLIGHT, PREVIEW


class EditorView(FrameView):
    """The map of the page's current apparatus: one graphics item per object (``map_items``, keyed by (kind,
    index)), the calibration ruler, the selected sequence's arrows, and the drawing tools. The image sits centred
    on the near-white work area, as in ANY-maze. ``selection_changed`` is emitted when the user changes the
    selection (not when it is set from code)."""

    selection_changed = Signal()

    def __init__(self, page):
        super().__init__()
        self.page = page
        self._cull_timer = QTimer(self)
        self._cull_timer.setSingleShot(True)
        self._cull_timer.setInterval(0)
        self._cull_timer.timeout.connect(self._cull_labels)
        self.show_labels = True
        self.snap_enabled = False
        self.map_items: dict[tuple[str, int], QGraphicsItem] = {}
        self.ruler: RulerItem | None = None
        self.ruler_label: Label | None = None
        self.seq_overlay: QGraphicsPathItem | None = None
        self._root: QGraphicsPathItem | None = None
        self._syncing = False
        self.tool = "select"
        self.template: dict | None = None  # the template being placed with the "template" tool (see TemplateDialog)
        self._start: QPointF | None = None
        self._poly: list[QPointF] = []
        self._preview: QGraphicsPathItem | None = None
        self._preview_group = None
        self._preview_ruler: RulerItem | None = None
        self._pan = None
        self.scene().setItemIndexMethod(QGraphicsScene.NoIndex)
        self.scene().selectionChanged.connect(lambda: None if self._syncing else self.selection_changed.emit())
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setBackgroundBrush(QBrush(QColor(theme.WORK_BG)))
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"QGraphicsView{{background:{theme.WORK_BG};border:none;}}")

    @property
    def app(self) -> Apparatus | None:
        return self.page.app

    # ---- map objects ------------------------------------------------------
    def rebuild(self, keep_selection: bool = True):
        """Recreate the items from the current apparatus."""
        sel = self.selected_key() if keep_selection else None
        sc = self.scene()
        with loading(self, "_syncing"):
            if self._root is not None:
                sc.removeItem(self._root)
            self._root = QGraphicsPathItem()
            self._root.setZValue(0)
            sc.addItem(self._root)
            self.map_items = {}
            self.ruler = self.ruler_label = self.seq_overlay = None
            app = self.app
            if app is not None:
                self._add_items(app)
            if sel in self.map_items:
                self.map_items[sel].setSelected(True)
        self.schedule_cull()

    def _add_items(self, app: Apparatus):
        if app.arena is not None:
            it = ShapeItem(self, "arena", 0, self._root)
            it.setZValue(1)
            self.map_items[("arena", 0)] = it
        order = sorted(range(len(app.zones)), key=lambda i: -app.zones[i].shape.area())
        for rank, i in enumerate(order):
            it = ShapeItem(self, "zone", i, self._root)
            it.setZValue(2 + rank * 0.01)
            self.map_items[("zone", i)] = it
        self._declutter_labels(order)
        for i in range(len(app.lines)):
            it = LineItem(self, i, self._root)
            it.setZValue(40)
            self.map_items[("line", i)] = it
        for i in range(len(app.points)):
            it = PointItem(self, i, self._root)
            it.setZValue(50)
            self.map_items[("point", i)] = it
        if app.calibration_line:
            x1, y1, x2, y2 = app.calibration_line
            self.ruler = RulerItem(x1, y1, x2, y2, app.px_per_cm, self._root)
            self.ruler.setZValue(45)
            if app.calibration_length_cm:
                lb = Label(f"{app.calibration_length_cm:g} cm", self._root, theme.RULER, anchor="top")
                lb.setPos((x1 + x2) / 2, (y1 + y2) / 2)
                lb.setZValue(46)
                lb.setVisible(self.show_labels)
                self.ruler_label = lb

    def item(self, kind: str, index: int = 0):
        return self.map_items.get((kind, index))

    def selected_key(self) -> tuple[str, int] | None:
        return next((k for k, it in self.map_items.items() if it.isSelected()), None)

    def selected_keys(self) -> list[tuple[str, int]]:
        return [k for k, it in self.map_items.items() if it.isSelected()]

    def select(self, keys, ensure_visible: bool = False):
        """Select exactly these items (without emitting selection_changed)."""
        with loading(self, "_syncing"):
            self.scene().clearSelection()
            for k in keys:
                it = self.map_items.get(k)
                if it is not None:
                    it.setSelected(True)
                    if ensure_visible:
                        self.ensureVisible(it.sceneBoundingRect(), 20, 20)

    def set_show_labels(self, on: bool):
        self.show_labels = on
        self.rebuild()

    def schedule_cull(self):
        if not self._cull_timer.isActive():
            self._cull_timer.start()

    def _cull_labels(self):
        """Hide name tags that would overlap a more important one: the selected object first, then points,
        lines, the ruler and smaller zones — so the map stays readable with many zones (e.g. grids)."""
        labels = []
        for (kind, _), it in self.map_items.items():
            lb = getattr(it, "label", None)
            if lb is None or lb.scene() is None:
                continue
            if it.isSelected():
                prio = (0, 0.0)
            elif kind == "zone":
                prio = (3, it.get_shape().area())
            else:
                prio = (1 if kind == "point" else 2, 0.0)
            labels.append((prio, lb))
        if self.ruler_label is not None and self.ruler_label.scene() is not None:
            labels.append(((2, 0.0), self.ruler_label))
        labels.sort(key=lambda t: t[0])
        placed: list[QRectF] = []
        if self.seq_overlay is not None:  # sequence step badges stay readable
            for ch in self.seq_overlay.childItems():
                if isinstance(ch, Badge):
                    q = self.mapFromScene(ch.scenePos())
                    placed.append(ch.boundingRect().translated(q.x(), q.y()))
        for _, lb in labels:
            if not self.show_labels or not lb.text:
                lb.setVisible(False)
                continue
            q = self.mapFromScene(lb.scenePos())
            r = lb.boundingRect().translated(q.x(), q.y())
            ok = not any(r.intersects(o) for o in placed)
            lb.setVisible(ok)
            if ok:
                placed.append(r.adjusted(-3, -1, 3, 1))

    def _declutter_labels(self, order):
        """Zones sharing a centre (e.g. Arena around Centre) get their label at the top edge instead."""
        tol = 18.0 / (self.transform().m11() or 1.0)
        placed = []
        for i in reversed(order):  # smallest first
            it = self.map_items[("zone", i)]
            c = it.get_shape().centroid()
            clash = any(math.hypot(c[0] - q[0], c[1] - q[1]) < tol for q in placed)
            if clash != it.label_at_top:
                it.label_at_top = clash
                it.sync()
            placed.append(c if not clash else (it.label.pos().x(), it.label.pos().y()))

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

    def show_sequence(self, q: Sequence | None):
        """White arrows between the steps of a sequence, with numbered badges, like the arrows in ANY-maze's
        sequence pictures (None removes them)."""
        if self.seq_overlay is not None and self.seq_overlay.scene() is not None:
            self.scene().removeItem(self.seq_overlay)
        self.seq_overlay = None
        if q is None or self._root is None:
            return
        cents = [c for c in (self._step_centre(n) for n in q.steps) if c is not None]
        root = QGraphicsPathItem(self._root)
        root.setZValue(60)
        root.setAcceptedMouseButtons(Qt.NoButton)
        self.seq_overlay = root
        path = QPainterPath()
        L = max(8.0, 0.03 * max(self.frame_size or (640, 480)))
        for (x0, y0), (x1, y1) in zip(cents, cents[1:]):
            d = math.hypot(x1 - x0, y1 - y0)
            if d < 1e-6:
                continue
            ux, uy = (x1 - x0) / d, (y1 - y0) / d
            sh = min(0.2 * d, 2.4 * L)
            a, b = QPointF(x0 + ux * sh, y0 + uy * sh), QPointF(x1 - ux * sh, y1 - uy * sh)
            path.moveTo(a)
            path.lineTo(b)
            for sgn in (1, -1):
                path.moveTo(b)
                path.lineTo(b.x() - L * ux + sgn * 0.6 * L * uy, b.y() - L * uy - sgn * 0.6 * L * ux)
        shadow = QGraphicsPathItem(path, root)
        sp = QPen(QColor(15, 23, 42, 90), 6)
        sp.setCosmetic(True)
        sp.setCapStyle(Qt.RoundCap)
        sp.setJoinStyle(Qt.RoundJoin)
        shadow.setPen(sp)
        shadow.setAcceptedMouseButtons(Qt.NoButton)
        shadow.setFlag(QGraphicsItem.ItemStacksBehindParent)
        root.setPath(path)
        pen = QPen(QColor("#ffffff"), 3.2)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        root.setPen(pen)
        for i, (x, y) in enumerate(cents):
            bd = Badge(f"{i + 1}", root)
            bd.setPos(x, y)
            bd.setZValue(61)
        if q.bidirectional and len(cents) > 1:
            lb = Label("both directions", root, theme.HEADING, anchor="right")
            lb.setPos(*cents[0])
        self.schedule_cull()

    def snap(self, p: QPointF, exclude=None) -> QPointF:
        """"Points attract": the nearest corner of another object within 8 screen pixels, else p itself."""
        if not self.snap_enabled or self.app is None:
            return p
        best, bd = None, 8.0 / (self.transform().m11() or 1.0)
        for it in self.map_items.values():
            if it is exclude:
                continue
            if isinstance(it, ShapeItem):
                s = it.get_shape()
                cands = it.handle_points() + ([(s.cx, s.cy)] if isinstance(s, Ellipse) else [])
            elif isinstance(it, LineItem):
                m = it.model
                cands = [(m.x1, m.y1), (m.x2, m.y2)]
            else:
                cands = [(it.model.x, it.model.y)]
            for x, y in cands:
                d = math.hypot(x - p.x(), y - p.y())
                if d < bd:
                    best, bd = (x, y), d
        return QPointF(*best) if best is not None else p

    # ---- view -----------------------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.schedule_cull()

    def wheelEvent(self, e):
        super().wheelEvent(e)
        self.schedule_cull()

    def scale(self, sx, sy):
        super().scale(sx, sy)
        self.schedule_cull()

    # ---- tool state ---------------------------------------------------
    def set_tool(self, tool: str):
        self.cancel_drawing()
        self.tool = tool
        if tool != "template":
            self.template = None
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
        for attr in ("_preview", "_preview_group", "_preview_ruler"):
            it = getattr(self, attr)
            if it is not None:
                sc.removeItem(it)
                setattr(self, attr, None)

    def _set_preview(self, path: QPainterPath, closed=True):
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(QColor(PREVIEW), 1.6, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self._preview.setZValue(500)
            self.scene().addItem(self._preview)
        fill = QColor(HIGHLIGHT)
        fill.setAlpha(60)
        self._preview.setBrush(fill if closed else Qt.NoBrush)
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
        square = bool(mods & Qt.ShiftModifier) or bool(self.template and self.template.get("square"))
        if kind in ("rect", "ellipse"):
            p = self.constrain(p0, p, square)
        r = QRectF(p0, p).normalized()
        if self.tool == "template":
            if self._preview_group is not None:
                self.scene().removeItem(self._preview_group)
                self._preview_group = None
            app = self._template_preview(r)
            if app is not None:
                self._preview_group = draw_apparatus(self.scene(), app)
                self._preview_group.setZValue(500)
            path = QPainterPath()
            path.addRect(r)
            self._set_preview(path, False)
            self.page.show_measure(f"{r.width():.0f} × {r.height():.0f} px")
            return
        if self.tool == "calibrate":
            if self._preview_ruler is None:
                self._preview_ruler = RulerItem(p0.x(), p0.y(), p.x(), p.y(), self.app.px_per_cm if self.app else None)
                self._preview_ruler.setZValue(500)
                self.scene().addItem(self._preview_ruler)
            self._preview_ruler.set_line(p0.x(), p0.y(), p.x(), p.y(), self.app.px_per_cm if self.app else None)
            self.page.show_measure(f"Length {math.hypot(p.x() - p0.x(), p.y() - p0.y()):.1f} px")
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

    def _template_preview(self, r: QRectF) -> Apparatus | None:
        """The template being placed, built into the dragged rectangle."""
        if r.width() < 2 or r.height() < 2:
            return None
        try:
            return templates.build(self.template["key"], r.x(), r.y(), r.width(), r.height(), **self.template["params"])
        except Exception:
            return None

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

    def _scene_pos(self, e) -> QPointF:
        """Scene position of a mouse event, attracted to nearby vertices when "Points attract" is on."""
        return self.snap(self.mapToScene(e.position().toPoint()))

    # ---- events ---------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if self.tool == "select":
            super().mousePressEvent(e)
            return
        p = self._scene_pos(e)
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
            self._update_drag_preview(self.snap(p), e.modifiers())
        elif self._poly:
            self._update_poly_preview(self.snap(p))

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
            p1 = self._scene_pos(e)
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

    def contextMenuEvent(self, e):
        if self.tool != "select" or self.drawing or isinstance(self.itemAt(e.pos()), Handle):
            return  # right-click finishes / cancels drawing, or removes a vertex
        self.page.show_context_menu(e.globalPos())

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
