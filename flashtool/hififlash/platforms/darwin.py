"""macOS: enumerate with ``diskutil list -plist physical`` + ``diskutil info -plist``, unmount all volumes of the card with
``diskutil unmountDisk`` (the disk and every APFS container stored on it; helper, root; then re-checked), then write the raw node ``/dev/rdiskN`` (unbuffered, much faster than ``/dev/diskN``)."""
from __future__ import annotations

from .. import disks as D
from ..writer import WriteError
from . import run_command
from .blockio import BlockTarget


class MacAdapter:
    platform = "darwin"

    def __init__(self, runner=run_command, io_factory=None):
        from .posixio import PosixDeviceIO
        self.run = runner
        self.io_factory = io_factory or (lambda path: PosixDeviceIO(path, "darwin"))

    def list_disks(self) -> list[D.Disk]:
        return D.list_disks("darwin", self.run)

    def prepare(self, disk: D.Disk) -> None:
        # APFS volumes are mounted from synthesized container disks whose physical store is on the card: unmount those too (review M11)
        for container in D.apfs_containers_on(self.run(D.DARWIN_ALL), disk.name):
            self.run(["diskutil", "unmountDisk", "/dev/" + container])
        self.run(["diskutil", "unmountDisk", disk.path])
        still = [m for d in self.list_disks() if d.path == disk.path for m in d.mounts]
        if still:
            raise OSError(16, "still mounted: " + ", ".join(still))

    def open(self, disk: D.Disk) -> BlockTarget:
        io = self.io_factory("/dev/r" + disk.path[len("/dev/"):])
        if io.size != disk.size:
            io.close()
            raise WriteError("the device reports another size than listed - nothing was written")
        return BlockTarget(io, disk.path, disk.serial)

    def finish(self, disk: D.Disk) -> None:
        pass
