"""hifi-netd - the root-side network agent (privilege separation, docs/NETWORK.md).

The bridge runs unprivileged and never executes anything as root. This small agent owns the network: it reads the bridge's
``network.json`` (and ``local_hostname`` from ``settings.json``) strictly, runs the state machine (``netmanager``) and applies its
decisions through ``netsys.LinuxSystem``. The bridge talks to it over a UNIX stream socket with a deliberately narrow interface:

    {"op": "status"}                      -> state, addresses, hotspot, trial, scan results, time sync (never a password)
    {"op": "trial", "id": <sha256>}       -> apply network.pending.json (its SHA-256 must equal id; validated again here)
    {"op": "confirm", "id": <sha256>}     -> keep the pending change
    {"op": "revert"}                      -> roll the pending change back now
    {"op": "scan"}                        -> refresh the Wi-Fi network list
    {"op": "hostname"}                    -> re-read local_hostname and apply it (system host name, Avahi)

One JSON object per connection, at most 4 KiB, only from uid 0 or the bridge's uid (SO_PEERCRED). The agent never writes into the
bridge's data directory; the bridge promotes or deletes its pending file after the agent's answer.

    python3 -m btbridge.net.netagent --bridge-data /var/lib/hifi-bridge --bridge-user hifi-bridge
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import sys
import threading
import time
from typing import Callable

from . import netconfig
from .netmanager import NetworkManager

SOCKET_NAME = "agent.sock"
MAX_REQUEST = 4096
OPS = ("status", "trial", "confirm", "revert", "scan", "hostname")
_ID = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def peer_uid(conn: socket.socket) -> int | None:
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid
    except OSError:
        return None


class Agent:
    def __init__(self, system, bridge_data: str, bridge_uid: int, run_dir: str, manager: NetworkManager | None = None,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print):
        self.system, self.data, self.uid, self.run_dir, self.log = system, bridge_data, bridge_uid, run_dir, log
        self.manager = manager or NetworkManager(system, clock, log)
        self.lock = threading.RLock()
        self.config_error: list[str] | None = None
        self.hostname: str | None = None
        self._stop = threading.Event()
        self._scanning = False
        self.sock: socket.socket | None = None

    # ---- inputs from the bridge's data directory (untrusted: strict read + full validation)
    def read_config(self, name: str = netconfig.FILE_NAME) -> tuple[dict | None, bytes | None]:
        raw = netconfig.read_strict(os.path.join(self.data, name), self.uid)
        return (netconfig.parse(raw) if raw is not None or name == netconfig.FILE_NAME else None), raw

    def read_hostname(self) -> str | None:
        try:
            raw = netconfig.read_strict(os.path.join(self.data, "settings.json"), self.uid)
            name = json.loads(raw.decode("utf-8")).get("local_hostname") if raw else None
        except (netconfig.NetConfigError, ValueError, UnicodeDecodeError, AttributeError):
            return None
        return name if isinstance(name, str) and _LABEL.fullmatch(name) else None

    def apply_hostname(self) -> str | None:
        name = self.read_hostname() or "hifi-bridge"
        self.system.hostname(name)
        self.hostname = name
        return name

    def start(self) -> None:
        try:
            cfg, _ = self.read_config()
            self.config_error = None
        except netconfig.NetConfigError as e:
            cfg, self.config_error = netconfig.defaults(), e.problems
            self.log("network agent: network.json rejected (" + ", ".join(e.problems) + ") - Ethernet with DHCP only")
        self.apply_hostname()
        with self.lock:
            self.manager.load(cfg)

    # ---- requests
    def handle(self, req) -> dict:
        if not isinstance(req, dict) or req.get("op") not in OPS:
            return {"error": "bad_request"}
        op = req["op"]
        if op in ("trial", "confirm") and not (isinstance(req.get("id"), str) and _ID.fullmatch(req["id"])):
            return {"error": "bad_request"}
        if op == "status":
            return self.status()
        if op == "hostname":
            return {"hostname": self.apply_hostname()}
        with self.lock:
            if op == "scan":
                self.manager.request_scan()
                return {"ok": True}
            if op == "confirm":
                return {"ok": self.manager.confirm(req["id"])}
            if op == "revert":
                return {"ok": self.manager.revert()}
            try:
                cfg, raw = self.read_config(netconfig.PENDING_NAME)
            except netconfig.NetConfigError as e:
                return {"error": "invalid", "problems": e.problems}
            if raw is None or netconfig.digest(raw) != req["id"]:
                return {"error": "mismatch"}
            return {"result": self.manager.begin_trial(cfg, req["id"])}

    def status(self) -> dict:
        with self.lock:
            st = self.manager.status()
        ifs = self.system.interfaces()
        roles = {n: "ethernet" for n in self.system.eth}
        if self.system.wlan:
            roles[self.system.wlan] = "wifi"
        st.update(managed=True, hostname=self.hostname, time_synchronized=self.system.time_synchronized(),
                  firewall=None if self.system.firewall_ok is None else ("applied" if self.system.firewall_ok else "failed"),
                  config_error=self.config_error, errors=list(self.system.errors[-5:]),
                  interfaces=[{"name": n, "role": roles.get(n, "other"), "carrier": i["carrier"], "mac": i["mac"],
                               "addresses": [f'{a["address"]}/{a["prefix"]}' for a in i["addresses"]], "default_route": i["default_route"]}
                              for n, i in sorted(ifs.items())])
        if st["state"] == "connected":
            st["wifi"].update(self.system.wifi_details())
        return st

    # ---- loop
    def tick(self) -> None:
        obs = self.system.observe()
        with self.lock:
            self.manager.tick(obs)
            mode = None if self._scanning else self.manager.scan_request()
        if mode:
            self._scanning = True
            threading.Thread(target=self._scan, args=(mode,), daemon=True, name="net-scan").start()

    def _scan(self, mode: str) -> None:
        results = None
        try:
            results = self.system.scan(mode)
        except Exception as e:                                 # never leave the scan flag stuck
            self.log(f"network agent: scan error {type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.manager.scan_done(results)
            self._scanning = False

    def loop(self, interval: float = 1.0) -> None:
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception as e:                             # the agent must outlive a bad observation
                self.log(f"network agent: tick error {type(e).__name__}: {e}")

    # ---- socket
    def bind(self, gid: int | None) -> str:
        path = os.path.join(self.run_dir, SOCKET_NAME)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o117)
        try:
            s.bind(path)
        finally:
            os.umask(old)
        if gid is not None and os.geteuid() == 0:
            os.chown(path, 0, gid)
            os.chown(self.run_dir, 0, gid)
            os.chmod(self.run_dir, 0o750)                       # nosec B103 - root:hifi-bridge; the bridge must reach the socket
        os.chmod(path, 0o660)                                   # nosec B103 - root:hifi-bridge only; requests are uid-checked too
        s.listen(8)
        s.settimeout(0.5)
        self.sock = s
        return path

    def serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True, name="net-req").start()

    def _client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5.0)
            uid = peer_uid(conn)
            if uid not in (0, self.uid):
                self.log(f"network agent: request from uid {uid} refused")
                reply = {"error": "forbidden"}
            else:
                data = b""
                try:
                    while b"\n" not in data and len(data) <= MAX_REQUEST:
                        chunk = conn.recv(MAX_REQUEST + 1)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    return
                if len(data) > MAX_REQUEST:
                    reply = {"error": "too_large"}
                else:
                    try:
                        reply = self.handle(json.loads(data.decode("utf-8")))
                    except (ValueError, UnicodeDecodeError):
                        reply = {"error": "bad_request"}
            try:
                conn.sendall(json.dumps(reply).encode() + b"\n")
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self.sock:
            self.sock.close()


def main(argv=None) -> int:
    import grp
    import pwd

    from ..supervision import StopSignal, SystemdNotifier
    from .netsys import LinuxSystem
    ap = argparse.ArgumentParser(prog="btbridge.net.netagent")
    ap.add_argument("--bridge-data", default="/var/lib/hifi-bridge")
    ap.add_argument("--bridge-user", default="hifi-bridge")
    ap.add_argument("--run-dir", default="/run/hifi-net")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--stream-port", type=int, default=8090)
    a = ap.parse_args(argv)
    try:
        pw = pwd.getpwnam(a.bridge_user)
        uid, gid = pw.pw_uid, grp.getgrgid(pw.pw_gid).gr_gid
    except KeyError:
        print(f"unknown user {a.bridge_user}", file=sys.stderr)
        return 2
    os.makedirs(a.run_dir, mode=0o750, exist_ok=True)
    log = lambda m: print(m, flush=True)                    # noqa: E731
    system = LinuxSystem(a.run_dir, a.http_port, a.https_port, a.stream_port, log=log)
    agent = Agent(system, a.bridge_data, uid, a.run_dir, log=log)
    agent.start()
    agent.bind(gid)
    threading.Thread(target=agent.serve, daemon=True, name="net-serve").start()
    threading.Thread(target=agent.loop, daemon=True, name="net-loop").start()
    SystemdNotifier().notify(READY=1, STATUS="network agent running")
    log(f"network agent: running (Ethernet {', '.join(system.eth) or '-'}, Wi-Fi {system.wlan or '-'})")
    StopSignal().wait()
    agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
