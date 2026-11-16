"""INTEGRATION: the bridge's remote syslog (via the real service's LogBuffer) -> REAL rsyslog 8 with TLS (gnutls). Certificates come from
the bridge's own CertManager (name syslog.lan). Checks delivery, RFC 5424 fields, redaction, and that a wrong CA delivers nothing."""
import os
import time

from btbridge.net.certs import CertManager
from btbridge.service import ApplianceService
from btbridge.system import SystemMonitor

OUT = "/syslog-out/messages"


def wait_for(cond, t=20.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.2)
    return False


def read():
    return open(OUT).read() if os.path.exists(OUT) else ""


def make(tmp_path, ca_pem):
    svc = ApplianceService(str(tmp_path), stream_port=0, advertise_host="bridge", echo=None, auth_iterations=1000,
                           monitor=SystemMonitor(thermal=None, data_dir=str(tmp_path)))
    svc.store.update({"syslog_enabled": True, "syslog_host": "syslog.lan", "syslog_port": 6514, "syslog_ca_pem": ca_pem,
                      "local_hostname": "hifi-bridge"})
    return svc


def test_service_logs_reach_real_rsyslog_over_verified_tls_redacted(tmp_path):
    ca = CertManager("/syslog-certs", "syslogca").ca_pem()
    svc = make(tmp_path / "a", ca); svc.start()
    token = svc.store.secret("stream_token")
    svc.logs(f"stream url http://bridge:8090/s/{token}/abc.wav must be redacted")
    svc.logs.fault("thermal", "critical: 83.5 C")
    assert wait_for(lambda: "critical: 83.5 C" in read()), read()[-500:]
    text = read()
    line = next(x for x in text.splitlines() if "must be redacted" in x)
    assert token not in text and token not in line and "/s/" in line                 # the URL arrived, the token did not
    fault = next(line for line in text.splitlines() if "critical: 83.5 C" in line)
    assert fault.split()[0] == "26" and "hifi-bridge" in fault                    # daemon.crit, our hostname/app
    assert svc.syslog.state()["sent"] >= 2
    svc.stop()


def test_a_wrong_ca_delivers_nothing(tmp_path):
    other = CertManager(str(tmp_path / "other"), "otherca1"); other.ensure_ca()
    svc = make(tmp_path / "b", other.ca_pem()); svc.start()
    svc.logs("SHOULD-NOT-ARRIVE-123")
    time.sleep(3)
    assert "SHOULD-NOT-ARRIVE-123" not in read()
    assert "CERTIFICATE_VERIFY_FAILED" in (svc.syslog.state()["last_error"] or "")
    svc.stop()
