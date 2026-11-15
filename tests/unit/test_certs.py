"""UNIT/INTEGRATION (real openssl 3 + Python ssl in the lab image): local CA, server certificates, renewal, custom certificates,
name constraints, Host validation. Nothing here disables certificate verification."""
import datetime as dt
import os
import socket
import ssl
import stat
import subprocess
import threading

import pytest

from btbridge.net.certs import CertError, CertManager, cert_info, permitted
from btbridge.net.hostcheck import HostPolicy, split_host

NAMES = ["hifi-bridge.local", "bridge.home.arpa", "192.168.1.20"]


@pytest.fixture
def cm(tmp_path):
    return CertManager(str(tmp_path), "ab12cd34")


def verify(ca, crt, *extra):
    return subprocess.run(["openssl", "verify", "-CAfile", ca, *extra, crt], capture_output=True, text=True)


def test_ca_is_name_constrained_private_and_stable(cm, tmp_path):
    info = cm.ensure_ca()
    assert "Hi-Fi Bridge local CA ab12cd34" in info["subject"] and len(info["sha256"]) == 95
    assert stat.S_IMODE(os.stat(cm.p["ca_key"]).st_mode) == 0o600
    txt = subprocess.run(["openssl", "x509", "-in", cm.p["ca_crt"], "-noout", "-text"], capture_output=True, text=True).stdout
    assert "Name Constraints: critical" in txt and "Permitted" in txt and "DNS:local" in txt and "pathlen:0" in txt
    assert cm.ensure_ca()["sha256"] == info["sha256"]                         # not regenerated on every start


def test_server_certificate_verifies_for_names_and_ips(cm):
    info = cm.issue_server(NAMES)
    assert set(info["sans"]) == set(NAMES)
    days = (dt.datetime.fromisoformat(info["not_after"]) - dt.datetime.now(dt.timezone.utc)).days
    assert 395 <= days <= 397 and stat.S_IMODE(os.stat(cm.p["srv_key"]).st_mode) == 0o600
    assert verify(cm.p["ca_crt"], cm.p["srv_crt"], "-verify_hostname", "hifi-bridge.local").returncode == 0
    assert verify(cm.p["ca_crt"], cm.p["srv_crt"], "-verify_ip", "192.168.1.20").returncode == 0
    r = verify(cm.p["ca_crt"], cm.p["srv_crt"], "-verify_hostname", "other.local")
    assert r.returncode != 0 and "hostname mismatch" in (r.stdout + r.stderr).lower()


def test_names_outside_the_constraints_are_never_put_into_the_certificate(cm):
    logs = []; cm.log = logs.append
    info = cm.issue_server(["hifi-bridge.local", "bridge.example.com", "8.8.8.8"])
    assert info["sans"] == ["hifi-bridge.local"] and any("outside" in m for m in logs)
    assert not permitted("example.com") and not permitted("8.8.8.8") and permitted("x.local") and permitted("fd00::1")


def test_a_forged_certificate_for_a_public_name_fails_verification_even_with_the_ca_key(cm, tmp_path):
    cm.ensure_ca()
    ext = tmp_path / "e.cnf"; ext.write_text("[x]\nsubjectAltName=DNS:www.example.com\nbasicConstraints=CA:FALSE\n")
    subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(tmp_path / "k"),
                    "-out", str(tmp_path / "csr"), "-subj", "/CN=www.example.com"], check=True, capture_output=True)
    subprocess.run(["openssl", "x509", "-req", "-in", str(tmp_path / "csr"), "-CA", cm.p["ca_crt"], "-CAkey", cm.p["ca_key"], "-days", "30",
                    "-extfile", str(ext), "-extensions", "x", "-out", str(tmp_path / "forged.crt")], check=True, capture_output=True)
    r = verify(cm.p["ca_crt"], str(tmp_path / "forged.crt"))
    assert r.returncode != 0 and "subtree violation" in (r.stdout + r.stderr).lower()


