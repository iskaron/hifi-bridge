#!/usr/bin/env bash
# Unit suite on Python 3.14 (the Buildroot image's version) - compatibility, not a replacement for the 3.11 lab runs.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-py314:dev
docker run --rm -v "$PWD":/work btbridge-py314:dev python3 -W error::DeprecationWarning -m pytest -p no:cacheprovider -q tests/unit "$@"
