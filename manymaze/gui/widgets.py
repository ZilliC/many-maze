"""Shared Qt widgets and helpers: frame display, video player, background workers, plot canvas, small property-page
helpers and record tables."""

from __future__ import annotations

import traceback
from contextlib import contextmanager
from typing import Callable

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QComboBox, QFrame, QGraphicsEllipseItem,
                               QGraphicsItemGroup, QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QProgressDialog, QPushButton, QSizePolicy, QSlider, QSpinBox, QStyle,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.apparatus import Apparatus
from ..core.geometry import Ellipse
from ..core.video import VideoSource
from . import theme


# ---------------------------------------------------------------- conversion
def cv_to_qimage(frame: np.ndarray) -> QImage:
    if frame.ndim == 2:
        h, w = frame.shape
        img = QImage(frame.data, w, h, w, QImage.Format_Grayscale8)
        return img.copy()
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def cv_to_qpixmap(frame: np.ndarray) -> QPixmap:
    return QPixmap.fromImage(cv_to_qimage(frame))


def fmt_time(t: float) -> str:
    if t is None or t != t:
        return "--:--"
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def value_text(v) -> str:
    """A value as an editor shows it: "" for None, 1 / 0 for booleans, whole floats without ".0"."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v)) if abs(v) < 1e15 else str(v)
    return str(v)


def error_box(parent, title: str, exc: BaseException | str):
    msg = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    QMessageBox.critical(parent, title, msg)


# --------------------------------------------------------------- frame view
class FrameView(QGraphicsView):
    """Zoomable view showing a video frame; scene coordinates are video pixels."""

    clicked = Signal(float, float)
    mouse_moved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QBrush(QColor("#1e293b")))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setZValue(-100)
        self.scene().addItem(self.pixmap_item)
        self._auto_fit = True
        self._fitting = False
        self.frame_size: tuple[int, int] | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 240)

    def set_frame(self, frame: np.ndarray | None):
        if frame is None:
            return
        h, w = frame.shape[:2]
        self.pixmap_item.setPixmap(cv_to_qpixmap(frame))
        if self.frame_size != (w, h):
            self.frame_size = (w, h)
            self.scene().setSceneRect(QRectF(-20, -20, w + 40, h + 40))
            self.fit()

    def fit(self):
        if self.frame_size and not self._fitting:
            # fit the whole scene rect (frame + margin) so scrollbars never toggle and re-trigger resizes
            self._fitting = True
            try:
                self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            finally:
                self._fitting = False
            self._auto_fit = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto_fit:
            self.fit()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self._auto_fit = False

    def mousePressEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.LeftButton:
            self.clicked.emit(p.x(), p.y())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        self.mouse_moved.emit(p.x(), p.y())
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MiddleButton or e.modifiers() & Qt.AltModifier:
            self.fit()
        super().mouseDoubleClickEvent(e)


def shape_path(shape) -> QPainterPath:
    """QPainterPath of a zone / arena shape in video-pixel coordinates."""
    path = QPainterPath()
    if isinstance(shape, Ellipse):
        path.addEllipse(QPointF(shape.cx, shape.cy), shape.rx, shape.ry)
    else:
        pts = shape.polygon()
        path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
        path.closeSubpath()
    return path


def draw_apparatus(scene: QGraphicsScene, app: Apparatus | None, labels: bool = True,
                   fill_alpha: int = 40) -> QGraphicsItemGroup:
    """Add a read-only rendering of an apparatus to a scene in the style of ANY-maze — thin orange outlines,
    zones lightly tinted with their colour, small dark labels — and return the item group."""
    orange = QColor(theme.APPARATUS)
    group = QGraphicsItemGroup()
    scene.addItem(group)
    if app is None:
        return group

    def outline(width=1.3, style=Qt.SolidLine):
        pen = QPen(orange)
        pen.setCosmetic(True)
        pen.setWidthF(width)
        pen.setStyle(style)
        return pen

    if app.arena is not None:
        it = QGraphicsPathItem(shape_path(app.arena))
        it.setPen(outline(1.3, Qt.DashLine))
        group.addToGroup(it)
    font = QFont()
    font.setPointSizeF(8.0)
    for z in app.zones:
        it = QGraphicsPathItem(shape_path(z.shape))
        it.setPen(outline(1.3, Qt.DashLine if z.hidden else Qt.SolidLine))
        fc = QColor(z.color)
        fc.setAlpha(max(0, min(255, int(fill_alpha * 0.7))))
        it.setBrush(QBrush(fc))
        group.addToGroup(it)
        if labels:
            cx, cy = z.shape.centroid()
            tx = QGraphicsSimpleTextItem(z.name)
            tx.setFont(font)
            tx.setBrush(QBrush(QColor("#ffffff")))
            halo = QPen(QColor(31, 41, 55, 200))
            halo.setWidthF(0.6)
            tx.setPen(halo)
            tx.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            br = tx.boundingRect()
            tx.setTransform(QTransform.fromTranslate(-br.width() / 2, -br.height() / 2))
            tx.setPos(cx, cy)
            group.addToGroup(tx)
    for p in app.points:
        r = 4
        it = QGraphicsEllipseItem(p.x - r, p.y - r, 2 * r, 2 * r)
        it.setBrush(QBrush(QColor(p.color)))
        it.setPen(outline(1.2))
        group.addToGroup(it)
        if p.radius_cm and app.px_per_cm:
            rr = p.radius_cm * app.px_per_cm
            ring = QGraphicsEllipseItem(p.x - rr, p.y - rr, 2 * rr, 2 * rr)
            ring.setPen(outline(1.0, Qt.DotLine))
            group.addToGroup(ring)
    for l in app.lines:
        it = QGraphicsLineItem(l.x1, l.y1, l.x2, l.y2)
        pen = QPen(QColor(l.color))
        pen.setCosmetic(True)
        pen.setWidthF(2)
        it.setPen(pen)
        group.addToGroup(it)
    return group


# --------------------------------------------------------------- video player
class VideoPlayer(QWidget):
    """Video display with transport controls.

    Set `overlay` to a callable (index, frame) -> frame to draw on frames before display.
    Emits frame_changed(index, t) after a frame is shown.
    """

    frame_changed = Signal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = FrameView()
        self.source: VideoSource | None = None
        self.overlay: Callable[[int, np.ndarray], np.ndarray] | None = None
        self.index = 0
        self.current_frame: np.ndarray | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.speed = 1.0
        self.range_start = 0  # frames
        self.range_end: int | None = None

        st = self.style()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle)
        back = QPushButton()
        back.setIcon(st.standardIcon(QStyle.SP_MediaSeekBackward))
        back.setToolTip("Back 1 frame (←); Shift: 1 s")
        back.clicked.connect(lambda: self.step(-1))
        fwd = QPushButton()
        fwd.setIcon(st.standardIcon(QStyle.SP_MediaSeekForward))
        fwd.setToolTip("Forward 1 frame (→); Shift: 1 s")
        fwd.clicked.connect(lambda: self.step(1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self._slider_moved)
        self.time_lbl = QLabel("--:--")
        self.time_lbl.setMinimumWidth(150)
        self.speed_btn = QPushButton("1×")
        self.speed_btn.setToolTip("Playback speed")
        self.speed_btn.clicked.connect(self._cycle_speed)
        bar = QHBoxLayout()
        for w in (back, self.play_btn, fwd, self.speed_btn):
            bar.addWidget(w)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_lbl)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view, 1)
        lay.addLayout(bar)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    def open(self, path: str) -> bool:
        self.close_video()
        try:
            self.source = VideoSource(path)
        except Exception as e:
            self.time_lbl.setText(f"cannot open: {e}")
            return False
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, self.source.frame_count - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.seek(0)
        return True

    def close_video(self):
        self.pause()
        if self.source is not None:
            self.source.release()
            self.source = None

    @property
    def fps(self) -> float:
        return self.source.fps if self.source else 25.0

    @property
    def time(self) -> float:
        return self.index / self.fps

    def seek(self, index: int):
        if self.source is None:
            return
        index = int(max(0, min(index, max(0, self.source.frame_count - 1))))
        f = self.source.frame_at(index)
        if f is None:
            return
        self.index = index
        self._show(f)

    def seek_time(self, t: float):
        self.seek(int(round(t * self.fps)))

    def refresh(self):
        if self.current_frame is not None:
            self._show(self.current_frame)

    def _show(self, f):
        self.current_frame = f
        disp = self.overlay(self.index, f) if self.overlay else f
        self.view.set_frame(disp)
        self.slider.blockSignals(True)
        self.slider.setValue(self.index)
        self.slider.blockSignals(False)
        total = self.source.duration if self.source else 0
        self.time_lbl.setText(f"{fmt_time(self.time)} / {fmt_time(total)}  [{self.index}]")
        self.frame_changed.emit(self.index, self.time)

    def _slider_moved(self, v):
        self.seek(v)

    def step(self, n: int):
        self.pause()
        self.seek(self.index + n)

    def play(self):
        if self.source is None:
            return
        self.timer.start(max(1, int(1000 / (self.fps * self.speed))))
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))

    def pause(self):
        self.timer.stop()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def toggle(self):
        if self.timer.isActive():
            self.pause()
        else:
            self.play()

    @property
    def playing(self) -> bool:
        return self.timer.isActive()

    def _cycle_speed(self):
        speeds = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
        i = speeds.index(self.speed) if self.speed in speeds else 2
        self.speed = speeds[(i + 1) % len(speeds)]
        self.speed_btn.setText(f"{self.speed:g}×")
        if self.playing:
            self.play()

    def _tick(self):
        if self.source is None:
            return
        skip = max(1, int(round(self.speed / 2))) if self.speed > 2 else 1
        if skip > 1:
            self.seek(self.index + skip)
        else:
            ok, f = self.source.read()
            if not ok:
                self.pause()
                return
            self.index = self.source.pos - 1
            self._show(f)
        end = self.range_end if self.range_end is not None else (self.source.frame_count - 1)
        if self.index >= end:
            self.pause()

    def keyPressEvent(self, e):
        step = int(self.fps) if e.modifiers() & Qt.ShiftModifier else 1
        if e.key() == Qt.Key_Space:
            self.toggle()
        elif e.key() == Qt.Key_Right:
            self.step(step)
        elif e.key() == Qt.Key_Left:
            self.step(-step)
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- workers
class _WorkerSignals(QObject):
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)


class Worker(QThread):
    """Run fn(progress_cb, should_stop) in a thread. Connect .signals.done / .failed / .progress."""

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.signals = _WorkerSignals()
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            res = self.fn(self.signals.progress.emit, lambda: self._stop)
            self.signals.done.emit(res)
        except Exception as e:  # pragma: no cover - surfaced to UI
            traceback.print_exc()
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


def run_with_progress(parent: QWidget, title: str, fn: Callable, on_done: Callable | None = None,
                      on_fail: Callable | None = None, cancellable: bool = True) -> Worker:
    """Run fn(progress, should_stop) in a background thread with a modal progress dialog."""
    dlg = QProgressDialog(title, "Cancel" if cancellable else None, 0, 1000, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)
    w = Worker(fn, parent)
    w.signals.progress.connect(lambda f: dlg.setValue(int(f * 1000)))
    dlg.canceled.connect(w.stop)

    def finished(res):
        dlg.close()
        if on_done:
            on_done(res)

    def failed(msg):
        dlg.close()
        if on_fail:
            on_fail(msg)
        else:
            error_box(parent, title, msg)

    w.signals.done.connect(finished)
    w.signals.failed.connect(failed)
    w.finished.connect(w.deleteLater)
    if not hasattr(parent, "_workers"):
        parent._workers = []
    parent._workers.append(w)
    w.finished.connect(lambda: parent._workers.remove(w) if w in parent._workers else None)
    w.start()
    dlg.show()
    return w


# ---------------------------------------------------------------- plotting
class PlotCanvas(QWidget):
    """Hosts a matplotlib Figure produced by core.plots."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = None
        self.figure = None

    def set_figure(self, fig):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

        if self.canvas is not None:
            self._lay.removeWidget(self.canvas)
            self.canvas.setParent(None)
            self.canvas.deleteLater()
        self.figure = fig
        self.canvas = FigureCanvasQTAgg(fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._lay.addWidget(self.canvas)
        self.canvas.draw_idle()

    def save(self, path: str, dpi: int = 300):
        if self.figure is not None:
            self.figure.savefig(path, dpi=dpi, bbox_inches="tight")


# ---------------------------------------------------------------- small helpers
@contextmanager
def loading(obj, attr: str = "_loading"):
    """Set the flag ``obj._loading`` (by default) while widgets are filled from the model, so their change
    signals are ignored; restores the previous value (nesting is fine)."""
    was = getattr(obj, attr)
    setattr(obj, attr, True)
    try:
        yield
    finally:
        setattr(obj, attr, was)


def hint(text: str = "", wrap: bool = True) -> QLabel:
    """A muted explanatory label."""
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(wrap)
    return lbl


def separator() -> QFrame:
    """Thin horizontal line between blocks of a panel or property page."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{theme.BORDER};border:none;margin:0;")
    return line


def button_row(*buttons, stretch: bool = True) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    for b in buttons:
        row.addWidget(b)
    if stretch:
        row.addStretch()
    return row


def color_icon(color: str, size: int = 12) -> QIcon:
    """A small rounded colour swatch."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color).darker(130), 1))
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 2, 2)
    p.end()
    return QIcon(pm)


