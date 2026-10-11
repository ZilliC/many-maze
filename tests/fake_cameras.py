"""Fake camera hardware for the tests: an OpenCV capture with settable properties, and fake modules of the
industrial camera SDKs (harvesters, pypylon, PySpin, ids_peak) driving a generic GenICam device.  Only the parts of
the SDK APIs that core.camsources uses are imitated."""

from __future__ import annotations

import sys
import types

import numpy as np


# ====================================================================== OpenCV
class FakeCap:
    """cv2.VideoCapture-like camera: ``props`` are readable, ``settable`` accept set(); ``clamp`` limits values
    (the driver adjusts them); ``backend`` is getBackendName()."""

    def __init__(self, props=None, settable=None, clamp=None, backend="V4L2", raises=False):
        self.props = dict(props or {})
        self.settable = set(settable if settable is not None else self.props)
        self.clamp = dict(clamp or {})
        self.backend = backend
        self.raises = raises
        self.sets = []
        self.opened = True

    def isOpened(self):
        return self.opened

    def getBackendName(self):
        return self.backend

    def get(self, prop):
        if self.raises:
            raise RuntimeError("driver error")
        return float(self.props.get(prop, -1))

    def set(self, prop, value):
        if self.raises:
            raise RuntimeError("driver error")
        self.sets.append((prop, value))
        if prop not in self.settable:
            return False
        lo, hi = self.clamp.get(prop, (-1e9, 1e9))
        self.props[prop] = min(max(value, lo), hi)
        return True

    def read(self):
        return True, np.full((48, 64, 3), 120, np.uint8)

    def release(self):
        self.opened = False


# ====================================================================== a GenICam device
FEATURES = {"Width": 64, "Height": 48, "PixelFormat": "Mono8", "ExposureTime": 10000.0, "ExposureAuto": "Off",
            "Gain": 0.0, "GainAuto": "Continuous", "BalanceWhiteAuto": "Off", "BlackLevel": 0.0,
            "AcquisitionFrameRateEnable": False, "AcquisitionFrameRate": 30.0, "TriggerSelector": "FrameStart",
            "TriggerMode": "Off", "TriggerSource": "Line0", "TriggerActivation": "RisingEdge",
            "AcquisitionMode": "Continuous", "PayloadSize": 64 * 48, "TLParamsLocked": 0}
LIMITS = {"Width": (16, 640), "Height": (16, 480), "ExposureTime": (20.0, 1e6), "Gain": (0.0, 24.0),
          "BlackLevel": (0.0, 255.0), "AcquisitionFrameRate": (1.0, 100.0)}
ENUMS = {"PixelFormat": ("Mono8", "Mono12", "BayerRG8", "RGB8"), "ExposureAuto": ("Off", "Once", "Continuous"),
         "GainAuto": ("Off", "Once", "Continuous"), "BalanceWhiteAuto": ("Off", "Once", "Continuous"),
         "TriggerSelector": ("FrameStart",), "TriggerMode": ("Off", "On"),
         "TriggerSource": ("Line0", "Line1", "Software"), "TriggerActivation": ("RisingEdge", "FallingEdge"),
         "AcquisitionMode": ("Continuous", "SingleFrame")}
LOCKED = {"Width", "Height", "PixelFormat", "TriggerMode", "TriggerSource", "PayloadSize"}


