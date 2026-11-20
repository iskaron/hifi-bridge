"""UNIT (real UDP sockets on loopback, fake Lyrion responder): the discovery socket uses ONE fixed local port, so the device firewall can
accept Lyrion's replies (source port 3483) on that port only instead of on every port >= 1024 (review N-8). The real LMS answer is
covered by tests/integration/test_07 (lab) and the firewall path by scripts/run_net.sh (fw_lms.py + fw_discover.py)."""
import socket
import threading

import pytest

from btbridge.lms import discovery as D
from btbridge.net import netrender as R


class FakeLyrion:
    """Answers discovery requests like LMS (from its own port, to the sender's address and port) and records the senders."""

    def __init__(self, name="Living room"):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("127.0.0.1", 0))
        self.port = self.s.getsockname()[1]
        self.senders, self.name = [], name.encode()
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _serve(self):
        while True:
            try:
                data, addr = self.s.recvfrom(2048)
            except OSError:
                return
            if data.startswith(b"e"):
                self.senders.append(addr)
                self.s.sendto(b"E" + b"NAME" + bytes([len(self.name)]) + self.name + b"JSON\x049000", addr)

    def close(self):
        self.s.close()


@pytest.fixture
def lyrion(monkeypatch):
    srv = FakeLyrion()
    monkeypatch.setattr(D, "local_broadcasts", lambda: ["127.0.0.1"])
    yield srv
    srv.close()


def test_requests_leave_from_the_fixed_reply_port_and_the_answer_arrives_there(lyrion):
    found = D.discover(timeout=0.6, port=lyrion.port, local_port=D.REPLY_PORT)
    assert lyrion.senders and {p for _, p in lyrion.senders} == {D.REPLY_PORT}
    assert found == [{"address": "127.0.0.1", "NAME": "Living room", "JSON": "9000"}]


def test_the_default_local_port_is_the_reply_port(lyrion, monkeypatch):
    calls = []
    real = socket.socket.bind

    def bind(self, addr):
        calls.append(addr)
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "bind", bind)
    D.discover(timeout=0.3, port=lyrion.port)
    assert ("0.0.0.0", D.REPLY_PORT) in calls


def test_two_discoveries_at_once_share_the_port_one_after_the_other(lyrion):
    """Two wizard pages asking at the same time: the second waits for the first instead of failing to bind the fixed port."""
    out, errors = [], []

    def run():
        try:
            out.append(D.discover(timeout=0.4, port=lyrion.port))
        except OSError as e:                                       # pragma: no cover - the failure this test guards against
            errors.append(e)
    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert not errors and len(out) == 2 and all(r and r[0]["NAME"] == "Living room" for r in out)


def test_an_answer_from_another_source_port_is_ignored(monkeypatch):
    """The firewall lets only source port 3483 through; the socket applies the same rule (also where no firewall runs)."""
    srv = FakeLyrion()
    monkeypatch.setattr(D, "local_broadcasts", lambda: ["127.0.0.1"])
    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.bind(("127.0.0.2", 0))                                   # another "host" on the loopback network

    def spoof():                                                   # answers every request from a port that is not the server port
        while srv.senders == []:
            threading.Event().wait(0.01)
        other.sendto(b"E" + b"NAME\x05Fake!", ("127.0.0.1", D.REPLY_PORT))
    t = threading.Thread(target=spoof, daemon=True)
    t.start()
    try:
        found = D.discover(timeout=0.6, port=srv.port)
    finally:
        srv.close()
        other.close()
    assert [f["NAME"] for f in found] == ["Living room"]


def test_the_firewall_accepts_discovery_replies_only_on_the_reply_port():
    for rs in (R.nftables(80, 443, 8090), R.nftables(80, 443, 8090, hotspot_iface="wlan0")):
        rules = [ln.strip() for ln in rs.splitlines() if "3483" in ln]
        assert rules == [f"udp sport {R.LYRION_DISCOVERY_PORT} udp dport {D.REPLY_PORT} accept"]
        assert "1024-65535" not in rs
    assert R.LYRION_DISCOVERY_PORT == D.SERVER_PORT == 3483 and D.REPLY_PORT not in (3483, 80, 443, 8090, 5353, 5355)
