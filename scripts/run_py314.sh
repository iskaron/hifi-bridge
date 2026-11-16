#!/usr/bin/env bash
# Unit suite on Python 3.14 (the Buildroot image's version) - compatibility, not a replacement for the 3.11 lab runs.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
docker build -q -f docker/Dockerfile.py314 -t btbridge-py314:dev docker >/dev/null || exit 2
docker run --rm -v "$PWD":/work btbridge-py314:dev python3 -W error::DeprecationWarning -m pytest -p no:cacheprovider -q tests/unit "$@"
