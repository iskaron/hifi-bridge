"""UNIT (real sockets, no LMS): regressions for review findings on the stream server and encoders
(reports/REVIEW_PHASE2B_EXISTING_CODE.md: M2, M7, L4)."""
import socket
import threading
import time

import pytest

from btbridge.core.ring import PcmRing
from btbridge.stream import server as S
from btbridge.stream.feeder import LiveFeeder
from btbridge.stream.server import StreamServer

TOKEN = "tok_SECRET-123"


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(S, "REQUEST_TIMEOUT_S", 0.5)
    monkeypatch.setattr(S, "WRITE_TIMEOUT_S", 1.0)
    monkeypatch.setattr(S, "MAX_CONNECTIONS", 4)
    ring = PcmRing(44100 * 4); f = LiveFeeder(ring); s = StreamServer(ring, f, TOKEN, "wav", lead_in_ms=200)
    port = s.start("127.0.0.1", 0); s.open_session("sess1"); stop = threading.Event()
    threading.Thread(target=f.run, args=(stop,), daemon=True).start()
    yield s, port, f
    stop.set(); s.stop()


def _closed_within(sock, seconds):
    sock.settimeout(seconds)
    try:
        while sock.recv(65536):
            pass
        return True
    except (ConnectionResetError, OSError):
        return True
    except socket.timeout:
        return False


def test_idle_connection_without_a_request_is_closed(srv):
    _, port, _ = srv
    c = socket.create_connection(("127.0.0.1", port))
    assert _closed_within(c, 3)                               # was: a thread per idle peer, forever


def test_stalled_reader_is_dropped(srv):
    s, port, _ = srv
    c = socket.socket()
    c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    c.connect(("127.0.0.1", port))
    c.sendall(f"GET /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())
    t0 = time.monotonic()                                     # never reads: the server's writes stall, then time out
    while time.monotonic() - t0 < 15 and "client_disconnected" not in [e[1] for e in s.events]:
        time.sleep(0.2)
    assert "client_disconnected" in [e[1] for e in s.events]  # was: the writer thread blocked forever
    c.close()


def test_bad_token_is_answered_and_closed_at_once(srv):
    _, port, _ = srv
    c = socket.create_connection(("127.0.0.1", port), timeout=3)
    c.sendall(b"GET /s/wrong/sess1.wav HTTP/1.1\r\nHost: x\r\n\r\n")
    data = b""
    t0 = time.monotonic()
    while True:
        b = c.recv(4096)
        if not b:
            break
        data += b
    assert data.startswith(b"HTTP/1.0 403") and time.monotonic() - t0 < 1.0


def test_connections_beyond_the_cap_are_refused(srv):
    _, port, _ = srv
    hold = [socket.create_connection(("127.0.0.1", port)) for _ in range(4)]
    time.sleep(0.1)
    extra = socket.create_connection(("127.0.0.1", port), timeout=3)
    assert extra.recv(100).startswith(b"HTTP/1.0 503")
    for h in hold:
        h.close()
