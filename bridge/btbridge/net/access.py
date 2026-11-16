"""Local access manager: device names (mDNS + owner-configured), Host policy, HTTPS certificates and their renewal, trusted proxies.
Concept and threat model: docs/LOCAL_ACCESS_TLS.md (DECISIONS #35)."""
from __future__ import annotations

import ipaddress
import ssl
import threading
from typing import Callable

from .addresses import local_ipv4
from .certs import CertError, CertManager, permitted
from .hostcheck import HostPolicy
from .mdns import MdnsResponder

RENEW_CHECK_S = 3600          # certificate validity check
ADDRESS_CHECK_S = 60          # a new DHCP address triggers a re-issue (the certificate lists the device's IPs)


class LocalAccess:
    def __init__(self, store, data_dir: str, instance_id: str, http_port: int, https_port: int, log: Callable[[str], None] = print,
                 addresses: Callable[[], list[str]] = local_ipv4, cert_manager: CertManager | None = None,
                 mdns_factory=MdnsResponder, avahi_factory=None):
        self.store, self.http_port, self.https_port, self.log, self.addresses = store, http_port, https_port, log, addresses
        self.certs = cert_manager or CertManager(data_dir, instance_id, log=log)
        from .avahi import AvahiPublisher
        self.mdns_factory, self.avahi_factory, self.mdns = mdns_factory, avahi_factory or AvahiPublisher, None
        self.mdns_backend = "off"
        self.mdns_name: str | None = None
        self.policy = HostPolicy(self.host_names)
        self.ctx: ssl.SSLContext | None = None
        self.cert_error: str | None = None
        self._stop = threading.Event()
        self._last_addrs: set[str] = set()

    # ---- names ----
    def main_name(self) -> str:
        return self.mdns_name or f"{self.store.get('local_hostname')}.local"

    def extra_names(self) -> list[str]:
        return [x.strip() for x in self.store.get("extra_hostnames").split(",") if x.strip()]

    def host_names(self) -> list[str]:
        return [self.main_name(), f"{self.store.get('local_hostname')}.local", *self.extra_names()]

    def cert_names(self) -> list[str]:
        ips = [ip for ip in self.addresses() if permitted(ip)]
        return list(dict.fromkeys([self.main_name(), *self.extra_names(), *ips]))

    # ---- TLS ----
    @property
    def mode(self) -> str:
        return self.store.get("tls_mode")

    def tls_available(self) -> bool:
        return self.mode != "proxy" and self.ctx is not None

    def _load_ctx(self) -> None:
        crt, key = self.certs.server_paths(self.mode)
        self._loaded = (self.mode, crt)
        if self.ctx is None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(crt, key)
            self.ctx = ctx
        else:
            self.ctx.load_cert_chain(crt, key)           # new handshakes use the new certificate; open connections are unaffected

    def ensure_certificates(self) -> str | None:
        """Make sure a valid certificate for the current names is loaded. Returns the renewal reason (or None)."""
        if self.mode == "proxy":
            self.ctx, self.cert_error = None, None
            return None
        try:
            if self.mode == "local_ca":
                why = self.certs.ensure_server(self.cert_names())
            else:
                why = None
                if not self.certs.custom_info():
                    raise CertError("no custom certificate installed")
            if why or self.ctx is None or getattr(self, "_loaded", None) != (self.mode, self.certs.server_paths(self.mode)[0]):
                self._load_ctx()                         # also after a mode switch (local CA <-> own certificate)
            self.cert_error = None
            return why
        except (CertError, ssl.SSLError, OSError) as e:
            self.cert_error = str(e)
            self.log(f"tls: {e}")
            return None

    def install_custom(self, cert_pem: str, key_pem: str) -> dict:
        info = self.certs.install_custom(cert_pem, key_pem, self.main_name() if not self.extra_names() else self.extra_names()[0])
        if self.mode == "custom":
            self._load_ctx()
        return info

    # ---- proxies ----
    def is_trusted_proxy(self, ip: str) -> bool:
        nets = [x.strip() for x in self.store.get("trusted_proxies").split(",") if x.strip()]
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in ipaddress.ip_network(n, strict=False) for n in nets)

    # ---- lifecycle ----
    def _make_mdns(self):
        """DECISIONS #49: Avahi when reachable (auto) or required (avahi); the built-in responder only as fallback (auto) or on request."""
        services = [("_https._tcp", self.https_port, {"path": "/"}), ("_http._tcp", self.http_port, {"path": "/"})]
        args = (self.store.get("local_hostname"), f"Hi-Fi Bridge ({self.store.get('device_name')})", services)
        backend = self.store.get("mdns_backend")
        if backend in ("auto", "avahi"):
            from .avahi import AvahiPublisher, AvahiUnavailable
            pub = self.avahi_factory(*args, on_name=self._named, log=self.log)
            try:
                pub.connect()
                self.mdns_backend = "avahi"
                return pub
            except AvahiUnavailable as e:
                if backend == "avahi":
                    raise OSError(f"avahi required but not available: {e}") from e
                self.log("mdns: no Avahi daemon reachable - using the built-in responder")
        self.mdns_backend = "builtin"
        return self.mdns_factory(*args, on_name=self._named, log=self.log)

    def _start_mdns(self) -> None:
        if not self.store.get("mdns_enabled"):
            self.mdns_backend = "off"
            return
        try:
            self.mdns = self._make_mdns()
            self.mdns.start()
        except OSError as e:
            self.log(f"mdns not available: {e}")
            self.mdns, self.mdns_backend = None, "unavailable"

    def start(self) -> None:
        self._start_mdns()
        self._last_addrs = set(self.addresses())
        self.ensure_certificates()
        threading.Thread(target=self._loop, daemon=True, name="local-access").start()

    def reconfigure(self, keys: set[str]) -> None:
        """Apply changed access settings in place."""
        if {"local_hostname", "mdns_enabled", "mdns_backend", "device_name"} & keys:
            if self.mdns:
                self.mdns.stop()
                self.mdns, self.mdns_name = None, None
            self._start_mdns()
        if {"local_hostname", "mdns_enabled", "mdns_backend", "extra_hostnames", "tls_mode"} & keys:
            self.ensure_certificates()

    def _named(self, fqdn: str) -> None:
        changed = fqdn != self.mdns_name
        self.mdns_name = fqdn
        if changed and self.ctx is not None:
            self.ensure_certificates()                  # a conflict-renamed device needs its new name in the certificate

    def _loop(self) -> None:
        next_renew = RENEW_CHECK_S
        waited = 0
        while not self._stop.wait(ADDRESS_CHECK_S):
            waited += ADDRESS_CHECK_S
            addrs = set(self.addresses())
            if addrs != self._last_addrs or waited >= next_renew:
                self._last_addrs, waited = addrs, 0
                why = self.ensure_certificates()
                if why:
                    self.log(f"tls: certificate re-issued ({why})")

    def stop(self) -> None:
        self._stop.set()
        if self.mdns:
            self.mdns.stop()

    def info(self) -> dict:
        ca = self.certs.ca_info() if self.mode == "local_ca" or self.certs.ca_info() else None
        srv = None
        try:
            from .certs import cert_info
            crt, _ = self.certs.server_paths(self.mode)
            srv = cert_info(crt) if self.mode != "proxy" and __import__("os").path.exists(crt) else None
        except CertError:
            pass
        return {"mode": self.mode, "https_only": self.store.get("https_only"), "main_name": self.main_name(), "names": self.host_names(),
                "mdns": self.mdns_name if self.mdns else None, "mdns_backend": self.mdns_backend, "http_port": self.http_port, "https_port": self.https_port,
                "ca": ca, "server": srv, "error": self.cert_error, "tls_available": self.tls_available()}
