"""Daemon entry point (lab/demo): ``python -m btbridge --lms http://lms:9000 --player MAC --source mod:Class``."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time

from .app import BridgeRuntime, RuntimeConfig
from .core.controller import Config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btbridge")
    ap.add_argument("--lms", default="http://lms:9000")
    ap.add_argument("--player", required=True, help="LMS player id (MAC) = the one configured target")
    ap.add_argument("--advertise-host", default="bridge")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--format", choices=("wav", "flac"), default="wav")
    ap.add_argument("--lead-in-ms", type=int, default=1000)
    ap.add_argument("--pause-strategy", choices=("drop_restart", "keepalive_silence"), default="drop_restart")
    ap.add_argument("--no-override", action="store_true")
    ap.add_argument("--icy", action="store_true")
    ap.add_argument("--source", help="module:Class implementing AudioSourceAdapter (e.g. btkit.sim_a2dp:SimulatedA2dpSource)")
    ap.add_argument("--status-every", type=float, default=0)
    a = ap.parse_args(argv)
    rc = RuntimeConfig(lms_url=a.lms, advertise_host=a.advertise_host, stream_port=a.port, stream_format=a.format,
                       lead_in_ms=a.lead_in_ms, icy_metadata=a.icy)
    cc = Config(target_player=a.player, override_lms=not a.no_override, pause_strategy=a.pause_strategy)
    src = None
    if a.source:
        mod, cls = a.source.split(":")
        src = getattr(importlib.import_module(mod), cls)()
    rt = BridgeRuntime(rc, cc, source=src)
    rt.start()
    print(f"bridge up, stream port {rt.port}, target {a.player}", flush=True)
    try:
        while True:
            time.sleep(a.status_every or 3600)
            if a.status_every:
                print(json.dumps(rt.status()), flush=True)
    except KeyboardInterrupt:
        rt.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
