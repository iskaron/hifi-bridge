"""UNIT (real ``lame`` child process, no LMS): the live MP3 encoder produces a genuine MPEG-1 Layer III 44.1 kHz stereo CBR 320 kbit/s
stream, stays bounded, never leaves a child behind and reports a missing encoder instead of failing late (DECISIONS #147)."""
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time

import numpy as np
import pytest

from btbridge.stream import encoders as E
from btbridge.stream import mp3frames as F

pytestmark = pytest.mark.skipif(shutil.which("lame") is None, reason="needs the lame CLI (the lab image has it)")

RATE = 44100


def tone(frames: int, start: int = 0, left_hz: float = 440.0, right_hz: float = 880.0, amp: int = 9000) -> bytes:
    k = np.arange(start, start + frames)
    out = np.empty(frames * 2, dtype="<i2")
    out[0::2] = (amp * np.sin(2 * np.pi * left_hz * k / RATE)).astype("<i2")
    out[1::2] = (amp * np.sin(2 * np.pi * right_hz * k / RATE)).astype("<i2")
    return out.tobytes()


def encode_all(seconds: float = 3.0, realtime: bool = False) -> bytes:
    enc = E.Mp3Encoder()
    out, n, step = [], 0, RATE // 10
    try:
        while n < int(seconds * RATE):
            out.append(enc.encode(tone(step, n)))
            n += step
            if realtime:
                time.sleep(0.1)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:             # the encoder holds back < one block; collect what it has produced
            time.sleep(0.1)
            out.append(enc.encode(b""))
        return b"".join(out)
    finally:
        enc.close()


def test_the_stream_is_real_mpeg1_layer3_cbr_320_at_44k1_stereo():
    data = encode_all(3.0)
    start = F.find_first_frame(data)
    assert start == 0, "a live stream starts with a frame (no ID3/Xing/Info tag)"
    frames, end = F.walk(data, start)
    assert len(frames) > 60
    assert {f.bitrate_kbps for f in frames} == {320} and {f.sample_rate for f in frames} == {44100} and {f.mode for f in frames} == {"stereo"}
    assert {f.size for f in frames} <= {1044, 1045}                           # 144 * 320000 / 44100 = 1044.9 (+1 padding byte)
    assert len(data) - end < 1100                                              # only an incomplete last frame follows
    assert not data.startswith(b"ID3") and b"Xing" not in data[:2200] and b"Info" not in data[:2200]


def test_the_data_rate_is_constant_320_kbit_per_second_not_wav():
    data = encode_all(4.0, realtime=True)
    rate = len(data) * 8 / 4.0 / 1000
    assert 300 < rate < 335, rate                                              # 320 kbit/s (+ the encoder's block holdback), not 1411
    assert len(data) < 4.0 * RATE * 4 * 0.30                                  # ~77 % less than the 176,400 byte/s of WAV


def test_the_audio_inside_is_the_encoded_pcm_with_left_and_right_in_place():
    data = encode_all(3.0)
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "a.mp3"), os.path.join(d, "a.wav")
        open(src, "wb").write(data)
        subprocess.run(["lame", "--decode", "--silent", src, dst], check=True, timeout=30)
        raw = open(dst, "rb").read()
    pcm = np.frombuffer(raw[44:], dtype="<i2").reshape(-1, 2).astype(float)
    assert pcm.shape[0] > RATE * 2.5
    mid = pcm[RATE:RATE * 2]                                                   # skip the encoder delay at the start

    def peak_hz(x):
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        return np.argmax(spec) * RATE / len(x)
    assert abs(peak_hz(mid[:, 0]) - 440) < 3 and abs(peak_hz(mid[:, 1]) - 880) < 3      # L = 440 Hz, R = 880 Hz: channels not swapped/merged
    assert 3000 < np.abs(mid).max() < 14000                                    # the level survives (no clipping, no silence)


def test_a_client_started_later_gets_a_stream_that_begins_on_a_frame_boundary():
    first = encode_all(1.0)
    second = encode_all(1.0)
    for data in (first, second):                                               # one encoder per connection: each begins at frame 0
        assert F.parse_header(data[:4]) is not None and F.walk(data)[1] > 20 * 1044


