"""LMS server discovery: UDP broadcast on port 3483 (the same mechanism Squeezelite uses).

Request : b"e" + (4-char tag + 0x00) per wanted field (IPAD, NAME, JSON, VERS, UUID).
Reply   : b"E" + TLVs (4-char tag, 1-byte length, value).  JSON = the server's HTTP/JSON-RPC port.
Verified against LMS 9.1.2 in the lab only (docs/FINDINGS.md); other versions untested.
"""
from __future__ import annotations

import socket
import time

TAGS = (b"NAME", b"JSON", b"VERS", b"UUID", b"IPAD")


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


def discover(timeout: float = 2.0, port: int = 3483) -> list[dict]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
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
        if not data.startswith(b"E"):
            continue
        info, i = {"address": addr[0]}, 1
        while i + 5 <= len(data):
            tag, ln = data[i:i + 4].decode("ascii", "replace"), data[i + 4]
            info[tag] = data[i + 5:i + 5 + ln].decode("utf-8", "replace")
            i += 5 + ln
        found[addr[0]] = info
    s.close()
    return list(found.values())
