"""SIMULATED bluetoothd for the demo: the 'radio side' of the bridge.

The bridge product runs unchanged with ``--source bluez`` and talks D-Bus to this process exactly as it would to BlueZ on the Pi. Virtual
phones (the demo console) act "over the air" through a small HTTP API (port 8099, internal demo network only) and send their decoded
audio over TCP (port 8098). For each connected phone a PipeWire node ``bluez_input.<MAC>`` is created (module-pipe-source), which the
product captures with its own ``pw-cat`` command - as the PipeWire BlueZ plugin would provide it on the device.

What is modelled (BlueZ D-Bus API, as documented): Adapter1 Alias/Pairable/Discoverable, Device1 Paired/Trusted/Connected/UUIDs,
the pairing agent (RequestAuthorization, AuthorizeService), Device1.Disconnect, Adapter1.RemoveDevice, MediaTransport1 State/Volume,
MediaPlayer1 Status/Track, persistent pairings (like /var/lib/bluetooth). NOT modelled: radio, SBC/AAC codecs, link quality, phone quirks.
"""
from __future__ import annotations

import argparse
import json
import os
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from btkit.fake_bluez import ADAPTER, FakeBluez

A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"
AVRCP_TARGET = "0000110c-0000-1000-8000-00805f9b34fb"
DEV = "org.bluez.Device1"
TRANSPORT = "org.bluez.MediaTransport1"
PLAYER = "org.bluez.MediaPlayer1"
MAX_EVENTS = 50


def dev_path(mac: str) -> str:
    return f"{ADAPTER}/dev_{mac.upper().replace(':', '_')}"


def _val(v):
    return v[1] if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) else v


