"""Demo console (SIMULATED devices only): virtual phones, the audio monitor, the demo radio and Lyrion helpers.

  python3 -m hifidemo.console --port 8100 --air http://hifi-bridge:8099 --pcm hifi-bridge:8098 --lms http://lms:9000 \
      --rooms "Wohnzimmer=wohnzimmer:9100,Schlafzimmer=schlafzimmer:9100,Arbeitszimmer=arbeitszimmer:9100" --data /data

The console never talks to the bridge product. Phones act through the virtual bluetoothd; Lyrion helpers act through Lyrion.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import decode, music

STATIC = os.path.join(os.path.dirname(__file__), "static")
PAGES = {"/": "index.html", "/phones": "phones.html", "/monitor": "monitor.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self'; "
       "worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PHONE_ACTIONS = {
    "scan": lambda p, b: {"ok": True, "visible": p.scan()},
    "pair": lambda p, b: p.pair(),
    "connect": lambda p, b: p.connect(),
    "disconnect": lambda p, b: p.disconnect(),
    "forget": lambda p, b: p.forget(),
    "bluetooth": lambda p, b: p.bluetooth(bool(b.get("on", True))),
    "play": lambda p, b: p.play(),
    "pause": lambda p, b: p.pause(),
    "next": lambda p, b: p.next_track(),
    "prev": lambda p, b: p.prev_track(),
    "select": lambda p, b: p.select(str(b.get("id", ""))),
    "volume": lambda p, b: p.set_volume(int(b.get("value", 0))),
    "notify": lambda p, b: p.notify(),
    "drop": lambda p, b: p.drop(),
}


def product_static(name: str) -> str | None:
    """The bridge's own stylesheet and logo, so the console shares the product's corporate identity."""
    import btbridge.webui as w
    path = os.path.join(os.path.dirname(w.__file__), "static", name)
    return path if name in ("app.css", "logo.svg") and os.path.isfile(path) else None


def wav_header(rate: int = music.RATE) -> bytes:
    size = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16)
            + b"data" + struct.pack("<I", size))


class Console:
    def __init__(self, phones: dict, library, lms, rooms: dict, radio_url: str, links: dict | None = None,
                 allowed_hosts=("localhost", "127.0.0.1", "::1", "console")):
        self.phones, self.library, self.lms, self.rooms = phones, library, lms, rooms
        self.radio_url, self.links, self.allowed_hosts = radio_url, links or {}, set(allowed_hosts)
        self._quit = threading.Event()
        threading.Thread(target=self._sync_loop, daemon=True, name="phone-sync").start()

    def _sync_loop(self):
        while not self._quit.wait(0.5):
            for p in list(self.phones.values()):
                p.sync()

    def shutdown(self):
        self._quit.set()
        for p in self.phones.values():
            p.shutdown()

    def state(self) -> dict:
        try:
            players = self.lms.players()
        except OSError:
            players = []
        return {"phones": [p.snapshot() for p in self.phones.values()], "players": players, "rooms": list(self.rooms),
                "library": self.library.items(), "links": self.links, "simulated": True}


def _host_ok(console: Console, host_header: str | None) -> bool:
    if not host_header:
        return False
    h = host_header.strip().lower()
    h = h[1:h.index("]")] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return h in console.allowed_hosts


def make_handler(console: Console):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "hifi-demo-console"

        def log_message(self, *a):
            pass

        # ---- helpers ----
        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode())

        def _guard(self, write: bool) -> bool:
            if not _host_ok(console, self.headers.get("Host")):
                self._send(421, b'{"error":"unknown host"}')
                return False
            if write:
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                    self._send(403, b'{"error":"cross-site request"}')
                    return False
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json" and self.headers.get("X-Hifi-Demo") != "1":
                    self._send(415, b'{"error":"JSON or X-Hifi-Demo header required"}')
                    return False
            return True

        def _body(self, limit: int = 65536) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        # ---- GET ----
        def do_GET(self):
            if not self._guard(False):
                return
            u = urlparse(self.path)
            if u.path in PAGES:
                return self._file(os.path.join(STATIC, PAGES[u.path]))
            if u.path.startswith("/static/"):
                name = u.path[8:]
                if not re.fullmatch(r"[a-z0-9_-]+\.(css|js|svg|png)", name):
                    return self._send(404, b"not found", "text/plain")
                path = product_static(name) or os.path.join(STATIC, name)
                return self._file(path)
            if u.path == "/api/state":
                return self._json(200, console.state())
            if u.path == "/radio.wav":
                return self._radio()
            if u.path.startswith("/stream/"):
                return self._relay(unquote(u.path[8:]))
            if u.path == "/health":
                return self._json(200, {"ok": True})
            self._send(404, b"not found", "text/plain")

        def _file(self, path: str):
            if not os.path.isfile(path):
                return self._send(404, b"not found", "text/plain")
            with open(path, "rb") as fh:
                self._send(200, fh.read(), TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))

        def _radio(self):
            """Endless live WAV for Lyrion (the 'internet radio' of the demo; it never leaves the internal network)."""
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-wav")
            self.send_header("Cache-Control", "no-store")
            self.send_header("icy-name", "Demo Radio (simulated)")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            radio = music.Radio()
            chunk_s, n = 0.05, int(music.RATE * 0.05) * 4
            try:
                self.wfile.write(wav_header() + radio.read(music.RATE * 4))       # 1 s lead-in fills the player's buffer
                next_t = time.monotonic()
                while True:
                    self.wfile.write(radio.read(n))
                    next_t += chunk_s
                    time.sleep(max(0.0, next_t - time.monotonic()))
            except OSError:
                return

        def _relay(self, room: str):
            target = console.rooms.get(room)
            if not target:
                return self._send(404, b'{"error":"unknown room"}')
            try:
                up = socket.create_connection(target, timeout=5)
            except OSError:
                return self._send(502, b'{"error":"room not reachable"}')
            f = up.makefile("rb")
            header = f.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Monitor-Info", header.decode(errors="replace").strip()[:300])
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while data := f.read1(65536):
                    self.wfile.write(data)
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                up.close()

        # ---- POST / PUT ----
        def do_POST(self):
            if not self._guard(True):
                return
            u = urlparse(self.path)
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
            m = re.fullmatch(r"/api/phones/([a-z0-9]+)/([a-z]+)", u.path)
            if m:
                phone, fn = console.phones.get(m.group(1)), PHONE_ACTIONS.get(m.group(2))
                if not phone or not fn:
                    return self._json(404, {"error": "unknown phone or action"})
                try:
                    return self._json(200, fn(phone, body))
                except (OSError, ValueError, KeyError) as e:
                    return self._json(502, {"ok": False, "message": "radio_error", "detail": f"{type(e).__name__}"})
            lms_actions = {"/api/lms/radio": lambda b: console.lms.play_radio(str(b["player"]), console.radio_url),
                           "/api/lms/stop": lambda b: console.lms.stop(str(b["player"])),
                           "/api/lms/sync": lambda b: console.lms.sync([str(x) for x in b["players"]]),
                           "/api/lms/unsync": lambda b: console.lms.unsync([str(x) for x in b["players"]])}
            if u.path in lms_actions:
                try:
                    lms_actions[u.path](body)
                    return self._json(200, {"ok": True})
                except (KeyError, ValueError, TypeError) as e:
                    return self._json(400, {"ok": False, "error": type(e).__name__})
                except OSError:
                    return self._json(502, {"ok": False, "error": "lyrion_unreachable"})
            self._json(404, {"error": "not found"})

        def do_PUT(self):
            if not self._guard(True):
                return
            u = urlparse(self.path)
            if u.path != "/api/library":
                return self._json(404, {"error": "not found"})
            name = (parse_qs(u.query).get("name") or ["upload"])[0]
            try:
                data = self._body(decode.MAX_UPLOAD_BYTES)
                item = console.library.add(decode.decode(data, name), name)
            except (decode.DecodeError, ValueError) as e:
                return self._json(400, {"ok": False, "error": str(e)})
            self._json(200, {"ok": True, "item": item})

    return H


def serve(console: Console, port: int, host: str = "0.0.0.0"):  # nosec B104 - internal demo network; published to 127.0.0.1 by the gateway
    srv = ThreadingHTTPServer((host, port), make_handler(console))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="console").start()
    return srv


DEFAULT_PHONES = (("a", "Annas Smartphone", "02:A1:00:00:00:01"), ("b", "Bens Smartphone", "02:B2:00:00:00:02"))


def main(argv=None) -> int:
    from .lmsctl import Lms
    from .phones import AirClient, Library, PcmLink, Phone, PhoneStore
    ap = argparse.ArgumentParser(description="Hi-Fi Bridge demo console (SIMULATED devices)")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--air", default="http://hifi-bridge:8099")
    ap.add_argument("--pcm", default="hifi-bridge:8098")
    ap.add_argument("--lms", default="http://lms:9000")
    ap.add_argument("--rooms", default="Wohnzimmer=wohnzimmer:9100,Schlafzimmer=schlafzimmer:9100,Arbeitszimmer=arbeitszimmer:9100")
    ap.add_argument("--data", default="/data")
    ap.add_argument("--radio-url", default="http://console:8100/radio.wav")
    a = ap.parse_args(argv)
    rooms = {}
    for item in a.rooms.split(","):
        name, hp = item.split("=")
        h, p = hp.rsplit(":", 1)
        rooms[name] = (h, int(p))
    air = AirClient(a.air)
    ph, pp = a.pcm.rsplit(":", 1)
    lib = Library(os.path.join(a.data, "uploads"))
    store = PhoneStore(os.path.join(a.data, "phones.json"))
    phones = {pid: Phone(pid, name, mac, air, lambda m: PcmLink(ph, int(pp), m), store, lib) for pid, name, mac in DEFAULT_PHONES}
    links = json.loads(os.environ.get("DEMO_LINKS", "{}"))
    c = Console(phones, lib, Lms(a.lms), rooms, a.radio_url, links)
    serve(c, a.port)
    print(f"[console] SIMULATED phones + audio monitor on :{a.port}", flush=True)
    from btbridge.supervision import StopSignal
    StopSignal().wait()
    c.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
