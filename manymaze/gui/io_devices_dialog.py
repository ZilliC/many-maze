"""The I/O devices dialog: edits ``project.io_devices`` (device type, port, channels) and shows the live state of
the I/O with manual output toggles and a test button."""

from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import ioconfig
from ..core import iodevices as iod
from .widgets import loading, run_and_wait, value_text

# channel table columns: (channel field, header); "options" holds the driver options as key=value text; "role" and
# "max_on_s" are the outputs' safety options (a shocker's outputs switch off after 60 s at most, see
# iodevices.Device.max_on_s)
CH_COLS = [("name", "Name"), ("kind", "Kind"), ("pin", "Pin"), ("pin_b", "Pin B"), ("invert", "Invert"),
           ("on", "On text"), ("off", "Off text"), ("options", "Options"), ("role", "Role"),
           ("max_on_s", "Max on (s)")]
_CH_KEYS = {"name", "kind", "pin", "pin_b", "invert", "on", "off", "role", "max_on_s"}
CH_ROLES = {"": "—", "shocker": "Shocker", "speaker": "Speaker", "light": "Light"}
OPTIONS_COL = [k for k, _l in CH_COLS].index("options")


def open_device_manager(parent, configs, title: str = "Connecting the I/O devices") -> tuple:
    """A DeviceManager of `configs`, opened in a Worker (a board can take seconds to answer). Returns (manager or
    None, problems to show the user: failures, device errors, or configured devices of which none opened)."""
    m, err = run_and_wait(parent, title, lambda progress, stop: iod.DeviceManager(configs))
    if m is None:
        return None, [err or "The I/O devices could not be opened."]
    problems = list(m.errors)
    if m.devices and not any(d.connected for d in m.devices.values()):
        problems.append("None of the configured I/O devices could be opened.")
    return m, problems


# labels of the device fields without a widget of their own (shown as text fields)
FIELD_LABELS = {"protocol": "Protocol", "device_id": "NI device", "identifier": "LabJack (serial / IP / ANY)",
                "smtp_host": "SMTP server", "smtp_port": "SMTP port", "smtp_user": "SMTP user",
                "smtp_password": "SMTP password", "from_addr": "From address", "email_to": "E-mail alerts to",
                "sms_to": "SMS alerts to", "twilio_sid": "Twilio account SID", "twilio_token": "Twilio auth token",
                "twilio_from": "Twilio phone number"}
_SECRET = ("smtp_password", "twilio_token")


