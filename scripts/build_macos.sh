#!/usr/bin/env bash
# Build mANY-MAZE.app and a .dmg on macOS (native arm64 on Apple Silicon).
# Usage: scripts/build_macos.sh            (uses python3.12 / python3 from PATH)
#
# Signing (see README, "Signed and notarised macOS builds"):
#   * nothing set: ad-hoc signature (runs on Apple Silicon, but Gatekeeper asks on first launch);
#   * MACOS_CERTIFICATE (base64 of a "Developer ID Application" .p12) + MACOS_CERTIFICATE_PASSWORD: imported into a
#     temporary keychain (deleted at the end), or MACOS_SIGN_IDENTITY naming an identity already in a keychain:
#     every binary is signed with the hardened runtime, a secure timestamp and packaging/entitlements.plist;
#   * notarisation, if also set — an App Store Connect API key: APPLE_API_KEY (base64 of the .p8 file),
#     APPLE_API_KEY_ID, APPLE_API_ISSUER; or an Apple ID: APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
#     (app-specific password): the app and the DMG are submitted with `notarytool submit --wait` and stapled.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-$(command -v python3.12 || command -v python3)}
echo "Using $PY ($($PY -c 'import platform; print(platform.machine())'))"
$PY -m venv build/venv
# shellcheck disable=SC1091
source build/venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -e ".[dev,serial]"
python scripts/make_icns.py
pyinstaller packaging/manymaze.spec --noconfirm --clean --distpath dist --workpath build/pyinstaller
APP="dist/mANY-MAZE.app"
ENTITLEMENTS="packaging/entitlements.plist"
VERSION=$(python -c "import manymaze; print(manymaze.__version__)")
DMG="dist/mANY-MAZE-${VERSION}-$(uname -m).dmg"

# ------------------------------------------------------------------------------------------------ signing identity
IDENTITY=${MACOS_SIGN_IDENTITY:-}
KEYCHAIN=""
OLD_KEYCHAINS=()
WORK=$(mktemp -d)

