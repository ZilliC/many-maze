"""Widgets of the Run tests page: ANY-maze style test panels (toolbar, title, camera image with the apparatus,
time slider, Session log / Video / Zones tabs) laid out in a grid, the real-time monitor (zone statistics, live
chart, I/O status, warnings), the camera options (with camera hardware settings), lens correction and industrial
cameras dialogs and the observation-only (TakeNote) panel."""

from .camera_options import CameraOptionsDialog, HardwarePanel, IndustrialCamerasDialog
from .lens import LensCorrectionDialog, lens_text
from .monitor import LiveChart, MonitorPanel
from .observation import ObservationPanel
from .panels import LAYOUT_LABELS, LAYOUTS, LiveView, PanelGrid, PanelSettingsDialog, TestPanel, short_time

__all__ = ["CameraOptionsDialog", "HardwarePanel", "IndustrialCamerasDialog", "LAYOUT_LABELS", "LAYOUTS",
           "LensCorrectionDialog", "LiveChart", "LiveView", "MonitorPanel", "ObservationPanel", "PanelGrid",
           "PanelSettingsDialog", "TestPanel", "lens_text", "short_time"]
