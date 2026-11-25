"""LAB ONLY (EXPERIMENT helper, never product code): a userspace TCP relay in front of ONE lab Squeezelite so an experiment can
stall only that player's **audio data connection** (LMS HTTP port 9000) while its Slimproto control connection (3483) keeps
working. Stand-in for a sync-group member whose Wi-Fi / data path stops delivering while it stays "connected" in LMS.

It needs no NET_ADMIN / iptables: Squeezelite is started with ``-s 127.0.0.1:3483``; LMS's ``strm`` carries server IP 0 for its
own streams, so the player opens the data connection to the same address, i.e. this relay, too. While the flag file exists the
relay stops reading the LMS side of every data connection (LMS's send queue fills, the player gets nothing); removing the flag
resumes it. Run:  python3 -m btkit.stall_relay <lms_host> <flag_file>
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

PORTS = {3483: False, 9000: True}            # listen port -> gated (only the audio data connection is ever stalled)


def _pipe(src: socket.socket, dst: socket.socket, flag: str | None) -> None:
    try:
        while True:
            if flag and os.path.exists(flag):
                time.sleep(0.02)
                continue
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve(port: int, upstream: str, gated: bool, flag: str) -> None:
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", port))
    ls.listen(8)
    while True:
        c, _ = ls.accept()
        try:
            u = socket.create_connection((upstream, port), timeout=5)
            u.settimeout(None)
        except OSError:
            c.close()
            continue
        for s in (c, u):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=_pipe, args=(c, u, None), daemon=True).start()                    # player -> LMS: never gated
        threading.Thread(target=_pipe, args=(u, c, flag if gated else None), daemon=True).start()  # LMS -> player


def main(argv=None) -> int:
    upstream, flag = (argv or sys.argv[1:])[:2]
    for port, gated in PORTS.items():
        threading.Thread(target=_serve, args=(port, upstream, gated, flag), daemon=True).start()
    print(f"stall_relay: 127.0.0.1:{list(PORTS)} -> {upstream} (data gated by {flag})", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
