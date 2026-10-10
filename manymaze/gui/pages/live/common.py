"""Constants and helpers of the Run tests page."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np

from ....core.camera import CameraView, merge_layout
from ....core.camhw import CONTROL_BY_NAME
from ....core.camsources import is_native_source
from ....core.lens import lens_from
from ....core.video import VideoRecorder, VideoSource

RESOLUTIONS = [("Camera default", None), ("640 × 480", (640, 480)), ("800 × 600", (800, 600)),
               ("1280 × 720", (1280, 720)), ("1920 × 1080", (1920, 1080))]
START_MODES = [("immediate", "Immediately when armed"), ("on_detection", "When the animal is detected"),
               ("experimenter_leaves", "When the experimenter leaves the view"),
               ("manual", "On a start key (keyboard / remote)"), ("scheduled", "At a clock time")]
MODES = [("single", "One test"), ("multi", "Several tests"), ("observe", "Observation only")]
MODE_ACTIONS = [("single", "One test", "video", "Run one test: one camera, one apparatus."),
                ("multi", "Several tests", "grid", "Run several tests at once: several cameras and / or several "
                 "apparatus in one camera image, each in its own panel."),
                ("observe", "Observation only", "observe", "TakeNote: score behaviour by direct observation, "
                 "without a camera.")]
_KEY_NAMES = {"pagedown": "PgDown", "pgdown": "PgDown", "pagedn": "PgDown", "pageup": "PgUp", "pgup": "PgUp",
              "space": "Space", "esc": "Esc", "escape": "Esc", "enter": "Return", "return": "Return"}


def qt_key(name: str) -> str:
    """Key name as typed by the user (Space, PageDown, F5, B…) → QKeySequence text."""
    n = name.strip()
    return _KEY_NAMES.get(n.lower(), n.upper() if len(n) == 1 else n)


def parse_keys(text: str) -> list[str]:
    return [k.strip() for k in re.split(r"[,;]", text or "") if k.strip()]


def serial_ports() -> list[str] | None:
    """Available serial ports, or None when pyserial is not installed."""
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None
    try:
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def recording_path(project, test, size=(640, 480), fps=25.0) -> str:
    """test_<id>_<animal>.mp4 in the recordings folder, or .avi if no mp4 writer is available."""
    safe = re.sub(r"[^\w.-]+", "_", test.animal_id or "animal")
    base = project.recordings_dir() / f"test_{test.id:04d}_{safe}"
    probe = project.recordings_dir() / f".probe_{threading.get_ident()}.mp4"
    try:
        VideoRecorder(str(probe), fps, size).close()
        return str(base.with_suffix(".mp4"))
    except Exception:
        return str(base.with_suffix(".avi"))
    finally:
        if probe.exists():
            probe.unlink()


TRAIL_LEN = 250  # positions drawn behind the animal on the camera images
VIEW_DEFAULTS = {"layout": "2x2", "fit": True, "trail": True, "beam": True, "zones": True, "labels": False,
                 "hide_report": False, "hide_apparatus": False, "panel": {}}


def peek_frame(path) -> np.ndarray | None:
    try:
        with VideoSource(path) as v:
            ok, f = v.read()
            return f if ok else None
    except Exception:
        return None


def describe_view(view: CameraView, second, layout: str, hardware=None, undistort=None) -> str:
    """One line describing the camera options (``second``: the merged source, or the list of merged sources)."""
    parts = []
    merged = [m for m in (second if isinstance(second, (list, tuple)) else [second]) if m is not None]
    if undistort and lens_from(undistort) is not None:
        parts.append("lens corrected")
    if merged:
        names = [m if not isinstance(m, str) or is_native_source(m) else Path(m).name for m in merged]
        how = {"side": "side by side", "stack": "stacked", "grid": "in a grid"}.get(merge_layout(layout), "")
        parts.append(f"merged with {', '.join(str(n) for n in names)} ({how})")
    if view.rotate:
        parts.append(f"rotated {view.rotate}°")
    if view.flip:
        parts.append({"h": "mirrored", "v": "upside down"}.get(view.flip, "flipped"))
    if view.crop:
        parts.append(f"region {view.crop[2]}×{view.crop[3]}")
    if view.zoom > 1:
        parts.append(f"zoom {view.zoom:g}×")
    if hardware is not None and not hardware.is_empty:
        names = [CONTROL_BY_NAME[k].label.lower() for k in hardware.to_dict() if k in CONTROL_BY_NAME]
        parts.append("camera settings" + (f" ({', '.join(names)})" if names else ""))
    return ", ".join(parts) if parts else "Whole image"


def camera_options_settings(view: CameraView, merge: list, layout: str, hardware, undistort: dict | None) -> dict:
    """The saved options of a camera (core.camera.camera_settings): only what differs from the defaults; a montage
    keeps ``second`` (read by older versions) and, with more than two cameras, ``merge``."""
    settings = {}
    if not view.is_identity:
        settings["view"] = view.to_dict()
    if merge:
        settings.update(second=merge[0], layout=merge_layout(layout))
        if len(merge) > 1:
            settings["merge"] = list(merge)
    if hardware is not None and not hardware.is_empty:
        settings["hardware"] = hardware.to_dict()
    if undistort and lens_from(undistort) is not None:
        settings["undistort"] = dict(undistort)
    return settings
