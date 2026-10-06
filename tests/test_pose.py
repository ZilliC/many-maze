import json
import os
import struct
import zipfile

import numpy as np
import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from manymaze.core import pose  # noqa: E402

COREML_MLPROGRAM_OPS = {
    "Add", "Clip", "Concat", "Conv", "Div", "GlobalAveragePool", "MatMul", "MaxPool", "Mul", "ReduceMean",
    "Relu", "Reshape", "Sigmoid", "Sqrt", "Transpose",
}
TINY_KPTS = ["nose", "neck", "mouse_center", "tail_base", "tail_end"]


# --------------------------------------------------------------------------- torch-format writer (no torch)

def _write_torch_zip(path, obj, root="archive"):
    """Write nested dicts of arrays / scalars in PyTorch's zip checkpoint format."""
    out = bytearray(b"\x80\x02")
    blobs = {}
    storages = {np.dtype(np.float32): "FloatStorage", np.dtype(np.int64): "LongStorage"}

    def s(text):
        b = text.encode()
        out.extend(b"X" + struct.pack("<I", len(b)) + b)

    def i(v):
        out.extend(b"J" + struct.pack("<i", v))

    def tup(vals):
        out.extend(b"(")
        for v in vals:
            i(v)
        out.extend(b"t")

    def tensor(a):
        if isinstance(a, _Strided):
            base, shape, stride, offset = a.base_arr, a.shape_, a.stride_, a.offset
        else:
            base, shape, stride, offset = np.ascontiguousarray(a), a.shape, None, 0
            stride = tuple(int(st // a.itemsize) for st in base.strides) if a.ndim else ()
        key = str(len(blobs))
        blobs[key] = base.tobytes()
        out.extend(b"ctorch._utils\n_rebuild_tensor_v2\n(")
        out.extend(b"(")
        s("storage")
        out.extend(f"ctorch\n{storages[base.dtype]}\n".encode())
        s(key)
        s("cpu")
        i(base.size)
        out.extend(b"tQ")
        i(offset)
        tup(shape)
        tup(stride)
        out.extend(b"\x89ccollections\nOrderedDict\n)RtR")

    def value(v):
        if isinstance(v, dict):
            out.extend(b"}(")
            for k, x in v.items():
                s(k)
                value(x)
            out.extend(b"u")
        elif isinstance(v, (np.ndarray, _Strided)):
            tensor(v)
        elif isinstance(v, float):
            out.extend(b"G" + struct.pack(">d", v))
        elif isinstance(v, int):
            i(v)
        else:
            s(str(v))

    value(obj)
    out.extend(b".")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr(f"{root}/data.pkl", bytes(out))
        z.writestr(f"{root}/byteorder", "little")
        for k, b in blobs.items():
            z.writestr(f"{root}/data/{k}", b)
        z.writestr(f"{root}/version", "3\n")


class _Strided:
    """A tensor view declared by (storage, shape, stride, offset) for the writer."""

    def __init__(self, base, shape, stride, offset=0):
        self.base_arr, self.shape_, self.stride_, self.offset = base, shape, stride, offset



# --------------------------------------------------------------------------- tiny RTMPose state dict

def _tiny_state_dict(seed=0, widen=0.125, k=len(TINY_KPTS), hidden=32, e=64, s=16, size=64):
    rng = np.random.default_rng(seed)
    sd = {}

    def cbn(p, cin, cout, ks, depthwise=False):
        sd[p + ".conv.weight"] = (rng.standard_normal((cout, 1 if depthwise else cin, ks, ks))
                                  * (1.0 / np.sqrt(ks * ks * (1 if depthwise else cin)))).astype(np.float32)
        sd[p + ".norm.weight"] = (1 + 0.1 * rng.standard_normal(cout)).astype(np.float32)
        sd[p + ".norm.bias"] = (0.1 * rng.standard_normal(cout)).astype(np.float32)
        sd[p + ".norm.running_mean"] = (0.1 * rng.standard_normal(cout)).astype(np.float32)
        sd[p + ".norm.running_var"] = rng.uniform(0.5, 1.5, cout).astype(np.float32)
        sd[p + ".norm.num_batches_tracked"] = np.array(7, np.int64)

    def csp(p, c, blocks, attention=True):
        mid = c // 2
        cbn(p + ".main_conv", c, mid, 1)
        cbn(p + ".short_conv", c, mid, 1)
        cbn(p + ".final_conv", 2 * mid, c, 1)
        for j in range(blocks):
            cbn(f"{p}.blocks.{j}.conv1", mid, mid, 3)
            cbn(f"{p}.blocks.{j}.conv2.depthwise_conv", mid, mid, 5, depthwise=True)
            cbn(f"{p}.blocks.{j}.conv2.pointwise_conv", mid, mid, 1)
        if attention:
            sd[p + ".attention.fc.weight"] = (rng.standard_normal((2 * mid, 2 * mid, 1, 1)) * 0.3).astype(np.float32)
            sd[p + ".attention.fc.bias"] = (rng.standard_normal(2 * mid) * 0.3).astype(np.float32)

    c = lambda v: int(v * widen)  # noqa: E731
    cbn("backbone.stem.0", 3, int(64 * widen // 2), 3)
    cbn("backbone.stem.1", int(64 * widen // 2), int(64 * widen // 2), 3)
    cbn("backbone.stem.2", int(64 * widen // 2), c(64), 3)
    for i, (cin, cout, nb, spp) in enumerate([(64, 128, 1, False), (128, 256, 2, False),
                                              (256, 512, 2, False), (512, 1024, 1, True)], 1):
        p = f"backbone.stage{i}"
        cbn(p + ".0", c(cin), c(cout), 3)
        if spp:
            cbn(p + ".1.conv1", c(cout), c(cout) // 2, 1)
            cbn(p + ".1.conv2", c(cout) // 2 * 4, c(cout), 1)
        csp(f"{p}.{2 if spp else 1}", c(cout), nb)
    h = "heads.bodypart"
    cin = c(1024)
    flat = (size // 32) ** 2
    sd[h + ".final_layer.weight"] = (rng.standard_normal((k, cin, 7, 7)) * 0.02).astype(np.float32)
    sd[h + ".final_layer.bias"] = (rng.standard_normal(k) * 0.1).astype(np.float32)
    sd[h + ".mlp.0.g"] = np.array([1.3], np.float32)
    sd[h + ".mlp.1.weight"] = (rng.standard_normal((hidden, flat)) * 0.5).astype(np.float32)
    g = h + ".gau"
    sd[g + ".gamma"] = rng.uniform(0, 1, (2, s)).astype(np.float32)
    sd[g + ".beta"] = rng.uniform(0, 1, (2, s)).astype(np.float32)
    sd[g + ".o.weight"] = (rng.standard_normal((hidden, e)) * 0.1).astype(np.float32)
    sd[g + ".uv.weight"] = (rng.standard_normal((2 * e + s, hidden)) * 0.2).astype(np.float32)
    sd[g + ".ln.g"] = np.array([0.8], np.float32)
    sd[g + ".res_scale.scale"] = rng.uniform(0.5, 1.5, hidden).astype(np.float32)
    sd[h + ".cls_x.weight"] = (rng.standard_normal((2 * size, hidden)) * 0.3).astype(np.float32)
    sd[h + ".cls_y.weight"] = (rng.standard_normal((2 * size, hidden)) * 0.3).astype(np.float32)
    return sd


def _tiny_meta(size=64):
    return pose.validate_meta({
        "title": "tiny", "input_size": [size, size], "keypoints": TINY_KPTS, "output": "simcc",
        "simcc_split_ratio": 2.0, "sigma": [5.66, 5.66], "decode_beta": 150.0,
        "parts": {"nose": "nose", "centre": ["neck", "mouse_center"], "tail_base": "tail_base"},
    })


# --------------------------------------------------------------------------- NumPy reference (DLC semantics)

def _conv(x, w, b, stride=1, pad=0, depthwise=False):
    xp = np.pad(x, ((0, 0), (pad, pad), (pad, pad)))
    k = w.shape[-1]
    win = np.lib.stride_tricks.sliding_window_view(xp, (k, k), axis=(1, 2))[:, ::stride, ::stride]
    y = np.einsum("chwij,cij->chw", win, w[:, 0]) if depthwise else np.einsum("chwij,ocij->ohw", win, w)
    return y + (0 if b is None else b[:, None, None])


def _silu(x):
    return x / (1 + np.exp(-x))


def _ref_forward(sd, x):
    x = x[0].astype(np.float64)

    def cbs(x, p, stride=1, dw=False):
        w = sd[p + ".conv.weight"].astype(np.float64)
        y = _conv(x, w, None, stride, w.shape[-1] // 2, dw)
        n = lambda k: sd[f"{p}.norm.{k}"][:, None, None]  # noqa: E731
        return _silu((y - n("running_mean")) / np.sqrt(n("running_var") + 1e-5) * n("weight") + n("bias"))

    def csp(x, p, identity):
        short, main = cbs(x, p + ".short_conv"), cbs(x, p + ".main_conv")
        j = 0
        while f"{p}.blocks.{j}.conv1.conv.weight" in sd:
            b = f"{p}.blocks.{j}"
            y = cbs(cbs(cbs(main, b + ".conv1"), b + ".conv2.depthwise_conv", dw=True), b + ".conv2.pointwise_conv")
            main = y + main if identity else y
            j += 1
        y = np.concatenate([main, short])
        a = y.mean(axis=(1, 2), keepdims=True)
        a = _conv(a, sd[p + ".attention.fc.weight"], sd[p + ".attention.fc.bias"])
        y = y * np.clip(a / 6 + 0.5, 0, 1)
        return cbs(y, p + ".final_conv")

    def maxpool(x, k):
        xp = np.pad(x, ((0, 0), (k // 2, k // 2), (k // 2, k // 2)), constant_values=-np.inf)
        return np.lib.stride_tricks.sliding_window_view(xp, (k, k), axis=(1, 2)).max(axis=(-1, -2))

    def scalenorm(x, g):
        n = np.linalg.norm(x, axis=-1, keepdims=True) * x.shape[-1] ** -0.5
        return x / np.maximum(n, 1e-5) * g

    x = cbs(x, "backbone.stem.0", 2)
    x = cbs(cbs(x, "backbone.stem.1"), "backbone.stem.2")
    for i in range(1, 5):
        p = f"backbone.stage{i}"
        x = cbs(x, p + ".0", 2)
        if i == 4:
            y = cbs(x, p + ".1.conv1")
            x = cbs(np.concatenate([y] + [maxpool(y, k) for k in (5, 9, 13)]), p + ".1.conv2")
        x = csp(x, f"{p}.{2 if i == 4 else 1}", identity=i < 4)
    h = "heads.bodypart"
    f = _conv(x, sd[h + ".final_layer.weight"], sd[h + ".final_layer.bias"], 1, 3).reshape(len(sd[h + ".final_layer.bias"]), -1)
    f = scalenorm(f, sd[h + ".mlp.0.g"]) @ sd[h + ".mlp.1.weight"].T
    g = h + ".gau"
    e, s = sd[g + ".o.weight"].shape[1], sd[g + ".gamma"].shape[1]
    uv = _silu(scalenorm(f, sd[g + ".ln.g"]) @ sd[g + ".uv.weight"].T)
    u, v, base = uv[:, :e], uv[:, e:2 * e], uv[:, 2 * e:]
    q = base * sd[g + ".gamma"][0] + sd[g + ".beta"][0]
    k = base * sd[g + ".gamma"][1] + sd[g + ".beta"][1]
    kern = np.square(np.maximum(q @ k.T / np.sqrt(s), 0))
    y = (u * (kern @ v)) @ sd[g + ".o.weight"].T + f * sd[g + ".res_scale.scale"]
    return y @ sd[h + ".cls_x.weight"].T, y @ sd[h + ".cls_y.weight"].T


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def tiny_ckpt(tmp_path_factory):
    d = tmp_path_factory.mktemp("ckpt")
    sd = _tiny_state_dict()
    p = d / "tiny.pt"
    _write_torch_zip(p, {"metadata": {"epoch": 3, "metrics": {"rmse": 1.5}}, "model": sd})
    return p, sd


@pytest.fixture
def tiny_model(tmp_path, monkeypatch, tiny_ckpt):
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path / "models"))
    spec = dict(pose.MODELS["topviewmouse_rtmpose_s"])
    spec.update(_tiny_meta(), url=tiny_ckpt[0].as_uri(), checkpoint="tiny.pt", sha256=None,
                onnx="tiny.onnx", meta="tiny.json", size_bytes=0)
    monkeypatch.setitem(pose.MODELS, "tiny", spec)
    return "tiny"


# --------------------------------------------------------------------------- tests

def test_checkpoint_reader(tmp_path):
    base = np.arange(12, dtype=np.float32)
    obj = {"model": {"a": np.arange(6, dtype=np.float32).reshape(2, 3), "n": np.array(5, np.int64),
                     "t": _Strided(base, (3, 2), (1, 3), 0), "off": _Strided(base, (2, 2), (1, 2), 4)},
           "metadata": {"epoch": 400, "loss": 0.25}}
    p = tmp_path / "x.pt"
    _write_torch_zip(p, obj, root="snapshot-400")
    ck = pose.load_torch_checkpoint(p)
    assert ck["metadata"] == {"epoch": 400, "loss": 0.25}
    sd = pose.find_state_dict(ck)
    np.testing.assert_array_equal(sd["a"], np.arange(6).reshape(2, 3))
    assert sd["n"] == 5 and sd["n"].dtype == np.int64
    np.testing.assert_array_equal(sd["t"], base[:6].reshape(2, 3).T)
    np.testing.assert_array_equal(sd["off"], [[4, 6], [5, 7]])


def test_checkpoint_reader_is_safe(tmp_path):
    p = tmp_path / "evil.pt"
    payload = b"\x80\x02cos\nsystem\n(X\x04\x00\x00\x00truetR."
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("archive/data.pkl", payload)
    obj = pose.load_torch_checkpoint(p)
    assert isinstance(obj, pose._Opaque)
    with pytest.raises(ValueError):
        pose.find_state_dict(obj)


def test_layout(tiny_ckpt):
    lay = pose.rtmpose_layout(tiny_ckpt[1])
    assert [s["blocks"] for s in lay["stages"]] == [1, 2, 2, 1]
    assert [s["spp"] for s in lay["stages"]] == [False, False, False, True]


def test_conversion_matches_numpy_reference(tiny_ckpt):
    import onnxruntime as ort
    sd = pose.find_state_dict(pose.load_torch_checkpoint(tiny_ckpt[0]))
    model = pose.build_rtmpose_onnx(sd, _tiny_meta())
    assert {n.op_type for n in model.graph.node} <= COREML_MLPROGRAM_OPS
    sess = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    x = np.random.default_rng(1).standard_normal((2, 3, 64, 64)).astype(np.float32)
    sx, sy = sess.run(None, {"image": x})
    assert sx.shape == (2, len(TINY_KPTS), 128) and sy.shape == (2, len(TINY_KPTS), 128)
    for i in range(2):
        rx, ry = _ref_forward(tiny_ckpt[1], x[i:i + 1])
        scale = max(np.abs(rx).max(), np.abs(ry).max())
        assert np.abs(sx[i] - rx).max() < 1e-4 * scale
        assert np.abs(sy[i] - ry).max() < 1e-4 * scale


def test_install_from_file_and_predict(tiny_model, tiny_ckpt):
    assert not pose.is_installed(tiny_model)
    seen = []
    path = pose.install_model(tiny_model, progress=seen.append, source_path=str(tiny_ckpt[0]))
    assert tiny_ckpt[0].exists()  # user-supplied checkpoint is kept
    assert pose.is_installed(tiny_model) and path.suffix == ".onnx"
    assert seen[-1] == 1.0 and seen == sorted(seen)
    meta = json.loads(path.with_suffix(".json").read_text())
    assert meta["keypoints"] == TINY_KPTS and meta["source_sha256"]
    assert not list(path.parent.glob("*.tmp"))

    est = pose.PoseEstimator(tiny_model, device="cpu")
    assert est.provider == "CPUExecutionProvider" and est.keypoint_names == TINY_KPTS
    frame = np.random.default_rng(0).integers(0, 255, (120, 160, 3), dtype=np.uint8)
    res = est.predict(frame, [(10, 20, 50, 40), (100, 60, 150, 118), (-10, -5, 30, 30)])
    assert len(res) == 3 and all(r.shape == (len(TINY_KPTS), 3) for r in res)
    assert all(np.all((r[:, 2] >= 0) & (r[:, 2] <= 1)) for r in res)
    assert est.predict(frame, []) == []
    bp = est.body_parts(res[0])
    assert set(bp) == {"nose", "centre", "tail_base"}
    k = res[0]
    i, j = TINY_KPTS.index("neck"), TINY_KPTS.index("mouse_center")
    if np.isfinite(k[[i, j], 0]).all():
        assert bp["centre"][0] == pytest.approx((k[i, 0] + k[j, 0]) / 2)

    fixed = pose.PoseEstimator(path, device="cpu", batch_size=2)
    assert fixed.batch == 2
    res2 = fixed.predict(frame, [(10, 20, 50, 40), (100, 60, 150, 118), (-10, -5, 30, 30)])
    for a, b in zip(res, res2):
        np.testing.assert_allclose(a, b, atol=1e-4)


def test_install_download_and_cancel(tiny_model):
    calls = []
    with pytest.raises(InterruptedError):
        pose.install_model(tiny_model, should_stop=lambda: True)
    d = pose.models_dir()
    assert not pose.is_installed(tiny_model) and not list(d.glob("*.pt*"))
    pose.install_model(tiny_model, progress=calls.append)
    assert pose.is_installed(tiny_model) and not list(d.glob("*.pt*"))
    assert calls[-1] == 1.0
    pose.uninstall_model(tiny_model)
    assert not pose.is_installed(tiny_model)


def test_sha_mismatch(tiny_model, tiny_ckpt, monkeypatch):
    monkeypatch.setitem(pose.MODELS[tiny_model], "sha256", "0" * 64)
    with pytest.raises(ValueError):
        pose.install_model(tiny_model, source_path=str(tiny_ckpt[0]))
    assert not pose.is_installed(tiny_model)


@pytest.mark.parametrize("box", [(40, 30, 120, 70), (5, 50, 45, 150), (150, -20, 210, 40), (-30, 100, 30, 190)])
def test_crop_roundtrip(box):
    frame = np.zeros((200, 200, 3), np.uint8)
    rng = np.random.default_rng(3)
    x0, y0, x1, y1 = box
    for _ in range(3):
        px = rng.uniform(max(x0, 0) + 5, min(x1, 199) - 5)
        py = rng.uniform(max(y0, 0) + 5, min(y1, 199) - 5)
        f = frame.copy()
        f[int(py), int(px)] = 255
        crop, (ox, oy), (sx, sy) = pose.crop_box(f, box, (256, 256))
        assert crop.shape == (256, 256, 3)
        assert sx == pytest.approx(sy, rel=0.02)
        g = crop[..., 0].astype(float)
        cy, cx = np.unravel_index(g.argmax(), g.shape)
        w = g[max(cy - 6, 0):cy + 7, max(cx - 6, 0):cx + 7]
        yy, xx = np.mgrid[max(cy - 6, 0):cy + 7, max(cx - 6, 0):cx + 7][:, :w.shape[0], :w.shape[1]]
        mx, my = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
        assert mx * sx + ox == pytest.approx(int(px), abs=1.0)
        assert my * sy + oy == pytest.approx(int(py), abs=1.0)


def test_predict_maps_crop_to_frame(tiny_model, tiny_ckpt):
    pose.install_model(tiny_model, source_path=str(tiny_ckpt[0]))
    est = pose.PoseEstimator(tiny_model, device="cpu")
    k = len(TINY_KPTS)

    def fake_infer(batch):
        x = np.full((len(batch), k, 128), -1.0, np.float32)
        y = np.full((len(batch), k, 128), -1.0, np.float32)
        x[:, :, 20], y[:, :, 100] = 1.0, 1.0  # crop pixel (10, 50)
        return [x, y]

    est.infer = fake_infer
    frame = np.zeros((300, 400, 3), np.uint8)
    box = (100, 150, 228, 182)  # 128 x 32 -> 128 x 128 square crop, scale 2
    [kp] = est.predict(frame, [box])
    _, (ox, oy), (sx, sy) = pose.crop_box(frame, box, (64, 64))
    assert (sx, sy) == (2.0, 2.0) and (ox, oy) == (100, 102)
    np.testing.assert_allclose(kp[:, 0], 10 * 2 + 100)
    np.testing.assert_allclose(kp[:, 1], 50 * 2 + 102)
    assert np.all(kp[:, 2] > 0.99)


def test_decode_simcc():
    xs = np.arange(512)
    lx = np.exp(-((xs - 300) ** 2) / 50.0)[None, None]
    ly = np.exp(-((xs - 41) ** 2) / 50.0)[None, None]
    out = pose.decode_simcc(np.concatenate([lx, np.zeros_like(lx)], 1), np.concatenate([ly, np.zeros_like(ly)], 1))
    assert out.shape == (1, 2, 3)
    assert tuple(out[0, 0, :2]) == (150.0, 20.5)
    assert 0 < out[0, 0, 2] <= 1
    assert np.isnan(out[0, 1, :2]).all()  # flat logits -> no location


def test_decode_heatmap():
    hm = np.zeros((1, 2, 16, 16), np.float32)
    hm[0, 0, 5, 9] = 1.0
    hm[0, 1, 2, 3], hm[0, 1, 2, 4] = 1.0, 0.5
    out = pose.decode_heatmap(hm, (64, 64))
    np.testing.assert_allclose(out[0, 0], [(9 + 0.5) * 4, (5 + 0.5) * 4, 1.0])
    np.testing.assert_allclose(out[0, 1, :2], [(3.25 + 0.5) * 4, (2 + 0.5) * 4])


def test_custom_heatmap_model(tmp_path):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    k = 3
    w = np.zeros((k, 3, 4, 4), np.float32)
    w[:, 0] = 1.0 / 16
    graph = helper.make_graph(
        [helper.make_node("Conv", ["input", "w"], ["heatmaps"], kernel_shape=[4, 4], strides=[4, 4])], "custom",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 48, 64])],
        [helper.make_tensor_value_info("heatmaps", TensorProto.FLOAT, [1, k, 12, 16])],
        [numpy_helper.from_array(w, "w")])
    m = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    p = tmp_path / "lab_model.onnx"
    onnx.save(m, str(p))
    meta = {"title": "lab", "input_size": [64, 48], "color": "BGR", "mean": [0, 0, 0], "std": [1, 1, 1],
            "output": "heatmap", "keypoints": ["snout", "body", "tailbase"],
            "parts": {"nose": "snout", "centre": "body", "tail_base": "tailbase"}}
    p.with_suffix(".json").write_text(json.dumps(meta))
    est = pose.PoseEstimator(str(p), device="cpu")
    assert est.batch == 1 and est.keypoint_names == meta["keypoints"]
    frame = np.zeros((240, 320, 3), np.uint8)
    frame[100:104, 200:204, 0] = 255  # bright square, blue channel
    [kp] = est.predict(frame, [(160, 80, 240, 140)])
    assert kp.shape == (3, 3)
    assert kp[0, 0] == pytest.approx(202, abs=4) and kp[0, 1] == pytest.approx(102, abs=4)
    res = est.predict(frame, [(160, 80, 240, 140), (0, 0, 50, 50)])  # batch 1 model, two boxes
    assert len(res) == 2
    assert set(est.body_parts(kp)) == {"nose", "centre", "tail_base"}

    p.with_suffix(".json").write_text(json.dumps(dict(meta, parts={"nose": "head"})))
    with pytest.raises(ValueError):
        pose.PoseEstimator(str(p), device="cpu")
    p.with_suffix(".json").unlink()
    with pytest.raises(FileNotFoundError):
        pose.load_model_info(p)


def test_embedded_metadata(tmp_path, tiny_ckpt):
    import onnx
    sd = pose.find_state_dict(pose.load_torch_checkpoint(tiny_ckpt[0]))
    p = tmp_path / "embedded.onnx"
    model = pose.build_rtmpose_onnx(sd, _tiny_meta())
    onnx.save(model, str(p))
    assert pose.load_model_info(p)["keypoints"] == TINY_KPTS
    key = {m.key: m.value for m in model.metadata_props}["COREML_CACHE_KEY"]
    assert key.isalnum() and len(key) < 64


def test_models_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path / "m"))
    assert pose.models_dir() == tmp_path / "m"
    monkeypatch.delenv("MANYMAZE_MODELS")
    monkeypatch.setattr(pose.sys, "platform", "darwin")
    assert pose.models_dir().parts[-3:] == ("Application Support", "mANY-MAZE", "models")
    monkeypatch.setattr(pose.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert pose.models_dir() == tmp_path / "xdg" / "manymaze" / "models"
    monkeypatch.setattr(pose.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert pose.models_dir() == tmp_path / "roaming" / "mANY-MAZE" / "models"


def test_registry_entry():
    m = pose.MODELS["topviewmouse_rtmpose_s"]
    assert len(m["keypoints"]) == 27 and m["input_size"] == [256, 256]
    assert m["parts"] == {"nose": "nose", "centre": "mouse_center", "tail_base": "tail_base"}
    assert "non-commercial" in m["license"] and "Nature Communications" in m["citation"]
    pose.validate_meta(pose._meta_from_spec("topviewmouse_rtmpose_s"))
    assert "CPUExecutionProvider" in pose.available_providers()


@pytest.mark.skipif(os.environ.get("MANYMAZE_TEST_DOWNLOAD") != "1", reason="set MANYMAZE_TEST_DOWNLOAD=1")
def test_download_real_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path))
    key = "topviewmouse_rtmpose_s"
    pose.install_model(key)
    assert pose.is_installed(key) and not list(tmp_path.glob("*.pt*"))
    est = pose.PoseEstimator(key)
    frame = np.full((480, 640, 3), 220, np.uint8)
    import cv2
    cv2.ellipse(frame, (320, 240), (40, 18), 0, 0, 360, (30, 30, 30), -1)
    cv2.line(frame, (360, 240), (430, 250), (40, 40, 40), 4)
    [kp] = est.predict(frame, [(275, 215, 435, 265)])
    assert kp.shape == (27, 3) and np.isfinite(kp[:, 2]).all()
