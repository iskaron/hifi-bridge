#!/bin/sh
# Linux package of the flash tool (run INSIDE btbridge-flash:dev via scripts/run_flashtool.sh package). Builds the PyInstaller one-folder
# app, collects the licence texts, adds the .desktop file, the polkit policy and the installer, then SMOKE-TESTS the result offscreen:
#   * the packaged GUI builds its whole window (--self-test);
#   * the packaged helper writes and verifies a SIMULATED card through its real stdio protocol;
#   * install.sh installs into a scratch DESTDIR (never the real system).
# Output: build/flashtool/hifi-bridge-flash-<version>-linux-<arch>.tar.gz (+ .sha256). Nothing is signed or published.
set -eu
REPO=/work
OUT=$REPO/build/flashtool
VERSION=$(python3 -c 'import sys; sys.path.insert(0, "/work/flashtool"); import hififlash; print(hififlash.__version__)')
ARCH=$(uname -m)
APP=$OUT/dist/hifi-bridge-flash
rm -rf "$OUT"
mkdir -p "$OUT"
cd /tmp
echo "### pyinstaller (one-folder)"
pyinstaller --noconfirm --clean --log-level WARN --distpath "$OUT/dist" --workpath /tmp/pyi-work "$REPO/flashtool/packaging/hifi-bridge-flash.spec"
echo "### licences"
python3 "$REPO/flashtool/packaging/collect_licenses.py" "$APP/licenses"
for f in LGPL-3.0.txt GPL-3.0.txt THIRD_PARTY_NOTICES.txt LICENSE-hifi-bridge.txt; do test -s "$APP/licenses/$f"; done
grep -q "LGPL-3.0" "$APP/licenses/THIRD_PARTY_NOTICES.txt"
ls "$APP"/_internal/PySide6/Qt/lib/libQt6Core.so.6 >/dev/null   # Qt stays a separate, replaceable shared library
echo "### desktop integration"
cp "$REPO/flashtool/packaging/linux/hifi-bridge-flash.desktop" "$REPO/flashtool/packaging/linux/io.github.iskaron.hifibridge.flash.policy" \
   "$REPO/flashtool/packaging/linux/install.sh" "$APP/"
python3 - "$APP/hifi-bridge-flash.png" <<'EOF'
import sys
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt
app = QGuiApplication([])
img = QImage(256, 256, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
QSvgRenderer("/work/flashtool/hififlash/gui/assets/logo.svg").render(p)
p.end()
img.save(sys.argv[1])
EOF
echo "### smoke test: GUI"
"$APP/hifi-bridge-flash" --self-test --lang de
echo "### smoke test: helper (simulated card, real protocol)"
"$APP/hififlash-helper" --version
SMOKE=/tmp/smoke
rm -rf "$SMOKE"
mkdir -p "$SMOKE"
python3 "$REPO/flashtool/packaging/linux/smoke_helper.py" "$APP/hififlash-helper" "$SMOKE"
echo "### smoke test: install.sh into a scratch DESTDIR"
DESTDIR=/tmp/destdir sh "$APP/install.sh"
test -x /tmp/destdir/opt/hifi-bridge-flash/hififlash-helper
test -f /tmp/destdir/usr/share/polkit-1/actions/io.github.iskaron.hifibridge.flash.policy
test -f /tmp/destdir/usr/share/applications/hifi-bridge-flash.desktop
echo "### archive"
TAR="$OUT/hifi-bridge-flash-$VERSION-linux-$ARCH.tar.gz"
tar -C "$OUT/dist" -czf "$TAR" hifi-bridge-flash
(cd "$OUT" && sha256sum "$(basename "$TAR")" > "$(basename "$TAR").sha256")
du -sh "$APP" "$TAR"
echo "package ok: $TAR"
