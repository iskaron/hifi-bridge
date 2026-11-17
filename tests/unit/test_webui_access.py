"""UNIT/INTEGRATION (real HTTP + real TLS with openssl-issued certificates, SIMULATED phone, FAKE LMS): local access - Host validation
(DNS rebinding), trust page and CA downloads, HTTPS listener with the local CA, HTTPS-only with lock-out protection, trusted reverse
proxies, custom certificates, /healthz, settings sections. TLS clients always VERIFY (no exceptions)."""
import http.client
import json
import re
import socket
import ssl
import subprocess
import time
import urllib.parse

import pytest
from test_service import P1, Lmses, fake_tree
from test_webui import PW, Client

from btbridge.net.access import LocalAccess
from btbridge.service import ApplianceService
from btbridge.webui.server import WebUI
from btkit.sim_a2dp import SimulatedA2dpSource

NAME = "hifi-bridge.local"


class TlsClient(Client):
    def __init__(self, port, cafile, server_hostname=NAME):
        super().__init__(port)
        self.cafile, self.sni = cafile, server_hostname

    def req(self, method, path, form=None, headers=None, raw_body=None):
        ctx = ssl.create_default_context(cafile=self.cafile)               # verification + hostname check ON
        orig = http.client.HTTPConnection
        port, sni = self.port, self.sni

        class Conn(http.client.HTTPSConnection):
            def __init__(self, host, port_, timeout=10):
                super().__init__(host, port_, timeout=timeout, context=ctx)

            def connect(self):
                sock = socket.create_connection(("127.0.0.1", port), timeout=10)
                self.sock = ctx.wrap_socket(sock, server_hostname=sni)
        http.client.HTTPConnection = Conn
        try:
            h = {"Host": f"{sni}:{port}", **(headers or {})}
            return super().req(method, path, form=form, headers=h, raw_body=raw_body)
        finally:
            http.client.HTTPConnection = orig


@pytest.fixture
def ui(tmp_path):
    svc = ApplianceService(str(tmp_path / "data"), source=SimulatedA2dpSource(), stream_port=0, advertise_host="bridge",
                           lms_factory=Lmses(), monitor=fake_tree(tmp_path), echo=None, auth_iterations=1000, discover=lambda timeout: [])
    svc.store.update({"mdns_enabled": False, "extra_hostnames": "bridge.home.arpa"})
    svc.access = LocalAccess(svc.store, str(tmp_path / "data"), svc.instance_id(), 0, 0, log=svc.logs, addresses=lambda: ["127.0.0.1"])
    svc.access.start(); svc.start()
    w = WebUI(svc, "127.0.0.1", 0, tls_port=0); port = w.start()
    code = open(svc.auth.code_file).read().strip()
    c = Client(port)
    c.post("/setup", {"code": code, "password": PW, "confirm": PW}, csrf=False)
    for path, form in (("/setup/bluetooth", {"device_name": "Wohnzimmer"}), ("/setup/lyrion", {"lms_host": "10.0.0.5", "lms_port": "9000"}),
                       ("/setup/target", {"target_player": P1}), ("/setup/finish", {})):
        c.post(path, form)
    yield type("U", (), dict(svc=svc, web=w, c=c, port=port, tls_port=w.tls_port, ca=svc.access.certs.p["ca_crt"]))
    w.stop(); svc.stop()


def flash(c):
    return urllib.parse.unquote(c.cookies.get("bab_flash", ""))


def test_foreign_host_names_are_refused_before_anything_else(ui):
    for host in ("evil.example", "hifi-bridge.local.evil.example", "rebind.attacker.test:8080"):
        r = ui.c.get("/login", headers={"Host": host})
        assert r.status == 421, host
    for host in (NAME, "bridge.home.arpa", f"127.0.0.1:{ui.port}", "localhost"):
        assert ui.c.get("/trust", headers={"Host": host}).status == 200, host


def test_trust_page_and_ca_downloads_are_public(ui):
    anon = Client(ui.port)
    page = anon.get("/trust").body
    fp = ui.svc.access.certs.ca_info()["sha256"]
    assert fp in page and "/ca.crt" in page and "iPhone" in page and f"https://{NAME}:{ui.tls_port}/" in page
    pem = anon.get("/ca.crt")
    assert pem.status == 200 and pem.body == open(ui.ca).read() and "attachment" in pem.getheader("Content-Disposition")
    assert anon.get("/ca.der").status == 200


