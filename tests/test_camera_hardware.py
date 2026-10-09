"""Camera hardware settings (core.camhw) and native industrial camera sources (core.camsources), with fake
OpenCV captures and fake SDK modules (no camera hardware needed)."""

import sys
import threading
import time

import cv2
import numpy as np
import pytest

from fake_cameras import INSTALLERS, FakeCap, FakeDevice, is_red, no_sdks
from manymaze.core import camsources
from manymaze.core import video as video_mod
from manymaze.core.camera import (CameraView, SourceReader, SourceSpec, TransformedSource, apply_hardware,
                                  camera_settings, hardware_target, set_camera_settings)
from manymaze.core.camhw import (CONTROLS, OK, UNSUPPORTED, CameraHardware, GenICamControls, OpenCVControls,
                                 UnsupportedPixelFormat, describe_report, to_bgr)
from manymaze.core.project import Project


# ====================================================================== settings
def test_hardware_settings_round_trip_and_tolerance():
    hw = CameraHardware(exposure=-6, auto_exposure=False, gain=12.5, auto_white_balance=True, trigger=True,
                        trigger_source="Line1")
    d = hw.to_dict()
    assert d == {"exposure": -6, "auto_exposure": False, "gain": 12.5, "auto_white_balance": True,
                 "trigger": True, "trigger_source": "Line1"}
    assert CameraHardware.from_dict(d) == hw
    assert CameraHardware().is_empty and CameraHardware().to_dict() == {}
    # old / hand-edited projects: missing, unknown, malformed values are ignored
    for bad in (None, {}, [], "x", {"exposure": "fast", "gain": float("nan"), "bogus": 3, "auto_focus": "yes",
                                    "brightness": "120"}):
        CameraHardware.from_dict(bad)
    odd = CameraHardware.from_dict({"exposure": "fast", "gain": float("nan"), "bogus": 3, "auto_focus": "yes",
                                    "brightness": "120"})
    assert odd.to_dict() == {"auto_focus": True, "brightness": 120.0}
    assert hw.merged({"gain": None, "focus": 3}).to_dict() == {**{k: v for k, v in d.items() if k != "gain"},
                                                                "focus": 3.0}
    rep = {"exposure": OK, "gain": UNSUPPORTED, "brightness": "adjusted:128", "auto_white_balance": OK}
    txt = describe_report(rep)
    assert "Applied: Exposure, Auto white balance." in txt and "Not supported by this camera / driver: Gain." in txt
    assert "Brightness (128)" in txt and describe_report({}) == ""


def test_source_spec_hardware_persistence(tmp_path):
    spec = SourceSpec(2, hardware=CameraHardware(gain=10, auto_exposure=True))
    d = spec.to_dict()
    assert d["hardware"] == {"gain": 10, "auto_exposure": True}
    assert SourceSpec.from_dict(d).hardware == spec.hardware
    old = {k: v for k, v in d.items() if k != "hardware"}  # saved before hardware settings existed
    assert SourceSpec.from_dict(old).hardware.is_empty and "hardware" not in SourceSpec(0).to_dict()
    # stored with the camera options in the project file
    p = Project(name="p")
    p.save(tmp_path / "p.mmaze")
    set_camera_settings(p, "camera:2", {"view": CameraView(flip="h").to_dict(), "hardware": d["hardware"]})
    set_camera_settings(p, "pylon:4001", {"hardware": {"exposure": 5000.0, "pixel_format": "BayerRG8"}})
    p.save()
    q = Project.load(tmp_path / "p.mmaze")
    assert CameraHardware.from_dict(camera_settings(q, "camera:2")["hardware"]) == spec.hardware
    assert camera_settings(q, "pylon:4001")["hardware"]["pixel_format"] == "BayerRG8"
    assert camera_settings(q, "camera:0") == {}


