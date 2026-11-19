"""Trusted image publishers (P9, docs/FLASH_TOOL.md §3).

* **Official** keys ship with the tool (``hififlash/keys/*.pem``, read-only, empty until the release key exists - G4 K-1).
* **Custom** keys (self-built images) are trusted only after the user adds them explicitly; the GUI shows the fingerprint first and labels
  every image they sign as "custom publisher". They live in the user's config directory (0700 directory, 0600 files).

Only ECDSA P-256 public keys are accepted (the manifest signature format, ``manifest.verify_signature``). A custom key file that no longer
parses is ignored (never trusted) and reported by ``problems()``.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

from . import manifest as M

OFFICIAL_DIR = os.path.join(os.path.dirname(__file__), "keys")
NAME_RE = re.compile(r"^[\w .()+-]{1,64}$")


class KeyStoreError(ValueError):
    pass


@dataclass(frozen=True)
class PublisherKey:
    name: str
    kind: str              # "official" | "custom"
    pem: bytes
    fingerprint: str       # SHA-256 of the DER SubjectPublicKeyInfo, colon-separated


def _load(pem: bytes):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError):
        raise KeyStoreError("not a PEM public key") from None
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise KeyStoreError("only ECDSA P-256 public keys are accepted")
    return key


def fingerprint(pem: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    der = _load(pem).public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, 64, 2))


class KeyStore:
    def __init__(self, custom_dir: str, official_dir: str = OFFICIAL_DIR):
        self.custom_dir, self.official_dir = custom_dir, official_dir

    def _scan(self, d: str, kind: str, bad: list[str]) -> list[PublisherKey]:
        out = []
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return out
        for n in names:
            if not n.endswith(".pem"):
                continue
            with open(os.path.join(d, n), "rb") as fh:
                raw = fh.read(16384)
            try:
                out.append(PublisherKey(n[:-4], kind, raw, fingerprint(raw)))
            except KeyStoreError:
                bad.append(n)
        return out

    def _all(self) -> tuple[list[PublisherKey], list[str]]:
        bad: list[str] = []
        return self._scan(self.official_dir, "official", bad) + self._scan(self.custom_dir, "custom", bad), bad

    def keys(self) -> list[PublisherKey]:
        return self._all()[0]

    def problems(self) -> list[str]:
        return self._all()[1]

    def add_custom(self, name: str, pem: bytes) -> PublisherKey:
        if not NAME_RE.match(name or "") or name.strip() != name or name.startswith("."):
            raise KeyStoreError("invalid name (1-64 letters, digits, space, . ( ) + - _)")
        fp = fingerprint(pem)
        existing = self.keys()
        if any(k.fingerprint == fp for k in existing):
            raise KeyStoreError("this key is already trusted")
        if any(k.name == name for k in existing):
            raise KeyStoreError("a key with this name already exists")
        os.makedirs(self.custom_dir, mode=0o700, exist_ok=True)
        os.chmod(self.custom_dir, 0o700)
        fd = os.open(os.path.join(self.custom_dir, name + ".pem"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        return PublisherKey(name, "custom", pem, fp)

    def remove_custom(self, name: str) -> None:
        if not any(k.name == name and k.kind == "custom" for k in self.keys()):
            raise KeyStoreError("only custom keys can be removed")
        os.remove(os.path.join(self.custom_dir, name + ".pem"))

    def verify(self, raw: bytes, sig: bytes) -> PublisherKey:
        """The trusted key that signed ``raw``; ManifestError otherwise."""
        keys = self.keys()
        name = M.verify_signature(raw, sig, {f"{k.kind}:{k.name}": k.pem for k in keys})
        return next(k for k in keys if f"{k.kind}:{k.name}" == name)
