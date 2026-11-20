#!/bin/sh
# Hi-Fi Bridge post-build step (Buildroot calls it with TARGET_DIR as $1 and BR2_CONFIG in the environment).
# Exercised by tests/os with a fake target tree and run by every real build (scripts/os_build.sh); nothing has been booted.
#
#  * update trust (P5, docs/UPDATE_TRUST.md): builds /etc/rauc/keyring.pem from the EXPLICIT choice in the configuration and records it in
#    /etc/hifi-bridge/update-trust.json. Fails on: no choice, missing/non-CA certificate, any private key, unexpected extra input.
#    Images that accept official bundles also record the FIXED official update source (GitHub Releases of iskaron/hifi-bridge,
#    DECISIONS #70). The source is transport only: a bundle is installed only if its signature matches the keyring.
#  * RAUC system.conf with the board's compatible string, U-Boot env access for fw_printenv/fw_setenv (libubootenv) and its complete
#    Linux-side default environment /etc/u-boot-initial-env,
#  * radio firmware pruned to the board's list (firmware/radio-<compatible>.txt),
#  * fstab (boot FAT root-owned, writable only through the group of the boot step; data partition), persistent paths, enabled units.
set -eu

TARGET_DIR="${1:?usage: post-build.sh TARGET_DIR}"
BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
EXT_DIR="$(cd "$BOARD_DIR/../.." && pwd)"
CONFIG="${BR2_CONFIG:?BR2_CONFIG not set}"
OPENSSL="openssl"
[ -n "${HOST_DIR:-}" ] && [ -x "${HOST_DIR}/bin/openssl" ] && OPENSSL="${HOST_DIR}/bin/openssl"

die() { echo "hifi-bridge post-build: ERROR: $*" >&2; exit 1; }

# value of a string/bool option in .config, with $(BR2_EXTERNAL_HIFI_BRIDGE_PATH) expanded
cfg() {
	sed -n "s/^$1=//p" "$CONFIG" | sed 's/^"//; s/"$//' | sed "s#\$(BR2_EXTERNAL_HIFI_BRIDGE_PATH)#$EXT_DIR#g"
}

# ---------------------------------------------------------------- update trust (P5)
mode=""
for m in OFFICIAL OWN OFFICIAL_AND_OWN; do
	[ "$(cfg BR2_PACKAGE_HIFI_BRIDGE_UPDATE_TRUST_$m)" = "y" ] && mode="$m"
done
[ -n "$mode" ] || die "update trust is not configured. Choose it explicitly (menuconfig: hifi-bridge -> Accepted update signatures); see docs/UPDATE_TRUST.md"

check_ca() {	# $1 role, $2 file
	[ -n "$2" ] || die "$1 update CA: no file configured"
	[ -f "$2" ] || die "$1 update CA: file not found: $2"
	if grep -q "PRIVATE KEY" "$2"; then die "$1 update CA: $2 contains a private key - only the public certificate may be built into an image"; fi
	n=$(grep -c "BEGIN CERTIFICATE" "$2")
	[ "$n" -eq 1 ] || die "$1 update CA: $2 must contain exactly one certificate (has $n)"
	"$OPENSSL" x509 -in "$2" -noout -ext basicConstraints 2>/dev/null | grep -q "CA:TRUE" || die "$1 update CA: $2 is not a CA certificate"
}

mkdir -p "$TARGET_DIR/etc/rauc" "$TARGET_DIR/etc/hifi-bridge"
keyring="$TARGET_DIR/etc/rauc/keyring.pem"
: > "$keyring"
json_entries=""
add_ca() {	# $1 role, $2 file
	check_ca "$1" "$2"
	cat "$2" >> "$keyring"
	# JSON string: escape backslashes first (RFC 2253 writes "\\," for a comma in a name), then quotes
	subj=$("$OPENSSL" x509 -in "$2" -noout -subject -nameopt RFC2253 | sed 's/^subject=//; s/\\/\\\\/g; s/"/\\"/g')
	fp=$("$OPENSSL" x509 -in "$2" -noout -fingerprint -sha256 | sed 's/^.*=//')
	[ -n "$json_entries" ] && json_entries="$json_entries,"
	json_entries="$json_entries
    {\"role\": \"$1\", \"subject\": \"$subj\", \"sha256\": \"$fp\"}"
}
case "$mode" in
	OFFICIAL) add_ca official "$(cfg BR2_PACKAGE_HIFI_BRIDGE_OFFICIAL_UPDATE_CA)" ;;
	OWN) add_ca own "$(cfg BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA)" ;;
	OFFICIAL_AND_OWN)
		add_ca official "$(cfg BR2_PACKAGE_HIFI_BRIDGE_OFFICIAL_UPDATE_CA)"
		add_ca own "$(cfg BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA)" ;;
