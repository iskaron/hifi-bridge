"""Minimal independent LMS JSON-RPC client for tests (separate from the bridge's own)."""
from __future__ import annotations

import json
import urllib.request

PLAYERS = {"player1": "02:00:00:00:00:01", "player2": "02:00:00:00:00:02",
           "player3": "02:00:00:00:00:03", "legacy1": "02:00:00:00:00:10"}


class Lms:
    def __init__(self, base="http://lms:9000"):
        self.url = base.rstrip("/") + "/jsonrpc.js"

    def req(self, player: str, *cmd, timeout=10):
        body = json.dumps({"id": 1, "method": "slim.request", "params": [player, list(cmd)]}).encode()
        r = urllib.request.urlopen(urllib.request.Request(self.url, body, {"Content-Type": "application/json"}), timeout=timeout)
        return json.loads(r.read()).get("result", {})
