"""Shared fixtures for the flash tool tests: throw-away signing keys (generated per run, never committed), signed SIMULATED images, and a
local release server speaking the GitHub Releases JSON with HTTP Range/ETag support and fault injection. Loopback only - never the internet."""
from __future__ import annotations

import hashlib
import http.server
import json
import lzma
import os
import threading

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from btbridge import provisioning as P
from hififlash import manifest as M

MB = 1 << 20


def keypair():
    k = ec.generate_private_key(ec.SECP256R1())
    return k, k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def sign(key, raw: bytes) -> bytes:
    return key.sign(raw, ec.ECDSA(hashes.SHA256()))


def image(tmp, compatible="hifi-bridge-rpi3", version="0.3.0", size=6 * MB, slot_off=2 * MB, compress=False, key=None):
    """A simulated card image with an empty provisioning slot; returns dict(path, manifest, raw, sig, data, name)."""
    data = bytearray(os.urandom(size))
    data[slot_off:slot_off + P.SLOT_SIZE] = P.placeholder()
    data = bytes(data)
    base = f"hifi-bridge-{compatible}-{version}.img"
    path = os.path.join(str(tmp), base + (".xz" if compress else ""))
    with open(path, "wb") as fh:
        fh.write(lzma.compress(data, preset=0) if compress else data)
    m = {"format": M.FORMAT, "compatible": compatible, "version": version, "image": base, "size": size,
         "sha256": hashlib.sha256(data).hexdigest(), "slot": {"file": P.FILE_NAME, "offset": slot_off, "size": P.SLOT_SIZE}}
    raw = M.dumps(m)
    return {"path": path, "manifest": m, "raw": raw, "sig": sign(key, raw) if key else None, "data": data, "name": base}


class ReleaseServer:
    """Serves ``files`` (name -> bytes) and ``/releases`` (GitHub JSON). Knobs: ``ignore_range``, ``drop_after`` (close the connection after N
    body bytes, once), ``status`` (force an HTTP status for every path), ``etag``."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.releases: list = []
        self.ignore_range = False
        self.drop_after: int | None = None
        self.status: int | None = None
        self.etag = '"v1"'
        self.requests: list[dict] = []
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append({"path": self.path, "range": self.headers.get("Range"), "if_range": self.headers.get("If-Range")})
                if srv.status:
                    self.send_response(srv.status); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                if self.path == "/releases":
                    body = json.dumps(srv.releases).encode()
                elif self.path.startswith("/f/") and self.path[3:] in srv.files:
                    body = srv.files[self.path[3:]]
                else:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                start, code = 0, 200
                rng = self.headers.get("Range")
                if rng and not srv.ignore_range and self.headers.get("If-Range", srv.etag) == srv.etag:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(body):
                        self.send_response(416); self.send_header("Content-Length", "0"); self.end_headers()
                        return
                    code = 206
                part = body[start:]
                self.send_response(code)
                self.send_header("Content-Length", str(len(part)))
                self.send_header("ETag", srv.etag)
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                if srv.drop_after is not None:
                    n, srv.drop_after = srv.drop_after, None
                    self.wfile.write(part[:n]); self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(part)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name: str) -> str:
        return f"{self.base}/f/{name}"

    def publish(self, tag: str, imgs: list[dict], prerelease=False, extra_assets=()):
        assets = []
        for im in imgs:
            name = os.path.basename(im["path"])
            self.files[name] = open(im["path"], "rb").read()
            self.files[im["name"] + ".manifest.json"] = im["raw"]
            assets += [name, im["name"] + ".manifest.json"]
            if im["sig"] is not None:
                self.files[im["name"] + ".manifest.json.sig"] = im["sig"]
                assets.append(im["name"] + ".manifest.json.sig")
        self.releases.append({"tag_name": tag, "prerelease": prerelease, "draft": False, "published_at": "2026-11-18T10:00:00Z",
                              "assets": [{"name": a, "size": len(self.files[a]), "browser_download_url": self.url(a)} for a in assets]
                              + list(extra_assets)})

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
