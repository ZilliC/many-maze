"""Building blocks of the Protocol section's element pages (ANY-maze property pages): a scrollable page with a
big blue title, flat section headings and sentence-style rows, the "Key" property panel of the Keys element and the
"Calculation" panel of the Calculations element."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...core import workflow as wf
from ...core.calculations import AGGREGATES, MAX_DECIMALS, MAX_NAMED_VALUES, MAX_UNITS, Calculation
from ...core.workflow import RADIO_SET, key_mode, mode_to_kind
from .. import theme
from ..icons import icon
from ..widgets import ColorButton, button_row, hint, loading, separator
from .base import property_form, section_title

# how a key works, as in ANY-maze ("Specify how you'd like this key to work"); see workflow.key_mode
KEY_MODES = [("simple", "Simple - key activity is occurring while key is pressed"),
             ("toggle", "Toggle - key activity starts on first press and ends on second press"),
             ("radio", "Radio - like toggle but also ends if any other radio key is pressed"),
             ("event", "Event - an instantaneous event, scored when the key is pressed")]


def small_button(text: str, icon_name: str | None = None, tip: str = "", slot=None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name))
    if tip:
        b.setToolTip(tip)
    if slot is not None:
        b.clicked.connect(lambda _=False: slot())
    return b


class ElementPage(QScrollArea):
    """One protocol element: a page title, an optional hint and the content in ``self.body``.

    ``max_width`` keeps forms at a readable width (inputs on the right are still wide); ``scroll=False`` gives
    the content the whole height (e.g. the procedure editor)."""

    def __init__(self, title: str, hint_text: str = "", max_width: int | None = None, scroll: bool = True):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if not scroll:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("ElementBody")
        if max_width:
            inner.setMaximumWidth(max_width)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 14, 28, 18)
        self.body.setSpacing(6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("PageTitle")
        self.body.addWidget(self.title_lbl)
        self.hint_lbl = hint(hint_text)
        self.hint_lbl.setVisible(bool(hint_text))
        self.body.addWidget(self.hint_lbl)
        self.body.addSpacing(4)
        self.setWidget(inner)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def add(self, w, stretch: int = 0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w

    def section(self, title: str) -> QLabel:
        return self.add(section_title(title))

    def finish(self):
        self.body.addStretch()


class KeyEditor(QWidget):
    """The "Key" property page of ANY-maze: key name, key stroke, how the key works, radio set, colour and whether
    the behaviour counts as activity.

    ``load(name, key, kind, group, color, activity=False)`` shows a key; ``edited`` is emitted with a dict of the
    new values (name, key, kind, group, color, activity) when the user changes one."""

    edited = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QLabel("Key")
        head.setObjectName("PageTitle")
        lay.addWidget(head)

        f = property_form()
        self.name = QLineEdit()
        self.name.setMinimumWidth(220)
        self.name.editingFinished.connect(self._emit)
        f.addRow(self._label("Key name"), self.name)
        lay.addLayout(f)
        lay.addWidget(separator())

        f = property_form()
        self.stroke = QComboBox()
        self.stroke.setMinimumWidth(220)
        for c in wf.SCORING_KEYS:
            self.stroke.addItem(icon("key"), c.upper() if c.isalpha() else c, c)
        self.stroke.currentIndexChanged.connect(self._emit)
        f.addRow(self._label("Key stroke"), self.stroke)
        lay.addLayout(f)
        lay.addWidget(separator())

        lay.addWidget(QLabel("Specify how you'd like this key to work"))
        self.mode = QComboBox()
        for v, text in KEY_MODES:
            self.mode.addItem(icon("key"), text, v)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        lay.addWidget(self.mode)

        f = property_form()
        self.group = QLineEdit()
        self.group.setMinimumWidth(220)
        self.group.setPlaceholderText(RADIO_SET)
        self.group.setToolTip("Keys in the same set cannot be active together: pressing one ends the others. "
                              "Radio keys share a set; give different sets to independent groups of radio keys.")
        self.group.editingFinished.connect(self._emit)
        self.group_lbl = QLabel("Radio set (keys in a set end each other)")
        f.addRow(self.group_lbl, self.group)
        self.color = ColorButton("#22c55e", "Key colour", width=64)
        self.color.setToolTip("Colour of the on-screen scoring button and of the key's bouts in plots")
        self.color.color_changed.connect(lambda _c: self._emit())
        f.addRow("Colour of the scoring button", self.color)
        lay.addLayout(f)
        self.activity = QCheckBox("This behaviour counts as activity")
        self.activity.setToolTip("As ANY-maze, the animal is active while it is mobile or doing a behaviour that "
                                 "counts as activity (e.g. grooming): Time active, active / inactive episodes. Used "
                                 "when Protocol ▸ Analysis ▸ Activity is measured as in ANY-maze; Simple, Toggle "
                                 "and Radio keys only.")
        self.activity.toggled.connect(self._emit)
        lay.addWidget(self.activity)
        lay.addStretch()
        self.setEnabled(False)

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setMinimumWidth(110)
        return lbl

    def load(self, name: str | None, key: str = "", kind: str = "state", group: str = "", color: str = "",
             activity: bool = False):
        """Show a key (name None = no key selected)."""
        with loading(self):
            self.setEnabled(name is not None)
            self.name.setText(name or "")
            i = self.stroke.findData((key or "").lower())
            if i < 0 and key:
                self.stroke.addItem(icon("key"), key, key)
                i = self.stroke.count() - 1
            self.stroke.setCurrentIndex(max(i, 0) if key else -1)
            self.mode.setCurrentIndex(max(0, self.mode.findData(key_mode(kind, group))))
            self.group.setText(group)
            if color:
                self.color.set_color(color)
            self.activity.setChecked(bool(activity))
            self._update_group_row()

    def _update_group_row(self):
        on = self.mode.currentData() == "radio" or bool(self.group.text().strip())
        self.group.setVisible(on)
        self.group_lbl.setVisible(on)
        self.activity.setEnabled(self.mode.currentData() != "event")  # an instant has no duration

    def _mode_changed(self, *_):
        if self._loading:
            return
        kind, group = mode_to_kind(self.mode.currentData(), self.group.text().strip())
        with loading(self):
            self.group.setText(group)
        self._update_group_row()
        self._emit()

    def values(self) -> dict:
        kind, group = mode_to_kind(self.mode.currentData(), self.group.text().strip())
        if self.mode.currentData() in ("simple", "event"):
            group = self.group.text().strip()
        return {"name": self.name.text().strip(), "key": self.stroke.currentData() or "", "kind": kind,
                "group": group, "color": self.color.color(), "activity": self.activity.isChecked()}

    def _emit(self, *_):
        if self._loading or not self.isEnabled():
            return
        self._update_group_row()
        self.edited.emit(self.values())



# "Insert function" menu of the formula: (label, function, arguments after the measure; None: a maths function)
CALC_FUNCTION_MENU = [("Result for part of the test", "result_for_period", ", 0, 60"),
                      ("Result for a trial", "result_for_trial", ", {stage}, 1"),
                      ("Result for the last trial of a stage", "result_for_last_trial", ", {stage}")] + \
    [(f"{label} across the trials of a stage", fn, ", {stage}") for fn, label in AGGREGATES.items()]
MATHS_MENU = [("abs", "absolute value"), ("sqrt", "square root"), ("log10", "logarithm (base 10)"),
              ("log", "natural logarithm"), ("round", "round(x, decimals)"), ("min", "smallest of the values"),
              ("max", "largest of the values"), ("is_undefined", "1 if the value is undefined")]


class CalculationEditor(QWidget):
    """The "Calculation" property page of ANY-maze: the calculation's name, the decimal places and units of its
    result, the graph Y axis range, named values and the formula.

    ``load(calc)`` shows a calculation (None: none selected); ``edited`` is emitted with a new Calculation holding
    the values when the user changes one. "Insert measure…" calls ``pick_measure(done)``, which should call
    done(column) with the chosen column; ``stages()`` gives the stage names for the inserted trial functions;
    ``set_status(errors, text)`` shows the formula's problems (in red) or a sample result."""

    edited = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.pick_measure = lambda done: None
        self.stages = lambda: []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QLabel("Calculation")
        head.setObjectName("PageTitle")
        lay.addWidget(head)

        f = property_form()
        self.name = QLineEdit()
        self.name.setMinimumWidth(200)
        self.name.editingFinished.connect(self._emit)
        f.addRow("Calculation name", self.name)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, MAX_DECIMALS)
        self.decimals.setFixedWidth(70)
        self.decimals.setToolTip("Results are rounded (not truncated) to this many decimal places")
        self.decimals.valueChanged.connect(self._emit)
        f.addRow("Decimal places of the result", self.decimals)
        self.units = QLineEdit()
        self.units.setMaxLength(MAX_UNITS)
        self.units.setFixedWidth(100)
        self.units.setPlaceholderText("e.g. %")
        self.units.setToolTip(f"Optional, up to {MAX_UNITS} characters: shown in brackets after the name")
        self.units.editingFinished.connect(self._emit)
        f.addRow("Units of the result", self.units)
        lay.addLayout(f)
        lay.addWidget(separator())

        lay.addWidget(section_title("Graph Y axis range"))
        lay.addWidget(hint("Leave blank for automatic scaling. A fixed range (e.g. 0–100 for a percentage) is used "
                           "while every result fits in it."))
        f = property_form()
        self.y_max, self.y_min = self._number_edit("Automatic"), self._number_edit("Automatic")
        f.addRow("Maximum of the Y axis", self.y_max)
        f.addRow("Minimum of the Y axis", self.y_min)
        lay.addLayout(f)
        lay.addWidget(separator())

        lay.addWidget(section_title("Named values"))
        lay.addWidget(hint("Constants with a name to use in the formula, e.g. Limit = 3: change the value here "
                           "instead of in the formula."))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)
        self.named = []
        for i in range(MAX_NAMED_VALUES):
            n = QLineEdit()
            n.setPlaceholderText("Name")
            n.editingFinished.connect(self._emit)
            v = self._number_edit("Value")
            g.addWidget(n, i, 0)
            g.addWidget(v, i, 1)
            self.named.append((n, v))
        g.setColumnStretch(2, 1)
        lay.addLayout(g)
        lay.addWidget(separator())

        lay.addWidget(section_title("Formula"))
        lay.addWidget(hint("Measures are written in braces, e.g. ({Novel: time (s)} − {Familiar: time (s)}) / "
                           "({Novel: time (s)} + {Familiar: time (s)}). Operators + − * / ( ), comparisons and "
                           "the functions below; the result is blank when a value it needs is blank."))
        self.formula = QPlainTextEdit()
        self.formula.setFixedHeight(92)
        self.formula.setPlaceholderText("100 * {Open arms: time (s)} / {Test duration (s)}")
        self.formula.textChanged.connect(self._emit)
        lay.addWidget(self.formula)
        self.insert_measure_btn = small_button("Insert measure…", "add", "Choose a measure (or another calculation) "
                                               "from the results", self._insert_measure)
        self.insert_function_btn = small_button("Insert function", "calculator", "Functions across trials and for "
                                                "part of the test, and maths functions")
        m = QMenu(self)
        for label, fn, rest in CALC_FUNCTION_MENU:
            m.addAction(label, lambda fn=fn, rest=rest: self._insert_function(fn, rest))
        m.addSeparator()
        for fn, tip in MATHS_MENU:
            a = m.addAction(f"{fn}()", lambda fn=fn: self._insert_text(f"{fn}()", back=1))
            a.setToolTip(tip)
        self.insert_function_btn.setMenu(m)
        lay.addLayout(button_row(self.insert_measure_btn, self.insert_function_btn))
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        lay.addStretch()
        self.setEnabled(False)

    def _number_edit(self, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setFixedWidth(120)
        e.setPlaceholderText(placeholder)
        v = QDoubleValidator(e)
        v.setNotation(QDoubleValidator.StandardNotation)
        e.setValidator(v)
        e.editingFinished.connect(self._emit)
        return e

    @staticmethod
    def _number(e: QLineEdit):
        try:
            return float(e.text().strip().replace(",", "."))
        except ValueError:
            return None

    def load(self, calc: Calculation | None):
        """Show a calculation (None: none selected)."""
        with loading(self):
            self.setEnabled(calc is not None)
            c = calc or Calculation("", "")
            self.name.setText(c.name)
            self.decimals.setValue(int(c.decimals))
            self.units.setText(c.units)
            self.y_max.setText("" if c.y_max is None else f"{c.y_max:g}")
            self.y_min.setText("" if c.y_min is None else f"{c.y_min:g}")
            for i, (n, v) in enumerate(self.named):
                name, val = c.named_values[i] if i < len(c.named_values) else ("", None)
                n.setText(name)
                v.setText("" if val is None else f"{val:g}")
            if self.formula.toPlainText() != c.formula:
                self.formula.setPlainText(c.formula)
            if calc is None:
                self.status.clear()

    def values(self) -> Calculation:
        named = [[n.text().strip(), self._number(v)] for n, v in self.named if n.text().strip() or v.text().strip()]
        return Calculation(self.name.text().strip(), self.formula.toPlainText(), self.decimals.value(),
                           self.units.text().strip(), self._number(self.y_max), self._number(self.y_min), named)

    def _emit(self, *_):
        if self._loading or not self.isEnabled():
            return
        self.edited.emit(self.values())

    def set_status(self, errors: list[str], text: str = ""):
        self.status.setStyleSheet(f"color:{theme.ERROR if errors else theme.HINT};")
        self.status.setText("\n".join(errors) if errors else text)

    def _insert_text(self, text: str, back: int = 0):
        self.formula.insertPlainText(text)
        if back:
            cur = self.formula.textCursor()
            cur.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, back)
            self.formula.setTextCursor(cur)
        self.formula.setFocus()

    def _insert_measure(self):
        self.pick_measure(lambda col: self._insert_text("{" + col + "}") if col else None)

    def _insert_function(self, fn: str, rest: str):
        stages = list(self.stages() or [])
        stage = repr(stages[0] if stages else "")

        def done(col):
            if col:
                self._insert_text(f"{fn}({{{col}}}{rest.format(stage=stage)})")

        self.pick_measure(done)
