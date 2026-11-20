#!/bin/sh
# macOS package of the flash tool - runs ON A MAC (the GitHub Actions workflow .github/workflows/flashtool-macos.yml or the owner's Mac),
# never in this repository's Docker lab. Builds the UNSIGNED one-folder .app with PyInstaller, collects the licence texts, checks that Qt
# stays a separate replaceable framework (LGPL) and that LSMinimumSystemVersion is not below what the bundled Qt needs, then smoke-tests:
#   * the packaged GUI builds its whole window offscreen (--self-test);
#   * the packaged helper writes and verifies a SIMULATED card (a regular file) through its real stdio protocol - no disk is touched.
# Signing and notarisation are a separate step (sign_and_notarize.sh), so an unsigned build never needs a secret.
# Usage: sh flashtool/packaging/macos/build.sh        Output: build/flashtool-macos/dist/Hi-Fi Bridge Flash.app (+ an .app.tar.gz)
set -eu
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
OUT=$REPO/build/flashtool-macos
APP="$OUT/dist/Hi-Fi Bridge Flash.app"
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$REPO/flashtool'); import hififlash; print(hififlash.__version__)")
ARCH=$(uname -m)
[ "$(uname -s)" = Darwin ] || { echo "build.sh: macOS only" >&2; exit 2; }
rm -rf "$OUT"
mkdir -p "$OUT"
echo "### pyinstaller (one-folder .app)"
pyinstaller --noconfirm --clean --log-level WARN --distpath "$OUT/dist" --workpath "$OUT/work" "$REPO/flashtool/packaging/hifi-bridge-flash.spec"
test -x "$APP/Contents/MacOS/hifi-bridge-flash"
test -x "$APP/Contents/MacOS/hififlash-helper"
echo "### licences"
LIC="$APP/Contents/Resources/licenses"
python3 "$REPO/flashtool/packaging/collect_licenses.py" "$LIC"
for f in LGPL-3.0.txt GPL-3.0.txt THIRD_PARTY_NOTICES.txt LICENSE-hifi-bridge.txt; do test -s "$LIC/$f"; done
grep -q "LGPL-3.0" "$LIC/THIRD_PARTY_NOTICES.txt"
echo "### Qt is a separate, replaceable framework (LGPL-3.0)"
QTCORE=$(find "$APP/Contents" -path '*QtCore.framework/Versions/A/QtCore' -type f | head -n 1)
[ -n "$QTCORE" ] || { echo "QtCore.framework not found as a separate framework" >&2; exit 1; }
echo "### minimum macOS version"
NEED=$(vtool -show-build "$QTCORE" | awk '/minos/ {print $2; exit}')
HAVE=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$APP/Contents/Info.plist")
echo "Qt needs macOS $NEED, Info.plist declares $HAVE"
python3 -c "import sys; v=lambda s: tuple(int(x) for x in s.split('.')); sys.exit(0 if v('$HAVE') >= v('$NEED') else 1)" \
  || { echo "LSMinimumSystemVersion $HAVE is below the bundled Qt's minimum $NEED - raise it in hifi-bridge-flash.spec" >&2; exit 1; }
echo "### smoke test: GUI (offscreen)"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/hifi-bridge-flash" --self-test --lang de
echo "### smoke test: helper (simulated card, real protocol)"
"$APP/Contents/MacOS/hififlash-helper" --version
SMOKE=$OUT/smoke
mkdir -p "$SMOKE"
HIFI_REPO="$REPO" python3 "$REPO/flashtool/packaging/linux/smoke_helper.py" "$APP/Contents/MacOS/hififlash-helper" "$SMOKE"
rm -rf "$SMOKE"
echo "### archive (tar keeps the framework symlinks and modes; artifact zips do not)"
TAR="$OUT/hifi-bridge-flash-$VERSION-macos-$ARCH-unsigned.app.tar.gz"
tar -C "$OUT/dist" -czf "$TAR" "Hi-Fi Bridge Flash.app"
(cd "$OUT" && shasum -a 256 "$(basename "$TAR")" > "$(basename "$TAR").sha256")
du -sh "$APP" "$TAR"
echo "package ok (UNSIGNED): $TAR"
