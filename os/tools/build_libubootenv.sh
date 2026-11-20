#!/bin/sh
# Compile the image's U-Boot environment tools (libubootenv fw_printenv/fw_setenv) NATIVELY for the corruption simulations, from the same
# source and patches as the image: the tarball pinned by Buildroot (package/libubootenv, sha256 below = libubootenv.hash; the Docker image
# btbridge-os keeps a verified copy in /opt/src) + every patch in os/br2-external/patches/libubootenv. Flags as Buildroot's cmake build of
# the package: -std=gnu99, Release (-DNDEBUG: no "Environment OK, copy N" debug lines), YAML support on, zlib; same default paths
# (/etc/fw_env.config, /etc/u-boot-initial-env) unless overridden with -c/-f. Runs inside btbridge-os (Docker). The result is a host (x86)
# build of the same C code, not the aarch64 binary of the image - SIMULATED evidence.
#
#   os/tools/build_libubootenv.sh OUTDIR       -> OUTDIR/fw_printenv, OUTDIR/fw_setenv (symlink), OUTDIR/VERSION
set -eu
OUT="${1:?usage: build_libubootenv.sh OUTDIR}"
VERSION=0.3.7
SHA256=92021fc5a0a965030588d0c867a771d28b28c0d17c9fd022f0dafe9f01de714e
SRC="${LIBUBOOTENV_TARBALL:-/opt/src/libubootenv-$VERSION.tar.gz}"
PATCHES="$(cd "$(dirname "$0")/../br2-external/patches/libubootenv" && pwd)"
[ -f "$SRC" ] || { echo "build_libubootenv: $SRC missing - rebuild the btbridge-os image (docker build -f docker/Dockerfile.os -t btbridge-os:dev docker)" >&2; exit 2; }
echo "$SHA256  $SRC" | sha256sum -c - >/dev/null || { echo "build_libubootenv: $SRC does not match the pinned sha256" >&2; exit 2; }
mkdir -p "$OUT"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar xzf "$SRC" -C "$work"
d="$work/libubootenv-$VERSION"
for p in "$PATCHES"/*.patch; do
	[ -e "$p" ] || continue
	patch -s -p1 -d "$d" < "$p"
done
gcc -std=gnu99 -O2 -DNDEBUG -DVERSION="\"$VERSION\"" -I"$d/src" -o "$OUT/fw_printenv" \
	"$d/src/fw_printenv.c" "$d/src/uboot_env.c" "$d/src/uboot_mtd.c" "$d/src/extended_config.c" "$d/src/common.c" -lz -lyaml
ln -sf fw_printenv "$OUT/fw_setenv"
{ echo "libubootenv $VERSION"; for p in "$PATCHES"/*.patch; do [ -e "$p" ] || continue; echo "patch $(basename "$p") $(sha256sum < "$p" | cut -c1-16)"; done; } > "$OUT/VERSION"
