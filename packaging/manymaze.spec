# PyInstaller spec for mANY-MAZE.  Build with:  pyinstaller packaging/manymaze.spec --noconfirm
# On macOS this produces dist/mANY-MAZE.app (arm64 when built with an arm64 Python on Apple Silicon).
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from manymaze import __version__  # noqa: E402

icns = ROOT / "packaging" / "mANY-MAZE.icns"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "manymaze" / "resources"), "manymaze/resources")],
    hiddenimports=["manymaze.gui.pages." + m for m in
                   ("experiment", "animals", "apparatus", "tests", "testview", "live", "results", "statistics",
                    "_results_cache")] + ["matplotlib.backends.backend_qtagg"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mANY-MAZE",
    console=False,
    target_arch=None,  # native: arm64 on Apple Silicon
    codesign_identity=None,
    entitlements_file=str(ROOT / "packaging" / "entitlements.plist") if sys.platform == "darwin" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mANY-MAZE")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="mANY-MAZE.app",
        icon=str(icns) if icns.exists() else None,
        bundle_identifier="org.manymaze.app",
        version=__version__,
        info_plist={
            "CFBundleName": "mANY-MAZE",
            "CFBundleDisplayName": "mANY-MAZE",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": True,  # light UI, matching the plots
            "NSCameraUsageDescription": "mANY-MAZE uses the camera to track animals during live tests.",
            "NSMicrophoneUsageDescription": "mANY-MAZE may record audio together with live test videos.",
            "LSApplicationCategoryType": "public.app-category.education",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "mANY-MAZE experiment",
                "CFBundleTypeExtensions": ["mmaze"],
                "CFBundleTypeRole": "Editor",
                "LSTypeIsPackage": False,
            }],
        },
    )
