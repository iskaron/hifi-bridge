"""Live HTTP media endpoint: ``/s/<token>/<session>.wav|.flac``.

* Token (cryptographically random, rotatable) and a per-bridge-session id are in the
  URL.  A stale session id answers ``410 Gone`` so late connections from an old
  stream can never receive (or hold open) current audio.
* New connections start near the live edge with a bounded lead-in (see feeder).
* ``Range: bytes=N-`` is honoured by *skipping the WAV header* (LMS asks for
  ``bytes=44-`` after parsing it); other ranges just start at the live edge.
* Optional in-band ICY metadata (``Icy-MetaData: 1``) carries AVRCP titles to LMS.
"""
from __future__ import annotations

import hmac
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .encoders import CONTENT_TYPES, FlacEncoder, WavEncoder, WAV_HEADER_LEN
from .feeder import LiveFeeder
from ..core.ring import PcmRing

URL_RE = re.compile(r"^/s/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\.(wav|flac)$")
METAINT = 16384
TOKEN_RE = re.compile(r"/s/[A-Za-z0-9_-]+/")


def redact(text: str) -> str:
    """Remove stream tokens from anything that is logged."""
    return TOKEN_RE.sub("/s/***/", text)


class StreamServer:
    def __init__(self, ring: PcmRing, feeder: LiveFeeder, token: str, fmt: str = "wav", lead_in_ms: int = 1000,
                 icy: bool = False, log=None):
        self.ring, self.feeder, self.token = ring, feeder, token
        self.fmt, self.lead_in_ms, self.icy = fmt, lead_in_ms, icy
        self._hold_until = 0.0               # test hook: simulate delivery hiccups (writer pauses)
        self.log = log or (lambda msg: None)
        self.session: str | None = None
        self.title = ""
        self.httpd: ThreadingHTTPServer | None = None
        self.stats = {"connections": 0, "stale_rejected": 0, "bad_token": 0, "bytes_sent": 0, "ring_skips": 0}
        self.events: list[tuple] = []        # (wall, event, detail) timing probes (tokens never recorded)
        self._lock = threading.Lock()
        self._active = 0

    # -- session control ---------------------------------------------------
    def open_session(self, sid: str) -> None:
        with self._lock:
            self.session = sid
        self.feeder.set_fill(True)           # keep the timeline alive from now on
        self._ev("session_open", sid)

    def close_session(self, sid: str) -> None:
        with self._lock:
            if self.session == sid:
                self.session = None
        if self.session is None:
            self.feeder.set_fill(False)
        self._ev("session_close", sid)

    def set_title(self, title: str) -> None:
        self.title = title

    def debug_hold_writes(self, seconds: float) -> None:
        """TEST HOOK: pause all writers for ``seconds`` (simulates CPU/Wi-Fi delivery hiccups)."""
        self._hold_until = time.monotonic() + seconds

    def rotate_token(self, new_token: str) -> None:
        self.token = new_token               # existing connections continue; new requests need the new token

    def url(self, host: str, port: int, sid: str) -> str:
        return f"http://{host}:{port}/s/{self.token}/{sid}.{self.fmt}"

    def _ev(self, name, detail=""):
        self.events.append((time.time(), name, detail))

    # -- http ----------------------------------------------------------------
    def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *a):
                srv.log(redact(fmt % a))

            def do_HEAD(self):
                srv._handle(self, head=True)

            def do_GET(self):
                srv._handle(self, head=False)

        ThreadingHTTPServer.daemon_threads = True
        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer((host, port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="stream-http").start()
        return self.httpd.server_address[1]

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def _handle(self, h: BaseHTTPRequestHandler, head: bool) -> None:
        m = URL_RE.match(h.path.split("?")[0])
        if not m:
            h.send_error(404)
            return
        tok, sid, fmt = m.groups()
        if not hmac.compare_digest(tok.encode(), self.token.encode()):
            self.stats["bad_token"] += 1
            h.send_error(403)
            return
        with self._lock:
            current = self.session
        if sid != current:
            self.stats["stale_rejected"] += 1
            self._ev("stale_request", sid)
            h.send_error(410, "stream session ended")
            return
        if fmt != self.fmt:
            h.send_error(404)
            return
        rng = h.headers.get("Range", "")
        start_byte = int(rng.split("=")[1].split("-")[0]) if rng.startswith("bytes=") and rng.split("=")[1].split("-")[0].isdigit() else 0
        want_icy = self.icy and fmt == "wav" and h.headers.get("Icy-MetaData") == "1"
        h.send_response(206 if start_byte else 200)
        if start_byte:
            h.send_header("Content-Range", f"bytes {start_byte}-4294967294/4294967295")
        h.send_header("Content-Type", CONTENT_TYPES[fmt])
        h.send_header("Cache-Control", "no-cache, no-store")
        h.send_header("icy-name", "Hi-Fi Bridge")
        if want_icy:
            h.send_header("icy-metaint", str(METAINT))
        h.end_headers()
        if head:
            return
        self.stats["connections"] += 1
        self._ev("client_connected", f"range={start_byte}")
        enc = FlacEncoder() if fmt == "flac" else WavEncoder(with_header=(start_byte < WAV_HEADER_LEN))
        pos = self.feeder.lead_in_start(self.lead_in_ms)
        first = True
        audio_sent = 0
        last_title = None
        try:
            hdr = enc.header()
            if hdr:
                h.wfile.write(hdr)
                audio_sent = len(hdr)       # ICY metaint counts body bytes, header included
            while True:
                with self._lock:
                    if self.session != sid:
                        break               # session ended: close promptly, never serve other audio on it
                if time.monotonic() < self._hold_until:
                    time.sleep(0.01)
                    continue
                pos, data, skipped = self.ring.read(pos, 4410, timeout=0.2)
                if skipped:
                    self.stats["ring_skips"] += 1
                if not data:
                    continue
                if first:
                    first = False
                    self._ev("first_bytes_sent", len(data))
                out = enc.encode(data)
                if want_icy and fmt == "wav":
                    out = self._with_icy(out, audio_sent)
                    audio_sent += len(data)
                if out:
                    h.wfile.write(out)
                    self.stats["bytes_sent"] += len(out)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            enc.close()
            self._ev("client_disconnected")

    def _with_icy(self, audio: bytes, sent_before: int) -> bytes:
        out = bytearray()
        buf = audio
        sent = sent_before
        while buf:
            room = METAINT - (sent % METAINT)
            part, buf = buf[:room], buf[room:]
            out += part
            sent += len(part)
            if sent % METAINT == 0:
                if self.title:
                    t = ("StreamTitle='%s';" % self.title.replace("'", "’")).encode("utf-8", "replace")[:4000]
                    t += b"\0" * (-len(t) % 16)
                    out += bytes([len(t) // 16]) + t
                else:
                    out += b"\0"
        return bytes(out)
