"""Widgets of the Live testing page: real-time monitor (zone statistics, live chart, I/O status, warnings), the
mosaic of camera images, the camera options dialog and the observation-only (no camera) panel."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGraphicsRectItem, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QPushButton, QSizePolicy, QSlider, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.camera import CameraView, merge_frames
from ..core.live import LiveStats
from .widgets import FrameView, fmt_time


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
        v.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel("No live test")
        self.title.setStyleSheet("font-weight:600")
        self.title.setWordWrap(True)
        v.addWidget(self.title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.vals: dict[str, QLabel] = {}
        for i, (k, cap) in enumerate((("distance", "Distance"), ("speed", "Speed"), ("state", "Animal"),
                                      ("zone", "Zone"))):
            c = QLabel(cap)
            c.setStyleSheet("color:palette(mid);font-size:11px")
            val = QLabel("—")
            val.setStyleSheet("font-size:14px;font-weight:600")
            grid.addWidget(c, 0, i)
            grid.addWidget(val, 1, i)
            self.vals[k] = val
        v.addLayout(grid)

        self.zones = QTableWidget(0, 4)
        self.zones.setHorizontalHeaderLabels(["Zone", "Time (s)", "Entries", "Latency (s)"])
        self.zones.verticalHeader().hide()
        self.zones.verticalHeader().setDefaultSectionSize(20)
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
        self.io_note.setStyleSheet("color:palette(mid)")
        self.io = QTableWidget(0, 4)
        self.io.setHorizontalHeaderLabels(["Device", "Channel", "Kind", "Value"])
        self.io.verticalHeader().hide()
        self.io.verticalHeader().setDefaultSectionSize(20)
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


# ====================================================================== mosaic
class MosaicView(QWidget):
    """All live camera images in a grid (one tile per source), with their overlays."""

    tile_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.setObjectName("mosaic")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#mosaic{background:#0f172a}")
        self.views: dict[str, FrameView] = {}
        self.captions: dict[str, QLabel] = {}
        self._tiles: list[QWidget] = []
        self.empty = QLabel("Add a camera (or a video file simulating one) and sessions below, then press "
                            "“Start cameras”.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#94a3b8;font-size:14px;background:#1e293b;padding:6px")
        self.grid.addWidget(self.empty, 0, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_sources(self, sources: list[tuple[str, str]]):
        """(key, caption) for every source, in display order."""
        if [k for k, _ in sources] == list(self.views) and all(self.captions[k].text() == c for k, c in sources):
            return
        for w in self._tiles:
            self.grid.removeWidget(w)
            w.deleteLater()
        self._tiles, self.views, self.captions = [], {}, {}
        self.empty.setVisible(not sources)
        n = len(sources)
        cols = max(1, math.ceil(math.sqrt(n)))
        for i, (key, cap) in enumerate(sources):
            tile = QWidget()
            tv = QVBoxLayout(tile)
            tv.setContentsMargins(0, 0, 0, 0)
            tv.setSpacing(0)
            lbl = QLabel(cap)
            lbl.setStyleSheet("background:#0f172a;color:#e2e8f0;padding:1px 6px;font-size:11px")
            view = FrameView()
            view.setMinimumSize(160, 120)
            view.setFrameShape(FrameView.NoFrame)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.clicked.connect(lambda *_a, k=key: self.tile_clicked.emit(k))
            tv.addWidget(lbl)
            tv.addWidget(view, 1)
            self.grid.addWidget(tile, i // cols, i % cols)
            self._tiles.append(tile)
            self.views[key] = view
            self.captions[key] = lbl
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def set_frame(self, key: str, frame: np.ndarray):
        v = self.views.get(key)
        if v is not None and frame is not None:
            v.set_frame(frame)


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
class ObservationPanel(QWidget):
    """Live observation without a camera (TakeNote): big clock, state, start / pause / stop and scoring buttons."""

    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        intro = QLabel("Observation only: no camera. Score the animal's behaviour by direct observation with the "
                       "keys or the buttons; the test clock runs from Start and stops while paused. The events are "
                       "saved in the test (status “scored”).")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:palette(mid)")
        v.addWidget(intro)
        self.clock = QLabel("00:00.00")
        self.clock.setAlignment(Qt.AlignCenter)
        self.clock.setStyleSheet("font-size:54px;font-weight:700;font-family:monospace")
        v.addWidget(self.clock)
        self.state = QLabel("Not started")
        self.state.setAlignment(Qt.AlignCenter)
        self.state.setStyleSheet("font-size:15px;color:palette(mid)")
        v.addWidget(self.state)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start observation")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop and save")
        for b, sig in ((self.start_btn, self.start_clicked), (self.pause_btn, self.pause_clicked),
                       (self.stop_btn, self.stop_clicked)):
            b.setMinimumHeight(34)
            b.clicked.connect(sig.emit)
            row.addWidget(b)
        v.addLayout(row)
        try:  # the scoring pad of the test viewer (mouse / touch-screen scoring)
            from .pages.testview import ScoringPad
            self.pad = ScoringPad()
        except Exception:  # pragma: no cover - fallback if the test viewer changes
            self.pad = None
        box = QGroupBox("Scoring")
        bv = QVBoxLayout(box)
        if self.pad is not None:
            bv.addWidget(self.pad)
        self.keys = QLabel()
        self.keys.setWordWrap(True)
        self.keys.setStyleSheet("color:palette(mid)")
        bv.addWidget(self.keys)
        v.addWidget(box)
        self.events = QListWidget()
        v.addWidget(self.events, 1)

    def show_session(self, session, duration_s: float = 0.0):
        if session is None:
            self.clock.setText("00:00.00")
            self.state.setText("Not started")
            self.start_btn.setText("Start observation")
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            if self.pad is not None:
                self.pad.set_active([])
            return
        el = session.elapsed
        self.clock.setText(fmt_time(el))
        st = session.state
        rem = f" · {fmt_time(max(0.0, duration_s - el))} left" if duration_s else ""
        self.state.setText({"waiting": "Waiting for the start key", "running": "Running" + rem,
                            "paused": "Paused", "finished": "Finished"}.get(st, st))
        self.start_btn.setText("Resume" if st == "paused" else "Start observation")
        self.start_btn.setEnabled(st in ("waiting", "paused"))
        self.pause_btn.setEnabled(st == "running")
        self.stop_btn.setEnabled(st in ("running", "paused", "waiting"))
        if self.pad is not None:
            self.pad.set_active(list(session.open_states))
        sig = (len(session.events), sum(1 for e in session.events if e.get("t_end") is not None))
        if sig != getattr(self, "_sig", None):
            self._sig = sig
            self.events.clear()
            for e in session.events:
                end = f" – {fmt_time(e['t_end'])}" if e.get("t_end") is not None else ""
                self.events.addItem(f"{fmt_time(e['t'])}{end}  {e['behaviour']}")
            self.events.scrollToBottom()
