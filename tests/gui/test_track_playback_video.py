"""GUI of the track-plot playback over the test's video (Data page ▸ Track plots): the video plays under the track
while playing and scrubbing, the saved playback shows it too, and the reader is closed with the plot."""

import shutil
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from manymaze.core.demo import create_demo_project
from manymaze.core.project import Project
from manymaze.core.video import VideoSource
from manymaze.gui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "d.mmaze"
    p = create_demo_project(d, n_per_group=1, seconds=6)
    p.save()
    return d


@pytest.fixture
def win(demo_dir, tmp_path, monkeypatch):
    for k in ("question", "information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, k, lambda *a, **kw: QMessageBox.Ok)
    d = tmp_path / "d.mmaze"
    shutil.copytree(demo_dir, d)
    w = MainWindow()
    w.set_project(Project.load(d))
    w.resize(1400, 880)
    yield w
    w.dirty = False
    w.close()


def wait_workers(page, ms=120000):
    t0 = time.monotonic()
    while getattr(page, "_workers", None) and (time.monotonic() - t0) * 1000 < ms:
        for w in list(page._workers):
            w.wait(50)
        app.processEvents()
    app.processEvents()


def test_playback_plays_the_test_video(win, tmp_path, monkeypatch):
    page = win.goto("ResultsPage")
    page.wait_loaded()
    page.set_view("track")
    page.table.selectRow(0)
    page.render_all()
    pb = page.playback
    assert pb.isEnabled() and pb.plays_video and pb.frames is not None
    img = pb.anim._image
    start = np.asarray(img.get_array()).copy()  # the test's first frame, until the playback plays
    pb.seek(pb.start + 3.0)
    t = pb.time
    expected = pb.frames(t)[..., ::-1]
    assert np.array_equal(np.asarray(img.get_array()), expected) and not np.array_equal(expected, start)
    pb.set_speed(16.0)
    assert pb.play()
    pb.advance(0.05)  # 0.8 s of track later: a later frame, read on from the last one
    assert pb.frames._index == pb.frames.index_at(pb.time) > pb.frames.index_at(0.0)
    assert np.array_equal(np.asarray(img.get_array()), pb.frames(pb.time)[..., ::-1])
    pb.pause()
    # saved playback: the video under the track too (a reader of its own, in the worker thread)
    out = tmp_path / "playback.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page.export_playback()
    wait_workers(page)
    with VideoSource(str(out)) as v:
        assert v.frame_count >= (pb.end - pb.start) / 16 * 25
        a, b = v.frame_at(0), v.frame_at(v.frame_count // 2)
    assert a is not None and b is not None and not np.array_equal(a, b)
    # another test (a new figure) gets its own reader and the previous one is closed; split plots have none
    frames = pb.frames
    page.table.selectRow(1)
    page.render_all()
    assert pb.frames is not None and pb.frames is not frames
    page.set_plot_options(split=True)
    page.render_all()
    assert not pb.isEnabled() and pb.frames is None
