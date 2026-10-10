"""Phase 5 in the interface: lens correction (camera options, the lens correction dialog, video tests on the
Apparatus page, the Review page), camera montages of up to four sources, Ignore lighting changes, the animal
presets of the detection settings and the animal choice of the pose model."""

import json
import shutil
import time

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from manymaze.core import pose
from manymaze.core.camera import CameraView, camera_settings
from manymaze.core.demo import create_demo_project
from manymaze.core.lens import CorrectedSource, LensCorrection, calibrate_checkerboard
from manymaze.core.project import Project
from manymaze.core.tracking import DetectionSettings
from manymaze.core.video import VideoSource
from manymaze.gui.live_widgets import CameraOptionsDialog, LensCorrectionDialog
from manymaze.gui.main_window import MainWindow
from manymaze.gui.pose_model import ConvertModelDialog, PoseModelBox

app = QApplication.instance() or QApplication([])


def pump(cond, timeout=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.03)
    return cond()


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    create_demo_project(d, n_per_group=1, seconds=4)
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    for k in ("question", "information", "warning"):
        monkeypatch.setattr(QMessageBox, k, lambda *a, **kw: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail(f"error box: {a[2:]}"))
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.resize(1400, 880)
    w.set_project(Project.load(d))
    yield w
    w.page("LivePage").shutdown()
    w.dirty = False
    w.close()


def checkerboard_images(n=10):
    """Views of a checkerboard (9 × 6 inner corners) through a barrel lens."""
    W, H = 640, 480
    K = np.array([[520.0, 0, W / 2], [0, 520.0, H / 2], [0, 0, 1]])
    D = np.array([[-0.25, 0.06, 0, 0, 0]])
    sq = 40
    tex = np.full((9 * sq, 12 * sq), 255, np.uint8)
    for r in range(7):
        for c in range(10):
            if (r + c) % 2 == 0:
                tex[(r + 1) * sq:(r + 2) * sq, (c + 1) * sq:(c + 2) * sq] = 0
    corners = np.float32([[0, 0], [12 * sq, 0], [12 * sq, 9 * sq], [0, 9 * sq]])
    world = np.float64([[x / sq - 2, y / sq - 2, 0] for x, y in corners])
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    und = cv2.undistortPoints(np.stack([xs.ravel(), ys.ravel()], 1).reshape(-1, 1, 2), K, D, P=K).reshape(H, W, 2)
    rng = np.random.default_rng(4)
    out = []
    for _ in range(n):
        rvec = np.array([rng.uniform(-0.4, 0.4), rng.uniform(-0.4, 0.4), rng.uniform(-0.3, 0.3)])
        tvec = np.array([rng.uniform(-7, -1), rng.uniform(-5, 0), rng.uniform(14, 20)])
        pts, _ = cv2.projectPoints(world, rvec, tvec, K, None)
        flat = cv2.warpPerspective(tex, cv2.getPerspectiveTransform(corners, pts.reshape(-1, 2).astype(np.float32)),
                                   (W, H), borderValue=170)
        out.append(cv2.cvtColor(cv2.remap(flat, und[..., 0], und[..., 1], cv2.INTER_LINEAR, borderValue=170),
                                cv2.COLOR_GRAY2BGR))
    return out


