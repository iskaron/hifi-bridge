"""Disk discovery and the SAFETY POLICY for choosing a target card (P9, docs/FLASH_TOOL.md).

Parsers turn the JSON/plist output of the platform tools into ``Disk`` records. The commands are listed in ``COMMANDS`` and run only by
``list_disks(runner=...)`` inside the future GUI on the owner's machine; tests and the development machine never run them (CLAUDE.md:
no host hardware discovery) - they feed recorded outputs to the parsers instead.

A disk is offered only when ALL rules hold (``problems`` explains every refusal):
  * removable or USB/SD/MMC transport, and a whole disk (not a partition);
  * not the system disk and nothing mounted at a system location - mounts are collected from the WHOLE device tree (partitions, LUKS,
    LVM, RAID; active swap counts as a system mount) and, on macOS, from the APFS containers whose physical store is on the disk; the disk
    behind ``/`` (``diskutil info -plist /``) is always the system disk (review M11);
  * no open crypt/LVM/RAID device on it (``in_use_by_volume_manager``); plain mounted partitions are allowed and are unmounted by the
    privileged helper before writing (it re-checks that nothing is mounted any more);
  * not read-only; size between ``MIN_BYTES`` and ``MAX_BYTES`` (larger needs an explicit "large disk" confirmation) and >= the image;
  * the user re-types the disk's short name (``confirm_token``) - one click is never enough;
  * the identity (path + size + serial) is re-checked immediately before writing (``same_disk``), so a swapped card is not overwritten.
"""
from __future__ import annotations

import json
import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

MIN_BYTES = 1 * 1000 ** 3            # PHASE2B_PLAN: >= 1 GB minimum (to be confirmed by measurement)
MAX_BYTES = 256 * 1000 ** 3          # above: explicit confirmation (an SD card bigger than this is unusual; a USB hard disk is likely)
SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/usr", "/var", "/home", "/System/Volumes/Data", "C:\\", "[SWAP]")
STACKED_TYPES = ("crypt", "lvm", "dm", "md", "mpath")
DARWIN_ALL = ["diskutil", "list", "-plist"]
DARWIN_ROOT = ["diskutil", "info", "-plist", "/"]


def _system_mount(m: str) -> bool:
    return m in SYSTEM_MOUNTS
