"""UNIT + loopback INTEGRATION (real multicast sockets inside one container): mDNS wire format, answers, probing/conflicts, goodbye.
Interoperability with a real Avahi is tested separately (scripts/test_mdns.sh)."""
import socket
import struct
import threading
import time

import pytest

from btbridge.net import mdns as M

PORT = 15353          # a private port + the real group: unit tests never touch the host's 5353 responders


def resp(host="hifi-bridge", ips=("192.168.1.20",), **kw):
    return M.MdnsResponder(host, "Hi-Fi Bridge (Wohnzimmer)", [("_https._tcp", 8443, {"path": "/"}), ("_http._tcp", 8080, {"path": "/"})],
                           addresses=lambda: list(ips), port=PORT, **kw)


def test_name_encoding_roundtrip_and_compression_pointer():
    data = b"\x00" * 12 + M.enc_name("hifi-bridge.local")
    assert M.dec_name(data, 12) == ("hifi-bridge.local", 12 + 19)
    ptr = data + b"\xc0\x0c"                                   # pointer to offset 12
    assert M.dec_name(ptr, len(data))[0] == "hifi-bridge.local"
    with pytest.raises(ValueError):
        M.dec_name(b"\xc0\x00", 0)                             # pointer loop
    with pytest.raises(ValueError):
        M.enc_name("a" * 64 + ".local")


def test_answers_for_a_ptr_srv_txt_and_service_enumeration():
    r = resp()
    q = lambda name, t: M.parse(M.packet(*r.answer([(name, t, 1)])))["records"]
    assert q("hifi-bridge.local", M.A) == [("hifi-bridge.local", M.A, 120, "192.168.1.20")]
    recs = q("_https._tcp.local", M.PTR)
    assert ("_https._tcp.local", M.PTR, 4500, "Hi-Fi Bridge (Wohnzimmer)._https._tcp.local") in recs
    assert any(t == M.SRV and v == (8443, "hifi-bridge.local") for _, t, _, v in recs)
    assert any(t == M.A and v == "192.168.1.20" for _, t, _, v in recs)
    srv = q("hi-fi bridge (wohnzimmer)._https._tcp.local", M.SRV)
    assert [t for _, t, _, _ in srv if t != M.A] == [M.SRV]
    assert {v for _, t, _, v in q("_services._dns-sd._udp.local", M.PTR)} == {"_https._tcp.local", "_http._tcp.local"}
    assert q("other.local", M.A) == [] and q("hifi-bridge.local", M.TXT) == []
    legacy = M.parse(M.packet(*r.answer([("hifi-bridge.local", M.A, 1)], legacy=True)))["records"]
    assert legacy[0][2] == 10                                  # short TTL for legacy unicast answers


def test_malformed_packets_do_not_crash_the_responder():
    r = resp(); r.start(probe=False)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(r.ifaces[0]))
        for junk in (b"", b"\x00" * 5, b"\x00" * 12 + b"\x00\x01" * 3, b"\xff" * 40, struct.pack("!6H", 0, 0, 1, 0, 0, 0) + b"\xc0\x0c"):
            s.sendto(junk, (M.GROUP, PORT))
        time.sleep(0.3)
        assert r._thread.is_alive()
        assert M.query("hifi-bridge.local", port=PORT)
    finally:
        r.stop()


def test_live_query_over_multicast_and_goodbye():
    r = resp(); name = r.start()
    try:
        assert name == "hifi-bridge.local"
        recs = M.query("hifi-bridge.local", port=PORT)
        assert ("hifi-bridge.local", M.A, 10, "192.168.1.20") in recs
    finally:
        r.stop()
    assert M.query("hifi-bridge.local", port=PORT, timeout=0.6) == []


def test_conflict_during_probing_picks_the_next_name():
    other = resp(ips=("192.168.1.99",)); other.start()          # another device already owns hifi-bridge.local
    names = []
    me = resp(on_name=names.append)
    try:
        assert me.start() == "hifi-bridge-2.local" and names == ["hifi-bridge-2.local"]
        assert ("hifi-bridge-2.local", M.A, 10, "192.168.1.20") in M.query("hifi-bridge-2.local", port=PORT)
        assert all(v == "192.168.1.99" for _, t, _, v in M.query("hifi-bridge.local", port=PORT) if t == M.A)
    finally:
        me.stop(); other.stop()
