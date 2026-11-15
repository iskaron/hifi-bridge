#!/bin/sh
# Prepare a STOCK Raspberry Pi OS Lite test installation for the hardware validation (NOT the appliance image).
# Run ONLY on the dedicated test Pi, by the owner:  sudo sh setup_test_pi.sh --i-am-on-the-test-pi
# It installs packages and copies the configuration files of this directory. It refuses to run anywhere else.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "${1:-}" != "--i-am-on-the-test-pi" ]; then
  echo "refusing: pass --i-am-on-the-test-pi to confirm this is the dedicated test Raspberry Pi" >&2; exit 2
fi
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)
case "$MODEL" in
  *"Raspberry Pi"*) echo "board: $MODEL" ;;
  *) echo "refusing: this is not a Raspberry Pi (model: ${MODEL:-unknown})" >&2; exit 3 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 4; }
apt-get update
apt-get install -y --no-install-recommends bluez pipewire pipewire-pulse wireplumber pipewire-bin pulseaudio-utils python3 python3-jeepney \
  python3-numpy
install -D -m 0644 "$HERE/10-hifi-bridge-rates.conf" /etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf
if ! command -v loginctl >/dev/null 2>&1; then
  install -D -m 0644 "$HERE/wireplumber-no-logind.lua" /etc/wireplumber/bluetooth.lua.d/80-no-logind.lua
  install -D -m 0644 "$HERE/wireplumber-no-logind.conf" /etc/wireplumber/wireplumber.conf.d/80-no-logind.conf
fi
echo "done. Not enabled automatically: bluetooth-override.conf (--experimental, plan A1) and main.conf.snippet."
echo "next: reboot, then: python3 -m hwkit preflight"
