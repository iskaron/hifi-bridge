"""UNIT (fake mDNS/Avahi backends, real openssl certificates, real HTTP/TLS on localhost): regressions for review findings on local
access (reports/REVIEW_PHASE2B_EXISTING_CODE.md: H5, H6, M9, L12)."""
import socket
import ssl
import time

import pytest
from test_avahi import FakeStore
from test_webui_access import NAME, TlsClient, ui  # noqa: F401  (TLS-enabled WebUI fixture)

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


# ---------------------------------------------------------------- L7: Avahi restarts and conflict storms
def test_avahi_daemon_restart_is_noticed_and_the_name_published_again():
    from test_avahi import FakeConn, Reply, pub
    p = pub([A.ESTABLISHED])
    p.start()
    adds = lambda c: [m for m, _ in c.calls if m == "AddService"]          # noqa: E731

    class Restarted(FakeConn):                                  # the old group object is gone after avahi-daemon restarted
        def send_and_get_reply(self, msg, timeout=None):
            from jeepney import HeaderFields
            if msg.header.fields[HeaderFields.member] == "GetState":
                return Reply(("org.freedesktop.Avahi.InvalidObjectError",), err=True)
            return super().send_and_get_reply(msg, timeout)
    p.conn = Restarted([A.ESTABLISHED])
    fresh = FakeConn([A.ESTABLISHED])

    def reconnect():
        p.conn = fresh
        return "avahi 0.8"
    p.connect = reconnect
    assert p.check() is True                                    # was: never checked, name gone until the bridge restarts
    assert p.state == "announced" and adds(fresh)
    assert p.check() is False                                   # healthy group: nothing to do


def test_avahi_still_down_is_retried_on_the_next_check():
    from test_avahi import FakeConn, pub
    p = pub([A.ESTABLISHED])
    p.start()

    def down():
        raise A.AvahiUnavailable("no daemon")
    p.conn.close(); p.conn = None
    p.connect = down
    with pytest.raises(A.AvahiUnavailable):
        p.check()
    p.connect = lambda: (setattr(p, "conn", FakeConn([A.ESTABLISHED])), "avahi 0.8")[1]
    assert p.check() is True and p.state == "announced"


def test_local_access_checks_the_mdns_backend_periodically(tmp_path):
    acc, made = _access(tmp_path, mdns_backend="builtin")
    acc._start_mdns()
    made["builtin"].check = lambda: (_ for _ in ()).throw(A.AvahiUnavailable("still down"))
    acc._check_mdns()                                           # errors are logged, never raised into the loop
    calls = []
    made["builtin"].check = lambda: calls.append(1) or False
    acc._check_mdns()
    assert calls == [1]


def test_builtin_responder_runs_one_reclaim_at_a_time():
    import threading
    r = M.MdnsResponder("hifi-bridge", "Hi-Fi Bridge (X)", [], addresses=lambda: ["192.168.1.20"])
    r.state = "announced"
    started, gate = [], threading.Event()
    r._reclaim = lambda: (started.append(1), gate.wait(2))
    for _ in range(50):                                         # a storm of conflicting announcements
        r._on_conflicting_announcement()
    time.sleep(0.2)
    assert len(started) == 1                                    # was: one thread per conflicting packet
    gate.set()


def test_builtin_responder_rate_limits_probing_after_15_conflicts_in_10_s():
    r = M.MdnsResponder("hifi-bridge", "Hi-Fi Bridge (X)", [], addresses=lambda: ["192.168.1.20"])
    now = [1000.0]
    sleeps, rounds = [], {"n": 0}
    r._clock = lambda: now[0]
    r._sleep = lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s))[0] and False

    class Done(Exception):
        pass

    def send(data, addr=None):
        rounds["n"] += 1
        if rounds["n"] > 25:
            raise Done()
        now[0] += 0.01
        r._conflict.set()                                       # every probe is answered by a conflicting claim
    r._send = send
    with pytest.raises(Done):
        r._claim()
    assert sleeps.count(5.0) >= 5                               # RFC 6762 s8.1: wait 5 s before each further probe