class VirtualBluetoothd(FakeBluez):
    def __init__(self, bus_address: str, state_file: str, nodes, adapter_address: str = "02:00:00:B7:1D:6E",
                 name: str = "hifi-bridge", log=lambda m: None, connect_settle_s: float = 0.6):
        super().__init__(bus_address)
        self.state_file, self.nodes, self.log = state_file, nodes, log
        self.adapter_address, self.default_name, self.connect_settle_s = adapter_address, name, connect_settle_s
        self._events: dict[str, list[dict]] = {}
        self._seq = 0
        self._ev_lock = threading.Lock()
        self._act = threading.RLock()          # serialises phone actions (one radio)
        self._connecting: set[str] = set()
        self._save_lock = threading.Lock()

    # ---- lifecycle ---------------------------------------------------------------------------------------
    def start(self, keep_objects: bool = False):
        super().start(keep_objects)
        if not keep_objects:
            a = self.objects[ADAPTER]["org.bluez.Adapter1"]
            a["Address"] = ("s", self.adapter_address)
            a["Alias"] = ("s", self.default_name)
            a["Name"] = ("s", self.default_name)
            for mac, d in self._load().items():                         # bonded devices survive restarts
                self.add_object(dev_path(mac), {DEV: self._dev_props(mac, d["name"], paired=True, trusted=bool(d.get("trusted")))})

    def _dev_props(self, mac, name, paired, trusted):
        return {"Address": mac.upper(), "Alias": name, "Name": name, "Paired": paired, "Trusted": trusted, "Connected": False,
                "UUIDs": [A2DP_SOURCE, AVRCP_TARGET]}

    # ---- persistence -------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.state_file) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        with self._save_lock:
            self._save_locked()

    def _save_locked(self) -> None:
        bonded = {}
        for path, ifs in list(self.objects.items()):
            d = ifs.get(DEV)
            if d and _val(d.get("Paired")):
                bonded[_val(d["Address"])] = {"name": _val(d.get("Alias", "")), "trusted": bool(_val(d.get("Trusted", False)))}
        tmp = self.state_file + ".tmp"
        os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(bonded, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.state_file)

    # ---- events for the phones ---------------------------------------------------------------------------
    def _event(self, mac: str, kind: str, detail: str = "") -> None:
        with self._ev_lock:
            self._seq += 1
            lst = self._events.setdefault(mac.upper(), [])
            lst.append({"seq": self._seq, "kind": kind, "detail": detail, "t": round(time.time(), 3)})
            del lst[:-MAX_EVENTS]
        self.log(f"{mac} {kind} {detail}".strip())

    def events(self, mac: str, since: int = 0) -> list[dict]:
        with self._ev_lock:
            return [e for e in self._events.get(mac.upper(), []) if e["seq"] > since]

    # ---- queries -----------------------------------------------------------------------------------------
    def _adapter(self, key):
        return _val(self.objects[ADAPTER]["org.bluez.Adapter1"].get(key))

    def visible(self) -> dict | None:
        if not self._adapter("Discoverable"):
            return None
        return {"name": self._adapter("Alias") or self.default_name, "address": self.adapter_address}

    def device(self, mac: str) -> dict | None:
        d = (self.objects.get(dev_path(mac)) or {}).get(DEV)
        if not d:
            return None
        return {"address": mac.upper(), "name": _val(d.get("Alias", "")), "paired": bool(_val(d.get("Paired"))),
                "trusted": bool(_val(d.get("Trusted"))), "connected": bool(_val(d.get("Connected")))}

    # ---- phone actions ("over the air") ------------------------------------------------------------------
    def pair(self, mac: str, name: str) -> str:
        with self._act:
            if not (self._adapter("Discoverable") and self._adapter("Pairable")):
                return "not_discoverable"
            path = dev_path(mac)
            if path not in self.objects:
                self.add_object(path, {DEV: self._dev_props(mac, name, paired=False, trusted=False)})
            else:
                self.set_props(path, DEV, Alias=name, Name=name)
            res = self._agent(path, "RequestAuthorization", "o", (path,))
            if res != ("ok",):
                if not (self.device(mac) or {}).get("paired"):
                    self.remove_object(path)
                self._event(mac, "pairing_rejected", res[-1])
                return "rejected"
            self.set_props(path, DEV, Paired=True)
            self._save()
            self._event(mac, "paired")
            return "paired"

    def _agent(self, path, member, sig, body):
        if not self.agent:
            return ("error", "no agent")
        try:
            return self.agent_call(member, path, sig=sig, body=body)
        except Exception as e:                                          # agent gone / timed out = rejected
            return ("error", f"{type(e).__name__}")

    def connect(self, mac: str) -> str:
        with self._act:
            dev = self.device(mac)
            if not dev or not dev["paired"]:
                return "not_paired"
            if dev["connected"]:
                return "connected"
            path = dev_path(mac)
            if not dev["trusted"]:
                if self._agent(path, "AuthorizeService", "os", (path, A2DP_SOURCE)) != ("ok",):
                    self._event(mac, "rejected", "service not authorised")
                    return "rejected"
            self.nodes.load(mac)                                        # PipeWire BlueZ node appears with the A2DP link
            self._connecting.add(mac.upper())
            self.set_props(path, DEV, Connected=True)
            self.add_object(path + "/fd0", {TRANSPORT: {"Device": ("o", path), "State": "idle", "Volume": ("q", 80),
                                                        "UUID": A2DP_SOURCE}})
            self.add_object(path + "/player0", {PLAYER: {"Device": ("o", path), "Status": "stopped", "Name": "Music"}})
        try:
            end = time.time() + self.connect_settle_s                   # the product may refuse (Device1.Disconnect) right away
            while time.time() < end:
                if not (self.device(mac) or {}).get("connected"):
                    return "refused"
                time.sleep(0.02)
        finally:
            self._connecting.discard(mac.upper())
        self._event(mac, "connected")
        return "connected"

    def _teardown(self, mac: str) -> None:
        path = dev_path(mac)
        for sub in ("/fd0", "/player0"):
            if path + sub in self.objects:
                self.remove_object(path + sub)
        self.nodes.unload(mac)

    def disconnect(self, mac: str) -> None:
        with self._act:
            if (self.device(mac) or {}).get("connected"):
                self._teardown(mac)
                self.set_props(dev_path(mac), DEV, Connected=False)
                self._event(mac, "disconnected")

    def drop(self, mac: str) -> None:
        """Link loss (out of range, battery): the device vanishes without an orderly AVRCP/A2DP shutdown."""
        with self._act:
            if (self.device(mac) or {}).get("connected"):
                self.set_props(dev_path(mac), DEV, Connected=False)
                self._teardown(mac)
                self._event(mac, "link_lost")

    def _connected_path(self, mac: str) -> str | None:
        return dev_path(mac) if (self.device(mac) or {}).get("connected") else None

    def set_transport(self, mac: str, state: str) -> bool:
        p = self._connected_path(mac)
        if p and p + "/fd0" in self.objects:
            self.set_props(p + "/fd0", TRANSPORT, State=state)
            return True
        return False

    def set_volume(self, mac: str, volume: int) -> bool:
        p = self._connected_path(mac)
        if p and p + "/fd0" in self.objects:
            self.set_props(p + "/fd0", TRANSPORT, Volume=("q", max(0, min(127, int(volume)))))
            return True
        return False

    def set_status(self, mac: str, status: str) -> bool:
        p = self._connected_path(mac)
        if p and p + "/player0" in self.objects:
            self.set_props(p + "/player0", PLAYER, Status=status)
            return True
        return False

    def set_track(self, mac: str, title: str, artist: str, album: str, duration_ms: int) -> bool:
        p = self._connected_path(mac)
        if p and p + "/player0" in self.objects:
            track = {"Title": ("s", title), "Artist": ("s", artist), "Album": ("s", album), "Duration": ("u", int(duration_ms))}
            self.set_props(p + "/player0", PLAYER, Track=("a{sv}", track))
            return True
        return False

    def write_pcm(self, mac: str, data: bytes) -> int:
        if not (self.device(mac) or {}).get("connected"):
            return 0
        return self.nodes.write(mac, data)

    # ---- calls from the product (bridge -> "bluetoothd") ---------------------------------------------------
    def _handle(self, msg):
        """Runs on the D-Bus service thread. It must never take ``_act``: a phone action may be waiting for the product's agent reply,
        and the product's agent itself calls back into this thread (Trusted)."""
        from jeepney import HeaderFields
        f = msg.header.fields
        path, iface, member = f.get(HeaderFields.path), f.get(HeaderFields.interface), f.get(HeaderFields.member)
        mac = kind = None
        if iface == DEV and member == "Disconnect" and path in self.objects:
            mac = _val(self.objects[path][DEV]["Address"])
            if _val(self.objects[path][DEV].get("Connected")):
                kind = "refused" if mac in self._connecting else "disconnected_by_bridge"
        elif iface == "org.bluez.Adapter1" and member == "RemoveDevice" and msg.body and msg.body[0] in self.objects:
            mac, kind = _val(self.objects[msg.body[0]][DEV]["Address"]), "removed"
        if kind:                                    # tell the phone and tear the link down BEFORE the state change becomes visible
            self._event(mac, kind, "the bridge owner removed this phone" if kind == "removed" else "")
            self._teardown(mac)
        super()._handle(msg)
        if kind == "removed" or (iface == "org.freedesktop.DBus.Properties" and member == "Set"
                                 and msg.body[:2] == (DEV, "Trusted")):
            self._save()


