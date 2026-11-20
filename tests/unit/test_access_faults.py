"""UNIT/INTEGRATION (real openssl 3 and Python ssl, fake mDNS backends): what happens to the device's HTTPS certificate when something under
it changes or breaks - a regenerated CA, a torn certificate file, a new DHCP address, a missing custom certificate, a vanished openssl.
The owner must end with a working certificate for the device's current names, or a clear error, never a 500 page. Coverage package 7b-3."""
import json
import os
import ssl
import time

import pytest
from test_avahi import FakeMdns, FakeStore

import btbridge.net.access as access_mod
from btbridge.net.access import LocalAccess
from btbridge.net.certs import CertError, CertManager, cert_info, _fsync_dir

NAMES = ["hifi-bridge.local", "192.168.1.20"]


@pytest.fixture
def cm(tmp_path):
    return CertManager(str(tmp_path), "ab12cd34")


def make_access(tmp_path, addresses, mdns=None, **store) -> LocalAccess:
    store.setdefault("tls_mode", "local_ca")
    return LocalAccess(FakeStore(**store), str(tmp_path), "ab12cd34", 8080, 8443, log=lambda m: None,
                       addresses=addresses, mdns_factory=mdns or (lambda *a, **k: FakeMdns(*a, **k)),
                       avahi_factory=lambda *a, **k: FakeMdns(*a, **k))


# ---------------------------------------------------------------- certificate manager
def test_a_regenerated_ca_with_the_same_name_still_forces_a_new_server_certificate(cm):
    cm.ensure_server(NAMES)
    assert cm.needs_renewal(NAMES) is None
    old_sha = cm.ca_info()["sha256"]
    for k in ("ca_crt", "ca_key"):                                  # the CA is lost (restored backup without it) and created again
        os.unlink(cm.p[k])
    cm.ensure_ca()
    assert cm.ca_info()["sha256"] != old_sha
    assert cm.needs_renewal(NAMES) == "ca_changed"                  # same subject, other key: the old certificate no longer verifies
    assert cm.ensure_server(NAMES) == "ca_changed"
    assert cm.needs_renewal(NAMES) is None


def test_a_torn_server_certificate_is_reissued(cm):
    cm.ensure_server(NAMES)
    with open(cm.p["srv_crt"], "w") as fh:
        fh.write("-----BEGIN CERTIFICATE-----\ngarbage\n-----END CERTIFICATE-----\n")
    assert cm.needs_renewal(NAMES) == "unreadable"
    assert cm.ensure_server(NAMES) == "unreadable"
    assert cert_info(cm.p["srv_crt"])["sans"]


def test_ca_info_is_none_without_a_ca_and_a_certificate_needs_at_least_one_usable_name(cm):
    assert cm.ca_info() is None
    with pytest.raises(CertError, match="no name usable"):
        cm.ensure_server(["example.org", "8.8.8.8"])                # only names the local CA may not vouch for
    assert not os.path.exists(cm.p["srv_crt"])


def test_a_damaged_state_file_is_treated_as_no_state(cm):
    cm.ensure_server(NAMES)
    with open(cm.p["state"], "w") as fh:
        fh.write("{not json")
    assert cm._read_state() == {}
    with open(cm.p["state"], "w") as fh:
        json.dump(["a list"], fh)
    assert cm._read_state() == {} and cm.needs_renewal(NAMES) is None      # without a recorded CA hash the checks that remain still pass


def test_missing_openssl_is_a_clear_error_not_a_crash(cm, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(CertError, match="openssl not available"):
        cm.ensure_ca()


def test_fsync_of_a_directory_that_cannot_be_opened_is_ignored(tmp_path):
    _fsync_dir(str(tmp_path / "no-such-directory" / "file"))        # best-effort durability: must not raise


# ---------------------------------------------------------------- local access
def test_without_an_installed_custom_certificate_https_is_unavailable_with_a_reason(tmp_path):
    messages = []
    acc = make_access(tmp_path, lambda: ["192.168.1.20"], tls_mode="custom")
    acc.log = messages.append
    assert acc.ensure_certificates() is None
    assert acc.ctx is None and not acc.tls_available() and acc.cert_error == "no custom certificate installed"
    assert any("no custom certificate installed" in m for m in messages)
    assert acc.info()["error"] == "no custom certificate installed" and acc.info()["server"] is None


def test_an_unreadable_certificate_is_shown_as_missing_on_the_access_page_not_as_an_error(tmp_path):
    acc = make_access(tmp_path, lambda: ["192.168.1.20"])
    acc.ensure_certificates()
    assert acc.info()["server"] is not None
    with open(acc.certs.p["srv_crt"], "w") as fh:
        fh.write("torn")
    assert acc.info()["server"] is None


@pytest.mark.parametrize("backend", ["avahi", "builtin"])
def test_a_new_dhcp_address_gets_into_the_certificate_without_a_restart(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(access_mod, "ADDRESS_CHECK_S", 0.05)
    addrs = ["192.168.1.20"]
    messages = []
    acc = make_access(tmp_path, lambda: list(addrs), mdns_backend=backend)
    acc.log = messages.append
    acc.start()
    try:
        assert "192.168.1.20" in cert_info(acc.certs.p["srv_crt"])["sans"]
        addrs[:] = ["192.168.1.77"]                                   # DHCP lease changed
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and "192.168.1.77" not in cert_info(acc.certs.p["srv_crt"])["sans"]:
            time.sleep(0.05)
        sans = cert_info(acc.certs.p["srv_crt"])["sans"]
        assert "192.168.1.77" in sans and "192.168.1.20" not in sans
        assert isinstance(acc.ctx, ssl.SSLContext)                  # and the listener has the new one
        if backend == "builtin":                                      # (the Avahi path re-issues inside the re-publish and does not log it)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not any("certificate re-issued (names_changed)" in m for m in messages):
                time.sleep(0.05)
            assert any("certificate re-issued (names_changed)" in m for m in messages)
    finally:
        acc.stop()


def test_the_periodic_check_re_issues_a_certificate_that_is_about_to_expire(tmp_path, monkeypatch):
    monkeypatch.setattr(access_mod, "ADDRESS_CHECK_S", 0.05)
    monkeypatch.setattr(access_mod, "RENEW_CHECK_S", 0.1)
    acc = make_access(tmp_path, lambda: ["192.168.1.20"])
    acc.start()
    try:
        serial = cert_info(acc.certs.p["srv_crt"])["not_after"]
        import datetime as dt
        acc.certs.clock = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650)       # far in the future: "expiring"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and cert_info(acc.certs.p["srv_crt"])["not_after"] == serial:
            time.sleep(0.05)
        assert cert_info(acc.certs.p["srv_crt"])["not_after"] != serial
    finally:
        acc.stop()


def test_a_backend_that_loses_its_records_is_asked_to_publish_again_and_a_failing_check_never_stops_the_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(access_mod, "ADDRESS_CHECK_S", 0.05)
    checks, messages = [], []

    class Flaky(FakeMdns):
        def check(self):
            checks.append(1)
            if len(checks) == 1:
                raise OSError("avahi-daemon restarted")

    acc = make_access(tmp_path, lambda: ["192.168.1.20"], mdns=Flaky, tls_mode="proxy", mdns_backend="builtin")
    acc.log = messages.append
    acc.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and len(checks) < 3:
            time.sleep(0.05)
        assert len(checks) >= 3                                       # the loop kept running after the first failure
        assert any("mdns: not published (OSError: avahi-daemon restarted); retrying" in m for m in messages)
    finally:
        acc.stop()
    assert acc.mdns.started is False                                # stop() says goodbye through the backend
