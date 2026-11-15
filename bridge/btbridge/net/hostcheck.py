"""Host-header validation against DNS rebinding (DECISIONS #35).

A rebinding attack makes a browser send requests for an attacker's *domain name* to the device's IP. Requests are therefore only served when
the Host header names the device: its mDNS name(s), owner-configured extra host names (e.g. a reverse-proxy name), ``localhost``, or a literal
IP address (an IP literal cannot be rebound). Everything else is refused before any processing (HTTP 421)."""
from __future__ import annotations

import ipaddress


def split_host(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):                                     # [v6]:port
        return h[1:h.index("]")] if "]" in h else h
    if h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


class HostPolicy:
    def __init__(self, names_provider):
        """``names_provider()`` returns the current allowed host names (mDNS name, extra names); called per request (cheap)."""
        self.names_provider = names_provider

    def allowed(self, host_header: str | None) -> bool:
        if not host_header:
            return False                                      # HTTP/1.1 requires Host; browsers always send it
        h = split_host(host_header)
        if not h:
            return False
        if is_ip(h) or h in ("localhost", "ip6-localhost"):
            return True
        return h in {n.lower().rstrip(".") for n in self.names_provider() if n}
