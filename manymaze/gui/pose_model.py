"""Pose-model management box: the animal the model is for, install (with licence acceptance) / remove the
deep-learning keypoint model, convert one's own DeepLabCut checkpoint or choose a custom ONNX model, and show which
accelerator will run it."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from ..core import pose
from .widgets import error_box, hint, run_with_progress

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
        self.species = QComboBox()
        for k, sp in pose.SPECIES.items():
            self.species.addItem(sp["title"], k)
        self.species.setToolTip("The animal filmed: the built-in model is for mice; for rats and other animals use "
                                "a model of your own")
        self.species.activated.connect(lambda i: self.set_species(self.species.itemData(i)))
        self.note = hint("")
        self.note.setTextFormat(Qt.RichText)
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
        self.convert_btn = QPushButton("Convert DeepLabCut model…")
        self.convert_btn.setToolTip("Convert your own DeepLabCut 3 RTMPose checkpoint (.pt), e.g. SuperAnimal-"
                                    "TopViewMouse fine-tuned on your rats, and use it")
        self.convert_btn.clicked.connect(lambda: self.convert_dialog())
        row = QHBoxLayout()
        row.setSpacing(6)
        for b in (self.install_btn, self.remove_btn, self.custom_btn, self.convert_btn):
            row.addWidget(b)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 2)
        lay.setSpacing(6)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Animal"))
        srow.addWidget(self.species)
        srow.addStretch()
        lay.addLayout(srow)
        lay.addWidget(self.note)
        lay.addWidget(self.status)
        lay.addLayout(row)
        self.refresh()

    def set_settings(self, settings):
        self.settings = settings
        self.refresh()

    def model(self) -> str:
        return (self.settings.pose_model if self.settings is not None else "") or DEFAULT_MODEL

    def species_key(self) -> str:
        sp = getattr(self.settings, "pose_species", "mouse") if self.settings is not None else "mouse"
        return sp if sp in pose.SPECIES else "mouse"

    def set_species(self, key: str):
        """The animal filmed: its built-in model if it has one, else (rats, other animals) a note on using one's
        own model (the current model is kept until another is chosen)."""
        if self.settings is None or key not in pose.SPECIES:
            return
        changed = self.settings.pose_species != key
        self.settings.pose_species = key
        built_in = pose.SPECIES[key]["model"]
        if built_in and self.settings.pose_model != built_in:
            self._set_model(built_in)
            return
        if changed:
            self.changed.emit()
        self.refresh()

    def refresh(self):
        m = self.model()
        self.setEnabled(self.settings is not None)
        sp = self.species_key()
        self.species.setCurrentIndex(max(0, self.species.findData(sp)))
        note = pose.SPECIES[sp].get("note", "")
        if note and m in pose.MODELS:
            note += f"<br><b>Until then the {pose.MODELS[m]['title']} model is used, which is made for mice.</b>"
        self.note.setText(note)
        self.note.setVisible(bool(note))
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

    def convert_dialog(self):
        """Choose one's own DeepLabCut RTMPose checkpoint, name its body parts and convert it (custom model)."""
        path, _ = QFileDialog.getOpenFileName(self, "DeepLabCut RTMPose checkpoint", str(Path.home()),
                                              "PyTorch checkpoints (*.pt *.pth);;All files (*)")
        if not path:
            return None
        try:
            n = pose.checkpoint_keypoint_count(path)
        except Exception as e:
            error_box(self, "Convert DeepLabCut model", e)
            return None
        dlg = ConvertModelDialog(Path(path).name, n, project_bodyparts(path, n), self.species_key(), self)
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return None
        return self.convert(path, **dlg.values())

    def convert(self, path: str, keypoints, parts: dict, input_size=(256, 256), species: str | None = None,
                wait: bool = False):
        """Convert a checkpoint (in the background unless ``wait``) and use it as the pose model."""
        species = species or self.species_key()

        def done(onnx_path):
            if self.settings is not None:
                self.settings.pose_species = species
            self._set_model(str(onnx_path))

        def work(progress, stop):
            return pose.convert_checkpoint(path, keypoints, parts, input_size, species, progress=progress)

        if wait:
            done(work(None, None))
            return self.model()
        return run_with_progress(self, "Converting the pose model", work, on_done=done)


