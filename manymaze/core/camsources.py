"""Camera sources beyond OpenCV: industrial GigE Vision / USB3 Vision cameras through their vendors' SDKs.

A live source is identified by:

* an int — an OpenCV camera index (webcams, UVC cameras and analogue capture cards / frame grabbers that present
  themselves as a video device, read by core.video.VideoSource);
* a string ``"<backend>:<serial>"`` — a native camera of one of the backends below (``"#<n>"`` instead of a serial
  picks the n-th camera of that backend);
* any other string — a video file simulating a camera.

Backends (all optional, imported lazily; a missing package lists no camera and says what to install):

=========  ============================  =========================================================================
prefix     package                       cameras
=========  ============================  =========================================================================
genicam    ``harvesters``                any GenICam camera, through the GenTL producer (.cti) of a vendor SDK
pylon      ``pypylon``                   Basler (pylon SDK bundled with the wheel)
spinnaker  ``PySpin``                    FLIR / Teledyne (Spinnaker SDK and its Python wheel)
ids        ``ids_peak``                  IDS (IDS peak SDK and its Python wheels)
=========  ============================  =========================================================================

Every backend opens a camera as a *driver* with a GenICam node-map adapter (``nodes``) and ``start`` / ``grab`` /
``stop`` / ``close``.  :class:`NativeCamera` wraps a driver behind the interface of core.video.VideoSource (read,
seek, release, fps, width, height, is_camera …) so frames flow into the live pipeline exactly like OpenCV frames,
converts the pixel format to BGR and applies the hardware settings of core.camhw.
"""

from __future__ import annotations

import glob
import importlib
import os
import threading
from dataclasses import dataclass

import numpy as np

from .camhw import CameraHardware, GenICamControls, UnsupportedPixelFormat, to_bgr


@dataclass
class CameraInfo:
    """A camera found by a scan: ``source`` is what SourceSpec.source stores (an int for OpenCV)."""

    source: object
    label: str
    backend: str = "opencv"
    serial: str = ""
    model: str = ""
    vendor: str = ""


def parse_source(source) -> tuple[str, str] | None:
    """("pylon", "40012345") for a native camera id, None for OpenCV indices and files."""
    if not isinstance(source, str) or ":" not in source:
        return None
    prefix, ident = source.split(":", 1)
    return (prefix, ident) if prefix in BACKENDS else None


def is_native_source(source) -> bool:
    return parse_source(source) is not None


def native_label(source) -> str:
    p = parse_source(source)
    if p is None:
        return str(source)
    b = BACKENDS[p[0]]
    return f"{b.vendor} camera {p[1]}"


def _import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:  # ImportError, or an SDK whose native libraries are missing
        return None


