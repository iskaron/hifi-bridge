#!/usr/bin/env bash
# Build the hardware validation bundle (in Docker): dist/hwkit-bundle-<rev>.tar.gz with bridge code + kit, and dist/hwkit-tracks/ with
# the phone test tracks (WAV + AAC .m4a for phones). Nothing is installed or run on the host; copy the bundle to the Pi yourself.
set -euo pipefail
cd "$(dirname "$0")/.."
REV=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
mkdir -p dist
docker build -q -f docker/Dockerfile.hwkit -t btbridge-hwkit:dev docker >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/work -w /work -e REV="$REV" btbridge-hwkit:dev bash -c '
  set -e
  python3 -m hwkit tracks -o dist/hwkit-tracks >/dev/null
  for f in dist/hwkit-tracks/*.wav; do ffmpeg -loglevel error -y -i "$f" -c:a aac -b:a 256k "${f%.wav}.m4a"; done
  echo "$REV" > dist/HWKIT_REVISION
  tar --exclude="__pycache__" -czf "dist/hwkit-bundle-$REV.tar.gz" bridge/btbridge hwkit flashtool/hififlash docs/HARDWARE_VALIDATION_PLAN.md \
      docs/HARDWARE_TEST_PROCEDURE.md docs/HARDWARE_GATE_CHECKLIST.md docs/PROVISIONING.md docs/TRUST_BOOTSTRAP.md dist/HWKIT_REVISION
  ls -la dist dist/hwkit-tracks'
