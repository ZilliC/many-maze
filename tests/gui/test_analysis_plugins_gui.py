"""Analysis plug-ins in the GUI: Protocol ▸ Analysis ▸ Analysis plug-ins (add the CSV / TSV importer with its
settings form, edit, remove, run on the tests) and the series in the results."""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core.project import Project
from manymaze.gui.main_window import MainWindow
from manymaze.gui.plugin_dialog import PluginOptionsDialog

app = QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    p = Project(name="Plug", test_duration_s=10.0)
    p.ensure_animal("M1")
    p.save(tmp_path / "plug.mmaze")
    t = p.add_test("", "M1")
    t.status, t.duration_s = "scored", 10.0
    (p.path / "M1.csv").write_text("Time;Pulse\n0;300\n5;320\n9;340\n")
    p.save()
    w = MainWindow()
    w.settings.setValue("current_user", "")
    w.set_project(p)
    yield w
    w.dirty = False


def test_add_edit_run_and_remove(win, monkeypatch):
    p = win.project
    ex = win.goto("ExperimentPage")
    ex.show_item("analysis")
    ex._fill_plugin_menu()
    assert [a.text() for a in ex.plugin_menu.actions()][0] == "Data file (CSV / TSV)"
    dlg = PluginOptionsDialog(p, {"plugin": "csv_import", "name": "Pulse", "enabled": True})
    kind, w = dlg.fields["file"]
    assert kind == "file"
    w.setText("{animal}.csv")
    dlg.name.setText("Oximeter")
    cfg = ex.add_plugin("csv_import", dlg)
    assert cfg["file"] == "{animal}.csv" and cfg["time_unit"] == "s" and p.analysis_plugins == [cfg]
    assert ex.plugin_list.count() == 1 and "Oximeter" in ex.plugin_list.item(0).text() and win.dirty
    dlg2 = PluginOptionsDialog(p, p.analysis_plugins[0])
    dlg2.fields["prefix"][1].setText("SpO2 ")
    assert ex.edit_plugin(dlg2)["prefix"] == "SpO2 "
    ex.run_plugins(wait=True)
    t = p.tests[0]
    assert t.extra_series["SpO2 Pulse"]["samples"] == 3 and not win.dirty  # saved
    assert ex.last_plugin_run == {"done": [t.id], "errors": []}
    row = next(r for r in p.results() if r["Period"] == "Whole test")
    assert row["SpO2 Pulse: mean"] == 320.0 and row["Oximeter: test start in the file (s)"] == 0.0
    assert Project.load(p.path).tests[0].extra_series  # stored
    ex.remove_plugin()
    assert p.analysis_plugins == [] and ex.plugin_list.count() == 0


def test_problems_are_shown(win, monkeypatch):
    p = win.project
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    p.analysis_plugins = [{"plugin": "csv_import", "name": "Missing", "file": "{animal}_missing.csv"}]
    ex = win.goto("ExperimentPage")
    ex.run_plugins(wait=True)
    assert shown and "Missing: no data file" in shown[0]
