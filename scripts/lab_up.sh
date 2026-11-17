#!/usr/bin/env bash
# Build the lab image and start real LMS + 4 headless Squeezelite players (3 normal, 1 'legacy-capability' emulation) + Mosquitto.
set -euo pipefail
cd "$(dirname "$0")/../docker"
# Create the host-side lab state as the calling user; otherwise Docker creates .lab/ as root for the bind mount and later host-side
# steps (Buildroot fetch, coverage) cannot write there.
mkdir -p ../.lab/lms-config ../.lab/coverage
docker compose build
docker compose up -d lms player1 player2 player3 legacy1 mosquitto
docker compose ps
