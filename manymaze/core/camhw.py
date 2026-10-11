"""Camera hardware settings: exposure, gain, brightness, contrast, saturation, white balance, focus (each with its
"auto" mode where the camera has one) and, for GenICam cameras, pixel format and external triggering.

The settings are stored per camera with the other camera options (``project.settings_extra["cameras"][key]
["hardware"]``) as a :class:`CameraHardware`; a value of None leaves the camera's own setting alone.  They are
applied when the camera opens and can be changed while it runs.  Two appliers implement them:

* :class:`OpenCVControls` — ``cv2.CAP_PROP_*`` properties of an OpenCV camera (webcams, UVC cameras, analogue
  capture cards).  Units and ranges are those of the driver, and many drivers ignore some properties (OpenCV's
  AVFoundation backend on macOS supports almost none), so every value is read back after it is set and the result
  is reported: applied, adjusted by the camera, or not supported.
* :class:`GenICamControls` — standard GenICam feature names (ExposureTime / ExposureAuto, Gain / GainAuto,
  BalanceWhiteAuto, AcquisitionFrameRate, PixelFormat, TriggerMode …) of an industrial camera, through a small
  node-map adapter provided by each SDK backend of :mod:`core.camsources`.

Nothing here raises because a camera lacks a setting: unsupported settings are reported, never fatal.
"""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass, fields

import cv2
import numpy as np

# report statuses of one setting
OK, UNSUPPORTED, ADJUSTED = "ok", "unsupported", "adjusted"


@dataclass
class CameraHardware:
    """Hardware settings of one camera; None = leave the camera's own setting.  Values are in the camera's units
    (driver units for OpenCV cameras; µs of exposure and dB of gain for most GenICam cameras)."""

    exposure: float | None = None
    auto_exposure: bool | None = None
    gain: float | None = None
    auto_gain: bool | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    white_balance: float | None = None  # colour temperature (K)
    auto_white_balance: bool | None = None
    focus: float | None = None
    auto_focus: bool | None = None
    pixel_format: str | None = None  # GenICam: Mono8, BayerRG8, RGB8 …
    trigger: bool | None = None  # GenICam: one frame per external trigger
    trigger_source: str | None = None  # GenICam: Line0, Line1 …

    def to_dict(self) -> dict:
        """Only the settings that are set (an empty dict for camera defaults)."""
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None}

    @classmethod
    def from_dict(cls, d: dict | None) -> "CameraHardware":
        """Tolerant of missing, unknown and malformed entries (old projects, hand-edited files)."""
        hw = cls()
        for f in fields(cls):
            v = (d or {}).get(f.name) if isinstance(d, dict) else None
            if v is None:
                continue
            try:
                if f.name in _BOOLS:
                    v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
                elif f.name in _STRINGS:
                    v = str(v).strip() or None
                else:
                    v = float(v)
                    if not math.isfinite(v):
                        continue
            except (TypeError, ValueError):
                continue
            setattr(hw, f.name, v)
        return hw

    @property
    def is_empty(self) -> bool:
        return not self.to_dict()

    def merged(self, changes: dict) -> "CameraHardware":
        """A copy with ``changes`` applied (a None value clears a setting)."""
        d = self.to_dict()
        d.update(changes)
        return CameraHardware.from_dict({k: v for k, v in d.items() if v is not None})


_BOOLS = {"auto_exposure", "auto_gain", "auto_white_balance", "auto_focus", "trigger"}
_STRINGS = {"pixel_format", "trigger_source"}


@dataclass(frozen=True)
class Control:
    """A value setting, its auto switch, its OpenCV property and GenICam features (first one present is used)."""

    name: str
    label: str
    auto: str | None
    cv_prop: int | None
    features: tuple
    auto_cv_prop: int | None = None
    auto_features: tuple = ()
    range: tuple = (0.0, 255.0)  # default range shown when the camera does not tell
    decimals: int = 0