def project_bodyparts(checkpoint, n: int) -> list[str]:
    """The body part names of the DeepLabCut project a checkpoint comes from (its pytorch_config.yaml, or the
    config.yaml of the project folder up to three levels above), when there are ``n`` of them; else []."""
    folder = Path(checkpoint).parent
    candidates = [folder / "pytorch_config.yaml"] + [d / "config.yaml" for d in [folder, *list(folder.parents)[:3]]]
    for f in candidates:
        if f.exists():
            try:
                names = pose.bodyparts_from_config(f)
            except Exception:
                continue
            if len(names) == n:
                return names
    return []


class ConvertModelDialog(QDialog):
    """The body part names of a DeepLabCut checkpoint (in the order of its project), which of them are the nose,
    body centre and tail base, the animal and the crop size it was trained on."""

    def __init__(self, name: str, n: int, names=(), species: str = "rat", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Convert DeepLabCut model")
        self.n = n
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"<b>{name}</b>: a DeepLabCut RTMPose model with {n} body parts."))
        f = QFormLayout()
        self.names = QLineEdit(", ".join(names))
        self.names.setPlaceholderText("nose, left_ear, right_ear, …")
        self.names.setToolTip("The body parts of the DeepLabCut project, in its order (config.yaml: bodyparts), "
                              "separated by commas")
        self.names.editingFinished.connect(self._fill_parts)
        f.addRow("Body parts", self.names)
        self.parts = {}
        for key, label in (("nose", "Nose (head)"), ("centre", "Body centre"), ("tail_base", "Tail base")):
            cb = QComboBox()
            self.parts[key] = cb
            f.addRow(label, cb)
        self.species = QComboBox()
        for k, sp in pose.SPECIES.items():
            self.species.addItem(sp["title"], k)
        self.species.setCurrentIndex(max(0, self.species.findData(species)))
        f.addRow("Animal", self.species)
        size = QHBoxLayout()
        self.width_, self.height_ = QSpinBox(), QSpinBox()
        for sp in (self.width_, self.height_):
            sp.setRange(32, 1024)
            sp.setSingleStep(32)
            sp.setValue(256)
        size.addWidget(self.width_)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height_)
        size.addStretch()
        f.addRow("Input size (px)", size)
        v.addLayout(f)
        v.addWidget(hint("Body parts left at “—” come from the animal's shape. The input size is the crop the model "
                         "was trained on (256 × 256 for DeepLabCut's RTMPose)."))
        self.error = hint("")
        v.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._fill_parts()

    def keypoints(self) -> list[str]:
        return [k.strip() for k in self.names.text().split(",") if k.strip()]

    def _fill_parts(self):
        names = self.keypoints()
        guess = {"nose": ("nose", "snout", "head"), "centre": ("center", "centre", "mid_back", "body", "spine"),
                 "tail_base": ("tail_base", "tailbase", "tail base", "base_tail")}
        for key, cb in self.parts.items():
            cur = cb.currentData()
            cb.clear()
            cb.addItem("—", "")
            for n in names:
                cb.addItem(n, n)
            i = cb.findData(cur) if cur else -1
            if i < 0:
                i = next((cb.findData(n) for g in guess[key] for n in names if g in n.lower()), 0)
            cb.setCurrentIndex(max(0, i))

    def values(self) -> dict:
        return {"keypoints": self.keypoints(), "parts": {k: cb.currentData() for k, cb in self.parts.items()},
                "input_size": (self.width_.value(), self.height_.value()), "species": self.species.currentData()}

    def accept(self):
        if len(self.keypoints()) != self.n:
            self.error.setText(f"Give the names of all {self.n} body parts ({len(self.keypoints())} so far).")
            return
        super().accept()