def test_native_source_ids():
    s = SourceSpec("pylon:4001", second=1)
    assert s.is_native and not s.is_file and s.key == "pylon:4001+camera:1"
    assert s.label == "Basler camera 4001 | Camera 1"
    assert SourceSpec("spinnaker:#0").is_native and SourceSpec("ids:77").key == "ids:77"
    assert SourceSpec("/videos/a.avi").is_file and SourceSpec("C:/videos/a.avi").is_file
    assert not SourceSpec("nope:1").is_native and SourceSpec("nope:1").is_file  # an unknown scheme is a file name
    assert camsources.parse_source("genicam:SN-1:2") == ("genicam", "SN-1:2") and camsources.parse_source(3) is None


# ====================================================================== OpenCV cameras
P = cv2


def _cap(**kw):
    props = {P.CAP_PROP_EXPOSURE: -5.0, P.CAP_PROP_AUTO_EXPOSURE: 3.0, P.CAP_PROP_GAIN: 0.0,
             P.CAP_PROP_BRIGHTNESS: 128.0, P.CAP_PROP_CONTRAST: 32.0, P.CAP_PROP_AUTO_WB: 1.0,
             P.CAP_PROP_WB_TEMPERATURE: 4600.0}
    return FakeCap(props, **kw)


def test_opencv_controls_apply_read_back_and_report():
    cap = _cap(settable={P.CAP_PROP_EXPOSURE, P.CAP_PROP_AUTO_EXPOSURE, P.CAP_PROP_GAIN, P.CAP_PROP_BRIGHTNESS},
               clamp={P.CAP_PROP_BRIGHTNESS: (0, 100)})
    c = OpenCVControls(cap, platform="linux")
    assert c.defaults.to_dict() == {"exposure": -5.0, "auto_exposure": True, "gain": 0.0, "brightness": 128.0,
                                    "contrast": 32.0, "white_balance": 4600.0, "auto_white_balance": True}
    rep = c.apply(CameraHardware(exposure=200, auto_exposure=False, brightness=150, saturation=60, contrast=40))
    assert rep == {"auto_exposure": OK, "exposure": OK, "brightness": "adjusted:100", "contrast": UNSUPPORTED,
                   "saturation": UNSUPPORTED}
    # auto switches before values; V4L2 auto exposure: 1 = manual
    assert cap.sets[0] == (P.CAP_PROP_AUTO_EXPOSURE, 1.0) and cap.props[P.CAP_PROP_EXPOSURE] == 200
    # a value whose auto mode is requested on is not forced
    cap.sets.clear()
    c.apply(CameraHardware(exposure=10, auto_exposure=True))
    assert cap.sets == [(P.CAP_PROP_AUTO_EXPOSURE, 3.0)]
    info = {i.name: i for i in c.info()}
    assert info["contrast"].supported is False and info["exposure"].supported is True
    assert info["saturation"].supported is False  # not even readable (-1)
    assert info["exposure"].auto is True and info["exposure"].has_auto and not info["gain"].has_auto
    # reset puts back what the camera had when it opened (its exposure was automatic: no manual value forced)
    c.apply(CameraHardware(auto_exposure=False, gain=9))
    c.reset()
    assert cap.props[P.CAP_PROP_AUTO_EXPOSURE] == 3.0 and cap.props[P.CAP_PROP_GAIN] == 0.0
    assert cap.props[P.CAP_PROP_BRIGHTNESS] == 100  # the driver's maximum
    # DirectShow / MSMF auto exposure values
    w = _cap()
    OpenCVControls(w, platform="win32").apply(CameraHardware(auto_exposure=False))
    assert w.sets == [(P.CAP_PROP_AUTO_EXPOSURE, 0.25)]


