"""I/O device configurations (``Project.io_devices``, see iodevices): device types, channel kinds, the fields
each device type uses, and the rules for new devices, pins and the Arduino watchdog."""

from __future__ import annotations

DEVICE_TYPES = {
    "virtual": "Simulated device",
    "arduino": "Arduino (mANY-MAZE I/O firmware)",
    "serial": "Serial port (text commands)",
    "audio": "Audio output (speakers)",
}
CHANNEL_KINDS = {
    "input": "Digital input",
    "output": "Digital output",
    "pwm": "PWM / analogue output",
    "analog": "Analogue input",
    "encoder": "Rotary encoder",
}
INPUT_KINDS = ("input", "analog", "encoder")
OUTPUT_KINDS = ("output", "pwm")
AUDIO_BACKENDS = ("auto", "none", "afplay", "paplay", "aplay")

# configuration fields of each device type (besides name, type, enabled and channels), with the defaults of a new
# device, and the channel fields it uses (besides name, kind and driver options such as pullup or debounce_ms)
DEVICE_FIELDS = {
    "virtual": {},
    "arduino": {"port": "", "baud": 115200, "watchdog_ms": None},  # None: not stored, see watchdog_ms()
    "serial": {"port": "", "baud": 9600},
    "audio": {"backend": "auto"},
}
CHANNEL_FIELDS = {"virtual": (), "arduino": ("pin", "pin_b", "invert"), "serial": ("on", "off"), "audio": ()}
_NAME_BASES = {"virtual": "sim", "arduino": "box", "serial": "serial", "audio": "speakers"}


def new_device(type_: str, taken=()) -> dict:
    """Configuration of a new device of a type, named "sim", "box", "serial" or "speakers" (with a number if the
    name is taken)."""
    base = _NAME_BASES[type_]
    name, k = base, 2
    while name in set(taken):
        name, k = f"{base}{k}", k + 1
    fields = {f: v for f, v in DEVICE_FIELDS[type_].items() if v is not None}
    return {"name": name, "type": type_, "enabled": True, "channels": [], **fields}


def next_free_pin(cfg: dict, first: int = 2, last: int = 69) -> int:
    """The lowest digital pin no channel of a board uses (pins 0 and 1 are the Arduino's serial port)."""
    used = {int(c["pin"]) for c in cfg.get("channels", []) if str(c.get("pin", "")).isdigit()}
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
