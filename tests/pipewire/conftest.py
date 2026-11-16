"""PipeWire capture tests: run INSIDE the btbridge-pw image (real PipeWire + WirePlumber + pipewire-pulse, no audio hardware).
A `module-pipe-source` node named like a BlueZ phone node is fed from a FIFO with the index-addressable pattern.
Label: INTEGRATION (real PipeWire tools) / SIMULATED source node. NOT a BlueZ node, NOT a phone, NOT a Pi."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "testkit", "pw"))
from pcm_probe import pattern  # noqa: E402

NODE = "bluez_input.AA_BB_CC_DD_EE_01"
FIFO = "/tmp/bt-src.fifo"
MIC = "usb_mic_simulated"                 # a second source standing in for e.g. a USB microphone on the Pi
MIC_FIFO = "/tmp/mic.fifo"
MIC_VALUE = 1234                          # the 'microphone' delivers a constant, easily recognisable sample value
HERE = os.path.dirname(os.path.abspath(__file__))
START = os.path.join(HERE, "..", "..", "testkit", "pw", "start_pipewire.sh")


def _env_after_start() -> dict:
    out = subprocess.run(["bash", "-c", f". {START} >/dev/null && env -0"], capture_output=True, timeout=60, check=True,
                         stdin=subprocess.DEVNULL).stdout
    return dict(kv.split("=", 1) for kv in out.decode().split("\0") if "=" in kv)


class MicFeeder(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.stop_ev = threading.Event()

    def run(self):
        import struct
        fd = writer_fd(MIC_FIFO); chunk = struct.pack("<hh", MIC_VALUE, MIC_VALUE) * 882; t = time.monotonic()
        while not self.stop_ev.is_set():
            os.write(fd, chunk); t += 0.02; time.sleep(max(0.0, t - time.monotonic()))


def pactl(*args, attempts=20):
    """pipewire-pulse applies some changes asynchronously right after start-up: retry briefly instead of failing flakily."""
    last = None
    for _ in range(attempts):
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
        last = r.stderr.strip()
        time.sleep(0.25)
    log = ""
    try:                                   # diagnose the rare pipewire-pulse disappearance seen once on 1.4.2 (docs/PIPEWIRE_CAPTURE.md)
        log = subprocess.run(["bash", "-c", "pgrep -a pipewire; tail -20 /tmp/pw-logs/pipewire-pulse.log"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        pass
    raise RuntimeError(f"pactl {' '.join(args)} failed: {last}\n--- pipewire processes / pipewire-pulse log ---\n{log}")


WRITERS: dict[str, int] = {}


def writer_fd(fifo: str) -> int:
    """A writer that stays attached for the whole session, opened BEFORE the pipe source is loaded: otherwise the module can see
    'no writer' (EOF) at start and then deliver only silence for the whole session (about 1 in 3 sessions on 0.3.65, found 2026-11-16)."""
    if fifo not in WRITERS:
        WRITERS[fifo] = os.open(fifo, os.O_RDWR)
    return WRITERS[fifo]


def load_source(name, fifo):
    if not os.path.exists(fifo):
        os.mkfifo(fifo)
    writer_fd(fifo)
    return pactl("load-module", "module-pipe-source", f"source_name={name}", f"file={fifo}", "format=s16le", "rate=44100", "channels=2")


class Feeder:
    """Writes the pattern into the FIFO in real time (20 ms chunks) starting at index 1 (index 0 == silence)."""
    def __init__(self):
        self.next_index, self._stop, self._t = 1, threading.Event(), None

    def start(self):
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True); self._t.start()

    def _run(self):
        fd = writer_fd(FIFO)                    # O_RDWR writer: survives unloading/reloading the source (node-vanish test)
        t = time.monotonic()
        while not self._stop.is_set():
            os.write(fd, pattern(self.next_index, 882)); self.next_index += 882
            t += 0.02
            time.sleep(max(0.0, t - time.monotonic()))

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=3)


@pytest.fixture(scope="session")
def pw():
    env = _env_after_start()
    os.environ.update({k: env[k] for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME") if k in env})
    module = load_source(NODE, FIFO)
    load_source(MIC, MIC_FIFO)
    mic = MicFeeder(); mic.start()
    # Keep the simulated phone node permanently running (a module-pipe-source suspends without consumers and produces a
    # resume gap that is an artefact of the stand-in, not of the capture tools under test).
    keep = subprocess.Popen(["parec", f"--device={NODE}", "--property=node.dont-fallback=true", "--raw"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    pactl("set-default-source", MIC)                                                # fallback target = the 'microphone'
    version = subprocess.run(["pipewire", "--version"], capture_output=True, text=True).stdout.strip().splitlines()[-1].split()[-1]
    yield {"node": NODE, "version": version, "module": module, "mic_value": MIC_VALUE}
    keep.terminate()
    mic.stop_ev.set()


def wait_until_node_delivers(node: str, timeout: float = 30.0) -> float:
    """Readiness of the SIMULATED node. On PipeWire 0.3.65 a fresh module-pipe-source sometimes delivers only silence for the first seconds
    (about 1 in 3 sessions, also with the previous image - found 2026-11-16). Capture with the bridge's own supervisor until real
    (non-zero) frames arrive, so measurements never start during that warm-up."""
    from btbridge.adapters.bluez.pcm import PipeWireCapture, parec_argv
    got = threading.Event()
    cap = PipeWireCapture(lambda a: parec_argv(node), retry_s=0.2)
    cap.start("x", lambda pcm: got.set() if pcm.strip(b"\x00") else None)
    t0 = time.monotonic()
    try:
        if not got.wait(timeout):
            raise RuntimeError(f"simulated node {node} delivered only silence for {timeout} s")
    finally:
        cap.stop()
    return time.monotonic() - t0


def bring_up_node(pw, attempts: int = 3) -> int:
    """Stand-in workaround (NOT product behaviour): reload the module-pipe-source until it delivers real frames. Returns the reloads."""
    reloads = 0
    while True:
        try:
            wait_until_node_delivers(NODE, timeout=8.0)
            return reloads
        except RuntimeError:
            if reloads == attempts:
                raise
            reloads += 1
            pactl("unload-module", pw["module"])
            pw["module"] = load_source(NODE, FIFO)


@pytest.fixture(scope="session")
def feeder(pw):
    """One continuous feeder for the whole session: the index never restarts, so a repeat/drop is always a real one.
    Stand-in workaround (NOT product behaviour): on PipeWire 0.3.65 about 1 session in 3 starts with a module-pipe-source that delivers
    only silence; reloading the module fixes it. Up to 3 reloads, each counted in the results file."""
    f = Feeder(); f.start(); time.sleep(0.5)
    t0 = time.monotonic()
    RESULTS["_stand_in_reloads"] = bring_up_node(pw)
    RESULTS["_node_ready_after_s"] = round(time.monotonic() - t0, 2)
    yield f
    f.stop()


RESULTS: dict = {}


def pytest_sessionfinish(session, exitstatus):
    if RESULTS:
        os.makedirs("/work/measurements", exist_ok=True)
        ver = RESULTS.get("_version", "unknown")
        with open(f"/work/measurements/pipewire_capture_{ver}.json", "w") as fh:
            json.dump(RESULTS, fh, indent=1, sort_keys=True)
