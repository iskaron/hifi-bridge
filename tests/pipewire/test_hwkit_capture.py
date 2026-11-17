"""Kit capture path through REAL PipeWire (parec, no fallback): a second simulated phone node plays the indexed test track;
`hwkit.capture.record` writes WAV + timestamps; the analyser must find a clean block sequence.  SIMULATED node, not BlueZ."""
import os
import subprocess
import threading
import time

from conftest import RESULTS, load_source, pactl, writer_fd

NODE2 = "bluez_input.AA_BB_CC_DD_EE_02"
FIFO2 = "/tmp/bt-src2.fifo"


SILENT_RETRIES = 3


def _drain(fd: int) -> None:
    """Discard what a previous attempt left in the FIFO, so a retry starts with the track and not with stale frames."""
    os.set_blocking(fd, False)
    try:
        while os.read(fd, 65536):
            pass
    except BlockingIOError:
        pass
    finally:
        os.set_blocking(fd, True)


def _record_once(tmp_path, pcm):
    from hwkit import capture as C
    module = load_source(NODE2, FIFO2)
    stop = threading.Event()

    def feed():
        fd = writer_fd(FIFO2); t = time.monotonic()
        for k in range(0, len(pcm), 3528):
            if stop.is_set():
                break
            os.write(fd, pcm[k:k + 3528]); t += 0.02; time.sleep(max(0.0, t - time.monotonic()))
        while not stop.is_set():                      # keep the node alive with silence after the track
            os.write(fd, b"\0" * 3528); t += 0.02; time.sleep(max(0.0, t - time.monotonic()))
    th = threading.Thread(target=feed, daemon=True)
    keep = subprocess.Popen(["parec", f"--device={NODE2}", "--property=node.dont-fallback=true", "--raw"], stdout=subprocess.DEVNULL)
    th.start(); time.sleep(0.3)
    meta = C.record(str(tmp_path), None, 8.5, node=NODE2, label="SIMULATED")
    stop.set(); keep.terminate(); th.join(3); pactl("unload-module", module)
    _drain(writer_fd(FIFO2))
    return meta


def test_hwkit_capture_through_real_pipewire_is_clean(pw, tmp_path):
    from hwkit import analyze as A
    from hwkit import signals as S
    pcm = S.to_int16_stereo(S.indexed_track(8, first_index=4242)).tobytes()
    # Stand-in workaround (NOT product behaviour, same fault as conftest.bring_up_node): a FRESH module-pipe-source on PipeWire 0.3.65
    # sometimes delivers only silence (seen once in six local runs on 2026-11-17). Only a recording that is completely silent is retried;
    # any drop, repeat or gap in a recording with signal still fails. Retries are counted in the results file.
    for attempt in range(SILENT_RETRIES + 1):
        meta = _record_once(tmp_path, pcm)
        x, rate = A.read_wav(str(tmp_path / "phone.wav"))
        if len(x) and float(abs(x).max()) > 0.0:
            break
    RESULTS["_hwkit_silent_retries"] = attempt
    c = A.continuity(A.find_blocks(x, rate))
    assert meta["recordings"]["phone"]["first_sample_wall"] is not None
    assert c["drops"] == 0 and c["repeats"] == 0 and not c["gaps"] and c["decoded"] >= 70 and c["last"] == 4242 + 79, c
