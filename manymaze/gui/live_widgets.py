"""Widgets of the Run tests page: ANY-maze style test panels (toolbar, title, camera image with the apparatus,
time slider, Session log / Video / Zones tabs) laid out in a grid, the real-time monitor (zone statistics, live
chart, I/O status, warnings), the camera options dialog and the observation-only (TakeNote) panel."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QFrame, QGraphicsEllipseItem, QGraphicsItemGroup, QGraphicsLineItem,
                               QGraphicsPathItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QListWidget, QMenu, QPushButton, QSizePolicy, QSlider,
                               QSpinBox, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from ..core.camera import CameraView, merge_frames
from ..core.live import LiveStats
from . import theme
from .icons import icon
from .scoring_pad import ScoringPad
from .widgets import FrameView, cv_to_qpixmap, fmt_time, shape_path


def _item(text, align_right=False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it


# ====================================================================== live chart
class LiveChart(QWidget):
    """A light-weight scrolling line chart (QPainter, no matplotlib) of one parameter over the last N seconds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t = np.zeros(0)
        self.y = np.zeros(0)
        self.label = ""
        self.unit = ""
        self.window = 60.0
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_data(self, t, y, label="", unit="", window=60.0):
        self.t, self.y = np.asarray(t, float), np.asarray(y, float)
        self.label, self.unit, self.window = label, unit, window
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        r = QRectF(self.rect()).adjusted(38, 8, -8, -20)
        p.fillRect(self.rect(), pal.base())
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(r)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        ok = np.isfinite(self.y)
        if len(self.t) < 2 or not ok.any():
            p.setPen(pal.mid().color())
            p.drawText(r, Qt.AlignCenter, "No data yet")
            return
        t1 = self.t[-1]
        t0 = max(self.t[0], t1 - self.window)
        if t1 - t0 < 1e-6:
            t0 = t1 - 1
        lo, hi = float(np.nanmin(self.y[ok])), float(np.nanmax(self.y[ok]))
        lo = min(lo, 0.0)
        if hi - lo < 1e-9:
            hi = lo + 1.0
        hi += 0.08 * (hi - lo)
        sx = lambda t: r.left() + (t - t0) / (t1 - t0) * r.width()
        sy = lambda v: r.bottom() - (v - lo) / (hi - lo) * r.height()
        path = QPainterPath()
        started = False
        for t, v in zip(self.t, self.y):
            if t < t0 or not math.isfinite(v):
                started = False
                continue
            pt = QPointF(sx(t), sy(v))
            if started:
                path.lineTo(pt)
            else:
                path.moveTo(pt)
                started = True
        p.setPen(QPen(QColor("#2563eb"), 1.6))
        p.drawPath(path)
        p.setPen(pal.text().color())
        p.drawText(QRectF(0, r.top() - 4, 35, 14), Qt.AlignRight, f"{hi:.3g}")
        p.drawText(QRectF(0, r.bottom() - 10, 35, 14), Qt.AlignRight, f"{lo:.3g}")
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignLeft, fmt_time(t0))
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignRight, fmt_time(t1))
        last = self.y[ok][-1]
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 16), Qt.AlignHCenter,
                   f"{self.label}: {last:.3g} {self.unit}".strip())


