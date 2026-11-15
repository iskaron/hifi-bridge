"""Local HTTPS certificate management (DECISIONS #35; concept: docs/LOCAL_ACCESS_TLS.md).

Model
  * one **local CA per device** (ECDSA P-256, 10 years), **name-constrained** to .local/.home.arpa/.lan/.internal and private IP ranges, so
    even a leaked CA key cannot mint certificates for public names; the owner installs it once on their devices after verifying its SHA-256
    fingerprint (no silent trust, no TLS exceptions);
  * a **server certificate** (ECDSA P-256, 397 days - within browser limits) for the mDNS name, extra names and current IP addresses;
    renewed automatically 30 days before expiry or when the name/address set changes;
  * **custom mode**: the owner's own certificate + key (validated: key matches, not expired, covers the main name);
  * proxy mode is handled by the WebUI (TLS terminated elsewhere).
Implemented with the ``openssl`` CLI (the Python stdlib cannot create X.509 certificates). Private keys are files with mode 0600.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import secrets
import ssl
import subprocess
import tempfile
import threading

SERVER_DAYS, CA_DAYS, RENEW_BEFORE_DAYS = 397, 3650, 30
PERMITTED_DNS = ("local", "home.arpa", "lan", "internal")
PERMITTED_IP = ("10.0.0.0/255.0.0.0", "172.16.0.0/255.240.0.0", "192.168.0.0/255.255.0.0", "169.254.0.0/255.255.0.0",
                "127.0.0.0/255.0.0.0", "fc00:0:0:0:0:0:0:0/fe00:0:0:0:0:0:0:0", "fe80:0:0:0:0:0:0:0/ffc0:0:0:0:0:0:0:0",
                "0:0:0:0:0:0:0:1/ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff")


class CertError(Exception):
    pass


def _run(args: list[str], **kw) -> str:
    try:
        r = subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=30, **kw)
    except FileNotFoundError as e:
        raise CertError("openssl not available") from e
    if r.returncode != 0:
        raise CertError(f"openssl {args[0]} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def _write_private(path: str, data: str) -> None:
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.replace(path + ".tmp", path)


def permitted(name: str) -> bool:
    """Would the CA's name constraints allow this SAN?"""
    try:
        ip = ipaddress.ip_address(name)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        n = name.lower().rstrip(".")
        return any(n == d or n.endswith("." + d) for d in PERMITTED_DNS)


