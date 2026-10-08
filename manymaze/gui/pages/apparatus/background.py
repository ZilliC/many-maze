"""The background image of the apparatus map: a frame of a test's (or any) video, remembered per apparatus."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QMenu, QMessageBox, QSlider

from ....core.apparatus import Apparatus
from ....core.video import VIDEO_EXTENSIONS, VideoSource
from ...icons import icon
from ...widgets import fmt_time, hint, loading


def placeholder_frame(w: int, h: int) -> np.ndarray:
    """Light grey stand-in for the video frame until a background image is loaded."""
    img = np.full((h, w, 3), (236, 234, 232), np.uint8)
    step = max(20, int(round(max(w, h) / 16 / 10)) * 10)
    for x in range(0, w, step):
        cv2.line(img, (x, 0), (x, h), (222, 219, 216), 1)
    for y in range(0, h, step):
        cv2.line(img, (0, y), (w, y), (222, 219, 216), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    sc = max(0.5, w / 900)
    for i, (txt, col) in enumerate((("No background image", (110, 104, 98)),
                                    ("Background > Load image from video", (150, 144, 138)))):
        (tw, th), _ = cv2.getTextSize(txt, font, sc * (1.0 if i == 0 else 0.7), 1)
        cv2.putText(img, txt, ((w - tw) // 2, h // 2 + i * int(36 * sc)), font, sc * (1.0 if i == 0 else 0.7),
                    col, 1, cv2.LINE_AA)
    return img


class BackgroundController(QObject):
    """The map's background frame for the page's current apparatus: the video and time last shown for it, else
    the video of a test that uses it, else a placeholder. Owns the open video and the background controls (a
    test video menu and combo box, a time slider and spin box)."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.memory: dict[int, tuple[str, float]] = {}  # id(apparatus) -> (video, time) last shown
        self.src: VideoSource | None = None
        self.path: str | None = None
        self.real = False  # a video frame is shown (not the placeholder)
        self.frame = None  # that frame (BGR), for exporting the zone map over it
        self._loading = False
        self.test_combo = QComboBox()
        self.test_combo.setToolTip("Use a frame from the video of one of the experiment's tests")
        self.test_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.test_combo.setMinimumContentsLength(16)
        self.test_combo.activated.connect(self._test_chosen)
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setToolTip("Time in the video of the background frame")
        self.time_slider.valueChanged.connect(self._slider_changed)
        self.time_spin = QDoubleSpinBox()
        self.time_spin.setDecimals(2)
        self.time_spin.setSuffix(" s")
        self.time_spin.setMinimumWidth(90)
        self.time_spin.setToolTip("Time in the video of the background frame")
        self.time_spin.valueChanged.connect(self._spin_changed)
        self.label = hint("No background", wrap=False)
        self.label.setFixedWidth(210)
        self.test_menu = QMenu(page)
        self.test_menu.aboutToShow.connect(self.fill_test_menu)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.seek)

    def add_controls(self, row: QHBoxLayout):
        row.addWidget(self.test_combo)
        row.addWidget(self.label)
        row.addWidget(self.time_slider, 1)
        row.addWidget(self.time_spin)

    def set_enabled(self, on: bool):
        for w in (self.test_combo, self.time_slider, self.time_spin):
            w.setEnabled(on)

    # ---- per-apparatus memory -----------------------------------------------------------
    def inherit(self, src: Apparatus | None, dst: Apparatus):
        """A new apparatus made from `src` starts with its background."""
        if src is not None and id(src) in self.memory:
            self.memory[id(dst)] = self.memory[id(src)]

    def forget(self, app: Apparatus):
        self.memory.pop(id(app), None)

    def real_frame_size(self):
        return self.page.view.frame_size if self.real else None

    def close(self):
        if self.src is not None:
            self.src.release()
        self.src = None
        self.path = None
        self.real = False
        self.frame = None

    # ---- showing ------------------------------------------------------------------------
    def show(self):
        """The background of the page's current apparatus."""
        app, p = self.page.app, self.page.project
        if app is not None:
            mem = self.memory.get(id(app))
            if mem and self.load(mem[0], mem[1], quiet=True):
                return
            for t in p.tests:
                if t.apparatus == app.name and t.video:
                    path = p.abs_path(t.video)
                    if Path(path).exists() and self.load(path, t.start_s, quiet=True):
                        return
        self.close()
        w, h = (app.frame_size if app is not None and app.frame_size else (640, 480))
        self.page.view.set_frame(placeholder_frame(int(w), int(h)))
        with loading(self):
            self.time_slider.setRange(0, 0)
            self.time_spin.setRange(0, 0)
        self.label.setText("No background image")
        self.label.setToolTip("" if app is None or not app.frame_size else f"Frame size {w}×{h} px")

    def load(self, path: str, t: float = 0.0, quiet: bool = False) -> bool:
        """Show the frame at time t of a video as the background for the current apparatus."""
        try:
            if self.src is None or self.path != path:
                self.close()
                self.src = VideoSource(path)
                self.path = path
            src = self.src
            idx = int(round(max(0.0, t) * src.fps))
            if src.frame_count:
                idx = min(idx, src.frame_count - 1)
            f = src.frame_at(idx)
            if f is None:
                idx = 0
                f = src.frame_at(0)
            if f is None:
                raise IOError("cannot read a frame from this video")
        except Exception as e:
            self.close()
            msg = f"Cannot load background from {Path(path).name}: {e}"
            if quiet:
                self.page.main.status(msg)
            else:
                QMessageBox.warning(self.page, "Background image", msg)
            return False
        self.real = True
        self.frame = f
        self.page.view.set_frame(f)
        h, w = f.shape[:2]
        app = self.page.app
        tt = idx / src.fps if src.fps else 0.0
        if app is not None:
            self.memory[id(app)] = (path, tt)
            if tuple(app.frame_size or ()) != (w, h):
                app.frame_size = (w, h)
                self.page.main.mark_dirty()
                self.page.refresh_info()
        with loading(self):
            self.time_slider.setRange(0, max(0, src.frame_count - 1))
            self.time_slider.setValue(idx)
            self.time_spin.setRange(0, max(0.0, src.duration))
            self.time_spin.setValue(tt)
        self.label.setText(f"{Path(path).name} · {fmt_time(tt)} · {w}×{h} px")
        self.label.setToolTip(path)
        return True

    def load_dialog(self):
        p = self.page.project
        start = str(p.path) if p and p.path else str(Path.home())
        if self.path:
            start = str(Path(self.path).parent)
        pats = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self.page, "Choose a video for the background image", start,
                                              f"Videos ({pats});;All files (*)")
        if path:
            self.load(path, 0.0)

    def seek(self):
        if self.path:
            self.load(self.path, self.time_spin.value(), quiet=True)

    def _slider_changed(self, v):
        if self._loading or self.src is None:
            return
        with loading(self):
            self.time_spin.setValue(v / self.src.fps)
        self._timer.start()

    def _spin_changed(self, v):
        if self._loading or self.src is None:
            return
        with loading(self):
            self.time_slider.setValue(int(round(v * self.src.fps)))
        self._timer.start()

    # ---- test videos ------------------------------------------------------------------------
    def use_test_video(self, test_id: int) -> bool:
        p = self.page.project
        t = p.get_test(test_id)
        if t is None or not t.video:
            return False
        return self.load(p.abs_path(t.video), t.start_s)

    def refresh_tests(self):
        self.test_combo.clear()
        self.test_combo.addItem("Use a test's video…", None)
        p = self.page.project
        if p is None:
            return
        for t in p.tests:
            if t.video:
                self.test_combo.addItem(f"Test {t.id}: {t.animal_id or '—'} · {Path(t.video).name}", t.id)
        self.test_combo.setEnabled(self.page.app is not None and self.test_combo.count() > 1)

    def _test_chosen(self, i: int):
        tid = self.test_combo.itemData(i)
        self.test_combo.setCurrentIndex(0)
        if tid is not None and self.page.project is not None:
            self.use_test_video(tid)

    def fill_test_menu(self):
        m = self.test_menu
        m.clear()
        p = self.page.project
        tests = [t for t in (p.tests if p else []) if t.video]
        if not tests:
            m.addAction("No test has a video yet").setEnabled(False)
            return
        cur = self.page.app
        for t in tests:
            a = m.addAction(icon("video"), f"Test {t.id}: {t.animal_id or '—'} · {Path(t.video).name}"
                            + ("  (this apparatus)" if cur is not None and t.apparatus == cur.name else ""))
            a.triggered.connect(lambda _=False, tid=t.id: self.use_test_video(tid))
