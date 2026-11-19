#!/usr/bin/env bash
# Licence/package report per board from `make show-info` (Docker, no build). Output: reports/evidence/os_licenses_<board>.md
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev
LAB="${HIFI_LAB:-$PWD/.lab}"
[ -d "$LAB/buildroot/configs" ] || scripts/os_fetch_buildroot.sh || exit 2
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e BR2_DL_DIR=/tmp/dl -v "$PWD":/work -v "$LAB/buildroot":/buildroot:ro btbridge-os:dev bash -c '
rc=0
for b in rpi3_64 rpi4_64 rpizero2w_64; do
  O=/tmp/out-$b
  make -s -C /buildroot O=$O BR2_EXTERNAL=/work/os/br2-external hifi_bridge_${b}_defconfig >/dev/null 2>&1 || { echo "defconfig failed: $b"; rc=1; continue; }
  make -s -C /buildroot O=$O BR2_EXTERNAL=/work/os/br2-external show-info > /tmp/info-$b.json 2>/tmp/err-$b || { tail -5 /tmp/err-$b; rc=1; continue; }
  python3 /work/os/tools/license_report.py /tmp/info-$b.json $b /work/reports/evidence/os_licenses_$b.md || rc=1
done
exit $rc'
