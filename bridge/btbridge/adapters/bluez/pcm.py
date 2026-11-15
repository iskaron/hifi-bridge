"""PCM capture of the phone's decoded A2DP audio (PipeWire side).  Delivery contract of ``on_pcm``:
44.1 kHz / s16le / stereo, roughly real time.

``PipeWireCapture`` supervises a ``pw-record``-style subprocess that writes raw PCM to stdout.  What is
verified without hardware: process supervision, chunking, retry while the node does not exist yet, clean stop
(UNIT with fake child processes; INTEGRATION against a real PipeWire daemon in Docker where available).
UNVERIFIED: that a BlueZ-created node for a phone exists under the name template below and delivers audio
(docs/HARDWARE_VALIDATION_PLAN.md).
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Protocol

RATE, CHANNELS, SAMPLE_BYTES = 44100, 2, 2
CHUNK_BYTES = RATE * CHANNELS * SAMPLE_BYTES // 50          # 20 ms


class PcmCapture(Protocol):
    def start(self, address: str, on_pcm: Callable[[bytes], None]) -> None: ...
    def stop(self) -> None: ...


def node_name_for(address: str, template: str = "bluez_input.{mac_}") -> str:
    """PipeWire node name for a phone (A2DP source role).  The default follows the naming found in PipeWire/WirePlumber
    documentation/community reports (``bluez_input.<MAC with underscores>...``) - UNVERIFIED on hardware."""
    return template.format(mac=address.upper(), mac_=address.upper().replace(":", "_"))


def record_argv(target: str, extra: tuple[str, ...] = (), tool: str = "pw-cat") -> list[str]:
    """``pw-cat`` command line recording ``target`` as 44.1 kHz s16 stereo to stdout (``-``).

    Checked: ``pw-cat --help`` of PipeWire 0.3.65 (Debian bookworm) has these options and *no* ``--raw`` flag.
    UNVERIFIED: that ``-`` as output file writes *headerless* PCM in that version (it may go through libsndfile) and that the
    capture-from-node linking works for a BlueZ node.  A headless PipeWire test in Docker could not link a null sink within the
    time box (docs/FINDINGS.md), so this is the first item of the hardware/PipeWire validation plan.  ``parec_argv`` is the alternative
    (documented raw output, needs pipewire-pulse)."""
    return [tool, "--record", "--target", target, "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16", *extra, "-"]


def parec_argv(target: str) -> list[str]:
    """PulseAudio-compatible capture (PipeWire's pipewire-pulse): ``parec`` writes raw PCM to stdout.  UNVERIFIED on target."""
    return ["parec", f"--device={target}", "--format=s16le", f"--rate={RATE}", f"--channels={CHANNELS}", "--raw", "--latency-msec=20"]


class PipeWireCapture:
    def __init__(self, argv_for: Callable[[str], list[str]] | None = None, popen=subprocess.Popen,
                 retry_s: float = 0.5, log: Callable[[str], None] = lambda m: None):
        self.argv_for = argv_for or (lambda addr: record_argv(node_name_for(addr)))
        self.popen, self.retry_s, self.log = popen, retry_s, log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = None
        self.starts = 0                                   # how often the child was (re)started

    def start(self, address: str, on_pcm: Callable[[bytes], None]) -> None:
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(address, on_pcm, self._stop), daemon=True, name="pw-capture")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread, self._proc = None, None

    def _run(self, address: str, on_pcm, stop: threading.Event) -> None:
        argv = self.argv_for(address)
        while not stop.is_set():
            try:
                proc = self.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except OSError as e:
                self.log(f"capture tool unavailable: {e}")
                if stop.wait(self.retry_s * 4):
                    return
                continue
            self._proc = proc
            self.starts += 1
            try:
                buf = b""
                while not stop.is_set():
                    data = proc.stdout.read(CHUNK_BYTES - len(buf))
                    if not data:
                        break                              # child exited (node missing / PipeWire restarted): retry
                    buf += data
                    if len(buf) >= CHUNK_BYTES:
                        on_pcm(buf)
                        buf = b""
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if stop.wait(self.retry_s):
                return
