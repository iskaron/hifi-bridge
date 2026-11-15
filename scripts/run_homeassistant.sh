#!/usr/bin/env bash
# INTEGRATION with a REAL Home Assistant (2026.11.1, pinned) + REAL Mosquitto: fresh HA config every run; the appliance service runs in
# the lab runner. Prints the HA log lines that mention MQTT warnings/errors about our discovery payloads.
set -uo pipefail
cd "$(dirname "$0")/.."  || exit 1
scripts/lab_up.sh >/dev/null
cd docker || exit 1
docker compose --profile ha rm -sf homeassistant >/dev/null 2>&1
docker volume rm -f btbridge-lab_ha-config >/dev/null 2>&1
# fresh broker: retained discovery configs of earlier lab runs (other instance ids) must not leak into this run
docker compose rm -sf mosquitto >/dev/null 2>&1 && docker compose up -d mosquitto >/dev/null 2>&1
docker compose --profile ha up -d homeassistant >/dev/null 2>&1
docker compose run --rm -T -e PYTHONPATH=/work/bridge:/work/testkit:/work/tests/homeassistant runner \
  python3 -m pytest -p no:cacheprovider -v tests/homeassistant "$@"; rc=$?
echo "### Home Assistant log: MQTT warnings/errors"
docker compose --profile ha logs --no-color homeassistant 2>/dev/null | grep -iE "(warning|error).*(mqtt|hifibridge|btbridge)" | head -20 || true
docker compose --profile ha rm -sf homeassistant >/dev/null 2>&1
exit $rc