COMMANDS = {
    "linux": ["lsblk", "--json", "--bytes", "--output", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,RM,RO,HOTPLUG,MOUNTPOINTS"],
    "darwin": ["diskutil", "list", "-plist", "physical"],
    "windows": ["powershell", "-NoProfile", "-Command",
                "Get-Disk | Select-Object Number,FriendlyName,SerialNumber,Size,BusType,IsBoot,IsSystem,IsReadOnly,"
                "@{n='DriveLetters';e={(Get-Partition -DiskNumber $_.Number -ErrorAction SilentlyContinue).DriveLetter -join ''}} | ConvertTo-Json"],
}


@dataclass(frozen=True)
class Disk:
    path: str                      # /dev/sdb, /dev/disk4, \\.\PhysicalDrive3
    name: str                      # short name the user re-types: sdb, disk4, PhysicalDrive3
    size: int
    model: str = ""
    serial: str = ""
    transport: str = ""            # usb, sd, mmc, sata, nvme, ...
    removable: bool = False
    readonly: bool = False
    system: bool = False
    mounts: tuple = field(default_factory=tuple)
    stacked: bool = False          # an open crypt/LVM/RAID device sits on the disk (unmounting does not release it)

    def label(self) -> str:
        return f"{self.name} - {self.model or 'unknown model'} - {self.size / 1000 ** 3:.1f} GB"


def walk(node: dict):
    """The node and ALL its descendants (partitions, and below them crypt/LVM/RAID devices - review M11)."""
    yield node
    for ch in node.get("children") or []:
        yield from walk(ch)


def parse_lsblk(text: str) -> list[Disk]:
    out = []
    for d in json.loads(text).get("blockdevices", []):
        if d.get("type") != "disk":
            continue
        nodes = list(walk(d))
        mounts = [m for n in nodes for m in (n.get("mountpoints") or []) if m]
        stacked = any(str(n.get("type") or "") in STACKED_TYPES or str(n.get("type") or "").startswith("raid") for n in nodes[1:])
        out.append(Disk(path=d["path"], name=d["name"], size=int(d.get("size") or 0), model=(d.get("model") or "").strip(),
                        serial=(d.get("serial") or "").strip(), transport=(d.get("tran") or "").lower(),
                        removable=bool(d.get("rm")) or bool(d.get("hotplug")), readonly=bool(d.get("ro")),
                        system=any(_system_mount(m) for m in mounts), mounts=tuple(mounts), stacked=stacked))
    return out


def whole_disk(ident: str) -> str:
    """macOS slice -> whole disk: disk4s2 -> disk4."""
    return re.sub(r"s[0-9]+$", "", ident)


def apfs_containers_on(all_plist: bytes, disk: str) -> list[str]:
    """Synthesized APFS container disks whose physical store lives on the physical ``disk`` (``diskutil list -plist``)."""
    out = []
    for e in plistlib.loads(all_plist).get("AllDisksAndPartitions", []):
        stores = [whole_disk(s.get("DeviceIdentifier", "")) for s in e.get("APFSPhysicalStores") or []]
        if disk in stores:
            out.append(e["DeviceIdentifier"])
    return out


def parse_diskutil(list_plist: bytes, infos: dict[str, bytes], all_plist: bytes | None = None, root_info: bytes | None = None) -> list[Disk]:
    """``list_plist`` = ``diskutil list -plist physical``; ``infos[disk]`` = ``diskutil info -plist <disk>`` for each whole disk;
    ``all_plist`` = ``diskutil list -plist`` (incl. synthesized APFS containers); ``root_info`` = ``diskutil info -plist /``.

    APFS volumes (also the running system's) are mounted from a SYNTHESIZED container disk, not from the physical disk. Their mount points
    are attributed to the physical disk that holds the container's physical store, and the disk behind ``/`` is the system disk (review
    M11) - also when the Mac booted from an external disk."""
    containers = plistlib.loads(all_plist).get("AllDisksAndPartitions", []) if all_plist else []
    root_parent = plistlib.loads(root_info).get("ParentWholeDisk", "") if root_info else ""
    root_disks = {root_parent}
    for c in containers:
        if c.get("DeviceIdentifier") == root_parent:
            root_disks |= {whole_disk(s.get("DeviceIdentifier", "")) for s in c.get("APFSPhysicalStores") or []}
    out = []
    for entry in plistlib.loads(list_plist).get("AllDisksAndPartitions", []):
        ident = entry["DeviceIdentifier"]
        info = plistlib.loads(infos[ident])
        mounts = [p.get("MountPoint") for p in entry.get("Partitions", []) if p.get("MountPoint")]
        for c in containers:
            if ident in {whole_disk(s.get("DeviceIdentifier", "")) for s in c.get("APFSPhysicalStores") or []}:
                mounts += [v["MountPoint"] for v in c.get("APFSVolumes") or [] if v.get("MountPoint")]
        out.append(Disk(path=f"/dev/{ident}", name=ident, size=int(info.get("TotalSize") or info.get("Size") or 0),
                        model=info.get("MediaName", ""), serial=info.get("DiskUUID", "") or info.get("IORegistryEntryName", ""),
                        transport=(info.get("BusProtocol") or "").lower().replace("secure digital", "sd"),
                        removable=bool(info.get("RemovableMedia") or info.get("Ejectable") or not info.get("Internal", True)),
                        readonly=not info.get("WritableMedia", True),
                        system=ident in root_disks or any(_system_mount(m) for m in mounts) or bool(info.get("SystemImage")),
                        mounts=tuple(mounts)))
    return out


def parse_windows(text: str) -> list[Disk]:
    data = json.loads(text)
    rows = data if isinstance(data, list) else [data]
    out = []
    for r in rows:
        n = int(r["Number"])
        letters = tuple(f"{c}:\\" for c in (r.get("DriveLetters") or ""))
        out.append(Disk(path=f"\\\\.\\PhysicalDrive{n}", name=f"PhysicalDrive{n}", size=int(r.get("Size") or 0),
                        model=(r.get("FriendlyName") or "").strip(), serial=(r.get("SerialNumber") or "").strip(),
                        transport=str(r.get("BusType") or "").lower(), removable=str(r.get("BusType")).upper() in ("USB", "SD", "MMC"),
                        readonly=bool(r.get("IsReadOnly")), system=bool(r.get("IsBoot")) or bool(r.get("IsSystem")) or "C:\\" in letters,
                        mounts=letters))
    return out


def problems(d: Disk, image_size: int, allow_large: bool = False) -> list[str]:
    p = []
    if d.system:
        p.append("system_disk")
    if any(m in SYSTEM_MOUNTS for m in d.mounts):
        p.append("system_mount")
    if not (d.removable or d.transport in ("usb", "sd", "mmc")):
        p.append("not_removable")
    if d.readonly:
        p.append("read_only")
    if d.stacked:
        p.append("in_use_by_volume_manager")
    if d.size < MIN_BYTES:
        p.append("too_small_for_product")
    if d.size < image_size:
        p.append("smaller_than_image")
    if d.size > MAX_BYTES and not allow_large:
        p.append("unusually_large_confirm_explicitly")
    return p


def eligible(disks: list[Disk], image_size: int, allow_large: bool = False) -> list[Disk]:
    return [d for d in disks if not problems(d, image_size, allow_large)]


def confirm_token(d: Disk) -> str:
    return d.name


def confirmed(d: Disk, typed: str) -> bool:
    return typed.strip() == confirm_token(d)


def same_disk(a: Disk, b: Disk) -> bool:
    return (a.path, a.size, a.serial) == (b.path, b.size, b.serial)


def list_disks(platform: str, runner: Callable[[list[str]], bytes] | None = None) -> list[Disk]:
    """Runs the platform command (GUI on the owner's machine only). Never called by tests (CLAUDE.md: no host hardware discovery)."""
    run = runner or (lambda argv: subprocess.run(argv, capture_output=True, check=True, timeout=20).stdout)
    if platform == "linux":
        return parse_lsblk(run(COMMANDS["linux"]).decode())
    if platform == "windows":
        return parse_windows(run(COMMANDS["windows"]).decode("utf-8", "replace"))
    if platform == "darwin":
        lst = run(COMMANDS["darwin"])
        idents = [e["DeviceIdentifier"] for e in plistlib.loads(lst).get("AllDisksAndPartitions", [])]
        infos = {i: run(["diskutil", "info", "-plist", i]) for i in idents}
        return parse_diskutil(lst, infos, run(DARWIN_ALL), run(DARWIN_ROOT))
    raise ValueError(f"unsupported platform {platform}")
