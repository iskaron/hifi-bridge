"""Minimal mDNS / DNS-SD responder (RFC 6762 / RFC 6763), stdlib only - gives the device a stable local name (default
``hifi-bridge.local``) and announces the WebUI as ``_https._tcp`` / ``_http._tcp`` services (DECISIONS #35).

What it does: probing with conflict resolution (``hifi-bridge`` -> ``hifi-bridge-2`` ...), announcement, answers for A, PTR, SRV, TXT and
ANY queries (multicast, and unicast for legacy one-shot resolvers), goodbye packets on stop, re-probing when another host claims the name.
What it does not do: IPv6/AAAA, known-answer suppression, per-interface answers (all non-loopback IPv4 addresses are announced).
Interoperability is tested against a real Avahi (tests/mdns). mDNS gives a NAME, not trust: see docs/LOCAL_ACCESS_TLS.md.
"""
from __future__ import annotations

import argparse
import socket
import struct
import threading
import time
from typing import Callable

from .addresses import local_ipv4

GROUP, PORT = "224.0.0.251", 5353
A, PTR, TXT, SRV, ANY = 1, 12, 16, 33, 255
IN, FLUSH, QU = 1, 0x8000, 0x8000
HOST_TTL, OTHER_TTL = 120, 4500


# ------------------------------------------------------------------ wire format
def enc_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        b = label.encode("utf-8")
        if not 0 < len(b) < 64:
            raise ValueError(f"bad label {label!r}")
        out += bytes([len(b)]) + b
    return out + b"\x00"


def dec_name(data: bytes, off: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        if off >= len(data):
            raise ValueError("truncated name")
        n = data[off]
        if n & 0xC0 == 0xC0:
            if jumps > 20:
                raise ValueError("pointer loop")
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | data[off + 1]
            jumps += 1
            continue
        off += 1
        if n == 0:
            break
        labels.append(data[off:off + n].decode("utf-8", "replace"))
        off += n
    return ".".join(labels), (end if end is not None else off)


def rr(name: str, rtype: int, ttl: int, rdata: bytes, flush: bool = False) -> bytes:
    return enc_name(name) + struct.pack("!HHIH", rtype, IN | (FLUSH if flush else 0), ttl, len(rdata)) + rdata


def txt_rdata(d: dict[str, str]) -> bytes:
    items = [f"{k}={v}".encode() for k, v in d.items()] or [b""]
    return b"".join(bytes([len(i)]) + i for i in items)


def parse(data: bytes) -> dict:
    tid, flags, qd, an, ns, ar = struct.unpack("!6H", data[:12])
    off, qs, rrs = 12, [], []
    for _ in range(qd):
        name, off = dec_name(data, off)
        qtype, qclass = struct.unpack("!HH", data[off:off + 4]); off += 4
        qs.append((name.lower(), qtype, qclass))
    for _ in range(an + ns + ar):
        name, off = dec_name(data, off)
        rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10]); off += 10
        rdata = data[off:off + rdlen]
        if rtype == A and rdlen == 4:
            val = socket.inet_ntoa(rdata)
        elif rtype in (PTR,):
            val = dec_name(data, off)[0]
        elif rtype == SRV:
            pri, w, port = struct.unpack("!HHH", rdata[:6]); val = (port, dec_name(data, off + 6)[0])
        else:
            val = rdata
        off += rdlen
        rrs.append((name.lower(), rtype, ttl, val))
    return {"id": tid, "response": bool(flags & 0x8000), "questions": qs, "records": rrs}


def packet(answers: list[bytes], additional: list[bytes] = (), questions: list[bytes] = (), tid: int = 0, response=True) -> bytes:
    flags = 0x8400 if response else 0
    return struct.pack("!6H", tid, flags, len(questions), len(answers), 0, len(additional)) + b"".join(questions) + \
        b"".join(answers) + b"".join(additional)


