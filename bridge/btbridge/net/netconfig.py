"""Network configuration of the appliance (``network.json``, format ``hifi-bridge-network/1``; concept: docs/NETWORK.md).

One file in the bridge's data directory (mode 0600) is the single source of truth. It is written by the provisioning step (P6), by the
owner WebUI (through a *pending* file that the root network agent tries first, see ``netmanager``) and by the SD-card network reset. The
root network agent only **reads** it, strictly (``read_strict``), and validates it with this module before anything is applied.

Shape after ``validate`` (every key present; unknown keys are rejected so typos never silently vanish)::

    {"format": "hifi-bridge-network/1",
     "country": "DE" | None,                                   # regulatory domain; required as soon as Wi-Fi or the hotspot exists
     "ethernet": {"ipv4": IPV4, "ipv6": IPV6},
     "wifi": None | {"ssid": str, "psk": str, "hidden": bool, "ipv4": IPV4, "ipv6": IPV6},
     "hotspot": None | {"ssid": str, "psk": str}}               # no hotspot without an owner-known password (DECISIONS #56)
    IPV4 = {"method": "dhcp"} | {"method": "static", "address": "a.b.c.d/nn", "gateway": str | "", "dns": [str, ...]}
    IPV6 = {"method": "auto" | "off"} | {"method": "static", "address": "x::y/nn", "gateway": str | "", "dns": [str, ...]}

The older provisioning output (``country``, ``hotspot``, ``wifi`` with ``ssid``/``psk`` only) is valid input; missing parts get defaults.
Problems are reported as ``field:reason`` codes and never contain values, because values can be secrets.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import stat

FORMAT = "hifi-bridge-network/1"
FILE_NAME = "network.json"
PENDING_NAME = "network.pending.json"
MAX_BYTES = 64 * 1024
HOTSPOT_PSK_MIN = 12                     # same rule as provisioning.HOTSPOT_PSK_MIN (test pins both)
HOTSPOT_ADDRESS = "10.42.0.1"            # fixed, documented setup address while the hotspot is active
HOTSPOT_NETWORK = ipaddress.ip_network("10.42.0.0/24")
MAX_DNS = 3
TOP = {"format", "country", "ethernet", "wifi", "hotspot"}
IP_KEYS = {"ipv4", "ipv6"}
WIFI_KEYS = {"ssid", "psk", "hidden"} | IP_KEYS
HOTSPOT_KEYS = {"ssid", "psk"}
STATIC_KEYS = {"method", "address", "gateway", "dns"}
IPV4_METHODS = ("dhcp", "static")
IPV6_METHODS = ("auto", "static", "off")


class NetConfigError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__(", ".join(problems))
        self.problems = problems


def default_ip() -> dict:
    return {"ipv4": {"method": "dhcp"}, "ipv6": {"method": "auto"}}


def defaults() -> dict:
    return {"format": FORMAT, "country": None, "ethernet": default_ip(), "wifi": None, "hotspot": None}


def printable_ascii(s: str) -> bool:
    return all(32 <= ord(c) <= 126 for c in s)


def ssid_ok(s) -> bool:
    return isinstance(s, str) and 1 <= len(s.encode("utf-8")) <= 32 and "\x00" not in s


def wifi_psk_ok(p) -> bool:
    return isinstance(p, str) and ((8 <= len(p) <= 63 and printable_ascii(p)) or re.fullmatch(r"[0-9a-fA-F]{64}", p) is not None)


def hotspot_psk_ok(p) -> bool:
    return isinstance(p, str) and HOTSPOT_PSK_MIN <= len(p) <= 63 and printable_ascii(p)


def country_ok(c) -> bool:
    return isinstance(c, str) and re.fullmatch(r"[A-Z]{2}", c) is not None and c != "00"


def _dns(v, version: int, field: str, p: list[str]) -> list[str]:
    if v in (None, ""):
        return []
    items = re.split(r"[,\s]+", v.strip()) if isinstance(v, str) else v
    if not isinstance(items, list) or len(items) > MAX_DNS:
        p.append(f"{field}:invalid")
        return []
    out = []
    for x in items:
        try:
            a = ipaddress.ip_address(str(x).strip())
        except ValueError:
            p.append(f"{field}:invalid")
            return []
        if a.version != version or a.is_multicast or a.is_unspecified:
            p.append(f"{field}:invalid")
            return []
        out.append(str(a))
    return list(dict.fromkeys(out))


def _ipv4(v, field: str, p: list[str]) -> dict:
    if v is None:
        return {"method": "dhcp"}
    if not isinstance(v, dict):
        p.append(f"{field}:not_an_object")
        return {"method": "dhcp"}
    p += [f"{field}.{k}:unknown_field" for k in sorted(set(v) - STATIC_KEYS)]
    method = v.get("method", "dhcp")
    if method not in IPV4_METHODS:
        p.append(f"{field}.method:invalid")
        return {"method": "dhcp"}
    if method == "dhcp":
        return {"method": "dhcp"}
    try:
        iface = ipaddress.IPv4Interface(str(v.get("address", "")).strip())
        if "/" not in str(v.get("address")):
            raise ValueError("prefix required")
    except ValueError:
        p.append(f"{field}.address:invalid")
        return {"method": "dhcp"}
    a, net = iface.ip, iface.network
    if (a.is_loopback or a.is_multicast or a.is_link_local or a.is_unspecified or a.is_reserved or not 8 <= net.prefixlen <= 30
            or a in (net.network_address, net.broadcast_address)):
        p.append(f"{field}.address:invalid")
    elif net.overlaps(HOTSPOT_NETWORK):
        p.append(f"{field}.address:conflicts_with_hotspot_network")
    gw = str(v.get("gateway") or "").strip()
    if gw:
        try:
            g = ipaddress.IPv4Address(gw)
            if g not in net or g == a or g in (net.network_address, net.broadcast_address):
                raise ValueError
        except ValueError:
            p.append(f"{field}.gateway:invalid")
            gw = ""
    return {"method": "static", "address": iface.with_prefixlen, "gateway": gw, "dns": _dns(v.get("dns"), 4, f"{field}.dns", p)}


def _ipv6(v, field: str, p: list[str]) -> dict:
    if v is None:
        return {"method": "auto"}
    if not isinstance(v, dict):
        p.append(f"{field}:not_an_object")
        return {"method": "auto"}
    p += [f"{field}.{k}:unknown_field" for k in sorted(set(v) - STATIC_KEYS)]
    method = v.get("method", "auto")
    if method not in IPV6_METHODS:
        p.append(f"{field}.method:invalid")
        return {"method": "auto"}
    if method != "static":
        return {"method": method}
    try:
        iface = ipaddress.IPv6Interface(str(v.get("address", "")).strip())
        if "/" not in str(v.get("address")):
            raise ValueError("prefix required")
    except ValueError:
        p.append(f"{field}.address:invalid")
        return {"method": "auto"}
    a = iface.ip
    if a.is_loopback or a.is_multicast or a.is_link_local or a.is_unspecified or not 16 <= iface.network.prefixlen <= 128:
        p.append(f"{field}.address:invalid")
    gw = str(v.get("gateway") or "").strip()
    if gw:
        try:
            g = ipaddress.IPv6Address(gw)
            if g.is_multicast or g.is_unspecified or g.is_loopback or g == a:
                raise ValueError
            gw = str(g)
        except ValueError:
            p.append(f"{field}.gateway:invalid")
            gw = ""
    return {"method": "static", "address": iface.with_prefixlen, "gateway": gw, "dns": _dns(v.get("dns"), 6, f"{field}.dns", p)}


def _ip_block(v, field: str, p: list[str]) -> dict:
    if v is None:
        return default_ip()
    if not isinstance(v, dict):
        p.append(f"{field}:not_an_object")
        return default_ip()
    p += [f"{field}.{k}:unknown_field" for k in sorted(set(v) - IP_KEYS)]
    return {"ipv4": _ipv4(v.get("ipv4"), f"{field}.ipv4", p), "ipv6": _ipv6(v.get("ipv6"), f"{field}.ipv6", p)}


def check(doc) -> tuple[dict, list[str]]:
    """Normalised configuration and the list of problems (empty = valid). Never raises for bad input."""
    p: list[str] = []
    out = defaults()
    if not isinstance(doc, dict):
        return out, ["document:not_an_object"]
    p += [f"{k}:unknown_field" for k in sorted(set(doc) - TOP)]
    if doc.get("format", FORMAT) != FORMAT:
        p.append("format:unsupported")
    out["ethernet"] = _ip_block(doc.get("ethernet"), "ethernet", p)
    w = doc.get("wifi")
    if w is not None:
        if not isinstance(w, dict):
            p.append("wifi:not_an_object")
        else:
            p += [f"wifi.{k}:unknown_field" for k in sorted(set(w) - WIFI_KEYS)]
            if not ssid_ok(w.get("ssid")):
                p.append("wifi.ssid:invalid")
            if not wifi_psk_ok(w.get("psk")):
                p.append("wifi.psk:invalid")
            if not isinstance(w.get("hidden", False), bool):
                p.append("wifi.hidden:invalid")
            ip = _ip_block({k: w[k] for k in IP_KEYS if k in w}, "wifi", p)
            out["wifi"] = {"ssid": w.get("ssid"), "psk": w.get("psk"), "hidden": w.get("hidden", False) is True, **ip}
    h = doc.get("hotspot")
    if h is not None:
        if not isinstance(h, dict):
            p.append("hotspot:not_an_object")
        else:
            p += [f"hotspot.{k}:unknown_field" for k in sorted(set(h) - HOTSPOT_KEYS)]
            if not ssid_ok(h.get("ssid")):
                p.append("hotspot.ssid:invalid")
            if not hotspot_psk_ok(h.get("psk")):
                p.append(f"hotspot.psk:required_{HOTSPOT_PSK_MIN}_to_63_printable_ascii")
            out["hotspot"] = {"ssid": h.get("ssid"), "psk": h.get("psk")}
    c = doc.get("country")
    if c is not None and not country_ok(c):
        p.append("country:required_iso_3166_alpha2")
    elif c is None and (out["wifi"] or out["hotspot"]):
        p.append("country:required_iso_3166_alpha2")
    out["country"] = c if country_ok(c) else None
    if out["wifi"] and out["hotspot"] and out["wifi"]["ssid"] == out["hotspot"]["ssid"]:
        p.append("hotspot.ssid:same_as_wifi")
    return out, p


def validate(doc) -> dict:
    cfg, problems = check(doc)
    if problems:
        raise NetConfigError(problems)
    return cfg


def dumps(cfg: dict) -> bytes:
    return json.dumps(validate(cfg), indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ files
def write_private(path: str, cfg: dict) -> bytes:
    """Validate, then write atomically with mode 0600 (fsync file + directory). Returns the bytes written."""
    raw = dumps(cfg)
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return raw


def read_strict(path: str, owner_uid: int | None = None) -> bytes | None:
    """Read a file written by the unprivileged bridge, as the ROOT agent: no symlinks, regular file, expected owner, size limit.
    None when the file does not exist. Raises NetConfigError for anything suspicious (the caller keeps the previous configuration)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise NetConfigError(["file:unreadable"]) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise NetConfigError(["file:not_a_regular_file"])
        if owner_uid is not None and st.st_uid not in (owner_uid, 0):
            raise NetConfigError(["file:wrong_owner"])
        if st.st_mode & 0o077:
            raise NetConfigError(["file:permissions_too_open"])
        if st.st_size > MAX_BYTES:
            raise NetConfigError(["file:too_large"])
        chunks, n = [], 0
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            n += len(b)
            if n > MAX_BYTES:
                raise NetConfigError(["file:too_large"])
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse(raw: bytes | None) -> dict:
    """Bytes of network.json -> validated configuration (defaults for a missing file)."""
    if raw is None:
        return defaults()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise NetConfigError(["document:not_json"]) from None
    return validate(doc)