def test_https_listener_serves_the_ui_with_a_verifiable_certificate_and_secure_cookies(ui):
    tc = TlsClient(ui.tls_port, ui.ca)
    r = tc.post("/login", {"password": PW}, csrf=False)
    cookie = next(v for k, v in r.headers_list if k == "Set-Cookie" and v.startswith("bab_session="))
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    page = tc.get("/")
    assert page.status == 200 and "Hi-Fi Bridge" in page.body and page.getheader("Cross-Origin-Opener-Policy") == "same-origin"
    plain = ui.c.post("/login", {"password": PW}, csrf=False)
    assert all("Secure" not in v for k, v in plain.headers_list if k == "Set-Cookie" and v.startswith("bab_session="))
    with pytest.raises(ssl.SSLCertVerificationError):                 # a name the certificate does not cover is NOT accepted
        TlsClient(ui.tls_port, ui.ca, server_hostname="other.local").get("/trust")


def test_https_only_cannot_be_enabled_over_http_and_then_redirects(ui):
    ui.c.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "", "https_only": "on"})
    assert ui.svc.store.get("https_only") is False and "HTTPS" in flash(ui.c)
    tc = TlsClient(ui.tls_port, ui.ca)
    tc.post("/login", {"password": PW}, csrf=False)
    tc.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "", "https_only": "on"})
    assert ui.svc.store.get("https_only") is True
    r = ui.c.get("/settings?x=1", headers={"Host": f"{NAME}:{ui.port}"})
    assert r.status == 308 and r.getheader("Location") == f"https://{NAME}:{ui.tls_port}/settings?x=1"
    assert ui.c.get("/trust").status == 200 and ui.c.get("/ca.crt").status == 200          # bootstrap stays reachable over HTTP


def test_forwarded_headers_only_from_trusted_proxies(ui):
    ui.c.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "", "https_only": ""})
    fwd = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.168.1.50", "X-Forwarded-Host": NAME}
    r = Client(ui.port).post("/login", {"password": PW}, csrf=False, headers=fwd)
    assert all("Secure" not in v for k, v in r.headers_list if k == "Set-Cookie")          # untrusted: headers ignored
    ui.c.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "127.0.0.1/32", "https_only": ""})
    assert ui.svc.store.get("trusted_proxies") == "127.0.0.1/32"
    r = Client(ui.port).post("/login", {"password": PW}, csrf=False, headers=fwd)
    assert any("Secure" in v for k, v in r.headers_list if k == "Set-Cookie" and v.startswith("bab_session="))
    assert Client(ui.port).get("/trust", headers={"X-Forwarded-Host": "evil.example"}).status == 421   # host check applies to proxies too
    ui.c.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "not-a-net", "https_only": ""})
    assert "not a valid address" in flash(ui.c)


