"""GUI for the per-test apparatus position, new experiment based on another protocol, protocol report,
one-row-per-animal and event-log exports, and ANCOVA in the correlation view."""

import csv
import shutil
import time

import numpy as np
import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from manymaze.core.apparatus import POSITION_KEY
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui import main_window
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pages.statistics import NONE

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=2, seconds=6)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a, **k: True)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    yield w
    w.dirty = False
    w.close()


def wait_workers(page, ms=120000):
    t0 = time.time()
    while getattr(page, "_workers", None) and time.time() - t0 < ms / 1000:
        app.processEvents()
        time.sleep(0.03)
    app.processEvents()


def test_apparatus_position_in_review(win):
    t = win.project.tests[0]
    win.open_test(t.id)
    v = win.page("TestViewPage")
    QTest.qWait(30)
    base = win.project.get_apparatus(t.apparatus).arena.centroid()
    assert v.pos_box.isEnabled() and v.pos_spins["scale"].value() == 1.0
    v.pos_spins["dx"].setValue(12)
    v.pos_spins["angle"].setValue(5)
    assert t.zone_overrides[POSITION_KEY] == {"dx": 12.0, "dy": 0.0, "angle": 5.0, "scale": 1.0}
    assert win.dirty
    assert v._app().arena.centroid()[0] == pytest.approx(base[0] + 12, abs=0.5)
    assert win.project.apparatus_of(t).arena.centroid()[0] == pytest.approx(base[0] + 12, abs=0.5)
    # another test keeps the drawn position; coming back shows this test's values
    win.open_test(win.project.tests[1].id)
    assert v.pos_spins["dx"].value() == 0.0
    win.open_test(t.id)
    assert v.pos_spins["dx"].value() == 12.0 and v.pos_spins["angle"].value() == 5.0
    v.reset_apparatus_position()
    assert POSITION_KEY not in t.zone_overrides and v.pos_spins["dx"].value() == 0.0


def test_new_experiment_based_on_another(win, tmp_path, monkeypatch):
    src = win.project
    src.stages = ["Habituation", "Test"]
    win.save()

    def fake_exec(dlg):
        dlg.name.setText("Cohort 2")
        dlg.folder.setText(str(tmp_path))
        dlg.based_on.setText(str(src.path))
        assert dlg.copy_treatments.isEnabled() and not dlg.protocol.isEnabled()
        dlg.copy_treatments.setChecked(True)
        return QDialog.Accepted

    monkeypatch.setattr(main_window.NewProjectDialog, "exec", fake_exec)
    win.new_project()
    p = win.project
    assert p.name == "Cohort 2" and p.path == tmp_path / "Cohort 2.mmaze"
    assert p.stages == ["Habituation", "Test"] and [a.name for a in p.apparatus] == [a.name for a in src.apparatus]
    assert [g.name for g in p.groups] == [g.name for g in src.groups] and not p.animals and not p.tests
    assert Project.load(p.path).stages == ["Habituation", "Test"]
    assert win.current_page() is win.page("AnimalsPage")


def test_protocol_report_command(win, tmp_path):
    win.welcome.refresh()
    assert win.welcome.side_buttons["protocol_report"].isEnabled()
    out = win.protocol_report(str(tmp_path / "protocol.html"))
    text = out.read_text(encoding="utf-8")
    assert "protocol</h1>" in text and "<img" in text


def test_wide_and_event_log_exports(win, tmp_path):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    rows, cols = page.wide_rows()
    assert len(rows) == len(win.project.animals)
    assert cols[:2] == ["Animal", "Group"] and any(c.startswith("Total distance (cm) [") for c in cols)
    out = page.export_wide(str(tmp_path / "wide.csv"))
    with open(out, newline="") as f:
        data = list(csv.DictReader(f))
    assert len(data) == len(win.project.animals) and data[0]["Animal"]
    out = page.export_trial_means(str(tmp_path / "means.csv"))
    with open(out, newline="") as f:
        means = list(csv.DictReader(f))
    assert len(means) == len(win.project.animals) and means[0]["Trials"] == "1"
    page.export_event_log(str(tmp_path / "events.csv"))
    wait_workers(page)
    with open(tmp_path / "events.csv", newline="") as f:
        log = list(csv.DictReader(f))
    assert {r["Test"] for r in log} == {str(t.id) for t in win.project.tests}
    assert {"Zone entry", "Zone exit"} <= {r["Event"] for r in log}


def test_ancova_in_correlation_view(win):
    page = win.goto("StatisticsPage")
    page.wait_loaded()
    page.tabs.setCurrentIndex(2)
    page.set_inputs(corr_x="Total distance (cm)", corr_y="Centre: time (s)", corr_by=NONE)
    assert page.ancova is None and "ANCOVA" not in page.corr_lbl.text()
    page.set_inputs(corr_by="Group")
    a = page.ancova
    assert a is not None and a["effects"][0]["effect"] == "Group"
    assert set(a["adjusted_means"]) == {"Control", "Anxious"}
    assert "ANCOVA" in page.corr_lbl.text() and "adjusted mean" in page.summary()


