"""Owner-initiated configuration backup and restore (docs/OTA.md section 7).

Format (JSON, ``format: hifi-bridge-backup``, ``format_version: 1``)::

    {"format", "format_version", "created", "app_version", "settings_schema", "device_name",
     "settings": {...},                       # every setting except the device identity (instance_id) and the wizard state
     "secrets": null | {"kdf": "scrypt", "n", "r", "p", "salt", "nonce", "cipher": "hmac-sha256-ctr", "ciphertext", "tag"}}

Secrets are handled EXPLICITLY:
  * by default they are NOT in the file (``secrets: null``) and the page says what has to be set again (owner password stays as it is on
    the target device, stream token, MQTT password);
  * on request they are included ENCRYPTED with an owner passphrase (at least 10 characters). Only the owner password hash, the stream
    token and the MQTT password are ever exported. The device CA key never leaves the device (DECISIONS #57), nor do session keys,
    setup codes, trust keys or an own TLS key.

Encryption uses only the standard library (DECISIONS #26): scrypt (n=2^14, r=8, p=1) derives 64 bytes = encryption key + MAC key;
the keystream is HMAC-SHA256(enc_key, nonce || counter) (a PRF in counter mode); the tag is HMAC-SHA256(mac_key, header || nonce ||
ciphertext) over the canonical JSON of every other field of the file (encrypt-then-MAC), so neither the secrets nor the settings can be
altered without the passphrase being detected as wrong. The tag is checked before anything is decrypted or applied.

Restore: the file is validated completely first (format, version, every setting); nothing is applied if anything is wrong.
Settings unknown to this version are reported and skipped (a backup from a newer release); older format versions are migrated.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from .. import __version__

FORMAT, FORMAT_VERSION = "hifi-bridge-backup", 1
MAX_BACKUP = 256 * 1024
EXCLUDED = ("instance_id", "setup_complete")            # stay with the device
EXPORTED_SECRETS = ("admin_password_hash", "stream_token", "mqtt_password")
MIN_PASSPHRASE = 10
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIGRATIONS: dict = {}                                  # format_version n -> function(doc) returning a doc of version n + 1


class BackupError(Exception):
    """``code``: invalid_file, wrong_format, too_new, invalid_settings, passphrase_required, wrong_passphrase, weak_passphrase."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{(': ' + detail) if detail else ''}")
        self.code, self.detail = code, detail


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s) -> bytes:
    try:
        return base64.b64decode(str(s), validate=True)
    except (ValueError, TypeError):
        raise BackupError("invalid_file", "bad base64") from None


def _keys(passphrase: str, salt: bytes, n: int, r: int, p: int) -> tuple[bytes, bytes]:
    k = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=64)
    return k[:32], k[32:]


