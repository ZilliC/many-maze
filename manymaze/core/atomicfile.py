"""Atomic file writes: the data goes to a temporary file next to the target, is flushed to the disk (fsync) and
then renamed over it, so a crash or a full disk never leaves a half-written experiment, track or apparatus file."""

from __future__ import annotations

import itertools
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

_counter = itertools.count()
_counter_lock = threading.Lock()


def _temp_name(p: Path) -> Path:
    """A temporary file name next to ``p`` that no other writer (thread, process or computer sharing the folder)
    uses: ``<name>.<pid>-<n>-<random>.tmp``."""
    with _counter_lock:
        n = next(_counter)
    return p.with_name(f"{p.name}.{os.getpid()}-{n}-{uuid.uuid4().hex[:8]}.tmp")


def _sync_file(fd: int):
    """Flush a file to the disk. On macOS fsync only reaches the drive's cache: F_FULLFSYNC asks the drive to
    write it out (fsync is used when the file system does not support it)."""
    if sys.platform == "darwin":
        import fcntl

        if hasattr(fcntl, "F_FULLFSYNC"):
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError:
                pass
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - file systems without fsync
        pass


def sync_dir(folder):
    """fsync a folder so a rename in it survives a power cut (not possible on Windows: skipped)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def atomic_write(path, mode: str = "w", encoding: str | None = "utf-8", newline: str | None = None):
    """``with atomic_write(path) as fh: ...`` writes ``path`` atomically (unique temp file, fsync, os.replace,
    fsync of the folder); on an error the target is left as it was and the temporary file is removed."""
    p = Path(path)
    tmp = _temp_name(p)
    if "b" in mode:
        encoding = None
    xmode = mode.replace("w", "x")  # the temp file must be new (never someone else's)
    try:
        with open(tmp, xmode, encoding=encoding, newline=newline) as fh:
            yield fh
            fh.flush()
            _sync_file(fh.fileno())
        os.replace(tmp, p)
        sync_dir(p.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def write_text_atomic(path, text: str, encoding: str = "utf-8"):
    """Write a text file atomically (see :func:`atomic_write`)."""
    with atomic_write(path, encoding=encoding) as fh:
        fh.write(text)
