"""UNIT (fake D-Bus connection): the Avahi backend's publishing logic - address + service records, collision -> next name, failure,
free on stop. The real interop test against Avahi is in scripts/test_mdns.sh (section 'avahi backend')."""
import pytest

from btbridge.net import avahi as A


class Reply:
    def __init__(self, body, err=False):
        from jeepney import MessageType
        self.body = body
        self.header = type("H", (), {"message_type": MessageType.error if err else MessageType.method_return})()


class FakeConn:
    def __init__(self, states, system_fqdn="buildroot.local"):
        self.calls, self.states, self.closed, self.system_fqdn = [], list(states), False, system_fqdn

    def send_and_get_reply(self, msg, timeout=None):
        from jeepney import HeaderFields
        member = msg.header.fields[HeaderFields.member]
        self.calls.append((member, msg.body))
        if member == "GetVersionString":
            return Reply(("avahi 0.8",))
        if member == "GetHostNameFqdn":
            return Reply((self.system_fqdn,))
        if member == "EntryGroupNew":
            return Reply(("/Client1/EntryGroup1",))
        if member == "GetState":
            return Reply((self.states[0],))
        if member == "Reset":
            self.states.pop(0)
        return Reply(())

    def close(self):
        self.closed = True


def pub(states, **kw):
    p = A.AvahiPublisher("hifi-bridge", "Hi-Fi Bridge (Wohnzimmer)", [("_https._tcp", 8443, {"path": "/"})],
                         addresses=lambda: ["192.168.1.20"], **kw)
    p.conn = FakeConn(states)
    return p


def test_publishes_address_and_service_records_for_the_name():
    names = []
    p = pub([A.ESTABLISHED], on_name=names.append)
    assert p.start() == "hifi-bridge.local" and names == ["hifi-bridge.local"]
    calls = dict((m, b) for m, b in p.conn.calls if m in ("AddAddress", "AddService"))
    assert calls["AddAddress"] == (-1, 0, A.PUBLISH_NO_REVERSE, "hifi-bridge.local", "192.168.1.20")
    svc = calls["AddService"]
    assert svc[3:8] == ("Hi-Fi Bridge (Wohnzimmer)", "_https._tcp", "", "hifi-bridge.local", 8443) and svc[8] == [b"path=/"]


def test_collision_moves_to_the_next_name():
    p = pub([A.COLLISION, A.ESTABLISHED])
    assert p.start() == "hifi-bridge-2.local"
    assert [b[3] for m, b in p.conn.calls if m == "AddAddress"] == ["hifi-bridge.local", "hifi-bridge-2.local"]


def test_failure_raises_and_stop_frees_the_group():
    with pytest.raises(A.AvahiUnavailable):
        pub([A.FAILURE]).start()
    p = pub([A.ESTABLISHED]); p.start(); conn = p.conn; p.stop()
    assert ("Free", ()) in conn.calls and conn.closed and p.state == "stopped"


def test_unreachable_avahi_is_reported_not_hidden():
    p = A.AvahiPublisher(bus="unix:path=/nonexistent/bus")
    with pytest.raises(A.AvahiUnavailable):
        p.connect()
    assert A.available(bus="unix:path=/nonexistent/bus") is False


# ---- backend selection in LocalAccess (DECISIONS #49) ----
class FakeStore:
    def __init__(self, **kw):
        self.v = {"local_hostname": "hifi-bridge", "device_name": "Wohnzimmer", "mdns_enabled": True, "mdns_backend": "auto",
                  "extra_hostnames": "", "tls_mode": "proxy", "trusted_proxies": "", "https_only": False, **kw}
    def get(self, k): return self.v[k]


class FakeMdns:
    def __init__(self, *a, on_name=None, log=None, ok=True): self.ok, self.on_name, self.started = ok, on_name, False
    def connect(self):
        if not self.ok:
            raise A.AvahiUnavailable("no daemon")
        return "avahi 0.8"
    def start(self): self.started = True; self.on_name("hifi-bridge.local"); return "hifi-bridge.local"
    def stop(self): self.started = False


def access(tmp_path, avahi_ok=True, **kw):
    from btbridge.net.access import LocalAccess
    made = {}
    def avahi_factory(*a, **k):
        made["avahi"] = FakeMdns(ok=avahi_ok, **k); return made["avahi"]
    def mdns_factory(*a, **k):
        made["builtin"] = FakeMdns(**k); return made["builtin"]
    acc = LocalAccess(FakeStore(**kw), str(tmp_path), "ab12cd34", 8080, 8443, log=lambda m: None, addresses=lambda: ["192.168.1.20"],
                      mdns_factory=mdns_factory, avahi_factory=avahi_factory)
    acc._start_mdns()
    return acc, made