CONTROLS = (
    Control("exposure", "Exposure", "auto_exposure", cv2.CAP_PROP_EXPOSURE,
            ("ExposureTime", "ExposureTimeAbs", "ExposureTimeRaw"), cv2.CAP_PROP_AUTO_EXPOSURE, ("ExposureAuto",),
            (-13.0, 10000.0), 2),
    Control("gain", "Gain", "auto_gain", cv2.CAP_PROP_GAIN, ("Gain", "GainAbs", "GainRaw"), None, ("GainAuto",),
            (0.0, 255.0), 2),
    Control("brightness", "Brightness", None, cv2.CAP_PROP_BRIGHTNESS, ("BlackLevel", "BlackLevelAbs",
                                                                         "BlackLevelRaw", "Brightness")),
    Control("contrast", "Contrast", None, cv2.CAP_PROP_CONTRAST, ("Contrast",)),
    Control("saturation", "Saturation", None, cv2.CAP_PROP_SATURATION, ("Saturation", "SaturationAbs"),
            decimals=2),
    Control("white_balance", "White balance", "auto_white_balance", cv2.CAP_PROP_WB_TEMPERATURE,
            ("ColorTemperature", "WhiteBalanceTemperature"), cv2.CAP_PROP_AUTO_WB, ("BalanceWhiteAuto",),
            (2000.0, 10000.0)),
    Control("focus", "Focus", "auto_focus", cv2.CAP_PROP_FOCUS, ("FocusPos", "Focus"), cv2.CAP_PROP_AUTOFOCUS,
            ("FocusAuto",)),
)
CONTROL_BY_NAME = {c.name: c for c in CONTROLS}
AUTO_OF = {c.auto: c for c in CONTROLS if c.auto}
PIXEL_FORMATS = ("Mono8", "BayerRG8", "BayerBG8", "BayerGR8", "BayerGB8", "RGB8", "BGR8")


@dataclass
class ControlInfo:
    """What the dialog shows for one control of an open camera.  supported: True / False / None (unknown until
    tried: OpenCV drivers do not say)."""

    name: str
    label: str
    value: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    supported: bool | None = None
    auto: bool | None = None  # current auto state (None = no auto mode / unknown)
    has_auto: bool = False


def describe_report(report: dict) -> str:
    """Human summary of an apply report {setting: "ok" | "unsupported" | "adjusted:<value>"}."""
    if not report:
        return ""

    def label(k):
        return (CONTROL_BY_NAME[k].label if k in CONTROL_BY_NAME else
                ("Auto " + AUTO_OF[k].label.lower() if k in AUTO_OF else k.replace("_", " ").capitalize()))

    ok = [label(k) for k, v in report.items() if v == OK]
    bad = [label(k) for k, v in report.items() if v == UNSUPPORTED]
    adj = [f"{label(k)} ({v.split(':', 1)[1]})" for k, v in report.items() if str(v).startswith(ADJUSTED)]
    parts = []
    if ok:
        parts.append("Applied: " + ", ".join(ok) + ".")
    if adj:
        parts.append("Adjusted by the camera: " + ", ".join(adj) + ".")
    if bad:
        parts.append("Not supported by this camera / driver: " + ", ".join(bad) + ".")
    return " ".join(parts)


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= max(1e-3, 0.01 * abs(float(a)))


def _order(hw: CameraHardware):
    """(setting, value) in application order: auto switches first (a manual value is refused while its auto mode
    is on), then the values whose auto mode is not requested on."""
    d = hw.to_dict()
    for c in CONTROLS:
        if c.auto and c.auto in d:
            yield c.auto, d[c.auto]
    for c in CONTROLS:
        if c.name in d and not (c.auto and d.get(c.auto) is True):
            yield c.name, d[c.name]


