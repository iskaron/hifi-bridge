"""UNIT: rtnetlink parsing with constructed messages (byte layout of linux/rtnetlink.h), plus one dump from the REAL kernel of the test
container (its own network namespace - never the host's)."""
import ipaddress
import socket
import struct

from btbridge.net import netlink as NL


def rta(typ, data):
    ln = 4 + len(data)
    return struct.pack("=HH", ln, typ) + data + b"\0" * ((-ln) % 4)


def msg(typ, payload):
    return struct.pack("=LHHLL", 16 + len(payload), typ, NL.NLM_F_MULTI, 1, 0) + payload + b"\0" * ((-len(payload)) % 4)


def link(index, name, flags=NL.IFF_UP, carrier=1, oper=6, mac=b"\x02\0\0\0\0\x01"):
    body = struct.pack("=BxHiII", 0, 1, index, flags, 0)
    return body + rta(NL.IFLA_IFNAME, name.encode() + b"\0") + rta(NL.IFLA_CARRIER, bytes([carrier])) + rta(NL.IFLA_OPERSTATE, bytes([oper])) \
        + rta(NL.IFLA_ADDRESS, mac)


def addr(index, ip, prefix):
    a = ipaddress.ip_address(ip)
    fam = socket.AF_INET if a.version == 4 else socket.AF_INET6
    return struct.pack("=BBBBI", fam, prefix, 0, 0, index) + rta(NL.IFA_ADDRESS, a.packed) + (rta(NL.IFA_LOCAL, a.packed) if a.version == 4 else b"")


def route(oif, gw=None, dst_len=0, table=NL.RT_TABLE_MAIN, family=socket.AF_INET, rtype=1):
    body = struct.pack("=BBBBBBBBI", family, dst_len, 0, 0, table if table < 256 else 252, 3, 0, rtype, 0)
    body += rta(NL.RTA_OIF, struct.pack("=i", oif))
    if table >= 256:
        body += rta(NL.RTA_TABLE, struct.pack("=I", table))
    if gw:
        body += rta(NL.RTA_GATEWAY, ipaddress.ip_address(gw).packed)
    return body


def test_parse_and_assemble():
    data = msg(NL.RTM_NEWLINK, link(1, "lo", flags=NL.IFF_UP | NL.IFF_LOOPBACK)) + msg(NL.RTM_NEWLINK, link(2, "end0")) \
        + msg(NL.RTM_NEWLINK, link(3, "wlan0", carrier=0, oper=2))
    links = [NL.parse_link(p) for t, p in NL.messages(data)]
    assert links[1] == {"index": 2, "name": "end0", "up": True, "loopback": False, "carrier": True, "operstate": "up", "mac": "02:00:00:00:00:01"}
    assert links[2]["carrier"] is False and links[2]["operstate"] == "down" and links[0]["loopback"] is True
    addrs = [NL.parse_addr(p) for p in (addr(2, "192.168.1.20", 24), addr(2, "fe80::1", 64), addr(2, "2001:db8::20", 64),
                                        addr(1, "127.0.0.1", 8), addr(9, "10.0.0.1", 8))]
    routes = [NL.parse_route(p) for p in (route(2, "192.168.1.1"), route(3, dst_len=24), route(2, "fe80::1", family=socket.AF_INET6),
                                          route(3, "10.1.1.1", table=1000))]
    assert routes[0]["gateway"] == "192.168.1.1" and routes[3]["table"] == 1000
    ifs = NL.assemble(links, addrs, routes)
    assert set(ifs) == {"end0", "wlan0"}
    end0 = ifs["end0"]
    assert end0["default_route"] is True and [a["address"] for a in end0["addresses"]] == ["192.168.1.20", "fe80::1", "2001:db8::20"]
    assert [a["usable"] for a in end0["addresses"]] == [True, False, True]
    assert ifs["wlan0"]["default_route"] is False and ifs["wlan0"]["addresses"] == []


def test_malformed_input_is_tolerated():
    assert list(NL.messages(b"\x05\0\0\0" + b"\0" * 12)) == []                  # length < header
    assert NL._attrs(struct.pack("=HH", 2, 1) + b"xxxx", 0) == {}
    p = struct.pack("=BxHiII", 0, 1, 7, 0, 0)                                       # no attributes at all
    assert NL.parse_link(p) == {"index": 7, "name": "", "up": False, "loopback": False, "carrier": False, "operstate": "unknown", "mac": None}
    assert NL.parse_addr(struct.pack("=BBBBI", socket.AF_INET, 24, 0, 0, 2))["address"] is None
    assert NL.usable({"address": None}) is False


class FakeSock:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def send(self, b):
        self.sent.append(b)

    def recv(self, n):
        return self.replies.pop(0)


def test_dump_reads_until_done_and_reports_errors():
    done = msg(NL.NLMSG_DONE, struct.pack("=i", 0))
    s = FakeSock([msg(NL.RTM_NEWLINK, link(2, "eth0")), msg(NL.RTM_NEWLINK, link(3, "wlan0")) + done])
    out = NL._dump(s, NL.RTM_GETLINK, b"", 1)
    assert [t for t, _ in out] == [NL.RTM_NEWLINK, NL.RTM_NEWLINK] and len(s.sent) == 1
    s = FakeSock([msg(NL.NLMSG_ERROR, struct.pack("=i", -1))])
    try:
        NL._dump(s, NL.RTM_GETLINK, b"", 1)
        raise AssertionError("no error")
    except OSError as e:
        assert e.errno == 1
    assert NL._dump(FakeSock([msg(NL.NLMSG_ERROR, struct.pack("=i", 0))]), NL.RTM_GETLINK, b"", 1) == []


def test_real_kernel_dump_of_the_container_namespace():
    ifs = NL.interfaces()                                   # the test container's own namespace (lo is omitted)
    for name, i in ifs.items():
        assert set(i) == {"index", "up", "carrier", "operstate", "mac", "addresses", "default_route"}
        for a in i["addresses"]:
            ipaddress.ip_address(a["address"])
    assert NL.safe_interfaces() == ifs or isinstance(NL.safe_interfaces(), dict)


def test_safe_interfaces_never_raises(monkeypatch):
    monkeypatch.setattr(NL, "interfaces", lambda: (_ for _ in ()).throw(OSError("no netlink")))
    assert NL.safe_interfaces() == {}
