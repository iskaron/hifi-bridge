"""First-setup provisioning file on the boot partition (P6, docs/PROVISIONING.md) - shared by the device and the flash tool.

Format ``hifi-bridge-provision/1`` (JSON, UTF-8) in the file ``hifi-provision.json`` on the FAT boot partition. The image ships the file as a
fixed-size **slot** (``SLOT_SIZE`` bytes, an "empty" placeholder padded with spaces) so the flash tool can write it in place on the raw card
without mounting a filesystem. A person can also write it by hand (documented manual bootstrap).

Device side (``consume``), run by ``btbridge.bootfiles`` before the service starts:

* validate strictly (unknown fields, weak or malformed values -> nothing is applied; problems are reported by field name, never by value);
* apply idempotently under a journal in the data directory, so a power cut at any point either repeats the whole step or finishes it;
* then **scrub** the file in place: overwrite every byte with the empty placeholder (same length, same clusters on FAT) and fsync. The file
  name stays, so the slot stays usable. Physical remnants inside the card's flash translation layer cannot be ruled out by software; the
  secrets are therefore short-lived by design (setup code ends with the setup, trust key ends with the setup, hotspot key only matters for
  the setup hotspot) - see docs/PROVISIONING.md.

Hotspot rule (review decision on P6): a setup hotspot exists ONLY with an individual password that the owner knows (it comes from this
file). Without it the device never opens a hotspot.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time

from .config import schema

FORMAT = "hifi-bridge-provision/1"
FILE_NAME = "hifi-provision.json"
SLOT_SIZE = 4096
JOURNAL = "provision-journal.json"
NETWORK_FILE = "network.json"
SETUP_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
HOTSPOT_PSK_MIN = 12          # stricter than WPA's 8: the hotspot is reachable by anyone nearby during setup
FIELDS = {"format", "created_by", "setup_code", "trust_key", "device_name", "local_hostname", "country", "hotspot", "wifi"}
NET_FIELDS = {"ssid", "psk"}


class ProvisionError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


def placeholder(size: int = SLOT_SIZE) -> bytes:
    body = json.dumps({"format": FORMAT, "state": "empty"}, separators=(",", ":")).encode()
    if size < len(body) + 1:
        return b" " * size
    return body + b"\n" + b" " * (size - len(body) - 1)


def is_empty(raw: bytes) -> bool:
    text = raw.replace(b"\x00", b" ").strip()
    if not text:
        return True
    try:
        doc = json.loads(text)
    except ValueError:
        return False
    return isinstance(doc, dict) and doc.get("state") == "empty"


def normalise_setup_code(code: str) -> str:
    return "".join(c for c in str(code).upper() if c.isalnum())


def _printable_ascii(s: str) -> bool:
    return all(32 <= ord(c) <= 126 for c in s)


def _ssid_ok(s) -> bool:
    return isinstance(s, str) and 1 <= len(s.encode("utf-8")) <= 32


def decode_trust_key(s: str) -> bytes:
    raw = base64.b32decode(s.upper() + "=" * (-len(s) % 8))
    if len(raw) != 32:
        raise ValueError("trust key must be 32 bytes")
    return raw


def encode_trust_key(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=")


def validate(doc) -> list[str]:
    """Problems as 'field:reason' codes (never values - they may be secrets)."""
    if not isinstance(doc, dict):
        return ["document:not_an_object"]
    p = [f"{k}:unknown_field" for k in sorted(set(doc) - FIELDS)]
    if doc.get("format") != FORMAT:
        p.append("format:unsupported")
    code = doc.get("setup_code")
    if not isinstance(code, str) or len(normalise_setup_code(code)) != 12 or any(c not in SETUP_ALPHABET for c in normalise_setup_code(code)):
        p.append("setup_code:invalid")
    if "trust_key" in doc:
        try:
            decode_trust_key(doc["trust_key"])
        except (ValueError, TypeError, AttributeError):
            p.append("trust_key:invalid")
    for key in ("device_name", "local_hostname"):
        if key in doc:
            try:
                schema.validate({key: doc[key]})
            except (ValueError, TypeError):
                p.append(f"{key}:invalid")
    if "created_by" in doc and not (isinstance(doc["created_by"], str) and len(doc["created_by"]) <= 64):
        p.append("created_by:invalid")
    nets = [k for k in ("hotspot", "wifi") if k in doc]
    if nets and not (isinstance(doc.get("country"), str) and re.fullmatch(r"[A-Z]{2}", doc["country"]) and doc["country"] != "00"):
        p.append("country:required_iso_3166_alpha2")
    for k in nets:
        net = doc[k]
        if not isinstance(net, dict):
            p.append(f"{k}:not_an_object")
            continue
        p += [f"{k}.{f}:unknown_field" for f in sorted(set(net) - NET_FIELDS)]
        if not _ssid_ok(net.get("ssid")):
            p.append(f"{k}.ssid:invalid")
        psk = net.get("psk")
        if k == "hotspot":
            if not (isinstance(psk, str) and HOTSPOT_PSK_MIN <= len(psk) <= 63 and _printable_ascii(psk)):
                p.append(f"hotspot.psk:required_{HOTSPOT_PSK_MIN}_to_63_printable_ascii")
        elif not (isinstance(psk, str) and ((8 <= len(psk) <= 63 and _printable_ascii(psk)) or re.fullmatch(r"[0-9a-fA-F]{64}", psk))):
            p.append("wifi.psk:invalid")
    return p


def parse(raw: bytes) -> dict | None:
    """None for an empty slot; the document when valid; ProvisionError otherwise."""
    if is_empty(raw):
        return None
    try:
        doc = json.loads(raw.replace(b"\x00", b" ").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProvisionError(["document:not_json"]) from None
    problems = validate(doc)
    if problems:
        raise ProvisionError(problems)
    return doc


def render(doc: dict, size: int = SLOT_SIZE) -> bytes:
    """Serialise a valid document padded to the slot size (flash tool)."""
    problems = validate(doc)
    if problems:
        raise ProvisionError(problems)
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) + 1 > size:
        raise ProvisionError(["document:too_large_for_slot"])
    return body + b"\n" + b" " * (size - len(body) - 1)


# ------------------------------------------------------------------ device side
def _write_json_private(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def _fsync_dir(d: str) -> None:
    try:
        fd = os.open(d or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def scrub(path: str) -> None:
    """Overwrite the whole file in place with the empty placeholder (same length -> same FAT clusters), then fsync."""
    size = os.path.getsize(path)
    with open(path, "r+b") as fh:
        fh.write(placeholder(size))
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_dir(os.path.dirname(path))


def _journal(data_dir: str) -> dict:
    try:
        with open(os.path.join(data_dir, JOURNAL)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def apply(doc: dict, data_dir: str, iterations: int | None = None, log=lambda m: None) -> list[str]:
    """Apply a validated document (idempotent). Returns notes for the status file (no secrets)."""
    from .config import ConfigStore
    from .webui.auth import Auth
    store = ConfigStore(data_dir)
    notes = []
    changes = {k: doc[k] for k in ("device_name", "local_hostname") if k in doc}
    if changes:
        store.update(changes)
    auth = Auth(store, log=log, **({"iterations": iterations} if iterations else {}))
    if auth.has_password():
        notes.append("owner password already set - the setup code in the file was ignored (use hifi-recovery to reset)")
    else:
        auth.adopt_setup_code(doc["setup_code"])
        notes.append("setup code from the provisioning file is active")
        if "trust_key" in doc:
            store.set_secret("trust_key", doc["trust_key"])
            notes.append("device trust key stored (used once by the flash tool to verify this device's certificate)")
    net = {k: doc[k] for k in ("country", "hotspot", "wifi") if k in doc}
    _write_json_private(os.path.join(data_dir, NETWORK_FILE), {"format": "hifi-bridge-network/1", **net})
    notes.append("network: " + ", ".join(f"{k}" for k in ("wifi", "hotspot") if k in net) if ("wifi" in net or "hotspot" in net)
                 else "network: no Wi-Fi and no setup hotspot configured (Ethernet only)")
    return notes


def consume(boot_dir: str, data_dir: str, iterations: int | None = None, log=lambda m: None) -> dict:
    """Process the provisioning file once, power-cut safe. Result: {'result': none|applied|invalid, 'notes'|'problems': [...]}."""
    path = os.path.join(boot_dir, FILE_NAME)
    j = _journal(data_dir)
    if not os.path.isfile(path):
        return {"result": "none"}
    with open(path, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    if is_empty(raw):
        if j.get("state") == "applied":                       # power cut between scrub and the final journal entry
            _write_json_private(os.path.join(data_dir, JOURNAL), {**j, "state": "done"})
        return {"result": "none"}
    try:
        doc = parse(raw)
    except ProvisionError as e:
        if j.get("state") == "applied":                       # torn scrub after a power cut: finish scrubbing, nothing else
            scrub(path)
            _write_json_private(os.path.join(data_dir, JOURNAL), {**j, "state": "done"})
            return {"result": "none"}
        scrub(path)                                           # never leave secrets on the FAT, even in an invalid file
        _write_json_private(os.path.join(data_dir, JOURNAL), {"state": "rejected", "at": time.time(), "problems": e.problems})
        log("provisioning file rejected: " + ", ".join(e.problems))
        return {"result": "invalid", "problems": e.problems}
    if not (j.get("state") == "applied" and j.get("digest") == digest):
        _write_json_private(os.path.join(data_dir, JOURNAL), {"state": "applying", "digest": digest, "at": time.time()})
        notes = apply(doc, data_dir, iterations, log)
        _write_json_private(os.path.join(data_dir, JOURNAL), {"state": "applied", "digest": digest, "at": time.time(), "notes": notes})
    else:
        notes = j.get("notes", [])
    scrub(path)
    _write_json_private(os.path.join(data_dir, JOURNAL), {"state": "done", "digest": digest, "at": time.time(), "notes": notes})
    log("provisioning file applied and scrubbed")
    return {"result": "applied", "notes": notes}


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="btbridge.provisioning")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("placeholder", help="write the empty slot (image build)")
    pl.add_argument("--size", type=int, default=SLOT_SIZE)
    sub.add_parser("check", help="validate a provisioning file from stdin (manual bootstrap)")
    a = ap.parse_args(argv)
    if a.cmd == "placeholder":
        sys.stdout.buffer.write(placeholder(a.size))
        return 0
    try:
        doc = parse(sys.stdin.buffer.read())
    except ProvisionError as e:
        print("invalid: " + ", ".join(e.problems), file=sys.stderr)
        return 1
    print("empty slot" if doc is None else "valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
