"""Procedure plug-ins: Python functions the "Trigger a plug-in" action calls during a live test.

A plug-in is ``fn(argument: str, info: dict) -> value``. ``info`` holds the test time (``t``), a copy of the
procedure variables (``variables``), the test context (``context``: zones, test, animal …) and ``log(text)``.
The value it returns (a number, text or list) can be stored in a variable; exceptions become procedure errors.
Plug-ins run in the frame thread: they must return quickly (start a thread for slow work).

Register one in code::

    from manymaze.core.procedures import plugins
    plugins.register("reward_server", lambda arg, info: notify_server(arg))

or from an installed package through the entry-point group ``manymaze.procedure_plugins`` (name = plug-in name,
object = the function)."""

from __future__ import annotations

from collections.abc import Callable

ENTRY_POINT_GROUP = "manymaze.procedure_plugins"
_PLUGINS: dict[str, Callable] = {}
_loaded = False


def register(name: str, fn: Callable) -> None:
    """Make ``fn(argument, info)`` available to procedures as the plug-in ``name``."""
    if not callable(fn):
        raise TypeError("a plug-in must be callable")
    _PLUGINS[str(name)] = fn


def unregister(name: str) -> None:
    _PLUGINS.pop(str(name), None)


def _load_entry_points():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if ep.name not in _PLUGINS:
                try:
                    _PLUGINS[ep.name] = ep.load()
                except Exception:  # pragma: no cover - a broken third-party package
                    pass
    except Exception:  # pragma: no cover
        pass


def get(name: str) -> Callable | None:
    _load_entry_points()
    return _PLUGINS.get(str(name))


def names() -> list[str]:
    """The registered plug-ins (for the procedure editor)."""
    _load_entry_points()
    return sorted(_PLUGINS)
