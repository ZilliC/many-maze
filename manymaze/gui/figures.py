"""File-dialog filters for figures and tables, and copying a figure to the clipboard."""

from __future__ import annotations

from matplotlib.figure import Figure
from PySide6.QtGui import QGuiApplication, QImage

from ..core import plots

FIG_FILTER = "PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)"
TABLE_FILTER = ("CSV file (*.csv);;Tab-separated text (*.tsv *.txt);;Excel workbook (*.xlsx);;SYLK spreadsheet (*.slk);;"
                "dBase table (*.dbf)")


def figure_to_clipboard(fig: Figure, dpi: int = 150) -> bool:
    if fig is None:
        return False
    img = QImage.fromData(plots.fig_to_png(fig, dpi=dpi))
    QGuiApplication.clipboard().setImage(img)
    return not img.isNull()
