"""LAB ONLY: run the real appliance (service + WebUI) with the SIMULATED phone and a tiny control API for that phone, so browser and
manual tests can 'press Play on the phone'.  The control API exists only here (testkit), never in the product package.

  python3 -m btkit.appliance_lab --data /data --http-port 8080 --sim-port 8099 [appliance options...]
  curl http://appliance:8099/connect | /play | /pause | /notify | /disconnect | /status

``--sim-rauc`` runs the SIMULATED RAUC D-Bus service (btkit.fake_rauc), so the update pages are managed.
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


def start_sim_rauc() -> list[str]:
    """The SIMULATED RAUC service (btkit.fake_rauc) on a private bus: booted slot A, an earlier release 0.2.0 in slot B, any uploaded
    file counts as a signed bundle of 0.4.0 and installs in ~5 s. Returns the appliance options that use it. No restart helper exists
    in the lab, so "Restart now" reports that it is unavailable."""
    import json
    import tempfile

    from .fake_bluez import PrivateBus
    from .fake_rauc import FakeRauc
    bus = PrivateBus()
    rauc = FakeRauc(bus.address)
    rauc.accept_any = True
    rauc.install_plan = {"result": 0, "steps": 12, "step_s": 0.4}
    rauc.slots["rootfs.1"]["bundle.version"] = "0.2.0"
    rauc.boot_status["rootfs.1"] = "good"
    rauc.start()
    fd, trust = tempfile.mkstemp(prefix="hifi-lab-update-trust-", suffix=".json")
    with open(fd, "w") as fh:
        json.dump({"format": "hifi-bridge-update-trust/1", "mode": "own", "keyring": "/etc/rauc/keyring.pem", "official_source": None,
                   "certificates": [{"role": "own", "subject": "CN=Hi-Fi Bridge lab update CA (SIMULATED)",
                                     "sha256": "4C:41:42:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00"}]}, fh)
    print(f"SIMULATED RAUC on {bus.address}", flush=True)
    return ["--updates", "rauc", "--dbus", bus.address, "--update-trust", trust]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--sim-rauc" in argv:
        argv.remove("--sim-rauc")
        argv += start_sim_rauc()
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
