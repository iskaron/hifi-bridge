#!/usr/bin/env bash
# INTEGRATION tests: real LMS + Squeezelite, simulated Bluetooth. Takes ~25-30 min in total.
# usage: scripts/run_integration.sh [pytest args]   e.g. -k ownership   |   -m "not slow"
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose
scripts/lab_up.sh -q >/dev/null
cd docker
docker compose run --rm -T runner python3 -m pytest tests/integration -v "$@"
