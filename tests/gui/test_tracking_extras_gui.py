"""GUI for the tracking-derived extras: the body outline in the test view, rearing / activity / outline settings."""

import shutil

import numpy as np
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.measures import AnalysisSettings
from manymaze.core.project import Project
from manymaze.core.tracking import ANIMAL_COLORS, DetectionSettings
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.base import (ANALYSIS_SECTIONS, ANALYSIS_SPEC, DETECTION_SECTIONS, DETECTION_SPEC,
                                     SettingsForm)

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=6)
    return d


def test_outline_drawn_in_test_view(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.set_project(Project.load(d))
    w.open_test(w.project.tests[0].id)
    v = w.page("TestViewPage")
    QTest.qWait(50)
    try:
        tr = v.tracks[0]
        assert tr.has_outline()  # tracked by the demo, saved and loaded back from the track file
        v.chk_trail.setChecked(False)
        v.player.seek_time(3.0)
        raw = v.player.current_frame
        shown = v._overlay(v.player.index, raw)
        j = v.sample_at(tr, 3.0)
        poly = tr.outline[j]
        col = np.array(ANIMAL_COLORS[0])
        # the outline is drawn in the animal's colour through its vertices
        hits = sum(bool((np.abs(shown[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3).astype(int) - col)
                         .max(axis=1) < 40).any()) for x, y in poly)
        assert hits >= len(poly) // 2  # (head and tail markers cover a few)
        # an old track without outlines still draws
        tr.outline = None
        assert v._overlay(v.player.index, raw).shape == raw.shape
    finally:
        v.player.close_video()
        w.dirty = False
        w.close()


def test_settings_forms_edit_the_new_settings():
    a = AnalysisSettings()
    f = SettingsForm(ANALYSIS_SPEC, a, sections=ANALYSIS_SECTIONS)
    assert {"Activity", "Rearing"} <= set(f.forms)
    f.editors["rearing"].setChecked(True)
    f.editors["rear_area_pct"].setValue(60)
    f.editors["activity_threshold_pct"].setValue(7.5)
    assert a.rearing and a.rear_area_pct == 60 and a.activity_threshold_pct == 7.5
    s = DetectionSettings()
    g = SettingsForm(DETECTION_SPEC, s, sections=DETECTION_SECTIONS)
    assert g.editors["record_outline"].isChecked()
    g.editors["record_outline"].setChecked(False)
    assert s.record_outline is False
