#!/usr/bin/env bash
# Tests of the entry points (tests/entry): the shared pre-flight/image helper against FAKE docker outputs, the compose files' isolation rules
# and Docker-version compatibility, image pins, the demo's Lyrion start script. Static / fake only: no Docker daemon is used by the tests,
# the container has no network. Needs btbridge-entry:dev (built on first use). Usage: scripts/run_entry_tests.sh [pytest args]
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_image btbridge-entry:dev
docker run --rm --network none -v "$PWD":/work:ro -w /work -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 btbridge-entry:dev \
  python3 -m pytest -p no:cacheprovider -q tests/entry "$@"
