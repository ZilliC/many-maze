"""Test view: review one test's video with zones and track overlay, tune detection, track, score and edit."""

from __future__ import annotations

import datetime as _dt
import html
import math
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QGraphicsItem,
                               QGraphicsTextItem, QGraphicsView, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLayout, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QStyle, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core import workflow as wf
from ...core.plots import heatmap, speed_trace, track_plot
from ...core.track import Track
from ...core.tracking import ArenaTracker, DetectionSettings, compute_background, draw_overlay
from ..confirm_id import confirm_animal_id
from ..widgets import PlotCanvas, VideoPlayer, Worker, error_box, fmt_time, run_with_progress
from .base import DETECTION_SPEC, Page, SettingsForm
from .tests import track_tests_job

SPEC_ATTRS = [a for a, *_ in DETECTION_SPEC]
ANIMAL_BGR = [(0, 200, 255), (255, 0, 200), (0, 230, 0), (255, 200, 0), (0, 128, 255), (200, 120, 255)]
INFO_KEYS = {"Test", "Animal", "Group", "Sex", "Stage", "Trial", "Apparatus", "Period"}
BEHAVIOUR_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16",
                    "#f97316", "#06b6d4"]
KIND_TEXT = {"state": "toggle", "hold": "hold", "point": "point"}
STATUS_COLORS = {"tracked": "#16a34a", "scored": "#0891b2", "pending": "#d97706", "skipped": "#9333ea",
                 "superseded": "#94a3b8", "excluded": "#94a3b8"}


def fmt_value(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if not math.isfinite(v):
            return "–"
        return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


def _put_text(img, text, org, color, scale):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (0, 0, 0), max(2, int(3 * scale)),
                cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, color, max(1, int(scale)), cv2.LINE_AA)


