"""The camera options dialog: region, digital zoom / pan, rotation, flip, merging up to four cameras, lens
distortion correction and the camera's hardware settings (exposure, gain, white balance … changed live while the
camera runs); the Industrial cameras dialog (GenICam / vendor SDK backends and GenTL producer files)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGraphicsRectItem, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ...core import camsources
from ...core.camera import MAX_SOURCES, MERGE_LAYOUTS, CameraView, merge_frames, merge_layout, source_key
from ...core.camhw import (ADJUSTED, CONTROLS, OK, PIXEL_FORMATS, UNSUPPORTED, CameraHardware, describe_report)
from ...core.lens import LensCorrection, lens_from
from ..widgets import FrameView
from .lens import LensCorrectionDialog, lens_text

_SLIDER_STEPS = 1000


class HardwarePanel(QWidget):
    """Camera hardware settings: a row per control (Auto check box, slider, value — "Camera default" leaves the
    camera's own setting) and, for GenICam cameras, pixel format and external trigger.

    ``camera`` is the open camera (VideoSource / NativeCamera: hardware_controls, apply_hardware,
    reset_hardware) or None; with a camera every change is applied at once and its result shown, and
    :meth:`restore` puts back the settings the dialog started with (Cancel)."""

    def __init__(self, hardware: CameraHardware | dict | None = None, camera=None, genicam: bool = False,
                 parent=None):
        super().__init__(parent)
        self.initial = hardware if isinstance(hardware, CameraHardware) else CameraHardware.from_dict(hardware)
        self.hw = self.initial.to_dict()
        self.camera = camera
        self.changed_live = False
        self._loading = True
        info = {}
        if camera is not None:
            try:
                info = {c.name: c for c in camera.hardware_controls()}
            except Exception:
                info = {}
        try:
            self.defaults = camera.current_hardware().to_dict() if camera is not None else {}
        except Exception:
            self.defaults = {}

        grid = QGridLayout(self)
        grid.setColumnStretch(2, 1)
        for col, txt in enumerate(("", "Auto", "", "Value", "")):
            if txt:
                grid.addWidget(QLabel(txt), 0, col)
        self.rows = {}
        for r, c in enumerate(CONTROLS, start=1):
            ci = info.get(c.name)
            lo = ci.minimum if ci is not None and ci.minimum is not None else c.range[0]
            hi = ci.maximum if ci is not None and ci.maximum is not None else c.range[1]
            if hi <= lo:
                lo, hi = c.range
            step = 10 ** -c.decimals
            auto = None
            if c.auto and (ci is None or ci.has_auto):
                auto = QCheckBox()
                auto.setToolTip(f"Automatic {c.label.lower()} (the camera adjusts it)")
                auto.setTristate(True)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, _SLIDER_STEPS)
            spin = QDoubleSpinBox()
            spin.setDecimals(c.decimals)
            spin.setRange(lo - step, hi)  # the minimum shows "Camera default"
            spin.setSingleStep(max(step, (hi - lo) / 100) if c.decimals else max(1.0, round((hi - lo) / 100)))
            spin.setSpecialValueText("Camera default")
            spin.setMinimumWidth(130)
            status = QLabel()
            status.setObjectName("Hint")
            lbl = QLabel(c.label)
            grid.addWidget(lbl, r, 0)
            if auto is not None:
                grid.addWidget(auto, r, 1)
            grid.addWidget(slider, r, 2)
            grid.addWidget(spin, r, 3)
            grid.addWidget(status, r, 4)
            row = {"control": c, "auto": auto, "slider": slider, "spin": spin, "status": status, "lo": lo,
                   "hi": hi, "step": step, "label": lbl}
            self.rows[c.name] = row
            if ci is not None:
                cur = "" if ci.value is None else f"camera: {ci.value:g}"
                status.setText(cur)
                if ci.supported is False:
                    self._mark(c.name, UNSUPPORTED)
            spin.valueChanged.connect(lambda v, n=c.name: self._spin_changed(n))
            slider.valueChanged.connect(lambda v, n=c.name: self._slider_changed(n))
            if auto is not None:
                auto.stateChanged.connect(lambda st, n=c.name: self._auto_changed(n))
        r = len(CONTROLS) + 1
        self.pixel_format = self.trigger = self.trigger_source = None
        if genicam:
            self.pixel_format = QComboBox()
            self.pixel_format.addItem("Camera default", None)
            for f in PIXEL_FORMATS:
                self.pixel_format.addItem(f, f)
            self.trigger = QComboBox()
            self.trigger.addItem("Camera default", None)
            self.trigger.addItem("Off (free running)", False)
            self.trigger.addItem("On: one frame per external trigger", True)
            self.trigger_source = QComboBox()
            self.trigger_source.setEditable(True)
            self.trigger_source.addItem("Camera default", None)
            for ln in ("Line0", "Line1", "Line2", "Line3", "Software"):
                self.trigger_source.addItem(ln, ln)
            for lbl, w in (("Pixel format", self.pixel_format), ("External trigger", self.trigger),
                           ("Trigger input", self.trigger_source)):
                grid.addWidget(QLabel(lbl), r, 0)
                grid.addWidget(w, r, 2, 1, 2)
                r += 1
        self.reset_btn = QPushButton("Reset to camera defaults")
        self.reset_btn.setToolTip("Forget these settings: the camera uses its own (as when it was opened)")
        self.reset_btn.clicked.connect(self.reset)
        self.report = QLabel()
        self.report.setWordWrap(True)
        self.report.setObjectName("Hint")
        grid.addWidget(self.reset_btn, r, 0, 1, 2)
        grid.addWidget(self.report, r + 1, 0, 1, 5)
        hint = QLabel("Values are in the camera's own units (driver units for webcams and capture cards; µs of "
                      "exposure and dB of gain for most industrial cameras). "
                      + ("Changes apply to the running camera at once." if camera is not None else
                         "Turn the camera image on to see changes at once; they apply when the camera opens."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:palette(mid)")
        grid.addWidget(hint, r + 2, 0, 1, 5)
        self._load()
        if genicam:
            for w in (self.pixel_format, self.trigger):
                w.currentIndexChanged.connect(self._genicam_changed)
            self.trigger_source.currentTextChanged.connect(self._genicam_changed)
        self._loading = False

    # ---- widgets ↔ settings
    def _slider_pos(self, name: str, v: float) -> int:
        row = self.rows[name]
        return int(round((v - row["lo"]) / max(1e-9, row["hi"] - row["lo"]) * _SLIDER_STEPS))

    def _load(self):
        self._loading = True
        for name, row in self.rows.items():
            c = row["control"]
            v = self.hw.get(name)
            row["spin"].setValue(row["spin"].minimum() if v is None else v)
            row["slider"].setValue(0 if v is None else self._slider_pos(name, v))
            if row["auto"] is not None:
                a = self.hw.get(c.auto)
                row["auto"].setCheckState(Qt.PartiallyChecked if a is None else Qt.Checked if a else Qt.Unchecked)
            self._enable_row(name)
        if self.pixel_format is not None:
            self.pixel_format.setCurrentIndex(max(0, self.pixel_format.findData(self.hw.get("pixel_format"))))
            self.trigger.setCurrentIndex(max(0, self.trigger.findData(self.hw.get("trigger"))))
            src = self.hw.get("trigger_source")
            if src and self.trigger_source.findData(src) < 0:
                self.trigger_source.addItem(src, src)
            self.trigger_source.setCurrentIndex(max(0, self.trigger_source.findData(src)))
        self._loading = False

    def _enable_row(self, name: str):
        row = self.rows[name]
        manual = row["auto"] is None or row["auto"].checkState() != Qt.Checked
        ok = row.get("supported", True)
        row["slider"].setEnabled(manual and ok)
        row["spin"].setEnabled(manual and ok)
        if row["auto"] is not None:
            row["auto"].setEnabled(ok)

    def _spin_changed(self, name: str):
        if self._loading:
            return
        row = self.rows[name]
        v = row["spin"].value()
        value = None if v <= row["spin"].minimum() else v
        self._loading = True
        row["slider"].setValue(0 if value is None else self._slider_pos(name, value))
        self._loading = False
        self._set({name: value})

    def _slider_changed(self, name: str):
        if self._loading:
            return
        row = self.rows[name]
        v = row["lo"] + row["slider"].value() / _SLIDER_STEPS * (row["hi"] - row["lo"])
        self._loading = True
        row["spin"].setValue(round(v, row["control"].decimals))
        self._loading = False
        self._set({name: row["spin"].value()})

    def _auto_changed(self, name: str):
        row = self.rows[name]
        self._enable_row(name)
        if self._loading:
            return
        st = row["auto"].checkState()
        self._set({row["control"].auto: None if st == Qt.PartiallyChecked else st == Qt.Checked})

    def _genicam_changed(self, *_):
        if self._loading:
            return
        src = self.trigger_source.currentData() if self.trigger_source.currentIndex() > 0 else None
        text = self.trigger_source.currentText().strip()
        if self.trigger_source.currentIndex() <= 0 and text and text != "Camera default":
            src = text
        self._set({"pixel_format": self.pixel_format.currentData(), "trigger": self.trigger.currentData(),
                   "trigger_source": src})

    def _set(self, changes: dict):
        """Record changes (None = camera default) and apply them to the running camera."""
        for k, v in changes.items():
            if v is None:
                self.hw.pop(k, None)
            else:
                self.hw[k] = v
        if self.camera is None:
            return
        # a setting back to "Camera default" gets the value the camera had when the dialog opened
        live = {k: (v if v is not None else self.defaults.get(k)) for k, v in changes.items()}
        live = CameraHardware.from_dict({k: v for k, v in live.items() if v is not None})
        if live.is_empty:
            return
        try:
            report = self.camera.apply_hardware(live)
        except Exception as e:
            report = {k: UNSUPPORTED for k in live.to_dict()}
            self.report.setText(f"The camera refused the change: {e}")
        self.changed_live = True
        self.show_report(report)

    def _mark(self, name: str, status: str):
        row = self.rows.get(name)
        if row is None:
            for r in self.rows.values():
                if r["control"].auto == name:
                    row = r
            if row is None:
                return
        if status == UNSUPPORTED:
            row["supported"] = False
            row["status"].setText("Not supported")
            row["status"].setToolTip("This camera / driver does not accept this setting")
            self._enable_row(row["control"].name)
        elif str(status).startswith(ADJUSTED):
            row["status"].setText(f"camera used {status.split(':', 1)[1]}")
        elif status == OK:
            row["status"].setText("✓")

    def show_report(self, report: dict):
        for k, v in (report or {}).items():
            self._mark(k, v)
        txt = describe_report(report)
        if txt:
            self.report.setText(txt)

    def reset(self):
        """Back to the camera's own settings (forget every setting)."""
        self.hw = {}
        for row in self.rows.values():
            row.pop("supported", None)
        self._load()
        if self.camera is not None:
            try:
                self.show_report(self.camera.reset_hardware())
                self.changed_live = True
            except Exception as e:
                self.report.setText(f"Could not reset the camera: {e}")
        self.report.setText("Camera defaults: the camera's own settings are used.")

    def restore(self):
        """Cancel: put the running camera back as it was when the dialog opened."""
        if self.camera is None or not self.changed_live:
            return
        try:
            self.camera.reset_hardware()
            if not self.initial.is_empty:
                self.camera.apply_hardware(self.initial)
        except Exception:
            pass

    def result(self) -> CameraHardware:
        return CameraHardware.from_dict(self.hw)


class IndustrialCamerasDialog(QDialog):
    """Industrial camera backends (installed or what to install) and the GenTL producer (.cti) files used by the
    GenICam backend."""

    def __init__(self, cti=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Industrial cameras")
        self.resize(640, 420)
        v = QVBoxLayout(self)
        intro = QLabel("GigE Vision / USB3 Vision cameras are read through their vendor's SDK. Webcams, UVC cameras "
                       "and analogue capture cards (frame grabbers showing up as a video device) need nothing: they "
                       "are listed as Camera 0, 1, …")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.status_lbl = QLabel()
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.status_lbl)
        v.addWidget(QLabel("GenTL producers (.cti) for the GenICam backend (harvesters):"))
        self.cti = QListWidget()
        for f in cti:
            self.cti.addItem(str(f))
        v.addWidget(self.cti, 1)
        row = QHBoxLayout()
        add = QPushButton("Add .cti file…")
        add.clicked.connect(self._add)
        rem = QPushButton("Remove")
        rem.clicked.connect(lambda: [self.cti.takeItem(self.cti.row(i)) for i in self.cti.selectedItems()])
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.refresh_status()

    def refresh_status(self):
        lines = []
        for _, vendor, ok, msg in camsources.backend_status():
            lines.append(f"{'✓' if ok else '–'} {msg if not ok else vendor + ': ' + msg}")
        self.status_lbl.setText("\n".join(lines))

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "GenTL producer", "", "GenTL producers (*.cti);;All files (*)")
        for p in paths:
            if not self.cti.findItems(p, Qt.MatchExactly):
                self.cti.addItem(p)

    def files(self) -> list[str]:
        return [self.cti.item(i).text() for i in range(self.cti.count())]


