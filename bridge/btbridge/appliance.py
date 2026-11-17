"""Run the appliance: service (bridge + config + monitoring + MQTT) and the owner WebUI.

    python -m btbridge.appliance --data /var/lib/btbridge --source bluez            (target device)
    python -m btbridge.appliance --data /data --source sim --thermal-path ""        (Docker lab / development)

``--source bluez`` connects to the system D-Bus and is meant for the target device only; it exits with an error when no BlueZ/D-Bus is
available (never pretends).  ``--source sim`` uses the SIMULATED phone from the testkit (lab only).
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import threading
from typing import Callable

from .mqtt import MqttBridge
from .net.access import LocalAccess
from .service import ApplianceService
from .supervision import StopSignal, SystemdNotifier, Watchdog
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
    ap.add_argument("--http-host", default="0.0.0.0")  # nosec B104 - LAN appliance: owner UI/players must reach it; protected by Host validation, auth and the stream token
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--https-port", type=int, default=8443, help="HTTPS listener (local CA or custom certificate); 0 = disabled")
    ap.add_argument("--no-mdns", action="store_true", help="do not announce the name via mDNS at all")
    ap.add_argument("--boot-dir", help="boot partition (appliance): processes a 'hifi-recovery' request and writes 'hifi-status.txt'")
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


def first_start_hint(auth) -> str | None:
    """Where the owner finds the one-time setup code: the generated development file, or (provisioned) the flash tool's owner sheet."""
    if auth.has_password():
        return None
    if os.path.isfile(auth.code_file):
        return f"first start: open the WebUI and enter the setup code from {auth.code_file}"
    return "first start: open the WebUI and enter the setup code from the owner sheet of the flash tool (provisioned device)"


def run(a, source, ready: Callable[[ApplianceService, WebUI], None] | None = None, done: threading.Event | None = None) -> int:
    """Start everything and block until SIGINT/SIGTERM (or until ``done`` is set - tests)."""
    if getattr(a, "boot_dir", None):                          # test Pi without the image's hifi-bridge-boot.service
        from .bootfiles import run as bootfiles
        try:
            r = bootfiles(a.data, a.boot_dir, log=lambda m: print(m, flush=True))
            print(f"boot files: recovery={r['recovery']} provisioning={r['provisioning']['result']}", flush=True)
        except OSError as e:                                  # e.g. a root-owned /boot/firmware on stock Pi OS: report, keep running
            print(f"boot files NOT processed ({type(e).__name__}: {e}) - the bridge starts without them", file=sys.stderr, flush=True)
    svc = ApplianceService(a.data, source=source, stream_port=a.stream_port, advertise_host=a.advertise_host,
                           monitor=SystemMonitor(proc=a.proc_root, thermal=a.thermal_path or None, data_dir=a.data),
                           mqtt_factory=None if a.no_mqtt else MqttBridge)
    if a.no_mdns:
        svc.store.update({"mdns_enabled": False})
    svc.access = LocalAccess(svc.store, a.data, svc.instance_id(), a.http_port, a.https_port or 0, log=svc.logs)
    svc.access.start()
    ca = svc.access.certs.ca_info()
    if ca:                                   # out-of-band reference for the trust step (docs/LOCAL_ACCESS_TLS.md 4.2)
        svc.logs(f"local CA fingerprint (SHA-256): {ca['sha256']}")
    svc.start()
    ui = WebUI(svc, a.http_host, a.http_port, tls_port=a.https_port or None)
    port = ui.start()
    svc.logs(f"WebUI on http://{a.http_host}:{port}/" + (f" and https://{svc.access.main_name()}:{ui.tls_port}/" if ui.tls_port and
                                                          ui._httpsd else ""))
    hint = first_start_hint(svc.auth)
    if hint:
        svc.logs(hint)
    if ready:
        ready(svc, ui)
    watchdog = Watchdog(svc.health, SystemdNotifier(), log=svc.logs)       # READY=1 + WATCHDOG=1 while healthy (no-op without systemd)
    watchdog.start()
    if done is None:
        StopSignal().wait()                                   # async-signal-safe (no lock in the handler)
    else:
        done.wait()
    watchdog.stop()
    ui.stop(); svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
