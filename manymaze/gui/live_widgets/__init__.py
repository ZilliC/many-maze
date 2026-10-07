"""Widgets of the Run tests page: ANY-maze style test panels (toolbar, title, camera image with the apparatus,
time slider, Session log / Video / Zones tabs) laid out in a grid, the real-time monitor (zone statistics, live
chart, I/O status, warnings), the camera options dialog and the observation-only (TakeNote) panel."""

from .camera_options import CameraOptionsDialog
from .monitor import LiveChart, MonitorPanel
from .observation import ObservationPanel
from .panels import LAYOUT_LABELS, LAYOUTS, LiveView, PanelGrid, PanelSettingsDialog, TestPanel, short_time

__all__ = ["CameraOptionsDialog", "LAYOUT_LABELS", "LAYOUTS", "LiveChart", "LiveView", "MonitorPanel",
           "ObservationPanel", "PanelGrid", "PanelSettingsDialog", "TestPanel", "short_time"]
