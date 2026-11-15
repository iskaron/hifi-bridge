"""Service supervision (DECISIONS #39; docs/SERVICE_SUPERVISION.md).

* ``SystemdNotifier``: the sd_notify datagram protocol (stdlib): READY=1, STATUS=..., WATCHDOG=1, STOPPING=1. No-op when NOTIFY_SOCKET
  is not set (e.g. Docker lab, or an init system without systemd).
* ``Watchdog``: pings WATCHDOG=1 at half the configured WatchdogSec **only while ``service.health()`` is not 'failed'**. A dead or hung
  internal loop therefore makes systemd kill and restart the process (Restart=on-failure / on-watchdog).
* ``SessionJournal``: remembers the stream URL the bridge currently owns at LMS. After a crash/kill the next start releases exactly that
  URL (``playlist deleteitem <url>``, URL-addressed - a no-op if anything else plays). Without it a dead bridge would leave its item in
  the player's playlist.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Callable


class SystemdNotifier:
    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.addr = env.get("NOTIFY_SOCKET")
        usec = env.get("WATCHDOG_USEC")
        pid = env.get("WATCHDOG_PID")
        self.watchdog_s = int(usec) / 1e6 if usec and (not pid or int(pid) == os.getpid()) else None

    @property
    def enabled(self) -> bool:
        return bool(self.addr)

    def notify(self, **fields) -> bool:
        if not self.addr:
            return False
        msg = "\n".join(f"{k.upper()}={v}" for k, v in fields.items()).encode()
        addr = "\0" + self.addr[1:] if self.addr.startswith("@") else self.addr       # abstract namespace
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                s.connect(addr)
                s.sendall(msg)
            return True
        except OSError:
            return False


class Watchdog:
    def __init__(self, health: Callable[[], dict], notifier: SystemdNotifier, log: Callable[[str], None] = lambda m: None,
                 interval_s: float | None = None):
        self.health, self.n, self.log = health, notifier, log
        self.interval = interval_s or (notifier.watchdog_s / 2 if notifier.watchdog_s else None)
        self._stop = threading.Event()
        self.pings = 0
        self.withheld = 0

    def start(self) -> None:
        self.n.notify(ready=1, status="running")
        if self.interval:
            threading.Thread(target=self._run, daemon=True, name="watchdog").start()

    def _run(self) -> None:
        last = None
        while not self._stop.wait(self.interval):
            try:
                h = self.health()
            except Exception as e:                       # a crashing health check is a failure, not a pass
                h = {"status": "failed", "checks": {"health": False}, "error": f"{type(e).__name__}: {e}"}
            if h["status"] == "failed":
                self.withheld += 1
                bad = [k for k, v in h.get("checks", {}).items() if not v]
                if last != "failed":
                    self.log(f"watchdog: health failed ({', '.join(bad)}); not pinging - the supervisor will restart the service")
                self.n.notify(status=f"failed: {', '.join(bad)}")
            else:
                self.n.notify(watchdog=1, status=h["status"])
                self.pings += 1
            last = h["status"]

    def stop(self) -> None:
        self._stop.set()
        self.n.notify(stopping=1)


class SessionJournal:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "session.json")
        self._lock = threading.Lock()

    def record(self, player: str, url: str) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"player": player, "url": url, "t": time.time()}, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)

    def clear(self) -> None:
        with self._lock:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass

    def pending(self) -> dict | None:
        try:
            with open(self.path) as fh:
                d = json.load(fh)
            return d if d.get("player") and d.get("url") else None
        except (OSError, ValueError):
            return None
