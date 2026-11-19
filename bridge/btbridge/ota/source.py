"""Manual update check against the FIXED official source in ``/etc/hifi-bridge/update-trust.json`` (DECISIONS #70, docs/UPDATE_TRUST.md
section 4a): the GitHub Releases API of ``iskaron/hifi-bridge``.

* The check only REPORTS a newer release; nothing is downloaded or installed without the owner (DECISIONS #10).
* The source is transport only: whatever it delivers is installed only if RAUC verifies the signature against the keyring.
* No credentials are ever sent. While the repository is private, the API answers 404 to anonymous requests; this is reported as
  ``not_public`` (OPEN_QUESTIONS #45), not as an error of the device.
* Images with ``own`` trust have no official source (``official_source: null``): updates come by upload only.

Release convention (also used by ``scripts/os_bundle.sh``): one asset per board named ``hifi-bridge-<compatible>-<version>.raucb``.
Drafts and pre-releases are ignored.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Callable

from .. import __version__

MAX_JSON = 2 * 1024 * 1024
_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+~]?([0-9A-Za-z.\-]+))?$")


def parse_version(text: str) -> tuple | None:
    """Sortable key for versions like ``0.4.0``, ``v1.2``, ``0.3.0-dev``, ``1.0.0-rc2``. A pre-release sorts before its release."""
    m = _VERSION.match((text or "").strip())
    if not m:
        return None
    main = tuple(int(x or 0) for x in m.group(1, 2, 3))
    pre = m.group(4)
    if not pre:
        return (main, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-]", pre) if p)
    return (main, 0, parts)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def load_trust(path: str = "/etc/hifi-bridge/update-trust.json") -> dict | None:
    """The image's update trust record (post-build.sh), or None on a development host without one."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect away from HTTPS (unless plain HTTP was explicitly allowed for a local test server)."""

    def __init__(self, allow_http: bool = False):
        self.allow_http = allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://") and not (self.allow_http and newurl.startswith("http://")):
            raise urllib.error.URLError("refusing redirect to a non-HTTPS location")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(allow_http: bool = False) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(HttpsOnlyRedirect(allow_http))


def check_url(url: str, allow_http: bool = False) -> None:
    if not (url.startswith("https://") or (allow_http and url.startswith("http://"))):
        raise ValueError("insecure_source")


class UpdateSource:
    """``source`` is the ``official_source`` object from update-trust.json (or None). ``allow_http`` exists for local test servers only."""

    def __init__(self, source: dict | None, allow_http: bool = False, timeout: float = 15.0, clock: Callable[[], float] = time.time,
                 open_url: Callable | None = None):
        self.source, self.allow_http, self.timeout, self.clock = source, allow_http, timeout, clock
        self._open = open_url or opener(allow_http).open

    @property
    def available(self) -> bool:
        return bool(self.source and self.source.get("type") == "github-releases" and self.source.get("api"))

    def _get_json(self, url: str):
        check_url(url, self.allow_http)
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"hifi-bridge/{__version__}",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
        with self._open(req, timeout=self.timeout) as r:
            raw = r.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ValueError("response too large")
        return json.loads(raw.decode("utf-8"))

    def check(self, current_version: str, compatible: str) -> dict:
        """Returns {"status", "checked_at", "latest"}. status: update_available | up_to_date | no_bundle | no_source | not_public |
        rate_limited | unreachable | invalid_response | insecure_source."""
        out = {"status": "no_source", "checked_at": self.clock(), "latest": None}
        if not self.available:
            return out
        url = self.source["api"].rstrip("/") + "?per_page=20"
        try:
            releases = self._get_json(url)
        except ValueError as e:
            out["status"] = "insecure_source" if str(e) == "insecure_source" else "invalid_response"
            return out
        except urllib.error.HTTPError as e:
            if (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0") or e.code == 429:
                out["status"] = "rate_limited"
            elif e.code in (401, 403, 404):
                out["status"] = "not_public"                     # private repository: anonymous access is refused (OPEN_QUESTIONS #45)
            else:
                out["status"] = "unreachable"
            return out
        except (urllib.error.URLError, OSError):
            out["status"] = "unreachable"
            return out
        latest = self.pick(releases, compatible)
        if latest is False:
            out["status"] = "invalid_response"
            return out
        if latest is None:
            out["status"] = "no_bundle"
            return out
        out["latest"] = latest
        out["status"] = "update_available" if is_newer(latest["version"], current_version) else "up_to_date"
        return out

    def pick(self, releases, compatible: str):
        """Newest published, non-pre-release with a bundle for this board; None if there is none; False if the data is malformed."""
        if not isinstance(releases, list):
            return False
        prefix, dl_prefix = f"hifi-bridge-{compatible}-", self._download_prefix()
        best = None
        for rel in releases:
            if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
                continue
            version = str(rel.get("tag_name") or "").lstrip("v")
            key = parse_version(version)
            if key is None:
                continue
            for a in rel.get("assets") or []:
                if not isinstance(a, dict):
                    continue
                name, url = str(a.get("name") or ""), str(a.get("browser_download_url") or "")
                if not (name.startswith(prefix) and name.endswith(".raucb")):
                    continue
                if not dl_prefix or not url.startswith(dl_prefix):  # only the official repository's release assets
                    continue
                try:
                    size = int(a.get("size") or 0)                   # HTTPS is enforced again by the download itself
                except (ValueError, TypeError):
                    continue
                digest = str(a.get("digest") or "")
                cand = {"version": version, "tag": str(rel.get("tag_name")), "name": str(rel.get("name") or rel.get("tag_name")),
                        "published_at": str(rel.get("published_at") or ""), "page": self._page(rel),
                        "asset": {"name": name, "url": url, "size": size,
                                  "sha256": digest[7:].lower() if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else None}}
                if best is None or key > parse_version(best["version"]):
                    best = cand
                break
        return best

    def _download_prefix(self) -> str:
        """GitHub release assets live at <releases>/download/<tag>/<name> (``browser_download_url``); GitHub's own redirect from there to
        its storage must stay on HTTPS (HttpsOnlyRedirect). A manipulated API answer can therefore not make the device fetch elsewhere."""
        base = str(self.source.get("releases") or "").rstrip("/")
        return base + "/download/" if base.startswith(("https://", "http://")) else ""

    def _page(self, rel: dict) -> str:
        """Link to the release page - only inside the official releases URL (never an arbitrary link from the API response)."""
        base = str(self.source.get("releases") or "")
        url = str(rel.get("html_url") or "")
        return url if base and url.startswith(base.rstrip("/") + "/") else base
