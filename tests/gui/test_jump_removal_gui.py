"""Jump removal in the GUI: the two detection settings (protocol and per test) and Review's Track editing ▸ Remove
jumps, which removes the jumps of a tracked test with its settings and fills them (undoable)."""

import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from manymaze.core.demo import create_demo_project
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def pump(n=5):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def test_remove_jumps_in_review(tmp_path):
    p = create_demo_project(tmp_path / "d.mmaze", n_per_group=1, seconds=4)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(p)
    try:
        # the protocol's animal tracking form has the settings
        ex = w.goto("ExperimentPage")
        ed = ex.det_form.editors["max_jump_speed"]
        ed.setValue(150.0)
        assert p.detection.max_jump_speed == 150.0 and "max_jump_s" in ex.det_form.editors
        # a reflection in the track for two frames
        t = p.tests[0]
        tr = p.load_tracks(t)[0]
        i = len(tr) // 2
        x0 = float(tr.x[i])
        between = tr.x[i - 1] + (tr.x[i + 2] - tr.x[i - 1]) * (tr.t[i] - tr.t[i - 1]) / (tr.t[i + 2] - tr.t[i - 1])
        app_ = p.apparatus_of(t)
        far = (x0 + 30 / app_.scale) if x0 * app_.scale < 20 else (x0 - 30 / app_.scale)  # 30 cm away
        tr.x[i:i + 2] = far
        p.save_tracks(t, [tr])
        w.open_test(t.id)
        v = w.page("TestViewPage")
        pump()
        assert "Remove jumps" in [b.defaultAction().text() for g in w.sections[w._page_section[id(v)]].panel.context
                                  for b in g.buttons]
        assert v.remove_jumps() == 1 and "Removed 1 jump" in v.edit_lbl.text()
        saved = p.load_tracks(t)[0]
        assert abs(saved.x[i] - between) < 0.01 and not saved.detected[i] and saved.meta["jumps_removed"] == "1"
        assert p.analyse_test(t)[0]["Jumps removed"] == 1
        assert v.remove_jumps() == 0 and v.edit_lbl.text() == "No jumps found."
        v.undo_edit()
        assert p.load_tracks(t)[0].x[i] == pytest.approx(far, abs=1e-3)  # (CSV precision)
        # without the setting, Review says where to set it
        t.detection = {"max_jump_speed": 0.0}
        assert v.remove_jumps() is None and "Remove jumps faster than" in v.edit_lbl.text()
        assert np.isfinite(p.load_tracks(t)[0].x).all()
    finally:
        w.dirty = False
        w.close()
