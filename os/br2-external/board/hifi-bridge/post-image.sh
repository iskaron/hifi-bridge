#!/bin/bash
# Hi-Fi Bridge post-image step: card image via genimage + flash manifest (unsigned; signing is a separate offline release step,
# docs/UPDATE_TRUST.md). Exercised in Docker with dummy content (tests/os) and by every real build (scripts/os_build.sh); not booted.
# Environment from Buildroot: BINARIES_DIR, BUILD_DIR, BR2_CONFIG.
set -euo pipefail

BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$BOARD_DIR/../../../.." && pwd)"
GENIMAGE_CFG="${BINARIES_DIR}/genimage.cfg"
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
export PYTHONPATH="${REPO_DIR}/bridge:${REPO_DIR}/flashtool"

cfg() { sed -n "s/^$1=//p" "$BR2_CONFIG" | sed 's/^"//; s/"$//'; }
compat="$(cfg BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE)"
[ -n "$compat" ] || { echo "post-image: BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE is empty" >&2; exit 1; }
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO_DIR/bridge/btbridge/__init__.py")"
# development images carry the variant in the file name (os-release and build-info.json carry it inside the image)
variant="$(cfg BR2_PACKAGE_HIFI_BRIDGE_BUILD_VARIANT)"
suffix=""
[ -n "$variant" ] && [ "$variant" != release ] && suffix="-$variant"

# the empty provisioning slot (fixed size, filled in place by the flash tool or by hand)
python3 -m btbridge.provisioning placeholder > "${BINARIES_DIR}/hifi-provision.json"

FILES=()
for i in "${BINARIES_DIR}"/rpi-firmware/* "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}/u-boot.bin" "${BINARIES_DIR}/boot.scr" \
         "${BINARIES_DIR}/hifi-provision.json"; do
	[ -e "$i" ] || { echo "post-image: missing $i" >&2; exit 1; }
	FILES+=( "${i#"${BINARIES_DIR}"/}" )
done
BOOT_FILES=$(printf '\\t\\t\\t"%s",\\n' "${FILES[@]}")
ROOTFS_SIZE=$(stat -c %s "${BINARIES_DIR}/rootfs.ext4")
sed -e "s|#BOOT_FILES#|${BOOT_FILES}|" -e "s|#ROOTFS_SIZE#|${ROOTFS_SIZE}|" "${BOARD_DIR}/genimage.cfg.in" > "${GENIMAGE_CFG}"

trap 'rm -rf "${ROOTPATH_TMP}"' EXIT
ROOTPATH_TMP="$(mktemp -d)"
rm -rf "${GENIMAGE_TMP}"
genimage --rootpath "${ROOTPATH_TMP}" --tmppath "${GENIMAGE_TMP}" --inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" --config "${GENIMAGE_CFG}"

img="${BINARIES_DIR}/hifi-bridge-${compat}-${version}${suffix}.img"
mv "${BINARIES_DIR}/sdcard.img" "$img"
python3 -m hififlash.manifest build --image "$img" --compatible "$compat" --version "$version" --out "${img}.manifest.json"
echo "post-image: $img (+ manifest; sign it offline before publishing)"
