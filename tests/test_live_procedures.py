"""Procedures inside live sessions: pause action, scored behaviours as events, state marks, result variables."""

import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.live import LiveSession
from manymaze.core.tracking import DetectionSettings

PROCS = [
    {"name": "Pause at 1 s", "statements": [
        {"type": "wait", "seconds": 1},
        {"type": "do", "action": "pause_test"}]},
    {"name": "Respond to rearing", "statements": [
        {"type": "var", "name": "responses", "value": 0, "result": True},
        {"type": "when", "event": "event_marked", "name": "Rearing", "body": [
            {"type": "do", "action": "mark_start", "name": "Light"},
            {"type": "wait", "seconds": 0.4},
            {"type": "do", "action": "mark_end", "name": "Light"},
            {"type": "set", "var": "responses", "value": "responses + 1"}]}]},
]


def _frame(x):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, 100, 0)
    return np.dstack([img] * 3)


def test_procedures_drive_live_session():
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    shared = {}
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=3.0, procedures=PROCS,
                    variables=shared)
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    i, paused_at = 0, None
    while s.state != "finished" and i < 400:
        if i == 5:
            s.score("Rearing")  # point event → procedures see "event marked"
        if s.state == "paused" and paused_at is None:
            paused_at = i
        if paused_at is not None and i == paused_at + 10:
            assert s.resume()
        s.process(_frame(60 + (i % 60)), i / 25)
        i += 1
    assert paused_at is not None and s.pauses and s.pauses[0][0] == pytest.approx(1.0, abs=0.05)
    light = [e for e in s.events if e["behaviour"] == "Light"]
    assert len(light) == 1 and light[0]["t_end"] - light[0]["t"] == pytest.approx(0.4, abs=0.05)
    assert s.result_variables == {"responses": 1}
    assert not s.engine.errors
