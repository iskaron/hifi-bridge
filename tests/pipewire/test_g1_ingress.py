"""G1 regression against REAL PipeWire (0.3.65 / 1.4.2): the phone's audio as a PLAYBACK stream must never be captured, the same audio
as an Audio/Source (WirePlumber rule bluez5.media-source-role = input) must be found by its properties and captured bit-exactly.

Real-hardware observation (Pi 3 B+, Pi OS Lite trixie, PipeWire/WirePlumber 1.4.2, 2026-11-24): `bluez_input.<MAC>.2` was a
Stream/Output/Audio node that WirePlumber linked to the Pi's headphone sink; the bridge looked for `bluez_input.<MAC>` and got nothing.
Stand-ins here: the playback stream is a real `pw-cat --playback` stream carrying the BlueZ node properties (`api.bluez5.address`,
`api.bluez5.profile = a2dp-source`, the `.2` name); the input-role node is a `module-pipe-source` with the same properties.
Label: INTEGRATION (real PipeWire graph, real pw-dump/pw-cat/parec) / SIMULATED phone - NOT BlueZ, NOT a phone, NOT a Pi."""
import os
import struct
import subprocess
import threading
import time
import wave

from conftest import RESULTS, pactl, vanish_node, writer_fd
from pcm_probe import analyse, pattern
from test_capture import mic_frames

from btbridge.adapters.bluez.pcm import PipeWireCapture, choose_capture, find_phone_node

PLAY_MAC, SRC_MAC = "AA:BB:CC:DD:EE:07", "AA:BB:CC:DD:EE:08"
SRC_NODE, SRC_FIFO = "bluez_input.AA_BB_CC_DD_EE_08.2", "/tmp/g1-src.fifo"
PLAY_VALUE = 1000                                      # what the playback stream carries: recognisable if it were ever captured


def bridge_capture(seconds: float, address: str):
    """The bridge's own path: discovery with pw-dump before every start + the capture tool this PipeWire version gets."""
    tool, argv_for_node = choose_capture()
    got, logs = [], []
    cap = PipeWireCapture(resolve=find_phone_node, argv_for_node=argv_for_node, retry_s=0.2, stall_s=1.5, log=logs.append)
    cap.start(address, got.append)
    time.sleep(seconds)
    status = cap.status()
    cap.stop()
    return b"".join(got), cap, status, logs, tool


def test_phone_audio_as_a_playback_stream_is_never_captured(pw, tmp_path):
    wav = str(tmp_path / "phone.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(struct.pack("<hh", PLAY_VALUE, PLAY_VALUE) * 44100 * 20)
    props = ('{ "node.name" = "bluez_input.AA_BB_CC_DD_EE_07.2" "api.bluez5.address" = "%s" "api.bluez5.profile" = "a2dp-source" '
             '"device.api" = "bluez5" }' % PLAY_MAC)
    play = subprocess.Popen(["pw-cat", "--playback", "-P", props, wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 10
        while "Stream/Output/Audio" not in find_phone_node(PLAY_MAC)[1] and time.monotonic() < end:
            time.sleep(0.1)
        node, why = find_phone_node(PLAY_MAC)
        data, cap, status, logs, tool = bridge_capture(3.0, PLAY_MAC)
    finally:
        play.terminate(); play.wait(5)
    RESULTS["_version"] = pw["version"]
    RESULTS["g1_playback_stream"] = {"node": node, "reason": why, "bytes": len(data), "starts": cap.starts, "status": status["state"],
                                     "mic_frames": mic_frames(data, pw["mic_value"]), "stream_frames": mic_frames(data, PLAY_VALUE),
                                     "tool": tool, "label": "INTEGRATION real PipeWire + SIMULATED phone stream"}
    assert node is None and "Stream/Output/Audio" in why and "media-source-role" in why, why
    assert data == b"" and cap.starts == 0                       # no child at all: not the stream, not a monitor, not the microphone
    assert status["state"] == "waiting" and "media-source-role" in status["reason"]
    assert sum("media-source-role" in m for m in logs) == 1


def test_input_role_source_is_discovered_and_captured_bit_exactly_then_released(pw):
    if not os.path.exists(SRC_FIFO):
        os.mkfifo(SRC_FIFO)
    fd = writer_fd(SRC_FIFO)
    module = pactl("load-module", "module-pipe-source", f"source_name={SRC_NODE}", f"file={SRC_FIFO}", "format=s16le", "rate=44100",
                   "channels=2", f'source_properties="api.bluez5.address={SRC_MAC} api.bluez5.profile=a2dp-source device.api=bluez5"')
    stop = threading.Event()

    def feed():
        i, t = 1, time.monotonic()
        while not stop.is_set():
            os.write(fd, pattern(i, 882)); i += 882; t += 0.02; time.sleep(max(0.0, t - time.monotonic()))
    th = threading.Thread(target=feed, daemon=True); th.start()
    # Stand-in workaround (NOT product behaviour, conftest.start_keepalive): keep the pipe source consumed so it does not suspend.
    keep = subprocess.Popen(["parec", f"--device={SRC_NODE}", "--property=node.dont-fallback=true", "--raw"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        node, why = find_phone_node(SRC_MAC.lower())
        best = None
        for _attempt in range(3):                               # 0.3.65: a fresh pipe source may deliver only silence at first
            data, cap, status, logs, tool = bridge_capture(5.0, SRC_MAC)
            res = analyse(data)
            best = (res, status, tool, cap.starts)
            if res.get("bit_exact"):
                break
        res, status, tool, starts = best
        stop.set(); keep.terminate(); keep.wait(5); th.join(3)
        vanish_node(SRC_NODE, module)
        gone_data, gone_cap, gone_status, _, _ = bridge_capture(2.0, SRC_MAC)
    finally:
        stop.set()
        if keep.poll() is None:
            keep.terminate()
    RESULTS["_version"] = pw["version"]
    RESULTS["g1_input_role"] = {"node": node, "tool": tool, "bit_exact": res.get("bit_exact"), "headerless": res.get("headerless"),
                                "payload_frames": res.get("payload_frames"), "status": status, "after_vanish_bytes": len(gone_data),
                                "after_vanish_status": gone_status["state"], "after_vanish_reason": gone_status["reason"],
                                "label": "INTEGRATION real PipeWire + SIMULATED input-role node"}
    assert node == SRC_NODE, why
    assert res["headerless"] and res["bit_exact"] and res["payload_frames"] > 44100 * 2, res
    assert status["state"] == "capturing" and status["node"] == SRC_NODE and starts == 1
    assert gone_data == b"" and gone_cap.starts == 0 and gone_status["state"] == "waiting" and "no PipeWire node" in gone_status["reason"]
    assert mic_frames(gone_data, pw["mic_value"]) == 0
