"""Free disk space: how much room is left where an experiment and its recordings are stored, and whether that is
enough (opening an experiment, arming and running recorded live tests, the procedures' "disk space low" / "disk
full" events, see live.LiveSession._check_disk).  The one free-space helper of the application."""

from __future__ import annotations

import errno
import shutil
from dataclasses import dataclass
from pathlib import Path

MB = 1024 ** 2
GB = 1024 ** 3
LOW_BYTES = 2 * GB  # less than this left: disk space is low (a warning)
CRITICAL_BYTES = 200 * MB  # less than this free: the disk is full for practical purposes (recording fails)
# rough size of a live recording per pixel and frame (H.264 in a fragmented MP4 at the usual camera sizes is
# 0.02–0.1 bit per pixel; the estimate errs on the large side)
BYTES_PER_PIXEL_FRAME = 0.02


def _existing(path) -> Path | None:
    """The path, or its nearest existing parent (a recordings folder may not exist yet)."""
    if path is None or str(path) == "":
        return None
    p = Path(path).expanduser().absolute()
    for q in (p, *p.parents):
        if q.exists():
            return q
    return None


def free_bytes(path) -> int | None:
    """Bytes available on the disk holding `path` (or its nearest existing parent); None when unknown."""
    p = _existing(path)
    if p is None:
        return None
    try:
        return int(shutil.disk_usage(p).free)
    except OSError:
        return None


def free_mb(path) -> float | None:
    """Free space in MB (10⁶ bytes) on the disk of `path`; None when unknown (the live procedures' "disk space
    low" / "disk full" thresholds are in MB)."""
    b = free_bytes(path)
    return None if b is None else b / 1e6


def is_disk_full(e: BaseException) -> bool:
    """Is this error a full disk (ENOSPC / EDQUOT, or an encoder message saying so)?"""
    if isinstance(e, OSError) and e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return True
    text = str(e).lower()
    return "no space left" in text or "disk full" in text or "disk quota" in text


def recording_bytes(width: int, height: int, fps: float, duration_s: float) -> int:
    """Estimated size of a live recording (0 when the duration is open-ended or the image size unknown)."""
    if not duration_s or duration_s <= 0 or not width or not height:
        return 0
    return int(width * height * (fps or 25.0) * duration_s * BYTES_PER_PIXEL_FRAME)


def format_bytes(n: float | None) -> str:
    if n is None:
        return "unknown"
    n = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class SpaceCheck:
    """The result of :func:`check`. level: "ok" | "low" | "critical" | "unknown" (free space not available)."""

    path: str
    free: int | None
    needed: int = 0
    level: str = "ok"

    @property
    def ok(self) -> bool:
        return self.level in ("ok", "unknown")

    @property
    def message(self) -> str:
        if self.ok:
            return ""
        what = f"Only {format_bytes(self.free)} free on the disk of {self.path}"
        if self.needed and self.free - self.needed < LOW_BYTES:
            return f"{what}, and the recording may need about {format_bytes(self.needed)}."
        if self.level == "critical":
            return f"{what}: the disk is (almost) full — recordings and saving will fail."
        return f"{what}: disk space is low."


def check(path, needed: int = 0, low: int = LOW_BYTES, critical: int = CRITICAL_BYTES) -> SpaceCheck:
    """Room on the disk of `path` for `needed` more bytes: "critical" below `critical` bytes free (or when
    `needed` does not fit at all), "low" when less than `low` bytes would be left afterwards."""
    free = free_bytes(path)
    c = SpaceCheck(str(path or ""), free, max(0, int(needed or 0)))
    if free is None:
        c.level = "unknown"
    elif free < critical or free < c.needed:
        c.level = "critical"
    elif free - c.needed < low:
        c.level = "low"
    return c
