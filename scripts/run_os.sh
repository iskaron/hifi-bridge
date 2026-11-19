#!/usr/bin/env bash
# Phase-2b preparation checks in ONE image (btbridge-os): Buildroot defconfig resolution + staleness, licence policy, and the pytest suites
# tests/os (post-build trust/keyring with real RAUC, genimage card layout, U-Boot script, growdata) and tests/flashtool (flash tool core
# with SIMULATED cards only). No board image is built, nothing is booted, no host disk is touched.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev
echo "### buildroot defconfigs"; scripts/os_defconfigs.sh check; D=$?
echo "### licences"; scripts/os_licenses.sh; L=$?
echo "### tests/os + tests/flashtool"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work \
  -e PYTHONPATH=/work/bridge:/work/flashtool:/work/testkit:/work/tests/unit:/work/tests/os \
  btbridge-os:dev python3 -m pytest -p no:cacheprovider -q tests/os tests/flashtool "$@"; T=$?
echo "os exit codes: defconfigs=$D licences=$L tests=$T"
[ $D -eq 0 ] && [ $L -eq 0 ] && [ $T -eq 0 ]
