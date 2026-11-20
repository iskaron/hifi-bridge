"""UNIT (fake D-Bus connection, fake device node, real Unix sockets and files): the network agent's helpers when the system around them
misbehaves - an Avahi that answers with errors, a missing rfkill node, a wpa_supplicant control socket that goes away, files that
cannot be written. A failing helper must report a code or return quietly; it must never stop the agent that fixes the owner's network.
Coverage package 7b-3. (Real tools: scripts/run_net.sh.)"""
import os

import pytest

import btbridge.net.netsys as S
from btbridge.net.addresses import local_ipv4
from btbridge.net.hostcheck import HostPolicy

jeepney = pytest.importorskip("jeepney")


class Reply:
    def __init__(self, body, err=False):
        self.body = body
        self.header = type("H", (), {"message_type": jeepney.MessageType.error if err else jeepney.MessageType.method_return})()


class FakeAvahiConn:
    def __init__(self, current, set_error=False, get_error=False, raise_on=None):
        self.current, self.set_error, self.get_error, self.raise_on, self.sent, self.closed = current, set_error, get_error, raise_on, [], False

    def send_and_get_reply(self, msg, timeout=None):
        member = msg.header.fields[jeepney.HeaderFields.member]
        self.sent.append(member)
        if member == self.raise_on:
            raise TimeoutError("no reply")
        if member == "GetHostName":
            return Reply(("" if self.get_error else self.current,), err=self.get_error)
        return Reply((), err=self.set_error)

    def close(self):
        self.closed = True


@pytest.fixture
def avahi(monkeypatch):
    def install(conn):
        import jeepney.io.blocking as blocking
        monkeypatch.setattr(blocking, "open_dbus_connection", lambda bus="SYSTEM": conn)
        return conn
    return install


@pytest.mark.parametrize("conn_kw,expect,sent", [
    (dict(current="old-name"), "ok", ["GetHostName", "SetHostName"]),
    (dict(current="hifi-bridge"), "same", ["GetHostName"]),                    # already published under that name: nothing to change
    (dict(current="old-name", get_error=True), "unavailable", ["GetHostName"]),   # the daemon answers, but not as an Avahi server
    (dict(current="old-name", set_error=True), "error", ["GetHostName", "SetHostName"]),
    (dict(current="old-name", raise_on="SetHostName"), "error", ["GetHostName", "SetHostName"]),
])
def test_avahi_host_name_update_reports_a_code_for_every_answer_and_always_closes_the_connection(avahi, conn_kw, expect, sent):
    conn = avahi(FakeAvahiConn(**conn_kw))
    assert S.avahi_set_hostname("hifi-bridge") == expect
    assert conn.sent == sent and conn.closed


def test_rfkill_without_the_device_node_reports_false_and_a_writable_node_gets_the_unblock_event(tmp_path):
    assert S.rfkill_unblock_wifi(str(tmp_path / "no-rfkill")) is False
    node = tmp_path / "rfkill"; node.write_bytes(b"")
    assert S.rfkill_unblock_wifi(str(node)) is True
    assert node.read_bytes() == b"\x00\x00\x00\x00\x01\x03\x00\x00"                # idx 0, type WLAN, op CHANGE_ALL, soft 0, hard 0


def test_rfkill_that_refuses_the_write_reports_false(tmp_path):
    full = "/dev/full"                                                              # every write fails with ENOSPC
    if os.path.exists(full):
        assert S.rfkill_unblock_wifi(full) is False


# ---------------------------------------------------------------- address and host helpers
def test_local_addresses_are_empty_not_an_error_when_the_kernel_table_is_unreadable(tmp_path):
    assert local_ipv4(str(tmp_path / "missing")) == []


def test_a_request_without_a_usable_host_header_is_not_for_this_device():
    policy = HostPolicy(lambda: ["hifi-bridge.local"])
    assert policy.allowed("hifi-bridge.local:8080") is True
    assert policy.allowed("") is False and policy.allowed(None) is False
    assert policy.allowed(":8080") is False and policy.allowed("[") is False       # nothing usable left after splitting off the port
    assert policy.allowed("evil.example") is False
