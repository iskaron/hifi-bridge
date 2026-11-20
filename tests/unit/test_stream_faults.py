"""UNIT (real sockets, no LMS): requests a Squeezelite or a stranger can send to the stream port that are not the happy path - HEAD, a path
that is not a stream, a stream in the other format, a session that has ended, a reader that never reads while the live ring overruns it.
Nothing may play audio of the wrong session or crash the server. Coverage package 7b-3."""
import socket
import time

import pytest
from test_review_stream import TOKEN, srv  # noqa: F401  (fixture)

from btbridge.stream.server import StreamServer


def http(port: int, request: bytes, timeout: float = 3.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(request)
        out = b""
        s.settimeout(timeout)
        try:
            while len(out) < 100000:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
                if b"\r\n\r\n" in out and request.startswith(b"HEAD"):
                    break
        except socket.timeout:
            pass
        return out
    finally:
        s.close()


def status(resp: bytes) -> int:
    return int(resp.split(b" ", 2)[1])


def test_head_returns_the_stream_headers_and_no_audio(srv):
    s, port, _ = srv
    resp = http(port, f"HEAD /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())
    assert status(resp) == 200 and b"Content-Type: audio/" in resp and resp.endswith(b"\r\n\r\n")


def test_a_path_that_is_not_a_stream_url_is_a_404_and_the_other_format_of_a_live_session_too(srv):
    s, port, _ = srv
    assert status(http(port, b"GET /favicon.ico HTTP/1.0\r\n\r\n")) == 404
    assert status(http(port, f"GET /s/{TOKEN}/sess1.flac HTTP/1.0\r\n\r\n".encode())) == 404          # the session streams WAV
    assert status(http(port, f"GET /s/{TOKEN}/old-session.wav HTTP/1.0\r\n\r\n".encode())) == 410      # ended sessions are gone, not 404
    assert s.stats["stale_rejected"] >= 1


def test_the_stream_stops_at_once_when_its_session_ends_and_a_late_request_for_it_is_refused(srv):
    s, port, _ = srv
    c = socket.create_connection(("127.0.0.1", port), timeout=3)
    c.sendall(f"GET /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())
    got = b""
    while b"\r\n\r\n" not in got:
        got += c.recv(4096)
    s.close_session("sess1")
    c.settimeout(3)
    try:
        while c.recv(65536):
            pass                                                              # drains to EOF: the server closed the stream
    except (socket.timeout, ConnectionResetError):
        pytest.fail("the stream was not closed after its session ended")
    finally:
        c.close()
    assert status(http(port, f"GET /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())) == 410


def test_a_reader_that_falls_a_whole_ring_behind_is_skipped_forward_and_counted(srv):
    import threading
    s, port, feeder = srv
    c = socket.create_connection(("127.0.0.1", port), timeout=3)
    c.sendall(f"GET /s/{TOKEN}/sess1.wav HTTP/1.0\r\n\r\n".encode())
    got = c.recv(4096)                                                        # the header: the stream is running
    assert got.startswith(b"HTTP/1.0 200")
    drain = threading.Thread(target=lambda: [None for _ in iter(lambda: c.recv(65536), b"")], daemon=True)
    drain.start()                                                             # the client keeps reading (a stalled one is dropped instead)
    s.debug_hold_writes(1.5)                                                  # the server's writer is held (a Wi-Fi hiccup) ...
    s.ring.write(b"\0" * (44100 * 4 * 6))                                     # ... while six seconds of audio overrun the four-second ring
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and s.stats["ring_skips"] == 0:
        time.sleep(0.05)
    c.close()
    assert s.stats["ring_skips"] >= 1


def test_the_server_without_a_started_listener_can_be_stopped():
    from btbridge.core.ring import PcmRing
    from btbridge.stream.feeder import LiveFeeder
    ring = PcmRing(1000)
    s = StreamServer(ring, LiveFeeder(ring), TOKEN, "wav")
    s.stop()                                                                  # never started: nothing to shut down
    assert s.url("host", 8090, "sid").startswith("http://host:8090/s/")
