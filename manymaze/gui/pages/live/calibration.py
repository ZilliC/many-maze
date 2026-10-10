"""Adjusting the apparatus calibration and geometry while a live test runs (ANY-maze: "adjust calibration during a
test", change the apparatus while it runs)."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QLabel, QRadioButton, QVBoxLayout)

from ....core.apparatus import DISTANCE_UNITS, POSITION_KEY, position_args
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
        # the length in the experiment's distance unit (the calibration itself is in px per cm)
        unit = getattr(app, "distance_unit", "cm") if app is not None else "cm"
        self.per_cm = DISTANCE_UNITS[unit]
        self.length = QDoubleSpinBox()
        self.length.setRange(0.001, 1e6)
        self.length.setDecimals(3 if unit == "m" else 2)
        self.length.setSuffix(f" {unit}")
        cm = app.calibration_length_cm if app is not None and app.calibration_length_cm else 10.0
        self.length.setValue(cm * self.per_cm)
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
            cm = self.length.value() / self.per_cm
            return {"px_per_cm": self.line_px / cm, "line": line, "length_cm": cm}
        return {"px_per_cm": self.ppc.value(), "line": None, "length_cm": None}


class LiveGeometryDialog(QDialog):
    """The apparatus map of a running test: move / rotate / scale the whole map, and / or move one zone."""

    def __init__(self, session: LiveSession, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust apparatus")
        self.session = session
        lay = QVBoxLayout(self)
        if session.name:
            lay.addWidget(QLabel(f"<b>{session.name}</b>"))
        pos = position_args(session.geometry.get(POSITION_KEY))
        box = QGroupBox("Whole apparatus map (from where it is drawn)")
        f = QFormLayout(box)
        self.pos: dict[str, QDoubleSpinBox] = {}
        for key, label, lo, hi, step, dec, suffix in (
                ("dx", "Move right", -5000, 5000, 1, 1, " px"), ("dy", "Move down", -5000, 5000, 1, 1, " px"),
                ("angle", "Rotate", -180, 180, 0.5, 1, "°"), ("scale", "Scale", 0.2, 5, 0.01, 3, "×")):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setSuffix(suffix)
            sp.setValue(pos[key])
            f.addRow(label, sp)
            self.pos[key] = sp
        lay.addWidget(box)
        zb = QGroupBox("Move one zone (from where it is now)")
        zf = QFormLayout(zb)
        self.zone = QComboBox()
        self.zone.addItem("—", None)
        app = session.apparatus
        for z in (app.zones if app is not None else []):
            self.zone.addItem(z.name, z.name)
        self.zone_dx, self.zone_dy = QDoubleSpinBox(), QDoubleSpinBox()
        for sp, lbl in ((self.zone_dx, "Move right"), (self.zone_dy, "Move down")):
            sp.setRange(-5000, 5000)
            sp.setDecimals(1)
            sp.setSuffix(" px")
        zf.addRow("Zone", self.zone)
        zf.addRow("Move right", self.zone_dx)
        zf.addRow("Move down", self.zone_dy)
        lay.addWidget(zb)
        lay.addWidget(hint("The new geometry is used from now on and saved with this test, whose results are "
                           "calculated with it (the apparatus map itself is not changed)."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        """set_geometry keyword arguments."""
        out = {"position": {k: sp.value() for k, sp in self.pos.items()}}
        name = self.zone.currentData()
        dx, dy = self.zone_dx.value(), self.zone_dy.value()
        if name and (dx or dy):
            z = self._moved_map(out["position"]).zone(name)
            out["zones"] = {name: z.shape.translated(dx, dy).to_dict()}
        return out

    def _moved_map(self, position: dict):
        """The test's map once the whole-map position of this dialog is applied: a zone moved in the same OK is
        moved from where it is then (else a map moved at the same time would leave the zone behind)."""
        s = self.session
        base = getattr(s, "_base_apparatus", None)
        if base is None:
            return s.apparatus
        pos = position_args(position)
        ov = {**(s.zone_overrides or {}), **s.geometry, **getattr(s, "procedure_zone_overrides", {})}
        if pos == {"dx": 0.0, "dy": 0.0, "angle": 0.0, "scale": 1.0}:
            ov.pop(POSITION_KEY, None)
        else:
            ov[POSITION_KEY] = pos
        return base.with_overrides(ov)


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
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
            if not accepted:
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

    def adjust_geometry(self, position: dict | None = None, zones: dict | None = None) -> dict | None:
        """Change the apparatus geometry of the running test (a dialog unless position or zones is given): the
        whole map moved / rotated / scaled ({"dx", "dy", "angle", "scale"}) and / or zones moved ({name: shape
        dict}). Returns the test's geometry overrides, None if nothing changed."""
        s, entry = self._calibration_target()
        if s is None:
            return None
        if position is None and zones is None:
            dlg = LiveGeometryDialog(s, self)
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
            if not accepted:
                return None
            kw = dlg.values()
        else:
            kw = {"position": position, "zones": zones}
        try:
            geo = s.set_geometry(**kw)
        except (ValueError, RuntimeError) as e:
            error_box(self, "Adjust apparatus", e)
            return None
        if entry is None:
            with self._lock:
                self._apparatus = s.apparatus
            self.single_panel.view.set_apparatus(s.apparatus)
        else:
            entry.apparatus = s.apparatus
            p = self._panels.get(entry.id)
            if p is not None:
                p.view.set_apparatus(s.apparatus)
        self._log("Apparatus geometry adjusted for this test.", entry)
        self.main.mark_dirty()
        return geo
