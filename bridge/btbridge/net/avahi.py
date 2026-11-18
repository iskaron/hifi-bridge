"""mDNS via the Avahi daemon (preferred production backend, DECISIONS #49), controlled over D-Bus (jeepney).

The bridge does not change the system host name: Avahi's `SetHostName` is root-only by default and the service runs unprivileged. Instead it
publishes its OWN entry group: an address record `<name>.local -> <each private IPv4>` plus the `_https._tcp` / `_http._tcp` services that
point to that name. Avahi probes the name; on a collision the group reports state COLLISION and the bridge tries `<name>-2`, `<name>-3` ...
(same behaviour as the built-in responder). `stop()` frees the group, so Avahi withdraws the records with goodbye packets.

Same interface as `mdns.MdnsResponder` (start() -> fqdn, stop(), fqdn, on_name). Interoperability tested against a real Avahi peer
(scripts/test_mdns.sh, section "avahi backend")."""
from __future__ import annotations

import argparse
import threading
import time
from typing import Callable

from .addresses import local_ipv4

SERVICE, IF_SERVER, IF_GROUP = "org.freedesktop.Avahi", "org.freedesktop.Avahi.Server", "org.freedesktop.Avahi.EntryGroup"
IF_UNSPEC, PROTO_INET = -1, 0
PUBLISH_NO_REVERSE = 16          # the host's own reverse (PTR) record for the IP already exists -> a second one collides locally
UNCOMMITED, REGISTERING, ESTABLISHED, COLLISION, FAILURE = range(5)


class AvahiUnavailable(Exception):
    pass


def txt_bytes(d: dict) -> list[bytes]:
    return [f"{k}={v}".encode() for k, v in d.items()]


