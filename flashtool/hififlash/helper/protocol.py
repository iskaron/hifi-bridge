"""Request protocol between the unprivileged GUI and the privileged write helper (docs/FLASH_TOOL.md §4).

Transport: UTF-8 JSON, one object per line (``\\n``), at most ``MAX_LINE`` bytes per line, over the helper's stdin/stdout (Linux, pkexec) or
over a loopback TCP connection that the helper opens back to the GUI after proving a one-time token (macOS/Windows, where elevation does not
keep stdio).

    helper -> GUI   {"event": "ready", "v": 1}
    GUI -> helper   {"v": 1, "op": "write", "disk": {"path", "name", "size", "serial"}, "allow_large": false,
                     "image": "/abs/path.img.xz", "manifest": {...}, "provisioning": "<base64 of the 4096-byte slot>" | null}
    GUI -> helper   {"op": "cancel"}                      (any time after the request; EOF on the GUI side also cancels)
    helper -> GUI   {"event": "progress", "stage": "prepare|write|verify", "done": n, "total": m}
    helper -> GUI   {"event": "done", "result": {"bytes", "sha256", "provisioned"}}   or
                    {"event": "error", "code": "<code>", "message": "<English text>", "reasons": [...]}

The helper accepts exactly ONE write request per run and validates it strictly: unknown fields, a disk path that is not a whole disk of the
platform (partitions, loop devices, files), a relative image path, an invalid manifest, or provisioning data that is not a valid, non-empty
``hifi-bridge-provision/1`` document of exactly the slot size are refused before anything is touched.
"""
from __future__ import annotations

import base64
import binascii
import json
import ntpath
import os
import posixpath
import re
from dataclasses import dataclass

from btbridge import provisioning as P

from .. import manifest as M
from ..disks import Disk

VERSION = 1
MAX_LINE = 64 << 10

DISK_PATH = {
    "linux": re.compile(r"^/dev/(sd[a-z]{1,3}|mmcblk[0-9]{1,3})$"),
    "darwin": re.compile(r"^/dev/disk[0-9]{1,3}$"),
    "windows": re.compile(r"^\\\\\.\\PhysicalDrive[0-9]{1,3}$"),
    "simulated": re.compile(r"^/[^\x00]*\.card$"),
}
REQUEST_FIELDS = {"v", "op", "disk", "allow_large", "image", "manifest", "provisioning"}
DISK_FIELDS = {"path", "name", "size", "serial"}

ERROR_CODES = ("bad_request", "disk_gone", "disk_changed", "disk_refused", "unmount_failed", "open_failed", "image_unreadable",
               "write_failed", "cancelled", "internal")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class WriteRequest:
    disk: Disk
    allow_large: bool
    image: str
    manifest: dict
    provisioning: bytes | None


def encode(obj: dict) -> bytes:
    line = json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if len(line) > MAX_LINE:
        raise ProtocolError("message too large")
    return line


def decode(line: bytes) -> dict:
    if len(line) > MAX_LINE:
        raise ProtocolError("message too large")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("not a JSON line") from None
    if not isinstance(obj, dict):
        raise ProtocolError("not a JSON object")
    return obj


def read_message(rfile) -> dict | None:
    """One message, or None at EOF. A line longer than MAX_LINE is an error, never buffered further."""
    line = rfile.readline(MAX_LINE + 1)
    if not line:
        return None
    if not line.endswith(b"\n"):
        raise ProtocolError("message too large or truncated")
    return decode(line)


def write_request(disk: Disk, image: str, manifest: dict, provisioning: bytes | None, allow_large: bool = False) -> dict:
    """GUI side: the request object (validated again by the helper)."""
    return {"v": VERSION, "op": "write", "disk": {"path": disk.path, "name": disk.name, "size": disk.size, "serial": disk.serial},
            "allow_large": bool(allow_large), "image": image, "manifest": manifest,
            "provisioning": base64.b64encode(provisioning).decode() if provisioning is not None else None}


def validate_request(obj: dict, platform: str) -> WriteRequest:
    if set(obj) != REQUEST_FIELDS:
        raise ProtocolError("unexpected or missing fields")
    if obj["v"] != VERSION or obj["op"] != "write":
        raise ProtocolError("unsupported version or operation")
    d = obj["disk"]
    if not isinstance(d, dict) or set(d) != DISK_FIELDS:
        raise ProtocolError("disk: unexpected or missing fields")
    rx = DISK_PATH.get(platform)
    if rx is None or not isinstance(d["path"], str) or not rx.match(d["path"]):
        raise ProtocolError("disk: not a whole disk of this platform")
    if not (isinstance(d["name"], str) and 0 < len(d["name"]) <= 64 and isinstance(d["serial"], str) and len(d["serial"]) <= 256
            and type(d["size"]) is int and d["size"] > 0):
        raise ProtocolError("disk: invalid name, size or serial")
    if type(obj["allow_large"]) is not bool:
        raise ProtocolError("allow_large must be a boolean")
    img = obj["image"]
    pathmod = ntpath if platform == "windows" else posixpath
    if not isinstance(img, str) or "\x00" in img or not (pathmod.isabs(img) or os.path.isabs(img)) or len(img) > 4096:
        raise ProtocolError("image: absolute path required")
    if (img.replace("\\", "/").startswith(("//./", "//?/")) if platform == "windows"
            else os.path.realpath(img).startswith(("/dev/", "/proc/", "/sys/"))):
        raise ProtocolError("image: device paths are not images")
    try:
        manifest = M.load(json.dumps(obj["manifest"]).encode())
    except (M.ManifestError, TypeError, ValueError) as e:
        raise ProtocolError(f"manifest: {e}") from None
    prov = obj["provisioning"]
    data = None
    if prov is not None:
        try:
            data = base64.b64decode(prov, validate=True) if isinstance(prov, str) else None
        except (binascii.Error, ValueError):
            data = None
        if data is None or len(data) != manifest["slot"]["size"]:
            raise ProtocolError("provisioning: not a slot-sized base64 block")
        try:
            if P.parse(data) is None:
                raise ProtocolError("provisioning: empty slot")
        except P.ProvisionError as e:
            raise ProtocolError("provisioning: invalid document (" + ", ".join(e.problems) + ")") from None
    disk = Disk(path=d["path"], name=d["name"], size=d["size"], serial=d["serial"])
    return WriteRequest(disk, obj["allow_large"], img, manifest, data)
