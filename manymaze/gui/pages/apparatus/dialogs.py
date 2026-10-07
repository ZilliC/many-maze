"""Dialogs of the apparatus page: create an apparatus from a template, add a grid of zones."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QRadioButton, QSpinBox, QVBoxLayout)

from ....core import templates
from ....core.apparatus import GRID_KINDS, Apparatus, unique_name
from ....core.templates import TEMPLATES
from ...widgets import draw_apparatus
from ..base import SettingsForm


def _param_label(key: str) -> str:
    unit = ""
    if key.endswith("_cm"):
        key, unit = key[:-3], " (cm)"
    elif key.endswith("_s"):
        key, unit = key[:-2], " (s)"
    return key.replace("_", " ").capitalize() + unit


def param_spec(info: templates.TemplateInfo) -> list[tuple]:
    """SettingsForm spec of a template's parameters, from their default values."""
    spec = []
    for k, v in info.params.items():
        if k in info.choices:
            row = ("choice", [(c, str(c)) for c in info.choices[k]])
        elif isinstance(v, bool):
            row = ("bool", None)
        elif isinstance(v, int):
            counted = k.startswith("n_") or k == "escape_hole"
            row = ("int", (3 if k.startswith("n_") else 1 if counted else 0, 100 if counted else 100000, 1))
        elif isinstance(v, float):
            row = ("float", (0.01, 1.0, 0.05, 3) if "fraction" in k else (0.0, 100000.0, 1.0, 1))
        else:
            row = ("text", None)
        spec.append((k, _param_label(k), *row, ""))
    return spec