class FakeDevice:
    """Features (with limits, enumerations and locks while acquiring), exposure stored in steps of 1000 µs,
    frames of a pure red scene in the current pixel format, external trigger (``trigger()``)."""

    def __init__(self, serial="SN1", model="Fake cam", vendor="FakeVendor", flat=False):
        self.serial, self.model, self.vendor = serial, model, vendor
        self.f = dict(FEATURES)
        self.acquiring = False
        self.closed = False
        self.pending = 0
        self.flat = flat  # deliver 1-D buffers (harvesters / IDS style)
        self.writes = []

    def has(self, name):
        return name in self.f

    def get(self, name):
        if name not in self.f:
            raise KeyError(name)
        return self.f[name]

    def set(self, name, v):
        if name not in self.f:
            raise KeyError(name)
        if name in LOCKED and self.acquiring:
            raise RuntimeError(f"{name} is locked while acquiring")
        if name in ENUMS and v not in ENUMS[name]:
            raise ValueError(f"{name}: bad entry {v}")
        if name in LIMITS and not LIMITS[name][0] <= v <= LIMITS[name][1]:
            raise ValueError(f"{name}: out of range")
        if name in ("ExposureTime", "Gain") and self.f[name.replace("Time", "") + "Auto"] != "Off":
            raise RuntimeError(f"{name} is controlled by the camera")
        if name == "ExposureTime":
            v = float(round(v / 1000.0) * 1000)
        self.writes.append((name, v))
        self.f[name] = v

    def command(self, name):
        if name == "AcquisitionStart":
            self.acquiring = True
        elif name == "AcquisitionStop":
            self.acquiring = False
        elif name == "TriggerSoftware":
            self.pending += 1

    def trigger(self, n=1):
        self.pending += n

    def frame(self):
        """(data, pixel format, width, height) or None (no trigger yet)."""
        if not self.acquiring:
            return None
        if self.f["TriggerMode"] == "On":
            if self.pending <= 0:
                return None
            self.pending -= 1
        w, h, fmt = self.f["Width"], self.f["Height"], self.f["PixelFormat"]
        if fmt == "Mono8":
            a = np.full((h, w), 100, np.uint8)
        elif fmt == "Mono12":
            a = np.full((h, w), 4095, np.uint16)
        elif fmt == "RGB8":
            a = np.zeros((h, w, 3), np.uint8)
            a[:, :, 0] = 200  # red first
        else:  # BayerRG8 of a red scene: R at (0, 0) of every 2 × 2 cell
            a = np.zeros((h, w), np.uint8)
            a[0::2, 0::2] = 200
        return (a.reshape(-1) if self.flat else a), fmt, w, h


def is_red(frame) -> bool:
    b, g, r = (frame[4:-4, 4:-4, i].mean() for i in range(3))
    return r > 150 and g < 50 and b < 50


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


# ====================================================================== harvesters
def install_harvesters(monkeypatch, devices, tmp_path):
    from manymaze.core import camsources

    class Node:
        def __init__(self, dev, name):
            self.dev, self.name = dev, name

        @property
        def value(self):
            return self.dev.get(self.name)

        @value.setter
        def value(self, v):
            self.dev.set(self.name, v)

        @property
        def min(self):
            return LIMITS[self.name][0]

        @property
        def max(self):
            return LIMITS[self.name][1]

        def execute(self):
            self.dev.command(self.name)

    class NodeMap:
        def __init__(self, dev):
            self._dev = dev

        def __getattr__(self, name):
            if name.startswith("_") or not self._dev.has(name):
                raise AttributeError(name)
            return Node(self._dev, name)

    class Component:
        def __init__(self, data, fmt, w, h):
            self.data, self.data_format, self.width, self.height = data, fmt, w, h

    class Buffer:
        def __init__(self, fr):
            self.payload = types.SimpleNamespace(components=[Component(*fr)])
            self.queued = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.queued = True

    class ImageAcquirer:
        def __init__(self, dev):
            self.dev = dev
            self.remote_device = types.SimpleNamespace(node_map=NodeMap(dev))

        def start(self):
            self.dev.command("AcquisitionStart")

        def stop(self):
            self.dev.command("AcquisitionStop")

        def fetch(self, timeout=0):
            fr = self.dev.frame()
            if fr is None:
                raise TimeoutError("timeout")
            return Buffer(fr)

        def destroy(self):
            self.dev.closed = True

    class Harvester:
        def __init__(self):
            self.files = []
            self.device_info_list = []

        def add_file(self, f):
            self.files.append(f)

        def update(self):
            self.device_info_list = [types.SimpleNamespace(serial_number=d.serial, model=d.model, vendor=d.vendor)
                                     for d in devices] if self.files else []

        def create(self, i):
            return ImageAcquirer(devices[i])

        def reset(self):
            self.files = []

    for d in devices:
        d.flat = True
    monkeypatch.setitem(sys.modules, "harvesters", _module("harvesters"))
    monkeypatch.setitem(sys.modules, "harvesters.core", _module("harvesters.core", Harvester=Harvester))
    monkeypatch.delenv("GENICAM_GENTL64_PATH", raising=False)
    monkeypatch.delenv("GENICAM_GENTL32_PATH", raising=False)
    cti = tmp_path / "Vendor.cti"
    cti.write_bytes(b"")
    camsources.set_cti_files([str(cti)])
    return "genicam"


