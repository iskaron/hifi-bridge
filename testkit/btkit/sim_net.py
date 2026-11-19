"""SIMULATED network hardware for the root network agent (unit tests, Docker browser lab). No real interface is touched.

``SimulatedNetworkSystem`` implements the same adapter interface as ``btbridge.net.netsys.LinuxSystem``: a Wi-Fi radio that joins a
network from a scripted "air" after ``connect_s`` (wrong password -> WRONG_KEY events, unknown SSID -> NETWORK-NOT-FOUND), a hotspot with a
settable number of stations, an Ethernet link that can be plugged in or out, scan results and time sync.

Lab entry point (runs the REAL agent with this simulated system on a UNIX socket, as root would on the device)::

    python3 -m btkit.sim_net --bridge-data /data --run-dir /run/hifi-net [--ethernet] [--control-port 8098]

``--control-port`` exposes a tiny HTTP control (``/ethernet/on|off``, ``/stations/<n>``, ``/sync``) for browser tests.
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from btbridge.net.netmanager import Observation

AIR = [
    {"ssid": "Home", "psk": "wifi-secret-123", "signal_dbm": -48, "security": "wpa2/wpa3", "frequency": 2437, "hidden": False},
    {"ssid": "Gartenhaus", "psk": "garden-pass-1", "signal_dbm": -71, "security": "wpa2", "frequency": 2462, "hidden": False},
    {"ssid": "Nachbar 5G", "psk": "x" * 12, "signal_dbm": -83, "security": "wpa3", "frequency": 5180, "hidden": False},
    {"ssid": "Cafe Gast", "psk": "", "signal_dbm": -77, "security": "open", "frequency": 2412, "hidden": False},
    {"ssid": "Versteckt", "psk": "hidden-pass-1", "signal_dbm": -60, "security": "wpa2", "frequency": 2437, "hidden": True},
]


class SimulatedNetworkSystem:
    def __init__(self, air: list | None = None, ethernet: bool = False, clock=time.monotonic, connect_s: float = 3.0,
                 wlan: str | None = "wlan0", eth: tuple = ("eth0",), synced: bool = True):
        self.air = [dict(n) for n in (air if air is not None else AIR)]
        self.eth, self.wlan = list(eth), wlan
        self.ethernet_link, self.clock, self.connect_s, self.synced = ethernet, clock, connect_s, synced
        self.role = "off"
        self.firewall_ok: bool | None = True
        self.errors: list[str] = []
        self.calls: list[tuple] = []
        self.wifi: dict | None = None
        self.started = 0.0
        self.stations = 0
        self.hostnames: list[str] = []
        self._reported: set = set()

    # ---- adapter interface (actions)
    def ethernet(self, ip):
        self.calls.append(("ethernet", ip["ipv4"]["method"]))

    def client(self, wifi, country):
        if not self.wlan:
            return
        self.role, self.wifi, self.started, self._reported = "client", wifi, self.clock(), set()
        self.calls.append(("client", wifi["ssid"], country))

    def hotspot(self, hs, country):
        if not self.wlan:
            return None
        self.role, self.wifi = "hotspot", None
        self.calls.append(("hotspot", hs["ssid"], country))
        return self.scan("station")

    def radio_off(self):
        self.role, self.wifi = "off", None
        self.calls.append(("off",))

    def scan(self, mode):
        if not self.wlan:
            return None
        self.calls.append(("scan", mode))
        return [{k: n[k] for k in ("ssid", "signal_dbm", "security", "frequency")} for n in self.air if not n["hidden"]]

    def hostname(self, name):
        self.hostnames.append(name)

    # ---- observation
    def _net(self):
        w = self.wifi
        return next((n for n in self.air if w and n["ssid"] == w["ssid"]), None)

    def _connected(self) -> bool:
        n = self._net()
        return bool(self.role == "client" and n and n["psk"] == self.wifi["psk"] and self.clock() - self.started >= self.connect_s)

    def observe(self) -> Observation:
        o = Observation(eth_carrier=self.ethernet_link, eth_up=self.ethernet_link, wifi_present=bool(self.wlan))
        if self.role == "client":
            n = self._net()
            if self._connected():
                o.wifi_state, o.wifi_address, o.wifi_addresses = "COMPLETED", True, ["192.168.1.57"]
            elif n is None:
                o.wifi_state = "SCANNING"
                if "nf" not in self._reported:
                    self._reported.add("nf")
                    o.events = ["<3>CTRL-EVENT-NETWORK-NOT-FOUND"]
            elif n["psk"] != self.wifi["psk"]:
                o.wifi_state = "4WAY_HANDSHAKE"
                if "wk" not in self._reported:
                    self._reported.add("wk")
                    o.events = [f'<3>CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="{n["ssid"]}" auth_failures=1 duration=10 reason=WRONG_KEY']
            else:
                o.wifi_state = "ASSOCIATING"
        elif self.role == "hotspot":
            o.stations = self.stations
        return o

    def interfaces(self) -> dict:
        out = {}
        for n in self.eth:
            addrs = [{"family": 4, "address": "192.168.1.20", "prefix": 24, "usable": True},
                     {"family": 6, "address": "fd00:1::20", "prefix": 64, "usable": True}] if self.ethernet_link else []
            out[n] = {"index": 2, "up": True, "carrier": self.ethernet_link, "operstate": "up" if self.ethernet_link else "down",
                      "mac": "02:00:00:00:00:20", "addresses": addrs, "default_route": self.ethernet_link}
        if self.wlan:
            addrs = []
            if self._connected():
                addrs = [{"family": 4, "address": "192.168.1.57", "prefix": 24, "usable": True}]
            elif self.role == "hotspot":
                addrs = [{"family": 4, "address": "10.42.0.1", "prefix": 24, "usable": True}]
            out[self.wlan] = {"index": 3, "up": self.role != "off", "carrier": bool(addrs), "operstate": "up" if addrs else "down",
                              "mac": "02:00:00:00:00:57", "addresses": addrs, "default_route": self._connected()}
        return out

    def wifi_details(self) -> dict:
        n = self._net()
        return {"signal_dbm": n["signal_dbm"], "frequency": n["frequency"]} if n else {}

    def time_synchronized(self) -> bool:
        return self.synced


def _control_server(system, port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = self.path.strip("/").split("/")
            if p[:1] == ["ethernet"] and len(p) == 2:
                system.ethernet_link = p[1] == "on"
            elif p[:1] == ["stations"] and len(p) == 2 and p[1].isdigit():
                system.stations = int(p[1])
            elif p == ["sync"]:
                system.synced = True
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok\n")

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)          # nosec B104 - isolated lab network only
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv=None) -> int:
    from btbridge.net.netagent import Agent
    ap = argparse.ArgumentParser(prog="btkit.sim_net")
    ap.add_argument("--bridge-data", required=True)
    ap.add_argument("--run-dir", default="/run/hifi-net")
    ap.add_argument("--ethernet", action="store_true")
    ap.add_argument("--unsynced", action="store_true")
    ap.add_argument("--control-port", type=int, default=0)
    a = ap.parse_args(argv)
    os.makedirs(a.run_dir, mode=0o750, exist_ok=True)
    system = SimulatedNetworkSystem(ethernet=a.ethernet, synced=not a.unsynced)
    agent = Agent(system, a.bridge_data, os.getuid(), a.run_dir, log=lambda m: print(m, flush=True))
    agent.start()
    agent.bind(None)
    threading.Thread(target=agent.serve, daemon=True).start()
    if a.control_port:
        _control_server(system, a.control_port)
    print(f"SIMULATED network agent on {a.run_dir}/agent.sock", flush=True)
    agent.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
