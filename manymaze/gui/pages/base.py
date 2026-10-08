"""Base class for main-window pages and a dataclass-driven settings form."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox,
                               QVBoxLayout, QWidget)

from ..widgets import loading

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
    ("method", "Detect the animal using", "choice", [("background", "Background subtraction"),
                                                     ("threshold", "A grey-level threshold"),
                                                     ("colour", "Its colour (or a colour mark)")],
     "Background subtraction compares each frame with an empty-arena model; thresholding uses absolute grey level; "
     "colour finds the pixels of the chosen colour (a coloured animal, dye mark, collar or LED)."),
    ("contrast", "Compared with the background the animal is", "choice",
     [("auto", "Darker or lighter"), ("dark", "Darker"), ("light", "Lighter")],
     "Contrast of the animal against the background (e.g. dark mouse on white floor = Darker)."),
    ("threshold", "Detection threshold (0 = automatic)", "int", (0, 255, 1),
     "Grey-level difference that counts as animal. 0 = automatic (Otsu)."),
    ("background", "Build the background from", "choice", [("median", "The median of sampled frames"),
                                                            ("frame", "An empty-arena frame"),
                                                            ("adaptive", "An adaptive model")],
     "Median of frames sampled through the test (animal must move); a chosen frame showing the empty arena; "
     "or an adaptive model for live cameras and changing light."),
    ("background_frame", "Empty-arena frame number", "int", (0, 10_000_000, 1),
     "Frame used when the background is built from an empty-arena frame."),
    ("background_samples", "Frames sampled for the median", "int", (3, 501, 2),
     "Frames used for the median background."),
    ("target_colour", "Colour of the animal or mark (#rrggbb)", "text", None,
     "Used when the animal is detected by its colour, e.g. #ff0000 for a red mark."),
    ("colour_tolerance", "Colour tolerance (hue, degrees)", "int", (1, 90, 1),
     "How far the hue may differ from the colour (shadows and lighting change it a little)."),
    ("min_saturation", "Ignore pixels greyer than (saturation 0–255)", "int", (0, 255, 5),
     "White, grey and black pixels have no colour; raise this if pale areas are detected."),
    ("identity_colours", "Identify several animals by colour marks", "text", None,
     "One colour per animal in the arena, in order, e.g. “#ff0000, #0000ff” (first animal red, second blue). "
     "Each animal is the blob carrying most of its colour, so identities never swap. Empty = by position."),
    ("min_area_px", "Ignore objects smaller than (px²)", "int", (1, 1_000_000, 10), "Smaller blobs are ignored."),
    ("max_area_px", "Ignore objects larger than (px², 0 = no limit)", "int", (0, 10_000_000, 100),
     "Larger blobs are ignored. 0 = no limit."),
    ("blur", "Blur the image by (px)", "int", (0, 31, 2), "Gaussian blur before detection (noise reduction)."),
    ("morph_open", "Remove specks up to (px)", "int", (0, 31, 2), "Morphological opening kernel."),
    ("morph_close", "Fill holes up to (px)", "int", (0, 51, 2), "Morphological closing kernel."),
    ("erase_thin_px", "Erase thin wires and bars up to (px)", "int", (0, 31, 1),
     "Removes thin structures up to this width — wires, tubes, cage bars — from the image before detection, so "
     "they neither split the animal nor are mistaken for it. Also removes thin parts of the animal (tail). 0 = off."),
    ("head_tail", "Detect the head and the tail", "bool", None, ""),
    ("tail_strip", "Tail removal strength", "float", (0.0, 1.0, 0.05, 2),
     "Opening kernel relative to body size used to strip the tail before locating head/tail."),
    ("record_outline", "Record the animal's whole-body outline", "bool", None,
     "Stores a simplified outline of the animal in every frame, drawn when the test is reviewed or exported "
     "as a video."),
    ("body_parts", "Locate the body parts using", "choice", [("contour", "The animal's shape"),
                                                              ("pose", "A pose model (AI)")],
     "Animal shape: head and tail from the blob outline (fast, no model needed). Pose model: a deep-learning "
     "keypoint model locates nose, body centre and tail base (more robust to shadows, reflections and poor "
     "contrast; runs on the Neural Engine / GPU on Apple Silicon). Install the model below first."),
    ("pose_min_conf", "Minimum keypoint confidence", "float", (0.0, 1.0, 0.05, 2),
     "Pose keypoints below this confidence fall back to the animal-shape estimate."),
    ("pose_device", "Run the pose model on", "choice", [("auto", "The fastest device available"), ("cpu", "The CPU")],
     "Neural Engine / GPU uses Core ML on macOS (CUDA on NVIDIA PCs); falls back to the CPU automatically."),
    ("motion_threshold", "A pixel is moving when it changes by (grey levels)", "int", (1, 255, 1),
     "Pixel change counted as movement for freezing / immobility."),
    ("max_gap_s", "Fill gaps in the track of up to (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("smoothing", "Smooth positions over (frames, 0 = off)", "int", (0, 51, 1),
     "Moving average applied to positions. 0 = off."),
    ("frame_step", "Analyse every Nth frame", "int", (1, 50, 1), "Speed up tracking of high frame-rate video."),
]

ANALYSIS_SPEC = [
    ("end_zone", "End the test when the animal stays in zone", "text", None,
     "Name of a zone or zone group, e.g. Platform (water maze) or Escape box (Barnes maze). Everything after the "
     "end is left out of the results. Empty = the test runs for its whole duration."),
    ("end_zone_s", "… for at least (s)", "float", (0.0, 600.0, 0.5, 1),
     "How long the animal must stay in the zone; 0 = the test ends on entering it."),
    ("speed_smoothing_s", "Smooth positions for distance and speed over (s)", "float", (0.0, 5.0, 0.05, 2),
     "Positions are averaged over this window before distance and speed are computed."),
    ("mobility_threshold", "The animal is immobile below (units/s)", "float", (0.0, 100.0, 0.1, 2), ""),
    ("min_immobile_s", "Shortest immobility episode (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("freeze_threshold_mode", "Freezing thresholds", "choice",
     [("manual", "Set manually (below)"), ("auto", "Automatic, from the motion of each test")],
     "Automatic: the start / end thresholds are derived from the distribution of the motion index of each test "
     "(the still and moving frames are separated on a log scale), adjusted by the sensitivity. Manual: the two "
     "thresholds below."),
    ("freeze_sensitivity", "Automatic threshold sensitivity (0–100)", "float", (0.0, 100.0, 5.0, 0),
     "50 = the threshold separating still from moving frames; higher values count more frames as freezing "
     "(+25 doubles the threshold), lower values fewer."),
    ("freeze_on_pct", "Freezing starts when movement falls below (% of body)", "float", (0.0, 100.0, 0.1, 2),
     "Pixel change, as a % of the animal's area, under which freezing begins."),
    ("freeze_off_pct", "Freezing ends when movement rises above (% of body)", "float", (0.0, 100.0, 0.1, 2), ""),
    ("min_freeze_s", "Shortest freezing episode (s)", "float", (0.0, 60.0, 0.1, 2), ""),
    ("activity_threshold_pct", "The animal is active when movement reaches (% of body)", "float",
     (0.0, 1000.0, 0.5, 2),
     "Activity comes from the pixels that change between frames (as a % of the animal's area), not from its "
     "speed: an animal grooming in place is active but immobile."),
    ("min_inactive_s", "Shortest inactive episode (s)", "float", (0.0, 60.0, 0.1, 2),
     "Inactive episodes shorter than this count as active."),
    ("rearing", "Detect rearing automatically", "bool", None,
     "Rears are detected from the animal's shape: seen from above, an animal standing on its hind legs looks "
     "smaller and shorter. Adds rear count, time, latency and durations, overall and per zone."),
    ("rear_area_pct", "A rear makes the body area fall below (% of usual)", "float", (1.0, 100.0, 5.0, 0),
     "The usual area is the animal's median area over the test."),
    ("rear_length_pct", "… and the head–tail length below (% of usual)", "float", (1.0, 100.0, 5.0, 0),
     "Used when the head and tail are tracked (from the animal's shape or the pose model)."),
    ("min_rear_s", "Shortest rear (s)", "float", (0.0, 60.0, 0.1, 2), "Shorter rears are ignored."),
    ("zone_body_part", "Decide whether the animal is in a zone using its", "choice",
     [("centre", "Centre of body"), ("head", "Head"), ("tail", "Tail base"), ("body", "Proportion of the body")],
     "Default entry rule; each zone can override it in the apparatus designer."),
    ("body_proportion_pct", "Part of the body that must be in the zone (%)", "float", (1.0, 100.0, 5.0, 0),
     "For the 'Proportion of the body' rule: the animal (an ellipse from head, tail and area) is in a zone when "
     "at least this much of it is inside."),
    ("hidden_zone_margin", "A lost animal is in a hidden zone within (units)", "float", (0.0, 1000.0, 0.5, 2),
     "An animal lost within this distance of a hidden zone (nest, tunnel) is in that zone. 0 = automatic."),
    ("entry_min_duration_s", "Ignore zone visits shorter than (s)", "float", (0.0, 60.0, 0.05, 2), ""),
    ("count_initial_entry", "Count starting the test in a zone as an entry", "bool", None, ""),
    ("latency_if_never", "When an event never occurs, its latency is", "choice",
     [("duration", "The test duration"), ("blank", "Left blank")], ""),
    ("thigmotaxis_distance", "Thigmotaxis band next to the wall (units)", "float", (0.0, 1000.0, 0.5, 2),
     "Distance from the arena wall counted as thigmotaxis. 0 = 25 % of the arena half-width."),
    ("exploration_facing_deg", "Exploring means facing the object within (°)", "float", (0.0, 180.0, 5.0, 1),
     "Head must point to the object within this angle (NOR / object exploration)."),
    ("grid_cells", "Grid for line crossings (N×N, 0 = off)", "int", (0, 20, 1),
     "Grid used for line-crossing counts. 0 = off."),
    ("contact_distance", "Social contact within (units)", "float", (0.0, 1000.0, 0.5, 2), "0 = one body length."),
    ("nose_contact_distance", "Nose contact within (units)", "float", (0.0, 1000.0, 0.5, 2),
     "Nose-to-nose / nose-to-body contact distance. 0 = a quarter of a body length."),
    ("follow_distance", "Following within (units)", "float", (0.0, 1000.0, 0.5, 2),
     "Max distance for one animal to be following another. 0 = two body lengths."),
    ("arena_quadrants", "Report the time in each quadrant of the arena", "bool", None,
     "Report the time in each quarter of the arena."),
    ("behaviour_by_zone", "Split the scored behaviours by zone", "bool", None,
     "Split manually scored behaviours (count, duration, latency, bouts) by the zone the animal was in."),
    ("whishaw_width", "Whishaw corridor width (units)", "float", (0.0, 1000.0, 1.0, 1),
     "Water maze corridor from the release point to the platform. 0 = 20 cm."),
    ("paired_chamber", "Drug-paired chamber (place preference)", "text", None,
     "Conditioned place preference: drug-paired chamber."),
    ("bin_length_s", "Split each test into time bins of (s, 0 = off)", "float", (0.0, 100000.0, 10.0, 1),
     "0 = no time bins."),
    ("novel_object", "The novel object is the point named", "text", None, "Name of the point that is the novel object."),
    ("social_side", "The social stimulus is on the", "choice", [("Left", "Left"), ("Right", "Right")], ""),
    ("io_baseline_s", "Analogue inputs: baseline period (s)", "float", (0.0, 100000.0, 1.0, 1),
     "The baseline of an analogue input is its mean over the first seconds of the test (or period). 0 = off."),
    ("io_deviation_sd", "Analogue inputs: a deviation is more than (baseline SD)", "float", (0.0, 100.0, 0.5, 2),
     "The signal deviates from the baseline when it is further from it than this many baseline SDs."),
    ("opad_contact", "OPAD: paw contact input", "text", None,
     "Operant plantar assay: name of the digital input of the paw contact with the thermal plate. Empty = no "
     "OPAD measures."),
    ("opad_lick", "OPAD: lick input", "text", None, "Name of the digital input of the lickometer."),
    ("opad_temperature", "OPAD: temperature input", "text", None,
     "Name of the analogue input of the plate temperature."),
    ("opad_temperatures", "OPAD: temperatures of interest", "text", None,
     "Comma-separated temperatures, e.g. 10, 45: time in contact, contacts made / broken and licks at each."),
    ("opad_tolerance", "OPAD: at a temperature within ±", "float", (0.0, 100.0, 0.5, 2),
     "The plate is at a temperature of interest when within this of it."),
]


# sections of the protocol's property pages: (heading, [attribute, ...]). Attributes of a spec that are not listed
# here are shown in the last section, so new spec entries always appear.
DETECTION_SECTIONS = [
    ("Detection", ["method", "contrast", "threshold", "background", "background_frame", "background_samples",
                   "min_area_px", "max_area_px"]),
    ("Colour", ["target_colour", "colour_tolerance", "min_saturation", "identity_colours"]),
    ("Body parts", ["head_tail", "tail_strip", "record_outline", "body_parts", "pose_min_conf", "pose_device"]),
    ("Clean-up", ["blur", "morph_open", "morph_close", "erase_thin_px"]),
    ("Tracking quality", ["motion_threshold", "max_gap_s", "smoothing", "frame_step"]),
]

ANALYSIS_SECTIONS = [
    ("Movement", ["speed_smoothing_s", "mobility_threshold", "min_immobile_s"]),
    ("Freezing", ["freeze_threshold_mode", "freeze_sensitivity", "freeze_on_pct", "freeze_off_pct",
                  "min_freeze_s"]),
    ("Activity", ["activity_threshold_pct", "min_inactive_s"]),
    ("Rearing", ["rearing", "rear_area_pct", "rear_length_pct", "min_rear_s"]),
    ("Zones", ["zone_body_part", "body_proportion_pct", "hidden_zone_margin", "entry_min_duration_s",
               "count_initial_entry", "latency_if_never"]),
    ("Test-specific measures", ["thigmotaxis_distance", "exploration_facing_deg", "grid_cells", "contact_distance",
                                "nose_contact_distance", "follow_distance", "arena_quadrants", "behaviour_by_zone",
                                "whishaw_width", "paired_chamber", "novel_object", "social_side"]),
    ("I/O measures", ["io_baseline_s", "io_deviation_sd", "opad_contact", "opad_lick", "opad_temperature",
                      "opad_temperatures", "opad_tolerance"]),
    ("Test end", ["end_zone", "end_zone_s"]),
    ("Time bins", ["bin_length_s"]),
]

FIELD_WIDTH = 220  # minimum width of the inputs (ANY-maze property pages have wide inputs)


def section_title(text: str) -> QLabel:
    """A flat blue section heading (ANY-maze property page style)."""
    lbl = QLabel(text)
    lbl.setObjectName("SectionTitle")
    return lbl


def property_form() -> QFormLayout:
    """A form laid out like an ANY-maze property page: sentence-style labels on the left, wide inputs on the
    right, roomy rows."""
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    form.setHorizontalSpacing(28)
    form.setVerticalSpacing(9)
    form.setContentsMargins(0, 4, 0, 6)
    return form


class SettingsForm(QWidget):
    """Edits attributes of a settings object according to a spec list.

    Labels read as sentences ("Ignore objects smaller than (px²)") with wide inputs on the right; check boxes carry
    their sentence themselves. Without sections the form is compact (narrow inputs, long rows wrap) for side
    panels. With ``sections`` ([(heading, [attr, ...]), ...]) the rows are grouped under blue
    section headings, and ``add_to_section(heading, widget)`` appends extra widgets (e.g. the pose model box) to a
    section."""

    changed = Signal()

    def __init__(self, spec, obj=None, parent=None, sections=None, field_width: int | None = None):
        super().__init__(parent)
        self.spec = spec
        self.obj = obj
        self.editors: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        self.forms: dict[str, QFormLayout] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        compact = not sections  # e.g. a narrow side panel: long rows wrap (input under its label)
        if field_width is None:
            field_width = 140 if compact else FIELD_WIDTH
        by_attr = {s[0]: s for s in spec}
        groups: list[tuple[str, list]] = []
        if sections:
            seen = set()
            for title, attrs in sections:
                rows = [by_attr[a] for a in attrs if a in by_attr and a not in seen]
                seen.update(a for a in attrs)
                groups.append((title, rows))
            rest = [s for s in spec if s[0] not in seen]
            if rest:
                groups[-1] = (groups[-1][0], groups[-1][1] + rest)
        else:
            groups = [("", list(spec))]
        for title, rows in groups:
            if title:
                lay.addWidget(section_title(title))
            form = property_form()
            if compact:
                form.setRowWrapPolicy(QFormLayout.WrapLongRows)
                form.setHorizontalSpacing(16)
            lay.addLayout(form)
            self.forms[title] = form
            for row in rows:
                self._add_row(form, row, field_width)
        lay.addStretch()
        # align the input column of every section
        widths = [lbl.sizeHint().width() for lbl in self.labels.values()]
        if widths and not compact:
            for lbl in self.labels.values():
                lbl.setMinimumWidth(max(widths))
        self._loading = False
        if obj is not None:
            self.load(obj)

    def _add_row(self, form: QFormLayout, row, field_width: int):
        attr, label, kind, opts, tip = row
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
            w = QCheckBox(label)
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
        if kind == "bool":
            form.addRow(w)
            return
        w.setMinimumWidth(field_width)
        lbl = QLabel(label)
        if tip:
            lbl.setToolTip(tip)
        lbl.setBuddy(w)
        self.labels[attr] = lbl
        form.addRow(lbl, w)

    def add_to_section(self, title: str, widget: QWidget):
        """Append a full-width widget to the rows of a section (the last section if `title` is unknown)."""
        form = self.forms.get(title) or list(self.forms.values())[-1]
        form.addRow(widget)

    def load(self, obj):
        self.obj = obj
        with loading(self):
            for attr, _, kind, _, _ in self.spec:
                w = self.editors[attr]
                v = getattr(obj, attr)
                if kind in ("int", "float"):
                    w.setValue(v)
                elif kind == "bool":
                    w.setChecked(bool(v))
                elif kind == "choice":
                    w.setCurrentIndex(max(0, w.findData(v)))
                else:
                    w.setText(str(v))

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
