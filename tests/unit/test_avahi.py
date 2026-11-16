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
    def __init__(self, states):
        self.calls, self.states, self.closed = [], list(states), False

    def send_and_get_reply(self, msg, timeout=None):
        from jeepney import HeaderFields
        member = msg.header.fields[HeaderFields.member]
        self.calls.append((member, msg.body))
        if member == "GetVersionString":
            return Reply(("avahi 0.8",))
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
    assert calls["AddAddress"] == (-1, 0, 0, "hifi-bridge.local", "192.168.1.20")
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