class TemplateDialog(QDialog):
    """Choose a built-in template, edit its parameters and how it is placed."""

    NON_SQUARE = {"light_dark", "three_chamber", "t_maze", "fear_conditioning", "custom", "novel_tank", "multi_well",
                  "cpp", "home_cage"}

    def __init__(self, parent=None, key: str = "open_field", current_name: str | None = None,
                 taken_names=(), has_background: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Create apparatus from template")
        self.current_name = current_name
        self.taken = list(taken_names)
        self.form: SettingsForm | None = None  # the parameters of the selected template
        self._values = SimpleNamespace()
        self._name_touched = False

        self.list = QListWidget()
        self.list.setFixedWidth(230)
        for k, t in TEMPLATES.items():
            it = QListWidgetItem(t.title)
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.params_box = QGroupBox("Parameters")
        QVBoxLayout(self.params_box)

        self.preview = QGraphicsView()
        self.preview.setScene(QGraphicsScene(self.preview))
        self.preview.setFixedSize(220, 220)
        self.preview.setRenderHints(QPainter.Antialiasing)
        self.preview.setBackgroundBrush(QColor("#ffffff"))
        self.preview.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview.setInteractive(False)

        place = QGroupBox("Placement on the video image")
        pl = QVBoxLayout(place)
        self.place_drag = QRadioButton("Drag a rectangle around the apparatus on the image")
        self.place_fit = QRadioButton("Fit to the whole frame")
        (self.place_drag if has_background else self.place_fit).setChecked(True)
        self.square = QCheckBox("Keep square proportions")
        pl.addWidget(self.place_drag)
        pl.addWidget(self.place_fit)
        pl.addWidget(self.square)

        tgt = QGroupBox("Apparatus")
        tl = QFormLayout(tgt)
        self.replace = QRadioButton(f"Replace “{current_name}”" if current_name else "Replace current apparatus")
        self.add_new = QRadioButton("Add as a new apparatus")
        self.replace.setEnabled(current_name is not None)
        (self.replace if current_name is not None else self.add_new).setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self.replace)
        bg.addButton(self.add_new)
        self.replace.toggled.connect(self._update_name)
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(lambda _: setattr(self, "_name_touched", True))
        tl.addRow(self.replace)
        tl.addRow(self.add_new)
        tl.addRow("Name", self.name_edit)

        right = QVBoxLayout()
        top = QHBoxLayout()
        tv = QVBoxLayout()
        tv.addWidget(self.title)
        tv.addWidget(self.desc)
        tv.addWidget(self.params_box)
        tv.addStretch()
        top.addLayout(tv, 1)
        top.addWidget(self.preview, 0, Qt.AlignTop)
        right.addLayout(top)
        right.addWidget(place)
        right.addWidget(tgt)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay = QHBoxLayout(self)
        lay.addWidget(self.list)
        lay.addLayout(right, 1)
        self.resize(820, 560)

        self.list.currentRowChanged.connect(self._template_changed)
        self.square.toggled.connect(self._update_preview)
        self.select_template(key if key in TEMPLATES else "open_field")

    # ---- public API ----------------------------------------------------------
    def select_template(self, key: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == key:
                self.list.setCurrentRow(i)
                return

    def key(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else "open_field"

    def params(self) -> dict:
        return dict(vars(self._values))

    def set_param(self, k: str, v):
        setattr(self._values, k, v)
        self.form.load(self._values)
        self._update_preview()

    def name(self) -> str:
        return self.name_edit.text().strip()

    def is_replace(self) -> bool:
        return self.replace.isChecked()

    def placement(self) -> str:
        return "fit" if self.place_fit.isChecked() else "drag"

    # ---- internals -----------------------------------------------------------
    def _template_changed(self, *_):
        info = TEMPLATES[self.key()]
        self.title.setText(info.title)
        self.desc.setText(info.description)
        if self.form is not None:
            self.form.deleteLater()
        self._values = SimpleNamespace(**info.params)
        self.form = SettingsForm(param_spec(info), self._values)
        self.form.changed.connect(self._update_preview)
        self.params_box.layout().addWidget(self.form)
        self.params_box.setVisible(bool(info.params))
        self.square.setChecked(self.key() not in self.NON_SQUARE)
        self._update_name()
        self._update_preview()

    def _update_name(self, *_):
        if self._name_touched:
            return
        if self.replace.isChecked() and self.current_name:
            self.name_edit.setText(self.current_name)
        else:
            self.name_edit.setText(unique_name(TEMPLATES[self.key()].title, self.taken))

    def _update_preview(self, *_):
        sc = self.preview.scene()
        sc.clear()
        w, h = (200, 200) if self.square.isChecked() else (240, 160)
        try:
            app = templates.build(self.key(), 0, 0, w, h, **self.params())
        except Exception:
            return
        draw_apparatus(sc, app, labels=False, fill_alpha=70)
        self.preview.fitInView(QRectF(-12, -12, w + 24, h + 24), Qt.KeepAspectRatio)


class GridDialog(QDialog):
    """Regularly spaced grid of zones: square cells, concentric rings, radial sectors or rings × sectors."""

    def __init__(self, parent=None, app: Apparatus | None = None, has_selection: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Add grid")
        self.app = app
        f = QFormLayout(self)
        self.kind = QComboBox()
        for k, lbl in GRID_KINDS.items():
            self.kind.addItem(lbl, k)
        self.region = QComboBox()
        self.region.addItem("Arena boundary", "arena")
        if has_selection:
            self.region.addItem("Selected zone", "zone")
        self.region.addItem("Whole image", "frame")
        self.name = QLineEdit(unique_name("Grid", (app.names() + [g.name for g in app.grids]) if app else []))
        self.nx, self.ny, self.rings, self.sectors = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for w, v in ((self.nx, 4), (self.ny, 4), (self.rings, 3), (self.sectors, 8)):
            w.setRange(1, 60)
            w.setValue(v)
        self.cell = QDoubleSpinBox()
        self.cell.setRange(0, 10000)
        self.cell.setDecimals(1)
        self.cell.setSuffix(" cm")
        self.cell.setSpecialValueText("Use column / row counts")
        self.cell.setToolTip("Real-world cell size (needs a calibration); overrides the counts")
        self.cell.setEnabled(bool(app and app.px_per_cm))
        self.start = QDoubleSpinBox()
        self.start.setRange(-360, 360)
        self.start.setValue(-90)
        self.start.setSuffix("°")
        self.start.setToolTip("Angle of the first sector edge: -90 = top, 0 = right; sectors run clockwise")
        self.clip = QCheckBox("Clip square cells to the region")
        self.clip.setChecked(True)
        self.group = QCheckBox("Add a zone group containing all cells")
        self.group.setChecked(True)
        f.addRow("Grid type", self.kind)
        f.addRow("Cover", self.region)
        f.addRow("Name", self.name)
        f.addRow("Columns", self.nx)
        f.addRow("Rows", self.ny)
        f.addRow("Cell size", self.cell)
        f.addRow("Rings", self.rings)
        f.addRow("Sectors", self.sectors)
        f.addRow("First sector at", self.start)
        f.addRow(self.clip)
        f.addRow(self.group)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Add grid")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._form = f
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self, *_):
        k = self.kind.currentData()
        for w, on in ((self.nx, k == "square"), (self.ny, k == "square"), (self.cell, k == "square"),
                      (self.clip, k == "square"), (self.rings, k in ("rings", "polar")),
                      (self.sectors, k in ("sectors", "polar")), (self.start, k in ("sectors", "polar"))):
            self._form.setRowVisible(w, on)

    def spec(self) -> dict:
        k = self.kind.currentData()
        params = {}
        if k == "square":
            params.update(nx=self.nx.value(), ny=self.ny.value(), clip=self.clip.isChecked())
            if self.cell.value() > 0:
                params["cell_cm"] = self.cell.value()
        if k in ("rings", "polar"):
            params["rings"] = self.rings.value()
        if k in ("sectors", "polar"):
            params.update(sectors=self.sectors.value(), start_deg=self.start.value())
        return {"kind": k, "region": self.region.currentData(), "name": self.name.text().strip() or "Grid",
                "group": self.group.isChecked(), "params": params}