# ====================================================================== OpenCV cameras
class OpenCVControls:
    """Hardware settings of a cv2.VideoCapture camera.  ``lock`` serialises property access with frame reading
    (settings can change from the GUI thread while the reader thread grabs)."""

    def __init__(self, cap, lock=None, platform: str = sys.platform):
        self.cap = cap
        self.lock = lock or threading.RLock()
        self.platform = platform
        try:
            self.backend = str(cap.getBackendName())
        except Exception:
            self.backend = ""
        self.supported: dict[str, bool] = {}
        self.defaults = self.current()

    # auto exposure values: V4L2 uses its menu (1 manual, 3 aperture priority), DirectShow / MSMF 0.25 / 0.75
    def _auto_value(self, name: str, on: bool) -> float:
        if name == "auto_exposure":
            if self.platform.startswith("linux"):
                return 3.0 if on else 1.0
            return 0.75 if on else 0.25
        return 1.0 if on else 0.0

    def _auto_state(self, name: str, v: float) -> bool:
        if name == "auto_exposure" and self.platform.startswith("linux"):
            return int(round(v)) != 1
        return v > 0.5

    def _get(self, prop) -> float | None:
        try:
            with self.lock:
                v = float(self.cap.get(prop))
        except Exception:
            return None
        return v if math.isfinite(v) and v != -1 else None

    def current(self) -> CameraHardware:
        """The camera's current settings (those the driver reports)."""
        d = {}
        for c in CONTROLS:
            v = self._get(c.cv_prop) if c.cv_prop is not None else None
            if v is not None:
                d[c.name] = v
            if c.auto_cv_prop is not None:
                a = self._get(c.auto_cv_prop)
                if a is not None:
                    d[c.auto] = self._auto_state(c.auto, a)
        return CameraHardware.from_dict(d)

    def info(self) -> list[ControlInfo]:
        cur = self.current().to_dict()
        out = []
        for c in CONTROLS:
            if c.cv_prop is None:
                continue
            sup = self.supported.get(c.name)
            if sup is None and c.name not in cur:
                sup = False if self.backend.upper() != "AVFOUNDATION" else None
            out.append(ControlInfo(c.name, c.label, cur.get(c.name), c.range[0], c.range[1], sup,
                                   cur.get(c.auto) if c.auto_cv_prop is not None else None,
                                   c.auto_cv_prop is not None and self.supported.get(c.auto) is not False))
        return out

    def _set(self, name: str, value) -> str:
        c = CONTROL_BY_NAME.get(name) or AUTO_OF.get(name)
        if c is None:
            return UNSUPPORTED
        is_auto = name == c.auto
        prop = c.auto_cv_prop if is_auto else c.cv_prop
        if prop is None:
            self.supported[name] = False
            return UNSUPPORTED
        v = self._auto_value(name, bool(value)) if is_auto else float(value)
        try:
            with self.lock:
                ok = bool(self.cap.set(prop, v))
        except Exception:
            ok = False
        self.supported[name] = ok
        if not ok:
            return UNSUPPORTED
        back = self._get(prop)
        if back is None:
            return OK  # accepted, not readable back
        if is_auto:
            return OK if self._auto_state(name, back) == bool(value) else f"{ADJUSTED}:{'on' if not value else 'off'}"
        return OK if _close(v, back) else f"{ADJUSTED}:{back:g}"

    def apply(self, hw: CameraHardware) -> dict:
        """Set every setting of ``hw``; returns {setting: status}."""
        return {name: self._set(name, v) for name, v in _order(hw) if name in CONTROL_BY_NAME or name in AUTO_OF}

    def reset(self) -> dict:
        """Back to the settings the camera had when it opened."""
        return self.apply(self.defaults)


