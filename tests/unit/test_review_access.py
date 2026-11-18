"""UNIT (fake mDNS/Avahi backends, real openssl certificates, real HTTP/TLS on localhost): regressions for review findings on local
access (reports/REVIEW_PHASE2B_EXISTING_CODE.md: H5, H6, M9, L12)."""
import pytest
from test_avahi import FakeStore

from btbridge.net import avahi as A
from btbridge.net import mdns as M
from btbridge.net.access import LocalAccess, instance_name


# ---------------------------------------------------------------- H5: device names that do not fit one DNS label
@pytest.mark.parametrize("name", ["ä" * 32, "Гостиная и кухня, второй этаж", "Wohnzimmer", "😀" * 32])
def test_instance_name_always_fits_one_dns_label(name):
    inst = instance_name(name)
    raw = inst.encode("utf-8")
    assert 0 < len(raw) <= 63 and inst.startswith("Hi-Fi Bridge (") and inst.endswith(")")
    M.enc_name(inst.replace(".", "_"))                      # the responder's encoder accepts it


def test_short_names_are_unchanged():
    assert instance_name("Wohnzimmer") == "Hi-Fi Bridge (Wohnzimmer)"


class Recording:
    def __init__(self, *a, on_name=None, log=None, fail=None):
        self.args, self.on_name, self.fail, self.started = a, on_name, fail, False

    def connect(self):
        return "avahi 0.8"

    def start(self):
        if self.fail:
            raise self.fail
        M.enc_name(self.args[1].replace(".", "_"))           # as the built-in responder does for the instance label
        self.started = True
        self.on_name("hifi-bridge.local")

    def stop(self):
        self.started = False


def _access(tmp_path, avahi_fail=None, builtin_fail=None, **store):
    made = {}

    def avahi_factory(*a, **k):
        made["avahi"] = Recording(*a, fail=avahi_fail, **k); return made["avahi"]

    def mdns_factory(*a, **k):
        made["builtin"] = Recording(*a, fail=builtin_fail, **k); return made["builtin"]
    acc = LocalAccess(FakeStore(**store), str(tmp_path), "ab12cd34", 8080, 8443, log=lambda m: None, addresses=lambda: ["192.168.1.20"],
                      mdns_factory=mdns_factory, avahi_factory=avahi_factory)
    return acc, made


def test_long_non_ascii_device_name_does_not_abort_the_builtin_responder(tmp_path):
    acc, made = _access(tmp_path, device_name="ä" * 32, mdns_backend="builtin")
    acc._start_mdns()                                         # was: ValueError (label >= 64 bytes) out of LocalAccess.start()
    assert made["builtin"].started and acc.mdns_backend == "builtin"


def test_an_avahi_error_reply_degrades_instead_of_aborting(tmp_path):
    acc, made = _access(tmp_path, avahi_fail=A.AvahiUnavailable("org.freedesktop.Avahi.InvalidServiceNameError"), mdns_backend="avahi")
    acc._start_mdns()                                         # was: AvahiUnavailable (not an OSError) out of appliance.run()
    assert acc.mdns is None and acc.mdns_backend == "unavailable"


def test_any_unexpected_mdns_error_degrades(tmp_path):
    acc, _ = _access(tmp_path, builtin_fail=ValueError("bad label"), mdns_backend="builtin")
    acc._start_mdns()
    assert acc.mdns is None and acc.mdns_backend == "unavailable"


# ---------------------------------------------------------------- H6: torn certificate files heal instead of crash-looping
def _certs(tmp_path, faults=None):
    from btbridge.net.certs import CertManager
    logs = []
    cm = CertManager(str(tmp_path / "data"), "ab12cd34", log=logs.append, fault=(lambda k, d: faults.append((k, d))) if faults is not None else None)
    return cm, logs


@pytest.mark.parametrize("victim", ["ca_crt", "ca_key"])
def test_truncated_ca_is_regenerated_with_a_new_fingerprint_and_a_fault(tmp_path, victim):
    faults = []
    cm, _ = _certs(tmp_path, faults)
    old = cm.ensure_ca()["sha256"]
    open(cm.p[victim], "w").close()                           # power cut: openssl wrote the file in place, never fsynced
    assert cm.ca_info() is None or victim == "ca_key"         # an unreadable CA is no CA (no exception)
    new = cm.ensure_ca()["sha256"]
    assert new != old and faults and faults[0][0] == "certificate"
    cm.issue_server(["hifi-bridge.local"])                    # the healed CA signs again


