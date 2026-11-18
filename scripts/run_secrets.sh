#!/usr/bin/env bash
# Secret scan of the COMPLETE Git history (all branches) with Gitleaks in Docker (DECISIONS #70). The repository is mounted read-only,
# the container has no network, findings are redacted. Exit code != 0 on any finding. Runs locally and in CI (job "security").
# In CI the checkout must have the full history (actions/checkout with fetch-depth: 0), otherwise only the last commit is scanned.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
IMAGE=ghcr.io/gitleaks/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055
commits=$(git rev-list --all --count)
[ "$(git rev-parse --is-shallow-repository)" = "false" ] || { echo "secrets: shallow clone - fetch the full history first"; exit 2; }
docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -v "$PWD":/repo:ro "$IMAGE" git /repo --log-opts="--all" --redact --no-banner --log-level warn; rc=$?
echo "gitleaks ($IMAGE) over $commits commits: rc=$rc"
exit $rc