# ====================================================================== GenICam cameras
class GenICamControls:
    """Hardware settings of a GenICam camera through a node-map adapter with ``has(name)``, ``get(name)``,
    ``set(name, value)``, ``limits(name) -> (min, max) | None`` and ``execute(name)`` (see core.camsources)."""

    def __init__(self, nodes, lock=None):
        self.nodes = nodes
        self.lock = lock or threading.RLock()
        self.defaults = self.current()

    def _has(self, name: str) -> bool:
        try:
            return bool(self.nodes.has(name))
        except Exception:
            return False

    def _first(self, names) -> str | None:
        return next((n for n in names if self._has(n)), None)

    def _get(self, name):
        try:
            with self.lock:
                return self.nodes.get(name)
        except Exception:
            return None

    def _limits(self, name):
        try:
            with self.lock:
                lim = self.nodes.limits(name)
            return (float(lim[0]), float(lim[1])) if lim else None
        except Exception:
            return None

    def _write(self, name: str, value) -> bool:
        try:
            with self.lock:
                self.nodes.set(name, value)
            return True
        except Exception:
            return False

    @staticmethod
    def _auto_on(v) -> bool | None:
        return None if v is None else str(v).strip().lower() not in ("off", "0", "false")

    def current(self) -> CameraHardware:
        d = {}
        for c in CONTROLS:
            f = self._first(c.features)
            v = self._get(f) if f else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                d[c.name] = float(v)
            a = self._first(c.auto_features)
            if a:
                st = self._auto_on(self._get(a))
                if st is not None:
                    d[c.auto] = st
        if self._has("PixelFormat"):
            d["pixel_format"] = self._get("PixelFormat")
        if self._has("TriggerMode"):
            d["trigger"] = self._auto_on(self._get("TriggerMode"))
        if self._has("TriggerSource"):
            d["trigger_source"] = self._get("TriggerSource")
        return CameraHardware.from_dict(d)

    def info(self) -> list[ControlInfo]:
        cur = self.current().to_dict()
        out = []
        for c in CONTROLS:
            f = self._first(c.features)
            lim = self._limits(f) if f else None
            a = self._first(c.auto_features)
            out.append(ControlInfo(c.name, c.label, cur.get(c.name), lim[0] if lim else c.range[0],
                                   lim[1] if lim else c.range[1], f is not None, cur.get(c.auto) if a else None,
                                   a is not None))
        return out

    def _set_value(self, c: Control, value: float) -> str:
        f = self._first(c.features)
        if f is None:
            return UNSUPPORTED
        a = self._first(c.auto_features)
        if a and self._auto_on(self._get(a)):
            self._write(a, "Off")  # a manual value needs the auto mode off
        lim = self._limits(f)
        v = float(value)
        if lim:
            v = min(max(v, lim[0]), lim[1])
        cur = self._get(f)
        if isinstance(cur, int) and not isinstance(cur, bool):
            v = int(round(v))  # integer features (…Raw)
        if not self._write(f, v):
            return UNSUPPORTED
        back = self._get(f)
        if back is None or _close(float(value), float(back)):
            return OK
        return f"{ADJUSTED}:{float(back):g}"

    def _set_auto(self, c: Control, on: bool) -> str:
        a = self._first(c.auto_features)
        if a is None:
            return UNSUPPORTED
        if not self._write(a, "Continuous" if on else "Off"):
            return UNSUPPORTED
        st = self._auto_on(self._get(a))
        return OK if st is None or st == on else f"{ADJUSTED}:{'on' if st else 'off'}"

    def _set_enum(self, name: str, value) -> str:
        if not self._has(name) or not self._write(name, value):
            return UNSUPPORTED
        back = self._get(name)
        return OK if back is None or str(back) == str(value) else f"{ADJUSTED}:{back}"

    def apply(self, hw: CameraHardware) -> dict:
        report = {}
        d = hw.to_dict()
        if "pixel_format" in d:
            report["pixel_format"] = self._set_enum("PixelFormat", d["pixel_format"])
        for name, v in _order(hw):
            c = CONTROL_BY_NAME.get(name)
            report[name] = self._set_value(c, v) if c is not None else self._set_auto(AUTO_OF[name], bool(v))
        if "trigger" in d or "trigger_source" in d:
            report.update(self.set_trigger(d.get("trigger"), d.get("trigger_source")))
        return report

    def set_trigger(self, on: bool | None, source: str | None = None) -> dict:
        """External trigger of every frame (TriggerSelector FrameStart, TriggerMode, TriggerSource)."""
        report = {}
        if self._has("TriggerSelector"):
            self._write("TriggerSelector", "FrameStart")
        if source:
            report["trigger_source"] = self._set_enum("TriggerSource", source)
        if on is not None:
            report["trigger"] = self._set_enum("TriggerMode", "On" if on else "Off")
            if on and self._has("TriggerActivation"):
                self._write("TriggerActivation", "RisingEdge")
        return report

    def apply_format(self, width=None, height=None, fps=None):
        """Image size and frame rate requested for the camera (best effort)."""
        for name, v in (("Width", width), ("Height", height)):
            if v and self._has(name):
                lim = self._limits(name)
                self._write(name, int(min(max(int(v), lim[0]), lim[1])) if lim else int(v))
        if fps:
            if self._has("AcquisitionFrameRateEnable"):
                self._write("AcquisitionFrameRateEnable", True)
            f = self._first(("AcquisitionFrameRate", "AcquisitionFrameRateAbs"))
            if f:
                lim = self._limits(f)
                self._write(f, float(min(max(float(fps), lim[0]), lim[1])) if lim else float(fps))

    @property
    def triggered(self) -> bool:
        return bool(self._has("TriggerMode") and self._auto_on(self._get("TriggerMode")))

    def frame_rate(self) -> float | None:
        for f in ("ResultingFrameRate", "ResultingFrameRateAbs", "AcquisitionFrameRate", "AcquisitionFrameRateAbs"):
            if self._has(f):
                v = self._get(f)
                if isinstance(v, (int, float)) and 0 < v < 10000:
                    return float(v)
        return None

    def reset(self) -> dict:
        return self.apply(self.defaults)


# ====================================================================== pixel formats
_BAYER = {"BAYERRG": cv2.COLOR_BayerBG2BGR, "BAYERBG": cv2.COLOR_BayerRG2BGR,  # OpenCV names the pattern from the
          "BAYERGR": cv2.COLOR_BayerGB2BGR, "BAYERGB": cv2.COLOR_BayerGR2BGR}  # second row: RGGB is its "BG"


