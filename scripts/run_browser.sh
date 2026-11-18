#!/usr/bin/env bash
# Real-browser UI tests: fresh appliance container (wizard from scratch) + Chromium (Playwright) on the isolated lab network.
# Screenshots and the axe-core report land in reports/screenshots/.
set -uo pipefail
cd "$(dirname "$0")/.."  || exit 1
scripts/lab_up.sh >/dev/null
cd docker || exit 1
docker compose --profile browser build -q browser >/dev/null
docker compose --profile browser rm -sf appliance >/dev/null 2>&1
docker volume rm -f btbridge-lab_appliance-data >/dev/null 2>&1
docker compose --profile browser up -d appliance >/dev/null
for _ in $(seq 60); do docker compose --profile browser exec -T appliance test -f /data/setup-code.txt 2>/dev/null && break; sleep 0.5; done
docker compose --profile browser run --rm -T browser python3 -m pytest -p no:cacheprovider -v tests/browser "$@"; rc=$?
docker compose --profile browser logs --no-color appliance 2>/dev/null | grep -E "ctl|error|Error|unreach" | tail -25
docker compose --profile browser rm -sf appliance >/dev/null 2>&1
[ -d ../reports/screenshots ] && docker run --rm -v "$PWD/../reports":/r alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc chown -R "$(id -u):$(id -g)" /r/screenshots >/dev/null 2>&1
exit $rc
