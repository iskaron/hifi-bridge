#!/usr/bin/env bash
# Fetch Buildroot at the pinned commit into .lab/buildroot (or $HIFI_LAB/buildroot) (host filesystem, not versioned). Shallow; about 80 MB.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require --tools git
COMMIT=679b9ead7620bbf193620d1ebf56f53c1764d37a          # 2026.02.x LTS ("Update for 2026.02.3"), docs/PHASE2B_PLAN.md
# Separate statements: with "mkdir && cd", a failing mkdir (e.g. .lab/ created by Docker as root) does not trigger set -e, and the
# fetch/checkout below then ran in the PROJECT repository (found by a CI replay in a fresh clone, 2026-11-17).
LAB="${HIFI_LAB:-$PWD/.lab}"   # lab directory (a git worktree can point at the main checkout's .lab)
mkdir -p "$LAB/buildroot"
cd "$LAB/buildroot"
[ -d .git ] || git init -q .
# Never touch any other repository: this directory must be the top level of its own Git repository.
[ "$(git rev-parse --show-toplevel)" = "$(pwd -P)" ] || { echo "os_fetch_buildroot: $(pwd -P) is not its own Git repository - refusing" >&2; exit 2; }
if [ "$(git rev-parse HEAD 2>/dev/null)" != "$COMMIT" ]; then
  git fetch -q --depth 1 https://gitlab.com/buildroot.org/buildroot.git "$COMMIT"
  git checkout -q FETCH_HEAD
fi
echo "buildroot $(git rev-parse --short HEAD)"
