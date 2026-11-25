"""Lyrion Media Server control via the standard JSON-RPC interface (``/jsonrpc.js``).

No custom LMS plugin is needed.  Safety design (docs/DECISIONS.md #5): the only
state-changing commands this client can send to a player are

  * ``play``      - explicit takeover with our own session URL
  * ``release``   - ``playlist deleteitem <our url>``; URL-addressed, so it is a
                    no-op if another source is current (verified in the lab)
  * ``power on``  - wake a standby target when taking over
  * ``volume``    - relative mixer volume while we own the target

There is deliberately NO generic stop/pause/clear.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class LmsUnreachable(Exception):
    pass


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Observation:
    reachable: bool = True
    player_known: bool = True
    player_connected: bool = True
    mode: str = "stop"           # play | pause | stop
    power: bool = True
    url: str | None = None       # current playlist item URL
    volume: int | None = None
    title: str | None = None
    time: float | None = None    # elapsed seconds of the current item (LMS 'time'; diagnostics/trace only)
    duration: float | None = None  # LMS 'duration' of the current item, if LMS believes it has one (trace only)
    waiting: bool = False        # LMS 'waitingToPlay': mode is play but the group controller is BUFFERING / WAITING_TO_SYNC - e.g.
                                 # rebuffering after ANY member's output underrun (Slim::Control::Queries status; trace only)

    @property
    def playing_something(self) -> bool:
        return self.player_connected and self.mode == "play" and self.url is not None


class _Resolver:
    """Bounded, cached name lookup for the LMS host (review H2): ``getaddrinfo`` has no timeout (glibc: about 10 s per
    name server and address family when DNS is down), which used to stall every LMS call. One lookup runs at a time in a
    worker thread; callers wait at most ``timeout`` and fail as unreachable. A result is reused for ``cache_s`` seconds
    and dropped on a connection failure (the LMS host may have a new DHCP address). IPv4 is preferred (home LAN)."""

    def __init__(self, host: str, port: int, resolve=socket.getaddrinfo, timeout: float = 2.0, cache_s: float = 60.0):
        self.host, self.port, self.resolve, self.timeout, self.cache_s = host, port, resolve, timeout, cache_s
        self._lock = threading.Lock()
        self._addr: str | None = None
        self._until = 0.0
        self._pending: threading.Thread | None = None
        self._result: tuple | None = None

    def _work(self) -> None:
        try:
            infos = self.resolve(self.host, self.port, 0, socket.SOCK_STREAM)
            v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
            addrs = v4 or [i[4][0] for i in infos]
            self._result = ("ok", addrs[0]) if addrs else ("err", "no address")
        except (OSError, UnicodeError) as e:
            self._result = ("err", str(e))

    def address(self) -> str:
        with self._lock:
            if self._addr and time.monotonic() < self._until:
                return self._addr
            if self._pending is None or not self._pending.is_alive():
                self._result = None
                self._pending = threading.Thread(target=self._work, daemon=True, name="lms-resolve")
                self._pending.start()
            t = self._pending
        t.join(self.timeout)
        with self._lock:
            res = self._result
            if t.is_alive() or res is None:
                raise LmsUnreachable(f"name lookup for {self.host} timed out")
            if res[0] != "ok":
                raise LmsUnreachable(f"name lookup for {self.host} failed: {res[1]}")
            self._addr, self._until = res[1], time.monotonic() + self.cache_s
            return self._addr

    def forget(self) -> None:
        with self._lock:
            self._addr = None


class LmsClient:
    def __init__(self, base_url: str, timeout: float = 3.0, resolve=socket.getaddrinfo, resolve_timeout: float = 2.0,
                 resolve_cache_s: float = 60.0):
        if not base_url.lower().startswith(("http://", "https://")):     # never file:// or custom schemes (bandit B310)
            raise ValueError("LMS URL must use http or https")
        self.endpoint = base_url.rstrip("/") + "/jsonrpc.js"
        self.timeout = timeout
        u = urllib.parse.urlsplit(self.endpoint)
        self._resolver: _Resolver | None = None
        host = u.hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if u.scheme == "http" and host:              # https keeps the name (certificate check); LMS speaks plain http
                self._resolver = _Resolver(host, u.port or 80, resolve, resolve_timeout, resolve_cache_s)
        self._netloc, self._path = u.netloc, u.path
        from ..core.bounded import BoundedList
        self.commands_sent: list[tuple] = BoundedList(1000)     # audit trail of state-changing commands (tests assert on it)
        self.command_times: list[tuple[float, float]] = BoundedList(1000)   # (wall before, wall after): probe t_lms_command

    def _rpc(self, player: str, *cmd):
        body = json.dumps({"id": 1, "method": "slim.request", "params": [player, list(cmd)]}).encode()
        url, headers = self.endpoint, {"Content-Type": "application/json"}
        if self._resolver is not None:                   # connect to the looked-up address, keep the name in Host
            addr = self._resolver.address()
            host = f"[{addr}]" if ":" in addr else addr
            port = urllib.parse.urlsplit(self.endpoint).port
            url = f"http://{host}{':' + str(port) if port else ''}{self._path}"
            headers["Host"] = self._netloc
        req = urllib.request.Request(url, body, headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # nosec B310 - scheme restricted to http/https in __init__
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            if self._resolver is not None and not isinstance(e, ValueError):
                self._resolver.forget()
            raise LmsUnreachable(str(e)) from e
        return data.get("result", {}) or {}

    # -- read-only ----------------------------------------------------------
    def server_version(self) -> str:
        return str(self._rpc("", "serverstatus", 0, 1).get("version", "?"))

    def observe(self, player: str) -> Observation:
        r = self._rpc(player, "status", "-", 1, "tags:u")
        if not r:
            return Observation(player_known=False, player_connected=False, mode="stop")
        loop = r.get("playlist_loop") or []
        return Observation(
            player_connected=bool(r.get("player_connected", 0)),
            mode=str(r.get("mode", "stop")),
            power=bool(r.get("power", 1)),
            url=loop[0].get("url") if loop else None,
            volume=int(r["mixer volume"]) if "mixer volume" in r else None,
            title=(loop[0].get("title") if loop else None),
            time=_num(r.get("time")),
            duration=_num(r.get("duration")),
            waiting=bool(r.get("waitingToPlay", 0)),
        )

    def group_status(self, player: str) -> list[dict]:
        """Read-only diagnostics for the stream trace only: every member of the player's sync group (the player alone when it is
        not synced), each as {name, connected, signal, master}. ``signal`` is LMS's ``signalstrength`` (Wi-Fi %, 0 = wired or not
        reported); ``master`` marks the sync master (Lyrion takes the elapsed ``time`` and the stream restart position from it).
        One ``status`` request per member; no ids or addresses are returned."""
        def one(pid, master_id=""):
            r = self._rpc(pid, "status", "-", 1)
            return r, {"name": str(r.get("player_name", "?")), "connected": bool(r.get("player_connected", 0)),
                       "signal": int(_num(r.get("signalstrength")) or 0), "master": bool(master_id) and pid == master_id}
        first, me = one(player)
        if not first:
            return []
        master_id = str(first.get("sync_master", ""))
        me["master"] = bool(master_id) and player == master_id
        ids = [x for x in [master_id] + str(first.get("sync_slaves", "")).split(",") if x]
        out = [me]
        for pid in dict.fromkeys(ids):
            if pid != player:
                r, m = one(pid, master_id)
                if r:
                    out.append(m)
        return out

    def players(self) -> list[dict]:
        """All players known to LMS (read-only): id, name, model, connected, power."""
        r = self._rpc("", "players", 0, 200)
        out = []
        for p in r.get("players_loop") or []:
            if not int(p.get("isplayer", 1)):
                continue
            out.append({"id": str(p.get("playerid", "")), "name": str(p.get("name", "")), "model": str(p.get("modelname") or p.get("model", "")),
                        "connected": bool(int(p.get("connected", 0))), "power": bool(int(p.get("power", 0)))})
        return out

    def sync_groups(self) -> list[dict]:
        """Existing sync groups (read-only; the bridge never creates or changes groups): [{members: [id..], names: [..]}]."""
        r = self._rpc("", "syncgroups", "?")
        out = []
        for g in r.get("syncgroups_loop") or []:
            ids = [x for x in str(g.get("sync_members", "")).split(",") if x]
            names = [x for x in str(g.get("sync_member_names", "")).split(",") if x]
            if ids:
                out.append({"members": ids, "names": names})
        return out

    def targets(self) -> list[dict]:
        """Selectable targets for the setup wizard: every player, plus each existing sync group as ONE target addressed via
        its first member (LMS plays to the whole group when any member is addressed - verified in the lab, phase 1)."""
        players = self.players()
        by_id = {p["id"]: p for p in players}
        out = []
        for g in self.sync_groups():
            names = [by_id.get(m, {}).get("name") or n for m, n in zip(g["members"], g["names"] + [""] * len(g["members"]))]
            out.append({"id": g["members"][0], "kind": "group", "name": " + ".join(n for n in names if n), "members": g["members"],
                        "connected": any(by_id.get(m, {}).get("connected") for m in g["members"])})
        for p in players:
            out.append({"id": p["id"], "kind": "player", "name": p["name"], "model": p["model"], "connected": p["connected"]})
        return out

    # -- state-changing (audited) -------------------------------------------
    def _audit(self, *what):
        self.commands_sent.append(what)

    def _timed(self, *cmd_args):
        t0 = time.time()
        try:
            return self._rpc(*cmd_args)
        finally:
            self.command_times.append((t0, time.time()))

    def power_on(self, player: str) -> None:
        self._audit("power_on", player)
        self._timed(player, "power", 1)

    def play(self, player: str, url: str, title: str = "Bluetooth") -> None:
        self._audit("play", player, url)
        self._timed(player, "playlist", "play", url, title)

    def release(self, player: str, url: str) -> None:
        self._audit("release", player, url)
        self._timed(player, "playlist", "deleteitem", url)

    def volume_delta(self, player: str, delta: int) -> None:
        self._audit("volume_delta", player, delta)
        self._timed(player, "mixer", "volume", f"{delta:+d}")
