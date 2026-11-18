#!/usr/bin/env bash
# Docker version matrix (docs/DOCKER_REQUIREMENTS.md): runs the demo's network topology and every Compose feature the entry scripts use
# against several Docker Engine versions, each in a DISPOSABLE Docker-in-Docker container, with several Compose release binaries.
#
#   scripts/test_docker_versions.sh [--engines "24.0.9 29.1.5"] [--compose "2.20.3 2.36.2"] [-q]
#
# Isolation of this test system: every sandbox is a privileged `docker:<version>-dind` container named hifi-entry-dind-<version> (privileged is
# what Docker-in-Docker needs; nothing of the host is mounted - no Docker socket, no project files except a read-only copy of tests/entry/
# docker_matrix via `docker cp`), and it is removed together with its volumes at the end (also on Ctrl-C). The inner daemon pulls the pinned
# alpine image from the registry. Compose binaries are downloaded from the project's releases and verified with the sha256 in
# tests/entry/docker_matrix/compose-releases.txt. Output: PASS/FAIL per engine x compose; exit 1 if anything failed.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_parse_quiet "$@"
ENGINES="24.0.9 25.0.5 26.1.4 27.5.1 28.0.4 29.1.5"; COMPOSES="2.20.3 2.36.2"
set -- "${HB_ARGS[@]+"${HB_ARGS[@]}"}"
while [ $# -gt 0 ]; do case "$1" in --engines) ENGINES=$2; shift ;; --compose) COMPOSES=$2; shift ;; esac; shift; done
hb_require --disk 6000 "the Docker-in-Docker sandboxes"
M=tests/entry/docker_matrix
CACHE=${HB_MATRIX_CACHE:-"${TMPDIR:-/tmp}/hifi-entry-compose-cache-$(id -u)"}
mkdir -p "$CACHE"

fetch_compose() {                                  # $1 = version -> $CACHE/dc-$1 (sha256-verified)
  local v=$1 want
  want=$(awk -v v="$v" '$1 == v { print $2 }' "$M/compose-releases.txt")
  [ -n "$want" ] || { hb_err "compose $v is not listed in $M/compose-releases.txt"; return 1; }
  if [ ! -f "$CACHE/dc-$v" ] || [ "$(sha256sum "$CACHE/dc-$v" | cut -d' ' -f1)" != "$want" ]; then
    hb_info "downloading docker compose $v"
    curl -fsSL -o "$CACHE/dc-$v" "https://github.com/docker/compose/releases/download/v$v/docker-compose-linux-x86_64" || return 1
    chmod +x "$CACHE/dc-$v"
  fi
  [ "$(sha256sum "$CACHE/dc-$v" | cut -d' ' -f1)" = "$want" ] || { hb_err "sha256 of compose $v does not match $M/compose-releases.txt"; return 1; }
}

NAME=""
cleanup() { [ -n "$NAME" ] && docker rm -f -v "$NAME" >/dev/null 2>&1; }
trap cleanup EXIT INT TERM
total=0; failed=0
for v in $COMPOSES; do fetch_compose "$v" || exit 2; done
for e in $ENGINES; do
  ref=$(scripts/image_pins.sh list | awk -v r="docker:$e-dind" '$1 == r { print $1 "@" $2 }')
  [ -n "$ref" ] || { hb_err "docker:$e-dind is not pinned in docker/image-pins.txt"; exit 2; }
  hb_ensure_pulled "$ref" "disposable Docker-in-Docker sandbox for Engine $e" || exit 2
  NAME="hifi-entry-dind-${e//./}"
  docker rm -f -v "$NAME" >/dev/null 2>&1
  docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= "$ref" >/dev/null || { hb_err "cannot start the sandbox $NAME"; exit 2; }
  for _ in $(seq 60); do docker exec "$NAME" docker info >/dev/null 2>&1 && break; sleep 1; done
  docker cp "$M" "$NAME:/matrix" >/dev/null
  for v in $COMPOSES; do
    docker cp "$CACHE/dc-$v" "$NAME:/usr/local/bin/dc" >/dev/null
    for probe in topology features; do
      total=$((total + 1))
      echo "=== Engine $e + Compose $v: $probe"
      if ! docker exec "$NAME" sh "/matrix/${probe}_probe.sh" /usr/local/bin/dc; then failed=$((failed + 1)); fi
    done
  done
  cleanup; NAME=""
done
echo "docker version matrix: $((total - failed))/$total probes passed"
[ "$failed" -eq 0 ]
