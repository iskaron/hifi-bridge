"""GUI side of the privilege separation: start the helper elevated, speak the protocol, relay progress and cancel.

Channels (how the GUI reaches the helper):
  * Linux   ``pkexec <helper> --stdio`` - polkit asks for an administrator password; stdin/stdout stay connected (``stdio``).
  * macOS   ``osascript -e 'do shell script "<helper> --connect P --token-file F" with administrator privileges'`` (``connect_back``).
  * Windows ``ShellExecuteExW(verb="runas")`` = UAC prompt; the helper's manifest also says requireAdministrator (``connect_back``).
  * Tests / ``--simulate``: the real server in a thread over pipes with SIMULATED cards (``inprocess``).

Connect-back: the GUI listens on 127.0.0.1 (random port) and writes a fresh 256-bit token into a file in a private temporary directory
(0700/0600); the helper (root/admin can read it) connects and sends the token first. Any other local connection is dropped. The token never
appears on a command line; only the file's path does.
"""
from __future__ import annotations

import hmac
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import protocol as PR


class HelperFailed(Exception):
    """``code``: a helper error code (protocol.ERROR_CODES) or elevation_refused | helper_died | protocol."""

    def __init__(self, code: str, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.code, self.message, self.reasons = code, message, list(reasons or [])


@dataclass
class Channel:
    rfile: object
    wfile: object
    closer: Callable[[], None]

    def send(self, obj: dict) -> None:
        self.wfile.write(PR.encode(obj))
        self.wfile.flush()

    def close(self) -> None:
        self.closer()


def run_session(channel: Channel, request: dict, progress: Callable[[str, int, int], None] = lambda *a: None,
                cancel: threading.Event | None = None) -> dict:
    cancel = cancel or threading.Event()
    finished = threading.Event()
    try:
        try:
            first = PR.read_message(channel.rfile)
        except PR.ProtocolError as e:
            raise HelperFailed("protocol", f"unexpected answer from the helper: {e}") from None
        if first is None:
            raise HelperFailed("elevation_refused", "the write helper did not start (administrator authorisation refused, or the helper "
                                                    "is missing)")
        if first.get("event") != "ready" or first.get("v") != PR.VERSION:
            raise HelperFailed("protocol", "the write helper speaks another protocol version")
        channel.send(request)

        def watch():
            while not finished.is_set():
                if cancel.wait(0.05):
                    try:
                        channel.send({"op": "cancel"})
                    except (OSError, ValueError):
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()
        while True:
            try:
                msg = PR.read_message(channel.rfile)
            except PR.ProtocolError as e:
                raise HelperFailed("protocol", f"unexpected answer from the helper: {e}") from None
            if msg is None:
                raise HelperFailed("helper_died", "the write helper ended unexpectedly - do not use the card before writing it again")
            ev = msg.get("event")
            if ev == "progress":
                progress(str(msg.get("stage")), int(msg.get("done", 0)), int(msg.get("total", 0)))
            elif ev == "done":
                return dict(msg.get("result") or {})
            elif ev == "error":
                raise HelperFailed(str(msg.get("code")), str(msg.get("message")), msg.get("reasons") or [])
    finally:
        finished.set()
        channel.close()


# ------------------------------------------------------------------ channels
def inprocess(adapter, platform: str = "simulated") -> Channel:
    from .server import serve
    a_r, a_w = os.pipe()
    b_r, b_w = os.pipe()
    helper_in, gui_out = os.fdopen(a_r, "rb"), os.fdopen(a_w, "wb")
    gui_in, helper_out = os.fdopen(b_r, "rb"), os.fdopen(b_w, "wb")

    def target():
        try:
            serve(helper_in, helper_out, adapter, platform)
        finally:
            helper_out.close()
    t = threading.Thread(target=target, daemon=True)
    t.start()

    def closer():
        gui_out.close()                      # EOF for the helper: cancels a write that is still running
        t.join(timeout=30)
        gui_in.close()
        helper_in.close()
    return Channel(gui_in, gui_out, closer)


def stdio(argv: list[str], popen=subprocess.Popen) -> Channel:
    p = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def closer():
        p.stdin.close()
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
        p.stdout.close()
    return Channel(p.stdout, p.stdin, closer)


def connect_back(launch: Callable[[int, str], object], timeout: float = 180, clock=time.monotonic) -> Channel:
    token = secrets.token_hex(32)
    d = tempfile.mkdtemp(prefix="hififlash-")
    tf = os.path.join(d, "token")
    fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        try:
            proc = launch(srv.getsockname()[1], tf)
        except OSError as e:
            raise HelperFailed("elevation_refused", f"administrator authorisation was refused or failed: {e}") from None
        conn, rfile = _accept_with_token(srv, token, timeout, clock, proc)
    finally:
        srv.close()
        shutil.rmtree(d, ignore_errors=True)
    wfile = conn.makefile("wb")

    def closer():
        for f in (wfile, rfile):
            f.close()
        conn.close()
    return Channel(rfile, wfile, closer)


def _accept_with_token(srv, token: str, timeout: float, clock, proc):
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        if remaining <= 0 or (proc is not None and hasattr(proc, "poll") and proc.poll() is not None):
            raise HelperFailed("elevation_refused", "the write helper did not connect (administrator authorisation refused?)")
        srv.settimeout(min(remaining, 0.5))
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        conn.settimeout(10)
        rfile = conn.makefile("rb")
        try:
            hello = PR.read_message(rfile)
        except (PR.ProtocolError, OSError):
            hello = None
        if isinstance(hello, dict) and isinstance(hello.get("hello"), str) and hmac.compare_digest(hello["hello"], token):
            conn.settimeout(None)
            return conn, rfile
        rfile.close()
        conn.close()                         # an impostor (or garbage): keep waiting for the real helper


# ------------------------------------------------------------------ elevation commands
def helper_command(frozen: bool | None = None, executable: str | None = None, platform: str = sys.platform) -> list[str]:
    """The helper's argv prefix: the sibling executable in a package, ``python -m hififlash.helper`` in development."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = executable or sys.executable
    if frozen:
        name = "hififlash-helper.exe" if platform in ("win32", "cygwin") else "hififlash-helper"
        return [os.path.join(os.path.dirname(os.path.abspath(executable)), name)]
    return [executable, "-m", "hififlash.helper"]


def linux_command(helper: list[str]) -> list[str]:
    return ["pkexec", *helper, "--stdio"]


def applescript_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_launch(helper: list[str], popen=subprocess.Popen) -> Callable[[int, str], object]:
    def launch(port: int, token_file: str):
        cmd = " ".join(shlex.quote(x) for x in [*helper, "--connect", str(port), "--token-file", token_file]) + " >/dev/null 2>&1"
        script = f"do shell script {applescript_string(cmd)} with administrator privileges"
        return popen(["osascript", "-e", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return launch


def windows_launch(helper: list[str], runas=None) -> Callable[[int, str], object]:
    def launch(port: int, token_file: str):
        exe, *rest = helper
        params = subprocess.list2cmdline([*rest, "--connect", str(port), "--token-file", token_file])
        if runas is None:
            from ..platforms.win32api import shell_execute_runas
            return shell_execute_runas(exe, params)
        return runas(exe, params)
    return launch


def channel_factory(platform: str = sys.platform, simulate: str | None = None, popen=subprocess.Popen,
                    runas=None) -> Callable[[], Channel]:
    """How the GUI opens a helper session on this platform."""
    if simulate:
        from ..platforms.simulated import SimulatedAdapter
        adapter = SimulatedAdapter(simulate)
        return lambda: inprocess(adapter, "simulated")
    helper = helper_command(platform=platform)
    if platform.startswith("linux"):
        return lambda: stdio(linux_command(helper), popen)
    if platform == "darwin":
        return lambda: connect_back(macos_launch(helper, popen))
    if platform in ("win32", "cygwin"):
        return lambda: connect_back(windows_launch(helper, runas))
    raise ValueError(f"unsupported platform {platform}")
