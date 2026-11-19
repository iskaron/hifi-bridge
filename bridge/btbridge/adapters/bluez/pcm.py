"""PCM capture of the phone's decoded A2DP audio (PipeWire side).  Delivery contract of ``on_pcm``:
44.1 kHz / s16le / stereo, roughly real time.

``PipeWireCapture`` supervises a capture subprocess that writes raw PCM to stdout.

Verified hardware-free (INTEGRATION against REAL PipeWire 0.3.65 and 1.4.2 tools in Docker, source node SIMULATED with
``module-pipe-source``; docs/PIPEWIRE_CAPTURE.md):
  * ``parec --raw`` (pipewire-pulse) is headerless and bit-exact on both versions when the graph allows 44.1 kHz;
  * ``pw-cat --record ... -`` WITHOUT ``--raw`` is not raw PCM on 1.4 (Sun-AU header), and 0.3.65 has no ``--raw`` at all;
  * a capture target is only a *hint*: if the node is missing, both tools silently record the DEFAULT source (e.g. a microphone).
    The bridge therefore always sets ``node.dont-fallback`` / ``node.dont-reconnect`` and supervises with a no-data watchdog.
UNVERIFIED: the real BlueZ node name/properties for a phone and its behaviour on suspend (docs/HARDWARE_VALIDATION_PLAN.md A3/A4).
"""
from __future__ import annotations

import select
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


NO_FALLBACK = ("node.dont-fallback=true", "node.dont-reconnect=true")     # never capture another source by accident


def parec_argv(target: str) -> list[str]:
    """Default capture command: ``parec`` via pipewire-pulse. Headerless, bit-exact, fails fast for a missing node (lab-verified)."""
    return ["parec", f"--device={target}", *(f"--property={p}" for p in NO_FALLBACK), "--format=s16le", f"--rate={RATE}",
            f"--channels={CHANNELS}", "--raw", "--latency-msec=20"]


def record_argv(target: str, raw: bool = True, tool: str = "pw-cat") -> list[str]:
    """Alternative: native ``pw-cat``. ``raw=True`` needs PipeWire >= 1.x (0.3.65 has no ``--raw``; without it, ``-`` is not raw PCM).
    On 0.3.65 a missing target with dont-fallback leaves pw-cat running without data -> the supervisor's watchdog restarts it."""
    props = "{ " + " ".join(p.replace("=", " = ") for p in NO_FALLBACK) + " }"
    return [tool, "--record", "--target", target, "-P", props, "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16",
            *(["--raw"] if raw else []), "-"]


def choose_capture(which=None, run=subprocess.run) -> tuple[str, Callable[[str], list[str]]]:
    """Pick the capture command available on this system (all variants keep node.dont-fallback/dont-reconnect):
      1. ``pw-cat --raw``  - PipeWire >= 1.x (Buildroot 2026.02 ships 1.2.8 WITHOUT parec; verified bit-exact on 1.4.2 in the lab)
      2. ``parec --raw``   - needs pipewire-pulse + pulseaudio-utils (verified bit-exact on 0.3.65 and 1.4.2)
      3. ``pw-cat``        - PipeWire 0.3.x without --raw, where '-' is raw PCM (verified on 0.3.65); a missing node keeps it running
                             silently, which the supervisor's no-data watchdog handles.
    Returns (name, argv_for_node)."""
    import shutil
    which = which or shutil.which
    pwcat = which("pw-cat")
    if pwcat:
        try:
            helptext = run([pwcat, "--help"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            helptext = ""
        if "--raw" in helptext:
            return "pw-cat --raw", lambda node: record_argv(node, raw=True, tool=pwcat)
    if which("parec"):
        return "parec", parec_argv
    if pwcat:
        return "pw-cat (0.3 raw)", lambda node: record_argv(node, raw=False, tool=pwcat)
    return "none", parec_argv          # nothing installed: supervisor logs 'capture tool unavailable' and retries


def source_present(node: str, run=subprocess.run) -> bool:
    """True if PipeWire currently has an audio source with this node name (``pactl list short sources``)."""
    try:
        out = run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(len(cols) > 1 and cols[1] == node for cols in (line.split("\t") for line in out.splitlines()))


RESTART_MAX_S = 5.0           # capture child restart back-off cap (review L4)
FAST_RETRIES = 4              # first retries at retry_s: the node usually appears within ~2 s of the transport (start latency)
HEALTHY_RUN_S = 5.0           # a child that delivered audio this long resets the back-off


def reap(proc, timeout: float = 2.0) -> None:
    """Terminate a child, wait for it (no zombie) and close its pipes (no fd leak); kill if it does not exit."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)
    except Exception:
        pass
    for f in (getattr(proc, "stdout", None), getattr(proc, "stdin", None)):
        try:
            if f is not None:
                f.close()
        except Exception:
            pass


class PipeWireCapture:
    def __init__(self, argv_for: Callable[[str], list[str]] | None = None, popen=subprocess.Popen,
                 retry_s: float = 0.5, log: Callable[[str], None] = lambda m: None, stall_s: float = 3.0,
                 present: Callable[[str], bool] | None = None):
        """``argv_for(address)`` builds the command; ``present(address)`` (optional) is checked before every start;
        ``stall_s``: restart the child if it delivers no data for that long (missing link, suspended node)."""
        self.argv_for = argv_for or (lambda addr: parec_argv(node_name_for(addr)))
        self.popen, self.retry_s, self.log, self.stall_s, self.present = popen, retry_s, log, stall_s, present
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
                p.terminate()                              # unblocks the reader; the capture thread reaps it
            except Exception:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        reap(p)                                            # idempotent: no zombie even if the thread did not get to it
        self._thread, self._proc = None, None

    def _run(self, address: str, on_pcm, stop: threading.Event) -> None:
        argv = self.argv_for(address)
        fails = 0                                          # consecutive runs without lasting audio (restart back-off)
        while not stop.is_set():
            if self.present is not None and not self.present(address):
                if stop.wait(self.retry_s):
                    return
                continue
            try:
                proc = self.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except OSError as e:
                self.log(f"capture tool unavailable: {e}")
                if stop.wait(self.retry_s * 4):
                    return
                continue
            self._proc = proc
            self.starts += 1
            started = time.monotonic()
            first_data = None
            try:
                buf, last_data = b"", time.monotonic()
                while not stop.is_set():
                    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                    if not ready:
                        if time.monotonic() - last_data > self.stall_s:
                            self.log("capture stalled: restarting")
                            break
                        continue
                    data = proc.stdout.read(CHUNK_BYTES - len(buf))
                    if not data:
                        break                              # child exited (node missing / PipeWire restarted): retry
                    last_data = time.monotonic()
                    first_data = first_data or last_data
                    buf += data
                    if len(buf) >= CHUNK_BYTES:
                        on_pcm(buf)
                        buf = b""
            finally:
                reap(proc)
            healthy = first_data is not None and time.monotonic() - started >= HEALTHY_RUN_S
            fails = 0 if healthy else fails + 1
            delay = self.retry_s if fails <= FAST_RETRIES else min(RESTART_MAX_S, self.retry_s * 2 ** (fails - FAST_RETRIES))
            if stop.wait(delay):
                return