# ====================================================================== monitor
class MonitorPanel(QWidget):
    """Real-time monitoring of one live session: state, distance / speed / freezing, per-zone statistics, a live
    chart, the I/O device status and warnings.  Call :meth:`refresh` at ≤ 5 Hz."""

    CHART_UNITS = {"speed": "{u}/s", "distance": "{u}", "motion": "%", "detected": "", "freezing": ""}

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 4, 10, 8)
        v.setSpacing(6)
        self.title = QLabel("No live test")
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        v.addWidget(self.title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(0)
        self.vals: dict[str, QLabel] = {}
        for i, (k, cap) in enumerate((("distance", "Distance"), ("speed", "Speed"), ("state", "Animal"),
                                      ("zone", "Zone"))):
            c = QLabel(cap)
            c.setObjectName("Hint")
            c.setStyleSheet("font-size:11px")
            val = ElidedLabel("—")
            val.setStyleSheet("font-size:14px")
            grid.addWidget(c, 0, i)
            grid.addWidget(val, 1, i)
            self.vals[k] = val
        v.addLayout(grid)

        self.zones = QTableWidget(0, 4)
        self.zones.setHorizontalHeaderLabels(["Zone", "Time (s)", "Entries", "Latency (s)"])
        self.zones.verticalHeader().hide()
        self.zones.verticalHeader().setDefaultSectionSize(24)
        self.zones.setShowGrid(False)
        self.zones.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.zones.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.zones.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.zones.setMinimumHeight(110)
        v.addWidget(self.zones, 2)

        row = QHBoxLayout()
        row.addWidget(QLabel("Chart"))
        self.param = QComboBox()
        for k, lbl in LiveStats.CHART_PARAMS.items():
            self.param.addItem(lbl, k)
        self.window = QComboBox()
        for c in (self.param, self.window):
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(6)
        for s in (30, 60, 120, 300):
            self.window.addItem(f"last {s} s", float(s))
        self.window.setCurrentIndex(1)
        row.addWidget(self.param, 1)
        row.addWidget(self.window)
        v.addLayout(row)
        self.chart = LiveChart()
        v.addWidget(self.chart, 2)

        io = QGroupBox("I/O devices")
        iv = QVBoxLayout(io)
        iv.setContentsMargins(6, 4, 6, 4)
        self.io_note = QLabel("No I/O devices")
        self.io_note.setObjectName("Hint")
        self.io = QTableWidget(0, 4)
        self.io.setHorizontalHeaderLabels(["Device", "Channel", "Kind", "Value"])
        self.io.verticalHeader().hide()
        self.io.verticalHeader().setDefaultSectionSize(24)
        self.io.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.io.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.io.setMaximumHeight(110)
        self.io.hide()
        iv.addWidget(self.io_note)
        iv.addWidget(self.io)
        v.addWidget(io)

        wb = QGroupBox("Warnings")
        wv = QVBoxLayout(wb)
        wv.setContentsMargins(6, 4, 6, 4)
        self.warnings = QListWidget()
        self.warnings.setMaximumHeight(90)
        wv.addWidget(self.warnings)
        v.addWidget(wb)
        self._n_warn = 0

    def clear(self, title: str = "No live test"):
        self.title.setText(title)
        for lbl in self.vals.values():
            lbl.setText("—")
        self.zones.setRowCount(0)
        self.chart.set_data([], [])

    def refresh(self, session, title: str = "", devices=None, warnings: list[str] | None = None):
        if session is None:
            self.clear(title or "No live test")
        else:
            self.title.setText(title or session.name or "Live test")
            st = getattr(session, "stats", None)
            if st is None:
                self.vals["distance"].setText("—")
                self.vals["speed"].setText("—")
                self.vals["state"].setText(f"{len(session.events)} events")
                self.vals["zone"].setText("no camera")
                self.zones.setRowCount(0)
                self.chart.set_data([], [])
            else:
                with session.lock:
                    rows = st.rows()
                    param = self.param.currentData()
                    win = self.window.currentData() or 60.0
                    t, y = st.series(param, win)
                    zones = st.current_zones()
                    det, frz, imm = st.detected, st.freezing, st.immobile
                    dist, spd, unit = st.distance, st.speed, st.unit
                self.vals["distance"].setText(f"{dist:.1f} {unit}")
                self.vals["speed"].setText(f"{spd:.1f} {unit}/s")
                self.vals["state"].setText("not detected" if not det else
                                           "freezing" if frz else "immobile" if imm else "moving")
                inner = [z for z in zones if z != "Arena"] or zones
                self.vals["zone"].setText(", ".join(inner[:2]) if inner else "—")
                if self.zones.rowCount() != len(rows):
                    self.zones.setRowCount(len(rows))
                for r, (name, tt, n, lat) in enumerate(rows):
                    self.zones.setItem(r, 0, _item(name))
                    self.zones.setItem(r, 1, _item(f"{tt:.1f}", True))
                    self.zones.setItem(r, 2, _item(str(n), True))
                    self.zones.setItem(r, 3, _item("—" if lat is None else f"{lat:.1f}", True))
                self.chart.set_data(t, y, self.param.currentText(),
                                    self.CHART_UNITS.get(param, "").format(u=unit), win)
        self._refresh_io(devices)
        ws = list(warnings or [])
        if len(ws) != self._n_warn:
            self.warnings.clear()
            self.warnings.addItems(ws[-200:])
            self.warnings.scrollToBottom()
            self._n_warn = len(ws)

    def _refresh_io(self, devices):
        status = None
        if devices is not None:
            try:
                status = list(devices.status())
            except Exception as e:
                self.io_note.setText(f"I/O status unavailable: {e}")
                status = None
        if not status:
            if devices is None:
                self.io_note.setText("No I/O devices")
            self.io_note.show()
            self.io.hide()
            return
        self.io_note.hide()
        self.io.show()
        self.io.setRowCount(len(status))
        for r, row in enumerate(status):
            for c, val in enumerate(list(row)[:4]):
                self.io.setItem(r, c, _item("" if val is None else f"{val:g}" if isinstance(val, float) else str(val)))


# ====================================================================== test panels (ANY-maze style)
STATE_STYLE = {
    "idle": ("Not armed", "#7b8794"),
    "preview": ("Preview", "#3f8fd2"),
    "waiting": ("Waiting", "#e39b1b"),
    "running": ("Running", "#d64541"),
    "paused": ("Paused", "#c08a00"),
    "finished": ("Finished", "#6f52b5"),
}
ZONE_FILL = QColor(76, 175, 80, 95)  # zone the animal is in (ANY-maze green)


def short_time(t: float) -> str:
    """0:38 style test time (as in the title of ANY-maze's test panels)."""
    t = max(0.0, float(t or 0.0))
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}" if m < 60 else f"{m // 60}:{m % 60:02d}:{s:02d}"


class ElidedLabel(QLabel):
    """A one-line label that elides its text (with …) to the available width."""

    def __init__(self, text: str = "", mode=Qt.ElideRight, parent=None):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)
        self.setText(text)

    def setText(self, text: str):
        self._full = text or ""
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width()))
        QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")


