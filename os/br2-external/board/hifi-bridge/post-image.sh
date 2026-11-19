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

# third-party notices on the FAT, readable on any computer: the firmware licences require the licence text and copyright notice to
# accompany every copy (docs/IMAGE_LICENSING.md). Required whenever the firmware package is part of the image.
lic="${BINARIES_DIR}/licenses"
rm -rf "$lic"
mkdir -p "$lic"
enabled() { grep -q "^$1=y" "$BR2_CONFIG"; }
copy_licence() {	# $1 glob below BUILD_DIR, $2 name on the FAT
	local f
	f=$(ls -d ${BUILD_DIR}/$1 2>/dev/null | head -1 || true)
	[ -n "$f" ] && [ -f "$f" ] || { echo "post-image: licence file $1 not found in ${BUILD_DIR}" >&2; exit 1; }
	cp "$f" "$lic/$2"
}
if enabled BR2_PACKAGE_RPI_FIRMWARE; then copy_licence "rpi-firmware-*/boot/LICENCE.broadcom" rpi-firmware.LICENCE.broadcom; fi
if enabled BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI; then
	copy_licence "brcmfmac_sdio-firmware-rpi-*/LICENSE" brcmfmac_sdio-firmware-rpi.LICENSE
	cp "${BOARD_DIR}/licenses/SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt" "$lic/"
fi
cat > "$lic/NOTICE.txt" <<'EOF'
Hi-Fi Bridge card image - third-party notices

This image is a collection of separately licensed programs. The Hi-Fi Bridge service is licensed under AGPL-3.0-only; its source
code is at https://github.com/iskaron/hifi-bridge. Every other component, its licence text and its source code are listed in the
legal-info archive published with the image (manifest.csv, licences, sources).

The following firmware is included as binary only and UNMODIFIED, under the licence files in this directory. It may be used only
with the hardware it was made for (the Raspberry Pi and its Broadcom/Cypress/Synaptics wireless chips):
  * Raspberry Pi boot firmware (bootcode.bin, start*.elf, fixup*.dat) - rpi-firmware.LICENCE.broadcom
    Copyright (c) 2006, Broadcom Corporation. Copyright (c) 2015, Raspberry Pi (Trading) Ltd. All rights reserved.
  * Wi-Fi and Bluetooth firmware in /usr/lib/firmware/{brcm,cypress,synaptics} of the root file system -
    brcmfmac_sdio-firmware-rpi.LICENSE (Broadcom, Cypress Semiconductor, Synaptics) and
    SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt (synaptics/*.hcd, Raspberry Pi Zero 2 W Bluetooth).
EOF

FILES=()
for i in "${BINARIES_DIR}"/rpi-firmware/* "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}/u-boot.bin" "${BINARIES_DIR}/boot.scr" \
         "${BINARIES_DIR}/hifi-provision.json" "$lic"; do
	[ -e "$i" ] || { echo "post-image: missing $i" >&2; exit 1; }
	FILES+=( "${i#"${BINARIES_DIR}"/}" )
done
BOOT_FILES=$(printf '\\t\\t\\t"%s",\\n' "${FILES[@]}")
# reproducible boot FAT: mcopy stores each file's modification time; the inputs carry their build time (found by comparing two clean
# builds: identical files, different FAT). Pin them to the build epoch. These are build outputs in BINARIES_DIR, nothing else.
if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
	for f in "${FILES[@]}"; do find "${BINARIES_DIR}/$f" -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +; done
fi
# -L: in a real build rootfs.ext4 is a symlink to rootfs.ext2 (found by the first real build: slot B got 11 bytes)
ROOTFS_SIZE=$(stat -L -c %s "${BINARIES_DIR}/rootfs.ext4")
# reproducible data.ext4: genimage stamps the super block (write/check time) after mke2fs; fs-timestamp pins it to the build epoch
# (Buildroot exports SOURCE_DATE_EPOCH with BR2_REPRODUCIBLE; without it the current time is used)
FS_TIMESTAMP=$(date -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m%d%H%M%S)
sed -e "s|#BOOT_FILES#|${BOOT_FILES}|" -e "s|#ROOTFS_SIZE#|${ROOTFS_SIZE}|" -e "s|#FS_TIMESTAMP#|${FS_TIMESTAMP}|" \
	"${BOARD_DIR}/genimage.cfg.in" > "${GENIMAGE_CFG}"

trap 'rm -rf "${ROOTPATH_TMP}"' EXIT
ROOTPATH_TMP="$(mktemp -d)"
rm -rf "${GENIMAGE_TMP}"
genimage --rootpath "${ROOTPATH_TMP}" --tmppath "${GENIMAGE_TMP}" --inputpath "${BINARIES_DIR}" \
	--outputpath "${BINARIES_DIR}" --config "${GENIMAGE_CFG}"

img="${BINARIES_DIR}/hifi-bridge-${compat}-${version}${suffix}.img"
mv "${BINARIES_DIR}/sdcard.img" "$img"
python3 -m hififlash.manifest build --image "$img" --compatible "$compat" --version "$version" --out "${img}.manifest.json"
echo "post-image: $img (+ manifest; sign it offline before publishing)"