def test_opencv_controls_never_crash():
    # AVFoundation (macOS): almost nothing settable
    mac = FakeCap({P.CAP_PROP_EXPOSURE: 0.0, P.CAP_PROP_GAIN: 0.0}, settable=set(), backend="AVFOUNDATION")
    c = OpenCVControls(mac, platform="darwin")
    rep = c.apply(CameraHardware(exposure=1, gain=2, auto_focus=False, auto_gain=True))
    assert set(rep.values()) == {UNSUPPORTED} and set(rep) == {"exposure", "auto_focus", "auto_gain"}  # gain: auto
    sup = {i.name: i.supported for i in c.info()}
    assert sup["exposure"] is False and sup["brightness"] is None  # tried and refused / unknown until tried
    # a driver raising on every call
    broken = FakeCap({}, raises=True)
    c = OpenCVControls(broken)
    assert c.defaults.is_empty and set(c.apply(CameraHardware(exposure=1)).values()) == {UNSUPPORTED}
    assert all(i.value is None for i in c.info())


def test_video_source_camera_hardware(monkeypatch):
    cap = _cap()
    monkeypatch.setattr(video_mod.cv2, "VideoCapture", lambda *a: cap)
    src = video_mod.VideoSource(0)
    assert src.is_camera and src.controls is not None
    assert src.apply_hardware(CameraHardware(gain=7)) == {"gain": OK} and cap.props[P.CAP_PROP_GAIN] == 7
    assert src.current_hardware().gain == 7 and src.reset_hardware()["gain"] == OK and cap.props[P.CAP_PROP_GAIN] == 0
    assert any(i.name == "focus" for i in src.hardware_controls())
    # applied when a SourceSpec opens; reported; transformed sources still reach the camera
    cap.sets.clear()
    spec = SourceSpec(0, view=CameraView(flip="h"), hardware=CameraHardware(brightness=90, saturation=10))
    out = spec.open()
    assert isinstance(out, TransformedSource) and hardware_target(out) is out.src
    assert out.hardware_report == {"brightness": OK, "saturation": UNSUPPORTED}
    assert cap.props[P.CAP_PROP_BRIGHTNESS] == 90
    assert apply_hardware(out, {"contrast": 50}) == {"contrast": OK}
    out.release()


def test_files_and_fake_openers_have_no_hardware(tmp_path):
    from manymaze.core import synthetic as syn

    p = tmp_path / "v.avi"
    syn.make_video(p, syn.line_path((20, 20), (80, 80), 5), size=(100, 100))
    src = SourceSpec(str(p), hardware=CameraHardware(gain=3)).open()
    assert src.hardware_report == {} and src.apply_hardware(CameraHardware(gain=1)) == {}
    src.release()

    class Minimal:  # a test opener without any hardware method
        is_camera, fps, width, height = True, 25.0, 10, 10

        def __init__(self, *a):
            pass

        def release(self):
            pass

    assert apply_hardware(Minimal(), CameraHardware(gain=1)) == {} and hardware_target(Minimal()) is None


# ====================================================================== pixel formats
def test_to_bgr_pixel_formats():
    mono = np.full((12, 16), 90, np.uint8)
    assert to_bgr(mono, "Mono8").shape == (12, 16, 3) and to_bgr(mono.reshape(-1), "Mono8", 16, 12).shape == \
        (12, 16, 3)
    assert to_bgr(np.full((6, 8), 4095, np.uint16), "Mono12")[0, 0, 0] == 255
    assert to_bgr(np.full((6, 8), 1 << 15, np.uint16), "Mono16")[0, 0, 0] == 128
    rgb = np.zeros((12, 16, 3), np.uint8)
    rgb[:, :, 0] = 200
    assert is_red(to_bgr(rgb, "RGB8")) and is_red(to_bgr(rgb.reshape(-1), "RGB8Packed", 16, 12))
    assert not is_red(to_bgr(rgb, "BGR8")) and to_bgr(rgb, "BGR8")[0, 0, 0] == 200
    # Bayer: GenICam names the pattern from the top-left pixel (RGGB = BayerRG)
    for fmt, (r_y, r_x) in (("BayerRG8", (0, 0)), ("BayerBG8", (1, 1)), ("BayerGR8", (0, 1)), ("BayerGB8", (1, 0))):
        a = np.zeros((16, 16), np.uint8)
        a[r_y::2, r_x::2] = 200
        assert is_red(to_bgr(a, fmt)), fmt
    a16 = np.zeros((16, 16), np.uint16)
    a16[0::2, 0::2] = 4000
    assert is_red(to_bgr(a16, "BayerRG12"))
    yuyv = np.full((6, 8, 2), 128, np.uint8)
    assert to_bgr(yuyv, "YUV422_8").shape == (6, 8, 3) and to_bgr(yuyv.reshape(-1), "YUV422Packed", 8, 6).shape == \
        (6, 8, 3)
    assert to_bgr(np.zeros((6, 8, 4), np.uint8), "BGRa8").shape == (6, 8, 3)


