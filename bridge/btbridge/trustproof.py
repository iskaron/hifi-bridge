"""Out-of-band authentication of the device CA for the flash tool (P7, docs/TRUST_BOOTSTRAP.md). Shared by device and flash tool; stdlib only.

The flash tool generates a random 32-byte **trust key**, shows nothing of it to anyone, and writes it into the provisioning file on the SD
card (physical channel). On first boot the device creates its CA (the key never leaves the device) and stores the trust key. The flash tool
then asks the device over the (untrusted) LAN for its CA certificate together with

    mac = HMAC-SHA256(trust_key, "hifi-bridge-trust/1" \\n nonce \\n SHA-256(CA certificate DER))

for a fresh random nonce. Only a device that knows the trust key can produce a valid mac for its CA, so a LAN attacker cannot substitute
its own CA, and replaying an old answer fails because of the nonce. The device deletes the trust key when the owner password is set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

FORMAT = "hifi-bridge-trust/1"
NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")


def mac(key: bytes, nonce: str, ca_der: bytes) -> str:
    msg = FORMAT.encode() + b"\n" + nonce.encode() + b"\n" + hashlib.sha256(ca_der).digest()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def fingerprint(ca_der: bytes) -> str:
    h = hashlib.sha256(ca_der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def answer(key: bytes, nonce: str, ca_der: bytes) -> dict:
    if not NONCE_RE.match(nonce or ""):
        raise ValueError("bad nonce")
    return {"format": FORMAT, "nonce": nonce, "ca_der": base64.b64encode(ca_der).decode(), "sha256": fingerprint(ca_der),
            "mac": mac(key, nonce, ca_der)}


class TrustProofError(Exception):
    pass


def verify(key: bytes, nonce: str, doc: dict) -> bytes:
    """Flash-tool side: returns the authenticated CA certificate (DER) or raises TrustProofError."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("nonce") != nonce:
        raise TrustProofError("unexpected answer")
    try:
        der = base64.b64decode(doc["ca_der"], validate=True)
        given = str(doc["mac"])
    except (KeyError, ValueError, TypeError):
        raise TrustProofError("malformed answer") from None
    if not hmac.compare_digest(given, mac(key, nonce, der)):
        raise TrustProofError("the device could not prove the trust key - do NOT trust this certificate")
    if doc.get("sha256") != fingerprint(der):
        raise TrustProofError("fingerprint does not match the certificate")
    return der
