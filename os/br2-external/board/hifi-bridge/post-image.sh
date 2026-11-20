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
radio_note=""
if enabled BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI; then
	copy_licence "brcmfmac_sdio-firmware-rpi-*/LICENSE" brcmfmac_sdio-firmware-rpi.LICENSE
	# per-board radio firmware (post-build.sh, firmware/radio-<compatible>.txt): the Synaptics End-User Software Tool License only where
	# Synaptics Bluetooth files remain (Zero 2 W), so the other images carry neither those files nor that licence (DECISIONS #134)
	fwlist="${BOARD_DIR}/firmware/radio-${compat}.txt"
	[ -f "$fwlist" ] || { echo "post-image: no radio firmware list $fwlist" >&2; exit 1; }
	has_lic() { awk -v l="$1" '!/^#/ && NF >= 4 && $4 == l { f = 1 } END { exit !f }' "$fwlist"; }
	who="Cypress Semiconductor"
	if has_lic synaptics-wifi; then who="$who, Synaptics (Wi-Fi)"; fi
	radio_note="  * Wi-Fi and Bluetooth firmware of this board (${compat}) in /usr/lib/firmware of the root file system -
    brcmfmac_sdio-firmware-rpi.LICENSE (${who}; Broadcom for brcm/brcmfmac*.bin)"
	if has_lic synaptics-bt; then
		cp "${BOARD_DIR}/licenses/SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt" "$lic/"
		radio_note="${radio_note} and
    SYNAPTICS-END-USER-SOFTWARE-TOOL-LICENSE.txt (synaptics/*.hcd, Raspberry Pi Zero 2 W Bluetooth)"
	fi
fi
cat > "$lic/NOTICE.txt" <<EOF
Hi-Fi Bridge card image - third-party notices

This image is a collection of separately licensed programs. The Hi-Fi Bridge service is licensed under AGPL-3.0-only; its source
code is at https://github.com/iskaron/hifi-bridge. Every other component, its licence text and its source code are listed in the
legal-info archive published with the image (manifest.csv, licences, sources).

The following firmware is included as binary only and UNMODIFIED, under the licence files in this directory. It may be used only
with the hardware it was made for (the Raspberry Pi and its Broadcom/Cypress/Synaptics wireless chips):
  * Raspberry Pi boot firmware (bootcode.bin, start*.elf, fixup*.dat) - rpi-firmware.LICENCE.broadcom
    Copyright (c) 2006, Broadcom Corporation. Copyright (c) 2015, Raspberry Pi (Trading) Ltd. All rights reserved.
${radio_note}
EOF

# both copies of the U-Boot environment (review N-6, uboot.fragment). They must exist from the first boot on: fw_setenv (RAUC) refuses
# to work while one of the files is missing, and U-Boot would create the second only on the second boot. U-Boot imports a valid copy
# INSTEAD of its built-in defaults, so each copy = the complete default environment (u-boot-initial-env, BR2_TARGET_UBOOT_INITIAL_ENV)
# + the slot state of a fresh card (uboot-env.txt). Copy 1 carries serial 1, the redundant copy 0: both U-Boot and fw_env start from
# copy 1 and write the other one next. /etc/u-boot-initial-env is the Linux-side default by now (post-build.sh: U-Boot's default + the
# slot lines of linux-default-env.txt, whose keys are a subset of uboot-env.txt's), so stripping uboot-env.txt's keys gives U-Boot's own.
initial_env="${TARGET_DIR:-}/etc/u-boot-initial-env"
[ -n "${TARGET_DIR:-}" ] && [ -s "$initial_env" ] || {
	echo "post-image: U-Boot default environment ${initial_env} (u-boot-initial-env) not found - BR2_TARGET_UBOOT_INITIAL_ENV=y?" >&2; exit 1; }
MKENVIMAGE=mkenvimage
[ -n "${HOST_DIR:-}" ] && [ -x "${HOST_DIR}/bin/mkenvimage" ] && MKENVIMAGE="${HOST_DIR}/bin/mkenvimage"
env_size=$(awk '!/^#/ && $1 == "/boot/uboot.env" {print $3}' "${BOARD_DIR}/uboot/fw_env.config")
hifi_keys=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "${BOARD_DIR}/uboot/uboot-env.txt" | paste -sd'|')
envtxt="${BINARIES_DIR}/uboot-env.txt"
{ grep -Ev "^(${hifi_keys})=" "$initial_env" || true; grep -Ev '^(#|$)' "${BOARD_DIR}/uboot/uboot-env.txt"; } > "$envtxt"
"$MKENVIMAGE" -r -s "$env_size" -o "${BINARIES_DIR}/uboot.env" "$envtxt"
"$MKENVIMAGE" -r -s "$env_size" -o "${BINARIES_DIR}/uboot-redund.env" "$envtxt"
printf '\000' | dd of="${BINARIES_DIR}/uboot-redund.env" bs=1 seek=4 conv=notrunc status=none		# serial byte after the CRC

FILES=()
for i in "${BINARIES_DIR}"/rpi-firmware/* "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}/u-boot.bin" "${BINARIES_DIR}/boot.scr" \
         "${BINARIES_DIR}/uboot.env" "${BINARIES_DIR}/uboot-redund.env" "${BINARIES_DIR}/hifi-provision.json" "$lic"; do
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
