"""Errors explained to the user in plain words (the technical text is for the console)."""

from __future__ import annotations

import errno
import json
import os
import zipfile
from pathlib import Path

# what to tell the user about a system error on a file (exc.filename), by type ({what}: the file's
# quoted name, or "the file")
_OS_ERRORS = {
    FileNotFoundError: "{what} was not found. It may have been moved, renamed or deleted, or its drive may not be "
                       "connected.",
    PermissionError: "mANY-MAZE is not allowed to open or change {what}. Check that it is not read-only or open "
                     "in another program, and that you may use its folder.",
    IsADirectoryError: "{what} is a folder: choose a file.",
    NotADirectoryError: "{what} is not a folder.",
    FileExistsError: "{what} already exists: choose another name.",
}
UNEXPECTED_ERROR = ("Something went wrong unexpectedly and the operation was not completed. The technical details "
                    "were written to the console; please include them when reporting the problem.")


def user_message(exc: BaseException) -> str:
    """What to tell the user about an error: a plain explanation, not the Python exception (the technical text is
    for the console). Errors raised by mANY-MAZE itself (ValueError, RuntimeError, OSError without an error number)
    already carry a message for the user and keep it."""
    text = str(exc).strip()
    if isinstance(exc, OSError) and exc.errno is not None:
        what = f"“{Path(os.fsdecode(exc.filename)).name}”" if exc.filename else "the file"
        if exc.errno == errno.ENOSPC:
            return "The disk is full: free some space (or choose another drive) and try again."
        for kind, msg in _OS_ERRORS.items():
            if isinstance(exc, kind):
                msg = msg.format(what=what)
                return msg[0].upper() + msg[1:]
        return f"{what[0].upper() + what[1:]} could not be read or written ({exc.strerror or 'system error'})."
    if isinstance(exc, zipfile.BadZipFile):
        return "The file is not in the expected format (it should be a zip archive) or it is damaged."
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return "The file is damaged or is not in the expected format, and cannot be read."
    if isinstance(exc, MemoryError):
        return "There is not enough memory for this. Close other programs, or work on fewer or shorter videos."
    if isinstance(exc, (ValueError, RuntimeError, OSError)) and text:
        return text
    return UNEXPECTED_ERROR
