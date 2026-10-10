"""Deep-learning keypoint (pose) estimation with ONNX Runtime.

Top-down pose models run on crops around animals found by the tracker and
return keypoints (nose, body centre, tail base, ...) in frame coordinates.
The built-in model is DeepLabCut's SuperAnimal-TopViewMouse RTMPose-S; its
PyTorch checkpoint is downloaded once and converted to ONNX here without
PyTorch (the CSPNeXt backbone and RTMCC head are rebuilt with the ``onnx``
package).  Inference uses the CoreML execution provider on macOS (Neural
Engine / GPU), CUDA when available, and the CPU otherwise.

Custom models: any ONNX file with a sidecar ``<name>.json`` using the same
metadata schema as ``MODELS`` entries (``input_size``, ``mean``, ``std``,
``color``, ``output`` = "simcc" | "heatmap", ``keypoints``, ``parts``...).
One's own DeepLabCut 3 RTMPose checkpoints (e.g. TopViewMouse fine-tuned on
rats, for which no model has been released: see ``SPECIES``) are converted to
such a model by :func:`convert_checkpoint`.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import pickle
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

CONVERTER_VERSION = 1

_TVM_KEYPOINTS = [
    "nose", "left_ear", "right_ear", "left_ear_tip", "right_ear_tip", "left_eye", "right_eye", "neck",
    "mid_back", "mouse_center", "mid_backend", "mid_backend2", "mid_backend3", "tail_base", "tail1",
    "tail2", "tail3", "tail4", "tail5", "left_shoulder", "left_midside", "left_hip", "right_shoulder",
    "right_midside", "right_hip", "tail_end", "head_midpoint",
]

_SUPERANIMAL_LICENSE = (
    "Modified MIT licence, Copyright 2023 Mackenzie Mathis, Shaokai Ye and contributors (Mathis lab). "
    "The model weights are licensed for academic, non-commercial purposes only; they may not be used to "
    "harm any animal deliberately; provided as is, without warranty. Commercial use requires a licence "
    "from Prof. Mackenzie W. Mathis / the EPFL TTO (tto@epfl.ch). These terms apply to the downloaded "
    "weights, not to mANY-MAZE itself (GPL-3.0)."
)

MODELS: dict[str, dict] = {
    "topviewmouse_rtmpose_s": {
        "title": "SuperAnimal-TopViewMouse (RTMPose-S)",
        "description": "27 keypoints of mice filmed from above (DeepLabCut model zoo)",
        "url": "https://huggingface.co/mwmathis/DeepLabCutModelZoo-SuperAnimal-TopViewMouse/resolve/main/"
               "superanimal_topviewmouse_rtmpose_s.pt",
        "checkpoint": "superanimal_topviewmouse_rtmpose_s.pt",
        "sha256": "94181b5755804fb2c1c09600563ae56c3cbdc52634b88c4c2492a9c1c57dad99",
        "size_bytes": 23189760,
        "onnx": "topviewmouse_rtmpose_s.onnx",
        "meta": "topviewmouse_rtmpose_s.json",
        "architecture": "rtmpose",
        "input_size": [256, 256],  # (width, height)
        "color": "RGB",
        "mean": [0.485, 0.456, 0.406],  # applied to pixel / 255
        "std": [0.229, 0.224, 0.225],
        "crop": "context",  # square crop around the box centre, zero padded, as DeepLabCut's top_down_crop
        "margin": 0,
        "output": "simcc",
        "simcc_split_ratio": 2.0,
        "sigma": [5.66, 5.66],
        "decode_beta": 150.0,
        "keypoints": _TVM_KEYPOINTS,
        "parts": {"nose": "nose", "centre": "mouse_center", "tail_base": "tail_base"},
        "license": _SUPERANIMAL_LICENSE,
        "license_url": "https://huggingface.co/mwmathis/DeepLabCutModelZoo-SuperAnimal-TopViewMouse",
        "citation": "Ye S, Filippova A, Lauer J, Schneider S, Vidal M, Qiu T, Mathis A, Mathis MW (2024). "
                    "SuperAnimal pretrained pose estimation models for behavioral analysis. "
                    "Nature Communications 15, 5165. doi:10.1038/s41467-024-48792-2",
    },
}

_META_DEFAULTS = {
    "color": "RGB", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225], "crop": "context",
    "margin": 0, "output": "simcc", "simcc_split_ratio": 2.0, "sigma": [5.66, 5.66], "decode_beta": 150.0,
    "heatmap_activation": "none", "parts": {},
}

# The animals offered by the pose model choice: the built-in model of each, or None when no released model exists
# (the user then converts or chooses a model of their own).  Checked in October 2026: DeepLabCut's SuperAnimal
# family has TopViewMouse (mice from above; its model card says it is not suitable for other species) and
# Quadruped (side views); no top-view rat model has been released by DeepLabCut, SLEAP or MMPose.
RAT_MODEL_NOTE = (
    "No pose model of rats filmed from above has been released (checked in October 2026: DeepLabCut's SuperAnimal "
    "models are TopViewMouse, for mice only, and Quadruped, for animals filmed from the side). Use a model of your "
    "own: fine-tune SuperAnimal-TopViewMouse RTMPose-S on frames of your rats in DeepLabCut 3 and convert the "
    "checkpoint here, or choose an ONNX model with its JSON description."
)
SPECIES: dict[str, dict] = {
    "mouse": {"title": "Mouse", "model": "topviewmouse_rtmpose_s"},
    "rat": {"title": "Rat", "model": None, "note": RAT_MODEL_NOTE},
    "other": {"title": "Another animal", "model": None,
              "note": "Use a keypoint model of your own: convert a DeepLabCut RTMPose checkpoint here, or choose an "
                      "ONNX model with its JSON description."},
}


# --------------------------------------------------------------------------- storage

def models_dir() -> Path:
    """Per-user directory holding converted models (env MANYMAZE_MODELS overrides)."""
    env = os.environ.get("MANYMAZE_MODELS")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mANY-MAZE" / "models"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "mANY-MAZE" / "models"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "manymaze" / "models"


def model_paths(key: str) -> tuple[Path, Path]:
    d = models_dir()
    return d / MODELS[key]["onnx"], d / MODELS[key]["meta"]


def is_installed(key: str) -> bool:
    onnx_path, meta_path = model_paths(key)
    return onnx_path.exists() and meta_path.exists()


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, progress=None, should_stop=None, expected_size: int = 0):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "mANY-MAZE"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) or expected_size
            done = 0
            while True:
                if should_stop is not None and should_stop():
                    raise InterruptedError("download cancelled")
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress is not None and total:
                    progress(min(done / total, 1.0))
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def install_model(key: str, progress: Callable[[float], None] | None = None,
                  should_stop: Callable[[], bool] | None = None, source_path: str | None = None) -> Path:
    """Download (or take ``source_path``), convert to ONNX and store a model; returns the .onnx path.

    ``progress`` receives 0..1 (download is 0..0.9, conversion the rest); ``should_stop``
    returning True cancels with InterruptedError.
    """
    spec = MODELS[key]
    onnx_path, meta_path = model_paths(key)
    downloaded = source_path is None
    pt = Path(source_path) if source_path else models_dir() / spec["checkpoint"]
    if downloaded:
        _download(spec["url"], pt, (lambda p: progress(0.9 * p)) if progress else None, should_stop,
                  spec.get("size_bytes", 0))
    try:
        digest = _sha256(pt)
        if spec.get("sha256") and digest != spec["sha256"]:
            raise ValueError(f"{pt.name}: unexpected SHA-256 {digest}")
        if should_stop is not None and should_stop():
            raise InterruptedError("installation cancelled")
        state = find_state_dict(load_torch_checkpoint(pt))
        meta = _meta_from_spec(key)
        meta["source_sha256"] = digest
        model = build_rtmpose_onnx(state, meta)
        if progress:
            progress(0.97)
        _atomic_write(onnx_path, model.SerializeToString())
        _atomic_write(meta_path, json.dumps(meta, indent=1).encode())
    finally:
        if downloaded:
            pt.unlink(missing_ok=True)
    if progress:
        progress(1.0)
    return onnx_path


def uninstall_model(key: str):
    for p in model_paths(key):
        p.unlink(missing_ok=True)


def checkpoint_keypoint_count(path) -> int:
    """Keypoints of a DeepLabCut RTMPose checkpoint (from its head)."""
    sd = find_state_dict(load_torch_checkpoint(path))
    w = sd.get("heads.bodypart.final_layer.weight")
    if w is None or "backbone.stem.0.conv.weight" not in sd:
        raise ValueError(f"{Path(path).name} is not a DeepLabCut RTMPose (CSPNeXt) checkpoint")
    return int(w.shape[0])


def convert_checkpoint(path, keypoints: Sequence[str], parts: dict, input_size: Sequence[int] = (256, 256),
                       species: str = "", dest=None, progress: Callable[[float], None] | None = None) -> Path:
    """Convert one's own DeepLabCut 3 RTMPose checkpoint (e.g. SuperAnimal-TopViewMouse RTMPose-S fine-tuned on
    rats) to an ONNX model with its JSON description, usable as a custom model; returns the .onnx path.

    ``keypoints`` are the model's body part names in order (the project's ``bodyparts``), ``parts`` maps "nose",
    "centre" and "tail_base" to them, ``input_size`` is the crop (width, height) the model was trained on.  The
    model goes to ``dest`` (a .onnx path), by default ``custom/<checkpoint name>.onnx`` in the models folder."""
    path = Path(path)
    if progress:
        progress(0.05)
    sd = find_state_dict(load_torch_checkpoint(path))
    w = sd.get("heads.bodypart.final_layer.weight")
    if w is None:
        raise ValueError(f"{path.name} is not a DeepLabCut RTMPose (CSPNeXt) checkpoint")
    keypoints = [str(k).strip() for k in keypoints if str(k).strip()]
    if len(keypoints) != int(w.shape[0]):
        raise ValueError(f"The model has {int(w.shape[0])} keypoints but {len(keypoints)} body part names were "
                         "given: list them all, in the order of the DeepLabCut project")
    meta = dict(_META_DEFAULTS)
    meta.update(title=f"{path.stem} ({species})" if species else path.stem, architecture="rtmpose",
                input_size=[int(v) for v in input_size], keypoints=keypoints,
                parts={k: v for k, v in parts.items() if v}, converter_version=CONVERTER_VERSION,
                source=path.name, source_sha256=_sha256(path))
    if species:
        meta["species"] = species
    meta = validate_meta(meta)
    if progress:
        progress(0.3)
    model = build_rtmpose_onnx(sd, meta)
    if progress:
        progress(0.9)
    onnx_path = Path(dest) if dest else models_dir() / "custom" / (path.stem + ".onnx")
    _atomic_write(onnx_path, model.SerializeToString())
    _atomic_write(onnx_path.with_suffix(".json"), json.dumps(meta, indent=1).encode())
    if progress:
        progress(1.0)
    return onnx_path


def bodyparts_from_config(path) -> list[str]:
    """The body part names of a DeepLabCut project: ``bodyparts`` (or ``multianimalbodyparts``) of its
    config.yaml, or ``metadata: bodyparts`` of a pytorch_config.yaml ([] when there are none)."""
    import re

    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    for key in ("bodyparts", "multianimalbodyparts"):
        for i, line in enumerate(lines):
            m = re.match(rf"^(\s*){key}:\s*(.*?)\s*$", line)
            if not m:
                continue
            rest = m.group(2)
            if rest.startswith("["):
                names = [n.strip().strip("'\"") for n in rest.strip("[]").split(",")]
            else:
                names = []
                indent = len(m.group(1))
                for nxt in lines[i + 1:]:
                    item = re.match(r"^(\s*)-\s*(.+?)\s*$", nxt)
                    if item is None or len(item.group(1)) < indent:
                        break
                    names.append(item.group(2).strip("'\""))
            names = [n for n in names if n]
            if names:
                return names
    return []


def _meta_from_spec(key: str) -> dict:
    spec = MODELS[key]
    skip = {"url", "checkpoint", "sha256", "size_bytes", "onnx", "meta"}
    meta = {k: v for k, v in spec.items() if k not in skip}
    meta["key"] = key
    meta["converter_version"] = CONVERTER_VERSION
    return meta


# --------------------------------------------------------------------------- torch-free checkpoint reading

_STORAGE_DTYPES = {
    "FloatStorage": np.float32, "DoubleStorage": np.float64, "HalfStorage": np.float16,
    "BFloat16Storage": "bfloat16", "LongStorage": np.int64, "IntStorage": np.int32,
    "ShortStorage": np.int16, "CharStorage": np.int8, "ByteStorage": np.uint8, "BoolStorage": np.bool_,
}


class _StorageType:
    def __init__(self, name: str):
        self.dtype = _STORAGE_DTYPES[name]


class _Opaque:
    """Stand-in for any non-tensor class found in a checkpoint (never executes its code)."""

    def __init__(self, *args, **kwargs):
        self.args = args

    def __setstate__(self, state):
        self.state = state


def _rebuild_tensor(storage, offset, size, stride, *args):
    size, stride = tuple(size), tuple(stride)
    if not size:
        return np.array(storage[offset])
    n = 1 + sum((s - 1) * st for s, st in zip(size, stride)) if all(size) else 0
    flat = storage[offset:offset + n]
    return np.lib.stride_tricks.as_strided(flat, shape=size, strides=[s * flat.itemsize for s in stride]).copy()


def load_torch_checkpoint(path) -> object:
    """Read a PyTorch zip checkpoint into nested dicts of NumPy arrays (no torch needed)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        pkl = next((n for n in names if n.endswith("data.pkl")), None)
        if pkl is None:
            raise ValueError(f"{path}: not a PyTorch zip checkpoint")
        root = pkl[: -len("data.pkl")]
        cache = {}

        def load_storage(dtype, key):
            if key not in cache:
                raw = z.read(f"{root}data/{key}")
                if dtype == "bfloat16":
                    arr = (np.frombuffer(raw, "<u2").astype(np.uint32) << 16).view(np.float32)
                else:
                    arr = np.frombuffer(raw, np.dtype(dtype).newbyteorder("<"))
                cache[key] = arr
            return cache[key]

        class Unpickler(pickle.Unpickler):
            def find_class(self, module, name):
                if module == "torch._utils" and name == "_rebuild_tensor_v2":
                    return _rebuild_tensor
                if module == "torch._utils" and name == "_rebuild_parameter":
                    return lambda data, *a: data
                if module == "torch" and name in _STORAGE_DTYPES:
                    return _StorageType(name)
                if module == "collections" and name == "OrderedDict":
                    return collections.OrderedDict
                return _Opaque

            def persistent_load(self, pid):
                if not isinstance(pid, tuple) or pid[0] != "storage":
                    raise pickle.UnpicklingError(f"unsupported persistent id {pid!r}")
                st, key = pid[1], pid[2]
                dtype = st.dtype if isinstance(st, _StorageType) else np.float32
                return load_storage(dtype, key)

        with z.open(pkl) as f:
            return Unpickler(f).load()


