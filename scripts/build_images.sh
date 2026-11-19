#!/usr/bin/env bash
# Rebuild ALL project images from scratch (--pull --no-cache) and record sizes and base-image digests.
# Only project images (btbridge-*, hifi-demo) are built; nothing else is touched. Output: reports/evidence/images.txt
# The image list, Dockerfiles and build arguments come from the registry in scripts/lib/common.sh (single source for all entry scripts).
# Usage: build_images.sh [--cached] [-q]   (--cached keeps the Docker layer cache for fast local rebuilds)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_parse_quiet "$@"
HB_BUILD_FLAGS="--pull --no-cache"; [ "${HB_ARGS[0]:-}" = "--cached" ] && HB_BUILD_FLAGS=""
export HB_BUILD_FLAGS
hb_require --disk 12000 "all project images (the browser image alone is 2.6 GB)"
read -r -a IMGS <<<"$HB_ALL_IMAGES"
hb_steps $(( ${#IMGS[@]} + 1 ))
rc=0
for i in "${IMGS[@]}"; do
  hb_step "build $i"
  hb_build_image "$i" || rc=1
done
hb_step "record sizes and base-image digests"
mkdir -p reports/evidence
{
  echo "# project images $(date -u +%FT%TZ)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep -E '^(btbridge-|hifi-demo)' | sort
  echo "# pinned third-party images (docker/image-pins.txt)"
  scripts/image_pins.sh list
} > reports/evidence/images.txt
cat reports/evidence/images.txt
exit $rc
