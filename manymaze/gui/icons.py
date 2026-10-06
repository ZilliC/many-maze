"""Application icons (Microsoft Fluent UI System Icons, MIT — see resources/icons/SOURCE.txt)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """QIcon for a semantic name (e.g. "save", "play", "zone"); an empty icon if unknown."""
    p = ICON_DIR / f"{name}.svg"
    return QIcon(str(p)) if p.exists() else QIcon()


def names() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))
