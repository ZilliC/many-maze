"""Protocol page: the experiment's protocol elements, one property page at a time as in ANY-maze.

The elements (Protocol, Animal tracking, Stages, Keys, Procedures, Analysis, Calculations, Hardware) are listed in
the explorer
under "Protocol" (``explorer_items`` / ``show_item``) and shown in an internal stack. Every widget of the old
single-form page is kept as an attribute (``name``, ``det_form``, ``beh``, ``crit``, ``ev_periods`` …)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import plugins, pose, security
from ...core import workflow as wf
from ...core.apparatus import unique_name
from ...core.calculations import Calculation, check_calculation, evaluate_calc, parse
from ...core.export import display_text
from ...core.measures import AnalysisSettings
from ...core.periods import ANCHORS
from ...core.project import ERROR_COLUMN, Behaviour, result_columns
from ...core.sync import sync_from
from ...core.templates import TEMPLATES
from ...core.tracking import DetectionSettings
from ..icons import icon
from ..pose_model import PoseModelBox
from ..widgets import ColorButton, RecordTable, button_row, hint, loading, run_with_progress, separator, style_table
from ._results_cache import cached_rows, get_rows, info_columns
from .base import (ANALYSIS_SECTIONS, ANALYSIS_SPEC, DETECTION_SECTIONS, DETECTION_SPEC, Page, SettingsForm,
                   property_form)
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
# key types of the keys table (the property page offers the full ANY-maze wording)
KIND_LABELS = [("hold", "Simple"), ("state", "Toggle"), ("state", "Radio"), ("point", "Event")]
BEH_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16", "#f97316",
              "#06b6d4"]
MET_ACTIONS = [("complete_stage", "Stage completed: skip remaining trials"), ("report", "Report only")]
FORM_WIDTH = 900  # property pages with only settings stay at a readable width
LOCKED_TEXT = ("The protocol is locked: only an administrator can change it (File ▸ Users and security). You can look "
               "at it and run tests.")


# record tables (see RecordTable): training criteria, time periods, event-anchored time periods
CRITERIA_COLS = [("stage", "Stage", "text_choice", None),  # options: the stages, given by the page
                 ("measure", "Measure", "text", None), ("op", "Is", "choice", [(o, o) for o in ("<", "<=", ">", ">=")]),
                 ("value", "Value", "number", None),
                 ("consecutive_trials", "Consecutive trials", "spin", (1, wf.MAX_TRIALS)),
                 ("action_met", "When met", "choice", MET_ACTIONS),
                 ("after", "Retire after", "spin", (0, wf.MAX_TRIALS, "never", " trials"))]
PERIOD_COLS = [("label", "Time period", "text", None), ("start", "Starts at (s)", "number", None),
               ("end", "Ends at (s)", "number", None)]
EVENT_PERIOD_COLS = [("label", "Time period", "text", None),
                     ("anchor", "The period starts at", "choice", list(ANCHORS.items())),
                     ("target", "Zone / key / input", "text", None), ("offset_s", "Offset (s)", "number", None),
                     ("duration_s", "Duration (s)", "number", None), ("occurrence", "Occurrence", "int", None)]
_TARGET_KEY = {"first_entry": "zone", "first_exit": "zone", "mark": "behaviour", "input": "channel"}


class ExperimentPage(Page):
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
        self.lock_lbl.setStyleSheet("background:#fff7e0;border-bottom:1px solid #f0d58a;padding:6px 28px;")
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
        f.addRow("Protocol name", self.name)
        f.addRow("Description", self.desc)
        f.addRow("Type of test", self.protocol)
        f.addRow("Mode", self.mode)
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
        pg.finish()

    def _build_tracking(self):
        pg = self._element_page("tracking", "Animal tracking", "How the animal is found in every frame. These are "
                                "the defaults of all tests; a test can override them on the Review and score page.",
                                FORM_WIDTH)
        self.takenote_lbl = QLabel("This protocol uses TakeNote mode: tests are scored by hand and these settings "
                                   "are only used if you track a test anyway.")
        self.takenote_lbl.setWordWrap(True)
        self.takenote_lbl.setStyleSheet("background:#fff7e0;border:1px solid #f0d58a;padding:6px 8px;")
        self.takenote_lbl.hide()
        pg.add(self.takenote_lbl)
        self.det_form = SettingsForm(DETECTION_SPEC, sections=DETECTION_SECTIONS)
        self.det_form.changed.connect(self.main.mark_dirty)
        self.det_form.changed.connect(self._update_pose_box)
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
        self.stages_lbl.setStyleSheet("color:#dc2626;")
        self.stages_lbl.hide()
        pg.add(self.stages_lbl)
        pg.add(separator())

        pg.section("Training criteria")
        pg.add(hint("A stage is completed when a result measure meets the condition on N consecutive trials; "
                    "animals that have not met it after the given number of trials can be retired. Apply the "
                    "criteria on the Animals page."))
        cols = [c if c[0] != "stage" else c[:3] + (lambda: self.project.stages if self.project else [],)
                for c in CRITERIA_COLS]
        self.crit = RecordTable(cols, stretch=(1,))
        for c, wd in ((0, 140), (2, 60), (3, 80), (4, 130), (5, 250), (6, 110)):
            self.crit.setColumnWidth(c, wd)
        self.crit.horizontalHeaderItem(6).setToolTip("Retire animals that have not met the criterion after this many "
                                                     "trials of the stage")
        self.crit.setMinimumHeight(170)
        self.crit.edited.connect(self._store_criteria)
        pg.add(self.crit)
        pg.add(button_row(small_button("Add criterion", "add", slot=self._add_criterion),
                          small_button("Remove", "delete", slot=self.crit.remove_current)))
        pg.finish()

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
        self.beh_lbl.setStyleSheet("color:#dc2626;")
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
        pg.add(hint("A period anchored to an event — e.g. the 30 s after the animal first leaves the start box. "
                    "Duration 0 = until the end of the test. Occurrence: 1 = first, 2 = second…, 0 = one period "
                    "for every occurrence. Periods whose event never happens are left out."))
        self.ev_periods = RecordTable(EVENT_PERIOD_COLS, stretch=(0, 2))
        self.ev_periods.setColumnWidth(1, 230)
        for c in (3, 4, 5):
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
        lbl.setStyleSheet("color:#dc2626;")
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
        return [(label, ic, key) for key, label, ic in ELEMENTS]

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
        self.ev_periods.set_records({**d, "target": d.get(_TARGET_KEY.get(d.get("anchor", ""), "zone"), "")}
                                    for d in p.analysis.event_periods)
        self.periods_lbl.hide()
        self.ev_periods_lbl.hide()
        self.det_form.load(p.detection)
        self.an_form.load(p.analysis)
        self._fill_calculations()
        self._fill_plugins()

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

    def _update_mode(self):
        mode = self.mode.currentData()
        self.takenote_lbl.setText("This protocol uses Input/output only mode: tests run without a camera and these "
                                  "settings are not used." if mode == "io_only" else
                                  "This protocol uses TakeNote mode: tests are scored by hand and these settings "
                                  "are only used if you track a test anyway.")
        self.takenote_lbl.setVisible(mode in ("takenote", "io_only"))

    def _store_mode(self, *_):
        self._update_mode()
        if self._loading or self.project is None:
            return
        self.project.settings_extra["mode"] = self.mode.currentData()
        self.main.mark_dirty()

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
        self._append_behaviour_row(Behaviour(name, wf.free_key(self.project.behaviours), b.kind, b.group, ""))
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

    # ================================================================== calculations
    def _fill_calculations(self):
        p = self.project
        cur = self.calc_list.currentRow()
        self.calc_list.blockSignals(True)
        self.calc_list.clear()
        for c in p.calculations if p is not None else []:
            self.calc_list.addItem(QListWidgetItem(icon("calculator"), c.column or "(no name)"))
        n = self.calc_list.count()
        if n:
            self.calc_list.setCurrentRow(min(max(cur, 0), n - 1))
        self.calc_list.blockSignals(False)
        self._calc_rows = self._calc_measures = None
        self._show_calculation()

    def current_calculation(self) -> Calculation | None:
        r = self.calc_list.currentRow()
        p = self.project
        return p.calculations[r] if p is not None and 0 <= r < len(p.calculations) else None

    def _show_calculation(self):
        self.calc_editor.load(self.current_calculation())
        self._check_calculation()

    def _calculation_rows(self) -> list[dict] | None:
        """Results rows to check the formulas against (the cached results, if any: computing them is slow)."""
        if self._calc_rows is None and self.project is not None:
            self._set_calculation_rows(cached_rows(self.project, False))
        return self._calc_rows

    def _set_calculation_rows(self, rows):
        """Keep results rows (made with the current calculations) and their measure columns."""
        self._calc_rows = rows
        calcs = {c.column for c in self.project.calculations} if self.project is not None else set()
        self._calc_measures = [c for c in result_columns(rows) if c not in calcs] if rows is not None else None

    def _check_calculation(self):
        """Show the problems of the selected calculation, or its result for the first test."""
        c, p = self.current_calculation(), self.project
        if c is None:
            self.calc_editor.set_status([])
            return
        rows = self._calculation_rows()
        reserved = info_columns(p) + [ERROR_COLUMN, "Warnings"]
        measures = [m for m in self._calc_measures or [] if m not in reserved] if rows else None
        errs = check_calculation(c, measures, p.calculations, reserved)
        text = "The results are worked out when they are shown on the Data page."
        row = next((r for r in rows or [] if ERROR_COLUMN not in r), None)
        if not errs and row is not None:
            if parse(c.formula).functions:
                text = "Worked out for every test when the results are calculated (it uses other trials or periods)."
            else:
                v = evaluate_calc(c, row)
                v = f"{v:.{c.decimals}f}" if isinstance(v, float) and v == v else display_text(v) or "undefined"
                text = f"Result for test {row.get('Test')} (animal {row.get('Animal')}): {v}"
        self.calc_editor.set_status(errs, text)

    def _calculation_edited(self, new: Calculation):
        r = self.calc_list.currentRow()
        p = self.project
        if p is None or not 0 <= r < len(p.calculations) or p.calculations[r] == new:
            return
        old = p.calculations[r].column
        p.calculations[r] = new
        # renamed: the other formulas follow (unless the old name is a measure's: theirs may mean the measure)
        if new.column and old and new.column != old and old not in (self._calc_measures or ()):
            for c in p.calculations:
                if c is not new:
                    c.formula = c.formula.replace("{" + old + "}", "{" + new.column + "}")
        self.calc_list.item(r).setText(new.column or "(no name)")
        self.main.mark_dirty()
        self._check_calculation()

    def new_calculation(self):
        p = self.project
        if p is None:
            return
        self._goto_element("calculations")
        name = unique_name("Calculation 1" if not p.calculations else f"Calculation {len(p.calculations) + 1}",
                           [c.name for c in p.calculations])
        p.calculations.append(Calculation(name, ""))
        self.main.mark_dirty()
        self._fill_calculations()
        self.calc_list.setCurrentRow(len(p.calculations) - 1)
        self.calc_editor.name.setFocus()
        self.calc_editor.name.selectAll()

    def duplicate_calculation(self):
        c, p = self.current_calculation(), self.project
        if c is None:
            return
        d = Calculation.from_dict(c.to_dict())
        d.name = unique_name(f"{c.name} copy", [x.name for x in p.calculations])
        p.calculations.insert(self.calc_list.currentRow() + 1, d)
        self.main.mark_dirty()
        row = self.calc_list.currentRow() + 1
        self._fill_calculations()
        self.calc_list.setCurrentRow(row)

    def delete_calculation(self, confirm: bool = True):
        c, p = self.current_calculation(), self.project
        if c is None:
            return
        users = [x.column for x in p.calculations if x is not c and "{" + c.column + "}" in x.formula]
        msg = f"Delete the calculation “{c.column or c.name}”?"
        if users:
            msg += "\n\nIts result is used by: " + ", ".join(users) + " (their results will be blank)."
        if confirm and QMessageBox.question(self, "Delete calculation", msg) != QMessageBox.Yes:
            return
        p.calculations.remove(c)
        self.main.mark_dirty()
        self._fill_calculations()

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
        p.protocol = key
        p.test_duration_s = float(t.default_duration_s)
        self.on_show()
        self.main.mark_dirty()
        self.main.status(f"Protocol set to {t.title} ({t.default_duration_s:g} s tests). Use “Apparatus from "
                         "template” to draw its apparatus.")

    def restore_detection_defaults(self):
        self._restore_defaults(self.project.detection if self.project else None, DetectionSettings(), DETECTION_SPEC,
                               self.det_form, "animal tracking")
        self._update_pose_box()

    def restore_analysis_defaults(self):
        self._restore_defaults(self.project.analysis if self.project else None, AnalysisSettings(), ANALYSIS_SPEC,
                               self.an_form, "analysis")

    def _restore_defaults(self, obj, default, spec, form, what):
        if obj is None or QMessageBox.question(self, "Restore defaults",
                                               f"Restore the default {what} settings?") != QMessageBox.Yes:
            return
        for attr, *_ in spec:
            setattr(obj, attr, getattr(default, attr))
        form.load(obj)
        self.main.mark_dirty()

    # ================================================================== analysis plug-ins
    def _fill_plugins(self):
        p = self.project
        row = self.plugin_list.currentRow()
        self.plugin_list.clear()
        for c in (p.analysis_plugins if p is not None else []):
            pl = plugins.analysis_plugin(c.get("plugin", ""))
            kind = pl.title if pl is not None else f"{c.get('plugin')} (not installed)"
            off = "" if c.get("enabled", True) else " — not run"
            self.plugin_list.addItem(QListWidgetItem(icon("chart"), f"{c.get('name') or kind}  ·  {kind}{off}"))
        if self.plugin_list.count():
            self.plugin_list.setCurrentRow(min(max(row, 0), self.plugin_list.count() - 1))

    def _fill_plugin_menu(self):
        self.plugin_menu.clear()
        for name in plugins.analysis_names():
            pl = plugins.analysis_plugin(name)
            a = self.plugin_menu.addAction(pl.title)
            a.setToolTip(pl.description)
            a.triggered.connect(lambda _=False, n=name: self.add_plugin(n))

    def add_plugin(self, name: str, dlg=None) -> dict | None:
        """Add a configured analysis plug-in to the protocol (its settings are asked first)."""
        p = self.project
        if p is None:
            return None
        cfg = plugins.new_config(name, [c.get("name") for c in p.analysis_plugins])
        cfg = self._plugin_dialog(cfg, dlg)
        if cfg is None:
            return None
        p.analysis_plugins.append(cfg)
        self.main.mark_dirty()
        self._fill_plugins()
        self.plugin_list.setCurrentRow(self.plugin_list.count() - 1)
        return cfg

    def _plugin_dialog(self, cfg: dict, dlg=None) -> dict | None:
        from ..plugin_dialog import PluginOptionsDialog

        given = dlg is not None
        dlg = dlg or PluginOptionsDialog(self.project, cfg, self)
        if not given and dlg.exec() != QDialog.Accepted:
            return None
        return dlg.values()

    def edit_plugin(self, dlg=None) -> dict | None:
        p = self.project
        i = self.plugin_list.currentRow()
        if p is None or not 0 <= i < len(p.analysis_plugins):
            return None
        cfg = self._plugin_dialog(p.analysis_plugins[i], dlg)
        if cfg is None:
            return None
        p.analysis_plugins[i] = cfg
        self.main.mark_dirty()
        self._fill_plugins()
        return cfg

    def remove_plugin(self):
        p = self.project
        i = self.plugin_list.currentRow()
        if p is None or not 0 <= i < len(p.analysis_plugins):
            return
        del p.analysis_plugins[i]
        self.main.mark_dirty()
        self._fill_plugins()

    def run_plugins(self, wait: bool = False):
        """Run the analysis plug-ins on every test performed (in the background), then save the experiment."""
        p = self.project
        if p is None or not p.analysis_plugins:
            return None
        if p.path is None and not self.main.save():
            return None

        def done(res):
            self.main.mark_dirty()
            self.main.save()
            msg = f"Ran the analysis plug-ins on {len(res['done'])} test(s)."
            self.main.status(msg)
            if res["errors"]:
                lines = [f"Test {tid}: {m}" for tid, m in res["errors"][:20]]
                QMessageBox.warning(self, "Analysis plug-ins", msg + "\n\n" + "\n".join(lines))
            self.last_plugin_run = res

        w = run_with_progress(self, "Running the analysis plug-ins",
                              lambda progress, stop: plugins.run_analysis_plugins(p, progress=progress),
                              on_done=done, on_fail=lambda m: QMessageBox.warning(self, "Analysis plug-ins", m),
                              cancellable=False)
        if wait:
            w.wait()
            QApplication.processEvents()
        return w

    def edit_io_devices(self):
        if self.project is None:
            return None
        from ..io_devices_dialog import IODevicesDialog
        dlg = IODevicesDialog(self.project, self)
        dlg.changed.connect(self.main.mark_dirty)
        dlg.exec()
        self._update_hardware()
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
        p.protocol = self.protocol.currentData()
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

    # ================================================================== keys (manually scored behaviours)
    def _append_behaviour_row(self, b: Behaviour):
        with loading(self):
            self._add_behaviour_cells(b)

    def _add_behaviour_cells(self, b: Behaviour):
        r = self.beh.rowCount()
        self.beh.insertRow(r)
        self._key_names.insert(r, b.name)
        self.beh.setItem(r, 0, QTableWidgetItem(b.name))
        k = QTableWidgetItem(b.key.upper() if len(b.key) == 1 else b.key)
        k.setTextAlignment(Qt.AlignCenter)
        self.beh.setItem(r, 1, k)
        kind = QComboBox()
        for v, label in KIND_LABELS:
            kind.addItem(label, v)
        kind.setToolTip("Simple: while the key is held down · Toggle: first press starts, second press ends · "
                        "Radio: a toggle that also ends when another key of its radio set is pressed · Event: "
                        "an instant")
        kind.setCurrentIndex(self._kind_index(b.kind, b.group))
        kind.currentIndexChanged.connect(lambda _i, w=kind: self._kind_changed(w))
        self.beh.setCellWidget(r, 2, kind)
        self.beh.setItem(r, 3, QTableWidgetItem(b.group))
        col = ColorButton(b.color or BEH_COLORS[r % len(BEH_COLORS)], "Key colour")
        col.setFixedSize(52, 22)
        col.setToolTip("Colour of the on-screen scoring button")
        col.color_changed.connect(lambda _c: self._store_behaviours())
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(6, 0, 6, 0)
        hl.addWidget(col)
        holder.setProperty("color", col.color())
        holder.button = col
        col.color_changed.connect(lambda c, h=holder: h.setProperty("color", c))
        self.beh.setCellWidget(r, 4, holder)

    @staticmethod
    def _kind_index(kind: str, group: str) -> int:
        mode = wf.key_mode(kind, group)
        return {"simple": 0, "toggle": 1, "radio": 2, "event": 3}[mode]

    def _kind_changed(self, combo: QComboBox):
        """The table's "How it works" changed: Radio keys need a radio set, Toggle keys have none."""
        if self._loading:
            return
        r = next((r for r in range(self.beh.rowCount()) if self.beh.cellWidget(r, 2) is combo), -1)
        if r < 0:
            return
        it = self.beh.item(r, 3)
        group = it.text().strip() if it else ""
        mode = ("simple", "toggle", "radio", "event")[combo.currentIndex()]
        _kind, new_group = wf.mode_to_kind(mode, group)
        if new_group != group:
            with loading(self):
                self.beh.setItem(r, 3, QTableWidgetItem(new_group))
        self._store_behaviours()

    def _add_behaviour(self):
        if self.project is None:
            return
        key = wf.free_key(self.project.behaviours)
        self._append_behaviour_row(Behaviour(f"Behaviour {self.beh.rowCount() + 1}", key, "state"))
        self.beh.setCurrentCell(self.beh.rowCount() - 1, 0)
        self._store_behaviours()

    def _remove_behaviour(self, confirm: bool = True):
        r = self.beh.currentRow()
        if r < 0:
            return
        b = self._row_behaviour(r)
        n, n_tests = self.project.key_events(b.name) if b is not None and self.project is not None else (0, 0)
        if n and confirm and QMessageBox.question(
                self, "Delete key", f"The key “{b.name}” has {n} scored event{'s' if n != 1 else ''} in {n_tests} "
                f"test{'s' if n_tests != 1 else ''}. Delete the key?\n\nThe events stay in the tests but are no longer "
                "analysed (a key of the same name analyses them again).") != QMessageBox.Yes:
            return
        if r < len(self._key_names):
            self._key_names.pop(r)
        self.beh.removeRow(r)
        self._store_behaviours()

    def _row_behaviour(self, r) -> Behaviour | None:
        name = self.beh.item(r, 0).text().strip() if self.beh.item(r, 0) else ""
        if not name:
            return None
        key = self.beh.item(r, 1).text().strip()[:1].lower() if self.beh.item(r, 1) else ""
        kind = self.beh.cellWidget(r, 2).currentData() if self.beh.cellWidget(r, 2) else "state"
        group = self.beh.item(r, 3).text().strip() if self.beh.item(r, 3) else ""
        color = self.beh.cellWidget(r, 4).property("color") if self.beh.cellWidget(r, 4) else ""
        return Behaviour(name, key, kind, group, color or "")

    def _store_behaviours(self, *_):
        if self._loading or self.project is None:
            return
        self._follow_key_renames()
        out = [b for b in (self._row_behaviour(r) for r in range(self.beh.rowCount())) if b is not None]
        self.project.behaviours = out
        with loading(self):
            for r in range(self.beh.rowCount()):  # Toggle ⇄ Radio follows the radio set
                combo, b = self.beh.cellWidget(r, 2), self._row_behaviour(r)
                if combo is not None and b is not None:
                    combo.setCurrentIndex(self._kind_index(b.kind, b.group))
        self._validate_behaviours()
        self._show_key()
        self._update_summary()
        self.main.mark_dirty()

    def _follow_key_renames(self):
        """A renamed key keeps its data: scored events, marked time periods and criteria follow the new name
        (p.rename_key). Rows are matched by position (the table's rows are the keys)."""
        names = [self.beh.item(r, 0).text().strip() if self.beh.item(r, 0) else "" for r in range(self.beh.rowCount())]
        if len(names) != len(self._key_names):
            self._key_names = names
            return
        for r, (prev, cur) in enumerate(zip(self._key_names, names)):
            if not cur:
                continue
            if prev and cur != prev and names.count(cur) == 1 and prev not in names:
                self.project.rename_key(prev, cur)
            self._key_names[r] = cur

    def _validate_behaviours(self) -> list[str]:
        errs = wf.validate_behaviours(self.project.behaviours) if self.project is not None else []
        self.beh_lbl.setText("<br>".join(errs))
        self.beh_lbl.setVisible(bool(errs))
        return errs

    def _show_key(self):
        """Show the selected key on the "Key" property page."""
        r = self.beh.currentRow()
        b = self._row_behaviour(r) if 0 <= r < self.beh.rowCount() else None
        if b is None:
            self.key_editor.load(None)
        else:
            self.key_editor.load(b.name, b.key, b.kind, b.group, b.color)

    def _key_edited(self, v: dict):
        r = self.beh.currentRow()
        if not 0 <= r < self.beh.rowCount():
            return
        with loading(self):
            if v["name"]:
                self.beh.item(r, 0).setText(v["name"])
            key = v["key"]
            self.beh.item(r, 1).setText(key.upper() if len(key) == 1 else key)
            self.beh.cellWidget(r, 2).setCurrentIndex(self._kind_index(v["kind"], v["group"]))
            self.beh.setItem(r, 3, QTableWidgetItem(v["group"]))
            holder = self.beh.cellWidget(r, 4)
            if holder is not None and v["color"]:
                holder.setProperty("color", v["color"])
                holder.button.set_color(v["color"])
        self._store_behaviours()

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
        fail = c["action_fail"]
        return {**c, "action_met": "complete_stage" if c["action_met"] == "advance" else c["action_met"],
                "after": fail["after_trials"] if fail["action"] == "retire" else 0}

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
             "action_fail": {"after_trials": c["after"], "action": "retire" if c["after"] else "none"}}
            for c in self.crit.records()]
        self.main.mark_dirty()

    def _add_event_period(self):
        zone = ""
        p = self.project
        if p is not None and p.apparatus and p.apparatus[0].zones:
            zone = p.apparatus[0].zones[-1].name
        n = self.ev_periods.rowCount() + 1
        self.ev_periods.add_record({"label": f"Event period {n}", "anchor": "first_exit", "target": zone,
                                    "offset_s": 0, "duration_s": 30, "occurrence": 1})
        self._store_event_periods()

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
            out.append({**d, "label": d["label"] or f"Event period {r + 1}"})
        self.project.analysis.event_periods = out
        self._show_rejected(self.ev_periods_lbl, bad, "the offset, duration and occurrence must be numbers")
        self.main.mark_dirty()

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
