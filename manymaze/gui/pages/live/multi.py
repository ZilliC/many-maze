"""Several tests at once: a LiveGroup of camera / video sources and test panels; in an Input/output only protocol,
test panels without a camera (e.g. operant chambers side by side), each with its own I/O device."""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

import cv2
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QTableWidgetItem

from ....core.camera import CameraView, SourceSpec, camera_settings, set_camera_settings
from ....core.camhw import CameraHardware
from ....core.iodevices import DeviceView
from ....core.livegroup import SHARED_DEVICE_TYPES, device_plan
from ....core.livemonitor import io_panel_lines
from ....core.procedures import Outputs
from ....core.video import VIDEO_EXTENSIONS
from ....core.workflow import confirm_id_enabled
from ...confirm_id import confirm_animal_id
from ...icons import icon
from ...live_widgets import CameraOptionsDialog, TestPanel, short_time
from ...widgets import fmt_time
from .common import peek_frame


class MultiTestMixin:
    """Several tests at once: sources, test panels (table, editor, grid), cameras, arming and control of the
    LiveGroup, saving the finished tests."""

    def add_source(self, source, second=None, layout: str = "side") -> str:
        """Add a camera index, a native camera id or a video file (simulated camera) to the multi-test sources;
        returns its key."""
        spec = SourceSpec(source)
        d = camera_settings(self.project, spec.key)
        spec.view = CameraView.from_dict(d.get("view"))
        spec.second = second if second is not None else d.get("second")
        spec.layout = d.get("layout", layout) if second is None else layout
        spec.hardware = CameraHardware.from_dict(d.get("hardware")) if not spec.is_file else CameraHardware()
        if not spec.is_file:
            spec.size = self.resolution.currentData()
            spec.fps = self.cam_fps.value() or None
        key = self.group.add_source(spec)
        self._save_group_layout()
        self._refresh_row_choices()
        if self.group.runners:
            self.start_cameras()
        self._sync_panels()
        self.main.status(f"Added {spec.label}. Add a test panel for every apparatus it shows.")
        return key

    def _add_file_source(self):
        start = str(self.project.path) if self.project and self.project.path else str(Path.home())
        exts = " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Video file to simulate a camera", start,
                                              f"Videos ({exts});;All files (*)")
        if path:
            self.add_source(path)

    def add_session_row(self, source_key: str | None = None, apparatus: str | None = None,
                        animal: str | None = None, stage: str | None = None, trial: int | None = None,
                        device: str | None = None):
        """Add a test panel (source × apparatus × animal / stage / trial) to the session.  In an Input/output only
        protocol the panel has no camera and uses an I/O device of its own (by default the first one no other
        panel uses, e.g. the next operant chamber); its apparatus is optional."""
        p = self.project
        if p is None:
            return None
        io = self.io_only
        if io:
            source_key = None
        elif not self.group.sources:
            QMessageBox.information(self, "Run tests", "Add a camera or a video file first (Add camera / video "
                                    "source).")
            return None
        else:
            keys = list(self.group.sources)
            source_key = source_key if source_key in self.group.sources else self._selected_source() or keys[0]
        used = {e.meta.get("apparatus") for e in self.group.entries_for(source_key)}
        if apparatus is None:
            apparatus = next((a.name for a in p.apparatus if a.name not in used),
                             "" if io else p.apparatus[0].name if p.apparatus else "")
        ids = [a.id for a in p.animals]
        taken = {e.meta.get("animal") for e in self.group.entries}
        if animal is None:
            animal = next((a for a in ids if a not in taken), ids[0] if ids else f"A{len(self.group.entries) + 1}")
        meta = {"apparatus": apparatus, "animal": animal,
                "stage": stage if stage is not None else (p.stages[0] if p.stages else ""),
                "trial": trial or 1, "test_id": None}
        if io or device is not None:
            meta["device"] = self._free_device() if device is None else device
        e = self.group.add_entry(source_key, p.find_apparatus(apparatus) if apparatus else None, "", meta)
        self._relabel(e)
        self._rebuild_session_table()
        self._save_group_layout()
        self.sess_table.selectRow(len(self._entries()) - 1)
        return e

    def remove_session_row(self):
        e = self._selected_entry()
        if e is None:
            return
        if e.state in ("waiting", "running", "paused"):
            QMessageBox.information(self, "Run tests", "Stop this test before removing it.")
            return
        self._cancel_pending([e])
        self.group.remove(e)
        keep = {x.source_key for x in self.group.entries}
        for k in [k for k in self.group.sources if k not in keep]:  # unused sources go too
            self.group.stop_sources([k])
            self.group.sources.pop(k, None)
        self._rebuild_session_table()
        self._save_group_layout()
        self._update_buttons()

    @staticmethod
    def _place(e) -> str:
        """Where the test of a panel runs: its apparatus, or (a panel without a camera) its I/O device."""
        m = e.meta
        dev = m.get("device") if e.source_key is None and m.get("device") != "-" else ""
        return m.get("apparatus") or dev or "?"

    def _relabel(self, e):
        e.label = f"{e.meta.get('animal') or '?'} · {self._place(e)}"

    def _entries(self) -> list:
        """The test panels: those of the camera sources or, in an Input/output only protocol, those without a camera
        (the other layout is kept for when the protocol's mode changes back); running tests are always listed."""
        io = self.io_only
        return [x for x in self.group.entries
                if (x.source_key is None) == io or x.state in ("waiting", "running", "paused")]

    def _free_device(self) -> str:
        """The first I/O device (box, chamber) that no panel without a camera uses yet ("" when every one is)."""
        p = self.project
        used = {e.meta.get("device") for e in self.group.entries if e.source_key is None}
        names = [str(c["name"]) for c in (p.io_devices if p else []) if c.get("enabled", True) and c.get("name")
                 and c.get("type", "virtual") not in SHARED_DEVICE_TYPES]
        return next((n for n in names if n not in used), "")

    def _entry_short(self, e) -> str:
        return f"{self._place(e)}: {e.meta.get('animal') or '?'}"

    def _rebuild_session_table(self):
        t = self.sess_table
        cur = self._selected_entry()
        t.blockSignals(True)
        t.setRowCount(0)
        rows = self._entries()
        for e in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c in range(7):
                t.setItem(r, c, QTableWidgetItem(""))
            self._fill_row(r, e)
        if rows:
            t.setCurrentCell(rows.index(cur) if cur in rows else 0, 0)
        t.blockSignals(False)
        self._load_row_editor()
        self._sync_panels()
        self._update_row_states()

    def _fill_row(self, r: int, e):
        spec = self.group.sources.get(e.source_key)
        m = e.meta
        dev = m.get("device") or ""
        src = spec.label if spec is not None else {"": "All devices", "-": "Simulated"}.get(dev, dev) \
            if e.source_key is None else "?"
        for c, txt in enumerate((src, m.get("apparatus", ""), m.get("animal", ""), m.get("stage", ""),
                                 str(m.get("trial", 1)))):
            it = self.sess_table.item(r, c)
            if it is not None and it.text() != txt:
                it.setText(txt)
                it.setToolTip(txt)

    def _load_row_editor(self):
        """Show the selected panel's camera / apparatus / animal / stage / trial in the editor below the table."""
        e = self._selected_entry()
        p = self.project
        eds = (self.row_source, self.row_apparatus, self.row_animal, self.row_stage, self.row_trial, self.row_device)
        for w in eds:
            w.blockSignals(True)
        try:
            self.row_source.clear()
            for k, spec in self.group.sources.items():
                self.row_source.addItem(spec.label, k)
            self.row_apparatus.clear()
            if e is not None and e.source_key is None:
                self.row_apparatus.addItem("None", "")  # without a camera the apparatus is optional
            for a in (p.apparatus if p else []):
                self.row_apparatus.addItem(a.name, a.name)
            self.row_animal.clear()
            self.row_animal.addItems([a.id for a in p.animals] if p else [])
            self.row_stage.clear()
            self.row_stage.addItems(p.stages if p else [])
            self.row_device.clear()
            self.row_device.addItem("Automatic (only test running)", "")
            for c in (p.io_devices if p else []):
                if c.get("enabled", True) and c.get("type", "virtual") != "audio" and c.get("name"):
                    self.row_device.addItem(f"{c['name']} ({c.get('type', 'virtual')})", str(c["name"]))
            self.row_device.addItem("None (simulated outputs)", "-")
            if e is not None:
                dv = e.meta.get("device", "") or ""
                i = self.row_device.findData(dv)
                if i < 0:  # a device that is no longer configured: kept, shown as missing
                    self.row_device.addItem(f"{dv} (not configured)", dv)
                    i = self.row_device.count() - 1
                self.row_device.setCurrentIndex(i)
                self.row_source.setCurrentIndex(max(0, self.row_source.findData(e.source_key)))
                self.row_apparatus.setCurrentIndex(self.row_apparatus.findData(e.meta.get("apparatus", "")))
                self.row_animal.setCurrentText(e.meta.get("animal", ""))
                self.row_stage.setCurrentText(e.meta.get("stage", ""))
                self.row_trial.setValue(int(e.meta.get("trial", 1)))
        finally:
            for w in eds:
                w.blockSignals(False)
        self.row_form.setRowVisible(self.row_source, e.source_key is not None if e is not None else not self.io_only)
        self.row_editor.setEnabled(e is not None and e.state not in ("waiting", "running", "paused"))

    def _refresh_row_choices(self):
        if self.mode == "multi" or self.group.entries:
            self._rebuild_session_table()

    def _row_editor_changed(self, *_):
        e = self._selected_entry()
        if e is None or e.state in ("waiting", "running", "paused"):
            return
        if e.source_key is not None:  # (a panel without a camera keeps none)
            e.source_key = self.row_source.currentData() or e.source_key
        e.meta.update(apparatus=self.row_apparatus.currentData() or "", animal=self.row_animal.currentText().strip(),
                      stage=self.row_stage.currentText().strip(), trial=self.row_trial.value(), test_id=None,
                      device=self.row_device.currentData() or "")
        e.apparatus = self.project.find_apparatus(e.meta["apparatus"]) if self.project else None
        self._relabel(e)
        self._fill_row(self._entries().index(e), e)
        self._save_group_layout()
        self._sync_panels()

    def _selected_entry(self):
        r = self.sess_table.currentRow()
        rows = self._entries()
        return rows[r] if 0 <= r < len(rows) else None

    def _selected_source(self):
        e = self._selected_entry()
        return e.source_key if e is not None else None

    def _selection_changed(self):
        e = self._selected_entry()
        self._load_row_editor()
        if e is not None:
            self.mosaic.set_current(e.id)
            try:
                self.main.select_explorer(self, e.id)
            except Exception:  # pragma: no cover
                pass
        self._refresh_monitor(force=True)
        self._update_buttons()

    def _panel_clicked(self, entry_id: int):
        for i, e in enumerate(self._entries()):
            if e.id == entry_id:
                if self.sess_table.currentRow() != i:
                    self.sess_table.selectRow(i)
                else:
                    self._selection_changed()
                return

    # ---- the test panels of the session
    def _new_panel(self, e) -> TestPanel:
        p = TestPanel(single=False)
        eid = e.id
        ent = lambda: self.group.entry(eid)  # noqa: E731 - the entry object may be replaced by a new session
        p.clicked.connect(lambda: self._panel_clicked(eid))
        p.start_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "start"))
        p.start_now.connect(lambda: ent() is not None and self._start_entry_now(ent()))
        p.arm_clicked.connect(lambda: ent() is not None and ent().state in ("idle", "finished")
                              and self.arm_all([ent()]))
        p.pause_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "pause"))
        p.stop_clicked.connect(lambda: ent() is not None and self.row_action(ent(), "stop"))
        p.undo_clicked.connect(lambda: ent() is not None and self.undo_last_event(ent().session))
        if e.source_key is not None:
            p.menu.addAction(icon("settings"), "Camera options…",
                             lambda: (self._panel_clicked(eid), self.multi_camera_options()))
        p.menu.addAction(icon("delete"), "Remove this test panel",
                         lambda: (self._panel_clicked(eid), self.remove_session_row()))
        p.menu.addSeparator()
        p.menu.addAction(self.panel_settings_act)
        if e.source_key is None:  # no camera: the panel shows the I/O instead, its Zones tab the inputs
            p.view.set_message("Input/output only: press ▶ to start the test (no camera).")
            p.tabs.setTabText(2, "Inputs")
            p.zones.setHorizontalHeaderLabels(["Input", "Time on (s)", "Activations", "Latency (s)"])
        else:
            p.view.set_message("Turn on the camera image (Show camera image) or press ▶ to start the test.")
        self._apply_prefs_to_panel(p)
        return p

    def _focus_rect(self, e) -> QRectF | None:
        """Several apparatus in one camera image: each panel shows its own apparatus."""
        if e.apparatus is None or e.source_key is None or len(self.group.entries_for(e.source_key)) < 2:
            return None
        try:
            x0, y0, x1, y1 = e.apparatus.arena_or_bounds().bounds()
        except Exception:
            return None
        m = 0.05 * max(x1 - x0, y1 - y0) + 4
        return QRectF(x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)

    def _sync_panels(self):
        """One panel per test of the session, in the order of the table."""
        panels = {}
        for e in self._entries():
            p = self._panels.get(e.id) or self._new_panel(e)
            panels[e.id] = p
            spec = self.group.sources.get(e.source_key)
            if spec is not None:
                src = str(spec.source) if spec.is_file else spec.label
                p.set_source(src, src)
            elif e.source_key is None:
                dev = e.meta.get("device") or ""
                p.set_source("No camera · " + {"": "all I/O devices", "-": "simulated I/O"}.get(dev, dev))
            p.view.set_apparatus(e.session.apparatus if e.session is not None and
                                 e.session.apparatus is not None else e.apparatus)
            p.view.set_focus(self._focus_rect(e))
        self._panels = panels
        self.mosaic.set_panels(panels)
        cur = self._selected_entry()
        if cur is not None:
            self.mosaic.set_current(cur.id)
        self._update_panels()
        self._refresh_explorer()

    def _panel_title(self, e) -> str:
        m = e.meta
        stage = m.get("stage") or ""
        title = (f"{m.get('apparatus') or '?'}: Animal {m.get('animal') or '?'}, {(stage + ' ') if stage else ''}"
                 f"trial {m.get('trial', 1)}")
        if e.session is not None:
            title += f" - {short_time(e.elapsed if e.state != 'waiting' else 0.0)}"
        return title

    def _update_panels(self):
        """State, time, statistics and zones of every panel (≤ 5 Hz)."""
        for e in self._entries():
            p = self._panels.get(e.id)
            if p is None:
                continue
            s = e.session
            st = e.state
            title = self._panel_title(e)
            if p.title.text() != title:
                p.set_title(title)
            if s is None:
                p.set_state("idle")
                p.reset_values()
            else:
                p.set_state(st, e.elapsed if st != "waiting" else 0.0, s.duration_s or 0.0)
                stats = s.stats
                if getattr(s, "io_only", False):
                    self._update_io_panel(p, s)
                elif stats is not None and st in ("running", "paused", "finished"):
                    with s.lock:
                        zones = stats.current_zones() if stats.detected else []
                        rows = stats.rows() if p.stack.currentIndex() == 2 else None
                        dist, unit = stats.distance, stats.unit
                    inner = [z for z in zones if z != "Arena"] or zones
                    p.vals["zone"].setText(", ".join(inner) if inner else ("—" if stats.detected else
                                                                           "not detected"))
                    p.vals["distance"].setText(f"{dist:.1f} {unit}")
                    p.view.set_active_zones(zones if st != "finished" else [])
                    if rows is not None:
                        p.set_zone_rows(rows, zones)
                p.vals["events"].setText(str(len(s.events)))
            active = st in ("waiting", "running", "paused")
            wend = bool(getattr(s, "waiting_end", False)) if s is not None else False
            p.start_btn.setEnabled((st != "running" or wend) and self.project is not None)
            p.start_btn.setText("Continue test" if wend else "Start now" if st == "waiting" else
                                "Resume" if st == "paused" else "Arm / Start test")
            p.start_btn.setToolTip(p.start_btn.text())
            p.pause_btn.setEnabled(st in ("running", "paused"))
            p.pause_btn.setText("Resume" if st == "paused" else "Pause")
            p.pause_btn.setIcon(icon("resume" if st == "paused" else "pause"))
            p.pause_btn.setToolTip(p.pause_btn.text())
            p.stop_btn.setEnabled(active)
            p.undo_btn.setEnabled(self._can_undo(s))
            p.set_recording(st in ("running", "paused") and bool(e.meta.get("record_path")), e.meta.get("record_path"))

    @staticmethod
    def _update_io_panel(p, s):
        """A test without a camera: its panel shows the states of its inputs and outputs instead of an image, and
        its Inputs tab the inputs' activations, time on and latency."""
        p.vals["zone"].setText("—")
        p.vals["distance"].setText("—")
        try:
            status = list(s.devices.status()) if s.devices is not None else []
        except Exception:  # pragma: no cover - a device being closed
            status = []
        rows = s.input_rows() if s.state != "waiting" else []
        text = "\n".join(io_panel_lines(status, rows)) or "No I/O channels"
        if p.view.message != text:
            p.view.set_message(text)
        if p.stack.currentIndex() == 2:
            p.set_zone_rows([(n, on if math.isfinite(on) else 0.0, k, lat) for n, _v, k, on, lat in rows])

    def _update_row_states(self):
        rows = self._entries()
        for r, e in enumerate(rows):
            st = e.state
            s = e.session
            if st == "waiting" and s.start_phase:
                txt = {"experimenter": "wait hand", "leaving": "hand in", "animal": "wait animal"}[s.start_phase]
            else:
                txt = {"idle": "not armed"}.get(st, st)
            it = self.sess_table.item(r, 5)
            if it is not None and it.text() != txt:
                it.setText(txt)
            it = self.sess_table.item(r, 6)
            if it is not None:
                it.setText(fmt_time(e.elapsed) if s is not None else "")
        sel = self._selected_entry()
        self.row_editor.setEnabled(sel is not None and sel.state not in ("waiting", "running", "paused"))
        self._update_panels()

    # ---- cameras of the group
    def _toggle_cameras(self):
        if self.group.runners:
            if any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
                QMessageBox.information(self, "Run tests", "Stop the tests before stopping the cameras.")
                return
            self.stop_cameras()
        else:
            self.start_cameras()

    def start_cameras(self) -> bool:
        if not self.group.sources:
            QMessageBox.information(self, "Run tests", "Add a camera or a video file first (Add camera / video "
                                    "source).")
            return False
        self.group.start_sources(speed=self.sim_speed.currentData() or 1.0)
        self._mosaic_timer.start()
        self._update_buttons()
        return True

    def stop_cameras(self):
        self.group.stop_sources()
        self._update_buttons()

    def capture_group_backgrounds(self) -> int:
        n = 0
        for key, r in self.group.runners.items():
            f = r.last_frame
            if f is not None:
                self._group_bgs[key] = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f.copy()
                n += 1
        self.group_lbl.setText(f"Empty-arena background captured for {n} camera{'s' if n != 1 else ''} at "
                               f"{_dt.datetime.now():%H:%M:%S}." if n else "Start the cameras first.")
        return n

    def multi_camera_options(self) -> bool:
        key = self._selected_source() or next(iter(self.group.sources), None)
        if key is None:
            QMessageBox.information(self, "Camera options", "Add a camera or a video file first.")
            return False
        spec = self.group.sources[key]
        if any(e.state in ("waiting", "running", "paused") for e in self.group.entries_for(key)):
            QMessageBox.information(self, "Camera options", "Stop the tests using this camera first.")
            return False
        r = self.group.runners.get(key)
        raw, raw2 = r.raw_frames() if r is not None else (None, None)
        if raw is None and spec.is_file:
            raw = peek_frame(spec.source)
        if raw2 is None and spec.second is not None and SourceSpec(spec.second).is_file:
            raw2 = peek_frame(spec.second)
        camera = r.camera() if r is not None and not spec.is_file else None
        dlg = CameraOptionsDialog(raw, spec.view, spec.second, spec.layout, self._merge_choices(spec.source), raw2,
                                  self, title=f"Camera options — {spec.label}", hardware=spec.hardware,
                                  camera=camera, is_camera=not spec.is_file, genicam=spec.is_native)
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()  # (when control returns to the event loop: its values are read below)
        if not accepted:
            return False
        self.apply_source_options(key, dlg.result())
        return True

    def apply_source_options(self, key: str, res: dict):
        spec = self.group.sources[key]
        old = (spec.view, spec.second, spec.layout)
        spec.view = CameraView.from_dict(res.get("view"))
        spec.second = res.get("second")
        spec.layout = res.get("layout", "side")
        if "hardware" in res:
            spec.hardware = CameraHardware.from_dict(res["hardware"])  # already applied live by the dialog
        settings = {}
        if not spec.view.is_identity:
            settings["view"] = spec.view.to_dict()
        if spec.second is not None:
            settings.update(second=spec.second, layout=spec.layout)
        if not spec.hardware.is_empty:
            settings["hardware"] = spec.hardware.to_dict()
        set_camera_settings(self.project, SourceSpec(spec.source).key, settings)
        self.main.mark_dirty()
        self._save_group_layout()
        if old == (spec.view, spec.second, spec.layout):
            return
        self._group_bgs.pop(key, None)
        if key in self.group.runners:
            self.group.stop_sources([key])
            self.group.start_sources(speed=self.sim_speed.currentData() or 1.0, keys=[key])
        self._sync_panels()

    def _save_group_layout(self):
        if self.project is None or self._loading:
            return
        keys = list(self.group.sources)
        # "source" -1: a panel without a camera (Input/output only; versions without them skip it)
        d = {"sources": [self.group.sources[k].to_dict() for k in keys],
             "sessions": [{"source": keys.index(e.source_key) if e.source_key is not None else -1,
                           **{k: e.meta.get(k) for k in ("apparatus", "animal", "stage", "trial")},
                           **({"device": e.meta["device"]} if e.meta.get("device") else {})}
                          for e in self.group.entries if e.source_key in self.group.sources
                          or e.source_key is None]}
        live = self._live_settings()
        if live.get("multi") != d:
            live["multi"] = d
            self.main.mark_dirty()

    def _restore_group_layout(self):
        d = (self.project.settings_extra.get("live") or {}).get("multi") or {}
        keys = []
        for sd in d.get("sources", []):
            spec = SourceSpec.from_dict(sd)
            if spec.is_file and not Path(str(spec.source)).exists():
                keys.append(None)
                continue
            keys.append(self.group.add_source(spec))
        for sd in d.get("sessions", []):
            i = sd.get("source", 0)
            io = i == -1  # a panel without a camera
            if not io and (not isinstance(i, int) or not 0 <= i < len(keys) or keys[i] is None):
                continue
            meta = {"apparatus": sd.get("apparatus", ""), "animal": sd.get("animal", ""),
                    "stage": sd.get("stage", ""), "trial": sd.get("trial", 1), "test_id": None,
                    "device": sd.get("device", "") or ""}
            e = self.group.add_entry(None if io else keys[i], self.project.find_apparatus(meta["apparatus"] or ""),
                                     "", meta)
            self._relabel(e)

    # ---- arming / control of the group
    def _notice(self, title: str, msg: str):
        """A non-modal message (the tests keep running; used instead of a dialog by scheduled starts)."""
        box = QMessageBox(QMessageBox.Warning, title, msg, QMessageBox.Ok, self)
        box.setModal(False)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

    def _source_open(self, key) -> bool:
        """The source of a panel delivers images: its frame rate, size and (video file) background are known."""
        r = self.group.runners.get(key)
        return r is not None and r.size is not None and r.last_frame is not None

    def _pending_entries(self) -> list:
        return [e for e in self.group.entries if e.meta.get("arm_pending")]

    def arm_row(self, e, interactive: bool = True) -> bool:
        """Arm the test of panel `e`.  When its camera / video is not open yet, the test is armed as soon as it
        is (_complete_pending_arms): its session needs the real frame rate, image size and background.
        `interactive` False (scheduled starts): no modal dialog, problems are logged and shown as notices."""
        p = self.project
        if p is None or e.session is not None and e.state != "finished":
            return False
        if e.meta.get("arm_pending"):
            return True
        if p.path is None:
            if interactive:
                QMessageBox.information(self, "Run tests", "Save the experiment first.")
            return False
        m = e.meta
        io = e.source_key is None  # no camera (Input/output only): the apparatus is optional
        app = p.find_apparatus(m.get("apparatus") or "")
        if (app is None and (m.get("apparatus") or not io)) or not m.get("animal"):
            what = (f"apparatus “{m.get('apparatus')}” no longer exists — choose its apparatus"
                    if m.get("apparatus") and app is None else "choose an animal" if io else
                    "choose an apparatus and an animal")
            self._log(f"{e.label}: not armed: {what} first.", e)
            return False
        plan, msg = self._io_plan(e)
        if plan is None:
            self._log(f"{e.label}: not armed. {msg}", e)
            if interactive:
                QMessageBox.warning(self, "Run tests", f"{e.label}: {msg}")
            else:
                self._notice("Run tests", f"{e.label}: {msg}")
            return False
        if not io and e.source_key not in self.group.runners:
            if not self.group.sources:
                return False
            self.start_cameras()
        if p.get_animal(m["animal"]) is None:
            p.ensure_animal(m["animal"])
        app_name = app.name if app is not None else ""
        test = p.get_test(m["test_id"]) if m.get("test_id") is not None else None
        if test is None:
            pend = next((t for t in p.tests if t.status == "pending" and not t.video and t.animal_id == m["animal"]
                         and t.stage == m.get("stage", "") and t.trial == m.get("trial", 1)), None)
            test = pend or p.add_test("", m["animal"], app_name, stage=m.get("stage", ""), trial=m.get("trial", 1))
            m["new_test"] = pend is None
        if interactive:
            if not confirm_animal_id(self, test):
                if m.get("new_test"):
                    self._remove_test(test)
                return False
        elif confirm_id_enabled(p):  # nobody to scan the animal at a scheduled start: noted, not asked
            note = f"{e.label}: scheduled start — the ID of animal {test.animal_id} was not checked."
            self._log(note, e)
            self._notice("Animal ID check", note)
        test.apparatus = app_name
        dur = self.duration.value()
        test.duration_s = 0.0 if abs(dur - p.test_duration_s) < 1e-9 else dur
        m["test_id"] = test.id
        m["io_plan"] = plan
        if not io and not self._source_open(e.source_key):
            m["arm_pending"] = True
            self._log(f"{e.label}: opening the camera / video — the test is armed as soon as it delivers images.",
                      e)
            return True
        return self._arm_row_session(e)

    def _arm_row_session(self, e) -> bool:
        """The session of panel `e` (its test chosen by arm_row), with the open source's frame rate and size."""
        p = self.project
        m = e.meta
        test = p.get_test(m.get("test_id")) if m.get("test_id") is not None else None
        app = p.find_apparatus(m.get("apparatus") or "")
        if test is None or app is None and (e.source_key is not None or m.get("apparatus")):
            self._log(f"{e.label}: not armed: its test or apparatus was removed meanwhile.", e)
            m["test_id"] = None
            return False
        r = self.group.runners.get(e.source_key)
        size, fps = (r.size if r is not None and r.size else (640, 480)), (r.fps if r is not None else 25.0)
        bg = self._group_bgs.get(e.source_key)
        if bg is None and r is not None and r.background is not None:
            bg = r.background
        if bg is not None and bg.shape[:2] != (size[1], size[0]):
            bg = None
        if self._group_outputs is None:
            self._group_outputs = Outputs(self.serial.currentText().strip() or None)
        try:
            devices = self._session_devices(m.get("io_plan") or "*")
        except Exception as ex:
            self._log(f"{e.label}: not armed. I/O devices: {ex}", e)
            if m.get("new_test"):
                self._remove_test(test)
            m["test_id"] = None
            return False
        panel = self._panels.get(e.id)
        if panel is not None:
            panel.log.clear()
        s = self._make_session(test, app, bg, size, fps, self._group_outputs, devices,
                               f"Test {test.id} · {test.animal_id} · {self._place(e)}", entry=e)
        m["record_path"] = s.record_path
        self.group.arm(e, s)
        self.main.mark_dirty()
        if panel is not None:
            panel.view.set_apparatus(s.apparatus)
        self._log(f"{e.label}: test {test.id} armed ({self.start_mode.currentText().lower()}).", e)
        return True

    def _cancel_pending(self, entries=None):
        """Tests waiting for their camera / video to open are not armed after all (stopped, removed…)."""
        p = self.project
        for e in (entries if entries is not None else self._pending_entries()):
            m = e.meta
            if not m.pop("arm_pending", None):
                continue
            m.pop("start_now", None)
            test = p.get_test(m.get("test_id")) if p is not None and m.get("test_id") is not None else None
            if test is not None and m.get("new_test"):
                self._remove_test(test)
            m["test_id"] = None
            self._log(f"{e.label}: not armed.", e)

    def _complete_pending_arms(self):
        """Arm the tests waiting for their source as soon as it delivers images (or give up when it failed)."""
        done = []
        for e in self._pending_entries():
            r = self.group.runners.get(e.source_key)
            failed = r is None or (not r.thread.is_alive() and r.last_frame is None)
            if not failed and not self._source_open(e.source_key):
                continue
            if failed:
                self._log(f"{e.label}: not armed: the camera / video did not open.", e)
                self._cancel_pending([e])
                continue
            e.meta.pop("arm_pending", None)
            start_now = e.meta.pop("start_now", False)
            if self._arm_row_session(e):
                done.append(e)
                if start_now and e.state == "waiting":
                    e.session.request_start()
        if not done:
            return
        busy = {x.source_key for x in self.group.entries if x not in done and x.state in ("running", "paused")}
        for key in {e.source_key for e in done}:
            spec = self.group.sources.get(key)
            if spec is not None and spec.is_file and key not in busy:
                self.group.restart_source(key)  # the test starts at the beginning of the video
        self._enable_shortcuts(True)
        self._update_row_states()
        self._update_buttons()

    def _io_plan(self, e) -> tuple[str | None, str]:
        """Which I/O devices the test of panel `e` may use (see livegroup.device_plan), given the running tests."""
        p = self.project
        others = [x.meta.get("io_plan") or "*" for x in self.group.entries
                  if x is not e and x.session is not None and x.state in ("waiting", "running", "paused")]
        if others and self.serial.currentText().strip():
            return None, ("the serial port in the test settings is shared by every test: with several tests at "
                          "once, configure each box as an I/O device instead (Experiment › I/O devices) and clear "
                          "the serial port.")
        if not p.io_devices:
            return "*", ""
        return device_plan(p.io_devices, e.meta.get("device", "") or "", others)

    def _session_devices(self, plan: str):
        """The device manager (plan "*"), or a per-test view of one box ("name") or of no hardware ("-")."""
        dm = self._open_devices()
        if plan == "*" or dm is None:
            return dm
        return DeviceView(dm, None if plan == "-" else plan)

    def arm_all(self, entries=None, interactive: bool = True) -> int:
        """Arm every idle (or finished) row; video files restart so the tests start at their beginning (unless
        another test already running uses the same video)."""
        panels = self._entries()
        ents = [e for e in (entries or panels) if e in panels and e.state in ("idle", "finished")]
        if not ents:
            return 0
        if not self.procedures_ready(interactive):  # checked (and programs allowed) once for all the tests
            for e in ents:
                self._log(f"{e.label}: not armed (procedures).", e)
            return 0
        busy = {x.source_key for x in self.group.entries if x not in ents and x.state in ("running", "paused")}
        n = sum(1 for e in ents if self.arm_row(e, interactive))
        if not n:
            return 0
        for key in {e.source_key for e in ents if e.session is not None}:
            spec = self.group.sources.get(key)
            if spec is not None and spec.is_file:
                if key in busy:
                    self._log(f"{spec.label}: another test is running on this video: the new test starts at the "
                              f"current position.")
                    continue
                self.group.restart_source(key)
        # a daily schedule re-arms its tests when it fires: they keep that schedule rather than get another one
        scheduled = {i for sch in self.group.schedules if sch.entry_ids is not None for i in sch.entry_ids}
        ids = [e.id for e in ents if (e.session is not None or e.meta.get("arm_pending")) and e.id not in scheduled]
        if self.start_mode.currentData() == "scheduled" and ids:
            sch = self._new_schedule(ids)
            self.group.on_schedule = self._on_group_schedule
            self._log(f"Scheduled start {sch.describe()}.")
        self._enable_shortcuts(True)
        self.tabs.setCurrentWidget(self.monitor_tab)
        self._update_row_states()
        self._update_buttons()
        return n

    def _on_group_schedule(self, sch, entries):
        """A clock schedule fired: start waiting tests, then re-arm finished rows (daily schedules) — without any
        modal dialog (nobody may be there; the other tests keep running)."""
        self._save_finished_entries()
        rearm = [e for e in entries if e.state in ("idle", "finished") and not e.meta.get("arm_pending")]
        for e in entries:
            if e.state == "waiting":
                e.session.request_start()
        if rearm and self.arm_all(rearm, interactive=False):
            for e in rearm:
                if e.state == "waiting":
                    e.session.request_start()
                elif e.meta.get("arm_pending"):
                    e.meta["start_now"] = True
        self._log(f"Scheduled start ({sch.at}): {len(entries)} test(s).")

    def row_action(self, e, kind: str):
        st = e.state
        if kind == "start":
            if st in ("idle", "finished"):
                self.arm_all([e])
            else:
                self.group.start(e)
        elif kind == "pause":
            if st == "paused":
                self.group.resume(e)
                self._log(f"{fmt_time(e.elapsed)}  test resumed", e)
            elif st == "running":
                self.group.pause(e)
                self._log(f"{fmt_time(e.elapsed)}  test paused", e)
        elif kind == "stop":
            self._cancel_pending([e])
            self.group.stop(e, save=st in ("running", "paused"))
            self._save_finished_entries()
        self._update_row_states()
        self._update_buttons()

    def _start_entry_now(self, e):
        """Panel ▶ ▾ Start now: arm the test if needed and start it without waiting for its start condition."""
        if e.state in ("idle", "finished") and not e.meta.get("arm_pending") and not self.arm_all([e]):
            return False
        if e.meta.get("arm_pending"):
            e.meta["start_now"] = True  # started as soon as its source is open
        if e.state == "waiting":
            e.session.request_start()
            self._log("Start requested.", e)
        self._update_row_states()
        self._update_buttons()
        return True

    def start_all(self):
        if not any(e.session is not None and e.state != "finished" for e in self.group.entries):
            self.arm_all()
        self.group.start_all()
        for e in self._pending_entries():
            e.meta["start_now"] = True
        self._update_buttons()

    def group_pause_all(self):
        self.group.pause_all()
        self._log("All tests paused.")
        self._update_row_states()
        self._update_buttons()

    def group_resume_all(self):
        self.group.resume_all()
        self._log("All tests resumed.")
        self._update_row_states()
        self._update_buttons()

    def _stop_all_clicked(self):
        self._cancel_pending()
        if not any(e.state in ("waiting", "running", "paused") for e in self.group.entries):
            self._update_buttons()
            return
        r = QMessageBox.question(self, "Stop all tests", "Stop every test now?\n\nSave keeps the data recorded so "
                                 "far; Discard throws the tests away.",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return
        self.group.stop_all(save=r == QMessageBox.Save)
        self._save_finished_entries()

    def _save_finished_entries(self):
        p = self.project
        for e in self.group.finished_unsaved():
            e.saved = True
            m = e.meta
            s = e.session
            test = p.get_test(m.get("test_id")) if p is not None and m.get("test_id") is not None else None
            if not self._store(test, s, m.get("record_path"), not e.aborted, bool(m.get("new_test")), e):
                self._log(f"{e.label}: test discarded.", e)
            else:
                self._log(f"{e.label}: test {test.id} finished after {fmt_time(s.elapsed)} and saved.", e)
                self._show_results(test, switch=False)
                m["trial"] = int(m.get("trial", 1)) + 1
                rows = self._entries()
                if e in rows:
                    self._fill_row(rows.index(e), e)
                if e is self._selected_entry():
                    self.row_trial.blockSignals(True)
                    self.row_trial.setValue(m["trial"])
                    self.row_trial.blockSignals(False)
            m["test_id"] = None
            m["record_path"] = None
        if not any(e.state in ("waiting", "running", "paused") or e.meta.get("arm_pending")
                   for e in self.group.entries):
            if self._group_outputs is not None:
                self._group_outputs.close()
                self._group_outputs = None
            if self.session is None and (self.obs is None or self.obs.state == "finished"):
                self._enable_shortcuts(False)
            self._close_devices()
