"""Phase 7 of the 2026-10-09 plan in the window: terminology, recorded video file names, Add point here on heat
maps, E-mail report, Downscale before tracking and Check video."""

import csv
import datetime as dt
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QElapsedTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.core.tracking import DetectionSettings, compute_background
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.results.email import EmailReportDialog

from fake_smtp import FakeSMTP, notify_device

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("polish") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a, **k: True)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.set_project(Project.load(d))
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def wait_for(cond, ms=120000):
    t = QElapsedTimer()
    t.start()
    while not cond() and t.elapsed() < ms:
        app.processEvents()
    app.processEvents()
    assert cond()


def wait_workers(page):
    wait_for(lambda: not page._workers)


def explorer_labels(w, section: int) -> list[str]:
    ex = w.sections[section].explorer
    out = []
    for i in range(ex.topLevelItemCount()):
        it = ex.topLevelItem(i)
        out.append(it.text(0))
        out += [it.child(j).text(0) for j in range(it.childCount())]
    return out


# ------------------------------------------------------------------ terminology
def test_terminology_renames_the_main_labels(win, tmp_path):
    p = win.project
    assert "Test schedule" in explorer_labels(win, 2) and "Animals" in explorer_labels(win, 1)  # defaults
    page = win.goto("ExperimentPage")
    page.show_item("protocol")
    assert page.terms.rowCount() == 7 and page.terms.item(0, 0).text() == "Animal"
    page.terms.item(0, 1).setText("Subject")  # as typed in the table
    page.terms.item(1, 1).setText("Condition")
    page.terms.item(2, 1).setText("Session")
    page.terms.item(6, 1).setText("Area")
    assert p.terminology["animal"] == {"singular": "Subject", "plural": "Subjects"} and win.dirty
    assert page.terms.item(0, 2).text() == "Subjects"  # the plural is filled in
    page.terms.item(0, 1).setText("Mouse")
    page.terms.item(0, 2).setText("Mice")
    assert p.terminology["animal"] == {"singular": "Mouse", "plural": "Mice"}
    page.terms.item(0, 1).setText("Rat")  # a new singular gets a new plural
    assert p.terminology["animal"]["plural"] == "Rats"
    assert "Session schedule" in explorer_labels(win, 2) and "Run sessions" in explorer_labels(win, 2)
    assert {"Rats", "Conditions"} <= set(explorer_labels(win, 1))
    tests = win.goto("TestsPage")
    heads = [tests.model.headerData(c, Qt.Horizontal) for c in range(3)]
    assert heads == ["Session", "Rat", "Condition"] and tests.title_lbl.text() == "Session schedule"
    animals = win.goto("AnimalsPage")
    assert animals.table.horizontalHeaderItem(1).text() == "Rat ID" and animals.title_lbl.text() == "Rats"
    win.goto("ApparatusPage")
    assert win.page("ApparatusPage").panel.tabs.tabText(0) == "Areas"
    res = win.goto("ResultsPage")
    res.wait_loaded()
    heads = [res.model.headerData(i, Qt.Horizontal) for i in range(res.model.columnCount())]
    assert "Rat" in heads and "Condition" in heads and "Animal" not in heads
    assert "Animal" in res.rows[0]  # the rows keep the standard names
    out = res.save_table(str(tmp_path / "r.csv"))
    head = next(csv.reader(open(out, encoding="utf-8")))
    assert "Rat" in head and "Condition" in head and "Group" not in head
    assert res.clipboard_text().split("\t")[1] == "Rat"


# ------------------------------------------------------------------ recorded video file names
def test_recorded_video_names_from_chosen_fields(win):
    p = win.project
    page = win.goto("LivePage")
    assert "Test number, animal" in page.rec_names_lbl.text()
    dlg = page.recording_names_dialog(modal=False)
    dlg.set_fields(["animal", "stage", "date"])
    assert dlg.fields() == ["animal", "stage", "date"]
    assert dlg.example.text().endswith(f"_{dt.date.today().isoformat()}.mp4")
    page.set_recording_name_fields(dlg.stored_fields())
    dlg.restore_defaults()
    assert dlg.stored_fields() == []
    dlg.deleteLater()
    assert p.recording_name_fields == ["animal", "stage", "date"] and win.dirty
    assert "Animal, stage, date" in page.rec_names_lbl.text()
    video = p.abs_path(p.tests[0].video)
    page.set_simulation_file(str(video))
    page.duration.setValue(2.0)
    page.start_mode.setCurrentIndex(page.start_mode.findData("immediate"))
    page.test_combo.setCurrentIndex(0)  # a new test
    page.animal.setCurrentText("C1")
    page.stage.setCurrentText("Day 1")
    page.on_show()
    page._on_file_background(compute_background(video, DetectionSettings(background_samples=11)))
    page.start_preview = lambda: True
    assert page.arm()
    name = Path(page._record_path).name
    assert name == f"C1_Day_1_{dt.date.today().isoformat()}.mp4", name
    page.stop_test(save=False)


