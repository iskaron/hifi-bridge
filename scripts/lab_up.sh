#!/usr/bin/env bash
# Build the lab image and start real LMS + 4 headless Squeezelite players (3 normal, 1 'legacy-capability' emulation) + Mosquitto.
# Usage: scripts/lab_up.sh [-q]      (-q / HB_QUIET=1: step lines only)
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_parse_quiet "$@"
hb_steps 3
hb_step "checking prerequisites (Docker Engine >= $HB_MIN_ENGINE, Compose >= $HB_MIN_COMPOSE, disk)"
hb_require --compose --disk 2000 "the lab image and Lyrion"
hb_step "images: lab image (cached build), Lyrion and Mosquitto"
hb_ensure_image btbridge-lab:dev --always
hb_pull_compose_images docker/compose.yml
hb_step "starting the lab (project btbridge-lab: Lyrion, 4 players, Mosquitto on an internal network)"
cd docker
# Create the host-side lab state as the calling user; otherwise Docker creates .lab/ as root for the bind mount and later host-side
# steps (Buildroot fetch, coverage) cannot write there.
mkdir -p ../.lab/lms-config ../.lab/coverage
docker compose up -d lms player1 player2 player3 legacy1 mosquitto
docker compose ps
