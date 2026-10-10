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
                    "_results_cache")] + ["matplotlib.backends.backend_qtagg", "manymaze.core.batch",
                                          "manymaze.core.pose", "manymaze.gui.pose_model", "onnx", "onnx.helper",
                                          "onnx.numpy_helper"] +
                  ["manymaze.core." + m for m in ("iodevices", "operant", "periods", "sequences", "charts",
                                                  "videoexport", "workflow", "camera", "livegroup", "security",
                                                  "sync")] +
                  # imported only when a protected experiment is opened (PyInstaller's hook bundles its Rust
                  # extension and OpenSSL)
                  ["cryptography.hazmat.primitives.ciphers.aead"] +
                  ["manymaze.gui." + m for m in ("procedure_editor", "touchscreen", "live_widgets", "confirm_id", "import_wizard",
                                                 "ribbon", "theme", "icons", "pages.protocol_pages",
                                                 "security_dialog")] +
                  ["manymaze.core.importers"],
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
            "LSApplicationCategoryType": "public.app-category.education",
            # a .mmaze experiment is a folder, deliberately NOT a package (com.apple.package / LSTypeIsPackage): a
            # package shows in Finder as a single file, which hides the exports/ and recordings/ users need to
            # reach (Finder, Prism, R, video players) and makes it unselectable in "Open experiment folder"
            # dialogs. The cost: double-clicking a .mmaze folder opens it in Finder; open it with File > Open or
            # drop it on the app (the document type below keeps "Open With mANY-MAZE").
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "org.manymaze.mmaze",
                "UTTypeDescription": "mANY-MAZE experiment",
                "UTTypeConformsTo": ["public.folder"],
                "UTTypeTagSpecification": {"public.filename-extension": ["mmaze"]},
            }],
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "mANY-MAZE experiment",
                "LSItemContentTypes": ["org.manymaze.mmaze"],
                "CFBundleTypeRole": "Editor",
            }],
        },
    )
