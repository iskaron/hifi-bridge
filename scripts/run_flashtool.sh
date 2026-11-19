#!/usr/bin/env bash
# Flash tool (core + PySide6 GUI + privileged helper) in Docker only (btbridge-flash:dev, docker/Dockerfile.flashtool).
#   scripts/run_flashtool.sh test [pytest args]   tests/flashtool + tests/flashtool_gui (QT_QPA_PLATFORM=offscreen, SIMULATED cards only)
#   scripts/run_flashtool.sh coverage             line + branch coverage of flashtool/hififlash -> reports/evidence/flashtool_coverage.txt
#   scripts/run_flashtool.sh screenshots          offscreen screenshots -> reports/screenshots/flashtool-*.png
#   scripts/run_flashtool.sh package              Linux one-folder package (PyInstaller) + offscreen smoke test -> build/flashtool/
# Nothing here enumerates host disks or writes a block device: the container sees no host device (no --device, no --privileged), it has
# no network (--network none; download tests use a loopback server inside the container), and the tests fail if a process such as
# lsblk/diskutil/PowerShell is started.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-flash:dev || exit 1
IMAGE=btbridge-flash:dev
NAME="hifi-flash-$$"
run() {
  docker run --rm --init --name "$NAME" --cpus 2 --memory 3g --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work "$IMAGE" "$@"
}
cmd="${1:-test}"; shift || true
case "$cmd" in
  test)
    targets=(tests/flashtool tests/flashtool_gui)
    for a in "$@"; do case "$a" in tests/*) targets=();; esac; done      # an explicit test path replaces the default targets
    run python3 -m pytest -p no:cacheprovider "${targets[@]}" "$@" ;;
  coverage)
    mkdir -p reports/evidence
    run sh -c 'COVERAGE_FILE=/tmp/.coverage python3 -m coverage run --rcfile=.coveragerc-flashtool -m pytest -p no:cacheprovider -q \
                 tests/flashtool tests/flashtool_gui >/tmp/pytest.txt 2>&1; rc=$?; tail -3 /tmp/pytest.txt;
               COVERAGE_FILE=/tmp/.coverage python3 -m coverage report --rcfile=.coveragerc-flashtool; exit $rc' > reports/evidence/flashtool_coverage.txt
    rc=$?; tail -4 reports/evidence/flashtool_coverage.txt; exit $rc ;;
  screenshots)
    mkdir -p reports/screenshots
    run python3 tests/flashtool_gui/make_screenshots.py /work/reports/screenshots ;;
  package)
    run sh flashtool/packaging/linux/build.sh ;;
  *)
    echo "usage: $0 test|coverage|screenshots|package [args]" >&2; exit 2 ;;
esac
