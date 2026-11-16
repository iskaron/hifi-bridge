"""Command line for the flash tool core (test day and development). There is deliberately NO command that writes a disk.

    python3 -m hififlash provision --country DE [--device-name NAME] [--no-hotspot] --out hifi-provision.json --key-out trust.key
    python3 -m hififlash verify-device --url http://<device>:8080 --key-file trust.key [--https https://<device>:8443]
    python3 -m hififlash verify-image IMAGE --manifest IMAGE.manifest.json [--sig IMAGE.manifest.json.sig --key publisher.pub]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

from btbridge import provisioning as P
from btbridge import trustproof

from . import manifest as M
from . import provision as FP
from . import trust as FT


def _write_private(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hififlash")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision", help="create a provisioning file (slot-sized) + owner sheet + trust key file")
    p.add_argument("--country", required=True); p.add_argument("--device-name"); p.add_argument("--no-hotspot", action="store_true")
    p.add_argument("--out", required=True); p.add_argument("--key-out", required=True); p.add_argument("--lang", default="en")
    v = sub.add_parser("verify-device", help="P7: authenticate the device CA with the trust key; optionally check HTTPS")
    v.add_argument("--url", required=True); v.add_argument("--key-file", required=True); v.add_argument("--https")
    v.add_argument("--ca-out")
    i = sub.add_parser("verify-image", help="check an image against its manifest (and the manifest signature)")
    i.add_argument("image"); i.add_argument("--manifest", required=True); i.add_argument("--sig"); i.add_argument("--key")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "provision":
            doc, key = FP.new_document(a.country, device_name=a.device_name, hotspot=not a.no_hotspot)
            _write_private(a.out, P.render(doc))
            _write_private(a.key_out, P.encode_trust_key(key).encode() + b"\n")
            print(FP.owner_sheet(doc, a.lang))
            print(f"written: {a.out} (copy it over hifi-provision.json on the boot partition), trust key: {a.key_out}")
            return 0
        if a.cmd == "verify-device":
            key = P.decode_trust_key(open(a.key_file).read().strip())
            der = FT.fetch_verified_ca(a.url, key)
            print(f"VERIFIED device CA, SHA-256 fingerprint: {trustproof.fingerprint(der)}")
            if a.ca_out:
                import ssl
                open(a.ca_out, "w").write(ssl.DER_cert_to_PEM_cert(der))
            if a.https:
                print(f"HTTPS with only this CA: HTTP {FT.check_https(a.https, der)}")
            return 0
        m_raw = open(a.manifest, "rb").read()
        m = M.load(m_raw)
        if a.sig:
            if not a.key:
                print("--sig needs --key (the publisher's public key)", file=sys.stderr)
                return 2
            print(f"manifest signed by: {M.verify_signature(m_raw, open(a.sig, 'rb').read(), {'publisher': open(a.key, 'rb').read()})}")
        h = hashlib.sha256()
        with open(a.image, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != m["sha256"] or os.path.getsize(a.image) != m["size"]:
            print("IMAGE DOES NOT MATCH THE MANIFEST", file=sys.stderr)
            return 1
        print(f"image matches the manifest ({m['compatible']} {m['version']})")
        return 0
    except (trustproof.TrustProofError, M.ManifestError, P.ProvisionError, OSError, ValueError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
