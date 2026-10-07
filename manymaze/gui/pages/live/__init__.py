"""Run tests page (ANY-maze's Test page while testing): track animals in real time from cameras (or video files
simulating them), each test in a panel with its own toolbar, title, camera image and Session log / Video / Zones tabs.

Three modes: one test; several tests at once (several cameras and / or several apparatus in one camera image,
panels in a grid with collective start / pause / stop); observation only (TakeNote: no camera, a clock and keys).
"""

from .common import VIEW_DEFAULTS
from .page import LivePage

__all__ = ["LivePage", "VIEW_DEFAULTS"]
