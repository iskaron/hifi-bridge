"""LAB ONLY: run the real appliance (service + WebUI) with the SIMULATED phone and a tiny control API for that phone, so browser and
manual tests can 'press Play on the phone'.  The control API exists only here (testkit), never in the product package.

  python3 -m btkit.appliance_lab --data /data --http-port 8080 --sim-port 8099 [appliance options...]
  curl http://appliance:8099/connect | /play | /pause | /notify | /disconnect | /status
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from btbridge import appliance

from .sim_a2dp import SimulatedA2dpSource


def control(sim: SimulatedA2dpSource, svc, port: int) -> None:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            act = {"connect": sim.connect, "play": sim.play, "pause": sim.pause, "disconnect": sim.disconnect,
                   "notify": lambda: sim.notification(0.8), "status": lambda: None,
                   "metadata": lambda: sim.set_metadata("Lab Song", "Lab Band", "Lab Album")}.get(self.path.strip("/"))
            if act is None:
                self.send_error(404)
                return
            act()
            body = json.dumps({"status": svc.status()}, default=str).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sim_port = 8099
    if "--sim-port" in argv:
        i = argv.index("--sim-port"); sim_port = int(argv[i + 1]); del argv[i:i + 2]
    a = appliance.parser().parse_args(argv)
    sim = SimulatedA2dpSource()
    return appliance.run(a, sim, ready=lambda svc, ui: control(sim, svc, sim_port))


if __name__ == "__main__":
    sys.exit(main())
