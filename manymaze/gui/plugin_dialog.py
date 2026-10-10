"""The settings of an analysis plug-in (Protocol ▸ Analysis ▸ Analysis plug-ins): a form built from the options the
plug-in declares (core.plugins.AnalysisPlugin.options), e.g. the CSV / TSV importer's file pattern, columns and
alignment."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ..core.plugins import analysis_plugin


class PluginOptionsDialog(QDialog):
    """Edit one configured analysis plug-in: its name, whether it is used, and its options. ``values()`` returns
    the new configuration."""

    def __init__(self, project, config: dict, parent=None):
        super().__init__(parent)
        self.project, self.config = project, dict(config)
        self.plugin = analysis_plugin(config.get("plugin", ""))
        title = self.plugin.title if self.plugin else str(config.get("plugin"))
        self.setWindowTitle(f"Analysis plug-in: {title}")
        lay = QVBoxLayout(self)
        if self.plugin is not None and self.plugin.description:
            d = QLabel(self.plugin.description)
            d.setWordWrap(True)
            d.setObjectName("Hint")
            lay.addWidget(d)
        f = QFormLayout()
        self.name = QLineEdit(str(config.get("name", "")))
        self.enabled = QCheckBox("Run this plug-in")
        self.enabled.setChecked(bool(config.get("enabled", True)))
        f.addRow("Name", self.name)
        f.addRow(self.enabled)
        self.fields: dict[str, tuple[str, QWidget]] = {}
        for opt in (self.plugin.options if self.plugin is not None else []):
            key, label, kind, default = opt[:4]
            extra = opt[4] if len(opt) > 4 else None
            value = config.get(key, default)
            w = self._widget(kind, value, extra)
            if isinstance(extra, str):
                w.setToolTip(extra)
            self.fields[key] = (kind, w)
            f.addRow(label, w.parent_row if hasattr(w, "parent_row") else w)
        lay.addLayout(f)
        if self.plugin is None:
            lay.addWidget(QLabel("This plug-in is not installed on this computer."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(560, 0)

    def _widget(self, kind: str, value, extra):
        if kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
        elif kind == "choice":
            w = QComboBox()
            for v, label in extra or []:
                w.addItem(label, v)
            w.setCurrentIndex(max(0, w.findData(value)))
        elif kind in ("number", "int"):
            w = QSpinBox() if kind == "int" else QDoubleSpinBox()
            w.setRange(-1_000_000_000, 1_000_000_000)
            if kind == "number":
                w.setDecimals(4)
            w.setValue(float(value or 0) if kind == "number" else int(value or 0))
        else:
            w = QLineEdit(str(value or ""))
            w.setMinimumWidth(300)
            if kind == "file":
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(w, 1)
                b = QPushButton("Choose…")
                b.clicked.connect(lambda _=False, w=w: self._browse(w))
                h.addWidget(b)
                w.parent_row = row
        return w

    def _browse(self, edit: QLineEdit):
        start = str(self.project.path) if self.project.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Data file", start, "Data files (*.csv *.tsv *.txt);;All (*)")
        if path:
            try:  # relative to the experiment when it is inside it
                path = str(Path(path).resolve().relative_to(Path(self.project.path).resolve()))
            except (ValueError, TypeError):
                pass
            edit.setText(path)

    def values(self) -> dict:
        out = dict(self.config)
        out["name"] = self.name.text().strip() or out.get("name", "")
        out["enabled"] = self.enabled.isChecked()
        for key, (kind, w) in self.fields.items():
            if kind == "bool":
                out[key] = w.isChecked()
            elif kind == "choice":
                out[key] = w.currentData()
            elif kind in ("number", "int"):
                out[key] = w.value()
            else:  # (text kept as typed: a name prefix may end with a space)
                out[key] = w.text().strip() if kind == "file" else w.text()
        return out
