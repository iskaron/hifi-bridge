"""Getting a bundle onto the data partition: a resumable, streamed HTTPS download and a streamed multipart upload parser.

Both write straight to disk in bounded chunks (a bundle is far larger than the bridge's memory limit), check the free space first, and
leave either a complete file or a clearly partial one (``*.part``) - never a file that looks complete but is not. Integrity and trust are
decided later by RAUC's signature check; the optional SHA-256 from the release metadata only catches transport damage early.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from typing import BinaryIO, Callable

from .. import __version__

CHUNK = 64 * 1024
SPACE_MARGIN = 16 * 1024 * 1024          # keep this much free on the data partition after a bundle has been stored


class TransferError(Exception):
    """``code`` is shown to the owner (translated): unreachable, http_error, size_mismatch, digest_mismatch, too_large, no_space,
    cancelled, interrupted, bad_request, truncated, too_many_parts, field_too_large, no_file."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{(': ' + detail) if detail else ''}")
        self.code, self.detail = code, detail


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def ensure_space(directory: str, needed: int, free: Callable[[str], int] = free_bytes) -> None:
    if needed + SPACE_MARGIN > free(directory):
        raise TransferError("no_space", f"{needed} bytes needed")


def _fsync_dir(d: str) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ===================================================================================================== download
class Download:
    """Resumable download of ``url`` to ``dest`` (via ``dest + '.part'`` and a small ``.part.json`` with the URL and expected size).

    A second run for the same URL continues with an HTTP Range request; a server that ignores the range (200) restarts from zero.
    ``progress(done, total)`` is called per chunk; ``cancel`` (an Event) stops cleanly and keeps the partial file for a later resume."""

    def __init__(self, url: str, dest: str, size: int, sha256: str | None = None, max_size: int = 1 << 30,
                 open_url: Callable | None = None, timeout: float = 30.0, free: Callable[[str], int] = free_bytes,
                 progress: Callable[[int, int], None] = lambda d, t: None, cancel: threading.Event | None = None,
                 allow_http: bool = False):
        from .source import check_url, opener
        check_url(url, allow_http)
        self.url, self.dest, self.size, self.sha256, self.max_size = url, dest, int(size), sha256, max_size
        self.part, self.meta = dest + ".part", dest + ".part.json"
        self._open = open_url or opener(allow_http).open
        self.timeout, self.free, self.progress = timeout, free, progress
        self.cancel = cancel or threading.Event()

    def _resume_offset(self) -> int:
        try:
            with open(self.meta, encoding="utf-8") as fh:
                m = json.load(fh)
            have = os.path.getsize(self.part)
        except (OSError, ValueError):
            return 0
        if m.get("url") == self.url and m.get("size") == self.size and 0 < have <= self.size:
            return have
        return 0

    def run(self) -> str:
        if self.size <= 0 or self.size > self.max_size:
            raise TransferError("too_large", str(self.size))
        d = os.path.dirname(self.dest) or "."
        os.makedirs(d, exist_ok=True)
        offset = self._resume_offset()
        if offset == 0:
            for p in (self.part, self.meta):
                if os.path.exists(p):
                    os.unlink(p)
        ensure_space(d, self.size - offset, self.free)
        with open(self.meta, "w", encoding="utf-8") as fh:
            json.dump({"url": self.url, "size": self.size}, fh)
        headers = {"User-Agent": f"hifi-bridge/{__version__}", "Accept": "application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            resp = self._open(urllib.request.Request(self.url, headers=headers), timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 416 and offset == self.size:          # already complete
                return self._finish()
            raise TransferError("http_error", str(e.code)) from None
        except (urllib.error.URLError, OSError) as e:
            raise TransferError("unreachable", type(e).__name__) from None
        with resp:
            status = getattr(resp, "status", 200)
            if offset and status == 206:
                m = re.match(r"bytes (\d+)-", resp.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != offset:
                    raise TransferError("http_error", "bad Content-Range")
                mode = "ab"
            elif status in (200, 206):
                offset, mode = 0, "wb"                          # no (usable) range support: start over
            else:
                raise TransferError("http_error", str(status))
            length = resp.headers.get("Content-Length")
            if length is not None and offset + int(length) != self.size:
                raise TransferError("size_mismatch", f"server announces {offset + int(length)}, release says {self.size}")
            done = offset
            with open(self.part, mode) as out:
                while True:
                    if self.cancel.is_set():
                        out.flush(); os.fsync(out.fileno())
                        raise TransferError("cancelled")
                    try:
                        block = resp.read(CHUNK)
                    except OSError as e:                        # connection dropped: keep the partial file for a resume
                        out.flush(); os.fsync(out.fileno())
                        raise TransferError("interrupted", type(e).__name__) from None
                    if not block:
                        break
                    done += len(block)
                    if done > self.size:
                        raise TransferError("size_mismatch", "more data than announced")
                    out.write(block)
                    self.progress(done, self.size)
                out.flush()
                os.fsync(out.fileno())
        if done != self.size:
            raise TransferError("interrupted", f"{done} of {self.size} bytes")
        return self._finish()

    def _finish(self) -> str:
        if self.sha256 and sha256_file(self.part) != self.sha256.lower():
            os.unlink(self.part)
            os.unlink(self.meta)
            raise TransferError("digest_mismatch")
        os.replace(self.part, self.dest)
        os.unlink(self.meta)
        _fsync_dir(os.path.dirname(self.dest) or ".")
        return self.dest


# ===================================================================================================== multipart upload
MAX_HEADER = 8 * 1024
MAX_FIELD = 4 * 1024
MAX_PARTS = 8


def boundary_of(content_type: str) -> bytes:
    m = re.match(r'^multipart/form-data;.*?\bboundary=(?:"([^"]{1,70})"|([^\s;"]{1,70}))(?=;|\s|$)', content_type or "", re.I)
    if not m:
        raise TransferError("bad_request", "not multipart/form-data")
    return (m.group(1) or m.group(2)).encode("latin-1")


def _disposition(headers: bytes) -> tuple[str, str | None]:
    text = headers.decode("utf-8", "replace")
    m = re.search(r'^content-disposition:\s*form-data(.*)$', text, re.I | re.M)
    if not m:
        raise TransferError("bad_request", "part without Content-Disposition")
    params = dict((k.lower(), v) for k, v in re.findall(r';\s*([A-Za-z*]+)="([^"]*)"', m.group(1)))
    if "name" not in params:
        raise TransferError("bad_request", "part without name")
    return params["name"], params.get("filename")


def parse_multipart(read: Callable[[int], bytes], boundary: bytes, open_file: Callable[[str, str], BinaryIO],
                    progress: Callable[[int], None] = lambda n: None) -> dict[str, str]:
    """Stream a multipart/form-data body. Text fields are returned (each at most 4 KiB); every file part is written to the sink that
    ``open_file(field, filename)`` returns. ``open_file`` is called only after all PRECEDING fields were read, so the caller can check
    the CSRF token (sent first by the form) before a single byte of the file is stored. ``read(n)`` must return b"" at the end."""
    delim = b"--" + boundary
    sep = b"\r\n" + delim
    buf = b""
    received = 0

    def more() -> bool:
        nonlocal buf, received
        block = read(CHUNK)
        if not block:
            return False
        received += len(block)
        progress(received)
        buf += block
        return True

    while delim not in buf:                                         # preamble
        if len(buf) > MAX_HEADER or not more():
            raise TransferError("bad_request", "no multipart boundary")
    buf = buf[buf.index(delim) + len(delim):]
    fields: dict[str, str] = {}
    parts = 0
    while True:
        while len(buf) < 2:
            if not more():
                raise TransferError("truncated")
        if buf[:2] == b"--":                                        # closing delimiter: drain the epilogue (bounded by the caller's
            while more():                                           # Content-Length), so nothing is left unread on the connection
                buf = b""
            return fields
        if buf[:2] != b"\r\n":
            raise TransferError("bad_request", "malformed delimiter")
        parts += 1
        if parts > MAX_PARTS:
            raise TransferError("too_many_parts")
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HEADER or not more():
                raise TransferError("bad_request" if len(buf) > MAX_HEADER else "truncated")
        head, buf = buf[2:].split(b"\r\n\r\n", 1)
        name, filename = _disposition(head)
        sink = open_file(name, filename) if filename is not None else None
        value = b""
        while True:
            i = buf.find(sep)
            if i >= 0:
                chunk, buf = buf[:i], buf[i + len(sep):]
            else:
                keep = len(sep) - 1                                 # a delimiter may straddle two reads
                chunk, buf = (buf[:-keep], buf[-keep:]) if len(buf) > keep else (b"", buf)
            if sink is not None:
                sink.write(chunk)
            else:
                value += chunk
                if len(value) > MAX_FIELD:
                    raise TransferError("field_too_large", name)
            if i >= 0:
                break
            if not more():
                raise TransferError("truncated")
        if sink is None:
            fields[name] = value.decode("utf-8", "replace")
