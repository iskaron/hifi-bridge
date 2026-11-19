"""Device I/O for Linux (block device, ``O_EXCL``) and macOS (raw character device ``/dev/rdiskN``, ``F_NOCACHE``).

``os``, ``ioctl`` and ``fcntl`` are injectable: the tests run the real ``os.pread``/``os.pwrite`` on a SIMULATED card (a regular file) with a
fake ``fstat`` and fake ioctls; nothing here is ever pointed at a real device on the development machine (CLAUDE.md).

* Linux: ``open(O_RDWR | O_EXCL)`` fails with EBUSY while anything (a mount, another writer) holds the disk - the kernel's own exclusive
  claim. Size ``BLKGETSIZE64``, logical sector ``BLKSSZGET``; after ``fsync`` the buffer cache is dropped with ``BLKFLSBUF`` and
  ``POSIX_FADV_DONTNEED`` so the read-back reads the card; ``BLKRRPART`` re-reads the partition table at the end.
* macOS: the raw node bypasses the buffer cache; ``F_NOCACHE`` as well. Size = ``DKIOCGETBLOCKCOUNT * DKIOCGETBLOCKSIZE``; flush with
  ``fsync`` + ``DKIOCSYNCHRONIZECACHE``. Exclusivity comes from ``diskutil unmountDisk`` before opening (no ``O_EXCL`` on character devices).
"""
from __future__ import annotations

import os
import stat
import struct

from ..writer import WriteError

BLKGETSIZE64, BLKSSZGET, BLKFLSBUF, BLKRRPART = 0x80081272, 0x1268, 0x1261, 0x125F
DKIOCGETBLOCKSIZE, DKIOCGETBLOCKCOUNT, DKIOCSYNCHRONIZECACHE = 0x40046418, 0x40086419, 0x20006416
F_NOCACHE = 48


def _real_ioctl(fd, req, arg=0):
    import fcntl
    return fcntl.ioctl(fd, req, arg)


def _real_fcntl(fd, cmd, arg=0):
    import fcntl
    return fcntl.fcntl(fd, cmd, arg)


class PosixDeviceIO:
    def __init__(self, path: str, platform: str, osmod=os, ioctl=_real_ioctl, fcntl_fn=_real_fcntl):
        if platform not in ("linux", "darwin"):
            raise ValueError("PosixDeviceIO is for linux or darwin")
        self.path, self.platform, self.os, self.ioctl, self.fcntl = path, platform, osmod, ioctl, fcntl_fn
        flags = osmod.O_RDWR | getattr(osmod, "O_CLOEXEC", 0) | (osmod.O_EXCL if platform == "linux" else 0)
        self.fd = osmod.open(path, flags)
        try:
            mode = osmod.fstat(self.fd).st_mode
            if not (stat.S_ISBLK(mode) if platform == "linux" else stat.S_ISCHR(mode)):
                raise WriteError(f"{path} is not a {'block' if platform == 'linux' else 'raw character'} device")
            if platform == "linux":
                self.size = struct.unpack("=Q", ioctl(self.fd, BLKGETSIZE64, bytes(8)))[0]
                self.sector = struct.unpack("=i", ioctl(self.fd, BLKSSZGET, bytes(4)))[0]
            else:
                self.sector = struct.unpack("=I", ioctl(self.fd, DKIOCGETBLOCKSIZE, bytes(4)))[0]
                self.size = struct.unpack("=Q", ioctl(self.fd, DKIOCGETBLOCKCOUNT, bytes(8)))[0] * self.sector
                fcntl_fn(self.fd, F_NOCACHE, 1)
            if self.sector not in (512, 1024, 2048, 4096) or self.size <= 0:
                raise WriteError(f"{path}: implausible geometry")
        except BaseException:
            osmod.close(self.fd)
            raise

    def pread(self, offset: int, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            chunk = self.os.pread(self.fd, n - len(out), offset + len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def pwrite(self, offset: int, data: bytes) -> None:
        view, done = memoryview(data), 0
        while done < len(data):
            n = self.os.pwrite(self.fd, view[done:], offset + done)
            if n <= 0:
                raise OSError(5, "the card accepted no data")
            done += n

    def sync(self) -> None:
        self.os.fsync(self.fd)
        if self.platform == "darwin":
            self.ioctl(self.fd, DKIOCSYNCHRONIZECACHE)

    def drop_cache(self) -> None:
        if self.platform == "linux":
            self.ioctl(self.fd, BLKFLSBUF)
            self.os.posix_fadvise(self.fd, 0, 0, self.os.POSIX_FADV_DONTNEED)

    def reread_partitions(self) -> None:
        if self.platform == "linux":
            try:
                self.ioctl(self.fd, BLKRRPART)
            except OSError:
                pass                                       # informational only: the next plug-in reads the new table anyway

    def close(self) -> None:
        if self.fd >= 0:
            self.reread_partitions()
            self.os.close(self.fd)
            self.fd = -1
