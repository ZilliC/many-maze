"""Camera montages of up to four sources (core.camera): row / column / grid layouts, the SourceSpec and its saved
form, and two-source montages saved by older versions."""

import json

import cv2
import numpy as np
import pytest

from manymaze.core.camera import (MAX_SOURCES, SourceSpec, TransformedSource, camera_settings, merge_frames,
                                  merge_layout, merged_size, merged_sources, set_camera_settings)
from manymaze.core.project import Project


def frames():
    a = np.full((100, 200, 3), 10, np.uint8)
    b = np.full((80, 150, 3), 20, np.uint8)
    c = np.full((120, 160), 30, np.uint8)  # a grey camera
    d = np.full((100, 200, 3), 40, np.uint8)
    return a, b, c, d


def test_merge_layouts():
    a, b, c, d = frames()
    row = merge_frames([a, b, c], layout="row")
    assert row.shape == (120, 510, 3)
    assert row[0, 0, 0] == 10 and row[0, 200, 0] == 20 and row[0, 350, 0] == 30
    assert row[110, 210, 0] == 0  # padded below the smaller image
    col = merge_frames([a, b, c], layout="column")
    assert col.shape == (300, 200, 3) and col[100, 0, 0] == 20 and col[180, 0, 0] == 30 and col[100, 170, 0] == 0
    grid = merge_frames([a, b, c], layout="grid")
    assert grid.shape == (220, 350, 3)  # 2 × 2: columns 200 + 150 wide, rows 100 + 120 high
    assert grid[0, 0, 0] == 10 and grid[0, 200, 0] == 20 and grid[100, 0, 0] == 30
    assert not grid[100:, 200:].any()  # the empty fourth cell is black
    four = merge_frames([a, b, c, d], layout="grid")
    assert four.shape == (220, 400, 3) and four[100, 200, 0] == 40
    for layout in ("row", "column", "grid"):
        assert merged_size([(f.shape[1], f.shape[0]) for f in (a, b, c)], layout) == \
            merge_frames([a, b, c], layout=layout).shape[1::-1]
    # the two-image form and layout names of older versions
    assert merge_frames(a, b, "side").shape == merge_frames([a, b], layout="row").shape == (100, 350, 3)
    assert merge_frames(a, b, "stack").shape == (180, 200, 3)
    assert merge_frames(c, c, "side").ndim == 2  # grey stays grey
    assert [merge_layout(x) for x in ("side", "row", "stack", "column", "grid", "nope", None)] == \
        ["side", "side", "stack", "stack", "grid", "side", "side"]


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("montage")
    out = []
    for i, (w, h) in enumerate(((160, 120), (160, 120), (120, 90), (160, 120))):
        p = d / f"cam{i}.avi"
        wr = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 25, (w, h))
        for k in range(10 + i):
            f = np.full((h, w, 3), 40 + 50 * i, np.uint8)
            cv2.putText(f, str(k), (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            wr.write(f)
        wr.release()
        out.append(str(p))
    return out


def test_three_and_four_source_montage(videos):
    spec = SourceSpec(videos[0], layout="grid")
    spec.merge = videos[1:3]
    assert spec.second == videos[1] and spec.others == [videos[2]] and spec.sources == videos[:3]
    assert spec.key == "+".join(f"file:{v}" for v in videos[:3])
    assert spec.label == " + ".join(f"cam{i}.avi" for i in range(3))
    src = spec.open()
    assert isinstance(src, TransformedSource) and (src.width, src.height) == (320, 210)
    assert src.frame_count == 10  # the shortest source
    ok, f = src.read()
    assert ok and f.shape == (210, 320, 3)
    assert abs(int(f[100, 300, 0]) - 90) < 15 and abs(int(f[200, 60, 0]) - 140) < 15 and not f[200, 300].any()
    assert [r.shape[:2] for r in src.last_raws] == [(120, 160), (120, 160), (90, 120)]
    src.release()
    spec.layout = "side"
    spec.merge = videos[1:] + ["ignored: at most four sources"]
    assert len(spec.sources) == MAX_SOURCES
    with spec.open() as src:
        assert (src.width, src.height) == (600, 120) and src.read()[1].shape == (120, 600, 3)


def test_saved_form(videos):
    spec = SourceSpec(videos[0], layout="stack")
    spec.merge = videos[1:3]
    d = json.loads(json.dumps(spec.to_dict()))
    assert d["second"] == videos[1] and d["merge"] == videos[1:3]  # older versions read the first one
    back = SourceSpec.from_dict(d)
    assert back.merge == videos[1:3] and back.layout == "stack"
    # a two-source montage is saved as before, and one saved by an older version opens as it was
    two = SourceSpec(videos[0], videos[1], "side")
    assert "merge" not in two.to_dict() and two.to_dict()["second"] == videos[1]
    old = SourceSpec.from_dict({"source": 1, "second": 2, "layout": "stack", "view": {}})
    assert old.merge == [2] and old.layout == "stack" and old.key == "camera:1+camera:2"
    assert merged_sources({"second": 3}) == [3] and merged_sources({"second": None, "merge": []}) == []
    assert merged_sources({"merge": [1, None, "", 2, 3, 4]}) == [1, 2, 3]
    assert SourceSpec(0).merge == [] and SourceSpec(0).to_dict()["second"] is None
    # camera options of a montage in the project (and older files without "merge")
    p = Project()
    set_camera_settings(p, "camera:0", {"second": 1, "merge": [1, 2, 3], "layout": "grid"})
    assert merged_sources(camera_settings(p, "camera:0")) == [1, 2, 3]
