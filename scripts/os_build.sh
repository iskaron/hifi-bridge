#!/usr/bin/env bash
# Internal DEVELOPMENT image build for one board, in Docker (btbridge-os). Hardware-free: builds only, nothing is flashed or booted.
#
#   scripts/os_build.sh <board> [step ...]      board: rpi3_64 | rpi4_64 | rpizero2w_64
#   steps (default: build legal sbom):
#     build   configure (committed defconfig + fragments/dev-build.fragment) and `make` (incremental; the bridge package is always
#             re-synced from the working tree)
#     legal   `make legal-info` (licence texts, sources, manifest.csv)
#     sbom    `make show-info` -> show-info.json, CycloneDX SBOM (utils/generate-cyclonedx), licence policy report
#     clean   remove the board's output directory first (download cache and ccache are kept)
#
# Environment (all optional):
#   HIFI_LAB        lab directory on the host filesystem (default: <repo>/.lab); Buildroot source in $HIFI_LAB/buildroot
#   HIFI_OS_OUT     output directory (default: $HIFI_LAB/br-out/<board>)
#   HIFI_OS_JOBS    BR2_JLEVEL (default 3)    HIFI_OS_CPUS (default 3)    HIFI_OS_MEM (default 8g)
#   HIFI_OS_MIN_FREE_GB   refuse to start below this much free space on the output filesystem (default 40)
#
# Results: $OUT/images/hifi-bridge-<compatible>-<version>-development.img (+ .manifest.json), $OUT/legal-info/, $OUT/hifi/
# (show-info.json, sbom.cdx.json, licences.md, build-times.tsv, logs/). Check them with scripts/os_check_image.sh <board>.
# The update trust is "own" with the temporary development CA from scripts/os_dev_ca.sh; only its PUBLIC certificate is mounted.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
BOARD="${1:?usage: os_build.sh <rpi3_64|rpi4_64|rpizero2w_64> [build|legal|sbom|clean ...]}"
shift
case "$BOARD" in rpi3_64|rpi4_64|rpizero2w_64) ;; *) echo "os_build: unknown board $BOARD" >&2; exit 2 ;; esac
STEPS="${*:-build legal sbom}"
LAB="${HIFI_LAB:-$REPO/.lab}"
OUT="${HIFI_OS_OUT:-$LAB/br-out/$BOARD}"
JOBS="${HIFI_OS_JOBS:-3}"; CPUS="${HIFI_OS_CPUS:-3}"; MEM="${HIFI_OS_MEM:-8g}"; MIN_FREE="${HIFI_OS_MIN_FREE_GB:-40}"
PINNED=679b9ead7620bbf193620d1ebf56f53c1764d37a

[ -d "$LAB/buildroot/configs" ] || HIFI_LAB="$LAB" scripts/os_fetch_buildroot.sh
[ "$(cat "$LAB/buildroot/.git/HEAD" 2>/dev/null)" = "$PINNED" ] || { echo "os_build: $LAB/buildroot is not at the pinned commit $PINNED" >&2; exit 2; }
docker image inspect btbridge-os:dev >/dev/null 2>&1 || docker build -q -f docker/Dockerfile.os -t btbridge-os:dev docker >/dev/null

if [[ " $STEPS " == *" clean "* ]]; then
	echo "os_build: removing $OUT"
	rm -rf "$OUT"
fi
mkdir -p "$OUT/hifi/logs" "$LAB/br-dl" "$LAB/br-ccache"
free_gb=$(df --output=avail -BG "$OUT" | tail -1 | tr -dc 0-9)
if [ "$free_gb" -lt "$MIN_FREE" ]; then
	echo "os_build: only ${free_gb} GB free on the output filesystem (minimum $MIN_FREE) - refusing to build" >&2
	exit 3
fi
HIFI_LAB="$LAB" scripts/os_dev_ca.sh

stamp=$(date -u +%Y%m%dT%H%M%SZ)
log="$OUT/hifi/logs/$stamp.log"
echo "os_build: $BOARD steps [$STEPS], jobs $JOBS, cpus $CPUS, memory $MEM, ${free_gb} GB free; log $log"
name="hifi-os-build-$BOARD"
docker rm -f "$name" >/dev/null 2>&1 || true
set +e
docker run --rm --name "$name" --cpus="$CPUS" --memory="$MEM" -u "$(id -u):$(id -g)" -e HOME=/tmp \
	-e BOARD="$BOARD" -e JOBS="$JOBS" -e STEPS="$STEPS" \
	-v "$REPO":/work:ro -v "$LAB/buildroot":/buildroot:ro -v "$OUT":/out -v "$LAB/br-dl":/dl -v "$LAB/br-ccache":/ccache \
	-v "$LAB/dev-update-ca/ca.pem":/devca/ca.pem:ro \
	btbridge-os:dev bash -c '
set -euo pipefail
EXT=/work/os/br2-external; B=/buildroot; O=/out
times=$O/hifi/build-times.tsv
[ -s $times ] || printf "step\tstart_utc\tseconds\tresult\n" > $times
step() {	# name command...
	local n=$1; shift; local t0; t0=$(date +%s); echo "=== $n ($(date -u +%FT%TZ))"
	if "$@"; then r=ok; else r=FAILED; fi
	printf "%s\t%s\t%s\t%s\n" "$n" "$(date -u -d @$t0 +%FT%TZ)" "$(( $(date +%s) - t0 ))" "$r" >> $times
	[ $r = ok ]
}
configure() {
	cat $EXT/configs/hifi_bridge_${BOARD}_defconfig $EXT/fragments/dev-build.fragment > /tmp/merged
	echo "BR2_JLEVEL=$JOBS" >> /tmp/merged
	make -s -C $B O=$O BR2_EXTERNAL=$EXT BR2_DEFCONFIG=/tmp/merged defconfig
	# an unmet dependency would silently drop a dev-build line (same rule as scripts/os_defconfigs.sh)
	while IFS= read -r l; do
		[[ "$l" =~ ^BR2_ ]] || continue
		grep -qxF "$l" $O/.config || { echo "configure: dropped by Kconfig: $l" >&2; return 1; }
	done < $EXT/fragments/dev-build.fragment
}
build() {
	# local package: re-sync the bridge sources from the working tree on every build
	if ls $O/build/hifi-bridge-*/.stamp_target_installed >/dev/null 2>&1; then make -C $O hifi-bridge-rebuild; fi
	make -C $O
}
sbom() {
	make -s -C $O show-info > $O/hifi/show-info.json
	python3 $B/utils/generate-cyclonedx -i $O/hifi/show-info.json -o $O/hifi/sbom.cdx.json \
		--project-name "hifi-bridge-$BOARD" --project-version "$(sed -n "s/^VERSION_ID=//p" $O/target/usr/lib/os-release)"
	python3 /work/os/tools/license_report.py $O/hifi/show-info.json $BOARD $O/hifi/licences.md
}
for s in $STEPS; do
	case $s in
		clean) ;;
		build) step configure configure; step build build ;;
		legal) step legal-info make -C $O legal-info ;;
		sbom) step sbom sbom ;;
		*) echo "unknown step $s" >&2; exit 2 ;;
	esac
done
echo "=== done ($(date -u +%FT%TZ))"
' 2>&1 | tee "$log" | grep --line-buffered -E '^(===|>>>|ERROR|make: \*\*\*|configure:|hifi-bridge post-|post-image:)'
rc=${PIPESTATUS[0]}
set -e
echo "os_build: $BOARD exit code $rc (log $log)"
exit "$rc"
