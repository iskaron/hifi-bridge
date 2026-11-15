"""Local IPv4 addresses from /proc/net/fib_trie (no ioctl, no external tools)."""
from __future__ import annotations

import ipaddress


def local_ipv4(path: str = "/proc/net/fib_trie") -> list[str]:
    out, prev = [], ""
    try:
        for line in open(path):
            if "/32 host LOCAL" in line and prev.strip().startswith("|--"):
                ip = prev.split()[-1]
                if not ip.startswith("127.") and ip not in out:
                    out.append(ip)
            prev = line
    except OSError:
        pass
    return [ip for ip in out if ipaddress.ip_address(ip).version == 4]
