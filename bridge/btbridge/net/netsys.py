"""Linux adapter of the root network agent: turns state-machine decisions into files, systemd units and kernel state, and observes.

Tools (all in the image's package set, docs/NETWORK.md): ``systemctl`` (units ``hifi-net-wpa@``/``hifi-net-hostapd@``/``hifi-net-dhcp@``,
which conflict with each other at the systemd level), ``networkctl`` (systemd-networkd reads our files from ``/run/systemd/network``),
``nft``, ``iw`` (scan, regulatory domain), ``hostnamectl``. wpa_supplicant and hostapd are observed through their control sockets
(``CtrlClient``, UNIX datagram protocol, stdlib). Every external call goes through an injected ``runner`` so the adapter is unit-tested
with recorded tool output; tests/net exercises the real tools in a container.
"""
from __future__ import annotations

import itertools
import os
import re
import socket
import struct
import subprocess
from typing import Callable

from . import netconfig, netlink, netrender
from .netmanager import Observation

WPA_UNIT, HOSTAPD_UNIT, DHCP_UNIT = "hifi-net-wpa@{}.service", "hifi-net-hostapd@{}.service", "hifi-net-dhcp@{}.service"
NETWORKD_DIR = "/run/systemd/network"
OUR_NETWORKD = ("10-hifi-ethernet.network", "11-hifi-wifi.network")
TIMESYNC_FLAG = "/run/systemd/timesync/synchronized"
_counter = itertools.count(1)


class CtrlError(OSError):
    pass


class CtrlClient:
    """wpa_supplicant/hostapd control interface: requests and unsolicited events ('<N>EVENT') on one UNIX datagram socket."""

    def __init__(self, path: str, local_dir: str, timeout: float = 2.0):
        self.path, self.timeout = path, timeout
        self.local = os.path.join(local_dir, f"ctrl-{os.getpid()}-{next(_counter)}")
        self.sock: socket.socket | None = None
        self.queued: list[str] = []
        self.attached = False

    def _open(self) -> socket.socket:
        if self.sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                try:
                    os.unlink(self.local)
                except FileNotFoundError:
                    pass
                s.bind(self.local)
                s.connect(self.path)
            except OSError as e:
                s.close()
                self._unlink()
                raise CtrlError(f"control socket {os.path.basename(self.path)}: {e.strerror or e}") from None
            s.settimeout(self.timeout)
            self.sock = s
        return self.sock

    def _unlink(self) -> None:
        try:
            os.unlink(self.local)
        except OSError:
            pass

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.attached = False
        self._unlink()

    def request(self, cmd: str) -> str:
        s = self._open()
        try:
            s.send(cmd.encode())
            while True:
                data = s.recv(65536).decode("utf-8", "replace")
                if re.match(r"<\d>", data):
                    self.queued.append(data)
                    continue
                return data
        except OSError as e:
            self.close()
            raise CtrlError(f"control request failed: {e}") from None

    def attach(self) -> bool:
        if not self.attached:
            self.attached = self.request("ATTACH").strip() == "OK"
        return self.attached

    def events(self) -> list[str]:
        out, self.queued = self.queued, []
        s = self.sock
        if s is None:
            return out
        s.setblocking(False)
        try:
            while True:
                data = s.recv(65536).decode("utf-8", "replace")
                if re.match(r"<\d>", data):
                    out.append(data)
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self.close()
        finally:
            if self.sock is not None:
                self.sock.settimeout(self.timeout)
        return out


def kv(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _unescape(s: str) -> str:
    """iw prints non-printable SSID bytes as \\xNN."""
    raw = re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), s.encode("utf-8", "surrogateescape"))
    return raw.decode("utf-8", "replace")