class CameraOptionsDialog(QDialog):
    """Region of interest (drag a rectangle on the image), digital zoom / pan, rotation, flip, merging with up to
    three more cameras (a montage) and the lens distortion correction of the camera.  ``second_choices`` is a list
    of (label, source) for the merge; ``second`` (and ``second_frame``) the merged source (and its image), or a
    list of them.  ``lens`` is the camera's lens correction and ``merge_lenses`` ({source key: correction}) those
    of the merged cameras, applied in the preview; ``frame_source`` returns the camera's latest original image for
    the lens correction dialog.

    For a camera (``is_camera``, or an open ``camera`` object) a Camera settings tab holds its hardware settings
    (:class:`HardwarePanel`; ``genicam`` adds pixel format and trigger); with an open camera they apply live and
    Cancel puts them back."""

    def __init__(self, frame: np.ndarray | None, view: CameraView, second=None, layout: str = "side",
                 second_choices=(), second_frame: np.ndarray | None = None, parent=None, title="Camera options",
                 hardware: CameraHardware | dict | None = None, camera=None, is_camera: bool = False,
                 genicam: bool = False, lens: LensCorrection | dict | None = None, merge_lenses: dict | None = None,
                 frame_source=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        self.raw = frame if frame is not None else np.full((480, 640, 3), 90, np.uint8)
        merged = [s for s in (second if isinstance(second, (list, tuple)) else [second]) if s is not None]
        frames = list(second_frame) if isinstance(second_frame, (list, tuple)) else [second_frame]
        # the images of the merged sources, by source (a source chosen in the dialog has none: a placeholder)
        self._frames = {source_key(s): f for s, f in zip(merged, frames) if f is not None}
        self.raw2 = frames[0] if frames else None
        self._have_frame = frame is not None  # without one, a grey placeholder: no region can be chosen on it
        self.view = CameraView.from_dict(view.to_dict())
        self.lens = LensCorrection.from_dict(lens).to_dict()
        self.merge_lenses = dict(merge_lenses or {})
        self.frame_source = frame_source
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
        # the merged sources: "Merge with", then up to two more ("and with"), each offered once the one before is set
        self.merge_combos: list[QComboBox] = []
        for k in range(MAX_SOURCES - 1):
            cb = QComboBox()
            cb.addItem("No (single camera)" if k == 0 else "No", None)
            for lbl, src in second_choices:
                cb.addItem(lbl, src)
            if k < len(merged):
                i = cb.findData(merged[k])
                if i < 0:
                    cb.addItem(str(merged[k]), merged[k])
                    i = cb.count() - 1
                cb.setCurrentIndex(i)
            self.merge_combos.append(cb)
        self.second = self.merge_combos[0]
        self.layout_combo = QComboBox()
        for k, lbl in MERGE_LAYOUTS:
            self.layout_combo.addItem(lbl, k)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(merge_layout(layout))))
        self.lens_lbl = QLabel()
        self.lens_btn = QPushButton("Lens correction…")
        self.lens_btn.setToolTip("Correct the distortion of a wide-angle (fish-eye / barrel) lens: a strength "
                                 "slider or a checkerboard calibration")
        self.lens_btn.clicked.connect(lambda: self.edit_lens())
        lens_row = QHBoxLayout()
        lens_row.addWidget(self.lens_lbl, 1)
        lens_row.addWidget(self.lens_btn)
        form.addRow("Merge with", self.second)
        for cb in self.merge_combos[1:]:
            form.addRow("… and with", cb)
        form.addRow("Merged layout", self.layout_combo)
        form.addRow("Lens correction", lens_row)
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
        self.hardware = None
        if is_camera or camera is not None:
            self.resize(900, 720)
            self.tabs = QTabWidget()
            image = QWidget()
            iv = QVBoxLayout(image)
            iv.addLayout(form)
            iv.addWidget(hint)
            self.tabs.addTab(image, "Image")
            self.hardware = HardwarePanel(hardware, camera, genicam)
            self.tabs.addTab(self.hardware, "Camera settings")
            v.addWidget(self.tabs)
        else:
            v.addLayout(form)
            v.addWidget(hint)
        v.addWidget(bb)

        self._load()
        self._geometry = self._initial_geometry = self._base_geometry()
        if not self._have_frame:
            for w in (self.cx, self.cy, self.cw, self.ch, self.full_btn):
                w.setEnabled(False)
                w.setToolTip("Turn the camera image on (or choose a video file) to choose a region on it")
        for w in (self.rotate, self.flip, self.layout_combo, *self.merge_combos):
            w.currentIndexChanged.connect(self._geometry_changed)
        for w in (self.cx, self.cy, self.cw, self.ch):
            w.valueChanged.connect(self._changed)
        self.zoom.valueChanged.connect(self._changed)
        self.pan_x.valueChanged.connect(self._changed)
        self.pan_y.valueChanged.connect(self._changed)
        self._loading = False
        self._changed()

    # ---- values
    def merged(self) -> list:
        """The sources merged into the image, in order (up to the first "No")."""
        out = []
        for cb in self.merge_combos:
            if cb.currentData() is None:
                break
            out.append(cb.currentData())
        return out

    def _base(self) -> np.ndarray:
        """Lens-corrected, merged, rotated and flipped image (the crop is selected on it)."""
        lens = lens_from(self.lens)
        frames = [lens.apply(self.raw) if lens is not None else self.raw]
        for s in self.merged():
            f = self._frames.get(source_key(s))
            if f is None:
                f = np.full_like(self.raw, 60)
            else:
                other = lens_from(self.merge_lenses.get(source_key(s)))
                f = other.apply(f) if other is not None else f
            frames.append(f)
        img = merge_frames(frames, layout=self.layout_combo.currentData()) if len(frames) > 1 else frames[0]
        return CameraView(rotate=self.rotate.currentData(), flip=self.flip.currentData()).apply(img)

    def _base_geometry(self) -> tuple:
        """What the region is chosen on: rotation, flip, merge (and its layout) and the size of that image."""
        merged = self.merged()
        return (self.rotate.currentData(), self.flip.currentData(), tuple(merged) or None,
                self.layout_combo.currentData() if merged else None, self._base().shape[:2])

    def _region_reliable(self) -> bool:
        """The region fields were chosen on the real image (not on the placeholder, nor on a merge with a camera
        whose image is unknown)."""
        return self._have_frame and all(source_key(s) in self._frames for s in self.merged())

    def edit_lens(self, dialog=None) -> bool:
        """The lens correction dialog for this camera (``dialog``: a LensCorrectionDialog already set up, for
        tests)."""
        dlg = dialog or LensCorrectionDialog(self.raw if self._have_frame else None, self.lens, self.frame_source,
                                             parent=self, title=f"Lens correction — {self.windowTitle()}")
        if dialog is None:
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()
            if not accepted:
                return False
        self.lens = dlg.result()
        self._geometry_changed()
        return True

    def _geometry_changed(self, *_):
        """Rotation, flip or merge changed: the region of the previous image means nothing on the new one — back to
        the whole image."""
        if self._loading:
            return
        g = self._base_geometry()
        if g != self._geometry:
            self._geometry = g
            h, w = g[-1]
            self._loading = True
            for sp, val in zip((self.cx, self.cy, self.cw, self.ch), (0, 0, w, h)):
                sp.setValue(val)
            self._loading = False
        self._changed()

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
        if not self._region_reliable():
            # never a region measured on a placeholder image: the saved one is kept while the image it was chosen
            # on is unchanged (same rotation / flip / merge), else the whole image
            same = (self.rotate.currentData(), self.flip.currentData()) == (self.view.rotate, self.view.flip) \
                and self._geometry == self._initial_geometry
            crop = list(self.view.crop) if same and self.view.crop else None
        return CameraView.from_dict({"crop": crop, "zoom": self.zoom.value(), "rotate": self.rotate.currentData(),
                                     "flip": self.flip.currentData(),
                                     "pan": [self.pan_x.value() / 100, self.pan_y.value() / 100]})

    def result(self) -> dict:
        """{"view": CameraView dict, "second": the first merged source or None, "merge": [merged sources],
        "layout": "side" | "stack" | "grid", "undistort": LensCorrection dict ({} = none), and for cameras
        "hardware": CameraHardware dict}."""
        merged = self.merged()
        res = {"view": self.result_view().to_dict(), "second": merged[0] if merged else None, "merge": merged,
               "layout": self.layout_combo.currentData(), "undistort": dict(self.lens)}
        if self.hardware is not None:
            res["hardware"] = self.hardware.result().to_dict()
        return res

    def reject(self):
        if self.hardware is not None:
            self.hardware.restore()
        super().reject()

    def done(self, r):
        try:  # the region filter goes before the widgets do (no call on a deleted viewport at teardown)
            self.src_view.viewport().removeEventFilter(self)
        except RuntimeError:
            pass
        super().done(r)

    def _full(self):
        h, w = self._base().shape[:2]
        self._loading = True
        for s, val in zip((self.cx, self.cy, self.cw, self.ch), (0, 0, w, h)):
            s.setValue(val)
        self._loading = False
        self._changed()

    def _reset(self):
        self.view = CameraView()
        self.lens = {}
        self._loading = True
        for cb in self.merge_combos:
            cb.setCurrentIndex(0)
        self._load()
        self._loading = False
        self._geometry = self._base_geometry()
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
        merged = self.merged()
        self.layout_combo.setEnabled(bool(merged))
        for k, cb in enumerate(self.merge_combos[1:], start=1):
            cb.setEnabled(len(merged) >= k)
        self.lens_lbl.setText(lens_text(self.lens))

    # ---- rubber band selection of the region
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent

        try:
            mine = obj is self.src_view.viewport()
        except RuntimeError:  # the dialog's widgets are being deleted
            return False
        if mine and self._have_frame:
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
