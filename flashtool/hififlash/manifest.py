"""Flash manifest of a card image: what the tool verifies before and after writing.

    {"format": "hifi-bridge-image/1", "compatible": "hifi-bridge-rpi3", "version": "...", "image": "<file name>",
     "size": <bytes>, "sha256": "<hex of the raw image>", "slot": {"file": "hifi-provision.json", "offset": N, "size": 4096}}

Signature (P5/P9): ``<manifest>.sig`` = ECDSA P-256 / SHA-256 (DER) over the exact manifest bytes, made OFFLINE with an image-signing key
(``openssl dgst -sha256 -sign key.pem``). The tool trusts only the public keys it ships with (official) plus keys the user adds explicitly
(self-built images, shown as "custom publisher"). The sha256 binds the manifest to the image; the write is verified by reading back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from btbridge.provisioning import FILE_NAME, SLOT_SIZE

from .fatslot import file_reader, find_file

FORMAT = "hifi-bridge-image/1"


class ManifestError(Exception):
    pass


def build(image: str, compatible: str, version: str) -> dict:
    h = hashlib.sha256()
    with open(image, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    offset, size = find_file(file_reader(image), FILE_NAME)
    if size != SLOT_SIZE:
        raise ManifestError(f"provisioning slot has {size} bytes, expected {SLOT_SIZE}")
    return {"format": FORMAT, "compatible": compatible, "version": version, "image": os.path.basename(image),
            "size": os.path.getsize(image), "sha256": h.hexdigest(), "slot": {"file": FILE_NAME, "offset": offset, "size": size}}


def dumps(m: dict) -> bytes:
    return (json.dumps(m, indent=1, sort_keys=True) + "\n").encode()


def load(raw: bytes) -> dict:
    try:
        m = json.loads(raw)
    except ValueError:
        raise ManifestError("manifest is not JSON") from None
    if not isinstance(m, dict):
        raise ManifestError("manifest is not a JSON object")
    need ={"format": str, "compatible": str, "version": str, "image": str, "size": int, "sha256": str, "slot": dict}
    for k, t in need.items():
        if not isinstance(m.get(k), t):
            raise ManifestError(f"manifest field {k} missing or wrong type")
    if m["format"] != FORMAT:
        raise ManifestError("unsupported manifest format")
    s = m["slot"]
    if not (isinstance(s.get("offset"), int) and s.get("size") == SLOT_SIZE and 0 < s["offset"] and s["offset"] + s["size"] <= m["size"]):
        raise ManifestError("invalid provisioning slot")
    if len(m["sha256"]) != 64:
        raise ManifestError("invalid sha256")
    return m


def verify_signature(raw: bytes, sig: bytes, trusted_pems: dict[str, bytes]) -> str:
    """Returns the name of the trusted key that signed the manifest; raises ManifestError otherwise."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    for name, pem in trusted_pems.items():
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise ManifestError(f"trusted key {name}: only ECDSA P-256 is accepted")
        try:
            key.verify(sig, raw, ec.ECDSA(hashes.SHA256()))
            return name
        except InvalidSignature:
            continue
    raise ManifestError("the manifest is not signed by a trusted publisher")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hififlash.manifest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--image", required=True); b.add_argument("--compatible", required=True); b.add_argument("--version", required=True)
    b.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        m = build(a.image, a.compatible, a.version)
    except Exception as e:                                           # noqa: BLE001 - CLI boundary
        print(f"manifest: {e}", file=sys.stderr)
        return 1
    with open(a.out, "wb") as fh:
        fh.write(dumps(m))
    print(f"manifest: {a.out} (slot at byte {m['slot']['offset']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