def parse_iw_scan(text: str) -> list[dict]:
    """``iw dev <if> scan`` -> [{ssid, signal_dbm, security, frequency}] (strongest first, one entry per SSID, hidden SSIDs skipped).
    security: wpa3 | wpa2/wpa3 | wpa2 | wpa | enterprise | wep | open (only the WPA personal variants can be joined)."""
    nets: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("BSS "):
            cur = {"ssid": None, "signal_dbm": None, "frequency": None, "rsn": "", "wpa": False, "privacy": False}
            nets.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("SSID:"):
            cur["ssid"] = _unescape(s[5:].strip()) if s[5:].strip() else None
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", s)
            cur["signal_dbm"] = round(float(m.group(1))) if m else None
        elif s.startswith("freq:"):
            m = re.search(r"(\d+)", s)
            cur["frequency"] = int(m.group(1)) if m else None
        elif s.startswith("capability:") and "Privacy" in s:
            cur["privacy"] = True
        elif s.startswith("RSN:"):
            cur["rsn"] = "RSN"
        elif s.startswith("WPA:"):
            cur["wpa"] = True
        elif "Authentication suites:" in s and cur["rsn"] == "RSN":
            cur["rsn"] = "RSN " + s.split(":", 1)[1].strip()
    best: dict[str, dict] = {}
    for n in nets:
        if not n["ssid"]:
            continue
        suites = n["rsn"].split()[1:] if n["rsn"] else []
        if "IEEE" in suites or "802.1X" in suites:
            sec = "enterprise"
        elif "SAE" in suites and "PSK" in suites:
            sec = "wpa2/wpa3"
        elif "SAE" in suites:
            sec = "wpa3"
        elif "PSK" in suites or n["rsn"]:
            sec = "wpa2"
        elif n["wpa"]:
            sec = "wpa"
        elif n["privacy"]:
            sec = "wep"
        else:
            sec = "open"
        entry = {"ssid": n["ssid"], "signal_dbm": n["signal_dbm"], "security": sec, "frequency": n["frequency"]}
        old = best.get(n["ssid"])
        if old is None or (entry["signal_dbm"] or -999) > (old["signal_dbm"] or -999):
            best[n["ssid"]] = entry
    return sorted(best.values(), key=lambda e: -(e["signal_dbm"] if e["signal_dbm"] is not None else -999))


def discover(sysfs: str = "/sys/class/net") -> tuple[list[str], str | None]:
    """(physical Ethernet interfaces, first Wi-Fi interface). Works with any naming scheme (eth0, end0, enx..., wlan0)."""
    eth, wlan = [], None
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return [], None
    for n in names:
        base = os.path.join(sysfs, n)
        if n == "lo":
            continue
        if os.path.exists(os.path.join(base, "phy80211")) or os.path.isdir(os.path.join(base, "wireless")):
            wlan = wlan or n
            continue
        try:
            typ = open(os.path.join(base, "type")).read().strip()
        except OSError:
            continue
        if typ == "1" and os.path.exists(os.path.join(base, "device")):        # ARPHRD_ETHER with a physical device behind it
            eth.append(n)
    return eth, wlan


def rfkill_unblock_wifi(dev: str = "/dev/rfkill") -> bool:
    """Clear a soft block on all Wi-Fi radios (struct rfkill_event: idx u32, type u8 WLAN=1, op u8 CHANGE_ALL=3, soft u8, hard u8)."""
    try:
        fd = os.open(dev, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, struct.pack("=IBBBB", 0, 1, 3, 0, 0))
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def avahi_set_hostname(name: str, bus: str = "SYSTEM") -> str:
    """Tell the running avahi-daemon the new host name (root only). Returns 'ok', 'same' or an error code; never raises."""
    try:
        from jeepney import DBusAddress, MessageType, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        conn = open_dbus_connection(bus=bus)
    except Exception:                                           # no jeepney, no bus
        return "unavailable"
    try:
        srv = DBusAddress("/", bus_name="org.freedesktop.Avahi", interface="org.freedesktop.Avahi.Server")
        r = conn.send_and_get_reply(new_method_call(srv, "GetHostName"), timeout=5)
        if r.header.message_type == MessageType.error:
            return "unavailable"
        if r.body[0] == name:
            return "same"
        r = conn.send_and_get_reply(new_method_call(srv, "SetHostName", "s", (name,)), timeout=5)
        return "error" if r.header.message_type == MessageType.error else "ok"
    except Exception:
        return "error"
    finally:
        conn.close()


