"""Manual demo: bridge + SIMULATED phone with a tiny HTTP control API (lab only).
  curl http://demo:8099/connect | /play | /pause | /notify | /disconnect | /volume?v=100 | /status | /diag
Target player: env DEMO_PLAYER (default player1)."""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .lab import make_bridge
from .lms_rpc import PLAYERS


def main():
    rt, sim = make_bridge(player=os.environ.get("DEMO_PLAYER", "player1"), fmt=os.environ.get("DEMO_FORMAT", "wav"))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query); r = u.path.strip("/")
            res = {"connect": lambda: sim.connect(), "play": sim.play, "pause": sim.pause, "disconnect": sim.disconnect,
                   "notify": lambda: sim.notification(0.8), "volume": lambda: sim.set_volume(int(q.get("v", ["64"])[0])),
                   "diag": rt.diagnostic_test, "status": lambda: None}.get(r)
            if not res:
                self.send_error(404); return
            out = res()
            body = json.dumps({"result": out, "status": rt.status()}, default=str).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    print("demo control on :8099 (lab network only)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8099), H).serve_forever()


if __name__ == "__main__":
    main()
