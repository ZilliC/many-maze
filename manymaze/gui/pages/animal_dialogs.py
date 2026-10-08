"""Dialogs of the Experiment tab: numbered series of animals, dose calculator, randomisation, training criteria and
weighing animals on a scale."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ...core import scales
from ...core import workflow as wf
from ...core.workflow import treatment_text
from ..widgets import color_icon


class AddSeveralDialog(QDialog):
    def __init__(self, groups: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add animals")
        f = QFormLayout(self)
        self.prefix = QLineEdit("M")
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setValue(10)
        self.start = QSpinBox()
        self.start.setRange(0, 100000)
        self.start.setValue(1)
        self.digits = QSpinBox()
        self.digits.setRange(1, 6)
        self.digits.setValue(2)
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + groups)
        self.preview = QLabel()
        self.preview.setStyleSheet("color:palette(mid)")
        for w in (self.prefix,):
            w.textChanged.connect(self._update)
        for w in (self.count, self.start, self.digits):
            w.valueChanged.connect(self._update)
        f.addRow("ID prefix", self.prefix)
        f.addRow("Number of animals", self.count)
        f.addRow("First number", self.start)
        f.addRow("Digits", self.digits)
        f.addRow("Treatment", self.group)
        f.addRow("", self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._update()

    def ids(self) -> list[str]:
        p, s, d = self.prefix.text().strip(), self.start.value(), self.digits.value()
        return [f"{p}{s + i:0{d}d}" for i in range(self.count.value())]

    def _update(self, *_):
        ids = self.ids()
        self.preview.setText(f"{ids[0]} … {ids[-1]}" if len(ids) > 1 else ids[0])


class DoseDialog(QDialog):
    """Injection volume = weight × dose / concentration, written to the animals' "Volume (mL)" field."""

    def __init__(self, project, n_selected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dose calculator")
        ds = wf.dose_settings(project)
        f = QFormLayout(self)
        f.addRow(QLabel("Volume (mL) = weight (g) / 1000 × dose (mg/kg) / concentration (mg/mL).<br>"
                        f"Animals with their own “{wf.DOSE_FIELD}” field use that dose instead of the default."))
        self.weight = QComboBox()
        self.weight.setEditable(True)
        fields = [x for x in project.animal_fields if x not in (wf.VOLUME_FIELD, wf.DOSE_FIELD)]
        self.weight.addItems(fields or [wf.WEIGHT_FIELD])
        self.weight.setCurrentText(ds["weight_field"] if ds["weight_field"] in fields or not fields
                                   else next((x for x in fields if "weight" in x.lower()), fields[0]))
        self.dose = QDoubleSpinBox()
        self.dose.setRange(0, 1e6)
        self.dose.setDecimals(3)
        self.dose.setSuffix(" mg/kg")
        self.dose.setValue(float(ds["dose_mg_kg"]))
        self.conc = QDoubleSpinBox()
        self.conc.setRange(0.0001, 1e6)
        self.conc.setDecimals(4)
        self.conc.setSuffix(" mg/mL")
        self.conc.setValue(float(ds["conc_mg_ml"]))
        self.only_sel = QCheckBox(f"Only the {n_selected} selected animal{'s' if n_selected != 1 else ''}")
        self.only_sel.setEnabled(n_selected > 0)
        self.only_sel.setChecked(n_selected > 0)
        f.addRow("Weight column", self.weight)
        f.addRow("Default dose", self.dose)
        f.addRow("Concentration", self.conc)
        f.addRow("", self.only_sel)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Calculate volumes")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def settings(self) -> dict:
        return {"weight_field": self.weight.currentText().strip() or wf.WEIGHT_FIELD,
                "dose_mg_kg": self.dose.value(), "conc_mg_ml": self.conc.value()}


class RandomiseDialog(QDialog):
    """Random allocation of animals to treatments, balanced overall and within an optional stratum (sex, …)."""

    def __init__(self, project, n_selected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Randomise treatments")
        f = QFormLayout(self)
        info = QLabel("Animals are allocated to the ticked treatments at random, in numbers that differ by at most "
                      "one. Their current treatments are replaced; retired animals are left out.")
        info.setWordWrap(True)
        f.addRow(info)
        self.groups = QListWidget()
        for g in project.groups:
            it = QListWidgetItem(color_icon(wf.display_color(project, g.name)), treatment_text(project, g.name))
            it.setData(Qt.UserRole, g.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.groups.addItem(it)
        self.groups.setMaximumHeight(140)
        self.strata = QComboBox()
        self.strata.addItem("- Nothing -", "")
        self.strata.addItem("Sex", "Sex")
        for fld in project.animal_fields:
            self.strata.addItem(fld, fld)
        self.strata.setToolTip("Spread each sex (or each value of a column such as litter or cage) evenly over the "
                               "treatments")
        self.seed = QSpinBox()
        self.seed.setRange(0, 999_999)
        self.seed.setSpecialValueText("New random order")
        self.seed.setToolTip("Enter a number to reproduce an allocation")
        self.only_sel = QCheckBox(f"Only the {n_selected} selected animal{'s' if n_selected != 1 else ''}")
        self.only_sel.setEnabled(n_selected > 0)
        self.only_sel.setChecked(n_selected > 1)
        f.addRow("Treatments", self.groups)
        f.addRow("Balance within", self.strata)
        f.addRow("Seed", self.seed)
        f.addRow("", self.only_sel)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Allocate")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def chosen_groups(self) -> list[str]:
        return [self.groups.item(i).data(Qt.UserRole) for i in range(self.groups.count())
                if self.groups.item(i).checkState() == Qt.Checked]


class CriteriaDialog(QDialog):
    """Result of evaluating the training criteria; Apply retires animals / completes stages."""

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Training criteria")
        lay = QVBoxLayout(self)
        rows = report["rows"]
        lay.addWidget(QLabel(f"{len(rows)} animal × criterion evaluation{'s' if len(rows) != 1 else ''}. "
                             f"<b>{sum(len(v) for v in report['completed'].values())}</b> stage(s) completed, "
                             f"<b>{len(report['retire'])}</b> animal(s) to retire."))
        t = QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Animal", "Criterion", "Trials", "Met at trial", "Outcome"])
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for r, row in enumerate(rows):
            outcome = ("criterion met" if row["met"] else "failed → retire" if row["failed"] and row["action"] == "retire"
                       else "failed" if row["failed"] else "in progress")
            for c, v in enumerate((row["animal"], row["criterion"], str(row["trials"]),
                                   str(row["met_at_trial"] or "–"), outcome)):
                it = QTableWidgetItem(v)
                if c == 4:
                    it.setForeground(QColor("#16a34a" if row["met"] else "#dc2626" if row["failed"] else "#475569"))
                t.setItem(r, c, it)
        lay.addWidget(t, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Apply).setToolTip("Retire failing animals (their pending tests are skipped) and "
                                                     "skip the remaining trials of completed stages")
        bb.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(760, 420)


class WeighDialog(QDialog):
    """Weigh animals: read the scale (or type the weight), record it with the date on the animal ("Weight (g)" column
    and weight history) and optionally go on to the next animal of the sheet.

    ``reader(cfg, timeout=..., stable=...)`` returns (grams, stable) (:func:`.scales.read_weight`); ``on_record(animal,
    grams)`` stores a weight."""

    def __init__(self, project, animals: list, index: int, reader, on_record, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Weigh animals")
        self.project, self.animals, self.index = project, list(animals), max(0, index)
        self.reader, self.on_record = reader, on_record
        self.recorded: list[tuple[str, float]] = []
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.animal_lbl = QLabel()
        self.animal_lbl.setStyleSheet("font-size:16px;font-weight:600;")
        self.scale = QComboBox()
        for c in scales.scale_configs(project):
            self.scale.addItem(f"{c.get('name', 'scale')} ({scales.SCALE_PROTOCOLS.get(c.get('protocol'), '?')})", c)
        self.scale.addItem("Type the weight", None)
        self.grams = QDoubleSpinBox()
        self.grams.setRange(0, 100000)
        self.grams.setDecimals(2)
        self.grams.setSuffix(" g")
        self.read_btn = QPushButton("Read scale")
        self.read_btn.clicked.connect(self.read_scale)
        self.scale.currentIndexChanged.connect(lambda _: self.read_btn.setEnabled(self.scale.currentData() is not None))
        self.read_btn.setEnabled(self.scale.currentData() is not None)
        row = QHBoxLayout()
        row.addWidget(self.grams, 1)
        row.addWidget(self.read_btn)
        self.auto_read = QCheckBox("Read the scale for the next animal")
        self.auto_read.setChecked(self.scale.currentData() is not None)
        self.state = QLabel()
        self.state.setObjectName("Hint")
        self.state.setWordWrap(True)
        f.addRow("Animal", self.animal_lbl)
        f.addRow("Scale", self.scale)
        f.addRow("Weight", row)
        f.addRow("", self.state)
        lay.addLayout(f)
        lay.addWidget(QLabel("Weight history"))
        self.history = QTableWidget(0, 2)
        self.history.setHorizontalHeaderLabels(["Date", "Weight (g)"])
        self.history.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setMinimumHeight(150)
        lay.addWidget(self.history, 1)
        lay.addWidget(self.auto_read)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        self.record_btn = bb.addButton("Record", QDialogButtonBox.ActionRole)
        self.next_btn = bb.addButton("Record && next", QDialogButtonBox.ActionRole)
        self.record_btn.clicked.connect(lambda: self.record(False))
        self.next_btn.clicked.connect(lambda: self.record(True))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(440, 460)
        self.show_animal()

    @property
    def animal(self):
        return self.animals[self.index] if 0 <= self.index < len(self.animals) else None

    def show_animal(self):
        a = self.animal
        self.animal_lbl.setText(a.id if a else "–")
        self.next_btn.setEnabled(a is not None and self.index + 1 < len(self.animals))
        self.record_btn.setEnabled(a is not None)
        hist = list(a.weights) if a else []
        self.history.setRowCount(len(hist))
        for r, w in enumerate(reversed(hist)):  # newest first
            self.history.setItem(r, 0, QTableWidgetItem(str(w.get("date", "")).replace("T", " ")))
            it = QTableWidgetItem(scales.format_grams(w.get("grams", 0)))
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.history.setItem(r, 1, it)
        self.grams.setValue(0)
        self.state.setText("")

    def read_scale(self) -> bool:
        cfg = self.scale.currentData()
        if cfg is None:
            return False
        self.state.setText("Reading the scale…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            QApplication.processEvents()
            grams, stable = self.reader(cfg, timeout=float(cfg.get("timeout_s", 5.0)), stable=True)
        except Exception as e:  # no pyserial, no port, time-out, balance error
            self.state.setText(f"<span style='color:#dc2626'>{e}</span>")
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self.grams.setValue(grams)
        self.state.setText(f"{scales.format_grams(grams)} g" + ("" if stable else " (not stable)"))
        return True

    def record(self, next_animal: bool = False) -> bool:
        a, g = self.animal, self.grams.value()
        if a is None:
            return False
        if g <= 0:
            self.state.setText("<span style='color:#dc2626'>Read the scale or type the weight first.</span>")
            return False
        self.on_record(a, g)
        self.recorded.append((a.id, g))
        if next_animal and self.index + 1 < len(self.animals):
            self.index += 1
            self.show_animal()
            if self.auto_read.isChecked() and self.scale.currentData() is not None:
                self.read_scale()
        else:
            self.show_animal()
            self.state.setText(f"Recorded {scales.format_grams(g)} g for {a.id}.")
        return True
