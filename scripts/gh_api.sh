#!/usr/bin/env bash
# GitHub REST API helper (CI monitoring, failure analysis; CLAUDE.md "GitHub access token").
# Reads the token from secrets/github-token (format GITHUB_TOKEN=..., git-ignored, mode 0600). The token is never printed, logged or
# passed as a command-line argument: curl reads the Authorization header from stdin (-K -). Only https://api.github.com is contacted;
# redirects (e.g. log downloads) go to pre-signed URLs and curl does not forward the header to other hosts.
# Usage: scripts/gh_api.sh /repos/iskaron/hifi-bridge/actions/runs [extra curl args, e.g. -i, -L, -o file]
set -euo pipefail
cd "$(dirname "$0")/.."
F=secrets/github-token
[ -f "$F" ] || { echo "gh_api: $F is missing" >&2; exit 2; }
perm=$(stat -c %a "$F")
[ "$perm" = 600 ] || { echo "gh_api: $F must have mode 0600 (has $perm)" >&2; exit 2; }
tok=$(sed -n 's/^GITHUB_TOKEN=//p' "$F" | head -n 1)
[ -n "$tok" ] || { echo "gh_api: no GITHUB_TOKEN= line in $F" >&2; exit 2; }
path=${1:?usage: gh_api.sh /repos/... [extra curl args]}
shift
case "$path" in
  /*) url="https://api.github.com$path" ;;
  https://api.github.com/*) url=$path ;;
  *) echo "gh_api: only api.github.com paths are allowed" >&2; exit 2 ;;
esac
printf 'header = "Authorization: Bearer %s"\n' "$tok" |
  curl -sS -K - -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" "$@" "$url"
