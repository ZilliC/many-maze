"""Adjusting the apparatus calibration while a live test runs (ANY-maze: "adjust calibration during a test")."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QRadioButton, QVBoxLayout)

from ....core.live import LiveSession
from ...widgets import error_box, hint


class LiveCalibrationDialog(QDialog):
    """The new scale of a running test: the real length of the apparatus's calibration line, or pixels per cm."""

    def __init__(self, app, title: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust calibration")
        self.app = app
        line = getattr(app, "calibration_line", None)
        self.line_px = math.hypot(line[2] - line[0], line[3] - line[1]) if line else 0.0
        lay = QVBoxLayout(self)
        if title:
            lay.addWidget(QLabel(f"<b>{title}</b>"))
        cur = f"{app.px_per_cm:.4g} px/cm" if app is not None and app.px_per_cm else "not calibrated (pixels)"
        lay.addWidget(QLabel(f"Current calibration: {cur}"))
        self.by_line = QRadioButton("Real length of the calibration line")
        self.by_ppc = QRadioButton("Pixels per cm")
        grp = QButtonGroup(self)
        grp.addButton(self.by_line)
        grp.addButton(self.by_ppc)
        self.length = QDoubleSpinBox()
        self.length.setRange(0.01, 100000)
        self.length.setDecimals(2)
        self.length.setSuffix(" cm")
        self.length.setValue(app.calibration_length_cm if app is not None and app.calibration_length_cm else 10.0)
        self.ppc = QDoubleSpinBox()
        self.ppc.setRange(0.001, 100000)
        self.ppc.setDecimals(4)
        self.ppc.setSuffix(" px/cm")
        self.ppc.setValue(app.px_per_cm if app is not None and app.px_per_cm else 1.0)
        f = QFormLayout()
        f.addRow(self.by_line, self.length)
        f.addRow(self.by_ppc, self.ppc)
        lay.addLayout(f)
        if self.line_px:
            lay.addWidget(hint(f"The calibration line is {self.line_px:.1f} px long."))
            self.by_line.setChecked(True)
        else:
            self.by_line.setEnabled(False)
            self.length.setEnabled(False)
            self.by_ppc.setChecked(True)
        lay.addWidget(hint("The new scale is used from now on and saved with this test, whose results are calculated "
                           "with it (the apparatus map itself is not changed)."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        """set_calibration keyword arguments."""
        line = getattr(self.app, "calibration_line", None)
        if self.by_line.isChecked() and self.line_px:
            return {"px_per_cm": self.line_px / self.length.value(), "line": line, "length_cm": self.length.value()}
        return {"px_per_cm": self.ppc.value(), "line": None, "length_cm": None}


class CalibrationMixin:
    """Run tests: "Adjust calibration" for the test of the single panel or the selected panel of several tests."""

    def _calibration_target(self):
        """(session, entry or None) of the live camera test whose calibration can be adjusted, else (None, None)."""
        if self.mode == "multi":
            e = self._selected_entry()
            s = e.session if e is not None else None
        else:
            e, s = None, self.session
        if isinstance(s, LiveSession) and s.state in ("waiting", "running", "paused"):
            return s, e
        return None, None

    def adjust_calibration(self, px_per_cm: float | None = None, length_cm: float | None = None) -> dict | None:
        """Change the scale of the running test (a dialog unless px_per_cm or length_cm — the real length of the
        apparatus's calibration line — is given). Returns the stored calibration, None if nothing changed."""
        s, entry = self._calibration_target()
        if s is None:
            return None
        app = s.apparatus
        if px_per_cm is None and length_cm is None:
            dlg = LiveCalibrationDialog(app, s.name, self)
            if dlg.exec() != QDialog.Accepted:
                return None
            kw = dlg.values()
        elif length_cm is not None:
            line = app.calibration_line if app is not None else None
            if not line:
                error_box(self, "Adjust calibration", "The apparatus has no calibration line.")
                return None
            kw = {"px_per_cm": math.hypot(line[2] - line[0], line[3] - line[1]) / length_cm, "line": line,
                  "length_cm": length_cm}
        else:
            kw = {"px_per_cm": px_per_cm, "line": None, "length_cm": None}
        try:
            cal = s.set_calibration(**kw)
        except (ValueError, RuntimeError) as e:
            error_box(self, "Adjust calibration", e)
            return None
        if entry is None:
            with self._lock:
                self._apparatus = s.apparatus
            self.single_panel.view.set_apparatus(s.apparatus)
        else:
            p = self._panels.get(entry.id)
            if p is not None:
                p.view.set_apparatus(s.apparatus)
        self._log(f"Calibration adjusted to {cal['px_per_cm']:.4g} px/cm.", entry)
        self.main.mark_dirty()
        return cal
