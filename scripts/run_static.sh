#!/usr/bin/env bash
# STATIC checks (Docker only): shellcheck for all shell scripts, Python byte-compilation, systemd unit verification, workflow lint.
set -uo pipefail
cd "$(dirname "$0")/.."  || exit 1
rc=0
step() { echo "### $1"; shift; "$@" || rc=1; }
step shellcheck docker run --rm -v "$PWD":/w:ro -w /w koalaman/shellcheck-alpine:v0.10.0 sh -c \
  'shellcheck -S warning scripts/*.sh docker/mosquitto/start.sh testkit/pw/start_pipewire.sh && shellcheck -s sh packaging/bin/hifi-bridge-respawn'
step python-compile docker run --rm -v "$PWD":/w:ro -w /w python:3.11-slim python3 -c \
  'import pathlib,sys
bad = 0
for p in sorted(pathlib.Path(".").glob("**/*.py")):
    try:
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
    except SyntaxError as e:
        print(e); bad += 1
print(f"compiled without writing bytecode; {bad} error(s)"); sys.exit(1 if bad else 0)'
docker image inspect btbridge-static:dev >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.static -t btbridge-static:dev docker >/dev/null
step systemd-unit docker run --rm -v "$PWD/packaging":/p:ro btbridge-static:dev sh -c \
  'cp /p/systemd/*.service /etc/systemd/system/ && systemd-analyze verify /etc/systemd/system/hifi-bridge*.service && systemd-analyze security --offline=yes /etc/systemd/system/hifi-bridge.service | tail -1'
step actionlint docker run --rm -v "$PWD":/repo:ro -w /repo rhysd/actionlint:1.7.7 -color
echo "static exit: $rc"
exit $rc