# ---- PipeWire nodes (real implementation) -------------------------------------------------------------------
class PipeWireNodes:
    """One ``module-pipe-source`` per connected phone, named like the PipeWire BlueZ plugin's node (``bluez_input.<MAC>``)."""

    PIPE_BYTES = 16384                                                  # ~93 ms: bounded latency; a full pipe drops (like a radio)

    def __init__(self, fifo_dir: str, run=None, log=lambda m: None):
        import subprocess
        self.fifo_dir, self.log = fifo_dir, log
        self.run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=10))
        self.modules: dict[str, str] = {}
        self.fds: dict[str, int] = {}
        self.dropped: dict[str, int] = {}
        self._lock = threading.Lock()
        os.makedirs(fifo_dir, exist_ok=True)

    def node_name(self, mac: str) -> str:
        from btbridge.adapters.bluez.pcm import node_name_for            # the product's own naming rule
        return node_name_for(mac)

    def load(self, mac: str) -> None:
        import fcntl
        with self._lock:
            if mac in self.modules:
                return
            fifo = os.path.join(self.fifo_dir, mac.replace(":", "") + ".fifo")
            if not os.path.exists(fifo):
                os.mkfifo(fifo, 0o600)
            fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)              # writer first: the module never sees EOF
            try:
                fcntl.fcntl(fd, 1031, self.PIPE_BYTES)                  # F_SETPIPE_SZ
            except OSError:
                pass
            r = self.run(["pactl", "load-module", "module-pipe-source", f"source_name={self.node_name(mac)}", f"file={fifo}",
                          "format=s16le", "rate=44100", "channels=2"])
            if r.returncode != 0:
                os.close(fd)
                raise RuntimeError(f"pactl load-module failed: {r.stderr.strip()}")
            self.modules[mac], self.fds[mac] = r.stdout.strip(), fd
            self.log(f"PipeWire node {self.node_name(mac)} (module {self.modules[mac]})")

    def unload(self, mac: str) -> None:
        with self._lock:
            mod, fd = self.modules.pop(mac, None), self.fds.pop(mac, None)
            if mod:
                self.run(["pactl", "unload-module", mod])
            if fd is not None:
                os.close(fd)

    def write(self, mac: str, data: bytes) -> int:
        fd = self.fds.get(mac)
        if fd is None:
            return 0
        try:
            return os.write(fd, data)
        except BlockingIOError:
            self.dropped[mac] = self.dropped.get(mac, 0) + len(data)
            return 0
        except OSError:
            return 0