esac
chmod 0644 "$keyring"
mode_lc=$(echo "$mode" | tr 'A-Z_' 'a-z-')
OFFICIAL_REPO="iskaron/hifi-bridge"	# fixed official update source (DECISIONS #70); not configurable on purpose
case "$mode" in
	OWN) official_source="null" ;;
	*) official_source="{\"type\": \"github-releases\", \"repository\": \"$OFFICIAL_REPO\",
    \"releases\": \"https://github.com/$OFFICIAL_REPO/releases\",
    \"api\": \"https://api.github.com/repos/$OFFICIAL_REPO/releases\"}" ;;
esac
cat > "$TARGET_DIR/etc/hifi-bridge/update-trust.json" <<EOF
{
  "format": "hifi-bridge-update-trust/1",
  "mode": "$mode_lc",
  "keyring": "/etc/rauc/keyring.pem",
  "official_source": $official_source,
  "certificates": [$json_entries
  ]
}
EOF

# ---------------------------------------------------------------- RAUC + U-Boot environment (P3, provisional)
compat="$(cfg BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE)"
[ -n "$compat" ] || die "BR2_PACKAGE_HIFI_BRIDGE_COMPATIBLE is empty"
sed "s/@COMPATIBLE@/$compat/" "$BOARD_DIR/rauc/system.conf.in" > "$TARGET_DIR/etc/rauc/system.conf"
# rpi_arm64 keeps its environment on the boot FAT (CONFIG_ENV_IS_IN_FAT), here in TWO copies (uboot.fragment, review N-6);
# fw_setenv must use both like U-Boot (size checked against the U-Boot build by the image check; on a board: gate item P3-1)
install -m 0644 "$BOARD_DIR/uboot/fw_env.config" "$TARGET_DIR/etc/fw_env.config"
# Linux-side default environment (DECISIONS #133, docs/OTA.md 4.1): libubootenv's fw_printenv/fw_setenv fall back to
# /etc/u-boot-initial-env when NEITHER copy is valid, and fw_setenv then stores it. It must be complete and bootable: U-Boot's own
# compiled-in default (installed there by BR2_TARGET_UBOOT_INITIAL_ENV) with the slot state of uboot/linux-default-env.txt instead of any
# slot variable (no hifi_env: boot.cmd then fails safe). Idempotent (an incremental build runs this on the already rewritten file).
if [ "$(cfg BR2_TARGET_UBOOT)" = y ]; then
	initial_env="$TARGET_DIR/etc/u-boot-initial-env"
	[ -s "$initial_env" ] || die "$initial_env (U-Boot's default environment) missing - BR2_TARGET_UBOOT_INITIAL_ENV=y?"
	slot_keys=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$BOARD_DIR/uboot/uboot-env.txt" "$BOARD_DIR/uboot/linux-default-env.txt" \
		| sort -u | paste -sd'|')
	{ grep -Ev "^(${slot_keys})=" "$initial_env" || true; grep -Ev '^(#|$)' "$BOARD_DIR/uboot/linux-default-env.txt"; } > "$initial_env.tmp"
	grep -q '^bootcmd=' "$initial_env.tmp" || die "$initial_env has no bootcmd - not U-Boot's default environment"
	mv "$initial_env.tmp" "$initial_env"
	chmod 0644 "$initial_env"
fi

