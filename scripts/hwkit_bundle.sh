#!/usr/bin/env bash
# Build the hardware validation bundle (in Docker): dist/hwkit-bundle-<rev>.tar.gz with bridge code + kit, and dist/hwkit-tracks/ with
# the phone test tracks (WAV + AAC .m4a for phones). Nothing is installed or run on the host; copy the bundle to the Pi yourself.
# Missing prerequisites and images (btbridge-lab:dev, then btbridge-hwkit:dev) are detected and built first, with visible progress.
# Usage: scripts/hwkit_bundle.sh [-q]      (-q / HB_QUIET=1: step lines only)
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_parse_quiet "$@"
hb_steps 3
hb_step "checking prerequisites (Docker Engine >= $HB_MIN_ENGINE, disk)"
hb_require --disk 2500 "the hardware-kit images (lab + FFmpeg)"
hb_step "images: btbridge-hwkit:dev (FROM btbridge-lab:dev); the first run builds both, ~3-5 min"
hb_ensure_image btbridge-hwkit:dev
hb_step "building the bundle and the phone test tracks (WAV, AAC) in the container"
REV=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
mkdir -p dist
HB_FILTER='.' hb_run "hardware-kit bundle" docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/work -w /work -e REV="$REV" btbridge-hwkit:dev bash -c '
  set -e
  python3 -m hwkit tracks -o dist/hwkit-tracks >/dev/null
  for f in dist/hwkit-tracks/*.wav; do ffmpeg -loglevel error -y -i "$f" -c:a aac -b:a 256k "${f%.wav}.m4a"; done
  echo "$REV" > dist/HWKIT_REVISION
  tar --exclude="__pycache__" -czf "dist/hwkit-bundle-$REV.tar.gz" bridge/btbridge hwkit flashtool/hififlash docs/HARDWARE_VALIDATION_PLAN.md \
      docs/HARDWARE_TEST_PROCEDURE.md docs/HARDWARE_GATE_CHECKLIST.md docs/PILOT.md docs/PROVISIONING.md docs/TRUST_BOOTSTRAP.md dist/HWKIT_REVISION
  ls -la dist dist/hwkit-tracks'
echo "bundle: dist/hwkit-bundle-$REV.tar.gz (copy it to the Raspberry Pi yourself; see docs/HARDWARE_TEST_PROCEDURE.md)"
