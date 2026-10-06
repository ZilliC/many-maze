"""Base class for main-window pages and a dataclass-driven settings form."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLineEdit, QSpinBox, QWidget

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class Page(QWidget):
    """A page of the main window.

    Subclasses implement set_project() (new project loaded) and on_show()
    (page became visible: refresh from self.project).
    """

    title = "Page"
    needs_project = True

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main

    @property
    def project(self):
        return self.main.project

    def set_project(self, project):
        pass

    def on_show(self):
        pass

    def on_hide(self):
        pass


# field spec: (attribute, label, kind, options, tooltip)
#   kind: "int" (options=(min, max, step)), "float" (options=(min, max, step, decimals)),
#         "bool", "choice" (options=[(value, label), ...]), "text"
DETECTION_SPEC = [
    ("method", "Detection method", "choice", [("background", "Background"),
                                               ("threshold", "Threshold")],
     "Background subtraction compares each frame with an empty-arena model; thresholding uses absolute grey level."),
    ("contrast", "Animal is", "choice", [("auto", "Darker or lighter"), ("dark", "Darker"), ("light", "Lighter")],
     "Contrast of the animal against the background (e.g. dark mouse on white floor = Darker)."),
    ("threshold", "Threshold", "int", (0, 255, 1), "Grey-level difference that counts as animal. 0 = automatic (Otsu)."),
    ("background", "Background model", "choice", [("median", "Median of frames"),
                                                   ("frame", "Empty-arena frame"),
                                                   ("adaptive", "Adaptive")],
     "Median of frames sampled through the test (animal must move); a chosen frame showing the empty arena; "
     "or an adaptive model for live cameras and changing light."),
    ("background_frame", "Background frame #", "int", (0, 10_000_000, 1), "Frame used when the model is 'Empty-arena frame'."),
    ("background_samples", "Frames sampled", "int", (3, 501, 2), "Frames used for the median background."),
    ("min_area_px", "Min animal area (px²)", "int", (1, 1_000_000, 10), "Smaller blobs are ignored."),
    ("max_area_px", "Max animal area (px²)", "int", (0, 10_000_000, 100), "Larger blobs are ignored. 0 = no limit."),
    ("blur", "Blur (px)", "int", (0, 31, 2), "Gaussian blur before detection (noise reduction)."),
    ("morph_open", "Remove specks (px)", "int", (0, 31, 2), "Morphological opening kernel."),
    ("morph_close", "Fill holes (px)", "int", (0, 51, 2), "Morphological closing kernel."),
    ("head_tail", "Detect head and tail", "bool", None, ""),
    ("tail_strip", "Tail removal strength", "float", (0.0, 1.0, 0.05, 2),
     "Opening kernel relative to body size used to strip the tail before locating head/tail."),
    ("motion_threshold", "Motion threshold (grey)", "int", (1, 255, 1),
     "Pixel change counted as movement for freezing / immobility."),
    ("max_gap_s", "Interpolate gaps up to (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("smoothing", "Position smoothing (frames)", "int", (0, 51, 1), "Moving average applied to positions. 0 = off."),
    ("frame_step", "Analyse every Nth frame", "int", (1, 50, 1), "Speed up tracking of high frame-rate video."),
]

ANALYSIS_SPEC = [
    ("speed_smoothing_s", "Speed smoothing (s)", "float", (0.0, 5.0, 0.05, 2),
     "Positions are averaged over this window before distance and speed are computed."),
    ("mobility_threshold", "Immobile below (units/s)", "float", (0.0, 100.0, 0.1, 2), ""),
    ("min_immobile_s", "Min immobile episode (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("freeze_on_pct", "Freezing starts below (% body)", "float", (0.0, 100.0, 0.1, 2),
     "Pixel change, as a % of the animal's area, under which freezing begins."),
    ("freeze_off_pct", "Freezing ends above (% body)", "float", (0.0, 100.0, 0.1, 2), ""),
    ("min_freeze_s", "Min freezing episode (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("zone_body_part", "Zone occupancy uses", "choice", [("centre", "Centre of body"), ("head", "Head"),
                                                         ("tail", "Tail base")], ""),
    ("entry_min_duration_s", "Ignore visits shorter than (s)", "float", (0.0, 60.0, 0.05, 2), ""),
    ("count_initial_entry", "Starting in a zone counts as entry", "bool", None, ""),
    ("latency_if_never", "Latency if never occurs", "choice", [("duration", "Test duration"), ("blank", "Blank")], ""),
    ("thigmotaxis_distance", "Thigmotaxis band (units)", "float", (0.0, 1000.0, 0.5, 2),
     "Distance from the arena wall counted as thigmotaxis. 0 = 25 % of the arena half-width."),
    ("exploration_facing_deg", "Exploring = facing within (°)", "float", (0.0, 180.0, 5.0, 1),
     "Head must point to the object within this angle (NOR / object exploration)."),
    ("grid_cells", "Open-field grid (N×N)", "int", (0, 20, 1), "Grid used for line-crossing counts. 0 = off."),
    ("contact_distance", "Social contact distance (units)", "float", (0.0, 1000.0, 0.5, 2), "0 = one body length."),
    ("bin_length_s", "Time bin length (s)", "float", (0.0, 100000.0, 10.0, 1), "0 = no time bins."),
    ("novel_object", "Novel object (default)", "text", None, "Name of the point that is the novel object."),
    ("social_side", "Social stimulus side (default)", "choice", [("Left", "Left"), ("Right", "Right")], ""),
]


class SettingsForm(QWidget):
    """Edits attributes of a settings object according to a spec list."""

    changed = Signal()

    def __init__(self, spec, obj=None, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.obj = obj
        self.editors: dict[str, QWidget] = {}
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for attr, label, kind, opts, tip in spec:
            if kind == "int":
                w = QSpinBox()
                w.setRange(opts[0], opts[1])
                w.setSingleStep(opts[2])
                w.valueChanged.connect(self._emit)
            elif kind == "float":
                w = QDoubleSpinBox()
                w.setRange(opts[0], opts[1])
                w.setSingleStep(opts[2])
                w.setDecimals(opts[3])
                w.valueChanged.connect(self._emit)
            elif kind == "bool":
                w = QCheckBox()
                w.toggled.connect(self._emit)
            elif kind == "choice":
                w = QComboBox()
                w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                w.setMinimumContentsLength(8)
                for v, lbl in opts:
                    w.addItem(lbl, v)
                w.currentIndexChanged.connect(self._emit)
            else:
                w = QLineEdit()
                w.editingFinished.connect(self._emit)
            if tip:
                w.setToolTip(tip)
            self.editors[attr] = w
            form.addRow(label, w)
        self._loading = False
        if obj is not None:
            self.load(obj)

    def load(self, obj):
        self.obj = obj
        self._loading = True
        for attr, _, kind, _, _ in self.spec:
            w = self.editors[attr]
            v = getattr(obj, attr)
            if kind in ("int", "float"):
                w.setValue(v)
            elif kind == "bool":
                w.setChecked(bool(v))
            elif kind == "choice":
                i = w.findData(v)
                w.setCurrentIndex(max(0, i))
            else:
                w.setText(str(v))
        self._loading = False

    def _emit(self, *_):
        if self._loading or self.obj is None:
            return
        self.store(self.obj)
        self.changed.emit()

    def store(self, obj):
        for attr, _, kind, _, _ in self.spec:
            w = self.editors[attr]
            if kind in ("int", "float"):
                setattr(obj, attr, w.value())
            elif kind == "bool":
                setattr(obj, attr, w.isChecked())
            elif kind == "choice":
                setattr(obj, attr, w.currentData())
            else:
                setattr(obj, attr, w.text())
