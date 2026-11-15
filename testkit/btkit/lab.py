"""Integration-lab helpers: real LMS + Squeezelite containers, in-process bridge with simulated phone."""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import threading
import time

from btbridge.app import BridgeRuntime, RuntimeConfig
from btbridge.core.controller import Config

from . import analyze as A
from .lms_rpc import Lms, PLAYERS
from .sim_a2dp import SimulatedA2dpSource
from . import synth_server as _synth
from .synth_server import H as SynthHandler

_synth.log = lambda **kw: None      # silence the exploratory server's request log inside tests

MEAS = "/work/measurements"


def my_ip() -> str:
    return socket.gethostbyname(socket.gethostname())


class Radio:
    """Stand-in for 'someone else's' LMS source (internet radio / local music): a second synthetic
    endless stream, served by the synth server in a thread.  Real LMS, fake station."""

    def __init__(self, port=8080):
        from http.server import ThreadingHTTPServer
        ThreadingHTTPServer.daemon_threads = True
        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), SynthHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://{my_ip()}:{port}/radio.wav"

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()


class Lab:
    def __init__(self):
        self.lms = Lms()
        self.t_reset = time.time()

    def reset(self):
        for pid in PLAYERS.values():
            self.lms.req(pid, "sync", "-")
        for pid in PLAYERS.values():
            self.lms.req(pid, "playlist", "clear"); self.lms.req(pid, "stop"); self.lms.req(pid, "power", 1); self.lms.req(pid, "mixer", "volume", 100)
        time.sleep(0.5)
        self.t_reset = time.time()

    def status(self, name="player1"):
        return self.lms.req(PLAYERS[name], "status", "-", 1, "tags:u")

    def mode(self, name="player1"):
        return self.status(name).get("mode")

    def url(self, name="player1"):
        pl = self.status(name).get("playlist_loop") or []
        return pl[0].get("url") if pl else None

    def capture(self, name="player1", since=None, until=1e18):
        return A.load(f"/captures/{name}", since=since or self.t_reset, until=until)

    def underruns(self, name, log_offset):
        with open(f"/captures/{name}/squeezelite.log", errors="replace") as f:
            f.seek(log_offset); return f.read().count("STAT: STMo")

    @staticmethod
    def log_size(name):
        return os.path.getsize(f"/captures/{name}/squeezelite.log")


def make_bridge(player="player1", *, fmt="wav", pause_strategy="drop_restart", lead_in_ms=1000, override=True, grace=30.0,
                icy=False, port=8090, sim=None, fill=True, pairing_s=300.0, **ctl):
    sim = sim or SimulatedA2dpSource()
    rc = RuntimeConfig(lms_url="http://lms:9000", advertise_host=my_ip(), stream_port=port, stream_format=fmt,
                       lead_in_ms=lead_in_ms, icy_metadata=icy, pairing_s=pairing_s)
    cc = Config(target_player=PLAYERS[player], override_lms=override, pause_grace_s=grace, pause_strategy=pause_strategy, **ctl)
    logs = []
    rt = BridgeRuntime(rc, cc, source=sim, log=logs.append)
    rt.logs = logs
    rt.start()
    return rt, sim


def wait_for(cond, timeout=10.0, step=0.05):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


def env_info() -> dict:
    def sh(c):
        try:
            return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return "?"
    return {"date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lms": Lms().req("", "serverstatus", 0, 1).get("version"),
            "squeezelite": sh("squeezelite -t 2>&1 | head -1"), "kernel": platform.release(), "python": platform.python_version(),
            "note": "all timings are software/virtual-DAC measurements inside Docker; NOT acoustic, NOT hardware"}


def save(name: str, data: dict) -> None:
    os.makedirs(MEAS, exist_ok=True)
    data = dict(data, env=env_info())
    with open(f"{MEAS}/{name}.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