def test_auto_prefers_avahi(tmp_path):
    acc, made = access(tmp_path)
    assert acc.mdns_backend == "avahi" and made["avahi"].started and "builtin" not in made


def test_auto_falls_back_to_builtin_only_without_avahi(tmp_path):
    acc, made = access(tmp_path, avahi_ok=False)
    assert acc.mdns_backend == "builtin" and made["builtin"].started


def test_required_avahi_is_reported_unavailable_not_replaced(tmp_path):
    acc, made = access(tmp_path, avahi_ok=False, mdns_backend="avahi")
    assert acc.mdns_backend == "unavailable" and acc.mdns is None and "builtin" not in made


def test_builtin_on_request_and_off(tmp_path):
    assert access(tmp_path, mdns_backend="builtin")[0].mdns_backend == "builtin"
    assert access(tmp_path, mdns_enabled=False)[0].mdns_backend == "off"


def test_system_host_name_equal_to_ours_publishes_only_services():
    p = pub([A.ESTABLISHED]); p.conn.system_fqdn = "hifi-bridge.local"
    assert p.start() == "hifi-bridge.local"
    members = [m for m, _ in p.conn.calls]
    assert "AddAddress" not in members and "AddService" in members


def test_access_reconfigure_restarts_mdns_and_reissues_certificates(tmp_path):
    acc, made = access(tmp_path, tls_mode="local_ca")
    first = made["avahi"]
    acc.store.v["local_hostname"] = "wohnzimmer"
    acc.reconfigure({"local_hostname"})
    assert not first.started and made["avahi"] is not first and made["avahi"].started
    acc.store.v["mdns_backend"] = "builtin"; acc.reconfigure({"mdns_backend"})
    assert acc.mdns_backend == "builtin"
    acc.store.v["mdns_enabled"] = False; acc.reconfigure({"mdns_enabled"})
    assert acc.mdns is None and acc.mdns_backend == "off"
    assert acc.ensure_certificates() in (None, "missing", "names_changed") and acc.ctx is not None
    info = acc.info()
    assert info["mode"] == "local_ca" and info["tls_available"] and info["ca"]["sha256"]
    assert acc.is_trusted_proxy("10.0.0.1") is False
    acc.store.v["trusted_proxies"] = "10.0.0.0/8"; assert acc.is_trusted_proxy("10.0.0.1") and not acc.is_trusted_proxy("bogus")


class ErrorOn(FakeConn):
    def __init__(self, member, text, states=(A.ESTABLISHED,), **kw):
        super().__init__(states, **kw)
        self.member, self.text = member, text

    def send_and_get_reply(self, msg, timeout=None):
        from jeepney import HeaderFields
        if msg.header.fields[HeaderFields.member] == self.member:
            self.calls.append((self.member, msg.body))
            return Reply((self.text,), err=True)
        return super().send_and_get_reply(msg, timeout)


def test_a_local_address_collision_moves_to_the_next_name_and_other_errors_surface():
    p = pub([A.ESTABLISHED])
    calls = {"n": 0}

    class OnceThenOk(ErrorOn):
        def send_and_get_reply(self, msg, timeout=None):
            from jeepney import HeaderFields
            if msg.header.fields[HeaderFields.member] == "AddAddress" and calls["n"] == 0:
                calls["n"] += 1
                return Reply(("Local name collision",), err=True)
            return FakeConn.send_and_get_reply(self, msg, timeout)
    p.conn = OnceThenOk("AddAddress", "x", states=[A.ESTABLISHED, A.ESTABLISHED])
    assert p.start() == "hifi-bridge-2.local"
    p2 = pub([A.ESTABLISHED]); p2.conn = ErrorOn("AddAddress", "Access denied")
    with pytest.raises(A.AvahiUnavailable, match="Access denied"):
        p2.start()


def test_a_group_that_never_settles_is_a_failure(monkeypatch):
    p = pub([A.REGISTERING] * 1000, timeout=0.3)
    with pytest.raises(A.AvahiUnavailable):
        p.start()


def test_stop_survives_a_failing_free_and_closes_the_connection():
    p = pub([A.ESTABLISHED]); p.start()
    conn = ErrorOn("Free", "gone")
    p.conn = conn
    p.stop()
    assert conn.closed and p.state == "stopped" and p.conn is None
    p.stop()                                                     # idempotent


def test_available_is_false_without_a_bus():
    assert A.available(bus="unix:path=/nonexistent/socket") is False
