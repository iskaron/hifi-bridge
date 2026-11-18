"""SIMULATED disks for tests, screenshots and the package smoke test: ``<dir>/<name>.card`` regular files (sparse) with optional
``<name>.card.json`` metadata (model, serial, transport, removable, readonly, system, mounts, busy, fail_after, lie_above). Writing goes
through the core's ``FileTarget``, which refuses anything that is not a regular file - this adapter can never touch a device."""
from __future__ import annotations

import json
import os

from .. import disks as D
from ..writer import FileTarget

META_KEYS = {"name", "model", "serial", "transport", "removable", "readonly", "system", "mounts", "busy", "fail_after", "lie_above"}


def create(directory: str, stem: str, size: int, **meta) -> str:
    """Create a simulated card (sparse file) and its metadata; returns the card's path."""
    unknown = set(meta) - META_KEYS
    if unknown:
        raise ValueError(f"unknown simulated-card fields {sorted(unknown)}")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, stem + ".card")
    with open(path, "wb") as fh:
        fh.truncate(size)
    with open(path + ".json", "w") as fh:
        json.dump(meta, fh)
    return os.path.realpath(path)


class SimulatedAdapter:
    platform = "simulated"

    def __init__(self, directory: str):
        self.dir = directory
        self.calls: list[tuple[str, str]] = []

    def _meta(self, path: str) -> dict:
        try:
            with open(path + ".json") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def list_disks(self) -> list[D.Disk]:
        out = []
        for n in sorted(os.listdir(self.dir)):
            if not n.endswith(".card"):
                continue
            p = os.path.realpath(os.path.join(self.dir, n))
            m = self._meta(p)
            out.append(D.Disk(path=p, name=m.get("name", n[:-5]), size=os.path.getsize(p), model=m.get("model", "Simulated SD card"),
                              serial=m.get("serial", "SIM-" + n[:-5]), transport=m.get("transport", "sd"),
                              removable=m.get("removable", True), readonly=m.get("readonly", False), system=m.get("system", False),
                              mounts=tuple(m.get("mounts", ()))))
        return out

    def prepare(self, disk: D.Disk) -> None:
        self.calls.append(("prepare", disk.path))
        m = self._meta(disk.path)
        if m.get("busy"):
            raise OSError(16, "target is busy (simulated)")

    def open(self, disk: D.Disk) -> FileTarget:
        self.calls.append(("open", disk.path))
        m = self._meta(disk.path)
        return FileTarget(disk.path, serial=disk.serial, fail_after=m.get("fail_after"), lie_above=m.get("lie_above"))

    def finish(self, disk: D.Disk) -> None:
        self.calls.append(("finish", disk.path))
