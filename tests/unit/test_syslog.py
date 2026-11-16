"""UNIT: RFC 5424/5425 framing, severity mapping, non-blocking queue, mandatory certificate verification (real TLS with the device CA)."""
import socket
import ssl
import threading
import time

import pytest

from btbridge.net.certs import CertManager
from btbridge.system.syslog import RemoteSyslog, frame, severity_of


def test_rfc5425_octet_counting_and_rfc5424_header():
    f = frame("crit", "hifi-bridge", "hifi-bridge", "FAULT thermal: critical", ts=0.5)
    length, _, body = f.partition(b" ")
    assert int(length) == len(body)
    assert body.startswith(b"<26>1 1970-01-01T00:00:00.500000Z hifi-bridge hifi-bridge - - - FAULT thermal")   # daemon(3)*8 + crit(2)


@pytest.mark.parametrize("line,sev", [("FAULT thermal: critical", "crit"), ("mqtt: invalid value", "info"), ("discovery failed: x", "err"),
                                      ("[ctl] reachability unreachable", "warning"), ("service started", "info")])
def test_severity(line, sev):
    assert severity_of(line) == sev


def test_queue_is_bounded_and_never_blocks():
    s = RemoteSyslog("127.0.0.1", 9, queue_size=5)
    t0 = time.monotonic()
    for i in range(50):
        s.emit(f"line {i}")
    assert time.monotonic() - t0 < 0.1 and len(s.q) == 5 and s.dropped == 45 and s.q[-1][2] == "line 49"


def tls_server(tmp_path, names):
    cm = CertManager(str(tmp_path), "sysl0g01"); cm.issue_server(names)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(*cm.server_paths())
    ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(4)
    got, handshakes = [], []

    def serve():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(c, server_side=True) as s:
                    handshakes.append("ok")
                    buf = b""
                    s.settimeout(3)
                    while True:
                        d = s.recv(4096)
                        if not d:
                            break
                        buf += d
                        while b" " in buf:
                            n, _, rest = buf.partition(b" ")
                            if len(rest) < int(n):
                                break
                            got.append(rest[:int(n)].decode()); buf = rest[int(n):]
            except (ssl.SSLError, OSError):
                handshakes.append("failed")
    threading.Thread(target=serve, daemon=True).start()
    return ls, cm, got, handshakes


def test_delivers_over_verified_tls_and_refuses_an_untrusted_server(tmp_path):
    ls, cm, got, hs = tls_server(tmp_path / "a", ["127.0.0.1"])
    port = ls.getsockname()[1]
    ok = RemoteSyslog("127.0.0.1", port, ca_pem=cm.ca_pem(), backoff=(0.1, 0.2)); ok.start()
    ok.emit("FAULT thermal: critical: 83.5 C"); ok.emit("service started")
    t0 = time.monotonic()
    while len(got) < 2 and time.monotonic() - t0 < 5:
        time.sleep(0.05)
    ok.stop()
    assert len(got) == 2 and got[0].startswith("<26>1 ") and "FAULT thermal" in got[0]
    other = CertManager(str(tmp_path / "b"), "other001"); other.ensure_ca()
    bad = RemoteSyslog("127.0.0.1", port, ca_pem=other.ca_pem(), backoff=(0.1, 0.2)); bad.start()
    bad.emit("must not be delivered"); time.sleep(1.0); bad.stop(flush_s=0)
    assert not any("must not be delivered" in g for g in got) and "CERTIFICATE_VERIFY_FAILED" in (bad.last_error or "")
    ls.close()
