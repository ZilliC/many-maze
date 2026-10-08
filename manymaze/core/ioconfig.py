"""I/O device configurations (``Project.io_devices``, see iodevices): device types, channel kinds, the fields
each device type uses, and the rules for new devices, pins and the Arduino watchdog."""

from __future__ import annotations

DEVICE_TYPES = {
    "virtual": "Simulated device",
    "arduino": "Arduino (mANY-MAZE I/O firmware)",
    "serial": "Serial port (text commands)",
    "audio": "Audio output (speakers)",
    "serial_lines": "USB-serial cable control lines (TTL cable)",
    "firmata": "Firmata board (StandardFirmata)",
    "nidaq": "National Instruments DAQ (NI-DAQmx)",
    "labjack": "LabJack T4 / T7 / T8 (LJM)",
    "notify": "Alerts (e-mail / SMS)",
    "syringe_pump": "Syringe pump(s)",
    "scale": "Balance (animal / food weights)",
}
CHANNEL_KINDS = {
    "input": "Digital input",
    "output": "Digital output",
    "pwm": "PWM / analogue output",
    "analog": "Analogue input",
    "encoder": "Rotary encoder",
    "pir": "Movement detector (PIR)",
    "sensor": "Sensor (weight, light, temperature, humidity)",
    "thermostat": "Temperature controller",
    "odour": "Odour delivery (olfactometer)",
    "pump": "Syringe pump",
    "status": "Device status",
}
INPUT_KINDS = ("input", "analog", "encoder", "pir", "sensor", "status")
DIGITAL_INPUT_KINDS = ("input", "pir", "status")  # on / off
VALUE_KINDS = ("analog", "encoder", "sensor")  # a number
OUTPUT_KINDS = ("output", "pwm")
CONTROL_KINDS = ("thermostat", "odour", "pump")  # driven by their own actions (set temperature, odour, pump)
SENSOR_TYPES = {"weight": "g", "light": "lux", "temperature": "°C", "humidity": "%", "generic": ""}
# where a sensor channel's readings come from: an analogue pin, an HX711 load-cell amplifier or a DHT22
SENSOR_INTERFACES = ("analog", "hx711", "dht22")
# derived status channels "<channel>.<suffix>" that drivers and controllers report, and the procedure event each
# one fires when it switches on
STATUS_EVENTS = {"at_target": "temperature_reached", "stalled": "pump_stalled",
                 "target_reached": "pump_target_reached"}
AUDIO_BACKENDS = ("auto", "none", "afplay", "paplay", "aplay")

# configuration fields of each device type (besides name, type, enabled and channels), with the defaults of a new
# device, and the channel fields it uses (besides name, kind and driver options such as pullup or debounce_ms)
DEVICE_FIELDS = {
    "virtual": {},
    "arduino": {"port": "", "baud": 115200, "watchdog_ms": None},  # None: not stored, see watchdog_ms()
    "serial": {"port": "", "baud": 9600},
    "audio": {"backend": "auto"},
    "serial_lines": {"port": ""},
    "firmata": {"port": "", "baud": 57600},
    "nidaq": {"device_id": "Dev1"},
    "labjack": {"identifier": "ANY"},
    "notify": {"smtp_host": "", "smtp_port": 587, "smtp_user": "", "smtp_password": "", "from_addr": "",
               "email_to": "", "sms_to": "", "twilio_sid": "", "twilio_token": "", "twilio_from": ""},
    "syringe_pump": {"port": "", "baud": 9600, "protocol": "new_era"},
    "scale": {"port": "", "baud": 9600, "protocol": "mt_sics"},
}
CHANNEL_FIELDS = {"virtual": (), "arduino": ("pin", "pin_b", "invert"), "serial": ("on", "off"), "audio": (),
                  "serial_lines": ("pin", "invert"), "firmata": ("pin", "invert"), "nidaq": ("pin", "invert"),
                  "labjack": ("pin", "invert"), "notify": (), "syringe_pump": (), "scale": ()}
_NAME_BASES = {"virtual": "sim", "arduino": "box", "serial": "serial", "audio": "speakers", "serial_lines": "ttl",
               "firmata": "firmata", "nidaq": "daq", "labjack": "labjack", "notify": "alerts",
               "syringe_pump": "pumps", "scale": "balance"}
