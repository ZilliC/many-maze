"""Experiment lock: a small file in the experiment folder saying which program (computer, process, user) has the
experiment open, so a second mANY-MAZE window, the command line or another computer sharing the folder does not
overwrite its work.

The lock is advisory: it is written when an experiment is opened and removed when it is closed. A lock whose
process is no longer running on this computer is stale and is ignored (taken over). A lock written by another
computer cannot be checked and counts as live; the user can still open the experiment anyway.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import socket
from pathlib import Path

LOCK_FILE = ".manymaze.lock"


def _host() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover
        return ""


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no user name in the environment
        return ""


def me() -> dict:
    """This process as a lock owner: {"host", "pid", "user", "since"}."""
    return {"host": _host(), "pid": os.getpid(), "user": _user(),
            "since": _dt.datetime.now().isoformat(timespec="seconds")}


def lock_path(folder) -> Path:
    return Path(folder) / LOCK_FILE


def read(folder) -> dict | None:
    """The lock of an experiment folder (None: not locked or unreadable)."""
    try:
        d = json.loads(lock_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def pid_alive(pid) -> bool:
    """A process of this computer with this id is running."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":  # os.kill would terminate the process on Windows
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k32.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process
        return True
    except OSError:
        return False
    return True


def is_mine(info: dict | None) -> bool:
    return bool(info) and info.get("host") == _host() and str(info.get("pid")) == str(os.getpid())


def owner_alive(info: dict | None) -> bool:
    """The owner of a lock (or of a crash-recovery side file) may still be running: on this computer its process
    is checked; another computer's cannot be, so it counts as alive."""
    if not info:
        return False
    if info.get("host") != _host():
        return True
    return pid_alive(info.get("pid"))


def held_by_other(folder) -> dict | None:
    """The lock of another program that is (or may be) still running, else None (no lock, ours, or stale)."""
    info = read(folder)
    if info is None or is_mine(info) or not owner_alive(info):
        return None
    return info


def acquire(folder, force: bool = False) -> dict | None:
    """Lock the experiment folder for this process. Returns None when locked, or the other owner's lock when it is
    held by a live program (nothing is written unless ``force``: open anyway)."""
    other = held_by_other(folder)
    if other is not None and not force:
        return other
    from .atomicfile import write_text_atomic

    try:
        write_text_atomic(lock_path(folder), json.dumps(me()))
    except OSError:  # a read-only folder: nothing to protect from us
        pass
    return None


def release(folder):
    """Remove the lock if it is this process's (another program's lock is left alone)."""
    if folder is None:
        return
    if is_mine(read(folder)):
        try:
            lock_path(folder).unlink()
        except OSError:
            pass


def describe(info: dict) -> str:
    """'mANY-MAZE on <host> (user <user>, process <pid>, since <time>)'."""
    host = info.get("host") or "another computer"
    extra = [x for x in (f"user {info['user']}" if info.get("user") else "",
                         f"process {info['pid']}" if info.get("pid") else "",
                         f"since {str(info['since']).replace('T', ' ')}" if info.get("since") else "") if x]
    where = "this computer" if host == _host() else host
    return f"mANY-MAZE on {where}" + (f" ({', '.join(extra)})" if extra else "")
