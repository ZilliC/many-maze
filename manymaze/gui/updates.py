"""Help ▸ Check for updates: the GitHub release check of core.updates in a background thread, and the optional
check at startup (off by default, at most once a week, remembered in the settings)."""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from .. import APP_NAME
from ..core import updates
from .widgets import Worker

STARTUP_KEY = "updates/check_at_startup"
LAST_CHECK_KEY = "updates/last_check"


def _flag(settings, key: str, default: bool = False) -> bool:
    v = settings.value(key, default)
    return v in (True, "true", "1", 1) if not isinstance(v, bool) else v


def startup_check_enabled(settings) -> bool:
    return _flag(settings, STARTUP_KEY, False)


def set_startup_check(settings, on: bool):
    settings.setValue(STARTUP_KEY, bool(on))


def check_updates(window, silent: bool = False, checker=None) -> Worker | None:
    """Check in the background; then offer to open the release page when a newer version exists. silent (the check
    at startup): say nothing unless there is a newer version. checker(): core.updates.check_for_updates (tests)."""
    if getattr(window, "_update_worker", None) is not None:
        return None
    checker = checker or updates.check_for_updates
    if not silent:
        window.status("Checking for updates…")
    w = Worker(lambda progress, stop: checker(), window)
    window._update_worker = w

    def done(info):
        window._update_worker = None
        window.settings.setValue(LAST_CHECK_KEY, _dt.datetime.now().isoformat(timespec="seconds"))
        window.last_update_info = info
        show_update_result(window, info, silent)

    def failed(msg):
        window._update_worker = None
        if not silent:
            QMessageBox.warning(window, "Check for updates", f"Could not check for updates: {msg}")

    w.signals.done.connect(done)
    w.signals.failed.connect(failed)
    w.finished.connect(w.deleteLater)
    w.start()
    return w


def show_update_result(window, info, silent: bool = False):
    if info.newer:
        window.status(info.message())
        box = QMessageBox(QMessageBox.Information, "Update available",
                          f"<b>{APP_NAME} {info.latest}</b> is available — you have {info.current}.",
                          QMessageBox.Open | QMessageBox.Close, window)
        box.setInformativeText("Open the release page to download it?")
        if info.notes:
            box.setDetailedText(info.notes[:4000])
        box.setDefaultButton(QMessageBox.Open)
        if box.exec() == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl(info.url))
    elif not silent:
        window.status(info.message())
        if info.ok:
            QMessageBox.information(window, "Check for updates", info.message())
        else:
            QMessageBox.warning(window, "Check for updates", info.message())


def startup_check(window, checker=None) -> Worker | None:
    """At startup: check silently if the user turned it on and the last check is a week old."""
    s = window.settings
    if not startup_check_enabled(s) or not updates.check_due(s.value(LAST_CHECK_KEY, None)):
        return None
    return check_updates(window, silent=True, checker=checker)
