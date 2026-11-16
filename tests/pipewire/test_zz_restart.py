"""Fault/restart regression (runs last: it restarts pipewire-pulse). INTEGRATION real PipeWire tools, SIMULATED nodes.
During a pipewire-pulse restart the phone node disappears; afterwards the 'microphone' comes back FIRST and becomes the default source,
the phone node only seconds later (worst case for an accidental fallback). The bridge's capture must resume with the phone's pattern and
must never contain microphone frames."""
import os
import subprocess
import time

from conftest import FIFO, MIC, MIC_FIFO, NODE, RESULTS, bring_up_node, load_source, pactl
from pcm_probe import analyse
from test_capture import mic_frames

from btbridge.adapters.bluez.pcm import PipeWireCapture, choose_capture


def restart_pipewire_pulse():
    subprocess.run(["pkill", "-f", "pipewire-pulse"], check=False)
    for _ in range(50):
        if subprocess.run(["pgrep", "-f", "pipewire-pulse"], capture_output=True).returncode != 0:
            break
        time.sleep(0.1)
    log = open(os.environ.get("PW_LOG", "/tmp/pw-logs") + "/pipewire-pulse-restart.log", "w")
    subprocess.Popen(["pipewire-pulse"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if subprocess.run(["pactl", "info"], capture_output=True).returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError("pipewire-pulse did not come back")


def test_capture_survives_a_pipewire_pulse_restart_without_ever_capturing_the_microphone(pw, feeder):
    name, argv_for = choose_capture()
    got = []
    cap = PipeWireCapture(lambda a: argv_for(NODE), retry_s=0.2, stall_s=1.0)
    cap.start("x", got.append)
    time.sleep(2.0)
    before = len(got)
    restart_pipewire_pulse()
    load_source(MIC, MIC_FIFO); pactl("set-default-source", MIC)         # the microphone is back first and is the default
    time.sleep(3.0)                                                       # ... while the phone node is still missing
    during = len(got)
    pw["module"] = load_source(NODE, FIFO)
    RESULTS["restart_stand_in_reloads"] = bring_up_node(pw)               # stand-in readiness only (see conftest)
    keep = subprocess.Popen(["parec", f"--device={NODE}", "--property=node.dont-fallback=true", "--raw"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)                    # stand-in keepalive (see conftest)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15 and not any(c.strip(b"\x00") for c in got[during:]):
        time.sleep(0.2)
    time.sleep(2.0)
    cap.stop(); keep.terminate()
    data = b"".join(got)
    after = b"".join(got[during:])
    res = analyse(after) if after else {}
    RESULTS["restart"] = {"tool": name, "chunks_before": before, "chunks_while_node_missing": during - before,
                          "mic_frames_total": mic_frames(data, pw["mic_value"]), "starts": cap.starts,
                          "resumed_pattern_frames": res.get("payload_frames")}
    assert before > 50                                                    # it was capturing
    assert mic_frames(data, pw["mic_value"]) == 0                         # never the microphone - before, during or after (ALL versions)
    assert cap.starts >= 2                                                # the supervisor restarted the capture child
    if pw["version"].startswith("0.3."):
        # Resume is NOT asserted on 0.3.65: the re-created module-pipe-source stand-in may hand a new stream only digital silence
        # (recorded in the results; product target is PipeWire 1.2.8, resume is asserted on 1.x). See docs/PIPEWIRE_CAPTURE.md.
        RESULTS["restart"]["resume_asserted"] = False
        return
    RESULTS["restart"]["resume_asserted"] = True
    assert res.get("payload_frames", 0) > 44100 and res.get("foreign_frames", 1) == 0, res    # resumed with the phone pattern
