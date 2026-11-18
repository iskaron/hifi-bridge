#!/usr/bin/env bash
# Maintenance of the pinned third-party container images (docker/image-pins.txt, docs/IMAGE_PINS.md, DECISIONS #73).
#   scripts/image_pins.sh list      the pins
#   scripts/image_pins.sh check     offline: every reference in the scanned files carries the digest of the lock file
#   scripts/image_pins.sh apply     rewrite every reference <name:tag>[@digest] to <name:tag>@<digest of the lock file>
#   scripts/image_pins.sh resolve   ask the registries (needs network + docker buildx) which digest each tag points to TODAY and
#                                   report differences to the lock file; it changes nothing
# Updating a pin: run "resolve", put the new tag/digest into docker/image-pins.txt, run "apply", then the tests (scripts/run_entry_tests.sh
# and the suites that use the image) - a changed Lyrion image in particular needs the integration and demo suites.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
LOCK=docker/image-pins.txt
FILES=$(ls docker/compose.yml demo/compose.yml docker/Dockerfile.* demo/Dockerfile scripts/*.sh scripts/lib/*.sh Makefile tests/entry/docker_matrix/* 2>/dev/null)

pins() { grep -vE '^\s*(#|$)' "$LOCK" | awk '{ print $1, $2 }'; }
esc() { printf '%s' "$1" | sed 's/[][\.*^$/#]/\\&/g'; }

case "${1:-}" in
  list) pins ;;
  apply)
    while read -r ref digest; do
      e=$(esc "$ref")
      for f in $FILES; do
        sed -E -i "s#(^|[^A-Za-z0-9./_@-])${e}(@sha256:[0-9a-f]{64})?([^A-Za-z0-9._-]|\$)#\1${ref}@${digest}\3#g" "$f"
      done
    done < <(pins)
    echo "applied $(pins | wc -l) pins"
    ;;
  check)
    rc=0
    while read -r ref digest; do
      e=$(esc "$ref")
      for f in $FILES; do
        # a reference without the right digest
        if grep -nE "(^|[^A-Za-z0-9./_@-])${e}(@sha256:[0-9a-f]{64})?([^A-Za-z0-9._-]|\$)" "$f" | grep -vF "${ref}@${digest}" >/dev/null; then
          echo "wrong or missing digest for $ref in $f"; rc=1
        fi
      done
    done < <(pins)
    [ $rc -eq 0 ] && echo "all references match $LOCK"
    exit $rc
    ;;
  resolve)
    docker buildx version >/dev/null 2>&1 || { echo "docker buildx is needed for 'resolve' (or compare with the registry's web page)"; exit 2; }
    rc=0
    while read -r ref digest; do
      now=$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}' 2>/dev/null | head -n 1)
      if [ -z "$now" ]; then echo "?        $ref (not resolvable)"; rc=1
      elif [ "$now" = "$digest" ]; then echo "same     $ref"
      else echo "CHANGED  $ref: lock $digest, registry now $now"; fi
    done < <(pins)
    exit $rc
    ;;
  *) echo "usage: $0 list|check|apply|resolve"; exit 2 ;;
esac
