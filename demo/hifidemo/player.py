"""A demo room: a real Squeezelite player (software, no sound card) whose PCM output feeds the audio monitor.

  python3 -m hifidemo.player --name Wohnzimmer --mac 02:00:00:00:01:01 --server lms:3483 --monitor-port 9100

The player is SIMULATED hardware (a software player in Docker); what it plays comes from the real Lyrion Media Server.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from . import monitor as MON


def squeezelite_argv(name: str, mac: str, server: str, codecs: str = "") -> list[str]:
    argv = ["squeezelite", "-n", name, "-m", mac, "-s", server, "-o", "-", "-a", "16", "-r", "44100", "-d", "all=warn"]
    if codecs:
        argv += ["-c", codecs]
    return argv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SIMULATED room: Squeezelite + virtual DAC + monitor tap")
    ap.add_argument("--name", required=True)
    ap.add_argument("--mac", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--monitor-port", type=int, default=9100)
    ap.add_argument("--codecs", default="")
    a = ap.parse_args(argv)
    b = MON.Broadcaster()
    MON.serve(b, a.monitor_port, info={"player": a.name, "mac": a.mac})
    cmd = squeezelite_argv(a.name, a.mac, a.server, a.codecs)
    print(f"[{a.name}] SIMULATED room: {' '.join(cmd)} -> virtual DAC -> monitor :{a.monitor_port}", flush=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        MON.run_dac(p.stdout.fileno(), MON.DacTap(b))
    finally:
        p.terminate()
    return p.wait()


if __name__ == "__main__":
    sys.exit(main())
