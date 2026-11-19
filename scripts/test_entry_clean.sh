#!/usr/bin/env bash
# Entry points from a CLEAN environment: a fresh `git clone` of the current branch inside a disposable Docker-in-Docker sandbox that has no
# project images, then `make demo` (start, status, stop) and scripts/hwkit_bundle.sh end to end, with a decoy "Lyrion on the host".
#
#   scripts/test_entry_clean.sh [--engine 25.0.5] [--compose 2.24.5] [--log FILE] [--keep]
#
# Sandbox: privileged container hifi-entry-clean-<engine> (what Docker-in-Docker needs), nothing of the host mounted (no Docker socket); the
# clone is copied in with `docker cp`; `apk add` of make/bash/... happens INSIDE the sandbox only. Removed afterwards (also on Ctrl-C) unless
# --keep. Only committed files are tested (the clone is made from the branch). Needs network (the sandbox builds and pulls everything).
# Transcript: reports/evidence/entry_clean_<engine>_compose<version>.log (setup code and passwords redacted).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
ENGINE=25.0.5; COMPOSE=2.24.5; LOG=""; KEEP=0
while [ $# -gt 0 ]; do case "$1" in --engine) ENGINE=$2; shift ;; --compose) COMPOSE=$2; shift ;; --log) LOG=$2; shift ;; --keep) KEEP=1 ;; esac; shift; done
LOG=${LOG:-reports/evidence/entry_clean_${ENGINE}_compose${COMPOSE}.log}
hb_require --tools git --disk 8000 "the sandbox (it builds the demo, lab and hardware-kit images)"
M=tests/entry/docker_matrix
want=$(awk -v v="$COMPOSE" '$1 == v { print $2 }' "$M/compose-releases.txt"); [ -n "$want" ] || { hb_err "compose $COMPOSE is not in $M/compose-releases.txt"; exit 2; }
CACHE=${HB_MATRIX_CACHE:-"${TMPDIR:-/tmp}/hifi-entry-compose-cache-$(id -u)"}; mkdir -p "$CACHE"
if [ ! -f "$CACHE/dc-$COMPOSE" ] || [ "$(sha256sum "$CACHE/dc-$COMPOSE" | cut -d' ' -f1)" != "$want" ]; then
  curl -fsSL -o "$CACHE/dc-$COMPOSE" "https://github.com/docker/compose/releases/download/v$COMPOSE/docker-compose-linux-x86_64" && chmod +x "$CACHE/dc-$COMPOSE"
fi
[ "$(sha256sum "$CACHE/dc-$COMPOSE" | cut -d' ' -f1)" = "$want" ] || { hb_err "sha256 mismatch for compose $COMPOSE"; exit 2; }
ref=$(scripts/image_pins.sh list | awk -v r="docker:$ENGINE-dind" '$1 == r { print $1 "@" $2 }')
[ -n "$ref" ] || { hb_err "docker:$ENGINE-dind is not pinned in docker/image-pins.txt"; exit 2; }
hb_ensure_pulled "$ref" "disposable Docker-in-Docker sandbox" || exit 2

NAME="hifi-entry-clean-${ENGINE//./}"
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/hifi-entry-clone.XXXXXX")
cleanup() { [ "$KEEP" = 1 ] || { docker rm -f -v "$NAME" >/dev/null 2>&1; rm -rf "$SCRATCH"; }; }
trap cleanup EXIT INT TERM
docker rm -f -v "$NAME" >/dev/null 2>&1
hb_info "fresh clone of $(git rev-parse --abbrev-ref HEAD) ($(git rev-parse --short HEAD))"
git clone -q --no-local --branch "$(git rev-parse --abbrev-ref HEAD)" "$PWD" "$SCRATCH/repo" || exit 2
docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= "$ref" >/dev/null || exit 2
for _ in $(seq 60); do docker exec "$NAME" docker info >/dev/null 2>&1 && break; sleep 1; done
docker exec "$NAME" mkdir -p /clone /usr/local/libexec/docker/cli-plugins
docker cp "$SCRATCH/repo" "$NAME:/clone/repo" >/dev/null
docker cp "$CACHE/dc-$COMPOSE" "$NAME:/usr/local/libexec/docker/cli-plugins/docker-compose" >/dev/null
docker cp tests/entry/clean/run_in_dind.sh "$NAME:/run_in_dind.sh" >/dev/null
docker exec "$NAME" apk add --no-cache bash make git curl coreutils gawk grep sed findutils iproute2 >/dev/null 2>&1 \
  || { hb_err "apk add failed inside the sandbox (network?)"; exit 2; }
mkdir -p "$(dirname "$LOG")"
{
  echo "# clean-environment test $(date -u +%FT%TZ): Engine $ENGINE (sandbox $ref), Compose $COMPOSE, clone of $(git rev-parse --short HEAD)"
  docker exec "$NAME" bash /run_in_dind.sh 2>&1
} | sed -E 's/(Setup code +)[A-Z0-9]{4}[A-Z0-9-]+/\1<redacted>/; s/(password )[A-Za-z0-9_+\/=-]{6,}/\1<redacted>/g; s/(Einrichtungscode +)[A-Z0-9-]+/\1<redacted>/' | tee "$LOG"
rc=${PIPESTATUS[0]}
grep -q 'CLEAN-ENVIRONMENT RESULT: PASS' "$LOG" || rc=1
echo "transcript: $LOG (exit $rc)"
exit "$rc"
