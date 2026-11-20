#!/usr/bin/env bash
# PREPARATION of the source provision of a later public release (DECISIONS #125, OPEN_QUESTIONS #75, docs/IMAGE_LICENSING.md section 6).
# Assembles, for ONE built board, what a release's source bundle needs, under the git-ignored lab directory. NEVER published by this
# script; nothing leaves the host. Run after scripts/os_build.sh <board> (incl. `legal`).
#
#   scripts/os_source_bundle.sh <board> [--no-overlay-source]
#
# Contents of $HIFI_LAB/source-bundle/<board>-<commit12>/:
#   legal-info/                     Buildroot `make legal-info` of the build (hard links: no extra disk space): sources, patches, licence
#                                   texts, manifest.csv, host-*, buildroot.config, legal-info.sha256
#   buildroot-<commit>.tar.gz       the Buildroot source itself at the pinned commit (legal-info does not save it; GPL-2.0+)
#   hifi-bridge-<commit>.tar.gz     this repository at the bundled commit (`git archive`: tracked files only - never secrets/, .lab/)
#   linux-overlays-<commit>.tar.gz  raspberrypi/linux at the commit the PREBUILT device-tree overlays of rpi-firmware were built from
#                                   (extra/git_hash; NOT our kernel commit, whose source is in legal-info/sources/linux-custom)
#                                   [--no-overlay-source: only a pointer]
#   toolchain/                      Bootlin toolchain metadata (README.txt, summary.csv, buildroot.config from the toolchain tarball), the
#                                   runtime files the image takes from it, licence texts for them (LGPL-2.1, GPL-3.0, GCC Runtime Library
#                                   Exception 3.1 - copied from Debian's common licences / libgcc-s1 copyright in btbridge-os) and the
#                                   pointers to Bootlin's own licences/ and sources/
#   MANIFEST.json, SHA256SUMS       commits, versions, URLs and the sha256 of every file outside legal-info/ (legal-info is covered by its
#                                   own legal-info.sha256, whose hash is in the manifest)
# Host side: read-only `git archive`/`git rev-parse` only. Everything else in Docker (btbridge-os). Refuses a dirty working tree (the bundle
# must correspond to a commit) unless HIFI_SOURCE_BUNDLE_DIRTY=1 (then marked "dirty" in the manifest; never for a release).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
BOARD="${1:?usage: os_source_bundle.sh <rpi3_64|rpi4_64|rpizero2w_64> [--no-overlay-source]}"
case "$BOARD" in rpi3_64|rpi4_64|rpizero2w_64) ;; *) echo "os_source_bundle: unknown board $BOARD" >&2; exit 2 ;; esac
OVERLAY_SOURCE=1
[ "${2:-}" = "--no-overlay-source" ] && OVERLAY_SOURCE=0
LAB="${HIFI_LAB:-$REPO/.lab}"
OUT="${HIFI_OS_OUT:-$LAB/br-out/$BOARD}"
PINNED=679b9ead7620bbf193620d1ebf56f53c1764d37a
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_require
hb_ensure_images btbridge-os:dev

[ -f "$OUT/legal-info/manifest.csv" ] || { echo "os_source_bundle: no legal-info in $OUT (run scripts/os_build.sh $BOARD build legal)" >&2; exit 2; }
br_commit=$(git -C "$LAB/buildroot" rev-parse HEAD)
[ "$br_commit" = "$PINNED" ] || { echo "os_source_bundle: $LAB/buildroot is at $br_commit, not the pinned $PINNED" >&2; exit 2; }
commit=$(git rev-parse HEAD)
dirty=false
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
	[ "${HIFI_SOURCE_BUNDLE_DIRTY:-0}" = 1 ] || { echo "os_source_bundle: uncommitted changes - commit first (or HIFI_SOURCE_BUNDLE_DIRTY=1 for a trial)" >&2; exit 2; }
	dirty=true