def _stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + (i // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        out += bytes(a ^ b for a, b in zip(data[i:i + 32], block))
    return bytes(out)


def _header(doc: dict) -> bytes:
    """Everything except the secrets' tag and ciphertext, canonical - authenticated by the tag."""
    d = {k: v for k, v in doc.items() if k != "secrets"}
    s = {k: v for k, v in (doc.get("secrets") or {}).items() if k not in ("tag", "ciphertext")}
    return json.dumps({"doc": d, "secrets": s}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def export(store, include_secrets: bool = False, passphrase: str = "", version: str = __version__, clock=time.time) -> bytes:
    from ..config.store import SCHEMA_VERSION
    settings = {k: v for k, v in store.all().items() if k not in EXCLUDED}
    doc = {"format": FORMAT, "format_version": FORMAT_VERSION, "created": int(clock()), "app_version": version,
           "settings_schema": SCHEMA_VERSION, "device_name": settings.get("device_name", ""), "settings": settings, "secrets": None}
    if include_secrets:
        if len(passphrase) < MIN_PASSPHRASE:
            raise BackupError("weak_passphrase")
        plain = json.dumps({k: store.secret(k) for k in EXPORTED_SECRETS if store.secret(k)}, sort_keys=True).encode("utf-8")
        salt, nonce = os.urandom(16), os.urandom(16)
        enc, mac = _keys(passphrase, salt, **SCRYPT)
        doc["secrets"] = {"kdf": "scrypt", **SCRYPT, "salt": _b64(salt), "nonce": _b64(nonce), "cipher": "hmac-sha256-ctr"}
        ct = _stream(enc, nonce, plain)
        doc["secrets"]["ciphertext"] = _b64(ct)
        doc["secrets"]["tag"] = _b64(hmac.new(mac, _header(doc) + nonce + ct, hashlib.sha256).digest())
    return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")


def parse(raw: bytes) -> dict:
    """Validate the envelope and migrate it to the current format version (no secret is decrypted here)."""
    if len(raw) > MAX_BACKUP:
        raise BackupError("invalid_file", "too large")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BackupError("invalid_file", "not JSON") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BackupError("wrong_format")
    fv = doc.get("format_version")
    if not isinstance(fv, int) or fv < 1:
        raise BackupError("wrong_format", "format_version")
    if fv > FORMAT_VERSION:
        raise BackupError("too_new", str(fv))
    while doc["format_version"] < FORMAT_VERSION:       # version 1 is the first format; later formats register their upgrade here
        doc = MIGRATIONS[doc["format_version"]](doc)
    if not isinstance(doc.get("settings"), dict) or not (doc.get("secrets") is None or isinstance(doc.get("secrets"), dict)):
        raise BackupError("invalid_file", "settings/secrets")
    return doc


def decrypt(doc: dict, passphrase: str) -> dict:
    s = doc.get("secrets")
    if not s:
        return {}
    if not passphrase:
        raise BackupError("passphrase_required")
    if s.get("kdf") != "scrypt" or s.get("cipher") != "hmac-sha256-ctr":
        raise BackupError("invalid_file", "unknown cipher")
    try:
        n, r, p = int(s["n"]), int(s["r"]), int(s["p"])
    except (KeyError, TypeError, ValueError):
        raise BackupError("invalid_file", "kdf parameters") from None
    if not (2 ** 10 <= n <= 2 ** 16 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):     # bounded work (no CPU/RAM bomb)
        raise BackupError("invalid_file", "kdf parameters")
    salt, nonce, ct, tag = (_unb64(s.get(k)) for k in ("salt", "nonce", "ciphertext", "tag"))
    enc, mac = _keys(passphrase, salt, n, r, p)
    if not hmac.compare_digest(hmac.new(mac, _header(doc) + nonce + ct, hashlib.sha256).digest(), tag):
        raise BackupError("wrong_passphrase")            # or a modified file: indistinguishable on purpose
    try:
        out = json.loads(_stream(enc, nonce, ct).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):             # a valid tag over a payload that is not ours
        raise BackupError("invalid_file", "secrets") from None
    return {k: str(v) for k, v in out.items() if k in EXPORTED_SECRETS and isinstance(v, str) and v}


def plan(doc: dict, passphrase: str = "") -> dict:
    """Everything that a restore would do, fully validated; raises BackupError before anything is changed."""
    from ..config import SETTINGS, SettingError, validate
    settings, skipped, invalid = {}, [], []
    for k, v in doc["settings"].items():
        if k in EXCLUDED:
            continue
        if k not in SETTINGS:
            skipped.append(k)                            # from a newer release: reported, not applied
            continue
        try:
            settings.update(validate({k: v}))
        except SettingError:
            invalid.append(k)
    if invalid:
        raise BackupError("invalid_settings", ", ".join(sorted(invalid)))
    return {"settings": settings, "skipped": sorted(skipped), "secrets": decrypt(doc, passphrase),
            "had_secrets": bool(doc.get("secrets")), "app_version": str(doc.get("app_version", "")), "created": doc.get("created")}


def apply(store, p: dict) -> dict:
    """Apply a validated plan: secrets first (they cannot fail), then all settings in one validated update."""
    for k, v in p["secrets"].items():
        store.set_secret(k, v)
    changed = store.update(p["settings"])
    return {"changed": sorted(changed), "skipped": p["skipped"], "secrets": sorted(p["secrets"]), "had_secrets": p["had_secrets"]}