# ====================================================================== GenICam mapping
class DictNodes:
    def __init__(self, dev):
        self.dev = dev

    def has(self, n):
        return self.dev.has(n)

    def get(self, n):
        return self.dev.get(n)

    def set(self, n, v):
        self.dev.set(n, v)

    def limits(self, n):
        from fake_cameras import LIMITS
        return LIMITS[n]

    def execute(self, n):
        self.dev.command(n)


def test_genicam_controls_mapping():
    dev = FakeDevice()
    c = GenICamControls(DictNodes(dev))
    assert c.defaults.to_dict() == {"exposure": 10000.0, "auto_exposure": False, "gain": 0.0, "auto_gain": True,
                                    "brightness": 0.0, "auto_white_balance": False, "pixel_format": "Mono8",
                                    "trigger": False, "trigger_source": "Line0"}
    rep = c.apply(CameraHardware(exposure=5400, gain=30, brightness=12, auto_white_balance=True, contrast=5,
                                 white_balance=5000, focus=3, pixel_format="BayerRG8"))
    assert rep["exposure"] == "adjusted:5000"  # the camera's exposure steps
    assert rep["gain"] == "adjusted:24" and dev.f["GainAuto"] == "Off"  # clamped; a manual gain turns auto off
    assert rep["brightness"] == OK and dev.f["BlackLevel"] == 12 and rep["auto_white_balance"] == OK
    assert rep["contrast"] == rep["focus"] == UNSUPPORTED and "white_balance" not in rep  # automatic
    assert rep["pixel_format"] == OK and dev.f["PixelFormat"] == "BayerRG8"
    assert c.apply(CameraHardware(pixel_format="YUV411")) == {"pixel_format": UNSUPPORTED}
    rep = c.apply(CameraHardware(trigger=True, trigger_source="Line1"))
    assert rep == {"trigger": OK, "trigger_source": OK} and c.triggered and dev.f["TriggerSource"] == "Line1"
    info = {i.name: i for i in c.info()}
    assert info["gain"].maximum == 24.0 and info["exposure"].minimum == 20.0 and not info["focus"].supported
    assert info["gain"].has_auto and not info["brightness"].has_auto
    c.apply_format(5000, 100, 60)
    assert dev.f["Width"] == 640 and dev.f["Height"] == 100 and dev.f["AcquisitionFrameRateEnable"] is True
    assert c.frame_rate() == 60.0
    c.reset()
    assert dev.f["PixelFormat"] == "Mono8" and dev.f["TriggerMode"] == "Off" and dev.f["GainAuto"] == "Continuous"
    assert dev.f["ExposureTime"] == 10000.0 and dev.f["BalanceWhiteAuto"] == "Off"


# ====================================================================== native backends (fake SDKs)
@pytest.fixture(params=sorted(INSTALLERS))
def backend(request, monkeypatch, tmp_path):
    no_sdks(monkeypatch)
    devices = [FakeDevice("SN1", "Cam A"), FakeDevice("SN2", "Cam B")]
    prefix = INSTALLERS[request.param](monkeypatch, devices, tmp_path)
    yield prefix, devices
    camsources.set_cti_files([])


