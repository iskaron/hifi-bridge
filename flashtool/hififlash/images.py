"""Cryptographic image verification before anything is written (P9, docs/FLASH_TOOL.md §3).

Order: the manifest must parse and carry a signature by a **trusted** publisher key (official, or custom after explicit addition); it must
be for the chosen board; then the image (raw, .xz or .gz) is streamed once and its size and SHA-256 must match the manifest. The writer
checks the hash again while writing and reads the card back, so this pre-check is for a clear UI state ("verified, signed by ...") and to
avoid touching a card with a damaged download.

``ImageError.code`` is what the GUI translates: unsigned | untrusted_signature | bad_manifest | wrong_board | hash_mismatch | size_mismatch |
unreadable | cancelled.
"""
from __future__ import annotations

import hashlib
import threading
import zlib
import lzma
from dataclasses import dataclass
from typing import Callable

from . import boards as BD
from . import manifest as M
from .keystore import KeyStore, PublisherKey
from .writer import open_image

CHUNK = 4 << 20


class ImageError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class VerifiedImage:
    path: str
    manifest: dict
    manifest_raw: bytes
    publisher: PublisherKey
    board: BD.Board


def check_manifest(raw: bytes, sig: bytes | None, keys: KeyStore, board: BD.Board) -> tuple[dict, PublisherKey]:
    try:
        m = M.load(raw)
    except M.ManifestError as e:
        raise ImageError("bad_manifest", str(e)) from None
    if not sig:
        raise ImageError("unsigned")
    try:
        publisher = keys.verify(raw, sig)
    except M.ManifestError as e:
        raise ImageError("untrusted_signature", str(e)) from None
    if m["compatible"] != board.compatible:
        raise ImageError("wrong_board", m["compatible"])
    return m, publisher


def hash_image(path: str, m: dict, cancel: threading.Event | None = None,
               progress: Callable[[int, int], None] = lambda done, total: None) -> None:
    cancel = cancel or threading.Event()
    h, n = hashlib.sha256(), 0
    try:
        with open_image(path) as fh:
            while True:
                if cancel.is_set():
                    raise ImageError("cancelled")
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                n += len(chunk)
                if n > m["size"]:
                    raise ImageError("size_mismatch")
                h.update(chunk)
                progress(n, m["size"])
    except (OSError, EOFError, lzma.LZMAError, zlib.error) as e:
        raise ImageError("unreadable", str(e)) from None
    if n != m["size"]:
        raise ImageError("size_mismatch")
    if h.hexdigest() != m["sha256"]:
        raise ImageError("hash_mismatch")


def verify_image(path: str, manifest_raw: bytes, sig: bytes | None, keys: KeyStore, board: BD.Board,
                 cancel: threading.Event | None = None, progress: Callable[[int, int], None] = lambda done, total: None) -> VerifiedImage:
    m, publisher = check_manifest(manifest_raw, sig, keys, board)
    hash_image(path, m, cancel, progress)
    return VerifiedImage(path, m, manifest_raw, publisher, board)