class UnsupportedPixelFormat(ValueError):
    """The camera delivers a pixel format that cannot be converted: choosing another format is the only remedy
    (reopening the camera — what a stalled camera gets — cannot help)."""


SUPPORTED_PIXEL_FORMATS = ("Mono8", "Mono10", "Mono12", "Mono14", "Mono16", "BayerRG8", "BayerBG8", "BayerGR8",
                           "BayerGB8", "BayerRG10/12/16 (and BG, GR, GB)", "RGB8", "BGR8", "RGB10/12/16",
                           "BGR10/12/16", "RGBa8", "BGRa8", "YUV422_8 (YUYV / UYVY)")


def _unsupported(pixel_format, why: str = "") -> UnsupportedPixelFormat:
    return UnsupportedPixelFormat(
        f"The camera's pixel format {pixel_format!r} is not supported{(' (' + why + ')') if why else ''}. Choose "
        f"Mono8, a Bayer 8-bit, RGB8 or BGR8 format in the camera options (supported: "
        f"{', '.join(SUPPORTED_PIXEL_FORMATS)}).")


def to_bgr(data: np.ndarray, pixel_format: str = "Mono8", width: int | None = None,
           height: int | None = None) -> np.ndarray:
    """A camera buffer (GenICam PixelFormat name) as an 8-bit BGR image like OpenCV cameras deliver.

    Handles Mono8/10/12/14/16, Bayer RG/BG/GR/GB 8/10/12/16, RGB / BGR 8/10/12/16, RGBa8 / BGRa8 and YUV 4:2:2
    (YUYV / UYVY); 10–16-bit data (one value per uint16) is scaled to 8 bits.  Bit-packed 10/12-bit formats
    (Mono12p, Mono10Packed, BayerRG12p …) and other formats raise :class:`UnsupportedPixelFormat` with a message
    saying so, as does a buffer whose size does not match the format and image size."""
    raw = str(pixel_format or "Mono8")
    fmt = raw.replace("_", "").upper()
    packed8 = fmt.replace("PACKED", "")
    if ("PACKED" in fmt and not packed8.endswith("8") and not packed8.startswith(("YUV", "YCBCR"))) or \
            (fmt.endswith("P") and fmt[-2:-1].isdigit()):
        raise _unsupported(raw, "bit-packed pixels")
    fmt = packed8
    a = np.asarray(data)
    if fmt.startswith("YUV422") or fmt.startswith("YCBCR422") or fmt in ("YUYV", "UYVY"):
        uyvy = fmt in ("UYVY", "YUV422") or fmt.endswith("UYVY") or fmt.endswith("CBYCRY")
        if width and height:
            if a.size != height * width * 2:
                raise _unsupported(raw, f"{a.size} bytes for a {width}×{height} image")
            a = a.reshape(height, width, 2)
        return cv2.cvtColor(np.ascontiguousarray(a.astype(np.uint8)),
                            cv2.COLOR_YUV2BGR_UYVY if uyvy else cv2.COLOR_YUV2BGR_YUYV)
    if not fmt.startswith(("MONO", "BAYER", "RGB", "BGR")):
        raise _unsupported(raw)
    bits = next((b for b in (16, 14, 12, 10) if fmt.endswith(str(b))), 8)
    if not fmt.endswith(("8", "10", "12", "14", "16")):
        raise _unsupported(raw)
    if fmt.startswith("BAYER") and _BAYER.get(fmt[:7]) is None:
        raise _unsupported(raw)
    channels = 4 if fmt.startswith(("RGBA", "BGRA")) else 3 if fmt.startswith(("RGB", "BGR")) else 1
    if channels == 4 and bits != 8:
        raise _unsupported(raw)
    if width and height:
        if a.size != height * width * channels:
            raise _unsupported(raw, f"{a.size} values for a {width}×{height} image")
        a = a.reshape((height, width) if channels == 1 else (height, width, channels))
    elif a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    if a.dtype != np.uint8:
        a = (a.astype(np.uint32) >> max(0, bits - 8)).clip(0, 255).astype(np.uint8)
    a = np.ascontiguousarray(a)
    if fmt.startswith("BAYER"):
        code = _BAYER.get(fmt[:7])
        return cv2.cvtColor(a, code) if code is not None else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    if channels == 4:
        return cv2.cvtColor(a, cv2.COLOR_RGBA2BGR if fmt.startswith("RGB") else cv2.COLOR_BGRA2BGR)
    if channels == 3:
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if fmt.startswith("RGB") else a
    return cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
