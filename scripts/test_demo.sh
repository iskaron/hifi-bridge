#!/usr/bin/env bash
# Full demo stack test on a SEPARATE compose project "hifi-demo-test" (own ports 28xxx, own subnets, fresh volumes), so a running
# `make demo` of the owner is never touched. Parts: 1 setup in the bridge WebUI (Chromium), 2 audio chain (demo image), 3 console UI +
# audio monitor + owner actions (Chromium), 4 P7 CA trust proof with the flash tool, 5 MQTT discovery on the demo broker.
# Measurements -> measurements/demo_*.json, screenshots -> reports/screenshots/demo-*.png. KEEP=1 leaves the test stack running.
set -uo pipefail
# shellcheck disable=SC2016,SC2317  # container-side expansion on purpose; cleanup() runs via trap
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose --disk 6000 "the demo and browser images"
hb_ensure_image hifi-demo:dev --always && hb_ensure_image btbridge-browser:dev || exit 2
hb_pull_compose_images demo/compose.yml || exit 2
export DEMO_HTTP_PORT=28080 DEMO_HTTPS_PORT=28443 DEMO_CONSOLE_PORT=28100 DEMO_LMS_PORT=29000 DEMO_HA_PORT=28123 \
       DEMO_SUBNET_DEMO=172.29.253.0/24 DEMO_SUBNET_EDGE=172.29.252.0/24
P=hifi-demo-test
DC=(docker compose -p "$P" -f demo/compose.yml)
STATE=.lab/demo-test
cleanup() { [ "${KEEP:-0}" = 1 ] || "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT

"${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
rm -rf "$STATE"; mkdir -p "$STATE" reports/screenshots measurements; chmod 0777 "$STATE"
"${DC[@]}" up -d >/dev/null 2>&1 || { "${DC[@]}" ps; exit 2; }
for _ in $(seq 120); do [ "$("${DC[@]}" ps --format '{{.Health}}' hifi-bridge)" = healthy ] && break; sleep 1; done

NET=${P}_demo
COMMON=(--rm --network "$NET" -v "$PWD":/work -v "${P}_owner":/owner:ro -v "${P}_mqtt-creds":/mqtt-creds:ro -w /work
        -e BASE_URL="http://hifi-bridge.local:$DEMO_HTTP_PORT" -e CONSOLE_URL=http://console:8100 -e DEMO_TEST_STATE="/work/$STATE"
        -e PYTHONPATH=/work/tests/demo/stack:/work/demo:/work/bridge:/work/testkit -e PYTHONDONTWRITEBYTECODE=1)
BROWSER=(docker run "${COMMON[@]}" --ipc host btbridge-browser:dev python3 -m pytest -p no:cacheprovider -v)
CHAIN=(docker run "${COMMON[@]}" -e HOME=/tmp hifi-demo:dev python3 -m pytest -p no:cacheprovider -v)

rc=0
echo "### 0 isolation: no route from the demo to the host or the LAN (scripts/check_isolation.sh; DECISIONS #71)"
scripts/check_isolation.sh "$P" --allow-edge gateway || { echo "ABORT: the demo network is not isolated"; exit 3; }
echo "### P7 CA trust proof BEFORE setup (flash tool; the one-time key is deleted when the owner password is set)"
out=$("${DC[@]}" exec -T console python3 -m hififlash verify-device --url "http://hifi-bridge.local:$DEMO_HTTP_PORT" \
      --key-file /owner/trust.key --https "https://hifi-bridge.local:$DEMO_HTTPS_PORT" 2>&1); echo "$out"
echo "$out" | grep -q "^VERIFIED" && echo "$out" | grep -q "HTTP 200" || rc=1
echo "### 1 setup (browser)";       "${BROWSER[@]}" tests/demo/stack/test_1_setup_ui.py || rc=1
echo "### 2 audio chain";           [ $rc = 0 ] && { "${CHAIN[@]}" tests/demo/stack/test_2_chain.py || rc=1; }
echo "### 3 console UI (browser)";  [ $rc = 0 ] && { "${BROWSER[@]}" tests/demo/stack/test_3_console_ui.py || rc=1; }
echo "### 4 trust after setup: manual fingerprint method (make demo-trust fallback)"
if [ $rc = 0 ]; then
  got=$("${DC[@]}" exec -T console sh -c "curl -fsS http://hifi-bridge.local:$DEMO_HTTP_PORT/ca.crt | openssl x509 -noout -fingerprint -sha256" | sed 's/.*=//' | tr -d '\r')
  want=$("${DC[@]}" exec -T hifi-bridge sh -c 'sed -n "s/.*Device CA fingerprint (SHA-256): //p" /boot-demo/hifi-status.txt' | tail -1 | tr -d '\r')
  echo "downloaded $got / boot partition $want"; [ -n "$want" ] && [ "$got" = "$want" ] || rc=1
fi
echo "### 5 MQTT discovery on the demo broker"
disc=$("${DC[@]}" exec -T mosquitto sh -c '. /mosquitto/creds/credentials.env; timeout 8 mosquitto_sub -h 127.0.0.1 -u "$HOMEASSISTANT_USER" -P "$HOMEASSISTANT_PASSWORD" -t "homeassistant/#" -v -W 6' 2>/dev/null | grep -c "/config ")
echo "discovery config topics: $disc"; [ "${disc:-0}" -ge 3 ] || rc=1
echo "### 6 SD-card recovery (make demo-recover)"
rec=$(DEMO_PROJECT="$P" scripts/demo.sh recover 2>&1)        # DEMO_PROJECT: never the owner's own demo
echo "$rec" | grep -E "recovery|setup code" | sed -E 's/(code: )[A-Z0-9-]+/\1<redacted>/'
loc=$("${DC[@]}" exec -T console curl -s -o /dev/null -w '%{redirect_url}' "http://hifi-bridge.local:$DEMO_HTTP_PORT/")
echo "after recovery / redirects to: $loc"; echo "$rec" | grep -q "setup code" && echo "$loc" | grep -q "/setup" || rc=1
echo "### resources"
mapfile -t ids < <("${DC[@]}" ps -q)
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "${ids[@]}" | sed 's/^/  /'
for f in demo_chain demo_monitor_latency_AudioWorklet demo_monitor_latency_ScriptProcessor demo_axe; do [ -f "$STATE/$f.json" ] && cp "$STATE/$f.json" "measurements/$f.json"; done
docker run --rm -v "$PWD/reports":/r -v "$PWD/$STATE":/s alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc chown -R "$(id -u):$(id -g)" /r/screenshots /s >/dev/null 2>&1
[ $rc = 0 ] || "${DC[@]}" logs --no-color --tail 40 hifi-bridge console 2>&1 | tail -60
echo "demo stack test: rc=$rc"
exit $rc
