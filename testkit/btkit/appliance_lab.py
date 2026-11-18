"""LAB ONLY: run the real appliance (service + WebUI) with the SIMULATED phone and a tiny control API for that phone, so browser and
manual tests can 'press Play on the phone'.  The control API exists only here (testkit), never in the product package.

  python3 -m btkit.appliance_lab --data /data --http-port 8080 --sim-port 8099 [appliance options...]
  curl http://appliance:8099/connect | /play | /pause | /notify | /disconnect | /status

``--sim-network [PORT]`` also runs the REAL network agent with the SIMULATED radio (btkit.sim_net) on a socket in /tmp, so the network
pages are managed; its control API (``/ethernet/on|off``, ``/stations/<n>``) listens on PORT (default 8098).
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


def start_sim_network(data: str, port: int) -> str:
    import os

    from btbridge.net.netagent import Agent

    from .sim_net import SimulatedNetworkSystem, _control_server
    run = "/tmp/hifi-net"
    os.makedirs(run, mode=0o700, exist_ok=True)
    os.makedirs(data, mode=0o700, exist_ok=True)
    system = SimulatedNetworkSystem()
    agent = Agent(system, data, os.getuid(), run, log=lambda m: print(m, flush=True))
    agent.start()
    path = agent.bind(None)
    threading.Thread(target=agent.serve, daemon=True, name="sim-net-serve").start()
    threading.Thread(target=agent.loop, daemon=True, name="sim-net-loop").start()
    _control_server(system, port)
    print(f"SIMULATED network agent on {path}, control on :{port}", flush=True)
    return path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sim_port = 8099
    if "--sim-port" in argv:
        i = argv.index("--sim-port"); sim_port = int(argv[i + 1]); del argv[i:i + 2]
    if "--sim-network" in argv:
        i = argv.index("--sim-network"); del argv[i]
        port = 8098
        if i < len(argv) and argv[i].isdigit():
            port = int(argv.pop(i))
        data = argv[argv.index("--data") + 1]
        argv += ["--net-agent", start_sim_network(data, port)]
    a = appliance.parser().parse_args(argv)
    sim = SimulatedA2dpSource()
    return appliance.run(a, sim, ready=lambda svc, ui: control(sim, svc, sim_port))


if __name__ == "__main__":
    sys.exit(main())