def test_the_output_queue_is_bounded_and_a_stalled_consumer_blocks_the_child_instead_of_growing_memory(monkeypatch):
    monkeypatch.setattr(E, "MP3_QUEUE_CAP", 16 * 1024)
    enc = E.Mp3Encoder()
    try:
        feeder_done = []

        def feed():
            import threading

            def run():
                try:
                    for i in range(60):                                         # 6 s of audio, never reading the output
                        enc.proc.stdin.write(tone(RATE // 10, i * (RATE // 10)))
                        enc.proc.stdin.flush()
                    feeder_done.append(True)
                except (BrokenPipeError, ValueError, OSError):
                    pass
            t = threading.Thread(target=run, daemon=True)
            t.start()
            return t
        t = feed()
        time.sleep(1.5)
        assert enc._queued <= 16 * 1024 + E.MP3_READ                           # the reader stopped: the queue never exceeded its cap
        assert not feeder_done                                                  # the child is blocked in write(): input backs up, nothing grows
        out = enc._take()
        assert 0 < len(out) <= 16 * 1024 + E.MP3_READ
        enc.close()
        t.join(3)
    finally:
        enc.close()


def test_close_kills_and_reaps_the_child_and_ends_the_reader_and_may_be_called_twice():
    enc = E.Mp3Encoder()
    enc.encode(tone(RATE // 10))
    pid = enc.pid
    assert enc.alive and (enc.cpu_seconds() or 0.0) >= 0.0
    enc.close()
    enc.close()
    assert enc.proc.poll() is not None and not enc._t.is_alive() and not enc.alive
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                                         # really gone: no zombie, no orphan


def test_a_dead_encoder_is_reported_to_the_writer_as_a_broken_pipe():
    enc = E.Mp3Encoder()
    try:
        enc.proc.kill()
        enc.proc.wait(timeout=3)
        with pytest.raises(BrokenPipeError):
            enc.encode(tone(RATE // 10))
            enc.encode(tone(RATE // 10))
        assert not enc.alive
    finally:
        enc.close()


def test_a_missing_encoder_raises_a_specific_error_and_leaves_wav_and_flac_untouched(monkeypatch):
    monkeypatch.setattr(E, "mp3_encoder_path", lambda: None)
    with pytest.raises(E.EncoderUnavailable):
        E.Mp3Encoder()
    with pytest.raises(E.EncoderUnavailable):
        E.Mp3Encoder(binary="/nonexistent/lame")
    assert E.WavEncoder().encode(b"\1\2\3\4") == b"\1\2\3\4" and E.WavEncoder().header()[:4] == b"RIFF"


def test_the_encoder_cli_is_started_without_a_shell_and_with_fixed_arguments(monkeypatch):
    seen = {}
    real = subprocess.Popen

    def spy(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return real(args, **kw)
    monkeypatch.setattr(E.subprocess, "Popen", spy)
    enc = E.Mp3Encoder()
    enc.close()
    a = seen["args"]
    assert isinstance(a, list) and not seen["kw"].get("shell")
    assert a[a.index("-b") + 1] == "320" and "--cbr" in a and "-t" in a and a[a.index("-s") + 1] == "44.1" and "-m" in a and a[a.index("-m") + 1] == "s"
    assert seen["kw"]["stderr"] == subprocess.DEVNULL                         # nothing is written anywhere


def test_frame_parser_rejects_what_is_not_mpeg1_layer3():
    assert F.parse_header(b"RIFF") is None and F.parse_header(b"\xff\xf3\x90\x00") is None      # MPEG-2 header
    assert F.parse_header(b"\xff\xfb\xf0\x00") is None and F.parse_header(b"\xff\xfb") is None  # bitrate index 15 invalid / truncated
    assert F.parse_header(b"\xff\xfb\xe0\x00").bitrate_kbps == 320 and F.parse_header(b"\xff\xfb\x90\x00").bitrate_kbps == 128
