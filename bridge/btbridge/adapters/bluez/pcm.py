"""PCM capture of the phone's decoded A2DP audio (PipeWire side).  Delivery contract of ``on_pcm``:
44.1 kHz / s16le / stereo, roughly real time.

``PipeWireCapture`` supervises a capture subprocess that writes raw PCM to stdout.

Verified hardware-free (INTEGRATION against REAL PipeWire 0.3.65 and 1.4.2 tools in Docker, source node SIMULATED with
``module-pipe-source``; docs/PIPEWIRE_CAPTURE.md):
  * ``parec --raw`` (pipewire-pulse) is headerless and bit-exact on both versions when the graph allows 44.1 kHz;
  * ``pw-cat --record ... -`` WITHOUT ``--raw`` is not raw PCM on 1.4 (Sun-AU header), and 0.3.65 has no ``--raw`` at all;
  * a capture target is only a *hint*: if the node is missing, both tools silently record the DEFAULT source (e.g. a microphone).
    The bridge therefore always sets ``node.dont-fallback`` / ``node.dont-reconnect`` and supervises with a no-data watchdog.

REAL-HARDWARE finding G1 (Pi 3 B+, Pi OS Lite trixie, PipeWire/WirePlumber 1.4.2, 2026-11-24; docs/PIPEWIRE_CAPTURE.md "G1"): with
WirePlumber's default ``bluez5.media-source-role = playback`` the phone's audio is a PLAYBACK stream ``bluez_input.<MAC>.2``
(Stream/Output/Audio) that WirePlumber plays on the Pi's own output - there is no source to record. The appliance and the test kit
therefore install a WirePlumber rule that sets ``bluez5.media-source-role = input`` (an Audio/Source node, never linked to a speaker),
and the bridge finds that node by its properties (``find_phone_node``: ``api.bluez5.address`` + ``api.bluez5.profile = a2dp-source``
+ ``media.class = Audio/Source``, read with ``pw-dump``) instead of guessing a name. Capturing with that rule on a real Pi is still
HARDWARE-PENDING (docs/HARDWARE_GATE_CHECKLIST.md G1).
"""
from __future__ import annotations

import json
import os
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


PHONE_PROFILE = "a2dp-source"            # api.bluez5.profile of the node carrying a phone's media audio (bluez5-device.c)
CAPTURE_CLASS = "Audio/Source"            # media.class with bluez5.media-source-role = input (media-source.c)
PLAYBACK_CLASS = "Stream/Output/Audio"    # media.class with the default role 'playback': played on the Pi's speakers, not recordable
ROLE_HINT = ("install the WirePlumber rule that sets bluez5.media-source-role = input "
             "(hwkit/pi-setup/50-hifi-bridge-a2dp-input.conf; docs/PIPEWIRE_CAPTURE.md G1)")


