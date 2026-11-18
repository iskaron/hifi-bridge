"""Windows: enumerate with PowerShell ``Get-Disk`` (works unprivileged), then in the UAC-elevated helper lock and dismount every volume of
the card (``FSCTL_LOCK_VOLUME`` + ``FSCTL_DISMOUNT_VOLUME`` on ``\\\\.\\E:``; Windows refuses raw writes into a mounted volume) and write
``\\\\.\\PhysicalDriveN`` with ``FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH`` (sector-aligned I/O, no cache, so the read-back reads
the card). At the end ``IOCTL_DISK_UPDATE_PROPERTIES`` makes Windows re-read the new partition table, then the volume locks are released.

All kernel32 calls go through ``k32`` (``win32api.Kernel32`` on Windows; a recording fake in the tests). NOT executed on Windows yet: the
G3 gate (docs/HARDWARE_GATE_CHECKLIST.md F-1..F-4) must confirm the call sequence on real Windows 11 with real cards.
"""
from __future__ import annotations

import struct
import time

from .. import disks as D
from ..writer import WriteError
from . import run_command
from .blockio import BlockTarget

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING = 1, 2, 3
FILE_FLAG_NO_BUFFERING, FILE_FLAG_WRITE_THROUGH = 0x20000000, 0x80000000
FSCTL_LOCK_VOLUME, FSCTL_UNLOCK_VOLUME, FSCTL_DISMOUNT_VOLUME = 0x00090018, 0x0009001C, 0x00090020
IOCTL_DISK_GET_LENGTH_INFO, IOCTL_DISK_GET_DRIVE_GEOMETRY, IOCTL_DISK_UPDATE_PROPERTIES = 0x0007405C, 0x00070000, 0x00070140
LOCK_ATTEMPTS = 20


class WindowsDeviceIO:
    def __init__(self, k32, path: str):
        self.k32 = k32
        self.h = k32.CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING,
                                FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH)
        try:
            self.size = struct.unpack("<q", k32.DeviceIoControl(self.h, IOCTL_DISK_GET_LENGTH_INFO, None, 8))[0]
            self.sector = struct.unpack_from("<I", k32.DeviceIoControl(self.h, IOCTL_DISK_GET_DRIVE_GEOMETRY, None, 24), 20)[0]
            if self.sector not in (512, 1024, 2048, 4096) or self.size <= 0:
                raise WriteError(f"{path}: implausible geometry")
        except BaseException:
            k32.CloseHandle(self.h)
            raise

    def pread(self, offset: int, n: int) -> bytes:
        return self.k32.ReadFile(self.h, offset, n)

    def pwrite(self, offset: int, data: bytes) -> None:
        if self.k32.WriteFile(self.h, offset, data) != len(data):
            raise OSError(5, "short write to the card")

    def sync(self) -> None:
        self.k32.FlushFileBuffers(self.h)

    def drop_cache(self) -> None:
        pass                                               # FILE_FLAG_NO_BUFFERING: nothing is cached

    def close(self) -> None:
        if self.h is not None:
            try:
                self.k32.DeviceIoControl(self.h, IOCTL_DISK_UPDATE_PROPERTIES, None, 0)
            except OSError:
                pass                                       # informational: Windows re-reads the table on the next plug-in anyway
            self.k32.CloseHandle(self.h)
            self.h = None


class WindowsAdapter:
    platform = "windows"

    def __init__(self, runner=run_command, k32=None, sleep=time.sleep):
        self.run, self.sleep = runner, sleep
        if k32 is None:
            from .win32api import Kernel32
            k32 = Kernel32()
        self.k32 = k32
        self.locked: dict[str, list] = {}

    def list_disks(self) -> list[D.Disk]:
        return D.list_disks("windows", self.run)

    def prepare(self, disk: D.Disk) -> None:
        handles = self.locked.setdefault(disk.path, [])
        for m in disk.mounts:                                          # "E:\\" -> \\.\E:
            h = self.k32.CreateFile("\\\\.\\" + m[:2], GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_EXISTING, 0)
            handles.append(h)
            for attempt in range(LOCK_ATTEMPTS):
                try:
                    self.k32.DeviceIoControl(h, FSCTL_LOCK_VOLUME, None, 0)
                    break
                except OSError:
                    if attempt == LOCK_ATTEMPTS - 1:
                        raise OSError(32, f"volume {m[:2]} is in use (close Explorer windows and programs using the card)") from None
                    self.sleep(0.25)
            self.k32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0)

    def open(self, disk: D.Disk) -> BlockTarget:
        io = WindowsDeviceIO(self.k32, disk.path)
        if io.size != disk.size:
            io.close()
            raise WriteError("the device reports another size than listed - nothing was written")
        return BlockTarget(io, disk.path, disk.serial)

    def finish(self, disk: D.Disk) -> None:
        for h in self.locked.pop(disk.path, []):
            try:
                self.k32.DeviceIoControl(h, FSCTL_UNLOCK_VOLUME, None, 0)
            except OSError:
                pass                                                   # not locked (prepare failed early): closing releases it
            self.k32.CloseHandle(h)
