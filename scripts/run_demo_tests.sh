#!/usr/bin/env bash
# Unit tests of the interactive demo (tests/demo, without the full stack) in the demo image. Uses the CURRENT source tree (mounted
# read-only), not the copy inside the image. The stack test (real LMS, Squeezelite, PipeWire, browser) is scripts/test_demo.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
docker image inspect hifi-demo:dev >/dev/null 2>&1 || docker build -q -f demo/Dockerfile -t hifi-demo:dev . >/dev/null
docker run --rm --network none -v "$PWD":/work:ro -w /work -e HOME=/tmp \
  -e PYTHONPATH=/work/bridge:/work/testkit:/work/flashtool:/work/demo hifi-demo:dev \
  python3 -m pytest -p no:cacheprovider tests/demo --ignore=tests/demo/stack "$@"