# ---- network interfaces --------------------------------------------------------------------------------------
def serve_air(vbt: VirtualBluetoothd, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # nosec B104 - internal demo network
    """JSON API used by the virtual phones (what a phone can do over the air)."""
    actions = {
        "pair": lambda b: {"result": vbt.pair(b["mac"], b.get("name", "Phone"))},
        "connect": lambda b: {"result": vbt.connect(b["mac"])},
        "disconnect": lambda b: (vbt.disconnect(b["mac"]), {"result": "ok"})[1],
        "drop": lambda b: (vbt.drop(b["mac"]), {"result": "ok"})[1],
        "transport": lambda b: {"result": vbt.set_transport(b["mac"], b["state"])},
        "status": lambda b: {"result": vbt.set_status(b["mac"], b["status"])},
        "volume": lambda b: {"result": vbt.set_volume(b["mac"], int(b["volume"]))},
        "track": lambda b: {"result": vbt.set_track(b["mac"], b.get("title", ""), b.get("artist", ""), b.get("album", ""),
                                                    int(b.get("duration_ms", 0)))},
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/air/scan":
                return self._send(200, {"visible": [v] if (v := vbt.visible()) else []})
            if u.path == "/air/device":
                return self._send(200, {"device": vbt.device(q.get("mac", "")),
                                        "events": vbt.events(q.get("mac", ""), int(q.get("since", 0) or 0))})
            if u.path == "/health":
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            name = self.path.removeprefix("/air/")
            fn = actions.get(name)
            if fn is None:
                return self._send(404, {"error": "unknown action"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                self._send(200, fn(body))
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": f"{type(e).__name__}: {e}"})

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="air-api").start()
    return srv


def serve_pcm(vbt: VirtualBluetoothd, port: int, host: str = "0.0.0.0"):  # nosec B104 - internal demo network
    """A2DP stand-in: a phone opens a TCP stream, sends its MAC and a newline, then decoded PCM in real time."""
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            mac = self.rfile.readline(64).decode("ascii", "replace").strip().upper()
            while True:
                data = self.rfile.read1(8192) if hasattr(self.rfile, "read1") else self.request.recv(8192)
                if not data:
                    return
                vbt.write_pcm(mac, data)

    class S(socketserver.ThreadingTCPServer):
        daemon_threads, allow_reuse_address = True, True
    srv = S((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="pcm").start()
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SIMULATED bluetoothd for the Hi-Fi Bridge demo")
    ap.add_argument("--bus", required=True, help="D-Bus address the bridge uses (--dbus)")
    ap.add_argument("--state", required=True, help="bonded devices (like /var/lib/bluetooth)")
    ap.add_argument("--fifo-dir", required=True)
    ap.add_argument("--air-port", type=int, default=8099)
    ap.add_argument("--pcm-port", type=int, default=8098)
    a = ap.parse_args(argv)
    log = lambda m: print(f"[vbluez] {m}", flush=True)  # noqa: E731
    vbt = VirtualBluetoothd(a.bus, a.state, PipeWireNodes(a.fifo_dir, log=log), log=log)
    vbt.start()
    serve_air(vbt, a.air_port)
    serve_pcm(vbt, a.pcm_port)
    log(f"SIMULATED bluetoothd on {a.bus}; air API :{a.air_port}, A2DP stand-in :{a.pcm_port}")
    from btbridge.supervision import StopSignal
    StopSignal().wait()
    vbt.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
