"""The camera options dialog: region, digital zoom / pan, rotation, flip, merging two cameras."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGraphicsRectItem,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout)

from ...core.camera import CameraView, merge_frames
from ..widgets import FrameView


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
