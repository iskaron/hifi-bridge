"""Write + verify + provision a card (P9). Works against the ``Target`` interface; this package ships only ``FileTarget`` (a simulated card =
a regular file of fixed size). A real block-device target is a later GUI deliverable and is NOT part of this package.

Sequence (each step can be cancelled; every failure or cancel INVALIDATES the card so a half-written image never boots):
  1. identity re-check of the chosen disk (the user may have swapped cards since selecting);
  2. stream the image (raw / .xz / .gz) in chunks, hashing on the fly; size and SHA-256 must match the signed manifest;
  3. flush, then read the written range back and hash it again (catches fake-capacity cards and write errors);
  4. write the provisioning document into the slot (fixed offset from the manifest) and read it back;
  5. flush. Done.
Invalidate = overwrite the first 4 MiB (partition table + start of the boot partition) with zeros and flush.
"""
from __future__ import annotations

import gzip
import hashlib
import lzma
import os
import threading
from typing import BinaryIO, Callable

CHUNK = 4 << 20
INVALIDATE_BYTES = 4 << 20


class WriteError(Exception):
    pass


class Cancelled(WriteError):
    pass


class Target:
    """Interface of a writable card."""
    size: int

    def identity(self) -> tuple:
        raise NotImplementedError

    def write_at(self, offset: int, data: bytes) -> None:
        raise NotImplementedError

    def read_at(self, offset: int, n: int) -> bytes:
        raise NotImplementedError

    def flush(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class FileTarget(Target):
    """A SIMULATED card: an existing regular file of fixed size. Refuses device nodes and anything that is not a regular file."""

    def __init__(self, path: str, serial: str = "SIM", fail_after: int | None = None, lie_above: int | None = None,
                 fail_always: bool = False):
        real = os.path.realpath(path)
        if real.startswith(("/dev/", "/sys/", "/proc/")) or real.lower().startswith("\\\\.\\") or not os.path.isfile(real):
            raise WriteError(f"FileTarget only accepts an existing regular file, not {path}")
        self.path, self.serial, self.size = real, serial, os.path.getsize(real)
        self.fail_after, self.lie_above, self.written = fail_after, lie_above, 0   # test hooks: I/O error / fake-capacity card
        self.fail_always = fail_always                                              # the card stays broken (invalidation fails too)
        self.fh = open(real, "r+b")

    def identity(self) -> tuple:
        return (self.path, self.size, self.serial)

    def write_at(self, offset: int, data: bytes) -> None:
        if offset + len(data) > self.size:
            raise WriteError("write beyond the end of the card")
        if self.fail_after is not None and self.written + len(data) > self.fail_after:
            self.fail_after = None if not self.fail_always else self.fail_after
            raise OSError(5, "simulated I/O error")
        if self.lie_above is not None and offset + len(data) > self.lie_above:   # fake card: silently drops data above its real size
            keep = max(0, self.lie_above - offset)
            data = data[:keep] + bytes(len(data) - keep)
        self.fh.seek(offset)
        self.fh.write(data)
        self.written += len(data)

    def read_at(self, offset: int, n: int) -> bytes:
        self.fh.seek(offset)
        return self.fh.read(n)

    def flush(self) -> None:
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def close(self) -> None:
        self.fh.close()


def open_image(path: str) -> BinaryIO:
    with open(path, "rb") as fh:
        magic = fh.read(6)
    if magic.startswith(b"\xfd7zXZ"):
        return lzma.open(path, "rb")
    if magic.startswith(b"\x1f\x8b"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def invalidate(t: Target) -> None:
    n = min(INVALIDATE_BYTES, t.size)
    t.write_at(0, bytes(n))
    t.flush()


def write_card(image: BinaryIO, manifest: dict, target: Target, expected_identity: tuple, provisioning: bytes | None,
               cancel: threading.Event | None = None, progress: Callable[[str, int, int], None] = lambda *a: None) -> dict:
    cancel = cancel or threading.Event()
    size, slot = manifest["size"], manifest["slot"]
    if target.identity() != expected_identity:
        raise WriteError("the selected card is no longer the same device - nothing was written")
    if size > target.size:
        raise WriteError("the image is larger than the card - nothing was written")
    if provisioning is not None and len(provisioning) != slot["size"]:
        raise WriteError("provisioning data does not match the slot size - nothing was written")
    started = False
    try:
        h, pos = hashlib.sha256(), 0
        while True:
            if cancel.is_set():
                raise Cancelled("cancelled while writing")
            chunk = image.read(CHUNK)
            if not chunk:
                break
            if pos + len(chunk) > size:
                raise WriteError("the image is longer than the manifest says")
            started = True
            target.write_at(pos, chunk)
            h.update(chunk)
            pos += len(chunk)
            progress("write", pos, size)
        if pos != size or h.hexdigest() != manifest["sha256"]:
            raise WriteError("the image does not match its signed manifest (damaged or modified download)")
        target.flush()
        h2, pos = hashlib.sha256(), 0
        while pos < size:
            if cancel.is_set():
                raise Cancelled("cancelled while verifying")
            n = min(CHUNK, size - pos)
            h2.update(target.read_at(pos, n))
            pos += n
            progress("verify", pos, size)
        if h2.hexdigest() != manifest["sha256"]:
            raise WriteError("read-back verification failed (faulty or fake-capacity card)")
        if provisioning is not None:
            target.write_at(slot["offset"], provisioning)
            target.flush()
            if target.read_at(slot["offset"], slot["size"]) != provisioning:
                raise WriteError("provisioning data could not be verified on the card")
        target.flush()
        return {"result": "ok", "bytes": size, "sha256": manifest["sha256"], "provisioned": provisioning is not None}
    except (WriteError, OSError) as e:
        note = ""
        if started:
            try:
                invalidate(target)
                note = " - the card was made unbootable; write it again"
            except (OSError, WriteError):
                note = " - the card could NOT be made unbootable: do not use it, re-write or discard it"
        if isinstance(e, Cancelled):
            raise Cancelled(f"{e}{note}") from e
        raise WriteError(f"{e}{note}") from e