def _try(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ====================================================================== backends
class Backend:
    """An SDK able to list and open cameras.  Subclasses set the class attributes and implement _enumerate /
    _open; ``module()`` imports the SDK lazily (None when missing)."""

    prefix = ""
    vendor = ""
    module_name = ""
    install = ""  # what to install when the module is missing

    def module(self):
        return _import(self.module_name)

    def available(self) -> bool:
        return self.module() is not None

    def status(self) -> str:
        """"" when usable, otherwise what is missing."""
        return "" if self.available() else f"{self.vendor} cameras: {self.install}"

    def enumerate(self) -> list[CameraInfo]:
        if not self.available():
            return []
        return [CameraInfo(f"{self.prefix}:{serial or '#' + str(i)}",
                           f"{vendor or self.vendor} {model}".strip() + (f" ({serial})" if serial else ""),
                           self.prefix, serial, model, vendor or self.vendor)
                for i, (serial, model, vendor) in enumerate(self._enumerate())]

    def open(self, ident: str):
        if not self.available():
            raise IOError(self.status())
        return self._open(ident)

    def _enumerate(self) -> list[tuple[str, str, str]]:
        """(serial, model, vendor) of every camera."""
        return []

    def _open(self, ident: str):
        raise NotImplementedError

    @staticmethod
    def _pick(devices, ident: str, serial_of):
        """The device whose serial is ``ident`` (or the n-th for "#n")."""
        devices = list(devices)
        if ident.startswith("#") and ident[1:].isdigit():
            i = int(ident[1:])
            if i < len(devices):
                return devices[i]
        for d in devices:
            if str(_try(lambda: serial_of(d), "")) == ident:
                return d
        raise IOError(f"camera {ident} not found (is it connected and not used by another program?)")


# ---------------------------------------------------------------- harvesters (any GenTL producer)
_cti_lock = threading.Lock()
_cti_files: list[str] = []


def set_cti_files(paths):
    """GenTL producer files (.cti) of the installed vendor SDKs, for the harvesters backend."""
    global _cti_files
    with _cti_lock:
        _cti_files = [str(p) for p in dict.fromkeys(paths or []) if p]
    HarvestersBackend.reset()


def cti_files() -> list[str]:
    """The configured producers plus those found in GENICAM_GENTL64_PATH / GENICAM_GENTL32_PATH."""
    found = list(_cti_files)
    for var in ("GENICAM_GENTL64_PATH", "GENICAM_GENTL32_PATH"):
        for d in (os.environ.get(var) or "").split(os.pathsep):
            if d and os.path.isdir(d):
                found += sorted(glob.glob(os.path.join(d, "*.cti")))
    return list(dict.fromkeys(f for f in found if os.path.isfile(f)))


class _HarvestersNodes:
    def __init__(self, node_map):
        self.nm = node_map

    def _node(self, name):
        return getattr(self.nm, name)

    def has(self, name):
        return _try(lambda: (self._node(name).value, True)[1], False)  # present and readable

    def get(self, name):
        return self._node(name).value

    def set(self, name, value):
        self._node(name).value = value

    def limits(self, name):
        n = self._node(name)
        return n.min, n.max

    def execute(self, name):
        self._node(name).execute()


class _HarvestersDriver:
    def __init__(self, ia):
        self.ia = ia
        self.nodes = _HarvestersNodes(ia.remote_device.node_map)

    def start(self):
        (getattr(self.ia, "start", None) or self.ia.start_acquisition)()

    def stop(self):
        _try((getattr(self.ia, "stop", None) or self.ia.stop_acquisition))

    def grab(self, timeout: float):
        fetch = getattr(self.ia, "fetch", None) or self.ia.fetch_buffer
        try:
            buf = fetch(timeout=timeout)
        except Exception:  # timeout
            return None
        with buf:
            c = buf.payload.components[0]
            return np.array(c.data, copy=True), str(c.data_format), int(c.width), int(c.height)

    def close(self):
        self.stop()
        _try(self.ia.destroy)


class HarvestersBackend(Backend):
    prefix, vendor, module_name = "genicam", "GenICam", "harvesters.core"
    install = ("pip install harvesters, then add the GenTL producer (.cti) of your camera's SDK (Basler pylon, "
               "Teledyne Spinnaker, IDS peak, Allied Vision Vimba X, MATRIX VISION mvIMPACT…) in Industrial "
               "cameras.")
    _harvester = None
    _lock = threading.Lock()

    @classmethod
    def reset(cls):
        with cls._lock:
            h, cls._harvester = cls._harvester, None
        if h is not None:
            _try(h.reset)

    def available(self) -> bool:
        return self.module() is not None and bool(cti_files())

    def status(self) -> str:
        if self.module() is None:
            return f"GenICam cameras: {self.install}"
        if not cti_files():
            return ("GenICam cameras: no GenTL producer (.cti) configured — add the .cti file of your camera "
                    "vendor's SDK in Industrial cameras (or set GENICAM_GENTL64_PATH).")
        return ""

    def harvester(self):
        with self._lock:
            if HarvestersBackend._harvester is None:
                h = self.module().Harvester()
                for f in cti_files():
                    _try(lambda: (getattr(h, "add_file", None) or h.add_cti_file)(f))
                HarvestersBackend._harvester = h
            h = HarvestersBackend._harvester
        (getattr(h, "update", None) or h.update_device_info_list)()
        return h

    def _enumerate(self):
        infos = self.harvester().device_info_list
        return [(str(getattr(d, "serial_number", "") or ""), str(getattr(d, "model", "") or ""),
                 str(getattr(d, "vendor", "") or "")) for d in infos]

    def _open(self, ident):
        h = self.harvester()
        d = self._pick(h.device_info_list, ident, lambda d: d.serial_number)
        i = list(h.device_info_list).index(d)
        create = getattr(h, "create", None)
        ia = create(i) if create is not None else h.create_image_acquirer(list_index=i)
        return _HarvestersDriver(ia)


# ---------------------------------------------------------------- Basler pypylon
class _PylonNodes:
    def __init__(self, cam):
        self.cam = cam

    def has(self, name):
        def check():
            n = getattr(self.cam, name)
            gen = _import("pypylon.genicam")
            if gen is not None and hasattr(gen, "IsAvailable"):
                return bool(gen.IsAvailable(n))
            return True
        return _try(check, False)

    def get(self, name):
        return getattr(self.cam, name).Value

    def set(self, name, value):
        getattr(self.cam, name).Value = value

    def limits(self, name):
        n = getattr(self.cam, name)
        return n.Min, n.Max

    def execute(self, name):
        getattr(self.cam, name).Execute()


class _PylonDriver:
    def __init__(self, pylon, cam):
        self.pylon, self.cam = pylon, cam
        self.nodes = _PylonNodes(cam)

    def start(self):
        self.cam.StartGrabbing(self.pylon.GrabStrategy_LatestImageOnly)

    def stop(self):
        if _try(self.cam.IsGrabbing, False):
            self.cam.StopGrabbing()

    def grab(self, timeout: float):
        res = self.cam.RetrieveResult(int(timeout * 1000), self.pylon.TimeoutHandling_Return)
        if res is None:
            return None
        try:
            if not _try(res.IsValid, True) or not res.GrabSucceeded():
                return None
            arr = np.array(res.Array, copy=True)
            fmt = _try(lambda: self.cam.PixelFormat.Value, "Mono8")
            return arr, str(fmt), int(res.Width), int(res.Height)
        finally:
            _try(res.Release)

    def close(self):
        self.stop()
        _try(self.cam.Close)


class PylonBackend(Backend):
    prefix, vendor, module_name = "pylon", "Basler", "pypylon.pylon"
    install = "pip install pypylon (the pylon runtime is bundled; GigE cameras may need the pylon IP configurator)."

    def _devices(self):
        pylon = self.module()
        return pylon, pylon.TlFactory.GetInstance(), list(pylon.TlFactory.GetInstance().EnumerateDevices())

    def _enumerate(self):
        _, _, devs = self._devices()
        return [(str(d.GetSerialNumber()), str(_try(d.GetModelName, "")), str(_try(d.GetVendorName, "Basler")))
                for d in devs]

    def _open(self, ident):
        pylon, tlf, devs = self._devices()
        d = self._pick(devs, ident, lambda d: d.GetSerialNumber())
        cam = pylon.InstantCamera(tlf.CreateDevice(d))
        cam.Open()
        return _PylonDriver(pylon, cam)


# ---------------------------------------------------------------- FLIR / Teledyne PySpin
class _SpinNodes:
    def __init__(self, spin, node_map):
        self.spin, self.nm = spin, node_map

    def _node(self, name):
        n = self.nm.GetNode(name)
        if n is None or not self.spin.IsAvailable(n):
            raise KeyError(name)
        return n

    def _typed(self, name):
        s, n = self.spin, self._node(name)
        t = n.GetPrincipalInterfaceType()
        ptr = {s.intfIFloat: s.CFloatPtr, s.intfIInteger: s.CIntegerPtr, s.intfIBoolean: s.CBooleanPtr,
               s.intfIEnumeration: s.CEnumerationPtr, s.intfIString: s.CStringPtr,
               s.intfICommand: s.CCommandPtr}.get(t)
        if ptr is None:
            raise TypeError(name)
        return t, ptr(n)

    def has(self, name):
        return _try(lambda: self.spin.IsReadable(self._node(name)), False)

    def get(self, name):
        t, p = self._typed(name)
        if t == self.spin.intfIEnumeration:
            return p.GetCurrentEntry().GetSymbolic()
        return p.GetValue()

    def set(self, name, value):
        t, p = self._typed(name)
        if t == self.spin.intfIEnumeration:
            entry = p.GetEntryByName(str(value))
            if entry is None or not self.spin.IsAvailable(entry):
                raise ValueError(f"{name}: no entry {value}")
            p.SetIntValue(entry.GetValue())
        else:
            p.SetValue(value)

    def limits(self, name):
        _, p = self._typed(name)
        return p.GetMin(), p.GetMax()

    def execute(self, name):
        self._typed(name)[1].Execute()


class _SpinDriver:
    def __init__(self, backend, spin, cam, cams):
        self.backend, self.spin, self.cam, self.cams = backend, spin, cam, cams
        self.nodes = _SpinNodes(spin, cam.GetNodeMap())
        self._acquiring = False

    def start(self):
        if self.nodes.has("AcquisitionMode"):
            _try(lambda: self.nodes.set("AcquisitionMode", "Continuous"))
        self.cam.BeginAcquisition()
        self._acquiring = True

    def stop(self):
        if self._acquiring:
            self._acquiring = False
            _try(self.cam.EndAcquisition)

    def grab(self, timeout: float):
        try:
            img = self.cam.GetNextImage(max(1, int(timeout * 1000)))
        except Exception:  # SpinnakerException on timeout
            return None
        try:
            if img.IsIncomplete():
                return None
            return (np.array(img.GetNDArray(), copy=True), str(img.GetPixelFormatName()), int(img.GetWidth()),
                    int(img.GetHeight()))
        finally:
            _try(img.Release)

    def close(self):
        self.stop()
        _try(self.cam.DeInit)
        self.cam = None
        _try(self.cams.Clear)
        self.backend.release_system()


class SpinnakerBackend(Backend):
    prefix, vendor, module_name = "spinnaker", "FLIR", "PySpin"
    install = ("install the Teledyne FLIR Spinnaker SDK, then its PySpin wheel (spinnaker_python) for your "
               "Python version.")
    _users = 0
    _lock = threading.Lock()

    def system(self):
        with self._lock:
            SpinnakerBackend._users += 1
        return self.module().System.GetInstance()

    def release_system(self):
        with self._lock:
            SpinnakerBackend._users = max(0, SpinnakerBackend._users - 1)
            last = SpinnakerBackend._users == 0
        if last:
            _try(lambda: self.module().System.GetInstance().ReleaseInstance())

    def _info(self, cam, name):
        spin = self.module()
        return _try(lambda: str(spin.CStringPtr(cam.GetTLDeviceNodeMap().GetNode(name)).GetValue()), "")

    def _enumerate(self):
        system = self.system()
        cams = system.GetCameras()
        try:
            return [(self._info(c, "DeviceSerialNumber"), self._info(c, "DeviceModelName"),
                     self._info(c, "DeviceVendorName")) for c in cams]
        finally:
            _try(cams.Clear)
            self.release_system()

    def _open(self, ident):
        system = self.system()
        cams = system.GetCameras()
        try:
            cam = self._pick(cams, ident, lambda c: self._info(c, "DeviceSerialNumber"))
            cam.Init()
        except Exception:
            _try(cams.Clear)
            self.release_system()
            raise
        return _SpinDriver(self, self.module(), cam, cams)


# ---------------------------------------------------------------- IDS peak
class _IdsNodes:
    def __init__(self, node_map):
        self.nm = node_map

    def has(self, name):
        return _try(lambda: bool(self.nm.HasNode(name)), False)

    def get(self, name):
        n = self.nm.FindNode(name)
        if hasattr(n, "CurrentEntry"):
            return n.CurrentEntry().SymbolicValue()
        return n.Value()

    def set(self, name, value):
        n = self.nm.FindNode(name)
        if hasattr(n, "SetCurrentEntry"):
            n.SetCurrentEntry(str(value))
        else:
            n.SetValue(value)

    def limits(self, name):
        n = self.nm.FindNode(name)
        return n.Minimum(), n.Maximum()

    def execute(self, name):
        n = self.nm.FindNode(name)
        n.Execute()
        if hasattr(n, "WaitUntilDone"):
            _try(n.WaitUntilDone)


class _IdsDriver:
    def __init__(self, backend, peak, device):
        self.backend, self.peak, self.device = backend, peak, device
        self.nodes = _IdsNodes(device.RemoteDevice().NodeMaps()[0])
        self.stream = None

    def start(self):
        ds = self.device.DataStreams()[0].OpenDataStream()
        payload = int(self.nodes.get("PayloadSize"))
        for _ in range(max(3, int(_try(ds.NumBuffersAnnouncedMinRequired, 3)))):
            ds.QueueBuffer(ds.AllocAndAnnounceBuffer(payload))
        if self.nodes.has("TLParamsLocked"):
            self.nodes.set("TLParamsLocked", 1)
        ds.StartAcquisition()
        self.stream = ds
        self.nodes.execute("AcquisitionStart")

    def stop(self):
        ds, self.stream = self.stream, None
        if ds is None:
            return
        _try(lambda: self.nodes.execute("AcquisitionStop"))
        _try(ds.KillWait)
        _try(lambda: ds.StopAcquisition(self.peak.AcquisitionStopMode_Default))
        _try(lambda: ds.Flush(self.peak.DataStreamFlushMode_DiscardAll))
        for b in _try(ds.AnnouncedBuffers, []) or []:
            _try(lambda b=b: ds.RevokeBuffer(b))
        if self.nodes.has("TLParamsLocked"):
            _try(lambda: self.nodes.set("TLParamsLocked", 0))

    def grab(self, timeout: float):
        ds = self.stream
        if ds is None:
            return None
        try:
            buf = ds.WaitForFinishedBuffer(max(1, int(timeout * 1000)))
        except Exception:  # timeout
            return None
        try:
            ext = _import("ids_peak.ids_peak_ipl_extension")
            img = ext.BufferToImage(buf)
            get = getattr(img, "get_numpy_1D", None) or img.get_numpy
            return (np.array(get(), copy=True), str(img.PixelFormat().Name()), int(img.Width()),
                    int(img.Height()))
        finally:
            _try(lambda: ds.QueueBuffer(buf))

    def close(self):
        self.stop()
        self.device = None
        self.backend.release_library()


class IdsBackend(Backend):
    prefix, vendor, module_name = "ids", "IDS", "ids_peak.ids_peak"
    install = "install the IDS peak SDK, then pip install ids_peak ids_peak_ipl."
    _users = 0
    _lock = threading.Lock()

    def library(self):
        peak = self.module()
        with self._lock:
            if IdsBackend._users == 0:
                peak.Library.Initialize()
            IdsBackend._users += 1
        return peak

    def release_library(self):
        with self._lock:
            IdsBackend._users = max(0, IdsBackend._users - 1)
            last = IdsBackend._users == 0
        if last:
            _try(self.module().Library.Close)

    def _devices(self):
        peak = self.library()
        dm = peak.DeviceManager.Instance()
        dm.Update()
        return peak, list(dm.Devices())

    def _enumerate(self):
        _, devs = self._devices()
        try:
            return [(str(_try(d.SerialNumber, "")), str(_try(d.ModelName, "")), "IDS") for d in devs]
        finally:
            self.release_library()

    def _open(self, ident):
        peak, devs = self._devices()
        try:
            d = self._pick(devs, ident, lambda d: d.SerialNumber())
            return _IdsDriver(self, peak, d.OpenDevice(peak.DeviceAccessType_Control))
        except Exception:
            self.release_library()
            raise


BACKENDS: dict[str, Backend] = {b.prefix: b for b in (HarvestersBackend(), PylonBackend(), SpinnakerBackend(),
                                                      IdsBackend())}


def backend_status() -> list[tuple[str, str, bool, str]]:
    """(prefix, vendor, usable, message) of every backend, for the Industrial cameras dialog."""
    out = []
    for p, b in BACKENDS.items():
        msg = _try(b.status, f"{b.vendor} cameras: the SDK failed to load")
        out.append((p, b.vendor, not msg, msg or f"{b.module_name.split('.')[0]} is installed"))
    return out


def list_native_cameras() -> tuple[list[CameraInfo], list[str]]:
    """Cameras of every installed backend, and messages about missing / failing backends."""
    cams, messages = [], []
    for b in BACKENDS.values():
        msg = _try(b.status, "")
        if msg:
            messages.append(msg)
            continue
        try:
            cams += b.enumerate()
        except Exception as e:
            messages.append(f"{b.vendor} cameras: {type(e).__name__}: {e}")
    return cams, messages


# ====================================================================== the camera object
class NativeCamera:
    """A native (SDK) camera behind the VideoSource interface used by the live pipeline.  Frames are BGR uint8
    whatever the PixelFormat; acquisition starts at the first read; ``timeout`` (s) bounds every read, so a
    camera waiting for its external trigger returns (False, None) instead of blocking.  A pixel format that cannot
    be converted raises :class:`~.camhw.UnsupportedPixelFormat` from :meth:`read` (after bad_frames_tolerated
    consecutive buffers) rather than looking like a camera that stopped delivering frames."""

    bad_frames_tolerated = 5

    def __init__(self, source: str, width: int | None = None, height: int | None = None, fps: float | None = None,
                 timeout: float = 0.5):
        p = parse_source(source)
        if p is None:
            raise IOError(f"Not a native camera: {source!r}")
        self.source = source
        self.is_camera = True
        self.frame_count = 0
        self.pos = 0
        self._bad_frames = 0
        self.timeout = timeout
        self._lock = threading.RLock()
        self._acquiring = False
        self.driver = BACKENDS[p[0]].open(p[1])
        try:
            # the defaults are those of the camera before the requested size / rate (Reset to camera defaults)
            self.controls = GenICamControls(self.driver.nodes, self._lock)
            self.controls.apply_format(width, height, fps)
            nodes = self.driver.nodes
            self.width = int(_try(lambda: nodes.get("Width")) or width or 0)
            self.height = int(_try(lambda: nodes.get("Height")) or height or 0)
            self.fps = float(self.controls.frame_rate() or fps or 25.0)
        except Exception:
            _try(self.driver.close)
            raise

    @property
    def duration(self) -> float:
        return 0.0

    @property
    def triggered(self) -> bool:
        """Waiting for an external trigger: frames come when the trigger fires (no frame is not an error)."""
        return self.controls.triggered

    def _start(self):
        if not self._acquiring:
            self.driver.start()
            self._acquiring = True

    def _stop(self):
        if self._acquiring:
            self._acquiring = False
            self.driver.stop()

    def read(self):
        with self._lock:
            if self.driver is None:
                return False, None
            self._start()
            got = self.driver.grab(self.timeout)
        if got is None:
            return False, None
        data, fmt, w, h = got
        try:
            frame = to_bgr(data, fmt, w, h)
        except UnsupportedPixelFormat:
            # reopening the camera (what a stalled camera gets) cannot help: fail with the reason instead
            if self._bad_frames < self.bad_frames_tolerated:  # a single truncated buffer is a dropped frame
                self._bad_frames += 1
                return False, None
            raise
        except Exception:
            return False, None
        self._bad_frames = 0
        self.width, self.height = frame.shape[1], frame.shape[0]
        self.pos += 1
        return True, frame

    def seek(self, index: int):
        pass

    def frame_at(self, index: int):
        ok, f = self.read()
        return f if ok else None

    # ---- hardware settings (any thread)
    def hardware_controls(self):
        return self.controls.info()

    def current_hardware(self) -> CameraHardware:
        return self.controls.current()

    def apply_hardware(self, hw: CameraHardware) -> dict:
        """Set ``hw``; pixel format / trigger changes stop and restart the acquisition (they are locked while the
        camera streams)."""
        with self._lock:
            restart = self._acquiring and (hw.pixel_format is not None or hw.trigger is not None or
                                           hw.trigger_source is not None)
            if restart:
                self._stop()
            try:
                return self.controls.apply(hw)
            finally:
                if restart:
                    _try(self._start)

    def reset_hardware(self) -> dict:
        return self.apply_hardware(self.controls.defaults)

    def release(self):
        with self._lock:
            d, self.driver = self.driver, None
            self._acquiring = False
        if d is not None:
            _try(d.close)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()
