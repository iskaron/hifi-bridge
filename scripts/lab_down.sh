#!/usr/bin/env bash
# Remove the lab containers, networks and volumes (disposable; nothing outside Docker is touched).
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"
hb_require --compose
cd "$(dirname "$0")/../docker" && docker compose down -v --remove-orphans