# ====================================================================== pypylon
def install_pylon(monkeypatch, devices, tmp_path=None):
    class Node:
        def __init__(self, dev, name):
            self.dev, self.name = dev, name

        @property
        def Value(self):
            return self.dev.get(self.name)

        @Value.setter
        def Value(self, v):
            self.dev.set(self.name, v)

        Min = property(lambda self: LIMITS[self.name][0])
        Max = property(lambda self: LIMITS[self.name][1])

        def Execute(self):
            self.dev.command(self.name)

    class DeviceInfo:
        def __init__(self, dev):
            self.dev = dev

        def GetSerialNumber(self):
            return self.dev.serial

        def GetModelName(self):
            return self.dev.model

        def GetVendorName(self):
            return "Basler"

    class Result:
        def __init__(self, fr):
            self.fr = fr
            self.released = False

        def IsValid(self):
            return True

        def GrabSucceeded(self):
            return self.fr is not None

        @property
        def Array(self):
            return self.fr[0]

        Width = property(lambda self: self.fr[2])
        Height = property(lambda self: self.fr[3])

        def Release(self):
            self.released = True

    class InstantCamera:
        def __init__(self, info):
            self._dev = info.dev
            self._grabbing = False

        def __getattr__(self, name):
            if name.startswith("_") or not self._dev.has(name):
                raise AttributeError(name)
            return Node(self._dev, name)

        def Open(self):
            pass

        def Close(self):
            self._dev.closed = True

        def StartGrabbing(self, strategy):
            self._grabbing = True
            self._dev.command("AcquisitionStart")

        def IsGrabbing(self):
            return self._grabbing

        def StopGrabbing(self):
            self._grabbing = False
            self._dev.command("AcquisitionStop")

        def RetrieveResult(self, ms, handling):
            return Result(self._dev.frame())

    class Factory:
        def EnumerateDevices(self):
            return [DeviceInfo(d) for d in devices]

        def CreateDevice(self, info):
            return info

    factory = Factory()
    pylon = _module("pypylon.pylon", TlFactory=types.SimpleNamespace(GetInstance=lambda: factory),
                    InstantCamera=InstantCamera, GrabStrategy_LatestImageOnly=1, TimeoutHandling_Return=2)
    monkeypatch.setitem(sys.modules, "pypylon", _module("pypylon", pylon=pylon))
    monkeypatch.setitem(sys.modules, "pypylon.pylon", pylon)
    monkeypatch.setitem(sys.modules, "pypylon.genicam", None)
    return "pylon"


