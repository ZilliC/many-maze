"""Apparatus page (ANY-maze "apparatus map" editor): draw the arena, zones, points and lines over a video frame;
templates; calibration with a ruler; zone groups and sequences.

The page owns the editing operations (each one an undoable step, see ``_edit``); the map (``EditorView``), the
property panel (``PropertyPanel``) and the background (``BackgroundController``) show the current apparatus."""

from __future__ import annotations

import copy
import math
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ....core import plots, security, templates
from ....core.apparatus import (DISTANCE_UNITS, Apparatus, Line, PointOfInterest, Sequence, Zone, ZoneGroup,
                                load_apparatus_file, make_grid, remove_grid, save_apparatus_file, unique_name)
from ....core.geometry import Ellipse, Polygon, Shape, shape_from_dict
from ....core.project import same_video
from ....core.templates import PALETTE, TEMPLATES
from ....core.terminology import term
from ... import theme
from ...icons import icon
from ...live_widgets import LensCorrectionDialog
from ...widgets import error_box, hint, loading, separator
from ..base import Page
from .background import BackgroundController
from .dialogs import CalibrationDialog, GridDialog, TemplateDialog
from .editor_view import EditorView
from .panel import MAP_KINDS, PropertyPanel
from .tool_icons import ARENA_HINTS, HINTS, TOOLS, tool_icon

