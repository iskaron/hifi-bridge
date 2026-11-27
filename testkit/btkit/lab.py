"""Integration-lab helpers: real LMS + Squeezelite containers, in-process bridge with simulated phone."""
from __future__ import annotations

import json
import os
import platform
import re
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

    # Squeezelite logs every volume change it receives ("audg gainL: N"); 65536 = unity gain (LMS volume 100).
    FULL_GAIN = 65536

    def wait_players_connected(self, timeout=60.0):
        """Block until Lyrion lists every lab player as connected (a recreated Lyrion has no client objects until the
        players have re-connected, and commands sent before that are lost)."""
        want = {m.lower() for m in PLAYERS.values()}
        deadline = time.time() + timeout
        have = set()
        while True:
            try:
                loop = self.lms.req("", "serverstatus", 0, 50).get("players_loop") or []
                have = {p["playerid"].lower() for p in loop if p.get("connected")}
                if want <= have:
                    return
            except OSError:
                pass                                         # Lyrion still starting
            if time.time() > deadline:
                raise RuntimeError(f"lab players not connected within {timeout:.0f}s: missing {sorted(want - have)}")
            time.sleep(0.25)

    @staticmethod
    def last_gain(name):
        """Gain the player actually applied last (from its Squeezelite log), None if unknown."""
        try:
            with open(f"/captures/{name}/squeezelite.log", "rb") as f:
                f.seek(0, 2); f.seek(max(0, f.tell() - 65536))
                gains = re.findall(rb"audg gainL: (\d+)", f.read())
            return int(gains[-1]) if gains else None
        except OSError:
            return None

    # Squeezelite logs the transition of every stream start: "strm s autostart: A transition period: P transition type: T" (0 = none,
    # 2 = fade-in).  Lyrion sends a fade-in when a play request carried one and no stream has started since (see settle_players).
    _TRANSITION = re.compile(rb"strm s autostart: \d+ transition period: \d+ transition type: (\d+)")
    _settled = False                                   # once per process: settle_players() ran against this Lyrion

    @classmethod
    def transitions_since(cls, name, offset):
        """Transition types of the stream starts Squeezelite logged after byte ``offset`` of its log."""
        with open(f"/captures/{name}/squeezelite.log", "rb") as f:
            f.seek(offset)
            return [int(t) for t in cls._TRANSITION.findall(f.read())]

    def settle_players(self, timeout=20.0):
        """Start and stop one stream on every lab player, then confirm that a stream starts without a transition.

        Why (OPEN_QUESTIONS #86): when a player connects to a (re)started Lyrion while its persisted state says it was playing
        (`playingAtPowerOff` + the queue in `prefs/clientplaylist_<mac>.m3u`, e.g. an old bridge URL from an earlier lab session),
        Lyrion resumes that item with a 1 s fade-in (`playlist jump <i> 1`). The old URL cannot be streamed, and Lyrion clears the
        pending fade-in only when a stream really starts - so the first stream of the suite would fade in and the bit-exact analysis
        cannot align. `stop` and `playlist clear` do not clear it. A stream that starts does, so every player plays the lab's own
        synthetic stream once; if that start carried a transition, a second start must not."""
        src = Radio(port=8081)
        try:
            for attempt in (1, 2):
                marks = {n: self.log_size(n) for n in PLAYERS}
                for pid in PLAYERS.values():
                    self.lms.req(pid, "playlist", "play", src.url, "lab settle")
                deadline = time.time() + timeout
                pending = set(PLAYERS)
                while pending:
                    for n in sorted(pending):
                        st = self.status(n)
                        if st.get("mode") == "play" and float(st.get("time") or 0) > 0:
                            pending.discard(n)
                    if pending and time.time() > deadline:
                        raise RuntimeError(f"lab settle: no stream started within {timeout:.0f}s on {sorted(pending)}")
                    time.sleep(0.1)
                for pid in PLAYERS.values():
                    self.lms.req(pid, "playlist", "clear"); self.lms.req(pid, "stop")
                seen = {n: self.transitions_since(n, marks[n]) for n in PLAYERS}
                carried = {n: t for n, t in seen.items() if any(t)}
                if not carried:
                    return attempt
                print(f"lab settle: stream start {attempt} carried a pending Lyrion transition {carried} (consumed)", flush=True)
            raise RuntimeError(f"lab settle: Lyrion still starts streams with a transition: {carried}")
        finally:
            src.stop()

    def _volume_confirmed(self, name):
        return self.status(name).get("mixer volume") == 100 and self.last_gain(name) in (self.FULL_GAIN, None)

    def reset(self):
        # The persisted per-player volume (Lyrion prefs survive a lab recreate, DECISIONS #47) is applied when a player connects, and
        # a volume command sent before that is lost.  A gain != unity makes the bit-exact checks fail ("samples altered"), so wait
        # for the players, then confirm the gain the player really applied (not only Lyrion's preference) and retry if needed.
        self.wait_players_connected()
        if not Lab._settled:                                 # once per process, before the first test (OPEN_QUESTIONS #86)
            for pid in PLAYERS.values():
                self.lms.req(pid, "sync", "-"); self.lms.req(pid, "power", 1)
            self.settle_players()
            Lab._settled = True
        for attempt in range(10):
            for pid in PLAYERS.values():
                self.lms.req(pid, "sync", "-")
            for pid in PLAYERS.values():
                self.lms.req(pid, "playlist", "clear"); self.lms.req(pid, "stop"); self.lms.req(pid, "power", 1)
                if attempt >= 2:                             # nudge: a set to the value Lyrion already holds can be a no-op
                    self.lms.req(pid, "mixer", "volume", 99)
                self.lms.req(pid, "mixer", "volume", 100)
            time.sleep(0.5)
            if all(self._volume_confirmed(n) for n in PLAYERS):
                break
        else:
            raise RuntimeError("lab reset: player volume not confirmed at 100: "
                               + str({n: (self.status(n).get("mixer volume"), self.last_gain(n)) for n in PLAYERS}))
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
                icy=False, icy_mp3=False, port=8090, sim=None, fill=True, pairing_s=300.0, trace=False, **ctl):
    sim = sim or SimulatedA2dpSource()
    rc = RuntimeConfig(lms_url="http://lms:9000", advertise_host=my_ip(), stream_port=port, stream_format=fmt,
                       lead_in_ms=lead_in_ms, icy_metadata=icy, icy_mp3=icy_mp3, pairing_s=pairing_s, stream_trace=trace)
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
