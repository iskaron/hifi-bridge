#!/usr/bin/env bash
# Update bundle round trip for a BUILT development image (hardware-free; nothing is installed):
#   1. `make ota` (scripts/os_bundle.sh) bundles the built rootfs.ext4 with the compatible and version READ FROM THE IMAGE, signed with
#      the temporary development signing key (scripts/os_dev_ca.sh) - the key the image's own keyring trusts;
#   2. `rauc info` with the image's OWN keyring and system.conf (read out of rootfs.ext4 with debugfs): must ACCEPT the bundle, and its
#      compatible must equal the image's;
#   3. the same rootfs signed by a throwaway foreign CA (created in the check container only): must be REFUSED.
# Installation, slot switch and rollback happen only on a device (`rauc install`): HARDWARE-PENDING (G2 P3-*).
#   scripts/os_check_ota.sh <board>          output: $OUT/ota/*.raucb (not committed), $OUT/hifi/check/ota-check.txt
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev
BOARD="${1:?usage: os_check_ota.sh <rpi3_64|rpi4_64|rpizero2w_64>}"
LAB="${HIFI_LAB:-$PWD/.lab}"
OUT="${HIFI_OS_OUT:-$LAB/br-out/$BOARD}"
CA="$LAB/dev-update-ca"
ROOTFS="$OUT/images/rootfs.ext2"                 # rootfs.ext4 is a symlink to it
[ -f "$ROOTFS" ] || { echo "os_check_ota: no build in $OUT" >&2; exit 2; }
[ -f "$CA/signing.key" ] || { echo "os_check_ota: no development CA (scripts/os_dev_ca.sh)" >&2; exit 2; }
mkdir -p "$OUT/hifi/check" "$OUT/ota"
REPORT="$OUT/hifi/check/ota-check.txt"
# compatible + version from the image itself (not from the working tree)
meta=$(docker run --rm --name "hifi-os-otameta-$BOARD" --network none -u "$(id -u):$(id -g)" -v "$ROOTFS":/rootfs.ext4:ro btbridge-os:dev \
	sh -c 'debugfs -R "cat /etc/rauc/system.conf" /rootfs.ext4 2>/dev/null | sed -n "s/^compatible=//p"; debugfs -R "cat /usr/lib/os-release" /rootfs.ext4 2>/dev/null | sed -n "s/^VERSION_ID=//p"')
COMPAT=$(echo "$meta" | sed -n 1p); VERSION=$(echo "$meta" | sed -n 2p)
[ -n "$COMPAT" ] && [ -n "$VERSION" ] || { echo "os_check_ota: cannot read compatible/version from the image" >&2; exit 2; }
{
echo "### make ota (compatible $COMPAT, version $VERSION, development signing key)"
make -s ota ROOTFS="$ROOTFS" COMPATIBLE="$COMPAT" VERSION="$VERSION" OUT="$OUT/ota" CERT="$CA/signing.pem" KEY="$CA/signing.key" || exit 1
} 2>&1 | tee "$REPORT"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "os_check_ota: make ota failed" >&2; exit 1; }
BUNDLE="$OUT/ota/hifi-bridge-$COMPAT-$VERSION.raucb"
docker run --rm --name "hifi-os-otacheck-$BOARD" --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -e COMPAT="$COMPAT" \
	-v "$ROOTFS":/rootfs.ext4:ro -v "$BUNDLE":/bundle.raucb:ro btbridge-os:dev bash -c '
set -uo pipefail
W=/tmp/w; mkdir -p $W/foreign; cd $W
for f in etc/rauc/system.conf etc/rauc/keyring.pem; do debugfs -R "dump /$f $W/$(basename $f)" /rootfs.ext4 >/dev/null 2>&1; done
# the image s own system.conf, made usable on this host: noop bootloader, local keyring and data paths
sed -e "s#^bootloader=.*#bootloader=noop#" -e "s#^path=.*#path=$W/keyring.pem#" -e "s#^data-directory=.*#data-directory=$W/data#" system.conf > host.conf
rc=0
echo "### rauc info with the image s keyring ($(grep -c "BEGIN CERTIFICATE" keyring.pem) certificate(s): $(openssl x509 -in keyring.pem -noout -subject | sed "s/^subject=//"))"
if rauc --conf=host.conf info --output-format=json /bundle.raucb > info.json 2> info.err; then
	bc=$(python3 -c "import json;print(json.load(open(\"info.json\"))[\"compatible\"])")
	echo "PASS accepted by the image keyring (bundle compatible $bc)"
	[ "$bc" = "$COMPAT" ] && echo "PASS bundle compatible = image compatible ($COMPAT)" || { echo "FAIL compatible $bc != $COMPAT"; rc=1; }
	python3 -c "import json;i=json.load(open(\"info.json\"));print(\"     version\", i[\"version\"], \"| image sha256\", i[\"images\"][0][\"rootfs\"][\"checksum\"])"
else
	echo "FAIL refused by the image keyring:"; cat info.err; rc=1
fi
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout foreign/ca.key -out foreign/ca.pem -days 2 -subj "/CN=foreign CA" \
	-addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign 2>/dev/null
printf "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n" > foreign/ext
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout foreign/s.key -out foreign/s.csr -subj "/CN=foreign signer" 2>/dev/null
openssl x509 -req -in foreign/s.csr -CA foreign/ca.pem -CAkey foreign/ca.key -CAcreateserial -out foreign/s.pem -days 2 -extfile foreign/ext 2>/dev/null
mkdir in; cp /rootfs.ext4 in/rootfs.ext4
printf "[update]\ncompatible=%s\nversion=0\n\n[bundle]\nformat=verity\n\n[image.rootfs]\nfilename=rootfs.ext4\n" "$COMPAT" > in/manifest.raucm
sed "s#^path=.*#path=$W/foreign/ca.pem#" host.conf > foreign.conf
rauc --conf=foreign.conf bundle --cert=foreign/s.pem --key=foreign/s.key in foreign.raucb >/dev/null 2>&1
if rauc --conf=host.conf info foreign.raucb >/dev/null 2>&1; then echo "FAIL a foreign-signed bundle is accepted"; rc=1; else echo "PASS a foreign-signed bundle is refused"; fi
echo "bundle size: $(stat -c %s /bundle.raucb) bytes"
exit $rc' 2>&1 | tee -a "$REPORT"
rc=${PIPESTATUS[0]}
echo "os_check_ota: $BOARD exit code $rc"
exit "$rc"
