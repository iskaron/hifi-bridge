#!/usr/bin/env bash
# Unit tests of the interactive demo (tests/demo, without the full stack) in the demo image. Uses the CURRENT source tree (mounted
# read-only), not the copy inside the image. The stack test (real LMS, Squeezelite, PipeWire, browser) is scripts/test_demo.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_image hifi-demo:dev
docker run --rm --network none -v "$PWD":/work:ro -w /work -e HOME=/tmp \
  -e PYTHONPATH=/work/bridge:/work/testkit:/work/flashtool:/work/demo hifi-demo:dev \
  python3 -m pytest -p no:cacheprovider tests/demo --ignore=tests/demo/stack "$@"
