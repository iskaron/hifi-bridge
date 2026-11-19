"""Read links, addresses and default routes from the kernel over rtnetlink (stdlib ``socket``; no ``ip`` binary, no root needed).

Used by the network agent (uplink detection) and by the WebUI status when no agent runs (Docker lab). Only dumps are requested; nothing is
changed. Parsing is separate from the socket so it is unit-tested with recorded/constructed messages; tests/net checks it against the real
kernel inside a container.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct

NETLINK_ROUTE = 0
NLMSG_ERROR, NLMSG_DONE = 2, 3
RTM_NEWLINK, RTM_GETLINK, RTM_NEWADDR, RTM_GETADDR, RTM_NEWROUTE, RTM_GETROUTE = 16, 18, 20, 22, 24, 26
NLM_F_REQUEST, NLM_F_MULTI, NLM_F_DUMP = 0x1, 0x2, 0x300
IFLA_ADDRESS, IFLA_IFNAME, IFLA_OPERSTATE, IFLA_CARRIER = 1, 3, 16, 33
IFA_ADDRESS, IFA_LOCAL = 1, 2
RTA_DST, RTA_OIF, RTA_GATEWAY, RTA_TABLE = 1, 4, 5, 15
RT_TABLE_MAIN = 254
IFF_UP, IFF_LOOPBACK = 0x1, 0x8
OPERSTATES = {0: "unknown", 1: "notpresent", 2: "down", 3: "lowerlayerdown", 4: "testing", 5: "dormant", 6: "up"}
_HDR = struct.Struct("=LHHLL")


def _attrs(buf: bytes, off: int) -> dict[int, bytes]:
    out = {}
    while off + 4 <= len(buf):
        ln, typ = struct.unpack_from("=HH", buf, off)
        if ln < 4:
            break
        out[typ & 0x3FFF] = buf[off + 4:off + ln]
        off += (ln + 3) & ~3
    return out


def messages(data: bytes):
    """Split a netlink datagram into (type, payload) pairs."""
    off = 0
    while off + 16 <= len(data):
        ln, typ, _flags, _seq, _pid = _HDR.unpack_from(data, off)
        if ln < 16:
            break
        yield typ, data[off + 16:off + ln]
        off += (ln + 3) & ~3


def parse_link(p: bytes) -> dict:
    _fam, _type, index, flags, _change = struct.unpack_from("=BxHiII", p, 0)
    a = _attrs(p, 16)
    name = a.get(IFLA_IFNAME, b"").split(b"\0")[0].decode("utf-8", "replace")
    carrier = a.get(IFLA_CARRIER)
    return {"index": index, "name": name, "up": bool(flags & IFF_UP), "loopback": bool(flags & IFF_LOOPBACK),
            "carrier": bool(carrier[0]) if carrier else False,
            "operstate": OPERSTATES.get(a[IFLA_OPERSTATE][0], "unknown") if IFLA_OPERSTATE in a else "unknown",
            "mac": ":".join(f"{b:02x}" for b in a[IFLA_ADDRESS]) if IFLA_ADDRESS in a else None}


def parse_addr(p: bytes) -> dict:
    family, prefix, _flags, scope, index = struct.unpack_from("=BBBBI", p, 0)
    a = _attrs(p, 8)
    raw = a.get(IFA_LOCAL) or a.get(IFA_ADDRESS) or b""
    addr = str(ipaddress.ip_address(raw)) if len(raw) in (4, 16) else None
    return {"index": index, "family": 4 if family == socket.AF_INET else 6, "address": addr, "prefix": prefix, "scope": scope}


def parse_route(p: bytes) -> dict:
    family, dst_len, _src, _tos, table, _proto, _scope, rtype, _fl = struct.unpack_from("=BBBBBBBBI", p, 0)
    a = _attrs(p, 12)
    if RTA_TABLE in a:
        table = struct.unpack("=I", a[RTA_TABLE][:4])[0]
    gw = a.get(RTA_GATEWAY)
    return {"family": 4 if family == socket.AF_INET else 6, "dst_len": dst_len, "table": table, "type": rtype,
            "oif": struct.unpack("=i", a[RTA_OIF][:4])[0] if RTA_OIF in a else None,
            "gateway": str(ipaddress.ip_address(gw)) if gw and len(gw) in (4, 16) else None}


def _dump(sock, msg_type: int, body: bytes, seq: int) -> list[tuple[int, bytes]]:
    sock.send(_HDR.pack(16 + len(body), msg_type, NLM_F_REQUEST | NLM_F_DUMP, seq, 0) + body)
    out = []
    while True:
        data = sock.recv(65536)
        for typ, payload in messages(data):
            if typ == NLMSG_DONE:
                return out
            if typ == NLMSG_ERROR:
                err = struct.unpack_from("=i", payload, 0)[0]
                if err:
                    raise OSError(-err, os.strerror(-err))
                return out
            out.append((typ, payload))


def usable(addr: dict) -> bool:
    """A global or site address (not loopback/link-local/tentative scope)."""
    if not addr.get("address"):
        return False
    ip = ipaddress.ip_address(addr["address"])
    return not (ip.is_loopback or ip.is_link_local or ip.is_multicast)


def assemble(links: list[dict], addrs: list[dict], routes: list[dict]) -> dict[str, dict]:
    by_index = {}
    for ln in links:
        if ln["loopback"]:
            continue
        by_index[ln["index"]] = {**ln, "addresses": [], "default_route": False}
    for a in addrs:
        if a["index"] in by_index and a["address"]:
            by_index[a["index"]]["addresses"].append({"family": a["family"], "address": a["address"], "prefix": a["prefix"],
                                                      "usable": usable(a)})
    for r in routes:
        if r["dst_len"] == 0 and r["table"] == RT_TABLE_MAIN and r["type"] == 1 and r["oif"] in by_index:
            by_index[r["oif"]]["default_route"] = True
    return {v["name"]: {k: v[k] for k in ("index", "up", "carrier", "operstate", "mac", "addresses", "default_route")}
            for v in by_index.values()}


def interfaces() -> dict[str, dict]:
    """{name: {up, carrier, operstate, mac, addresses: [{family, address, prefix, usable}], default_route}} (loopback omitted)."""
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE) as s:
        s.settimeout(2.0)
        s.bind((0, 0))
        links = [parse_link(p) for t, p in _dump(s, RTM_GETLINK, struct.pack("=BxHiII", socket.AF_UNSPEC, 0, 0, 0, 0), 1)
                 if t == RTM_NEWLINK]
        addrs = [parse_addr(p) for t, p in _dump(s, RTM_GETADDR, struct.pack("=BBBBI", socket.AF_UNSPEC, 0, 0, 0, 0), 2)
                 if t == RTM_NEWADDR]
        routes = [parse_route(p) for t, p in _dump(s, RTM_GETROUTE, struct.pack("=BBBBBBBBI", socket.AF_UNSPEC, 0, 0, 0, 0, 0, 0, 0, 0), 3)
                  if t == RTM_NEWROUTE]
    return assemble(links, addrs, routes)


def safe_interfaces() -> dict[str, dict]:
    try:
        return interfaces()
    except OSError:
        return {}
