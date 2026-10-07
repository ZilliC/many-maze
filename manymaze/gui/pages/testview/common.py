"""Shared helpers of the test view: result / event table items."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem


def fmt_value(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if not math.isfinite(v):
            return "–"
        return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)

def _num_item(v, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(fmt_value(v) if not isinstance(v, str) else v)
    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _ro_item(text, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it
