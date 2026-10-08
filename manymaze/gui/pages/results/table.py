"""The Data page spreadsheet: the results model (display format, sorting), its row filter and the categories
of the measure chooser."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from ....core.export import display_text, value_text
from ....core.stats import is_number

GENERAL_PREFIXES = ("Test duration", "Detection", "Total distance", "Mean speed", "Max speed", "Time mobile",
                    "Time immobile", "Immobile episodes", "Latency to first immobility", "Time freezing",
                    "Freezing", "Latency to first freezing", "Mean freezing episode", "Mean motion",
                    "Path efficiency", "Absolute turn angle", "Meander", "Rotations", "Mean distance from wall",
                    "Thigmotaxis", "Time outside arena", "Time not detected", "Centre positions recorded",
                    "Head positions recorded", "Head tracked", "Tracking quality", "Average freezing score",
                    "Time active", "Time inactive", "Active episodes", "Inactive episodes", "Longest active",
                    "Shortest active", "Longest inactive", "Shortest inactive", "Head distance", "Head turn angle",
                    "Rears", "Time rearing", "Latency to first rear", "Mean rear", "Max rear", "Min rear",
                    "Time hidden", "Time not hidden", "Mobile episodes", "Mean mobile episode",
                    "Mean immobile episode", "Longest immobile", "Shortest immobile", "Longest mobile",
                    "Shortest mobile", "Latency to first mobile", "Latency to last mobile", "Latency to last immobile",
                    "Longest freezing", "Shortest freezing", "Path tortuosity", "Mean turn angle", "Angular velocity",
                    "Total rotations", "Path rotations", "Average position", "Mean distance from centre",
                    "Max distance from centre", "Arena quadrant", "First zone entered", "Visited zones",
                    "Investigated zones", "Zone transitions", "Total line crossings")
CATEGORY_ORDER = ["Information", "General", "Zones", "Points of interest", "Lines", "Test-specific", "Behaviours",
                  "Social", "I/O", "Other"]
COLUMN_LABELS = {"Group": "Treatment"}


def _names(project) -> dict[str, set]:
    zones, points, lines = set(), set(), set()
    for a in (project.apparatus if project else []):
        zones.update(z.name for z in a.zones)
        zones.update(g.name for g in a.groups)
        points.update(p.name for p in a.points)
        lines.update(l.name for l in a.lines)
    beh = {b.name for b in (project.behaviours if project else [])}
    io = set()
    for d in getattr(project, "io_devices", None) or []:
        for c in d.get("channels", []) or []:
            if c.get("name"):
                io.update((str(c["name"]), f"{d.get('name')}/{c['name']}"))
    return {"zones": zones, "points": points, "lines": lines, "behaviours": beh, "io": io}


def measure_category(col: str, names: dict) -> tuple[str, str]:
    """(category, sub-category) of a measure column, inferred from its "Name: measure" prefix."""
    if ": " in col:
        prefix = col.split(": ", 1)[0]
        for key, cat in (("zones", "Zones"), ("points", "Points of interest"), ("lines", "Lines"),
                         ("behaviours", "Behaviours")):
            if prefix in names[key]:
                return cat, prefix
        if prefix.startswith("Animal "):
            return "Social", ""
        for word, sub in (("Shocker ", "Shockers"), ("Speaker ", "Speakers"), ("Light ", "Lights")):
            if prefix.startswith(word):
                return "I/O", sub
        if prefix == "OPAD" or prefix.startswith("OPAD at "):
            return "I/O", "OPAD"
        if prefix == "Variable":
            return "I/O", "Result variables"
        if prefix in names.get("io", ()) or prefix.split(" in ")[0] in names.get("io", ()):
            return "I/O", prefix
        if col.startswith(GENERAL_PREFIXES):  # e.g. "Arena quadrant NE: time (%)"
            return "General", ""
        return "Other", prefix
    if col.startswith(GENERAL_PREFIXES):
        return "General", ""
    return "Test-specific", ""


def column_label(col: str) -> str:
    """Column heading as shown (ANY-maze terms; the data keep their names, e.g. "Group" holds the treatment)."""
    return COLUMN_LABELS.get(col, col)


# ------------------------------------------------------------------ table model
class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.columns: list[str] = []

    def set_data(self, rows: list[dict], columns: list[str]):
        self.beginResetModel()
        self.rows = rows
        self.columns = columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def value(self, row: int, col: int):
        return self.rows[row].get(self.columns[col])

    def sort_key(self, row: int, col: int):
        v = self.value(row, col)
        if is_number(v):
            return (0, float(v), "") if math.isfinite(v) else (2, 0.0, "")
        if v is None or v == "":
            return (2, 0.0, "")
        return (1, 0.0, str(v).lower())

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self.value(index.row(), index.column())
        if role == Qt.DisplayRole:
            return display_text(v)
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if is_number(v) else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and is_number(v) and not isinstance(v, (int, np.integer)):
            return value_text(v)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            c = self.columns[section]
            if role == Qt.DisplayRole:
                return column_label(c)
            if role == Qt.ToolTipRole:
                return "Treatment (the animal's group)" if c == "Group" else c
        elif orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None


class ResultsProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filters: dict[str, str] = {}

    def set_filters(self, filters: dict):
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            self.filters = {k: v for k, v in filters.items() if v is not None}
            self.endFilterChange()
        else:
            self.filters = {k: v for k, v in filters.items() if v is not None}
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        return all(str(r.get(k, "")) == v for k, v in self.filters.items())

    def lessThan(self, left, right):
        m = self.sourceModel()
        return m.sort_key(left.row(), left.column()) < m.sort_key(right.row(), right.column())
