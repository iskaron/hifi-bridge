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


def test_mqtt_form_refuses_a_ca_with_markers_but_no_certificate(ui):
    import urllib.parse
    from test_webui import finish_wizard
    finish_wizard(ui)
    r = ui.c.post("/settings/mqtt", {"mqtt_host": "broker.lan", "mqtt_port": "8883", "mqtt_tls": "on", "mqtt_enabled": "on",
                                      "mqtt_ca_pem": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"})
    assert r.status == 303 and "PEM" in urllib.parse.unquote(ui.c.cookies.get("bab_flash", ""))
    assert ui.svc.store.get("mqtt_ca_pem") == "" and ui.svc.store.get("mqtt_enabled") is False


# ---------------------------------------------------------------- M1/M2: TLS handshake per connection, connection timeouts and caps
def _threads():
    import threading
    return threading.active_count()


def test_idle_connections_are_released_after_the_timeout(ui, monkeypatch):
    from btbridge.webui import server as S
    ui.web.stop()
    monkeypatch.setattr(S, "CONN_TIMEOUT_S", 0.5)
    port = S.WebUI(ui.svc, "127.0.0.1", 0).start()
    base = _threads()
    conns = [socket.create_connection(("127.0.0.1", port)) for _ in range(30)]
    time.sleep(0.3)
    assert _threads() > base
    time.sleep(1.5)
    assert _threads() <= base + 2                                            # was: one thread per idle peer, forever
    for c in conns:
        c.close()
    assert Client(port).get("/healthz").status == 200


def test_connections_beyond_the_cap_are_refused_not_queued(ui, monkeypatch):
    from btbridge.webui import server as S
    ui.web.stop()
    monkeypatch.setattr(S, "MAX_CONNECTIONS", 5)
    port = S.WebUI(ui.svc, "127.0.0.1", 0).start()
    conns = [socket.create_connection(("127.0.0.1", port)) for _ in range(5)]
    time.sleep(0.2)
    extra = socket.create_connection(("127.0.0.1", port), timeout=3)
    assert extra.recv(100).startswith(b"HTTP/1.0 503")
    for c in conns:
        c.close()


# ---------------------------------------------------------------- M6: the pairing countdown names the device, not the product
def test_pairing_countdown_carries_the_bluetooth_name_for_the_script(ui):
    import os
    import re
    from test_webui import finish_wizard
    from btbridge.webui import views
    finish_wizard(ui)                                                        # device name "Wohnzimmer"
    ui.c.post("/pairing/open")
    for path in ("/", "/pairing"):
        body = ui.c.get(path).body
        m = re.search(r'<p class="countdown"[^>]*>', body)
        assert m and 'data-name="Wohnzimmer"' in m.group(0), (path, m and m.group(0))
    js = open(os.path.join(views.STATIC_DIR, "app.js")).read()
    assert ".brand strong" not in js and 'getAttribute("data-name")' in js


def test_normal_login_post_still_works(ui):
    c = Client(ui.port)
    r = c.post("/setup", {"code": ui.code, "password": "a good long passphrase", "confirm": "a good long passphrase"}, csrf=False)
    assert r.status == 303 and r.getheader("Location") == "/setup/bluetooth"