fi
free_gb=$(df --output=avail -BG "$LAB" | tail -1 | tr -dc 0-9)
[ "$free_gb" -ge 30 ] || { echo "os_source_bundle: only ${free_gb} GB free in $LAB (keep >= 30 GB)" >&2; exit 3; }
DEST="$LAB/source-bundle/$BOARD-${commit:0:12}"
rm -rf "$DEST"
mkdir -p "$DEST/toolchain"
echo "os_source_bundle: $BOARD at ${commit:0:12} (dirty=$dirty) -> $DEST"
# read-only git: tracked files of the commit only (secrets/ and .lab/ are not tracked)
git archive --format=tar.gz --prefix="hifi-bridge-${commit:0:12}/" -o "$DEST/hifi-bridge-$commit.tar.gz" "$commit"
git -C "$LAB/buildroot" archive --format=tar.gz --prefix="buildroot-$br_commit/" -o "$DEST/buildroot-$br_commit.tar.gz" "$br_commit"

name="hifi-source-bundle-$BOARD"
docker rm -f "$name" >/dev/null 2>&1 || true
docker run --rm --name "$name" --cpus=2 --memory=2g -u "$(id -u):$(id -g)" -e HOME=/tmp \
	-e BOARD="$BOARD" -e COMMIT="$commit" -e BR_COMMIT="$br_commit" -e DIRTY="$dirty" -e OVERLAY_SOURCE="$OVERLAY_SOURCE" \
	-e DEST="/lab/${DEST#"$LAB"/}" -e OUT="/lab/${OUT#"$LAB"/}" \
	-v "$LAB":/lab -v "$REPO":/work:ro btbridge-os:dev bash -c '
set -euo pipefail
cd "$DEST"
cp -al "$OUT/legal-info" legal-info
# Bootlin toolchain: metadata from the tarball, the runtime files the image takes from it, licence texts and source pointers
tc=$(ls /lab/br-dl/toolchain-external-bootlin/*.tar.xz | head -1)
tcdir=$(basename "$tc" .tar.xz)
tar xJf "$tc" -C toolchain --strip-components=1 "$tcdir/README.txt" "$tcdir/summary.csv" "$tcdir/buildroot.config"
# runtime files the image takes from the toolchain: in the target, named like a file of the toolchain sysroot, owned by no package
# (Buildroot does not record the external toolchain in packages-file-list.txt)
tar tJf "$tc" | sed -n "s#^$tcdir/aarch64-buildroot-linux-gnu/sysroot/\(lib\|lib64\|usr/lib\)/\([^/]*\)\$#\2#p" | sort -u > /tmp/tc-names
cut -d, -f2- "$OUT/build/packages-file-list.txt" | sed "s#^\./##" | sort -u > /tmp/owned
(cd "$OUT/target" && find usr/lib -maxdepth 1 \( -type f -o -type l \)) | sort | while read -r p; do
	grep -qxF "${p##*/}" /tmp/tc-names && ! grep -qxF "$p" /tmp/owned && echo "/$p"
done > toolchain/files-in-image.txt || true
[ -s toolchain/files-in-image.txt ] || { echo "no toolchain runtime files found in the target"; exit 1; }
cp /usr/share/common-licenses/LGPL-2.1 toolchain/LGPL-2.1.txt
cp /usr/share/common-licenses/GPL-3 toolchain/GPL-3.0.txt
awk "/^GCC RUNTIME LIBRARY EXCEPTION/{f=1} f{print} f && /requirements of the license of GCC\\./{exit}" /usr/share/doc/libgcc-s1/copyright \
	> toolchain/GCC-RUNTIME-LIBRARY-EXCEPTION-3.1.txt