# ---------------------------------------------------------------- radio firmware per board (DECISIONS #134, docs/IMAGE_LICENSING.md 5)
# Only the Wi-Fi/Bluetooth firmware this board's image can need: every other file below /lib/firmware/{brcm,cypress,synaptics} is
# deleted, every listed one must be present and unmodified (sha256 / link target). The image check enforces the same list.
if [ "$(cfg BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI)" = y ]; then
	fwlist="$BOARD_DIR/firmware/radio-$compat.txt"
	[ -f "$fwlist" ] || die "no radio firmware list for '$compat' ($fwlist)"
	fw="$TARGET_DIR/lib/firmware"
	removed=0
	for d in brcm cypress synaptics; do
		[ -d "$fw/$d" ] || continue
		for f in "$fw/$d"/* "$fw/$d"/.[!.]*; do
			[ -e "$f" ] || [ -L "$f" ] || continue
			if ! awk -v p="$d/${f##*/}" '!/^#/ && $1 == p { found = 1 } END { exit !found }' "$fwlist"; then
				rm -rf "$f"; removed=$((removed + 1))
			fi
		done
		rmdir "$fw/$d" 2>/dev/null || true
	done
	kept=0
	while read -r rel kind val lic; do
		case "$rel" in ""|"#"*) continue ;; esac
		p="$fw/$rel"
		case "$kind" in
			file) { [ -f "$p" ] && [ ! -L "$p" ]; } || die "radio firmware $rel ($lic) missing"
			      [ "$(sha256sum < "$p" | cut -d' ' -f1)" = "$val" ] \
			          || die "radio firmware $rel differs from $fwlist (modified, or a new package version: review the list)" ;;
			link) { [ -L "$p" ] && [ "$(readlink "$p")" = "$val" ]; } || die "radio firmware link $rel -> $val missing or different" ;;
			*) die "$fwlist: bad line '$rel $kind'" ;;
		esac
		kept=$((kept + 1))
	done < "$fwlist"
	echo "hifi-bridge post-build: radio firmware for $compat: $kept entries kept, $removed removed"
fi

# ---------------------------------------------------------------- build identity (development vs. release)
# Every image that is not an official release is a DEVELOPMENT build and is marked as such in os-release, build-info.json and the
# image file name (post-image.sh). "release" requires official-only trust (an own CA in a release image would be a mix-up).
variant="$(cfg BR2_PACKAGE_HIFI_BRIDGE_BUILD_VARIANT)"
[ -n "$variant" ] || variant="development"
case "$variant" in
	development) ;;
	release) [ "$mode" = "OFFICIAL" ] || die "build variant 'release' requires update trust 'official releases only' (have '$mode_lc')" ;;
	*) die "build variant must be 'development' or 'release', not '$variant'" ;;
esac
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$EXT_DIR/../../bridge/btbridge/__init__.py")"
[ -n "$version" ] || die "cannot read the bridge version"
br_version="$(sed -n 's/^VERSION=//p' "$TARGET_DIR/usr/lib/os-release" 2>/dev/null | tr -d '"')"
if [ "$variant" = development ]; then
	variant_name="Development build (not hardware-validated)"
	pretty="Hi-Fi Bridge $version (development build, not hardware-validated)"
else
	variant_name="Release"
	pretty="Hi-Fi Bridge $version"
fi
rm -f "$TARGET_DIR/usr/lib/os-release"
mkdir -p "$TARGET_DIR/usr/lib"
cat > "$TARGET_DIR/usr/lib/os-release" <<EOF
NAME="Hi-Fi Bridge"
ID=hifi-bridge
ID_LIKE=buildroot
VERSION="$version ($variant)"
VERSION_ID=$version
PRETTY_NAME="$pretty"
VARIANT="$variant_name"
VARIANT_ID=$variant
IMAGE_ID=$compat
IMAGE_VERSION=$version
BUILDROOT_VERSION=${br_version:-unknown}
HOME_URL="https://github.com/iskaron/hifi-bridge"
EOF
ln -sf ../usr/lib/os-release "$TARGET_DIR/etc/os-release"
hw=false
cat > "$TARGET_DIR/etc/hifi-bridge/build-info.json" <<EOF
{
  "format": "hifi-bridge-build/1",
  "variant": "$variant",
  "hardware_validated": $hw,
  "version": "$version",
  "compatible": "$compat",
  "buildroot": "${br_version:-unknown}",
  "update_trust": "$mode_lc",
  "source_date_epoch": ${SOURCE_DATE_EPOCH:-null}
}
EOF

