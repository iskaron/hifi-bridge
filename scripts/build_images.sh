#!/usr/bin/env bash
# Rebuild ALL project images from scratch (--pull --no-cache) and record sizes and base-image digests.
# Only project images (btbridge-*) are built; nothing else is touched. Output: reports/evidence/images.txt
# Usage: build_images.sh [--cached]   (--cached keeps the Docker layer cache for fast local rebuilds)
set -uo pipefail
cd "$(dirname "$0")/../docker" || exit 1
NC="--no-cache"; [ "${1:-}" = "--cached" ] && NC=""
rc=0
b() { echo "### build $1"; shift; docker build -q --pull $NC "$@" >/dev/null || rc=1; }
b lab -f Dockerfile.lab -t btbridge-lab:dev .
b pipewire-bookworm -f Dockerfile.pipewire --build-arg BASE=debian:bookworm-slim -t btbridge-pw:bookworm .
b pipewire-trixie -f Dockerfile.pipewire --build-arg BASE=debian:trixie-slim -t btbridge-pw:trixie .
echo "### build hwkit (FROM btbridge-lab:dev)"; docker build -q $NC -f Dockerfile.hwkit -t btbridge-hwkit:dev . >/dev/null || rc=1
b avahi -f Dockerfile.avahi -t btbridge-avahi:dev .
b browser -f Dockerfile.browser -t btbridge-browser:dev .
b static -f Dockerfile.static -t btbridge-static:dev .
{
  echo "# project images $(date -u +%FT%TZ)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep '^btbridge-' | sort
  echo "# base images (digests)"
  for i in debian:bookworm-slim debian:trixie-slim mcr.microsoft.com/playwright/python:v1.49.1-noble lmscommunity/lyrionmusicserver:stable \
           eclipse-mosquitto:2.0.20 ghcr.io/home-assistant/home-assistant:2026.11.1; do
    echo "$i $(docker image inspect "$i" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
  done
} > ../reports/evidence/images.txt
cat ../reports/evidence/images.txt
exit $rc
