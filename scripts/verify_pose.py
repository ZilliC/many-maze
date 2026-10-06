"""Compare mANY-MAZE's torch-free ONNX conversion of SuperAnimal-TopViewMouse RTMPose-S
with DeepLabCut's own PyTorch implementation.

Needs a SCRATCH environment with torch (never add torch / deeplabcut to mANY-MAZE):

    uv venv /path/dlcref/venv --python 3.12
    uv pip install --python /path/dlcref/venv/bin/python torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python /path/dlcref/venv/bin/python numpy opencv-python-headless \
        onnx onnxruntime pyyaml scipy
    # DeepLabCut source only (its dependencies are not needed):
    pip download deeplabcut==3.0.2 --no-deps -d wheels && python -c \
        "import zipfile,glob; zipfile.ZipFile(glob.glob('wheels/deeplabcut-*.whl')[0]).extractall('wheels/src')"

    /path/dlcref/venv/bin/python scripts/verify_pose.py --dlc-src wheels/src/deeplabcut \
        --checkpoint superanimal_topviewmouse_rtmpose_s.pt --frames /tmp/real/m4s1 \
        --labels /tmp/real/m4s1/CollectedData_Pranav.csv --video /tmp/real/m3v1mp4.mp4

DeepLabCut's real model / crop / decoding source files (cspnext.py, csp.py, norm.py,
gated_attention_unit.py, rtmcc_head.py, sim_cc.py, image.py) are executed with their
registries and unrelated imports stubbed out, so the full deeplabcut install
(and its memory footprint) is avoided.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manymaze.core import pose  # noqa: E402

PKG = "deeplabcut.pose_estimation_pytorch"


def _module(name: str, **attrs) -> types.ModuleType:
    m = sys.modules.get(name) or types.ModuleType(name)
    m.__path__ = []
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def _exec(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Registry:
    def register_module(self, cls=None, **kw):
        return cls if cls is not None else (lambda c: c)


def load_dlc(src: Path) -> dict:
    """Execute the relevant DeepLabCut source files with stubbed packages."""
    src = Path(src)
    mp = src / "pose_estimation_pytorch"

    class BaseBackbone(nn.Module):
        def __init__(self, stride, freeze_bn_weights=True, freeze_bn_stats=True, **kw):
            super().__init__()
            self.stride = stride

    class HuggingFaceWeightsMixin:
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)

    class BaseHead(nn.Module):
        def __init__(self, stride, predictor, target_generator, criterion, aggregator, weight_init=None):
            super().__init__()
            self.stride = stride
            self.predictor = predictor

    for name in ["deeplabcut", PKG, f"{PKG}.models", f"{PKG}.models.modules", f"{PKG}.models.backbones",
                 f"{PKG}.models.heads", f"{PKG}.models.predictors", f"{PKG}.data"]:
        _module(name)
    _module("timm")
    _module("timm.layers", DropPath=nn.Identity)
    sys.modules["timm"].layers = sys.modules["timm.layers"]
    _module(f"{PKG}.models.backbones.base", BACKBONES=_Registry(), BaseBackbone=BaseBackbone,
            HuggingFaceWeightsMixin=HuggingFaceWeightsMixin)
    _module(f"{PKG}.models.heads.base", HEADS=_Registry(), BaseHead=BaseHead,
            WeightConversionMixin=type("WeightConversionMixin", (), {}))
    _module(f"{PKG}.models.criterions", BaseCriterion=object, BaseLossAggregator=object)
    _module(f"{PKG}.models.target_generators", BaseGenerator=object)
    _module(f"{PKG}.models.weight_init", BaseWeightInitializer=object)
    _module(f"{PKG}.models.predictors.base", PREDICTORS=_Registry(), BasePredictor=nn.Module)
    _module(f"{PKG}.data.utils", _compute_crop_bounds=None)

    norm = _exec(f"{PKG}.models.modules.norm", mp / "models/modules/norm.py")
    _exec(f"{PKG}.models.modules.csp", mp / "models/modules/csp.py")
    gau = _exec(f"{PKG}.models.modules.gated_attention_unit", mp / "models/modules/gated_attention_unit.py")
    sys.modules[f"{PKG}.models.modules"].__dict__.update(
        GatedAttentionUnit=gau.GatedAttentionUnit, ScaleNorm=norm.ScaleNorm)
    simcc = _exec(f"{PKG}.models.predictors.sim_cc", mp / "models/predictors/sim_cc.py")
    sys.modules[f"{PKG}.models.predictors"].BasePredictor = nn.Module
    cspnext = _exec(f"{PKG}.models.backbones.cspnext", mp / "models/backbones/cspnext.py")
    head = _exec(f"{PKG}.models.heads.rtmcc_head", mp / "models/heads/rtmcc_head.py")
    image = _exec(f"{PKG}.data.image", mp / "data/image.py")
    model_cfg = yaml.safe_load((src / "modelzoo/model_configs/rtmpose_s.yaml").read_text())
    project_cfg = yaml.safe_load((src / "modelzoo/project_configs/superanimal_topviewmouse.yaml").read_text())
    return dict(cspnext=cspnext, head=head, simcc=simcc, image=image, model_cfg=model_cfg,
                bodyparts=project_cfg["bodyparts"])


class RefModel(nn.Module):
    def __init__(self, dlc: dict, num_keypoints: int):
        super().__init__()
        cfg = dlc["model_cfg"]["model"]
        bb = {k: v for k, v in cfg["backbone"].items() if k not in ("type", "freeze_bn_stats", "freeze_bn_weights")}
        self.backbone = dlc["cspnext"].CSPNeXt(**bb)
        h = dict(cfg["heads"]["bodypart"])
        p = h["predictor"]
        self.predictor = dlc["simcc"].SimCCPredictor(simcc_split_ratio=p["simcc_split_ratio"], sigma=p["sigma"],
                                                     decode_beta=p["decode_beta"])
        self.head = dlc["head"].RTMCCHead(
            input_size=h["input_size"], in_channels=h["in_channels"], out_channels=num_keypoints,
            in_featuremap_size=h["in_featuremap_size"], simcc_split_ratio=h["simcc_split_ratio"],
            final_layer_kernel_size=h["final_layer_kernel_size"], gau_cfg=h["gau_cfg"],
            predictor=self.predictor, target_generator=None, criterion=None, aggregator=None)

    def forward(self, x):
        return self.head(self.backbone(x))


def load_reference(dlc: dict, checkpoint: Path) -> RefModel:
    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=True)
    sd = ckpt["model"]
    k = sd["heads.bodypart.final_layer.weight"].shape[0]
    model = RefModel(dlc, k)
    remapped = {("head." + n[len("heads.bodypart."):] if n.startswith("heads.bodypart.") else n): v
                for n, v in sd.items()}
    missing, unexpected = model.load_state_dict(remapped, strict=False)
    missing = [m for m in missing if not m.startswith("predictor")]
    assert not missing and not unexpected, (missing, unexpected)
    del ckpt, sd, remapped
    return model.eval()


def reference_predict(dlc, model, frame_bgr, box):
    """DeepLabCut pipeline for one box: RGB -> top_down_crop -> Normalize -> model -> SimCCPredictor."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    x0, y0, x1, y1 = box
    crop, offset, scale = dlc["image"].top_down_crop(rgb, np.array([x0, y0, x1 - x0, y1 - y0], float), (256, 256),
                                                     margin=0, crop_with_context=True)
    inp = (crop.astype(np.float32) - np.array([0.485, 0.456, 0.406], np.float32) * 255) / (
        np.array([0.229, 0.224, 0.225], np.float32) * 255)
    t = torch.from_numpy(inp.transpose(2, 0, 1)[None].copy())
    with torch.no_grad():
        out = model(t)
        poses = model.predictor(1.0, out)["poses"][0, 0].numpy().astype(np.float64)
    poses[:, :2] = poses[:, :2] * np.array(scale) + np.array(offset)
    return inp.transpose(2, 0, 1), out["x"][0].numpy(), out["y"][0].numpy(), poses


