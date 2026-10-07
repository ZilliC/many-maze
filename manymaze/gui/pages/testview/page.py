"""Test view: review one test's video with zones and track overlay, tune detection, track, score and edit."""

from __future__ import annotations

import html
import math
from functools import partial
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFrame, QGraphicsItem, QGraphicsTextItem,
                               QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
                               QSplitter, QTableWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from ....core import workflow as wf
from ....core.batch import track_tests
from ....core.plots import heatmap, speed_trace, track_plot
from ....core.track import Track
from ....core.tracking import DetectionSettings
from ... import ribbon, theme
from ...widgets import PlotCanvas, VideoPlayer, Worker, error_box, run_with_progress
from ..base import Page
from .common import _num_item, _ro_item
from .detection import DetectionMixin
from .overlay import OverlayMixin
from .scoring import ObservationClock, ScoringMixin
from .track_edit import TrackEditMixin

INFO_KEYS = {"Test", "Animal", "Group", "Sex", "Stage", "Trial", "Apparatus", "Period"}
SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
STATUS_COLORS = {"tracked": "#16a34a", "scored": "#0891b2", "pending": "#d97706", "skipped": "#9333ea",
                 "superseded": "#94a3b8", "excluded": "#94a3b8"}


class TestViewPage(DetectionMixin, OverlayMixin, ScoringMixin, TrackEditMixin, Page):
    """Review and score one test, laid out like an ANY-maze test panel: a small toolbar, the title line
    ("Open field: Animal C1, Day 1 trial 1 - 0:09"), the video on a light background and the time slider, with
    Results / Plots / Scoring / Detection / Track editing tabs on the right."""

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

        # ---- commands (ribbon groups Test, Playback, Scoring, View, Track editing) -------------------
        def act(text, ic, fn=None, tip="", checkable=False, large=False):
            return ribbon.action(self, text, ic, fn, tip, checkable, large)

        self.prev_btn = act("Previous test", "back", lambda: self.step_test(-1), "Previous test of the schedule")
        self.next_btn = act("Next test", "forward", lambda: self.step_test(1), "Next test of the schedule")
        self.track_btn = act("Track this test", "tracking", lambda: self.track_this_test(),
                             "Track the animal in this test's video", large=True)
        self.a_play = act("Play", "play", self.play, "Play the video (Space)")
        self.a_pause = act("Pause", "pause", self.pause, "Pause the video (Space)")
        self.a_stop = act("Stop", "stop", self.stop, "Stop and go back to the start of the test")
        self.a_back = act("Step back", "previous", lambda: self.player.step(-1), "Back one frame (←; Shift: 1 s)")
        self.a_fwd = act("Step forward", "next", lambda: self.player.step(1), "Forward one frame (→; Shift: 1 s)")
        self.a_speed = act("Speed 1×", "speed", None, "Playback speed")
        speed_menu = QMenu(self)
        self._speed_group = QActionGroup(self)
        for v in SPEEDS:
            a = speed_menu.addAction(f"{v:g}×")
            a.setCheckable(True)
            a.setChecked(v == 1.0)
            a.triggered.connect(lambda _=False, v=v: self.set_speed(v))
            self._speed_group.addAction(a)
        self.a_speed.setMenu(speed_menu)
        self.a_keys = act("On-screen keys", "keyboard", self._keys_toggled,
                          "Show the scoring keys as on-screen buttons (mouse / touch screen)", True, True)
        self.a_keys.blockSignals(True)
        self.a_keys.setChecked(True)
        self.a_keys.blockSignals(False)
        self.clock_start_btn = act("Start", "timer", lambda: self.clock_start(),
                                   "TakeNote: start the observation clock (tests without video)")
        self.clock_pause_btn = act("Pause", "pause", self.clock_pause, "TakeNote: pause the observation clock")
        self.clock_stop_btn = act("Stop", "stop", lambda: self.clock_stop(),
                                  "TakeNote: stop the observation — the test is scored")
        self.chk_zones = act("Zones", "zone", self._refresh_frame, "Show the apparatus zones", True)
        self.chk_animal = act("Animal", "animal", self._refresh_frame, "Show the tracked animal", True)
        self.chk_trail = act("Trail", "trail", self._refresh_frame, "Show the trail behind the animal", True)
        self.chk_preview = act("Detection preview", "detect", self._refresh_frame,
                               "Run the detector on the displayed frame with this test's settings and tint the "
                               "detected foreground", True)
        for a in (self.chk_zones, self.chk_animal, self.chk_trail):
            a.blockSignals(True)
            a.setChecked(True)
            a.blockSignals(False)
        self.mark_btn = act("Mark position", "mark", self._mark_toggled,
                            "Mark the animal's position by clicking on the video", True, True)
        self.a_range_start = act("Range start", "range", lambda: self.set_range(0),
                                 "Start the time range at the current time")
        self.a_range_end = act("Range end", "range", lambda: self.set_range(1),
                               "End the time range at the current time")
        self.a_interp = act("Interpolate", "interpolate", lambda: self.interpolate_range(),
                            "Interpolate the positions in the time range")
        self.a_del_range = act("Delete range", "delete", lambda: self.delete_range(),
                               "Delete the positions in the time range")
        self.undo_btn = act("Undo", "undo", self.undo_edit, "Undo the last track edit")

        # ---- test panel: toolbar, title, video, time slider ----------------------------------------
        self.test_combo = QComboBox()
        self.test_combo.setMinimumWidth(200)
        self.test_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.test_combo.setMinimumContentsLength(24)
        self.test_combo.setToolTip("Test shown")
        self.test_combo.currentIndexChanged.connect(self._combo_changed)
        tb = QHBoxLayout()
        tb.setSpacing(2)
        for i, a in enumerate((self.a_play, self.a_pause, self.a_stop, None, self.a_back, self.a_fwd, self.a_speed,
                               None, self.undo_btn, self.mark_btn, None, self.chk_preview)):
            if a is None:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color:{theme.BORDER};")
                sep.setFixedHeight(22)
                tb.addSpacing(4)
                tb.addWidget(sep)
                tb.addSpacing(4)
                continue
            tb.addWidget(self._tool(a, icon_only=a is not self.a_speed))
        tb.addStretch()
        tb.addWidget(QLabel("Test"))
        tb.addWidget(self.test_combo)
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("TestTitle")
        self.title_lbl.setStyleSheet(f"font-size:17px;color:{theme.TEXT};padding:4px 2px 0 2px;")
        self.info_lbl = QLabel()
        self.info_lbl.setObjectName("Hint")
        self.info_lbl.setStyleSheet("padding:0 2px 2px 2px;")
        head = QWidget()
        head.setObjectName("TestPanelHead")
        head.setStyleSheet(f"QWidget#TestPanelHead{{background:{theme.RIBBON_BG};border-bottom:1px solid "
                           f"{theme.BORDER};}}")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(8, 4, 8, 6)
        hl.setSpacing(2)
        hl.addLayout(tb)
        hl.addWidget(self.title_lbl)
        hl.addWidget(self.info_lbl)

        self.player = VideoPlayer()
        self.player.overlay = self._overlay
        self.player.frame_changed.connect(self._frame_changed)
        self.player.view.clicked.connect(self._view_clicked)
        for b in self.player.findChildren(QPushButton):  # the panel toolbar and the ribbon drive playback
            b.hide()
        v = self.player.view
        v.setBackgroundBrush(QColor(theme.WORK_BG))
        v.setFrameShape(QFrame.NoFrame)
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.setDragMode(QGraphicsView.ScrollHandDrag)
        v.setToolTip("Wheel: zoom · drag: pan · Alt+double-click: fit")
        self.player.time_lbl.setStyleSheet(f"color:{theme.MUTED};")
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
        self.trail_spin = QDoubleSpinBox()
        self.trail_spin.setRange(0, 3600)
        self.trail_spin.setDecimals(0)
        self.trail_spin.setValue(5)
        self.trail_spin.setSuffix(" s")
        self.trail_spin.setToolTip("Length of the trail drawn behind the animal")
        self.trail_spin.valueChanged.connect(self._refresh_frame)
        for sp, wd in ((self.start_spin, 96), (self.dur_spin, 96), (self.trail_spin, 66)):
            sp.setFixedWidth(wd)
        timing = QHBoxLayout()
        timing.setContentsMargins(8, 0, 8, 0)
        timing.addWidget(QLabel("Test start"))
        timing.addWidget(self.start_spin)
        timing.addWidget(b_start)
        timing.addSpacing(10)
        timing.addWidget(QLabel("Duration"))
        timing.addWidget(self.dur_spin)
        timing.addWidget(b_end)
        timing.addSpacing(10)
        timing.addWidget(QLabel("Trail"))
        timing.addWidget(self.trail_spin)
        timing.addStretch()
        self.pos_lbl = QLabel()
        self.pos_lbl.setObjectName("Hint")
        pos_row = QHBoxLayout()
        pos_row.setContentsMargins(8, 0, 8, 4)
        pos_row.addStretch()
        pos_row.addWidget(self.pos_lbl)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        ll.addWidget(head)
        self.player.layout().setContentsMargins(8, 0, 8, 0)
        ll.addWidget(self.player, 1)
        ll.addLayout(timing)
        ll.addLayout(pos_row)

        # ---- right: tabs -------------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SideTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet(
            "QTabWidget#SideTabs::pane{border:none;border-top:1px solid #d6d6d6;}"
            "QTabWidget#SideTabs > QTabBar::tab{background:transparent;border:none;"
            f"border-bottom:2px solid transparent;padding:7px 12px;margin:0 2px;color:{theme.MUTED};font-size:13px;}}"
            f"QTabWidget#SideTabs > QTabBar::tab:selected{{color:{theme.ACCENT};border-bottom:2px solid "
            f"{theme.ACCENT};}}"
            f"QTabWidget#SideTabs > QTabBar::tab:hover:!selected{{color:{theme.TEXT};background:{theme.HOVER};}}")
        self.tabs.addTab(self._build_results_tab(), "Results")
        self.tabs.addTab(self._build_plots_tab(), "Plots")
        self.tabs.addTab(self._build_scoring_tab(), "Scoring")
        self.tabs.addTab(self._build_detection_tab(), "Detection")
        self.tabs.addTab(self._build_edit_tab(), "Track editing")
        self.tabs.currentChanged.connect(lambda _: self._ensure_tab())

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([740, 440])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split, 1)

        for w in (self.player, self.player.view, self.player.slider, self.events_table):
            w.installEventFilter(self)
        self._enable(False)

    def _tool(self, action, icon_only=False) -> QToolButton:
        """A flat button for an action (panel toolbar, tabs); keyboard focus stays with the video."""
        b = QToolButton()
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setIconSize(QSize(20, 20) if icon_only else QSize(16, 16))
        b.setToolButtonStyle(Qt.ToolButtonIconOnly if icon_only else Qt.ToolButtonTextBesideIcon)
        if action.menu() is not None:
            b.setPopupMode(QToolButton.InstantPopup)
        return b

    # ================================================================== ribbon
    def ribbon_groups(self):
        return [("Test", [(self.prev_btn, "small"), (self.next_btn, "small"), self.track_btn]),
                ("Playback", [(self.a_play, "small"), (self.a_pause, "small"), (self.a_stop, "small"),
                              (self.a_back, "small"), (self.a_fwd, "small"), (self.a_speed, "small")]),
                ("Scoring", [self.a_keys, (self.clock_start_btn, "small"), (self.clock_pause_btn, "small"),
                             (self.clock_stop_btn, "small")]),
                ("View", [(self.chk_zones, "small"), (self.chk_animal, "small"), (self.chk_trail, "small"),
                          (self.chk_preview, "small")]),
                ("Track editing", [self.mark_btn, (self.a_interp, "small"), (self.a_del_range, "small"),
                                   (self.undo_btn, "small")])]

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.pause()
        if self.test is not None and self.player.source is not None:
            self.player.seek_time(self.video_start())

    def set_speed(self, v: float):
        self.player.speed = float(v)
        self.player.speed_btn.setText(f"{v:g}×")
        self.a_speed.setText(f"Speed {v:g}×")
        for a in self._speed_group.actions():
            a.setChecked(a.text() == f"{v:g}×")
        if self.player.playing:
            self.player.play()

    def _keys_toggled(self, on):
        self.pad.setVisible(on)

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
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.verticalHeader().setDefaultSectionSize(26)
        self.results_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.results_table.setAlternatingRowColors(False)
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
        """Flush before saving: finished events are already in the test; a behaviour still being scored stays open
        (Ctrl+S must not end it). It is closed when the test is left (on_hide / load_test / shutdown)."""
        if self.test is not None:
            self.test.events.sort(key=lambda e: e["t"])

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
        self._enable(True)
        self._update_info()
        self._update_clock_ui()
        self._mark_stale("results", "plots")
        self.player.view.setFocus()

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
        video = on and self.player.source is not None
        for a in (self.a_play, self.a_pause, self.a_stop, self.a_back, self.a_fwd, self.a_speed):
            a.setEnabled(video)
        for a in (self.mark_btn, self.a_range_start, self.a_range_end, self.a_interp, self.a_del_range,
                  self.undo_btn, self.chk_preview):
            a.setEnabled(on)
        self.prev_btn.setEnabled(self.test_combo.count() > 1)
        self.next_btn.setEnabled(self.test_combo.count() > 1)

    def _clear_views(self):
        self.info_lbl.setText("")
        self.title_lbl.setText("No test selected")
        self.pos_lbl.setText("")
        self.results_table.setRowCount(0)
        self.results_lbl.setText("")
        self.events_table.setRowCount(0)
        self.pad.set_behaviours([])
        self._load_notes()
        self.clock_box.hide()
        self._placeholder("No test selected")

    def _placeholder(self, text: str):
        self.player.view.set_frame(np.full((360, 480, 3), 228, np.uint8))
        self.player.time_lbl.setText("--:--")
        self._set_hud([(line, "#e2e8f0") for line in text.splitlines()])

    def _update_info(self):
        t, p = self.test, self.project
        if t is None or p is None:
            self.info_lbl.setText("")
            return
        a = p.get_animal(t.animal_id)
        grp = (f"Treatment <span style='color:{wf.display_color(p, a.group)}'>"
               f"{html.escape(wf.treatment_text(p, a.group))}</span>" if a and a.group else "")
        if a is not None and a.retired:
            grp += " <span style='color:#dc2626'>(animal retired)</span>"
        dur = t.duration_s or p.test_duration_s
        status_col = STATUS_COLORS.get(t.status, "#334155")
        video = (f"<span title='{html.escape(p.abs_path(t.video))}'>{html.escape(Path(t.video).name)}</span>"
                 if t.video else "no video")
        parts = [f"Test {t.id}" + (f" (attempt {t.attempt})" if t.attempt > 1 else ""), grp, video,
                 f"test period {t.start_s:g}–{t.start_s + dur:g} s" if dur else "",
                 f"<span style='color:{status_col}'>{t.status}</span>"]
        self.info_lbl.setText(" · ".join(x for x in parts if x))
        self._update_title()

    def _update_title(self, tt: float | None = None):
        """ANY-maze style title line: "Open field: Animal C1, Day 1 trial 1 - 0:09"."""
        t = self.test
        if t is None or self.project is None:
            self.title_lbl.setText("No test selected")
            return
        if tt is None:
            tt = self.test_time() if (self.player.source is not None or self.clock.state != "stopped") else 0.0
        m, sec = divmod(int(max(0.0, tt)), 60)
        who = f"Animal {t.animal_id}" if t.animal_id else "No animal"
        trial = f"{t.stage} trial {t.trial}" if t.stage else f"Trial {t.trial}"
        self.title_lbl.setText(f"{t.apparatus + ': ' if t.apparatus else ''}{who}, {trial} - {m}:{sec:02d}")

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
        self._update_title(tt)
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
        return run_with_progress(self, f"Tracking test {t.id}", partial(track_tests, p, [t]),
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
            from ....core.measures import kinematics

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