def cert_info(pem_path: str) -> dict:
    out = _run(["x509", "-in", pem_path, "-noout", "-subject", "-issuer", "-enddate", "-startdate", "-fingerprint", "-sha256",
                "-ext", "subjectAltName"])
    end = re.search(r"notAfter=(.+)", out).group(1).strip()
    start = re.search(r"notBefore=(.+)", out).group(1).strip()
    parse = lambda s: dt.datetime.strptime(s.replace("  ", " "), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
    sans = []
    m = re.search(r"Subject Alternative Name:\s*\n\s*(.+)", out)
    if m:
        for part in m.group(1).split(","):
            k, _, v = part.strip().partition(":")
            if k in ("DNS", "IP Address"):
                sans.append(v.strip().lower())
    fp = re.search(r"Fingerprint=([0-9A-F:]+)", out).group(1)
    return {"subject": re.search(r"subject=(.*)", out).group(1).strip(), "issuer": re.search(r"issuer=(.*)", out).group(1).strip(),
            "not_before": parse(start).isoformat(), "not_after": parse(end).isoformat(), "sha256": fp, "sans": sans}


class CertManager:
    def __init__(self, data_dir: str, instance_id: str, log=lambda m: None, clock=lambda: dt.datetime.now(dt.timezone.utc)):
        self.dir = os.path.join(data_dir, "certs")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.iid, self.log, self.clock = instance_id, log, clock
        self.p = {k: os.path.join(self.dir, f) for k, f in (("ca_key", "ca.key"), ("ca_crt", "ca.crt"), ("srv_key", "server.key"),
                                                             ("srv_crt", "server.crt"), ("cus_key", "custom.key"), ("cus_crt", "custom.crt"),
                                                             ("state", "state.json"))}
        self._lock = threading.Lock()

    # ---- local CA ----
    def ensure_ca(self) -> dict:
        with self._lock:
            if not (os.path.exists(self.p["ca_key"]) and os.path.exists(self.p["ca_crt"])):
                self._create_ca()
            return cert_info(self.p["ca_crt"])

    def _create_ca(self) -> None:
        nc = ",".join([f"permitted;DNS:{d}" for d in PERMITTED_DNS] + [f"permitted;IP:{i}" for i in PERMITTED_IP])
        cnf = f"""[req]
distinguished_name=dn
prompt=no
[dn]
CN=Hi-Fi Bridge local CA {self.iid}
O=Hi-Fi Bridge (this device only)
[v3_ca]
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
nameConstraints=critical,{nc}
"""
        with tempfile.TemporaryDirectory() as td:
            cfg, key = os.path.join(td, "ca.cnf"), os.path.join(td, "ca.key")
            open(cfg, "w").write(cnf)
            _run(["req", "-x509", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", key,
                  "-out", self.p["ca_crt"], "-days", str(CA_DAYS), "-config", cfg, "-extensions", "v3_ca",
                  "-set_serial", "0x" + secrets.token_hex(16)])
            _write_private(self.p["ca_key"], open(key).read())
        self.log("local CA created (owner must install and trust it once; see the Secure access page)")

    # ---- server certificate ----
    def server_paths(self, mode: str = "local_ca") -> tuple[str, str]:
        return (self.p["cus_crt"], self.p["cus_key"]) if mode == "custom" else (self.p["srv_crt"], self.p["srv_key"])

    def needs_renewal(self, names: list[str]) -> str | None:
        """Reason to (re)issue the server certificate, or None."""
        if not os.path.exists(self.p["srv_crt"]) or not os.path.exists(self.p["srv_key"]):
            return "missing"
        try:
            info = cert_info(self.p["srv_crt"])
        except CertError:
            return "unreadable"
        left = dt.datetime.fromisoformat(info["not_after"]) - self.clock()
        if left < dt.timedelta(days=RENEW_BEFORE_DAYS):
            return "expiring"
        want = {n.lower() for n in names if permitted(n)}
        if want != set(info["sans"]):
            return "names_changed"
        ca = self.ca_info()
        if ca and info["issuer"] != ca["subject"]:
            return "ca_changed"
        return None

    def ca_info(self) -> dict | None:
        return cert_info(self.p["ca_crt"]) if os.path.exists(self.p["ca_crt"]) else None

    def issue_server(self, names: list[str]) -> dict:
        """Issue a server certificate for ``names`` (first = main name). Names outside the CA's constraints are skipped (logged)."""
        self.ensure_ca()
        ok = [n for n in dict.fromkeys(n.lower() for n in names if n) if permitted(n)]
        skipped = [n for n in names if n and n.lower() not in ok]
        if skipped:
            self.log(f"certificate: names outside the local CA's scope skipped: {', '.join(skipped)} (use a custom certificate for them)")
        if not ok:
            raise CertError("no name usable for the local certificate")
        san = ",".join(("IP:" if _isip(n) else "DNS:") + n for n in ok)
        ext = f"""[v3_srv]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName={san}
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
"""
        with self._lock, tempfile.TemporaryDirectory() as td:
            cfg, key, csr, crt = (os.path.join(td, x) for x in ("ext.cnf", "s.key", "s.csr", "s.crt"))
            open(cfg, "w").write(ext)
            cn = ok[0] if not _isip(ok[0]) else "hifi-bridge"
            _run(["req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", key, "-out", csr,
                  "-subj", f"/CN={cn}"])
            _run(["x509", "-req", "-in", csr, "-CA", self.p["ca_crt"], "-CAkey", self.p["ca_key"], "-days", str(SERVER_DAYS),
                  "-extfile", cfg, "-extensions", "v3_srv", "-set_serial", "0x" + secrets.token_hex(16), "-out", crt])
            _write_private(self.p["srv_key"], open(key).read())
            chain = open(crt).read() + open(self.p["ca_crt"]).read()      # leaf + CA (clients that already trust the CA ignore it)
            tmp = self.p["srv_crt"] + ".tmp"
            open(tmp, "w").write(chain)
            os.replace(tmp, self.p["srv_crt"])
        info = cert_info(self.p["srv_crt"])
        self._state({"issued": self.clock().isoformat(), "names": ok})
        self.log(f"server certificate issued for {', '.join(ok)} (valid until {info['not_after'][:10]})")
        return info

    def ensure_server(self, names: list[str]) -> str | None:
        why = self.needs_renewal(names)
        if why:
            self.issue_server(names)
        return why

    # ---- custom certificate ----
    def install_custom(self, cert_pem: str, key_pem: str, main_name: str) -> dict:
        """Validate and store the owner's certificate (+chain) and key. Raises CertError with a specific reason."""
        if "PRIVATE KEY" not in key_pem or "BEGIN CERTIFICATE" not in cert_pem:
            raise CertError("pem_format")
        with tempfile.TemporaryDirectory() as td:
            c, k = os.path.join(td, "c.pem"), os.path.join(td, "k.pem")
            open(c, "w").write(cert_pem.strip() + "\n")
            _write_private(k, key_pem.strip() + "\n")
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(c, k)
            except ssl.SSLError as e:
                raise CertError("key_mismatch") from e
            info = cert_info(c)
            if dt.datetime.fromisoformat(info["not_after"]) <= self.clock():
                raise CertError("expired")
            if main_name.lower() not in info["sans"] and not any(s.startswith("*.") and main_name.lower().endswith(s[1:]) for s in info["sans"]):
                raise CertError("name_not_covered")
            with self._lock:
                _write_private(self.p["cus_key"], open(k).read())
                open(self.p["cus_crt"], "w").write(open(c).read())
        self.log(f"custom certificate installed ({info['subject']}, valid until {info['not_after'][:10]})")
        return info

    def custom_info(self) -> dict | None:
        return cert_info(self.p["cus_crt"]) if os.path.exists(self.p["cus_crt"]) else None

    def remove_custom(self) -> None:
        for k in ("cus_crt", "cus_key"):
            try:
                os.unlink(self.p[k])
            except FileNotFoundError:
                pass

    def ca_pem(self) -> str:
        return open(self.p["ca_crt"]).read()

    def ca_der(self) -> bytes:
        return subprocess.run(["openssl", "x509", "-in", self.p["ca_crt"], "-outform", "DER"], capture_output=True, check=True).stdout

    def _state(self, d: dict) -> None:
        open(self.p["state"], "w").write(json.dumps(d))


def _isip(n: str) -> bool:
    try:
        ipaddress.ip_address(n)
        return True
    except ValueError:
        return False