class ColorButton(QPushButton):
    """A colour swatch; click to choose a colour (``color_changed`` is emitted with the new colour name)."""

    color_changed = Signal(str)

    def __init__(self, color: str = "#3b82f6", title: str = "Colour", parent=None, width: int = 30):
        super().__init__(parent)
        self.title = title
        self._color = color
        self.setFixedSize(width, 26)
        self.clicked.connect(self.pick)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, c: str):
        self._color = c
        self.setToolTip(f"{self.title}: {c} (click to change)")
        self.setStyleSheet(f"QPushButton{{background:{c};border:1px solid #9ca3af;border-radius:2px;padding:0;}}"
                           f"QPushButton:hover{{border-color:{theme.ACCENT};}}"
                           "QPushButton:disabled{background:#e5e7eb;border-color:#d1d5db;}")

    def pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, self.title)
        if c.isValid():
            self.set_color(c.name())
            self.color_changed.emit(c.name())


# ---------------------------------------------------------------- record tables
def style_table(t: QTableWidget, headers: list[str], stretch=(0,)):
    """Columns, stretching columns and the row look of the property pages' tables (one selected row)."""
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    for c in stretch:
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(32)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)


class RecordTable(QTableWidget):
    """A table editing a list of records (dicts), one row each. Columns are specs (key, header, kind, options):

    - "text": an editable cell (value: the stripped text)
    - "number" / "int": an editable right-aligned cell (value: the number, None if the text is not one)
    - "choice": a combo box of (value, label) options (value: the chosen value)
    - "text_choice": an editable combo box suggesting the options (value: the stripped text)
    - "spin": a spin box, options (minimum, maximum, special value text, suffix) (value: the number)

    Options may be a callable (evaluated for each new row). ``edited`` is emitted when the user changes a cell."""

    edited = Signal()

    def __init__(self, columns: list[tuple], stretch=(0,)):
        super().__init__(0, len(columns))
        self.columns = columns
        self._loading = False
        style_table(self, [c[1] for c in columns], stretch)
        self.itemChanged.connect(self._changed)

    def _changed(self, *_):
        if not self._loading:
            self.edited.emit()

    def set_records(self, records):
        with loading(self):
            self.setRowCount(0)
            for rec in records:
                self.add_record(rec)

    def add_record(self, rec: dict):
        """Append a row showing a record (missing keys show empty / default cells)."""
        with loading(self):
            r = self.rowCount()
            self.insertRow(r)
            for c, (key, _header, kind, opts) in enumerate(self.columns):
                opts = opts() if callable(opts) else opts
                v = rec.get(key)
                if kind in ("text", "number", "int"):
                    it = QTableWidgetItem(value_text(v))
                    if kind != "text":
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.setItem(r, c, it)
                    continue
                if kind == "spin":
                    w = QSpinBox()
                    w.setRange(opts[0], opts[1])
                    w.setSpecialValueText(opts[2] if len(opts) > 2 else "")
                    w.setSuffix(opts[3] if len(opts) > 3 else "")
                    w.setValue(int(v or 0))
                    w.valueChanged.connect(self._changed)
                else:
                    w = QComboBox()
                    if kind == "text_choice":
                        w.setEditable(True)
                        w.addItems(list(opts))
                        w.setCurrentText(v or "")
                        w.currentTextChanged.connect(self._changed)
                    else:
                        for value, label in opts:
                            w.addItem(label, value)
                        w.setCurrentIndex(max(0, w.findData(v)))
                        w.currentIndexChanged.connect(self._changed)
                self.setCellWidget(r, c, w)

    def records(self) -> list[dict]:
        out = []
        for r in range(self.rowCount()):
            rec = {}
            for c, (key, _header, kind, _opts) in enumerate(self.columns):
                w, it = self.cellWidget(r, c), self.item(r, c)
                if kind == "spin":
                    rec[key] = w.value()
                elif kind == "choice":
                    rec[key] = w.currentData()
                elif kind == "text_choice":
                    rec[key] = w.currentText().strip()
                else:
                    text = it.text().strip() if it is not None else ""
                    rec[key] = text if kind == "text" else _number(text, kind == "int")
            out.append(rec)
        return out

    def remove_current(self):
        r = self.currentRow()
        if r >= 0:
            self.removeRow(r)
            self.edited.emit()


def _number(text: str, integer: bool):
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return None
    return int(v) if integer else v
