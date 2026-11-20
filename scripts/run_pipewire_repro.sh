#!/usr/bin/env bash
# Reproduce the two PipeWire harness flakes of docs/PIPEWIRE_CAPTURE.md ("Harness flakes") in DISPOSABLE containers (no hardware, no host access):
#   startup   pipewire-pulse start-up race with the harness's readiness poll (testkit/pw/repro_startup_race.sh)
#   unload    pipewire-pulse 1.4.2 SIGSEGV when a module-pipe-source is unloaded under streams (testkit/pw/repro_unload_crash.sh)
# usage: scripts/run_pipewire_repro.sh startup [trixie|bookworm] [N] [poll|pre]
#        scripts/run_pipewire_repro.sh unload  [trixie|bookworm] [N] [alt|never|always|race|sup] [unload|destroy]
# Each run starts its own container; the exit status is 0 when pipewire-pulse never failed. Expect failures for `startup ... poll` (about 3-6 %
# of start-ups) and for `unload ... alt|sup unload` on trixie (1.4.2), none for `startup ... pre` and `unload ... sup destroy`.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"
hb_require
what=${1:-}; base=${2:-trixie}; shift 2 2>/dev/null || true
case "$what" in
  startup) script=repro_startup_race.sh; args=("${1:-100}" "${2:-poll}") ;;
  unload)  script=repro_unload_crash.sh; args=("${1:-100}" "${2:-alt}" "${3:-unload}") ;;
  *) echo "usage: $0 startup|unload [trixie|bookworm] [N] [mode...]" >&2; exit 2 ;;
esac
hb_ensure_image "btbridge-pw:${base}" --always || exit 2
cd "$(dirname "$0")/.." || exit 1
docker run --rm --network none -v "$PWD/testkit/pw":/s:ro "btbridge-pw:${base}" bash "/s/${script}" "${args[@]}"
