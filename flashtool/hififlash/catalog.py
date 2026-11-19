"""Image catalogue and download management (P9, DECISIONS #70).

Official images are published as GitHub Release assets of ``iskaron/hifi-bridge``: for every image ``<base>`` (``hifi-bridge-<compatible>-
<version>.img``) a release carries ``<base>`` or ``<base>.xz``/``<base>.gz``, ``<base>.manifest.json`` and ``<base>.manifest.json.sig``.
The **source is transport only**: trust comes from the manifest signature (``keystore``), never from where the bytes came from. The source
URL is configurable (a mirror or a test server speaking the same GitHub Releases JSON); the repository is private today, so the default
source answers 404 and the GUI says so clearly (``SourceError.code``).

Downloads stream into a cache directory as ``<name>.part`` and resume with an HTTP ``Range`` request (``If-Range`` with the ETag, so a
changed file restarts from zero). Cancel keeps the partial file for a later resume. Everything that comes from the network is untrusted:
asset names must be plain file names, JSON documents and manifests are size-limited.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

DEFAULT_SOURCE = "https://api.github.com/repos/iskaron/hifi-bridge/releases"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
MAX_JSON = 4 << 20
MAX_MANIFEST = 64 << 10
MAX_SIG = 4 << 10
CHUNK = 1 << 20
IMAGE_SUFFIXES = ("", ".xz", ".gz")
USER_AGENT = "hifi-bridge-flash"

Opener = Callable[..., object]


class SourceError(Exception):
    """``code``: unreachable | not_found_or_private | forbidden | http_error | bad_response | bad_url."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Entry:
    """One image of one release (not yet verified - the manifest and its signature are fetched with ``fetch_manifest``)."""
    release: str
    prerelease: bool
    published: str
    image: Asset
    manifest: Asset
    signature: Asset | None


def check_url(url: str) -> str:
    p = urllib.parse.urlsplit(url or "")
    if p.scheme not in ("https", "http") or not p.netloc:
        raise SourceError("bad_url", "only http(s) URLs are accepted")
    return url


def _get(url: str, opener: Opener, timeout: float, limit: int, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(check_url(url), headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with opener(req, timeout=timeout) as r:
            body = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        code = {404: "not_found_or_private", 401: "forbidden", 403: "forbidden"}.get(e.code, "http_error")
        raise SourceError(code, f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise SourceError("unreachable", str(getattr(e, "reason", e))) from None
    if len(body) > limit:
        raise SourceError("bad_response", "response too large")
    return body


def list_entries(source: str = DEFAULT_SOURCE, opener: Opener = urllib.request.urlopen, timeout: float = 20) -> list[Entry]:
    raw = _get(source, opener, timeout, MAX_JSON, {"Accept": "application/vnd.github+json"})
    try:
        releases = json.loads(raw)
        if not isinstance(releases, list):
            raise ValueError("not a list")
        out = []
        for rel in releases:
            if rel.get("draft"):
                continue
            assets = {}
            for a in rel.get("assets") or []:
                name, url, size = a["name"], str(a["browser_download_url"]), int(a["size"])
                if NAME_RE.match(name) and size >= 0 and url.startswith(("https://", "http://")):   # anything else: ignored
                    assets[name] = Asset(name, url, size)
            for name in sorted(assets):
                if not name.endswith(".manifest.json"):
                    continue
                base = name[: -len(".manifest.json")]
                image = next((assets[base + s] for s in IMAGE_SUFFIXES if base + s in assets), None)
                if image is None:
                    continue
                out.append(Entry(str(rel.get("tag_name") or ""), bool(rel.get("prerelease")), str(rel.get("published_at") or ""),
                                 image, assets[name], assets.get(name + ".sig")))
        return out
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceError("bad_response", str(e)) from None


def fetch_manifest(entry: Entry, opener: Opener = urllib.request.urlopen, timeout: float = 20) -> tuple[bytes, bytes | None]:
    """(manifest bytes, signature bytes or None) - small documents, size-limited."""
    raw = _get(entry.manifest.url, opener, timeout, MAX_MANIFEST)
    sig = _get(entry.signature.url, opener, timeout, MAX_SIG) if entry.signature else None
    return raw, sig


def _read_meta(path: str) -> dict:
    try:
        with open(path) as fh:
            m = json.load(fh)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


class Downloader:
    def __init__(self, cache_dir: str, opener: Opener = urllib.request.urlopen, timeout: float = 30):
        self.cache_dir, self.opener, self.timeout = cache_dir, opener, timeout

    def path_for(self, name: str) -> str:
        if not NAME_RE.match(name or ""):
            raise DownloadError("refusing an unsafe file name")
        return os.path.join(self.cache_dir, name)

    def cached(self, asset: Asset) -> str | None:
        p = self.path_for(asset.name)
        return p if os.path.isfile(p) and os.path.getsize(p) == asset.size else None

    def fetch(self, asset: Asset, cancel: threading.Event | None = None,
              progress: Callable[[int, int], None] = lambda done, total: None) -> str:
        cancel = cancel or threading.Event()
        final = self.path_for(asset.name)
        if self.cached(asset):
            progress(asset.size, asset.size)
            return final
        os.makedirs(self.cache_dir, exist_ok=True)
        part, metaf = final + ".part", final + ".part.json"
        meta = _read_meta(metaf)
        start = os.path.getsize(part) if os.path.isfile(part) and meta.get("url") == asset.url else 0
        if start > asset.size:
            start = 0
        headers = {"User-Agent": USER_AGENT}
        if start:
            headers["Range"] = f"bytes={start}-"
            if meta.get("etag"):
                headers["If-Range"] = meta["etag"]
        req = urllib.request.Request(check_url(asset.url), headers=headers)
        try:
            resp = self.opener(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 416 and start == asset.size:
                return self._finish(part, metaf, final, asset)
            if e.code == 416:
                os.remove(part)                              # our partial file does not fit the server's file: start over next time
            raise DownloadError(f"download failed: HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise DownloadError(f"download failed: {getattr(e, 'reason', e)}") from None
        with resp:
            status = getattr(resp, "status", 200)
            if status != 206:
                start = 0                                    # the server ignored the range or the file changed: start over
            with open(metaf, "w") as fh:
                json.dump({"url": asset.url, "etag": resp.headers.get("ETag", "")}, fh)
            done = start
            with open(part, "r+b" if start else "wb") as out:
                out.seek(start)
                out.truncate()
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled("download cancelled (it resumes next time)")
                    try:
                        chunk = resp.read(CHUNK)
                    except OSError as e:
                        raise DownloadError(f"download interrupted: {e} (it resumes next time)") from None
                    if not chunk:
                        break
                    if done + len(chunk) > asset.size:
                        raise DownloadError("the server sent more data than announced")
                    out.write(chunk)
                    done += len(chunk)
                    progress(done, asset.size)
        if done != asset.size:
            raise DownloadError("download incomplete (it resumes next time)")
        return self._finish(part, metaf, final, asset)

    @staticmethod
    def _finish(part: str, metaf: str, final: str, asset: Asset) -> str:
        os.replace(part, final)                              # both callers checked that the partial file has the announced size
        os.remove(metaf)                                     # exists on both paths that reach here
        return final
