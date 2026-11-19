"""Entry point of the privileged helper (``hififlash-helper`` in the packages; ``python3 -m hififlash.helper`` in development).

    hififlash-helper --stdio                                   Linux (pkexec keeps stdin/stdout)
    hififlash-helper --connect PORT --token-file PATH          macOS/Windows: connect back to the GUI on 127.0.0.1:PORT, prove the token
    ... --simulate DIR                                         SIMULATED cards only (tests, package smoke test)

It never takes a disk, image or option from the command line: everything comes in the single validated request (protocol.py).
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys

from .. import __version__
from ..platforms import adapter_for
from . import protocol as PR
from .server import serve

TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def read_token(path: str) -> str:
    with open(path) as fh:
        token = fh.read(200).strip()
    if not TOKEN_RE.match(token):
        raise ValueError("invalid token file")
    return token


def main(argv=None, stdin=None, stdout=None, platform: str = sys.platform, connect=socket.create_connection) -> int:
    ap = argparse.ArgumentParser(prog="hififlash-helper")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdio", action="store_true")
    mode.add_argument("--connect", type=int, metavar="PORT")
    mode.add_argument("--version", action="store_true")
    ap.add_argument("--token-file")
    ap.add_argument("--simulate", metavar="DIR")
    a = ap.parse_args(argv)
    if a.version:
        print(f"hififlash-helper {__version__} protocol {PR.VERSION}")
        return 0
    if a.simulate and hasattr(os, "geteuid") and os.geteuid() == 0:
        ap.error("--simulate is for tests and package checks only and never runs elevated")
    adapter, plat = adapter_for(platform, a.simulate)
    if a.stdio:
        return serve(stdin if stdin is not None else sys.stdin.buffer, stdout if stdout is not None else sys.stdout.buffer, adapter, plat)
    if not a.token_file or not (0 < a.connect < 65536):
        ap.error("--connect needs a valid port and --token-file")
    token = read_token(a.token_file)
    sock = connect(("127.0.0.1", a.connect), timeout=30)
    sock.settimeout(None)
    with sock, sock.makefile("rb") as rfile, sock.makefile("wb") as wfile:
        wfile.write(PR.encode({"hello": token}))
        wfile.flush()
        return serve(rfile, wfile, adapter, plat)


def run_and_exit() -> None:
    """Process entry (also of the packaged executable). After the session, the cancel watcher may still block in a read of stdin;
    a normal interpreter shutdown then aborts ("could not acquire lock for stdin", found by the package smoke test). The result has been
    reported and flushed at that point, so the process ends without finalisation."""
    rc = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    run_and_exit()
