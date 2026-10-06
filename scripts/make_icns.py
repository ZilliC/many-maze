"""Render assets/icon.svg into an .iconset and (on macOS) an .icns via iconutil."""

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    svg = QSvgRenderer(str(ROOT / "assets" / "icon.svg"))
    iconset = ROOT / "packaging" / "mANY-MAZE.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            img = QImage(px, px, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            svg.render(p, QRectF(0, 0, px, px))
            p.end()
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            img.save(str(iconset / name))
    (ROOT / "manymaze" / "resources").mkdir(exist_ok=True)
    shutil.copy(iconset / "icon_256x256@2x.png", ROOT / "manymaze" / "resources" / "icon.png")
    if sys.platform == "darwin":
        subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "packaging" / "mANY-MAZE.icns")])
        print("wrote packaging/mANY-MAZE.icns")
    else:
        print(f"wrote {iconset} (run on macOS to build the .icns)")


if __name__ == "__main__":
    main()
