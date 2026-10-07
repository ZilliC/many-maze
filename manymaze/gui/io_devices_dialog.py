"""The I/O devices dialog: edits ``project.io_devices`` (device type, port, channels) and shows the live state of
the I/O with manual output toggles and a test button."""

from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import iodevices as iod
from .widgets import loading, value_text

# channel table columns: (channel field, header); "options" holds the driver options as key=value text
CH_COLS = [("name", "Name"), ("kind", "Kind"), ("pin", "Pin"), ("pin_b", "Pin B"), ("invert", "Invert"),
           ("on", "On text"), ("off", "Off text"), ("options", "Options")]
_CH_KEYS = {"name", "kind", "pin", "pin_b", "invert", "on", "off"}


def _parse_options(text: str) -> dict:
    out = {}
    for part in text.replace(";", ",").split(","):
        if "=" not in part:
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        if not k:
            continue
        try:
            fv = float(v)
            out[k] = int(fv) if fv.is_integer() and "." not in v else fv
        except ValueError:
            out[k] = {"true": True, "false": False}.get(v.lower(), v)
    return out


def _options_text(ch: dict) -> str:
    return ", ".join(f"{k}={value_text(v)}" for k, v in ch.items() if k not in _CH_KEYS)


class IODevicesDialog(QDialog):
    """Configure project.io_devices, with live I/O status, manual outputs and a test button."""

    changed = Signal()

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("I/O devices")
        self.project = project
        self.configs = copy.deepcopy(list(project.io_devices or []))
        self.manager: iod.DeviceManager | None = None  # while connected
        self._loading = False
        self._status_keys: list = []
        self._build()
        self._refresh_list(0)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._poll)

    def _build(self):
        v = QVBoxLayout(self)
        top = QSplitter(Qt.Horizontal)
        v.addWidget(top, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("<b>Devices</b>"))
        self.dev_list = QListWidget()
        self.dev_list.currentRowChanged.connect(lambda *_: self._load_device())
        lv.addWidget(self.dev_list, 1)
        hb = QHBoxLayout()
        add = QToolButton()
        add.setText("Add ▾")
        add.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(add)
        for t, label in iod.DEVICE_TYPES.items():
            m.addAction(label, lambda t=t: self.add_device(t))
        add.setMenu(m)
        rm = QToolButton()
        rm.setText("Remove")
        rm.clicked.connect(self.remove_device)
        hb.addWidget(add)
        hb.addWidget(rm)
        hb.addStretch()
        lv.addLayout(hb)
        top.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.dev_box = QGroupBox("Device")
        f = QFormLayout(self.dev_box)
        self.f_name = QLineEdit()
        self.f_type = QComboBox()
        for t, label in iod.DEVICE_TYPES.items():
            self.f_type.addItem(label, t)
        self.f_port = QComboBox()
        self.f_port.setEditable(True)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Refresh the list of serial ports")
        refresh.clicked.connect(self._fill_ports)
        prow = QWidget()
        ph = QHBoxLayout(prow)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self.f_port, 1)
        ph.addWidget(refresh)
        self.f_baud = QComboBox()
        self.f_baud.setEditable(True)
        self.f_baud.addItems(["115200", "57600", "38400", "19200", "9600"])
        self.f_watchdog = QSpinBox()
        self.f_watchdog.setRange(0, 60000)
        self.f_watchdog.setSingleStep(500)
        self.f_watchdog.setSuffix(" ms")
        self.f_watchdog.setSpecialValueText("Off")
        self.f_watchdog.setToolTip("All outputs switch off if the computer stops talking to the board for this long "
                                   "(default 2000 ms when the board has outputs; Off = 0)")
        self.f_backend = QComboBox()
        for b in iod.AUDIO_BACKENDS:
            self.f_backend.addItem(b, b)
        self.f_enabled = QCheckBox("Enabled")
        f.addRow("Name", self.f_name)
        f.addRow("Type", self.f_type)
        f.addRow("Serial port", prow)
        f.addRow("Baud rate", self.f_baud)
        f.addRow("Watchdog", self.f_watchdog)
        f.addRow("Audio player", self.f_backend)
        f.addRow("", self.f_enabled)
        # form rows of the device fields (core DEVICE_FIELDS)
        self._rows = {"port": prow, "baud": self.f_baud, "watchdog_ms": self.f_watchdog, "backend": self.f_backend}
        self._form = f
        self.f_name.editingFinished.connect(self._save_device)
        for w in (self.f_type, self.f_backend):
            w.currentIndexChanged.connect(lambda *_: self._save_device(retype=True))
        self.f_port.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_baud.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_watchdog.valueChanged.connect(self._watchdog_changed)
        self.f_enabled.toggled.connect(lambda *_: self._save_device())
        rv.addWidget(self.dev_box)

        self.ch_box = QGroupBox("Channels")
        cv = QVBoxLayout(self.ch_box)
        self.ch_table = QTableWidget(0, len(CH_COLS))
        self.ch_table.setHorizontalHeaderLabels([label for _k, label in CH_COLS])
        self.ch_table.verticalHeader().hide()
        hh = self.ch_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(CH_COLS) - 1, QHeaderView.Stretch)
        self.ch_table.itemChanged.connect(lambda *_: self._save_channels())
        self.ch_table.setToolTip("Options (key=value, comma separated): pullup, debounce_ms, counts_per_rev, "
                                 "cm_per_rev, scale, period_ms, deadband")
        cv.addWidget(self.ch_table)
        cb = QHBoxLayout()
        b1 = QPushButton("Add channel")
        b1.clicked.connect(lambda: self.add_channel())
        b2 = QPushButton("Remove channel")
        b2.clicked.connect(self.remove_channel)
        cb.addWidget(b1)
        cb.addWidget(b2)
        cb.addStretch()
        cv.addLayout(cb)
        rv.addWidget(self.ch_box, 1)
        top.addWidget(right)
        top.setSizes([170, 560])

        live = QGroupBox("Live status")
        lv2 = QVBoxLayout(live)
        hb2 = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip("Open the devices and show their inputs and outputs live")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.test_btn = QPushButton("Test")
        self.test_btn.setToolTip("Pulse the selected output for 0.5 s, or play a 1 kHz tone on an audio device")
        self.test_btn.clicked.connect(self.test_selected)
        hb2.addWidget(self.connect_btn)
        hb2.addWidget(self.test_btn)
        self.conn_lbl = QLabel("Not connected")
        self.conn_lbl.setWordWrap(True)
        hb2.addWidget(self.conn_lbl, 1)
        lv2.addLayout(hb2)
        self.status = QTableWidget(0, 5)
        self.status.setHorizontalHeaderLabels(["Device", "Channel", "Kind", "Value", ""])
        self.status.verticalHeader().hide()
        self.status.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.status.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.status.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.status.setMinimumHeight(130)
        lv2.addWidget(self.status)
        v.addWidget(live)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(860, 640)
        self._fill_ports()

    # ------------------------------------------------------------------ devices
    def _fill_ports(self):
        ports = iod.serial_ports()
        cur = self.f_port.currentText()
        with loading(self):
            self.f_port.clear()
            if ports is None:
                self.f_port.setToolTip("pyserial is not installed (pip install pyserial)")
            else:
                self.f_port.addItems(ports)
            self.f_port.setCurrentText(cur)

    def _refresh_list(self, select=None):
        cur = self.dev_list.currentRow() if select is None else select
        with loading(self):
            self.dev_list.clear()
            for c in self.configs:
                self.dev_list.addItem(f"{c.get('name', '?')}  ·  {iod.DEVICE_TYPES.get(c.get('type'), c.get('type'))}")
        if self.configs:
            self.dev_list.setCurrentRow(min(max(cur, 0), len(self.configs) - 1))
        self._load_device()

    def _cur(self) -> dict | None:
        i = self.dev_list.currentRow()
        return self.configs[i] if 0 <= i < len(self.configs) else None

    def add_device(self, type_: str = "virtual"):
        self.configs.append(iod.new_device(type_, [c.get("name") for c in self.configs]))
        self._refresh_list(len(self.configs) - 1)

    def remove_device(self):
        i = self.dev_list.currentRow()
        if 0 <= i < len(self.configs):
            del self.configs[i]
            self._refresh_list(min(i, len(self.configs) - 1))

    def _load_device(self):
        c = self._cur()
        self.dev_box.setEnabled(c is not None)
        self.ch_box.setEnabled(c is not None)
        c = c or {}
        with loading(self):
            self.f_name.setText(c.get("name", ""))
            self.f_type.setCurrentIndex(max(0, self.f_type.findData(c.get("type", "virtual"))))
            self.f_port.setCurrentText(c.get("port", ""))
            self.f_baud.setCurrentText(str(c.get("baud", 115200)))
            self.f_watchdog.setValue(iod.watchdog_ms(c))
            self.f_backend.setCurrentIndex(max(0, self.f_backend.findData(c.get("backend", "auto"))))
            self.f_enabled.setChecked(c.get("enabled", True))
            self._fill_channels(c)
        self._update_visibility()

    def _update_visibility(self):
        t = (self._cur() or {}).get("type", "virtual")
        for key, w in self._rows.items():
            show = key in iod.DEVICE_FIELDS.get(t, {})
            w.setVisible(show)
            lbl = self._form.labelForField(w)
            if lbl is not None:
                lbl.setVisible(show)
        shown = {"name", "kind", "options", *iod.CHANNEL_FIELDS.get(t, ())}
        for col, (key, _label) in enumerate(CH_COLS):
            self.ch_table.setColumnHidden(col, key not in shown)
        self.ch_box.setVisible(t != "audio")

    def _save_device(self, retype=False):
        c = self._cur()
        if self._loading or c is None:
            return
        c["name"] = self.f_name.text().strip() or c.get("name", "device")
        c["type"] = self.f_type.currentData()
        c["enabled"] = self.f_enabled.isChecked()
        fields = iod.DEVICE_FIELDS[c["type"]]
        for k in self._rows:
            if k not in fields:
                c.pop(k, None)
        if "port" in fields:
            c["port"] = self.f_port.currentText().strip()
            try:
                c["baud"] = int(self.f_baud.currentText())
            except ValueError:
                c["baud"] = 115200
        if "backend" in fields:
            c["backend"] = self.f_backend.currentData()
        it = self.dev_list.currentItem()
        if it is not None:
            it.setText(f"{c['name']}  ·  {iod.DEVICE_TYPES.get(c['type'], c['type'])}")
        if retype:
            self._update_visibility()

    def _watchdog_changed(self, v):
        """Only an explicit change is stored: without the key the core default follows the board's outputs."""
        c = self._cur()
        if not self._loading and c is not None:
            c["watchdog_ms"] = v

    # ------------------------------------------------------------------ channels
    def _fill_channels(self, c):
        self.ch_table.setRowCount(0)
        for ch in c.get("channels", []) or []:
            self._append_channel_row(ch)

    def _append_channel_row(self, ch):
        r = self.ch_table.rowCount()
        self.ch_table.insertRow(r)
        kind = QComboBox()
        for k, label in iod.CHANNEL_KINDS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(ch.get("kind", "input"))))
        kind.currentIndexChanged.connect(lambda *_: self._save_channels())
        inv = QTableWidgetItem()
        inv.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        inv.setCheckState(Qt.Checked if ch.get("invert") else Qt.Unchecked)
        for col, (key, _label) in enumerate(CH_COLS):
            if key == "kind":
                self.ch_table.setCellWidget(r, col, kind)
            elif key == "invert":
                self.ch_table.setItem(r, col, inv)
            else:
                text = _options_text(ch) if key == "options" else value_text(ch.get(key, ""))
                self.ch_table.setItem(r, col, QTableWidgetItem(text))

    def add_channel(self, ch: dict | None = None):
        c = self._cur()
        if c is None:
            return
        n = len(c.get("channels", []))
        ch = ch or {"name": f"ch{n + 1}", "kind": "output" if n % 2 else "input"}
        if "pin" in iod.CHANNEL_FIELDS.get(c.get("type"), ()) and "pin" not in ch:
            ch["pin"] = iod.next_free_pin(c)
        c.setdefault("channels", []).append(ch)
        with loading(self):
            self._append_channel_row(ch)

    def remove_channel(self):
        c = self._cur()
        r = self.ch_table.currentRow()
        if c is not None and 0 <= r < len(c.get("channels", [])):
            del c["channels"][r]
            self.ch_table.removeRow(r)

    def _save_channels(self):
        c = self._cur()
        if self._loading or c is None:
            return
        t = self.ch_table
        col = {key: i for i, (key, _label) in enumerate(CH_COLS)}

        def txt(r, key):
            it = t.item(r, col[key])
            return it.text().strip() if it is not None else ""
        out = []
        for r in range(t.rowCount()):
            ch = {"name": txt(r, "name"), "kind": t.cellWidget(r, col["kind"]).currentData()}
            for key in ("pin", "pin_b"):
                v = txt(r, key)
                if v:
                    ch[key] = int(v) if v.lstrip("-").isdigit() else v
            if t.item(r, col["invert"]).checkState() == Qt.Checked:
                ch["invert"] = True
            for key in ("on", "off"):
                if txt(r, key):
                    ch[key] = txt(r, key)
            # driver options; the columns win over options named like them
            ch.update({k: v for k, v in _parse_options(txt(r, "options")).items() if k not in _CH_KEYS})
            out.append(ch)
        c["channels"] = out
        if "watchdog_ms" not in c:  # the default depends on whether the board has outputs
            with loading(self):
                self.f_watchdog.setValue(iod.watchdog_ms(c))

    # ------------------------------------------------------------------ live status
    def toggle_connection(self):
        if self.manager is not None:
            self.disconnect_devices()
        else:
            self.connect_devices()

    def connect_devices(self):
        self._save_channels()
        if self.manager is not None:
            self.manager.close()
        self.manager = iod.DeviceManager(self.configs)
        self.connect_btn.setText("Disconnect")
        self._refresh_status(force=True)
        self.timer.start()

    def disconnect_devices(self):
        self.timer.stop()
        if self.manager is not None:
            self.manager.close()
        self.manager = None
        self.connect_btn.setText("Connect")
        self.status.setRowCount(0)
        self.conn_lbl.setText("Not connected")

    def _poll(self):
        if self.manager is None:
            return
        self.manager.read_inputs()
        self._refresh_status()

    def _refresh_status(self, force=False):
        m = self.manager
        if m is None:
            return
        rows = m.status()
        keys = [(d, c, k) for d, c, k, _v in rows]
        if force or keys != self._status_keys:
            self._status_keys = keys
            self.status.setRowCount(len(rows))
            for r, (d, c, k, _v) in enumerate(rows):
                for col, text in enumerate((d, c, iod.CHANNEL_KINDS.get(k, k))):
                    self.status.setItem(r, col, QTableWidgetItem(text))
                self.status.setItem(r, 3, QTableWidgetItem(""))
                dev = m.devices.get(d)
                if k in iod.OUTPUT_KINDS or (k == "input" and isinstance(dev, iod.VirtualDevice)):
                    b = QPushButton("Toggle" if k in iod.OUTPUT_KINDS else "Simulate")
                    b.clicked.connect(lambda _=False, d=d, c=c, k=k: self.toggle_channel(d, c, k))
                    self.status.setCellWidget(r, 4, b)
                else:
                    self.status.removeCellWidget(r, 4)
        for r, (_d, _c, k, v) in enumerate(rows):
            it = self.status.item(r, 3)
            text = ("ON" if v else "off") if k in ("input", "output") else \
                value_text(round(v, 3) if isinstance(v, float) else v)
            if it.text() != text:
                it.setText(text)
                it.setForeground(QBrush(QColor("#15803d" if v else "#6b7280")))
        errs = m.errors
        self.conn_lbl.setText("; ".join(errs[-3:]) if errs else f"Connected: {len(m.devices)} device(s)")
        self.conn_lbl.setStyleSheet("color:#dc2626" if errs else "color:#15803d")

    def toggle_channel(self, device, channel, kind):
        m = self.manager
        if m is None:
            return
        dev = m.devices.get(device)
        if kind in iod.OUTPUT_KINDS:
            m.set_output(device, channel, 0 if dev.outputs.get(channel) else 1)
        else:
            m.set_input(device, channel, 0 if dev.inputs.get(channel) else 1)
        self._poll()

    def test_selected(self):
        if self.manager is None:
            self.connect_devices()
        m = self.manager
        r = self.status.currentRow()
        c = self._cur()
        if c is not None and c.get("type") == "audio" and m.has(c.get("name")):
            m.audio(c["name"], "tone", frequency=1000, duration=0.5, volume=0.5)
            self.conn_lbl.setText(f"Played a 1 kHz tone on {c['name']}")
            return
        if 0 <= r < len(self._status_keys):
            d, ch, k = self._status_keys[r]
        else:
            outs = [key for key in self._status_keys if key[2] in iod.OUTPUT_KINDS]
            if not outs:
                self.conn_lbl.setText("Select an output to test")
                return
            d, ch, k = outs[0]
        if k not in iod.OUTPUT_KINDS:
            self.conn_lbl.setText(f"{ch} is an input: press the lever / break the beam to see it change")
            return
        m.set_output(d, ch, 1)
        QTimer.singleShot(500, lambda: (m.set_output(d, ch, 0), self._refresh_status()))
        self._refresh_status()

    # ------------------------------------------------------------------ close
    def accept(self):
        self._save_device()
        self._save_channels()
        names = [c.get("name") for c in self.configs]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            QMessageBox.warning(self, "I/O devices", f"Device names must be unique: {', '.join(sorted(dup))}")
            return
        if self.configs != list(self.project.io_devices or []):
            self.project.io_devices[:] = copy.deepcopy(self.configs)
            self.changed.emit()
        self.disconnect_devices()
        super().accept()

    def reject(self):
        self.disconnect_devices()
        super().reject()

    def closeEvent(self, e):
        self.disconnect_devices()
        super().closeEvent(e)

    def sizeHint(self):
        return QSize(860, 640)
