"""Owner-initiated configuration backup and restore (docs/OTA.md section 7).

Format (JSON, ``format: hifi-bridge-backup``, ``format_version: 2``)::

    {"format", "format_version", "created", "app_version", "settings_schema", "device_name",
     "settings": {...},                       # every setting except the device identity (instance_id) and the wizard state
     "secrets": null | {"kdf": "scrypt", "n", "r", "p", "salt", "cipher": "aes-256-ctr", "openssl": "enc-pbkdf2-sha256-iter1-nosalt",
                        "ciphertext", "tag"}}

Secrets are handled EXPLICITLY:
  * by default they are NOT in the file (``secrets: null``) and the page says what has to be set again (owner password stays as it is on
    the target device, stream token, MQTT password);
  * on request they are included ENCRYPTED with an owner passphrase (at least 10 characters). Only the owner password hash, the stream
    token and the MQTT password are ever exported. The device CA key never leaves the device (DECISIONS #57), nor do session keys,
    setup codes, trust keys or an own TLS key.

Cryptography (DECISIONS #50: OpenSSL, no own cryptography):
  * ``hashlib.scrypt`` (n=2^14, r=8, p=1, random 16-byte salt per file) turns the passphrase into 64 bytes: the first 32 are the
    encryption key material, the last 32 the MAC key (independent halves of one KDF output).
  * The cipher is **AES-256-CTR run by the openssl CLI** (``openssl enc``). The key material reaches openssl only through a pipe
    (``-pass fd:N``) - never on the command line, in the environment or in a file. openssl turns it into key and IV with PBKDF2-SHA256
    and ONE iteration: its input is already 256 bits from scrypt, unique per file (random salt), so no salt and no stretching are needed
    there; (key, IV) never repeat. ``openssl enc`` with ``-K``/``-iv`` was not used because that puts the key on the command line.
  * Encrypt-then-MAC: ``tag = HMAC-SHA256(mac_key, header || ciphertext)``, where the header is the canonical JSON of the whole file
    except ciphertext and tag (format version, settings, KDF parameters, cipher names). The tag is compared in constant time BEFORE
    openssl decrypts anything; a wrong passphrase and a modified file are indistinguishable on purpose.

Version 1 (an own HMAC-based keystream) existed only on the unmerged development branch and never on a device; it is refused.
Restore: the file is validated completely first (format, version, every setting); nothing is applied if anything is wrong.
Settings unknown to this version are reported and skipped (a backup from a newer release); older supported format versions are
migrated through ``MIGRATIONS``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time

from .. import __version__

FORMAT, FORMAT_VERSION, MIN_FORMAT_VERSION = "hifi-bridge-backup", 2, 2
MAX_BACKUP = 256 * 1024
EXCLUDED = ("instance_id", "setup_complete")            # stay with the device
EXPORTED_SECRETS = ("admin_password_hash", "stream_token", "mqtt_password")
MIN_PASSPHRASE = 10
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
CIPHER, OPENSSL_MODE = "aes-256-ctr", "enc-pbkdf2-sha256-iter1-nosalt"
OPENSSL_ENC = ("enc", "-aes-256-ctr", "-pbkdf2", "-iter", "1", "-md", "sha256", "-nosalt")
OPENSSL: str | None = None                             # path of the openssl binary (None = from PATH); tests may wrap it
OPENSSL_TIMEOUT = 30.0
MIGRATIONS: dict = {}                                  # format_version n -> function(doc) returning a doc of version n + 1


class BackupError(Exception):
    """``code``: invalid_file, wrong_format, too_new, invalid_settings, passphrase_required, wrong_passphrase, weak_passphrase,
    crypto_unavailable, crypto_failed."""

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


def _openssl(decrypt: bool, key_material: bytes, data: bytes) -> bytes:
    """AES-256-CTR through ``openssl enc``; the key material goes through an inherited pipe (``-pass fd:N``), data through stdin/stdout."""
    exe = OPENSSL or shutil.which("openssl")
    if not exe:
        raise BackupError("crypto_unavailable", "openssl not found")
    r, w = os.pipe()
    try:
        proc = subprocess.Popen([exe, *OPENSSL_ENC, "-d" if decrypt else "-e", "-pass", f"fd:{r}"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(r,), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except OSError as e:
        os.close(w)
        raise BackupError("crypto_unavailable", type(e).__name__) from None
    finally:
        os.close(r)
    with os.fdopen(w, "wb") as fh:                     # a few bytes: fits the pipe buffer, never blocks
        fh.write(key_material.hex().encode("ascii") + b"\n")
    try:
        out, _ = proc.communicate(data, timeout=OPENSSL_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise BackupError("crypto_failed", "timeout") from None
    if proc.returncode != 0:
        raise BackupError("crypto_failed", f"openssl exit {proc.returncode}")
    return out


def _header(doc: dict) -> bytes:
    """Everything except the secrets' tag and ciphertext, canonical - authenticated by the tag."""
    d = {k: v for k, v in doc.items() if k != "secrets"}
    s = {k: v for k, v in (doc.get("secrets") or {}).items() if k not in ("tag", "ciphertext")}
    return json.dumps({"doc": d, "secrets": s}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _tag(mac_key: bytes, doc: dict, ct: bytes) -> bytes:
    return hmac.new(mac_key, _header(doc) + ct, hashlib.sha256).digest()


def export(store, include_secrets: bool = False, passphrase: str = "", version: str = __version__, clock=time.time) -> bytes:
    from ..config.store import SCHEMA_VERSION
    settings = {k: v for k, v in store.all().items() if k not in EXCLUDED}
    doc = {"format": FORMAT, "format_version": FORMAT_VERSION, "created": int(clock()), "app_version": version,
           "settings_schema": SCHEMA_VERSION, "device_name": settings.get("device_name", ""), "settings": settings, "secrets": None}
    if include_secrets:
        if len(passphrase) < MIN_PASSPHRASE:
            raise BackupError("weak_passphrase")
        plain = json.dumps({k: store.secret(k) for k in EXPORTED_SECRETS if store.secret(k)}, sort_keys=True).encode("utf-8")
        salt = os.urandom(16)
        enc, mac = _keys(passphrase, salt, **SCRYPT)
        doc["secrets"] = {"kdf": "scrypt", **SCRYPT, "salt": _b64(salt), "cipher": CIPHER, "openssl": OPENSSL_MODE}
        ct = _openssl(False, enc, plain)
        doc["secrets"]["ciphertext"] = _b64(ct)
        doc["secrets"]["tag"] = _b64(_tag(mac, doc, ct))
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
    if not isinstance(fv, int) or fv < MIN_FORMAT_VERSION:
        raise BackupError("wrong_format", "format_version")
    if fv > FORMAT_VERSION:
        raise BackupError("too_new", str(fv))
    while doc["format_version"] < FORMAT_VERSION:       # later formats register their upgrade here
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
    if s.get("kdf") != "scrypt" or s.get("cipher") != CIPHER or s.get("openssl") != OPENSSL_MODE:
        raise BackupError("invalid_file", "unknown cipher")
    try:
        n, r, p = int(s["n"]), int(s["r"]), int(s["p"])
    except (KeyError, TypeError, ValueError):
        raise BackupError("invalid_file", "kdf parameters") from None
    if not (2 ** 10 <= n <= 2 ** 16 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):     # bounded work (no CPU/RAM bomb)
        raise BackupError("invalid_file", "kdf parameters")
    salt, ct, tag = (_unb64(s.get(k)) for k in ("salt", "ciphertext", "tag"))
    enc, mac = _keys(passphrase, salt, n, r, p)
    if not hmac.compare_digest(_tag(mac, doc, ct), tag):   # BEFORE anything is decrypted
        raise BackupError("wrong_passphrase")            # or a modified file: indistinguishable on purpose
    try:
        out = json.loads(_openssl(True, enc, ct).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):             # a valid tag over a payload that is not ours
        raise BackupError("invalid_file", "secrets") from None
    if not isinstance(out, dict):
        raise BackupError("invalid_file", "secrets")
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