class LinuxSystem:
    def __init__(self, run_dir: str, http_port: int = 80, https_port: int = 443, stream_port: int = 8090,
                 sysfs: str = "/sys/class/net", networkd_dir: str = NETWORKD_DIR, runner: Callable | None = None,
                 interfaces: Callable[[], dict] = netlink.safe_interfaces, timesync_flag: str = TIMESYNC_FLAG,
                 log: Callable[[str], None] = print, avahi: Callable[[str], str] = avahi_set_hostname,
                 rfkill: Callable[[], bool] = rfkill_unblock_wifi):
        self.run_dir, self.ports = run_dir, (http_port, https_port, stream_port)
        self.sysfs, self.networkd_dir, self.log = sysfs, networkd_dir, log
        self.runner = runner or self._run
        self.interfaces, self.timesync_flag, self.avahi, self.rfkill = interfaces, timesync_flag, avahi, rfkill
        self.eth, self.wlan = discover(sysfs)
        self.role = "off"
        self.ip_cfg = netconfig.defaults()
        self.firewall_ok: bool | None = None
        self.errors: list[str] = []
        self.wpa: CtrlClient | None = None
        self.ap: CtrlClient | None = None
        self.current_hostname: str | None = None
        for d in ("wpa", "hostapd", "ctl"):
            os.makedirs(os.path.join(run_dir, d), mode=0o700, exist_ok=True)

    # ---- plumbing
    @staticmethod
    def _run(argv: list[str], timeout: float = 30.0) -> tuple[int, str]:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return r.returncode, (r.stdout + r.stderr)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 127, f"{type(e).__name__}: {e}"

    def run(self, *argv: str, timeout: float = 30.0) -> bool:
        rc, out = self.runner(list(argv), timeout=timeout)
        if rc != 0:
            msg = f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: exit {rc}: {out.strip()[-200:]}"
            self.log(f"network agent: {msg}")
            self.errors = (self.errors + [msg])[-10:]
        return rc == 0

    def _write(self, path: str, text: str) -> None:
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def refresh(self) -> None:
        self.eth, self.wlan = discover(self.sysfs)

    # ---- configuration
    def _networkd(self) -> None:
        files = netrender.networkd_files(self.ip_cfg, self.eth, self.wlan, self.role)
        os.makedirs(self.networkd_dir, exist_ok=True)
        for name in OUR_NETWORKD:
            p = os.path.join(self.networkd_dir, name)
            if name in files:
                self._write(p, files[name])
                os.chmod(p, 0o644)                              # networkd runs as its own user
            elif os.path.exists(p):
                os.unlink(p)
        self.run("networkctl", "reload")
        for ifname in [*self.eth, *([self.wlan] if self.wlan else [])]:
            self.run("networkctl", "reconfigure", ifname)

    def firewall(self) -> bool:
        path = os.path.join(self.run_dir, "nftables.nft")
        self._write(path, netrender.nftables(*self.ports, hotspot_iface=self.wlan if self.role == "hotspot" else None))
        self.firewall_ok = self.run("nft", "-f", path)
        return self.firewall_ok

    def _stop_radio_units(self) -> None:
        if self.wlan:
            self.run("systemctl", "stop", WPA_UNIT.format(self.wlan), HOSTAPD_UNIT.format(self.wlan), DHCP_UNIT.format(self.wlan))
        for c in (self.wpa, self.ap):
            if c:
                c.close()
        self.wpa = self.ap = None

    def _regdomain(self, country: str) -> None:
        self.rfkill()
        self.run("iw", "reg", "set", country)

    def ethernet(self, ip: dict) -> None:
        self.ip_cfg = {**self.ip_cfg, "ethernet": ip}
        self._networkd()

    def client(self, wifi: dict, country: str) -> None:
        if not self.wlan:
            return
        self._stop_radio_units()                                 # XOR: the hotspot is down before the client starts
        self.role = "client"
        self.ip_cfg = {**self.ip_cfg, "wifi": wifi}
        self._regdomain(country)
        self._write(os.path.join(self.run_dir, "wpa_supplicant.conf"),
                    netrender.wpa_supplicant(wifi, country, os.path.join(self.run_dir, "wpa")))
        self.firewall()
        self._networkd()
        self.run("systemctl", "start", WPA_UNIT.format(self.wlan))

    def hotspot(self, hs: dict, country: str) -> list | None:
        if not self.wlan:
            return None
        self._stop_radio_units()                                 # XOR: the client is down before the hotspot starts
        self._regdomain(country)
        results = self.scan("station")                           # the radio is idle now: best moment for the owner's network list
        self.role = "hotspot"
        self._write(os.path.join(self.run_dir, "hostapd.conf"),
                    netrender.hostapd(hs, country, self.wlan, os.path.join(self.run_dir, "hostapd")))
        self._write(os.path.join(self.run_dir, "dnsmasq.conf"), netrender.dnsmasq(self.wlan, self.run_dir))
        self.firewall()                                          # hotspot rules are in place before the first client can join
        self._networkd()
        self.run("systemctl", "start", HOSTAPD_UNIT.format(self.wlan), DHCP_UNIT.format(self.wlan))
        return results

    def radio_off(self) -> None:
        self._stop_radio_units()
        self.role = "off"
        self.firewall()
        self._networkd()

    def scan(self, mode: str) -> list | None:
        """None = scanning is not possible right now (e.g. not supported in AP mode by the driver)."""
        if not self.wlan:
            return None
        if mode == "station" and self.role == "off":
            self.run("ip", "link", "set", self.wlan, "up")
        argv = ["iw", "dev", self.wlan, "scan"] + (["ap-force"] if mode == "ap" else [])
        rc, out = self.runner(argv, timeout=20.0)
        if rc != 0:
            self.log(f"network agent: scan ({mode}) not possible: {out.strip()[-120:]}")
            return None
        return parse_iw_scan(out)

    def hostname(self, name: str) -> None:
        if name == self.current_hostname:
            return
        if self.run("hostnamectl", "--transient", "set-hostname", name):
            self.current_hostname = name
            r = self.avahi(name)
            if r not in ("ok", "same", "unavailable"):
                self.log(f"network agent: avahi host name not updated ({r})")

    # ---- observation
    def _ctrl(self, which: str) -> CtrlClient | None:
        attr, sub = ("wpa", "wpa") if which == "wpa" else ("ap", "hostapd")
        c = getattr(self, attr)
        if c is None and self.wlan and os.path.exists(os.path.join(self.run_dir, sub, self.wlan)):
            c = CtrlClient(os.path.join(self.run_dir, sub, self.wlan), os.path.join(self.run_dir, "ctl"))
            setattr(self, attr, c)
        return c

    def observe(self) -> Observation:
        ifs = self.interfaces()
        if self.wlan is None or self.wlan not in ifs or not self.eth:
            self.refresh()
        eth = [ifs[n] for n in self.eth if n in ifs]
        o = Observation(eth_carrier=any(i["carrier"] for i in eth),
                        eth_up=any(i["carrier"] and any(a["usable"] for a in i["addresses"]) for i in eth),
                        wifi_present=bool(self.wlan))
        w = ifs.get(self.wlan) if self.wlan else None
        if w:
            o.wifi_addresses = [a["address"] for a in w["addresses"] if a["usable"]]
            o.wifi_address = bool(o.wifi_addresses) and self.role == "client"
        if self.role == "client":
            c = self._ctrl("wpa")
            if c:
                try:
                    c.attach()
                    o.wifi_state = kv(c.request("STATUS")).get("wpa_state", "")
                    o.events = c.events()
                except CtrlError:
                    c.close()
                    self.wpa = None
        elif self.role == "hotspot":
            c = self._ctrl("ap")
            if c:
                try:
                    o.stations = int(kv(c.request("STATUS")).get("num_sta[0]", "0") or 0)
                except (CtrlError, ValueError):
                    c.close()
                    self.ap = None
        return o

    def wifi_details(self) -> dict:
        """Signal and frequency of the client connection (best effort)."""
        c = self.wpa if self.role == "client" else None
        if c is None:
            return {}
        try:
            sig = kv(c.request("SIGNAL_POLL"))
            return {"signal_dbm": int(sig["RSSI"]) if "RSSI" in sig else None, "frequency": int(sig["FREQUENCY"]) if "FREQUENCY" in sig else None}
        except (CtrlError, ValueError):
            return {}

    def time_synchronized(self) -> bool:
        return os.path.exists(self.timesync_flag)