# ---------------------------------------------------------------- L9: certificate errors are translated, never raw
def test_an_unknown_certificate_error_is_translated_not_shown_raw(ui, monkeypatch):
    import urllib.parse
    from btbridge.net.certs import CertError

    def broken(cert, key):
        raise CertError("openssl x509 failed: unable to load certificate 140:error:0480006C:PEM routines")
    monkeypatch.setattr(ui.svc.access, "install_custom", broken)
    ui.c.cookies.pop("bab_flash", None)
    r = ui.c.post("/settings/access/custom", {"custom_cert_pem": "x", "custom_key_pem": "y"})
    assert r.status == 303 and r.getheader("Location") == "/settings/access"
    msg = urllib.parse.unquote(ui.c.cookies.get("bab_flash", ""))
    assert msg.startswith("e:") and "error.cert" not in msg and "openssl" not in msg and "PEM routines" not in msg


# ---------------------------------------------------------------- M1: the TLS handshake runs per connection, with a timeout
def test_idle_tcp_connection_does_not_block_https_for_others(ui):
    idle = socket.create_connection(("127.0.0.1", ui.tls_port))             # never starts a TLS handshake
    time.sleep(0.2)
    ctx = ssl.create_default_context(cafile=ui.ca)
    t0 = time.monotonic()
    with ctx.wrap_socket(socket.create_connection(("127.0.0.1", ui.tls_port), timeout=5), server_hostname=NAME) as tc:
        tc.sendall(f"GET /healthz HTTP/1.1\r\nHost: {NAME}\r\n\r\n".encode())
        assert tc.recv(100).startswith(b"HTTP/1.0 200")
    assert time.monotonic() - t0 < 2.0                                      # was: blocked until the idle peer went away
    idle.close()


def test_plain_http_on_the_tls_port_does_not_break_the_listener(ui):
    s = socket.create_connection(("127.0.0.1", ui.tls_port), timeout=5)
    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
    try:
        s.recv(100)
    except OSError:
        pass
    s.close()
    assert TlsClient(ui.tls_port, ui.ca).get("/healthz").status == 200


# ---------------------------------------------------------------- M3: CSRF origin check behind a trusted reverse proxy
def _proxy(ui):
    ui.c.post("/settings/access/tls", {"tls_mode": "local_ca", "trusted_proxies": "127.0.0.1/32", "https_only": ""})
    assert ui.svc.store.get("trusted_proxies") == "127.0.0.1/32"


def test_browser_login_through_a_trusted_proxy_passes_the_origin_check(ui):
    from test_webui import PW, Client
    _proxy(ui)
    via = {"Host": f"127.0.0.1:{ui.port}", "X-Forwarded-Host": "bridge.home.arpa", "X-Forwarded-Proto": "https",
           "X-Forwarded-For": "192.168.1.50", "Origin": "https://bridge.home.arpa"}
    r = Client(ui.port).post("/login", {"password": PW}, csrf=False, headers=via)
    assert r.status == 303 and r.getheader("Location") == "/"            # was: 403 (Origin compared with the raw Host)


def test_proxy_origin_check_still_refuses_foreign_origins(ui):
    from test_webui import PW, Client
    _proxy(ui)
    for origin in ("https://evil.example", "https://bridge.home.arpa.evil.example"):
        via = {"Host": f"127.0.0.1:{ui.port}", "X-Forwarded-Host": "bridge.home.arpa", "X-Forwarded-Proto": "https",
               "X-Forwarded-For": "192.168.1.50", "Origin": origin}
        assert Client(ui.port).post("/login", {"password": PW}, csrf=False, headers=via).status == 403, origin


def test_untrusted_peer_cannot_use_forwarded_host_to_pass_the_origin_check(ui):
    from test_webui import PW, Client
    via = {"Host": f"127.0.0.1:{ui.port}", "X-Forwarded-Host": "bridge.home.arpa", "Origin": "https://bridge.home.arpa"}
    assert Client(ui.port).post("/login", {"password": PW}, csrf=False, headers=via).status == 403


# ---------------------------------------------------------------- M4: login back-off cannot be bypassed
def test_forwarded_for_rotation_does_not_reset_the_back_off(ui):
    from test_webui import Client
    _proxy(ui)
    for i in range(8):                                                   # attacker varies the leftmost X-Forwarded-For entry
        Client(ui.port).post("/login", {"password": "wrong"}, csrf=False,
                             headers={"X-Forwarded-For": f"10.9.9.{i}, 192.168.1.50"})
    from btbridge.webui.auth import AuthError
    with pytest.raises(AuthError, match="rate_limited"):
        ui.svc.auth.limiter.check("192.168.1.50")                        # the address the proxy itself saw is limited


