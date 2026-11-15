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
        fd = os.open(MIC_FIFO, os.O_WRONLY); chunk = struct.pack("<hh", MIC_VALUE, MIC_VALUE) * 882; t = time.monotonic()
        while not self.stop_ev.is_set():
            os.write(fd, chunk); t += 0.02; time.sleep(max(0.0, t - time.monotonic()))
        os.close(fd)


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


def load_source(name, fifo):
    if not os.path.exists(fifo):
        os.mkfifo(fifo)
    return pactl("load-module", "module-pipe-source", f"source_name={name}", f"file={fifo}", "format=s16le", "rate=44100", "channels=2")


class Feeder:
    """Writes the pattern into the FIFO in real time (20 ms chunks) starting at index 1 (index 0 == silence)."""
    def __init__(self):
        self.next_index, self._stop, self._t = 1, threading.Event(), None

    def start(self):
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True); self._t.start()

    def _run(self):
        while not self._stop.is_set():          # reopen when the source node is unloaded/reloaded (node-vanish test)
            fd = os.open(FIFO, os.O_WRONLY)
            t = time.monotonic()
            try:
                while not self._stop.is_set():
                    os.write(fd, pattern(self.next_index, 882)); self.next_index += 882
                    t += 0.02
                    time.sleep(max(0.0, t - time.monotonic()))
            except BrokenPipeError:
                pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

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


@pytest.fixture(scope="session")
def feeder(pw):
    """One continuous feeder for the whole session: the index never restarts, so a repeat/drop is always a real one."""
    f = Feeder(); f.start(); time.sleep(0.5); yield f; f.stop()


RESULTS: dict = {}


def pytest_sessionfinish(session, exitstatus):
    if RESULTS:
        os.makedirs("/work/measurements", exist_ok=True)
        ver = RESULTS.get("_version", "unknown")
        with open(f"/work/measurements/pipewire_capture_{ver}.json", "w") as fh:
            json.dump(RESULTS, fh, indent=1, sort_keys=True)
