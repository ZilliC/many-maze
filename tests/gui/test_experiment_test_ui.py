"""ANY-maze style Experiment tab, Test schedule and Review and score: ribbon groups, explorer items and the
spreadsheet / test panel presentation."""

import shutil

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QStyleOptionViewItem

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui import theme
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.animals import treatment_code, treatment_text, two_lines
from manymaze.gui.pages.tests import C_ID, C_STATUS, READY_BG, ready_tests

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.show()
    w.set_project(Project.load(d))
    yield w
    tv = w.page("TestViewPage")
    tv.player.close_video()
    w.dirty = False
    w.close()


def group_titles(w, page):
    return [g.title for g in w.sections[w._page_section[id(page)]].panel.context]


def ribbon_texts(w, page):
    panel = w.sections[w._page_section[id(page)]].panel
    return [b.defaultAction().text() for g in panel.context for b in g.buttons]


# ---------------------------------------------------------------- Experiment tab
def test_experiment_ribbon_and_views(win, monkeypatch):
    p = win.project
    page = win.goto("AnimalsPage")
    assert group_titles(win, page) == ["Experiment", "Animals", "Fields"]
    texts = ribbon_texts(win, page)
    for t in ("View treatments", "View animals", "Add animals", "Delete animals", "Reveal treatment coding",
              "Import animals", "Import tests", "Retire", "Dose calculator", "Training criteria", "Export CSV"):
        assert t in texts
    # large buttons keep their two-line labels after the action changes
    panel = win.sections[win._page_section[id(page)]].panel
    btn = next(b for g in panel.context for b in g.buttons if b.defaultAction() is page.a_del_animals)
    page.table.selectRow(0)
    page.table.clearSelection()
    assert btn.text() == "Delete\nanimals" == two_lines("Delete animals")
    # explorer sub-items switch between the sheets
    assert page.explorer_items() == [("Treatments", "treatment", "treatments"), ("Animals", "animal", "animals")]
    sec = win.sections[win._page_section[id(page)]]
    it = sec.item_for(page, "treatments")
    assert it is not None
    sec.explorer.setCurrentItem(it)
    assert page.view == "treatments" and page.stack.currentWidget() is page.treatments
    assert page.a_view_treat.isChecked() and not page.a_view_animals.isChecked()
    assert group_titles(win, page) == ["Experiment", "Treatments"]
    assert page.treatments.rowCount() == 2
    assert [page.treatments.item(0, c).text() for c in (0, 1, 3)] == ["Control", "A", "2"]
    page.a_view_animals.trigger()
    assert page.view == "animals" and sec.explorer.currentItem() is sec.item_for(page, "animals")
    assert group_titles(win, page) == ["Experiment", "Animals", "Fields"]
    # import buttons use the import wizard
    calls = []
    monkeypatch.setattr(win, "import_table", lambda kind, path=None: calls.append(kind))
    page.a_import_animals.trigger()
    page.a_import_tests.trigger()
    assert calls == ["animals", "tests"]
    assert p.groups[0].name == "Control"