def test_native_enumeration_and_frames(backend):
    prefix, devices = backend
    cams, messages = camsources.list_native_cameras()
    mine = [c for c in cams if c.backend == prefix]
    assert [c.source for c in mine] == [f"{prefix}:SN1", f"{prefix}:SN2"] and "SN1" in mine[0].label
    assert len(messages) == 3  # the other three SDKs are missing
    status = {p: ok for p, _, ok, _ in camsources.backend_status()}
    assert status[prefix] and sum(status.values()) == 1

    cam = camsources.NativeCamera(f"{prefix}:SN2", 320, 240, 50)
    dev = devices[1]
    assert (cam.width, cam.height, cam.fps) == (320, 240, 50.0) and cam.is_camera and not dev.acquiring
    ok, f = cam.read()
    assert ok and f.shape == (240, 320, 3) and f.dtype == np.uint8 and dev.acquiring and cam.pos == 1
    # settings while streaming; the pixel format is locked while acquiring: acquisition restarts
    rep = cam.apply_hardware(CameraHardware(pixel_format="BayerRG8", exposure=3000, auto_gain=False))
    assert rep == {"pixel_format": OK, "auto_gain": OK, "exposure": OK}
    ok, f = cam.read()
    assert ok and is_red(f) and dev.acquiring
    cam.apply_hardware(CameraHardware(pixel_format="RGB8"))
    assert is_red(cam.read()[1])
    cam.apply_hardware(CameraHardware(pixel_format="Mono12"))
    assert cam.read()[1][0, 0, 0] == 255
    # external trigger: no frame until it fires (not an error)
    cam.apply_hardware(CameraHardware(trigger=True, trigger_source="Line1"))
    assert cam.triggered and cam.read() == (False, None)
    dev.trigger()
    assert cam.read()[0] and cam.read() == (False, None)
    assert {i.name: i.supported for i in cam.hardware_controls()}["gain"]
    cam.reset_hardware()
    assert dev.f["PixelFormat"] == "Mono8" and dev.f["TriggerMode"] == "Off" and dev.f["GainAuto"] == "Continuous"
    assert cam.read()[0]
    cam.release()
    assert dev.closed and not dev.acquiring and cam.read() == (False, None)
    cam.release()  # twice is harmless
    # "#n" picks the n-th camera; an absent serial fails to open with a clear error
    first = camsources.NativeCamera(f"{prefix}:#0")
    assert (first.width, first.height) == (64, 48) and first.read()[0]
    first.release()
    with pytest.raises(IOError, match="not found"):
        camsources.NativeCamera(f"{prefix}:NOPE")


def test_native_source_in_live_pipeline(backend):
    """A native camera flows through SourceSpec / SourceReader like an OpenCV camera; with the external trigger
    on, waiting for frames is not a camera failure."""
    prefix, devices = backend
    dev = devices[0]
    spec = SourceSpec(f"{prefix}:SN1", view=CameraView(rotate=90), size=(80, 60),
                      hardware=CameraHardware(trigger=True, gain=5, contrast=3))
    frames, failed = [], []

    class Reader(SourceReader):
        def on_frame(self, frame, ts):
            frames.append((frame, ts))

        def on_failed(self, msg):
            failed.append(msg)

    r = Reader(spec)
    r.start()
    t0 = time.monotonic()
    while r.src is None and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    assert r.size == (60, 80) and r.camera() is not None
    assert r.hardware_report["contrast"] == UNSUPPORTED and r.hardware_report["gain"] == OK
    assert "Contrast" in r.hardware_message and "Gain" not in r.hardware_message
    time.sleep(0.4)  # far more than 100 empty reads
    assert not frames and not failed and r.thread.is_alive()
    dev.trigger(3)
    t0 = time.monotonic()
    while len(frames) < 3 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    assert len(frames) == 3 and frames[0][0].shape == (80, 60, 3)
    assert r.set_hardware({"trigger": False}) == {"trigger": OK} and spec.hardware.trigger is False
    t0 = time.monotonic()
    while len(frames) < 10 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    assert len(frames) >= 10
    r.stop()
    assert not r.thread.is_alive() and dev.closed and not failed