def blob_box(frame_bgr, bg_gray, pad: int = 0):
    """Bounding box of the largest dark blob vs the background (inner arena only)."""
    g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    d = cv2.subtract(bg_gray, g)
    m = (cv2.GaussianBlur(d, (5, 5), 0) > 25).astype(np.uint8)
    m[:8], m[-12:], m[:, :8], m[:, -12:] = 0, 0, 0, 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[i, :4]
    return (x - pad, y - pad, x + w + pad, y + h + pad)


def read_labels(path: Path) -> dict:
    rows = list(csv.reader(open(path)))
    parts, coords = rows[1][1:], rows[2][1:]
    out = {}
    for r in rows[3:]:
        vals = {}
        for p, c, v in zip(parts, coords, r[1:]):
            vals.setdefault(p, [np.nan, np.nan])[0 if c == "x" else 1] = float(v) if v else np.nan
        out[Path(r[0]).name] = {k: np.array(v) for k, v in vals.items()}
    return out


def video_frames(path: Path, n: int):
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    for i in np.linspace(0, total - 1, n).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            yield f"frame{i:05d}", f
    cap.release()


def value_ranges(onnx_path: Path, batch: np.ndarray) -> list[tuple[float, str, str]]:
    """Max |activation| of every node output (float16 range check for CoreML / ANE)."""
    import onnx
    import onnxruntime as ort
    m = onnx.load(str(onnx_path))
    have = {o.name for o in m.graph.output}
    for node in m.graph.node:
        for o in node.output:
            if o not in have:
                m.graph.output.append(onnx.helper.make_empty_tensor_value_info(o))
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    s = ort.InferenceSession(m.SerializeToString(), so, providers=["CPUExecutionProvider"])
    names = [o.name for o in s.get_outputs()]
    ops = {o: n.op_type for n in m.graph.node for o in n.output}
    res = {}
    for i in range(len(batch)):
        for name, v in zip(names, s.run(None, {"image": batch[i:i + 1]})):
            res[name] = max(res.get(name, 0.0), float(np.abs(v).max()))
    return sorted(((v, k, ops.get(k, "?")) for k, v in res.items()), reverse=True)


