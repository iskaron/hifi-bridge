"""Optional secure remote syslog (PRODUCT_SPEC: "optionally secure remote syslog").

RFC 5424 messages over TLS with octet-counting framing (RFC 5425, default port 6514). The server certificate is ALWAYS verified (system CAs or
the owner's CA in PEM) including the host name - there is no plaintext or "insecure" mode. Lines arrive already token-redacted from the
LogBuffer. Non-blocking: a bounded queue (oldest dropped and counted when full) and a worker thread that reconnects with back-off, so logging
never waits for the network."""
from __future__ import annotations

import collections
import socket
import ssl
import threading
import time
from typing import Callable

FACILITY_DAEMON = 3
SEVERITY = {"crit": 2, "err": 3, "warning": 4, "info": 6}


def frame(severity: str, hostname: str, app: str, msg: str, ts: float | None = None, procid: str = "-") -> bytes:
    pri = FACILITY_DAEMON * 8 + SEVERITY[severity]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts or time.time())) + f".{int(((ts or time.time()) % 1) * 1e6):06d}Z"
    host = (hostname or "-").replace(" ", "-")[:255]
    body = f"<{pri}>1 {stamp} {host} {app} {procid} - - {msg}".encode("utf-8", "replace")
    return str(len(body)).encode() + b" " + body


def severity_of(line: str) -> str:
    if line.startswith("FAULT"):
        return "crit"
    low = line.lower()
    if " error" in low or low.startswith("error") or "failed" in low:
        return "err"
    if "warning" in low or "unreachable" in low:
        return "warning"
    return "info"


class RemoteSyslog:
    def __init__(self, host: str, port: int = 6514, ca_pem: str = "", hostname: str = "hifi-bridge", app: str = "hifi-bridge",
                 queue_size: int = 1000, backoff: tuple[float, float] = (1.0, 60.0), log: Callable[[str], None] = lambda m: None):
        self.host, self.port, self.hostname, self.app, self.log = host, port, hostname, app, log
        self.ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
        self.ctx.check_hostname, self.ctx.verify_mode = True, ssl.CERT_REQUIRED       # never weakened
        self.q: collections.deque = collections.deque(maxlen=queue_size)
        self.backoff = backoff
        self.sent = self.dropped = 0
        self.last_error: str | None = None
        self.connected = False
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def emit(self, line: str, severity: str | None = None) -> None:
        with self._cv:
            if len(self.q) == self.q.maxlen:
                self.dropped += 1
            self.q.append((time.time(), severity or severity_of(line), line))
            self._cv.notify()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="syslog")
        self._thread.start()

    def stop(self, flush_s: float = 2.0) -> None:
        t0 = time.monotonic()
        while self.q and self.connected and time.monotonic() - t0 < flush_s:
            time.sleep(0.05)
        self._stop.set()
        with self._cv:
            self._cv.notify()
        if self._thread:
            self._thread.join(timeout=5)

    def state(self) -> dict:
        return {"connected": self.connected, "sent": self.sent, "dropped": self.dropped, "queued": len(self.q), "last_error": self.last_error}

    def _run(self) -> None:
        delay = self.backoff[0]
        while not self._stop.is_set():
            try:
                raw = socket.create_connection((self.host, self.port), timeout=10)
                with self.ctx.wrap_socket(raw, server_hostname=self.host) as s:
                    self.connected, self.last_error, delay = True, None, self.backoff[0]
                    while not self._stop.is_set():
                        with self._cv:
                            while not self.q and not self._stop.is_set():
                                self._cv.wait(1.0)
                            if self._stop.is_set() and not self.q:
                                break
                            item = self.q[0]
                        s.sendall(frame(item[1], self.hostname, self.app, item[2], item[0]))
                        with self._cv:
                            if self.q and self.q[0] is item:
                                self.q.popleft()
                        self.sent += 1
                    try:
                        s.unwrap()                     # proper TLS close_notify on a clean stop
                    except (OSError, ssl.SSLError):
                        pass
            except (OSError, ssl.SSLError) as e:
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                self.connected = False
            if self._stop.wait(delay):
                return
            delay = min(self.backoff[1], delay * 2)