# what "pin" means for each device type (help text of the channel table)
PIN_HELP = {
    "arduino": "Arduino pin number (analogue inputs: 0 = A0)",
    "serial_lines": "control line: RTS or DTR (outputs), CTS, DSR, RI or CD (inputs)",
    "firmata": "Arduino pin number (analogue inputs: 0 = A0)",
    "nidaq": "physical channel without the device, e.g. port0/line0, ai0, ao0",
    "labjack": "register name, e.g. FIO0, EIO2, AIN0, DAC0",
}


def new_device(type_: str, taken=()) -> dict:
    """Configuration of a new device of a type, named "sim", "box", "serial" or "speakers" (with a number if the
    name is taken)."""
    base = _NAME_BASES.get(type_, type_)
    name, k = base, 2
    while name in set(taken):
        name, k = f"{base}{k}", k + 1
    fields = {f: v for f, v in DEVICE_FIELDS.get(type_, {}).items() if v is not None}
    return {"name": name, "type": type_, "enabled": True, "channels": [], **fields}


def next_free_pin(cfg: dict, first: int = 2, last: int = 69) -> int:
    """The lowest digital pin no channel of a board uses (pins 0 and 1 are the Arduino's serial port)."""
    used = {int(c["pin"]) for c in cfg.get("channels", []) if str(c.get("pin", "")).isdigit()
            and c.get("kind") not in ("analog", "sensor")}
    return next(p for p in range(first, last + 1) if p not in used)


DEFAULT_WATCHDOG_MS = 2000


def watchdog_ms(cfg: dict) -> int:
    """An Arduino's heartbeat watchdog timeout: the configured ``watchdog_ms`` (0 = off) or, when not set, 2000 ms
    if the board drives outputs (all outputs go off if mANY-MAZE stops talking to the board)."""
    v = cfg.get("watchdog_ms")
    if v is None:
        has_out = any(c.get("name") and c.get("kind", "input") in OUTPUT_KINDS
                      for c in cfg.get("channels", []) or [])
        return DEFAULT_WATCHDOG_MS if has_out else 0
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def register_device_type(type_: str, label: str, fields: dict | None = None, channel_fields=(), name_base=None):
    """Add a device type (drivers in other modules, e.g. syringe pumps and balances)."""
    DEVICE_TYPES[type_] = label
    DEVICE_FIELDS[type_] = dict(fields or {})
    CHANNEL_FIELDS[type_] = tuple(channel_fields)
    _NAME_BASES[type_] = name_base or type_


def channel_kinds_of(param_type: str) -> tuple:
    """The channel kinds a procedure parameter type accepts ("input", "output", "sensor", "thermostat"...)."""
    return {"input": INPUT_KINDS, "output": OUTPUT_KINDS, "sensor": ("sensor", "analog"),
            "thermostat": ("thermostat",), "odour": ("odour",), "pump": ("pump",)}.get(param_type, ())


def parse_pairs(v, sep="|") -> list[tuple[str, str]]:
    """Pairs written "a:b|c:d" in a channel option (or given as a dict / list of pairs)."""
    if isinstance(v, dict):
        return [(str(k), str(x)) for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        return [(str(a), str(b)) for a, b in v]
    out = []
    for part in str(v or "").split(sep):
        if ":" in part:
            a, b = (x.strip() for x in part.split(":", 1))
            if a:
                out.append((a, b))
    return out


def calibration_points(v) -> list[tuple[float, float]]:
    """A calibration table "level:value|level:value" (e.g. shocker output level 0..1 : current in mA), sorted."""
    pts = []
    for a, b in parse_pairs(v):
        try:
            pts.append((float(a), float(b)))
        except ValueError:
            continue
    return sorted(pts)


def level_for(value: float, points, max_level: float = 1.0) -> float:
    """The output level that gives `value` according to a calibration table (linear interpolation; without a
    table the value is taken as the level). Values outside the table are clamped to its range."""
    pts = sorted(points or [], key=lambda p: p[1])
    if len(pts) < 2:
        return max(0.0, min(max_level, float(value)))
    if value <= pts[0][1]:
        return pts[0][0]
    for (l0, v0), (l1, v1) in zip(pts, pts[1:]):
        if value <= v1:
            return l0 + (l1 - l0) * ((value - v0) / (v1 - v0) if v1 != v0 else 0.0)
    return pts[-1][0]
