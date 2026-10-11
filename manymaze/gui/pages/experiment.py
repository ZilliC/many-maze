"""Protocol page: the experiment's protocol elements, one property page at a time as in ANY-maze.

The elements (Protocol, Animal tracking, Stages, Keys, Procedures, Analysis, Calculations, Hardware) are listed in
the explorer
under "Protocol" (``explorer_items`` / ``show_item``) and shown in an internal stack. Every widget of the old
single-form page is kept as an attribute (``name``, ``det_form``, ``beh``, ``crit``, ``ev_periods`` …)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QSpinBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import ioconfig, pose, security
from ...core import workflow as wf
from ...core.ioconfig import PRESET_KEY
from ...core.measures import AnalysisSettings
from ...core.periods import ANCHORS, END_ANCHORS, TARGET_KEY, check_periods
from ...core.project import ERROR_COLUMN, Behaviour, result_columns
from ...core.sync import sync_from
from ...core.template_measures import FST_TEMPLATES
from ...core.templates import TEMPLATES
from ...core.terminology import TERMS, term, terminology_from
from ...core.tracking import DetectionSettings
from .. import theme
from ..icons import icon
from ..pose_model import PoseModelBox
from ..widgets import RecordTable, button_row, hint, loading, run_with_progress, separator, style_table
from ._results_cache import get_rows, info_columns
from .base import (ANALYSIS_SECTIONS, ANALYSIS_SPEC, DETECTION_SECTIONS, DETECTION_SPEC, FST_FIELDS, FST_SECTION,
                   FST_SPEC, PRESET_TIP, AnimalPresetCombo, Page, SettingsForm, apply_preset_to_form, property_form)
from .experiment_calcs import CalculationsMixin, PluginsMixin
from .experiment_keys import KeysMixin
from .protocol_pages import CalculationEditor, ElementPage, KeyEditor, small_button
from .results.dialogs import MeasurePickerDialog, measure_groups
from .results.table import _names

# protocol elements: (key, explorer label, icon)
ELEMENTS = [("protocol", "Protocol", "protocol"), ("tracking", "Animal tracking", "tracking"),
            ("stages", "Stages", "stages"), ("keys", "Keys", "key"), ("procedures", "Procedures", "procedure"),
            ("analysis", "Analysis", "chart"), ("calculations", "Calculations", "calculator"),
            ("hardware", "Hardware", "plug")]
MODES = [("tracking", "Video tracking — the animal is tracked and keys can be scored"),
         ("takenote", "TakeNote — behaviours are scored by hand only (no tracking)"),
         ("io_only", "Input/output only — tests run with the I/O devices and procedures (no video)")]
MET_ACTIONS = [("complete_stage", "Stage completed: skip remaining trials"), ("report", "Report only")]
FORM_WIDTH = 900  # property pages with only settings stay at a readable width
LOCKED_TEXT = ("The protocol is locked: only an administrator can change it (File ▸ Users and security). You can look "
               "at it and run tests.")


# record tables (see RecordTable): training criteria, time periods, event-anchored time periods
CRITERIA_COLS = [("stage", "Stage", "text_choice", None),  # options: the stages, given by the page
                 ("measure", "Measure", "text", None),
                 ("op", "Is", "choice", [(o, o) for o in ("<", "<=", ">", ">=")] + [("any", "any")]),
                 ("value", "Value", "number", None),
                 ("consecutive_trials", "Consecutive trials", "spin", (1, wf.MAX_TRIALS)),
                 ("action_met", "When met", "choice", MET_ACTIONS),
                 ("after", "Retire after", "spin", (0, wf.MAX_TRIALS, "never", " trials")),
                 ("min_trials", "Minimum trials", "spin", (0, wf.MAX_TRIALS, "none", "")),
                 # the acceptable variability (hidden: edited in the Variability row under the table)
                 ("var_stat", "Variability", "choice", [("", "none")] + list(wf.VARIABILITY_STATS.items())),
                 ("var_measure", "Variability of", "text", None),
                 ("var_trials", "Variability over", "spin", (2, wf.MAX_TRIALS)),
                 ("var_max", "Variability at most", "number", None)]
_VAR_COLS = {c[0]: i for i, c in enumerate(CRITERIA_COLS) if c[0].startswith("var_")}
PERIOD_COLS = [("label", "Time period", "text", None), ("start", "Starts at (s)", "number", None),
               ("end", "Ends at (s)", "number", None)]
EVENT_PERIOD_COLS = [("label", "Time period", "text", None),
                     ("anchor", "The period starts at", "choice", list(ANCHORS.items())),
                     ("target", "Zone / key / input / calculation", "text", None),
                     ("offset_s", "Offset (s)", "number", None), ("duration_s", "Duration (s)", "number", None),
                     ("occurrence", "Occurrence", "int", None),
                     ("end_anchor", "The period ends", "choice", list(END_ANCHORS.items())),
                     ("end_target", "Ends at zone / key / input / calculation", "text", None),
                     ("end_offset_s", "End offset (s)", "number", None),
                     ("end_occurrence", "End occurrence", "int", None)]
_TARGET_KEY = {k: v for k, v in TARGET_KEY.items() if k in ANCHORS}


class ExperimentPage(CalculationsMixin, PluginsMixin, KeysMixin, Page):
    title = "Protocol"

    def __init__(self, main):
        super().__init__(main)
        self._loading = False
        self._quiet_show = False
        self.element = "protocol"
        self._key_names: list[str] = []  # name of each row of the keys table as last stored (renames follow it)
        self._stage_names: list[str] = []  # stage of each line of the stages box as last stored
        self._period_table = None  # the time-period table last worked in (see delete_time_period)
        self._calc_rows: list[dict] | None = None  # results rows the calculations are checked against …
        self._calc_measures: list[str] | None = None  # … and their columns that are not calculations
        self.stack = QStackedWidget()
        self.elements: dict[str, ElementPage] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lock_lbl = QLabel(LOCKED_TEXT)
        self.lock_lbl.setWordWrap(True)
        theme.style(self.lock_lbl, lambda: f"background:{theme.NOTE_BG};border-bottom:1px solid {theme.NOTE_BORDER};"
                                          "padding:6px 28px;")
        self.lock_lbl.hide()
        lay.addWidget(self.lock_lbl)
        lay.addWidget(self.stack)
        self._build_protocol()
        self._build_tracking()
        self._build_stages()
        self._build_keys()
        self._build_procedures()
        self._build_analysis()
        self._build_calculations()
        self._build_hardware()
        self._build_actions()
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._focus_changed)

    def _element_page(self, key: str, *args, **kw) -> ElementPage:
        pg = ElementPage(*args, **kw)
        self.elements[key] = pg
        self.stack.addWidget(pg)
        return pg

    # ================================================================== element pages
    def _build_protocol(self):
        pg = self._element_page("protocol", "Protocol", "The protocol defines how every test of the experiment is "
                                "run. Its elements are listed on the left under Protocol; the apparatus is drawn "
                                "on the Apparatus page.", FORM_WIDTH)
        f = property_form()
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._store)
        self.desc = QPlainTextEdit()
        self.desc.setFixedHeight(76)
        self.desc.setPlaceholderText("What the experiment tests, the animals and the conditions")
        self.desc.textChanged.connect(self._store)
        self.protocol = QComboBox()
        for k, t in TEMPLATES.items():
            self.protocol.addItem(t.title, k)
        self.protocol.setToolTip("The type of test: selects the measures reported and the apparatus template")
        self.protocol.currentIndexChanged.connect(self._store)
        self.mode = QComboBox()
        for v, text in MODES:
            self.mode.addItem(text, v)
        self.mode.setToolTip("TakeNote mode is for experiments scored by hand: tests are scored with keys while "
                             "watching the video or the animal, without tracking. Input/output only (e.g. operant "
                             "chambers) runs the tests with the I/O devices and the procedures, without a camera.")
        self.mode.currentIndexChanged.connect(self._store_mode)
        for w in (self.name, self.desc, self.protocol, self.mode):
            w.setMinimumWidth(320)
        # Input/output only mode: the I/O devices of the operant chambers from a preset
        self.chambers_lbl = hint("")
        self.chambers_btn = small_button("Operant chambers…", "plug", slot=self.set_up_chambers,
                                         tip="Set up the I/O devices of the chambers from a preset: levers, nose "
                                             "pokes, lights, pellet dispenser, house light, shocker")
        self.chambers_row = QWidget()
        h = QHBoxLayout(self.chambers_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.chambers_lbl, 1)
        h.addWidget(self.chambers_btn, 0, Qt.AlignTop)
        f.addRow("Protocol name", self.name)
        f.addRow("Description", self.desc)
        f.addRow("Type of test", self.protocol)
        f.addRow("Mode", self.mode)
        f.addRow("Chambers", self.chambers_row)
        self.protocol_form = f
        pg.add(f)
        pg.add(separator())

        pg.section("Tests")
        f = property_form()
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 1e6)
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText("Until the end of the video")
        self.duration.setToolTip("Test duration. 0 = until the end of the video.")
        self.duration.valueChanged.connect(self._store)
        self.start_mode = QComboBox()
        self.start_mode.addItem("At the test start time set for each test", "manual")
        self.start_mode.addItem("When the animal is first detected in the apparatus", "on_detection")
        self.start_mode.addItem("When the experimenter's hand has left the image", "experimenter_leaves")
        self.start_mode.currentIndexChanged.connect(self._store)
        for w in (self.duration, self.start_mode):
            w.setMinimumWidth(320)
        f.addRow("Each test lasts", self.duration)
        f.addRow("Tests start", self.start_mode)
        pg.add(f)
        pg.add(separator())

        # forced swim / tail suspension: shown for those types of test (the same settings are under Analysis)
        self.fst_box = QWidget()
        lay = QVBoxLayout(self.fst_box)
        lay.setContentsMargins(0, 0, 0, 0)
        self.fst_form = SettingsForm(FST_SPEC, sections=[(FST_SECTION, FST_FIELDS)])
        self.fst_form.changed.connect(self._fst_changed)
        lay.addWidget(self.fst_form)
        lay.addWidget(hint("As ANY-maze's Forced swim / Tail suspension mode: the animal is immobile once it has "
                           "stopped struggling, judged from the quick movements in the image, wherever it is. "
                           "Film it from the side; the Struggle index chart of a test helps to set the threshold."))
        lay.addWidget(separator())
        self.fst_box.hide()
        pg.add(self.fst_box)

        pg.section("Testing")
        self.blind = QCheckBox("Blind testing — hide the treatments (shown as codes) while testing and scoring")
        self.blind.setToolTip("Hide treatment groups (shown as codes) while testing and scoring")
        self.blind.toggled.connect(self._blind_toggled)
        self.confirm_id = QCheckBox("Confirm the animal's ID before each test")
        self.confirm_id.setToolTip("Scan the barcode / microchip or type the ID; a mismatch blocks the test")
        self.confirm_id.toggled.connect(self._store_workflow)
        self.weigh_first = QCheckBox("Weigh the animal before each live test (when a balance is connected)")
        self.weigh_first.setToolTip("A live test is armed only once its animal has a weight of today: the Weigh "
                                    "dialog opens for it. Needs a balance among the I/O devices.")
        self.weigh_first.toggled.connect(self._store_weigh)
        pg.add(self.blind)
        pg.add(self.confirm_id)
        pg.add(self.weigh_first)
        pg.body.addSpacing(10)
        self.summary_lbl = hint("")
        pg.add(self.summary_lbl)
        pg.add(separator())

        pg.section("Terminology")
        pg.add(hint("The words this experiment uses for animals, treatments, tests … — in the window, the results "
                    "and the exported files (e.g. Subject, Condition, Session). Leave a term blank to keep "
                    "mANY-MAZE's word; the plural is filled in for you. Measure names and formulas are not renamed."))
        self.terms = QTableWidget(len(TERMS), 3)
        self.terms.setHorizontalHeaderLabels(["Term", "Called", "Plural"])
        self.terms.verticalHeader().hide()
        self.terms.setMaximumWidth(560)
        self.terms.verticalHeader().setDefaultSectionSize(26)
        self.terms.setFixedHeight(26 * len(TERMS) + 44)
        self.terms.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for c, w in ((0, 160), (1, 190), (2, 190)):
            self.terms.setColumnWidth(c, w)
        for r, (one, many) in enumerate(TERMS.values()):
            it = QTableWidgetItem(one)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it.setToolTip(f"mANY-MAZE's word: {one} / {many}")
            self.terms.setItem(r, 0, it)
        self.terms.itemChanged.connect(self._store_terminology)
        pg.add(self.terms)
        pg.finish()

    def _build_tracking(self):
        pg = self._element_page("tracking", "Animal tracking", "How the animal is found in every frame. These are "
                                "the defaults of all tests; a test can override them on the Review and score page.",
                                FORM_WIDTH)
        self.takenote_lbl = QLabel("This protocol uses TakeNote mode: tests are scored by hand and these settings "
                                   "are only used if you track a test anyway.")
        self.takenote_lbl.setWordWrap(True)
        theme.style(self.takenote_lbl, lambda: f"background:{theme.NOTE_BG};border:1px solid {theme.NOTE_BORDER};"
                    "padding:6px 8px;")
        self.takenote_lbl.hide()
        pg.add(self.takenote_lbl)
        self.det_form = SettingsForm(DETECTION_SPEC, sections=DETECTION_SECTIONS)
        self.det_form.changed.connect(self.main.mark_dirty)
        self.det_form.changed.connect(self._update_pose_box)
        self.preset = AnimalPresetCombo()
        self.preset.chosen.connect(self.apply_animal_preset)
        self.det_form.insert_row("Detection", "Set the detection up for", self.preset, tip=PRESET_TIP)
        self.pose_box = PoseModelBox()
        self.pose_box.changed.connect(self.main.mark_dirty)
        self.det_form.add_to_section("Body parts", self.pose_box)
        pg.add(self.det_form)
        pg.finish()

    def _build_stages(self):
        pg = self._element_page("stages", "Stages", "Stages divide the experiment into phases — habituation, "
                                "training days, probe trial… Tests are scheduled per stage and trial.")
        f = property_form()
        self.stages = QPlainTextEdit()
        self.stages.setPlaceholderText("One stage per line, e.g.\nHabituation\nTraining day 1\nProbe")
        self.stages.setFixedHeight(150)
        self.stages.setMinimumWidth(360)
        self.stages.setMaximumWidth(520)
        self.stages.textChanged.connect(self._store)
        f.addRow("Enter the stages, one per line", self.stages)
        pg.add(f)
        self.stages_lbl = QLabel()
        theme.style(self.stages_lbl, lambda: f"color:{theme.ERROR};")
        self.stages_lbl.hide()
        pg.add(self.stages_lbl)
        pg.add(separator())

        pg.section("Training criteria (stage end rules)")
        pg.add(hint("A stage is completed when a result measure meets the condition on N consecutive trials (Is "
                    "“any”: any value), once the animal has done the minimum number of trials and, with an "
                    "acceptable variability, when the measure varies little enough over its last trials. Animals "
                    "that have not met it after the given number of trials can be retired. Apply the criteria on "
                    "the Animals page."))
        cols = [c if c[0] != "stage" else c[:3] + (lambda: self.project.stages if self.project else [],)
                for c in CRITERIA_COLS]
        self.crit = RecordTable(cols, stretch=(1,))
        for c, wd in ((0, 140), (2, 64), (3, 80), (4, 130), (5, 250), (6, 110), (7, 110)):
            self.crit.setColumnWidth(c, wd)
        for c in _VAR_COLS.values():
            self.crit.setColumnHidden(c, True)
        self.crit.horizontalHeaderItem(6).setToolTip("Retire animals that have not met the criterion after this many "
                                                     "trials of the stage")
        self.crit.horizontalHeaderItem(7).setToolTip("The stage cannot end before the animal has done this many "
                                                     "trials of it, even when the condition is met earlier")
        self.crit.setMinimumHeight(170)
        self.crit.edited.connect(self._store_criteria)
        self.crit.currentCellChanged.connect(lambda *_: self._show_variability())
        pg.add(self.crit)
        # the selected criterion's acceptable variability (ANY-maze 7.30)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.var_stat = QComboBox()
        self.var_stat.addItem("No variability rule", "")
        for k, label in wf.VARIABILITY_STATS.items():
            self.var_stat.addItem(label, k)
        self.var_stat.setToolTip("Variability (%): ANY-maze's ((highest − lowest) / (highest + lowest)) × 100 over "
                                 "the trials; SD: their standard deviation; CV (%): the SD as a % of their mean")
        self.var_measure = QLineEdit()
        self.var_measure.setPlaceholderText("the criterion's measure")
        self.var_measure.setMinimumWidth(200)
        self.var_trials = QSpinBox()
        self.var_trials.setRange(2, wf.MAX_TRIALS)
        self.var_trials.setSuffix(" trials")
        self.var_max = QDoubleSpinBox()
        self.var_max.setRange(0.0, 1e6)
        self.var_max.setDecimals(3)
        for w in (QLabel("Acceptable variability:"), self.var_stat, QLabel("of"), self.var_measure,
                  QLabel("over the last"), self.var_trials, QLabel("is at most"), self.var_max):
            row.addWidget(w)
        row.addStretch()
        self.var_stat.currentIndexChanged.connect(self._variability_edited)
        self.var_measure.editingFinished.connect(self._variability_edited)
        self.var_trials.valueChanged.connect(self._variability_edited)
        self.var_max.valueChanged.connect(self._variability_edited)
        pg.add(row)
        pg.add(button_row(small_button("Add criterion", "add", slot=self._add_criterion),
                          small_button("Remove", "delete", slot=self.crit.remove_current)))
        pg.finish()
        self._show_variability()

    def _build_keys(self):
        pg = self._element_page("keys", "Keys", "Keys are the behaviours you score by hand: press the key (or click "
                                "its on-screen button) while a test runs or while you review its video.")
        row = QHBoxLayout()
        row.setSpacing(28)
        left = QVBoxLayout()
        self.beh = QTableWidget()
        style_table(self.beh, ["Key name", "Key stroke", "How it works", "Radio set", "Colour"])
        self.beh.setColumnWidth(1, 84)
        self.beh.setColumnWidth(2, 110)
        self.beh.setColumnWidth(3, 96)
        self.beh.setColumnWidth(4, 70)
        self.beh.setMinimumHeight(260)
        self.beh.setMaximumHeight(460)
        self.beh.setToolTip("Key stroke: a letter, digit or punctuation key (up to 46 keys). Keys in the same "
                            "radio (exclusive) set cannot overlap — starting one stops the others.")
        self.beh.itemChanged.connect(self._store_behaviours)
        self.beh.currentCellChanged.connect(lambda *_: self._show_key())
        left.addWidget(self.beh, 1)
        self.beh_lbl = QLabel()
        self.beh_lbl.setWordWrap(True)
        theme.style(self.beh_lbl, lambda: f"color:{theme.ERROR};")
        self.beh_lbl.hide()
        left.addWidget(self.beh_lbl)
        left.addLayout(button_row(small_button("New key", "add", slot=self._add_behaviour),
                                  small_button("Delete key", "delete", slot=self._remove_behaviour)))
        row.addLayout(left, 11)
        self.key_editor = KeyEditor()
        self.key_editor.edited.connect(self._key_edited)
        row.addWidget(self.key_editor, 10)
        pg.add(row)
        pg.finish()

    def _build_procedures(self):
        pg = self._element_page("procedures", "Procedures", "Procedures control the test and the hardware during "
                                "live tests: wait for events, switch outputs, deliver rewards, end the test… All "
                                "ticked procedures run together.", scroll=False)
        from ..procedure_editor import ProcedureEditor

        self.proc_editor = ProcedureEditor()
        self.proc_editor.changed.connect(self.main.mark_dirty)
        pg.add(self.proc_editor, 1)

    def _build_analysis(self):
        pg = self._element_page("analysis", "Analysis", "How the results are calculated from the tracks. Changes "
                                "apply to every test the next time results are shown.")
        self.an_form = SettingsForm(ANALYSIS_SPEC, sections=ANALYSIS_SECTIONS)
        self.an_form.setMaximumWidth(FORM_WIDTH - 56)
        self.an_form.changed.connect(self.main.mark_dirty)
        self.an_form.changed.connect(self._analysis_changed)
        pg.add(self.an_form)

        pg.section("Time periods")
        pg.add(hint("Analyse each test in custom periods (they replace the time bins), e.g. the first and the "
                    "last minute."))
        self.periods = RecordTable(PERIOD_COLS)
        self.periods.setColumnWidth(1, 140)
        self.periods.setColumnWidth(2, 140)
        self.periods.setMinimumHeight(130)
        self.periods.setMaximumHeight(190)
        self.periods.edited.connect(self._store_periods)
        pg.add(self.periods)
        self.periods_lbl = self._error_label()
        pg.add(self.periods_lbl)
        pg.add(button_row(small_button("New time period", "add", slot=self._add_period),
                          small_button("Remove", "delete", slot=self.periods.remove_current)))

        pg.section("Time periods based on a time marker")
        pg.add(hint("A period anchored to an event — e.g. the 30 s after the animal first leaves the start box — "
                    "or starting at the time (s) given by a calculation. Occurrence: 1 = first, 2 = second…, 0 = one "
                    "period for every occurrence. Periods whose event never happens are left out. The period lasts "
                    "its duration (0 = until the end of the test) or ends at an event (its occurrence after the "
                    "start: 1 = the first) or at a calculation's time; when that never happens, it ends at the end "
                    "of the test and its results say so (Warnings)."))
        self.ev_periods = RecordTable(EVENT_PERIOD_COLS, stretch=(0, 2, 7))
        self.ev_periods.setColumnWidth(1, 230)
        self.ev_periods.setColumnWidth(6, 230)
        for c in (3, 4, 5, 8, 9):
            self.ev_periods.setColumnWidth(c, 100)
        self.ev_periods.setMinimumHeight(130)
        self.ev_periods.setMaximumHeight(190)
        self.ev_periods.edited.connect(self._store_event_periods)
        pg.add(self.ev_periods)
        self.ev_periods_lbl = self._error_label()
        pg.add(self.ev_periods_lbl)
        pg.add(button_row(small_button("New event period", "add", slot=self._add_event_period),
                          small_button("Remove", "delete", slot=self.ev_periods.remove_current)))

        pg.section("Analysis plug-ins")
        pg.add(hint("Data recorded by other systems — heart rate, Spike2 or LabChart exports, fibre photometry — "
                    "brought into the results: each series gets the analogue-signal measures (mean, minimum, "
                    "maximum, baseline…) for the whole test, every time period and every zone. The built-in "
                    "plug-in reads a CSV / TSV file per test; others are installed as Python packages. Run them "
                    "once the tests are done (and again when the files change)."))
        self.plugin_list = QListWidget()
        self.plugin_list.setFixedHeight(110)
        self.plugin_list.setStyleSheet("QListWidget::item{padding:4px 4px;}")
        self.plugin_list.itemDoubleClicked.connect(lambda *_: self.edit_plugin())
        pg.add(self.plugin_list)
        self.plugin_add = small_button("Add plug-in", "add")
        self.plugin_menu = QMenu(self.plugin_add)
        self.plugin_menu.aboutToShow.connect(self._fill_plugin_menu)
        self.plugin_add.setMenu(self.plugin_menu)
        pg.add(button_row(self.plugin_add, small_button("Edit…", "edit", slot=self.edit_plugin),
                          small_button("Remove", "delete", slot=self.remove_plugin),
                          small_button("Run on the tests", "play", slot=self.run_plugins,
                                       tip="Run the plug-ins on every test performed and save the experiment")))
        pg.finish()

    def _build_calculations(self):
        pg = self._element_page("calculations", "Calculations", "Results worked out from other results with a "
                                "formula, e.g. a discrimination index or the percentage of time in the open arms. "
                                "They are listed under Calculation results on the Data page and can be exported, "
                                "compared in the statistics and used in other calculations.")
        row = QHBoxLayout()
        row.setSpacing(28)
        left = QVBoxLayout()
        self.calc_list = QListWidget()
        self.calc_list.setMinimumHeight(260)
        self.calc_list.setMaximumHeight(460)
        self.calc_list.setStyleSheet("QListWidget::item{padding:5px 4px;}")
        self.calc_list.currentRowChanged.connect(lambda *_: self._show_calculation())
        left.addWidget(self.calc_list, 1)
        left.addLayout(button_row(small_button("New calculation", "add", slot=self.new_calculation),
                                  small_button("Delete", "delete", slot=self.delete_calculation)))
        left.addStretch()
        row.addLayout(left, 8)
        self.calc_editor = CalculationEditor()
        self.calc_editor.edited.connect(self._calculation_edited)
        self.calc_editor.pick_measure = self._pick_measure
        self.calc_editor.stages = lambda: self.project.stages if self.project else []
        row.addWidget(self.calc_editor, 13)
        pg.add(row)
        pg.finish()

    @staticmethod
    def _error_label() -> QLabel:
        lbl = QLabel()
        lbl.setWordWrap(True)
        theme.style(lbl, lambda: f"color:{theme.ERROR};")
        lbl.hide()
        return lbl

    def _build_hardware(self):
        pg = self._element_page("hardware", "Hardware", "Devices used by procedures during live tests.", FORM_WIDTH)
        pg.section("I/O devices")
        pg.add(hint("Arduino boards (see firmware/), serial devices, audio and simulated devices: levers, nose "
                    "pokes, lights, pellet dispensers, shockers, lasers, wheels…"))
        self.io_list = QListWidget()
        self.io_list.setFixedHeight(150)
        self.io_list.setStyleSheet("QListWidget::item{padding:5px 4px;}")
        self.io_list.itemDoubleClicked.connect(lambda *_: self.edit_io_devices())
        pg.add(self.io_list)
        self.io_btn = small_button("Set up I/O devices…", "plug", slot=self.edit_io_devices,
                                   tip="Arduino boards, serial devices, audio and simulated devices used by "
                                       "procedures")
        pg.add(button_row(self.io_btn))
        pg.add(separator())
        pg.section("Synchronisation")
        pg.add(hint("Pulses on a digital output that let another recording system (electrophysiology, imaging, "
                    "photometry) align its data with the test. Pulses due at the same moment are sent as one: with "
                    "a pulse for every frame, there are as many pulses as frames. The Arduino firmware times each "
                    "pulse's width on the board; LabJack and National Instruments devices use their digital line."))
        f = property_form()
        self.sync_on = QCheckBox("Send synchronisation pulses in live tests")
        self.sync_out = QComboBox()
        self.sync_out.setMinimumWidth(320)
        self.sync_out.setToolTip("A digital output of an I/O device (Set up I/O devices…)")
        self.sync_checks = {}
        boxes = QVBoxLayout()
        boxes.setSpacing(2)
        for key, text in (("test_start", "When the test starts"), ("test_end", "When the test ends"),
                          ("per_frame", "For every captured frame (while the test runs or is paused)"),
                          ("per_position", "For every position stored in the track")):
            cb = QCheckBox(text)
            cb.toggled.connect(self._store_sync)
            self.sync_checks[key] = cb
            boxes.addWidget(cb)
        self.sync_width = QDoubleSpinBox()
        self.sync_width.setRange(0.001, 1000.0)
        self.sync_width.setDecimals(3)
        self.sync_width.setSuffix(" ms")
        self.sync_width.setToolTip("How long each pulse lasts")
        f.addRow(self.sync_on)
        f.addRow("Output", self.sync_out)
        f.addRow("Send a pulse", boxes)
        f.addRow("Pulse width", self.sync_width)
        pg.add(f)
        self.sync_on.toggled.connect(self._store_sync)
        self.sync_out.currentIndexChanged.connect(self._store_sync)
        self.sync_width.valueChanged.connect(self._store_sync)
        pg.add(separator())
        pg.section("Touch screen")
        f = property_form()
        self.ts_lbl = QLabel()
        f.addRow("The touch screen is", self.ts_lbl)
        pg.add(f)
        self.ts_btn = small_button("Touch screen settings…", "touch", slot=self.edit_touchscreen)
        pg.add(button_row(self.ts_btn))
        pg.body.addSpacing(10)
        self.hw_lbl = hint("")
        pg.add(self.hw_lbl)
        pg.finish()

    # ================================================================== ribbon & explorer
    def _act(self, text, icon_name, slot, tip="") -> QAction:
        a = QAction(icon(icon_name), text, self)
        a.setToolTip(tip or text)
        a.triggered.connect(lambda _=False: slot())
        return a

    def _build_actions(self):
        self.add_item_act = self._act("Add item", "add", lambda: None, "Add an apparatus, stage, key, procedure, "
                                      "time period or calculation to the protocol")
        m = QMenu(self)
        for text, ic, fn in (("New apparatus", "zone", self.new_apparatus), ("New stage", "stages", self.new_stage),
                             ("New key", "key", self.new_key), ("New procedure", "procedure", self.new_procedure),
                             ("New time period", "timer", self.new_time_period),
                             ("New event-based time period", "clock", self.new_event_period),
                             ("New training criterion", "check", self.new_criterion),
                             ("New calculation", "calculator", self.new_calculation)):
            m.addAction(icon(ic), text, fn)
        self.add_item_act.setMenu(m)
        self.template_act = self._act("Apply template", "layers", lambda: None,
                                      "Use the settings of a test type: protocol type and test duration")
        tm = QMenu(self)
        for k, t in TEMPLATES.items():
            tm.addAction(t.title, lambda k=k: self.apply_template(k))
        self.template_act.setMenu(tm)
        self.apparatus_tpl_act = self._act("Apparatus from template", "zone", self.apparatus_from_template,
                                           "Draw an apparatus from a template on the Apparatus page")
        A = self._act
        self.blind_act = A("Blind testing", "eye_off", lambda: self.blind.setChecked(self.blind_act.isChecked()),
                           "Hide the treatments (shown as codes) while testing and scoring")
        self.blind_act.setCheckable(True)
        self.blind.toggled.connect(lambda on: self.blind_act.setChecked(on))
        self.element_acts = {
            "protocol": [self.blind_act],
            "tracking": [A("Install pose model", "brain", lambda: self.pose_box.install(),
                           "Install the AI pose model used to locate the body parts"),
                         A("Restore defaults", "refresh", self.restore_detection_defaults,
                           "Restore the default animal tracking settings")],
            "stages": [A("New stage", "stages", self.new_stage), A("New criterion", "check", self.new_criterion),
                       ("small", A("Delete criterion", "delete", self.crit.remove_current))],
            "keys": [A("New key", "key", self.new_key), A("Delete key", "delete", self._remove_behaviour),
                     ("small", A("Duplicate key", "copy", self.duplicate_key))],
            "procedures": [A("New procedure", "procedure", self.new_procedure),
                           ("small", A("Duplicate procedure", "copy", lambda: self.proc_editor.duplicate_procedure())),
                           ("small", A("Delete procedure", "delete", lambda: self.proc_editor.remove_procedure())),
                           ("small", A("Edit as text", "code", lambda: self.proc_editor.edit_json())),
                           A("Functions", "help", lambda: self.proc_editor.show_functions(),
                             "Functions and operators available in expressions")],
            "analysis": [A("New time period", "timer", self.new_time_period),
                         A("New event period", "clock", self.new_event_period),
                         ("small", A("Delete time period", "delete", self.delete_time_period)),
                         ("small", A("Restore defaults", "refresh", self.restore_analysis_defaults))],
            "calculations": [A("New calculation", "calculator", self.new_calculation),
                             A("Delete calculation", "delete", self.delete_calculation),
                             ("small", A("Duplicate calculation", "copy", self.duplicate_calculation))],
            "hardware": [A("I/O devices", "plug", self.edit_io_devices), A("Touch screen", "touch",
                                                                             self.edit_touchscreen)],
        }

    def ribbon_groups(self):
        groups = [("Protocol", [(self.add_item_act, "large")])]
        acts = self.element_acts.get(self.element) or []
        if acts:
            title = "Testing" if self.element == "protocol" else dict((k, t) for k, t, _i in ELEMENTS)[self.element]
            groups.append((title, [(a[1], "small") if isinstance(a, tuple) else (a, "large") for a in acts]))
        groups.append(("Templates", [(self.template_act, "large"), (self.apparatus_tpl_act, "large")]))
        return groups

    def explorer_items(self):
        p = self.project
        return [(term(p, "stage", plural=True) if key == "stages" else label, ic, key) for key, label, ic in ELEMENTS]

    def show_item(self, key: str):
        """Show a protocol element (explorer sub-item)."""
        if key not in self.elements:
            return
        changed = key != self.element
        self.element = key
        self.stack.setCurrentWidget(self.elements[key])
        self.main.select_explorer(self, key)
        self._element_shown(key)
        if changed and self.main.current_page() is self:
            self._quiet_show = True
            try:
                self.main.refresh_ribbon()
            finally:
                self._quiet_show = False

    def _element_shown(self, key):
        if key == "procedures" and self.project is not None:
            self.proc_editor.set_project(self.project)  # the Live page edits the same procedures
        elif key == "keys":
            self._show_key()
        elif key == "calculations":
            self._show_calculation()
        elif key == "analysis" and self.project is not None:
            self._show_event_period_problems([])  # (the calculations may have changed)

    # ================================================================== loading
    def set_project(self, project):
        self.on_show()

    def on_show(self):
        if self._quiet_show:
            return
        p = self.project
        self.pose_box.set_settings(p.detection if p is not None else None)
        self._update_hardware()
        self.proc_editor.set_project(p)
        if p is None:
            return
        with loading(self):
            self._load(p)
        self._show_key()
        self._update_mode()
        self._update_summary()
        self._apply_lock()
        self.main.select_explorer(self, self.element)

    @property
    def locked(self) -> bool:
        """The protocol is locked for the current user (Project.security, see core.security)."""
        p = self.project
        return p is not None and not security.can(p, "edit_protocol")

    def _apply_lock(self):
        """A locked protocol is shown read-only: the element pages and the ribbon's editing commands are
        disabled."""
        locked = self.locked
        self.lock_lbl.setVisible(locked)
        for pg in self.elements.values():
            pg.widget().setEnabled(not locked)
        acts = [self.add_item_act, self.template_act, self.apparatus_tpl_act]
        acts += [a[1] if isinstance(a, tuple) else a for v in self.element_acts.values() for a in v]
        for a in acts:
            a.setEnabled(not locked)

    def security_changed(self):
        self._apply_lock()
        if self.main.current_page() is self:
            self._quiet_show = True
            try:
                self.main.refresh_ribbon()
            finally:
                self._quiet_show = False

    def _load(self, p):
        self.name.setText(p.name)
        self.desc.setPlainText(p.description)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(p.protocol)))
        self.mode.setCurrentIndex(max(0, self.mode.findData(p.settings_extra.get("mode", "tracking"))))
        self.duration.setValue(p.test_duration_s)
        self.start_mode.setCurrentIndex(max(0, self.start_mode.findData(p.start_mode)))
        self.stages.setPlainText("\n".join(p.stages))
        self._stage_names = list(p.stages)
        row = max(self.beh.currentRow(), 0)
        self.beh.setRowCount(0)
        self._key_names = []
        for b in p.behaviours:
            self._append_behaviour_row(b)
        if self.beh.rowCount():
            self.beh.setCurrentCell(min(row, self.beh.rowCount() - 1), 0)
        self._validate_behaviours()
        self.blind.setChecked(p.blind)
        self.confirm_id.setChecked(wf.confirm_id_enabled(p))
        self.weigh_first.setChecked(bool(p.require_weight_before_test))
        self.crit.set_records(self._criterion_row(c) for c in p.training_criteria)
        self.periods.set_records({"label": lbl, "start": a, "end": b} for lbl, a, b in p.analysis.custom_periods)
        self.ev_periods.set_records(self._event_period_record(d) for d in p.analysis.event_periods)
        self.periods_lbl.hide()
        self._show_event_period_problems([])
        self.det_form.load(p.detection)
        self.an_form.load(p.analysis)
        self.fst_form.load(p.analysis)
        self._update_fst()
        self._fill_calculations()
        self._fill_plugins()
        self._load_terminology(p)

    def _load_terminology(self, p):
        for r, key in enumerate(TERMS):
            t = p.terminology.get(key) or {}
            for c, k in ((1, "singular"), (2, "plural")):
                text, it = str(t.get(k, "")), self.terms.item(r, c)
                if it is None:  # (items are kept: this also runs while one of them is being edited)
                    self.terms.setItem(r, c, QTableWidgetItem(text))
                elif it.text() != text:
                    it.setText(text)

    def _store_terminology(self, item=None):
        """Protocol ▸ Terminology edited: keep the changed terms (a new singular gets a new plural unless one was
        typed) and rename the window's labels."""
        if self._loading or self.project is None:
            return
        p = self.project
        d = {}
        for r, key in enumerate(TERMS):
            one = (self.terms.item(r, 1).text() if self.terms.item(r, 1) else "").strip()
            many = (self.terms.item(r, 2).text() if self.terms.item(r, 2) else "").strip()
            old = p.terminology.get(key) or {}
            if item is not None and item.row() == r and item.column() == 1 and many == old.get("plural"):
                many = ""  # the singular changed: its plural follows
            if one:
                d[key] = {"singular": one, "plural": many}
        new = terminology_from(d)
        if new != p.terminology:
            p.terminology = new
            self.main.mark_dirty()
            self.main.apply_terminology()
        with loading(self):
            self._load_terminology(p)

    def _update_summary(self):
        p = self.project
        if p is None:
            return
        n_app = len(p.apparatus)
        procs = sum(1 for q in p.procedures if not isinstance(q, dict) or q.get("enabled", True))
        parts = [f"{n_app} apparatus", f"{len(p.stages)} stage{'s' if len(p.stages) != 1 else ''}",
                 f"{len(p.behaviours)} key{'s' if len(p.behaviours) != 1 else ''}",
                 f"{procs} procedure{'s' if procs != 1 else ''}"]
        self.summary_lbl.setText("This protocol has " + ", ".join(parts) + ".")

    def _update_fst(self):
        """The forced swim / tail suspension settings are shown for those types of test."""
        self.fst_box.setVisible(self.protocol.currentData() in FST_TEMPLATES)

    def _fst_changed(self):
        if self.project is not None:
            self.an_form.load(self.project.analysis)
        self.main.mark_dirty()

    def _analysis_changed(self):
        if self.project is not None:
            self.fst_form.load(self.project.analysis)

    def _update_mode(self):
        mode = self.mode.currentData()
        self.takenote_lbl.setText("This protocol uses Input/output only mode: tests run without a camera and these "
                                  "settings are not used." if mode == "io_only" else
                                  "This protocol uses TakeNote mode: tests are scored by hand and these settings "
                                  "are only used if you track a test anyway.")
        self.takenote_lbl.setVisible(mode in ("takenote", "io_only"))
        self.protocol_form.setRowVisible(self.chambers_row, mode == "io_only")
        p = self.project
        preset = ioconfig.OPERANT_PRESETS.get(p.settings_extra.get(PRESET_KEY) or "") if p is not None else None
        n = len(p.io_devices) if p is not None else 0
        devs = f"{n} I/O device{'s' if n != 1 else ''}"
        self.chambers_lbl.setText(f"{preset['label']}; {devs} (Hardware)." if preset else
                                  f"{devs} (Hardware). Set up the chambers' levers, nose pokes, lights, dispenser and "
                                  "shocker from a preset: Med Associates-, Coulbourn- or Lafayette-style, or custom."
                                  if n else
                                  "No I/O devices yet: set up the chambers' levers, nose pokes, lights, dispenser "
                                  "and shocker from a preset (Med Associates-, Coulbourn- or Lafayette-style, or "
                                  "custom).")

    def set_up_chambers(self, preset: str | None = None) -> bool:
        """Input/output only mode: the I/O devices of the operant chambers from a preset (one device per chamber,
        its inputs and outputs named and its pins numbered for the interface chosen), replacing the experiment's
        I/O devices or added to them."""
        p = self.project
        if p is None:
            return False
        from ..io_devices_dialog import OperantPresetDialog
        dlg = OperantPresetDialog(self, preset or p.settings_extra.get(PRESET_KEY))
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return False
        keep = []
        if p.io_devices:
            names = ", ".join(str(d.get("name", "?")) for d in p.io_devices)
            r = QMessageBox.question(self, "Operant chambers", f"The experiment already has I/O devices ({names}). "
                                     "Replace them with the chambers?\n\nNo adds the chambers to them.",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.No:
                keep = list(p.io_devices)
        new = dlg.devices([d.get("name") for d in keep])
        p.io_devices[:] = keep + new
        p.settings_extra[PRESET_KEY] = dlg.preset()
        self.main.mark_dirty()
        self._update_hardware()
        self._update_mode()
        self.proc_editor.validate()  # the devices changed
        self.main.status(f"Set up {len(new)} chamber{'s' if len(new) != 1 else ''} "
                         f"({ioconfig.OPERANT_PRESETS[dlg.preset()]['label']}, "
                         f"{ioconfig.DEVICE_TYPES[dlg.device_type()]}): check the ports and pins in Hardware › I/O "
                         "devices.")
        return True

    def _store_mode(self, *_):
        self._update_mode()
        if self._loading or self.project is None:
            return
        self.project.settings_extra["mode"] = self.mode.currentData()
        self.main.mark_dirty()
        self.proc_editor.validate()  # (Input/output only: what needs the animal is an error)
        self._show_event_period_problems([])  # (and time periods at zone entries / exits never happen)
        if self.mode.currentData() == "io_only" and not self.project.io_devices:
            self.main.status("Input/output only: set up the I/O devices of the chambers from a preset with "
                             "“Operant chambers…”.")

    def _load_sync(self, p):
        """The synchronisation element of the Hardware page (Project.sync)."""
        s = sync_from(p.sync if p is not None else None)
        with loading(self):
            self.sync_out.clear()
            for d in (p.io_devices if p is not None else []):
                for c in d.get("channels") or []:
                    if c.get("kind", "input") == "output" and c.get("name"):
                        self.sync_out.addItem(f"{d.get('name', '?')}/{c['name']}", (d.get("name", ""), c["name"]))
            want = (s["device"], s["channel"])
            i = next((k for k in range(self.sync_out.count()) if self.sync_out.itemData(k) == want or
                      not s["device"] and self.sync_out.itemData(k)[1] == s["channel"]), -1)
            if i < 0 and s["channel"]:
                self.sync_out.addItem(f"{'/'.join(x for x in want if x)} (not configured)", want)
                i = self.sync_out.count() - 1
            self.sync_out.setCurrentIndex(max(i, 0) if self.sync_out.count() else -1)  # default: the first output
            self.sync_on.setChecked(s["enabled"])
            for k, cb in self.sync_checks.items():
                cb.setChecked(s[k])
            self.sync_width.setValue(s["width_ms"])
        self._sync_enabled()

    def _sync_enabled(self):
        on = self.sync_on.isChecked()
        for w in [self.sync_out, self.sync_width, *self.sync_checks.values()]:
            w.setEnabled(on)

    def _store_sync(self, *_):
        self._sync_enabled()
        p = self.project
        if self._loading or p is None:
            return
        dev, ch = self.sync_out.currentData() or ("", "")
        p.sync = sync_from({"enabled": self.sync_on.isChecked(), "device": dev, "channel": ch,
                            "width_ms": self.sync_width.value(),
                            **{k: cb.isChecked() for k, cb in self.sync_checks.items()}})
        self.main.mark_dirty()

    def _update_hardware(self):
        p = self.project
        self.io_list.clear()
        self._load_sync(p)
        if p is None:
            self.hw_lbl.setText("")
            self.ts_lbl.setText("")
            return
        for d in p.io_devices:
            n = len(d.get("channels") or [])
            state = "" if d.get("enabled", True) else " — disabled"
            it = QListWidgetItem(icon("plug"), f"{d.get('name', '?')}  ·  {d.get('type', '?')}, {n} channel"
                                               f"{'s' if n != 1 else ''}{state}")
            self.io_list.addItem(it)
        if not p.io_devices:
            it = QListWidgetItem("No I/O devices — press “Set up I/O devices…” to add one.")
            it.setFlags(Qt.NoItemFlags)
            self.io_list.addItem(it)
        devs = ", ".join(f"{d.get('name', '?')} ({d.get('type', '?')})" for d in p.io_devices) or "none"
        cfg = p.settings_extra.get("touchscreen") or {}
        ts = "on" if cfg.get("enabled") else "off"
        n_areas = len(cfg.get("areas") or [])
        self.ts_lbl.setText(f"Used in live tests, with {n_areas} response window{'s' if n_areas != 1 else ''}"
                            if cfg.get("enabled") else "Not used")
        self.hw_lbl.setText(f"I/O devices: {devs}. Touch screen: {ts}. Procedures are edited under Protocol › "
                            "Procedures and on the Run tests page.")

    # ================================================================== commands
    def _goto_element(self, key):
        if self.main.current_page() is not self:
            self.main.show_page(self)
        self.show_item(key)

    def new_apparatus(self):
        page = self.main.goto("ApparatusPage")
        if page is not None:
            page.add_apparatus()
            self.main.refresh_explorer(page)

    def apparatus_from_template(self):
        page = self.main.goto("ApparatusPage")
        if page is not None:
            page.create_from_template()

    def new_stage(self):
        if self.project is None:
            return
        self._goto_element("stages")
        names = set(self.project.stages)
        n = len(self.project.stages) + 1
        while f"Stage {n}" in names:
            n += 1
        lines = [s for s in self.stages.toPlainText().splitlines() if s.strip()]
        self.stages.setPlainText("\n".join(lines + [f"Stage {n}"]))
        self.stages.setFocus()

    def new_key(self):
        if self.project is None:
            return
        self._goto_element("keys")
        self._add_behaviour()

    def duplicate_key(self):
        r = self.beh.currentRow()
        if self.project is None or not 0 <= r < len(self.project.behaviours):
            return
        b = self.project.behaviours[r]
        names = {x.name for x in self.project.behaviours}
        name = f"{b.name} copy"
        k = 2
        while name in names:
            name = f"{b.name} copy {k}"
            k += 1
        self._append_behaviour_row(Behaviour(name, wf.free_key(self.project.behaviours), b.kind, b.group, "",
                                             b.activity))
        self.beh.setCurrentCell(self.beh.rowCount() - 1, 0)
        self._store_behaviours()

    def new_procedure(self):
        if self.project is None:
            return
        self._goto_element("procedures")
        self.proc_editor.add_procedure()

    def new_time_period(self):
        if self.project is None:
            return
        self._goto_element("analysis")
        self._add_period()

    def new_event_period(self):
        if self.project is None:
            return
        self._goto_element("analysis")
        self._add_event_period()

    def _pick_measure(self, done):
        """Choose a results column for a formula (the Select data tree of the Data page) and pass it to done();
        the results are calculated first if none are at hand (editing the calculations does not change the
        measures: the rows already checked against are used)."""
        p = self.project
        if p is None:
            return

        def pick(rows):
            if rows is not self._calc_rows:
                self._set_calculation_rows(rows)
            c = self.current_calculation()
            others = [x.column for x in p.calculations if x is not c and x.column]
            cols = [x for x in dict.fromkeys(result_columns(rows) + others)
                    if x != ERROR_COLUMN and (c is None or x != c.column)]
            info = [x for x in info_columns(p) if x in cols and x != "Test"]
            cats = measure_groups([x for x in cols if x not in info], _names(p), info)
            dlg = self.measure_dialog(cats)
            done(dlg.selected() if dlg.exec() == MeasurePickerDialog.Accepted else None)

        rows = self._calculation_rows()
        if rows is not None:
            pick(rows)
        elif not p.tests:
            QMessageBox.information(self, "Insert measure", "The measures are listed once the experiment has "
                                    "tests with results. You can also type a measure's name in braces, e.g. "
                                    "{Total distance (m)}.")
        else:
            run_with_progress(self, "Calculating results", lambda progress, _stop: get_rows(p, False,
                                                                                         progress=progress), pick)

    def measure_dialog(self, cats: dict) -> MeasurePickerDialog:
        return MeasurePickerDialog(cats, self)

    def new_criterion(self):
        if self.project is None:
            return
        self._goto_element("stages")
        self._add_criterion()

    def _focus_changed(self, _old, new):
        for t in (self.periods, self.ev_periods):
            if new is not None and (new is t or t.isAncestorOf(new)):
                self._period_table = t

    def delete_time_period(self):
        """Delete the selected period of the table worked in last (its cell editor or a cell's list may have the
        focus, or nothing has: the ribbon took it)."""
        t = self._period_table
        if t is None or t.currentRow() < 0:
            t = self.ev_periods if self.ev_periods.currentRow() >= 0 and self.periods.currentRow() < 0 \
                else self.periods
        t.remove_current()

    def apply_template(self, key: str):
        """Use a test type's settings: protocol type and default test duration."""
        p = self.project
        if p is None or key not in TEMPLATES:
            return
        t = TEMPLATES[key]
        if p.tests and QMessageBox.question(
                self, "Apply template", f"Make this a {t.title} protocol with tests of {t.default_duration_s:g} s? "
                "Existing results are recalculated with the new protocol type.") != QMessageBox.Yes:
            return
        p.set_protocol(key)
        p.test_duration_s = float(t.default_duration_s)
        self.on_show()
        self.main.mark_dirty()
        self.main.status(f"Protocol set to {t.title} ({t.default_duration_s:g} s tests). Use “Apparatus from "
                         "template” to draw its apparatus.")

    def apply_animal_preset(self, key: str) -> list[str]:
        """Set an animal preset (core.tracking.ANIMAL_PRESETS) on the default detection settings; the body-size
        limits use the calibration of the first calibrated apparatus.  Returns the fields changed."""
        p = self.project
        if p is None or self.det_form.obj is None:
            return []
        ppc = next((a.px_per_cm for a in p.apparatus if a.px_per_cm), None)
        changed, msg = apply_preset_to_form(self.det_form, key, ppc)
        if changed:
            self.main.mark_dirty()
            self._update_pose_box()
        self.main.status(msg)
        return changed

    def restore_detection_defaults(self):
        self._restore_defaults(self.project.detection if self.project else None, DetectionSettings(), DETECTION_SPEC,
                               self.det_form, "animal tracking")
        self._update_pose_box()

    def restore_analysis_defaults(self):
        self._restore_defaults(self.project.analysis if self.project else None,
                               AnalysisSettings.for_new_experiment(), ANALYSIS_SPEC,
                               self.an_form, "analysis")

    def _restore_defaults(self, obj, default, spec, form, what):
        if obj is None or QMessageBox.question(self, "Restore defaults",
                                               f"Restore the default {what} settings?") != QMessageBox.Yes:
            return
        for attr, *_ in spec:
            setattr(obj, attr, getattr(default, attr))
        form.load(obj)
        self.main.mark_dirty()

    def edit_io_devices(self):
        if self.project is None:
            return None
        from ..io_devices_dialog import IODevicesDialog
        dlg = IODevicesDialog(self.project, self)
        dlg.changed.connect(self.main.mark_dirty)
        dlg.exec()
        self._update_hardware()
        self._update_mode()  # (the chambers row: devices and preset)
        self.proc_editor.validate()  # the devices changed
        return dlg

    def edit_touchscreen(self):
        if self.project is None:
            return None
        from ..touchscreen import TouchScreenDialog
        dlg = TouchScreenDialog(self.project, self)
        if dlg.exec():
            self.main.mark_dirty()
        self._update_hardware()
        return dlg

    def _update_pose_box(self):
        self.pose_box.refresh()
        if self.project is not None and self.project.detection.body_parts == "pose" \
                and self.project.detection.pose_model in pose.MODELS \
                and not pose.is_installed(self.project.detection.pose_model):
            self.main.status("The pose model is not installed yet — press Install… under Animal tracking › "
                             "Body parts.")

    def _store(self, *_):
        if self._loading or self.project is None:
            return
        p = self.project
        p.name = self.name.text().strip() or p.name
        p.description = self.desc.toPlainText()
        if p.protocol != self.protocol.currentData():  # the forced swim / tail suspension immobility follows
            p.set_protocol(self.protocol.currentData())
            self.an_form.load(p.analysis)
            self.fst_form.load(p.analysis)
            self._update_fst()
        p.test_duration_s = self.duration.value()
        p.start_mode = self.start_mode.currentData()
        self._follow_stage_renames(p)
        stages = [s.strip() for s in self.stages.toPlainText().splitlines() if s.strip()]
        self.stages_lbl.setVisible(len(stages) > wf.MAX_STAGES)
        self.stages_lbl.setText(f"At most {wf.MAX_STAGES} stages — the lines after the {wf.MAX_STAGES}th are ignored.")
        p.stages = stages[:wf.MAX_STAGES]
        self.main.mark_dirty()
        self.main.update_title()
        self._update_summary()

    def _follow_stage_renames(self, p):
        """A line of the stages box edited in place renames its stage: the tests, criteria and completed stages
        of the old name follow (p.rename_stage). Lines are matched by position while their number is unchanged; a
        line emptied while being retyped keeps its stage until it has a name again."""
        lines = [s.strip() for s in self.stages.toPlainText().split("\n")]
        if len(lines) != len(self._stage_names):
            self._stage_names = lines
            return
        for i, (prev, cur) in enumerate(zip(self._stage_names, lines)):
            if not cur:
                continue
            if prev and cur != prev and lines.count(cur) == 1 and prev not in lines and cur not in p.stages:
                p.rename_stage(prev, cur)
            self._stage_names[i] = cur

    # ================================================================== workflow
    def _blind_toggled(self, on):
        p = self.project
        if self._loading or p is None:
            return
        if not on and p.blind and not security.can(p, "reveal_codes"):
            with loading(self):
                self.blind.setChecked(True)
            QMessageBox.information(self, "Unblind", "Only an administrator can reveal the treatment coding of this "
                                    "experiment (File ▸ Users and security).")
            return
        if not on and p.blind and QMessageBox.question(
                self, "Unblind", "Reveal the treatment groups? The experimenter will no longer be blind to the "
                "treatments on the Experiment, Test schedule, Run tests and Review and score pages.") != QMessageBox.Yes:
            with loading(self):
                self.blind.setChecked(True)
            return
        p.blind = on
        if on:
            wf.blind_codes(p)
        self.main.mark_dirty()

    def _store_workflow(self, *_):
        if self._loading or self.project is None:
            return
        self.project.settings_extra["confirm_id"] = self.confirm_id.isChecked()
        self.main.mark_dirty()

    def _store_weigh(self, on: bool):
        if self._loading or self.project is None:
            return
        self.project.require_weight_before_test = bool(on)
        self.main.mark_dirty()

    # ================================================================== training criteria, time periods
    @staticmethod
    def _criterion_row(c: dict) -> dict:
        c = wf.normalize_criterion(c)
        fail, var = c["action_fail"], c["variability"] or {}
        return {**c, "action_met": "complete_stage" if c["action_met"] == "advance" else c["action_met"],
                "after": fail["after_trials"] if fail["action"] == "retire" else 0,
                "var_stat": var.get("stat", ""), "var_measure": var.get("measure", ""),
                "var_trials": var.get("trials", c["consecutive_trials"]), "var_max": var.get("max", 10.0)}

    def _add_criterion(self):
        if self.project is None:
            return
        stage = self.project.stages[0] if self.project.stages else ""
        self.crit.add_record(self._criterion_row({"stage": stage, "measure": "Latency to first entry (s)", "op": "<",
                                                  "value": 10, "consecutive_trials": 3}))
        self._store_criteria()

    def _store_criteria(self, *_):
        if self._loading or self.project is None:
            return
        self.project.training_criteria = [
            {"stage": c["stage"], "measure": c["measure"], "op": c["op"], "value": c["value"] or 0.0,
             "consecutive_trials": c["consecutive_trials"], "action_met": c["action_met"],
             "action_fail": {"after_trials": c["after"], "action": "retire" if c["after"] else "none"},
             "min_trials": c["min_trials"],
             "variability": {"stat": c["var_stat"], "measure": c["var_measure"], "trials": c["var_trials"],
                             "max": c["var_max"] or 0.0} if c["var_stat"] else None}
            for c in self.crit.records()]
        self.main.mark_dirty()

    def _show_variability(self):
        """The Variability row shows the selected criterion's acceptable variability."""
        r = self.crit.currentRow()
        rec = self.crit.records()[r] if 0 <= r < self.crit.rowCount() else None
        with loading(self):
            self.var_stat.setCurrentIndex(max(0, self.var_stat.findData(rec["var_stat"] if rec else "")))
            self.var_measure.setText(rec["var_measure"] if rec else "")
            self.var_trials.setValue(int(rec["var_trials"] or 2) if rec else 3)
            self.var_max.setValue(float(rec["var_max"] or 0.0) if rec else 10.0)
        self.var_stat.setEnabled(rec is not None)
        on = rec is not None and bool(rec["var_stat"])
        for w in (self.var_measure, self.var_trials, self.var_max):
            w.setEnabled(on)

    def _variability_edited(self, *_):
        """Store the Variability row into the selected criterion (its hidden cells, so the row keeps it)."""
        r = self.crit.currentRow()
        if self._loading or not 0 <= r < self.crit.rowCount():
            return
        t, cols = self.crit, _VAR_COLS
        t.cellWidget(r, cols["var_stat"]).setCurrentIndex(max(0, t.cellWidget(r, cols["var_stat"]).findData(
            self.var_stat.currentData())))
        t.item(r, cols["var_measure"]).setText(self.var_measure.text().strip())
        t.cellWidget(r, cols["var_trials"]).setValue(self.var_trials.value())
        t.item(r, cols["var_max"]).setText(f"{self.var_max.value():g}")
        self._show_variability()

    def _add_event_period(self):
        zone = ""
        p = self.project
        if p is not None and p.apparatus and p.apparatus[0].zones:
            zone = p.apparatus[0].zones[-1].name
        n = self.ev_periods.rowCount() + 1
        self.ev_periods.add_record({"label": f"Event period {n}", "anchor": "first_exit", "target": zone,
                                    "offset_s": 0, "duration_s": 30, "occurrence": 1, "end_anchor": "duration",
                                    "end_offset_s": 0, "end_occurrence": 1})
        self._store_event_periods()

    @staticmethod
    def _event_period_record(d: dict) -> dict:
        """A stored event-anchored period as a row of the table (its start's and end's zone / key / input /
        calculation in one column each)."""
        end = d.get("end") if isinstance(d.get("end"), dict) else {}
        ea = end.get("anchor") or "duration"
        return {**d, "target": d.get(_TARGET_KEY.get(d.get("anchor", ""), "zone"), ""), "end_anchor": ea,
                "end_target": end.get(TARGET_KEY.get(ea, "zone"), ""), "end_offset_s": end.get("offset_s", 0),
                "end_occurrence": end.get("occurrence", 1)}

    def _store_event_periods(self, *_):
        if self._loading or self.project is None:
            return
        out, bad = [], []
        for r, d in enumerate(self.ev_periods.records()):
            if None in (d["offset_s"], d["duration_s"], d["occurrence"]):
                bad.append(d["label"] or f"Event period {r + 1}")
                continue
            target = d.pop("target")
            if d["anchor"] in _TARGET_KEY:
                d[_TARGET_KEY[d["anchor"]]] = target
            ea, et = d.pop("end_anchor") or "duration", d.pop("end_target")
            eo, en = d.pop("end_offset_s"), d.pop("end_occurrence")
            if ea != "duration":  # ends at an event or a calculation's time (an empty offset / occurrence: 0 / 1)
                d["end"] = {"anchor": ea, TARGET_KEY.get(ea, "zone"): et, "offset_s": eo or 0.0,
                            "occurrence": max(1, en or 1)}
            out.append({**d, "label": d["label"] or f"Event period {r + 1}"})
        self.project.analysis.event_periods = out
        self._show_event_period_problems(bad)
        self.main.mark_dirty()

    def _show_event_period_problems(self, bad: list[str]):
        """The periods not stored (numbers missing) and those whose calculation cannot define them (unknown, worked
        out from other trials, circular reference: they are left out or end at the end of the test)."""
        p = self.project
        self._show_rejected(self.ev_periods_lbl, bad, "the offset, duration and occurrence must be numbers")
        problems = check_periods(p.analysis.event_periods, p.calculations, info_columns(p) + [ERROR_COLUMN],
                                 io_only=p.settings_extra.get("mode") == "io_only") if p is not None else []
        if problems:
            text = "\n".join(f"“{label}”: {msg}" for label, msg in problems)
            self.ev_periods_lbl.setText((self.ev_periods_lbl.text() + "\n" if bad else "") + text)
            self.ev_periods_lbl.setVisible(True)

    @staticmethod
    def _show_rejected(lbl: QLabel, labels: list[str], why: str):
        """Periods that cannot be used are not stored: say so (they are gone once the page is reloaded)."""
        if labels:
            names = ", ".join(f"“{x}”" for x in labels)
            lbl.setText(f"Not used: {names} — {why}. Correct {'it' if len(labels) == 1 else 'them'}, or "
                        f"{'it is' if len(labels) == 1 else 'they are'} left out.")
        lbl.setVisible(bool(labels))

    def _add_period(self):
        ends = [p["end"] for p in self.periods.records()[-1:] if p["end"] is not None]
        start = ends[0] if ends else 0.0
        self.periods.add_record({"label": f"Period {self.periods.rowCount() + 1}", "start": start, "end": start + 60})
        self._store_periods()

    def _store_periods(self, *_):
        if self._loading or self.project is None:
            return
        recs = self.periods.records()
        ok = [p for p in recs if None not in (p["start"], p["end"]) and p["end"] > p["start"]]
        self.project.analysis.custom_periods = [[p["label"], p["start"], p["end"]] for p in ok]
        self._show_rejected(self.periods_lbl, [p["label"] or f"Period {i + 1}" for i, p in enumerate(recs)
                                               if p not in ok], "a period needs a start and an end after it")
        self.main.mark_dirty()
