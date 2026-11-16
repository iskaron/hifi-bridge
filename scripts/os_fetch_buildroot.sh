#!/usr/bin/env bash
# Fetch Buildroot at the pinned commit into .lab/buildroot (host filesystem, not versioned). Shallow; about 80 MB.
set -euo pipefail
cd "$(dirname "$0")/.."
COMMIT=679b9ead7620bbf193620d1ebf56f53c1764d37a          # 2026.02.x LTS ("Update for 2026.02.3"), docs/PHASE2B_PLAN.md
mkdir -p .lab/buildroot && cd .lab/buildroot
[ -d .git ] || git init -q .
if [ "$(git rev-parse HEAD 2>/dev/null)" != "$COMMIT" ]; then
  git fetch -q --depth 1 https://gitlab.com/buildroot.org/buildroot.git "$COMMIT"
  git checkout -q FETCH_HEAD
fi
echo "buildroot $(git rev-parse --short HEAD)"
