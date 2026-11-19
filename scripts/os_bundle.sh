#!/usr/bin/env bash
# Reproducible RAUC update bundle (`make ota`, docs/OTA.md section 8): rootfs image -> signed verity bundle
#   hifi-bridge-<compatible>-<version>.raucb   (the asset name the update check looks for on GitHub Releases)
# Runs the host RAUC tool inside the btbridge-os image; nothing is installed on the host.
#
#   scripts/os_bundle.sh --rootfs output/images/rootfs.ext4 --compatible hifi-bridge-rpi3 [--version 0.4.0] [--out DIR]
#                        [--cert signing.pem --key signing.key]
#
# Signing: with --cert/--key the given certificate signs (it needs the codeSigning purpose, check-purpose=codesign). Without them a
# DEVELOPMENT CA and signing key are created once in .keys/dev-update/ (git-ignored) and used; configure .keys/dev-update/ca.pem as the
# own update CA of a self-built image (BR2_PACKAGE_HIFI_BRIDGE_OWN_UPDATE_CA). RELEASE keys never come near this script: an official
# bundle is made by re-signing offline (`rauc resign`, docs/UPDATE_TRUST.md section 4).
#
# Reproducibility: file times and the squashfs are pinned with SOURCE_DATE_EPOCH (default: the last commit time), so two runs over the
# same rootfs give the same image checksum and a byte-identical squashfs image (both printed; checked in tests/ota/test_rauc_real.py).
# Not reproducible by design of RAUC: the dm-verity salt (random per bundle, so the hash tree and the signed manifest's hash differ)
# and the CMS signature's signing time.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"

