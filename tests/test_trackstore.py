"""H-P08: the track of a live test in bounded memory (core.trackstore): rows and body outlines go to a side file in
chunks, the whole track comes back at the end, and a test interrupted by a crash is recovered whole."""

import json
import math
import tracemalloc
from pathlib import Path

import cv2
import numpy as np
import pytest

from manymaze.core import synthetic as syn
from manymaze.core import templates
from manymaze.core.autosave import RecoveredSession, recover
from manymaze.core.live import LiveSession
from manymaze.core.livemonitor import LiveCharts
from manymaze.core.project import Project
from manymaze.core.track import COLUMNS
from manymaze.core.tracking import Detection, DetectionSettings
from manymaze.core.trackstore import TrackBuilder


def _det(i: int, rng) -> Detection:
    x, y = 100 + 50 * math.sin(i / 40), 100 + 50 * math.cos(i / 55)
    d = Detection(x=x, y=y, hx=x + 5, hy=y, tx=x - 5, ty=y, area=300.0 + i % 7, angle=float(i % 360),
                  motion=float(i % 11), detected=i % 13 != 0)
    if i % 13 == 0:  # not seen: no position, no outline
        d.x = d.y = math.nan
    elif i % 17:
        k = int(rng.integers(6, 25))
        d.outline = rng.integers(0, 640, (k, 2)).astype(np.int32)
        if i == 400:
            d.outline[0] = (70000, -40000)  # beyond int16: that chunk is stored as int32
    return d


def _same(a, b):
    assert len(a) == len(b)
    for c in COLUMNS:
        np.testing.assert_array_equal(getattr(a, c), getattr(b, c))
    assert (a.outline is None) == (b.outline is None)
    if a.outline is not None:
        for oa, ob in zip(a.outline, b.outline):
            assert (oa is None) == (ob is None)
            if oa is not None:
                np.testing.assert_array_equal(oa, ob)
                assert ob.dtype == np.int32 and ob.shape[1] == 2


def test_spilled_track_comes_back_whole(tmp_path):
    rng = np.random.default_rng(1)
    dets = [_det(i, rng) for i in range(1234)]
    mem, disk = TrackBuilder(), TrackBuilder(path=str(tmp_path / "x.track"), spill=True, chunk=100)
    for i, d in enumerate(dets):
        mem.add(i / 25, d)
        disk.add(i / 25, d)
    assert len(disk) == 1234 and len(disk._tail["t"]) == 34 and len(disk._chunks) == 12
    assert (tmp_path / "x.track").stat().st_size > 1200 * 12 * 8
    _same(mem.build(25.0), disk.build(25.0))
    # reads across chunks and the rows in memory
    part = disk.read(95, 1210, ["t", "x", "detected"], outline=True)
    full = mem.build(25.0)
    np.testing.assert_array_equal(part["t"], full.t[95:1210])
    np.testing.assert_array_equal(part["x"], full.x[95:1210])
    assert part["detected"].dtype == bool and list(part["detected"]) == list(full.detected[95:1210])
    assert disk.last("t") == pytest.approx(1233 / 25) and disk.last("detected") is True
    assert disk.index_of_time(10.0) == 250 and disk.index_of_time(49.0) == 1225 and disk.index_of_time(-1) == 0
    assert disk.index_of_time(1e9) == 1234 and disk.index_of_time(10.0, hi=200) == 200
    # the read-only column view of LiveSession.cols
    from manymaze.core.trackstore import ColumnsView

    cols = ColumnsView(disk)
    assert len(cols["t"]) == 1234 and cols["t"][-1] == pytest.approx(1233 / 25) and cols["x"][-3:] == list(full.x[-3:])
    # cut back into a chunk on disk, then into the rows in memory
    disk.truncate(1220)
    assert len(disk) == 1220 and len(disk._chunks) == 12
    disk.truncate(555)
    assert len(disk) == 555 and len(disk._chunks) == 5 and len(disk._tail["t"]) == 55
    _same(full.take(slice(0, 555)), disk.build(25.0))
    for i in range(555, 800):  # and go on adding: the chunks after the cut are written again
        disk.add(i / 25, dets[i])
    _same(full.take(slice(0, 800)), disk.build(25.0))
    disk.close(remove=True)
    assert not (tmp_path / "x.track").exists()


def test_temporary_side_file_without_autosave():
    b = TrackBuilder(spill=True, chunk=10)
    rng = np.random.default_rng(2)
    for i in range(35):
        b.add(i / 25, _det(i, rng))
    assert b.path is None and len(b._chunks) == 3 and len(b.build(25.0)) == 35
    assert b.recovery_state().get("track_file") is None  # nothing a crash-recovery file could point to
    snap = b.snapshot()  # every row, as lists (as before rows went to disk)
    assert len(snap["t"]) == 35 and snap["t"][-1] == pytest.approx(34 / 25) and len(snap["outline"]) == 35
    b.truncate(20)  # (a test ended by a procedure: cut at a chunk's end)
    b.close()
    assert len(b) == 20 and b.last("t") == pytest.approx(19 / 25)  # still known once the file is gone


def test_memory_of_a_long_live_track_stays_bounded():
    """A long test in miniature: 60 000 rows with outlines (40 minutes at 25 frames/s). Kept in memory they grow
    without end (here only by the lists' pointers and times: the detections are shared); written to the side file
    in chunks, memory stops growing after the first chunk."""
    rng = np.random.default_rng(3)
    dets = [_det(i, rng) for i in range(500)]

    def growth(b):
        tracemalloc.start()
        try:
            for i in range(10000):
                b.add(i / 25, dets[i % 500])
            base = tracemalloc.get_traced_memory()[0]
            for i in range(10000, 60000):
                b.add(i / 25, dets[i % 500])
            return tracemalloc.get_traced_memory()[0] - base
        finally:
            tracemalloc.stop()

    assert growth(TrackBuilder()) > 5_000_000  # all in memory (a video file's tracking)
    b = TrackBuilder(spill=True, chunk=1500)
    grown = growth(b)
    assert len(b) == 60000 and len(b._tail["t"]) < 1500
    assert grown < 1_000_000, f"{grown / 1e6:.1f} MB more for 50 000 more rows"
    tr = b.build(25.0)
    assert len(tr) == 60000 and tr.t[-1] == pytest.approx(59999 / 25) and tr.has_outline()