grep -q "No Weakening of GCC Copyleft" toolchain/GCC-RUNTIME-LIBRARY-EXCEPTION-3.1.txt || { echo "GCC exception text not found"; exit 1; }
cat > toolchain/SOURCES.txt <<EOF
Toolchain: $tcdir (Bootlin, https://toolchains.bootlin.com), tarball $(basename "$tc") sha256 $(sha256sum < "$tc" | cut -d" " -f1)
The image contains the toolchain runtime files listed in files-in-image.txt (glibc: LGPL-2.1+; libgcc_s/libstdc++/libatomic: GPL-3.0+
with the GCC Runtime Library Exception 3.1). Versions of every toolchain component: summary.csv.
Corresponding source and licence texts as published by Bootlin (to be mirrored for a release; NOT downloaded by this script):
  https://toolchains.bootlin.com/downloads/releases/sources/
  https://toolchains.bootlin.com/downloads/releases/licenses/
The licence texts in this directory were copied from Debian (btbridge-os: /usr/share/common-licenses, /usr/share/doc/libgcc-s1/copyright).
EOF
# source of the PREBUILT device-tree overlays (rpi-firmware extra/git_hash), not our kernel commit
ov=$(cat "$OUT"/build/rpi-firmware-*/extra/git_hash)
kc=$(sed -n "s/.*github,raspberrypi,linux,\([0-9a-f]*\)).*/\1/p" /work/os/br2-external/configs/hifi_bridge_${BOARD}_defconfig)
ov_file=""
if [ "$OVERLAY_SOURCE" = 1 ]; then
	ov_file="linux-overlays-$ov.tar.gz"
	wget -q -O "$ov_file" "https://github.com/raspberrypi/linux/archive/$ov.tar.gz"
fi
python3 - "$ov" "$kc" "$ov_file" "$(basename "$tc")" <<"PY"
import hashlib, json, os, sys, datetime
ov, kc, ov_file, tc = sys.argv[1:]
commit, brc = os.environ["COMMIT"], os.environ["BR_COMMIT"]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
files = {}
for dp, dns, fns in os.walk("."):
    if dp == "./legal-info" or dp.startswith("./legal-info/"):
        continue
    for fn in fns:
        p = os.path.relpath(os.path.join(dp, fn), ".")
        if p not in ("MANIFEST.json", "SHA256SUMS"):
            files[p] = sha(p)
li = sum(len(f) for _, _, f in os.walk("legal-info"))
warn = [ln.strip() for ln in open("legal-info/README", errors="replace") if "WARNING" in ln]
m = {"format": "hifi-bridge-source-bundle/1", "published": False,
     "note": "internal preparation for a later public release (OPEN_QUESTIONS #75); not a release, not legal advice",
     "board": os.environ["BOARD"], "created_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
     "repository": {"commit": os.environ["COMMIT"], "dirty": os.environ["DIRTY"] == "true", "archive": f"hifi-bridge-{commit}.tar.gz"},
     "buildroot": {"commit": brc, "archive": f"buildroot-{brc}.tar.gz"},
     "kernel": {"repository": "https://github.com/raspberrypi/linux", "commit": kc, "source": "legal-info/sources/linux-custom"},
     "dt_overlays": {"repository": "https://github.com/raspberrypi/linux", "commit": ov,
                     "source": ov_file or f"NOT INCLUDED (--no-overlay-source): https://github.com/raspberrypi/linux/archive/{ov}.tar.gz",
                     "why": "the 358 prebuilt overlays/*.dtbo of rpi-firmware were compiled from this commit, not from our kernel"},
     "toolchain": {"tarball": tc, "pointers": ["https://toolchains.bootlin.com/downloads/releases/sources/",
                                               "https://toolchains.bootlin.com/downloads/releases/licenses/"]},
     "legal_info": {"files": li, "legal-info.sha256": sha("legal-info/legal-info.sha256"), "buildroot_warnings": warn},
     "files": files}
json.dump(m, open("MANIFEST.json", "w"), indent=1)
with open("SHA256SUMS", "w") as f:
    for p, h in sorted(files.items()):
        f.write(f"{h}  {p}\n")
    f.write(sha("legal-info/legal-info.sha256") + "  legal-info/legal-info.sha256\n")
print(f"bundle: {len(files)} files + legal-info ({li} files); overlays {ov[:8]} vs kernel {kc[:8]}")
PY
'
du -sh --apparent-size "$DEST" | sed 's/^/os_source_bundle: apparent size /'
echo "os_source_bundle: $DEST/MANIFEST.json (NOT published)"
