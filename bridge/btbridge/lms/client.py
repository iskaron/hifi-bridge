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

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LmsUnreachable(Exception):
    pass


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

    @property
    def playing_something(self) -> bool:
        return self.player_connected and self.mode == "play" and self.url is not None


class LmsClient:
    def __init__(self, base_url: str, timeout: float = 3.0):
        self.endpoint = base_url.rstrip("/") + "/jsonrpc.js"
        self.timeout = timeout
        self.commands_sent: list[tuple] = []     # audit trail of state-changing commands (tests assert on it)
        self.command_times: list[tuple[float, float]] = []   # (wall before, wall after) per command: timing probe t_lms_command

    def _rpc(self, player: str, *cmd):
        body = json.dumps({"id": 1, "method": "slim.request", "params": [player, list(cmd)]}).encode()
        req = urllib.request.Request(self.endpoint, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
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
        )

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
