"""Track plots, heat maps (per test and per treatment) and video export views of the Data page."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QStackedWidget, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ....core import charts, plots
from ....core.measures import kinematics
from ....core.pauses import period_frames
from ....core.videoexport import export_video
from ...figures import FIG_FILTER, figure_to_clipboard
from ...widgets import PlotCanvas, cv_to_qpixmap, error_box
from .dialogs import VideoExportDialog
from .playback import TrackPlayback

POSITION = "Position (all time)"
RIBBON_CONTROL_STYLE = "QComboBox, QDoubleSpinBox { padding-top: 1px; padding-bottom: 1px; min-height: 20px; }"
PLOT_VIEWS = ("track", "heat", "video")


class PlotViewsMixin:
    """The track plot, heat map and video export views of ResultsPage (a list of the shown tests and plots of the
    selected one), their ribbon controls and treatment heat maps."""

    def _build_plot_view(self) -> QWidget:
        """Track plots / heat maps / video export: a list of tests and the selected test."""
        self.test_list = QListWidget()
        self.test_list.setObjectName("TestList")
        self.test_list.setFixedWidth(230)
        self.test_list.currentRowChanged.connect(self._test_list_changed)
        self.detail_lbl = QLabel("Select a test")
        self.detail_lbl.setObjectName("PlotCaption")
        self.mode_a = QToolButton()
        self.mode_b = QToolButton()
        self._modes = QButtonGroup(self)
        for i, b in enumerate((self.mode_a, self.mode_b)):
            b.setObjectName("ModeButton")
            b.setCheckable(True)
            b.setAutoRaise(False)
            self._modes.addButton(b, i)
        self._modes.idClicked.connect(self._mode_clicked)
        self.tabs = QTabWidget()  # plot canvases; the explorer / mode buttons choose the one shown
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        self.track_canvas = PlotCanvas()
        self.heat_canvas = PlotCanvas()
        self.speed_canvas = PlotCanvas()
        self.groups_canvas = PlotCanvas()
        for c, name in ((self.track_canvas, "Track"), (self.heat_canvas, "Heat map"),
                        (self.speed_canvas, "Speed"), (self.groups_canvas, "Groups")):
            c.setMinimumSize(260, 260)
            self.tabs.addTab(c, name)
        self.tabs.currentChanged.connect(self._tab_changed)
        plots_panel = QWidget()
        pl = QVBoxLayout(plots_panel)
        pl.setContentsMargins(18, 0, 0, 0)
        ph = QHBoxLayout()
        ph.addWidget(self.detail_lbl, 1)
        ph.setSpacing(0)
        ph.addWidget(self.mode_a)
        ph.addWidget(self.mode_b)
        pl.addLayout(ph)
        pl.addWidget(self.tabs, 1)
        self.playback = TrackPlayback()  # animated track: play / pause, speed, time slider
        self.playback.setVisible(False)
        pl.addWidget(self.playback)
        self.video_opts = VideoExportDialog(self).embed()
        self.video_lbl = QLabel()
        self.video_lbl.setObjectName("PlotCaption")
        vbtn = QPushButton("Export video…")
        vbtn.setDefault(True)
        vbtn.clicked.connect(lambda: self.export_video())
        self.video_preview = QLabel()
        self.video_preview.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.video_preview.setMinimumSize(320, 240)
        self.video_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_panel = QWidget()
        vl = QVBoxLayout(video_panel)
        vl.setContentsMargins(18, 0, 0, 0)
        vl.addWidget(self.video_lbl)
        vh = QHBoxLayout()
        vform = QVBoxLayout()
        cap = QLabel("Overlays")
        cap.setObjectName("SectionTitle")
        vform.addWidget(cap)
        vform.addWidget(self.video_opts)
        vb = QHBoxLayout()
        vb.addWidget(vbtn)
        vb.addStretch()
        vform.addLayout(vb)
        vform.addStretch()
        vh.addLayout(vform)
        vh.addSpacing(18)
        vh.addWidget(self.video_preview, 1)
        vl.addLayout(vh, 1)
        self.plot_stack = QStackedWidget()
        self.plot_stack.addWidget(plots_panel)
        self.plot_stack.addWidget(video_panel)
        plot_view = QWidget()
        pv = QHBoxLayout(plot_view)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        pv.addWidget(self.test_list)
        pv.addWidget(self.plot_stack, 1)
        self._build_plot_options()
        return plot_view


    def _mode_clicked(self, i: int):
        if self.view == "track":
            self.tabs.setCurrentIndex(2 if i else 0)
        elif self.view == "heat":
            if i and (self.groups_canvas.figure is None or not self.groups_canvas.figure.axes):
                self.group_heatmaps()
            self.tabs.setCurrentIndex(3 if i else 1)

    def _sync_modes(self):
        i = self.tabs.currentIndex()
        (self.mode_b if i in (2, 3) else self.mode_a).setChecked(True)

    def _fill_test_list(self):
        rows = self.shown_rows()
        cur = self.current_row()
        self.test_list.blockSignals(True)
        self.test_list.clear()
        sel = -1
        for i, r in enumerate(rows):
            sub = [str(r.get("Group") or "")]
            if self.segmented and r.get("Period") not in (None, "", "Whole test"):
                sub.append(str(r.get("Period")))
            text = f"Test {r.get('Test')}  ·  {r.get('Animal', '')}"
            if any(sub):
                text += "\n" + "  ·  ".join(x for x in sub if x)
            self.test_list.addItem(QListWidgetItem(text))
            if r is cur:
                sel = i
        self.test_list.setCurrentRow(sel)
        self.test_list.blockSignals(False)

    def _test_list_changed(self, i: int):
        if 0 <= i < self.proxy.rowCount():
            self.table.selectRow(i)

    def _update_video_panel(self):
        r = self.current_row()
        p = self.project
        test = p.get_test(r.get("Test")) if r is not None and p is not None else None
        if test is None:
            self.video_lbl.setText("Select a test")
            self.video_preview.clear()
            return
        self.video_lbl.setText(f"Test {test.id}  ·  {r.get('Animal', '')}"
                               + (f"  ·  {r.get('Group')}" if r.get("Group") else "")
                               + ("" if test.video else "  —  no video"))
        frame = self._frame(test) if test.video else None
        if frame is None:
            self.video_preview.clear()
            return
        pm = cv_to_qpixmap(frame)
        self.video_preview.setPixmap(pm.scaled(self.video_preview.size() * 0.98, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))

    def _build_plot_options(self):
        self.part_combo = self._ribbon_combo(130)
        for label, data in (("Centre", "centre"), ("Head", "head")):
            self.part_combo.addItem(label, data)
        self.part_combo.setToolTip("Body part drawn in track plots and heat maps")
        self.color_combo = self._ribbon_combo(170)
        for label, data in (("Time", "time"), ("Speed", "speed"), ("Single colour", "none")):
            self.color_combo.addItem(label, data)
        self.color_combo.setToolTip("Colour the track by time, speed or any per-frame parameter")
        self.markers_check = QCheckBox("Markers", self._holder)
        self.markers_check.setToolTip("Mark freezing episodes and scored behaviours on the track")
        self.markers_check.setChecked(True)
        self.split_check = QCheckBox("Split by period", self._holder)
        self.split_check.setToolTip("One small track plot per time period (time bins / custom periods; "
                                    "quarters of the test if none are set)")
        self.heat_of = self._ribbon_combo(190)
        self.heat_of.addItem(POSITION, None)
        self.heat_of.setToolTip("Heat map of the position, or only of frames where a behaviour occurred (freezing, "
                                "immobility, scored behaviours…)")
        self.heat_norm = self._ribbon_combo(130)
        for label, data in (("Automatic", "auto"), ("% of time", "percent"), ("Relative", "relative"),
                            ("Fixed max", "fixed")):
            self.heat_norm.addItem(label, data)
        self.heat_norm.setToolTip("Colour scale: each map scaled to its own maximum, % of the mapped time per bin, "
                                  "relative to the maximum, or a fixed maximum for comparing tests")
        self.heat_max = QDoubleSpinBox(self._holder)
        self.heat_max.setRange(0.001, 1e6)
        self.heat_max.setDecimals(3)
        self.heat_max.setValue(1.0)
        self.heat_max.setFixedWidth(130)
        self.heat_max.setStyleSheet(RIBBON_CONTROL_STYLE)
        self.heat_max.setToolTip("Maximum of the colour scale (same units as the colour bar)")
        self.heat_max.setEnabled(False)
        self.align_combo = self._ribbon_combo(170)
        for k, v in plots.TRANSFORMS.items():
            self.align_combo.addItem(v, k)
        self.align_combo.setToolTip("Orientation of this test in treatment heat maps (rotate / mirror so that "
                                    "equivalent parts of the apparatus line up between tests)")
        self._track_opts = [self.color_combo, self.markers_check, self.split_check]
        self._heat_opts = [self.heat_of, self.heat_norm, self.heat_max, self.align_combo]
        for c in (self.part_combo, self.color_combo, self.heat_of, self.heat_norm):
            c.currentIndexChanged.connect(self._options_changed)
        for c in (self.markers_check, self.split_check):
            c.toggled.connect(self._options_changed)
        self.heat_max.valueChanged.connect(self._options_changed)
        self.align_combo.currentIndexChanged.connect(self._align_changed)

    def export_video(self, path: str | None = None, options=None):
        """Render the selected test's video with overlays in the background (progress dialog, cancellable)."""
        r = self.current_row()
        p = self.project
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        if test is None or not test.video:
            error_box(self, "Export video", "This test has no video.")
            return
        if options is None and self.view == "video":
            options = self.video_opts.options()  # the options shown on the Video export view
        if options is None:
            dlg = VideoExportDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            options = dlg.options()
        if path is None:
            base = str(p.exports_dir() / f"test_{test.id:04d}_overlay.mp4") if p.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Export video with overlays", base,
                                                  "MPEG-4 video (*.mp4);;AVI video (*.avi)")
            if not path:
                return
        if Path(path).suffix.lower() not in (".mp4", ".m4v", ".mov", ".avi"):
            path += ".mp4"

        def work(progress, stop):
            return export_video(p, test, path, options, progress, stop)

        def done(out):
            self.main.status(f"Video saved: {out}" if out else "Video export cancelled")

        return self._run(f"Exporting video of test {test.id}", work, on_done=done)

    def _frame(self, test):
        if test.id not in self._frames:
            self._frames[test.id] = self.project.start_frame(test)
        return self._frames[test.id]

    def _select_changed(self):
        r = self.current_row()
        p = self.project
        self._update_actions()
        if self.view in PLOT_VIEWS:
            i = self.table.currentIndex().row()
            if i != self.test_list.currentRow() and i < self.test_list.count():
                self.test_list.blockSignals(True)
                self.test_list.setCurrentRow(i)
                self.test_list.blockSignals(False)
            if self.view == "video":
                self._update_video_panel()
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        key = (r.get("Test"), r.get("Animal"), r.get("Period"))
        if test is None or (key == self._detail_key and self._detail is not None):
            return
        self._detail_key = key
        tracks = p.load_tracks(test) if p.has_track(test) else []
        ids = [test.animal_id] + list(test.extra_animals)
        ai = ids.index(r.get("Animal")) if r.get("Animal") in ids else 0
        title = f"Test {test.id} · {r.get('Animal', '')}"
        if r.get("Group"):
            title += f" · {r.get('Group')}"
        self.align_combo.blockSignals(True)
        self.align_combo.setCurrentIndex(max(0, self.align_combo.findData(
            (test.variables or {}).get("heatmap_transform", "none"))))
        self.align_combo.blockSignals(False)
        if not tracks or ai >= len(tracks):
            self._detail = None
            self.playback.detach()
            self.detail_lbl.setText(f"{title} — no track")
            for c in (self.track_canvas, self.heat_canvas, self.speed_canvas):
                c.set_figure(Figure(figsize=(3, 3)))
            return
        tr = tracks[ai]
        full = tr
        app = p.apparatus_of(test)
        period = r.get("Period", "Whole test")
        t_range = None
        if period and period != "Whole test":
            t_range = self._period_range(test, tr, period)
            if t_range is not None:
                title += f" · {period}"
        # periods are in test time (as the results): select the frames by their test time, leaving out the paused
        # ones (the whole test too); the track keeps its recording times for the video
        keep = period_frames(tr, test.pauses, t_range)
        if not keep.all():
            tr = tr.take(keep)
        self._detail = {"test": test, "track": tr, "full": full, "app": app, "t_range": t_range,
                        "events": test.events if ai == 0 else [], "others": [o for j, o in enumerate(tracks) if j != ai]}
        self.detail_lbl.setText(title)
        self._fill_param_combos(app, tr)
        self._stale = {0, 1, 2}
        self._render_current()

    def _periods(self, test, track) -> list[tuple[str, float, float]]:
        """Time bins, custom and event-anchored periods of a test ([] if they cannot be computed)."""
        try:
            return self.project.test_periods(test, track)
        except Exception:
            return []

    def _period_range(self, test, track, label) -> tuple[float, float] | None:
        return next(((a, b) for lab, a, b in self._periods(test, track) if lab == label), None)

    def _fill_param_combos(self, app, tr):
        beh = self.project.behaviours
        params = charts.parameters(app, tr, beh)
        for combo, items, keep in (
                (self.color_combo, [p for p in params if p.kind != charts.STATE and p.group not in ("Zones",)],
                 [("Time", "time"), ("Speed", "speed"), ("Single colour", "none")]),
                (self.heat_of, [p for p in params if p.kind == charts.STATE and p.group not in ("Position",)],
                 [(POSITION, None)])):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for label, data in keep:
                combo.addItem(label, data)
            combo.insertSeparator(combo.count())
            for prm in items:
                if prm.name in ("Speed",):
                    continue
                combo.addItem(prm.label if combo is self.color_combo else prm.name, prm.name)
            i = combo.findData(cur)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)

    def _sync_playback(self):
        """The playback bar is shown under the (single) track plot only."""
        on = self.tabs.currentIndex() == 0 and getattr(self, "view", None) == "track"
        if not on:
            self.playback.pause()
        self.playback.setVisible(on)

    def _tab_changed(self, *_):
        i = self.tabs.currentIndex()
        self._sync_modes()
        self._sync_playback()
        for w in self._track_opts:
            w.setEnabled(i == 0)
        for w in self._heat_opts:
            w.setEnabled(i in (1, 3))
        self.heat_max.setEnabled(i in (1, 3) and self.heat_norm.currentData() == "fixed")
        self._render_current()

    def _options_changed(self, *_):
        self.heat_max.setEnabled(self.tabs.currentIndex() in (1, 3) and self.heat_norm.currentData() == "fixed")
        if self._detail is not None:
            self._stale |= {0, 1}
            self._render_current()

    def _align_changed(self, *_):
        r = self.current_row()
        p = self.project
        if r is None or p is None:
            return
        test = p.get_test(r.get("Test"))
        if test is None:
            return
        tf = self.align_combo.currentData()
        if tf == "none":
            test.variables.pop("heatmap_transform", None)
        else:
            test.variables["heatmap_transform"] = tf
        self.main.mark_dirty()
        self.main.status(f"Test {test.id}: group heat map alignment “{plots.TRANSFORMS[tf]}”")

    def plot_options(self) -> dict:
        return {"part": self.part_combo.currentData(), "color_by": self.color_combo.currentData() or "time",
                "markers": self.markers_check.isChecked(), "split": self.split_check.isChecked(),
                "heat_of": self.heat_of.currentData(), "norm": self.heat_norm.currentData(),
                "vmax": self.heat_max.value() if self.heat_norm.currentData() == "fixed" else None}

    def set_plot_options(self, **kw):
        """Programmatic setup (tests): part, color_by, markers, split, heat_of, norm, vmax, align."""
        combos = {"part": self.part_combo, "color_by": self.color_combo, "heat_of": self.heat_of,
                  "norm": self.heat_norm, "align": self.align_combo}
        for k, v in kw.items():
            if k in combos:
                i = combos[k].findData(v)
                if i < 0:
                    raise ValueError(f"{k}: {v!r} not available")
                combos[k].setCurrentIndex(i)
            elif k == "markers":
                self.markers_check.setChecked(bool(v))
            elif k == "split":
                self.split_check.setChecked(bool(v))
            elif k == "vmax":
                self.heat_max.setValue(float(v))

    def _mask(self, track, app, which, test, events=None):
        if not which:
            return None
        beh = self.project.behaviours
        return charts.state_mask(track, app, which, self.project.analysis_for(test),
                                 test.events if events is None else events, beh)

    def _render_current(self):
        i = self.tabs.currentIndex()
        if self._detail is None or i not in self._stale:
            return
        self._stale.discard(i)
        d = self._detail
        tr, app, test = d["track"], d["app"], d["test"]
        o = self.plot_options()
        s = self.project.analysis_for(test)
        beh = self.project.behaviours
        try:
            if i == 0:
                frame = self._frame(test)
                markers = plots.behaviour_markers(tr, app, s, d["events"], beh) if o["markers"] else None
                if o["split"]:
                    full = d["full"]
                    dur = full.t[-1] + full.dt if len(full) else 0
                    periods = self._periods(test, full) or [(f"{a:g}-{b:g} s", a, b) for a, b in
                                                       zip(np.linspace(0, dur, 5)[:-1], np.linspace(0, dur, 5)[1:])]
                    fm = plots.behaviour_markers(full, app, s, d["events"], beh) if o["markers"] else None
                    fig = plots.segmented_track_plot(full, app, periods, frame=frame, color_by=o["color_by"],
                                                     part=o["part"], markers=fm, settings=s, pauses=test.pauses)
                else:
                    fig = plots.track_plot(tr, app, frame=frame, size=(4.2, 4), color_by=o["color_by"],
                                           part=o["part"], markers=markers, colorbar=o["color_by"] != "none",
                                           settings=s)
                self.track_canvas.set_figure(fig)
                if o["split"]:
                    self.playback.detach()
                else:
                    self.playback.attach(self.track_canvas, tr)
                self._sync_playback()
            elif i == 1:
                mask = self._mask(tr, app, o["heat_of"], test, d["events"])
                label = plots._norm_label({"auto": "time", "fixed": "time"}.get(o["norm"], o["norm"]),
                                          o["heat_of"] or "")
                self.heat_canvas.set_figure(plots.heatmap([tr], app, size=(4.4, 4), part=o["part"], norm=o["norm"],
                                                          vmax=o["vmax"], masks=[mask], label=label,
                                                          title=o["heat_of"] or ""))
            elif i == 2:
                fr = None
                try:
                    fr = kinematics(tr, app, s).freezing
                except Exception:
                    pass
                self.speed_canvas.set_figure(plots.speed_trace(tr, app, freezing=fr, size=(4.4, 3)))
        except Exception as e:
            self.main.status(f"Plot failed: {e}")

    def render_all(self):
        """Render every stale detail tab (tests / screenshots)."""
        cur = self.tabs.currentIndex()
        for i in sorted(self._stale):
            self.tabs.setCurrentIndex(i)
        self.tabs.setCurrentIndex(cur)

    def group_heatmaps(self):
        p = self.project
        if p is None or not self.rows:
            return
        by_group: dict[str, list] = {}
        seen = set()
        for r in self.shown_rows():
            t = p.get_test(r.get("Test"))
            if t is None or t.id in seen or not p.has_track(t):
                continue
            seen.add(t.id)
            by_group.setdefault(str(r.get("Group", "")) or "No group", []).append(t)
        if not by_group:
            return
        o = self.plot_options()
        per = self.period_combo.currentData() if self.seg_check.isChecked() else None
        per = per if per and per != "Whole test" else None

        def work(progress, stop):
            return plots.group_heatmap(p, by_group, o["heat_of"], per, o["part"], o["norm"], o["vmax"], progress)

        def done(fig):
            self.groups_canvas.set_figure(fig)
            self.tabs.setCurrentWidget(self.groups_canvas)
            self._sync_modes()

        return self._run("Treatment heat maps", work, on_done=done)

    def save_figure(self, path: str | None = None):
        canvas = self.tabs.currentWidget()
        if not isinstance(canvas, PlotCanvas) or canvas.figure is None or not canvas.figure.axes:
            return
        if path is None:
            name = self.tabs.tabText(self.tabs.currentIndex()).split()[0].lower()
            base = str(self.project.exports_dir() / f"{name}.png") if self.project and self.project.path else ""
            path, _ = QFileDialog.getSaveFileName(self, "Save figure", base, FIG_FILTER)
            if not path:
                return
        if Path(path).suffix.lower() not in (".png", ".pdf", ".svg"):
            path += ".png"
        try:
            canvas.save(path)
        except Exception as e:
            error_box(self, "Save figure", e)
            return
        self.main.status(f"Saved {path}")
        return path

    def copy_figure(self):
        canvas = self.tabs.currentWidget()
        if isinstance(canvas, PlotCanvas) and figure_to_clipboard(canvas.figure):
            self.main.status("Figure copied to the clipboard")
