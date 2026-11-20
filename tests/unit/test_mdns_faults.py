"""UNIT + loopback INTEGRATION (real multicast sockets in one container, fake D-Bus for Avahi): the mDNS responders on networks that are
not a tidy home LAN - an address on which multicast cannot be joined or sent, a second device claiming our name after we are up, a
service manager's SIGTERM, an Avahi that went away. Coverage package 7b-3 (and DECISIONS: a device on a LAN without a gateway must still
announce; the lab proof of that is scripts/test_mdns.sh, section 'without a gateway')."""
import faulthandler
import os
import signal
import threading
import time

import pytest
from test_avahi import FakeConn, pub
from test_mdns import PORT, resp

from btbridge.net import avahi as A
from btbridge.net import mdns as M


def wait_for(cond, t=5.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- built-in responder
def test_an_address_that_cannot_join_the_group_is_skipped_and_without_any_the_responder_refuses_to_start(monkeypatch):
    messages = []
    monkeypatch.setattr(M, "local_ipv4", lambda: ["203.0.113.9"])            # not an address of this host: the kernel refuses the join
    r = resp(log=messages.append)
    with pytest.raises(OSError, match="no interface could join"):
        r.start()
    assert any("cannot join 224.0.0.251 on 203.0.113.9" in m for m in messages)
    assert r.sock is None or r.state != "announced"


def test_one_bad_interface_does_not_keep_the_good_one_from_announcing(monkeypatch):
    messages = []
    monkeypatch.setattr(M, "local_ipv4", lambda: ["203.0.113.9", "127.0.0.1"])
    r = resp(log=messages.append)
    try:
        assert r.start() == "hifi-bridge.local" and r.ifaces == ["127.0.0.1"]
        assert any("cannot join 224.0.0.251 on 203.0.113.9" in m for m in messages)
    finally:
        r.stop()


def test_a_send_that_fails_on_one_interface_is_logged_and_the_others_still_get_the_packet(monkeypatch):
    messages = []
    r = resp(log=messages.append)
    r.start()
    try:
        r.ifaces = ["203.0.113.9", *r.ifaces]                               # an address that went away after the group was joined
        messages.clear()
        r.announce()
        assert any("mdns send failed (203.0.113.9)" in m for m in messages)
        for _ in range(80):                                                  # the 64-entry loop-back filter stays bounded
            r._send(b"x" * 20 + bytes([_]))
        assert len(r._sent) <= 65
    finally:
        r.stop()


def test_stop_is_harmless_before_start_and_twice():
    r = resp()
    r.stop()                                                                 # never started
    r.start(); r.stop(); r.stop()
    assert r.state == "stopped"


def test_a_device_that_claims_our_name_after_we_are_up_makes_us_probe_again_and_move():
    names = []
    me = resp(on_name=names.append)
    me.start()
    other = resp(ips=("192.168.1.99",))
    try:
        other.start(probe=False)                                             # announces hifi-bridge.local for ITS address, unasked
        assert wait_for(lambda: names[-1:] == ["hifi-bridge-2.local"], 15), names
        assert me.state == "announced" and me.fqdn == "hifi-bridge-2.local"
        assert any(v == "192.168.1.20" for _, t, _, v in M.query("hifi-bridge-2.local", port=PORT) if t == M.A)
    finally:
        other.stop(); me.stop()


def test_a_second_conflicting_packet_while_reprobing_starts_no_second_reprobe():
    me = resp(); me.start()
    started = []
    me._reclaim = lambda: (started.append(1), time.sleep(0.5))
    try:
        me._on_conflicting_announcement(); me._on_conflicting_announcement()
        assert wait_for(lambda: started == [1]) and me._reclaiming
    finally:
        wait_for(lambda: not me._reclaiming)
        me.stop()


def test_the_lab_responder_runs_until_sigterm_and_says_goodbye(capfd):
    """The CLI of the Docker mDNS lab (btbridge.net.mdns) - the way a service manager stops a daemon."""
    timer = threading.Timer(2.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    timer.start()
    try:
        assert M.main(["--hostname", "cov-lab", "--instance", "Coverage lab"]) == 0
    finally:
        timer.cancel()
        faulthandler.unregister(signal.SIGUSR1)                              # main() registered a thread-dump handler on stderr
        signal.signal(signal.SIGTERM, old[0]); signal.signal(signal.SIGINT, old[1])
    out = capfd.readouterr().out
    assert "mdns name: cov-lab.local" in out and out.rstrip().endswith("mdns: goodbye sent")


# ---------------------------------------------------------------- Avahi backend
def test_check_has_nothing_to_do_before_the_first_publish_and_survives_a_connection_that_cannot_be_closed():
    p = pub([A.ESTABLISHED])
    assert p.check() is False                                                # never published: nothing was lost

    p.start()
    published = []

    class BrokenClose(FakeConn):
        def close(self):
            raise OSError("socket already gone")
    p.conn = BrokenClose([A.FAILURE])                                        # the group is no longer established
    p.connect = lambda: published.append(1) or setattr(p, "conn", FakeConn([A.ESTABLISHED]))
    assert p.check() is True and published == [1] and p.state == "announced"   # published again over a fresh connection


def test_available_reports_a_reachable_daemon_and_closes_the_probe_connection(monkeypatch):
    conns = []

    def connect(self):
        self.conn = FakeConn([A.ESTABLISHED]); conns.append(self.conn)
        return "avahi 0.8"
    monkeypatch.setattr(A.AvahiPublisher, "connect", connect)
    assert A.available() is True and conns[0].closed


def test_the_avahi_lab_publisher_runs_until_sigterm_and_says_goodbye(monkeypatch, capsys):
    published = []

    class Pub(A.AvahiPublisher):
        def connect(self):
            self.conn = FakeConn([A.ESTABLISHED]); return "avahi 0.8"

        def start(self):
            published.append("start"); return super().start()

        def stop(self):
            published.append("stop"); super().stop()
    monkeypatch.setattr(A, "AvahiPublisher", Pub)
    timer = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    timer.start()
    try:
        assert A.main(["--hostname", "cov-avahi"]) == 0
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old[0]); signal.signal(signal.SIGINT, old[1])
    out = capsys.readouterr().out
    assert published == ["start", "stop"] and "avahi avahi 0.8" in out and out.rstrip().endswith("mdns: goodbye sent")