# ------------------------------------------------------------------ responder
class MdnsResponder:
    def __init__(self, hostname: str = "hifi-bridge", instance: str = "Hi-Fi Bridge", services: list[tuple[str, int, dict]] = (),
                 addresses: Callable[[], list[str]] = local_ipv4, on_name: Callable[[str], None] = lambda n: None,
                 log: Callable[[str], None] = lambda m: None, port: int = PORT, group: str = GROUP):
        self.base, self.instance, self.services = hostname.lower(), instance, list(services)
        self.addresses, self.on_name, self.log, self.port, self.group = addresses, on_name, log, port, group
        self.host = self.base
        self.sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent: set[bytes] = set()
        self._probing = False
        self._conflict = threading.Event()
        self.state = "stopped"

    @property
    def fqdn(self) -> str:
        return f"{self.host}.local"

    def _inst(self, stype: str) -> str:
        return f"{self.instance}.{stype}.local"

    # ---- socket ----
    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("", self.port))
        # Join per interface address: joining via 0.0.0.0 needs a multicast/default route, which isolated networks (and some
        # appliance set-ups) do not have (ENODEV).  Every interface also gets its own copy of our announcements (see _send).
        self.ifaces = local_ipv4() or ["127.0.0.1"]
        joined = []
        for ip in self.ifaces:
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(self.group) + socket.inet_aton(ip))
                joined.append(ip)
            except OSError as e:
                self.log(f"mdns: cannot join {self.group} on {ip}: {e}")
        if not joined:
            raise OSError("mdns: no interface could join the multicast group")
        self.ifaces = joined
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.settimeout(0.25)
        return s

    def _send(self, data: bytes, addr=None) -> None:
        self._sent.add(data)
        if len(self._sent) > 64:
            self._sent.pop()
        targets = [None] if addr else self.ifaces
        for ifip in targets:
            try:
                if ifip:
                    self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ifip))
                self.sock.sendto(data, addr or (self.group, self.port))
            except OSError as e:
                self.log(f"mdns send failed ({ifip or addr}): {e}")

    # ---- records ----
    def host_records(self, ttl: int = HOST_TTL) -> list[bytes]:
        return [rr(self.fqdn, A, ttl, socket.inet_aton(ip), flush=True) for ip in self.addresses()]

    def service_records(self, stype: str, port: int, txt: dict, ttl: int = OTHER_TTL) -> tuple[list[bytes], list[bytes]]:
        ptr, srv, txtr = self._svc(stype, port, txt, ttl)
        return [ptr], [srv, txtr]

    def _svc(self, stype: str, port: int, txt: dict, ttl: int) -> tuple[bytes, bytes, bytes]:
        inst = self._inst(stype)
        return (rr(f"{stype}.local", PTR, ttl, enc_name(inst)),
                rr(inst, SRV, min(ttl, HOST_TTL), struct.pack("!HHH", 0, 0, port) + enc_name(self.fqdn), flush=True),
                rr(inst, TXT, ttl, txt_rdata(txt), flush=True))

    def answer(self, questions: list[tuple[str, int, int]], legacy: bool = False) -> tuple[list[bytes], list[bytes]]:
        ttl_host, ttl_other = (10, 10) if legacy else (HOST_TTL, OTHER_TTL)
        ans, add = [], []
        for name, qtype, _ in questions:
            if name == self.fqdn and qtype in (A, ANY):
                ans += self.host_records(ttl_host)
            elif name == "_services._dns-sd._udp.local" and qtype in (PTR, ANY):
                ans += [rr(name, PTR, ttl_other, enc_name(f"{st}.local")) for st, _, _ in self.services]
            for st, port, txt in self.services:
                inst = self._inst(st).lower()
                if name == f"{st}.local" and qtype in (PTR, ANY):
                    a, d = self.service_records(st, port, txt, ttl_other)
                    ans += a; add += d + self.host_records(ttl_host)
                elif name == inst and qtype in (SRV, TXT, ANY):
                    _, srv, txtr = self._svc(st, port, txt, ttl_other)
                    ans += ([srv] if qtype in (SRV, ANY) else []) + ([txtr] if qtype in (TXT, ANY) else [])
                    add += self.host_records(ttl_host)
        return ans, add

    # ---- lifecycle ----
    def start(self, probe: bool = True) -> str:
        self.sock = self._open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="mdns")
        self._thread.start()
        if probe:
            self._claim()
        self.state = "announced"
        self.announce()
        self.on_name(self.fqdn)
        return self.fqdn

    def _claim(self) -> None:
        n = 1
        while True:
            self._conflict.clear()
            self._probing = True
            q = enc_name(self.fqdn) + struct.pack("!HH", ANY, IN | QU)
            for _ in range(3):
                self._send(packet(answers=[], additional=self.host_records(), questions=[q], response=False))
                if self._conflict.wait(0.25):
                    break
            self._probing = False
            if not self._conflict.is_set():
                return
            n += 1
            old, self.host = self.fqdn, f"{self.base}-{n}"
            self.log(f"mdns: {old} is used by another device; trying {self.fqdn}")

    def announce(self, ttl: int | None = None) -> None:
        for i in range(2):
            ans, add = self.host_records(HOST_TTL if ttl is None else ttl), []
            for st, port, txt in self.services:
                a, d = self.service_records(st, port, txt, OTHER_TTL if ttl is None else ttl)
                ans += a + d
            self._send(packet(ans, add))
            if i == 0 and not self._stop.wait(1.0 if ttl is None else 0.1):
                continue

    def stop(self) -> None:
        if self.sock is None:
            return
        try:
            self.announce(ttl=0)                       # goodbye: caches drop the records at once
        finally:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=2)
            self.sock.close()
            self.sock, self.state = None, "stopped"

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, src = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                return
            if data in self._sent:                    # our own multicast looped back
                continue
            try:
                msg = parse(data)
            except (ValueError, struct.error, IndexError):
                continue
            mine = set(self.addresses())
            if msg["response"] or msg["questions"] == [] or self._probing:
                claims = [r for r in msg["records"] if r[0] == self.fqdn and r[1] == A and r[3] not in mine]
                if claims:
                    if self._probing:
                        self._conflict.set()
                    elif self.state == "announced":
                        self.log(f"mdns: another device announced {self.fqdn}; re-probing")
                        threading.Thread(target=self._reclaim, daemon=True).start()
                if msg["response"]:
                    continue
            if self._probing:
                continue
            legacy = src[1] != self.port
            ans, add = self.answer(msg["questions"], legacy=legacy)
            if not ans:
                continue
            if legacy:                                 # RFC 6762 s6.7: unicast reply with the query id and the questions echoed
                qs = [enc_name(n) + struct.pack("!HH", t, IN) for n, t, _ in msg["questions"]]
                self._send(packet(ans, add, questions=qs, tid=msg["id"]), src)
            else:
                self._send(packet(ans, add))

    def _reclaim(self) -> None:
        self.state = "probing"
        self._claim()
        self.state = "announced"
        self.announce()
        self.on_name(self.fqdn)


