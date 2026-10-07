"""The drawing tools of the apparatus map (labels, shortcuts, hints) and their ribbon icons, painted in the
style of ANY-maze's apparatus-map tools (thin blue outlines, orange vertices); the colours of the map."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from ... import theme

ACCENT = theme.APPARATUS  # zone outlines, selection and handles (ANY-maze orange)
HIGHLIGHT = "#2aa7d6"  # fill of the selected zone (ANY-maze light blue / teal)
MAP_BLUE = "#3b78c4"  # outline colour of the drawing-tool icons
PREVIEW = "#ff8a1f"

# key, ribbon label, shortcut, tooltip
TOOLS = [
    ("select", "Select objects", "V", "Select, move and reshape objects. Drag the orange handles to edit vertices; "
                                      "double-click a polygon edge to add a vertex, right-click a vertex to "
                                      "remove it. Ctrl+C / Ctrl+V copy and paste the selection (also into "
                                      "another apparatus)."),
    ("rect", "Rectangle tool", "R", "Draw a rectangular zone (Shift: square)"),
    ("ellipse", "Ellipse tool", "E", "Draw an elliptical zone (Shift: circle)"),
    ("polygon", "Multiline tool", "P", "Click the corners of a zone of any shape; double-click, right-click or "
                                       "Enter closes it"),
    ("point", "Point", "O", "Click to add a point of interest (object, platform, cup…)"),
    ("line", "Line tool", "L", "Drag a line; crossings are counted in each direction"),
    ("arena", "Arena", "A", "Draw the arena boundary (the tracker only searches inside it); choose a rectangle, "
                            "ellipse or polygon"),
    ("calibrate", "Ruler", "C", "Calibrate: drag the ruler along something of known length (e.g. the arena wall), "
                                "then enter its real length"),
]

HINTS = {
    "select": "Click to select · drag to move · drag the handles to reshape · Del deletes · wheel zooms · "
              "middle-drag pans",
    "rect": "Drag to draw a rectangular zone (Shift = square)",
    "ellipse": "Drag to draw an elliptical zone (Shift = circle)",
    "polygon": "Click to add corners · double-click / right-click / Enter closes · Backspace removes a corner · "
               "Esc cancels",
    "point": "Click to place a point of interest",
    "line": "Drag to draw a crossing line",
    "calibrate": "Drag the ruler along an object of known length (e.g. the arena wall)",
    "template": "Drag a rectangle around the whole apparatus · Esc cancels",
}
ARENA_HINTS = {"rect": "Drag a rectangle for the arena boundary",
               "ellipse": "Drag an ellipse for the arena boundary (Shift = circle)",
               "polygon": "Click the vertices of the arena boundary; double-click or Enter closes it"}


def _paint_tool(p: QPainter, kind: str):
    """Paint a ribbon icon on a 32×32 canvas in the style of ANY-maze's apparatus-map tools: thin blue
    outlines, orange vertices."""
    pt = QPointF
    blue, orange = QColor(MAP_BLUE), QColor(ACCENT)

    def pen(c, w=1.6, style=Qt.SolidLine):
        q = QPen(QColor(c), w, style)
        q.setJoinStyle(Qt.RoundJoin)
        q.setCapStyle(Qt.RoundCap)
        return q

    def vertex(x, y, s=4.2):
        p.setPen(Qt.NoPen)
        p.setBrush(orange)
        p.drawRect(QRectF(x - s / 2, y - s / 2, s, s))

    p.setBrush(Qt.NoBrush)
    if kind == "select":
        p.setPen(pen("#4b5563", 1.4))
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(QPolygonF([pt(9, 3), pt(9, 25), pt(14.5, 20), pt(18.5, 28.5), pt(22, 27), pt(18, 18.5),
                                 pt(25, 18.5)]))
    elif kind == "polygon":
        hexa = [pt(16 + 11 * math.cos(math.radians(a)), 16 + 11 * math.sin(math.radians(a)))
                for a in range(-90, 270, 60)]
        p.setPen(pen(blue, 1.5))
        p.drawPolygon(QPolygonF(hexa))
        for q in hexa:
            vertex(q.x(), q.y())
    elif kind == "rect":
        p.setPen(pen(blue, 1.6))
        p.drawRect(QRectF(4, 9, 24, 14))
    elif kind == "ellipse":
        p.setPen(pen(blue, 1.6))
        p.drawEllipse(QRectF(4, 9, 24, 14))
    elif kind == "line":
        p.setPen(pen(blue, 1.8))
        p.drawLine(pt(6, 8), pt(26, 25))
    elif kind == "point":
        p.setPen(pen(blue, 1.4, Qt.DotLine))
        p.drawEllipse(pt(16, 16), 11, 11)
        p.setPen(Qt.NoPen)
        p.setBrush(orange)
        p.drawEllipse(pt(16, 16), 4.5, 4.5)
    elif kind == "arena":
        p.setPen(pen(blue, 1.6, Qt.DashLine))
        p.drawRoundedRect(QRectF(5, 5, 22, 22), 3, 3)
        for x, y in ((5, 5), (27, 5), (5, 27), (27, 27)):
            vertex(x, y, 4.6)
    elif kind == "calibrate":
        p.setPen(pen(theme.RULER, 2.4))
        p.drawLine(pt(3, 20), pt(29, 20))
        p.setPen(pen("#1f9d6a", 1.4))
        for i, x in enumerate(range(4, 30, 3)):
            p.drawLine(pt(x, 20), pt(x, 14 if i % 3 == 0 else 17))
    elif kind == "grid":
        p.setPen(pen(blue, 1.5))
        for v in (5, 11.5, 18, 24.5):
            p.drawLine(pt(v + 1.5, 4), pt(v + 1.5, 28))
            p.drawLine(pt(4, v + 1.5), pt(28, v + 1.5))
    elif kind == "select_all":
        p.setPen(pen("#6b7280", 1.3, Qt.DashLine))
        p.drawRect(QRectF(5, 5, 22, 22))
        p.setPen(pen(blue, 1.4))
        p.drawRect(QRectF(11, 11, 10, 10))
        for x, y in ((5, 5), (27, 5), (5, 27), (27, 27)):
            vertex(x, y, 5)
    elif kind == "delete_sel":
        p.setPen(pen(blue, 1.5))
        p.drawRect(QRectF(4, 4, 20, 20))
        p.setPen(pen("#d9412b", 3))
        p.drawLine(pt(14, 14), pt(28, 28))
        p.drawLine(pt(28, 14), pt(14, 28))
    elif kind == "magnet":
        p.setPen(QPen(orange, 6.5, Qt.SolidLine, Qt.FlatCap))
        path = QPainterPath(pt(8, 26))
        path.lineTo(8, 15)
        path.arcTo(QRectF(8, 7, 16, 16), 180, -180)
        path.lineTo(24, 26)
        p.drawPath(path)
        p.setPen(QPen(QColor("#d9412b"), 6.5, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(pt(8, 26), pt(8, 29.5))
        p.drawLine(pt(24, 26), pt(24, 29.5))
    elif kind == "fit":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f2a649"))
        p.drawRect(QRectF(10, 10, 12, 12))
        p.setBrush(blue)
        for ang in (0, 90, 180, 270):
            p.save()
            p.translate(16, 16)
            p.rotate(ang)
            p.drawPolygon(QPolygonF([pt(-3.5, -10.5), pt(3.5, -10.5), pt(0, -15)]))
            p.restore()
    elif kind == "labels":
        p.setPen(pen(blue, 1.4))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(3, 9, 26, 14), 3, 3)
        f = QFont()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#374151"))
        p.drawText(QRectF(3, 9, 26, 14), Qt.AlignCenter, "Abc")
    elif kind == "template":
        p.setPen(pen(blue, 1.4))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(3, 3, 26, 26))
        p.setPen(pen(orange, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(7, 7, 18, 18))
        p.drawRect(QRectF(12, 12, 8, 8))
        for x, y in ((7, 7), (25, 7), (7, 25), (25, 25)):
            p.drawLine(pt(x, y), pt(x + (5 if x < 16 else -5), y + (5 if y < 16 else -5)))
    elif kind == "group":
        p.setPen(pen(orange, 1.6))
        p.setBrush(QColor(42, 167, 214, 90))
        p.drawRect(QRectF(4, 6, 15, 15))
        p.drawEllipse(QRectF(12, 12, 16, 16))
    elif kind == "sequence":
        p.setPen(pen(blue, 1.4))
        for x, y in ((7, 24), (16, 8), (25, 24)):
            p.drawEllipse(pt(x, y), 4, 4)
        p.setPen(pen(orange, 1.8))
        for (x0, y0), (x1, y1) in (((9, 20.5), (13.5, 12)), ((18.5, 12), (23, 20.5))):
            p.drawLine(pt(x0, y0), pt(x1, y1))
            ang = math.atan2(y1 - y0, x1 - x0)
            for s in (1, -1):
                p.drawLine(pt(x1, y1), pt(x1 - 4 * math.cos(ang + s * 0.6), y1 - 4 * math.sin(ang + s * 0.6)))
    elif kind == "clear_cal":
        p.setPen(pen(theme.RULER, 2.4))
        p.drawLine(pt(3, 22), pt(24, 22))
        p.setPen(pen("#1f9d6a", 1.3))
        for x in range(4, 25, 4):
            p.drawLine(pt(x, 22), pt(x, 17))
        p.setPen(pen("#d9412b", 2.6))
        p.drawLine(pt(19, 4), pt(29, 14))
        p.drawLine(pt(29, 4), pt(19, 14))
    elif kind == "redo":
        p.setPen(pen(blue, 2.2))
        path = QPainterPath(pt(23, 13))
        path.cubicTo(pt(17, 6), pt(5, 8), pt(7, 21))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(blue)
        p.drawPolygon(QPolygonF([pt(28, 9), pt(20, 18), pt(19, 8)]))


def tool_icon(kind: str) -> QIcon:
    """Crisp icon at ribbon sizes (16 and 32 px, plus 2× for high-DPI screens)."""
    ic = QIcon()
    for size in (16, 32, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 32, size / 32)
        _paint_tool(p, kind)
        p.end()
        ic.addPixmap(pm)
    return ic
