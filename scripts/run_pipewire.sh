#!/usr/bin/env bash
# PipeWire capture tests against REAL PipeWire tools (0.3.65 / bookworm and 1.4.x / trixie) in disposable containers.
# No audio hardware: the phone node is a module-pipe-source fed with a known pattern. Results: measurements/pipewire_capture_<ver>.json
set -uo pipefail
cd "$(dirname "$0")/../docker"
rc=0
for base in bookworm trixie; do
  docker build -q -f Dockerfile.pipewire --build-arg BASE=debian:${base}-slim -t btbridge-pw:${base} . >/dev/null || exit 2
  echo "### PipeWire on debian:${base}"
  timeout 300 docker run --rm -v "$PWD/..":/work btbridge-pw:${base} python3 -m pytest -p no:cacheprovider -v tests/pipewire "$@" || rc=1
done
exit $rc
