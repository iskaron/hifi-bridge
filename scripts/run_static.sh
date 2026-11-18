#!/usr/bin/env bash
# STATIC checks (Docker only): shellcheck for all shell scripts, Python byte-compilation, systemd unit verification, workflow lint.
set -uo pipefail
cd "$(dirname "$0")/.."  || exit 1
rc=0
step() { echo "### $1"; shift; "$@" || rc=1; }
step shellcheck docker run --rm -v "$PWD":/w:ro -w /w koalaman/shellcheck-alpine:v0.10.0@sha256:5921d946dac740cbeec2fb1c898747b6105e585130cc7f0602eec9a10f7ddb63 sh -c \
  'shellcheck -S warning scripts/*.sh docker/mosquitto/start.sh demo/mosquitto/start.sh testkit/pw/start_pipewire.sh os/br2-external/board/hifi-bridge/post-image.sh \
   && shellcheck -s sh packaging/bin/hifi-bridge-respawn os/br2-external/board/hifi-bridge/post-build.sh \
      os/br2-external/board/hifi-bridge/overlay/usr/libexec/hifi-bridge/growdata.sh'
step licence-copy cmp LICENSE bridge/LICENSE                  # Buildroot needs the licence inside the package directory
step python-compile docker run --rm -v "$PWD":/w:ro -w /w python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 python3 -c \
  'import pathlib,sys
bad = 0
for p in sorted(x for x in pathlib.Path(".").glob("**/*.py") if ".lab" not in x.parts):
    try:
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
    except SyntaxError as e:
        print(e); bad += 1
print(f"compiled without writing bytecode; {bad} error(s)"); sys.exit(1 if bad else 0)'
docker image inspect btbridge-static:dev >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.static -t btbridge-static:dev docker >/dev/null
step systemd-unit docker run --rm -v "$PWD/packaging":/p:ro -v "$PWD/os/br2-external/board/hifi-bridge/overlay":/o:ro btbridge-static:dev sh -c \
  'cp /p/systemd/*.service /o/usr/lib/systemd/system/*.service /etc/systemd/system/ && mkdir -p /usr/libexec/hifi-bridge \
   && cp /o/usr/libexec/hifi-bridge/growdata.sh /usr/libexec/hifi-bridge/ \
   && systemd-analyze verify /etc/systemd/system/hifi-bridge*.service \
   && for u in hifi-bridge hifi-bridge-boot; do printf "%s: " $u; systemd-analyze security --offline=yes /etc/systemd/system/$u.service | tail -1; done'
step actionlint docker run --rm -v "$PWD":/repo:ro -w /repo rhysd/actionlint:1.7.7@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color
echo "static exit: $rc"
exit $rc
