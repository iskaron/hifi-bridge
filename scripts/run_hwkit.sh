#!/usr/bin/env bash
# Hardware-validation-kit self-tests (Docker): signal/codec robustness with real FFmpeg SBC/AAC, trace recording against the fake BlueZ,
# results rules, CLI. Needs the lab image (scripts/lab_up.sh builds it) and builds btbridge-hwkit:dev on top.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_image btbridge-hwkit:dev --always     # builds btbridge-lab:dev first when it is missing
docker run --rm -v "$PWD":/work -w /work btbridge-hwkit:dev python3 -m pytest -p no:cacheprovider tests/hwkit "$@"