def load(path: str) -> dict:
    """Bridge side (own file). Missing -> defaults; invalid -> NetConfigError."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return defaults()
    if len(raw) > MAX_BYTES:
        raise NetConfigError(["file:too_large"])
    return parse(raw)


# ------------------------------------------------------------------ views on a configuration
def redact(cfg: dict) -> dict:
    """For status, diagnostics, exports and MQTT: passwords are replaced by a flag (never their value, length or a hint)."""
    out = copy.deepcopy(cfg)
    for k in ("wifi", "hotspot"):
        if out.get(k):
            out[k].pop("psk", None)
            out[k]["password_set"] = True
    return out


def secrets_of(cfg: dict) -> list[str]:
    return [cfg[k]["psk"] for k in ("wifi", "hotspot") if cfg.get(k) and cfg[k].get("psk")]


def changed_sections(old: dict, new: dict) -> set[str]:
    """Which parts differ: country, ethernet, wifi (credentials or its IP settings), hotspot."""
    return {k for k in ("country", "ethernet", "wifi", "hotspot") if old.get(k) != new.get(k)}


def reset_network(cfg: dict) -> dict:
    """SD-card network reset (recovery): forget the client Wi-Fi and every static address; keep the regulatory country and the
    owner-known hotspot credentials, so the setup hotspot can come up."""
    out = defaults()
    out["country"], out["hotspot"] = cfg.get("country"), copy.deepcopy(cfg.get("hotspot"))
    return out
