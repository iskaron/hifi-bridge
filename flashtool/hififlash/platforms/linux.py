"""Linux: enumerate with ``lsblk --json`` (works unprivileged), unmount every mounted partition of the card with ``umount`` (helper, root),
then open the whole disk with ``O_EXCL`` - the kernel refuses that while anything still holds the disk."""
from __future__ import annotations

import json

from .. import disks as D
from ..writer import WriteError
from . import run_command
from .blockio import BlockTarget


class LinuxAdapter:
    platform = "linux"

    def __init__(self, runner=run_command, io_factory=None):
        from .posixio import PosixDeviceIO
        self.run = runner
        self.io_factory = io_factory or (lambda path: PosixDeviceIO(path, "linux"))

    def list_disks(self) -> list[D.Disk]:
        return D.list_disks("linux", self.run)

    def _mounted(self, disk: D.Disk) -> list[tuple[str, list[str]]]:
        """(device path, mount points) of the disk itself and its partitions that are mounted."""
        tree = json.loads(self.run(D.COMMANDS["linux"]).decode())
        out = []
        for d in tree.get("blockdevices", []):
            if d.get("path") != disk.path:
                continue
            for node in D.walk(d):                                  # the whole tree (review M11)
                mounts = [m for m in (node.get("mountpoints") or []) if m]
                if mounts:
                    out.append((node.get("path") or "/dev/" + node["name"], mounts))
        return out

    def prepare(self, disk: D.Disk) -> None:
        for dev, _ in self._mounted(disk):
            self.run(["umount", dev])
        still = self._mounted(disk)
        if still:
            raise OSError(16, "still mounted: " + ", ".join(m for _, ms in still for m in ms))

    def open(self, disk: D.Disk) -> BlockTarget:
        io = self.io_factory(disk.path)
        if io.size != disk.size:
            io.close()
            raise WriteError("the device reports another size than listed - nothing was written")
        return BlockTarget(io, disk.path, disk.serial)

    def finish(self, disk: D.Disk) -> None:
        pass
