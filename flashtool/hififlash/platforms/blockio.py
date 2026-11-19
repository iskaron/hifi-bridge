"""A whole disk as a ``writer.Target``, independent of the platform. Raw disks (``/dev/rdiskN``, ``\\\\.\\PhysicalDriveN``) accept only
sector-aligned I/O, so unaligned requests (the last chunk of an image, the provisioning slot) are done as read-modify-write of whole
sectors. ``flush`` writes through and drops the OS cache so the writer's read-back really reads the card."""
from __future__ import annotations

from typing import Protocol

from ..writer import Target, WriteError


class DeviceIO(Protocol):
    size: int
    sector: int

    def pread(self, offset: int, n: int) -> bytes: ...
    def pwrite(self, offset: int, data: bytes) -> None: ...
    def sync(self) -> None: ...
    def drop_cache(self) -> None: ...
    def close(self) -> None: ...


class BlockTarget(Target):
    def __init__(self, io: DeviceIO, path: str, serial: str):
        self.io, self.size = io, io.size
        self._identity = (path, io.size, serial)

    def identity(self) -> tuple:
        return self._identity

    def _span(self, offset: int, n: int) -> tuple[int, int]:
        s = self.io.sector
        start = offset - offset % s
        end = min(-(-(offset + n) // s) * s, self.size)
        return start, end

    def write_at(self, offset: int, data: bytes) -> None:
        if offset < 0 or offset + len(data) > self.size:
            raise WriteError("write beyond the end of the card")
        start, end = self._span(offset, len(data))
        if (start, end) != (offset, offset + len(data)):
            buf = bytearray(self.io.pread(start, end - start))
            if len(buf) != end - start:
                raise WriteError("short read from the card")
            buf[offset - start: offset - start + len(data)] = data
            data, offset = bytes(buf), start
        self.io.pwrite(offset, data)

    def read_at(self, offset: int, n: int) -> bytes:
        start, end = self._span(offset, n)
        raw = self.io.pread(start, end - start)
        return raw[offset - start: offset - start + n]

    def flush(self) -> None:
        self.io.sync()
        self.io.drop_cache()

    def close(self) -> None:
        self.io.close()
