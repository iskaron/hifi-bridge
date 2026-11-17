"""Demo helpers that act on Lyrion the way an owner would in the Lyrion web UI: start the demo radio on a room, stop it, build or dissolve
a synchronised group. They never touch the bridge; the bridge sees these changes through Lyrion like any other source."""
from __future__ import annotations

import json
import urllib.request

RADIO_TITLE = "Demo Radio (simulated)"


class Lms:
    def __init__(self, base: str = "http://lms:9000", transport=None):
        self.url = base.rstrip("/") + "/jsonrpc.js"
        self.transport = transport or self._http

    def _http(self, body: dict) -> dict:
        req = urllib.request.Request(self.url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:  # nosec B310 - fixed http URL of the demo's own LMS (internal network)
            return json.loads(r.read())

    def req(self, player: str, *cmd) -> dict:
        return self.transport({"id": 1, "method": "slim.request", "params": [player, [str(c) for c in cmd]]}).get("result") or {}

    def players(self) -> list[dict]:
        res = self.req("", "players", 0, 50)
        out = []
        for p in res.get("players_loop", []):
            st = self.req(p["playerid"], "status", "-", 1, "tags:a")
            loop = st.get("playlist_loop") or [{}]
            out.append({"id": p["playerid"], "name": p.get("name", ""), "connected": bool(p.get("connected")),
                        "mode": st.get("mode", "stop"), "title": loop[0].get("title", st.get("current_title", "")),
                        "volume": st.get("mixer volume"), "sync_master": st.get("sync_master"),
                        "sync_slaves": [s for s in str(st.get("sync_slaves", "")).split(",") if s]})
        return out

    def play_radio(self, player_id: str, url: str) -> None:
        self.req(player_id, "playlist", "play", url, RADIO_TITLE)

    def stop(self, player_id: str) -> None:
        self.req(player_id, "stop")

    def sync(self, player_ids: list[str]) -> None:
        """First id = the group's master; the others join it (Lyrion 'sync' command)."""
        master, *others = player_ids
        for p in others:
            self.req(master, "sync", p)

    def unsync(self, player_ids: list[str]) -> None:
        for p in player_ids:
            self.req(p, "sync", "-")
