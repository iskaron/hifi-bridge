"""Test doubles for the update stream: a local HTTP server that imitates the GitHub Releases API and asset downloads (never GitHub itself),
and an in-process fake of the RAUC client interface (``btbridge.ota.rauc.RaucClient``) for the state-machine tests."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from btbridge.ota.rauc import RaucError, RaucUnavailable


class ReleaseServer:
    """GET /repos/o/r/releases -> ``releases`` (or ``api_status``); GET /dl/<name> -> 302 -> /assets/<name>; /assets/<name> honours Range
    unless ``ignore_range``; ``cut_after`` drops the connection after that many body bytes (once)."""

    def __init__(self):
        self.releases: object = []
        self.api_status, self.api_headers = 200, {}
        self.assets: dict[str, bytes] = {}
        self.ignore_range, self.cut_after = False, None
        self.requests: list[tuple[str, dict]] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append((self.path, dict(self.headers)))
                if self.path.startswith("/repos/"):
                    body = json.dumps(srv.releases).encode() if not isinstance(srv.releases, bytes) else srv.releases
                    self.send_response(srv.api_status)
                    for k, v in srv.api_headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/dl/") or "/releases/download/" in self.path:
                    self.send_response(302)                         # like GitHub: release download -> storage
                    self.send_header("Location", f"http://127.0.0.1:{srv.port}/assets/{self.path.rsplit('/', 1)[-1]}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path.startswith("/assets/") and self.path[8:] in srv.assets:
                    data = srv.assets[self.path[8:]]
                    rng = self.headers.get("Range")
                    start = int(rng[6:-1]) if rng and not srv.ignore_range else 0
                    if start >= len(data) and rng:
                        self.send_response(416); self.send_header("Content-Length", "0"); self.end_headers(); return
                    self.send_response(206 if start else 200)
                    if start:
                        self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                    self.send_header("Content-Length", str(len(data) - start))
                    self.end_headers()
                    body = data[start:]
                    if srv.cut_after is not None:
                        n, srv.cut_after = srv.cut_after, None
                        self.wfile.write(body[:n]); self.wfile.flush()
                        self.connection.shutdown(2)
                        return
                    self.wfile.write(body)
                else:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def source(self) -> dict:
        return {"type": "github-releases", "repository": "iskaron/hifi-bridge", "releases": f"{self.base}/iskaron/hifi-bridge/releases",
                "api": f"{self.base}/repos/iskaron/hifi-bridge/releases"}

    def release(self, tag, assets, draft=False, prerelease=False):
        return {"tag_name": tag, "name": f"Hi-Fi Bridge {tag}", "draft": draft, "prerelease": prerelease, "published_at": "2026-11-20T10:00:00Z",
                "html_url": f"{self.base}/iskaron/hifi-bridge/releases/tag/{tag}",
                "assets": [{"name": n, "size": len(self.assets.get(n, b"")) or 10,
                            "browser_download_url": f"{self.base}/iskaron/hifi-bridge/releases/download/{tag}/{n}", **extra}
                           for n, extra in assets]}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeRaucClient:
    """Same methods as RaucClient, in process. ``plan`` scripts installations; ``down`` makes every call raise RaucUnavailable."""

    def __init__(self, compatible="hifi-bridge-rpi3", booted="A"):
        self.compatible, self.booted = compatible, booted
        self.operation, self.last_error, self.progress = "idle", "", (0, "", 0)
        self.slot = {"rootfs.0": {"bootname": "A", "boot-status": "good"}, "rootfs.1": {"bootname": "B", "boot-status": "bad"}}
        self.primary_slot = self.name_of(booted)
        self.bundles: dict[str, dict | str] = {}
        self.plan = {"result": 0}
        self.down = False
        self.marks: list[tuple[str, str]] = []
        self.installs: list[str] = []
        self._completed: list[int] = []
        self.polls_until_done = 2
        self._polls = 0

    def name_of(self, bootname):
        return next(n for n, s in self.slot.items() if s["bootname"] == bootname)

    def other(self):
        return next(n for n in self.slot if n != self.name_of(self.booted))

    def _up(self):
        if self.down:
            raise RaucUnavailable("rauc down")

    def properties(self):
        self._up()
        if self.operation == "installing":
            self._polls += 1
            if self._polls >= self.polls_until_done:
                self._finish()
        return {"Operation": self.operation, "LastError": self.last_error, "Progress": self.progress, "Compatible": self.compatible,
                "Variant": "", "BootSlot": self.booted}

    def slots(self):
        self._up()
        return [{"name": n, "class": "rootfs", "state": "booted" if s["bootname"] == self.booted else "inactive", **s}
                for n, s in sorted(self.slot.items())]

    def primary(self):
        self._up()
        return self.primary_slot

    def inspect(self, path):
        self._up()
        b = self.bundles.get(path)
        if b is None or isinstance(b, str):
            raise RaucError(b or "Failed to open bundle")
        return {"update": {"compatible": b["compatible"], "version": b["version"], "description": ""}, "bundle": {"format": "verity"}}

    def install(self, path):
        self._up()
        if self.operation != "idle":
            raise RaucError("Already processing a different method")
        self.installs.append(path)
        if self.plan.get("refuse"):
            raise RaucError(self.plan["refuse"])
        self.operation, self.last_error, self.progress, self._polls = "installing", "", (10, "Checking bundle", 1), 0

    def _finish(self):
        if self.plan.get("hang"):
            return
        if self.plan.get("crash"):
            self.operation, self.progress = "idle", (0, "", 0)          # daemon restarted: idle, no Completed, no error
            return
        res = int(self.plan.get("result", 0))
        if res == 0:
            o = self.other()
            b = self.bundles.get(self.installs[-1])
            if isinstance(b, dict):
                self.slot[o]["bundle.version"] = b["version"]
            self.slot[o]["boot-status"], self.primary_slot = "good", o
            self.progress = (100, "Installing done.", 1)
        else:
            self.last_error = self.plan.get("error", "Installation error")
            self.progress = (100, "Installing failed.", 1)
        self.operation = "idle"
        if not self.plan.get("no_signal"):
            self._completed.append(res)

    def mark(self, state, slot):
        self._up()
        name = {"booted": self.name_of(self.booted), "other": self.other()}.get(slot, slot)
        if name not in self.slot:
            raise RaucError(f"No slot with name '{slot}' found")
        self.marks.append((state, name))
        if state in ("good", "active"):
            self.slot[name]["boot-status"] = "good"
        if state == "active":
            self.primary_slot = name
        if state == "bad":
            self.slot[name]["boot-status"] = "bad"
            if self.primary_slot == name:
                self.primary_slot = self.other() if name == self.name_of(self.booted) else self.name_of(self.booted)
        return name, f"{state} {name}"

    def completed(self):
        if self.down:
            raise RaucUnavailable("rauc down")
        return self._completed.pop(0) if self._completed else None

    def close(self):
        pass