def test_client_address_is_the_rightmost_untrusted_hop(ui):
    from btbridge.webui.server import client_address
    trusted = lambda ip: ip in ("127.0.0.1", "10.0.0.2")                 # noqa: E731
    assert client_address("127.0.0.1", "6.6.6.6, 192.168.1.50", trusted) == "192.168.1.50"
    assert client_address("127.0.0.1", "6.6.6.6, 192.168.1.50, 10.0.0.2", trusted) == "192.168.1.50"
    assert client_address("127.0.0.1", "", trusted) == "127.0.0.1"
    assert client_address("127.0.0.1", "garbage", trusted) == "127.0.0.1"


def test_ipv6_clients_are_limited_per_64_prefix():
    from btbridge.webui.auth import RateLimiter, limit_key
    assert limit_key("2001:db8:1:2::1") == limit_key("2001:db8:1:2:ffff::9") != limit_key("2001:db8:1:3::1")
    assert limit_key("192.168.1.50") == "192.168.1.50"
    t = [0.0]
    lim = RateLimiter(clock=lambda: t[0])
    for i in range(6):
        lim.fail(limit_key(f"2001:db8:1:2::{i + 1}"))
    from btbridge.webui.auth import AuthError
    with pytest.raises(AuthError):
        lim.check(limit_key("2001:db8:1:2::abcd"))


def test_global_failure_budget_limits_a_distributed_guess(tmp_path):
    from btbridge.config import ConfigStore
    from btbridge.webui.auth import Auth, AuthError
    a = Auth(ConfigStore(str(tmp_path)), iterations=1000)
    a.complete_setup("127.0.0.1", open(a.code_file).read().strip(), "a good long passphrase")
    hits = 0
    for i in range(200):
        try:
            a.login(f"10.{i // 250}.{i % 250}.1", "wrong")
        except AuthError as e:
            hits += e.code == "rate_limited"
    assert hits > 0                                                      # many addresses, one budget



def test_change_password_with_a_wrong_old_password_backs_off(tmp_path):
    from btbridge.config import ConfigStore
    from btbridge.webui.auth import Auth, AuthError
    a = Auth(ConfigStore(str(tmp_path)), iterations=1000)
    a.complete_setup("127.0.0.1", open(a.code_file).read().strip(), "a good long passphrase")
    for _ in range(5):
        with pytest.raises(AuthError):
            a.change_password("wrong", "another long passphrase", client="192.168.1.50")
    with pytest.raises(AuthError, match="rate_limited"):
        a.change_password("a good long passphrase", "another long passphrase", client="192.168.1.50")


# ---------------------------------------------------------------- M9: bare host names (router search domain) and a helpful 421
def test_the_bare_device_name_is_accepted(ui):
    from test_webui import Client
    for host in ("hifi-bridge", f"hifi-bridge:{ui.port}", "HIFI-BRIDGE."):
        assert Client(ui.port).get("/healthz", headers={"Host": host}).status == 200, host   # was: 421


def test_a_bare_extra_host_name_can_be_configured(ui):
    from test_webui import Client
    from btbridge.config.schema import validate
    assert validate({"extra_hostnames": "hifibox, bridge.home.arpa"})["extra_hostnames"] == "hifibox, bridge.home.arpa"
    ui.svc.store.update({"extra_hostnames": "hifibox, bridge.home.arpa"})
    assert Client(ui.port).get("/healthz", headers={"Host": "hifibox"}).status == 200


def test_unknown_host_gets_a_bilingual_page_with_working_addresses(ui):
    from test_webui import Client
    r = Client(ui.port).get("/", headers={"Host": "evil.example"})
    assert r.status == 421
    assert 'lang="de"' in r.body and 'lang="en"' in r.body
    assert "evil.example" in r.body and "<script" not in r.body
    assert f'href="http://hifi-bridge.local:{ui.port}/"' in r.body and f'href="http://127.0.0.1:{ui.port}/"' in r.body
    r = Client(ui.port).get("/", headers={"Host": "<b>x</b>"})
    assert r.status == 421 and "<b>x</b>" not in r.body                  # the refused name is escaped


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
