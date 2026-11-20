#!/bin/sh
# macOS: sign the flash tool's .app with the owner's Developer ID Application certificate (hardened runtime, secure timestamp, inside-out -
# never `codesign --deep`), notarise and staple it, wrap it in a signed, notarised and stapled .dmg. Runs ON A MAC only (GitHub Actions
# workflow .github/workflows/flashtool-macos.yml, job "sign", or the owner's Mac). Setup and secrets: docs/MACOS_SIGNING.md. No certificate,
# key or password is part of this repository; nothing here was executed in the repository's Docker lab (no macOS runtime, G3 F-6).
#
# Input (environment):
#   APP        path of the unsigned .app (default: build/flashtool-macos/dist/Hi-Fi Bridge Flash.app)
#   IDENTITY   "Developer ID Application: <name> (<TEAMID>)" - must already be in a keychain of the search list
#   KEYCHAIN   optional: the keychain that holds IDENTITY (CI: a temporary keychain)
#   notarisation, ONE of:
#     NOTARY_KEY_FILE + NOTARY_KEY_ID + NOTARY_ISSUER   App Store Connect API key (.p8 file path, key id, issuer id) - CI
#     NOTARY_PROFILE                                     a `xcrun notarytool store-credentials` keychain profile - the owner's Mac
#   SKIP_NOTARIZE=1   sign and verify only (e.g. to test the certificate import without using the notary service)
# Output: build/flashtool-macos/hifi-bridge-flash-<version>-macos-<arch>.dmg (+ .sha256)
set -eu
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OUT=$REPO/build/flashtool-macos
APP=${APP:-"$OUT/dist/Hi-Fi Bridge Flash.app"}
ENT="$REPO/flashtool/packaging/macos/entitlements.plist"
[ "$(uname -s)" = Darwin ] || { echo "sign_and_notarize.sh: macOS only" >&2; exit 2; }
: "${IDENTITY:?set IDENTITY to the Developer ID Application identity}"
[ -d "$APP" ] || { echo "no app at $APP (run build.sh first)" >&2; exit 2; }
VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo 0)
ARCH=$(uname -m)
KC_ARG=""
[ -n "${KEYCHAIN:-}" ] && KC_ARG="--keychain $KEYCHAIN"

sign() {   # $1 = path; hardened runtime + secure timestamp + the (empty) entitlements
  # shellcheck disable=SC2086
  codesign --force --options runtime --timestamp --entitlements "$ENT" $KC_ARG --sign "$IDENTITY" "$1"
}

echo "### sign inside-out: every Mach-O file, deepest first"
find "$APP/Contents" -type f -print | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2- | while IFS= read -r f; do
  if file -b "$f" | grep -q 'Mach-O'; then
    case "$f" in "$APP/Contents/MacOS/hifi-bridge-flash"|"$APP/Contents/MacOS/hififlash-helper") continue ;; esac
    sign "$f"
  fi
done
echo "### sign framework bundles, deepest first"
find "$APP/Contents" -type d -name '*.framework' -print | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2- | while IFS= read -r d; do
  sign "$d"
done
echo "### sign the privileged helper, then the GUI, then the bundle"
sign "$APP/Contents/MacOS/hififlash-helper"
sign "$APP/Contents/MacOS/hifi-bridge-flash"
sign "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign --display --verbose=2 "$APP/Contents/MacOS/hififlash-helper" 2>&1 | grep -E '^(Identifier|Authority|TeamIdentifier|Runtime)'

notarize() {   # $1 = file to submit; fails unless Apple reports "Accepted" (prints the notary log otherwise)
  if [ -n "${NOTARY_KEY_FILE:-}" ]; then
    set -- "$1" --key "$NOTARY_KEY_FILE" --key-id "${NOTARY_KEY_ID:?}" --issuer "${NOTARY_ISSUER:?}"
  else
    set -- "$1" --keychain-profile "${NOTARY_PROFILE:?set NOTARY_PROFILE or NOTARY_KEY_FILE/NOTARY_KEY_ID/NOTARY_ISSUER}"
  fi
  file=$1; shift
  xcrun notarytool submit "$file" "$@" --wait --output-format json > "$OUT/notary.json"
  status=$(python3 -c "import json; print(json.load(open('$OUT/notary.json')).get('status', ''))")
  id=$(python3 -c "import json; print(json.load(open('$OUT/notary.json')).get('id', ''))")
  echo "notarisation of $(basename "$file"): $status ($id)"
  if [ "$status" != Accepted ]; then
    if [ -n "$id" ]; then xcrun notarytool log "$id" "$@" || true; fi
    exit 1
  fi
}

if [ "${SKIP_NOTARIZE:-0}" = 1 ]; then
  echo "SKIP_NOTARIZE=1: signed and verified, not notarised"; exit 0
fi
echo "### notarise + staple the app"
ZIP="$OUT/notarize-app.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
notarize "$ZIP"
rm -f "$ZIP"
xcrun stapler staple "$APP"
spctl --assess --type execute --verbose "$APP"
echo "### disk image: create, sign, notarise, staple"
DMG="$OUT/hifi-bridge-flash-$VERSION-macos-$ARCH.dmg"
STAGE="$OUT/dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Hi-Fi Bridge Flash.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Hi-Fi Bridge Flash" -srcfolder "$STAGE" -format UDZO -fs HFS+ "$DMG"
rm -rf "$STAGE"
# shellcheck disable=SC2086
codesign --force --timestamp $KC_ARG --sign "$IDENTITY" "$DMG"
notarize "$DMG"
xcrun stapler staple "$DMG"
spctl --assess --type open --context context:primary-signature --verbose "$DMG"
(cd "$OUT" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "signed + notarised: $DMG"