if [ "${1:-}" = "--inside" ]; then
	shift
	ROOTFS="" COMPAT="" VERSION="" OUT="" CERT="" KEY="" KEYDIR=""
	while [ $# -gt 0 ]; do
		case "$1" in
			--rootfs) ROOTFS=$2; shift 2 ;; --compatible) COMPAT=$2; shift 2 ;; --version) VERSION=$2; shift 2 ;;
			--out) OUT=$2; shift 2 ;; --cert) CERT=$2; shift 2 ;; --key) KEY=$2; shift 2 ;; --dev-keys) KEYDIR=$2; shift 2 ;;
			*) echo "unknown argument: $1" >&2; exit 2 ;;
		esac
	done
	if [ -z "$CERT" ]; then
		CERT="$KEYDIR/signing.pem" KEY="$KEYDIR/signing.key"
		if [ ! -f "$CERT" ]; then
			echo "os_bundle: creating DEVELOPMENT update keys in .keys/dev-update/ (never use them for releases)"
			mkdir -p "$KEYDIR"; chmod 700 "$KEYDIR"
			openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$KEYDIR/ca.key" -out "$KEYDIR/ca.pem" -days 3650 \
				-subj "/CN=Hi-Fi Bridge DEVELOPMENT update CA" -addext "basicConstraints=critical,CA:TRUE" \
				-addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
			printf 'basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=codeSigning\n' > "$KEYDIR/signing.ext"
			openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout "$KEY" -out "$KEYDIR/signing.csr" \
				-subj "/CN=Hi-Fi Bridge DEVELOPMENT update signing" 2>/dev/null
			openssl x509 -req -in "$KEYDIR/signing.csr" -CA "$KEYDIR/ca.pem" -CAkey "$KEYDIR/ca.key" -CAcreateserial -out "$CERT" \
				-days 825 -extfile "$KEYDIR/signing.ext" 2>/dev/null
			chmod 600 "$KEYDIR"/*.key
		fi
		KEYRING="$KEYDIR/ca.pem"
	else
		KEYRING=""
	fi
	openssl x509 -in "$CERT" -noout -ext extendedKeyUsage 2>/dev/null | grep -q "Code Signing" \
		|| { echo "os_bundle: $CERT has no codeSigning purpose - RAUC (check-purpose=codesign) would refuse the bundle" >&2; exit 3; }
	work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
	mkdir "$work/content"
	cp "$ROOTFS" "$work/content/rootfs.ext4"
	cat > "$work/content/manifest.raucm" <<EOF
[update]
compatible=$COMPAT
version=$VERSION
description=Hi-Fi Bridge $VERSION
build=$SOURCE_DATE_EPOCH

[bundle]
format=verity

[image.rootfs]
filename=rootfs.ext4
EOF
	find "$work/content" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
	name="hifi-bridge-$COMPAT-$VERSION.raucb"
	mkdir -p "$OUT"
	rm -f "$OUT/$name"
	printf '[system]\ncompatible=%s\nbootloader=noop\n' "$COMPAT" > "$work/system.conf"
	[ -z "$KEYRING" ] || printf '[keyring]\npath=%s\ncheck-purpose=codesign\n' "$KEYRING" >> "$work/system.conf"
	rauc --conf="$work/system.conf" bundle --cert="$CERT" --key="$KEY" "$work/content" "$OUT/$name" >/dev/null
	echo "os_bundle: $OUT/$name ($(stat -c %s "$OUT/$name") bytes)"
	if [ -n "$KEYRING" ]; then
		rauc --conf="$work/system.conf" info --output-format=json "$OUT/$name" 2>/dev/null | python3 -c '
import hashlib, json, sys
i = json.load(sys.stdin)
print("compatible   :", i["compatible"]); print("version      :", i["version"])
print("image sha256 :", i["images"][0]["rootfs"]["checksum"]); print("manifest hash:", i["hash"])
data = open(sys.argv[1], "rb").read()                  # verity bundle = squashfs + hash tree + CMS signature + 8-byte signature size
sig = int.from_bytes(data[-8:], "big")
print("squashfs hash:", hashlib.sha256(data[:int.from_bytes(data[40:48], "little")]).hexdigest())   # superblock bytes_used
print("payload hash :", hashlib.sha256(data[:len(data) - 8 - sig]).hexdigest())' "$OUT/$name"
	fi
	exit 0
fi

ROOTFS="" COMPAT="" VERSION="" OUT="$REPO/output/ota" CERT="" KEY=""
while [ $# -gt 0 ]; do
	case "$1" in
		--rootfs) ROOTFS=$2; shift 2 ;; --compatible) COMPAT=$2; shift 2 ;; --version) VERSION=$2; shift 2 ;;
		--out) OUT=$2; shift 2 ;; --cert) CERT=$2; shift 2 ;; --key) KEY=$2; shift 2 ;;
		-h|--help) sed -n '2,17p' "$0"; exit 0 ;;
		*) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
	esac
done
[ -f "$ROOTFS" ] || { echo "os_bundle: --rootfs FILE is required (e.g. output/images/rootfs.ext4)" >&2; exit 2; }
case "$COMPAT" in hifi-bridge-rpi3|hifi-bridge-rpi4|hifi-bridge-rpizero2w|hifi-bridge-*) ;; *) echo "os_bundle: --compatible hifi-bridge-<board> is required" >&2; exit 2 ;; esac
[ -n "$VERSION" ] || VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' bridge/btbridge/__init__.py)"
[[ "$VERSION" =~ ^[0-9A-Za-z.+~-]+$ ]] || { echo "os_bundle: bad version '$VERSION'" >&2; exit 2; }
{ [ -z "$CERT" ] && [ -z "$KEY" ]; } || { [ -f "$CERT" ] && [ -f "$KEY" ]; } || { echo "os_bundle: give both --cert and --key, or neither" >&2; exit 2; }
if [ -n "$KEY" ] && git ls-files --error-unmatch "$KEY" >/dev/null 2>&1; then
	echo "os_bundle: refusing a signing key that is tracked by git: $KEY" >&2; exit 3
fi
EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || echo 0)}"
# shellcheck source=lib/common.sh
. scripts/lib/common.sh
hb_ensure_images btbridge-os:dev || exit 1
mkdir -p "$OUT" .keys
abs() { (cd "$(dirname "$1")" && printf '%s/%s' "$PWD" "$(basename "$1")"); }
ROOTFS=$(abs "$ROOTFS"); OUT=$(cd "$OUT" && pwd)
args=(--inside --rootfs /in/rootfs --compatible "$COMPAT" --version "$VERSION" --out /out --dev-keys /keys/dev-update)
mounts=(-v "$ROOTFS:/in/rootfs:ro" -v "$OUT:/out" -v "$REPO/.keys:/keys" -v "$REPO/scripts/os_bundle.sh:/os_bundle.sh:ro")
if [ -n "$CERT" ]; then
	mounts+=(-v "$(abs "$CERT"):/sign/cert.pem:ro" -v "$(abs "$KEY"):/sign/key.pem:ro"); args+=(--cert /sign/cert.pem --key /sign/key.pem)
fi
docker run --rm --name "hifi-ota-bundle-$$" --cpus 2 --memory 1g --network none -u "$(id -u):$(id -g)" -e HOME=/tmp \
	-e SOURCE_DATE_EPOCH="$EPOCH" "${mounts[@]}" btbridge-os:dev bash /os_bundle.sh "${args[@]}"