cleanup() {
    if [ -n "$KEYCHAIN" ]; then
        if [ ${#OLD_KEYCHAINS[@]} -gt 0 ]; then
            security list-keychains -d user -s "${OLD_KEYCHAINS[@]}" || true  # restore the search list
        fi
        security delete-keychain "$KEYCHAIN" || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT

if [ -n "${MACOS_CERTIFICATE:-}" ]; then
    : "${MACOS_CERTIFICATE_PASSWORD:?MACOS_CERTIFICATE is set but MACOS_CERTIFICATE_PASSWORD is not}"
    echo "Importing the Developer ID certificate into a temporary keychain"
    KEYCHAIN="$WORK/manymaze-signing.keychain-db"
    KEYCHAIN_PASSWORD=${MACOS_KEYCHAIN_PASSWORD:-$(uuidgen)}
    while IFS= read -r kc; do
        kc=${kc#*\"}
        OLD_KEYCHAINS+=("${kc%\"*}")
    done < <(security list-keychains -d user)
    security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
    security set-keychain-settings -lut 21600 "$KEYCHAIN"
    security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
    printf '%s' "$MACOS_CERTIFICATE" | base64 --decode > "$WORK/certificate.p12"
    security import "$WORK/certificate.p12" -k "$KEYCHAIN" -P "$MACOS_CERTIFICATE_PASSWORD" -f pkcs12 \
        -T /usr/bin/codesign -T /usr/bin/security -T /usr/bin/productsign
    rm -f "$WORK/certificate.p12"
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
    security list-keychains -d user -s "$KEYCHAIN" ${OLD_KEYCHAINS[@]+"${OLD_KEYCHAINS[@]}"}
    if [ -z "$IDENTITY" ]; then
        IDENTITY=$(security find-identity -v -p codesigning "$KEYCHAIN" \
            | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -n 1)
    fi
    if [ -z "$IDENTITY" ]; then
        echo "No 'Developer ID Application' identity in MACOS_CERTIFICATE" >&2
        exit 1
    fi
fi

KC_ARGS=()
if [ -n "$KEYCHAIN" ]; then
    KC_ARGS=(--keychain "$KEYCHAIN")
fi

sign() {  # sign one file or bundle with the Developer ID identity, hardened runtime and secure timestamp
    codesign --force --sign "$IDENTITY" ${KC_ARGS[@]+"${KC_ARGS[@]}"} --options runtime --timestamp "$@"
}

if [ -z "$IDENTITY" ]; then
    # ad-hoc signature so Gatekeeper on Apple Silicon allows running the arm64 binaries
    echo "No signing identity: ad-hoc signature (Gatekeeper will ask on first launch)"
    codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$APP"
else
    echo "Signing with: $IDENTITY"
    # inside out: every Mach-O file (dylibs, Python extension modules, helper executables), then the nested
    # frameworks / bundles (contents before their container), then the app itself with the entitlements
    # (codesign --deep is deprecated for signing)
    MAIN_EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
    find "$APP/Contents" -type f ! -path "$MAIN_EXE" -print0 \
        | while IFS= read -r -d '' f; do
            desc=$(file -b "$f")
            case "$desc" in
                *Mach-O*executable*) sign --entitlements "$ENTITLEMENTS" "$f" ;;  # helper: the app's rights
                *Mach-O*) sign "$f" ;;  # dylib, Python extension module (bundle)
                *) ;;
            esac
        done
    find "$APP/Contents" -depth \( -name "*.framework" -o -name "*.bundle" -o -name "*.app" -o -name "*.xpc" \) \
        -type d -print0 \
        | while IFS= read -r -d '' b; do
            # only real bundles (a directory merely named *.bundle cannot be signed)
            if [ -e "$b/Contents/Info.plist" ] || [ -e "$b/Resources/Info.plist" ] || [ -d "$b/Versions" ]; then
                sign "$b"
            fi
        done
    sign --entitlements "$ENTITLEMENTS" "$APP"
    codesign --verify --deep --strict --verbose=2 "$APP"
fi

# ------------------------------------------------------------------------------------------------ notarisation
NOTARY_ARGS=()
if [ -n "$IDENTITY" ]; then
    if [ -n "${APPLE_API_KEY:-}" ] && [ -n "${APPLE_API_KEY_ID:-}" ] && [ -n "${APPLE_API_ISSUER:-}" ]; then
        printf '%s' "$APPLE_API_KEY" | base64 --decode > "$WORK/AuthKey_${APPLE_API_KEY_ID}.p8"
        NOTARY_ARGS=(--key "$WORK/AuthKey_${APPLE_API_KEY_ID}.p8" --key-id "$APPLE_API_KEY_ID"
                     --issuer "$APPLE_API_ISSUER")
    elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
        NOTARY_ARGS=(--apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD")
    else
        echo "Signed but not notarised: set APPLE_API_KEY / APPLE_API_KEY_ID / APPLE_API_ISSUER or" \
             "APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD to notarise"
    fi
fi

json_field() {  # a field of the JSON on stdin ("" if it is not JSON)
    python -c 'import json, sys
try:
    print(json.load(sys.stdin).get(sys.argv[1], ""))
except ValueError:
    print("")' "$1"
}

notarise() {  # submit a .zip / .dmg, wait for Apple's verdict, show the log if it is rejected
    local out status id
    out=$(xcrun notarytool submit "$1" "${NOTARY_ARGS[@]}" --wait --timeout 2h --output-format json) || true
    echo "$out"
    status=$(json_field status <<<"$out")
    if [ "$status" != "Accepted" ]; then
        id=$(json_field id <<<"$out")
        if [ -n "$id" ]; then
            xcrun notarytool log "$id" "${NOTARY_ARGS[@]}" || true
        fi
        echo "Notarisation of $1 failed (status: ${status:-unknown})" >&2
        exit 1
    fi
}

if [ ${#NOTARY_ARGS[@]} -gt 0 ]; then
    echo "Notarising the app"
    ditto -c -k --keepParent "$APP" "$WORK/mANY-MAZE.zip"
    notarise "$WORK/mANY-MAZE.zip"
    xcrun stapler staple "$APP"
    xcrun stapler validate "$APP"
fi

# ------------------------------------------------------------------------------------------------ disk image
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "mANY-MAZE" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
if [ -n "$IDENTITY" ]; then
    codesign --force --sign "$IDENTITY" ${KC_ARGS[@]+"${KC_ARGS[@]}"} --timestamp "$DMG"
fi
if [ ${#NOTARY_ARGS[@]} -gt 0 ]; then
    echo "Notarising the disk image"
    notarise "$DMG"
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    spctl --assess --type open --context context:primary-signature -v "$DMG"
    spctl --assess --type execute -v "$APP"
fi
echo "Built $APP and $DMG"
