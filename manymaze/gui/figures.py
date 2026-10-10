"""File-dialog filters for figures and tables, copying a figure to the clipboard, and matplotlib figures in the
colours of the window's scheme.

Figures are made by core.plots in light colours. Shown in the window in the dark scheme (:class:`ThemedCanvas`),
their neutral colours are swapped before each draw: white backgrounds become the dark work area, black and dark-grey
text, axes, ticks and lines become light, grid lines dim; data colours (treatments, heat maps, video frames) stay.
Saving, copying and exporting a figure draw it in its own light colours (:func:`light`)."""

from __future__ import annotations

import weakref
from contextlib import contextmanager

import matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.collections import Collection
from matplotlib.colors import to_hex, to_rgba
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.text import Text
from PySide6.QtGui import QGuiApplication, QImage

from ..core import plots
from . import theme

FIG_FILTER = "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)"
TABLE_FILTER = ("CSV file (*.csv);;Tab-separated text (*.tsv *.txt);;Excel workbook (*.xlsx);;SYLK spreadsheet (*.slk);;"
                "dBase table (*.dbf)")

# artist → {property: original (light) colour} of the figures recoloured for the dark scheme
_ORIGINAL: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def figure_to_clipboard(fig: Figure, dpi: int = 150) -> bool:
    if fig is None:
        return False
    with light(fig):
        img = QImage.fromData(plots.fig_to_png(fig, dpi=dpi))
    QGuiApplication.clipboard().setImage(img)
    return not img.isNull()


def _neutral(c) -> tuple[float, float] | None:
    """(luminance, alpha) of a grey (unsaturated) colour, None for a colour or 'none'."""
    try:
        r, g, b, a = to_rgba(c)
    except (TypeError, ValueError):
        return None
    if a == 0 or max(r, g, b) - min(r, g, b) > 0.12:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b, a


def _dark(c, role: str):
    """The dark-scheme colour of a light-scheme colour (None: unchanged). role "face": backgrounds (white → the
    dark surface); "fg": text, axes and lines (black / dark grey → light grey); "grid": light grid lines (dimmed)."""
    n = _neutral(c)
    if n is None:
        return None
    lum, alpha = n
    if role == "face":
        return to_rgba(theme.DARK["BASE"], alpha) if lum > 0.85 else None
    if role == "grid":
        return to_rgba(theme.DARK["GRID"], alpha) if lum > 0.75 else None
    if lum < 0.5:
        v = 0.91 - 0.6 * lum
        return (v, v, v, alpha)
    return None


def _colour_props(artist):
    """(property, role, getter, setter) of the colours of an artist that may follow the scheme."""
    if isinstance(artist, Text):
        return [("color", "fg", artist.get_color, artist.set_color)]
    if isinstance(artist, Line2D):
        role = "grid" if getattr(artist, "_mm_grid", False) else "fg"
        return [("color", role, artist.get_color, artist.set_color),
                ("mec", "fg", artist.get_markeredgecolor, artist.set_markeredgecolor),
                ("mfc", "fg", artist.get_markerfacecolor, artist.set_markerfacecolor)]
    if isinstance(artist, Patch):
        return [("fc", "face", artist.get_facecolor, artist.set_facecolor),
                ("ec", "fg", artist.get_edgecolor, artist.set_edgecolor)]
    if isinstance(artist, Collection):
        return [("fcs", "fg", artist.get_facecolor, artist.set_facecolor),
                ("ecs", "fg", artist.get_edgecolor, artist.set_edgecolor)]
    return []


def apply_scheme(fig: Figure, dark: bool | None = None):
    """Recolour a figure's neutral colours for the dark scheme, or give them back (light). dark: default the
    scheme in use."""
    if fig is None:
        return
    dark = theme.is_dark() if dark is None else dark
    for ax in fig.axes:  # the grid lines are dimmed rather than inverted
        for line in ax.get_xgridlines() + ax.get_ygridlines():
            line._mm_grid = True
    for artist in fig.findobj(lambda a: True):
        if artist is fig.patch:
            props = [("fc", "face", artist.get_facecolor, artist.set_facecolor)]
        else:
            props = _colour_props(artist)
        if not props:
            continue
        orig = _ORIGINAL.get(artist)
        if orig is None:
            if not dark:
                continue
            orig = _ORIGINAL[artist] = {}
        for name, role, get, set_ in props:
            if name not in orig:
                v = get()
                orig[name] = v.copy() if hasattr(v, "copy") else v
            v = orig[name]
            if not dark:
                set_(v)
                continue
            if name in ("fcs", "ecs"):
                if len(v):
                    new = [(_dark(c, role) or tuple(c)) for c in v]
                    set_(new)
            else:
                new = _dark(v, role)
                set_(new if new is not None else v)
    if fig.patch in _ORIGINAL and dark:  # the figure's own background: the work area
        fig.patch.set_facecolor(theme.DARK["WORK_BG"])
    for ax in fig.axes:  # ticks made later (zooming) get the scheme's colours too
        if dark:
            ax.tick_params(axis="both", which="both", colors=to_hex(_dark("black", "fg")))
        elif ax in _ORIGINAL or any(t in _ORIGINAL for t in ax.get_xticklabels()):
            ax.tick_params(axis="both", which="both", colors=matplotlib.rcParams["xtick.color"])
    fig._mm_dark = dark


@contextmanager
def light(fig: Figure):
    """The figure in its light colours while saving, copying or exporting it; then as shown again."""
    was = getattr(fig, "_mm_dark", False) if fig is not None else False
    if was:
        apply_scheme(fig, False)
    try:
        yield fig
    finally:
        if was:
            apply_scheme(fig, True)


class ThemedCanvas(FigureCanvasQTAgg):
    """A matplotlib canvas whose figure is drawn in the colours of the window's scheme."""

    def draw(self):
        if self.figure is not None and (theme.is_dark() or getattr(self.figure, "_mm_dark", False)):
            apply_scheme(self.figure)
        super().draw()

    def theme_changed(self):
        if self.figure is not None:
            apply_scheme(self.figure)
        self.draw_idle()

    def print_figure(self, *args, **kwargs):  # savefig: light colours
        with light(self.figure):
            return super().print_figure(*args, **kwargs)
