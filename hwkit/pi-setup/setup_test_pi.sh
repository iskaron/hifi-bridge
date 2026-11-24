#!/bin/sh
# Prepare a STOCK Raspberry Pi OS Lite (trixie) test installation for the hardware validation (NOT the appliance image).
# Run ONLY on the dedicated test Pi, by the owner:  sudo sh setup_test_pi.sh --i-am-on-the-test-pi [--headless | --desktop]
# It installs packages and copies the configuration files of this directory. It refuses to run anywhere else. Idempotent.
#   --headless   install the WirePlumber seat-monitoring override (default when the system does not boot into graphical.target)
#   --desktop    do not install it (a Pi with a local desktop session keeps WirePlumber's seat handling)
# HIFI_SETUP_ROOT=<dir> (tests only): DRY RUN into <dir> - files go below <dir>, packages are only listed, no service is touched.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${HIFI_SETUP_ROOT:-}
CONFIRMED=no MODE=auto
for arg in "$@"; do
  case "$arg" in
    --i-am-on-the-test-pi) CONFIRMED=yes ;;
    --headless) MODE=headless ;;
    --desktop) MODE=desktop ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ "$CONFIRMED" != yes ]; then
  echo "refusing: pass --i-am-on-the-test-pi to confirm this is the dedicated test Raspberry Pi" >&2; exit 2
fi
MODEL=$(tr -d '\0' < "$ROOT/proc/device-tree/model" 2>/dev/null || true)
case "$MODEL" in
  *"Raspberry Pi"*) echo "board: $MODEL" ;;
  *) echo "refusing: this is not a Raspberry Pi (model: ${MODEL:-unknown})" >&2; exit 3 ;;
esac
[ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 4; }

# libspa-0.2-bluetooth is PipeWire's BlueZ plugin. It is only a *Recommends* of the PipeWire packages, so with --no-install-recommends
# BlueZ never gets a local A2DP endpoint ("Audio Sink" UUID 0000110b missing in `bluetoothctl show`) - G1 finding 2026-11-24.
PACKAGES="bluez pipewire pipewire-pulse wireplumber pipewire-bin libspa-0.2-bluetooth pulseaudio-utils python3 python3-jeepney python3-numpy"
if [ -n "$ROOT" ]; then
  echo "dry run: would install: $PACKAGES"
else
  apt-get update
  # shellcheck disable=SC2086  # word splitting of the package list is intended
  apt-get install -y --no-install-recommends $PACKAGES
fi

install -D -m 0644 "$HERE/10-hifi-bridge-rates.conf" "$ROOT/etc/pipewire/pipewire.conf.d/10-hifi-bridge-rates.conf"
# The phone's audio must be a recordable source, not a stream played on the Pi's headphone jack (G1; docs/PIPEWIRE_CAPTURE.md).
install -D -m 0644 "$HERE/50-hifi-bridge-a2dp-input.conf" "$ROOT/etc/wireplumber/wireplumber.conf.d/50-hifi-bridge-a2dp-input.conf"

# Seat monitoring: WirePlumber enables Bluetooth audio only while the user has an ACTIVE LOCAL seat session (systemd-logind). A headless
# Pi used over SSH has logind but no active seat, so the A2DP endpoints never appear (G1). The old rule "install only when loginctl is
# missing" was wrong: logind is present on Pi OS Lite.
if [ "$MODE" = auto ]; then
  if [ -n "$ROOT" ]; then
    TARGET=$(basename "$(readlink "$ROOT/etc/systemd/system/default.target" 2>/dev/null || echo multi-user.target)")
  else
    TARGET=$(systemctl get-default 2>/dev/null || echo unknown)
  fi
  if [ "$TARGET" = graphical.target ]; then MODE=desktop; else MODE=headless; fi
  echo "default target: $TARGET -> $MODE"
fi
if [ "$MODE" = headless ]; then
  install -D -m 0644 "$HERE/wireplumber-no-logind.lua" "$ROOT/etc/wireplumber/bluetooth.lua.d/80-no-logind.lua"
  install -D -m 0644 "$HERE/wireplumber-no-logind.conf" "$ROOT/etc/wireplumber/wireplumber.conf.d/80-no-logind.conf"
  echo "headless: WirePlumber seat monitoring disabled for Bluetooth (80-no-logind.conf)"
else
  echo "desktop: WirePlumber seat monitoring left on (Bluetooth audio needs an active local session; use --headless to change)"
fi

USER_NAME=${SUDO_USER:-}
if [ -z "$ROOT" ] && [ -n "$USER_NAME" ] && [ "$USER_NAME" != root ]; then
  if systemctl --user -M "$USER_NAME@" restart wireplumber pipewire pipewire-pulse 2>/dev/null; then
    echo "restarted PipeWire/WirePlumber of $USER_NAME"
  else
    echo "could not restart PipeWire/WirePlumber of $USER_NAME: reboot (or log in as $USER_NAME: systemctl --user restart wireplumber)"
  fi
fi
echo "done. Not enabled automatically: bluetooth-override.conf (--experimental, plan A1) and main.conf.snippet."
echo "next: reconnect the phone (the node is created with the new role), then: python3 -m hwkit preflight"