# ---------------------------------------------------------------- mounts (P4) - uid/gid 990 = hifi-bridge (static, see hifi-bridge.mk)
fstab="$TARGET_DIR/etc/fstab"
touch "$fstab"
# one entry per mount point: an entry of an earlier (incremental) build with other options is replaced, not duplicated
add_fstab() {
	mp=$(echo "$1" | awk '{print $2}')
	grep -qxF "$1" "$fstab" && return 0
	awk -v mp="$mp" '$2 != mp' "$fstab" > "$fstab.tmp" && mv "$fstab.tmp" "$fstab"
	echo "$1" >> "$fstab"
}
# boot FAT (review N-14): owned by root (RAUC/fw_setenv write the U-Boot environment as root); group 991 = hifi-boot (hifi-bridge.mk,
# no members) may write it - only hifi-bridge-boot.service joins that group (SupplementaryGroups, drop-in in the overlay). Every other
# process of the hifi-bridge user, sandboxed or not, cannot change boot.scr, config.txt or the U-Boot environment. Others: no access
# (the provisioning file holds secrets until the boot step has scrubbed it).
add_fstab "/dev/mmcblk0p1 /boot vfat rw,noatime,flush,uid=0,gid=991,fmask=0117,dmask=0007 0 2"
add_fstab "/dev/mmcblk0p4 /data ext4 rw,noatime,commit=5,x-systemd.growfs 0 2"
add_fstab "/data/hifi-bridge /var/lib/hifi-bridge none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
add_fstab "/data/bluetooth /var/lib/bluetooth none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
# the clock survives a reboot without RTC (docs/NETWORK.md "Time")
add_fstab "/data/timesync /var/lib/systemd/timesync none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
mkdir -p "$TARGET_DIR/boot" "$TARGET_DIR/data" "$TARGET_DIR/var/lib/hifi-bridge" "$TARGET_DIR/var/lib/bluetooth" "$TARGET_DIR/var/lib/systemd/timesync"

# ---------------------------------------------------------------- enabled units
wants="$TARGET_DIR/etc/systemd/system/multi-user.target.wants"
mkdir -p "$wants"
for u in hifi-bridge-boot.service hifi-bridge.service hifi-firewall.service hifi-netd.service; do
	ln -sf "/usr/lib/systemd/system/$u" "$wants/$u"
done
for u in hifi-bridge-health.service hifi-bridge-reboot.path; do	# updates: confirm/roll back after boot, owner-confirmed restart
	ln -sf "/usr/lib/systemd/system/$u" "$wants/$u"
done
# updates (docs/OTA.md section 4): the slot is marked good ONLY by hifi-bridge-health.service after its checks. Upstream RAUC's
# rauc-mark-good.service would mark every boot good unconditionally and defeat the rollback, so it is masked (harmless if absent).
ln -sf /dev/null "$TARGET_DIR/etc/systemd/system/rauc-mark-good.service"
# Stock units that Buildroot's presets enable but the product does not use (found by the first real image check):
#  * wpa_supplicant.service (D-Bus mode, root): Wi-Fi is run only by hifi-netd through hifi-net-wpa@<if> (docs/NETWORK.md); a second
#    supplicant is attack surface and could fight over the interface. Masked, so D-Bus activation cannot start it either.
#  * avahi-dnsconfd.service: would take DNS servers announced over mDNS by any host on the LAN into the resolver configuration.
for u in wpa_supplicant.service avahi-dnsconfd.service; do
	rm -f "$TARGET_DIR/etc/systemd/system/multi-user.target.wants/$u"
	ln -sf /dev/null "$TARGET_DIR/etc/systemd/system/$u"
done
mkdir -p "$TARGET_DIR/etc/systemd/system/data.mount.wants" "$TARGET_DIR/etc/systemd/system/local-fs.target.wants"
ln -sf /usr/lib/systemd/system/hifi-bridge-growdata.service "$TARGET_DIR/etc/systemd/system/data.mount.wants/hifi-bridge-growdata.service"
ln -sf /usr/lib/systemd/system/hifi-bridge-data.service "$TARGET_DIR/etc/systemd/system/local-fs.target.wants/hifi-bridge-data.service"
echo "hifi-bridge post-build: $variant build $version, update trust '$mode_lc', compatible '$compat'"
