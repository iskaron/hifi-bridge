#!/usr/bin/env bash
# (Re)generate the committed board defconfigs of the br2-external tree from the pinned upstream board defconfig + our fragments, and/or
# check them. Runs inside btbridge-os (Docker). Buildroot source: .lab/buildroot at the pinned commit (scripts/os_fetch_buildroot.sh).
#   scripts/os_defconfigs.sh generate   - rewrite os/br2-external/configs/*_defconfig
#   scripts/os_defconfigs.sh check      - fail if a committed defconfig is stale OR if Kconfig dropped any required fragment line
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev
MODE="${1:-check}"
LAB="${HIFI_LAB:-$PWD/.lab}"
[ -d "$LAB/buildroot/configs" ] || scripts/os_fetch_buildroot.sh || exit 2
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e BR2_DL_DIR=/tmp/dl -e MODE="$MODE" -v "$PWD":/work -v "$LAB/buildroot":/buildroot:ro btbridge-os:dev bash -c '
set -uo pipefail
EXT=/work/os/br2-external; rc=0
for pair in rpi3_64:raspberrypi3_64 rpi4_64:raspberrypi4_64 rpizero2w_64:raspberrypizero2w_64; do
  b=${pair%%:*}; up=${pair#*:}; O=/tmp/out-$b; mkdir -p $O
  cat /buildroot/configs/${up}_defconfig $EXT/fragments/common.fragment $EXT/fragments/$b.fragment > /tmp/merged-$b
  if [ "$MODE" = generate ]; then
    make -s -C /buildroot O=$O BR2_EXTERNAL=$EXT BR2_DEFCONFIG=/tmp/merged-$b defconfig >/tmp/log-$b 2>&1 || { cat /tmp/log-$b; exit 1; }
    make -s -C /buildroot O=$O BR2_EXTERNAL=$EXT savedefconfig BR2_DEFCONFIG=$EXT/configs/hifi_bridge_${b}_defconfig >/dev/null 2>&1
    echo "generated configs/hifi_bridge_${b}_defconfig"
  fi
  make -s -C /buildroot O=$O BR2_EXTERNAL=$EXT hifi_bridge_${b}_defconfig >/tmp/log-$b 2>&1 || { cat /tmp/log-$b; rc=1; continue; }
  # every required line of the fragments must survive Kconfig (an unmet dependency silently drops it)
  missing=0
  while IFS= read -r line; do
    case "$line" in ""|"#"*" is not set") ;; esac
    if [[ "$line" =~ ^#\ (BR2_[A-Z0-9_]+)\ is\ not\ set$ ]]; then
      grep -q "^${BASH_REMATCH[1]}=y" $O/.config && { echo "  $b: $line - but it is set"; missing=1; }
    elif [[ "$line" =~ ^(BR2_[A-Z0-9_]+)=(.*)$ ]]; then
      grep -qxF "$line" $O/.config || { echo "  $b: required but not in .config: $line"; missing=1; }
    fi
  done < <(cat $EXT/fragments/common.fragment $EXT/fragments/$b.fragment)
  # stale check: regenerate into a temp file and compare with the committed one
  make -s -C /buildroot O=$O BR2_EXTERNAL=$EXT BR2_DEFCONFIG=/tmp/merged-$b defconfig >/dev/null 2>&1
  make -s -C /buildroot O=$O BR2_EXTERNAL=$EXT savedefconfig BR2_DEFCONFIG=/tmp/fresh-$b >/dev/null 2>&1
  if ! diff -q /tmp/fresh-$b $EXT/configs/hifi_bridge_${b}_defconfig >/dev/null; then echo "  $b: committed defconfig is stale (run generate)"; missing=1; fi
  [ $missing -eq 0 ] && echo "PASS $b: defconfig resolves, all $(grep -cE "^(BR2_|# BR2_)" $EXT/fragments/common.fragment) common + board requirements kept" || rc=1
done
# the libubootenv source the environment simulations compile (docker/Dockerfile.os, os/tools/build_libubootenv.sh) = the image'\''s
bv=$(sed -n "s/^LIBUBOOTENV_VERSION = //p" /buildroot/package/libubootenv/libubootenv.mk)
bh=$(awk -v f="libubootenv-$bv.tar.gz" "\$3 == f {print \$2}" /buildroot/package/libubootenv/libubootenv.hash)
if [ -n "$bv" ] && [ -n "$bh" ] && grep -qx "VERSION=$bv" /work/os/tools/build_libubootenv.sh && grep -qx "SHA256=$bh" /work/os/tools/build_libubootenv.sh \
   && grep -qx "ARG LIBUBOOTENV_VERSION=$bv" /work/docker/Dockerfile.os && grep -qx "ARG LIBUBOOTENV_SHA256=$bh" /work/docker/Dockerfile.os; then
  echo "PASS libubootenv $bv: simulation source = Buildroot pin (sha256 ${bh:0:16}...)"
else
  echo "  libubootenv: Buildroot pins $bv ($bh) - docker/Dockerfile.os / os/tools/build_libubootenv.sh differ"; rc=1
fi
exit $rc'
