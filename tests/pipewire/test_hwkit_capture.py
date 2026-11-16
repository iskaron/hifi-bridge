"""Kit capture path through REAL PipeWire (parec, no fallback): a second simulated phone node plays the indexed test track;
`hwkit.capture.record` writes WAV + timestamps; the analyser must find a clean block sequence.  SIMULATED node, not BlueZ."""
import os
import threading
import time

from conftest import load_source, pactl, writer_fd  # noqa: F401

NODE2 = "bluez_input.AA_BB_CC_DD_EE_02"
FIFO2 = "/tmp/bt-src2.fifo"


def test_hwkit_capture_through_real_pipewire_is_clean(pw, tmp_path):
    from hwkit import analyze as A
    from hwkit import capture as C
    from hwkit import signals as S
    module = load_source(NODE2, FIFO2)
    pcm = S.to_int16_stereo(S.indexed_track(8, first_index=4242)).tobytes()
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
    keep = __import__("subprocess").Popen(["parec", f"--device={NODE2}", "--property=node.dont-fallback=true", "--raw"],
                                          stdout=__import__("subprocess").DEVNULL)
    th.start(); time.sleep(0.3)
    meta = C.record(str(tmp_path), None, 8.5, node=NODE2, label="SIMULATED")
    stop.set(); keep.terminate(); th.join(3); pactl("unload-module", module)
    x, rate = A.read_wav(str(tmp_path / "phone.wav"))
    c = A.continuity(A.find_blocks(x, rate))
    assert meta["recordings"]["phone"]["first_sample_wall"] is not None
    assert c["drops"] == 0 and c["repeats"] == 0 and not c["gaps"] and c["decoded"] >= 70 and c["last"] == 4242 + 79, c