def test_custom_certificate_upload_switches_the_https_certificate(ui, tmp_path):
    k, c = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
                    "-subj", "/CN=bridge.home.arpa", "-addext", "subjectAltName=DNS:bridge.home.arpa", "-keyout", str(k), "-out", str(c)],
                   check=True, capture_output=True)
    ui.c.post("/settings/access/custom", {"custom_cert_pem": c.read_text(), "custom_key_pem": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"})
    assert "key does not belong" in flash(ui.c) or "PEM" in flash(ui.c)
    ui.c.post("/settings/access/custom", {"custom_cert_pem": c.read_text(), "custom_key_pem": k.read_text()})
    assert "installed" in flash(ui.c)
    ui.c.post("/settings/access/tls", {"tls_mode": "custom", "trusted_proxies": "", "https_only": ""})
    assert TlsClient(ui.tls_port, str(c), server_hostname="bridge.home.arpa").get("/trust").status == 200
    assert "custom.key" not in ui.c.get("/settings/access").body and k.read_text().splitlines()[1] not in ui.c.get("/settings/access").body


def test_healthz_reports_a_hung_control_loop(ui):
    r = ui.c.get("/healthz")
    h = json.loads(r.body)
    assert r.status == 200 and h["status"] in ("ok", "degraded") and h["checks"]["control_loop"] is True
    ui.svc.runtime._stop.set(); time.sleep(0.3)                       # the control loop stops beating (like a hang)
    r = ui.c.get("/healthz")
    h = json.loads(r.body)
    assert r.status == 503 and h["status"] == "failed" and h["checks"]["control_loop"] is False


def test_settings_overview_and_sections(ui):
    page = ui.c.get("/settings").body
    for n in ("lyrion", "playback", "bluetooth", "access", "mqtt", "logging", "appearance", "security"):
        assert f'href="/settings/{n}"' in page
        sub = ui.c.get(f"/settings/{n}")
        assert sub.status == 200 and 'href="/settings"' in sub.body, n
    assert "Not enabled." in page                                        # MQTT disabled by default
    mq = ui.c.get("/settings/mqtt").body
    assert 'name="mqtt_allow_pairing"' in mq and "checked" not in mq.split('name="mqtt_allow_pairing"')[1][:30]


def test_error_messages_are_alerts_that_receive_focus(ui):
    ui.c.post("/settings/lyrion", {"lms_host": "bad host!", "lms_port": "9000"})
    body = ui.c.get("/settings/lyrion").body
    assert re.search(r'<div class="flash error" role="alert" tabindex="-1" data-focus="1">', body)
    ui.c.post("/settings/lyrion", {"lms_host": "10.0.0.5", "lms_port": "9000"})
    assert '<div class="flash" role="status">' in ui.c.get("/settings/lyrion").body


def test_header_separates_product_and_device_name(ui):
    body = ui.c.get("/").body
    assert "<strong>Hi-Fi Bridge</strong>" in body and "Wohnzimmer" in body and "<title>Status · Wohnzimmer – Hi-Fi Bridge</title>" in body


def test_decision_48_no_hsts_in_any_mode(ui, tmp_path):
    """HSTS is off for the local WebUI (DECISIONS #48): no Strict-Transport-Security header over HTTP, HTTPS (local CA) or an own cert."""
    for r in (ui.c.get("/trust"), ui.c.get("/"), TlsClient(ui.tls_port, ui.ca).get("/trust"), TlsClient(ui.tls_port, ui.ca).get("/login")):
        assert r.getheader("Strict-Transport-Security") is None
    k, c = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
                    "-subj", "/CN=bridge.home.arpa", "-addext", "subjectAltName=DNS:bridge.home.arpa", "-keyout", str(k), "-out", str(c)],
                   check=True, capture_output=True)
    ui.c.post("/settings/access/custom", {"custom_cert_pem": c.read_text(), "custom_key_pem": k.read_text()})
    ui.c.post("/settings/access/tls", {"tls_mode": "custom", "trusted_proxies": "", "https_only": ""})
    assert TlsClient(ui.tls_port, str(c), server_hostname="bridge.home.arpa").get("/trust").getheader("Strict-Transport-Security") is None
    import btbridge.webui.server as S
    assert "Strict-Transport-Security" not in open(S.__file__).read()          # static guard: no code path sets it


def test_exactly_the_bootstrap_endpoints_are_public():
    """Guards against a decorator slipping to the wrong function: the set of unauthenticated routes is fixed and reviewed."""
    import btbridge.webui.views as V
    public = sorted(f"{m} {p}" for (m, p), fn in V.routes(None).items() if getattr(fn, "public", False))
    assert public == ["GET /ca.crt", "GET /ca.der", "GET /healthz", "GET /login", "GET /manifest.webmanifest", "GET /setup", "GET /trust",
                      "GET /trust/proof", "POST /lang", "POST /login", "POST /setup"], public


def test_agpl_source_offer_in_every_page_footer(ui):
    """AGPL-3.0 section 13: users interacting over the network are offered the corresponding source (public pages and the app)."""
    from btbridge import SOURCE_URL, __version__
    for client, path in ((Client(ui.port), "/trust"), (ui.c, "/"), (ui.c, "/settings")):
        body = client.get(path).body
        assert f'href="{SOURCE_URL}"' in body and "AGPL-3.0-only" in body and __version__ in body, path
    assert SOURCE_URL == "https://github.com/iskaron/hifi-bridge"