def test_swap_identities_in_review(win):
    p = win.project
    t = p.tests[0]
    tr = p.load_tracks(t)[0]
    other = tr.copy()
    other.x += 50
    t.extra_animals = [p.tests[1].animal_id]
    p.save_tracks(t, [tr, other])
    win.open_test(t.id)
    v = win.page("TestViewPage")
    QTest.qWait(30)
    assert not v.swap_box.isHidden() and v.swap_with.currentData() == 1
    x0, x1 = tr.x.copy(), other.x.copy()
    v.set_range(0, 1.0)
    v.set_range(1, 2.0)
    assert v.swap_identities()
    a, b = v.tracks
    m = (a.t >= 1.0 - 1e-6) & (a.t <= 2.0 + 1e-6)
    assert np.allclose(a.x[m], x1[m]) and np.allclose(b.x[m], x0[m]) and np.allclose(a.x[~m], x0[~m])
    assert np.allclose(p.load_tracks(t)[1].x[m], x0[m])  # saved
    v.undo_edit()
    assert np.allclose(v.tracks[0].x, x0) and np.allclose(v.tracks[1].x, x1)


def test_restore_backup(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    p = win.project
    stages = list(p.stages)
    p.stages = ["Changed"]
    win.save()  # backs up the original file
    assert p.list_backups()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (a[3][-1], True))  # the oldest backup
    restored = win.restore_backup()
    assert restored is win.project and win.project.stages == stages and not win.dirty
    assert Project.load(p.path).stages == stages


def test_schedule_print_save_copy(win, tmp_path):
    from PySide6.QtPrintSupport import QPrinter

    page = win.goto("TestsPage")
    menu = [a.text() for a in page.a_schedule.menu().actions()]
    assert menu == ["Create schedule…", "Print schedule…", "Save schedule…", "Copy schedule"]
    assert "Join video files into one test…" in [a.text() for a in page.a_add.menu().actions()]
    headers, rows = page.schedule_rows()
    assert headers[:2] == ["Test", "Animal"] and len(rows) == len(win.project.tests)
    out = page.save_schedule(str(tmp_path / "schedule.csv"))
    with open(out, newline="") as f:
        data = list(csv.DictReader(f))
    assert [d["Animal"] for d in data] == [r[1] for r in rows]
    assert page.copy_schedule().startswith("Test\tAnimal") and QApplication.clipboard().text().count("\n") == len(rows) + 1
    assert "Done" in page.schedule_html()
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "schedule.pdf"))
    assert page.print_schedule(printer) is printer and (tmp_path / "schedule.pdf").stat().st_size > 1000


def test_randomise_treatments_dialog(win, monkeypatch):
    from manymaze.gui.pages import animals as animals_mod

    page = win.goto("AnimalsPage")
    assert page.a_random in page.ribbon_groups()[1][1]

    def fake_exec(dlg):
        assert dlg.groups.count() == 2 and dlg.chosen_groups() == ["Control", "Anxious"]
        dlg.strata.setCurrentIndex(dlg.strata.findData("Sex"))
        dlg.seed.setValue(7)
        return QDialog.Accepted

    monkeypatch.setattr(animals_mod.RandomiseDialog, "exec", fake_exec)
    res = page.randomise_dialog()
    assert len(res) == len(win.project.animals) and win.dirty
    counts = [list(res.values()).count(g) for g in ("Control", "Anxious")]
    assert abs(counts[0] - counts[1]) <= 1
    assert {a.id: a.group for a in win.project.animals} == res


def test_join_video_files(win, tmp_path):
    import cv2

    from manymaze.core.video import VideoSource

    files = []
    for i in range(2):
        path = str(tmp_path / f"cam_{i}.avi")
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
        for _ in range(6):
            vw.write(np.full((48, 64, 3), 100, np.uint8))
        vw.release()
        files.append(path)
    page = win.goto("TestsPage")
    n = len(win.project.tests)
    page.table.clearSelection()
    test = page.join_videos(list(reversed(files)))
    assert len(win.project.tests) == n + 1 and test.video.endswith(".m3u")
    with VideoSource(win.project.abs_path(test.video)) as v:
        assert v.frame_count == 12
    # with one test selected, the joined video replaces its video
    page.select_ids({win.project.tests[0].id})
    t2 = page.join_videos(files)
    assert t2 is win.project.tests[0] and t2.video.endswith(".m3u") and len(win.project.tests) == n + 1


def test_import_and_export_apparatus(win, tmp_path, demo_dir):
    page = win.goto("ApparatusPage")
    n = len(win.project.apparatus)
    name = win.project.apparatus[0].name
    added = page.import_apparatus(str(demo_dir))  # from another experiment (same names: renamed)
    assert len(added) == 1 and added[0].name == f"{name} 2" and len(win.project.apparatus) == n + 1
    assert [z.name for z in added[0].zones] == [z.name for z in win.project.apparatus[0].zones]
    out = page.export_apparatus(str(tmp_path / "of.json"))
    again = page.import_apparatus(str(out))
    assert len(again) == 1 and again[0].name.startswith(name) and len(win.project.apparatus) == n + 2


def test_archive_and_open_archive(win, tmp_path):
    win.welcome.refresh()
    assert win.welcome.side_buttons["archive"].isEnabled()
    z = win.archive_experiment(str(tmp_path / "exp"), wait=True)
    assert z and z.endswith("exp.zip")
    n = len(win.project.tests)
    folder = win.open_archive(z, str(tmp_path / "unpacked"), wait=True)
    assert folder is not None and win.project.path == folder and len(win.project.tests) == n
    assert win.project.has_track(win.project.tests[0])
