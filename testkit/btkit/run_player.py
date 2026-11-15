"""Run a headless Squeezelite whose decoded PCM output is tapped and recorded."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .pcm_tap import PcmTap, paced_read_loop, set_pipe_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--mac", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--codecs", default="")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[])
    a = ap.parse_args()
    os.makedirs(a.capture, exist_ok=True)
    cmd = ["squeezelite", "-n", a.name, "-m", a.mac, "-s", a.server, "-o", "-", "-a", "16", "-r", "44100",
           "-f", os.path.join(a.capture, "squeezelite.log"), "-d", "all=info", "-d", "slimproto=debug"]
    if a.codecs:
        cmd += ["-c", a.codecs]
    cmd += a.extra
    print("exec:", " ".join(cmd), flush=True)
    tap = PcmTap(a.capture)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    fd = p.stdout.fileno()
    set_pipe_size(fd)
    try:
        paced_read_loop(fd, tap)
    finally:
        p.terminate()
    return p.wait()


if __name__ == "__main__":
    sys.exit(main())
