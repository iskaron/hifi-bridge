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
b pipewire-bookworm -f Dockerfile.pipewire --build-arg BASE=debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc -t btbridge-pw:bookworm .
b pipewire-trixie -f Dockerfile.pipewire --build-arg BASE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 -t btbridge-pw:trixie .
echo "### build hwkit (FROM btbridge-lab:dev)"; docker build -q $NC -f Dockerfile.hwkit -t btbridge-hwkit:dev . >/dev/null || rc=1
b avahi -f Dockerfile.avahi -t btbridge-avahi:dev .
b browser -f Dockerfile.browser -t btbridge-browser:dev .
b static -f Dockerfile.static -t btbridge-static:dev .
b syslog -f Dockerfile.syslog -t btbridge-syslog:dev .
b py314 -f Dockerfile.py314 -t btbridge-py314:dev .
b security -f Dockerfile.security -t btbridge-security:dev .
b os -f Dockerfile.os -t btbridge-os:dev .
{
  echo "# project images $(date -u +%FT%TZ)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep '^btbridge-' | sort
  echo "# base images (digests)"
  for i in debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 mcr.microsoft.com/playwright/python:v1.49.1-noble@sha256:42e251ec3238672c931be6c3565ff279690ae072c3e22058128d0412494f6866 lmscommunity/lyrionmusicserver:9.1.2@sha256:a8709abda8a80766274abeb44015189b7e050eb1a6372678dcb077eb8c3a1e21 \
           eclipse-mosquitto:2.0.20@sha256:21421af7b32bf9ce508e9090c8eb13bb81f410ca778dc205506180a6f862d0eb ghcr.io/home-assistant/home-assistant:2026.11.1@sha256:6785d1eca68747a524c156faae1b139ed415c07357386ae6fe17291986e59e82; do
    echo "$i $(docker image inspect "$i" --format '{{index .RepoDigests 0}}' 2>/dev/null)"
  done
} > ../reports/evidence/images.txt
cat ../reports/evidence/images.txt
exit $rc
