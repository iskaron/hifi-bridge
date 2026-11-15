"""Run the appliance: service (bridge + config + monitoring + MQTT) and the owner WebUI.

    python -m btbridge.appliance --data /var/lib/btbridge --source bluez            (target device)
    python -m btbridge.appliance --data /data --source sim --thermal-path ""        (Docker lab / development)

``--source bluez`` connects to the system D-Bus and is meant for the target device only; it exits with an error when no BlueZ/D-Bus is
available (never pretends).  ``--source sim`` uses the SIMULATED phone from the testkit (lab only).
"""
from __future__ import annotations

import argparse
import importlib
import signal
import sys
import threading
from typing import Callable

from .mqtt import MqttBridge
from .service import ApplianceService
from .system import SystemMonitor
from .webui.server import WebUI


def build_source(spec: str, dbus: str):
    if spec == "none":
        return None
    if spec == "sim":
        from btkit.sim_a2dp import SimulatedA2dpSource           # lab only (testkit on PYTHONPATH)
        return SimulatedA2dpSource()
    if spec == "bluez":
        from .adapters.bluez.factory import create_adapter
        return create_adapter(bus=dbus)
    mod, cls = spec.split(":")
    return getattr(importlib.import_module(mod), cls)()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="btbridge.appliance")
    ap.add_argument("--data", required=True, help="persistent data directory (settings, secrets, faults)")
    ap.add_argument("--http-host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--tls-cert"); ap.add_argument("--tls-key")
    ap.add_argument("--stream-port", type=int, default=8090)
    ap.add_argument("--advertise-host", default="auto", help="address LMS/players use to reach the stream (auto = route to the LMS)")
    ap.add_argument("--source", default="bluez", help="bluez | sim | none | module:Class")
    ap.add_argument("--dbus", default="SYSTEM")
    ap.add_argument("--proc-root", default="/proc")
    ap.add_argument("--thermal-path", default="/sys/class/thermal/thermal_zone0/temp", help='"" disables temperature reading')
    ap.add_argument("--no-mqtt", action="store_true")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        source = build_source(a.source, a.dbus)
    except Exception as e:                                       # BusError, missing jeepney, ...
        print(f"cannot start source {a.source!r}: {e}", file=sys.stderr)
        return 2
    return run(a, source)


def run(a, source, ready: Callable[[ApplianceService, WebUI], None] | None = None) -> int:
    svc = ApplianceService(a.data, source=source, stream_port=a.stream_port, advertise_host=a.advertise_host,
                           monitor=SystemMonitor(proc=a.proc_root, thermal=a.thermal_path or None, data_dir=a.data),
                           mqtt_factory=None if a.no_mqtt else MqttBridge)
    svc.start()
    tls = (a.tls_cert, a.tls_key) if a.tls_cert and a.tls_key else None
    ui = WebUI(svc, a.http_host, a.http_port, tls=tls)
    port = ui.start()
    svc.logs(f"WebUI on {'https' if tls else 'http'}://{a.http_host}:{port}/")
    if not svc.auth.has_password():
        svc.logs(f"first start: open the WebUI and enter the setup code from {svc.auth.code_file}")
    if ready:
        ready(svc, ui)
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    done.wait()
    ui.stop(); svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