def test_lens_correction_dialog(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    frame = np.full((240, 320, 3), 200, np.uint8)
    cv2.rectangle(frame, (20, 20), (300, 220), (30, 30, 30), 3)
    dlg = LensCorrectionDialog(frame, None)
    assert dlg.result() == {} and not dlg.barrel_box.isVisible()
    dlg.method.setCurrentIndex(dlg.method.findData("barrel"))
    dlg.slider.setValue(60)
    assert dlg.strength.value() == 60 and dlg.result() == {"method": "barrel", "strength": 60.0, "alpha": 0.0}
    assert "barrel strength 60" in dlg.status.text()
    dlg.keep_whole.setChecked(True)
    assert dlg.result()["alpha"] == 1.0
    dlg.show_original.setChecked(True)
    assert dlg.status.text() == "Original image"
    dlg.close()
    # checkerboard: views captured from the "camera", then calibrated
    images = checkerboard_images()
    current = {"f": images[0]}
    dlg = LensCorrectionDialog(images[0], {}, frame_source=lambda: current["f"])
    dlg.method.setCurrentIndex(dlg.method.findData("checkerboard"))
    assert dlg.board_box.isVisibleTo(dlg) and not dlg.calibrate_btn.isEnabled()
    dlg.accept()  # not calibrated yet: the dialog stays open with a message
    assert "Calibrate" in told[0] and dlg.result() == LensCorrection().to_dict() == {}
    for f in images:
        current["f"] = f
        dlg.capture_view()
    assert not dlg.capture_view(images[0])  # the same view twice adds nothing
    assert len(dlg.views) >= 8 and dlg.calibrate_btn.isEnabled()
    assert dlg.calibrate() and dlg.result()["method"] == "checkerboard"
    assert dlg.result()["views"] == len(dlg.views) and "Calibrated" in dlg.views_lbl.text()
    # the views of a video of the board
    vp = tmp_path / "board.avi"
    wr = cv2.VideoWriter(str(vp), cv2.VideoWriter_fourcc(*"MJPG"), 10, (640, 480))
    for f in images:
        wr.write(f)
    wr.release()
    dlg.clear_views()
    assert dlg.add_video_views(str(vp)) >= 8 and len(dlg.views) >= 8
    dlg.close()
    # an existing calibration is shown as such
    lens = calibrate_checkerboard(dlg.views, (640, 480))
    again = LensCorrectionDialog(images[0], lens.to_dict())
    assert again.method.currentData() == "checkerboard" and again.result()["views"] == lens.views
    again.close()


def test_camera_options_montage_and_lens():
    a = np.full((120, 160, 3), 50, np.uint8)
    b = np.full((120, 160, 3), 100, np.uint8)
    c = np.full((120, 160, 3), 150, np.uint8)
    choices = [("one.avi", "one.avi"), ("two.avi", "two.avi"), ("three.avi", "three.avi")]
    dlg = CameraOptionsDialog(a, CameraView(), ["one.avi", "two.avi"], "grid", choices, [b, c])
    assert dlg.merged() == ["one.avi", "two.avi"] and dlg.layout_combo.currentData() == "grid"
    assert [cb.isEnabled() for cb in dlg.merge_combos] == [True, True, True]
    assert dlg.src_view.frame_size == (320, 240) and dlg._region_reliable()
    res = dlg.result()
    assert res["merge"] == ["one.avi", "two.avi"] and res["second"] == "one.avi" and res["undistort"] == {}
    dlg.merge_combos[2].setCurrentIndex(dlg.merge_combos[2].findData("three.avi"))
    assert dlg.merged() == ["one.avi", "two.avi", "three.avi"] and not dlg._region_reliable()  # no image yet
    dlg.layout_combo.setCurrentIndex(dlg.layout_combo.findData("side"))
    assert dlg.src_view.frame_size == (640, 120)
    dlg.merge_combos[0].setCurrentIndex(0)  # no merge: the others are ignored and disabled
    assert dlg.merged() == [] and not dlg.merge_combos[1].isEnabled() and not dlg.layout_combo.isEnabled()
    # lens correction of the camera, from the image tab
    assert dlg.lens_lbl.text() == "None"
    lens_dlg = LensCorrectionDialog(a, None)
    lens_dlg.method.setCurrentIndex(lens_dlg.method.findData("barrel"))
    lens_dlg.strength.setValue(50)
    assert dlg.edit_lens(lens_dlg)
    assert dlg.result()["undistort"] == {"method": "barrel", "strength": 50.0, "alpha": 0.0}
    assert dlg.lens_lbl.text().startswith("Barrel strength 50")
    dlg._reset()
    assert dlg.result()["undistort"] == {} and dlg.result()["merge"] == []
    dlg.close()
    # two-source montages of older versions: a single "second" source and its image
    old = CameraOptionsDialog(a, CameraView(), "one.avi", "stack", choices, b)
    assert old.merged() == ["one.avi"] and old.result()["layout"] == "stack" and old.src_view.frame_size == (160, 240)
    old.close()


def test_live_montage_and_lens(win, tmp_path):
    p = win.project
    page = win.goto("LivePage")
    video = p.abs_path(p.tests[0].video)
    others = []
    for i in (1, 2):
        dst = tmp_path / f"copy{i}.avi"
        shutil.copy(video, dst)
        others.append(str(dst))
    page.set_simulation_file(video)
    lens = LensCorrection.barrel(40).to_dict()
    page._apply_single_view({"view": {}, "second": others[0], "merge": others, "layout": "side", "undistort": lens})
    key = f"file:{video}"
    saved = camera_settings(p, key)
    assert saved["merge"] == others and saved["second"] == others[0] and saved["undistort"] == lens
    page.set_simulation_file(video)  # reloaded from the saved options
    assert page._merge == others and page._second == others[0] and page._undistort == lens
    txt = page.view_lbl.text()
    assert "lens corrected" in txt and "merged with copy1.avi, copy2.avi" in txt
    with VideoSource(video) as v:
        w, h = v.width, v.height
    assert page.start_preview()
    assert pump(lambda: page._frame_size is not None and page._last_frame is not None)
    assert page._frame_size == (3 * w, h)
    raws = page.grabber.raw_frames_all()
    assert len(raws) == 3 and all(r is not None and r.shape[:2] == (h, w) for r in raws)
    page.stop_preview()
    # the camera options dialog of the running preview gets the montage and the lens
    seen = {}

    def run_dialog(dlg):
        seen["dlg"] = dlg
        return 0

    from manymaze.gui.live_widgets import camera_options
    orig = camera_options.CameraOptionsDialog.exec
    camera_options.CameraOptionsDialog.exec = run_dialog
    try:
        assert not page.camera_options()
    finally:
        camera_options.CameraOptionsDialog.exec = orig
    assert seen["dlg"].merged() == others and seen["dlg"].lens == lens
    # several tests: the source gets the saved montage and every camera's own correction
    assert page.set_mode("multi")
    k = page.add_source(video)
    spec = page.group.sources[k]
    assert spec.merge == others and spec.undistort == {key: lens}
    page.apply_source_options(k, {"view": {}, "merge": others[:1], "layout": "stack", "undistort": {}})
    assert spec.merge == others[:1] and spec.layout == "stack" and spec.undistort == {}
    assert camera_settings(p, key) == {"second": others[0], "layout": "stack"}
    sources = p.settings_extra["live"]["multi"]["sources"]
    assert sources[0]["second"] == others[0] and "merge" not in sources[0]


def test_apparatus_lens_correction_of_video_tests(win):
    p = win.project
    page = win.goto("ApparatusPage")
    app.processEvents()
    assert "Lens correction…" in [a.text() for _, acts in page.ribbon_groups() for a, _ in acts
                                  if hasattr(a, "text")]
    t0 = p.tests[0]
    assert page.bg.real and page.bg.lens is None
    page.bg.use_test_video(t0.id)
    raw = page.bg.frame.copy()
    lens = LensCorrection.barrel(60).to_dict()
    assert page.lens_correction(lens, "video") == 1
    assert t0.undistort == lens and all(t.undistort == {} for t in p.tests[1:])
    assert page.bg.lens is not None and np.array_equal(page.bg.raw_frame, raw)
    assert np.abs(page.bg.frame.astype(int) - raw.astype(int)).mean() > 1
    assert "lens corrected" in page.bg.label.text()
    assert page.lens_correction(lens, "all") == len(p.tests) - 1
    assert page.lens_correction({}, "all") == len(p.tests)  # removed again
    assert all(t.undistort == {} for t in p.tests) and page.bg.lens is None
    # the dialog offers the tests of the video, of the apparatus or all of them
    seen = {}

    def run_dialog(dlg):
        seen["choices"] = [dlg.apply_combo.itemData(i) for i in range(dlg.apply_combo.count())]
        dlg.method.setCurrentIndex(dlg.method.findData("barrel"))
        dlg.strength.setValue(30)
        dlg.apply_combo.setCurrentIndex(dlg.apply_combo.findData("apparatus"))
        return 1

    orig = LensCorrectionDialog.exec
    LensCorrectionDialog.exec = run_dialog
    try:
        n = page.lens_correction()
    finally:
        LensCorrectionDialog.exec = orig
    assert seen["choices"] == ["video", "apparatus", "all"]
    assert n == len([t for t in p.tests if t.apparatus == page.app.name]) and t0.undistort["strength"] == 30.0
    # saved with the experiment
    p.save()
    assert json.loads((p.path / "project.json").read_text())["tests"][0]["undistort"]["strength"] == 30.0


def test_review_page_and_tracking_use_the_correction(win):
    p = win.project
    t = p.tests[0]
    t.undistort = LensCorrection.barrel(50).to_dict()
    win.open_test(t.id)
    v = win.page("TestViewPage")
    assert isinstance(v.player.source, CorrectedSource)
    with VideoSource(p.abs_path(t.video)) as src:
        raw = src.frame_at(0)
    v.player.seek(0)
    assert np.abs(v.player.current_frame.astype(int) - p.lens_for(t).apply(raw).astype(int)).max() == 0
    assert v.bg_key(p.detection_for(t))[-1] == p.lens_for(t).key()
    [tr] = p.track_test(t)
    assert tr.meta["lens_correction"] == "barrel strength 50"
    v.player.close_video()


def test_lighting_option_and_presets_in_the_protocol(win):
    p = win.project
    page = win.goto("ExperimentPage")
    page.show_item("tracking") if hasattr(page, "show_item") else None
    form = page.det_form
    chk = form.editors["lighting_compensation"]
    assert chk.text() == "Ignore lighting changes" and not chk.isChecked()
    chk.setChecked(True)
    assert p.detection.lighting_compensation
    combo = page.preset
    assert [combo.itemText(i) for i in range(combo.count())] == ["Choose an animal…", "Mouse", "Rat", "Hooded rat",
                                                                 "White animal on sawdust", "Zebrafish"]
    ppc = next(a.px_per_cm for a in p.apparatus if a.px_per_cm)
    combo.activated.emit(combo.findData("white_on_sawdust"))
    assert combo.currentIndex() == 0  # an action, not a setting
    d = p.detection
    assert d.contrast == "light" and d.lighting_compensation
    assert d.min_area_px == round(2.0 * ppc ** 2) and d.max_area_px == round(300.0 * ppc ** 2)
    assert form.editors["contrast"].currentData() == "light"  # the form shows the new values
    changed = page.apply_animal_preset("hooded_rat")
    assert "contrast" in changed and p.detection.contrast == "auto" and not p.detection.lighting_compensation
    # without a calibrated apparatus the body-size limits stay
    for a in p.apparatus:
        a.px_per_cm = None
    p.detection.min_area_px = 77
    page.apply_animal_preset("rat")
    assert p.detection.min_area_px == 77


def test_presets_on_the_review_page(win):
    p = win.project
    t = p.tests[0]
    win.open_test(t.id)
    v = win.page("TestViewPage")
    changed = v.apply_animal_preset("zebrafish")
    assert "contrast" in changed and t.detection["contrast"] == "dark"
    assert p.detection.contrast != "dark" or p.detection.contrast == DetectionSettings().contrast
    assert "differ from the experiment" in v.det_lbl.text()
    v.player.close_video()


def test_pose_model_animal_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("MANYMAZE_MODELS", str(tmp_path / "models"))
    s = DetectionSettings(body_parts="pose")
    box = PoseModelBox()
    box.set_settings(s)
    assert box.species.currentData() == "mouse" and not box.note.isVisibleTo(box)
    box.species.activated.emit(box.species.findData("rat"))
    assert s.pose_species == "rat" and box.note.isVisibleTo(box)
    assert "No pose model of rats" in box.note.text() and "made for mice" in box.note.text()
    assert s.pose_model == "topviewmouse_rtmpose_s"  # kept until one's own model is chosen

    def fake_convert(path, keypoints, parts, input_size=(256, 256), species="", dest=None, progress=None):
        out = tmp_path / "models" / "custom" / "rat.onnx"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"onnx")
        out.with_suffix(".json").write_text(json.dumps({"keypoints": keypoints, "species": species}))
        return out

    monkeypatch.setattr(pose, "convert_checkpoint", fake_convert)
    m = box.convert("rat.pt", ["nose", "back", "tail_base"], {"nose": "nose"}, wait=True)
    assert m.endswith("rat.onnx") and s.pose_model == m and s.pose_species == "rat"
    assert "Custom model" in box.status.text() and "found" in box.status.text()
    assert not box.note.text().endswith("made for mice.</b>")
    box.species.activated.emit(box.species.findData("mouse"))
    assert s.pose_model == "topviewmouse_rtmpose_s" and s.pose_species == "mouse"
    # the conversion dialog: body part names (from the DeepLabCut project), parts and input size
    dlg = ConvertModelDialog("rat.pt", 4, ["snout", "neck", "spine_mid", "tailbase"], "rat")
    v = dlg.values()
    assert v["parts"] == {"nose": "snout", "centre": "spine_mid", "tail_base": "tailbase"}
    assert v["input_size"] == (256, 256) and v["species"] == "rat"
    dlg.names.setText("a, b")
    dlg.accept()
    assert "all 4 body parts" in dlg.error.text()
    dlg.close()
