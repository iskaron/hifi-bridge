"""Locate a file's byte range inside partition 1 (FAT12/16/32) of an MBR disk image, so the provisioning slot can be written in place on
a raw card without mounting anything. Read-only; only contiguous files are accepted (a fresh image always has them)."""
from __future__ import annotations

import struct
from typing import Callable

Reader = Callable[[int, int], bytes]


class SlotError(Exception):
    pass


def mbr_partitions(read: Reader) -> list[tuple[int, int, int]]:
    mbr = read(0, 512)
    if mbr[510:512] != b"\x55\xaa":
        raise SlotError("no MBR signature")
    parts = []
    for i in range(4):
        e = mbr[446 + 16 * i: 446 + 16 * (i + 1)]
        ptype, start, count = e[4], struct.unpack_from("<I", e, 8)[0], struct.unpack_from("<I", e, 12)[0]
        if ptype:
            parts.append((ptype, start, count))
    return parts


def _short_name(e: bytes) -> str:
    base, ext = e[0:8].decode("latin-1").rstrip(), e[8:11].decode("latin-1").rstrip()
    return f"{base}.{ext}" if ext else base


def find_file(read: Reader, name: str, partition: int = 0) -> tuple[int, int]:
    """(absolute byte offset, size) of ``name`` in the root directory of the FAT partition."""
    ptype, start_lba, _ = mbr_partitions(read)[partition]
    base = start_lba * 512
    bs = read(base, 512)
    bps, spc = struct.unpack_from("<H", bs, 11)[0], bs[13]
    reserved, nfats, root_entries = struct.unpack_from("<H", bs, 14)[0], bs[16], struct.unpack_from("<H", bs, 17)[0]
    total = struct.unpack_from("<H", bs, 19)[0] or struct.unpack_from("<I", bs, 32)[0]
    fatsz = struct.unpack_from("<H", bs, 22)[0] or struct.unpack_from("<I", bs, 36)[0]
    if bps not in (512, 1024, 2048, 4096) or spc == 0 or nfats == 0:
        raise SlotError("not a FAT filesystem")
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_start = reserved + nfats * fatsz + root_sectors
    clusters = (total - data_start) // spc
    kind = 12 if clusters < 4085 else 16 if clusters < 65525 else 32
    fat_off = base + reserved * bps

    def next_cluster(c: int) -> int:
        if kind == 12:
            v = struct.unpack("<H", read(fat_off + c + c // 2, 2))[0]
            return (v >> 4) if c & 1 else (v & 0xFFF)
        if kind == 16:
            return struct.unpack("<H", read(fat_off + 2 * c, 2))[0]
        return struct.unpack("<I", read(fat_off + 4 * c, 4))[0] & 0x0FFFFFFF

    eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFF8}[kind]
    cluster_bytes = spc * bps

    def cluster_offset(c: int) -> int:
        return base + (data_start + (c - 2) * spc) * bps

    if kind == 32:
        chunks, c = [], struct.unpack_from("<I", bs, 44)[0]
        while 2 <= c < eoc:
            chunks.append(read(cluster_offset(c), cluster_bytes))
            c = next_cluster(c)
        root = b"".join(chunks)
    else:
        root = read(base + (reserved + nfats * fatsz) * bps, root_entries * 32)

    lfn: list[str] = []
    for i in range(0, len(root), 32):
        e = root[i:i + 32]
        if e[0] == 0:
            break
        if e[0] == 0xE5:
            lfn = []
            continue
        if e[11] == 0x0F:
            part = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace").split("\x00")[0].replace("￿", "")
            lfn = [part] + lfn if not (e[0] & 0x40) else [part]
            continue
        long_name, lfn = "".join(lfn), []
        if e[11] & 0x18:                                   # volume label or directory
            continue
        if name.lower() not in (long_name.lower(), _short_name(e).lower()):
            continue
        first = (struct.unpack_from("<H", e, 20)[0] << 16 | struct.unpack_from("<H", e, 26)[0]) if kind == 32 else struct.unpack_from("<H", e, 26)[0]
        size = struct.unpack_from("<I", e, 28)[0]
        c, n = first, 1
        while True:
            nxt = next_cluster(c)
            if nxt >= eoc:
                break
            if nxt != c + 1:
                raise SlotError(f"{name} is fragmented")
            c, n = nxt, n + 1
        if n * cluster_bytes < size:
            raise SlotError(f"{name}: cluster chain shorter than the file")
        return cluster_offset(first), size
    raise SlotError(f"{name} not found in the root directory")


def file_reader(path: str) -> Reader:
    def read(off: int, n: int) -> bytes:
        with open(path, "rb") as fh:
            fh.seek(off)
            return fh.read(n)
    return read