# ====================================================================== PySpin
def install_spin(monkeypatch, devices, tmp_path=None):
    F, INT, B, E, S, C = range(6)

    class Node:
        def __init__(self, dev, name):
            self.dev, self.name = dev, name

        def GetPrincipalInterfaceType(self):
            v = self.dev.get(self.name)
            return E if self.name in ENUMS else B if isinstance(v, bool) else INT if isinstance(v, int) else \
                F if isinstance(v, float) else S

    class Entry:
        def __init__(self, i, sym):
            self.i, self.sym = i, sym

        def GetValue(self):
            return self.i

        def GetSymbolic(self):
            return self.sym

    class Ptr:
        def __init__(self, node):
            self.node = node

        def GetValue(self):
            return self.node.dev.get(self.node.name)

        def SetValue(self, v):
            self.node.dev.set(self.node.name, v)

        def GetMin(self):
            return LIMITS[self.node.name][0]

        def GetMax(self):
            return LIMITS[self.node.name][1]

        def GetCurrentEntry(self):
            return Entry(0, self.GetValue())

        def GetEntryByName(self, name):
            entries = ENUMS[self.node.name]
            return Entry(entries.index(name), name) if name in entries else None

        def SetIntValue(self, i):
            self.node.dev.set(self.node.name, ENUMS[self.node.name][i])

        def Execute(self):
            self.node.dev.command(self.node.name)

    class StrNode:
        def __init__(self, v):
            self.v = v

        def GetValue(self):
            return self.v

    class NodeMap:
        def __init__(self, dev):
            self.dev = dev

        def GetNode(self, name):
            return Node(self.dev, name) if self.dev.has(name) else None

    class TLMap:
        def __init__(self, dev):
            self.d = {"DeviceSerialNumber": dev.serial, "DeviceModelName": dev.model,
                      "DeviceVendorName": "FLIR"}

        def GetNode(self, name):
            return StrNode(self.d[name])

    class SpinnakerException(Exception):
        pass

    class Image:
        def __init__(self, fr):
            self.fr = fr

        def IsIncomplete(self):
            return False

        def GetNDArray(self):
            return self.fr[0]

        def GetPixelFormatName(self):
            return self.fr[1]

        def GetWidth(self):
            return self.fr[2]

        def GetHeight(self):
            return self.fr[3]

        def Release(self):
            pass

    class Camera:
        def __init__(self, dev):
            self.dev = dev

        def GetTLDeviceNodeMap(self):
            return TLMap(self.dev)

        def Init(self):
            pass

        def DeInit(self):
            self.dev.closed = True

        def GetNodeMap(self):
            return NodeMap(self.dev)

        def BeginAcquisition(self):
            self.dev.command("AcquisitionStart")

        def EndAcquisition(self):
            self.dev.command("AcquisitionStop")

        def GetNextImage(self, ms):
            fr = self.dev.frame()
            if fr is None:
                raise SpinnakerException("timeout")
            return Image(fr)

    class CamList(list):
        def Clear(self):
            self.clear()

        def GetBySerial(self, s):
            return next(c for c in self if c.dev.serial == s)

    class System:
        instances = 0

        @classmethod
        def GetInstance(cls):
            cls.instances += 1
            return cls()

        def GetCameras(self):
            return CamList(Camera(d) for d in devices)

        def ReleaseInstance(self):
            System.instances = 0

    spin = _module("PySpin", System=System, SpinnakerException=SpinnakerException, intfIFloat=F, intfIInteger=INT,
                   intfIBoolean=B, intfIEnumeration=E, intfIString=S, intfICommand=C, CFloatPtr=Ptr,
                   CIntegerPtr=Ptr, CBooleanPtr=Ptr, CEnumerationPtr=Ptr,
                   CStringPtr=lambda n: n if isinstance(n, StrNode) else Ptr(n), CCommandPtr=Ptr,
                   IsAvailable=lambda n: n is not None, IsReadable=lambda n: n is not None)
    monkeypatch.setitem(sys.modules, "PySpin", spin)
    return "spinnaker"


