"""LMS server discovery: UDP broadcast on port 3483 (the same mechanism Squeezelite uses).

Request : b"e" + (4-char tag + 0x00) per wanted field (IPAD, NAME, JSON, VERS, UUID).
Reply   : b"E" + TLVs (4-char tag, 1-byte length, value).  JSON = the server's HTTP/JSON-RPC port.
Verified against LMS 9.1.2 in the lab only (docs/FINDINGS.md); other versions untested.

The request leaves from ONE fixed local port (``REPLY_PORT``) and only answers from the server port 3483 are taken: the device firewall
accepts Lyrion's replies on exactly that port (``udp sport 3483 udp dport 3484``) instead of every port >= 1024 (review N-8). A broadcast
request has no conntrack entry that a unicast reply would match, so the reply needs this explicit rule. Discoveries run one after the
other (the port can be bound once).
"""
from __future__ import annotations

import socket
import threading
import time

TAGS = (b"NAME", b"JSON", b"VERS", b"UUID", b"IPAD")
SERVER_PORT = 3483           # Lyrion/LMS discovery (and SlimProto) port
REPLY_PORT = 3484            # our fixed local discovery port (firewall: netrender.nftables)
_lock = threading.Lock()


def local_broadcasts() -> list[str]:
    """Directed broadcast addresses of local IPv4 subnets (Linux /proc/net/fib_trie); works without a default route."""
    out = []
    try:
        prev = ""
        for line in open("/proc/net/fib_trie"):
            if "/32 link BROADCAST" in line and prev.strip().startswith("|--"):
                ip = prev.split()[-1]
                if not ip.startswith("127.") and ip not in out:
                    out.append(ip)
            prev = line
    except OSError:
        pass
    return out


def discover(timeout: float = 2.0, port: int = SERVER_PORT, local_port: int = REPLY_PORT) -> list[dict]:
    with _lock:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("0.0.0.0", local_port))  # nosec B104 - replies to the LAN broadcast arrive on any interface; firewall: only sport 3483 -> dport 3484
            s.settimeout(0.3)
            return _ask(s, timeout, port)
        finally:
            s.close()


def _ask(s: socket.socket, timeout: float, port: int) -> list[dict]:
    req = b"e" + b"".join(t + b"\x00" for t in TAGS)
    for b in local_broadcasts() + ["255.255.255.255"]:
        try:
            s.sendto(req, (b, port))
        except OSError:
            pass
    found, end = {}, time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            continue
        if addr[1] != port or not data.startswith(b"E"):
            continue
        info, i = {"address": addr[0]}, 1
        while i + 5 <= len(data):
            tag, ln = data[i:i + 4].decode("ascii", "replace"), data[i + 4]
            info[tag] = data[i + 5:i + 5 + ln].decode("utf-8", "replace")
            i += 5 + ln
        found[addr[0]] = info
    return list(found.values())
