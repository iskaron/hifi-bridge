#!/usr/bin/env bash
# Remove the lab containers, networks and volumes (disposable; nothing outside Docker is touched).
cd "$(dirname "$0")/../docker" && docker compose down -v --remove-orphans
