#!/usr/bin/env bash
# UNIT tests (fake clock / fake LMS / scripted BlueZ events) inside the lab image. No LMS needed.
# Usage: run_unit.sh [pytest args...]   (default target: tests/unit; any path argument replaces it)
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"
hb_require --compose
hb_ensure_image btbridge-lab:dev
cd "$(dirname "$0")/../docker"
targets=(tests/unit)
for a in "$@"; do case "$a" in tests/*) targets=();; esac; done
docker compose run --rm -T runner python3 -m pytest "${targets[@]}" "$@"