def fp16_emulation(onnx_path: Path, est, batch: np.ndarray):
    """Round weights and every activation to float16 (rough model of Core ML on the ANE / GPU).

    Returns max |logit diff| and max keypoint shift (input pixels) vs float32.
    """
    import onnx
    import onnxruntime as ort
    from onnx import TensorProto, helper, numpy_helper
    m = onnx.load(str(onnx_path))
    for init in m.graph.initializer:
        a = numpy_helper.to_array(init)
        if a.dtype == np.float32:
            init.CopyFrom(numpy_helper.from_array(a.astype(np.float16).astype(np.float32), init.name))
    nodes = []
    outs = {o.name for o in m.graph.output}
    for node in m.graph.node:
        renamed = []
        for o in node.output:
            if o in outs:
                renamed.append(o)
                continue
            raw = o + "_f32"
            nodes.append(helper.make_node("Cast", [raw], [o + "_h"], to=TensorProto.FLOAT16))
            nodes.append(helper.make_node("Cast", [o + "_h"], [o], to=TensorProto.FLOAT))
            renamed.append(raw)
        node.output[:] = renamed
        nodes.insert(len(nodes) - 2 * sum(1 for o in renamed if o.endswith("_f32")), node)
    del m.graph.node[:]
    m.graph.node.extend(nodes)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    s16 = ort.InferenceSession(m.SerializeToString(), so, providers=["CPUExecutionProvider"])
    dl, dk, dp = 0.0, 0.0, 0.0
    used = [est.keypoint_names.index(n) for n in est.meta["parts"].values()]
    for i in range(len(batch)):
        x32, y32 = est.infer(batch[i:i + 1])
        x16, y16 = s16.run(None, {"image": batch[i:i + 1]})
        dl = max(dl, float(np.abs(x16 - x32).max()), float(np.abs(y16 - y32).max()))
        k32, k16 = est.decode([x32, y32]), est.decode([x16, y16])
        ok = np.isfinite(k32[..., 0]) & np.isfinite(k16[..., 0])
        dk = max(dk, float(np.abs(k16[ok][:, :2] - k32[ok][:, :2]).max()))
        dp = max(dp, float(np.nanmax(np.abs(k16[0, used, :2] - k32[0, used, :2]))))
    return dl, dk, dp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dlc-src", required=True, help="path of the deeplabcut package source")
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--frames", help="directory of labelled frames (png)")
    ap.add_argument("--labels", help="DeepLabCut CollectedData_*.csv for --frames")
    ap.add_argument("--video")
    ap.add_argument("--video-frames", type=int, default=30)
    ap.add_argument("--margin", type=int, default=0, help="pad around blob boxes (px)")
    args = ap.parse_args(argv)
    torch.set_num_threads(2)

    dlc = load_dlc(Path(args.dlc_src))
    assert dlc["bodyparts"] == pose.MODELS["topviewmouse_rtmpose_s"]["keypoints"], "keypoint order differs"
    print("keypoint names/order match DeepLabCut's superanimal_topviewmouse project config")
    ref = load_reference(dlc, Path(args.checkpoint))

    tmp = Path(tempfile.mkdtemp(prefix="manymaze-verify-"))
    os.environ["MANYMAZE_MODELS"] = str(tmp)
    try:
        run(args, dlc, ref, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(args, dlc, ref, tmp: Path):
    t = time.perf_counter()
    onnx_path = pose.install_model("topviewmouse_rtmpose_s", source_path=args.checkpoint)
    print(f"torch-free conversion: {time.perf_counter() - t:.2f} s -> {onnx_path} ({onnx_path.stat().st_size / 1e6:.1f} MB)")
    est = pose.PoseEstimator(onnx_path, device="cpu")
    print("ORT provider:", est.provider)

    samples = []  # (name, frame, box, labels or None)
    if args.frames:
        labels = read_labels(Path(args.labels)) if args.labels else {}
        files = sorted(Path(args.frames).glob("*.png"))
        bg = np.median(np.stack([cv2.imread(str(f), cv2.IMREAD_GRAYSCALE) for f in files]), axis=0).astype(np.uint8)
        for f in files:
            fr = cv2.imread(str(f))
            box = blob_box(fr, bg, args.margin)
            if box is not None:
                samples.append((f.name, fr, box, labels.get(f.name)))
    if args.video:
        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in video_frames(Path(args.video), 15)]
        vbg = np.median(np.stack(grays), axis=0).astype(np.uint8)
        del grays
        for name, fr in video_frames(Path(args.video), args.video_frames):
            box = blob_box(fr, vbg, args.margin)
            if box is not None:
                samples.append((name, fr, box, None))
    print(f"{len(samples)} crops")

    d_in, d_lx, d_ly, d_xy, d_c, rel = [], [], [], [], [], []
    batch = []
    err = {"nose": [], "tail_base": []}
    for name, fr, box, lab in samples:
        inp, rx, ry, rpose = reference_predict(dlc, ref, fr, box)
        b, _ = est.preprocess(fr, [box])
        ox, oy = est.infer(b)
        k = est.predict(fr, [box])[0]
        batch.append(b[0])
        d_in.append(np.abs(b[0] - inp).max())
        d_lx.append(np.abs(ox[0] - rx).max())
        d_ly.append(np.abs(oy[0] - ry).max())
        rel.append(max(np.abs(ox[0] - rx).max(), np.abs(oy[0] - ry).max()) / max(np.abs(rx).max(), np.abs(ry).max()))
        valid = np.isfinite(k[:, 0])
        d_xy.append(np.abs(k[valid, :2] - rpose[valid, :2]).max() if valid.any() else 0.0)
        d_c.append(np.abs(k[:, 2] - rpose[:, 2]).max())
        if lab is not None:
            parts = est.body_parts(k)
            for ours, theirs in (("nose", "snout"), ("tail_base", "tailbase")):
                if theirs in lab and np.isfinite(lab[theirs]).all():
                    err[ours].append(float(np.hypot(parts[ours][0] - lab[theirs][0], parts[ours][1] - lab[theirs][1])))
    print(f"input tensor        max |diff| = {max(d_in):.2e}")
    print(f"SimCC x logits      max |diff| = {max(d_lx):.2e}")
    print(f"SimCC y logits      max |diff| = {max(d_ly):.2e}   (max relative to logit range {max(rel):.2e})")
    print(f"keypoints (px)      max |diff| = {max(d_xy):.3f}")
    print(f"confidence          max |diff| = {max(d_c):.2e}")
    for part, e in err.items():
        if e:
            e = np.array(e)
            print(f"{part:9s} vs labels: n={len(e)} median {np.median(e):.1f} px, mean {e.mean():.1f} px, "
                  f"<5 px {np.mean(e < 5):.0%}, <10 px {np.mean(e < 10):.0%}, max {e.max():.1f}")

    batch = np.stack(batch)
    for n in (1, 4):
        if len(batch) < n:
            continue
        x = batch[:n]
        est.infer(x)
        reps = max(3, 20 // n)
        t = time.perf_counter()
        for _ in range(reps):
            est.infer(x)
        ms = (time.perf_counter() - t) / reps / n * 1000
        with torch.no_grad():
            tx = torch.from_numpy(x)
            ref(tx)
            t = time.perf_counter()
            for _ in range(reps):
                ref(tx)
            tms = (time.perf_counter() - t) / reps / n * 1000
        print(f"CPU speed batch {n}: ONNX Runtime {ms:.1f} ms/crop, PyTorch {tms:.1f} ms/crop")

    top = value_ranges(onnx_path, batch[: min(8, len(batch))])
    print("largest activations (float16 max is 65504):")
    for v, name, op in top[:6]:
        print(f"  {v:10.1f}  {op:8s} {name}")
    dl, dk, dp = fp16_emulation(onnx_path, est, batch[: min(16, len(batch))])
    print(f"float16 emulation: max |logit diff| {dl:.3f}, max keypoint shift {dk:.1f} px "
          f"(nose/centre/tail base {dp:.1f} px), in 256 px crop units")


if __name__ == "__main__":
    main()
