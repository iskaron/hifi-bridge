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
from ..core.bounded import BoundedList
from ..core.ring import PcmRing

URL_RE = re.compile(r"^/s/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)\.(wav|flac)$")
METAINT = 16384
TOKEN_RE = re.compile(r"/s/[A-Za-z0-9_-]+/")


def redact(text: str) -> str:
    """Remove stream tokens from anything that is logged."""
    return TOKEN_RE.sub("/s/***/", text)


REQUEST_TIMEOUT_S = 10.0     # reading the request (an idle or slowloris peer is dropped: review M2)
WRITE_TIMEOUT_S = 60.0       # one write may block this long; a reader that takes nothing for a minute is gone
MAX_CONNECTIONS = 16         # players of a sync group + probes; more are answered 503 and closed


class _Server(ThreadingHTTPServer):
    """Threaded stream listener with a cap on concurrent connections (review M2)."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, max_connections: int):
        super().__init__(addr, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1.0)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass


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
        self.events: list[tuple] = BoundedList(1000)   # (wall, event, detail) timing probes (tokens never recorded)
        self._lock = threading.Lock()
        self._active = 0
        self.trace = None                    # optional stream.trace.StreamTrace (HIFI_STREAM_TRACE=1); None = no tracing at all

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
    def start(self, host: str = "0.0.0.0", port: int = 0) -> int:  # nosec B104 - players fetch the stream over the LAN (token-protected)
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            timeout = REQUEST_TIMEOUT_S

            def log_message(self, fmt, *a):
                srv.log(redact(fmt % a))

            def do_HEAD(self):
                srv._handle(self, head=True)

            def do_GET(self):
                srv._handle(self, head=False)

        self.httpd = _Server((host, port), H, MAX_CONNECTIONS)
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
        code = 206 if start_byte else 200
        content_range = f"bytes {start_byte}-4294967294/4294967295" if start_byte else None
        h.send_response(code)                # NOTE: BaseHTTPRequestHandler logs the '"GET ..." 206 -' line HERE, at the START
        if content_range:
            h.send_header("Content-Range", content_range)
        h.send_header("Content-Type", CONTENT_TYPES[fmt])
        h.send_header("Cache-Control", "no-cache, no-store")
        h.send_header("icy-name", "Hi-Fi Bridge")
        if want_icy:
            h.send_header("icy-metaint", str(METAINT))
        h.end_headers()
        tr = self.trace
        with_header = fmt == "flac" or start_byte < WAV_HEADER_LEN
        if head:
            if tr:
                tr.conn_start(sid, h.client_address[0], "HEAD", code, rng, content_range, False, None)
            return
        self.stats["connections"] += 1
        self._ev("client_connected", f"range={start_byte}")
        h.connection.settimeout(WRITE_TIMEOUT_S)            # a stalled reader times out instead of pinning this thread
        enc = FlacEncoder() if fmt == "flac" else WavEncoder(with_header=(start_byte < WAV_HEADER_LEN))
        pos = self.feeder.lead_in_start(self.lead_in_ms)
        rid = tr.conn_start(sid, h.client_address[0], "GET", code, rng, content_range, with_header and fmt == "wav",
                            self.ring.head - pos) if tr else None
        reason = "error"
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
                        reason = "session-ended"
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
                    if tr:
                        tr.write_begin(rid, pos)
                    h.wfile.write(out)
                    self.stats["bytes_sent"] += len(out)
                    if tr:
                        tr.write_end(rid, len(out), data, self.ring.head - pos, skipped, pos)
        except BrokenPipeError:
            reason = "peer-closed"
        except ConnectionResetError:
            reason = "peer-reset"
        except TimeoutError:
            reason = f"write-timeout-{WRITE_TIMEOUT_S:.0f}s"
        except OSError as e:
            reason = f"os-error-{e.errno}"
        except BaseException as e:
            reason = f"error-{type(e).__name__}"
            raise
        finally:
            enc.close()
            self._ev("client_disconnected", reason)
            if tr:
                tr.conn_end(rid, reason)

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