class AvahiPublisher:
    def __init__(self, hostname: str = "hifi-bridge", instance: str = "Hi-Fi Bridge", services: list[tuple[str, int, dict]] = (),
                 addresses: Callable[[], list[str]] = local_ipv4, on_name: Callable[[str], None] = lambda n: None,
                 log: Callable[[str], None] = lambda m: None, bus: str = "SYSTEM", timeout: float = 10.0):
        self.base, self.instance, self.services = hostname.lower(), instance, list(services)
        self.addresses, self.on_name, self.log, self.bus, self.timeout = addresses, on_name, log, bus, timeout
        self.host = self.base
        self.conn = None
        self.group: str | None = None
        self.state = "stopped"
        self.version: str | None = None
        self._lock = threading.Lock()

    @property
    def fqdn(self) -> str:
        return f"{self.host}.local"

    # ---- D-Bus helpers ----
    def _call(self, path: str, iface: str, method: str, sig: str = "", args: tuple = ()):
        from jeepney import DBusAddress, MessageType, new_method_call
        reply = self.conn.send_and_get_reply(new_method_call(DBusAddress(path, bus_name=SERVICE, interface=iface), method, sig, args),
                                             timeout=self.timeout)
        if reply.header.message_type == MessageType.error:
            raise AvahiUnavailable(f"{method}: {reply.body}")
        return reply.body

    def connect(self) -> str:
        try:
            from jeepney.io.blocking import open_dbus_connection
            self.conn = open_dbus_connection(bus=self.bus)
            self.version = self._call("/", IF_SERVER, "GetVersionString")[0]
        except Exception as e:                                     # no bus, no avahi-daemon, no jeepney
            self.conn = None
            raise AvahiUnavailable(f"avahi not reachable: {type(e).__name__}: {e}") from e
        return self.version

    # ---- publishing ----
    def _publish(self) -> int:
        g = self.group
        system_fqdn = self._call("/", IF_SERVER, "GetHostNameFqdn")[0]
        if system_fqdn.lower() != self.fqdn:            # the system already publishes its own host name - only add ours if different
            for ip in self.addresses():
                try:
                    self._call(g, IF_GROUP, "AddAddress", "iiuss", (IF_UNSPEC, PROTO_INET, PUBLISH_NO_REVERSE, self.fqdn, ip))
                except AvahiUnavailable as e:
                    if "collision" in str(e).lower():
                        return COLLISION              # another local service owns the name -> next name
                    raise
        for stype, port, txt in self.services:
            self._call(g, IF_GROUP, "AddService", "iiussssqaay",
                       (IF_UNSPEC, PROTO_INET, 0, self.instance, stype, "", self.fqdn, port, txt_bytes(txt)))
        self._call(g, IF_GROUP, "Commit")
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.timeout:
            st = self._call(g, IF_GROUP, "GetState")[0]
            if st in (ESTABLISHED, COLLISION, FAILURE):
                return st
            time.sleep(0.1)
        return FAILURE

    def start(self) -> str:
        with self._lock:
            if self.conn is None:
                self.connect()
            self.group = self._call("/", IF_SERVER, "EntryGroupNew")[0]
            n = 1
            while True:
                st = self._publish()
                if st == ESTABLISHED:
                    break
                self._call(self.group, IF_GROUP, "Reset")
                if st == FAILURE:
                    raise AvahiUnavailable(f"avahi could not publish {self.fqdn}")
                n += 1
                old, self.host = self.fqdn, f"{self.base}-{n}"
                self.log(f"mdns (avahi): {old} is used by another device; trying {self.fqdn}")
            self.state = "announced"
        self.on_name(self.fqdn)
        return self.fqdn

    def check(self) -> bool:
        """Publish again when the entry group is gone - avahi-daemon restarted (its objects vanish with it) or the group failed
        (review L7: the name used to stay unpublished until the bridge restarted). True if it published again; raises
        AvahiUnavailable while the daemon is still down (the caller retries on its next check)."""
        with self._lock:
            if self.state not in ("announced", "lost"):
                return False
            if self.state == "announced":
                try:
                    if self._call(self.group, IF_GROUP, "GetState")[0] == ESTABLISHED:
                        return False
                except Exception:                              # error reply, broken connection, ...
                    pass
                self.log("mdns (avahi): our entry group is gone (avahi-daemon restarted?) - publishing again")
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:
                pass
            self.conn, self.group, self.state, self.host = None, None, "lost", self.base
        self.start()                                            # raises AvahiUnavailable while the daemon is down
        return True

    def stop(self) -> None:
        with self._lock:
            if self.conn is None:
                return
            try:
                if self.group:
                    self._call(self.group, IF_GROUP, "Free")       # Avahi withdraws the records (goodbye packets)
            except AvahiUnavailable:
                pass
            finally:
                self.conn.close()
                self.conn, self.group, self.state = None, None, "stopped"


def available(bus: str = "SYSTEM") -> bool:
    p = AvahiPublisher(bus=bus, timeout=3)
    try:
        p.connect()
        return True
    except AvahiUnavailable:
        return False
    finally:
        if p.conn is not None:
            p.conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge.net.avahi", description="publish the bridge name/services via avahi (lab/testing)")
    ap.add_argument("--hostname", default="hifi-bridge"); ap.add_argument("--instance", default="Hi-Fi Bridge")
    ap.add_argument("--https-port", type=int, default=8443); ap.add_argument("--http-port", type=int, default=8080)
    a = ap.parse_args(argv)
    pub = AvahiPublisher(a.hostname, a.instance, [("_https._tcp", a.https_port, {"path": "/"}), ("_http._tcp", a.http_port, {"path": "/"})],
                         log=lambda m: print(m, flush=True), on_name=lambda n: print(f"mdns name: {n}", flush=True))
    from ..supervision import StopSignal
    stop = StopSignal()                                      # async-signal-safe: no lock in the handler
    print(f"avahi {pub.connect()}", flush=True)
    pub.start()
    stop.wait()
    pub.stop()
    print("mdns: goodbye sent", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
