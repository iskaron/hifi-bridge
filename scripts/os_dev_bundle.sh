#!/usr/bin/env bash
# DEVELOPMENT RAUC bundle round trip for a built image (hardware-free; nothing is installed). In Docker (btbridge-os, Debian RAUC):
#   1. bundle the built rootfs.ext4 (format verity, compatible + version from the image itself), signed with the temporary development
#      signing key (scripts/os_dev_ca.sh; mounted read-only into THIS container only, never into a build container);
#   2. `rauc info` with the image's OWN keyring and system.conf (read out of the card image with debugfs): must ACCEPT;
#   3. the same content signed by a throwaway foreign CA: must be REFUSED by the image's keyring.
# The compatible check and the installation itself happen only on a device (`rauc install`): HARDWARE-PENDING (G2 P3-*).
# Output: $OUT/images/hifi-bridge-<compatible>-<version>-development.raucb (not committed) and $OUT/hifi/check/dev-bundle.txt.
#   scripts/os_dev_bundle.sh <board>
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
BOARD="${1:?usage: os_dev_bundle.sh <rpi3_64|rpi4_64|rpizero2w_64>}"
LAB="${HIFI_LAB:-$PWD/.lab}"
OUT="${HIFI_OS_OUT:-$LAB/br-out/$BOARD}"
CA="$LAB/dev-update-ca"
[ -f "$OUT/images/rootfs.ext4" ] || { echo "os_dev_bundle: no build in $OUT" >&2; exit 2; }
[ -f "$CA/signing.key" ] || { echo "os_dev_bundle: no development CA (scripts/os_dev_ca.sh)" >&2; exit 2; }
mkdir -p "$OUT/hifi/check"
docker run --rm --name "hifi-os-bundle-$BOARD" --network none -u "$(id -u):$(id -g)" -e HOME=/tmp \
	-v "$OUT":/out -v "$CA/signing.pem":/ca/signing.pem:ro -v "$CA/signing.key":/ca/signing.key:ro btbridge-os:dev bash -c '
set -uo pipefail
W=/tmp/b; mkdir -p $W/in $W/foreign; cd $W
img=$(ls -t /out/images/hifi-bridge-*.img | head -1)
for f in etc/rauc/system.conf etc/rauc/keyring.pem usr/lib/os-release; do
	debugfs -R "dump /$f $W/$(basename $f)" /out/images/rootfs.ext4 >/dev/null 2>&1 || { echo "cannot read /$f from the image"; exit 2; }
done
compat=$(sed -n "s/^compatible=//p" system.conf); version=$(sed -n "s/^VERSION_ID=//p" os-release)
variant=$(sed -n "s/^VARIANT_ID=//p" os-release)
cp /out/images/rootfs.ext4 in/rootfs.ext4
printf "[update]\ncompatible=%s\nversion=%s\ndescription=Hi-Fi Bridge %s build (not hardware-validated)\n\n[bundle]\nformat=verity\n\n[image.rootfs]\nfilename=rootfs.ext4\n" \
	"$compat" "$version" "$variant" > in/manifest.raucm
# a system.conf usable on this host: the image s own, with the noop bootloader and local paths
sed -e "s#^bootloader=.*#bootloader=noop#" -e "s#^path=.*#path=$W/keyring.pem#" -e "s#^data-directory=.*#data-directory=$W/data#" system.conf > host.conf
out=/out/images/hifi-bridge-$compat-$version-$variant.raucb
rm -f $out
rauc --conf=host.conf bundle --cert=/ca/signing.pem --key=/ca/signing.key in $out >/dev/null || { echo "FAIL bundle creation"; exit 1; }
rc=0
if rauc --conf=host.conf info $out > info.txt 2>&1; then echo "PASS image keyring accepts the development bundle"; else echo "FAIL image keyring refuses the development bundle"; cat info.txt; rc=1; fi
# a foreign CA (throwaway, this container only) must be refused
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout foreign/ca.key -out foreign/ca.pem -days 2 -subj "/CN=foreign CA" \
	-addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign 2>/dev/null
printf "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n" > foreign/ext
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout foreign/s.key -out foreign/s.csr -subj "/CN=foreign signer" 2>/dev/null
openssl x509 -req -in foreign/s.csr -CA foreign/ca.pem -CAkey foreign/ca.key -CAcreateserial -out foreign/s.pem -days 2 -extfile foreign/ext 2>/dev/null
sed "s#^path=.*#path=$W/foreign/ca.pem#" host.conf > foreign.conf
rauc --conf=foreign.conf bundle --cert=foreign/s.pem --key=foreign/s.key in foreign.raucb >/dev/null 2>&1
if rauc --conf=host.conf info foreign.raucb >/dev/null 2>&1; then echo "FAIL image keyring accepts a foreign-signed bundle"; rc=1; else echo "PASS image keyring refuses a foreign-signed bundle"; fi
echo "bundle: $(basename $out) $(stat -c %s $out) bytes, compatible=$compat version=$version"
grep -E "^(Compatible|Version|Bundle Format|Verified|Signing|  )" info.txt | head -12
exit $rc' 2>&1 | tee "$OUT/hifi/check/dev-bundle.txt"
rc=${PIPESTATUS[0]}
echo "os_dev_bundle: $BOARD exit code $rc"
exit "$rc"
