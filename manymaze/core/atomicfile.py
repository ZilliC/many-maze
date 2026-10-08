"""Atomic file writes: the data goes to a temporary file next to the target, is flushed to the disk (fsync) and
then renamed over it, so a crash or a full disk never leaves a half-written experiment, track or apparatus file."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_write(path, mode: str = "w", encoding: str | None = "utf-8", newline: str | None = None):
    """``with atomic_write(path) as fh: ...`` writes ``path`` atomically (temp file, fsync, os.replace); on an
    error the target is left as it was and the temporary file is removed."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    if "b" in mode:
        encoding = None
    try:
        with open(tmp, mode, encoding=encoding, newline=newline) as fh:
            yield fh
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:  # pragma: no cover - file systems without fsync
                pass
        os.replace(tmp, p)
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