class LiveView(FrameView):
    """The live camera image on a light background with the apparatus drawn on top in ANY-maze orange; the zones
    the animal is in are filled green.  ``focus`` restricts the view to one apparatus of a shared camera image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(QColor("#ffffff")))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(120, 90)
        self.message = ""
        self.focus: QRectF | None = None
        self.scale_to_fit = True
        self.show_zones = True
        self.show_labels = False
        self.show_apparatus = True
        self.apparatus = None
        self._group: QGraphicsItemGroup | None = None
        self._zone_items: dict[str, QGraphicsPathItem] = {}
        self._zone_area: dict[str, float] = {}
        self._labels: list[QGraphicsSimpleTextItem] = []
        self._active: set[str] = set()

    # ---- image
    def set_pixmap(self, pix: QPixmap):
        self.pixmap_item.setPixmap(pix)
        size = (pix.width(), pix.height())
        if self.frame_size != size:
            self.frame_size = size
            self.scene().setSceneRect(QRectF(-20, -20, size[0] + 40, size[1] + 40))
            self._update_group_visibility()
            self.fit()
            self.viewport().update()

    def set_frame(self, frame: np.ndarray | None):
        if frame is not None:
            self.set_pixmap(cv_to_qpixmap(frame))

    def set_message(self, text: str):
        self.message = text
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect):
        if self.frame_size is None and self.message:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor(theme.MUTED))
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() + 1)
            painter.setFont(f)
            r = QRectF(self.viewport().rect()).adjusted(30, 20, -30, -20)
            painter.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self.message)
            painter.restore()

    # ---- fitting
    def set_focus(self, rect: QRectF | None):
        self.focus = rect
        self.fit()

    def set_scale_to_fit(self, on: bool):
        self.scale_to_fit = on
        self._auto_fit = True
        self.fit()

    def fit(self):
        if not self.frame_size or getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            r = self.focus if self.focus is not None else self.sceneRect()
            if self.scale_to_fit:
                self.fitInView(r, Qt.KeepAspectRatio)
            else:
                self.resetTransform()
                self.centerOn(r.center())
        finally:
            self._fitting = False
        self._auto_fit = True

    # ---- apparatus overlay
    def set_apparatus(self, app):
        if app is self.apparatus and self._group is not None:
            return
        if self._group is not None:
            self.scene().removeItem(self._group)
            self._group = None
        self.apparatus = app
        self._zone_items, self._zone_area, self._labels, self._active = {}, {}, [], set()
        if app is None:
            return
        g = QGraphicsItemGroup()
        g.setZValue(10)
        orange = QColor(theme.APPARATUS)

        def pen(width, style=Qt.SolidLine, col=orange):
            p = QPen(col)
            p.setCosmetic(True)
            p.setWidthF(width)
            p.setStyle(style)
            return p

        if app.arena is not None:
            it = QGraphicsPathItem(shape_path(app.arena))
            it.setPen(pen(2.2))
            g.addToGroup(it)
        for z in app.zones:
            it = QGraphicsPathItem(shape_path(z.shape))
            it.setPen(pen(1.5, Qt.DashLine if z.hidden else Qt.SolidLine))
            g.addToGroup(it)
            self._zone_items[z.name] = it
            try:
                xy = np.asarray(z.shape.polygon(), float)
                self._zone_area[z.name] = 0.5 * abs(float(np.dot(xy[:, 0], np.roll(xy[:, 1], 1))
                                                          - np.dot(xy[:, 1], np.roll(xy[:, 0], 1))))
            except Exception:
                self._zone_area[z.name] = math.inf
            cx, cy = z.shape.centroid()
            tx = QGraphicsSimpleTextItem(z.name)
            tx.setBrush(QBrush(QColor("#ffffff")))
            tx.setPen(pen(0.6, col=QColor(0, 0, 0, 170)))
            tx.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            br = tx.boundingRect()
            tx.setPos(cx, cy)
            tx.setTransformOriginPoint(br.center())
            tx.setVisible(self.show_labels)
            g.addToGroup(tx)
            self._labels.append(tx)
        for p in app.points:
            r = 4
            it = QGraphicsEllipseItem(p.x - r, p.y - r, 2 * r, 2 * r)
            it.setBrush(QBrush(orange))
            it.setPen(QPen(Qt.NoPen))
            g.addToGroup(it)
        for ln in app.lines:
            it = QGraphicsLineItem(ln.x1, ln.y1, ln.x2, ln.y2)
            it.setPen(pen(1.8))
            g.addToGroup(it)
        self.scene().addItem(g)
        self._group = g
        self._update_group_visibility()

    def _update_group_visibility(self):
        if self._group is not None:  # no apparatus floating in an empty view before the first image
            self._group.setVisible(self.show_apparatus and self.frame_size is not None)

    def set_active_zones(self, names):
        """Fill the zone the animal is in (the smallest one when zones are nested, e.g. a corner of the
        periphery of the arena)."""
        act = [n for n in (names or []) if n in self._zone_items] if self.show_zones else []
        act = {min(act, key=lambda n: self._zone_area.get(n, math.inf))} if act else set()
        if act == self._active:
            return
        self._active = act
        for name, it in self._zone_items.items():
            it.setBrush(QBrush(ZONE_FILL) if name in act else QBrush(Qt.NoBrush))

    def set_indicators(self, zones: bool | None = None, labels: bool | None = None):
        if zones is not None:
            self.show_zones = zones
            if not zones:
                self.set_active_zones([])
        if labels is not None:
            self.show_labels = labels
            for t in self._labels:
                t.setVisible(labels)

    def set_apparatus_visible(self, on: bool):
        self.show_apparatus = on
        self._update_group_visibility()


PANEL_QSS = f"""
QFrame#TestPanel {{ background: white; border: 1px solid #dcdcdc; }}
QWidget#PanelHead {{ background: #f5f5f5; }}
QWidget#PanelHead[selected="true"] {{ background: #e6eef9; }}
QToolButton#PanelTool {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 2px; }}
QToolButton#PanelTool:hover {{ background: {theme.HOVER}; border-color: #c5d7ef; }}
QToolButton#PanelTool:checked {{ background: {theme.SELECTION}; border-color: #8fb0de; }}
QToolButton#PanelTool[popupMode="1"] {{ padding-right: 12px; }}
QLabel#PanelTitle {{ color: {theme.TEXT}; font-size: 13px; }}
QLabel#PanelTitle[large="true"] {{ color: {theme.TEXT}; font-size: 20px; font-weight: 300; }}
QLabel#PanelSource {{ color: {theme.MUTED}; }}
QLabel#PanelCaption {{ color: {theme.MUTED}; font-size: 11px; }}
QLabel#PanelValue {{ color: {theme.TEXT}; }}
QLabel#PanelTime {{ color: {theme.TEXT}; font-size: 13px; }}
QFrame#PanelSep {{ color: #d9d9d9; }}
QTabBar#PanelTabs {{ background: white; }}
QTabBar#PanelTabs::tab {{ background: transparent; border: 1px solid transparent; border-top: none;
    padding: 3px 10px; margin: 0 1px; color: {theme.TEXT}; }}
QTabBar#PanelTabs::tab:selected {{ color: {theme.ACCENT}; border-color: {theme.BORDER}; background: white; }}
QTabBar#PanelTabs::tab:hover:!selected {{ background: {theme.HOVER}; }}
QSlider#PanelSlider::groove:horizontal {{ height: 6px; background: #dcdcdc; border-radius: 3px; }}
QSlider#PanelSlider::sub-page:horizontal {{ background: #7a7a7a; border-radius: 3px; }}
QSlider#PanelSlider::handle:horizontal {{ background: white; border: 1px solid #8a8a8a; width: 10px;
    margin: -4px 0; border-radius: 5px; }}
