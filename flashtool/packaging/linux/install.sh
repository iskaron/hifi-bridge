#!/bin/sh
# Install the unpacked flash tool on a Linux desktop (run by the OWNER with sudo on their own computer, from the unpacked folder):
#     sudo sh install.sh            (or DESTDIR=/some/root sh install.sh for a staged install; the package smoke test uses that)
# Installs to /opt/hifi-bridge-flash, the polkit policy (so pkexec shows a specific prompt for ONLY the write helper), the desktop
# entry and the icon. The GUI itself always runs as the normal user.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
D=${DESTDIR:-}
PREFIX=$D/opt/hifi-bridge-flash
mkdir -p "$PREFIX" "$D/usr/share/polkit-1/actions" "$D/usr/share/applications" "$D/usr/share/icons/hicolor/256x256/apps" "$D/usr/local/bin"
cp -R "$HERE/." "$PREFIX/"
chmod 0755 "$PREFIX/hififlash-helper" "$PREFIX/hifi-bridge-flash"
install -m 0644 "$HERE/io.github.iskaron.hifibridge.flash.policy" "$D/usr/share/polkit-1/actions/"
install -m 0644 "$HERE/hifi-bridge-flash.desktop" "$D/usr/share/applications/"
install -m 0644 "$HERE/hifi-bridge-flash.png" "$D/usr/share/icons/hicolor/256x256/apps/"
ln -sf /opt/hifi-bridge-flash/hifi-bridge-flash "$D/usr/local/bin/hifi-bridge-flash"
echo "installed to $PREFIX"
