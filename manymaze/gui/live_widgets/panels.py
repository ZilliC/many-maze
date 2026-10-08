"""ANY-maze style test panels: toolbar, title, camera image with the apparatus, time slider, Session log /
Video / Zones tabs; the grid that lays them out and its settings."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFrame, QGraphicsEllipseItem,
                               QGraphicsItemGroup, QGraphicsLineItem, QGraphicsPathItem, QGraphicsSimpleTextItem,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QMenu, QSizePolicy, QSlider,
                               QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
                               QWidget)

from .. import theme
from ..icons import icon
from ..widgets import FrameView, cv_to_qpixmap, fmt_time, shape_path


def table_item(text, align_right=False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it

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
            items = (table_item(name), table_item(f"{tt:.1f}", True), table_item(str(n), True),
                     table_item("—" if lat is None else f"{lat:.1f}", True))
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


# (columns, rows of one screenful) — the rows are only indicative: every panel is shown, in as many rows as needed.
# "auto": a near-square grid for any number of panels (ANY-maze: up to 40 apparatus at once).
LAYOUTS = {"1": (1, 1), "2x1": (2, 1), "2x2": (2, 2), "3x2": (3, 2), "4x3": (4, 3), "5x4": (5, 4), "6x5": (6, 5),
           "8x5": (8, 5), "8x6": (8, 6), "auto": (0, 0)}
LAYOUT_LABELS = {"1": "One apparatus", "2x1": "2 × 1", "2x2": "2 × 2", "3x2": "3 × 2", "4x3": "4 × 3",
                 "5x4": "5 × 4", "6x5": "6 × 5", "8x5": "8 × 5 (40 apparatus)", "8x6": "8 × 6 (48)",
                 "auto": "Automatic (fit all)"}


def grid_columns(layout_key: str, n: int) -> int:
    """Columns of the panel grid for n panels shown with a layout."""
    cols = LAYOUTS.get(layout_key, LAYOUTS["2x2"])[0]
    if not cols:  # automatic: near-square
        cols = math.ceil(math.sqrt(max(1, n)))
    return max(1, min(cols, max(1, n)))


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
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        ids = list(self.panels)
        self.empty.setVisible(not ids)
        if self.layout_key == "1":
            show = [self.current] if self.current in self.panels else ids[:1]
        else:
            show = ids
        cols = grid_columns(self.layout_key, len(show))
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
