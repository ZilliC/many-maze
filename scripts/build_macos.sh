#!/usr/bin/env bash
# Build mANY-MAZE.app and a .dmg on macOS (native arm64 on Apple Silicon).
# Usage: scripts/build_macos.sh            (uses python3.12 / python3 from PATH)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-$(command -v python3.12 || command -v python3)}
echo "Using $PY ($($PY -c 'import platform; print(platform.machine())'))"
$PY -m venv build/venv
source build/venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -e ".[dev,serial]"
python scripts/make_icns.py
pyinstaller packaging/manymaze.spec --noconfirm --clean --distpath dist --workpath build/pyinstaller
APP="dist/mANY-MAZE.app"
# ad-hoc signature so Gatekeeper on Apple Silicon allows running the arm64 binaries
codesign --force --deep --sign - --entitlements packaging/entitlements.plist "$APP"
VERSION=$(python -c "import manymaze; print(manymaze.__version__)")
DMG="dist/mANY-MAZE-${VERSION}-$(uname -m).dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "mANY-MAZE" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Built $APP and $DMG"
