#!/usr/bin/env bash
# Infrastructure-outage scenario (real LMS / real Squeezelite containers, simulated phone).
# usage: scripts/test_outage.sh lms|player1
# Host needs only Docker + Compose; the scenario itself runs in the runner container.
set -euo pipefail
cd "$(dirname "$0")/../docker"
KIND="${1:?usage: $0 lms|player1}"
docker compose up -d lms player1 player2 player3 legacy1 >/dev/null
docker rm -f outage-scn >/dev/null 2>&1 || true
docker compose run -d --name outage-scn -T runner python3 -m btkit.scenario_outage --kind "$KIND" --duration 100 >/dev/null
sleep 22; echo "[host] stopping $KIND"; docker compose stop "$KIND" >/dev/null
sleep 15; echo "[host] starting $KIND"; docker compose start "$KIND" >/dev/null
RC=$(docker wait outage-scn); docker logs outage-scn 2>&1 | tail -40; docker rm outage-scn >/dev/null
echo "scenario exit code: $RC"; exit "$RC"
