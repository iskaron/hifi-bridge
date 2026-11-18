"""Pseudonyms for Bluetooth/MAC addresses in logs, faults, remote syslog and the diagnostics bundle (review M10).

A phone's MAC address is personal data and a stable tracking identifier. Log lines therefore carry ``dev-<8 hex>`` instead:
SHA-256 over a random per-device salt (a secret in ``secrets.json``, renewed by a factory reset) and the normalised address.
The same phone gets the same pseudonym on one bridge, so logs stay correlatable; two bridges' logs are not linkable, and the
address cannot be read back from a log. The owner's own WebUI/MQTT views are not changed by this module.
"""
from __future__ import annotations

import hashlib
import re
import secrets

MAC_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}([:_-]))(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}(?![0-9a-f])")

_salt: bytes = secrets.token_bytes(16)          # until the service configures the device's persistent salt


def configure(salt: bytes) -> None:
    global _salt
    _salt = bytes(salt)


def mac(address: str) -> str:
    norm = re.sub(r"[-_]", ":", str(address)).upper()
    return "dev-" + hashlib.sha256(_salt + norm.encode()).hexdigest()[:8]


def scrub(text: str) -> str:
    """Replace every MAC-shaped address (``:``, ``-`` or BlueZ ``_`` separators) by its pseudonym."""
    return MAC_RE.sub(lambda m: mac(m.group(0)), str(text))


def scrub_data(obj):
    """Deep copy of JSON-like data with every string scrubbed (dict keys too)."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {scrub(k) if isinstance(k, str) else k: scrub_data(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_data(v) for v in obj]
    return obj


def scrubbed_print(msg: str) -> None:
    """Console logger for components that log before/outside the service's log buffer (BlueZ adapter -> journald)."""
    print(scrub(msg), flush=True)
