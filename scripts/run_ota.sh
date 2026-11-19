#!/usr/bin/env bash
# Update management against the REAL RAUC 1.13 D-Bus service (docs/OTA.md section 9). One disposable container (hifi-ota-real) with no
# network: a private dbus-daemon with system-bus rules, upstream RAUC's policy plus ours, `rauc service` with the product's system.conf
# (U-Boot backend on a FILE-BACKED environment, file-backed slots, boot slot overridden to A), bundles built by scripts/os_bundle.sh with
# throw-away keys. Root INSIDE the container only, to run clients as uid 990 (hifi-bridge) and 1234 (another user) for the policy test.
# Limit: RAUC cannot mount a bundle without loop devices, so an installation stops at the mount step (asserted); writing a slot,
# booting it and the boot-count are hardware-gate items (G2 P3-3...P3-6).
# Usage: scripts/run_ota.sh [pytest args]       Output: reports/evidence/ota_realrauc.txt
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-ota:dev || exit 1
OUT=reports/evidence/ota_realrauc.txt
mkdir -p reports/evidence
cleanup() { docker rm -f hifi-ota-real >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
{
  echo "# update management with the real RAUC service - $(date -u +%Y-%m-%dT%H:%M:%SZ) - label INTEGRATION (Docker, no device, no loop device)"
  docker run --rm --network none btbridge-ota:dev sh -c 'echo "rauc: $(rauc --version)"; echo "dbus-daemon: $(dbus-daemon --version | head -1)";
    echo "fw_setenv: $(dpkg-query -W -f "\${Version}" libubootenv-tool)"; echo "jeepney: $(python3 -c "import jeepney; print(jeepney.__version__)")"'
} > "$OUT" 2>&1
docker run --rm --name hifi-ota-real --cpus 2 --memory 1g --network none -v "$PWD":/work:ro -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/work/bridge:/work/testkit:/work/tests/unit btbridge-ota:dev \
  python3 -m pytest -p no:cacheprovider -v tests/ota "$@" 2>&1 | tee -a "$OUT"
T=${PIPESTATUS[0]}
echo "ota exit code: tests=$T" | tee -a "$OUT"
exit "$T"
