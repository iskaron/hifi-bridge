#!/usr/bin/env bash
# INTEGRATION tests: real LMS + Squeezelite, simulated Bluetooth. Takes ~25-30 min in total.
# usage: scripts/run_integration.sh [pytest args]   e.g. -k ownership   |   -m "not slow"
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/lab_up.sh >/dev/null
cd docker
docker compose run --rm -T runner python3 -m pytest tests/integration -v "$@"