def test_renewal_reasons(cm):
    assert cm.needs_renewal(NAMES) == "missing"
    cm.ensure_server(NAMES)
    assert cm.needs_renewal(NAMES) is None
    assert cm.needs_renewal(NAMES + ["192.168.1.21"]) == "names_changed"                 # DHCP gave a new address
    before = cert_info(cm.p["srv_crt"])["sha256"]
    cm.clock = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=380)
    assert cm.needs_renewal(NAMES) == "expiring"
    assert cm.ensure_server(NAMES) == "expiring" and cert_info(cm.p["srv_crt"])["sha256"] != before     # really re-issued


def test_real_tls_handshake_with_the_local_ca_and_hostname_check(cm):
    cm.issue_server(["hifi-bridge.local", "127.0.0.1"])
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); srv_ctx.load_cert_chain(*cm.server_paths())
    ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(1); port = ls.getsockname()[1]

    def serve():
        c, _ = ls.accept()
        try:
            with srv_ctx.wrap_socket(c, server_side=True) as s:
                s.sendall(b"ok")
        except (ssl.SSLError, OSError):
            pass
    for host, expect_ok in (("hifi-bridge.local", True), ("127.0.0.1", True), ("evil.local", False)):
        t = threading.Thread(target=serve); t.start()
        cli = ssl.create_default_context(cafile=cm.p["ca_crt"])               # verification ON, hostname check ON
        try:
            with cli.wrap_socket(socket.create_connection(("127.0.0.1", port)), server_hostname=host) as s:
                ok = s.recv(2) == b"ok"
        except ssl.SSLCertVerificationError:
            ok = False
        t.join(5)
        assert ok is expect_ok, host
    ls.close()


def test_custom_certificate_validation(cm, tmp_path):
    def mk(name, days=30, key_for=None):
        k, c = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", str(days),
                        "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}", "-keyout", str(k), "-out", str(c)],
                       check=True, capture_output=True)
        return c.read_text(), k.read_text()
    cert, key = mk("bridge.example.org")
    info = cm.install_custom(cert, key, "bridge.example.org")                # public names are fine for an owner certificate
    assert info["sans"] == ["bridge.example.org"] and cm.server_paths("custom")[0].endswith("custom.crt")
    assert stat.S_IMODE(os.stat(cm.p["cus_key"]).st_mode) == 0o600
    other_cert, other_key = mk("other.example.org")
    for c, k, name, code in ((cert, other_key, "bridge.example.org", "key_mismatch"), (cert, key, "nope.example.org", "name_not_covered"),
                             ("junk", key, "x", "pem_format")):
        with pytest.raises(CertError, match=code):
            cm.install_custom(c, k, name)
    cm.clock = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=60)
    with pytest.raises(CertError, match="expired"):
        cm.install_custom(cert, key, "bridge.example.org")
    cm.remove_custom(); assert cm.custom_info() is None


def test_ca_downloads(cm):
    cm.ensure_ca()
    assert cm.ca_pem().startswith("-----BEGIN CERTIFICATE-----") and cm.ca_der()[:1] == b"\x30"


# ---- Host validation (DNS rebinding) ----
@pytest.mark.parametrize("host,ok", [("hifi-bridge.local", True), ("HIFI-BRIDGE.local:8443", True), ("hifi-bridge.local.", True),
                                     ("192.168.1.20:443", True), ("[fd00::1]:8443", True), ("localhost:8080", True),
                                     ("proxy.home.arpa", True), ("evil.example", False), ("hifi-bridge.local.evil.example", False),
                                     ("", False), (None, False), ("hifi-bridge", False)])
def test_host_policy(host, ok):
    assert HostPolicy(lambda: ["hifi-bridge.local", "proxy.home.arpa"]).allowed(host) is ok


def test_split_host():
    assert split_host("[::1]:80") == "::1" and split_host("a.local:80") == "a.local" and split_host("fd00::1") == "fd00::1"
