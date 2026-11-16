#!/usr/bin/env bash
# Static security analysis of the product code (bandit 1.8.6 in Docker). Fails on any MEDIUM/HIGH finding that is not explicitly
# justified with "# nosec <id> - reason". Full report (incl. LOW): reports/evidence/security_bandit.txt
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
docker image inspect btbridge-security:dev >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.security -t btbridge-security:dev docker >/dev/null
docker run --rm -v "$PWD":/w:ro btbridge-security:dev bandit -q -r bridge/btbridge -f txt > reports/evidence/security_bandit.txt 2>&1
docker run --rm -v "$PWD":/w:ro btbridge-security:dev bandit -q -r bridge/btbridge -ll; rc=$?
grep -A4 "Total issues (by severity)" reports/evidence/security_bandit.txt
echo "bandit medium/high gate: rc=$rc"
exit $rc