def test_zero_length_server_key_is_reissued(tmp_path):
    cm, _ = _certs(tmp_path)
    cm.issue_server(["hifi-bridge.local"])
    open(cm.p["srv_key"], "w").close()
    assert cm.needs_renewal(["hifi-bridge.local"]) in ("key_mismatch", "unreadable")
    assert cm.ensure_server(["hifi-bridge.local"])
    import ssl
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cm.p["srv_crt"], cm.p["srv_key"])


@pytest.mark.parametrize("fail_at", [1, 2])
def test_a_crash_between_the_write_steps_never_leaves_an_unusable_pair(tmp_path, monkeypatch, fail_at):
    import os
    import ssl
    cm, _ = _certs(tmp_path)
    cm.ensure_ca()
    cm.issue_server(["hifi-bridge.local"])
    real, n = os.replace, {"i": 0}

    def crashing_replace(a, b):
        n["i"] += 1
        if n["i"] == fail_at:
            raise OSError("power cut")
        return real(a, b)
    monkeypatch.setattr(os, "replace", crashing_replace)
    with pytest.raises(OSError):
        cm.issue_server(["hifi-bridge.local", "bridge.home.arpa"])
    monkeypatch.setattr(os, "replace", real)
    cm.ensure_server(["hifi-bridge.local", "bridge.home.arpa"])     # heals on the next check
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cm.p["srv_crt"], cm.p["srv_key"])
    assert cm.needs_renewal(["hifi-bridge.local", "bridge.home.arpa"]) is None


def test_bootfiles_with_a_truncated_ca_still_writes_the_status_file(tmp_path):
    from btbridge import bootfiles
    d, b = tmp_path / "data", tmp_path / "boot"
    b.mkdir()
    bootfiles.run(str(d), str(b), iterations=1000, log=lambda m: None)
    open(d / "certs" / "ca.crt", "w").close()
    r = bootfiles.run(str(d), str(b), iterations=1000, log=lambda m: None)    # was: CertError, hifi-status.txt never written
    status = (b / "hifi-status.txt").read_text()
    assert r["ca_sha256"] in status and "setup code" in status


def test_bootfiles_without_any_usable_ca_still_writes_the_status_file(tmp_path, monkeypatch):
    from btbridge import bootfiles
    from btbridge.net import certs
    d, b = tmp_path / "data", tmp_path / "boot"
    b.mkdir()

    def no_openssl(*a, **k):
        raise certs.CertError("openssl not available")
    monkeypatch.setattr(certs, "_run", no_openssl)
    r = bootfiles.run(str(d), str(b), iterations=1000, log=lambda m: None)
    status = (b / "hifi-status.txt").read_text()
    assert r["ca_sha256"] is None and "setup code" in status and "not available" in status


def test_appliance_starts_and_heals_with_a_truncated_ca(tmp_path, monkeypatch):
    import threading
    from test_service import wait
    from btbridge import appliance as AP
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    import socket
    d = tmp_path / "data"
    with socket.socket() as x:
        x.bind(("127.0.0.1", 0)); tls_port = str(x.getsockname()[1])
    args = ["--data", str(d), "--http-port", "0", "--https-port", tls_port, "--stream-port", "0", "--no-mdns", "--source", "none",
            "--thermal-path", "", "--advertise-host", "127.0.0.1", "--no-mqtt"]
    from btbridge.net.certs import CertManager
    from btbridge.config import ConfigStore
    st = ConfigStore(str(d)); st.update({"instance_id": "ab12cd34"})
    CertManager(str(d), "ab12cd34").issue_server(["hifi-bridge.local"])
    open(d / "certs" / "ca.crt", "w").close()
    done, info = threading.Event(), {}
    t = threading.Thread(target=AP.run, args=(AP.parser().parse_args(args), None),
                         kwargs={"ready": lambda s, u: info.update(svc=s, ui=u), "done": done}, daemon=True)
    t.start()
    assert wait(lambda: "svc" in info, t=20), "appliance.run did not come up"
    try:
        assert info["svc"].access.certs.ca_info() and info["svc"].access.tls_available()
        assert info["ui"]._httpsd is not None
    finally:
        done.set(); t.join(10)