def query(name: str, qtype: int = A, timeout: float = 1.5, port: int = PORT, group: str = GROUP) -> list[tuple]:
    """One-shot legacy-unicast query (source port != 5353): returns the answer records."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.2)
    ifs = local_ipv4()
    if ifs:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ifs[0]))
    tid = int(time.time() * 1000) & 0xFFFF
    s.sendto(packet([], questions=[enc_name(name) + struct.pack("!HH", qtype, IN)], tid=tid, response=False), (group, port))
    out, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            data, _ = s.recvfrom(9000)
        except socket.timeout:
            continue
        msg = parse(data)
        if msg["response"] and msg["id"] == tid:
            out += msg["records"]
    s.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.net.mdns", description="run only the mDNS responder (lab/testing)")
    ap.add_argument("--hostname", default="hifi-bridge"); ap.add_argument("--instance", default="Hi-Fi Bridge")
    ap.add_argument("--https-port", type=int, default=8443); ap.add_argument("--http-port", type=int, default=8080)
    a = ap.parse_args(argv)
    r = MdnsResponder(a.hostname, a.instance, [("_https._tcp", a.https_port, {"path": "/"}), ("_http._tcp", a.http_port, {"path": "/"})],
                      log=lambda m: print(m, flush=True), on_name=lambda n: print(f"mdns name: {n}", flush=True))
    import signal
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):              # service managers stop with SIGTERM: always send the goodbye
        signal.signal(sig, lambda *_: done.set())
    r.start()
    done.wait()
    r.stop()
    print("mdns: goodbye sent", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