def find_state_dict(obj) -> dict[str, np.ndarray]:
    """Locate the tensor dict in a checkpoint (``{"model": ...}``, ``{"state_dict": ...}``, ...)."""
    if isinstance(obj, dict):
        tensors = {k: v for k, v in obj.items() if isinstance(v, np.ndarray)}
        if tensors and len(tensors) >= len(obj) / 2:
            return {k[7:] if k.startswith("module.") else k: v for k, v in tensors.items()}
        for k in ("model", "state_dict", "model_state_dict", "net"):
            if isinstance(obj.get(k), dict):
                return find_state_dict(obj[k])
        for v in obj.values():
            if isinstance(v, dict):
                try:
                    return find_state_dict(v)
                except ValueError:
                    pass
    raise ValueError("no state dict found in checkpoint")


# --------------------------------------------------------------------------- RTMPose -> ONNX

_BN_EPS = 1e-5
_SCALENORM_EPS = 1e-5
_SPP_KERNELS = (5, 9, 13)


class _Graph:
    def __init__(self, helper, numpy_helper, sd: dict):
        self.h, self.nh, self.sd = helper, numpy_helper, sd
        self.nodes, self.inits = [], []

    def const(self, arr, name: str | None = None) -> str:
        name = name or f"const_{len(self.inits)}"
        self.inits.append(self.nh.from_array(np.ascontiguousarray(arr), name))
        return name

    def op(self, op_type: str, inputs: Sequence[str], out: str | None = None, **attrs) -> str:
        out = out or f"{op_type.lower()}_{len(self.nodes)}"
        self.nodes.append(self.h.make_node(op_type, list(inputs), [out], **attrs))
        return out

    def w(self, key: str) -> np.ndarray:
        return np.asarray(self.sd[key], dtype=np.float32)

    def has(self, key: str) -> bool:
        return key in self.sd

    def silu(self, x: str) -> str:
        return self.op("Mul", [x, self.op("Sigmoid", [x])])

    def conv(self, x: str, p: str, stride: int = 1, act: bool = True, depthwise: bool = False) -> str:
        """CSPConvModule (conv + folded BatchNorm + SiLU)."""
        w = self.w(p + ".conv.weight")
        if self.has(p + ".norm.weight"):
            scale = self.w(p + ".norm.weight") / np.sqrt(self.w(p + ".norm.running_var") + _BN_EPS)
            b = self.w(p + ".norm.bias") - self.w(p + ".norm.running_mean") * scale
            w = w * scale[:, None, None, None]
        else:
            b = self.w(p + ".conv.bias") if self.has(p + ".conv.bias") else np.zeros(w.shape[0], np.float32)
        k = w.shape[-1]
        y = self.op("Conv", [x, self.const(w, p + ".w"), self.const(b, p + ".b")], kernel_shape=[k, k],
                    strides=[stride, stride], pads=[k // 2] * 4, group=w.shape[0] if depthwise else 1)
        return self.silu(y) if act else y

    def csp_layer(self, x: str, p: str, add_identity: bool) -> str:
        short = self.conv(x, p + ".short_conv")
        main = self.conv(x, p + ".main_conv")
        i = 0
        while self.has(f"{p}.blocks.{i}.conv1.conv.weight"):
            b = f"{p}.blocks.{i}"
            y = self.conv(main, b + ".conv1")
            y = self.conv(y, b + ".conv2.depthwise_conv", depthwise=True)
            y = self.conv(y, b + ".conv2.pointwise_conv")
            main = self.op("Add", [y, main]) if add_identity else y
            i += 1
        y = self.op("Concat", [main, short], axis=1)
        if self.has(p + ".attention.fc.weight"):
            # Hardsigmoid(fc(avgpool)) == Clip(fc'(avgpool), 0, 1) with fc' = fc / 6 + 0.5
            a = self.op("GlobalAveragePool", [y])
            fw = self.w(p + ".attention.fc.weight") / 6.0
            fb = self.w(p + ".attention.fc.bias") / 6.0 + 0.5
            a = self.op("Conv", [a, self.const(fw), self.const(fb)], kernel_shape=[1, 1])
            a = self.op("Clip", [a, self.const(np.float32(0)), self.const(np.float32(1))])
            y = self.op("Mul", [y, a])
        return self.conv(y, p + ".final_conv")

    def spp(self, x: str, p: str) -> str:
        x = self.conv(x, p + ".conv1")
        pools = [self.op("MaxPool", [x], kernel_shape=[k, k], strides=[1, 1], pads=[k // 2] * 4)
                 for k in _SPP_KERNELS]
        return self.conv(self.op("Concat", [x] + pools, axis=1), p + ".conv2")

    def scale_norm(self, x: str, prescale: float) -> str:
        """DeepLabCut ScaleNorm without its gain (folded into the next MatMul).

        x / max(sqrt(mean(x^2)), eps), computed on x / prescale so that x^2 stays
        within float16 range on the Neural Engine / GPU.
        """
        y = self.op("Mul", [x, self.const(np.float32(1.0 / prescale))])
        n = self.op("Sqrt", [self.op("ReduceMean", [self.op("Mul", [y, y])], axes=[-1], keepdims=1)])
        n = self.op("Clip", [n, self.const(np.float32(_SCALENORM_EPS / prescale)), ""])
        return self.op("Div", [y, n])

    def linear(self, x: str, weight: np.ndarray) -> str:
        return self.op("MatMul", [x, self.const(np.ascontiguousarray(weight.T))])


def rtmpose_layout(sd: dict, prefix: str = "backbone") -> dict:
    """Describe the CSPNeXt stages present in a state dict."""
    stages = []
    i = 1
    while f"{prefix}.stage{i}.0.conv.weight" in sd:
        spp = f"{prefix}.stage{i}.1.conv1.conv.weight" in sd and f"{prefix}.stage{i}.1.main_conv.conv.weight" not in sd
        csp = f"{prefix}.stage{i}.{2 if spp else 1}"
        nb = 0
        while f"{csp}.blocks.{nb}.conv1.conv.weight" in sd:
            nb += 1
        stages.append({"spp": spp, "blocks": nb, "channels": int(sd[f"{prefix}.stage{i}.0.conv.weight"].shape[0])})
        i += 1
    return {"stages": stages}


def build_rtmpose_onnx(sd: dict, meta: dict, head: str = "heads.bodypart", opset: int = 17):
    """Build an ONNX graph for a DeepLabCut RTMPose (CSPNeXt-P5 + RTMCC head) state dict.

    Input ``image`` (batch, 3, H, W) float32 normalised; outputs ``simcc_x`` (batch, K, W*r)
    and ``simcc_y`` (batch, K, H*r) logits.
    """
    from onnx import TensorProto, checker, helper, numpy_helper

    g = _Graph(helper, numpy_helper, sd)
    w_in, h_in = meta["input_size"]
    x = "image"
    x = g.conv(x, "backbone.stem.0", stride=2)
    x = g.conv(x, "backbone.stem.1")
    x = g.conv(x, "backbone.stem.2")
    layout = rtmpose_layout(sd)
    if not layout["stages"]:
        raise ValueError("state dict does not contain a CSPNeXt backbone")
    if f"{head}.gau.w" in sd:
        raise ValueError("RTMCC heads with relative position bias are not supported")
    for i, st in enumerate(layout["stages"], 1):
        p = f"backbone.stage{i}"
        x = g.conv(x, p + ".0", stride=2)
        if st["spp"]:
            x = g.spp(x, p + ".1")
        x = g.csp_layer(x, f"{p}.{2 if st['spp'] else 1}", add_identity=not st["spp"])

    fl = g.w(f"{head}.final_layer.weight")
    k = fl.shape[0]
    x = g.op("Conv", [x, g.const(fl, "final_layer.w"), g.const(g.w(f"{head}.final_layer.bias"), "final_layer.b")],
             kernel_shape=list(fl.shape[2:]), pads=[fl.shape[-1] // 2] * 4)
    fh, fw = h_in // 32, w_in // 32
    x = g.op("Reshape", [x, g.const(np.array([-1, k, fh * fw], np.int64))])
    x = g.scale_norm(x, prescale=16.0)
    x = g.linear(x, g.w(f"{head}.mlp.1.weight") * g.w(f"{head}.mlp.0.g")[0])

    # gated attention unit (self-attention, no relative bias, no rotary encoding)
    gp = f"{head}.gau"
    uv = g.w(gp + ".uv.weight") * g.w(gp + ".ln.g")[0]
    e = g.w(gp + ".o.weight").shape[1]
    s = g.w(gp + ".gamma").shape[1]
    gamma, beta = g.w(gp + ".gamma"), g.w(gp + ".beta")
    n = g.scale_norm(x, prescale=4.0)
    u = g.silu(g.linear(n, uv[:e]))
    v = g.silu(g.linear(n, uv[e:2 * e]))
    base = g.silu(g.linear(n, uv[2 * e:2 * e + s]))
    rs = 1.0 / math.sqrt(s)
    q = g.op("Add", [g.op("Mul", [base, g.const(gamma[0] * rs)]), g.const(beta[0] * rs)])
    kk = g.op("Add", [g.op("Mul", [base, g.const(gamma[1])]), g.const(beta[1])])
    qk = g.op("MatMul", [q, g.op("Transpose", [kk], perm=[0, 2, 1])])
    kern = g.op("Relu", [qk])
    kern = g.op("Mul", [kern, kern])
    y = g.op("Mul", [u, g.op("MatMul", [kern, v])])
    y = g.linear(y, g.w(gp + ".o.weight"))
    if g.has(gp + ".res_scale.scale"):
        y = g.op("Add", [g.op("Mul", [x, g.const(g.w(gp + ".res_scale.scale"))]), y])
    sx = g.w(f"{head}.cls_x.weight")
    sy = g.w(f"{head}.cls_y.weight")
    g.op("MatMul", [y, g.const(np.ascontiguousarray(sx.T))], out="simcc_x")
    g.op("MatMul", [y, g.const(np.ascontiguousarray(sy.T))], out="simcc_y")

    graph = helper.make_graph(
        g.nodes, "rtmpose",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["batch", 3, h_in, w_in])],
        [helper.make_tensor_value_info("simcc_x", TensorProto.FLOAT, ["batch", k, sx.shape[0]]),
         helper.make_tensor_value_info("simcc_y", TensorProto.FLOAT, ["batch", k, sy.shape[0]])],
        g.inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", opset)],
                              producer_name="manymaze", producer_version=str(CONVERTER_VERSION))
    model.ir_version = 8
    meta_json = json.dumps(meta, sort_keys=True)
    cache_key = hashlib.sha256((meta_json + str(CONVERTER_VERSION)).encode()).hexdigest()[:40]
    helper.set_model_props(model, {"manymaze": meta_json, "COREML_CACHE_KEY": cache_key})
    checker.check_model(model)
    return model


# --------------------------------------------------------------------------- pre/post-processing

def crop_box(frame: np.ndarray, box, size: tuple[int, int], margin: float = 0,
             context: bool = True) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    """Crop ``box`` (x0, y0, x1, y1) and resize to ``size`` (w, h) like DeepLabCut's top_down_crop.

    The box is grown about its centre to the output aspect ratio (``context``),
    out-of-frame parts are zero padded.  Returns (crop, offset, scale) with
    frame = crop * scale + offset.
    """
    ih, iw = frame.shape[:2]
    out_w, out_h = size
    x0, y0, x1, y1 = (float(v) for v in box)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    cx, cy = x0 + w / 2, y0 + h / 2
    w += 2 * margin
    h += 2 * margin
    if context:
        r_out = out_w / out_h
        if w / h > r_out:
            h = w / r_out
        elif w / h < r_out:
            w = h * r_out
    bx1, by1 = int(round(cx - w / 2)), int(round(cy - h / 2))
    bx2, by2 = int(round(cx + w / 2)), int(round(cy + h / 2))
    pl, pt = max(-bx1, 0), max(-by1, 0)
    pr, pb = max(bx2 - iw, 0), max(by2 - ih, 0)
    bx1, by1, bx2, by2 = max(bx1, 0), max(by1, 0), min(bx2, iw), min(by2, ih)
    cw, ch = max(bx2 - bx1, 0), max(by2 - by1, 0)
    if not context and cw and ch:
        r_out = out_w / out_h
        if cw / ch > r_out:  # too wide: pad rows up to cw / r_out
            d = int(round(cw / r_out - ch)) // 2
            pt, pb = pt + d, pb + d
        elif cw / ch < r_out:  # too tall: pad columns up to ch * r_out
            d = int(round(ch * r_out - cw)) // 2
            pl, pr = pl + d, pr + d
    canvas = np.zeros((ch + pt + pb, cw + pl + pr) + frame.shape[2:], dtype=frame.dtype)
    canvas[pt:pt + ch, pl:pl + cw] = frame[by1:by2, bx1:bx2]
    crop = cv2.resize(canvas, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    offset = (bx1 - pl, by1 - pt)
    scale = (canvas.shape[1] / out_w, canvas.shape[0] / out_h)
    return crop, offset, scale


def decode_simcc(simcc_x: np.ndarray, simcc_y: np.ndarray, split_ratio: float = 2.0,
                 sigma=(5.66, 5.66), beta: float = 150.0) -> np.ndarray:
    """SimCC logits (N, K, Wx), (N, K, Wy) -> (N, K, 3) of x, y (input pixels), confidence.

    As DeepLabCut's SimCCPredictor: softmax of logits * sigma * beta, argmax,
    confidence = min of the x / y maxima; low-confidence points become NaN.
    """
    def softmax(a, s):
        a = a.astype(np.float64) * s
        a -= a.max(axis=-1, keepdims=True)
        np.exp(a, out=a)
        return a / a.sum(axis=-1, keepdims=True)

    sigma = np.broadcast_to(np.asarray(sigma, float), (2,))
    px, py = softmax(simcc_x, sigma[0] * beta), softmax(simcc_y, sigma[1] * beta)
    out = np.empty(simcc_x.shape[:-1] + (3,), np.float64)
    out[..., 0] = px.argmax(-1) / split_ratio
    out[..., 1] = py.argmax(-1) / split_ratio
    out[..., 2] = np.minimum(px.max(-1), py.max(-1))
    out[out[..., 2] <= 1.0 / simcc_x.shape[-1], :2] = np.nan
    return out


def decode_heatmap(heatmaps: np.ndarray, input_size: tuple[int, int], activation: str = "none") -> np.ndarray:
    """Heatmaps (N, K, h, w) -> (N, K, 3) of x, y (input pixels), confidence.

    Argmax with quarter-pixel refinement towards the larger neighbour; heatmap
    cell centres map to (i + 0.5) * stride.
    """
    hm = heatmaps.astype(np.float32)
    if activation == "sigmoid":
        hm = 1.0 / (1.0 + np.exp(-hm))
    n, k, h, w = hm.shape
    flat = hm.reshape(n, k, -1)
    idx = flat.argmax(-1)
    yy, xx = np.divmod(idx, w)
    conf = np.take_along_axis(flat, idx[..., None], -1)[..., 0]
    fx, fy = xx.astype(np.float64), yy.astype(np.float64)
    ni, ki = np.indices((n, k))
    right, left = hm[ni, ki, yy, np.minimum(xx + 1, w - 1)], hm[ni, ki, yy, np.maximum(xx - 1, 0)]
    down, up = hm[ni, ki, np.minimum(yy + 1, h - 1), xx], hm[ni, ki, np.maximum(yy - 1, 0), xx]
    fx += 0.25 * np.sign(right - left) * ((xx > 0) & (xx < w - 1))
    fy += 0.25 * np.sign(down - up) * ((yy > 0) & (yy < h - 1))
    sx, sy = input_size[0] / w, input_size[1] / h
    return np.stack([(fx + 0.5) * sx, (fy + 0.5) * sy, conf], axis=-1)


# --------------------------------------------------------------------------- inference

def available_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    return list(ort.get_available_providers())


def load_model_info(path) -> dict:
    """Metadata for an ONNX model: sidecar JSON, else embedded 'manymaze' metadata, else MODELS."""
    path = Path(path)
    side = path.with_suffix(".json")
    meta = None
    if side.exists():
        meta = json.loads(side.read_text(encoding="utf-8"))
    else:
        try:
            import onnx
            m = onnx.load(str(path), load_external_data=False)
            props = {p.key: p.value for p in m.metadata_props}
            if "manymaze" in props:
                meta = json.loads(props["manymaze"])
        except Exception:
            meta = None
    if meta is None and path.stem in MODELS:
        meta = _meta_from_spec(path.stem)
    if meta is None:
        raise FileNotFoundError(f"no model metadata found for {path} (expected {side.name})")
    return validate_meta(meta)


def validate_meta(meta: dict) -> dict:
    m = dict(_META_DEFAULTS)
    m.update(meta)
    for key in ("input_size", "keypoints"):
        if key not in m:
            raise ValueError(f"model metadata is missing {key!r}")
    m["input_size"] = [int(v) for v in m["input_size"]]
    if m["output"] not in ("simcc", "heatmap"):
        raise ValueError(f"unknown model output kind {m['output']!r}")
    names = set(m["keypoints"])
    for part, kp in m["parts"].items():
        for k in [kp] if isinstance(kp, str) else kp:
            if k not in names:
                raise ValueError(f"part {part!r} refers to unknown keypoint {k!r}")
    return m


def _resolve(model) -> Path:
    if isinstance(model, str) and model in MODELS:
        path = model_paths(model)[0]
        if not path.exists():
            raise FileNotFoundError(f"pose model {model!r} is not installed")
        return path
    path = Path(model)
    if not path.exists():
        raise FileNotFoundError(path)
    return path


class PoseEstimator:
    """Top-down keypoint estimator on ONNX Runtime.

    ``model`` is a MODELS key (installed) or a path to an .onnx file with
    metadata.  ``device``: "auto" (CoreML on macOS, else CUDA, else CPU),
    "cpu", "coreml" or "cuda".  On CoreML the batch dimension is fixed
    (``batch_size``, default 1) so the whole graph can run on the ANE/GPU.
    """

    def __init__(self, model="topviewmouse_rtmpose_s", device: str = "auto", batch_size: int | None = None,
                 threads: int = 0):
        import onnxruntime as ort

        self.threads = threads

        self.path = _resolve(model)
        self.meta = load_model_info(self.path)
        self.keypoint_names = list(self.meta["keypoints"])
        self.input_size = tuple(self.meta["input_size"])
        avail = ort.get_available_providers()
        want = device.lower()
        if want == "auto":
            want = "coreml" if sys.platform == "darwin" and "CoreMLExecutionProvider" in avail else \
                "cuda" if "CUDAExecutionProvider" in avail else "cpu"
        attempts = []
        if want == "coreml" and "CoreMLExecutionProvider" in avail:
            cache = models_dir() / "coreml-cache" / f"batch{batch_size or 1}"
            full = {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL", "ModelCacheDirectory": str(cache)}
            attempts += [("CoreMLExecutionProvider", full, batch_size or 1),
                         ("CoreMLExecutionProvider", {"MLComputeUnits": "ALL"}, batch_size or 1)]
        elif want == "cuda" and "CUDAExecutionProvider" in avail:
            attempts.append(("CUDAExecutionProvider", {}, batch_size))
        attempts.append(("CPUExecutionProvider", {}, batch_size))
        err = None
        for name, opts, batch in attempts:
            try:
                self.session = self._session(ort, name, opts, batch)
                break
            except Exception as e:
                err = e
        else:
            raise RuntimeError(f"cannot create inference session for {self.path}: {err}")
        self.provider = self.session.get_providers()[0]
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        b = inp.shape[0]
        self.batch = b if isinstance(b, int) and b > 0 else 0
        self._mean = np.asarray(self.meta["mean"], np.float32) * 255.0
        self._inv_std = 1.0 / (np.asarray(self.meta["std"], np.float32) * 255.0)

    def _session(self, ort, provider: str, opts: dict, batch: int | None):
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        if self.threads:
            so.intra_op_num_threads = self.threads
        elif hasattr(os, "sched_getaffinity"):
            so.intra_op_num_threads = len(os.sched_getaffinity(0))
        if batch:
            so.add_free_dimension_override_by_name("batch", int(batch))
        if opts.get("ModelCacheDirectory"):
            Path(opts["ModelCacheDirectory"]).mkdir(parents=True, exist_ok=True)
        providers = [(provider, opts)] if provider != "CPUExecutionProvider" else []
        providers.append("CPUExecutionProvider")
        sess = ort.InferenceSession(str(self.path), sess_options=so, providers=providers)
        if sess.get_providers()[0] != provider:
            raise RuntimeError(f"{provider} not usable")
        return sess

    def preprocess(self, frame_bgr: np.ndarray, boxes) -> tuple[np.ndarray, list]:
        """Crops -> normalised NCHW batch, plus (offset, scale) per box."""
        margin = self.meta.get("margin", 0)
        context = self.meta.get("crop", "context") == "context"
        batch = np.empty((len(boxes), 3, self.input_size[1], self.input_size[0]), np.float32)
        tfs = []
        for i, box in enumerate(boxes):
            crop, off, sc = crop_box(frame_bgr, box, self.input_size, margin, context)
            if crop.ndim == 2:
                crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            if self.meta["color"].upper() == "RGB":
                crop = crop[..., ::-1]
            batch[i] = ((crop.astype(np.float32) - self._mean) * self._inv_std).transpose(2, 0, 1)
            tfs.append((off, sc))
        return batch, tfs

    def infer(self, batch: np.ndarray) -> list[np.ndarray]:
        """Run the network on a preprocessed batch (chunked / padded to the session batch)."""
        n = len(batch)
        step = self.batch or n
        outs = None
        for s in range(0, n, step):
            chunk = batch[s:s + step]
            m = len(chunk)
            if self.batch and m < self.batch:
                chunk = np.concatenate([chunk, np.zeros((self.batch - m,) + chunk.shape[1:], chunk.dtype)])
            res = self.session.run(None, {self.input_name: chunk})
            outs = [[] for _ in res] if outs is None else outs
            for acc, r in zip(outs, res):
                acc.append(r[:m])
        return [np.concatenate(a) for a in outs]

    def decode(self, outputs: list[np.ndarray]) -> np.ndarray:
        m = self.meta
        if m["output"] == "simcc":
            names = [o.name for o in self.session.get_outputs()]
            ix = names.index("simcc_x") if "simcc_x" in names else 0
            iy = names.index("simcc_y") if "simcc_y" in names else 1
            return decode_simcc(outputs[ix], outputs[iy], m["simcc_split_ratio"], m["sigma"], m["decode_beta"])
        return decode_heatmap(outputs[0], self.input_size, m.get("heatmap_activation", "none"))

    def predict(self, frame_bgr: np.ndarray, boxes) -> list[np.ndarray]:
        """Keypoints for each box (x0, y0, x1, y1): list of (K, 3) arrays x, y, confidence (frame pixels)."""
        boxes = list(boxes)
        if not boxes:
            return []
        batch, tfs = self.preprocess(frame_bgr, boxes)
        kp = self.decode(self.infer(batch))
        res = []
        for k, ((ox, oy), (sx, sy)) in zip(kp, tfs):
            k = k.copy()
            k[:, 0] = k[:, 0] * sx + ox
            k[:, 1] = k[:, 1] * sy + oy
            res.append(k)
        return res

    def body_parts(self, kpts: np.ndarray) -> dict[str, tuple[float, float, float]]:
        """Map keypoints to nose / centre / tail_base (x, y, conf); lists of keypoints are averaged."""
        out = {}
        for part, names in self.meta["parts"].items():
            names = [names] if isinstance(names, str) else list(names)
            rows = kpts[[self.keypoint_names.index(n) for n in names]]
            out[part] = (float(np.nanmean(rows[:, 0])) if np.isfinite(rows[:, 0]).any() else math.nan,
                         float(np.nanmean(rows[:, 1])) if np.isfinite(rows[:, 1]).any() else math.nan,
                         float(np.mean(rows[:, 2])))
        return out
