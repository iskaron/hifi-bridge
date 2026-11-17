#!/usr/bin/env bash
# Hi-Fi Bridge interactive demo (docs/DEMO.md). Host requirements: Docker with Compose, make. Nothing is installed on the host.
#   scripts/demo.sh up | stop | status | logs [service] | reset | trust | ha | ha-stop
# Touches ONLY the compose project "hifi-demo" (its containers, networks and volumes) and the image hifi-demo:dev.
set -euo pipefail
# shellcheck disable=SC2016  # single-quoted commands are expanded INSIDE the containers on purpose
cd "$(dirname "$0")/.."
DC=(docker compose -f demo/compose.yml)
HOST=${DEMO_HOST:-127.0.0.1}
P_HTTP=${DEMO_HTTP_PORT:-18080}; P_HTTPS=${DEMO_HTTPS_PORT:-18443}; P_CON=${DEMO_CONSOLE_PORT:-18100}; P_LMS=${DEMO_LMS_PORT:-19000}
P_HA=${DEMO_HA_PORT:-18123}
OUT=.demo                                       # host-side output (git-ignored): the verified device CA certificate

need() { command -v docker >/dev/null || { echo "Docker is required."; exit 2; }; docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required."; exit 2; }; }
running() { [ -n "$("${DC[@]}" ps -q --status running "$1" 2>/dev/null)" ]; }
sheet_code() { "${DC[@]}" exec -T console sh -c 'cat /owner/owner-sheet.txt 2>/dev/null' | sed -n 's/^\(Einrichtungscode\|Setup code\): //p' | head -1; }

urls() {
  cat <<EOF

  Hi-Fi Bridge demo  (SIMULATED: phones, Bluetooth radio, rooms/speakers - REAL: bridge software, Lyrion, Squeezelite)

    Bridge WebUI (HTTP)     http://$HOST:$P_HTTP
    Bridge WebUI (HTTPS)    https://$HOST:$P_HTTPS        (after "make demo-trust" and importing the CA, see docs/DEMO.md)
    Demo console            http://$HOST:$P_CON          (overview)
      Virtual phones        http://$HOST:$P_CON/phones
      Audio monitor         http://$HOST:$P_CON/monitor
    Lyrion Media Server     http://$HOST:$P_LMS
EOF
  if running homeassistant; then echo "    Home Assistant          http://$HOST:$P_HA"; fi
}

fingerprint_oob() { "${DC[@]}" exec -T hifi-bridge sh -c 'sed -n "s/.*Device CA fingerprint (SHA-256): //p" /boot-demo/hifi-status.txt' 2>/dev/null | tail -1 | tr -d '\r'; }

secrets_info() {
  local code where
  code=$(sheet_code || true)
  where=$(curl -s -m 5 -o /dev/null -w '%{redirect_url}' "http://$HOST:$P_HTTP/" 2>/dev/null || true)
  echo
  echo "  Owner data (local demo only - do not paste into public issues):"
  if printf '%s' "$where" | grep -q '/setup'; then
    echo "    Setup code              ${code:-not available yet}   (one-time; enter it in the setup wizard)"
  else
    echo "    Setup code              (used - the owner password is set; forgot it? docs/DEMO.md 'Recovery')"
  fi
  echo "    Device CA (SHA-256)     $(fingerprint_oob)"
  "${DC[@]}" exec -T console sh -c '. /mqtt-creds/credentials.env 2>/dev/null && printf "    MQTT (bridge settings)  host mosquitto, port 1883, user %s, password %s\n" "$HIFI_BRIDGE_USER" "$HIFI_BRIDGE_PASSWORD"' 2>/dev/null || true
  if running homeassistant; then
    "${DC[@]}" exec -T console sh -c 'python3 -c "import json;d=json.load(open(\"/data/home-assistant-owner.json\"));print(\"    Home Assistant login    user\",d[\"username\"],\"password\",d[\"password\"])"' 2>/dev/null || echo "    Home Assistant login    run: make demo-ha"
  fi
}

case "${1:-}" in
  up)
    need
    mkdir -p "$OUT"
    if ! running gateway; then                  # our own running demo holds these ports legitimately
      busy=""
      for p in "$P_HTTP" "$P_HTTPS" "$P_CON" "$P_LMS" "$P_HA"; do
        (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && busy="$busy $p"
      done
      if [ -n "$busy" ]; then
        echo "Port(s) already in use on this computer:$busy"
        echo "Choose others, e.g.: DEMO_HTTP_PORT=28080 DEMO_HTTPS_PORT=28443 DEMO_CONSOLE_PORT=28100 DEMO_LMS_PORT=29000 DEMO_HA_PORT=28123 make demo"
        exit 3
      fi
    fi
    "${DC[@]}" build -q
    "${DC[@]}" up -d --remove-orphans
    echo -n "Waiting for the bridge "
    for _ in $(seq 120); do
      [ "$("${DC[@]}" ps --format '{{.Health}}' hifi-bridge 2>/dev/null)" = "healthy" ] && break; echo -n "."; sleep 1
    done; echo
    urls; secrets_info
    echo; echo "  Next: open the bridge, enter the setup code, choose Lyrion server 'lms' and a room. Guide: docs/DEMO.md"
    ;;
  stop) need; "${DC[@]}" --profile ha stop ;;
  status)
    need
    "${DC[@]}" --profile ha ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
    urls; secrets_info
    echo; echo "  Resources:"
    mapfile -t ids < <("${DC[@]}" --profile ha ps -q)
    [ "${#ids[@]}" -gt 0 ] && docker stats --no-stream --format '    {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "${ids[@]}"
    docker image ls hifi-demo:dev --format '    image hifi-demo:dev {{.Size}}'
    ;;
  logs) need; shift; "${DC[@]}" --profile ha logs -f --tail 100 "$@" ;;
  reset)
    need
    echo "This deletes ONLY the demo's own data: compose project 'hifi-demo' (containers, networks and volumes: bridge settings,"
    echo "pairings, Lyrion state, uploads, Home Assistant). Nothing else on this computer is touched."
    if [ "${DEMO_RESET_CONFIRM:-}" != "yes" ]; then
      read -r -p "Type RESET to continue: " a; [ "$a" = "RESET" ] || { echo "Aborted."; exit 1; }
    fi
    "${DC[@]}" --profile ha down -v --remove-orphans
    rm -f "$OUT/hifi-bridge-ca.pem"
    echo "Demo data removed. Start again with: make demo"
    ;;
  trust)
    need
    mkdir -p "$OUT"
    echo "P7 trust proof with the owner's one-time trust key (the flash tool's method, docs/TRUST_BOOTSTRAP.md):"
    if out=$("${DC[@]}" exec -T console python3 -m hififlash verify-device --url "http://hifi-bridge.local:$P_HTTP" --key-file /owner/trust.key \
          --https "https://hifi-bridge.local:$P_HTTPS" --ca-out /data/hifi-bridge-ca.pem 2>&1); then
      echo "$out"
    elif printf '%s' "$out" | grep -q "404"; then
      echo "  The one-time trust key is gone: the bridge deletes it when the owner password is set (by design)."
      echo "  Manual method instead: download the CA and compare its fingerprint with the one on the boot partition (hifi-status.txt)."
      "${DC[@]}" exec -T console curl -fsS -o /data/hifi-bridge-ca.pem "http://hifi-bridge.local:$P_HTTP/ca.crt" || exit 1
      got=$("${DC[@]}" exec -T console openssl x509 -in /data/hifi-bridge-ca.pem -noout -fingerprint -sha256 | sed 's/.*=//' | tr -d '\r')
      want=$(fingerprint_oob)
      echo "  downloaded CA:            $got"
      echo "  boot partition (device):  $want"
      if [ -z "$want" ] || [ "$got" != "$want" ]; then echo "  FINGERPRINTS DIFFER - do not trust this certificate."; exit 1; fi
      echo "  MATCH"
    else
      echo "$out"; exit 1
    fi
    "${DC[@]}" cp console:/data/hifi-bridge-ca.pem "$OUT/hifi-bridge-ca.pem" >/dev/null
    echo "Verified device CA saved to $OUT/hifi-bridge-ca.pem - import it into your browser (docs/DEMO.md, 'Trusted HTTPS')."
    ;;
  ha)
    need
    "${DC[@]}" --profile ha up -d homeassistant
    "${DC[@]}" exec -T console python3 -m hifidemo.hasetup
    secrets_info
    echo "  Home Assistant: http://$HOST:$P_HA"
    ;;
  ha-stop) need; "${DC[@]}" --profile ha stop homeassistant ;;
  *) echo "usage: $0 up|stop|status|logs [service]|reset|trust|ha|ha-stop"; exit 2 ;;
esac