class ApparatusPage(Page):
    title = "Apparatus"

    _clipboard: dict | None = None  # copied map objects, shared by all apparatus (class attribute)

    def __init__(self, main):
        super().__init__(main)
        self._loading = False
        self.arena_shape = "rect"
        self._undo: dict[int, list[dict]] = {}
        self._redo: dict[int, list[dict]] = {}
        self._undo_merge = None  # merge key of the last undo step (see push_undo)
        self._explorer_names: list[str] | None = None

        self.view = EditorView(self)
        self.view.selection_changed.connect(lambda: self.panel.sync_with_map(self.view.selected_key()))
        self.view.mouse_moved.connect(lambda x, y: self.coords.setText(f"x {x:.0f}  y {y:.0f} px"))
        # the list of apparatus is shown in the explorer (one sub-item per apparatus); this hidden list keeps
        # the current row
        self.app_list = QListWidget(self)
        self.app_list.hide()
        self.app_list.currentRowChanged.connect(self._app_row_changed)
        self.bg = BackgroundController(self)
        self.panel = PropertyPanel(self)

        self._build_actions()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_centre(), 1)
        lay.addWidget(self.panel)
        theme.style(self, lambda: f"QLabel#FooterCaption {{ color: {theme.HEADING}; font-size: 13px; }}")
        self._set_enabled(False)

    # ================================================================ ribbon
    def _action(self, text: str, ic, tip: str, fn=None, shortcut=None, checkable: bool = False) -> QAction:
        a = QAction(ic if isinstance(ic, QIcon) else icon(ic), text, self)
        a.setToolTip(tip + (f"  [{QKeySequence(shortcut).toString()}]" if isinstance(shortcut, str) else ""))
        a.setStatusTip(tip)
        a.setCheckable(checkable)
        if shortcut is not None:
            if isinstance(shortcut, list):
                a.setShortcuts(shortcut)
            else:
                a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.addAction(a)
        if fn is not None:
            a.triggered.connect(lambda _=False: fn())
        return a

    def _build_actions(self):
        # apparatus
        self.tpl_act = self._action("From template", tool_icon("template"),
                                    "Create an apparatus from a template (open field, plus maze, water maze…) and "
                                    "drag it over the apparatus in the image", self.create_from_template)
        self.new_act = self._action("New", "add", "Add an empty apparatus and draw its map yourself",
                                    self.add_apparatus)
        self.dup_act = self._action("Duplicate", "copy", "Duplicate the current apparatus", self.duplicate_apparatus)
        self.import_act = self._action("Import…", "import", "Copy apparatus from another experiment or from an "
                                       "apparatus file", self.import_apparatus)
        self.export_act = self._action("Export…", "save", "Save the current apparatus to a file to use it in "
                                       "other experiments or share it", self.export_apparatus)
        self.map_img_act = self._action("Map image…", "export", "Export the zone map of the current "
                                         "apparatus as an image (PNG, SVG or PDF), optionally over the background "
                                         "frame", self.export_map_image)
        self.ren_act = self._action("Rename", "edit", "Rename the current apparatus (tests that use it follow)",
                                    self.rename_apparatus)
        self.del_act = self._action("Delete", "delete", "Delete the current apparatus", self.delete_apparatus)
        # drawing tools: an exclusive group of checkable actions
        self.tool_actions: dict[str, QAction] = {}
        grp = QActionGroup(self)
        grp.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.tool_group = grp
        for key, label, sc, tip in TOOLS:
            a = self._action(label, tool_icon(key), tip, lambda k=key: self.set_tool(k), sc, checkable=True)
            grp.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        menu = QMenu(self)  # arena shape
        self.arena_actions = {}
        ag = QActionGroup(self)
        for k, lbl in (("rect", "Rectangle"), ("ellipse", "Ellipse / circle"), ("polygon", "Polygon")):
            a = menu.addAction(tool_icon(k), lbl)
            a.setCheckable(True)
            a.setChecked(k == "rect")
            ag.addAction(a)
            a.triggered.connect(lambda _=False, k=k: self.set_arena_shape(k))
            self.arena_actions[k] = a
        self.arena_menu = menu
        self.tool_actions["arena"].setMenu(menu)
        self.select_all_act = self._action("Select all", tool_icon("select_all"), "Select every zone, point and line",
                                           self.select_all, "Ctrl+A")
        self.delete_sel_act = self._action("Delete selection", tool_icon("delete_sel"),
                                           "Delete the selected objects (Del)", self.delete_selected)
        self.snap_act = self._action("Points attract", tool_icon("magnet"),
                                     "Points attract: new corners and dragged handles snap to nearby corners of "
                                     "other objects, so zones share their edges exactly", checkable=True)
        self.snap_act.toggled.connect(lambda on: setattr(self.view, "snap_enabled", bool(on)))
        self.grid_act = self._action("Zone grid", tool_icon("grid"),
                                     "Add a regular grid of zones: square cells (in real-world units), concentric "
                                     "rings or radial sectors", self.add_grid_dialog, "G")
        # define
        self.group_act = self._action("Zone group", tool_icon("group"),
                                      "Define a zone group: several zones treated as one (e.g. all corners)",
                                      self.add_group)
        self.seq_act = self._action("Sequence", tool_icon("sequence"),
                                    "Define a sequence of zone visits (e.g. spontaneous alternation)",
                                    self.add_sequence)
        # calibration / background
        self.clear_cal_act = self._action("Clear", tool_icon("clear_cal"),
                                          "Clear the calibration (results in pixels)", self.clear_calibration)
        self.bg_act = self._action("Video file…", "video_file",
                                   "Use a frame of a video file as the background image of the map",
                                   self.bg.load_dialog)
        self.testvid_act = self._action("Test video", "video", "Use a frame from the video of one of the tests")
        self.testvid_act.setMenu(self.bg.test_menu)
        self.lens_act = self._action("Lens correction…", "camera",
                                     "Correct the distortion of a wide-angle (fish-eye / barrel) lens in the test "
                                     "videos, so that the map is drawn on straight images and tests are tracked in "
                                     "them", self.lens_correction)
        # edit
        self.undo_act = self._action("Undo", "undo", "Undo the last change to the map", self.undo, QKeySequence.Undo)
        self.redo_act = self._action("Redo", tool_icon("redo"), "Redo", self.redo,
                                     [QKeySequence(QKeySequence.Redo), QKeySequence("Ctrl+Y")])
        self.copy_act = self._action("Copy", "copy", "Copy the selected zones, points and lines (Ctrl+C)",
                                     self.copy_selected, QKeySequence.Copy)
        self.paste_act = self._action("Paste", "paste",
                                      "Paste copied objects into this apparatus — also into another one (Ctrl+V)",
                                      self.paste, QKeySequence.Paste)
        # view
        self.fit_act = self._action("Scale to fit", tool_icon("fit"), "Fit the image to the window", self.view.fit, "F")
        self.labels_act = self._action("Show labels", tool_icon("labels"),
                                       "Show the names of zones, points and lines on the map", checkable=True)
        self.labels_act.setChecked(True)
        self.labels_act.toggled.connect(self._toggle_labels)

    def ribbon_groups(self):
        t = self.tool_actions
        return [
            ("Apparatus", [(self.tpl_act, "large"), (self.new_act, "small"), (self.dup_act, "small"),
                           (self.ren_act, "small"), (self.del_act, "small"), (self.import_act, "small"),
                           (self.export_act, "small")]),
            ("Apparatus map", [(t["select"], "large"), (t["polygon"], "large"), (t["rect"], "small"),
                               (t["ellipse"], "small"), (t["line"], "small"), (self.select_all_act, "small"),
                               (self.delete_sel_act, "small"), (self.snap_act, "small")]),
            ("Define", [(t["arena"], "small"), (t["point"], "small"), (self.grid_act, "small"),
                        (self.group_act, "small"), (self.seq_act, "small")]),
            ("Calibration", [(t["calibrate"], "small"), (self.clear_cal_act, "small")]),
            ("Background", [(self.bg_act, "small"), (self.testvid_act, "small"), (self.lens_act, "small")]),
            ("View", [(self.fit_act, "small"), (self.labels_act, "small"), (self.map_img_act, "small")]),
        ]

    # ============================================================== explorer
    def explorer_items(self):
        p = self.project
        return [(a.name, "zone", a.name) for a in p.apparatus] if p is not None else []

    def show_item(self, key):
        p = self.project
        i = next((i for i, a in enumerate(p.apparatus) if a.name == key), None) if p is not None else None
        if i is not None and self.app_list.currentRow() != i:
            self.app_list.setCurrentRow(i)

    def _sync_explorer(self):
        """Mirror the apparatus list in the explorer and highlight the current apparatus."""
        names = [a.name for a in self.project.apparatus] if self.project is not None else []
        if names != self._explorer_names:
            self._explorer_names = names
            self.main.refresh_explorer(self)
        if self.app is not None:
            self.main.select_explorer(self, self.app.name)

    # ================================================================ layout
    def _build_centre(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 8, 14, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.page_title = QLabel("Apparatus")
        self.page_title.setObjectName("PageTitle")
        top.addWidget(self.page_title)
        top.addStretch()
        self.app_info = hint(wrap=False)
        self.app_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.app_info)
        lay.addLayout(top)
        self.lock_lbl = QLabel("The protocol is locked: only an administrator can change the apparatus (File ▸ "
                               "Users and security).")
        self.lock_lbl.setWordWrap(True)
        self.lock_lbl.setStyleSheet("background:#fff7e0;border:1px solid #f0d58a;padding:6px 8px;")
        self.lock_lbl.hide()
        lay.addWidget(self.lock_lbl)
        lay.addWidget(self.view, 1)
        sb = QHBoxLayout()
        self.hint = hint(HINTS["select"], wrap=False)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.measure = hint(wrap=False)
        self.coords = hint(wrap=False)
        self.coords.setMinimumWidth(110)
        self.coords.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sb.addWidget(self.hint, 1)
        sb.addWidget(self.measure)
        sb.addWidget(self.coords)
        lay.addLayout(sb)
        lay.addSpacing(2)
        lay.addWidget(separator())
        lay.addSpacing(2)

        def footer_row(caption):
            row = QHBoxLayout()
            row.setSpacing(8)
            cap = QLabel(caption)
            cap.setObjectName("FooterCaption")
            cap.setFixedWidth(84)
            row.addWidget(cap)
            lay.addLayout(row)
            return row

        self.cal_label = QLabel()
        self.cal_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.ppc_spin = QDoubleSpinBox()
        self.ppc_spin.setRange(0.0, 100000.0)
        self.ppc_spin.setDecimals(3)
        self.ppc_spin.setSpecialValueText("Not calibrated")
        self.ppc_spin.setSuffix(" px/cm")
        self.ppc_spin.setMinimumWidth(130)
        self.ppc_spin.setToolTip("Pixels per centimetre. 0 = not calibrated (results in pixels).")
        self.ppc_spin.editingFinished.connect(self._ppc_edited)
        self.btn_cal = QPushButton(tool_icon("calibrate"), "Calibrate with the ruler")
        self.btn_cal.setToolTip(TOOLS[-1][3])
        self.btn_cal.clicked.connect(lambda: self.set_tool("calibrate"))
        self.btn_cal_clear = QPushButton("Clear")
        self.btn_cal_clear.setToolTip("Clear the calibration (results in pixels)")
        self.btn_cal_clear.clicked.connect(self.clear_calibration)
        self.unit_combo = QComboBox()
        for u in DISTANCE_UNITS:
            self.unit_combo.addItem(f"Results in {u}", u)
        self.unit_combo.setToolTip("The unit distances and speeds are reported in, for every apparatus of the "
                                   "experiment (measure names, charts and exports follow it). The calibration and "
                                   "the distance settings stay in centimetres.")
        self.unit_combo.currentIndexChanged.connect(lambda _i: self._unit_chosen())
        row = footer_row("Calibration")
        row.addWidget(self.cal_label, 1)
        row.addWidget(self.ppc_spin)
        row.addWidget(self.unit_combo)
        row.addWidget(self.btn_cal)
        row.addWidget(self.btn_cal_clear)
        self.bg.add_controls(footer_row("Background"))
        return w

    # ============================================================ page API
    @property
    def app(self) -> Apparatus | None:
        p = self.project
        r = self.app_list.currentRow()
        if p is None or not (0 <= r < len(p.apparatus)):
            return None
        return p.apparatus[r]

    def set_project(self, project):
        self.bg.close()
        self.bg.memory.clear()
        self._undo.clear()
        self._redo.clear()
        self._explorer_names = None
        self.set_tool("select")
        self._refresh_app_list(select_row=0)
        self.bg.refresh_tests()

    def on_show(self):
        if self.project is None:
            return
        self.panel.tabs.setTabText(0, term(self.project, "zone", plural=True))  # the experiment's terminology
        self._refresh_app_list(select=self.app)
        self.bg.refresh_tests()
        self.view.setFocus()

    def on_hide(self):
        self.commit()
        self.view.cancel_drawing()

    def commit(self):
        """Flush half-edited names (e.g. when saving while a name field has focus)."""
        if self.app is not None and not self._loading:
            self.panel.commit()

    def shutdown(self):
        self.bg.close()

    # ========================================================= apparatus list
    def _refresh_app_list(self, select: Apparatus | None = None, select_row: int | None = None):
        p = self.project
        with loading(self):
            self.app_list.clear()
            row = -1
            if p is not None:
                for i, a in enumerate(p.apparatus):
                    it = QListWidgetItem(a.name)
                    n = sum(1 for t in p.tests if t.apparatus == a.name)
                    info = TEMPLATES.get(a.template)
                    it.setToolTip(f"{info.title if info else a.template} · {n} test(s)")
                    self.app_list.addItem(it)
                    if a is select:
                        row = i
                if row < 0 and p.apparatus:
                    row = min(select_row or 0, len(p.apparatus) - 1)
            self.app_list.setCurrentRow(row)
        self._app_selected()
        self._sync_explorer()

    def _app_row_changed(self, _row):
        if not self._loading:
            self._app_selected()
            self._sync_explorer()

    def _app_selected(self):
        self._undo_merge = None
        self.view.cancel_drawing()
        self._set_enabled(self.app is not None)
        self.bg.show()
        self._rebuild_map(keep_selection=False)
        self.panel.refresh()
        self.refresh_info()

    @property
    def locked(self) -> bool:
        """The protocol is locked for the current user (core.security): the apparatus is shown read-only."""
        p = self.project
        return p is not None and not security.can(p, "edit_protocol")

    def _set_enabled(self, on: bool):
        locked = self.locked
        edit = on and not locked
        for a in [a for k, a in self.tool_actions.items() if k != "select"] + [
                self.undo_act, self.redo_act, self.grid_act, self.paste_act, self.dup_act, self.ren_act,
                self.del_act, self.bg_act, self.testvid_act, self.lens_act, self.clear_cal_act, self.delete_sel_act,
                self.group_act, self.seq_act]:
            a.setEnabled(edit)
        for a in (self.tool_actions["select"], self.copy_act, self.export_act, self.map_img_act, self.select_all_act):
            a.setEnabled(on)
        for w in (self.panel.tabs, self.ppc_spin, self.btn_cal, self.btn_cal_clear):
            w.setEnabled(edit)
        self.bg.set_enabled(edit)
        self.view.setInteractive(not locked)  # nothing on the map can be selected, moved or reshaped
        self.lock_lbl.setVisible(locked)
        for a in (self.new_act, self.import_act, self.tpl_act):
            a.setEnabled(self.project is not None and not locked)

    def security_changed(self):
        """Another user, or other security settings: the apparatus is editable or read-only."""
        if self.locked:
            self.set_tool("select")
        self._set_enabled(self.app is not None)

    def _names(self, exclude: Apparatus | None = None):
        return [a.name for a in self.project.apparatus if a is not exclude]

    def add_apparatus(self, name: str | None = None) -> Apparatus:
        cur = self.app
        app = Apparatus(name=unique_name(name or f"Apparatus {len(self.project.apparatus) + 1}", self._names()),
                        distance_unit=self.project.distance_unit)
        app.frame_size = (cur.frame_size if cur else None) or self.bg.real_frame_size()
        self.bg.inherit(cur, app)
        self.project.apparatus.append(app)
        self.main.mark_dirty()
        self._refresh_app_list(select=app)
        return app

    def duplicate_apparatus(self) -> Apparatus | None:
        cur = self.app
        if cur is None:
            return None
        app = cur.copy()
        app.name = unique_name(f"{cur.name} copy", self._names())
        self.bg.inherit(cur, app)
        self.project.apparatus.insert(self.project.apparatus.index(cur) + 1, app)
        self.main.mark_dirty()
        self._refresh_app_list(select=app)
        return app

    def import_apparatus(self, source: str | None = None, names: list[str] | None = None) -> list[Apparatus]:
        """Copy apparatus maps from another experiment (.mmaze folder) or an apparatus file (.json)."""
        if self.project is None:
            return []
        if source is None:
            source, _ = QFileDialog.getOpenFileName(
                self, "Import apparatus from an experiment (project.json) or an apparatus file",
                str(self.project.path.parent if self.project.path else Path.home()),
                "Experiments and apparatus files (project.json *.json);;All files (*)")
            if not source:
                return []
        src = Path(source)
        try:  # another experiment protected by a password asks for it
            apps = self.main.with_password(lambda pw: load_apparatus_file(source, pw), "Import apparatus",
                                           f"“{src.stem if src.is_dir() else src.parent.stem}”")
        except Exception as e:
            QMessageBox.warning(self, "Import apparatus", f"Cannot read apparatus from {source}:\n{e}")
            return []
        if apps is None:
            return []
        if names is None and len(apps) > 1:
            choices = ["All"] + [a.name for a in apps]
            item, ok = QInputDialog.getItem(self, "Import apparatus", "Apparatus to import:", choices, 0, False)
            if not ok:
                return []
            names = None if item == "All" else [item]
        if names is not None:
            apps = [a for a in apps if a.name in names]
        unit = self.project.distance_unit
        for a in apps:
            a.name = unique_name(a.name, self._names())
            a.distance_unit = unit  # one unit for the experiment
            self.project.apparatus.append(a)
        if apps:
            self.main.mark_dirty()
            self._refresh_app_list(select=apps[0])
            self.main.status(f"Imported {', '.join(a.name for a in apps)}.")
        return apps

    def export_apparatus(self, path: str | None = None) -> Path | None:
        app = self.app
        if app is None:
            return None
        if path is None:
            base = self.project.path.parent if self.project.path else Path.home()
            path, _ = QFileDialog.getSaveFileName(self, "Save apparatus to a file", str(base / f"{app.name}.json"),
                                                  "Apparatus file (*.json)")
            if not path:
                return None
        out = save_apparatus_file([app], path)
        self.main.status(f"Saved {app.name} to {out}")
        return out

    def lens_correction(self, result: dict | None = None, apply_to: str | None = None) -> int:
        """Lens distortion correction of video tests (a dialog on the background frame unless ``result`` — a
        LensCorrection dict, {} for none — and ``apply_to`` are given): "video" the tests that use the background
        video, "apparatus" the video tests of this apparatus, "all" every video test.  Returns how many tests were
        changed."""
        p, app = self.project, self.app
        if p is None:
            return 0
        bg_path = self.bg.path if self.bg.real else None
        groups = {"video": [t for t in p.tests if t.video and bg_path and same_video(p.abs_path(t.video), bg_path)],
                  "apparatus": [t for t in p.tests if t.video and app is not None and t.apparatus == app.name],
                  "all": [t for t in p.tests if t.video]}
        if not groups["all"]:
            QMessageBox.information(self, "Lens correction", "No test has a video yet. Lens correction applies to "
                                    "the videos of tests; for a camera, use Camera options on the Run tests page.")
            return 0
        if result is None:
            labels = {"video": "Tests that use this video", "apparatus": "Video tests of this apparatus",
                      "all": "All the tests with a video"}
            choices = [(f"{labels[k]} ({len(v)})", k) for k, v in groups.items() if v]
            current = self.bg.lens.to_dict() if self.bg.lens is not None else (
                next((t.undistort for t in groups[choices[0][1]] if t.undistort), {}))
            dlg = LensCorrectionDialog(self.bg.raw_frame if self.bg.real else None, current,
                                       video_path=bg_path or "", parent=self,
                                       title="Lens correction of the test videos", apply_choices=choices)
            accepted = dlg.exec() == QDialog.Accepted
            dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
            if not accepted:
                return 0
            result, apply_to = dlg.result(), dlg.apply_to()
        tests = groups.get(apply_to or "video") or []
        changed = [t for t in tests if (t.undistort or {}) != (result or {})]
        for t in changed:
            t.undistort = dict(result or {})
        if not changed:
            return 0
        self.main.mark_dirty()
        if self.bg.path:
            self.bg.load(self.bg.path, self.bg.time_spin.value(), quiet=True)
        tracked = [t for t in changed if p.has_track(t)]
        what = "removed" if not result else "set"
        msg = f"Lens correction {what} for {len(changed)} test{'s' if len(changed) != 1 else ''}."
        if tracked:
            msg += (f" {len(tracked)} of them {'were' if len(tracked) != 1 else 'was'} tracked without it: check the "
                    "apparatus map on the corrected image, then track them again.")
        self.main.status(msg)
        return len(changed)

    def export_map_image(self, path: str | None = None, background: bool | None = None) -> str | None:
        """Save the zone map as an image (PNG / SVG / PDF by extension); background: draw it over the background
        frame (by default when a video frame is shown)."""
        app = self.app
        if app is None:
            return None
        if path is None:
            base = self.project.path.parent if self.project.path else Path.home()
            path, _ = QFileDialog.getSaveFileName(self, "Export the zone map as an image",
                                                  str(base / f"{app.name} map.png"),
                                                  "PNG image (*.png);;SVG drawing (*.svg);;PDF document (*.pdf)")
            if not path:
                return None
        if background is None:
            background = self.bg.real
        frame = self.bg.frame if background and self.bg.real else None
        try:
            out = plots.save_zone_map(app, path, frame, labels=self.labels_act.isChecked())
        except Exception as e:
            error_box(self, "Export map image", e)
            return None
        self.main.status(f"Saved the map of {app.name} to {out}")
        return out

    def rename_apparatus(self, new_name: str | None = None) -> bool:
        app = self.app
        if app is None:
            return False
        if new_name is None:
            new_name, ok = QInputDialog.getText(self, "Rename apparatus", "Name:", QLineEdit.Normal, app.name)
            if not ok:
                return False
        if not self._rename_apparatus(app, new_name):
            return False
        self._refresh_app_list(select=app)
        return True

    def _rename_apparatus(self, app: Apparatus, new_name: str) -> bool:
        new_name = new_name.strip()
        if not new_name or new_name == app.name:
            return False
        if new_name in self._names(exclude=app):
            QMessageBox.warning(self, "Rename apparatus", f"An apparatus called “{new_name}” already exists.")
            return False
        old = app.name
        users = [t for t in self.project.tests if t.apparatus == old]
        for t in users:
            t.apparatus = new_name
        app.name = new_name
        self._rename_live_refs(old, new_name)
        self.main.mark_dirty()
        if users:
            self.main.status(f"Renamed “{old}” to “{new_name}” and updated {len(users)} test(s)")
        return True

    def delete_apparatus(self, confirm: bool = True) -> bool:
        app = self.app
        if app is None:
            return False
        p = self.project
        users = [t for t in p.tests if t.apparatus == app.name]
        others = [a for a in p.apparatus if a is not app]
        if confirm:
            msg = f"Delete the apparatus “{app.name}”?"
            if users:
                msg += (f"\n\n{len(users)} test(s) use it; they will be assigned to "
                        f"“{others[0].name}”." if others else
                        f"\n\n{len(users)} test(s) use it and will have no apparatus.")
            if QMessageBox.question(self, "Delete apparatus", msg) != QMessageBox.Yes:
                return False
        row = p.apparatus.index(app)
        for t in users:
            t.apparatus = others[0].name if others else ""
        self._rename_live_refs(app.name, "")  # test panels must choose another apparatus (never a silent one)
        p.apparatus.remove(app)
        self._undo.pop(id(app), None)
        self._redo.pop(id(app), None)
        self.bg.forget(app)
        self.main.mark_dirty()
        self._refresh_app_list(select_row=max(0, row - 1))
        return True

    def _rename_live_refs(self, old: str, new: str):
        """An apparatus was renamed (or deleted: `new` ""): the test panels of Run tests (several tests) and their
        saved layout follow, so no panel arms with another apparatus."""
        live = self.project.settings_extra.get("live") or {}
        for sd in (live.get("multi") or {}).get("sessions", []):
            if sd.get("apparatus") == old:
                sd["apparatus"] = new
        page = self.main.page("LivePage") if hasattr(self.main, "page") else None
        group = getattr(page, "group", None)
        for e in (group.entries if group is not None else ()):
            if e.meta.get("apparatus") == old:
                e.meta["apparatus"] = new
                if e.session is None or e.state == "finished":
                    e.apparatus = self.project.find_apparatus(new) if new else None
                relabel = getattr(page, "_relabel", None)
                if relabel is not None:
                    relabel(e)

    def refresh_info(self):
        """Title, template and calibration of the current apparatus."""
        app = self.app
        self.page_title.setText(app.name if app is not None else "Apparatus")
        self.panel.show_arena(app)
        self.panel.update_counts()
        if app is None:
            self.app_info.setText("No apparatus yet — choose Create from template (recommended) or New apparatus "
                                  "in the ribbon." if self.project else "")
            self.cal_label.setText("")
            return
        info = TEMPLATES.get(app.template)
        n = sum(1 for t in self.project.tests if t.apparatus == app.name)
        fs = f"{app.frame_size[0]}×{app.frame_size[1]} px" if app.frame_size else "frame size unknown"
        self.app_info.setText(f"{info.title if info else app.template} template · used by {n} test"
                              f"{'' if n == 1 else 's'} · {fs}")
        with loading(self):
            if app.px_per_cm:
                extra = ""
                if app.calibration_line and app.calibration_length_cm:
                    extra = f"ruler on a {app.length_text(app.calibration_length_cm)} line"
                elif app.calibration_length_cm:
                    extra = f"template, {app.length_text(app.calibration_length_cm)} wide"
                self.cal_label.setText(f"1 cm = {app.px_per_cm:.2f} px"
                                       + (f" <span style='color:{theme.MUTED}'>· {extra}</span>" if extra else ""))
                self.ppc_spin.setValue(app.px_per_cm)
                if app.report_unit != "cm":
                    self.cal_label.setText(self.cal_label.text() + f" <span style='color:{theme.MUTED}'>· "
                                           f"results in {app.report_unit}</span>")
            else:
                self.cal_label.setText(f"<span style='color:{theme.WARNING}'>Not calibrated</span> "
                                       f"<span style='color:{theme.MUTED}'>· results in pixels</span>")
                self.ppc_spin.setValue(0)
            self.unit_combo.setCurrentIndex(max(0, self.unit_combo.findData(app.distance_unit)))
            self.unit_combo.setEnabled(bool(app.px_per_cm))

    # ================================================================ map
    def _rebuild_map(self, keep_selection: bool = True):
        self.view.rebuild(keep_selection)
        self.show_sequence_overlay()

    def show_sequence_overlay(self):
        """Arrows of the sequence selected in the panel, while the Sequences tab is shown."""
        self.view.show_sequence(self.panel.shown_sequence() if self.app is not None else None)

    def _toggle_labels(self, on: bool):
        self.view.show_labels = on
        self._rebuild_map()

    def select_item(self, kind: str, index: int):
        self.view.select([(kind, index)], ensure_visible=True)
        self.panel.sync_with_map(self.view.selected_key())

    def select_all(self) -> int:
        """Select every zone, point and line of the map (not the arena boundary)."""
        self.set_tool("select")
        self.view.select([k for k in self.view.map_items if k[0] != "arena"])
        self.panel.sync_with_map(self.view.selected_key())
        return len(self.view.selected_keys())

    def set_tool(self, tool: str):
        if self.app is None and tool not in ("select", "template"):
            tool = "select"
        self.view.set_tool(tool)
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        else:
            for a in self.tool_actions.values():
                a.setChecked(False)
        self.hint.setText(ARENA_HINTS[self.arena_shape] if tool == "arena" else HINTS.get(tool, ""))
        self.view.setFocus()

    def set_arena_shape(self, shape: str):
        self.arena_shape = shape
        self.arena_actions[shape].setChecked(True)
        self.set_tool("arena")

    def show_context_menu(self, global_pos):
        """Right-click menu of the map (select tool)."""
        if self.locked:
            return
        m = QMenu(self)
        has_sel = self.view.selected_key() is not None
        for a, on in ((self.copy_act, has_sel), (self.paste_act, bool(ApparatusPage._clipboard)),
                      (self.delete_sel_act, has_sel)):
            m.addAction(a)
            a.setEnabled(on and self.app is not None)
        m.addSeparator()
        for a in (self.select_all_act, self.undo_act, self.redo_act, self.fit_act):
            m.addAction(a)
        m.exec(global_pos)
        for a in (self.copy_act, self.paste_act, self.delete_sel_act):
            a.setEnabled(self.app is not None)

    def show_measure(self, text: str):
        self.measure.setText(text)

    # ================================================================ editing
    @contextmanager
    def _edit(self, select: tuple[str, int] | None = None, after=None, merge=None):
        """An undoable change of the current apparatus: saves an undo step (see push_undo for `merge`), then marks
        the project changed and refreshes — with `after()` if given, else the whole map and panel, selecting
        `select` on the map."""
        self.push_undo(merge=merge)
        yield
        self.main.mark_dirty()
        if after is not None:
            after()
        else:
            self._model_changed(select)

    def _obj(self, kind: str, index: int):
        """The index-th zone / point / line / group / sequence of the current apparatus, or None."""
        items = getattr(self.app, kind + "s") if self.app is not None else []
        return items[index] if 0 <= index < len(items) else None

    def _model_changed(self, select: tuple[str, int] | None = None):
        """The apparatus changed structurally: redraw the map and the panel."""
        self._rebuild_map(keep_selection=select is None)
        if select is not None:
            self.view.select([select])
        self.panel.refresh()
        self.refresh_info()

    def _item_changed(self, kind: str, index: int):
        """Properties of one object changed: redraw it and show them."""
        it = self.view.item(kind, index)
        if it is not None:
            it.sync()
        self.panel.load_editor()

    def geometry_edited(self):
        """A vertex drag or other geometry change of a map item (already saved for undo)."""
        self.main.mark_dirty()
        self.panel.load_editor()
        self.refresh_info()

    # ---- creation (called by the view's tools) ----------------------------------
    def finish_drag(self, x0, y0, x1, y1, square: bool = False):
        """A drag of the current tool finished: create the corresponding item."""
        tool = self.view.tool
        if tool == "template":
            spec = self.view.template
            self.set_tool("select")
            if spec:
                if square or spec.get("square"):
                    p1 = EditorView.constrain(QPointF(x0, y0), QPointF(x1, y1), True)
                    x1, y1 = p1.x(), p1.y()
                self.apply_template(spec["key"], min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0),
                                    spec["params"], spec["name"], spec["replace"])
            return
        if tool == "line":
            self.add_line(x0, y0, x1, y1)
            return
        if tool == "calibrate":
            self.set_tool("select")
            self.calibrate_from_line(x0, y0, x1, y1)
            return
        if square:
            p1 = EditorView.constrain(QPointF(x0, y0), QPointF(x1, y1), True)
            x1, y1 = p1.x(), p1.y()
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        if self.view.drag_kind() == "ellipse":
            shape = Ellipse((xa + xb) / 2, (ya + yb) / 2, (xb - xa) / 2, (yb - ya) / 2)
        else:
            shape = Polygon([(xa, ya), (xb, ya), (xb, yb), (xa, yb)])
        if tool == "arena":
            self.set_arena(shape)
        else:
            self.add_zone(shape)

    def finish_polygon(self, points):
        shape = Polygon([(float(x), float(y)) for x, y in points])
        if self.view.tool == "arena":
            self.set_arena(shape)
        else:
            self.add_zone(shape)

    def _add(self, kind: str, make, name: str | None):
        """Append a new object made by make(name, count) — its name made unique, by default "<Kind> <n>" — and
        select it."""
        app = self.app
        if app is None:
            return None
        items = getattr(app, kind + "s")
        taken = app.names() if kind in ("zone", "group") else [x.name for x in items]  # zones and groups share names
        obj = make(unique_name(name or f"{kind.capitalize()} {len(items) + 1}", taken), len(items))

        def show_in_panel():
            self._model_changed()
            self.panel.select(kind, len(items) - 1)
        with self._edit(select=(kind, len(items)), after=None if kind in MAP_KINDS else show_in_panel):
            items.append(obj)
        return obj

    def add_zone(self, shape: Shape, name: str | None = None, color: str | None = None) -> Zone | None:
        return self._add("zone", lambda n, k: Zone(n, shape, color or PALETTE[k % len(PALETTE)]), name)

    def add_point(self, x: float, y: float, name: str | None = None) -> PointOfInterest | None:
        return self._add("point", lambda n, k: PointOfInterest(n, float(x), float(y),
                                                               color=PALETTE[(3 + k) % len(PALETTE)]), name)

    def add_line(self, x1, y1, x2, y2, name: str | None = None) -> Line | None:
        return self._add("line", lambda n, k: Line(n, float(x1), float(y1), float(x2), float(y2),
                                                   PALETTE[(2 + k) % len(PALETTE)]), name)

    def add_group(self, name: str | None = None, zones=(), exclude=()) -> ZoneGroup | None:
        return self._add("group", lambda n, k: ZoneGroup(n, list(zones), list(exclude)), name)

    def add_sequence(self, name: str | None = None, steps=()) -> Sequence | None:
        return self._add("sequence", lambda n, k: Sequence(n, [s for s in steps if s in self.app.names()]), name)

    def set_arena(self, shape: Shape):
        if self.app is None:
            return
        with self._edit(select=("arena", 0)):
            self.app.arena = shape
        self.set_tool("select")
        self.main.status("Arena boundary set")

    def calibrate_from_line(self, x1, y1, x2, y2, length_cm: float | None = None, unit: str | None = None) -> bool:
        """Calibrate the apparatus with a ruler line whose real length is `length_cm` (asked for, in mm, cm or m,
        when not given). The unit chosen in the dialog (or `unit`) becomes the unit the results are reported in."""
        app = self.app
        px = math.hypot(x2 - x1, y2 - y1)
        if app is None or px < 1:
            return False
        if length_cm is None:
            dlg = CalibrationDialog(px, app.calibration_length_cm or 10.0, unit or self.project.distance_unit, self)
            if dlg.exec() != QDialog.Accepted:
                return False
            length_cm, unit = dlg.length_cm(), dlg.unit()
        try:
            with self._edit():
                app.calibrate(float(x1), float(y1), float(x2), float(y2), float(length_cm))
        except ValueError as e:
            QMessageBox.warning(self, "Calibrate", str(e))
            return False
        if unit and unit != self.project.distance_unit:
            self.set_distance_unit(unit)
        self.main.status(f"Calibrated: 1 cm = {app.px_per_cm:.2f} px")
        return True

    def set_distance_unit(self, unit: str):
        """Report distances in `unit` for every apparatus of the experiment."""
        if self.project is None or unit == self.project.distance_unit and \
                all(a.distance_unit == unit for a in self.project.apparatus):
            return
        self.project.set_distance_unit(unit)
        self.main.mark_dirty()
        self.refresh_info()
        self.main.status(f"Distances are reported in {unit} (speeds in {unit}/s) for every apparatus.")

    def _unit_chosen(self):
        if not self._loading and self.unit_combo.currentData():
            self.set_distance_unit(self.unit_combo.currentData())

    def set_px_per_cm(self, v: float):
        app = self.app
        v = float(v) or None
        if app is None or v == app.px_per_cm:
            return
        with self._edit():
            app.px_per_cm = v
            app.calibration_line = None
            app.calibration_length_cm = None

    def _ppc_edited(self):
        """editingFinished also comes when the box just loses the focus: only a value that differs from the one
        shown (the calibration rounded to the box's decimals) is a new calibration — else the ruler is kept."""
        app = self.app
        if self._loading or app is None:
            return
        v = self.ppc_spin.value()
        if abs(round(app.px_per_cm or 0.0, self.ppc_spin.decimals()) - v) < 1e-9:
            return
        self.set_px_per_cm(v)

    def clear_calibration(self):
        self.set_px_per_cm(0)

    # ---- changes ---------------------------------------------------------------------
    def commit_moves(self):
        """Objects dragged on the map: store their new positions."""
        moved = [it for it in self.view.map_items.values() if it.moved()]
        if moved:
            with self._edit(after=self.geometry_edited):
                for it in moved:
                    it.commit_pos()

    def delete_selected(self) -> bool:
        keys = self.view.selected_keys()
        if not keys or self.app is None:
            return False
        with self._edit():
            for kind, idx in sorted(keys, key=lambda k: -k[1]):
                self.app.remove(kind, idx)
        return True

    def delete_item(self, kind: str, index: int = 0) -> bool:
        """Delete the arena or a zone / point / line / group / sequence (and the references to it)."""
        if kind == "arena":
            exists = self.app is not None and self.app.arena is not None
        else:
            exists = self._obj(kind, index) is not None
        if not exists:
            return False
        with self._edit():
            self.app.remove(kind, index)
        return True

    def duplicate_zone(self, index: int) -> Zone | None:
        z = self._obj("zone", index)
        return self.add_zone(z.shape.translated(10.0, 10.0), f"{z.name} copy", z.color) if z else None

    def zone_to_arena(self, index: int):
        z = self._obj("zone", index)
        if z is not None:
            with self._edit(select=("zone", index)):
                self.app.arena = shape_from_dict(z.shape.to_dict())

    def rename(self, kind: str, index: int, name: str) -> bool:
        """Rename a zone / point / line / group / sequence; groups, sequences, grids and the per-test positions
        of moveable zones follow. Names are made unique."""
        m, name = self._obj(kind, index), name.strip()
        if m is None or not name or name == m.name:
            return False
        with self._edit(select=(kind, index) if kind in MAP_KINDS else None):
            new = self.project.rename_in_apparatus(self.app, kind, index, name)
        if new != name:
            self.main.status(f"“{name}” is already used; renamed to “{new}”")
        return True

    def set_item_color(self, kind: str, index: int, color: str):
        m = self._obj(kind, index)
        if m is not None:
            with self._edit(select=(kind, index)):
                m.color = color

    def set_property(self, kind: str, index: int, **values) -> bool:
        """Set attributes of a zone, point or sequence (e.g. hidden=True). Steps of a number (spin boxes) make one
        undo step."""
        m = self._obj(kind, index)
        if m is None or all(getattr(m, k) == v for k, v in values.items()):
            return False
        numeric = all(isinstance(v, float) for v in values.values())
        after = self.show_sequence_overlay if kind == "sequence" else lambda: self._item_changed(kind, index)
        with self._edit(after=after, merge=(kind, index, *values) if numeric else None):
            for k, v in values.items():
                setattr(m, k, v)
        return True

    # ---- groups and sequences -------------------------------------------------------------
    def set_group_members(self, index: int, zones, exclude=()):
        g = self._obj("group", index)
        if g is None:
            return
        order = [z.name for z in self.app.zones]
        with self._edit(after=self.panel.load_group_editor):
            g.zones = [n for n in order if n in set(zones)]
            g.exclude = [n for n in order if n in set(exclude)]

    def _edit_steps(self, index: int, fn, select: int) -> bool:
        q = self._obj("sequence", index)
        new = fn(list(q.steps)) if q is not None else None
        if new is None or new == q.steps:
            return False

        def after():
            self.panel.load_seq_editor(step=select)
            self.show_sequence_overlay()
        with self._edit(after=after):
            q.steps = new
        return True

    def add_sequence_step(self, index: int, zone: str) -> bool:
        q = self._obj("sequence", index)
        return q is not None and zone in self.app.names() and self._edit_steps(index, lambda st: st + [zone],
                                                                               len(q.steps))

    def remove_sequence_step(self, index: int, step: int) -> bool:
        return self._edit_steps(index, lambda st: st[:step] + st[step + 1:] if 0 <= step < len(st) else None,
                                max(0, step - 1))

    def move_sequence_step(self, index: int, step: int, delta: int) -> bool:
        def mv(st):
            j = step + delta
            if not (0 <= step < len(st) and 0 <= j < len(st)):
                return None
            st[step], st[j] = st[j], st[step]
            return st
        return self._edit_steps(index, mv, step + delta)

    # ---- grids ---------------------------------------------------------------------------
    def add_grid_dialog(self):
        app = self.app
        if app is None:
            return None
        key = self.view.selected_key()
        dlg = GridDialog(self, app, has_selection=key is not None and key[0] == "zone")
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return None
        spec = dlg.spec()
        return self.add_grid(spec["kind"], spec["region"], spec["name"], spec["group"], **spec["params"])

    def add_grid(self, kind: str = "square", region="arena", name: str = "Grid", group: bool = True, **params):
        """Add a grid of zones covering the arena ("arena"), the selected zone ("zone"), the whole image
        ("frame") or a given shape."""
        app = self.app
        if app is None:
            return None
        key = self.view.selected_key()
        if isinstance(region, Shape):
            shp = region
        elif region == "zone" and key and key[0] == "zone":
            shp = app.zones[key[1]].shape
        elif region == "frame" or (region == "arena" and app.arena is None and not app.zones):
            w, h = self.view.frame_size or app.frame_size or (640, 480)
            shp = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
        else:
            shp = app.arena_or_bounds()
        try:
            with self._edit():
                g = make_grid(app, kind, shp, name or "Grid", group=group, **params)
        except ValueError as e:
            QMessageBox.warning(self, "Add grid", str(e))
            return None
        self.main.status(f"Added grid “{g.name}” with {len(g.zones)} zones")
        return g

    def delete_grid_of(self, index: int) -> bool:
        z = self._obj("zone", index)
        g = next((g for g in self.app.grids if z.name in g.zones), None) if z is not None else None
        if g is None:
            return False
        with self._edit():
            remove_grid(self.app, g.name)
        return True

    # ---- copy / paste --------------------------------------------------------------------
    def copy_selected(self) -> int:
        app = self.app
        keys = [k for k in self.view.selected_keys() if k[0] in MAP_KINDS]
        if app is None or not keys:
            return 0
        clip = {"zones": [], "points": [], "lines": [], "source": id(app)}
        for kind, i in sorted(keys):
            clip[kind + "s"].append(getattr(app, kind + "s")[i].to_dict())
        ApparatusPage._clipboard = clip
        self.main.status(f"Copied {len(keys)} item(s)")
        return len(keys)

    def paste(self) -> int:
        """Paste copied zones / points / lines (offset when pasted into the apparatus they came from)."""
        app = self.app
        clip = ApparatusPage._clipboard
        if app is None or not clip:
            return 0
        off = 12.0 * (clip.setdefault("pasted", {}).get(id(app), 0) + (1 if clip.get("source") == id(app) else 0))
        new_keys = []
        with self._edit(after=lambda: self._model_changed(select=new_keys[0] if new_keys else None)):
            for d in clip["zones"]:
                z = Zone.from_dict(d)
                z.shape = z.shape.translated(off, off)
                z.name = unique_name(z.name if z.name not in app.names() else f"{z.name} copy", app.names())
                app.zones.append(z)
                new_keys.append(("zone", len(app.zones) - 1))
            for d in clip["points"]:
                p = PointOfInterest.from_dict(d)
                p.x, p.y = p.x + off, p.y + off
                p.name = unique_name(p.name, [q.name for q in app.points])
                app.points.append(p)
                new_keys.append(("point", len(app.points) - 1))
            for d in clip["lines"]:
                ln = Line.from_dict(d)
                ln.x1, ln.y1, ln.x2, ln.y2 = ln.x1 + off, ln.y1 + off, ln.x2 + off, ln.y2 + off
                ln.name = unique_name(ln.name, [q.name for q in app.lines])
                app.lines.append(ln)
                new_keys.append(("line", len(app.lines) - 1))
        self.view.select(new_keys)
        clip["pasted"][id(app)] = clip["pasted"].get(id(app), 0) + 1  # pasting again offsets further
        self.main.status(f"Pasted {len(new_keys)} item(s)")
        return len(new_keys)

    # ================================================================== undo
    def push_undo(self, before: dict | None = None, merge=None):
        """Save the apparatus (or its state `before` an edit already made) for undo. Consecutive edits with the
        same `merge` key (e.g. spin box steps of one field) make a single undo step."""
        app = self.app
        if app is None:
            return
        if merge is not None and merge == self._undo_merge:
            return
        self._undo_merge = merge
        st = self._undo.setdefault(id(app), [])
        d = self._state(app, before)
        if not st or st[-1] != d:
            st.append(d)
            del st[:-200]
        self._redo[id(app)] = []

    def undo(self) -> bool:
        return self._undo_redo(self._undo, self._redo, "Undo")

    def redo(self) -> bool:
        return self._undo_redo(self._redo, self._undo, "Redo")

    def _undo_redo(self, src, dst, what) -> bool:
        app = self.app
        if app is None or self.view.drawing:
            if app is not None:
                self.view.cancel_drawing()
            return False
        st = src.get(id(app), [])
        cur = self._state(app)
        while st and st[-1] == cur:
            st.pop()
        if not st:
            self.main.status(f"Nothing to {what.lower()}")
            return False
        dst.setdefault(id(app), []).append(cur)
        self._undo_merge = None
        old = st.pop()
        self._replace(app, Apparatus.from_dict(old["app"]))
        for t in self.project.tests_using(app.name):  # e.g. a zone rename moved them to the new name
            t.zone_overrides = copy.deepcopy(old["overrides"].get(t.id, {}))
        self.main.mark_dirty()
        self._model_changed()
        return True

    def _state(self, app: Apparatus, before: dict | None = None) -> dict:
        """An undo step: the apparatus (or its state `before` an edit) and the per-test positions of its moveable
        zones (Test.zone_overrides, keyed by zone name: a rename moves them)."""
        return {"app": before if before is not None else app.to_dict(),
                "overrides": {t.id: copy.deepcopy(t.zone_overrides) for t in self.project.tests_using(app.name)
                              if t.zone_overrides}}

    @staticmethod
    def _replace(app: Apparatus, new: Apparatus):
        """Give `app` the contents of `new`, keeping its name (tests refer to it)."""
        name = app.name
        app.__dict__.update(new.__dict__)
        app.name = name

    # ============================================================= templates
    def create_from_template(self):
        if self.project is None:
            return
        cur = self.app
        key = cur.template if cur is not None and cur.template in TEMPLATES and cur.template != "custom" \
            else self.project.protocol
        dlg = TemplateDialog(self, key, cur.name if cur else None, self._names(), self.bg.real)
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return
        spec = {"key": dlg.key(), "params": dlg.params(), "name": dlg.name(), "replace": dlg.is_replace(),
                "square": dlg.square.isChecked()}
        if dlg.placement() == "fit":
            w, h = self.view.frame_size or (640, 480)
            x, y = 0.0, 0.0
            if spec["square"]:
                s = min(w, h)
                x, y, w, h = (w - s) / 2, (h - s) / 2, s, s
            return self.apply_template(spec["key"], x, y, w, h, spec["params"], spec["name"], spec["replace"])
        self.start_template_placement(spec)

    def start_template_placement(self, spec: dict):
        self.view.template = spec
        self.set_tool("template")
        self.main.status("Drag a rectangle around the apparatus on the image (Esc cancels)", 15000)

    def apply_template(self, key: str, x, y, w, h, params: dict | None = None, name: str | None = None,
                       replace: bool = False) -> Apparatus:
        """Build a template into the bounding box (x, y, w, h); replace the current apparatus or add a new one.
        Templates with several arenas (e.g. multi-well plates) give one apparatus per arena; the first one
        replaces the current apparatus if asked."""
        built = templates.build_many(key, float(x), float(y), float(w), float(h), **(params or {}))
        cur = self.app
        fs = (cur.frame_size if cur else None) or self.bg.real_frame_size()
        multi = len(built) > 1
        prefix = (name + " ") if multi and name and name != TEMPLATES[key].title and not replace else ""
        if replace and cur is not None:
            self.push_undo()
        first, unit = None, self.project.distance_unit
        for i, a in enumerate(built):
            a.frame_size, a.distance_unit = fs, unit
            a.name = prefix + a.name
            if i == 0 and replace and cur is not None:
                self._replace(cur, a)
                if not multi and name and name != cur.name:
                    self._rename_apparatus(cur, name)
                first = cur
                continue
            a.name = unique_name(a.name if multi else name or a.name, self._names())
            self.bg.inherit(cur, a)
            self.project.apparatus.append(a)
            first = first or a
        self.main.mark_dirty()
        self._refresh_app_list(select=first)
        if multi:
            self.main.status(f"Created {len(built)} apparatus from the {TEMPLATES[key].title} template "
                             "(one per arena; tests on the same video are tracked together)")
        else:
            self.main.status(f"Created “{first.name}” from the {TEMPLATES[key].title} template — adjust zones as "
                             "needed")
        return first
