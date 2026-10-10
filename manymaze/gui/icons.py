"""Application icons (Microsoft Fluent UI System Icons, MIT — see resources/icons/SOURCE.txt).

The mono icons are tinted mid-tone colours chosen for the light ribbon; in the dark scheme they are drawn in lighter
tints of the same colours (``DARK_TINTS``) so they keep their contrast. The icons follow the scheme as they are
painted, so a change of *View ▸ Appearance* needs no new icons."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
# mono icon tints → their dark-scheme counterparts (WCAG 3:1 against the dark ribbon and work area)
DARK_TINTS = {"#2f6fbf": "#7fb0f0", "#5a6472": "#aab4c3", "#7e57c2": "#b39ddb", "#d64541": "#f07a76",
              "#2e9a4f": "#5fd38a", "#e8762b": "#f29d5c", "#6b7280": "#a3aab5", "#9333ea": "#c084fc",
              "#367af2": "#7fb0f0", "#c8463d": "#f07a76", "#b0b0b0": "#c8c8c8"}


@lru_cache(maxsize=None)
def _renderer(path: str, scheme: str) -> QSvgRenderer:
    data = Path(path).read_bytes()
    if scheme == "dark":
        for a, b in DARK_TINTS.items():
            data = data.replace(f'"{a}"'.encode(), f'"{b}"'.encode())
    return QSvgRenderer(data)


class _SchemeIconEngine(QIconEngine):
    """Draws an SVG icon in the tints of the scheme in use (theme.scheme()), at the device's resolution."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._cache: dict[tuple, QPixmap] = {}

    def _render(self, size: QSize, mode, scale: float = 1.0) -> QPixmap:
        scale = max(1.0, float(scale or 1.0))
        key = (theme.scheme(), size.width(), size.height(), scale, mode == QIcon.Disabled)
        pm = self._cache.get(key)
        if pm is not None:
            return pm
        w, h = max(1, round(size.width() * scale)), max(1, round(size.height() * scale))
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        r = _renderer(self.path, key[0])
        vb = r.viewBoxF()
        side = min(w / vb.width(), h / vb.height()) if vb.width() and vb.height() else 1.0
        tw, th = vb.width() * side, vb.height() * side  # keep the aspect ratio, centred
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF((w - tw) / 2, (h - th) / 2, tw, th))
        p.end()
        if mode == QIcon.Disabled:
            from PySide6.QtWidgets import QApplication, QStyleOption

            app = QApplication.instance()
            if app is not None:
                pm = app.style().generatedIconPixmap(QIcon.Disabled, pm, QStyleOption())
        pm.setDevicePixelRatio(scale)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = pm
        return pm

    def paint(self, painter, rect: QRect, mode, state):
        painter.drawPixmap(rect, self._render(rect.size(), mode, painter.device().devicePixelRatioF()))

    def pixmap(self, size, mode, state):
        return self._render(size, mode)

    def scaledPixmap(self, size, mode, state, scale):
        return self._render(size, mode, scale)

    def actualSize(self, size, mode, state):
        return size

    def clone(self):
        return _SchemeIconEngine(self.path)

    def key(self):
        return "manymaze-scheme-svg"

    def isNull(self):
        return False


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """QIcon for a semantic name (e.g. "save", "play", "zone"); an empty icon if unknown."""
    p = ICON_DIR / f"{name}.svg"
    return QIcon(_SchemeIconEngine(str(p))) if p.exists() else QIcon()


def names() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))