# ------------------------------------------------------------------ heat maps: Add point here
def test_add_point_at_the_hottest_spot(win):
    p = win.project
    page = win.goto("ResultsPage")
    page.wait_loaded()
    page.set_view("heat")
    page.table.selectRow(0)
    page.tabs.setCurrentIndex(1)
    page.render_all()
    test = p.get_test(page.current_row()["Test"])
    app_ = p.get_apparatus(test.apparatus)
    n = len(app_.points)
    ((label, x, y),) = page.hot_spots()
    assert label == ""
    assert page.add_hot_spot_point() == ["Hottest spot"] and win.dirty
    pt = app_.points[-1]
    assert len(app_.points) == n + 1 and (pt.x, pt.y) == (round(x, 2), round(y, 2))
    assert app_.arena_or_bounds().contains([pt.x], [pt.y])[0]
    assert page.add_hot_spot_point() == ["Hottest spot 2"]  # unique names
    # treatment maps: one point per treatment, or the one chosen
    page.group_heatmaps()
    wait_workers(page)
    assert page.tabs.currentWidget() is page.groups_canvas
    spots = page.hot_spots()
    assert [s[0] for s in spots] == ["Control", "Anxious"]
    assert page.add_hot_spot_point(which="Anxious") == ["Hottest spot (Anxious)"]
    assert page.add_hot_spot_point(which="") == ["Hottest spot (Control)", "Hottest spot (Anxious) 2"]


# ------------------------------------------------------------------ e-mail a report
def test_email_report_through_the_alert_device(win, monkeypatch):
    p = win.project
    page = win.goto("ResultsPage")
    page.wait_loaded()
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]) or QMessageBox.Ok)
    assert page.email_report(to="pi@example.org") is None and "No e-mail server" in infos[0]  # none set up
    with FakeSMTP() as srv:
        p.io_devices = [notify_device(srv.port, email_to="boss@example.org")]
        dlg = EmailReportDialog(p, page)
        assert dlg.to.text() == "boss@example.org" and p.name in dlg.subject.text()
        dlg._accept()
        assert dlg.result() == dlg.DialogCode.Accepted
        dlg.to.setText("")
        dlg._accept()
        assert "address" in dlg.error.text()
        dlg.deleteLater()
        page.email_report(to="pi@example.org, lab@example.org", table=".csv", report=True)
        wait_workers(page)
        wait_for(lambda: srv.messages)
    (m,) = srv.messages
    assert m["to"] == ["pi@example.org", "lab@example.org"] and "results" in m["message"]["Subject"]
    files = {a.get_filename(): a.get_payload(decode=True) for a in m["message"].iter_attachments()}
    assert set(files) == {"Demo_open_field results.csv", "Demo_open_field report.html"}
    assert files["Demo_open_field results.csv"].decode("utf-8").startswith("Test,Animal,")
    assert b"<h2>Results</h2>" in files["Demo_open_field report.html"]
    assert "e-mailed to pi@example.org, lab@example.org" in win.statusBar().currentMessage()
    assert page.email_act.isEnabled() and page.email_act in [a for _, acts in page.ribbon_groups()
                                                              for a in (x[0] if isinstance(x, tuple) else x
                                                                        for x in acts)]


# ------------------------------------------------------------------ video files
def test_downscale_setting_and_check_video(win):
    p = win.project
    proto = win.goto("ExperimentPage")
    proto.show_item("tracking")
    combo = proto.det_form.editors["downscale"]
    combo.setCurrentIndex(combo.findData(2))
    assert p.detection.downscale == 2
    tests = win.goto("TestsPage")
    tests.select_ids({p.tests[0].id, p.tests[1].id})
    assert tests.a_check.isEnabled()
    assert tests.check_videos() is not None
    wait_for(lambda: hasattr(tests, "video_checks") and getattr(tests, "_check_dialog", None) is not None)
    checks = tests.video_checks
    assert len(checks) == 2 and all(c.frames == 150 and not c.error for c in checks.values())
    table = tests._check_dialog.findChild(QTableWidget)
    assert table.rowCount() == 2 and table.item(0, 2).text() == "150"
    assert "Checked 2 videos" in win.statusBar().currentMessage()
    tests._check_dialog.close()
