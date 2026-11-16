"""P7 on the owner's computer: authenticate the freshly booted device's CA with the trust key from the provisioning file, then prove that
the HTTPS endpoint really uses a certificate from that CA. Only after both checks is the fingerprint shown as "verified" and offered for
installation into the owner's trust stores (that last step is platform UI, not part of this package)."""
from __future__ import annotations

import json
import secrets
import ssl
import urllib.request

from btbridge import trustproof


def fetch_verified_ca(http_base: str, key: bytes, opener=urllib.request.urlopen, timeout: float = 10) -> bytes:
    nonce = secrets.token_hex(16)
    with opener(f"{http_base.rstrip('/')}/trust/proof?nonce={nonce}", timeout=timeout) as r:
        doc = json.loads(r.read(65536))
    return trustproof.verify(key, nonce, doc)


def check_https(https_base: str, ca_der: bytes, timeout: float = 10) -> int:
    """GET /healthz over HTTPS trusting ONLY the verified CA; the host name in ``https_base`` must be in the server certificate."""
    ctx = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert(ca_der))
    ctx.check_hostname, ctx.verify_mode = True, ssl.CERT_REQUIRED
    with urllib.request.urlopen(f"{https_base.rstrip('/')}/healthz", context=ctx, timeout=timeout) as r:
        return r.status
