#!/bin/sh
# Hi-Fi Bridge post-build step (Buildroot calls it with TARGET_DIR as $1 and BR2_CONFIG in the environment).
# PHASE-2B SKELETON: exercised by tests/os with a fake target tree; not yet run inside a real Buildroot build.
#
#  * update trust (P5, docs/UPDATE_TRUST.md): builds /etc/rauc/keyring.pem from the EXPLICIT choice in the configuration and records it in
#    /etc/hifi-bridge/update-trust.json. Fails on: no choice, missing/non-CA certificate, any private key, unexpected extra input.
#    Images that accept official bundles also record the FIXED official update source (GitHub Releases of iskaron/hifi-bridge,
#    DECISIONS #70). The source is transport only: a bundle is installed only if its signature matches the keyring.
#  * RAUC system.conf with the board's compatible string, U-Boot env access for fw_printenv/fw_setenv,
#  * fstab (boot FAT writable for the hifi-bridge user only; data partition), persistent paths, enabled units.
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
	subj=$("$OPENSSL" x509 -in "$2" -noout -subject -nameopt RFC2253 | sed 's/^subject=//; s/"/\\"/g')
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
# rpi_arm64 keeps its environment in the file uboot.env on the boot FAT (CONFIG_ENV_IS_IN_FAT; size to be verified, gate item P3-1)
echo "/boot/uboot.env 0x0000 0x4000" > "$TARGET_DIR/etc/fw_env.config"

# ---------------------------------------------------------------- mounts (P4) - uid/gid 990 = hifi-bridge (static, see hifi-bridge.mk)
fstab="$TARGET_DIR/etc/fstab"
touch "$fstab"
add_fstab() { grep -qF "$1" "$fstab" || echo "$1" >> "$fstab"; }
add_fstab "/dev/mmcblk0p1 /boot vfat rw,noatime,flush,uid=990,gid=990,fmask=0177,dmask=0077 0 2"
add_fstab "/dev/mmcblk0p4 /data ext4 rw,noatime,commit=5,x-systemd.growfs 0 2"
add_fstab "/data/hifi-bridge /var/lib/hifi-bridge none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
add_fstab "/data/bluetooth /var/lib/bluetooth none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
# the clock survives a reboot without RTC (docs/NETWORK.md "Time")
add_fstab "/data/timesync /var/lib/systemd/timesync none bind,x-systemd.requires=hifi-bridge-data.service 0 0"
mkdir -p "$TARGET_DIR/boot" "$TARGET_DIR/data" "$TARGET_DIR/var/lib/hifi-bridge" "$TARGET_DIR/var/lib/bluetooth" "$TARGET_DIR/var/lib/systemd/timesync"

# ---------------------------------------------------------------- enabled units
wants="$TARGET_DIR/etc/systemd/system/multi-user.target.wants"
mkdir -p "$wants"
for u in hifi-bridge-boot.service hifi-bridge.service hifi-firewall.service hifi-netd.service hifi-bridge-health.service \
	hifi-bridge-reboot.path; do
	ln -sf "/usr/lib/systemd/system/$u" "$wants/$u"
done
# updates (docs/OTA.md section 4): the slot is marked good ONLY by hifi-bridge-health.service after its checks. Upstream RAUC's
# rauc-mark-good.service would mark every boot good unconditionally and defeat the rollback, so it is masked (harmless if absent).
ln -sf /dev/null "$TARGET_DIR/etc/systemd/system/rauc-mark-good.service"
mkdir -p "$TARGET_DIR/etc/systemd/system/data.mount.wants" "$TARGET_DIR/etc/systemd/system/local-fs.target.wants"
ln -sf /usr/lib/systemd/system/hifi-bridge-growdata.service "$TARGET_DIR/etc/systemd/system/data.mount.wants/hifi-bridge-growdata.service"
ln -sf /usr/lib/systemd/system/hifi-bridge-data.service "$TARGET_DIR/etc/systemd/system/local-fs.target.wants/hifi-bridge-data.service"
echo "hifi-bridge post-build: update trust '$mode_lc', compatible '$compat'"
