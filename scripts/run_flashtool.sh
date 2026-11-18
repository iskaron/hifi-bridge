#!/usr/bin/env bash
# Flash tool (core + PySide6 GUI + privileged helper) in Docker only (hifi-flash:dev, docker/Dockerfile.flashtool).
#   scripts/run_flashtool.sh test [pytest args]   tests/flashtool + tests/flashtool_gui (QT_QPA_PLATFORM=offscreen, SIMULATED cards only)
#   scripts/run_flashtool.sh coverage             line + branch coverage of flashtool/hififlash -> reports/evidence/flashtool_coverage.txt
#   scripts/run_flashtool.sh screenshots          offscreen screenshots -> reports/screenshots/flashtool-*.png
#   scripts/run_flashtool.sh package              Linux one-folder package (PyInstaller) + offscreen smoke test -> build/flashtool/
# Nothing here enumerates host disks or writes a block device: the container sees no host device (no --device, no --privileged), it has
# no network for tests (--network none), and the tests fail if a platform command (lsblk, diskutil, ...) is started.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
IMAGE=hifi-flash:dev
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.flashtool -t "$IMAGE" docker >/dev/null || exit 1
NAME="hifi-flash-$$"
run() {
  docker run --rm --name "$NAME" --cpus 2 --memory 3g --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work "$IMAGE" "$@"
}
cmd="${1:-test}"; shift || true
case "$cmd" in
  test)
    run python3 -m pytest -p no:cacheprovider -q tests/flashtool tests/flashtool_gui "$@" ;;
  coverage)
    mkdir -p reports/evidence
    run sh -c 'COVERAGE_FILE=/tmp/.coverage python3 -m coverage run --branch --source=/work/flashtool/hififlash -m pytest -p no:cacheprovider -q \
                 tests/flashtool tests/flashtool_gui >/tmp/pytest.txt 2>&1; rc=$?; tail -3 /tmp/pytest.txt;
               COVERAGE_FILE=/tmp/.coverage python3 -m coverage report --skip-empty -m; exit $rc' > reports/evidence/flashtool_coverage.txt
    rc=$?; tail -4 reports/evidence/flashtool_coverage.txt; exit $rc ;;
  screenshots)
    mkdir -p reports/screenshots
    run python3 -m hififlash.gui.screenshots /work/reports/screenshots ;;
  package)
    run sh flashtool/packaging/linux/build.sh ;;
  *)
    echo "usage: $0 test|coverage|screenshots|package [args]" >&2; exit 2 ;;
esac