QListWidget#PanelLog, QTableWidget#PanelZones {{ border: none; }}
QListWidget#PanelLog::item {{ padding: 2px 6px; }}
"""


class _TitleRow(QWidget):
    """Title, grey source and ▾ button of a panel: the title gets the room it needs, the source what is left."""

    parts: tuple = ()

    def sizeHint(self):
        h = max((w.sizeHint().height() for w in self.parts), default=20)
        return QSize(200, h)

    def minimumSizeHint(self):
        return QSize(60, self.sizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.arrange()

    def arrange(self):
        if not self.parts:
            return
        title, source, menu = self.parts
        w, h = self.width(), self.height()
        mw = menu.sizeHint().width()
        menu.setGeometry(w - mw, (h - menu.sizeHint().height()) // 2, mw, menu.sizeHint().height())
        room = w - mw - 8
        src_min = 0 if source.isHidden() else min(150, room // 4)
        need = title.fontMetrics().horizontalAdvance(title.text()) + 8
        tw = max(30, min(need, room - src_min - 12))
        title.setGeometry(0, 0, tw, h)
        title.setText(title.text())  # re-elide to the new width
        if not source.isHidden():
            sx = tw + 12
            source.setGeometry(sx, 0, max(0, room - sx), h)
            source.setText(source.text())


class TestPanel(QFrame):
    """One test, as a panel of ANY-maze's Test page: a small toolbar (start ▾, pause, stop, undo, record indicator),
    the title "Apparatus: Animal …, Stage trial n - 0:38" with the (elided, grey) source, the camera image with the
    apparatus, a time slider and bottom tabs "Session log | Video | Zones"."""

    clicked = Signal()
    start_clicked = Signal()  # ▶: arm, or start now when armed
    start_now = Signal()  # ▶ ▾ Start now
    arm_clicked = Signal()  # ▶ ▾ Arm and wait for the start condition
    pause_clicked = Signal()
    stop_clicked = Signal()
    undo_clicked = Signal()

    TABS = ("Session log", "Video", "Zones")

    def __init__(self, single: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("TestPanel")
        self.setStyleSheet(PANEL_QSS)
        self.single = single
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- header: toolbar and title
        self.head = QWidget()
        self.head.setObjectName("PanelHead")
        self.head.setAttribute(Qt.WA_StyledBackground, True)
        hv = QVBoxLayout(self.head)
        hv.setContentsMargins(6, 3, 6, 4)
        hv.setSpacing(2)
        self.toolbar = QWidget()
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(1)
        size = 22 if single else 18

        def tool(name, tip, signal=None, checkable=False):
            b = QToolButton()
            b.setObjectName("PanelTool")
            b.setIcon(icon(name))
            b.setIconSize(QSize(size, size))
            b.setToolTip(tip)
            b.setText(tip)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCheckable(checkable)
            if signal is not None:
                b.clicked.connect(signal.emit)
            tb.addWidget(b)
            return b

        def sep():
            f = QFrame()
            f.setObjectName("PanelSep")
            f.setFrameShape(QFrame.VLine)
            f.setFixedHeight(size)
            tb.addSpacing(3)
            tb.addWidget(f)
            tb.addSpacing(3)

        self.start_btn = tool("play", "Arm / Start test", self.start_clicked)
        self.start_menu = QMenu(self.start_btn)
        self.start_menu.addAction(icon("play"), "Start now", self.start_now.emit)
        self.start_menu.addAction(icon("timer"), "Arm and wait for the start condition", self.arm_clicked.emit)
        self.start_btn.setMenu(self.start_menu)
        self.start_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.pause_btn = tool("pause", "Pause", self.pause_clicked)
        self.stop_btn = tool("stop", "Stop", self.stop_clicked)
        sep()
        self.undo_btn = tool("undo", "Undo the last scored event", self.undo_clicked)
        sep()
        # single test only: camera image on / off, empty-arena background, next test (set up by the page)
        self.camera_btn = tool("video", "Show the camera image (preview)", checkable=True)
        self.bg_btn = tool("image_capture", "Capture the empty-arena background")
        self.extra_sep = QFrame()
        self.extra_sep.setObjectName("PanelSep")
        self.extra_sep.setFrameShape(QFrame.VLine)
        self.extra_sep.setFixedHeight(size)
        tb.addSpacing(3)
        tb.addWidget(self.extra_sep)
        tb.addSpacing(3)
        self.rec_lbl = QLabel()
        self.rec_lbl.setFixedSize(size + 6, size + 4)
        self.rec_lbl.setAlignment(Qt.AlignCenter)
        tb.addWidget(self.rec_lbl)
        self.next_btn = tool("forward", "Next test")
        tb.addStretch()
        self.state_lbl = QLabel()
        self.state_lbl.setAlignment(Qt.AlignCenter)
        tb.addWidget(self.state_lbl)
        for w in (self.camera_btn, self.bg_btn, self.extra_sep, self.next_btn):
            w.setVisible(single)
        hv.addWidget(self.toolbar)

        self.title_row = _TitleRow()
        self.title = ElidedLabel(parent=self.title_row)
        self.title.setObjectName("PanelTitle")
        self.title.setProperty("large", single)
        self.source = ElidedLabel(mode=Qt.ElideMiddle, parent=self.title_row)
        self.source.setObjectName("PanelSource")
        self.menu_btn = QToolButton(self.title_row)
        self.menu_btn.setObjectName("PanelTool")
        self.menu_btn.setIcon(icon("chevron_down"))
        self.menu_btn.setIconSize(QSize(10, 10))
        self.menu_btn.setToolTip("Options of this test")
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.menu_btn.setStyleSheet("QToolButton::menu-indicator{image:none;}")
        self.menu = QMenu(self.menu_btn)
        self.menu_btn.setMenu(self.menu)
        self.title_row.parts = (self.title, self.source, self.menu_btn)
        hv.addWidget(self.title_row)
        v.addWidget(self.head)

        # ---- body: one page per bottom tab
        self.stack = QStackedWidget()
        self.log = QListWidget()
        self.log.setObjectName("PanelLog")
        self.log.setWordWrap(True)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack.addWidget(self.log)

        video = QWidget()
        vv = QVBoxLayout(video)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(0)
        self.view = LiveView()
        self.view.clicked.connect(lambda *_: self.clicked.emit())
        vv.addWidget(self.view, 1)
        self.time_row = QWidget()
        tr = QHBoxLayout(self.time_row)
        tr.setContentsMargins(10, 3, 10, 3)
        tr.setSpacing(8)
        self.elapsed_lbl = QLabel("00:00.00")
        self.elapsed_lbl.setObjectName("PanelTime")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("PanelSlider")
        self.slider.setRange(0, 1000)
        self.slider.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.remaining_lbl = QLabel("")
        self.remaining_lbl.setObjectName("PanelTime")
        self.remaining_lbl.setToolTip("Time remaining")
        tr.addWidget(self.elapsed_lbl)
        tr.addWidget(self.slider, 1)
        tr.addWidget(self.remaining_lbl)
        vv.addWidget(self.time_row)
        self.info_row = QWidget()
        ir = QHBoxLayout(self.info_row)
        ir.setContentsMargins(10, 0, 10, 4)
        ir.setSpacing(6)
        self.vals: dict[str, QLabel] = {"elapsed": self.elapsed_lbl, "remaining": self.remaining_lbl}
        for key, cap in (("zone", "Zone"), ("distance", "Distance"), ("events", "Events")):
            c = QLabel(cap)
            c.setObjectName("PanelCaption")
            val = ElidedLabel("—") if key == "zone" else QLabel("—")
            val.setObjectName("PanelValue")
            if key != "zone":
                ir.addSpacing(8)
            ir.addWidget(c)
            ir.addWidget(val, 1 if key == "zone" else 0)
            self.vals[key] = val
        vv.addWidget(self.info_row)
        self.stack.addWidget(video)

        self.zones = QTableWidget(0, 4)
        self.zones.setObjectName("PanelZones")
        self.zones.setHorizontalHeaderLabels(["Zone", "Time (s)", "Entries", "Latency (s)"])
        self.zones.verticalHeader().hide()
        self.zones.verticalHeader().setDefaultSectionSize(24)
        self.zones.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.zones.setSelectionMode(QAbstractItemView.NoSelection)
        self.zones.setShowGrid(False)
        hh = self.zones.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.stack.addWidget(self.zones)
        v.addWidget(self.stack, 1)

        self.tabs = QTabBar()
        self.tabs.setObjectName("PanelTabs")
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        for t in self.TABS:
            self.tabs.addTab(t)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.tabs.setCurrentIndex(1)
        v.addWidget(self.tabs)

        self.selected = False
        self._shown = None
        self.set_recording(False)
        self.set_state("idle")

    # ---- content
    def set_title(self, text: str):
        self.title.setText(text)
        self._fit_title()

    def set_source(self, text: str, tip: str = ""):
        self.source.setText(text)
        if tip:
            self.source.setToolTip(tip)

    def _fit_title(self):
        self.title_row.arrange()

    def set_selected(self, on: bool):
        if on != self.selected:
            self.selected = on
            self.head.setProperty("selected", on)
            self.head.style().unpolish(self.head)
            self.head.style().polish(self.head)

    def set_recording(self, on: bool, path: str | None = None):
        key = (on, path)
        if key == getattr(self, "_rec", None):
            return
        self._rec = key
        n = 18 if self.single else 15
        self.rec_lbl.setPixmap(icon("record").pixmap(n, n, QIcon.Normal if on else QIcon.Disabled))
        self.rec_lbl.setToolTip(f"Recording to {path}" if on and path else "Recording" if on else
                                "Not recording")

    def set_state(self, state: str, elapsed: float | None = None, duration: float = 0.0):
        if state != self._shown:
            self._shown = state
            text, col = STATE_STYLE.get(state, STATE_STYLE["idle"])
            self.state_lbl.setText(text)
            self.state_lbl.setStyleSheet(f"background:{col};color:white;border-radius:3px;padding:1px 8px;"
                                         f"font-size:{12 if self.single else 11}px;font-weight:600")
        if elapsed is None:
            self.slider.setValue(0)
            return
        self.elapsed_lbl.setText(fmt_time(elapsed))
        if duration:
            self.remaining_lbl.setText("−" + fmt_time(max(0.0, duration - elapsed)))
            self.slider.setValue(int(round(1000 * min(1.0, elapsed / duration))))
        else:
            self.remaining_lbl.setText("∞")
            self.slider.setValue(0)

    def reset_values(self):
        self.elapsed_lbl.setText("00:00.00")
        self.remaining_lbl.setText("")
        self.slider.setValue(0)
        for k in ("zone", "distance", "events"):
            self.vals[k].setText("—")

    def append_log(self, line: str):
        self.log.addItem(line)
        if self.log.count() > 2000:
            self.log.takeItem(0)
        self.log.scrollToBottom()

    def set_zone_rows(self, rows, current=()):
        """rows = [(zone, time s, entries, latency s or None), ...]; the zones the animal is in are highlighted."""
        cur = set(current or [])
        if self.zones.rowCount() != len(rows):
            self.zones.setRowCount(len(rows))
        hl = QColor(ZONE_FILL)
        hl.setAlpha(60)
        for r, (name, tt, n, lat) in enumerate(rows):
            items = (_item(name), _item(f"{tt:.1f}", True), _item(str(n), True),
                     _item("—" if lat is None else f"{lat:.1f}", True))
            for c, it in enumerate(items):
                if name in cur:
                    it.setBackground(hl)
                self.zones.setItem(r, c, it)

    def apply_settings(self, d: dict):
        """Panel settings (ribbon View ▸ Panel settings): which parts of the panel are shown."""
        self.toolbar.setVisible(d.get("toolbar", True))
        self.source.setVisible(d.get("source", True))
        self._fit_title()
        self.time_row.setVisible(d.get("slider", True))
        self.info_row.setVisible(d.get("stats", True))
        self.tabs.setVisible(d.get("tabs", True))
        if not d.get("tabs", True):
            self.tabs.setCurrentIndex(1)

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


LAYOUTS = {"1": (1, 1), "2x1": (2, 1), "2x2": (2, 2), "3x2": (3, 2)}
LAYOUT_LABELS = {"1": "One apparatus", "2x1": "2 × 1", "2x2": "2 × 2", "3x2": "3 × 2"}


class PanelGrid(QWidget):
    """The test panels of a session laid out in a grid (ribbon: View ▸ Apparatus layout).  With the "1" layout only
    the current panel is shown."""

    panel_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelGrid")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#PanelGrid{{background:{theme.BORDER};}}")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.panels: dict[int, TestPanel] = {}
        self.layout_key = "2x2"
        self.current: int | None = None
        self.empty = QLabel("Add a camera or a video file (Session ▸ Add camera / video source), then a test panel "
                            "for every apparatus (Add test panel).")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"background:white;color:{theme.MUTED};font-size:14px;padding:30px")
        self.grid.addWidget(self.empty, 0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    @property
    def views(self) -> dict[int, LiveView]:
        return {k: p.view for k, p in self.panels.items()}

    def set_panels(self, panels: dict[int, TestPanel]):
        for k, p in list(self.panels.items()):
            if panels.get(k) is not p:
                self.grid.removeWidget(p)
                p.setParent(None)
                p.deleteLater()
        self.panels = dict(panels)
        if self.current not in self.panels:
            self.current = next(iter(self.panels), None)
        self.arrange()

    def set_layout_key(self, key: str):
        self.layout_key = key if key in LAYOUTS else "2x2"
        self.arrange()

    def set_current(self, key: int | None):
        if key not in self.panels:
            return
        self.current = key
        for k, p in self.panels.items():
            p.set_selected(k == key and len(self.panels) > 1)
        if self.layout_key == "1":
            self.arrange()

    def arrange(self):
        for p in self.panels.values():
            self.grid.removeWidget(p)
        for c in range(6):
            self.grid.setColumnStretch(c, 0)
        for r in range(12):
            self.grid.setRowStretch(r, 0)
        ids = list(self.panels)
        self.empty.setVisible(not ids)
        if self.layout_key == "1":
            show = [self.current] if self.current in self.panels else ids[:1]
        else:
            show = ids
        cols = min(LAYOUTS[self.layout_key][0], max(1, len(show)))
        for i, k in enumerate(ids):
            p = self.panels[k]
            if k in show:
                j = show.index(k)
                self.grid.addWidget(p, j // cols, j % cols)
                p.show()
            else:
                p.hide()
        rows = max(1, math.ceil(len(show) / cols)) if show else 1
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        for r in range(rows):
            self.grid.setRowStretch(r, 1)
        for k, p in self.panels.items():
            p.set_selected(k == self.current and len(self.panels) > 1)


class PanelSettingsDialog(QDialog):
    """What every test panel shows (ribbon: View ▸ Panel settings)."""

    ITEMS = (("toolbar", "Show the toolbar of each test (start, pause, stop…)"),
             ("source", "Show the video source next to the title"),
             ("slider", "Show the time slider"),
             ("stats", "Show the zone, distance and number of events under the image"),
             ("tabs", "Show the Session log, Video and Zones tabs"))

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Panel settings")
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        h = QLabel("Test panels")
        h.setObjectName("SectionTitle")
        v.addWidget(h)
        hint = QLabel("Specify what each test panel shows while tests run.")
        hint.setObjectName("Hint")
        v.addWidget(hint)
        self.checks: dict[str, QCheckBox] = {}
        for k, lbl in self.ITEMS:
            c = QCheckBox(lbl)
            c.setChecked(bool(settings.get(k, True)))
            v.addWidget(c)
            self.checks[k] = c
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addSpacing(8)
        v.addWidget(bb)

    def result_settings(self) -> dict:
        return {k: c.isChecked() for k, c in self.checks.items()}


# ====================================================================== camera options
class CameraOptionsDialog(QDialog):
    """Region of interest (drag a rectangle on the image), digital zoom / pan, rotation, flip and merging with a
    second camera.  ``second_choices`` is a list of (label, source) for the merge."""

    def __init__(self, frame: np.ndarray | None, view: CameraView, second=None, layout: str = "side",
                 second_choices=(), second_frame: np.ndarray | None = None, parent=None, title="Camera options"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        self.raw = frame if frame is not None else np.full((480, 640, 3), 90, np.uint8)
        self.raw2 = second_frame
        self.view = CameraView.from_dict(view.to_dict())
        self._loading = True

        self.src_view = FrameView()
        self.src_view.setMinimumSize(300, 220)
        self.out_view = FrameView()
        self.out_view.setMinimumSize(300, 220)
        self.rect_item = QGraphicsRectItem()
        pen = QPen(QColor("#f59e0b"), 0)
        pen.setCosmetic(True)
        pen.setWidthF(2)
        pen.setStyle(Qt.DashLine)
        self.rect_item.setPen(pen)
        self.rect_item.setZValue(10)
        self.src_view.scene().addItem(self.rect_item)
        self._drag = None
        self.src_view.viewport().installEventFilter(self)

        form = QFormLayout()
        self.rotate = QComboBox()
        for a in (0, 90, 180, 270):
            self.rotate.addItem("None" if not a else f"{a}° clockwise", a)
        self.flip = QComboBox()
        for k, lbl in (("", "None"), ("h", "Mirror left–right"), ("v", "Upside down"), ("hv", "Both")):
            self.flip.addItem(lbl, k)
        self.cx, self.cy, self.cw, self.ch = (QSpinBox() for _ in range(4))
        for s in (self.cx, self.cy, self.cw, self.ch):
            s.setRange(0, 10000)
        crop_row = QHBoxLayout()
        for lbl, s in (("x", self.cx), ("y", self.cy), ("w", self.cw), ("h", self.ch)):
            crop_row.addWidget(QLabel(lbl))
            crop_row.addWidget(s)
        self.full_btn = QPushButton("Whole image")
        self.full_btn.clicked.connect(self._full)
        crop_row.addWidget(self.full_btn)
        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(1.0, 8.0)
        self.zoom.setSingleStep(0.25)
        self.zoom.setSuffix(" ×")
        self.pan_x, self.pan_y = QSlider(Qt.Horizontal), QSlider(Qt.Horizontal)
        for s in (self.pan_x, self.pan_y):
            s.setRange(0, 100)
        self.second = QComboBox()
        self.second.addItem("No (single camera)", None)
        for lbl, src in second_choices:
            self.second.addItem(lbl, src)
        if second is not None:
            i = self.second.findData(second)
            if i < 0:
                self.second.addItem(str(second), second)
                i = self.second.count() - 1
            self.second.setCurrentIndex(i)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Side by side", "side")
        self.layout_combo.addItem("One above the other", "stack")
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(layout)))
        form.addRow("Merge with", self.second)
        form.addRow("Merged layout", self.layout_combo)
        form.addRow("Rotate", self.rotate)
        form.addRow("Flip", self.flip)
        form.addRow("Region", crop_row)
        form.addRow("Digital zoom", self.zoom)
        form.addRow("Pan left–right", self.pan_x)
        form.addRow("Pan up–down", self.pan_y)
        hint = QLabel("Drag a rectangle on the left image to capture only that region. Draw the apparatus on the "
                      "transformed image (right): changing these options later moves the image under the "
                      "apparatus.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:palette(mid)")

        views = QHBoxLayout()
        for cap, w in (("Camera image (drag to select the region)", self.src_view), ("Result", self.out_view)):
            col = QVBoxLayout()
            col.addWidget(QLabel(cap))
            col.addWidget(w, 1)
            views.addLayout(col, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Reset).clicked.connect(self._reset)
        v = QVBoxLayout(self)
        v.addLayout(views, 1)
        v.addLayout(form)
        v.addWidget(hint)
        v.addWidget(bb)

        self._load()
        for w in (self.rotate, self.flip, self.second, self.layout_combo):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.cx, self.cy, self.cw, self.ch):
            w.valueChanged.connect(self._changed)
        self.zoom.valueChanged.connect(self._changed)
        self.pan_x.valueChanged.connect(self._changed)
        self.pan_y.valueChanged.connect(self._changed)
        self._loading = False
        self._changed()

    # ---- values
    def _base(self) -> np.ndarray:
        """Merged, rotated and flipped image (the crop is selected on it)."""
        img = self.raw
        if self.second.currentData() is not None:
            other = self.raw2 if self.raw2 is not None else np.full_like(self.raw, 60)
            img = merge_frames(img, other, self.layout_combo.currentData())
        return CameraView(rotate=self.rotate.currentData(), flip=self.flip.currentData()).apply(img)

    def _load(self):
        v = self.view
        self.rotate.setCurrentIndex(max(0, self.rotate.findData(v.rotate)))
        self.flip.setCurrentIndex(max(0, self.flip.findData(v.flip)))
        h, w = self._base().shape[:2]
        x, y, cw, ch = v.crop or (0, 0, w, h)
        for s, val in zip((self.cx, self.cy, self.cw, self.ch), (x, y, cw, ch)):
            s.setValue(int(val))
        self.zoom.setValue(v.zoom)
        self.pan_x.setValue(int(round(v.pan[0] * 100)))
        self.pan_y.setValue(int(round(v.pan[1] * 100)))

    def result_view(self) -> CameraView:
        h, w = self._base().shape[:2]
        crop = [self.cx.value(), self.cy.value(), self.cw.value(), self.ch.value()]
        if crop == [0, 0, w, h] or crop[2] <= 1 or crop[3] <= 1:
            crop = None
        return CameraView.from_dict({"crop": crop, "zoom": self.zoom.value(), "rotate": self.rotate.currentData(),
                                     "flip": self.flip.currentData(),
                                     "pan": [self.pan_x.value() / 100, self.pan_y.value() / 100]})

    def result(self) -> dict:
        """{"view": CameraView dict, "second": source or None, "layout": "side" | "stack"}."""
        return {"view": self.result_view().to_dict(), "second": self.second.currentData(),
                "layout": self.layout_combo.currentData()}

    def _full(self):
        h, w = self._base().shape[:2]
        self._loading = True
        for s, val in zip((self.cx, self.cy, self.cw, self.ch), (0, 0, w, h)):
            s.setValue(val)
        self._loading = False
        self._changed()

    def _reset(self):
        self.view = CameraView()
        self._loading = True
        self.second.setCurrentIndex(0)
        self._load()
        self._loading = False
        self._full()

    def _changed(self, *_):
        if self._loading:
            return
        base = self._base()
        self.src_view.set_frame(base)
        self.rect_item.setRect(QRectF(self.cx.value(), self.cy.value(), self.cw.value(), self.ch.value()))
        v = self.result_view()
        # the result is the crop / zoom of the already merged, rotated and flipped image
        self.out_view.set_frame(CameraView(crop=v.crop, zoom=v.zoom, pan=v.pan).apply(base))
        self.pan_x.setEnabled(v.zoom > 1.0)
        self.pan_y.setEnabled(v.zoom > 1.0)
        self.layout_combo.setEnabled(self.second.currentData() is not None)

    # ---- rubber band selection of the region
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent

        if obj is self.src_view.viewport():
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                p = self.src_view.mapToScene(ev.position().toPoint())
                self._drag = (p.x(), p.y())
                return True
            if t == QEvent.MouseMove and self._drag is not None:
                p = self.src_view.mapToScene(ev.position().toPoint())
                self.select_region(*self._drag, p.x(), p.y(), final=False)
                return True
            if t == QEvent.MouseButtonRelease and self._drag is not None:
                p = self.src_view.mapToScene(ev.position().toPoint())
                self.select_region(*self._drag, p.x(), p.y())
                self._drag = None
                return True
        return super().eventFilter(obj, ev)

    def select_region(self, x0, y0, x1, y1, final: bool = True):
        h, w = self._base().shape[:2]
        x0, x1 = sorted((int(np.clip(x0, 0, w)), int(np.clip(x1, 0, w))))
        y0, y1 = sorted((int(np.clip(y0, 0, h)), int(np.clip(y1, 0, h))))
        if x1 - x0 < 8 or y1 - y0 < 8:
            if final:
                return
            self.rect_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            return
        self._loading = True
        for s, val in zip((self.cx, self.cy, self.cw, self.ch), (x0, y0, x1 - x0, y1 - y0)):
            s.setValue(val)
        self._loading = False
        if final:
            self._changed()
        else:
            self.rect_item.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))


# ====================================================================== observation only
class ObservationPanel(QFrame):
    """Live observation without a camera, like ANY-maze's TakeNote direct observation mode: a toolbar (start, pause,
    stop), the test title, a big test clock, the scoring keys as on-screen buttons and the list of scored events."""

    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    undo_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TestPanel")
        self.setStyleSheet(PANEL_QSS)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        head.setObjectName("PanelHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        hv = QVBoxLayout(head)
        hv.setContentsMargins(6, 3, 6, 4)
        hv.setSpacing(2)
        tb = QHBoxLayout()
        tb.setSpacing(2)
        self.start_btn = QToolButton()
        self.pause_btn = QToolButton()
        self.stop_btn = QToolButton()
        self.undo_btn = QToolButton()
        for b, name, text, sig in ((self.start_btn, "play", "Start observation", self.start_clicked),
                                   (self.pause_btn, "pause", "Pause", self.pause_clicked),
                                   (self.stop_btn, "stop", "Stop and save", self.stop_clicked),
                                   (self.undo_btn, "undo", "Undo", self.undo_clicked)):
            b.setObjectName("PanelTool")
            b.setIcon(icon(name))
            b.setIconSize(QSize(22, 22))
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(sig.emit)
            tb.addWidget(b)
        self.undo_btn.setToolTip("Undo the last scored event")
        self.undo_btn.setEnabled(False)
        tb.addStretch()
        self.state_pill = QLabel()
        tb.addWidget(self.state_pill)
        hv.addLayout(tb)
        trow = QHBoxLayout()
        self.title = ElidedLabel("Observation")
        self.title.setObjectName("PanelTitle")
        self.title.setProperty("large", True)
        src = ElidedLabel("TakeNote direct observation · no camera")
        src.setObjectName("PanelSource")
        trow.addWidget(self.title, 3)
        trow.addWidget(src, 2)
        hv.addLayout(trow)
        v.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(24, 14, 18, 14)
        body.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(6)
        intro = QLabel("Score the animal's behaviour by direct observation with the keys or the buttons below. "
                       "The test clock runs from Start and stops while paused; the events are saved in the test "
                       "(testing status “scored”).")
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        left.addWidget(intro)
        left.addStretch(1)
        self.clock = QLabel("00:00.00")
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setStyleSheet(f"font-size:64px;font-weight:300;color:{theme.TEXT}")
        left.addWidget(self.clock)
        self.state = QLabel("Not started")
        self.state.setAlignment(Qt.AlignCenter)
        self.state.setStyleSheet(f"font-size:15px;color:{theme.MUTED}")
        left.addWidget(self.state)
        left.addStretch(1)
        kt = QLabel("Keys")
        kt.setObjectName("SectionTitle")
        left.addWidget(kt)
        self.pad = ScoringPad()  # mouse / touch-screen scoring
        left.addWidget(self.pad)
        self.keys = QLabel()
        self.keys.setWordWrap(True)
        self.keys.setObjectName("Hint")
        left.addWidget(self.keys)
        body.addLayout(left, 3)
        right = QVBoxLayout()
        et = QLabel("Events")
        et.setObjectName("SectionTitle")
        right.addWidget(et)
        self.events = QListWidget()
        self.events.setStyleSheet("QListWidget::item{padding:3px 4px;}")
        right.addWidget(self.events, 1)
        body.addLayout(right, 2)
        v.addLayout(body, 1)
        self._pill = None
        self._sig = None
        self._set_pill("idle")

    def set_title(self, text: str):
        self.title.setText(text)

    def _set_pill(self, state):
        if state == self._pill:
            return
        self._pill = state
        text, col = STATE_STYLE.get(state, STATE_STYLE["idle"])
        if state == "idle":
            text = "Not started"
        self.state_pill.setText(text)
        self.state_pill.setStyleSheet(f"background:{col};color:white;border-radius:3px;padding:1px 8px;"
                                      "font-size:12px;font-weight:600")

    def show_session(self, session, duration_s: float = 0.0):
        if session is None:
            self.clock.setText("00:00.00")
            self.state.setText("Not started")
            self.start_btn.setText("Start observation")
            self.start_btn.setIcon(icon("play"))
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._set_pill("idle")
            self.pad.set_active([])
            return
        el = session.elapsed
        self.clock.setText(fmt_time(el))
        st = session.state
        self._set_pill(st)
        rem = f" · {fmt_time(max(0.0, duration_s - el))} left" if duration_s else ""
        self.state.setText({"waiting": "Waiting for the start key", "running": "Running" + rem,
                            "paused": "Paused", "finished": "Finished"}.get(st, st))
        self.start_btn.setText("Resume" if st == "paused" else "Start observation")
        self.start_btn.setIcon(icon("resume" if st == "paused" else "play"))
        self.start_btn.setEnabled(st in ("waiting", "paused"))
        self.pause_btn.setEnabled(st == "running")
        self.stop_btn.setEnabled(st in ("running", "paused", "waiting"))
        self.pad.set_active(list(session.open_states))
        sig = (len(session.events), sum(1 for e in session.events if e.get("t_end") is not None))
        if sig != self._sig:
            self._sig = sig
            self.events.clear()
            for e in session.events:
                end = f" – {fmt_time(e['t_end'])}" if e.get("t_end") is not None else ""
                self.events.addItem(f"{fmt_time(e['t'])}{end}   {e['behaviour']}")
            self.events.scrollToBottom()