def test_animals_sheet_cells(win):
    p = win.project
    page = win.goto("AnimalsPage")
    page.add_field("Animal weight")
    heads = [page.table.horizontalHeaderItem(c).text() for c in range(page.table.columnCount())]
    assert heads == ["Animal", "Animal ID", "Status", "Treatment", "Animal weight", "Sex", "Tests"]
    assert page.table.item(0, 0).data(Qt.DisplayRole) == 1
    assert page.table.verticalHeader().defaultSectionSize() >= 30
    d = page.table.itemDelegate()
    c_tr, c_st, c_sex = page.col_of("treatment"), page.col_of("status"), page.col_of("sex")
    # treatments are shown with their code; the cells offer drop-down lists
    opt = QStyleOptionViewItem()
    d.initStyleOption(opt, page.table.model().index(0, c_tr))
    assert opt.text == "A - Control" == treatment_text(p, "Control")
    ed = d.createEditor(page.table.viewport(), opt, page.table.model().index(0, c_st))
    assert isinstance(ed, QComboBox) and [ed.itemText(i) for i in range(ed.count())] == ["Normal", "Retired"]
    ed = d.createEditor(page.table.viewport(), opt, page.table.model().index(0, c_tr))
    assert ed.isEditable() and ed.itemData(ed.findText("B - Anxious")) == "Anxious"
    d.setEditorData(ed, page.table.model().index(0, c_tr))
    ed.setCurrentIndex(ed.findText("B - Anxious"))
    d.setModelData(ed, page.table.model(), page.table.model().index(0, c_tr))
    aid = page.table.item(0, page.col_of("id")).text()
    assert p.get_animal(aid).group == "Anxious"
    ed = d.createEditor(page.table.viewport(), opt, page.table.model().index(0, c_sex))
    assert ed.findText("Female") >= 0
    # typing "B - Anxious" / a new name in the cell
    page.table.item(1, c_tr).setText("B - Anxious")
    assert p.get_animal(page.table.item(1, page.col_of("id")).text()).group == "Anxious"
    # retired animals are greyed
    page.table.item(1, c_st).setText("Retired")
    assert page.table.item(1, page.col_of("id")).foreground().color() == QColor("#9ca3af")
    # blind coding: codes only, treatments cannot be edited
    page.set_blind(True)
    assert treatment_code(p, "Anxious") == p.settings_extra["blind_codes"]["Anxious"]
    r = next(r for r in range(page.table.rowCount()) if page.table.item(r, page.col_of("id")).text() == aid)
    assert page.table.item(r, c_tr).text() == treatment_code(p, "Anxious")
    assert not page.table.item(r, c_tr).flags() & Qt.ItemIsEditable
    assert not page.a_reveal.isChecked() and not page.blind_lbl.isHidden()
    page.a_reveal.setChecked(True)  # asks, then reveals
    assert not p.blind and page.table.item(r, c_tr).text() == "Anxious"


# ---------------------------------------------------------------- Test schedule
def test_schedule_ribbon_ready_and_status(win, tmp_path, monkeypatch):
    p = win.project
    page = win.goto("TestsPage")
    assert group_titles(win, page) == ["Tests", "Testing", "Status", "Variables"]
    texts = ribbon_texts(win, page)
    for t in ("Add tests from videos", "Add test", "Schedule…", "Delete", "Review test", "Track selected",
              "Track all untracked", "Import track…", "Import track data…", "Skip", "Resume", "Re-perform",
              "Exclude", "Clear tracks", "Test variables"):
        assert t in texts
    heads = [page.model.headerData(c, Qt.Horizontal) for c in range(8)]
    assert heads == ["Test", "Animal", "Treatment", "Stage", "Trial", "Apparatus", "Video", "Testing status"]
    # the next pending test of each apparatus is Ready (pale green)
    new = page.create_tests_for([("C1", "Day 2", 1), ("C2", "Day 2", 1)], p.apparatus[0].name)
    assert ready_tests(p) == {new[0].id}
    row = p.tests.index(new[0])
    assert page.model.index(row, C_STATUS).data() == "Ready"
    assert page.model.index(row, C_ID).data(Qt.BackgroundRole).color() == QColor(READY_BG)
    assert page.model.index(row + 1, C_STATUS).data() == ""
    assert page.model.index(0, C_STATUS).data() == "Tracked"
    page.select_ids({new[0].id})
    page.skip_selected()
    assert new[0].status == "skipped" and ready_tests(p) == {new[1].id}
    assert page.model.index(row, C_STATUS).data() == "Skipped"
    assert page.model.index(row, C_ID).data(Qt.ForegroundRole).color() == QColor("#a3a3a3")
    page.resume_selected()
    assert ready_tests(p) == {new[0].id}
    # a retired animal's test is not Ready
    p.get_animal("C1").retired = True
    page.refresh()
    assert ready_tests(p) == {new[1].id}
    # track data from another program, through the import wizard
    from manymaze.gui.import_wizard import ImportDialog

    csv = tmp_path / "anymaze_positions.csv"
    rows = ["Time,Centre position X,Centre position Y"] + [f"{i / 25:.2f},{100 + i},{150}" for i in range(50)]
    csv.write_text("\n".join(rows) + "\n")
    monkeypatch.setattr(ImportDialog, "exec", lambda self: (self.accept(), QDialog.Accepted if self.result is not None
                                                            else QDialog.Rejected)[1])
    page.select_ids({new[1].id})
    assert page.a_import_data.isEnabled()
    tr = page.import_track_data(str(csv))
    assert tr is not None and len(tr) == 50
    assert new[1].status == "tracked" and p.has_track(new[1]) and win.dirty
    assert page.model.index(p.tests.index(new[1]), C_STATUS).data() == "Tracked"