def _num_item(v, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(fmt_value(v) if not isinstance(v, str) else v)
    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _ro_item(text, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def behaviour_color(b, index: int) -> str:
    return b.color or BEHAVIOUR_COLORS[index % len(BEHAVIOUR_COLORS)]


class FlowLayout(QLayout):
    """Lays out widgets left to right, wrapping to new lines as needed."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do_layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h, sp = r.x(), r.y(), 0, self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + sp, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ScoringPad(QWidget):
    """On-screen scoring buttons (mouse / touch screen), one per behaviour.

    Emits pressed(name) / released(name); hold behaviours are scored between the two."""

    pressed = Signal(str)
    released = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, 6)
        self.buttons: dict[str, QPushButton] = {}
        self._colors: dict[str, str] = {}
        self._active: set[str] = set()

    def set_behaviours(self, behaviours):
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.buttons.clear()
        self._colors.clear()
        for i, b in enumerate(behaviours):
            key = b.key.upper() if b.key else "–"
            btn = QPushButton(f"{b.name}\n[{key}]  {KIND_TEXT.get(b.kind, b.kind)}")
            btn.setFocusPolicy(Qt.NoFocus)  # the keyboard stays with the video
            btn.setMinimumSize(124, 58)
            btn.setToolTip({"hold": "Press and hold while the behaviour lasts",
                            "state": "Press to start, press again to stop",
                            "point": "Press when the event occurs"}.get(b.kind, "")
                           + (f" · exclusive set “{b.group}”" if b.group else ""))
            btn.pressed.connect(lambda n=b.name: self.pressed.emit(n))
            btn.released.connect(lambda n=b.name: self.released.emit(n))
            self.flow.addWidget(btn)
            btn.show()
            self.buttons[b.name] = btn
            self._colors[b.name] = behaviour_color(b, i)
        self.set_active(self._active)
        self.updateGeometry()

    def set_active(self, names):
        self._active = set(names)
        for name, btn in self.buttons.items():
            c = QColor(self._colors[name])
            if name in self._active:
                css = (f"background:{c.name()};color:white;border:2px solid {c.darker(130).name()};"
                       "border-radius:8px;font-weight:bold;padding:4px 10px;")
            else:
                css = (f"background:rgba({c.red()},{c.green()},{c.blue()},38);color:#0f172a;"
                       f"border:2px solid {c.name()};border-radius:8px;padding:4px 10px;")
            btn.setStyleSheet(f"QPushButton{{{css}}}QPushButton:pressed{{background:{c.name()};color:white;}}")


class ObservationClock:
    """Start / pause / stop clock for scoring by direct observation (no video)."""

    def __init__(self, time_fn=time.monotonic):
        self.time_fn = time_fn
        self.state = "stopped"  # stopped | running | paused
        self._acc = 0.0
        self._t0 = 0.0

    def elapsed(self) -> float:
        return self._acc + (self.time_fn() - self._t0 if self.state == "running" else 0.0)

    def start(self):
        if self.state == "stopped":
            self._acc = 0.0
        if self.state != "running":
            self._t0 = self.time_fn()
            self.state = "running"

    def pause(self):
        if self.state == "running":
            self._acc = self.elapsed()
            self.state = "paused"

    def stop(self) -> float:
        self._acc = self.elapsed()
        self.state = "stopped"
        return self._acc

    def reset(self):
        self.state, self._acc = "stopped", 0.0


class TestViewPage(Page):
    title = "Test view"

    def __init__(self, main):
        super().__init__(main)
        self.test = None
        self.tracks: list[Track] = []
        self.det_obj: DetectionSettings | None = None
        self._bg_cache: dict[tuple, np.ndarray] = {}
        self._bg_worker: Worker | None = None
        self._bg_key: tuple | None = None
        self._bg_error: str = ""
        self._open_states: dict[str, float] = {}
        self._confirmed: set[int] = set()  # tests whose animal ID was confirmed this session
        self.clock = ObservationClock()
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(100)
        self._clock_timer.timeout.connect(self._clock_tick)
        self._event_rows: list[tuple[dict, bool]] = []
        self._undo: list[tuple[int, Track | None]] = []
        self._range: list[float | None] = [None, None]
        self._stale = {"results": True, "plots": True}
        self._loading = False
        self._tracking = False
        self.preview_info = ""

        st = self.style()
        # ---- top bar -----------------------------------------------------------
        self.test_combo = QComboBox()
        self.test_combo.setMinimumWidth(300)
        self.test_combo.currentIndexChanged.connect(self._combo_changed)
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(st.standardIcon(QStyle.SP_ArrowBack))
        self.prev_btn.setToolTip("Previous test")
        self.prev_btn.clicked.connect(lambda: self.step_test(-1))
        self.next_btn = QPushButton()
        self.next_btn.setIcon(st.standardIcon(QStyle.SP_ArrowForward))
        self.next_btn.setToolTip("Next test")
        self.next_btn.clicked.connect(lambda: self.step_test(1))
        self.info_lbl = QLabel()
        self.info_lbl.setStyleSheet("color:#475569;")
        self.track_btn = QPushButton("Track this test")
        self.track_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.track_btn.setStyleSheet("font-weight:bold;padding:4px 12px;")
        self.track_btn.clicked.connect(self.track_this_test)
        top = QHBoxLayout()
        top.addWidget(QLabel("Test"))
        top.addWidget(self.test_combo)
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        top.addSpacing(10)
        top.addWidget(self.info_lbl, 1)
        top.addWidget(self.track_btn)

        # ---- video + timing + display options ------------------------------------
        self.player = VideoPlayer()
        self.player.overlay = self._overlay
        self.player.frame_changed.connect(self._frame_changed)
        self.player.view.clicked.connect(self._view_clicked)
        v = self.player.view
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.setDragMode(QGraphicsView.ScrollHandDrag)
        v.setToolTip("Wheel: zoom · drag: pan · Alt+double-click: fit")
        self.hud = QGraphicsTextItem()
        self.hud.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.hud.setZValue(100)
        self.hud.setPos(0, 0)
        v.scene().addItem(self.hud)

        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0, 1e7)
        self.start_spin.setDecimals(2)
        self.start_spin.setSuffix(" s")
        self.start_spin.setToolTip("Time in the video at which the test starts")
        self.start_spin.valueChanged.connect(self._timing_changed)
        self.dur_spin = QDoubleSpinBox()
        self.dur_spin.setRange(0, 1e7)
        self.dur_spin.setDecimals(2)
        self.dur_spin.setSuffix(" s")
        self.dur_spin.setSpecialValueText("default")
        self.dur_spin.setToolTip("Test duration. 0 = experiment default")
        self.dur_spin.valueChanged.connect(self._timing_changed)
        b_start = QPushButton("= now")
        b_start.setToolTip("Set the test start to the current video time")
        b_start.clicked.connect(self.set_start_now)
        b_end = QPushButton("End = now")
        b_end.setToolTip("Set the duration so the test ends at the current video time")
        b_end.clicked.connect(self.set_end_now)
        b_go = QPushButton()
        b_go.setIcon(st.standardIcon(QStyle.SP_MediaSkipBackward))
        b_go.setToolTip("Go to the test start")
        b_go.clicked.connect(lambda: self.test and self.player.seek_time(self.test.start_s))
        timing = QHBoxLayout()
        timing.addWidget(QLabel("Test start"))
        timing.addWidget(self.start_spin)
        timing.addWidget(b_start)
        timing.addSpacing(6)
        timing.addWidget(QLabel("Duration"))
        timing.addWidget(self.dur_spin)
        timing.addWidget(b_end)
        timing.addWidget(b_go)
        timing.addStretch()

        self.chk_zones = QCheckBox("Zones")
        self.chk_zones.setChecked(True)
        self.chk_animal = QCheckBox("Animal")
        self.chk_animal.setChecked(True)
        self.trail_spin = QDoubleSpinBox()
        self.trail_spin.setRange(0, 3600)
        self.trail_spin.setDecimals(0)
        self.trail_spin.setValue(5)
        self.trail_spin.setSuffix(" s")
        self.trail_spin.setToolTip("Length of the trail drawn behind the animal")
        self.chk_preview = QCheckBox("Detection preview")
        self.chk_preview.setToolTip("Run the detector on the displayed frame with this test's settings and tint "
                                    "the detected foreground")
        for w in (self.chk_zones, self.chk_animal, self.chk_preview):
            w.toggled.connect(self._refresh_frame)
        self.trail_spin.valueChanged.connect(self._refresh_frame)
        self.pos_lbl = QLabel()
        self.pos_lbl.setStyleSheet("color:#475569;")
        disp = QHBoxLayout()
        disp.addWidget(QLabel("Show:"))
        disp.addWidget(self.chk_zones)
        disp.addWidget(self.chk_animal)
        disp.addWidget(QLabel("trail"))
        disp.addWidget(self.trail_spin)
        disp.addSpacing(8)
        disp.addWidget(self.chk_preview)
        disp.addStretch()
        disp.addWidget(self.pos_lbl)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.player, 1)
        ll.addLayout(timing)
        ll.addLayout(disp)

        # ---- right: tabs -------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_results_tab(), "Results")
        self.tabs.addTab(self._build_plots_tab(), "Plots")
        self.tabs.addTab(self._build_scoring_tab(), "Scoring")
        self.tabs.addTab(self._build_detection_tab(), "Detection")
        self.tabs.addTab(self._build_edit_tab(), "Track editing")
        self.tabs.currentChanged.connect(lambda _: self._ensure_tab())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 460])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(split, 1)

        for w in (self.player, self.player.view, self.player.slider, self.events_table):
            w.installEventFilter(self)
        self._enable(False)

    # ================================================================== UI building
    def _build_results_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.results_lbl = QLabel()
        self.results_lbl.setWordWrap(True)
        self.results_table = QTableWidget(0, 2)
        self.results_table.setHorizontalHeaderLabels(["Measure", "Value"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results_table.verticalHeader().hide()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.verticalHeader().setDefaultSectionSize(22)
        copy = QPushButton("Copy table")
        copy.clicked.connect(self.copy_results)
        self.results_filter = QLineEdit()
        self.results_filter.setPlaceholderText("Filter measures…")
        self.results_filter.setClearButtonEnabled(True)
        self.results_filter.setMaximumWidth(180)
        self.results_filter.textChanged.connect(self._filter_results)
        row = QHBoxLayout()
        row.addWidget(self.results_lbl, 1)
        row.addWidget(self.results_filter)
        row.addWidget(copy)
        lay.addLayout(row)
        lay.addWidget(self.results_table, 1)
        return w

    def _build_plots_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.plot_animal = QComboBox()
        self.plot_animal.currentIndexChanged.connect(lambda _: self._mark_stale("plots"))
        self.plot_lbl = QLabel()
        row = QHBoxLayout()
        row.addWidget(QLabel("Animal"))
        row.addWidget(self.plot_animal)
        row.addWidget(self.plot_lbl, 1)
        self.track_canvas = PlotCanvas()
        self.heat_canvas = PlotCanvas()
        self.speed_canvas = PlotCanvas()
        figs = QHBoxLayout()
        figs.addWidget(self.track_canvas, 1)
        figs.addWidget(self.heat_canvas, 1)
        lay.addLayout(row)
        lay.addLayout(figs, 3)
        lay.addWidget(self.speed_canvas, 2)
        return w

    def _build_scoring_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel("Score with the keys (click on the video first) or the buttons. Toggle behaviours switch on/off, "
                      "hold behaviours last while the key or button is held, point behaviours are logged at the "
                      "current time. Space = play/pause, ←/→ = step (Shift: 1 s).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#475569;")
        lay.addWidget(hint)

        self.clock_box = QGroupBox("Observation clock (no video: score by direct observation)")
        cl = QHBoxLayout(self.clock_box)
        self.clock_lbl = QLabel("00:00.0")
        self.clock_lbl.setStyleSheet("font-size:22px;font-weight:bold;font-family:monospace;")
        st = self.style()
        self.clock_start_btn = QPushButton("Start")
        self.clock_start_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.clock_start_btn.clicked.connect(lambda: self.clock_start())
        self.clock_pause_btn = QPushButton("Pause")
        self.clock_pause_btn.setIcon(st.standardIcon(QStyle.SP_MediaPause))
        self.clock_pause_btn.clicked.connect(self.clock_pause)
        self.clock_stop_btn = QPushButton("Stop")
        self.clock_stop_btn.setIcon(st.standardIcon(QStyle.SP_MediaStop))
        self.clock_stop_btn.clicked.connect(self.clock_stop)
        cl.addWidget(self.clock_lbl)
        cl.addStretch()
        for b in (self.clock_start_btn, self.clock_pause_btn, self.clock_stop_btn):
            b.setFocusPolicy(Qt.NoFocus)
            cl.addWidget(b)
        self.clock_box.hide()
        lay.addWidget(self.clock_box)

        self.pad = ScoringPad()
        self.pad.pressed.connect(self._pad_pressed)
        self.pad.released.connect(self._pad_released)
        lay.addWidget(self.pad)
        self.active_lbl = QLabel()
        self.active_lbl.setWordWrap(True)
        lay.addWidget(self.active_lbl)
        self.events_table = QTableWidget(0, 4)
        self.events_table.setHorizontalHeaderLabels(["Behaviour", "Start (s)", "End (s)", "Duration (s)"])
        self.events_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.events_table.verticalHeader().hide()
        self.events_table.verticalHeader().setDefaultSectionSize(22)
        self.events_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events_table.cellClicked.connect(self._event_clicked)
        lay.addWidget(self.events_table, 1)
        row = QHBoxLayout()
        dele = QPushButton("Delete selected")
        dele.clicked.connect(self.delete_selected_events)
        clr = QPushButton("Clear all")
        clr.clicked.connect(self.clear_events)
        self.events_lbl = QLabel()
        row.addWidget(self.events_lbl, 1)
        row.addWidget(dele)
        row.addWidget(clr)
        lay.addLayout(row)
        lay.addWidget(QLabel("Notes"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Notes about this test (exported with the results)")
        self.notes_edit.setMaximumHeight(60)
        self.notes_edit.textChanged.connect(self._notes_changed)
        lay.addWidget(self.notes_edit)
        return w

    def _build_detection_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.det_lbl = QLabel()
        self.det_lbl.setWordWrap(True)
        lay.addWidget(self.det_lbl)
        self.det_form = SettingsForm(DETECTION_SPEC)
        self.det_form.changed.connect(self._detection_changed)
        sc = QScrollArea()
        sc.setWidget(self.det_form)
        sc.setWidgetResizable(True)
        lay.addWidget(sc, 1)
        self.preview_lbl = QLabel()
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet("color:#475569;")
        lay.addWidget(self.preview_lbl)
        row = QGridLayout()
        reset = QPushButton("Reset to experiment defaults")
        reset.clicked.connect(self.reset_detection)
        allb = QPushButton("Apply to all tests")
        allb.setToolTip("Give every test of the experiment these per-test settings")
        allb.clicked.connect(lambda: self.apply_detection_to_all())
        dflt = QPushButton("Save as experiment default")
        dflt.clicked.connect(self.save_detection_as_default)
        row.addWidget(reset, 0, 0)
        row.addWidget(dflt, 0, 1)
        row.addWidget(allb, 1, 0)
        lay.addLayout(row)
        return w

    def _build_edit_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Fix tracking errors: mark the animal by clicking on the video, or select a time range and "
                      "interpolate / delete the positions in it. Changes are saved to the track immediately.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#475569;")
        lay.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Animal"))
        self.edit_animal = QComboBox()
        row.addWidget(self.edit_animal, 1)
        lay.addLayout(row)

        mk = QGroupBox("Mark position")
        ml = QVBoxLayout(mk)
        self.mark_btn = QPushButton("Mark animal position (click on the video)")
        self.mark_btn.setCheckable(True)
        self.mark_btn.toggled.connect(self._mark_toggled)
        adv = QHBoxLayout()
        adv.addWidget(QLabel("Then advance"))
        self.advance_spin = QSpinBox()
        self.advance_spin.setRange(0, 1000)
        self.advance_spin.setValue(1)
        self.advance_spin.setSuffix(" frame(s)")
        adv.addWidget(self.advance_spin)
        adv.addStretch()
        ml.addWidget(self.mark_btn)
        ml.addLayout(adv)
        lay.addWidget(mk)

        rg = QGroupBox("Time range")
        rl = QVBoxLayout(rg)
        r1 = QHBoxLayout()
        b1 = QPushButton("Range start = now")
        b1.clicked.connect(lambda: self.set_range(0))
        b2 = QPushButton("Range end = now")
        b2.clicked.connect(lambda: self.set_range(1))
        r1.addWidget(b1)
        r1.addWidget(b2)
        self.range_lbl = QLabel()
        r2 = QHBoxLayout()
        bi = QPushButton("Interpolate range")
        bi.clicked.connect(lambda: self.interpolate_range())
        bd = QPushButton("Delete positions in range")
        bd.clicked.connect(lambda: self.delete_range())
        r2.addWidget(bi)
        r2.addWidget(bd)
        rl.addLayout(r1)
        rl.addWidget(self.range_lbl)
        rl.addLayout(r2)
        lay.addWidget(rg)
        mv = QGroupBox("Moveable zones in this test (e.g. the platform position)")
        mvl = QHBoxLayout(mv)
        self.move_zone = QComboBox()
        self.move_btn = QPushButton("Place (click on video)")
        self.move_btn.setCheckable(True)
        self.move_btn.toggled.connect(self._mark_toggled)
        self.move_reset = QPushButton("Reset")
        self.move_reset.setToolTip("Use the apparatus position for this test")
        self.move_reset.clicked.connect(self.reset_moveable_zone)
        mvl.addWidget(self.move_zone, 1)
        mvl.addWidget(self.move_btn)
        mvl.addWidget(self.move_reset)
        self.move_box = mv
        lay.addWidget(mv)
        r3 = QHBoxLayout()
        self.undo_btn = QPushButton("Undo last edit")
        self.undo_btn.clicked.connect(self.undo_edit)
        self.edit_lbl = QLabel()
        r3.addWidget(self.undo_btn)
        r3.addWidget(self.edit_lbl, 1)
        lay.addLayout(r3)
        lay.addStretch()
        return w

    # ================================================================== page API
    def set_project(self, project):
        self._close_open_states()
        self._reset_clock()
        self._confirmed.clear()
        self.player.close_video()
        self.test = None
        self.tracks = []
        self._bg_cache.clear()
        self._undo.clear()
        self._fill_combo()
        self._clear_views()
        self._enable(False)

    def on_show(self):
        p = self.project
        if p is None:
            return
        self._fill_combo()
        if self.test is None or self.test not in p.tests:
            if p.tests:
                self.load_test(p.tests[0].id)
            else:
                self.test = None
                self.player.close_video()
                self._clear_views()
                self._enable(False)
            return
        self.reload_current()

    def reload_current(self):
        """Re-read the current test from the project (it may have been edited or tracked elsewhere)."""
        t = self.test
        self._load_tracks()
        self._loading = True
        self.start_spin.setValue(t.start_s)
        self.dur_spin.setValue(t.duration_s)
        self._loading = False
        self._load_detection_form()
        self._fill_behaviours()
        self._fill_events()
        self._load_notes()
        self._update_info()
        self._update_clock_ui()
        self._enable(True)
        self._mark_stale("results", "plots")
        self._refresh_frame()

    def on_hide(self):
        self.player.pause()
        if self.clock.state == "running":
            self.clock_pause()
        self._close_open_states()

    def commit(self):
        self._close_open_states()

    def shutdown(self):
        self.player.close_video()
        if self._bg_worker is not None:
            self._bg_worker.wait(5000)

    # ================================================================== test selection
    def _fill_combo(self):
        self.test_combo.blockSignals(True)
        self.test_combo.clear()
        p = self.project
        if p is not None:
            for t in p.tests:
                txt = f"{t.id}: {t.animal_id or '—'}"
                if t.stage:
                    txt += f" · {t.stage}"
                txt += f" · trial {t.trial}  [{t.status}]"
                self.test_combo.addItem(txt, t.id)
            if self.test is not None:
                self.test_combo.setCurrentIndex(max(0, self.test_combo.findData(self.test.id)))
        self.test_combo.blockSignals(False)

    def _combo_changed(self, i):
        tid = self.test_combo.itemData(i)
        if tid is not None and (self.test is None or tid != self.test.id):
            self.load_test(tid)

    def step_test(self, d: int):
        n = self.test_combo.count()
        if n:
            i = min(n - 1, max(0, self.test_combo.currentIndex() + d))
            self.test_combo.setCurrentIndex(i)

    def load_test(self, test_id: int):
        p = self.project
        if p is None:
            return
        t = p.get_test(test_id)
        if t is None:
            return
        if self.clock.state != "stopped":
            if t is self.test:  # an observation of this test is in progress: keep it going
                self.reload_current()
                return
            self.clock_stop()
        self._close_open_states()
        self._reset_clock()
        self.player.pause()
        self.test = t
        self._undo.clear()
        self._range = [None, None]
        self.mark_btn.setChecked(False)
        self._fill_combo()
        self._load_tracks()
        self._loading = True
        self.start_spin.setValue(t.start_s)
        self.dur_spin.setValue(t.duration_s)
        self._loading = False
        self._load_detection_form()
        self._fill_behaviours()
        self._fill_events()
        self._load_notes()
        self._update_range_lbl()
        self._enable(True)
        path = p.abs_path(t.video) if t.video else ""
        if path and Path(path).exists() and self.player.open(path):
            self.player.seek_time(t.start_s)
        else:
            self.player.close_video()
            self.player.current_frame = None
            self.pos_lbl.setText("")
            self._placeholder("No video for this test" if not path else f"Video not found:\n{path}")
        self._update_info()
        self._update_clock_ui()
        self._mark_stale("results", "plots")
        self.player.view.setFocus()

    def _fill_moveable(self):
        base = self.project.get_apparatus(self.test.apparatus) if self.test is not None else None
        names = [z.name for z in base.zones if z.moveable] if base is not None else []
        self.move_zone.clear()
        for n in names:
            moved = self.test is not None and n in self.test.zone_overrides
            self.move_zone.addItem(f"{n}{'  (moved)' if moved else ''}", n)
        self.move_box.setVisible(bool(names))

    def place_moveable_zone(self, x: float, y: float):
        """Centre the selected moveable zone on (x, y) for this test only."""
        name = self.move_zone.currentData()
        base = self.project.get_apparatus(self.test.apparatus) if self.test is not None else None
        z = base.zone(name) if base is not None and name else None
        if z is None:
            return
        cx, cy = z.shape.centroid()
        self.test.zone_overrides[name] = z.shape.translated(x - cx, y - cy).to_dict()
        self._moveable_changed(f"{name} placed at ({x:.0f}, {y:.0f}) px for this test.")

    def reset_moveable_zone(self):
        name = self.move_zone.currentData()
        if self.test is not None and name and self.test.zone_overrides.pop(name, None) is not None:
            self._moveable_changed(f"{name} reset to the apparatus position.")

    def _moveable_changed(self, msg):
        i = self.move_zone.currentIndex()
        self._fill_moveable()
        self.move_zone.setCurrentIndex(i)
        self.main.mark_dirty()
        self._mark_stale("results", "plots")
        self._refresh_frame()
        self.edit_lbl.setText(msg)

    def _load_tracks(self):
        self._fill_moveable()
        self.tracks = self.project.load_tracks(self.test) if self.test is not None else []
        ids = [self.test.animal_id] + list(self.test.extra_animals) if self.test else []
        for cb in (self.plot_animal, self.edit_animal):
            cb.blockSignals(True)
            cur = cb.currentIndex()
            cb.clear()
            for i in range(max(len(self.tracks), self.test.n_animals if self.test else 0)):
                cb.addItem(ids[i] if i < len(ids) and ids[i] else f"Animal {i + 1}", i)
            cb.setCurrentIndex(cur if 0 <= cur < cb.count() else 0)
            cb.blockSignals(False)

    def _enable(self, on: bool):
        for w in (self.tabs, self.start_spin, self.dur_spin, self.track_btn):
            w.setEnabled(on)
        has = on and self.test is not None and bool(self.test.video)
        self.track_btn.setEnabled(has)
        self.prev_btn.setEnabled(self.test_combo.count() > 1)
        self.next_btn.setEnabled(self.test_combo.count() > 1)

    def _clear_views(self):
        self.info_lbl.setText("")
        self.pos_lbl.setText("")
        self.results_table.setRowCount(0)
        self.results_lbl.setText("")
        self.events_table.setRowCount(0)
        self.pad.set_behaviours([])
        self._load_notes()
        self.clock_box.hide()
        self._placeholder("No test selected")

    def _placeholder(self, text: str):
        self.player.view.set_frame(np.full((360, 480, 3), 40, np.uint8))
        self.player.time_lbl.setText("--:--")
        self._set_hud([(line, "#e2e8f0") for line in text.splitlines()])

    def _set_hud(self, lines: list[tuple[str, str]]):
        """Crisp text drawn over the top-left corner of the video (not scaled with it)."""
        if not lines:
            self.hud.setVisible(False)
            return
        body = "<br>".join(f"<span style='color:{c}'>{html.escape(t)}</span>" for t, c in lines)
        self.hud.setHtml(f"<div style='background-color:rgba(15,23,42,170);'>&nbsp;{body}&nbsp;</div>")
        self.hud.setVisible(True)

    def _update_info(self):
        t, p = self.test, self.project
        if t is None or p is None:
            self.info_lbl.setText("")
            return
        a = p.get_animal(t.animal_id)
        grp = (f" <span style='color:{wf.display_color(p, a.group)}'>({html.escape(wf.display_group(p, a.group))})"
               "</span>" if a and a.group else "")
        if a is not None and a.retired:
            grp += " <span style='color:#dc2626'>retired</span>"
        dur = t.duration_s or p.test_duration_s
        status_col = STATUS_COLORS.get(t.status, "#334155")
        parts = [f"<b>{t.animal_id or '—'}</b>{grp}", t.stage or "",
                 f"trial {t.trial}" + (f" (attempt {t.attempt})" if t.attempt > 1 else ""), t.apparatus,
                 Path(t.video).name if t.video else "no video", f"{t.start_s:g}–{t.start_s + dur:g} s" if dur else "",
                 f"<b style='color:{status_col}'>{t.status}</b>"]
        self.info_lbl.setText(" · ".join(x for x in parts if x))

    # ================================================================== overlay
    def _app(self):
        if self.test is None:
            return None
        app = self.project.get_apparatus(self.test.apparatus)
        return app.with_overrides(self.test.zone_overrides) if app is not None else None

    def video_start(self, tr: Track | None = None) -> float:
        """Video time (s) at which track time 0 occurs."""
        for x in ([tr] if tr is not None else self.tracks):
            v = x.meta.get("video_start_s")
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return self.test.start_s if self.test is not None else 0.0

    def test_time(self) -> float:
        """Current time relative to the test start (the time base of the track and of scored events).

        Without a video this is the observation clock."""
        if self.player.source is None:
            return self.clock.elapsed()
        return self.player.time - self.video_start()

    @staticmethod
    def sample_at(tr: Track, t: float) -> int | None:
        if len(tr) == 0:
            return None
        i = int(np.searchsorted(tr.t, t))
        cands = [j for j in (i - 1, i) if 0 <= j < len(tr)]
        j = min(cands, key=lambda j: abs(tr.t[j] - t))
        if abs(tr.t[j] - t) > max(1.5 * tr.dt, 0.02):
            return None
        return j

    def _overlay(self, index: int, frame: np.ndarray) -> np.ndarray:
        if self.test is None or self.project is None:
            return frame
        app = self._app()
        h, w = frame.shape[:2]
        sc = max(1.0, w / 640)
        vt = index / self.player.fps
        hud: list[tuple[str, str]] = []
        if self.chk_preview.isChecked():
            img = self._preview(frame, app, self.chk_zones.isChecked(), hud)
        elif self.chk_zones.isChecked() and app is not None:
            img = draw_overlay(frame, [], app)
        else:
            img = frame.copy()
        if self.chk_zones.isChecked() and app is not None and app.arena is not None:
            cv2.polylines(img, [np.round(app.arena.polygon()).astype(np.int32)], True, (235, 235, 235),
                          max(1, int(sc)), cv2.LINE_AA)
        if self.chk_animal.isChecked() and not self.chk_preview.isChecked():
            self._draw_tracks(img, vt, sc)
        # test window + scoring state
        start = self.video_start()
        dur = self.test.duration_s or self.project.test_duration_s
        if vt < start - 1e-6:
            hud.insert(0, (f"before test start ({fmt_time(start)})", "#fbbf24"))
        elif dur and vt > start + dur + 1e-6:
            hud.insert(0, ("after test end", "#fbbf24"))
        else:
            hud.insert(0, (f"test time {fmt_time(vt - start)}", "#ffffff"))
        for name in self._open_states:
            hud.append((f"● {name}", "#4ade80"))
        if self._range[0] is not None or self._range[1] is not None:
            tt = vt - start
            a, b = self._range
            if a is not None and b is not None and min(a, b) <= tt <= max(a, b):
                cv2.rectangle(img, (0, 0), (w - 1, h - 1), (0, 200, 255), max(2, int(2 * sc)))
        if self.mark_btn.isChecked():
            hud.append(("click on the animal to mark its position", "#22d3ee"))
        self._set_hud(hud)
        return img

    def _draw_tracks(self, img, vt, sc):
        trail = self.trail_spin.value()
        r = max(3, int(4 * sc))
        lw = max(1, int(sc))
        ids = [self.test.animal_id] + list(self.test.extra_animals)
        for ai, tr in enumerate(self.tracks):
            if len(tr) == 0:
                continue
            col = ANIMAL_BGR[ai % len(ANIMAL_BGR)]
            tt = vt - self.video_start(tr)
            if trail > 0:
                m = (tr.t <= tt) & (tr.t >= tt - trail)
                xs, ys = tr.x[m], tr.y[m]
                ok = np.isfinite(xs) & np.isfinite(ys)
                # split the trail at missing samples
                for run in np.split(np.arange(len(xs)), np.flatnonzero(~ok)):
                    run = run[ok[run]] if len(run) else run
                    if len(run) > 1:
                        pts = np.column_stack([xs[run], ys[run]]).round().astype(np.int32)
                        cv2.polylines(img, [pts], False, (255, 170, 40), lw, cv2.LINE_AA)
            j = self.sample_at(tr, tt)
            if j is None or not (math.isfinite(tr.x[j]) and math.isfinite(tr.y[j])):
                continue
            c = (int(round(tr.x[j])), int(round(tr.y[j])))
            if math.isfinite(tr.hx[j]) and math.isfinite(tr.tx[j]):
                hp = (int(round(tr.hx[j])), int(round(tr.hy[j])))
                tp = (int(round(tr.tx[j])), int(round(tr.ty[j])))
                cv2.line(img, tp, hp, col, lw, cv2.LINE_AA)
                cv2.circle(img, hp, r, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.circle(img, tp, max(2, r - 1), (255, 80, 0), -1, cv2.LINE_AA)
            cv2.circle(img, c, r, col, -1 if tr.detected[j] else lw, cv2.LINE_AA)
            if len(self.tracks) > 1:
                label = ids[ai] if ai < len(ids) and ids[ai] else f"#{ai + 1}"
                _put_text(img, label, (c[0] + r + 2, c[1] - r - 2), col, sc * 0.9)

    # ---- detection preview --------------------------------------------------------
    def _preview_settings(self) -> DetectionSettings:
        return self.project.detection_for(self.test)

    def _bg_settings(self, s: DetectionSettings) -> DetectionSettings:
        b = DetectionSettings.from_dict(s.to_dict())
        if b.background == "adaptive":
            b.background = "median"
        return b

    def bg_key(self, s: DetectionSettings) -> tuple:
        b = self._bg_settings(s)
        return (self.project.abs_path(self.test.video), b.background, b.background_frame, b.background_samples,
                round(b.start_time_s, 3), round(b.duration_s, 3))

    def _preview(self, frame, app, draw_zones=True, hud=None):
        hud = hud if hud is not None else []
        s = self._preview_settings()
        h, w = frame.shape[:2]
        mask = None
        if app is not None:
            try:
                mask = app.arena_or_bounds().mask((h, w))
            except ValueError:
                mask = None
        tracker = ArenaTracker(s, mask)
        if s.method == "background":
            key = self.bg_key(s)
            bg = self._bg_cache.get(key)
            if bg is None or bg.shape != (h, w):
                self.request_background()
                img = draw_overlay(frame, [], app if draw_zones else None)
                msg = self._bg_error or "computing background model…"
                hud.append((msg, "#fbbf24"))
                self._set_preview_info(f"Background: {msg}")
                return img
            tracker.set_background(bg)
        dets, fg = tracker.process(frame)
        img = draw_overlay(frame, dets, app if draw_zones else None, fg=fg)
        hud.append(("detection preview", "#f87171"))
        if tracker.pose_error:
            hud.append((f"pose model unavailable: {tracker.pose_error}", "#fbbf24"))
        found = [d for d in dets if d.detected]
        if found:
            info = "; ".join(f"animal {i + 1}: ({d.x:.0f}, {d.y:.0f}) px, area {d.area:.0f} px²"
                             for i, d in enumerate(dets) if d.detected)
            info = f"Detected {len(found)}/{len(dets)} — {info}"
        else:
            info = "<span style='color:#dc2626'>No animal detected in this frame.</span> Try a lower threshold, " \
                   "a smaller minimum area or a different contrast."
        fg_px = int((fg > 0).sum())
        self._set_preview_info(f"{info}<br>Foreground pixels: {fg_px}")
        return img

    def _set_preview_info(self, text):
        self.preview_info = text
        self.preview_lbl.setText(text)

    def request_background(self):
        if self.test is None or not self.test.video:
            return
        s = self._bg_settings(self._preview_settings())
        key = self.bg_key(s)
        if key in self._bg_cache or self._bg_key == key:
            return
        self._bg_error = ""
        if self._bg_worker is not None:  # one at a time; re-requested when the current one finishes
            return
        video = key[0]
        self._bg_key = key
        w = Worker(lambda progress, stop: (key, compute_background(video, s)), self)
        w.signals.done.connect(self._bg_done)
        w.signals.failed.connect(self._bg_failed)
        w.finished.connect(w.deleteLater)
        self._bg_worker = w
        w.start()

    def _bg_done(self, res):
        key, bg = res
        self._bg_cache[key] = bg
        self._bg_worker = None
        self._bg_key = None
        if self.chk_preview.isChecked():
            self._refresh_frame()

    def _bg_failed(self, msg):
        self._bg_worker = None
        self._bg_key = None
        self._bg_error = f"background failed: {msg}"
        self.main.status(self._bg_error)

    # ================================================================== frame / timing
    def _refresh_frame(self, *_):
        if self.player.current_frame is not None:
            self.player.refresh()

    def _frame_changed(self, index, t):
        if self.test is None:
            return
        tt = t - self.video_start()
        txt = f"test time {tt:.2f} s"
        if self.tracks:
            tr = self.tracks[0]
            j = self.sample_at(tr, t - self.video_start(tr))
            if j is not None and math.isfinite(tr.x[j]):
                txt += f" · centre ({tr.x[j]:.0f}, {tr.y[j]:.0f}) px"
                if not tr.detected[j]:
                    txt += " (interpolated)"
            elif j is not None:
                txt += " · not detected"
        self.pos_lbl.setText(txt)
        if self._open_states:
            self._update_active()

    def _timing_changed(self, *_):
        if self._loading or self.test is None:
            return
        self.test.start_s = self.start_spin.value()
        self.test.duration_s = self.dur_spin.value()
        self.main.mark_dirty()
        self._update_info()
        self._refresh_frame()

    def set_start_now(self):
        if self.test is not None and self.player.source is not None:
            self.start_spin.setValue(round(self.player.time, 2))

    def set_end_now(self):
        if self.test is not None and self.player.source is not None:
            d = self.player.time - self.test.start_s
            if d > 0:
                self.dur_spin.setValue(round(d, 2))

    # ================================================================== tracking
    def track_this_test(self):
        p, t = self.project, self.test
        if p is None or t is None or not t.video:
            return None
        if not Path(p.abs_path(t.video)).exists():
            error_box(self, "Track", f"Video not found: {p.abs_path(t.video)}")
            return None
        if p.path is None and not self.main.save():
            return None
        self.player.pause()
        self._tracking = True
        return run_with_progress(self, f"Tracking test {t.id}", track_tests_job(p, [t]),
                                 on_done=self._tracking_done, on_fail=self._tracking_failed)

    def _tracking_done(self, res):
        self._tracking = False
        self.main.save()
        if res.get("errors"):
            error_box(self, "Tracking", "\n".join(res["errors"]))
        if self.test is None:
            return
        self._load_tracks()
        self._undo.clear()
        self._fill_combo()
        self._update_info()
        self._mark_stale("results", "plots")
        if self.chk_preview.isChecked():
            self.chk_preview.setChecked(False)
        self._refresh_frame()
        if res.get("cancelled"):
            self.main.status("Tracking cancelled — the previous track (if any) was kept.")
        else:
            self.tabs.setCurrentIndex(0)
            self.main.status(f"Tracked test {self.test.id}.")

    def _tracking_failed(self, msg):
        self._tracking = False
        error_box(self, "Tracking", msg)

    # ================================================================== results & plots
    def _mark_stale(self, *what):
        for w in what:
            self._stale[w] = True
        self._ensure_tab()

    def _ensure_tab(self):
        i = self.tabs.currentIndex()
        if i == 0 and self._stale["results"]:
            self.refresh_results()
        elif i == 1 and self._stale["plots"]:
            self.refresh_plots()

    def refresh_results(self):
        self._stale["results"] = False
        tbl = self.results_table
        tbl.setRowCount(0)
        p, t = self.project, self.test
        if p is None or t is None:
            return
        rows = []
        if self.tracks or (t.events and p.behaviours):
            try:
                rows = p.analyse_test(t)
            except Exception as e:
                self.results_lbl.setText(f"<span style='color:#dc2626'>Analysis failed: {e}</span>")
                return
        if not rows:
            self.results_lbl.setText("Not tracked yet — click <b>Track this test</b>, import a track on the Tests "
                                     "page, or score behaviours manually.")
            tbl.setColumnCount(2)
            tbl.setHorizontalHeaderLabels(["Measure", "Value"])
            return
        skip = INFO_KEYS | set(p.animal_fields)
        measures = []
        for r in rows:
            for k in r:
                if k not in skip and k not in measures:
                    measures.append(k)
        heads = ["Measure"] + ([str(r.get("Animal", "")) or f"#{i + 1}" for i, r in enumerate(rows)]
                               if len(rows) > 1 else ["Value"])
        tbl.setColumnCount(len(heads))
        tbl.setHorizontalHeaderLabels(heads)
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(heads)):
            tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        tbl.setRowCount(len(measures))
        for i, m in enumerate(measures):
            tbl.setItem(i, 0, _ro_item(m))
            for j, r in enumerate(rows):
                tbl.setItem(i, j + 1, _num_item(r.get(m)))
        self._filter_results()
        src = "manual scoring" if not self.tracks else f"{len(self.tracks)} track{'s' if len(self.tracks) > 1 else ''}"
        self.results_lbl.setText(f"<b>{len(measures)}</b> measures from {src} · unit: "
                                 f"{self._app().unit if self._app() else 'px'}")

    def _filter_results(self, *_):
        words = self.results_filter.text().lower().split()
        tbl = self.results_table
        for r in range(tbl.rowCount()):
            name = tbl.item(r, 0).text().lower() if tbl.item(r, 0) else ""
            tbl.setRowHidden(r, not all(w in name for w in words))

    def results_dict(self) -> dict[str, str]:
        tbl = self.results_table
        return {tbl.item(r, 0).text(): tbl.item(r, 1).text() for r in range(tbl.rowCount())
                if tbl.item(r, 0) and tbl.item(r, 1)}

    def copy_results(self):
        tbl = self.results_table
        lines = ["\t".join(tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount()))]
        for r in range(tbl.rowCount()):
            lines.append("\t".join(tbl.item(r, c).text() if tbl.item(r, c) else "" for c in range(tbl.columnCount())))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.main.status("Results copied to the clipboard.")

    def refresh_plots(self):
        self._stale["plots"] = False
        p, t = self.project, self.test
        if p is None or t is None or not self.tracks:
            self.plot_lbl.setText("No track yet.")
            for c in (self.track_canvas, self.heat_canvas, self.speed_canvas):
                if c.canvas is not None:
                    c.canvas.setParent(None)
                    c.canvas = None
            return
        i = self.plot_animal.currentData() or 0
        tr = self.tracks[min(i, len(self.tracks) - 1)]
        app = self._app()
        try:
            from ...core.measures import kinematics

            k = kinematics(tr, app, p.analysis_for(t))
            for canvas, fig in ((self.track_canvas, track_plot(tr, app, title="Track (colour = time)", size=(3, 3))),
                                (self.heat_canvas, heatmap([tr], app, title="Occupancy", size=(3.2, 3))),
                                (self.speed_canvas, speed_trace(tr, app, freezing=k.freezing, size=(5, 2.2)))):
                fig.set_layout_engine("tight")  # re-layout when the canvas is resized
                canvas.set_figure(fig)
            self.plot_lbl.setText(f"{len(tr)} samples · {tr.duration:.1f} s · detected "
                                  f"{100 * tr.detected.mean():.1f} %")
        except Exception as e:
            self.plot_lbl.setText(f"Plot failed: {e}")

    # ================================================================== manual scoring
    def _fill_behaviours(self):
        p = self.project
        self.pad.set_behaviours(p.behaviours if p is not None else [])
        self._update_active()

    def behaviour_for_key(self, text: str):
        text = text.lower()
        if not text or self.project is None:
            return None
        return next((b for b in self.project.behaviours if b.key and b.key.lower() == text), None)

    def behaviour_named(self, name: str):
        return next((b for b in self.project.behaviours if b.name == name), None) if self.project else None

    def scoring_ready(self) -> bool:
        """Whether key / button scoring may happen now (asks for the animal ID first when the experiment requires
        it; without a video the observation clock must be running)."""
        t, p = self.test, self.project
        if t is None or p is None:
            return False
        if self.player.source is None and self.clock.state != "running":
            self.main.status("No video: start the observation clock (Scoring tab) to score by direct observation.")
            return False
        return self.confirm_animal()

    def confirm_animal(self) -> bool:
        t = self.test
        if t is None or t.id in self._confirmed or not wf.confirm_id_enabled(self.project):
            return True
        self.player.pause()
        if not confirm_animal_id(self, t, self.project):
            self.main.status("Animal not confirmed — scoring blocked.")
            return False
        self._confirmed.add(t.id)
        return True

    def score(self, b, t: float | None = None):
        """Toggle a state / hold behaviour or log a point behaviour at test time t (default: now)."""
        if self.test is None:
            return
        t = round(self.test_time() if t is None else t, 3)
        if b.kind == "point":
            self.test.events.append({"behaviour": b.name, "t": t, "t_end": None})
            self.main.status(f"{b.name} at {t:.2f} s")
            self._scoring_changed()
        elif b.name in self._open_states:
            self.stop_behaviour(b, t)
        else:
            self.start_behaviour(b, t)

    def start_behaviour(self, b, t: float | None = None):
        """Start a state / hold behaviour (stopping the others of its exclusive set)."""
        if self.test is None or b.name in self._open_states:
            return
        t = round(self.test_time() if t is None else t, 3)
        for o in wf.exclusive_partners(self.project.behaviours, b):
            if o.name in self._open_states:
                self._end_state(o.name, t)
        self._open_states[b.name] = t
        self.main.status(f"{b.name} on at {t:.2f} s")
        self._scoring_changed()

    def stop_behaviour(self, b, t: float | None = None):
        if self.test is None or b.name not in self._open_states:
            return
        t = round(self.test_time() if t is None else t, 3)
        self._end_state(b.name, t)
        self.main.status(f"{b.name} off at {t:.2f} s")
        self._scoring_changed()

    def _end_state(self, name, t):
        t0 = self._open_states.pop(name)
        a, z = sorted((t0, t))
        if z > a:
            self.test.events.append({"behaviour": name, "t": a, "t_end": z})

    def _scoring_changed(self):
        self.test.events.sort(key=lambda e: e["t"])
        self.main.mark_dirty()
        self._events_changed()

    def press_behaviour(self, b) -> bool:
        """Key / button pressed: start (hold), toggle (state) or log (point)."""
        if not self.scoring_ready():
            return False
        if b.kind == "hold":
            self.start_behaviour(b)
        else:
            self.score(b)
        return True

    def release_behaviour(self, b) -> bool:
        if b.kind != "hold" or b.name not in self._open_states:
            return False
        self.stop_behaviour(b)
        return True

    def _pad_pressed(self, name):
        b = self.behaviour_named(name)
        if b is not None:
            self.press_behaviour(b)

    def _pad_released(self, name):
        b = self.behaviour_named(name)
        if b is not None:
            self.release_behaviour(b)

    def _close_open_states(self):
        if not self._open_states or self.test is None:
            self._open_states.clear()
            return
        t = round(self.test_time(), 3)
        for name, t0 in list(self._open_states.items()):
            a, z = sorted((t0, t))
            if z > a:
                self.test.events.append({"behaviour": name, "t": a, "t_end": z})
        self._open_states.clear()
        self.test.events.sort(key=lambda e: e["t"])
        self.main.mark_dirty()
        self._events_changed()

    def _events_changed(self):
        if self.test is not None and self.project is not None:
            old = self.test.status
            if wf.refresh_status(self.project, self.test) != old:
                self._fill_combo()
                self._update_info()
        self._fill_events()
        self._update_active()
        self._mark_stale("results")
        self._refresh_frame()
        self._update_observation_hud()

    def _update_active(self):
        now = self.test_time() if self.test is not None and (self.player.source is not None
                                                             or self.clock.state != "stopped") else None
        if self._open_states:
            parts = [f"<b style='color:#16a34a'>{html.escape(n)}</b> since {t0:.2f} s" +
                     (f" ({now - t0:.1f} s)" if now is not None else "") for n, t0 in self._open_states.items()]
            self.active_lbl.setText("Active: " + ", ".join(parts))
        else:
            self.active_lbl.setText("<span style='color:#94a3b8'>No state behaviour active.</span>")
        self.pad.set_active(self._open_states)

    # ---- observation clock (TakeNote mode) ------------------------------------
    def _update_clock_ui(self):
        no_video = self.test is not None and self.player.source is None
        self.clock_box.setVisible(no_video)
        st = self.clock.state
        self.clock_start_btn.setText("Resume" if st == "paused" else "Start")
        self.clock_start_btn.setEnabled(no_video and st != "running")
        self.clock_pause_btn.setEnabled(st == "running")
        self.clock_stop_btn.setEnabled(st != "stopped")
        e = self.clock.elapsed()
        self.clock_lbl.setText(f"{int(e // 60):02d}:{e % 60:04.1f}")
        self.clock_lbl.setStyleSheet("font-size:22px;font-weight:bold;font-family:monospace;color:"
                                     + {"running": "#16a34a", "paused": "#d97706"}.get(st, "#334155") + ";")
        self._update_observation_hud()

    def _update_observation_hud(self):
        if self.test is None or self.player.source is not None:
            return
        e = self.clock.elapsed()
        lines = [("No video — scoring by direct observation", "#e2e8f0"),
                 (f"observation clock {fmt_time(e)}  ({self.clock.state})",
                  {"running": "#4ade80", "paused": "#fbbf24"}.get(self.clock.state, "#e2e8f0"))]
        if self.clock.state == "stopped" and not self.test.events:
            lines.append(("Start the clock on the Scoring tab", "#94a3b8"))
        lines += [(f"● {n}", "#4ade80") for n in self._open_states]
        self._set_hud(lines)

    def _clock_tick(self):
        if self.clock.state != "running" or self.test is None:
            self._clock_timer.stop()
            return
        dur = self.test.duration_s or self.project.test_duration_s
        if dur and self.clock.elapsed() >= dur:
            self.clock_stop(at=dur)
            self.main.status(f"Observation finished ({dur:g} s).")
            return
        self._update_clock_ui()
        if self._open_states:
            self._update_active()

    def clock_start(self, confirm: bool = True) -> bool:
        t = self.test
        if t is None or self.player.source is not None or self.clock.state == "running":
            return False
        if self.clock.state == "stopped":
            if t.events and confirm and QMessageBox.question(
                    self, "Observation", f"Test {t.id} already has {len(t.events)} scored events. Delete them and "
                    "score the test again?") != QMessageBox.Yes:
                return False
            if not self.confirm_animal():
                return False
            if t.events:
                t.events = []
                self.main.mark_dirty()
                self._events_changed()
        self.clock.start()
        self._clock_timer.start()
        self._update_clock_ui()
        self.main.status("Observation clock running — score with the keys or the buttons.")
        return True

    def clock_pause(self):
        if self.clock.state != "running":
            return
        self.clock.pause()
        self._clock_timer.stop()
        self._update_clock_ui()

    def clock_stop(self, at: float | None = None):
        """Stop the observation: close running behaviours, store the duration and mark the test scored."""
        t, p = self.test, self.project
        if self.clock.state == "stopped" or t is None:
            return
        if at is not None:
            self.clock._acc, self.clock.state = float(at), "paused"
        e = round(self.clock.stop(), 3)
        self._clock_timer.stop()
        for name in list(self._open_states):
            self._end_state(name, e)
        dur = t.duration_s or p.test_duration_s
        if not dur or e < dur - 1e-6:
            t.duration_s = round(e, 2)
            self._loading = True
            self.dur_spin.setValue(t.duration_s)
            self._loading = False
        if not t.recorded_at:
            t.recorded_at = _dt.datetime.now().isoformat(timespec="seconds")
        self._scoring_changed()
        self._update_info()
        self._update_clock_ui()
        self.main.status(f"Observation of test {t.id} stopped at {e:.1f} s — {len(t.events)} events.")

    def _reset_clock(self):
        self._clock_timer.stop()
        self.clock.reset()

    # ---- notes ----------------------------------------------------------------------
    def _load_notes(self):
        self._loading = True
        self.notes_edit.setPlainText(self.test.notes if self.test is not None else "")
        self.notes_edit.setEnabled(self.test is not None)
        self._loading = False

    def _notes_changed(self):
        if self._loading or self.test is None:
            return
        self.test.notes = self.notes_edit.toPlainText()
        self.main.mark_dirty()

    def _fill_events(self):
        tbl = self.events_table
        tbl.setRowCount(0)
        if self.test is None:
            return
        rows = [(e["t"], e, False) for e in self.test.events]
        rows += [(t0, {"behaviour": n, "t": t0, "t_end": None}, True) for n, t0 in self._open_states.items()]
        rows.sort(key=lambda r: r[0])
        self._event_rows = [(e, running) for _, e, running in rows]
        for t0, e, running in rows:
            r = tbl.rowCount()
            tbl.insertRow(r)
            tbl.setItem(r, 0, _ro_item(e["behaviour"]))
            tbl.setItem(r, 1, _num_item(f"{e['t']:.2f}", e["t"]))
            end = e.get("t_end")
            tbl.setItem(r, 2, _num_item("running…" if running else ("—" if end is None else f"{end:.2f}")))
            tbl.setItem(r, 3, _num_item("" if end is None else f"{end - e['t']:.2f}"))
        n = len(self.test.events)
        self.events_lbl.setText(f"{n} event{'s' if n != 1 else ''}")

    def _event_clicked(self, row, _col):
        it = self.events_table.item(row, 1)
        if it is not None and self.player.source is not None:
            self.player.seek_time(self.video_start() + float(it.data(Qt.UserRole)))

    def delete_selected_events(self):
        if self.test is None:
            return
        rows = {i.row() for i in self.events_table.selectionModel().selectedRows()}
        if not rows:
            return
        sel = [self._event_rows[r] for r in rows if r < len(self._event_rows)]
        ids = {id(e) for e, running in sel if not running}
        for e, running in sel:
            if running:
                self._open_states.pop(e["behaviour"], None)
        self.test.events = [e for e in self.test.events if id(e) not in ids]
        self.main.mark_dirty()
        self._events_changed()

    def clear_events(self):
        if self.test is None or not (self.test.events or self._open_states):
            return
        if QMessageBox.question(self, "Clear events", f"Delete all {len(self.test.events)} scored events of "
                                f"test {self.test.id}?") != QMessageBox.Yes:
            return
        self.test.events = []
        self._open_states.clear()
        self.main.mark_dirty()
        self._events_changed()

    def _handle_key(self, e) -> bool:
        if self.test is None or e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        b = self.behaviour_for_key(e.text().strip())
        if b is None:
            return False
        if not e.isAutoRepeat():
            self.press_behaviour(b)
        return True

    def _handle_key_release(self, e) -> bool:
        if self.test is None or e.isAutoRepeat():
            return False
        b = self.behaviour_for_key(e.text().strip())
        return b is not None and self.release_behaviour(b)

    def keyReleaseEvent(self, e):
        if self._handle_key_release(e):
            return
        super().keyReleaseEvent(e)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyRelease and self._handle_key_release(e):
            return True
        if e.type() == QEvent.KeyPress:
            if self._handle_key(e):
                return True
            if obj is not self.player and e.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right) \
                    and not (obj is self.player.slider and e.key() != Qt.Key_Space):
                self.player.keyPressEvent(e)
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if self._handle_key(e):
            return
        if e.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right):
            self.player.keyPressEvent(e)
            return
        super().keyPressEvent(e)

    # ================================================================== detection settings
    def _load_detection_form(self):
        if self.test is None or self.project is None:
            return
        self.det_obj = self.project.detection_for(self.test)
        self.det_form.load(self.det_obj)
        self._update_det_lbl()

    def overrides(self) -> dict:
        ov = self.test.detection or {}
        return {k: v for k, v in ov.items() if k in SPEC_ATTRS and v != getattr(self.project.detection, k, None)}

    def _update_det_lbl(self):
        if self.test is None:
            return
        ov = self.overrides()
        labels = {a: lbl for a, lbl, *_ in DETECTION_SPEC}
        if ov:
            self.det_lbl.setText(f"<b>{len(ov)}</b> setting{'s' if len(ov) != 1 else ''} differ from the experiment "
                                 "defaults for this test: " + ", ".join(f"<i>{labels[k]}</i>" for k in ov))
        else:
            self.det_lbl.setText("This test uses the experiment's default detection settings. Changes made here "
                                 "apply to this test only.")

    def _detection_changed(self):
        if self.test is None or self.det_obj is None:
            return
        base = self.project.detection
        ov = {k: v for k, v in (self.test.detection or {}).items() if k not in SPEC_ATTRS}
        for a in SPEC_ATTRS:
            v = getattr(self.det_obj, a)
            if v != getattr(base, a):
                ov[a] = v
        self.test.detection = ov
        self.main.mark_dirty()
        self._update_det_lbl()
        if not self.chk_preview.isChecked():
            self.chk_preview.setChecked(True)  # refreshes the frame
        else:
            self._refresh_frame()

    def reset_detection(self):
        if self.test is None:
            return
        self.test.detection = {}
        self.main.mark_dirty()
        self._load_detection_form()
        self._refresh_frame()

    def apply_detection_to_all(self, confirm: bool = True):
        p, t = self.project, self.test
        if t is None:
            return
        others = [x for x in p.tests if x is not t]
        if not others:
            return
        if confirm and QMessageBox.question(
                self, "Apply to all tests", f"Use this test's detection settings for all {len(others)} other "
                "tests? (Their own per-test settings are replaced.)") != QMessageBox.Yes:
            return
        for x in others:
            x.detection = dict(t.detection or {})
        self.main.mark_dirty()
        self.main.status(f"Detection settings applied to {len(others)} tests.")

    def save_detection_as_default(self):
        p, t = self.project, self.test
        if t is None or self.det_obj is None:
            return
        for a in SPEC_ATTRS:
            setattr(p.detection, a, getattr(self.det_obj, a))
        t.detection = {k: v for k, v in (t.detection or {}).items() if k not in SPEC_ATTRS}
        self.main.mark_dirty()
        self._load_detection_form()
        self.main.status("Saved as the experiment's default detection settings.")

    # ================================================================== track editing
    def _edit_index(self) -> int:
        return self.edit_animal.currentData() or 0

    def _mark_toggled(self, on):
        v = self.player.view
        v.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        v.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)
        if on:
            self.player.pause()
            self.player.view.setFocus()
        self._refresh_frame()

    def _view_clicked(self, x, y):
        if self.move_btn.isChecked():
            self.place_moveable_zone(x, y)
            self.move_btn.setChecked(False)
        elif self.mark_btn.isChecked():
            self.mark_position(x, y)

    def _new_track(self) -> Track:
        p, t = self.project, self.test
        fps = self.player.fps
        dur = t.duration_s or p.test_duration_s
        if not dur and self.player.source is not None:
            dur = max(0.0, self.player.source.duration - t.start_s)
        n = max(1, int(round(dur * fps)))
        nan = np.full(n, np.nan)
        tr = Track(t=np.arange(n) / fps, x=nan.copy(), y=nan.copy(), fps=fps, detected=np.zeros(n, bool))
        tr.meta["video_start_s"] = t.start_s
        tr.meta["source"] = "manual"
        return tr

    def _push_undo(self, ai):
        self._undo.append((ai, self.tracks[ai].copy() if ai < len(self.tracks) else None))
        del self._undo[:-30]

    def _save_tracks(self, msg=""):
        self.project.save_tracks(self.test, self.tracks)
        self.main.mark_dirty()
        self._update_info()
        self._mark_stale("results", "plots")
        self._refresh_frame()
        self.edit_lbl.setText(msg)

    def mark_position(self, x, y):
        if self.test is None or self.player.source is None or self.project.path is None:
            return False
        ai = self._edit_index()
        created = False
        while len(self.tracks) <= ai:
            self.tracks.append(self._new_track())
            created = True
        tr = self.tracks[ai]
        j = self.sample_at(tr, self.player.time - self.video_start(tr))
        if j is None:
            self.edit_lbl.setText("The current frame is outside the tracked period.")
            return False
        if not created:
            self._push_undo(ai)
        else:
            self._undo.append((ai, None))
        if math.isfinite(tr.x[j]) and math.isfinite(tr.y[j]):
            dx, dy = x - tr.x[j], y - tr.y[j]
            for cx, cy in (("hx", "hy"), ("tx", "ty")):
                getattr(tr, cx)[j] += dx
                getattr(tr, cy)[j] += dy
        else:
            for c in ("hx", "hy", "tx", "ty", "angle"):
                getattr(tr, c)[j] = np.nan
        tr.x[j], tr.y[j] = x, y
        tr.detected[j] = True
        self._save_tracks(f"Marked ({x:.0f}, {y:.0f}) at {tr.t[j]:.2f} s")
        if created:
            self._load_tracks()
        if self.advance_spin.value():
            self.player.seek(self.player.index + self.advance_spin.value())
        return True

    def set_range(self, which: int, t: float | None = None):
        if self.test is None:
            return
        self._range[which] = round(self.test_time() if t is None else t, 3)
        self._update_range_lbl()
        self._refresh_frame()

    def _update_range_lbl(self):
        a, b = self._range
        f = lambda v: "—" if v is None else f"{v:.2f} s"  # noqa: E731
        self.range_lbl.setText(f"Range (test time): {f(a)} → {f(b)}")

    def _range_indices(self, tr):
        a, b = self._range
        if a is None or b is None or len(tr) == 0:
            self.edit_lbl.setText("Set the range start and end first.")
            return None
        a, b = sorted((a, b))
        idx = np.flatnonzero((tr.t >= a - 1e-6) & (tr.t <= b + 1e-6))
        if len(idx) == 0:
            self.edit_lbl.setText("No track samples in the range.")
            return None
        return idx

    def interpolate_range(self):
        ai = self._edit_index()
        if self.test is None or ai >= len(self.tracks):
            return False
        tr = self.tracks[ai]
        idx = self._range_indices(tr)
        if idx is None:
            return False
        self._push_undo(ai)
        for c in ("x", "y", "hx", "hy", "tx", "ty"):
            v = getattr(tr, c)
            ok = np.isfinite(v)
            ok[idx] = False
            v[idx] = np.interp(tr.t[idx], tr.t[ok], v[ok]) if ok.sum() >= 1 else np.nan
        with np.errstate(invalid="ignore"):
            tr.angle[idx] = np.degrees(np.arctan2(tr.hy[idx] - tr.ty[idx], tr.hx[idx] - tr.tx[idx]))
        tr.detected[idx] = False
        self._save_tracks(f"Interpolated {len(idx)} samples.")
        return True

    def delete_range(self):
        ai = self._edit_index()
        if self.test is None or ai >= len(self.tracks):
            return False
        tr = self.tracks[ai]
        idx = self._range_indices(tr)
        if idx is None:
            return False
        self._push_undo(ai)
        for c in ("x", "y", "hx", "hy", "tx", "ty", "angle"):
            getattr(tr, c)[idx] = np.nan
        tr.detected[idx] = False
        self._save_tracks(f"Deleted {len(idx)} positions.")
        return True

    def undo_edit(self):
        if not self._undo or self.test is None:
            return
        ai, prev = self._undo.pop()
        if prev is None:  # the track was created by the edit
            if ai < len(self.tracks):
                self.tracks.pop(ai)
                tp = self.project.track_path(self.test, ai)
                if tp.exists():
                    tp.unlink()
            if not self.tracks:
                self.test.status = "pending"
                self.main.mark_dirty()
                self._update_info()
                self._mark_stale("results", "plots")
                self._refresh_frame()
                self.edit_lbl.setText("Undone.")
                return
        else:
            self.tracks[ai] = prev
        self._save_tracks("Undone.")

