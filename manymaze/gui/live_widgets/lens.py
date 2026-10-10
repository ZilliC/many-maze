"""The lens distortion correction dialog: a barrel strength slider with a live preview, or a checkerboard
calibration from views captured with the camera or found in a video (core.lens)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from ...core.lens import (DEFAULT_PATTERN, MIN_VIEWS, LensCorrection, calibrate_checkerboard,
                          checkerboard_views_in_video, distinct_view, find_checkerboard)
from ...core.tracking import hex_to_bgr
from ...core.video import VIDEO_EXTENSIONS
from .. import theme
from ..widgets import FrameView, error_box, hint, run_with_progress

METHODS = [("", "None"), ("barrel", "Barrel strength (one slider)"),
           ("checkerboard", "Checkerboard calibration")]


def lens_text(lens: LensCorrection | dict | None) -> str:
    """One line describing a correction, for the camera options and the apparatus page."""
    lens = LensCorrection.from_dict(lens) if not isinstance(lens, LensCorrection) else lens
    return "None" if lens.is_identity else lens.describe().capitalize()


class LensCorrectionDialog(QDialog):
    """Lens distortion correction of one camera or video.

    ``frame`` is an original (uncorrected) image of it; ``frame_source`` (optional) returns the camera's latest
    original image, which then refreshes the preview and can be captured as a checkerboard view; ``video_path`` is
    where *Find views in a video…* starts.  ``apply_choices`` [(label, value)] adds an *Apply to* choice (video
    tests); :meth:`result` is the LensCorrection dict ({} for none) and :meth:`apply_to` the chosen value."""

    def __init__(self, frame: np.ndarray | None, lens: LensCorrection | dict | None = None, frame_source=None,
                 video_path: str = "", parent=None, title: str = "Lens distortion correction", apply_choices=()):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 600)
        self.raw = frame if frame is not None else None
        self.frame_source = frame_source
        self.video_path = video_path
        lens = LensCorrection.from_dict(lens) if not isinstance(lens, LensCorrection) else lens
        self.views: list[np.ndarray] = []
        self.view_size: tuple[int, int] | None = None
        self.calibration = lens if lens.method == "checkerboard" and not lens.is_identity else None
        self._loading = True

        self.preview = FrameView()
        self.preview.setMinimumSize(420, 320)
        self.show_original = QCheckBox("Show the original image")
        self.show_original.setToolTip("Compare with the image before the correction")
        self.guides = QCheckBox("Straight guide lines")
        self.guides.setToolTip("Draw straight lines over the image: the walls of the apparatus should run "
                               "along them once the lens is corrected")
        self.guides.setChecked(True)
        left = QVBoxLayout()
        left.addWidget(self.preview, 1)
        row = QHBoxLayout()
        row.addWidget(self.show_original)
        row.addWidget(self.guides)
        row.addStretch()
        left.addLayout(row)
        self.status = hint("")
        left.addWidget(self.status)

        right = QVBoxLayout()
        form = QFormLayout()
        self.method = QComboBox()
        for k, lbl in METHODS:
            self.method.addItem(lbl, k)
        form.addRow("Correction", self.method)
        right.addLayout(form)

        self.barrel_box = QGroupBox("Barrel strength")
        bl = QVBoxLayout(self.barrel_box)
        srow = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(-100, 100)
        self.strength = QSpinBox()
        self.strength.setRange(-100, 100)
        self.strength.setToolTip("Positive: barrel (fish-eye) distortion, the edges of the image bulge outwards. "
                                 "Negative: pincushion distortion.")
        srow.addWidget(self.slider, 1)
        srow.addWidget(self.strength)
        bl.addLayout(srow)
        bl.addWidget(hint("Move the slider until the walls of the apparatus are straight in the preview. Strong "
                          "fish-eye lenses need the checkerboard calibration."))
        right.addWidget(self.barrel_box)

        self.board_box = QGroupBox("Checkerboard calibration")
        cl = QVBoxLayout(self.board_box)
        prow = QHBoxLayout()
        self.cols, self.rows = QSpinBox(), QSpinBox()
        for sp, v in ((self.cols, DEFAULT_PATTERN[0]), (self.rows, DEFAULT_PATTERN[1])):
            sp.setRange(3, 40)
            sp.setValue(v)
        prow.addWidget(QLabel("Inner corners"))
        prow.addWidget(self.cols)
        prow.addWidget(QLabel("×"))
        prow.addWidget(self.rows)
        prow.addStretch()
        cl.addLayout(prow)
        cl.addWidget(hint(f"Print a checkerboard, hold it flat under the camera and capture at least {MIN_VIEWS} "
                          "views of all of it, in different places of the image (the corners too) and at "
                          "different angles. Count the inner corners, where four squares meet (9 × 6 on a board "
                          "of 10 × 7 squares)."))
        brow = QHBoxLayout()
        self.capture_btn = QPushButton("Capture view")
        self.capture_btn.setToolTip("Find the checkerboard in the camera image now and keep this view")
        self.video_btn = QPushButton("Find views in a video…")
        self.video_btn.setToolTip("Find the checkerboard in frames spread through a video of it, filmed with this "
                                  "camera")
        brow.addWidget(self.capture_btn)
        brow.addWidget(self.video_btn)
        cl.addLayout(brow)
        crow = QHBoxLayout()
        self.calibrate_btn = QPushButton("Calibrate")
        self.clear_btn = QPushButton("Clear views")
        crow.addWidget(self.calibrate_btn)
        crow.addWidget(self.clear_btn)
        crow.addStretch()
        cl.addLayout(crow)
        self.views_lbl = QLabel()
        self.views_lbl.setWordWrap(True)
        cl.addWidget(self.views_lbl)
        right.addWidget(self.board_box)

        self.keep_whole = QCheckBox("Keep the whole image (black corners)")
        self.keep_whole.setToolTip("Off: the corrected image is enlarged to fill the frame and its edges are cut "
                                   "off. On: all of the original image is kept and black areas appear at the "
                                   "corners.")
        right.addWidget(self.keep_whole)
        self.apply_combo = None
        if apply_choices:
            self.apply_combo = QComboBox()
            for lbl, v in apply_choices:
                self.apply_combo.addItem(lbl, v)
            af = QFormLayout()
            af.addRow("Apply to", self.apply_combo)
            right.addLayout(af)
        right.addWidget(hint("Tracking, the apparatus map and recorded videos use the corrected image. Draw (or "
                             "check) the apparatus again after changing the correction."))
        right.addStretch()

        body = QHBoxLayout()
        body.addLayout(left, 3)
        side = QWidget()
        side.setLayout(right)
        side.setMinimumWidth(330)
        body.addWidget(side, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addLayout(body, 1)
        v.addWidget(bb)

        self.method.setCurrentIndex(max(0, self.method.findData(lens.method if not lens.is_identity else "")))
        self.strength.setValue(int(round(lens.strength)) if lens.method == "barrel" else 0)
        self.slider.setValue(self.strength.value())
        self.keep_whole.setChecked(lens.alpha >= 0.5)
        self.method.currentIndexChanged.connect(self._changed)
        self.slider.valueChanged.connect(lambda val: self.strength.setValue(val))
        self.strength.valueChanged.connect(self._strength_changed)
        self.keep_whole.toggled.connect(self._changed)
        self.show_original.toggled.connect(self._changed)
        self.guides.toggled.connect(self._changed)
        self.capture_btn.clicked.connect(lambda: self.capture_view())
        self.video_btn.clicked.connect(self._video_dialog)
        self.calibrate_btn.clicked.connect(lambda: self.calibrate())
        self.clear_btn.clicked.connect(self.clear_views)
        self._loading = False
        self._timer = None
        if frame_source is not None:
            self._timer = QTimer(self)
            self._timer.setInterval(250)
            self._timer.timeout.connect(self._pull_frame)
            self._timer.start()
        self._changed()

    # ---- values
    def correction(self) -> LensCorrection:
        """The correction as the dialog now stands (no correction when the checkerboard is not calibrated)."""
        m = self.method.currentData()
        alpha = 1.0 if self.keep_whole.isChecked() else 0.0
        if m == "barrel":
            return LensCorrection.barrel(self.strength.value(), alpha)
        if m == "checkerboard" and self.calibration is not None:
            return LensCorrection.from_dict({**self.calibration.to_dict(), "alpha": alpha})
        return LensCorrection()

    def result(self) -> dict:
        return self.correction().to_dict()

    def apply_to(self):
        return self.apply_combo.currentData() if self.apply_combo is not None else None

    def accept(self):
        if self.method.currentData() == "checkerboard" and self.calibration is None:
            QMessageBox.information(self, self.windowTitle(),
                                    f"Capture at least {MIN_VIEWS} views of the checkerboard and click Calibrate "
                                    "first (or choose another correction).")
            return
        super().accept()

    def done(self, r):
        if self._timer is not None:
            self._timer.stop()
        super().done(r)

    # ---- preview
    def _pull_frame(self):
        try:
            f = self.frame_source()
        except Exception:
            f = None
        if f is not None:
            self.raw = f
            self._show()

    def _strength_changed(self, val: int):
        if self.slider.value() != val:
            self.slider.blockSignals(True)
            self.slider.setValue(val)
            self.slider.blockSignals(False)
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        m = self.method.currentData()
        self.barrel_box.setVisible(m == "barrel")
        self.board_box.setVisible(m == "checkerboard")
        self.keep_whole.setEnabled(bool(m))
        self.capture_btn.setEnabled(self.frame_source is not None or self.raw is not None)
        self.calibrate_btn.setEnabled(len(self.views) >= MIN_VIEWS)
        self.clear_btn.setEnabled(bool(self.views))
        self._update_views_label()
        self._show()

    def _update_views_label(self):
        n = len(self.views)
        txt = f"{n} view{'s' if n != 1 else ''} of the checkerboard"
        txt += f" ({MIN_VIEWS} needed)." if n < MIN_VIEWS else "."
        if self.calibration is not None:
            c = self.calibration
            txt += (f"<br><b>Calibrated</b> from {c.views} views: reprojection error {c.error_px:.2f} px"
                    + (" — good." if c.error_px < 1.0 else " — high: capture sharper views of the whole board."))
        self.views_lbl.setText(txt)

    def _show(self):
        if self.raw is None:
            self.preview.set_frame(np.full((480, 640, 3), 90, np.uint8))
            self.status.setText("No image: turn the camera on (or choose a video) to see the correction.")
            return
        lens = self.correction()
        img = self.raw if self.show_original.isChecked() or lens.is_identity else lens.apply(self.raw)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        else:
            img = img.copy()
        if self.guides.isChecked():
            h, w = img.shape[:2]
            col = hex_to_bgr(theme.RULER)
            for k in range(1, 8):
                x = int(round(w * k / 8))
                cv2.line(img, (x, 0), (x, h - 1), col, 1, cv2.LINE_AA)
            for k in range(1, 6):
                y = int(round(h * k / 6))
                cv2.line(img, (0, y), (w - 1, y), col, 1, cv2.LINE_AA)
        self.preview.set_frame(img)
        self.status.setText("Original image" if self.show_original.isChecked() or lens.is_identity else
                            f"Corrected: {lens.describe()}")

    # ---- checkerboard
    def pattern(self) -> tuple[int, int]:
        return self.cols.value(), self.rows.value()

    def _add_view(self, corners: np.ndarray, size: tuple[int, int]) -> bool:
        if self.view_size is not None and tuple(size) != tuple(self.view_size):
            self.views.clear()  # views of another image size cannot be calibrated together
        self.view_size = tuple(size)
        if len(corners) != self.pattern()[0] * self.pattern()[1] or not distinct_view(corners, self.views, size):
            return False
        self.views.append(corners)
        return True

    def capture_view(self, frame: np.ndarray | None = None) -> bool:
        """Find the checkerboard in the current camera image (or ``frame``) and keep the view."""
        f = frame
        if f is None and self.frame_source is not None:
            try:
                f = self.frame_source()
            except Exception:
                f = None
        if f is None:
            f = self.raw
        if f is None:
            self.status.setText("No image to capture.")
            return False
        corners = find_checkerboard(f, self.pattern())
        if corners is None:
            self.status.setText(f"No checkerboard of {self.pattern()[0]} × {self.pattern()[1]} inner corners in "
                                "this image: show all of it, flat and well lit.")
            return False
        if not self._add_view(corners, (f.shape[1], f.shape[0])):
            self.status.setText("This view is too like one already captured: move or tilt the checkerboard.")
            return False
        self.status.setText(f"View {len(self.views)} captured.")
        self._changed()
        return True

    def add_video_views(self, path: str, progress=None, should_stop=None) -> int:
        """Add the checkerboard views found in a video; returns how many were added."""
        views, size, _ = checkerboard_views_in_video(path, self.pattern(), progress=progress,
                                                      should_stop=should_stop)
        return self._views_found((views, size))

    def _views_found(self, res) -> int:
        views, size = res
        n = sum(1 for c in views if self._add_view(c, size))
        self.status.setText(f"{n} view{'s' if n != 1 else ''} of the checkerboard found in the video."
                            if n else "No (new) view of the checkerboard in this video: check the number of "
                                      "inner corners.")
        self._changed()
        return n

    def _video_dialog(self):
        start = str(Path(self.video_path).parent) if self.video_path else str(Path.home())
        exts = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Video of the checkerboard", start,
                                              f"Videos ({exts});;All files (*)")
        if not path:
            return
        pattern = self.pattern()
        run_with_progress(self, "Looking for the checkerboard",
                          lambda progress, stop: checkerboard_views_in_video(path, pattern, progress=progress,
                                                                             should_stop=stop)[:2],
                          on_done=self._views_found)

    def calibrate(self) -> bool:
        if self.view_size is None or len(self.views) < MIN_VIEWS:
            self.status.setText(f"Capture at least {MIN_VIEWS} views of the checkerboard first.")
            return False
        try:
            self.calibration = calibrate_checkerboard(self.views, self.view_size, self.pattern(),
                                                      1.0 if self.keep_whole.isChecked() else 0.0)
        except Exception as e:
            error_box(self, "Checkerboard calibration", e)
            return False
        self.method.setCurrentIndex(self.method.findData("checkerboard"))
        self._changed()
        return True

    def clear_views(self):
        self.views.clear()
        self.view_size = None
        self._changed()
