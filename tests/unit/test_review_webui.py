"""UNIT (real HTTP server on localhost, SIMULATED phone, FAKE LMS): regressions for the review findings on the WebUI HTTP layer
(reports/REVIEW_PHASE2B_EXISTING_CODE.md: H1, M1-M4, L9, L10, L12)."""
import socket
import time

import pytest
from test_webui import Client, ui  # noqa: F401  (fixture)


def _rss_kib() -> int:
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) for line in f if line.startswith("VmRSS"))


def _raw(port: int, head: bytes, body: bytes = b"", timeout: float = 5.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(head)
        try:
            if body:
                s.sendall(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                                        # the server may answer and close before the body is sent: that is fine
        out = b""
        while True:
            try:
                b = s.recv(65536)
            except ConnectionResetError:
                break
            if not b:
                break
            out += b
        return out
    finally:
        s.close()


def _status(resp: bytes) -> int:
    return int(resp.split(b"\r\n", 1)[0].split()[1])


# ---------------------------------------------------------------- H1: request body limits before any authentication
def test_negative_content_length_is_rejected_without_reading_the_body(ui):
    before = _rss_kib()
    t0 = time.monotonic()
    head = (f"POST /login HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: -1\r\n\r\n").encode()
    resp = _raw(ui.port, head, b"a" * (8 << 20))
    assert _status(resp) == 400
    assert time.monotonic() - t0 < 3.0
    assert _rss_kib() - before < 4096                  # the 8 MiB body was not buffered (was: read until EOF)


@pytest.mark.parametrize("value,status", [("abc", 400), ("1e3", 400), ("+5", 400), (" 7 , 7", 400), (str(70 * 1024), 413)])
def test_malformed_or_oversized_content_length(ui, value, status):
    head = (f"POST /login HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {value}\r\n\r\n").encode()
    assert _status(_raw(ui.port, head, b"password=x")) == status


def test_chunked_request_bodies_are_refused(ui):
    head = (f"POST /login HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
            "Transfer-Encoding: chunked\r\n\r\n").encode()
    resp = _raw(ui.port, head, b"a\r\npassword=x\r\n0\r\n\r\n")
    assert _status(resp) in (400, 411) and resp.count(b"HTTP/1.") == 1   # nothing of the body is parsed as a second request


def test_normal_login_post_still_works(ui):
    c = Client(ui.port)
    r = c.post("/setup", {"code": ui.code, "password": "a good long passphrase", "confirm": "a good long passphrase"}, csrf=False)
    assert r.status == 303 and r.getheader("Location") == "/setup/bluetooth"