def pw_dump(run=subprocess.run, timeout: float = 5.0) -> list:
    """The PipeWire object list (``pw-dump``, JSON). Raises ``RuntimeError`` with a short reason."""
    try:
        r = run(["pw-dump"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError(f"pw-dump unavailable ({e}); install pipewire-bin / the PipeWire tools") from e
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"pw-dump failed: {type(e).__name__}") from e
    if r.returncode != 0:
        raise RuntimeError(f"pw-dump failed with exit status {r.returncode} (is PipeWire running for this user?)")
    try:
        objs = json.loads(r.stdout or "[]")
    except ValueError as e:
        raise RuntimeError("pw-dump output is not valid JSON") from e
    if not isinstance(objs, list):
        raise RuntimeError("pw-dump output is not a list")
    return objs


def find_phone_node(address: str, dump: list | None = None, run=subprocess.run) -> tuple[str | None, str]:
    """PipeWire node that carries the decoded A2DP audio of the phone ``address`` and can be RECORDED.

    Matches only nodes of that phone (``api.bluez5.address``), its media profile (``api.bluez5.profile = a2dp-source``; never a
    hands-free/SCO node) and ``media.class = Audio/Source`` (not internal). Several matches: the newest (highest ``object.serial``).
    Returns ``(node.name, reason)``; ``node.name`` is None when nothing can be captured, and the reason says why - in particular when
    the phone's audio exists only as a playback stream (WirePlumber's default role, the G1 finding). Never raises."""
    if dump is None:
        try:
            dump = pw_dump(run)
        except RuntimeError as e:
            return None, str(e)
    want = address.upper()
    sources, playback = [], []
    for o in dump:
        if not isinstance(o, dict) or o.get("type") != "PipeWire:Interface:Node":
            continue
        p = (o.get("info") or {}).get("props") or {}
        if str(p.get("api.bluez5.address", "")).upper() != want or p.get("api.bluez5.profile") != PHONE_PROFILE:
            continue
        if str(p.get("api.bluez5.internal", "false")).lower() == "true":
            continue
        cls, name = p.get("media.class"), p.get("node.name")
        if not name:
            continue
        try:
            serial = int(p.get("object.serial", o.get("id", 0)))
        except (TypeError, ValueError):
            serial = 0
        if cls == CAPTURE_CLASS:
            sources.append((serial, name))
        elif cls == PLAYBACK_CLASS:
            playback.append(name)
    if sources:
        serial, name = max(sources)
        return name, f"capture source {name}"
    if playback:
        return None, (f"the phone's audio is a playback stream ({playback[0]}, {PLAYBACK_CLASS}) that PipeWire plays on this "
                      f"device's own output, not a source the bridge can record: {ROLE_HINT}")
    return None, f"no PipeWire node for {want} ({PHONE_PROFILE}) yet"


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


STDERR_KEEP = 2048            # bytes of a capture child's stderr kept for the diagnosis (RAM, per child)
LOG_REPEAT_EVERY = 50         # an identical capture failure is logged again only every N times (bounded RAM log)


def _last_line(raw: bytes) -> str:
    lines = [ln.strip() for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:300]


class PipeWireCapture:
    def __init__(self, argv_for: Callable[[str], list[str]] | None = None, popen=subprocess.Popen,
                 retry_s: float = 0.5, log: Callable[[str], None] = lambda m: None, stall_s: float = 3.0,
                 present: Callable[[str], bool] | None = None,
                 resolve: Callable[[str], tuple[str | None, str]] | None = None,
                 argv_for_node: Callable[[str], list[str]] | None = None):
        """Two ways to build the command:
          * ``resolve(address) -> (node | None, reason)`` + ``argv_for_node(node)`` (the bridge): the target is discovered again
            before every child start; while it is None no child runs and the reason is shown (``status()``, logged once);
          * ``argv_for(address)`` (fixed command; tests and tools), optionally gated by ``present(address)``.
        Default: ``find_phone_node`` + ``parec_argv``. ``stall_s``: restart a child that delivers no data for that long."""
        if argv_for is None and resolve is None:
            resolve, argv_for_node = find_phone_node, argv_for_node or parec_argv
        self.argv_for, self.resolve, self.argv_for_node = argv_for, resolve, argv_for_node or parec_argv
        self.popen, self.retry_s, self.log, self.stall_s, self.present = popen, retry_s, log, stall_s, present
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = None
        self.starts = 0                                   # how often the child was (re)started
        self._status = {"state": "stopped", "address": None, "node": None, "reason": "", "last_error": ""}
        self._last_logged, self._repeats = None, 0

    def status(self) -> dict:
        """Bounded, non-secret capture state for diagnostics: stopped | waiting | capturing | restarting."""
        return {**self._status, "starts": self.starts}

    def _set(self, **kw) -> None:
        self._status = {**self._status, **kw}

    def _log_once(self, key: str, msg: str) -> None:
        """Log a message when it differs from the previous one, an identical one only every LOG_REPEAT_EVERY times."""
        if key == self._last_logged:
            self._repeats += 1
            if self._repeats % LOG_REPEAT_EVERY:
                return
            msg = f"{msg} (repeated {self._repeats} times)"
        else:
            self._last_logged, self._repeats = key, 0
        self.log(msg)

    def start(self, address: str, on_pcm: Callable[[bytes], None]) -> None:
        self.stop()
        self._stop = threading.Event()
        self._last_logged, self._repeats = None, 0
        self._set(state="waiting", address=address, node=None, reason="starting", last_error="")
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
        self._set(state="stopped", node=None)

    def _argv(self, address: str) -> tuple[list[str] | None, str | None, str]:
        if self.resolve is not None:
            node, why = self.resolve(address)
            return (self.argv_for_node(node) if node else None), node, why
        if self.present is not None and not self.present(address):
            return None, None, "capture target not present"
        return self.argv_for(address), None, ""

    def _run(self, address: str, on_pcm, stop: threading.Event) -> None:
        fails = 0                                          # consecutive runs without lasting audio (restart back-off)
        while not stop.is_set():
            argv, node, why = self._argv(address)
            if argv is None:
                self._set(state="waiting", node=None, reason=why)
                self._log_once("wait:" + why, f"capture waiting: {why}")
                if stop.wait(self.retry_s):
                    return
                continue
            try:
                proc = self.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            except OSError as e:
                self._set(state="waiting", reason=f"capture tool unavailable: {e}", last_error=str(e))
                self._log_once(f"tool:{e}", f"capture tool unavailable: {e}")
                if stop.wait(self.retry_s * 4):
                    return
                continue
            self._proc = proc
            self.starts += 1
            self._set(state="restarting" if self.starts > 1 else "waiting", node=node, reason=why)
            started = time.monotonic()
            first_data, err, ended = None, b"", "exited"
            try:
                buf, last_data = b"", time.monotonic()
                streams = [proc.stdout] + ([proc.stderr] if getattr(proc, "stderr", None) is not None else [])
                while not stop.is_set():
                    ready, _, _ = select.select(streams, [], [], 0.2)
                    if not ready:
                        if time.monotonic() - last_data > self.stall_s:
                            ended = "stalled"
                            break
                        continue
                    if len(streams) > 1 and proc.stderr in ready:       # drain stderr so the child never blocks on a full pipe
                        chunk = os.read(proc.stderr.fileno(), 4096)
                        if chunk:
                            err = (err + chunk)[-STDERR_KEEP:]
                        else:
                            streams.remove(proc.stderr)
                    if proc.stdout not in ready:
                        if time.monotonic() - last_data > self.stall_s:    # stderr chatter is not audio: the watchdog still applies
                            ended = "stalled"
                            break
                        continue
                    data = proc.stdout.read(CHUNK_BYTES - len(buf))
                    if not data:
                        break                              # child exited (node missing / PipeWire restarted): retry
                    last_data = time.monotonic()
                    if first_data is None:
                        first_data = last_data
                        self._set(state="capturing", last_error="")
                        self._last_logged, self._repeats = None, 0
                    buf += data
                    if len(buf) >= CHUNK_BYTES:
                        on_pcm(buf)
                        buf = b""
            finally:
                if not stop.is_set():
                    err = self._drain_stderr(proc, err)
                reap(proc)
                for f in (getattr(proc, "stderr", None),):
                    try:
                        if f is not None:
                            f.close()
                    except Exception:
                        pass
            if stop.is_set():
                return
            rc = proc.returncode
            tail = _last_line(err)
            what = "stalled (no audio for %.0f s)" % self.stall_s if ended == "stalled" else f"exited with status {rc}"
            msg = f"capture child {what} after {time.monotonic() - started:.1f} s" + (f": {tail}" if tail else "")
            self._set(state="restarting", last_error=msg)
            self._log_once(f"child:{what}:{tail}", msg)
            healthy = first_data is not None and time.monotonic() - started >= HEALTHY_RUN_S
            fails = 0 if healthy else fails + 1
            delay = self.retry_s if fails <= FAST_RETRIES else min(RESTART_MAX_S, self.retry_s * 2 ** (fails - FAST_RETRIES))
            if stop.wait(delay):
                return

    @staticmethod
    def _drain_stderr(proc, err: bytes, limit: int = 1 << 20) -> bytes:
        """Read what is left on a finished child's stderr (bounded: at most ``limit`` bytes are read, the last STDERR_KEEP kept)."""
        f = getattr(proc, "stderr", None)
        if f is None or f.closed:
            return err
        try:
            if proc.poll() is None:
                return err
            read = 0
            while read < limit:
                r, _, _ = select.select([f], [], [], 0)
                if not r:
                    break
                chunk = os.read(f.fileno(), 65536)
                if not chunk:
                    break
                read += len(chunk)
                err = (err + chunk)[-STDERR_KEEP:]
        except (OSError, ValueError):
            pass
        return err
