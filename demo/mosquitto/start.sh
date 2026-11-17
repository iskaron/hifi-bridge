#!/bin/sh
# Creates random per-installation credentials on first start (kept in the demo's own volumes, shown by `make demo-status`).
# Users: hifi-bridge (for the bridge's MQTT settings) and homeassistant (for Home Assistant's MQTT integration).
set -e
creds=/mosquitto/creds/credentials.env
if [ ! -s "$creds" ]; then
  umask 077
  gen() { head -c 30 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20; }
  printf 'HIFI_BRIDGE_USER=hifi-bridge\nHIFI_BRIDGE_PASSWORD=%s\nHOMEASSISTANT_USER=homeassistant\nHOMEASSISTANT_PASSWORD=%s\n' "$(gen)" "$(gen)" > "$creds"
  chmod 0644 "$creds"   # read by the demo console (internal); the volume is not exposed anywhere else
fi
# shellcheck source=/dev/null
. "$creds"
rm -f /mosquitto/data/passwords
mosquitto_passwd -b -c /mosquitto/data/passwords "$HIFI_BRIDGE_USER" "$HIFI_BRIDGE_PASSWORD"
mosquitto_passwd -b /mosquitto/data/passwords "$HOMEASSISTANT_USER" "$HOMEASSISTANT_PASSWORD"
chown mosquitto:mosquitto /mosquitto/data/passwords 2>/dev/null || true
chmod 0700 /mosquitto/data/passwords
exec mosquitto -c /mosquitto/config/mosquitto.conf
