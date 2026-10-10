"""I/O device configurations (``Project.io_devices``, see iodevices): device types, channel kinds, the fields
each device type uses, the rules for new devices, pins and the Arduino watchdog, and the operant chamber
presets."""

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
# sensor types and their units: sound level (a sound level meter's analogue output, A-weighted), ultrasound (a
# bat / USV detector's peak frequency and its level)
SENSOR_TYPES = {"weight": "g", "light": "lux", "temperature": "°C", "humidity": "%", "sound": "dBA",
                "ultrasound": "kHz", "ultrasound_level": "dB", "generic": ""}
DECIBEL_SENSORS = ("sound", "ultrasound_level")  # levels in dB: their equivalent level (Leq) is also reported
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
# device fields holding passwords / tokens: kept out of project.json (io-secrets.json), reports, archives and
# protocol copies (see is_secret)
SECRET_FIELDS = ("smtp_password", "twilio_token")
_SECRET_SUFFIXES = ("password", "_token", "_secret", "api_key")


def is_secret(key: str) -> bool:
    """A device configuration field that holds a password or token (also those of device types added later:
    names ending in password, _token, _secret or api_key)."""
    k = str(key).lower()
    return k in SECRET_FIELDS or k.endswith(_SECRET_SUFFIXES)


CHANNEL_FIELDS = {"virtual": (),"arduino": ("pin", "pin_b", "invert"), "serial": ("on", "off"), "audio": (),
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


# ---------------------------------------------------------------- operant chamber presets
# The named inputs and outputs of typical operant chambers, to set up the I/O device of each chamber at once
# (Protocol ▸ Mode Input/output only, Experiment › I/O devices). A preset describes the user's own wiring of the
# chamber's levers, nose pokes, lights, dispenser and shocker to one of the drivers above (the mANY-MAZE Arduino
# firmware, Firmata, NI-DAQmx or LabJack, or a simulated device): the vendors' own interface cards and software
# (Med Associates MED-PC, Coulbourn Habitest, Lafayette ABET) are not driven. Channels are (name, kind, role); the
# role is the I/O devices dialog's (a shocker's outputs switch off after 60 s at most, see iodevices.Device).
# Names are those of the example procedures where they exist (lever, pellet, house_light, shocker).
OPERANT_PRESETS: dict[str, dict] = {
    "med_associates": {
        "label": "Med Associates-style chamber",
        "description": "Two retractable levers with a cue light above each, a pellet receptacle with a head entry "
                       "detector, a house light, a pellet dispenser, a tone (Sonalert) and a grid floor shocker.",
        "channels": [("left_lever", "input", ""), ("right_lever", "input", ""), ("head_entry", "input", ""),
                     ("left_lever_out", "output", ""), ("right_lever_out", "output", ""),
                     ("left_light", "output", "light"), ("right_light", "output", "light"),
                     ("house_light", "output", "light"), ("pellet", "output", ""), ("tone", "output", "speaker"),
                     ("shocker", "output", "shocker")]},
    "coulbourn": {
        "label": "Coulbourn-style chamber",
        "description": "A retractable lever with a cue light, two nose pokes with their lights, a feeder trough "
                       "with a head entry beam, a house light, a pellet feeder, a tone and a shocker.",
        "channels": [("lever", "input", ""), ("left_poke", "input", ""), ("right_poke", "input", ""),
                     ("head_entry", "input", ""), ("lever_out", "output", ""), ("cue_light", "output", "light"),
                     ("left_poke_light", "output", "light"), ("right_poke_light", "output", "light"),
                     ("house_light", "output", "light"), ("pellet", "output", ""), ("tone", "output", "speaker"),
                     ("shocker", "output", "shocker")]},
    "lafayette": {
        "label": "Lafayette-style chamber",
        "description": "A wall of five nose-poke holes with a light in each (five-choice serial reaction time "
                       "task), a food magazine with a head entry beam and a light, a house light, a pellet "
                       "dispenser and a tone.",
        "channels": [*((f"poke_{i}", "input", "") for i in range(1, 6)), ("head_entry", "input", ""),
                     *((f"poke_{i}_light", "output", "light") for i in range(1, 6)),
                     ("magazine_light", "output", "light"), ("house_light", "output", "light"),
                     ("pellet", "output", ""), ("tone", "output", "speaker")]},
    "custom": {
        "label": "Custom chamber",
        "description": "No channels: add the chamber's inputs and outputs yourself (Experiment › I/O devices).",
        "channels": []},
}
# the drivers a preset can be wired to (the simulated device lets the protocol be tried without hardware)
PRESET_DEVICE_TYPES = ("arduino", "firmata", "nidaq", "labjack", "virtual")
PRESET_KEY = "operant_preset"  # Project.settings_extra: the preset the protocol's chambers were set up with
# LabJack T4 / T7 digital lines in order (FIO, EIO, CIO, MIO)
_LABJACK_LINES = ([f"FIO{i}" for i in range(8)] + [f"EIO{i}" for i in range(8)] + [f"CIO{i}" for i in range(4)]
                  + [f"MIO{i}" for i in range(3)])


def preset_pins(type_: str, n_in: int, n_out: int) -> tuple[list, list]:
    """The pins of a chamber's inputs and outputs for a device type: Arduino / Firmata pins from 2 (inputs first;
    on an Uno pins 14–19 are A0–A5), NI lines port0/lineN for the inputs and port1, port2 … for the outputs,
    LabJack lines FIO0… then EIO, CIO and MIO; none for a simulated device."""
    if type_ in ("arduino", "firmata"):
        pins = list(range(2, 2 + n_in + n_out))
        return pins[:n_in], pins[n_in:]
    if type_ == "nidaq":
        first = (n_in + 7) // 8  # the outputs start on the port after the inputs'
        return ([f"port{i // 8}/line{i % 8}" for i in range(n_in)],
                [f"port{first + i // 8}/line{i % 8}" for i in range(n_out)])
    if type_ == "labjack":
        lines = _LABJACK_LINES + [f"DIO{i}" for i in range(len(_LABJACK_LINES), n_in + n_out)]
        return lines[:n_in], lines[n_in:n_in + n_out]
    return [None] * n_in, [None] * n_out


def operant_device(preset: str, type_: str = "arduino", name: str = "chamber", taken=()) -> dict:
    """The I/O device of one chamber of a preset (OPERANT_PRESETS) for a device type (PRESET_DEVICE_TYPES): a new
    device of that type with the preset's named inputs and outputs, roles and pins. Its name is `name`, with a
    number if taken."""
    if preset not in OPERANT_PRESETS:
        raise ValueError(f"unknown operant chamber preset '{preset}'")
    if type_ not in PRESET_DEVICE_TYPES:
        raise ValueError(f"an operant chamber cannot use a device of type '{type_}'")
    cfg = new_device(type_, taken)
    base, k = name, 2
    while name in set(taken):
        name, k = f"{base}{k}", k + 1
    cfg["name"] = name
    chans = OPERANT_PRESETS[preset]["channels"]
    ins, outs = preset_pins(type_, sum(k == "input" for _n, k, _r in chans),
                            sum(k == "output" for _n, k, _r in chans))
    ins, outs = iter(ins), iter(outs)
    for n, kind, role in chans:
        ch = {"name": n, "kind": kind}
        pin = next(ins if kind == "input" else outs)
        if pin is not None:
            ch["pin"] = pin
        if role:
            ch["role"] = role
        cfg["channels"].append(ch)
    return cfg


def operant_devices(preset: str, type_: str = "arduino", n: int = 1, taken=()) -> list[dict]:
    """The I/O devices of `n` chambers of a preset, one device per chamber (each test of Several tests at once
    uses its own device): "chamber" for one, else "chamber1", "chamber2" … (the numbers of names not taken)."""
    names, out, k = set(taken), [], 1
    for _ in range(max(1, int(n))):
        if n == 1 and "chamber" not in names:
            name = "chamber"
        else:
            while f"chamber{k}" in names:
                k += 1
            name = f"chamber{k}"
        names.add(name)
        out.append(operant_device(preset, type_, name))
    return out