# ---------------------------------------------------------------- Review and score
def test_review_panel_and_ribbon(win):
    p = win.project
    win.open_test(p.tests[0].id)
    v = win.page("TestViewPage")
    assert group_titles(win, v) == ["Test", "Playback", "Scoring", "View", "Track editing"]
    texts = ribbon_texts(win, v)
    for t in ("Previous test", "Next test", "Track this test", "Play", "Pause", "Step back", "Step forward",
              "On-screen keys", "Zones", "Animal", "Trail", "Detection preview", "Mark position", "Interpolate",
              "Delete range", "Undo"):
        assert t in texts
    # light background, ANY-maze title line
    assert v.player.view.backgroundBrush().color() == QColor(theme.WORK_BG)
    t = v.test
    v.player.seek_time(3.0)
    assert v.title_lbl.text() == f"{t.apparatus}: Animal {t.animal_id}, {t.stage} trial {t.trial} - 0:03"
    # playback
    v.set_speed(2.0)
    assert v.player.speed == 2.0 and v.a_speed.text() == "Speed 2×"
    v.a_play.trigger()
    assert v.player.playing
    v.a_stop.trigger()
    assert not v.player.playing and v.player.time == pytest.approx(v.video_start(), abs=0.05)
    v.a_fwd.trigger()
    assert v.player.index == int(round(v.video_start() * v.player.fps)) + 1
    # view toggles
    v.player.seek_time(4.0)
    with_trail = v._overlay(v.player.index, v.player.current_frame)
    v.chk_trail.setChecked(False)
    no_trail = v._overlay(v.player.index, v.player.current_frame)
    assert (with_trail != no_trail).any()
    v.chk_zones.setChecked(False)
    v.chk_animal.setChecked(False)
    assert (v._overlay(v.player.index, v.player.current_frame) == v.player.current_frame).all()
    # on-screen keys
    v.tabs.setCurrentIndex(2)
    v.a_keys.setChecked(False)
    assert v.pad.isHidden()
    v.a_keys.setChecked(True)
    assert not v.pad.isHidden()
    # test navigation and track editing from the ribbon
    v.next_btn.trigger()
    assert v.test.id == p.tests[1].id
    v.prev_btn.trigger()
    assert v.test.id == p.tests[0].id
    v.chk_animal.setChecked(True)
    tr = v.tracks[0]
    v.player.seek_time(2.0)
    v.a_range_start.trigger()
    v.player.seek_time(3.0)
    v.a_range_end.trigger()
    v.a_del_range.trigger()
    m = (v.tracks[0].t >= 2.0 - 1e-6) & (v.tracks[0].t <= 3.0 + 1e-6)
    assert np.isnan(v.tracks[0].x[m]).all()
    v.undo_btn.trigger()
    assert np.isfinite(v.tracks[0].x[m]).any() and len(v.tracks[0]) == len(tr)
    # a test without video: TakeNote clock in the ribbon, playback disabled
    nt = p.add_test("", "C1", stage="Day 1", trial=3)
    v.load_test(nt.id)
    assert not v.a_play.isEnabled() and v.clock_start_btn.isEnabled() and not v.track_btn.isEnabled()
    assert v.title_lbl.text().endswith("- 0:00")
