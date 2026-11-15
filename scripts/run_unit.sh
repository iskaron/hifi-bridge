#!/usr/bin/env bash
# UNIT tests (fake clock / fake LMS) inside the lab image. No LMS needed.
set -euo pipefail
cd "$(dirname "$0")/../docker"
docker compose run --rm -T runner python3 -m pytest tests/unit -v "$@"
