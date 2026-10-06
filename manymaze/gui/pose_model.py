"""Pose-model management box: install (with licence acceptance) / remove the deep-learning keypoint model,
choose a custom ONNX model, and show which accelerator will run it."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout)

from ..core import pose
from .widgets import error_box, run_with_progress

DEFAULT_MODEL = "topviewmouse_rtmpose_s"


def accelerator_text() -> str:
    try:
        prov = pose.available_providers()
    except Exception:
        return "unavailable (onnxruntime is not installed)"
    if sys.platform == "darwin" and "CoreMLExecutionProvider" in prov:
        return "Core ML — Apple Neural Engine / GPU"
    if "CUDAExecutionProvider" in prov:
        return "CUDA GPU"
    return "CPU"


class PoseModelBox(QGroupBox):
    """Shows / installs the model referenced by a DetectionSettings object."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Pose model (AI body parts)", parent)
        self.settings = None
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        self.status.setOpenExternalLinks(True)
        self.install_btn = QPushButton("Install…")
        self.install_btn.clicked.connect(self.install)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove)
        self.custom_btn = QPushButton("Custom ONNX…")
        self.custom_btn.setToolTip("Use your own exported keypoint model (.onnx with a .json description)")
        self.custom_btn.clicked.connect(self.choose_custom)
        row = QHBoxLayout()
        row.setSpacing(6)
        for b in (self.install_btn, self.remove_btn, self.custom_btn):
            row.addWidget(b)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 2)
        lay.setSpacing(6)
        lay.addWidget(self.status)
        lay.addLayout(row)
        self.refresh()

    def set_settings(self, settings):
        self.settings = settings
        self.refresh()

    def model(self) -> str:
        return (self.settings.pose_model if self.settings is not None else "") or DEFAULT_MODEL

    def refresh(self):
        m = self.model()
        self.setEnabled(self.settings is not None)
        if m in pose.MODELS:
            info = pose.MODELS[m]
            ok = pose.is_installed(m)
            state = "<b style='color:#16a34a'>installed</b>" if ok else "<b style='color:#d97706'>not installed</b>"
            text = (f"{info['title']} — {state}<br><span style='color:#64748b'>{info['description']}. "
                    f"Licence: academic, non-commercial use only.</span>")
            self.install_btn.setText("Install…")
            self.install_btn.setVisible(not ok)
            self.remove_btn.setVisible(ok)
        else:
            ok = Path(m).exists()
            state = "<b style='color:#16a34a'>found</b>" if ok else "<b style='color:#dc2626'>missing</b>"
            text = f"Custom model <code>{Path(m).name}</code> — {state}"
            self.install_btn.setVisible(True)
            self.install_btn.setText("Use built-in model")
            self.remove_btn.setVisible(False)
        text += f"<br><span style='color:#64748b'>Runs on: {accelerator_text()}</span>"
        self.status.setText(text)

    def _set_model(self, m: str):
        self.settings.pose_model = m
        self.changed.emit()
        self.refresh()

    def install(self, confirm: bool = True):
        m = self.model()
        if m not in pose.MODELS:
            self._set_model(DEFAULT_MODEL)
            m = DEFAULT_MODEL
            if pose.is_installed(m):
                return None
        info = pose.MODELS[m]
        if confirm:
            box = QMessageBox(self)
            box.setWindowTitle("Install pose model")
            box.setIcon(QMessageBox.Question)
            box.setTextFormat(Qt.RichText)
            box.setText(f"<b>{info['title']}</b><br>{info['description']}.<br><br>"
                        f"Downloads {info.get('size_bytes', 0) / 1e6:.0f} MB from "
                        f"<a href='{info['license_url']}'>Hugging Face</a> and converts it for on-device inference "
                        f"({accelerator_text()}).<br><br><b>Licence of the model weights:</b> {info['license']}"
                        f"<br><br>Please cite: {info['citation']}")
            box.setStandardButtons(QMessageBox.Cancel)
            accept = box.addButton("Accept licence and install", QMessageBox.AcceptRole)
            box.exec()
            if box.clickedButton() is not accept:
                return None

        def done(_):
            self.refresh()
            self.changed.emit()

        return run_with_progress(self, "Installing pose model",
                                 lambda progress, stop: pose.install_model(m, progress, stop), on_done=done)

    def remove(self):
        m = self.model()
        if m in pose.MODELS and QMessageBox.question(self, "Remove pose model",
                                                     f"Delete the installed {pose.MODELS[m]['title']}?") \
                == QMessageBox.Yes:
            try:
                pose.uninstall_model(m)
            except Exception as e:
                error_box(self, "Remove pose model", e)
            self.refresh()

    def choose_custom(self):
        path, _ = QFileDialog.getOpenFileName(self, "Custom pose model", str(Path.home()), "ONNX models (*.onnx)")
        if not path:
            return
        try:
            pose.load_model_info(path)
        except Exception as e:
            error_box(self, "Custom pose model", f"{e}\n\nThe model needs a sidecar JSON describing its input "
                                                 "size, normalisation, outputs and keypoints (see the user guide).")
            return
        self._set_model(path)
