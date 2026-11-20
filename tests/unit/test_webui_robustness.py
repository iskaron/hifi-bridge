"""UNIT (real HTTP server on localhost): what the WebUI does with requests no browser sends - duplicated or short bodies, JSON bodies,
broken cookies, HEAD, a client that leaves before the answer is written. Nothing here may crash a handler thread, leak a stack trace
or let a half-read body be parsed as the next request. Coverage package 7b-3."""
import socket
import time

from test_review_webui import _raw, _status
from test_webui import PW, ui  # noqa: F401  (fixture)

FORM = "Content-Type: application/x-www-form-urlencoded"


def head(ui, method: str, path: str, *extra: str) -> bytes:
    return (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\n" + "".join(f"{h}\r\n" for h in extra) + "\r\n").encode()


def test_two_different_content_length_headers_are_refused_and_the_connection_closed(ui):
    resp = _raw(ui.port, head(ui, "POST", "/login", FORM, "Content-Length: 5", "Content-Length: 500"), b"a=1&b")
    assert _status(resp) == 400 and resp.count(b"HTTP/1.") == 1


def test_a_body_shorter_than_its_content_length_ends_with_400_not_a_blocked_thread(ui):
    s = socket.create_connection(("127.0.0.1", ui.port), timeout=5)
    try:
        s.sendall(head(ui, "POST", "/login", FORM, "Content-Length: 50") + b"password=x")
        s.shutdown(socket.SHUT_WR)                              # the client leaves after 10 of 50 bytes
        out = b""
        while chunk := s.recv(65536):
            out += chunk
    finally:
        s.close()
    assert _status(out) == 400 and out.count(b"HTTP/1.") == 1


def test_json_bodies_are_accepted_for_forms_and_broken_json_is_a_400(ui):
    ui.c.post("/setup", {"code": ui.code, "password": PW, "confirm": PW}, csrf=False)
    ui.c.cookies.clear()
    wrong = b'{"password": "nope"}'
    r = _raw(ui.port, head(ui, "POST", "/login", "Content-Type: application/json", f"Content-Length: {len(wrong)}"), wrong)
    assert _status(r) == 303 and b"Location: /login" in r                           # parsed as a form: the password is simply wrong
    good = ('{"password": "%s"}' % PW).encode()
    r = _raw(ui.port, head(ui, "POST", "/login", "Content-Type: application/json", f"Content-Length: {len(good)}"), good)
    assert _status(r) == 303 and b"Location: /" in r and b"bab_session=" in r
    lst = b"[1, 2, 3]"                                                               # valid JSON but not an object: no fields at all
    r = _raw(ui.port, head(ui, "POST", "/login", "Content-Type: application/json", f"Content-Length: {len(lst)}"), lst)
    assert _status(r) == 303 and b"Location: /login" in r
    broken = b"{not json"
    r = _raw(ui.port, head(ui, "POST", "/login", "Content-Type: application/json", f"Content-Length: {len(broken)}"), broken)
    assert _status(r) == 400 and b"Traceback" not in r


def test_a_garbled_cookie_header_means_no_session_not_an_error(ui):
    r = _raw(ui.port, head(ui, "GET", "/setup", 'Cookie: bab_session="unterminated; ; =; \x01x'))
    assert _status(r) == 200 and b"Traceback" not in r


def test_head_returns_the_headers_of_get_without_a_body(ui):
    got = _raw(ui.port, head(ui, "GET", "/setup"))
    r = _raw(ui.port, head(ui, "HEAD", "/setup"))
    assert _status(r) == 200 and r.endswith(b"\r\n\r\n")                             # nothing after the header block
    assert b"Content-Length: " + str(len(got.split(b"\r\n\r\n", 1)[1])).encode() in r


def test_a_client_that_leaves_before_the_page_is_written_does_not_hurt_the_server(ui):
    for _ in range(5):
        s = socket.create_connection(("127.0.0.1", ui.port), timeout=5)
        s.sendall(head(ui, "GET", "/setup"))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")     # RST on close
        s.close()
    time.sleep(0.3)
    assert _status(_raw(ui.port, head(ui, "GET", "/setup"))) == 200                 # still serving


def test_a_redirect_target_with_control_characters_falls_back_to_the_start_page():
    from btbridge.webui.server import redirect
    assert dict(redirect("/ok").headers)["Location"] == "/ok"
    assert dict(redirect("/x\r\nSet-Cookie: evil=1").headers)["Location"] == "/"


def test_a_missing_static_file_and_path_tricks_are_404(ui):
    for path in ("/static/does-not-exist.css", "/static/..%2fapp.py", "/static/.hidden", "/static/"):
        assert _status(_raw(ui.port, head(ui, "GET", path))) == 404