def _frame(x, y=100):
    img = np.full((200, 200), 200, np.uint8)
    syn.draw_mouse(img, x, y, 0)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _session(**kw):
    app = templates.build("open_field", 10, 10, 180, 180, size_cm=40)
    s = LiveSession(app, DetectionSettings(background="frame"), duration_s=0, **kw)
    s.set_background(np.full((200, 200, 3), 200, np.uint8))
    s._track = TrackBuilder(path=f"{s.autosave_path}.track" if s.autosave_path else None, spill=True, chunk=100)
    return s


def _walk(s, n, start=0):
    for i in range(start, start + n):
        s.process(_frame(40 + (i * 3) % 120, 60 + (i // 40) % 80), i / 25)


def test_a_crashed_live_test_is_recovered_whole(tmp_path):
    proj = Project(name="p")
    proj.save(tmp_path / "p.mmaze")
    test = proj.add_test("", "A1", "")
    proj.save()
    side = proj.path / "recordings" / "test_0001_A1.autosave.json"
    side.parent.mkdir(exist_ok=True)
    s = _session(autosave_path=str(side), autosave_s=1000,
                 autosave_meta={"test_id": test.id, "animal": "A1", "apparatus": "", "stage": "", "trial": 1})
    _walk(s, 450)
    s.flush_autosave()
    live = s._track.build(s.fps)  # what the test had at the last autosave
    d = json.loads(side.read_text())
    assert len(d["cols"]["t"]) == 50 and d["track_file"]["rows"] == 400  # the JSON holds only the latest rows
    track_file = Path(d["track_file"]["file"])
    assert track_file == Path(f"{side}.track") and track_file.exists()
    assert live.has_outline() and len(live) == 450
    _walk(s, 120, 450)  # rows after the last autosave (one more chunk written): lost with the crash, as before
    # "crash": the session never finishes (its files are closed, as when the program dies); the experiment is opened
    # again
    s._track.close()
    rec = RecoveredSession(json.loads(side.read_text()))
    _same(live, rec._track.build(s.fps))
    rec._track.close()
    proj2 = Project.load(proj.path)
    [t] = recover(proj2)
    tr = proj2.load_tracks(t)[0]
    assert t.id == test.id and t.status == "tracked" and len(tr) == 450
    np.testing.assert_allclose(tr.t, live.t)
    assert tr.has_outline() and sum(o is not None for o in tr.outline) == sum(o is not None for o in live.outline)
    assert not side.exists() and not track_file.exists()  # the side files go once the test is saved


def test_recovery_after_a_crash_in_the_middle_of_a_chunk(tmp_path):
    side = tmp_path / "x.autosave.json"
    s = _session(autosave_path=str(side), autosave_s=1000)
    _walk(s, 330)
    s.flush_autosave()
    d = json.loads(side.read_text())
    live = s._track.build(s.fps)
    s._track.close()  # the crash
    f = Path(d["track_file"]["file"])
    assert d["track_file"]["rows"] == 300
    with open(f, "r+b") as fh:  # the last chunk was being written when the computer went off
        fh.truncate(f.stat().st_size - 100)
    rec = RecoveredSession(d)
    tr = rec._track.build(s.fps)
    assert len(tr) == 230 and "200 of its 300 rows" in rec._track.error  # two whole chunks, then the last rows
    np.testing.assert_array_equal(tr.t, np.concatenate([live.t[:200], live.t[300:]]))
    rec.remove_side_files()
    assert not f.exists()


def test_old_crash_recovery_files_with_the_whole_track_still_load():
    cols = {c: [float(i) for i in range(5)] for c in COLUMNS}
    cols["outline"] = [[[1, 2], [3, 4], [5, 6]], None]  # nested lists, two of five rows
    rec = RecoveredSession({"cols": cols})
    tr = rec.track()
    assert len(tr) == 5 and rec.elapsed == 4.0 and tr.outline[0].tolist() == [[1, 2], [3, 4], [5, 6]]
    assert tr.outline[2] is None


def test_live_session_reads_its_spilled_track(tmp_path):
    s = _session()
    _walk(s, 260)
    assert len(s._track._chunks) == 2 and len(s.cols["t"]) == 260 and s.has_data
    assert s.elapsed == pytest.approx(259 / 25)
    raw = s._track.build(s.fps)
    np.testing.assert_array_equal(np.array(s.trail(250)), np.column_stack([raw.x, raw.y])[-250:])  # disk + memory
    lc = LiveCharts()
    t, _x = lc.series(s, "X position", window_s=2.0)
    assert len(t) and t[-1] == pytest.approx(s.elapsed) and t[0] >= s.elapsed - 2.0 - 1e-6
    t, d = lc.series(s, "Time mobile", window_s=1000.0)  # a running total over the whole test
    assert len(d) == 260 and np.all(np.diff(d) >= 0)
    s.finish()
    assert len(s.track()) == 260
    s._track.close()  # saved, its side file removed, still shown on the monitor: no chart, no error
    assert LiveCharts().series(s, "Time mobile")[0].size == 0 and s.elapsed == pytest.approx(259 / 25)
