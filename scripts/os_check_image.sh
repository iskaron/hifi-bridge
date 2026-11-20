#!/usr/bin/env bash
# All static checks of a BUILT development image (after scripts/os_build.sh), in Docker. Hardware-free: nothing is booted, no host block
# device or loop mount is used. The card image is taken apart inside the container (sfdisk/dd, mtools, debugfs rdump as container root so
# owners and modes are the image's; the extracted trees stay in the container and disappear with it).
#
#   scripts/os_check_image.sh <board>       board: rpi3_64 | rpi4_64 | rpizero2w_64
#
# Checks (os/tools/image_check.py): identity (development variant), MBR layout and slot sizes, manifest + provisioning slot, boot FAT
# (firmware, U-Boot, config.txt), kernel in the slot, read-only root (fstab, bootargs, /var overlay), data partition + bind mounts, RAUC
# (system.conf, keyring = public dev CA only, fw_env vs. U-Boot's CONFIG_ENV_SIZE), both U-Boot environment copies (content, serials,
# defaults, torn-copy fallback with this build's fw_printenv/fw_setenv, boot.scr fail-safe), root-owned boot FAT and its only writer,
# users locked, no remote login, setuid list, bridge + bytecode, enabled units, `systemd-analyze verify --root` and offline `security` of
# our units, network/firewall/resolved/firmware facts, sizes.
# Then the secret scan (os/tools/secret_scan.py) over the rootfs tree, the boot tree and the RAW card image, and a file manifest of the
# rootfs (os/tools/tree_manifest.py) for reproducibility comparisons.
#
# Results: $OUT/hifi/check/ (image-check.{json,md}, secret-scan.json, rootfs-manifest.tsv, boot-manifest.tsv). Copied as evidence (with
# the build's SBOM, licence report and legal-info manifest.csv) to
# reports/evidence/os_image_<board>.md and reports/evidence/os_secret_scan_<board>.json. Exit code: non-zero if any check fails.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev
BOARD="${1:?usage: os_check_image.sh <rpi3_64|rpi4_64|rpizero2w_64>}"
LAB="${HIFI_LAB:-$REPO/.lab}"
OUT="${HIFI_OS_OUT:-$LAB/br-out/$BOARD}"
[ -d "$OUT/images" ] || { echo "os_check_image: no build in $OUT (run scripts/os_build.sh $BOARD)" >&2; exit 2; }
mkdir -p "$OUT/hifi/check"
name="hifi-os-check-$BOARD"
docker rm -f "$name" >/dev/null 2>&1 || true
docker run --rm --name "$name" --network none --cpus=2 --memory=4g -e BOARD="$BOARD" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
	-v "$REPO":/work:ro -v "$OUT":/out -v "$LAB/dev-update-ca/ca.pem":/devca/ca.pem:ro btbridge-os:dev bash -c '
set -uo pipefail
O=/out; C=$O/hifi/check; W=/tmp/chk; T=/work/os/tools
rm -rf $C/*; mkdir -p $W/rootfs $W/boot
img=$(ls -t $O/images/hifi-bridge-*.img 2>/dev/null | head -1)
[ -n "$img" ] || { echo "no card image in $O/images"; exit 2; }
echo "image: $(basename $img) ($(stat -c %s $img) bytes)"
python3 - "$img" "$W" <<PY
import json, subprocess, sys
img, w = sys.argv[1:]
pt = json.loads(subprocess.run(["sfdisk", "--json", img], capture_output=True, text=True, check=True).stdout)["partitiontable"]
with open(img, "rb") as fh:
    for i, p in enumerate(pt["partitions"], 1):
        fh.seek(p["start"] * 512)
        left = p["size"] * 512
        with open(f"{w}/p{i}", "wb") as out:
            while left:
                b = fh.read(min(left, 1 << 22)); out.write(b); left -= len(b)
PY
mcopy -s -n -i $W/p1 "::*" $W/boot/ || exit 3
debugfs -R "rdump / $W/rootfs" $W/p2 2>&1 | grep -v "^debugfs " | head -5
# slot B must be empty on a fresh card (boot.cmd never tries it, RAUC fills it)
if [ -n "$(tr -d "\0" < $W/p3 | head -c 1)" ]; then echo "slot B: NOT EMPTY"; slotb=1; else echo "slot B: all zero"; slotb=0; fi
dumpimage -T script -p 0 -o $W/boot.cmd $W/boot/boot.scr >/dev/null
bootargs=$(sed -n "s/^setenv bootargs \"\(.*\)\"$/\1/p" $W/boot.cmd | tail -1)
ucfg=$(ls -d $O/build/uboot-*/.config 2>/dev/null | grep -v tools | head -1)
python3 $T/image_check.py --board $BOARD --image $img --manifest $img.manifest.json --rootfs $W/rootfs --boot $W/boot \
	--config $O/.config --rootfs-img $O/images/rootfs.ext4 --slot-a $W/p2 --dev-ca /devca/ca.pem \
	--file-list $O/build/packages-file-list.txt --uboot-config "$ucfg" --bootargs "$bootargs" \
	--fw-printenv $O/host/bin/fw_printenv --boot-script $W/boot.cmd \
	--json $C/image-check.json --md $C/image-check.md
rc=$?
[ $slotb -eq 0 ] || rc=1
echo "--- secret scan"
python3 $T/secret_scan.py --tree $W/rootfs --tree $W/boot --raw $img --json $C/secret-scan.json; s=$?
python3 $T/tree_manifest.py make $W/rootfs $C/rootfs-manifest.tsv
python3 $T/tree_manifest.py make $W/boot $C/boot-manifest.tsv
sha256sum $img $O/images/rootfs.ext4 $W/p1 $W/p4 | sed "s#$W/##; s#$O/##" > $C/hashes.txt
cp $W/boot.cmd $C/boot.cmd.txt
chown -R $HOST_UID:$HOST_GID $C
echo "image-check rc=$rc secret-scan rc=$s"
[ $rc -eq 0 ] && [ $s -eq 0 ]'
rc=$?
mkdir -p reports/evidence
[ -f "$OUT/hifi/check/image-check.md" ] && cp "$OUT/hifi/check/image-check.md" "reports/evidence/os_image_$BOARD.md"
[ -f "$OUT/hifi/check/secret-scan.json" ] && cp "$OUT/hifi/check/secret-scan.json" "reports/evidence/os_secret_scan_$BOARD.json"
# SBOM, licence policy report and the legal-info manifest of this build (small; legal-info itself is not committed)
[ -f "$OUT/hifi/sbom.cdx.json" ] && cp "$OUT/hifi/sbom.cdx.json" "reports/evidence/os_sbom_$BOARD.cdx.json"
[ -f "$OUT/hifi/licences.md" ] && cp "$OUT/hifi/licences.md" "reports/evidence/os_licenses_$BOARD.md"
[ -f "$OUT/legal-info/manifest.csv" ] && cp "$OUT/legal-info/manifest.csv" "reports/evidence/os_legal_manifest_$BOARD.csv"
echo "os_check_image: $BOARD exit code $rc"
exit $rc