def test_missing_sdks_list_nothing_with_helpful_messages(monkeypatch, tmp_path):
    no_sdks(monkeypatch)
    cams, messages = camsources.list_native_cameras()
    assert cams == []
    text = "\n".join(messages)
    for hint in ("pip install harvesters", "pip install pypylon", "Spinnaker SDK", "IDS peak SDK"):
        assert hint in text
    assert all(not ok for _, _, ok, _ in camsources.backend_status())
    with pytest.raises(IOError, match="pypylon"):
        camsources.NativeCamera("pylon:1")
    # harvesters installed but no GenTL producer configured
    from fake_cameras import install_harvesters

    install_harvesters(monkeypatch, [FakeDevice()], tmp_path)
    camsources.set_cti_files([])
    msg = next(m for _, v, ok, m in camsources.backend_status() if v == "GenICam")
    assert "no GenTL producer" in msg
    camsources.set_cti_files([])


def test_cti_files_from_environment(monkeypatch, tmp_path):
    (tmp_path / "a.cti").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("x")
    monkeypatch.setenv("GENICAM_GENTL64_PATH", str(tmp_path))
    camsources.set_cti_files([str(tmp_path / "missing.cti")])
    assert camsources.cti_files() == [str(tmp_path / "a.cti")]
    camsources.set_cti_files([])


def test_failing_sdk_does_not_break_the_scan(monkeypatch, tmp_path):
    no_sdks(monkeypatch)
    from fake_cameras import install_pylon

    install_pylon(monkeypatch, [FakeDevice()])

    def boom():
        raise RuntimeError("transport layer error")

    monkeypatch.setattr(sys.modules["pypylon.pylon"].TlFactory, "GetInstance", boom)
    cams, messages = camsources.list_native_cameras()
    assert cams == [] and any("transport layer error" in m for m in messages)


def test_concurrent_settings_while_reading(backend):
    prefix, devices = backend
    cam = camsources.NativeCamera(f"{prefix}:SN1")
    stop = threading.Event()
    errors = []

    def reader():
        while not stop.is_set():
            try:
                cam.read()
            except Exception as e:  # pragma: no cover - would be the bug
                errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    for i in range(30):
        cam.apply_hardware(CameraHardware(gain=i % 20, pixel_format=("Mono8", "BayerRG8")[i % 2]))
    stop.set()
    t.join(5)
    cam.release()
    assert not errors


def test_controls_table_is_consistent():
    names = [c.name for c in CONTROLS]
    assert names == ["exposure", "gain", "brightness", "contrast", "saturation", "white_balance", "focus"]
    assert all(c.auto is None or c.auto.startswith("auto_") for c in CONTROLS)
    assert all(hasattr(CameraHardware(), c.name) and (c.auto is None or hasattr(CameraHardware(), c.auto))
               for c in CONTROLS)


