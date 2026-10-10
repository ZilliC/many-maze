"""Procedure plug-ins: Python functions the "Trigger a plug-in" action calls during a live test. They now live in
:mod:`manymaze.core.plugins` with the analysis plug-ins; this module keeps the names it always offered::

    from manymaze.core.procedures import plugins
    plugins.register("reward_server", lambda arg, info: notify_server(arg))
"""

from __future__ import annotations

from ..plugins import _PLUGINS, ENTRY_POINT_GROUP, get, names, register, unregister

__all__ = ["ENTRY_POINT_GROUP", "_PLUGINS", "get", "names", "register", "unregister"]
