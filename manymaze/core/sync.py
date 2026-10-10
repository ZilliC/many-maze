"""The synchronisation element (ANY-maze's Synchronisation, Protocol ▸ Hardware): TTL pulses on a digital output so
that another recording system — electrophysiology, imaging, fibre photometry, a second camera — can align its data
with the test: a pulse when the test starts and when it ends, a pulse for every captured frame and / or for every
position stored in the track.

``Project.sync``::

    {"enabled": True, "device": "box1", "channel": "sync", "test_start": True, "test_end": True,
     "per_frame": False, "per_position": False, "width_ms": 1.0}

(``device`` empty: the first device that has the channel). Pulses go out by the device's fastest path
(iodevices.DeviceManager.sync_pulse): the Arduino firmware's ``SYNC`` command (the width timed by a timer interrupt),
a LabJack's own timing, otherwise the output switched on and off by the computer.

Pulses due at the same moment are sent as one: a frame's pulse also marks the test start (the test's first frame),
the position stored in that frame and the test end (its last frame), so with *per frame* the number of pulses equals
the number of frames of the test. :class:`SyncOutput` counts the pulses of each kind.
"""

from __future__ import annotations

from .ioconfig import OUTPUT_KINDS

SYNC_DEFAULTS = {"enabled": False, "device": "", "channel": "", "test_start": True, "test_end": True,
                 "per_frame": False, "per_position": False, "width_ms": 1.0}
# what a pulse marks: (Project.sync option, label)
EVENTS = {"test_start": ("test_start", "test start"), "test_end": ("test_end", "test end"),
          "frame": ("per_frame", "frames"), "position": ("per_position", "positions")}
MIN_WIDTH_MS, MAX_WIDTH_MS = 0.001, 1000.0


def sync_from(d) -> dict:
    """``Project.sync`` with its defaults (anything missing or invalid replaced)."""
    out = dict(SYNC_DEFAULTS)
    if isinstance(d, dict):
        for k, v in SYNC_DEFAULTS.items():
            if k not in d:
                continue
            if isinstance(v, bool):
                out[k] = bool(d[k])
            elif isinstance(v, str):
                out[k] = str(d[k] or "").strip()
            else:
                try:
                    out[k] = min(MAX_WIDTH_MS, max(MIN_WIDTH_MS, float(d[k])))
                except (TypeError, ValueError):
                    pass
    return out


def sync_on(d) -> bool:
    """The synchronisation element is used: enabled, with an output and at least one kind of pulse."""
    s = sync_from(d)
    return bool(s["enabled"] and s["channel"] and any(s[opt] for opt, _ in EVENTS.values()))


class SyncOutput:
    """The synchronisation pulses of one test. ``pulse(what, merged)`` sends a pulse for ``what`` ("test_start",
    "test_end", "frame" or "position") when the element asks for it; with ``merged`` the pulse already sent for
    the same moment counts for it too (nothing is sent). ``counts`` / ``sent`` / ``failed`` keep the tally."""

    def __init__(self, devices, cfg: dict):
        self.cfg = sync_from(cfg)
        self.devices = devices
        self.width_s = self.cfg["width_ms"] / 1000.0
        self.counts = {k: 0 for k in EVENTS}
        self.sent = 0
        self.failed = 0
        self.problem = ""
        self.device = self.cfg["device"]
        ch = self.cfg["channel"]
        if devices is None:
            self.problem = "Synchronisation: no I/O devices are configured, no pulses are sent"
            return
        if not self.device:
            self.device = devices.find_channel(ch, OUTPUT_KINDS) or ""
        kind = devices.channel_kind(self.device, ch) if self.device else None
        if kind != "output":
            self.problem = (f"Synchronisation: '{self.label}' is not a digital output of a configured device: "
                            "no pulses are sent")

    @property
    def label(self) -> str:
        return f"{self.device}/{self.cfg['channel']}" if self.device else self.cfg["channel"]

    @property
    def ok(self) -> bool:
        return not self.problem

    def wants(self, what: str) -> bool:
        return bool(self.cfg[EVENTS[what][0]])

    def pulse(self, what: str, merged: bool = False) -> bool:
        """A pulse for ``what`` if the element asks for one; returns whether a pulse was sent (or merged)."""
        if not self.wants(what) or not self.ok:
            return False
        self.counts[what] += 1
        if merged:
            return True
        try:
            ok = self.devices.sync_pulse(self.device, self.cfg["channel"], self.width_s) is not False
        except Exception:  # pragma: no cover - hardware dependent: never stop a test for a pulse
            ok = False
        if ok:
            self.sent += 1
        else:
            self.failed += 1
        return ok

    def summary(self) -> str:
        """For the test notes: "Synchronisation pulses on box/sync (1 ms): test start 1, frames 7500 … (7500
        pulses sent)"."""
        parts = [f"{EVENTS[k][1]} {n}" for k, n in self.counts.items() if self.wants(k)]
        out = (f"Synchronisation pulses on {self.label} ({self.cfg['width_ms']:g} ms): " + ", ".join(parts)
               + f" ({self.sent} pulse{'s' if self.sent != 1 else ''} sent")
        return out + (f", {self.failed} not sent)" if self.failed else ")")
