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