# ====================================================================== IDS peak
def install_ids(monkeypatch, devices, tmp_path=None):
    class Node:
        def __init__(self, dev, name):
            self.dev, self.name = dev, name

        def Value(self):
            return self.dev.get(self.name)

        def SetValue(self, v):
            self.dev.set(self.name, v)

        def Minimum(self):
            return LIMITS[self.name][0]

        def Maximum(self):
            return LIMITS[self.name][1]

        def Execute(self):
            self.dev.command(self.name)

        def WaitUntilDone(self):
            pass

    class EnumNode(Node):
        def CurrentEntry(self):
            return types.SimpleNamespace(SymbolicValue=lambda: self.dev.get(self.name))

        def SetCurrentEntry(self, v):
            self.dev.set(self.name, v)

    class NodeMap:
        def __init__(self, dev):
            self.dev = dev

        def HasNode(self, name):
            return self.dev.has(name) or name in ("AcquisitionStart", "AcquisitionStop")

        def FindNode(self, name):
            return (EnumNode if name in ENUMS else Node)(self.dev, name)

    class Stream:
        def __init__(self, dev):
            self.dev, self.buffers, self.queue = dev, [], []

        def NumBuffersAnnouncedMinRequired(self):
            return 2

        def AllocAndAnnounceBuffer(self, size):
            b = types.SimpleNamespace(size=size, fr=None)
            self.buffers.append(b)
            return b

        def QueueBuffer(self, b):
            self.queue.append(b)

        def StartAcquisition(self):
            pass

        def WaitForFinishedBuffer(self, ms):
            fr = self.dev.frame()
            if fr is None or not self.queue:
                raise RuntimeError("timeout")
            b = self.queue.pop(0)
            b.fr = fr
            return b

        def KillWait(self):
            pass

        def StopAcquisition(self, mode):
            pass

        def Flush(self, mode):
            self.queue.clear()

        def AnnouncedBuffers(self):
            return list(self.buffers)

        def RevokeBuffer(self, b):
            self.buffers.remove(b)

    class Device:
        def __init__(self, dev):
            self.dev = dev

        def RemoteDevice(self):
            return types.SimpleNamespace(NodeMaps=lambda: [NodeMap(self.dev)])

        def DataStreams(self):
            return [types.SimpleNamespace(OpenDataStream=lambda: Stream(self.dev))]

    class Descriptor:
        def __init__(self, dev):
            self.dev = dev

        def SerialNumber(self):
            return self.dev.serial

        def ModelName(self):
            return self.dev.model

        def OpenDevice(self, access):
            return Device(self.dev)

    class Library:
        users = 0

        @classmethod
        def Initialize(cls):
            cls.users += 1

        @classmethod
        def Close(cls):
            cls.users -= 1
            for d in devices:  # devices are closed with the library
                d.closed = True

    class DeviceManager:
        _inst = None

        @classmethod
        def Instance(cls):
            cls._inst = cls._inst or cls()
            return cls._inst

        def Update(self):
            pass

        def Devices(self):
            return [Descriptor(d) for d in devices]

    class Image:
        def __init__(self, fr):
            self.fr = fr

        def get_numpy_1D(self):
            return self.fr[0]

        def PixelFormat(self):
            return types.SimpleNamespace(Name=lambda: self.fr[1])

        def Width(self):
            return self.fr[2]

        def Height(self):
            return self.fr[3]

    for d in devices:
        d.flat = True
    peak = _module("ids_peak.ids_peak", Library=Library, DeviceManager=DeviceManager, DeviceAccessType_Control=1,
                   AcquisitionStopMode_Default=0, DataStreamFlushMode_DiscardAll=0)
    ext = _module("ids_peak.ids_peak_ipl_extension", BufferToImage=lambda b: Image(b.fr))
    monkeypatch.setitem(sys.modules, "ids_peak", _module("ids_peak", ids_peak=peak, ids_peak_ipl_extension=ext))
    monkeypatch.setitem(sys.modules, "ids_peak.ids_peak", peak)
    monkeypatch.setitem(sys.modules, "ids_peak.ids_peak_ipl_extension", ext)
    return "ids"


INSTALLERS = {"genicam": install_harvesters, "pylon": install_pylon, "spinnaker": install_spin, "ids": install_ids}
MODULES = ("harvesters", "harvesters.core", "pypylon", "pypylon.pylon", "pypylon.genicam", "PySpin", "ids_peak",
           "ids_peak.ids_peak", "ids_peak.ids_peak_ipl_extension")


def no_sdks(monkeypatch):
    """Make every SDK import fail (as on a machine without them)."""
    for m in MODULES:
        monkeypatch.setitem(sys.modules, m, None)

