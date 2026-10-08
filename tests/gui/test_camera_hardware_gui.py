"""Camera hardware settings in the camera options dialog (live adjustment, Auto, Reset to camera defaults,
Cancel), their persistence with the camera options, native industrial cameras in the camera chooser and the
Industrial cameras dialog — with fake cameras / SDKs."""

import shutil
import time

import cv2
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from fake_cameras import FakeCap, FakeDevice, install_pylon, no_sdks
from manymaze.core import camsources
from manymaze.core.camera import CameraView, camera_settings
from manymaze.core.camhw import CameraHardware, OpenCVControls
from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.gui.live_widgets import CameraOptionsDialog, HardwarePanel, IndustrialCamerasDialog
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
P = cv2


def pump(cond, timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class LiveCam:
    """An open OpenCV camera as the dialog sees it (VideoSource's hardware methods over a fake capture)."""

    def __init__(self):
        self.cap = FakeCap({P.CAP_PROP_EXPOSURE: -5.0, P.CAP_PROP_AUTO_EXPOSURE: 3.0, P.CAP_PROP_GAIN: 4.0,
                            P.CAP_PROP_BRIGHTNESS: 128.0},
                           settable={P.CAP_PROP_EXPOSURE, P.CAP_PROP_AUTO_EXPOSURE, P.CAP_PROP_GAIN,
                                     P.CAP_PROP_BRIGHTNESS})
        self.controls = OpenCVControls(self.cap, platform="linux")

    def hardware_controls(self):
        return self.controls.info()

    def current_hardware(self):
        return self.controls.current()

    def apply_hardware(self, hw):
        return self.controls.apply(hw)

    def reset_hardware(self):
        return self.controls.reset()


def test_hardware_panel_live():
    cam = LiveCam()
    panel = HardwarePanel({"brightness": 100}, cam)
    rows = panel.rows
    # unreadable controls are shown as not supported and disabled
    assert not rows["saturation"]["spin"].isEnabled() and rows["saturation"]["status"].text() == "Not supported"
    assert rows["brightness"]["spin"].value() == 100 and rows["gain"]["spin"].value() == \
        rows["gain"]["spin"].minimum()  # "Camera default"
    assert rows["gain"]["spin"].specialValueText() == "Camera default"
    # a value applies to the running camera at once
    rows["gain"]["spin"].setValue(20)
    assert cam.cap.props[P.CAP_PROP_GAIN] == 20 and panel.hw["gain"] == 20 and "Gain" in panel.report.text()
    assert rows["gain"]["slider"].value() > 0
    rows["brightness"]["slider"].setValue(500)  # middle of 0–255
    assert cam.cap.props[P.CAP_PROP_BRIGHTNESS] == pytest.approx(127.5, abs=1)
    # Auto: tri-state (partially checked = camera default); manual value enabled only when auto is off
    auto = rows["exposure"]["auto"]
    assert auto.checkState() == Qt.PartiallyChecked
    auto.setCheckState(Qt.Unchecked)
    assert cam.cap.props[P.CAP_PROP_AUTO_EXPOSURE] == 1.0 and rows["exposure"]["spin"].isEnabled()
    auto.setCheckState(Qt.Checked)
    assert cam.cap.props[P.CAP_PROP_AUTO_EXPOSURE] == 3.0 and not rows["exposure"]["spin"].isEnabled()
    assert panel.result().auto_exposure is True
    # back to "Camera default": forgotten, and the camera gets the value it had when the dialog opened
    rows["gain"]["spin"].setValue(rows["gain"]["spin"].minimum())
    assert "gain" not in panel.hw and cam.cap.props[P.CAP_PROP_GAIN] == 4.0
    # an unsupported setting is reported, never fatal
    rows["contrast"]["spin"].setValue(10)
    assert rows["contrast"]["status"].text() == "Not supported" and not rows["contrast"]["spin"].isEnabled()
    # Reset to camera defaults
    rows["gain"]["spin"].setValue(9)
    panel.reset_btn.click()
    assert panel.result().is_empty and cam.cap.props[P.CAP_PROP_GAIN] == 4.0
    assert cam.cap.props[P.CAP_PROP_BRIGHTNESS] == 128.0 and "Camera defaults" in panel.report.text()
    # Cancel puts back the settings the dialog started with
    rows["gain"]["spin"].setValue(15)
    panel.restore()
    assert cam.cap.props[P.CAP_PROP_GAIN] == 4.0 and cam.cap.props[P.CAP_PROP_BRIGHTNESS] == 100


def test_camera_options_dialog_hardware_tab():
    # files: no camera settings tab
    dlg = CameraOptionsDialog(None, CameraView())
    assert dlg.hardware is None and "hardware" not in dlg.result()
    dlg.close()
    # a camera that is not running: settings are only recorded; GenICam extras
    dlg = CameraOptionsDialog(None, CameraView(), is_camera=True, genicam=True,
                              hardware={"exposure": 5000, "trigger": True, "trigger_source": "Line1"})
    assert dlg.tabs.count() == 2 and dlg.tabs.tabText(1) == "Camera settings"
    hp = dlg.hardware
    assert hp.trigger.currentData() is True and hp.trigger_source.currentText() == "Line1"
    hp.pixel_format.setCurrentIndex(hp.pixel_format.findData("BayerRG8"))
    hp.trigger.setCurrentIndex(hp.trigger.findData(False))
    hp.rows["gain"]["spin"].setValue(6)
    res = dlg.result()
    assert res["hardware"] == {"exposure": 5000.0, "gain": 6.0, "pixel_format": "BayerRG8", "trigger": False,
                               "trigger_source": "Line1"}
    assert res["view"] == CameraView().to_dict()
    dlg.close()
    # Cancel with a live camera restores it
    cam = LiveCam()
    dlg = CameraOptionsDialog(None, CameraView(), camera=cam, hardware={"gain": 8})
    dlg.hardware.rows["gain"]["spin"].setValue(30)
    assert cam.cap.props[P.CAP_PROP_GAIN] == 30
    dlg.reject()
    assert cam.cap.props[P.CAP_PROP_GAIN] == 8


def test_industrial_cameras_dialog(monkeypatch, tmp_path):
    no_sdks(monkeypatch)
    dlg = IndustrialCamerasDialog([str(tmp_path / "a.cti")])
    text = dlg.status_lbl.text()
    assert "pip install pypylon" in text and "pip install harvesters" in text
    assert dlg.files() == [str(tmp_path / "a.cti")]
    install_pylon(monkeypatch, [FakeDevice()])
    dlg.refresh_status()
    assert "✓ Basler" in dlg.status_lbl.text()
    dlg.close()


# ====================================================================== the Run tests page
@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=4).save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    for k in ("question", "information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, k, lambda *a, **kw: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()
    camsources.set_cti_files([])


def test_native_camera_on_the_run_tests_page(win, monkeypatch):
    import manymaze.gui.pages.live.single as live_mod

    no_sdks(monkeypatch)
    dev = FakeDevice("4001", "acA1300")
    install_pylon(monkeypatch, [dev])
    p = win.project
    page = win.goto("LivePage")
    # the scan lists OpenCV cameras then the industrial cameras of the installed SDKs
    monkeypatch.setattr(live_mod, "list_cameras", lambda: [0])
    page.scan_cameras()
    assert pump(lambda: page._scan_worker is None)
    data = [page.camera.itemData(i) for i in range(page.camera.count())]
    assert data == [0, "pylon:4001"] and "acA1300" in page.camera.itemText(1)
    assert "pip install harvesters" in page.scan_btn.toolTip()  # the missing SDKs say what to install
    page.cam_radio.setChecked(True)
    page.camera.setCurrentIndex(1)
    assert page._source() == "pylon:4001" and page._single_key() == "pylon:4001"
    # settings saved with the camera options (no image options: the preview is not restarted)
    page._apply_single_view({"view": {}, "second": None, "layout": "side",
                             "hardware": {"gain": 6.0, "pixel_format": "BayerRG8", "contrast": 3}})
    assert p.settings_extra["cameras"]["pylon:4001"] == {"hardware": {"gain": 6.0, "contrast": 3.0,
                                                                      "pixel_format": "BayerRG8"}}
    assert "camera settings (gain, contrast)" in page.view_lbl.text()
    # applied when the camera opens; what the camera refuses goes to the log
    assert page.start_preview()
    assert pump(lambda: page._frame_size is not None and page._last_frame is not None)
    assert page._frame_size == (64, 48) and dev.f["Gain"] == 6.0 and dev.f["PixelFormat"] == "BayerRG8"
    assert pump(lambda: any("Contrast" in page.log.item(i).text() for i in range(page.log.count())))
    # live adjustment through the dialog of the running camera
    seen = {}

    def run_dialog(dlg):
        seen["dlg"] = dlg
        dlg.hardware.rows["gain"]["spin"].setValue(12)
        return QDialog.Accepted

    monkeypatch.setattr(CameraOptionsDialog, "exec", run_dialog)
    grabber = page.grabber
    assert page.camera_options()
    assert dev.f["Gain"] == 12.0 and page.grabber is grabber  # applied live, no restart
    assert seen["dlg"].hardware.pixel_format is not None  # GenICam extras for a native camera
    assert camera_settings(p, "pylon:4001")["hardware"]["gain"] == 12.0
    page.stop_preview()
    assert pump(lambda: dev.closed)
    # round trip through the project file
    p.save()
    win.set_project(Project.load(p.path))
    page = win.goto("LivePage")
    page._cameras_found(([("Camera 0", 0), ("Basler acA1300 (4001)", "pylon:4001")], []))
    page.cam_radio.setChecked(True)
    page.camera.setCurrentIndex(1)
    assert page._hardware == CameraHardware(gain=12.0, contrast=3.0, pixel_format="BayerRG8")
    # several tests: the source gets its saved settings
    assert page.set_mode("multi")
    key = page.add_source("pylon:4001")
    spec = page.group.sources[key]
    assert spec.is_native and spec.hardware.gain == 12.0
    page.apply_source_options(key, {"view": {}, "second": None, "layout": "side", "hardware": {"gain": 2}})
    assert camera_settings(page.project, "pylon:4001") == {"hardware": {"gain": 2.0}}
    sources = page.project.settings_extra["live"]["multi"]["sources"]
    assert sources[0]["source"] == "pylon:4001" and sources[0]["hardware"] == {"gain": 2.0}


def test_industrial_cameras_setting(win, monkeypatch, tmp_path):
    page = win.goto("LivePage")
    cti = tmp_path / "GenTL.cti"
    cti.write_bytes(b"")
    monkeypatch.setattr(IndustrialCamerasDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(IndustrialCamerasDialog, "files", lambda self: [str(cti)])
    monkeypatch.setattr(page, "scan_cameras", lambda: None)
    assert page.industrial_cameras()
    assert page._cti_files_setting() == [str(cti)] and str(cti) in camsources.cti_files()
    win.settings.setValue(page.CTI_KEY, [])
