#!/usr/bin/env bash
# Line + branch coverage of the PRODUCT code (bridge/btbridge) from the unit suite, optionally plus the integration suite (--integration).
# Report: reports/evidence/coverage.txt (file table with missing lines, then a per-package summary). Hardware-only paths stay uncovered by design (listed in docs/TEST_COVERAGE.md).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --compose
hb_ensure_images btbridge-lab:dev btbridge-os:dev
WITH_INT=0; [ "${1:-}" = "--integration" ] && WITH_INT=1
[ $WITH_INT -eq 1 ] && scripts/lab_up.sh >/dev/null
cd docker || exit 1
RUN="docker compose run --rm -T -e COVERAGE_FILE=/work/.lab/coverage/.coverage runner"
mkdir -p ../.lab/coverage && rm -f ../.lab/coverage/.coverage*
$RUN python3 -m coverage run -p --branch --source=/work/bridge/btbridge -m pytest -q -p no:cacheprovider tests/unit >/dev/null
[ $WITH_INT -eq 1 ] && $RUN python3 -m coverage run -p --branch --source=/work/bridge/btbridge -m pytest -q -p no:cacheprovider tests/integration >/dev/null
# tests/os + tests/flashtool run in btbridge-os (openssl, rauc, genimage, mtools); same /work paths, so the data files combine
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/..":/work -e COVERAGE_FILE=/work/.lab/coverage/.coverage \
  -e PYTHONPATH=/work/bridge:/work/flashtool:/work/testkit:/work/tests/unit:/work/tests/os btbridge-os:dev \
  python3 -m coverage run -p --branch --source=/work/bridge/btbridge -m pytest -q -p no:cacheprovider tests/os tests/flashtool >/dev/null
# The flash tool (flashtool/hififlash, incl. its GUI) is measured by scripts/run_flashtool.sh (reports/evidence/flashtool_coverage.txt): its GUI
# needs PySide6, which these images do not have, so counting it here would only add a misleading 0 %.
$RUN sh -c "cd /work/.lab/coverage && python3 -m coverage combine -q && python3 -m coverage report --skip-empty -m && python3 -m coverage json -q -o cov.json \
  && python3 /work/scripts/lib/cov_summary.py cov.json" > ../reports/evidence/coverage.txt
tail -16 ../reports/evidence/coverage.txt
