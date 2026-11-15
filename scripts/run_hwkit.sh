#!/usr/bin/env bash
# Hardware-validation-kit self-tests (Docker): signal/codec robustness with real FFmpeg SBC/AAC, trace recording against the fake BlueZ,
# results rules, CLI. Needs the lab image (scripts/lab_up.sh builds it) and builds btbridge-hwkit:dev on top.
set -euo pipefail
cd "$(dirname "$0")/.."
docker image inspect btbridge-lab:dev >/dev/null 2>&1 || (cd docker && docker compose build >/dev/null)
docker build -q -f docker/Dockerfile.hwkit -t btbridge-hwkit:dev docker >/dev/null
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m pytest -p no:cacheprovider tests/hwkit "$@"