def protocols_for(type_: str) -> dict:
    """The protocols of syringe pumps and balances (id -> label)."""
    if type_ == "syringe_pump":
        from ..core.pumps import PUMP_PROTOCOLS
        return dict(PUMP_PROTOCOLS)
    if type_ == "scale":
        from ..core.scales import SCALE_PROTOCOLS
        return dict(SCALE_PROTOCOLS)
    return {}


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
        self._output_errors: list[str] = []  # outputs that could not be set while connected
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
        for t, label in ioconfig.DEVICE_TYPES.items():
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
        for t, label in ioconfig.DEVICE_TYPES.items():
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
        self.f_safe = QSpinBox()  # syringe pumps (New Era): the pumps' safe mode
        self.f_safe.setRange(0, 255)
        self.f_safe.setSuffix(" s")
        self.f_safe.setSpecialValueText("Off")
        self.f_safe.setToolTip("New Era pumps: safe mode — a pump stops by itself when it hears nothing valid from "
                               "the computer for this long (1-255 s; Off = 0). Untested on a real pump: check it "
                               "before relying on it.")
        self.f_backend = QComboBox()
        for b in ioconfig.AUDIO_BACKENDS:
            self.f_backend.addItem(b, b)
        self.f_enabled = QCheckBox("Enabled")
        f.addRow("Name", self.f_name)
        f.addRow("Type", self.f_type)
        f.addRow("Serial port", prow)
        f.addRow("Baud rate", self.f_baud)
        f.addRow("Watchdog", self.f_watchdog)
        f.addRow("Safe mode", self.f_safe)
        f.addRow("Audio player", self.f_backend)
        # form rows of the device fields (core DEVICE_FIELDS)
        self._rows = {"port": prow, "baud": self.f_baud, "watchdog_ms": self.f_watchdog, "backend": self.f_backend}
        self._extra: dict[str, QWidget] = {}  # the other fields: text (protocol: a list)
        for fields in ioconfig.DEVICE_FIELDS.values():
            for k in fields:
                if k in self._rows:
                    continue
                if k == "protocol":
                    w = QComboBox()
                    w.setEditable(True)
                    w.currentTextChanged.connect(lambda *_: self._save_device())
                else:
                    w = QLineEdit()
                    if k in _SECRET:
                        w.setEchoMode(QLineEdit.Password)
                    w.editingFinished.connect(self._save_device)
                f.addRow(FIELD_LABELS.get(k, k.replace("_", " ").capitalize()), w)
                self._extra[k] = self._rows[k] = w
        f.addRow("", self.f_enabled)
        self._form = f
        self.f_name.editingFinished.connect(self._save_device)
        for w in (self.f_type, self.f_backend):
            w.currentIndexChanged.connect(lambda *_: self._save_device(retype=True))
        self.f_port.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_baud.currentTextChanged.connect(lambda *_: self._save_device())
        self.f_watchdog.valueChanged.connect(self._watchdog_changed)
        self.f_safe.valueChanged.connect(self._safe_mode_changed)
        self.f_enabled.toggled.connect(lambda *_: self._save_device())
        rv.addWidget(self.dev_box)

        self.ch_box = QGroupBox("Channels")
        cv = QVBoxLayout(self.ch_box)
        self.ch_table = QTableWidget(0, len(CH_COLS))
        self.ch_table.setHorizontalHeaderLabels([label for _k, label in CH_COLS])
        self.ch_table.verticalHeader().hide()
        hh = self.ch_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(OPTIONS_COL, QHeaderView.Stretch)
        self.ch_table.itemChanged.connect(lambda *_: self._save_channels())
        self.ch_table.setToolTip(
            "Options (key=value, comma separated): pullup, debounce_ms, counts_per_rev, cm_per_rev, scale, offset, "
            "period_ms (1 = 1 kHz), deadband, filter (lowpass, highpass, bandpass, average) with cutoff_hz / low_hz "
            "/ high_hz / order / window; sensors: sensor (weight, light, temperature, humidity, sound (dBA), "
            "ultrasound (peak kHz), ultrasound_level (dB)), interface (analog, "
            "hx711, dht22), units, alert_min, alert_max; intensity (the output "
            "that sets a shocker's or laser's intensity) with calibration or max_ma; thermostat: sensor, heat, cool, "
            "kp, ki, kd, band, max_temp, set_cmd; olfactometer: odours=name:valve|name:valve, blank, flow, max_flow; "
            "pumps: address, syringe or diameter_mm; dripper: drop_ul.\n"
            "Role: Shocker — the output switches off after 60 s at most, whatever the procedures ask. Max on (s): "
            "the longest the output may stay on (empty: no limit, or 60 s for a shocker).")
        cv.addWidget(self.ch_table)
        cb = QHBoxLayout()
        b1 = QPushButton("Add channel")
        b1.clicked.connect(lambda: self.add_channel())
        b2 = QPushButton("Remove channel")
        b2.clicked.connect(self.remove_channel)
        b3 = QPushButton("Calibrate…")
        b3.setToolTip("Shocker or laser with an intensity output: measure the current / power at several levels")
        b3.clicked.connect(self.calibrate_channel)
        cb.addWidget(b1)
        cb.addWidget(b2)
        cb.addWidget(b3)
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
                self.dev_list.addItem(f"{c.get('name', '?')}  ·  {ioconfig.DEVICE_TYPES.get(c.get('type'), c.get('type'))}")
        if self.configs:
            self.dev_list.setCurrentRow(min(max(cur, 0), len(self.configs) - 1))
        self._load_device()

    def _cur(self) -> dict | None:
        i = self.dev_list.currentRow()
        return self.configs[i] if 0 <= i < len(self.configs) else None

    def add_device(self, type_: str = "virtual"):
        self.configs.append(ioconfig.new_device(type_, [c.get("name") for c in self.configs]))
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
            self.f_watchdog.setValue(ioconfig.watchdog_ms(c))
            self.f_safe.setValue(int(c.get("safe_mode_s") or 0))
            self.f_backend.setCurrentIndex(max(0, self.f_backend.findData(c.get("backend", "auto"))))
            self.f_enabled.setChecked(c.get("enabled", True))
            t = c.get("type", "virtual")
            for k, w in self._extra.items():
                v = c.get(k, ioconfig.DEVICE_FIELDS.get(t, {}).get(k, ""))
                if isinstance(w, QComboBox):
                    w.clear()
                    for pid, label in protocols_for(t).items():
                        w.addItem(f"{pid} — {label}", pid)
                    i = w.findData(v)
                    if i >= 0:
                        w.setCurrentIndex(i)
                    else:
                        w.setCurrentText(str(v or ""))
                else:
                    w.setText(value_text(v) if v not in (None, "") else "")
            self._fill_channels(c)
        self._update_visibility()

    def _update_visibility(self):
        t = (self._cur() or {}).get("type", "virtual")
        for key, w in self._rows.items():
            show = key in ioconfig.DEVICE_FIELDS.get(t, {})
            w.setVisible(show)
            lbl = self._form.labelForField(w)
            if lbl is not None:
                lbl.setVisible(show)
        self.f_safe.setVisible(t == "syringe_pump")
        lbl = self._form.labelForField(self.f_safe)
        if lbl is not None:
            lbl.setVisible(t == "syringe_pump")
        shown = {"name", "kind", "options", "role", "max_on_s", *ioconfig.CHANNEL_FIELDS.get(t, ())}
        for col, (key, _label) in enumerate(CH_COLS):
            self.ch_table.setColumnHidden(col, key not in shown)
        self.ch_box.setVisible(t not in ("audio", "notify"))
        pin_col = [k for k, _l in CH_COLS].index("pin")
        hdr = self.ch_table.horizontalHeaderItem(pin_col)
        if hdr is not None:
            hdr.setToolTip(ioconfig.PIN_HELP.get(t, ""))

    def _save_device(self, retype=False):
        c = self._cur()
        if self._loading or c is None:
            return
        c["name"] = self.f_name.text().strip() or c.get("name", "device")
        c["type"] = self.f_type.currentData()
        c["enabled"] = self.f_enabled.isChecked()
        fields = ioconfig.DEVICE_FIELDS[c["type"]]
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
        for k, w in self._extra.items():
            if k not in fields or retype:
                continue
            if isinstance(w, QComboBox):
                v = w.currentData() if w.currentIndex() >= 0 and w.currentText() == w.itemText(w.currentIndex()) \
                    else w.currentText().split(" — ")[0].strip()
            else:
                v = w.text().strip()
                if isinstance(fields[k], int) and v.lstrip("-").isdigit():
                    v = int(v)
            c[k] = v
        if c["type"] != "syringe_pump":
            c.pop("safe_mode_s", None)
        if retype:  # a new type: its own defaults for the fields it did not have
            for k, v in ioconfig.DEVICE_FIELDS.get(c["type"], {}).items():
                if v is not None and k not in c:
                    c[k] = v
        it = self.dev_list.currentItem()
        if it is not None:
            it.setText(f"{c['name']}  ·  {ioconfig.DEVICE_TYPES.get(c['type'], c['type'])}")
        if retype:
            self._load_device()

    def _watchdog_changed(self, v):
        """Only an explicit change is stored: without the key the core default follows the board's outputs."""
        c = self._cur()
        if not self._loading and c is not None:
            c["watchdog_ms"] = v

    def _safe_mode_changed(self, v):
        """New Era pumps' safe mode (seconds; 0 = off: not stored)."""
        c = self._cur()
        if self._loading or c is None or c.get("type") != "syringe_pump":
            return
        if v:
            c["safe_mode_s"] = int(v)
        else:
            c.pop("safe_mode_s", None)

    # ------------------------------------------------------------------ channels
    def _fill_channels(self, c):
        self.ch_table.setRowCount(0)
        for ch in c.get("channels", []) or []:
            self._append_channel_row(ch)

    def _append_channel_row(self, ch):
        r = self.ch_table.rowCount()
        self.ch_table.insertRow(r)
        kind = QComboBox()
        for k, label in ioconfig.CHANNEL_KINDS.items():
            if k != "status" or ch.get("kind") == "status":  # status channels are made by the drivers
                kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(ch.get("kind", "input"))))
        kind.currentIndexChanged.connect(lambda *_: self._save_channels())
        role = QComboBox()
        for k, label in CH_ROLES.items():
            role.addItem(label, k)
        r0 = str(ch.get("role", "") or "").lower()
        if r0 and role.findData(r0) < 0:
            role.addItem(r0, r0)  # a role this list does not know: kept
        role.setCurrentIndex(max(0, role.findData(r0)))
        role.currentIndexChanged.connect(lambda *_: self._save_channels())
        inv = QTableWidgetItem()
        inv.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        inv.setCheckState(Qt.Checked if ch.get("invert") else Qt.Unchecked)
        for col, (key, _label) in enumerate(CH_COLS):
            if key == "kind":
                self.ch_table.setCellWidget(r, col, kind)
            elif key == "role":
                self.ch_table.setCellWidget(r, col, role)
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
        if "pin" in ioconfig.CHANNEL_FIELDS.get(c.get("type"), ()) and "pin" not in ch:
            ch["pin"] = ioconfig.next_free_pin(c)
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
            role = t.cellWidget(r, col["role"])
            if role is not None and role.currentData():
                ch["role"] = role.currentData()
            try:
                cap = float(txt(r, "max_on_s") or 0)
            except ValueError:
                cap = 0.0
            if cap > 0:
                ch["max_on_s"] = int(cap) if cap.is_integer() else cap
            # driver options; the columns win over options named like them
            ch.update({k: v for k, v in _parse_options(txt(r, "options")).items() if k not in _CH_KEYS})
            out.append(ch)
        c["channels"] = out
        if "watchdog_ms" not in c:  # the default depends on whether the board has outputs
            with loading(self):
                self.f_watchdog.setValue(ioconfig.watchdog_ms(c))

    def calibrate_channel(self):
        c = self._cur()
        r = self.ch_table.currentRow()
        if c is None or not 0 <= r < len(c.get("channels", [])):
            QMessageBox.information(self, "Calibrate", "Select the shocker (or laser) channel first.")
            return
        ch = c["channels"][r]
        if not ch.get("intensity"):
            QMessageBox.information(self, "Calibrate", f"'{ch.get('name')}' has no intensity option: add "
                                    "intensity=<the PWM / analogue output that sets its current> to its options.")
            return
        manager = self.manager if self.manager is not None and self.manager.has(c.get("name")) else None
        dlg = CalibrationDialog(ch, c.get("name"), manager, self)
        if dlg.exec() == QDialog.Accepted:
            ch["calibration"] = dlg.table_text()
            with loading(self):
                self._fill_channels(c)

    # ------------------------------------------------------------------ live status
    def toggle_connection(self):
        if self.manager is not None:
            self.disconnect_devices()
        else:
            self.connect_devices()

    def connect_devices(self) -> bool:
        self._save_channels()
        self.disconnect_devices()
        m, problems = open_device_manager(self, self.configs)
        if m is None:
            QMessageBox.warning(self, "I/O devices", "\n".join(problems))
            return False
        self.manager = m
        self.connect_btn.setText("Disconnect")
        self._refresh_status(force=True)
        self.timer.start()
        if problems:
            QMessageBox.warning(self, "I/O devices", "\n".join(problems[-10:]))
        return True

    def disconnect_devices(self):
        self.timer.stop()
        if self.manager is not None:
            self.manager.close()
        self.manager = None
        self._output_errors = []
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
                for col, text in enumerate((d, c, ioconfig.CHANNEL_KINDS.get(k, k))):
                    self.status.setItem(r, col, QTableWidgetItem(text))
                self.status.setItem(r, 3, QTableWidgetItem(""))
                dev = m.devices.get(d)
                if k in ioconfig.OUTPUT_KINDS or (k == "input" and isinstance(dev, iod.VirtualDevice)):
                    b = QPushButton("Toggle" if k in ioconfig.OUTPUT_KINDS else "Simulate")
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
        errs = m.errors + self._output_errors
        self.conn_lbl.setText("; ".join(errs[-3:]) if errs else f"Connected: {len(m.devices)} device(s)")
        self.conn_lbl.setStyleSheet("color:#dc2626" if errs else "color:#15803d")

    def _set_output(self, m, device, channel, value) -> bool:
        """Set an output of the connected manager `m`, unless it was disconnected meanwhile; failures are shown."""
        if m is None or m is not self.manager:
            return False
        try:
            ok = m.set_output(device, channel, value) is not False
        except Exception as e:
            ok, msg = False, f"{device} · {channel}: {e}"
        else:
            msg = f"{device} · {channel}: the output could not be set"
        if not ok and msg not in self._output_errors:
            self._output_errors.append(msg)
        return ok

    def toggle_channel(self, device, channel, kind):
        m = self.manager
        if m is None:
            return
        dev = m.devices.get(device)
        if kind in ioconfig.OUTPUT_KINDS:
            self._set_output(m, device, channel, 0 if dev.outputs.get(channel) else 1)
        else:
            m.set_input(device, channel, 0 if dev.inputs.get(channel) else 1)
        self._poll()

    def test_selected(self):
        if self.manager is None and not self.connect_devices():
            return
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
            outs = [key for key in self._status_keys if key[2] in ioconfig.OUTPUT_KINDS]
            if not outs:
                self.conn_lbl.setText("Select an output to test")
                return
            d, ch, k = outs[0]
        if k not in ioconfig.OUTPUT_KINDS:
            self.conn_lbl.setText(f"{ch} is an input: press the lever / break the beam to see it change")
            return
        if self._set_output(m, d, ch, 1):
            # switched off by the same manager only: it may have been disconnected (closed) within the 500 ms
            QTimer.singleShot(500, lambda: m is self.manager and (self._set_output(m, d, ch, 0),
                                                                  self._refresh_status()))
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
        if not self.check_bandwidth():
            return
        if self.configs != list(self.project.io_devices or []):
            self.project.io_devices[:] = copy.deepcopy(self.configs)
            self.changed.emit()
        self.disconnect_devices()
        super().accept()

    def bandwidth_problems(self) -> tuple[list[str], bool]:
        """The devices whose inputs could saturate their serial link (iodevices.bandwidth_check): ([messages],
        refused — a configuration the board would not run as asked)."""
        msgs, refused = [], False
        for c in self.configs:
            if c.get("enabled", True) is False:
                continue
            m, refuse = iod.bandwidth_check(c)
            msgs += [f"{c.get('name', '?')}: {x}" for x in m]
            refused = refused or refuse
        return msgs, refused

    def check_bandwidth(self) -> bool:
        """Before the configuration is accepted: a refusal is shown and the dialog stays open (sample the inputs
        less often); a warning asks whether to keep the configuration."""
        msgs, refused = self.bandwidth_problems()
        if not msgs:
            return True
        text = "\n\n".join(msgs)
        if refused:
            QMessageBox.warning(self, "I/O devices", f"{text}\n\nChange the inputs' period_ms (or the baud rate) "
                                "before keeping this configuration.")
            return False
        return QMessageBox.question(self, "I/O devices", f"{text}\n\nKeep this configuration?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def reject(self):
        self.disconnect_devices()
        super().reject()

    def closeEvent(self, e):
        self.disconnect_devices()
        super().closeEvent(e)

    def sizeHint(self):
        return QSize(860, 640)


class CalibrationDialog(QDialog):
    """Calibration of an intensity output (shocker current in mA, laser power): the output is set to each level in
    turn (when connected) and the measured value is typed in; stored as the channel's ``calibration`` option."""

    LEVELS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)

    def __init__(self, ch: dict, device: str, manager=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Calibrate {ch.get('name')}")
        self.ch, self.device, self.manager = ch, device, manager
        pts = dict(ioconfig.calibration_points(ch.get("calibration"))) or {}
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Set each level of <b>{ch['intensity']}</b>, measure the output (e.g. the shock "
                           "current with a meter across a test resistor) and type the value in."
                           + ("" if manager else "<br><i>Connect the devices to set the levels from here.</i>")))
        v.itemAt(0).widget().setWordWrap(True)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Level (0–1)", "Measured (mA)", ""])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for lv in sorted(set(self.LEVELS) | set(pts)):
            self._add_row(lv, pts.get(lv))
        v.addWidget(self.table)
        hb = QHBoxLayout()
        add = QPushButton("Add level")
        add.clicked.connect(lambda: self._add_row(0.5, None))
        hb.addWidget(add)
        hb.addStretch()
        v.addLayout(hb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_row(self, level, value):
        r = self.table.rowCount()
        self.table.insertRow(r)
        lv = QDoubleSpinBox()
        lv.setRange(0, 1)
        lv.setDecimals(3)
        lv.setSingleStep(0.05)
        lv.setValue(level)
        val = QDoubleSpinBox()
        val.setRange(-1, 1000)
        val.setDecimals(3)
        val.setSpecialValueText("—")
        val.setValue(value if value is not None else -1)
        b = QPushButton("Set")
        b.setEnabled(self.manager is not None)
        b.clicked.connect(lambda _=False, w=lv: self.set_level(w.value()))
        self.table.setCellWidget(r, 0, lv)
        self.table.setCellWidget(r, 1, val)
        self.table.setCellWidget(r, 2, b)

    def set_level(self, level: float, quiet: bool = False) -> bool:
        if self.manager is None:
            return False
        try:
            ok = self.manager.set_output(self.device, self.ch["intensity"], level) is not False
        except Exception as e:
            ok, msg = False, str(e)
        else:
            msg = "the output could not be set"
        if not ok and not quiet:
            QMessageBox.warning(self, "Calibrate", f"{self.device} · {self.ch['intensity']}: {msg}")
        return ok

    def points(self) -> list[tuple[float, float]]:
        out = {}
        for r in range(self.table.rowCount()):
            lv, val = self.table.cellWidget(r, 0).value(), self.table.cellWidget(r, 1).value()
            if val >= 0:
                out[round(lv, 4)] = round(val, 4)
        return sorted(out.items())

    def table_text(self) -> str:
        return "|".join(f"{lv:g}:{v:g}" for lv, v in self.points())

    def done(self, r):
        if self.manager is not None:
            self.set_level(0, quiet=True)
        super().done(r)