# ====================================================================== audit 2026-10-08: unsupported formats
def test_to_bgr_unsupported_formats_say_so():
    """Bit-packed and unknown formats raise UnsupportedPixelFormat naming the format (not a reshape error that
    looks like a broken frame); RGB / BGR with more than 8 bits per channel are converted."""
    packed = np.zeros(16 * 12 * 3 // 2, np.uint8)
    for fmt in ("Mono12p", "Mono10Packed", "Mono12Packed", "BayerRG12p", "Mono10p", "Coord3D_C16", "YUV411_8"):
        with pytest.raises(UnsupportedPixelFormat, match=fmt):
            to_bgr(packed, fmt, 16, 12)
    with pytest.raises(UnsupportedPixelFormat, match="Mono8"):  # wrong buffer size for the image
        to_bgr(np.zeros(100, np.uint8), "Mono8", 16, 12)
    rgb12 = np.zeros((12, 16, 3), np.uint16)
    rgb12[:, :, 0] = 4000
    assert is_red(to_bgr(rgb12, "RGB12")) and is_red(to_bgr(rgb12.reshape(-1), "RGB12", 16, 12))
    assert not is_red(to_bgr(rgb12, "BGR12")) and to_bgr(rgb12, "BGR12")[0, 0, 0] == 250
    assert is_red(to_bgr((rgb12.astype(np.uint32) << 4).astype(np.uint16), "RGB16"))


def test_native_camera_unsupported_format_is_an_error_not_a_disconnect(backend):
    prefix, devices = backend
    cam = camsources.NativeCamera(f"{prefix}:SN1")
    assert cam.read()[0]
    devices[0].f["PixelFormat"] = "Mono12p"  # set behind our back (e.g. by the vendor's tool)
    for _ in range(cam.bad_frames_tolerated):  # a few bad buffers are dropped frames ...
        assert cam.read() == (False, None)
    with pytest.raises(UnsupportedPixelFormat, match="Mono12p"):  # ... then the reason is reported
        cam.read()
    devices[0].f["PixelFormat"] = "Mono8"
    assert cam.read()[0]
    cam.release()


def test_reader_fails_at_once_with_the_pixel_format_message():
    """SourceReader does not try to reopen a camera whose format cannot be converted (it would only fail after
    the reconnect timeout with "the camera stopped delivering frames")."""

    class BadFormatCam:
        is_camera, fps, width, height, frame_count = True, 25.0, 32, 24, 0
        opened = 0

        def __init__(self, *a):
            type(self).opened += 1

        def read(self):
            raise UnsupportedPixelFormat("The camera's pixel format 'Mono12p' is not supported")

        def seek(self, i):
            pass

        def release(self):
            pass

    failed = []

    class Reader(SourceReader):
        def on_failed(self, msg):
            failed.append(msg)

    r = Reader(SourceSpec(0), opener=lambda *a: BadFormatCam(*a))
    r.start()
    r.thread.join(5)
    assert not r.thread.is_alive() and failed and "Mono12p" in failed[0] and BadFormatCam.opened == 1
    assert not r.capture_log


def test_camera_reconnecting_at_another_size_fails_clearly():
    sizes = iter([(24, 32), (48, 64)])

    class Cam:
        is_camera, fps, frame_count = True, 25.0, 0

        def __init__(self, *a):
            self.h, self.w = next(sizes)
            self.width, self.height = self.w, self.h
            self.n = 5

        def read(self):
            if self.n <= 0:
                return False, None
            self.n -= 1
            return True, np.zeros((self.h, self.w, 3), np.uint8)

        def seek(self, i):
            pass

        def release(self):
            pass

    events = []

    class Reader(SourceReader):
        reconnect_delays = (0.01,)
        stall_reads = 3

        def on_capture_restored(self, gap):
            events.append("restored")

        def on_failed(self, msg):
            events.append(msg)

    r = Reader(SourceSpec(0), opener=lambda *a: Cam(*a))
    r.start()
    r.thread.join(5)
    assert not r.thread.is_alive() and "restored" not in events
    assert "64×48 instead of 32×24" in events[-1]


def test_reader_stops_promptly_while_pacing_a_slow_file(tmp_path):
    """Slow-motion playback of a file: stop() returns at once instead of waiting for the next frame to be due."""
    from manymaze.core import synthetic as syn

    vp = tmp_path / "v.avi"
    syn.make_video(vp, syn.random_walk(10, (10, 10, 50, 50), seed=1), size=(64, 64), fps=25,
                   arena=("rect", 10, 10, 40, 40))
    frames = []

    class Reader(SourceReader):
        def on_frame(self, frame, ts):
            frames.append(ts)

    r = Reader(SourceSpec(str(vp)), speed=0.002)  # one frame every 20 s
    r.start()
    t0 = time.monotonic()
    while len(frames) < 1 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    t0 = time.monotonic()
    r.stop()
    assert not r.thread.is_alive() and time.monotonic() - t0 < 1 and len(frames) == 1
